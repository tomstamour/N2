#!/usr/bin/env python3
"""
Stock-Split-finder.py — standalone split add-on for universe TSVs and PR-stats.

Reads a TSV (any column count — existing columns are byte-preserved via the
read-back-as-strings pattern) and writes a sibling ..._splits.tsv with 4
appended columns describing the most recent stock split per row (source:
yfinance via market_state_calculator.fetch_splits, no IBKR connection):

    LastSplit_Date      YYYY-MM-DD ex-date of the most recent split that was
                        ALREADY PUBLIC at the row's anchor session
    LastSplit_Factor    new/old share ratio: 0.05 = 1-for-20 reverse,
                        20 = 20-for-1 forward (yfinance native convention)
    LastSplit_MktHours  marketable hours elapsed since that split, counting
                        only 04:00–20:00 ET (16 h) on NYSE trading days,
                        from 04:00 ET on the ex-date to the row's instant
    LastSplit_Status    resolved | never_split | no_split_yet | no_data |
                        no_anchor — WHY the other three hold what they hold

MISSING DATA IS NOT ONE THING (see --never-split-fill). A blank used to cover
four situations that are epistemically different:

  never_split   yfinance affirmatively reports no split, ever. This is an
                OBSERVED NEGATIVE, not a gap. Under the default fill=maxima it
                becomes a censored observation so the row stays usable:
                    MktHours = max(resolved) * --sentinel-bump
                    Date     = min(resolved)      (earliest = longest ago)
                    Factor   = 1.0                (no-op ratio: literally true)
                Expressing one idea — "longest possible time since a split" —
                in all three columns at once. Taking each column's own max
                would NOT do this: max-hours is the OLDEST split while max-date
                is the most RECENT one, so the row would assert a split both 41
                years and 2 days ago. This mirrors the zero-trade censoring in
                trade_frequency_and_size.addOn.py, which writes a dead session
                as the full session length rather than letting it decay to NaN.

  no_split_yet  the symbol HAS a split, it just had not gone ex by the row's
                anchor. Right-censored by --days-prior, and informative: these
                are overwhelmingly steep reverse splits about to happen, which
                is the opposite of a company that never needed one. NOT pooled
                with never_split. -> --na-rep

  no_data       lookup failed; genuinely unknown.                 -> --na-rep
  no_anchor     the row's date could not be resolved to a session.-> --na-rep

The imputation is reversible: LastSplit_Status records the cause for every row,
so a consumer can always undo it. Sentinels derive from THIS file's resolved
rows, so they move with the corpus — pin them with --sentinel-bump if you need
two files to stay comparable, or use --never-split-fill blank for raw output.

TWO MODES, chosen by whether --date-column is present in the input:

  PER-ROW (PR-stats, e.g. an ArrivalDate/ArrivalTime corpus)
      Each row is anchored on its own date. Split ELIGIBILITY is gated at
      `Sessions.resolve(ArrivalDate, --days-prior)`, so a split that had not
      gone ex by then is invisible to that row — the columns are leak-free
      training features. The HOURS, by contrast, are counted to the row's real
      instant (ArrivalDate + ArrivalTime, ET): elapsed time since a past
      ex-date is fully knowable at PR time, so truncating it at the anchor
      would only throw away signal. Leakage lives in the selection, and the
      selection is where --days-prior is applied.

  WHOLE-FILE (universe TSV with no date column)
      One anchor for every row, from --as-of (default: now), still shifted by
      --days-prior. This is the original behaviour of this script.

--days-prior follows market_state_calculator.Sessions.resolve, which is
IMPORTED rather than retyped (the decrement for non-session dates is subtle
enough that a local copy would drift):
    0 = the last session on or before the row's date
    N = the Nth session strictly before it
Default is 1, matching marketcap_backfill_AddON / trade_frequency backfill:
"the last session that CLOSED before this calendar date".

Usage:
    # PR-stats corpus (per-row anchors)
    python3 Stock-Split-finder.py -i ../training_data/08_..._RawData.tsv

    # universe TSV (single anchor)
    python3 Stock-Split-finder.py -i data/stocks_universe_2026-07-15.tsv \
                                  --date-column '' --as-of '2026-07-15 20:00'
"""

