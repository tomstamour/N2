#!/usr/bin/env python3
"""rtpr_hist_backfill.py — historical PR backfill from the RTPR REST API.

Fetches historical press releases for a list of tickers (a universe TSV or a
plain ticker-list file) over a date window and writes them in the **O5 /
PR-stats TSV** schema (`Orchestrator5.1.py:_TSV_COLUMNS`), so the output drops
straight into `enriched_tsv_concatener.py` and the nightly dailyStats pipeline.

Why this exists / how it differs from the live path
---------------------------------------------------
The RTPR REST API (`api.rtpr.io`) now retains ~365 days of articles — the "last
24 hours" note in the May local doc is stale (see
RTPR_documention/RTPR_REST_API_notes.txt). The live firehose the connector
consumes is `ws.rtpr.io`, a *different* endpoint on a *different* rate budget, so
running this backfill does NOT slow the firehose (it is a separate process
hitting a separate server). See the notes file for the full evaluation.

The only genuinely new capability over the existing REST helpers
(`price_stats_loader/rtpr_body_fill.py`, `HistNewsFetcher2.py`) is **cursor
pagination**: `/articles/{ticker}` returns newest→oldest, capped at `limit`
(<=500) per page, with a `next_token` cursor. We page a ticker until its oldest
article on a page falls below the window start (or the cursor is exhausted).

Fidelity invariant
------------------
The time/text helpers below are **byte-for-byte copies** of
`RTPR_connector3_Firehose.py` (FIXED_TZ, to_fixed_tz, format_millis_*,
clean_inline_text, strip_rtpr_suffix). Body is `clean_inline_text(article_body)`
of the RAW body (NOT the connector's HTML-stripped `to_plain_body`), exactly as
O5 writes its `Body` cell — so an offline rescore
(`keyword_scorer-2.py --text-col Body`) reproduces the live combo. Keep these in
sync if the connector's helpers ever change.

Limitations (also in the notes file)
------------------------------------
- REST article objects carry no `id` and no co-`tickers` array. We derive a
  stable id (sha1) and reconstruct co-tickers by grouping fetched articles on
  (normalized title, created) across the supplied ticker set — so a PR's second
  ticker is only recovered if it too is in the list.
- Baseline/score columns (Float, MktCap, ITI/tradeSize baselines, FinBERT,
  ClerkSentiment, combo) are left blank; the existing offline add-ons fill them,
  exactly as the nightly master is already built.
"""

import argparse
import csv
import hashlib
import logging
import re
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_API_KEY_FILE = SCRIPT_DIR / 'RTPR_API-Key.txt'
DEFAULT_OUTPUT = SCRIPT_DIR / 'outputs' / 'rtpr_backfill.tsv'

RTPR_BASE_URL = 'https://api.rtpr.io'

# --- REST rate limiting / retry (mirrors price_stats_loader/rtpr_body_fill.py) ---
MAX_RETRIES = 5
BASE_RETRY_DELAY = 2      # seconds, exponential backoff on HTTP 429
THROTTLE_SEC = 1.05       # 60 req/min limit -> stay just under one call per second
PAGE_LIMIT_MAX = 500      # live API max per the openapi spec

# --- O5 output schema (verbatim from Orchestrator5.1.py:_TSV_COLUMNS, 24 cols) ---
_TSV_COLUMNS = [
    'Symbol', 'Tickers', 'ID', 'ArrivalDate', 'Created', 'ArrivalTime',
    'Headline', 'Body', 'Author',
    'Float', 'MktCap', 'Exchange',
    'LastDailyClose', 'itiBaseline', 'tradeSizeBaseline',
    'FinBERTCompletedAt',
    'positive', 'negative', 'neutral', 'sentiment_score', 'label',
    'ClerkSentiment',
    'model_proba_PLUS_kw_mult_nb', 'KwScoreCompletedAt',
]

logger = logging.getLogger('rtpr_hist_backfill')


# ─── Byte-for-byte copies of RTPR_connector3_Firehose.py helpers ──────────────
# (kept verbatim so Body/Headline/timestamps match the live O5 TSV exactly)

FIXED_TZ = timezone(timedelta(hours=-4), name='UTC-4')


def to_fixed_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(FIXED_TZ)


