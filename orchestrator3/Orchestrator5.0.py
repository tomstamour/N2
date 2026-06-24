import sys
import os
import csv
import json
import logging
import threading
import socket
import asyncio
import importlib.util as _ilu
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime
from zoneinfo import ZoneInfo
import glob
from pathlib import Path

# ── Repo-relative anchor + centralized per-user config ────────────────────────
# Every sibling script lives one level under scripts/, so this resolves the
# repo's scripts/ dir from THIS file — no hard-coded /home/... paths. Per-user
# credentials/connection live in config/n2_config_file.txt (RTPR key, clerk host/port).
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_cfg_spec = _ilu.spec_from_file_location("n2_config", SCRIPTS_DIR / "config" / "n2_config.py")
n2_config = _ilu.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(n2_config)
_CFG = n2_config.load_config()
N2_CONFIG_FILE = SCRIPTS_DIR / "config" / "n2_config_file.txt"

sys.path.insert(0, str(SCRIPTS_DIR / 'universe_finder'))
sys.path.insert(0, str(SCRIPTS_DIR / 'FinBERT_pipeline' / 'FinBERT_body_noCoref'))

# ── FinBERT import (hyphen in filename prevents normal import) ────────────────
_finbert_spec = _ilu.spec_from_file_location(
    "FinBERT_headliner",
    str(SCRIPTS_DIR / "FinBERT" / "FinBERT-headliner.py"),
)
_finbert_mod = _ilu.module_from_spec(_finbert_spec)
_finbert_spec.loader.exec_module(_finbert_mod)
analyze_headline = _finbert_mod.analyze_headline
load_model       = _finbert_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── FinBERT body pipeline (noCoref) + neutral-management add-on ───────────────
from FinBERT_body_noCoref import FinBERTBodyPipeline
from finBERT_neutral_management_addON import aggregate as _nocoref_aggregate
# ─────────────────────────────────────────────────────────────────────────────

# ── RTPR_connector import (the news source) ──────────────────────────────────
# Orchestrator5.0 drives RTPR_connector's asyncio loop IN-PROCESS and taps each
# article via the connector's latency-neutral hooks (set_alert_hook / set_row_hook,
# both default None so a standalone connector run is unaffected). Executing the
# module only defines its functions/Cfg/hooks — main() is __main__-guarded.
_rtpr_spec = _ilu.spec_from_file_location(
    "RTPR_connector",
    str(SCRIPTS_DIR / "RTPR_connector" / "RTPR_connector.py"),
)
rtpr = _ilu.module_from_spec(_rtpr_spec)
_rtpr_spec.loader.exec_module(rtpr)
# ─────────────────────────────────────────────────────────────────────────────

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = str(SCRIPTS_DIR / "orchestrator3" / "tables")
# ─────────────────────────────────────────────────────────────────────────────

# ── Universe / filtering inputs ───────────────────────────────────────────────
UNIVERSE_DATA_DIR = str(SCRIPTS_DIR / 'universe_finder' / 'data')
# NW4-level excluded strings: a headline containing any of these DROPS the article
# entirely (no TSV row, no clerk arm) — one of the two NW4 input filters kept.
NW4_EXCLUDED_STRINGS_FILE  = str(SCRIPTS_DIR / 'newswatcher3' / 'excluded_strings.txt')
# Orchestrator-level excluded strings (arm-block): a matching headline is still
# written to the TSV but is NOT armed at the clerk (kept clerk-side gate).
ORCH_EXCLUDED_STRINGS_FILE = str(SCRIPTS_DIR / 'orchestrator3' / 'excluded_strings-2.txt')
MAX_TICKERS_PER_ARTICLE    = 2   # NW4 count filter: drop articles naming > 2 tickers
# ─────────────────────────────────────────────────────────────────────────────

# ── Clerk connection (x-wing-mole/clerk-1.1.py) ───────────────────────────────
# Warm pool of pre-connected ibapi clients running trade-mole + x-wing duos,
# driven over TCP/JSON with a two-step handshake:
#   STEP 1a (ARM)       on PR alert  → {ticker, lastDailyClose, itiBaseline, tradeSizeBaseline}
#   STEP 1b (SENTIMENT) after FinBERT → {ticker, Sentiment: "OK"|"BAD"}
# Start the clerk FIRST, e.g.:
#   path/to/venv/bin/python clerk-1.1.py --client-qty 5 --port 4002 --listen-port 8765
# (--port 4001 is the LIVE Gateway — real money. Use 4002 = paper GW for testing.)
CLERK_HOST             = n2_config.get(_CFG, 'CLERK_HOST', '127.0.0.1')
CLERK_PORT             = n2_config.get_int(_CFG, 'CLERK_PORT', 8765)
CLERK_TIMEOUT_SEC      = 5       # per-message TCP send/recv timeout
CLERK_ARM_WAIT_SEC     = 20      # max wait on the arm future before sending sentiment

