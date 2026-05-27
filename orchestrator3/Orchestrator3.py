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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/newswatcher3')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N2/scripts/volume/trade_frequency_baseline')

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

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = "/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/tables"
# ─────────────────────────────────────────────────────────────────────────────

import NewsWatcher3 as nw
import pre_trade_frequency_baseline as ptb

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
NW3_REJECT_FLOAT_GT       = 300        # M shares; matches old universe filter
NW3_REJECT_PRICE_GT       = 15.00
NW3_FLUSH_INTERVAL_SEC    = 3600
# ─────────────────────────────────────────────────────────────────────────────

# ── Trade-frequency baseline (per-ticker IBKR call) ───────────────────────────
TF_BASE_CLIENT_ID  = 999          # first clientID; auto-increments per call
TF_HOST            = '127.0.0.1'
TF_PORT            = 4001         # 4001=live GW  4002=paper GW  7496/7497=TWS
TF_ON_TRIGGER      = True         # True → upper bound = "now"; False → use TF_START_TIME
TF_START_TIME      = '09:30:00'   # only used when TF_ON_TRIGGER is False
TF_TICKS_QUANTITY  = 20
TF_CROSS_SESSION   = True
TF_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/outputs'
TF_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/logs'
TF_CONNECT_TIMEOUT = 10
TF_FETCH_TIMEOUT   = 5
# ─────────────────────────────────────────────────────────────────────────────

# ── Trade-mole trigger (per-ticker subprocess launch) ─────────────────────────
TM_SENTIMENT_SCORE_MIN = 0.05     # launch only if sentiment_score >  this
TM_FLOAT_MAX_M         = 300     # launch only if Float (millions) <  this
TM_REQUIRE_VALID_TPS   = True    # require Trades/sec > 0 (else skip + warn)

TM_SCRIPT          = '/home/tom/Documents/ibkr_scripts/N2/scripts/volume/trade_surge_mole/trade_mole.py'
TM_BASE_CLIENT_ID  = 400         # first clientID; auto-increments per launch
TM_LIFETIME        = '05:00'     # mm:ss — passed to --lifeTime
TM_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_outputs'
TM_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/trade_mole_logs'
TM_HOST            = '127.0.0.1'
TM_PORT            = 4001        # 4001=live GW  4002=paper GW  7496/7497=TWS
TM_PYTHON          = sys.executable   # same interpreter as Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('Orchestrator3')

_priced_df: pd.DataFrame = None

# ─── ThreadPoolExecutor ───────────────────────────────────────────────────────

# _executor: FinBERT + _collect_and_log (1 FinBERT + up to 2 collect per article → 4 workers)
# _tf_executor: analyze_trade_frequency only — isolated so TF tasks are never queued
#               behind FinBERT inference or collect I/O during a news burst.
_executor    = ThreadPoolExecutor(max_workers=4, thread_name_prefix='echo_worker')
_tf_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='tf_worker')
_tsv_lock    = threading.Lock()

_tf_clientid_counter = itertools.count(TF_BASE_CLIENT_ID)
_tf_clientid_lock    = threading.Lock()

_tm_clientid_counter = itertools.count(TM_BASE_CLIENT_ID)
_tm_clientid_lock    = threading.Lock()


def _next_tf_clientid() -> int:
    with _tf_clientid_lock:
        return next(_tf_clientid_counter)


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


