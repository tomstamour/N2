#!/usr/bin/env python3
"""
IBKR Future-Window Trade-Frequency Baseline  (TICK-based, parallel)
===================================================================
Samples a FORWARD window on each of the last N past trading days at true
per-tick resolution. For each day, one `reqHistoricalTicks` call pulls up
to --ticks-quantity TRADES ticks ending at (anchor + windowMin), on its
own IBKR connection (clientID = base + i), and all N calls run in
PARALLEL. Total wall time ≈ 500 ms – 2 s for 10 days.

Complements the bar-based sibling `futureWindows_trade_frequency_baseline.py`
by measuring true inter-trade intervals from actual trade timestamps rather
than bar-count approximations.

Caveats
-------
* IBKR caps `reqHistoricalTicks` at 1000 ticks per request. On busy tapes
  you may hit that cap — the returned batch then covers only the END of
  the window. The per-day `partial_coverage` column flags this.
* `HistoricalTickLast.time` is integer unix seconds. On fast tapes many
  successive ticks share a timestamp so `iti_min_sec` can legitimately
  be 0 while `iti_mean_sec` stays meaningful.
* This script opens --days (default 10) concurrent IBKR connections with
  consecutive clientIDs. Pick a base clientID that leaves
  [base, base+days-1] free.

Usage
-----
    # Default: 30-min window starting 09:30 ET across last 10 past days,
    # using clientIDs 40..49 in parallel.
    python futureWindows_tick_trade_frequency_baseline.py \\
        --symbol MARA --clientID 40

    # Pre-market 60-min window, ETH, custom tick cap
    python futureWindows_tick_trade_frequency_baseline.py \\
        --symbol MARA --clientID 50 \\
        --startTime 07:00:00 --windowMin 60 --useRth 0 \\
        --ticks-quantity 1000 --port 4001

    # Trigger mode: 30 min AFTER "now" ET on each past day, ETH forced on
    python futureWindows_tick_trade_frequency_baseline.py \\
        --symbol MARA --clientID 60 --on-trigger true \\
        --log ./logs/MARA_future_tick.log
"""

import argparse
import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


# ── Constants ────────────────────────────────────────────────────────────────
TIMEZONE        = "US/Eastern"
CONNECT_TIMEOUT = 10   # seconds to wait for nextValidId (per connection)
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


def _parse_bool(s: str) -> bool:
    if s.lower() in ("true", "1", "yes"):
        return True
    if s.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got {s!r}")


def _hms_to_seconds(hms: str) -> int:
    h, m, s = map(int, hms.split(":"))
    return h * 3600 + m * 60 + s


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


