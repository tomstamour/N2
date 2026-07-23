import sys
import os
import csv
import json
import logging
import threading
import socket
import time
import importlib.util as _ilu
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
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

# ── FinBERT import (hyphen in filename prevents normal import) ────────────────
_finbert_spec = _ilu.spec_from_file_location(
    "FinBERT_headliner",
    str(SCRIPTS_DIR / "FinBERT" / "FinBERT-headliner.py"),
)
_finbert_mod = _ilu.module_from_spec(_finbert_spec)
_finbert_spec.loader.exec_module(_finbert_mod)
analyze_headline   = _finbert_mod.analyze_headline
load_finbert_model = _finbert_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── FinMarBa import (2nd headline model, runs in parallel with FinBERT) ───────
# baptle/FinBERT_market_based (ONNX INT8) — its `indecisive` output confirms the
# headline trade leg (see _evaluate_trigger). Loaded by path like the others; its
# module name is distinct from FinBERT's, so their module-level singletons
# (_inferencer/_model_dir) and identically-named load_model() never collide. The
# default model_dir resolves to tools/Encoder_Models/finmarba_onnx (already
# exported). API: load_model() to pre-warm, score_headline(str) → dict with
# {positive, indecisive, negative, sentiment_score, label, elapsed_ms}.
_finmarba_spec = _ilu.spec_from_file_location(
    "FinMarBa_headliner",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "FinMarBa_headliner.py"),
)
_finmarba_mod = _ilu.module_from_spec(_finmarba_spec)
_finmarba_spec.loader.exec_module(_finmarba_mod)
score_finmarba_headline = _finmarba_mod.score_headline
load_finmarba_model     = _finmarba_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── keyword/combo scorer import (keyword_scorer-2.py — hyphen, so importlib) ──
# The LIVE body scorer (NOT the old keyword_scorer.py, whose ge20 defaults are
# wrong). get_default_scorer() wires the frozen ge15 artifacts + pinned
# prevalence 0.185 + the frozen combo JSON; O5.2 consumes ONLY
# res["model_proba_PLUS_kw_mult_nb"]. The module inserts its own dir into
# sys.path at exec time so its internal keyword_finder import resolves itself.
_kw_spec = _ilu.spec_from_file_location(
    "keyword_scorer_2",
    str(SCRIPTS_DIR / "tools" / "keyword" / "keyword_scorer-2.py"),
)
kw2 = _ilu.module_from_spec(_kw_spec)
_kw_spec.loader.exec_module(kw2)
# ─────────────────────────────────────────────────────────────────────────────

# ── RTPR_connector3 import (shared helpers ONLY — the connector is NOT run) ───
# RTPR_connector3 runs as its OWN standalone process and publishes every
# accepted firehose article on a local TCP feed; _run_feed_client below is the
# subscriber. This import never starts anything (main() is __main__-guarded) —
# it only borrows the connector's helpers so both processes agree byte-for-byte
# on clock and formatting: FIXED_TZ, now_fixed, format_millis_time/date,
# clean_inline_text, DailyFixedTZFileHandler/FixedTZFormatter.
_c3_spec = _ilu.spec_from_file_location(
    "RTPR_connector3_Firehose",
    str(SCRIPTS_DIR / "RTPR_connector3" / "RTPR_connector3_Firehose.py"),
)
c3 = _ilu.module_from_spec(_c3_spec)
# Must be registered BEFORE exec: the connector's frozen dataclass resolves its
# string annotations via sys.modules[cls.__module__] (PEP 563 + dataclasses).
sys.modules["RTPR_connector3_Firehose"] = c3
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
# Local to this script's directory so O5 is self-contained (no orchestrator3 dep).
ORCH_EXCLUDED_STRINGS_FILE = str(Path(__file__).resolve().parent / 'excluded_strings-2.txt')
MAX_TICKERS_PER_ARTICLE    = 2   # NW4 count filter: drop articles naming > 2 tickers
# ─────────────────────────────────────────────────────────────────────────────

