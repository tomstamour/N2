"""Enrich a news/PR-stats TSV with calendar-position columns.

Adds three columns:

    DayOfWeek    ISO weekday of the row's arrival date: 1 = Monday .. 7 = Sunday.
    DayOfMonth   Calendar day of the month of the arrival date: 1 .. 31.
    TimeToOpen   Hours between the PR's arrival and the 04:00 ET ETH open of the
                 session it lands in. Negative = the market has not opened yet,
                 positive = hours already elapsed since that open, 0.0 = exactly
                 at 04:00. Two decimals.

DayOfWeek and DayOfMonth are the RAW CALENDAR fields of the arrival date -
after-hours and weekend PRs are NOT rolled forward to the next trading session,
so a Friday 20:35 PR is 5 and a Sunday PR is 7.

TimeToOpen is the one column that IS session-aware, because "how long until the
market opens" is meaningless without the exchange calendar:

    reference session = the arrival date itself when that date is an XNYS
                        session AND the arrival time is before 20:00 (the ETH
                        close); otherwise the next session on the calendar.

    TimeToOpen        = arrival - 04:00 ET of the reference session, in hours

so the sign falls out of the arithmetic rather than being special-cased:

    09:00:00 on a session day          ->   +5.00  (5 h into the ETH session)
    16:30:00 on a session day          ->  +12.50  (post-RTH, still today)
    02:00:00 on a session day          ->   -2.00  (pre-open, same session)
    21:00:00 Tuesday                   ->   -7.00  (past 20:00 -> Wednesday)
    21:00:00 Saturday                  ->  -31.00  (-> Monday)
    17:30:00 Sunday, Monday a holiday  ->  -34.50  (-> Tuesday)

The 20:00 cutoff is fixed. Early-close half-days (the day before July 4, Black
Friday, Christmas Eve) end ETH at 17:00 and are NOT special-cased: a 17:30 PR on
one of those still measures against that day's own open. No such day is in the
2026-05-04..08-28 corpus.

The calendar comes from market_state_calculator.Sessions - the same XNYS index
every other add-on anchors on - so holidays are handled identically here.
Note this is NOT a --days-prior anchor: nothing here looks BACKWARD a session.
Reference-session lookup is forward-only, and the script deliberately has no
--days-prior flag.

Two TSV schemas live in this folder and both are handled:

    new (Orchestrator5.x)  ArrivalDate = 2026-07-12, ArrivalTime is time-only
    old (news_output_*)    no ArrivalDate column at all; the full timestamp
                           2026-05-03 20:35:00 sits in ArrivalTime

The source column is auto-detected (ArrivalDate, else ArrivalTime) unless
--date-column says otherwise. When ArrivalDate is the chosen column but a
particular row's cell is blank or unparseable, that row falls back to
ArrivalTime, which covers the historical rows whose date leaked into
ArrivalTime through the old column-split bug. The clock time always comes from
ArrivalTime; without it TimeToOpen is left blank while the two calendar columns
are still filled.

Output goes to <input_stem>_dow.tsv (input is never modified).

Usage:
    /home/tom/venv/bin/python dayOfWeek_addOn.py -i inputs/news_output_2026-07-12.tsv
"""

import argparse
import logging
import os
import sys
from bisect import bisect_left
from collections import Counter
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Reuse the flexible TSV reader/writer and the date parser rather than adding a
# third copy of each; parse_arrival_date already handles both schemas.
from vix_addOn import read_tsv_flexible, write_tsv, parse_arrival_date

# The XNYS session index is imported, not rebuilt: a local mcal call here would
# be a second calendar that could drift from the one msc/vix/mcap anchor on.
_MSC_DIR = Path(__file__).resolve().parent.parent / 'tools' / 'market_state'
if str(_MSC_DIR) not in sys.path:
    sys.path.insert(0, str(_MSC_DIR))
from market_state_calculator import Sessions          # noqa: E402

DAY_COLUMN = 'DayOfWeek'
DOM_COLUMN = 'DayOfMonth'
TTO_COLUMN = 'TimeToOpen'
NEW_COLUMNS = [DAY_COLUMN, DOM_COLUMN, TTO_COLUMN]

# ETH runs 04:00-20:00 ET. OPEN is what TimeToOpen measures from; CUTOFF is the
# moment a PR stops belonging to today's session and starts counting down to
# tomorrow's open.
ETH_OPEN = time(4, 0)
ETH_CUTOFF = time(20, 0)

# Auto-detection order. ArrivalTime is last because in the new schema it is
# time-only and useless on its own.
CANDIDATE_COLUMNS = ['ArrivalDate', 'ArrivalTime']
FALLBACK_COLUMN = 'ArrivalTime'
TIME_COLUMN = 'ArrivalTime'

