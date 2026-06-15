#!/usr/bin/env python3
"""
trade-mole-2.1.py — IBKR Trade-Frequency Surge Detector (clerk-aware)
=====================================================================

Refactor of trade_mole_4.1.py. Two run modes:

  * STANDALONE (``python trade-mole-2.1.py --symbol ... ``): opens its own
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
    python trade-mole-2.1.py \\
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

# Last-3-trades ITI-collapse trigger (added 2026-06-14). Fires when
# bslnITI_on_last3trades_avrgITI > BSLN_ITI_LAST3_THRESHOLD, i.e. the hist baseline
# ITI is >100x the mean of the two most recent measured ITIs — the tape has sped up
# dramatically vs the stock's own cadence over the last couple of trades, a live
# (not start-of-session) surge signature. ORed with the other rules in should_fire.
# Strict greater-than; hardcoded (not CLI-tunable). Skipped when the value is None
# (first accepted trade, or no valid ITI baseline), so it cannot false-fire.
BSLN_ITI_LAST3_THRESHOLD = 100.0

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

# Generic ticks for reqMktData
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221"

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
        if self.triggered and self.stop_at_trigger:
            return
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
        # Expose this trade's bslnITI_on_last3trades_avrgITI to should_fire (Rule C).
        self._cur_bsln_on_last3 = bsln_on_last3
        surge, reason = self._detect_surge(win, iti)

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
            if not self.triggered:
                self.triggered = True
                last_ask = self._last_ask
                self._log.warning(
                    f"BUY-SIGNAL {self.symbol}: handing last_ask={last_ask} to x-wing"
                )
                cb = self.buy_signal_callback
                if cb is not None:
                    try:
                        cb(last_ask)
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
        for w in WINDOWS:
            c = counts[w]
            out[f"trades_in_{w}s"] = c
            out[f"volume_in_{w}s"] = volumes[w]
            out[f"dollar_vol_in_{w}s"] = dollar_vols[w]
            out[f"trade_rate_{w}s"] = c / w
            out[f"avg_iti_{w}s"] = (w / c) if c > 0 else None
            # M3 — dollar volume rate
            out[f"dollar_rate_{w}s"] = dollar_vols[w] / w
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

    def should_fire(self, w: dict, iti: Optional[float]) -> tuple:
        """
        OR of three trigger rules — any one alone fires the buy-signal.

        Rule A — Quote-churn (active since 2026-06-11): fire when the NBBO is being
        repainted rapidly, i.e.

            quote_updates_{window}s >= surge_min_quote_updates

        Calibrated on the 2026-06-08..11 labeled set (see
        mole-outputs/iti-threshold-analysis-2026-06-11.md). It replaced the prior
        price-momentum rule (bid_drift + mid_velocity), which only fired on a
        *fresh* upward price thrust and therefore missed surges that were already
        in progress when recording started (flat/choppy/retracing price, zero
        bid_drift). Quote-churn is high from the first tick on a genuine surge, so
        this fires sub-second even when the price move predates the recording, and
        it does not disadvantage illiquid low-float names.

        Rule B — Start-staleness (added 2026-06-14): fire when

            bsln_over_prev_trd_gap > BSLN_OVER_PREV_TRD_GAP_THRESHOLD

        i.e. the tape was very stale at subscribe relative to the stock's own
        historical cadence — another signature of a surge that began before we hit
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
        record. Skipped when the value is None (first accepted trade, or no valid ITI
        baseline), so it cannot false-fire.
        """
        # Rule B — start-staleness (OR). Constant per session; may fire immediately.
        bsln_gap = self._bsln_over_prev_trd_gap()
        if bsln_gap is not None and bsln_gap > BSLN_OVER_PREV_TRD_GAP_THRESHOLD:
            return (
                True,
                f"bsln_over_prev_trd_gap={bsln_gap:.2f}>"
                f"{BSLN_OVER_PREV_TRD_GAP_THRESHOLD:.0f}",
            )

        # Rule C — last-3-trades ITI collapse (OR). Updates every accepted trade.
        bsln_last3 = self._cur_bsln_on_last3
        if bsln_last3 is not None and bsln_last3 > BSLN_ITI_LAST3_THRESHOLD:
            return (
                True,
                f"bslnITI_on_last3trades_avrgITI={bsln_last3:.2f}>"
                f"{BSLN_ITI_LAST3_THRESHOLD:.0f}",
            )

        # Rule A — quote-churn (OR).
        col = f"quote_updates_{self._surge_quote_window}s"
        qu = w.get(col)
        if qu is not None and qu >= self._surge_min_quote_updates:
            return (True, f"{col}={qu:.0f}>={self._surge_min_quote_updates:.0f}")

        return (False, "")

    # ------------------------------------------------------------------
    # Generic tick callbacks (from reqMktData)
    # ------------------------------------------------------------------

    def tickPrice(self, reqId, tickType, price, attrib):
        if self.triggered and self.stop_at_trigger:
            return
        now_mono = time.perf_counter()
        rec = {
            "event_type": "TICK_PRICE",
            "local_arrival_time": time.time(),
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "price": price,
        }
        updated_nbbo = False
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
        if updated_nbbo:
            self._append_quote_log(now_mono)
        self.records.append(rec)

    def tickSize(self, reqId, tickType, size):
        if self.triggered and self.stop_at_trigger:
            return
        now_mono = time.perf_counter()
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
        elif tickType == 89:
            self._shortable_shares = sz
            rec["shortable_shares"] = sz
        if updated_nbbo:
            self._append_quote_log(now_mono)
        self.records.append(rec)

    def tickGeneric(self, reqId, tickType, value):
        if self.triggered and self.stop_at_trigger:
            return
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
        self.records.append(rec)

    def tickString(self, reqId, tickType, value):
        if self.triggered and self.stop_at_trigger:
            return
        now_mono = time.perf_counter()
        now_wall = time.time()

        # 48 = RTVolume (tick 233), 77 = RTTradeVolume (tick 375)
        if tickType in (48, 77) and value:
            try:
                parts = value.split(";")
                if len(parts) >= 6 and parts[0] and parts[1]:
                    rt_price = float(parts[0])
                    rt_size = int(float(parts[1]))
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

        # 45 = LAST_TIMESTAMP: epoch (sec) of the last trade. Freeze the FIRST one
        # we receive — IBKR's subscription snapshot carries the last trade BEFORE
        # we connected, which seeds the pre-first-trade measured ITI.
        if tickType == 45 and value and self._last_timestamp_at_subscribe is None:
            try:
                self._last_timestamp_at_subscribe = float(int(value))
            except (ValueError, TypeError):
                self._log.debug(f"LAST_TIMESTAMP parse error on {value!r}")

        self.records.append({
            "event_type": "TICK_STRING",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "value_str": value,
        })

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
                        help="Composite rule: block trigger if last price is more than "
                             "this fraction below the session-start price "
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
    parser.add_argument("--keep-recording-after-trigger", action="store_true",
                        help="Do NOT stop recording when the surge/buy-signal fires "
                             "(default: stop at the trigger row). Standalone only.")
    parser.add_argument("--host", default="127.0.0.1", help="IBKR Gateway/TWS host")
    parser.add_argument("--port", type=int, default=7497,
                        help="IBKR port (7497=paper TWS, 7496=live TWS, "
                             "4002=paper Gateway, 4001=live Gateway)")
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
            if app.triggered and app.stop_at_trigger:
                logging.info("Buy-signal fired; stopping collection at the trigger row.")
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
    if not app.records:
        log.warning("No events captured; no output file written.")
        return None

    log.info(f"Building DataFrame from {len(app.records)} events...")
    df = pd.DataFrame(app.records)

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

    df.to_csv(output_path, index=False, float_format="%.9f")
    log.info(f"Wrote {len(df)} rows x {len(df.columns)} cols -> {output_path}")
    return output_path


if __name__ == "__main__":
    main()
