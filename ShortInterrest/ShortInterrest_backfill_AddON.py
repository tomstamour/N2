#!/usr/bin/env python3
"""
ShortInterrest_backfill_AddON.py — historical sister of ShortInterrest_AddON.py.

The live addon anchors every fetch on eastern_today(), so it can only describe
*now*. This script computes the SAME 24 columns, in the SAME order, with the
SAME formulas, but **as of a historical date**, so universe TSVs written before
Step 8 existed can be enriched retroactively.

Point-in-time by construction: for a run date R it uses only data that had
actually been PUBLISHED by R. Nothing here can see the future, so a backfilled
row carries no lookahead advantage over a live row sitting next to it.

WHICH DATES GET FETCHED  (--days-prior)
---------------------------------------
The universe cron fires at 20:30 ET EVERY CALENDAR DAY and names its output
after the run date, so "N nights back" is a CALENDAR step while the windows it
produces are SESSION walks. Everything is derived from a reconstructed run date:

    run_date = <row date> - N calendar days   # which nightly run we are replaying
    anchor   = last XNYS session <= run_date  # SVR window end + FINRA settlement
    reg_end  = last XNYS session <  run_date  # newest Reg SHO threshold list

The two lags are NOT symmetric, and both are confirmed by the download
timestamps of the files sitting in cache/ (12 of 12 each):

    CNMSshvol{D}.txt  fetched on D itself, 20:33-21:55  ->  anchor is  <= R
    nasdaqth{D}.txt   fetched on D+1                    ->  reg_end is <  R

That is the same contract market_state_calculator.py offers, and it was checked
against the live addon over all 237 calendar days of 2026 to date:

    N=0 on D    == what ShortInterrest_AddON.py fetched on D    0 mismatches
    N=1 on D+1  == N=0 on D                                     0 mismatches
    anchor      == MSC Sessions.resolve(date, N), N in {0,1}    identical

N DEFAULTS TO 1. A PR that arrived on D+1 was traded against the universe file
the 20:30 run of D produced, so --days-prior 1 is the leak-free reading and the
one to use for training backfills. --days-prior 0 reproduces a universe file's
own run, and is REQUIRED for --compare-to.

For N>=2 this counts nightly RUNS, not sessions: on a weekend it steps one
calendar day at a time where MSC would step a whole session. N=0 and N=1 - the
modes this script exists for - are identical under both readings.

Session walks use the XNYS calendar, so a holiday is never requested. The live
addon walks weekdays and lets the holiday URL 404 instead; both land on the same
dates (verified above), this one just does not pay for the failed round-trip.

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
  # leak-free backfill of a universe file (N=1: what the PREVIOUS night saw)
  /home/tom/venv/bin/python ShortInterrest_backfill_AddON.py \
      -i ../universe_finder/data/nasdaq_symbols_data_priced_2026-05-14.tsv

  # per-row anchors: every PR row resolved from its own ArrivalDate
  /home/tom/venv/bin/python ShortInterrest_backfill_AddON.py \
      -i ../price_stats_loader/outputs/07_PRstats_....tsv --date-col ArrivalDate

  # show the resolved dates and exit - no fetching, no output file
  /home/tom/venv/bin/python ShortInterrest_backfill_AddON.py \
      -i ../universe_finder/data/stocks_universe_2026-08-11.tsv --check-dates

  # validate against a file the live addon already enriched (needs N=0)
  /home/tom/venv/bin/python ShortInterrest_backfill_AddON.py \
      -i ../universe_finder/data/stocks_universe_2026-08-11.tsv \
      --days-prior 0 --compare-to same

  # prove the contract: these two must produce identical SI columns
  ... -i .../stocks_universe_2026-08-10.tsv --days-prior 0 -o /tmp/si_n0
  ... -i .../stocks_universe_2026-08-11.tsv --days-prior 1 -o /tmp/si_n1

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

# Same rule for the IBKR rate cache: a symbol whose FEE_RATE bars stop well
# before an anchor is not quoted on that anchor, so it goes blank. The bars are
# dense (one per session) where iBorrowDesk's array is sparse, so this only
# trips on a genuinely delisted/halted name rather than on thin quoting.
IBKR_RATES_MAX_STALENESS_DAYS = 5

# Subdirectory of --cache-dir holding {SYMBOL}.json written by
# ibkr_rates_fetch_AddON.py. Rates on disk are ALREADY IN PERCENT.
IBKR_RATES_SUBDIR = 'ibkr_rates'

# Nasdaq posts nasdaqth{D}.txt too late for a 20:30 ET run on D, so the newest
# threshold list a run dated D can see is the PREVIOUS SESSION's. Evidence:
# every cached file's mtime is exactly one day after its own date
# (nasdaqth20260810 downloaded 2026-08-11, ...20260811 on 2026-08-12, 12 of 12
# with no exception). Anchoring the walk at the run date instead would read a
# list that was not yet public - real lookahead, and it showed up as a
# systematic +1 on RegSHO_ConsecDays when validating against 2026-08-11.
#
# Counted in SESSIONS, not calendar days. Those agree on an ordinary Tuesday
# and disagree across a holiday: a run on Tue 2026-07-07 must reach back past
# the closed Fri 2026-07-03 to Thu 2026-07-02, which "minus one day" does not.
#
# The FINRA short-volume file has NO such lag: CNMSshvol{D}.txt is downloaded on
# D itself (20:33-21:55, 12 of 12), which is why the SVR window ENDS ON the
# run date's session while Reg SHO ends one session earlier.
REGSHO_PUBLICATION_LAG_SESSIONS = 1

# "N nights back". 1 = the run that a PR arriving the next morning was traded
# against, i.e. the leak-free reading; see the module docstring.
DEFAULT_DAYS_PRIOR = 1

# Extra sessions to scan past the window length, absorbing a session for which
# a publisher simply never posted a file. Holidays no longer consume this - the
# XNYS walk never asks for them - so it is pure margin against a missing file.
SESSION_SCAN_SLACK = 4

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

    def index_on_or_before(self, d: date) -> int | None:
        """Position of the latest session <= d, or None if d predates the index."""
        i = bisect_left(self.days, d)
        if i < len(self.days) and self.days[i] == d:
            return i
        return i - 1 if i > 0 else None

    def resolve(self, d: date, days_prior: int) -> date | None:
        """Anchor session for date d - market_state_calculator.Sessions.resolve.

            days_prior == 0  -> the last session ON OR BEFORE d
            days_prior >= 1  -> the days_prior-th session STRICTLY BEFORE d

        The second clause matters only when d is not itself a session. Rolling a
        Sunday back to Friday has ALREADY stepped back one session, so shifting
        again would land on Thursday - one session staler than what the Friday
        20:00 run actually produced. Decrementing here makes weekend and holiday
        dates agree with weekdays: --days-prior 1 always means "the last session
        that closed before this calendar date".

        Kept byte-identical to MSC's version on purpose: the two scripts are
        expected to resolve the same anchor for the same (date, N), and that
        equivalence is asserted for N=0 and N=1 in the docstring's check.
        """
        i = self.index_on_or_before(d)
        if i is None:
            return None
        if days_prior >= 1 and self.days[i] != d:
            days_prior -= 1
        j = i - days_prior
        return self.days[j] if 0 <= j < len(self.days) else None

    def back(self, end_session: date, n: int) -> list[date]:
        """The n sessions ending at end_session, NEWEST FIRST.

        Replaces the live addon's weekdays_back() for the SVR and Reg SHO walks:
        same dates, but a holiday is never requested in the first place.
        """
        i = self.index_on_or_before(end_session)
        if i is None:
            return []
        return list(reversed(self.days[max(0, i - n + 1):i + 1]))


def build_calendar(dates) -> BusinessCalendar:
    """One XNYS index wide enough for every window any of `dates` will need.

    Built once per input file and threaded through: resolve_settlement_date used
    to construct its own calendar per call, which is invisible for one as-of and
    pathological in per-row mode, where a PR file resolves dozens of them.

    400 days of back-margin covers the 120-day settlement candidate scan with
    room to spare; 30 forward covers the publication lag lookahead.
    """
    lo, hi = min(dates), max(dates)
    return BusinessCalendar(lo - timedelta(days=400), hi + timedelta(days=30))


def resolve_windows(cal: BusinessCalendar, row_date: date, days_prior: int,
                    regsho_lag_sessions: int = REGSHO_PUBLICATION_LAG_SESSIONS
                    ) -> tuple[date, date | None, date | None]:
    """(run_date, anchor, reg_end) for one input date.

    The cron fires every calendar day, so stepping back N nightly runs is a
    CALENDAR step; the windows those runs saw are SESSION walks. See the module
    docstring for the evidence behind the asymmetry between the two anchors.
    """
    run_date = row_date - timedelta(days=days_prior)
    return (run_date,
            cal.resolve(run_date, 0),                    # SVR end, FINRA basis
            cal.resolve(run_date, regsho_lag_sessions))  # newest Reg SHO list


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


def resolve_settlement_date(as_of: date,
                            cal: BusinessCalendar | None = None) -> date | None:
    """The newest FINRA settlement whose publication date <= as_of.

    `cal` is the shared per-file calendar; without one a private calendar is
    built, which keeps the function usable standalone.
    """
    if cal is None:
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
class ParseCache:
    """Per-input-file memo for the market-wide files.

    Consecutive anchors share 5 of their 6 short-volume files and 9 of their 10
    threshold lists, so per-row mode would otherwise re-parse the same files
    dozens of times. Scoped to ONE input file rather than module-global,
    because the short-volume rows are filtered down to that file's symbols.

    A value of None is a remembered miss (no file for that session), so
    membership must be tested with `in`, not truthiness.
    """

    def __init__(self, symbols):
        self.symbols: set[str] = set(symbols)
        self.shvol: dict[date, list[tuple[str, float]] | None] = {}
        self.regsho: dict[date, set[str] | None] = {}
        self.ibd: dict[str, dict | None] = {}
        self.ibkr: dict[str, dict | None] = {}


def _shvol_day(cache_dir: str, d: date,
               pc: ParseCache) -> list[tuple[str, float]] | None:
    """[(symbol, SVR%)] for one session in FILE ORDER, or None if not posted.

    File order is preserved rather than collapsed into a dict so that a symbol
    appearing twice in one file behaves exactly as it does in the live addon.
    """
    if d in pc.shvol:
        return pc.shvol[d]
    ymd = d.strftime('%Y%m%d')
    path = os.path.join(cache_dir, 'shvol', f"CNMSshvol{ymd}.txt")
    text = cached_download(path, lambda u=FINRA_SHVOL_URL.format(ymd=ymd): http_get_text(u))
    if not text:
        pc.shvol[d] = None
        return None
    rows: list[tuple[str, float]] = []
    for line in text.splitlines():
        parts = line.strip().split('|')
        if len(parts) < 5 or parts[0] == 'Date':
            continue
        sym = parts[1].strip().upper()
        if sym not in pc.symbols:
            continue
        short_v, total_v = to_float(parts[2]), to_float(parts[4])
        if short_v is None or not total_v:
            continue
        rows.append((sym, round(short_v / total_v * 100.0, 2)))
    pc.shvol[d] = rows
    return rows


def fetch_short_volume_history_asof(cache_dir: str, cal: BusinessCalendar,
                                    anchor: date, pc: ParseCache
                                    ) -> dict[str, list[tuple[str, float]]]:
    """Per-symbol [(date, SVR%), ...] over the sessions ending AT the anchor.

    Mirrors ShortInterrest_AddON.fetch_short_volume_history; the only changes
    are the window anchor and an XNYS walk in place of the weekday one. The
    on-disk cache is data-date-keyed and shared with the live script.

    Ends ON the anchor, not before it: CNMSshvol{D}.txt is published on D itself,
    in time for the 20:30 run. See the module docstring.
    """
    per_symbol: dict[str, list[tuple[str, float]]] = {}
    found = 0
    for d in cal.back(anchor, SHVOL_DAYS + SESSION_SCAN_SLACK):
        if found >= SHVOL_DAYS:
            break
        rows = _shvol_day(cache_dir, d, pc)
        if rows is None:
            continue  # the file was never posted for that session
        found += 1
        stamp = d.isoformat()
        for sym, pct in rows:
            per_symbol.setdefault(sym, []).append((stamp, pct))
    for series in per_symbol.values():
        series.sort()
    log.info(f"Short-volume: {found} session(s) ending {anchor}, "
             f"{len(per_symbol)} symbols.")
    return per_symbol


# ------------------------------------- 5. Nasdaq Reg SHO threshold list (as-of)
def _regsho_day(cache_dir: str, d: date, pc: ParseCache) -> set[str] | None:
    """{symbols on the threshold list} for one session, or None if unusable."""
    if d in pc.regsho:
        return pc.regsho[d]
    ymd = d.strftime('%Y%m%d')
    path = os.path.join(cache_dir, 'regsho', f"nasdaqth{ymd}.txt")
    text = cached_download(path, lambda u=NASDAQ_REGSHO_URL.format(ymd=ymd): http_get_text(u))
    if text is None:
        pc.regsho[d] = None
        return None
    if not text.lstrip().startswith('Symbol|'):
        log.info(f"Reg SHO {ymd}: unexpected file format - skipping session.")
        pc.regsho[d] = None
        return None
    symbols: set[str] = set()
    for line in text.splitlines():
        parts = line.strip().split('|')
        if len(parts) < 3 or parts[0] in ('Symbol', '') or parts[0].startswith('#'):
            continue
        symbols.add(parts[0].strip().upper())
    pc.regsho[d] = symbols
    return symbols


def fetch_regsho_membership_asof(cache_dir: str, cal: BusinessCalendar,
                                 reg_end: date, pc: ParseCache
                                 ) -> list[tuple[str, set[str]]]:
    """[(date_iso, {threshold-list symbols})], NEWEST FIRST, ending at reg_end.

    reg_end is already the lagged session (resolve_windows): the list dated the
    run date is not posted in time for the 20:30 ET run. Nasdaq posts it late
    that evening (~23:00), so a run that overran could catch it - on 2026-07-06
    the pipeline ran until 23:43 and did, which is the one night
    --regsho-lag-sessions 0 reproduces and 1 does not. Every other night in the
    series shows the next-day pattern.
    """
    lists: list[tuple[str, set[str]]] = []
    for d in cal.back(reg_end, REGSHO_DAYS + SESSION_SCAN_SLACK):
        if len(lists) >= REGSHO_DAYS:
            break
        symbols = _regsho_day(cache_dir, d, pc)
        if symbols is None:
            continue
        lists.append((d.isoformat(), symbols))
    log.info(f"Reg SHO: {len(lists)} session(s) ending {reg_end}.")
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
                           allow_network: bool = False,
                           pc: 'ParseCache | None' = None) -> dict | None:
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
        if pc is not None and chosen in pc.ibd:
            data = pc.ibd[chosen]          # same snapshot, many anchors
        else:
            try:
                with open(chosen) as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                data = None
            if pc is not None:
                pc.ibd[chosen] = data
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


def fetch_ibkr_rates_asof(symbol: str, cache_dir: str, as_of: date,
                          pc: 'ParseCache | None' = None) -> dict | None:
    """IBKR FEE_RATE/REBATE_RATE history truncated to bars on or before as_of.

    Returns {'daily': [{'date', 'fee', 'available'}], 'rebate': <as-of rebate>}
    or None. The `daily` list is shaped for the UNCHANGED
    ShortInterrest_AddON.borrow_history_metrics, which is what derives fee_now
    and fee_delta_5d — no formula is reimplemented here. `available` is
    deliberately absent: IBKR's shortableShares is a live snapshot with no
    historical whatToShow, so it cannot be reconstructed for a past date and a
    fabricated value would poison Borrow_UtilProxy.

    Point-in-time by construction: a FEE_RATE bar dated D is that session's
    value, so slicing to <= as_of can never read past the anchor.

    Cached rates are already in PERCENT (ibkr_rates_fetch_AddON.py scales by
    100 once, at write time).
    """
    key = symbol.upper()
    if pc is not None and key in pc.ibkr:
        data = pc.ibkr[key]
    else:
        path = os.path.join(cache_dir, IBKR_RATES_SUBDIR, f"{key}.json")
        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None
        if pc is not None:
            pc.ibkr[key] = data
    if not data:
        return None

    cutoff = as_of.isoformat()
    bars = [e for e in (data.get('daily') or [])
            if str(e.get('date') or '')[:10] <= cutoff]
    if not bars:
        return None
    newest = max(str(e.get('date') or '')[:10] for e in bars)
    try:
        gap = (as_of - datetime.strptime(newest, '%Y-%m-%d').date()).days
    except ValueError:
        return None
    if gap > IBKR_RATES_MAX_STALENESS_DAYS:
        return None

    # Only the fee half goes into the history dict; the rebate is a level, not a
    # series anything derives a delta from, so it rides alongside.
    daily = [{'date': str(e.get('date'))[:10], 'fee': e.get('fee')}
             for e in bars if e.get('fee') is not None]
    if not daily:
        return None
    rebate = None
    for entry in reversed(bars):
        if entry.get('rebate') is not None:
            rebate = entry['rebate']
            break
    return {'daily': daily, 'rebate': rebate}


def merge_borrow_hist(ibkr_hist: dict, ibd_hist: dict) -> dict:
    """Fee fields from IBKR, availability fields from iBorrowDesk.

    The two halves measure different quantities and are NEVER blended into one
    formula: ibkr_borrow_addOn.py warns that mixing iBorrowDesk's rounded
    availability with IBKR's precise values corrupts any delta spanning the
    seam, and the same reasoning applies in reverse to the fee series.
    IBKR's fee_delta_5d is also the better of the two — it is a true 5-session
    step over dense daily bars, where iBorrowDesk's indexes 6 records back in a
    sparse array, which on a thinly-quoted symbol can span months.
    """
    return {
        'fee_now': ibkr_hist['fee_now'],
        'fee_delta_5d': ibkr_hist['fee_delta_5d'],
        'available_now': ibd_hist['available_now'],
        'avail_delta_5d_pct': ibd_hist['avail_delta_5d_pct'],
        'util_proxy': ibd_hist['util_proxy'],
    }


# ------------------------------------------------------------- the entry point
def _blank_metrics(symbol: str) -> dict:
    """The all-empty column dict for a row whose anchor could not be resolved."""
    return compute_symbol_metrics(
        symbol=symbol, float_shares=None, last_close=None, si=None,
        svr_series=None, ftp_borrow=None,
        borrow_hist=borrow_history_metrics(None), regsho=(None, None))


def enrich_dataframe_perrow(df: pd.DataFrame, row_dates,
                            days_prior: int = DEFAULT_DAYS_PRIOR,
                            cal: BusinessCalendar | None = None,
                            cache_dir: str | None = None,
                            skip_borrow_history: bool = False,
                            allow_network_borrow: bool = False,
                            regsho_lag_sessions: int = REGSHO_PUBLICATION_LAG_SESSIONS,
                            float_column_name: str = 'Float_M',
                            lastClose_column_name: str = 'LastDailyClosePrice',
                            ibkr_rates: bool = False
                            ) -> pd.DataFrame:
    """Append the 24 columns, resolving an anchor PER ROW.

    `row_dates` is a sequence POSITIONALLY aligned with df's rows, holding a
    date (or None) each. Rows that resolve to the same (anchor, reg_end) pair
    are fetched once as a group, so a PR-stats file spanning three months costs
    one settlement pull per settlement and one parse per session file - not one
    per row.

    Same contract as before otherwise: 'Symbol' is required, the column named
    by `float_column_name` (default 'Float_M') in millions and
    'LastDailyClosePrice' are used when present, values may be strings,
    blank-Symbol rows are dropped, the caller's frame is not mutated,
    and the columns land in OUTPUT_COLUMNS order. Unlike the single-anchor path
    this reads each row's OWN float and close rather than the first row's for
    that symbol, which matters when a symbol appears on several dates.
    """
    if 'Symbol' not in df.columns:
        raise ValueError("DataFrame has no 'Symbol' column.")
    cache_dir = cache_dir or os.path.join(_SCRIPT_DIR, 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    row_dates = list(row_dates)
    if len(row_dates) != len(df):
        raise ValueError(f"row_dates has {len(row_dates)} entries for {len(df)} rows.")

    keep = [bool(v) and str(v).strip() != '' and str(v).strip().lower() != 'nan'
            for v in df['Symbol'].fillna('')]
    row_dates = [d for d, k in zip(row_dates, keep) if k]
    df = df[pd.Series(keep, index=df.index)].copy().reset_index(drop=True)

    syms = df['Symbol'].astype(str).str.strip().str.upper().tolist()
    symbols = sorted(set(syms))
    floats = (df[float_column_name].tolist() if float_column_name in df.columns
              else [None] * len(df))
    closes = (df[lastClose_column_name].tolist()
              if lastClose_column_name in df.columns else [None] * len(df))

    known = [d for d in row_dates if d is not None]
    if cal is None:
        cal = build_calendar(known) if known else build_calendar([date.today()])

    # group row positions by the window pair they resolve to
    groups: dict[tuple, list[int]] = {}
    for pos, d in enumerate(row_dates):
        key = (None, None)
        if d is not None:
            _run, anchor, reg_end = resolve_windows(cal, d, days_prior,
                                                    regsho_lag_sessions)
            if anchor is not None and reg_end is not None:
                key = (anchor, reg_end)
        groups.setdefault(key, []).append(pos)

    resolved = sorted(k for k in groups if k[0] is not None)
    unresolved = len(groups.get((None, None), []))
    log.info(f"{len(df)} row(s), {len(symbols)} unique symbol(s), "
             f"{len(resolved)} anchor group(s), days_prior={days_prior}"
             + (f", {unresolved} row(s) with no resolvable date" if unresolved else ""))

    pc = ParseCache(symbols)
    metrics: list[dict | None] = [None] * len(df)

    for anchor, reg_end in resolved:
        positions = groups[(anchor, reg_end)]
        grp_syms = sorted({syms[i] for i in positions})

        settlement = resolve_settlement_date(anchor, cal)
        si_all = fetch_finra_short_interest_asof(cache_dir, settlement) if settlement else {}
        si_map = {sym: si_all[sym] for sym in grp_syms if sym in si_all}
        svr_map = fetch_short_volume_history_asof(cache_dir, cal, anchor, pc)
        regsho_lists = fetch_regsho_membership_asof(cache_dir, cal, reg_end, pc)
        log.info(f"anchor {anchor} (Reg SHO to {reg_end}): {len(positions)} row(s), "
                 f"FINRA SI matched {len(si_map)}/{len(grp_syms)} "
                 f"({100.0 * len(si_map) / max(len(grp_syms), 1):.0f}%). "
                 "No yfinance fallback by design (today-only snapshot).")

        # both are per (symbol, anchor); the anchor is constant inside a group
        borrow: dict[str, dict] = {}
        rebate: dict[str, float | None] = {}
        regsho: dict[str, tuple] = {}
        for sym in grp_syms:
            ibd_hist = borrow_history_metrics(
                None if skip_borrow_history else
                fetch_iborrowdesk_asof(sym, cache_dir, anchor,
                                       allow_network_borrow, pc))
            if ibkr_rates:
                rates = fetch_ibkr_rates_asof(sym, cache_dir, anchor, pc)
                if rates:
                    # Same engine, different series: borrow_history_metrics is
                    # what turns the sliced bars into fee_now/fee_delta_5d.
                    borrow[sym] = merge_borrow_hist(
                        borrow_history_metrics({'daily': rates['daily']}),
                        ibd_hist)
                    rebate[sym] = rates['rebate']
                else:
                    borrow[sym] = ibd_hist
                    rebate[sym] = None
            else:
                borrow[sym] = ibd_hist
                rebate[sym] = None
            regsho[sym] = regsho_flag_and_streak(sym, regsho_lists)

        for pos in positions:
            sym = syms[pos]
            float_m = to_float(floats[pos])
            if float_m is not None and float_m == PIPELINE_SENTINEL:
                float_m = None
            last_close = to_float(closes[pos])
            if last_close is not None and last_close == PIPELINE_SENTINEL:
                last_close = None
            # The IBKR path carries the rebate through the `ftp_borrow`
            # snapshot slot, the only channel compute_symbol_metrics reads
            # CTB_RebatePct from. fee is left None there so it falls through to
            # borrow_hist['fee_now'], which is the same IBKR series; available
            # stays None because IBKR cannot serve it historically at all.
            # The dict is passed whenever IBKR had this symbol, even with a null
            # rebate, so that 'ibkr_api' is registered as the fee's provenance
            # (an all-None dict is still truthy, which is what that turns on).
            ftp_borrow = None
            history_label = 'iborrowdesk'
            if ibkr_rates and borrow[sym]['fee_now'] is not None:
                ftp_borrow = {'fee': None, 'rebate': rebate.get(sym),
                              'available': None}
                # borrow_hist is a merge: the fee half is IBKR's (already
                # credited via snapshot_label), the availability half is
                # iBorrowDesk's. Name the history after whichever actually
                # contributed, so a row carrying both reads
                # 'ibkr_api+iborrowdesk' and a fee-only row just 'ibkr_api'.
                if all(borrow[sym][k] is None for k in
                       ('available_now', 'avail_delta_5d_pct', 'util_proxy')):
                    history_label = 'ibkr_api'
            metrics[pos] = compute_symbol_metrics(
                symbol=sym,
                float_shares=float_m * 1e6 if float_m else None,
                last_close=last_close,
                si=si_map.get(sym),
                svr_series=svr_map.get(sym),
                ftp_borrow=ftp_borrow,
                borrow_hist=borrow[sym],
                regsho=regsho[sym],
                snapshot_label='ibkr_api' if ibkr_rates else 'ibkr_ftp',
                history_label=history_label)

    for pos in groups.get((None, None), []):
        metrics[pos] = _blank_metrics(syms[pos])

    for col in OUTPUT_COLUMNS:
        df[col] = [m[col] for m in metrics]

    covered = sum(1 for m in metrics if m['SI_DataCoverage'] != 'none')
    scored = sum(1 for m in metrics if (m['SqueezeScore'] or 0) > 0)
    borrowed = sum(1 for m in metrics if m['Borrow_Source'])
    log.info(f"Rows: {len(df)} | with any data: {covered} | "
             f"with borrow: {borrowed} | score > 0: {scored}")
    return df


def enrich_dataframe_asof(df: pd.DataFrame, as_of: date,
                          cache_dir: str | None = None,
                          skip_borrow_history: bool = False,
                          allow_network_borrow: bool = False,
                          regsho_lag_sessions: int = REGSHO_PUBLICATION_LAG_SESSIONS,
                          days_prior: int = DEFAULT_DAYS_PRIOR,
                          cal: BusinessCalendar | None = None,
                          float_column_name: str = 'Float_M',
                          lastClose_column_name: str = 'LastDailyClosePrice',
                          ibkr_rates: bool = False) -> pd.DataFrame:
    """Single-anchor entry point: every row is dated `as_of`.

    NOTE days_prior defaults to 1, so this describes the nightly run of
    `as_of` MINUS ONE DAY. Pass days_prior=0 to reproduce the run of `as_of`
    itself, which is what --compare-to validates against.
    """
    return enrich_dataframe_perrow(
        df, [as_of] * len(df), days_prior=days_prior, cal=cal, cache_dir=cache_dir,
        skip_borrow_history=skip_borrow_history,
        allow_network_borrow=allow_network_borrow,
        regsho_lag_sessions=regsho_lag_sessions,
        float_column_name=float_column_name,
        lastClose_column_name=lastClose_column_name,
        ibkr_rates=ibkr_rates)


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


DATE_COLUMN_CANDIDATES = ('ArrivalDate', 'Date', 'date')


def pick_date_column(df: pd.DataFrame, explicit: str | None) -> str | None:
    """The per-row date column to anchor on, or None if there is none.

    Mirrors market_state_calculator's column pick so the same PR-stats file
    anchors identically in both tools.
    """
    if explicit:
        return explicit if explicit in df.columns else None
    for cand in DATE_COLUMN_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


def parse_row_date(value) -> date | None:
    """yyyy-mm-dd out of a cell, or None. Tolerates a trailing time."""
    if value is None:
        return None
    hit = _DATE_RE.search(str(value))
    if not hit:
        return None
    try:
        return datetime.strptime(hit.group(1), '%Y-%m-%d').date()
    except ValueError:
        return None


def read_input(path: str) -> pd.DataFrame:
    """dtype=str + keep_default_na=False preserves the upstream formatting of
    the base columns byte-for-byte (the live addon's CLI reformats
    1.7200 -> 1.72) and stops pandas coercing the ticker 'NA' (Nano Labs) into
    NaN, which would drop that row entirely."""
    return pd.read_csv(path, sep='\t', dtype=str, keep_default_na=False)


def resolve_row_dates(df: pd.DataFrame, path: str, file_date: date | None,
                      args) -> tuple[list, str]:
    """(one date per row, a label for the log) for `df`.

    Per-row mode when --date-col is given or the frame carries an ArrivalDate;
    otherwise every row takes the file's own date.
    """
    date_col = pick_date_column(df, args.date_col)
    if args.date_col and date_col is None:
        raise ValueError(f"--date-col {args.date_col!r} is not a column of {path}")
    if date_col and not args.date_col and args.as_of:
        # An explicit --as-of is a deliberate single-anchor request and outranks
        # auto-detection; naming --date-col explicitly outranks --as-of.
        log.info(f"--as-of given: ignoring the auto-detected {date_col!r} column.")
        date_col = None
    if date_col is None:
        if file_date is None:
            raise ValueError("no date: filename has no yyyy-mm-dd, no --as-of, "
                             "and no date column")
        return [file_date] * len(df), f"file date {file_date}"

    dates = [parse_row_date(v) for v in df[date_col]]
    bad = sum(1 for d in dates if d is None)
    if bad:
        log.warning(f"{bad} row(s) have an unparseable {date_col} — their SI "
                    "columns will be blank.")
    good = [d for d in dates if d is not None]
    span = f"{min(good)}..{max(good)}" if good else "none parseable"
    return dates, f"column {date_col!r} ({span})"


def describe_dates(path: str, df: pd.DataFrame, row_dates: list, args) -> None:
    """--check-dates: print what WOULD be fetched, then fetch nothing."""
    known = sorted({d for d in row_dates if d is not None})
    if not known:
        print(f"\n{os.path.basename(path)}: no resolvable dates.")
        return
    cal = build_calendar(known)
    seen: dict[tuple, int] = {}
    for d in row_dates:
        if d is None:
            continue
        run_date, anchor, reg_end = resolve_windows(
            cal, d, args.days_prior, args.regsho_lag_sessions)
        seen.setdefault((d, run_date, anchor, reg_end), 0)
        seen[(d, run_date, anchor, reg_end)] += 1

    print(f"\n{os.path.basename(path)} — {len(df)} row(s), --days-prior "
          f"{args.days_prior}, --regsho-lag-sessions {args.regsho_lag_sessions}")
    print(f"{'row date':<12} {'run date':<12} {'anchor(SVR end)':<16} "
          f"{'Reg SHO end':<13} {'FINRA settle':<13} {'rows':>6}")
    print('-' * 76)
    for (d, run_date, anchor, reg_end), n in sorted(seen.items())[:40]:
        settle = resolve_settlement_date(anchor, cal) if anchor else None
        print(f"{str(d):<12} {str(run_date):<12} {str(anchor):<16} "
              f"{str(reg_end):<13} {str(settle):<13} {n:>6}")
    if len(seen) > 40:
        print(f"... {len(seen) - 40} more distinct date(s) not shown")


def process_file(path: str, file_date: date | None, args) -> bool:
    out_dir = args.output_dir or os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    out_path = os.path.join(out_dir, f"{stem}_SIback.tsv")
    if (os.path.exists(out_path) and not args.overwrite
            and not args.compare_to and not args.check_dates):
        log.info(f"Skipping {os.path.basename(path)} — {os.path.basename(out_path)} "
                 "exists (use --overwrite).")
        return True

    df = read_input(path)
    if 'Symbol' not in df.columns:
        log.error(f"{path}: no 'Symbol' column — skipping.")
        return False

    try:
        row_dates, label = resolve_row_dates(df, path, file_date, args)
    except ValueError as exc:
        log.error(f"{path}: {exc} — skipping.")
        return False

    if args.check_dates:
        describe_dates(path, df, row_dates, args)
        return True

    log.info(f"Input: {path} ({len(df)} rows, {len(df.columns)} cols), "
             f"anchored on {label}, days_prior={args.days_prior}")

    enriched = enrich_dataframe_perrow(
        df, row_dates, days_prior=args.days_prior, cache_dir=args.cache_dir,
        skip_borrow_history=args.skip_borrow_history,
        allow_network_borrow=args.allow_network_borrow,
        regsho_lag_sessions=args.regsho_lag_sessions,
        float_column_name=args.float_column_name,
        lastClose_column_name=args.lastClose_column_name,
        ibkr_rates=args.ibkr_rates)

    os.makedirs(out_dir, exist_ok=True)
    enriched.to_csv(out_path, sep='\t', index=False)
    log.info(f"Saved {out_path} ({len(enriched)} rows, {len(enriched.columns)} cols)")

    if args.compare_to:
        ref_path = path if args.compare_to == 'same' else args.compare_to
        reference = read_input(ref_path)
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
                        help='One reference date for every row, overriding both '
                             'the filename and an auto-detected date column. '
                             'Default: parsed from each filename.')
    parser.add_argument('--output-dir', '-o', metavar='DIR', default=None,
                        help="Output directory (default: each input's directory).")
    parser.add_argument('--cache-dir', metavar='DIR', default=None,
                        help='Raw-download cache (default: ./cache next to this script).')
    parser.add_argument('--float-column-name', metavar='COL', default='Float_M',
                        help='Column name for float values in millions '
                             '(default: Float_M).')
    parser.add_argument('--lastClose-column-name', metavar='COL', default='LastDailyClosePrice',
                        help='Column name for last daily close price '
                             '(default: LastDailyClosePrice).')
    parser.add_argument('--log', '-l', metavar='FILE', dest='log_file', default=None,
                        help='Optional log file (appended).')
    parser.add_argument('--skip-borrow-history', action='store_true',
                        help='Skip the iBorrowDesk lookup entirely.')
    parser.add_argument('--allow-network-borrow', action='store_true',
                        help='Allow live iBorrowDesk HTTP when the cache misses '
                             '(the endpoint currently returns 403).')
    parser.add_argument('--ibkr-rates', action='store_true',
                        help='Source CTB_FeePct / CTB_RebatePct / CTB_FeeDelta5d '
                             'from the IBKR FEE_RATE/REBATE_RATE bar cache '
                             f'(--cache-dir/{IBKR_RATES_SUBDIR}/), which '
                             'ibkr_rates_fetch_AddON.py builds. Covers the whole '
                             'corpus, unlike the iBorrowDesk cache that ends '
                             '2026-07-21. Borrow_Available / _AvailDelta5dPct / '
                             '_UtilProxy are NOT affected: IBKR has no historical '
                             'shortableShares, so they still come from '
                             'iBorrowDesk where it reaches.')
    parser.add_argument('--days-prior', type=int, default=DEFAULT_DAYS_PRIOR,
                        metavar='N',
                        help='How many nightly runs back to replay '
                             f'(default {DEFAULT_DAYS_PRIOR}). 1 = the run a PR '
                             'arriving the next morning was traded against (the '
                             'leak-free reading); 0 = a universe file\'s own run, '
                             'and required by --compare-to.')
    parser.add_argument('--date-col', metavar='COL', default=None,
                        help='Resolve an anchor PER ROW from this column instead '
                             'of from the filename (e.g. ArrivalDate on a '
                             'PR-stats TSV). Auto-detected from '
                             f'{", ".join(DATE_COLUMN_CANDIDATES)} when present.')
    parser.add_argument('--check-dates', action='store_true',
                        help='Print the resolved run/anchor/Reg SHO/settlement '
                             'dates for each input and exit. Fetches nothing and '
                             'writes nothing.')
    parser.add_argument('--regsho-lag-sessions', type=int,
                        default=REGSHO_PUBLICATION_LAG_SESSIONS, metavar='N',
                        help='Sessions to step back for the newest Reg SHO '
                             'threshold list (default '
                             f'{REGSHO_PUBLICATION_LAG_SESSIONS}). Use 0 only to '
                             'reproduce a run that overran past ~23:00 ET, e.g. '
                             'the 2026-07-06 night.')
    parser.add_argument('--regsho-lag-days', type=int, dest='regsho_lag_sessions',
                        default=REGSHO_PUBLICATION_LAG_SESSIONS,
                        help=argparse.SUPPRESS)   # pre-session-walk name
    parser.add_argument('--compare-to', metavar='FILE', default=None,
                        help="Validation mode: diff the 24 SI columns against "
                             "FILE, or 'same' to compare each input to itself.")
    parser.add_argument('--overwrite', action='store_true',
                        help='Redo inputs whose _SIback.tsv already exists.')
    args = parser.parse_args()

    setup_logging(args.log_file)

    if args.days_prior < 0:
        log.error(f"--days-prior must be >= 0, got {args.days_prior}.")
        return 2
    if args.compare_to and args.days_prior != 0:
        log.warning(
            f"--compare-to with --days-prior {args.days_prior}: the reference was "
            "written by the live addon on the file's OWN date, so every column "
            "will differ by one run. Pass --days-prior 0 to validate.")

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

    jobs: list[tuple[date | None, str]] = []
    for path in paths:
        if not os.path.exists(path):
            log.error(f"{path}: not found — skipping.")
            continue
        # A file date may legitimately be absent in per-row mode, where the date
        # column supplies it. process_file() refuses the row only if BOTH are
        # missing; what never happens is a silent fall back to today.
        jobs.append((override or as_of_from_filename(path), path))

    if not jobs:
        log.error("Nothing to do.")
        return 2

    # Oldest first, so files sharing a FINRA settlement download it once.
    jobs.sort(key=lambda job: (job[0] is None, job[0] or date.min, job[1]))
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
