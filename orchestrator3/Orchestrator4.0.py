import sys
import os
import csv
import json
import signal
import logging
import threading
import socket
import importlib.util as _ilu
import shutil
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, wait as _futures_wait
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import glob
from pathlib import Path

# ── Repo-relative anchor + centralized per-user config ────────────────────────
# Every sibling script lives one level under scripts/, so this resolves the
# repo's scripts/ dir from THIS file — no hard-coded /home/... paths, so a fresh
# clone runs as-is. Per-user credentials/connection live in the single file
# config/n2_config_file.txt (RTPR key, clerk host/port).
SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_cfg_spec = _ilu.spec_from_file_location("n2_config", SCRIPTS_DIR / "config" / "n2_config.py")
n2_config = _ilu.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(n2_config)
_CFG = n2_config.load_config()
N2_CONFIG_FILE = SCRIPTS_DIR / "config" / "n2_config_file.txt"

sys.path.insert(0, str(SCRIPTS_DIR / 'newswatcher3'))
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
# Both modules import cleanly once their directory is on sys.path (above).
from FinBERT_body_noCoref import FinBERTBodyPipeline
from finBERT_neutral_management_addON import aggregate as _nocoref_aggregate
# ─────────────────────────────────────────────────────────────────────────────

# ── Daily TSV output ──────────────────────────────────────────────────────────
OUTPUT_DIR = str(SCRIPTS_DIR / "orchestrator3" / "tables")
# ─────────────────────────────────────────────────────────────────────────────

# ── NewsWatcher4.1 import (dot in filename prevents normal import) ────────────
_nw_spec = _ilu.spec_from_file_location(
    "NewsWatcher4_1",
    str(SCRIPTS_DIR / "newswatcher3" / "NewsWatcher4.2.py"),
)
nw = _ilu.module_from_spec(_nw_spec)
_nw_spec.loader.exec_module(nw)
# ─────────────────────────────────────────────────────────────────────────────

# ── NewsWatcher4 inputs (RTPR alerts WS + permalink curl) ─────────────────────
#
# PREREQUISITE: a filter rule must already exist on https://rtpr.io/wire.
#   Recommended catch-all:  tickers_length gte 1
# Without it, the alerts WS connects but emits no `alert` messages.
#
NW4_UNIVERSE_TSV          = None  # resolved at startup via _resolve_latest_universe_tsv()
NW4_PRICED_TSV            = None  # resolved at startup via _resolve_latest_universe_tsv()
NW4_BLACK_LIST            = str(SCRIPTS_DIR / 'orchestrator3' / 'black_list.csv')
NW4_API_KEYS              = str(N2_CONFIG_FILE)   # RTPR key now lives in the central config
NW4_LOG_DIR               = str(SCRIPTS_DIR / 'orchestrator3' / 'logs')
NW4_OUTPUT_DIR            = str(SCRIPTS_DIR / 'orchestrator3' / 'outputs')
NW4_NEWS_DF_DIR           = str(SCRIPTS_DIR / 'orchestrator3' / 'outputs')
NW4_BLOCKED_DIR           = str(SCRIPTS_DIR / 'orchestrator3' / 'outputs' / 'blocked_PRs')
NW4_ACCEPTED_DIR          = str(SCRIPTS_DIR / 'orchestrator3' / 'outputs' / 'accepted_PRs')
NW4_EXCLUDED_STRINGS_FILE = str(SCRIPTS_DIR / 'newswatcher3' / 'excluded_strings.txt')
ORCH_EXCLUDED_STRINGS_FILE  = str(SCRIPTS_DIR / 'orchestrator3' / 'excluded_strings-2.txt')
NW4_BLACKLIST_EXPIRY_HOURS = 0
NW4_REJECT_FLOAT_GT       = 50        # M shares; matches old universe filter
NW4_REJECT_PRICE_GT       = 10.00
NW4_FLUSH_INTERVAL_SEC    = 3600
# ─────────────────────────────────────────────────────────────────────────────

