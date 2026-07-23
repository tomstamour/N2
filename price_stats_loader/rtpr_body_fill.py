#!/usr/bin/env python3
"""Fill NOT_FOUND body cells in a PR-stats TSV from the RTPR REST API.

`json_bodyColumn_addON.py` fills the `body` column by matching each row's `ID`
against local per-article JSONs under orchestrator3/outputs/. For rows whose
article JSON was never written to disk (e.g. the 2026-06-23 -> 07-01 pipeline
gap), it leaves the marker `NOT_FOUND`.

This companion tool recovers those bodies from the RTPR REST API, which now
serves ~a year of history. The API no longer exposes the internal `nGNX...` id,
so rows are matched to articles by:

  1. ticker  == the row's `Symbol` (also tries the extra tickers in `Tickers`);
  2. headline == the article `title` (whitespace-collapsed, case-insensitive);
  3. created-timestamp as tiebreaker/fallback: the row's ET `Created` time-of-day
     shifted to UTC equals the article's UTC `created` to the (sub)second.

Only rows where `body == NOT_FOUND` are touched; already-filled rows pass through
unchanged. Output: <input_stem>_rtprfilled.tsv in --output-dir.
"""

import argparse
import ast
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

from json_bodyColumn_addON import NOT_FOUND, collapse_and_truncate

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / 'outputs'
DEFAULT_API_KEY_FILE = SCRIPT_DIR.parent / 'historicalNewsFetch' / 'RTPR_API-Key.txt'

RTPR_BASE_URL = 'https://api.rtpr.io'
# Eastern (EDT, June) -> UTC offset. The window this tool targets is all in DST.
ET_TO_UTC_HOURS = 4
# Timestamp tiebreaker tolerance (row Created vs article created), seconds.
TS_TOLERANCE_SEC = 2.0

MAX_RETRIES = 5
BASE_RETRY_DELAY = 2  # seconds
THROTTLE_SEC = 1.05   # 60 req/min limit -> stay just under one call per second


def load_api_key(path):
    """Return the RTPR key: the first non-empty line after a `Key:` marker."""
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    for i, line in enumerate(lines):
        if line.strip().rstrip(':').lower() == 'key':
            for follow in lines[i + 1:]:
                if follow.strip():
                    return follow.strip()
    raise ValueError(f"No 'Key:' entry found in {path}")


def normalize(text):
    """Whitespace-collapsed, lowercased key for headline<->title comparison."""
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()


def fetch_articles(ticker, api_key, limit, retry_count=0):
    """GET /articles/{ticker}?limit=N with 429 exponential backoff.

    Returns the list of article dicts (possibly empty). None means a hard,
    non-retryable failure (auth/forbidden) where retrying other tickers is moot.
    """
    url = f"{RTPR_BASE_URL}/articles/{ticker}"
    headers = {"Authorization": f"Bearer {api_key}"}
    params = {"limit": limit}
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        print(f"  WARNING: request error for {ticker}: {exc}", file=sys.stderr)
        return []

    if resp.status_code == 200:
        try:
            return resp.json().get('articles', []) or []
        except ValueError as exc:
            print(f"  WARNING: bad JSON for {ticker}: {exc}", file=sys.stderr)
            return []

    if resp.status_code == 429 and retry_count < MAX_RETRIES:
        delay = min(BASE_RETRY_DELAY * (2 ** retry_count), 64)
        print(f"  Rate limited on {ticker}, retrying in {delay}s "
              f"(attempt {retry_count + 1}/{MAX_RETRIES})", file=sys.stderr)
        time.sleep(delay)
        return fetch_articles(ticker, api_key, limit, retry_count + 1)

    if resp.status_code in (401, 403):
        print(f"  ERROR: HTTP {resp.status_code} (auth/forbidden) — check API key.",
              file=sys.stderr)
        return None

    print(f"  WARNING: HTTP {resp.status_code} for {ticker}", file=sys.stderr)
    return []


def row_tickers(row):
    """Ordered unique ticker candidates for a row: Symbol first, then Tickers."""
    out = []
    sym = (row.get('Symbol') or '').strip()
    if sym:
        out.append(sym)
    raw = (row.get('Tickers') or '').strip()
    if raw:
        try:
            for t in ast.literal_eval(raw):
                t = str(t).strip()
                if t and t not in out:
                    out.append(t)
        except (ValueError, SyntaxError):
            pass
    return out


def row_target_utc(row):
    """Parse the row's ArrivalDate + ET Created into a naive UTC datetime.

    Returns None if either field is unparseable.
    """
    date_s = (row.get('ArrivalDate') or '').strip()
    time_s = (row.get('Created') or '').strip()
    if not date_s or not time_s:
        return None
    fmt = '%Y-%m-%d %H:%M:%S.%f' if '.' in time_s else '%Y-%m-%d %H:%M:%S'
    try:
        et = datetime.strptime(f"{date_s} {time_s}", fmt)
    except ValueError:
        return None
    return et + timedelta(hours=ET_TO_UTC_HOURS)


