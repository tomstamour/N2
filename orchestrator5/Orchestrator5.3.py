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
import queue
import itertools
from concurrent.futures import Future
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
# ⚠ PrClassifier.BODY_LEAD_CHARS (1500) is deliberately NOT imported any more. Since
# the 200-word alignment the classifier's body fallback reads the SAME _prep_body_200
# string as the body encoders and the embedder, not its own slice of the raw cell —
# see _classify_and_score_earnings. The batch script reproduces this with --body-200w.

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

# ── 5.3: the XGBoost leg's three extra encoders ──────────────────────────────
# These feed the XGBoost models, NOT the rules gate. Two score the HEADLINE
# (DeBERTa_ss_hd.1, TradeHero_ss_hd.1) and two score the BODY (FinancialB_ss_bd.1,
# TradeHero_pos_bd.1) — TradeHero does both channels from ONE module. So three new
# files, four new scoring calls.
#
# ⚠ 2026-09-20 (model-2): RoBERTa_headliner was REMOVED from this block. model-1
# read RoBERTa_neg_hd.1; model-2's 25-feature allowlist does not, so the call was
# pure cost (~9 ms/PR) and the RoBERTa_*_hd TSV block went with it. The module is
# still on disk and still exercised by tools/Encoder_Models/verify_headliners.py —
# re-adding it is a three-line importlib block plus a _score_headline_extras call.
# Prosus on the BODY went the same way; Prosus on the HEADLINE stays, because it is
# both a model-2 feature and two gate conditions.
#
# ⚠ A DISTINCT module name per file is mandatory, not cosmetic: all three define
# `load_model`, and DeBERTa's scorer is `analyze_headline` — the same name already
# bound to ProsusAI FinBERT above. Each module carries its own _inferencer /
# _model_dir singleton, and a shared module name would make them collide.
#
# ⚠ Do NOT pass a `labels=` argument to score_deberta. It is a zero-shot NLI model
# whose return SHAPE depends on that argument: with the default labels it returns
# pos/neg/neu/ss, and with any custom list those vanish in favour of a nested
# `scores` dict. The frozen offline column DeBERTa_ss_hd.1 was produced with the
# default, so live == backtest requires the default.
_deberta_spec = _ilu.spec_from_file_location(
    "DeBERTa_headliner",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "DeBERTa_headliner.py"),
)
_deberta_mod = _ilu.module_from_spec(_deberta_spec)
_deberta_spec.loader.exec_module(_deberta_mod)
score_deberta      = _deberta_mod.analyze_headline     # ⚠ never pass labels=
load_deberta_model = _deberta_mod.load_model

_tradehero_spec = _ilu.spec_from_file_location(
    "TradeHero_headliner",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "Trading-Hero_headliner.py"),
)
_tradehero_mod = _ilu.module_from_spec(_tradehero_spec)
_tradehero_spec.loader.exec_module(_tradehero_mod)
score_tradehero      = _tradehero_mod.score_headline   # used on BOTH channels
load_tradehero_model = _tradehero_mod.load_model

_financialb_spec = _ilu.spec_from_file_location(
    "FinancialB_headliner",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "FinancialBERT_headliner.py"),
)
_financialb_mod = _ilu.module_from_spec(_financialb_spec)
_financialb_spec.loader.exec_module(_financialb_mod)
score_financialb      = _financialb_mod.score_headline
load_financialb_model = _financialb_mod.load_model
# ─────────────────────────────────────────────────────────────────────────────

# ── 5.3: body text prep (the encoders' and embedder's shared input) ──────────
# body_prep.prep_body = clean_body (CSV-escape artifact, leading verbatim headline
# repeat, wire dateline, tracker URLs, strip_wire_boilerplate) + cap_words(n).
# This is the EXACT function BodyScore11encoders.py used to build the offline
# *_bd columns, which is what makes live == backtest for them.
#
# ⚠ It lazily imports tools/keyword/keyword_finder.py (pandas + sklearn) on its
# FIRST call, so main() warms it once rather than paying that on a live PR.
# ⚠ It is loaded under its own module name here. finbert_embed_core loads its own
# private copy as "body_prep_for_embed"; we never trigger that path because we
# hand embed_texts() pre-prepped strings (see _embed_and_project).
_bodyprep_spec = _ilu.spec_from_file_location(
    "o53_body_prep",
    str(SCRIPTS_DIR / "tools" / "Encoder_Models" / "body_prep.py"),
)
_bodyprep_mod = _ilu.module_from_spec(_bodyprep_spec)
_bodyprep_spec.loader.exec_module(_bodyprep_mod)
prep_body = _bodyprep_mod.prep_body
# ─────────────────────────────────────────────────────────────────────────────

# ── 5.3: FinBERT embedding + frozen PCA projection (the 20 *_pca_* features) ──
# ProsusAI/finbert exported as an ONNX FP32 FEATURE-EXTRACTION model — a different
# artifact from the INT8 classification model the gate's Prosus leg uses. The
# embedding is the attention-masked mean of the last hidden layer (768 dims per
# stream); finbert_pca.pkl then projects it onto the basis fit on the TRAIN FOLD
# ONLY. Both are frozen: re-exporting or re-fitting either invalidates every
# hd_pca_*/bd_pca_* the models were trained on.
#
# ⚠ We bind embed_texts (which takes ALREADY-PREPPED strings), not embed_pr
# (which re-runs prep_body internally through its own module copy). 5.3 preps the
# body once per article and shares that string with the three body encoders.
_embcore_spec = _ilu.spec_from_file_location(
    "o53_finbert_embed_core",
    str(SCRIPTS_DIR / "training_data" / "finbert_embed_core.py"),
)
_embcore_mod = _ilu.module_from_spec(_embcore_spec)
_embcore_spec.loader.exec_module(_embcore_mod)
embed_texts      = _embcore_mod.embed_texts
load_embed_model = _embcore_mod.load_model
EMBED_DIM        = _embcore_mod.EMBED_DIM              # 768

_pca_spec = _ilu.spec_from_file_location(
    "o53_finbert_pca_transform",
    str(SCRIPTS_DIR / "training_data" / "finbert_pca_transform.py"),
)
_pca_mod = _ilu.module_from_spec(_pca_spec)
_pca_spec.loader.exec_module(_pca_mod)
transform_pr = _pca_mod.transform_pr
load_pca     = _pca_mod.load
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
    "o53_dayofweek_addon",
    str(SCRIPTS_DIR / "price_stats_loader" / "dayOfWeek_addOn.py"),
)
_dow_mod = _ilu.module_from_spec(_dow_spec)
_dow_spec.loader.exec_module(_dow_mod)
# Only these two are bound: time_to_open() already calls reference_session()
# internally, and _compute_time_to_open reaches parse_arrival_time through _dow_mod.
time_to_open = _dow_mod.time_to_open
Sessions     = _dow_mod.Sessions
# ─────────────────────────────────────────────────────────────────────────────

# ── 5.3: the XGBoost apply-time transform ────────────────────────────────────
# XGBoost_inference_script is the counterpart to train_xgb_fromPRhigh.build_xy:
# it replays, from each model's own _prep.json, the sentinel substitution,
# feature selection, type cast and winsorisation — IN THAT ORDER, which matters.
# We reuse its build_row_features verbatim rather than reimplementing it, so a
# retrain that changes the preprocessing cannot silently desync live from offline.
# Its CLI half (which reads a finished training TSV) is __main__-guarded and never
# runs here.
import xgboost as xgb  # noqa: E402

_xgbinf_spec = _ilu.spec_from_file_location(
    "o53_xgb_inference",
    str(SCRIPTS_DIR / "training_data" / "XGBoost_inference_script.py"),
)
_xgbinf_mod = _ilu.module_from_spec(_xgbinf_spec)
_xgbinf_spec.loader.exec_module(_xgbinf_mod)
# build_row_features and _invert_target are the two pieces worth reusing. Its
# load_prep/load_booster are NOT bound: they resolve through the script's own
# MODEL_MAP, which pins a different (smaller) artifact set than XGB_MODELS below.
xgb_build_row_features = _xgbinf_mod.build_row_features
xgb_invert_target      = _xgbinf_mod._invert_target
XGB_SENTINEL           = _xgbinf_mod.SENTINEL           # 44444.0
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
#   process holds. `Orchestrator5.3.py --check-rules` parses + echoes and exits.
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
# them. Run only ONE orchestrator instance — NEVER two of Orchestrator5.0/5.1/5.2/5.3
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

# ── 5.3: the XGBoost leg ──────────────────────────────────────────────────────
# MEASURED ON THIS BOX (8 threads, warm models, 24 REAL articles off the corpus,
# medians — NOT one synthetic string in a tight loop, which reads ~2x optimistic).
# The bracketed figure is what the same stage cost under model-1, before the
# 2026-09-20 encoder cut:
#   body prep (regex)                                              ~4 ms
#   2 headline encoders (DeBERTa + TradeHero)                    ~184 ms   p90 220
#                                                                          [was 192]
#   2 body encoders @200 words (FinancialB + TradeHero)          ~203 ms   p90 238
#                                                                          [was 285]
#   FinBERT embedding, headline + body, + PCA projection         ~412 ms   p90 509
#                                              LEG TOTAL        ~801 ms   p90 988
#                                                                          [was 917]
#   feature assembly + 2 DMatrix builds                            ~8 ms
#   10 boosters, predict                                           ~3 ms
#
# So the cut bought ~116 ms of the ~176 ms the two dropped calls cost in isolation,
# and the EMBEDDING still dominates — it is 51% of the leg and nothing in the
# model-2 move touched it. Halving the leg again means attacking the body forward
# pass, not the encoders.
#
# ⚠ These move with CPU frequency and background load (observed 788..1251 ms for
# identical work). Treat them as an order of magnitude; for what actually happened on
# a given day, read Xgb_total_ms out of the TSV.
#
# ⚠ MORE THREADS DO NOT MAKE THIS FASTER. A single ONNX inference already saturates
# all 8 cores through ORT's intra-op pool, so extra threads only oversubscribe.
# Interleaved A/B over the three leg stages, 24 runs each (2026-09-18): 926 ms run
# sequentially in one worker vs 997 ms across three concurrent pools — sequential wins
# by 1.08x. An earlier synthetic benchmark put the gap at 1.2-1.3x (655 ms vs
# 780-875 ms across pools of 4/8/11); on real articles the honest figure is the
# smaller one, but the SIGN is consistent and that is what the design rests on.
# (OMP_NUM_THREADS has no effect — ORT uses its own pool, not OpenMP. Capping a
# session's intra_op_num_threads DOES work and is bit-identical — max diff 0.000e+00
# across 1/2/3/4 threads — but it only shifts CPU between models; total work is fixed.)
#
# So the leg is ONE task on ONE worker running its three stages back to back. The pool
# still earns its place: it lets the leg overlap the clerk-arm round-trip and the
# gate's own scorers, which are on other threads. Nothing inside it competes with
# itself, and a burst queues in one place instead of thrashing every core.
XGBLEG_WORKERS = 1