def format_millis_time(dt: datetime) -> str:
    fixed = to_fixed_tz(dt)
    return f"{fixed:%H:%M:%S}.{fixed.microsecond // 1000:03d}"


def format_millis_date(dt: datetime) -> str:
    return f"{to_fixed_tz(dt):%Y-%m-%d}"


def clean_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_rtpr_suffix(title: str) -> str:
    return re.sub(r"\s+(?:—|-)\s+RTPR\s*$", "", title).strip()


# ─── REST helpers (adapted from rtpr_body_fill.py / HistNewsFetcher2.py) ──────

class AuthError(Exception):
    """Non-retryable 401/403 — abort the whole run."""


def load_api_key(path) -> str:
    """Return the RTPR key: the first non-empty line after a `Key:` marker."""
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    for i, line in enumerate(lines):
        if line.strip().rstrip(':').lower() == 'key':
            for follow in lines[i + 1:]:
                if follow.strip():
                    return follow.strip()
    raise ValueError(f"No 'Key:' entry found in {path}")


def normalize(text) -> str:
    """Whitespace-collapsed, lowercased key for title de-dup/grouping."""
    return re.sub(r'\s+', ' ', str(text or '')).strip().lower()


def parse_created_utc(created):
    """Parse an article's `created` ISO string into a tz-aware UTC datetime.

    Returns None if unparseable (article is then skipped — it can't be placed
    in the window or timestamped).
    """
    s = str(created or '').strip()
    if not s:
        return None
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


_last_call_at = [0.0]


def _throttle() -> None:
    """Space real network calls >= THROTTLE_SEC apart (global across tickers)."""
    now = time.monotonic()
    wait = THROTTLE_SEC - (now - _last_call_at[0])
    if _last_call_at[0] and wait > 0:
        time.sleep(wait)
    _last_call_at[0] = time.monotonic()


def _get_page(url, headers, params, retry_count=0):
    """GET one page with 429 exponential backoff. Returns the parsed JSON dict,
    or None on a soft failure (skip this ticker's remaining pages). Raises
    AuthError on 401/403."""
    _throttle()
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
    except requests.exceptions.RequestException as exc:
        logger.warning("request error %s params=%s: %s", url, params, exc)
        return None

    if resp.status_code == 200:
        try:
            return resp.json()
        except ValueError as exc:
            logger.warning("bad JSON from %s: %s", url, exc)
            return None

    if resp.status_code == 429 and retry_count < MAX_RETRIES:
        delay = min(BASE_RETRY_DELAY * (2 ** retry_count), 64)
        logger.warning("rate limited (429), retrying in %ds (attempt %d/%d)",
                       delay, retry_count + 1, MAX_RETRIES)
        time.sleep(delay)
        return _get_page(url, headers, params, retry_count + 1)

    if resp.status_code in (401, 403):
        raise AuthError(f"HTTP {resp.status_code} (auth/forbidden) — check API key")

    logger.warning("HTTP %d from %s params=%s", resp.status_code, url, params)
    return None


def fetch_ticker(ticker, api_key, start_utc, limit):
    """Page /articles/{ticker} newest→oldest until the page's oldest article is
    below `start_utc` or the cursor is exhausted. Returns the raw article dicts
    (unfiltered by the end bound; the caller windows them)."""
    url = f"{RTPR_BASE_URL}/articles/{ticker}"
    headers = {'Authorization': f'Bearer {api_key}'}
    collected = []
    next_token = None
    pages = 0
    while True:
        params = {'limit': limit}
        if next_token:
            params['next_token'] = next_token
        data = _get_page(url, headers, params)
        if data is None:
            break
        arts = data.get('articles', []) or []
        collected.extend(arts)
        pages += 1
        oldest = None
        for a in arts:
            c = parse_created_utc(a.get('created'))
            if c is not None and (oldest is None or c < oldest):
                oldest = c
        next_token = data.get('next_token')
        if not next_token:
            break
        if oldest is not None and oldest < start_utc:
            break
    logger.info("  %s: %d articles over %d page(s)", ticker, len(collected), pages)
    return collected


# ─── Ticker list + row assembly ──────────────────────────────────────────────

