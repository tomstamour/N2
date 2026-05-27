#!/usr/bin/env python3
"""
IBKR Trade-Frequency Surge Detector (v4.4 — speculative-subscribe pool variant)
==============================================================================

Drop-in successor to ``trade_mole_4.3.py`` that adds a **two-phase stdin
protocol** in pool mode so the orchestrator (Orchestrator3.8.py) can issue
``reqMktData`` **before** the news trigger is fully evaluated. This removes
the ~200–600 ms first-tick latency from the critical path: by the time the
orchestrator decides "fire", the worker's tick stream is already warm.

Two runtime modes
-----------------

1. **Single-symbol mode** (no ``--pool-mode``) — byte-compatible with
   ``trade_mole_4.3.py`` for ad-hoc CLI runs.

       python3 trade_mole_4.4.py --symbol AAPL --clientID 7 \\
           --lifeTime 15:00 --output ./data/ --baseline-iti 0.2

2. **Pool mode** (``--pool-mode``, used only by Orchestrator3.8):

       python3 trade_mole_4.4.py --pool-mode --clientID 330 --port 4001 \\
           --log-dir /path/to/tm_logs

   New three-event stdin protocol:

     a. Connect → emit ``{"event":"ready","client_id":<N>}`` (stdout, flush).
     b. Read one JSON line — must be ``{"event":"subscribe","symbol":"ABC"}``.
        Worker calls ``reqMktData`` immediately. All incoming ticks are
        **discarded** while we wait for the orchestrator's decision.
     c. Read the next JSON line — either:

            {"event":"commit","baseline_iti":44.4,"lifeTime_sec":600,
             "output_dir":"/.../trade_mole_outputs"}

        → set baseline + flip ``_committed=True``, run the standard
          collection loop, write CSV, exit (one-shot post-commit).

            {"event":"abort"}

        → ``cancelMktData``, reset session state, re-emit ``ready``, loop
          back to (b).

     d. If neither ``commit`` nor ``abort`` arrives within
        ``POOL_SUBSCRIBED_TIMEOUT_SEC`` seconds, auto-abort and re-emit
        ``ready`` (protects against an orchestrator crash mid-decision).

   The ``IBKRSurgeApp`` class adds ONE field over v4.3 — ``_committed`` —
   plus an early-return guard on each tick callback when ``_committed`` is
   False. The surge math, deque trimming, RTVolume parser, tick-54
   reconciler and CSV writer are otherwise byte-for-byte unchanged.

   In pool mode stdout is reserved exclusively for protocol messages
   (``ready``); every log line is routed to ``--log-dir/pool_<clientID>_<ts>.log``.

Requires: ibapi, pandas. TWS or IB Gateway running with API enabled.
"""

import argparse
import json
import logging
import os
import queue
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

# Surge detection thresholds (denominator is the historical baseline supplied via CLI)
SURGE_MIN_TRADES_1S = 2        # need >= 2 trades in last 1s (absolute floor)
SURGE_RATE_RATIO_1_BASE = 5.0  # 1s rate >= 5x historical baseline rate
SURGE_RATE_RATIO_5_BASE = 3.0  # 5s rate >= 3x historical baseline rate (sustained)
SURGE_ITI_COLLAPSE = 2.0       # current inter-trade time < 2s ...
SURGE_PRIOR_ITI_MIN = 10.0     # ... while historical baseline avg ITI was > 10s

# Generic ticks for reqMktData
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221"

# Eastern Time zone for the pre-market start gate
ET = ZoneInfo("America/New_York")