def parse_article_utc(created):
    """Parse an article's UTC `created` ISO string into a naive datetime."""
    if not created:
        return None
    s = str(created).strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)


def match_article(row, articles):
    """Pick the article for `row` from a ticker's article list.

    Primary key: normalized headline == normalized title. When that is
    ambiguous or absent, fall back to the closest created-timestamp within
    TS_TOLERANCE_SEC. Returns the article dict or None.
    """
    target_hl = normalize(row.get('Headline'))
    hl_hits = [a for a in articles if target_hl and normalize(a.get('title')) == target_hl]
    if len(hl_hits) == 1:
        return hl_hits[0]

    target_utc = row_target_utc(row)
    pool = hl_hits or articles  # disambiguate headline dupes by time when possible
    if target_utc is not None:
        best, best_delta = None, None
        for a in pool:
            a_utc = parse_article_utc(a.get('created'))
            if a_utc is None:
                continue
            delta = abs((a_utc - target_utc).total_seconds())
            if best_delta is None or delta < best_delta:
                best, best_delta = a, delta
        if best is not None and best_delta <= TS_TOLERANCE_SEC:
            return best

    # Last resort: a lone unambiguous headline hit already handled above; give up.
    return None


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--input', '-i', required=True, metavar='FILE',
                        dest='input_file',
                        help='PR-stats _body.tsv containing a "body" column.')
    parser.add_argument('--number-of-words', type=int, default=400, metavar='N',
                        dest='number_of_words',
                        help='Words to keep from the start of the body (default: 400).')
    parser.add_argument('--output-dir', '-o', default=str(DEFAULT_OUTPUT_DIR),
                        metavar='DIR', help=f'Output dir (default: {DEFAULT_OUTPUT_DIR}).')
    parser.add_argument('--api-key-file', default=str(DEFAULT_API_KEY_FILE),
                        metavar='FILE',
                        help=f'RTPR key file (default: {DEFAULT_API_KEY_FILE}).')
    parser.add_argument('--limit', type=int, default=100, metavar='N',
                        help='Articles to request per ticker (max 100, default: 100).')
    args = parser.parse_args()

    if args.number_of_words <= 0:
        print("ERROR: --number-of-words must be greater than 0.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input_file)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    import pandas as pd
    df = pd.read_csv(input_path, sep='\t', dtype=str)
    for col in ('body', 'Symbol', 'Headline'):
        if col not in df.columns:
            print(f"ERROR: column '{col}' not found in input file.", file=sys.stderr)
            sys.exit(1)

    api_key = load_api_key(args.api_key_file)

    rows = df.to_dict('records')
    missing_idx = [i for i, r in enumerate(rows)
                   if (r.get('body') or '') == NOT_FOUND]
    print(f"Rows: {len(rows)} | NOT_FOUND to fill: {len(missing_idx)}")
    if not missing_idx:
        print("Nothing to do.")
        return

    # One fetch per distinct candidate ticker, cached and reused across rows.
    cache = {}

    def articles_for(ticker):
        if ticker not in cache:
            if cache:  # throttle only between real network calls
                time.sleep(THROTTLE_SEC)
            arts = fetch_articles(ticker, api_key, args.limit)
            if arts is None:  # hard auth failure — stop early
                print("Aborting: authentication/authorization failure.", file=sys.stderr)
                sys.exit(2)
            cache[ticker] = arts
            print(f"  fetched {ticker}: {len(arts)} articles")
        return cache[ticker]

    filled = 0
    still_missing = []
    for i in missing_idx:
        row = rows[i]
        article = None
        for ticker in row_tickers(row):
            article = match_article(row, articles_for(ticker))
            if article is not None:
                break
        if article is None:
            still_missing.append(row)
            continue
        body = collapse_and_truncate(article.get('article_body'), args.number_of_words)
        if not body:
            still_missing.append(row)
            continue
        df.at[i, 'body'] = body
        filled += 1

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{input_path.stem}_rtprfilled.tsv"
    df.to_csv(out_path, sep='\t', index=False)

    print()
    print(f"Filled from RTPR: {filled}")
    print(f"Still NOT_FOUND: {len(still_missing)}")
    for r in still_missing:
        print(f"  MISS {r.get('Symbol')} {r.get('ArrivalDate')} "
              f"{(r.get('Headline') or '')[:60]}")
    print(f"Output: {out_path}")


if __name__ == '__main__':
    main()
