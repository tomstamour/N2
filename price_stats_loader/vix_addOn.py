"""Enrich a news/PR-stats TSV with historical VIX and CNN Fear & Greed values.

Every row is pinned to ONE session - the anchor, resolved from the row's
ArrivalDate and shifted back by --days-prior - and gets seven columns describing
it:

    VIX_AnchorDate  the session the other six describe (yyyy-mm-dd)
    VIX_PrevClose   close of the session BEFORE the anchor
    VIX_Open        anchor session open
    VIX_High        anchor session high
    VIX_Low         anchor session low
    VIX_Close       anchor session close
    VIX_Chg1D_pct   (VIX_PrevClose - the close before it) / that close * 100,
                    the one-day move into VIX_PrevClose

plus, unless --no-fng is given, twelve CNN Fear & Greed columns for the SAME
anchor. VIX is only one axis of risk appetite; F&G is a 0-100 composite of seven
sentiment indicators, and the indicator levels are carried through raw so a model
can use them directly:

    FNG_Index               0-100 composite at the anchor (0 = extreme fear)
    FNG_Rating              CNN's own band for that score: extreme fear | fear |
                            neutral | greed | extreme greed
    FNG_PrevIndex           FNG_Index of the session BEFORE the anchor
    FNG_Chg1D               FNG_PrevIndex minus the score before it - the one-day
                            move into FNG_PrevIndex, in POINTS not percent (the
                            index is bounded 0-100 and has printed as low as
                            0.30, where a percentage change would explode)
    FNG_Momentum_SP500      S&P 500 level
    FNG_Momentum_SP125      its 125-day moving average (CNN scores the gap)
    FNG_StockPriceStrength  net 52-week highs vs lows on the NYSE
    FNG_StockPriceBreadth   McClellan volume summation index
    FNG_PutCallRatio        5-day average put/call ratio
    FNG_VIX50               50-day moving average of VIX
    FNG_JunkBondSpread      junk vs investment-grade yield spread
    FNG_SafeHavenDemand     20-day stock return minus bond return
    FNG_AsOf                the CNN day the twelve columns above actually came
                            from - normally the anchor itself, and equal to
                            VIX_AnchorDate; earlier than it only on rows served
                            by --fng-carry-forward (see CNN holes below)

The F&G columns are resolved on the same session index as the VIX columns, so
VIX_AnchorDate names the anchor for both. FNG_AsOf is not a second anchor - it
records provenance, and reading FNG_AsOf != VIX_AnchorDate is the only way to
find a carried row.

Anchor contract. Deliberately the same --days-prior contract as
tools/market_state/market_state_calculator.py (MSC), so a VIX_ column and an
msc_ column produced with the same N describe the same session:

    days_prior == 0 -> anchor = the last VIX session ON OR BEFORE ArrivalDate
    days_prior == N -> anchor = the Nth VIX session STRICTLY BEFORE it

    --days-prior 0, Tue 2026-08-25 -> 2026-08-25
    --days-prior 1, Tue 2026-08-25 -> 2026-08-24
    --days-prior 1, Sun 2026-08-23 -> 2026-08-21 (Fri)
    --days-prior 1, Sun 2026-04-05 -> 2026-04-02 (Thu - skips the weekend AND
                                                  Good Friday)

    "Strictly before" is what makes the weekend cases work. Rolling a Sunday
    back to Friday has ALREADY stepped back one session, so treating
    --days-prior 1 as a further shift would land on Thursday - one session
    staler than what a Monday trade would actually have seen. As written,
    --days-prior 1 means the same thing on every calendar day: the last session
    that CLOSED before this date.

Lookahead. At --days-prior 1 every column is known before ArrivalDate opens; that
is the setting for building training features, and VIX_Close is then the last VIX
close before ArrivalDate - on a trading-day row exactly what --days-prior 0 puts
in VIX_PrevClose. (On a weekend or holiday row the two settings resolve to the
SAME anchor, per the decrement above, so there --days-prior 0's VIX_PrevClose is
a session further back.) At --days-prior 0 the anchor is the PR day itself, so
VIX_High/VIX_Low/VIX_Close are LOOKAHEAD and are description-only - VIX_Open
(known at 09:30), VIX_PrevClose and VIX_Chg1D_pct stay lookahead-free.

The F&G columns behave the same way: FNG_Index at --days-prior 0 is the PR day's
own reading, which CNN keeps updating until the session ends, so it is lookahead
AND provisional. FNG_PrevIndex and FNG_Chg1D stay lookahead-free at either
setting. (The intraday point CNN appends for the current moment is always
discarded, so a run during market hours and a run after the close write the same
values - see _fng_points().)

The session index is the XNYS calendar INTERSECTED with the days the CBOE file
actually carries, because the CBOE file on its own is not a session index: since
2022 it also publishes a row on every US market holiday, from CBOE's global
trading hours, with real and quite different values (2026-01-19, MLK: 18.63 /
19.59 / 18.28 / 18.84 against the preceding Friday's 15.44 / 16.29 / 15.30 /
15.86). Anchoring on one of those would put the VIX columns a whole session
ahead of the msc_ columns carrying the same --days-prior, and would silently
corrupt VIX_PrevClose on the day after every US holiday since 2022. 33 such rows
exist. See build_session_index().

A date more than --max-lookback-days past the newest session in the history (a
stale cache, or a future date) is refused rather than silently anchored on old
data.

Needs pandas_market_calendars (venv only - see scripts/CLAUDE.md).

CNN holes. Unlike the CBOE file, CNN never publishes a non-session row - its day
set is a strict subset of XNYS. It does however MISS two real sessions,
2026-03-12 and 2026-07-15 (confirmed genuine: a narrow-window request omits them
too). Rows anchored there keep a correct VIX_AnchorDate and by default get blank
FNG_ columns, counted as fng_hole. Anchoring on CNN's own day set instead would
silently shift --days-prior by one session for every date after a hole and put
the FNG_ columns out of step with the VIX_ and msc_ columns. F&G history starts
2020-09-18; earlier anchors are blank too (fng_before_history).

--fng-carry-forward N fills such a row from the last CNN day within N sessions
before the anchor instead of leaving it blank (default 0 = off). The WHOLE F&G
block is then read at that day, FNG_PrevIndex and FNG_Chg1D included, so the
twelve columns keep describing one single session rather than mixing two; and
FNG_AsOf records which. VIX_AnchorDate is untouched - the shift is confined to
the F&G block and is greppable, which is what separates this from anchoring on
CNN's day set. Keep N small: it is a patch for CNN's one-day holes, not a way to
paper over a multi-day outage.

Source: the official CBOE daily history CSV (free, no key, OHLC since 1990):
    https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv
A Yahoo Finance ^VIX fallback is available via --source yahoo. Stooq is NOT
supported: as of 2026-07 it serves a JavaScript proof-of-work challenge page
instead of the CSV.

Fear & Greed comes from the undocumented endpoint CNN's own page calls:
    https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start-date}
It answers 418 "I'm a teapot. You're a bot." unless the request carries a Referer
and an explicit Accept - see FNG_HEADERS. Only the composite has a historical
0-100 score; the seven indicators are served as raw levels, which is what the
FNG_ indicator columns carry. CNN's own market_volatility_vix series is dropped
as redundant - it is byte-identical to the CBOE VIX close over all 1493
overlapping days.

Each history is cached next to this script (cache/vix_history.csv,
cache/cnn_fng_history.csv) and reused until older than --max-cache-age-hours
(default 12), so reruns work offline. --refresh forces a download, --no-network
forbids one. A dead CNN endpoint is not fatal: the FNG_ columns go blank and the
VIX columns are written as usual.

Output goes to <input_stem>_vixfng.tsv, or <input_stem>_vix.tsv under --no-fng
(input is never modified). The VIX_ and FNG_ groups are filled independently, so
running this over a file that already has VIX_ columns backfills only the F&G
ones.

Usage:
    # training features, leak-free, with CNN's one-day holes patched
    /home/tom/venv/bin/python vix_addOn.py -i inputs/news_output_2026-07-12.tsv \
        --days-prior 1 --fng-carry-forward 1

    # describe the PR day itself (lookahead in High/Low/Close and FNG_Index)
    /home/tom/venv/bin/python vix_addOn.py -i inputs/news_output_2026-07-12.tsv

    # VIX only, as before
    /home/tom/venv/bin/python vix_addOn.py -i inputs/news_output_2026-07-12.tsv \
        --days-prior 1 --no-fng
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
import urllib.request
from bisect import bisect_right
from datetime import datetime, date, timezone
from pathlib import Path

import pandas_market_calendars as mcal

SCRIPT_DIR = Path(__file__).parent
DEFAULT_CACHE = SCRIPT_DIR / 'cache' / 'vix_history.csv'
DEFAULT_FNG_CACHE = SCRIPT_DIR / 'cache' / 'cnn_fng_history.csv'

CBOE_URL = 'https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv'
YAHOO_URL = ('https://query1.finance.yahoo.com/v8/finance/chart/%5EVIX'
             '?period1=0&period2={end}&interval=1d')
USER_AGENT = 'Mozilla/5.0 (X11; Linux x86_64)'

FNG_URL = 'https://production.dataviz.cnn.io/index/fearandgreed/graphdata/{start}'
FNG_START = '2020-09-18'      # the first day CNN serves; earlier starts 500

# CNN answers 418 "I'm a teapot. You're a bot." unless the request carries a
# Referer AND an explicit Accept. urllib sends neither by default, so _get()'s
# plain User-Agent is not enough - verified 2026-09-02: UA alone 418, UA+Referer
# 418, UA+Referer+Accept 200. Do NOT add Accept-Encoding: it also clears the 418
# but urllib will not decompress the gzip body it then returns.
FNG_HEADERS = {'Referer': 'https://edition.cnn.com/',
               'Accept': 'application/json, text/plain, */*'}

VIX_COLUMNS = ['VIX_AnchorDate', 'VIX_PrevClose', 'VIX_Open', 'VIX_High',
               'VIX_Low', 'VIX_Close', 'VIX_Chg1D_pct']

# CNN series key -> output column, for the eight raw indicator levels. Ordered;
# drives the parse, the cache round-trip and the column order.
#
# market_volatility_vix is deliberately absent: it is byte-identical to the CBOE
# VIX close (0 disagreements over the 1493 overlapping days), so it would only
# duplicate VIX_Close. Its 50-day average is kept - that one is new information.
FNG_SERIES = {
    'market_momentum_sp500':    'FNG_Momentum_SP500',
    'market_momentum_sp125':    'FNG_Momentum_SP125',
    'stock_price_strength':     'FNG_StockPriceStrength',
    'stock_price_breadth':      'FNG_StockPriceBreadth',
    'put_call_options':         'FNG_PutCallRatio',
    'market_volatility_vix_50': 'FNG_VIX50',
    'junk_bond_demand':         'FNG_JunkBondSpread',
    'safe_haven_demand':        'FNG_SafeHavenDemand',
}

FNG_COLUMNS = (['FNG_Index', 'FNG_Rating', 'FNG_PrevIndex', 'FNG_Chg1D']
               + list(FNG_SERIES.values()) + ['FNG_AsOf'])

# What the cache stores per day. FNG_PrevIndex/FNG_Chg1D are derived at join
# time from neighbouring sessions, so they are not cached.
FNG_CACHE_FIELDS = ['FNG_Index', 'FNG_Rating'] + list(FNG_SERIES.values())

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# The PR-stats corpora carry a raw Body column; csv's 131072-byte default field
# limit is comfortable today (widest field 11811) but a single long PR would
# abort the read of a 24 MB file. Same value as split_train_val.py.
csv.field_size_limit(10 ** 9)


# --------------------------------------------------------------------------- #
# fetching
# --------------------------------------------------------------------------- #

def _get(url: str, timeout: int, headers: dict = None) -> bytes:
    hdrs = {'User-Agent': USER_AGENT}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(url, headers=hdrs)
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
# CNN Fear & Greed
# --------------------------------------------------------------------------- #

def _fng_points(series: dict) -> dict:
    """{date: point} for the SETTLED daily points of one CNN series.

    CNN stamps every settled day at exactly 00:00:00 UTC, then appends one extra
    point carrying the current, still-moving reading, stamped at the moment of
    the request (e.g. 14:20:00Z). Keeping only the midnight points drops that
    live point, so a run during market hours writes the same value as a run
    after the close. The current DAY is still present - its midnight point is
    itself provisional until the session ends, exactly like VIX_Close at
    --days-prior 0.
    """
    out = {}
    for p in series.get('data', []):
        ts, y = p.get('x'), p.get('y')
        if ts is None or y is None:
            continue
        t = datetime.fromtimestamp(ts / 1000.0, timezone.utc)
        if (t.hour, t.minute, t.second) != (0, 0, 0):
            continue
        out[t.date()] = p
    return out


def fetch_fng(timeout: int) -> dict:
    """Return {date: {column: value}} from CNN's Fear & Greed graphdata API."""
    raw = _get(FNG_URL.format(start=FNG_START), timeout, FNG_HEADERS)
    try:
        payload = json.loads(raw.decode('utf-8', 'replace'))
    except json.JSONDecodeError as exc:
        raise ValueError(f'CNN did not return JSON ({exc}) - the endpoint is '
                         f'probably serving its bot-check page') from exc
    if 'fear_and_greed_historical' not in payload:
        raise ValueError(f'unexpected CNN payload, keys={sorted(payload)!r} '
                         '(the endpoint may be serving an error page)')

    hist = {}
    for d, p in _fng_points(payload['fear_and_greed_historical']).items():
        rec = {c: None for c in FNG_CACHE_FIELDS}
        rec['FNG_Index'] = float(p['y'])
        # Only fear_and_greed_historical carries a usable rating - it matches the
        # 0-100 bands on every one of the 1495 points. The rating inside each
        # INDICATOR series is a rendering artifact, constant over the whole
        # history (put_call_options is "extreme fear" on all 1494 days), and is
        # never read.
        rec['FNG_Rating'] = (p.get('rating') or '').strip() or None
        hist[d] = rec

    for key, col in FNG_SERIES.items():
        if key not in payload:
            logging.warning(f'CNN payload carries no {key!r} series - '
                            f'{col} left blank.')
            continue
        for d, p in _fng_points(payload[key]).items():
            if d in hist:               # the index is what defines a usable day
                hist[d][col] = float(p['y'])
    return hist


