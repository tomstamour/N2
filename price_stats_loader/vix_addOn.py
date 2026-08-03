"""Enrich a news/PR-stats TSV with historical VIX index values.

Adds six columns keyed on each row's ArrivalDate:

    VIX_PrevClose   close of the last VIX trading day STRICTLY BEFORE ArrivalDate.
                    Known before the market opens on the PR day, so it is the
                    lookahead-free column to use as a predictor.
    VIX_Open        ArrivalDate's own VIX open  (known at 09:30, no lookahead)
    VIX_High        ArrivalDate's own VIX high  (LOOKAHEAD - research only)
    VIX_Low         ArrivalDate's own VIX low   (LOOKAHEAD - research only)
    VIX_Close       ArrivalDate's own VIX close (LOOKAHEAD - research only)
    VIX_Chg1D_pct   (VIX_PrevClose - close of the day before it) / that close * 100,
                    i.e. how much fear moved INTO the PR day. Also lookahead-free.

Source: the official CBOE daily history CSV (free, no key, OHLC since 1990):
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
A Yahoo Finance ^VIX fallback is available via --source yahoo. Stooq is NOT
supported: as of 2026-07 it serves a JavaScript proof-of-work challenge page
instead of the CSV.

The history is cached to cache/vix_history.csv next to this script and reused
until it is older than --max-cache-age-hours (default 12), so reruns work
offline. --refresh forces a download, --no-network forbids one.

Output goes to <input_stem>_vix.tsv (input is never modified).

Usage:
    /home/tom/venv/bin/python vix_addOn.py -i inputs/news_output_2026-07-12.tsv
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
DEFAULT_CACHE = SCRIPT_DIR / 'cache' / 'vix_history.csv'

CBOE_URL = 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv'
YAHOO_URL = ('https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX'
             '?period1=0&period2={end}&interval=1d')
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64)'

VIX_COLUMNS = ['VIX_PrevClose', 'VIX_Open', 'VIX_High', 'VIX_Low',
               'VIX_Close', 'VIX_Chg1D_pct']

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def fetch_cboe(timeout: int) -> list:
    """Return [(date, open, high, low, close)] from the CBOE daily history CSV."""
    text = _get(CBOE_URL, timeout).decode('utf-8', 'replace')
    rows = []
    reader = csv.reader(text.splitlines())
    header = next(reader, None)
    if not header or header[0].strip().upper() != 'DATE':
        raise ValueError(f'unexpected CBOE header: {header!r} '
                         '(the endpoint may be serving an error page)')
    for fields in reader:
        if len(fields) < 5 or not fields[0].strip():
            continue
        try:
            d = datetime.strptime(fields[0].strip(), '%m/%d/%Y').date()
            o, h, l, c = (float(x) for x in fields[1:5])
        except ValueError:
            continue
        rows.append((d, o, h, l, c))
    return rows


def fetch_yahoo(timeout: int) -> list:
    """Fallback source: Yahoo Finance ^VIX daily chart JSON."""
    url = YAHOO_URL.format(end=int(time.time()) + 86400)
    payload = json.loads(_get(url, timeout).decode('utf-8', 'replace'))
    result = payload['chart']['result'][0]
    stamps = result['timestamp']
    q = result['indicators']['quote'][0]
    rows = []
    for i, ts in enumerate(stamps):
        o, h, l, c = q['open'][i], q['high'][i], q['low'][i], q['close'][i]
        if None in (o, h, l, c):
            continue
        # Yahoo stamps each ^VIX daily bar at 07:00 UTC on the session date, so
        # the UTC date is the session date; converting to any local timezone
        # west of UTC would shift the whole series back a day.
        d = datetime.fromtimestamp(ts, timezone.utc).date()
        rows.append((d, float(o), float(h), float(l), float(c)))
    return rows


FETCHERS = {'cboe': fetch_cboe, 'yahoo': fetch_yahoo}


def write_cache(rows: list, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + '.tmp')
    with open(tmp, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['DATE', 'OPEN', 'HIGH', 'LOW', 'CLOSE'])
        for d, o, h, l, c in rows:
            w.writerow([d.isoformat(), o, h, l, c])
    os.replace(tmp, cache_path)


def read_cache(cache_path: Path) -> list:
    rows = []
    with open(cache_path, newline='') as f:
        reader = csv.reader(f)
        next(reader, None)
        for fields in reader:
            if len(fields) < 5:
                continue
            rows.append((date.fromisoformat(fields[0]),
                         float(fields[1]), float(fields[2]),
                         float(fields[3]), float(fields[4])))
    return rows


def load_history(source: str, cache_path: Path, max_age_hours: float,
                 refresh: bool, no_network: bool, timeout: int) -> dict:
    """Return {date: (open, high, low, close)}, using the cache when it is fresh."""
    cache_ok = cache_path.is_file()
    age_h = ((time.time() - cache_path.stat().st_mtime) / 3600.0
             if cache_ok else float('inf'))

    if cache_ok and not refresh and age_h <= max_age_hours:
        logging.info(f'Using cached VIX history ({cache_path}, {age_h:.1f}h old).')
        rows = read_cache(cache_path)
    elif no_network:
        if not cache_ok:
            raise SystemExit(f'--no-network but no cache at {cache_path}')
        logging.warning(f'--no-network: using stale cache ({age_h:.1f}h old).')
        rows = read_cache(cache_path)
    else:
        try:
            logging.info(f'Downloading VIX history from {source}...')
            rows = FETCHERS[source](timeout)
            write_cache(rows, cache_path)
            logging.info(f'Cached {len(rows)} VIX days to {cache_path}.')
        except Exception as exc:
            if not cache_ok:
                raise SystemExit(f'VIX download from {source} failed and no '
                                 f'cache is available: {exc}')
            logging.error(f'Download failed ({exc}); falling back to cache '
                          f'({age_h:.1f}h old).')
            rows = read_cache(cache_path)

    hist = {d: (o, h, l, c) for d, o, h, l, c in rows}
    if not hist:
        raise SystemExit('VIX history is empty - nothing to join on.')
    logging.info(f'VIX history: {len(hist)} days, '
                 f'{min(hist)} .. {max(hist)}.')
    return hist


# --------------------------------------------------------------------------- #
# joining
# --------------------------------------------------------------------------- #

def parse_arrival_date(value: str):
    """Accept YYYY-MM-DD, or a full timestamp whose first token is that date."""
    if not value:
        return None
    token = value.strip().split()[0].split('T')[0]
    try:
        return date.fromisoformat(token)
    except ValueError:
        return None


def previous_vix_days(hist: dict, target: date, count: int, max_back: int):
    """The `count` most recent VIX days strictly before `target`, newest first.

    Walking back day-by-day (rather than indexing a sorted list) keeps holidays
    and weekends handled without a market calendar, and max_back bounds the walk
    so a date past the end of history fails loudly instead of scanning to 1990.
    """
    found = []
    d = target - timedelta(days=1)
    for _ in range(max_back):
        if d in hist:
            found.append((d, hist[d]))
            if len(found) == count:
                return found
        d -= timedelta(days=1)
    return found


def fmt(x) -> str:
    return '' if x is None else f'{x:.2f}'


def enrich(rows: list, header: list, hist: dict, date_column: str,
           max_back: int, overwrite: bool) -> tuple:
    """Return (out_header, out_rows, stats)."""
    out_header = list(header)
    for col in VIX_COLUMNS:
        if col not in out_header:
            out_header.append(col)

    newest = max(hist)
    stats = {'filled': 0, 'kept': 0, 'no_date': 0, 'no_prev': 0, 'no_sameday': 0}
    out_rows = []

    for row in rows:
        rec = dict(row)
        already = any(rec.get(col, '') != '' for col in VIX_COLUMNS)
        if already and not overwrite:
            stats['kept'] += 1
            out_rows.append(rec)
            continue

        for col in VIX_COLUMNS:
            rec[col] = ''

        target = parse_arrival_date(rec.get(date_column, ''))
        if target is None:
            stats['no_date'] += 1
            out_rows.append(rec)
            continue

        # Same-day OHLC: only present if ArrivalDate was itself a trading day.
        same = hist.get(target)
        if same:
            rec['VIX_Open'], rec['VIX_High'] = fmt(same[0]), fmt(same[1])
            rec['VIX_Low'], rec['VIX_Close'] = fmt(same[2]), fmt(same[3])
        else:
            stats['no_sameday'] += 1
            if target > newest:
                logging.warning(f'{target} is beyond the end of VIX history '
                                f'({newest}) - same-day columns left blank.')

        prev = previous_vix_days(hist, target, 2, max_back)
        if prev:
            prev_close = prev[0][1][3]
            rec['VIX_PrevClose'] = fmt(prev_close)
            if len(prev) == 2:
                before = prev[1][1][3]
                if before:
                    rec['VIX_Chg1D_pct'] = f'{(prev_close - before) / before * 100:.2f}'
            stats['filled'] += 1
        else:
            stats['no_prev'] += 1
            logging.warning(f'No VIX day found within {max_back} days before '
                            f'{target} - row left blank.')

        out_rows.append(rec)

    return out_header, out_rows, stats


# --------------------------------------------------------------------------- #

def read_tsv_flexible(path: str) -> tuple:
    """Read a TSV where some rows may have more fields than the header declares."""
    with open(path, newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader, None)
        if header is None:
            raise SystemExit(f'{path} is empty.')
        rows = [dict(zip(header, fields)) for fields in reader]
    return header, rows


def write_tsv(path: str, header: list, rows: list) -> None:
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=header, delimiter='\t',
                           extrasaction='ignore', restval='')
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news/PR-stats TSV with historical VIX values: '
            'VIX_PrevClose, VIX_Open/High/Low/Close for the ArrivalDate, and '
            'VIX_Chg1D_pct. Data comes from the free CBOE daily history CSV '
            '(Yahoo fallback) and is cached locally. '
            'Output is written to <input_stem>_vix.tsv; the input is untouched.\n\n'
            'Lookahead note: VIX_PrevClose, VIX_Chg1D_pct and VIX_Open are known '
            'at or before the PR; VIX_High/Low/Close are not - use them for '
            'description only, never as model features.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', '-i', required=True, dest='input_file',
                        metavar='FILE', help='Input .tsv (needs an ArrivalDate column).')
    parser.add_argument('--output', '-o', dest='output_file', metavar='FILE',
                        help='Output path (default: <input_stem>_vix.tsv).')
    parser.add_argument('--date-column', default='ArrivalDate', metavar='COL',
                        help='Column holding the row date (default: ArrivalDate).')
    parser.add_argument('--source', choices=sorted(FETCHERS), default='cboe',
                        help='Download source (default: cboe). Stooq is unusable: '
                             'it serves a JS proof-of-work page instead of CSV.')
    parser.add_argument('--cache-file', default=str(DEFAULT_CACHE), metavar='FILE',
                        help=f'History cache path (default: {DEFAULT_CACHE}).')
    parser.add_argument('--max-cache-age-hours', type=float, default=12.0,
                        metavar='H', help='Re-download if the cache is older (default: 12).')
    parser.add_argument('--refresh', action='store_true',
                        help='Force a download even if the cache is fresh.')
    parser.add_argument('--no-network', action='store_true',
                        help='Never download; use the cache even if stale.')
    parser.add_argument('--timeout', type=int, default=30, metavar='SEC',
                        help='HTTP timeout in seconds (default: 30).')
    parser.add_argument('--max-lookback-days', type=int, default=10, metavar='N',
                        help='Calendar days to walk back for the prior VIX '
                             'session (default: 10, covers holiday runs).')
    parser.add_argument('--overwrite', action='store_true',
                        help='Recompute rows that already carry VIX values '
                             '(default: leave them as they are).')
    args = parser.parse_args()

    if not os.path.isfile(args.input_file):
        raise SystemExit(f'Input not found: {args.input_file}')

    header, rows = read_tsv_flexible(args.input_file)
    if args.date_column not in header:
        raise SystemExit(f'Column {args.date_column!r} not in {args.input_file}. '
                         f'Header: {header}')
    logging.info(f'Read {len(rows)} rows from {args.input_file}.')

    hist = load_history(args.source, Path(args.cache_file),
                        args.max_cache_age_hours, args.refresh,
                        args.no_network, args.timeout)

    out_header, out_rows, stats = enrich(rows, header, hist, args.date_column,
                                         args.max_lookback_days, args.overwrite)

    out_path = args.output_file
    if not out_path:
        root, ext = os.path.splitext(args.input_file)
        out_path = f'{root}_vix{ext or ".tsv"}'
    write_tsv(out_path, out_header, out_rows)

    logging.info(f'Filled {stats["filled"]} rows, kept {stats["kept"]} existing, '
                 f'{stats["no_date"]} unparseable dates, '
                 f'{stats["no_prev"]} without a prior VIX day, '
                 f'{stats["no_sameday"]} without same-day VIX (non-trading day).')
    logging.info(f'Wrote {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
