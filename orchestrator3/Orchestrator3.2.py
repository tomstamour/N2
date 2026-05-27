import sys
import os
import csv
import json
import signal
import logging
import threading
import itertools
import subprocess
import importlib.util as _ilu
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/FinBERT_pipeline')

# ── FinBERT import (hyphen in filename prevents normal import) ────────────────
_finbert_spec = _ilu.spec_from_file_location(
    "FinBERT_headliner",
    "/home/tom/Documents/ibkr_scripts/N2/scripts/FinBERT/FinBERT-headliner.py",
)
_finbert_mod = _ilu.module_from_spec(_finbert_spec)
_finbert_spec.loader.exec_module(_finbert_mod)
analyze_headline = _finbert_mod.analyze_headline
load_model       = _finbert_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

from FinBERT_body_pipeline import FinBERTBodyPipeline

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = "/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/tables"
# ─────────────────────────────────────────────────────────────────────────────

import NewsWatcher3 as nw

# ── NewsWatcher3 inputs (RTPR firehose) ───────────────────────────────────────
NW3_UNIVERSE_TSV          = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv'
NW3_PRICED_TSV            = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv'
NW3_BLACK_LIST            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/black_list.csv'
NW3_API_KEYS              = '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3/RTPR_API-Key.txt'
NW3_LOG_DIR               = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/logs'
NW3_OUTPUT_DIR            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
NW3_NEWS_DF_DIR           = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
NW3_BLOCKED_DIR           = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs/blocked_PRs'
NW3_ACCEPTED_DIR          = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs/accepted_PRs'
NW3_EXCLUDED_STRINGS_FILE = '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3/excluded_strings.txt'
NW3_BLACKLIST_EXPIRY_HOURS = 0
NW3_REJECT_FLOAT_GT       = 50        # M shares; matches old universe filter
NW3_REJECT_PRICE_GT       = 10.00
NW3_FLUSH_INTERVAL_SEC    = 3600
# ─────────────────────────────────────────────────────────────────────────────

# ── Trade-mole trigger (per-ticker subprocess launch) ─────────────────────────
TM_SENTIMENT_SCORE_MIN = 0.7     # launch only if sentiment_score >  this
TM_FLOAT_MAX_M         = 50     # launch only if Float (millions) <  this

TM_SCRIPT          = '/home/tom/Documents/ibkr_scripts/N2/scripts/volume/trade_surge_mole/trade_mole_2.py'
TM_BASE_CLIENT_ID  = 400         # first clientID; auto-increments per launch
TM_LIFETIME        = '10:00'     # mm:ss — passed to --lifeTime
TM_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_outputs'
TM_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_logs'
TM_HOST            = '127.0.0.1'
TM_PORT            = 4001        # 4001=live GW  4002=paper GW  7496/7497=TWS
TM_PYTHON          = sys.executable   # same interpreter as Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

# ── FinBERT body pipeline ─────────────────────────────────────────────────────
BODY_FINBERT_OUTPUT_DIR    = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/body_finbert_outputs'
BODY_FINBERT_WORKERS       = 2          # one heavy pipeline (spaCy + fastcoref + ONNX FinBERT) per worker
BODY_FINBERT_COREF_MODE    = 'full'     # 'full' = fastcoref, 'simple' = pronoun-only
BODY_FINBERT_WRITE_OUTPUTS = True       # dump cleaned/pronouns/sentences/NER/FinBERT JSON per article
BODY_FINBERT_TIMEOUT_SEC   = 300        # per-article ceiling when resolving the future
# ─────────────────────────────────────────────────────────────────────────────

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('Orchestrator3.2')

_priced_df: pd.DataFrame = None

# ─── ThreadPoolExecutor ───────────────────────────────────────────────────────

# Split pools so collect-tasks idle-waiting on the FinBERT future cannot
# starve FinBERT workers. With one shared pool, submission order interleaves
# (F1,C1,F2,C2,…) and Cn-workers blocking on f_finbert.result() halve the
# effective FinBERT parallelism in bursts.
_finbert_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='finbert')
_collect_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='collect')

# Body pipeline is NOT thread-safe (shared spaCy nlp, fastcoref model, ONNX
# session, TickerResolver cache). Each worker gets its own instance loaded once
# via the ThreadPoolExecutor initializer — guaranteed before any article arrives
# thanks to the pre-warm loop in main().
_body_local = threading.local()


