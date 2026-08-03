from ib_insync import IB, Stock
import pandas as pd
import pandas_market_calendars as mcal
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo

IB_HOST = '127.0.0.1'
# Default to the idle paper Gateway (4002): these bulk 1-min extended-hours
# queries contend with the live stack on 4001 (Error 162 "query cancelled").
# Override with --port 4001 to use the live Gateway.
IB_PORT = 4002
CLIENT_ID = 7
# 1-min extended-hours queries on illiquid small caps are slow on HMDS
# (80-150s+ observed when the live stack shares the Gateway) — default high.
REQUEST_TIMEOUT = 180
PACING_SLEEP_SECONDS = 2

# Resilience tuning
FETCH_RETRIES = 2            # attempts per (symbol, created) before giving up
RECONNECT_ATTEMPTS = 3       # reconnect tries when the connection drops
RECONNECT_BACKOFF_SECONDS = 5
CONSECUTIVE_FAILURE_ABORT = 10  # abort run after this many US fetches fail in a row

# US market closes at 4 PM ET; extended session runs 4 AM - 8 PM ET.
MARKET_CLOSE_ET_HOUR = 16
EXTENDED_OPEN_ET_HOUR = 4
EXTENDED_CLOSE_ET_HOUR = 20

EASTERN = ZoneInfo('America/New_York')

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


def parse_created_dt(created_str: str, arrival_str: str,
                     arrival_date_str: str) -> datetime:
    """Combine the Created time-of-day (US/Eastern) with the row's date.

    Created holds only 'HH:MM:SS.mmm'; the date comes from ArrivalTime when it
    is a full ISO datetime ('2026-05-03 20:45:01'), otherwise from ArrivalDate.
    Returns a tz-aware US/Eastern datetime. Raises ValueError if unparseable.
    """
    created = created_str.strip()
    if not created:
        raise ValueError('empty Created value')

    arrival = arrival_str.strip()
    if arrival[:5].count('-'):
        day = datetime.fromisoformat(arrival).date()
    elif arrival_date_str.strip():
        day = date.fromisoformat(arrival_date_str.strip())
    else:
        raise ValueError('no date available (ArrivalTime time-only, no ArrivalDate)')

    tod = datetime.strptime(created, '%H:%M:%S.%f').time()
    return datetime.combine(day, tod, tzinfo=EASTERN)


def get_target_date(created_dt: datetime) -> date:
    """Return the trading session whose end (8 PM ET) closes the high window.

    Created before the 4 PM close on a trading day -> that same day. Otherwise
    (after close, or a weekend/holiday) -> the next trading session.
    """
    created_date = created_dt.date()
    if created_dt.hour < MARKET_CLOSE_ET_HOUR and is_trading_day(created_date):
        return created_date
    return next_trading_day(created_date)


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


def bar_time_et(bar) -> datetime:
    """Return the bar's start time as a tz-aware US/Eastern datetime."""
    dt = bar.date
    if dt.tzinfo is None:
        return dt.replace(tzinfo=EASTERN)
    return dt.astimezone(EASTERN)


def open_seconds_between(start: datetime, end: datetime) -> int:
    """Seconds between start and end that fall within extended trading sessions
    (4 AM-8 PM ET on US trading days). Overnight closes (8 PM-4 AM) and
    non-trading days contribute zero, so the result reflects only time the
    market was open (extended hours included)."""
    if end <= start:
        return 0
    total = 0
    day = start.date()
    while day <= end.date():
        if is_trading_day(day):
            sess_open = datetime(day.year, day.month, day.day,
                                 EXTENDED_OPEN_ET_HOUR, tzinfo=EASTERN)
            sess_close = datetime(day.year, day.month, day.day,
                                  EXTENDED_CLOSE_ET_HOUR, tzinfo=EASTERN)
            lo = max(start, sess_open)
            hi = min(end, sess_close)
            if hi > lo:
                total += int((hi - lo).total_seconds())
        day += timedelta(days=1)
    return total


