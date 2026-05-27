import sys
import os
import csv
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

sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2')
sys.path.insert(0, '/home/tom/Documents/ibkr_scripts/N1/scripts/volume/trade_frequency_baseline')

# ── FinBERT import (hyphen in filename prevents normal import) ────────────────
_finbert_spec = _ilu.spec_from_file_location(
    "FinBERT_headliner",
    "/home/tom/Documents/ibkr_scripts/N1/scripts/FinBERT/FinBERT-headliner.py",
)
_finbert_mod = _ilu.module_from_spec(_finbert_spec)
_finbert_spec.loader.exec_module(_finbert_mod)
analyze_headline = _finbert_mod.analyze_headline
load_model       = _finbert_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── TSV output ────────────────────────────────────────────────────────────────
OUTPUT_DIR = "/home/tom/Documents/ibkr_scripts/N1/scripts/orchestrator/outputs"
# ─────────────────────────────────────────────────────────────────────────────

# ── Fake stream option ────────────────────────────────────────────────────────
# 'YES' → replay a JSON file through NW2's real 7 filters (no network needed).
# 'NO'  → connect to the real Alpaca WebSocket stream.
# Must be evaluated before `import NewsWatcher2` — the patch replaces
# alpaca.data.live.news.NewsDataStream before NW2's `from ... import` runs.
FAKE_STREAM = 'NO'
if FAKE_STREAM == 'YES':
    import fake_Alpaca_WebSocket_stream  # noqa: F401 — side-effect: patches alpaca module
# ─────────────────────────────────────────────────────────────────────────────

import NewsWatcher2 as nw
import yfinance_stock_universe
import pre_trade_frequency_baseline as ptb

# ── Trade-frequency baseline (per-news IBKR call) ─────────────────────────────
TF_BASE_CLIENT_ID  = 999          # first clientID; auto-increments per call
TF_HOST            = '127.0.0.1'
TF_PORT            = 4001         # 4001=live GW  4002=paper GW  7496/7497=TWS
TF_ON_TRIGGER      = True         # True → upper bound = "now"; False → use TF_START_TIME
TF_START_TIME      = '09:30:00'   # only used when TF_ON_TRIGGER is False
TF_TICKS_QUANTITY  = 50
TF_CROSS_SESSION   = True
TF_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N1/scripts/orchestrator/outputs'
TF_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N1/scripts/orchestrator/logs'
TF_CONNECT_TIMEOUT = 10
TF_FETCH_TIMEOUT   = 5
# ─────────────────────────────────────────────────────────────────────────────

# ── Trade-mole trigger (per-news subprocess launch) ───────────────────────────
# Trigger conditions — edit thresholds here.
TM_SENTIMENT_SCORE_MIN = 0.2     # launch only if sentiment_score >  this
TM_FLOAT_MAX_M         = 100     # launch only if Float (millions) <  this
TM_REQUIRE_VALID_TPS   = True    # require Trades/sec > 0 (else skip + warn)

# trade_mole.py launch options — edit defaults here.
TM_SCRIPT          = '/home/tom/Documents/ibkr_scripts/N1/scripts/volume/trade_surge_mole/trade_mole.py'
TM_BASE_CLIENT_ID  = 400         # first clientID; auto-increments per launch
TM_LIFETIME        = '05:00'     # mm:ss — passed to --lifeTime
TM_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N1/scripts/orchestrator/trade_mole_outputs'
TM_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N1/scripts/orchestrator/trade_mole_logs'
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
logger = logging.getLogger('Orchestrator')

_nasdaq_df: pd.DataFrame = None

# ─── ThreadPoolExecutor for parallel echo work ────────────────────────────────

# max_workers=6: analyze_finbert + echo2 + analyze_trade_frequency + _collect_and_log
# + headroom for overlapping news items
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='echo_worker')
_tsv_lock = threading.Lock()

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
    """FinBERT headline sentiment analysis (replaces echo1)."""
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


def echo2(news_dict: dict) -> str:
    """Placeholder for second analysis pass (e.g. NER / pronounCer)."""
    result = f"[echo2] Symbol={news_dict['Symbol']} Headline='{news_dict['Headline'][:60]}'"
    logger.info(result)
    return result


def analyze_trade_frequency(news_dict: dict) -> dict:
    """
    Per-news IBKR tick fetch via pre_trade_frequency_baseline. Returns
    {'trades_per_sec': float|None}. Never raises — failures yield None.
    """
    symbol = news_dict['Symbol']
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
            if app is not None and app.isConnected():
                app.disconnect()
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


# ─── Trade-mole launcher ──────────────────────────────────────────────────────

def maybe_launch_trade_mole(completed_dict: dict) -> None:
    """Fire-and-forget launch of trade_mole.py if trigger conditions are met.
    Detached subprocess: survives Orchestrator shutdown."""
    symbol = completed_dict['Symbol']
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

_TSV_COLUMNS = ['Symbol', 'ID', 'ArrivalTime', 'Headline', 'Float',
                'positive', 'negative', 'neutral', 'sentiment_score', 'label',
                'Trades/sec']


def _append_to_tsv(completed_dict: dict) -> None:
    """Appends one row to the daily TSV output file. Thread-safe."""
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


# ─── Callback — called from NewsWatcher2 background thread ───────────────────

