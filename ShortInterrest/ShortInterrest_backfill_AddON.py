#!/usr/bin/env python3
"""
ShortInterrest_backfill_AddON.py — historical sister of ShortInterrest_AddON.py.

The live addon anchors every fetch on eastern_today(), so it can only describe
*now*. This script computes the SAME 24 columns, in the SAME order, with the
SAME formulas, but **as of a historical date**, so universe TSVs written before
Step 8 existed can be enriched retroactively.

Point-in-time by construction: for a file dated D it uses only data that had
actually been PUBLISHED by D. Nothing here can see the future, so a backfilled
row carries no lookahead advantage over a live row sitting next to it.

  Source                     Columns              As-of mechanism
  -------------------------  -------------------  ----------------------------
  FINRA bi-monthly SI        SI_* (8)             settlementDate is a FINRA
                                                  partition key -> query it
                                                  with an EQUAL CompareFilter.
                                                  Which settlement is visible
                                                  on D follows the publication
                                                  lag (7 business days).
  FINRA daily short volume   SVR_* (4)            CNMSshvol{YYYYMMDD}.txt is
                                                  permanently archived.
  Nasdaq Reg SHO list        RegSHO_* (2)         nasdaqth{YYYYMMDD}.txt idem.
  iBorrowDesk                CTB_*/Borrow_* (7)   the cached JSON holds ~1 year
                                                  of daily history -> slice it
                                                  to entries <= as_of.
  IBKR shortstock FTP        CTB_RebatePct        snapshot only; unavailable
                                                  here (port 21 blocked) and
                                                  blank in production too.
  yfinance                   SI_* fallback        DELIBERATELY DROPPED - it is
                                                  a today-only snapshot and
                                                  would be a lookahead leak.

Everything that determines a *value* is imported from ShortInterrest_AddON
(compute_symbol_metrics, borrow_history_metrics, regsho_flag_and_streak,
_parse_finra_records, the thresholds, OUTPUT_COLUMNS). Only the date-windowing
loops are reimplemented here. That is what keeps the two scripts from drifting
and what makes --compare-to a meaningful test. ShortInterrest_AddON.py itself
is NOT modified: it runs nightly as Step 8 of daily_universe_pipeline-2.py.

Run with the venv interpreter:
  /home/tom/venv/bin/python ShortInterrest_backfill_AddON.py \
      -i ../universe_finder/data/nasdaq_symbols_data_priced_2026-05-14.tsv

  # validate against a file the live addon already enriched
  /home/tom/venv/bin/python ShortInterrest_backfill_AddON.py \
      -i ../universe_finder/data/stocks_universe_2026-08-11.tsv --compare-to same

Output: <input_stem>_SIback.tsv next to the input. Inputs are never modified.
"""

import argparse
import calendar
import glob as globmod
import json
import logging
import os
import re
import sys
from bisect import bisect_left
from datetime import date, datetime, timedelta

import pandas as pd
import requests

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

# The shared engine. Anything that turns inputs into a column value lives here.
from ShortInterrest_AddON import (  # noqa: E402
    FINRA_SHVOL_URL,
    FINRA_SI_URL,
    HTTP_HEADERS,
    HTTP_TIMEOUT,
    NASDAQ_REGSHO_URL,
    OUTPUT_COLUMNS,
    REGSHO_DAYS,
    SHVOL_DAYS,
    borrow_history_metrics,
    cached_download,
    compute_symbol_metrics,
    http_get_text,
    regsho_flag_and_streak,
    to_float,
    weekdays_back,
    _parse_finra_records,
)
import ShortInterrest_AddON as _addon  # noqa: E402  (network borrow escape hatch)

log = logging.getLogger("short_interest")

# ----------------------------------------------------------------- constants
FINRA_DATASET = 'consolidatedShortInterest'   # the OTC 'standardized' set
                                              # returns [] for every cached
                                              # call on disk; skipped.
FINRA_PAGE_LIMIT = 5000
FINRA_MAX_PAGES = 20