def analyze_trade_frequency(news_dict: dict, symbol: str) -> dict:
    """
    Per-ticker IBKR tick fetch via pre_trade_frequency_baseline. Returns
    {'trades_per_sec': float|None}. Never raises — failures yield None.
    """
    client_id = _next_tf_clientid()

    now_et = datetime.now(ZoneInfo(ptb.TIMEZONE))
    end_hms = now_et.strftime('%H:%M:%S') if TF_ON_TRIGGER else TF_START_TIME
    end_dt_str = f"{now_et.strftime('%Y%m%d')} {end_hms} {ptb.TIMEZONE}"
    n_ticks = min(TF_TICKS_QUANTITY, ptb.MAX_TICKS)

    log_handler = None
    root_logger = logging.getLogger()
    if TF_LOG_DIR:
        try:
            ts = now_et.strftime('%Y-%m-%d_%H-%M-%S')
            log_path = os.path.join(TF_LOG_DIR, f"{symbol}_{ts}_{client_id}.log")
            log_handler = logging.FileHandler(log_path, mode='w', encoding='utf-8')
            log_handler.setFormatter(logging.Formatter(
                '%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s',
                datefmt='%H:%M:%S',
            ))
            root_logger.addHandler(log_handler)
        except Exception as exc:
            logger.warning(f"[TF] could not open log file for {symbol}: {exc}")
            log_handler = None

    app = None
    api_thread = None
    try:
        logger.info(
            f"[TF] {symbol} clientId={client_id} ticks={n_ticks} "
            f"end={end_dt_str} cross_session={TF_CROSS_SESSION}"
        )
        app = ptb.PreBaselineApp()
        app.connect(TF_HOST, TF_PORT, clientId=client_id)
        api_thread = threading.Thread(
            target=app.run, daemon=True, name=f'tf-ibapi-{client_id}'
        )
        api_thread.start()

        if not app.connected_event.wait(timeout=TF_CONNECT_TIMEOUT):
            logger.warning(
                f"[TF] {symbol} clientId={client_id}: IBKR connect timeout "
                f"after {TF_CONNECT_TIMEOUT}s"
            )
            return {'trades_per_sec': None}

        contract = ptb.make_contract(symbol)
        ticks = app.fetch_ticks(
            contract=contract, end_dt_str=end_dt_str, n_ticks=n_ticks,
        )

        if TF_CROSS_SESSION and len(ticks) < n_ticks and ticks:
            prev_end_unix = ticks[0].time - 1
            prev_end_str = datetime.fromtimestamp(
                prev_end_unix, ZoneInfo(ptb.TIMEZONE)
            ).strftime('%Y%m%d %H:%M:%S') + f" {ptb.TIMEZONE}"
            logger.info(
                f"[TF] {symbol}: cross-session follow-up "
                f"({len(ticks)}/{n_ticks}) end={prev_end_str}"
            )
            prev_ticks = app.fetch_ticks(
                contract=contract, end_dt_str=prev_end_str, n_ticks=n_ticks,
            )
            if prev_ticks:
                merged = list(ticks) + list(prev_ticks)
                seen = set()
                deduped = []
                for t in merged:
                    key = (t.time, float(t.price),
                           float(getattr(t, 'size', 0) or 0),
                           getattr(t, 'exchange', ''))
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(t)
                deduped.sort(key=lambda t: t.time)
                ticks = deduped[-n_ticks:]

        row = ptb.compute_pre_stats(ticks, symbol, end_dt_str)

        if TF_OUTPUT_DIR:
            try:
                col_order = [
                    'symbol', 'window_end', 'n_ticks',
                    'first_tick_ts', 'last_tick_ts', 'span_sec',
                    'iti_mean_sec', 'iti_median_sec', 'iti_min_sec', 'iti_max_sec',
                    'trades_per_sec', 'trades_per_min',
                    'total_volume', 'avg_price',
                ]
                df = pd.DataFrame([row])
                df = df[[c for c in col_order if c in df.columns]]
                out_path = ptb._resolve_output_path(TF_OUTPUT_DIR, symbol)
                df.to_csv(out_path, index=False)
            except Exception as exc:
                logger.warning(f"[TF] {symbol}: CSV write failed: {exc}")

        tps = row.get('trades_per_sec')
        logger.info(
            f"[TF] {symbol} clientId={client_id} → "
            f"trades_per_sec={tps if tps is None else f'{tps:.4f}'} "
            f"(n_ticks={row.get('n_ticks')})"
        )
        return {'trades_per_sec': tps}

    except Exception as exc:
        logger.error(
            f"[TF] {symbol} clientId={client_id} failed: {exc}", exc_info=True
        )
        return {'trades_per_sec': None}

    finally:
        try:
            if app is not None:
                app.disconnect()
        except Exception:
            pass
        try:  # force-close socket when connState is CONNECTING (isConnected() is False there)
            if app is not None and getattr(app, 'conn', None) is not None:
                app.conn.disconnect()
        except Exception:
            pass
        if api_thread is not None:
            api_thread.join(timeout=3)
        if log_handler is not None:
            root_logger.removeHandler(log_handler)
            try:
                log_handler.close()
            except Exception:
                pass


# ─── Trigger evaluator ───────────────────────────────────────────────────────

def evaluate_trigger(completed_dict: dict) -> str:
    """Returns 'YES' if all trade_mole conditions are met, else 'NO:cond1,cond2,...'."""
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')
    tps       = completed_dict.get('Trades/sec')

    failures = []
    if sentiment is None or sentiment <= TM_SENTIMENT_SCORE_MIN:
        failures.append('sentiment_score')
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        failures.append('Float')
    if TM_REQUIRE_VALID_TPS and (tps is None or tps <= 0):
        failures.append('Trades/sec')

    return 'YES' if not failures else 'NO:' + ','.join(failures)


# ─── Trade-mole launcher ──────────────────────────────────────────────────────