def _collect_and_log(news_dict: dict, f_finbert, f_echo2, f_tradefreq) -> None:
    """Collects results from analyze_finbert, echo2, and analyze_trade_frequency,
    then logs the completed dict."""
    symbol  = news_dict['Symbol']
    news_id = news_dict['ID']
    try:
        finbert_val = f_finbert.result(timeout=60)
        echo2_val   = f_echo2.result(timeout=60)
    except Exception as exc:
        logger.error(f"Error in analysis for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        echo2_val   = f"ERROR: {exc}"

    try:
        tradefreq_val = f_tradefreq.result(timeout=120)
    except Exception as exc:
        logger.error(
            f"Error in trade-frequency analysis for id={news_id}: {exc}",
            exc_info=True,
        )
        tradefreq_val = {}

    float_val = (
        _nasdaq_df.loc[_nasdaq_df['Symbol'] == symbol, 'Float_M'].iloc[0]
        if _nasdaq_df is not None and symbol in _nasdaq_df['Symbol'].values
        else None
    )

    completed_dict = {
        'Symbol':          symbol,
        'ID':              news_id,
        'ArrivalTime':     news_dict['ArrivalTime'].replace(microsecond=0),
        'Headline':        news_dict['Headline'],
        'Float':           float_val,
        'positive':        finbert_val.get('positive'),
        'negative':        finbert_val.get('negative'),
        'neutral':         finbert_val.get('neutral'),
        'sentiment_score': finbert_val.get('sentiment_score'),
        'label':           finbert_val.get('label'),
        'Trades/sec':      tradefreq_val.get('trades_per_sec'),
        'Echo2':           echo2_val,
    }

    _append_to_tsv(completed_dict)
    maybe_launch_trade_mole(completed_dict)
    logger.info(f"Completed news_dict: {completed_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol          : {completed_dict['Symbol']}")
    print(f"  ID              : {completed_dict['ID']}")
    print(f"  ArrivalTime     : {completed_dict['ArrivalTime']}")
    print(f"  Headline        : {completed_dict['Headline']}")
    print(f"  FinBERT label   : {completed_dict['label']}")
    print(f"  sentiment_score : {completed_dict['sentiment_score']}")
    print(f"  positive        : {completed_dict['positive']}")
    print(f"  negative        : {completed_dict['negative']}")
    print(f"  neutral         : {completed_dict['neutral']}")
    print(f"  Float           : {completed_dict['Float']}")
    print(f"  Trades/sec      : {completed_dict['Trades/sec']}")
    print(f"  Echo2           : {completed_dict['Echo2']}")
    print(f"{'='*60}\n")


def on_news_accepted(news_dict: dict) -> None:
    """
    Invoked by NewsWatcher2 for every item that passes all 7 filters.

    Submits analyze_finbert and echo2 to the executor in parallel, then
    immediately submits _collect_and_log (fire-and-forget) so the NW2
    asyncio thread is never blocked by slow ML inference.
    """
    symbol  = news_dict['Symbol']
    news_id = news_dict['ID']
    logger.info(f"on_news_accepted triggered: {symbol} id={news_id}")

    f_finbert   = _executor.submit(analyze_finbert, news_dict)
    f_echo2     = _executor.submit(echo2, news_dict)
    f_tradefreq = _executor.submit(analyze_trade_frequency, news_dict)
    _executor.submit(_collect_and_log, news_dict, f_finbert, f_echo2, f_tradefreq)  # returns immediately


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info("Orchestrator starting...")

    if TF_OUTPUT_DIR:
        os.makedirs(TF_OUTPUT_DIR, exist_ok=True)
    if TF_LOG_DIR:
        os.makedirs(TF_LOG_DIR, exist_ok=True)
    if TM_OUTPUT_DIR:
        os.makedirs(TM_OUTPUT_DIR, exist_ok=True)
    if TM_LOG_DIR:
        os.makedirs(TM_LOG_DIR, exist_ok=True)

    logger.info("Pre-loading FinBERT model...")
    load_model()
    logger.info("FinBERT model ready.")

    #symbols = yfinance_stock_universe.fetch(max_market_cap=300) #The yfinance_stock_universe script was missing some rare symbols, it is not used for now
    #logger.info(f"Universe: {len(symbols)} symbols")
    global _nasdaq_df
    _nasdaq_df = pd.read_csv('/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/data/nasdaq_symbols_data.tsv', sep='\t')
    symbols = _nasdaq_df[_nasdaq_df['Float_M'] < 300]['Symbol'].tolist()

    # Register callback BEFORE start() — no race window for missed items
    nw.register_callback(on_news_accepted)

    excluded_strings_file = "/home/tom/Documents/ibkr_scripts/N1/scripts/orchestrator/excluded_strings.txt"
    with open(excluded_strings_file) as f:
        excluded_strings = [line.strip() for line in f if line.strip()]

    nw.start(
        stock_universe=symbols,
        black_list="/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/black_list.csv",
        blacklist_expiry_days=15,
        api_keys="/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/alpaca_API-Keys.txt",
        flush_interval_seconds=3600,
        news_df_dir="/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/outputs",
        excluded_strings=excluded_strings,
    )

    logger.info("NewsWatcher2 started. Waiting for news... (Ctrl+C to stop)")

    # ── Keep-alive ────────────────────────────────────────────────────────────
    _stop_event = threading.Event()

    def _handle_sigint(signum, frame):
        logger.info("Shutdown signal received — stopping...")
        _stop_event.set()

    # Register AFTER nw.start() so Orchestrator's handlers override NW2's
    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        logger.info("Shutting down executor...")
        _executor.shutdown(wait=True)
        logger.info("Stopping NewsWatcher2...")
        nw.stop()
        logger.info("Orchestrator stopped.")


if __name__ == '__main__':
    main()
