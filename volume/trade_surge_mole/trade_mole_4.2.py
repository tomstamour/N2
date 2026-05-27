#!/usr/bin/env python3
"""
IBKR Trade-Frequency Surge Detector — Shared-Client Manager (v4.2)
==================================================================

Refactor of ``trade_mole_4.py`` that consolidates many per-symbol "lines"
onto a single IBKR API client connection, so 3 long-lived clients
(clientIDs 100/101/102) can serve up to 30 concurrent symbols under the
32-client account cap — instead of one subprocess per symbol.

Two runtime modes
-----------------

1. **Manager mode** (default — invoked by Orchestrator3.6.py):
       python3 trade_mole_4.2.py --client-id 100 --port 4001 \
           --manager-log-dir /path/to/tm_logs

   The process connects one IBKR client, prints
   ``{"event":"ready","client_id":100}`` on stdout, then reads
   newline-delimited JSON commands from stdin:

       {"cmd":"launch","req_id":"...","symbol":"SOFI",
        "baseline_iti":44.2,"lifeTime_sec":600,
        "output_dir":"...","log_dir":"..."}
       {"cmd":"extend","req_id":"...","symbol":"SOFI","additional_sec":600}
       {"cmd":"shutdown"}

   For each command it emits a one-line JSON ack on stdout (accepted /
   full / extended / extend_failed / freed). All other output (info,
   warnings, errors) goes to a manager log file — stdout is reserved
   exclusively for the IPC ack stream.

2. **Single-symbol mode** (ad-hoc, backward compat with trade_mole_4.py):
       python3 trade_mole_4.2.py --client-id 100 --port 4001 \
           --single-symbol SOFI --baseline-iti 44.2 \
           --lifeTime 10:00 --output /tmp/out --log-dir /tmp/logs

   Allocates exactly one slot and exits when its lifeTime is up. Output
   files are byte-identical to a trade_mole_4.py run.

Two-stage hot path
------------------

To keep the EClient.run() callback thread fast (so one slow symbol can't
starve the others on the same client), all tick callbacks do *only*:

  1. perf_counter() timestamp
  2. dict lookup: reqId -> SymbolTracker
  3. queue.put((kind, payload, now_mono, now_wall))

The per-symbol worker thread drains its queue and runs the original
``_process_trade_event`` / surge-detection / record-building logic. Each
``SymbolTracker`` owns its own ``_trade_log``, ``_cum_*`` counters,
quote snapshot (``_last_bid/_last_ask/_last_bid_size/_last_ask_size``),
``_pending_rt_events`` aggregation queue, ``records`` buffer, and a
dedicated worker thread.

Lifetime management
-------------------

A 1-second sweeper thread expires slots past their ``end_time_mono``,
cancelling the IBKR subscription, signalling the worker, flushing the
CSV in ``finalize()``, and emitting ``{"event":"freed",...}``. The slot
index then becomes available for a new launch.

Extension
---------

When the manager receives an ``extend`` command for an already-active
symbol, it bumps the tracker's ``end_time_mono`` by ``additional_sec``
and increments an extension counter (capped at ``--max-extensions``,
default 3). The CSV keeps growing in place.

Pre-04:00 ET gating
-------------------

Removed from this script entirely — Orchestrator3.6.py owns all
time-of-day deferral via threading.Timer in its
``maybe_launch_trade_mole`` path. This script always subscribes to
market data immediately upon receiving a launch command.

Requires: ibapi, pandas. TWS or IB Gateway running with API enabled.
"""

import argparse
import json
import logging
import os
import queue
import signal
import sys
import threading
import time
from collections import deque
from datetime import datetime
from typing import Optional

import pandas as pd

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


# ============================================================================
# Configuration (mirrors trade_mole_4.py so output semantics stay identical)
# ============================================================================

# Rolling window sizes in seconds
WINDOWS = [1, 2, 3, 4, 5, 10]

# Deque trim cutoff — retain only the longest user-facing window
DEQUE_HISTORY_SECONDS = max(WINDOWS)

# Surge detection thresholds (denominator = externally supplied historical
# baseline trade rate)
SURGE_MIN_TRADES_1S = 2
SURGE_RATE_RATIO_1_BASE = 5.0
SURGE_RATE_RATIO_5_BASE = 3.0
SURGE_ITI_COLLAPSE = 2.0
SURGE_PRIOR_ITI_MIN = 10.0

# Generic ticks for reqMktData (same set as trade_mole_4.py)
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221"

# Per-tracker queue cap. Tick events at IBKR snapshot rate (~4 Hz) over
# the longest lifeTime (~hours w/ extensions) stay well under this.
TRACKER_QUEUE_MAXSIZE = 100_000

# Sweeper tick interval (seconds)
SWEEPER_INTERVAL_SEC = 1.0


# ============================================================================
# Small helpers (kept name-compatible with trade_mole_4.py where useful)
# ============================================================================

def parse_lifetime(s: str) -> int:
    """Parse 'mm:ss' -> total seconds."""
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


def resolve_output_path(output_arg: str, symbol: str, ts: Optional[str] = None) -> str:
    """
    Build a per-symbol CSV path.

    If ``output_arg`` is a directory (exists or ends with a separator),
    auto-name as ``SYMBOL_YYYY-MM-DD_HH-MM-SS.txt`` inside it. Otherwise
    treat ``output_arg`` as a full literal path.

    Seconds precision in the timestamp avoids collisions if the same
    symbol is launched twice within the same minute (e.g. after its
    first line freed and a new news event arrived).
    """
    ts = ts or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
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


def resolve_log_path(log_dir: str, symbol: str, ts: Optional[str] = None) -> str:
    """Build a per-symbol log path: ``{log_dir}/{SYMBOL}_{TS}.log``."""
    ts = ts or datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f"{symbol}_{ts}.log")