def write_fng_cache(hist: dict, cache_path: Path) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_path.with_suffix(cache_path.suffix + '.tmp')
    with open(tmp, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['DATE'] + FNG_CACHE_FIELDS)
        for d in sorted(hist):
            rec = hist[d]
            w.writerow([d.isoformat()] + ['' if rec.get(c) is None else rec[c]
                                          for c in FNG_CACHE_FIELDS])
    os.replace(tmp, cache_path)


def read_fng_cache(cache_path: Path) -> dict:
    hist = {}
    with open(cache_path, newline='') as f:
        for row in csv.DictReader(f):
            try:
                d = date.fromisoformat((row.get('DATE') or '').strip())
            except ValueError:
                continue
            rec = {}
            for c in FNG_CACHE_FIELDS:
                v = (row.get(c) or '').strip()
                if c == 'FNG_Rating':
                    rec[c] = v or None
                else:
                    try:
                        rec[c] = float(v)
                    except ValueError:
                        rec[c] = None
            hist[d] = rec
    return hist


def load_fng_history(cache_path: Path, max_age_hours: float, refresh: bool,
                     no_network: bool, timeout: int) -> dict:
    """Return {date: {column: value}}, or {} if CNN cannot be reached.

    Unlike load_history(), a failure here is NOT fatal. The VIX columns owe
    nothing to CNN, so an unreachable endpoint leaves the FNG_ columns blank and
    lets the rest of the run finish rather than losing the whole file.
    """
    cache_ok = cache_path.is_file()
    age_h = ((time.time() - cache_path.stat().st_mtime) / 3600.0
             if cache_ok else float('inf'))

    if cache_ok and not refresh and age_h <= max_age_hours:
        logging.info(f'Using cached CNN F&G history ({cache_path}, '
                     f'{age_h:.1f}h old).')
        hist = read_fng_cache(cache_path)
    elif no_network:
        if not cache_ok:
            logging.error(f'--no-network but no CNN F&G cache at {cache_path} - '
                          f'FNG_ columns left blank.')
            return {}
        logging.warning(f'--no-network: using stale CNN F&G cache '
                        f'({age_h:.1f}h old).')
        hist = read_fng_cache(cache_path)
    else:
        try:
            logging.info('Downloading CNN Fear & Greed history...')
            hist = fetch_fng(timeout)
            write_fng_cache(hist, cache_path)
            logging.info(f'Cached {len(hist)} CNN F&G days to {cache_path}.')
        except Exception as exc:
            if not cache_ok:
                logging.error(f'CNN F&G download failed ({exc}) and no cache is '
                              f'available - FNG_ columns left blank.')
                return {}
            logging.error(f'CNN F&G download failed ({exc}); falling back to '
                          f'cache ({age_h:.1f}h old).')
            hist = read_fng_cache(cache_path)

    if not hist:
        logging.error('CNN F&G history is empty - FNG_ columns left blank.')
        return {}
    logging.info(f'CNN F&G history: {len(hist)} days, '
                 f'{min(hist)} .. {max(hist)}.')
    return hist