def read_universe_tickers(path) -> list[str]:
    """Symbols from a universe TSV (uses the `Symbol` column)."""
    out, seen = [], set()
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f, delimiter='\t')
        if 'Symbol' not in (reader.fieldnames or []):
            sys.exit(f"Error: no 'Symbol' column in universe file {path}")
        for row in reader:
            sym = (row.get('Symbol') or '').strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
    return out


def read_ticker_file(path) -> list[str]:
    """Symbols from a plain file, one per line (blank / #-comment lines ignored)."""
    out, seen = [], set()
    for line in Path(path).read_text(encoding='utf-8').splitlines():
        sym = line.strip().upper()
        if not sym or sym.startswith('#'):
            continue
        if sym not in seen:
            seen.add(sym)
            out.append(sym)
    return out


def group_and_build_rows(fetched, start_utc, end_utc):
    """Window-filter, de-dup, reconstruct co-tickers, and build O5 rows.

    `fetched` is a list of (fetch_ticker, article) pairs. Articles are grouped
    by (normalized title, created-to-the-second); each group's ticker set
    becomes `Tickers` and shares one derived `ID`. One row is emitted per
    (ticker, group) — mirroring O5's per-in-universe-ticker fan-out.
    """
    # group_key -> {tickers: {sym: article}, created_utc, title}
    groups: dict[tuple, dict] = {}
    for fetch_ticker, art in fetched:
        created_utc = parse_created_utc(art.get('created'))
        if created_utc is None:
            continue
        if not (start_utc <= created_utc < end_utc):
            continue
        title = str(art.get('title') or '')
        key = (normalize(title), created_utc.strftime('%Y-%m-%dT%H:%M:%S'))
        # Prefer the article's own ticker field; fall back to the fetch ticker.
        sym = (str(art.get('ticker') or '').strip().upper() or fetch_ticker)
        g = groups.setdefault(key, {'tickers': {}, 'created_utc': created_utc,
                                    'title': title})
        g['tickers'].setdefault(sym, art)

    rows = []
    for (norm_title, created_iso), g in groups.items():
        group_syms = sorted(g['tickers'])
        created_utc = g['created_utc']
        # One shared, stable id per PR (group), like O5's shared connector id.
        id_seed = f"{','.join(group_syms)}{created_iso}{norm_title}"
        pr_id = hashlib.sha1(id_seed.encode('utf-8')).hexdigest()[:16]
        tickers_cell = '[' + ','.join(f'"{s}"' for s in group_syms) + ']'
        arrival_date = format_millis_date(created_utc)
        created_time = format_millis_time(created_utc)
        for sym in group_syms:
            art = g['tickers'][sym]
            title = str(art.get('title') or '')
            row = {c: '' for c in _TSV_COLUMNS}
            row.update({
                'Symbol':      sym,
                'Tickers':     tickers_cell,
                'ID':          pr_id,
                'ArrivalDate': arrival_date,
                'Created':     created_time,
                'ArrivalTime': created_time,   # no true arrival for backfill
                'Headline':    strip_rtpr_suffix(clean_inline_text(title)),
                'Body':        clean_inline_text(str(art.get('article_body') or '')),
                'Author':      str(art.get('author') or ''),
                'Exchange':    str(art.get('exchange') or ''),
            })
            rows.append(row)
    # Deterministic order: by arrival date, time, then symbol.
    rows.sort(key=lambda r: (r['ArrivalDate'], r['ArrivalTime'], r['Symbol']))
    return rows


def write_combined(rows, out_path) -> None:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=_TSV_COLUMNS, delimiter='\t',
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    logger.info("Wrote %d rows -> %s", len(rows), out_path)


