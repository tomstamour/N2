#!/usr/bin/env python3
"""
trade-mole-2.5.py — IBKR Trade-Frequency Surge Detector (clerk-aware)
=====================================================================

New in v2.5 (2026-10-02) — THIRD pass, on the operator's labels >= 2026-07-09
grown to 192 files (62 pos / 90 neg / 27 neu / 13 late). Copy of 2.4. Report:
``mole-outputs/surge-predictor-analysis-2026-10-02.md``. Scored through the real
``should_fire()`` / ``_maybe_fire_pretrade()`` via
``analyze_trade_mole/eval_rules.py``, under the operator's 2026-10-02 trade-off:

    total = neg + 0.4 x neu + 3 x (pos missed + late missed)
    ("losing 1 good trigger is OK only if it removes at least 3 bad ones"),
    and a change that makes _late_ files fire earlier may add NO new _neg_ fire.

  2.4 on this corpus:  pos 54/62  neu 16/27  neg 24/90  late 12/13   total 57.4
  2.5 defaults:        pos 54/62  neu 16/27  neg 24/90  late 12/13   total 57.4
                       LESL fires +3.9 s instead of +26.2 s, 27.5% cheaper;
                       LGHL fires +2.5 s instead of +57.9 s, 4.6% cheaper.

THE ONE DECISION CHANGE — the VWAP direction gate is narrowed to the rules that
have no price-direction leg of their own (``SURGE_VWAP_GATE_RULES`` = C, D).
2.4 described the gate as "the last print vs the session's OWN VWAP, equal to
the price at the first trade". That is not what it reads: ``self._vwap`` is the
VWAP field of the RTVolume message, i.e. IBKR's WHOLE-DAY VWAP. On a name that
traded higher earlier in the day the gate therefore holds every rule shut until
the news move climbs back to that old average — exactly the operator's "late"
complaint. LESL 2026-09-30 sat 30% under its day VWAP at the first print and
fired only at +26 s, after a +40% run; LGHL sat 6.7% under and fired at +58 s.
Rules E and N already demand a price RISE since the session started, so for
them the day-VWAP test is a second, staler direction test; C and D have no
direction leg at all, and keep the gate. The narrowing is what keeps the one
negative the gate earns its keep on (PHGE 2026-07-24, a D fire 14.9% under its
day VWAP): dropping the gate outright, or reading this session's own VWAP
(measured — inert, it never sits 1% under the price at a fire), re-admits it.
Unlabelled control (264 replayable recordings): +2 fires — IMCC 2026-09-23
(+23% max in the 134 s that follow) and SLE 2026-08-14 (fired live in August;
x-wing stopped out at -4.6%).

RECORD-ONLY additions (no rule reads them; for the next calibration):
  * POST-TRIGGER TAIL (``POST_TRIGGER_RECORD_SEC`` = 60, operator request
    2026-10-03). Recording no longer stops at the buy-signal: for 60 s, or until
    the clerk tears the session down, every tick is still recorded — raw, with
    ``post_trigger=True``, no features computed — and the clerk (1.2) rewrites
    the CSV when the tail ends, with one ``POST_TRIGGER_END`` row saying how.
    This is what lets the next pass see a stricter rule fire LATER, and
    calibrate a confirmation wait. replay_mole.py drives tail rows through the
    real handlers, so features are rebuilt offline. ⚠ The tail includes our own
    x-wing prints. 0 / None restores 2.4.
  * ``true_day_volume`` / ``true_vol_5s`` / ``true_vol_x_normal_5s`` — IBKR's
    running day volume (tick 8 and the totalVolume field of every tick-48
    RTVolume message, including the zero-size volume-only messages the print
    path drops). It counts the sub-100-share prints and the volume IBKR
    aggregates into one message, which the recorded prints miss: LRHC
    2026-10-01 printed 14 lots at +2.0 s while the day volume had moved 94.
    ``true_vol_x_normal_5s`` divides by the name's normal flow, with the
    baseline trade size converted from SHARES (see below) to lots.
  * ``tws_trade_count`` is now filled. IBKR delivers tick 54 (day trade count)
    through tickGeneric, and only tickSize handled it, so the column was empty
    in every recording to date. ⚠ ``_reconcile_trade_count`` is still NOT called
    from the new path: wiring it would change the trade log and every decision.
    It has never run in production.

MEASURED AND NOT ADOPTED (numbers in the report):
  * Any veto at the trigger instant. Cross-validated on held-out files, a
    random forest / logistic model over every recorded, hidden-volume, news and
    stock-fact feature separates the trade-path fires no better than chance
    (AUC 0.43-0.53; pre-trade 0.30-0.61 on 27 files). Single-feature cuts that
    score are one-file plateaus.
  * A confirmation wait (operator allowed <= 1 s): not measurable — 102 of the
    106 files that fire have < 1 s recorded after it (recordings stop at the
    live fire; 100 end at that very instant).
  * A "first burst" rule on true volume: good and bad files share the
    signature at the first print (YCBD, CVKD, REBN, MIMI all > 7,000x normal).
  * DEDUP_EXCLUDE_TWIN_FROM_COUNTS / RATE_NORMALIZE_BY_SESSION_AGE: total
    57.4 -> 110.4 / 61.4. Both stay off.
  * News / stock-fact session vetoes (kw15, FM3, market cap, float): none lowers
    the total; FM3 helps by 1.8 on its one week of coverage only.
  * Single-file timing tweaks (E price 1.2% -> 1.15% for IMCC, pre-trade gap cap
    1.25 -> 1.3 s for CUPR, E flow 10 -> 5): each moves one file and sits at the
    edge of its plateau.

⚠ Units, cosmetic: ``hist_baseline_trade_size`` is in SHARES
(universe_finder/tradeSizeExplained.txt, lot_multiplier = 1) while every live
size is in LOTS, so ``flow_x_baseline_5s`` (2.4) is 100x understated — "10x
normal" is really ~1000x (half that again with the RTVolume/RTTradeVolume twin).
The thresholds were fit on the same scale, so decisions are unaffected; the 2.4
comment calling both sides lot-denominated is wrong.

New in v2.4 (2026-09-18) — SECOND precision pass, on a corpus grown from 90 to
132 labelled files (>= 2026-07-09). Copy of 2.3. Scored end-to-end through the
real ``should_fire()`` / ``_maybe_fire_pretrade()`` via
``analyze_trade_mole/eval_rules.py``; report in
``mole-outputs/surge-predictor-analysis-2026-09-18.md``.

  2.3 on the 132-file corpus:  pos 35/39  neu 14/23  neg 25/70   cost 30.6
  2.4 defaults:                pos 33/39  neu 12/23  neg 11/70   cost 15.8
  (cost = neg + 0.4 x neu, the operator's stated weighting; budget was
   "lose at most 2 positives".)

  Median time-to-fire on positives is 1.05s -> 1.00s with the shipped defaults
  (-> 0.52s if branch NF is switched on; see change 6). Fires later than 3s
  12 -> 10. No positive that still fires is materially delayed except NNVC
  (13.77s -> 18.52s, which is E's relative-flow leg taking two more prints to
  clear).

  On the 206 replayable UNLABELLED recordings >= 2026-07-09 — the sessions the
  operator never judged, kept as a check that nothing was quietly loosened —
  2.4 fires 59 times against 2.3's 78, and with the shipped defaults it adds
  ZERO fires that 2.3 did not already make.

⚠ THE OPERATOR'S CONSTRAINT WAS "NO ADDED DELAY". Every change below is a
same-tick test on state the mole already holds at the instant of the decision.
Nothing waits, nothing re-checks on a timer, no confirmation window was added.

THE ORGANISING FINDING: 2.3's rules mix two incompatible ideas of "surge".
Rules B/C and the pre-trade rule are RELATIVE — they ask whether the tape is
fast FOR THIS NAME. Rules D and E are ABSOLUTE — they ask whether the tape is
fast, full stop. On a universe where the historical inter-trade interval spans
0.4s to 34,200s, an absolute bar means something completely different at the
two ends, and that is where the false fires live. 2.4 gives the absolute rules
a relative leg and a direction leg, and leaves the relative rules alone.

THE SIX CHANGES, each with its own constant and kill switch, in descending
order of measured value (leave-one-out over the corpus, at the shipped
thresholds — see each constant's block for its own plateau):

  1. Rule E gains a RELATIVE-FLOW leg — ``SURGE_E_MIN_FLOW_X_BASELINE`` = 10.
     Worth 9.0 cost for 1 positive. E was the only rule with no baseline
     normalisation at all and it was the largest single source of false fires
     (9 of 25). At E's fire instants the historical ITI of its positives runs
     157s-34,200s while every one of its nine negatives sits below 93s: the
     absolute test "$3/s and +1.2%" is a regime change on a name that trades
     once every ten minutes and ordinary tape on a name that trades every
     minute. Two independent measurements agree (quoted spread separates the
     same way), which is why this is read as structure and not as a threshold.
  2. Rule D gains a SIZE leg — ``SURGE_D_MIN_VOLUME_5S`` = 20 lots.
     Worth 2.4 cost for 1 positive. D fired on three clustered prints of any
     size; this is the dust filter Rule A was given on 2026-07-07 and D never
     got. ⚠ Narrowest plateau in the set ([18, 21]) — see the constant.
  3. Rule C gains a MONEY gate — ``SURGE_C_MIN_CUM_DOLLAR`` = 80 lot-dollars.
     Worth 2.0 cost for 0 positives. Free.
  4. A global VWAP DIRECTION gate — ``SURGE_MIN_PX_VS_VWAP_PCT`` = -1.0%.
     Worth 1.0 cost for 0 positives, on a plateau 40x wide. A cluster of fast
     prints BELOW the session's own volume-weighted average is distribution,
     not accumulation.
  5. New branch N (``SURGE_N_MIN_PRICE_RISE_PCT`` = 4%) — the absolute-flow
     path survives, but only for a move four times larger than Rule E's.
     Buys back 1 positive (HIHO) at zero cost, which is what pays for change 2.
  6. New branch NF (``SURGE_NF_MIN_FLOW_X_BASELINE``) — extreme relative flow
     with no price confirmation. ⚠⚠ SHIPPED DISABLED. It is free on the
     labelled corpus and worth 0.48s of median latency, but the unlabelled set
     shows it fires on "one or two prints on a name that never trades, with no
     price move" — Rule B's shape, which 2.3 deliberately switched off. It buys
     no recall, so it is off. Full evidence in the constant's block.

WHAT WAS TRIED AND REJECTED (do not re-litigate without new data):
  * A second gate on the PRE-TRADE path. It is now the dominant remaining cost
    (6 of 11 negatives, 7 of 12 neutrals) and nothing survives scrutiny: the
    best-scoring cuts found by an exhaustive search over every recorded and
    derived pre-trade feature were (a) wall-clock timestamp cuts — pure corpus
    artifacts — and (b) an ask-vs-close bar at exactly 7.36%, which is the
    value of HCTI, a positive, so the plateau is one data point wide. Before
    the first print the snapshot carries staleness and a gap, and that is all
    it carries. 2.3 already extracted the gap.
  * Gap-vs-previous-close on the TRADE path (2.3's pre-trade gate, generalised).
    Confirmed again on the larger corpus: it does not separate, and for Rule D
    it INVERTS.
  * A baseline-relative leg for Rule D. D's positives and negatives have
    overlapping relative flow (pos 0.20-1024, neg 0.36-58); the absolute size
    leg is what works there, which is coherent — D is the baseline-FREE rule.
  * Turning Rule C off. With the money gate it is down to 2 positives for 1
    negative; switching it off saves 1.0 cost but costs BOTH positives
    (pos 33 -> 31), which breaches the "lose at most 2" budget outright. Left
    on. ``RULE_C_ENABLED`` is there if the budget ever changes.

⚠ MEASUREMENT LIMIT, unchanged from 2.3 and worth restating: recordings STOP at
the live buy-signal. For a file that fired, "2.4 does not fire" is provable only
inside the recorded window — the tape after it does not exist. Suppression of a
fire is therefore "deferred past the recorded window" and the true rate can only
be worse, never better. Files that never fired carry their whole watch and their
numbers are unconditional.

New in v2.3 (2026-08-14) — PRECISION pass on the operator's own labels.
Copy of 2.2. Scored end-to-end through the real ``should_fire()`` /
``_maybe_fire_pretrade()`` on all 90 files the operator labelled
``_pos_/_neg_/_neu_/_neutral_/_medium_`` from 2026-07-09 on, via
``analyze_trade_mole/eval_rules.py``.

  2.2 on that corpus:   pos 28/28   neu 15/18   neg 23/44
  2.3 defaults:         see the CALIBRATION block below.

⚠ FIRST, THE MEASUREMENT BUG THIS PASS EXISTS TO CORRECT. Every precision
number published before 2026-08-14 — including 2.2's own "neg 23/30 -> 13"
above — describes the TRADE PATH ONLY. ``replay_mole.replay_file`` required
the column ``rt_source``, which only TRADE rows carry, and recovered the ITI
baseline only from a TRADE row. A session that fired on the PRE-TRADE path
stops recording before any trade prints, so its table has neither. Result:
every pre-trade-only recording was silently skipped by the whole sweep stack
— 26 of the 90 labelled files, including 8 positives and 10 negatives. The
pre-trade path was thereby never scored, and it turns out to be roughly HALF
of all false fires (7 neg + 6 neu of the 23+15 that fire under 2.2). Fixed in
replay_mole.py the same day; ``eval_rules.py`` is the scorer that sees them.

THE FOUR CHANGES, in descending order of measured value:

  1. Rule B (start-staleness) is OFF by default — ``RULE_B_ENABLED``.
     Marginal economics on the corpus: it buys ONE positive (SLND) and pays
     FIVE negatives (CVKD, SCWO, DAIO, HTOO, CRWS). It cannot be rescued by
     moving its threshold, and this is not a new finding — 2.2's own header
     records "CVKD 20777 sits above pos SLND 13657 — not separable". A
     threshold is a one-dimensional cut through a distribution where the
     classes are interleaved, so there is no value of it that keeps SLND and
     drops CVKD. What is new is the willingness to spend that positive: the
     operator's budget for this pass is up to 5 positives corpus-wide.

  2. The PRE-TRADE path gains a GAP-VS-PREVIOUS-CLOSE gate —
     ``PRETRADE_MIN_GAP_VS_CLOSE_PCT`` = 5.0.
     Before the first print the mole has no tape to measure, which is exactly
     why the pre-trade rule is a coin flip: ``bslnITI_over_pretrade_gap`` says
     only "this tape is stale", never "this tape is stale AND something
     happened". The snapshot does carry the missing direction, in two ticks
     the mole was throwing away: tick 4 (last) and tick 9 (previous close).
     Measured at the fire instant, gap = (last-close)/close:

         pre-trade positives   median +7.8%   [-7.7 .. +14.0]
         pre-trade negatives   median +0.9%   [-2.0 .. +12.7]

     A 5% cut keeps every pre-trade-ONLY positive (8/8) and drops 5 of 7
     firing negatives plus 3 of 6 neutrals. The 4 positives it drops from
     this path (RUBI, HAO, VRAX, ARTL) all live in FULL recordings that keep
     going, so their re-catch by the trade path is measured, not assumed.
     ⚠ Both ticks are REQUIRED: no tick 4 or no tick 9 ⇒ no fire. That alone
     drops BLNE, a negative that quoted an ask with no last price at all.
     (FFAI is the other no-tick-4 name and is likewise blocked, but it was
     already silent under 2.2, so it is not a saving.) Falling back to the ask
     as the numerator was measured and REJECTED — ask/close scores positives
     7.4-19.9 against negatives -1.9-12.8, destroying the separation.
     ⚠ The gate DEFERS, it does not disable: a name held below the bar can
     still fire if a later tick 4 lifts it through. GWH is exactly that case —
     under 2.2 it fired on the first ask; under 2.3 it is held, then fires when
     tick 4 lands at +6.43%. So it remains a false fire, and the gate's value
     is not that it blocks every negative but that it blocks the DIRECTIONLESS
     ones.
     ⚠ This gate is PRE-TRADE ONLY and must not be generalised to the trade
     path, where the same feature INVERTS: Rule B's negatives gap up MORE
     than its positives (+8.2% vs -0.3% median).

  3. Rule D (absolute ITI collapse) gains a session-age cap —
     ``SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC`` = 1.5.
     D is the worst trade-path rule (6 pos / 5 neu / 8 neg) and its classes
     are otherwise INDISTINGUISHABLE: at the fire instant pos and neg have
     the same median measured_ITI (0.25), the same trades_in_5s (3), the same
     trades_in_1s (3) and overlapping dollar_rate/spread/volume. The single
     feature that does separate is WHEN it fires: all 6 positives fire within
     0.50 s of session start, while three negatives (BNRG 3.0 s, IDAI 2.8 s,
     NUWE 47.3 s) and two neutrals (OMH 2.5 s, VTAK 15.7 s) fire late. The
     cap keeps D for its intended case — "we started recording into a tape
     that is ALREADY running" — and removes the late-session grind, which is
     a different phenomenon wearing the same numbers.
     ⚠ 1.5 s is MID-PLATEAU, not a tight fit: every value in [0.6, 2.0] scores
     identically, and the plateau's walls are the slowest positive (VSEE at
     0.501 s) and the earliest late false fire (OMH at 2.502 s).

  4. Rule A (quote-churn + dollar flow) is OFF by default —
     ``RULE_A_ENABLED``. It contributes ZERO unique positives to the corpus:
     removing it changes recall not at all (its one positive, IPW, is caught
     the same instant by another rule) and removes 2 neutrals. It is pure
     cost. Kept in the file, behind a flag, because it is the only rule that
     reads the quote stream rather than the trade stream.

NOT CHANGED, and why:
  * Rule C (last-3 ITI collapse) is the best rule in the set — 2 unique
    positives and its single negative (RGNT) is caught by other rules anyway,
    so leave-one-out cost is EXACTLY ZERO. Do not touch it.
  * Rule E (dollar-flow + price confirmation) is the second best: 4 unique
    positives for 2.8 weighted cost. Its price leg is the only direction
    condition on the trade path.
  * The 2.2 Rule-F veto (gap/ratio/no-print caps) stays exactly as calibrated;
    the new gate is ANDed with it, not a replacement. It is still doing work:
    it vetoes 5 negatives (LITS, HSCS, NIXX-07-30, PRFX, SBEV) that the gap
    gate would not all catch.

CENSORING — the bound on every number above. Recordings STOP at the fire, so
a rule that is STRICTER than the one that produced the file cannot be observed
firing LATER in that file. Positive losses therefore read PESSIMISTIC (a
dropped positive may well have fired a second later) and negative kills read
OPTIMISTIC. This asymmetry is why change 2 was preferred over simply raising
the pre-trade threshold: its dropped positives sit in recordings that CONTINUE.

Inherited from v2.2 (2026-07-30) — precision re-calibration, zero structural change.
Copy of 2.1; the delta is thresholds + one Rule-F veto, from the 07-09..07-30
labeled-window analysis (mole-outputs/surge-predictor-analysis-2026-07-30.md,
re-runnable via analyze_trade_mole/analyze_surge_predictors_v3.py — on that
window 2.1 fired on 23/30 neg):

  * BSLN_ITI_LAST3_THRESHOLD (Rule C)     100  -> 1550
  * SURGE_A_MIN_DOLLAR_RATE_5S (Rule A)   2.0  -> 60.0
  * SURGE_ITI_COLLAPSE_MAX_SEC (Rule D)   0.3  -> 0.28
  * SURGE_FLOW_MIN_PRICE_RISE_PCT (Rule E) 0.01 -> 0.012
  * Rule F veto — a pretrade fire is SUPPRESSED (and the session keeps
    watching on the trade path) unless all three hold: no trade printed yet,
    pretrade gap <= PRETRADE_MAX_GAP_SEC, ratio <= PRETRADE_MAX_RATIO.
    A suppressed fire writes one PRETRADE_VETO row and disables the pretrade
    path for the session. Kill-switch: --no-pretrade-veto.

Verified on the labeled window (OR-level replay): pos 16/16 trade-path +
5/5 pretrade kept, neg fires 23/30 -> 12 trade-path + 1 pretrade, median
pos time-to-fire unchanged (1.78s). B unchanged (CVKD 20777 sits above pos
SLND 13657 — not separable). ⚠ The F-veto rests on n=5 pretrade pos.

Refactor of trade_mole_4.1.py. Two run modes:

  * STANDALONE (``python trade-mole-2.2.py --symbol ... ``): opens its own
    ibapi connection, subscribes to market data, records the full dataset,
    and writes the CSV — exactly like v4.1.

  * DRIVEN (imported by clerk-1.1.py): the clerk injects ONE shared ibapi
    client and forwards the tick callbacks into an ``IBKRSurgeApp`` instance
    that never opens its own socket. The detector runs purely as a data
    processor. When the (swappable) trigger rule fires it:
        1. records the trigger row,
        2. STOPS recording (``stop_at_trigger``), and
        3. calls ``buy_signal_callback(last_ask)`` so x-wing can place orders.

The ITI_baseline machinery and ALL of its derived columns are preserved
intact — collecting that dataset is the strategic purpose of this script.
The current composite surge rule is TEMPORARY and isolated behind the single
``IBKRSurgeApp.should_fire()`` method so it can be replaced by an
ITI_baseline-based rule later without touching the rest of the pipeline.

Inherited from v4.1 — added metrics derived from the existing
tickString / tickPrice / tickSize callbacks (NO new IBKR subscriptions):

  M1. Per-trade aggressor classification (BUY / SELL / MID) against the
      prevailing NBBO snapshot at trade time.
  M2. Order-flow imbalance per rolling window:
        buy_count, sell_count, mid_count, buy_volume, sell_volume,
        signed_volume, signed_dollar, lift_offer_ratio.
  M3. Dollar-volume RATE per rolling window: dollar_rate_{w}s
        = dollar_vol_in_{w}s / w.   (Single strongest separator between
        the positive GXAI case and the negative SCNX case observed.)
  M4. NBBO drift / mid-price velocity over each window:
        bid_drift_{w}s_bp, mid_velocity_{w}s_bp_per_s.
  M5. Spread-compression metrics:
        spread_pct_at_{w}s_ago, spread_compression_{w}s.
  M6. Trade-size distribution per window:
        median_size_{w}s, mean_size_{w}s, max_size_{w}s.
  M7. Quote-update frequency per window:
        quote_updates_{w}s.
  M8. Trade-cluster burstiness:
        max_cluster_200ms_in_5s.

New in v2.1 — trade-SIZE baseline metrics (NO new IBKR subscriptions; all
derived inside the existing single trade-log scan, zero added latency):

  M9. Trade-size surge vs the historical per-trade-size baseline supplied by
      the orchestrator (RTH_tradeSize / ETH_TradeSize from the universe
      pipeline). That baseline is defined to match mean_size exactly, so it is
      directly comparable to the M6 columns. Per rolling window:
        size_ratio_{w}s          = mean_size_{w}s / baseline
        buy_size_ratio_{w}s      = avg BUY print size / baseline
        signed_size_ratio_{w}s   = signed avg print size / baseline
        large_trade_count_{w}s   = # prints >= large_trade_mult * baseline
        large_trade_volume_frac_{w}s = share of window volume from those prints
      Plus the cross-window size momentum:
        size_accel_{1,2,5}s_vs_10s = mean_size_{n}s / mean_size_10s
      and the baseline echo hist_baseline_trade_size. When no valid baseline is
      supplied (missing, <=0, or the 44444 pipeline sentinel) every M9 column
      is emitted as None so the schema stays stable.

The placeholder surge rule from v4 (which depended on a meaningless
--baseline-iti) is replaced with a composite rule keyed off the new
metrics, with CLI-tunable thresholds. The --baseline-iti argument is
retained for backward compatibility but is now only used to populate
the legacy hist_baseline_* columns and the legacy accel_*_vs_hist_baseline
ratios; it has no effect on whether surge_detected fires.

Primary signal sources (unchanged from v4)
------------------------------------------
1. reqMktData generic tick 233 (RTVolume) -> per-trade prints via tickString
2. reqMktData generic tick 375 (RTTradeVolume) -> same channel, odd-lot variant
3. reqMktData generic tick 54 (TRADE_COUNT) -> cumulative trade count;
       used to reconcile aggregated RTVolume bursts (single_trade_flag=False)
4. reqMktData regular tickPrice/tickSize  -> bid/ask snapshot at each trade
       AND quote-log feed for M4/M5/M7.

Usage
-----
    python trade-mole-2.2.py \\
        --symbol GXAI \\
        --clientID 7 \\
        --lifeTime 10:00 \\
        --output ./outputs/ \\
        --baseline-iti 30.0 \\
        --baseline-trade-size 150

Requires: ibapi, pandas. TWS or IB Gateway running with API enabled.
"""

