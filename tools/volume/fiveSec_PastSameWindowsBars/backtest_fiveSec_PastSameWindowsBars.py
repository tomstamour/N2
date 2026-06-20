"""Backtest variant of fiveSec_PastSameWindowsBars.

Historical-only. AverageVolume is computed from the same clock-time window
across the previous Y market days (X non-zero bars before + X non-zero bars
after the anchor time on each day) PLUS the current day's X non-zero bars
before --start-datetime. Current-day bars at/after --start-datetime through
--start-datetime + --lifetime are appended to the output but do NOT feed the
average. VolumeRatio = Volume / AverageVolume.

Output rows are ordered chronologically: previous days' windows first,
then the current day's pre-start non-zero bars, then current-day bars from
--start-datetime through --lifetime.
"""

import argparse
import threading
import time
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


ET = pytz.timezone("America/New_York")
MAX_DUR_PER_REQ_SECS = 2000


# NYSE observed-close dates for 2024-2027 (hard-coded to avoid an external
# calendar dependency). Extend this set if you query older/newer dates.
NYSE_HOLIDAYS: set[date] = {
    date(2024, 1, 1), date(2024, 1, 15), date(2024, 2, 19), date(2024, 3, 29),
    date(2024, 5, 27), date(2024, 6, 19), date(2024, 7, 4), date(2024, 9, 2),
    date(2024, 11, 28), date(2024, 12, 25),
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
}


def parse_lifetime(s: str) -> int:
    mm, ss = s.split(":")
    return int(mm) * 60 + int(ss)


def parse_start_datetime(s: str) -> datetime:
    try:
        return ET.localize(datetime.strptime(s, "%Y-%m-%d %H:%M:%S"))
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Invalid --start-datetime '{s}': {e}. Expected format: YYYY-MM-DD HH:MM:SS"
        ) from e


def parse_ibkr_bar_date(raw: str) -> datetime:
    try:
        return datetime.fromtimestamp(int(raw), tz=pytz.UTC).astimezone(ET)
    except ValueError:
        return ET.localize(datetime.strptime(raw, "%Y%m%d %H:%M:%S"))


def format_output_filename(symbol: str, dt: datetime) -> str:
    return f"{symbol}_{dt.day}_{dt.strftime('%B')}_{dt.year}.txt"


def build_stock_contract(symbol: str) -> Contract:
    c = Contract()
    c.symbol = symbol
    c.secType = "STK"
    c.exchange = "SMART"
    c.currency = "USD"
    c.primaryExchange = "NASDAQ"
    return c


def is_market_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    return d not in NYSE_HOLIDAYS


def previous_market_days(anchor_day: date, n: int) -> list[date]:
    out: list[date] = []
    cursor = anchor_day - timedelta(days=1)
    while len(out) < n:
        if is_market_day(cursor):
            out.append(cursor)
        cursor -= timedelta(days=1)
        if cursor < anchor_day - timedelta(days=365 * 2):
            raise RuntimeError("Could not find enough previous market days within 2 years")
    return out


class IBApp(EWrapper, EClient):
    def __init__(self):
        EClient.__init__(self, self)
        self.connection_ready = threading.Event()
        self.historical_done = threading.Event()
        self.lock = threading.Lock()
        self.historical_rows: list[dict] = []
        self.next_valid_id = None

    def nextValidId(self, orderId: int):
        self.next_valid_id = orderId
        self.connection_ready.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2158, 2107, 2100):
            return
        logging.error("IBKR error reqId=%s code=%s msg=%s", reqId, errorCode, errorString)
        if errorCode == 162:
            self.historical_done.set()

    def historicalData(self, reqId, bar):
        dt = parse_ibkr_bar_date(bar.date)
        with self.lock:
            self.historical_rows.append({
                "DateTime": dt,
                "Open": float(bar.open),
                "High": float(bar.high),
                "Low": float(bar.low),
                "Close": float(bar.close),
                "Volume": int(float(bar.volume)) * 100,
            })

    def historicalDataEnd(self, reqId, start, end):
        self.historical_done.set()


def fetch_window(app: IBApp, contract: Contract, end_dt: datetime, total_secs: int, start_req_id: int) -> int:
    """Fetch `total_secs` of 5-sec bars ending at end_dt. Returns next unused reqId."""
    req_id = start_req_id
    remaining = total_secs
    cursor_end = end_dt
    while remaining > 0:
        chunk = min(remaining, MAX_DUR_PER_REQ_SECS)
        end_str = cursor_end.astimezone(ET).strftime("%Y%m%d %H:%M:%S US/Eastern")
        app.historical_done.clear()
        app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_str,
            durationStr=f"{chunk} S",
            barSizeSetting="5 secs",
            whatToShow="TRADES",
            useRTH=0,
            formatDate=2,
            keepUpToDate=False,
            chartOptions=[],
        )
        if not app.historical_done.wait(timeout=60):
            raise TimeoutError(f"Historical request {req_id} timed out")
        req_id += 1
        remaining -= chunk
        if remaining > 0:
            cursor_end = cursor_end - timedelta(seconds=chunk)
            time.sleep(1)
    return req_id


def select_x_nonzero_around(day_df: pd.DataFrame, anchor_dt: datetime, x: int):
    """Return (before_df, after_df) of up to x non-zero-volume bars on each
    side of anchor_dt, restricted to day_df (already filtered to one day)."""
    before = day_df[(day_df["DateTime"] < anchor_dt) & (day_df["Volume"] > 0)].tail(x)
    after = day_df[(day_df["DateTime"] >= anchor_dt) & (day_df["Volume"] > 0)].head(x)
    return before, after


