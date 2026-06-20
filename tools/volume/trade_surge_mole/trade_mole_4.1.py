#!/usr/bin/env python3
"""
IBKR Trade-Frequency Surge Detector v4.1 (extended order-flow metrics)
======================================================================

Successor to trade_mole_4.py. Same connection model (reqMktData only,
one market-data line per symbol) and same rolling-window framework
(1/2/3/4/5/10 s), with the following added metrics derived from the
existing tickString / tickPrice / tickSize callbacks — NO new IBKR
subscriptions are required:

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
    python trade_mole_4.1.py \\
        --symbol GXAI \\
        --clientID 7 \\
        --lifeTime 10:00 \\
        --output ./outputs/ \\
        --baseline-iti 30.0

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

# Legacy thresholds kept ONLY so the column schema remains stable; they
# do not gate the new composite surge rule.
LEGACY_RATE_RATIO_1_BASE = 5.0
LEGACY_RATE_RATIO_5_BASE = 3.0
LEGACY_ITI_COLLAPSE = 2.0
LEGACY_PRIOR_ITI_MIN = 10.0
LEGACY_MIN_TRADES_1S = 2

# Generic ticks for reqMktData
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221"

# Eastern Time zone for the pre-market start gate
ET = ZoneInfo("America/New_York")


# ============================================================================
# CLI helpers
# ============================================================================


def _compute_collection_start_delay(now=None):
    """
    If `now` (default: real wall clock) falls in the closed-market window
    [20:00, 24:00) U [00:00, 04:00) ET, return (seconds_until_next_0400_ET,
    target_dt_in_ET). Otherwise return (0.0, None) and the caller starts
    collection immediately.
    """
    now = now if now is not None else datetime.now(tz=ET)
    if now.tzinfo is None:
        now = now.replace(tzinfo=ET)
    else:
        now = now.astimezone(ET)

    h = now.hour
    if 4 <= h < 20:
        return 0.0, None

    if h >= 20:
        base = now + timedelta(days=1)
    else:
        base = now
    target = base.replace(hour=4, minute=0, second=0, microsecond=0)
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
    ):
        EClient.__init__(self, wrapper=self)
        self.symbol = symbol
        # Legacy baseline (kept for column compatibility only)
        self._hist_baseline_avg_iti = baseline_avg_iti
        self._hist_baseline_trade_rate = 1.0 / baseline_avg_iti

        # New composite-rule thresholds
        self._surge_dollar_rate = surge_dollar_rate
        self._surge_max_spread_pct = surge_max_spread_pct
        self._surge_min_lift_ratio = surge_min_lift_ratio
        self._surge_min_bid_drift_bp = surge_min_bid_drift_bp
        self._surge_min_trades_5s = surge_min_trades_5s

        # Request IDs
        self.req_id_mkt = 1001

        # Live state
        self._session_start_mono: Optional[float] = None
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

        # Captured event records -> DataFrame at end
        self.records: list = []

        # Connection signaling
        self.connected_event = threading.Event()
        self.disconnected_flag = False

    # ------------------------------------------------------------------
    # Lifecycle / errors
    # ------------------------------------------------------------------

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2107, 2158, 2119, 2100, 2108):
            logging.info(f"[IBKR {errorCode}] {errorString}")
            return
        logging.warning(f"[IBKR err {errorCode}] reqId={reqId}: {errorString}")

    def nextValidId(self, orderId):
        logging.info(f"Connected to IBKR. nextValidId={orderId}")
        self.connected_event.set()

    def connectionClosed(self):
        self.disconnected_flag = True
        logging.info("IBKR connection closed.")

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
        if self._session_start_mono is None:
            self._session_start_mono = now_mono

        # Inter-trade time
        iti = None
        if self._last_trade_mono is not None:
            iti = now_mono - self._last_trade_mono
        self._last_trade_mono = now_mono

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
            "surge_detected": surge,
            "surge_reason": reason,
        }
        rec.update(win)
        self.records.append(rec)

        if surge:
            iti_str = f"{iti:.2f}s" if iti is not None else "n/a"
            logging.warning(
                f"\U0001F680 SURGE {self.symbol} @ ${price:.4f} sz={size} "
                f"| trades_5s={win['trades_in_5s']} $rate_5s={win['dollar_rate_5s']:.0f}/s "
                f"| lift_5s={win['lift_offer_ratio_5s']:.2f} "
                f"| bid_drift_5s={win['bid_drift_5s_bp']}bp "
                f"| iti={iti_str} | {reason}"
            )

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
        """
        Composite rule on the new metrics.

        Fires if ALL of:
          - dollar_rate_5s >= --surge-dollar-rate
          - spread_pct (current) <= --surge-max-spread-pct
          - lift_offer_ratio_5s >= --surge-min-lift-ratio
          - bid_drift_5s_bp >= --surge-min-bid-drift-bp
          - trades_in_5s >= --surge-min-trades-5s
        """
        reasons = []
        sp = self._spread_pct(self._last_bid) if self._last_bid else None
        # Fall back to the live midprice for spread_pct denominator
        mid = self._midprice()
        if mid and self._last_bid is not None and self._last_ask is not None and self._last_ask > self._last_bid:
            sp = (self._last_ask - self._last_bid) / mid

        cond_trades = w.get("trades_in_5s", 0) >= self._surge_min_trades_5s
        cond_dollar = w.get("dollar_rate_5s", 0.0) >= self._surge_dollar_rate
        cond_spread = sp is not None and sp <= self._surge_max_spread_pct
        lift = w.get("lift_offer_ratio_5s")
        cond_lift = lift is not None and lift >= self._surge_min_lift_ratio
        bid_drift = w.get("bid_drift_5s_bp")
        cond_drift = bid_drift is not None and bid_drift >= self._surge_min_bid_drift_bp

        if cond_trades and cond_dollar and cond_spread and cond_lift and cond_drift:
            reasons.append(
                f"$rate_5s={w['dollar_rate_5s']:.0f}|"
                f"sp={sp:.3f}|"
                f"lift={lift:.2f}|"
                f"bid_drift={bid_drift:.0f}bp"
            )
        return (len(reasons) > 0, "|".join(reasons))

    # ------------------------------------------------------------------
    # Generic tick callbacks (from reqMktData)
    # ------------------------------------------------------------------

    def tickPrice(self, reqId, tickType, price, attrib):
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
                logging.warning(f"⛔ HALT detected: code={self._halted}")
        elif tickType == 55:
            self._tws_trade_rate = value
            rec["tws_trade_rate_per_min"] = value
        elif tickType == 56:
            self._tws_volume_rate = value
            rec["tws_volume_rate_per_min"] = value
        self.records.append(rec)

    def tickString(self, reqId, tickType, value):
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
                logging.debug(f"RTVolume parse error on {value!r}: {e}")

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
            f"bid_drift_{w}s_bp", f"mid_velocity_{w}s_bp_per_s",
            f"spread_pct_at_{w}s_ago", f"spread_compression_{w}s",
            f"quote_updates_{w}s",
        ]
    cols += [
        "max_cluster_200ms_in_5s",
        "hist_baseline_trade_rate", "hist_baseline_avg_iti",
        "accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s",
        "accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline", "accel_5s_vs_hist_baseline",
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
        f"Composite rule: dollar_rate_5s>={args.surge_dollar_rate} "
        f"AND spread_pct<={args.surge_max_spread_pct} "
        f"AND lift_5s>={args.surge_min_lift_ratio} "
        f"AND bid_drift_5s>={args.surge_min_bid_drift_bp}bp "
        f"AND trades_5s>={args.surge_min_trades_5s}"
    )

    app = IBKRSurgeApp(
        symbol,
        baseline_avg_iti=args.baseline_avg_iti,
        surge_dollar_rate=args.surge_dollar_rate,
        surge_max_spread_pct=args.surge_max_spread_pct,
        surge_min_lift_ratio=args.surge_min_lift_ratio,
        surge_min_bid_drift_bp=args.surge_min_bid_drift_bp,
        surge_min_trades_5s=args.surge_min_trades_5s,
    )
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

    if not app.records:
        logging.warning("No events captured; no output file written.")
        sys.exit(0)

    logging.info(f"Building DataFrame from {len(app.records)} events...")
    df = pd.DataFrame(app.records)

    if "local_arrival_iso" in df.columns:
        df["Time"] = df["local_arrival_iso"].str.slice(11, 23)

    preferred = build_column_order()
    existing = [c for c in preferred if c in df.columns]
    extras = [c for c in df.columns if c not in existing]
    df = df[existing + extras]

    trades_df = df[df["event_type"] == "TRADE"] if "event_type" in df.columns else pd.DataFrame()
    if "surge_detected" in df.columns:
        n_surges = int((df["surge_detected"] == True).sum())
    else:
        n_surges = 0

    logging.info(
        f"Summary: total_events={len(df)} trades={len(trades_df)} "
        f"surge_events={n_surges} cum_vol={app._cum_volume} "
        f"cum_trades={app._cum_trade_count}"
    )

    df.to_csv(output_path, index=False, float_format="%.9f")
    logging.info(f"Wrote {len(df)} rows x {len(df.columns)} cols -> {output_path}")


if __name__ == "__main__":
    main()