# Ceiling so a hung model can never stall the TSV write. Generous versus the ~801 ms
# median because a burst legitimately queues behind the single worker: at ~0.8 s
# each, 30 s is ~37 PRs deep (measured on a 6-PR simultaneous burst: the last row
# landed at 4.9 s, i.e. the queue drains at about the single-PR rate). ⚠ A burst deeper than that starts timing the leg out — those
# rows are still written, with the gate's own columns intact and
# Xgb_status='error', but their XGBoost cells are lost. The worst burst observed on
# this feed is ~15 PRs in 1 s (2026-07-02), which fits.
XGBLEG_WAIT_SEC = 30

# ⚠ OPERATOR DECISION 2026-09-18: 200 words for EVERY body the encoders and the
# embedder see, chosen for latency.
#
# ✅ SINCE 2026-09-20 (model-2) THIS IS FAITHFUL WITH NO EXCEPTIONS. Every body
# feature the live models read — FinancialB_ss_bd.1, TradeHero_pos_bd.1 and every
# bd_pca_* — was trained on exactly this text: BodyScore11encoders.py ran at
# --cap-words 200 and finbert_embed_core pins BODY_CAP_WORDS = 200. Verified to
# reproduce the stored corpus values (30/30 rows exact, maxdiff 1e-6 on the PCA
# columns) from this same prep.
#
# HISTORY, so nobody re-derives it: under model-1 this cap was a KNOWN DIVERGENCE
# for Prosus_neg_bd / Prosus_ss_bd. Those two came from FinBERT_TSV_scorer.py,
# which has no body_prep import at all — it feeds the raw Body cell and lets the
# tokenizer truncate at 512 — so the raw body reproduced the stored value 30/30
# and the 200-word prep 0/30. It was accepted knowingly, for 122 ms. model-2
# closed it from both ends at once: the corpus rescored those two columns at 200
# words, AND the 25-feature allowlist dropped them, so the live path no longer
# scores Prosus on the body at all. Nothing here needs "fixing back".
#
# ⚠ NOT applied to the ge15 keyword scorer, which keeps its own KW15_BODY_MAX_WORDS
# = 400 and its own _kw15_prep cleaner: kw15 is a tfidf predict_proba costing a few
# ms (so capping saves nothing) AND it is a LIVE GATE INPUT feeding three of the
# five rules, so changing its text window would change which PRs trade.
XGB_BODY_CAP_WORDS = 200

# The model family — 2026-09-20: model-1 (7 boosters, 49 features) was REPLACED by
# model-2 and model-2FT (5 each, 25 features). model-2 retrained on corrected data
# (Prosus body rescored at 200 words, FineClass/BroadClass recategorised) and
# SHAP-pruned 49 -> 25; it measures indistinguishable from model-1 on half the
# inputs. model-2FT is the same 25 features and the same hyperparameters refit on
# 100% of the rows with NO held-out fold — so treat its numbers with less
# confidence than model-2's, and its cons_q80 most of all (4000 rounds, no
# early-stop safety net). Both generations are scored so the tape can tell them
# apart later.
#
# All ten share ONE 25-feature list in ONE order (verified), so the only thing that
# splits them into two DataFrame builds is the winsor bounds, which differ between
# the dirs — _load_xgb_models discovers that by signature rather than assuming it.
#
#   NOT RUNNABLE LIVE, deliberately omitted:
#     model-1/*                       superseded; would drag back the RoBERTa
#                                     headline and Prosus body calls it alone reads.
#     xgb_fromPRhigh_reg / _q80       need all 283 training columns.
XGB_DIR = SCRIPTS_DIR / "training_data"
XGB_MODELS = (
    # (TSV column, model dir,  .ubj stem,                  kind)
    ('se_m2',      'model-2',   'xgb_cons_se',             'regressor'),
    ('q80_m2',     'model-2',   'xgb_cons_q80',            'regressor'),
    ('ge20_m2',    'model-2',   'xgb_fromPRhigh_clf_ge20', 'classifier'),
    ('ge35_m2',    'model-2',   'xgb_fromPRhigh_clf_ge35', 'classifier'),
    ('ge50_m2',    'model-2',   'xgb_fromPRhigh_clf_ge50', 'classifier'),
    ('se_m2FT',    'model-2FT', 'xgb_cons_se',             'regressor'),
    ('q80_m2FT',   'model-2FT', 'xgb_cons_q80',            'regressor'),
    ('ge20_m2FT',  'model-2FT', 'xgb_fromPRhigh_clf_ge20', 'classifier'),
    ('ge35_m2FT',  'model-2FT', 'xgb_fromPRhigh_clf_ge35', 'classifier'),
    ('ge50_m2FT',  'model-2FT', 'xgb_fromPRhigh_clf_ge50', 'classifier'),
)

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
logger = logging.getLogger('Orchestrator5.3')


class _OrchDailyFileHandler(c3.DailyFixedTZFileHandler):
    """Daily Orchestrator5.3_YYYY_MM_DD.log in ./logs (fixed UTC-4 date key) —
    only the filename differs from the connector's handler. Attached to the ROOT
    logger so every orchestrator record lands in the file; connector3 records do
    NOT propagate (its logger is queue-fed with propagate=False) and are already
    persisted in RTPR_connector3/logs."""

    def path_for_date(self, date_key: str) -> Path:
        return self.log_dir / f"Orchestrator5.3_{date_key}.log"


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

# ── 5.3 singletons ────────────────────────────────────────────────────────────
# Loaded once in main() (or --check-xgb) and then READ-ONLY, so the pools touch
# them without a lock — the same contract as _earn_params / the kw pipelines.
# _xgb_models: tuple of (tsv_col, booster, prep, kind, iteration_range).
_xgb_models: tuple = ()
# Distinct feature orders across the family (the 49-set and the 18-set), so the
# DataFrame/DMatrix is built once per ORDER rather than once per model.
_xgb_feature_orders: tuple = ()
# True once the loaded universe TSV is confirmed to carry XGB_UNIVERSE_COLS.
# When False the encoders still run and their columns are still written; only the
# Xgb_* predictions are suppressed, with Xgb_status = 'disabled_universe'.
_xgb_universe_ok: bool = False
# XNYS session index for TimeToOpen; rebuilt daily beside the universe reload.
_sessions = None
_sessions_lock = threading.Lock()
# Set by _load_trigger_rules: True when a LOADED rule names an xgb_* field, which
# is what makes the gate WAIT for the XGBoost leg instead of sending STEP-1b on
# the five original scores. Ships False — see _collect_and_log.
_gate_needs_xgb: bool = False

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

# ── 5.3: the XGBoost leg's pool — see the XGBLEG_WORKERS note above ──────────
# ONE worker running the three stages sequentially. It does not parallelise CPU
# (measured: concurrent pools are ~2x SLOWER here); it exists so the leg starts at
# reception and overlaps the clerk-arm round-trip and the gate's own scorers, and so
# a burst queues in one place instead of thrashing every core.
# Priorities for _PriorityExecutor.submit(). LOWER IS SERVED FIRST.
_PRIO_TRADEABLE = 0    # the gate could still fire on this PR — it is on the critical path
_PRIO_CORPUS    = 1    # a REQUIRE precondition already rules it out; TSV columns only
_PRIO_SHUTDOWN  = 9    # drains everything queued before it


class _PriorityExecutor:
    """One worker thread serving a PRIORITY queue instead of a FIFO one.

    A drop-in for ThreadPoolExecutor(max_workers=1) except that submit() takes a
    leading integer priority, lower served first, ties broken by submission order.

    ⚠ WHY THIS EXISTS, AND WHY IT IS NOT MORE WORKERS. The leg is ~0.63 s of
    CPU-saturating ONNX work, so a burst serialises and the wait grows ~0.63 s per
    queue position — measured live on 2026-09-21, a 9-PR burst at 08:30 put the last
    row at 7542 ms. MORE WORKERS DO NOT FIX THAT: re-measured the same day on this
    4-physical-core box, N workers x intra_op=8/N over 8 bodies gives 1x8 1770 ms,
    2x4 1611 ms (1.10x), 4x2 1712 ms, 8x1 1663 ms, and cross-PR BATCHING is worse
    still at 0.75x. There is no parallelism to win here; the only lever is WHICH PR
    the one worker serves next.

    Six of the nine PRs in that 08:30 burst were ClassWhitelist:BLOCK and could never
    trade, yet every one of them sat ahead of a PR that could. This queue fixes that
    ordering and nothing else: every PR is still scored, and every TSV column is
    still written. Blocked PRs simply stop delaying tradeable ones.
    """

    def __init__(self, name: str = 'xgbleg'):
        self._q = queue.PriorityQueue()
        self._seq = itertools.count()          # ties -> FIFO, and keeps the tuple
        self._thread = threading.Thread(       # orderable without comparing Futures
            target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, priority: int, fn, *args, **kwargs) -> Future:
        f = Future()
        self._q.put((int(priority), next(self._seq), f, fn, args, kwargs))
        return f

    def _run(self):
        while True:
            _prio, _seq, f, fn, args, kwargs = self._q.get()
            if f is None:                      # shutdown sentinel
                return
            if not f.set_running_or_notify_cancel():
                continue
            try:
                f.set_result(fn(*args, **kwargs))
            except BaseException as exc:       # noqa: BLE001 — mirrors Executor
                f.set_exception(exc)

    def shutdown(self, wait: bool = True):
        # _PRIO_SHUTDOWN is the highest number, so everything already queued is
        # served before the sentinel — same contract as Executor.shutdown(wait=True).
        self._q.put((_PRIO_SHUTDOWN, next(self._seq), None, None, (), {}))
        if wait:
            self._thread.join(timeout=XGBLEG_WAIT_SEC + 5)

    def qsize(self) -> int:
        return self._q.qsize()


_xgbleg_executor = _PriorityExecutor(name='xgbleg')

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
        # is now on the same _prep_body_200 string as the three body encoders and the
        # embedder (clean_body + first XGB_BODY_CAP_WORDS words). It replaced a
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


