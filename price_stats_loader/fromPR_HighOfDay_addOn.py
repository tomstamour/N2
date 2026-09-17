from ib_insync import IB, Stock
import pandas as pd
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, date

# Session/calendar/exchange helpers live in session_utils so tools that never touch
# the Gateway (coffee_filter.py) can reuse the exact definitions used to build
# fromPR_High(%) without importing ib_insync. Re-exported here so existing callers
# (dailyStats_pipeline.py, prevclose_backfill_addOn.py) keep importing them from
# this module.
from session_utils import (  # noqa: F401  (re-exported for importers of this module)
    EASTERN,
    EXTENDED_CLOSE_ET_HOUR,
    EXTENDED_OPEN_ET_HOUR,
    MARKET_CLOSE_ET_HOUR,
    US_EXCHANGES,
    get_target_date,
    is_trading_day,
    is_us_exchange,
    next_trading_day,
    open_seconds_between,
    parse_created_dt,
)

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

# Historical request span, in CALENDAR days back from the session end.
BASE_DURATION = '2 D'
# Fallback used only when BASE_DURATION returned no bar before the PR minute:
# 2 calendar days from a Monday 20:00 stops at Saturday, missing Friday's
# session entirely. 5 clears every US market closure (incl. a Good-Friday or
# holiday-Monday long weekend) and stays inside IBKR's 1-week cap for 1-min bars.
WIDE_DURATION = '5 D'

# Resilience tuning
FETCH_RETRIES = 2            # attempts per (symbol, created) before giving up
RECONNECT_ATTEMPTS = 3       # reconnect tries when the connection drops
RECONNECT_BACKOFF_SECONDS = 5
CONSECUTIVE_FAILURE_ABORT = 10  # abort run after this many US fetches fail in a row
# ...but only if they span this many DISTINCT symbols. An HMDS outage is
# symbol-agnostic; a dead contract is not. Without this guard a handful of
# permanently unfetchable symbols retried in file order (CODX x9 + BNBX + INLX,
# 2026-08-30) reach 10 on their own and abort a healthy run.
FAILURE_ABORT_MIN_SYMBOLS = 3

# reqHistoricalData returns an empty list for BOTH a real HMDS outage and a
# contract that will never have data, so the accompanying error text is the only
# thing that tells them apart. Same lists (and the ErrorWatch class below) as
# tools/ohlc-fetch/ohlc_minute_fetch.py — keep them in sync.
#   transient -> 'failure': counts toward the outage abort, retried next run.
TRANSIENT_ERROR_TEXTS = (
    'query cancelled', 'pacing violation', 'timed out', 'time out',
    'not connected', 'connection is broken', 'connectivity between',
    'historical market data service query message',
    'no market data permissions', 'max number of requests',
)
#   permanent -> 'skip': a per-contract condition, must NOT trip the abort.
#   'not supported for this contract' is the CODX case (retired contract, error
#   162 "BEST queries are not supported for this contract"); 'query returned no
#   data' is the BNBX case (PINK/OTC with no HMDS history).
PERMANENT_ERROR_TEXTS = (
    'unknown contract', 'no security definition', 'query returned no data',
    'no data of type', 'invalid step', 'not supported for this contract',
)
# Codes that always mean the connection/farm is the problem.
TRANSIENT_ERROR_CODES = {1100, 1101, 1102, 1300, 2103, 2105, 2157, 504}