def _resolve_output_path(output_arg: str, symbol: str, prefix: str) -> str:
    """
    If `output_arg` is a directory, auto-generate
    `SYMBOL_{prefix}_YYYY-MM-DD_HH-MM.csv` inside it. Otherwise treat
    `output_arg` as the literal file path.
    """
    is_dir = (
        output_arg.endswith(os.sep)
        or output_arg.endswith("/")
        or os.path.isdir(output_arg)
    )
    if is_dir:
        os.makedirs(output_arg, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        return os.path.join(output_arg, f"{symbol}_{prefix}_{ts}.csv")
    return output_arg


def past_trading_days(n: int) -> list:
    """Return the last N weekdays (Mon-Fri) in YYYYMMDD, newest first."""
    days, d = [], datetime.now().date() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days


def make_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol          = symbol
    c.secType         = "STK"
    c.exchange        = "SMART"
    c.primaryExchange = "NASDAQ"
    c.currency        = "USD"
    return c


def _day_anchor_unix(day_yyyymmdd: str, hms: str) -> int:
    """Unix seconds for a given past-day YYYYMMDD at HH:MM:SS in ET."""
    dt = datetime.strptime(f"{day_yyyymmdd} {hms}", "%Y%m%d %H:%M:%S")
    dt = dt.replace(tzinfo=ZoneInfo(TIMEZONE))
    return int(dt.timestamp())


# ── IBKR App (one per connection) ────────────────────────────────────────────

class FutureTickApp(EWrapper, EClient):
    """
    Per-connection tick fetcher. One instance = one IBKR clientID = one
    reqHistoricalTicks call for one past trading day. `fire_ticks_request`
    is non-blocking so the caller can fan-out N instances in parallel.
    """

    def __init__(self, client_id: int):
        EClient.__init__(self, wrapper=self)
        self.client_id        = client_id
        self.connected_event  = threading.Event()
        self._ticks: list     = []
        self._error: Optional[str] = None
        self._done_event      = threading.Event()
        self._req_id: Optional[int] = None

    def nextValidId(self, orderId):
        self.connected_event.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2107, 2158, 2100, 2108, 2119, 2176):
            return  # informational only
        logging.warning(
            f"[IBKR {errorCode}] clientID={self.client_id} reqId={reqId}: {errorString}"
        )
        if self._req_id is not None and reqId == self._req_id:
            self._error = f"{errorCode}: {errorString}"
            self._done_event.set()

    def historicalTicksLast(self, reqId, ticks, done):
        self._ticks.extend(ticks)
        if done:
            self._done_event.set()

    def fire_ticks_request(
        self,
        contract:   Contract,
        end_dt_str: str,
        n_ticks:    int,
        use_rth:    int,
        req_id:     int,
    ) -> None:
        """Kick off reqHistoricalTicks and return immediately (non-blocking)."""
        self._ticks = []
        self._error = None
        self._done_event.clear()
        self._req_id = req_id

        self.reqHistoricalTicks(
            reqId          = req_id,
            contract       = contract,
            startDateTime  = "",
            endDateTime    = end_dt_str,
            numberOfTicks  = n_ticks,
            whatToShow     = "TRADES",
            useRth         = use_rth,
            ignoreSize     = False,
            miscOptions    = [],
        )

    def wait_ticks(self, timeout: float) -> list:
        """Block until historicalTicksLast(done=True) or timeout."""
        fired = self._done_event.wait(timeout=timeout)
        if not fired:
            logging.warning(
                f"clientID={self.client_id} tick fetch TIMEOUT after {timeout}s"
            )
        elif self._error:
            logging.warning(
                f"clientID={self.client_id} tick fetch ERROR: {self._error}"
            )
        return self._ticks


# ── Per-day stats ────────────────────────────────────────────────────────────

def compute_future_tick_stats(
    day:                 str,
    raw_ticks:           list,
    window_label:        str,
    window_start_unix:   int,
    window_sec:          int,
    n_ticks_requested:   int,
) -> dict:
    """
    Filter ticks to [window_start, window_end) and compute per-day stats
    with the same column shape used by pastWindows_/futureWindows_ so the
    average row builder works unchanged.
    """
    # IBKR walks backward from endDateTime; drop any tick before our anchor.
    ticks = [t for t in raw_ticks if t.time >= window_start_unix]

    base = {
        "date":     day,
        "window":   window_label,
        "n_trades": len(ticks),
    }

    numeric_keys = [
        "iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
        "trades_per_sec", "trades_per_min",
        "span_sec", "total_volume", "avg_price",
        "first_tick_ts", "last_tick_ts",
    ]

    if not ticks:
        base.update({k: None for k in numeric_keys})
        base["partial_coverage"] = ""
        return base

    tz = ZoneInfo(TIMEZONE)
    first_t = ticks[0].time
    last_t  = ticks[-1].time
    base["first_tick_ts"] = datetime.fromtimestamp(first_t, tz).strftime("%H:%M:%S")
    base["last_tick_ts"]  = datetime.fromtimestamp(last_t,  tz).strftime("%H:%M:%S")

    # partial_coverage: hit the 1000-tick cap AND earliest tick is after window start
    # → there are likely earlier in-window ticks we didn't receive.
    base["partial_coverage"] = (
        len(ticks) >= n_ticks_requested and first_t > window_start_unix
    )

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
        # trades/min still meaningful vs requested window_sec when we have ≥1 tick
        if window_sec > 0:
            base["trades_per_sec"] = len(ticks) / window_sec
            base["trades_per_min"] = (len(ticks) / window_sec) * 60
        return base

    itis = [ticks[i].time - ticks[i - 1].time for i in range(1, len(ticks))]
    base["iti_mean_sec"]   = span_sec / (len(ticks) - 1)
    base["iti_median_sec"] = _median(itis)
    base["iti_min_sec"]    = min(itis)
    base["iti_max_sec"]    = max(itis)
    base["trades_per_sec"] = len(ticks) / span_sec
    base["trades_per_min"] = (len(ticks) / span_sec) * 60
    return base


