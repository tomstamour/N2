from ib_insync import IB, Stock
import pandas as pd
import argparse
import logging
import os
from datetime import datetime, timedelta, date, timezone

IB_HOST = '127.0.0.1'
IB_PORT = 4001
CLIENT_ID = 6
REQUEST_TIMEOUT = 30
PACING_SLEEP_SECONDS = 2

# US market closes at 4 PM ET; ArrivalTime values are stored in UTC-4 (EDT)
MARKET_CLOSE_ET_HOUR = 16

# Exchange names as they appear in the TSV that are covered by the US data subscription
US_EXCHANGES = {
    'NASDAQ', 'NYSE', 'NYSE AMERICAN', 'NYSE ARCA', 'NYSE MKT', 'AMEX', 'CBOE',
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def is_us_exchange(exchange_val) -> bool:
    """Return True if the exchange value is a supported US market."""
    return str(exchange_val).strip().upper() in {e.upper() for e in US_EXCHANGES}


def get_target_date(arrival_time_str: str) -> date:
    """Return the next trading session date after the given UTC arrival time."""
    dt = datetime.fromisoformat(arrival_time_str).replace(tzinfo=timezone.utc)
    if dt.hour < MARKET_CLOSE_ET_HOUR:
        target = dt.date()
    else:
        target = (dt + timedelta(days=1)).date()
    # Advance past weekends
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return target


def fetch_hod_and_prev_close(ib: IB, symbol: str, target_dt: date):
    """
    Fetch the daily high for target_dt and the close of the prior trading day.
    Returns (daily_high, prev_close) or (None, None) on failure.
    """
    contract = Stock(symbol, 'SMART', 'USD')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            logging.warning(f"{symbol}: could not qualify contract, skipping.")
            return None, None
        contract = qualified[0]
    except Exception as e:
        logging.warning(f"{symbol}: qualification error — {e}")
        return None, None

    # End at midnight UTC of the day after target_dt so the target bar is included
    end_dt_str = (
        datetime(target_dt.year, target_dt.month, target_dt.day, tzinfo=timezone.utc)
        + timedelta(days=1)
    ).strftime('%Y%m%d %H:%M:%S UTC')

    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime=end_dt_str,
            durationStr='3 D',
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=False,
            formatDate=1,
            keepUpToDate=False,
            timeout=REQUEST_TIMEOUT,
        )
    except Exception as e:
        logging.warning(f"{symbol}: reqHistoricalData error — {e}")
        return None, None

    if not bars:
        logging.warning(f"{symbol}: no bars returned for {target_dt}.")
        return None, None

    target_bar = None
    prev_bar = None
    for i, bar in enumerate(bars):
        bar_date = bar.date.date() if isinstance(bar.date, datetime) else bar.date
        if bar_date == target_dt:
            target_bar = bar
            prev_bar = bars[i - 1] if i > 0 else None
            break

    if target_bar is None:
        logging.warning(f"{symbol}: no bar found for target date {target_dt} (bars cover {[b.date for b in bars]}).")
        return None, None

    daily_high = round(target_bar.high, 2)
    prev_close = round(prev_bar.close, 2) if prev_bar is not None else None

    return daily_high, prev_close


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news TSV with DailyHigh($) and DailyHigh(%%) columns.\n\n'
            'Connects to an IBKR TWS/Gateway instance to fetch the intraday high '
            'and previous close for each symbol on the trading day following the '
            'news arrival time. Skips non-US exchange symbols automatically.\n\n'
            'Output file is named <input_stem>_enriched.tsv and written to '
            '--output-dir (defaults to the same directory as the input file).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='FILE',
        dest='input_file',
        help='Path to the input .tsv file (must contain Symbol, ArrivalTime, and Exchange columns).',
    )
    parser.add_argument(
        '--output-dir', '-o',
        metavar='DIR',
        dest='output_dir',
        default=None,
        help='Directory where the enriched TSV will be written. Defaults to the input file\'s directory.',
    )
    args = parser.parse_args()

    df = pd.read_csv(args.input_file, sep='\t')
    df['DailyHigh($)'] = None
    df['DailyHigh(%)'] = None

    ib = IB()
    try:
        logging.info(f"Connecting to IBKR at {IB_HOST}:{IB_PORT} clientId={CLIENT_ID}...")
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=20)
        except ConnectionRefusedError:
            logging.error(
                f"Connection refused on {IB_HOST}:{IB_PORT}. "
                "Make sure TWS or IB Gateway is running with API connections enabled."
            )
            return
        except Exception as e:
            logging.error(f"Could not connect to IBKR: {e}")
            return
        if not ib.isConnected():
            logging.error("Failed to connect to IBKR. Exiting.")
            return
        logging.info("Connected.")

        cache: dict = {}

        for idx, row in df.iterrows():
            symbol = str(row['Symbol']).strip()
            arrival_str = str(row['ArrivalTime']).strip()
            exchange = str(row.get('Exchange', '')).strip()

            if exchange and not is_us_exchange(exchange):
                logging.info(f"{symbol}: exchange '{exchange}' not in US subscription — skipping.")
                continue

            if not exchange:
                logging.info(f"{symbol}: Exchange value missing, attempting SMART/USD.")

            try:
                target_dt = get_target_date(arrival_str)
            except Exception as e:
                logging.warning(f"Row {idx} ({symbol}): could not parse ArrivalTime '{arrival_str}' — {e}")
                continue

            cache_key = (symbol, target_dt)
            if cache_key not in cache:
                logging.info(f"Fetching HOD for {symbol} on {target_dt}...")
                high, prev_close = fetch_hod_and_prev_close(ib, symbol, target_dt)
                cache[cache_key] = (high, prev_close)
                ib.sleep(PACING_SLEEP_SECONDS)
            else:
                high, prev_close = cache[cache_key]

            df.at[idx, 'DailyHigh($)'] = high

            if high is not None and prev_close is not None and prev_close != 0:
                pct = round((high - prev_close) / prev_close * 100, 2)
                df.at[idx, 'DailyHigh(%)'] = pct

    finally:
        if ib.isConnected():
            ib.disconnect()
            logging.info("Disconnected.")

    input_stem = os.path.splitext(os.path.basename(args.input_file))[0]
    out_dir = args.output_dir if args.output_dir else os.path.dirname(os.path.abspath(args.input_file))
    out_path = os.path.join(out_dir, f"{input_stem}_enriched.tsv")
    df.to_csv(out_path, sep='\t', index=False)
    logging.info(f"Saved enriched file to {out_path}")
    logging.info(f"Rows processed: {len(df)}, unique (symbol, date) lookups: {len(cache)}")


if __name__ == '__main__':
    main()
