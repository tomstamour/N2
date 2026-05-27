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
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import glob

sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/FinBERT_pipeline/FinBERT_body_noCoref')

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

# ── FinBERT body pipeline (noCoref) + neutral-management add-on ───────────────
# Both modules import cleanly once their directory is on sys.path (above).
from FinBERT_body_noCoref import FinBERTBodyPipeline
from finBERT_neutral_management_addON import aggregate as _nocoref_aggregate
# ─────────────────────────────────────────────────────────────────────────────

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = "/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/tables"
# ─────────────────────────────────────────────────────────────────────────────

import NewsWatcher4 as nw

# ── NewsWatcher4 inputs (RTPR alerts WS + permalink curl) ─────────────────────
#
# PREREQUISITE: a filter rule must already exist on https://rtpr.io/wire.
#   Recommended catch-all:  tickers_length gte 1
# Without it, the alerts WS connects but emits no `alert` messages.
#
NW4_UNIVERSE_TSV          = None  # resolved at startup via _resolve_latest_universe_tsv()
NW4_PRICED_TSV            = None  # resolved at startup via _resolve_latest_universe_tsv()
NW4_BLACK_LIST            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/black_list.csv'
NW4_API_KEYS              = '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3/RTPR_API-Key.txt'
NW4_LOG_DIR               = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/logs'
NW4_OUTPUT_DIR            = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
NW4_NEWS_DF_DIR           = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
NW4_BLOCKED_DIR           = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs/blocked_PRs'
NW4_ACCEPTED_DIR          = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs/accepted_PRs'
NW4_EXCLUDED_STRINGS_FILE = '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3/excluded_strings.txt'
TM_EXCLUDED_STRINGS_FILE  = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/excluded_strings-2.txt'
NW4_BLACKLIST_EXPIRY_HOURS = 0
NW4_REJECT_FLOAT_GT       = 50        # M shares; matches old universe filter
NW4_REJECT_PRICE_GT       = 10.00
NW4_FLUSH_INTERVAL_SEC    = 3600
# ─────────────────────────────────────────────────────────────────────────────

# ── Trade-mole trigger (per-ticker subprocess launch) ─────────────────────────
TM_SENTIMENT_SCORE_MIN = 0.0     # launch only if sentiment_score >  this
TM_FLOAT_MAX_M         = 100     # launch only if Float (millions) <  this

TM_SCRIPT          = '/home/tom/Documents/ibkr_scripts/N2/scripts/volume/trade_surge_mole/trade_mole_4.1.py'
TM_BASE_CLIENT_ID  = 400         # first clientID; auto-increments per launch
TM_LIFETIME        = '10:00'     # mm:ss — passed to --lifeTime
TM_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_outputs'
TM_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_logs'
TM_HOST            = '127.0.0.1'
TM_PORT            = 4001        # 4001=live GW  4002=paper GW  7496/7497=TWS
TM_PYTHON          = sys.executable   # same interpreter as Orchestrator
TM_DEFAULT_BASELINE_ITI = 44444.0    # fallback ITI (s) when symbol absent/NaN in universe
TM_ITI_DATA_DIR    = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data'

# ── trade_mole_4.1 composite surge rule thresholds ────────────────────────────
TM_SURGE_DOLLAR_RATE      = 300.0   # dollar_rate_5s >= this (USD/s)
TM_SURGE_MAX_SPREAD_PCT   = 0.02    # spread_pct <= this (2%)
TM_SURGE_MIN_LIFT_RATIO   = 0.55    # lift_offer_ratio_5s >= this
TM_SURGE_MIN_BID_DRIFT_BP = 0.0     # bid_drift_5s_bp >= this
TM_SURGE_MIN_TRADES_5S    = 5       # trades_in_5s >= this
# ─────────────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────

# ── FinBERT body pipeline (noCoref) + neutral-management ──────────────────────
BODY_FINBERT_WORKERS      = 2     # one pipeline instance per worker thread
BODY_FINBERT_TIMEOUT_SEC  = 180   # per-article ceiling; trade_mole already fired by now
NOCOREF_NEUTRAL_THRESHOLD = 0.85  # method 1 (neutral_filter)
NOCOREF_TOP_K             = 3     # method 4 (top_k)
NOCOREF_POSITIONAL_DECAY  = 0.1   # method 5 (positional)
NOCOREF_SENTENCES_TO_ANALYSE = 20  # cap spaCy sentences scored per article (None = all)
# ─────────────────────────────────────────────────────────────────────────────

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('Orchestrator3.5')