# ── Clerk connection (x-wing-mole/clerk-1.1.py) ───────────────────────────────
# Warm pool of pre-connected ibapi clients running trade-mole + x-wing duos,
# driven over TCP/JSON with a two-step handshake:
#   STEP 1a (ARM)       on PR reception → {ticker, lastDailyClose, itiBaseline, tradeSizeBaseline}
#   STEP 1b (SENTIMENT) after scoring   → {ticker, Sentiment: "OK"|"BAD"}
# Start the clerk FIRST, e.g.:
#   path/to/venv/bin/python clerk-1.1.py --client-qty 5 --port 4002 --listen-port 8765
# (--port 4001 is the LIVE Gateway — real money. Use 4002 = paper GW for testing.)
CLERK_HOST             = n2_config.get(_CFG, 'CLERK_HOST', '127.0.0.1')
CLERK_PORT             = n2_config.get_int(_CFG, 'CLERK_PORT', 8765)
CLERK_TIMEOUT_SEC      = 5       # per-message TCP send/recv timeout
CLERK_ARM_WAIT_SEC     = 20      # max wait on the arm future before sending sentiment

# Trade-trigger gate (STEP 1b) — OR rule, either leg fires the trade:
#   Headline leg: FinBERT `positive` >= SENTIMENT_POSITIVE_MIN
#                 AND FinMarBa `indecisive` <= FMB_INDECISIVE_MAX  (both must hold)
#   Keyword  leg: body combo `model_proba_PLUS_kw_mult_nb` >= COMBO_TRIGGER_MIN
# (5.2 change: the old FinBERT-`positive`-alone leg at 0.83 is gone — the headline
# leg now also requires FinMarBa's `indecisive` confirmation.) A score that is
# None (model error / scorer timeout) makes its own condition false: a missing
# positive OR indecisive kills the headline leg, a missing combo kills the keyword
# leg; the other leg can still fire, and both legs false → "BAD". The wire
# protocol to the clerk stays "OK"/"BAD"; the trigger source (Keyword /
# FinBERT_FMB / Keyword_FinBERT_FMB) is recorded in the TSV ClerkSentiment only.
SENTIMENT_POSITIVE_MIN = 0.6     # FinBERT `positive` floor (headline leg, cond 1)
FMB_INDECISIVE_MAX     = 0.1347     # FinMarBa `indecisive` ceiling (headline leg, cond 2)
COMBO_TRIGGER_MIN      = 0.6     # keyword-combo leg threshold (walk-forward rule)
KW_SCORE_WAIT_SEC      = 10      # max wait on the kw-score future before STEP 1b
                                 # (~4 ms typical — a hung scorer must never
                                 # delay the trade decision beyond this)

# Baseline lookups from the daily universe TSV.
TRADE_SIZE_SENTINEL  = 44444.0   # trade-mole treats this (or <=0/missing) as "no baseline"
DEFAULT_BASELINE_ITI = 44444.0   # fallback ITI (s) when symbol absent/NaN in universe
# ─────────────────────────────────────────────────────────────────────────────

# ── RTPR_connector3 feed (the news source) ────────────────────────────────────
# The STANDALONE connector publishes every accepted firehose article as one
# JSON line on its loopback TCP feed —
#   {"type":"article","arrival_ms":…,"data":{raw payload},"row":{connector row}}
# plus a 5s heartbeat. Start it separately (either order works — we retry until
# the feed is up):
#   cd RTPR_connector3 && /home/tom/venv/bin/python RTPR_connector3_Firehose.py --flush-on-shutdown
# Articles published while this process is down are dropped by design (no
# replay on reconnect) — the connector's own PR_dataframe table still records
# them. Run only ONE orchestrator instance — NEVER two of Orchestrator5.0/5.1/5.2
# at the same time (both would double-arm the clerk); extra read-only watchers
# like `nc 127.0.0.1 8900` are fine.
FEED_HOST                = n2_config.get(_CFG, 'FEED_HOST', '127.0.0.1')
FEED_PORT                = n2_config.get_int(_CFG, 'FEED_PORT', 8900)
FEED_CONNECT_TIMEOUT_SEC = 5     # per-attempt TCP connect timeout
FEED_RETRY_SEC           = 3     # reconnect cadence while the connector is down
FEED_SILENCE_TIMEOUT_SEC = 20    # connector heartbeats every 5s → 20s silence = dead peer
# ─────────────────────────────────────────────────────────────────────────────