class ErrorWatch:
    """Collects IB error events so an empty bar set can be classified.

    Ported from tools/ohlc-fetch/ohlc_minute_fetch.py (the TODO left in
    version_control.txt on 2026-07-27). classify() speaks this module's status
    vocabulary: 'failure' (transient) / 'skip' (permanent) / None (nothing seen).
    """

    def __init__(self, ib):
        self.events: list[tuple[int, str]] = []
        ib.errorEvent += self._on_error

    def _on_error(self, reqId, errorCode, errorString, contract):
        self.events.append((errorCode, errorString or ''))

    def clear(self) -> None:
        self.events.clear()

    def classify(self) -> str | None:
        """'failure' | 'skip' | None. Transient wins: when both kinds of error
        were seen the request is worth retrying on a later run."""
        verdict = None
        for code, text in self.events:
            low = text.lower()
            if code in TRANSIENT_ERROR_CODES:
                return 'failure'
            if any(p in low for p in TRANSIENT_ERROR_TEXTS):
                return 'failure'
            if any(p in low for p in PERMANENT_ERROR_TEXTS):
                verdict = 'skip'
        return verdict

    def texts(self) -> str:
        return '; '.join(f"{c}: {t}" for c, t in self.events)


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


def has_value(series: pd.Series) -> pd.Series:
    """Boolean mask of rows whose cell holds a real (non-empty) value.

    notna() alone is not enough: a cell can round-trip as a whitespace-only
    string, which would otherwise count as 'already scored' and never be filled.
    """
    return series.fillna('').astype(str).str.strip().ne('')


def select_rows_to_fetch(df: pd.DataFrame, retry_skipped: bool) -> tuple[list, dict]:
    """Return (row indices needing a lookup, counts for the run summary).

    A row is done once fromPR_High(%) holds a value. Rows previously booked
    'skip' are permanent per-symbol/data conditions (retired contract, non-US
    exchange, no baseline bar) and are left alone unless retry_skipped is set —
    retrying them every run wastes requests and, when they were misbooked as
    'failure', used to trip CONSECUTIVE_FAILURE_ABORT. Rows booked 'failure'
    are transient (HMDS/connectivity) and are always retried.
    """
    scored = has_value(df['fromPR_High(%)'])
    skipped = ~scored & df['fromPR_status'].fillna('').astype(str).str.strip().eq('skip')

    todo_mask = ~scored if retry_skipped else ~scored & ~skipped
    counts = {
        'total': len(df),
        'scored': int(scored.sum()),
        'skipped': int(skipped.sum()),
        'todo': int(todo_mask.sum()),
    }
    return list(df.index[todo_mask]), counts


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