# ── Clerk connection (x-wing-mole/clerk-1.1.py) ───────────────────────────────
# The clerk is a warm pool of pre-connected ibapi clients running trade-mole +
# x-wing duos. We drive it over TCP/JSON with a two-step handshake:
#   STEP 1a (ARM)       on PR arrival → {ticker, lastDailyClose, itiBaseline, tradeSizeBaseline}
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
UNIVERSE_DATA_DIR    = str(SCRIPTS_DIR / 'universe_finder' / 'data')
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
logger = logging.getLogger('Orchestrator4.0')

_iti_df: pd.DataFrame = None
_last_iti_reload_date = None
_iti_df_lock = threading.Lock()

# In-strategy universe (symbols whose Float_M is <= NW4_REJECT_FLOAT_GT or blank),
# built by _split_universe_files() at startup + daily reload. Used to gate arming
# of partner tickers in on_news_accepted so a >50M partner riding on an
# in-strategy primary's article is never armed. Rebound (not mutated) on reload,
# so lock-free reads always see a consistent set object.
_inscope_set: set[str] = set()

_excluded_strings: set[str] = set()


def _resolve_latest_universe_tsv() -> str:
    """Return the most recent nasdaq_symbols_data_priced_YYYY-MM-DD.tsv in
    UNIVERSE_DATA_DIR, falling back to the non-dated file if none is found."""
    pattern = os.path.join(UNIVERSE_DATA_DIR, 'stocks_universe_????-??-??.tsv')
    matches = sorted(glob.glob(pattern))
    if matches:
        return matches[-1]
    fallback = os.path.join(UNIVERSE_DATA_DIR, 'stocks_universe.tsv')
    logger.warning(f"[Universe] No dated universe TSV found — falling back to {fallback}")
    return fallback


def _split_universe_files() -> tuple[str, set]:
    """Archive the full universe as a sibling '..._nonFiltered.tsv' and (re)write
    the canonical 'stocks_universe_YYYY-MM-DD.tsv' as the in-strategy subset:
    drop rows whose Float_M is KNOWN and > NW4_REJECT_FLOAT_GT. A blank/NaN
    Float_M is KEPT — a missing float is a data gap, not a known-large float, and
    is mostly small/obscure names (price is intentionally NOT a universe filter,
    so missing-LastDailyClose rows are kept too).

    NW4 and the rest of the orchestrator only ever read the canonical file, so
    after this runs NW4's universe-membership pre-filter drops out-of-strategy
    primaries BEFORE the permalink curl. The full universe stays inspectable in
    the '_nonFiltered' archive but is never read by NW4/Orchestrator.

    Idempotent: the '_nonFiltered' archive is the raw source of truth. The
    canonical is copied to it only if it does not yet exist; we then always
    re-derive the filtered canonical FROM the archive — so a restart (canonical
    already filtered) reproduces the same result instead of clobbering the raw
    archive with already-filtered data.

    Returns (canonical_path, in_strategy_symbol_set). Never raises: on any error
    it leaves the canonical untouched and returns its full symbol set so the
    pipeline still starts (just without the curl-reduction benefit)."""
    canonical = _resolve_latest_universe_tsv()
    root, ext = os.path.splitext(canonical)
    nonfiltered = f"{root}_nonFiltered{ext}"
    try:
        if not os.path.exists(nonfiltered):
            shutil.copy2(canonical, nonfiltered)
            logger.info(f"[Universe] Archived full universe → {os.path.basename(nonfiltered)}")

        # Read the raw archive verbatim (dtype=str + keep_default_na=False keeps
        # 'skipped_nan'/blank cells and exact decimals so the rewritten canonical
        # preserves every other cell byte-for-byte).
        raw = pd.read_csv(nonfiltered, sep='\t', dtype=str, keep_default_na=False)
        if 'Float_M' not in raw.columns or 'Symbol' not in raw.columns:
            raise ValueError("universe TSV missing Symbol/Float_M column")

        float_num = pd.to_numeric(raw['Float_M'], errors='coerce')
        keep = ~(float_num > NW4_REJECT_FLOAT_GT)   # blank/NaN/<=threshold kept
        filtered = raw[keep]
        filtered.to_csv(canonical, sep='\t', index=False)

        symbols = set(filtered['Symbol'].astype(str).str.strip())
        symbols.discard('')
        logger.info(
            f"[Universe] Filtered universe: {len(filtered)}/{len(raw)} symbols kept "
            f"(dropped {len(raw) - len(filtered)} with Float_M > {NW4_REJECT_FLOAT_GT}M) "
            f"→ {os.path.basename(canonical)}"
        )
        return canonical, symbols
    except Exception as exc:
        logger.warning(
            f"[Universe] _split_universe_files failed ({exc}) — using canonical "
            f"{os.path.basename(canonical)} unfiltered"
        )
        try:
            syms = set(pd.read_csv(canonical, sep='\t', usecols=['Symbol'],
                                   dtype=str, keep_default_na=False)['Symbol'].str.strip())
            syms.discard('')
        except Exception:
            syms = set()
        return canonical, syms


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