# ── Worker pools (parallelism knobs) ──────────────────────────────────────────
# The two headline models (FinBERT + FinMarBa) are the trade-critical path: a
# burst of ~15 PRs inside 1s (observed 2026-07-02) clears in ~4 waves on 4
# threads. Each model gets its OWN pool so they run simultaneously (both are ONNX
# INT8 → inference releases the GIL, so two threads = genuine parallel execution
# on separate cores) and FinMarBa can never starve FinBERT. For a single article
# the critical path uses just one thread from each pool at once; the two 4-wide
# pools only co-saturate under simultaneous bursts, tolerable for ~12 ms INT8
# inferences. The keyword/combo scorer is ~4 ms/PR, so ONE worker absorbs the
# worst observed burst (~60 ms of queue) and sidesteps any KeywordScorer/sklearn
# thread-safety question.
FINBERT_HEADLINE_WORKERS  = 4     # threads sharing the preloaded FinBERT model
FINMARBA_HEADLINE_WORKERS = 4     # threads sharing the preloaded FinMarBa model
KW_SCORE_WORKERS          = 1     # single worker on purpose — see above
COLLECT_WORKERS           = 8     # per-ticker collectors (resolve futures + TSV)
CLERK_WORKERS             = 6     # STEP-1a arms; a touch above clerk's 5-client pool
# ─────────────────────────────────────────────────────────────────────────────

DEDUP_MAX_IDS = 50_000  # FIFO cap on the per-day seen-article-ID stash (leak guard)
CONTENT_DEDUP_MAX_KEYS = 10_000  # FIFO cap on the content-fingerprint stash (leak guard)

# ─── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger('Orchestrator5.2')


class _OrchDailyFileHandler(c3.DailyFixedTZFileHandler):
    """Daily Orchestrator5.2_YYYY_MM_DD.log in ./logs (fixed UTC-4 date key) —
    only the filename differs from the connector's handler. Attached to the ROOT
    logger so every orchestrator record lands in the file; connector3 records do
    NOT propagate (its logger is queue-fed with propagate=False) and are already
    persisted in RTPR_connector3/logs."""

    def path_for_date(self, date_key: str) -> Path:
        return self.log_dir / f"Orchestrator5.2_{date_key}.log"


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

# Keyword/combo scorer (keyword_scorer-2.py KeywordScorer) — loaded once in
# main() before the feed connects, then only read by the kwscore worker.
_kw_scorer = None

# Orchestrator-side article-ID dedup. The connector's own dedup lives in its
# dataframe_writer DOWNSTREAM of the row hook, and a WS reconnect can replay
# recent articles straight through the listener — so the hook dedups here.
# Touched ONLY from _on_row_built (the single WS loop thread) → lock-free. The
# insertion-ordered dict doubles as a FIFO for the size cap; cleared when the
# arrival day changes.
_seen_ids: dict = {}
_seen_day = None

# Content-level dedup: RTPR sometimes re-publishes the same article under a
# NEW ID (PRZO 2026-07-06 227ms apart; FOFO 2026-07-07 ~12min apart), so the
# ID dedup above misses it. key = (normalized headline, sorted ticker tuple)
# → arrival_dt of first sighting; any same-day hit is dropped. Same lock-free
# single-thread + per-day-clear + insertion-order-FIFO regime as _seen_ids.
_seen_content: dict = {}


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
# Second headline model — its own pool so it runs in parallel with FinBERT.
_finmarba_executor = ThreadPoolExecutor(max_workers=FINMARBA_HEADLINE_WORKERS,
                                        thread_name_prefix='finmarba')
_collect_executor = ThreadPoolExecutor(max_workers=COLLECT_WORKERS,
                                       thread_name_prefix='collect')

# Keyword/combo body scorer — single worker (see KW_SCORE_WORKERS note above).
_kw_executor = ThreadPoolExecutor(max_workers=KW_SCORE_WORKERS,
                                  thread_name_prefix='kwscore')

_tsv_lock = threading.Lock()

# Dedicated pool for clerk STEP-1a arms so arming is never starved by the collect
# tasks.
_clerk_executor = ThreadPoolExecutor(max_workers=CLERK_WORKERS, thread_name_prefix='clerk')


# ─── Analysis functions ───────────────────────────────────────────────────────

def analyze_finbert(news_dict: dict) -> dict:
    """FinBERT headline sentiment analysis. Runs once per article, in parallel
    with analyze_finmarba (separate thread pools, ONNX inference releases the GIL)."""
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