# How long the worker will sit in the SUBSCRIBED state waiting for the
# orchestrator's commit/abort decision before auto-aborting and recycling.
# In practice FinBERT-headliner resolves in 50–200 ms, so 30 s is generous
# but bounded enough that a stuck orchestrator can't leave us paying for
# an open subscription forever.
POOL_SUBSCRIBED_TIMEOUT_SEC = 30.0


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

    The single cross-thread field added in v4.4 is ``_committed`` — a bool
    set by the main thread when the orchestrator's commit arrives. Reads
    in the ibapi callbacks are atomic under the GIL; a stale read can at
    most drop or admit one tick at the commit boundary, which is exactly
    the documented behavior for "discard pre-commit ticks".

    Hot-path ordering in _process_trade_event (driven by tickString's
    RTVolume branch):
      1) perf_counter timestamp
      2) append to trade deque (exact or provisional) + trim
      3) compute windows (O(n) over bounded deque)
      4) surge detection (vs. external historical baseline)
      5) build record dict and append

    Aggregation correction: when RTVolume aggregates multiple trades into a
    single tickString event (single_trade_flag=False), the event is appended
    as 'provisional' and later inflated to the true trade count by
    _reconcile_trade_count() when generic tick 54 (TRADE_COUNT) updates.

    Pool-mode note (v4.4): instances are constructed with a placeholder
    ``symbol`` / ``baseline_avg_iti`` and ``_committed=False``. The main
    thread sets ``symbol`` at SUBSCRIBE time (before reqMktData), but ticks
    are discarded by the ``_committed`` guard until the COMMIT command
    arrives and main flips ``_committed=True``. Baselines are written by
    main immediately before flipping the flag.
    """

    def __init__(self, symbol: str, baseline_avg_iti: float):
        EClient.__init__(self, wrapper=self)
        self.symbol = symbol
        # Store both forms — input is ITI (seconds); rate = 1/ITI is what
        # the surge ratios compare against. Keeping both means downstream
        # code from trade_mole.py can be ported unchanged.
        self._hist_baseline_avg_iti = baseline_avg_iti
        self._hist_baseline_trade_rate = 1.0 / baseline_avg_iti

        # Request IDs. Bumped on every (re-)subscribe in pool mode so that
        # late ticks from a cancelled subscription can't slip in under a
        # fresh subscription's reqId.
        self.req_id_mkt = 1001

        # Two-phase pool-mode gate. False during the speculative SUBSCRIBED
        # phase → every tick callback returns early. Flipped to True by the
        # main thread at COMMIT, after baselines are set. Single-symbol
        # mode sets this to True before reqMktData so the single-symbol
        # path is unchanged.
        self._committed = False

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
    # Per-session reset (pool mode, abort path)
    # ------------------------------------------------------------------

    def reset_session_state(self) -> None:
        """Wipe every per-symbol live field so the next SUBSCRIBE starts
        clean. Called from the main thread only on the ABORT path —
        ``_committed`` is False at this point, so the ibapi callback
        thread is already discarding anything in flight.

        ``req_id_mkt`` is bumped so any straggler ticks from the cancelled
        subscription land on a stale reqId and never collide with the next
        subscription's accounting. Baselines (``_hist_baseline_*``) are
        intentionally NOT reset here — they're rewritten on the next COMMIT
        anyway, and resetting to a placeholder would briefly divide-by-zero
        if a stray tick slipped past the _committed guard."""
        self.symbol = "<idle>"
        self._committed = False
        self.req_id_mkt += 1
        self._session_start_mono = None
        self._last_trade_mono = None
        self._last_bid = None
        self._last_ask = None
        self._last_bid_size = None
        self._last_ask_size = None
        self._cum_volume = 0
        self._cum_trade_count = 0
        self._cum_dollar_volume = 0.0
        self._vwap = None
        self._halted = 0
        self._shortable = None
        self._shortable_shares = None
        self._mark_price = None
        self._last_rth_trade = None
        self._tws_trade_rate = None
        self._tws_volume_rate = None
        self._tws_trade_count = None
        self._last_tws_trade_count = None
        self._tick54_seen = False
        self._pending_rt_events.clear()
        self._trade_log.clear()
        self.records.clear()

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
                f"| hist_baseline={win['hist_baseline_trade_rate']:.4f}/s "
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

        # Externally provided historical baseline (constant for the session)
        out["hist_baseline_trade_rate"] = self._hist_baseline_trade_rate
        out["hist_baseline_avg_iti"] = self._hist_baseline_avg_iti

        # Acceleration ratios
        r10 = out["trade_rate_10s"]
        rb = self._hist_baseline_trade_rate
        out["accel_1s_vs_10s"] = (out["trade_rate_1s"] / r10) if r10 > 0 else None
        out["accel_2s_vs_10s"] = (out["trade_rate_2s"] / r10) if r10 > 0 else None
        out["accel_5s_vs_10s"] = (out["trade_rate_5s"] / r10) if r10 > 0 else None
        out["accel_1s_vs_hist_baseline"] = out["trade_rate_1s"] / rb
        out["accel_2s_vs_hist_baseline"] = out["trade_rate_2s"] / rb
        out["accel_5s_vs_hist_baseline"] = out["trade_rate_5s"] / rb

        return out

    def _detect_surge(self, w: dict, iti: Optional[float]) -> tuple:
        """
        Returns (bool, reason_str).

        Three independent rules. A surge fires if any rule trips.
        Live from trade #1 — no warmup period (the historical baseline is
        always defined, supplied via --baseline-iti).
        """
        reasons = []

        # Rule A: short-window rate sharply exceeds historical baseline rate
        if (w["trades_in_1s"] >= SURGE_MIN_TRADES_1S
                and w["accel_1s_vs_hist_baseline"] >= SURGE_RATE_RATIO_1_BASE):
            reasons.append(f"rate_1s/hist_baseline={w['accel_1s_vs_hist_baseline']:.1f}x")

        # Rule B: 5s rate sustainedly above historical baseline (catches slower burns)
        if (w["trades_in_5s"] >= 5
                and w["accel_5s_vs_hist_baseline"] >= SURGE_RATE_RATIO_5_BASE):
            reasons.append(f"rate_5s/hist_baseline={w['accel_5s_vs_hist_baseline']:.1f}x")

        # Rule C: inter-trade time just collapsed relative to baseline average
        if (iti is not None and iti < SURGE_ITI_COLLAPSE
                and self._hist_baseline_avg_iti > SURGE_PRIOR_ITI_MIN):
            reasons.append(f"iti_collapse:{self._hist_baseline_avg_iti:.1f}s→{iti:.2f}s")

        return (len(reasons) > 0, "|".join(reasons))

    # ------------------------------------------------------------------
    # Generic tick callbacks (from reqMktData)
    #
    # The leading ``if not self._committed: return`` is the v4.4 gate: while
    # we're in the speculative SUBSCRIBED phase the ibapi thread can be
    # receiving ticks for the (cancelled-or-about-to-be-cancelled) request,
    # and we want zero state mutation. A stale read of _committed at the
    # commit boundary can at most drop or admit one tick — exactly the
    # "discard pre-commit ticks" semantics the design specifies.
    # ------------------------------------------------------------------

    def tickPrice(self, reqId, tickType, price, attrib):
        if not self._committed:
            return
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
        if not self._committed:
            return
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
        if not self._committed:
            return
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
        if not self._committed:
            return
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
        "hist_baseline_trade_rate", "hist_baseline_avg_iti",
        "accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s",
        "accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline", "accel_5s_vs_hist_baseline",
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


def _configure_logging(loglevel: str, log_path: Optional[str], pool_mode: bool) -> None:
    """Set up root logger. In pool mode, stdout is reserved for IPC, so the
    root logger gets ONLY a FileHandler — never a StreamHandler that writes
    to stdout/stderr. In single-symbol mode, behavior matches trade_mole_4.py
    (basicConfig + optional FileHandler)."""
    level = getattr(logging, loglevel)
    fmt = "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s"
    datefmt = "%H:%M:%S"

    if pool_mode:
        # File-only logging. If log_path is None we discard logs entirely
        # (NullHandler) rather than risk polluting stdout. The orchestrator
        # always supplies --log-dir for pool workers so this is just defensive.
        root = logging.getLogger()
        root.setLevel(level)
        for h in list(root.handlers):
            root.removeHandler(h)
        if log_path:
            fh = logging.FileHandler(log_path)
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
            root.addHandler(fh)
        else:
            root.addHandler(logging.NullHandler())
        return

    # Single-symbol mode: stream + optional file (trade_mole_4.py behavior)
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
    if log_path:
        fh = logging.FileHandler(log_path)
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
        logging.getLogger().addHandler(fh)
        logging.info(f"Log file: {log_path}")


def _open_pool_log(log_dir: str, client_id: int) -> str:
    """Resolve the pool-worker log path: ``{log_dir}/pool_{clientID}_{ts}.log``.
    One log file per worker process — covers idle / subscribed / committed
    phases. Symbol is appended via a log message after each subscribe."""
    os.makedirs(log_dir, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return os.path.join(log_dir, f"pool_{client_id}_{ts}.log")


def _emit_ready(client_id: int) -> None:
    """Write the READY JSON line on stdout and flush. Stdout is reserved
    in pool mode (logs go to file) so the orchestrator's reader thread
    sees exactly one line per ready edge."""
    print(json.dumps({"event": "ready", "client_id": client_id}), flush=True)


def _start_stdin_reader() -> "queue.Queue":
    """Spawn a daemon thread that reads stdin line-by-line and pushes each
    parsed JSON dict onto a Queue. On EOF or unparseable input the thread
    pushes a sentinel (None for EOF, or a {'_invalid': line} dict for parse
    error) and exits. Decouples the main state loop from blocking I/O so
    we can wait with a timeout in the SUBSCRIBED state."""
    q: "queue.Queue" = queue.Queue()

    def _reader():
        try:
            for raw in sys.stdin:
                line = raw.strip()
                if not line:
                    continue
                try:
                    q.put(json.loads(line))
                except json.JSONDecodeError as exc:
                    q.put({"_invalid": line, "_error": str(exc)})
        except Exception as exc:
            logging.debug(f"[pool] stdin reader exit: {exc}")
        finally:
            q.put(None)  # EOF sentinel

    threading.Thread(target=_reader, daemon=True, name="stdin-reader").start()
    return q


def _pool_handle_subscribe(app: "IBKRSurgeApp", cmd: dict) -> bool:
    """Bind the symbol onto the app, call reqMktData. Returns True if the
    subscribe succeeded; on validation failure logs and returns False so
    the caller can recycle. Note: ``_committed`` stays False — every tick
    that flows in until COMMIT lands is discarded by the callback guards."""
    symbol = cmd.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        logging.error(f"[pool] subscribe missing/invalid symbol: {cmd!r}")
        return False
    app.symbol = symbol.upper()
    contract = make_contract(app.symbol)
    try:
        logging.info(
            f"[pool] SUBSCRIBE {app.symbol}: reqMktData generic ticks "
            f"{GENERIC_TICKS} (id={app.req_id_mkt})"
        )
        app.reqMktData(app.req_id_mkt, contract, GENERIC_TICKS, False, False, [])
    except Exception as exc:
        logging.error(f"[pool] reqMktData failed for {app.symbol}: {exc}")
        return False
    return True


def _pool_handle_abort(app: "IBKRSurgeApp") -> None:
    """cancelMktData on the current reqId, then wipe per-session state.
    Idempotent — safe to call even if reqMktData failed."""
    logging.info(f"[pool] ABORT {app.symbol} (reqId={app.req_id_mkt})")
    try:
        app.cancelMktData(app.req_id_mkt)
    except Exception as exc:
        logging.debug(f"[pool] cancelMktData on abort raised: {exc}")
    # Give the ibapi thread a beat to absorb the cancel + any in-flight
    # ticks under the (still-False) _committed guard.
    time.sleep(0.25)
    app.reset_session_state()


def _pool_run_commit(
    app: "IBKRSurgeApp",
    api_thread: threading.Thread,
    cmd: dict,
) -> None:
    """Bind the baseline + output path from the COMMIT command, flip the
    ``_committed`` gate to True, run the lifetime collection loop, then
    tear down and persist the CSV. The worker process exits at the end
    of this function — pool reaper respawns the slot."""
    if "baseline_iti" not in cmd or "lifeTime_sec" not in cmd or "output_dir" not in cmd:
        raise ValueError(
            f"commit missing required keys (baseline_iti, lifeTime_sec, output_dir): {cmd!r}"
        )
    baseline_iti = float(cmd["baseline_iti"])
    if baseline_iti <= 0:
        raise ValueError(f"baseline_iti must be > 0, got {baseline_iti}")
    lifeTime_sec = int(cmd["lifeTime_sec"])
    if lifeTime_sec <= 0:
        raise ValueError(f"lifeTime_sec must be > 0, got {lifeTime_sec}")

    app._hist_baseline_avg_iti = baseline_iti
    app._hist_baseline_trade_rate = 1.0 / baseline_iti
    output_path = resolve_output_path(cmd["output_dir"], app.symbol)

    logging.info(
        f"[pool] COMMIT {app.symbol}: baseline_iti={baseline_iti:.2f}s "
        f"lifeTime={lifeTime_sec}s output={output_path}"
    )
    # Flip the gate AFTER baselines are set. Any tick arriving on the
    # ibapi thread from now on will run through _process_trade_event with
    # correct baselines.
    app._committed = True
    # Tell _run_subscription_cycle that reqMktData was already issued at
    # SUBSCRIBE time so it doesn't double-subscribe.
    app._subscribed_already = True

    # The pre-04:00 ET gate inside _run_subscription_cycle is a no-op
    # because the orchestrator only sends COMMIT during on-hours
    # (off-hours triggers never subscribe — they take the existing
    # threading.Timer deferral path on the orchestrator side).
    _run_subscription_cycle(app, api_thread, lifeTime_sec, output_path)


def main():
    parser = argparse.ArgumentParser(
        description="IBKR trade-frequency surge detector — reqMktData-only, external baseline (ITI input). "
                    "v4.4 adds a two-phase subscribe/commit/abort stdin protocol in pool mode.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    # All single-symbol args are optional at parse time; we validate them
    # below based on whether --pool-mode is set.
    parser.add_argument("--symbol", help="Stock ticker, e.g. AAPL (required unless --pool-mode)")
    parser.add_argument("--clientID", type=int, required=True,
                        help="IBKR API client ID (integer; must be unique per connection)")
    parser.add_argument("--lifeTime", type=parse_lifetime,
                        help="Collection duration in mm:ss format, e.g. 15:00 (required unless --pool-mode)")
    parser.add_argument("--output",
                        help="Output path - directory (auto-name) or full file path. "
                             "Auto-name format: SYMBOL_YYYY-MM-DD_HH-MM.txt (CSV content). "
                             "Required unless --pool-mode.")
    parser.add_argument("--baseline-iti", dest="baseline_avg_iti",
                        type=float,
                        help="Externally computed historical baseline average "
                             "inter-trade interval, in seconds (> 0). Used as the "
                             "denominator for surge-detection acceleration ratios. "
                             "Implied baseline trade rate = 1/baseline-iti. "
                             "Required unless --pool-mode (in pool mode it arrives via stdin).")
    parser.add_argument("--host", default="127.0.0.1", help="IBKR Gateway/TWS host")
    parser.add_argument("--port", type=int, default=7497,
                        help="IBKR port (7497=paper TWS, 7496=live TWS, "
                             "4002=paper Gateway, 4001=live Gateway)")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-dir", default=None,
                        help="Directory for log file output. "
                             "Single-symbol auto-name: SYMBOL_YYYY-MM-DD_HH-MM.log. "
                             "Pool auto-name: pool_<clientID>_<ts>.log. "
                             "Required when --pool-mode.")
    parser.add_argument("--pool-mode", action="store_true",
                        help="Run as a pre-connected pool worker driven by "
                             "Orchestrator3.8.py via a subscribe/commit/abort "
                             "JSON protocol on stdin.")
    args = parser.parse_args()

    # --- Mode-specific argument validation ---
    if args.pool_mode:
        if not args.log_dir:
            parser.error("--pool-mode requires --log-dir")
        # In pool mode the orchestrator supplies symbol/baseline/lifeTime/output
        # via stdin; CLI versions of those flags are ignored if present.
    else:
        missing = []
        if not args.symbol:               missing.append("--symbol")
        if not args.lifeTime:             missing.append("--lifeTime")
        if not args.output:                missing.append("--output")
        if args.baseline_avg_iti is None: missing.append("--baseline-iti")
        if missing:
            parser.error(f"single-symbol mode requires: {', '.join(missing)}")
        if args.baseline_avg_iti <= 0:
            parser.error(f"--baseline-iti must be > 0 (seconds), got {args.baseline_avg_iti}")

    # --- Logging setup (route off stdout when pool mode) ---
    if args.pool_mode:
        log_path = _open_pool_log(args.log_dir, args.clientID)
    elif args.log_dir:
        os.makedirs(args.log_dir, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        log_path = os.path.join(args.log_dir, f"{args.symbol.upper()}_{ts}.log")
    else:
        log_path = None
    _configure_logging(args.loglevel, log_path, pool_mode=args.pool_mode)

    # =========================================================================
    # POOL MODE — connect first, then loop over subscribe→(commit|abort)
    # =========================================================================
    if args.pool_mode:
        logging.info(f"[pool] worker starting: clientID={args.clientID} port={args.port}")
        # Placeholder symbol / baseline so __init__ doesn't divide by zero.
        # Both are overwritten by SUBSCRIBE / COMMIT before any tick is
        # admitted past the _committed guard.
        app = IBKRSurgeApp(symbol="<idle>", baseline_avg_iti=1.0)
        logging.info(f"[pool] connecting to {args.host}:{args.port} ...")
        app.connect(args.host, args.port, clientId=args.clientID)
        api_thread = threading.Thread(target=app.run, daemon=True, name="ibapi-run")
        api_thread.start()
        if not app.connected_event.wait(timeout=10):
            logging.error("[pool] failed to connect within 10s. Aborting.")
            app.disconnect()
            sys.exit(1)

        stdin_q = _start_stdin_reader()

        # Outer loop: emit READY, await SUBSCRIBE, await COMMIT-or-ABORT,
        # recycle on abort or exit on commit-complete.
        while True:
            _emit_ready(args.clientID)
            logging.info(
                f"[pool] READY emitted (reqId={app.req_id_mkt}); awaiting SUBSCRIBE"
            )

            # READY state: block indefinitely for next command. EOF → exit.
            cmd = stdin_q.get()
            if cmd is None:
                logging.info("[pool] stdin EOF (orchestrator closed pipe). Disconnecting.")
                break
            if "_invalid" in cmd:
                logging.error(f"[pool] READY: discarding unparseable line {cmd['_invalid']!r}: {cmd.get('_error')}")
                continue
            event = cmd.get("event")
            if event != "subscribe":
                logging.warning(
                    f"[pool] READY: expected event=subscribe, got {event!r} "
                    f"(payload={cmd!r}) — ignoring"
                )
                continue

            if not _pool_handle_subscribe(app, cmd):
                # Failed to issue reqMktData — recycle cleanly.
                app.reset_session_state()
                continue

            # SUBSCRIBED state: wait bounded for commit/abort. Auto-abort on
            # timeout or EOF so the worker always recovers.
            try:
                cmd2 = stdin_q.get(timeout=POOL_SUBSCRIBED_TIMEOUT_SEC)
            except queue.Empty:
                logging.warning(
                    f"[pool] SUBSCRIBED {app.symbol}: no commit/abort in "
                    f"{POOL_SUBSCRIBED_TIMEOUT_SEC:.0f}s — auto-aborting"
                )
                _pool_handle_abort(app)
                continue

            if cmd2 is None:
                logging.info(
                    f"[pool] SUBSCRIBED {app.symbol}: stdin EOF — aborting and exiting"
                )
                _pool_handle_abort(app)
                break
            if "_invalid" in cmd2:
                logging.error(
                    f"[pool] SUBSCRIBED {app.symbol}: unparseable command "
                    f"{cmd2['_invalid']!r} ({cmd2.get('_error')}) — aborting"
                )
                _pool_handle_abort(app)
                continue

            event2 = cmd2.get("event")
            if event2 == "abort":
                _pool_handle_abort(app)
                continue
            if event2 == "commit":
                try:
                    _pool_run_commit(app, api_thread, cmd2)
                except (ValueError, KeyError) as exc:
                    logging.error(
                        f"[pool] COMMIT {app.symbol}: invalid payload ({exc}) — aborting"
                    )
                    _pool_handle_abort(app)
                    continue
                # Commit always exits the process (one-shot post-commit).
                return
            logging.warning(
                f"[pool] SUBSCRIBED {app.symbol}: unexpected event={event2!r} — "
                f"treating as abort"
            )
            _pool_handle_abort(app)

        # Clean shutdown after EOF in READY state.
        app.disconnect()
        api_thread.join(timeout=5)
        sys.exit(0)

    # =========================================================================
    # SINGLE-SYMBOL MODE — byte-compatible with trade_mole_4.3.py
    # =========================================================================
    symbol = args.symbol.upper()
    output_path = resolve_output_path(args.output, symbol)
    logging.info(f"Symbol: {symbol}")
    logging.info(f"ClientID: {args.clientID}")
    logging.info(f"Lifetime: {args.lifeTime}s")
    logging.info(f"Output: {output_path}")
    logging.info(
        f"Historical baseline: avg ITI {args.baseline_avg_iti:.2f}s "
        f"(implied rate {1.0/args.baseline_avg_iti:.4f} trades/s)"
    )

    app = IBKRSurgeApp(symbol, baseline_avg_iti=args.baseline_avg_iti)
    # In single-symbol mode every tick is meaningful from the first subscribe,
    # so the _committed gate must be open before reqMktData is issued.
    app._committed = True
    logging.info(f"Connecting to {args.host}:{args.port} ...")
    app.connect(args.host, args.port, clientId=args.clientID)

    api_thread = threading.Thread(target=app.run, daemon=True, name="ibapi-run")
    api_thread.start()

    if not app.connected_event.wait(timeout=10):
        logging.error("Failed to connect to IBKR within 10s. Is TWS/Gateway running?")
        app.disconnect()
        sys.exit(1)

    _run_subscription_cycle(app, api_thread, args.lifeTime, output_path)


def _run_subscription_cycle(
    app: "IBKRSurgeApp",
    api_thread: threading.Thread,
    lifeTime_sec: int,
    output_path: str,
) -> None:
    """Single-symbol mode (and pool COMMIT post-flip) collection cycle:
    pre-04:00 ET gate, reqMktData (single-symbol only — pool mode already
    issued it at SUBSCRIBE), lifeTime collection loop, teardown, CSV write.

    In pool COMMIT context ``app._committed`` is True and ``reqMktData`` was
    already called at SUBSCRIBE time. We must NOT call reqMktData a second
    time. We detect "already subscribed" by checking whether records have
    started to flow OR by relying on the caller having flipped _committed
    AFTER the SUBSCRIBE call — both are guaranteed by the pool-mode caller.

    Behavior in single-symbol mode is preserved byte-for-byte from
    trade_mole_4.3.py."""
    # --- Pre-04:00 ET gate ---
    # In pool COMMIT this is a no-op because the orchestrator only fires
    # COMMIT during on-hours. In single-symbol mode this kept the original
    # off-hours wait behavior.
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
            app.disconnect(); api_thread.join(timeout=5)
            sys.exit(0)
        if app.disconnected_flag:
            logging.error("IBKR connection dropped during pre-04:00 wait. Aborting.")
            app.disconnect(); api_thread.join(timeout=5)
            sys.exit(1)
        logging.info("Reached 04:00 ET - subscribing to market data now.")

    # --- Subscribe (single-symbol path only) ---
    # Pool COMMIT sets ``_subscribed_already=True`` and already issued
    # reqMktData at SUBSCRIBE time — calling reqMktData again on the same
    # reqId would error. Single-symbol mode does not set this flag, so it
    # falls through to the subscribe call exactly as in v4.3.
    if not getattr(app, "_subscribed_already", False):
        contract = make_contract(app.symbol)
        logging.info(f"Requesting mktData generic ticks {GENERIC_TICKS} (id={app.req_id_mkt})")
        app.reqMktData(app.req_id_mkt, contract, GENERIC_TICKS, False, False, [])

    try:
        # --- Collection loop ---
        end_time = time.time() + lifeTime_sec
        logging.info(f"Collecting for {lifeTime_sec}s. Ctrl+C to stop early.")

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