# Dedicated pool for clerk STEP-1a arms so arming is never starved by the
# collect tasks. Sized a touch above the clerk's default 5-client pool.
_clerk_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix='clerk')

# Arm futures created on the NW4 alert (pre-fetch, via _on_alert) and resolved
# later either by on_news_accepted (article accepted) or _release_armed (article
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
    """STEP 1a — ARM. Build the per-ticker trigger from the daily universe TSV
    and send it so the clerk binds a warm client and starts reqMktData ASAP.
    Runs on _clerk_executor (off the NW4 callback thread). Returns the reply."""
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


# ─── NW4 alert-flow callbacks (pre-fetch arm / release) ──────────────────────

def _on_alert(ticker: str, art_id: str, recv_ts=None) -> None:
    """NW4 alert callback — fires the instant the alert's primary ticker passes
    NW4's cheap pre-filter, BEFORE the body is curled. Arm the clerk now (so
    reqMktData starts ASAP, not after the fetch) and stash the future keyed by
    (art_id, ticker); on_news_accepted reuses it, or _release_armed frees it if
    the article is later dropped.

    Runs on NW4's asyncio loop thread → must not block, so the arm itself runs
    on _clerk_executor."""
    fut = _clerk_executor.submit(_arm_clerk, ticker)
    with _arm_lock:
        _arm_results[(art_id, ticker)] = fut
        # Safety net: entries are normally popped within ~seconds by
        # on_news_accepted or _release_armed. Cap the stash (dropping oldest, in
        # insertion order) so an unexpected exception on an NW4 task can't leak
        # the dict unbounded. 512 >> any plausible in-flight arm count.
        while len(_arm_results) > 512:
            _arm_results.pop(next(iter(_arm_results)))
    when = recv_ts.strftime('%H:%M:%S.%f')[:-3] if hasattr(recv_ts, 'strftime') else recv_ts
    logger.info(f"[Clerk] ALERT-ARM {ticker} id={art_id} recv={when}")


def _on_alert_release(ticker: str, art_id: str) -> None:
    """NW4 alert-release callback — a pre-armed article was dropped before
    acceptance. Non-blocking on the loop thread; defers to _clerk_executor."""
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
    """float(val) or None for blanks / NaN / non-numeric strings. The universe
    TSV uses the literal string 'skipped_nan' (and blank cells) for ~13% of
    rows; a bare float(val) would raise on those, so coerce defensively."""
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
    """Raw cell for (symbol, col) from the daily universe df, or None if the df
    is unloaded or the symbol/column is absent."""
    with _iti_df_lock:
        df = _iti_df
    if df is None or col not in df.columns or symbol not in df['Symbol'].values:
        return None
    return df.loc[df['Symbol'] == symbol, col].iloc[0]


def _lookup_float(symbol: str):
    """Pull Float_M for `symbol` from the daily universe TSV. None if absent /
    NaN / non-numeric (e.g. 'skipped_nan')."""
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


def _lookup_author(news_id: str):
    """Pull `author` from NW4's in-memory accepted-objects store. Returns None
    if the article has already been pruned (post-flush) or is missing."""
    obj = nw.get_news_object(f"id-{news_id}")
    if obj is None:
        return None
    return obj.get('author')