import argparse
import logging
import os
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


# ============================================================================
# Configuration
# ============================================================================

# Centralized per-user config (scripts/config/n2_config_file.txt). trade-mole
# only opens its own IBKR connection in STANDALONE mode; the --host/--port
# argparse defaults below are sourced from this file (CLI flags still override).
# In DRIVEN mode (imported by clerk) the clerk owns the shared socket.
import importlib.util as _ilu
from pathlib import Path as _Path
_SCRIPTS_DIR = _Path(__file__).resolve().parent.parent
_cfg_spec = _ilu.spec_from_file_location(
    "n2_config", _SCRIPTS_DIR / "config" / "n2_config.py")
n2_config = _ilu.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(n2_config)
_CFG = n2_config.load_config()

# User-requested rolling window sizes in seconds
WINDOWS = [1, 2, 3, 4, 5, 10]

# Deque trim cutoff: only need to retain the longest user-facing window
DEQUE_HISTORY_SECONDS = max(WINDOWS)

# Composite-rule defaults (overridable via CLI). Calibrated against the
# GXAI / SCNX comparison documented in the planning notes.
DEFAULT_SURGE_DOLLAR_RATE = 300.0        # USD/s in the 5s window
DEFAULT_SURGE_MAX_SPREAD_PCT = 0.02      # 2% of price
DEFAULT_SURGE_MIN_LIFT_RATIO = 0.55      # ≥55% buyer-initiated in 5s
DEFAULT_SURGE_MIN_BID_DRIFT_BP = 0.0     # bid must be non-falling over 5s
DEFAULT_SURGE_MIN_TRADES_5S = 5          # absolute floor
DEFAULT_SURGE_MAX_PRICE_DROP_PCT = 0.005  # block if last price is >0.5% below session-start price

# Quote-churn surge rule (active in should_fire as of 2026-06-11). Calibrated on
# the 2026-06-08..11 labeled set; see mole-outputs/iti-threshold-analysis-2026-06-11.md.
# quote_updates_10s >= 11 caught 13/13 positives at ~0.75s median latency with the
# fewest false positives. The 5s/8 variant fires ~0.25s sooner (one more FP).
DEFAULT_SURGE_QUOTE_WINDOW = 10          # seconds; must be one of WINDOWS
DEFAULT_SURGE_MIN_QUOTE_UPDATES = 11     # quote_updates in that window

# Start-staleness trigger (added 2026-06-14). bsln_over_prev_trd_gap is constant
# per session = hist baseline ITI / tape-staleness-at-subscribe (prev_trd_time_gap).
# A high value means the tape was very stale relative to the stock's own cadence at
# the instant we hit record — i.e. a surge that was already in progress. This value
# is available from the first trade, so the rule fires immediately. It is ORed with
# the quote-churn rule in should_fire. Strict greater-than; hardcoded (not CLI-tunable).
# Skipped when there is no valid ITI baseline (see _has_iti_baseline), so the 44444
# sentinel / a stale tape can't false-fire it.
BSLN_OVER_PREV_TRD_GAP_THRESHOLD = 100.0

# v2.3 (2026-08-14): Rule B is OFF. On the 90-file labelled corpus it buys ONE
# positive (SLND, ratio 13657) and pays FIVE negatives (CVKD 20777, SCWO 3607,
# DAIO 989, HTOO 585, CRWS 108). No threshold fixes that — CVKD sits ABOVE SLND,
# so every cut that keeps the positive keeps the worst negative too (2.2's own
# header already recorded this; 2.3 acts on it). Leave-one-out: -1 pos, -5.0
# weighted cost, the largest single improvement available on the trade path.
# Set True to restore 2.2 behaviour exactly.
RULE_B_ENABLED = False

# v2.3 (2026-08-14): Rule A is OFF. Leave-one-out on the corpus is -0.8 weighted
# cost for ZERO positives lost: its only positive (IPW) is caught the same
# instant by another leg, and it adds 2 neutrals. Retained behind a flag because
# it is the sole rule reading the QUOTE stream — if a future corpus contains a
# surge with churn but no qualifying prints, this is the leg that would catch it.
RULE_A_ENABLED = False

# Last-3-trades ITI-collapse trigger (added 2026-06-14). Fires when
# bslnITI_on_last3trades_avrgITI > BSLN_ITI_LAST3_THRESHOLD, i.e. the hist baseline
# ITI is >1550x the mean of the two most recent measured ITIs — the tape has sped up
# dramatically vs the stock's own cadence over the last couple of trades, a live
# (not start-of-session) surge signature. ORed with the other rules in should_fire.
# Strict greater-than; hardcoded (not CLI-tunable). Skipped when the value is None
# (first accepted trade, or no valid ITI baseline), so it cannot false-fire.
# 2026-07-30: 100 -> 1550. At 100 Rule C was the largest trade-path FP source on
# the 07-09..07-30 window (EVGN 229, PMA 205, MNDR 171, BNRG 158); 1550 sits above
# the SAGT neu (1532) and below the surviving RGNT neg (1706). Every pos that C
# caught below 1550 is covered by another leg (verified at the OR level).
BSLN_ITI_LAST3_THRESHOLD = 1550.0

# 2026-07-07 re-calibration (see mole-outputs/trigger-analysis-2026-07-07.md,
# re-runnable via analyze_trade_mole/analyze_trigger_rules.py). Fit on the
# _pos_/_neg_ labels of 2026-07-07 with the June labeled set as regression:
# 8/8 + 20/20 positives fire, 0/4 of the day's false positives survive, June
# false-positive names unchanged. All hardcoded (not CLI-tunable), like BSLN_*.
# 2026-07-30 (v2.2) precision re-fit of A/D/E on the 07-09..07-30 window (see
# mole-outputs/surge-predictor-analysis-2026-07-30.md): values below updated.

# Rule A flow gate: quote churn alone false-fired on 1-share dust prints
# (BLIN $0.28/s, SYNX $0.22/s); churn-confirmed positives trade >= $2/s (CMND $5.6/s).
# 2026-07-30: 2.0 -> 60.0. All five neg A-fires of the window churned at <= $53.3/s
# (CHAI/SPRC $2.3, GV $30.3, MEDS $52.0, VCIG $53.3) while the one pos that needs
# Rule A (IPW) traded $102.6/s — 60 kills all five and keeps IPW's 0.42s fire.
SURGE_A_MIN_DOLLAR_RATE_5S = 60.0

# Rule D — baseline-free ITI collapse: the tape prints <= this fast with at least
# this many trades in the 5s window. Covers 44444-sentinel names that Rules B/C
# must skip (BIAF fires D@0.25s). Nearest labeled negative: DYAI at 0.95s (3x margin).
# 2026-07-30: 0.3 -> 0.28. Slowest pos D-print is KIDZ at 0.276s; 0.28 shaves the
# 0.28-0.3 band without touching any pos.
SURGE_ITI_COLLAPSE_MAX_SEC = 0.28
SURGE_ITI_COLLAPSE_MIN_TRADES_5S = 3

# v2.3 (2026-08-14): Rule D may only fire this many seconds into the session.
# D is the largest trade-path false-fire source (6 pos / 5 neu / 8 neg) and its
# classes are indistinguishable on every FEATURE — same median measured_ITI
# (0.25), same trades_in_5s (3), same trades_in_1s (3), overlapping dollar_rate,
# spread and cum_volume. The one thing that does separate them is WHEN:
#
#     positives    session_age at fire   0.099 .. 0.501 s   (all 6)
#     negatives    ... plus              2.755 (IDAI), 3.003 (BNRG), 47.312 (NUWE)
#     neutrals     ... plus              2.502 (OMH),  15.676 (VTAK)
#
# D exists for the case "we hit record on a tape that is ALREADY running", which
# is by construction a first-trades phenomenon. A sub-0.28 s print 47 s into a
# quiet session is a different thing wearing the same number. Cap removes 3 neg
# + 2 neu at zero positive cost.
#
# 1.5 s is the MIDDLE of the plateau, not an edge. Swept on the corpus, every
# value in [0.6, 2.0] scores identically (pos 24/28, neu 9, neg 11); 0.3 loses a
# positive and 5.0 re-admits the late fires. The plateau's real walls are the
# slowest positive (VSEE, 0.501 s) and the earliest late false fire (OMH, a
# neutral, 2.502 s), so 1.5 buys ~3x headroom above the former while still
# excluding the latter. Sitting at 0.6 would score the same today and lose any
# future positive that fires a tenth of a second slower than VSEE.
# Set to None to disable the cap and restore 2.2 behaviour.
SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC = 1.5

# Rule E — dollar-flow burst confirmed by price: >= $10/s in the 5s window AND last
# price >= +1.2% above session start. Recovers sentinel-name positives with slow
# prints but real flow (SUGP fires E@2.62s). The price gate is what rejects AUID
# (max +0.78% before its -2.3% SELL) and SMCI — still the thinnest margin in the
# rule set; tune here first if a similar FP reappears.
# 2026-07-30: 0.01 -> 0.012 (kills the GV neg at +1.199%; no pos E-fire below +5.3%).
# A "Rule G" for MODERATE relative acceleration was built and REJECTED on
# 2026-08-08 — do not re-propose it without new evidence. Motivation was AZI
# 2026-08-07, which ran ~6.8x its baseline trade rate at ~0.75 s absolute ITI:
# between Rule D's absolute 0.28 s bar and Rule B/C's 100x/1550x relative bars,
# so nothing was watching that shape. Tested as
# `accel_{1,2,5}s_vs_hist_baseline >= G AND price_change >= 1.2%`, swept
# G in {3,5,8,12,20} x window {1,2,5} on the corrected 07-09..07-30 corpus:
#   * WITH the price condition it is first-to-fire in 1 file of 44 and moves
#     nothing (16/16 pos, 12/20 neg, median TTF 0.25 s at every setting) — the
#     directional term makes it a near-subset of Rule E.
#   * WITHOUT it, every threshold from 3x to 40x fires 20/20 neg and 8/8 neu.
#     A bare relative cut fires on everything; this is the same wall Rule C hit,
#     and it is why C sits at 1550 rather than at a "reasonable" multiple.
# Structural reason it could not have helped: the OR was already at 16/16 recall,
# and an ADDED branch can only increase negative fires, never kill them.
# Numbers: mole-outputs/surge-predictor-analysis-2026-08-08-*.md
#
# 2026-08-11: 10.0 -> 3.0. Driven by PLAG 2026-08-11 07:30 (FLANGSolo-gated PR,
# mole-outputs/trade-mole-table_PLAG_2026-08-11_07-30-01.csv). Its whole 149 s
# watch produced TWO fills — 1 lot MID @0.6016, then 18 lots BUY @0.62 — and
# nothing else in the rule set was within 35x of firing (B 1.31 vs 100, C 2.59 vs
# 1550, D 9.76 s vs 0.28, F 1.31 vs 100; A blocked outright at quote_updates_10s=3
# vs 11). Rule E was the ONLY near miss: its price leg passed at +3.06% while
# dollar_rate_5s reached only 4.46. Break-even is <=4.4, verified by replaying the
# recording through the real should_fire() (analyze_trade_mole/replay_mole.py),
# not a re-implementation. B/C/F being nowhere near is by design, not a
# miscalibration: they are relative to the name's own cadence and PLAG's
# itiBaseline is 876.9 s, so an 11-minute pre-PR gap is NORMAL for it
# (876.9/669.2 = 1.31).
#   LABELLED-corpus cost: none. sweep_thresholds.py --fixes none is identical at
#   every step from 10 down to 3 — pos 16/16, neu 7/8, neg 11/20, median TTF
#   0.25 s — over the 44 scored of the 61 labelled recordings >= 2026-07-09; 2.2
#   is where the first extra negative appears. But the dollar leg is INERT on
#   that corpus (the +1.2% price leg is what binds), so it endorses the change no
#   more than it forbids it — do not quote "zero cost" without the next para.
#   WIDER cost, measured over ALL 2.2-era recordings (>= 2026-07-30, 54 of 114
#   scored — the unlabelled live sessions the sweep never looks at): fires go
#   27 -> 30, i.e. +3 (+11%). The new fires are PLAG itself, OXBR 2026-08-04
#   ($rate 9.8, +4.73%) and DXST 2026-07-30 ($rate 5.0, +6.41%); REBN
#   2026-08-11 fires 2.25 s EARLIER via E instead of Rule C. Nine other files
#   differ only in the threshold printed inside surge_reason — same row, same
#   instant, cosmetic. OXBR and DXST are unlabelled and their recordings hold no
#   prints after the new fire point, so whether those two are wins is UNKNOWN;
#   this cut is accepted on PLAG (n=1) plus the absence of a measurable negative.
#   Label those two and re-check before loosening this constant any further.
# WARNING — coupled to DEDUP_EXCLUDE_TWIN_FROM_COUNTS. PLAG's 4.46 is
#   twin-inflated (that single 18-lot fill is counted twice); de-twinned it is
#   2.232, so at 3.0 this rule STOPS firing PLAG the moment that flag is turned
#   on. If it is ever flipped, move this constant to 2.0 in the SAME edit
#   (--fixes both is flat 10 -> 1.5 at neg 9/20, so 2.0 is free there).
# WARNING — direction of travel. At 3.0 Rule E is close to "price up >=1.2% with
#   any real flow behind it". The pre-2026-06-11 price-momentum rule was retired
#   for firing too loosely, and SURGE_FLOW_MIN_PRICE_RISE_PCT is now the only
#   thing holding this one. If an FP reappears, tune the PRICE leg, not this one.

SURGE_FLOW_MIN_DOLLAR_RATE_5S = 3.0
SURGE_FLOW_MIN_PRICE_RISE_PCT = 0.012