import argparse
import logging
import sys
from bisect import bisect_left
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Sessions/fetch_splits are imported, not retyped: the days_prior decrement for
# non-session dates and the ""-vs-None "never split"/"no data" distinction are
# both load-bearing, and a local copy would eventually drift from the
# convention every other add-on in this repo follows.
_SCRIPT_DIR = Path(__file__).resolve().parent
_MSC_DIR = _SCRIPT_DIR.parent / "tools" / "market_state"
if str(_MSC_DIR) not in sys.path:
    sys.path.insert(0, str(_MSC_DIR))
from market_state_calculator import Sessions, fetch_splits   # noqa: E402

_MSC_CACHE_DIR = _MSC_DIR / "tables"      # fetch_splits appends /splits

ET = ZoneInfo("America/New_York")

_SESSION_START_H = 4    # 04:00 ET
_SESSION_END_H   = 20   # 20:00 ET
_SESSION_HOURS   = float(_SESSION_END_H - _SESSION_START_H)   # 16 h/trading day

_RUNS_DIR = _SCRIPT_DIR / "runs"

OUT_COLUMNS = ("LastSplit_Date", "LastSplit_Factor", "LastSplit_MktHours",
               "LastSplit_Status")

# Status vocabulary, lowercase snake_case to match msc_status / fromPR_status.
ST_RESOLVED     = "resolved"
ST_NEVER_SPLIT  = "never_split"
ST_NO_SPLIT_YET = "no_split_yet"
ST_NO_DATA      = "no_data"
ST_NO_ANCHOR    = "no_anchor"
STATUS_ORDER = (ST_RESOLVED, ST_NEVER_SPLIT, ST_NO_SPLIT_YET, ST_NO_DATA,
                ST_NO_ANCHOR)

# Factor for a company that never split: the share count never changed, so the
# new/old ratio is exactly 1. Unlike the other two sentinels this is not a
# derived maximum — it is the true value.
NEVER_SPLIT_FACTOR = 1.0

log = logging.getLogger("split_finder")


def _setup_logging() -> None:
    date_str = datetime.now().strftime("%d-%b-%Y")
    log_dir = _RUNS_DIR / date_str
    log_dir.mkdir(parents=True, exist_ok=True)

    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(log_dir / "stock_split_finder.log")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        log.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(ch)


# ── Row date/time parsing ────────────────────────────────────────────────────

