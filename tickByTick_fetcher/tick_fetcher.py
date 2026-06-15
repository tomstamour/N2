#!/usr/bin/env python3
"""
tick_fetcher.py
---------------
Fetch historical tick-by-tick TRADES and BID_ASK data for a single stock
within a time window and write two TSV files plus a summary to stdout.

IBKR caps each reqHistoricalTicks call at 1000 ticks. For windows longer than
~30 s on active names you may need to widen the window and accept truncation, or
loop with repeated calls (not implemented here).

Usage:
    python tick_fetcher.py --symbol AAPL \
        --start "20260609 09:30:00" --end "20260609 09:30:30" \
        [--output-dir .] [--clientID 10] [--port 4001] [--host 127.0.0.1]

Time strings must be in ET (US Eastern) in YYYYMMDD HH:MM:SS format — this is
the format IBKR expects for US equities.
"""

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
from ib_insync import IB, Stock

log = logging.getLogger(__name__)
ET  = ZoneInfo("America/New_York")
UTC = timezone.utc


def _to_ibkr_utc(ts_str: str) -> str:
    """Convert 'YYYYMMDD HH:MM:SS' ET → IBKR's explicit UTC format 'yyyymmdd-HH:MM:SS'."""
    dt_et = datetime.strptime(ts_str, "%Y%m%d %H:%M:%S").replace(tzinfo=ET)
    return dt_et.astimezone(UTC).strftime("%Y%m%d-%H:%M:%S")


def parse_ibkr_time(ts_str: str) -> datetime:
    """Parse 'YYYYMMDD HH:MM:SS' into an ET-aware datetime."""
    return datetime.strptime(ts_str, "%Y%m%d %H:%M:%S").replace(tzinfo=ET)


def safe_filename_ts(ts_str: str) -> str:
    """'20260609 09:30:00' → '20260609_093000'"""
    return ts_str.replace(" ", "_").replace(":", "")


