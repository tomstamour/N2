#!/usr/bin/env python3
"""
IBKR Fast Trade-Frequency Baseline  (bar-based)
================================================
Issues ONE `reqHistoricalData` call covering the last N trading days and
derives per-day trade-frequency stats from the returned TRADES bars
(each bar carries a `.barCount` field = number of trades in the bar).

Total wall time is a single round-trip to IBKR (~300-700 ms), regardless
of how many days we sample — no same-contract same-tickType pacing.

Usage
-----
    python trade_frequency_baseline.py --symbol MARA --clientID 10
    python trade_frequency_baseline.py --symbol MARA --clientID 10 \\
        --startTime "07:00:00" --endTime "09:30:00" --useRth 0

    # Trigger-time baseline — last `--windowMin` minutes (default 30)
    # before "now", per past trading day (RTH+ETH indifferent)
    python trade_frequency_baseline.py --symbol MARA --clientID 10 --on-trigger true
"""

import argparse
import logging
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


# ── Constants ────────────────────────────────────────────────────────────────
TIMEZONE        = "US/Eastern"
CONNECT_TIMEOUT = 10   # seconds to wait for nextValidId
FETCH_TIMEOUT   = 5    # seconds to wait for historicalDataEnd
REQ_ID_BASE     = 5000

# barSizeSetting → seconds per bar (subset of valid IBKR bar sizes)
_BAR_SIZE_SEC = {
    "1 secs":   1,  "5 secs":   5,  "10 secs":  10,  "15 secs":  15,
    "30 secs": 30,
    "1 min":   60,  "2 mins": 120,  "3 mins": 180,  "5 mins": 300,
    "10 mins": 600, "15 mins": 900, "20 mins": 1200, "30 mins": 1800,
    "1 hour": 3600,
}
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


def _parse_bar_date(s: str):
    """
    Split IBKR bar.date (formatDate=1) into (yyyymmdd, tod_sec, hhmmss_str).
    Handles 'YYYYMMDD', 'YYYYMMDD  HH:MM:SS', 'YYYYMMDD HH:MM:SS US/Eastern'.
    """
    parts = s.strip().split()
    date  = parts[0]
    if len(parts) >= 2 and ":" in parts[1]:
        h, m, sec = map(int, parts[1].split(":"))
        return date, h * 3600 + m * 60 + sec, parts[1]
    return date, None, None


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


# ── IBKR App ─────────────────────────────────────────────────────────────────

class FastBaselineApp(EWrapper, EClient):
    """
    Single-request bar fetcher.
    One reqHistoricalData call covers all N days; historicalData() appends
    each bar, historicalDataEnd() fires the done-event.
    """

    def __init__(self):
        EClient.__init__(self, wrapper=self)
        self.connected_event = threading.Event()
        self._bars: list = []
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

    def historicalData(self, reqId, bar):
        self._bars.append(bar)

    def historicalDataEnd(self, reqId, start, end):
        self._done_event.set()

    def fetch_bars(
        self,
        contract:     Contract,
        end_dt_str:   str,
        duration_str: str,
        bar_size:     str,
        use_rth:      int,
    ) -> list:
        self._bars = []
        self._error = None
        self._done_event.clear()

        t0 = time.perf_counter()
        self.reqHistoricalData(
            reqId          = REQ_ID_BASE,
            contract       = contract,
            endDateTime    = end_dt_str,
            durationStr    = duration_str,
            barSizeSetting = bar_size,
            whatToShow     = "TRADES",
            useRTH         = use_rth,
            formatDate     = 1,
            keepUpToDate   = False,
            chartOptions   = [],
        )
        fired = self._done_event.wait(timeout=FETCH_TIMEOUT)
        elapsed_ms = (time.perf_counter() - t0) * 1000

        if not fired:
            logging.warning(f"Bar fetch TIMEOUT after {FETCH_TIMEOUT}s")
        elif self._error:
            logging.warning(f"Bar fetch ERROR: {self._error}")

        logging.info(
            f"Bar fetch complete: {len(self._bars)} bars in {elapsed_ms:.0f} ms"
        )
        return self._bars


# ── Frequency computation ─────────────────────────────────────────────────────