# Padding around the corpus dates so the forward lookup for a PR on the last day
# still finds a next session, and Sessions never starts mid-range.
CALENDAR_PAD_DAYS = 90

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


def resolve_date_column(header: list, requested: str) -> str:
    """Pick the column holding each row's date, or exit with a clear message."""
    if requested:
        if requested not in header:
            raise SystemExit(f'Column {requested!r} not in the input. '
                             f'Header: {header}')
        return requested
    for col in CANDIDATE_COLUMNS:
        if col in header:
            return col
    raise SystemExit(f'None of {CANDIDATE_COLUMNS} found in the input; pass '
                     f'--date-column. Header: {header}')


def row_date(row: dict, date_column: str, fallback_column: str):
    """Date for one row, falling back to ArrivalTime when the primary is empty."""
    parsed = parse_arrival_date(row.get(date_column, ''))
    if parsed is None and fallback_column:
        parsed = parse_arrival_date(row.get(fallback_column, ''))
    return parsed


def parse_arrival_time(value: str):
    """Clock time out of an ArrivalTime cell, or None.

    Handles all three shapes the two schemas produce: '08:32:15',
    '10:00:04.801' (3421 of 4804 rows in the current corpus carry milliseconds)
    and the old '2026-05-03 20:35:00' where the date shares the cell. Fractional
    seconds are dropped - TimeToOpen is rounded to 2dp (36 s) anyway.
    """
    if not value:
        return None
    token = value.strip().replace('T', ' ').split()[-1].split('.')[0]
    try:
        return time.fromisoformat(token)
    except ValueError:
        return None


def reference_session(d: date, t: time, sessions: Sessions):
    """The session whose 04:00 ETH open TimeToOpen is measured against.

    The arrival date itself when it is a session and the clock has not yet
    reached the 20:00 ETH close; otherwise the next session strictly after it.
    A pre-04:00 arrival on a session day still references that same day - it is
    simply early, which is what the negative sign says.
    """
    i = bisect_left(sessions.days, d)
    on_a_session = i < len(sessions.days) and sessions.days[i] == d
    if on_a_session and t < ETH_CUTOFF:
        return sessions.days[i]
    j = i + 1 if on_a_session else i
    return sessions.days[j] if j < len(sessions.days) else None


def time_to_open(d: date, t: time, sessions: Sessions):
    """Hours from the reference session's 04:00 open, or None if unresolvable."""
    ref = reference_session(d, t, sessions)
    if ref is None:
        return None
    delta = datetime.combine(d, t) - datetime.combine(ref, ETH_OPEN)
    hours = round(delta.total_seconds() / 3600.0, 2)
    # round() can hand back -0.0 for an arrival a few hundred ms before 04:00,
    # which formats as the nonsense '-0.00'.
    return hours + 0.0 if hours else 0.0


def build_sessions(rows: list, date_column: str, fallback: str) -> Sessions:
    """XNYS index spanning the corpus dates with room to look one session ahead."""
    dates = [d for d in (row_date(r, date_column, fallback) for r in rows)
             if d is not None]
    if not dates:
        raise SystemExit(f'No parseable date in {date_column!r} anywhere in the '
                         f'input - nothing to anchor the calendar on.')
    return Sessions(min(dates) - timedelta(days=CALENDAR_PAD_DAYS),
                    max(dates) + timedelta(days=CALENDAR_PAD_DAYS))