def _lookup_created(news_id: str):
    """Pull the RTPR `created` timestamp from NW4's accepted-objects store and
    return it as HH:MM:SS.mmm adjusted to UTC-4 (matching ArrivalTime's offset).
    The raw field is ISO-8601 UTC with milliseconds (e.g. '2026-04-29T00:30:00.073Z').
    Returns '' if missing or unparseable."""
    obj = nw.get_news_object(f"id-{news_id}")
    if obj is None:
        return ''
    raw = (obj.get('created') or '').strip()
    if not raw:
        return ''
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        dt = dt.astimezone(timezone.utc) - timedelta(hours=4)
        return dt.strftime('%H:%M:%S') + f".{dt.microsecond // 1000:03d}"
    except Exception as exc:
        logger.warning(f"_lookup_created: could not parse created={raw!r}: {exc}")
        return ''


def _load_latest_iti_tsv() -> None:
    """Load the most recent nasdaq_symbols_data_priced_YYYY-MM-DD.tsv into _iti_df."""
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
    """RTH/ETH average inter-trade interval (s) for `symbol`, by ET clock.
    Falls back to DEFAULT_BASELINE_ITI when absent / NaN / skipped_nan / <=0."""
    col = 'RTH_avgITI_sec' if _is_rth_now() else 'ETH_avgITI_sec'
    val = _coerce_float(_universe_cell(symbol, col))
    if val is None or val <= 0:
        logger.warning(f"[Clerk] {symbol}: {col} missing/invalid — using default {DEFAULT_BASELINE_ITI}s")
        return DEFAULT_BASELINE_ITI
    return val


def _lookup_baseline_trade_size(symbol: str) -> float:
    """RTH/ETH average trade size for `symbol`, by ET clock. Returns the
    TRADE_SIZE_SENTINEL (44444) when absent / NaN / skipped_nan / <=0 —
    trade-mole treats the sentinel as 'no baseline' and emits its M9 columns as
    None. NOTE the inconsistent casing in the universe TSV header:
    'RTH_tradeSize' (lowercase t) vs 'ETH_TradeSize' (uppercase T)."""
    col = 'RTH_tradeSize' if _is_rth_now() else 'ETH_TradeSize'
    val = _coerce_float(_universe_cell(symbol, col))
    if val is None or val <= 0:
        return TRADE_SIZE_SENTINEL
    return val


def _iti_reload_worker(stop_event: threading.Event) -> None:
    """Background thread: reloads the latest universe TSV once per day at/after 03:00 ET."""
    global _last_iti_reload_date, _inscope_set
    ET_TZ = ZoneInfo('America/New_York')
    while not stop_event.is_set():
        now = datetime.now(tz=ET_TZ)
        if now.hour >= 3 and now.date() != _last_iti_reload_date:
            logger.info("[ITI] 03:00+ ET — reloading latest universe TSV")
            # Re-split the new day's file (archive raw → _nonFiltered, write the
            # float-filtered canonical) BEFORE loading it, so _iti_df and NW4 both
            # see the in-strategy subset.
            latest_path, _inscope_set = _split_universe_files()
            _load_latest_iti_tsv()
            _last_iti_reload_date = now.date()

            with _iti_df_lock:
                df = _iti_df
            if df is not None and 'Symbol' in df.columns:
                symbols = df['Symbol'].astype(str).str.strip().tolist()
                nw.update_universe(symbols)
                logger.info(f"[Universe] NW4 universe reloaded: {len(symbols)} in-strategy symbols")
            nw.update_priced_tsv(latest_path)
            logger.info(f"[Universe] NW4 priced data reloaded from {latest_path}")

        stop_event.wait(timeout=60.0)