# --------------------------------------------------------------------------- #
# anchor resolution
# --------------------------------------------------------------------------- #

def build_session_index(hist: dict) -> list:
    """Ascending list of anchorable sessions: VIX days that are also XNYS days.

    The CBOE history is NOT a session index. Since 2022 it carries a row on every
    US market holiday - MLK, Presidents', Memorial, Juneteenth, July 4, Labor
    Day, Thanksgiving, plus 2004-06-11 (Reagan) and 2025-01-09 (Carter) - 33 rows
    in all, priced from CBOE's global trading hours rather than a US cash
    session, and carrying genuinely different values from the previous session
    rather than a repeat of it. Left in the index they would anchor the day after
    a holiday one session later than MSC does for the same --days-prior, and
    would make VIX_PrevClose the holiday's close. The calendar therefore has the
    final say. Checked against the real Sessions.resolve(): with the intersection
    the two agree on all 31,656 resolutions over 2005-01-01..2026-09-01 at
    N=0..3; on the raw CBOE index they disagree 278 times.

    The reverse gap - an XNYS session the CBOE file skips - cannot be filled and
    simply rolls back to the previous VIX day, which is the only thing a VIX
    consumer can do. There are exactly four, all pre-2000 (1991-03-01,
    1997-01-31, 1997-11-26, 1999-12-31), and they are the only reason the parity
    run over the FULL history is 60 mismatches rather than 0.
    """
    days = sorted(hist)
    cal = mcal.get_calendar('XNYS')
    valid = {d.date() for d in cal.valid_days(str(days[0]), str(days[-1]))}
    keep = [d for d in days if d in valid]
    if not keep:
        raise SystemExit('No VIX day in the history is an XNYS session.')
    logging.info(f'Session index: {len(keep)} anchorable sessions '
                 f'({len(days) - len(keep)} non-session VIX rows dropped - CBOE '
                 f'publishes VIX on US market holidays), {keep[0]} .. {keep[-1]}.')
    return keep


