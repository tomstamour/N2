#!/usr/bin/env python3
"""
IBKR Pre-Trigger Trade-Frequency Baseline  (tick-based)
========================================================
Issues ONE `reqHistoricalTicks` call to fetch the most recent N trades ending
at a chosen upper bound (either --startTime today, or "now" with --on-trigger),
then derives trade-frequency stats from exact per-tick timestamps.

Total wall time is a single round-trip to IBKR (~300-700 ms) for up to 1000
ticks (IBKR's per-request cap).

Note: IBKR's reqHistoricalTicks with empty startDateTime is session-bounded
— it does not cross overnight gaps. If your --startTime lands near a session
open (e.g. 04:00:05 pre-market) you may get back fewer ticks than requested.
Pass --cross-session true to allow ONE additional fetch into the previous
session when the first request under-fills.

Usage
-----
    # Trigger-time baseline — last N ticks ending "now"
    python pre_trade_frequency_baseline.py --symbol MARA --clientID 21 \\
        --on-trigger true

    # Explicit upper bound (ticks before 09:45 ET today)
    python pre_trade_frequency_baseline.py --symbol MARA --clientID 22 \\
        --startTime 09:45:00 --ticks-quantity 500

    # Pre-market open: fall back to previous session if under-filled
    python pre_trade_frequency_baseline.py --symbol MARA --clientID 22 \\
        --startTime 04:00:05 --ticks-quantity 500 --cross-session true

    # Save summary to CSV
    python pre_trade_frequency_baseline.py --symbol MARA --clientID 23 \\
        --on-trigger true --output ./baselines/MARA_pre.csv
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


# ── Constants ────────────────────────────────────────────────────────────────
TIMEZONE        = "US/Eastern"
CONNECT_TIMEOUT = 10   # seconds to wait for nextValidId
FETCH_TIMEOUT   = 5    # seconds to wait for historicalTicksLast done=True
REQ_ID_BASE     = 5000
MAX_TICKS       = 1000  # IBKR per-request hard cap for reqHistoricalTicks
# ─────────────────────────────────────────────────────────────────────────────


class _Tee:
    """Duplicate writes to multiple streams (e.g. stdout + a log file)."""

    def __init__(self, *streams):
        self.streams = streams

    def write(self, data):
        for s in self.streams:
            s.write(data)

    def flush(self):
        for s in self.streams:
            s.flush()


def _parse_date(s: str) -> str:
    """Accept YYYY-MM-DD or YYYYMMDD; return YYYYMMDD."""
    s = s.replace("-", "")
    if len(s) != 8 or not s.isdigit():
        raise argparse.ArgumentTypeError(
            f"expected YYYY-MM-DD or YYYYMMDD, got {s!r}"
        )
    return s


def _parse_bool(s: str) -> bool:
    if s.lower() in ("true", "1", "yes"):
        return True
    if s.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {s!r}")


def _seconds_to_hms(sec: int) -> str:
    h, rem = divmod(max(sec, 0), 3600)
    m, s   = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _median(lst: list) -> Optional[float]:
    if not lst:
        return None
    s = sorted(lst)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def _resolve_output_path(output_arg: str, symbol: str) -> str:
    """
    If `output_arg` is a directory (exists as dir or ends with a separator),
    auto-generate `SYMBOL_pre_YYYY-MM-DD_HH-MM.csv` inside it (creating the
    directory if needed). Otherwise treat `output_arg` as the full literal
    file path.
    """
    is_dir = (
        output_arg.endswith(os.sep)
        or output_arg.endswith("/")
        or os.path.isdir(output_arg)
    )
    if is_dir:
        os.makedirs(output_arg, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        return os.path.join(output_arg, f"{symbol}_pre_{ts}.csv")
    return output_arg


def make_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol          = symbol
    c.secType         = "STK"
    c.exchange        = "SMART"
    c.primaryExchange = "NASDAQ"
    c.currency        = "USD"
    return c


# ── IBKR App ─────────────────────────────────────────────────────────────────

class PreBaselineApp(EWrapper, EClient):
    """
    Single-request tick fetcher.
    One reqHistoricalTicks call returns up to MAX_TICKS ticks ending at the
    requested upper bound; historicalTicksLast() collects them and fires the
    done-event when the batch is complete.
    """

    def __init__(self):
        EClient.__init__(self, wrapper=self)
        self.connected_event = threading.Event()
        self._ticks: list = []
        self._error: Optional[str] = None
        self._done_event = threading.Event()

    def nextValidId(self, orderId):
        self.connected_event.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2107, 2158, 2100, 2108, 2119, 2176):
            return  # informational only
        logging.warning(f"[IBKR {errorCode}] reqId={reqId}: {errorString}")
        if reqId == REQ_ID_BASE:
            self._error = f"{errorCode}: {errorString}"
            self._done_event.set()

    def historicalTicksLast(self, reqId, ticks, done):
        self._ticks.extend(ticks)
        if done:
            self._done_event.set()

    def fetch_ticks(
        self,
        contract:   Contract,
        end_dt_str: str,
        n_ticks:    int,
    ) -> list:
        self._ticks = []
        self._error = None
        self._done_event.clear()

        t0 = time.perf_counter()
        self.reqHistoricalTicks(
            reqId          = REQ_ID_BASE,
            contract       = contract,
            startDateTime  = "",
            endDateTime    = end_dt_str,
            numberOfTicks  = n_ticks,
            whatToShow     = "TRADES",
            useRth         = 0,
            ignoreSize     = False,
            miscOptions    = [],
        )
        fired = self._done_event.wait(timeout=FETCH_TIMEOUT)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if not fired:
            logging.warning(f"Tick fetch TIMEOUT after {FETCH_TIMEOUT}s")
        elif self._error:
            logging.warning(f"Tick fetch ERROR: {self._error}")

        logging.info(
            f"Tick fetch complete: {len(self._ticks)} ticks in {elapsed_ms:.0f} ms"
        )
        return self._ticks


# ── Frequency computation ─────────────────────────────────────────────────────

def compute_pre_stats(ticks: list, symbol: str, end_dt_str: str) -> dict:
    """
    Compute trade-frequency stats from a single batch of historical ticks.

    ITI is computed from exact per-tick timestamps (not bar approximations):
        iti_mean_sec   = mean of successive .time diffs
        iti_median/min/max_sec = order stats of the same diffs
    """
    base = {
        "symbol":     symbol,
        "window_end": end_dt_str,
        "n_ticks":    len(ticks),
    }

    numeric_keys = [
        "first_tick_ts", "last_tick_ts", "span_sec",
        "iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
        "trades_per_sec", "trades_per_min",
        "total_volume", "avg_price",
    ]

    if not ticks:
        base.update({k: None for k in numeric_keys})
        return base

    tz = ZoneInfo(TIMEZONE)
    first_t = ticks[0].time
    last_t  = ticks[-1].time
    base["first_tick_ts"] = datetime.fromtimestamp(first_t, tz).strftime("%H:%M:%S")
    base["last_tick_ts"]  = datetime.fromtimestamp(last_t,  tz).strftime("%H:%M:%S")

    span_sec = last_t - first_t
    base["span_sec"] = span_sec

    total_volume = sum(float(getattr(t, "size", 0) or 0) for t in ticks)
    if total_volume > 0:
        avg_price = sum(float(t.price) * float(t.size) for t in ticks) / total_volume
    else:
        prices = [float(t.price) for t in ticks]
        avg_price = sum(prices) / len(prices) if prices else None
    base["total_volume"] = total_volume
    base["avg_price"]    = avg_price

    if len(ticks) < 2 or span_sec <= 0:
        for k in ("iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
                  "trades_per_sec", "trades_per_min"):
            base[k] = None
        return base

    itis = [ticks[i].time - ticks[i - 1].time for i in range(1, len(ticks))]
    base["iti_mean_sec"]   = sum(itis) / len(itis)
    base["iti_median_sec"] = _median(itis)
    base["iti_min_sec"]    = min(itis)
    base["iti_max_sec"]    = max(itis)
    base["trades_per_sec"] = len(ticks) / span_sec
    base["trades_per_min"] = (len(ticks) / span_sec) * 60
    return base


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fast single-request IBKR pre-trigger trade-frequency baseline (tick-based)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",    required=True, help="Ticker e.g. MARA")
    parser.add_argument("--clientID",  type=int, required=True)
    parser.add_argument("--startTime", default="09:30:00",
                        help="Upper bound HH:MM:SS Eastern — ticks fetched are "
                             "those BEFORE this time today")
    parser.add_argument("--date", default=None, type=_parse_date,
                        metavar="YYYY-MM-DD",
                        help="Date for the upper bound (default: today ET). "
                             "Ignored when --on-trigger true.")
    parser.add_argument("--on-trigger", dest="on_trigger",
                        type=_parse_bool, default=False,
                        metavar="<true|false>",
                        help="If true: override --startTime; upper bound becomes "
                             "current ET time ('now').")
    parser.add_argument("--ticks-quantity", dest="ticks_quantity",
                        type=int, default=1000,
                        help=f"Number of ticks to fetch (hard-capped at {MAX_TICKS} "
                             f"per IBKR per-request limit)")
    parser.add_argument("--cross-session", dest="cross_session",
                        type=_parse_bool, default=False,
                        metavar="<true|false>",
                        help="If true and the first fetch returns fewer ticks than "
                             "--ticks-quantity, issue ONE additional reqHistoricalTicks "
                             "call whose endDateTime is 1 second before the oldest tick, "
                             "landing in the previous session. Max 2 requests total.")
    parser.add_argument("--host",      default="127.0.0.1")
    parser.add_argument("--port",      type=int, default=7497,
                        help="7497=paper TWS  7496=live TWS  "
                             "4002=paper GW  4001=live GW")
    parser.add_argument("--output",    default=None, help="Optional CSV output path")
    parser.add_argument("--log",       default=None, metavar="<path>",
                        help="Tee all stdout+stderr to this file")
    parser.add_argument("--loglevel",  default="INFO",
                        choices=["DEBUG", "INFO", "WARNING"])
    args = parser.parse_args()

    if args.log:
        _log_fh = open(args.log, "w", buffering=1)
        sys.stdout = _Tee(sys.stdout, _log_fh)
        sys.stderr = _Tee(sys.stderr, _log_fh)

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    symbol = args.symbol.upper()

    # ── Resolve upper bound ───────────────────────────────────────────────
    now_et = datetime.now(ZoneInfo(TIMEZONE))
    if args.on_trigger:
        end_hms = now_et.strftime("%H:%M:%S")
        logging.info(f"--on-trigger=true: upper bound = NOW ({end_hms} ET)")
    else:
        end_hms = args.startTime

    if args.on_trigger or args.date is None:
        target_yyyymmdd = now_et.strftime("%Y%m%d")
        if args.date is not None:
            logging.warning("--date is ignored when --on-trigger true")
    else:
        target_yyyymmdd = args.date
    end_dt_str = f"{target_yyyymmdd} {end_hms} {TIMEZONE}"

    # Clamp ticks quantity
    if args.ticks_quantity > MAX_TICKS:
        logging.warning(
            f"--ticks-quantity={args.ticks_quantity} exceeds IBKR cap of "
            f"{MAX_TICKS}; clamping to {MAX_TICKS}"
        )
    n_ticks = min(args.ticks_quantity, MAX_TICKS)
    if n_ticks < 1:
        logging.error(f"--ticks-quantity must be >= 1, got {args.ticks_quantity}")
        return

    logging.info(
        f"Symbol={symbol}  ticks_quantity={n_ticks}  upper_bound={end_dt_str}"
    )

    # ── Connect ────────────────────────────────────────────────────────────
    app = PreBaselineApp()
    app.connect(args.host, args.port, clientId=args.clientID)

    api_thread = threading.Thread(target=app.run, daemon=True, name="ibapi-run")
    api_thread.start()

    try:
        if not app.connected_event.wait(timeout=CONNECT_TIMEOUT):
            logging.error("Failed to connect to IBKR within 10s. "
                          "Is TWS or IB Gateway running with API enabled?")
            return

        # ── Tick fetch (up to 2 requests if --cross-session true) ─────────
        t_wall = time.perf_counter()
        contract = make_contract(symbol)
        ticks = app.fetch_ticks(
            contract   = contract,
            end_dt_str = end_dt_str,
            n_ticks    = n_ticks,
        )
        n_requests = 1

        if args.cross_session and len(ticks) < n_ticks and ticks:
            # Oldest tick sits at (or near) current session's open. Step 1 second
            # before it to land inside the previous session.
            prev_end_unix = ticks[0].time - 1
            prev_end_str  = datetime.fromtimestamp(
                prev_end_unix, ZoneInfo(TIMEZONE)
            ).strftime("%Y%m%d %H:%M:%S") + f" {TIMEZONE}"
            logging.info(
                f"--cross-session=true: got {len(ticks)}/{n_ticks} ticks; "
                f"issuing follow-up fetch with endDateTime={prev_end_str}"
            )
            prev_ticks = app.fetch_ticks(
                contract   = contract,
                end_dt_str = prev_end_str,
                n_ticks    = n_ticks,
            )
            n_requests = 2
            if not prev_ticks:
                logging.warning(
                    "Cross-session follow-up returned 0 ticks — "
                    "symbol may be illiquid in the previous session."
                )
            else:
                merged = list(ticks) + list(prev_ticks)
                seen = set()
                deduped = []
                for t in merged:
                    key = (t.time, float(t.price), float(getattr(t, "size", 0) or 0),
                           getattr(t, "exchange", ""))
                    if key in seen:
                        continue
                    seen.add(key)
                    deduped.append(t)
                deduped.sort(key=lambda t: t.time)
                ticks = deduped[-n_ticks:]

        total_ms = (time.perf_counter() - t_wall) * 1000

    finally:
        app.disconnect()
        api_thread.join(timeout=3)

    # ── Compute stats ──────────────────────────────────────────────────────
    row = compute_pre_stats(ticks, symbol, end_dt_str)
    col_order = [
        "symbol", "window_end", "n_ticks",
        "first_tick_ts", "last_tick_ts", "span_sec",
        "iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
        "trades_per_sec", "trades_per_min",
        "total_volume", "avg_price",
    ]
    df = pd.DataFrame([row])
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]

    # ── Display ────────────────────────────────────────────────────────────
    bar_line = "=" * 74
    print(f"\n{bar_line}")
    print(f"  Pre-trigger trade-frequency baseline  |  {symbol}  |  ending @ {end_dt_str}")
    print(bar_line)
    print(df.to_string(index=False))
    req_label = (
        "single reqHistoricalTicks request"
        if n_requests == 1
        else f"{n_requests} reqHistoricalTicks requests (cross-session)"
    )
    print(f"\n  Total wall time: {total_ms:.0f} ms  "
          f"({req_label}, {n_ticks} ticks requested)")

    iti  = row.get("iti_mean_sec")
    rate = row.get("trades_per_min")
    if iti is not None and rate is not None:
        print(f"\n{'─'*74}")
        print(f"  Plug these into ibkr_trade_surge.py:")
        print(f"    SURGE_PRIOR_ITI_MIN = {iti:.1f}   "
              f"# baseline avg seconds between trades")
        print(f"    # A surge fires when live ITI < 2s AND baseline was > {iti:.1f}s")
        print(f"    # Baseline rate: {rate:.1f} trades/min "
              f"→ surge threshold: {rate*5:.0f}+ trades/min")
        print(f"{'─'*74}\n")
    elif row.get("n_ticks", 0) == 0:
        logging.warning(
            "No ticks returned — likely market closed or no trades before the "
            "requested upper bound. Skipping surge-threshold hint."
        )

    if args.output:
        out_path = _resolve_output_path(args.output, symbol)
        df.to_csv(out_path, index=False)
        logging.info(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
