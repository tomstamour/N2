from ib_insync import IB, Stock
import pandas as pd
import pandas_market_calendars as mcal
import argparse
import logging
import os
from datetime import datetime, timedelta, date, timezone

IB_HOST = '127.0.0.1'
IB_PORT = 4001
CLIENT_ID = 6
REQUEST_TIMEOUT = 30
PACING_SLEEP_SECONDS = 2

# Resilience tuning
FETCH_RETRIES = 2            # attempts per (symbol, date) before giving up
RECONNECT_ATTEMPTS = 3       # reconnect tries when the connection drops
RECONNECT_BACKOFF_SECONDS = 5
CONSECUTIVE_FAILURE_ABORT = 10  # abort run after this many US fetches fail in a row

# US market closes at 4 PM ET; ArrivalTime values are stored in UTC-4 (EDT)
MARKET_CLOSE_ET_HOUR = 16

# Exchange names as they appear in the TSV that are covered by the US data subscription
US_EXCHANGES = {
    'NASDAQ', 'NYSE', 'NYSE AMERICAN', 'NYSE ARCA', 'NYSE MKT', 'AMEX', 'CBOE',
}

def setup_logging(log_path: str | None = None) -> None:
    """Configure logging to stderr and, optionally, to a file.

    When log_path is given, log records are also appended to that file. Parent
    directories are created if needed.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path:
        log_dir = os.path.dirname(os.path.abspath(log_path))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True,
    )


def is_us_exchange(exchange_val) -> bool:
    """Return True if the exchange value is a supported US market."""
    return str(exchange_val).strip().upper() in {e.upper() for e in US_EXCHANGES}


# US equity trading calendar (NYSE/Nasdaq). Used to skip weekends AND holidays
# (e.g. Memorial Day) so target dates always land on a real trading session.
_XNYS = mcal.get_calendar('XNYS')


def is_trading_day(d: date) -> bool:
    """Return True if d is a US equity trading session (not weekend/holiday)."""
    return len(_XNYS.valid_days(start_date=d, end_date=d)) > 0


def next_trading_day(d: date) -> date:
    """Return the first US trading session strictly after d."""
    sched = _XNYS.valid_days(start_date=d + timedelta(days=1),
                             end_date=d + timedelta(days=10))
    return sched[0].date()


def get_target_date(arrival_time_str: str, arrival_date_str: str = "") -> date:
    """Return the trading session date to fetch for the given UTC arrival time.

    Before market close on a trading day -> that same day. Otherwise (after
    close, or arrival landing on a weekend/holiday) -> the next trading session.

    ArrivalTime may be a full ISO datetime ('2026-06-12 11:00:04.751') or, in
    newer inputs, time-only ('12:00:13.918'). In the time-only case the date is
    taken from arrival_date_str (the row's ArrivalDate column).
    """
    s = arrival_time_str.strip()
    # Time-only values (no leading 'YYYY-' date part): prepend ArrivalDate.
    if not s[:5].count('-') and arrival_date_str:
        s = f"{arrival_date_str.strip()} {s}"
    dt = datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
    arrival_date = dt.date()
    if dt.hour < MARKET_CLOSE_ET_HOUR and is_trading_day(arrival_date):
        return arrival_date
    return next_trading_day(arrival_date)


def ensure_connected(ib: IB) -> bool:
    """Ensure the IB connection is live, reconnecting if it has dropped.

    Returns True if connected (or successfully reconnected), False otherwise.
    """
    if ib.isConnected():
        return True

    for attempt in range(1, RECONNECT_ATTEMPTS + 1):
        logging.warning(f"Connection lost — reconnect attempt {attempt}/{RECONNECT_ATTEMPTS}...")
        try:
            ib.disconnect()
        except Exception:
            pass
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=20)
        except Exception as e:
            logging.warning(f"Reconnect attempt {attempt} failed: {e}")
        if ib.isConnected():
            logging.info("Reconnected.")
            return True
        ib.sleep(RECONNECT_BACKOFF_SECONDS)

    logging.error(f"Could not reconnect after {RECONNECT_ATTEMPTS} attempts.")
    return False


def fetch_hod_and_prev_close(ib: IB, symbol: str, target_dt: date):
    """
    Fetch the daily high for target_dt and the close of the prior trading day.

    Returns (daily_high, prev_close, status) where status is one of:
      'ok'      - data fetched successfully
      'failure' - genuine empty/errored response (HMDS/connectivity signal);
                  counts toward the consecutive-failure abort
      'skip'    - contract not found or target date is not a trading session;
                  a per-symbol/data condition that must NOT trip the abort
    """
    contract = Stock(symbol, 'SMART', 'USD')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            logging.warning(f"{symbol}: could not qualify contract, skipping.")
            return None, None, 'skip'
        contract = qualified[0]
    except Exception as e:
        logging.warning(f"{symbol}: qualification error — {e}")
        return None, None, 'skip'

    # End at midnight UTC of the day after target_dt so the target bar is included
    end_dt_str = (
        datetime(target_dt.year, target_dt.month, target_dt.day, tzinfo=timezone.utc)
        + timedelta(days=1)
    ).strftime('%Y%m%d %H:%M:%S UTC')

    bars = None
    for attempt in range(1, FETCH_RETRIES + 1):
        if not ensure_connected(ib):
            logging.warning(f"{symbol}: not connected, cannot fetch.")
            return None, None, 'failure'
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
            logging.warning(f"{symbol}: reqHistoricalData error (attempt {attempt}/{FETCH_RETRIES}) — {e}")
            bars = None

        if bars:
            break

        if attempt < FETCH_RETRIES:
            logging.warning(f"{symbol}: no bars for {target_dt} (attempt {attempt}/{FETCH_RETRIES}), retrying...")
            ib.sleep(PACING_SLEEP_SECONDS)

    if not bars:
        logging.warning(f"{symbol}: no bars returned for {target_dt} after {FETCH_RETRIES} attempts.")
        return None, None, 'failure'

    target_bar = None
    prev_bar = None
    for i, bar in enumerate(bars):
        bar_date = bar.date.date() if isinstance(bar.date, datetime) else bar.date
        if bar_date == target_dt:
            target_bar = bar
            prev_bar = bars[i - 1] if i > 0 else None
            break

    if target_bar is None:
        # Bars came back but the target day isn't among them: a non-trading
        # session (holiday/half-day gap), not an HMDS outage — skip, don't abort.
        logging.warning(f"{symbol}: no bar found for target date {target_dt} (bars cover {[b.date for b in bars]}).")
        return None, None, 'skip'

    daily_high = round(target_bar.high, 2)
    prev_close = round(prev_bar.close, 2) if prev_bar is not None else None

    return daily_high, prev_close, 'ok'


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
    parser.add_argument(
        '--log', '-l',
        metavar='FILE',
        dest='log_file',
        default=None,
        help='Path to a log file. Log output is written here (appended) in addition to the console.',
    )
    args = parser.parse_args()

    setup_logging(args.log_file)
    if args.log_file:
        logging.info(f"Logging to {os.path.abspath(args.log_file)}")

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
        consecutive_failures = 0

        for idx, row in df.iterrows():
            symbol = str(row['Symbol']).strip()
            arrival_str = str(row['ArrivalTime']).strip()
            arrival_date_str = str(row.get('ArrivalDate', '')).strip()
            exchange = str(row.get('Exchange', '')).strip()

            if exchange and not is_us_exchange(exchange):
                logging.info(f"{symbol}: exchange '{exchange}' not in US subscription — skipping.")
                continue

            if not exchange:
                logging.info(f"{symbol}: Exchange value missing, attempting SMART/USD.")

            try:
                target_dt = get_target_date(arrival_str, arrival_date_str)
            except Exception as e:
                logging.warning(f"Row {idx} ({symbol}): could not parse ArrivalTime '{arrival_str}' — {e}")
                continue

            cache_key = (symbol, target_dt)
            if cache_key not in cache:
                logging.info(f"Fetching HOD for {symbol} on {target_dt}...")
                high, prev_close, status = fetch_hod_and_prev_close(ib, symbol, target_dt)
                cache[cache_key] = (high, prev_close)

                # Track consecutive genuine fetch failures to detect an HMDS outage.
                # 'skip' (unqualifiable contract / non-trading target date) is a
                # per-symbol data condition and must not trip the abort.
                if status == 'failure':
                    consecutive_failures += 1
                    if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                        logging.error(
                            f"{consecutive_failures} consecutive fetches failed — HMDS appears down. "
                            f"Aborting to avoid silent data loss; restart IB Gateway and re-run. "
                            f"Last attempted: {symbol} on {target_dt} (row {idx})."
                        )
                        break
                elif status == 'ok':
                    consecutive_failures = 0

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
