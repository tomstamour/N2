#!/usr/bin/env python
"""
ohlc_minute_fetch.py — attach a window of 1-minute OHLCV bars to each PR row.

Reads a TSV that has (at least) the columns `Symbol`, `ArrivalDate` and
`Created` (the wire timestamp, e.g. `02:11:27.094`), and for every row fetches
1-minute TRADES bars from IBKR around that moment, writing them back out as
wide, offset-labelled columns:

    m-05_open m-05_high m-05_low m-05_close m-05_volume  m-04_open ...

`m+00` is the minute containing the PR timestamp; offsets are minutes relative
to it. Minutes with no trade come back blank (an illiquid pre-market name
simply has no bar for that minute — that is not the same as volume 0).

Resumability: the output TSV is appended row-by-row and flushed+fsynced after
each one. Re-running the same command picks up at the first input row that has
no output row yet, so a crashed or --stop-point'ed run just continues.

Usage
-----
    /home/tom/venv/bin/python ohlc_minute_fetch.py input_example.tsv \
        --minutes-before 5 --minutes-after 60 --log

    # overnight run that halts before the open so the Gateway is free
    ... --stop-point 2026-07-27-09-25

Notes
-----
* Timestamps in the input are interpreted as US/Eastern.
* Run bulk jobs against the idle paper Gateway (default port 4002); the live
  Gateway drops 1-min extended-hours queries under contention.
* Volume is written exactly as IB returns it. On this Gateway (server v176)
  that is a share count, and it can be fractional (odd/fractional lots) — e.g.
  YXT 2026-07-10 07:30 came back as 333.4. Use --volume-scale only if your
  Gateway reports round lots instead.
"""

import argparse
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock

EASTERN = ZoneInfo('America/New_York')

IB_HOST = '127.0.0.1'
IB_PORT = 4002          # idle paper Gateway; 4001 is LIVE
CLIENT_ID = 31

REQUEST_TIMEOUT = 180           # 1-min extended-hours HMDS queries are slow
PACING_SLEEP_SECONDS = 2
FETCH_RETRIES = 2               # attempts per row before calling it a failure
RECONNECT_ATTEMPTS = 3
RECONNECT_BACKOFF_SECONDS = 5
CONSECUTIVE_FAILURE_ABORT = 10  # abort the run after this many failures in a row

# Extended US session, ET.
EXTENDED_OPEN_HOUR = 4
EXTENDED_CLOSE_HOUR = 20

# Exchange strings in the TSV that our US market-data subscription covers.
US_EXCHANGES = {
    'NASDAQ', 'NYSE', 'NYSE AMERICAN', 'NYSE ARCA', 'NYSE MKT', 'AMEX', 'CBOE',
}

# Extra columns written next to the bars.
META_COLS = ['ohlc_anchor_et', 'ohlc_status', 'ohlc_bars_returned']

# Row statuses that are terminal (row gets written, never retried on resume).
TERMINAL_STATUSES = {'ok', 'nodata', 'no_contract', 'non_us', 'holiday', 'bad_time'}

# An empty bar set means two very different things, and IB reports both as an
# empty list. Text in the accompanying error decides:
#   transient (the run should retry / eventually abort, row NOT written) ...
TRANSIENT_ERROR_TEXTS = (
    'query cancelled', 'pacing violation', 'timed out', 'time out',
    'not connected', 'connection is broken', 'connectivity between',
    'historical market data service query message',
    'no market data permissions', 'max number of requests',
)
#   ... vs genuinely nothing there (row written blank, never retried).
PERMANENT_ERROR_TEXTS = (
    'unknown contract', 'no security definition', 'query returned no data',
    'no data of type', 'invalid step',
)
# Codes that always mean the connection/farm is the problem.
TRANSIENT_ERROR_CODES = {1100, 1101, 1102, 1300, 2103, 2105, 2157, 504}


# --------------------------------------------------------------------------- #
# logging
# --------------------------------------------------------------------------- #

