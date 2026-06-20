#!/usr/bin/env python3
"""
IBKR Trade-Frequency Surge Detector (baseline-free, reqMktData-only)
=====================================================================

Monitors real-time Level-1 trade stream for a single symbol and detects
sudden acceleration in trade frequency (inter-trade interval compression).

This variant uses ONLY reqMktData (no reqTickByTickData), so each process
consumes one market-data line (~100/account default) instead of two
tick-by-tick streams (3/account hard cap). Spawn one process per symbol
with a unique --clientID to track many symbols simultaneously.

Surge detection uses purely window-vs-window acceleration ratios — no
externally supplied historical baseline is required. The 10-second rolling
window acts as the "recent normal" against which shorter windows are compared.

Primary signal sources
----------------------
1. reqMktData generic tick 233 (RTVolume) -> per-trade prints via tickString
2. reqMktData generic tick 375 (RTTradeVolume) -> same channel, odd-lot variant
3. reqMktData generic tick 54 (TRADE_COUNT) -> cumulative trade count;
       used to reconcile aggregated RTVolume bursts (single_trade_flag=False)
4. reqMktData regular tickPrice/tickSize  -> bid/ask snapshot at each trade

Resolution: reqMktData is snapshot-throttled to ~250 ms (~4 Hz). When the
tape is quiet, RTVolume delivers per-trade events. During bursts > 4 trades/s,
multiple trades aggregate into one tickString event with single_trade_flag=False;
tick-54 deltas reconcile the true trade count so 1s/5s/10s window metrics stay
accurate. Bid/ask snapshots embedded in TRADE records may be up to ~250 ms stale.

Rolling windows: 1s, 2s, 3s, 4s, 5s, 10s.
Timing uses time.perf_counter() (monotonic, microsecond resolution).

Usage
-----
    python trade_mole_3.py \\
        --symbol AAPL \\
        --clientID 7 \\
        --lifeTime 15:00 \\
        --output ./data/

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

# Surge detection thresholds — all window-vs-window, no external baseline needed
SURGE_MIN_TRADES_1S = 2          # floor: need >= 2 trades in last 1s
SURGE_ACCEL_1S_VS_10S = 5.0      # 1s rate >= 5x the 10s rolling rate
SURGE_MIN_TRADES_5S = 5          # floor: need >= 5 trades in last 5s
SURGE_ACCEL_5S_VS_10S = 3.0      # 5s rate >= 3x the 10s rolling rate
SURGE_ITI_COMPRESS_RATIO = 5.0   # Rule C: current ITI < avg_iti_10s / 5
SURGE_ITI_COMPRESS_MIN_AVG = 2.0 # Rule C: only active when avg_iti_10s > 2s

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
    Captures every trade and quote into a raw records list and maintains a
    bounded rolling trade-time deque for real-time surge detection.

    Thread model
    ------------
    All ibapi callbacks fire on the EClient.run() thread. State mutation is
    confined to that thread; the main thread only reads `self.records`
    *after* disconnect(), so no locking is required.

    Hot-path ordering in _process_trade_event (driven by tickString's
    RTVolume branch):
      1) perf_counter timestamp
      2) append to trade deque (exact or provisional) + trim
      3) compute windows (O(n) over bounded deque)
      4) surge detection
      5) build record dict and append

    Aggregation correction: when RTVolume aggregates multiple trades into a
    single tickString event (single_trade_flag=False), the event is appended
    as 'provisional' and later inflated to the true trade count by
    _reconcile_trade_count() when generic tick 54 (TRADE_COUNT) updates.
    """

    def __init__(self, symbol: str):
        EClient.__init__(self, wrapper=self)
        self.symbol = symbol

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
        # Each: (mono_ts, rt_size, rt_total_volume, price)
        self._pending_rt_events: deque = deque()

        # Rolling trade log: (mono_ts, price, size, kind) where
        # kind ∈ {"exact", "provisional"}; capped at DEQUE_HISTORY_SECONDS
        self._trade_log: deque = deque()

        # Captured event records -> DataFrame at end
        self.records: list = []

        # Connection signaling
        self.connected_event = threading.Event()
        self.disconnected_flag = False

    # ------------------------------------------------------------------
    # Lifecycle / errors
    # ------------------------------------------------------------------

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # Non-error informational codes from TWS
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
    # Trade events (RTVolume via tickString) - PRIMARY signal source
    # ------------------------------------------------------------------

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
        """
        Hot path called from tickString when an RTVolume/RTTradeVolume event
        is parsed. All state mutation happens here on the EClient.run() thread.

        For single_trade_flag=True the event represents one print and is
        appended to _trade_log as ("exact"). For single_trade_flag=False the
        event aggregates >1 trades from one ~250 ms snapshot; we append a
        provisional entry and queue it for tick-54 reconciliation.
        """
        if self._session_start_mono is None:
            self._session_start_mono = now_mono

        # Inter-trade time
        iti = None
        if self._last_trade_mono is not None:
            iti = now_mono - self._last_trade_mono
        self._last_trade_mono = now_mono

        # Update cumulative totals (will be patched by reconciler when
        # an aggregated event is later confirmed to cover >1 trade)
        self._cum_trade_count += 1
        self._cum_volume += size
        self._cum_dollar_volume += price * size

        # Append to trade log
        if single_trade_flag:
            self._trade_log.append((now_mono, price, size, "exact"))
        else:
            self._trade_log.append((now_mono, price, size, "provisional"))
            self._pending_rt_events.append(
                (now_mono, size, rt_total_volume, price)
            )

        # Trim trade log to deque history window
        cutoff = now_mono - DEQUE_HISTORY_SECONDS
        while self._trade_log and self._trade_log[0][0] < cutoff:
            self._trade_log.popleft()

        # Drop pending entries older than 2s (tick-54 lag tolerance)
        pending_cutoff = now_mono - 2.0
        while self._pending_rt_events and self._pending_rt_events[0][0] < pending_cutoff:
            self._pending_rt_events.popleft()

        # Rolling window metrics
        win = self._compute_windows(now_mono)

        # Surge detection (live from trade #1 — no warmup)
        session_age = now_mono - self._session_start_mono
        surge, reason = self._detect_surge(win, iti)

        # Build record
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
            "rt_single_trade_flag": single_trade_flag,
            "rt_total_volume": rt_total_volume,
            # Quote snapshot at time of trade (up to ~250 ms stale)
            "bid": self._last_bid,
            "ask": self._last_ask,
            "bid_size": self._last_bid_size,
            "ask_size": self._last_ask_size,
            "spread": self._spread(),
            "spread_pct": self._spread_pct(price),
            "midprice": self._midprice(),
            "microprice": self._microprice(),
            # Cumulative
            "cum_volume": self._cum_volume,
            "cum_trade_count": self._cum_trade_count,
            "cum_dollar_volume": self._cum_dollar_volume,
            "vwap": self._vwap,
            # Timing
            "inter_trade_time_sec": iti,
            "session_age_sec": session_age,
            # Market state
            "halted": self._halted,
            "shortable": self._shortable,
            "shortable_shares": self._shortable_shares,
            "mark_price": self._mark_price,
            "last_rth_trade": self._last_rth_trade,
            # TWS-smoothed (slow, for reference)
            "tws_trade_rate_per_min": self._tws_trade_rate,
            "tws_volume_rate_per_min": self._tws_volume_rate,
            "tws_trade_count": self._tws_trade_count,
            # Surge flags
            "surge_detected": surge,
            "surge_reason": reason,
        }
        rec.update(win)
        self.records.append(rec)

        # Real-time alert
        if surge:
            iti_str = f"{iti:.2f}s" if iti is not None else "n/a"
            logging.warning(
                f"\U0001F680 SURGE {self.symbol} @ ${price:.4f} sz={size} "
                f"| trades_1s={win['trades_in_1s']} rate_1s={win['trade_rate_1s']:.1f}/s "
                f"| accel_1s_vs_10s={win['accel_1s_vs_10s']:.1f} "
                f"| iti={iti_str} | {reason}"
            )

    def _reconcile_trade_count(self, now_mono: float, new_count: int):
        """
        Called from tickSize on tickType 54 (TRADE_COUNT cumulative).

        Compares incoming cumulative count against the previous value to
        derive how many real trades occurred. For each pending provisional
        RTVolume burst, replicate the trade-log entry to reflect the true
        trade count (so trades_in_1s / 5s / 10s windows stay accurate).
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
        # Drain ONE pending provisional and inflate it to `delta` entries.
        # Multiple provisionals may have accumulated between tick-54 updates;
        # we attribute the entire delta to the oldest pending burst and let
        # subsequent tick-54 updates handle later bursts. (Conservative: this
        # may briefly under-count if two bursts collide between tick-54s.)
        if not self._pending_rt_events:
            return
        ts, rt_size, _rt_tot_vol, price = self._pending_rt_events.popleft()

        # Find the provisional entry in _trade_log matching this ts and inflate.
        replicated = 0
        new_log: deque = deque()
        per_trade_size = max(int(rt_size / max(delta, 1)), 1) if rt_size else 0
        # Spread synthetic timestamps backward over a short window so they
        # land inside the 1 s bucket without going past now_mono.
        # Anchor is the original burst ts; spread across last ~0.25 s.
        for entry in self._trade_log:
            e_ts, e_price, e_sz, e_kind = entry
            if e_kind == "provisional" and abs(e_ts - ts) < 1e-9 and replicated == 0:
                # Inflate this entry into `delta` exact entries
                spread = 0.25  # ~one snapshot window
                step = spread / max(delta - 1, 1) if delta > 1 else 0.0
                for i in range(delta):
                    sub_ts = e_ts - (delta - 1 - i) * step
                    new_log.append((sub_ts, e_price, per_trade_size or e_sz, "exact"))
                replicated = delta
            else:
                new_log.append(entry)
        if replicated:
            self._trade_log = new_log
            # Patch cum_trade_count: provisional added 1, true count is delta
            self._cum_trade_count += (delta - 1)

    def _compute_windows(self, now_mono: float) -> dict:
        """
        Single O(n) scan of the bounded trade log, bucketing into all windows.
        n is bounded by DEQUE_HISTORY_SECONDS of history.
        """
        counts = {w: 0 for w in WINDOWS}
        volumes = {w: 0 for w in WINDOWS}
        dollar_vols = {w: 0.0 for w in WINDOWS}

        for ts, price, sz, _kind in self._trade_log:
            age = now_mono - ts
            for w in WINDOWS:
                if age <= w:
                    counts[w] += 1
                    volumes[w] += sz
                    dollar_vols[w] += price * sz

        out = {}
        for w in WINDOWS:
            c = counts[w]
            out[f"trades_in_{w}s"] = c
            out[f"volume_in_{w}s"] = volumes[w]
            out[f"dollar_vol_in_{w}s"] = dollar_vols[w]
            out[f"trade_rate_{w}s"] = c / w  # trades per second
            out[f"avg_iti_{w}s"] = (w / c) if c > 0 else None  # avg seconds between trades in the window

        # Acceleration ratios (window-vs-window)
        r10 = out["trade_rate_10s"]
        out["accel_1s_vs_10s"] = (out["trade_rate_1s"] / r10) if r10 > 0 else None
        out["accel_2s_vs_10s"] = (out["trade_rate_2s"] / r10) if r10 > 0 else None
        out["accel_5s_vs_10s"] = (out["trade_rate_5s"] / r10) if r10 > 0 else None

        return out

    def _detect_surge(self, w: dict, iti: Optional[float]) -> tuple:
        """
        Returns (bool, reason_str).

        Three independent rules. A surge fires if any rule trips.
        All comparisons are window-vs-window — no external baseline required.
        Live from trade #1 — no warmup period.
        """
        reasons = []

        # Rule A: 1s rate sharply above 10s rolling rate
        accel_1s = w.get("accel_1s_vs_10s")
        if (w["trades_in_1s"] >= SURGE_MIN_TRADES_1S
                and accel_1s is not None
                and accel_1s >= SURGE_ACCEL_1S_VS_10S):
            reasons.append(f"rate_1s/rate_10s={accel_1s:.1f}x")

        # Rule B: 5s rate sustainedly above 10s rolling rate
        accel_5s = w.get("accel_5s_vs_10s")
        if (w["trades_in_5s"] >= SURGE_MIN_TRADES_5S
                and accel_5s is not None
                and accel_5s >= SURGE_ACCEL_5S_VS_10S):
            reasons.append(f"rate_5s/rate_10s={accel_5s:.1f}x")

        # Rule C: current ITI collapsed relative to recent 10s average ITI
        # avg_iti_10s = 10 / trades_in_10s — after a quiet tape (few trades in 10s),
        # a burst fires when the new gap is < avg_iti_10s / SURGE_ITI_COMPRESS_RATIO.
        avg_iti_10s = w.get("avg_iti_10s")
        if (iti is not None
                and avg_iti_10s is not None
                and avg_iti_10s > SURGE_ITI_COMPRESS_MIN_AVG
                and iti < avg_iti_10s / SURGE_ITI_COMPRESS_RATIO):
            reasons.append(f"iti_compress:{avg_iti_10s:.1f}s→{iti:.2f}s")

        return (len(reasons) > 0, "|".join(reasons))

    # ------------------------------------------------------------------
    # Generic tick callbacks (from reqMktData)
    # ------------------------------------------------------------------

    def tickPrice(self, reqId, tickType, price, attrib):
        rec = {
            "event_type": "TICK_PRICE",
            "local_arrival_time": time.time(),
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "price": price,
        }
        # 1 = BID, 2 = ASK, 37 = MARK_PRICE, 75 = LAST_RTH_TRADE
        if tickType == 1 and price > 0:
            self._last_bid = price
        elif tickType == 2 and price > 0:
            self._last_ask = price
        elif tickType == 37 and price > 0:
            self._mark_price = price
            rec["mark_price"] = price
        elif tickType == 75 and price > 0:
            self._last_rth_trade = price
            rec["last_rth_trade"] = price
        self.records.append(rec)

    def tickSize(self, reqId, tickType, size):
        sz = float(size) if size else 0.0
        rec = {
            "event_type": "TICK_SIZE",
            "local_arrival_time": time.time(),
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "size": sz,
        }
        # 0 = BID_SIZE, 3 = ASK_SIZE, 54 = TRADE_COUNT, 89 = SHORTABLE_SHARES
        if tickType == 0:
            self._last_bid_size = sz
        elif tickType == 3:
            self._last_ask_size = sz
        elif tickType == 54:
            new_count = int(sz)
            self._tws_trade_count = new_count
            rec["tws_trade_count"] = new_count
            # Reconcile aggregated RTVolume bursts against the true count
            self._reconcile_trade_count(time.perf_counter(), new_count)
        elif tickType == 89:
            self._shortable_shares = sz
            rec["shortable_shares"] = sz
        self.records.append(rec)

    def tickGeneric(self, reqId, tickType, value):
        rec = {
            "event_type": "TICK_GENERIC",
            "local_arrival_time": time.time(),
            "local_arrival_iso": datetime.now().isoformat(timespec="milliseconds"),
            "tick_type": tickType,
            "value": value,
        }
        # 46 = SHORTABLE, 49 = HALTED, 55 = TRADE_RATE, 56 = VOLUME_RATE
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
        # Capture wall + monotonic at entry — _process_trade_event uses
        # perf_counter for the surge timeline.
        now_mono = time.perf_counter()
        now_wall = time.time()

        # 48 = RTVolume (tick 233), 77 = RTTradeVolume (tick 375)
        # Format: "price;size;time_ms;total_volume;vwap;single_trade_flag"
        if tickType in (48, 77) and value:
            try:
                parts = value.split(";")
                if len(parts) >= 6 and parts[0] and parts[1]:
                    rt_price = float(parts[0])
                    rt_size = int(parts[1])
                    rt_time_ms = int(parts[2]) if parts[2] else None
                    rt_total_volume = int(parts[3]) if parts[3] else None
                    vwap = float(parts[4]) if parts[4] else None
                    single_trade_flag = (parts[5].lower() == "true")
                    rt_source = "RTVolume" if tickType == 48 else "RTTradeVolume"
                    if vwap:
                        self._vwap = vwap
                    if rt_size > 0:
                        # Real trade event — drive the surge hot path.
                        # No separate TICK_STRING record is emitted; the
                        # TRADE record _process_trade_event appends already
                        # carries rt_source, rt_total_volume, single_trade_flag.
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

        # Non-trade tickString (heartbeat, parse failure, or non-RT tickType):
        # record raw for trace.
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
        """
        Stoikov microprice: (bid * ask_sz + ask * bid_sz) / (bid_sz + ask_sz)

        Weights each side by OPPOSITE side size so that heavy ask -> pulls
        fair value toward ask (sellers pressuring) and vice versa.
        """
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
    """Preferred column ordering for the output DataFrame."""
    cols = [
        "event_type",
        "local_arrival_time", "local_arrival_iso", "Time", "local_mono_time",
        "exchange_time_epoch", "exchange_time_iso",
        "tick_type", "rt_source",
        # Trade payload
        "price", "size", "exchange", "special_conditions",
        "past_limit", "unreported",
        # Quote snapshot
        "bid", "ask", "bid_size", "ask_size",
        "spread", "spread_pct", "midprice", "microprice",
        # Cumulative
        "cum_volume", "cum_trade_count", "cum_dollar_volume", "vwap",
        # Timing
        "inter_trade_time_sec", "session_age_sec",
    ]
    for w in WINDOWS:
        cols += [
            f"trades_in_{w}s",
            f"volume_in_{w}s",
            f"dollar_vol_in_{w}s",
            f"trade_rate_{w}s",
            f"avg_iti_{w}s",
        ]
    cols += [
        "accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s",
        "surge_detected", "surge_reason",
        # Status
        "halted", "shortable", "shortable_shares",
        "mark_price", "last_rth_trade",
        # TWS-smoothed comparators
        "tws_trade_rate_per_min", "tws_volume_rate_per_min", "tws_trade_count",
        # Raw RTVolume parse
        "rt_price", "rt_size", "rt_time_ms", "rt_total_volume",
        "rt_vwap", "rt_single_trade_flag",
        # Catch-all
        "value", "value_str",
    ]
    return cols


def main():
    parser = argparse.ArgumentParser(
        description="IBKR trade-frequency surge detector — baseline-free, window-vs-window",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol", required=True, help="Stock ticker, e.g. AAPL")
    parser.add_argument("--clientID", type=int, required=True,
                        help="IBKR API client ID (integer; must be unique per connection)")
    parser.add_argument("--lifeTime", required=True, type=parse_lifetime,
                        help="Collection duration in mm:ss format, e.g. 15:00")
    parser.add_argument("--output", required=True,
                        help="Output path - directory (auto-name) or full file path. "
                             "Auto-name format: SYMBOL_YYYY-MM-DD_HH-MM.txt (CSV content)")
    parser.add_argument("--host", default="127.0.0.1", help="IBKR Gateway/TWS host")
    parser.add_argument("--port", type=int, default=7497,
                        help="IBKR port (7497=paper TWS, 7496=live TWS, "
                             "4002=paper Gateway, 4001=live Gateway)")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-dir", default=None,
                        help="Directory for log file output. "
                             "Auto-name: SYMBOL_YYYY-MM-DD_HH-MM.log. "
                             "If omitted, logs go to stdout only.")
    args = parser.parse_args()

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

    # --- Connect ---
    app = IBKRSurgeApp(symbol)
    logging.info(f"Connecting to {args.host}:{args.port} ...")
    app.connect(args.host, args.port, clientId=args.clientID)

    api_thread = threading.Thread(target=app.run, daemon=True, name="ibapi-run")
    api_thread.start()

    if not app.connected_event.wait(timeout=10):
        logging.error("Failed to connect to IBKR within 10s. Is TWS/Gateway running?")
        app.disconnect()
        sys.exit(1)

    # --- Pre-04:00 ET gate ---
    # If launched in the closed-market window (20:00-04:00 ET), idle until
    # 04:00 ET before subscribing. Connection stays open so TWS/Gateway pings
    # keep the session alive.
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

    # --- Subscribe ---
    contract = make_contract(symbol)
    try:
        logging.info(f"Requesting mktData generic ticks {GENERIC_TICKS} (id={app.req_id_mkt})")
        app.reqMktData(app.req_id_mkt, contract, GENERIC_TICKS, False, False, [])

        # --- Collection loop ---
        end_time = time.time() + args.lifeTime
        logging.info(f"Collecting for {args.lifeTime}s. Ctrl+C to stop early.")

        while time.time() < end_time and not app.disconnected_flag:
            time.sleep(1)
            remaining = int(end_time - time.time())
            # Periodic progress every 30s
            if remaining > 0 and remaining % 30 == 0:
                logging.info(
                    f"[{remaining:>4}s left] events={len(app.records)} "
                    f"trades={app._cum_trade_count} vol={app._cum_volume} "
                    f"vwap={app._vwap} halted={app._halted}"
                )
    except KeyboardInterrupt:
        logging.info("Interrupted by user.")
    finally:
        # --- Shutdown (always runs — even on unhandled exceptions) ---
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

    # --- Persist ---
    if not app.records:
        logging.warning("No events captured; no output file written.")
        sys.exit(0)

    logging.info(f"Building DataFrame from {len(app.records)} events...")
    df = pd.DataFrame(app.records)

    if "local_arrival_iso" in df.columns:
        df["Time"] = df["local_arrival_iso"].str.slice(11, 23)

    # Reorder columns with our preferred ordering, leftovers appended
    preferred = build_column_order()
    existing = [c for c in preferred if c in df.columns]
    extras = [c for c in df.columns if c not in existing]
    df = df[existing + extras]

    # Summary
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

    # Write as CSV content with user-specified .txt extension
    df.to_csv(output_path, index=False, float_format="%.9f")
    logging.info(f"Wrote {len(df)} rows x {len(df.columns)} cols -> {output_path}")


if __name__ == "__main__":
    main()
