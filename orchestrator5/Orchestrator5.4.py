import os
# ── 5.4: native thread pools pinned to ONE thread, BEFORE numpy/pandas load ─────
# Every model now runs inside the scoring engine (o54_engine.py), which sets each ONNX
# session's thread count explicitly. Nothing else in this process may start a
# hardware-sized BLAS/OpenMP pool that would compete with it for the pinned CPUs.
# setdefault: an operator's explicit environment still wins.
for _v in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
    os.environ.setdefault(_v, '1')
os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
import sys
import argparse
import csv
import json
import logging
import math
import threading
import socket
import time
import re
import difflib
import functools
import operator
import importlib.util as _ilu
import pandas as pd
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
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

# ── 5.4: the gate's three headline models + the ge15 keyword scorer ───────────
# ProsusAI FinBERT, FinMarBa and FLANG-BERT (the gate's headline models) and the two
# corrected-label ge15 tfidf pipelines are NO LONGER imported here. The scoring engine
# (o54_engine.py, ModelSet) loads them — in this process for the thread engine, in
# each worker for the process engine — through the SAME frozen headliner modules,
# module names and public functions 5.3 called, so every score is the one 5.3 would
# have produced. The engine only decides how many threads each ONNX session may use;
# the goldens self-check (bench/o54_goldens.json) proves at every startup that the
# choice changes no output digit. The keyword scorer keeps its 400-word body cap and
# its own cleaner (o54_engine.KW15_BODY_MAX_WORDS). The history of every cut still
# lives in orchestrator_trigger_rules.txt and version-control.txt.
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
# ⚠ PrClassifier.BODY_LEAD_CHARS (1500) is deliberately NOT imported. Since the
# 200-word alignment (2026-09-19) the classifier's body fallback reads the
# _prep_body_200 string (clean_body + the first 200 words), not its own slice of the
# raw cell — see _classify_and_score_earnings. Offline twin: PrClassifier.py --body-200w.

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

# ── 5.4: model-2's extra encoders are GONE ───────────────────────────────────
# 5.3 imported DeBERTa, Trading-Hero and FinancialBERT here for model-2's headline and
# body channels — ~620 ms of model time per PR, the reason bursts queued far past the
# clerk's 2 s gate. model-3 reads none of them: beyond the gate's own three headline
# models its ONLY model call is the INT8 FinBERT headline embedding (o54_engine.py).
# The modules are still on disk and still exercised by
# tools/Encoder_Models/verify_headliners.py; 5.3 is the rollback that runs them.
# ─────────────────────────────────────────────────────────────────────────────

# ── body text prep: the PR classifier's input (5.4: its ONLY consumer) ────────
# body_prep.prep_body = clean_body (CSV-escape artifact, leading verbatim headline
# repeat, wire dateline, tracker URLs, strip_wire_boilerplate) + cap_words(n).
# 5.3 shared this string between the PR classifier, two body encoders and the FP32
# embedder. model-3 reads NO body, so in 5.4 it feeds exactly one thing — FineClass,
# through _classify_article — and that is why it stays: FineClass is both a model-3
# feature and a gate input (every `FineClass in ...` list, and XgbSolo's liveness
# guard). Removing or re-capping it would silently move FineClass, and so what trades.
#
# ⚠ It lazily imports tools/keyword/keyword_finder.py (pandas + sklearn) on its
# FIRST call, so main() warms it once rather than paying that on a live PR.
_bodyprep_spec = _ilu.spec_from_file_location(
    "o54_body_prep",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "body_prep.py"),
)
_bodyprep_mod = _ilu.module_from_spec(_bodyprep_spec)
_bodyprep_spec.loader.exec_module(_bodyprep_mod)
prep_body = _bodyprep_mod.prep_body
# ─────────────────────────────────────────────────────────────────────────────

# ── 5.4: the scoring engine (o54_engine.py, next to this file) ────────────────
# Holds the per-article model tasks — the gate's three headline models, kw15,
# model-3's INT8 headline embedding + PCA, the model-3/model-3FT boosters — and the
# two ways of running them: threads in this process, or pinned worker subprocesses.
# A plain import (its name has no hyphen or dot). The directory goes on sys.path
# explicitly so the import also works when a bench tool loads this file by path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import o54_engine as o54  # noqa: E402
# ─────────────────────────────────────────────────────────────────────────────

# ── 5.3: TimeToOpen (hours from the 04:00 ET ETH open of the PR's session) ────
# dayOfWeek_addOn owns this column offline; importing it rather than re-deriving
# the calendar is deliberate — a second pandas_market_calendars instance here
# could drift from the one the msc_/VIX/mcap anchors all resolve against.
#
# ⚠ This transitively imports tools/market_state/market_state_calculator.py,
# which imports ib_insync at module level. It does NOT connect to anything — no
# IB() is constructed at import — but it is a heavyweight import, so it happens
# once here at startup and never on the trade path.
# Two sys.path entries, both required by dayOfWeek_addOn's own imports:
#   market_state/     -> its `from market_state_calculator import Sessions`
#   price_stats_loader/ -> its sibling `from vix_addOn import ...` (that directory is
#                          not a package, so a path-load alone does not resolve it)
sys.path.insert(0, str(SCRIPTS_DIR / "tools" / "market_state"))
sys.path.insert(0, str(SCRIPTS_DIR / "price_stats_loader"))
_dow_spec = _ilu.spec_from_file_location(
    "o54_dayofweek_addon",
    str(SCRIPTS_DIR / "price_stats_loader" / "dayOfWeek_addOn.py"),
)
_dow_mod = _ilu.module_from_spec(_dow_spec)
_dow_spec.loader.exec_module(_dow_mod)
# Only these two are bound: time_to_open() already calls reference_session()
# internally, and _compute_time_to_open reaches parse_arrival_time through _dow_mod.
time_to_open = _dow_mod.time_to_open
Sessions     = _dow_mod.Sessions
# ─────────────────────────────────────────────────────────────────────────────

# ── 5.4: the XGBoost apply-time transform moved into the engine ───────────────
# XGBoost_inference_script.build_row_features (sentinel substitution, winsorisation,
# categoricals, feature order) is still the ONLY preprocessing — imported verbatim,
# never reimplemented — but it now runs where the boosters live:
# o54_engine.ModelSet._t_xgb. This process only assembles the RAW feature row
# (_xgb_raw_row) from values it already holds for the gate.
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
# The production dirs. --out-dir / --log-dir redirect both: a replay or a benchmark
# must never append to the live tables/ file that daily-board and the 20:05 cron read.
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
#   process holds. `Orchestrator5.4.py --check-rules` parses + echoes and exits.
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
# them. Run only ONE orchestrator instance — NEVER two of Orchestrator5.0/5.1/5.2/5.3/5.4
# at the same time (both would double-arm the clerk); extra read-only watchers
# like `nc 127.0.0.1 8900` are fine.
FEED_HOST                = n2_config.get(_CFG, 'FEED_HOST', '127.0.0.1')
FEED_PORT                = n2_config.get_int(_CFG, 'FEED_PORT', 8900)
FEED_CONNECT_TIMEOUT_SEC = 5     # per-attempt TCP connect timeout
FEED_RETRY_SEC           = 3     # reconnect cadence while the connector is down
FEED_SILENCE_TIMEOUT_SEC = 20    # connector heartbeats every 5s → 20s silence = dead peer
# ─────────────────────────────────────────────────────────────────────────────

# ── Worker pools (plumbing only — 5.4 runs no model on these) ─────────────────
# 5.3 kept a 4-wide pool per headline model here, a single keyword worker and a single
# XGBoost-leg worker. 5.4 moved every model call into ONE scoring engine (o54_engine.py,
# configured below). What remains here never runs a model:
COLLECT_WORKERS    = 8     # per-ticker collectors (resolve futures + gate + clerk + TSV)
CLERK_WORKERS      = 6     # STEP-1a arms; a touch above clerk's 5-client pool
# PR classifier + earnings scorer: pure-Python regex, so it HOLDS the GIL. 2 workers is
# not about throughput — it just stops one long body from head-of-line-blocking the next.
EARN_SCORE_WORKERS = 2

# ── 5.4: the scoring engine and where it runs ────────────────────────────────
# THE BUDGET (operator, 2026-09-24): reception -> STEP-1b sent in under 400 ms for EVERY
# armed PR, including the worst bursts on record — 17 PRs in 2.0 s (08-06 08:30), 7 in
# 0.20 s (08-18 08:30) — which needs >= ~14 PRs/s of throughput. 5.3 measured median
# 983 ms, p90 4.9 s, max 7.5 s over 09-21..09-24: 94% of its decisions were over 400 ms.
#
# Engine layouts (o54_engine.py; override with --engine / --pools / --workers /
# --intra-op / --cpus):
#   threads/shared     ONE priority pool of N threads for every model task, each ONNX
#                      session single-threaded (intra_op=1): N concurrent inferences on
#                      N CPUs, PRs a clerk is waiting on served first. kw15 keeps its
#                      own single thread, as in 5.3.
#   threads/per-model  5.3's layout (a pool per headline model, one leg worker, ORT's
#                      default thread pools) — the benchmark's reference point.
#   procs              N pinned worker SUBPROCESSES, each holding every model: the same
#                      task parallelism with no shared GIL.
# CHOSEN BY THE BURST BENCHMARK (2026-09-24, bench/reports/O54_latency_2026-09-24_1426.md;
# the REAL 5.4 against a fake feed replaying the 16 largest historical bursts at their
# recorded timing + a fake clerk with the real clerk's ARM delays; 2 repeats). Worst
# PR of any historical burst, reception -> STEP-1b received by the clerk:
#   5.3's layout (per-model pools, ORT default threads)  423 ms unpinned, 529 pinned
#   threads/shared, 6 workers, intra_op=1  <- SHIPPED     168 ms  (lone PR ~60 ms)
#   threads/shared, 3 workers, intra_op=1 or 2            184 / 206 ms
#   6 pinned worker PROCESSES (--engine procs)            141 ms, but ~10 GB RSS
#   3 pinned worker processes x intra_op=2                157 ms, ~5 GB RSS
# The operator's rule is the SIMPLEST layout under 300 ms: threads. The process
# engine is the fallback if bursts grow — it keeps ~15 PRs landing inside 0.2 s under
# 400 ms where threads keep ~10 (the GIL-bound Python halves stop sharing one lock).
# Why this is not 5.3's settled "parallelism buys 1.10x": that was measured on
# 270-token BODIES, where one session's 4 intra-op threads already scale 2.61x. The
# INT8 headline embedding takes 14.0 ms on ONE thread — the same as on ORT's default
# pool — so N single-threaded sessions are N times the throughput.
ENGINE_KIND     = 'threads'    # 'threads' | 'procs'
ENGINE_POOLS    = 'shared'     # threads only: 'shared' | 'per-model'
ENGINE_WORKERS  = 6
ENGINE_INTRA_OP = 1            # ORT intra_op_num_threads per session; 0 = ORT's default pool
ENGINE_SPINNING = False        # only matters for intra_op > 1

# ⚠ OPERATOR DECISION 2026-09-24: physical core 0 — logical CPUs 0 AND 4 (hyperthread
#   siblings: on this i7-1185G7 CPU c and c+4 share one core) — is RESERVED for
#   clerk-1.2 (trade-mole's surge detection), both IB Gateways and the connector. The
#   whole orchestrator — feed thread, collectors, engine threads, worker processes — is
#   pinned to the other three cores. Hyperthread pairs share one AVX-512 VNNI unit, so
#   these 6 logical CPUs are worth ~3.3-3.6 cores of INT8 throughput, not 6.
INFER_CPUS = o54.DEFAULT_INFER_CPUS   # (1, 2, 3, 5, 6, 7); --cpus overrides, 'all' = no pin

# CPU performance mode (perf_mode.sh), MEASURED 2026-09-24 on this layout
# (bench/reports/O54_latency_2026-09-24_1711_perf.md vs ..._1426.md): typical decisions
# ~9 ms faster (burst p90 92 -> 82, lone median 60 -> 51 ms, faster on 13/16 bursts),
# the worst burst unchanged (168 -> 171 ms). Below the agreed >= 10 ms bar, so it is NOT
# required. Set True only if the operator schedules it: main() then WARNS when it
# starts inside trading hours with the mode off.
EXPECT_PERF_MODE = False

# ── 5.4: the XGBoost leg — model-3 (training_data/model-3) ───────────────────
# model-3 is HEADLINE-ONLY. Beyond the gate's three headline models (computed for the
# gate anyway, reused as features at zero cost) it makes ONE model call per PR: the
# INT8 FinBERT embedding of the headline, projected onto model-3's own PCA basis.
# MEASURED at the model-3 build (m3_parity.py, 60 val articles, one worker, quiet CPU):
#   INT8 embedding (headline) + PCA                    ~15-22 ms
#   feature build + DMatrix + 5 predicts, nthread=1     ~12 ms
#   LEG                                                 ~32 ms median / ~42 ms p90
# against model-2's ~620 ms (DeBERTa + TradeHero on the headline, FinancialB + TradeHero
# on a 200-word body, the FP32 embedding of both). Val AUC(ge50) 0.720 vs 0.722 — every
# paired CI contains 0. Full record: training_data/model-3/model-3_implementation_guide.md
# and training_data/model-3/reports/MODEL-3_SUMMARY.md.
#
# ⚠ ONLY THE BOOSTERS A RULE READS RUN BEFORE STEP-1b. _gate_xgb_cols is derived from the
#   loaded rules exactly like _gate_needs_xgb (today: ge50_m3, 47 trees on one feature
#   build). The other nine boosters and the m3FT feature build (~15-20 ms of GIL-bound
#   pandas per ticker) run AFTER the decision is sent; they only fill the TSV.
#
# Ceilings so a hung engine can never stall a decision or a row:
XGBLEG_WAIT_SEC   = 5     # gate path: embedding + gate booster (~150 PRs deep at ~32 ms)
XGB_POST_WAIT_SEC = 30    # after STEP-1b: the rest of the family, and corpus-lane rows