# ─── 5.3: the XGBoost leg's text scorers ──────────────────────────────────────
#
# Three coarse tasks, one per pool, all submitted at reception. Each runs its
# models SEQUENTIALLY inside one worker — measured 2026-09-18, splitting them
# across more threads makes the wall clock WORSE on this box (one ONNX inference
# already saturates all 8 cores). See the XGB_* worker note at the top.
#
# None of these feed the rules gate. They exist to build the 25-feature vector the
# XGBoost family consumes, and their full distributions land in the TSV so a tape
# can be re-scored offline without re-running any model.


def _prep_body_200(news_dict: dict) -> str:
    """The ONE body string every 5.3 body consumer sees, built once per article.

    body_prep.prep_body = clean_body (CSV-escape artifact, leading verbatim headline
    repeat, wire dateline, tracker URLs, strip_wire_boilerplate) + first
    XGB_BODY_CAP_WORDS words. It is fed clean_inline_text(body_raw) — the exact
    string that lands in the TSV Body cell, the same one _score_keywords and
    _classify_and_score_earnings score — so an offline rescore of the daily TSV
    reproduces these values.

    ⚠ 200 words is an operator decision (2026-09-18). Since the model-2 cut it is
    faithful to the training corpus for every body feature the models read, with no
    exceptions. The full reasoning is on XGB_BODY_CAP_WORDS; read it first."""
    body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
    return prep_body(body_cell, news_dict['Headline'], XGB_BODY_CAP_WORDS)


def _enc_block(prefix: str, channel: str, result: dict, completed_at: str) -> dict:
    """Flatten one headliner's result dict into TSV columns.

    The naming convention is HeadScore11encoders'/BodyScore11encoders' exactly:
    f"{Model}_{key}_{hd|bd}". The suffix is applied HERE, not in the headliner —
    a headliner handed a body string cannot know which channel it got, which is
    precisely how one module serves both channels for TradeHero and Prosus.

    `_at_*` is a COMPLETION TIMESTAMP (the FinMarBa_at_hd convention); `_ms_*` is
    the model's own reported inference duration. They are different quantities."""
    out = {f'{prefix}_at_{channel}': completed_at}
    for key in ('pos', 'neu', 'neg', 'ss', 'lab', 'ms'):
        if key in result:
            out[f'{prefix}_{key}_{channel}'] = result[key]
    return out


def _score_headline_extras(news_dict: dict) -> dict:
    """DeBERTa + TradeHero on the HEADLINE (~184 ms median on real articles).
    DeBERTa is nearly all of it: FP32 zero-shot, 3 NLI pairs per call.

    Scores EXACTLY news_dict['Headline'] — the same raw string the three gate
    models get, and the same one HeadScore11encoders scored offline. The models
    read DeBERTa_ss_hd.1 and TradeHero_ss_hd.1 out of this; every other channel is
    stored for offline re-analysis. (RoBERTa ran here until 2026-09-20; model-2
    does not read it — see the encoder block at the top of the file.)

    ⚠ score_deberta is called with the DEFAULT labels. Passing a custom list
    changes the return SHAPE (pos/neg/neu/ss disappear) and would silently break
    both this dict and the live == backtest invariant."""
    headline = news_dict['Headline']
    t0 = time.monotonic()
    out = {}

    r_deb = score_deberta(headline)
    out.update(_enc_block('DeBERTa', 'hd', r_deb,
                          c3.format_millis_time(c3.now_fixed())))
    r_th = score_tradehero(headline)
    out.update(_enc_block('TradeHero', 'hd', r_th,
                          c3.format_millis_time(c3.now_fixed())))

    out['_hdx_ms'] = round((time.monotonic() - t0) * 1000.0, 1)
    logger.info(f"[HdExtras] {news_dict.get('Symbol')} → "
                f"DeBERTa_ss={r_deb['ss']:.4f} "
                f"TradeHero_ss={r_th['ss']:.4f} in {out['_hdx_ms']:.1f}ms")
    return out


def _score_body_encoders(news_dict: dict, body_200: str) -> dict:
    """FinancialB + TradeHero on the 200-word BODY (~203 ms median).

    The models read FinancialB_ss_bd.1 and TradeHero_pos_bd.1 out of this; the rest
    of each block is stored for offline re-analysis. Prosus ran here too until
    2026-09-20 (the same module the gate runs on the headline, one model and two
    channels) — model-2 reads no Prosus body feature, so the call and its seven
    columns went. That is also what retired the 200-word divergence; see
    XGB_BODY_CAP_WORDS.

    A blank body yields an all-None block rather than a model call: the encoders
    would score the empty string and return a meaningless distribution, and None
    is what the feature builder turns into the NaN XGBoost expects."""
    t0 = time.monotonic()
    out = {}

    if not body_200.strip():
        logger.warning(f"[BodyEnc] {news_dict.get('Symbol')}: blank body — "
                       f"body encoder columns left empty")
        for prefix in ('FinancialB', 'TradeHero'):
            out[f'{prefix}_at_bd'] = None
            for key in ('pos', 'neu', 'neg', 'ss', 'lab', 'ms'):
                out[f'{prefix}_{key}_bd'] = None
        out['_body_ms'] = round((time.monotonic() - t0) * 1000.0, 1)
        return out

    r_fin = score_financialb(body_200)
    out.update(_enc_block('FinancialB', 'bd', r_fin,
                          c3.format_millis_time(c3.now_fixed())))
    r_th = score_tradehero(body_200)
    out.update(_enc_block('TradeHero', 'bd', r_th,
                          c3.format_millis_time(c3.now_fixed())))

    out['_body_ms'] = round((time.monotonic() - t0) * 1000.0, 1)
    logger.info(f"[BodyEnc] {news_dict.get('Symbol')} → "
                f"FinancialB_ss={r_fin['ss']:.4f} "
                f"TradeHero_ss={r_th['ss']:.4f} in {out['_body_ms']:.1f}ms")
    return out


_EMBED_NONE_RESULT = {'hd_pca': None, 'bd_pca': None, '_embed_ms': None}


def _score_xgb_leg(news_dict: dict, body_200: str) -> dict:
    """The whole text half of the XGBoost leg, as ONE task on ONE worker.

    Runs the three stages BACK TO BACK rather than racing them: on this 8-thread box
    a single ONNX inference already saturates every core, so concurrency between them
    buys nothing and costs contention (interleaved A/B, 24 runs each, 2026-09-18:
    926 ms sequential vs 997 ms across three pools, for identical work — measured
    with model-1's six encoder calls, but the finding is about core saturation, not
    about which models run, so it survives the model-2 cut).

    Order is deliberate — cheapest stage first, so that if a later one hangs and hits
    XGBLEG_WAIT_SEC the row still carries the stages that did finish."""
    t0 = time.monotonic()
    hdx = _score_headline_extras(news_dict)
    body = _score_body_encoders(news_dict, body_200)
    embed = _embed_and_project(news_dict, body_200)
    total = (time.monotonic() - t0) * 1000.0
    logger.info(f"[XgbLeg] {news_dict.get('Symbol')} → hdx {hdx.get('_hdx_ms')}ms + "
                f"body {body.get('_body_ms')}ms + embed {embed.get('_embed_ms')}ms "
                f"= {total:.1f}ms")
    return {'hdx': hdx, 'body': body, 'embed': embed}


def _embed_and_project(news_dict: dict, body_200: str) -> dict:
    """FinBERT embedding of headline + body, projected onto the frozen PCA basis.
    ~205 ms median since 2026-09-21; the body forward dominates (FP32, ~270 tokens)
    and the PCA projection itself is ~0 ms.

    ⚠⚠ TWO SEPARATE embed_texts CALLS, DELIBERATELY — one per stream. Batching
    [headline, body] into ONE forward is bit-identical (re-verified 2026-09-21 on 12
    real PRs, max diff 0.000e+00) but almost exactly HALF THE SPEED, because
    _embed_batch pads to the longest item in the batch: a ~12-token headline is
    padded out to the body's ~270 tokens and then gets the body's full forward pass
    spent on it, so the batch of two costs two body-sized passes instead of one body
    plus one headline.

    ⚠ FIXED 2026-09-21. From 2026-09-18 to 2026-09-21 this function called
    embed_texts([headline, body_200]) — the single combined call this docstring has
    always warned against. Measured on 12 real PRs the day it was found:
    596 ms combined vs 299 ms split, 1.99x, embeddings identical to the last bit.
    That was ~296 ms on EVERY PR, and with the leg gate-blocking since 2026-09-20 it
    was ~296 ms on every trade decision. If you ever see the two calls merged back
    into one "for efficiency", this is the measurement that says not to.

    embed_texts takes ALREADY-PREPPED strings, which is why this is handed
    body_200 rather than the raw body: embed_pr would re-run prep_body internally
    through finbert_embed_core's own private copy of the module, doing the regex
    work twice. Blank text -> an all-zero vector, matching the batch builder.

    The PCA basis was fit on the TRAIN FOLD ONLY and is frozen; transform_pr is a
    mean-subtract and one matmul, ~0 ms.

    ⚠ There is ONE PCA basis for every generation: finbert_pca_transform pins
    model-1/finbert_pca.pkl and model-2 never refit it, deliberately — the
    embeddings did not change between generations, only two structured columns and
    the class labels did, so a refit would have been bit-identical. There is no
    model-2/finbert_pca.pkl; do not go looking for one.

    Both streams are still needed: model-2 reads hd_pca_004 and seven bd_pca_*."""
    headline = news_dict['Headline'].strip()
    t0 = time.monotonic()
    # ⚠ TWO calls. NEVER embed_texts([headline, body_200]) — that is one padded
    #   forward and costs 2x for a bit-identical result. See the docstring.
    hd_vec = embed_texts([headline])[0]            # (768,) float32
    bd_vec = embed_texts([body_200])[0]            # (768,) float32
    pcs = transform_pr({'hd': hd_vec, 'bd': bd_vec})
    out = {
        'hd_pca': pcs['hd_pca'],
        'bd_pca': pcs['bd_pca'],
        '_embed_ms': round((time.monotonic() - t0) * 1000.0, 1),
    }
    logger.info(f"[Embed] {news_dict.get('Symbol')} → "
                f"hd_pca[0]={out['hd_pca'][0]:.4f} bd_pca[0]={out['bd_pca'][0]:.4f} "
                f"in {out['_embed_ms']:.1f}ms")
    return out