# FINRA disseminates a settlement on the 7th business day after it. Validated
# 7/7 against the production series (see the table in the module docs of the
# plan): the 2026-07-09 -> 2026-07-10 flip only lands with a holiday-aware
# calendar, because Friday 2026-07-03 was the observed Independence Day.
FINRA_PUBLICATION_LAG_BDAYS = 7

# Pipeline sentinel for "IBKR gave us nothing". A 44444 close would otherwise
# produce an absurd SI_DollarADV.
PIPELINE_SENTINEL = 44444.0

# An iBorrowDesk cache file older than this (relative to as_of) no longer
# describes as_of, so the borrow block is left blank rather than backdated.
IBD_MAX_STALENESS_DAYS = 5

# Nasdaq posts nasdaqth{D}.txt too late for a 20:30 ET run on D, so the newest
# threshold list a run dated D can see is D-1. Evidence: every cached file's
# mtime is exactly one day after its own date (nasdaqth20260810 downloaded
# 2026-08-11, nasdaqth20260811 downloaded 2026-08-12, and so on without
# exception). Anchoring the walk at as_of instead would read a list that was
# not yet public — real lookahead, and it showed up as a systematic +1 on
# RegSHO_ConsecDays when validating against 2026-08-11.
# The FINRA short-volume file has NO such lag: CNMSshvol{D}.txt is downloaded
# on D itself, which is why SVR_* reproduces exactly with an as_of anchor.
REGSHO_PUBLICATION_LAG_DAYS = 1

_BORROW_COLUMNS = {
    'CTB_FeePct', 'CTB_RebatePct', 'CTB_FeeDelta5d', 'Borrow_Available',
    'Borrow_AvailDelta5dPct', 'Borrow_UtilProxy', 'Borrow_Source',
}
_SI_COLUMNS = {
    'SI_Source', 'SI_SettlementDate', 'SI_SharesShort', 'SI_PrevSharesShort',
    'SI_ChangePct', 'SI_PctFloat', 'SI_DTC', 'SI_DollarADV',
}
_DERIVED_COLUMNS = {'SqueezeFlags', 'SqueezeScore', 'SI_DataCoverage'}

_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')


def setup_logging(log_path: str | None = None) -> None:
    """Configure the named 'short_interest' logger.

    Deliberately does NOT reuse ShortInterrest_AddON.setup_logging, which calls
    basicConfig(force=True) and would wipe an importing process's handlers.
    """
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    log.addHandler(stream)
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        handler = logging.FileHandler(log_path)
        handler.setFormatter(fmt)
        log.addHandler(handler)


# ------------------------------------------------------- business-day calendar
class BusinessCalendar:
    """NYSE trading days over a window, for settlement/publication arithmetic.

    Holiday-awareness is a correctness requirement, not a nicety: a holiday
    inside the 7-business-day publication window shifts dissemination by a day
    and would otherwise pick the wrong settlement for files near a boundary.
    """

    def __init__(self, start: date, end: date):
        self.days = self._trading_days(start, end)
        self._set = set(self.days)

    @staticmethod
    def _trading_days(start: date, end: date) -> list[date]:
        try:
            import pandas_market_calendars as mcal
            sched = mcal.get_calendar('NYSE').schedule(
                start_date=start.isoformat(), end_date=end.isoformat())
            return [ts.date() for ts in sched.index]
        except Exception as exc:  # pragma: no cover - env-dependent
            log.warning(f"pandas_market_calendars unavailable ({exc}); falling "
                        "back to weekday arithmetic — settlement selection may "
                        "be off by a day around market holidays.")
            out, d = [], start
            while d <= end:
                if d.weekday() < 5:
                    out.append(d)
                d += timedelta(days=1)
            return out

    def roll_back(self, d: date) -> date:
        """The latest trading day <= d."""
        i = bisect_left(self.days, d)
        if i < len(self.days) and self.days[i] == d:
            return d
        return self.days[i - 1] if i > 0 else d

    def add(self, d: date, n: int) -> date | None:
        """The date n trading days after d (d itself need not be a trading day)."""
        i = bisect_left(self.days, d)
        if i < len(self.days) and self.days[i] == d:
            target = i + n
        else:
            target = i + n - 1   # days[i] is already 1 business day after d
        return self.days[target] if 0 <= target < len(self.days) else None