def index_on_or_before(days: list, d: date):
    """Position of the last session <= d, or None if d predates the history."""
    i = bisect_right(days, d) - 1
    return i if i >= 0 else None


def resolve_anchor(days: list, target: date, days_prior: int, max_back: int):
    """Return (anchor_index, problem) for `target` under the --days-prior contract.

        days_prior == 0  -> the last VIX session ON OR BEFORE target
        days_prior >= 1  -> the days_prior-th VIX session STRICTLY BEFORE target

    The second clause only bites when target is not itself a session. Rolling a
    Sunday back to Friday has ALREADY stepped back one session, so shifting
    again would land on Thursday - one session staler than what a Monday trade
    would actually have seen. Decrementing here makes weekend and holiday dates
    agree with weekdays: --days-prior 1 always means "the last session that
    closed before this calendar date". Session dates are unaffected (days[i] ==
    target, so nothing is consumed).

    This mirrors Sessions.resolve() in
    tools/market_state/market_state_calculator.py line for line; keep the two in
    sync. `days` is build_session_index()'s output, so the index it walks is the
    XNYS calendar restricted to days VIX actually printed. `max_back` is the one
    thing MSC has no need of: a bisect on a bounded history happily returns the
    newest session for any future date, so a target past the end of the history
    is refused rather than anchored on stale data.

    problem is None on success, else one of:
        'before_history'  target predates the start of the VIX history
        'stale'           the nearest session on or before target is further
                          than max_back calendar days back - target is past the
                          end of the history, not merely inside a holiday run
        'short_history'   the days_prior shift ran off the front of the history
    """
    i = index_on_or_before(days, target)
    if i is None:
        return None, 'before_history'
    if (target - days[i]).days > max_back:
        return None, 'stale'
    if days_prior >= 1 and days[i] != target:
        days_prior -= 1
    j = i - days_prior
    if j < 0:
        return None, 'short_history'
    return j, None


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


