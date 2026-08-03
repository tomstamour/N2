import sys
import os
import csv
import json
import logging
import threading
import socket
import time
import importlib.util as _ilu
import joblib
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

# ── FLANG-BERT import (3rd headline model, 2026-07-29) ───────────────────────
# avinasht/FLANG-BERT_flang-bert (ONNX INT8) — its `sentiment_score`
# (positive − negative) is the STANDALONE 3rd trade leg (see _evaluate_trigger).
# This is the SAME module that produced the offline FLANG-BERT_sentiment_score
# column via tools/Encoder_Models/HeadScore11encoders.py, so the live score equals
# an offline rescore of the same headline — which is what the >= FLANG_SCORE_MIN
# cutoff was calibrated on. Do NOT edit the headliner or its
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

# Trade-trigger gate (STEP 1b) — an OR of FOUR branches as of 2026-08-03. A row trades
# when ANY ONE of them holds:
#   1. CORE      — the five-way AND defined immediately below (2026-08-01)
#   2. EARNINGS  — FineClass/EarningScore/kw15_body (2026-08-02; block further down)
#   3. KwBodySolo— kw15_body  >= KW15_BODY_SOLO_MIN alone   (2026-08-03; block below)
#   4. FLANGSolo — FLANG score >= FLANG_SOLO_MIN alone      (2026-08-03; block below)
#
# THIS block defines branch 1 only. It is an AND over ALL FIVE conditions — the trade
# fires on this branch only when EVERY one holds (2026-08-01; version-control.txt #4):
#   FinBERT    `positive`        >= SENTIMENT_POSITIVE_MIN
#   FinMarBa   `indecisive`      <= FMB_INDECISIVE_MAX     (the only ceiling)
#   ge15 body  `model_proba`     >= KW15_BODY_MIN
#   ge15 head  `model_proba`     >= KW15_HEAD_MIN
#   FLANG-BERT `sentiment_score` >= FLANG_SCORE_MIN
# The three LEGS (headline = FinBERT+FMB, keyword = body+head, FLANG) survive as names
# in ClerkSentiment, but since each leg is itself an AND, "all three legs" and "all five
# conditions" are the SAME boolean function — within this branch the legs are a
# grouping, not alternatives.
#
# ⚠ The 2026-08-03 solo branches SUBSUME this one wherever they hold, because they read
# two of these same five scores at higher cuts. This branch (and the earnings one) is
# therefore only DECISIVE for rows with kw15_body in [KW15_BODY_MIN, KW15_BODY_SOLO_MIN)
# — currently [0.50, 0.70) — and FLANG below FLANG_SOLO_MIN. The five thresholds below
# were calibrated when this AND was the whole gate; that calibration describes the
# branch, not the gate, from 2026-08-03 on.
#
# ⚠ MISSING scores. A None (model error / scorer timeout) makes only its OWN condition
# false — but within this AND branch that single false blocks it entirely, and with four
# branches now running a dead model no longer shows up as a silent tape at all. That is
# why _evaluate_trigger returns `missing_blocked` and the caller logs a WARNING when a
# MISSING score is the ONLY thing standing between the row and a fire; from 2026-08-03
# that alarm also fires whenever kw15_body or FLANG is None, since each of those alone
# is a whole branch.
# The wire protocol to the clerk stays "OK"/"BAD"; the trigger source is in the TSV only
# — ClerkSentiment (fired leg names joined with "_" in the fixed order Keyword,
# FinBERT_FMB, FLANG, Earnings, KwBodySolo, FLANGSolo) and the Trigger column, which
# drops a level below the legs and records YES/NO plus a PASS/FAIL/MISSING token per
# CONDITION with its score and the threshold in force, so a row shows WHICH condition
# failed and by how much.
#
# Calibration of the AND gate AND all five thresholds (2026-08-01), on
# price_stats_loader/outputs/
#   06_PRstats_2026-06-22-2026-07-30_11EncBodyHeadScored_kwScored_FinBERT.tsv
# (1055 labelled rows; the full-coverage FinBERT_positive / FinMarBa_indecisive cols):
#                                     fires         P@>=30%  lift    recall
#   OLD  OR  0.6/0.13/0.60/0.65/0.97   267 (25.3%)   32.6%   1.90x   48.1%
#   NEW  AND 0.8/0.14/0.50/0.50/0.96    26 ( 2.5%)   46.2%   2.69x    6.6%
# At the >=15% target the AND gate runs 80.8% precision / 2.51x lift. The retuned
# thresholds are matched TO the AND: loosening kw15 to 0.50/0.50 and FLANG to 0.96 more
# than doubles the fire count vs AND-with-the-old-thresholds (11 -> 26) at essentially
# unchanged precision, while FinBERT tightens 0.6 -> 0.8. Deliberate trade: ~1 trade/day
# at a much higher hit rate, accepting the large recall drop.
# ⚠ n=26 — 46.2% carries roughly a +/-19pp CI and is post-selection (the thresholds were
# chosen against this same file). Treat it as an operating point, not a measurement.
#
# ⚠ 2026-07-28 keyword-leg REPLACEMENT (historical — explains OLD TSVs). `Keyword` in
# ClerkSentiment means a DIFFERENT rule before and after that date. Was: body combo
# `model_proba_PLUS_kw_mult_nb` >= 0.67 from the biased-label model. Now: the
# corrected-label ge15 body+headline pair. Its 0.60/0.65 operating point (75 fires,
# 57.3% precision, 3.18x lift on the 777-row VAL holdout) is SUPERSEDED by the
# 2026-08-01 calibration above — the cuts are now 0.50/0.50 and the body+head PAIR no
# longer fires on its own (though from 2026-08-03 the body channel alone does, at 0.70).
# Original derivation: price_stats_loader/statsByClaude/
# July28_SentimentScore_stats_report.md §5.1-5.4.
#
# ⚠ 2026-07-29 FLANG leg ADDED (historical — explains OLD TSVs). ClerkSentiment gained
# the labels FLANG, Keyword_FLANG, FinBERT_FMB_FLANG and Keyword_FinBERT_FMB_FLANG from
# that date, and the TSV grew the 7 FLANG_* columns (31 -> 38). The 2026-08-01 AND left
# only the last of those four reachable; the 08-02 and 08-03 branches reopened the
# vocabulary and added Earnings, KwBodySolo and FLANGSolo to it.
SENTIMENT_POSITIVE_MIN = 0.8     # FinBERT `positive` floor
FMB_INDECISIVE_MAX     = 0.14    # FinMarBa `indecisive` ceiling (the only <= condition)
KW15_BODY_MIN          = 0.50    # ge15 BODY model_proba floor
KW15_HEAD_MIN          = 0.50    # ge15 HEADLINE model_proba floor
# FLANG-BERT headline sentiment_score (= positive − negative) floor. Was a STANDALONE
# leg at 0.97, calibrated 2026-07-28 on 04_PRstats_2026-05-04-2026-07-23_..._FromPRhigh
# .tsv (3434 rows): 304 fires (8.9%) at 46.1% precision / 1.81x lift, of which 191
# (5.6% of all rows) were fires the FinBERT+FinMarBa leg missed at the same precision —
# that marginal number is what justified OR-ing it in. Both facts are MOOT from
# 2026-08-01: it is now one AND-ed condition among five, so its standalone precision no
# longer describes anything the gate does. The BODY twin was measured and REJECTED
# (precision flat 0.90 -> 0.98, 33-35%, ~1.3x lift — saturated ⇒ useless as a gate);
# headline only. Retune HERE.
FLANG_SCORE_MIN        = 0.96
KW_SCORE_WAIT_SEC      = 10      # max wait on the kw-score future before STEP 1b
                                 # (~4 ms typical — a hung scorer must never
                                 # delay the trade decision beyond this)
