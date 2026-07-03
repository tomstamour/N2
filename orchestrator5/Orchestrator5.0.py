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

# ── RTPR_connector3 import (the news source) ──────────────────────────────────
# Orchestrator5.0 drives RTPR_connector3's asyncio loop IN-PROCESS and taps each
# firehose article via the connector's latency-neutral set_row_hook (default None
# so a standalone connector run is unaffected). The firehose pushes the FULL
# article inline — no curl phase — so 4.2's alert/pre-arm/release split collapses
# into a single reception hook. Executing the module only defines its
# functions/Config/helpers — main() is __main__-guarded.
_c3_spec = _ilu.spec_from_file_location(
    "RTPR_connector3",
    str(SCRIPTS_DIR / "RTPR_connector3" / "RTPR_connector3.py"),
)
c3 = _ilu.module_from_spec(_c3_spec)
# Must be registered BEFORE exec: the connector's frozen dataclass resolves its
# string annotations via sys.modules[cls.__module__] (PEP 563 + dataclasses).
sys.modules["RTPR_connector3"] = c3
_c3_spec.loader.exec_module(c3)
# ─────────────────────────────────────────────────────────────────────────────

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = str(SCRIPTS_DIR / "orchestrator5" / "tables")
LOG_DIR    = SCRIPTS_DIR / "orchestrator5" / "logs"
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
#   STEP 1a (ARM)       on PR reception → {ticker, lastDailyClose, itiBaseline, tradeSizeBaseline}
#   STEP 1b (SENTIMENT) after FinBERT   → {ticker, Sentiment: "OK"|"BAD"}
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

# ── RTPR_connector3 (news source) knobs ───────────────────────────────────────
# The connector config is built via its own parse_args (see main()) so all its
# CLI defaults apply unchanged (writer batch 200 / idle flush 0.5s, 20:35 daily
# flush, 03:50 universe reload + WS re-subscribe, firehose URL, universe-list
# subscribe, dedup on); only the CWD-relative paths are overridden here. The
# connector's own logs/tables keep living in RTPR_connector3/ — do NOT also run
# the standalone connector, or two firehose connections write the same files.
C3_API_KEY_FILE = str(SCRIPTS_DIR / 'RTPR_connector3' / 'RTPR_API-Key.txt')
C3_LOG_DIR      = str(SCRIPTS_DIR / 'RTPR_connector3' / 'logs')
C3_TABLE_DIR    = str(SCRIPTS_DIR / 'RTPR_connector3' / 'tables')
# ─────────────────────────────────────────────────────────────────────────────

# ── Worker pools (parallelism knobs) ──────────────────────────────────────────
# Headline FinBERT is the trade-critical path: a burst of ~15 PRs inside 1s
# (observed 2026-07-02) clears in ~4 waves on 4 threads. Body pipelines are heavy
# (spaCy + ONNX per worker) and intentionally trail — they never delay the clerk.
FINBERT_HEADLINE_WORKERS  = 4     # threads sharing the preloaded headline model
BODY_FINBERT_WORKERS      = 2     # one FinBERTBodyPipeline instance per worker thread
COLLECT_WORKERS           = 8     # per-ticker collectors (resolve futures + TSV)
CLERK_WORKERS             = 6     # STEP-1a arms; a touch above clerk's 5-client pool
BODY_FINBERT_TIMEOUT_SEC  = 180   # per-article ceiling; clerk already armed/sentiment-sent by now
NOCOREF_NEUTRAL_THRESHOLD = 0.85  # method 1 (neutral_filter)
NOCOREF_TOP_K             = 3     # method 4 (top_k)
NOCOREF_POSITIONAL_DECAY  = 0.1   # method 5 (positional)
NOCOREF_SENTENCES_TO_ANALYSE = 20  # cap spaCy sentences scored per article (None = all)
# ─────────────────────────────────────────────────────────────────────────────

DEDUP_MAX_IDS = 50_000  # FIFO cap on the per-day seen-article-ID stash (leak guard)

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('Orchestrator5.0')


class _OrchDailyFileHandler(c3.DailyFixedTZFileHandler):
    """Daily Orchestrator5.0_YYYY_MM_DD.log in ./logs (fixed UTC-4 date key) —
    only the filename differs from the connector's handler. Attached to the ROOT
    logger so every orchestrator record lands in the file; connector3 records do
    NOT propagate (its logger is queue-fed with propagate=False) and are already
    persisted in RTPR_connector3/logs."""

    def path_for_date(self, date_key: str) -> Path:
        return self.log_dir / f"Orchestrator5.0_{date_key}.log"