def settlement_candidates(cal: BusinessCalendar, start: date, end: date) -> list[date]:
    """FINRA settlement dates in [start, end]: the 15th and each month-end,
    each rolled back to a trading day."""
    out: set[date] = set()
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for day in (15, calendar.monthrange(y, m)[1]):
            d = cal.roll_back(date(y, m, day))
            if start <= d <= end:
                out.add(d)
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return sorted(out)


def resolve_settlement_date(as_of: date) -> date | None:
    """The newest FINRA settlement whose publication date <= as_of."""
    cal = BusinessCalendar(as_of - timedelta(days=200), as_of + timedelta(days=30))
    best = None
    for settle in settlement_candidates(cal, as_of - timedelta(days=120), as_of):
        published = cal.add(settle, FINRA_PUBLICATION_LAG_BDAYS)
        if published is not None and published <= as_of:
            best = settle
    if best is None:
        log.warning(f"No FINRA settlement published on or before {as_of}.")
    else:
        log.info(f"as-of {as_of}: FINRA settlement {best} "
                 f"(published {cal.add(best, FINRA_PUBLICATION_LAG_BDAYS)}).")
    return best


# --------------------------------------------- 1. FINRA short interest (as-of)
def fetch_finra_short_interest_asof(cache_dir: str, settlement: date) -> dict[str, dict]:
    """Whole-market FINRA short interest for one settlement date.

    settlementDate is a partition key, so the API accepts it only with an EQUAL
    CompareFilter — which is exactly what makes historical queries possible.
    One paginated pull covers every symbol (~21k rows), so the cache is keyed by
    settlement date and shared across every input file resolving to it.

    NOTE the cache lives in finra_si_asof/, NOT the live addon's finra_si/,
    whose key is {dataset}_{fetch_date}_{sha1(symbols)} — no settlement filter
    in the key, so reading it here would silently return today's records.
    """
    cache_path = os.path.join(cache_dir, 'finra_si_asof',
                              f"{FINRA_DATASET}_settle_{settlement.isoformat()}.json")
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 0:
        with open(cache_path) as fh:
            records = json.load(fh)
        log.info(f"FINRA SI settle {settlement}: {len(records)} records (cached).")
    else:
        records = []
        for page in range(FINRA_MAX_PAGES):
            body = {
                'limit': FINRA_PAGE_LIMIT,
                'offset': page * FINRA_PAGE_LIMIT,
                'compareFilters': [{'compareType': 'EQUAL',
                                    'fieldName': 'settlementDate',
                                    'fieldValue': settlement.isoformat()}],
            }
            try:
                resp = requests.post(
                    FINRA_SI_URL.format(dataset=FINRA_DATASET), json=body,
                    timeout=HTTP_TIMEOUT,
                    headers={**HTTP_HEADERS, 'Accept': 'application/json',
                             'Content-Type': 'application/json'})
            except requests.RequestException as exc:
                log.warning(f"FINRA SI settle {settlement} page {page} failed: {exc}")
                return {}
            if resp.status_code == 204:
                break
            if resp.status_code != 200:
                log.warning(f"FINRA SI settle {settlement} page {page}: "
                            f"HTTP {resp.status_code} — {resp.text[:200]}")
                return {}
            try:
                chunk = resp.json()
            except ValueError:
                log.warning(f"FINRA SI settle {settlement} page {page}: not JSON.")
                return {}
            if not chunk:
                break
            records.extend(chunk)
            if len(chunk) < FINRA_PAGE_LIMIT:
                break
        else:
            log.warning(f"FINRA SI settle {settlement}: hit the {FINRA_MAX_PAGES}"
                        "-page cap; result may be truncated.")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, 'w') as fh:
            json.dump(records, fh)
        log.info(f"FINRA SI settle {settlement}: {len(records)} records downloaded.")

    parsed = _parse_finra_records(records, 'finra')
    log.info(f"FINRA SI settle {settlement}: {len(parsed)} symbols parsed.")
    return parsed