def compute_freq(
    day:          str,
    bars:         list,
    window_label: str,
    window_sec:   int,
    bar_sec:      int,
) -> dict:
    """
    Compute trade-frequency stats for one day from its in-window bars.

    iti_mean_sec      = window_sec / total_trades       (true average over whole window)
    iti_{median,min,max}_sec come from per-bar rates on NON-empty bars
        (= bar_sec / bar.barCount) — represents intra-window variability.
    """
    n_trades = sum(int(getattr(b, "barCount", 0) or 0) for b in bars)
    vol_tot  = sum(float(getattr(b, "volume",   0) or 0) for b in bars)

    base = {
        "date":     day,
        "window":   window_label,
        "n_trades": n_trades,
    }

    if n_trades == 0 or window_sec <= 0:
        base.update({k: None for k in [
            "iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
            "trades_per_sec", "trades_per_min",
            "span_sec", "total_volume", "avg_price",
            "first_bar_ts", "last_bar_ts",
        ]})
        return base

    # Per-bar ITI across bars with ≥1 trade
    per_bar_itis = [bar_sec / int(b.barCount)
                    for b in bars
                    if int(getattr(b, "barCount", 0) or 0) > 0]

    # Volume-weighted average price (falls back to mean close if zero volume)
    if vol_tot > 0:
        avg_price = sum(float(b.average) * float(b.volume) for b in bars) / vol_tot
    else:
        closes = [float(b.close) for b in bars]
        avg_price = sum(closes) / len(closes) if closes else None

    base.update({
        "iti_mean_sec":   window_sec / n_trades,
        "iti_median_sec": _median(per_bar_itis),
        "iti_min_sec":    min(per_bar_itis) if per_bar_itis else None,
        "iti_max_sec":    max(per_bar_itis) if per_bar_itis else None,
        "trades_per_sec": n_trades / window_sec,
        "trades_per_min": (n_trades / window_sec) * 60,
        "span_sec":       window_sec,
        "total_volume":   vol_tot,
        "avg_price":      avg_price,
        "first_bar_ts":   bars[0].date,
        "last_bar_ts":    bars[-1].date,
    })
    return base


