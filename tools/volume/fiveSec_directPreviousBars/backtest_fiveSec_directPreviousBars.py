"""Backtest variant of fiveSec_directPreviousBars.

Historical-only: fetches 5-sec bars covering a warm-up window (before
--start-datetime) plus a simulated "live" window (after --start-datetime, of
duration --lifetime). AverageVolume is the mean volume of the warm-up slice
only, broadcast to every row. VolumeRatio = Volume / AverageVolume.
"""

import argparse
import threading
import time
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytz

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract


ET = pytz.timezone("America/New_York")
MAX_DUR_PER_REQ_SECS = 2000


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
        # 162 = no data returned; IBKR won't call historicalDataEnd, so unblock the wait.
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
    """Fetch `total_secs` of 5-sec bars ending at end_dt, splitting into
    multiple requests if needed. Returns next unused reqId."""
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
        # IBKR pacing: brief sleep between historical requests to avoid throttling.
        if remaining > 0:
            cursor_end = cursor_end - timedelta(seconds=chunk)
            time.sleep(1)
    return req_id


def main():
    p = argparse.ArgumentParser(description="Backtest 5-sec OHLCV collector (historical only).")
    p.add_argument("--symbol", required=True)
    p.add_argument("--client-id", type=int, required=True)
    p.add_argument("--historical-bars-fetch", type=int, required=True,
                   help="Number of warm-up 5-sec bars BEFORE --start-datetime used for AverageVolume.")
    p.add_argument("--start-datetime", required=True,
                   help="ET anchor in 'YYYY-MM-DD HH:MM:SS' format.")
    p.add_argument("--lifetime", required=True,
                   help="MM:SS duration AFTER --start-datetime to keep (e.g. 05:30).")
    p.add_argument("--output", required=True, help="Output directory.")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4001)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    start_dt = parse_start_datetime(args.start_datetime)
    live_seconds = parse_lifetime(args.lifetime)
    live_bars = live_seconds // 5
    warmup_bars = args.historical_bars_fetch
    total_bars = warmup_bars + live_bars
    total_secs = total_bars * 5
    end_dt = start_dt + timedelta(seconds=live_seconds)

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

    logging.info("Fetching %d total 5-sec bars (%d warm-up + %d live) ending at %s...",
                 total_bars, warmup_bars, live_bars, end_dt)
    fetch_window(app, contract, end_dt, total_secs, start_req_id=1)

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

    warmup_slice = df[df["DateTime"] < start_dt]
    if len(warmup_slice) == 0:
        logging.error("Warm-up slice is empty — no bars before %s", start_dt)
        sys.exit(1)
    # Use up to `warmup_bars` most-recent pre-start bars for the average.
    warmup_for_avg = warmup_slice.tail(warmup_bars)
    average_volume = warmup_for_avg["Volume"].mean()
    logging.info("AverageVolume = %.2f (from %d pre-start bars)", average_volume, len(warmup_for_avg))

    # Keep: all warmup bars used in the average + live_bars worth of post-start bars.
    live_slice = df[df["DateTime"] >= start_dt].head(live_bars)
    final = pd.concat([warmup_for_avg, live_slice], ignore_index=True)
    final["AverageVolume"] = average_volume
    final["VolumeRatio"] = final["Volume"] / average_volume
    final = final[["DateTime", "Open", "High", "Low", "Close", "Volume", "AverageVolume", "VolumeRatio"]]

    out_path = out_dir / format_output_filename(args.symbol, start_dt)
    final.to_csv(out_path, sep="\t", index=False)
    logging.info("Wrote %d rows to %s", len(final), out_path)


if __name__ == "__main__":
    main()