def build_average_row(rows: list) -> dict:
    """Column-wise numeric mean across day rows that had at least 1 trade."""
    valid = [r for r in rows if r.get("n_trades", 0) > 0]
    if not valid:
        return {"date": "AVG (no data)", "n_trades": 0}

    avg = {
        "date":   f"AVG ({len(valid)} days)",
        "window": valid[0].get("window", ""),
    }
    numeric_keys = [
        k for k, v in valid[0].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
           and k not in ("date", "window")
    ]
    for k in numeric_keys:
        vals = [r[k] for r in valid if r.get(k) is not None]
        avg[k] = sum(vals) / len(vals) if vals else None
    return avg


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Parallel tick-based IBKR future-window trade-frequency baseline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",    required=True, help="Ticker e.g. MARA")
    parser.add_argument("--clientID",  type=int, required=True,
                        help="BASE clientID. Script uses [clientID, clientID+1, …, "
                             "clientID+days-1] — those IDs must all be free.")
    parser.add_argument("--days",      type=int, default=10,
                        help="Past trading days to sample (= number of parallel "
                             "connections opened)")
    parser.add_argument("--startTime", default="09:30:00",
                        help="Anchor HH:MM:SS Eastern — window is "
                             "[startTime, startTime + windowMin)")
    parser.add_argument("--windowMin", type=int, default=30,
                        help="Window length in minutes (applies to both non-trigger "
                             "and --on-trigger modes)")
    parser.add_argument("--useRth",    type=int, default=1, choices=[0, 1],
                        help="1=RTH only  0=include extended hours (forced to 0 "
                             "under --on-trigger)")
    parser.add_argument("--on-trigger", dest="on_trigger",
                        type=_parse_bool, default=False,
                        metavar="<true|false>",
                        help="If true: override --startTime/--useRth. Anchor becomes "
                             "'now' ET and window becomes [now, now + windowMin). "
                             "useRth forced to 0, applied per past trading day.")
    parser.add_argument("--ticks-quantity", dest="ticks_quantity",
                        type=int, default=MAX_TICKS,
                        help=f"Per-day tick cap (hard-capped at {MAX_TICKS} per "
                             f"IBKR per-request limit)")
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

    trigger_hms = None
    if args.on_trigger:
        now_et = datetime.now(ZoneInfo(TIMEZONE))
        trigger_hms    = now_et.strftime("%H:%M:%S")
        args.startTime = trigger_hms
        args.useRth    = 0

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

    symbol = args.symbol.upper()
    days   = past_trading_days(args.days)

    start_sec  = _hms_to_seconds(args.startTime)
    window_sec = args.windowMin * 60
    end_sec    = start_sec + window_sec
    end_hms    = _seconds_to_hms(end_sec)
    window_label = f"{args.startTime}-{end_hms}"

    if trigger_hms is not None:
        logging.info(
            f"--on-trigger=true: {args.windowMin}-min window AFTER "
            f"{trigger_hms} ET, useRth forced to 0"
        )

    logging.info(
        f"Symbol={symbol}  days={args.days}  window={window_label}  "
        f"useRth={args.useRth}  ticks/day={n_ticks}"
    )
    logging.info(
        f"clientIDs={args.clientID}..{args.clientID + args.days - 1}  "
        f"(all must be free)"
    )
    logging.info(f"Dates to sample: {days}")

    # ── 1. Create + connect all N apps ────────────────────────────────────
    apps:        list = []
    api_threads: list = []
    for i, day in enumerate(days):
        cid = args.clientID + i
        app = FutureTickApp(client_id=cid)
        app.connect(args.host, args.port, clientId=cid)
        t = threading.Thread(
            target=app.run, daemon=True, name=f"ibapi-run-{cid}"
        )
        t.start()
        apps.append(app)
        api_threads.append(t)

    # ── 2. Wait for nextValidId on each ───────────────────────────────────
    for app in apps:
        if not app.connected_event.wait(timeout=CONNECT_TIMEOUT):
            logging.error(
                f"clientID={app.client_id} failed to connect within "
                f"{CONNECT_TIMEOUT}s — its day will be skipped"
            )

    # ── 3. Fire all reqHistoricalTicks in parallel ────────────────────────
    t_wall   = time.perf_counter()
    contract = make_contract(symbol)
    for i, (app, day) in enumerate(zip(apps, days)):
        if not app.connected_event.is_set():
            continue
        end_dt_str = f"{day} {end_hms} {TIMEZONE}"
        app.fire_ticks_request(
            contract   = contract,
            end_dt_str = end_dt_str,
            n_ticks    = n_ticks,
            use_rth    = args.useRth,
            req_id     = REQ_ID_BASE + i,
        )

    # ── 4. Wait for all done events ───────────────────────────────────────
    for app in apps:
        if app.connected_event.is_set():
            app.wait_ticks(timeout=FETCH_TIMEOUT)

    # ── 5. Disconnect all ─────────────────────────────────────────────────
    for app, t in zip(apps, api_threads):
        app.disconnect()
        t.join(timeout=3)
    total_ms = (time.perf_counter() - t_wall) * 1000

    # ── Compute per-day rows ──────────────────────────────────────────────
    rows = []
    for app, day in zip(apps, days):
        window_start_unix = _day_anchor_unix(day, args.startTime)
        rows.append(compute_future_tick_stats(
            day               = day,
            raw_ticks         = app._ticks,
            window_label      = window_label,
            window_start_unix = window_start_unix,
            window_sec        = window_sec,
            n_ticks_requested = n_ticks,
        ))

    avg_row  = build_average_row(rows)
    all_rows = rows + [avg_row]

    df = pd.DataFrame(all_rows)
    col_order = [
        "date", "window", "n_trades", "span_sec",
        "iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
        "trades_per_sec", "trades_per_min",
        "total_volume", "avg_price",
        "first_tick_ts", "last_tick_ts", "partial_coverage",
    ]
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]

    # ── Display ───────────────────────────────────────────────────────────
    bar_line = "=" * 82
    print(f"\n{bar_line}")
    print(f"  Trade-frequency baseline (FUTURE window, tick-resolution)  "
          f"|  {symbol}  |  {window_label}")
    print(bar_line)
    print(df.to_string(index=False))
    print(f"\n  Total wall time: {total_ms:.0f} ms  "
          f"({args.days} parallel reqHistoricalTicks, "
          f"{n_ticks} ticks/day requested)")

    iti  = avg_row.get("iti_mean_sec")
    rate = avg_row.get("trades_per_min")
    if iti is not None and rate is not None:
        print(f"\n{'─'*82}")
        print(f"  Post-trigger calibration reference (tick-resolution, "
              f"{len([r for r in rows if r.get('n_trades', 0) > 0])}-day avg):")
        print(f"    POST_TRIGGER_ITI_MEAN_SEC = {iti:.1f}   "
              f"# post-trigger avg seconds between trades")
        print(f"    # Expected post-trigger rate: {rate:.1f} trades/min")
        print(f"    # (integer-second tick resolution: iti_min may be 0 on fast tapes)")
        print(f"{'─'*82}\n")

    if args.output:
        out_path = _resolve_output_path(args.output, symbol, prefix="future_tick")
        df.to_csv(out_path, index=False)
        logging.info(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()