# ─── 5.3: the XGBoost feature vector ──────────────────────────────────────────
#
# ONE table mapping every model feature name to where its value comes from — the
# same shape as _TRIGGER_FIELDS, and for the same reason: a single place to read
# when asking "where does this number come from?", and a single place to edit when
# a retrain changes the allowlist. --check-xgb asserts it covers every feature of
# every loaded model, so a retrain that adds a feature fails at startup with the
# name of the missing one rather than scoring a silent NaN forever.
#
# ⚠ THE '.1' SUFFIXES ARE REAL AND LOAD-BEARING. They are not typos and must not be
# tidied away. When the training corpus was built, the 11-encoder blocks were
# appended to a master that already carried them, so pandas disambiguated the
# SECOND (fresh) copy as '<name>.1'; step 9 then kept the '.1' copies and dropped
# the bare ones. The models were fit on those names, XGBoost matches features BY
# NAME, and a DMatrix built with the bare spellings is rejected. This table is
# exactly where the live bare name is translated to the trained name.
#
# source -> which dict the value is read out of at score time:
#   finbert/fmb/flang  the three gate headline models, REUSED — scored once per
#                      article for the gate, read again here, never scored twice
#   hdx/body           _score_headline_extras / _score_body_encoders
#   earn               _classify_and_score_earnings (the two categoricals)
#   row                row_vals, already resolved for the gate
#   univ               the raw universe row (pipeline-3 market-state block)
#   calc               computed per row (TimeToOpen)
_XGB_FEATURE_SOURCES = {
    # ── sentiment: two of the three models the gate already runs ──────────────
    # ⚠ FinMarBa is NOT here. model-1 read FinMarBa_ind_hd.1; model-2 does not.
    # FinMarBa still runs on every PR and is still scored FIRST — it feeds CORE,
    # KwBodySolo and FLANGSolo. "Only what the models need" is scoped to this
    # table; it says nothing about what the gate needs.
    'Prosus_ss_hd.1':          ('finbert', 'ss'),
    'FLANG_ss_hd.1':           ('flang',   'ss'),
    # ── sentiment: the two headline encoders the leg adds ─────────────────────
    'DeBERTa_ss_hd.1':         ('hdx',     'DeBERTa_ss_hd'),
    'TradeHero_ss_hd.1':       ('hdx',     'TradeHero_ss_hd'),
    # ── sentiment: the body channel ───────────────────────────────────────────
    # Both are scored on the 200-word prepped body, which is exactly what
    # BodyScore11encoders --cap-words 200 produced for the training corpus. No
    # exceptions any more — see XGB_BODY_CAP_WORDS.
    'FinancialB_ss_bd.1':      ('body',    'FinancialB_ss_bd'),
    'TradeHero_pos_bd.1':      ('body',    'TradeHero_pos_bd'),
    # ── catalyst class. BroadClass was a model-1 feature and is not a model-2
    #    one; it is still WRITTEN to the TSV because it falls out of the same
    #    classify_pr call, so keeping it costs nothing. ───────────────────────
    'FineClass':               ('earn',    'FineClass'),
    # ── size: already resolved for the gate. Float_M / MarketCap_M are ALREADY
    #    IN MILLIONS and the training columns are the same scale from the same
    #    source (NASDAQ_symbols_data) — do NOT multiply by 1e6 here. (The x1e6 in
    #    _classify_and_score_earnings is a different quantity: float-cap in
    #    dollars, which earning_scorer wants in units.) ─────────────────────────
    'Float':                   ('row',     'Float'),
    'MktCap':                  ('row',     'MktCap'),
    # ── market state: the pipeline-3 universe block. model-1 read 13 of these;
    #    model-2 reads these 7. XGB_UNIVERSE_COLS is derived from exactly this
    #    subset, so the startup check tracks the models automatically. ─────────
    'float_turnover_max_20d':  ('univ',    'float_turnover_max_20d'),
    'atr_pct_14':              ('univ',    'atr_pct_14'),
    'range_pct_1d':            ('univ',    'range_pct_1d'),
    'dist_swing_high_20d_pct': ('univ',    'dist_swing_high_20d_pct'),
    'amihud_63d':              ('univ',    'amihud_63d'),
    # ⚠ msc_basis_ratio sits at ~1.0 on every live row (832/833 on 2026-09-17)
    # because pipeline-3 captures the bars and the reference close on the same
    # day, where the training column has real tails. Known train/serve skew,
    # inherent to point-in-time inference; documented, not corrected here.
    'msc_basis_ratio':         ('univ',    'msc_basis_ratio'),
    'SI_ChangePct':            ('univ',    'SI_ChangePct'),
    # ── computed at reception ─────────────────────────────────────────────────
    'TimeToOpen':              ('calc',    'TimeToOpen'),
}

# The universe columns the loaded models actually read, DERIVED so it can never
# drift from the table above — a retrain that adds or drops a market-state feature
# moves this with it, and --check-xgb reports the new set.
#
# All of them exist ONLY in a daily_universe_pipeline-3 file (147 cols, first
# written 2026-09-17); a pre-pipeline-3 file has 38 columns and none of them, which
# is what _universe_supports_xgb() checks.
# ⚠ These use '' (empty) for "uncomputable", NOT the 44444 sentinel the ITI and
# trade-size columns in the SAME file use — pipeline-3 does that on purpose,
# because XGBoost would happily split on 44444. _coerce_float maps '' -> None and
# the feature builder then maps None -> NaN, which is what XGBoost wants.
XGB_UNIVERSE_COLS = tuple(
    key for _feat, (src, key) in _XGB_FEATURE_SOURCES.items() if src == 'univ')

# Read unconditionally by build_row_features BEFORE the feature slice is taken
# (it replays _fit_sentinels, which ran on the full X at fit time), so they must be
# present and float-parseable even though none currently survives into
# features_used. float(raw_row[col]) there has no try/except — a missing key is a
# KeyError on the trade path, which is why these are filled defensively.
_XGB_SENTINEL_COLS = {
    'RTH_avgITI_sec': ('univ', 'RTH_avgITI_sec'),
    'ETH_avgITI_sec': ('univ', 'ETH_avgITI_sec'),
    'RTH_tradeSize':  ('univ', 'RTH_tradeSize'),
}

# Sources whose absence from the gate is normal rather than wasteful: `row` is
# universe data that is read anyway, and the four 5.3 sources are scored to feed the
# XGBoost models and the TSV corpus, which is a job even when no rule reads them.
_GATE_NO_WARN_SOURCES = frozenset({'row', 'hdx', 'body', 'xgb', 'calc'})

# The _FieldSpec sources that only exist once the XGBoost leg has resolved. A rule
# naming ANY field from one of these makes the gate wait for the leg — see
# _load_trigger_rules / _gate_needs_xgb.
_XGB_GATE_SOURCES = frozenset({'hdx', 'body', 'xgb', 'calc'})

_XGB_NONE_RESULT = {col: None for col, _dir, _stem, _kind in XGB_MODELS}
_XGB_NONE_RESULT.update({'Xgb_status': None, 'Xgb_feat_ms': None,
                         'Xgb_predict_ms': None, 'Xgb_at': None})


def _load_xgb_models() -> tuple:
    """Load every booster + its _prep.json, group them by preprocessing signature,
    and verify _XGB_FEATURE_SOURCES covers every feature. Called once from main()
    and from --check-xgb. Raises on anything wrong — a trading process must not run
    with a model that would silently score NaN.

    Returns (models, feature_orders):
      models         tuple of (tsv_col, booster, prep, kind, iteration_range, group)
      feature_orders tuple of prep dicts — one per DISTINCT preprocessing, so the
                     DataFrame/DMatrix is built once per group rather than once per
                     model (10 -> 2 builds). `group` indexes into it, resolved HERE
                     at load time so the trade path never hashes a prep dict.
                     model-2 and model-2FT share a feature list but not their winsor
                     bounds (fitted on the train fold vs on everything), which is
                     what makes it 2 groups and not 1 — discovered by signature, so a
                     future generation that DID share bounds would collapse to 1.

    ⚠ iteration_range pins the tree count to what the model was validated at.
    Booster.predict() defaults to ALL rounds, which for model-2's cons_se means 286
    trees where early stopping chose 185 — a different number, and not the one its
    _report.md quotes. See the comment on `best` below for why model-2 and model-2FT
    need OPPOSITE handling."""
    models = []
    groups = {}
    order = []
    for tsv_col, model_dir, stem, kind in XGB_MODELS:
        ubj = XGB_DIR / model_dir / f"{stem}.ubj"
        prep_path = XGB_DIR / model_dir / f"{stem}_prep.json"
        if not ubj.is_file():
            raise RuntimeError(f"XGBoost model missing: {ubj}")
        if not prep_path.is_file():
            raise RuntimeError(f"XGBoost prep missing: {prep_path}")

        booster = xgb.Booster()
        booster.load_model(str(ubj))
        prep = json.loads(prep_path.read_text(encoding='utf-8'))
        if 'features_used' not in prep:
            raise RuntimeError(
                f"{prep_path.name} has no 'features_used' — that is a FULL-feature "
                f"model (all 283 training columns) and cannot be scored live")

        # ⚠ model-2 early-stopped, so it KEPT the overfit tail past best_iteration
        # and must be told to stop there. model-2FT has NO best_iteration attribute
        # at all — its round count was FIXED from model-2's early stop, so every
        # round it built is wanted. The `else` branch is not a fallback: passing
        # iteration_range=None raises TypeError inside Booster.predict (xgboost
        # 3.4.1), which would have turned every model-2FT row into Xgb_status=error.
        best = getattr(booster, 'best_iteration', None)
        it_range = ((0, int(best) + 1) if best is not None
                    else (0, booster.num_boosted_rounds()))

        # Signature over everything build_row_features reads, so two models share a
        # DataFrame only when their preprocessing is genuinely identical.
        sig = json.dumps([prep['features_used'], prep['cat_cols'], prep['categories'],
                          prep['sentinel_high'], prep['sentinel_zero'], prep['winsor']],
                         sort_keys=True)
        if sig not in groups:
            groups[sig] = len(groups)
            order.append(prep)
        models.append((tsv_col, booster, prep, kind, it_range, groups[sig]))

        missing = [f for f in prep['features_used']
                   if not (f.startswith('hd_pca_') or f.startswith('bd_pca_'))
                   and f not in _XGB_FEATURE_SOURCES]
        if missing:
            raise RuntimeError(
                f"{model_dir}/{stem}: no _XGB_FEATURE_SOURCES entry for {missing} — "
                f"a retrain changed the feature allowlist; add the source(s) before "
                f"running live")

    return tuple(models), tuple(order)