# Rule F — pre-trade start-staleness (added 2026-07-23). Snapshot-only twin of
# Rule B: fires off the reqMktData snapshot burst BEFORE the first trade prints,
# on bslnITI_over_pretrade_gap = hist baseline ITI / pretrade_gap, where
# pretrade_gap = (first-event wall clock) − LAST_TIMESTAMP (tick 45, the last
# pre-subscribe trade). Distinct from Rule B on purpose: Rule B anchors the gap to
# the first NEW trade and requires a live print to confirm; Rule F anchors to
# subscribe/now and acts on the snapshot alone (no post-subscribe confirmation), so
# it gets its OWN threshold and a gap floor. LAST_TIMESTAMP is whole-second, so the
# raw pretrade gap can be sub-second and inflate the ratio — PRETRADE_MIN_GAP_SEC
# clamps the denominator. Skipped when there is no valid ITI baseline (see
# _has_iti_baseline). Enabled by default; a valid ask must be present to price the
# entry (there is no trade print). CLI-tunable via --pretrade-staleness-threshold /
# --no-pretrade-staleness-fire.
PRETRADE_STALENESS_THRESHOLD = 100.0
PRETRADE_MIN_GAP_SEC = 1.0
PRETRADE_STALENESS_FIRE = True

# Rule F veto (v2.2, 2026-07-30). Rule F was the largest FP source of the
# 07-09..07-30 window (7 of 23 neg fires) and its ratio alone cannot separate
# (neg ratios straddle the pos range on both ends). A pretrade fire is KEPT only
# if all three hold, else it is suppressed (one PRETRADE_VETO row is written and
# the pretrade path is disabled for the session — the trade-path rules keep
# watching):
#   1. no trade has printed yet — F is a PRE-trade rule, but it could fire after
#      prints (FFAI: 3 trades at -0.6% below start, then F fired; F bypasses the
#      price-drop veto so this closed a real hole);
#   2. pretrade gap <= PRETRADE_MAX_GAP_SEC — the last pre-subscribe trade was
#      within ~a second of the snapshot (all 5 labeled pos sit at 1.00-1.18s;
#      the four vetoed negs at 1.41-1.72s);
#   3. ratio <= PRETRADE_MAX_RATIO — an absurdly high ratio is the signature of
#      a quasi-never-trading name plus one dust print, not a surge (LITS 34200
#      vs pos max 3420).
# On the labeled window: kills 6/7 neg F-fires, keeps 5/5 pos (and both neu).
# ⚠ Calibrated on only 5 pretrade pos — kill-switch: --no-pretrade-veto.
PRETRADE_VETO = True
PRETRADE_MAX_GAP_SEC = 1.25
PRETRADE_MAX_RATIO = 5000.0

# --------------------------------------------------------------------------- #
# v2.3 (2026-08-14) — pre-trade DIRECTION gate.
#
# The pre-trade rule fires off a snapshot, before any print, on the single fact
# that the tape is stale relative to the name's own cadence. Staleness is not
# direction: a name that has not traded because nothing happened looks exactly
# like a name that has not traded because the halt/PR is one second old. Scored
# on the corpus the pre-trade path is close to a coin flip — 13 pos / 6 neu /
# 7 neg — and it is HALF of all false fires.
#
# The direction the rule needs is already in the snapshot, in two ticks the mole
# received and discarded: tick 4 (LAST price) and tick 9 (previous CLOSE).
# Measured at the fire instant across the labelled corpus:
#
#     pre-trade positives   median +7.84%   range  -7.71 .. +13.99
#     pre-trade neutrals    median +5.57%   range  -6.00 ..  +9.09
#     pre-trade negatives   median +0.91%   range  -2.01 .. +12.68
#
# At 5.0% the gate keeps 8/8 of the pre-trade-ONLY positives, and drops 5 of 7
# firing negatives and 3 of 6 neutrals. The 4 positives it drops on this path
# (RUBI, HAO, VRAX, ARTL) sit in FULL recordings that keep running, so whether
# the trade path re-catches them is measured rather than assumed — which is the
# whole reason this gate was preferred to simply raising the staleness ratio.
#
# ⚠ BOTH ticks are required. No tick 4 or no tick 9 ⇒ gap unknown ⇒ NO FIRE.
#   That is not merely conservative, it is load-bearing: BLNE and FFAI (both
#   negatives) quoted an ask with no last price at all, and this is what stops
#   them. Substituting the ask for a missing last was measured and REJECTED —
#   ask/close scores positives 7.36-19.88 against negatives -1.93-12.79, which
#   erases the separation entirely.
# ⚠ PRE-TRADE ONLY. On the trade path this feature INVERTS: Rule B's negatives
#   gap up MORE than its positives (median +8.2% vs -0.3%). Do not generalise it.
# ⚠ ANDed with the 2.2 Rule-F veto, not a replacement for it. The veto still
#   independently blocks 5 negatives on gap/ratio grounds.
#
# Set to None to disable the gate and restore 2.2 pre-trade behaviour.
PRETRADE_MIN_GAP_VS_CLOSE_PCT = 5.0

# ---------------------------------------------------------------------------
# v2.4 (2026-09-18) — the six same-tick changes. Each reverts on its own.
# ---------------------------------------------------------------------------

# 1. Rule E's RELATIVE-FLOW leg. Rule E's dollar test is absolute; this makes it
#    relative to what the name normally trades:
#
#        flow_x_baseline_5s = dollar_rate_5s
#                             / (hist_trade_rate x hist_trade_size x last_price)
#
#    Numerator and denominator are both lot-denominated (see the RTVolume-lots
#    note at the bottom of this file), so the 100x cancels and the ratio is
#    unitless: "money is moving N times faster than this name's own normal".
#
#    At Rule E's fire instants on the 132-file corpus:
#        positives   0.01, 1.16, 1.40, 6.01, 23.7, 35.8, 37.8, 113, 1343
#        negatives   0.05, 0.20, 0.62, 0.72, 0.93, 3.74, 4.04, 4.40, 10.2
#    OR-level plateau is [5, 25] — every value in it scores pos 33 / neu 12 /
#    neg 11; 4 costs a negative, 30 costs a positive. 10.0 is mid-plateau
#    (geometric centre 11.2), deliberately NOT the 12 that would sit just above
#    the largest negative.
#    Skipped (like Rules B and C) when there is no valid ITI or trade-size
#    baseline — Rule D is the baseline-free fallback, which is its stated job.
#    Set to None to restore 2.3's purely absolute Rule E.
SURGE_E_MIN_FLOW_X_BASELINE = 10.0

# 2. Rule D's SIZE leg, in LOTS over the 5s window (20 lots ~ 2,000 shares).
#    2.3's D fires on three prints inside 0.28s regardless of size, which on a
#    tape where sub-100-share prints are already dropped still admits three
#    1-lot prints. This is the same dust filter Rule A received on 2026-07-07.
#    ⚠ NARROWEST PLATEAU IN THE FILE: [18, 21]. 16 gives back a negative, 24
#    costs a positive. 20 is the middle of three working values, not a wide
#    basin — treat it as the least-settled constant here and re-check it first
#    when new labels arrive.
#    Set to None to restore 2.3's size-blind Rule D.
SURGE_D_MIN_VOLUME_5S = 20.0

# 3. Rule C's MONEY gate, in lot-dollars of cumulative session dollar volume
#    (80 lot-$ ~ $8,000 real). C is the relative-cadence rule and says nothing
#    about whether the acceleration carries money.
#    OR-level plateau [60, 120]; 40 gives back a negative, 125 costs a positive.
#    Set to None to restore 2.3's Rule C.
SURGE_C_MIN_CUM_DOLLAR = 80.0

# 4. GLOBAL VWAP DIRECTION GATE, in percent. No TRADE-path rule may fire while
#    the last print sits more than this far below the session's own VWAP.
#    A burst of fast prints below the session VWAP is distribution wearing a
#    surge's numbers. Costs ZERO positives on a plateau spanning [-8, -0.2]
#    (40x wide). The wall at -0.05 is a float-equality artifact, not a finding:
#    at the first trade vwap == price exactly, so any gate at >= 0 is a knife
#    edge on equality and drops three positives. Stay strictly negative.
#    ⚠ Deliberately NOT applied to the pre-trade path, which has no VWAP.
#    Set to None to disable.
#    ⚠ v2.5 correction: the VWAP read here is IBKR's WHOLE-DAY VWAP (the VWAP
#    field of every RTVolume message), not the session's own, and it equals the
#    first print only when that print is the day's first trade. See
#    SURGE_VWAP_GATE_RULES for what that did to names that had traded higher
#    earlier in the day.
SURGE_MIN_PX_VS_VWAP_PCT = -1.0

# v2.5 (2026-10-02) — which trade-path rules the VWAP gate above holds.
#    2.4 applied it to every rule. Because the VWAP is the whole day's, a name
#    whose news move starts from BELOW an earlier, higher average (LESL
#    2026-09-30: -30% at the first print; LGHL 2026-09-30: -6.7%) cannot fire on
#    ANY rule until the move climbs back to within 1% of that old average — the
#    operator labelled both _late_ (LESL fired at +26 s after a +40% run, LGHL at
#    +58 s). E and N already require the price to have RISEN since the session
#    started (SURGE_FLOW_MIN_PRICE_RISE_PCT / SURGE_N_MIN_PRICE_RISE_PCT), so for
#    them the gate is a second, staler direction test. C and D carry no
#    direction leg at all and keep it — as do A, B and NF, which are switched
#    off by default but would need it for the same reason if re-enabled.
#      Labelled, vs gating every rule: LESL +26.2 s -> +3.9 s (E, 27.5% cheaper),
#      LGHL +57.9 s -> +2.5 s (E, 4.6% cheaper); no other file changes.
#      Gating D alone also moves LESL to C at +3.4 s, but adds TRNR 2026-08-14 on
#      the unlabelled set (fired live, stopped out -6.0%). Dropping the gate, or
#      reading the session's own VWAP, re-admits PHGE 2026-07-24 (a D fire 14.9%
#      under its day VWAP) — that one negative is what the gate is for.
#      Unlabelled control: +2 fires, IMCC 2026-09-23 (+23% max over the next
#      134 s) and SLE 2026-08-14 (fired live, stopped out -4.6%).
#    Set to None to restore 2.4 exactly (the gate holds every rule).
SURGE_VWAP_GATE_RULES = ("A", "B", "C", "D", "NF")

# 5. Branch N — the ABSOLUTE-flow path survives Rule E's new relative leg, but
#    only for a move four times larger than Rule E's +1.2%. This is what keeps
#    a genuine surge on a LIQUID name reachable at all: HIHO (historical ITI
#    0.4s) ran +5.31% off its session start with no relative-flow signature
#    whatsoever, and nothing else in the rule set can see it.
#    Plateau [3%, 5%]; 2.5% costs a negative, 5.5% costs the positive. 4% is
#    dead centre. Shares Rule E's SURGE_FLOW_MIN_DOLLAR_RATE_5S floor.
#    Set to None to disable.
SURGE_N_MIN_PRICE_RISE_PCT = 0.04

# 6. Branch NF — extreme relative flow, no price confirmation.
#    ⚠⚠ SHIPPED DISABLED (None). It scored perfectly on the labelled corpus —
#    pos 33 / neu 12 / neg 11 unchanged, 0 false fires of its own, and the
#    positive-latency median 1.00s -> 0.52s (NTRP 3.70 -> 0.43, AIRE 0.77 ->
#    0.52, BBLG 45.24 -> 30.09) — and it is switched off anyway, because the
#    UNLABELLED set showed what it is actually measuring.
#
#    Replaying 206 unlabelled live recordings >= 2026-07-09, 2.4 fires 59 times
#    against 2.3's 78, and NF is the ONLY source of added fires: 4 of them, and
#    all four have the same shape —
#
#        APVO  2 trades in 5s, price +0.22%, px_vs_vwap  0.00%, base ITI 34200s
#        BLIN  1 trade  in 5s, price  0.00%, px_vs_vwap -0.08%, base ITI 17100s
#        LIQT  2 trades in 5s, price -0.01%, px_vs_vwap +0.01%, base ITI  5700s
#        PLUR  2 trades in 5s, price  0.00%, px_vs_vwap -0.35%, base ITI 34200s
#
#    — one or two prints, no price movement at all, on a name whose baseline is
#    so dead that the DENOMINATOR alone produces the ratio. NF is not detecting
#    a flow burst; it is detecting "an illiquid name printed", which is Rule B's
#    start-staleness shape wearing different numbers. Rule B was switched off in
#    2.3 for buying 1 positive at the price of 5 negatives, and the three
#    positives NF catches have that same 1-2-print, 0.00%-move signature — they
#    are sessions that happened to become real afterwards, not sessions NF could
#    tell apart at the instant it fired.
#    It cannot be repaired either: requiring trades_in_5s >= 3 blocks all four
#    unlabelled fires AND all three positives, and a non-negative price leg
#    blocks only one of the four.
#
#    So it costs nothing to keep off (recall and precision are byte-identical
#    without it) and the only thing given up is 0.48s of median latency. If that
#    latency is later judged worth the risk, set this to 700.0: working plateau
#    is [520, 1000], and the margin over the highest value ANY non-positive
#    labelled file ever reaches (516) is only 1.36x — the thinnest in the file.
SURGE_NF_MIN_FLOW_X_BASELINE = None

# Rule C master switch. With the money gate above, C carries 2 positives for 1
# negative — leave-one-out through the real harness: switching it off gives
# pos 33 -> 31, cost 15.8 -> 14.8. Left ON because the operator's budget for
# this pass was "lose at most 2 positives" and 2.4 already spends both
# (RUBI 2026-07-16 and VIVK 2026-08-21).
RULE_C_ENABLED = True

# Trade-size baseline (v2.1). The universe pipeline writes 44444 when the
# historical trade-size fetch was ATTEMPTED but FAILED (see
# universe_finder/tradeSizeExplained.txt); treat it as "no baseline".
TRADE_SIZE_BASELINE_SENTINEL = 44444.0
# A print counts as "large" (block-ish) when size >= this multiple of baseline.
DEFAULT_LARGE_TRADE_MULT = 2.0

# Each fill is double-reported on RTVolume (tick 48) + RTTradeVolume (tick 77)
# ~0.5ms apart. A trade arriving < this gap after the previously accepted trade
# from the OTHER RT channel is treated as that duplicate and does not advance the
# measured-ITI series (it forward-fills the parent values instead).
DEDUP_EPSILON_SEC = 0.005

# ---------------------------------------------------------------------------
# Recorded-feature corrections (added 2026-08-08, both DEFAULT OFF).
#
# Found while investigating the AZI 2026-08-07 fire. Each changes the value of
# every count/rate column, and the 2026-07-30 A/D/E thresholds were fit on the
# UNCORRECTED columns, so turning either on without re-deriving the constants
# would silently move the operating point. Flip them together with the new
# thresholds once the recalibration report exists. Toggle offline with
# `analyze_trade_mole/replay_mole.py --set NAME=True`.
#
# 1. The DEDUP_EPSILON_SEC dedup above suppresses the RTVolume/RTTradeVolume
#    twin for the measured-ITI series only — the twin is still counted into
#    _cum_* and _trade_log, so trades_in_{w}s, volume/dollar sums and every rate
#    derived from them double-count each fill. The inflation is not a constant
#    factor: it tracks the odd-lot share, which varies per symbol. On AZI,
#    cum_volume read 208 against a true 141 and dollar_rate_5s 71.96 against 48.9.
DEDUP_EXCLUDE_TWIN_FROM_COUNTS = False
# 2. Window rates divide by the NOMINAL window w, so during the first w seconds
#    of a session — the whole life of a PR surge — they are diluted by the time
#    we had no subscription for. At AZI's fire the session was 2.53 s old and
#    every 5 s rate read ~2x low. With this on, the divisor is the elapsed
#    session age, floored at RATE_MIN_ELAPSED_SEC so a single early print cannot
#    manufacture an unbounded rate.
RATE_NORMALIZE_BY_SESSION_AGE = False
# Never claim to have measured a rate over less than this. Itself a tunable —
# it sets how aggressive the first second of a session looks, so the
# recalibration should sweep it rather than treat it as fixed.
RATE_MIN_ELAPSED_SEC = 1.0

# v2.5 (2026-10-03, operator request) — keep recording this many seconds AFTER
# the buy-signal. RECORD-ONLY: the decision is made and handed to x-wing exactly
# as before; nothing after the trigger can move it.
#
# Why: every recording used to STOP at the live trigger, so a stricter candidate
# rule could never be seen firing later — "no longer fires" only ever meant "not
# inside the recorded window" — and no confirmation wait could be calibrated:
# on the 2026-10-02 corpus 102 of the 106 fired files had < 1 s after the fire.
# The tail removes that limit for every session that fires from now on.
#
# Cost, because the clerk feeds the mole FIRST on every tick and x-wing waits
# behind it: during the tail the tick handlers only latch state and append the
# row (as they do before the trigger), and _process_trade_event skips ITIs,
# windows and should_fire() entirely and appends the print as a RAW row (price,
# size, NBBO, RT fields). A few microseconds a tick. Every tail row carries
# post_trigger=True. replay_mole.py replays them through the real handlers, so
# every feature can be rebuilt offline and a candidate rule CAN fire inside them.
#
# The tail ends at whichever comes first: this many seconds, or the session's
# teardown (sentiment timeout / x-wing + T47 done — the clerk cancels market data
# then, and must not hold the client longer). The clerk stamps one
# POST_TRIGGER_END row saying which, and rewrites the CSV at that moment
# (it still writes it once at the trigger too, so a crash loses nothing).
# ⚠ The tail contains OUR OWN entry (and possibly exit) prints from x-wing; match
#   against xwing_tables fills before reading the tail as the market's response.
# ⚠ Driven mode only (stop_at_trigger=True). Standalone --keep-recording-after-
#   trigger still records everything with full features, as before.
# Set to 0 / None to restore 2.4 exactly (stop at the trigger row).
POST_TRIGGER_RECORD_SEC = 60.0

# Generic ticks for reqMktData
# 2026-10-02: +595 (ticks 63/64/65 = 3/5/10-minute volume). Requested for
# X-WING's exit calibration, not for the mole -- the mole's tickGeneric does not
# branch on 63/64/65 so its rules cannot move. Proven with
# analyze_trade_mole/replay_mole.py --self-check before this went live.
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221,595"

# Eastern Time zone for the pre-market start gate
ET = ZoneInfo("America/New_York")


# ============================================================================
# CLI helpers
# ============================================================================


def _compute_collection_start_delay(now=None):
    """
    Return (seconds_to_wait, target_dt_in_ET) for when to begin collecting.

    On a WEEKDAY in the open window [04:00, 20:00) ET, return (0.0, None) and the
    caller starts collection immediately. Otherwise the market is closed (the
    [20:00, 04:00) overnight window, OR any time on a weekend), so defer to the
    next 04:00 ET, rolling Saturday/Sunday targets forward to Monday 04:00 ET so
    we never subscribe to a dead weekend market. NYSE holidays are NOT handled
    here (they need an external market calendar) — a follow-up.
    """
    now = now if now is not None else datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)

    h = now.hour
    is_weekend = now.weekday() >= 5          # Saturday(5) / Sunday(6)
    if 4 <= h < 20 and not is_weekend:
        return 0.0, None

    if h >= 20:
        base = now + timedelta(days=1)
    else:
        base = now
    target = base.replace(hour=4, minute=0, second=0, microsecond=0)
    while target.weekday() >= 5:             # roll Sat/Sun forward to Monday 04:00
        target += timedelta(days=1)
    delay = (target - now).total_seconds()
    return max(delay, 0.0), target