def setup_logging(level: str, log_path: str) -> None:
    """Send log records to both stderr (short time) and a log file (full date)."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))
    root.handlers.clear()

    stream = logging.StreamHandler()
    stream.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(stream)

    file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(file_handler)


def connect(host: str, port: int, client_id: int) -> IB:
    ib = IB()
    log.info(f"Connecting to IBKR at {host}:{port} clientId={client_id} ...")
    try:
        ib.connect(host, port, clientId=client_id, timeout=20)
    except ConnectionRefusedError:
        log.error(f"Connection refused — is TWS/Gateway running on port {port}?")
        sys.exit(1)
    except Exception as exc:
        log.error(f"Connection failed: {exc}")
        sys.exit(1)
    if not ib.isConnected():
        log.error("connect() returned but IB reports not connected.")
        sys.exit(1)
    log.info("Connected.")
    return ib


_REQUEST_TIMEOUT = 30  # seconds before giving up on a hung IBKR request


def _req_ticks(ib: IB, contract, start: str, end: str, what: str) -> list:
    # IBKR's reqHistoricalTicks accepts only ONE of startDateTime/endDateTime
    # (ib_insync docstring: "the other must be blank"). Anchor on the window
    # start, fetch forward up to 1000 ticks, then trim to the window end.
    start_utc = _to_ibkr_utc(start)
    end_dt    = parse_ibkr_time(end)   # ET-aware; tick.time is UTC-aware → comparison OK
    try:
        ticks = ib.run(asyncio.wait_for(
            ib.reqHistoricalTicksAsync(
                contract,
                startDateTime=start_utc,
                endDateTime="",            # only ONE may be set
                numberOfTicks=1000,
                whatToShow=what,
                useRth=False,
                ignoreSize=False,
            ),
            timeout=_REQUEST_TIMEOUT,
        ))
    except asyncio.TimeoutError:
        log.error(f"{what} request timed out after {_REQUEST_TIMEOUT}s — no data returned")
        return []
    return [t for t in ticks if t.time <= end_dt]


def fetch_trades(ib: IB, contract, start: str, end: str) -> list:
    log.info(f"Fetching TRADES ticks  {start} → {end} ...")
    ticks = _req_ticks(ib, contract, start, end, "TRADES")
    log.info(f"  received {len(ticks)} TRADES ticks")
    return ticks


def fetch_bidask(ib: IB, contract, start: str, end: str) -> list:
    log.info(f"Fetching BID_ASK ticks {start} → {end} ...")
    ticks = _req_ticks(ib, contract, start, end, "BID_ASK")
    log.info(f"  received {len(ticks)} BID_ASK ticks")
    return ticks


def trades_to_df(ticks: list) -> pd.DataFrame:
    rows = []
    for t in ticks:
        rows.append({
            "timestamp_et":         t.time.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S.%f"),
            "price":                round(float(t.price), 4),
            "size":                 int(t.size),
            "exchange":             getattr(t, "exchange", ""),
            "conditions":           getattr(t, "specialConditions", ""),
        })
    return pd.DataFrame(rows)


def bidask_to_df(ticks: list) -> pd.DataFrame:
    rows = []
    for t in ticks:
        bid  = round(float(t.priceBid), 4)
        ask  = round(float(t.priceAsk), 4)
        rows.append({
            "timestamp_et": t.time.astimezone(ET).strftime("%Y-%m-%d %H:%M:%S.%f"),
            "bid_price":    bid,
            "ask_price":    ask,
            "bid_size":     int(t.sizeBid),
            "ask_size":     int(t.sizeAsk),
            "spread":       round(ask - bid, 4),
        })
    return pd.DataFrame(rows)


def print_summary(symbol: str, start: str, end: str,
                  trades_df: pd.DataFrame, bidask_df: pd.DataFrame) -> None:
    sep = "─" * 52
    print(f"\n{sep}")
    print(f"  {symbol}  |  {start}  →  {end}")
    print(sep)

    if trades_df.empty:
        print("  TRADES : no data")
    else:
        prices = trades_df["price"]
        sizes  = trades_df["size"]
        vwap   = (prices * sizes).sum() / sizes.sum() if sizes.sum() > 0 else float("nan")
        print(f"  TRADES : {len(trades_df)} ticks")
        print(f"    price  low={prices.min():.4f}  high={prices.max():.4f}  last={prices.iloc[-1]:.4f}")
        print(f"    vwap   {vwap:.4f}")
        print(f"    volume {sizes.sum():,} shares  avg_size={sizes.mean():.1f}")

    print()

    if bidask_df.empty:
        print("  BID_ASK: no data")
    else:
        spreads = bidask_df["spread"]
        print(f"  BID_ASK: {len(bidask_df)} ticks")
        print(f"    spread  min={spreads.min():.4f}  max={spreads.max():.4f}  mean={spreads.mean():.4f}")
        print(f"    last bid={bidask_df['bid_price'].iloc[-1]:.4f}  ask={bidask_df['ask_price'].iloc[-1]:.4f}")

    print(sep + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="Fetch historical tick-by-tick TRADES + BID_ASK data from IBKR.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--symbol",     required=True,  metavar="SYM",
                        help="Stock ticker symbol, e.g. AAPL")
    parser.add_argument("--start",      required=True,  metavar="DATETIME",
                        help="Window start: 'YYYYMMDD HH:MM:SS' ET")
    parser.add_argument("--end",        required=True,  metavar="DATETIME",
                        help="Window end:   'YYYYMMDD HH:MM:SS' ET")
    parser.add_argument("--output-dir", default=".",    metavar="DIR",
                        help="Directory for output TSV files")
    parser.add_argument("--clientID",   type=int, default=10, metavar="ID",
                        help="IBKR API client ID")
    parser.add_argument("--port",       type=int, default=4001, metavar="PORT",
                        help="IB Gateway/TWS port (4001=live GW, 4002=paper GW, 7496=live TWS)")
    parser.add_argument("--host",       default="127.0.0.1", metavar="HOST")
    parser.add_argument("--loglevel",   default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)
    ts_start = safe_filename_ts(args.start)
    ts_end   = safe_filename_ts(args.end)
    sym      = args.symbol.upper()

    log_path    = os.path.join(args.output_dir, f"{sym}_{ts_start}_{ts_end}.log")
    trades_path = os.path.join(args.output_dir, f"{sym}_trades_{ts_start}_{ts_end}.tsv")
    bidask_path = os.path.join(args.output_dir, f"{sym}_bidask_{ts_start}_{ts_end}.tsv")

    setup_logging(args.loglevel, log_path)
    log.info(f"Logging to {log_path}")

    # Validate timestamp format and ordering.
    try:
        start_dt = parse_ibkr_time(args.start)
        end_dt   = parse_ibkr_time(args.end)
    except ValueError:
        log.error("--start / --end must be in 'YYYYMMDD HH:MM:SS' format")
        sys.exit(1)
    if start_dt >= end_dt:
        log.error(f"--start must be before --end (got {args.start} >= {args.end})")
        sys.exit(1)

    ib = connect(args.host, args.port, args.clientID)
    try:
        contract = Stock(sym, "SMART", "USD")
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            log.error(f"Could not qualify contract for {sym}")
            sys.exit(1)
        contract = qualified[0]
        log.info(f"Qualified: {contract}")

        trades_ticks = fetch_trades(ib, contract, args.start, args.end)
        ib.sleep(0.3)
        bidask_ticks = fetch_bidask(ib, contract, args.start, args.end)
    finally:
        ib.disconnect()
        log.info("Disconnected.")

    trades_df = trades_to_df(trades_ticks)
    bidask_df = bidask_to_df(bidask_ticks)

    trades_df.to_csv(trades_path, sep="\t", index=False)
    bidask_df.to_csv(bidask_path, sep="\t", index=False)
    log.info(f"Wrote {len(trades_df)} rows → {trades_path}")
    log.info(f"Wrote {len(bidask_df)} rows → {bidask_path}")

    print_summary(sym, args.start, args.end, trades_df, bidask_df)


if __name__ == "__main__":
    main()
