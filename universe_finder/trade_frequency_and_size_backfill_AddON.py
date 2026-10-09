#!/usr/bin/env python3
"""
trade_frequency_and_size_backfill_AddON.py
==========================================
Point-in-time sister of trade_frequency_and_size.addOn.py.

The live addon anchors every request on "now" (endDateTime=""), so it can only
ever describe the session that just closed. This script computes the SAME four
columns with the SAME code, but **as of a historical session**, so universe
TSVs written before Step 3 grew the trade-size fetch can be enriched
retroactively:

    RTH_avgITI_sec   ETH_avgITI_sec   RTH_tradeSize   ETH_TradeSize

Nothing here re-implements a formula. `fetch_freq_async` is imported from the
live addon and called with one extra argument, so the session windows, the
barCount accumulation, the zero-trade censoring, the IQR-filtered mean, the
aggregate volume/trade ratio, the retry loop and every reqHistoricalData
parameter are literally the same objects the nightly cron runs. The live addon
is NOT otherwise modified: it is Step 3 of daily_universe_pipeline-2.py.

WHICH SESSION GETS MEASURED  (--days-prior)
-------------------------------------------
Same contract as ShortInterrest_backfill_AddON.py and
../tools/market_state/market_state_calculator.py (MSC). The universe cron fires
at 20:30 ET EVERY CALENDAR DAY and names its output after the run date, so "N
nights back" is a CALENDAR step, while the window that run measured is a
SESSION walk:

    run_date    = <row date> - N calendar days   # which nightly run we replay
    end_session = last XNYS session <= run_date  # the session that run measured
    window      = the --past-days-lookback sessions ending at end_session

`end_session` is exactly what the live addon's `past_trading_days` resolves to
at 20:30: `now.hour >= 20` keeps the run date, then the NYSE calendar walks
back to the last session on or before it. ("NYSE" and "XNYS" are aliases for
the same pandas_market_calendars calendar, so no date moves.)

    2026-08-11 (Tue), N=0  ->  2026-08-11      2026-08-11, N=1  ->  2026-08-10
    2026-08-23 (Sun), N=1  ->  2026-08-21      2026-04-05 (Sun), N=1 -> 2026-04-02
                                               (skips the weekend AND Good Friday)

N DEFAULTS TO 1, matching ShortInterrest_backfill_AddON.py: a PR that arrived
on D+1 was traded against the universe file the 20:30 run of D produced, so N=1
is the leak-free reading for a training backfill. **N=0 reproduces a universe
file's own run and is REQUIRED for --compare-to.**

For N>=2 this counts nightly RUNS, not sessions: over a weekend it steps one
calendar day where MSC would step a whole session. N=0 and N=1 -- the modes
this script exists for -- are identical under both readings.

TWO KNOBS, BOTH DEFAULTING TO 1 -- DO NOT CONFUSE THEM
------------------------------------------------------
    --days-prior N          shifts the ANCHOR   (which nightly run to replay)
    --past-days-lookback L  the WINDOW LENGTH   (how many sessions it averages)

The nightly cron runs L=1, so the default here is L=1 too. Values written by
the pre-2026-07-15 pipeline used L=3 ending the session BEFORE the run date;
this script does not reproduce that -- it deliberately restates the whole
history under the current definition.

WHICH ROWS GET FETCHED  (--only-missing, on by default)
-------------------------------------------------------
Per column, a cell is "missing" when it is absent / blank / NaN, or carries the
pipeline's 44444 fetch-failure sentinel. A cell the IMPUTERS filled is left
alone: `ITI_impute_flag` / `TradeSize_impute_flag` name which columns were
imputed (RTH is real iff the flag is 'ok' or 'eth'), and a model prediction
someone may already depend on is not silently replaced. Pass --all-rows to
recompute everything regardless.

SHARE SPLITS -- KNOWN OPEN ISSUE ON THE TRADE-SIZE COLUMNS
----------------------------------------------------------
IBKR back-adjusts historical VOLUME for splits, so bars for a past session
fetched today arrive in TODAY's share terms while the live 20:30 run recorded
that session in its own. The two ITI columns are immune (barCount is a count of
transactions, which no split rescales); RTH_tradeSize / ETH_TradeSize are
volume/barCount and move by the cumulative factor of every split since the
anchor. In this universe that is not exotic -- 285 of the 758 symbols in
stocks_universe_2026-07-15.tsv had split within the prior year, reverse splits
being how sub-$6 nano-caps keep their listing -- and the error grows the longer
after the target session you run the backfill.

The correction is `restated = raw / split_factor_since(splits, end_session)`.
It is NOT applied yet, on purpose: --compare-to reports a `split` bucket and the
observed backfill/reference ratio for exactly those symbols, so the correction
gets written against a measurement rather than an assumption. The prediction it
tests is sharp -- trade-size ratios should track the split factor while the ITI
columns stay clean. See CLAUDE.md.

COST
----
The fetch unit is a **(symbol, end_session) pair**, not a symbol. Unlike MSC --
which pulls one long daily history per symbol and slices it per row -- intraday
bars cannot be sliced across months, so per-row mode (--date-col) on a PR file
spanning 60 dates is thousands of requests. Run --check-dates first; it prints
the pair count and fetches nothing. There is also no Step 2 here to hand over
pre-qualified contracts, so symbols are qualified in their own pass (once per
symbol, reused across every anchor).

Run with the venv interpreter, against the PAPER Gateway (see CLAUDE.md: the
2026-07-06 live-Gateway contention incident):

  # show the resolved sessions and the fetch cost, then exit
  /home/tom/venv/bin/python trade_frequency_and_size_backfill_AddON.py \
      -i data/stocks_universe_2026-08-11.tsv --check-dates

  # validate against a file the live addon already enriched (needs N=0)
  /home/tom/venv/bin/python trade_frequency_and_size_backfill_AddON.py \
      -i data/stocks_universe_2026-08-11.tsv \
      --days-prior 0 --compare-to same --limit 30 --all-rows

  # backfill the hole
  /home/tom/venv/bin/python trade_frequency_and_size_backfill_AddON.py \
      -i 'data/nasdaq_symbols_data_priced_2026-05-*.tsv'

  # per-row anchors: every PR row resolved from its own ArrivalDate
  /home/tom/venv/bin/python trade_frequency_and_size_backfill_AddON.py \
      -i ../price_stats_loader/outputs/07_PRstats_....tsv --date-col ArrivalDate

Output: <input_stem>_freqSizeBack.tsv next to the input. Inputs are never
modified.

Requires: ib_insync, pandas, pandas_market_calendars.
"""

import argparse
import asyncio
import glob as globmod
import importlib.util
import logging
import os
import re
import sys
from bisect import bisect_right
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal
from ib_insync import IB, Stock

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from closing_price_fetch_addOn import ensure_connected  # noqa: E402

log = logging.getLogger("trade_freq_size_backfill")


# --------------------------------------------------------------------------- #
# the live addon: imported, never re-implemented
# --------------------------------------------------------------------------- #

_ADDON_PATH = os.path.join(_SCRIPT_DIR, "trade_frequency_and_size.addOn.py")