def write_split_daily(rows, out_dir) -> None:
    """Emit news_output_<date>.tsv day-files (drop-in for the dailyStats pipeline)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r['ArrivalDate'], []).append(r)
    for day, day_rows in sorted(by_day.items()):
        path = out_dir / f"news_output_{day}.tsv"
        with open(path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=_TSV_COLUMNS, delimiter='\t',
                                    extrasaction='ignore')
            writer.writeheader()
            writer.writerows(day_rows)
        logger.info("Wrote %d rows -> %s", len(day_rows), path.name)


# ─── main ────────────────────────────────────────────────────────────────────

def setup_logging(log_dir=None) -> None:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter('%(asctime)s %(levelname)s: %(message)s',
                            datefmt='%Y-%m-%d %H:%M:%S')
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    if log_dir:
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(
            Path(log_dir) / f"rtpr_hist_backfill_{datetime.now():%Y-%m-%d}.log")
        fh.setFormatter(fmt)
        logger.addHandler(fh)


def parse_et_date(s: str) -> datetime:
    try:
        d = datetime.strptime(s, '%Y-%m-%d')
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {s!r}")
    return d.replace(tzinfo=FIXED_TZ)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--universe', metavar='FILE',
                     help='Universe TSV; fetches every ticker in its Symbol column.')
    src.add_argument('--tickers-file', metavar='FILE',
                     help='Plain file, one ticker symbol per line.')
    ap.add_argument('--start', required=True, type=parse_et_date, metavar='YYYY-MM-DD',
                    help='Inclusive ET window start (e.g. 2026-05-01).')
    ap.add_argument('--end', required=True, type=parse_et_date, metavar='YYYY-MM-DD',
                    help='Inclusive ET window end (e.g. 2026-05-31).')
    ap.add_argument('--output', default=str(DEFAULT_OUTPUT), metavar='FILE',
                    help=f'Combined output TSV (default: {DEFAULT_OUTPUT}).')
    ap.add_argument('--split-daily', action='store_true',
                    help='Instead emit news_output_<date>.tsv day-files into the '
                         'directory of --output (drop-in for the dailyStats pipeline).')
    ap.add_argument('--limit', type=int, default=PAGE_LIMIT_MAX, metavar='N',
                    help=f'Articles per page (max {PAGE_LIMIT_MAX}, default: {PAGE_LIMIT_MAX}).')
    ap.add_argument('--api-key-file', default=str(DEFAULT_API_KEY_FILE), metavar='FILE',
                    help=f'RTPR key file (default: {DEFAULT_API_KEY_FILE}).')
    ap.add_argument('--log-dir', default=str(SCRIPT_DIR / 'logs'), metavar='DIR',
                    help='Log directory (default: ./logs).')
    args = ap.parse_args()

    setup_logging(args.log_dir)

    if args.end < args.start:
        sys.exit("Error: --end is before --start.")
    limit = max(1, min(args.limit, PAGE_LIMIT_MAX))

    start_utc = args.start.astimezone(timezone.utc)
    end_utc = (args.end + timedelta(days=1)).astimezone(timezone.utc)  # exclusive

    tickers = (read_universe_tickers(args.universe) if args.universe
               else read_ticker_file(args.tickers_file))
    if not tickers:
        sys.exit("Error: no tickers to fetch.")

    api_key = load_api_key(args.api_key_file)

    logger.info("Backfill window %s..%s ET | %d tickers | limit=%d",
                args.start.date(), args.end.date(), len(tickers), limit)

    # Graceful stop: finish the current ticker, then write what we have.
    stop = {'flag': False}

    def _on_sig(signum, frame):
        logger.warning("shutdown requested — will write partial output after "
                       "the current ticker.")
        stop['flag'] = True
    signal.signal(signal.SIGINT, _on_sig)
    signal.signal(signal.SIGTERM, _on_sig)

    fetched = []
    done = 0
    try:
        for t in tickers:
            arts = fetch_ticker(t, api_key, start_utc, limit)
            for a in arts:
                fetched.append((t, a))
            done += 1
            if done % 25 == 0:
                logger.info("progress: %d/%d tickers, %d raw articles so far",
                            done, len(tickers), len(fetched))
            if stop['flag']:
                break
    except AuthError as exc:
        logger.error("Aborting: %s", exc)
        sys.exit(2)

    rows = group_and_build_rows(fetched, start_utc, end_utc)
    logger.info("Built %d rows (%d tickers fetched, %d raw articles).",
                len(rows), done, len(fetched))

    if args.split_daily:
        write_split_daily(rows, Path(args.output).parent)
    else:
        write_combined(rows, args.output)

    if stop['flag']:
        logger.warning("Partial run (stopped after %d/%d tickers).", done, len(tickers))


if __name__ == '__main__':
    main()