FLANG_SCORE_WAIT_SEC   = 10      # same ceiling for the FLANG future (~6.6 ms measured)

# ── EARNINGS LEG (2026-08-02) — a SECOND trade branch, OR-ed with the five-way AND ──
# ⚠ This partially undoes the 2026-08-01 "pure AND" switch. The five conditions above
# are UNTOUCHED and still form one complete branch; what changed is that a row can now
# also fire on:
#       FineClass == 'Earnings_Results' AND EarningScore >= 4 AND kw15_body >= 0.50
# so the gate is `core_and OR earnings_leg`. Note kw15_body is SHARED with the AND
# branch (same condition, same local); kw15_head, FinBERT, FMB and FLANG are NOT
# required by this leg.
#
# Why a separate branch instead of more AND conditions: the five-way AND is
# structurally BLIND to earnings PRs. Earnings_text_structures_report.md §4 measured
# the gate's own FinBERT headline score at test AUC 0.297 on earnings PRs — ANTI-
# predictive — because winners and duds share near-identical headlines ("X Reports Q1
# 2026 Financial Results"); the separating numbers are in the BODY.
#
# Calibrated 2026-08-02 on tools/Earning_Scorer/PRstats_2026-05-04-2026-07-30_
# EarningResults_earnScored.tsv (518 labelled Earnings_Results rows, base rate 7.1%
# on the >=30% target), with kw15_body recomputed by the frozen ge15 body pipeline:
#     current five-way AND on those rows :  3 fires,  0.0% precision  (blind, as above)
#     EarningScore>=4 AND kw15_body>=0.50: 36 fires, 47.2% precision, 6.6x lift
#     test block only (>=2026-06-20, n=79, base 13.9%): 7 fires, 42.9%, 3.1x lift
# OVERLAP WITH THE AND BRANCH IS EXACTLY ZERO — this leg is purely additive: ~0.71
# extra trades/day at the same precision bar the AND branch holds (46.2%).
# Cut choice: precision is FLAT below 4 (>=1 .321, >=2 .314, >=3 .339) and steps up at
# 4 (.472) — 4 is the knee, and matches the report's own defensible operating point.
# ⚠ n=36 ⇒ wide error bars, and EarningScore is a RANKING CUT, not a calibrated
# probability: the >=30% base rate itself moved 3.7% (May) -> ~15-20% (Jun/Jul).
# ⚠ EarningScore is INTEGER POINTS (observed −7…+9, median 0), NOT a 0-1 score — a
# threshold like 0.4 would silently mean ">= 1" (36.5% of earnings PRs at 32%
# precision). Retune HERE; the params behind the points are frozen in
# tools/Earning_Scorer/earn_score_params.json (calibrated 2026-08-01).
EARN_CLASS          = 'Earnings_Results'  # the only FineClass this leg accepts
EARN_SCORE_MIN      = 4                   # EarningScore POINTS floor (integer, see above)
EARN_SCORE_WAIT_SEC = 10                  # ceiling on the earn future, as KW/FLANG

