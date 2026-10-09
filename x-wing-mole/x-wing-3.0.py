#!/usr/bin/env python
"""
x-wing-3.0.py - yield-laddered trailing-stop trader for IBKR (ibapi).

Trades ONE symbol (long side). Two run modes:

  * STANDALONE (``python x-wing-3.0.py --symbol ...``): opens its own ibapi
    connection (a single client doing BOTH market data and orders), waits for
    the in-process buy-signal helper to fire the entry, then manages the
    protective stop until the position is closed.

  * DRIVEN (imported by clerk-1.0.py): the clerk injects ONE shared ibapi
    client. x-wing does NOT subscribe to market data itself — the clerk fans
    the shared tick stream into ``ctl.update_quote(...)`` and the trade-mole
    detector calls ``ctl.on_buy_signal(last_ask)`` to fire the entry.

Design:
  1. On buy-signal, place a BUY LMT for floor(CAPITAL / entry) shares, priced
     at ref * (1 + 2.5%) where ref = max(ask, trigger-print, last), and then
     TRACK the live ask with that same order (see "New in 3.0" below).
  2. As it (partially) fills, arm a protective SELL STP LMT for the filled qty.
  3. Every loop tick, sample the L1 mid, compute yield vs the frozen original
     average fill, look up the limits table (banded) -> Trigger(%)/Limit(%) and
     REPLACE (never cancel) the resting protective order with the ratcheted
     aux/lmt. The stop only moves UP.
  4. Synthetic trigger: when mid <= aux, REPLACE the order into a SELL LMT at
     the held table limit to force the exit (ETH-safe). If it gaps below, it
     rests; do not end the script.
  5. When the protective sell fully fills, the position is closed: signal done
     and stop. NO re-entry (removed in 2.0).

New in 3.0 — the entry limit TRACKS THE ASK instead of being priced once.
2.1 priced the entry at signal time and left it resting GTC until it filled or
the 10 s timeout cancelled it. Two measured problems followed from that:

  * A static limit is a STALE CEILING that IBKR fills us into. Of 248 filled
    entries, all 20 that took >10 s paid 100% of the pad: IBKR's price-cap
    machinery ratchets a resting over-market limit up toward itself over 3-90 s.
    That is the population rejected-orders-findings.txt shows losing 9-13%
    (BAOS -13.40%, NIVF -11.54%, GWH -9.35%). Immediate fills consume ~0% of the
    pad (70.2% fill at or below the reference ask).
  * The last_close * 1.30 clamp priced 7 of 317 entries BELOW the market
    (GLE -24.8%, UZX -19.8%, NUWE -43.2%, BYAH -41.7%, ICCM -70.4%, OBAI -17.8%,
    BNRG -12.1%) - structurally unfillable, and it forfeited GLE's +52.6% T47
    re-entry.

3.0 re-prices the SAME orderId in place (never cancel/replace) so the limit
holds ~2.5% above the LIVE ask, in both directions:

  * UP, for ENTRY_TRACK_UP_WINDOW_SEC only (3 s). Bounded by a ceiling frozen at
    placement: trigger_ref * (1 + ENTRY_TRACK_MAX_PCT/100) = +6%. This replaces
    the last_close clamp entirely.
  * DOWN, with hysteresis, for the WHOLE pending life. This is the half the
    evidence actually supports: it is what stops the limit becoming a stale
    ceiling. Upward aggression is risky and gets time-boxed; the downward
    ratchet is protective and must not be.

Also new in 3.0: usePriceMgmtAlgo is OFF on the entry (2.1 had it True). It
prevents neither the 2161 soft cap (2161 predates 2.1 - seen 07-20 and 07-22)
nor the Part-B silent Inactive (NCRA 2026-07-29 died with the algo attached);
what it does do is let IBKR re-price the order internally, so the working price
is not the one we set and our modifies contend with it. A tracking limit needs
deterministic control. ENTRY_USE_PRICE_MGMT_ALGO is kept as a one-line revert.

Removed vs 1.0: re-entry loop, --exit-cross-percent second-buy logic, the
SIGUSR1/--prewarm arming path. Never uses reqTickByTickData (3-request cap) or
reqGlobalCancel (would nuke other instances' orders on the shared account).
"""

import argparse
import csv
import logging
import math
import os
import re
import signal
import statistics
import sys
import threading
import time
from collections import deque
from datetime import datetime, time as dtime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - fallback if tzdata missing
    ET = None

try:
    import pandas as pd
except ImportError:
    print("Error: pandas not found. Install with: pip install pandas")
    sys.exit(1)

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
except ImportError:
    print("Error: ibapi module not found. Install with: pip install ibapi")
    sys.exit(1)

logger = logging.getLogger("x-wing")

# --------------------------------------------------------------------------- #
# Centralized per-user config (scripts/config/n2_config_file.txt). x-wing runs
# both standalone and imported by clerk-1.1.py via spec_from_file_location, so
# __file__ is always the real path and these resolve relative to the repo.
# --------------------------------------------------------------------------- #
import importlib.util as _ilu
_HERE = Path(__file__).resolve().parent
_SCRIPTS_DIR = _HERE.parent
_spec = _ilu.spec_from_file_location("n2_config", _SCRIPTS_DIR / "config" / "n2_config.py")
n2_config = _ilu.module_from_spec(_spec)
_spec.loader.exec_module(n2_config)
_CFG = n2_config.load_config()

# --------------------------------------------------------------------------- #
# Configuration (NOT received from the clerk).
# These are the x-wing-side defaults the clerk does NOT override. Only `symbol`
# (ticker) and `last_close_price` (lastDailyClose) come from the orch-trigger.
# Per-user values (ACCOUNT, host, port) come from the central config above; the
# rest are edited here. Standalone --flags fall back to these same values.
# --------------------------------------------------------------------------- #
CAPITAL                       = 5.00            # dollar budget per trade
ACCOUNT                       = n2_config.get(_CFG, "IBKR_ACCOUNT", "UNCONFIGURED_IBKR_ACCOUNT")  # set IBKR_ACCOUNT in config/n2_config_file.txt; sentinel makes orders fail loudly if unset
INPUT_LIMITS_TABLE            = str(_HERE / "yield-triggerLimit-data" / "v8-input.tsv")
LOG_DIR                       = str(_HERE / "xwing-logs")                          # xwing_<symbol>_<date>_<time>.log
PRICE_ACTION_TABLE_DIR        = str(_HERE / "xwing_tables")  # xwing-table_<symbol>_<date>_<time>.tsv
LIFETIME                      = "04:00:00"         # hh:mm:ss or mm:ss (max 12h); starts at the buy-signal
MAX_LIMIT_ENTRY_PERCENT_PRICE = 2.5             # entry = ref * (1 + pct/100); ALSO the tracking offset
# 2.9 -> 2.5 in 3.0. The required-offset CDF over the 199 entries that filled
# within 1 s (fill price vs the entry reference) has a sharp knee here:
#   +1.0% -> 82.9%   +1.5% -> 85.4%   +2.0% -> 91.0%
#   +2.5% -> 96.0%   +2.9% -> 97.0%   +4.0% -> 99.0%
# Everything between 1.5% and 2.5% is load-bearing; above 2.5% it is ~1 pt per
# unit. The knee is set by FEED STALENESS, not policy: the ask we price against
# is 0.3-0.9 s old (0.507 s median exchange->local lag + reqMktData's 250 ms
# aggregation) and the p90 ask move in 1 s is +3.74%. Trimming below the knee
# does NOT buy anything back either - on the 12 caps pairable with an entry,
# IBKR's 2161 band ceiling sat at cap/ask 0.985-1.030 (essentially ON the
# offer), so even a +1.5% limit would have been capped in 11 of 12.
# Percent, not cents or ticks: across these price buckets the spread in CENTS
# varies 27x (0.40c median under $0.50 vs 11.0c at $2-5) while the spread in
# PERCENT varies only 1.9x (1.81% vs 3.55%), and 45% of entries are sub-$1
# where minTick is $0.0001 (2 ticks there is 0.03% - inert).
# Stale-ask entry pricing (added 2026-07-22). The entry reference is priced off the
# HIGHER of (ask, trigger-print, last) — not the ask alone — because the ask in the
# opening market-data snapshot is often stale when the surge fired within a few
# seconds of subscribing (see the 227-run analysis: staleness only occurs when
# arm->surge < 5s, and concentrates in the bsln_over_prev_trd_gap rule). DAIC
# 2026-07-22: ask 1.39 was already 2.8% below a 1.43 print, so ask*1.04=1.45 never
# went marketable (real ask was 1.47) and filled 9.8s later on the reversal at a loss.
# When the ask is detectably stale (more than STALE_QUOTE_THRESHOLD_PCT below the
# trigger print) the pad widens to STALE_QUOTE_ENTRY_PERCENT_PRICE: on the 14 stale
# runs with price tables, max(ask,print)*1.04 rescued 5/14 vs 11/14 at *1.06. Runs
# with a healthy ask (193 of 210) keep ref=ask and the 4% pad and are unaffected.
STALE_QUOTE_ENTRY_PERCENT_PRICE = 4.0           # pad used when the ask is stale
STALE_QUOTE_THRESHOLD_PCT       = 1.0           # ask < print*(1-pct/100) => stale
# ^ 3.0: the stale pad applies to the OPENING SHOT ONLY. Once the entry is
# working, the tracker re-prices off the live ask every ~250 ms, which
# self-corrects a stale opening snapshot within one interval.
# MAX_CAP_ENTRY_PERCENT (was 30.0 = last_close * 1.30) is REMOVED in 3.0. It
# priced 7 of 317 entries BELOW the market — structurally unfillable — because
# it anchors on a price the name has already left (GLE -24.8%, UZX -19.8%,
# NUWE -43.2%, BYAH -41.7%, ICCM -70.4%, OBAI -17.8%, BNRG -12.1%), and it is
# what made T47 re-entries impossible on any name that had run (GLE's +52.6%
# winner). ENTRY_TRACK_MAX_PCT replaces it with an anchor on the trigger
# reference — the price we actually decided to buy at, which is the
# economically meaningful bound. None disables the old clamp entirely;
# --max-cap-entry-percent still exists for standalone experiments.
MAX_CAP_ENTRY_PERCENT         = None            # REMOVED in 3.0 -> ENTRY_TRACK_MAX_PCT
# IBKR Price Management Algo on the BUY entry. TRUE in 2.1, FALSE in 3.0. It does
# NOT prevent the 2161 soft cap (2161 predates 2.1 — seen 07-20 and 07-22) and it
# does NOT prevent the Part-B silent Inactive (NCRA 2026-07-29 died with the algo
# attached; see rejected-orders-findings.txt). What it DOES do is let IBKR
# re-price the order internally, so the working price is not the one we set and
# our modifies contend with it — unacceptable for a tracking limit. Regression
# risk of turning it off: an over-aggressive limit can hard-reject (202) instead
# of being capped-and-worked; the 2.5% offset and the 6% ceiling (vs IBKR's
# ~+9.8% 202 wall) make that unlikely. Set back to True to revert.
ENTRY_USE_PRICE_MGMT_ALGO     = False

# --------------------------------------------------------------------------- #
# Entry ask-tracking (new in 3.0)
# --------------------------------------------------------------------------- #
# The entry limit is re-priced IN PLACE on the same orderId (never
# cancel/replace) so it holds MAX_LIMIT_ENTRY_PERCENT_PRICE above the LIVE ask.
# Reusing the id is also a hard T47 requirement: T47._prior_entry_ids keys on
# ctl.active_buy_id and its ctl.orders[...] lookup assumes role=="entry" is one
# id for the life of the run.
ENTRY_TRACK_ENABLED           = True    # False => single-shot entry, i.e. 2.1 behaviour
# Hard ceiling on the limit, frozen at placement as trigger_ref * (1 + pct/100).
# 6.0: of 317 entries only 14 (4.4%) genuinely failed because the ask outran the
# pad (they needed +3.50% .. +25.0%), and 6% converts about half. Full-population
# fill-rate simulation runs 75.2% at 2.9% -> 84.4% at 6% -> 85.1% at 9%, i.e.
# flat past 6%. IBKR also HARD-cancels with code 202 at ~+9.80% over ITS OWN
# reference (4 observed, MHH/MTVA), and that reference can be a stale last_close
# (PFSA: cap/ask 0.859), so 6% over OUR reference is the last comfortable stop.
ENTRY_TRACK_MAX_PCT           = 6.0
# The UPWARD follow is time-boxed; the DOWNWARD ratchet is not. 84.6% of fills
# land within 3 s and the deep-tail losers are all >=7 s (BAOS 7.2 s, NIVF 9.3 s,
# GWH 23.0 s), so buying more aggressively after 3 s is exactly the behaviour the
# 30->10 s timeout cut was made to remove. The down ratchet stays live for the
# whole pending life because it is what prevents the stale-ceiling fill.
ENTRY_TRACK_UP_WINDOW_SEC     = 3.0
# ...with ONE exception. The window is a brake on chasing a WORKING order, but a
# 2161-capped entry was never working: IBKR holds it below the offer, so it can
# neither fill nor "chase" anything while the clock runs out on it. MGRX
# 2026-08-13 is the case — capped to 0.5764 against a 0.5770 ask, ask then flat
# for 4.5 s (tracker idle, n=0), and the FIRST leg up (0.5770 -> 0.5970) arrived
# 4.54 s in, so the lazy window check froze UP on exactly the move that would
# have filled us. Aborted at the 10 s timeout; the stock ran +27.3%.
# REBN the SAME DAY is the control: same cap (1.70 -> 1.63 vs a 1.64 ask), but
# its leg up landed at 1.775 s, INSIDE the window -> CHASE_CEIL 1.70 -> 1.75 ->
# filled 1.72 in 139 ms. Same fault, opposite outcome, decided only by timing.
# REBN filling ABOVE its original 1.70 limit is also the proof that raising our
# limit is what produces the fill while capped: IBKR works the order up "to a
# more aggressive price still within your specified limit price" -- our limit is
# the envelope it is allowed to work inside.
# So: exactly ONE post-window UP move, armed only while IBKR is demonstrably
# holding us under the ask. Runs that were never capped are unaffected.
ENTRY_CAP_RESCUE_ENABLED      = True
# 10-06 (NMTC 10-05): the rescue's room test used the dead band (>= 1 tick),
# so with limit 1.26 and bound floor_to_tick(1.20*1.06)=1.27 it needed
# 1.27 > 1.27 and never armed. The one-shot rescue is already single-use, so
# the flicker dead band buys nothing there: arm on ONE real tick of room.
ENTRY_CAP_RESCUE_ONE_TICK     = True
ENTRY_TRACK_MIN_OFFSET_TICKS  = 1       # floor under the percentage offset
# Lower the limit only once it is more than mult * offset above the live ask, so
# ordinary quote flicker costs nothing and we spend ~1 revision per real leg down.
ENTRY_TRACK_DOWN_HYST_MULT    = 2.0
ENTRY_TRACK_MIN_INTERVAL_SEC  = 0.25    # >= the reqMktData 250 ms aggregation cadence
ENTRY_TRACK_MIN_DELTA_TICKS   = 1       # dead band: move at least this...
ENTRY_TRACK_MIN_DELTA_FRAC    = 0.25    # ...or this fraction of the offset, whichever is larger
ENTRY_TRACK_MAX_MODIFIES      = 20      # per run, hard budget
# A rejected modification can surface as a transient "Cancelled" on an order that
# is in fact still live. Defer that abort by one grace period and take it only if
# no live status follows. Scoped to Cancelled/ApiCancelled: a Part-B "Inactive"
# still aborts immediately so its measurement series stays continuous.
ENTRY_TRACK_STATUS_GRACE_SEC  = 0.75
# Connection defaults — used by STANDALONE mode only; in driven mode the clerk
# owns the shared connection. Host/port come from the central config.
DEFAULT_PORT                  = n2_config.get_int(_CFG, "IBKR_GATEWAY_PORT", 4001)
DEFAULT_HOST                  = n2_config.get(_CFG, "IBKR_GATEWAY_HOST", "127.0.0.1")
DEFAULT_CLIENT_ID_BASE        = 10000

# STANDALONE market-data request id (driven mode uses the clerk's reqId)
MKT_DATA_REQ_ID = 1

# ibapi tick types we care about
TICK_BID, TICK_ASK, TICK_LAST, TICK_CLOSE = 1, 2, 4, 9
DELAYED_BID, DELAYED_ASK, DELAYED_LAST, DELAYED_CLOSE = 66, 67, 68, 75
# 3.1: tick 37 = Mark Price, IBKR's own bid/ask-bounded "theoretical value".
# Requested already (the clerk's genericTickList carries 221) and thrown away
# until now. It is the closest readable proxy for the REFERENCE price IBKR
# prices its regulatory cap against: on YAAS 2026-08-27 the mark was 2.5000 and
# IBKR's stated reference in the 202 was 2.5 exactly. Read-only, for the wall
# estimate and the diagnostic snapshot -- it never prices an order on its own.
TICK_MARK = 37
UNSET_DOUBLE = 1.7976931348623157e+308

# 3.3 (2026-10-02): the tick types that were ALREADY ARRIVING and were being
# dropped on the floor. The clerk subscribes once per trio with trade-mole's
# genericTickList (clerk-1.2.py:666 -> trade-mole-2.4.py GENERIC_TICKS) and its
# SharedIBClient implements tickSize/tickString/tickGeneric -- but fanned all
# three to the MOLE ONLY, so x-wing saw tickPrice alone and kept 5 fields out
# of it. Sizes (0/3/5), day volume (8) and day high/low/open (6/7/14) come with
# the DEFAULT tick set; everything else below is in the generic list already.
# Reading them costs no new request, no extra market-data line and no latency:
# ibapi has already decoded these packets today.
TICK_BID_SIZE, TICK_ASK_SIZE, TICK_LAST_SIZE = 0, 3, 5
TICK_HIGH, TICK_LOW, TICK_OPEN = 6, 7, 14
TICK_VOLUME = 8                       # CUMULATIVE day volume, in LOTS
TICK_AVG_VOLUME = 21                  # generic 165
TICK_LOW_52W, TICK_HIGH_52W = 19, 20  # generic 165
TICK_SHORTABLE = 46                   # generic 236
TICK_LAST_TIMESTAMP = 45
TICK_RT_VOLUME = 48                   # generic 233, via tickString
TICK_HALTED = 49
TICK_TRADE_COUNT, TICK_TRADE_RATE, TICK_VOLUME_RATE = 54, 55, 56  # 293/294/295
TICK_VOL_3MIN, TICK_VOL_5MIN, TICK_VOL_10MIN = 63, 64, 65         # generic 595
TICK_RT_TRD_VOLUME = 77               # generic 375, via tickString
TICK_SHORTABLE_SHARES = 89            # generic 236
DELAYED_BID_SIZE, DELAYED_ASK_SIZE, DELAYED_LAST_SIZE = 69, 70, 71
DELAYED_HIGH, DELAYED_LOW, DELAYED_VOLUME, DELAYED_OPEN = 72, 73, 74, 76

# STANDALONE genericTickList. Driven mode uses the clerk's (trade-mole's) list;
# this mirrors it plus 595 so an offline/standalone run records the same fields.
# Kept as a literal rather than imported from trade-mole: the clerk imports BOTH
# modules and x-wing must not depend on the mole.
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221,595"

# error codes that indicate an order modification was rejected (-> cancel+new fallback)
MODIFY_REJECT_CODES = {103, 104, 105, 106, 110, 161, 201, 202, 10147, 10148}

# hard rejections of a new (unfilled) BUY entry/re-entry order -> stop the run
ENTRY_REJECT_CODES = {110, 200, 201, 203}

# --- 3.0 error codes reached only because the entry is now re-priced ---------
# IBKR's regulatory price cap. NOT a rejection: the order is accepted, silently
# worked at a lower price, reported "Submitted", and 2161 arrives as a warning.
# 2.1 dropped it (only the clerk's catch-all logged it), which is why a capped
# run and a genuine deactivation were indistinguishable in every historical
# grep. Deliberately NOT in ENTRY_REJECT_CODES: a capped order is alive.
ENTRY_CAP_WARN_CODE = 2161
# "We cannot accept an order at a limit price at or more aggressive than X" —
# a HARD cancel at ~+9.80% over IBKR's own reference. Our 6% ceiling is below
# that against OUR reference, but IBKR's can be a stale last_close (PFSA:
# cap/ask 0.859), so it is reachable. Freezes the upward follow, keeps the
# down ratchet, lets the existing dead-status/timeout paths resolve the order.
ENTRY_TOO_AGGRESSIVE_CODE = 202
# Modify-class errors on the ENTRY id. 2.1 only ever consulted MODIFY_REJECT_CODES
# for the protective order, so these were silently ignored. 10147/10148 mean the
# order already resolved (benign race: a modify that crossed a fill); the rest
# mean the modification did not take. Either way: stop tracking, keep the order,
# resync from orderStatus. Never blind-retry.
ENTRY_TRACK_STOP_CODES = {104, 105, 161, 10147, 10148}
# ...with ONE exception, added 2026-08-14. 103 "Duplicate order id" is not a bad
# order, it is a REGISTRATION RACE: a re-price that reaches the Gateway before it
# has registered the original placeOrder is classified as a NEW order re-using a
# live id. The order stays alive at its previous limit, and the SAME modify sent a
# moment later succeeds — so this is the one modify-class error where a retry is
# not blind. Non-deterministic by gap: SCNX 2026-08-14 lost it at +4 ms and CAPS
# 08-12 at +52 ms, while CYCU (+0 ms) and NEXR (+46 ms) were both accepted.
# Deliberately excludes the content errors (106 invalid trigger price, 110 tick
# conformance): those recur identically on retry, so they belong in the stop set.
ENTRY_MODIFY_RETRY_CODES = {103}
# "Max rate of messages per second has been exceeded". Unhandled anywhere in the
# repo before 3.0. TWS eventually CLOSES the connection if the 50 msg/s cap keeps
# being breached, so this is a process-wide brake, not a per-run one.
MSG_RATE_ERROR_CODE = 100
MSG_RATE_BACKOFF_SEC = 5.0
# Process-global token bucket over entry re-prices. Every clerk session shares one
# Gateway, so the governor has to be process-wide: worst case is --client-qty
# (5 in practice, 30 max) simultaneous movers each re-pricing up to 4x/s. Set well
# under the 50 msg/s API cap to leave headroom for market data and the protective
# ladder. Measured baseline is only ~6-7 order actions per run against 2
# executions (OER ~2) at ~3.8 entries/day, so this is insurance, not a bottleneck.
GLOBAL_ORDER_MSG_PER_SEC = 30.0

# order statuses that mean the (unfilled) entry is dead / not working -> stop the run.
# IBKR can deactivate an entry with only this status and NO error() callback (seen
# 2026-07-10 JZXN: marketable BUY LMT -> "Inactive" @ +110ms, no code), so relying on
# ENTRY_REJECT_CODES alone leaves the duo idling in ENTRY_PENDING for the whole session.
ENTRY_DEAD_STATUSES = {"Inactive", "Cancelled", "ApiCancelled"}