def parse_lifetime(s: str) -> int:
    """Parse mm:ss -> total seconds."""
    try:
        mm, ss = s.split(":")
        total = int(mm) * 60 + int(ss)
        if total <= 0:
            raise ValueError
        return total
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--lifeTime must be in mm:ss format with positive value, got {s!r}"
        )


def resolve_output_path(output_arg: str, symbol: str) -> str:
    """
    Generate the output filename.

    If `output_arg` is a directory (exists as dir or ends with a separator),
    we auto-generate `SYMBOL_YYYY-MM-DD_HH-MM.txt` inside it.
    Otherwise we treat `output_arg` as the full literal file path.
    """
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
    fname = f"{symbol}_{ts}.txt"

    is_dir = (
        output_arg.endswith(os.sep)
        or output_arg.endswith("/")
        or os.path.isdir(output_arg)
    )
    if is_dir:
        os.makedirs(output_arg, exist_ok=True)
        return os.path.join(output_arg, fname)

    parent = os.path.dirname(output_arg)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return output_arg


def make_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "STK"
    c.exchange = "SMART"
    c.primaryExchange = "NASDAQ"  # SMART will route; this is just a disambiguator
    c.currency = "USD"
    return c


# ============================================================================
# IBKR Client / Wrapper
# ============================================================================

