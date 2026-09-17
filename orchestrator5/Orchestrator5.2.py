import sys
import os
import csv
import json
import logging
import threading
import socket
import time
import re
import difflib
import operator
import importlib.util as _ilu
import joblib
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import NamedTuple
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

# ── FLANG-BERT import (3rd headline model, 2026-07-29) ───────────────────────
# avinasht/FLANG-BERT_flang-bert (ONNX INT8) — its `sentiment_score`
# (positive − negative) is exposed to the rules file as `FLANG_score`.
# This is the SAME module that produced the offline FLANG-BERT_sentiment_score
# column via tools/Encoder_Models/HeadScore11encoders.py, so the live score equals
# an offline rescore of the same headline — which is what every FLANG_score cut in
# orchestrator_trigger_rules.txt was calibrated on. Do NOT edit the headliner or its
# flangbert_sentiment_onnx/ artifacts; both are frozen.
# Two collisions to be aware of: this module's public fn is `analyze_headline`
# (the SAME name already bound to FinBERT above) and its loader is likewise named
# `load_model`, so the distinct module name below plus the distinct aliases are
# what keep the three modules' singletons (_inferencer/_model_dir) apart.
# API: load_model() to pre-warm, analyze_headline(str) → dict with
# {headline, positive, neutral, negative, sentiment_score, label, inference_ms}.
# NOTE the timing key is `inference_ms` here, not FinMarBa's `elapsed_ms`.
_flang_spec = _ilu.spec_from_file_location(
    "FLANG_BERT_headliner",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "FLANG-BERT_headliner.py"),
)
_flang_mod = _ilu.module_from_spec(_flang_spec)
_flang_spec.loader.exec_module(_flang_mod)
score_flang_headline = _flang_mod.analyze_headline
load_flang_model     = _flang_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── keyword scorers (keyword_scorer-2.py — hyphen, so importlib) ─────────────
# 2026-07-28: the old `model_proba_PLUS_kw_mult_nb >= 0.67` combo leg is GONE.
# It came from SELECTED-MODEL_classify_fromPRhigh_ge15_excStr2, trained on the
# PRE-2026-07-26 BIASED fromPR_High label (the baseline was the PR's own bar).
# Replaced by the corrected-label ge15 pair below — see the gate block ~line 120.
#
# Only the two text-prep helpers are borrowed from this module; the pipelines are
# loaded straight from joblib and called directly. Deliberate: KeywordScorer's
# score_texts() also runs the n-gram/keyword machinery for the kw_* methods,
# which we would compute and throw away twice per PR on the trade-critical path.
# The module inserts its own dir into sys.path at exec time so its internal
# keyword_finder import resolves itself.
_kw_spec = _ilu.spec_from_file_location(
    "keyword_scorer_2",
    str(SCRIPTS_DIR / "tools" / "keyword" / "keyword_scorer-2.py"),
)
kw2 = _ilu.module_from_spec(_kw_spec)
_kw_spec.loader.exec_module(kw2)
from keyword_finder import strip_wire_boilerplate  # noqa: E402 (kw2 put it on sys.path)

# The two corrected-label models. Trained on
# 04_PRstats_..._FromPRhigh_TRAIN_pre20260620_cap400.tsv (2647 rows, train pos
# rate 0.2286) — i.e. TRAIN only, so VAL (777 rows from 2026-06-20) was an honest
# holdout for the thresholds below.
KW15_DIR = (SCRIPTS_DIR / "tools" / "keyword" /
            "model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN")
KW15_BODY_PIPELINE = KW15_DIR / "classify_fromPRhigh_ge15" / "tfidf_fitted_pipeline.joblib"
KW15_HEAD_PIPELINE = KW15_DIR / "classify_fromPRhigh_ge15_headline" / "tfidf_fitted_pipeline.joblib"

# ⚠ The 400-word body cap is NOT optional. Corpus bodies are capped at 400 words
# UPSTREAM (Training_guide §"already capped at 400 words", median = max = 400),
# so the models only ever saw the first 400 words. Verified 2026-07-28 on the 777
# VAL rows: dropping the cap moves max |Δproba| to 0.212 and FLIPS 49 fire
# decisions (154 → 165 fires). The cap's ORDER relative to stripping does not
# matter (max |Δ| 0.014, zero flips), so capping after the strip — as here — is
# equivalent to the corpus's cap-then-strip. Headlines are never capped.
KW15_BODY_MAX_WORDS = 400
# ─────────────────────────────────────────────────────────────────────────────

# ── PR classifier + earnings scorer (the Earnings trade leg, 2026-08-02) ──────
# Neither filename has a hyphen, so a plain import would work — but both live in
# directories that are NOT packages, and `earning_scorer` is a generic enough name
# that putting its dir on sys.path invites a collision. Path-loading keeps the
# file's existing style and touches nothing global. No sys.modules pre-registration
# is needed here: that gotcha is specific to RTPR_connector3_Firehose's frozen
# dataclass, and neither of these two defines one.
#
# Both are import-safe (module level is stdlib imports + literals + re.compile;
# argparse/pandas are only touched inside their __main__-guarded main()) and
# thread-safe (compiled regexes + function-locals only, no mutable globals), so a
# pool worker can call them concurrently.
_prclass_spec = _ilu.spec_from_file_location(
    "PrClassifier",
    str(SCRIPTS_DIR / "tools" / "categorizer" / "PrClassifier.py"),
)
_prclass_mod = _ilu.module_from_spec(_prclass_spec)
_prclass_spec.loader.exec_module(_prclass_mod)
classify_pr = _prclass_mod.classify            # (text) -> (FineClass, BroadClass)
PRCLASS_BODY_LEAD_CHARS = _prclass_mod.BODY_LEAD_CHARS   # body fallback window (1500)

# The regex earnings extractor + additive rule score. Its `mktcap` argument is
# really FLOAT-CAP IN DOLLARS (Float(M shares) * LastDailyClose * 1e6), not market
# cap — see _classify_and_score_earnings. Aliased like the headliners so nothing
# generic (`classify`, `load_params`, `score_metrics`) lands in this namespace.
_earn_spec = _ilu.spec_from_file_location(
    "earning_scorer",
    str(SCRIPTS_DIR / "tools" / "Earning_Scorer" / "earning_scorer.py"),
)
_earn_mod = _ilu.module_from_spec(_earn_spec)
_earn_spec.loader.exec_module(_earn_mod)
earn_extract_metrics = _earn_mod.extract_metrics
earn_score_metrics   = _earn_mod.score_metrics
earn_load_params     = _earn_mod.load_params
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

# ── Trade-trigger gate (STEP 1b) ─────────────────────────────────────────────
# THE GATE IS NOT IN THIS FILE. Every rule and every threshold lives in
#     orchestrator5/orchestrator_trigger_rules.txt
# read ONCE at startup by _load_trigger_rules(), BEFORE any model is loaded. A
# missing / empty / malformed file is a HARD FAIL — there is deliberately no
# built-in fallback gate and no skip-the-bad-line: a trading process must never
# run a gate nobody wrote, and a typo must never silently delete a branch while
# the tape still looks alive.
# ⚠ Read at STARTUP ONLY. Editing that file does NOT change a running process.
#   The `[Gate] ACTIVE` block in logs/ is the authoritative record of what a given
#   process holds. `Orchestrator5.2.py --check-rules` parses + echoes and exits.
# Semantics: ordinary rules are OR-ed (any one firing trades), conditions within a
#   rule are AND-ed, a None value ALWAYS fails its condition. A `REQUIRE <Name>: ...`
#   line is a PRECONDITION, AND-ed over the whole gate: it never fires, it only
#   permits, so the gate is (every REQUIRE holds) AND (any ordinary rule fires).
#   ⚠ A REQUIRE condition therefore FAILS CLOSED — a dead scorer behind one stops all
#   trading, not one branch. See _evaluate_trigger.
# Calibration, dates, corpora and the history of every cut: orchestrator5/CLAUDE.md
#   §"Clerk handshake" and orchestrator5/version-control.txt. The ⚠ warnings a human
#   RETUNING the gate must read — above all: do NOT add a `FinMarBa_ind_hd <= x` rule
#   that fires alone, it is direction-agnostic and traded a reverse split — live in
#   the header of the rules file, which is where the edit actually happens.
TRIGGER_RULES_FILE = str(Path(__file__).resolve().parent / 'orchestrator_trigger_rules.txt')

# Ceilings on the scoring futures, so a hung model can never delay the trade
# decision. Not gate thresholds — these stay in code.
KW_SCORE_WAIT_SEC    = 10        # kw future (~4 ms typical)
FLANG_SCORE_WAIT_SEC = 10        # FLANG future (~6.6 ms measured)
EARN_SCORE_WAIT_SEC  = 10        # earnings future

# ⚠ A SCORING-path constant, NOT a gate threshold. _classify_and_score_earnings
# skips the earnings extractor entirely unless FineClass is this, which is why
# EarningScore is None on ~81% of rows (and prints `NA`, not `MISSING`). That skip
# must keep working even if the rules file never mentions Earnings_Results, so this
# is deliberately not derived from the rules. The GATE's use of the class lives in
# orchestrator_trigger_rules.txt.
EARN_CLASS = 'Earnings_Results'

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
# The three headline models (FinBERT + FinMarBa + FLANG-BERT) are the
# trade-critical path: a burst of ~15 PRs inside 1s (observed 2026-07-02) clears in
# ~4 waves on 4 threads. Each model gets its OWN pool so they run simultaneously
# (all are ONNX INT8 → inference releases the GIL, so three threads = genuine
# parallel execution on separate cores) and no model can starve another. For a
# single article the critical path uses just one thread from each pool at once; the
# three 4-wide pools only co-saturate under simultaneous bursts, tolerable for
# ~7-12 ms INT8 inferences. Note all three sessions use ORT's default intra-op
# pool, so on this 8-thread box they mostly time-share rather than truly overlap
# (measured ~20 ms for the FinBERT+FinMarBa pair; expect ~30 ms for the trio) —
# still trivial against the clerk-arm round-trip, which dominates.
# The keyword scorer is a few ms/PR (two tfidf predict_proba calls),
# so ONE worker absorbs the worst observed burst and sidesteps any sklearn
# thread-safety question.
FINBERT_HEADLINE_WORKERS  = 4     # threads sharing the preloaded FinBERT model
FINMARBA_HEADLINE_WORKERS = 4     # threads sharing the preloaded FinMarBa model
FLANG_HEADLINE_WORKERS    = 4     # threads sharing the preloaded FLANG-BERT model
KW_SCORE_WORKERS          = 1     # single worker on purpose — see above
COLLECT_WORKERS           = 8     # per-ticker collectors (resolve futures + TSV)
CLERK_WORKERS             = 6     # STEP-1a arms; a touch above clerk's 5-client pool
# PR classifier + earnings scorer: pure-Python regex, so it HOLDS the GIL and cannot
# truly overlap the other pools (unlike the ONNX models). 2 workers is not about
# throughput — it just stops one long body from head-of-line-blocking the next PR.
EARN_SCORE_WORKERS        = 2
# ─────────────────────────────────────────────────────────────────────────────

DEDUP_MAX_IDS = 50_000  # FIFO cap on the per-day seen-article-ID stash (leak guard)
# FIFO cap on the content-fingerprint stash (leak guard). The key is per-TICKER
# since 2026-08-12, so an article consumes up to MAX_TICKERS_PER_ARTICLE entries
# rather than 1 — doubled from 10_000 to keep the original day's headroom.
CONTENT_DEDUP_MAX_KEYS = 20_000

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

# The two corrected-label ge15 sklearn pipelines — loaded once in main() before
# the feed connects, then only read by the kwscore worker.
_kw15_body_pipe = None
_kw15_head_pipe = None