# backstop: abort if the entry is still unfilled this many seconds after placement
# (covers silent non-fills where IBKR sends neither an error nor a terminal status).
# 0 disables the timeout. Lowered 30->10 on 2026-07-22: slow fills are almost all
# reversal fills (of 27 entries that took >1s, only 3 made money; every fill past
# 15s lost bar one). 10s clears the deep-tail losers without cutting normal entries;
# the pricing fix above already removes most slow fills at the source. See analysis.
# --------------------------------------------------------------------------- #
# 3.1: IBKR's regulatory price wall
# --------------------------------------------------------------------------- #
# IBKR caps aggressive limits against a reference price of its own. Two shapes:
#   2161 -> the order is LIVE and worked at a capped price (soft);
#   202  -> the order is DEAD, refused outright (hard).
# Measured on every occurrence in the corpus, exact to 6+ digits:
#   wall_buy = ref * 1.196   (YAAS 2.50->2.99, CJMB 2.01->2.40396,
#                             EHGO 1.75->2.093)
#   wall_sell = ref * 0.902  (IVF 2026-08-17, three refs)
# i.e. +19.6% / -9.8%, the Clearly-Erroneous outside-RTH guideline (20% sub-$25)
# less a haircut. ⚠ The reference is IBKR's OWN and lags the tape by seconds.
#
# Two failure shapes, both live on 2026-08-27:
#   YAAS — the OPENING shot was over the wall (a 21.5 s stale 1-lot ask at 3.05
#          priced a 3.13 limit against a 2.99 wall) -> 202 in 269 ms, n=0, and
#          the stock then ran +60%. Nothing was capped first, so only the retry
#          can recover this one.
#   CJMB — the opening 2.36 was ACCEPTED and 2161-capped to 2.25 (live and
#          working), then OUR OWN CHASE_CEIL to the 2.44 ceiling crossed the
#          2.40396 wall and killed it. EHGO 2026-08-19 is identical.
#
# ⚠⚠ The CJMB shape is structural, not bad luck: whenever a 2161 has fired, the
# +6% ceiling sits AT OR ABOVE the wall — CJMB +1.5%, EHGO +3.0%, CAST +0.5%,
# FEED +0.04%, XOS -0.09%. So CHASE_CEIL and CAP_RESCUE (which targets
# entry_ceiling verbatim) are 202 GENERATORS on every capped run. The clamp
# below is what stops that, and it is why CAP_RESCUE's target moves.
ENTRY_WALL_ENABLED            = True   # False => 3.0 behaviour, no clamp, no retry
ENTRY_WALL_RETRY_ENABLED      = True   # False => clamp only, never re-place
ENTRY_WALL_RETRY_MAX          = 2      # re-places per run, hard
ENTRY_WALL_RETRY_MIN_GAP_SEC  = 0.50   # between re-places
ENTRY_WALL_RETRY_MIN_LIFE_SEC = 1.5    # don't re-place with less than this left
# The 2161 cap MEASURES the reference we cannot otherwise read, so the wall is
# reachable as cap * this multiplier. The value is NOT a theory — it is bounded
# on BOTH sides by real runs, and the two sides very nearly meet:
#
#   must PREVENT (bound has to land under an observed wall):
#     CJMB  cap 2.25 wall 2.40396  ->  mult <  1.06843
#     EHGO  cap 1.91 wall 2.09300  ->  mult <  1.09581
#   must PRESERVE (bound has to still reach the ask that actually filled):
#     REBN  cap 1.63 filled at ask 1.72  ->  mult >= 1.05521
#
#   => SAFE WINDOW [1.05521, 1.06843).  1.06 sits inside with room either way.
#
# ⚠ The window is that narrow because a cap and a later modify are priced off
# DIFFERENT references — IBKR's moves between them (REBN legged up 1.775 s after
# its cap). So no single multiplier is universally correct, and erring LOW is
# the right bias: too low leaves the order ALIVE at a slightly worse limit, too
# high gets it CANCELLED outright. At 1.06 every run on record still behaves:
#   REBN 1.63 -> 1.72 = exactly its filling ask, still fills at 1.72
#   CJMB 2.25 -> 2.38 (clamps the 2.44 chase, under the 2.40396 wall, and still
#                      above the 2.36 IBKR had already accepted)
#   EHGO 1.91 -> 2.02 (under 2.093)
#   CAST 0.96 -> 1.01 (under its ~1.025 wall, still above its 0.97 ask)
#   MGRX 0.5764 -> 0.6109 (still well above its 0.5770 ask)
# The theoretical cap=ref*1.12 => 1.0679 is inside the window too, but by only
# 0.0005 against CJMB. Do not adopt it without calibrate_price_wall.py n >= 8.
ENTRY_WALL_OVER_CAP_MULT      = 1.06
# Latest observation wins rather than a running min(): IBKR's reference only
# RISES during a surge, so a min() would permanently cap a winner on a stale
# reading (AZI 2026-08-07 drew two 2161s in one run, 1.73 then 1.77).
ENTRY_WALL_OBS_LATEST         = True
# 2168/2169 sit in the 2xxx "warning" band but BLOCK the order (TWS 983+ says
# the eTradeOnly/firmQuoteOnly attributes must be removed, not set False — see
# _base_order). Zero occurrences in the corpus; defensive only.
ENTRY_WALL_BLOCK_CODES        = {202, 2168, 2169}
# STAGE 3, ships dark: estimating the wall from our OWN reference. Held off
# because our reference does not match IBKR's — measured, IBKR's ref sits at or
# BELOW our last by 0-10% (YAAS 2.50/2.50, CJMB 2.01/2.16, EHGO 1.75/1.95), so
# a band fitted to three points either fails to bind or throttles every normal
# gapper. calibrate_price_wall.py report (C) picks these; do not guess them.
ENTRY_WALL_ESTIMATE_ENABLED   = False
ENTRY_WALL_REF_SOURCE         = "last"   # last | mark | min_last_mark
ENTRY_WALL_REF_MULT           = 1.196
ENTRY_WALL_SAFETY_PCT         = 0.0

# The SELL side of the same wall, and the storm it caused. on_order_error's
# protective cancel+replace fallback had NO counter, NO throttle and NO gap, and
# it re-sent the IDENTICAL price every time. IVF 2026-08-17 09:40:59.627 ->
# 09:41:01.284: orderIds 34..44, ELEVEN orders in 1.66 s, every one 202-rejected
# and every one also drawing a 10148, while the position sat UNPROTECTED for
# ~6.4 s through a -8% move. IBKR's reference was a stale 2.6899 (wall
# 2.4262898 = ref*0.902) against a real mid of 2.4050, and the very same 2.39
# was accepted at 09:41:06 once the reference caught up.
# So: bound the attempts, space them, and on a 202 move the exit ABOVE the wall
# instead of re-sending a price IBKR has already refused.
PROTECTIVE_MODIFY_RETRY_MAX          = 3
PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC  = 0.50

# 3.4: RE-ARM A DEAD PROTECTIVE EXIT.
# JUNS 2026-10-06 is the evidence, and it cost 4 hours of naked exposure:
#   11:15:38.103  hard_floor fires -> type-change modify on orderId 61
#                 (STP LMT -> LMT @ 2.50, the bid)
#   11:15:38.105  IBKR: 103 "Duplicate order id" -- a type change by modify on a
#                 live id is not allowed, which is exactly what the cancel+new
#                 fallback below exists for
#   11:15:38.105  fallback cancels 61 and places 62 ... at _lmt_floor = 1.98, the
#                 LADDER's limit, NOT the bid the hard floor asked for (defect A)
#   11:15:38.216  IBKR: 202 "more aggressive than 2.3001 ... market price 2.55".
#                 1.98 was 22% under the market, so the refusal was certain.
#                 It landed 111 ms after the replace, inside the min-gap guard,
#                 so the handler returned having done NOTHING (defect B)
#   -> position 1 sh LONG, `exiting` latched, protective_status=Cancelled, and
#      no code path anywhere re-checked it. It sat unprotected from 11:15 to the
#      15:15 lifetime flatten, which the same wall refused again, leaving the
#      share open with a resting SELL LMT.
# The three guards in on_order_error (budget spent / min gap / empty token
# bucket) each return after only `wake.set()`. They are right to refuse to storm
# the Gateway, but nothing owned the state they leave behind. This supervisor
# does: once `exiting` is latched and a position is open, a dead resting exit is
# a bug to be repaired, not a terminal state.
EXIT_REARM_ENABLED     = True
# Grace after the order first reads dead, so a cancel+replace in flight is not
# raced. PendingCancel -> Cancelled -> the new order's Submitted all land inside
# ~150 ms on this Gateway (JUNS above), so 2 s is ~13x that.
EXIT_REARM_AFTER_SEC   = 2.0
EXIT_REARM_MAX_N       = 5
# Far wider than PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC on purpose: that guard
# spaces a burst inside one rejection, this one spaces whole repair attempts.
EXIT_REARM_MIN_GAP_SEC = 2.0
# A resting order in one of these states can never fill. "Rejected" is ours (set
# by the entry-reject branch), the other three are IBKR's.
ORDER_DEAD_STATUSES = frozenset(("Cancelled", "ApiCancelled", "Inactive",
                                 "Rejected"))

ENTRY_FILL_TIMEOUT_SEC = 10

# On timeout we CANCEL the resting entry (never leave it working) and wait this many
# seconds for the broker to confirm the outcome: a clean cancel (-> entry_inactive) or
# a late fill (-> pos>0, protective ladder arms). This is only a backstop so the run
# can't hang if the broker reports neither.
ENTRY_CANCEL_CONFIRM_SEC = 5


# --------------------------------------------------------------------------- #
# Exit-noise defences (2026-10-02)
# --------------------------------------------------------------------------- #
# Measured on 31 filled sessions 2026-09-28..10-01 (xwing_tables/). The whole
# exit path runs off the BID: current_exit_ref() returns it, yield_pct is
# (bid-entry_fill)/entry_fill, high_water is the peak BID and aux is
# high_water*(1-Trigger%/100). Two separate stops then exist:
#
#   1. a REAL IBKR "STP LMT" resting at the Gateway. triggerMethod is never set,
#      so IBKR uses its default -- the LAST TRADED price. This one was correct
#      and never fired.
#   2. x-wing's OWN check, `if exit_ref <= aux:` in control_loop, once a second,
#      on the BID. On fire it replaces the stop-limit with a plain SELL LMT
#      (arm_or_replace_protective(force_exit=True)), which has no trigger at all
#      and sells straight into the book.
#
# (2) did the damage. A bid is an offer that can be WITHDRAWN with no shares
# changing hands, so the level it ejects at need never have traded -- which is
# why the fills had no corresponding print on a TradingView chart. In 6 of 26
# triggered exits the stop fired while the last trade AND the ask were still at
# or above the entry fill: MEDS (entry 3.73, ejected on bid 3.58 with last 3.73
# / ask 3.75), VEEA, EDBL, IMCC, KIDZ (entry 2.83, ejected on bid 2.45 while
# last and ask were 2.90 -- a -13% loss on a stock that was UP), NAMI.
#
# Two independent causes, so two independent defences:
#
#  A. the bid alone must not be allowed to decide. Require a real TRADE at or
#     below aux as well. Replay shows this is what rescues the big givebacks:
#     HCTI exits at +8.38% instead of -6.06% and ZBAO at +10.41% instead of
#     -3.43%, each after holding 9 passes where the bid had crossed the stop and
#     no trade agreed.
#  B. a 1.0% trigger cannot survive a 2.4% median spread. v8-input.tsv rows for
#     yields 0-7 set Trigger(%)=1.0, and high_water at the fill row is already
#     the depressed bid, so aux sat ~1% under a price that jitters 2-6%. MEDS
#     needed 4 cents. The bid's jitter scales with the SPREAD, so scale the
#     trigger with it too and treat the table value as a floor.
#
# Note the band uses the 10 s MEDIAN spread, not the instantaneous one, and the
# two differ a lot on these names: HXHX quoted 0.43/0.44 (2.3%) for one tick
# while its 10 s median was 4.9% (its book was 0.40/0.42 and 0.4052/0.43). The
# median is the honest measure of a name's noise scale, and on the replay B
# alone is already enough to hold all three labelled ejections.
EXIT_REQUIRE_TRADE_CONFIRM    = True   # (A) synthetic trigger also needs last <= aux
# A name that has not printed recently gives no signal. Without this escape the
# bid-side stop would be suppressed ENTIRELY on a silent tape, leaving only the
# hard floor and IBKR's own STP LMT.
EXIT_CONFIRM_MAX_LAST_AGE_SEC = 30.0
EXIT_TRIGGER_SPREAD_MULT      = 1.5   # (B) eff_trigger% = max(table, mult * median spread%)
EXIT_SPREAD_WINDOW_SEC        = 10.0  # rolling window; the MEDIAN over it is used, so
                                      # one wide quote cannot move the stop
EXIT_SPREAD_MAX_PCT           = 20.0  # clamp: a NAMI-style 23.8% spread must not
                                      # widen the stop until it no longer exists
# Unconditional floor: no confirmation, no spread scaling. This is what lets A
# and B be generous safely. Measured on the LAST print, not the bid: the widest
# bid-ONLY excursion in the data is VEEA at 5.6% (bid 2.50 -> 2.36 with last
# still 2.50), so a 5% floor on the bid would fire on that noise and undo A.
# The same 5% on the last print still caps the loss at the chosen level.
HARD_FLOOR_PCT                = 5.0
HARD_FLOOR_ON_LAST            = True
# LESL 2026-09-30: the bid gapped 0.2320 -> 0.1815 straight THROUGH aux 0.2085
# and the sell limit at 0.2074 (v8 leaves only 0.5% between Trigger and Limit)
# rested unfilled for 860+ seconds. Widen the gap with the spread, and chase the
# limit down if it still has not filled.
EXIT_LMT_SPREAD_MULT          = 1.0   # limit gap = max(table gap, mult * spread%)
EXIT_LMT_CHASE_ENABLED        = True
EXIT_LMT_CHASE_AFTER_SEC      = 3.0
EXIT_LMT_CHASE_MAX_N          = 5
# Post-exit price sampling, written to a SEPARATE file so it can never interleave
# with the main table.
# 3.3: now ON. The 3.2 note said this could not work because "the clerk cancels
# the market-data subscription on recycle" -- that is wrong in the normal case.
# T47.on_run_done (T47-airspeeder-2.0.py:367-407) OPENS its re-entry watch when
# a run ends flat with budget left, and clerk-1.2.py:30 holds the client "for
# the WHOLE trio (watch included)"; cancelMktData is reached only in
# TrioSession.finish() (clerk-1.2.py:846). So quotes keep flowing after the exit
# for up to two hours. The tail goes dark only when T47 ends the trio at once
# (residual position / pending finish / budget exhausted) or relaunches and
# swaps session.xwing -- both covered by the stale bail below.
# Dense for the first 30 s (that is where "was I ejected into a bounce?" lives),
# 1 Hz after. ~175 KB/session; xwing_tables is 102 MB for 533 sessions today.
POST_EXIT_WATCH_SEC           = 300.0
POST_EXIT_FAST_SEC            = 30.0   # sample at POST_EXIT_FAST_HZ for this long
POST_EXIT_FAST_HZ             = 4.0
POST_EXIT_STALE_BAIL_SEC      = 30.0   # nothing moved for this long -> feed is gone
# Window for the 3.3 rolling microstructure metrics (quote rate, bid volatility).
MICRO_WINDOW_SEC              = 10.0


# --------------------------------------------------------------------------- #
# Argument parsing
# --------------------------------------------------------------------------- #
MAX_LIFETIME_SEC = 12 * 60 * 60  # 12 hours


def parse_lifetime(s: str) -> int:
    """hh:mm:ss or mm:ss -> total seconds (>0, capped at 12 hours)."""
    try:
        parts = s.split(":")
        if len(parts) == 3:
            hh, mm, ss = parts
            total = int(hh) * 3600 + int(mm) * 60 + int(ss)
        elif len(parts) == 2:
            mm, ss = parts
            total = int(mm) * 60 + int(ss)
        else:
            raise ValueError
        if total <= 0:
            raise ValueError
        if total > MAX_LIFETIME_SEC:
            raise argparse.ArgumentTypeError(
                f"--lifetime must be <= 12 hours ({MAX_LIFETIME_SEC}s), got {total}s")
        return total
    except argparse.ArgumentTypeError:
        raise
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--lifetime must be hh:mm:ss or mm:ss with a positive value, got {s!r}")


def parse_hhmm(s: str) -> dtime:
    """HH:MM (24h, ET) -> datetime.time."""
    try:
        hh, mm = s.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        raise argparse.ArgumentTypeError(
            f"--session-end must be HH:MM (24h ET), got {s!r}")