def fmt(x) -> str:
    return '' if x is None else f'{x:.2f}'


def fmt4(x) -> str:
    """4dp, for the raw CNN indicator levels.

    fmt()'s 2dp is fine for index points and VIX, but would flatten
    FNG_PutCallRatio (0.7106) and FNG_StockPriceStrength (-0.4974) into noise.
    """
    return '' if x is None else f'{x:.4f}'


def warn_unresolved(target: date, problem: str, days: list, days_prior: int,
                    max_back: int) -> None:
    if problem == 'before_history':
        logging.warning(f'{target} predates the VIX history ({days[0]}) - '
                        f'row left blank.')
    elif problem == 'stale':
        nearest = days[index_on_or_before(days, target)]
        logging.warning(f'{target} is {(target - nearest).days} calendar days past '
                        f'the nearest VIX session on or before it ({nearest}), more '
                        f'than --max-lookback-days {max_back} - row left blank. If '
                        f'that date is recent the cached history is stale: rerun '
                        f'with --refresh.')
    elif problem == 'short_history':
        logging.warning(f'{target} shifted back --days-prior {days_prior} sessions '
                        f'lands before the start of the VIX history ({days[0]}) - '
                        f'row left blank.')


def fill_fng(rec: dict, fng: dict, days: list, j: int, fng_start: date,
             stats: dict, warned: set, carry_forward: int = 0) -> None:
    """Write the FNG_ columns for the anchor at days[j]; blanks if CNN has no row.

    The anchor comes from the VIX/XNYS session index, NOT from CNN's own day set,
    so FNG_ and VIX_ on a row always describe the same session. CNN skips two
    real XNYS sessions (2026-03-12, 2026-07-15); by default those anchors stay
    correct and the columns are simply left blank, which is honest and greppable.
    Walking CNN's days instead would silently shift --days-prior by one session
    for every date after a hole and break parity with the VIX_ and msc_ columns.

    carry_forward > 0 fills a hole from the last CNN day within that many
    sessions before the anchor. The whole block - FNG_PrevIndex and FNG_Chg1D
    included - is then read at that day: reading FNG_Index at the carried day but
    FNG_PrevIndex at days[j-1] would make them the SAME value and imply a zero
    one-day move into a day that did move. FNG_AsOf names the day used, so the
    substitution is visible in the output rather than only in this docstring.
    """
    anchor = days[j]
    # range(carry_forward), NOT carry_forward + 1: the body decrements without
    # re-testing, so an extra pass walks back one day past what was asked for -
    # at carry_forward=0 that silently turned the feature ON.
    k = j
    for _ in range(carry_forward):
        if days[k] in fng or k == 0:
            break
        k -= 1
    cur = fng.get(days[k])
    if cur is None:
        if anchor < fng_start:
            stats['fng_before_history'] += 1
        else:
            stats['fng_hole'] += 1
            if anchor not in warned:
                warned.add(anchor)
                logging.warning(f'CNN published no Fear & Greed reading for '
                                f'{anchor}, which IS an XNYS session - FNG_ '
                                f'columns left blank for rows anchored there '
                                f'(the VIX_ columns are unaffected). Pass '
                                f'--fng-carry-forward to fill from the previous '
                                f'CNN day instead.')
        return

    if k != j:
        stats['fng_carried'] += 1
        if anchor not in warned:
            warned.add(anchor)
            logging.warning(f'CNN published no Fear & Greed reading for {anchor}, '
                            f'which IS an XNYS session - carrying {days[k]} '
                            f'forward for rows anchored there (FNG_AsOf records '
                            f'it; VIX_AnchorDate stays {anchor}).')

    rec['FNG_AsOf'] = days[k].isoformat()
    rec['FNG_Index'] = fmt(cur['FNG_Index'])
    rec['FNG_Rating'] = cur['FNG_Rating'] or ''
    for col in FNG_SERIES.values():
        rec[col] = fmt4(cur.get(col))

    # Mirrors VIX_PrevClose/VIX_Chg1D_pct: the session BEFORE the one the block
    # describes, and the one-day move INTO it. Points, not percent - the index is
    # bounded 0-100 and has printed as low as 0.30, where a percentage change
    # would explode.
    prev = fng.get(days[k - 1]) if k >= 1 else None
    if prev and prev.get('FNG_Index') is not None:
        rec['FNG_PrevIndex'] = fmt(prev['FNG_Index'])
        before = fng.get(days[k - 2]) if k >= 2 else None
        if before and before.get('FNG_Index') is not None:
            rec['FNG_Chg1D'] = fmt(prev['FNG_Index'] - before['FNG_Index'])

    stats['fng_filled'] += 1