# ---------------------------------------- 2. FINRA daily short volume (as-of)
def fetch_short_volume_history_asof(cache_dir: str,
                                    as_of: date) -> dict[str, list[tuple[str, float]]]:
    """Per-symbol [(date, SVR%), ...] over the trading days ending at as_of.

    Mirrors ShortInterrest_AddON.fetch_short_volume_history; the only change is
    the window anchor. The on-disk cache is already data-date-keyed and is
    shared with the live script.
    """
    per_symbol: dict[str, list[tuple[str, float]]] = {}
    found = 0
    for d in weekdays_back(as_of, SHVOL_DAYS + 4):
        if found >= SHVOL_DAYS:
            break
        ymd = d.strftime('%Y%m%d')
        path = os.path.join(cache_dir, 'shvol', f"CNMSshvol{ymd}.txt")
        text = cached_download(path, lambda u=FINRA_SHVOL_URL.format(ymd=ymd): http_get_text(u))
        if not text:
            continue  # holiday, or the file was never posted
        found += 1
        for line in text.splitlines():
            parts = line.strip().split('|')
            if len(parts) < 5 or parts[0] == 'Date':
                continue
            short_v, total_v = to_float(parts[2]), to_float(parts[4])
            if short_v is None or not total_v:
                continue
            sym = parts[1].strip().upper()
            per_symbol.setdefault(sym, []).append(
                (d.isoformat(), round(short_v / total_v * 100.0, 2)))
    for series in per_symbol.values():
        series.sort()
    log.info(f"Short-volume files loaded: {found} day(s) ending {as_of}, "
             f"{len(per_symbol)} symbols.")
    return per_symbol


# ------------------------------------- 5. Nasdaq Reg SHO threshold list (as-of)
def fetch_regsho_membership_asof(cache_dir: str, as_of: date,
                                 lag_days: int = REGSHO_PUBLICATION_LAG_DAYS
                                 ) -> list[tuple[str, set[str]]]:
    """[(date_iso, {threshold-list symbols})], newest first.

    The window ends at as_of - lag_days, because the list dated as_of is not
    posted in time for the 20:30 ET run on as_of. Nasdaq actually posts it late
    that evening (~23:00), so a run that overran could catch it: on 2026-07-06
    the pipeline ran until 23:43 and did, which is the one night lag_days=0
    reproduces and lag_days=1 does not. Every other night in the series shows
    the D+1 pattern.
    """
    lists: list[tuple[str, set[str]]] = []
    newest = as_of - timedelta(days=lag_days)
    for d in weekdays_back(newest, REGSHO_DAYS + 4):
        if len(lists) >= REGSHO_DAYS:
            break
        ymd = d.strftime('%Y%m%d')
        path = os.path.join(cache_dir, 'regsho', f"nasdaqth{ymd}.txt")
        text = cached_download(path, lambda u=NASDAQ_REGSHO_URL.format(ymd=ymd): http_get_text(u))
        if text is None:
            continue
        if not text.lstrip().startswith('Symbol|'):
            log.info(f"Reg SHO {ymd}: unexpected file format — skipping day.")
            continue
        symbols: set[str] = set()
        for line in text.splitlines():
            parts = line.strip().split('|')
            if len(parts) < 3 or parts[0] in ('Symbol', '') or parts[0].startswith('#'):
                continue
            symbols.add(parts[0].strip().upper())
        lists.append((d.isoformat(), symbols))
    log.info(f"Reg SHO threshold lists loaded: {len(lists)} day(s) ending "
             f"{newest} (as-of {as_of} minus the {lag_days}-day posting lag).")
    return lists