def _load_addon():
    """Load trade_frequency_and_size.addOn.py by path (its '.' breaks import).

    Same mechanism daily_universe_pipeline-2.py:211 uses. Everything that
    determines a VALUE comes from here; this file only decides which dates to
    ask for.
    """
    spec = importlib.util.spec_from_file_location(
        "trade_frequency_and_size_addOn", _ADDON_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load trade-size addon at {_ADDON_PATH}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_tfs = _load_addon()
fetch_freq_async = _tfs.fetch_freq_async
ET = _tfs.ET
_FREQ_BAR_SIZE = _tfs._FREQ_BAR_SIZE
_MAX_LOOKBACK_WARN = _tfs._MAX_LOOKBACK_WARN

_MSC_PATH = os.path.join(_SCRIPT_DIR, os.pardir, "tools", "market_state",
                         "market_state_calculator.py")
_MSC_CACHE = os.path.join(os.path.dirname(_MSC_PATH), "tables")
_msc = None


def _load_msc():
    """Lazily load market_state_calculator for its split lookup.

    Only `fetch_splits` / `fetch_all_splits` are used -- yfinance-backed, disk
    cached, and careful to return None for "lookup failed" where [] means
    "never split". Reusing them (and MSC's cache directory) rather than
    re-implementing keeps one definition of a split factor in the repo. Loaded
    lazily and behind a try/except because splits are a reporting nicety here,
    not something worth failing a backfill over.
    """
    global _msc
    if _msc is None:
        spec = importlib.util.spec_from_file_location(
            "market_state_calculator", os.path.abspath(_MSC_PATH))
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load MSC at {_MSC_PATH}")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _msc = mod
    return _msc


def split_factor_since(splits, end_session: date):
    """Cumulative split factor for splits with ex_date AFTER end_session.

    yfinance's factor is new/old shares: 0.05 = 1-for-20 reverse, 20 = 20-for-1
    forward. Returns 1.0 when nothing happened since, None when the lookup
    failed (which must NOT be read as "no split").

    Why this matters here: IBKR back-adjusts historical VOLUME for splits (see
    market_state_calculator.residual_split_jump), so bars for a past session
    fetched today come back in TODAY's share terms, while the live 20:30 run
    recorded that same session in ITS OWN share terms. RTH_tradeSize /
    ETH_TradeSize are volume/barCount and therefore move by this factor; the
    ITI columns are barCount alone -- a count of transactions, which no split
    rescales -- and are immune.
    """
    if splits is None:
        return None
    factor = 1.0
    for ex_date, value in splits:
        if ex_date > end_session:
            factor *= value
    return factor


def fetch_splits_for(symbols, args) -> dict:
    """{symbol: [(ex_date, factor), ...]} from MSC's cached yfinance lookup.

    A missing/None entry means the lookup FAILED, which must never be read as
    "never split" -- split_factor_since propagates that as None.
    """
    if not symbols:
        return {}
    try:
        msc = _load_msc()
        return msc.fetch_all_splits(sorted(symbols), Path(args.split_cache_dir),
                                    True, args.split_workers,
                                    args.split_max_age_days)
    except Exception as exc:
        log.error(f"Split lookup unavailable ({type(exc).__name__}: {exc}); "
                  "falling back to the price probe alone where possible.")
        return {}


def split_factors_for_units(units, args) -> dict:
    """{(symbol, end_session): cumulative split factor} for the fetch cohort.

    Keyed by the PAIR, not the symbol: the factor is "splits since this
    anchor", so one symbol backfilled at several anchors gets several factors.
    """
    if not units:
        return {}
    splits = fetch_splits_for({u[0] for u in units}, args)
    return {(sym, end): split_factor_since(splits.get(sym), end)
            for sym, end, _days in units}


def plan_price_probes(df, row_dates, cal, unit_rows, args) -> dict:
    """{unit index: (price_session, reference_close)} for the split cross-check.

    LastDailyClosePrice on a pipeline row is the close of that row's OWN
    session, written by the live run in that session's share terms. Re-fetching
    the same session today returns it back-adjusted, so

        implied_factor = reference_close / refetched_close

    is the cumulative split factor since then, measured from the very source
    whose adjustment we are undoing -- with no dependence on yfinance's split
    table being complete. That is the whole point: yfinance under-reported
    LIMN on 2026-08-27 (one 0.02 where IBKR had applied two) and nothing in a
    production run would have caught it.

    Note the probe session is the ROW's session, not the fetch anchor. They
    coincide at --days-prior 0; see resolve_split_factors for how the two are
    reconciled when they do not.
    """
    if CLOSE_COLUMN not in df.columns:
        return {}
    probes: dict = {}
    for u, rows in enumerate(unit_rows):
        for i in rows:
            close = _to_float(df.at[i, CLOSE_COLUMN])
            if close is None or close == SENTINEL or close <= 0:
                continue
            price_session = cal.resolve(row_dates[i], 0)
            if price_session is not None:
                probes[u] = (price_session, close)
                break
    return probes


def resolve_split_factors(ib, units, unit_rows, df, row_dates, cal,
                          contracts, args) -> dict:
    """Final {(symbol, end_session): factor} used to restate trade size.

    yfinance supplies EXACT rational factors (0.02, not 0.0199997) but an
    incomplete history; the price probe measures IBKR's actual adjustment but
    carries 4-decimal rounding. So yfinance leads and the probe audits:

        discrepancy = implied_factor / yfinance_factor   (both at price_session)

    Within PRICE_AGREE_TOL of 1.0 the two agree and yfinance's exact value is
    used untouched -- important, because multiplying a clean 1.0 by a noisy
    1.0004 would turn exact --compare-to matches into 'near'. Outside it,
    yfinance is missing splits and the factor is scaled by the discrepancy.
    For LIMN that is 0.02 x (0.0004/0.02) = 0.0004, the value IBKR actually
    applied.

    Scaling rather than substituting is what lets this work when the probe
    session and the fetch anchor differ (--days-prior > 0): a split yfinance
    missed after price_session is also after end_session, so the same
    discrepancy applies to both windows. The residual blind spot is a split
    missed BETWEEN end_session and price_session -- at most --days-prior days
    wide, and logged as unverifiable rather than silently trusted.
    """
    if not units:
        return {}
    use_yf = args.split_source in ('both', 'yfinance')
    use_price = args.split_source in ('both', 'price')

    splits = fetch_splits_for({u[0] for u in units}, args) if use_yf else {}
    probes = plan_price_probes(df, row_dates, cal, unit_rows, args) if use_price else {}

    closes: dict = {}
    if probes:
        pairs = sorted({(units[u][0], ps) for u, (ps, _c) in probes.items()})
        log.info(f"Split cross-check: probing {len(pairs)} daily close(s) "
                 f"(source={args.split_source}) …")
        closes = dict(zip(pairs, fetch_closes(ib, pairs, contracts, args)))

    factors, overrides, unverified = {}, [], 0
    for u, (symbol, end_session, _days) in enumerate(units):
        yf_end = split_factor_since(splits.get(symbol), end_session) if use_yf else None

        implied = None
        if u in probes:
            price_session, ref_close = probes[u]
            got = closes.get((symbol, price_session))
            if got is not None and got == got and got > 0:
                implied = ref_close / got

        if implied is None:
            factors[(symbol, end_session)] = yf_end
            if use_price and yf_end not in (None, 1.0):
                unverified += 1
            continue

        yf_price = (split_factor_since(splits.get(symbol), probes[u][0])
                    if use_yf else None)
        if yf_price is None or yf_price <= 0:
            # No yfinance baseline: trust the probe, but only where it can
            # speak for the anchor -- and snap rounding noise back to "no split".
            if probes[u][0] == end_session:
                factors[(symbol, end_session)] = (
                    1.0 if abs(implied - 1.0) <= PRICE_AGREE_TOL else implied)
            else:
                factors[(symbol, end_session)] = None
            continue

        discrepancy = implied / yf_price
        if abs(discrepancy - 1.0) <= PRICE_AGREE_TOL:
            factors[(symbol, end_session)] = yf_end
        else:
            factors[(symbol, end_session)] = (
                yf_end * discrepancy if yf_end is not None else implied)
            overrides.append((symbol, yf_price, implied, discrepancy))

    if overrides:
        log.warning(
            f"{len(overrides)} symbol(s) where IBKR's own price adjustment "
            f"disagrees with yfinance's split history -- trusting IBKR "
            f"(these would otherwise be silently mis-scaled):")
        for symbol, yf_price, implied, disc in sorted(overrides)[:15]:
            log.warning(f"    {symbol}: yfinance={yf_price:.6g} "
                        f"IBKR-implied={implied:.6g} (x{disc:.6g})")
        if len(overrides) > 15:
            log.warning(f"    … and {len(overrides) - 15} more")
    if unverified:
        log.info(f"{unverified} split symbol(s) had no usable close to verify "
                 f"against ({CLOSE_COLUMN} missing/blank/44444, or no bar for "
                 f"that session); yfinance's factor used as-is.")
    return factors


def restate_for_splits(result, factor):
    """Put a fetched 4-tuple back into the anchor session's share terms.

    IBKR back-adjusts historical VOLUME, so bars fetched today for a past
    session arrive in today's shares while the live 20:30 run recorded that
    session in its own. Dividing by the cumulative factor undoes exactly that.
    Measured 2026-08-27 on 12 reverse-split symbols spanning factors
    0.005-0.2: observed backfill/live ratios tracked the factor to 3-4
    significant figures (LBGJ 0.005 vs 0.005, HAO 0.05004 vs 0.05, CIIT
    0.0998 vs 0.1) while both ITI columns matched the live file untouched.

    Only the two trade-size values move. ITI is session_seconds / barCount --
    a count of transactions, which no split rescales.
    """
    if factor in (None, 1.0):
        return result
    rth_iti, eth_iti, rth_size, eth_size = result
    return (rth_iti, eth_iti, rth_size / factor, eth_size / factor)


# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

OUTPUT_COLUMNS = ["RTH_avgITI_sec", "ETH_avgITI_sec",
                  "RTH_tradeSize", "ETH_TradeSize"]

# Which impute-flag column governs which output column, and the cohort letter
# that flag uses to mean "this one was imputed" ('both' always counts).
_IMPUTE_FLAG = {
    "RTH_avgITI_sec": ("ITI_impute_flag", "rth"),
    "ETH_avgITI_sec": ("ITI_impute_flag", "eth"),
    "RTH_tradeSize":  ("TradeSize_impute_flag", "rth"),
    "ETH_TradeSize":  ("TradeSize_impute_flag", "eth"),
}

# The pipeline's attempted-but-failed marker. Written back for the same reason
# daily_universe_pipeline-2.py:664-688 writes it: it is what distinguishes a
# failed fetch from a row that was never attempted, and the imputers key on it.
SENTINEL = 44444.0

# "N nights back". 1 = the run a PR arriving the next morning was traded
# against, i.e. the leak-free reading; see the module docstring.
DEFAULT_DAYS_PRIOR = 1

# Below 0.5% relative, a disagreement with a live-run value is tape noise
# (late/corrected trade reports settling in after the original 20:30 fetch),
# not a windowing error. A windowing error is systematic and much larger.
NEAR_MATCH_REL = 0.005

_DATE_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')
DATE_COLUMN_CANDIDATES = ('ArrivalDate', 'Date', 'date')

# How far the price-implied split factor may sit from yfinance's before we stop
# believing yfinance. Stored closes carry 4-decimal rounding, so a non-split
# symbol lands within a fraction of a percent of 1.0; a missed split is off by
# the whole factor (LIMN 2026-08-27: 50x). Nothing real lives in between, so a
# loose 2% keeps rounding noise from ever perturbing an exact factor.
PRICE_AGREE_TOL = 0.02

# The column holding each row's own session close, in that session's share
# terms -- the anchor for the price-implied factor.
CLOSE_COLUMN = 'LastDailyClosePrice'


# --------------------------------------------------------------------------- #
# trading calendar
# --------------------------------------------------------------------------- #

class Sessions:
    """XNYS session index with on-or-before lookup and session-count shifting."""

    def __init__(self, start: date, end: date):
        cal = mcal.get_calendar("XNYS")
        self.days: list = [d.date() for d in cal.valid_days(str(start), str(end))]
        if not self.days:
            raise RuntimeError(f"XNYS calendar returned no sessions for {start}..{end}")
        self.first, self.last = self.days[0], self.days[-1]

    def index_on_or_before(self, d: date):
        """Position of the last session <= d, or None if d predates the index."""
        i = bisect_right(self.days, d) - 1
        return i if i >= 0 else None

    def resolve(self, d: date, days_prior: int):
        """Anchor session for date d.

            days_prior == 0  -> the last session ON OR BEFORE d
            days_prior >= 1  -> the days_prior-th session STRICTLY BEFORE d

        The second clause matters only when d is not itself a session. Rolling a
        Sunday back to Friday has ALREADY stepped back one session, so shifting
        again would land on Thursday - one session staler than what the Friday
        20:30 run actually produced. Decrementing here makes weekend and holiday
        dates agree with weekdays: --days-prior 1 always means "the last session
        that closed before this calendar date".

        Kept byte-identical to market_state_calculator.Sessions.resolve and
        ShortInterrest_backfill_AddON.BusinessCalendar.resolve on purpose: the
        three scripts are expected to resolve the same anchor for the same
        (date, N), and --check-dates is where that gets spot-checked.
        """
        i = self.index_on_or_before(d)
        if i is None:
            return None
        if days_prior >= 1 and self.days[i] != d:
            days_prior -= 1
        j = i - days_prior
        return self.days[j] if 0 <= j < len(self.days) else None

    def back(self, end_session: date, n: int) -> list:
        """The n sessions ending at end_session, NEWEST FIRST.

        Replaces the live addon's past_trading_days() for the lookback window:
        same dates for the same anchor, but resolvable for a past date.
        """
        i = self.index_on_or_before(end_session)
        if i is None:
            return []
        return list(reversed(self.days[max(0, i - n + 1):i + 1]))


def build_calendar(dates) -> Sessions:
    """One XNYS index wide enough for every window any of `dates` will need.

    400 days of back-margin covers a --past-days-lookback far beyond the
    22-session ceiling the addon warns at; 30 forward covers a --days-prior 0
    run on today's file.
    """
    lo, hi = min(dates), max(dates)
    return Sessions(lo - timedelta(days=400), hi + timedelta(days=30))


def resolve_window(cal: Sessions, row_date: date, days_prior: int, lookback: int):
    """(run_date, end_session, days) for one input date.

    `days` is the YYYYMMDD strings compute_session_itis / compute_session_volumes
    expect: the `lookback` sessions ending at end_session, newest first.
    """
    run_date = row_date - timedelta(days=days_prior)
    end_session = cal.resolve(run_date, 0)
    if end_session is None:
        return run_date, None, []
    days = [d.strftime("%Y%m%d") for d in cal.back(end_session, lookback)]
    return run_date, end_session, days


def end_datetime_str(end_session: date) -> str:
    """IBKR endDateTime for an INTRADAY request covering end_session.

    NOT market_state_calculator.end_datetime_str. That one anchors at UTC
    midnight of end_session+1, which is 20:00 ET under EDT but 19:00 ET under
    EST -- harmless for its daily bars, but here it would clip 19:00-20:00 of
    the after-hours session on every Nov-Mar date, deflating ETH barCounts and
    inflating ETH_avgITI_sec. Anchor in ET so DST cannot move it.

    Returns "" ("now") when end_session is today or later: IBKR rejects a
    future endDateTime, and "now" is what the live addon uses anyway. Any time
    in [20:00, 24:00) ET is equivalent -- bars at tod >= 20:00 are dropped by
    the session windows and surplus dates by the `days` filter -- so 20:15 just
    mirrors the live run's ~20:30 "now".
    """
    if end_session >= datetime.now(tz=ET).date():
        return ""
    return datetime(end_session.year, end_session.month, end_session.day,
                    20, 15, tzinfo=ET).strftime("%Y%m%d %H:%M:%S US/Eastern")


def warn_if_session_incomplete(end_sessions, days_prior: int) -> None:
    """The analogue of MSC's warning, moved to the 20:00 ETH close.

    MSC only needs the 16:00 RTH close; these metrics cover 04:00-20:00, so an
    anchor of today is partial until after-hours ends.
    """
    now = datetime.now(tz=ET)
    if now.date() in end_sessions and now.hour < 20:
        log.warning(
            f"[Anchor] --days-prior {days_prior} resolves to TODAY ({now.date()}) "
            f"for some rows and it is {now:%H:%M} ET -- before the 20:00 ETH "
            f"close. Those bars are PARTIAL and the ITI will read high. Re-run "
            f"after 20:00 ET for final values.")


# --------------------------------------------------------------------------- #
# cell helpers
# --------------------------------------------------------------------------- #

def _norm(value) -> str:
    """Normalise a VALUE cell, treating the usual null spellings as empty.

    Not for symbols -- see _norm_symbol.
    """
    if value is None:
        return ''
    text = str(value).strip()
    return '' if text.lower() in ('', 'nan', 'none', '<na>') else text


def _norm_symbol(value) -> str:
    """Normalise a TICKER: strip and upper-case, nothing else.

    Deliberately not _norm: 'NAN' and 'NONE' are real listed tickers, and
    _norm's null-marker list would erase them. NAN sits at row 5716 of
    nasdaq_symbols_data_priced_2026-05-14.tsv, so this is not hypothetical --
    it is the same trap that makes every reader here pass
    keep_default_na=False to protect 'NA' (Nano Labs), one layer up.
    """
    return '' if value is None else str(value).strip().upper()


def _to_float(value):
    text = _norm(value)
    if not text:
        return None
    try:
        return float(text.replace(',', ''))
    except ValueError:
        return None


def _is_imputed(flag_value, which: str) -> bool:
    """True when an impute flag says `which` ('rth'/'eth') was model-filled.

    The flag names the cohort that WAS imputed, so 'ok' means neither and
    'both' means both (daily_universe_pipeline-2.py's overwrite-protection
    reads it the same way). 'skipped_nan' rows were never attempted and are
    blank, so they fall out as missing on the value test regardless.
    """
    return _norm(flag_value).lower() in (which, 'both')


def _cell_is_missing(df: pd.DataFrame, row: int, col: str) -> bool:
    """Does this cell need a fetch? See the module docstring's --only-missing.

    The test is on the VALUE alone, and that is deliberate rather than an
    oversight of the impute flags. A cell the imputer filled holds a real
    number and is kept; a cell the imputer's min-train-rows gate skipped still
    reads 44444 even though the flag says 'rth'/'eth'/'both', and that one DOES
    want a fetch. Going by the value gets both cases right; going by the flag
    would get the second one wrong.
    """
    if col not in df.columns:
        return True
    value = _to_float(df.at[row, col])
    return value is None or value == SENTINEL


def _fmt(value) -> str:
    """A fetched value as the pipeline would write it.

    NaN means the fetch was attempted and failed, which is exactly what the
    44444 sentinel records (daily_universe_pipeline-2.py:664-688); rows that
    were never attempted keep their original text and never reach here.
    """
    if value is None or value != value:
        return str(int(SENTINEL))
    return f"{float(value):.4f}"


# --------------------------------------------------------------------------- #
# input / date resolution
# --------------------------------------------------------------------------- #

def read_input(path: str) -> pd.DataFrame:
    """dtype=str + keep_default_na=False preserves the upstream formatting of
    every base column byte-for-byte and stops pandas coercing the ticker 'NA'
    (Nano Labs) into NaN, which would drop that row entirely."""
    return pd.read_csv(path, sep='\t', dtype=str, keep_default_na=False)


def date_from_filename(path: str):
    hit = _DATE_RE.search(os.path.basename(path))
    if not hit:
        return None
    try:
        return datetime.strptime(hit.group(1), '%Y-%m-%d').date()
    except ValueError:
        return None


def parse_row_date(value):
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


def pick_date_column(df: pd.DataFrame, explicit):
    """The per-row date column to anchor on, or None if there is none.

    Mirrors ShortInterrest_backfill_AddON / market_state_calculator so the same
    PR-stats file anchors identically in all three tools.
    """
    if explicit:
        return explicit if explicit in df.columns else None
    for cand in DATE_COLUMN_CANDIDATES:
        if cand in df.columns:
            return cand
    return None


def resolve_row_dates(df: pd.DataFrame, path: str, file_date, args):
    """(one date per row, a label for the log) for `df`."""
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
        log.warning(f"{bad} row(s) have an unparseable {date_col} -- their "
                    "trade-frequency columns are left untouched.")
    good = [d for d in dates if d is not None]
    span = f"{min(good)}..{max(good)}" if good else "none parseable"
    return dates, f"column {date_col!r} ({span})"


# --------------------------------------------------------------------------- #
# fetch planning
# --------------------------------------------------------------------------- #

def plan_units(df: pd.DataFrame, row_dates, cal: Sessions, args):
    """Work out what to fetch.

    Returns (units, unit_rows, needs, stats) where
      units[u]     = (symbol, end_session, days)
      unit_rows[u] = the row indices that unit feeds
      needs[i]     = the set of columns row i is missing
    A row joins a unit only if at least one of its four cells is missing, so
    --only-missing shrinks the IBKR bill rather than just the write.
    """
    needs, unit_index, units, unit_rows = {}, {}, [], []
    stats = {"no_date": 0, "no_symbol": 0, "complete": 0, "unresolved": 0}

    for i, row_date in zip(df.index, row_dates):
        symbol = _norm_symbol(df.at[i, 'Symbol'])
        if not symbol:
            stats["no_symbol"] += 1
            continue
        if row_date is None:
            stats["no_date"] += 1
            continue

        if args.all_rows:
            wanted = set(OUTPUT_COLUMNS)
        else:
            wanted = {c for c in OUTPUT_COLUMNS if _cell_is_missing(df, i, c)}
        if not wanted:
            stats["complete"] += 1
            continue

        _run, end_session, days = resolve_window(
            cal, row_date, args.days_prior, args.past_days_lookback)
        if end_session is None or not days:
            stats["unresolved"] += 1
            continue

        needs[i] = wanted
        key = (symbol, end_session)
        u = unit_index.get(key)
        if u is None:
            u = len(units)
            unit_index[key] = u
            units.append((symbol, end_session, days))
            unit_rows.append([])
        unit_rows[u].append(i)

    return units, unit_rows, needs, stats


# --------------------------------------------------------------------------- #
# IBKR
# --------------------------------------------------------------------------- #

def _batches(seq, n):
    """Yield (start_index, slice) chunks of `seq` of length `n`."""
    for i in range(0, len(seq), n):
        yield i, seq[i:i + n]


def _run_concurrent(ib, items, coro_factory, is_empty, nan_value, label, args):
    """Batched concurrent fetch, mirroring daily_universe_pipeline-2._run_concurrent.

    Kept structurally identical to the pipeline's version so the two behave the
    same way against the same Gateway: results are pre-filled with `nan_value`
    so every exit path returns a complete aligned list, a mid-batch socket
    disconnect costs one batch rather than the run, and a lost batch is NOT
    retried (retrying near the 03:59 ET Gateway restart risks looping forever).
    """
    total = len(items)
    results: list = [nan_value] * total

    async def _gather(batch):
        return await asyncio.gather(*[coro_factory(x) for x in batch])

    for start, batch in _batches(items, args.batch_size):
        ensure_connected(ib, args.host, args.port, args.clientID)

        try:
            out = ib.run(_gather(batch))
        except (ConnectionError, OSError) as exc:
            log.warning(f"  {label}: connection lost mid-batch at {start} "
                        f"({exc!r}); attempting reconnect …")
            if not ensure_connected(ib, args.host, args.port, args.clientID):
                log.error(f"  {label}: could not reconnect; aborting and "
                          f"recording {total - start - len(batch)} remaining "
                          f"items as missing.")
                return results
            log.warning(f"  {label}: reconnected; skipping retry of batch "
                        f"at {start} and continuing.")
            continue

        if not ib.isConnected():
            log.warning(f"  {label}: connection lost during batch at {start}; "
                        f"reconnecting and retrying batch …")
            if ensure_connected(ib, args.host, args.port, args.clientID):
                try:
                    out = ib.run(_gather(batch))
                except (ConnectionError, OSError) as exc:
                    log.warning(f"  {label}: retry also failed ({exc!r}); "
                                f"keeping NaN for batch at {start}.")
                    out = [nan_value] * len(batch)

        results[start:start + len(batch)] = out
        done = start + len(batch)
        empties = sum(1 for r in out if is_empty(r))
        log.info(f"  {label}: {done}/{total}  (batch empties: {empties}/{len(batch)})")

        # Contention canary (2026-07-06 incident): >50% of the FIRST batch empty
        # means the Gateway's historical path is starved from minute one -- not
        # the slot-exhaustion cascade, which builds up over time.
        if start == 0 and len(batch) >= 50 and empties > len(batch) / 2:
            log.error(
                f"  {label}: CONTENTION CANARY -- {empties}/{len(batch)} of the "
                f"first batch came back empty. Historical queries are being "
                f"starved at the Gateway on {args.host}:{args.port} (the "
                f"2026-07-06 signature: mass timeouts from the first request, "
                f"Error 162, no pacing violations). Check what else is connected "
                f"to this Gateway -- the rest of this run will likely fail the "
                f"same way.")

    return results


def qualify_symbols(ib, symbols, args) -> dict:
    """{symbol: qualified Stock or None}, one round-trip per DISTINCT symbol.

    The nightly pipeline gets its contracts free from Step 2's price fetch and
    hands them to fetch_freq_async; there is no Step 2 here, so we do our own
    pass. Doing it up front (rather than letting fetch_freq_async qualify per
    call) means a symbol appearing at 40 different anchors in --date-col mode
    still qualifies once, and a symbol that no longer qualifies -- delisted,
    renamed -- short-circuits all of its units without touching IBKR.
    """
    sem = asyncio.Semaphore(args.qualify_concurrency)

    async def _one(symbol):
        async with sem:
            try:
                qualified = await ib.qualifyContractsAsync(Stock(symbol, "SMART", "USD"))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.debug(f"  qualify {symbol}: {type(exc).__name__}: {exc}")
                return None
            return qualified[0] if qualified else None

    out = _run_concurrent(ib, symbols, coro_factory=_one,
                          is_empty=lambda c: c is None, nan_value=None,
                          label="qualify", args=args)
    return dict(zip(symbols, out))


def fetch_units(ib, units, contracts, args) -> list:
    """One (rth_iti, eth_iti, rth_size, eth_size) per unit, aligned with `units`."""
    nan = float("nan")
    nan4 = (nan, nan, nan, nan)
    sem = asyncio.Semaphore(args.freq_concurrency)

    async def _one(unit):
        symbol, end_session, days = unit
        contract = contracts.get(symbol)
        if contract is None:
            return nan4
        return await fetch_freq_async(
            ib, symbol, days, sem,
            args.freq_timeout, args.freq_retries, args.freq_bar_size,
            contract=contract,
            end_datetime=end_datetime_str(end_session),
        )

    return _run_concurrent(ib, units, coro_factory=_one,
                           is_empty=lambda r: r[0] != r[0] and r[1] != r[1],
                           nan_value=nan4, label="freq fetch", args=args)


def _bar_date(bar):
    """The session a daily bar belongs to, as a date."""
    value = getattr(bar, 'date', None)
    if isinstance(value, datetime):        # datetime subclasses date -- first
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


async def fetch_close_at_async(ib, contract, session: date, sem,
                               timeout: int, retries: int) -> float:
    """The daily close of `session`, AS IBKR REPORTS IT TODAY.

    Deliberately NOT closing_price_fetch_addOn.fetch_last_rth_close_async.
    That one hardcodes endDateTime='' and returns bars[-1] unconditionally,
    which is exactly right for "the latest close" and wrong here: anchored on a
    past session we must VERIFY the bar we got IS that session. If the symbol
    was halted that day IBKR returns an earlier bar quite happily, and a close
    from the wrong session would silently corrupt the implied split factor --
    the one number this whole cross-check exists to produce. We also reuse the
    contract the caller already qualified instead of re-qualifying.

    `timeout` goes INTO reqHistoricalDataAsync rather than an outer
    asyncio.wait_for; see closing_price_fetch_addOn.py:134 for why (leaked
    reqId slots starve the Gateway). Do not "simplify" it.

    NaN means no usable bar, which the caller must treat as "no opinion" --
    never as "no split".
    """
    async with sem:
        for attempt in range(retries + 1):
            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime=end_datetime_str(session),
                    durationStr='4 D',      # spans a long weekend before it
                    barSizeSetting='1 day',
                    whatToShow='TRADES',
                    useRTH=True,
                    formatDate=1,
                    keepUpToDate=False,
                    timeout=timeout,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                bars = None
            if bars:
                for bar in reversed(bars):
                    if _bar_date(bar) == session:
                        close = float(bar.close)
                        return close if close > 0 else float('nan')
                return float('nan')         # traded nothing that session
            if attempt < retries:
                await asyncio.sleep(1.0)
        return float('nan')


def fetch_closes(ib, pairs, contracts, args) -> list:
    """One close per (symbol, session) pair, aligned with `pairs`."""
    nan = float("nan")
    sem = asyncio.Semaphore(args.price_concurrency)

    async def _one(pair):
        symbol, session = pair
        contract = contracts.get(symbol)
        if contract is None:
            return nan
        return await fetch_close_at_async(ib, contract, session, sem,
                                          args.price_timeout, args.price_retries)

    return _run_concurrent(ib, pairs, coro_factory=_one,
                           is_empty=lambda r: r != r,
                           nan_value=nan, label="close probe", args=args)


def connect(args) -> IB:
    ib = IB()
    log.info(f"Connecting to IBKR at {args.host}:{args.port} "
             f"(clientId={args.clientID}) …")
    ib.connect(args.host, args.port, clientId=args.clientID, timeout=30)
    if not ib.isConnected():
        raise ConnectionError("failed to connect to IBKR")
    return ib


# --------------------------------------------------------------------------- #
# enrichment
# --------------------------------------------------------------------------- #

def enrich(df: pd.DataFrame, row_dates, cal: Sessions, args) -> pd.DataFrame:
    """Fill the four columns for every row that needs them. Returns a new frame."""
    units, unit_rows, needs, stats = plan_units(df, row_dates, cal, args)

    log.info(f"Rows: {len(df)}   needing a fetch: {len(needs)}   "
             f"already complete: {stats['complete']}   "
             f"no date: {stats['no_date']}   blank symbol: {stats['no_symbol']}   "
             f"unresolvable anchor: {stats['unresolved']}")
    log.info(f"Fetch units (symbol x end_session): {len(units)}   "
             f"distinct symbols: {len({u[0] for u in units})}   "
             f"distinct sessions: {len({u[1] for u in units})}")

    if args.limit and len(units) > args.limit:
        log.warning(f"--limit {args.limit}: fetching {args.limit} of {len(units)} "
                    f"units; rows behind the rest keep their original values.")
        # Truncating from the front keeps units[u] and unit_rows[u] aligned.
        units = units[:args.limit]

    out = df.copy()
    for col in OUTPUT_COLUMNS:
        if col not in out.columns:
            out[col] = ""

    if not units:
        log.info("Nothing to fetch.")
        return out

    warn_if_session_incomplete({u[1] for u in units}, args.days_prior)

    symbols = sorted({u[0] for u in units})
    ib = connect(args)
    try:
        log.info(f"Qualifying {len(symbols)} distinct symbol(s) …")
        contracts = qualify_symbols(ib, symbols, args)
        bad = [s for s, c in contracts.items() if c is None]
        if bad:
            log.warning(f"{len(bad)} symbol(s) did not qualify (delisted/renamed?) "
                        f"-- their rows get the {int(SENTINEL)} sentinel: "
                        f"{', '.join(bad[:15])}{' …' if len(bad) > 15 else ''}")

        log.info(f"Fetching {len(units)} unit(s)  "
                 f"(bar='{args.freq_bar_size}', lookback={args.past_days_lookback}, "
                 f"concurrency={args.freq_concurrency}, timeout={args.freq_timeout}s) …")
        results = fetch_units(ib, units, contracts, args)

        # Inside the connection: the split cross-check needs a daily close per
        # (symbol, session) from the same Gateway session.
        factors = ({} if args.no_split_adjust else
                   resolve_split_factors(ib, units, unit_rows, df, row_dates,
                                         cal, contracts, args))
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    # Restate trade size into the anchor session's share terms before writing.
    # Done here rather than in fetch_units so the fetch stays a thin wrapper
    # around the live addon and the correction is visible at the write site.
    adjusted = sum(1 for u in units if factors.get(u[:2]) not in (None, 1.0))
    unknown = [u[0] for u in units if u[:2] in factors and factors[u[:2]] is None]
    if factors:
        log.info(f"Split adjustment: {adjusted} of {len(units)} unit(s) restated "
                 "into their anchor's share terms.")
    if unknown:
        log.warning(
            f"{len(unknown)} symbol(s) have no split history — their trade "
            f"sizes stay in TODAY's share terms and are WRONG if they split: "
            f"{', '.join(sorted(set(unknown))[:15])}"
            f"{' …' if len(set(unknown)) > 15 else ''}")

    written = 0
    for u, result in enumerate(results):
        values = dict(zip(OUTPUT_COLUMNS,
                          restate_for_splits(result, factors.get(units[u][:2]))))
        for i in unit_rows[u]:
            for col in needs[i]:
                out.at[i, col] = _fmt(values[col])
                written += 1

    ok = sum(1 for r in results if r[0] == r[0])
    log.info(f"Fetched {ok}/{len(results)} units with an RTH value; "
             f"{written} cell(s) written.")
    return out


# --------------------------------------------------------------------------- #
# --check-dates
# --------------------------------------------------------------------------- #

def describe_dates(path: str, df: pd.DataFrame, row_dates, cal: Sessions,
                   args) -> None:
    """Print what WOULD be fetched, then fetch nothing."""
    known = sorted({d for d in row_dates if d is not None})
    if not known:
        print(f"\n{os.path.basename(path)}: no resolvable dates.")
        return

    units, unit_rows, needs, stats = plan_units(df, row_dates, cal, args)
    seen: dict = {}
    for d in known:
        run_date, end_session, days = resolve_window(
            cal, d, args.days_prior, args.past_days_lookback)
        span = f"{days[-1]}..{days[0]}" if days else "-"
        seen[d] = (run_date, end_session, span,
                   end_datetime_str(end_session) if end_session else "-")

    counts: dict = {}
    for d in row_dates:
        if d is not None:
            counts[d] = counts.get(d, 0) + 1

    print(f"\n{os.path.basename(path)} — {len(df)} row(s), --days-prior "
          f"{args.days_prior}, --past-days-lookback {args.past_days_lookback}, "
          f"{'--all-rows' if args.all_rows else '--only-missing'}")
    print(f"{'row date':<12} {'run date':<12} {'end session':<13} "
          f"{'window':<20} {'endDateTime':<28} {'rows':>6}")
    print('-' * 96)
    for d in sorted(seen)[:40]:
        run_date, end_session, span, end_str = seen[d]
        print(f"{str(d):<12} {str(run_date):<12} {str(end_session):<13} "
              f"{span:<20} {(end_str or '(now)'):<28} {counts.get(d, 0):>6}")
    if len(seen) > 40:
        print(f"... {len(seen) - 40} more distinct date(s) not shown")

    print('-' * 96)
    accounted = (len(needs) + stats['complete'] + stats['no_date']
                 + stats['no_symbol'] + stats['unresolved'])
    print(f"rows needing a fetch : {len(needs)}   "
          f"already complete: {stats['complete']}   "
          f"no date: {stats['no_date']}   blank symbol: {stats['no_symbol']}   "
          f"unresolvable: {stats['unresolved']}")
    if accounted != len(df):
        print(f"  !! {len(df) - accounted} row(s) UNACCOUNTED FOR — this is a bug")
    print(f"FETCH UNITS (symbol x end_session): {len(units)}   "
          f"distinct symbols: {len({u[0] for u in units})}"
          f"{f'   (--limit would cut to {args.limit})' if args.limit else ''}")
    probes = 0
    if not args.no_split_adjust and args.split_source in ('both', 'price'):
        probes = len(plan_price_probes(df, row_dates, cal, unit_rows, args))
    print(f"This is the IBKR cost: one intraday request per unit, plus one "
          f"qualify per distinct symbol"
          + (f", plus {probes} daily bar(s) for the split cross-check "
             f"(--split-source yfinance skips these)." if probes else ".") + "\n")


# --------------------------------------------------------------------------- #
# --compare-to
# --------------------------------------------------------------------------- #

def _precision_tolerance(b_text: str, fb: float) -> float:
    """Half a unit in the reference's last decimal place.

    The trailing epsilon is not cosmetic. A value the reference stores at one
    decimal sits EXACTLY half a unit away from its neighbours, so the guard is
    always evaluated on the boundary: KLXE 2026-08-26 had backfill 9.7500 vs
    reference 9.8, where 9.8 - 9.75 == 0.05000000000000071 in binary floating
    point and failed `<= 0.05` by 7e-16. Widening by a relative hair makes the
    boundary inclusive without loosening the test in any way that matters.
    """
    decimals = len(b_text.split('.')[1]) if '.' in b_text else 0
    return max(0.5 * (10 ** -decimals), abs(fb) * 1e-6) * (1 + 1e-9)


def _classify(a_text: str, b_text: str) -> str:
    """'match' | 'near' | 'mismatch' for one backfill/reference pair.

    Compared at the REFERENCE's own precision: ITI_imputer rewrites ITI at one
    decimal (2632.5) while the raw addon writes %.4f, so an exact string test
    would fail on formatting alone.
    """
    fa, fb = _to_float(a_text), _to_float(b_text)
    if fa is None or fb is None:
        return 'match' if _norm(a_text) == _norm(b_text) else 'mismatch'
    tol = _precision_tolerance(b_text, fb)
    if abs(fa - fb) <= tol:
        return 'match'
    if abs(fa - fb) <= NEAR_MATCH_REL * max(abs(fa), abs(fb), 1e-9):
        return 'near'
    return 'mismatch'


_SESSION_SECONDS = {"RTH_avgITI_sec": 23400.0, "ETH_avgITI_sec": 34200.0}


def _tape_gain_symbols(got: pd.DataFrame, ref: pd.DataFrame, shared) -> set:
    """Symbols whose backfill saw MORE trades than the live run did.

    ITI is session_seconds / barCount, so barCount is recoverable from either
    side and the DIRECTION of the disagreement is diagnostic:

      - A tape can only GAIN trades after the fact (late and corrected prints
        settle in for hours-to-days after the 20:30 fetch). Re-fetching later
        therefore sees >= the live count, never fewer.
      - A windowing error has no such constraint: a wrong session window drops
        bars as readily as it adds them.

    So "trade count went up, on every column where both sides are usable" is a
    physically one-directional signature that cannot be produced by the bug
    this test exists to catch. Measured 2026-08-28 on 806 symbols: 108 of 108
    disagreeing RTH rows had gained trades, none lost any (median +2).

    A symbol qualifies only if NO usable ITI column shows a decrease -- one
    column going backwards disqualifies the whole symbol, so a windowing bug
    that happens to inflate one session still lands in MISMATCH.

    The test runs on the ITI values at the REFERENCE's own precision, never on
    the recovered counts. ITI_imputer rewrites the reference at one decimal, and
    the count is its reciprocal, so rounding is amplified without bound as ITI
    shrinks: OMH's stored `0.1` implies 234,000 trades, but a true 0.091 also
    stores as `0.1` and implies 257,000. Taking that 10% artefact for a real
    change made fast symbols look like they had LOST trades and wrongly
    disqualified them. Sub-tolerance differences are simply no evidence either
    way -- the same threshold _classify uses to call a cell matched.
    """
    gained = set()
    for sym in shared:
        up = False
        for col in _SESSION_SECONDS:
            if col not in got.columns or col not in ref.columns:
                continue
            a_text, b_text = _norm(got.at[sym, col]), _norm(ref.at[sym, col])
            fa, fb = _to_float(a_text), _to_float(b_text)
            if (fa is None or fb is None or fa <= 0 or fb <= 0
                    or fa == SENTINEL or fb == SENTINEL):
                continue
            tol = _precision_tolerance(b_text, fb)
            if fa < fb - tol:          # shorter interval => MORE trades
                up = True
            elif fa > fb + tol:        # longer interval => FEWER trades
                up = False
                break
        if up:
            gained.add(sym)
    return gained


def compare_frames(backfilled: pd.DataFrame, reference: pd.DataFrame,
                   split_factors: dict | None = None) -> bool:
    """Per-column agreement on the four columns. True on PASS.

    Three difference classes are expected rather than failures, because the
    reference is a live-run file and not a pure fetch record:
      - the imputers filled the reference cell (it holds an HGB prediction)
      - the reference cell is 44444 (the live fetch failed; re-fetching is the
        point of this script)
      - the reference cell is blank (the row was never attempted)
    A fourth, COVERAGE LOSS -- the backfill has nothing where the reference has
    a real value -- is reported separately and loudly: a large count means IBKR
    no longer serves that history, which invalidates the test rather than
    passing it.

    `split_factors` {symbol: cumulative factor since the anchor} turns this into
    the experiment that settles whether IBKR back-adjusts VOLUME as well as
    price. The prediction is sharp and falsifiable: symbols that split since the
    anchor should mismatch on RTH_tradeSize / ETH_TradeSize by exactly that
    factor while still MATCHING on the two ITI columns. Those cells are counted
    in their own `split` bucket and their observed ratio is printed, so the
    correction can be written against a measurement instead of an assumption.
    Splits are NOT yet corrected for -- see CLAUDE.md.
    """
    missing = [c for c in OUTPUT_COLUMNS if c not in reference.columns]
    if missing:
        log.error(f"Reference file lacks {missing} -- compare against a file the "
                  "live addon already enriched.")
        return False

    # Keep the first row per symbol: .at on a duplicated index returns a Series
    # and would blow up. Universe files are unique by symbol; a PR-stats file is
    # not, and --compare-to is not meant for one, but fail readably if it is.
    def _by_symbol(frame: pd.DataFrame) -> pd.DataFrame:
        indexed = frame.set_index(
            frame['Symbol'].astype(str).str.strip().str.upper())
        dupes = indexed.index.duplicated()
        if dupes.any():
            log.warning(f"{dupes.sum()} duplicate symbol row(s) — comparing the "
                        "first of each.")
        return indexed[~dupes]

    ref, got = _by_symbol(reference), _by_symbol(backfilled)
    shared = [s for s in got.index if s in ref.index]
    log.info(f"Comparing {len(shared)} symbols "
             f"(backfill {len(got)}, reference {len(ref)}).")

    split_factors = split_factors or {}
    split_syms = {s for s in shared
                  if split_factors.get(s) not in (None, 1.0)}
    if split_syms:
        log.info(f"{len(split_syms)} symbol(s) split between the anchor and today.")

    gained = _tape_gain_symbols(got, ref, shared)
    if gained:
        log.info(f"{len(gained)} symbol(s) show MORE trades in the backfill than "
                 f"the live run recorded — late tape reports, not a windowing "
                 f"error (see the `tape` column).")

    total_mismatch = total_coverage = 0
    split_ratios: dict = {}
    print(f"\n{'column':<18} {'match':>7} {'near':>6} {'expected':>9} "
          f"{'coverage':>9} {'split':>6} {'tape':>5} {'MISMATCH':>9}")
    print('-' * 76)
    for col in OUTPUT_COLUMNS:
        match = near = expected = coverage = split_n = tape_n = mismatch = 0
        examples = []
        flag_col, which = _IMPUTE_FLAG[col]
        for sym in shared:
            a, b = _norm(got.at[sym, col]), _norm(ref.at[sym, col])
            fb = _to_float(b)
            if not b or fb is None:
                expected += 1                      # never attempted in the live run
            elif fb == SENTINEL:
                expected += 1                      # live fetch failed
            elif flag_col in ref.columns and _is_imputed(ref.at[sym, flag_col], which):
                expected += 1                      # reference holds a prediction
            elif not a or _to_float(a) == SENTINEL:
                coverage += 1                      # we have nothing; ref does
                if len(examples) < 3:
                    examples.append(f"{sym}: no data (ref={b})")
            else:
                verdict = _classify(a, b)
                if verdict == 'match':
                    match += 1
                elif verdict == 'near':
                    near += 1
                elif sym in split_syms:
                    # Held out of MISMATCH so a known split cannot mask a real
                    # windowing bug -- and so the ratio below is measurable.
                    split_n += 1
                    fa = _to_float(a)
                    if fa and fb:
                        split_ratios.setdefault(col, []).append(
                            (sym, fa / fb, split_factors.get(sym)))
                elif sym in gained:
                    tape_n += 1
                else:
                    mismatch += 1
                    if len(examples) < 3:
                        examples.append(f"{sym}: backfill={a} ref={b}")
        total_mismatch += mismatch
        total_coverage += coverage
        flag = '  <-- ' + '; '.join(examples) if examples else ''
        print(f"{col:<18} {match:>7} {near:>6} {expected:>9} "
              f"{coverage:>9} {split_n:>6} {tape_n:>5} {mismatch:>9}{flag}")

    print('-' * 76)
    print(f"expected  = reference blank / 44444 / imputed (nothing to validate)")
    print(f"near      = within {NEAR_MATCH_REL:.1%} — late/corrected tape reports")
    print(f"coverage  = backfill has no data where the reference does: {total_coverage}")
    print(f"split     = disagreed AND the symbol split since the anchor "
          f"({len(split_syms)} such symbols)")
    print(f"tape      = disagreed AND the backfill saw MORE trades than the "
          f"live run ({len(gained)} such symbols) — late/corrected prints "
          f"settling in after the original 20:30 fetch. A windowing error "
          f"would also LOSE trades; these never do.")

    if split_ratios:
        # The falsifiable bit. If IBKR back-adjusts volume, backfill/reference
        # on the trade-size columns should equal the split factor, and the ITI
        # columns should not appear here at all.
        print("\nSPLIT DIAGNOSTIC — observed backfill/reference vs split factor")
        for col in OUTPUT_COLUMNS:
            rows = split_ratios.get(col)
            if not rows:
                print(f"  {col:<18} no disagreements among split symbols")
                continue
            shown = '; '.join(f"{s} obs={r:.4g} factor={f:.4g}"
                              for s, r, f in rows[:3] if f)
            print(f"  {col:<18} {len(rows):>4} case(s)   {shown}")
        print("  Expect: trade-size ratios tracking the factor, ITI columns clean.")

    if total_mismatch == 0:
        print("\nRESULT: PASS — every comparable value reproduces the live addon.\n")
        return True
    print(f"\nRESULT: FAIL — {total_mismatch} unexplained mismatches.")
    print("A windowing error is SYSTEMATIC (nearly every symbol, large delta); "
          "a scatter of small ones is tape noise.\n")
    return False


# --------------------------------------------------------------------------- #
# driver
# --------------------------------------------------------------------------- #

def process_file(path: str, file_date, args) -> bool:
    out_dir = args.output_dir or os.path.dirname(os.path.abspath(path))
    stem = os.path.splitext(os.path.basename(path))[0]
    out_path = os.path.join(out_dir, f"{stem}_freqSizeBack.tsv")
    if (os.path.exists(out_path) and not args.overwrite
            and not args.compare_to and not args.check_dates):
        log.info(f"Skipping {os.path.basename(path)} — "
                 f"{os.path.basename(out_path)} exists (use --overwrite).")
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

    known = [d for d in row_dates if d is not None]
    if not known:
        log.error(f"{path}: no resolvable row dates — skipping.")
        return False
    cal = build_calendar(known)

    if args.check_dates:
        describe_dates(path, df, row_dates, cal, args)
        return True

    log.info(f"Input: {path} ({len(df)} rows, {len(df.columns)} cols), "
             f"anchored on {label}, days_prior={args.days_prior}, "
             f"lookback={args.past_days_lookback}")

    enriched = enrich(df, row_dates, cal, args)

    os.makedirs(out_dir, exist_ok=True)
    enriched.to_csv(out_path, sep='\t', index=False)
    log.info(f"Saved {out_path} ({len(enriched)} rows, {len(enriched.columns)} cols)")

    if args.compare_to:
        ref_path = path if args.compare_to == 'same' else args.compare_to
        log.info(f"Comparing against {ref_path}")
        return compare_frames(enriched, read_input(ref_path),
                              collect_split_factors(enriched, row_dates, cal, args))
    return True


def collect_split_factors(df: pd.DataFrame, row_dates, cal: Sessions, args) -> dict:
    """{symbol: cumulative split factor since its anchor} for --compare-to.

    Best-effort: any failure leaves the dict empty and the comparison simply
    loses the split bucket rather than the whole run.
    """
    if args.no_split_report:
        return {}
    anchors: dict = {}
    for i, row_date in zip(df.index, row_dates):
        symbol = _norm_symbol(df.at[i, 'Symbol'])
        if not symbol or row_date is None or symbol in anchors:
            continue
        _run, end_session, _days = resolve_window(
            cal, row_date, args.days_prior, args.past_days_lookback)
        if end_session is not None:
            anchors[symbol] = end_session
    if not anchors:
        return {}
    try:
        msc = _load_msc()
        splits = msc.fetch_all_splits(sorted(anchors), Path(args.split_cache_dir),
                                      True, args.split_workers,
                                      args.split_max_age_days)
    except Exception as exc:
        log.warning(f"Split lookup unavailable ({type(exc).__name__}: {exc}); "
                    "the comparison loses its split bucket.")
        return {}
    return {sym: split_factor_since(splits.get(sym), end)
            for sym, end in anchors.items()}


def setup_logging(log_file, verbose: bool) -> None:
    """Own handlers on a named logger, plus the addon's module logger.

    The addon logs per-symbol fetch failures through logging.getLogger(__name__)
    -- "trade_frequency_and_size_addOn" once loaded by path -- so route it here
    or those warnings vanish.
    """
    if log.handlers:
        return
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter('%(asctime)s %(levelname)-8s %(message)s',
                            '%Y-%m-%d %H:%M:%S')
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file is None:
        day_dir = os.path.join(_SCRIPT_DIR, 'runs',
                               datetime.now().strftime('%d-%b-%Y'))
        os.makedirs(day_dir, exist_ok=True)
        log_file = os.path.join(day_dir, 'trade_freq_size_backfill.log')
    handlers.append(logging.FileHandler(log_file))
    for handler in handlers:
        handler.setFormatter(fmt)

    for target in (log, logging.getLogger("trade_frequency_and_size_addOn")):
        target.setLevel(level)
        target.propagate = False
        for handler in handlers:
            target.addHandler(handler)
    logging.getLogger('ib_insync').setLevel(
        logging.INFO if verbose else logging.ERROR)
    log.info(f"Logging to {log_file}")


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Backfill RTH/ETH avg inter-trade interval and trade size '
                    'onto historical universe TSVs, point-in-time.',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--input', '-i', required=True, nargs='+', metavar='FILE',
                   help='input TSV(s) with a Symbol column; globs accepted')
    p.add_argument('--as-of', metavar='YYYY-MM-DD', default=None,
                   help='override the date parsed from the filename; also '
                        'forces single-anchor mode over an auto-detected '
                        'date column')
    p.add_argument('--date-col', metavar='COL', default=None,
                   help='resolve each row from this column (e.g. ArrivalDate) '
                        f'instead of one date per file. Auto-detected from '
                        f'{DATE_COLUMN_CANDIDATES} when present.')
    p.add_argument('--days-prior', type=int, default=DEFAULT_DAYS_PRIOR,
                   metavar='N',
                   help='ANCHOR shift: how many nightly runs back to replay. '
                        '1 = the run a PR arriving the next morning was traded '
                        "against (leak-free); 0 = a universe file's own run, "
                        'and REQUIRED for --compare-to.')
    p.add_argument('--past-days-lookback', type=int, default=1, metavar='L',
                   help='WINDOW LENGTH: sessions averaged, ending at the '
                        'anchor. Not the same knob as --days-prior. The '
                        'nightly cron uses 1.')
    p.add_argument('--check-dates', action='store_true',
                   help='print the resolved sessions and the fetch cost, then '
                        'exit without connecting to IBKR')
    p.add_argument('--all-rows', action='store_true',
                   help='recompute every row instead of only those whose cells '
                        'are blank or carry the 44444 sentinel')
    p.add_argument('--limit', type=int, default=0, metavar='N',
                   help='fetch at most N units (0 = no limit); rows behind the '
                        'rest keep their original values')
    p.add_argument('--output-dir', '-o', metavar='DIR', default=None,
                   help="where to write <stem>_freqSizeBack.tsv (default: the "
                        "input's directory)")
    p.add_argument('--overwrite', action='store_true',
                   help='rewrite an existing output file')
    p.add_argument('--compare-to', metavar='FILE', default=None,
                   help="validate against a file the live addon enriched; "
                        "'same' compares against the input itself. Needs "
                        "--days-prior 0.")

    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=4002,
                   help='4002 = paper Gateway. Use it: the live GW (4001) '
                        'starves historical queries under the trading stack.')
    p.add_argument('--clientID', type=int, default=14,
                   help='clear of clerk (1000-1029), dailyStats (77), '
                        'daily-board (20000) and the addon CLI (12)')
    p.add_argument('--freq-concurrency', type=int, default=2)
    p.add_argument('--freq-timeout', type=int, default=60)
    p.add_argument('--freq-retries', type=int, default=0)
    p.add_argument('--freq-bar-size', default=_FREQ_BAR_SIZE)
    p.add_argument('--qualify-concurrency', type=int, default=8)
    p.add_argument('--split-cache-dir', default=_MSC_CACHE,
                   help="market_state_calculator's split cache, shared so a "
                        'symbol is looked up once across both tools')
    p.add_argument('--split-workers', type=int, default=8,
                   help='yfinance threads for the --compare-to split lookup')
    p.add_argument('--split-max-age-days', type=int, default=1, metavar='N',
                   help='age after which a cached split list is re-queried '
                        "from yfinance (MSC's own default). Pass 36500 "
                        '(fetch-if-absent, never re-query) for historical '
                        'work: for a DELISTED symbol yfinance returns empty '
                        'splits with live metadata, so a refresh writes a '
                        "header-only 'never split' file over real history "
                        '(NTZ, OMH on 2026-09-07) and the size restatement '
                        'then silently uses a factor of 1.0.')
    p.add_argument('--no-split-report', action='store_true',
                   help='skip the split bucket in --compare-to (no yfinance)')
    p.add_argument('--split-source', choices=('both', 'yfinance', 'price'),
                   default='both',
                   help="where the split factor comes from. 'both' (default) "
                        "takes yfinance's exact factor and audits it against "
                        "IBKR's own price adjustment, costing one extra daily "
                        "bar per (symbol, session) -- the fast path, and the "
                        'only setting that catches a split yfinance never '
                        "recorded. 'yfinance' skips the probe; 'price' ignores "
                        'the split table entirely.')
    p.add_argument('--price-concurrency', type=int, default=12,
                   help='daily bars are cheap; this is the Step-2 default, not '
                        'the intraday one')
    p.add_argument('--price-timeout', type=int, default=30)
    p.add_argument('--price-retries', type=int, default=1)
    p.add_argument('--no-split-adjust', action='store_true',
                   help="write trade sizes as IBKR returns them, i.e. in "
                        "TODAY's share terms. Off by default: IBKR "
                        'back-adjusts volume, so raw values disagree with the '
                        'live run by the split factor. For debugging only.')
    p.add_argument('--batch-size', type=int, default=200)
    p.add_argument('--log', '-l', metavar='FILE', dest='log_file', default=None)
    p.add_argument('--verbose', '-v', action='store_true')
    return p