# ⚠ 200 WORDS, AND IT MUST STAY 200. Since 5.4 no model reads the body: this cap serves
#   ONLY the PR classifier's body fallback (_classify_article), and FineClass was
#   trained (the _reCat corpus) and gate-tuned on exactly this string. Moving it moves
#   FineClass — model-3's categorical feature AND every `FineClass in ...` gate list.
#   (5.3 named it XGB_BODY_CAP_WORDS, when two body encoders and the FP32 embedder read
#   the same string.)
# ⚠ NOT the ge15 keyword scorer's window: kw15 keeps KW15_BODY_MAX_WORDS = 400 and its
#   own cleaner (o54_engine.py) — it is a live gate input and its window is calibrated.
PRCLASS_BODY_CAP_WORDS = 200

# The model family — model-3 (held-out: trained on rows < 2026-07-28, val-scored once)
# and model-3FT (the same 25 features refit on every row). Defined ONCE, in
# o54_engine.XGB_MODELS, because the process holding the boosters and the TSV header
# must agree column for column. Only the held-out `_m3` twin may feed a rule: `_m3FT`
# has no held-out score at all, and its ge50 sits on a different scale.
XGB_MODELS = o54.XGB_MODELS
XGB_COLS   = o54.XGB_COLS
XGB_GEN    = o54.M3_GEN      # the Xgb_gen TSV cell: which generation scored the row

# (XGB_UNIVERSE_COLS is DERIVED from _XGB_FEATURE_SOURCES, further down, so it can
# never drift from what the loaded models actually read.)
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
logger = logging.getLogger('Orchestrator5.4')


class _OrchDailyFileHandler(c3.DailyFixedTZFileHandler):
    """Daily Orchestrator5.4_YYYY_MM_DD.log (fixed UTC-4 date key) — only the filename
    differs from the connector's handler. Attached to the ROOT logger, so the engine's
    lines and its workers' relayed [W<n>] lines land in the same file; connector3
    records do NOT propagate (its logger is queue-fed with propagate=False)."""

    def path_for_date(self, date_key: str) -> Path:
        return self.log_dir / f"Orchestrator5.4_{date_key}.log"


