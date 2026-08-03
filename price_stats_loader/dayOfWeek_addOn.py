"""Enrich a news/PR-stats TSV with a DayOfWeek column.

Adds one integer column:

    DayOfWeek   ISO weekday of the row's arrival date: 1 = Monday .. 7 = Sunday.

This is the RAW CALENDAR weekday of the arrival date - after-hours and weekend
PRs are NOT rolled forward to the next trading session, so a Friday 20:35 PR is
5 and a Sunday PR is 7.

Two TSV schemas live in this folder and both are handled:

    new (Orchestrator5.x)  ArrivalDate = 2026-07-12, ArrivalTime is time-only
    old (news_output_*)    no ArrivalDate column at all; the full timestamp
                           2026-05-03 20:35:00 sits in ArrivalTime

The source column is auto-detected (ArrivalDate, else ArrivalTime) unless
--date-column says otherwise. When ArrivalDate is the chosen column but a
particular row's cell is blank or unparseable, that row falls back to
ArrivalTime, which covers the historical rows whose date leaked into
ArrivalTime through the old column-split bug.

Output goes to <input_stem>_dow.tsv (input is never modified).

Usage:
    /home/tom/venv/bin/python dayOfWeek_addOn.py -i inputs/news_output_2026-07-12.tsv
"""

import argparse
import logging
import os
import sys
from collections import Counter

# Reuse the flexible TSV reader/writer and the date parser rather than adding a
# third copy of each; parse_arrival_date already handles both schemas.
from vix_addOn import read_tsv_flexible, write_tsv, parse_arrival_date

DAY_COLUMN = 'DayOfWeek'
# Auto-detection order. ArrivalTime is last because in the new schema it is
# time-only and useless on its own.
CANDIDATE_COLUMNS = ['ArrivalDate', 'ArrivalTime']
FALLBACK_COLUMN = 'ArrivalTime'

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


def enrich(rows: list, header: list, date_column: str, overwrite: bool) -> tuple:
    """Return (out_header, out_rows, stats, histogram)."""
    out_header = list(header)
    if DAY_COLUMN not in out_header:
        out_header.append(DAY_COLUMN)

    # Only worth falling back when the primary column is a different one that
    # actually exists in this file.
    fallback = (FALLBACK_COLUMN
                if date_column != FALLBACK_COLUMN and FALLBACK_COLUMN in header
                else None)

    stats = Counter()
    histogram = Counter()
    out_rows = []

    for line_no, row in enumerate(rows, start=2):
        rec = dict(row)
        if rec.get(DAY_COLUMN, '') != '' and not overwrite:
            stats['kept'] += 1
            out_rows.append(rec)
            continue

        parsed = row_date(rec, date_column, fallback)
        if parsed is None:
            rec[DAY_COLUMN] = ''
            stats['unparseable'] += 1
            logging.warning(f'Line {line_no}: no usable date in '
                            f'{date_column!r} - DayOfWeek left blank.')
        else:
            dow = parsed.isoweekday()  # natively 1=Mon .. 7=Sun
            rec[DAY_COLUMN] = str(dow)
            histogram[dow] += 1
            stats['filled'] += 1

        out_rows.append(rec)

    return out_header, out_rows, stats, histogram


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news/PR-stats TSV with a DayOfWeek column: the ISO '
            'weekday of the row\'s arrival date, 1 = Monday .. 7 = Sunday. '
            'The date column is auto-detected (ArrivalDate, else ArrivalTime) '
            'so both the new and old TSV schemas work. Weekend and after-hours '
            'PRs keep their real calendar weekday - nothing is rolled forward '
            'to the next trading session. Output is written to '
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
                        help='Recompute rows that already carry a DayOfWeek '
                             '(default: leave them as they are).')
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
    logging.info(f'Wrote {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