# Sentiment gate (STEP 1b): headline FinBERT `positive` is the sole criterion.
SENTIMENT_POSITIVE_MIN = 0.8     # positive >= this → Sentiment "OK", else "BAD"

# Baseline lookups from the daily universe TSV.
TRADE_SIZE_SENTINEL  = 44444.0   # trade-mole treats this (or <=0/missing) as "no baseline"
DEFAULT_BASELINE_ITI = 44444.0   # fallback ITI (s) when symbol absent/NaN in universe
# ─────────────────────────────────────────────────────────────────────────────

# ── RTPR_connector (news source) knobs ────────────────────────────────────────
# Mirror RTPR_connector's CLI defaults so its hot path is byte-for-byte unchanged.
RTPR_API_KEY_FILE  = str(SCRIPTS_DIR / 'RTPR_connector' / 'RTPR_API-Key.txt')
RTPR_WORKERS       = 32          # concurrent-fetch ceiling (async worker coroutines)
RTPR_FETCH_TIMEOUT = 20.0        # per-fetch timeout (s)
RTPR_MAX_RETRIES   = 0           # single attempt — no retry storm during bursts
RTPR_QUEUE_MAX     = 10000
RTPR_OUT_DIR       = str(SCRIPTS_DIR / 'RTPR_connector' / 'tables')  # connector's own daily flush
RTPR_LOG_DIR       = str(SCRIPTS_DIR / 'RTPR_connector' / 'logs')
RTPR_FLUSH_AT      = "20:35"     # connector's once-daily CSV flush (HH:MM ET)
# ─────────────────────────────────────────────────────────────────────────────

# ── FinBERT body pipeline (noCoref) + neutral-management ──────────────────────
BODY_FINBERT_WORKERS      = 2     # one pipeline instance per worker thread
BODY_FINBERT_TIMEOUT_SEC  = 180   # per-article ceiling; clerk already armed/sentiment-sent by now
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
logger = logging.getLogger('Orchestrator5.0')

_iti_df: pd.DataFrame = None
_last_iti_reload_date = None
_iti_df_lock = threading.Lock()

# Full in-universe symbol set (Symbol column of the latest universe TSV). With the
# float filter dropped, this is simply universe membership — used to gate arming of
# partner tickers and TSV fan-out. Rebound (not mutated) on reload so lock-free reads
# always see a consistent set object.
_inscope_set: set = set()

# Headline filters. _nw_excluded_strings DROPS the article (NW4 input filter kept);
# _excluded_strings (orchestrator-level) only blocks the clerk arm (clerk gate kept).
_nw_excluded_strings: set = set()
_excluded_strings: set = set()


def _resolve_latest_universe_tsv() -> str:
    """Return the most recent stocks_universe_YYYY-MM-DD.tsv in UNIVERSE_DATA_DIR,
    falling back to the non-dated file if none is found."""
    pattern = os.path.join(UNIVERSE_DATA_DIR, 'stocks_universe_????-??-??.tsv')
    matches = sorted(glob.glob(pattern))
    if matches:
        return matches[-1]
    fallback = os.path.join(UNIVERSE_DATA_DIR, 'stocks_universe.tsv')
    logger.warning(f"[Universe] No dated universe TSV found — falling back to {fallback}")
    return fallback


def _load_excluded_strings(path: str) -> set:
    """Load a set of excluded substrings from a file (one per line). Returns an
    empty set on any error so filtering from that file becomes a no-op."""
    try:
        with open(path, encoding='utf-8') as f:
            strings = {line.strip() for line in f if line.strip()}
        logger.info(f"[Filter] Loaded {len(strings)} excluded strings from {path}: {sorted(strings)}")
        return strings
    except Exception as exc:
        logger.warning(f"[Filter] Could not load {path}: {exc} — no filtering from this file")
        return set()

# ─── ThreadPoolExecutors ──────────────────────────────────────────────────────

_finbert_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='finbert')
_collect_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix='collect')

# Body pipeline is NOT thread-safe (shared spaCy nlp, ONNX session, TickerResolver
# cache). Each worker gets its own instance loaded once via the ThreadPoolExecutor
# initializer — guaranteed before any article arrives thanks to the pre-warm loop
# in main().
_body_local = threading.local()


def _init_body_worker() -> None:
    """ThreadPoolExecutor initializer: runs exactly once per worker thread.
    All heavy models (spaCy, ONNX FinBERT, SEC-EDGAR ticker map) are loaded here
    so no article ever pays the cold-start penalty."""
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

# Dedicated pool for clerk STEP-1a arms so arming is never starved by the collect
# tasks. Sized a touch above the clerk's default 5-client pool.
_clerk_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='clerk')