# ─── Per-ticker collector — runs on collect_worker thread ────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_body, f_arm, arm_blocked: bool) -> None:
    """Resolve headline FinBERT, complete the clerk handshake (wait for the
    STEP-1a arm reply, then send the STEP-1b sentiment), then resolve the body
    future and write the TSV row with all 5 nocoref scores + NoCorefCompletedAt.

    Ordering is load-bearing: we wait on the arm future *before* sending
    sentiment so the clerk has registered the session — required both for the OK
    gate and for the BAD early-release to land. The (possibly slow) body
    pipeline is resolved last so it never delays the trade decision."""
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

    # 2) Build the headline portion of the row (records what we sent the clerk)
    completed_dict = {
        'Symbol':             symbol,
        'Tickers':            json.dumps(tickers),
        'ID':                 news_id,
        'Created':            _lookup_created(news_id),
        'ArrivalDate':        news_dict['ArrivalTime'].strftime('%Y-%m-%d'),
        'ArrivalTime':        news_dict['ArrivalTime'].strftime('%H:%M:%S') + f".{news_dict['ArrivalTime'].microsecond // 1000:03d}",
        'CurlTime':           news_dict['CurlTime'].strftime('%H:%M:%S') + f".{news_dict['CurlTime'].microsecond // 1000:03d}",
        'Headline':           news_dict['Headline'],
        'Author':             _lookup_author(news_id),
        'Float':              _lookup_float(symbol),
        'MktCap':             _lookup_mktcap(symbol),
        'Exchange':           news_dict.get('exchange', ''),
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

    # 3) Clerk STEP-1b — resolve the arm, then confirm sentiment (or, if the
    #    headline was excluded but we had pre-armed on the alert, release).
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
    # NoCorefCompletedAt is stamped on any successful pipeline run, even when
    # the per-ticker block is empty (NER + allowed_tickers fallback both
    # failed). None means we didn't run a pipeline at all (empty body or error).
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


# ─── Callback — invoked from NW4 background thread ───────────────────────────

def on_news_accepted(news_dict: dict) -> None:
    """
    Invoked by NewsWatcher4 for every article that passes all filters.

    NW4's `Symbol` field is comma-joined for multi-ticker articles (up to 2).
    Strategy: fan-out per ticker. FinBERT-headliner and the body pipeline each
    run **once per article** (shared futures); the clerk ARM (STEP 1a) and
    sentiment (STEP 1b) run **once per ticker**.

    STEP 1a (the arm) now fires earlier — on the raw alert via _on_alert, before
    the body is curled — for any ticker that passed NW4's pre-filter. Here we
    therefore *reuse* the stashed arm future for pre-armed tickers and only arm
    NEW in-universe partner tickers discovered in the full article. A ticker
    that was pre-armed but is excluded (headline) or absent from the final list
    is released so we don't hold a warm pool client.
    """
    # Curl finished moments ago in NW4 (curl → normalize → accept → this callback);
    # stamp it here as the orchestrator-side "curl process finished" time.
    news_dict['CurlTime'] = datetime.now()

    raw_symbol = news_dict['Symbol']
    news_id    = news_dict['ID']
    tickers    = list(dict.fromkeys(t.strip() for t in raw_symbol.split(',') if t.strip()))
    if not tickers:
        logger.warning(f"on_news_accepted: id={news_id} has no tickers — skipping")
        return

    headline    = news_dict.get('Headline') or ''
    arm_blocked = any(s.lower() in headline.lower() for s in _excluded_strings)

    prearmed_set = set(news_dict.get('prearmed') or [])
    art_id       = news_dict.get('art_id') or news_id

    logger.info(
        f"on_news_accepted triggered: id={news_id} tickers={tickers} "
        f"prearmed={sorted(prearmed_set)} arm_blocked={arm_blocked}"
    )

    # STEP 1a — ARM (per ticker). Pre-armed tickers (armed on the alert by
    # _on_alert) already have an in-flight arm future stashed — reuse it. Partner
    # tickers found only in the full article are armed now, but ONLY if they are
    # in the in-strategy universe (_inscope_set: Float_M <= NW4_REJECT_FLOAT_GT or
    # blank) and the headline did not match an orchestrator excluded string. The
    # _inscope_set gate prevents a >50M partner riding on an in-strategy primary's
    # article from being armed — the per-article priced backstop can't see it
    # because _priced_data is now the filtered universe. Pre-armed primaries are
    # in-strategy by construction (they passed NW4's filtered universe pre-filter).
    f_arm = {}
    for symbol in tickers:
        if symbol in prearmed_set:
            with _arm_lock:
                f_arm[symbol] = _arm_results.pop((art_id, symbol), None)
        elif not arm_blocked and symbol in _inscope_set:
            f_arm[symbol] = _clerk_executor.submit(_arm_clerk, symbol)
        # else: arm_blocked, or a partner not in the in-strategy universe
        #       (Float_M > NW4_REJECT_FLOAT_GT) → leave unset (skipped downstream)

    # Release any ticker pre-armed on the alert that isn't among this article's
    # final tickers (alert primary differed from the scraped list).
    for symbol in prearmed_set - set(tickers):
        logger.info(f"[Clerk] {symbol} id={art_id}: pre-armed but not an article ticker — releasing")
        _on_alert_release(symbol, art_id)

    if arm_blocked:
        logger.info(f"[Clerk] id={news_id}: headline matched excluded string — "
                    f"not arming new tickers (pre-armed released via sentiment)")

    # FinBERT-headliner + body pipeline run once per article (shared futures).
    f_finbert = _finbert_executor.submit(analyze_finbert, news_dict)
    f_body    = _body_executor.submit(_run_body_pipeline, news_dict)
    # Fan out a TSV row only for in-strategy tickers. Out-of-universe partners
    # (e.g. foreign/dual-listings like 4TO riding on an in-strategy primary's PR)
    # are never armed and would only leave a near-empty audit row — skip them.
    # They remain recorded in the surviving row's Tickers JSON column.
    row_tickers = [s for s in tickers if s in _inscope_set or s in prearmed_set]
    if not row_tickers:
        # Safety net: an accepted article should always have >=1 in-scope ticker;
        # if the universe sets disagree, fall back to writing every ticker rather
        # than silently dropping the article from the TSV.
        logger.warning(f"on_news_accepted: id={news_id} has no in-scope tickers "
                       f"among {tickers} — writing all rows as fallback")
        row_tickers = tickers
    else:
        skipped = [s for s in tickers if s not in row_tickers]
        if skipped:
            logger.info(f"[Fan-out] id={news_id}: skipping out-of-universe "
                        f"partner tickers {skipped} (no TSV row)")
    for symbol in row_tickers:
        _collect_executor.submit(
            _collect_and_log, news_dict, tickers, symbol,
            f_finbert, f_body, f_arm.get(symbol), arm_blocked,
        )


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _inscope_set
    logger.info("Orchestrator4.0 starting...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

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

    # Archive the full universe as '..._nonFiltered.tsv' and rewrite the canonical
    # file to the in-strategy (Float_M <= NW4_REJECT_FLOAT_GT or blank) subset, so
    # NW4 never curls out-of-strategy names. Runs BEFORE _load_latest_iti_tsv() so
    # _iti_df loads the filtered canonical.
    _universe_tsv, _inscope_set = _split_universe_files()
    logger.info(
        f"[Universe] In-strategy universe: {len(_inscope_set)} symbols "
        f"(Float_M <= {NW4_REJECT_FLOAT_GT}M or blank)"
    )

    _load_latest_iti_tsv()
    _load_excluded_strings(ORCH_EXCLUDED_STRINGS_FILE)

    logger.info(f"[Universe] Using universe TSV: {_universe_tsv}")

    # Register callbacks BEFORE start() — no race window for missed items.
    # on_news_accepted fires post-curl (accepted articles); the alert callbacks
    # fire pre-curl so the clerk arm / reqMktData starts the instant a PR's
    # primary ticker passes NW4's cheap pre-filter.
    nw.register_callback(on_news_accepted)
    nw.register_alert_callback(_on_alert)
    nw.register_alert_release_callback(_on_alert_release)

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

    # Register AFTER nw.start() so Orchestrator4.0's handlers override NW4's
    signal.signal(signal.SIGINT,  _handle_sigint)
    signal.signal(signal.SIGTERM, _handle_sigint)

    try:
        _stop_event.wait()
    finally:
        logger.info("Shutting down executors...")
        _clerk_executor.shutdown(wait=True)
        _finbert_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        _body_executor.shutdown(wait=True)
        logger.info("Stopping NewsWatcher4...")
        nw.stop()
        logger.info("Orchestrator4.0 stopped.")


if __name__ == '__main__':
    main()