def analyze_finmarba(news_dict: dict) -> dict:
    """FinMarBa (baptle/FinBERT_market_based) headline sentiment. Runs once per
    article, in parallel with analyze_finbert. Its `indecisive` value is the
    second condition of the headline trade leg; the full distribution is stored in
    the TSV FMB_* columns for offline re-analysis."""
    result = score_finmarba_headline(news_dict['Headline'])
    logger.info(
        f"[FinMarBa] {news_dict['Symbol']} → {result['label'].upper()} "
        f"(indecisive={result['indecisive']:.4f})"
    )
    return {
        'positive':        result['positive'],
        'indecisive':      result['indecisive'],
        'negative':        result['negative'],
        'sentiment_score': result['sentiment_score'],
        'label':           result['label'],
    }


_KW_NONE_RESULT = {'model_proba_PLUS_kw_mult_nb': None, 'KwScoreCompletedAt': None}


def _score_keywords(news_dict: dict) -> dict:
    """Keyword/combo scorer on the PR body. Runs once per article on the kwscore
    worker. Never raises — any error returns the all-None dict so the pipeline
    keeps running and the row records None.

    Scores EXACTLY the string that lands in the TSV Body cell
    (clean_inline_text(body_raw)) — NOT the connector-cleaned article body —
    so the live number equals what an offline rescore of the same TSV produces
    (all backtests / walk-forward truth were computed on the Body column). An
    empty body is scored anyway (combo ≈ 0.024) to stay consistent with offline
    rescoring."""
    news_id = news_dict.get('ID')
    try:
        body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
        t0 = time.monotonic()
        res = _kw_scorer.score_text(body_cell, headline=news_dict['Headline'])
        combo = res['model_proba_PLUS_kw_mult_nb']  # KeyError = combo not loaded → caught below
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        # numpy float64 → plain float; 6 decimals matches the backtest TSVs.
        combo = float(round(float(combo), 6))
        logger.info(f"[KwScore] id={news_id} sym={news_dict.get('Symbol')}: "
                    f"combo={combo:.6f} in {elapsed_ms:.1f}ms")
        return {
            'model_proba_PLUS_kw_mult_nb': combo,
            'KwScoreCompletedAt': c3.format_millis_time(c3.now_fixed()),
        }
    except Exception as exc:
        logger.error(f"[KwScore] id={news_id}: scorer error: {exc}", exc_info=True)
        return dict(_KW_NONE_RESULT)


# ─── Trade-trigger gate ───────────────────────────────────────────────────────

def _evaluate_trigger(finbert_val: dict, finmarba_val: dict, kw_val: dict) -> tuple:
    """STEP-1b gate, OR rule:
        Headline leg: FinBERT positive >= SENTIMENT_POSITIVE_MIN
                      AND FinMarBa indecisive <= FMB_INDECISIVE_MAX  (both required)
        Keyword  leg: body combo >= COMBO_TRIGGER_MIN
    Returns (is_ok, label) naming what fired: 'Keyword', 'FinBERT_FMB',
    'Keyword_FinBERT_FMB' (both), or 'BAD' (neither). A None score (model error /
    scorer timeout) makes only its own condition false — a missing positive OR
    indecisive kills the headline leg, a missing combo kills the keyword leg; the
    other leg can still fire, and both legs false → 'BAD'."""
    positive   = finbert_val.get('positive')
    indecisive = finmarba_val.get('indecisive')
    combo      = kw_val.get('model_proba_PLUS_kw_mult_nb')
    headline_leg = (positive   is not None and positive   >= SENTIMENT_POSITIVE_MIN
                    and indecisive is not None and indecisive <= FMB_INDECISIVE_MAX)
    keyword_leg  = combo      is not None and combo      >= COMBO_TRIGGER_MIN
    if headline_leg and keyword_leg:
        return True, 'Keyword_FinBERT_FMB'
    if headline_leg:
        return True, 'FinBERT_FMB'
    if keyword_leg:
        return True, 'Keyword'
    return False, 'BAD'


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
    """STEP 1b — SENTIMENT. Inform the clerk whether the trade-trigger gate
    passed. 'BAD' is sent explicitly so the clerk can early-release the client.
    The wire protocol stays "OK"/"BAD" — the trigger label is TSV-only."""
    payload = {"ticker": symbol, "Sentiment": "OK" if is_ok else "BAD"}
    reply = _send_clerk(payload)
    logger.info(f"[Clerk] SENTIMENT {symbol} {payload['Sentiment']} → {reply or 'no-reply'}")
    return reply