def enrich(rows: list, header: list, date_column: str, overwrite: bool) -> tuple:
    """Return (out_header, out_rows, stats, histogram)."""
    out_header = list(header)
    for col in NEW_COLUMNS:
        if col not in out_header:
            out_header.append(col)

    # Only worth falling back when the primary column is a different one that
    # actually exists in this file.
    fallback = (FALLBACK_COLUMN
                if date_column != FALLBACK_COLUMN and FALLBACK_COLUMN in header
                else None)
    has_time = TIME_COLUMN in header
    if not has_time:
        logging.warning(f'No {TIME_COLUMN!r} column - {TTO_COLUMN} will be blank '
                        f'on every row.')

    sessions = build_sessions(rows, date_column, fallback)
    logging.info(f'XNYS calendar {sessions.first} .. {sessions.last} '
                 f'({len(sessions.days)} sessions).')

    stats = Counter()
    histogram = Counter()
    out_rows = []

    for line_no, row in enumerate(rows, start=2):
        rec = dict(row)
        # One decision for the whole group: a row is "already done" only when
        # every new column is present, so re-running after this script grew a
        # column backfills it instead of skipping the row.
        if not overwrite and all(rec.get(c, '') != '' for c in NEW_COLUMNS):
            stats['kept'] += 1
            out_rows.append(rec)
            continue

        parsed = row_date(rec, date_column, fallback)
        if parsed is None:
            rec[DAY_COLUMN] = ''
            rec[DOM_COLUMN] = ''
            rec[TTO_COLUMN] = ''
            stats['unparseable'] += 1
            logging.warning(f'Line {line_no}: no usable date in '
                            f'{date_column!r} - {", ".join(NEW_COLUMNS)} left '
                            f'blank.')
            out_rows.append(rec)
            continue

        dow = parsed.isoweekday()  # natively 1=Mon .. 7=Sun
        rec[DAY_COLUMN] = str(dow)
        rec[DOM_COLUMN] = str(parsed.day)
        histogram[dow] += 1
        stats['filled'] += 1

        clock = parse_arrival_time(rec.get(TIME_COLUMN, '')) if has_time else None
        hours = None if clock is None else time_to_open(parsed, clock, sessions)

        if clock is None:
            rec[TTO_COLUMN] = ''
            stats['no_time'] += 1
            if has_time:
                logging.warning(f'Line {line_no}: unparseable {TIME_COLUMN} '
                                f'{rec.get(TIME_COLUMN, "")!r} - {TTO_COLUMN} '
                                f'left blank.')
        elif hours is None:
            rec[TTO_COLUMN] = ''
            stats['no_session'] += 1
            logging.warning(f'Line {line_no}: no XNYS session at or after '
                            f'{parsed} within the calendar '
                            f'({sessions.first}..{sessions.last}) - '
                            f'{TTO_COLUMN} left blank.')
        else:
            rec[TTO_COLUMN] = f'{hours:.2f}'
            stats['tto_filled'] += 1
            stats['tto_pre_open' if hours < 0 else 'tto_in_session'] += 1

        out_rows.append(rec)

    return out_header, out_rows, stats, histogram


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news/PR-stats TSV with DayOfWeek (ISO weekday of the '
            'arrival date, 1 = Monday .. 7 = Sunday), DayOfMonth (1 .. 31) and '
            'TimeToOpen (hours from the 04:00 ET ETH open of the session the PR '
            'lands in; negative before the open, positive after). The date '
            'column is auto-detected (ArrivalDate, else ArrivalTime) so both '
            'the new and old TSV schemas work. DayOfWeek/DayOfMonth are raw '
            'calendar fields - weekend and after-hours PRs keep their real '
            'date and are NOT rolled forward. TimeToOpen is the only '
            'session-aware column: a PR at or after 20:00, or on a non-session '
            'day, counts down to the next XNYS open. Output is written to '
            '<input_stem>_dow.tsv; the input is untouched.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', '-i', required=True, dest='input_file',
                        metavar='FILE', help='Input .tsv file.')
    parser.add_argument('--output', '-o', dest='output_file', metavar='FILE',
                        help='Output path (default: <input_stem>_dow.tsv).')
    parser.add_argument('--date-column', default='', metavar='COL',
                        help='Column holding the row date. Default: '
                             'auto-detect ArrivalDate, else ArrivalTime.')
    parser.add_argument('--overwrite', action='store_true',
                        help='Recompute rows that already carry all of '
                             'DayOfWeek/DayOfMonth/TimeToOpen (default: leave '
                             'them as they are; a row missing any one of the '
                             'three is recomputed either way).')
    args = parser.parse_args()

    if not os.path.isfile(args.input_file):
        raise SystemExit(f'Input not found: {args.input_file}')

    header, rows = read_tsv_flexible(args.input_file)
    date_column = resolve_date_column(header, args.date_column)
    logging.info(f'Read {len(rows)} rows from {args.input_file}; '
                 f'reading dates from {date_column!r}'
                 f'{"" if args.date_column else " (auto-detected)"}.')

    out_header, out_rows, stats, histogram = enrich(rows, header, date_column,
                                                    args.overwrite)

    out_path = args.output_file
    if not out_path:
        root, ext = os.path.splitext(args.input_file)
        out_path = f'{root}_dow{ext or ".tsv"}'
    write_tsv(out_path, out_header, out_rows)

    spread = ', '.join(f'{d}:{histogram[d]}' for d in range(1, 8) if histogram[d])
    logging.info(f'Filled {stats["filled"]} rows, kept {stats["kept"]} existing, '
                 f'{stats["unparseable"]} without a usable date.')
    logging.info(f'DayOfWeek spread (1=Mon..7=Sun): {spread or "none"}')
    logging.info(f'TimeToOpen: {stats["tto_filled"]} filled '
                 f'({stats["tto_in_session"]} at or after the 04:00 open, '
                 f'{stats["tto_pre_open"]} counting down to the next open), '
                 f'{stats["no_time"]} without a usable time, '
                 f'{stats["no_session"]} with no session ahead.')
    logging.info(f'Wrote {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