# Arm futures created on the RTPR alert (pre-fetch, via _on_alert) and resolved
# later either by _on_row_built (article accepted) or _release_armed (article
# dropped before acceptance). Keyed by (art_id, ticker).
_arm_results: dict = {}
_arm_lock = threading.Lock()


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
    """Run the noCoref body pipeline on news_dict. Returns the full result dict, or
    {} if the body is missing/empty or any exception is raised.

    A shallow-copied dict is passed to the pipeline with a `tickers` list injected
    (derived from the comma-joined Symbol). The pipeline's NER fallback uses
    `tickers` as allowed_tickers when SEC EDGAR fails to resolve, ensuring every
    fan-out ticker has a chance to be scored."""
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
    """Run the 5 neutral-management aggregations for `symbol` against the body
    pipeline's per-sentence FinBERT output. Returns a dict with 5 float keys
    (rounded to 4 decimals) or all-None on any missing data."""
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


# ─── Sentiment gate ──────────────────────────────────────────────────────────

def evaluate_sentiment(finbert_val: dict) -> bool:
    """STEP-1b gate: the headline FinBERT `positive` probability is the sole
    criterion. Returns True ("OK") iff positive >= SENTIMENT_POSITIVE_MIN.
    A missing/None positive (FinBERT error) returns False ("BAD")."""
    positive = finbert_val.get('positive')
    return positive is not None and positive >= SENTIMENT_POSITIVE_MIN


# ─── Clerk senders (TCP/JSON to clerk-1.1.py) ────────────────────────────────

def _send_clerk(payload: dict) -> dict:
    """Send one JSON object (newline-terminated) to the clerk and read back one
    JSON reply line on the same connection. Never raises — on any socket/parse
    error it logs and returns {} so the news pipeline keeps running."""
    try:
        with socket.create_connection((CLERK_HOST, CLERK_PORT),
                                      timeout=CLERK_TIMEOUT_SEC) as s:
            s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        return json.loads(buf.split(b"\n", 1)[0].decode("utf-8") or "{}")
    except Exception as exc:
        logger.warning(f"[Clerk] send failed for {payload!r}: {exc}")
        return {}


def _arm_clerk(symbol: str) -> dict:
    """STEP 1a — ARM. Build the per-ticker trigger from the daily universe TSV and
    send it so the clerk binds a warm client and starts reqMktData ASAP. Runs on
    _clerk_executor (off the connector loop thread). Returns the reply."""
    payload = {
        "ticker":            symbol,
        "lastDailyClose":    _lookup_last_close(symbol),          # may be None → JSON null
        "itiBaseline":       _lookup_baseline_iti(symbol),
        "tradeSizeBaseline": _lookup_baseline_trade_size(symbol),
    }
    reply = _send_clerk(payload)
    logger.info(
        f"[Clerk] ARM {symbol} close={payload['lastDailyClose']} "
        f"iti={payload['itiBaseline']:.1f} tradeSize={payload['tradeSizeBaseline']:.1f} "
        f"→ {reply or 'no-reply'}"
    )
    return reply


def _send_sentiment(symbol: str, is_ok: bool) -> dict:
    """STEP 1b — SENTIMENT. Inform the clerk whether the headline sentiment gate
    passed. 'BAD' is sent explicitly so the clerk can early-release the client."""
    payload = {"ticker": symbol, "Sentiment": "OK" if is_ok else "BAD"}
    reply = _send_clerk(payload)
    logger.info(f"[Clerk] SENTIMENT {symbol} {payload['Sentiment']} → {reply or 'no-reply'}")
    return reply


# ─── RTPR alert-flow callbacks (pre-fetch arm / release) ─────────────────────

def _on_alert(ticker: str, art_id: str, recv_ts=None) -> None:
    """RTPR alert callback — fired by the connector's _alert_hook the instant a
    universe+dedup-passing alert is enqueued, BEFORE the body is curled. Arm the
    clerk now (so reqMktData starts ASAP, not after the fetch) and stash the future
    keyed by (art_id, ticker); _on_row_built reuses it, or _release_armed frees it
    if the article is later dropped.

    Runs on the connector's asyncio loop thread → must not block, so the arm itself
    runs on _clerk_executor."""
    fut = _clerk_executor.submit(_arm_clerk, ticker)
    with _arm_lock:
        _arm_results[(art_id, ticker)] = fut
        # Safety net: entries are normally popped within ~seconds by _on_row_built
        # or _release_armed. Cap the stash (dropping oldest, insertion order) so an
        # unexpected exception can't leak the dict unbounded.
        while len(_arm_results) > 512:
            _arm_results.pop(next(iter(_arm_results)))
    when = recv_ts.strftime('%H:%M:%S.%f')[:-3] if hasattr(recv_ts, 'strftime') else recv_ts
    logger.info(f"[Clerk] ALERT-ARM {ticker} id={art_id} recv={when}")


def _on_alert_release(ticker: str, art_id: str) -> None:
    """A pre-armed article was dropped before acceptance. Non-blocking on the loop
    thread; defers to _clerk_executor."""
    _clerk_executor.submit(_release_armed, ticker, art_id)