def build_arg_parser() -> argparse.ArgumentParser:
    """STANDALONE-mode CLI. Defaults come from the hard-coded config block at
    the top of the file; in driven mode the clerk builds the config directly
    via build_config() and does not use this parser."""
    p = argparse.ArgumentParser(
        description="x-wing-2.1: yield-laddered trailing-stop trader (IBKR/ibapi).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # --- clerk-supplied inputs ---
    p.add_argument("--symbol", required=True, help="Stock ticker, e.g. AAPL")
    p.add_argument("--last-close-price", dest="last_close_price", type=float, default=None,
                   help="Previous session close (lastDailyClose). Reported in the entry "
                        "log line; since 3.0 it no longer bounds the entry (the ceiling "
                        "is anchored on the trigger reference instead).")
    # --- x-wing-side config (defaults from the hard-coded block) ---
    p.add_argument("--capital", type=float, default=CAPITAL,
                   help="Dollar budget; shares = floor(capital / entry-limit-price)")
    p.add_argument("--Entry-limit-price", dest="entry_limit_price", default=None,
                   type=float, help="Fixed buy LMT fallback if no ask/last/close.")
    p.add_argument("--max-limit-entry-percent-price", dest="max_limit_entry_percent_price",
                   type=float, default=MAX_LIMIT_ENTRY_PERCENT_PRICE,
                   help="Buy LMT = ask * (1 + pct/100), priced off the ask handed over "
                        "by trade-mole (falls back ask->last->close). In 3.0 this is "
                        "also the offset the tracker holds above the LIVE ask.")
    p.add_argument("--max-cap-entry-percent", dest="max_cap_entry_percent", type=float,
                   default=MAX_CAP_ENTRY_PERCENT,
                   help="LEGACY last-close entry cap = last-close-price * (1 + pct/100). "
                        "Off by default in 3.0; --entry-track-max-pct replaces it.")
    p.add_argument("--entry-track-max-pct", dest="entry_track_max_pct", type=float,
                   default=ENTRY_TRACK_MAX_PCT,
                   help="Hard ceiling on the entry limit = trigger-reference * (1 + pct/100).")
    p.add_argument("--entry-track-up-window", dest="entry_track_up_window", type=float,
                   default=ENTRY_TRACK_UP_WINDOW_SEC,
                   help="Seconds after placement during which the limit may still be "
                        "raised. The downward ratchet is not time-boxed.")
    p.add_argument("--no-entry-track", dest="entry_track_enabled", action="store_false",
                   default=ENTRY_TRACK_ENABLED,
                   help="Disable ask-tracking: price the entry once and rest (2.1 behaviour).")
    # 3.1 wall kill-switches. These override the MODULE GLOBALS in main(), the
    # same shape as ENTRY_CAP_RESCUE_ENABLED: the wall logic is read from globals
    # so it is identically visible in standalone AND driven (clerk) mode — a
    # tunable plumbed only through build_arg_parser would be invisible in
    # production, which is the trap build_config exists to avoid.
    p.add_argument("--no-entry-wall", dest="entry_wall_enabled",
                   action="store_false", default=ENTRY_WALL_ENABLED,
                   help="Disable the IBKR price-wall clamp AND the re-place "
                        "(full x-wing-3.0 behaviour).")
    p.add_argument("--no-cap-rescue-one-tick", dest="cap_rescue_one_tick",
                   action="store_false", default=ENTRY_CAP_RESCUE_ONE_TICK,
                   help="CAP_RESCUE arms only with a full dead-band of room "
                        "(pre-10-06 behaviour).")
    p.add_argument("--no-entry-wall-retry", dest="entry_wall_retry_enabled",
                   action="store_false", default=ENTRY_WALL_RETRY_ENABLED,
                   help="Keep the wall clamp but never re-place after a 202.")
    p.add_argument("--entry-wall-retry-max", dest="entry_wall_retry_max", type=int,
                   default=ENTRY_WALL_RETRY_MAX,
                   help="Max wall re-places per run (default %d)."
                        % ENTRY_WALL_RETRY_MAX)
    p.add_argument("--entry-wall-over-cap-mult", dest="entry_wall_over_cap_mult",
                   type=float, default=ENTRY_WALL_OVER_CAP_MULT,
                   help="Wall bound = 2161 cap * this. Empirical safe window is "
                        "[1.05521, 1.06843); default %.4f."
                        % ENTRY_WALL_OVER_CAP_MULT)
    p.add_argument("--account", default=ACCOUNT,
                   help="IBKR account ID (set on every order).")
    p.add_argument("--input-limits-table", dest="limits_table", default=INPUT_LIMITS_TABLE,
                   help="TSV with columns: 'Yield (%%)', 'Trigger(%%)', 'Limit(%%)'")
    p.add_argument("--log-dir", dest="log_dir", default=LOG_DIR, help="Log directory")
    p.add_argument("--price-action-table", dest="price_action_table",
                   default=PRICE_ACTION_TABLE_DIR,
                   help="Output TSV path (file) or directory. "
                        "Auto-name: xwing-table_<symbol>_<date>_<time>.tsv")
    p.add_argument("--lifetime", type=parse_lifetime, default=parse_lifetime(LIFETIME),
                   help="Run duration hh:mm:ss or mm:ss (max 12h); on expiry "
                        "flatten @ bid and exit")
    p.add_argument("--session-end", dest="session_end", type=parse_hhmm, default=None,
                   help="ET session-close HH:MM; flatten @ bid and exit at/after it")
    p.add_argument("--client-id-base", dest="client_id_base", type=int,
                   default=DEFAULT_CLIENT_ID_BASE,
                   help="STANDALONE single-client id (driven mode ignores this).")
    p.add_argument("--host", default=DEFAULT_HOST, help="TWS/Gateway host")
    p.add_argument("--port", type=int, default=DEFAULT_PORT,
                   help="IBKR port (4001 live GW / 4002 paper GW / 7497 paper TWS)")
    p.add_argument("--market-data-type", dest="market_data_type", type=int, default=1,
                   choices=[1, 2, 3, 4],
                   help="1=real-time, 2=frozen, 3=delayed, 4=delayed-frozen")
    p.add_argument("--loop-interval", dest="loop_interval", type=float, default=1.0,
                   help="Control-loop / price-sample interval in seconds")
    p.add_argument("--entry-fill-timeout", dest="entry_fill_timeout", type=float,
                   default=ENTRY_FILL_TIMEOUT_SEC,
                   help="Abort if the entry is still unfilled this many seconds after "
                        "placement (0 disables). Also aborts immediately if IBKR marks "
                        "the entry Inactive/Cancelled.")
    # --- exit-noise defences (3.2) -------------------------------------------
    p.add_argument("--no-exit-trade-confirm", dest="exit_trade_confirm",
                   action="store_false", default=EXIT_REQUIRE_TRADE_CONFIRM,
                   help="Let the BID alone fire x-wing's own stop (pre-3.2 "
                        "behaviour). Default requires a trade at/below aux too.")
    p.add_argument("--no-exit-spread-trigger", dest="exit_spread_trigger",
                   action="store_true", default=False,
                   help="Use the table Trigger%%/Limit%% verbatim instead of "
                        "widening them with the measured spread.")
    p.add_argument("--exit-trigger-spread-mult", dest="exit_trigger_spread_mult",
                   type=float, default=EXIT_TRIGGER_SPREAD_MULT,
                   help="eff_trigger%% = max(table, mult * median spread%%); "
                        "default {:.2f}".format(EXIT_TRIGGER_SPREAD_MULT))
    p.add_argument("--hard-floor-pct", dest="hard_floor_pct", type=float,
                   default=HARD_FLOOR_PCT,
                   help="Unconditional exit below entry_fill*(1-pct/100); "
                        "0 disables. Default %.1f" % HARD_FLOOR_PCT)
    p.add_argument("--hard-floor-on-bid", dest="hard_floor_on_last",
                   action="store_false", default=HARD_FLOOR_ON_LAST,
                   help="Measure the hard floor on the BID instead of the last "
                        "print. ⚠ The widest bid-only noise excursion on record "
                        "is 5.6%% (VEEA), so a 5%% floor on the bid will fire on "
                        "that noise and undo --no-exit-trade-confirm's defence.")
    p.add_argument("--no-exit-lmt-chase", dest="exit_lmt_chase",
                   action="store_false", default=EXIT_LMT_CHASE_ENABLED,
                   help="Never re-price an unfilled exit LMT down to the bid "
                        "(LESL 2026-09-30 rested 860+s through a gap-down).")
    p.add_argument("--no-exit-rearm", dest="exit_rearm",
                   action="store_false", default=EXIT_REARM_ENABLED,
                   help="Never re-place a protective exit that IBKR has killed "
                        "(JUNS 2026-10-06 left 1 sh naked for 4 hours).")
    p.add_argument("--post-exit-watch-sec", dest="post_exit_watch_sec", type=float,
                   default=POST_EXIT_WATCH_SEC,
                   help="Sample prices for N seconds after the exit into "
                        "xwing_tables/postexit/; 0 disables (default).")
    p.add_argument("--loglevel", default="INFO",
                   choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p


def build_config(symbol, last_close_price, **overrides):
    """Build an args-like config namespace for DRIVEN mode (clerk). Mirrors the
    hard-coded block; `symbol` and `last_close_price` come from the orch-trigger.
    Any field can be overridden via kwargs (dest names)."""
    from types import SimpleNamespace
    cfg = SimpleNamespace(
        symbol=symbol,
        last_close_price=last_close_price,
        capital=CAPITAL,
        entry_limit_price=None,
        max_limit_entry_percent_price=MAX_LIMIT_ENTRY_PERCENT_PRICE,
        max_cap_entry_percent=MAX_CAP_ENTRY_PERCENT,
        entry_track_enabled=ENTRY_TRACK_ENABLED,
        entry_track_max_pct=ENTRY_TRACK_MAX_PCT,
        entry_track_up_window=ENTRY_TRACK_UP_WINDOW_SEC,
        account=ACCOUNT,
        limits_table=INPUT_LIMITS_TABLE,
        log_dir=LOG_DIR,
        price_action_table=PRICE_ACTION_TABLE_DIR,
        lifetime=parse_lifetime(LIFETIME),
        session_end=None,
        client_id_base=DEFAULT_CLIENT_ID_BASE,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        market_data_type=1,
        loop_interval=1.0,
        entry_fill_timeout=ENTRY_FILL_TIMEOUT_SEC,
        loglevel="INFO",
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
def setup_logging(log_dir: str, symbol: str, level: str,
                  name: str = "x-wing", console: bool = True) -> logging.Logger:
    """Create/configure a logger writing to xwing_<symbol>_<date>_<time>.log.

    Standalone mode uses the module logger ("x-wing"). Driven mode (clerk)
    passes a unique `name` per duo (e.g. "xwing.JWEL.1003") so concurrent duos
    each get their own file; with console=False it relies on the clerk's root
    console handler. Returns the logger; the FileHandler is attached so the
    caller can detach it on teardown."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_path = Path(log_dir) / f"xwing_{symbol}_{datetime.now():%Y-%m-%d_%H-%M-%S}.log"
    lg = logging.getLogger(name)
    lg.setLevel(getattr(logging, level))
    fmt = logging.Formatter("%(asctime)s.%(msecs)03d - %(levelname)s - %(message)s",
                            datefmt="%H:%M:%S")
    fh = logging.FileHandler(log_path)
    fh.setFormatter(fmt)
    fh.setLevel(logging.DEBUG)
    lg.addHandler(fh)
    if console and not any(isinstance(h, logging.StreamHandler)
                           and not isinstance(h, logging.FileHandler)
                           for h in lg.handlers):
        ch = logging.StreamHandler()
        ch.setFormatter(fmt)
        ch.setLevel(getattr(logging, level))
        lg.addHandler(ch)
    lg.info("Log file: %s", log_path.resolve())
    return lg


# --------------------------------------------------------------------------- #
# Limits table
# --------------------------------------------------------------------------- #
class LimitsTable:
    """Yield -> (Trigger%, Limit%) banded lookup. Ignores any illustrative
    auxPrice($)/lmtPrice($) columns; recomputes from the live midpoint."""

    def __init__(self, path: str, log: logging.Logger = None):
        self._log = log or logger
        df = pd.read_csv(path, sep="\t")
        df.columns = [c.strip() for c in df.columns]
        # tolerate minor header variations
        ycol = self._find(df, ["Yield (%)", "Yield(%)", "Yield"])
        tcol = self._find(df, ["Trigger(%)", "Trigger (%)", "Trigger"])
        lcol = self._find(df, ["Limit(%)", "Limit (%)", "Limit"])
        rows = df[[ycol, tcol, lcol]].dropna().astype(float)
        rows = rows.sort_values(ycol).reset_index(drop=True)
        if rows.empty:
            raise ValueError(f"No usable rows in limits table {path!r}")
        self.yields = rows[ycol].tolist()
        self.triggers = rows[tcol].tolist()
        self.limits = rows[lcol].tolist()
        self._log.info("Loaded limits table %s (%d rows, yields %.0f..%.0f%%)",
                       path, len(self.yields), self.yields[0], self.yields[-1])

    @staticmethod
    def _find(df, candidates):
        for c in candidates:
            if c in df.columns:
                return c
        raise ValueError(f"limits table missing a column among {candidates}; "
                         f"found {list(df.columns)}")

    def lookup(self, yield_pct: float):
        """Highest row whose Yield (%) <= yield_pct; floor at the first row."""
        idx = 0
        for i, y in enumerate(self.yields):
            if y <= yield_pct:
                idx = i
            else:
                break
        return self.yields[idx], self.triggers[idx], self.limits[idx]


def round_to_tick(price: float, tick: float) -> float:
    if tick is None or tick <= 0:
        tick = 0.01
    # SEC Rule 612: US NMS stocks priced >= $1.00 must be in $0.01 increments;
    # sub-penny ($0.0001) is allowed only below $1.00. contractDetails.minTick is
    # the finest band, so enforce the penny floor at/above $1.00.
    if price >= 1.0 and tick < 0.01:
        tick = 0.01
    return round(round(price / tick) * tick, 6)


_CAP_PRICE_RE = re.compile(
    r"cap\s*\(or limit\)\s*the price of your Limit Order to\s*([0-9]*\.?[0-9]+)",
    re.IGNORECASE)


def _parse_capped_price(msg: str):
    """Pull IBKR's cap out of a 2161 warning, or None.

    Verbatim shape (clerk.log 2026-07-27, PFSA): "...we will initially cap (or
    limit) the price of your Limit Order to 1.76 or a more aggressive price still
    within your specified limit price...". This is the only place the capped
    price appears anywhere in the API surface — orderStatus never carries a price
    — so without it a capped run and a genuine deactivation are indistinguishable,
    which is exactly the collision rejected-orders-findings.txt documents."""
    if not msg:
        return None
    m = _CAP_PRICE_RE.search(msg)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


# 3.1: the HARD wall, from a 202 rejection. Distinct from the 2161 cap above:
# a 2161 leaves the order LIVE and working at the capped price, a 202 means the
# order is DEAD and the stated price was refused outright.
#
# Verbatim shape (clerk.log 2026-08-27, YAAS): "Order Canceled - reason:We
# cannot accept an order at a limit price at or more aggressive than 2.99.
# Please submit your order using a <br>limit price that is closer to the current
# market price of 2.5."
#
# Two numbers, and BOTH matter:
#   * the WALL  - the price IBKR refused. Note "at or more aggressive than", so
#     the wall itself is rejected too and a limit must land strictly below it
#     (see _wall_step_down).
#   * the REF   - IBKR's own reference price, which we cannot otherwise read and
#     which LAGS the tape by seconds. Every observation on record satisfies
#     wall == ref * 1.196 on the buy side and ref * 0.902 on the sell side, to
#     6+ digits: YAAS 2.50->2.99, CJMB 2.01->2.40396, EHGO 1.75->2.093, and
#     IVF's sell side 2.6899->2.4262898, 2.674->2.411948, 2.6662->2.4049124.
#
# WARNING: 202 is a GENERIC bucket -- its base text is "Order cancelled -
# Reason:" with an arbitrary suffix, and it is also the acknowledgement of a
# cancel WE requested. Match on the reason substring, never on the code alone.
# WARNING: the live message contains a literal "<br>" inside the sentence and
# its position MOVES with the length of the price string ("using a <br>limit",
# "using <br>a limit", "order <br>using a limit" all occur in the corpus), so
# neither pattern below may span it. Both anchors sit on the safe side of every
# break observed.
_WALL_PRICE_RE = re.compile(
    r"more aggressive than\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)
_WALL_REF_RE = re.compile(
    r"current market price of\s*([0-9]*\.?[0-9]+)", re.IGNORECASE)


def _parse_wall_prices(msg: str):
    """(wall, ref) out of a 202 aggressive-limit rejection, or (None, None).

    Either element can be None on its own if IBKR reworded one half; callers
    must treat the wall as the load-bearing one and the ref as diagnostic."""
    if not msg:
        return None, None
    out = []
    for rx in (_WALL_PRICE_RE, _WALL_REF_RE):
        m = rx.search(msg)
        if not m:
            out.append(None)
            continue
        try:
            v = float(m.group(1))
        except ValueError:
            v = None
        out.append(v if (v is not None and v > 0) else None)
    return out[0], out[1]


class _GlobalOrderThrottle:
    """Process-wide token bucket over entry re-prices, plus the error-100 brake.

    Has to be process-wide, not per-controller: every clerk session shares one
    Gateway socket pool, so the 50 msg/s API cap is a property of the process.
    Worst case is --client-qty simultaneous movers (5 in practice, 30 max) each
    re-pricing up to 4x/s. Non-blocking by design — a refused token just means
    the tracker skips this evaluation and retries on the next tick; blocking
    here would stall the control-loop thread of a live position."""

    def __init__(self, rate_per_sec: float):
        self.rate = rate_per_sec
        self._tokens = rate_per_sec
        self._ts = time.time()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> bool:
        now = time.time()
        with self._lock:
            if now < self._blocked_until:
                return False
            self._tokens = min(self.rate, self._tokens + (now - self._ts) * self.rate)
            self._ts = now
            if self._tokens < 1.0:
                return False
            self._tokens -= 1.0
            return True

    def back_off(self, seconds: float):
        """Called on IBKR error 100. TWS eventually CLOSES the connection if the
        message cap keeps being breached, so this halts ALL tracking in the
        process, not just the run that tripped it."""
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.time() + seconds)
            self._tokens = 0.0

    def blocked(self) -> bool:
        with self._lock:
            return time.time() < self._blocked_until


ORDER_THROTTLE = _GlobalOrderThrottle(GLOBAL_ORDER_MSG_PER_SEC)


def effective_tick(price: float, tick: float) -> float:
    """The increment a limit at `price` must actually land on. Same SEC Rule 612
    penny floor as round_to_tick, exposed so the tracker can size its dead band
    and its minimum offset in real ticks (which are $0.0001 below $1.00 and
    $0.01 at/above it — a 270x difference across this universe)."""
    if tick is None or tick <= 0:
        tick = 0.01
    if price >= 1.0 and tick < 0.01:
        tick = 0.01
    return tick


def ceil_to_tick(price: float, tick: float) -> float:
    """Round a BUY limit UP to a valid increment. round_to_tick() rounds to the
    NEAREST, which can quietly shave the offset by half a tick — at $1.00-$2.00
    a tick is a full 0.5-1.0% of price, so that is the difference between
    crossing the offer and resting under it. Used for the entry only; the
    protective STP LMT keeps nearest-rounding."""
    t = effective_tick(price, tick)
    return round(math.ceil(round(price / t, 9)) * t, 6)


def floor_to_tick(price: float, tick: float) -> float:
    """Round a BUY limit DOWN to a valid increment — the mirror of ceil_to_tick.

    Needed only for the regulatory wall bound (3.1): every other BUY rounding in
    this file rounds UP to stay marketable, but a bound must never round UP past
    the thing it is bounding, or the clamp hands back the tick it just took."""
    t = effective_tick(price, tick)
    return round(math.floor(round(price / t, 9)) * t, 6)


# --------------------------------------------------------------------------- #
# Price-action table writer
# --------------------------------------------------------------------------- #
# 3.0 appends five entry columns AFTER why_held. Appending keeps older parsers
# working (analyze_trade_mole/analyze_firstrade_outcomes.py set-matches on the
# `event` value, and why_held was added the same way on 2026-08-11). Until now
# the entry limit price was not in the tables at ALL — `lmt_price` is the
# protective stop — so every entry-pricing question had to be answered by
# re-parsing xwing-logs/.
PA_COLUMNS = [
    "timestamp", "symbol", "phase", "bid", "ask", "mid", "orig_avg_fill",
    "filled_qty", "position", "yield_pct", "row_yield_threshold", "trigger_pct",
    "limit_pct", "aux_price", "lmt_price", "protective_order_id",
    "protective_status", "event", "why_held",
    "entry_order_id", "entry_lmt", "entry_status", "track_n", "capped_to",
    # 3.1, append-only again: wall_obs is the price bound IBKR's regulatory cap
    # is imposing on us right now (blank when nothing has been observed);
    # ref_snapshot is _ref_snapshot_str() -- every price input to the entry
    # decision and its age, so a rejected entry can be diagnosed from the table
    # alone instead of by cross-reading three logs.
    "wall_obs", "ref_snapshot",
    # 3.2, append-only again (verify_xwing3_tracking.py,
    # analyze_firstrade_outcomes.py and calibrate_price_wall.py all parse these
    # files by NAME, so never reorder). `last` is overdue: the traded price was
    # previously reachable only by string-parsing ref_snapshot, which is what
    # made the 2026-10-01 early-ejection diagnosis slow. trigger_source says
    # WHICH stop fired (bid+last / hard_floor / bid_only_stale_last), and
    # exit_fill_px/realized_yield_pct finally make the outcome of a trade
    # measurable from the table alone -- until now the loop exited before the
    # fill was ever written, so realised profit and loss was not recorded
    # anywhere.
    "last", "spread_pct", "eff_trigger_pct", "eff_limit_pct", "trigger_source",
    "deferred_n", "exit_fill_px", "exit_fill_ts", "realized_yield_pct",
    # 3.3, append-only again. Every field here was ALREADY ARRIVING on the
    # existing subscription and was being thrown away (see the tick-type block
    # near the top). RECORD-ONLY: no exit decision reads any of them in 3.3.
    # They exist so the exit rules can be calibrated from a labelled corpus of
    # these tables instead of from argument. Real columns, not packed into a
    # k=v string: the 3.2 diagnosis was slow precisely because `last` was
    # reachable only by string-parsing ref_snapshot. The one exception is
    # static_snapshot, which holds the handful of values that arrive once per
    # session and never move.
    "bid_size", "ask_size", "last_size", "microprice", "touch_imbalance",
    "day_high", "day_low", "day_volume_lots", "vwap", "px_vs_vwap_pct",
    "trade_count", "trade_rate", "volume_rate", "vol_3min", "vol_5min",
    "vol_10min", "halted", "quote_rate", "bid_vol_10s", "trade_through_n",
    "last_exch_ts", "static_snapshot",
]


class PriceActionWriter:
    def __init__(self, path_arg, symbol, log_dir, log: logging.Logger = None):
        self._log = log or logger
        if not path_arg:
            path_arg = log_dir
        p = Path(path_arg)
        if p.suffix.lower() == ".tsv":
            self.path = p
        else:  # treat as a directory -> auto-name
            now = datetime.now()
            fname = f"xwing-table_{symbol}_{now:%Y-%m-%d_%H-%M-%S}.tsv"
            self.path = p / fname
        # The file (path + timestamp) is resolved now so it stays aligned with the
        # session-start naming, but it is NOT opened until the first write() — this
        # avoids leaving empty, header-only files for sessions that never trade.
        self._lock = threading.Lock()
        self._fh = None
        self._w = None

    def _ensure_open(self):
        """Open the file and write the header on first use. Caller holds _lock."""
        if self._fh is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=PA_COLUMNS, delimiter="\t",
                                 extrasaction="ignore")
        if new:
            self._w.writeheader()
            self._fh.flush()
        self._log.info("Price-action table: %s", self.path.resolve())

    def write(self, row: dict):
        with self._lock:
            self._ensure_open()
            self._w.writerow(row)
            self._fh.flush()

    def close(self):
        if self._fh is None:
            return
        try:
            self._fh.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# IB clients
# --------------------------------------------------------------------------- #
def route_tick_price(ctl, tickType, price):
    """Map an ibapi tickPrice into a controller quote update. Shared by the
    standalone XWingClient and the clerk's SharedIBClient fan-out so both modes
    feed x-wing quotes identically."""
    if price is None or price <= 0 or price == UNSET_DOUBLE:
        return
    if tickType in (TICK_BID, DELAYED_BID):
        ctl.update_quote(bid=price)
    elif tickType in (TICK_ASK, DELAYED_ASK):
        ctl.update_quote(ask=price)
    elif tickType in (TICK_LAST, DELAYED_LAST):
        ctl.update_quote(last=price)
    elif tickType in (TICK_CLOSE, DELAYED_CLOSE):
        ctl.update_quote(close=price)
    elif tickType == TICK_MARK:
        ctl.update_quote(mark=price)
    elif tickType in (TICK_HIGH, DELAYED_HIGH):
        ctl.update_quote(day_high=price)
    elif tickType in (TICK_LOW, DELAYED_LOW):
        ctl.update_quote(day_low=price)
    elif tickType in (TICK_OPEN, DELAYED_OPEN):
        ctl.update_quote(day_open=price)
    elif tickType == TICK_HIGH_52W:
        ctl.update_quote(high_52w=price)
    elif tickType == TICK_LOW_52W:
        ctl.update_quote(low_52w=price)


def route_tick_size(ctl, tickType, size):
    """3.3: map an ibapi tickSize into the controller. Shared by the standalone
    XWingClient and the clerk's SharedIBClient fan-out, exactly like
    route_tick_price.

    ⚠ Unlike a price, a size of ZERO is meaningful and must pass: a bid whose
    size goes to 0 is the book being pulled, which is the single event behind
    every one of the 2026-10-01 noise ejections. Only None and the ibapi
    not-set sentinel are rejected. Sizes arrive as Decimal on current ibapi."""
    if size is None:
        return
    try:
        v = float(size)
    except (TypeError, ValueError):
        return
    if v < 0 or v == UNSET_DOUBLE:
        return
    if tickType in (TICK_BID_SIZE, DELAYED_BID_SIZE):
        ctl.update_quote(bid_size=v)
    elif tickType in (TICK_ASK_SIZE, DELAYED_ASK_SIZE):
        ctl.update_quote(ask_size=v)
    elif tickType in (TICK_LAST_SIZE, DELAYED_LAST_SIZE):
        ctl.update_quote(last_size=v)
    elif tickType in (TICK_VOLUME, DELAYED_VOLUME):
        ctl.update_quote(day_volume=v)
    elif tickType == TICK_AVG_VOLUME:
        ctl.update_quote(avg_volume=v)
    elif tickType == TICK_SHORTABLE_SHARES:
        ctl.update_quote(shortable_shares=v)


def route_tick_generic(ctl, tickType, value):
    """3.3: map an ibapi tickGeneric into the controller.

    These are all already requested (the clerk passes trade-mole's generic list)
    and all server-side computed, so they cost nothing to read. Zero is a valid
    value for every one of them -- a trade rate of 0 IS the dead tape that lets
    a bid be withdrawn with nothing trading, which is the MEDS shape."""
    if value is None:
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        return
    if v == UNSET_DOUBLE:
        return
    if tickType == TICK_TRADE_COUNT:
        ctl.update_quote(trade_count=v)
    elif tickType == TICK_TRADE_RATE:
        ctl.update_quote(trade_rate=v)
    elif tickType == TICK_VOLUME_RATE:
        ctl.update_quote(volume_rate=v)
    elif tickType == TICK_VOL_3MIN:
        ctl.update_quote(vol_3min=v)
    elif tickType == TICK_VOL_5MIN:
        ctl.update_quote(vol_5min=v)
    elif tickType == TICK_VOL_10MIN:
        ctl.update_quote(vol_10min=v)
    elif tickType == TICK_SHORTABLE:
        ctl.update_quote(shortable=v)
    elif tickType == TICK_HALTED:
        # 0 = not halted, 1 = halted (general), 2 = halted (volatility). Kept
        # raw: a halt is the worst state to sell into and x-wing currently
        # cannot tell one from a dead tape. Recorded only in 3.3.
        ctl.update_quote(halted=v)


def _parse_rt_volume(value):
    """Parse an RTVolume / RTTradeVolume tickString payload.

    Format: price;size;time_ms;totalVolume;VWAP;singleTradeFlag — any field can
    be empty (IBKR sends partially-filled payloads), so every one is parsed
    independently and a bad field never discards the good ones. trade-mole has
    its own parser; this one is deliberately separate because the clerk imports
    BOTH modules and x-wing must not depend on the mole."""
    parts = (value or "").split(";")
    if len(parts) < 6:
        return None
    out = {}

    def num(i, key, cast=float):
        s = parts[i].strip()
        if not s:
            return
        try:
            x = cast(s)
        except (TypeError, ValueError):
            return
        out[key] = x

    num(0, "last")
    num(1, "last_size")
    num(2, "last_exch_ms")
    num(3, "day_volume")
    num(4, "vwap")
    flag = parts[5].strip().lower()
    if flag in ("true", "1"):
        out["last_single_trade"] = 1.0
    elif flag in ("false", "0"):
        out["last_single_trade"] = 0.0
    return out or None


def route_tick_string(ctl, tickType, value):
    """3.3: map an ibapi tickString into the controller.

    RTVolume (48, generic 233) is the one that matters: it carries the VWAP and
    the EXCHANGE timestamp of the print for free. The exchange stamp is strictly
    better than our own arrival stamp, which is all last_age() can offer today."""
    if tickType in (TICK_RT_VOLUME, TICK_RT_TRD_VOLUME):
        d = _parse_rt_volume(value)
        if d:
            px = d.pop("last", None)
            if px is not None and px <= 0:
                px = None
            ctl.update_quote(last=px, **d)
    elif tickType == TICK_LAST_TIMESTAMP:
        try:
            ctl.update_quote(last_exch_ms=float(value) * 1000.0)
        except (TypeError, ValueError):
            pass


class XWingClient(EWrapper, EClient):
    """STANDALONE single-client: handles BOTH order placement/fills/contract
    resolution AND streaming L1 market data on one ibapi connection. (In driven
    mode the clerk's SharedIBClient plays this role and this class is unused.)"""

    def __init__(self, controller):
        EClient.__init__(self, self)
        self.ctl = controller
        self.connected_evt = threading.Event()
        self.contract_evt = threading.Event()

    # ---- connection / ids ----
    def nextValidId(self, orderId: int):
        self.ctl.set_next_order_id(orderId)
        if not self.connected_evt.is_set():
            self.ctl.log.info("XWingClient connected; nextValidId=%d", orderId)
            self.connected_evt.set()

    def contractDetails(self, reqId: int, contractDetails):
        self.ctl.set_contract_details(contractDetails)
        self.contract_evt.set()

    def contractDetailsEnd(self, reqId: int):
        self.contract_evt.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        info = {2104, 2106, 2107, 2108, 2109, 2119, 2158, 2100, 2150, 399,
                10167, 10197}
        msg = f"IB msg reqId={reqId} code={errorCode}: {errorString}"
        if errorCode in info:
            self.ctl.log.info(msg)
        else:
            self.ctl.log.warning(msg)
        self.ctl.on_order_error(reqId, errorCode, errorString)

    # ---- order callbacks ----
    def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                    permId, parentId, lastFillPrice, clientId, whyHeld,
                    mktCapPrice=0.0):
        self.ctl.on_order_status_ex(orderId, status, float(filled),
                                    float(avgFillPrice), float(lastFillPrice),
                                    why_held=whyHeld or "",
                                    mkt_cap_price=float(mktCapPrice or 0.0))

    def openOrder(self, orderId, contract, order, orderState):
        # INFO, not DEBUG: on a silent "Inactive" entry (see rejected-orders-findings
        # PART B) warningText/completedStatus is the only reason IBKR ever sends, and
        # at DEBUG we were discarding it.
        self.ctl.log.info("openOrder %d %s %s qty=%s lmt=%s status=%s completed=%s warn=%r",
                          orderId, order.action, order.orderType,
                          order.totalQuantity, getattr(order, "lmtPrice", ""),
                          orderState.status,
                          getattr(orderState, "completedStatus", ""),
                          getattr(orderState, "warningText", "") or "")
        # 3.0: the working limit is the only way to SEE a 2161 cap (orderStatus
        # never reports a price). The clerk forwards this too, getattr-guarded.
        self.ctl.on_open_order(orderId, getattr(order, "lmtPrice", None),
                               orderState.status)

    def execDetails(self, reqId, contract, execution):
        self.ctl.log.info("exec %s %s %s @ %.4f (orderId=%d)", execution.side,
                          execution.shares, contract.symbol, execution.price,
                          execution.orderId)

    # ---- market data ----
    def tickPrice(self, reqId, tickType, price, attrib):
        route_tick_price(self.ctl, tickType, price)

    # 3.3: standalone mode must see exactly what driven mode sees, or an offline
    # replay of a recorded table is calibrating against different inputs.
    def tickSize(self, reqId, tickType, size):
        route_tick_size(self.ctl, tickType, size)

    def tickGeneric(self, reqId, tickType, value):
        route_tick_generic(self.ctl, tickType, value)

    def tickString(self, reqId, tickType, value):
        route_tick_string(self.ctl, tickType, value)


# --------------------------------------------------------------------------- #
# Controller / state machine
# --------------------------------------------------------------------------- #
PHASE_INIT = "INIT"
PHASE_ARMED = "ARMED"
PHASE_ENTRY_PENDING = "ENTRY_PENDING"
PHASE_ENTRY_PARTIAL = "ENTRY_PARTIAL"
PHASE_LONG_MONITOR = "LONG_MONITOR"
PHASE_EXITING = "EXITING"
PHASE_FLATTEN = "FLATTEN"
PHASE_SHUTDOWN = "SHUTDOWN"
PHASE_ENTRY_FAILED = "ENTRY_FAILED"   # entry deactivated/rejected/timed-out, never filled


class XWingController:
    def __init__(self, args, table: LimitsTable, pa_writer: PriceActionWriter,
                 log: logging.Logger = None, driven: bool = False,
                 id_alloc=None, on_done=None):
        self.args = args
        self.symbol = args.symbol.upper()
        self.table = table
        self.pa = pa_writer
        self.log = log or logger

        # driven (clerk) vs standalone wiring
        self.driven = driven
        self._id_alloc = id_alloc        # callable -> next order id (driven mode)
        self.on_done = on_done           # callback(reason) when the trade closes
        self.done_reason = None          # set when the trade closes / aborts

        self.lock = threading.RLock()
        self.wake = threading.Event()      # interruptible sleep for the main loop
        self.stop_evt = threading.Event()  # set on signal / lifetime / session end / done

        # In driven mode order_client is the shared client; there is no separate
        # data client (the clerk fans the tick stream into update_quote()).
        self.order_client = None
        self.data_client = None

        # contract
        self.contract = None
        self.min_tick = 0.01

        # quotes
        self.bid = self.ask = self.last = self.close = None
        # 3.1: mark price (tick 37) + per-field arrival stamps. Diagnostic:
        # the wall estimate and the ref snapshot read them, no order does.
        self.mark = None
        self.bid_ts = self.ask_ts = self.last_ts = self.mark_ts = None
        # 3.3: everything below ALREADY ARRIVES on the existing subscription and
        # was being discarded. RECORD-ONLY in 3.3 -- no exit decision reads any
        # of it. The point is to make the exit calibratable from the tables.
        # Size at the touch is the variable behind every noise ejection: a 1-lot
        # bid being withdrawn is not a market falling, a 50-lot bid being eaten
        # is, and nothing in this process could tell them apart.
        self.bid_size = self.ask_size = self.last_size = None
        self.day_high = self.day_low = self.day_open = None
        self.day_volume = None        # ⚠ CUMULATIVE and in LOTS (ticks 8/233),
                                      # the same convention as RTVolume. Do not
                                      # divide by 100 twice.
        self.avg_volume = None
        self.high_52w = self.low_52w = None
        self.vwap = None              # free inside the RTVolume payload
        self.last_exch_ms = None      # EXCHANGE stamp of the print, not arrival
        self.last_single_trade = None
        self.trade_count = self.trade_rate = self.volume_rate = None
        self.vol_3min = self.vol_5min = self.vol_10min = None
        self.shortable = self.shortable_shares = None
        self.halted = None
        # rolling windows, same pattern as _spread_hist: time-bounded deques fed
        # from update_quote, read through a current_*/\*_pct accessor.
        self._bid_hist = deque()      # (ts, bid) -> realised bid volatility
        self._tick_hist = deque()     # ts of every quote tick -> quote rate
        self._trade_through_n = 0     # prints strictly at/below the live stop
        self._tt_seen_ts = None       # last print already counted
        # surge print handed over by trade-mole at the buy-signal (the price the
        # stock actually just traded at). Anchors the entry reference alongside the
        # ask so a stale opening-snapshot ask can't underprice the entry; None in
        # standalone mode, where no print is available.
        self.trigger_print = None

        # order-id allocation (standalone counter; driven uses _id_alloc)
        self.next_order_id = None

        # order bookkeeping: orderId -> dict
        self.orders = {}
        self.entry_shares = 0
        self.active_buy_id = None
        self.entry_ts = None            # time.time() when the entry order was placed
        self.entry_failed = None        # terminal event str once the entry is deemed dead
        self.entry_why_held = ""        # IBKR whyHeld/warningText on a dead entry, if any
        self._entry_abort_pending = False  # timeout fired: cancel sent, awaiting resolution
        self._entry_cancel_ts = None    # time.time() when the timeout cancel was sent
        self.protective_id = None

        # ---- 3.0 entry ask-tracking state ----
        self.entry_ref = None           # trigger reference frozen at placement
        self.entry_ceiling = None       # entry_ref * (1 + entry_track_max_pct/100)
        self.entry_lmt = None           # the limit we last SENT (intended, not confirmed)
        self.entry_lmt_working = None   # the limit IBKR reports working, via on_open_order
        self.entry_capped_to = None     # price IBKR capped us to (2161), if any
        self._track_n = 0               # modifies sent on the entry this run
        self._track_last_ts = None      # time.time() of the last modify
        self._track_prev_lmt = None     # limit the last modify is REPLACING (rollback target)
        self._modify_reject_event = None  # one-shot MODIFY_REJECT_* label for the TSV
        self._track_stopped = None      # reason string once tracking is disabled for the run
        self._track_up_frozen = False   # 202: no more UP moves, DOWN ratchet stays live
        self._cap_rescue_used = False   # the single post-window 2161 shot has been spent
        self._cap_rescue_blocked = False # 202 said "too aggressive": never bid higher
        self._entry_dead_pending = None # (status, why_held, deadline) deferred abort
        # 3.1 regulatory price-wall state. entry_wall_obs is the EXACT wall out
        # of a 202 ("more aggressive than X"), entry_wall_ref is IBKR's own
        # reference price from the same message -- the only reading we ever get
        # of the number IBKR prices its cap against, and it lags the tape by
        # seconds. _retired_entry_ids holds entry orderIds superseded by a wall
        # retry: they are dead, but a late fill on one still has to be caught.
        self.entry_wall_obs = None
        self.entry_wall_ref = None
        self.entry_wall_ts = None
        self._entry_wall_retry_pending = False
        self._entry_retry_n = 0
        self._entry_retry_last_ts = None
        self._retired_entry_ids = set()
        self._wall_event = None         # one-shot ENTRY_WALL_*/ENTRY_RETRY_* TSV label
        self._prot_reject_n = 0         # protective cancel+replace attempts this run
        self._prot_reject_last_ts = None
        # 3.4 dead-exit supervisor (see EXIT_REARM_*). _exit_dead_since is the
        # first moment the resting exit READ dead, so the grace is measured from
        # the state itself and a cancel+replace in flight is never raced.
        self._exit_rearm_n = 0
        self._exit_rearm_ts = 0.0
        self._exit_dead_since = None
        self._exit_rearm_exhausted = False
        # 3.4 SELL-side regulatory wall, the mirror of entry_wall_obs. Parsed out
        # of a 202 on the PROTECTIVE id ("more aggressive than X" -- for a sell
        # that X is a FLOOR). Until now it was read inside the handler and thrown
        # away, so a repair had no way to avoid re-sending a refused price.
        self.exit_wall_obs = None
        self.exit_wall_ref = None
        self.exit_wall_ts = None
        self._pa_last_row_ts = 0.0      # TSV row throttle while ENTRY_PENDING
        self._capped_row_written = False
        self._residual_cancel_sent = False
        self.original_avg_fill = None   # frozen after first full entry fill
        self.running_entry_avg = None
        self.position = 0.0
        self.phase = PHASE_INIT

        # protective order's last sent state (to skip redundant replaces)
        self._prot_sent = None  # (otype, aux, lmt, qty)
        self.exiting = False    # protective order has been converted to exit LMT
        self.terminating = False

        # ratchet state (stop holds when price falls, only moves UP) - per cycle
        self.high_water = None       # peak bid (realizable price) since position opened
        self._aux_floor = None       # highest aux ever computed this cycle
        self._lmt_floor = None       # highest lmt ever computed this cycle
        # 3.2: the pure table-component ratchet, separate from the effective
        # levels above so the spread band can push the stop back down.
        self._aux_table_floor = None
        self._lmt_table_floor = None

        # --- exit-noise defences (2026-10-02) ---
        # Rolling (ts, spread%) samples; current_spread_pct() takes the MEDIAN so
        # a single wide quote cannot widen the stop. Bounded by the window, not
        # by a count, so a fast tape cannot grow it without limit.
        self._spread_hist = deque()
        # What compute_protective_levels() actually used on the last pass, for the
        # table. Kept off the return tuple so the 5-tuple signature (unpacked in
        # two places) does not change.
        self._spread_pct_used = None
        self._eff_trig = None
        self._eff_lim = None
        self._trigger_source = None   # bid+last | hard_floor | bid_only(unconfirmed-stale)
        self._deferred_n = 0          # how often (A) held a trade that would have exited
        self._exit_lmt_chase_n = 0
        self._exit_lmt_chase_ts = 0.0
        self._exit_lmt_sent = None    # last chased price, so an identical re-send is skipped
        self.exit_fill_px = None
        self.exit_fill_ts = None
        self._exit_ts = None          # when the synthetic trigger fired

    # ---------------------- connection helpers ----------------------
    def set_next_order_id(self, oid):
        with self.lock:
            if self.next_order_id is None or oid > self.next_order_id:
                self.next_order_id = oid

    def _alloc_id(self):
        # Driven mode: the shared client owns a single monotonic id stream.
        if self._id_alloc is not None:
            return self._id_alloc()
        with self.lock:
            oid = self.next_order_id
            self.next_order_id += 1
            return oid

    def set_contract_details(self, cd):
        with self.lock:
            self.contract = cd.contract
            try:
                if cd.minTick and cd.minTick > 0:
                    self.min_tick = cd.minTick
            except Exception:
                pass
            self.log.info("Contract resolved: %s conId=%s primaryExch=%s minTick=%s",
                          self.contract.symbol, self.contract.conId,
                          self.contract.primaryExchange, self.min_tick)

    # ---------------------- market data ----------------------
    def update_quote(self, bid=None, ask=None, last=None, close=None, mark=None,
                     bid_size=None, ask_size=None, last_size=None,
                     day_high=None, day_low=None, day_open=None,
                     day_volume=None, avg_volume=None,
                     high_52w=None, low_52w=None, vwap=None,
                     last_exch_ms=None, last_single_trade=None,
                     trade_count=None, trade_rate=None, volume_rate=None,
                     vol_3min=None, vol_5min=None, vol_10min=None,
                     shortable=None, shortable_shares=None, halted=None):
        """Record a quote field. 3.1 also stamps a per-field ARRIVAL time.

        There is no quote timestamp anywhere in the ibapi L1 surface — tickPrice
        carries none, and the only time-bearing ticks (45, 233/375) time the last
        TRADE, not the quote. So "how old is this ask" is unanswerable unless we
        stamp it ourselves on arrival, per field: bid, ask and last have wholly
        independent update sequences and a still-populated field is NOT evidence
        of a fresh one. YAAS 2026-08-27 is the case the stamps exist for — its
        ask arrived in the opening snapshot at 07:30:02.730 and had not moved
        21.5 s later when it priced the entry that IBKR then rejected, and
        nothing in this process could see that.

        Diagnostic only: nothing prices or gates an order off these stamps."""
        nudge = False
        now = time.time()
        with self.lock:
            if bid is not None:
                self.bid = bid
                self.bid_ts = now
            if ask is not None:
                # 3.0: a moved ask while the entry is working is the tracker's
                # only input, so wake the control loop instead of waiting out its
                # sleep. We do NOT re-price here — update_quote runs on the ibapi
                # reader thread, which in driven mode is SHARED by every duo on
                # that client (clerk-1.2.py:399-410); a blocking placeOrder here
                # would stall tick delivery for every concurrent session. All
                # order traffic stays on the per-run control-loop thread, which
                # also guarantees no orphan sends after T47 swaps session.xwing
                # (run_trade has already returned by then).
                nudge = (ask != self.ask and self.phase == PHASE_ENTRY_PENDING)
                self.ask = ask
                self.ask_ts = now
            if last is not None:
                self.last = last
                self.last_ts = now
            if close is not None:
                self.close = close
            if mark is not None:
                self.mark = mark
                self.mark_ts = now
            # 3.3: plain stores. Every one of these is record-only; none is read
            # by exit_decision() or by any order price. A size of 0 is a real
            # observation (the book was pulled) so `is not None` is the test, not
            # truthiness -- `if bid_size:` would silently drop exactly the event
            # these fields exist to capture.
            for _k, _v in (("bid_size", bid_size), ("ask_size", ask_size),
                           ("last_size", last_size), ("day_high", day_high),
                           ("day_low", day_low), ("day_open", day_open),
                           ("day_volume", day_volume), ("avg_volume", avg_volume),
                           ("high_52w", high_52w), ("low_52w", low_52w),
                           ("vwap", vwap), ("last_exch_ms", last_exch_ms),
                           ("last_single_trade", last_single_trade),
                           ("trade_count", trade_count),
                           ("trade_rate", trade_rate),
                           ("volume_rate", volume_rate),
                           ("vol_3min", vol_3min), ("vol_5min", vol_5min),
                           ("vol_10min", vol_10min), ("shortable", shortable),
                           ("shortable_shares", shortable_shares),
                           ("halted", halted)):
                if _v is not None:
                    setattr(self, _k, _v)
            # 3.3: quote-tick rate. A frozen feed and a quiet market look
            # identical in the tables today; this separates them.
            if bid is not None or ask is not None:
                self._tick_hist.append(now)
                _cut = now - MICRO_WINDOW_SEC
                while self._tick_hist and self._tick_hist[0] < _cut:
                    self._tick_hist.popleft()
            # 3.3: realised bid volatility -- a per-name noise scale that does
            # not assume the spread is the whole story.
            if bid is not None and bid > 0:
                self._bid_hist.append((now, bid))
                _cut = now - MICRO_WINDOW_SEC
                while self._bid_hist and self._bid_hist[0][0] < _cut:
                    self._bid_hist.popleft()
            # 3.2: sample the spread for the exit trigger's noise band. Only a
            # two-sided, positive, properly-ordered quote counts -- a phantom or
            # crossed quote would otherwise inflate the band and loosen the stop.
            if (bid is not None or ask is not None) and self.bid and self.ask:
                if self.ask > self.bid > 0:
                    m = (self.bid + self.ask) / 2.0
                    if m > 0:
                        self._spread_hist.append((now, (self.ask - self.bid) / m * 100.0))
                        cut = now - EXIT_SPREAD_WINDOW_SEC
                        while self._spread_hist and self._spread_hist[0][0] < cut:
                            self._spread_hist.popleft()
        if nudge:
            self.wake.set()

    def current_mid(self):
        """BID/ASK midpoint; fall back to last, then close."""
        with self.lock:
            if self.bid and self.ask and self.bid > 0 and self.ask > 0:
                return (self.bid + self.ask) / 2.0
            if self.last and self.last > 0:
                return self.last
            if self.close and self.close > 0:
                return self.close
            return None

    def current_spread_pct(self):
        """MEDIAN bid/ask spread over the last EXIT_SPREAD_WINDOW_SEC, in percent
        of the midpoint. Drives the exit trigger's noise band.

        The MEDIAN, not the instantaneous spread: the instantaneous value flickers
        as hard as the bid does, and a single wide quote arriving in the same pass
        as a dip would widen the stop at exactly the wrong moment. Falls back to
        the live spread when the window is empty (first pass after a fill), and to
        None when there is no usable two-sided quote at all -- callers then get no
        noise band and the table Trigger% stands alone."""
        with self.lock:
            now = time.time()
            cut = now - EXIT_SPREAD_WINDOW_SEC
            vals = [v for ts, v in self._spread_hist if ts >= cut]
            if vals:
                return statistics.median(vals)
            if self.ask and self.bid and self.ask > self.bid > 0:
                m = (self.bid + self.ask) / 2.0
                if m > 0:
                    return (self.ask - self.bid) / m * 100.0
            return None

    def microprice(self):
        """3.3: SIZE-WEIGHTED fair value between the bid and the ask.

        microprice = (bid*ask_size + ask*bid_size) / (bid_size + ask_size).
        The weights are crossed on purpose: a large bid size and a small ask size
        means the next trade is far likelier to happen at the ask, so the fair
        value sits HIGH, not low. This is the honest answer to the "the yield is
        measured on the bid so it is always negative at the fill" problem -- a
        1-lot bid 6% below a 50-lot ask barely moves the microprice, while the
        same bid drags the raw bid-based yield straight through the stop.

        RECORD-ONLY in 3.3. Nothing anchors on it yet; that decision wants the
        labelled corpus this change exists to collect. Degrades to the plain mid
        when either size is missing, and to current_mid() when a side is."""
        with self.lock:
            b, a = self.bid, self.ask
            bs, asz = self.bid_size, self.ask_size
        if not (b and a and b > 0 and a > 0):
            return self.current_mid()
        if bs is None or asz is None or (bs + asz) <= 0:
            return (b + a) / 2.0
        return (b * asz + a * bs) / (bs + asz)

    def touch_imbalance(self):
        """3.3: bid_size / (bid_size + ask_size) at the touch, 0..1.

        Above 0.5 the buyers are deeper than the sellers. Record-only."""
        with self.lock:
            bs, asz = self.bid_size, self.ask_size
        if bs is None or asz is None or (bs + asz) <= 0:
            return None
        return bs / (bs + asz)

    def quote_rate(self):
        """3.3: bid/ask ticks per second over MICRO_WINDOW_SEC.

        Near zero means the feed has stopped, not that the market is calm. The
        tables cannot currently tell those apart, and the difference decides
        whether a stop that did not fire was a decision or an absence."""
        with self.lock:
            now = time.time()
            cut = now - MICRO_WINDOW_SEC
            k = sum(1 for ts in self._tick_hist if ts >= cut)
        return k / MICRO_WINDOW_SEC

    def bid_vol_pct(self):
        """3.3: standard deviation of successive bid returns over the window, in
        percent. Needs 3 samples; None before that."""
        with self.lock:
            now = time.time()
            cut = now - MICRO_WINDOW_SEC
            vals = [v for ts, v in self._bid_hist if ts >= cut and v > 0]
        if len(vals) < 3:
            return None
        rets = [(vals[i] - vals[i - 1]) / vals[i - 1] * 100.0
                for i in range(1, len(vals)) if vals[i - 1] > 0]
        if len(rets) < 2:
            return None
        try:
            return statistics.stdev(rets)
        except statistics.StatisticsError:
            return None

    def px_vs_vwap_pct(self):
        """3.3: last print against the session VWAP, in percent. The VWAP comes
        free inside the RTVolume payload and is already the mole's direction
        gate (SURGE_MIN_PX_VS_VWAP_PCT)."""
        with self.lock:
            px, vw = self.last, self.vwap
        if not (px and vw and vw > 0):
            return None
        return (px - vw) / vw * 100.0

    def note_trade_through(self, aux):
        """3.3: count prints that actually traded at or below the live stop.

        `deferred_n` counts the times the BID crossed the stop with no trade
        agreeing. This is its complement: the times a TRADE did agree. Together
        they say whether a held trade was held correctly. Counted once per
        distinct print (keyed on last_ts), from the control loop."""
        with self.lock:
            if aux is None or not self.last or self.last_ts is None:
                return
            if self._tt_seen_ts == self.last_ts:
                return
            self._tt_seen_ts = self.last_ts
            if self.last <= aux:
                self._trade_through_n += 1

    def static_snapshot_str(self):
        """3.3: the once-per-session fields, packed. These arrive once and never
        move, so a column each would be 6 columns of repeated constants."""
        with self.lock:
            items = (("shortable", self.shortable),
                     ("shortable_shares", self.shortable_shares),
                     ("avg_volume", self.avg_volume),
                     ("open", self.day_open),
                     ("high52", self.high_52w),
                     ("low52", self.low_52w))
        return ";".join("%s=%s" % (k, "" if v is None else ("%g" % v))
                        for k, v in items)

    def market_snapshot_row(self):
        """3.3: the new append-only columns as one dict, so the three row
        builders (control loop, terminal row, post-exit watch) cannot drift
        apart. Formatting matches the rest of the table via _f()."""
        with self.lock:
            bs, asz, ls = self.bid_size, self.ask_size, self.last_size
            dh, dl, dv = self.day_high, self.day_low, self.day_volume
            vw, tc, tr, vr = self.vwap, self.trade_count, self.trade_rate, self.volume_rate
            v3, v5, v10 = self.vol_3min, self.vol_5min, self.vol_10min
            hl, ems, tt = self.halted, self.last_exch_ms, self._trade_through_n
        return dict(
            bid_size=_f(bs, 0), ask_size=_f(asz, 0), last_size=_f(ls, 0),
            microprice=_f(self.microprice()),
            touch_imbalance=_f(self.touch_imbalance(), 3),
            day_high=_f(dh), day_low=_f(dl), day_volume_lots=_f(dv, 0),
            vwap=_f(vw), px_vs_vwap_pct=_f(self.px_vs_vwap_pct(), 2),
            trade_count=_f(tc, 0), trade_rate=_f(tr, 2), volume_rate=_f(vr, 2),
            vol_3min=_f(v3, 0), vol_5min=_f(v5, 0), vol_10min=_f(v10, 0),
            halted=_f(hl, 0), quote_rate=_f(self.quote_rate(), 2),
            bid_vol_10s=_f(self.bid_vol_pct(), 3),
            trade_through_n=tt,
            last_exch_ts=(datetime.fromtimestamp(ems / 1000.0)
                          .isoformat(timespec="seconds") if ems else ""),
            static_snapshot=self.static_snapshot_str(),
        )

    def last_age(self):
        """Seconds since the last TRADE print arrived, or None if none has."""
        with self.lock:
            if self.last_ts is None:
                return None
            return max(0.0, time.time() - self.last_ts)

    def current_exit_ref(self):
        """Realizable SELL reference = the bid (where a marketable SELL fills),
        falling back to last, then close. Drives yield_pct and the protective stop
        so a wide/phantom ask can't inflate either. The `mid` column still reports
        the true (bid+ask)/2 for transparency. Note: deliberately does NOT fall back
        to current_mid() (which would reintroduce the ask)."""
        with self.lock:
            if self.bid and self.bid > 0:
                return self.bid
            if self.last and self.last > 0:
                return self.last
            if self.close and self.close > 0:
                return self.close
            return None

    def _flatten_price(self):
        """Marketable SELL price used only to FLATTEN on shutdown: the current
        bid (a SELL LMT at the bid crosses the spread and fills). Falls back
        bid -> mid -> last -> close."""
        with self.lock:
            if self.bid and self.bid > 0:
                return self.bid
            return self.current_mid()  # current_mid already does mid->last->close

    # ---------------------- order construction ----------------------
    def _base_order(self, action, qty, otype):
        o = Order()
        o.action = action
        o.orderType = otype
        o.totalQuantity = int(qty)
        o.tif = "GTC"
        o.outsideRth = True
        o.transmit = True
        o.eTradeOnly = False     # default True in ibapi 9.81 -> would reject
        o.firmQuoteOnly = False
        if self.args.account:
            o.account = self.args.account
        return o

    def limit_order(self, action, qty, lmt, use_price_mgmt_algo=False):
        o = self._base_order(action, qty, "LMT")
        o.lmtPrice = round_to_tick(lmt, self.min_tick)
        # Price Management Algo: entry-only (place_entry passes True). IBKR caps an
        # over-aggressive limit to the allowed band and works it instead of rejecting.
        if use_price_mgmt_algo:
            o.usePriceMgmtAlgo = True
        return o

    def stp_lmt_order(self, action, qty, aux, lmt):
        o = self._base_order(action, qty, "STP LMT")
        o.auxPrice = round_to_tick(aux, self.min_tick)
        o.lmtPrice = round_to_tick(lmt, self.min_tick)
        return o

    # ---------------------- placing ----------------------
    def _ref_snapshot(self):
        """Every price input to the entry decision, plus how old each one is.

        Exists because the forensics were impossible without it: trade-mole
        stops recording at the fire, so for CJMB's 2026-08-27 CHASE_CEIL — the
        modify that walked into IBKR's wall and killed a live order — there is
        no recorded last, mark or close ANYWHERE, only the 1 Hz bid/ask in the
        x-wing table. Caller need not hold self.lock; reads are atomic enough
        for a diagnostic and blocking the ibapi thread here would be worse."""
        now = time.time()

        def age(ts):
            return None if ts is None else round(now - ts, 3)

        return {
            "bid": self.bid, "ask": self.ask, "last": self.last,
            "close": self.close, "mark": self.mark,
            "trigger_print": self.trigger_print,
            "bid_age": age(self.bid_ts), "ask_age": age(self.ask_ts),
            "last_age": age(self.last_ts), "mark_age": age(self.mark_ts),
            "entry_lmt": self.entry_lmt, "entry_ref": self.entry_ref,
            "entry_ceiling": self.entry_ceiling,
            "capped_to": self.entry_capped_to,
        }

    def _ref_snapshot_str(self):
        """The snapshot as a compact k=v; string for logs and the TSV column."""
        return ";".join(
            "%s=%s" % (k, ("" if v is None else
                           ("%g" % v if isinstance(v, float) else v)))
            for k, v in self._ref_snapshot().items())

    def _wall_step_down(self, wall):
        """The highest limit IBKR will still accept strictly below `wall`.

        The 202 text is "at or more aggressive than X" — X ITSELF is refused —
        so landing exactly on the wall is landing on the reject. A price already
        below the wall only needs floor-rounding; a price that floor-rounds back
        onto the wall has to give up one more tick. YAAS: wall 2.99, tick 0.01
        -> 2.98. CJMB: wall 2.40396 -> 2.40 (already strictly below)."""
        t = effective_tick(wall, self.min_tick)
        b = floor_to_tick(wall, self.min_tick)
        if b >= wall - 1e-9:
            b = round(b - t, 6)
        return b

    def _wall_step_up(self, wall):
        """The SELL-side mirror: the lowest limit IBKR will still accept
        strictly ABOVE `wall`.

        For a sell, "more aggressive" means LOWER, so the wall is a FLOOR and a
        limit at or below it is refused — IVF 2026-08-17 tried 2.39 against a
        2.4262898 floor eleven times. Same off-by-one as the buy side: landing
        exactly on the wall is landing on the reject."""
        t = effective_tick(wall, self.min_tick)
        b = ceil_to_tick(wall, self.min_tick)
        if b <= wall + 1e-9:
            b = round(b + t, 6)
        return b

    def _wall_estimate(self):
        """STAGE 3 (dark): guess the wall from our own reference. None if off.

        ⚠ Our reference is NOT IBKR's. Measured, IBKR's lags at or below our
        last by 0-10%, so this is a calibration problem, not an arithmetic one —
        see ENTRY_WALL_REF_SOURCE. Ships disabled on purpose."""
        if not ENTRY_WALL_ESTIMATE_ENABLED:
            return None
        src = ENTRY_WALL_REF_SOURCE
        if src == "mark":
            ref = self.mark
        elif src == "min_last_mark":
            cands = [x for x in (self.last, self.mark) if x and x > 0]
            ref = min(cands) if cands else None
        else:
            ref = self.last
        if not ref or ref <= 0:
            return None
        est = ref * ENTRY_WALL_REF_MULT * (1.0 - ENTRY_WALL_SAFETY_PCT / 100.0)
        return floor_to_tick(est, self.min_tick)

    def _wall_bound(self):
        """The ceiling IBKR's regulatory price cap puts on our BUY limit, or
        +inf when we have no evidence of one. Caller holds self.lock.

        Precedence, strongest first:
          1. an OBSERVED 202 wall — exact, straight out of the message. Ground
             truth, and the only source that needs no inference at all.
          2. an OBSERVED 2161 cap — cap * ENTRY_WALL_OVER_CAP_MULT. IBKR caps at
             a tighter multiple of the same reference it walls at, so the cap is
             a MEASUREMENT of that reference. This is the branch that saves the
             CJMB/EHGO shape, where a cap always arrives BEFORE the chase that
             would otherwise walk into the wall.
          3. an ESTIMATE off our own reference (stage 3, off by default).

        ⚠ 2 is `elif`, not a second `if`: once a 202 has given us the exact wall,
        an older cap must not tighten it further.
        ⚠ Observed bounds are NEVER floored at the bid. The wall can legitimately
        sit BELOW the live ask — YAAS's wall was 2.99 against a stale 1-lot 3.05
        offer — and refusing to believe that is the entire bug this fixes. Only
        the ESTIMATE is floored, because an estimate that prices us under the
        market is the MAX_CAP_ENTRY_PERCENT failure 3.0 deleted (it put 7 of 317
        entries below the market, GLE -24.8% ... ICCM -70.4%)."""
        if not ENTRY_WALL_ENABLED:
            return float("inf")
        bound = float("inf")
        if self.entry_wall_obs is not None and self.entry_wall_obs > 0:
            bound = min(bound, self._wall_step_down(self.entry_wall_obs))
        elif self.entry_capped_to is not None and self.entry_capped_to > 0:
            bound = min(bound, floor_to_tick(
                self.entry_capped_to * ENTRY_WALL_OVER_CAP_MULT, self.min_tick))
        est = self._wall_estimate()
        if est is not None:
            if self.bid and self.bid > 0:
                est = max(est, ceil_to_tick(self.bid, self.min_tick))
            bound = min(bound, est)
        return bound

    def _entry_upper(self):
        """The single upper bound on the entry limit: our frozen ceiling AND
        IBKR's wall. Replaces every bare self.entry_ceiling on the entry path.

        With no cap and no 202 observed this is exactly entry_ceiling, so a run
        that never meets the wall behaves identically to 3.0."""
        ceil = self.entry_ceiling
        if ceil is None:
            return self._wall_bound()
        return min(ceil, self._wall_bound())

    def _select_entry_reference(self):
        """(src, ref) for the entry: the HIGHEST of ask / trigger-print / last,
        with close only as a last resort (it's yesterday's close, not today's
        tape). Extracted in 3.0 so the opening shot and the ceiling are anchored
        on exactly the same number. Caller holds self.lock."""
        cands = []
        if self.ask and self.ask > 0:
            cands.append(("ask", self.ask))
        if self.trigger_print and self.trigger_print > 0:
            cands.append(("print", self.trigger_print))
        if self.last and self.last > 0:
            cands.append(("last", self.last))
        if cands:
            return max(cands, key=lambda c: c[1])
        if self.close and self.close > 0:
            return "close", self.close
        return None, None

    def compute_entry_limit_price(self):
        """Price the OPENING entry shot and freeze the tracking anchors.

        ref = max(ask, trigger-print, last), close only as a fallback; the limit
        is ref * (1 + pad/100), ceil-rounded to a valid tick. The pad widens to
        STALE_QUOTE_ENTRY_PERCENT_PRICE when the ask is detectably stale (more
        than STALE_QUOTE_THRESHOLD_PCT below the trigger print) so the opening
        shot stays marketable off a stale opening-snapshot ask — from 3.0 that
        only has to survive one ~250 ms tracking interval, after which the
        tracker re-prices off the live ask.

        Side effect (3.0): freezes self.entry_ref and self.entry_ceiling. The
        ceiling is ref * (1 + entry_track_max_pct/100) and is the ONLY bound on
        the entry limit for the rest of the run — it replaces the last_close
        clamp, which is off by default (see MAX_CAP_ENTRY_PERCENT). Returns None
        when no price can be determined."""
        with self.lock:
            a = self.args
            if a.max_limit_entry_percent_price is not None:
                src, ref = self._select_entry_reference()
                if ref is None:
                    if a.entry_limit_price is not None:
                        self.log.warning("No ask/print/last/close at entry; using fixed "
                                         "--Entry-limit-price %.4f", a.entry_limit_price)
                        return ceil_to_tick(a.entry_limit_price, self.min_tick)
                    self.log.error("No ask/print/last/close and no --Entry-limit-price; "
                                   "cannot price entry")
                    return None
                # Widen the pad only when the ask is DETECTABLY stale: present, but
                # more than STALE_QUOTE_THRESHOLD_PCT below the trigger print.
                pad = a.max_limit_entry_percent_price
                stale = (self.ask and self.ask > 0
                         and self.trigger_print and self.trigger_print > 0
                         and self.ask < self.trigger_print
                         * (1 - STALE_QUOTE_THRESHOLD_PCT / 100.0))
                if stale:
                    pad = max(STALE_QUOTE_ENTRY_PERCENT_PRICE,
                              a.max_limit_entry_percent_price)
                # Freeze the tracking anchors on this reference.
                self.entry_ref = ref
                self.entry_ceiling = ceil_to_tick(
                    ref * (1 + (a.entry_track_max_pct or 0.0) / 100.0), self.min_tick)
                price = ceil_to_tick(ref * (1 + pad / 100.0), self.min_tick)
                # 3.1: bounded by the WALL as well as our own ceiling. On the
                # opening shot the wall is only known if a previous attempt in
                # this run already drew a 2161/202 (i.e. on a retry) — a first
                # shot normally sees +inf here and prices exactly as 3.0 did.
                upper = self._entry_upper()
                if price > upper:
                    self.log.info("Entry %.4f clamped to %.4f by the wall bound "
                                  "(ceiling %.4f)", price, upper, self.entry_ceiling)
                    price = upper
                # LEGACY last_close clamp: off by default in 3.0 (it priced 7 of
                # 317 entries below the market). Honoured only if explicitly set.
                if a.last_close_price is not None and a.max_cap_entry_percent is not None:
                    cap = round_to_tick(
                        a.last_close_price * (1 + a.max_cap_entry_percent / 100.0),
                        self.min_tick)
                    if price > cap:
                        self.log.info("Entry %.4f capped to %.4f (legacy last_close %.4f "
                                      "+ %.1f%%)", price, cap, a.last_close_price,
                                      a.max_cap_entry_percent)
                        price = cap
                        self.entry_ceiling = min(self.entry_ceiling, cap)
                self.log.info("Entry price: %s=%.4f * (1+%.2f%%) -> %.4f%s "
                              "[ceiling %.4f = ref +%.1f%%, last_close=%s]", src, ref,
                              pad, price,
                              (" [STALE ask=%.4f vs print=%.4f]"
                               % (self.ask, self.trigger_print)) if stale else "",
                              self.entry_ceiling, a.entry_track_max_pct or 0.0,
                              _f(a.last_close_price))
                return price
            if a.entry_limit_price is not None:
                return ceil_to_tick(a.entry_limit_price, self.min_tick)
            return None

    def on_buy_signal(self, last_ask=None, trigger_print=None):
        """The buy-signal entry point. In driven mode trade-mole calls this with
        the last Ask price AND the trigger print (the price the surge trade just
        executed at) it saw at the moment of trigger; in standalone the SIGUSR1
        helper calls it with None. Seeds the ask and the trigger print so the
        entry is priced off the higher of them (the ask alone is often stale in
        the opening snapshot). Idempotent: a second call after the entry is placed
        is ignored."""
        with self.lock:
            if self.active_buy_id is not None or self.stop_evt.is_set():
                self.log.info("on_buy_signal ignored (already armed/stopping)")
                return
            if last_ask is not None and last_ask > 0:
                self.ask = last_ask
                self.log.info("Buy-signal: using last_ask=%.4f from trade-mole", last_ask)
            if trigger_print is not None and trigger_print > 0:
                self.trigger_print = trigger_print
                self.log.info("Buy-signal: trigger_print=%.4f from trade-mole", trigger_print)
        self.place_entry()

    def place_entry(self):
        with self.lock:
            price = self.compute_entry_limit_price()
            if price is None or price <= 0:
                self.log.error("Cannot determine entry limit price; aborting entry")
                self.stop_evt.set()
                self.wake.set()
                return
            shares = int(math.floor(self.args.capital / price))
            if shares < 1:
                self.log.error("capital %.2f / entry %.4f < 1 share; nothing to do",
                               self.args.capital, price)
                self.stop_evt.set()
                self.wake.set()
                return
            # entry_shares is fixed HERE and never recomputed. If the tracker
            # raises the limit afterwards the notional overshoots `capital` by at
            # most the ceiling headroom (~3.5%, i.e. ~17c at CAPITAL=5.00) — that
            # is accepted. Re-sizing is not an option: totalQuantity must stay at
            # the ORIGINAL size on every modify (IBKR reads it as the cumulative
            # target, so sending "remaining" after a partial under-buys), and a
            # size below `filled` is an error whose code is not documented.
            self.entry_shares = shares
            oid = self._alloc_id()
            self.active_buy_id = oid
            self.entry_lmt = price
            self.orders[oid] = dict(action="BUY", otype="LMT", status="Submitted",
                                    filled=0.0, avg=0.0, role="entry")
            order = self.limit_order("BUY", shares, price,
                                     use_price_mgmt_algo=ENTRY_USE_PRICE_MGMT_ALGO)
            self.log.info("ENTRY: BUY LMT %d %s @ %.4f (orderId=%d)%s", shares,
                          self.symbol, order.lmtPrice, oid,
                          "" if self.args.entry_track_enabled else " [tracking OFF]")
            self.order_client.placeOrder(oid, self.contract, order)
            self.entry_ts = time.time()
            self.phase = PHASE_ENTRY_PENDING
            self._pa_last_row_ts = 0.0

    def _arm_wall_retry(self):
        """Is a re-place after a price-wall rejection allowed right now?

        Caller holds self.lock. Cheap pre-check on the ibapi thread; the real
        guards run again in retry_entry_after_wall on the control-loop thread,
        because between the two the order can fill or the run can end."""
        if not (ENTRY_WALL_ENABLED and ENTRY_WALL_RETRY_ENABLED):
            return False
        if self._entry_retry_n >= ENTRY_WALL_RETRY_MAX:
            self._wall_event = self._wall_event or "ENTRY_WALL_RETRY_REFUSED"
            return False
        if (self.terminating or self.stop_evt.is_set()
                or self._entry_abort_pending or self.position > 0):
            return False
        rec = self.orders.get(self.active_buy_id) or {}
        if rec.get("filled", 0.0) > 0:
            return False
        return True

    def retry_entry_after_wall(self):
        """Re-place a wall-killed entry on a NEW orderId, inside IBKR's wall.

        Control-loop thread ONLY — same rule as every other order send here.

        Why a new id: a 202 is a CONFIRMED cancel, and IBKR will not accept a
        placeOrder on an id it has already resolved (that is error 103,
        "Duplicate order id"). So an in-place re-price, which is what the whole
        rest of 3.0 does, is simply not available once the order is dead.

        ⚠ Why it fires even when the bound is UNDER the live ask: IBKR's
        reference lags the tape by seconds and then catches up. IVF 2026-08-17
        is the measurement, on the sell side of the same wall — the IDENTICAL
        2.39 limit was refused 11 times in 1.66 s against a stale 2.6899
        reference and then ACCEPTED at 09:41:06 once the reference had fallen to
        the real market. A resting limit costs nothing; a missed surge costs the
        entire trade (YAAS ran +60% after its 202). So "not marketable yet" is
        not a reason to sit out — it is the reason to be resting when it turns.

        Returns the TSV event string when it re-placed, else None."""
        with self.lock:
            if not self._entry_wall_retry_pending:
                return None
            if not self._arm_wall_retry():
                self._entry_wall_retry_pending = False
                return None
            a = self.args
            now = time.time()
            # Never extend the run: the fill timeout stays anchored on the FIRST
            # placement (see below), so a retry has only the remainder of it.
            # Below ~1.5 s there is not enough left for a fill to be plausible
            # and we would just be adding an order to IBKR's efficiency ratio.
            left = (a.entry_fill_timeout or 0.0) - (now - (self.entry_ts or now))
            if left < ENTRY_WALL_RETRY_MIN_LIFE_SEC:
                self._entry_wall_retry_pending = False
                self._wall_event = self._wall_event or "ENTRY_WALL_RETRY_REFUSED"
                self.log.warning("Wall retry refused: only %.2fs left of the "
                                 "%.1fs entry window", left, a.entry_fill_timeout or 0.0)
                return None
            if (self._entry_retry_last_ts is not None
                    and (now - self._entry_retry_last_ts)
                    < ENTRY_WALL_RETRY_MIN_GAP_SEC):
                return None            # keep pending; try again next pass
            target = self._entry_upper()
            if target is None or target <= 0 or target == float("inf"):
                self._entry_wall_retry_pending = False
                self._wall_event = self._wall_event or "ENTRY_WALL_RETRY_REFUSED"
                self.log.warning("Wall retry refused: no usable bound (%s)", target)
                return None
            if target < self.min_tick:
                self._entry_wall_retry_pending = False
                self._wall_event = self._wall_event or "ENTRY_WALL_RETRY_REFUSED"
                return None
            old_oid = self.active_buy_id
            orec = self.orders.get(old_oid) or {}
            filled = float(orec.get("filled", 0.0) or 0.0)
            # ⚠ REMAINING here, not the original size — and that is NOT a
            # contradiction of the comment in place_entry. That one governs
            # MODIFIES, where totalQuantity is IBKR's CUMULATIVE target for an
            # existing order, so sending the remainder after a partial under-
            # buys. This is a brand-new order with its own cumulative target, so
            # the remainder is exactly right. _arm_wall_retry already refuses
            # any partial, so in practice this equals entry_shares.
            qty = int(max(0, (self.entry_shares or 0) - int(filled)))
            if qty < 1:
                self._entry_wall_retry_pending = False
                return None
            if not ORDER_THROTTLE.acquire():
                return None            # keep pending; the bucket refills
            # Retire the dead id. It stays in self.orders so a late fill is still
            # attributable, but under a role no downstream consumer treats as the
            # live entry (T47 keys the current run on active_buy_id).
            if old_oid is not None:
                self._retired_entry_ids.add(old_oid)
                orec["role"] = "entry_retired"
                orec["status"] = orec.get("status") or "Cancelled"
            oid = self._alloc_id()
            self.active_buy_id = oid
            self.entry_lmt = target
            self.orders[oid] = dict(action="BUY", otype="LMT", status="Submitted",
                                    filled=0.0, avg=0.0, role="entry")
            order = self.limit_order("BUY", qty, target,
                                     use_price_mgmt_algo=ENTRY_USE_PRICE_MGMT_ALGO)
            # Per-ORDER state is meaningless on the new id and must be cleared,
            # or the first status on it is read against the dead order's history.
            self._track_prev_lmt = None
            self.entry_lmt_working = None
            self._entry_dead_pending = None
            self.entry_failed = None
            self.entry_why_held = ""
            if self.done_reason == "entry_inactive":
                self.done_reason = None
            self.phase = PHASE_ENTRY_PENDING
            self._track_stopped = None      # the new order may still ratchet DOWN
            self._pa_last_row_ts = 0.0
            # ⚠ Deliberately NOT reset:
            #   entry_ts        - the 10 s fill timeout stays anchored on the
            #                     FIRST placement. Letting a retry restart it
            #                     would quietly undo the 30->10 s cut, whose
            #                     evidence is that of 27 entries taking >1 s only
            #                     3 made money and every fill past 15 s but one
            #                     lost. A retry gets the REMAINDER, nothing more.
            #   _track_n        - the 20-modify budget is per RUN, not per order.
            #   _track_up_frozen / _cap_rescue_blocked - IBKR has just told us we
            #                     are too aggressive. The replacement tracks DOWN
            #                     only; it must not start climbing again.
            n = self._entry_retry_n
            self._entry_retry_n += 1
            self._entry_retry_last_ts = now
            self._entry_wall_retry_pending = False
            self.log.warning("WALL RETRY %d/%d: entry %s dead at %s -> re-placing as "
                             "%d @ %.4f (wall=%s ceiling=%s ask=%s, %.2fs left) | %s",
                             n + 1, ENTRY_WALL_RETRY_MAX, old_oid,
                             _f(orec.get("lmt") or self._track_prev_lmt), oid,
                             order.lmtPrice, _f(self.entry_wall_obs),
                             _f(self.entry_ceiling), _f(self.ask), left,
                             self._ref_snapshot_str())
            self.order_client.placeOrder(oid, self.contract, order)
            return "ENTRY_RETRY_%d" % n

    # ---------------------- entry ask-tracking (3.0) ----------------------
    def _stop_tracking(self, reason):
        """Disable further re-pricing for this run. The order itself is left
        exactly where it is — the existing dead-status / timeout paths own its
        fate. Idempotent; first reason wins."""
        if self._track_stopped is None:
            self._track_stopped = reason
            self.log.info("entry tracking stopped: %s (n=%d, lmt=%s)",
                          reason, self._track_n, _f(self.entry_lmt))

    def _entry_offset(self, ask):
        """Dollar offset to hold above `ask`: the percentage, floored at
        ENTRY_TRACK_MIN_OFFSET_TICKS real ticks. The floor only ever binds at
        $1-2, where one tick is already 0.5-1.0% of price; below $1 the tick is
        $0.0001 and the percentage always dominates."""
        pct = (self.args.max_limit_entry_percent_price or 0.0) / 100.0
        tick = effective_tick(ask, self.min_tick)
        return max(ask * pct, ENTRY_TRACK_MIN_OFFSET_TICKS * tick)

    def track_entry_limit(self):
        """Re-price the working entry so it holds ~offset above the LIVE ask.

        Called from the control-loop thread only (see the note in update_quote).
        UP moves are allowed for entry_track_up_window seconds and are bounded by
        the frozen ceiling; DOWN moves run for the whole pending life, gated by
        ENTRY_TRACK_DOWN_HYST_MULT so quote flicker costs nothing. Returns the
        event string when it re-priced, else None."""
        with self.lock:
            a = self.args
            if not a.entry_track_enabled or self._track_stopped is not None:
                return None
            # never touch an order that is resolving, gone, or being cancelled
            if (self.active_buy_id is None or self.entry_failed is not None
                    or self._entry_abort_pending or self.terminating
                    or self.stop_evt.is_set() or self.entry_lmt is None
                    or self.entry_ceiling is None):
                return None
            if self.phase != PHASE_ENTRY_PENDING:
                return None
            rec = self.orders.get(self.active_buy_id) or {}
            if rec.get("filled", 0.0) >= self.entry_shares:
                return None
            if self._track_n >= ENTRY_TRACK_MAX_MODIFIES:
                self._stop_tracking("budget_exhausted")
                return None
            ask = self.ask
            if not ask or ask <= 0:
                return None

            now = time.time()
            offset = self._entry_offset(ask)
            tick = effective_tick(ask, self.min_tick)
            working = self.entry_lmt
            upper = self._entry_upper()
            target = min(ceil_to_tick(ask + offset, self.min_tick), upper)
            # Dead band: at least one real tick, and at least a quarter of the
            # offset. IBKR's own OER guidance is to modify only when the old price
            # is no longer competitive and the new one is — this is that test.
            delta_floor = max(ENTRY_TRACK_MIN_DELTA_TICKS * tick,
                              ENTRY_TRACK_MIN_DELTA_FRAC * offset)

            # --- 2161 rescue arming (see ENTRY_CAP_RESCUE_ENABLED) --------------
            # Computed BEFORE the branch chain, because the rescue needs its own
            # branch: 3.0 nested it inside "do we want to move up?", and a capped
            # order under a FLAT ask can never open that gate -- our own limit is
            # already offset% above the ask, so `target` equals `working`. CAST
            # 2026-08-18 is the case: capped 0.9600 against an ask frozen at
            # 0.9700 for the full 10 s, 0.9943 > 0.9943 + 0.0061 is false, and the
            # entry died with n=0 modifies.
            #
            # ⚠ `entry_capped_to` is the ONLY honest source for the price IBKR is
            # working. `entry_lmt_working` is NOT a second opinion on it: openOrder
            # echoes the limit WE asked for, verified on every capped entry on
            # record (CAST openOrder 0.9943 vs cap 0.9600; MGRX 0.5915 vs 0.5764;
            # REBN 1.70 vs 1.63; BBLG 1.01 vs 0.9646; CAPS 0.3383 vs 0.3254; FEED
            # 0.449 vs 0.4346; RCT 0.244 vs 0.2377; XOS 2.31 vs 2.24). 3.0
            # PREFERRED it here, and since our limit always sits above the ask that
            # made `capped_inert` permanently False in production -- which, with
            # the nesting above, is why this rescue fired ZERO times across 10
            # capped entries in 6 days. (entry_lmt_working keeps its own, correct
            # job in on_open_order as the rejected-modify detector.)
            window_elapsed = bool(a.entry_track_up_window
                                  and self.entry_ts is not None
                                  and (now - self.entry_ts) > a.entry_track_up_window)
            capped_inert = (self.entry_capped_to is not None
                            and self.entry_capped_to < ask)
            # The room check belongs HERE, not after the branch chain: if we are
            # already at the ceiling there is nothing to give, and arming anyway
            # would swallow the elif and leave a stranded limit with no DOWN
            # ratchet for the rest of the run.
            rescue_armed = bool(ENTRY_CAP_RESCUE_ENABLED and capped_inert
                                and not self._cap_rescue_used
                                and not self._cap_rescue_blocked
                                and (self._track_up_frozen or window_elapsed)
                                and upper > working + (
                                    tick - 1e-9 if ENTRY_CAP_RESCUE_ONE_TICK
                                    else delta_floor))

            if target > working + delta_floor:
                # The normal up-follow. A capped run that can still legitimately
                # chase takes THIS path and never touches the rescue branch below
                # -- REBN legged up at 1.775 s, inside the window, and filled.
                if (not self._track_up_frozen and not rescue_armed
                        and window_elapsed):
                    # Freeze UP only. The DOWN ratchet must keep running for the
                    # rest of the pending life: 84.6% of fills land within 3 s and
                    # every deep-tail loser on record is >=7 s, so bidding higher
                    # after the window is exactly the behaviour the 30->10 s
                    # timeout cut removed — but a limit left stranded above a
                    # retreating ask is the stale ceiling we are here to kill.
                    self._track_up_frozen = True
                    self.log.info("entry up-window closed after %.1fs (lmt %.4f, n=%d); "
                                  "down ratchet stays live", a.entry_track_up_window,
                                  working, self._track_n)
                if self._track_up_frozen and not rescue_armed:
                    return None
                # 3.1: distinguish "we hit OUR ceiling" from "we hit IBKR's
                # wall" in the table — they need different fixes and conflating
                # them is what hid the CJMB shape for a week.
                event = ("CAP_RESCUE" if rescue_armed else
                         "CHASE_WALL" if (target >= upper - 1e-9
                                          and upper < self.entry_ceiling - 1e-9)
                         else "CHASE_CEIL" if target >= upper - 1e-9
                         else "CHASE_UP")
            elif rescue_armed:
                # IBKR is holding us UNDER the offer and the normal up-follow has
                # nothing it wants to do (flat or retreating ask). This is the
                # branch CAST needed. It sits BEFORE the DOWN ratchet on purpose:
                # while capped-and-inert the answer is to raise, never to lower.
                # Self-limiting -- once the ask falls to/below the cap the order is
                # no longer inert, capped_inert goes False, and DOWN takes over.
                event = "CAP_RESCUE"
            elif working > ask * (1 + ENTRY_TRACK_DOWN_HYST_MULT
                                  * (a.max_limit_entry_percent_price or 0.0) / 100.0):
                # The limit has become a stale ceiling well above the live offer.
                # This is the half of the tracker the evidence actually supports:
                # every one of the 20 entries that took >10 s to fill paid 100%
                # of a limit exactly like this one.
                target = min(ceil_to_tick(ask + offset, self.min_tick), upper)
                if working - target < delta_floor:
                    return None
                event = "CHASE_DOWN"
            else:
                return None

            if event == "CAP_RESCUE":
                # Spend the one shot on the FULL envelope, never `ask + offset`:
                # on a flat ask that is the limit we already hold, so the rescue
                # would modify 0.9943 -> 0.9943 and change nothing (CAST). Capped,
                # OUR limit is the envelope IBKR is allowed to work inside, so hand
                # it all of it. ⚠ A limit is not a fill: REBN's went to 1.75 and it
                # filled 1.72 (the then-ask), CAPS filled at its 0.3254 cap, well
                # under its 0.3383 limit. The bounded worst case is unchanged at
                # +ENTRY_TRACK_MAX_PCT over trigger_ref, which CHASE_CEIL already
                # reaches routinely.
                # Room was already proven in rescue_armed (elif path) or by
                # `target > working + delta_floor` with target <= ceiling (UP path),
                # so this can only ever raise the limit.
                # ⚠ 3.1: the envelope is now _entry_upper(), not the bare ceiling.
                # A capped order is capped BECAUSE it is near the wall, so this is
                # exactly where the old target was most likely to be a 202: CAST
                # 2026-08-18's rescue would have gone to 1.0300 against a wall
                # estimated at ~1.0252 and killed the order it was rescuing. It
                # now goes to 1.0176 — still 4.9% above CAST's 0.97 ask, so the
                # rescue's economics are unchanged in kind, but it survives.
                target = upper

            if self._track_last_ts is not None and \
                    (now - self._track_last_ts) < ENTRY_TRACK_MIN_INTERVAL_SEC:
                return None
            if not ORDER_THROTTLE.acquire():
                return None

            # Same orderId => modify in place. Same qty => the ORIGINAL size.
            order = self.limit_order("BUY", self.entry_shares, target,
                                     use_price_mgmt_algo=ENTRY_USE_PRICE_MGMT_ALGO)
            # Remember what this modify is REPLACING. If IBKR rejects it the order
            # keeps working at exactly this price, and entry_lmt below would other-
            # wise hold a limit that never existed. See _rollback_entry_lmt.
            self._track_prev_lmt = working
            self.entry_lmt = order.lmtPrice
            self._track_n += 1
            self._track_last_ts = now
            # Spend the rescue only now that the modify is actually on the wire.
            # Setting it where `rescue` is computed would let the throttle or the
            # MIN_INTERVAL gate above silently burn the single shot on a return
            # path that never sent anything.
            if event == "CAP_RESCUE":
                self._cap_rescue_used = True
            self.log.info("%s id=%d %s %.4f -> %.4f (ask %.4f, +%.2f%% vs ref %.4f, "
                          "n=%d)", event, self.active_buy_id, self.symbol, working,
                          order.lmtPrice, ask,
                          (order.lmtPrice / self.entry_ref - 1) * 100.0
                          if self.entry_ref else 0.0,
                          self.entry_ref or 0.0, self._track_n)
            self.order_client.placeOrder(self.active_buy_id, self.contract, order)
            # No _stop_tracking on CHASE_CEIL: at the ceiling the UP branch stops
            # firing on its own (target is clamped, so it can never clear the dead
            # band again), while the DOWN ratchet must stay available.
            return event

    def _rollback_entry_lmt(self, code):
        """A modify did NOT take: the order is still working at whatever it held
        before we tried to re-price it. Restore entry_lmt to that price.

        This is the whole fix for SCNX 2026-08-14. track_entry_limit reads
        entry_lmt as `working` and tests `target > working + delta_floor`; leaving
        it on the REJECTED price makes that test compare a target against itself,
        which can never be true, so the tracker silently concludes it has already
        done the job and never re-prices again for the rest of the run. SCNX sat
        0.5% under a motionless 0.4504 offer for 3.8 s that way and timed out while
        the stock ran +43.6%.

        Caller must hold self.lock. Idempotent; a no-op when no modify is
        outstanding (e.g. the error refers to the original placement)."""
        prev = self._track_prev_lmt
        if prev is None or self.entry_lmt is None or prev == self.entry_lmt:
            return False
        self.log.info("Entry limit rolled back %s -> %s (modify rejected, code %s)",
                      _f(self.entry_lmt), _f(prev), code)
        self.entry_lmt = prev
        self._track_prev_lmt = None
        return True

    def on_open_order(self, orderId, lmt_price, status):
        """Forwarded by the clerk (getattr-guarded) and by the standalone client.
        placeOrder is fire-and-forget with no ack, so entry_lmt is our INTENDED
        limit and this is the CONFIRMED one.

        ⚠ 2026-08-14: a gap between them does NOT mean we are capped — that was
        this method's original premise and it is falsified (see the comment below
        and the 2161 handler in on_order_error). It means the last MODIFY did not
        take."""
        with self.lock:
            if orderId != self.active_buy_id:
                return
            try:
                px = float(lmt_price)
            except (TypeError, ValueError):
                return
            if px > 0:
                self.entry_lmt_working = px
                tol = effective_tick(px, self.min_tick) / 2
                if not (self.entry_lmt is not None and px < self.entry_lmt - tol
                        and self.entry_capped_to is None):
                    return
                # This branch was written as a PRICE-CAP detector and has never once
                # detected a price cap. openOrder echoes the limit WE asked for, so a
                # 2161 is invisible here: all 7 genuine caps since 3.0 (REBN, MGRX,
                # CAPS, BBLG x2, FEED, DAIO) reported our own limit straight back
                # (MGRX: openOrder 0.5915 while IBKR's cap was 0.5764). Every one of
                # its 4 firings was instead a REJECTED MODIFY -- SCNX 08-14 and CAPS
                # 08-12, the only two 103s on record.
                #
                # So it is the SECOND trigger for _rollback_entry_lmt, ~130 ms behind
                # the error code but able to catch a modify dropped with NO error at
                # all, which nothing else in the process can see. The equality guard
                # is what makes it safe: we only claim the modify failed when IBKR is
                # working EXACTLY the price we tried to replace. A stale echo of some
                # older price, or a cap IBKR ever did report honestly, will not match
                # and cannot trigger a false rollback.
                if (self._track_prev_lmt is not None
                        and abs(px - self._track_prev_lmt) <= tol):
                    self.log.warning("Entry %d working at %.4f, below our limit %.4f "
                                     "(status=%s) -> the modify did NOT take; "
                                     "resyncing to the working price",
                                     orderId, px, self.entry_lmt, status)
                    self._rollback_entry_lmt("openOrder")
                    self._modify_reject_event = "MODIFY_REJECT_openOrder"
                    self.wake.set()
                else:
                    self.log.warning("Entry %d working at %.4f, below our limit %.4f "
                                     "(status=%s); no outstanding modify to blame",
                                     orderId, px, self.entry_lmt, status)

    def _signal_done(self, reason):
        """Position closed (or unrecoverable): record the reason and end the
        control loop. The actual on_done callback (clerk teardown) is fired by
        the control-loop runner AFTER the loop exits, off the ibapi thread."""
        with self.lock:
            if self.done_reason is None:
                self.done_reason = reason
        self.log.info("x-wing done: %s", reason)
        self.stop_evt.set()
        self.wake.set()

    def realized_yield_pct(self):
        """Actual yield on the round trip: (exit fill - entry fill) / entry fill.

        This is the number that was missing entirely. The control loop exits as
        soon as _signal_done sets stop_evt, which happens BEFORE the next pass,
        so no row was ever written carrying the exit fill -- realised profit and
        loss could not be computed from the tables at all. Returns None until the
        protective order actually fills."""
        with self.lock:
            ref = self.original_avg_fill or self.running_entry_avg
            if not ref or not self.exit_fill_px:
                return None
            return (self.exit_fill_px - ref) / ref * 100.0

    def _bid_floor_hit(self, ref, exit_ref):
        """Spread-widened loss cap on the realisable (bid) price.

        HARD_FLOOR_PCT alone on the bid would fire on noise -- VEEA's bid fell
        5.6% with `last` unmoved -- so the cap is widened by the name's own
        spread, the same reasoning as the trigger band."""
        if not (ref and exit_ref):
            return False
        sp = min(max(self.current_spread_pct() or 0.0, 0.0), EXIT_SPREAD_MAX_PCT)
        return exit_ref <= ref * (1 - (HARD_FLOOR_PCT + sp) / 100.0)

    def exit_decision(self, exit_ref, aux):
        """Decide whether x-wing's OWN (Python-side) stop fires this pass.

        Returns (fire, source). This replaces the bare `if exit_ref <= aux:`
        that ejected MEDS, VEEA, EDBL, IMCC, KIDZ and NAMI -- in all six the bid
        crossed aux while the last trade AND the ask were still at or above the
        entry fill, i.e. nothing had traded down at all. A bid is an offer that
        can be withdrawn for free; a trade cannot be un-made. So the bid alone no
        longer decides.

        Note what is NOT changed: the real IBKR STP LMT keeps resting at the
        Gateway at `aux` the whole time. Its triggerMethod is unset, so IBKR
        evaluates it on the LAST TRADED price -- which is why the broker never
        fired in any of those six cases. It stays as the backstop, and because
        (B) widens the spread band, aux (and therefore that backstop) also sits
        further away than it did.

        Three outcomes:
          hard_floor -- unconditional, no confirmation, no spread band.
          bid+last   -- the bid is at/below aux AND a real trade agrees.
          deferred   -- the bid is at/below aux but no trade does: HOLD.
        """
        with self.lock:
            ref = self.original_avg_fill or self.running_entry_avg
            last = self.last

            # --- the hard floor, first and unconditional ---------------------
            # Bounds the loss so the confirmation below can afford to be
            # generous. Measured on the last PRINT by default: the widest
            # bid-only excursion on record is VEEA at 5.6% (bid 2.50 -> 2.36 with
            # last still 2.50), so a 5% floor on the bid would fire on that same
            # noise and quietly undo the confirmation.
            if HARD_FLOOR_PCT and ref:
                floor_last = ref * (1 - HARD_FLOOR_PCT / 100.0)
                if HARD_FLOOR_ON_LAST and last and last > 0:
                    if last <= floor_last:
                        return True, "hard_floor"
                    # ⚠ The clause above caps the TRADED decline at
                    # HARD_FLOOR_PCT, but the sale happens at the BID, which is
                    # a spread lower -- so it does NOT by itself cap the realised
                    # loss at HARD_FLOOR_PCT. Replay of 2026-09-28..10-02 shows
                    # it firing at a bid of -8.57% (NUR) and -12.51% (QCLS).
                    # A plain bid floor at HARD_FLOOR_PCT is not the answer
                    # either: VEEA's bid fell 5.6% on noise alone with `last`
                    # unmoved, so a 5% bid floor would fire on that and undo the
                    # trade confirmation. So widen the bid floor by the spread --
                    # the same insight as the trigger band. A name whose spread
                    # is 12% (LRHC) simply cannot be exited for a 5% loss, and
                    # this makes that explicit instead of hiding it.
                    if self._bid_floor_hit(ref, exit_ref):
                        return True, "hard_floor_bid"
                elif HARD_FLOOR_ON_LAST:
                    # On the last-print basis but NO usable print exists. Use the
                    # spread-widened bid floor, not the bare one: a wide-spread
                    # name with a silent tape would otherwise be ejected at a 5%
                    # bid drop, which is inside its own noise.
                    if self._bid_floor_hit(ref, exit_ref):
                        return True, "hard_floor_bid"
                elif exit_ref and exit_ref <= floor_last:
                    # --hard-floor-on-bid: the operator asked for a literal
                    # HARD_FLOOR_PCT cap on the realisable price, un-widened.
                    return True, "hard_floor"

            if exit_ref is None or aux is None or exit_ref > aux:
                return False, None

            # --- the bid is at/below the stop: does a TRADE agree? -----------
            if not EXIT_REQUIRE_TRADE_CONFIRM:
                return True, "bid_only"
            age = self.last_age()
            if (not last) or last <= 0 or (age is not None
                                           and age > EXIT_CONFIRM_MAX_LAST_AGE_SEC):
                # Silent or stale tape: there is no trade signal to confirm
                # against, so withholding the exit here would leave the position
                # protected only by the hard floor. Let the bid stand alone and
                # say so in the table.
                return True, "bid_only_stale_last"
            if last <= aux:
                return True, "bid+last"
            self._deferred_n += 1
            return False, "deferred"

    def chase_exit_limit(self):
        """The exit LMT has not filled. Re-price it DOWN toward the live bid.

        LESL 2026-09-30: the bid gapped 0.2320 -> 0.1815 straight THROUGH aux
        0.2085, and the SELL LMT at 0.2074 then rested unfilled for 860+ seconds
        while the position bled. A stop-limit whose limit sits only 0.5% under
        its trigger cannot fill in a gap.

        Bounded and throttled deliberately. This exact code path once had no
        counter, no throttle and re-sent an IDENTICAL price: IVF 2026-08-17
        produced ELEVEN orders in 1.66 s, every one 202-rejected, while the
        position sat unprotected through a -8% move. So: at most
        EXIT_LMT_CHASE_MAX_N attempts, spaced by at least
        PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC, strictly DOWN-only so an identical
        price can never be re-sent, and abandoned entirely once IBKR has
        refused a price (_prot_reject_n > 0) -- a 202 means "too aggressive",
        and for a SELL that means too LOW, so pushing lower cannot help. The
        existing on_order_error handler owns that case and lifts the exit back
        above the wall."""
        if not EXIT_LMT_CHASE_ENABLED:
            return None
        with self.lock:
            if not self.exiting or int(round(self.position)) <= 0:
                return None
            if self._exit_lmt_chase_n >= EXIT_LMT_CHASE_MAX_N:
                return None
            if self._prot_reject_n:
                return None          # IBKR already refused a price; do not push lower
            now = time.time()
            if self._exit_ts is None or (now - self._exit_ts) < EXIT_LMT_CHASE_AFTER_SEC:
                return None
            if (now - self._exit_lmt_chase_ts) < PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC:
                return None
            px = self.bid
            if not px or px <= 0:
                return None
            px = round_to_tick(px, self.min_tick)
            if self._exit_lmt_sent is not None and px >= self._exit_lmt_sent:
                return None          # down-only; never re-send the same price
            self._exit_lmt_chase_n += 1
            self._exit_lmt_chase_ts = now
            self._exit_lmt_sent = px
            n = self._exit_lmt_chase_n
            self.log.warning("EXIT LMT unfilled %.1fs -> chase #%d down to %s",
                             now - self._exit_ts, n, _f(px))
            self.arm_or_replace_protective(px, force_exit=True, exit_lmt=px,
                                           reason=f"exit-chase#{n}")
            return f"EXIT_CHASE_{n}"

    def _note_exit_wall(self, msg):
        """Record the SELL-side regulatory wall out of a 202 on the protective id.

        The mirror of the entry_wall_obs block in on_order_error. For a SELL
        "more aggressive" means LOWER, so the number IBKR quotes is a FLOOR our
        limit must stay strictly above. Keep the HIGHEST floor we have been shown
        this run: IBKR's reference lags the tape, and a repair that clears the
        strictest reading we have seen is the one least likely to be refused
        again. Caller holds self.lock."""
        wall, ibkr_ref = _parse_wall_prices(msg)
        if not wall:
            return
        prev = self.exit_wall_obs
        self.exit_wall_obs = wall if prev is None else max(prev, wall)
        self.exit_wall_ref = ibkr_ref
        self.exit_wall_ts = time.time()
        self.log.warning("Protective exit hit IBKR's sell wall: floor=%s ref=%s "
                         "(band %s) | %s", _f(wall), _f(ibkr_ref),
                         _f(wall / ibkr_ref, 4) if ibkr_ref else "",
                         self._ref_snapshot_str())

    def _exit_repair_price(self, force_bid=False):
        """The SELL limit to use when re-placing a protective exit.
        Caller holds self.lock. `force_bid` is for the shutdown flatten, which
        must FILL and so can never rest at a ladder limit above the market.

        Two corrections over the 3.2 fallback, both paid for by JUNS 2026-10-06:

        1. It honours WHICH trigger fired. `_exit_lmt_sent is None` is already
           the control loop's record that the hard floor fired (see the
           synthetic-trigger block) and that the exit has to be marketable at the
           bid -- the hard floor IS the loss cap, and a limit resting above the
           market caps nothing. 3.2 reached for `_lmt_floor` unconditionally, so
           a hard-floor exit priced at the 2.50 bid was re-sent at the ladder's
           1.98 against a 2.55 market.
        2. It clears any sell wall already shown to us (see _note_exit_wall).
           Re-sending a refused price cannot work: IBKR refused it for a REASON.

        Bounded above by nothing, deliberately, exactly as the 3.1 comment says:
        an exit resting slightly high is recoverable on the next pass; one IBKR
        refuses outright leaves the position naked."""
        if (force_bid or self._trigger_source == "hard_floor"
                or self._exit_lmt_sent is None):
            px = self._flatten_price() or self._lmt_floor
        else:
            px = (self._exit_lmt_sent or self._lmt_floor
                  or self._flatten_price())
        if px is not None and self.exit_wall_obs:
            lifted = self._wall_step_up(self.exit_wall_obs)
            if lifted > px:
                self.log.warning("Exit repair price %s is at/below the observed "
                                 "sell wall %s -> lifting to %s",
                                 _f(px), _f(self.exit_wall_obs), _f(lifted))
                px = lifted
        return round_to_tick(px, self.min_tick) if px else None

    def rearm_dead_exit(self):
        """`exiting` is latched, a position is open, and no SELL can fill. Fix it.

        THE HOLE THIS CLOSES (JUNS 2026-10-06, 1 sh naked for 4 hours). The three
        guards in on_order_error's cancel+new fallback -- budget spent, min gap,
        empty token bucket -- each return after only `wake.set()`. They are right
        to refuse to storm the Gateway; what was missing is that nobody owned the
        state they leave behind. JUNS took the min-gap branch (the 202 landed
        111 ms after the replace) and the run then sat in phase EXITING with
        protective_status=Cancelled until the lifetime flatten four hours later.
        `chase_exit_limit` could not help: it bails outright on `_prot_reject_n`,
        by design, because pushing a refused SELL lower cannot work.

        So a dead resting exit is treated as a repair, not a terminal state.

        ⚠ A FRESH orderId every time. This is the first defect in the JUNS
        chain: the type-change modify (STP LMT -> LMT) on the live id 61 drew
        103 "Duplicate order id", and re-placing on a dead id draws it again.
        arm_or_replace_protective reuses self.protective_id by design -- same id
        means modify, which is what the trailing ladder wants -- so the id is
        cleared here before the call, never inside it.

        Bounded the same way every other order path in this file is bounded, for
        the same reason (IVF 2026-08-17: eleven orders in 1.66 s): at most
        EXIT_REARM_MAX_N attempts, spaced EXIT_REARM_MIN_GAP_SEC, only after the
        order has read dead for EXIT_REARM_AFTER_SEC, and each one through the
        global token bucket. Returns a TSV event label or None."""
        if not EXIT_REARM_ENABLED:
            return None
        with self.lock:
            qty = int(round(self.position))
            if not self.exiting or qty <= 0 or self.protective_id is None:
                return None
            rec = self.orders.get(self.protective_id, {})
            status = rec.get("status", "")
            if status not in ORDER_DEAD_STATUSES:
                # Alive, or no status yet. An unknown status is NOT treated as
                # dead on purpose: re-placing over a working exit would double
                # the sell side.
                self._exit_dead_since = None
                return None
            if rec.get("filled", 0.0) >= qty:
                return None          # dead because it finished
            now = time.time()
            if self._exit_dead_since is None:
                self._exit_dead_since = now
                self.log.warning("Protective exit %s reads %s with %d sh still "
                                 "open -> re-arm armed, %.1fs grace",
                                 self.protective_id, status, qty,
                                 EXIT_REARM_AFTER_SEC)
                return None
            if (now - self._exit_dead_since) < EXIT_REARM_AFTER_SEC:
                return None
            if self._exit_rearm_n >= EXIT_REARM_MAX_N:
                if not self._exit_rearm_exhausted:
                    self._exit_rearm_exhausted = True
                    # Log once, loudly. Nothing else in the process can close
                    # this position before the lifetime flatten, so this line is
                    # the operator's only warning that the share is naked.
                    self.log.error("Protective exit re-arm budget (%d) SPENT and "
                                   "%d sh still open at %s. THE POSITION IS "
                                   "UNPROTECTED — close it by hand. Last price "
                                   "tried %s, observed sell wall %s | %s",
                                   EXIT_REARM_MAX_N, qty, _f(self.original_avg_fill),
                                   _f(self._exit_lmt_sent),
                                   _f(self.exit_wall_obs),
                                   self._ref_snapshot_str())
                return None
            if (now - self._exit_rearm_ts) < EXIT_REARM_MIN_GAP_SEC:
                return None
            px = self._exit_repair_price()
            if not px:
                return None          # no usable price; try again next pass
            if not ORDER_THROTTLE.acquire():
                self.log.warning("Exit re-arm deferred: order token bucket empty")
                return None
            self._exit_rearm_n += 1
            self._exit_rearm_ts = now
            self._exit_dead_since = None
            n = self._exit_rearm_n
            dead_id = self.protective_id
            self.log.warning("EXIT RE-ARM #%d/%d: order %s is %s with %d sh open "
                             "-> new SELL LMT @ %s (trigger=%s, wall=%s)",
                             n, EXIT_REARM_MAX_N, dead_id, status, qty, _f(px),
                             self._trigger_source or "?", _f(self.exit_wall_obs))
            # fresh id, and _prot_sent cleared so no redundancy check can skip it
            self.protective_id = None
            self._prot_sent = None
            self._exit_lmt_sent = px
            self.arm_or_replace_protective(px, force_exit=True, exit_lmt=px,
                                           reason=f"re-arm#{n} after {status}")
            return f"EXIT_REARM_{n}"

    def compute_protective_levels(self, px):
        """Single source of truth for the protective STP LMT levels.

        `px` is the realizable price (the bid; see current_exit_ref). Tracks a
        high-water bid and RATCHETS the result: aux/lmt only ever move UP and HOLD
        when price falls, so the resting STP LMT behaves like a true native
        stop-limit (it actually triggers when price drops to aux) instead of
        floating down with the price. Returns (row_yield, trig%, lim%, aux, lmt)."""
        with self.lock:
            ref = self.original_avg_fill or self.running_entry_avg or px
            self.high_water = max(self.high_water or px, px)
            hw = self.high_water
            ypct = (hw - ref) / ref * 100.0 if ref else 0.0
            ythr, trig, lim = self.table.lookup(ypct)

            # 3.2 (B): the table Trigger%/Limit% are now a FLOOR, not the value.
            # The bid's own jitter scales with the spread, so a trigger narrower
            # than the spread is guaranteed to fire on noise -- v8's 1.0% inside a
            # 2.4% median spread is what ejected MEDS, VEEA and EDBL within
            # seconds of the fill. Widen both with the measured spread.
            sp = self.current_spread_pct()
            if EXIT_TRIGGER_SPREAD_MULT and sp is not None:
                sp = min(max(sp, 0.0), EXIT_SPREAD_MAX_PCT)
                eff_trig = max(trig, EXIT_TRIGGER_SPREAD_MULT * sp)
                # Keep the table's own trigger->limit gap unless the spread needs
                # a wider one: LESL's 0.5% gap left the exit limit resting
                # unfilled through a gap-down.
                eff_lim = eff_trig + max(lim - trig, EXIT_LMT_SPREAD_MULT * sp)
            else:
                sp = None
                eff_trig, eff_lim = trig, lim
            self._spread_pct_used, self._eff_trig, self._eff_lim = sp, eff_trig, eff_lim

            # The RATCHET applies to the table component only. `hw` is monotonic,
            # so hw*(1-trig/100) can only fall when the table ROW loosens (trig
            # grows as the yield rises) -- that is what the ratchet exists to
            # hold. Keeping the ratchet on the table value and applying the
            # spread band as a separate CAP means the profit ratchet survives
            # intact while the stop can still be pushed back DOWN when the
            # spread widens.
            aux_tbl = round_to_tick(hw * (1 - trig / 100.0), self.min_tick)
            lmt_tbl = round_to_tick(hw * (1 - lim / 100.0), self.min_tick)
            if self._aux_table_floor is not None:
                aux_tbl = max(aux_tbl, self._aux_table_floor)
            if self._lmt_table_floor is not None:
                lmt_tbl = max(lmt_tbl, self._lmt_table_floor)
            self._aux_table_floor, self._lmt_table_floor = aux_tbl, lmt_tbl

            # 3.2: the ratchet must NOT push the stop up INTO the noise band. The
            # profit ratchet cannot protect a gain that sits inside a spread we
            # would have to cross to realise it. This is exactly the MEDS
            # failure: aux had ratcheted to bid*0.99 and the bid's own 4-cent
            # oscillation then crossed it. Total risk stays bounded by
            # HARD_FLOOR_PCT instead. The cap is recomputed every pass, so aux
            # recovers upward again once the spread narrows.
            aux = min(aux_tbl, round_to_tick(hw * (1 - eff_trig / 100.0), self.min_tick))
            lmt = min(lmt_tbl, round_to_tick(hw * (1 - eff_lim / 100.0), self.min_tick))
            lmt = min(lmt, aux)          # keep limit at/below trigger (boundary safety)
            # _aux_floor/_lmt_floor stay the EFFECTIVE levels actually resting at
            # the broker: on_order_error's fallback and the `exiting` branch both
            # read them as the real held price.
            self._aux_floor, self._lmt_floor = aux, lmt
            return ythr, trig, lim, aux, lmt

    def arm_or_replace_protective(self, mid, *, aux=None, lmt=None,
                                  force_exit=False, exit_lmt=None, reason=""):
        """Place/replace the single resting protective SELL order (same orderId
        => modify, never cancel). When force_exit, convert it to a SELL LMT to
        force the exit: at the held table limit (exit_lmt, a synthetic stop
        trigger) or, when none is given, the bid used to flatten on shutdown
        (see _flatten_price)."""
        with self.lock:
            qty = int(round(self.position))
            if qty <= 0:
                return
            if force_exit:
                px = exit_lmt if exit_lmt is not None else (self._flatten_price() or mid)
                otype, aux, lmt = "LMT", None, round_to_tick(px, self.min_tick)
            else:
                if aux is None or lmt is None:
                    _, _, _, aux, lmt = self.compute_protective_levels(mid)
                otype = "STP LMT"

            sent = (otype, aux, lmt, qty)
            if sent == self._prot_sent and not force_exit:
                return  # nothing changed; skip redundant replace

            if self.protective_id is None:
                self.protective_id = self._alloc_id()
            oid = self.protective_id

            if otype == "LMT":
                order = self.limit_order("SELL", qty, lmt)
            else:
                order = self.stp_lmt_order("SELL", qty, aux, lmt)

            self.orders.setdefault(oid, dict(action="SELL", filled=0.0, avg=0.0,
                                             role="protective"))
            self.orders[oid].update(otype=otype, status="WorkingMod")
            self._prot_sent = sent
            self.order_client.placeOrder(oid, self.contract, order)
            verb = "EXIT->LMT@bid" if force_exit else "replace"
            self.log.info("STOP %s id=%d SELL %s qty=%d aux=%s lmt=%s %s", verb, oid,
                          otype, qty, aux, lmt, f"({reason})" if reason else "")

    # ---------------------- callbacks ----------------------
    def on_order_status_ex(self, orderId, status, filled, avg, last_fill,
                           why_held="", mkt_cap_price=0.0):
        """on_order_status + IBKR's mktCapPrice, which 3.0 discarded.

        mktCapPrice is the last positional arg of the ibapi orderStatus callback
        and is documented as "if an order has been capped, the current capped
        price" — i.e. the machine-readable twin of the price we currently have to
        REGEX out of the 2161 prose. Both clerks and this client already accepted
        the argument and dropped it on the floor.

        ⚠ Do NOT build on it. It is documented for the Price Management Algo and
        ENTRY_USE_PRICE_MGMT_ALGO is False, so the realistic expectation is 0.0
        forever; every cap we have ever observed arrived only as 2161 text. This
        exists as a zero-cost structured cross-check, and _parse_capped_price
        stays the primary source. It is a separate method, not a wider signature
        on on_order_status, so clerk-1.1 -> x-wing-2.1 keeps working untouched.
        """
        if (mkt_cap_price and mkt_cap_price > 0.0
                and orderId == self.active_buy_id
                and mkt_cap_price != self.entry_capped_to):
            self.entry_capped_to = mkt_cap_price
            self.log.warning("Entry %d capped to %.4f per orderStatus.mktCapPrice "
                             "(no 2161 text needed) | %s", orderId, mkt_cap_price,
                             self._ref_snapshot_str())
        return self.on_order_status(orderId, status, filled, avg, last_fill,
                                    why_held=why_held)

    def on_order_status(self, orderId, status, filled, avg, last_fill, why_held=""):
        with self.lock:
            o = self.orders.get(orderId)
            if o is None:
                return
            prev_filled = o.get("filled", 0.0)
            o["status"] = status
            o["filled"] = filled
            o["avg"] = avg
            newly = filled - prev_filled

            if o["action"] == "BUY":
                if newly > 0:
                    self.position += newly
                    self.running_entry_avg = avg or self.running_entry_avg
                # 3.1: a fill on an entry id we RETIRED after a wall re-place.
                # A 202 is a confirmed cancel, so this should be impossible —
                # every walled order in the corpus went straight to Cancelled
                # with nothing resting. Handle it anyway: the shares are real and
                # already counted into self.position above, so the protective
                # ladder will cover them and shutdown() will flatten them. Loud,
                # because it means the id retirement raced an execution.
                if newly > 0 and orderId in self._retired_entry_ids:
                    self.log.error("LATE FILL on RETIRED entry %d: %s sh @ %s after a "
                                   "wall re-place (live entry is %s). Position is now "
                                   "%s and is covered by the protective ladder.",
                                   orderId, newly, _f(avg), self.active_buy_id,
                                   self.position)
                    if self.done_reason is None:
                        self.done_reason = "entry_late_retired_fill"
                    self.wake.set()
                if orderId == self.active_buy_id and o.get("role") == "entry":
                    if filled > 0:
                        # Any fill ends tracking, including a PARTIAL one: we got
                        # shares at the price we asked for, so bidding higher for
                        # the remainder only worsens the average the whole yield
                        # ladder is measured against. The residual keeps working
                        # at its current limit and is cancelled at the timeout
                        # (see the partial-residual block in control_loop).
                        # Also clears a deferred abort — the order is plainly live.
                        self._entry_dead_pending = None
                        self._stop_tracking("filled")
                    if status == "Filled" and self.original_avg_fill is None:
                        self.original_avg_fill = avg
                        self.log.info("Entry FILLED: %d sh, original avg fill %.4f",
                                      int(filled), avg)
                    elif (filled <= 0 and self.entry_failed is None
                          and not self.terminating
                          and status in ENTRY_DEAD_STATUSES):
                        # IBKR deactivated/cancelled the unfilled entry (often with NO
                        # error() callback - see JZXN 2026-07-10). Flag it; the control
                        # loop stamps a terminal row and tears the duo down, freeing the
                        # pooled clerk client instead of idling in ENTRY_PENDING.
                        #
                        # 3.0 exception: a REJECTED MODIFICATION can surface as a
                        # transient "Cancelled" on an order that is in fact still
                        # live (the documented Submitted->Modified->Cancelled
                        # desync). Since we now send modifies, defer that one case
                        # by ENTRY_TRACK_STATUS_GRACE_SEC and take the abort only
                        # if no live status follows. Deliberately NOT applied to
                        # "Inactive": Part B is a sub-second Inactive and its
                        # 12-event/2790-run series must stay directly comparable.
                        # Our own timeout cancel is unaffected — tracking has
                        # already stopped by then (_entry_abort_pending).
                        modify_in_flight = (
                            status != "Inactive"
                            and self._track_last_ts is not None
                            and not self._entry_abort_pending
                            and (time.time() - self._track_last_ts)
                            < ENTRY_TRACK_STATUS_GRACE_SEC)
                        # 3.1 ⚠ THE ORDERING FIX. A 202 arrives AFTER the
                        # Cancelled it explains: YAAS 2026-08-27 set
                        # entry_failed at 07:30:24.544 and the 202 landed behind
                        # it, so by the time on_order_error could parse the wall
                        # the run was already terminal and control_loop was one
                        # pass from breaking. Without holding the abort open
                        # there is no run left to re-place INTO, and the retry
                        # below can never fire on the shape it exists for.
                        # (CJMB and EHGO only survived because a modify happened
                        # to be in flight, which is luck, not design.)
                        # Same grace, same Inactive exclusion: a Part-B Inactive
                        # still aborts instantly so that series stays comparable.
                        wall_retry_armed = (
                            ENTRY_WALL_ENABLED and ENTRY_WALL_RETRY_ENABLED
                            and status != "Inactive"
                            and not self._entry_abort_pending
                            and self._entry_retry_n < ENTRY_WALL_RETRY_MAX
                            and self.entry_ts is not None
                            and (time.time() - self.entry_ts)
                            < (self.args.entry_fill_timeout or 0.0))
                        if ((modify_in_flight or wall_retry_armed)
                                and self._entry_dead_pending is None):
                            self._stop_tracking("cancelled_after_modify"
                                                if modify_in_flight
                                                else "cancelled_wall_pending")
                            self._entry_dead_pending = (
                                status, why_held,
                                time.time() + ENTRY_TRACK_STATUS_GRACE_SEC)
                            self.log.warning("Entry order %d %s (%s) -> deferring abort "
                                             "%.2fs: %s", orderId, status,
                                             ("%.0f ms after a re-price"
                                              % ((time.time() - self._track_last_ts)
                                                 * 1000.0))
                                             if modify_in_flight else "no modify in flight",
                                             ENTRY_TRACK_STATUS_GRACE_SEC,
                                             "the MODIFY may be what was rejected"
                                             if modify_in_flight else
                                             "a price-wall error may still be in flight")
                            self.wake.set()
                        else:
                            self.entry_failed = "entry_inactive"
                            self.entry_why_held = why_held
                            self._stop_tracking("entry_dead")
                            if self.done_reason is None:
                                self.done_reason = "entry_inactive"
                            self.log.warning("Entry order %d %s with no fill (whyHeld=%r) "
                                             "-> aborting run", orderId, status, why_held)
                            self.wake.set()
                    elif (self._entry_dead_pending is not None
                          and status not in ENTRY_DEAD_STATUSES):
                        # the order is alive after all: only the modify was rejected
                        self._entry_dead_pending = None
                        self.log.info("Entry order %d back to %s — the modify was "
                                      "rejected, not the order", orderId, status)
                if newly > 0:
                    self.exiting = False
                    # 3.4: a new entry fill starts a new protective cycle, so the
                    # dead-exit supervisor's budget and its latched warning reset
                    # with it (the ratchet state below already does the same).
                    self._exit_rearm_n = 0
                    self._exit_rearm_ts = 0.0
                    self._exit_dead_since = None
                    self._exit_rearm_exhausted = False
                    self.wake.set()  # arm/replace protective ASAP
                self.log.info("BUY status id=%d %s filled=%.0f avg=%.4f%s", orderId,
                              status, filled, avg,
                              f" whyHeld={why_held!r}" if why_held else "")

            elif o["action"] == "SELL":
                if newly > 0:
                    self.position -= newly
                    self.log.info("STOP fill id=%d %s sold=%.0f @ %.4f pos=%.0f",
                                  orderId, status, newly, last_fill or avg,
                                  self.position)
                if status == "Filled" and orderId == self.protective_id:
                    # Protective SELL STP LMT fully filled -> position closed.
                    # NO re-entry (removed in 2.0): we are done.
                    # 3.2: capture the fill so the terminal row can report the
                    # realised yield. `avg` is the order's average fill price;
                    # last_fill is the most recent partial, used only as a
                    # fallback when the broker sends no average.
                    self.exit_fill_px = avg or last_fill or self.exit_fill_px
                    self.exit_fill_ts = datetime.now().isoformat(timespec="seconds")
                    rz = self.realized_yield_pct()
                    self.log.info("EXIT fill @ %s (entry %s) -> realised %s",
                                  _f(self.exit_fill_px), _f(self.original_avg_fill),
                                  (f"{rz:+.2f}%" if rz is not None else "n/a"))
                    self._prot_sent = None
                    self.exiting = False
                    self.log.info("Protective filled; position closed (no re-entry)")
                    self.wake.set()
                    if not self.terminating:
                        self._signal_done("stop_filled")

    def on_order_error(self, reqId, code, msg):
        with self.lock:
            # --- 3.0: process-wide message-rate brake -------------------------
            # Not scoped to an order (reqId is often -1 on a 100) and not scoped
            # to this run: TWS eventually CLOSES the connection if the 50 msg/s
            # cap keeps being breached, and every session shares that Gateway.
            if code == MSG_RATE_ERROR_CODE:
                ORDER_THROTTLE.back_off(MSG_RATE_BACKOFF_SEC)
                self.log.warning("IBKR message-rate cap hit (code 100) -> suspending ALL "
                                 "entry tracking for %.0fs: %s", MSG_RATE_BACKOFF_SEC, msg)
                return

            rec = self.orders.get(reqId)
            is_entry = bool(rec and rec.get("role") == "entry")

            # --- 3.0: IBKR's regulatory price cap on the entry ----------------
            if is_entry and code == ENTRY_CAP_WARN_CODE:
                capped = _parse_capped_price(msg)
                first = self.entry_capped_to is None
                if capped:
                    self.entry_capped_to = capped
                if first:
                    # log ONCE per run: a tracked entry can draw a fresh 2161 on
                    # every re-price and the message is ~1 kB each time.
                    ref_ask = self.ask or 0.0
                    self.log.warning("Entry %d PRICE-CAPPED by IBKR to %s (our limit %s, "
                                     "ask %s, cap/ask %s). Order is still ALIVE. | %s",
                                     reqId,
                                     _f(capped), _f(self.entry_lmt), _f(ref_ask),
                                     _f(capped / ref_ask, 3) if (capped and ref_ask) else "",
                                     self._ref_snapshot_str())
                    self.wake.set()   # let the loop stamp an ENTRY_CAPPED row
                return

            # --- 3.0: "limit price too aggressive" on the entry ---------------
            # IBKR's HARD wall. 3.1 measured it: the wall is ITS reference price
            # times exactly 1.196 on the buy side (and 0.902 on the sell side),
            # to 6+ digits on every observation on record — YAAS 2.50->2.99,
            # CJMB 2.01->2.40396, EHGO 1.75->2.093, IVF sell 2.6899->2.4262898.
            # That is the Clearly-Erroneous outside-RTH guideline (20% sub-$25)
            # less a haircut. ⚠ The reference is IBKR's own and LAGS the tape by
            # seconds, so a limit well inside OUR 6% ceiling can still trip it,
            # and conversely a price refused now can be accepted moments later
            # once the reference catches up (IVF 2026-08-17: the identical 2.39
            # was rejected 11 times in 1.66 s and then accepted 5 s later).
            # Freeze the upward follow; keep the down ratchet; let the existing
            # dead-status / timeout paths own the order's fate.
            if is_entry and code in ENTRY_WALL_BLOCK_CODES:
                # 202 is ALSO the acknowledgement of a cancel WE requested, and it
                # carries the same code with an empty reason. MGRX 2026-08-13 logged
                # "aggressive-limit wall" 96 ms after its own timeout-cancel, which
                # is purely misleading — and now it would also block the 2161 rescue
                # on a run that never hit the wall. If our cancel is in flight, this
                # is that ack: record it and leave the tracking flags alone.
                if self._entry_abort_pending:
                    self.log.info("Entry %d cancel acknowledged (202): %s", reqId, msg)
                    return
                self._track_up_frozen = True
                self._cap_rescue_blocked = True   # IBKR says too aggressive: never bid higher
                # 3.1: the message carries the EXACT wall and IBKR's own
                # reference. That wall is ground truth — better than any estimate
                # we can build — so record it and let _wall_bound() bind every
                # later price in this run to it, including a re-place.
                wall, ibkr_ref = _parse_wall_prices(msg)
                if wall:
                    prev = self.entry_wall_obs
                    if ENTRY_WALL_OBS_LATEST or prev is None:
                        self.entry_wall_obs = wall
                    else:
                        self.entry_wall_obs = min(prev, wall)
                    self.entry_wall_ref = ibkr_ref
                    self.entry_wall_ts = time.time()
                    if self._wall_event is None:
                        self._wall_event = "ENTRY_WALL_%d" % code
                self.log.warning("Entry %d hit IBKR's aggressive-limit wall (%d) at "
                                 "%s -> wall=%s ref=%s (band %s) | %s | %s", reqId,
                                 code, _f(self.entry_lmt), _f(wall), _f(ibkr_ref),
                                 _f(wall / ibkr_ref, 4) if (wall and ibkr_ref) else "",
                                 self._ref_snapshot_str(), msg)
                # The order is DEAD (a 202 is a confirmed cancel). Arm the
                # re-place; the control loop owns the actual send.
                if self._arm_wall_retry():
                    self._entry_wall_retry_pending = True
                self.wake.set()
                return

            # --- 3.0: modify-class errors on the ENTRY id ---------------------
            # 2.1 consulted MODIFY_REJECT_CODES only for the protective order, so
            # these were dropped. 10147/10148 are the benign race (a modify that
            # crossed a fill/cancel); the rest mean the modification did not take.
            # Either way: stop tracking, keep the order, resync from orderStatus.
            # Never blind-retry — that is what turns one bad modify into a storm.
            # --- the ONE retryable modify error: the 103 registration race ------
            # The order is untouched and still working at the previous limit, so
            # the only thing broken is our MODEL of it. Roll that back and keep
            # tracking: the next quote re-issues the identical modify, held at
            # least ENTRY_TRACK_MIN_INTERVAL_SEC (0.25 s) by the throttle, by which
            # point the Gateway has registered the order (orderStatus ack latency
            # is median 149 ms / p90 577 ms over the 36 entries since 3.0). This is
            # NOT the blind retry the stop codes forbid: it is bounded by
            # ENTRY_TRACK_MAX_MODIFIES, the 3 s UP window and the global token
            # bucket, and each attempt must independently clear the dead band.
            if is_entry and code in ENTRY_MODIFY_RETRY_CODES:
                self._rollback_entry_lmt(code)
                self._modify_reject_event = f"MODIFY_REJECT_{code}"
                self.log.warning("Entry %d modify REJECTED (code %d): %s -> order still "
                                 "working at %s; retrying on the next quote (n=%d)",
                                 reqId, code, msg, _f(self.entry_lmt), self._track_n)
                self.wake.set()   # stamp the row and re-price without waiting 1 s
                return

            if is_entry and code in ENTRY_TRACK_STOP_CODES:
                # Same rollback: even when we stop tracking, entry_lmt must name the
                # price IBKR is really working or the TSV and every later log line
                # describe an order that does not exist.
                self._rollback_entry_lmt(code)
                self._modify_reject_event = f"MODIFY_REJECT_{code}"
                self._stop_tracking(f"modify_error_{code}")
                self.log.info("Entry %d modify-class error %d: %s", reqId, code, msg)
                return

            if (is_entry and rec.get("filled", 0.0) <= 0
                    and code in ENTRY_REJECT_CODES):
                self._stop_tracking(f"entry_reject_{code}")
                self.log.error("Entry order %d rejected (code %d): %s; aborting run",
                               reqId, code, msg)
                rec["status"] = "Rejected"
                self.stop_evt.set()
                self.wake.set()  # interrupt the 1 Hz control-loop sleep so it exits promptly
                return
            if reqId == self.protective_id and self.exiting and code in MODIFY_REJECT_CODES:
                # documented fallback: type-change modify rejected -> cancel+new
                #
                # ⚠ 3.1 BOUNDS THIS. As shipped it had no counter, no throttle
                # and no gap, and it re-sent the IDENTICAL price each time, so a
                # standing rejection became an order storm: IVF 2026-08-17 burned
                # orderIds 34..44 — eleven orders in 1.66 s, each 202-rejected and
                # each also drawing a 10148 — while the position was UNPROTECTED
                # for ~6.4 s. That is precisely the unbounded retry loop IBKR's
                # Order Efficiency Ratio penalises, and it never could have
                # succeeded: the price was refused for a REASON.
                now = time.time()
                # 3.4: a 202 on the protective id carries the EXACT sell-side wall
                # and IBKR's own reference. 3.2 parsed it inside this handler and
                # threw it away, so every later repair was free to re-send a price
                # IBKR had already refused. Record it FIRST — the guards below
                # return early, and the reading is just as valuable on the attempt
                # we decline to make as on the one we do.
                if code == ENTRY_TOO_AGGRESSIVE_CODE:
                    self._note_exit_wall(msg)
                if self._prot_reject_n >= PROTECTIVE_MODIFY_RETRY_MAX:
                    # ⚠ 3.2 said here that "the control loop will flatten". It
                    # did NOT: nothing in the loop looked at the resting exit's
                    # status, and the only flatten is the one at lifetime end.
                    # rearm_dead_exit now owns this state, so the handover is
                    # named instead of assumed.
                    self.log.error("Protective modify rejected (code %d) but the "
                                   "cancel+new budget (%d) is spent — leaving the "
                                   "resting exit alone. Position %s: handing over "
                                   "to the dead-exit supervisor (re-arm %d/%d "
                                   "used).", code, PROTECTIVE_MODIFY_RETRY_MAX,
                                   self.position, self._exit_rearm_n,
                                   EXIT_REARM_MAX_N)
                    self.wake.set()
                    return
                if (self._prot_reject_last_ts is not None
                        and (now - self._prot_reject_last_ts)
                        < PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC):
                    self.log.warning("Protective modify rejected (code %d) %.0f ms "
                                     "after the last replace — spacing it by %.2fs "
                                     "instead of storming",
                                     code, (now - self._prot_reject_last_ts) * 1000.0,
                                     PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC)
                    self.wake.set()
                    return
                if not ORDER_THROTTLE.acquire():
                    self.log.warning("Protective cancel+new deferred: order token "
                                     "bucket empty")
                    self.wake.set()
                    return
                self.log.warning("Protective modify rejected (code %d); cancel+new "
                                 "exit (%d/%d)", code, self._prot_reject_n + 1,
                                 PROTECTIVE_MODIFY_RETRY_MAX)
                try:
                    self.order_client.cancelOrder(self.protective_id)
                except Exception:
                    pass
                # 3.4: ONE source of truth for the repair price, and it honours
                # which trigger fired. 3.2 reached for _lmt_floor unconditionally,
                # which is the LADDER's limit — so a hard-floor exit whose own
                # price was the 2.50 bid went out at 1.98 (JUNS 2026-10-06) and
                # the 202 that followed was arithmetically certain.
                px = self._exit_repair_price()
                self._prot_reject_n += 1
                self._prot_reject_last_ts = now
                qty = int(round(self.position))
                if qty > 0 and px:
                    oid = self._alloc_id()
                    self.protective_id = oid
                    self.orders[oid] = dict(action="SELL", otype="LMT",
                                            status="Submitted", filled=0.0, avg=0.0,
                                            role="protective")
                    self.order_client.placeOrder(oid, self.contract,
                                                 self.limit_order("SELL", qty, px))
                    self._prot_sent = ("LMT", None, round_to_tick(px, self.min_tick), qty)

    # ---------------------- session helpers ----------------------
    def now_et(self):
        return datetime.now(ET) if ET else datetime.now()

    def is_rth(self):
        n = self.now_et()
        if n.weekday() >= 5:
            return False
        return dtime(9, 30) <= n.time() < dtime(16, 0)

    def session_label(self):
        return "RTH" if self.is_rth() else "ETH"

    def session_end_reached(self):
        if self.args.session_end is None:
            return False
        return self.now_et().time() >= self.args.session_end


# --------------------------------------------------------------------------- #
# Main control loop
# --------------------------------------------------------------------------- #
def control_loop(ctl: XWingController, start_ts: float):
    a = ctl.args
    while not ctl.stop_evt.is_set():
        ctl.wake.clear()
        mid = ctl.current_mid()
        exit_ref = ctl.current_exit_ref()   # bid-based realizable price (yield + stop basis)

        # 3.0: re-price the working entry BEFORE the rest of the pass, so a moved
        # ask is acted on in the same wake-up that delivered it. This is the only
        # place in the process that sends entry order traffic, and it runs on the
        # per-run control-loop thread (never the shared ibapi reader).
        track_event = ctl.track_entry_limit()
        # 3.1: a wall-killed entry gets ONE bounded re-place, on this thread,
        # BEFORE the deferred-abort resolution and the entry_failed break below
        # -- once entry_failed sticks, the loop breaks and there is no run left
        # to re-place into.
        retry_event = ctl.retry_entry_after_wall()
        if retry_event:
            track_event = (f"{track_event}+{retry_event}" if track_event
                           else retry_event)

        with ctl.lock:
            bid, ask = ctl.bid, ctl.ask
            pos = int(round(ctl.position))
            ref = ctl.original_avg_fill or ctl.running_entry_avg
            ypct = ((exit_ref - ref) / ref * 100.0) if (exit_ref and ref) else None
            event = track_event or ""
            if ctl.entry_capped_to is not None and not ctl._capped_row_written:
                ctl._capped_row_written = True
                event = "ENTRY_CAPPED"
            # A rejected modify is set from the ibapi thread (on_order_error /
            # on_open_order) and drained here. Concatenated rather than overriding,
            # because the retry it triggers can land in the same pass and losing
            # either half would make the run unreadable after the fact.
            if ctl._modify_reject_event:
                event = (f"{event}+{ctl._modify_reject_event}" if event
                         else ctl._modify_reject_event)
                ctl._modify_reject_event = None
            # 3.1: same drain-and-concatenate as above. A 202 and the re-place it
            # triggers land in the SAME control-loop pass, so dropping either half
            # makes the run unreadable after the fact.
            if ctl._wall_event:
                event = (f"{event}+{ctl._wall_event}" if event else ctl._wall_event)
                ctl._wall_event = None

            # 3.0: resolve a deferred abort. on_order_status parks a Cancelled that
            # landed within ENTRY_TRACK_STATUS_GRACE_SEC of a re-price, in case only
            # the MODIFY was rejected; if the grace expires with no live status, the
            # order really is gone and we take the abort we would have taken in 2.1.
            if ctl._entry_dead_pending is not None and pos <= 0:
                st, wh, deadline = ctl._entry_dead_pending
                if time.time() >= deadline:
                    ctl._entry_dead_pending = None
                    if ctl.entry_failed is None and not ctl.terminating:
                        ctl.entry_failed = "entry_inactive"
                        ctl.entry_why_held = wh
                        if ctl.done_reason is None:
                            ctl.done_reason = "entry_inactive"
                        ctl.log.warning("Entry %s %s not revived within %.2fs -> aborting "
                                        "run", ctl.active_buy_id, st,
                                        ENTRY_TRACK_STATUS_GRACE_SEC)

            # entry never established: IBKR-deactivated (on_order_status set entry_failed)
            # or still unfilled past the timeout backstop. On timeout we CANCEL the resting
            # BUY (never leave it working -> silent stop-less fills) and let on_order_status
            # resolve it: a clean cancel becomes entry_inactive (terminal row + teardown);
            # a late fill lands as pos>0 and the protective block below arms the ladder. Only
            # if the broker confirms neither within ENTRY_CANCEL_CONFIRM_SEC do we force-abort.
            if (ctl.entry_failed is None and ctl.active_buy_id is not None and pos <= 0
                    and ctl.entry_ts is not None and a.entry_fill_timeout
                    and (time.time() - ctl.entry_ts) >= a.entry_fill_timeout):
                if not ctl._entry_abort_pending:
                    ctl._entry_abort_pending = True
                    ctl._entry_cancel_ts = time.time()
                    ctl._stop_tracking("entry_timeout")
                    ctl.order_client.cancelOrder(ctl.active_buy_id)
                    ctl.log.warning("Entry %s unfilled after %.0fs -> cancelling resting BUY",
                                    ctl.active_buy_id, a.entry_fill_timeout)
                elif (time.time() - ctl._entry_cancel_ts) >= ENTRY_CANCEL_CONFIRM_SEC:
                    ctl.entry_failed = "entry_timeout"
                    if ctl.done_reason is None:
                        ctl.done_reason = "entry_timeout"
                    ctl.log.warning("Entry %s cancel unconfirmed after %.0fs -> aborting run",
                                    ctl.active_buy_id, ENTRY_CANCEL_CONFIRM_SEC)
            if ctl.entry_failed and pos <= 0:
                event = ctl.entry_failed
                ctl.phase = PHASE_ENTRY_FAILED

            # 3.0: PARTIALLY filled entry still working past the timeout. The
            # timeout above is guarded by pos <= 0, so in 2.1 the residual BUY
            # rested GTC for the whole session — and T47 then hard-stops the trio
            # on any residual position at run end (t47:residual_position).
            # Stepping the limit through price levels makes partials more likely,
            # so cancel the remainder and let the protective ladder cover exactly
            # what filled. Fire-and-forget: whatever fills before the cancel lands
            # simply grows `position` and the ladder resizes on the next pass.
            if (pos > 0 and not ctl._residual_cancel_sent
                    and ctl.active_buy_id is not None and ctl.entry_ts is not None
                    and a.entry_fill_timeout
                    and (time.time() - ctl.entry_ts) >= a.entry_fill_timeout):
                erec = ctl.orders.get(ctl.active_buy_id, {})
                if (erec.get("status") not in ("Filled", "Cancelled", "ApiCancelled",
                                               "Inactive")
                        and erec.get("filled", 0.0) < ctl.entry_shares):
                    ctl._residual_cancel_sent = True
                    ctl._stop_tracking("partial_residual_cancel")
                    ctl.order_client.cancelOrder(ctl.active_buy_id)
                    ctl.log.warning("Entry %s partially filled (%.0f/%d) after %.0fs -> "
                                    "cancelling the residual", ctl.active_buy_id,
                                    erec.get("filled", 0.0), ctl.entry_shares,
                                    a.entry_fill_timeout)

            # protective management while long
            row_thr = trig = lim = aux = lmt = None
            if pos > 0 and exit_ref is not None:
                if ctl.exiting:
                    # The resting exit LMT is left alone (spec step 5); show held
                    # levels. 3.2: unless it has NOT FILLED -- see chase_exit_limit
                    # (LESL rested 860+ s through a gap-down).
                    row_thr, trig, lim = ctl.table.lookup(ypct if ypct is not None else 0.0)
                    aux, lmt = ctl._aux_floor, ctl._lmt_floor
                    # 3.4: BEFORE chasing, check the exit is still alive at the
                    # broker. A dead order cannot be chased, and chase_exit_limit
                    # bails on _prot_reject_n by design, so a rejected exit had
                    # no owner at all -- JUNS 2026-10-06 sat EXITING with a
                    # Cancelled exit and 1 sh open for 4 hours. See rearm_dead_exit.
                    rearm_event = ctl.rearm_dead_exit()
                    if rearm_event:
                        event = f"{event}+{rearm_event}" if event else rearm_event
                    chase_event = ctl.chase_exit_limit()
                    if chase_event:
                        event = f"{event}+{chase_event}" if event else chase_event
                else:
                    row_thr, trig, lim, aux, lmt = ctl.compute_protective_levels(exit_ref)
                    # 3.2: the bid alone no longer decides. See exit_decision --
                    # a withdrawn bid with no trade behind it is not a fall.
                    fire, src = ctl.exit_decision(exit_ref, aux)
                    if fire:
                        # synthetic stop trigger: place the SELL limit at the held
                        # table limit (true stop-limit; rests if it gaps below - step 5).
                        # The hard floor instead exits at the bid (exit_lmt=None ->
                        # _flatten_price), because it must actually FILL: it is the
                        # loss cap, and the ladder limit can sit above the market.
                        ctl._trigger_source = src
                        ctl._exit_ts = time.time()
                        ctl._exit_lmt_sent = None if src == "hard_floor" else lmt
                        ctl.arm_or_replace_protective(
                            exit_ref, force_exit=True,
                            exit_lmt=(None if src == "hard_floor" else lmt),
                            reason=f"synthetic-trigger({src})")
                        ctl.exiting = True
                        ctl.phase = PHASE_EXITING
                        event = "stop_triggered"
                    else:
                        ctl.arm_or_replace_protective(exit_ref, aux=aux, lmt=lmt, reason="trail")
                        ctl.phase = (PHASE_LONG_MONITOR if ctl.original_avg_fill
                                     else PHASE_ENTRY_PARTIAL)
                        if src == "deferred":
                            # The bid crossed the stop and NO trade agreed. Recorded
                            # so the tables show how often this defence pays.
                            ctl.log.info(
                                "TRIGGER_DEFERRED #%d bid=%s <= aux=%s but last=%s "
                                "is above it (nothing traded down)",
                                ctl._deferred_n, _f(exit_ref), _f(aux), _f(ctl.last))
                            event = (f"{event}+TRIGGER_DEFERRED" if event
                                     else "TRIGGER_DEFERRED")

            # 3.3: the complement of deferred_n -- prints that DID trade at or
            # below the live stop. Record-only; counted once per distinct print.
            ctl.note_trade_through(aux)

            prot = ctl.orders.get(ctl.protective_id, {})
            erec = ctl.orders.get(ctl.active_buy_id, {})
            row = dict(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                symbol=ctl.symbol, phase=ctl.phase,
                bid=_f(bid), ask=_f(ask), mid=_f(mid),
                orig_avg_fill=_f(ctl.original_avg_fill), filled_qty=pos,
                position=pos, yield_pct=_f(ypct, 2),
                row_yield_threshold=_f(row_thr, 1), trigger_pct=_f(trig, 2),
                limit_pct=_f(lim, 2), aux_price=_f(aux), lmt_price=_f(lmt),
                protective_order_id=ctl.protective_id,
                protective_status=prot.get("status", ""), event=event,
                why_held=ctl.entry_why_held,
                entry_order_id=ctl.active_buy_id,
                entry_lmt=_f(ctl.entry_lmt_working if ctl.entry_lmt_working is not None
                             else ctl.entry_lmt),
                entry_status=erec.get("status", ""),
                track_n=ctl._track_n,
                capped_to=_f(ctl.entry_capped_to),
                wall_obs=_f(ctl.entry_wall_obs),
                ref_snapshot=ctl._ref_snapshot_str(),
                last=_f(ctl.last),
                spread_pct=_f(ctl._spread_pct_used, 2),
                eff_trigger_pct=_f(ctl._eff_trig, 2),
                eff_limit_pct=_f(ctl._eff_lim, 2),
                trigger_source=ctl._trigger_source or "",
                deferred_n=ctl._deferred_n,
                exit_fill_px=_f(ctl.exit_fill_px),
                exit_fill_ts=ctl.exit_fill_ts or "",
                realized_yield_pct=_f(ctl.realized_yield_pct(), 2),
                **ctl.market_snapshot_row(),
            )
            # 3.0 row throttle. While ENTRY_PENDING the loop now wakes on every
            # ask tick (up to ~4 Hz) instead of once a second, so write a row only
            # when something happened or a second has passed. Keeps the table's
            # historical 1 Hz cadence and still records every re-price.
            now_ts = time.time()
            emit = (event != "" or ctl.phase != PHASE_ENTRY_PENDING
                    or (now_ts - ctl._pa_last_row_ts) >= 1.0)
            if emit:
                ctl._pa_last_row_ts = now_ts

        if emit:
            ctl.pa.write(row)
        if emit and mid is not None:
            ctl.log.info("%s %s | mid=%s yld=%s | %s | STOP aux=%s lmt=%s | pos=%d @avg%s",
                         ctl.session_label(), ctl.symbol, _f(mid),
                         (f"{ypct:+.2f}%" if ypct is not None else "n/a"),
                         (f"row({row_thr:.0f}%) {trig}/{lim}" if row_thr is not None else "no-pos"),
                         _f(aux), _f(lmt), pos, _f(ctl.original_avg_fill))
        elif emit and mid is None:
            ctl.log.warning("%s %s | NO market data yet", ctl.session_label(), ctl.symbol)

        # termination conditions
        if ctl.entry_failed is not None:
            # entry deactivated/rejected/timed-out without ever filling; the terminal
            # row was just written above. Stop so the clerk recycles the client.
            ctl.stop_evt.set()
            break
        if a.lifetime is not None and (time.time() - start_ts) >= a.lifetime:
            ctl.log.info("Lifetime %ds reached -> shutdown", a.lifetime)
            if ctl.done_reason is None:
                ctl.done_reason = "lifetime"
            ctl.stop_evt.set()
            break
        if ctl.session_end_reached():
            ctl.log.info("Session-end %s ET reached -> shutdown", a.session_end)
            if ctl.done_reason is None:
                ctl.done_reason = "session_end"
            ctl.stop_evt.set()
            break

        # 3.0: tighten the sleep while the entry is working so the tracker sees
        # the ask at the feed's own ~250 ms cadence. update_quote also sets
        # ctl.wake on an ask change in this phase, so this is only the ceiling on
        # how long we can miss one. Everything else (a live position, the 1 Hz
        # protective ladder) keeps the original loop_interval.
        interval = a.loop_interval
        if (a.entry_track_enabled and ctl.phase == PHASE_ENTRY_PENDING
                and ctl._track_stopped is None):
            interval = min(interval, ENTRY_TRACK_MIN_INTERVAL_SEC)
        ctl.wake.wait(timeout=interval)


def _f(v, nd=4):
    if v is None:
        return ""
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


# --------------------------------------------------------------------------- #
# Terminal row + post-exit watch (3.2)
# --------------------------------------------------------------------------- #
def write_terminal_row(ctl: XWingController, event: str = "session_end"):
    """Write the ONE row the table never had: the exit fill and its realised yield.

    control_loop's `while not ctl.stop_evt.is_set()` is checked before any row is
    built, and _signal_done("stop_filled") sets stop_evt from the ibapi callback
    thread, so the loop always exited without recording the fill. This runs from
    shutdown(), after the loop and before pa.close()."""
    try:
        with ctl.lock:
            pos = int(round(ctl.position))
            rz = ctl.realized_yield_pct()
            ref = ctl.original_avg_fill or ctl.running_entry_avg
            ypct = ((ctl.current_exit_ref() - ref) / ref * 100.0
                    if (ctl.current_exit_ref() and ref) else None)
            row = dict(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                symbol=ctl.symbol, phase=ctl.phase,
                bid=_f(ctl.bid), ask=_f(ctl.ask), mid=_f(ctl.current_mid()),
                orig_avg_fill=_f(ref), filled_qty=pos, position=pos,
                yield_pct=_f(ypct, 2),
                protective_order_id=ctl.protective_id,
                protective_status=ctl.orders.get(ctl.protective_id, {}).get("status", ""),
                event=f"{event}:{ctl.done_reason}" if ctl.done_reason else event,
                entry_order_id=ctl.active_buy_id,
                last=_f(ctl.last),
                spread_pct=_f(ctl._spread_pct_used, 2),
                eff_trigger_pct=_f(ctl._eff_trig, 2),
                eff_limit_pct=_f(ctl._eff_lim, 2),
                trigger_source=ctl._trigger_source or "",
                deferred_n=ctl._deferred_n,
                exit_fill_px=_f(ctl.exit_fill_px),
                exit_fill_ts=ctl.exit_fill_ts or "",
                realized_yield_pct=_f(rz, 2),
                ref_snapshot=ctl._ref_snapshot_str(),
                **ctl.market_snapshot_row(),
            )
        ctl.pa.write(row)
    except Exception as e:
        ctl.log.error("terminal row not written: %s", e)


def start_post_exit_watch(ctl: XWingController, seconds: float):
    """Sample bid/ask/last for `seconds` AFTER the exit, in a separate file.

    This answers the question the operator could not answer from the tables:
    was the ejection early? It cannot run in the control loop -- _signal_done
    sets stop_evt, and stop_evt is load-bearing in 14 places including the clerk
    teardown and the T47 hand-off, so holding the control thread for minutes
    would delay T47's re-entry watch and the clerk's per-symbol claim. So it is
    a daemon thread that touches ONLY its own writer and the tick fields, never
    stop_evt, the session, or T47, and it writes a SEPARATE file so it can never
    interleave with the main table.

    3.3: ON by default, and the 3.2 note that said it could not work was wrong.
    The subscription is NOT cancelled when a run ends: T47.on_run_done opens its
    re-entry watch (T47-airspeeder-2.0.py:367-407) and the clerk holds the client
    for the whole trio, cancelMktData being reached only in TrioSession.finish()
    (clerk-1.2.py:846). Quotes therefore keep arriving for up to two hours. The
    cases where they do NOT -- T47 ending the trio at once, or relaunching and
    swapping session.xwing so this controller stops being fed -- are covered by
    the stale bail: nothing moving for POST_EXIT_STALE_BAIL_SEC ends the watch,
    so a dead feed produces a short file instead of a long flat one.

    Sampling is dense (POST_EXIT_FAST_HZ) for the first POST_EXIT_FAST_SEC and
    1 Hz after: the question "was the ejection early?" is answered in the first
    half minute, and the rest only has to show whether the name recovered.

    ⚠ It still must not touch stop_evt, the session or T47, and it still writes
    its own file -- those constraints are what make it safe, not the cadence.
    """
    if not seconds or seconds <= 0:
        return None
    try:
        stem = ctl.pa.path.stem
        out = ctl.pa.path.parent / "postexit" / f"{stem}-post.tsv"
        writer = PriceActionWriter(str(out), ctl.symbol, str(out.parent), log=ctl.log)
    except Exception as e:
        ctl.log.error("post-exit watch not started: %s", e)
        return None

    t0 = time.time()
    ref = ctl.original_avg_fill or ctl.running_entry_avg

    fast_dt = (1.0 / POST_EXIT_FAST_HZ) if POST_EXIT_FAST_HZ > 0 else 1.0

    def _run():
        last_sig = None
        last_write = 0.0
        last_move = time.time()
        while True:
            now = time.time()
            el = now - t0
            if el >= seconds:
                break
            if (now - last_move) >= POST_EXIT_STALE_BAIL_SEC:
                ctl.log.info("post-exit watch: feed went quiet after %.0fs "
                             "(no quote change in %.0fs) -- stopping",
                             el, POST_EXIT_STALE_BAIL_SEC)
                break
            with ctl.lock:
                bid, ask, last = ctl.bid, ctl.ask, ctl.last
                bsz, asz = ctl.bid_size, ctl.ask_size
            sig = (bid, ask, last, bsz, asz)
            moved = sig != last_sig
            if moved:
                last_move = now
            # Write on a change, and at least once a second regardless, so a
            # flat stretch is recorded as flat rather than as missing -- the two
            # are not the same thing when calibrating an exit.
            if moved or (now - last_write) >= 1.0:
                last_sig = sig
                last_write = now
                try:
                    writer.write(dict(
                        timestamp=datetime.now().isoformat(timespec="seconds"),
                        symbol=ctl.symbol, phase="POST_EXIT",
                        bid=_f(bid), ask=_f(ask), last=_f(last),
                        mid=_f(ctl.current_mid()),
                        orig_avg_fill=_f(ref),
                        exit_fill_px=_f(ctl.exit_fill_px),
                        exit_fill_ts=ctl.exit_fill_ts or "",
                        yield_pct=_f(((bid - ref) / ref * 100.0)
                                     if (bid and ref) else None, 2),
                        realized_yield_pct=_f(ctl.realized_yield_pct(), 2),
                        event=f"post_exit_{el:.0f}s",
                        **ctl.market_snapshot_row(),
                    ))
                except Exception:
                    break
            time.sleep(fast_dt if el < POST_EXIT_FAST_SEC else 1.0)
        try:
            writer.close()
        except Exception:
            pass
        ctl.log.info("post-exit watch finished (%.0fs): %s", seconds, out)

    th = threading.Thread(target=_run, name=f"postexit-{ctl.symbol}", daemon=True)
    th.start()
    return th


# --------------------------------------------------------------------------- #
# Shutdown
# --------------------------------------------------------------------------- #
def shutdown(ctl: XWingController):
    """Flatten any open position and finalize the price-action table.

    In DRIVEN mode the shared ibapi socket belongs to the clerk, so this never
    disconnects it (the clerk cancels market data and recycles the client). In
    STANDALONE mode it cancels market data and disconnects the single client."""
    with ctl.lock:
        ctl.terminating = True
        ctl.phase = PHASE_FLATTEN
        pos = int(round(ctl.position))
        mid = ctl.current_mid()
    if pos > 0 and mid is not None:
        with ctl.lock:
            # 3.4: the flatten inherited both JUNS 2026-10-06 defects. It
            # re-placed on self.protective_id, which was already Cancelled, so
            # IBKR answered 103 "Duplicate order id" a second time; and the
            # fallback price behind that 103 was the ladder's limit, which the
            # observed sell wall had already refused. Result: "FLATTEN
            # incomplete: 1 sh still open". Fresh id when the exit is dead, and
            # a price that clears the wall we have been shown.
            rec = ctl.orders.get(ctl.protective_id, {})
            if rec.get("status", "") in ORDER_DEAD_STATUSES:
                ctl.log.warning("FLATTEN: resting exit %s is %s -> re-placing on "
                                "a fresh orderId", ctl.protective_id,
                                rec.get("status"))
                ctl.protective_id = None
                ctl._prot_sent = None
            px = ctl._exit_repair_price(force_bid=True)
        ctl.log.info("FLATTEN: closing %d sh with marketable SELL LMT @ %s",
                     pos, _f(px))
        ctl.arm_or_replace_protective(mid, force_exit=True, exit_lmt=px,
                                      reason="flatten")
        # best-effort wait for the close to fill
        deadline = time.time() + 10
        while time.time() < deadline:
            with ctl.lock:
                if int(round(ctl.position)) <= 0:
                    break
            time.sleep(0.25)
        with ctl.lock:
            left = int(round(ctl.position))
        if left > 0:
            ctl.log.warning("FLATTEN incomplete: %d sh still open; SELL LMT left "
                            "resting at the broker", left)

    with ctl.lock:
        ctl.phase = PHASE_SHUTDOWN
    # 3.2: record the exit fill and the realised yield before the table closes,
    # then (optionally) keep sampling prices in a separate file.
    write_terminal_row(ctl)
    start_post_exit_watch(ctl, POST_EXIT_WATCH_SEC)
    # NOTE: deliberately NO reqGlobalCancel (would hit other instances).
    if ctl.driven:
        # The clerk owns the socket; just finalize our local artifacts.
        ctl.pa.close()
        return
    for client, name in ((ctl.data_client, "data"), (ctl.order_client, "order")):
        try:
            if client and client.isConnected():
                if name == "data" and ctl.contract is not None:
                    try:
                        client.cancelMktData(MKT_DATA_REQ_ID)
                    except Exception:
                        pass
                client.disconnect()
                ctl.log.info("%s client disconnected", name)
        except Exception as e:
            ctl.log.error("Error disconnecting %s client: %s", name, e)
    ctl.pa.close()


def run_trade(ctl: XWingController, start_ts: float):
    """Run the control loop to completion, flatten/finalize, then fire on_done.
    Shared by standalone main() and the clerk's driven session. on_done runs
    here (off the ibapi callback thread) exactly once."""
    try:
        control_loop(ctl, start_ts=start_ts)
    finally:
        shutdown(ctl)
        if ctl.on_done is not None:
            try:
                ctl.on_done(ctl.done_reason or "stopped")
            except Exception as e:
                ctl.log.error("on_done callback raised: %s", e, exc_info=True)


def build_driven(symbol, last_close_price, shared_client, *, log, on_done,
                 id_alloc, **cfg_overrides):
    """DRIVEN-mode factory used by clerk-1.0.py. Builds a controller bound to an
    already-connected shared ibapi client. The clerk is responsible for:
      1. resolving the contract and calling ctl.set_contract_details(cd),
      2. feeding ticks via ctl.update_quote(...) (fan-out from the shared client),
      3. calling ctl.on_buy_signal(last_ask) when trade-mole triggers, then
      4. running run_trade(ctl, start_ts=now) in a thread.
    The shared client owns the socket; shutdown() will NOT disconnect it."""
    cfg = build_config(symbol, last_close_price, **cfg_overrides)
    table = LimitsTable(cfg.limits_table, log=log)
    pa = PriceActionWriter(cfg.price_action_table, cfg.symbol, cfg.log_dir, log=log)
    ctl = XWingController(cfg, table, pa, log=log, driven=True,
                          id_alloc=id_alloc, on_done=on_done)
    ctl.order_client = shared_client     # placeOrder / cancelOrder target
    ctl.data_client = None               # clerk cancels market data, not x-wing
    ctl.phase = PHASE_ARMED
    return ctl


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
def make_contract(symbol):
    c = Contract()
    c.symbol = symbol
    c.secType = "STK"
    c.exchange = "SMART"
    c.currency = "USD"
    return c


def connect_client(client, host, port, client_id, ready_evt, label, log=None,
                   timeout=15):
    log = log or logger
    log.info("Connecting %s client clientId=%d to %s:%d", label, client_id, host, port)
    client.connect(host, port, client_id)
    t = threading.Thread(target=client.run, daemon=True, name=f"{label}-run")
    t.start()
    if not ready_evt.wait(timeout=timeout):
        raise RuntimeError(f"{label} client did not connect within {timeout}s")
    return t


def main():
    """STANDALONE mode: single ibapi client (data + orders). Arms and waits for
    SIGUSR1 to FIRE the entry (manual stand-in for trade-mole's buy-signal)."""
    args = build_arg_parser().parse_args()
    symbol = args.symbol.upper()
    log = setup_logging(args.log_dir, symbol, args.loglevel)

    if args.port in (4001, 7496):
        log.warning("PORT %d looks like a LIVE trading port. Real money at risk.",
                    args.port)
    if args.account:
        log.info("Orders will be placed in account %s", args.account)

    # entry-pricing validation: need at least one way to price the entry
    if args.max_limit_entry_percent_price is None and args.entry_limit_price is None:
        log.error("No entry price source: pass --max-limit-entry-percent-price "
                  "(computed off the ask) or --Entry-limit-price (fixed).")
        sys.exit(2)
    # the cap requires BOTH last-close-price and max-cap-entry-percent
    if (args.last_close_price is None) != (args.max_cap_entry_percent is None):
        log.error("--last-close-price and --max-cap-entry-percent must be given "
                  "together to enable the entry-limit cap.")
        sys.exit(2)

    # Apply the wall kill-switches to the module globals the controller reads.
    global ENTRY_WALL_ENABLED, ENTRY_WALL_RETRY_ENABLED
    global ENTRY_WALL_RETRY_MAX, ENTRY_WALL_OVER_CAP_MULT, ENTRY_CAP_RESCUE_ONE_TICK
    ENTRY_CAP_RESCUE_ONE_TICK = bool(args.cap_rescue_one_tick)
    ENTRY_WALL_ENABLED = bool(args.entry_wall_enabled)
    ENTRY_WALL_RETRY_ENABLED = bool(args.entry_wall_retry_enabled)
    ENTRY_WALL_RETRY_MAX = int(args.entry_wall_retry_max)
    ENTRY_WALL_OVER_CAP_MULT = float(args.entry_wall_over_cap_mult)
    log.info("price wall: clamp=%s retry=%s max=%d over_cap_mult=%.4f",
             ENTRY_WALL_ENABLED, ENTRY_WALL_RETRY_ENABLED,
             ENTRY_WALL_RETRY_MAX, ENTRY_WALL_OVER_CAP_MULT)

    # Same pattern for the 3.2 exit-noise defences. DRIVEN mode (the clerk) has
    # no argparse, so it reads these module constants directly -- which is why
    # the defaults above, not build_config, are the live configuration.
    global EXIT_REQUIRE_TRADE_CONFIRM, EXIT_TRIGGER_SPREAD_MULT
    global HARD_FLOOR_PCT, HARD_FLOOR_ON_LAST, EXIT_LMT_CHASE_ENABLED
    global POST_EXIT_WATCH_SEC, EXIT_REARM_ENABLED
    EXIT_REQUIRE_TRADE_CONFIRM = bool(args.exit_trade_confirm)
    EXIT_TRIGGER_SPREAD_MULT = (0.0 if args.exit_spread_trigger
                                else float(args.exit_trigger_spread_mult))
    HARD_FLOOR_PCT = float(args.hard_floor_pct)
    HARD_FLOOR_ON_LAST = bool(args.hard_floor_on_last)
    EXIT_LMT_CHASE_ENABLED = bool(args.exit_lmt_chase)
    POST_EXIT_WATCH_SEC = float(args.post_exit_watch_sec)
    EXIT_REARM_ENABLED = bool(args.exit_rearm)
    log.info("exit defences: trade_confirm=%s spread_mult=%.2f hard_floor=%.1f%% "
             "on_last=%s lmt_chase=%s rearm=%s post_exit=%.0fs",
             EXIT_REQUIRE_TRADE_CONFIRM, EXIT_TRIGGER_SPREAD_MULT, HARD_FLOOR_PCT,
             HARD_FLOOR_ON_LAST, EXIT_LMT_CHASE_ENABLED, EXIT_REARM_ENABLED,
             POST_EXIT_WATCH_SEC)

    table = LimitsTable(args.limits_table, log=log)
    pa = PriceActionWriter(args.price_action_table, symbol, args.log_dir, log=log)
    ctl = XWingController(args, table, pa, log=log, driven=False)

    client = XWingClient(ctl)
    ctl.order_client = client
    ctl.data_client = client

    fire_evt = threading.Event()

    def _sig(signum, frame):
        log.info("Signal %s received -> shutdown", signum)
        ctl.stop_evt.set()
        ctl.wake.set()
        fire_evt.set()  # unblock the arm wait so it sees stop_evt and aborts
    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    # SIGUSR1 = manual FIRE (stand-in for trade-mole's buy-signal). Register
    # before connecting so an early signal isn't lost.
    if hasattr(signal, "SIGUSR1"):
        def _fire(signum, frame):
            log.info("SIGUSR1 received -> FIRE entry (manual)")
            fire_evt.set()
        signal.signal(signal.SIGUSR1, _fire)

    try:
        connect_client(client, args.host, args.port, args.client_id_base,
                       client.connected_evt, "xwing", log=log)

        # resolve contract (+ minTick)
        client.contract_evt.clear()
        client.reqContractDetails(1, make_contract(symbol))
        if not client.contract_evt.wait(timeout=15) or ctl.contract is None:
            raise RuntimeError(f"Could not resolve contract for {symbol}")

        client.reqMarketDataType(args.market_data_type)
        client.reqMktData(MKT_DATA_REQ_ID, ctl.contract, GENERIC_TICKS,
                          False, False, [])

        # wait for a usable price, with a delayed-data fallback
        log.info("Waiting for first market-data tick...")
        if not _wait_for_mid(ctl, 10):
            if args.market_data_type == 1:
                log.warning("No real-time data; retrying with delayed (type 3)")
                client.reqMarketDataType(3)
                _wait_for_mid(ctl, 10)
        if ctl.current_mid() is None:
            log.warning("Proceeding without a confirmed quote; entry uses limit only")

        ctl.phase = PHASE_ARMED
        log.info("ARMED for %s; FIRE with: pkill -USR1 -f 'x-wing-2.1.py --symbol %s' "
                 "(or SIGINT/SIGTERM to abort).", symbol, symbol)
        while not fire_evt.is_set() and not ctl.stop_evt.is_set():
            fire_evt.wait(timeout=0.2)

        if ctl.stop_evt.is_set():
            log.info("Aborted before firing; no trade placed.")
        else:
            ctl.on_buy_signal(None)             # fire entry off the live ask
            run_trade(ctl, start_ts=time.time())  # loop + flatten + finalize
    except Exception as e:
        log.exception("Fatal error: %s", e)
    finally:
        if ctl.phase != PHASE_SHUTDOWN:
            shutdown(ctl)
        time.sleep(0.5)
    log.info("x-wing exited.")


def _wait_for_mid(ctl, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if ctl.current_mid() is not None:
            return True
        time.sleep(0.2)
    return False


if __name__ == "__main__":
    main()