def parse_date(value: str):
    """YYYY-MM-DD -> date, or None. Tolerates a trailing time component."""
    s = (value or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_clock(value: str):
    """
    'HH:MM:SS' or 'HH:MM:SS.mmm' -> time, or None.

    The corpus carries BOTH precisions (1383 bare-second rows vs 3421
    millisecond rows in the 2026-05..08 file), and neither form states a
    timezone — they are ET wall clock, like every other timestamp in this
    pipeline.
    """
    s = (value or "").strip()
    if not s:
        return None
    for fmt in ("%H:%M:%S.%f", "%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


# ── Marketable-hours clock ───────────────────────────────────────────────────

class MarketHoursClock:
    """
    Counts marketable hours (04:00–20:00 ET on NYSE trading days) between a
    split ex-date (anchored at 04:00 ET) and a per-row as-of instant.

    The NYSE session list is built once for the widest span any row needs and
    shared via bisect. Because every session contributes exactly 16 h, the
    interior of the span is a multiplication rather than a loop — a split from
    2004 is ~5,500 sessions back, and looping that per row over a 4,800-row
    corpus would be ~10M iterations for an answer bisect gives in O(log n).
    """

    def __init__(self, earliest: date, latest: date, calendar_name: str = "XNYS"):
        sessions = Sessions(earliest, latest)
        self.days = sessions.days

    def hours_since(self, split_date: date, as_of: datetime):
        """Marketable hours from 04:00 ET on split_date to as_of, or None if
        as_of precedes the ex-date."""
        as_of = as_of.astimezone(ET)
        d_asof = as_of.date()
        if split_date > d_asof:
            return None

        lo = bisect_left(self.days, split_date)   # first session >= ex-date
        hi = bisect_left(self.days, d_asof)       # first session >= as-of date
        total = _SESSION_HOURS * max(0, hi - lo)  # whole sessions in [ex, as_of)

        # Partial credit for the as-of day itself, when it is a session.
        if hi < len(self.days) and self.days[hi] == d_asof and d_asof >= split_date:
            start = datetime(d_asof.year, d_asof.month, d_asof.day,
                             _SESSION_START_H, tzinfo=ET)
            end = datetime(d_asof.year, d_asof.month, d_asof.day,
                           _SESSION_END_H, tzinfo=ET)
            if as_of < end:
                end = as_of
            if end > start:
                total += (end - start).total_seconds() / 3600.0
        return round(total, 1)


# ── Split fetch ──────────────────────────────────────────────────────────────

def fetch_all(symbols, cache_dir: Path, use_cache: bool, max_age_days: int,
              force_refresh: bool, workers: int):
    """symbol -> [(ex_date, factor), ...] | None (lookup failed).

    [] means "never split" and None means "no data" — fetch_splits keeps the
    two apart and so must we, or a failed lookup becomes a confident zero.
    """
    results, failures = {}, []
    log.info("fetching split history for %d symbols (%d workers, cache=%s)",
             len(symbols), workers, "on" if use_cache else "off")

    def one(sym):
        return fetch_splits(sym, cache_dir, use_cache=use_cache, attempts=2,
                            max_age_days=max_age_days, force_refresh=force_refresh)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(one, s): s for s in symbols}
        for n, fut in enumerate(as_completed(futs), 1):
            sym = futs[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = None
                log.warning("fetch raised for %s: %s", sym, exc)
            results[sym] = res
            if res is None:
                failures.append(sym)
            if n % 200 == 0:
                log.info("  %d/%d fetched", n, len(symbols))

    with_split = sum(1 for v in results.values() if v)
    log.info("fetch done: %d symbols, %d with a split on record, %d never split, "
             "%d lookup failures", len(results), with_split,
             sum(1 for v in results.values() if v == []), len(failures))
    if failures:
        log.warning("lookup failed for %d symbols: %s", len(failures),
                    ", ".join(sorted(failures)[:60]) +
                    (" …" if len(failures) > 60 else ""))
    return results


# ── MSC cross-check ──────────────────────────────────────────────────────────

def compare_to_msc(df, dates, factors) -> None:
    """Diff the new columns against msc_split_last_date / msc_split_factor.

    MSC computes the same point-in-time selection (market_state_calculator.py
    split_features(), `at_or_before[-1]`), so on rows where both sides resolved
    the same anchor they should agree exactly. Disagreement means one of the
    two fetched a different split list — worth knowing before either column is
    trusted as a feature.
    """
    if "msc_split_last_date" not in df.columns:
        log.info("no msc_split_* columns in input — skipping cross-check")
        return

    msc_date = df["msc_split_last_date"].fillna("").astype(str).str.strip()
    msc_fac = df["msc_split_factor"].fillna("").astype(str).str.strip()
    msc_anchor = (df["msc_anchor_date"].fillna("").astype(str).str.strip()
                  if "msc_anchor_date" in df.columns else None)

    tally = Counter()
    examples = []
    for i in range(len(df)):
        ours, theirs = dates[i], msc_date.iat[i]
        if not ours and not theirs:
            tally["both_blank"] += 1
        elif ours and not theirs:
            tally["ours_only"] += 1
        elif theirs and not ours:
            tally["msc_only"] += 1
            if len(examples) < 15:
                examples.append((df["Symbol"].iat[i], "-", theirs))
        elif ours == theirs:
            tally["date_agree"] += 1
            try:
                if abs(float(factors[i]) - float(msc_fac.iat[i])) > 1e-6 * max(
                        1.0, abs(float(msc_fac.iat[i]))):
                    tally["factor_differs"] += 1
            except ValueError:
                tally["factor_unparsed"] += 1
        else:
            tally["date_differs"] += 1
            if len(examples) < 15:
                examples.append((df["Symbol"].iat[i], ours, theirs))

    log.info("── cross-check vs msc_split_* ──")
    for k in ("date_agree", "factor_differs", "factor_unparsed", "date_differs",
              "ours_only", "msc_only", "both_blank"):
        if tally[k]:
            log.info("  %-16s %6d", k, tally[k])
    for sym, ours, theirs in examples:
        log.info("    %-8s ours=%-12s msc=%-12s", sym, ours or "-", theirs or "-")

    if msc_anchor is not None:
        log.info("  (msc_anchor_date is informational; a differing anchor "
                 "explains a differing split without either side being wrong)")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Append point-in-time last-split columns to a TSV.")
    ap.add_argument("-i", "--input", required=True, help="path to the TSV")
    ap.add_argument("-o", "--output", default=None,
                    help="output path (default: <stem>_splits.tsv beside input)")
    ap.add_argument("--date-column", default="ArrivalDate",
                    help="per-row anchor date column; '' forces whole-file mode "
                         "(default ArrivalDate)")
    ap.add_argument("--time-column", default="ArrivalTime",
                    help="per-row clock column, ET wall time (default ArrivalTime). "
                         "Missing/unparsable falls back to --eod-fallback.")
    ap.add_argument("--days-prior", type=int, default=1, metavar="N",
                    help="split-eligibility anchor, per Sessions.resolve: 0 = last "
                         "session on or before the row's date, N = the Nth session "
                         "strictly before it (default 1)")
    ap.add_argument("--eod-fallback", action="store_true",
                    help="when the time column is absent/unparsable, count hours to "
                         "20:00 ET instead of 04:00 ET on the row's date")
    ap.add_argument("--as-of", default=None, metavar="'YYYY-MM-DD HH:MM'",
                    help="whole-file mode: override 'now' (ET)")
    ap.add_argument("--workers", type=int, default=10,
                    help="yfinance fetch threads (default 10)")
    ap.add_argument("--limit", type=int, default=None,
                    help="only fetch the first N unique symbols (testing)")
    ap.add_argument("--split-cache-dir", default=str(_MSC_CACHE_DIR),
                    help="MSC split cache root; /splits is appended")
    ap.add_argument("--split-max-age-days", type=int, default=1,
                    help="cache entries older than this are refetched from "
                         "yfinance (default 1, matching MSC)")
    ap.add_argument("--no-split-cache", action="store_true",
                    help="ignore and do not write the shared cache")
    ap.add_argument("--refresh-splits", action="store_true",
                    help="treat every cache entry as a miss")
    ap.add_argument("--no-compare-msc", action="store_true",
                    help="skip the msc_split_* cross-check")
    ap.add_argument("--never-split-fill", choices=("maxima", "nan", "blank"),
                    default="maxima",
                    help="how to fill rows whose symbol affirmatively never "
                         "split: 'maxima' = censored sentinel (earliest date, "
                         "factor 1.0, max hours x --sentinel-bump), 'nan' = "
                         "--na-rep like any other miss, 'blank' = empty cell, "
                         "the pre-2026-09 behaviour (default maxima)")
    ap.add_argument("--sentinel-bump", type=float, default=1.05, metavar="F",
                    help="multiply the max resolved MktHours by F for the "
                         "never_split sentinel, so it cannot collide with a "
                         "genuine maximum (default 1.05)")
    ap.add_argument("--na-rep", default="NaN", metavar="S",
                    help="text written for no_split_yet / no_data / no_anchor. "
                         "Default 'NaN'; pass '' for the repo-wide empty-cell "
                         "convention (both read back as NaN under pandas "
                         "defaults)")
    ap.add_argument("--no-status-column", action="store_true",
                    help="do not append LastSplit_Status (makes the "
                         "never_split imputation irreversible)")
    args = ap.parse_args()

    _setup_logging()

    in_path = Path(args.input)
    if not in_path.exists():
        log.error("input file not found: %s", in_path)
        return 1
    out_path = (Path(args.output) if args.output
                else in_path.with_name(in_path.stem + "_splits.tsv"))

    # Read back as strings so the existing columns round-trip byte-identical.
    df = pd.read_csv(in_path, sep="\t", dtype=str, keep_default_na=False,
                     low_memory=False)
    log.info("loaded %s: %d rows, %d columns", in_path.name, len(df), df.shape[1])
    for col in OUT_COLUMNS:
        if col in df.columns:
            log.error("input already has column %s — refusing to double-append", col)
            return 1
    if "Symbol" not in df.columns:
        log.error("input has no Symbol column")
        return 1

    # ── anchors ─────────────────────────────────────────────────────────────
    per_row = bool(args.date_column) and args.date_column in df.columns
    if args.date_column and not per_row:
        log.warning("date column %r not found — falling back to whole-file mode",
                    args.date_column)

    if per_row:
        row_dates = [parse_date(v) for v in df[args.date_column]]
        n_bad = sum(1 for d in row_dates if d is None)
        if n_bad:
            log.warning("%d rows have an unparsable %s", n_bad, args.date_column)
        good = [d for d in row_dates if d]
        if not good:
            log.error("no parsable dates in %s", args.date_column)
            return 1
        have_time = bool(args.time_column) and args.time_column in df.columns
        if not have_time and args.time_column:
            log.warning("time column %r not found — using %02d:00 ET",
                        args.time_column,
                        _SESSION_END_H if args.eod_fallback else _SESSION_START_H)
        row_times = ([parse_clock(v) for v in df[args.time_column]]
                     if have_time else [None] * len(df))
        log.info("per-row mode on %s/%s: %s .. %s, --days-prior %d",
                 args.date_column, args.time_column if have_time else "-",
                 min(good), max(good), args.days_prior)
    else:
        as_of = (datetime.strptime(args.as_of, "%Y-%m-%d %H:%M").replace(tzinfo=ET)
                 if args.as_of else datetime.now(tz=ET))
        row_dates = [as_of.date()] * len(df)
        row_times = [as_of.timetz()] * len(df)
        good = [as_of.date()]
        log.info("whole-file mode: as-of %s, --days-prior %d",
                 as_of.strftime("%Y-%m-%d %H:%M %Z"), args.days_prior)

    sessions = Sessions(min(good) - timedelta(days=90),
                        max(max(good), date.today()) + timedelta(days=30))
    anchors = [sessions.resolve(d, args.days_prior) if d else None
               for d in row_dates]
    n_no_anchor = sum(1 for a in anchors if a is None)
    if n_no_anchor:
        log.warning("%d rows could not resolve an anchor session", n_no_anchor)

    # ── fetch ───────────────────────────────────────────────────────────────
    symbols = sorted({s.strip().upper() for s in df["Symbol"] if s and s.strip()})
    if args.limit:
        symbols = symbols[: args.limit]
    splits_by_sym = fetch_all(symbols, Path(args.split_cache_dir),
                              use_cache=not args.no_split_cache,
                              max_age_days=args.split_max_age_days,
                              force_refresh=args.refresh_splits,
                              workers=args.workers)

    # ── select the last split at or before each row's anchor ────────────────
    # The status is recorded PER ROW, not just counted: it is what makes the
    # never_split imputation below reversible, and it is the only thing that
    # keeps "observed negative" apart from "lookup failed" in the output.
    selected = [None] * len(df)       # (ex_date, factor)
    statuses = [ST_NO_ANCHOR] * len(df)
    for i in range(len(df)):
        sym = (df["Symbol"].iat[i] or "").strip().upper()
        anchor = anchors[i]
        if anchor is None:
            continue                                    # ST_NO_ANCHOR
        hist = splits_by_sym.get(sym)
        if hist is None:
            statuses[i] = ST_NO_DATA
            continue
        eligible = [(d, f) for d, f in hist if d <= anchor]
        if not eligible:
            statuses[i] = ST_NO_SPLIT_YET if hist else ST_NEVER_SPLIT
            continue
        selected[i] = eligible[-1]
        statuses[i] = ST_RESOLVED

    used = [s[0] for s in selected if s]
    if not used:
        log.warning("no row resolved a split — nothing to derive sentinels from")
    clock = MarketHoursClock(min(used) - timedelta(days=7) if used else min(good),
                             max(max(good), date.today()) + timedelta(days=30))

    # ── phase A: real values for resolved rows ──────────────────────────────
    dates = [None] * len(df)
    factors = [None] * len(df)
    hours = [None] * len(df)
    fallback_h = _SESSION_END_H if args.eod_fallback else _SESSION_START_H
    for i in range(len(df)):
        hit = selected[i]
        if hit is None:
            continue
        d, factor = hit
        t = row_times[i] or dtime(fallback_h, 0)
        rd = row_dates[i]
        instant = datetime(rd.year, rd.month, rd.day,
                           t.hour, t.minute, t.second, t.microsecond, tzinfo=ET)
        h = clock.hours_since(d, instant)
        if h is None:                 # as-of precedes the ex-date; unreachable
            statuses[i] = ST_NO_DATA  # under days_prior >= 0, but stay honest
            continue
        dates[i], factors[i], hours[i] = d, float(factor), h

    # ── phase B: fill the non-resolved rows ─────────────────────────────────
    # Sentinels must come from the resolved rows only — deriving them from the
    # full column would be circular once the fill is applied.
    res_h = [h for h in hours if h is not None]
    res_d = [d for d in dates if d is not None]
    sentinel = None
    if args.never_split_fill == "maxima" and res_h and res_d:
        sentinel = (min(res_d), NEVER_SPLIT_FACTOR, max(res_h) * args.sentinel_bump)
        log.info("never_split sentinel: Date=%s Factor=%g MktHours=%.1f "
                 "(max resolved %.1f x %.2f)", sentinel[0].isoformat(), sentinel[1],
                 sentinel[2], max(res_h), args.sentinel_bump)
        if sentinel[2] <= max(res_h):
            log.warning("--sentinel-bump %.2f does not clear the max resolved "
                        "value — sentinel rows will collide with real ones",
                        args.sentinel_bump)
    elif args.never_split_fill == "maxima":
        log.warning("no resolved rows — never_split falls back to %r", args.na_rep)

    out_d, out_f, out_h = [], [], []
    for i in range(len(df)):
        if statuses[i] == ST_RESOLVED:
            out_d.append(dates[i].isoformat())
            out_f.append(f"{factors[i]:.6g}")
            out_h.append(f"{hours[i]:.1f}")
        elif statuses[i] == ST_NEVER_SPLIT and sentinel is not None:
            out_d.append(sentinel[0].isoformat())
            out_f.append(f"{sentinel[1]:.6g}")
            out_h.append(f"{sentinel[2]:.1f}")
        elif args.never_split_fill == "blank":
            out_d.append(""); out_f.append(""); out_h.append("")
        else:
            out_d.append(args.na_rep); out_f.append(args.na_rep); out_h.append(args.na_rep)

    df["LastSplit_Date"] = out_d
    df["LastSplit_Factor"] = out_f
    df["LastSplit_MktHours"] = out_h
    if not args.no_status_column:
        df["LastSplit_Status"] = statuses

    stats = Counter(statuses)
    log.info("── row outcomes ──")
    for k in STATUS_ORDER:
        if stats[k]:
            log.info("  %-14s %6d  (%4.1f%%)%s", k, stats[k],
                     stats[k] / len(df) * 100,
                     "  -> sentinel" if k == ST_NEVER_SPLIT and sentinel
                     else ("  -> " + repr(args.na_rep) if k != ST_RESOLVED
                           and args.never_split_fill != "blank" else ""))

    if not args.no_compare_msc:
        # PRE-imputation values on purpose: comparing the emitted column would
        # score all 1500-odd sentinel-filled never_split rows as disagreements
        # with MSC and drown the handful of real ones.
        compare_to_msc(df,
                       ["" if d is None else d.isoformat() for d in dates],
                       ["" if f is None else f"{f:.6g}" for f in factors])

    df.to_csv(out_path, sep="\t", index=False)
    log.info("wrote %s (%d rows, %d columns)", out_path.name, len(df), df.shape[1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