# ─── TSV writer ───────────────────────────────────────────────────────────────

_TSV_COLUMNS = [
    'Symbol', 'Tickers', 'ID', 'ArrivalDate', 'Created', 'ArrivalTime',
    'Headline', 'Body', 'Author',
    'Float', 'MktCap', 'Exchange',
    'LastDailyClose', 'itiBaseline', 'tradeSizeBaseline',
    'FinBERTCompletedAt',
    'positive', 'negative', 'neutral', 'sentiment_score', 'label',
    'FMB_time',
    'FMB_positive', 'FMB_indecisive', 'FMB_negative', 'FMB_sentiment_score', 'FMB_label',
    'ClerkSentiment',
    'model_proba_PLUS_kw_mult_nb', 'KwScoreCompletedAt',
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
                     f_finbert, f_finmarba, f_kw, f_arm, arm_blocked: bool) -> None:
    """Resolve the two headline models (FinBERT + FinMarBa) and the keyword/combo
    body score, evaluate the OR trade gate, complete the clerk handshake (wait for
    the STEP-1a arm reply, then send the STEP-1b sentiment), and write the TSV row.

    Ordering is load-bearing: we wait on the arm future *before* sending sentiment
    so the clerk has registered the session — required both for the OK gate and for
    the BAD early-release to land. The kw future is resolved BEFORE the arm wait
    (adds only ms; the arm reply wait already dominates), with a hard
    KW_SCORE_WAIT_SEC ceiling so a hung scorer can't stall the trade decision. The
    two headline futures were submitted together in _on_row_built and run in
    parallel, so resolving them back-to-back here costs ~max(model), not the sum."""
    news_id = news_dict['ID']

    # 1a) Headline FinBERT
    try:
        finbert_val = f_finbert.result(timeout=60)
        finbert_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}", exc_info=True)
        finbert_val = {}
        finbert_completed_at = None

    # 1b) Headline FinMarBa (ran in parallel with FinBERT; supplies `indecisive`)
    try:
        finmarba_val = f_finmarba.result(timeout=60)
        fmb_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FinMarBa-headliner error for id={news_id}: {exc}", exc_info=True)
        finmarba_val = {}
        fmb_completed_at = None

    # 2) Keyword/combo body score (~ms; both fan-out tickers share the future)
    try:
        kw_val = f_kw.result(timeout=KW_SCORE_WAIT_SEC)
    except Exception as exc:
        logger.error(f"[KwScore] {symbol}: kw future failed/timed out for id={news_id}: {exc}")
        kw_val = dict(_KW_NONE_RESULT)

    # 3) OR trade gate → decision + trigger label
    is_ok, trigger_label = _evaluate_trigger(finbert_val, finmarba_val, kw_val)

    # 4) Build the row. Created/Arrival*/Author/Exchange are pass-throughs from
    #    the reception (all stamped on the connector's fixed UTC-4 clock). The
    #    Body CELL stores the RAW payload as RTPR sent it (HTML tags/entities
    #    kept, if any) with only whitespace runs collapsed so the TSV stays
    #    one-row-per-line — the SAME string _score_keywords scored, so an
    #    offline rescore of this TSV reproduces the live combo exactly.
    completed_dict = {
        'Symbol':             symbol,
        'Tickers':            json.dumps(tickers),
        'ID':                 news_id,
        'ArrivalDate':        news_dict.get('ArrivalDate', ''),
        'Created':            news_dict.get('Created', ''),
        'ArrivalTime':        news_dict.get('ArrivalTime', ''),
        'Headline':           news_dict['Headline'],
        'Body':               c3.clean_inline_text(news_dict.get('body_raw') or ''),
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
        'FMB_time':             fmb_completed_at,
        'FMB_positive':         finmarba_val.get('positive'),
        'FMB_indecisive':       finmarba_val.get('indecisive'),
        'FMB_negative':         finmarba_val.get('negative'),
        'FMB_sentiment_score':  finmarba_val.get('sentiment_score'),
        'FMB_label':            finmarba_val.get('label'),
        'model_proba_PLUS_kw_mult_nb': kw_val.get('model_proba_PLUS_kw_mult_nb'),
        'KwScoreCompletedAt':          kw_val.get('KwScoreCompletedAt'),
    }

    # 5) Clerk STEP-1b — resolve the arm, then confirm sentiment. f_arm is None when
    #    the ticker was never armed (excluded headline, or not in the universe).
    #    ClerkSentiment encodes BOTH the trigger and the clerk reply:
    #      armed        → "<Keyword|FinBERT|Keyword_FinBERT|BAD>:<reply status>"
    #      arm rejected → "skipped:arm_rejected"
    #      never armed  → "skipped:excluded_string" / "skipped:not_armed"
    #    (arm accept/reject detail stays in the log via _arm_clerk).
    if f_arm is None:
        completed_dict['ClerkSentiment'] = ('skipped:excluded_string' if arm_blocked
                                            else 'skipped:not_armed')
    else:
        try:
            arm_reply = f_arm.result(timeout=CLERK_ARM_WAIT_SEC)
        except Exception as exc:
            logger.warning(f"[Clerk] {symbol}: arm future failed/timed out: {exc}")
            arm_reply = {}
        arm_status = arm_reply.get('status')
        if arm_status == 'rejected':
            # No session was created — sending sentiment is pointless.
            completed_dict['ClerkSentiment'] = 'skipped:arm_rejected'
        else:
            # accepted (session registered) or unknown (best-effort) → STEP 1b.
            sentiment_reply = _send_sentiment(symbol, is_ok)
            completed_dict['ClerkSentiment'] = (
                f"{trigger_label}:{sentiment_reply.get('status', 'no_reply') or 'no_reply'}")

    # 6) Write the TSV row
    _append_to_tsv(completed_dict)

    # Full body lives in the TSV only — log/print a preview. OrchTime is kept in
    # news_dict/logs (reception stamp) but is no longer a TSV column.
    log_dict = dict(completed_dict)
    log_dict['OrchTime'] = news_dict.get('OrchTime', '')
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
    print(f"  FMB_indecisive               : {completed_dict['FMB_indecisive']}")
    print(f"  FMB_positive                 : {completed_dict['FMB_positive']}")
    print(f"  FMB_negative                 : {completed_dict['FMB_negative']}")
    print(f"  FMB_label                    : {completed_dict['FMB_label']}")
    print(f"  FMB_time                     : {completed_dict['FMB_time']}")
    print(f"  model_proba_PLUS_kw_mult_nb  : {completed_dict['model_proba_PLUS_kw_mult_nb']}")
    print(f"  KwScoreCompletedAt           : {completed_dict['KwScoreCompletedAt']}")
    print(f"  Trigger                      : {trigger_label}")
    print(f"  ClerkSentiment               : {completed_dict['ClerkSentiment']}")
    print(f"{'='*60}\n")


