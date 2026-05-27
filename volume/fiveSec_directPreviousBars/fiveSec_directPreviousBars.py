"""Live 5-second OHLC+Volume collector for a single stock via IBKR ibapi.

Fetches a warm-up window of historical 5-sec bars (used to compute a static
AverageVolume), then streams live bars via reqRealTimeBars until --lifetime
elapses. Writes a tab-separated file with columns:
DateTime, Open, High, Low, Close, Volume, AverageVolume, VolumeRatio.
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
MAX_DUR_PER_REQ_SECS = 2000  # IBKR cap for 5-sec bar historical requests


def parse_lifetime(s: str) -> int:
    mm, ss = s.split(":")
    return int(mm) * 60 + int(ss)


def parse_ibkr_bar_date(raw: str) -> datetime:
    # formatDate=2 returns epoch seconds as a string.
    try:
        return datetime.fromtimestamp(int(raw), tz=pytz.UTC).astimezone(ET)
    except ValueError:
        # Fallback to "yyyymmdd HH:mm:ss" if ever returned in that format.
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
        self.live_rows: list[dict] = []
        self.next_valid_id = None

    def nextValidId(self, orderId: int):
        self.next_valid_id = orderId
        self.connection_ready.set()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        # 2104/2106/2158 are info messages about farm connectivity — ignore.
        if errorCode in (2104, 2106, 2158, 2107, 2100):
            return
        logging.error("IBKR error reqId=%s code=%s msg=%s", reqId, errorCode, errorString)

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


def fetch_historical_warmup(app: IBApp, contract: Contract, n_bars: int, start_req_id: int) -> int:
    """Issue one or more 5-sec historical requests covering n_bars * 5 seconds
    ending at now. Returns the next unused reqId."""
    total_secs = n_bars * 5
    req_id = start_req_id
    # Request from most recent backwards in chunks of MAX_DUR_PER_REQ_SECS.
    end_dt_str = ""  # "" == now
    remaining = total_secs
    while remaining > 0:
        chunk = min(remaining, MAX_DUR_PER_REQ_SECS)
        app.historical_done.clear()
        app.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_dt_str,
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
            # Anchor next chunk to end just before the earliest bar we already have.
            with app.lock:
                earliest = min(r["DateTime"] for r in app.historical_rows)
            end_dt_str = (earliest - timedelta(seconds=1)).astimezone(ET).strftime("%Y%m%d-%H:%M:%S US/Eastern")
    return req_id


def main():
    p = argparse.ArgumentParser(description="Collect live 5-sec OHLCV for a stock via IBKR.")
    p.add_argument("--symbol", required=True)
    p.add_argument("--client-id", type=int, required=True)
    p.add_argument("--historical-bars-fetch", type=int, required=True,
                   help="Number of 5-sec warm-up bars used to compute AverageVolume.")
    p.add_argument("--lifetime", required=True, help="MM:SS streaming duration, e.g. 05:30")
    p.add_argument("--output", required=True, help="Output directory (filename auto-generated).")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=4001)
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    total_seconds = parse_lifetime(args.lifetime)
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

    logging.info("Fetching %d warm-up bars (%d seconds)...", args.historical_bars_fetch,
                 args.historical_bars_fetch * 5)
    next_req_id = fetch_historical_warmup(app, contract, args.historical_bars_fetch, start_req_id=1)

    with app.lock:
        warmup_volumes = [r["Volume"] for r in app.historical_rows]
    if not warmup_volumes:
        logging.error("No historical bars returned; aborting.")
        app.disconnect()
        sys.exit(1)
    average_volume = sum(warmup_volumes) / len(warmup_volumes)
    logging.info("AverageVolume = %.2f (from %d bars)", average_volume, len(warmup_volumes))

    logging.info("Starting live stream for %d seconds...", total_seconds)
    live_req_id = next_req_id
    app.reqRealTimeBars(
        reqId=live_req_id,
        contract=contract,
        barSize=5,
        whatToShow="TRADES",
        useRTH=False,
        realTimeBarsOptions=[],
    )

    try:
        time.sleep(total_seconds)
    except KeyboardInterrupt:
        logging.info("Interrupted; stopping stream early.")

    app.cancelRealTimeBars(live_req_id)
    app.disconnect()
    api_thread.join(timeout=5)

    with app.lock:
        hist_rows = list(app.historical_rows)
        live_rows = list(app.live_rows)
    logging.info("Collected %d historical + %d live bars", len(hist_rows), len(live_rows))

    all_rows = hist_rows + live_rows
    df = pd.DataFrame(all_rows)
    df.sort_values("DateTime", inplace=True)
    df.drop_duplicates(subset="DateTime", keep="last", inplace=True)
    df["AverageVolume"] = average_volume
    df["VolumeRatio"] = df["Volume"] / average_volume
    df = df[["DateTime", "Open", "High", "Low", "Close", "Volume", "AverageVolume", "VolumeRatio"]]

    out_path = out_dir / format_output_filename(args.symbol, datetime.now(ET))
    df.to_csv(out_path, sep="\t", index=False)
    logging.info("Wrote %d rows to %s", len(df), out_path)


if __name__ == "__main__":
    main()