class IBKRSurgeApp(EWrapper, EClient):
    """
    Captures every trade and quote into a raw records list, maintains a
    bounded rolling trade-time deque AND a bounded rolling quote deque for
    real-time surge detection.

    Thread model
    ------------
    All ibapi callbacks fire on the EClient.run() thread. State mutation is
    confined to that thread; the main thread only reads `self.records`
    *after* disconnect(), so no locking is required.

    Trade-log entry schema (5-tuple):
        (mono_ts, price, size, kind, aggr)
        kind ∈ {"exact", "provisional"}
        aggr ∈ {"BUY", "SELL", "MID", "UNKNOWN"}

    Quote-log entry schema (6-tuple):
        (mono_ts, bid, ask, bid_size, ask_size, spread_pct)
    """

    def __init__(
        self,
        symbol: str,
        baseline_avg_iti: float,
        surge_dollar_rate: float,
        surge_max_spread_pct: float,
        surge_min_lift_ratio: float,
        surge_min_bid_drift_bp: float,
        surge_min_trades_5s: int,
        surge_max_price_drop_pct: float = DEFAULT_SURGE_MAX_PRICE_DROP_PCT,
        surge_min_quote_updates: int = DEFAULT_SURGE_MIN_QUOTE_UPDATES,
        surge_quote_window: int = DEFAULT_SURGE_QUOTE_WINDOW,
        baseline_avg_trade_size: Optional[float] = None,
        large_trade_mult: float = DEFAULT_LARGE_TRADE_MULT,
        pretrade_staleness_fire: bool = PRETRADE_STALENESS_FIRE,
        pretrade_staleness_threshold: float = PRETRADE_STALENESS_THRESHOLD,
        pretrade_veto: bool = PRETRADE_VETO,
        logger: Optional[logging.Logger] = None,
    ):
        EClient.__init__(self, wrapper=self)
        self.symbol = symbol
        # Per-instance logger so concurrent duos (driven mode) can log to
        # distinct files. Defaults to the module logger for standalone use.
        self._log = logger or logging.getLogger("trade-mole")

        # ITI_baseline — the strategic feature. Preserved verbatim: feeds the
        # hist_baseline_* columns and accel_*_vs_hist_baseline ratios.
        self._hist_baseline_avg_iti = baseline_avg_iti
        self._hist_baseline_trade_rate = 1.0 / baseline_avg_iti
        # Whether the ITI baseline is usable for the start-staleness trigger.
        # The universe pipeline writes the same 44444 sentinel on a failed fetch;
        # a missing / non-positive / sentinel value disables the bsln_over_prev_trd_gap
        # rule (it must not false-fire on garbage), while the legacy hist_baseline_*
        # columns are still emitted unguarded as before.
        self._has_iti_baseline = (
            baseline_avg_iti is not None
            and baseline_avg_iti > 0
            and abs(baseline_avg_iti - TRADE_SIZE_BASELINE_SENTINEL) > 1e-6
        )

        # Trade-size baseline (v2.1). The orchestrator supplies the historical
        # average shares-per-trade (RTH/ETH already selected). It is defined to
        # match mean_size exactly, so it is directly comparable to the M6
        # mean_size_{w}s columns. A missing / non-positive / 44444-sentinel
        # value disables every M9 column (they are emitted as None).
        self._hist_baseline_trade_size = baseline_avg_trade_size
        self._has_trade_size_baseline = (
            baseline_avg_trade_size is not None
            and baseline_avg_trade_size > 0
            and abs(baseline_avg_trade_size - TRADE_SIZE_BASELINE_SENTINEL) > 1e-6
        )
        self._large_trade_mult = large_trade_mult

        # New composite-rule thresholds
        self._surge_dollar_rate = surge_dollar_rate
        self._surge_max_spread_pct = surge_max_spread_pct
        self._surge_min_lift_ratio = surge_min_lift_ratio
        self._surge_min_bid_drift_bp = surge_min_bid_drift_bp
        self._surge_min_trades_5s = surge_min_trades_5s
        self._surge_max_price_drop_pct = surge_max_price_drop_pct

        # Active quote-churn rule thresholds (see should_fire).
        self._surge_min_quote_updates = surge_min_quote_updates
        self._surge_quote_window = surge_quote_window

        # Rule F — pre-trade start-staleness (snapshot-only). Independent of the
        # Rule B threshold; see _maybe_fire_pretrade / _bslnITI_over_pretrade_gap.
        self._pretrade_fire = pretrade_staleness_fire
        self._pretrade_threshold = pretrade_staleness_threshold
        # v2.2 Rule F veto (see PRETRADE_VETO block); False = 2.1 behaviour.
        self._pretrade_veto = pretrade_veto

        # Request IDs
        self.req_id_mkt = 1001

        # Live state
        self._session_start_mono: Optional[float] = None
        self._start_price: Optional[float] = None       # first-trade price = session anchor
        self._last_trade_price: Optional[float] = None  # most recent executed trade price
        self._last_trade_mono: Optional[float] = None
        self._last_bid: Optional[float] = None
        self._last_ask: Optional[float] = None
        self._last_bid_size: Optional[float] = None
        self._last_ask_size: Optional[float] = None
        self._cum_volume: int = 0
        self._cum_trade_count: int = 0
        self._cum_dollar_volume: float = 0.0
        self._vwap: Optional[float] = None
        self._halted: int = 0
        self._shortable: Optional[float] = None
        self._shortable_shares: Optional[float] = None
        self._mark_price: Optional[float] = None
        self._last_rth_trade: Optional[float] = None
        self._tws_trade_rate: Optional[float] = None
        self._tws_volume_rate: Optional[float] = None
        self._tws_trade_count: Optional[int] = None
        # v2.5 — IBKR's running day volume in LOTS, RECORD-ONLY (no rule reads
        # it). Fed by tick 8 and by the totalVolume field of every tick-48
        # RTVolume message, the zero-size volume-only ones included, so it counts
        # the sub-100-share prints and the aggregated volume the print path
        # misses. (mono_ts, day_volume), trimmed to the longest window plus the
        # newest entry older than it, so a windowed difference can be taken.
        self._true_vol_log: deque = deque()

        # Tick-54 reconciliation state (for aggregated RTVolume bursts)
        self._last_tws_trade_count: Optional[int] = None
        self._tick54_seen: bool = False
        # Pending provisional entries awaiting tick-54 confirmation.
        # Each: (mono_ts, rt_size, rt_total_volume, price, aggr)
        self._pending_rt_events: deque = deque()

        # Rolling trade log: (mono_ts, price, size, kind, aggr)
        self._trade_log: deque = deque()

        # Rolling quote log: (mono_ts, bid, ask, bid_size, ask_size, spread_pct)
        # Updated from any tickPrice (1/2) or tickSize (0/3) call.
        self._quote_log: deque = deque()

        # Measured-ITI state. last_timestamp_at_subscribe is the LAST_TIMESTAMP
        # (tick 45) frozen from the first such tick — the exchange time of the
        # last trade BEFORE we subscribed; it seeds the first measured ITI.
        self._last_timestamp_at_subscribe: Optional[float] = None
        # Wall-clock epoch of the moment we started recording (first event seen).
        # prev_trd_time_gap = this − LAST_TIMESTAMP: how stale the tape was at the
        # instant we hit record (available before any trade prints). NOTE: tick-45
        # is whole-second resolution and this is the local clock vs the exchange
        # clock, so it is a coarse, conservative upper bound on staleness.
        self._subscribe_wall: Optional[float] = None
        # Wall-clock epoch of the very first tick of ANY kind (quote/size/string/
        # generic). Rule F anchors its staleness gap here so it can fire off the
        # snapshot before the first trade sets _subscribe_wall. Kept separate from
        # _subscribe_wall so the recorded prev_trd_time_gap column (trade path) is
        # unchanged.
        self._first_event_wall: Optional[float] = None
        # v2.3 snapshot prices, for the pre-trade direction gate. tick 4 is the
        # LAST traded price and tick 9 the PREVIOUS CLOSE; reqMktData delivers
        # both in the opening snapshot, ahead of the first ask, so they are
        # available at the moment the pre-trade rule wants to fire. The mole
        # received these ticks before v2.3 and simply dropped them on the floor.
        # Kept separate from _last_trade_price, which is the RTVolume print
        # stream and is by definition still None when the pre-trade rule runs.
        self._snap_last: Optional[float] = None
        self._snap_close: Optional[float] = None
        # Log the pre-trade gap gate's first hold only; it is re-evaluated on
        # every NBBO update and would otherwise spam a whole session's log.
        self._pretrade_gap_gate_logged: bool = False
        self._prev_trd_time_gap: Optional[float] = None
        self._last_accepted_trade_mono: Optional[float] = None
        self._last_accepted_rt_source: Optional[str] = None
        self._prev_measured_iti: Optional[float] = None
        self._prev_ratio_baseline: Optional[float] = None
        # bslnITI_on_last3trades_avrgITI = hist baseline ITI / mean of the 2 most
        # recent accepted measured ITIs. Forward-filled on deduped twin prints.
        self._prev_bsln_on_last3: Optional[float] = None
        # Current trade's bslnITI_on_last3trades_avrgITI, exposed to should_fire
        # (Rule C). Set each accepted/duplicate trade just before surge detection.
        self._cur_bsln_on_last3: Optional[float] = None
        # Current trade's measured_ITIsec, exposed to should_fire (Rule D).
        # Forward-filled on deduped twin prints, mirroring Rule C's value.
        self._cur_measured_iti: Optional[float] = None
        # v2.3 — current trade's session age, exposed to should_fire for Rule D's
        # window cap. None until the first accepted trade, which is safe: Rule D
        # cannot fire before then anyway (it needs a measured ITI).
        self._cur_session_age: Optional[float] = None

        # Captured event records -> DataFrame at end
        self.records: list = []

        # Connection signaling
        self.connected_event = threading.Event()
        self.disconnected_flag = False

        # Buy-signal / stop-at-trigger (driven mode).
        #   buy_signal_callback(last_ask) is invoked once when the trigger
        #   fires; stop_at_trigger gates all recording afterwards.
        self.buy_signal_callback = None
        self.stop_at_trigger = True
        self.triggered = False
        # v2.5 post-trigger tail (see POST_TRIGGER_RECORD_SEC): wall-clock end of
        # the tail, set when the buy-signal fires; closed early by
        # end_post_trigger() when the clerk tears the session down.
        self._post_trigger_until: Optional[float] = None
        self._post_trigger_closed: bool = False

    # ------------------------------------------------------------------
    # Lifecycle / errors
    # ------------------------------------------------------------------

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2107, 2158, 2119, 2100, 2108):
            self._log.info(f"[IBKR {errorCode}] {errorString}")
            return
        self._log.warning(f"[IBKR err {errorCode}] reqId={reqId}: {errorString}")

    def nextValidId(self, orderId):
        self._log.info(f"Connected to IBKR. nextValidId={orderId}")
        self.connected_event.set()

    def connectionClosed(self):
        self.disconnected_flag = True
        self._log.info("IBKR connection closed.")

    # ------------------------------------------------------------------
    # Quote-log helper (M4 / M5 / M7)
    # ------------------------------------------------------------------

    def _note_day_volume(self, now_mono: float, day_volume) -> None:
        """v2.5 — latch IBKR's day volume (lots). Monotone: a smaller value
        (out-of-order delivery) is ignored. Record-only."""
        if day_volume is None:
            return
        log = self._true_vol_log
        v = float(day_volume)
        if log and v < log[-1][1]:
            return
        log.append((now_mono, v))
        cutoff = now_mono - DEQUE_HISTORY_SECONDS
        while len(log) >= 2 and log[1][0] <= cutoff:
            log.popleft()

    def _tail_open(self) -> bool:
        """v2.5 — True while the post-trigger tail is recording (only ever
        consulted after the trigger, so it costs nothing before it)."""
        if self._post_trigger_closed or self._post_trigger_until is None:
            return False
        return time.time() <= self._post_trigger_until

    def _record_raw_trade(self, now_mono, now_wall, price, size, rt_time_ms,
                          single_trade_flag, rt_total_volume, rt_source) -> None:
        """v2.5 — one post-trigger print as a RAW row: everything replay_mole
        needs to drive it back through _process_trade_event, plus the NBBO for
        reading. No ITI, no windows, no should_fire — the clerk feeds the mole
        before x-wing on every tick. Never raises."""
        try:
            self.records.append({
                "event_type": "TRADE",
                "local_arrival_time": now_wall,
                "local_arrival_iso": datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
                "local_mono_time": now_mono,
                "exchange_time_epoch": (rt_time_ms / 1000.0) if rt_time_ms else None,
                "rt_source": rt_source,
                "price": price,
                "size": size,
                "aggr": self._classify_aggressor(price),
                "rt_single_trade_flag": single_trade_flag,
                "rt_total_volume": rt_total_volume,
                "bid": self._last_bid,
                "ask": self._last_ask,
                "bid_size": self._last_bid_size,
                "ask_size": self._last_ask_size,
                "vwap": self._vwap,
                "post_trigger": True,
            })
        except Exception as e:   # a recording bug must never cost x-wing its tick
            self._log.debug(f"post-trigger raw trade row failed: {e}")

    def end_post_trigger(self, reason: str = "complete") -> None:
        """v2.5 — close the post-trigger tail (idempotent) and stamp ONE
        POST_TRIGGER_END row saying how it ended: 'complete' (the clerk's timer,
        POST_TRIGGER_RECORD_SEC elapsed) or 'session_end:<why>' (torn down
        first). A file whose tail has no such row was cut off by a crash. Called
        from the clerk's threads; safe against the ibapi thread appending."""
        if self._post_trigger_until is None or self._post_trigger_closed:
            return
        self._post_trigger_closed = True
        try:
            now = time.time()
            started = self._post_trigger_until - (POST_TRIGGER_RECORD_SEC or 0.0)
            self.records.append({
                "event_type": "POST_TRIGGER_END",
                "local_arrival_time": now,
                "local_arrival_iso": datetime.fromtimestamp(now).isoformat(timespec="milliseconds"),
                "value_str": f"{reason} after {now - started:.1f}s",
                "post_trigger": True,
            })
        except Exception as e:
            self._log.debug(f"post-trigger end row failed: {e}")

    def _true_vol_window(self, now_mono: float, window: int = 5) -> Optional[float]:
        """v2.5 — lots traded in the last `window` seconds by IBKR's day-volume
        counter: newest value minus the last value at or before the window start
        (the first value, i.e. the subscribe snapshot, if none is that old)."""
        log = self._true_vol_log
        if not log:
            return None
        cut = now_mono - window
        base = log[0][1]
        for ts, v in log:
            if ts > cut:
                break
            base = v
        return log[-1][1] - base

    def _append_quote_log(self, now_mono: float):
        """Append current NBBO snapshot to the quote log and trim to history."""
        b, a = self._last_bid, self._last_ask
        bs, as_ = self._last_bid_size, self._last_ask_size
        sp = None
        if b is not None and a is not None and a > b:
            mid = (a + b) / 2.0
            if mid > 0:
                sp = (a - b) / mid
        self._quote_log.append((now_mono, b, a, bs, as_, sp))
        cutoff = now_mono - DEQUE_HISTORY_SECONDS
        while self._quote_log and self._quote_log[0][0] < cutoff:
            self._quote_log.popleft()

    # ------------------------------------------------------------------
    # Trade events (RTVolume via tickString) - PRIMARY signal source
    # ------------------------------------------------------------------

    def _classify_aggressor(self, price: float) -> str:
        b, a = self._last_bid, self._last_ask
        if b is not None and a is not None and a > b:
            if price >= a:
                return "BUY"
            if price <= b:
                return "SELL"
            return "MID"
        return "UNKNOWN"

    def _process_trade_event(
        self,
        now_mono: float,
        now_wall: float,
        price: float,
        size: int,
        rt_time_ms: Optional[int],
        single_trade_flag: bool,
        rt_total_volume: Optional[int],
        rt_source: str,
    ):
        # Stop-at-trigger: once the buy-signal has fired, recording halts.
        # v2.5: ...except for the post-trigger tail, which keeps the RAW print.
        if self.triggered and self.stop_at_trigger:
            if self._tail_open():
                self._record_raw_trade(now_mono, now_wall, price, size, rt_time_ms,
                                       single_trade_flag, rt_total_volume, rt_source)
            return
        # v2.5 record-only: tick 48's totalVolume is IBKR's day volume (tick 77
        # carries a different total, unreportable trades excluded — skipped).
        if rt_source == "RTVolume":
            self._note_day_volume(now_mono, rt_total_volume)
        if self._session_start_mono is None:
            self._session_start_mono = now_mono
            self._subscribe_wall = now_wall  # epoch of recording start, for prev_trd_time_gap
            self._start_price = price       # anchor = first trade price of the session
        self._last_trade_price = price

        # Inter-trade time
        iti = None
        if self._last_trade_mono is not None:
            iti = now_mono - self._last_trade_mono
        self._last_trade_mono = now_mono

        # Measured-ITI columns. Local-clock trade-to-trade gaps, deduped per real
        # trade (the RTVolume/RTTradeVolume twin of a fill forward-fills instead of
        # injecting a ~0.0005s spike). The FIRST accepted trade is seeded from the
        # exchange-epoch pre-first-trade gap (first trade time − LAST_TIMESTAMP at
        # subscribe). All ratios/velocities guard zero/None denominators.
        measured_iti = None
        ratio_base_over_iti = None
        bsln_on_last3 = None
        m_collapse_ratio = m_collapse_diff = m_collapse_vel = None
        r_collapse_ratio = r_collapse_diff = r_collapse_vel = None

        is_duplicate = (
            self._last_accepted_trade_mono is not None
            and rt_source != self._last_accepted_rt_source
            and (now_mono - self._last_accepted_trade_mono) < DEDUP_EPSILON_SEC
        )

        if is_duplicate:
            # Same fill on the other RT channel: forward-fill, do not advance state.
            measured_iti = self._prev_measured_iti
            ratio_base_over_iti = self._prev_ratio_baseline
            bsln_on_last3 = self._prev_bsln_on_last3
        else:
            if self._last_accepted_trade_mono is None:
                # Seed the first measured ITI from the exchange-clock pre-first-trade
                # gap (first trade time − LAST_TIMESTAMP). This stays within the
                # exchange clock, so it is the cleaner "true first interval".
                exch = (rt_time_ms / 1000.0) if rt_time_ms else None
                first_iti = None
                if exch is not None and self._last_timestamp_at_subscribe is not None:
                    first_iti = exch - self._last_timestamp_at_subscribe
                measured_iti = first_iti
                # prev_trd_time_gap: staleness of the tape at recording-start, the
                # snapshot lead signal (computed once, available at t=0).
                if (
                    self._prev_trd_time_gap is None
                    and self._subscribe_wall is not None
                    and self._last_timestamp_at_subscribe is not None
                ):
                    self._prev_trd_time_gap = (
                        self._subscribe_wall - self._last_timestamp_at_subscribe
                    )
            else:
                measured_iti = now_mono - self._last_accepted_trade_mono

            if measured_iti is not None and measured_iti > 0:
                ratio_base_over_iti = self._hist_baseline_avg_iti / measured_iti

            pm = self._prev_measured_iti
            if measured_iti is not None and pm is not None:
                m_collapse_diff = measured_iti - pm
                if pm > 0:
                    m_collapse_ratio = measured_iti / pm
                if measured_iti > 0:
                    m_collapse_vel = (measured_iti - pm) / measured_iti
                # bslnITI_on_last3trades_avrgITI: baseline ITI over the mean of the
                # 2 most recent measured ITIs (current + previous accepted trade).
                # None on the first accepted trade (pm is None there).
                avg2 = (measured_iti + pm) / 2.0
                if avg2 > 0:
                    bsln_on_last3 = self._hist_baseline_avg_iti / avg2
            pr = self._prev_ratio_baseline
            if ratio_base_over_iti is not None and pr is not None:
                r_collapse_diff = ratio_base_over_iti - pr
                if pr != 0:
                    r_collapse_ratio = ratio_base_over_iti / pr
                if measured_iti is not None and measured_iti > 0:
                    r_collapse_vel = (ratio_base_over_iti - pr) / measured_iti

            self._last_accepted_trade_mono = now_mono
            self._last_accepted_rt_source = rt_source
            self._prev_measured_iti = measured_iti
            self._prev_ratio_baseline = ratio_base_over_iti
            self._prev_bsln_on_last3 = bsln_on_last3

        # M1 — aggressor classification against the prevailing NBBO snapshot
        aggr = self._classify_aggressor(price)

        # A twin is the SAME fill re-reported on the other RT channel. Counting it
        # inflates every count/volume/rate column (see
        # DEDUP_EXCLUDE_TWIN_FROM_COUNTS). The twin still gets its own recorded
        # row — the audit trail and the forward-filled ITI columns are unchanged —
        # it just stops contributing to the totals and the rolling window scan.
        count_this = not (is_duplicate and DEDUP_EXCLUDE_TWIN_FROM_COUNTS)

        if count_this:
            # Cumulative totals (patched later by reconciler for aggregated events)
            self._cum_trade_count += 1
            self._cum_volume += size
            self._cum_dollar_volume += price * size

            # Append to trade log
            if single_trade_flag:
                self._trade_log.append((now_mono, price, size, "exact", aggr))
            else:
                self._trade_log.append((now_mono, price, size, "provisional", aggr))
                self._pending_rt_events.append(
                    (now_mono, size, rt_total_volume, price, aggr)
                )

        # Trim trade log
        cutoff = now_mono - DEQUE_HISTORY_SECONDS
        while self._trade_log and self._trade_log[0][0] < cutoff:
            self._trade_log.popleft()

        # Drop pending entries older than 2s (tick-54 lag tolerance)
        pending_cutoff = now_mono - 2.0
        while self._pending_rt_events and self._pending_rt_events[0][0] < pending_cutoff:
            self._pending_rt_events.popleft()

        # Rolling window metrics (trade-log scan + quote-log scan)
        win = self._compute_windows(now_mono)

        # Surge detection — composite rule on new metrics
        session_age = now_mono - self._session_start_mono
        # Expose this trade's bslnITI_on_last3trades_avrgITI (Rule C) and
        # measured_ITIsec (Rule D) to should_fire.
        self._cur_bsln_on_last3 = bsln_on_last3
        self._cur_measured_iti = measured_iti
        # v2.3 — and this trade's session age, for Rule D's window cap. Threaded
        # on the instance rather than through `win` on purpose: _compute_windows'
        # dict does NOT carry session_age_sec (the recorded column of that name is
        # written separately, below), and adding a key to `win` would add a column
        # to every TRADE row and break schema comparability with the 2.2 corpus.
        self._cur_session_age = session_age
        surge, reason = self._detect_surge(win, iti)

        # v2.5 record-only: true volume over 5 s, and as a multiple of the name's
        # normal 5-s volume. The baseline trade size is in SHARES and every live
        # volume in LOTS, hence the /100 (tradeSizeExplained.txt).
        true_vol_5s = self._true_vol_window(now_mono, 5)
        true_vol_x_normal_5s = None
        if (true_vol_5s is not None and self._has_iti_baseline
                and self._has_trade_size_baseline):
            normal_5s = 5.0 * self._hist_baseline_trade_rate * self._hist_baseline_trade_size / 100.0
            if normal_5s > 0:
                true_vol_x_normal_5s = true_vol_5s / normal_5s

        rec = {
            "event_type": "TRADE",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
            "local_mono_time": now_mono,
            "exchange_time_epoch": (rt_time_ms / 1000.0) if rt_time_ms else None,
            "exchange_time_iso": (
                datetime.fromtimestamp(rt_time_ms / 1000.0).isoformat(timespec="milliseconds")
                if rt_time_ms else None
            ),
            "tick_type": None,
            "rt_source": rt_source,
            "price": price,
            "size": size,
            "aggr": aggr,
            "rt_single_trade_flag": single_trade_flag,
            "rt_total_volume": rt_total_volume,
            "bid": self._last_bid,
            "ask": self._last_ask,
            "bid_size": self._last_bid_size,
            "ask_size": self._last_ask_size,
            "spread": self._spread(),
            "spread_pct": self._spread_pct(price),
            "midprice": self._midprice(),
            "microprice": self._microprice(),
            "cum_volume": self._cum_volume,
            "cum_trade_count": self._cum_trade_count,
            "cum_dollar_volume": self._cum_dollar_volume,
            "vwap": self._vwap,
            # v2.4 decision inputs, recorded so a fire (or a non-fire) is
            # explainable from the table alone. Appended, never reordered.
            "px_vs_vwap_pct": self._px_vs_vwap_pct(),
            "flow_x_baseline_5s": self._flow_x_baseline(win, 5),
            "measured_ITIsec": measured_iti,
            "bslnITI_on_last3trades_avrgITI": bsln_on_last3,
            "ratio_baseline_over_measured_ITI": ratio_base_over_iti,
            "measured_ITIsec_collapse_ratio": m_collapse_ratio,
            "measured_ITIsec_collapse_diff": m_collapse_diff,
            "measured_ITIsec_collapse_velocity": m_collapse_vel,
            "ratio_baseline_collapse_ratio": r_collapse_ratio,
            "ratio_baseline_collapse_diff": r_collapse_diff,
            "ratio_baseline_collapse_velocity": r_collapse_vel,
            "inter_trade_time_sec": iti,
            "session_age_sec": session_age,
            "halted": self._halted,
            "shortable": self._shortable,
            "shortable_shares": self._shortable_shares,
            "mark_price": self._mark_price,
            "last_rth_trade": self._last_rth_trade,
            "tws_trade_rate_per_min": self._tws_trade_rate,
            "tws_volume_rate_per_min": self._tws_volume_rate,
            "tws_trade_count": self._tws_trade_count,
            "price_change_since_start_pct": (
                (price - self._start_price) / self._start_price if self._start_price else None
            ),
            "surge_detected": surge,
            "surge_reason": reason,
            # v2.5 record-only — see _note_day_volume. Appended, never reordered.
            "true_day_volume": self._true_vol_log[-1][1] if self._true_vol_log else None,
            "true_vol_5s": true_vol_5s,
            "true_vol_x_normal_5s": true_vol_x_normal_5s,
        }
        rec.update(win)
        self.records.append(rec)

        if surge:
            iti_str = f"{iti:.2f}s" if iti is not None else "n/a"
            lift_5s = win["lift_offer_ratio_5s"]
            lift_str = f"{lift_5s:.2f}" if lift_5s is not None else "n/a"
            self._log.warning(
                f"\U0001F680 SURGE {self.symbol} @ ${price:.4f} sz={size} "
                f"| quote_updates_{self._surge_quote_window}s="
                f"{win.get(f'quote_updates_{self._surge_quote_window}s')} "
                f"| trades_5s={win['trades_in_5s']} $rate_5s={win['dollar_rate_5s']:.0f}/s "
                f"| lift_5s={lift_str} "
                f"| bid_drift_5s={win['bid_drift_5s_bp']}bp "
                f"| iti={iti_str} | {reason}"
            )
            # Fire the buy-signal exactly once. The trigger row is already in
            # self.records; stop_at_trigger halts all further recording.
            # `price` is the surge trade's print — the price the stock just traded
            # at. Handed alongside the ask so x-wing can price the entry off the
            # higher of the two (the opening-snapshot ask is often stale when the
            # surge fires within a few seconds of subscribing).
            self._emit_buy_signal(price, self._last_ask, reason, "trade")

    def _emit_buy_signal(self, price, last_ask, reason, source):
        """Fire the buy-signal exactly once, shared by the trade path and the
        pre-trade path (Rule F). `price` is the trigger_print handed to x-wing
        (the trade print on the trade path, the ask on the pre-trade path);
        x-wing prices the entry off max(last_ask, trigger_print)."""
        if self.triggered:
            return
        self.triggered = True
        # v2.5 — open the post-trigger tail (driven mode: stop_at_trigger).
        if POST_TRIGGER_RECORD_SEC and self.stop_at_trigger:
            self._post_trigger_until = time.time() + POST_TRIGGER_RECORD_SEC
        self._log.warning(
            f"BUY-SIGNAL {self.symbol} (source={source}): handing last_ask={last_ask} "
            f"trigger_print={price} to x-wing"
        )
        cb = self.buy_signal_callback
        if cb is not None:
            try:
                cb(last_ask, price)
            except Exception as e:  # never let a downstream error kill the feed
                self._log.error(f"buy_signal_callback raised: {e}", exc_info=True)

    def _reconcile_trade_count(self, now_mono: float, new_count: int):
        """
        Inflate a provisional aggregated RTVolume burst into `delta` exact
        entries when tick 54 confirms the true trade count. The inflated
        synthetic entries inherit the aggressor flag of the parent burst.
        """
        if not self._tick54_seen:
            self._tick54_seen = True
            self._last_tws_trade_count = new_count
            return

        prev = self._last_tws_trade_count
        self._last_tws_trade_count = new_count
        if prev is None or new_count <= prev:
            return

        delta = new_count - prev
        if not self._pending_rt_events:
            return
        ts, rt_size, _rt_tot_vol, price, aggr = self._pending_rt_events.popleft()

        replicated = 0
        new_log: deque = deque()
        per_trade_size = max(int(rt_size / max(delta, 1)), 1) if rt_size else 0
        for entry in self._trade_log:
            e_ts, e_price, e_sz, e_kind, e_aggr = entry
            if e_kind == "provisional" and abs(e_ts - ts) < 1e-9 and replicated == 0:
                spread = 0.25
                step = spread / max(delta - 1, 1) if delta > 1 else 0.0
                for i in range(delta):
                    sub_ts = e_ts - (delta - 1 - i) * step
                    new_log.append((sub_ts, e_price, per_trade_size or e_sz, "exact", aggr))
                replicated = delta
            else:
                new_log.append(entry)
        if replicated:
            self._trade_log = new_log
            self._cum_trade_count += (delta - 1)

    def _compute_windows(self, now_mono: float) -> dict:
        """
        Two bounded scans:
          - one over _trade_log for trade-side metrics (M2/M3/M6/M8 + legacy)
          - one over _quote_log for NBBO drift / spread compression / quote
            churn (M4/M5/M7).
        """
        out = {}

        # ----- Trade-side accumulators -----
        counts = {w: 0 for w in WINDOWS}
        volumes = {w: 0 for w in WINDOWS}
        dollar_vols = {w: 0.0 for w in WINDOWS}
        buy_c = {w: 0 for w in WINDOWS}
        sell_c = {w: 0 for w in WINDOWS}
        mid_c = {w: 0 for w in WINDOWS}
        buy_v = {w: 0 for w in WINDOWS}
        sell_v = {w: 0 for w in WINDOWS}
        buy_d = {w: 0.0 for w in WINDOWS}
        sell_d = {w: 0.0 for w in WINDOWS}
        sizes = {w: [] for w in WINDOWS}
        max_sz = {w: 0 for w in WINDOWS}
        # M9 — large-trade (block) accumulators
        large_c = {w: 0 for w in WINDOWS}
        large_v = {w: 0 for w in WINDOWS}

        # M9 — "large" print threshold in shares (None disables the M9 columns)
        large_threshold = (
            self._large_trade_mult * self._hist_baseline_trade_size
            if self._has_trade_size_baseline else None
        )

        # Pre-extract trade timestamps for M8 clustering
        trades_5s_ts = []

        for ts, price, sz, _kind, aggr in self._trade_log:
            age = now_mono - ts
            if age <= 5:
                trades_5s_ts.append(ts)
            for w in WINDOWS:
                if age <= w:
                    counts[w] += 1
                    volumes[w] += sz
                    dollar_vols[w] += price * sz
                    sizes[w].append(sz)
                    if sz > max_sz[w]:
                        max_sz[w] = sz
                    if large_threshold is not None and sz >= large_threshold:
                        large_c[w] += 1
                        large_v[w] += sz
                    if aggr == "BUY":
                        buy_c[w] += 1
                        buy_v[w] += sz
                        buy_d[w] += price * sz
                    elif aggr == "SELL":
                        sell_c[w] += 1
                        sell_v[w] += sz
                        sell_d[w] += price * sz
                    elif aggr == "MID":
                        mid_c[w] += 1

        # ----- Emit trade-side per-window metrics -----
        # Rate divisor: the nominal window, or the part of it we have actually
        # been recording for (see RATE_NORMALIZE_BY_SESSION_AGE). Only the first
        # w seconds of a session differ; after that the window is full and the
        # two agree exactly.
        elapsed = None
        if RATE_NORMALIZE_BY_SESSION_AGE and self._session_start_mono is not None:
            elapsed = max(now_mono - self._session_start_mono, RATE_MIN_ELAPSED_SEC)

        for w in WINDOWS:
            c = counts[w]
            span = min(w, elapsed) if elapsed is not None else float(w)
            out[f"trades_in_{w}s"] = c
            out[f"volume_in_{w}s"] = volumes[w]
            out[f"dollar_vol_in_{w}s"] = dollar_vols[w]
            out[f"trade_rate_{w}s"] = c / span
            out[f"avg_iti_{w}s"] = (span / c) if c > 0 else None
            # M3 — dollar volume rate
            out[f"dollar_rate_{w}s"] = dollar_vols[w] / span
            # M2 — order-flow imbalance
            out[f"buy_count_{w}s"] = buy_c[w]
            out[f"sell_count_{w}s"] = sell_c[w]
            out[f"mid_count_{w}s"] = mid_c[w]
            out[f"buy_volume_{w}s"] = buy_v[w]
            out[f"sell_volume_{w}s"] = sell_v[w]
            out[f"signed_volume_{w}s"] = buy_v[w] - sell_v[w]
            out[f"signed_dollar_{w}s"] = buy_d[w] - sell_d[w]
            classified = buy_c[w] + sell_c[w]
            out[f"lift_offer_ratio_{w}s"] = (
                buy_c[w] / classified if classified > 0 else None
            )
            # M6 — trade-size distribution
            if sizes[w]:
                arr = sorted(sizes[w])
                n = len(arr)
                med = arr[n // 2] if n % 2 == 1 else (arr[n // 2 - 1] + arr[n // 2]) / 2
                out[f"median_size_{w}s"] = med
                out[f"mean_size_{w}s"] = sum(arr) / n
                out[f"max_size_{w}s"] = max_sz[w]
            else:
                out[f"median_size_{w}s"] = None
                out[f"mean_size_{w}s"] = None
                out[f"max_size_{w}s"] = None

            # M9 — trade-size vs historical baseline. All None when there is no
            # valid baseline (so the column schema is stable for sentinel/blank).
            if self._has_trade_size_baseline:
                base = self._hist_baseline_trade_size
                mean_sz = out[f"mean_size_{w}s"]
                out[f"size_ratio_{w}s"] = (mean_sz / base) if mean_sz is not None else None
                out[f"buy_size_ratio_{w}s"] = (
                    (buy_v[w] / buy_c[w]) / base if buy_c[w] > 0 else None
                )
                out[f"signed_size_ratio_{w}s"] = (
                    ((buy_v[w] - sell_v[w]) / classified) / base
                    if classified > 0 else None
                )
                out[f"large_trade_count_{w}s"] = large_c[w]
                out[f"large_trade_volume_frac_{w}s"] = (
                    large_v[w] / volumes[w] if volumes[w] > 0 else None
                )
            else:
                out[f"size_ratio_{w}s"] = None
                out[f"buy_size_ratio_{w}s"] = None
                out[f"signed_size_ratio_{w}s"] = None
                out[f"large_trade_count_{w}s"] = None
                out[f"large_trade_volume_frac_{w}s"] = None

        # M8 — max trades in any 200ms sub-window over the last 5s.
        # Two-pointer O(n) over the sorted-by-time 5s sub-list (deque is
        # already chronological).
        max_cluster = 0
        j = 0
        n5 = len(trades_5s_ts)
        for i, t in enumerate(trades_5s_ts):
            if j < i:
                j = i
            while j < n5 and (trades_5s_ts[j] - t) <= 0.2:
                j += 1
            cluster = j - i
            if cluster > max_cluster:
                max_cluster = cluster
        out["max_cluster_200ms_in_5s"] = max_cluster

        # Legacy baseline (kept for column-schema compatibility)
        out["hist_baseline_trade_rate"] = self._hist_baseline_trade_rate
        out["hist_baseline_avg_iti"] = self._hist_baseline_avg_iti
        r10 = out["trade_rate_10s"]
        rb = self._hist_baseline_trade_rate
        out["accel_1s_vs_10s"] = (out["trade_rate_1s"] / r10) if r10 > 0 else None
        out["accel_2s_vs_10s"] = (out["trade_rate_2s"] / r10) if r10 > 0 else None
        out["accel_5s_vs_10s"] = (out["trade_rate_5s"] / r10) if r10 > 0 else None
        out["accel_1s_vs_hist_baseline"] = out["trade_rate_1s"] / rb
        out["accel_2s_vs_hist_baseline"] = out["trade_rate_2s"] / rb
        out["accel_5s_vs_hist_baseline"] = out["trade_rate_5s"] / rb

        # M9 — trade-size baseline echo + cross-window size momentum.
        out["hist_baseline_trade_size"] = (
            self._hist_baseline_trade_size if self._has_trade_size_baseline else None
        )
        ms10 = out["mean_size_10s"]
        for n in (1, 2, 5):
            msn = out[f"mean_size_{n}s"]
            out[f"size_accel_{n}s_vs_10s"] = (
                (msn / ms10) if (msn is not None and ms10) else None
            )

        # ----- Quote-side scan: M4 / M5 / M7 -----
        b_now, a_now = self._last_bid, self._last_ask
        mid_now = self._midprice()
        sp_now = None
        if b_now is not None and a_now is not None and a_now > b_now and mid_now and mid_now > 0:
            sp_now = (a_now - b_now) / mid_now

        # For each window, find the OLDEST quote entry with age <= w.
        # Since _quote_log is chronological, do a single sweep keeping the
        # most recent qualifying "oldest" per window-cutoff threshold.
        # Simpler O(m * |WINDOWS|) loop is fine — m is bounded by 10s of
        # quote churn (a few hundred at most on a busy book).
        quotes_in_window: dict = {w: 0 for w in WINDOWS}
        oldest_in_window: dict = {w: None for w in WINDOWS}
        for entry in self._quote_log:
            q_ts, q_b, q_a, _qbs, _qas, q_sp = entry
            age = now_mono - q_ts
            for w in WINDOWS:
                if age <= w:
                    quotes_in_window[w] += 1
                    if oldest_in_window[w] is None:
                        oldest_in_window[w] = entry  # first hit = oldest in window

        for w in WINDOWS:
            out[f"quote_updates_{w}s"] = quotes_in_window[w]
            old = oldest_in_window[w]
            if old is None or b_now is None or a_now is None:
                out[f"bid_drift_{w}s_bp"] = None
                out[f"mid_velocity_{w}s_bp_per_s"] = None
                out[f"spread_pct_at_{w}s_ago"] = None
                out[f"spread_compression_{w}s"] = None
                continue
            _qts, q_b, q_a, _qbs, _qas, q_sp = old
            if q_b and q_b > 0:
                out[f"bid_drift_{w}s_bp"] = (b_now - q_b) / q_b * 10000.0
            else:
                out[f"bid_drift_{w}s_bp"] = None
            if q_b is not None and q_a is not None and (q_b + q_a) > 0 and mid_now is not None:
                q_mid = (q_b + q_a) / 2.0
                if q_mid > 0:
                    out[f"mid_velocity_{w}s_bp_per_s"] = (mid_now - q_mid) / q_mid * 10000.0 / w
                else:
                    out[f"mid_velocity_{w}s_bp_per_s"] = None
            else:
                out[f"mid_velocity_{w}s_bp_per_s"] = None
            out[f"spread_pct_at_{w}s_ago"] = q_sp
            if q_sp is not None and sp_now is not None and sp_now > 0:
                out[f"spread_compression_{w}s"] = q_sp / sp_now
            else:
                out[f"spread_compression_{w}s"] = None

        return out

    def _detect_surge(self, w: dict, iti: Optional[float]) -> tuple:
        """Thin wrapper kept for call-site compatibility. Delegates the whole
        decision to should_fire() — the single, swappable trigger rule."""
        return self.should_fire(w, iti)

    # ====================================================================== #
    # >>> TEMPORARY TRIGGER RULE — THE ONE PLACE TO SWAP <<<
    # ---------------------------------------------------------------------- #
    # This composite rule is a PLACEHOLDER. The strategic plan is to replace
    # it with a rule keyed off the ITI_baseline calculations (preserved in
    # _compute_windows / the hist_baseline_* + accel_* columns) once enough
    # output data has been collected to calibrate it. Replace the body of this
    # method only; everything upstream (recording, columns, buy-signal wiring)
    # stays the same. Return (should_fire: bool, reason: str).
    # ====================================================================== #
    def _bsln_over_prev_trd_gap(self) -> Optional[float]:
        """
        Live value of the bsln_over_prev_trd_gap column = hist baseline ITI /
        tape-staleness-at-subscribe (prev_trd_time_gap). Constant per session once
        prev_trd_time_gap is established (on the first accepted trade). Mirrors the
        formula in write_records_output, except it returns None when there is no
        valid ITI baseline (missing / <=0 / 44444 sentinel) so the trigger can skip
        it — the written column itself stays unguarded, matching the prior behavior.
        """
        if not self._has_iti_baseline:
            return None
        gap = self._prev_trd_time_gap
        base = self._hist_baseline_avg_iti
        if gap and gap > 0 and base is not None:
            return base / gap
        return None

    def _pretrade_gap(self) -> Optional[float]:
        """Pre-trade tape-staleness gap = (first-event wall clock) − LAST_TIMESTAMP,
        floored at PRETRADE_MIN_GAP_SEC. Available before any trade prints. None
        until both the first tick and LAST_TIMESTAMP (tick 45) have been seen."""
        if self._first_event_wall is None or self._last_timestamp_at_subscribe is None:
            return None
        return max(
            self._first_event_wall - self._last_timestamp_at_subscribe,
            PRETRADE_MIN_GAP_SEC,
        )

    def _snapshot_gap_pct(self) -> Optional[float]:
        """v2.3 — pre-trade move vs the previous close, in percent:

            (snapshot LAST − previous CLOSE) / previous CLOSE × 100

        The pre-trade rule's only other input, tape staleness, is direction-free;
        this is the direction. Returns None unless BOTH ticks have arrived, and a
        None result blocks the fire — see PRETRADE_MIN_GAP_VS_CLOSE_PCT for why
        substituting the ask for a missing last was measured and rejected."""
        last, close = self._snap_last, self._snap_close
        if last is None or close is None or close <= 0:
            return None
        return (last - close) / close * 100.0

    def _px_vs_vwap_pct(self) -> Optional[float]:
        """v2.4 — the last print's distance from the session's own VWAP, in
        percent. Both sides come from trades this session only, so it needs no
        external data and is defined from the second print onward (at the first,
        vwap == price and the value is exactly 0.0 — which is why the gate that
        reads it must sit strictly below zero; see SURGE_MIN_PX_VS_VWAP_PCT)."""
        px, vw = self._last_trade_price, self._vwap
        if px is None or not vw:
            return None
        return (px - vw) / vw * 100.0

    def _flow_x_baseline(self, w: dict, window: int = 5) -> Optional[float]:
        """v2.4 — dollar flow over the last `window` seconds as a MULTIPLE of what
        this name normally trades:

            dollar_rate_Ns / (hist_trade_rate x hist_trade_size x last_price)

        Numerator and denominator are both lot-denominated (window dollar rates
        are built from RTVolume sizes, which are LOTS), so the ~100x cancels and
        the result is a unitless "N times this name's own normal flow".

        Returns None without a valid ITI baseline or trade-size baseline — the
        same guard Rules B and C use. Rule D is the baseline-free fallback."""
        if not self._has_iti_baseline or not self._has_trade_size_baseline:
            return None
        dr = w.get(f"dollar_rate_{window}s")
        rate = self._hist_baseline_trade_rate
        size = self._hist_baseline_trade_size
        px = self._last_trade_price
        if dr is None or not rate or not size or not px:
            return None
        denom = rate * size * px
        return (dr / denom) if denom > 0 else None

    def _bslnITI_over_pretrade_gap(self) -> Optional[float]:
        """Rule F ratio = hist baseline ITI / pretrade_gap. Snapshot-only twin of
        bsln_over_prev_trd_gap: anchors the staleness gap to subscribe/now rather
        than the first new trade. Returns None when there is no valid ITI baseline
        (missing / <=0 / 44444 sentinel) or the pretrade gap isn't known yet."""
        if not self._has_iti_baseline:
            return None
        gap = self._pretrade_gap()
        base = self._hist_baseline_avg_iti
        if gap and gap > 0 and base is not None:
            return base / gap
        return None

    def _note_first_event(self, now_wall: float) -> None:
        """Stamp the wall-clock epoch of the very first tick of any kind, so Rule F
        can anchor its staleness gap before the first trade sets _subscribe_wall."""
        if self._first_event_wall is None:
            self._first_event_wall = now_wall

    def _maybe_fire_pretrade(self, now_mono: float, now_wall: float) -> None:
        """Rule F — fire off the reqMktData snapshot before the first trade prints.
        Requires: enabled, not already triggered, a valid ITI baseline, LAST_TIMESTAMP
        seen, and a usable ask (there is no trade print, so the ask prices the entry).
        Skips a locked/crossed snapshot. Constant per session once inputs are known,
        so it fires the instant all preconditions are met.

        v2.2: a fire that clears the threshold must ALSO pass the three veto
        keep-conditions (no print yet / gap cap / ratio cap — see the
        PRETRADE_VETO constant block). A vetoed fire writes one PRETRADE_VETO
        row and permanently disables the pretrade path for this session (every
        veto input is monotone or constant, so it can never un-veto); the
        trade-path rules keep watching."""
        if not self._pretrade_fire or self.triggered:
            return
        ask = self._last_ask
        if ask is None or ask <= 0:
            return
        # Skip a locked/crossed snapshot quote.
        if self._last_bid is not None and self._last_bid > 0 and ask < self._last_bid:
            return
        ratio = self._bslnITI_over_pretrade_gap()
        if ratio is None or ratio <= self._pretrade_threshold:
            return

        gap = self._pretrade_gap()
        if self._pretrade_veto:
            veto_why = None
            if self._last_trade_price is not None:
                veto_why = "trade already printed"
            elif gap is not None and gap > PRETRADE_MAX_GAP_SEC:
                veto_why = f"gap {gap:.3f}s > {PRETRADE_MAX_GAP_SEC}"
            elif ratio > PRETRADE_MAX_RATIO:
                veto_why = f"ratio {ratio:.2f} > {PRETRADE_MAX_RATIO:.0f}"
            if veto_why:
                self._log.warning(
                    f"\U0001F6D1 PRETRADE-VETO {self.symbol}: {veto_why} "
                    f"| ratio={ratio:.2f} gap={gap:.3f}s ask=${ask:.4f} "
                    f"— pretrade path disabled, trade-path rules still armed"
                )
                self.records.append({
                    "event_type": "PRETRADE_VETO",
                    "local_arrival_time": now_wall,
                    "local_arrival_iso": datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
                    "ask": ask,
                    "bid": self._last_bid,
                    "prev_trd_time_gap": gap,
                    "bsln_over_prev_trd_gap": ratio,
                    "hist_baseline_avg_iti": self._hist_baseline_avg_iti,
                    "surge_detected": False,
                    "surge_reason": f"pretrade veto: {veto_why}",
                })
                self._pretrade_fire = False
                return

        # v2.3 — DIRECTION gate. Staleness says the tape stopped; it never says
        # the tape stopped because something HAPPENED. Require the snapshot to
        # already sit >= PRETRADE_MIN_GAP_VS_CLOSE_PCT above the previous close.
        #
        # This DEFERS rather than disables, and the distinction matters: the veto
        # above is a permanent judgement on constant-per-session inputs, whereas
        # the gap moves — a later tick 4 can lift a name through the bar, and
        # that name should still be able to fire. So this returns without setting
        # _pretrade_fire = False and without writing a row. Placed AFTER the veto
        # block on purpose, so veto evaluation and its logging stay byte-identical
        # to 2.2 and the two PRETRADE_VETO series remain comparable.
        #
        # A missing gap (no tick 4 and/or no tick 9) blocks the fire — see the
        # constant's block: that is what stops BLNE and FFAI.
        if PRETRADE_MIN_GAP_VS_CLOSE_PCT is not None:
            snap_gap = self._snapshot_gap_pct()
            if snap_gap is None or snap_gap < PRETRADE_MIN_GAP_VS_CLOSE_PCT:
                if not self._pretrade_gap_gate_logged:
                    self._pretrade_gap_gate_logged = True
                    self._log.info(
                        f"pretrade gap-gate holding {self.symbol}: "
                        f"snapshot gap vs close = "
                        f"{'n/a' if snap_gap is None else f'{snap_gap:+.2f}%'} "
                        f"< {PRETRADE_MIN_GAP_VS_CLOSE_PCT:.1f}% "
                        f"(last={self._snap_last} close={self._snap_close}) "
                        f"— staleness ratio {ratio:.2f} alone is not direction"
                    )
                return

        reason = (
            f"bslnITI_over_pretrade_gap={ratio:.2f}>{self._pretrade_threshold:.0f}"
        )
        # Anchor downstream price math; there is no trade, so use the ask.
        if self._start_price is None:
            self._start_price = ask
        # Populate the staleness columns so the written CSV explains the fire. These
        # are only set on the pre-trade branch (no trade ever runs), so the recorded
        # prev_trd_time_gap semantics on the trade path are unchanged.
        if self._prev_trd_time_gap is None:
            self._prev_trd_time_gap = gap
        if self._subscribe_wall is None:
            self._subscribe_wall = self._first_event_wall
        self._log.warning(
            f"\U0001F680 PRETRADE-SURGE {self.symbol} @ ask=${ask:.4f} "
            f"| pretrade_gap={gap:.3f}s baselineITI={self._hist_baseline_avg_iti} "
            f"| {reason}"
        )
        self.records.append({
            "event_type": "PRETRADE_SURGE",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
            "ask": ask,
            "bid": self._last_bid,
            "prev_trd_time_gap": gap,
            "bsln_over_prev_trd_gap": ratio,
            "hist_baseline_avg_iti": self._hist_baseline_avg_iti,
            # v2.3 — the direction inputs that cleared the gap gate, so the
            # written table explains the fire without a replay.
            "snap_last": self._snap_last,
            "snap_close": self._snap_close,
            "snap_gap_vs_close_pct": self._snapshot_gap_pct(),
            "surge_detected": True,
            "surge_reason": reason,
        })
        self._emit_buy_signal(ask, ask, reason, "pretrade")

    def should_fire(self, w: dict, iti: Optional[float]) -> tuple:
        """
        Price-drop VETO, then OR of five trigger rules — any one alone fires the
        buy-signal. Re-calibrated 2026-07-07 on that day's _pos_/_neg_ labels with
        the June labeled set as regression (see
        mole-outputs/trigger-analysis-2026-07-07.md, re-runnable via
        analyze_trade_mole/analyze_trigger_rules.py): 8/8 + 20/20 positives fire,
        0/4 of the day's false positives survive, June false-positive names
        unchanged. Thresholds for C/A/D/E re-fit 2026-07-30 (v2.2 precision pass
        on the 07-09..07-30 window, verified at the OR level with 0 pos lost; see
        mole-outputs/surge-predictor-analysis-2026-07-30.md and the constants'
        comments). Numeric examples below keep their original calibration-era
        values; the constants are authoritative.

        VETO — price drop (wired in 2026-07-07): no rule may fire while the last
        trade price is more than surge_max_price_drop_pct (default 0.5%) BELOW the
        session-start price. A surge we want to buy does not print under where it
        started; AUID false-fired on a SELL print 2.3% below start. Re-evaluated
        every trade, so a dip only defers firing. (The threshold existed since v4
        but was never consulted by the quote-churn-era rules.)

        Rule B — Start-staleness (added 2026-06-14): fire when

            bsln_over_prev_trd_gap > BSLN_OVER_PREV_TRD_GAP_THRESHOLD

        i.e. the tape was very stale at subscribe relative to the stock's own
        historical cadence — the signature of a surge that began before we hit
        record. This ratio is constant per session and known from the first accepted
        trade, so Rule B can fire on the very first trade row. It is skipped when
        there is no valid ITI baseline (missing / <=0 / 44444 sentinel) so a failed
        baseline fetch cannot false-fire it.

        Rule C — last-3-trades ITI collapse (added 2026-06-14): fire when

            bslnITI_on_last3trades_avrgITI > BSLN_ITI_LAST3_THRESHOLD

        i.e. the hist baseline ITI is >100x the mean of the two most recent measured
        ITIs — the tape has accelerated sharply vs the stock's own cadence over the
        last couple of trades. Unlike Rule B (a fixed, start-of-session ratio), this
        updates every accepted trade, so it catches surges that develop after we hit
        record. Skipped when the value is None (first accepted trade) and — fixed
        2026-07-07 — when there is no valid ITI baseline: previously the ratio was
        still computed from the 44444 fetch-failed sentinel, so 44444/avg2 cleared
        the threshold on essentially any tape (AUID and DYAI false-fired through
        this). Guarded at the rule level like Rule B; the recorded column stays
        unguarded, matching bsln_over_prev_trd_gap.

        Rule A — quote-churn with flow behind it (churn active since 2026-06-11;
        dollar-rate gate added 2026-07-07): fire when the NBBO is being repainted
        rapidly AND real money is trading:

            quote_updates_{window}s >= surge_min_quote_updates
            AND dollar_rate_5s >= SURGE_A_MIN_DOLLAR_RATE_5S

        Ungated churn false-fired on single 1-share MID dust prints (BLIN with 26
        updates at $0.28/s, SYNX with 12 at $0.22/s); churn-confirmed positives
        trade >= $2/s. Rule A's history/rationale vs the old price-momentum rule is
        in mole-outputs/iti-threshold-analysis-2026-06-11.md.

        Rule D — absolute ITI collapse (added 2026-07-07): fire when

            measured_ITIsec <= SURGE_ITI_COLLAPSE_MAX_SEC
            AND trades_in_5s >= SURGE_ITI_COLLAPSE_MIN_TRADES_5S

        Baseline-free fallback for Rule C: sub-0.3s prints with >=3 trades in 5s is
        surge cadence for any name in our low-float universe, no history needed.
        Covers the 44444-sentinel names Rules B/C must skip (BIAF), and fires
        several confirmed positives faster than C (STAK 3.2s -> 0.25s).

        Rule E — dollar-flow burst confirmed by price (added 2026-07-07): fire when

            dollar_rate_5s >= SURGE_FLOW_MIN_DOLLAR_RATE_5S
            AND price_change_since_start >= SURGE_FLOW_MIN_PRICE_RISE_PCT

        The June analysis showed dollar_rate_5s >= 10 alone is a 0.00s-latency
        full-recall trigger but fires on flow with no direction; requiring the last
        price to already sit >= +1% above session start keeps only bought-up tape
        (rejects AUID at max +0.78% and SMCI, recovers SUGP which prints slowly but
        large). The dollar leg was cut 10 -> 3 on 2026-08-11 (PLAG: price leg passed
        at +3.06% on a 2-fill tape while the rate reached only 4.46) — it is inert on
        the labelled corpus, so the PRICE leg is now the whole of Rule E's direction
        AND its selectivity. See the constant's comment block for the coupling to
        DEDUP_EXCLUDE_TWIN_FROM_COUNTS.
        v2.3 note superseded: as of v2.4 Rule E ALSO requires flow_x_baseline_5s
        >= SURGE_E_MIN_FLOW_X_BASELINE, so the absolute dollar leg is no longer
        the whole story — the relative leg is what carries the selectivity now.

        --- v2.4 additions (all evaluated on the SAME tick, nothing is deferred) --

        GATE — VWAP direction (SURGE_MIN_PX_VS_VWAP_PCT): no trade-path rule may
        fire while the last print is more than this far below the session VWAP.
        Costs zero positives on the corpus. Sits beside the price-drop veto and,
        like it, only defers: a later print above VWAP re-opens every rule.
        v2.5: the VWAP is IBKR's whole-day VWAP, and the gate now holds only the
        rules listed in SURGE_VWAP_GATE_RULES (those without a price-rise leg:
        A, B, C, D, NF). E and N carry their own "price up since the session
        started" test. See the constant for LESL / LGHL / PHGE.

        Rule N — the ABSOLUTE-flow escape hatch. Rule E's relative leg makes a
        surge on a LIQUID name unreachable, and some of those are real (HIHO,
        historical ITI 0.4s, ran +5.31% off its session start). N keeps the old
        absolute shape but at four times the price bar:

            dollar_rate_5s >= SURGE_FLOW_MIN_DOLLAR_RATE_5S
            AND price_change_since_start >= SURGE_N_MIN_PRICE_RISE_PCT

        Rule NF — extreme relative flow with NO price confirmation:

            flow_x_baseline_5s >= SURGE_NF_MIN_FLOW_X_BASELINE
            AND dollar_rate_5s >= SURGE_FLOW_MIN_DOLLAR_RATE_5S

        It adds no recall — every positive it catches is caught by another rule
        — and exists only to reach them sooner. Placed LAST so it never takes a
        fire a cheaper rule would have taken at the same instant.
        """
        # VETO — last price too far below session start blocks every rule this row.
        sp = self._start_price
        lp = self._last_trade_price
        pc = None
        if sp and lp is not None:
            pc = (lp - sp) / sp
            if pc < -self._surge_max_price_drop_pct:
                return (False, "")

        # v2.4 GATE — direction vs the VWAP (IBKR's whole-day VWAP; see the
        # constant). Re-evaluated every trade, so it defers rather than disables.
        # v2.5: it holds only the rules in SURGE_VWAP_GATE_RULES — the ones with
        # no price-rise leg of their own. Like C's money gate it is part of each
        # rule's CONDITION, so a held C/D falls through to E/N on the same tick.
        # SURGE_VWAP_GATE_RULES = None is 2.4: an early return that holds all.
        _vw_held = ()
        if SURGE_MIN_PX_VS_VWAP_PCT is not None:
            _pv = self._px_vs_vwap_pct()
            if _pv is None or _pv < SURGE_MIN_PX_VS_VWAP_PCT:
                if SURGE_VWAP_GATE_RULES is None:
                    return (False, "")
                _vw_held = SURGE_VWAP_GATE_RULES

        # v2.4 — computed once per decision; read by Rules E and NF.
        flow_x_base = self._flow_x_baseline(w, 5)

        # Rule B — start-staleness (OR). Constant per session; may fire immediately.
        # v2.3: OFF by default (RULE_B_ENABLED) — 1 positive for 5 negatives, and
        # not separable by threshold because CVKD (20777) sits above SLND (13657).
        bsln_gap = self._bsln_over_prev_trd_gap() if RULE_B_ENABLED else None
        if (bsln_gap is not None and bsln_gap > BSLN_OVER_PREV_TRD_GAP_THRESHOLD
                and "B" not in _vw_held):
            return (
                True,
                f"bsln_over_prev_trd_gap={bsln_gap:.2f}>"
                f"{BSLN_OVER_PREV_TRD_GAP_THRESHOLD:.0f}",
            )

        # Rule C — last-3-trades ITI collapse (OR). Updates every accepted trade.
        # _has_iti_baseline guard (2026-07-07): never ratio against the 44444 sentinel.
        # v2.4: ANDed with a cumulative-dollar floor (SURGE_C_MIN_CUM_DOLLAR) —
        # cadence acceleration with no money behind it is not a surge. The gate
        # must be part of the CONDITION, not a filter on the result: a blocked C
        # has to fall through to D/E/N/NF on the SAME tick, and a post-hoc filter
        # that returns early silently suppresses them too.
        bsln_last3 = self._cur_bsln_on_last3 if (
            self._has_iti_baseline and RULE_C_ENABLED) else None
        _c_dollars_ok = (
            SURGE_C_MIN_CUM_DOLLAR is None
            or self._cum_dollar_volume >= SURGE_C_MIN_CUM_DOLLAR
        )
        if (bsln_last3 is not None and bsln_last3 > BSLN_ITI_LAST3_THRESHOLD
                and _c_dollars_ok and "C" not in _vw_held):
            return (
                True,
                f"bslnITI_on_last3trades_avrgITI={bsln_last3:.2f}>"
                f"{BSLN_ITI_LAST3_THRESHOLD:.0f}",
            )

        # Rule A — quote-churn backed by dollar flow (OR).
        # v2.3: OFF by default (RULE_A_ENABLED) — zero unique positives on the
        # corpus; IPW, its only positive, is caught the same instant by another leg.
        col = f"quote_updates_{self._surge_quote_window}s"
        qu = w.get(col) if RULE_A_ENABLED else None
        rate5 = w.get("dollar_rate_5s")
        if (
            qu is not None and qu >= self._surge_min_quote_updates
            and rate5 is not None and rate5 >= SURGE_A_MIN_DOLLAR_RATE_5S
            and "A" not in _vw_held
        ):
            return (
                True,
                f"{col}={qu:.0f}>={self._surge_min_quote_updates:.0f}"
                f"&$rate_5s={rate5:.1f}>={SURGE_A_MIN_DOLLAR_RATE_5S:g}",
            )

        # Rule D — absolute ITI collapse, baseline-free (OR).
        # v2.3: additionally capped to the first SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC
        # of the session. D's positives and negatives are identical on every
        # feature and differ only in WHEN they fire — see the constant's block.
        m_iti = self._cur_measured_iti
        trades5 = w.get("trades_in_5s")
        age = self._cur_session_age
        d_in_window = (
            SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC is None
            or (age is not None and age <= SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC)
        )
        # v2.4 — plus a SIZE leg (SURGE_D_MIN_VOLUME_5S, in lots): the burst has
        # to carry something. Without it D fires on three 1-lot prints.
        vol5 = w.get("volume_in_5s")
        d_size_ok = (
            SURGE_D_MIN_VOLUME_5S is None
            or (vol5 is not None and vol5 >= SURGE_D_MIN_VOLUME_5S)
        )
        if (
            d_in_window
            and m_iti is not None and m_iti <= SURGE_ITI_COLLAPSE_MAX_SEC
            and trades5 is not None and trades5 >= SURGE_ITI_COLLAPSE_MIN_TRADES_5S
            and d_size_ok
            and "D" not in _vw_held
        ):
            return (
                True,
                f"measured_ITI={m_iti:.3f}<={SURGE_ITI_COLLAPSE_MAX_SEC:g}"
                f"&trades_5s={trades5:.0f}>={SURGE_ITI_COLLAPSE_MIN_TRADES_5S}"
                + ("" if SURGE_D_MIN_VOLUME_5S is None
                   else f"&vol_5s={vol5:.0f}>={SURGE_D_MIN_VOLUME_5S:g}"),
            )

        # Rule E — dollar-flow burst confirmed by a rising price (OR).
        # v2.4: ALSO requires the flow to be abnormal FOR THIS NAME.
        if (
            rate5 is not None and rate5 >= SURGE_FLOW_MIN_DOLLAR_RATE_5S
            and pc is not None and pc >= SURGE_FLOW_MIN_PRICE_RISE_PCT
            and (SURGE_E_MIN_FLOW_X_BASELINE is None
                 or (flow_x_base is not None
                     and flow_x_base >= SURGE_E_MIN_FLOW_X_BASELINE))
            and "E" not in _vw_held
        ):
            return (
                True,
                f"$rate_5s={rate5:.1f}>={SURGE_FLOW_MIN_DOLLAR_RATE_5S:g}"
                f"&price_chg={pc * 100:+.2f}%>=+{SURGE_FLOW_MIN_PRICE_RISE_PCT * 100:g}%"
                + ("" if SURGE_E_MIN_FLOW_X_BASELINE is None
                   else f"&flow_x_base={flow_x_base:.1f}>="
                        f"{SURGE_E_MIN_FLOW_X_BASELINE:g}"),
            )

        # Rule N (v2.4) — the absolute-flow path, at 4x Rule E's price bar. This
        # is the only way a surge on a genuinely liquid name can still be seen.
        if (
            SURGE_N_MIN_PRICE_RISE_PCT is not None
            and rate5 is not None and rate5 >= SURGE_FLOW_MIN_DOLLAR_RATE_5S
            and pc is not None and pc >= SURGE_N_MIN_PRICE_RISE_PCT
            and "N" not in _vw_held
        ):
            return (
                True,
                f"bigmove_price_chg={pc * 100:+.2f}%>="
                f"+{SURGE_N_MIN_PRICE_RISE_PCT * 100:g}%"
                f"&$rate_5s={rate5:.1f}>={SURGE_FLOW_MIN_DOLLAR_RATE_5S:g}",
            )

        # Rule NF (v2.4) — extreme relative flow, no price confirmation. LAST on
        # purpose: it adds no recall, only earliness, so it must never pre-empt a
        # rule that would have fired on the same tick with a better reason.
        if (
            SURGE_NF_MIN_FLOW_X_BASELINE is not None
            and flow_x_base is not None
            and flow_x_base >= SURGE_NF_MIN_FLOW_X_BASELINE
            and rate5 is not None and rate5 >= SURGE_FLOW_MIN_DOLLAR_RATE_5S
            and "NF" not in _vw_held
        ):
            return (
                True,
                f"flowburst_x_base={flow_x_base:.0f}>="
                f"{SURGE_NF_MIN_FLOW_X_BASELINE:g}"
                f"&$rate_5s={rate5:.1f}>={SURGE_FLOW_MIN_DOLLAR_RATE_5S:g}",
            )

        return (False, "")

    # ------------------------------------------------------------------
    # Generic tick callbacks (from reqMktData)
    # ------------------------------------------------------------------

    # v2.5 — every tick handler is split into a thin public callback and a body.
    # Before the trigger the callback calls the body exactly as 2.4 ran it. After
    # the trigger it returns at once, as before — except during the post-trigger
    # tail (POST_TRIGGER_RECORD_SEC), when it runs the SAME body with the row
    # flagged post_trigger and wrapped so it can never raise: the clerk calls the
    # mole first on every tick and x-wing, mid-position, gets it only afterwards.
    def tickPrice(self, reqId, tickType, price, attrib):
        if self.triggered and self.stop_at_trigger:
            if self._tail_open():
                try:
                    self._tick_price(tickType, price, post=True)
                except Exception as e:
                    self._log.debug(f"post-trigger tickPrice failed: {e}")
            return
        self._tick_price(tickType, price)

    def _tick_price(self, tickType, price, post=False):
        now_mono = time.perf_counter()
        now_wall = time.time()
        self._note_first_event(now_wall)
        rec = {
            "event_type": "TICK_PRICE",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "price": price,
        }
        updated_nbbo = False
        # v2.3 — a tick 4/9 changes the pre-trade gap gate's inputs without
        # touching the NBBO, so it must also be able to re-open the pre-trade
        # check (see below). Without this a name whose ask is quoted once and
        # then goes quiet, but whose LAST ticks up through the bar, would be
        # held by the gate forever with nothing left to re-trigger evaluation.
        snap_moved = False
        if tickType == 1 and price > 0:
            self._last_bid = price
            updated_nbbo = True
        elif tickType == 2 and price > 0:
            self._last_ask = price
            updated_nbbo = True
        elif tickType == 37 and price > 0:
            self._mark_price = price
            rec["mark_price"] = price
        elif tickType == 75 and price > 0:
            self._last_rth_trade = price
            rec["last_rth_trade"] = price
        # v2.3 — snapshot LAST (4) and previous CLOSE (9), the two inputs to the
        # pre-trade direction gate. Recorded on the row as well as latched, so a
        # future recording can be re-scored on this gate offline.
        elif tickType == 4 and price > 0:
            self._snap_last = price
            rec["snap_last"] = price
            snap_moved = True
        elif tickType == 9 and price > 0:
            self._snap_close = price
            rec["snap_close"] = price
            snap_moved = True
        if updated_nbbo:
            self._append_quote_log(now_mono)
        if post:
            rec["post_trigger"] = True
        self.records.append(rec)
        # Rule F — an ask may have just landed; check the snapshot-only trigger.
        # v2.3 also re-checks on a snapshot last/close update, because those move
        # the gap gate. _maybe_fire_pretrade is idempotent and self-guarding (it
        # returns immediately unless every precondition holds), so the extra call
        # cannot fire anything the NBBO path would not have fired.
        if updated_nbbo or snap_moved:
            self._maybe_fire_pretrade(now_mono, now_wall)

    def tickSize(self, reqId, tickType, size):
        if self.triggered and self.stop_at_trigger:
            if self._tail_open():
                try:
                    self._tick_size(tickType, size, post=True)
                except Exception as e:
                    self._log.debug(f"post-trigger tickSize failed: {e}")
            return
        self._tick_size(tickType, size)

    def _tick_size(self, tickType, size, post=False):
        now_mono = time.perf_counter()
        self._note_first_event(time.time())
        sz = float(size) if size else 0.0
        rec = {
            "event_type": "TICK_SIZE",
            "local_arrival_time": time.time(),
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "size": sz,
        }
        updated_nbbo = False
        if tickType == 0:
            self._last_bid_size = sz
            updated_nbbo = True
        elif tickType == 3:
            self._last_ask_size = sz
            updated_nbbo = True
        elif tickType == 54:
            new_count = int(sz)
            self._tws_trade_count = new_count
            rec["tws_trade_count"] = new_count
            self._reconcile_trade_count(now_mono, new_count)
        elif tickType == 8:
            # v2.5 record-only: day volume (lots), the snapshot value included.
            self._note_day_volume(now_mono, sz)
        elif tickType == 89:
            self._shortable_shares = sz
            rec["shortable_shares"] = sz
        if updated_nbbo:
            self._append_quote_log(now_mono)
        if post:
            rec["post_trigger"] = True
        self.records.append(rec)

    def tickGeneric(self, reqId, tickType, value):
        if self.triggered and self.stop_at_trigger:
            if self._tail_open():
                try:
                    self._tick_generic(tickType, value, post=True)
                except Exception as e:
                    self._log.debug(f"post-trigger tickGeneric failed: {e}")
            return
        self._tick_generic(tickType, value)

    def _tick_generic(self, tickType, value, post=False):
        self._note_first_event(time.time())
        rec = {
            "event_type": "TICK_GENERIC",
            "local_arrival_time": time.time(),
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "value": value,
        }
        if tickType == 46:
            self._shortable = value
            rec["shortable"] = value
        elif tickType == 49:
            self._halted = int(value)
            rec["halted"] = self._halted
            if self._halted:
                self._log.warning(f"⛔ HALT detected: code={self._halted}")
        elif tickType == 55:
            self._tws_trade_rate = value
            rec["tws_trade_rate_per_min"] = value
        elif tickType == 56:
            self._tws_volume_rate = value
            rec["tws_volume_rate_per_min"] = value
        elif tickType == 54:
            # v2.5 — IBKR delivers the day trade count HERE, not via tickSize, so
            # tws_trade_count was empty in every recording before 2.5. Record it.
            # ⚠ Deliberately NOT calling _reconcile_trade_count: it has never run
            # in production, and wiring it would rewrite the trade log and move
            # every decision. That is a calibration of its own.
            try:
                self._tws_trade_count = int(value)
                rec["tws_trade_count"] = self._tws_trade_count
            except (TypeError, ValueError):
                pass
        if post:
            rec["post_trigger"] = True
        self.records.append(rec)

    def tickString(self, reqId, tickType, value):
        if self.triggered and self.stop_at_trigger:
            if self._tail_open():
                try:
                    self._tick_string(tickType, value, post=True)
                except Exception as e:
                    self._log.debug(f"post-trigger tickString failed: {e}")
            return
        self._tick_string(tickType, value)

    def _tick_string(self, tickType, value, post=False):
        now_mono = time.perf_counter()
        now_wall = time.time()
        self._note_first_event(now_wall)

        # 48 = RTVolume (tick 233), 77 = RTTradeVolume (tick 375)
        #
        # ⚠ `size` AND `totalVolume` BELOW ARE IN **LOTS**, NOT SHARES. DO NOT
        # "FIX" THIS — the truncation it causes is load-bearing. Measured
        # 2026-08-11 (mole-outputs/tickbytick-probe-2026-08-11.md):
        # rt_total_volume == reqMktData tick 8 exactly (28143 vs 28139 on SCKT,
        # ratio 1.000; tick 8 is IBKR's lot-denominated volume tick), and `size`
        # shares that unit (sum of message sizes == cumulative delta, ratio 1.00
        # on 5/5 probed symbols). The corpus agrees: of 4017 recorded TRADE rows
        # 93.2% of sizes are <100 and only 0.7% are multiples of 100, with modes
        # 1/2/5/10/20/50 — lots, not the multiples-of-100 clustering that real
        # share counts show.
        #
        # Two consequences, and they pull in OPPOSITE directions:
        #  1. _cum_volume and _cum_dollar_volume are ~100x understated in
        #     share/dollar terms, so SURGE_A_MIN_DOLLAR_RATE_5S = 60 is really
        #     ~$6000/s and SURGE_FLOW_MIN_DOLLAR_RATE_5S = 10 is ~$1000/s. This
        #     is COSMETIC: every threshold was fit on these same columns, so the
        #     operating point is self-consistent. Rescaling buys nothing and
        #     forces A and E to be re-derived. Fix the names/docs, not the code.
        #  2. `int()` truncation means a fill under 100 shares becomes 0 lots and
        #     is dropped by the `rt_size > 0` guard below. That silently discards
        #     ~64% of prints (but only ~8% of volume — which is why A and E barely
        #     care and the ITI rules B/C/D carry the whole exposure).
        #
        # (2) reads like a bug and is NOT. On four non-surging small caps in
        # pre-market, the unfiltered tick-by-tick tape spends 24-62% of its
        # intervals under Rule D's 0.28 s bar and reaches a 5 s count of 34
        # against a threshold of 3 — on a day with no news on any of them. The
        # lot truncation is acting as a noise filter that strips small,
        # numerous, uninformative prints; Rule D's 0.28 s was calibrated against
        # that filtered tape and depends on it. Feeding the full tape does not
        # surface surges earlier, it makes ordinary background trading look like
        # one. Compounding it: measured_ITI is computed on the LOCAL ARRIVAL
        # clock, and the unfiltered feed delivers in bursts (SCKT's median
        # arrival-ITI there was literally 0.000 s).
        if tickType in (48, 77) and value:
            try:
                parts = value.split(";")
                if len(parts) >= 6 and parts[0] and parts[1]:
                    rt_price = float(parts[0])
                    rt_size = int(float(parts[1]))   # LOTS — see the note above
                    rt_time_ms = int(parts[2]) if parts[2] else None
                    rt_total_volume = int(float(parts[3])) if parts[3] else None
                    vwap = float(parts[4]) if parts[4] else None
                    single_trade_flag = (parts[5].lower() == "true")
                    rt_source = "RTVolume" if tickType == 48 else "RTTradeVolume"
                    if vwap:
                        self._vwap = vwap
                    if rt_size > 0:
                        self._process_trade_event(
                            now_mono=now_mono,
                            now_wall=now_wall,
                            price=rt_price,
                            size=rt_size,
                            rt_time_ms=rt_time_ms,
                            single_trade_flag=single_trade_flag,
                            rt_total_volume=rt_total_volume,
                            rt_source=rt_source,
                        )
                        return
            except Exception as e:
                self._log.debug(f"RTVolume parse error on {value!r}: {e}")
            # v2.5 record-only: a tick-48 message that did not become a TRADE (a
            # sub-100-share print truncated to 0 lots, or a volume-only message
            # with no price) still carries IBKR's day volume in field 4. The
            # message itself is recorded as a TICK_STRING row below, as before.
            if tickType == 48:
                try:
                    parts = value.split(";")
                    if len(parts) >= 4 and parts[3]:
                        self._note_day_volume(now_mono, float(parts[3]))
                except (ValueError, AttributeError):
                    pass

        # 45 = LAST_TIMESTAMP: epoch (sec) of the last trade. Freeze the FIRST one
        # we receive — IBKR's subscription snapshot carries the last trade BEFORE
        # we connected, which seeds the pre-first-trade measured ITI.
        if tickType == 45 and value and self._last_timestamp_at_subscribe is None:
            try:
                self._last_timestamp_at_subscribe = float(int(value))
            except (ValueError, TypeError):
                self._log.debug(f"LAST_TIMESTAMP parse error on {value!r}")
            # Rule F — LAST_TIMESTAMP just completed the staleness gap; check it.
            self._maybe_fire_pretrade(now_mono, now_wall)

        rec = {
            "event_type": "TICK_STRING",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "value_str": value,
        }
        if post:
            rec["post_trigger"] = True
        self.records.append(rec)

    # ------------------------------------------------------------------
    # Derived quote metrics
    # ------------------------------------------------------------------

    def _spread(self):
        if self._last_bid and self._last_ask and self._last_ask > self._last_bid:
            return self._last_ask - self._last_bid
        return None

    def _spread_pct(self, ref_price):
        sp = self._spread()
        if sp and ref_price and ref_price > 0:
            return sp / ref_price
        return None

    def _midprice(self):
        if self._last_bid and self._last_ask and self._last_ask > self._last_bid:
            return (self._last_bid + self._last_ask) / 2
        return None

    def _microprice(self):
        if (self._last_bid and self._last_ask
                and self._last_bid_size is not None
                and self._last_ask_size is not None):
            total = self._last_bid_size + self._last_ask_size
            if total > 0:
                return (
                    self._last_bid * self._last_ask_size
                    + self._last_ask * self._last_bid_size
                ) / total
        return None


# ============================================================================
# Main
# ============================================================================

def build_column_order() -> list:
    cols = [
        "event_type",
        "local_arrival_time", "local_arrival_iso", "Time", "local_mono_time",
        "exchange_time_epoch", "exchange_time_iso",
        "tick_type", "rt_source",
        "price", "size", "aggr", "exchange", "special_conditions",
        "past_limit", "unreported",
        "bid", "ask", "bid_size", "ask_size",
        "spread", "spread_pct", "midprice", "microprice",
        "cum_volume", "cum_trade_count", "cum_dollar_volume", "vwap",
        "prev_trd_time_gap", "bsln_over_prev_trd_gap",
        "measured_ITIsec", "bslnITI_on_last3trades_avrgITI",
        "ratio_baseline_over_measured_ITI",
        "measured_ITIsec_collapse_ratio", "measured_ITIsec_collapse_diff",
        "measured_ITIsec_collapse_velocity",
        "ratio_baseline_collapse_ratio", "ratio_baseline_collapse_diff",
        "ratio_baseline_collapse_velocity",
        "inter_trade_time_sec", "session_age_sec",
    ]
    for w in WINDOWS:
        cols += [
            f"trades_in_{w}s",
            f"volume_in_{w}s",
            f"dollar_vol_in_{w}s",
            f"dollar_rate_{w}s",
            f"trade_rate_{w}s",
            f"avg_iti_{w}s",
            f"buy_count_{w}s", f"sell_count_{w}s", f"mid_count_{w}s",
            f"buy_volume_{w}s", f"sell_volume_{w}s",
            f"signed_volume_{w}s", f"signed_dollar_{w}s",
            f"lift_offer_ratio_{w}s",
            f"median_size_{w}s", f"mean_size_{w}s", f"max_size_{w}s",
            f"size_ratio_{w}s", f"buy_size_ratio_{w}s", f"signed_size_ratio_{w}s",
            f"large_trade_count_{w}s", f"large_trade_volume_frac_{w}s",
            f"bid_drift_{w}s_bp", f"mid_velocity_{w}s_bp_per_s",
            f"spread_pct_at_{w}s_ago", f"spread_compression_{w}s",
            f"quote_updates_{w}s",
        ]
    cols += [
        "max_cluster_200ms_in_5s",
        "hist_baseline_trade_rate", "hist_baseline_avg_iti",
        "hist_baseline_trade_size",
        "accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s",
        "accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline", "accel_5s_vs_hist_baseline",
        "size_accel_1s_vs_10s", "size_accel_2s_vs_10s", "size_accel_5s_vs_10s",
        "price_change_since_start_pct",
        "surge_detected", "surge_reason",
        "halted", "shortable", "shortable_shares",
        "mark_price", "last_rth_trade",
        "tws_trade_rate_per_min", "tws_volume_rate_per_min", "tws_trade_count",
        "rt_price", "rt_size", "rt_time_ms", "rt_total_volume",
        "rt_vwap", "rt_single_trade_flag",
        "value", "value_str",
    ]
    return cols


def main():
    parser = argparse.ArgumentParser(
        description="IBKR trade-frequency surge detector v4.1 — extended order-flow metrics",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol", required=True, help="Stock ticker, e.g. AAPL")
    parser.add_argument("--clientID", type=int, required=True,
                        help="IBKR API client ID (integer; must be unique per connection)")
    parser.add_argument("--lifeTime", required=True, type=parse_lifetime,
                        help="Collection duration in mm:ss format, e.g. 15:00")
    parser.add_argument("--output", required=True,
                        help="Output path - directory (auto-name) or full file path.")
    parser.add_argument("--baseline-iti", dest="baseline_avg_iti",
                        type=float, required=True,
                        help="Legacy: historical baseline avg inter-trade interval (sec). "
                             "Retained for hist_baseline_* column compatibility only; "
                             "the v4.1 composite surge rule does not depend on it.")
    parser.add_argument("--baseline-trade-size", dest="baseline_avg_trade_size",
                        type=float, required=True,
                        help="Historical baseline avg shares-per-trade (RTH/ETH already "
                             "selected upstream). Feeds the M9 trade-size columns. The "
                             "44444 pipeline sentinel (or any value <=0) disables them.")
    parser.add_argument("--large-trade-mult", type=float,
                        default=DEFAULT_LARGE_TRADE_MULT,
                        help="M9: a print counts as 'large' when size >= this multiple "
                             "of the trade-size baseline.")
    parser.add_argument("--surge-dollar-rate", type=float,
                        default=DEFAULT_SURGE_DOLLAR_RATE,
                        help="Composite rule: dollar_rate_5s threshold (USD/s).")
    parser.add_argument("--surge-max-spread-pct", type=float,
                        default=DEFAULT_SURGE_MAX_SPREAD_PCT,
                        help="Composite rule: maximum current spread_pct (e.g. 0.02 = 2%%).")
    parser.add_argument("--surge-min-lift-ratio", type=float,
                        default=DEFAULT_SURGE_MIN_LIFT_RATIO,
                        help="Composite rule: minimum lift_offer_ratio_5s (buyer-init %%).")
    parser.add_argument("--surge-min-bid-drift-bp", type=float,
                        default=DEFAULT_SURGE_MIN_BID_DRIFT_BP,
                        help="Composite rule: minimum bid_drift_5s_bp.")
    parser.add_argument("--surge-min-trades-5s", type=int,
                        default=DEFAULT_SURGE_MIN_TRADES_5S,
                        help="Composite rule: minimum trades_in_5s floor.")
    parser.add_argument("--surge-max-price-drop-pct", type=float,
                        default=DEFAULT_SURGE_MAX_PRICE_DROP_PCT,
                        help="Active VETO (since 2026-07-07): block every trigger rule "
                             "while the last price is more than this fraction below "
                             "the session-start price "
                             "(e.g. 0.005 = 0.5%%; 0 blocks any net drop).")
    parser.add_argument("--surge-min-quote-updates", type=int,
                        default=DEFAULT_SURGE_MIN_QUOTE_UPDATES,
                        help="Active rule: minimum quote_updates in the quote window "
                             "to fire (NBBO repaint surge).")
    parser.add_argument("--surge-quote-window", type=int,
                        default=DEFAULT_SURGE_QUOTE_WINDOW,
                        choices=WINDOWS,
                        help="Active rule: rolling window (s) for the quote_updates "
                             "trigger; must be one of the recorded windows.")
    parser.add_argument("--pretrade-staleness-threshold", type=float,
                        default=PRETRADE_STALENESS_THRESHOLD,
                        help="Rule F: fire off the snapshot before the first trade "
                             "when bslnITI_over_pretrade_gap exceeds this.")
    parser.add_argument("--no-pretrade-staleness-fire", dest="pretrade_staleness_fire",
                        action="store_false",
                        help="Disable Rule F (pre-trade snapshot staleness fire). "
                             "Enabled by default.")
    parser.set_defaults(pretrade_staleness_fire=PRETRADE_STALENESS_FIRE)
    parser.add_argument("--no-pretrade-veto", dest="pretrade_veto",
                        action="store_false",
                        help="Disable the v2.2 Rule F veto (no-print / gap / ratio "
                             "keep-conditions) and revert to 2.1 fire behaviour. "
                             "Enabled by default.")
    parser.set_defaults(pretrade_veto=PRETRADE_VETO)
    parser.add_argument("--keep-recording-after-trigger", action="store_true",
                        help="Do NOT stop recording when the surge/buy-signal fires "
                             "(default: stop at the trigger row). Standalone only.")
    parser.add_argument("--host", default=n2_config.get(_CFG, "IBKR_GATEWAY_HOST", "127.0.0.1"),
                        help="IBKR Gateway/TWS host (default from n2_config_file.txt)")
    parser.add_argument("--port", type=int,
                        default=n2_config.get_int(_CFG, "IBKR_GATEWAY_PORT", 4001),
                        help="IBKR port (7497=paper TWS, 7496=live TWS, "
                             "4002=paper Gateway, 4001=live Gateway; "
                             "default from n2_config_file.txt)")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-dir", default=None,
                        help="Directory for log file output. "
                             "Auto-name: SYMBOL_YYYY-MM-DD_HH-MM.log.")
    args = parser.parse_args()

    if args.baseline_avg_iti <= 0:
        parser.error(
            f"--baseline-iti must be > 0 (seconds), got {args.baseline_avg_iti}"
        )

    symbol = args.symbol.upper()

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.log_dir:
        os.makedirs(args.log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        log_path = os.path.join(args.log_dir, f"{symbol}_{ts}.log")
        fh = logging.FileHandler(log_path)
        fh.setLevel(getattr(logging, args.loglevel))
        fh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        logging.getLogger().addHandler(fh)
        logging.info(f"Log file: {log_path}")

    output_path = resolve_output_path(args.output, symbol)
    logging.info(f"Symbol: {symbol}")
    logging.info(f"ClientID: {args.clientID}")
    logging.info(f"Lifetime: {args.lifeTime}s")
    logging.info(f"Output: {output_path}")
    logging.info(
        f"Trade-size baseline: {args.baseline_avg_trade_size} "
        f"(large_trade_mult={args.large_trade_mult})"
    )
    logging.info(
        f"Active trigger (quote-churn): "
        f"quote_updates_{args.surge_quote_window}s>={args.surge_min_quote_updates}"
    )

    app = IBKRSurgeApp(
        symbol,
        baseline_avg_iti=args.baseline_avg_iti,
        surge_dollar_rate=args.surge_dollar_rate,
        surge_max_spread_pct=args.surge_max_spread_pct,
        surge_min_lift_ratio=args.surge_min_lift_ratio,
        surge_min_bid_drift_bp=args.surge_min_bid_drift_bp,
        surge_min_trades_5s=args.surge_min_trades_5s,
        surge_max_price_drop_pct=args.surge_max_price_drop_pct,
        surge_min_quote_updates=args.surge_min_quote_updates,
        surge_quote_window=args.surge_quote_window,
        baseline_avg_trade_size=args.baseline_avg_trade_size,
        large_trade_mult=args.large_trade_mult,
        pretrade_staleness_fire=args.pretrade_staleness_fire,
        pretrade_veto=args.pretrade_veto,
        pretrade_staleness_threshold=args.pretrade_staleness_threshold,
    )
    app.stop_at_trigger = not args.keep_recording_after_trigger
    logging.info(f"Connecting to {args.host}:{args.port} ...")
    app.connect(args.host, args.port, clientId=args.clientID)

    api_thread = threading.Thread(target=app.run, daemon=True, name="ibapi-run")
    api_thread.start()

    if not app.connected_event.wait(timeout=10):
        logging.error("Failed to connect to IBKR within 10s. Is TWS/Gateway running?")
        app.disconnect()
        sys.exit(1)

    delay_sec, target_et = _compute_collection_start_delay()
    if delay_sec > 0:
        logging.info(
            f"Closed-market launch: idling {delay_sec:.0f}s until "
            f"{target_et.isoformat()} before subscribing."
        )
        deadline = time.time() + delay_sec
        try:
            while time.time() < deadline and not app.disconnected_flag:
                remaining_before = deadline - time.time()
                time.sleep(min(60.0, max(0.0, remaining_before)))
                remaining = int(deadline - time.time())
                if remaining > 0 and remaining % 300 == 0:
                    logging.info(f"[idle] {remaining}s until 04:00 ET subscribe")
        except KeyboardInterrupt:
            logging.info("Interrupted during pre-04:00 wait. Disconnecting.")
            app.disconnect()
            api_thread.join(timeout=5)
            sys.exit(0)
        if app.disconnected_flag:
            logging.error("IBKR connection dropped during pre-04:00 wait. Aborting.")
            app.disconnect()
            api_thread.join(timeout=5)
            sys.exit(1)
        logging.info("Reached 04:00 ET - subscribing to market data now.")

    contract = make_contract(symbol)
    try:
        logging.info(f"Requesting mktData generic ticks {GENERIC_TICKS} (id={app.req_id_mkt})")
        app.reqMktData(app.req_id_mkt, contract, GENERIC_TICKS, False, False, [])

        end_time = time.time() + args.lifeTime
        logging.info(f"Collecting for {args.lifeTime}s. Ctrl+C to stop early.")

        while time.time() < end_time and not app.disconnected_flag:
            time.sleep(1)
            # v2.5: after the trigger, keep collecting through the post-trigger
            # tail (POST_TRIGGER_RECORD_SEC), then stop as before.
            if app.triggered and app.stop_at_trigger and not app._tail_open():
                logging.info("Buy-signal fired; stopping collection "
                             + ("after the post-trigger tail." if app._post_trigger_until
                                else "at the trigger row."))
                break
            remaining = int(end_time - time.time())
            if remaining > 0 and remaining % 30 == 0:
                logging.info(
                    f"[{remaining:>4}s left] events={len(app.records)} "
                    f"trades={app._cum_trade_count} vol={app._cum_volume} "
                    f"vwap={app._vwap} halted={app._halted}"
                )
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    finally:
        logging.info("Cancelling subscriptions...")
        try:
            app.cancelMktData(app.req_id_mkt)
        except Exception as e:
            logging.debug(f"Cancel error: {e}")
        time.sleep(0.5)

        logging.info("Disconnecting...")
        app.disconnect()
        api_thread.join(timeout=5)
        if api_thread.is_alive():
            logging.warning("API thread did not stop within 5s after disconnect.")

    # v2.5 — stamp how the post-trigger tail ended (no-op without a trigger).
    app.end_post_trigger("complete" if app._post_trigger_until
                         and time.time() > app._post_trigger_until else "session_end:standalone")
    write_records_output(app, output_path)


def driven_output_path(output_dir: str, symbol: str) -> str:
    """Clerk-mode CSV path: trade-mole-table_<symbol>_<date>_<time>.csv .
    <symbol> is included (beyond the bare date_time spec) so concurrent duos
    never collide on the same file."""
    os.makedirs(output_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(output_dir, f"trade-mole-table_{symbol}_{ts}.csv")


def write_records_output(app: "IBKRSurgeApp", output_path: str,
                         log: Optional[logging.Logger] = None) -> Optional[str]:
    """Build the DataFrame from app.records and write it to output_path.
    Shared by standalone main() and the clerk's driven teardown. Returns the
    written path, or None when there was nothing to write."""
    log = log or app._log
    # v2.5 — snapshot first: with the post-trigger tail the ibapi thread may
    # still be appending while the clerk writes the file from another thread.
    # Copying a list is one C call under the GIL, so the snapshot is consistent.
    records = list(app.records)
    if not records:
        log.warning("No events captured; no output file written.")
        return None

    log.info(f"Building DataFrame from {len(records)} events...")
    df = pd.DataFrame(records)

    if "local_arrival_iso" in df.columns:
        df["Time"] = df["local_arrival_iso"].str.slice(11, 23)

    # prev_trd_time_gap is a single value (recording-start − LAST_TIMESTAMP at
    # subscribe); emit it as a constant on every row, including pre-first-trade rows.
    df["prev_trd_time_gap"] = app._prev_trd_time_gap

    # bsln_over_prev_trd_gap = hist baseline ITI / prev_trd_time_gap: start-staleness
    # in units of the stock's own trade cadence (higher = surge). Constant per file;
    # the 44444 baseline sentinel is left unguarded, matching ratio_baseline_over_measured_ITI.
    _gap = app._prev_trd_time_gap
    _base = app._hist_baseline_avg_iti
    df["bsln_over_prev_trd_gap"] = (
        (_base / _gap) if (_gap and _gap > 0 and _base is not None) else None
    )

    preferred = build_column_order()
    existing = [c for c in preferred if c in df.columns]
    extras = [c for c in df.columns if c not in existing]
    df = df[existing + extras]

    trades_df = df[df["event_type"] == "TRADE"] if "event_type" in df.columns else pd.DataFrame()
    n_surges = int((df["surge_detected"] == True).sum()) if "surge_detected" in df.columns else 0

    log.info(
        f"Summary: total_events={len(df)} trades={len(trades_df)} "
        f"surge_events={n_surges} cum_vol={app._cum_volume} "
        f"cum_trades={app._cum_trade_count}"
    )

    # v2.5 — the clerk now writes the same path twice (at the trigger, then at
    # the end of the post-trigger tail). Write a sibling and rename it over the
    # target, so a reader (daily-board) never sees a half-written file.
    tmp_path = output_path + ".tmp"
    df.to_csv(tmp_path, index=False, float_format="%.9f")
    os.replace(tmp_path, output_path)
    log.info(f"Wrote {len(df)} rows x {len(df.columns)} cols -> {output_path}")
    return output_path


if __name__ == "__main__":
    main()