def _init_body_worker() -> None:
    """ThreadPoolExecutor initializer: runs exactly once per worker thread.
    All heavy models (spaCy, fastcoref, ONNX FinBERT, SEC-EDGAR ticker map)
    are loaded here so no article ever pays the cold-start penalty."""
    logger.info(
        f"[Body] thread {threading.current_thread().name}: "
        f"loading FinBERTBodyPipeline (coref_mode={BODY_FINBERT_COREF_MODE})..."
    )
    pipe = FinBERTBodyPipeline(
        coref_mode=BODY_FINBERT_COREF_MODE,
        output_dir=BODY_FINBERT_OUTPUT_DIR,
        write_outputs=BODY_FINBERT_WRITE_OUTPUTS,
    )
    pipe.load_models()
    _body_local.pipeline = pipe
    logger.info(f"[Body] thread {threading.current_thread().name}: pipeline ready.")


_body_executor = ThreadPoolExecutor(
    max_workers=BODY_FINBERT_WORKERS,
    thread_name_prefix='body',
    initializer=_init_body_worker,
)

_tsv_lock = threading.Lock()

_tm_clientid_counter = itertools.count(TM_BASE_CLIENT_ID)
_tm_clientid_lock    = threading.Lock()


def _next_tm_clientid() -> int:
    with _tm_clientid_lock:
        return next(_tm_clientid_counter)


def _get_body_pipeline() -> FinBERTBodyPipeline:
    """Return this thread's pre-loaded pipeline (set by _init_body_worker)."""
    return _body_local.pipeline

# ─── Analysis functions ───────────────────────────────────────────────────────

def analyze_finbert(news_dict: dict) -> dict:
    """FinBERT headline sentiment analysis. Runs once per article."""
    result = analyze_headline(news_dict['Headline'])
    logger.info(
        f"[FinBERT] {news_dict['Symbol']} → {result['label'].upper()} "
        f"(score={result['sentiment_score']:.4f})"
    )
    return {
        'positive':        result['positive'],
        'negative':        result['negative'],
        'neutral':         result['neutral'],
        'sentiment_score': result['sentiment_score'],
        'label':           result['label'],
    }


def analyze_body_finbert(news_dict: dict) -> dict:
    """Run the FinBERT body pipeline on news_dict['article_body']. Runs once
    per article. Returns the full pipeline result dict, or {} if the body is
    missing/empty."""
    body = (news_dict.get('article_body') or '').strip()
    if not body:
        logger.info(
            f"[Body] {news_dict['Symbol']} id={news_dict['ID']}: "
            f"empty article_body — skipping"
        )
        return {}

    pipe = _get_body_pipeline()
    result = pipe.process(news_dict)  # FIELD_NAME='article_body' matches our key
    tickers_found = result.get('finbert', {}).get('metadata', {}).get('unique_tickers', [])
    logger.info(
        f"[Body] {news_dict['Symbol']} id={news_dict['ID']} → "
        f"{len(tickers_found)} ticker(s) scored: {tickers_found}"
    )
    return result


# ─── Trigger evaluator ───────────────────────────────────────────────────────

def evaluate_trigger(completed_dict: dict) -> str:
    """Returns 'YES' if all trade_mole conditions are met, else 'NO:cond1,cond2,...'."""
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')

    failures = []
    if sentiment is None or sentiment <= TM_SENTIMENT_SCORE_MIN:
        failures.append('sentiment_score')
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        failures.append('Float')

    return 'YES' if not failures else 'NO:' + ','.join(failures)


# ─── Trade-mole launcher ──────────────────────────────────────────────────────