def _release_armed(ticker: str, art_id: str) -> None:
    """Resolve the stashed arm future and, if a warm client was actually bound,
    release it with a BAD sentiment (clerk → sentiment_bad_released)."""
    with _arm_lock:
        fut = _arm_results.pop((art_id, ticker), None)
    if fut is None:
        return
    try:
        arm_reply = fut.result(timeout=CLERK_ARM_WAIT_SEC)
    except Exception as exc:
        logger.warning(f"[Clerk] {ticker} id={art_id}: arm future failed before release: {exc}")
        return
    if arm_reply.get('status') == 'accepted':
        _send_sentiment(ticker, is_ok=False)
        logger.info(f"[Clerk] {ticker} id={art_id}: pre-armed article dropped → released")


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = [
    'Symbol', 'Tickers', 'ID', 'ArrivalDate', 'Created', 'ArrivalTime', 'CurlTime', 'Headline', 'Author',
    'Float', 'MktCap', 'Exchange',
    'LastDailyClose', 'itiBaseline', 'tradeSizeBaseline',
    'FinBERTCompletedAt',
    'positive', 'negative', 'neutral', 'sentiment_score', 'label',
    'Sentiment', 'ClerkArm', 'ClerkSentiment',
    'nocoref_neutral_filter', 'nocoref_confidence_weighted',
    'nocoref_net_score', 'nocoref_top_k', 'nocoref_positional',
    'NoCorefCompletedAt',
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

def _coerce_float(val):
    """float(val) or None for blanks / NaN / non-numeric strings. The universe TSV
    uses the literal string 'skipped_nan' (and blank cells) for ~13% of rows; a
    bare float(val) would raise on those, so coerce defensively."""
    if val is None:
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _universe_cell(symbol: str, col: str):
    """Raw cell for (symbol, col) from the daily universe df, or None if the df is
    unloaded or the symbol/column is absent."""
    with _iti_df_lock:
        df = _iti_df
    if df is None or col not in df.columns or symbol not in df['Symbol'].values:
        return None
    return df.loc[df['Symbol'] == symbol, col].iloc[0]


def _lookup_float(symbol: str):
    """Pull Float_M for `symbol` from the daily universe TSV. None if absent / NaN /
    non-numeric (e.g. 'skipped_nan')."""
    return _coerce_float(_universe_cell(symbol, 'Float_M'))


def _lookup_mktcap(symbol: str):
    """Pull MarketCap_M (millions USD) for `symbol` from the daily universe TSV.
    None if absent / NaN / non-numeric (e.g. 'skipped_nan')."""
    return _coerce_float(_universe_cell(symbol, 'MarketCap_M'))


def _lookup_last_close(symbol: str):
    """LastDailyClosePrice for `symbol`, or None if absent/NaN/skipped_nan/<=0.
    x-wing treats None as 'no entry-price cap', so a missing close is non-fatal."""
    val = _coerce_float(_universe_cell(symbol, 'LastDailyClosePrice'))
    return val if (val is not None and val > 0) else None


def _load_latest_iti_tsv() -> None:
    """Load the most recent stocks_universe_YYYY-MM-DD.tsv into _iti_df."""
    global _iti_df
    pattern = os.path.join(UNIVERSE_DATA_DIR, 'stocks_universe_????-??-??.tsv')
    matches = sorted(glob.glob(pattern))
    if not matches:
        logger.warning(f"[ITI] No dated universe TSV found in {UNIVERSE_DATA_DIR} — ITI lookups will use default")
        return
    path = matches[-1]
    try:
        df = pd.read_csv(path, sep='\t')
        with _iti_df_lock:
            _iti_df = df
        logger.info(f"[ITI] Loaded {path} ({len(df)} symbols)")
    except Exception as exc:
        logger.error(f"[ITI] Failed to load {path}: {exc}", exc_info=True)


def _is_rth_now() -> bool:
    """True iff the current ET clock is within regular trading hours (09:30–16:00).
    Shared by the ITI and trade-size baseline lookups."""
    now_et = datetime.now(tz=ZoneInfo('America/New_York'))
    h, m = now_et.hour, now_et.minute
    return (h == 9 and m >= 30) or (10 <= h < 16)


def _lookup_baseline_iti(symbol: str) -> float:
    """RTH/ETH average inter-trade interval (s) for `symbol`, by ET clock. Falls
    back to DEFAULT_BASELINE_ITI when absent / NaN / skipped_nan / <=0."""
    col = 'RTH_avgITI_sec' if _is_rth_now() else 'ETH_avgITI_sec'
    val = _coerce_float(_universe_cell(symbol, col))
    if val is None or val <= 0:
        logger.warning(f"[Clerk] {symbol}: {col} missing/invalid — using default {DEFAULT_BASELINE_ITI}s")
        return DEFAULT_BASELINE_ITI
    return val


def _lookup_baseline_trade_size(symbol: str) -> float:
    """RTH/ETH average trade size for `symbol`, by ET clock. Returns the
    TRADE_SIZE_SENTINEL (44444) when absent / NaN / skipped_nan / <=0 — trade-mole
    treats the sentinel as 'no baseline'. NOTE the inconsistent casing in the
    universe TSV header: 'RTH_tradeSize' (lowercase t) vs 'ETH_TradeSize' (uppercase T)."""
    col = 'RTH_tradeSize' if _is_rth_now() else 'ETH_TradeSize'
    val = _coerce_float(_universe_cell(symbol, col))
    if val is None or val <= 0:
        return TRADE_SIZE_SENTINEL
    return val


def _iti_reload_worker(stop_event: threading.Event, cfg) -> None:
    """Background thread: once per day at/after 03:00 ET, reload the latest universe
    TSV into _iti_df, rebuild _inscope_set, and live-swap the connector's universe
    frozenset (ws_listener reads cfg.universe per alert; attribute assignment is
    atomic in CPython, so a lock-free swap is safe)."""
    global _last_iti_reload_date, _inscope_set
    ET_TZ = ZoneInfo('America/New_York')
    while not stop_event.is_set():
        now = datetime.now(tz=ET_TZ)
        if now.hour >= 3 and now.date() != _last_iti_reload_date:
            logger.info("[ITI] 03:00+ ET — reloading latest universe TSV")
            _load_latest_iti_tsv()

            with _iti_df_lock:
                df = _iti_df
            if df is not None and 'Symbol' in df.columns:
                symbols = set(df['Symbol'].astype(str).str.strip())
                symbols.discard('')
                _inscope_set = symbols
                latest_path = _resolve_latest_universe_tsv()
                try:
                    cfg.universe = rtpr.load_universe(Path(latest_path))
                    logger.info(
                        f"[Universe] Reloaded: {len(symbols)} symbols → connector "
                        f"universe swapped ({len(cfg.universe)} symbols)"
                    )
                except Exception as exc:
                    logger.warning(f"[Universe] connector universe swap failed: {exc}")
            _last_iti_reload_date = now.date()

        stop_event.wait(timeout=60.0)


# ─── Per-ticker collector — runs on collect_worker thread ────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_body, f_arm, arm_blocked: bool) -> None:
    """Resolve headline FinBERT, complete the clerk handshake (wait for the STEP-1a
    arm reply, then send the STEP-1b sentiment), then resolve the body future and
    write the TSV row with all 5 nocoref scores + NoCorefCompletedAt.

    Ordering is load-bearing: we wait on the arm future *before* sending sentiment
    so the clerk has registered the session — required both for the OK gate and for
    the BAD early-release to land. The (possibly slow) body pipeline is resolved last
    so it never delays the trade decision."""
    news_id = news_dict['ID']

    # 1) Headline FinBERT → sentiment decision
    try:
        finbert_val = f_finbert.result(timeout=60)
        finbert_completed_at = datetime.now()
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        finbert_completed_at = None

    is_ok = evaluate_sentiment(finbert_val)

    # 2) Build the headline portion of the row. Author / Created / Arrival* / CurlTime
    #    are pass-throughs from the RTPR_connector row (no nw.get_news_object lookup).
    completed_dict = {
        'Symbol':             symbol,
        'Tickers':            json.dumps(tickers),
        'ID':                 news_id,
        'Created':            news_dict.get('Created', ''),
        'ArrivalDate':        news_dict.get('ArrivalDate', ''),
        'ArrivalTime':        news_dict.get('ArrivalTime', ''),
        'CurlTime':           news_dict.get('CurlTime', ''),
        'Headline':           news_dict['Headline'],
        'Author':             news_dict.get('Author', ''),
        'Float':              _lookup_float(symbol),
        'MktCap':             _lookup_mktcap(symbol),
        'Exchange':           news_dict.get('Exchange', ''),
        'LastDailyClose':     _lookup_last_close(symbol),
        'itiBaseline':        _lookup_baseline_iti(symbol),
        'tradeSizeBaseline':  _lookup_baseline_trade_size(symbol),
        'FinBERTCompletedAt': finbert_completed_at,
        'positive':           finbert_val.get('positive'),
        'negative':           finbert_val.get('negative'),
        'neutral':            finbert_val.get('neutral'),
        'sentiment_score':    finbert_val.get('sentiment_score'),
        'label':              finbert_val.get('label'),
        'Sentiment':          'OK' if is_ok else 'BAD',
    }

    # 3) Clerk STEP-1b — resolve the arm, then confirm sentiment (or, if the headline
    #    was excluded but we had pre-armed on the alert, release).
    if f_arm is None:
        # Never armed: excluded headline with no pre-arm, or a partner ticker we
        # deliberately did not arm.
        completed_dict['ClerkArm']       = ('skipped:excluded_string' if arm_blocked
                                            else 'skipped:not_armed')
        completed_dict['ClerkSentiment'] = 'skipped:not_armed'
    else:
        try:
            arm_reply = f_arm.result(timeout=CLERK_ARM_WAIT_SEC)
        except Exception as exc:
            logger.warning(f"[Clerk] {symbol}: arm future failed/timed out: {exc}")
            arm_reply = {}
        arm_status = arm_reply.get('status')
        if arm_status == 'accepted':
            completed_dict['ClerkArm'] = f"accepted:{arm_reply.get('clientId')}"
        elif arm_status == 'rejected':
            completed_dict['ClerkArm'] = f"rejected:{arm_reply.get('reason')}"
        else:
            completed_dict['ClerkArm'] = arm_status or 'no_reply'

        if arm_status == 'rejected':
            # No session was created — sending sentiment is pointless.
            completed_dict['ClerkSentiment'] = 'skipped:arm_rejected'
        elif arm_blocked:
            # Pre-armed on the alert, but the headline matched an orchestrator
            # excluded string → release the warm client instead of confirming.
            rel = _send_sentiment(symbol, is_ok=False)
            completed_dict['ClerkSentiment'] = rel.get('status', 'no_reply')
            logger.info(f"[Clerk] {symbol}: pre-armed but excluded → released "
                        f"({completed_dict['ClerkSentiment']})")
        else:
            # accepted (session registered) or unknown (best-effort) → STEP 1b.
            sentiment_reply = _send_sentiment(symbol, is_ok)
            completed_dict['ClerkSentiment'] = sentiment_reply.get('status', 'no_reply')

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
    # NoCorefCompletedAt is stamped on any successful pipeline run, even when the
    # per-ticker block is empty (NER + allowed_tickers fallback both failed). None
    # means we didn't run a pipeline at all (empty body or error).
    completed_dict['NoCorefCompletedAt'] = datetime.now() if body_result else None

    # 5) Write the TSV row
    _append_to_tsv(completed_dict)

    logger.info(f"Completed news_dict: {completed_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol                       : {completed_dict['Symbol']}")
    print(f"  Tickers                      : {completed_dict['Tickers']}")
    print(f"  ID                           : {completed_dict['ID']}")
    print(f"  Created                      : {completed_dict['Created']}")
    print(f"  ArrivalDate                  : {completed_dict['ArrivalDate']}")
    print(f"  ArrivalTime                  : {completed_dict['ArrivalTime']}")
    print(f"  CurlTime                     : {completed_dict['CurlTime']}")
    print(f"  Headline                     : {completed_dict['Headline']}")
    print(f"  Author                       : {completed_dict['Author']}")
    print(f"  FinBERT label                : {completed_dict['label']}")
    print(f"  sentiment_score              : {completed_dict['sentiment_score']}")
    print(f"  positive                     : {completed_dict['positive']}")
    print(f"  negative                     : {completed_dict['negative']}")
    print(f"  neutral                      : {completed_dict['neutral']}")
    print(f"  Float                        : {completed_dict['Float']}")
    print(f"  Exchange                     : {completed_dict['Exchange']}")
    print(f"  LastDailyClose               : {completed_dict['LastDailyClose']}")
    print(f"  itiBaseline                  : {completed_dict['itiBaseline']}")
    print(f"  tradeSizeBaseline            : {completed_dict['tradeSizeBaseline']}")
    print(f"  FinBERTCompletedAt           : {completed_dict['FinBERTCompletedAt']}")
    print(f"  Sentiment                    : {completed_dict['Sentiment']}")
    print(f"  ClerkArm                     : {completed_dict['ClerkArm']}")
    print(f"  ClerkSentiment               : {completed_dict['ClerkSentiment']}")
    print(f"  nocoref_neutral_filter       : {completed_dict['nocoref_neutral_filter']}")
    print(f"  nocoref_confidence_weighted  : {completed_dict['nocoref_confidence_weighted']}")
    print(f"  nocoref_net_score            : {completed_dict['nocoref_net_score']}")
    print(f"  nocoref_top_k                : {completed_dict['nocoref_top_k']}")
    print(f"  nocoref_positional           : {completed_dict['nocoref_positional']}")
    print(f"  NoCorefCompletedAt           : {completed_dict['NoCorefCompletedAt']}")
    print(f"{'='*60}\n")


# ─── Row-built tap — invoked by RTPR_connector's _row_hook (loop thread) ──────

def _on_row_built(job: dict, row: dict) -> None:
    """RTPR_connector row-built tap. Mirrors Orchestrator4.0.on_news_accepted but
    consumes a connector row dict instead of an NW4 payload.

    The connector already filtered by universe membership + dedup. Here we apply the
    two kept NW4 input filters (ticker count ≤ 2, NW4-level excluded strings → drop),
    then fan out per in-universe ticker. STEP-1a arming already fired pre-curl on the
    alert (_on_alert stashed a future); we reuse it for the primary and arm any NEW
    in-universe partner tickers found in the scraped list.

    Runs on the connector loop thread (worker coroutine, post-curl) → stays lean:
    only JSON parse + string filters + non-blocking submits; all heavy work goes to
    the thread pools."""
    art_id  = row.get('ID') or job.get('id')
    primary = job.get('symbol')

    headline   = (row.get('Headline') or '').strip()
    raw_tickers = row.get('Tickers') or '[]'

    # Failed/empty curl (connector build_failed_row): no headline / no tickers. The
    # body never materialized → release any pre-armed primary, write no row.
    if not headline or raw_tickers == '[]':
        logger.info(f"[Row] id={art_id} sym={primary}: failed/empty curl row — releasing pre-arm")
        _on_alert_release(primary, art_id)
        return

    try:
        tickers = list(dict.fromkeys(
            t.strip() for t in json.loads(raw_tickers) if t and t.strip()))
    except Exception as exc:
        logger.warning(f"[Row] id={art_id}: could not parse Tickers={raw_tickers!r}: {exc}")
        _on_alert_release(primary, art_id)
        return

    if not tickers:
        logger.warning(f"[Row] id={art_id}: no tickers after parse — releasing pre-arm")
        _on_alert_release(primary, art_id)
        return

    # ── Kept NW4 input filter #1: ticker count ≤ 2 ──
    if len(tickers) > MAX_TICKERS_PER_ARTICLE:
        logger.info(f"[Row] id={art_id}: {len(tickers)} tickers > {MAX_TICKERS_PER_ARTICLE} — dropped")
        _on_alert_release(primary, art_id)
        return

    # ── Kept NW4 input filter #2: NW4-level excluded strings (drop article) ──
    hl_lower = headline.lower()
    nw_hit = next((s for s in _nw_excluded_strings if s.lower() in hl_lower), None)
    if nw_hit is not None:
        logger.info(f"[Row] id={art_id}: headline matched NW4 excluded string '{nw_hit}' — dropped")
        _on_alert_release(primary, art_id)
        return

    # Orchestrator-level excluded strings (clerk gate kept): block the arm but still
    # write the row.
    arm_blocked = any(s.lower() in hl_lower for s in _excluded_strings)

    # Exchange: connector stores a JSON array; 4.0's TSV used a single string.
    try:
        exch_list = json.loads(row.get('Exchange') or '[]')
        exchange = exch_list[0] if exch_list else ''
    except Exception:
        exchange = ''

    # ArrivalDate from the real arrival datetime (job carries it; row only has the
    # time string), matching 4.0's "date of arrival" semantic.
    arrival_dt = job.get('arrival_dt')
    try:
        arrival_date = arrival_dt.astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d')
    except Exception:
        arrival_date = (row.get('ArrivalDate') or '')

    news_dict = {
        'Symbol':       ','.join(tickers),
        'ID':           art_id,
        'Headline':     headline,
        'article_body': row.get('Body') or '',
        'Author':       row.get('Source') or '',
        'Created':      row.get('Created') or '',
        'ArrivalDate':  arrival_date,
        'ArrivalTime':  row.get('ArrivalTime') or '',
        'CurlTime':     row.get('CurlTime') or '',
        'Exchange':     exchange,
    }

    prearmed_set = {primary} if primary else set()

    logger.info(
        f"on_row_built: id={art_id} tickers={tickers} primary={primary} "
        f"arm_blocked={arm_blocked}"
    )

    # STEP 1a — ARM (per ticker). The primary was pre-armed on the alert (_on_alert
    # stashed a future) → reuse it. Partner tickers found only in the scraped list
    # are armed now, but ONLY if in the universe (_inscope_set) and the headline did
    # not match an orchestrator excluded string.
    f_arm = {}
    for symbol in tickers:
        if symbol in prearmed_set:
            with _arm_lock:
                f_arm[symbol] = _arm_results.pop((art_id, symbol), None)
        elif not arm_blocked and symbol in _inscope_set:
            f_arm[symbol] = _clerk_executor.submit(_arm_clerk, symbol)
        # else: arm_blocked, or a partner not in the universe → leave unset (skipped)

    # Release any ticker pre-armed on the alert that isn't among this article's final
    # tickers (alert primary differed from the scraped list).
    for symbol in prearmed_set - set(tickers):
        logger.info(f"[Clerk] {symbol} id={art_id}: pre-armed but not an article ticker — releasing")
        _on_alert_release(symbol, art_id)

    if arm_blocked:
        logger.info(f"[Clerk] id={art_id}: headline matched excluded string — "
                    f"not arming new tickers (pre-armed released via sentiment)")

    # FinBERT-headliner + body pipeline run once per article (shared futures).
    f_finbert = _finbert_executor.submit(analyze_finbert, news_dict)
    f_body    = _body_executor.submit(_run_body_pipeline, news_dict)

    # Fan out a TSV row only for in-universe tickers. Out-of-universe partners are
    # never armed and would leave a near-empty audit row — skip them (still recorded
    # in the surviving row's Tickers JSON column).
    row_tickers = [s for s in tickers if s in _inscope_set or s in prearmed_set]
    if not row_tickers:
        logger.warning(f"on_row_built: id={art_id} has no in-scope tickers among "
                       f"{tickers} — writing all rows as fallback")
        row_tickers = tickers
    else:
        skipped = [s for s in tickers if s not in row_tickers]
        if skipped:
            logger.info(f"[Fan-out] id={art_id}: skipping out-of-universe partner "
                        f"tickers {skipped} (no TSV row)")
    for symbol in row_tickers:
        _collect_executor.submit(
            _collect_and_log, news_dict, tickers, symbol,
            f_finbert, f_body, f_arm.get(symbol), arm_blocked,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _inscope_set, _nw_excluded_strings, _excluded_strings, _last_iti_reload_date
    logger.info("Orchestrator5.0 starting...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    logger.info("Pre-loading FinBERT-headliner model...")
    load_model()
    logger.info("FinBERT-headliner model ready.")

    # Pre-warm body pipeline workers: submit N no-op tasks and wait. Each worker
    # thread runs _init_body_worker on first task pickup, so by the time
    # _futures_wait returns, every worker has its FinBERTBodyPipeline fully loaded.
    logger.info(f"Pre-warming {BODY_FINBERT_WORKERS} FinBERT body pipeline worker(s)...")
    _futures_wait([_body_executor.submit(lambda: None)
                   for _ in range(BODY_FINBERT_WORKERS)])
    logger.info("All body pipeline workers ready.")

    # Resolve + load the universe (full set — float filter is dropped, so this is
    # plain universe membership). _iti_df also backs the Float/MktCap/close/ITI/
    # tradeSize lookups for the TSV + clerk arm.
    universe_tsv = _resolve_latest_universe_tsv()
    _load_latest_iti_tsv()
    with _iti_df_lock:
        df = _iti_df
    if df is not None and 'Symbol' in df.columns:
        _inscope_set = set(df['Symbol'].astype(str).str.strip())
        _inscope_set.discard('')
    logger.info(
        f"[Universe] In-strategy universe: {len(_inscope_set)} symbols "
        f"(universe membership; no float filter)"
    )

    _nw_excluded_strings = _load_excluded_strings(NW4_EXCLUDED_STRINGS_FILE)
    _excluded_strings    = _load_excluded_strings(ORCH_EXCLUDED_STRINGS_FILE)

    logger.info(f"[Universe] Using universe TSV: {universe_tsv}")

    # ── Build the RTPR_connector cfg (mirrors RTPR_connector.main()) ──────────
    cfg = rtpr.Cfg()
    cfg.universe = rtpr.load_universe(Path(universe_tsv))
    cfg.api_key  = rtpr.load_api_key(Path(RTPR_API_KEY_FILE))
    cfg.workers  = max(1, RTPR_WORKERS)
    cfg.out_dir  = Path(RTPR_OUT_DIR)
    cfg.log_dir  = Path(RTPR_LOG_DIR)
    cfg.queue_max = RTPR_QUEUE_MAX
    cfg.fetch_timeout = RTPR_FETCH_TIMEOUT
    cfg.max_retries = max(0, RTPR_MAX_RETRIES)
    cfg.writer_batch_size = 50
    cfg.writer_max_delay = 0.5
    hh, mm = RTPR_FLUSH_AT.split(":")
    cfg.flush_hour, cfg.flush_minute = int(hh), int(mm)

    os.makedirs(cfg.out_dir, exist_ok=True)
    rtpr.setup_logging(cfg.log_dir, logging.INFO)   # connector's own QueueListener logger

    # uvloop: drop-in faster event loop on Linux; degrade gracefully if unavailable.
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("uvloop enabled")
    except Exception as exc:
        logger.info(f"uvloop unavailable ({exc}) — using default asyncio loop")

    logger.info(f"[RTPR] {len(cfg.universe)} universe symbols, {cfg.workers} fetch workers")

    # Register the connector hooks BEFORE the loop runs (no missed-item race).
    #   _on_alert     fires pre-curl   → clerk arm + stash future
    #   _on_row_built fires post-curl  → filter + fan-out + sentiment + TSV
    rtpr.set_alert_hook(_on_alert)
    rtpr.set_row_hook(_on_row_built)

    # Daily universe reload thread (swaps cfg.universe + _iti_df + _inscope_set).
    _stop_event = threading.Event()
    _last_iti_reload_date = datetime.now(tz=ZoneInfo('America/New_York')).date()
    _iti_thread = threading.Thread(
        target=_iti_reload_worker, args=(_stop_event, cfg),
        daemon=True, name='iti-reload',
    )
    _iti_thread.start()

    logger.info("Starting RTPR_connector loop. Waiting for news... (Ctrl+C to stop)")

    # main_async installs its own SIGINT/SIGTERM handlers and does a graceful drain
    # + final CSV flush on signal, then returns.
    try:
        asyncio.run(rtpr.main_async(cfg))
    finally:
        logger.info("Shutting down executors...")
        _stop_event.set()
        _clerk_executor.shutdown(wait=True)
        _finbert_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        _body_executor.shutdown(wait=True)
        logger.info("Orchestrator5.0 stopped.")


if __name__ == '__main__':
    main()