# Calibrated earnings-score params (earn_score_params.json), loaded ONCE in main().
# ⚠ Must always be passed explicitly as `params=` — earn_score_metrics(feats) with no
# params calls load_params() internally, which does os.path.exists + json.load on
# EVERY call, i.e. a disk read per PR on the trade-critical path.
_earn_params = None

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
# ID dedup above misses it. key = (normalized headline, ONE ticker) →
# arrival_dt of first sighting; any same-day hit drops that ticker, and an
# article whose every ticker is a hit is dropped whole. Same lock-free
# single-thread + per-day-clear + insertion-order-FIFO regime as _seen_ids.
#
# ⚠ The key was (normalized headline, sorted ticker TUPLE) until 2026-08-12,
# when four re-publishes fired the gate twice in one day (NEXR, BBLG, FABC,
# EXYN — three of them opening two clerk sessions on the same symbol). The
# headline always matched; the TICKER LIST did not, because the re-publish
# adds or drops the warrant (NEXR / NEXR+NEXRW, BBLG / BBLG+BBLGW) or a
# co-mentioned partner (FABC / FABC+KOPN). Whole-article keying can never see
# those as the same article; per-ticker keying does, because the ticker that
# matters is in both copies. It also handles the partner case exactly right —
# the re-publish's FABC is dropped while KOPN, already handled on the first
# copy, is left alone.
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
# Third headline model (2026-07-29) — own pool again, so all three start at
# reception and run concurrently rather than queueing behind one another.
_flang_executor = ThreadPoolExecutor(max_workers=FLANG_HEADLINE_WORKERS,
                                     thread_name_prefix='flang')
_collect_executor = ThreadPoolExecutor(max_workers=COLLECT_WORKERS,
                                       thread_name_prefix='collect')

# Keyword/combo body scorer — single worker (see KW_SCORE_WORKERS note above).
_kw_executor = ThreadPoolExecutor(max_workers=KW_SCORE_WORKERS,
                                  thread_name_prefix='kwscore')

# PR classifier + earnings scorer (2026-08-02) — its own pool so a long earnings body
# never queues behind the keyword scorer, which sits on the same critical path.
_earn_executor = ThreadPoolExecutor(max_workers=EARN_SCORE_WORKERS,
                                    thread_name_prefix='earnscore')

_tsv_lock = threading.Lock()

# Dedicated pool for clerk STEP-1a arms so arming is never starved by the collect
# tasks.
_clerk_executor = ThreadPoolExecutor(max_workers=CLERK_WORKERS, thread_name_prefix='clerk')


# ─── Analysis functions ───────────────────────────────────────────────────────

def analyze_finbert(news_dict: dict) -> dict:
    """FinBERT headline sentiment analysis. Runs once per article, in parallel
    with analyze_finmarba (separate thread pools, ONNX inference releases the GIL).

    This is `Prosus` in the unified column convention (ProsusAI/finbert) — the one
    headliner living outside tools/Encoder_Models/, and the one whose TSV columns
    were UNPREFIXED until 2026-08-09."""
    result = analyze_headline(news_dict['Headline'])
    logger.info(
        f"[FinBERT] {news_dict['Symbol']} → {result['lab'].upper()} "
        f"(score={result['ss']:.4f})"
    )
    return {
        'pos': result['pos'],
        'neg': result['neg'],
        'neu': result['neu'],
        'ss':  result['ss'],
        'lab': result['lab'],
    }


def analyze_finmarba(news_dict: dict) -> dict:
    """FinMarBa (baptle/FinBERT_market_based) headline sentiment. Runs once per
    article, in parallel with analyze_finbert. Its `ind` value is the second
    condition of the headline trade leg; the full distribution is stored in the TSV
    FinMarBa_*_hd columns for offline re-analysis."""
    result = score_finmarba_headline(news_dict['Headline'])
    logger.info(
        f"[FinMarBa] {news_dict['Symbol']} → {result['lab'].upper()} "
        f"(indecisive={result['ind']:.4f})"
    )
    return {
        'pos': result['pos'],
        'ind': result['ind'],
        'neg': result['neg'],
        'ss':  result['ss'],
        'lab': result['lab'],
    }


def analyze_flang(news_dict: dict) -> dict:
    """FLANG-BERT (avinasht/FLANG-BERT_flang-bert) headline sentiment. Runs once
    per article, in parallel with the other two headline models. Its
    `sentiment_score` (positive − negative) reaches the gate as the rules-file field
    `FLANG_score`; the whole distribution is stored in the TSV FLANG_* columns for
    offline re-analysis.

    Scores EXACTLY news_dict['Headline'], the same string the other two headline
    models get, so the live value equals an offline HeadScore11encoders rescore of
    the TSV's Headline column (where the threshold was calibrated)."""
    result = score_flang_headline(news_dict['Headline'])
    logger.info(
        f"[FLANG] {news_dict['Symbol']} → {result['lab'].upper()} "
        f"(score={result['ss']:.4f}) in {result['ms']:.1f}ms"
    )
    return {
        'pos': result['pos'],
        'neu': result['neu'],
        'neg': result['neg'],
        'ss':  result['ss'],
        'lab': result['lab'],
        # `ms` is uniform across all three headline models since 2026-08-09; this
        # module used to be the odd one out, naming its timing key `inference_ms`.
        'ms':  result['ms'],
    }


_KW_NONE_RESULT = {'kw15_proba_bd': None, 'kw15_proba_hd': None,
                   'kw15_at': None}


def _kw15_prep(text: str, max_words: int | None) -> str:
    """The EXACT training text prep, in the order keyword_scorer-2.score_texts
    uses it: strip HTML, strip wire boilerplate, then optionally cap. The corpus
    capped BEFORE stripping; verified 2026-07-28 that the order is immaterial
    (max |Δproba| 0.014, zero fire-decision flips over 777 VAL rows)."""
    out = strip_wire_boilerplate(kw2._strip_html(text or ''))
    return ' '.join(out.split()[:max_words]) if max_words else out


def _score_keywords(news_dict: dict) -> dict:
    """The two corrected-label ge15 model_proba scores — body and headline — which
    together form the keyword trade leg. Runs once per article on the kwscore
    worker. Never raises: any error returns the all-None dict. ⚠ Since 2026-08-01 the
    gate is an AND, so that no longer just drops one leg — it blocks EVERY trade until
    the scorer recovers, which is why _evaluate_trigger flags `missing_blocked` and the
    caller escalates it to WARNING.

    Scores EXACTLY the string that lands in the TSV Body cell
    (clean_inline_text(body_raw)) — NOT the connector-cleaned article body —
    so the live number equals what an offline rescore of the same TSV produces
    (all backtests / walk-forward truth were computed on the Body column). An
    empty body is scored anyway, to stay consistent with offline rescoring."""
    news_id = news_dict.get('ID')
    try:
        body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
        headline  = news_dict['Headline']
        t0 = time.monotonic()
        # One batched call per model; predict_proba on a 1-row list is the same
        # code path score_texts uses.
        body_p = _kw15_body_pipe.predict_proba(
            [_kw15_prep(body_cell, KW15_BODY_MAX_WORDS)])[0][1]
        head_p = _kw15_head_pipe.predict_proba(
            [_kw15_prep(headline, None)])[0][1]
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        # numpy float64 → plain float; 6 decimals matches the backtest TSVs.
        body_p = float(round(float(body_p), 6))
        head_p = float(round(float(head_p), 6))
        logger.info(f"[KwScore] id={news_id} sym={news_dict.get('Symbol')}: "
                    f"kw15_proba_bd={body_p:.6f} kw15_proba_hd={head_p:.6f} "
                    f"in {elapsed_ms:.1f}ms")
        return {
            # `_bd`/`_hd` put the scored channel in the same terminal position as
            # every encoder column, so the rules file and the TSV share one name.
            'kw15_proba_bd': body_p,
            'kw15_proba_hd': head_p,
            'kw15_at': c3.format_millis_time(c3.now_fixed()),
        }
    except Exception as exc:
        logger.error(f"[KwScore] id={news_id}: scorer error: {exc}", exc_info=True)
        return dict(_KW_NONE_RESULT)


_EARN_NONE_RESULT = {'FineClass': None, 'EarningScore': None, 'EarnBreakdown': None}


def _classify_and_score_earnings(news_dict: dict, primary_symbol: str) -> dict:
    """Catalyst class for every PR + the earnings rule score for the ones that are
    Earnings_Results. Runs once per article on the earnscore worker. Never raises:
    any error returns None scores, which simply makes the earnings leg false — a dead
    earnings SCORER costs the new trades but never blocks the old ones.

    ⚠ That promise is why the except below KEEPS `fine` when classification already
    succeeded and only the extractor/scorer blew up. Since the class whitelist landed
    (2026-08-17) FineClass feeds a REQUIRE precondition, so returning None there would
    close the WHOLE gate — turning "the earnings scorer crashed" into "this row cannot
    trade at all", which is precisely what the sentence above rules out. A genuinely
    unknown class (the future timed out in _collect_and_log) is a different thing and
    correctly stays None.

    Scores EXACTLY the string that lands in the TSV Body cell
    (clean_inline_text(body_raw)) — the same string _score_keywords sees — so an
    offline rescore of the daily TSV reproduces the live EarningScore. That is the
    live == backtest invariant; never pass the raw payload or a differently-cleaned
    body.

    `primary_symbol` supplies the float-cap denominator for the two size-relative
    point rules (revenue$ / profit$ vs Float x LastDailyClose). It is the article's
    FIRST in-universe ticker: the score is computed once per ARTICLE like every other
    model here, so on a 2-ticker PR the second ticker inherits the first's
    denominator. Deliberate — MAX_TICKERS_PER_ARTICLE is 2 and 2-ticker PRs are rare."""
    news_id = news_dict.get('ID')
    fine = None                     # survives into the except below — see the docstring
    try:
        body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
        headline  = news_dict['Headline']
        t0 = time.monotonic()

        # Headline first, body LEAD as fallback — reproduces PrClassifier.classify_row
        # without a shim dict (news_dict's body key is `body_raw`, not Body/body).
        # The lead-only window matters: about-us / safe-harbor boilerplate deeper in
        # the text is full of false triggers.
        # headline=True unlocks PrClassifier.HEADLINE_RULES (the loose "reports
        # revenue/earnings" rule, added 2026-08-07). It MUST stay off for the body
        # call: over 1500 chars of prose that rule promotes law-firm notices,
        # analyst coverage and cash-distribution PRs into Earnings_Results, which is
        # exactly the class this function gates a real trade on.
        fine, _broad = classify_pr(headline, headline=True)
        if fine == 'Other':
            fine, _broad = classify_pr(body_cell[:PRCLASS_BODY_LEAD_CHARS])

        if fine != EARN_CLASS:
            # ~85% of PRs — skip the extractor entirely. A non-Earnings_Results row can
            # never fire the earnings leg, so its score would be dead weight.
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            logger.info(f"[EarnScore] id={news_id} sym={news_dict.get('Symbol')}: "
                        f"class={fine} (not {EARN_CLASS}) — not scored, "
                        f"in {elapsed_ms:.1f}ms")
            return {'FineClass': fine, 'EarningScore': None, 'EarnBreakdown': None}

        # Float-cap in DOLLARS — this is what earning_scorer calls `mktcap`. Mirrors
        # its own _row_size_dollars: Float(M shares) x LastDailyClose x 1e6, falling
        # back to MktCap($M) x 1e6. The universe columns are already in millions, so
        # both 1e6 factors are exactly the M->units conversion. Done HERE on the pool
        # thread, never in _on_row_built: _universe_cell takes _iti_df_lock and does a
        # pandas mask scan, which must stay off the feed-reader thread.
        fl  = _lookup_float(primary_symbol)
        px  = _lookup_last_close(primary_symbol)
        fcap = fl * px * 1e6 if (fl and px) else None
        if not fcap:
            mc = _lookup_mktcap(primary_symbol)
            fcap = mc * 1e6 if mc else None

        feats = earn_extract_metrics(headline, body_cell, fcap)
        # These two hold every matched sentence of the PR verbatim — drop them before
        # anything can log or serialize the dict.
        feats.pop('_body_hits', None)
        feats.pop('_head_hits', None)
        score, breakdown = earn_score_metrics(feats, params=_earn_params)
        score = int(score)
        # One-lined defensively: the breakdown is a space-joined token string like
        # "revG:+3(60%) rec:+1 prof/fcap:+1(0.34)", but it lands in a TSV cell.
        breakdown = ' '.join(str(breakdown).split())
        elapsed_ms = (time.monotonic() - t0) * 1000.0
        logger.info(f"[EarnScore] id={news_id} sym={news_dict.get('Symbol')}: "
                    f"class={fine} EarningScore={score} "
                    f"(fcap={'None' if fcap is None else f'{fcap:.3e}'} "
                    f"via {primary_symbol}) [{breakdown}] in {elapsed_ms:.1f}ms")
        return {
            'FineClass':     fine,
            'EarningScore':  score,
            'EarnBreakdown': breakdown,
        }
    except Exception as exc:
        logger.error(f"[EarnScore] id={news_id}: scorer error: {exc}", exc_info=True)
        # KEEP the class if classification already got that far — it is a separate step
        # from scoring, and since 2026-08-17 FineClass feeds a REQUIRE precondition, so
        # wiping it here would close the whole gate on a row CORE would have traded.
        # `fine` is None only when classify_pr itself failed, which IS an unknown class.
        return {'FineClass': fine, 'EarningScore': None, 'EarnBreakdown': None}