def maybe_launch_trade_mole(completed_dict: dict) -> None:
    """Fire-and-forget launch of trade_mole.py if trigger conditions are met
    for this single ticker. Detached subprocess: survives Orchestrator shutdown."""
    symbol    = completed_dict['Symbol']
    sentiment = completed_dict.get('sentiment_score')
    float_m   = completed_dict.get('Float')
    tps       = completed_dict.get('Trades/sec')

    if sentiment is None or sentiment <= TM_SENTIMENT_SCORE_MIN:
        return
    if float_m is None or float_m >= TM_FLOAT_MAX_M:
        return
    if TM_REQUIRE_VALID_TPS and (tps is None or tps <= 0):
        logger.warning(
            f"[TM] {symbol}: trigger conditions met (sent={sentiment:.4f}, "
            f"float={float_m}) but Trades/sec={tps} is invalid — skipping launch"
        )
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
        '--baseline-trade-per-second', f'{tps:.6f}',
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
            f"tps={tps:.4f} sentiment={sentiment:.4f} float={float_m} → {log_path}"
        )
    except Exception as exc:
        logger.error(f"[TM] {symbol}: failed to launch trade_mole: {exc}", exc_info=True)


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = ['Symbol', 'Tickers', 'ID', 'ArrivalTime', 'Headline', 'Author',
                'Float',
                'positive', 'negative', 'neutral', 'sentiment_score', 'label',
                'Trades/sec', 'Trigger']


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
                     f_finbert, f_tradefreq) -> None:
    """Resolves analysis futures for one ticker, appends a TSV row, and
    evaluates the trade_mole trigger."""
    news_id = news_dict['ID']
    try:
        finbert_val = f_finbert.result(timeout=60)
    except Exception as exc:
        logger.error(f"Error in analysis for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}

    try:
        tradefreq_val = f_tradefreq.result(timeout=120)
    except Exception as exc:
        logger.error(
            f"Error in trade-frequency analysis for id={news_id} symbol={symbol}: {exc}",
            exc_info=True,
        )
        tradefreq_val = {}

    completed_dict = {
        'Symbol':          symbol,
        'Tickers':         json.dumps(tickers),
        'ID':              news_id,
        'ArrivalTime':     news_dict['ArrivalTime'].replace(microsecond=0),
        'Headline':        news_dict['Headline'],
        'Author':          _lookup_author(news_id),
        'Float':           _lookup_float(symbol),
        'positive':        finbert_val.get('positive'),
        'negative':        finbert_val.get('negative'),
        'neutral':         finbert_val.get('neutral'),
        'sentiment_score': finbert_val.get('sentiment_score'),
        'label':           finbert_val.get('label'),
        'Trades/sec':      tradefreq_val.get('trades_per_sec'),
    }
    completed_dict['Trigger'] = evaluate_trigger(completed_dict)

    _append_to_tsv(completed_dict)
    maybe_launch_trade_mole(completed_dict)
    logger.info(f"Completed news_dict: {completed_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol          : {completed_dict['Symbol']}")
    print(f"  Tickers         : {completed_dict['Tickers']}")
    print(f"  ID              : {completed_dict['ID']}")
    print(f"  ArrivalTime     : {completed_dict['ArrivalTime']}")
    print(f"  Headline        : {completed_dict['Headline']}")
    print(f"  Author          : {completed_dict['Author']}")
    print(f"  FinBERT label   : {completed_dict['label']}")
    print(f"  sentiment_score : {completed_dict['sentiment_score']}")
    print(f"  positive        : {completed_dict['positive']}")
    print(f"  negative        : {completed_dict['negative']}")
    print(f"  neutral         : {completed_dict['neutral']}")
    print(f"  Float           : {completed_dict['Float']}")
    print(f"  Trades/sec      : {completed_dict['Trades/sec']}")
    print(f"  Trigger         : {completed_dict['Trigger']}")
    print(f"{'='*60}\n")


# ─── Callback — invoked from NW3 background thread ───────────────────────────

def on_news_accepted(news_dict: dict) -> None:
    """
    Invoked by NewsWatcher3 for every article that passes all filters.

    NW3's `Symbol` field is comma-joined for multi-ticker articles (up to 2).
    Strategy: fan-out per ticker. FinBERT runs once per article and its future
    is shared by every ticker's _collect_and_log task. TF
    analysis and trade_mole evaluation run independently per ticker.
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

    f_finbert = _executor.submit(analyze_finbert, news_dict)
    for symbol in tickers:
        f_tradefreq = _tf_executor.submit(analyze_trade_frequency, news_dict, symbol)
        _executor.submit(_collect_and_log, news_dict, tickers, symbol,
                         f_finbert, f_tradefreq)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Orchestrator3 starting...")

    for d in (OUTPUT_DIR, TF_OUTPUT_DIR, TF_LOG_DIR, TM_OUTPUT_DIR, TM_LOG_DIR):
        if d:
            os.makedirs(d, exist_ok=True)

    logger.info("Pre-loading FinBERT model...")
    load_model()
    logger.info("FinBERT model ready.")

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

    # Register AFTER nw.start() so Orchestrator3's handlers override NW3's
    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        logger.info("Shutting down executors...")
        _executor.shutdown(wait=True)
        _tf_executor.shutdown(wait=True)
        logger.info("Stopping NewsWatcher3...")
        nw.stop()
        logger.info("Orchestrator3 stopped.")


if __name__ == '__main__':
    main()