def main():
    p = argparse.ArgumentParser(description="Backtest 5-sec OHLCV with same-clock-window averaging across prior market days.")
    p.add_argument("--symbol", required=True)
    p.add_argument("--client-id", type=int, required=True)
    p.add_argument("--x-bars", type=int, required=True,
                   help="Number of non-zero-volume 5-sec bars to select on each side of the anchor (per day).")
    p.add_argument("--previous-days", type=int, required=True,
                   help="Number of prior market days to sample the same clock window from.")
    p.add_argument("--start-datetime", required=True,
                   help="ET anchor in 'YYYY-MM-DD HH:MM:SS' format.")
    p.add_argument("--lifetime", required=True,
                   help="MM:SS duration AFTER --start-datetime to include in the output.")
    p.add_argument("--output", required=True, help="Output directory.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4001)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start_dt = parse_start_datetime(args.start_datetime)
    lifetime_secs = parse_lifetime(args.lifetime)
    x = args.x_bars
    y = args.previous_days
    # Buffer per side: 20x the theoretical minimum (x*5s) but at least 30 minutes,
    # so that zero-volume stretches still leave room to collect x non-zero bars.
    buffer_secs = max(1800, x * 5 * 20)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    prev_days = previous_market_days(start_dt.date(), y)
    logging.info("Previous %d market days: %s", y, [d.isoformat() for d in prev_days])

    app = IBApp()
    app.connect(args.host, args.port, args.client_id)
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    if not app.connection_ready.wait(timeout=15):
        logging.error("Failed to connect to IBKR within 15s")
        app.disconnect()
        sys.exit(1)
    logging.info("Connected; nextValidId=%s", app.next_valid_id)

    contract = build_stock_contract(args.symbol)
    req_id = 1

    # Per-previous-day: fetch a symmetric buffer around the same clock time.
    for d in prev_days:
        day_anchor = ET.localize(datetime.combine(d, start_dt.time()))
        end_dt = day_anchor + timedelta(seconds=buffer_secs)
        total_secs = 2 * buffer_secs
        logging.info("Fetching previous-day window: day=%s anchor=%s (+/- %ds)",
                     d, day_anchor.strftime("%H:%M:%S"), buffer_secs)
        req_id = fetch_window(app, contract, end_dt, total_secs, req_id)

    # Current day: fetch from (start_dt - buffer) through (start_dt + lifetime).
    current_end = start_dt + timedelta(seconds=lifetime_secs)
    current_total = buffer_secs + lifetime_secs
    logging.info("Fetching current-day window: start=%s, lifetime=%ds, pre-buffer=%ds",
                 start_dt, lifetime_secs, buffer_secs)
    req_id = fetch_window(app, contract, current_end, current_total, req_id)

    app.disconnect()
    api_thread.join(timeout=5)

    with app.lock:
        rows = list(app.historical_rows)
    if not rows:
        logging.error("No historical bars returned; aborting.")
        sys.exit(1)

    df = pd.DataFrame(rows)
    df.sort_values("DateTime", inplace=True)
    df.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    df.reset_index(drop=True, inplace=True)
    # Attach ET date column for per-day slicing.
    df["_Date"] = df["DateTime"].apply(lambda d: d.astimezone(ET).date())

    # Per-previous-day selection.
    avg_parts: list[pd.DataFrame] = []
    for d in prev_days:
        day_df = df[df["_Date"] == d]
        day_anchor = ET.localize(datetime.combine(d, start_dt.time()))
        before, after = select_x_nonzero_around(day_df, day_anchor, x)
        if len(before) < x:
            logging.warning("Day %s: only %d non-zero bars BEFORE anchor (wanted %d)", d, len(before), x)
        if len(after) < x:
            logging.warning("Day %s: only %d non-zero bars AFTER anchor (wanted %d)", d, len(after), x)
        avg_parts.append(before)
        avg_parts.append(after)

    # Current day "before" only.
    current_day_df = df[df["_Date"] == start_dt.date()]
    current_before, _ = select_x_nonzero_around(current_day_df, start_dt, x)
    if len(current_before) < x:
        logging.warning("Current day %s: only %d non-zero bars BEFORE --start-datetime (wanted %d)",
                        start_dt.date(), len(current_before), x)
    avg_parts.append(current_before)

    avg_source = pd.concat(avg_parts, ignore_index=True)
    if avg_source.empty:
        logging.error("No bars available for AverageVolume computation; aborting.")
        sys.exit(1)
    avg_source.sort_values("DateTime", inplace=True)
    avg_source.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    average_volume = avg_source["Volume"].mean()
    logging.info("AverageVolume = %.2f (from %d non-zero bars across %d previous day(s) + current-day 'before')",
                 average_volume, len(avg_source), y)

    # Post-start current-day bars (for display; governed by --lifetime).
    post_start = df[(df["_Date"] == start_dt.date())
                    & (df["DateTime"] >= start_dt)
                    & (df["DateTime"] < start_dt + timedelta(seconds=lifetime_secs))]

    final = pd.concat([avg_source, post_start], ignore_index=True)
    final.sort_values("DateTime", inplace=True)
    final.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    final["AverageVolume"] = average_volume
    final["VolumeRatio"] = final["Volume"] / average_volume
    final = final[["DateTime", "Open", "High", "Low", "Close", "Volume", "AverageVolume", "VolumeRatio"]]

    out_path = out_dir / format_output_filename(args.symbol, start_dt)
    final.to_csv(out_path, sep="\t", index=False)
    logging.info("Wrote %d rows to %s", len(final), out_path)


if __name__ == "__main__":
    main()