_file_handler = _OrchDailyFileHandler(LOG_DIR)
_file_handler.setFormatter(c3.FixedTZFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
logging.getLogger().addHandler(_file_handler)

_iti_df: pd.DataFrame = None
_last_iti_reload_date = None
_iti_df_lock = threading.Lock()

# Full in-universe symbol set (Symbol column of the latest universe TSV) — plain
# universe membership, used to gate clerk arming and TSV fan-out. Rebound (not
# mutated) on reload so lock-free reads always see a consistent set object.
_inscope_set: set = set()

# Headline filters. _nw_excluded_strings DROPS the article (NW4 input filter kept);
# _excluded_strings (orchestrator-level) only blocks the clerk arm (clerk gate kept).
_nw_excluded_strings: set = set()
_excluded_strings: set = set()

# Orchestrator-side article-ID dedup. The connector's own dedup lives in its
# dataframe_writer DOWNSTREAM of the row hook, and a WS reconnect can replay
# recent articles straight through the listener — so the hook dedups here.
# Touched ONLY from _on_row_built (the single WS loop thread) → lock-free. The
# insertion-ordered dict doubles as a FIFO for the size cap; cleared when the
# arrival day changes.
_seen_ids: dict = {}
_seen_day = None


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

_finbert_executor = ThreadPoolExecutor(max_workers=FINBERT_HEADLINE_WORKERS,
                                       thread_name_prefix='finbert')
_collect_executor = ThreadPoolExecutor(max_workers=COLLECT_WORKERS,
                                       thread_name_prefix='collect')

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
# tasks.
_clerk_executor = ThreadPoolExecutor(max_workers=CLERK_WORKERS, thread_name_prefix='clerk')


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


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = [
    'Symbol', 'Tickers', 'ID', 'ArrivalDate', 'Created', 'ArrivalTime', 'OrchTime',
    'Headline', 'Body', 'Author',
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
    """Appends one row (one ticker) to the daily TSV output file. Thread-safe.
    The file date follows the connector's fixed UTC-4 clock like every timestamp."""
    date_str  = c3.format_millis_date(c3.now_fixed())
    file_path = os.path.join(OUTPUT_DIR, f"news_output_{date_str}.tsv")
    try:
        with _tsv_lock:
            file_exists = os.path.isfile(file_path)
            if file_exists:
                with open(file_path, 'r', encoding='utf-8') as f:
                    existing_header = f.readline().rstrip('\n').rstrip('\r').split('\t')
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


def _parse_exchange(value):
    """Firehose `exchange` payload value → exchange string. The value SHAPE is
    unverified (only the key name was confirmed live), so accept a plain string
    or a list and return the first non-empty entry, else ''."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for item in value:
            s = str(item or '').strip()
            if s:
                return s
    return ''


def _lookup_exchange(symbol: str) -> str:
    """Exchange for `symbol` from the daily universe TSV, or '' if absent/NaN —
    the per-ticker fallback for articles whose payload carried no exchange."""
    val = _universe_cell(symbol, 'Exchange')
    if val is None:
        return ''
    try:
        if pd.isna(val):
            return ''
    except (TypeError, ValueError):
        pass
    return str(val).strip()


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


def _iti_reload_worker(stop_event: threading.Event) -> None:
    """Background thread: once per day at/after 03:00 ET, reload the latest universe
    TSV into _iti_df and rebuild _inscope_set (arming + fan-out gate). The WS
    subscribe list is NOT touched here — RTPR_connector3 reloads and re-subscribes
    on its own daily schedule (03:50 UTC-4)."""
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
                logger.info(f"[Universe] Reloaded: {len(symbols)} symbols in scope")
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
        finbert_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        finbert_completed_at = None

    is_ok = evaluate_sentiment(finbert_val)

    # 2) Build the headline portion of the row. Created/Arrival*/OrchTime/Author/
    #    Exchange are pass-throughs from the firehose reception (all stamped on the
    #    connector's fixed UTC-4 clock). The Body CELL is flattened to one line here
    #    so the TSV stays one-row-per-line; the body pipeline consumed the pristine
    #    multiline text via news_dict['article_body'].
    completed_dict = {
        'Symbol':             symbol,
        'Tickers':            json.dumps(tickers),
        'ID':                 news_id,
        'ArrivalDate':        news_dict.get('ArrivalDate', ''),
        'Created':            news_dict.get('Created', ''),
        'ArrivalTime':        news_dict.get('ArrivalTime', ''),
        'OrchTime':           news_dict.get('OrchTime', ''),
        'Headline':           news_dict['Headline'],
        'Body':               c3.clean_inline_text(news_dict.get('article_body') or ''),
        'Author':             news_dict.get('Author', ''),
        'Float':              _lookup_float(symbol),
        'MktCap':             _lookup_mktcap(symbol),
        'Exchange':           news_dict.get('Exchange') or _lookup_exchange(symbol),
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

    # 3) Clerk STEP-1b — resolve the arm, then confirm sentiment. f_arm is None when
    #    the ticker was never armed (excluded headline, or not in the universe); the
    #    4.2 "pre-armed but excluded → release" branch is gone by construction — we
    #    never arm a blocked article in the first place.
    if f_arm is None:
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
    # means we didn't run a pipeline at all (empty body or error). Time-only on the
    # same UTC-4 clock as every other stamp in the row.
    completed_dict['NoCorefCompletedAt'] = (c3.format_millis_time(c3.now_fixed())
                                            if body_result else None)

    # 5) Write the TSV row
    _append_to_tsv(completed_dict)

    # Full body lives in the TSV only — log/print a preview.
    log_dict = dict(completed_dict)
    body_cell = log_dict.get('Body') or ''
    if len(body_cell) > 120:
        log_dict['Body'] = body_cell[:120] + '…'
    logger.info(f"Completed news_dict: {log_dict}")
    print(f"\n{'='*60}")
    print(f"NEWS ITEM PROCESSED")
    print(f"  Symbol                       : {completed_dict['Symbol']}")
    print(f"  Tickers                      : {completed_dict['Tickers']}")
    print(f"  ID                           : {completed_dict['ID']}")
    print(f"  Created                      : {completed_dict['Created']}")
    print(f"  ArrivalDate                  : {completed_dict['ArrivalDate']}")
    print(f"  ArrivalTime                  : {completed_dict['ArrivalTime']}")
    print(f"  OrchTime                     : {completed_dict['OrchTime']}")
    print(f"  Headline                     : {completed_dict['Headline']}")
    print(f"  Body (preview)               : {body_cell[:100]}")
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


# ─── Firehose reception tap — invoked by RTPR_connector3's row hook ───────────

def _on_row_built(data: dict, row: dict, arrival_dt) -> None:
    """RTPR_connector3 row hook. Fires on the WS loop thread the instant an
    in-universe firehose article arrives — the full article comes inline, so 4.2's
    alert/pre-arm/release split collapses into this single reception event: dedup,
    the two kept NW4 input filters (ticker count ≤ 2, NW4-level excluded strings →
    drop), then arm every in-universe ticker and fan out per ticker.

    Runs on the connector loop thread → stays lean: only dict/string filters and
    non-blocking executor submits; all heavy work goes to the thread pools. `row`
    and `data` are shared with the connector (row is later batched into its own
    PR_dataframe) and must never be mutated here."""
    global _seen_day
    # OrchTime = the moment this article lands in the orchestrator — stamped first,
    # on the connector's fixed UTC-4 clock like every other time column.
    orch_time = c3.format_millis_time(c3.now_fixed())

    art_id = (row.get('ID') or '').strip()

    # Dedup. The connector's own dedup sits in its dataframe_writer AFTER this hook,
    # and a WS reconnect can replay recent articles — so dedup here, keyed per
    # arrival day (arrival_dt is UTC-4-aware). Loop-thread-only → no lock.
    day = arrival_dt.date()
    if day != _seen_day:
        _seen_ids.clear()
        _seen_day = day
    if art_id:
        if art_id in _seen_ids:
            logger.info(f"[Row] id={art_id}: duplicate article ID — skipped")
            return
        _seen_ids[art_id] = None
        while len(_seen_ids) > DEDUP_MAX_IDS:
            _seen_ids.pop(next(iter(_seen_ids)))
    else:
        logger.warning("[Row] article with empty ID — cannot dedup, processing anyway")

    headline = (row.get('Headline') or '').strip()
    if not headline:
        logger.info(f"[Row] id={art_id}: empty headline — dropped")
        return

    try:
        tickers = list(dict.fromkeys(
            t.strip() for t in json.loads(row.get('Tickers') or '[]') if t and t.strip()))
    except Exception as exc:
        logger.warning(f"[Row] id={art_id}: could not parse Tickers={row.get('Tickers')!r}: {exc}")
        return

    if not tickers:
        logger.info(f"[Row] id={art_id}: no tickers — dropped")
        return

    # ── Kept NW4 input filter #1: ticker count ≤ 2 ──
    if len(tickers) > MAX_TICKERS_PER_ARTICLE:
        logger.info(f"[Row] id={art_id}: {len(tickers)} tickers > {MAX_TICKERS_PER_ARTICLE} — dropped")
        return

    # ── Kept NW4 input filter #2: NW4-level excluded strings (drop article) ──
    hl_lower = headline.lower()
    nw_hit = next((s for s in _nw_excluded_strings if s.lower() in hl_lower), None)
    if nw_hit is not None:
        logger.info(f"[Row] id={art_id}: headline matched NW4 excluded string '{nw_hit}' — dropped")
        return

    # Orchestrator-level excluded strings (clerk gate kept): block the arm but still
    # write the row.
    arm_blocked = any(s.lower() in hl_lower for s in _excluded_strings)

    news_dict = {
        'Symbol':       ','.join(tickers),
        'ID':           art_id,
        'Headline':     headline,
        'article_body': row.get('Body') or '',   # pristine (newlines intact) for the body pipeline
        'Author':       str(data.get('author') or ''),
        'Created':      row.get('Created') or '',
        # Date of ARRIVAL (4.2 semantic) — the connector row's ArrivalDate is the
        # published date, which differs on replayed/late articles.
        'ArrivalDate':  c3.format_millis_date(arrival_dt),
        'ArrivalTime':  row.get('ArrivalTime') or '',
        'OrchTime':     orch_time,
        'Exchange':     _parse_exchange(data.get('exchange')),
    }

    logger.info(
        f"on_row_built: id={art_id} tickers={tickers} arm_blocked={arm_blocked} "
        f"arrival={news_dict['ArrivalTime']} orch={orch_time}"
    )

    # STEP 1a — ARM every in-universe ticker now. Reception IS the earliest possible
    # moment (no pre-curl alert phase exists anymore).
    f_arm = {}
    if arm_blocked:
        logger.info(f"[Clerk] id={art_id}: headline matched excluded string — not arming")
    else:
        for symbol in tickers:
            if symbol in _inscope_set:
                f_arm[symbol] = _clerk_executor.submit(_arm_clerk, symbol)

    # FinBERT-headliner + body pipeline run once per article (shared futures).
    f_finbert = _finbert_executor.submit(analyze_finbert, news_dict)
    f_body    = _body_executor.submit(_run_body_pipeline, news_dict)

    # Fan out a TSV row only for in-universe tickers. Out-of-universe partners are
    # never armed and would leave a near-empty audit row — skip them (still recorded
    # in the surviving row's Tickers JSON column).
    row_tickers = [s for s in tickers if s in _inscope_set]
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

    # Load the universe (plain membership — no float filter). _iti_df also backs the
    # Float/MktCap/Exchange/close/ITI/tradeSize lookups for the TSV + clerk arm. The
    # connector loads its own copy for the WS subscribe list.
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

    # ── Build the RTPR_connector3 config via its own CLI parser ───────────────
    # Reuses the connector's defaults + validation; only the CWD-relative paths
    # are overridden. --flush-on-shutdown preserves the graceful final-flush.
    config = c3.parse_args([
        "--api-key-file", C3_API_KEY_FILE,
        "--universe-dir", UNIVERSE_DATA_DIR,
        "--log-dir",      C3_LOG_DIR,
        "--table-dir",    C3_TABLE_DIR,
        "--flush-on-shutdown",
    ])

    # uvloop: drop-in faster event loop on Linux; degrade gracefully if unavailable.
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        logger.info("uvloop enabled")
    except Exception as exc:
        logger.info(f"uvloop unavailable ({exc}) — using default asyncio loop")

    # Register the reception hook BEFORE the loop runs (no missed-article race).
    c3.set_row_hook(_on_row_built)

    # Daily universe reload thread (swaps _iti_df + _inscope_set; the connector
    # reloads its own WS subscribe list at 03:50).
    _stop_event = threading.Event()
    _last_iti_reload_date = datetime.now(tz=ZoneInfo('America/New_York')).date()
    _iti_thread = threading.Thread(
        target=_iti_reload_worker, args=(_stop_event,),
        daemon=True, name='iti-reload',
    )
    _iti_thread.start()

    logger.info("Starting RTPR_connector3 loop. Waiting for news... (Ctrl+C to stop)")

    # run_connector installs its own SIGINT/SIGTERM handlers, sets up the
    # connector's queue-fed logging internally (do NOT call c3.setup_logging here —
    # it would leak a second QueueListener), and on signal drains the writer +
    # final-flushes, then returns.
    try:
        asyncio.run(c3.run_connector(config))
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