def _score_xgb(news_dict: dict, symbol: str, finbert_val: dict, finmarba_val: dict,
               flang_val: dict, earn_val: dict, hdx_val: dict, body_val: dict,
               embed_val: dict, row_vals: dict, univ_row: dict,
               time_to_open_h) -> dict:
    """Score one TICKER with the whole XGBoost family. Never raises.

    Per-ticker, not per-article: the text half of the vector is shared across a
    2-ticker PR but the structured half (Float, atr_pct_14, ...) is per-symbol, so
    the two tickers of one PR correctly get two different predictions. Cheap enough
    to sit on the collect worker — ~8 ms of DataFrame/DMatrix building (2 builds,
    one per winsor set) and ~3 ms of actual prediction across all ten boosters.

    Preprocessing is XGBoost_inference_script.build_row_features, imported rather
    than reimplemented, so a retrain that changes the sentinel/winsor/categorical
    parameters cannot desync live from offline. It wants RAW values (it casts and
    clips them itself), so this assembles a raw_row and hands it over."""
    out = dict(_XGB_NONE_RESULT)
    if not _xgb_models:
        out['Xgb_status'] = 'not_loaded'
        return out
    if not _xgb_universe_ok:
        # The encoders still ran and their columns are still written; only the
        # predictions are suppressed, because 7 of the 9 structured features are
        # absent and a score built without them is not comparable to a real one.
        out['Xgb_status'] = 'disabled_universe'
        return out
    if embed_val.get('hd_pca') is None or embed_val.get('bd_pca') is None:
        out['Xgb_status'] = 'no_embedding'
        return out

    t0 = time.monotonic()
    try:
        sources = {
            # 'fmb' is deliberately absent: FinMarBa is scored on every PR for the
            # gate, but no model-2 feature reads it. Adding the key back is harmless;
            # adding a FEATURE that reads it is what --check-xgb would demand.
            'finbert': finbert_val, 'flang': flang_val,
            'earn': earn_val, 'hdx': hdx_val, 'body': body_val,
            'row': row_vals, 'univ': univ_row,
            'calc': {'TimeToOpen': time_to_open_h},
        }
        raw_row = {}
        for feat, (src, key) in _XGB_FEATURE_SOURCES.items():
            raw_row[feat] = sources[src].get(key)

        # The three sentinel columns build_row_features reads unconditionally. A
        # missing/blank cell becomes the 44444 sentinel, which is exactly what it
        # means upstream ("IBKR had no bars"), so the stored substitution applies.
        for col, (src, key) in _XGB_SENTINEL_COLS.items():
            v = _coerce_float(sources[src].get(key))
            raw_row[col] = XGB_SENTINEL if v is None else v

        hd_pca, bd_pca = embed_val['hd_pca'], embed_val['bd_pca']

        frames = [xgb.DMatrix(xgb_build_row_features(raw_row, prep, hd_pca, bd_pca),
                              enable_categorical=True)
                  for prep in _xgb_feature_orders]
        feat_ms = (time.monotonic() - t0) * 1000.0

        t1 = time.monotonic()
        for tsv_col, booster, prep, kind, it_range, group in _xgb_models:
            raw_pred = float(booster.predict(frames[group], iteration_range=it_range)[0])
            # Regressors were fit on asinh(target) after a train-fold upper cap, so
            # the prediction is inverted back into % units. Classifier output is
            # already sigmoid(logit); note it is NOT calibrated (each was fit with
            # scale_pos_weight 4.18/8.33/12.53), so it is a RANKING score, not a
            # literal probability for an EV calculation — ge50_m2 averages 0.357
            # against a 9.6% base rate. A fitted Platt calibration exists at
            # price_stats_loader/statsByClaude/model2_calibration_params.json; it is
            # deliberately NOT applied here (it is monotone, so it changes no ranking
            # and no top-N selection, it was fitted on model-2's own val fold, and
            # there is no model-2FT equivalent). Calibrate offline if you need odds.
            out[tsv_col] = round(
                xgb_invert_target(raw_pred, prep) if kind == 'regressor' else raw_pred, 6)
        predict_ms = (time.monotonic() - t1) * 1000.0

        out['Xgb_status'] = 'ok'
        out['Xgb_feat_ms'] = round(feat_ms, 1)
        out['Xgb_predict_ms'] = round(predict_ms, 2)
        out['Xgb_at'] = c3.format_millis_time(c3.now_fixed())
        logger.info(
            f"[Xgb] {symbol} → m2 se={out.get('se_m2')} ge20={out.get('ge20_m2')} "
            f"ge35={out.get('ge35_m2')} ge50={out.get('ge50_m2')} | "
            f"m2FT se={out.get('se_m2FT')} ge20={out.get('ge20_m2FT')} "
            f"(feat {feat_ms:.1f}ms + predict {predict_ms:.2f}ms)")
        return out
    except Exception as exc:
        logger.error(f"[Xgb] {symbol}: scoring error: {exc}", exc_info=True)
        out = dict(_XGB_NONE_RESULT)
        out['Xgb_status'] = 'error'
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
    # ── 5.3: the XGBoost leg ─────────────────────────────────────────────────
    # Registered so a rule CAN name one, but NONE is referenced by the shipped
    # orchestrator_trigger_rules.txt, so the gate is byte-identical to 5.2 on day
    # one. Naming any of these in a rule is what sets _gate_needs_xgb and makes
    # STEP-1b wait for the whole leg — deliberately a text edit plus a restart,
    # reported by --check-rules, never a silent change.
    #
    # The ten XGBoost predictions (2026-09-20: model-2 + model-2FT replaced
    # model-1's seven). ⚠ se_* and q80_* are in PERCENT, not 0-1 — a rule reading
    # `se_m2 >= 0.3` would mean 0.3%, so they get no [0,1] range. The six ge* are
    # 0-1 but are UNCALIBRATED ranking scores; pick a cut from a measured quantile
    # of your own tape, not from what the number looks like. Naming a single one of
    # these in a rule flips the whole leg to gate-blocking (_XGB_GATE_SOURCES).
    'se_m2':             _FieldSpec(_KIND_NUM, 'xgb', 'se_m2',        None, None, True),
    'q80_m2':            _FieldSpec(_KIND_NUM, 'xgb', 'q80_m2',       None, None, True),
    'ge20_m2':           _FieldSpec(_KIND_NUM, 'xgb', 'ge20_m2',       0.0, 1.0, True),
    'ge35_m2':           _FieldSpec(_KIND_NUM, 'xgb', 'ge35_m2',       0.0, 1.0, True),
    'ge50_m2':           _FieldSpec(_KIND_NUM, 'xgb', 'ge50_m2',       0.0, 1.0, True),
    'se_m2FT':           _FieldSpec(_KIND_NUM, 'xgb', 'se_m2FT',      None, None, True),
    'q80_m2FT':          _FieldSpec(_KIND_NUM, 'xgb', 'q80_m2FT',     None, None, True),
    'ge20_m2FT':         _FieldSpec(_KIND_NUM, 'xgb', 'ge20_m2FT',     0.0, 1.0, True),
    'ge35_m2FT':         _FieldSpec(_KIND_NUM, 'xgb', 'ge35_m2FT',     0.0, 1.0, True),
    'ge50_m2FT':         _FieldSpec(_KIND_NUM, 'xgb', 'ge50_m2FT',     0.0, 1.0, True),
    # The two headline encoders the leg adds, on the gate's own naming convention.
    'DeBERTa_ss_hd':     _FieldSpec(_KIND_NUM, 'hdx', 'DeBERTa_ss_hd',   -1.0, 1.0, True),
    'DeBERTa_lab_hd':    _FieldSpec(_KIND_STR, 'hdx', 'DeBERTa_lab_hd',  None, None, True),
    'TradeHero_ss_hd':   _FieldSpec(_KIND_NUM, 'hdx', 'TradeHero_ss_hd', -1.0, 1.0, True),
    'TradeHero_lab_hd':  _FieldSpec(_KIND_STR, 'hdx', 'TradeHero_lab_hd', None, None, True),
    # The body channel.
    'FinancialB_ss_bd':  _FieldSpec(_KIND_NUM, 'body', 'FinancialB_ss_bd',  -1.0, 1.0, True),
    'TradeHero_ss_bd':   _FieldSpec(_KIND_NUM, 'body', 'TradeHero_ss_bd',   -1.0, 1.0, True),
    'TradeHero_pos_bd':  _FieldSpec(_KIND_NUM, 'body', 'TradeHero_pos_bd',   0.0, 1.0, True),
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
    global _trigger_rules, _trigger_fields, _gate_needs_xgb
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

    # ── 5.3: does the gate depend on the XGBoost leg? ────────────────────────
    # This single flag is the whole switch between "STEP-1b goes out on the five
    # original scores at ~30 ms" and "STEP-1b waits ~900 ms for the XGBoost leg".
    # It is derived from the rules FILE, so turning XGB into a trade input is an
    # edit plus a restart — the same way every other gate change is made — and
    # never a code change or a silent default.
    #
    # The trigger is any field whose SOURCE is part of the leg, not just the
    # xgb predictions: a rule on DeBERTa_ss_hd or FinancialB_ss_bd needs the encoder
    # that produces it, which is in exactly the same futures.
    _gate_needs_xgb = any(spec.source in _XGB_GATE_SOURCES
                          for _name, spec in _trigger_fields)
    if _gate_needs_xgb:
        _using = sorted({n for n, sp in _trigger_fields
                         if sp.source in _XGB_GATE_SOURCES})
        logger.warning(
            "[Gate] XGB IS GATE-BLOCKING — %d rule field(s) come from the XGBoost "
            "leg (%s), so STEP-1b sentiment now WAITS for it instead of going out on "
            "the five original scores. MEASURED: 943 ms median from reception "
            "(p90 1030) vs 71 ms non-blocking, i.e. +872 ms on EVERY trade decision, "
            "more in a burst. The ARM is unaffected (17 ms). Remove those field(s) "
            "from %s to go back to non-blocking.",
            len(_using), ', '.join(_using), os.path.basename(path))

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
                       ('hdx', 'DeBERTa/TradeHero headline'),
                       ('body', 'Prosus/FinancialB/TradeHero body'),
                       ('xgb', 'XGBoost'), ('calc', 'TimeToOpen')):
        used = sorted({f"{r.name}{' (REQUIRE)' if r.kind == _RULE_REQUIRE else ''}"
                       for r in _trigger_rules
                       for c in r.conds if c.spec.source == src})
        if used:
            logger.info("[Gate]   %s feeds rule(s): %s", label, ', '.join(used))
        elif src not in _GATE_NO_WARN_SOURCES:
            logger.warning("[Gate]   %s is scored on EVERY PR but NO rule reads it — "
                           "it costs latency and gates nothing", label)
    # The 5.3 leg not feeding a rule is the SHIPPED state, not a fault, so it gets a
    # statement of fact rather than the warning above — which exists for a gate model
    # that is being paid for and ignored.
    logger.info("[Gate]   XGBoost leg: %s",
                "GATE-BLOCKING — STEP-1b waits for it" if _gate_needs_xgb else
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
                      hdx_val: dict = None, body_val: dict = None,
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
    # The four 5.3 sources are APPENDED with defaults, never inserted: the first six
    # stay positional so anything that calls this the 5.2 way keeps working and simply
    # sees the new fields as absent. A rule naming one of them on such a call reads
    # MISSING, which is honest — that caller genuinely did not score it.
    sources = {'finbert': finbert_val, 'fmb': finmarba_val, 'flang': flang_val,
               'kw': kw_val, 'earn': earn_val, 'row': row_vals,
               'hdx': hdx_val or {}, 'body': body_val or {},
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
    # ── 5.3: the XGBoost leg (2026-09-18; model-2 from 2026-09-20) ──────────
    # Appended LAST again, so every column above keeps its ordinal and anything that
    # reads this file positionally up to col 42 is unaffected.
    #
    # BroadClass is PrClassifier's grouped rollup, the companion to FineClass. 5.2
    # computed and discarded it. It was a model-1 feature and is not a model-2 one,
    # but it is kept because it falls out of the SAME classify_pr call as FineClass —
    # so the two can never describe different texts, and it costs nothing.
    # TimeToOpen is hours from the 04:00 ET ETH open of the PR's own session —
    # NEGATIVE means the market has not opened yet.
    'BroadClass', 'TimeToOpen',
    # The two headline encoders the leg adds, in the unified
    # {Model}_{channel}_{hd|bd} convention that HeadScore11encoders writes offline —
    # so an offline rescore of this file's Headline column reproduces these cells.
    # Only DeBERTa_ss_hd and TradeHero_ss_hd feed the models; the rest of each block
    # is stored for offline re-analysis, the same bargain the FLANG block has had
    # since 2026-07-29.
    # ⚠ The RoBERTa_*_hd block was REMOVED on 2026-09-20 with the model-1 cut —
    # model-2 does not read RoBERTa_neg_hd.1 and nothing else did.
    'DeBERTa_at_hd', 'DeBERTa_pos_hd', 'DeBERTa_neu_hd', 'DeBERTa_neg_hd',
    'DeBERTa_ss_hd', 'DeBERTa_lab_hd', 'DeBERTa_ms_hd',
    'TradeHero_at_hd', 'TradeHero_pos_hd', 'TradeHero_neu_hd', 'TradeHero_neg_hd',
    'TradeHero_ss_hd', 'TradeHero_lab_hd', 'TradeHero_ms_hd',
    # The BODY channel — the first body scores this pipeline has ever written live.
    # Both score the 200-WORD prepped body (XGB_BODY_CAP_WORDS), which is exactly how
    # the offline *_bd columns were built (BodyScore11encoders --cap-words 200), so an
    # offline rescore of this file's Body cell reproduces them with NO exceptions.
    # ⚠ The Prosus_*_bd block was REMOVED on 2026-09-20. It was the one block that did
    # NOT reproduce (FinBERT_TSV_scorer.py fed the uncapped cell), and model-2 reads no
    # Prosus body feature — so dropping it retired the divergence and saved ~91 ms at
    # the same time. See XGB_BODY_CAP_WORDS for the full history.
    'FinancialB_at_bd', 'FinancialB_pos_bd', 'FinancialB_neu_bd', 'FinancialB_neg_bd',
    'FinancialB_ss_bd', 'FinancialB_lab_bd', 'FinancialB_ms_bd',
    'TradeHero_at_bd', 'TradeHero_pos_bd', 'TradeHero_neu_bd', 'TradeHero_neg_bd',
    'TradeHero_ss_bd', 'TradeHero_lab_bd', 'TradeHero_ms_bd',
    # The predictions — model-2 then model-2FT, same five kinds each, on the SAME
    # 25-feature vector. m2 early-stopped against a held-out fold; m2FT refit the same
    # configuration on 100% of the rows with no fold left to validate against, so its
    # numbers carry strictly less evidence (and q80_m2FT least of all — 4000 rounds,
    # no early-stop safety net).
    # ⚠ se_* and q80_* are in PERCENT (predicted fromPR_High(%)), not 0-1.
    # ⚠ The six ge* ARE 0-1 but are NOT calibrated probabilities — each was fit with
    # scale_pos_weight, which multiplies the modelled odds, so ge50_m2 averages 0.357
    # against a 9.6% base rate. They rank; they do not state odds. A fitted Platt
    # calibration for the model-2 three lives in price_stats_loader/statsByClaude/
    # model2_calibration_params.json and is deliberately applied OFFLINE, not here.
    'se_m2', 'q80_m2', 'ge20_m2', 'ge35_m2', 'ge50_m2',
    'se_m2FT', 'q80_m2FT', 'ge20_m2FT', 'ge35_m2FT', 'ge50_m2FT',
    # ⚠ Xgb_status is what keeps a blank prediction unambiguous: 'ok',
    # 'disabled_universe' (the universe TSV predates daily_universe_pipeline-3, so 7
    # of the 9 structured features do not exist), 'no_embedding', 'timeout',
    # 'not_loaded' or 'error'. A blank se_m2 with status 'ok' would be a bug; with
    # any other status it is the documented behaviour.
    'Xgb_status',
    # Per-stage inference timings, in ms. The first three are the model stages and are
    # written even when the prediction itself is suppressed, so a slow encoder is
    # visible on a row that produced no score. Xgb_total_ms is the WALL CLOCK from PR
    # reception to the prediction landing — the number that answers "how late is the
    # score?", and the only one that includes queueing behind another PR.
    'Xgb_hdx_ms', 'Xgb_body_ms', 'Xgb_embed_ms', 'Xgb_feat_ms', 'Xgb_predict_ms',
    'Xgb_total_ms',
    # Completion stamp on the connector's fixed UTC-4 clock (the *_at_* convention).
    'Xgb_at',
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


def _universe_row(symbol: str) -> dict:
    """Every cell for `symbol` in ONE pass — {} if the df is unloaded or the symbol
    is absent.

    5.3 needs 13 more universe columns than 5.2 did, and _universe_cell takes
    _iti_df_lock and does a full pandas mask scan PER COLUMN. Nineteen of those per
    row would be wasteful; this does one lock acquisition and one scan, so adding the
    model features makes the hot path cheaper rather than 13x more expensive.

    Values come back RAW (strings, NaN, 'skipped_nan'); every caller goes through
    _coerce_float, exactly as with _universe_cell."""
    with _iti_df_lock:
        df = _iti_df
    if df is None:
        return {}
    match = df.index[df['Symbol'] == symbol]
    if len(match) == 0:
        return {}
    return df.loc[match[0]].to_dict()


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
    stale file is therefore not a partial degradation, it is 7 of the 9 structured
    features gone at once, which is why it is detected explicitly rather than left to
    read as a row of NaNs.

    The column list is DERIVED from _XGB_FEATURE_SOURCES, so this checks exactly what
    the loaded models read and nothing more — model-1 needed 13 of these, model-2
    needs 7, and neither number is written down anywhere else."""
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
                     f_leg=None, recv_t0=None) -> None:
    """Resolve the scoring futures, evaluate the trade gate, complete the clerk
    handshake (wait for the STEP-1a arm reply, then send the STEP-1b sentiment), run
    the XGBoost leg, and write the TSV row.

    Ordering is load-bearing: we wait on the arm future *before* sending sentiment
    so the clerk has registered the session — required both for the OK gate and for
    the BAD early-release to land. The FLANG, kw and earnings futures are resolved
    BEFORE the arm wait (adds only ms; the arm reply wait already dominates), each
    with a hard ceiling (FLANG_SCORE_WAIT_SEC / KW_SCORE_WAIT_SEC /
    EARN_SCORE_WAIT_SEC) so a hung model can't stall the trade decision. All the
    scoring futures were submitted together in _on_row_built and run in parallel, so
    resolving them back-to-back here costs ~max(model), not the sum.

    ⚠ 5.3 — WHERE THE XGBoost LEG SITS RELATIVE TO THE TRADE IS CONDITIONAL, and the
    condition is `_gate_needs_xgb`, derived from the rules FILE at startup:

      _gate_needs_xgb True — THE SHIPPED STATE since 2026-09-20, when the XgbSolo
        rule (ge50_m2 >= 0.70) was added. The leg is resolved BEFORE the gate,
        because a gate cannot be evaluated on a score that has not been computed,
        so every sentiment send waits for it.
      _gate_needs_xgb False — what you get by commenting XgbSolo out. The gate is
        evaluated on the five original scores and STEP-1b goes out at 5.2's
        latency; the leg is resolved AFTERWARDS and only delays the TSV row, which
        nothing trades on.

    MEASURED, single PRs one at a time, 9 real articles:
      gate-blocking (today)  ARM  17 ms · STEP-1b  943 ms  (p90 1030, 806..1030)
      observational          ARM ~17 ms · STEP-1b   71 ms  (TSV row at 963 ms)
    So the rule costs +872 ms on the trade DECISION and nothing on the arm — the
    clerk still registers the session at reception either way. That difference is
    the whole reason this fork exists, and it is one line in the rules file.

    This is why the row is assembled at the END rather than before the clerk branch
    as it was in 5.2: ClerkSentiment and the XGBoost columns are now produced on
    either side of that fork, so the single completed_dict literal has to come after
    both. `recv_t0` is the time.monotonic() stamped in _on_row_built, which is what
    makes Xgb_total_ms a true reception-to-prediction wall clock including queueing."""
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

    # 2d) The XGBoost leg's own state. Filled by _resolve_leg() below — either here
    #     (when a rule needs it) or after the sentiment send (the shipped path).
    leg = {'hdx': {}, 'body': {}, 'embed': dict(_EMBED_NONE_RESULT),
           'calc': {}, 'xgb': dict(_XGB_NONE_RESULT), 'done': False}

    def _resolve_leg():
        """Resolve the three 5.3 futures, compute TimeToOpen, and score the XGBoost
        family. Idempotent — called from exactly one of two places depending on
        _gate_needs_xgb, and the `done` flag makes a double call harmless.

        Each future is resolved defensively: a failure leaves its block empty and the
        XGBoost prediction is suppressed with an explanatory Xgb_status, rather than
        scoring a vector with silent NaNs in it."""
        if leg['done']:
            return
        leg['done'] = True
        try:
            if f_leg is not None:
                parts = f_leg.result(timeout=XGBLEG_WAIT_SEC)
                leg['hdx'], leg['body'] = parts['hdx'], parts['body']
                leg['embed'] = parts['embed']
        except Exception as exc:
            # The whole leg is ONE future, so a failure loses all three blocks at
            # once. That is the right granularity: the XGBoost vector needs every one
            # of them, so a partial result could not be scored anyway.
            logger.error(f"[XgbLeg] {symbol}: leg future failed/timed out for "
                         f"id={news_id}: {exc}")

        leg['calc'] = {'TimeToOpen': _compute_time_to_open(news_dict)}
        # One universe scan for the 13 market-state features + the 3 sentinel
        # columns. Separate from row_vals, which holds the gate-addressable fields.
        univ_row = _universe_row(symbol)
        leg['xgb'] = _score_xgb(news_dict, symbol, finbert_val, finmarba_val,
                                flang_val, earn_val, leg['hdx'], leg['body'],
                                leg['embed'], row_vals, univ_row,
                                leg['calc']['TimeToOpen'])
        if recv_t0 is not None:
            leg['xgb']['Xgb_total_ms'] = round((time.monotonic() - recv_t0) * 1000.0, 1)

    # 2e) Does the leg actually DECIDE this row? A rule reads it, so the naive answer
    #     is "always wait" — and from 2026-09-20 to 2026-09-21 that is what happened,
    #     costing ~900 ms on EVERY PR, including ones the class whitelist had already
    #     blocked and ones that fire on CORE or EARNINGS without ever reading a
    #     model-2 score. _gate_early_decision proves, monotonically, when the verdict
    #     is already forced; only a genuinely undecided row pays the wait.
    #     Measured over 890 tape rows: 65.7% forced BAD, 3.7% forced OK, 30.6% wait.
    early = (_gate_early_decision(finbert_val, finmarba_val, flang_val, kw_val,
                                  earn_val, row_vals) if _gate_needs_xgb else None)
    if _gate_needs_xgb and early is None:
        _resolve_leg()

    # 3) Trade gate from orchestrator_trigger_rules.txt — every REQUIRE precondition
    #    AND an OR of the ordinary rules → decision + label + Trigger cell
    is_ok, trigger_label, trigger_detail, missing_blocked = _evaluate_trigger(
        finbert_val, finmarba_val, flang_val, kw_val, earn_val, row_vals,
        hdx_val=leg['hdx'], body_val=leg['body'], xgb_val=leg['xgb'],
        calc_val=leg['calc'])
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

    # ⚠ On a short-circuited row the cells above are the leg's BLANKS, not its
    #   values, so `detail` would read ge50_m2:MISSING and `missing_blocked` would be
    #   an artefact of our own skipping. Defer the log to the re-evaluation at 5b.
    if early is None:
        _log_gate(is_ok, trigger_detail, missing_blocked)

    # 4) Build the row. Created/Arrival*/Author/Exchange are pass-throughs from
    #    the reception (all stamped on the connector's fixed UTC-4 clock). The
    #    Body CELL stores the RAW payload as RTPR sent it (HTML tags/entities
    #    kept, if any) with only whitespace runs collapsed so the TSV stays
    #    one-row-per-line — the SAME string _score_keywords scored, so an
    #    offline rescore of this TSV reproduces the live combo exactly.
    #
    # ⚠ 5.3 assembles this AFTER the clerk branch, where 5.2 built it before and then
    #    overwrote ClerkSentiment in place. The reason is the fork above: on the
    #    shipped non-blocking path the XGBoost columns do not exist yet at this point
    #    in 5.2's ordering. One literal containing every cell — including
    #    ClerkSentiment — is what keeps the row impossible to half-fill.
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
    #      armed        → "<fired legs joined by '_', or BAD>:<reply status>", e.g.
    #                     "Keyword_FinBERT_FMB_FLANG:sent" (core branch), "Earnings:sent",
    #                     "KwBodySolo:sent", "FLANG_FLANGSolo:sent", "BAD:sent". The
    #                     2026-08-01 AND made this degenerate (two values); the 08-02
    #                     earnings branch and the 08-03 solo branches made it informative
    #                     again — the label names exactly which legs held.
    #      arm rejected → "skipped:arm_rejected"
    #      never armed  → "skipped:excluded_string" / "skipped:not_armed"
    #    (arm accept/reject detail stays in the log via _arm_clerk).
    _sent_status = None          # set only on the branch that actually sends STEP-1b
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
            sentiment_reply = _send_sentiment(symbol, is_ok)
            _sent_status = sentiment_reply.get('status', 'no_reply') or 'no_reply'
            clerk_sentiment = f"{trigger_label}:{_sent_status}"

    # 5b) The XGBoost leg, on the shipped non-blocking path: the trade decision has
    #     already been sent, so these ~900 ms delay only this TSV row. No-op when
    #     _gate_needs_xgb already forced it before the gate.
    _resolve_leg()

    if early is not None:
        # We answered the clerk without waiting. Now that the leg has landed, re-run
        # the SAME gate over the COMPLETE inputs, so the TSV keeps a full Trigger
        # cell and the dead-scorer alarm judges real values instead of the blanks our
        # own short-circuit left behind. This costs microseconds — it is a pass over
        # already-computed numbers, no model runs again.
        f_ok, f_label, trigger_detail, missing_blocked = _evaluate_trigger(
            finbert_val, finmarba_val, flang_val, kw_val, earn_val, row_vals,
            hdx_val=leg['hdx'], body_val=leg['body'], xgb_val=leg['xgb'],
            calc_val=leg['calc'])
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
    completed_dict.update(leg['hdx'])
    completed_dict.update(leg['body'])
    completed_dict.update(leg['xgb'])
    completed_dict['BroadClass'] = earn_val.get('BroadClass')
    completed_dict['TimeToOpen'] = leg['calc'].get('TimeToOpen')
    # The three model-stage timings are written even when the PREDICTION was
    # suppressed, so a slow or dead encoder stays visible on a row that scored
    # nothing. (_enc_block's per-model *_ms_* columns are the models' own reported
    # inference times; these are the wall clock of the whole pooled task, which also
    # captures queueing behind another PR.)
    completed_dict['Xgb_hdx_ms']   = leg['hdx'].get('_hdx_ms')
    completed_dict['Xgb_body_ms']  = leg['body'].get('_body_ms')
    completed_dict['Xgb_embed_ms'] = leg['embed'].get('_embed_ms')
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
    # The body is prepped ONCE, here on the feed thread, BEFORE anything is submitted:
    # the PR classifier (in the earnings task), the three body encoders and the
    # embedder all score THE SAME STRING. That is the property that makes an offline
    # rescore of the TSV reproduce these cells, and two independent preps could
    # silently diverge. A deliberate exception to "keep _on_row_built lean":
    # prep_body is regex-only and sub-ms once warm.
    recv_t0 = time.monotonic()
    body_200 = _prep_body_200(news_dict)

    f_finbert  = _finbert_executor.submit(analyze_finbert, news_dict)
    f_finmarba = _finmarba_executor.submit(analyze_finmarba, news_dict)
    f_flang    = _flang_executor.submit(analyze_flang, news_dict)
    f_kw       = _kw_executor.submit(_score_keywords, news_dict)
    # 5.3 / 2026-09-21: the catalyst class is resolved HERE, on the feed thread, for
    # two consumers at once — the earnings task (which used to compute it itself) and
    # _leg_priority, which must know it BEFORE the leg is queued. Net cost is zero:
    # the work moved, it did not duplicate. ~0.2 ms, the same justification as
    # _prep_body_200 above.
    preclass = _classify_article(news_dict['Headline'], body_200)
    f_earn     = _earn_executor.submit(_classify_and_score_earnings,
                                       news_dict, primary_symbol, body_200, preclass)

    # ── 5.3: the XGBoost leg, submitted HERE with the rest ───────────────────
    # Submitting at reception rather than inside _collect_and_log is what lets the
    # ~900 ms of model time overlap the clerk-arm network round-trip. It is
    # per-ARTICLE like the five above: the text is identical for both tickers of a
    # 2-ticker PR, so it is scored once and the per-TICKER XGBoost prediction is
    # built from it later, in _collect_and_log.
    #
    # body_200 was built above, before the earnings submit, because the PR classifier
    # reads it too — see the comment there.
    #
    # ⚠ Submitted with a PRIORITY, not FIFO (2026-09-21). A PR the class whitelist
    # has already blocked can never trade, so it must not sit in front of one that
    # can — see _leg_priority and the _PriorityExecutor docstring for the burst
    # measurement that forced this.
    _leg_prio = _leg_priority(*(preclass or (None, None)))
    f_leg = _xgbleg_executor.submit(_leg_prio, _score_xgb_leg, news_dict, body_200)
    if _leg_prio == _PRIO_CORPUS:
        logger.info("[XgbLeg] id=%s class=%s cannot trade — deferred to the corpus "
                    "lane; queue depth %d", art_id,
                    (preclass or (None,))[0], _xgbleg_executor.qsize())

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
            f_leg, recv_t0,
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
    global _xgb_models, _xgb_feature_orders, _xgb_universe_ok
    logger.info("Orchestrator5.3 starting...")

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
    # ever put in this live-gated class were wrong. For the same reason the move of
    # the body fallback to the 200-word _prep_body_200 string leaves this check — and
    # the Earnings_Results class itself — untouched: neither can come from a body.
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

    # ── 5.3: the XGBoost leg ─────────────────────────────────────────────────
    # Every one of these hard-fails if the artifact is missing or the shape is
    # wrong, the same contract the five pre-warms above have: a trading process must
    # not run with a permanently-dead scorer, which here would mean an XGBoost vector
    # quietly full of NaNs producing plausible-looking numbers forever.
    logger.info("Pre-warming the XGBoost leg's encoders "
                "(DeBERTa + TradeHero + FinancialBERT)...")
    load_deberta_model()
    load_tradehero_model()
    load_financialb_model()
    _smoke_hd = "Acme Corp announces FDA approval of its lead drug candidate"
    for _name, _fn in (('DeBERTa', score_deberta), ('TradeHero', score_tradehero),
                       ('FinancialB', score_financialb)):
        _r = _fn(_smoke_hd)
        if not all(k in _r for k in ('pos', 'neg', 'ss', 'lab')):
            raise RuntimeError(f"{_name} headliner returned an unexpected shape "
                               f"({sorted(_r)}) — refusing to start")
    logger.info("XGBoost-leg encoders ready (smoke: DeBERTa_ss=%.4f "
                "TradeHero_ss=%.4f FinancialB_ss=%.4f).",
                score_deberta(_smoke_hd)['ss'],
                score_tradehero(_smoke_hd)['ss'], score_financialb(_smoke_hd)['ss'])

    # body_prep lazily imports tools/keyword/keyword_finder.py (pandas + sklearn) on
    # its FIRST call. Warm it here so that import never lands on a live PR.
    _smoke_body = prep_body("Acme Corp today announced record quarterly revenue.",
                            _smoke_hd, XGB_BODY_CAP_WORDS)
    if not _smoke_body:
        raise RuntimeError("body_prep.prep_body returned empty on a non-empty body "
                           "— refusing to start")
    logger.info("body_prep ready (cap=%d words, smoke: %d words out).",
                XGB_BODY_CAP_WORDS, len(_smoke_body.split()))

    logger.info("Pre-warming the FinBERT embedder + PCA projection...")
    load_embed_model()
    load_pca()
    _smoke_vecs = embed_texts([_smoke_hd, _smoke_body])
    if _smoke_vecs.shape != (2, EMBED_DIM):
        raise RuntimeError(f"embed_texts returned {_smoke_vecs.shape}, expected "
                           f"(2, {EMBED_DIM}) — refusing to start")
    _smoke_pcs = transform_pr({'hd': _smoke_vecs[0], 'bd': _smoke_vecs[1]})
    logger.info("FinBERT embedder + PCA ready (%d-dim → %d hd_pca / %d bd_pca comps).",
                EMBED_DIM, len(_smoke_pcs['hd_pca']), len(_smoke_pcs['bd_pca']))

    logger.info("Loading the XGBoost models...")
    _xgb_models, _xgb_feature_orders = _load_xgb_models()
    logger.info("XGBoost ready — %d model(s), %d distinct feature order(s): %s",
                len(_xgb_models), len(_xgb_feature_orders),
                ', '.join(f"{c}@iter{r[1] - 1 if r else 'all'}"
                          for c, _b, _p, _k, r, _g in _xgb_models))

    # The XNYS session index behind TimeToOpen. Built once, spanning a year either
    # side so it survives a year boundary; rebuilt daily beside the universe reload.
    _refresh_sessions()

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

    # ── 5.3: does this universe file carry the XGBoost feature columns? ──────
    # Only daily_universe_pipeline-3 files do (147 cols, first written 2026-09-17).
    # An older 38-column file is missing 7 of the 9 structured features at once.
    _xgb_universe_ok, _missing = _universe_supports_xgb(df)
    if _xgb_universe_ok:
        logger.info("[Universe] pipeline-3 schema confirmed — all %d XGBoost feature "
                    "columns present.", len(XGB_UNIVERSE_COLS))
    elif _gate_needs_xgb:
        # A rule reads the leg, so there is no safe way to continue: the gate cannot
        # be evaluated on features that do not exist, and failing closed here is the
        # same policy a malformed rules file gets.
        raise RuntimeError(
            f"the newest universe TSV is missing {len(_missing)} XGBoost feature "
            f"column(s) {_missing} — and the loaded gate READS the XGBoost leg, so "
            f"it cannot be evaluated. Run daily_universe_pipeline-3.py, or remove the "
            f"XGBoost field(s) from {os.path.basename(TRIGGER_RULES_FILE)}")
    else:
        # Trading is NOT stopped: the gate does not read the leg, so the five original
        # scores decide exactly as they did in 5.2. Only the predictions are
        # suppressed, and Xgb_status says so on every row rather than leaving a blank
        # that could be mistaken for a quiet scorer.
        logger.warning(
            "[Universe] this universe TSV predates daily_universe_pipeline-3 and is "
            "missing %d XGBoost feature column(s): %s. XGB PREDICTIONS ARE DISABLED "
            "(Xgb_status='disabled_universe' on every row); the encoders still run and "
            "their columns are still written. TRADING IS UNAFFECTED — the gate does not "
            "read the XGBoost leg. Run daily_universe_pipeline-3.py to restore it.",
            len(_missing), _missing)

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
        _xgbleg_executor.shutdown(wait=True)
        _collect_executor.shutdown(wait=True)
        logger.info("Orchestrator5.3 stopped.")


def _check_xgb() -> int:
    """--check-xgb: load the models and prove the feature vector can be built, then
    exit. The analogue of --check-rules for the XGBoost leg — ~2 s, no sockets, no
    encoders, no feed.

    It answers the three questions that otherwise only surface on a live PR:
      1. do all ten .ubj/_prep.json artifacts load, across BOTH model dirs?
      2. does _XGB_FEATURE_SOURCES cover every feature of every model? (a retrain
         that adds one fails HERE, naming it, instead of scoring a silent NaN)
      3. does the newest universe TSV carry the pipeline-3 feature columns?
    and then actually scores a synthetic row, so a preprocessing mismatch cannot hide
    behind a successful load. The synthetic score is what catches a per-generation
    predict() problem — it is how the model-2FT iteration_range crash was found."""
    global _xgb_models, _xgb_feature_orders, _xgb_universe_ok
    _load_trigger_rules(TRIGGER_RULES_FILE)
    _xgb_models, _xgb_feature_orders = _load_xgb_models()
    _dirs = {c: d for c, d, _s, _k in XGB_MODELS}
    print(f"[Xgb] {len(_xgb_models)} model(s) from "
          f"{len(set(_dirs.values()))} dir(s), "
          f"{len(_xgb_feature_orders)} distinct feature order(s):")
    for tsv_col, booster, prep, kind, it_range, group in _xgb_models:
        thr = prep.get('threshold')
        # `built` vs `trees` differ exactly when a model early-stopped and kept its
        # overfit tail; equal means the round count was fixed (model-2FT).
        print(f"  {tsv_col:<10s} {_dirs[tsv_col]:<10s} {kind:<10s} "
              f"{len(prep['features_used']):>2d} feat  group={group}  "
              f"trees={it_range[1]}/{booster.num_boosted_rounds()} built"
              f"{f'  threshold>={int(thr)}' if thr else ''}")

    feats = sorted({f for _c, _b, p, _k, _r, _g in _xgb_models
                    for f in p['features_used']})
    structured = [f for f in feats
                  if not (f.startswith('hd_pca_') or f.startswith('bd_pca_'))]
    print(f"\n[Xgb] {len(feats)} distinct feature(s): {len(structured)} structured "
          f"+ {len(feats) - len(structured)} PCA")
    by_src = {}
    for f in structured:
        by_src.setdefault(_XGB_FEATURE_SOURCES[f][0], []).append(f)
    for src in sorted(by_src):
        print(f"  {src:<8s} ({len(by_src[src]):>2d}) {', '.join(sorted(by_src[src]))}")

    _load_latest_iti_tsv()
    with _iti_df_lock:
        df = _iti_df
    _xgb_universe_ok, missing = _universe_supports_xgb(df)
    print(f"\n[Xgb] universe: {'OK — pipeline-3 schema' if _xgb_universe_ok else 'MISSING ' + str(missing)}")
    print(f"[Xgb] gate-blocking: {'YES' if _gate_needs_xgb else 'no'}")

    # Score a synthetic row end-to-end. Values are deliberately mid-range rather than
    # zeros so a winsor bound or a categorical mapping that silently drops everything
    # still produces a distinguishable number.
    smoke_row = {'Symbol': '_SMOKE_', 'Float': 5.0, 'MktCap': 20.0,
                 'Exchange': 'NASDAQ', 'LastDailyClose': 2.0,
                 'itiBaseline': 10.0, 'tradeSizeBaseline': 500.0, 'TickerCount': 1}
    smoke_univ = {c: 1.0 for c in XGB_UNIVERSE_COLS}
    smoke_univ.update({'RTH_avgITI_sec': 10.0, 'ETH_avgITI_sec': 30.0,
                       'RTH_tradeSize': 500.0})
    res = _score_xgb({'Symbol': '_SMOKE_'}, '_SMOKE_',
                     {'ss': 0.5}, {'ind': 0.1}, {'ss': 0.5},
                     {'FineClass': 'MA', 'BroadClass': 'MA'},
                     {'DeBERTa_ss_hd': 0.5, 'TradeHero_ss_hd': 0.5},
                     {'FinancialB_ss_bd': 0.5, 'TradeHero_pos_bd': 0.5,
                      'TradeHero_ss_bd': 0.5},
                     {'hd_pca': [0.0] * 40, 'bd_pca': [0.0] * 40},
                     smoke_row, smoke_univ, 1.5)
    print(f"\n[Xgb] synthetic row → status={res['Xgb_status']} "
          f"feat={res['Xgb_feat_ms']}ms predict={res['Xgb_predict_ms']}ms")
    for col, _dir, _stem, _kind in XGB_MODELS:
        print(f"  {col:<10s} {res[col]}")
    if res['Xgb_status'] != 'ok':
        print(f"\nFAILED: synthetic scoring returned {res['Xgb_status']}")
        return 1
    # A blank prediction under status 'ok' is the shape the model-2FT
    # iteration_range bug would have taken if it had not raised — assert it away.
    blank = [c for c, _d, _s, _k in XGB_MODELS if res[c] is None]
    if blank:
        print(f"\nFAILED: status ok but {len(blank)} prediction(s) blank: {blank}")
        return 1
    print("\n[Xgb] OK")
    return 0


if __name__ == '__main__':
    if '--check-xgb' in sys.argv:
        # The XGBoost-leg twin of --check-rules. Run it after any retrain, and after
        # any change to _XGB_FEATURE_SOURCES.
        sys.exit(_check_xgb())
    if '--check-rules' in sys.argv:
        # Parse + echo orchestrator_trigger_rules.txt, then exit. No models, no
        # sockets, no universe read: ~1 s. This is the fast edit/verify loop for the
        # rules file, and the mitigation that keeps a typo from turning the
        # refuse-to-start policy into a trading outage. Exit 0 means the gate echoed
        # here is exactly the gate O5.3 would run.
        _load_trigger_rules(TRIGGER_RULES_FILE)
        _log_active_gate()
        sys.exit(0)
    main()