# ─── Trigger rules: the file format, the field table, and the parser ──────────
#
# The gate itself is data. Everything between here and _evaluate_trigger exists to
# turn orchestrator_trigger_rules.txt into a tuple of _Rule, ONCE at startup, so
# that the per-row gate is dict lookups and pre-resolved callables — no parsing, no
# string dispatch and no threshold formatting on the trade-critical path.
#
# NO eval(). A rule line is split into (field, op, value) triples that are checked
# against the whitelists below; an operator is resolved to a callable at load time.
# There is no code path that executes text from the file. (Two of those callables are
# module-level defs rather than `operator` builtins — see _op_in. The guarantee never
# rested on their being builtins: it rests on the operator being looked up in a CLOSED
# whitelist keyed by the literal text, and the callable being bound ONCE at parse time.)


def _op_in(value, choices) -> bool:
    """`value in choices`, in the argument order every other _OPS entry uses.

    `operator.contains(a, b)` is `b in a` — reversed — so this thin wrapper is what
    keeps _cond_true a single uniform `cond.fn(lhs, cond.rhs)` call with no
    per-operator branch on the trade-critical path. `choices` is the frozenset built
    at PARSE time; this does no parsing and no allocation."""
    return value in choices


def _op_not_in(value, choices) -> bool:
    return value not in choices


_OPS = {'>=': operator.ge, '<=': operator.le, '>': operator.gt,
        '<': operator.lt, '==': operator.eq, '!=': operator.ne,
        'in': _op_in, 'not in': _op_not_in}
# The relation that is TRUE of a row that FAILED its condition — what the Trigger
# cell prints on a FAIL. Same key set as _OPS deliberately: an operator added to one
# and not the other is a KeyError at load, never a silently wrong Trigger cell.
_OP_NEG = {'>=': '<', '<': '>=', '<=': '>', '>': '<=', '==': '!=', '!=': '==',
           'in': 'not in', 'not in': 'in'}
# How an operator RENDERS inside a Trigger token. Identity for the six comparisons —
# they are single symbols and juxtapose unambiguously (`0.9100>=0.80`). The two WORD
# operators cannot: `MA in {...}` would put spaces inside a token, and tokens are
# space-separated. Same key set as the two above, for the same reason those two share
# one. (ASCII alternative, if a terminal ever mangles these: '~' / '!~'.)
_OP_CELL = {'>=': '>=', '<=': '<=', '>': '>', '<': '<', '==': '==', '!=': '!=',
            'in': '∈', 'not in': '∉'}          # ∈ / ∉
# The shared-key-set promise above, enforced instead of merely asserted. `raise`, not
# `assert`: python -O strips asserts, and this is the guard that stops a half-added
# operator from rendering a wrong Trigger cell.
if not (_OPS.keys() == _OP_NEG.keys() == _OP_CELL.keys()):
    raise RuntimeError(
        "_OPS / _OP_NEG / _OP_CELL must share one operator key set — "
        f"{sorted(_OPS)} / {sorted(_OP_NEG)} / {sorted(_OP_CELL)}")

_KIND_NUM = 'float'   # the six comparisons; renders 4 dp. `in`/`not in` REJECTED.
_KIND_INT = 'int'     # the six comparisons; renders as an integer, REJECTS 0.4
_KIND_STR = 'str'     # == != in `not in` only, case-insensitive; renders bare


class _FieldSpec(NamedTuple):
    """One addressable field: where its value comes from and how it behaves."""
    kind:    str            # _KIND_NUM / _KIND_INT / _KIND_STR
    source:  str            # which dict _evaluate_trigger reads it out of
    key:     str            # the key inside that dict
    lo:      float          # documented range, for the out-of-range WARN only
    hi:      float          # (None disables the check)
    alarm:   bool           # does a None here arm the dead-scorer WARNING?
    na_when: object = None  # fn(vals) -> True when absence is BY DESIGN, not a fault


class _Cond(NamedTuple):
    """One compiled `<field> <op> <value>`. `rhs` is coerced and `thr_str` rendered
    at PARSE time, and `fn` is resolved at PARSE time, so evaluating this per row is
    one dict lookup plus one call."""
    field:   str    # canonical name — ALSO the Trigger-cell token name
    op:      str
    rhs:     object  # float | int | lowercased str | frozenset[str] (in / not in)
    thr_str: str     # threshold as the Trigger cell should print it
    val_fmt: str     # '.4f' | 'd' | None(bare str), from spec.kind
    fn:      object  # _OPS[op]
    spec:    _FieldSpec


# A rule's KIND. An ordinary rule is one of the OR-ed branches: when its conditions all
# hold it FIRES the clerk. A REQUIRE rule is a PRECONDITION — it never fires anything,
# it only PERMITS, and every one of them is AND-ed over the whole gate. So:
#     gate = (every REQUIRE rule holds) AND (any one ordinary rule holds)
_RULE_FIRE    = 'fire'
_RULE_REQUIRE = 'require'
# What a rule GROUP prints as its verdict in the Trigger cell, per kind. A REQUIRE
# group deliberately does NOT print YES/NO: a REQUIRE rule that holds has not fired
# anything, and reusing YES would compound the misreading this kind already introduces
# (a `CORE:YES` group can now sit in a cell whose OVERALL verdict is NO — see
# _evaluate_trigger). BLOCK is also the grep handle for "why did this not trade?":
#     grep -c ':BLOCK\[' tables/news_output_*.tsv
_RULE_VERDICT = {_RULE_FIRE:    ('YES',   'NO'),
                 _RULE_REQUIRE: ('ALLOW', 'BLOCK')}


class _Rule(NamedTuple):
    name:   str     # lands in the TSV ClerkSentiment cell — FIRE rules only
    conds:  tuple   # tuple[_Cond, ...] — AND-ed, file order preserved
    lineno: int
    text:   str     # rebuilt FROM conds for the [Gate] ACTIVE echo
    kind:   str = _RULE_FIRE   # _RULE_FIRE | _RULE_REQUIRE. LAST, and defaulted, so
                               # the 4-positional construction still works.


def _earnscore_na(vals: dict) -> bool:
    """True when a missing EarningScore is BY DESIGN rather than a fault.

    _classify_and_score_earnings skips the extractor entirely unless FineClass is
    EARN_CLASS, so EarningScore is None on ~81% of rows (measured: 315/390 on the
    2026-08-03..08-07 tape). That absence is not a dead scorer, so it prints
    `EarningScore:NA`, never MISSING, and never arms the BLOCKED-BY-MISSING alarm —
    otherwise the one row where the scorer really died would be buried under three
    hundred innocent ones.

    A None FineClass is NOT NA: that means the classifier itself failed, which is
    exactly what the alarm is for."""
    fc = vals.get('FineClass')
    return fc is not None and str(fc).strip().lower() != EARN_CLASS.lower()


# Every field a rule may name. Adding a model output here is all it takes to make it
# addressable from the rules file — there is deliberately no second table to update
# (the Trigger cell prints these same names, so a token can be pasted back into a
# rule). `alarm` is False for the row fields: a symbol missing from today's universe
# TSV is a universe gap, not a dead model, and must not raise the dead-scorer alarm.
#
# Since 2026-08-09 these names ARE the daily TSV column names, under the unified
# `{Model}_{channel}_{hd|bd}` convention shared with the offline batch scorers in
# tools/Encoder_Models/. That is what retired the old _TSV_ALIASES table: there is no
# longer a TSV spelling and a rules spelling to translate between, so a column read
# off a header can be pasted straight into a rule.
_TRIGGER_FIELDS = {
    # ProsusAI FinBERT headline  (`Prosus` in the unified column convention)
    'Prosus_pos_hd':     _FieldSpec(_KIND_NUM, 'finbert', 'pos', 0.0,  1.0, True),
    'Prosus_neu_hd':     _FieldSpec(_KIND_NUM, 'finbert', 'neu', 0.0,  1.0, True),
    'Prosus_neg_hd':     _FieldSpec(_KIND_NUM, 'finbert', 'neg', 0.0,  1.0, True),
    'Prosus_ss_hd':      _FieldSpec(_KIND_NUM, 'finbert', 'ss',  -1.0, 1.0, True),
    'Prosus_lab_hd':     _FieldSpec(_KIND_STR, 'finbert', 'lab', None, None, True),
    # FinMarBa headline — `ind` is NOT a neutral probability: a low value means
    # confident in EITHER direction, which is why no rule may use it alone.
    'FinMarBa_pos_hd':   _FieldSpec(_KIND_NUM, 'fmb', 'pos', 0.0,  1.0, True),
    'FinMarBa_ind_hd':   _FieldSpec(_KIND_NUM, 'fmb', 'ind', 0.0,  1.0, True),
    'FinMarBa_neg_hd':   _FieldSpec(_KIND_NUM, 'fmb', 'neg', 0.0,  1.0, True),
    'FinMarBa_ss_hd':    _FieldSpec(_KIND_NUM, 'fmb', 'ss',  -1.0, 1.0, True),
    'FinMarBa_lab_hd':   _FieldSpec(_KIND_STR, 'fmb', 'lab', None, None, True),
    # FLANG-BERT headline
    'FLANG_pos_hd':      _FieldSpec(_KIND_NUM, 'flang', 'pos', 0.0,  1.0, True),
    'FLANG_neu_hd':      _FieldSpec(_KIND_NUM, 'flang', 'neu', 0.0,  1.0, True),
    'FLANG_neg_hd':      _FieldSpec(_KIND_NUM, 'flang', 'neg', 0.0,  1.0, True),
    'FLANG_ss_hd':       _FieldSpec(_KIND_NUM, 'flang', 'ss',  -1.0, 1.0, True),
    'FLANG_lab_hd':      _FieldSpec(_KIND_STR, 'flang', 'lab', None, None, True),
    # ge15 keyword pipelines
    'kw15_proba_bd':     _FieldSpec(_KIND_NUM, 'kw', 'kw15_proba_bd', 0.0, 1.0, True),
    'kw15_proba_hd':     _FieldSpec(_KIND_NUM, 'kw', 'kw15_proba_hd', 0.0, 1.0, True),
    # PR classifier + earnings scorer
    'FineClass':         _FieldSpec(_KIND_STR, 'earn', 'FineClass',    None, None, True),
    'EarningScore':      _FieldSpec(_KIND_INT, 'earn', 'EarningScore', -20,  20,   True,
                                    _earnscore_na),
    # The row itself (this ticker, from today's universe TSV)
    'Float':             _FieldSpec(_KIND_NUM, 'row', 'Float',             0.0, None, False),
    'MktCap':            _FieldSpec(_KIND_NUM, 'row', 'MktCap',            0.0, None, False),
    'LastDailyClose':    _FieldSpec(_KIND_NUM, 'row', 'LastDailyClose',    0.0, None, False),
    'itiBaseline':       _FieldSpec(_KIND_NUM, 'row', 'itiBaseline',       0.0, None, False),
    'tradeSizeBaseline': _FieldSpec(_KIND_NUM, 'row', 'tradeSizeBaseline', 0.0, None, False),
    'Exchange':          _FieldSpec(_KIND_STR, 'row', 'Exchange',    None, None, False),
    'Symbol':            _FieldSpec(_KIND_STR, 'row', 'Symbol',      None, None, False),
    'TickerCount':       _FieldSpec(_KIND_INT, 'row', 'TickerCount', 1,    2,    False),
}