_iti_df: pd.DataFrame = None
_last_iti_reload_date = None
_iti_df_lock = threading.Lock()

_excluded_strings: set[str] = set()


def _resolve_latest_universe_tsv() -> str:
    """Return the most recent nasdaq_symbols_data_priced_YYYY-MM-DD.tsv in
    TM_ITI_DATA_DIR, falling back to the non-dated file if none is found."""
    pattern = os.path.join(TM_ITI_DATA_DIR, 'nasdaq_symbols_data_priced_????-??-??.tsv')
    matches = sorted(glob.glob(pattern))
    if matches:
        return matches[-1]
    fallback = os.path.join(TM_ITI_DATA_DIR, 'nasdaq_symbols_data_priced.tsv')
    logger.warning(f"[Universe] No dated universe TSV found — falling back to {fallback}")
    return fallback


def _load_excluded_strings(path: str) -> None:
    global _excluded_strings
    try:
        with open(path, encoding='utf-8') as f:
            strings = {line.strip() for line in f if line.strip()}
        _excluded_strings = strings
        logger.info(f"[Filter] Loaded {len(strings)} excluded strings from {path}: {sorted(strings)}")
    except Exception as exc:
        logger.warning(f"[Filter] Could not load {path}: {exc} — no headline filtering")

# ─── ThreadPoolExecutors ──────────────────────────────────────────────────────

_finbert_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='finbert')
_collect_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='collect')

# Body pipeline is NOT thread-safe (shared spaCy nlp, ONNX session,
# TickerResolver cache). Each worker gets its own instance loaded once via the
# ThreadPoolExecutor initializer — guaranteed before any article arrives thanks
# to the pre-warm loop in main().
_body_local = threading.local()