def fetch_from_pr_high(ib: IB, symbol: str, created_dt: datetime,
                       target_dt: date, request_timeout: int = REQUEST_TIMEOUT,
                       watch: 'ErrorWatch | None' = None):
    """
    Fetch 1-min bars around created_dt and measure the high since the PR.

    Baseline is the close of the last 1-min bar ENDING at or before the PR's
    own minute (i.e. the last bar strictly before created_dt truncated to the
    minute) — the bar containing the PR already prices the news in and is
    excluded; the high is the max bar high from created_dt to 8 PM ET of
    target_dt (extended hours included), which DOES include the PR's bar.

    Returns (pct, high_time_str, status, hmds_answered) where status is one of:
      'ok'      - data fetched and both columns computed
      'failure' - empty/errored response that looks transient (HMDS/connectivity
                  signal); counts toward the consecutive-failure abort
      'skip'    - contract not found, an empty response the error text marks as
                  permanent (retired contract, no HMDS history), or bars
                  returned but no baseline/window bar exists; a per-symbol/data
                  condition that must NOT trip the abort

    hmds_answered is True when HMDS actually returned bars for this request.
    Only that proves the data farm is alive, so it — not the status — is what
    the caller uses to reset the consecutive-failure streak: a 'skip' from a
    failed qualification says nothing about HMDS, while a 'skip' from
    "bars returned but no baseline" says everything.
    """
    contract = Stock(symbol, 'SMART', 'USD')
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            logging.warning(f"{symbol}: could not qualify contract, skipping.")
            return None, None, 'skip', False
        contract = qualified[0]
    except Exception as e:
        logging.warning(f"{symbol}: qualification error — {e}")
        return None, None, 'skip', False

    # Window ends at 8 PM ET of the target session; '2 D' of 1-min bars also
    # covers the prior trading day so the pre-Created baseline bar is present
    # even for 04:00 pre-market PRs and Friday-evening -> Monday rolls.
    session_end = datetime(target_dt.year, target_dt.month, target_dt.day,
                           EXTENDED_CLOSE_ET_HOUR, tzinfo=EASTERN)
    end_dt_str = session_end.strftime('%Y%m%d %H:%M:%S US/Eastern')

    def request_bars(duration: str):
        """Issue the historical request, retrying FETCH_RETRIES times.

        Returns the bar list, or None if the connection is gone (the caller
        turns that into 'failure').
        """
        got = None
        for attempt in range(1, FETCH_RETRIES + 1):
            if not ensure_connected(ib):
                logging.warning(f"{symbol}: not connected, cannot fetch.")
                return None
            # Cleared per attempt so classify() only ever sees this request's
            # errors (a permanent verdict from the previous symbol must not
            # leak forward).
            if watch is not None:
                watch.clear()
            try:
                got = ib.reqHistoricalData(
                    contract,
                    endDateTime=end_dt_str,
                    durationStr=duration,
                    barSizeSetting='1 min',
                    whatToShow='TRADES',
                    useRTH=False,
                    formatDate=2,
                    keepUpToDate=False,
                    timeout=request_timeout,
                )
            except Exception as e:
                logging.warning(f"{symbol}: reqHistoricalData error "
                                f"(attempt {attempt}/{FETCH_RETRIES}) — {e}")
                got = None

            if got:
                break

            if attempt < FETCH_RETRIES:
                logging.warning(f"{symbol}: no bars around {created_dt} "
                                f"(attempt {attempt}/{FETCH_RETRIES}), retrying...")
                ib.sleep(PACING_SLEEP_SECONDS)
        return got

    bars = request_bars(BASE_DURATION)
    if bars is None and not ensure_connected(ib):
        return None, None, 'failure', False

    if not bars:
        # An empty list is ambiguous; the error text decides. Anything the
        # classifier does not recognise stays 'failure' so a novel outage still
        # arms the abort.
        reason = watch.texts() if watch is not None else ''
        status = (watch.classify() if watch is not None else None) or 'failure'
        logging.warning(
            f"{symbol}: no bars returned around {created_dt} after {FETCH_RETRIES} "
            f"attempts — booking '{status}'{f' [{reason}]' if reason else ''}."
        )
        return None, None, status, False

    one_min = timedelta(minutes=1)
    # The bar CONTAINING the PR starts before created_dt but already holds the
    # news reaction, so it must not become the baseline — comparing against it
    # subtracts the very move this metric measures (and inverts the sign on a
    # PR that gaps down inside its own minute). Baseline is therefore the last
    # bar that ENDS at or before the PR minute's start; that same PR-minute bar
    # still counts as a high candidate below, which is intended.
    pr_minute_start = created_dt.replace(second=0, microsecond=0)

    def scan(bar_list):
        """Pick the baseline close, the window's highest high, and its volume."""
        base = None
        hi = None
        hi_t = None
        win_vol = 0.0
        for bar in bar_list:
            start = bar_time_et(bar)
            if start < pr_minute_start:
                base = bar.close
            if start + one_min > created_dt and start <= session_end:
                win_vol += bar.volume or 0
                if hi is None or bar.high > hi:
                    hi = bar.high
                    hi_t = start
        return base, hi, hi_t, win_vol

    baseline, high, high_time, window_volume = scan(bars)

    if baseline is None or not baseline:
        # BASE_DURATION is CALENDAR days back from the session end, so a Monday
        # (or post-holiday) request never reaches the previous *trading*
        # session: '2 D' from Monday 20:00 stops at Saturday. A symbol with no
        # prints in its own pre-market then has no baseline bar at all and used
        # to be skipped outright (SBFM 2026-06-01, INCR 2026-08-24, both
        # Mondays). Widen once, far enough to clear any US market closure.
        # This cannot change an existing value: the baseline is the LAST bar
        # before the PR minute, so older history never displaces it, and the
        # high scan is bounded by created_dt..session_end either way.
        logging.info(f"{symbol}: no baseline bar within {BASE_DURATION}, "
                     f"retrying with {WIDE_DURATION}...")
        wider = request_bars(WIDE_DURATION)
        if wider:
            baseline, high, high_time, window_volume = scan(wider)

    if baseline is None or not baseline:
        logging.warning(f"{symbol}: no bar ending at or before {pr_minute_start} "
                        f"to use as baseline (tried {BASE_DURATION} and "
                        f"{WIDE_DURATION}), skipping.")
        return None, None, 'skip', True
    if high is None:
        logging.warning(f"{symbol}: no bars between {created_dt} and {session_end}, skipping.")
        return None, None, 'skip', True
    if not window_volume:
        # IB forward-fills no-trade minutes inside a returned range (repeated
        # price, volume=0), so a window that never traded still yields bars —
        # and every one of them carries the pre-PR price. The metric then
        # computes a fabricated 0.00 ("no move") from a high that was never
        # printed. Audited 2026-08-31: 5 of 225 zero rows are this case (XHG,
        # DBCA x2, NTZ, CNF); the other 220 have real volume and are genuine.
        # A zero-volume BASELINE bar is normal (90% of all rows) and fine — the
        # carried price is the last real trade. This tests the whole window.
        logging.warning(f"{symbol}: window {created_dt}..{session_end} has zero traded "
                        f"volume (all bars forward-filled), skipping rather than "
                        f"reporting a fabricated 0.00.")
        return None, None, 'skip', True

    pct = round((high - baseline) / baseline * 100, 2)
    # Count only market-open (extended-session) time, so overnight closes and
    # weekends/holidays between the PR and the high are not included.
    delta_s = open_seconds_between(created_dt, high_time)
    high_time_str = f"{delta_s // 60:02d}:{delta_s % 60:02d}"

    return pct, high_time_str, 'ok', True


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
            'INCREMENTAL BY DEFAULT: rows that already carry a fromPR_High(%) value '
            'are detected and left untouched, and only the empty ones are fetched. '
            'Rows previously booked fromPR_status="skip" are not retried either '
            '(--retry-skipped overrides). Pass --rescore-all to wipe the columns and '
            're-fetch everything. The three result columns are created if absent, so '
            'a file that has never been scored is handled the same way.\n\n'
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
        '--rescore-all',
        action='store_true',
        dest='rescore_all',
        help='Clear the three result columns and re-fetch EVERY row, ignoring values '
             'already present. Use after a metric change (e.g. the 2026-07-26 baseline '
             'fix) that invalidates existing values. Default is incremental gap-fill.',
    )
    parser.add_argument(
        '--retry-skipped',
        action='store_true',
        dest='retry_skipped',
        help='Also re-attempt rows previously booked fromPR_status="skip". Those are '
             'normally permanent per-symbol conditions (retired contract, non-US '
             'exchange, no baseline bar), so they are left alone by default.',
    )
    parser.add_argument(
        '--only-missing',
        action='store_true',
        dest='only_missing',
        help=argparse.SUPPRESS,  # deprecated: gap-fill is now the default. Kept so
                                 # dailyStats_pipeline.py keeps working unchanged.
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

    if args.only_missing:
        logging.warning("--only-missing is deprecated and now a no-op: incremental "
                        "gap-fill is the default. Use --rescore-all to re-fetch every row.")

    # dtype=str keeps every untouched column byte-for-byte on the round-trip;
    # default inference would e.g. rewrite integer columns that contain gaps as
    # floats, silently mutating the master on in-place gap-fill runs.
    df = pd.read_csv(args.input_file, sep='\t', dtype=str)
    if 'Created' not in df.columns:
        logging.error("Column 'Created' not found in input file. Run createdTime_AddOn.py first.")
        return
    result_cols = ['fromPR_High(%)', 'fromPR_HighTime(m:s)', 'fromPR_status']
    if args.rescore_all:
        for col in result_cols:
            df[col] = None
    else:
        for col in result_cols:
            if col not in df.columns:
                df[col] = None

    todo, counts = select_rows_to_fetch(df, args.retry_skipped)
    already_scored = counts['scored']
    if args.rescore_all:
        logging.info("--rescore-all: re-fetching all %d rows.", counts['total'])
    else:
        logging.info(
            "Scan: %d rows — %d already scored, %d booked 'skip'%s, %d to fetch.",
            counts['total'], counts['scored'], counts['skipped'],
            ' (retrying)' if args.retry_skipped else ' (not retried)', counts['todo'],
        )

    ib = IB()
    aborted = False
    cache: dict = {}
    watch: ErrorWatch | None = None
    if not todo:
        # Nothing to do — don't take a Gateway client slot just to rewrite the
        # file unchanged. The output is still written below so --output-file
        # semantics (e.g. copying to a new path) hold either way.
        logging.info("No rows need fetching; skipping the IBKR connection.")
    try:
        if todo:
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
            # Subscribed once for the whole run; ensure_connected() reconnects
            # the same IB object, so the handler survives a dropped connection.
            watch = ErrorWatch(ib)

        consecutive_failures = 0
        failure_symbols: set[str] = set()

        for n, idx in enumerate(todo, 1):
            row = df.loc[idx]
            symbol = str(row['Symbol']).strip()

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
                logging.info(f"[{n}/{len(todo)}] Fetching fromPR high for {symbol} since "
                             f"{created_dt} (window ends {target_dt} 20:00 ET)...")
                pct, high_time_str, status, hmds_answered = fetch_from_pr_high(
                    ib, symbol, created_dt, target_dt, args.timeout, watch)
                cache[cache_key] = (pct, high_time_str, status)

                # Track consecutive genuine fetch failures to detect an HMDS
                # outage. 'skip' (unqualifiable contract, permanently dead
                # contract, no baseline/window bars) is a per-symbol data
                # condition and must not trip the abort. The streak is reset by
                # any request HMDS actually answered with bars — not just an
                # 'ok' — and the abort additionally requires the streak to span
                # several symbols, since an outage fails everything while a dead
                # contract fails only itself.
                if hmds_answered:
                    consecutive_failures = 0
                    failure_symbols.clear()
                elif status == 'failure':
                    consecutive_failures += 1
                    failure_symbols.add(symbol)
                    if (consecutive_failures >= CONSECUTIVE_FAILURE_ABORT
                            and len(failure_symbols) >= FAILURE_ABORT_MIN_SYMBOLS):
                        logging.error(
                            f"{consecutive_failures} consecutive fetches failed across "
                            f"{len(failure_symbols)} symbols — HMDS appears down. "
                            f"Aborting to avoid silent data loss; restart IB Gateway and re-run. "
                            f"Last attempted: {symbol} since {created_dt} (row {idx})."
                        )
                        aborted = True
                        break

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
    filled = int(has_value(df['fromPR_High(%)']).sum())
    logging.info(
        f"Rows attempted: {len(todo)} of {len(df)}, unique (symbol, created) lookups: "
        f"{len(cache)}, rows with fromPR_High(%): {filled} (was {already_scored}), "
        f"blank: {len(df) - filled}"
    )
    status_counts = df['fromPR_status'].fillna('(blank)').value_counts().to_dict()
    logging.info(f"fromPR_status: {status_counts}")
    if aborted:
        # Partial results are saved above; nonzero exit lets a caller (cron
        # pipeline) flag the run. Missing rows are retried on the next run.
        sys.exit(2)


if __name__ == '__main__':
    main()