# ─── Article reception — fed by the standalone connector's TCP feed ───────────

def _on_row_built(data: dict, row: dict, arrival_dt) -> None:
    """Article reception handler, called by _run_feed_client for every article
    line the standalone connector publishes (`data` = raw firehose payload,
    `row` = connector-built row, arrival_dt = when the connector received the
    firehose frame). The full article arrives inline, so 4.2's
    alert/pre-arm/release split stays collapsed into this single reception
    event: dedup, the two kept NW4 input filters (ticker count ≤ 2, NW4-level
    excluded strings → drop), then arm every in-universe ticker and fan out.

    Runs on the feed-reader (main) thread → stays lean: only dict/string
    filters and non-blocking executor submits; all heavy work goes to the
    thread pools. `data` and `row` are this process's own JSON copies."""
    global _seen_day
    # OrchTime = the moment this article lands in the orchestrator (includes the
    # local TCP hop) — stamped first, on the connector's fixed UTC-4 clock like
    # every other time stamp. Log-only since 5.1 (no longer a TSV column).
    orch_time = c3.format_millis_time(c3.now_fixed())

    art_id = (row.get('ID') or '').strip()

    # Dedup. The feed broadcasts BEFORE the connector's own dedup, and a firehose
    # reconnect on the connector side can replay recent articles — so dedup here,
    # keyed per arrival day (arrival_dt is UTC-4-aware). Single reader thread →
    # no lock.
    day = arrival_dt.date()
    if day != _seen_day:
        _seen_ids.clear()
        _seen_content.clear()
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

    # Content dedup — same headline+tickers the same day is an RTPR re-publish
    # under a new ID (PRZO 2026-07-06, FOFO 2026-07-07) → drop like an ID dup.
    fp = (' '.join(headline.lower().split()), tuple(sorted(tickers)))
    prev = _seen_content.get(fp)
    if prev is not None:
        logger.info(f"[Row] id={art_id}: duplicate content (headline+tickers seen "
                    f"{(arrival_dt - prev).total_seconds():.1f}s ago) — skipped")
        return
    _seen_content[fp] = arrival_dt
    while len(_seen_content) > CONTENT_DEDUP_MAX_KEYS:
        _seen_content.pop(next(iter(_seen_content)))

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
        'body_raw':     str(data.get('article_body') or data.get('body') or ''),  # exactly as RTPR sent it → TSV Body cell + kw scorer
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

    # FinBERT + FinMarBa headliners + keyword/combo scorer run once per article
    # (shared futures — all three are text-only and identical for both tickers).
    # The two headline models are submitted together to their separate pools so
    # they execute simultaneously (ONNX inference releases the GIL).
    f_finbert  = _finbert_executor.submit(analyze_finbert, news_dict)
    f_finmarba = _finmarba_executor.submit(analyze_finmarba, news_dict)
    f_kw       = _kw_executor.submit(_score_keywords, news_dict)

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
            f_finbert, f_finmarba, f_kw, f_arm.get(symbol), arm_blocked,
        )