# Names used by builds BEFORE the 2026-08-09 column unification, kept ONLY to make
# the error message better when someone pastes a rule out of an old rules file or an
# old TSV header. They are never accepted as valid input: one name per field is what
# lets a Trigger token be pasted back into a rule unchanged.
_LEGACY_FIELD_NAMES = {
    'kw15_body':             'kw15_proba_bd',
    'kw15_head':             'kw15_proba_hd',
    'kw15_body_proba':       'kw15_proba_bd',
    'kw15_head_proba':       'kw15_proba_hd',
    'FLANG_score':           'FLANG_ss_hd',
    'FLANG_sentiment_score': 'FLANG_ss_hd',
    'FLANG_positive':        'FLANG_pos_hd',
    'FLANG_neutral':         'FLANG_neu_hd',
    'FLANG_negative':        'FLANG_neg_hd',
    'FLANG_label':           'FLANG_lab_hd',
    'FMB_score':             'FinMarBa_ss_hd',
    'FMB_sentiment_score':   'FinMarBa_ss_hd',
    'FMB_indecisive':        'FinMarBa_ind_hd',
    'FMB_positive':          'FinMarBa_pos_hd',
    'FMB_negative':          'FinMarBa_neg_hd',
    'FMB_label':             'FinMarBa_lab_hd',
    'FinBERT_positive':      'Prosus_pos_hd',
    'FinBERT_neutral':       'Prosus_neu_hd',
    'FinBERT_negative':      'Prosus_neg_hd',
    'FinBERT_score':         'Prosus_ss_hd',
    'FinBERT_label':         'Prosus_lab_hd',
    'positive':              'Prosus_pos_hd',
    'negative':              'Prosus_neg_hd',
    'neutral':               'Prosus_neu_hd',
    'sentiment_score':       'Prosus_ss_hd',
    'label':                 'Prosus_lab_hd',
}

# `REQUIRE` may be spelled in any case and separated from the rule name by any run of
# whitespace, exactly like `AND`. It is stripped BEFORE the ':' partition, so every
# downstream name check (alnum, no underscore, reserved, duplicate) still sees a bare
# name and needs no special case. The `\s+` matters: `REQUIRE:` with no space is NOT a
# prefix — it is a rule NAMED require, which _RESERVED_RULE_NAMES rejects rather than
# letting it parse as an ordinary rule that merely looks like a precondition.
# The `|$` catches a bare `REQUIRE` line so it gets the dedicated "nothing after it"
# error rather than a confusing "found no ':'". Neither alternative matches a rule
# genuinely named e.g. `REQUIREMENT:`, which still parses as an ordinary rule.
_REQUIRE_PREFIX_RE = re.compile(r'^REQUIRE(?:\s+|$)', re.IGNORECASE)

_RESERVED_RULE_NAMES = {'bad', 'skipped', 'require'}

# The compiled gate. Assigned ONCE by _load_trigger_rules() from main(), before any
# worker thread exists, and never mutated after — same publication regime as
# _earn_params / the kw pipelines, so the pools read them without a lock.
_trigger_rules:  tuple = ()   # tuple[_Rule, ...] — OR-ed
_trigger_fields: tuple = ()   # tuple[(name, _FieldSpec)] — the DISTINCT fields read


def _parse_trigger_rules(path: str) -> tuple:
    """orchestrator_trigger_rules.txt -> tuple[_Rule, ...].

    Raises ValueError, message prefixed `<basename>:<lineno>: `, on ANYTHING wrong:
    there is no partial parse and no skipped line. The caller turns that into a
    RuntimeError that stops the process — a typo must never silently narrow the gate.
    """
    fname = os.path.basename(path)

    def err(msg: str, lineno=None) -> ValueError:
        where = f"{fname}:{lineno}" if lineno else fname
        return ValueError(f"{where}: {msg}")

    try:
        raw_lines = Path(path).read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        raise err(f"not found at {path} — the trade gate has no rules") from None
    except Exception as exc:
        raise err(f"cannot read {path}: {exc}") from None

    rules, seen = [], {}
    for lineno, raw in enumerate(raw_lines, 1):
        line = raw.split('#', 1)[0].strip()
        if not line:
            continue

        kind = _RULE_FIRE
        m = _REQUIRE_PREFIX_RE.match(line)
        if m:
            kind = _RULE_REQUIRE
            line = line[m.end():].strip()
            if not line:
                raise err("'REQUIRE' with nothing after it — write "
                          "'REQUIRE <RuleName>: <cond> AND <cond> ...'", lineno)

        name, sep, body = line.partition(':')
        if not sep:
            shape = ("'REQUIRE <RuleName>: <cond> AND <cond> ...'"
                     if kind == _RULE_REQUIRE else
                     "'<RuleName>: <cond> AND <cond> ...'")
            raise err(f"expected {shape}, found no ':' in {line!r}", lineno)
        name = name.strip()
        if not name:
            raise err("empty rule name before ':'", lineno)
        if not name.isalnum() or not name[0].isalpha():
            raise err(f"rule name {name!r} must be letters and digits only and start "
                      f"with a letter. Fired rule names are joined with '_' into the "
                      f"TSV ClerkSentiment cell, so a '_' in a name would make that "
                      f"cell ambiguous (the same spelling rule applies to REQUIRE "
                      f"names, so every group name in the Trigger cell reads alike)",
                      lineno)
        if name.lower() in _RESERVED_RULE_NAMES:
            raise err(f"rule name {name!r} is reserved (ClerkSentiment writes 'BAD' "
                      f"when no rule fires, and 'skipped:*' when the ticker was never "
                      f"armed; 'REQUIRE <Name>: ...' — with a space — marks a "
                      f"precondition)", lineno)
        if name.lower() in seen:
            raise err(f"duplicate rule name {name!r} (first defined on line "
                      f"{seen[name.lower()]}). Ordinary and REQUIRE rules share ONE "
                      f"name space: both print as a group in the Trigger cell",
                      lineno)

        body = body.strip()
        if not body:
            raise err(f"rule {name!r} has no conditions after ':'", lineno)
        # A trailing 'AND' leaves no whitespace after it, so the split below cannot
        # see it and the last condition would fail with a confusing "needs a number".
        # (A LEADING or DOUBLED 'AND' is caught in _parse_condition, where the stray
        # token lands in the field position.)
        if re.search(r'\s+AND$', body, flags=re.IGNORECASE):
            raise err(f"rule {name!r} ends with a trailing 'AND' — every 'AND' must "
                      f"join two conditions", lineno)

        conds = []
        for chunk in re.split(r'\s+AND\s+', body, flags=re.IGNORECASE):
            chunk = chunk.strip()
            if not chunk:
                raise err(f"rule {name!r} has an empty condition (check for a stray "
                          f"or doubled 'AND')", lineno)
            conds.append(_parse_condition(chunk, name, lineno, err, fname))

        text = ' AND '.join(f"{c.field} {c.op} {c.thr_str}" for c in conds)
        rules.append(_Rule(name, tuple(conds), lineno, text, kind))
        seen[name.lower()] = lineno

    if not rules:
        raise err("no rules found (every line is blank or a comment) — the gate "
                  "would never fire")
    if not any(r.kind == _RULE_FIRE for r in rules):
        raise err(f"only REQUIRE rule(s) found "
                  f"({', '.join(r.name for r in rules)}) — a REQUIRE line is a "
                  f"PRECONDITION: it permits, it never fires. The gate needs at least "
                  f"one ordinary '<RuleName>: <cond> ...' line or nothing can trade")
    return tuple(rules)


def _parse_condition(chunk: str, rule_name: str, lineno: int, err, fname: str) -> _Cond:
    """One `<field> <op> <value>` -> _Cond. Splits on the first two runs of
    whitespace only, so a STRING value may contain spaces."""
    parts = chunk.split(None, 2)
    if len(parts) != 3:
        raise err(f"expected '<field> <op> <value>', got {chunk!r}", lineno)
    field, op, val = parts[0], parts[1], parts[2].strip()

    # A doubled or leading 'AND' survives the split above (the second one has no
    # whitespace before it, so it is not a separator) and lands here in the FIELD
    # position. Say what actually happened rather than "unknown field 'AND'".
    if field.upper() == 'AND':
        raise err(f"rule {rule_name!r} has an empty condition (check for a stray "
                  f"or doubled 'AND')", lineno)

    # `not in` is the only TWO-WORD operator, and the split above cannot see it: the
    # operator position is always the second whitespace token, so `not` lands there
    # with the real `in` at the head of the value. Glue it back BEFORE _OPS is
    # consulted — otherwise the difflib suggester below would offer `!=` for `not`.
    # `in` / `not in` are case-folded to their canonical spelling exactly as `AND` is,
    # so _OPS, _Rule.text and the Trigger cell all speak one spelling. Note the elif
    # rather than a blanket .lower(): a typo like `Ge` must still reach the error
    # message spelled the way the human typed it.
    if op.lower() == 'not':
        rest = val.split(None, 1)
        if len(rest) != 2 or rest[0].lower() != 'in':
            raise err(f"unknown operator 'not' for field {field!r} — the only "
                      f"two-word operator is 'not in'", lineno)
        op, val = 'not in', rest[1].strip()
    elif op.lower() == 'in':
        op = 'in'

    spec = _TRIGGER_FIELDS.get(field)
    if spec is None:
        modern = _LEGACY_FIELD_NAMES.get(field)
        if modern:
            raise err(f"unknown field {field!r} — did you mean {modern!r}? "
                      f"({field!r} is a pre-2026-08-09 name; field names now follow "
                      f"the unified {{Model}}_{{channel}}_{{hd|bd}} column "
                      f"convention)", lineno)
        near = difflib.get_close_matches(field, list(_TRIGGER_FIELDS), n=3, cutoff=0.4)
        if near:
            raise err(f"unknown field {field!r} — did you mean: "
                      f"{', '.join(near)}?", lineno)
        raise err(f"unknown field {field!r} — no similar field name; see the "
                  f"'Fields' table in the header of this file for the full list",
                  lineno)

    if op not in _OPS:
        near = difflib.get_close_matches(op, list(_OPS), n=1, cutoff=0.3)
        hint = f" — did you mean {near[0]!r}?" if near else ""
        raise err(f"unknown operator {op!r} for field {field!r}{hint}. Valid "
                  f"operators: {', '.join(_OPS)}", lineno)

    if spec.kind == _KIND_STR:
        if op in ('in', 'not in'):
            return _parse_in_list(field, op, val, lineno, err, spec)
        if op not in ('==', '!='):
            raise err(f"operator {op!r} is not allowed on string field {field!r} — "
                      f"string fields support ==, !=, in and not in", lineno)
        if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
            val = val[1:-1].strip()
        if not val:
            raise err(f"field {field!r} has an empty value after {op!r}", lineno)
        # rhs lowercased for a case-insensitive compare; thr_str keeps what was typed.
        return _Cond(field, op, val.lower(), val, None, _OPS[op], spec)

    # `in` / `not in` are set membership over STRING values, and stay that way. On a
    # numeric field they would need a second render format and a float-equality story
    # for no present demand; the mirror-image restriction ("string fields take only ==
    # and !=") is already this file's established shape.
    if op in ('in', 'not in'):
        raise err(f"operator {op!r} is not allowed on {spec.kind} field {field!r} — "
                  f"'in'/'not in' are set membership over STRING values. Numeric "
                  f"fields take >= <= > < == != (write a range, or a second rule)",
                  lineno)

    if spec.kind == _KIND_INT:
        if not re.fullmatch(r'[+-]?\d+', val):
            extra = ("" if field != 'EarningScore' else
                     " '>= 4' means four points; '>= 0.4' would silently have meant"
                     " '>= 1'")
            raise err(f"field {field!r} is INTEGER POINTS (observed -7..+9), got "
                      f"{val!r} — write a whole number.{extra}", lineno)
        rhs = int(val)
        thr_str, val_fmt = str(rhs), 'd'
    else:
        try:
            rhs = float(val)
        except ValueError:
            raise err(f"field {field!r} needs a number, got {val!r}", lineno) from None
        thr_str, val_fmt = _fmt_thr(rhs), '.4f'

    if spec.lo is not None and rhs < spec.lo:
        logger.warning("[Gate] %s:%d: rule %r compares %s %s %s, but %s never goes "
                       "below %s — check this threshold",
                       fname, lineno, rule_name, field, op, val, field, spec.lo)
    if spec.hi is not None and rhs > spec.hi:
        logger.warning("[Gate] %s:%d: rule %r compares %s %s %s, but %s never goes "
                       "above %s — check this threshold",
                       fname, lineno, rule_name, field, op, val, field, spec.hi)

    return _Cond(field, op, rhs, thr_str, val_fmt, _OPS[op], spec)