def setup_logging(log_path, verbose):
    """Console always; a verbatim file log when --log was given."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)-7s %(message)s')

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(console)

    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)) or '.', exist_ok=True)
        fh = logging.FileHandler(log_path, mode='a', encoding='utf-8')
        fh.setFormatter(fmt)
        fh.setLevel(logging.DEBUG)      # the file gets everything, verbatim
        root.addHandler(fh)
        logging.info(f"Log file: {log_path}")


# --------------------------------------------------------------------------- #
# time helpers
# --------------------------------------------------------------------------- #

def parse_anchor(arrival_date, created, fallback_created=None):
    """
    Combine `ArrivalDate` (YYYY-MM-DD) and `Created` (HH:MM:SS[.mmm]) into an
    ET datetime floored to the minute. Returns None if unparseable.
    """
    for value in (created, fallback_created):
        if not value:
            continue
        text = value.strip()
        if not text:
            continue
        for time_fmt in ('%H:%M:%S.%f', '%H:%M:%S', '%H:%M'):
            try:
                t = datetime.strptime(text, time_fmt)
            except ValueError:
                continue
            try:
                d = datetime.strptime(arrival_date.strip(), '%Y-%m-%d')
            except (ValueError, AttributeError):
                return None
            return datetime(d.year, d.month, d.day, t.hour, t.minute,
                            tzinfo=EASTERN)
    return None


def parse_stop_point(text):
    """--stop-point yyyy-mm-dd-hh-mm -> ET datetime."""
    try:
        dt = datetime.strptime(text, '%Y-%m-%d-%H-%M')
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--stop-point must look like 2026-07-27-09-25 (got {text!r})")
    return dt.replace(tzinfo=EASTERN)


def load_sessions(dates):
    """
    Set of NYSE session dates covering `dates` (plus a week of slack so a
    late-evening PR can roll onto the next session).
    """
    if not dates:
        return None
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        logging.warning("pandas_market_calendars not available — session/holiday "
                        "checks disabled (run with the venv python).")
        return None
    cal = mcal.get_calendar('NYSE')
    start = min(dates) - timedelta(days=7)
    end = max(dates) + timedelta(days=10)
    days = cal.valid_days(start_date=start.isoformat(), end_date=end.isoformat())
    return {d.date() for d in days}


def resolve_anchor(anchor, sessions):
    """
    Map a raw PR minute onto a tradeable session minute.

    * before 04:00 ET  -> 04:00 the same session day (pre-market open)
    * at/after 20:00 ET -> 04:00 of the next session
    * on a holiday/weekend -> 04:00 of the next session

    Returns (anchor_et, note) or (None, reason) when no session can be found.
    """
    note = None

    def next_session(after_date):
        for i in range(1, 15):
            cand = after_date + timedelta(days=i)
            if sessions is None or cand in sessions:
                return cand
        return None

    if anchor.hour >= EXTENDED_CLOSE_HOUR:
        nxt = next_session(anchor.date())
        if nxt is None:
            return None, 'holiday'
        anchor = datetime(nxt.year, nxt.month, nxt.day, EXTENDED_OPEN_HOUR,
                          tzinfo=EASTERN)
        note = 'rolled to next session (PR after 20:00 ET)'
    elif anchor.hour < EXTENDED_OPEN_HOUR:
        anchor = anchor.replace(hour=EXTENDED_OPEN_HOUR, minute=0)
        note = 'clamped to 04:00 ET (PR before pre-market open)'

    if sessions is not None and anchor.date() not in sessions:
        nxt = next_session(anchor.date())
        if nxt is None:
            return None, 'holiday'
        anchor = datetime(nxt.year, nxt.month, nxt.day, EXTENDED_OPEN_HOUR,
                          tzinfo=EASTERN)
        note = 'rolled to next session (PR on a non-session day)'

    return anchor, note


# --------------------------------------------------------------------------- #
# IBKR
# --------------------------------------------------------------------------- #

def connect(ib, host, port, client_id):
    for attempt in range(1, RECONNECT_ATTEMPTS + 1):
        try:
            ib.connect(host, port, clientId=client_id, timeout=20)
            logging.info(f"Connected to IB Gateway {host}:{port} clientId={client_id}")
            return True
        except Exception as e:
            logging.warning(f"Connect attempt {attempt}/{RECONNECT_ATTEMPTS} failed — {e}")
            time.sleep(RECONNECT_BACKOFF_SECONDS)
    return False


def ensure_connected(ib, host, port, client_id):
    if ib.isConnected():
        return True
    logging.warning("Connection lost — reconnecting...")
    try:
        ib.disconnect()
    except Exception:
        pass
    return connect(ib, host, port, client_id)


def qualify(ib, symbol, cache):
    if symbol in cache:
        return cache[symbol]
    contract = None
    try:
        qualified = ib.qualifyContracts(Stock(symbol, 'SMART', 'USD'))
        if qualified:
            contract = qualified[0]
            logging.debug(f"{symbol}: qualified as conId={contract.conId} "
                          f"primary={contract.primaryExchange}")
        else:
            logging.warning(f"{symbol}: could not qualify contract.")
    except Exception as e:
        logging.warning(f"{symbol}: qualification error — {e}")
    cache[symbol] = contract
    return contract


class ErrorWatch:
    """Collects IB error events so an empty bar set can be classified."""

    def __init__(self, ib):
        self.events = []
        ib.errorEvent += self._on_error

    def _on_error(self, reqId, errorCode, errorString, contract):
        self.events.append((errorCode, errorString or ''))

    def clear(self):
        self.events.clear()

    def classify(self):
        """'failure' | 'nodata' | None (nothing relevant seen)."""
        verdict = None
        for code, text in self.events:
            low = text.lower()
            if code in TRANSIENT_ERROR_CODES:
                return 'failure'
            if any(p in low for p in TRANSIENT_ERROR_TEXTS):
                return 'failure'
            if any(p in low for p in PERMANENT_ERROR_TEXTS):
                verdict = 'nodata'
        return verdict

    def texts(self):
        return '; '.join(f"{c}: {t}" for c, t in self.events)


def duration_str(seconds):
    """IB durationStr covering `seconds` of wall clock."""
    seconds = max(int(seconds) + 120, 300)      # pad so edge bars are included
    if seconds <= 86400:
        return f"{seconds} S"
    days = seconds // 86400 + 2
    return f"{min(days, 30)} D"


def fetch_bars(ib, contract, start_et, end_et, timeout, host, port, client_id,
               watch):
    """
    Fetch 1-min TRADES bars covering [start_et, end_et].

    Returns (bars, status) where status is 'ok', 'nodata' (genuine empty
    response for this symbol/window) or 'failure' (connectivity/HMDS problem —
    the caller retries and eventually aborts rather than writing a misleading
    permanent blank row).
    """
    now_et = datetime.now(EASTERN)
    if end_et > now_et:
        end_str = ''                            # '' means 'now' to IB
        span = (now_et - start_et).total_seconds()
    else:
        end_str = end_et.strftime('%Y%m%d %H:%M:%S US/Eastern')
        span = (end_et - start_et).total_seconds()
    if span <= 0:
        return None, 'nodata'
    dur = duration_str(span)

    last_error = None
    verdict = None
    for attempt in range(1, FETCH_RETRIES + 1):
        if not ensure_connected(ib, host, port, client_id):
            return None, 'failure'
        watch.clear()
        try:
            logging.debug(f"{contract.symbol}: reqHistoricalData end={end_str or 'now'} "
                          f"duration={dur} 1 min TRADES useRTH=False")
            bars = ib.reqHistoricalData(
                contract,
                endDateTime=end_str,
                durationStr=dur,
                barSizeSetting='1 min',
                whatToShow='TRADES',
                useRTH=False,
                formatDate=2,
                keepUpToDate=False,
                timeout=timeout,
            )
        except Exception as e:
            last_error = e
            logging.warning(f"{contract.symbol}: reqHistoricalData error "
                            f"(attempt {attempt}/{FETCH_RETRIES}) — {e}")
            bars = None

        if bars:
            logging.debug(f"{contract.symbol}: {len(bars)} raw bars returned")
            return bars, 'ok'

        verdict = watch.classify()
        errs = watch.texts()
        logging.warning(f"{contract.symbol}: empty bar set "
                        f"(attempt {attempt}/{FETCH_RETRIES}) "
                        f"[{errs or 'no IB error reported'}] -> "
                        f"{verdict or 'unclassified'}")
        if verdict == 'nodata':
            # Nothing there to find — retrying would only burn Gateway time.
            return None, 'nodata'

        if attempt < FETCH_RETRIES:
            ib.sleep(PACING_SLEEP_SECONDS)

    if last_error is not None or verdict == 'failure':
        return None, 'failure'
    # Empty and quiet: IB simply has nothing for this window.
    return None, 'nodata'


def index_bars(bars):
    """{ET minute datetime: bar} for the returned bars."""
    out = {}
    for bar in bars:
        dt = bar.date
        if isinstance(dt, str):                 # defensive: formatDate variants
            try:
                dt = datetime.strptime(dt[:19], '%Y%m%d  %H:%M:%S')
            except ValueError:
                continue
        if getattr(dt, 'tzinfo', None) is None:
            dt = dt.replace(tzinfo=EASTERN)
        else:
            dt = dt.astimezone(EASTERN)
        out[dt.replace(second=0, microsecond=0)] = bar
    return out


# --------------------------------------------------------------------------- #
# output layout
# --------------------------------------------------------------------------- #

def offset_label(offset):
    return f"m{offset:+03d}"


def bar_columns(before, after):
    cols = []
    for off in range(-before, after + 1):
        tag = offset_label(off)
        cols += [f"{tag}_open", f"{tag}_high", f"{tag}_low",
                 f"{tag}_close", f"{tag}_volume"]
    return cols


def fmt(value):
    if value is None:
        return ''
    return f"{value:g}" if isinstance(value, float) else str(value)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def main():
    ap = argparse.ArgumentParser(
        description="Attach 1-minute IBKR OHLCV bars around each PR timestamp.")
    ap.add_argument('input', help="input TSV (needs Symbol, ArrivalDate, Created)")
    ap.add_argument('-o', '--output',
                    help="output TSV (default: <input>_ohlc.tsv)")
    ap.add_argument('--minutes-before', type=int, default=5,
                    help="minutes of bars before the PR minute (default 5)")
    ap.add_argument('--minutes-after', type=int, default=60,
                    help="minutes of bars after the PR minute (default 60)")
    ap.add_argument('--stop-point', type=parse_stop_point, metavar='YYYY-MM-DD-HH-MM',
                    help="wall-clock ET deadline; exit cleanly when reached "
                         "(re-run to resume)")
    ap.add_argument('--time-column', default='Created',
                    help="column holding the PR time (default Created; "
                         "ArrivalTime is used as fallback)")
    ap.add_argument('--host', default=IB_HOST)
    ap.add_argument('--port', type=int, default=IB_PORT,
                    help=f"Gateway port (default {IB_PORT} = idle paper GW; "
                         f"4001 is LIVE)")
    ap.add_argument('--client-id', type=int, default=CLIENT_ID)
    ap.add_argument('--timeout', type=int, default=REQUEST_TIMEOUT,
                    help=f"per-request timeout seconds (default {REQUEST_TIMEOUT})")
    ap.add_argument('--pacing', type=float, default=PACING_SLEEP_SECONDS,
                    help="sleep between requests (default 2s)")
    ap.add_argument('--volume-scale', type=float, default=1.0,
                    help="multiply IB volume by this (default 1 = raw; IB "
                         "returns shares here, pass 100 if your GW sends lots)")
    ap.add_argument('--limit', type=int,
                    help="stop after this many rows fetched this run")
    ap.add_argument('--restart', action='store_true',
                    help="ignore any existing output and start from row 1")
    ap.add_argument('--no-sessions', action='store_true',
                    help="skip holiday/session handling")
    ap.add_argument('--log', nargs='?', const='AUTO', metavar='PATH',
                    help="write a verbatim log file (default logs/ohlc_fetch_<ts>.log)")
    ap.add_argument('-v', '--verbose', action='store_true',
                    help="also show debug lines on the console")
    args = ap.parse_args()

    log_path = args.log
    if log_path == 'AUTO':
        stamp = datetime.now(EASTERN).strftime('%Y-%m-%d_%H%M%S')
        log_path = os.path.join(os.path.dirname(os.path.abspath(args.input)),
                                'logs', f'ohlc_fetch_{stamp}.log')
    setup_logging(log_path, args.verbose)

    if args.minutes_before < 0 or args.minutes_after < 0:
        logging.error("--minutes-before/--minutes-after must be >= 0")
        return 2

    out_path = args.output
    if not out_path:
        stem, ext = os.path.splitext(args.input)
        out_path = f"{stem}_ohlc{ext or '.tsv'}"

    # ---- read input -------------------------------------------------------
    with open(args.input, newline='', encoding='utf-8') as fh:
        reader = csv.reader(fh, delimiter='\t')
        try:
            in_header = next(reader)
        except StopIteration:
            logging.error("input file is empty")
            return 2
        in_rows = [r for r in reader if any(c.strip() for c in r)]

    in_header = [h.strip() for h in in_header]
    for required in ('Symbol', 'ArrivalDate', args.time_column):
        if required not in in_header:
            logging.error(f"input is missing required column {required!r}; "
                          f"found: {in_header}")
            return 2
    idx = {name: i for i, name in enumerate(in_header)}
    sym_i, date_i, time_i = idx['Symbol'], idx['ArrivalDate'], idx[args.time_column]
    alt_time_i = idx.get('ArrivalTime')
    exch_i = idx.get('Exchange')

    out_header = in_header + META_COLS + bar_columns(args.minutes_before,
                                                     args.minutes_after)
    logging.info(f"Input:  {args.input} ({len(in_rows)} rows)")
    logging.info(f"Output: {out_path}")
    logging.info(f"Window: m{-args.minutes_before:+d} .. m{args.minutes_after:+d} "
                 f"({args.minutes_before + args.minutes_after + 1} bars/row, "
                 f"{len(out_header)} columns)")

    # ---- resume -----------------------------------------------------------
    done = 0
    if args.restart and os.path.exists(out_path):
        logging.info("--restart: existing output will be overwritten")
        os.remove(out_path)
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        with open(out_path, newline='', encoding='utf-8') as fh:
            rdr = csv.reader(fh, delimiter='\t')
            existing_header = next(rdr, None)
            existing = [r for r in rdr if any(c.strip() for c in r)]
        if existing_header != out_header:
            logging.error(
                "existing output header does not match the header this run would "
                "write (different --minutes-before/--minutes-after or a different "
                "input?). Refusing to append. Use --restart or -o <new path>.")
            return 2
        done = len(existing)
        if done > len(in_rows):
            logging.error(f"output already has {done} rows but the input only has "
                          f"{len(in_rows)} — mismatched files?")
            return 2
        if done and existing[-1][sym_i] != in_rows[done - 1][sym_i]:
            logging.error(
                f"resume check failed: output row {done} is "
                f"{existing[-1][sym_i]!r} but input row {done} is "
                f"{in_rows[done - 1][sym_i]!r}. The input changed between runs.")
            return 2
        logging.info(f"Resuming: {done} rows already present, "
                     f"{len(in_rows) - done} to go")

    if done >= len(in_rows):
        logging.info("Nothing to do — output is already complete.")
        return 0

    anchors_dates = []
    for row in in_rows:
        a = parse_anchor(row[date_i], row[time_i],
                         row[alt_time_i] if alt_time_i is not None and
                         alt_time_i < len(row) else None)
        if a:
            anchors_dates.append(a.date())
    sessions = None if args.no_sessions else load_sessions(anchors_dates)
    if sessions is not None:
        logging.info(f"NYSE session calendar loaded ({len(sessions)} sessions in range)")

    if args.stop_point and datetime.now(EASTERN) >= args.stop_point:
        logging.info(f"--stop-point {args.stop_point:%Y-%m-%d %H:%M} ET is already "
                     f"past — not connecting. {len(in_rows) - done} rows remain.")
        return 0

    # ---- connect ----------------------------------------------------------
    ib = IB()
    if not connect(ib, args.host, args.port, args.client_id):
        logging.error("could not connect to the IB Gateway — aborting "
                      "(nothing written, safe to re-run).")
        return 1

    new_file = not os.path.exists(out_path) or os.path.getsize(out_path) == 0
    out_fh = open(out_path, 'a', newline='', encoding='utf-8')
    writer = csv.writer(out_fh, delimiter='\t', lineterminator='\n')
    if new_file:
        writer.writerow(out_header)
        out_fh.flush()
        os.fsync(out_fh.fileno())

    watch = ErrorWatch(ib)
    contract_cache = {}
    consecutive_failures = 0
    counts = {}
    fetched_this_run = 0
    exit_code = 0

    try:
        for i in range(done, len(in_rows)):
            row = list(in_rows[i]) + [''] * (len(in_header) - len(in_rows[i]))
            symbol = row[sym_i].strip()
            rownum = i + 1

            if args.stop_point and datetime.now(EASTERN) >= args.stop_point:
                logging.info(f"--stop-point {args.stop_point:%Y-%m-%d %H:%M} ET reached "
                             f"at row {rownum} — stopping cleanly. Re-run to resume.")
                break
            if args.limit and fetched_this_run >= args.limit:
                logging.info(f"--limit {args.limit} reached — stopping cleanly.")
                break

            raw_anchor = parse_anchor(
                row[date_i], row[time_i],
                row[alt_time_i] if alt_time_i is not None else None)
            status, note, anchor, bars_returned = None, None, None, 0

            if raw_anchor is None:
                status = 'bad_time'
                logging.warning(f"[{rownum}/{len(in_rows)}] {symbol}: unparseable "
                                f"{args.time_column}={row[time_i]!r} / "
                                f"ArrivalDate={row[date_i]!r} — blank row.")
            elif exch_i is not None and row[exch_i].strip() and \
                    row[exch_i].strip().upper() not in US_EXCHANGES:
                status = 'non_us'
                logging.warning(f"[{rownum}/{len(in_rows)}] {symbol}: exchange "
                                f"{row[exch_i]!r} outside the US subscription — skipped.")
            else:
                anchor, note = resolve_anchor(raw_anchor, sessions)
                if anchor is None:
                    status = 'holiday'
                    logging.warning(f"[{rownum}/{len(in_rows)}] {symbol}: no session "
                                    f"found near {raw_anchor:%Y-%m-%d} — blank row.")

            bar_by_min = {}
            if status is None:
                if note:
                    logging.info(f"[{rownum}/{len(in_rows)}] {symbol}: anchor "
                                 f"{raw_anchor:%Y-%m-%d %H:%M} -> {anchor:%Y-%m-%d %H:%M} ET "
                                 f"({note})")
                contract = qualify(ib, symbol, contract_cache)
                if contract is None:
                    status = 'no_contract'
                else:
                    start_et = anchor - timedelta(minutes=args.minutes_before)
                    end_et = anchor + timedelta(minutes=args.minutes_after + 1)
                    t0 = time.time()
                    bars, status = fetch_bars(ib, contract, start_et, end_et,
                                              args.timeout, args.host, args.port,
                                              args.client_id, watch)
                    elapsed = time.time() - t0
                    if status == 'failure':
                        consecutive_failures += 1
                        logging.error(
                            f"[{rownum}/{len(in_rows)}] {symbol}: fetch FAILED "
                            f"({consecutive_failures} in a row). Row not written — "
                            f"re-run to retry from here.")
                        if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                            logging.error(
                                f"{CONSECUTIVE_FAILURE_ABORT} consecutive failures — "
                                f"aborting. Check the Gateway (HMDS reachable? live "
                                f"stack contending on port {args.port}?).")
                            exit_code = 1
                            break
                        time.sleep(args.pacing)
                        continue
                    consecutive_failures = 0
                    if bars:
                        bar_by_min = index_bars(bars)
                        window = {m for m in bar_by_min
                                  if start_et <= m <= anchor + timedelta(minutes=args.minutes_after)}
                        bars_returned = len(window)
                        expected = args.minutes_before + args.minutes_after + 1
                        status = 'ok' if bars_returned else 'nodata'
                        logging.info(
                            f"[{rownum}/{len(in_rows)}] {symbol} "
                            f"{anchor:%Y-%m-%d %H:%M} ET: {bars_returned}/{expected} "
                            f"bars in window ({len(bars)} raw, {elapsed:.1f}s) "
                            f"status={status}")
                    else:
                        logging.info(f"[{rownum}/{len(in_rows)}] {symbol}: no bars "
                                     f"({elapsed:.1f}s) status={status}")

            # ---- assemble the row ----
            cells = [anchor.strftime('%Y-%m-%d %H:%M') if anchor else '',
                     status, str(bars_returned)]
            for off in range(-args.minutes_before, args.minutes_after + 1):
                bar = bar_by_min.get(anchor + timedelta(minutes=off)) if anchor else None
                if bar is None:
                    cells += ['', '', '', '', '']
                else:
                    vol = bar.volume
                    if vol is not None and vol >= 0:
                        vol = vol * args.volume_scale
                    else:
                        vol = None
                    cells += [fmt(bar.open), fmt(bar.high), fmt(bar.low),
                              fmt(bar.close), fmt(vol)]

            writer.writerow(row + cells)
            out_fh.flush()
            os.fsync(out_fh.fileno())
            counts[status] = counts.get(status, 0) + 1
            fetched_this_run += 1

            if i < len(in_rows) - 1:
                ib.sleep(args.pacing)
    except KeyboardInterrupt:
        logging.warning("Interrupted — output is flushed up to the last completed "
                        "row; re-run to resume.")
        exit_code = 130
    finally:
        out_fh.close()
        try:
            ib.disconnect()
        except Exception:
            pass

    total_done = done + fetched_this_run
    logging.info(f"Wrote {fetched_this_run} rows this run "
                 f"({total_done}/{len(in_rows)} complete) -> {out_path}")
    if counts:
        logging.info("Status breakdown: " +
                     ', '.join(f"{k}={v}" for k, v in sorted(counts.items())))
    if total_done < len(in_rows):
        logging.info(f"{len(in_rows) - total_done} rows remain — re-run the same "
                     f"command to continue.")
    return exit_code


if __name__ == '__main__':
    sys.exit(main())