def fetch_from_pr_high(ib: IB, symbol: str, created_dt: datetime,
                       target_dt: date, request_timeout: int = REQUEST_TIMEOUT):
    """
    Fetch 1-min bars around created_dt and measure the high since the PR.

    Baseline is the close of the last 1-min bar ENDING at or before the PR's
    own minute (i.e. the last bar strictly before created_dt truncated to the
    minute) — the bar containing the PR already prices the news in and is
    excluded; the high is the max bar high from created_dt to 8 PM ET of
    target_dt (extended hours included), which DOES include the PR's bar.

    Returns (pct, high_time_str, status) where status is one of:
      'ok'      - data fetched and both columns computed
      'failure' - genuine empty/errored response (HMDS/connectivity signal);
                  counts toward the consecutive-failure abort
      'skip'    - contract not found, or bars returned but no baseline/window
                  bar exists; a per-symbol/data condition that must NOT trip
                  the abort
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

    # Window ends at 8 PM ET of the target session; '2 D' of 1-min bars also
    # covers the prior trading day so the pre-Created baseline bar is present
    # even for 04:00 pre-market PRs and Friday-evening -> Monday rolls.
    session_end = datetime(target_dt.year, target_dt.month, target_dt.day,
                           EXTENDED_CLOSE_ET_HOUR, tzinfo=EASTERN)
    end_dt_str = session_end.strftime('%Y%m%d %H:%M:%S US/Eastern')

    bars = None
    for attempt in range(1, FETCH_RETRIES + 1):
        if not ensure_connected(ib):
            logging.warning(f"{symbol}: not connected, cannot fetch.")
            return None, None, 'failure'
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_dt_str,
                durationStr='2 D',
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
                timeout=request_timeout,
            )
        except Exception as e:
            logging.warning(f"{symbol}: reqHistoricalData error (attempt {attempt}/{FETCH_RETRIES}) — {e}")
            bars = None

        if bars:
            break

        if attempt < FETCH_RETRIES:
            logging.warning(f"{symbol}: no bars around {created_dt} (attempt {attempt}/{FETCH_RETRIES}), retrying...")
            ib.sleep(PACING_SLEEP_SECONDS)

    if not bars:
        logging.warning(f"{symbol}: no bars returned around {created_dt} after {FETCH_RETRIES} attempts.")
        return None, None, 'failure'

    baseline = None
    high = None
    high_time = None
    one_min = timedelta(minutes=1)
    # The bar CONTAINING the PR starts before created_dt but already holds the
    # news reaction, so it must not become the baseline — comparing against it
    # subtracts the very move this metric measures (and inverts the sign on a
    # PR that gaps down inside its own minute). Baseline is therefore the last
    # bar that ENDS at or before the PR minute's start; that same PR-minute bar
    # still counts as a high candidate below, which is intended.
    pr_minute_start = created_dt.replace(second=0, microsecond=0)
    for bar in bars:
        start = bar_time_et(bar)
        if start < pr_minute_start:
            baseline = bar.close
        if start + one_min > created_dt and start <= session_end:
            if high is None or bar.high > high:
                high = bar.high
                high_time = start

    if baseline is None or not baseline:
        logging.warning(f"{symbol}: no bar ending at or before {pr_minute_start} "
                        f"to use as baseline, skipping.")
        return None, None, 'skip'
    if high is None:
        logging.warning(f"{symbol}: no bars between {created_dt} and {session_end}, skipping.")
        return None, None, 'skip'

    pct = round((high - baseline) / baseline * 100, 2)
    # Count only market-open (extended-session) time, so overnight closes and
    # weekends/holidays between the PR and the high are not included.
    delta_s = open_seconds_between(created_dt, high_time)
    high_time_str = f"{delta_s // 60:02d}:{delta_s % 60:02d}"

    return pct, high_time_str, 'ok'


def main():
    global IB_HOST, IB_PORT, CLIENT_ID
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news TSV with fromPR_High(%) and fromPR_HighTime(m:s) columns.\n\n'
            'Connects to an IBKR TWS/Gateway instance and, for each row, measures '
            'the highest price reached since the PR "Created" time as a % of the '
            'price just before the PR came out (close of the last 1-min bar ending '
            "at or before the PR's own minute — the bar containing Created already "
            'prices the news in and is excluded), extended hours included. The '
            'window runs until 8 PM ET of '
            'the same session, or of the next trading session for PRs created '
            'after the close / on non-trading days. fromPR_HighTime(m:s) is the '
            'elapsed MM:SS from Created to the bar where that high printed, '
            'counting only market-open (extended-session, 4 AM-8 PM ET) time so '
            'overnight closes and weekends/holidays are excluded.\n\n'
            'Output file is named <input_stem>_fromPRhigh.tsv and written to '
            '--output-dir (defaults to the same directory as the input file).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='FILE',
        dest='input_file',
        help='Path to the input .tsv file (must contain Symbol, Created, ArrivalTime, and Exchange columns).',
    )
    parser.add_argument(
        '--output-dir', '-o',
        metavar='DIR',
        dest='output_dir',
        default=None,
        help='Directory where the enriched TSV will be written. Defaults to the input file\'s directory.',
    )
    parser.add_argument(
        '--output',
        metavar='FILE',
        dest='output_file',
        default=None,
        help='Exact output file path (overrides the <input_stem>_fromPRhigh.tsv naming). '
             'May be the input file itself: the file is written to a temp path and '
             'atomically renamed, so in-place enrichment is safe.',
    )
    parser.add_argument(
        '--only-missing',
        action='store_true',
        dest='only_missing',
        help='Incremental mode: keep existing fromPR_High(%%) values and only fetch rows '
             'where it is empty and fromPR_status is not "skip". Without this flag all '
             'rows are (re)scored, as before.',
    )
    parser.add_argument(
        '--timeout', '-t',
        type=int,
        default=REQUEST_TIMEOUT,
        metavar='SECONDS',
        help=f'Per-request historical data timeout in seconds (default: {REQUEST_TIMEOUT}). '
             'Intraday 1-min queries can take 80-150s+ when the Gateway is busy.',
    )
    parser.add_argument(
        '--log', '-l',
        metavar='FILE',
        dest='log_file',
        default=None,
        help='Path to a log file. Log output is written here (appended) in addition to the console.',
    )
    parser.add_argument(
        '--host',
        default=IB_HOST,
        metavar='HOST',
        help=f'IBKR TWS/Gateway host (default: {IB_HOST}).',
    )
    parser.add_argument(
        '--port',
        type=int,
        default=IB_PORT,
        metavar='PORT',
        help=f'IBKR TWS/Gateway API port (default: {IB_PORT}). 4002 = idle paper '
             'Gateway, recommended for bulk historical to avoid live-stack '
             'contention (Error 162); 4001 = live Gateway.',
    )
    parser.add_argument(
        '--client-id',
        type=int,
        default=CLIENT_ID,
        metavar='ID',
        dest='client_id',
        help=f'IBKR API client id (default: {CLIENT_ID}). Use a distinct id to '
             'avoid collisions when running alongside other scripts on the same Gateway.',
    )
    args = parser.parse_args()

    IB_HOST = args.host
    IB_PORT = args.port
    CLIENT_ID = args.client_id

    setup_logging(args.log_file)
    if args.log_file:
        logging.info(f"Logging to {os.path.abspath(args.log_file)}")

    # dtype=str keeps every untouched column byte-for-byte on the round-trip;
    # default inference would e.g. rewrite integer columns that contain gaps as
    # floats, silently mutating the master on --only-missing in-place runs.
    df = pd.read_csv(args.input_file, sep='\t', dtype=str)
    if 'Created' not in df.columns:
        logging.error("Column 'Created' not found in input file. Run createdTime_AddOn.py first.")
        return
    result_cols = ['fromPR_High(%)', 'fromPR_HighTime(m:s)', 'fromPR_status']
    if args.only_missing:
        for col in result_cols:
            if col not in df.columns:
                df[col] = None
    else:
        for col in result_cols:
            df[col] = None

    ib = IB()
    aborted = False
    try:
        logging.info(f"Connecting to IBKR at {IB_HOST}:{IB_PORT} clientId={CLIENT_ID}...")
        try:
            ib.connect(IB_HOST, IB_PORT, clientId=CLIENT_ID, timeout=20)
        except ConnectionRefusedError:
            logging.error(
                f"Connection refused on {IB_HOST}:{IB_PORT}. "
                "Make sure TWS or IB Gateway is running with API connections enabled."
            )
            sys.exit(1)
        except Exception as e:
            logging.error(f"Could not connect to IBKR: {e}")
            sys.exit(1)
        if not ib.isConnected():
            logging.error("Failed to connect to IBKR. Exiting.")
            sys.exit(1)
        logging.info("Connected.")

        cache: dict = {}
        consecutive_failures = 0
        already_scored = 0

        for idx, row in df.iterrows():
            symbol = str(row['Symbol']).strip()

            if args.only_missing:
                # Keep rows that already have a value, and don't retry rows marked
                # 'skip' (permanent per-symbol/data conditions). 'failure' rows
                # (transient HMDS/connectivity) stay blank and are retried here.
                if pd.notna(row['fromPR_High(%)']):
                    already_scored += 1
                    continue
                prior_status = str(row['fromPR_status']).strip() if pd.notna(row['fromPR_status']) else ''
                if prior_status == 'skip':
                    continue

            created_str = str(row['Created']).strip() if pd.notna(row['Created']) else ''
            arrival_str = str(row.get('ArrivalTime', '')).strip()
            arrival_date_str = str(row.get('ArrivalDate', '')).strip()
            exchange = str(row.get('Exchange', '')).strip()

            if not created_str:
                logging.info(f"Row {idx} ({symbol}): empty Created — skipping.")
                df.at[idx, 'fromPR_status'] = 'skip'
                continue

            if exchange and not is_us_exchange(exchange):
                logging.info(f"{symbol}: exchange '{exchange}' not in US subscription — skipping.")
                df.at[idx, 'fromPR_status'] = 'skip'
                continue

            if not exchange:
                logging.info(f"{symbol}: Exchange value missing, attempting SMART/USD.")

            try:
                created_dt = parse_created_dt(created_str, arrival_str, arrival_date_str)
                target_dt = get_target_date(created_dt)
            except Exception as e:
                logging.warning(f"Row {idx} ({symbol}): could not parse Created '{created_str}' — {e}")
                df.at[idx, 'fromPR_status'] = 'skip'
                continue

            cache_key = (symbol, created_dt)
            if cache_key not in cache:
                logging.info(f"Fetching fromPR high for {symbol} since {created_dt} (window ends {target_dt} 20:00 ET)...")
                pct, high_time_str, status = fetch_from_pr_high(
                    ib, symbol, created_dt, target_dt, args.timeout)
                cache[cache_key] = (pct, high_time_str, status)

                # Track consecutive genuine fetch failures to detect an HMDS outage.
                # 'skip' (unqualifiable contract / no baseline/window bars) is a
                # per-symbol data condition and must not trip the abort.
                if status == 'failure':
                    consecutive_failures += 1
                    if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                        logging.error(
                            f"{consecutive_failures} consecutive fetches failed — HMDS appears down. "
                            f"Aborting to avoid silent data loss; restart IB Gateway and re-run. "
                            f"Last attempted: {symbol} since {created_dt} (row {idx})."
                        )
                        aborted = True
                        break
                elif status == 'ok':
                    consecutive_failures = 0

                ib.sleep(PACING_SLEEP_SECONDS)
            else:
                pct, high_time_str, status = cache[cache_key]

            df.at[idx, 'fromPR_High(%)'] = pct
            df.at[idx, 'fromPR_HighTime(m:s)'] = high_time_str
            df.at[idx, 'fromPR_status'] = status

    finally:
        if ib.isConnected():
            ib.disconnect()
            logging.info("Disconnected.")

    if args.output_file:
        out_path = os.path.abspath(args.output_file)
    else:
        input_stem = os.path.splitext(os.path.basename(args.input_file))[0]
        out_dir = args.output_dir if args.output_dir else os.path.dirname(os.path.abspath(args.input_file))
        out_path = os.path.join(out_dir, f"{input_stem}_fromPRhigh.tsv")
    tmp_path = out_path + '.tmp'
    df.to_csv(tmp_path, sep='\t', index=False)
    os.replace(tmp_path, out_path)
    logging.info(f"Saved enriched file to {out_path}")
    filled = df['fromPR_High(%)'].notna().sum()
    logging.info(
        f"Rows processed: {len(df)}, unique (symbol, created) lookups: {len(cache)}, "
        f"rows with fromPR_High(%): {filled}, blank: {len(df) - filled}"
    )
    if args.only_missing:
        logging.info(f"Rows already scored, kept untouched: {already_scored}")
    if aborted:
        # Partial results are saved above; nonzero exit lets a caller (cron
        # pipeline) flag the run. Missing rows are retried on the next run.
        sys.exit(2)


if __name__ == '__main__':
    main()