def _parse_in_list(field: str, op: str, val: str, lineno: int, err, spec) -> _Cond:
    """`<field> in a, b, c` -> a _Cond whose rhs is a frozenset of lowercased values.

    The list is comma-separated. Surrounding braces are OPTIONAL on input and always
    present on output, so the `[Gate] ACTIVE` echo — which rebuilds each rule from its
    compiled conditions — renders `FineClass in {a,b,c}`, which can be pasted straight
    back into the rules file. Elements may be quoted, like any string value.

    EVERY degenerate list is a hard error rather than a silently smaller set. That is
    the whole point: each of these mistakes SHRINKS the set, and a whitelist that
    quietly lost an element is a gate nobody wrote."""
    body = val.strip()
    if len(body) >= 2 and body[0] == '{' and body[-1] == '}':
        body = body[1:-1].strip()
    if not body:
        raise err(f"field {field!r} has an empty list after {op!r} — write "
                  f"'{field} {op} <value>, <value>, ...'", lineno)

    items, seen = [], {}
    for raw in body.split(','):
        item = raw.strip()
        if len(item) >= 2 and item[0] == item[-1] and item[0] in ('"', "'"):
            item = item[1:-1].strip()
        if not item:
            raise err(f"field {field!r} has an EMPTY element in its {op!r} list "
                      f"{val!r} — check for a doubled or a trailing ','", lineno)
        if any(ch.isspace() for ch in item):
            raise err(f"field {field!r} has a list element {item!r} containing a "
                      f"space — an {op!r} list is comma-separated and its values may "
                      f"not contain whitespace. DID YOU FORGET A COMMA? (Use == for a "
                      f"single value that really does contain one.)", lineno)
        if item.lower() in seen:
            raise err(f"field {field!r} lists {item!r} twice in its {op!r} list "
                      f"(first at position {seen[item.lower()]}; the compare is "
                      f"case-insensitive, so 'MA' and 'ma' are the same value)",
                      lineno)
        seen[item.lower()] = len(items) + 1
        items.append(item)

    # thr_str is built from the ORDERED, AS-TYPED items, NEVER from the frozenset:
    # frozenset iteration order varies with PYTHONHASHSEED, which would make the
    # [Gate] ACTIVE echo and the Trigger cell differ between two processes running the
    # identical file. Braces + no spaces keep the whole rendering one token.
    thr_str = '{' + ','.join(items) + '}'
    return _Cond(field, op, frozenset(i.lower() for i in items), thr_str, None,
                 _OPS[op], spec)


def _load_trigger_rules(path: str = None) -> None:
    """Parse the rules file into the module globals. Raises RuntimeError on any
    problem — the same hard-fail class as the model pre-warm self-checks, and for the
    same reason: a trading process must not run a gate nobody wrote."""
    global _trigger_rules, _trigger_fields
    path = path or TRIGGER_RULES_FILE
    try:
        rules = _parse_trigger_rules(path)
    except ValueError as exc:
        raise RuntimeError(f"{exc} — refusing to start") from None

    # The DISTINCT fields any rule reads, in first-appearance order. _evaluate_trigger
    # extracts exactly these once per row, rather than once per condition.
    fields, order = {}, []
    for rule in rules:
        for cond in rule.conds:
            if cond.field not in fields:
                fields[cond.field] = cond.spec
                order.append(cond.field)
    # EarningScore's na_when reads FineClass, so FineClass must be extracted whenever
    # EarningScore is referenced — even by a rule that never tests it. One line here
    # is what makes the NA verdict total.
    if 'EarningScore' in fields and 'FineClass' not in fields:
        fields['FineClass'] = _TRIGGER_FIELDS['FineClass']
        order.append('FineClass')

    _trigger_rules  = rules
    _trigger_fields = tuple((n, fields[n]) for n in order)

    # A rule that reads EarningScore without pinning FineClass can effectively never
    # fire, because the scorer only ever runs on EARN_CLASS rows. Not fatal — a rule
    # may legitimately be narrower — but it is almost always a mistake.
    for rule in rules:
        names = {c.field: c for c in rule.conds}
        if 'EarningScore' not in names:
            continue
        fc = names.get('FineClass')
        # `== Earnings_Results` and `in {Earnings_Results}` pin the class equally well.
        # (The `==` arm is safe against a frozenset rhs: a str never equals a set.)
        pinned = fc is not None and (
            (fc.op == '==' and fc.rhs == EARN_CLASS.lower()) or
            (fc.op == 'in' and fc.rhs == frozenset({EARN_CLASS.lower()})))
        if not pinned:
            logger.warning(
                "[Gate] %s:%d: rule %r tests EarningScore but does not require "
                "FineClass == %s. The earnings scorer only runs on that class, so "
                "EarningScore is None on ~81%% of rows and this rule can almost "
                "never fire.", os.path.basename(path), rule.lineno, rule.name,
                EARN_CLASS)


def _log_active_gate() -> None:
    """Echo the WHOLE gate once, at startup.

    The rules are read at startup ONLY, so a process started before an edit keeps the
    OLD gate until it is restarted — and nothing else in the log says which one is
    live (the TSV header has been identical across gate changes since 2026-08-02).
    This block is the cheapest way to answer "which gate is this process running?"
    from logs/ alone; it is the reason it exists.

    Each rule is rebuilt FROM ITS COMPILED CONDITIONS, never echoed from the raw
    line, so a parse that differs from what the human meant is visible right here.

    ⚠ With no REQUIRE rule loaded this block is byte-identical to the pre-2026-08-17
    one, deliberately: that is what lets a `diff` of two --check-rules runs prove the
    REQUIRE refactor changed no behaviour."""
    fires = [r for r in _trigger_rules if r.kind == _RULE_FIRE]
    reqs  = [r for r in _trigger_rules if r.kind == _RULE_REQUIRE]
    if reqs:
        logger.info("[Gate] ACTIVE — %d PRECONDITION(s) AND (OR of %d rule(s)) from "
                    "%s (read ONCE at startup; edit + RESTART to change). A row "
                    "trades when EVERY REQUIRE line holds AND ANY ONE ordinary rule's "
                    "conditions ALL hold; a REQUIRE line that fails closes the gate "
                    "however many ordinary rules held.",
                    len(reqs), len(fires), TRIGGER_RULES_FILE)
    else:
        logger.info("[Gate] ACTIVE — OR of %d rule(s) from %s (read ONCE at startup; "
                    "edit + RESTART to change). A row trades when ANY rule's "
                    "conditions ALL hold.", len(fires), TRIGGER_RULES_FILE)
    for i, rule in enumerate(reqs, 1):
        logger.info("[Gate]   REQUIRE (%d) %s: %s", i, rule.name, rule.text)
    for i, rule in enumerate(fires, 1):
        logger.info("[Gate]   (%d) %s: %s", i, rule.name, rule.text)
    # Which loaded rules read which scorer — DERIVED, so it stays true after any edit.
    # A model that no rule reads is still scored on every PR: it burns the
    # trade-critical path for nothing, and a reader of logs/ would assume it still
    # gates trades. That is worth a WARNING.
    for src, label in (('finbert', 'FinBERT'), ('fmb', 'FinMarBa'),
                       ('flang', 'FLANG-BERT'), ('kw', 'ge15 keyword'),
                       ('earn', 'PrClassifier/earnings'), ('row', 'universe row')):
        used = sorted({f"{r.name}{' (REQUIRE)' if r.kind == _RULE_REQUIRE else ''}"
                       for r in _trigger_rules
                       for c in r.conds if c.spec.source == src})
        if used:
            logger.info("[Gate]   %s feeds rule(s): %s", label, ', '.join(used))
        elif src != 'row':
            logger.warning("[Gate]   %s is scored on EVERY PR but NO rule reads it — "
                           "it costs latency and gates nothing", label)


# ─── Trade-trigger gate ───────────────────────────────────────────────────────

def _fmt_thr(v: float) -> str:
    """A threshold parsed from orchestrator_trigger_rules.txt, as the Trigger cell
    should print it: 2 dp when that round-trips exactly, otherwise %g. So a 0.975 cut
    renders in full rather than as a misleadingly-rounded 0.97, and a retune to 0.625
    renders as 0.625.

    Called at LOAD time only — the result is cached in _Cond.thr_str, so no threshold
    is ever formatted on the per-row path."""
    s = f"{v:.2f}"
    return s if float(s) == v else f"{v:g}"


def _cond_token(name: str, value, thr_str: str, op: str, ok: bool,
                val_fmt: str = '.4f') -> str:
    """One `name:VERDICT(score op threshold)` token of the Trigger cell.

    `ok` is passed IN rather than recomputed here — that is what makes it
    impossible for the TSV cell to disagree with the gate that actually fired.
    The printed operator is always the relation that is TRUE of the row: the rule's
    own operator on a pass, its negation from _OP_NEG on a fail (so a `>=` cut prints
    `<` when it failed, and the `<=` ceiling prints `>`). A None score (model error /
    future timeout) renders as a bare MISSING, so a dead scorer never looks like a
    model that merely scored low. Scores are display-rounded to 4 dp — the PASS/FAIL
    token, not the number, is authoritative.

    `thr_str` arrives PRE-RENDERED from _Cond, which is why this function cannot be
    handed a threshold of the wrong type: coercion happens once, at parse time.
    `val_fmt` follows the field's kind — 'd' for an integer-valued field like
    EarningScore (`EarningScore:PASS(5>=4)`, not a misleading `5.0000>=4.00`), None
    for a string field, 4 dp for everything else.

    A set-membership condition renders with the ∈ / ∉ glyph and the WHOLE list, never
    truncated: `FineClass:PASS(MA∈{MA,Partnership,...})`. Truncating it would break the
    "a row stays self-describing across a retune" property this cell exists for."""
    if value is None:
        return f"{name}:MISSING"
    shown = (str(value).strip() if val_fmt is None else
             format(int(value), 'd') if val_fmt == 'd' else
             format(value, val_fmt))
    # _OP_CELL, not the raw op: `in`/`not in` are WORDS and would put spaces inside a
    # token. The relation printed is always the one that is TRUE of this row — the
    # rule's own operator on a pass, its negation on a fail.
    return (f"{name}:{'PASS' if ok else 'FAIL'}"
            f"({shown}{_OP_CELL[op if ok else _OP_NEG[op]]}{thr_str})")