def _median(lst: list) -> Optional[float]:
    if not lst:
        return None
    s = sorted(lst)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


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
        if isinstance(v, (int, float)) and k not in ("date", "window")
    ]
    for k in numeric_keys:
        vals = [r[k] for r in valid if r.get(k) is not None]
        avg[k] = sum(vals) / len(vals) if vals else None
    return avg


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fast single-request IBKR trade-frequency baseline (bar-based)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",    required=True, help="Ticker e.g. MARA")
    parser.add_argument("--clientID",  type=int, required=True)
    parser.add_argument("--days",      type=int, default=10,
                        help="Past trading days to sample")
    parser.add_argument("--startTime", default="09:30:00",
                        help="Window start HH:MM:SS Eastern")
    parser.add_argument("--endTime",   default="10:00:00",
                        help="Window end HH:MM:SS Eastern")
    parser.add_argument("--useRth",    type=int, default=1, choices=[0, 1],
                        help="1=RTH only  0=include extended hours")
    parser.add_argument("--on-trigger", dest="on_trigger",
                        type=_parse_bool, default=False,
                        metavar="<true|false>",
                        help="If true: override --startTime/--endTime/--useRth. "
                             "Window becomes (now - windowMin) -> now, "
                             "useRth forced to 0, applied per past trading day.")
    parser.add_argument("--windowMin", type=int, default=30,
                        help="Minutes before trigger time (used only with --on-trigger)")
    parser.add_argument("--barSize",   default="1 min",
                        choices=list(_BAR_SIZE_SEC.keys()),
                        help="IBKR bar size")
    parser.add_argument("--ticks",     type=int, default=10,
                        help="(legacy — unused in bar-based mode, kept for CLI compat)")
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

    trigger_hms = None
    if args.on_trigger:
        now_et = datetime.now(ZoneInfo(TIMEZONE))
        trigger_hms   = now_et.strftime("%H:%M:%S")
        end_sec       = _hms_to_seconds(trigger_hms)
        start_sec     = max(0, end_sec - args.windowMin * 60)
        args.startTime = _seconds_to_hms(start_sec)
        args.endTime   = trigger_hms
        args.useRth    = 0

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s.%(msecs)03d [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    bar_sec = _BAR_SIZE_SEC[args.barSize]
    symbol  = args.symbol.upper()
    days    = past_trading_days(args.days)

    if trigger_hms is not None:
        logging.info(
            f"--on-trigger=true: {args.windowMin}-min window before "
            f"{trigger_hms} ET, useRth forced to 0"
        )

    window_label = f"{args.startTime}-{args.endTime}"
    logging.info(
        f"Symbol={symbol}  days={args.days}  barSize={args.barSize}  "
        f"window={window_label}  useRth={args.useRth}"
    )
    logging.info(f"Dates to sample: {days}")

    # ── Connect ────────────────────────────────────────────────────────────
    app = FastBaselineApp()
    app.connect(args.host, args.port, clientId=args.clientID)

    api_thread = threading.Thread(target=app.run, daemon=True, name="ibapi-run")
    api_thread.start()

    if not app.connected_event.wait(timeout=CONNECT_TIMEOUT):
        logging.error("Failed to connect to IBKR within 10s. "
                      "Is TWS or IB Gateway running with API enabled?")
        return

    # ── Single bar-fetch covering all sampled days ────────────────────────
    # endDateTime anchors at today's args.endTime in ET — IBKR walks back from there.
    # Duration pads +4 calendar days over requested trading days to absorb weekends/holidays.
    today_yyyymmdd = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y%m%d")
    end_dt_str     = f"{today_yyyymmdd} {args.endTime} {TIMEZONE}"
    duration_str   = f"{args.days + 4} D"

    t_wall = time.perf_counter()
    bars = app.fetch_bars(
        contract     = make_contract(symbol),
        end_dt_str   = end_dt_str,
        duration_str = duration_str,
        bar_size     = args.barSize,
        use_rth      = args.useRth,
    )
    app.disconnect()
    api_thread.join(timeout=3)
    total_ms = (time.perf_counter() - t_wall) * 1000

    # ── Group bars by date + window-filter ────────────────────────────────
    start_sec  = _hms_to_seconds(args.startTime)
    end_sec    = _hms_to_seconds(args.endTime)
    window_sec = end_sec - start_sec

    by_date: dict = defaultdict(list)
    for b in bars:
        date, tod, _ = _parse_bar_date(b.date)
        if tod is None:
            continue
        if start_sec <= tod < end_sec:
            by_date[date].append(b)

    # ── Compute per-day stats ─────────────────────────────────────────────
    rows = [
        compute_freq(day, by_date.get(day, []), window_label, window_sec, bar_sec)
        for day in days
    ]
    avg_row  = build_average_row(rows)
    all_rows = rows + [avg_row]

    df = pd.DataFrame(all_rows)
    col_order = [
        "date", "window", "n_trades", "span_sec",
        "iti_mean_sec", "iti_median_sec", "iti_min_sec", "iti_max_sec",
        "trades_per_sec", "trades_per_min",
        "total_volume", "avg_price",
        "first_bar_ts", "last_bar_ts",
    ]
    existing = [c for c in col_order if c in df.columns]
    df = df[existing]

    # ── Display ────────────────────────────────────────────────────────────
    bar_line = "=" * 74
    print(f"\n{bar_line}")
    print(f"  Trade-frequency baseline  |  {symbol}  |  {window_label}")
    print(bar_line)
    print(df.to_string(index=False))
    print(f"\n  Total wall time: {total_ms:.0f} ms  "
          f"({args.days} days, single {args.barSize}-bar request)")

    iti  = avg_row.get("iti_mean_sec")
    rate = avg_row.get("trades_per_min")
    if iti is not None and rate is not None:
        print(f"\n{'─'*74}")
        print(f"  Plug these into ibkr_trade_surge.py:")
        print(f"    SURGE_PRIOR_ITI_MIN = {iti:.1f}   "
              f"# baseline avg seconds between trades")
        print(f"    # A surge fires when live ITI < 2s AND baseline was > {iti:.1f}s")
        print(f"    # Baseline rate: {rate:.1f} trades/min "
              f"→ surge threshold: {rate*5:.0f}+ trades/min")
        print(f"{'─'*74}\n")

    if args.output:
        df.to_csv(args.output, index=False)
        logging.info(f"Saved -> {args.output}")


if __name__ == "__main__":
    main()