def make_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "STK"
    c.exchange = "SMART"
    c.primaryExchange = "NASDAQ"
    c.currency = "USD"
    return c


def build_column_order() -> list:
    """Preferred column ordering for the per-symbol CSV (same as v4)."""
    cols = [
        "event_type",
        "local_arrival_time", "local_arrival_iso", "Time", "local_mono_time",
        "exchange_time_epoch", "exchange_time_iso",
        "tick_type", "rt_source",
        "price", "size", "exchange", "special_conditions",
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
            f"trade_rate_{w}s",
            f"avg_iti_{w}s",
        ]
    cols += [
        "hist_baseline_trade_rate", "hist_baseline_avg_iti",
        "accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s",
        "accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline",
        "accel_5s_vs_hist_baseline",
        "surge_detected", "surge_reason",
        "halted", "shortable", "shortable_shares",
        "mark_price", "last_rth_trade",
        "tws_trade_rate_per_min", "tws_volume_rate_per_min", "tws_trade_count",
        "rt_price", "rt_size", "rt_time_ms", "rt_total_volume",
        "rt_vwap", "rt_single_trade_flag",
        "value", "value_str",
    ]
    return cols


# ============================================================================
# SymbolTracker — per-symbol state machine + worker thread
# ============================================================================

class SymbolTracker:
    """
    One per active "line". Owns everything that was app-level state in
    ``IBKRSurgeApp`` in trade_mole_4.py.

    Threading
    ---------
    * Constructor + ``start()`` + ``finalize()``: called by the manager
      / single-symbol main thread.
    * ``enqueue(...)``: called by the EClient.run() thread on every tick.
    * ``_run_worker()``: runs in this tracker's dedicated worker thread;
      sole writer of ``records``, ``_trade_log``, ``_cum_*``,
      ``_last_bid/ask/*``, ``_pending_rt_events``, ``_vwap``, etc.
    * ``end_time_mono``: written by main thread (extend), read by sweeper
      thread. Single-word write of a float; race is benign (an extend
      that lands the same instant the sweeper expires the slot is
      indistinguishable from one that lands a microsecond later).
    * ``extensions``: same — mutated under the SharedClient's state_lock
      so extend/expiry don't race semantically.
    """

    def __init__(
        self,
        symbol: str,
        baseline_iti: float,
        slot_idx: int,
        req_id: int,
        lifetime_sec: int,
        output_path: str,
        log_path: str,
        logger: logging.Logger,
        on_done: Optional[callable] = None,
    ):
        if baseline_iti <= 0:
            raise ValueError(f"baseline_iti must be > 0, got {baseline_iti}")

        self.symbol = symbol
        self.slot_idx = slot_idx
        self.req_id = req_id
        self.output_path = output_path
        self.log_path = log_path
        self.logger = logger
        self._on_done = on_done  # optional callback after finalize() returns

        # Baseline (constant for the session)
        self._hist_baseline_avg_iti = baseline_iti
        self._hist_baseline_trade_rate = 1.0 / baseline_iti

        # Lifetime
        now_mono = time.perf_counter()
        self.start_time_mono = now_mono
        self.end_time_mono = now_mono + lifetime_sec
        self.lifetime_sec = lifetime_sec
        self.extensions = 0
        self.finalized = False  # flips True exactly once

        # Live state (was self._* on IBKRSurgeApp)
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

        # Tick-54 reconciliation (aggregated RTVolume bursts)
        self._last_tws_trade_count: Optional[int] = None
        self._tick54_seen: bool = False
        self._pending_rt_events: deque = deque()

        # Rolling trade log: (mono_ts, price, size, kind) where
        # kind in {"exact", "provisional"}
        self._trade_log: deque = deque()

        # Captured event records -> DataFrame at finalize()
        self.records: list = []

        # Two-stage hot path: queue + worker thread
        self.in_queue: queue.Queue = queue.Queue(maxsize=TRACKER_QUEUE_MAXSIZE)
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._run_worker,
            name=f"tracker-{symbol}",
            daemon=True,
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self):
        """Spawn the per-symbol worker thread. Call after construction."""
        self._worker_thread.start()
        self.logger.info(
            f"START {self.symbol} slot={self.slot_idx} reqId={self.req_id} "
            f"lifeTime={self.lifetime_sec}s "
            f"baseline_iti={self._hist_baseline_avg_iti:.2f}s "
            f"(rate={self._hist_baseline_trade_rate:.4f}/s)"
        )

    def extend(self, additional_sec: int) -> float:
        """Bump end_time_mono by additional_sec. Returns new wall-clock end epoch."""
        self.end_time_mono += additional_sec
        self.extensions += 1
        new_end_epoch = time.time() + (self.end_time_mono - time.perf_counter())
        self.logger.info(
            f"EXTEND {self.symbol} by {additional_sec}s "
            f"(extensions={self.extensions})"
        )
        return new_end_epoch

    def stop_worker(self, timeout: float = 5.0):
        """Signal the worker thread to exit and join (best-effort)."""
        self._stop_event.set()
        try:
            self.in_queue.put_nowait(None)  # sentinel
        except queue.Full:
            pass  # the stop_event will be picked up on next loop iteration
        self._worker_thread.join(timeout=timeout)
        if self._worker_thread.is_alive():
            self.logger.warning(
                f"{self.symbol} worker did not stop within {timeout}s"
            )

    def finalize(self):
        """Flush records to CSV. Idempotent — safe to call twice."""
        if self.finalized:
            return
        self.finalized = True

        if not self.records:
            self.logger.warning(
                f"FINALIZE {self.symbol}: no events captured; no CSV written."
            )
            return

        self.logger.info(
            f"FINALIZE {self.symbol}: building DataFrame from "
            f"{len(self.records)} events..."
        )
        df = pd.DataFrame(self.records)

        if "local_arrival_iso" in df.columns:
            df["Time"] = df["local_arrival_iso"].str.slice(11, 23)

        preferred = build_column_order()
        existing = [c for c in preferred if c in df.columns]
        extras = [c for c in df.columns if c not in existing]
        df = df[existing + extras]

        n_trades = int((df["event_type"] == "TRADE").sum()) \
            if "event_type" in df.columns else 0
        n_surges = int((df["surge_detected"] == True).sum()) \
            if "surge_detected" in df.columns else 0

        self.logger.info(
            f"SUMMARY {self.symbol}: total_events={len(df)} trades={n_trades} "
            f"surge_events={n_surges} cum_vol={self._cum_volume} "
            f"cum_trades={self._cum_trade_count}"
        )

        try:
            df.to_csv(self.output_path, index=False, float_format="%.9f")
            self.logger.info(
                f"WROTE {self.symbol}: {len(df)} rows x {len(df.columns)} cols "
                f"-> {self.output_path}"
            )
        except Exception as e:
            self.logger.exception(f"Failed to write CSV {self.output_path}: {e}")

        if self._on_done is not None:
            try:
                self._on_done(self)
            except Exception:
                self.logger.exception("on_done callback raised")

    # ------------------------------------------------------------------
    # Hot-path entry (EClient.run() thread — keep tiny)
    # ------------------------------------------------------------------

    def enqueue(self, event: tuple):
        """Push an event tuple onto the worker queue. Tick callback context."""
        try:
            self.in_queue.put_nowait(event)
        except queue.Full:
            # Drop the tick rather than block the EClient thread. Log once
            # at most every few seconds via a coarse rate-limit.
            self.logger.error(
                f"{self.symbol} queue full ({TRACKER_QUEUE_MAXSIZE}); dropping event"
            )

    # ------------------------------------------------------------------
    # Worker thread loop
    # ------------------------------------------------------------------

    def _run_worker(self):
        while not self._stop_event.is_set():
            try:
                event = self.in_queue.get(timeout=0.25)
            except queue.Empty:
                continue
            if event is None:
                break
            kind = event[0]
            try:
                if kind == "string":
                    self._handle_string(*event[1:])
                elif kind == "price":
                    self._handle_price(*event[1:])
                elif kind == "size":
                    self._handle_size(*event[1:])
                elif kind == "generic":
                    self._handle_generic(*event[1:])
                else:
                    self.logger.warning(f"Unknown event kind: {kind!r}")
            except Exception as e:
                self.logger.exception(
                    f"{self.symbol} worker error on {kind}: {e}"
                )

    # ------------------------------------------------------------------
    # Tick handlers (run on the worker thread)
    # Direct ports of IBKRSurgeApp.tickPrice/tickSize/tickGeneric/tickString
    # from trade_mole_4.py, but mutating tracker state instead of app state.
    # ------------------------------------------------------------------

    def _handle_price(self, tick_type: int, price: float,
                      now_mono: float, now_wall: float):
        rec = {
            "event_type": "TICK_PRICE",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall)
                .isoformat(timespec="milliseconds"),
            "tick_type": tick_type,
            "price": price,
        }
        # 1=BID, 2=ASK, 37=MARK_PRICE, 75=LAST_RTH_TRADE
        if tick_type == 1 and price > 0:
            self._last_bid = price
        elif tick_type == 2 and price > 0:
            self._last_ask = price
        elif tick_type == 37 and price > 0:
            self._mark_price = price
            rec["mark_price"] = price
        elif tick_type == 75 and price > 0:
            self._last_rth_trade = price
            rec["last_rth_trade"] = price
        self.records.append(rec)

    def _handle_size(self, tick_type: int, size: float,
                     now_mono: float, now_wall: float):
        sz = float(size) if size else 0.0
        rec = {
            "event_type": "TICK_SIZE",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall)
                .isoformat(timespec="milliseconds"),
            "tick_type": tick_type,
            "size": sz,
        }
        # 0=BID_SIZE, 3=ASK_SIZE, 54=TRADE_COUNT, 89=SHORTABLE_SHARES
        if tick_type == 0:
            self._last_bid_size = sz
        elif tick_type == 3:
            self._last_ask_size = sz
        elif tick_type == 54:
            new_count = int(sz)
            self._tws_trade_count = new_count
            rec["tws_trade_count"] = new_count
            self._reconcile_trade_count(now_mono, new_count)
        elif tick_type == 89:
            self._shortable_shares = sz
            rec["shortable_shares"] = sz
        self.records.append(rec)

    def _handle_generic(self, tick_type: int, value: float,
                        now_mono: float, now_wall: float):
        rec = {
            "event_type": "TICK_GENERIC",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall)
                .isoformat(timespec="milliseconds"),
            "tick_type": tick_type,
            "value": value,
        }
        # 46=SHORTABLE, 49=HALTED, 55=TRADE_RATE, 56=VOLUME_RATE
        if tick_type == 46:
            self._shortable = value
            rec["shortable"] = value
        elif tick_type == 49:
            self._halted = int(value)
            rec["halted"] = self._halted
            if self._halted:
                self.logger.warning(f"⛔ HALT detected {self.symbol}: code={self._halted}")
        elif tick_type == 55:
            self._tws_trade_rate = value
            rec["tws_trade_rate_per_min"] = value
        elif tick_type == 56:
            self._tws_volume_rate = value
            rec["tws_volume_rate_per_min"] = value
        self.records.append(rec)

    def _handle_string(self, tick_type: int, value: str,
                       now_mono: float, now_wall: float):
        # 48=RTVolume (tick 233), 77=RTTradeVolume (tick 375)
        # Format: "price;size;time_ms;total_volume;vwap;single_trade_flag"
        if tick_type in (48, 77) and value:
            try:
                parts = value.split(";")
                if len(parts) >= 6 and parts[0] and parts[1]:
                    rt_price = float(parts[0])
                    rt_size = int(parts[1])
                    rt_time_ms = int(parts[2]) if parts[2] else None
                    rt_total_volume = int(parts[3]) if parts[3] else None
                    vwap = float(parts[4]) if parts[4] else None
                    single_trade_flag = (parts[5].lower() == "true")
                    rt_source = "RTVolume" if tick_type == 48 else "RTTradeVolume"
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
                self.logger.debug(f"RTVolume parse error on {value!r}: {e}")

        # Non-trade tickString — store raw for trace
        self.records.append({
            "event_type": "TICK_STRING",
            "local_arrival_time": now_wall,
            "local_arrival_iso": datetime.fromtimestamp(now_wall)
                .isoformat(timespec="milliseconds"),
            "tick_type": tick_type,
            "value_str": value,
        })

    # ------------------------------------------------------------------
    # Trade-event hot path (ported from trade_mole_4.py:287-407)
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
        if self._session_start_mono is None:
            self._session_start_mono = now_mono

        # Inter-trade time
        iti = None
        if self._last_trade_mono is not None:
            iti = now_mono - self._last_trade_mono
        self._last_trade_mono = now_mono

        # Cumulative totals (patched by reconciler for aggregated events)
        self._cum_trade_count += 1
        self._cum_volume += size
        self._cum_dollar_volume += price * size

        # Append to trade log
        if single_trade_flag:
            self._trade_log.append((now_mono, price, size, "exact"))
        else:
            self._trade_log.append((now_mono, price, size, "provisional"))
            self._pending_rt_events.append((now_mono, size, rt_total_volume, price))

        # Trim trade log to deque history window (10s)
        cutoff = now_mono - DEQUE_HISTORY_SECONDS
        while self._trade_log and self._trade_log[0][0] < cutoff:
            self._trade_log.popleft()

        # Drop pending entries older than 2s (tick-54 lag tolerance)
        pending_cutoff = now_mono - 2.0
        while (self._pending_rt_events
               and self._pending_rt_events[0][0] < pending_cutoff):
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
            "local_arrival_iso": datetime.fromtimestamp(now_wall)
                .isoformat(timespec="milliseconds"),
            "local_mono_time": now_mono,
            "exchange_time_epoch": (rt_time_ms / 1000.0) if rt_time_ms else None,
            "exchange_time_iso": (
                datetime.fromtimestamp(rt_time_ms / 1000.0)
                .isoformat(timespec="milliseconds")
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

        if surge:
            iti_str = f"{iti:.2f}s" if iti is not None else "n/a"
            self.logger.warning(
                f"\U0001F680 SURGE {self.symbol} @ ${price:.4f} sz={size} "
                f"| trades_1s={win['trades_in_1s']} "
                f"rate_1s={win['trade_rate_1s']:.1f}/s "
                f"| hist_baseline={win['hist_baseline_trade_rate']:.4f}/s "
                f"| iti={iti_str} | {reason}"
            )

    def _reconcile_trade_count(self, now_mono: float, new_count: int):
        """Drain one pending provisional and inflate to `delta` exact entries."""
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
        ts, rt_size, _rt_tot_vol, price = self._pending_rt_events.popleft()

        replicated = 0
        new_log: deque = deque()
        per_trade_size = max(int(rt_size / max(delta, 1)), 1) if rt_size else 0
        for entry in self._trade_log:
            e_ts, e_price, e_sz, e_kind = entry
            if (e_kind == "provisional" and abs(e_ts - ts) < 1e-9
                    and replicated == 0):
                spread = 0.25
                step = spread / max(delta - 1, 1) if delta > 1 else 0.0
                for i in range(delta):
                    sub_ts = e_ts - (delta - 1 - i) * step
                    new_log.append((sub_ts, e_price, per_trade_size or e_sz, "exact"))
                replicated = delta
            else:
                new_log.append(entry)
        if replicated:
            self._trade_log = new_log
            # Patch cum_trade_count: provisional added 1, true is delta
            self._cum_trade_count += (delta - 1)

    def _compute_windows(self, now_mono: float) -> dict:
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
            out[f"trade_rate_{w}s"] = c / w
            out[f"avg_iti_{w}s"] = (w / c) if c > 0 else None

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
        return out

    def _detect_surge(self, w: dict, iti: Optional[float]) -> tuple:
        reasons = []
        if (w["trades_in_1s"] >= SURGE_MIN_TRADES_1S
                and w["accel_1s_vs_hist_baseline"] >= SURGE_RATE_RATIO_1_BASE):
            reasons.append(f"rate_1s/hist_baseline={w['accel_1s_vs_hist_baseline']:.1f}x")
        if (w["trades_in_5s"] >= 5
                and w["accel_5s_vs_hist_baseline"] >= SURGE_RATE_RATIO_5_BASE):
            reasons.append(f"rate_5s/hist_baseline={w['accel_5s_vs_hist_baseline']:.1f}x")
        if (iti is not None and iti < SURGE_ITI_COLLAPSE
                and self._hist_baseline_avg_iti > SURGE_PRIOR_ITI_MIN):
            reasons.append(
                f"iti_collapse:{self._hist_baseline_avg_iti:.1f}s→{iti:.2f}s"
            )
        return (len(reasons) > 0, "|".join(reasons))

    # ------------------------------------------------------------------
    # Quote helpers
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
# SharedClient — one IBKR connection, up to N concurrent SymbolTrackers
# ============================================================================

class SharedClient(EWrapper, EClient):
    """
    Wraps a single IBKR API client connection and dispatches tick callbacks
    to up to ``max_slots`` SymbolTrackers. The EClient.run() thread does
    only O(1) dispatch (perf_counter + dict lookup + queue.put_nowait);
    all per-symbol processing runs on per-symbol worker threads.

    Request-ID allocation: ``req_id = REQ_ID_BASE + slot_idx`` so reqIds
    are 1001..1001+max_slots-1 (default 1001..1010).
    """

    REQ_ID_BASE = 1001

    def __init__(
        self,
        client_id: int,
        max_slots: int,
        manager_logger: logging.Logger,
        log_dir: str,
        emit_fn: Optional[callable] = None,
        propagate_tracker_logs: bool = False,
    ):
        EClient.__init__(self, wrapper=self)
        self.client_id = client_id
        self.max_slots = max_slots
        self.manager_logger = manager_logger
        self.log_dir = log_dir
        self._emit = emit_fn  # callable(dict) -> None ; None in single-symbol mode
        # When True (single-symbol CLI), per-tracker log messages also reach
        # the root logger so they appear on stdout — matches trade_mole_4.py
        # behavior. When False (manager IPC), per-tracker logs stay in their
        # own file so stdout remains exclusively the IPC ack channel.
        self._propagate_tracker_logs = propagate_tracker_logs

        # Per-slot state
        self._slots: list = [None] * max_slots  # list[Optional[SymbolTracker]]
        self._reqid_to_tracker: dict = {}
        self._symbol_to_tracker: dict = {}

        # state_lock guards _slots, _reqid_to_tracker, _symbol_to_tracker
        # AND tracker.extensions / end_time_mono mutations from main thread.
        # Tick callbacks do NOT take it — they do a raw dict.get(), which is
        # GIL-atomic for CPython.
        self._state_lock = threading.RLock()

        # Connection signalling
        self.connected_event = threading.Event()
        self.disconnected_flag = False

        # Sweeper / shutdown
        self.shutdown_event = threading.Event()
        self._sweeper_thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # EWrapper overrides
    # ------------------------------------------------------------------

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2107, 2158, 2119, 2100, 2108):
            self.manager_logger.info(f"[IBKR {errorCode}] {errorString}")
            return
        # Annotate with symbol if reqId matches a known tracker
        tracker = self._reqid_to_tracker.get(reqId)
        sym = tracker.symbol if tracker else "?"
        self.manager_logger.warning(
            f"[IBKR err {errorCode}] reqId={reqId} ({sym}): {errorString}"
        )

    def nextValidId(self, orderId):
        self.manager_logger.info(
            f"Connected to IBKR. clientId={self.client_id} nextValidId={orderId}"
        )
        self.connected_event.set()

    def connectionClosed(self):
        self.disconnected_flag = True
        self.manager_logger.info(f"IBKR connection closed (clientId={self.client_id})")

    # ------------------------------------------------------------------
    # Tick callbacks (HOT PATH — keep tiny)
    # ------------------------------------------------------------------

    def tickPrice(self, reqId, tickType, price, attrib):
        now_mono = time.perf_counter()
        now_wall = time.time()
        tracker = self._reqid_to_tracker.get(reqId)
        if tracker is not None:
            tracker.enqueue(("price", tickType, price, now_mono, now_wall))

    def tickSize(self, reqId, tickType, size):
        now_mono = time.perf_counter()
        now_wall = time.time()
        tracker = self._reqid_to_tracker.get(reqId)
        if tracker is not None:
            tracker.enqueue(("size", tickType, size, now_mono, now_wall))

    def tickGeneric(self, reqId, tickType, value):
        now_mono = time.perf_counter()
        now_wall = time.time()
        tracker = self._reqid_to_tracker.get(reqId)
        if tracker is not None:
            tracker.enqueue(("generic", tickType, value, now_mono, now_wall))

    def tickString(self, reqId, tickType, value):
        now_mono = time.perf_counter()
        now_wall = time.time()
        tracker = self._reqid_to_tracker.get(reqId)
        if tracker is not None:
            tracker.enqueue(("string", tickType, value, now_mono, now_wall))

    # ------------------------------------------------------------------
    # Public API (called by manager-mode stdin handler or single-symbol main)
    # ------------------------------------------------------------------

    def active_slot_count(self) -> int:
        with self._state_lock:
            return sum(1 for s in self._slots if s is not None)

    def has_symbol(self, symbol: str) -> bool:
        with self._state_lock:
            return symbol in self._symbol_to_tracker

    def allocate_slot(
        self,
        symbol: str,
        baseline_iti: float,
        lifetime_sec: int,
        output_dir: str,
        log_dir: str,
    ) -> Optional[tuple]:
        """
        Allocate a free slot and subscribe to market data for ``symbol``.
        Returns ``(slot_idx, end_time_epoch)`` on success, or ``None`` if
        no free slot is available or the symbol is already active.
        """
        with self._state_lock:
            if symbol in self._symbol_to_tracker:
                self.manager_logger.info(
                    f"allocate_slot {symbol}: already active "
                    f"in slot {self._symbol_to_tracker[symbol].slot_idx}"
                )
                return None
            slot_idx = next(
                (i for i, s in enumerate(self._slots) if s is None),
                None,
            )
            if slot_idx is None:
                return None

            req_id = self.REQ_ID_BASE + slot_idx
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            output_path = resolve_output_path(output_dir, symbol, ts=ts)
            log_path = resolve_log_path(log_dir, symbol, ts=ts)

            tracker_logger = self._build_tracker_logger(symbol, log_path)
            tracker = SymbolTracker(
                symbol=symbol,
                baseline_iti=baseline_iti,
                slot_idx=slot_idx,
                req_id=req_id,
                lifetime_sec=lifetime_sec,
                output_path=output_path,
                log_path=log_path,
                logger=tracker_logger,
            )

            self._slots[slot_idx] = tracker
            self._reqid_to_tracker[req_id] = tracker
            self._symbol_to_tracker[symbol] = tracker

        # Start worker before subscribing so the queue drains immediately
        tracker.start()

        contract = make_contract(symbol)
        try:
            self.reqMktData(req_id, contract, GENERIC_TICKS, False, False, [])
            self.manager_logger.info(
                f"reqMktData {symbol} reqId={req_id} slot={slot_idx} "
                f"baseline_iti={baseline_iti:.2f}s lifetime={lifetime_sec}s"
            )
        except Exception as e:
            self.manager_logger.exception(
                f"reqMktData failed for {symbol}: {e}"
            )
            # Roll back the allocation
            with self._state_lock:
                self._slots[slot_idx] = None
                self._reqid_to_tracker.pop(req_id, None)
                self._symbol_to_tracker.pop(symbol, None)
            tracker.stop_worker(timeout=2.0)
            return None

        # end_time_epoch = wall-clock equivalent of tracker.end_time_mono
        end_time_epoch = time.time() + (tracker.end_time_mono - time.perf_counter())
        return (slot_idx, end_time_epoch)

    def extend_symbol(
        self,
        symbol: str,
        additional_sec: int,
        max_extensions: int,
    ) -> Optional[tuple]:
        """
        Bump an active symbol's deadline. Returns ``(new_end_time_epoch,
        extensions)`` on success, ``None`` if symbol unknown, or
        ``("max_extensions", extensions)`` if cap already reached.
        """
        with self._state_lock:
            tracker = self._symbol_to_tracker.get(symbol)
            if tracker is None:
                return None
            if tracker.extensions >= max_extensions:
                return ("max_extensions", tracker.extensions)
            new_end_epoch = tracker.extend(additional_sec)
            return (new_end_epoch, tracker.extensions)

    def free_slot(self, slot_idx: int) -> Optional[str]:
        """
        Free the given slot — cancel subscription, finalize tracker (CSV),
        remove from maps. Returns the freed symbol name, or None if slot
        was already empty. Safe to call from sweeper or shutdown path.
        """
        with self._state_lock:
            tracker = self._slots[slot_idx]
            if tracker is None:
                return None
            symbol = tracker.symbol
            req_id = tracker.req_id
            self._slots[slot_idx] = None
            self._reqid_to_tracker.pop(req_id, None)
            self._symbol_to_tracker.pop(symbol, None)

        # Outside the lock — cancel + finalize may take time
        try:
            self.cancelMktData(req_id)
        except Exception as e:
            self.manager_logger.debug(f"cancelMktData({req_id}) error: {e}")
        tracker.stop_worker(timeout=5.0)
        tracker.finalize()
        return symbol

    # ------------------------------------------------------------------
    # Sweeper — expires slots past their end_time_mono
    # ------------------------------------------------------------------

    def start_sweeper(self):
        self._sweeper_thread = threading.Thread(
            target=self._sweeper_loop,
            name=f"sweeper-{self.client_id}",
            daemon=True,
        )
        self._sweeper_thread.start()

    def _sweeper_loop(self):
        while not self.shutdown_event.is_set():
            self.shutdown_event.wait(timeout=SWEEPER_INTERVAL_SEC)
            if self.shutdown_event.is_set():
                break
            now_mono = time.perf_counter()
            with self._state_lock:
                expired = [
                    (i, t) for i, t in enumerate(self._slots)
                    if t is not None and not t.finalized
                       and t.end_time_mono <= now_mono
                ]
            for slot_idx, tracker in expired:
                freed_symbol = self.free_slot(slot_idx)
                if freed_symbol is not None and self._emit is not None:
                    self._emit({
                        "event": "freed",
                        "symbol": freed_symbol,
                        "slot": slot_idx,
                    })

    # ------------------------------------------------------------------
    # Shutdown — flush every active tracker, cancel all, disconnect
    # ------------------------------------------------------------------

    def shutdown_all(self):
        self.shutdown_event.set()
        with self._state_lock:
            occupied = [i for i, s in enumerate(self._slots) if s is not None]
        for slot_idx in occupied:
            freed_symbol = self.free_slot(slot_idx)
            if freed_symbol is not None and self._emit is not None:
                self._emit({
                    "event": "freed",
                    "symbol": freed_symbol,
                    "slot": slot_idx,
                })
        if self._sweeper_thread is not None:
            self._sweeper_thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Per-tracker logger setup
    # ------------------------------------------------------------------

    def _build_tracker_logger(self, symbol: str, log_path: str) -> logging.Logger:
        """
        Create a dedicated logger writing to the per-symbol log file. Does
        NOT propagate to root — that keeps the manager log and stdout
        clean.
        """
        logger = logging.getLogger(f"tm.{self.client_id}.{symbol}.{id(log_path)}")
        logger.setLevel(logging.INFO)
        logger.propagate = self._propagate_tracker_logs
        # Clear any handlers from a prior allocation reusing this name (rare)
        for h in list(logger.handlers):
            logger.removeHandler(h)
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
            datefmt="%H:%M:%S",
        ))
        logger.addHandler(fh)
        return logger