def _cond_true(cond: _Cond, value) -> bool:
    """Evaluate ONE compiled condition against a NON-None value.

    Never raises. A value the operator cannot handle — an unexpected type out of a
    model, a non-numeric string where a number was promised — FAILS, exactly as a
    None does. This is the trade-critical path and it must not throw.

    A word operator (`in` / `not in`) needs no branch here: _OPS hands back a callable
    with the same (lhs, rhs) argument order as every comparison, and its rhs is the
    frozenset built at parse time — so set membership is the same single call."""
    try:
        if cond.spec.kind == _KIND_STR:
            return bool(cond.fn(str(value).strip().lower(), cond.rhs))
        return bool(cond.fn(float(value), cond.rhs))
    except (TypeError, ValueError):
        return False


def _evaluate_trigger(finbert_val: dict, finmarba_val: dict, flang_val: dict,
                      kw_val: dict, earn_val: dict, row_vals: dict,
                      rules: tuple = None) -> tuple:
    """STEP-1b trade gate. Returns `(is_ok, label, detail, missing_blocked)`.

    THE RULES ARE NOT IN THIS FILE. They are parsed from
    orchestrator_trigger_rules.txt by _load_trigger_rules() ONCE at startup into
    `_trigger_rules`. This docstring describes the MECHANISM and deliberately does
    NOT restate the rules: a docstring that repeats external thresholds is guaranteed
    to rot the first time somebody edits that file, which is the exact failure mode
    this design exists to remove. To see the gate a given process actually ran, read
    the `[Gate] ACTIVE` block in its log — the rules are read at startup, so a running
    process keeps its gate until restarted.

    Semantics:
      - Ordinary rules are OR-ed: one FIRES when its conditions ALL hold.
      - REQUIRE rules are PRECONDITIONS, AND-ed over the whole gate. They never fire
        and never reach ClerkSentiment; they only permit. So
            is_ok = (every REQUIRE rule holds) AND (any ordinary rule fires)
      - Conditions within any rule are AND-ed.
      - A `None` value ALWAYS fails its condition — never passes, never raises. On a
        REQUIRE condition that closes the WHOLE gate (fail closed), which is why the
        `missing_blocked` alarm below matters more than it used to.
      - String fields compare case-insensitively, and `in`/`not in` test membership of
        a set fixed at parse time.
    `rules` is injectable so an offline replay can score a candidate rule set without
    touching module state; it defaults to the loaded gate.

    `label` -> the TSV ClerkSentiment cell: fired rule names joined with '_', else
      'BAD'. The names come from the file, so this vocabulary is the operator's.
      A row a REQUIRE rule blocked reads 'BAD' like any other non-fire; `detail`'s
      `:BLOCK[` group is what tells the two apart.

    `detail` -> the TSV Trigger cell: `YES|NO | ` then one group per rule,
      `<RuleName>:<verdict>[<token> <token> ...]`, where the verdict is YES/NO for an
      ordinary rule and ALLOW/BLOCK for a REQUIRE rule, e.g.

        NO | ClassWhitelist:BLOCK[FineClass:FAIL(Other∉{MA,Partnership,...})]
             CORE:NO[Prosus_pos_hd:PASS(0.9100>=0.80) FinMarBa_ind_hd:FAIL(0.2>0.14)
             ...] EARNINGS:NO[FineClass:FAIL(Other!=Earnings_Results) EarningScore:NA
             kw15_proba_bd:PASS(0.5500>=0.50)]
             KwBodySolo:NO[kw15_proba_bd:FAIL(0.5500<0.70)]

      EVERY rule prints on EVERY row, including rows that fired, so the TSV stays a
      complete offline replay input — a candidate retune can be scored from tables/
      alone. Each token carries the score AND the threshold it was judged against, so
      a row stays self-describing across a retune. A token is PASS, FAIL, MISSING (the
      score was None — model error or future timeout) or NA (absent BY DESIGN; see
      _earnscore_na).

      ⚠ A `:YES` GROUP NO LONGER IMPLIES THE ROW FIRED. Before 2026-08-17 a cell
      starting `NO |` meant every group read `:NO`. Now an ordinary rule can hold while
      a REQUIRE rule blocks the gate, so `NO | ClassWhitelist:BLOCK[...] CORE:YES[...]`
      is a normal, correct cell. Read the LEADING verdict for what the row did; read
      the groups for why. Anything counting fires by grepping `:YES` is wrong.

    `missing_blocked` -> feeds a WARNING and nothing else; it can never affect
      trading. True when the row did NOT fire and some rule was blocked by a MISSING
      score ALONE: every one of that rule's evaluable conditions passed, and at least
      one alarming field was None. This one expression generalizes the three
      hand-written terms it replaced. It is exact for a multi-condition rule, and for
      a single-condition rule "all evaluable passed" is vacuous — which makes the
      alarm loud by design, because a dead model there removes an entire branch while
      the tape still looks alive. It is deliberately a STRICT SUPERSET in one case the
      old code missed: a dead PR classifier (FineClass None) on a row whose other
      earnings conditions passed now warns, where before `c_class` was False and the
      alarm stayed silent.
      ⚠ Since 2026-08-17 a REQUIRE rule arms it too, and that is the loudest case in
      the file: a dead PR classifier fails the class whitelist, which closes the whole
      gate, so this WARNING fires on EVERY row until it is fixed. That volume IS the
      signal — the alarm is now the only thing distinguishing "trading has stopped"
      from "a quiet news day".

    ⚠ Trigger-cell format history, for anyone parsing old TSVs: 5 flat tokens =
      2026-08-01, 7 = 08-02, 10 = 08-03. From 2026-08-07 the cell is rule-GROUPED and
      the presence of '[' identifies this build. Token names are now the rules-file
      field names (`Prosus_pos_hd`, not `FinBERT`) — which since 2026-08-09 are also
      the TSV column names, so a token can be pasted straight back into a rule.
      From 2026-08-17 an `ALLOW`/`BLOCK` group verdict (and the ∈/∉ glyph) identifies
      the build, and a `:YES` group can appear in a `NO` cell."""
    if rules is None:
        rules = _trigger_rules
    sources = {'finbert': finbert_val, 'fmb': finmarba_val, 'flang': flang_val,
               'kw': kw_val, 'earn': earn_val, 'row': row_vals}
    # ONE extraction pass over the DISTINCT fields any rule reads, rather than one per
    # condition — a score shared by several rules is fetched once.
    vals = {name: sources[spec.source].get(spec.key)
            for name, spec in _trigger_fields}

    fired, groups, blocked = [], [], False
    required_ok = True
    for rule in rules:
        rule_ok = all_evaluable_passed = True
        rule_missing = False
        toks = []
        for cond in rule.conds:
            value = vals.get(cond.field)
            if value is None:
                rule_ok = False
                if cond.spec.na_when is not None and cond.spec.na_when(vals):
                    # Absent BY DESIGN. Not a fault, so it does not arm the alarm and
                    # does not count as "an evaluable condition that failed".
                    toks.append(f"{cond.field}:NA")
                else:
                    toks.append(f"{cond.field}:MISSING")
                    if cond.spec.alarm:
                        rule_missing = True
                continue
            ok = _cond_true(cond, value)
            if not ok:
                rule_ok = all_evaluable_passed = False
            toks.append(_cond_token(cond.field, value, cond.thr_str, cond.op, ok,
                                    cond.val_fmt))
        if rule_ok:
            # A REQUIRE rule that holds has not FIRED anything — it has only permitted.
            # It never enters `fired`, so it never reaches ClerkSentiment.
            if rule.kind == _RULE_FIRE:
                fired.append(rule.name)
        else:
            if rule.kind == _RULE_REQUIRE:
                required_ok = False
            # Reached by BOTH kinds: a precondition blocked by a dead scorer alone is
            # exactly the case the alarm exists for, and it now stops the whole gate.
            if rule_missing and all_evaluable_passed:
                blocked = True
        yes_word, no_word = _RULE_VERDICT[rule.kind]
        groups.append(f"{rule.name}:{yes_word if rule_ok else no_word}"
                      f"[{' '.join(toks)}]")

    # THE GATE: every precondition AND any one ordinary rule.
    is_ok = required_ok and bool(fired)
    missing_blocked = (not is_ok) and blocked
    # ⚠ LOAD-BEARING since 2026-08-17, after being merely defensive from 08-07 to then.
    # `fired` holds RULE names, so is_ok == bool(fired) held by construction while every
    # rule was a firing rule. A REQUIRE rule does NOT imply a fire: a row whose CORE
    # conditions all held but whose whitelist blocked it has a NON-EMPTY `fired` and
    # is_ok False, and without this guard ClerkSentiment would read `CORE_FLANGSolo` on
    # a row that never traded — which replay_gate.py and every fired_* analysis would
    # then count as a fire. This is the "day a rule kind appears that does NOT imply a
    # fire" the pre-08-17 comment here was written for.
    # (It was load-bearing once before, too: pre-08-07 `fired` held LEG names — Keyword
    # / FinBERT_FMB / FLANG — that were sub-parts of the CORE branch and could be true
    # with nothing firing at all, on 23.8% of the 08-03..08-07 tape.)
    label = '_'.join(fired) if is_ok else 'BAD'
    return (is_ok, label,
            f"{'YES' if is_ok else 'NO'} | {' '.join(groups)}", missing_blocked)


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
    # ── Sentiment blocks. Since 2026-08-09 every score column follows the unified
    # `{Model}_{channel}_{hd|bd}` convention, IDENTICAL to what the offline batch
    # scorers in tools/Encoder_Models/ emit — so a live row and an offline rescore
    # of the same headline now share column names, not just values.
    # `_hd` = the scored text was the Headline. `_at_hd` is a COMPLETION TIMESTAMP,
    # `_ms_hd` the model's own inference DURATION — the old FLANG_time / FLANG_ms
    # pair conflated those two ideas by name.
    # ProsusAI FinBERT — these five were UNPREFIXED (`positive`, `label`, …) before
    # 2026-08-09, the last columns in the file with no model in their name.
    'Prosus_at_hd',
    'Prosus_pos_hd', 'Prosus_neg_hd', 'Prosus_neu_hd', 'Prosus_ss_hd', 'Prosus_lab_hd',
    # FinMarBa. `ind` is deliberately NOT `neu`: a low value means confident in
    # EITHER direction, so it must never be read as an ordinary neutral probability.
    'FinMarBa_at_hd',
    'FinMarBa_pos_hd', 'FinMarBa_ind_hd', 'FinMarBa_neg_hd', 'FinMarBa_ss_hd',
    'FinMarBa_lab_hd',
    # FLANG-BERT (2026-07-29). Only FLANG_ss_hd feeds the gate; the rest are for
    # offline re-analysis.
    'FLANG_at_hd',
    'FLANG_pos_hd', 'FLANG_neu_hd', 'FLANG_neg_hd', 'FLANG_ss_hd',
    'FLANG_lab_hd', 'FLANG_ms_hd',
    'ClerkSentiment',
    'kw15_proba_bd', 'kw15_proba_hd', 'kw15_at',
    # Trigger (2026-07-29) — the gate's own account of itself, built by
    # _evaluate_trigger from the same booleans the gate fires on. Since 2026-08-07 it
    # is RULE-GROUPED: YES/NO, then one `<RuleName>:<YES|NO>[<tokens>]` group per
    # loaded rule, every rule printed on every row (which is what keeps this TSV a
    # complete offline replay input). Tokens are PASS/FAIL/MISSING/NA with the score
    # and the threshold it was judged against. NOT a duplicate of ClerkSentiment: that
    # one records the fired RULES and is overwritten with 'skipped:*' when the ticker
    # was never armed, whereas Trigger is populated on EVERY row (the gate runs before
    # the arm branch) and resolves a rule into its underlying models.
    # ⚠ READING OLD TSVs: the cell was a FLAT token list until 08-07 and its token
    # COUNT identified the build (5 = 08-01, 7 = 08-02, 10 = 08-03). From 08-07 the
    # presence of '[' identifies it, and the group count follows the rules file, not
    # the release date. Gate changes have not touched this header since 08-02, so 42
    # columns spans every gate from then on.
    # ⚠ Since 2026-08-09 the token NAMES are the unified column names too
    # (`Prosus_pos_hd:PASS(...)`, not `FinBERT_positive:PASS(...)`) — which is the
    # cheapest way to tell a pre- from a post-rename row apart from the header.
    'Trigger',
    # Earnings block (2026-08-02) — appended LAST for the same reason. FineClass is the
    # PrClassifier catalyst class, written on EVERY row. EarningScore (integer POINTS,
    # not a probability) and EarnBreakdown are populated ONLY on Earnings_Results rows:
    # the scorer is skipped elsewhere, so those two cells are blank for ~85% of rows —
    # blank means "not an earnings PR", not "scored 0". EarnBreakdown names the rule
    # families that fired with their values ("revG:+3(60%) rec:+1 prof/fcap:+1(0.34)"),
    # which is what makes an EarningScore auditable without the 25 earn_* feature cols.
    'FineClass', 'EarningScore', 'EarnBreakdown',
]
# ⚠ 2026-08-09 — COLUMN COUNT UNCHANGED (42), NAMES ALL CHANGED. Every sentiment
# column was renamed to the unified `{Model}_{channel}_{hd|bd}` convention shared
# with tools/Encoder_Models/. This is the inverse of the 2026-08-07 refactor, which
# changed the gate without touching the header: here the header changes and the gate
# does not (verified: 391/391 rows of the 08-03..08-09 tape produce identical fire
# decisions and identical fired-rule labels). A file whose header still reads
# `positive` / `FMB_indecisive` / `kw15_body_proba` was written by an older build —
# migrate it with tools/Encoder_Models/rename_encoder_cols.py rather than re-scoring.
# Because the arity is identical on both sides, a half-migrated file CANNOT be caught
# by a column count: only the names distinguish them.


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
                     f_finbert, f_finmarba, f_flang, f_kw, f_earn, f_arm,
                     arm_blocked: bool) -> None:
    """Resolve the three headline models (FinBERT + FinMarBa + FLANG-BERT), the
    keyword body/headline scores and the PR class + earnings score, evaluate the trade
    gate, complete the clerk handshake (wait for the STEP-1a arm reply, then send the
    STEP-1b sentiment), and write the TSV row.

    Ordering is load-bearing: we wait on the arm future *before* sending sentiment
    so the clerk has registered the session — required both for the OK gate and for
    the BAD early-release to land. The FLANG, kw and earnings futures are resolved
    BEFORE the arm wait (adds only ms; the arm reply wait already dominates), each
    with a hard ceiling (FLANG_SCORE_WAIT_SEC / KW_SCORE_WAIT_SEC /
    EARN_SCORE_WAIT_SEC) so a hung model can't stall the trade decision. All the
    scoring futures were submitted together in _on_row_built and run in parallel, so
    resolving them back-to-back here costs ~max(model), not the sum."""
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

    # 1c) Headline FLANG-BERT (ran in parallel with both; supplies the standalone
    #     `sentiment_score` leg). A failure leaves {} → its leg is simply false.
    try:
        flang_val = f_flang.result(timeout=FLANG_SCORE_WAIT_SEC)
        flang_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FLANG-headliner error for id={news_id}: {exc}", exc_info=True)
        flang_val = {}
        flang_completed_at = None

    # 2) Keyword/combo body score (~ms; both fan-out tickers share the future)
    try:
        kw_val = f_kw.result(timeout=KW_SCORE_WAIT_SEC)
    except Exception as exc:
        logger.error(f"[KwScore] {symbol}: kw future failed/timed out for id={news_id}: {exc}")
        kw_val = dict(_KW_NONE_RESULT)

    # 2b) PR class + earnings score (~ms; both fan-out tickers share the future).
    #     A failure leaves the all-None dict → the earnings leg is simply false, and
    #     the core AND branch is unaffected.
    try:
        earn_val = f_earn.result(timeout=EARN_SCORE_WAIT_SEC)
    except Exception as exc:
        logger.error(f"[EarnScore] {symbol}: earn future failed/timed out for id={news_id}: {exc}")
        earn_val = dict(_EARN_NONE_RESULT)

    # 2c) Per-ticker universe fields, resolved ONCE. They feed BOTH the gate (row
    #     fields are addressable from orchestrator_trigger_rules.txt) and
    #     completed_dict below — each _lookup_* takes _iti_df_lock and does a pandas
    #     mask scan, so they are read here exactly once and reused rather than looked
    #     up again when the row is built. Exchange keeps its payload-first /
    #     universe-fallback order. TickerCount counts EVERY ticker the article named,
    #     not just the in-universe ones.
    #     Two harmless side effects of resolving these before the gate instead of
    #     after: _lookup_baseline_iti's "missing/invalid — using default" WARNING now
    #     prints before this row's [Gate] line, and _is_rth_now() is evaluated a few
    #     ms earlier.
    row_vals = {
        'Symbol':            symbol,
        'Float':             _lookup_float(symbol),
        'MktCap':            _lookup_mktcap(symbol),
        'Exchange':          news_dict.get('Exchange') or _lookup_exchange(symbol),
        'LastDailyClose':    _lookup_last_close(symbol),
        'itiBaseline':       _lookup_baseline_iti(symbol),
        'tradeSizeBaseline': _lookup_baseline_trade_size(symbol),
        'TickerCount':       len(tickers),
    }

    # 3) Trade gate from orchestrator_trigger_rules.txt — every REQUIRE precondition
    #    AND an OR of the ordinary rules → decision + label + Trigger cell
    is_ok, trigger_label, trigger_detail, missing_blocked = _evaluate_trigger(
        finbert_val, finmarba_val, flang_val, kw_val, earn_val, row_vals)
    # Log every gate input against its threshold, so a rejection can be diagnosed from
    # the log alone. This is the SAME string that lands in the TSV Trigger cell, so the
    # log can never drift from the row.
    if not is_ok:
        logger.info("[Gate] %s %s", symbol, trigger_detail)
        # A single dead model can block a branch while looking exactly like a quiet
        # news day — and the OR makes that quieter still, since the other branch keeps
        # trading. Escalate the case where a MISSING score is the only thing that
        # stopped this row, so logs/ shows a broken scorer instead of silence.
        if missing_blocked:
            logger.warning("[Gate] %s BLOCKED BY MISSING SCORE ONLY — a branch's "
                           "every evaluable condition passed; a model or future is "
                           "dead: %s", symbol, trigger_detail)

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
        'Float':              row_vals['Float'],
        'MktCap':             row_vals['MktCap'],
        'Exchange':           row_vals['Exchange'],
        'LastDailyClose':     row_vals['LastDailyClose'],
        'itiBaseline':        row_vals['itiBaseline'],
        'tradeSizeBaseline':  row_vals['tradeSizeBaseline'],
        'Prosus_at_hd':   finbert_completed_at,
        'Prosus_pos_hd':  finbert_val.get('pos'),
        'Prosus_neg_hd':  finbert_val.get('neg'),
        'Prosus_neu_hd':  finbert_val.get('neu'),
        'Prosus_ss_hd':   finbert_val.get('ss'),
        'Prosus_lab_hd':  finbert_val.get('lab'),
        'FinMarBa_at_hd':  fmb_completed_at,
        'FinMarBa_pos_hd': finmarba_val.get('pos'),
        'FinMarBa_ind_hd': finmarba_val.get('ind'),
        'FinMarBa_neg_hd': finmarba_val.get('neg'),
        'FinMarBa_ss_hd':  finmarba_val.get('ss'),
        'FinMarBa_lab_hd': finmarba_val.get('lab'),
        'FLANG_at_hd':  flang_completed_at,
        'FLANG_pos_hd': flang_val.get('pos'),
        'FLANG_neu_hd': flang_val.get('neu'),
        'FLANG_neg_hd': flang_val.get('neg'),
        'FLANG_ss_hd':  flang_val.get('ss'),
        'FLANG_lab_hd': flang_val.get('lab'),
        'FLANG_ms_hd':  flang_val.get('ms'),
        'kw15_proba_bd': kw_val.get('kw15_proba_bd'),
        'kw15_proba_hd': kw_val.get('kw15_proba_hd'),
        'kw15_at':       kw_val.get('kw15_at'),
        # Set here, not in the clerk branches below: the gate verdict is recorded on
        # every row even when the ticker is never armed.
        'Trigger':                     trigger_detail,
        # FineClass on every row; the other two only on Earnings_Results rows (blank
        # elsewhere = not scored, NOT scored-zero).
        'FineClass':                   earn_val.get('FineClass'),
        'EarningScore':                earn_val.get('EarningScore'),
        'EarnBreakdown':               earn_val.get('EarnBreakdown'),
    }

    # 5) Clerk STEP-1b — resolve the arm, then confirm sentiment. f_arm is None when
    #    the ticker was never armed (excluded headline, or not in the universe).
    #    ClerkSentiment encodes BOTH the trigger and the clerk reply:
    #      armed        → "<fired legs joined by '_', or BAD>:<reply status>", e.g.
    #                     "Keyword_FinBERT_FMB_FLANG:sent" (core branch), "Earnings:sent",
    #                     "KwBodySolo:sent", "FLANG_FLANGSolo:sent", "BAD:sent". The
    #                     2026-08-01 AND made this degenerate (two values); the 08-02
    #                     earnings branch and the 08-03 solo branches made it informative
    #                     again — the label names exactly which legs held.
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
    print(f"  Prosus_lab_hd                : {completed_dict['Prosus_lab_hd']}")
    print(f"  Prosus_ss_hd                 : {completed_dict['Prosus_ss_hd']}")
    print(f"  Prosus_pos_hd                : {completed_dict['Prosus_pos_hd']}")
    print(f"  Prosus_neg_hd                : {completed_dict['Prosus_neg_hd']}")
    print(f"  Prosus_neu_hd                : {completed_dict['Prosus_neu_hd']}")
    print(f"  Float                        : {completed_dict['Float']}")
    print(f"  Exchange                     : {completed_dict['Exchange']}")
    print(f"  LastDailyClose               : {completed_dict['LastDailyClose']}")
    print(f"  itiBaseline                  : {completed_dict['itiBaseline']}")
    print(f"  tradeSizeBaseline            : {completed_dict['tradeSizeBaseline']}")
    print(f"  Prosus_at_hd                 : {completed_dict['Prosus_at_hd']}")
    print(f"  FinMarBa_ind_hd              : {completed_dict['FinMarBa_ind_hd']}")
    print(f"  FinMarBa_pos_hd              : {completed_dict['FinMarBa_pos_hd']}")
    print(f"  FinMarBa_neg_hd              : {completed_dict['FinMarBa_neg_hd']}")
    print(f"  FinMarBa_lab_hd              : {completed_dict['FinMarBa_lab_hd']}")
    print(f"  FinMarBa_at_hd               : {completed_dict['FinMarBa_at_hd']}")
    print(f"  FLANG_ss_hd                  : {completed_dict['FLANG_ss_hd']}")
    print(f"  FLANG_pos_hd                 : {completed_dict['FLANG_pos_hd']}")
    print(f"  FLANG_neg_hd                 : {completed_dict['FLANG_neg_hd']}")
    print(f"  FLANG_neu_hd                 : {completed_dict['FLANG_neu_hd']}")
    print(f"  FLANG_lab_hd                 : {completed_dict['FLANG_lab_hd']}")
    print(f"  FLANG_ms_hd                  : {completed_dict['FLANG_ms_hd']}")
    print(f"  FLANG_at_hd                  : {completed_dict['FLANG_at_hd']}")
    print(f"  kw15_proba_bd                : {completed_dict['kw15_proba_bd']}")
    print(f"  kw15_proba_hd                : {completed_dict['kw15_proba_hd']}")
    print(f"  kw15_at                      : {completed_dict['kw15_at']}")
    print(f"  FineClass                    : {completed_dict['FineClass']}")
    print(f"  EarningScore                 : {completed_dict['EarningScore']}")
    print(f"  EarnBreakdown                : {completed_dict['EarnBreakdown']}")
    print(f"  Trigger                      : {completed_dict['Trigger']}")
    print(f"  Trigger legs                 : {trigger_label}")
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

    # Content dedup — the same headline for the same TICKER on the same day is an
    # RTPR re-publish under a new ID (PRZO 2026-07-06, FOFO 2026-07-07) → drop
    # that ticker like an ID dup. Keyed per ticker rather than per article since
    # 2026-08-12 because a re-publish routinely carries a DIFFERENT ticker list
    # (warrant added/dropped, partner added) — see the _seen_content note above.
    #
    # Runs AFTER the ticker-count filter on purpose: an article naming > 2
    # tickers is spam whatever the dedup state, so that filter must keep seeing
    # the original list.
    hl_norm = ' '.join(headline.lower().split())
    fresh, dupes = [], []
    for symbol in tickers:
        prev = _seen_content.get((hl_norm, symbol))
        if prev is None:
            fresh.append(symbol)
        else:
            dupes.append(f"{symbol} ({(arrival_dt - prev).total_seconds():.1f}s ago)")
    if not fresh:
        logger.info(f"[Row] id={art_id}: duplicate content (headline already seen for "
                    f"every ticker: {', '.join(dupes)}) — skipped")
        return
    if dupes:
        # Partial overlap. The article brings a ticker we have not handled today,
        # but if none of those is in-universe there is nothing left to act on —
        # this is the common re-publish shape, where the copy that arrives second
        # is the one carrying the warrant (BBLG / BBLG+BBLGW). Arming already
        # skips the warrant, but the fan-out's all-out-of-universe fallback would
        # write a junk warrant-only row into the calibration corpus. Drop it.
        # Guarded on `dupes` so a genuinely new article keeps today's behaviour,
        # fallback row included.
        if not any(s in _inscope_set for s in fresh):
            logger.info(f"[Row] id={art_id}: duplicate content for {', '.join(dupes)}; "
                        f"remaining {fresh} all out-of-universe — skipped")
            return
        logger.info(f"[Row] id={art_id}: duplicate content for {', '.join(dupes)} — "
                    f"those tickers dropped, continuing with {fresh}")
        tickers = fresh
    for symbol in fresh:
        _seen_content[(hl_norm, symbol)] = arrival_dt
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

    # FinBERT + FinMarBa + FLANG-BERT headliners + keyword scorer + PR class/earnings
    # scorer run once per article (shared futures — all five are text-only and
    # identical for both tickers). The three headline models are submitted together to
    # their separate pools so they execute simultaneously (ONNX inference releases the
    # GIL); the two regex/sklearn scorers hold it, so they interleave rather than
    # overlap — the win is that everything starts at reception and runs under the
    # (much longer) clerk-arm round-trip.
    #
    # The earnings scorer is the one model that needs a SYMBOL: its two size-relative
    # point rules divide by float-cap. It gets the article's first in-universe ticker
    # and is still scored once per ARTICLE, so a 2-ticker PR's second ticker inherits
    # that denominator (MAX_TICKERS_PER_ARTICLE is 2; such PRs are rare). The lookup
    # itself happens on the pool thread, not here — this one must stay lean.
    primary_symbol = next((s for s in tickers if s in _inscope_set), tickers[0])
    f_finbert  = _finbert_executor.submit(analyze_finbert, news_dict)
    f_finmarba = _finmarba_executor.submit(analyze_finmarba, news_dict)
    f_flang    = _flang_executor.submit(analyze_flang, news_dict)
    f_kw       = _kw_executor.submit(_score_keywords, news_dict)
    f_earn     = _earn_executor.submit(_classify_and_score_earnings,
                                       news_dict, primary_symbol)

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
            f_finbert, f_finmarba, f_flang, f_kw, f_earn, f_arm.get(symbol),
            arm_blocked,
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
    global _kw15_body_pipe, _kw15_head_pipe, _earn_params
    logger.info("Orchestrator5.2 starting...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # The gate, BEFORE any model loads. Deliberately first: the rules file is the one
    # artifact a human hand-edits, so it is the one most likely to be wrong, and
    # paying ~30 s of ONNX + joblib loading only to learn you typed `kw15_body`
    # is a bad loop at 06:00 before the open. The models are frozen artifacts that
    # change almost never; this file changes weekly. Nothing in the parse needs a
    # model. Raises RuntimeError and stops the process on any problem.
    _load_trigger_rules(TRIGGER_RULES_FILE)
    # Terse now, full echo after pre-warm — so the gate is on the record even if a
    # model load dies before we reach the [Gate] ACTIVE block.
    logger.info("[Gate] Loaded %d rule(s) from %s: %s", len(_trigger_rules),
                TRIGGER_RULES_FILE,
                ', '.join(f"{r.name}{' (REQUIRE)' if r.kind == _RULE_REQUIRE else ''}"
                          for r in _trigger_rules))

    # Pre-warm ALL THREE headline models + a smoke inference each so the first real
    # PR pays no session/tokenizer init or first-call kernel-JIT cost on any model.
    logger.info("Pre-loading FinBERT-headliner model...")
    load_finbert_model()
    analyze_headline("startup self-check")          # warm ORT kernels
    logger.info("FinBERT-headliner model ready.")

    logger.info("Pre-loading FinMarBa-headliner model...")
    load_finmarba_model()
    score_finmarba_headline("startup self-check")   # warm ORT kernels
    logger.info("FinMarBa-headliner model ready.")

    logger.info("Pre-loading FLANG-BERT-headliner model...")
    load_flang_model()
    score_flang_headline("startup self-check")      # warm ORT kernels
    logger.info("FLANG-BERT-headliner model ready.")

    # Keyword leg: load the two corrected-label ge15 pipelines and HARD-FAIL on
    # any problem. joblib.load raises on a missing/corrupt file, and the smoke
    # inferences below catch a pipeline that loads but cannot score — a trading
    # process must refuse to start rather than run with a permanently-None
    # keyword leg, which would silently degrade the gate to FinBERT/FMB only.
    # The smoke calls also pre-warm sklearn so the first real PR pays no
    # first-call overhead.
    logger.info("Pre-loading ge15 keyword pipelines (body + headline)...")
    _kw15_body_pipe = joblib.load(KW15_BODY_PIPELINE)
    _kw15_head_pipe = joblib.load(KW15_HEAD_PIPELINE)
    _smoke_body = _kw15_body_pipe.predict_proba(
        [_kw15_prep("startup self-check", KW15_BODY_MAX_WORDS)])[0][1]
    _smoke_head = _kw15_head_pipe.predict_proba(
        [_kw15_prep("startup self-check", None)])[0][1]
    for _name, _v in (("body", _smoke_body), ("headline", _smoke_head)):
        if not (0.0 <= float(_v) <= 1.0):
            raise RuntimeError(f"ge15 {_name} pipeline returned {_v!r}, not a "
                               "probability; refusing to start")
    # A MODEL smoke test — it can no longer assert anything about the gate, since the
    # cuts these scores are judged against live in the rules file and there may be any
    # number of them. Which rules read this model is echoed by _log_active_gate().
    logger.info("ge15 keyword pipelines ready (smoke: body=%.6f head=%.6f).",
                _smoke_body, _smoke_head)

    # Earnings leg: load the calibrated score params ONCE (never in the hot path — the
    # default path re-reads the JSON per call) and HARD-FAIL on any problem. Same
    # reasoning as the keyword leg: this leg can fire trades on its own, so a
    # classifier that silently stopped recognising Earnings_Results, or a scorer that
    # returned something other than points, must stop the process rather than quietly
    # remove a whole trade branch. The smoke calls also warm the regex caches.
    logger.info("Pre-loading PR classifier + earnings scorer...")
    _earn_params = earn_load_params()
    _smoke_hl = "Acme Corp Reports Second Quarter 2026 Financial Results"
    _smoke_body_txt = ("Revenue grew 60% year-over-year to $24.3 million. "
                       "The company reported record quarterly revenue.")
    # headline=True, to match how _classify_and_score_earnings actually calls it
    # below. Without it this smoke-tested the BODY path against a headline, which
    # stopped being equivalent on 2026-08-14: Earnings_Results is now headline-only
    # (PrClassifier.BODY_EXCLUDED_CLASSES), because 7 of the 9 rows the body path
    # ever put in this live-gated class were wrong.
    _smoke_class, _ = classify_pr(_smoke_hl, headline=True)
    if _smoke_class != EARN_CLASS:
        raise RuntimeError(f"PrClassifier returned {_smoke_class!r} for a canonical "
                           f"earnings headline, expected {EARN_CLASS!r}; refusing to start")
    _smoke_feats = earn_extract_metrics(_smoke_hl, _smoke_body_txt, None)
    _smoke_feats.pop('_body_hits', None)
    _smoke_feats.pop('_head_hits', None)
    _smoke_score, _smoke_brk = earn_score_metrics(_smoke_feats, params=_earn_params)
    if not isinstance(_smoke_score, int) or _smoke_score < 1:
        raise RuntimeError(f"earning_scorer returned {_smoke_score!r} on a PR with "
                           "explicit revenue growth; expected a positive int score — "
                           "refusing to start")
    logger.info("PR classifier + earnings scorer ready (smoke: class=%s score=%d [%s]).",
                _smoke_class, _smoke_score, _smoke_brk)

    # Echo the WHOLE gate: every rule, with the thresholds actually in force, rebuilt
    # from the compiled conditions. See _log_active_gate().
    _log_active_gate()

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
        _flang_executor.shutdown(wait=True)
        _kw_executor.shutdown(wait=True)
        _earn_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        logger.info("Orchestrator5.2 stopped.")


if __name__ == '__main__':
    if '--check-rules' in sys.argv:
        # Parse + echo orchestrator_trigger_rules.txt, then exit. No models, no
        # sockets, no universe read: ~1 s. This is the fast edit/verify loop for the
        # rules file, and the mitigation that keeps a typo from turning the
        # refuse-to-start policy into a trading outage. Exit 0 means the gate echoed
        # here is exactly the gate O5.2 would run.
        _load_trigger_rules(TRIGGER_RULES_FILE)
        _log_active_gate()
        sys.exit(0)
    main()