# ─── Feed client (subscribes to the standalone RTPR_connector3) ───────────────

def _handle_feed_line(raw: bytes) -> None:
    """Dispatch one JSON line from the feed. Never raises — a malformed line or
    a handler bug must not kill the reception loop."""
    try:
        msg = json.loads(raw.decode('utf-8'))
    except Exception as exc:
        logger.warning(f"[Feed] bad JSON line ({exc}) — {len(raw)} bytes skipped")
        return

    mtype = msg.get('type')
    if mtype == 'article':
        data = msg.get('data')
        row  = msg.get('row')
        if not isinstance(data, dict) or not isinstance(row, dict):
            logger.warning(f"[Feed] malformed article message — keys={sorted(msg)}")
            return
        # ArrivalTime semantics preserved across the hop: arrival_ms is the
        # connector's firehose-frame stamp, not our read time.
        arrival_ms = msg.get('arrival_ms')
        arrival_dt = (datetime.fromtimestamp(arrival_ms / 1000.0, tz=c3.FIXED_TZ)
                      if isinstance(arrival_ms, (int, float)) else c3.now_fixed())
        try:
            _on_row_built(data, row, arrival_dt)
        except Exception:
            logger.exception(f"[Feed] _on_row_built failed for id={row.get('ID') or '-'}")
    elif mtype == 'hello':
        logger.info(f"[Feed] server hello: service={msg.get('service')} "
                    f"proto={msg.get('proto')} server_time={msg.get('server_time')}")
        if msg.get('proto') != 1:
            logger.warning(f"[Feed] unexpected feed protocol {msg.get('proto')!r} (expected 1)")
    elif mtype != 'heartbeat':
        logger.info(f"[Feed] ignoring message type={mtype!r}")


def _log_feed_retry(attempts: int, exc=None) -> None:
    """Throttled connect-retry logging: WARNING once, then ~1 line/min."""
    if attempts == 1:
        detail = f" ({exc})" if exc else ""
        logger.warning(f"[Feed] connector feed unreachable at {FEED_HOST}:{FEED_PORT}{detail} "
                       f"— retrying every {FEED_RETRY_SEC}s (is RTPR_connector3_Firehose.py running?)")
    elif attempts % 20 == 0:
        logger.info(f"[Feed] still waiting for the connector feed (attempt {attempts})")


