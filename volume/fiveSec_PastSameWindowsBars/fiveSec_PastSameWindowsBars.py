"""Live 5-second OHLC+Volume collector with same-clock-window averaging.

At startup, anchors at 'now' and computes AverageVolume from:
  - X non-zero-volume 5-sec bars BEFORE 'now' on the current day, AND
  - X non-zero bars BEFORE + X non-zero bars AFTER the same clock time on
    each of the previous Y market days.
Then streams live 5-sec bars via reqRealTimeBars for --lifetime. All live
bars get VolumeRatio = Volume / AverageVolume using the startup-computed
average (constant).

Output columns: DateTime, Open, High, Low, Close, Volume, AverageVolume, VolumeRatio.
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
        self.live_rows: list[dict] = []
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
                "Volume": int(float(bar.volume)),
            })

    def historicalDataEnd(self, reqId, start, end):
        self.historical_done.set()

    def realtimeBar(self, reqId, time_, open_, high, low, close, volume, wap, count):
        dt = datetime.fromtimestamp(int(time_), tz=pytz.UTC).astimezone(ET)
        with self.lock:
            self.live_rows.append({
                "DateTime": dt,
                "Open": float(open_),
                "High": float(high),
                "Low": float(low),
                "Close": float(close),
                "Volume": int(float(volume)),
            })


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
    before = day_df[(day_df["DateTime"] < anchor_dt) & (day_df["Volume"] > 0)].tail(x)
    after = day_df[(day_df["DateTime"] >= anchor_dt) & (day_df["Volume"] > 0)].head(x)
    return before, after


def main():
    p = argparse.ArgumentParser(description="Live 5-sec OHLCV with same-clock-window averaging across prior market days.")
    p.add_argument("--symbol", required=True)
    p.add_argument("--client-id", type=int, required=True)
    p.add_argument("--x-bars", type=int, required=True,
                   help="Number of non-zero-volume 5-sec bars per side of the anchor (per day).")
    p.add_argument("--previous-days", type=int, required=True,
                   help="Number of prior market days to sample the same clock window from.")
    p.add_argument("--lifetime", required=True, help="MM:SS streaming duration, e.g. 05:30")
    p.add_argument("--output", required=True, help="Output directory.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4001)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    lifetime_secs = parse_lifetime(args.lifetime)
    x = args.x_bars
    y = args.previous_days
    buffer_secs = max(1800, x * 5 * 20)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

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

    anchor_dt = datetime.now(ET)
    logging.info("Live anchor = %s", anchor_dt.isoformat())
    prev_days = previous_market_days(anchor_dt.date(), y)
    logging.info("Previous %d market days: %s", y, [d.isoformat() for d in prev_days])

    # Per-previous-day: fetch symmetric buffer around same clock time.
    for d in prev_days:
        day_anchor = ET.localize(datetime.combine(d, anchor_dt.time()))
        end_dt = day_anchor + timedelta(seconds=buffer_secs)
        total_secs = 2 * buffer_secs
        logging.info("Fetching previous-day window: day=%s anchor=%s (+/- %ds)",
                     d, day_anchor.strftime("%H:%M:%S"), buffer_secs)
        req_id = fetch_window(app, contract, end_dt, total_secs, req_id)

    # Current day: fetch only pre-anchor buffer (future bars don't exist yet).
    logging.info("Fetching current-day pre-anchor window: anchor=%s buffer=%ds",
                 anchor_dt, buffer_secs)
    req_id = fetch_window(app, contract, anchor_dt, buffer_secs, req_id)

    with app.lock:
        rows = list(app.historical_rows)
    if not rows:
        logging.error("No historical bars returned; aborting.")
        app.disconnect()
        sys.exit(1)

    df = pd.DataFrame(rows)
    df.sort_values("DateTime", inplace=True)
    df.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    df.reset_index(drop=True, inplace=True)
    df["_Date"] = df["DateTime"].apply(lambda d: d.astimezone(ET).date())

    avg_parts: list[pd.DataFrame] = []
    for d in prev_days:
        day_df = df[df["_Date"] == d]
        day_anchor = ET.localize(datetime.combine(d, anchor_dt.time()))
        before, after = select_x_nonzero_around(day_df, day_anchor, x)
        if len(before) < x:
            logging.warning("Day %s: only %d non-zero bars BEFORE anchor (wanted %d)", d, len(before), x)
        if len(after) < x:
            logging.warning("Day %s: only %d non-zero bars AFTER anchor (wanted %d)", d, len(after), x)
        avg_parts.append(before)
        avg_parts.append(after)

    current_day_df = df[df["_Date"] == anchor_dt.date()]
    current_before, _ = select_x_nonzero_around(current_day_df, anchor_dt, x)
    if len(current_before) < x:
        logging.warning("Current day %s: only %d non-zero bars BEFORE anchor (wanted %d)",
                        anchor_dt.date(), len(current_before), x)
    avg_parts.append(current_before)

    avg_source = pd.concat(avg_parts, ignore_index=True)
    if avg_source.empty:
        logging.error("No bars available for AverageVolume computation; aborting.")
        app.disconnect()
        sys.exit(1)
    avg_source.sort_values("DateTime", inplace=True)
    avg_source.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    average_volume = avg_source["Volume"].mean()
    logging.info("AverageVolume = %.2f (from %d non-zero bars across %d previous day(s) + current-day 'before')",
                 average_volume, len(avg_source), y)

    # Stream live bars for --lifetime.
    logging.info("Starting live stream for %d seconds...", lifetime_secs)
    live_req_id = req_id
    app.reqRealTimeBars(
        reqId=live_req_id,
        contract=contract,
        barSize=5,
        whatToShow="TRADES",
        useRTH=False,
        realTimeBarsOptions=[],
    )

    try:
        time.sleep(lifetime_secs)
    except KeyboardInterrupt:
        logging.info("Interrupted; stopping stream early.")

    app.cancelRealTimeBars(live_req_id)
    app.disconnect()
    api_thread.join(timeout=5)

    with app.lock:
        live_rows = list(app.live_rows)
    logging.info("Collected %d live bars", len(live_rows))

    live_df = pd.DataFrame(live_rows) if live_rows else pd.DataFrame(columns=avg_source.columns.drop("_Date", errors="ignore"))

    avg_source_out = avg_source.drop(columns=["_Date"], errors="ignore")
    final = pd.concat([avg_source_out, live_df], ignore_index=True)
    final.sort_values("DateTime", inplace=True)
    final.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    final["AverageVolume"] = average_volume
    final["VolumeRatio"] = final["Volume"] / average_volume
    final = final[["DateTime", "Open", "High", "Low", "Close", "Volume", "AverageVolume", "VolumeRatio"]]

    out_path = out_dir / format_output_filename(args.symbol, datetime.now(ET))
    final.to_csv(out_path, sep="\t", index=False)
    logging.info("Wrote %d rows to %s", len(final), out_path)


if __name__ == "__main__":
    main()