# ── SOLO LEGS (2026-08-03) — two MORE standalone branches, OR-ed beside the two above ──
#   is_ok = core_and OR earnings_leg OR kw15_body >= KW15_BODY_SOLO_MIN
#                                    OR flang    >= FLANG_SOLO_MIN
# Each is ONE condition and fires the trade ALONE — no confirmation from any other model.
# They re-read scores the core branch already consumes, at HIGHER cuts, which has a
# structural consequence worth stating plainly: a row clearing a solo cut fires no matter
# what the other four models said, so the five-way AND and the earnings leg are only
# DECISIVE for rows with kw15_body in [KW15_BODY_MIN, KW15_BODY_SOLO_MIN) — i.e. [0.50,
# 0.70) — and, for FLANG, below 0.975. Nothing was removed or retuned: every row that
# fired before still fires, this is purely additive.
# ⚠ UNLIKE EVERY OTHER CUT IN THIS FILE, these two were specified by the operator on
# 2026-08-03 and were NOT calibrated against a labelled corpus. No fires/precision/lift
# number backs them. For scale, from the calibrations recorded above: FLANG standalone at
# 0.97 fired 8.9% of 3434 rows at 46.1% precision (>=15%) / 24.3% (>=30%), and the ge15
# body channel alone ran ~41% precision at 0.60 — so expect the gate's fire rate to move
# off the AND-era ~2.5% (~1 trade/day) into roughly the 5-10% band, at those standalone
# precisions rather than the AND branch's 46.2% @>=30%. Retune HERE.
KW15_BODY_SOLO_MIN = 0.70    # ge15 BODY model_proba — fires alone (core cut is 0.50)
FLANG_SOLO_MIN     = 0.975   # FLANG sentiment_score — fires alone (core cut is 0.96)

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


def analyze_flang(news_dict: dict) -> dict:
    """FLANG-BERT (avinasht/FLANG-BERT_flang-bert) headline sentiment. Runs once
    per article, in parallel with the other two headline models. Its
    `sentiment_score` (positive − negative) must clear FLANG_SCORE_MIN for the trade
    to fire — one of the five AND-ed gate conditions since 2026-08-01 (it was a
    STANDALONE leg that fired on its own until then). The full distribution is stored
    in the TSV FLANG_* columns for offline re-analysis.

    Scores EXACTLY news_dict['Headline'], the same string the other two headline
    models get, so the live value equals an offline HeadScore11encoders rescore of
    the TSV's Headline column (where the threshold was calibrated)."""
    result = score_flang_headline(news_dict['Headline'])
    logger.info(
        f"[FLANG] {news_dict['Symbol']} → {result['label'].upper()} "
        f"(score={result['sentiment_score']:.4f}) in {result['inference_ms']:.1f}ms"
    )
    return {
        'positive':        result['positive'],
        'neutral':         result['neutral'],
        'negative':        result['negative'],
        'sentiment_score': result['sentiment_score'],
        'label':           result['label'],
        # NOTE: this module names its timing key `inference_ms`, unlike FinMarBa's
        # `elapsed_ms` — normalised here so the TSV writer stays uniform.
        'inference_ms':    result['inference_ms'],
    }