def _run_feed_client() -> None:
    """Blocking JSON-lines feed client — runs on the main thread until Ctrl+C.

    Connects to the standalone connector's feed and hands every article message
    to _on_row_built. Reconnects forever on any failure (connector down or
    restarting); articles published while disconnected are DROPPED by design —
    the connector's own PR_dataframe table still records them, and no replay
    happens on reconnect. The connector heartbeats every 5s, so
    FEED_SILENCE_TIMEOUT_SEC without a single byte means a dead/hung peer."""
    attempts = 0
    lost_at = None  # time.monotonic() of the last connection loss (for gap logging)
    while True:
        connected = False
        try:
            with socket.create_connection((FEED_HOST, FEED_PORT),
                                          timeout=FEED_CONNECT_TIMEOUT_SEC) as sock:
                sock.settimeout(FEED_SILENCE_TIMEOUT_SEC)
                connected = True
                attempts = 0
                gap = f" gap={time.monotonic() - lost_at:.1f}s" if lost_at is not None else ""
                logger.info(f"[Feed] FEED_CONNECTED {FEED_HOST}:{FEED_PORT}{gap}"
                            + (" — PRs from the gap were dropped (see connector's table)"
                               if lost_at is not None else ""))
                buf = b""
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        logger.warning("[Feed] FEED_DISCONNECTED reason=eof "
                                       "(connector closed the feed)")
                        break
                    buf += chunk
                    while True:
                        nl = buf.find(b"\n")
                        if nl < 0:
                            break
                        line, buf = buf[:nl], buf[nl + 1:]
                        if line:
                            _handle_feed_line(line)
            lost_at = time.monotonic()
        except socket.timeout as exc:
            if connected:
                logger.warning(f"[Feed] FEED_SILENCE >{FEED_SILENCE_TIMEOUT_SEC}s "
                               f"without data/heartbeat — reconnecting")
                lost_at = time.monotonic()
            else:
                attempts += 1
                _log_feed_retry(attempts, exc)
        except OSError as exc:
            if connected:
                logger.warning(f"[Feed] FEED_DISCONNECTED reason={exc}")
                lost_at = time.monotonic()
            else:
                attempts += 1
                _log_feed_retry(attempts, exc)
        time.sleep(FEED_RETRY_SEC)


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global _inscope_set, _nw_excluded_strings, _excluded_strings, _last_iti_reload_date
    global _kw_scorer
    logger.info("Orchestrator5.2 starting...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # Pre-warm BOTH headline models + a smoke inference each so the first real PR
    # pays no session/tokenizer init or first-call kernel-JIT cost on either model.
    logger.info("Pre-loading FinBERT-headliner model...")
    load_finbert_model()
    analyze_headline("startup self-check")          # warm ORT kernels
    logger.info("FinBERT-headliner model ready.")

    logger.info("Pre-loading FinMarBa-headliner model...")
    load_finmarba_model()
    score_finmarba_headline("startup self-check")   # warm ORT kernels
    logger.info("FinMarBa-headliner model ready.")

    # Keyword/combo scorer: load the frozen ge15 artifacts (~1 s, prints its own
    # "KeywordScorer: ... | combo=yes" line) and HARD-FAIL if the combo weights
    # JSON didn't load — KeywordScorer.load only prints a note and score_text
    # then silently lacks the combo key; a trading process must refuse to start
    # rather than trade on None. The smoke call also pre-warms sklearn so the
    # first real PR pays no first-call overhead.
    logger.info("Pre-loading keyword/combo scorer...")
    _kw_scorer = kw2.get_default_scorer()
    smoke = _kw_scorer.score_text("startup self-check", headline="")
    if "model_proba_PLUS_kw_mult_nb" not in smoke:
        raise RuntimeError("combo score missing — DEFAULT_COMBO_MODEL JSON not "
                           "found/loaded; refusing to start")
    logger.info("Keyword/combo scorer ready.")

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

    # Daily universe reload thread (swaps _iti_df + _inscope_set; the standalone
    # connector reloads its own WS subscribe list at 03:50 on its side).
    _stop_event = threading.Event()
    _last_iti_reload_date = datetime.now(tz=ZoneInfo('America/New_York')).date()
    _iti_thread = threading.Thread(
        target=_iti_reload_worker, args=(_stop_event,),
        daemon=True, name='iti-reload',
    )
    _iti_thread.start()

    logger.info(
        f"Subscribing to the RTPR_connector3 feed at {FEED_HOST}:{FEED_PORT} — "
        f"start the connector as its own process (any order). "
        f"Waiting for news... (Ctrl+C to stop)"
    )

    try:
        _run_feed_client()
    except KeyboardInterrupt:
        logger.info("Ctrl+C — shutting down (the standalone connector is unaffected)")
    finally:
        logger.info("Shutting down executors...")
        _stop_event.set()
        _clerk_executor.shutdown(wait=True)
        _finbert_executor.shutdown(wait=True)
        _finmarba_executor.shutdown(wait=True)
        _kw_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        logger.info("Orchestrator5.2 stopped.")


if __name__ == '__main__':
    main()