# ------------------------------------------------ 4. iBorrowDesk history (as-of)
_IBD_INDEX: dict[str, dict[str, list[tuple[date, str]]]] = {}


def _iborrowdesk_index(cache_dir: str) -> dict[str, list[tuple[date, str]]]:
    """{SYMBOL: [(fetch_date, path), ...] sorted} over the cached JSONs."""
    if cache_dir in _IBD_INDEX:
        return _IBD_INDEX[cache_dir]
    index: dict[str, list[tuple[date, str]]] = {}
    for path in globmod.glob(os.path.join(cache_dir, 'iborrowdesk', '*.json')):
        stem = os.path.splitext(os.path.basename(path))[0]
        sym, _, stamp = stem.rpartition('_')
        if not sym:
            continue
        try:
            fetched = datetime.strptime(stamp, '%Y-%m-%d').date()
        except ValueError:
            continue
        index.setdefault(sym.upper(), []).append((fetched, path))
    for entries in index.values():
        entries.sort()
    log.info(f"iBorrowDesk cache index: {len(index)} symbols.")
    _IBD_INDEX[cache_dir] = index
    return index


def fetch_iborrowdesk_asof(symbol: str, cache_dir: str, as_of: date,
                           allow_network: bool = False) -> dict | None:
    """Borrow history truncated to entries on or before as_of, or None.

    The live endpoint returns HTTP 403 now, so this is cache-first: pick the
    cached snapshot whose fetch date is >= as_of and closest to it (its `daily`
    array then certainly spans as_of) and slice. The result goes to the
    unchanged borrow_history_metrics.
    """
    entries = _iborrowdesk_index(cache_dir).get(symbol.upper(), [])
    data = None
    later = [e for e in entries if e[0] >= as_of]
    chosen = later[0][1] if later else (entries[-1][1] if entries else None)
    if chosen:
        try:
            with open(chosen) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
    if data is None and allow_network:
        data = _addon.fetch_iborrowdesk(symbol, cache_dir)
    if not data:
        return None

    cutoff = as_of.isoformat()
    daily = [e for e in (data.get('daily') or [])
             if str(e.get('date') or '')[:10] <= cutoff]
    if not daily:
        return None
    # A snapshot that stops well before as_of describes a different day; blank
    # is more honest than a backdated value.
    newest = max(str(e.get('date') or '')[:10] for e in daily)
    try:
        gap = (as_of - datetime.strptime(newest, '%Y-%m-%d').date()).days
    except ValueError:
        return None
    if gap > IBD_MAX_STALENESS_DAYS:
        return None
    return {'daily': daily}