def main() -> int:
    args = build_arg_parser().parse_args()
    setup_logging(args.log_file, args.verbose)

    if args.days_prior < 0:
        log.error(f"--days-prior must be >= 0, got {args.days_prior}.")
        return 2
    if args.past_days_lookback < 1:
        log.error(f"--past-days-lookback must be >= 1, got {args.past_days_lookback}.")
        return 2
    if args.past_days_lookback > _MAX_LOOKBACK_WARN:
        log.warning(f"--past-days-lookback {args.past_days_lookback} exceeds the "
                    f"~{_MAX_LOOKBACK_WARN} trading days IBKR reliably returns "
                    f"within its 30-calendar-day intraday cap; the oldest "
                    f"sessions will silently contribute nothing.")
    if args.compare_to and args.days_prior != 0:
        log.error(f"--compare-to with --days-prior {args.days_prior}: the "
                  "reference records its OWN nightly run, so the anchors would "
                  "differ by a session and every row would look wrong. Pass "
                  "--days-prior 0 to validate.")
        return 2

    override = None
    if args.as_of:
        try:
            override = datetime.strptime(args.as_of, '%Y-%m-%d').date()
        except ValueError:
            log.error(f"--as-of {args.as_of!r} is not YYYY-MM-DD.")
            return 2

    paths: list = []
    for pattern in args.input:
        paths.extend(sorted(globmod.glob(pattern))
                     if any(c in pattern for c in '*?[') else [pattern])

    jobs: list = []
    for path in paths:
        if not os.path.exists(path):
            log.error(f"{path}: not found — skipping.")
            continue
        # A date column can supply the anchor per row, so a file with no date in
        # its name is only fatal once resolve_row_dates finds nothing either.
        jobs.append((override or date_from_filename(path) or date.max, path))
    if not jobs:
        log.error("Nothing to do.")
        return 2
    jobs.sort()   # oldest first

    failures = 0
    for file_date, path in jobs:
        try:
            ok = process_file(
                path, None if file_date is date.max else file_date, args)
        except Exception:
            log.exception(f"{path}: unhandled error")
            ok = False
        if not ok:
            failures += 1
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