def enrich(rows: list, header: list, hist: dict, days: list, fng: dict,
           date_column: str, days_prior: int, max_back: int, overwrite: bool,
           want_fng: bool, fng_carry_forward: int = 0) -> tuple:
    """Return (out_header, out_rows, stats)."""
    out_header = list(header)
    for col in VIX_COLUMNS + (FNG_COLUMNS if want_fng else []):
        if col not in out_header:
            out_header.append(col)

    stats = {'filled': 0, 'kept': 0, 'no_date': 0, 'rolled': 0, 'no_prevclose': 0,
             'before_history': 0, 'stale': 0, 'short_history': 0,
             'fng_filled': 0, 'fng_kept': 0, 'fng_hole': 0, 'fng_carried': 0,
             'fng_before_history': 0}
    out_rows = []
    warned = set()
    fng_warned = set()
    session_set = set(days)      # membership test for the "was it a session?" count
    fng_start = min(fng) if fng else None
    # The FNG_ columns stay in out_header above even when CNN could not be
    # reached; they are simply left blank. The output schema must not depend on
    # network luck, or a downstream consumer would see the column set come and go.
    fill_fng_cols = want_fng and bool(fng)

    for row in rows:
        rec = dict(row)
        # The two column groups are tracked independently, so a rerun over a file
        # that already carries VIX_ values fills the FNG_ columns without
        # disturbing them - that is the path for backfilling existing corpora.
        do_vix = overwrite or not any(rec.get(c, '') != '' for c in VIX_COLUMNS)
        do_fng = fill_fng_cols and (overwrite or
                                    not any(rec.get(c, '') != '' for c in FNG_COLUMNS))
        if not do_vix:
            stats['kept'] += 1
        if fill_fng_cols and not do_fng:
            stats['fng_kept'] += 1
        if not do_vix and not do_fng:
            out_rows.append(rec)
            continue

        for col in (VIX_COLUMNS if do_vix else []) + (FNG_COLUMNS if do_fng else []):
            rec[col] = ''

        target = parse_arrival_date(rec.get(date_column, ''))
        if target is None:
            stats['no_date'] += 1
            out_rows.append(rec)
            continue

        j, problem = resolve_anchor(days, target, days_prior, max_back)
        if problem:
            stats[problem] += 1
            if target not in warned:          # a corpus repeats each date many times
                warned.add(target)
                warn_unresolved(target, problem, days, days_prior, max_back)
            out_rows.append(rec)
            continue

        if target not in session_set:
            stats['rolled'] += 1

        anchor = days[j]
        if do_vix:
            o, h, l, c = hist[anchor]
            rec['VIX_AnchorDate'] = anchor.isoformat()
            rec['VIX_Open'], rec['VIX_High'] = fmt(o), fmt(h)
            rec['VIX_Low'], rec['VIX_Close'] = fmt(l), fmt(c)

            if j >= 1:
                prev_close = hist[days[j - 1]][3]
                rec['VIX_PrevClose'] = fmt(prev_close)
                if j >= 2:
                    before = hist[days[j - 2]][3]
                    if before:
                        rec['VIX_Chg1D_pct'] = f'{(prev_close - before) / before * 100:.2f}'
            else:
                stats['no_prevclose'] += 1

            stats['filled'] += 1

        if do_fng:
            fill_fng(rec, fng, days, j, fng_start, stats, fng_warned,
                     fng_carry_forward)

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
    # lineterminator='\n': csv defaults to '\r\n', which made every file this
    # writer produced CRLF while the rest of the chain (pandas to_csv) is LF -
    # and MIXED inside a file, because newlines embedded in a quoted Body field
    # stay LF regardless. Nothing downstream wanted the CR.
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=header, delimiter='\t',
                           extrasaction='ignore', restval='',
                           lineterminator='\n')
        w.writeheader()
        w.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news/PR-stats TSV with historical VIX values: '
            'VIX_AnchorDate, VIX_PrevClose, VIX_Open/High/Low/Close for the '
            'anchor session, and VIX_Chg1D_pct. Data comes from the free CBOE '
            'daily history CSV (Yahoo fallback) and is cached locally. '
            'Twelve CNN Fear & Greed columns (FNG_Index, FNG_Rating and the '
            'seven underlying indicators) are added for the SAME anchor unless '
            '--no-fng is given. Output is written to <input_stem>_vixfng.tsv '
            '(_vix.tsv under --no-fng); the input is untouched.\n\n'
            'Lookahead note: at --days-prior 1 every column is known before the '
            'row date opens. At --days-prior 0 the anchor is the row date itself, '
            'so VIX_High/Low/Close and FNG_Index are lookahead - use them for '
            'description only, never as model features.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', '-i', required=True, dest='input_file',
                        metavar='FILE', help='Input .tsv (needs an ArrivalDate column).')
    parser.add_argument('--output', '-o', dest='output_file', metavar='FILE',
                        help='Output path (default: <input_stem>_vixfng.tsv, '
                             'or <input_stem>_vix.tsv under --no-fng).')
    parser.add_argument('--date-column', default='ArrivalDate', metavar='COL',
                        help='Column holding the row date (default: ArrivalDate).')
    parser.add_argument('--days-prior', type=int, default=0, metavar='N',
                        help='Shift the anchor back N VIX sessions (default 0). '
                             'Use 1 to enrich PR rows leak-free; 0 to describe '
                             'the row date itself. Same contract as --days-prior '
                             'in tools/market_state/market_state_calculator.py.')
    parser.add_argument('--source', choices=sorted(FETCHERS), default='cboe',
                        help='Download source (default: cboe). Stooq is unusable: '
                             'it serves a JS proof-of-work page instead of CSV.')
    parser.add_argument('--cache-file', default=str(DEFAULT_CACHE), metavar='FILE',
                        help=f'History cache path (default: {DEFAULT_CACHE}).')
    parser.add_argument('--no-fng', action='store_true',
                        help='Skip the CNN Fear & Greed columns (VIX only, and '
                             'the output keeps the _vix.tsv suffix).')
    parser.add_argument('--fng-carry-forward', type=int, default=0, metavar='N',
                        help='When the anchor is a real session CNN published no '
                             'reading for, fill the FNG_ columns from the last '
                             'CNN day within N sessions before it instead of '
                             'leaving them blank (default 0 = leave blank). The '
                             'whole F&G block comes from that day and FNG_AsOf '
                             'records which; VIX_AnchorDate is unaffected. CNN '
                             'has exactly two such holes (2026-03-12, '
                             '2026-07-15), so 1 is enough - a larger N would '
                             'quietly paper over a real outage.')
    parser.add_argument('--fng-cache-file', default=str(DEFAULT_FNG_CACHE),
                        metavar='FILE',
                        help=f'CNN F&G cache path (default: {DEFAULT_FNG_CACHE}).')
    parser.add_argument('--max-cache-age-hours', type=float, default=12.0,
                        metavar='H', help='Re-download if the cache is older (default: 12).')
    parser.add_argument('--refresh', action='store_true',
                        help='Force a download even if the cache is fresh.')
    parser.add_argument('--no-network', action='store_true',
                        help='Never download; use the cache even if stale.')
    parser.add_argument('--timeout', type=int, default=30, metavar='SEC',
                        help='HTTP timeout in seconds (default: 30).')
    parser.add_argument('--max-lookback-days', type=int, default=10, metavar='N',
                        help='Largest calendar gap tolerated between a row date '
                             'and the VIX session on or before it (default: 10, '
                             'covers holiday runs). A wider gap means the date is '
                             'past the end of the history, and the row is left '
                             'blank rather than anchored on stale data.')
    parser.add_argument('--overwrite', action='store_true',
                        help='Recompute rows that already carry VIX or FNG values '
                             '(default: leave them as they are). The two groups '
                             'are tracked separately, so a file that already has '
                             'VIX_ columns gets its FNG_ columns filled without '
                             'this flag.')
    args = parser.parse_args()

    if not os.path.isfile(args.input_file):
        raise SystemExit(f'Input not found: {args.input_file}')
    if args.days_prior < 0:
        raise SystemExit('--days-prior must be >= 0; a negative shift would '
                         'anchor on a session that had not happened yet.')
    if args.fng_carry_forward < 0:
        raise SystemExit('--fng-carry-forward must be >= 0.')

    header, rows = read_tsv_flexible(args.input_file)
    if args.date_column not in header:
        raise SystemExit(f'Column {args.date_column!r} not in {args.input_file}. '
                         f'Header: {header}')
    logging.info(f'Read {len(rows)} rows from {args.input_file}.')

    # A banner that survives in the log, because --days-prior is recorded in the
    # output only as VIX_AnchorDate, per row.
    logging.info(f'days_prior={args.days_prior}   ' + (
        'LEAK-FREE (the anchor session closed BEFORE the row date)'
        if args.days_prior >= 1 else
        'the anchor IS the row date - VIX_High/Low/Close and FNG_Index are '
        'lookahead if that is the PR date'))

    hist = load_history(args.source, Path(args.cache_file),
                        args.max_cache_age_hours, args.refresh,
                        args.no_network, args.timeout)
    days = build_session_index(hist)

    want_fng = not args.no_fng
    fng = (load_fng_history(Path(args.fng_cache_file), args.max_cache_age_hours,
                            args.refresh, args.no_network, args.timeout)
           if want_fng else {})

    out_header, out_rows, stats = enrich(rows, header, hist, days, fng,
                                         args.date_column, args.days_prior,
                                         args.max_lookback_days, args.overwrite,
                                         want_fng, args.fng_carry_forward)

    out_path = args.output_file
    if not out_path:
        root, ext = os.path.splitext(args.input_file)
        suffix = '_vixfng' if want_fng else '_vix'
        out_path = f'{root}{suffix}{ext or ".tsv"}'
    write_tsv(out_path, out_header, out_rows)

    logging.info(f'Filled {stats["filled"]} rows, kept {stats["kept"]} existing, '
                 f'{stats["no_date"]} unparseable dates, '
                 f'{stats["before_history"] + stats["stale"] + stats["short_history"]} '
                 f'without a resolvable anchor '
                 f'(before_history={stats["before_history"]}, '
                 f'stale={stats["stale"]}, short_history={stats["short_history"]}).')
    logging.info(f'{stats["rolled"]} of the filled rows had a non-session date '
                 f'(weekend/holiday) and were anchored on the preceding session.')
    if stats['no_prevclose']:
        logging.warning(f'{stats["no_prevclose"]} rows anchored on the very first '
                        f'session in the history - VIX_PrevClose/VIX_Chg1D_pct '
                        f'left blank.')
    if stats['kept']:
        logging.warning(f'{stats["kept"]} rows already carried VIX values and were '
                        f'left alone - they were computed under whatever '
                        f'--days-prior the earlier run used (check their '
                        f'VIX_AnchorDate). Pass --overwrite to recompute the whole '
                        f'file at --days-prior {args.days_prior}.')

    if want_fng:
        logging.info(f'CNN F&G: filled {stats["fng_filled"]} rows '
                     f'({stats["fng_carried"]} of them carried forward from an '
                     f'earlier CNN day - see FNG_AsOf), '
                     f'kept {stats["fng_kept"]} existing, '
                     f'{stats["fng_hole"]} anchored on a session CNN skipped, '
                     f'{stats["fng_before_history"]} anchored before CNN history '
                     f'starts ({FNG_START}).')
        if stats['fng_before_history']:
            logging.warning(f'{stats["fng_before_history"]} rows anchor before '
                            f'{FNG_START}, the first day CNN serves - FNG_ columns '
                            f'left blank there. The VIX_ columns are unaffected.')
        if stats['fng_kept']:
            logging.warning(f'{stats["fng_kept"]} rows already carried FNG values '
                            f'and were left alone - like the VIX columns they were '
                            f'computed under whatever --days-prior the earlier run '
                            f'used. Pass --overwrite to recompute at '
                            f'--days-prior {args.days_prior}.')
    logging.info(f'Wrote {out_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