def _install_file_logging(log_dir) -> None:
    """Attach the daily file handler. Called from the entry point, NOT at import (5.3
    attached it at import): bench tools load this module by path and must never write
    into the production logs/ dir."""
    log_dir = Path(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    handler = _OrchDailyFileHandler(log_dir)
    handler.setFormatter(c3.FixedTZFormatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.getLogger().addHandler(handler)

_iti_df: pd.DataFrame = None
_last_iti_reload_date = None
_iti_df_lock = threading.Lock()
# 5.4: the same universe as `symbol -> row dict` (the FIRST row per symbol, RAW cells),
# built ONCE per (re)load. Every per-ticker lookup is one dict get instead of 5.3's
# lock + full pandas mask scan per cell (~8 per row, each holding the GIL). Rebound,
# never mutated, so lock-free readers always see one consistent universe.
_univ_index: dict = {}
# --universe-file pins ONE universe file (replays/benchmarks). None = newest by name.
UNIVERSE_FILE_OVERRIDE = None

# Full in-universe symbol set (Symbol column of the latest universe TSV) — plain
# universe membership, used to gate clerk arming and TSV fan-out. Rebound (not
# mutated) on reload so lock-free reads always see a consistent set object.
_inscope_set: set = set()

# Headline filters. _nw_excluded_strings DROPS the article (NW4 input filter kept);
# _excluded_strings (orchestrator-level) only blocks the clerk arm (clerk gate kept).
_nw_excluded_strings: set = set()
_excluded_strings: set = set()

# Calibrated earnings-score params (earn_score_params.json), loaded ONCE in main().
# ⚠ Must always be passed explicitly as `params=` — earn_score_metrics(feats) with no
# params calls load_params() internally, which does os.path.exists + json.load on
# EVERY call, i.e. a disk read per PR on the trade-critical path.
_earn_params = None

# ── 5.4 singletons ────────────────────────────────────────────────────────────
# Set once in main() (or --check-xgb) and then READ-ONLY, so the pools touch them
# without a lock — the same contract as _earn_params.
# The scoring engine: o54_engine.ThreadEngine | ProcessEngine.
_engine = None
# The ten boosters' preps as validated by o54_engine.load_xgb_preps() — NOT the
# boosters, which the engine holds in whichever process runs them. Empty = no leg.
_xgb_meta: tuple = ()
# True once the loaded universe TSV is confirmed to carry XGB_UNIVERSE_COLS. When
# False the predictions are suppressed with Xgb_status = 'disabled_universe'.
_xgb_universe_ok: bool = False
# XNYS session index for TimeToOpen; rebuilt daily beside the universe reload.
_sessions = None
_sessions_lock = threading.Lock()
# Set by _load_trigger_rules: True when a LOADED rule names a field of the XGBoost leg,
# which makes STEP-1b WAIT for it. _gate_xgb_cols are the prediction columns those
# rules read — the ONLY boosters scored before the decision is sent.
_gate_needs_xgb: bool = False
_gate_xgb_cols: tuple = ()

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

# ─── ThreadPoolExecutors (plumbing) + the engine's priorities ─────────────────

_collect_executor = ThreadPoolExecutor(max_workers=COLLECT_WORKERS,
                                       thread_name_prefix='collect')

# PR classifier + earnings scorer (2026-08-02) — its own pool so a long earnings body
# never queues behind anything on the critical path.
_earn_executor = ThreadPoolExecutor(max_workers=EARN_SCORE_WORKERS,
                                    thread_name_prefix='earnscore')

# Priorities for _engine.submit(). LOWER IS SERVED FIRST, FIFO within a priority.
# 5.3 prioritised only the XGBoost leg, and only on REQUIRE preconditions — inert since
# the last REQUIRE rule left the rules file (every PR became "tradeable"). 5.4
# prioritises EVERY engine task by whether a STEP-1b decision waits on it
# (_task_priority): a PR no clerk will ever hear about must not delay one it will.
_PRIO_TRADEABLE = o54.PRIO_TRADEABLE   # a decision the clerk is waiting for depends on it
_PRIO_CORPUS    = o54.PRIO_CORPUS      # TSV-only: served when nothing tradeable waits

_tsv_lock = threading.Lock()

# Dedicated pool for clerk STEP-1a arms so arming is never starved by the collect
# tasks.
_clerk_executor = ThreadPoolExecutor(max_workers=CLERK_WORKERS, thread_name_prefix='clerk')


# ─── Engine results: logging + small adapters ────────────────────────────────
#
# 5.3's analyze_finbert / analyze_finmarba / analyze_flang / _score_keywords scored AND
# logged. In 5.4 the scoring lives in o54_engine.ModelSet — possibly in another process —
# so the log lines are written here, from a done-callback: once per ARTICLE, in the
# format 5.3 wrote them, so every grep over logs/ keeps working.

_KW_NONE_RESULT = {'kw15_proba_bd': None, 'kw15_proba_hd': None,
                   'kw15_at': None}


def _log_task(fut, label: str, news_dict: dict) -> None:
    """Future done-callback: one log line per scorer per article. Runs on whichever
    thread completed the future (an engine thread, or a worker's feeder thread), or at
    once on the caller if it was already done. Never raises."""
    try:
        sym = news_dict.get('Symbol')
        exc = fut.exception()
        if exc is not None:
            logger.error("[%s] id=%s sym=%s: scorer error: %s", label,
                         news_dict.get('ID'), sym, exc)
            return
        r = fut.result()
        if label == 'FinBERT':
            logger.info(f"[FinBERT] {sym} → {r['lab'].upper()} (score={r['ss']:.4f})")
        elif label == 'FinMarBa':
            logger.info(f"[FinMarBa] {sym} → {r['lab'].upper()} "
                        f"(indecisive={r['ind']:.4f})")
        elif label == 'FLANG':
            logger.info(f"[FLANG] {sym} → {r['lab'].upper()} (score={r['ss']:.4f}) "
                        f"in {r['ms']:.1f}ms")
        elif label == 'KwScore':
            logger.info(f"[KwScore] id={news_dict.get('ID')} sym={sym}: "
                        f"kw15_proba_bd={r['kw15_proba_bd']:.6f} "
                        f"kw15_proba_hd={r['kw15_proba_hd']:.6f} in {r['_ms']:.1f}ms")
        elif label == 'Embed':
            logger.info(f"[Embed] {sym} → hd_pca[3]={r['hd_pca'][3]:.4f} "
                        f"in {r['_embed_ms']:.1f}ms")
    except Exception:                                        # noqa: BLE001
        logger.exception("[%s] result logging failed", label)


def _kw_from_task(r: dict) -> dict:
    """The kw15 task's result -> the three TSV / gate cells. kw15_at is the scorer's own
    COMPLETION stamp (5.3 wrote it from inside the scorer), carried as epoch seconds so a
    worker process needs no connector import to produce it."""
    ep = r.get('_done_epoch')
    return {
        'kw15_proba_bd': r.get('kw15_proba_bd'),
        'kw15_proba_hd': r.get('kw15_proba_hd'),
        'kw15_at': (c3.format_millis_time(datetime.fromtimestamp(ep, tz=c3.FIXED_TZ))
                    if ep else None),
    }


_EARN_NONE_RESULT = {'FineClass': None, 'BroadClass': None,
                     'EarningScore': None, 'EarnBreakdown': None}


def _classify_article(headline: str, body_200: str):
    """PrClassifier's (FineClass, BroadClass) for one article, or None if it raised.

    THE one place the two-step headline-then-body call lives, so the feed thread and
    the earnings worker can never disagree about a PR's class. Pure regex, measured
    0.20 ms median / 3.5 ms p100 over 36 real PRs, which is why 2026-09-21 moved it
    onto the feed thread: _leg_priority needs the class BEFORE the leg is queued, and
    computing it here means it is computed ONCE rather than twice.

    Returns None (not a (None, None) pair) on failure, so the caller can tell
    "the classifier blew up, retry it under your own handler" apart from
    "classified, and the answer was nothing"."""
    try:
        fine, broad = classify_pr(headline, headline=True)
        if fine == 'Other':
            fine, broad = classify_pr(body_200)
        return fine, broad
    except Exception as exc:                        # noqa: BLE001
        logger.warning("[PrClass] feed-thread classify failed (%s) — the earnings "
                       "worker will retry it", exc)
        return None


def _classify_and_score_earnings(news_dict: dict, primary_symbol: str,
                                 body_200: str, preclassified=None) -> dict:
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

    The earnings SCORER reads EXACTLY the string that lands in the TSV Body cell
    (clean_inline_text(body_raw)) — the same string _score_keywords sees — so an
    offline rescore of the daily TSV reproduces the live EarningScore. That is the
    live == backtest invariant; never pass the raw payload or a differently-cleaned
    body. The CLASSIFIER's body fallback is a different string: `body_200`, the one
    _prep_body_200 built on the feed thread. Both reproduce offline.

    `primary_symbol` supplies the float-cap denominator for the two size-relative
    point rules (revenue$ / profit$ vs Float x LastDailyClose). It is the article's
    FIRST in-universe ticker: the score is computed once per ARTICLE like every other
    model here, so on a 2-ticker PR the second ticker inherits the first's
    denominator. Deliberate — MAX_TICKERS_PER_ARTICLE is 2 and 2-ticker PRs are rare."""
    news_id = news_dict.get('ID')
    fine = None                     # survives into the except below — see the docstring
    broad = None                    # ditto (5.3: BroadClass is an XGBoost feature)
    try:
        body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
        headline  = news_dict['Headline']
        t0 = time.monotonic()

        # Headline first, body as fallback — PrClassifier.classify_row's logic
        # without a shim dict (news_dict's body key is `body_raw`, not Body/body).
        # ⚠ The fallback text is `body_200`, NOT a slice of body_cell: the classifier
        # reads the _prep_body_200 string (clean_body + the first PRCLASS_BODY_CAP_WORDS
        # words), built once on the feed thread. It replaced a
        # 1500-char slice of the raw cell. Two properties made that safe to change:
        # the window is still LEAD-only (about-us / safe-harbor boilerplate deeper in
        # the text is full of false triggers), and clean_body first strips the wire
        # dateline, the verbatim headline repeat and tracker URLs, so 200 words of
        # prose reach FURTHER into the actual article than 1500 raw chars did.
        # Offline twin: PrClassifier.py --body-200w.
        # headline=True unlocks PrClassifier.HEADLINE_RULES (the loose "reports
        # revenue/earnings" rule, added 2026-08-07). It MUST stay off for the body
        # call: over body prose that rule promotes law-firm notices, analyst
        # coverage and cash-distribution PRs into Earnings_Results, which is exactly
        # the class this function gates a real trade on.
        # 5.3 KEEPS the second element. BroadClass was discarded through 5.2 because
        # no rule read it; it is now an XGBoost feature (rank 13/16/12 by gain across
        # the three classifiers), so both halves of the classifier's verdict are
        # carried. The fallback re-assigns BOTH, so the pair always comes from the
        # same call and can never describe two different texts.
        # 2026-09-21: normally already done on the feed thread by _classify_article
        # (it is needed there for _leg_priority), so this reuses it rather than
        # paying for the same regex pass twice. `preclassified is None` means the
        # feed-thread attempt RAISED — retry it here, under this function's handler.
        if preclassified is not None:
            fine, broad = preclassified
        else:
            fine, broad = (_classify_article(news_dict['Headline'], body_200)
                           or (None, None))

        if fine != EARN_CLASS:
            # ~85% of PRs — skip the extractor entirely. A non-Earnings_Results row can
            # never fire the earnings leg, so its score would be dead weight.
            elapsed_ms = (time.monotonic() - t0) * 1000.0
            logger.info(f"[EarnScore] id={news_id} sym={news_dict.get('Symbol')}: "
                        f"class={fine} (not {EARN_CLASS}) — not scored, "
                        f"in {elapsed_ms:.1f}ms")
            return {'FineClass': fine, 'BroadClass': broad,
                    'EarningScore': None, 'EarnBreakdown': None}

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
            'BroadClass':    broad,
            'EarningScore':  score,
            'EarnBreakdown': breakdown,
        }
    except Exception as exc:
        logger.error(f"[EarnScore] id={news_id}: scorer error: {exc}", exc_info=True)
        # KEEP the class if classification already got that far — it is a separate step
        # from scoring, and since 2026-08-17 FineClass feeds a REQUIRE precondition, so
        # wiping it here would close the whole gate on a row CORE would have traded.
        # `fine` is None only when classify_pr itself failed, which IS an unknown class.
        return {'FineClass': fine, 'BroadClass': broad,
                'EarningScore': None, 'EarnBreakdown': None}


# ─── 5.4: the PR classifier's body string ────────────────────────────────────
#
# 5.3 spent this section on model-2's text scorers — DeBERTa + TradeHero on the
# headline, FinancialB + TradeHero on the body, the FP32 embedding of both: three
# stages, ~620 ms. model-3's single model call lives in o54_engine.ModelSet._t_embed.
# What is left here is the one body string the PR classifier reads.


def _prep_body_200(news_dict: dict) -> str:
    """The PR classifier's body-fallback string, built ONCE per article on the feed
    thread: body_prep.prep_body(clean_inline_text(body_raw), Headline, 200).

    5.4: the classifier is its ONLY consumer (model-3 reads no body). It is fed the exact
    string that lands in the TSV Body cell, so PrClassifier.py --body-200w reproduces
    FineClass offline. ⚠ The 200 must not move — see PRCLASS_BODY_CAP_WORDS."""
    body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
    return prep_body(body_cell, news_dict['Headline'], PRCLASS_BODY_CAP_WORDS)


_EMBED_NONE_RESULT = {'hd_pca': None, '_embed_ms': None}


# ─── 5.4: the XGBoost feature vector (model-3) ───────────────────────────────
#
# ONE table mapping every non-PCA model-3 feature to where its value comes from — the
# same shape as _TRIGGER_FIELDS, for the same reason. _load_xgb_meta() asserts it
# covers every feature of all ten boosters, so a retrain that adds a feature fails at
# startup naming the missing one, instead of scoring a silent NaN forever. The five
# hd_pca_* features come from the engine's embedding task, not from this table.
#
# ⚠ THE '.1' SUFFIXES ARE REAL AND LOAD-BEARING — a pandas duplicate-column artifact
# frozen into the models at fit time. XGBoost matches features BY NAME, and this table
# is where each live bare name is translated to the trained one.
#
# source -> which dict the value is read out of at score time:
#   finbert/fmb/flang  the gate's three headline models, REUSED — scored once per
#                      article for the gate, read again here, never scored twice
#   earn               _classify_and_score_earnings (FineClass)
#   row                row_vals, already resolved for the gate
#   univ               the raw universe row (pipeline-3 market-state block)
#   calc               computed per row (TimeToOpen)
_XGB_FEATURE_SOURCES = {
    # ── the gate's three headline models, REUSED. Their headliners already return
    #    values rounded to 4 dp — exactly what the training corpus holds
    #    (HeadScore11encoders._fmt) — so no further rounding happens here. ─────────
    'Prosus_ss_hd.1':           ('finbert', 'ss'),
    'FLANG_ss_hd.1':            ('flang',   'ss'),
    'FLANG_neu_hd.1':           ('flang',   'neu'),
    # model-3 READS FinMarBa (model-2 did not), so the 'fmb' source is new in 5.4.
    'FinMarBa_ind_hd.1':        ('fmb',     'ind'),
    # ── catalyst class (categorical; an unseen label becomes NaN SILENTLY — re-run the
    #    24-row class check after any PrClassifier edit) ───────────────────────────
    'FineClass':                ('earn',    'FineClass'),
    # ── size, already resolved for the gate. Float_M / MarketCap_M are ALREADY IN
    #    MILLIONS, the training scale — do NOT multiply by 1e6 here. ───────────────
    'Float':                    ('row',     'Float'),
    'MktCap':                   ('row',     'MktCap'),
    # ── the pipeline-3 universe block (12). XGB_UNIVERSE_COLS is derived from exactly
    #    this subset, so the startup schema check tracks the models. None of these uses
    #    the 44444 sentinel: a blank is NaN, in training and live alike. ───────────
    'atr_pct_14':               ('univ',    'atr_pct_14'),
    'realized_vol_21d':         ('univ',    'realized_vol_21d'),
    'range_pct_1d':             ('univ',    'range_pct_1d'),
    'gap_abs_mean_20d':         ('univ',    'gap_abs_mean_20d'),
    'dist_52w_high_pct':        ('univ',    'dist_52w_high_pct'),
    'dist_swing_high_20d_pct':  ('univ',    'dist_swing_high_20d_pct'),
    'dist_swing_high_60d_pct':  ('univ',    'dist_swing_high_60d_pct'),
    'amihud_63d':               ('univ',    'amihud_63d'),
    'msc_split_sessions_since': ('univ',    'msc_split_sessions_since'),  # blank = no split
    # ⚠ ~1.0 on every live row where training has real tails — known, accepted
    #   train/serve skew, inherent to point-in-time inference.
    'msc_basis_ratio':          ('univ',    'msc_basis_ratio'),
    'SI_SharesShort':           ('univ',    'SI_SharesShort'),
    'SVR_5dAvgPct':             ('univ',    'SVR_5dAvgPct'),
    # ── computed at reception ───────────────────────────────────────────────────
    'TimeToOpen':               ('calc',    'TimeToOpen'),
}
# ⚠ DO NOT ADD CTB_* / Borrow_* / SqueezeScore. They are 0% filled in every live
#   pipeline-3 universe file but 99.5% filled in the training corpus; model-3 excluded
#   them for exactly that reason (training_data/model-3/m3_config.TRAIN_SERVE_SKEW).

# The universe columns the loaded models read, DERIVED so it can never drift from the
# table above. They exist ONLY in a daily_universe_pipeline-3 file (147 cols, first
# written 2026-09-17); _universe_supports_xgb() checks for them.
XGB_UNIVERSE_COLS = tuple(
    key for _feat, (src, key) in _XGB_FEATURE_SOURCES.items() if src == 'univ')

# Read unconditionally by build_row_features BEFORE the feature slice is taken (it
# replays _fit_sentinels, which ran on the full X at fit time), so they must be present
# and float-parseable even though none survives into features_used — a missing key is a
# KeyError inside the engine. Filled defensively; model-3's preps carry sentinel_high
# for them too.
_XGB_SENTINEL_COLS = {
    'RTH_avgITI_sec': ('univ', 'RTH_avgITI_sec'),
    'ETH_avgITI_sec': ('univ', 'ETH_avgITI_sec'),
    'RTH_tradeSize':  ('univ', 'RTH_tradeSize'),
}

# Sources whose absence from the gate is normal rather than wasteful: `row` is universe
# data read anyway, and the leg's sources are scored for the TSV corpus even when no
# rule reads them.
_GATE_NO_WARN_SOURCES = frozenset({'row', 'xgb', 'calc'})

# The _FieldSpec sources that only exist once the XGBoost leg has resolved. A rule
# naming ANY field from one of these makes the gate wait for the leg. (5.3's 'hdx' and
# 'body' sources left with model-2's extra encoders.)
_XGB_GATE_SOURCES = frozenset({'xgb', 'calc'})

_XGB_NONE_RESULT = {col: None for col in XGB_COLS}
_XGB_NONE_RESULT.update({'Xgb_status': None, 'Xgb_gen': XGB_GEN, 'Xgb_feat_ms': None,
                         'Xgb_predict_ms': None, 'Xgb_total_ms': None, 'Xgb_at': None})


def _load_xgb_meta() -> tuple:
    """Load + validate the ten _prep.json files (o54_engine.load_xgb_preps: model-3
    provenance, fixed round counts, identical feature lists, preprocessing groups) and
    verify _XGB_FEATURE_SOURCES covers every non-PCA feature. The BOOSTERS are loaded
    by the engine, in whichever process runs them. Raises on anything wrong — a trading
    process must not run a model that would silently score NaN."""
    entries = o54.load_xgb_preps()
    for e in entries:
        feats = e['prep']['features_used']
        missing = [f for f in feats
                   if not (f.startswith('hd_pca_') or f.startswith('bd_pca_'))
                   and f not in _XGB_FEATURE_SOURCES]
        if missing:
            raise RuntimeError(
                f"{e['cfg']}/{e['stem']}: no _XGB_FEATURE_SOURCES entry for {missing} — "
                f"a retrain changed the feature allowlist; add the source(s) before "
                f"running live")
        body = [f for f in feats if f.startswith('bd_pca_')]
        if body:
            raise RuntimeError(f"{e['cfg']}/{e['stem']} reads body PCA features {body} — "
                               f"5.4 computes no body embedding; this is the wrong loader")
    return tuple(entries)


def _xgb_raw_row(finbert_val: dict, finmarba_val: dict, flang_val: dict, earn_val: dict,
                 row_vals: dict, univ_row: dict, time_to_open_h) -> dict:
    """The RAW feature row for ONE ticker: every _XGB_FEATURE_SOURCES feature under its
    trained name, plus the three sentinel columns. build_row_features (in the engine)
    does the casting, sentinel substitution, winsorisation and categoricals exactly as
    offline, so values go in raw. Per-TICKER: the text half is shared across a 2-ticker
    PR, the structured half (Float, atr_pct_14, ...) is not."""
    sources = {'finbert': finbert_val, 'fmb': finmarba_val, 'flang': flang_val,
               'earn': earn_val, 'row': row_vals, 'univ': univ_row,
               'calc': {'TimeToOpen': time_to_open_h}}
    raw_row = {feat: sources[src].get(key)
               for feat, (src, key) in _XGB_FEATURE_SOURCES.items()}
    # A missing/blank sentinel cell becomes 44444 — what it means upstream ("IBKR had no
    # bars") — so the stored substitution applies.
    for col, (src, key) in _XGB_SENTINEL_COLS.items():
        v = _coerce_float(sources[src].get(key))
        raw_row[col] = o54.XGB_SENTINEL if v is None else v
    return raw_row


def _score_xgb(symbol: str, raw_row: dict, embed_val: dict, cols: tuple, prio: int,
               wait_s: float) -> dict:
    """Predict `cols` for ONE ticker through the engine. Never raises.

    Returns the requested prediction columns plus 'Xgb_status' and the engine's
    '_feat_ms' / '_predict_ms'; an empty `cols` returns {} (nothing asked, nothing to
    report). The ge* outputs are UNCALIBRATED ranking scores (scale_pos_weight ~11.5-12.5
    on ge50) — not probabilities; se/q80 come back inverted to PERCENT."""
    if not cols:
        return {}
    if not _xgb_meta:
        return {'Xgb_status': 'not_loaded'}
    if not _xgb_universe_ok:
        # The universe TSV lacks the pipeline-3 block: 12 of the 25 features are absent,
        # and a score built without them is not comparable to a real one.
        return {'Xgb_status': 'disabled_universe'}
    if embed_val.get('hd_pca') is None:
        return {'Xgb_status': 'no_embedding'}
    try:
        r = _engine.submit(prio, 'xgb', raw_row, embed_val['hd_pca'],
                           tuple(cols)).result(timeout=wait_s)
    except FutureTimeout:
        logger.error(f"[Xgb] {symbol}: the engine did not return {len(cols)} "
                     f"prediction(s) within {wait_s:.0f}s")
        return {'Xgb_status': 'timeout'}
    except Exception as exc:                                  # noqa: BLE001
        logger.error(f"[Xgb] {symbol}: scoring error: {exc}")
        return {'Xgb_status': 'error'}
    out = {c: r.get(c) for c in cols}
    out['Xgb_status'] = 'ok'
    out['_feat_ms'] = r.get('_feat_ms')
    out['_predict_ms'] = r.get('_predict_ms')
    return out


def _compute_time_to_open(news_dict: dict):
    """TimeToOpen — hours from the 04:00 ET ETH open of the session this PR lands in.
    Negative means the market has not opened yet. Never raises; None on any failure.

    Reference session = ArrivalDate itself when that is an XNYS session AND
    ArrivalTime < 20:00, else the NEXT session — a FORWARD lookup, not a
    --days-prior anchor. Reuses dayOfWeek_addOn's own functions so this cannot drift
    from the offline column; the lookup is a bisect over a prebuilt session list,
    ~2 microseconds.

    ⚠ The 20:00 cutoff is fixed and does NOT shrink to 17:00 on early-close
    half-days, matching the offline column exactly. 2026-11-27 and 2026-12-24 are the
    next two in range: a 17:30 PR there reads +13.50 instead of rolling forward."""
    try:
        with _sessions_lock:
            sessions = _sessions
        if sessions is None:
            return None
        arrival_date = datetime.strptime(news_dict['ArrivalDate'], '%Y-%m-%d').date()
        arrival_time = _dow_mod.parse_arrival_time(news_dict['ArrivalTime'])
        if arrival_time is None:
            return None
        return time_to_open(arrival_date, arrival_time, sessions)
    except Exception as exc:
        logger.warning(f"[TimeToOpen] {news_dict.get('Symbol')}: {exc}")
        return None


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
    'BroadClass':        _FieldSpec(_KIND_STR, 'earn', 'BroadClass',   None, None, True),
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
    # ── 5.4: the XGBoost leg — model-3 (held-out) and model-3FT (full refit) ─────
    # Naming ANY of these in a rule makes STEP-1b wait for the leg (_XGB_GATE_SOURCES)
    # and puts that booster on the pre-decision path (_gate_xgb_cols); every other
    # booster runs after the send. ⚠ se_* and q80_* are in PERCENT, not 0-1 — a rule
    # reading `se_m3 >= 0.3` means 0.3%, so they get no [0,1] range. The six ge* are
    # 0-1 but UNCALIBRATED ranking scores: pick a cut from a measured quantile of the
    # tape, never from what the number looks like. Only the held-out `_m3` twin has an
    # honest performance estimate; `_m3FT` trained on every row.
    # (2026-09-24: the *_m2 / *_m2FT fields and the DeBERTa / TradeHero / FinancialB
    # fields left with the calls that produced them. A rules file naming one is an
    # unknown field — a hard fail at load, by design.)
    'se_m3':             _FieldSpec(_KIND_NUM, 'xgb', 'se_m3',        None, None, True),
    'q80_m3':            _FieldSpec(_KIND_NUM, 'xgb', 'q80_m3',       None, None, True),
    'ge20_m3':           _FieldSpec(_KIND_NUM, 'xgb', 'ge20_m3',       0.0, 1.0, True),
    'ge35_m3':           _FieldSpec(_KIND_NUM, 'xgb', 'ge35_m3',       0.0, 1.0, True),
    'ge50_m3':           _FieldSpec(_KIND_NUM, 'xgb', 'ge50_m3',       0.0, 1.0, True),
    'se_m3FT':           _FieldSpec(_KIND_NUM, 'xgb', 'se_m3FT',      None, None, True),
    'q80_m3FT':          _FieldSpec(_KIND_NUM, 'xgb', 'q80_m3FT',     None, None, True),
    'ge20_m3FT':         _FieldSpec(_KIND_NUM, 'xgb', 'ge20_m3FT',     0.0, 1.0, True),
    'ge35_m3FT':         _FieldSpec(_KIND_NUM, 'xgb', 'ge35_m3FT',     0.0, 1.0, True),
    'ge50_m3FT':         _FieldSpec(_KIND_NUM, 'xgb', 'ge50_m3FT',     0.0, 1.0, True),
    # Hours from the 04:00 ET ETH open of this PR's session. NEGATIVE means the
    # market has not opened yet, so `TimeToOpen >= 0` reads "already in session".
    # alarm=False: a blank here is a calendar/parse gap, not a dead model.
    'TimeToOpen':        _FieldSpec(_KIND_NUM, 'calc', 'TimeToOpen', -100.0, 24.0, False),
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
    global _trigger_rules, _trigger_fields, _gate_needs_xgb, _gate_xgb_cols
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

    # ── does the gate depend on the XGBoost leg — and on which boosters? ──────
    # Derived from the rules FILE, so turning the leg into a trade input is an edit plus
    # a restart — the same way every other gate change is made — and never a code
    # change or a silent default. The trigger is any field whose SOURCE is part of the
    # leg (a prediction, or TimeToOpen).
    # 5.4: _gate_xgb_cols is the subset of the ten predictions the rules read. ONLY those
    # boosters run before STEP-1b; the rest of the family runs after the send.
    _gate_needs_xgb = any(spec.source in _XGB_GATE_SOURCES
                          for _name, spec in _trigger_fields)
    _read = {spec.key for _n, spec in _trigger_fields if spec.source == 'xgb'}
    _gate_xgb_cols = tuple(c for c in XGB_COLS if c in _read)
    if _gate_needs_xgb:
        _using = sorted({n for n, sp in _trigger_fields
                         if sp.source in _XGB_GATE_SOURCES})
        logger.warning(
            "[Gate] XGB IS GATE-BLOCKING — %d rule field(s) come from the XGBoost leg "
            "(%s), so STEP-1b WAITS for model-3's headline embedding and %s before it "
            "is sent. model-3 costs ~32 ms of model time per PR (model-2 cost ~650 ms); "
            "the other booster(s) run after the send. Remove those field(s) from %s to "
            "go back to non-blocking.",
            len(_using), ', '.join(_using),
            (f"the {', '.join(_gate_xgb_cols)} booster(s)" if _gate_xgb_cols
             else "TimeToOpen only"),
            os.path.basename(path))

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
                       ('earn', 'PrClassifier/earnings'), ('row', 'universe row'),
                       ('xgb', 'XGBoost model-3'), ('calc', 'TimeToOpen')):
        used = sorted({f"{r.name}{' (REQUIRE)' if r.kind == _RULE_REQUIRE else ''}"
                       for r in _trigger_rules
                       for c in r.conds if c.spec.source == src})
        if used:
            logger.info("[Gate]   %s feeds rule(s): %s", label, ', '.join(used))
        elif src not in _GATE_NO_WARN_SOURCES:
            logger.warning("[Gate]   %s is scored on EVERY PR but NO rule reads it — "
                           "it costs latency and gates nothing", label)
    # The leg not feeding a rule is a legitimate state, not a fault, so it gets a
    # statement of fact rather than the warning above.
    logger.info("[Gate]   XGBoost leg: %s",
                (f"GATE-BLOCKING — STEP-1b waits for "
                 f"{', '.join(_gate_xgb_cols) or 'TimeToOpen'}; the other booster(s) "
                 f"run after the send") if _gate_needs_xgb else
                "observational (no rule reads it; STEP-1b does not wait for it)")


# ─── Trade-trigger gate ───────────────────────────────────────────────────────

# Keys resolvable from the PR classifier alone, on the feed thread, before any model
# has run. _leg_priority may only reason about REQUIRE rules built purely from these.
_PRIORITY_KNOWABLE_KEYS = frozenset({'FineClass', 'BroadClass'})


def _leg_priority(fine_class, broad_class) -> int:
    """Queue priority for one article's XGBoost leg: _PRIO_TRADEABLE or _PRIO_CORPUS.

    LOW priority (_PRIO_CORPUS) iff a REQUIRE precondition that reads ONLY the PR
    classifier can already be shown to FAIL. A precondition is AND-ed over the whole
    gate, so once one fails this PR cannot trade no matter what any model says later
    — its leg exists only to fill the research corpus and must not delay a PR whose
    trade is still live.

    ⚠ This NEVER skips work and never changes a verdict. Every PR is still scored and
    every TSV column is still written; only the ORDER the single worker serves them
    in changes. Measured on the 08:30 burst of 2026-09-21: 6 of 9 PRs were
    ClassWhitelist:BLOCK, and moving them behind the other 3 takes the last tradeable
    PR from 7542 ms to 1881 ms.

    Derived from the LOADED rules, not from a hardcoded class list, so it tracks
    orchestrator_trigger_rules.txt: delete the whitelist and every PR becomes
    _PRIO_TRADEABLE, which is exactly right. Rules naming a key we cannot know yet
    (EarningScore, any model score) are skipped — being unable to prove a PR is dead
    means treating it as live.

    `fine_class` is None when the classifier itself failed. That is NOT a reason to
    deprioritise: a dead classifier closes the whole gate and the alarm should fire
    on a normally-scheduled row, not on one parked behind the corpus queue."""
    if not _gate_needs_xgb or fine_class is None:
        return _PRIO_TRADEABLE
    known = {'FineClass': fine_class, 'BroadClass': broad_class}
    for rule in _trigger_rules:
        if rule.kind != _RULE_REQUIRE:
            continue
        if any(c.spec.key not in _PRIORITY_KNOWABLE_KEYS for c in rule.conds):
            continue                      # not decidable yet -> assume still live
        for cond in rule.conds:
            value = known.get(cond.spec.key)
            if value is None or not _cond_true(cond, value):
                return _PRIO_CORPUS       # precondition fails -> can never trade
    return _PRIO_TRADEABLE


def _task_priority(fine_class, broad_class, tradeable: bool) -> tuple:
    """(gate_prio, leg_prio) for one article's engine tasks.

    gate_prio — the gate's three headline models and kw15. _PRIO_CORPUS exactly when NO
      ticker of this PR will ever be sent a STEP-1b decision: the arm was blocked by an
      orchestrator excluded string (15.5% of rows 08-01..09-24) or no ticker is in the
      universe. Such a row is still fully scored and written — after every PR a clerk
      is waiting on. (A 2-ticker PR with one armed ticker is tradeable.)
    leg_prio — model-3's embedding and the gate boosters. Additionally _PRIO_CORPUS when
      a classifier-only REQUIRE precondition already fails (_leg_priority, 5.3's logic):
      that row's decision is BAD without the leg, which _gate_early_decision proves, so
      only its TSV row waits for the leg.

    ⚠ NEVER skips work and never changes a verdict — only the ORDER the engine serves
      tasks in."""
    if not tradeable:
        return _PRIO_CORPUS, _PRIO_CORPUS
    return _PRIO_TRADEABLE, _leg_priority(fine_class, broad_class)


def _gate_early_decision(finbert_val: dict, finmarba_val: dict, flang_val: dict,
                         kw_val: dict, earn_val: dict, row_vals: dict):
    """Can the gate be decided WITHOUT the XGBoost leg? True / False / None.

    Returns the final verdict when it is already forced, else None meaning "the leg
    is decisive, you must wait for it". **Whatever this returns, _evaluate_trigger
    over the complete inputs is guaranteed to agree** — the caller re-runs the real
    gate once the leg lands and logs at ERROR if it ever does not.

    The guarantee rests on the gate being MONOTONE in each direction:
      - `is_ok = (every REQUIRE holds) AND (any FIRE rule holds)`.
      - A REQUIRE rule with a non-leg condition already FALSE can never hold, and it
        is AND-ed over everything -> the answer is BAD whatever the leg says.
      - If no FIRE rule can still hold, there is nothing left to fire -> BAD.
      - If every REQUIRE already holds outright AND some FIRE rule already holds
        outright, adding a disjunct cannot unmake it -> OK.
      - Anything else is genuinely undecided.

    A condition is treated as PENDING exactly when its field's source is in
    _XGB_GATE_SOURCES; every other None makes its condition false, which is what
    _evaluate_trigger does too.

    ⚠ WHY THIS EXISTS. Since 2026-09-20 the leg is gate-blocking, so STEP-1b waited
    ~900 ms on EVERY PR — including rows that fire on CORE or EARNINGS and rows the
    class whitelist had already blocked, where the score could not change anything.
    Measured over 890 rows of tape: 65.7% decidable BAD at once, 3.7% decidable OK at
    once, only 30.6% genuinely need the score. The clerk aborts a trade whose
    sentiment is more than SENTIMENT_TIMEOUT_S = 2.0 s late
    (../x-wing-mole/clerk-1.2.py), which is the budget this buys back."""
    # ⚠ Key names MUST match _evaluate_trigger's own `sources` dict exactly — in
    #   particular FinMarBa is keyed 'fmb', not 'finmarba'. A mismatch here is a
    #   KeyError on the trade path, which is why the tape replay sweeps every rule.
    sources = {'finbert': finbert_val, 'fmb': finmarba_val, 'flang': flang_val,
               'kw': kw_val, 'earn': earn_val, 'row': row_vals}
    vals = {name: sources[spec.source].get(spec.key)
            for name, spec in _trigger_fields
            if spec.source not in _XGB_GATE_SOURCES}

    require_all_hold = True
    fire_holds = fire_possible = False
    for rule in _trigger_rules:
        pending = dead = False
        for cond in rule.conds:
            if cond.spec.source in _XGB_GATE_SOURCES:
                pending = True            # value not in yet — cannot judge this one
                continue
            value = vals.get(cond.field)
            if value is None or not _cond_true(cond, value):
                dead = True
                break
        if rule.kind == _RULE_REQUIRE:
            if dead:
                return False              # AND-ed precondition already lost
            if pending:
                require_all_hold = False  # might hold, might not
        else:
            if dead:
                continue
            fire_possible = True
            if not pending:
                fire_holds = True
    if not fire_possible:
        return False                      # no rule left that could ever fire
    if require_all_hold and fire_holds:
        return True
    return None



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
                      rules: tuple = None,
                      xgb_val: dict = None, calc_val: dict = None) -> tuple:
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
    # The two leg sources are APPENDED with defaults, never inserted: the first six stay
    # positional, so anything that calls this the 5.2 way keeps working and simply sees
    # the leg's fields as absent. A rule naming one of them on such a call reads MISSING,
    # which is honest — that caller genuinely did not score it. (5.3's 'hdx' and 'body'
    # sources left with model-2's extra encoders.)
    sources = {'finbert': finbert_val, 'fmb': finmarba_val, 'flang': flang_val,
               'kw': kw_val, 'earn': earn_val, 'row': row_vals,
               'xgb': xgb_val or {}, 'calc': calc_val or {}}
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
    # ── the XGBoost leg (5.3: 2026-09-18; model-3 since 5.4, 2026-09-24) ─────────
    # Appended LAST, so every column above keeps its ordinal and anything that reads
    # this file positionally up to col 42 is unaffected.
    #
    # BroadClass is PrClassifier's grouped rollup, the companion to FineClass — it falls
    # out of the SAME classify_pr call, so the two can never describe different texts,
    # and it costs nothing. TimeToOpen is hours from the 04:00 ET ETH open of the PR's
    # own session — NEGATIVE means the market has not opened yet.
    'BroadClass', 'TimeToOpen',
    # ⚠ 5.4 REMOVED 30 COLUMNS (90 -> 62 with the two it added): the DeBERTa_*_hd,
    # TradeHero_*_hd, FinancialB_*_bd and TradeHero_*_bd blocks (7 each) plus
    # Xgb_hdx_ms / Xgb_body_ms — model-3 makes none of those calls. The writer rotates
    # a same-day file whose header differs (`_rotated.tsv`), so a 5.3 file is never
    # appended to.
    #
    # The predictions — model-3 then model-3FT, the same five kinds each, on the SAME
    # 25-feature vector. m3 trained on rows < 2026-07-28 and was val-scored once; m3FT
    # refit the same configuration on every row, so it has NO held-out estimate.
    # ⚠ se_* and q80_* are in PERCENT (predicted fromPR_High(%)), not 0-1.
    # ⚠ The six ge* ARE 0-1 but are NOT calibrated probabilities (scale_pos_weight
    # multiplies the modelled odds). They rank; they do not state odds.
    'se_m3', 'q80_m3', 'ge20_m3', 'ge35_m3', 'ge50_m3',
    'se_m3FT', 'q80_m3FT', 'ge20_m3FT', 'ge35_m3FT', 'ge50_m3FT',
    # ⚠ Xgb_status keeps a blank prediction unambiguous: 'ok'; 'partial' (the gate's
    # booster(s) scored before STEP-1b, the rest failed after); 'late' (the gate's
    # booster(s) were NOT in time for STEP-1b — the gate read them as MISSING — but the
    # values landed afterwards and are in the row); 'disabled_universe' (the universe
    # TSV lacks the pipeline-3 block); 'no_embedding'; 'timeout'; 'not_loaded';
    # 'error'. A blank prediction under 'ok' would be a bug.
    # Xgb_gen names the generation that scored the row (m3:int8_c200_s04), so a mixed
    # tape can always be told apart.
    'Xgb_status', 'Xgb_gen',
    # Timings, ms. Xgb_embed_ms = the INT8 headline embedding (the leg's only model
    # call). Xgb_feat_ms / Xgb_predict_ms = the PRE-DECISION booster work (the gate's
    # column(s); on a row that did not wait for the leg, the whole family).
    # Xgb_total_ms = WALL CLOCK from reception to that prediction landing — the number
    # that answers "how late was the score?", queueing included.
    'Xgb_embed_ms', 'Xgb_feat_ms', 'Xgb_predict_ms', 'Xgb_total_ms',
    # Completion stamp on the connector's fixed UTC-4 clock (the *_at_* convention).
    'Xgb_at',
    # ── 5.4: the SLO column ─────────────────────────────────────────────────────
    # Reception (the feed line leaving the socket buffer) -> STEP-1b sent to the clerk,
    # in ms. The operator's budget (2026-09-24): under 400 ms for EVERY armed PR, bursts
    # included. Blank when no decision was sent (never armed, or arm rejected).
    'Decision_ms',
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


def _build_univ_index(df) -> dict:
    """symbol -> row dict for the whole universe df: the FIRST row per symbol (the row
    5.3's `.iloc[0]` / `match[0]` picked), cells RAW (strings, NaN, 'skipped_nan'),
    keyed by the raw Symbol cell exactly as 5.3 compared it. Built once per (re)load."""
    idx = {}
    if df is None or 'Symbol' not in df.columns:
        return idx
    for rec in df.to_dict('records'):
        sym = rec.get('Symbol')
        if sym not in idx:
            idx[sym] = rec
    return idx


def _universe_cell(symbol: str, col: str):
    """Raw cell for (symbol, col) from the daily universe, or None if the universe is
    unloaded or the symbol/column is absent. 5.4: one dict lookup (see _univ_index)."""
    row = _univ_index.get(symbol)
    return None if row is None else row.get(col)


def _universe_row(symbol: str) -> dict:
    """Every cell for `symbol` — {} if the universe is unloaded or the symbol absent.

    ⚠ Returns the SHARED index row: read it, never mutate it. Values are RAW (strings,
    NaN, 'skipped_nan'); every caller goes through _coerce_float, exactly as with
    _universe_cell."""
    return _univ_index.get(symbol) or {}


def _update_xgb_universe_ok(df) -> None:
    """Re-evaluate _xgb_universe_ok after a daily universe reload, logging only on a
    TRANSITION so a persistent outage does not print the same warning every day at
    03:00 while a genuine flip is impossible to miss."""
    global _xgb_universe_ok
    was = _xgb_universe_ok
    ok, missing = _universe_supports_xgb(df)
    _xgb_universe_ok = ok
    if ok and not was:
        logger.info("[Universe] XGBoost feature columns are BACK — predictions "
                    "re-enabled.")
    elif not ok and was:
        logger.warning(
            "[Universe] the reloaded universe TSV is missing %d XGBoost feature "
            "column(s) %s — XGB PREDICTIONS DISABLED (Xgb_status='disabled_universe'). "
            "Trading is unaffected unless a rule reads the leg.", len(missing), missing)


def _universe_supports_xgb(df) -> tuple:
    """(ok, missing_cols) — does this universe df carry the XGBoost feature columns?

    Only daily_universe_pipeline-3 files (147 cols, first written 2026-09-17) have
    them; every earlier file has 38 columns and none of the market-state block. A
    stale file is therefore not a partial degradation, it is 12 of model-3's 25
    features gone at once, which is why it is detected explicitly rather than left to
    read as a row of NaNs.

    The column list is DERIVED from _XGB_FEATURE_SOURCES, so this checks exactly what
    the loaded models read and nothing more — model-1 needed 13 of these, model-2 7,
    model-3 12, and none of those numbers is written down anywhere else."""
    if df is None:
        return False, list(XGB_UNIVERSE_COLS)
    missing = [c for c in XGB_UNIVERSE_COLS if c not in df.columns]
    return (not missing), missing


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
    """Load the most recent stocks_universe_YYYY-MM-DD.tsv (or --universe-file) into
    _iti_df, and rebuild the symbol index every per-ticker lookup reads."""
    global _iti_df, _univ_index
    if UNIVERSE_FILE_OVERRIDE:
        path = str(UNIVERSE_FILE_OVERRIDE)
    else:
        pattern = os.path.join(UNIVERSE_DATA_DIR, 'stocks_universe_????-??-??.tsv')
        matches = sorted(glob.glob(pattern))
        if not matches:
            logger.warning(f"[ITI] No dated universe TSV found in {UNIVERSE_DATA_DIR} — "
                           f"ITI lookups will use default")
            return
        path = matches[-1]
    try:
        df = pd.read_csv(path, sep='\t')
        idx = _build_univ_index(df)
        with _iti_df_lock:
            _iti_df = df
            _univ_index = idx
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


def _refresh_sessions() -> None:
    """(Re)build the XNYS session index behind TimeToOpen — a year either side of
    today, so a bisect always lands inside it and the index survives a year boundary.

    Costs ~0.1 s (pandas_market_calendars), so it is rebuilt once a day beside the
    universe reload rather than per row; the per-PR lookup is then a bisect, ~2 us.
    Never raises: a calendar failure leaves the previous index in place (or None,
    which makes TimeToOpen blank) rather than stopping the reload thread."""
    global _sessions
    try:
        today = datetime.now(tz=ZoneInfo('America/New_York')).date()
        sessions = Sessions(today.replace(year=today.year - 1),
                            today.replace(year=today.year + 1))
        with _sessions_lock:
            _sessions = sessions
        logger.info("[TimeToOpen] session index rebuilt: %d sessions, %s .. %s",
                    len(sessions.days), sessions.first, sessions.last)
    except Exception as exc:
        logger.error("[TimeToOpen] could not build the session index: %s — "
                     "TimeToOpen will be blank until the next reload", exc,
                     exc_info=True)


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
            # 5.3: the universe file changed, so re-check whether it still carries
            # the XGBoost feature columns — a pipeline-3 run that failed overnight
            # shows up HERE, not only at startup, and flips the leg off (or back on)
            # for the rest of the day.
            _update_xgb_universe_ok(df)
            # And rebuild the session index, so TimeToOpen stays correct across a
            # year boundary without a restart.
            _refresh_sessions()
            _last_iti_reload_date = now.date()

        stop_event.wait(timeout=60.0)


# ─── Per-ticker collector — runs on collect_worker thread ────────────────────

def _collect_and_log(news_dict: dict, tickers: list, symbol: str,
                     f_finbert, f_finmarba, f_flang, f_kw, f_earn, f_arm,
                     arm_blocked: bool,
                     f_emb=None, recv_t0=None, leg_prio: int = _PRIO_TRADEABLE) -> None:
    """Resolve the scoring futures, evaluate the trade gate, complete the clerk
    handshake (wait for the STEP-1a arm reply, then send the STEP-1b sentiment), finish
    the XGBoost leg, and write the TSV row.

    Ordering is load-bearing: we wait on the arm future *before* sending sentiment so
    the clerk has registered the session — required both for the OK gate and for the
    BAD early-release to land. Every scoring future was submitted together in
    _on_row_built and runs in parallel inside the engine, so resolving them back to
    back here costs ~max(model), not the sum; each has a hard ceiling
    (FLANG_SCORE_WAIT_SEC / KW_SCORE_WAIT_SEC / EARN_SCORE_WAIT_SEC / XGBLEG_WAIT_SEC)
    so a hung model can never stall the trade decision.

    ⚠ 5.4 — THE XGBoost LEG IS SPLIT AT THE DECISION.
      BEFORE STEP-1b, only when a loaded rule reads the leg (_gate_needs_xgb) AND
        _gate_early_decision cannot settle the row without it: model-3's headline
        embedding + ONLY the boosters the rules read (_gate_xgb_cols — today ge50_m3,
        47 trees on one feature build).
      AFTER STEP-1b: every other booster — the whole family on a row that did not wait
        — at _PRIO_CORPUS. It fills the TSV; nothing trades on it.
    `recv_t0` is the time.monotonic() stamped when the feed line left the socket
    buffer: the start of both Xgb_total_ms and Decision_ms (the operator's SLO clock).

    The row is assembled at the END because ClerkSentiment and the XGBoost columns are
    produced on either side of the send; one literal containing every cell is what
    keeps the row impossible to half-fill."""
    news_id = news_dict['ID']

    # 1a) Headline FinBERT (ProsusAI) — an engine future
    try:
        finbert_val = f_finbert.result(timeout=60)
        finbert_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FinBERT-headliner error for id={news_id}: {exc}")
        finbert_val = {}
        finbert_completed_at = None

    # 1b) Headline FinMarBa (ran in parallel with FinBERT; supplies `indecisive`)
    try:
        finmarba_val = f_finmarba.result(timeout=60)
        fmb_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FinMarBa-headliner error for id={news_id}: {exc}")
        finmarba_val = {}
        fmb_completed_at = None

    # 1c) Headline FLANG-BERT (ran in parallel with both). A failure leaves {} → the
    #     conditions on it simply fail.
    try:
        flang_val = f_flang.result(timeout=FLANG_SCORE_WAIT_SEC)
        flang_completed_at = c3.format_millis_time(c3.now_fixed())
    except Exception as exc:
        logger.error(f"FLANG-headliner error for id={news_id}: {exc}")
        flang_val = {}
        flang_completed_at = None

    # 2) ge15 keyword scores (~ms; both fan-out tickers share the future)
    try:
        kw_val = _kw_from_task(f_kw.result(timeout=KW_SCORE_WAIT_SEC))
    except Exception as exc:
        logger.error(f"[KwScore] {symbol}: kw future failed/timed out for id={news_id}: {exc}")
        kw_val = dict(_KW_NONE_RESULT)

    # 2b) PR class + earnings score (~ms; both fan-out tickers share the future).
    #     A failure leaves the all-None dict → the earnings leg is simply false.
    try:
        earn_val = f_earn.result(timeout=EARN_SCORE_WAIT_SEC)
    except Exception as exc:
        logger.error(f"[EarnScore] {symbol}: earn future failed/timed out for id={news_id}: {exc}")
        earn_val = dict(_EARN_NONE_RESULT)

    # 2c) Per-ticker universe fields, resolved ONCE. They feed BOTH the gate (row fields
    #     are addressable from orchestrator_trigger_rules.txt) and the row below.
    #     5.4: each lookup is one dict get (_univ_index), not a locked pandas scan.
    #     Exchange keeps its payload-first / universe-fallback order. TickerCount counts
    #     EVERY ticker the article named, not just the in-universe ones.
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

    # 2d) The XGBoost leg's state. TimeToOpen is computed eagerly (a bisect, ~2 us) so a
    #     rule on it never waits for the embedding.
    leg = {'embed': dict(_EMBED_NONE_RESULT),
           'calc': {'TimeToOpen': _compute_time_to_open(news_dict)},
           'raw_row': None, 'prepared': False}
    xgb_out = dict(_XGB_NONE_RESULT)
    gate_part = {}          # what the pre-decision booster call returned ({} = not run)

    def _prepare_leg(wait_s: float):
        """Resolve the embedding future and assemble the raw feature row — ONCE."""
        if leg['prepared']:
            return
        leg['prepared'] = True
        try:
            if f_emb is not None:
                leg['embed'] = f_emb.result(timeout=wait_s)
        except Exception as exc:
            # One future carries the whole text half of the vector; without it the
            # predictions are suppressed with Xgb_status='no_embedding'.
            logger.error(f"[XgbLeg] {symbol}: embedding future failed/timed out for "
                         f"id={news_id}: {exc}")
        leg['raw_row'] = _xgb_raw_row(finbert_val, finmarba_val, flang_val, earn_val,
                                      row_vals, _universe_row(symbol),
                                      leg['calc']['TimeToOpen'])

    def _stamp(part: dict):
        xgb_out['Xgb_feat_ms'] = part.get('_feat_ms')
        xgb_out['Xgb_predict_ms'] = part.get('_predict_ms')
        if recv_t0 is not None:
            xgb_out['Xgb_total_ms'] = round((time.monotonic() - recv_t0) * 1000.0, 1)
        xgb_out['Xgb_at'] = c3.format_millis_time(c3.now_fixed())

    def _gate_xgb():
        """BEFORE STEP-1b: the embedding + only the boosters the loaded rules read."""
        nonlocal gate_part
        _prepare_leg(XGBLEG_WAIT_SEC)
        gate_part = _score_xgb(symbol, leg['raw_row'], leg['embed'], _gate_xgb_cols,
                               leg_prio, XGBLEG_WAIT_SEC)
        for c in _gate_xgb_cols:
            xgb_out[c] = gate_part.get(c)
        xgb_out['Xgb_status'] = gate_part.get('Xgb_status')
        _stamp(gate_part)
        if gate_part.get('Xgb_status') == 'ok':
            logger.info(f"[Xgb] {symbol} → gate "
                        + ' '.join(f"{c}={xgb_out[c]}" for c in _gate_xgb_cols)
                        + f" (feat {gate_part.get('_feat_ms')}ms + predict "
                          f"{gate_part.get('_predict_ms')}ms; "
                          f"{xgb_out['Xgb_total_ms']}ms from reception)")

    def _post_xgb():
        """AFTER STEP-1b: everything not scored yet, at corpus priority."""
        _prepare_leg(XGB_POST_WAIT_SEC)
        gate_status = gate_part.get('Xgb_status')
        done = set(_gate_xgb_cols) if gate_status == 'ok' else set()
        rest = tuple(c for c in XGB_COLS if c not in done)
        part = _score_xgb(symbol, leg['raw_row'], leg['embed'], rest, _PRIO_CORPUS,
                          XGB_POST_WAIT_SEC)
        for c in rest:
            xgb_out[c] = part.get(c)
        post_status = part.get('Xgb_status')
        if not gate_part:
            # Nothing ran before the decision: this call IS the row's leg.
            xgb_out['Xgb_status'] = post_status
            _stamp(part)
        elif gate_status == 'ok':
            xgb_out['Xgb_status'] = 'ok' if (post_status == 'ok' or not rest) else 'partial'
        elif post_status == 'ok' and gate_status in ('timeout', 'error'):
            # The gate read MISSING (too late), but the values exist now — record both.
            xgb_out['Xgb_status'] = 'late'
        # else: the pre-decision failure (no_embedding, disabled_universe, ...) stands —
        #       the first failure is the informative one.
        if post_status == 'ok':
            logger.info(
                f"[Xgb] {symbol} → m3 se={xgb_out['se_m3']} q80={xgb_out['q80_m3']} "
                f"ge20={xgb_out['ge20_m3']} ge35={xgb_out['ge35_m3']} "
                f"ge50={xgb_out['ge50_m3']} | m3FT se={xgb_out['se_m3FT']} "
                f"ge20={xgb_out['ge20_m3FT']} ge50={xgb_out['ge50_m3FT']}")

    # 2e) Does the leg actually DECIDE this row? _gate_early_decision proves,
    #     monotonically, when the verdict is already forced; only a genuinely undecided
    #     row waits for the leg (model-3: ~30 ms of model time, not model-2's ~650).
    early = (_gate_early_decision(finbert_val, finmarba_val, flang_val, kw_val,
                                  earn_val, row_vals) if _gate_needs_xgb else None)
    if _gate_needs_xgb and early is None:
        _gate_xgb()

    # 3) Trade gate from orchestrator_trigger_rules.txt — every REQUIRE precondition
    #    AND an OR of the ordinary rules → decision + label + Trigger cell
    is_ok, trigger_label, trigger_detail, missing_blocked = _evaluate_trigger(
        finbert_val, finmarba_val, flang_val, kw_val, earn_val, row_vals,
        xgb_val=xgb_out, calc_val=leg['calc'])
    # Log every gate input against its threshold, so a rejection can be diagnosed from
    # the log alone. This is the SAME string that lands in the TSV Trigger cell, so the
    # log can never drift from the row.
    def _log_gate(ok, detail, mb):
        if ok:
            return
        logger.info("[Gate] %s %s", symbol, detail)
        # A single dead model can block a branch while looking exactly like a quiet
        # news day — and the OR makes that quieter still, since the other branch keeps
        # trading. Escalate the case where a MISSING score is the only thing that
        # stopped this row, so logs/ shows a broken scorer instead of silence.
        if mb:
            logger.warning("[Gate] %s BLOCKED BY MISSING SCORE ONLY — a branch's "
                           "every evaluable condition passed; a model or future is "
                           "dead: %s", symbol, detail)

    # ⚠ On a short-circuited row the leg's cells are BLANKS, not its values, so `detail`
    #   would read ge50_m3:MISSING and `missing_blocked` would be an artefact of our own
    #   skipping. Defer the log to the re-evaluation at 5b.
    if early is None:
        _log_gate(is_ok, trigger_detail, missing_blocked)

    # 4) Build the row. Created/Arrival*/Author/Exchange are pass-throughs from the
    #    reception (all stamped on the connector's fixed UTC-4 clock). The Body CELL
    #    stores the RAW payload as RTPR sent it (HTML tags/entities kept, if any) with
    #    only whitespace runs collapsed so the TSV stays one-row-per-line — the SAME
    #    string kw15 scored, so an offline rescore of this TSV reproduces it exactly.
    _row_base = {
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
    #      armed        → "<fired rule names joined by '_', or BAD>:<reply status>"
    #      arm rejected → "skipped:arm_rejected"
    #      never armed  → "skipped:excluded_string" / "skipped:not_armed"
    #    (arm accept/reject detail stays in the log via _arm_clerk).
    _sent_status = None          # set only on the branch that actually sends STEP-1b
    decision_ms = None           # the SLO number: reception -> STEP-1b sent
    if f_arm is None:
        clerk_sentiment = ('skipped:excluded_string' if arm_blocked
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
            clerk_sentiment = 'skipped:arm_rejected'
        else:
            # accepted (session registered) or unknown (best-effort) → STEP 1b.
            if recv_t0 is not None:
                decision_ms = round((time.monotonic() - recv_t0) * 1000.0, 1)
            sentiment_reply = _send_sentiment(symbol, is_ok)
            _sent_status = sentiment_reply.get('status', 'no_reply') or 'no_reply'
            clerk_sentiment = f"{trigger_label}:{_sent_status}"
            if decision_ms is not None:
                logger.info("[Decision] %s %s sent %.1f ms after reception%s", symbol,
                            'OK' if is_ok else 'BAD', decision_ms,
                            ' — OVER THE 400 ms BUDGET' if decision_ms > 400 else '')

    # 5b) The rest of the XGBoost leg, after the decision: it delays only this TSV row.
    _post_xgb()

    if early is not None:
        # We answered the clerk without waiting. Now that the leg has landed, re-run the
        # SAME gate over the COMPLETE inputs, so the TSV keeps a full Trigger cell and
        # the dead-scorer alarm judges real values instead of the blanks our own
        # short-circuit left behind. Microseconds: no model runs again.
        f_ok, f_label, trigger_detail, missing_blocked = _evaluate_trigger(
            finbert_val, finmarba_val, flang_val, kw_val, earn_val, row_vals,
            xgb_val=xgb_out, calc_val=leg['calc'])
        if f_ok != is_ok:
            # MUST NEVER HAPPEN. _gate_early_decision only answers when the gate is
            # monotonically forced, so this is a logic bug, not a race. The SENT
            # decision stands — it is what the clerk acted on — and the row records
            # the full gate, so the disagreement stays auditable in both places.
            logger.error("[Gate] %s SHORT-CIRCUIT DISAGREED WITH THE FULL GATE — "
                         "sent is_ok=%s, full gate says %s. The clerk acted on the "
                         "SENT value. detail=%s", symbol, is_ok, f_ok, trigger_detail)
        elif f_label != trigger_label:
            # The leg fired a rule alongside the one we already had (e.g. XgbSolo
            # beside EARNINGS). The wire said OK either way; ClerkSentiment names
            # WHICH rules fired, so it takes the complete list.
            trigger_label = f_label
            if _sent_status is not None:
                clerk_sentiment = f"{trigger_label}:{_sent_status}"
        _row_base['Trigger'] = trigger_detail
        _log_gate(is_ok, trigger_detail, missing_blocked)

    # 6) Build and write the row.
    completed_dict = dict(_row_base)
    completed_dict['ClerkSentiment'] = clerk_sentiment
    completed_dict.update(xgb_out)
    completed_dict['BroadClass'] = earn_val.get('BroadClass')
    completed_dict['TimeToOpen'] = leg['calc'].get('TimeToOpen')
    completed_dict['Xgb_embed_ms'] = leg['embed'].get('_embed_ms')
    completed_dict['Decision_ms'] = decision_ms
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
    print(f"  ge50_m3 / Xgb_status         : {completed_dict['ge50_m3']} / {completed_dict['Xgb_status']}")
    print(f"  Trigger                      : {completed_dict['Trigger']}")
    print(f"  Trigger legs                 : {trigger_label}")
    print(f"  ClerkSentiment               : {completed_dict['ClerkSentiment']}")
    print(f"  Decision_ms                  : {completed_dict['Decision_ms']}")
    print(f"{'='*60}\n")


# ─── Article reception — fed by the standalone connector's TCP feed ───────────

def _on_row_built(data: dict, row: dict, arrival_dt, recv_t0: float = None) -> None:
    """Article reception handler, called by _run_feed_client for every article
    line the standalone connector publishes (`data` = raw firehose payload,
    `row` = connector-built row, arrival_dt = when the connector received the
    firehose frame). The full article arrives inline, so 4.2's
    alert/pre-arm/release split stays collapsed into this single reception
    event: dedup, the two kept NW4 input filters (ticker count ≤ 2, NW4-level
    excluded strings → drop), then arm every in-universe ticker and fan out.

    Runs on the feed-reader (main) thread → stays lean: only dict/string
    filters and non-blocking submits; every model runs in the scoring engine.
    `data` and `row` are this process's own JSON copies.

    5.4: `recv_t0` is the time.monotonic() stamped when this line left the socket
    buffer (_run_feed_client) — the START of the operator's SLO clock (Decision_ms).
    A caller that has none (a tool) gets "now"."""
    global _seen_day
    if recv_t0 is None:
        recv_t0 = time.monotonic()
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

    # The earnings scorer is the one model that needs a SYMBOL: its two size-relative
    # point rules divide by float-cap. It gets the article's first in-universe ticker
    # and is still scored once per ARTICLE, so a 2-ticker PR's second ticker inherits
    # that denominator (MAX_TICKERS_PER_ARTICLE is 2; such PRs are rare). The lookup
    # itself happens on the pool thread, not here — this one must stay lean.
    primary_symbol = next((s for s in tickers if s in _inscope_set), tickers[0])
    # The PR classifier's body string is prepped ONCE, here on the feed thread, and the
    # class resolved right after: _task_priority needs it BEFORE anything is queued, and
    # the earnings task reuses it rather than classifying twice. Both are regex-only and
    # sub-ms once warm — a deliberate exception to "keep _on_row_built lean".
    body_200 = _prep_body_200(news_dict)
    preclass = _classify_article(news_dict['Headline'], body_200)

    # ── 5.4: every model task goes to the scoring engine, with a priority ─────
    # A PR is TRADEABLE when at least one of its tickers was just armed, i.e. a clerk
    # will wait for its STEP-1b. Everything else — an excluded-string headline, a PR
    # with no in-universe ticker — is still fully scored and written, but in the corpus
    # lane, behind every PR a clerk is waiting on.
    tradeable = bool(f_arm)
    gate_prio, leg_prio = _task_priority(*(preclass or (None, None)), tradeable)

    # The gate's three headline models, kw15 and model-3's embedding run once per
    # ARTICLE (shared futures — all text-only, identical for both tickers), submitted
    # together so they execute simultaneously inside the engine. Each scores EXACTLY
    # what 5.3 scored: the plain headline; for kw15 also the TSV Body cell string.
    body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
    f_finbert  = _engine.submit(gate_prio, 'prosus', headline)
    f_finmarba = _engine.submit(gate_prio, 'fmb', headline)
    f_flang    = _engine.submit(gate_prio, 'flang', headline)
    f_kw       = _engine.submit(gate_prio, 'kw15', headline, body_cell)
    f_emb      = _engine.submit(leg_prio, 'embed', headline)
    for _f, _label in ((f_finbert, 'FinBERT'), (f_finmarba, 'FinMarBa'),
                       (f_flang, 'FLANG'), (f_kw, 'KwScore'), (f_emb, 'Embed')):
        _f.add_done_callback(functools.partial(_log_task, label=_label,
                                               news_dict=news_dict))
    f_earn = _earn_executor.submit(_classify_and_score_earnings,
                                   news_dict, primary_symbol, body_200, preclass)
    if gate_prio == _PRIO_CORPUS or leg_prio == _PRIO_CORPUS:
        logger.info("[Engine] id=%s class=%s %s — corpus lane; engine queue depth %d",
                    art_id, (preclass or (None,))[0],
                    'no ticker will be sent STEP-1b' if not tradeable
                    else 'a REQUIRE precondition already fails',
                    _engine.qdepth())

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
            f_emb, recv_t0, leg_prio,
        )


# ─── Feed client (subscribes to the standalone RTPR_connector3) ───────────────

def _handle_feed_line(raw: bytes, recv_t0: float = None) -> None:
    """Dispatch one JSON line from the feed. Never raises — a malformed line or
    a handler bug must not kill the reception loop. `recv_t0` is the monotonic stamp of
    the socket read that completed this line (the SLO clock's start)."""
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
            _on_row_built(data, row, arrival_dt, recv_t0)
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
    FEED_SILENCE_TIMEOUT_SEC without a single byte means a dead/hung peer.

    5.4: every complete line is handed over with the monotonic time of the recv() that
    completed it — so in a burst the SECOND article's clock starts when it arrived, not
    after the first one's reception work finished."""
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
                    t_chunk = time.monotonic()
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
                            _handle_feed_line(line, t_chunk)
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

def _engine_spec() -> str:
    """The configured engine layout, as one log/--check-xgb string."""
    if ENGINE_KIND == 'procs':
        return (f"procs workers={ENGINE_WORKERS} intra_op={ENGINE_INTRA_OP or 'ORT-default'}"
                f" cpus={','.join(map(str, INFER_CPUS))}")
    return (f"threads/{ENGINE_POOLS} workers={ENGINE_WORKERS} "
            f"intra_op={ENGINE_INTRA_OP or 'ORT-default'} "
            f"cpus={','.join(map(str, INFER_CPUS))}")


def _in_trading_hours() -> bool:
    """Weekday 04:00-20:00 ET — the window the clerk and the gate are live in."""
    now = datetime.now(tz=ZoneInfo('America/New_York'))
    return now.weekday() < 5 and 4 <= now.hour < 20


def _log_engine_banner(pinned: set) -> None:
    """[Engine] startup block: the layout, the CPU placement and the CPU power state —
    the three things that decide burst latency, on the record in logs/."""
    st = o54.read_power_state()
    every = set(range(os.cpu_count() or 1))
    logger.info("[Engine] %s | process pinned to CPUs %s; left free for the clerk, "
                "Gateways and connector: %s", _engine_spec(), sorted(pinned),
                sorted(every - pinned) or 'none')
    logger.info("[Engine] CPU power: platform_profile=%s EPP=%s governor=%s "
                "min_perf_pct=%s no_turbo=%s", st['platform_profile'], st['epp'],
                st['governor'], st['min_perf_pct'], st['no_turbo'])
    if EXPECT_PERF_MODE and not o54.power_state_is_performance(st) and _in_trading_hours():
        logger.warning("[Engine] CPU PERFORMANCE MODE IS OFF during trading hours — the "
                       "burst benchmark measured it matters. Run: sudo "
                       "%s/perf_mode.sh on", Path(__file__).resolve().parent)


def _smoke_engine() -> None:
    """One call per task kind through the REAL engine path (routing, priorities, and for
    the process engine the worker pipes), with hard-fail checks — 5.3's per-model smoke
    tests, now behind the engine. The goldens self-check inside _engine.start() already
    proved the VALUES are 5.3's; this proves the plumbing delivers them."""
    hd = "Acme Corp announces FDA approval of its lead drug candidate"
    res = {k: _engine.submit(_PRIO_TRADEABLE, k, hd).result(timeout=120)
           for k in ('prosus', 'fmb', 'flang')}
    for k, r in res.items():
        if not all(key in r for key in ('pos', 'neg', 'ss', 'lab')):
            raise RuntimeError(f"engine task {k!r} returned an unexpected shape "
                               f"({sorted(r)}) — refusing to start")
    kw = _engine.submit(_PRIO_TRADEABLE, 'kw15', "startup self-check",
                        "startup self-check").result(timeout=120)
    for name in ('kw15_proba_bd', 'kw15_proba_hd'):
        v = kw.get(name)
        if not (isinstance(v, float) and 0.0 <= v <= 1.0):
            raise RuntimeError(f"ge15 {name} returned {v!r}, not a probability; "
                               f"refusing to start")
    emb = _engine.submit(_PRIO_TRADEABLE, 'embed', hd).result(timeout=120)
    if len(emb.get('hd_pca') or []) != o54.M3_PCA_WIDTH:
        raise RuntimeError(f"model-3 PCA returned {len(emb.get('hd_pca') or [])} "
                           f"components, expected {o54.M3_PCA_WIDTH} — refusing to start")
    raw = o54.synthetic_raw_row(_xgb_meta[0]['prep']['features_used'])
    x = _engine.submit(_PRIO_TRADEABLE, 'xgb', raw, emb['hd_pca'],
                       XGB_COLS).result(timeout=120)
    bad = [c for c in XGB_COLS
           if not isinstance(x.get(c), float) or not math.isfinite(x[c])]
    if bad:
        raise RuntimeError(f"model-3 synthetic row scored no finite value for {bad} — "
                           f"refusing to start")
    logger.info("[Engine] smoke OK — Prosus_ss=%.4f FinMarBa_ind=%.4f FLANG_ss=%.4f "
                "kw15=%.6f/%.6f hd_pca[3]=%.4f ge50_m3=%.6f ge50_m3FT=%.6f "
                "(embed %.1f ms, xgb feat %.1f + predict %.2f ms)",
                res['prosus']['ss'], res['fmb']['ind'], res['flang']['ss'],
                kw['kw15_proba_bd'], kw['kw15_proba_hd'], emb['hd_pca'][3],
                x['ge50_m3'], x['ge50_m3FT'], emb['_embed_ms'], x['_feat_ms'],
                x['_predict_ms'])


def main():
    global _inscope_set, _nw_excluded_strings, _excluded_strings, _last_iti_reload_date
    global _earn_params, _engine, _xgb_meta, _xgb_universe_ok
    logger.info("Orchestrator5.4 starting...")

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 0) CPU placement FIRST — before any model creates a thread, so every thread this
    #    process ever starts inherits the mask (see INFER_CPUS: core 0 stays free).
    _pinned = o54.pin_process(INFER_CPUS)
    _log_engine_banner(_pinned)

    # The gate, BEFORE any model loads. Deliberately first: the rules file is the one
    # artifact a human hand-edits, so it is the one most likely to be wrong, and paying
    # the model loads only to learn you typed `kw15_body` is a bad loop at 06:00
    # before the open. Raises RuntimeError and stops the process on any problem.
    _load_trigger_rules(TRIGGER_RULES_FILE)
    # Terse now, full echo after pre-warm — so the gate is on the record even if a
    # model load dies before we reach the [Gate] ACTIVE block.
    logger.info("[Gate] Loaded %d rule(s) from %s: %s", len(_trigger_rules),
                TRIGGER_RULES_FILE,
                ', '.join(f"{r.name}{' (REQUIRE)' if r.kind == _RULE_REQUIRE else ''}"
                          for r in _trigger_rules))

    # ── model-3's artifacts: byte identity (md5), then the preps + feature coverage.
    #    Every check hard-fails: a trading process must not run on the wrong artifacts.
    logger.info("Checking model-3 artifacts (md5 of the INT8 embedder + PCA basis)...")
    o54.check_m3_artifacts()
    _xgb_meta = _load_xgb_meta()
    logger.info("model-3 preps OK — %d booster(s) in %d preprocessing group(s), "
                "%d features, trees %s.", len(_xgb_meta),
                len({e['group'] for e in _xgb_meta}),
                len(_xgb_meta[0]['prep']['features_used']),
                ', '.join(f"{e['col']}={e['prep']['n_rounds']}" for e in _xgb_meta))

    # ── The scoring engine: loads + warms the gate's three headline models, kw15,
    #    model-3's INT8 embedder + PCA and the ten boosters (here or in its workers),
    #    then proves every output digit against bench/o54_goldens.json. Hard-fails.
    logger.info("Starting the scoring engine (%s)...", _engine_spec())
    _engine = o54.make_engine(kind=ENGINE_KIND, pools=ENGINE_POOLS,
                              workers=ENGINE_WORKERS, intra_op=ENGINE_INTRA_OP,
                              cpus=INFER_CPUS, spinning=ENGINE_SPINNING, logger=logger)
    _engine.start()
    logger.info("[Engine] ready — %s", _engine.describe())
    _smoke_engine()

    # Earnings leg: load the calibrated score params ONCE (never in the hot path — the
    # default path re-reads the JSON per call) and HARD-FAIL on any problem. This leg
    # can fire trades on its own, so a classifier that silently stopped recognising
    # Earnings_Results, or a scorer that returned something other than points, must
    # stop the process rather than quietly remove a whole trade branch.
    logger.info("Pre-loading PR classifier + earnings scorer...")
    _earn_params = earn_load_params()
    _smoke_hl = "Acme Corp Reports Second Quarter 2026 Financial Results"
    _smoke_body_txt = ("Revenue grew 60% year-over-year to $24.3 million. "
                       "The company reported record quarterly revenue.")
    # headline=True, to match how _classify_article calls it. Earnings_Results is
    # headline-only (PrClassifier.BODY_EXCLUDED_CLASSES), so the body fallback can never
    # produce it.
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

    # body_prep lazily imports tools/keyword/keyword_finder.py (pandas + sklearn) on its
    # FIRST call. Warm it here so that import never lands on a live PR.
    _smoke_body = prep_body("Acme Corp today announced record quarterly revenue.",
                            "Acme Corp announces record revenue", PRCLASS_BODY_CAP_WORDS)
    if not _smoke_body:
        raise RuntimeError("body_prep.prep_body returned empty on a non-empty body "
                           "— refusing to start")
    logger.info("body_prep ready (PR classifier cap=%d words, smoke: %d words out).",
                PRCLASS_BODY_CAP_WORDS, len(_smoke_body.split()))

    # The XNYS session index behind TimeToOpen. Built once, spanning a year either
    # side so it survives a year boundary; rebuilt daily beside the universe reload.
    _refresh_sessions()

    # Echo the WHOLE gate: every rule, with the thresholds actually in force, rebuilt
    # from the compiled conditions. See _log_active_gate().
    _log_active_gate()

    # Load the universe (plain membership — no float filter) and its symbol index,
    # which backs every Float/MktCap/Exchange/close/ITI/tradeSize/model-3 lookup. The
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

    # ── does this universe file carry model-3's 12 market-state columns? ──────
    # Only daily_universe_pipeline-3 files do (147 cols, first written 2026-09-17).
    _xgb_universe_ok, _missing = _universe_supports_xgb(df)
    if _xgb_universe_ok:
        logger.info("[Universe] pipeline-3 schema confirmed — all %d XGBoost feature "
                    "columns present.", len(XGB_UNIVERSE_COLS))
    elif _gate_needs_xgb:
        # A rule reads the leg, so there is no safe way to continue: the gate cannot be
        # evaluated on features that do not exist, and failing closed here is the same
        # policy a malformed rules file gets. ⚠ 20:30-21:35 ET the nightly file is
        # mid-build — do not restart in that window.
        raise RuntimeError(
            f"the newest universe TSV is missing {len(_missing)} XGBoost feature "
            f"column(s) {_missing} — and the loaded gate READS the XGBoost leg, so "
            f"it cannot be evaluated. Run daily_universe_pipeline-3.py, or remove the "
            f"XGBoost field(s) from {os.path.basename(TRIGGER_RULES_FILE)}")
    else:
        # Trading is NOT stopped: the gate does not read the leg. Only the predictions
        # are suppressed, and Xgb_status says so on every row.
        logger.warning(
            "[Universe] this universe TSV predates daily_universe_pipeline-3 and is "
            "missing %d XGBoost feature column(s): %s. XGB PREDICTIONS ARE DISABLED "
            "(Xgb_status='disabled_universe' on every row). TRADING IS UNAFFECTED — the "
            "gate does not read the XGBoost leg. Run daily_universe_pipeline-3.py to "
            "restore it.", len(_missing), _missing)

    _nw_excluded_strings = _load_excluded_strings(NW4_EXCLUDED_STRINGS_FILE)
    _excluded_strings    = _load_excluded_strings(ORCH_EXCLUDED_STRINGS_FILE)

    # Daily universe reload thread (swaps _iti_df + _univ_index + _inscope_set; the
    # standalone connector reloads its own WS subscribe list at 03:50 on its side).
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
        # The engine drains everything already queued before it stops (its shutdown
        # sentinel sorts after every real priority), so collectors still waiting on a
        # future get their answer and write their row.
        if _engine is not None:
            _engine.shutdown(wait=True)
        _earn_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        logger.info("Orchestrator5.4 stopped.")


# --check-xgb latency budget for the LEG (embedding + the ten boosters, one PR, warm):
# model-time per PR. Guide §3.11: the clerk allows 2 s and bursts reach ~10+ PRs, so
# 200 ms/PR is the ceiling and 100 leaves room for gate-model contention. The FAIL line
# is 2x the budget — a STRUCTURAL regression (someone re-added a body model or a batched
# embed), not timing noise on a busy box, which only WARNs.
LEG_BUDGET_MS = 100.0

_LATENCY_FALLBACK_HEADLINES = (
    "Acme Corp announces FDA approval of its lead drug candidate",
    "XYZ Therapeutics Reports Second Quarter 2026 Financial Results",
    "ABC Inc. Receives Nasdaq Notice Regarding Minimum Bid Price Deficiency",
    "Company Announces Pricing of $5 Million Public Offering",
    "DEF Corp. Enters into Definitive Agreement to Be Acquired for $4.50 per Share",
    "GHI Ltd Announces 1-for-20 Reverse Stock Split",
    "JKL Biotech Announces Positive Topline Results from Phase 2 Trial",
    "MNO Holdings Secures $12 Million Contract with U.S. Department of Defense",
)


def _latency_headlines(n: int = 20) -> list:
    """Real headlines from the newest live TSVs, else a fixed list. ⚠ news_output_*.tsv
    is written by csv.DictWriter (QUOTE_MINIMAL): read it with the csv module's DEFAULT
    quoting, never QUOTE_NONE (that mangles quoted cells silently)."""
    heads = []
    try:
        csv.field_size_limit(sys.maxsize)
        for p in reversed(sorted(glob.glob(os.path.join(OUTPUT_DIR,
                                                        'news_output_????-??-??.tsv')))):
            with open(p, newline='', encoding='utf-8') as fh:
                for d in csv.DictReader(fh, delimiter='\t'):
                    h = (d.get('Headline') or '').strip()
                    if h:
                        heads.append(h)
            if len(set(heads)) >= n:
                break
    except Exception:                                         # noqa: BLE001
        heads = []
    heads = list(dict.fromkeys(heads))[:n]
    for h in _LATENCY_FALLBACK_HEADLINES:
        if len(heads) >= n:
            break
        if h not in heads:
            heads.append(h)
    return heads


def _check_xgb() -> int:
    """--check-xgb: prove the model-3 leg can run, then exit. No sockets, no feed, no
    gate headline models — a few seconds.

    Answers, before a live PR does:
      1. are the INT8 embedder and the PCA basis the bytes model-3 trained on (md5)?
      2. do all ten .ubj/_prep.json load, with model-3's provenance and round counts?
      3. does _XGB_FEATURE_SOURCES cover every feature? (a retrain that adds one fails
         HERE, naming it)
      4. does the newest universe TSV carry the 12 pipeline-3 columns?
      5. does a synthetic row score through the REAL raw-row builder and the REAL task
         code (the engine's ModelSet, with the configured ONNX thread count)?
    and prints the leg's per-stage latency on real headlines against LEG_BUDGET_MS."""
    global _xgb_meta, _xgb_universe_ok
    import numpy as np
    _load_trigger_rules(TRIGGER_RULES_FILE)
    o54.check_m3_artifacts()
    print("[Xgb] model-3 artifacts: md5 OK (INT8 embedder + PCA basis)")
    _xgb_meta = _load_xgb_meta()
    groups = {e['group'] for e in _xgb_meta}
    print(f"[Xgb] {len(_xgb_meta)} model(s) from {len(o54.M3_CONFIGS)} config(s) "
          f"({', '.join(d.name for d in o54.M3_CONFIGS.values())}), "
          f"{len(groups)} distinct preprocessing group(s):")
    for e in _xgb_meta:
        thr = e['prep'].get('threshold')
        print(f"  {e['col']:<10s} {e['cfg']:<5s} {e['kind']:<10s} "
              f"{len(e['prep']['features_used']):>2d} feat  group={e['group']}  "
              f"trees={e['prep']['n_rounds']}"
              f"{f'  threshold>={int(thr)}' if thr else ''}")

    feats = list(_xgb_meta[0]['prep']['features_used'])
    pca = [f for f in feats if f.startswith('hd_pca_')]
    structured = [f for f in feats if f not in pca]
    print(f"\n[Xgb] {len(feats)} feature(s): {len(structured)} structured + {len(pca)} "
          f"PCA ({', '.join(pca)})")
    by_src = {}
    for f in structured:
        by_src.setdefault(_XGB_FEATURE_SOURCES[f][0], []).append(f)
    for src in sorted(by_src):
        print(f"  {src:<8s} ({len(by_src[src]):>2d}) {', '.join(sorted(by_src[src]))}")

    _load_latest_iti_tsv()
    with _iti_df_lock:
        df = _iti_df
    _xgb_universe_ok, missing = _universe_supports_xgb(df)
    print(f"\n[Xgb] universe: "
          f"{'OK — pipeline-3 schema' if _xgb_universe_ok else 'MISSING ' + str(missing)}")
    print(f"[Xgb] gate-blocking: "
          + (f"YES — pre-decision booster(s): {', '.join(_gate_xgb_cols) or 'none (TimeToOpen only)'}"
             if _gate_needs_xgb else "no"))

    # The models, in THIS process, with the configured ONNX thread count. (The check
    # stays a few seconds whatever --engine says: the process engine runs this same
    # ModelSet code in each worker.)
    print(f"\n[Xgb] loading the INT8 embedder + boosters "
          f"(intra_op={ENGINE_INTRA_OP or 'ORT default'})...")
    ms = o54.ModelSet(intra_op=ENGINE_INTRA_OP or None, spinning=ENGINE_SPINNING,
                      kinds=('embed', 'xgb')).load()
    gold = ms.self_check(required=False)
    print(f"[Xgb] loaded in {ms.load_s}s; goldens (embed + xgb): {gold}")
    bad_trees = [c for c, nr, end, _g, _k in ms.describe_xgb() if end != nr]
    if bad_trees:
        print(f"\nFAILED: iteration range is not every tree for {bad_trees}")
        return 1

    # A synthetic row through the REAL raw-row builder. Mid-range values, not zeros, so
    # a winsor bound or categorical mapping that silently drops everything still
    # produces a distinguishable number.
    smoke_row = {'Symbol': '_SMOKE_', 'Float': 5.0, 'MktCap': 20.0,
                 'Exchange': 'NASDAQ', 'LastDailyClose': 2.0,
                 'itiBaseline': 10.0, 'tradeSizeBaseline': 500.0, 'TickerCount': 1}
    smoke_univ = {c: 1.0 for c in XGB_UNIVERSE_COLS}
    smoke_univ.update({'RTH_avgITI_sec': 10.0, 'ETH_avgITI_sec': 30.0,
                       'RTH_tradeSize': 500.0})
    raw = _xgb_raw_row({'ss': 0.5}, {'ind': 0.1, 'ss': 0.0}, {'ss': 0.5, 'neu': 0.3},
                       {'FineClass': 'MA', 'BroadClass': 'MA'},
                       smoke_row, smoke_univ, 1.5)
    emb = ms.run('embed', "Acme Corp announces FDA approval of its lead drug candidate")
    res = ms.run('xgb', raw, emb['hd_pca'], None)
    print(f"\n[Xgb] synthetic row → feat={res['_feat_ms']}ms "
          f"predict={res['_predict_ms']}ms")
    for col in XGB_COLS:
        print(f"  {col:<10s} {res.get(col)}")
    blank = [c for c in XGB_COLS
             if not isinstance(res.get(c), float) or not math.isfinite(res[c])]
    if blank:
        print(f"\nFAILED: {len(blank)} prediction(s) blank or non-finite: {blank}")
        return 1

    # The latency line: the REAL leg (embedding + the whole family, one PR at a time,
    # warm) on real headlines.
    heads = _latency_headlines(20)
    ms.run('embed', heads[0])                                   # warm, discarded
    emb_ms, feat_ms, pred_ms = [], [], []
    for h in heads:
        e = ms.run('embed', h)
        r = ms.run('xgb', raw, e['hd_pca'], None)
        emb_ms.append(e['_embed_ms'])
        feat_ms.append(r['_feat_ms'])
        pred_ms.append(r['_predict_ms'])
    leg = [a + b + c for a, b, c in zip(emb_ms, feat_ms, pred_ms)]
    med = lambda v: float(np.median(v))                         # noqa: E731
    p90 = lambda v: float(np.percentile(v, 90))                 # noqa: E731
    m = med(leg)
    verdict = ('OK' if m <= LEG_BUDGET_MS else
               'WARN (over budget — timing on a busy box is noisy)' if m <= 2 * LEG_BUDGET_MS
               else 'FAIL (over 2x budget — a structural regression)')
    print(f"\n[Xgb] latency n={len(heads)} warm, 1 thread, intra_op="
          f"{ENGINE_INTRA_OP or 'ORT default'}: embed_hd {med(emb_ms):.1f}/{p90(emb_ms):.1f}"
          f" | feat {med(feat_ms):.1f}/{p90(feat_ms):.1f}"
          f" | predict {med(pred_ms):.2f}/{p90(pred_ms):.2f}"
          f" | LEG {m:.1f}/{p90(leg):.1f} ms (median/p90; budget "
          f"{LEG_BUDGET_MS:.0f}) {verdict}")
    print("[Xgb] (all ten boosters here; live, only the gate's run before STEP-1b)")
    if m > 2 * LEG_BUDGET_MS:
        return 1
    print("\n[Xgb] OK")
    return 0


def _parse_cli(argv=None):
    ap = argparse.ArgumentParser(
        description="Orchestrator5.4 — news -> sentiment gate -> clerk, with the model-3 "
                    "XGBoost leg and a pinned scoring engine.")
    ap.add_argument('--check-rules', action='store_true',
                    help='parse + echo the rules file, then exit (~1 s, no models)')
    ap.add_argument('--check-xgb', action='store_true',
                    help='check the model-3 artifacts + print the leg latency, then exit')
    ap.add_argument('--rules', help='rules file (default: orchestrator_trigger_rules.txt)')
    ap.add_argument('--feed-host', help='connector feed host (default: config file)')
    ap.add_argument('--feed-port', type=int, help='connector feed port (default: config file)')
    ap.add_argument('--clerk-host', help='clerk host (default: config file)')
    ap.add_argument('--clerk-port', type=int, help='clerk port (default: config file)')
    ap.add_argument('--out-dir', help='TSV output dir (default: orchestrator5/tables)')
    ap.add_argument('--log-dir', help='log dir (default: orchestrator5/logs)')
    ap.add_argument('--universe-file', help='pin one universe TSV (default: the newest)')
    ap.add_argument('--engine', choices=('threads', 'procs'))
    ap.add_argument('--pools', choices=('shared', 'per-model'))
    ap.add_argument('--workers', type=int)
    ap.add_argument('--intra-op', type=int,
                    help='ORT intra_op_num_threads per session (0 = ORT default pool)')
    ap.add_argument('--spinning', action='store_true',
                    help='let ORT intra-op threads spin (only matters for --intra-op > 1)')
    ap.add_argument('--cpus', help="CPUs to pin to, e.g. '1-3,5-7' (default) or 'all'")
    return ap.parse_args(argv)


def _apply_cli(a) -> None:
    """Fold the CLI into the module globals every function reads at call time."""
    global TRIGGER_RULES_FILE, FEED_HOST, FEED_PORT, CLERK_HOST, CLERK_PORT
    global OUTPUT_DIR, LOG_DIR, UNIVERSE_FILE_OVERRIDE
    global ENGINE_KIND, ENGINE_POOLS, ENGINE_WORKERS, ENGINE_INTRA_OP, ENGINE_SPINNING
    global INFER_CPUS
    if a.rules:
        TRIGGER_RULES_FILE = str(Path(a.rules).resolve())
    if a.feed_host:
        FEED_HOST = a.feed_host
    if a.feed_port:
        FEED_PORT = a.feed_port
    if a.clerk_host:
        CLERK_HOST = a.clerk_host
    if a.clerk_port:
        CLERK_PORT = a.clerk_port
    if a.out_dir:
        OUTPUT_DIR = str(Path(a.out_dir).resolve())
    if a.log_dir:
        LOG_DIR = Path(a.log_dir).resolve()
    if a.universe_file:
        UNIVERSE_FILE_OVERRIDE = str(Path(a.universe_file).resolve())
    if a.engine:
        ENGINE_KIND = a.engine
    if a.pools:
        ENGINE_POOLS = a.pools
    if a.workers:
        ENGINE_WORKERS = a.workers
    if a.intra_op is not None:
        ENGINE_INTRA_OP = a.intra_op
    if a.spinning:
        ENGINE_SPINNING = True
    if a.cpus:
        INFER_CPUS = o54.parse_cpus(a.cpus)


if __name__ == '__main__':
    _args = _parse_cli()
    _apply_cli(_args)
    _install_file_logging(LOG_DIR)
    if _args.check_xgb:
        # The XGBoost-leg twin of --check-rules. Run it after any retrain, and after
        # any change to _XGB_FEATURE_SOURCES.
        sys.exit(_check_xgb())
    if _args.check_rules:
        # Parse + echo the rules file, then exit. No models, no sockets, no universe
        # read: ~1 s. The fast edit/verify loop for the rules file, and the mitigation
        # that keeps a typo from turning the refuse-to-start policy into a trading
        # outage. Exit 0 means the gate echoed here is exactly the gate 5.4 would run.
        _load_trigger_rules(TRIGGER_RULES_FILE)
        _log_active_gate()
        sys.exit(0)
    main()
