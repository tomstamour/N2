#!/usr/bin/env python3
"""
Stock-Split-finder.py — standalone add-on for the daily universe TSVs.

Reads a data/stocks_universe_YYYY-MM-DD.tsv (any column count — existing
columns are byte-preserved via the read-back-as-strings pattern) and writes a
sibling ..._splits.tsv with 3 appended columns describing the most recent
stock split per symbol (source: yfinance, no IBKR connection):

    LastSplit_Date      YYYY-MM-DD ex-date of the most recent split
    LastSplit_Factor    new/old share ratio: 0.05 = 1-for-20 reverse,
                        20 = 20-for-1 forward (yfinance native convention)
    LastSplit_MktHours  marketable hours elapsed since the split, counting
                        only 04:00–20:00 ET (16 h) on NYSE trading days,
                        anchored at 04:00 ET on the ex-date

All 3 stay blank when the symbol has no split on record or the fetch failed
(permissive missing-data policy, logged at WARNING).

Usage:
    python3 Stock-Split-finder.py --input data/stocks_universe_2026-07-15.tsv
                                  [--workers 10] [--limit N]
                                  [--as-of "YYYY-MM-DD HH:MM"]
"""

import argparse
import logging
import sys
import time
from bisect import bisect_left, bisect_right
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

try:
    import pandas_market_calendars as _mcal
except ImportError:
    _mcal = None

ET = ZoneInfo("America/New_York")

_SESSION_START_H = 4    # 04:00 ET
_SESSION_END_H   = 20   # 20:00 ET
_SESSION_HOURS   = _SESSION_END_H - _SESSION_START_H  # 16 h/trading day

_SCRIPT_DIR = Path(__file__).resolve().parent
_RUNS_DIR   = _SCRIPT_DIR / "runs"

log = logging.getLogger("split_finder")


def _setup_logging() -> None:
    date_str = datetime.now().strftime("%d-%b-%Y")
    log_dir = _RUNS_DIR / date_str
    log_dir.mkdir(parents=True, exist_ok=True)

    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(log_dir / "stock_split_finder.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        log.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(ch)


# ── Split fetch ──────────────────────────────────────────────────────────────

def fetch_last_split(symbol: str, attempts: int = 2):
    """
    Return (ex_date: date, factor: float) for the most recent split, or None
    when the symbol has never split. Raises on persistent fetch failure so the
    caller can distinguish "no split" from "no data".
    """
    last_exc = None
    for i in range(attempts):
        try:
            splits = yf.Ticker(symbol).splits
            if splits is None or len(splits) == 0:
                return None
            ts = splits.index[-1]
            if ts.tzinfo is not None:
                ts = ts.tz_convert(ET)
            return ts.date(), float(splits.iloc[-1])
        except Exception as exc:
            last_exc = exc
            if i + 1 < attempts:
                time.sleep(1.0)
    raise last_exc


# ── Marketable-hours clock ───────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> list:
    """NYSE trading days in [start, end], as dates. Weekday walk if the
    calendar library is unavailable (holidays then count as trading days —
    logged once at startup)."""
    if start > end:
        return []
    if _mcal is not None:
        valid = _mcal.get_calendar("NYSE").valid_days(start_date=start, end_date=end)
        return [ts.date() for ts in valid]
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


class MarketHoursClock:
    """
    Counts marketable hours (04:00–20:00 ET on NYSE trading days) between a
    split ex-date (anchored at 04:00 ET) and a fixed as-of instant. The
    trading-day calendar is fetched once for the widest span needed and shared
    across all symbols via bisect.
    """

    def __init__(self, as_of: datetime, earliest: date):
        self.as_of = as_of.astimezone(ET)
        self.days = _trading_days(earliest, self.as_of.date())

    def hours_since(self, split_date: date) -> float:
        lo = bisect_left(self.days, split_date)
        hi = bisect_right(self.days, self.as_of.date())
        total = 0.0
        for d in self.days[lo:hi]:
            start = datetime(d.year, d.month, d.day, _SESSION_START_H, tzinfo=ET)
            end   = datetime(d.year, d.month, d.day, _SESSION_END_H, tzinfo=ET)
            if end > self.as_of:
                end = self.as_of
            if end > start:
                total += (end - start).total_seconds() / 3600.0
        return round(total, 1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--input", required=True,
                    help="path to a stocks_universe_YYYY-MM-DD.tsv")
    ap.add_argument("--workers", type=int, default=10,
                    help="yfinance fetch threads (default 10)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only fetch the first N symbols (testing)")
    ap.add_argument("--as-of", default=None, metavar="'YYYY-MM-DD HH:MM'",
                    help="override 'now' (ET) for reproducible hour counts")
    args = ap.parse_args()

    _setup_logging()

    in_path = Path(args.input)
    if not in_path.exists():
        log.error("input file not found: %s", in_path)
        return 1
    out_path = in_path.with_name(in_path.stem + "_splits.tsv")

    if args.as_of:
        as_of = datetime.strptime(args.as_of, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    else:
        as_of = datetime.now(tz=ET)

    if _mcal is None:
        log.warning("pandas_market_calendars unavailable — weekday walk, "
                    "holidays will count as trading days")

    # Read back as strings so the existing columns round-trip byte-identical.
    df = pd.read_csv(in_path, sep="\t", dtype=str, keep_default_na=False)
    log.info("loaded %s: %d rows, %d columns", in_path.name, len(df), df.shape[1])
    for col in ("LastSplit_Date", "LastSplit_Factor", "LastSplit_MktHours"):
        if col in df.columns:
            log.error("input already has column %s — refusing to double-append", col)
            return 1

    symbols = df["Symbol"].tolist()
    fetch_syms = symbols[: args.limit] if args.limit else symbols

    results = {}   # symbol -> (date, factor) or None (= no split on record)
    failures = []
    log.info("fetching split history for %d symbols (%d workers)",
             len(fetch_syms), args.workers)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futs = {pool.submit(fetch_last_split, s): s for s in fetch_syms}
        for n, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                results[sym] = fut.result()
            except Exception as exc:
                failures.append(sym)
                log.warning("fetch failed for %s: %s", sym, exc)
            if n % 100 == 0:
                log.info("  %d/%d fetched", n, len(fetch_syms))

    split_count = sum(1 for v in results.values() if v is not None)
    log.info("fetch done: %d/%d ok, %d with a split on record, %d failed",
             len(results), len(fetch_syms), split_count, len(failures))
    if failures:
        log.warning("failed symbols: %s", ", ".join(sorted(failures)))

    earliest = min((v[0] for v in results.values() if v is not None),
                   default=as_of.date())
    clock = MarketHoursClock(as_of, earliest)

    dates, factors, hours = [], [], []
    for sym in symbols:
        hit = results.get(sym)
        if hit is None:
            dates.append(""); factors.append(""); hours.append("")
            continue
        d, factor = hit
        dates.append(d.isoformat())
        factors.append(f"{factor:.6g}")
        hours.append(f"{clock.hours_since(d):.1f}")

    df["LastSplit_Date"] = dates
    df["LastSplit_Factor"] = factors
    df["LastSplit_MktHours"] = hours
    df.to_csv(out_path, sep="\t", index=False)
    log.info("wrote %s (%d columns, as-of %s)", out_path.name, df.shape[1],
             as_of.strftime("%Y-%m-%d %H:%M %Z"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