_KW_NONE_RESULT = {'kw15_body_proba': None, 'kw15_head_proba': None,
                   'KwScoreCompletedAt': None}


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
                    f"kw15_body={body_p:.6f} kw15_head={head_p:.6f} "
                    f"in {elapsed_ms:.1f}ms")
        return {
            'kw15_body_proba': body_p,
            'kw15_head_proba': head_p,
            'KwScoreCompletedAt': c3.format_millis_time(c3.now_fixed()),
        }
    except Exception as exc:
        logger.error(f"[KwScore] id={news_id}: scorer error: {exc}", exc_info=True)
        return dict(_KW_NONE_RESULT)


_EARN_NONE_RESULT = {'FineClass': None, 'EarningScore': None, 'EarnBreakdown': None}


def _classify_and_score_earnings(news_dict: dict, primary_symbol: str) -> dict:
    """Catalyst class for every PR + the earnings rule score for the ones that are
    Earnings_Results. Runs once per article on the earnscore worker. Never raises:
    any error returns the all-None dict, which simply makes the earnings leg false
    (the five-way AND branch is unaffected — this leg is OR-ed beside it, so a dead
    earnings scorer costs the new trades but never blocks the old ones).

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
    try:
        body_cell = c3.clean_inline_text(news_dict.get('body_raw') or '')
        headline  = news_dict['Headline']
        t0 = time.monotonic()

        # Headline first, body LEAD as fallback — reproduces PrClassifier.classify_row
        # without a shim dict (news_dict's body key is `body_raw`, not Body/body).
        # The lead-only window matters: about-us / safe-harbor boilerplate deeper in
        # the text is full of false triggers.
        fine, _broad = classify_pr(headline)
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
        return dict(_EARN_NONE_RESULT)


# ─── Trade-trigger gate ───────────────────────────────────────────────────────

def _fmt_thr(v: float) -> str:
    """A gate threshold for the Trigger cell: 2 dp when that round-trips exactly
    (true for the five core cuts → 0.80 / 0.14 / 0.50 / 0.50 / 0.96, and for the
    0.70 solo cut), otherwise %g. So FLANG_SOLO_MIN renders as 0.975 rather than a
    misleadingly-rounded 0.97, and a retune to 0.625 renders as 0.625."""
    s = f"{v:.2f}"
    return s if float(s) == v else f"{v:g}"


def _cond_token(name: str, value, threshold, ok: bool, is_floor: bool,
                val_fmt: str = '.4f', thr_fmt: str | None = None) -> str:
    """One `name:VERDICT(score op threshold)` token of the Trigger cell.

    `ok` is passed IN rather than recomputed here — that is what makes it
    impossible for the TSV cell to disagree with the gate that actually fired.
    The printed operator is always the relation that is TRUE of the row: `>=`/`<=`
    on a pass, its negation `<`/`>` on a fail. A None score (model error / future
    timeout) renders as a bare MISSING, so a dead scorer never looks like a model
    that merely scored low. Scores are display-rounded to 4 dp — the PASS/FAIL
    token, not the number, is authoritative.

    `val_fmt`/`thr_fmt` exist for the earnings condition alone, whose score is
    integer POINTS, not a probability: it renders `earn:PASS(5>=4)` instead of a
    misleading `earn:PASS(5.0000>=4.00)`. Everything else keeps the 4 dp / _fmt_thr
    defaults."""
    if value is None:
        return f"{name}:MISSING"
    op = ('>=' if ok else '<') if is_floor else ('<=' if ok else '>')
    thr = format(threshold, thr_fmt) if thr_fmt else _fmt_thr(threshold)
    return f"{name}:{'PASS' if ok else 'FAIL'}({format(value, val_fmt)}{op}{thr})"


def _evaluate_trigger(finbert_val: dict, finmarba_val: dict, flang_val: dict,
                      kw_val: dict, earn_val: dict) -> tuple:
    """STEP-1b gate. Since 2026-08-03 it is an OR of FOUR branches:

      CORE branch — the 2026-08-01 five-way AND, every condition required, unchanged:
        FinBERT    positive        >= SENTIMENT_POSITIVE_MIN
        FinMarBa   indecisive      <= FMB_INDECISIVE_MAX   (the only ceiling)
        ge15 body  model_proba     >= KW15_BODY_MIN
        ge15 head  model_proba     >= KW15_HEAD_MIN
        FLANG-BERT sentiment_score >= FLANG_SCORE_MIN

      EARNINGS branch (2026-08-02) — three conditions, also all required:
        PrClassifier FineClass     == EARN_CLASS ('Earnings_Results')
        EarningScore (POINTS, int) >= EARN_SCORE_MIN
        ge15 body  model_proba     >= KW15_BODY_MIN   ← the SAME c_body local

      KwBodySolo branch (2026-08-03) — ONE condition, fires unaided:
        ge15 body  model_proba     >= KW15_BODY_SOLO_MIN

      FLANGSolo branch (2026-08-03) — ONE condition, fires unaided:
        FLANG-BERT sentiment_score >= FLANG_SOLO_MIN

        is_ok = core_and OR earnings_leg OR kwbody_solo_leg OR flang_solo_leg

    Returns (is_ok, label, detail, missing_blocked).

    The earnings branch exists because the core AND is structurally blind to earnings
    PRs — its FinBERT headline score measured test AUC 0.297 there (anti-predictive):
    winners and duds share the same headline, the numbers are in the body. On 518
    labelled Earnings_Results rows the core AND fired 3x at 0% precision while this
    leg fires 36x at 47.2%, with ZERO overlap. See the EARNINGS LEG comment block at
    the top of this file for the full calibration.

    The two SOLO branches re-read scores the core branch already uses, at higher cuts,
    so they SUBSUME it wherever they hold: the five-way AND and the earnings leg are
    only decisive for rows with kw15_body in [KW15_BODY_MIN, KW15_BODY_SOLO_MIN).
    ⚠ Their cuts are operator-specified (2026-08-03) and, alone among the thresholds in
    this file, are NOT backed by a corpus calibration — see the SOLO LEGS comment block.

    `label` joins the fired leg names with '_' in the fixed order ('Keyword',
    'FinBERT_FMB', 'FLANG', 'Earnings', 'KwBodySolo', 'FLANGSolo'), gated on `is_ok`,
    NOT on a non-empty `fired` — a row can have one leg true and still not fire (e.g.
    Keyword alone), and labelling it 'Keyword' would contradict the gate. THAT GUARD IS
    STILL LOAD-BEARING even though the solo legs made most single-leg cases firing ones.
    The label names exactly which legs held, so it is fully informative again; the
    vocabulary is now much wider than the AND-era 'Keyword_FinBERT_FMB_FLANG'/'BAD'
    pair, e.g. 'KwBodySolo', 'FLANG_FLANGSolo', 'Keyword_KwBodySolo', 'Earnings',
    'Keyword_FinBERT_FMB_FLANG_KwBodySolo_FLANGSolo'. Two implications constrain it:
    KW15_BODY_SOLO_MIN > KW15_BODY_MIN, so 'KwBodySolo' always implies c_body (hence
    'Keyword' too whenever kw15_head passes); and FLANG_SOLO_MIN > FLANG_SCORE_MIN, so
    'FLANGSolo' can NEVER appear without 'FLANG'.

    `missing_blocked` is the dead-model alarm: True when this row did NOT fire and the
    only thing standing between it and a fire was a None score — every evaluable CORE
    condition passed but a model/future returned nothing, OR a scored-eligible earnings
    PR (class + kw15_body OK) has a None EarningScore, OR — since 2026-08-03 — kw15_body
    or FLANG is None at all, because a solo branch has exactly ONE condition and a
    missing score is therefore the whole of what blocked it. The caller logs it at
    WARNING. That third term makes the alarm markedly louder by design: with four
    branches a dead model no longer halts trading, so it is even less visible in the
    fire count, while each of those two scores now backs a branch that trades unaided.

    `detail` is the TSV `Trigger` cell (added 2026-07-29): the YES/NO verdict plus a
    PASS/FAIL/MISSING token per CONDITION, so a row shows WHICH one failed and by how
    much. It grew by appending only — the five core tokens keep their position and
    order, `earn`/`class` were appended 2026-08-02 and the two solo tokens 2026-08-03 —
    so a parser written for the 5- or 7-token format still works. Nine tokens now:
        YES | FinBERT:PASS(0.8231>=0.80) FMB:PASS(0.0512<=0.14) \
