#!/usr/bin/env python3
"""
marketcap_backfill_AddON.py — reconstruct MarketCap_M for the files that predate it.

`MarketCap_M` was added to Step 1 on 2026-06-05, so the sixteen
`nasdaq_symbols_data_priced_2026-05-14 … 2026-06-01` files carry `Float_M` but
no market cap. This script computes it as of each file's own date and writes a
sibling `<input_stem>_mcapBack.tsv`. **Inputs are never modified** and there is
no `--in-place`, matching `ShortInterrest_backfill_AddON.py` and
`trade_frequency_and_size_backfill_AddON.py`.

The identity
------------
Per `universe_finder/CLAUDE.md`, `MarketCap_M` is yfinance's `marketCap`, i.e.
**shares outstanding × price** — NOT float × price. Every input row already
carries its own contemporaneous `LastDailyClosePrice`, fetched from IBKR on the
night of that run, so the only unknown is the share count:

    MarketCap_M(D) = LastDailyClosePrice(D) × shares_out_as_of(D) / 1e6

Because the row's close is in that session's share terms and the share count is
taken as of the same session, the two agree and **no split adjustment is
needed** — unlike the trade-size backfill, where IBKR back-adjusts volume.

Where the share count comes from
--------------------------------
ChartExchange's `data/stocks/shares/` endpoint would be the natural source, but
the account's API key is deprecated and now answers **HTTP 401** on every
symbol, so that path is closed. Two sources remain, and for each symbol the
observation **nearest in time** to the row's date wins:

  A. `implied_prod` — the production files from 2026-06-09 onward record both
     `MarketCap_M` and `LastDailyClosePrice`, so `mcap × 1e6 / close` recovers
     the share count that Step 1 actually used. Only the unfiltered ~7 000-row
     June files are read (`2026-06-09` plus the `*_nonFiltered` ones through
     `2026-06-21`); the filtered files would bias coverage toward nano-caps.

     ⚠ These are combined by **cross-file median, per symbol** rather than
     taken from any single file. Step 1's fundamentals cache has a 24 h TTL, so
     on some nights the market cap is priced off a different session than the
     Step 2 close and that file's implied share count is systematically off.
     Measured: 2026-06-10, 06-12 and 06-17 land within 1% of the cross-file
     median for only 33.5% / 33.6% / 24.8% of symbols, while every other file
     manages 94-99%. The median is immune; a single-file estimate is not.

  B. `yfinance` — `Ticker.get_shares_full()` returns a *dated* shares series
     (filing-driven, so sparse). The latest point on or before the row's date
     is used. This is what wins for the older May dates, where a filing landed
     days before the row while the June observation is three weeks after it.

Accuracy (measured, not assumed)
--------------------------------
Validated by reconstructing a file that already HAS the column, which is the
only ground truth available. Predicting `stocks_universe_2026-06-09.tsv` from
the eleven other unfiltered June files:

    n=4973   within 1%: 96.9%   within 5%: 98.3%   within 25%: 99.3%

Source A carried back 6 days beats source B alone (96.7% vs 80.0% within 1% on
the sub-$100 M cohort), and scaling by the contemporaneous float ratio — an
obvious-looking correction — made it **worse** (87.7%), so it is not applied.

The residual error is real share-count drift: 46% of symbols show a byte-exact
implied share count over six days, and of those that move more than 1%, 105 of
161 also moved their float, i.e. they genuinely issued stock. Error therefore
grows with the gap, and the May files sit 8-26 days from the nearest production
observation. **Every row records which source and which observation date was
used**, so a consumer can discard the ones carried too far rather than trust
the column blindly.

Provenance columns (suppress with `--no-provenance`)
    MarketCap_Source  universe_join | implied_prod | yfinance | none
    MarketCap_AsOf    date of the share-count observation used
    MarketCap_LagDays signed days from that observation to the row's date
                      (negative = observation predates the row)

PR-stats mode (`--date-column`, default `ArrivalDate`)
------------------------------------------------------
The same script also backfills the `MktCap` column of a PR-stats TSV, which
absorbed the job of the deleted `price_stats_loader/mktcap_backfill_addOn.py`.
It switches on automatically when the date column is present: dates then come
per ROW rather than from the filename, and `MktCap`/`Float` are used in place of
`MarketCap_M`/`Float_M`. Two passes, in order:

  **Pass 1 — universe join.** `MktCap` is not a computation: the orchestrator
  wrote it straight out of the `stocks_universe_*.tsv` it had loaded at start.
  Replicating that is a lookup, so it is anchored on the CALENDAR day before the
  row (`ArrivalDate - 1`), taking the newest mcap-bearing file on or before it.
  Calendar, not session, because the cron writes files on weekends too and a
  Monday PR was genuinely served by Sunday's file. (Those weekend files carry
  Friday's close — 6291 of 6292 shared symbols are byte-equal to the Friday
  file — so the basis is the same either way; the calendar rule just names the
  file the orchestrator actually held.)

  **Pass 2 — the formula**, for rows no universe file can serve: everything
  before 2026-05-14, plus the few symbols missing from their anchor file. This
  is a reconstruction rather than a replay, so it anchors on the SESSION rule
  established in `tools/market_state/market_state_calculator.py` —
  `Sessions.resolve(date, days_prior)`, `--days-prior` defaulting to 1.

⚠ **Splits must be divided out in pass 2**, unlike the universe-file case above.
The invariant that made the identity safe — close and share count drawn from the
same session — does NOT hold here. A PR row's `LastDailyClosePrice` is
deliberately on the AS-TRADED basis of its own date (see `lastDailyClose_AddOn.py`),
while the implied share count is in the June production files' share terms, so a
symbol that split in between has its two sides on different bases:

    shares_at_row = shares_observed / F,  F = forward_split_factor(splits, row)

bounded above at the observation date, since a split AFTER the observation is
already in neither number. Omitting this understates the market cap by F —
measured on this corpus, 20 of 765 formula rows across 14 symbols, worst case
EZRA at F=0.025, i.e. 40x too small. `F is None` (unknown, not 1.0) leaves the
cell blank rather than filling it wrong.

Usage
-----
    /home/tom/venv/bin/python marketcap_backfill_AddON.py \
        -i 'data/nasdaq_symbols_data_priced_2026-05-*.tsv'

    # acceptance test against a file that already has the column
    /home/tom/venv/bin/python marketcap_backfill_AddON.py \
        -i data/stocks_universe_2026-06-09.tsv --compare-to same

    # PR-stats: fill blank MktCap only, no provenance columns
    /home/tom/venv/bin/python marketcap_backfill_AddON.py --no-provenance \
        -i ../price_stats_loader/outputs/PRstats_..._DailyClose.tsv
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import glob
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_DATA_DIR = _SCRIPT_DIR / "data"

# Sessions/forward_split_factor are imported rather than retyped: the
# days_prior decrement for non-session dates is subtle enough that a local copy
# would eventually drift from the convention every other add-on follows.
_MSC_DIR = _SCRIPT_DIR.parent / "tools" / "market_state"
if str(_MSC_DIR) not in sys.path:
    sys.path.insert(0, str(_MSC_DIR))
from market_state_calculator import (            # noqa: E402
    Sessions, fetch_splits, forward_split_factor, split_cache_path)

_MSC_CACHE_DIR = _MSC_DIR / "tables"             # split_cache_path appends /splits

MCAP_COL = "MarketCap_M"
CLOSE_COL = "LastDailyClosePrice"
FLOAT_COL = "Float_M"
SENTINEL = "44444"
PROV_COLS = ["MarketCap_Source", "MarketCap_AsOf", "MarketCap_LagDays"]

# PR-stats aliases. Presence of the date column is what selects PR-stats mode;
# these just say what the equivalent columns are called there.
PR_MCAP_COL = "MktCap"
PR_FLOAT_COL = "Float"

# Universe files that can serve a pass-1 join. The _mcapBack files are this
# script's own output for the pre-2026-06-05 era and are what extends join
# coverage back to 2026-05-14; _SIback files are enrichment sidecars, not
# universe snapshots, and would double-count a date.
_UNIVERSE_GLOBS = ["stocks_universe_*.tsv",
                   "nasdaq_symbols_data_priced_*_mcapBack.tsv"]

# Unfiltered production files that can donate an implied share count. 06-09 is
# full-universe in its own right; from 06-10 the *_nonFiltered sidecars are.
# Deliberately excludes the filtered files -- they are nano-cap only and would
# bias which symbols get coverage.
_PROD_GLOBS = ["stocks_universe_2026-06-09.tsv",
               "stocks_universe_2026-*_nonFiltered.tsv"]

log = logging.getLogger("marketcap_backfill")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _to_float(val):
    """Parse a cell, treating blank / NaN / the 44444 sentinel as absent."""
    if val is None:
        return None
    text = str(val).strip()
    if not text or text.lower() in ("nan", "none") or text == SENTINEL:
        return None
    try:
        num = float(text)
    except ValueError:
        return None
    if num == 44444.0:
        return None
    return num


def _fmt(value: float) -> str:
    """4dp, trailing zeros trimmed -- matches the pre-float_format May files."""
    return f"{round(value, 4):.4f}".rstrip("0").rstrip(".")


def _norm_symbol(sym: str) -> str:
    return str(sym).strip().upper()


def parse_file_date(path: str) -> date | None:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(path))
    return date.fromisoformat(m.group(1)) if m else None


def read_tsv(path: str) -> pd.DataFrame:
    """dtype=str + keep_default_na=False: preserves byte formatting of every
    untouched column and stops pandas eating the ticker NA (Nano Labs)."""
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)


def resolve_columns(df) -> tuple[str, str]:
    """(market-cap column, float column) for this file's flavour.

    PR-stats calls them MktCap/Float, universe files MarketCap_M/Float_M.
    """
    mcap = PR_MCAP_COL if PR_MCAP_COL in df.columns else MCAP_COL
    flt = PR_FLOAT_COL if PR_FLOAT_COL in df.columns else FLOAT_COL
    return mcap, flt


# ---------------------------------------------------------------------------
# pass 1 -- the universe-file join
# ---------------------------------------------------------------------------
class UniverseIndex:
    """Symbol -> market cap, per universe-file date, loaded on demand.

    Only a handful of dates are ever touched (one per distinct ArrivalDate in
    the input), so the files are parsed lazily and memoised rather than read up
    front -- the full set is ~100 files and several hundred MB.
    """

    def __init__(self, data_dir: Path):
        by_date: dict[date, list[str]] = {}
        for pattern in _UNIVERSE_GLOBS:
            for path in sorted(glob.glob(str(data_dir / pattern))):
                base = os.path.basename(path)
                if "siback" in base.lower():
                    continue
                day = parse_file_date(path)
                if day is not None:
                    by_date.setdefault(day, []).append(path)
        self._paths = by_date
        self.dates = sorted(by_date)
        self._cache: dict[date, dict[str, str]] = {}
        if self.dates:
            log.info("  universe index: %d date(s) %s .. %s",
                     len(self.dates), self.dates[0], self.dates[-1])
        else:
            log.warning("  universe index EMPTY -- no join source in %s", data_dir)

    def newest_on_or_before(self, day: date) -> date | None:
        candidates = [d for d in self.dates if d <= day]
        return candidates[-1] if candidates else None

    def mcaps(self, day: date) -> dict[str, str]:
        """{SYMBOL: market-cap text} for one universe date, blanks dropped.

        The value is carried across as TEXT: it is the production number the
        orchestrator itself would have read, and reformatting it would make the
        backfilled rows differ from the live ones for no reason.
        """
        if day in self._cache:
            return self._cache[day]
        out: dict[str, str] = {}
        for path in self._paths.get(day, []):
            try:
                df = read_tsv(path)
            except Exception as exc:                   # pragma: no cover
                log.warning("  unreadable universe file %s: %s",
                            os.path.basename(path), exc)
                continue
            if MCAP_COL not in df.columns or "Symbol" not in df.columns:
                continue
            for sym, val in zip(df["Symbol"].map(_norm_symbol), df[MCAP_COL]):
                if _to_float(val) is not None:
                    out[sym] = str(val).strip()        # later file wins
        self._cache[day] = out
        return out


# ---------------------------------------------------------------------------
# split basis
# ---------------------------------------------------------------------------
class SplitFactors:
    """forward_split_factor() per symbol, memoised, served from the MSC cache.

    Reads `tools/market_state/tables/splits/`. The freshness rule that governs
    MSC (SPLIT_CACHE_MAX_AGE_DAYS = 1) exists because a split posted in the last
    day or two may not yet be in IBKR's bars; that hazard does not apply to a
    backfill whose anchors are months old, so the cache is accepted at any age.

    ⚠ **Cache-only unless --split-network.** fetch_splits falls through to
    yfinance on a miss, and the cache covers 3313 symbols against the 7143 in a
    full universe file — an uncorrected run would burst ~3800 live calls at the
    same endpoint the nightly Step 1 fundamentals depend on, which is the exact
    way this machine got blocked before (see fetch_yf_shares). A miss therefore
    returns None, meaning "unknown", which blanks the cell rather than filling
    it on a guessed 1.0.
    """

    def __init__(self, cache_dir: Path, enabled: bool = True,
                 allow_network: bool = False, max_age_days: int = 36500):
        self._cache_dir = cache_dir
        self._enabled = enabled
        self._allow_network = allow_network
        self._max_age = max_age_days
        self._splits: dict[str, list | None] = {}
        self.misses: set[str] = set()

    def _for(self, symbol: str):
        if symbol not in self._splits:
            if not self._allow_network and not split_cache_path(
                    self._cache_dir, symbol).exists():
                self.misses.add(symbol)
                self._splits[symbol] = None
                return None
            try:
                self._splits[symbol] = fetch_splits(
                    symbol, self._cache_dir, use_cache=True,
                    max_age_days=self._max_age)
            except Exception:                          # pragma: no cover
                self._splits[symbol] = None
        return self._splits[symbol]

    def factor(self, symbol: str, anchor: date, observed: date) -> float | None:
        """Cumulative split factor over (anchor, observed].

        `observed` bounds the product from above: a split after the share count
        was observed is in neither side of the identity, so multiplying it out
        would introduce the very error this corrects. forward_split_factor
        excludes `d >= bars_fetched_at`, hence the +1 day to keep a split ON the
        observation date inside the window.
        """
        if not self._enabled:
            return 1.0
        splits = self._for(symbol)
        if splits is None:
            return None
        return forward_split_factor(splits, anchor, observed + timedelta(days=1))


# ---------------------------------------------------------------------------
# source A -- share counts implied by the production files
# ---------------------------------------------------------------------------
def build_implied_shares(data_dir: Path,
                         exclude_dates=()) -> tuple[pd.Series, date | None]:
    """Cross-file median implied share count per symbol, and the earliest date
    contributing to it (used as the observation's nominal date).

    `exclude_dates` drops production files of those dates. The caller passes
    the input files' own dates so a file can never help reconstruct itself --
    without this the acceptance test would be scoring self-prediction and would
    report a fake ~100%.

    Returns an empty Series if no production file carries MarketCap_M.
    """
    paths = []
    for pattern in _PROD_GLOBS:
        paths.extend(sorted(glob.glob(str(data_dir / pattern))))
    paths = [p for p in paths if "back" not in os.path.basename(p).lower()]

    excluded = set(exclude_dates)
    per_file, dates = {}, []
    for path in paths:
        day = parse_file_date(path)
        if day is None:
            continue
        if day in excluded:
            log.info("  excluding %s from source A (it is an input date)",
                     os.path.basename(path))
            continue
        try:
            df = read_tsv(path)
        except Exception as exc:                       # pragma: no cover
            log.warning("  skipping %s: %s", os.path.basename(path), exc)
            continue
        if MCAP_COL not in df.columns or CLOSE_COL not in df.columns:
            continue
        close = df[CLOSE_COL].map(_to_float)
        mcap = df[MCAP_COL].map(_to_float)
        ok = close.notna() & mcap.notna() & (close > 0) & (mcap > 0)
        if not ok.any():
            continue
        implied = pd.Series(
            (mcap[ok].astype(float) * 1e6 / close[ok].astype(float)).values,
            index=df.loc[ok, "Symbol"].map(_norm_symbol).values)
        per_file[day] = implied[~implied.index.duplicated(keep="last")]
        dates.append(day)

    if not per_file:
        log.warning("  no production file carries %s -- source A unavailable",
                    MCAP_COL)
        return pd.Series(dtype=float), None

    table = pd.DataFrame(per_file)
    median = table.median(axis=1)
    log.info("  source A: %d symbols from %d production file(s) %s .. %s",
             median.notna().sum(), len(per_file), min(dates), max(dates))
    return median.dropna(), min(dates)


# ---------------------------------------------------------------------------
# source B -- yfinance dated shares series
# ---------------------------------------------------------------------------
class RateLimited(RuntimeError):
    """Yahoo is refusing us. Raised so the run STOPS instead of hammering."""


_YF_CACHE = _DATA_DIR / "cache" / "yf_shares.json"


def _load_yf_cache() -> dict:
    if not _YF_CACHE.exists():
        return {}
    try:
        import json
        raw = json.loads(_YF_CACHE.read_text())
        return {k: [(date.fromisoformat(d), float(v)) for d, v in pts]
                for k, pts in raw.items()}
    except Exception:                                  # pragma: no cover
        log.warning("  yfinance cache unreadable — starting empty")
        return {}


def _save_yf_cache(points: dict):
    try:
        import json
        _YF_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _YF_CACHE.write_text(json.dumps(
            {k: [[d.isoformat(), v] for d, v in pts] for k, pts in points.items()}))
        log.info("  yfinance cache: %d symbol(s) at %s",
                 len(points), _YF_CACHE)
    except Exception as exc:                           # pragma: no cover
        log.warning("  could not write yfinance cache: %s", exc)


def _shares_series(symbol: str, start: str, end: str):
    """Dated shares-outstanding series, de-duplicated and sorted.

    Returns None when the symbol simply has no series. Raises RateLimited when
    Yahoo blocks us -- that is NOT a per-symbol failure and must not be
    swallowed, or the run burns thousands of calls deepening the block.
    """
    try:
        import yfinance as yf
        ser = yf.Ticker(symbol).get_shares_full(start=start, end=end)
    except Exception as exc:
        if "RateLimit" in type(exc).__name__ or "Too Many Requests" in str(exc):
            raise RateLimited(str(exc)) from exc
        return None
    if ser is None or len(ser) == 0:
        return None
    ser = ser[~ser.index.duplicated(keep="last")].sort_index()
    return ser


def fetch_yf_shares(symbols, start: str, end: str, workers: int,
                    use_cache: bool = True) -> dict:
    """{symbol: [(date, shares), ...]}, disk-cached and rate-limit aware.

    ⚠ Yahoo rate-limits hard. A 7 143-symbol burst at 10 workers got this
    machine blocked outright (every call `YFRateLimitError`) -- and the nightly
    `NASDAQ_symbols_data.py` Step 1 depends on the same endpoint, with finviz
    dead, so a block here breaks the 20:30 cron's fundamentals. Hence: low
    default concurrency, a disk cache so a re-run costs nothing, and an
    immediate abort on the first rate-limit rather than pushing through.
    """
    out = _load_yf_cache() if use_cache else {}
    todo = [s for s in symbols if s not in out]
    if not todo:
        log.info("  source B: all %d symbol(s) served from cache", len(symbols))
        return out

    total = len(todo)
    log.info("  source B: querying yfinance for %d symbol(s) (%d cached), "
             "%d workers …", total, len(out) , workers)
    done, limited = 0, False
    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_shares_series, s, start, end): s for s in todo}
        for fut in cf.as_completed(futures):
            sym = futures[fut]
            done += 1
            if done % 250 == 0:
                log.info("    shares %d/%d", done, total)
            if limited:
                continue
            try:
                ser = fut.result()
            except RateLimited as exc:
                limited = True
                log.error("  source B ABORTED — Yahoo rate-limited us (%s). "
                          "Partial results kept and cached; re-run later to "
                          "resume. Source A is unaffected.", exc)
                for pending in futures:
                    pending.cancel()
                continue
            except Exception:                          # pragma: no cover
                continue
            if ser is not None:
                out[sym] = [(ts.date(), float(v)) for ts, v in ser.items()]

    if use_cache:
        _save_yf_cache(out)
    log.info("  source B: %d symbol(s) have a shares series", len(out))
    return out


def yf_asof(points, day: date):
    """Latest (date, shares) on or before `day`; None if the series starts after."""
    prior = [p for p in points if p[0] <= day]
    return prior[-1] if prior else None


# ---------------------------------------------------------------------------
# the estimator
# ---------------------------------------------------------------------------
def choose_shares(symbol, day, implied, implied_date, yf_points, prefer):
    """Pick the share-count observation nearest in time to `day`.

    Returns (shares, source, as_of, lag_days) or (None, "none", "", "").
    lag_days is signed: negative when the observation predates the row.
    """
    candidates = []
    if prefer != "yfinance":
        val = implied.get(symbol)
        if val is not None and pd.notna(val) and val > 0 and implied_date:
            candidates.append((float(val), "implied_prod", implied_date))
    if prefer != "implied":
        hit = yf_asof(yf_points.get(symbol, []), day)
        if hit and hit[1] > 0:
            candidates.append((float(hit[1]), "yfinance", hit[0]))

    if not candidates:
        return None, "none", "", ""
    # nearest in time; ties go to the production observation, which is a direct
    # record of what Step 1 used rather than a filing-date approximation.
    shares, source, as_of = min(
        candidates, key=lambda c: (abs((c[2] - day).days),
                                   0 if c[1] == "implied_prod" else 1))
    return shares, source, as_of.isoformat(), (as_of - day).days


def row_dates(df, day, date_col):
    """Per-row date list. Falls back to the file's own date for every row.

    PR-stats rows each carry their own ArrivalDate; a universe file has one date
    for the whole file, taken from the filename. Returning a list either way
    keeps enrich() from having to care which it is.
    """
    if date_col and date_col in df.columns:
        out = []
        for text in df[date_col]:
            try:
                out.append(date.fromisoformat(str(text).strip()[:10]))
            except ValueError:
                out.append(None)
        return out
    return [day] * len(df)


def enrich(df, day, implied, implied_date, yf_points, args,
           universe=None, sessions=None, splits=None):
    """Return (out_df, stats). `df` is never mutated.

    Blank market caps are served by the universe join first and the close x
    shares formula second; a row that already has a value is left alone unless
    --all-rows.
    """
    out = df.copy()
    mcap_col, float_col = resolve_columns(df)
    dates = row_dates(df, day, args.date_column)
    values, sources, asofs, lags = [], [], [], []
    stats = {"joined": 0, "filled": 0, "no_close": 0, "no_shares": 0,
             "no_split": 0, "no_date": 0, "kept": 0}

    for (_, row), row_day in zip(df.iterrows(), dates):
        symbol = _norm_symbol(row["Symbol"])
        existing = _to_float(row.get(mcap_col)) if mcap_col in df.columns else None
        if existing is not None and not args.all_rows:
            values.append(str(row[mcap_col]))
            sources.append("existing")
            asofs.append("")
            lags.append("")
            stats["kept"] += 1
            continue

        def blank(reason):
            values.append("")
            sources.append("none")
            asofs.append("")
            lags.append("")
            stats[reason] += 1

        if row_day is None:
            blank("no_date")
            continue

        # -- pass 1: what the orchestrator itself would have read ------------
        if universe is not None:
            src_day = universe.newest_on_or_before(row_day - timedelta(days=1))
            if src_day is not None:
                hit = universe.mcaps(src_day).get(symbol)
                if hit:
                    values.append(hit)
                    sources.append("universe_join")
                    asofs.append(src_day.isoformat())
                    lags.append(str((src_day - row_day).days))
                    stats["joined"] += 1
                    continue

        # -- pass 2: reconstruct it ------------------------------------------
        close = _to_float(row.get(CLOSE_COL))
        if close is None or close <= 0:
            blank("no_close")
            continue

        anchor = sessions.resolve(row_day, args.days_prior) if sessions else row_day
        if anchor is None:
            blank("no_date")
            continue

        shares, source, as_of, lag = choose_shares(
            symbol, anchor, implied, implied_date, yf_points, args.shares_source)
        if shares is None:
            blank("no_shares")
            continue

        # The share count is in its observation date's terms, the close is in
        # the row's. Reconcile before multiplying, and refuse to guess: an
        # unknown factor is not 1.0.
        #
        # Anchored on row_day, NOT on the session anchor above. The window is a
        # question about what basis the CLOSE is on, and lastDailyClose_AddOn
        # decided that using ArrivalDate (the convention settled in
        # fix_lastdailyclose_sessions.py FIX 1). Using the session anchor
        # instead straddles any ex-date falling between the two, and re-applies
        # a correction the price already carries: EZRA's 2026-05-17 1-for-40
        # sits between resolve(2026-05-18, 1) = 05-15 and the 05-18 row, whose
        # close is flagged none_needed and is already post-split — correcting it
        # inflated that row 40x. Agreement with LastDailyClosePrice_SplitAdj is
        # exact on every affected row under this anchor and not under the other.
        factor = splits.factor(symbol, row_day,
                               date.fromisoformat(as_of)) if splits else 1.0
        if factor is None or factor <= 0:
            blank("no_split")
            continue

        values.append(_fmt(close * (shares / factor) / 1e6))
        sources.append(source)
        asofs.append(as_of)
        lags.append(str(lag))
        stats["filled"] += 1

    out[mcap_col] = values
    if not args.no_provenance:
        for col, vals in zip(PROV_COLS, (sources, asofs, lags)):
            out[col] = vals

    # Canonical position: Symbol, Exchange, Float_M, MarketCap_M, Float_Source…
    if mcap_col not in df.columns and float_col in out.columns:
        cols = [c for c in out.columns if c != mcap_col]
        cols.insert(cols.index(float_col) + 1, mcap_col)
        out = out[cols]
    return out, stats


# ---------------------------------------------------------------------------
# acceptance test
# ---------------------------------------------------------------------------
def compare(got, ref, label):
    """Per-bucket table against a file that already carries MarketCap_M."""
    g = got.set_index(got["Symbol"].map(_norm_symbol))
    r = ref.set_index(ref["Symbol"].map(_norm_symbol))
    shared = [s for s in g.index if s in r.index]

    buckets = {"exact": 0, "within_1pct": 0, "within_5pct": 0,
               "within_25pct": 0, "OFF": 0, "no_reference": 0,
               "no_reconstruction": 0}
    worst = []
    for sym in shared:
        a, b = _to_float(g.at[sym, MCAP_COL]), _to_float(r.at[sym, MCAP_COL])
        if b is None or b <= 0:
            buckets["no_reference"] += 1
            continue
        if a is None:
            buckets["no_reconstruction"] += 1
            continue
        ratio = a / b
        dev = abs(ratio - 1)
        if dev < 1e-6:
            buckets["exact"] += 1
        elif dev < 0.01:
            buckets["within_1pct"] += 1
        elif dev < 0.05:
            buckets["within_5pct"] += 1
        elif dev < 0.25:
            buckets["within_25pct"] += 1
        else:
            buckets["OFF"] += 1
        worst.append((dev, sym, b, a, ratio))

    total = max(len(worst), 1)
    print(f"\nAcceptance test vs {label} — {len(shared)} shared symbol(s)")
    print("-" * 62)
    cum = 0
    for key in ("exact", "within_1pct", "within_5pct", "within_25pct", "OFF"):
        cum += buckets[key]
        print(f"  {key:<20}{buckets[key]:>7}   cumulative {100*cum/total:5.1f}%")
    print(f"  {'no_reference':<20}{buckets['no_reference']:>7}   "
          f"(reference blank — nothing to check)")
    print(f"  {'no_reconstruction':<20}{buckets['no_reconstruction']:>7}   "
          f"(no share count from either source)")
    worst.sort(reverse=True)
    if worst[:8]:
        print("\n  worst 8:")
        print(f"  {'sym':<8}{'reference':>14}{'reconstructed':>16}{'ratio':>9}")
        for dev, sym, b, a, ratio in worst[:8]:
            print(f"  {sym:<8}{b:>14.4f}{a:>16.4f}{ratio:>9.3f}")
    ok = 100 * (buckets["exact"] + buckets["within_1pct"]) / total
    print("-" * 62)
    print(f"RESULT: {ok:.1f}% within 1% of the production value.")
    return buckets


# ---------------------------------------------------------------------------
# logging / CLI
# ---------------------------------------------------------------------------
def _setup_logging(verbose: bool):
    run_dir = _SCRIPT_DIR / "runs" / datetime.now().strftime("%d-%b-%Y")
    run_dir.mkdir(parents=True, exist_ok=True)
    log.setLevel(logging.DEBUG)
    log.handlers.clear()
    fh = logging.FileHandler(run_dir / "marketcap_backfill.log")
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    fh.setLevel(logging.DEBUG)
    sh = logging.StreamHandler()
    sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s",
                                      "%H:%M:%S"))
    sh.setLevel(logging.DEBUG if verbose else logging.INFO)
    log.addHandler(fh)
    log.addHandler(sh)


def build_parser():
    p = argparse.ArgumentParser(
        description="Reconstruct MarketCap_M for files written before the "
                    "column existed. Inputs are never modified.")
    p.add_argument("-i", "--input", required=True, nargs="+",
                   help="input TSV(s); globs accepted (quote them)")
    p.add_argument("--output-dir", default=None,
                   help="where to write <stem>_mcapBack.tsv (default: beside input)")
    p.add_argument("--overwrite", action="store_true",
                   help="replace an existing output file")
    p.add_argument("--all-rows", action="store_true",
                   help="recompute even where MarketCap_M already has a value")
    p.add_argument("--shares-source", choices=["both", "implied", "yfinance"],
                   default="implied",
                   help="share-count source. Default 'implied' uses ONLY the "
                        "production files and touches no network — the "
                        "validated path. 'both' adds yfinance, which is "
                        "rate-limited and shares an endpoint with the nightly "
                        "Step 1; see fetch_yf_shares.")
    p.add_argument("--no-provenance", action="store_true",
                   help="do not append the MarketCap_Source/AsOf/LagDays columns")
    p.add_argument("--date-column", default="ArrivalDate",
                   help="per-row date column (PR-stats mode). When the column "
                        "is absent the file's own filename date is used for "
                        "every row, which is the universe-file behaviour. "
                        "Pass '' to force filename dates.")
    p.add_argument("--days-prior", type=int, default=1,
                   help="session anchor for the formula pass, following "
                        "market_state_calculator.Sessions.resolve: 0 = last "
                        "session on or before the row's date, N = the Nth "
                        "session strictly before it (default 1). Does NOT "
                        "affect the universe join, which is calendar D-1 "
                        "because it replays which file the orchestrator held.")
    p.add_argument("--no-universe-join", action="store_true",
                   help="skip pass 1 and reconstruct every blank row instead")
    p.add_argument("--no-split-correction", action="store_true",
                   help="do not divide the share count by the split factor. "
                        "Only for reproducing pre-2026-09 output — it "
                        "understates any symbol that split between the share "
                        "observation and the row.")
    p.add_argument("--split-network", action="store_true",
                   help="let the split lookup fall through to yfinance on a "
                        "cache miss. OFF by default: a full universe file "
                        "misses ~3800 of 7143 symbols and that burst shares an "
                        "endpoint with the nightly Step 1 fundamentals.")
    p.add_argument("--workers", type=int, default=2,
                   help="yfinance concurrency (default 2 — deliberately low)")
    p.add_argument("--compare-to", default=None,
                   help="'same' or a path: acceptance-test instead of writing")
    p.add_argument("--check-only", action="store_true",
                   help="report what would be fetched and exit")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    _setup_logging(args.verbose)

    paths, seen = [], set()
    for pattern in args.input:
        for path in sorted(glob.glob(pattern)) or ([pattern]
                                                   if os.path.exists(pattern)
                                                   else []):
            if path not in seen:
                seen.add(path)
                paths.append(path)
    if not paths:
        log.error("no file matches %s", " ".join(args.input))
        return 2

    # A PR-stats file carries a date per row, so a filename date is neither
    # available nor wanted; a universe file has one date for the whole file and
    # cannot be processed without it.
    frames = {path: read_tsv(path) for path in paths}
    per_row = {path: bool(args.date_column and args.date_column in df.columns)
               for path, df in frames.items()}

    dated = []
    for path in paths:
        day = parse_file_date(path)
        if day is None and not per_row[path]:
            log.error("cannot parse a date from %s and it has no %s column "
                      "— skipping", path, args.date_column or "date")
            continue
        dated.append((path, day))
    if not dated:
        log.error("no input file has a parseable YYYY-MM-DD in its name or a "
                  "%s column", args.date_column or "date")
        return 2

    log.info("%d input file(s):", len(dated))
    for path, day in dated:
        log.info("    %-58s %s", os.path.basename(path),
                 f"dates from {args.date_column}" if per_row[path]
                 else f"date={day}")

    # Row dates, needed to size the session calendar and to know which
    # production files a file could be reconstructing from.
    all_dates = set()
    for path, day in dated:
        all_dates |= {d for d in row_dates(frames[path], day, args.date_column)
                      if d is not None}
    if not all_dates:
        log.error("no usable row date in any input")
        return 2

    log.info("Building share-count observations …")
    # Only a whole-file date can make a file reconstruct itself; per-row inputs
    # are a different corpus entirely and must not lose source A to this guard.
    implied, implied_date = build_implied_shares(
        _DATA_DIR,
        exclude_dates={d for p, d in dated if d is not None and not per_row[p]})

    sessions = Sessions(min(all_dates) - timedelta(days=90),
                        max(all_dates) + timedelta(days=5))
    universe = None if args.no_universe_join else UniverseIndex(_DATA_DIR)
    splits = SplitFactors(_MSC_CACHE_DIR,
                          enabled=not args.no_split_correction,
                          allow_network=args.split_network)

    symbols = set()
    for path, _ in dated:
        symbols |= set(frames[path]["Symbol"].map(_norm_symbol))
    symbols = sorted(symbols)

    if args.check_only:
        covered = sum(1 for s in symbols if s in implied.index)
        log.info("%d distinct symbol(s); source A covers %d (%.1f%%), "
                 "yfinance would be queried for all of them",
                 len(symbols), covered, 100 * covered / max(len(symbols), 1))
        return 0

    yf_points = {}
    if args.shares_source != "implied":
        oldest = min(all_dates)
        yf_points = fetch_yf_shares(
            symbols, start=(oldest.replace(year=oldest.year - 1)).isoformat(),
            end=max(all_dates).isoformat(), workers=args.workers)

    rc = 0
    for path, day in dated:
        df = frames[path]
        # The join replays a PR against the universe file the orchestrator held.
        # A universe file IS that snapshot, so joining one to its predecessor
        # would be a substitution, not a replay -- pass 1 is per-row mode only.
        #
        # Split correction is likewise per-row only. It was measured on the
        # PR-stats corpus, where LastDailyClosePrice is explicitly basis-managed
        # and the split cache covers every symbol; a universe file is neither,
        # so switching it on there would blank thousands of cells on cache
        # misses and silently change the validated 96.9% reconstruction.
        out, stats = enrich(df, day, implied, implied_date, yf_points, args,
                            universe=universe if per_row[path] else None,
                            sessions=sessions,
                            splits=splits if per_row[path] else None)
        log.info("%s: joined %d, formula %d, kept %d | unfilled: no close %d, "
                 "no share count %d, no split factor %d, no date %d",
                 os.path.basename(path), stats["joined"], stats["filled"],
                 stats["kept"], stats["no_close"], stats["no_shares"],
                 stats["no_split"], stats["no_date"])
        if splits.misses:
            log.warning("  %d symbol(s) had no cached split list and were left "
                        "blank rather than assumed unsplit (e.g. %s); "
                        "--split-network would fetch them",
                        len(splits.misses), ", ".join(sorted(splits.misses)[:6]))

        if args.compare_to:
            ref_path = path if args.compare_to == "same" else args.compare_to
            compare(out, read_tsv(ref_path), os.path.basename(ref_path))
            continue

        stem = os.path.splitext(os.path.basename(path))[0]
        out_dir = Path(args.output_dir) if args.output_dir else Path(path).parent
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / f"{stem}_mcapBack.tsv"
        if dest.exists() and not args.overwrite:
            log.warning("  %s exists — skipping (use --overwrite)", dest.name)
            continue
        out.to_csv(dest, sep="\t", index=False)
        log.info("  wrote %s (%d rows, %d cols)", dest.name, len(out),
                 out.shape[1])
    return rc


if __name__ == "__main__":
    sys.exit(main())