def maybe_launch_trade_mole(completed_dict: dict) -> None:
    """Fire-and-forget launch of trade_mole_2.py if trigger conditions are met
    for this single ticker. Detached subprocess: survives Orchestrator shutdown."""
    symbol    = completed_dict['Symbol']
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')

    if sentiment is None or sentiment <= TM_SENTIMENT_SCORE_MIN:
        return
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        return

    _et_hour = datetime.now(tz=ZoneInfo('America/New_York')).hour
    if _et_hour >= 20 or _et_hour < 4:
        logger.info(
            f"[TM] {symbol}: off-market hours ({_et_hour:02d}:xx ET) — "
            f"skipping trade_mole launch to avoid multi-hour idle connection"
        )
        return

    client_id = _next_tm_clientid()
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_path = os.path.join(TM_LOG_DIR, f"{symbol}_{ts}_{client_id}.log")

    argv = [
        TM_PYTHON, TM_SCRIPT,
        '--symbol', symbol,
        '--clientID', str(client_id),
        '--lifeTime', TM_LIFETIME,
        '--output', TM_OUTPUT_DIR,
        '--host', TM_HOST,
        '--port', str(TM_PORT),
        '--log-dir', TM_LOG_DIR,
    ]

    try:
        log_fh = open(log_path, 'w', encoding='utf-8')
        # start_new_session=True detaches from Orchestrator's pgid so Ctrl+C
        # on the parent does not propagate to the child.
        proc = subprocess.Popen(
            argv,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        logger.info(
            f"[TM] launched {symbol} pid={proc.pid} clientID={client_id} "
            f"sentiment={sentiment:.4f} float={float_m} → {log_path}"
        )
    except Exception as exc:
        logger.error(f"[TM] {symbol}: failed to launch trade_mole: {exc}", exc_info=True)


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = ['Symbol', 'Tickers', 'ID', 'ArrivalTime', 'Headline', 'Author',
                'Float', 'FinBERTCompletedAt',
                'positive', 'negative', 'neutral', 'sentiment_score', 'label',
                'body_sentiment', 'BodyCompletedAt', 'body_duration_ms',
                'Trigger']


def _append_to_tsv(completed_dict: dict) -> None:
    """Appends one row (one ticker) to the daily TSV output file. Thread-safe."""
    date_str  = datetime.now().strftime('%Y-%m-%d')
    file_path = os.path.join(OUTPUT_DIR, f"news_output_{date_str}.tsv")
    try:
        with _tsv_lock:
            file_exists = os.path.isfile(file_path)
            if not file_exists:
                os.makedirs(OUTPUT_DIR, exist_ok=True)
            with open(file_path, 'a', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=_TSV_COLUMNS,
                                        delimiter='\t', extrasaction='ignore')
                if not file_exists:
                    writer.writeheader()
                writer.writerow(completed_dict)
    except Exception as exc:
        logger.error(f"TSV write failed for id={completed_dict.get('ID')}: {exc}", exc_info=True)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _lookup_float(symbol: str):
    """Pull Float_M for `symbol` from the priced TSV. Returns None if absent."""
    if _priced_df is None or symbol not in _priced_df['Symbol'].values:
        return None
    val = _priced_df.loc[_priced_df['Symbol'] == symbol, 'Float_M'].iloc[0]
    return None if pd.isna(val) else val


def _lookup_author(news_id: str):
    """Pull `author` from NW3's in-memory accepted-objects store. Returns None
    if the article has already been pruned (post-flush) or is missing."""
    obj = nw.get_news_object(f"id-{news_id}")
    if obj is None:
        return None
    return obj.get('author')


# ─── Per-ticker collector — runs on echo_worker thread ───────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_body) -> None:
    """Resolves analysis futures for one ticker, appends a TSV row, and
    evaluates the trade_mole trigger."""
    news_id = news_dict['ID']

    # FinBERT-headliner — resolve first so a slow body never gates the
    # trigger decision.
    try:
        finbert_val = f_finbert.result(timeout=60)
        finbert_completed_at = datetime.now()
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        finbert_completed_at = None

    # FinBERT body pipeline — pull this ticker's overall_sentiment_score
    # (mean of every sentence's sentiment_score for that ticker, computed in
    # SentimentAggregator.build_output()).
    body_sentiment = None
    body_duration_ms = None
    body_completed_at = None
    try:
        body_result = f_body.result(timeout=BODY_FINBERT_TIMEOUT_SEC)
        body_completed_at = datetime.now()
        ticker_sentiments = body_result.get('finbert', {}).get('ticker_sentiments', {})
        ticker_block = ticker_sentiments.get(symbol)
        if ticker_block is not None:
            body_sentiment = ticker_block.get('overall_sentiment_score')
        body_duration_ms = round(
            sum(t.get('elapsed_ms', 0) for t in body_result.get('timings', [])), 2
        )
    except Exception as exc:
        logger.error(
            f"FinBERT-body error for id={news_id} symbol={symbol}: {exc}",
            exc_info=True,
        )

    completed_dict = {
        'Symbol':          symbol,
        'Tickers':         json.dumps(tickers),
        'ID':              news_id,
        'ArrivalTime':     news_dict['ArrivalTime'].replace(microsecond=0),
        'Headline':        news_dict['Headline'],
        'Author':          _lookup_author(news_id),
        'Float':           _lookup_float(symbol),
        'FinBERTCompletedAt': finbert_completed_at,
        'positive':        finbert_val.get('positive'),
        'negative':        finbert_val.get('negative'),
        'neutral':         finbert_val.get('neutral'),
        'sentiment_score': finbert_val.get('sentiment_score'),
        'label':           finbert_val.get('label'),
        'body_sentiment':  body_sentiment,
        'BodyCompletedAt': body_completed_at,
        'body_duration_ms': body_duration_ms,
    }
    completed_dict['Trigger'] = evaluate_trigger(completed_dict)

    _append_to_tsv(completed_dict)
    maybe_launch_trade_mole(completed_dict)
    logger.info(f"Completed news_dict: {completed_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol             : {completed_dict['Symbol']}")
    print(f"  Tickers            : {completed_dict['Tickers']}")
    print(f"  ID                 : {completed_dict['ID']}")
    print(f"  ArrivalTime        : {completed_dict['ArrivalTime']}")
    print(f"  Headline           : {completed_dict['Headline']}")
    print(f"  Author             : {completed_dict['Author']}")
    print(f"  FinBERT label      : {completed_dict['label']}")
    print(f"  sentiment_score    : {completed_dict['sentiment_score']}")
    print(f"  positive           : {completed_dict['positive']}")
    print(f"  negative           : {completed_dict['negative']}")
    print(f"  neutral            : {completed_dict['neutral']}")
    print(f"  Float              : {completed_dict['Float']}")
    print(f"  FinBERTCompletedAt : {completed_dict['FinBERTCompletedAt']}")
    print(f"  body_sentiment     : {completed_dict['body_sentiment']}")
    print(f"  BodyCompletedAt    : {completed_dict['BodyCompletedAt']}")
    print(f"  body_duration_ms   : {completed_dict['body_duration_ms']}")
    print(f"  Trigger            : {completed_dict['Trigger']}")
    print(f"{'='*60}\n")


# ─── Callback — invoked from NW3 background thread ───────────────────────────

def on_news_accepted(news_dict: dict) -> None:
    """
    Invoked by NewsWatcher3 for every article that passes all filters.

    NW3's `Symbol` field is comma-joined for multi-ticker articles (up to 2).
    Strategy: fan-out per ticker. FinBERT-headliner and FinBERT-body each run
    once per article and their futures are shared by every ticker's
    _collect_and_log task.
    """
    raw_symbol = news_dict['Symbol']
    news_id    = news_dict['ID']
    tickers    = [t.strip() for t in raw_symbol.split(',') if t.strip()]
    if not tickers:
        logger.warning(f"on_news_accepted: id={news_id} has no tickers — skipping")
        return

    logger.info(
        f"on_news_accepted triggered: id={news_id} tickers={tickers}"
    )

    f_finbert = _finbert_executor.submit(analyze_finbert, news_dict)
    f_body    = _body_executor.submit(analyze_body_finbert, news_dict)
    for symbol in tickers:
        _collect_executor.submit(
            _collect_and_log, news_dict, tickers, symbol, f_finbert, f_body,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Orchestrator3.2 starting...")

    for d in (OUTPUT_DIR, TM_OUTPUT_DIR, TM_LOG_DIR, BODY_FINBERT_OUTPUT_DIR):
        if d:
            os.makedirs(d, exist_ok=True)

    logger.info("Pre-loading FinBERT-headliner model...")
    load_model()
    logger.info("FinBERT-headliner model ready.")

    # Force all body worker threads to start NOW so their initializer
    # (_init_body_worker → load_models) runs before the firehose opens.
    # Without this, workers are created lazily and the first article on each
    # thread would pay the full cold-start penalty (~30–60 s).
    logger.info(f"Pre-warming {BODY_FINBERT_WORKERS} FinBERT body pipeline worker(s)...")
    _futures_wait([_body_executor.submit(lambda: None) for _ in range(BODY_FINBERT_WORKERS)])
    logger.info("All body pipeline workers ready.")

    global _priced_df
    _priced_df = pd.read_csv(NW3_PRICED_TSV, sep='\t')
    logger.info(f"Priced TSV loaded: {len(_priced_df)} symbols (for Float lookup)")

    # Register callback BEFORE start() — no race window for missed items
    nw.register_callback(on_news_accepted)

    nw.start(
        universe_tsv=NW3_UNIVERSE_TSV,
        black_list=NW3_BLACK_LIST,
        blacklist_expiry_hours=NW3_BLACKLIST_EXPIRY_HOURS,
        api_keys=NW3_API_KEYS,
        log_dir=NW3_LOG_DIR,
        output_dir=NW3_OUTPUT_DIR,
        news_df_dir=NW3_NEWS_DF_DIR,
        blocked_dir=NW3_BLOCKED_DIR,
        accepted_dir=NW3_ACCEPTED_DIR,
        excluded_strings_file=NW3_EXCLUDED_STRINGS_FILE,
        priced_tsv=NW3_PRICED_TSV,
        reject_float_greater_then=NW3_REJECT_FLOAT_GT,
        reject_price_greater_then=NW3_REJECT_PRICE_GT,
        flush_interval_seconds=NW3_FLUSH_INTERVAL_SEC,
    )

    logger.info("NewsWatcher3 started. Waiting for news... (Ctrl+C to stop)")

    # ── Keep-alive ────────────────────────────────────────────────────────────
    _stop_event = threading.Event()

    def _handle_sigint(signum, frame):
        logger.info("Shutdown signal received — stopping...")
        _stop_event.set()

    # Register AFTER nw.start() so Orchestrator3.2's handlers override NW3's
    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        logger.info("Shutting down executors...")
        _finbert_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        _body_executor.shutdown(wait=True)
        logger.info("Stopping NewsWatcher3...")
        nw.stop()
        logger.info("Orchestrator3.2 stopped.")


if __name__ == '__main__':
    main()