kw15_body:PASS(0.7300>=0.50) kw15_head:FAIL(0.4412<0.50) FLANG:FAIL(0.6210<0.96) \
earn:NA class:Product_Update kw15_body_solo:PASS(0.7300>=0.70) \
FLANG_solo:FAIL(0.6210<0.975)
    The two solo tokens deliberately REPRINT the kw15_body and FLANG scores against
    their own cuts, so the row records both thresholds it was judged against and stays
    self-describing across a retune of either.
    `earn` is the one token in POINTS, not a probability, and the one with a fourth
    verdict: `earn:NA` on a row whose class is known and simply isn't Earnings_Results
    (deliberately not scored — the common case), leaving `earn:MISSING` to mean what
    MISSING means everywhere else, a score that should exist and doesn't.
    All outputs come from the SAME per-condition booleans below, so the cell can never
    disagree with the gate.

    ⚠ The gate was an OR-of-three-legs through 2026-07-31, a pure AND on 2026-08-01, a
    two-branch OR from 2026-08-02 and this four-branch OR from 2026-08-03; 'Keyword'
    means a DIFFERENT rule before/after 2026-07-28, and the FLANG labels only exist from
    2026-07-29 — see the gate comment blocks near the top of this file before comparing
    historical TSVs. The TSV header does NOT distinguish the 08-02 and 08-03 builds
    (still 42 cols); the Trigger cell's token count (7 vs 9) does."""
    positive   = finbert_val.get('positive')
    indecisive = finmarba_val.get('indecisive')
    kw_body    = kw_val.get('kw15_body_proba')
    kw_head    = kw_val.get('kw15_head_proba')
    flang      = flang_val.get('sentiment_score')
    fine_class = earn_val.get('FineClass')
    earn_score = earn_val.get('EarningScore')
    # Per-CONDITION verdicts, computed once and used for the gate, the legs AND the
    # Trigger cell — so none of the three can drift from the others.
    c_fin   = positive   is not None and positive   >= SENTIMENT_POSITIVE_MIN
    c_fmb   = indecisive is not None and indecisive <= FMB_INDECISIVE_MAX
    c_body  = kw_body    is not None and kw_body    >= KW15_BODY_MIN
    c_head  = kw_head    is not None and kw_head    >= KW15_HEAD_MIN
    c_fla   = flang      is not None and flang      >= FLANG_SCORE_MIN
    c_class = fine_class == EARN_CLASS
    c_earn  = earn_score is not None and earn_score >= EARN_SCORE_MIN
    # 2026-08-03 solo cuts — the SAME two scores as c_body/c_fla, read at higher floors.
    c_body_solo = kw_body is not None and kw_body >= KW15_BODY_SOLO_MIN
    c_fla_solo  = flang   is not None and flang   >= FLANG_SOLO_MIN
    headline_leg = c_fin  and c_fmb
    keyword_leg  = c_body and c_head
    flang_leg    = c_fla
    # NOTE c_body is SHARED with keyword_leg — one condition, one local, two branches.
    earnings_leg = c_class and c_earn and c_body
    # The two 2026-08-03 solo branches: one condition each, each fires on its own.
    kwbody_solo_leg = c_body_solo
    flang_solo_leg  = c_fla_solo
    # Appended, never reordered — every pre-existing label keeps its prefix and order.
    fired = [name for name, ok in (('Keyword',     keyword_leg),
                                   ('FinBERT_FMB', headline_leg),
                                   ('FLANG',       flang_leg),
                                   ('Earnings',    earnings_leg),
                                   ('KwBodySolo',  kwbody_solo_leg),
                                   ('FLANGSolo',   flang_solo_leg)) if ok]
    # Core AND (2026-08-01). Equivalent to `headline_leg and keyword_leg and flang_leg`
    # — each leg is itself an AND — but spelled over the five conditions because that
    # is the rule as calibrated.
    core_and = c_fin and c_fmb and c_body and c_head and c_fla
    # The OR over all four branches — 2026-08-02 added earnings, 2026-08-03 the two solo
    # legs. Every branch is added BESIDE the core AND, never inside it, so anything that
    # fired under an earlier build still fires under this one.
    is_ok = core_and or earnings_leg or kwbody_solo_leg or flang_solo_leg
    # A None score is the ONLY thing blocking this row: every condition that could be
    # evaluated passed, but at least one model/future gave nothing back. One term per
    # branch; `not is_ok` because a row another branch already traded has nothing blocked.
    _vals  = (positive, indecisive, kw_body, kw_head, flang)
    _conds = (c_fin, c_fmb, c_body, c_head, c_fla)
    core_missing = (any(v is None for v in _vals)
                    and all(c for c, v in zip(_conds, _vals) if v is not None))
    # Earnings twin: a known-Earnings_Results row whose kw15_body passed but whose
    # EarningScore came back None — the earnings scorer is dead, not the news quiet.
    earn_missing = c_class and c_body and earn_score is None
    # Solo twin. A solo branch is a SINGLE condition, so "every evaluable condition
    # passed" degenerates to "the one score is missing" — which makes this term much
    # louder than the other two: a dead kw or FLANG scorer now WARNs on EVERY row rather
    # than only on rows where four other models happened to agree. That volume IS the
    # signal; each of those scores now backs a branch that can trade unaided, so losing
    # one silently removes a whole branch while the tape still looks alive.
    solo_missing = (kw_body is None) or (flang is None)
    missing_blocked = (not is_ok) and (core_missing or earn_missing or solo_missing)
    # is_floor=False for FMB alone — `indecisive` is a CEILING, the only <= condition.
    detail = ' '.join(
        _cond_token(name, value, threshold, ok, is_floor)
        for name, value, threshold, ok, is_floor in (
            ('FinBERT',   positive,   SENTIMENT_POSITIVE_MIN, c_fin,  True),
            ('FMB',       indecisive, FMB_INDECISIVE_MAX,     c_fmb,  False),
            ('kw15_body', kw_body,    KW15_BODY_MIN,          c_body, True),
            ('kw15_head', kw_head,    KW15_HEAD_MIN,          c_head, True),
            ('FLANG',     flang,      FLANG_SCORE_MIN,        c_fla,  True)))
    # `earn` prints as integer POINTS ('5>=4'), hence the format overrides. NA rather
    # than MISSING when the class is known and simply isn't Earnings_Results: the
    # scorer was deliberately skipped, not broken. That distinction is the whole point
    # of the token — ~85% of rows are non-earnings, and MISSING on all of them would
    # drown out the one row where the scorer actually died. MISSING is therefore left
    # to mean exactly what it means everywhere else: the score should exist and does
    # not (an Earnings_Results row that failed to score, or a dead worker whose
    # FineClass is None too).
    if earn_score is None and fine_class is not None and not c_class:
        detail += ' earn:NA'
    else:
        detail += ' ' + _cond_token('earn', earn_score, EARN_SCORE_MIN, c_earn, True,
                                    val_fmt='d', thr_fmt='d')
    # The class is categorical, not a threshold comparison, so it gets a plain token
    # rather than a PASS/FAIL one — it doubles as the FineClass record in the log line.
    detail += f" class:{fine_class if fine_class else 'MISSING'}"
    # The two 2026-08-03 solo cuts, APPENDED for the same reason `earn`/`class` were: the
    # seven tokens before them keep their position, so a 5- or 7-token parser still works.
    # They print the SAME two scores as the kw15_body / FLANG tokens above against a
    # DIFFERENT threshold, which is the point — the row records both cuts it was judged
    # against, and stays self-describing when either is retuned. Plain probabilities and
    # floors, so no format overrides; _fmt_thr renders 0.975 as 0.975, not 0.97.
    detail += ' ' + _cond_token('kw15_body_solo', kw_body, KW15_BODY_SOLO_MIN,
                                c_body_solo, True)
    detail += ' ' + _cond_token('FLANG_solo', flang, FLANG_SOLO_MIN, c_fla_solo, True)
    # `fired` alone is NOT the condition here: a row can still have a leg true and not
    # fire, and labelling it after that leg would contradict the gate. The 2026-08-03
    # solo branches NARROWED this window but did not close it — e.g. kw15_body 0.55
    # (Keyword true with kw15_head, below the 0.70 solo cut), FLANG under 0.96, not an
    # earnings PR: `fired` reads ['Keyword'] and the gate says BAD.
    label = '_'.join(fired) if is_ok else 'BAD'
    return is_ok, label, f"{'YES' if is_ok else 'NO'} | {detail}", missing_blocked


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
    # FLANG-BERT block (2026-07-29). Only FLANG_sentiment_score feeds the gate; the
    # rest are for offline re-analysis. These map 1:1 onto the offline FLANG-BERT_*
    # columns emitted by tools/Encoder_Models/HeadScore11encoders.py.
    # FLANG_time = completion timestamp (FMB_time convention); FLANG_ms = the
    # model's own inference_ms duration.
    'FLANG_time',
    'FLANG_positive', 'FLANG_neutral', 'FLANG_negative', 'FLANG_sentiment_score',
    'FLANG_label', 'FLANG_ms',
    'ClerkSentiment',
    'kw15_body_proba', 'kw15_head_proba', 'KwScoreCompletedAt',
    # Trigger (2026-07-29) — the gate's own account of itself: YES/NO plus a
    # PASS/FAIL/MISSING token per condition with the score and the threshold it was
    # compared against. Built by _evaluate_trigger from the same booleans the gate
    # fires on. NOT a duplicate of ClerkSentiment: that one records the fired LEGS
    # and is overwritten with 'skipped:*' when the ticker was never armed, whereas
    # Trigger is populated on EVERY row (the gate runs before the arm branch), and
    # it resolves a leg into its underlying models. Appended LAST on purpose —
    # every pre-existing column keeps its index. Its TOKEN COUNT is also the only
    # thing that distinguishes the 08-02 build (7) from the 08-03 one (9), since the
    # solo branches added no columns and so leave this header identical.
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

    # 3) Trade gate (core five-way AND OR earnings leg) → decision + label + Trigger cell
    is_ok, trigger_label, trigger_detail, missing_blocked = _evaluate_trigger(
        finbert_val, finmarba_val, flang_val, kw_val, earn_val)
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
        'FLANG_time':             flang_completed_at,
        'FLANG_positive':         flang_val.get('positive'),
        'FLANG_neutral':          flang_val.get('neutral'),
        'FLANG_negative':         flang_val.get('negative'),
        'FLANG_sentiment_score':  flang_val.get('sentiment_score'),
        'FLANG_label':            flang_val.get('label'),
        'FLANG_ms':               flang_val.get('inference_ms'),
        'kw15_body_proba': kw_val.get('kw15_body_proba'),
        'kw15_head_proba': kw_val.get('kw15_head_proba'),
        'KwScoreCompletedAt':          kw_val.get('KwScoreCompletedAt'),
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
    print(f"  FLANG_sentiment_score        : {completed_dict['FLANG_sentiment_score']}")
    print(f"  FLANG_positive               : {completed_dict['FLANG_positive']}")
    print(f"  FLANG_negative               : {completed_dict['FLANG_negative']}")
    print(f"  FLANG_neutral                : {completed_dict['FLANG_neutral']}")
    print(f"  FLANG_label                  : {completed_dict['FLANG_label']}")
    print(f"  FLANG_ms                     : {completed_dict['FLANG_ms']}")
    print(f"  FLANG_time                   : {completed_dict['FLANG_time']}")
    print(f"  kw15_body_proba             : {completed_dict['kw15_body_proba']}")
    print(f"  kw15_head_proba             : {completed_dict['kw15_head_proba']}")
    print(f"  KwScoreCompletedAt           : {completed_dict['KwScoreCompletedAt']}")
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
    logger.info("ge15 keyword pipelines ready (smoke: body=%.6f head=%.6f; "
                "core branch needs body>=%s AND head>=%s, and body>=%s fires alone).",
                _smoke_body, _smoke_head, _fmt_thr(KW15_BODY_MIN),
                _fmt_thr(KW15_HEAD_MIN), _fmt_thr(KW15_BODY_SOLO_MIN))

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
    _smoke_class, _ = classify_pr(_smoke_hl)
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
    logger.info("PR classifier + earnings scorer ready (smoke: class=%s score=%d [%s]; "
                "leg needs FineClass==%s AND EarningScore>=%d AND kw15_body>=%s).",
                _smoke_class, _smoke_score, _smoke_brk, EARN_CLASS, EARN_SCORE_MIN,
                _fmt_thr(KW15_BODY_MIN))

    # Echo the WHOLE gate once, every branch with the thresholds actually in force. The
    # constants are module-level and read at import, so a process that was started before
    # a retune keeps the OLD gate until it is restarted — and nothing else in the log
    # says which one is live (the TSV header is identical across the 08-02 and 08-03
    # builds). This line is the cheapest way to answer "which gate is this process
    # running?" from logs/ alone.
    logger.info("[Gate] ACTIVE — OR of 4 branches: "
                "(1) CORE FinBERT>=%s AND FMB<=%s AND kw15_body>=%s AND kw15_head>=%s "
                "AND FLANG>=%s | (2) EARNINGS class==%s AND earn>=%d AND kw15_body>=%s | "
                "(3) KwBodySolo kw15_body>=%s | (4) FLANGSolo FLANG>=%s",
                _fmt_thr(SENTIMENT_POSITIVE_MIN), _fmt_thr(FMB_INDECISIVE_MAX),
                _fmt_thr(KW15_BODY_MIN), _fmt_thr(KW15_HEAD_MIN),
                _fmt_thr(FLANG_SCORE_MIN), EARN_CLASS, EARN_SCORE_MIN,
                _fmt_thr(KW15_BODY_MIN), _fmt_thr(KW15_BODY_SOLO_MIN),
                _fmt_thr(FLANG_SOLO_MIN))

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
    main()