# ------------------------------------------------------------- the entry point
def enrich_dataframe_asof(df: pd.DataFrame, as_of: date,
                          cache_dir: str | None = None,
                          skip_borrow_history: bool = False,
                          allow_network_borrow: bool = False,
                          regsho_lag_days: int = REGSHO_PUBLICATION_LAG_DAYS
                          ) -> pd.DataFrame:
    """Append the 24 SI_*/SVR_*/CTB_*/Borrow_*/RegSHO_*/Squeeze columns as of `as_of`.

    Same contract as ShortInterrest_AddON.enrich_dataframe: `df` needs a
    'Symbol' column, 'Float_M' (millions) and 'LastDailyClosePrice' are used
    when present, values may be strings, blank-Symbol rows are dropped, and the
    caller's frame is not mutated. Columns land at the right edge in
    OUTPUT_COLUMNS order (or in place if they already exist).
    """
    if 'Symbol' not in df.columns:
        raise ValueError("DataFrame has no 'Symbol' column.")
    cache_dir = cache_dir or os.path.join(_SCRIPT_DIR, 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    df = df[df['Symbol'].notna()].copy()
    df = df[df['Symbol'].astype(str).str.strip() != ''].copy()
    symbols = sorted({str(s).strip().upper() for s in df['Symbol']})
    log.info(f"{len(symbols)} unique symbols to enrich as of {as_of}")

    settlement = resolve_settlement_date(as_of)
    si_all = fetch_finra_short_interest_asof(cache_dir, settlement) if settlement else {}
    si_map = {s: si_all[s] for s in symbols if s in si_all}
    log.info(f"FINRA SI matched {len(si_map)}/{len(symbols)} symbols "
             f"({100.0 * len(si_map) / max(len(symbols), 1):.0f}%). "
             "No yfinance fallback by design (today-only snapshot).")

    svr_map = fetch_short_volume_history_asof(cache_dir, as_of)
    regsho_lists = fetch_regsho_membership_asof(cache_dir, as_of, regsho_lag_days)

    # First row per symbol, in one pass (the live addon re-scans the frame per
    # symbol, which is O(n^2) and painful on the 7k-row priced files).
    first_row: dict[str, pd.Series] = {}
    for _, row in df.iterrows():
        key = str(row['Symbol']).strip().upper()
        if key not in first_row:
            first_row[key] = row

    metrics: dict[str, dict] = {}
    for sym in symbols:
        row = first_row[sym]
        float_m = to_float(row.get('Float_M'))
        if float_m is not None and float_m == PIPELINE_SENTINEL:
            float_m = None
        float_shares = float_m * 1e6 if float_m else None
        last_close = to_float(row.get('LastDailyClosePrice'))
        if last_close is not None and last_close == PIPELINE_SENTINEL:
            last_close = None

        if skip_borrow_history:
            hist = borrow_history_metrics(None)
        else:
            hist = borrow_history_metrics(
                fetch_iborrowdesk_asof(sym, cache_dir, as_of, allow_network_borrow))

        metrics[sym] = compute_symbol_metrics(
            symbol=sym,
            float_shares=float_shares,
            last_close=last_close,
            si=si_map.get(sym),
            svr_series=svr_map.get(sym),
            ftp_borrow=None,          # IBKR FTP is a snapshot; no history exists
            borrow_hist=hist,
            regsho=regsho_flag_and_streak(sym, regsho_lists),
        )

    for col in OUTPUT_COLUMNS:
        df[col] = [metrics[str(s).strip().upper()][col] for s in df['Symbol']]

    covered = sum(1 for m in metrics.values() if m['SI_DataCoverage'] != 'none')
    scored = sum(1 for m in metrics.values() if (m['SqueezeScore'] or 0) > 0)
    borrow = sum(1 for m in metrics.values() if m['Borrow_Source'])
    log.info(f"Symbols: {len(symbols)} | with any data: {covered} | "
             f"with borrow: {borrow} | score > 0: {scored}")
    return df


# ------------------------------------------------------------ validation mode
def _norm(value) -> str:
    if value is None:
        return ''
    text = str(value).strip()
    return '' if text.lower() in ('', 'nan', 'none', '<na>') else text


def _values_agree(a, b) -> bool:
    na, nb = _norm(a), _norm(b)
    if na == nb:
        return True
    fa, fb = to_float(na), to_float(nb)
    if fa is not None and fb is not None:
        return abs(fa - fb) <= 1e-6 * max(1.0, abs(fa), abs(fb))
    return False


def compare_frames(backfilled: pd.DataFrame, reference: pd.DataFrame) -> bool:
    """Report per-column agreement on the 24 SI columns. Returns True on PASS.

    Two mismatch classes are expected and reported separately rather than
    counted as failures:
      - rows the live addon sourced from yfinance (backfill leaves them blank)
      - the borrow block, whose provenance differs between a live snapshot and
        a sliced history
    """
    missing = [c for c in OUTPUT_COLUMNS if c not in reference.columns]
    if missing:
        log.error(f"Reference file has no SI columns ({len(missing)} missing) — "
                  "compare against a file the live addon already enriched.")
        return False

    ref = reference.set_index(reference['Symbol'].astype(str).str.strip().str.upper())
    got = backfilled.set_index(backfilled['Symbol'].astype(str).str.strip().str.upper())
    shared = [s for s in got.index if s in ref.index]
    log.info(f"Comparing {len(shared)} symbols "
             f"(backfill {len(got)}, reference {len(ref)}).")

    yf_rows = {s for s in shared if _norm(ref.at[s, 'SI_Source']).lower() == 'yfinance'}
    # SqueezeFlags/Score/Coverage are a function of the SI and borrow blocks, so
    # a difference there is explained whenever one of those inputs legitimately
    # differs -- in EITHER direction. The backfill is often the richer side: a
    # live run that lost an iBorrowDesk fetch to a 403/444 wrote blanks, while
    # the cached history still yields a (point-in-time correct) value.
    borrow_differs = {
        s for s in shared
        if any(not _values_agree(got.at[s, c], ref.at[s, c]) for c in _BORROW_COLUMNS)
    }
    total_mismatch = 0
    print(f"\n{'column':<24} {'match':>7} {'expected':>9} {'MISMATCH':>9}")
    print('-' * 52)
    for col in OUTPUT_COLUMNS:
        match = expected = mismatch = 0
        examples = []
        for sym in shared:
            a, b = got.at[sym, col], ref.at[sym, col]
            if _values_agree(a, b):
                match += 1
            elif col in _BORROW_COLUMNS:
                expected += 1
            elif sym in yf_rows and (col in _SI_COLUMNS or col in _DERIVED_COLUMNS):
                expected += 1
            elif col in _DERIVED_COLUMNS and sym in borrow_differs:
                expected += 1   # score/flags shifted by the borrow block
            else:
                mismatch += 1
                if len(examples) < 3:
                    examples.append(f"{sym}: backfill={_norm(a)!r} ref={_norm(b)!r}")
        total_mismatch += mismatch
        flag = '  <-- ' + '; '.join(examples) if examples else ''
        print(f"{col:<24} {match:>7} {expected:>9} {mismatch:>9}{flag}")

    print('-' * 52)
    print(f"yfinance-sourced reference rows (blank in backfill): {len(yf_rows)}")
    if total_mismatch == 0:
        print("\nRESULT: PASS — every comparable value reproduces the live addon.\n")
        return True
    print(f"\nRESULT: FAIL — {total_mismatch} unexplained mismatches.\n")
    return False


# ----------------------------------------------------------------------- CLI
def as_of_from_filename(path: str) -> date | None:
    hit = _DATE_RE.search(os.path.basename(path))
    if not hit:
        return None
    try:
        return datetime.strptime(hit.group(1), '%Y-%m-%d').date()
    except ValueError:
        return None


def process_file(path: str, as_of: date, args) -> bool:
    out_dir = args.output_dir or os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    out_path = os.path.join(out_dir, f"{stem}_SIback.tsv")
    if os.path.exists(out_path) and not args.overwrite and not args.compare_to:
        log.info(f"Skipping {os.path.basename(path)} — {os.path.basename(out_path)} "
                 "exists (use --overwrite).")
        return True

    # dtype=str + keep_default_na=False preserves the upstream formatting of the
    # base columns byte-for-byte (the live addon's CLI reformats 1.7200 -> 1.72)
    # and stops pandas coercing the ticker 'NA' (Nano Labs) into NaN, which
    # would drop that row entirely.
    df = pd.read_csv(path, sep='\t', dtype=str, keep_default_na=False)
    if 'Symbol' not in df.columns:
        log.error(f"{path}: no 'Symbol' column — skipping.")
        return False
    log.info(f"Input: {path} ({len(df)} rows, {len(df.columns)} cols), as-of {as_of}")

    enriched = enrich_dataframe_asof(
        df, as_of, cache_dir=args.cache_dir,
        skip_borrow_history=args.skip_borrow_history,
        allow_network_borrow=args.allow_network_borrow,
        regsho_lag_days=args.regsho_lag_days)

    os.makedirs(out_dir, exist_ok=True)
    enriched.to_csv(out_path, sep='\t', index=False)
    log.info(f"Saved {out_path} ({len(enriched)} rows, {len(enriched.columns)} cols)")

    if args.compare_to:
        ref_path = path if args.compare_to == 'same' else args.compare_to
        reference = pd.read_csv(ref_path, sep='\t', dtype=str, keep_default_na=False)
        log.info(f"Comparing against {ref_path}")
        return compare_frames(enriched, reference)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description=('Backfill short-interest / squeeze metrics onto historical '
                     'universe TSVs, point-in-time correct. Output is '
                     '<input_stem>_SIback.tsv.'),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', '-i', required=True, nargs='+', metavar='FILE',
                        dest='input_files',
                        help='Input .tsv file(s) with a Symbol column; glob '
                             'patterns are accepted.')
    parser.add_argument('--as-of', metavar='YYYY-MM-DD', default=None,
                        help='Reference date. Default: parsed from each filename.')
    parser.add_argument('--output-dir', '-o', metavar='DIR', default=None,
                        help="Output directory (default: each input's directory).")
    parser.add_argument('--cache-dir', metavar='DIR', default=None,
                        help='Raw-download cache (default: ./cache next to this script).')
    parser.add_argument('--log', '-l', metavar='FILE', dest='log_file', default=None,
                        help='Optional log file (appended).')
    parser.add_argument('--skip-borrow-history', action='store_true',
                        help='Skip the iBorrowDesk lookup entirely.')
    parser.add_argument('--allow-network-borrow', action='store_true',
                        help='Allow live iBorrowDesk HTTP when the cache misses '
                             '(the endpoint currently returns 403).')
    parser.add_argument('--regsho-lag-days', type=int, default=REGSHO_PUBLICATION_LAG_DAYS,
                        metavar='N',
                        help='Days to step back for the newest Reg SHO threshold '
                             f'list (default {REGSHO_PUBLICATION_LAG_DAYS}). Use 0 '
                             'only to reproduce a run that overran past ~23:00 ET, '
                             'e.g. the 2026-07-06 night.')
    parser.add_argument('--compare-to', metavar='FILE', default=None,
                        help="Validation mode: diff the 24 SI columns against "
                             "FILE, or 'same' to compare each input to itself.")
    parser.add_argument('--overwrite', action='store_true',
                        help='Redo inputs whose _SIback.tsv already exists.')
    args = parser.parse_args()

    setup_logging(args.log_file)

    paths: list[str] = []
    for pattern in args.input_files:
        hits = sorted(globmod.glob(pattern)) if any(c in pattern for c in '*?[') else [pattern]
        if not hits:
            log.warning(f"No files match {pattern!r}")
        paths.extend(hits)

    override = None
    if args.as_of:
        try:
            override = datetime.strptime(args.as_of, '%Y-%m-%d').date()
        except ValueError:
            log.error(f"--as-of {args.as_of!r} is not YYYY-MM-DD.")
            return 2

    jobs: list[tuple[date, str]] = []
    for path in paths:
        if not os.path.exists(path):
            log.error(f"{path}: not found — skipping.")
            continue
        as_of = override or as_of_from_filename(path)
        if as_of is None:
            # Never silently fall back to today; that would defeat the point.
            log.error(f"{path}: no YYYY-MM-DD in the filename and no --as-of "
                      "given — skipping.")
            continue
        jobs.append((as_of, path))

    if not jobs:
        log.error("Nothing to do.")
        return 2

    # Oldest first, so files sharing a FINRA settlement download it once.
    jobs.sort()
    failures = 0
    for as_of, path in jobs:
        try:
            if not process_file(path, as_of, args):
                failures += 1
        except Exception as exc:
            log.exception(f"{path}: failed ({exc!r}) — continuing.")
            failures += 1

    log.info(f"Done: {len(jobs) - failures}/{len(jobs)} file(s) succeeded.")
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