# ============================================================================
# Manager mode — IPC over stdin/stdout
# ============================================================================

def _make_stdout_emitter():
    """Return a thread-safe emit(dict) that writes one JSON line per call."""
    lock = threading.Lock()
    def emit(event: dict):
        line = json.dumps(event, separators=(",", ":"))
        with lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    return emit


def _setup_manager_logger(client_id: int, manager_log_dir: str,
                          loglevel: str) -> logging.Logger:
    """Manager's own log -> a file (NOT stdout, which is reserved for IPC)."""
    os.makedirs(manager_log_dir, exist_ok=True)
    log_path = os.path.join(
        manager_log_dir,
        f"manager_{client_id}_{datetime.now().strftime('%Y-%m-%d')}.log",
    )
    logger = logging.getLogger(f"manager.{client_id}")
    logger.setLevel(getattr(logging, loglevel))
    logger.propagate = False
    for h in list(logger.handlers):
        logger.removeHandler(h)
    fh = logging.FileHandler(log_path)
    fh.setLevel(getattr(logging, loglevel))
    fh.setFormatter(logging.Formatter(
        "%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    ))
    logger.addHandler(fh)
    logger.info(f"Manager log: {log_path}")
    return logger


def _handle_command(client: SharedClient, cmd: dict, emit: callable,
                    manager_logger: logging.Logger, max_extensions: int):
    """Dispatch one parsed JSON command to the SharedClient and emit an ack."""
    name = cmd.get("cmd")
    req_id = cmd.get("req_id")

    if name == "launch":
        symbol = cmd["symbol"]
        baseline_iti = float(cmd["baseline_iti"])
        lifetime_sec = int(cmd["lifeTime_sec"])
        output_dir = cmd["output_dir"]
        log_dir = cmd["log_dir"]

        # Reject duplicates explicitly (Orchestrator should have caught these,
        # but be defensive — also lets manager handle missed acks gracefully).
        if client.has_symbol(symbol):
            emit({
                "event": "full",
                "req_id": req_id,
                "symbol": symbol,
                "active_slots": client.active_slot_count(),
                "reason": "duplicate_symbol",
            })
            return

        if client.active_slot_count() >= client.max_slots:
            emit({
                "event": "full",
                "req_id": req_id,
                "symbol": symbol,
                "active_slots": client.active_slot_count(),
            })
            return

        result = client.allocate_slot(
            symbol=symbol,
            baseline_iti=baseline_iti,
            lifetime_sec=lifetime_sec,
            output_dir=output_dir,
            log_dir=log_dir,
        )
        if result is None:
            emit({
                "event": "full",
                "req_id": req_id,
                "symbol": symbol,
                "active_slots": client.active_slot_count(),
            })
            return
        slot_idx, end_time_epoch = result
        emit({
            "event": "accepted",
            "req_id": req_id,
            "symbol": symbol,
            "slot": slot_idx,
            "end_time_epoch": end_time_epoch,
        })
        return

    if name == "extend":
        symbol = cmd["symbol"]
        additional_sec = int(cmd["additional_sec"])
        result = client.extend_symbol(symbol, additional_sec, max_extensions)
        if result is None:
            emit({
                "event": "extend_failed",
                "req_id": req_id,
                "symbol": symbol,
                "reason": "not_active",
            })
            return
        if isinstance(result[0], str) and result[0] == "max_extensions":
            emit({
                "event": "extend_failed",
                "req_id": req_id,
                "symbol": symbol,
                "reason": "max_extensions",
                "extensions": result[1],
            })
            return
        new_end_epoch, extensions = result
        emit({
            "event": "extended",
            "req_id": req_id,
            "symbol": symbol,
            "new_end_time_epoch": new_end_epoch,
            "extensions": extensions,
        })
        return

    if name == "shutdown":
        manager_logger.info("shutdown command received")
        raise _ShutdownRequested()

    manager_logger.warning(f"unknown command: {name!r}")


class _ShutdownRequested(Exception):
    """Signals the stdin reader to exit and trigger graceful shutdown."""


def _stdin_reader_loop(client: SharedClient, emit: callable,
                       manager_logger: logging.Logger, max_extensions: int,
                       shutdown_callback: callable):
    """
    Read newline-delimited JSON commands from stdin. On EOF (parent died),
    stop reading but allow active lines to drain — sweeper will finalize
    them naturally and SIGTERM will trigger full shutdown.
    """
    try:
        for raw in sys.stdin:
            line = raw.strip()
            if not line:
                continue
            try:
                cmd = json.loads(line)
            except json.JSONDecodeError as e:
                manager_logger.warning(f"bad JSON on stdin: {e} | line={line!r}")
                continue
            try:
                _handle_command(client, cmd, emit, manager_logger, max_extensions)
            except _ShutdownRequested:
                shutdown_callback()
                return
            except Exception as e:
                manager_logger.exception(f"error handling command {cmd!r}: {e}")
    except Exception as e:
        manager_logger.exception(f"stdin reader crashed: {e}")
    manager_logger.info(
        "stdin EOF — no more launches accepted; active lines will drain."
    )


def main_manager_mode(args):
    """Manager IPC mode: connect, READY, read commands forever."""
    manager_logger = _setup_manager_logger(
        args.client_id, args.manager_log_dir, args.loglevel
    )
    manager_logger.info(
        f"trade_mole_4.2 manager starting: client_id={args.client_id} "
        f"max_slots={args.max_slots} host={args.host} port={args.port}"
    )

    emit = _make_stdout_emitter()

    client = SharedClient(
        client_id=args.client_id,
        max_slots=args.max_slots,
        manager_logger=manager_logger,
        log_dir=args.manager_log_dir,
        emit_fn=emit,
    )

    manager_logger.info(f"Connecting to {args.host}:{args.port}...")
    client.connect(args.host, args.port, clientId=args.client_id)
    api_thread = threading.Thread(
        target=client.run, daemon=True, name=f"ibapi-run-{args.client_id}"
    )
    api_thread.start()

    if not client.connected_event.wait(timeout=args.connect_timeout):
        manager_logger.error(
            f"Failed to connect to IBKR within {args.connect_timeout}s"
        )
        client.disconnect()
        sys.exit(1)

    # Emit READY ack so Orchestrator unblocks
    emit({"event": "ready", "client_id": args.client_id})
    manager_logger.info(f"READY (client_id={args.client_id})")

    client.start_sweeper()

    # Graceful-shutdown plumbing
    shutdown_done = threading.Event()
    def do_shutdown():
        if shutdown_done.is_set():
            return
        shutdown_done.set()
        manager_logger.info("shutting down — flushing all active trackers...")
        client.shutdown_all()
        manager_logger.info("cancelling subscriptions, disconnecting...")
        try:
            client.disconnect()
        except Exception:
            pass
        api_thread.join(timeout=5)

    # SIGTERM / SIGINT -> graceful shutdown
    def _sig_handler(signum, frame):
        manager_logger.info(f"signal {signum} received")
        do_shutdown()

    signal.signal(signal.SIGTERM, _sig_handler)
    signal.signal(signal.SIGINT, _sig_handler)

    # Run the stdin reader on the main thread so SIGTERM lands here.
    try:
        _stdin_reader_loop(
            client=client,
            emit=emit,
            manager_logger=manager_logger,
            max_extensions=args.max_extensions,
            shutdown_callback=do_shutdown,
        )
    finally:
        # After stdin EOF, idle until all slots drain OR shutdown signal.
        while not shutdown_done.is_set():
            with client._state_lock:
                active = client.active_slot_count()
            if active == 0:
                manager_logger.info("all slots drained; exiting.")
                do_shutdown()
                break
            time.sleep(2.0)


# ============================================================================
# Single-symbol mode — backward-compatible CLI (one line, then exit)
# ============================================================================

def main_single_symbol_mode(args):
    """One-shot CLI: allocate one slot, run lifeTime, finalize, exit."""
    symbol = args.single_symbol.upper()

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    manager_logger = logging.getLogger("single")

    if args.log_dir:
        os.makedirs(args.log_dir, exist_ok=True)

    client = SharedClient(
        client_id=args.client_id,
        max_slots=1,
        manager_logger=manager_logger,
        log_dir=args.log_dir or "/tmp",
        emit_fn=None,
        propagate_tracker_logs=True,  # surge alerts visible on stdout (v4 parity)
    )

    manager_logger.info(f"Connecting to {args.host}:{args.port}...")
    client.connect(args.host, args.port, clientId=args.client_id)
    api_thread = threading.Thread(
        target=client.run, daemon=True, name=f"ibapi-run-{args.client_id}"
    )
    api_thread.start()
    if not client.connected_event.wait(timeout=args.connect_timeout):
        manager_logger.error("Failed to connect to IBKR.")
        client.disconnect()
        sys.exit(1)
    client.start_sweeper()

    lifetime_sec = args.lifeTime
    output_dir = args.output
    log_dir = args.log_dir or os.path.dirname(args.output) or "/tmp"

    result = client.allocate_slot(
        symbol=symbol,
        baseline_iti=args.baseline_avg_iti,
        lifetime_sec=lifetime_sec,
        output_dir=output_dir,
        log_dir=log_dir,
    )
    if result is None:
        manager_logger.error(f"failed to allocate slot for {symbol}")
        client.disconnect()
        api_thread.join(timeout=5)
        sys.exit(1)
    slot_idx, end_time_epoch = result
    manager_logger.info(
        f"Allocated {symbol} slot={slot_idx} "
        f"end_time={datetime.fromtimestamp(end_time_epoch).isoformat()}"
    )

    # Block until the sweeper frees the slot OR Ctrl+C
    try:
        while client.active_slot_count() > 0 and not client.disconnected_flag:
            time.sleep(1.0)
            remaining = int(end_time_epoch - time.time())
            if remaining > 0 and remaining % 30 == 0:
                tracker = client._slots[slot_idx]
                if tracker is not None:
                    manager_logger.info(
                        f"[{remaining:>4}s left] events={len(tracker.records)} "
                        f"trades={tracker._cum_trade_count} "
                        f"vol={tracker._cum_volume} vwap={tracker._vwap} "
                        f"halted={tracker._halted}"
                    )
    except KeyboardInterrupt:
        manager_logger.info("Interrupted by user.")

    manager_logger.info("Shutting down single-symbol session...")
    client.shutdown_all()
    try:
        client.disconnect()
    except Exception:
        pass
    api_thread.join(timeout=5)


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="IBKR trade-frequency surge detector — shared-client manager (v4.2)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Shared args
    parser.add_argument("--client-id", type=int, required=True,
                        help="IBKR clientID for this manager's connection")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001,
                        help="IBKR port (7497=paper TWS, 7496=live TWS, "
                             "4002=paper Gateway, 4001=live Gateway)")
    parser.add_argument("--max-slots", type=int, default=10,
                        help="Max concurrent symbols on this client")
    parser.add_argument("--max-extensions", type=int, default=3,
                        help="Max times a symbol's lifeTime can be extended")
    parser.add_argument("--connect-timeout", type=int, default=30,
                        help="Seconds to wait for IBKR nextValidId")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    # Manager mode args
    parser.add_argument("--manager-log-dir",
                        default="/tmp/trade_mole_4_2_manager_logs",
                        help="Directory for manager-level log files")

    # Single-symbol mode args (mutually exclusive use)
    parser.add_argument("--single-symbol", default=None,
                        help="If set, run one-shot for this symbol then exit "
                             "(backward-compat with trade_mole_4.py CLI). "
                             "Requires --baseline-iti, --lifeTime, --output.")
    parser.add_argument("--baseline-iti", dest="baseline_avg_iti",
                        type=float, default=None,
                        help="Historical baseline avg inter-trade interval (seconds). "
                             "Required for --single-symbol mode.")
    parser.add_argument("--lifeTime", type=parse_lifetime, default=None,
                        help="mm:ss collection duration. Required for "
                             "--single-symbol mode.")
    parser.add_argument("--output", default=None,
                        help="Output dir or full CSV path. Required for "
                             "--single-symbol mode.")
    parser.add_argument("--log-dir", default=None,
                        help="Per-symbol log dir (single-symbol mode only).")

    args = parser.parse_args()

    if args.single_symbol is not None:
        # Validate single-symbol args
        missing = [
            name for name, val in
            (("--baseline-iti", args.baseline_avg_iti),
             ("--lifeTime", args.lifeTime),
             ("--output", args.output))
            if val is None
        ]
        if missing:
            parser.error(
                f"--single-symbol requires {', '.join(missing)}"
            )
        if args.baseline_avg_iti <= 0:
            parser.error("--baseline-iti must be > 0")
        main_single_symbol_mode(args)
        return

    main_manager_mode(args)


if __name__ == "__main__":
    main()