def _init_body_worker() -> None:
    """ThreadPoolExecutor initializer: runs exactly once per worker thread.
    All heavy models (spaCy, ONNX FinBERT, SEC-EDGAR ticker map) are loaded
    here so no article ever pays the cold-start penalty.

    Note: each worker's pipeline holds its own TickerResolver. We deliberately
    do NOT call pipe.shutdown() on Orchestrator exit — the SEC EDGAR cache will
    rebuild from disk on next startup. Two threads racing on save_cache() is
    the only thing we'd risk, and the cost of not saving is negligible.
    """
    logger.info(
        f"[Body] thread {threading.current_thread().name}: "
        f"loading FinBERTBodyPipeline (noCoref)..."
    )
    pipe = FinBERTBodyPipeline(
        write_outputs=False,
        sentences_to_analyse=NOCOREF_SENTENCES_TO_ANALYSE,
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


def _run_body_pipeline(news_dict: dict) -> dict:
    """Run the noCoref body pipeline on news_dict. Returns the full result dict,
    or {} if the body is missing/empty or any exception is raised.

    A shallow-copied dict is passed to the pipeline with a `tickers` list
    injected (derived from the comma-joined Symbol). The pipeline's NER
    fallback uses `tickers` as allowed_tickers when SEC EDGAR fails to resolve,
    ensuring every fan-out ticker has a chance to be scored.
    """
    news_id = news_dict.get('ID')
    symbol  = news_dict.get('Symbol')

    body = (news_dict.get('article_body') or '').strip()
    if not body:
        logger.info(f"[Body] id={news_id} sym={symbol}: empty article_body — skipping")
        return {}

    tickers = [t.strip() for t in (symbol or '').split(',') if t.strip()]
    pipeline_input = dict(news_dict)
    pipeline_input['tickers'] = tickers

    pipe = _body_local.pipeline
    t0 = datetime.now()
    logger.info(f"[Body] id={news_id} sym={symbol}: starting pipeline")
    try:
        result = pipe.process(pipeline_input, write_outputs=False)
    except ValueError as exc:
        # FIELD_NAME missing/empty — defensive (we already returned above).
        logger.warning(f"[Body] id={news_id}: {exc}")
        return {}
    except Exception as exc:
        logger.error(
            f"[Body] id={news_id} sym={symbol}: pipeline error: {exc}",
            exc_info=True,
        )
        return {}

    elapsed_ms = (datetime.now() - t0).total_seconds() * 1000.0
    found = result.get('finbert', {}).get('metadata', {}).get('unique_tickers', [])
    logger.info(
        f"[Body] id={news_id} sym={symbol}: done in {elapsed_ms:.0f}ms — "
        f"{len(found)} ticker(s) scored: {found}"
    )
    return result


def _compute_nocoref_scores(body_result: dict, symbol: str) -> dict:
    """Run the 5 neutral-management aggregations for `symbol` against the
    body pipeline's per-sentence FinBERT output. Returns a dict with 5 float
    keys (rounded to 4 decimals) or all-None on any missing data."""
    none_result = {
        'nocoref_neutral_filter':      None,
        'nocoref_confidence_weighted': None,
        'nocoref_net_score':           None,
        'nocoref_top_k':               None,
        'nocoref_positional':          None,
    }

    if not body_result:
        return none_result

    ticker_block = (
        body_result
        .get('finbert', {})
        .get('ticker_sentiments', {})
        .get(symbol)
    )
    if not ticker_block:
        return none_result

    sentences = ticker_block.get('sentences', [])
    if not sentences:
        return none_result

    def _agg(method: int) -> float:
        score, _used = _nocoref_aggregate(
            method, sentences,
            neutral_threshold=NOCOREF_NEUTRAL_THRESHOLD,
            top_k=NOCOREF_TOP_K,
            positional_decay=NOCOREF_POSITIONAL_DECAY,
        )
        return round(score, 4)

    return {
        'nocoref_neutral_filter':      _agg(1),
        'nocoref_confidence_weighted': _agg(2),
        'nocoref_net_score':           _agg(3),
        'nocoref_top_k':               _agg(4),
        'nocoref_positional':          _agg(5),
    }


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
    headline = completed_dict.get('Headline') or ''
    if any(s.lower() in headline.lower() for s in _excluded_strings):
        failures.append('excluded_string')

    return 'YES' if not failures else 'NO:' + ','.join(failures)


# ─── Trade-mole launcher ──────────────────────────────────────────────────────

def _seconds_until_market_open() -> float:
    """Return seconds until 04:00 ET (start of pre-market window)."""
    now_et = datetime.now(tz=ZoneInfo('America/New_York'))
    target = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    if now_et >= target:
        target += timedelta(days=1)
    return (target - now_et).total_seconds()


def _do_launch_trade_mole(completed_dict: dict) -> None:
    """Spawn the trade_mole subprocess. Called immediately or by a deferred Timer."""
    symbol    = completed_dict['Symbol']
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')

    client_id = _next_tm_clientid()
    ts = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    log_path = os.path.join(TM_LOG_DIR, f"{symbol}_{ts}_{client_id}.log")

    baseline_iti = _lookup_baseline_iti(symbol)
    argv = [
        TM_PYTHON, TM_SCRIPT,
        '--symbol', symbol,
        '--clientID', str(client_id),
        '--lifeTime', TM_LIFETIME,
        '--output', TM_OUTPUT_DIR,
        '--host', TM_HOST,
        '--port', str(TM_PORT),
        '--log-dir', TM_LOG_DIR,
        '--baseline-iti', str(baseline_iti),
        '--surge-dollar-rate',    str(TM_SURGE_DOLLAR_RATE),
        '--surge-max-spread-pct', str(TM_SURGE_MAX_SPREAD_PCT),
        '--surge-min-lift-ratio', str(TM_SURGE_MIN_LIFT_RATIO),
        '--surge-min-bid-drift-bp', str(TM_SURGE_MIN_BID_DRIFT_BP),
        '--surge-min-trades-5s',  str(TM_SURGE_MIN_TRADES_5S),
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
            f"sentiment={sentiment:.4f} float={float_m} baseline_iti={baseline_iti:.2f}s → {log_path}"
        )
    except Exception as exc:
        logger.error(f"[TM] {symbol}: failed to launch trade_mole: {exc}", exc_info=True)


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
    headline = completed_dict.get('Headline') or ''
    if any(s.lower() in headline.lower() for s in _excluded_strings):
        return

    _et_hour = datetime.now(tz=ZoneInfo('America/New_York')).hour
    if _et_hour >= 20 or _et_hour < 4:
        delay = _seconds_until_market_open()
        t = threading.Timer(delay, _do_launch_trade_mole, args=[completed_dict.copy()])
        t.daemon = True
        t.name = f"tm-deferred-{symbol}"
        t.start()
        logger.info(
            f"[TM] {symbol}: off-market hours ({_et_hour:02d}:xx ET) — "
            f"deferred trade_mole launch to 04:00 ET ({delay/3600:.1f}h from now)"
        )
        return

    _do_launch_trade_mole(completed_dict)


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = [
    'Symbol', 'Tickers', 'ID', 'ArrivalTime', 'Headline', 'Author',
    'Float', 'Exchange', 'FinBERTCompletedAt',
    'positive', 'negative', 'neutral', 'sentiment_score', 'label',
    'nocoref_neutral_filter', 'nocoref_confidence_weighted',
    'nocoref_net_score', 'nocoref_top_k', 'nocoref_positional',
    'NoCorefCompletedAt',
    'Trigger',
]


def _append_to_tsv(completed_dict: dict) -> None:
    """Appends one row (one ticker) to the daily TSV output file. Thread-safe."""
    date_str  = datetime.now().strftime('%Y-%m-%d')
    file_path = os.path.join(OUTPUT_DIR, f"news_output_{date_str}.tsv")
    try:
        with _tsv_lock:
            file_exists = os.path.isfile(file_path)
            if file_exists:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_header = f.readline().rstrip('\n').split('\t')
                if existing_header != _TSV_COLUMNS:
                    rotated = file_path.replace('.tsv', '_rotated.tsv')
                    os.rename(file_path, rotated)
                    logger.warning(
                        f"TSV header mismatch — rotated old file to {rotated}"
                    )
                    file_exists = False
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
    """Pull Float_M for `symbol` from the daily universe TSV. Returns None if absent."""
    with _iti_df_lock:
        df = _iti_df
    if df is None or symbol not in df['Symbol'].values:
        return None
    val = df.loc[df['Symbol'] == symbol, 'Float_M'].iloc[0]
    return None if pd.isna(val) else val


def _lookup_author(news_id: str):
    """Pull `author` from NW4's in-memory accepted-objects store. Returns None
    if the article has already been pruned (post-flush) or is missing."""
    obj = nw.get_news_object(f"id-{news_id}")
    if obj is None:
        return None
    return obj.get('author')


def _load_latest_iti_tsv() -> None:
    """Load the most recent nasdaq_symbols_data_priced_YYYY-MM-DD.tsv into _iti_df."""
    global _iti_df
    pattern = os.path.join(TM_ITI_DATA_DIR, 'nasdaq_symbols_data_priced_????-??-??.tsv')
    matches = sorted(glob.glob(pattern))
    if not matches:
        logger.warning(f"[ITI] No dated universe TSV found in {TM_ITI_DATA_DIR} — ITI lookups will use default")
        return
    path = matches[-1]
    try:
        df = pd.read_csv(path, sep='\t')
        with _iti_df_lock:
            _iti_df = df
        logger.info(f"[ITI] Loaded {path} ({len(df)} symbols)")
    except Exception as exc:
        logger.error(f"[ITI] Failed to load {path}: {exc}", exc_info=True)


def _lookup_baseline_iti(symbol: str) -> float:
    """Return the appropriate baseline ITI for symbol based on current ET time."""
    now_et = datetime.now(tz=ZoneInfo('America/New_York'))
    h, m = now_et.hour, now_et.minute
    is_rth = (h == 9 and m >= 30) or (10 <= h < 16)
    col = 'RTH_avgITI_sec' if is_rth else 'ETH_avgITI_sec'
    with _iti_df_lock:
        df = _iti_df
    if df is None or symbol not in df['Symbol'].values:
        logger.warning(f"[TM] {symbol}: ITI not found in universe — using default {TM_DEFAULT_BASELINE_ITI}s")
        return TM_DEFAULT_BASELINE_ITI
    val = df.loc[df['Symbol'] == symbol, col].iloc[0]
    if pd.isna(val):
        logger.warning(f"[TM] {symbol}: {col} is NaN — using default {TM_DEFAULT_BASELINE_ITI}s")
        return TM_DEFAULT_BASELINE_ITI
    return float(val)


def _iti_reload_worker(stop_event: threading.Event) -> None:
    """Background thread: reloads the latest universe TSV once per day at/after 03:00 ET."""
    global _last_iti_reload_date
    ET_TZ = ZoneInfo('America/New_York')
    while not stop_event.is_set():
        now = datetime.now(tz=ET_TZ)
        if now.hour >= 3 and now.date() != _last_iti_reload_date:
            logger.info("[ITI] 03:00+ ET — reloading latest universe TSV")
            _load_latest_iti_tsv()
            _last_iti_reload_date = now.date()

            latest_path = _resolve_latest_universe_tsv()
            with _iti_df_lock:
                df = _iti_df
            if df is not None and 'Symbol' in df.columns:
                symbols = df['Symbol'].astype(str).str.strip().tolist()
                nw.update_universe(symbols)
                logger.info(f"[Universe] NW4 universe reloaded: {len(symbols)} symbols")
            nw.update_priced_tsv(latest_path)
            logger.info(f"[Universe] NW4 priced data reloaded from {latest_path}")

        stop_event.wait(timeout=60.0)


# ─── Per-ticker collector — runs on collect_worker thread ────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_body) -> None:
    """Resolves headline FinBERT first, evaluates trigger and launches
    trade_mole IMMEDIATELY, then resolves the body future and writes the TSV
    row with all 5 nocoref scores + NoCorefCompletedAt.

    The body pipeline can take seconds-to-minutes for long press releases;
    decoupling trade_mole launch from the TSV write keeps order execution
    fast while still landing the body enrichment in news_output_*.tsv."""
    news_id = news_dict['ID']

    # 1) Headline FinBERT
    try:
        finbert_val = f_finbert.result(timeout=60)
        finbert_completed_at = datetime.now()
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        finbert_completed_at = None

    # 2) Build the headline portion of the row
    completed_dict = {
        'Symbol':             symbol,
        'Tickers':            json.dumps(tickers),
        'ID':                 news_id,
        'ArrivalTime':        news_dict['ArrivalTime'].strftime('%Y-%m-%d %H:%M:%S') + f".{news_dict['ArrivalTime'].microsecond // 1000:03d}",
        'Headline':           news_dict['Headline'],
        'Author':             _lookup_author(news_id),
        'Float':              _lookup_float(symbol),
        'Exchange':           news_dict.get('exchange', ''),
        'FinBERTCompletedAt': finbert_completed_at,
        'positive':           finbert_val.get('positive'),
        'negative':           finbert_val.get('negative'),
        'neutral':            finbert_val.get('neutral'),
        'sentiment_score':    finbert_val.get('sentiment_score'),
        'label':              finbert_val.get('label'),
    }
    completed_dict['Trigger'] = evaluate_trigger(completed_dict)

    # 3) Launch trade_mole IMMEDIATELY — do NOT wait for the body pipeline.
    # This is the key ordering change vs 3.4: trade_mole used to fire AFTER
    # _append_to_tsv. In 3.5, TSV write is gated by the body future, so we
    # must launch trade_mole before that wait or order execution would slow.
    maybe_launch_trade_mole(completed_dict)

    # 4) Body pipeline — populate the 5 nocoref scores + timestamp
    try:
        body_result = f_body.result(timeout=BODY_FINBERT_TIMEOUT_SEC)
    except Exception as exc:
        logger.error(
            f"FinBERT-body error for id={news_id} sym={symbol}: {exc}",
            exc_info=True,
        )
        body_result = {}

    completed_dict.update(_compute_nocoref_scores(body_result, symbol))
    # NoCorefCompletedAt is stamped on any successful pipeline run, even when
    # the per-ticker block is empty (NER + allowed_tickers fallback both
    # failed). None means we didn't run a pipeline at all (empty body or error).
    completed_dict['NoCorefCompletedAt'] = datetime.now() if body_result else None

    # 5) Write the TSV row (now populated with all 21 columns)
    _append_to_tsv(completed_dict)

    logger.info(f"Completed news_dict: {completed_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol                       : {completed_dict['Symbol']}")
    print(f"  Tickers                      : {completed_dict['Tickers']}")
    print(f"  ID                           : {completed_dict['ID']}")
    print(f"  ArrivalTime                  : {completed_dict['ArrivalTime']}")
    print(f"  Headline                     : {completed_dict['Headline']}")
    print(f"  Author                       : {completed_dict['Author']}")
    print(f"  FinBERT label                : {completed_dict['label']}")
    print(f"  sentiment_score              : {completed_dict['sentiment_score']}")
    print(f"  positive                     : {completed_dict['positive']}")
    print(f"  negative                     : {completed_dict['negative']}")
    print(f"  neutral                      : {completed_dict['neutral']}")
    print(f"  Float                        : {completed_dict['Float']}")
    print(f"  Exchange                     : {completed_dict['Exchange']}")
    print(f"  FinBERTCompletedAt           : {completed_dict['FinBERTCompletedAt']}")
    print(f"  nocoref_neutral_filter       : {completed_dict['nocoref_neutral_filter']}")
    print(f"  nocoref_confidence_weighted  : {completed_dict['nocoref_confidence_weighted']}")
    print(f"  nocoref_net_score            : {completed_dict['nocoref_net_score']}")
    print(f"  nocoref_top_k                : {completed_dict['nocoref_top_k']}")
    print(f"  nocoref_positional           : {completed_dict['nocoref_positional']}")
    print(f"  NoCorefCompletedAt           : {completed_dict['NoCorefCompletedAt']}")
    print(f"  Trigger                      : {completed_dict['Trigger']}")
    print(f"{'='*60}\n")


# ─── Callback — invoked from NW4 background thread ───────────────────────────

def on_news_accepted(news_dict: dict) -> None:
    """
    Invoked by NewsWatcher4 for every article that passes all filters.

    NW4's `Symbol` field is comma-joined for multi-ticker articles (up to 2).
    Strategy: fan-out per ticker. FinBERT-headliner and the body pipeline each
    run **once per article**; their futures are shared by every ticker's
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
    f_body    = _body_executor.submit(_run_body_pipeline, news_dict)
    for symbol in tickers:
        _collect_executor.submit(
            _collect_and_log, news_dict, tickers, symbol, f_finbert, f_body,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Orchestrator3.5 starting...")

    for d in (OUTPUT_DIR, TM_OUTPUT_DIR, TM_LOG_DIR):
        if d:
            os.makedirs(d, exist_ok=True)

    logger.info("Pre-loading FinBERT-headliner model...")
    load_model()
    logger.info("FinBERT-headliner model ready.")

    # Pre-warm body pipeline workers: submit N no-op tasks and wait. Each
    # worker thread runs _init_body_worker on first task pickup, so by the
    # time _futures_wait returns, every worker has its FinBERTBodyPipeline
    # fully loaded. This guarantees no article ever pays the cold-start cost.
    logger.info(f"Pre-warming {BODY_FINBERT_WORKERS} FinBERT body pipeline worker(s)...")
    _futures_wait([_body_executor.submit(lambda: None)
                   for _ in range(BODY_FINBERT_WORKERS)])
    logger.info("All body pipeline workers ready.")

    _load_latest_iti_tsv()
    _load_excluded_strings(TM_EXCLUDED_STRINGS_FILE)

    _universe_tsv = _resolve_latest_universe_tsv()
    logger.info(f"[Universe] Using universe TSV: {_universe_tsv}")

    # Register callback BEFORE start() — no race window for missed items
    nw.register_callback(on_news_accepted)

    nw.start(
        universe_tsv=_universe_tsv,
        black_list=NW4_BLACK_LIST,
        blacklist_expiry_hours=NW4_BLACKLIST_EXPIRY_HOURS,
        api_keys=NW4_API_KEYS,
        log_dir=NW4_LOG_DIR,
        output_dir=NW4_OUTPUT_DIR,
        news_df_dir=NW4_NEWS_DF_DIR,
        blocked_dir=NW4_BLOCKED_DIR,
        accepted_dir=NW4_ACCEPTED_DIR,
        excluded_strings_file=NW4_EXCLUDED_STRINGS_FILE,
        priced_tsv=_universe_tsv,
        reject_float_greater_then=NW4_REJECT_FLOAT_GT,
        reject_price_greater_then=NW4_REJECT_PRICE_GT,
        flush_interval_seconds=NW4_FLUSH_INTERVAL_SEC,
    )

    logger.info("NewsWatcher4 started. Waiting for news... (Ctrl+C to stop)")

    # ── Keep-alive ────────────────────────────────────────────────────────────
    _stop_event = threading.Event()

    _iti_thread = threading.Thread(
        target=_iti_reload_worker, args=(_stop_event,),
        daemon=True, name='iti-reload',
    )
    _iti_thread.start()

    def _handle_sigint(signum, frame):
        logger.info("Shutdown signal received — stopping...")
        _stop_event.set()

    # Register AFTER nw.start() so Orchestrator3.5's handlers override NW4's
    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        logger.info("Shutting down executors...")
        _finbert_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        _body_executor.shutdown(wait=True)
        logger.info("Stopping NewsWatcher4...")
        nw.stop()
        logger.info("Orchestrator3.5 stopped.")


if __name__ == '__main__':
    main()
