"""Referee the LastDailyClosePrice column against every independent source there is.

lastDailyClose_AddOn.py fills LastDailyClosePrice from three places: the orchestrator5
and orchestrator3 daily tables (point-in-time, recorded live on the PR day) and, for
rows those tables don't cover, a live IBKR historical fetch corrected by a yfinance
split factor. Those two paths land on the same share basis -- the AS-TRADED basis --
but only the orchestrator path is inherently trustworthy. The live path multiplies an
IBKR bar (back-adjusted to the share basis in force at REQUEST time) by

    F = forward_split_factor(splits, anchor)

and yfinance is the sole authority on F. market_state_calculator.py refuses to trust
it alone -- "on microcaps yfinance is wrong in BOTH directions" -- and refereess every
F against a point-in-time close via basis_check(). lastDailyClose_AddOn.py mirrored
fetch_splits() and forward_split_factor() but NOT that referee, so a wrong F is silent.

This script is that missing referee, run after the fact. It is strictly READ-ONLY: it
never rewrites the data file. For every row it recomputes F from the shared split
cache, reconstructs the raw IBKR bar as price/F, and compares the stored price against
whichever independent reference is available, in descending order of authority:

  universe   stocks_universe_<date>.tsv, written once on that date and never
             rewritten -- the only reference that settles a basis question outright.
  orch       orchestrator5/orchestrator3 news_output_<date>.tsv matched on
             (Symbol, ArrivalDate) rather than PR ID, which reaches rows the loader's
             ID join missed. Also point-in-time.
  yfinance   (--yfinance) daily close, split-adjusted to today. Refereess the PRICE
             LEVEL only: it is multiplied by the same F under test, so it cannot
             referee F itself. Reported separately for that reason.

Run with the ibapi venv:
    /home/tom/venv/bin/python audit_lastdailyclose.py \\
        -i outputs/PRstats_..._DailyClose.tsv --row-out /tmp/audit_rows.tsv
"""

import argparse
import math
import os
import sys
from bisect import bisect_right
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

from highOfDay_addON import get_target_date

SCRIPT_DIR = Path(__file__).parent
ORCH5_TABLES_DIR = SCRIPT_DIR.parent / 'orchestrator5' / 'tables'
ORCH3_TABLES_DIR = SCRIPT_DIR.parent / 'orchestrator3' / 'tables'
MSC_CACHE_DIR = SCRIPT_DIR.parent / 'tools' / 'market_state' / 'tables'
UNIVERSE_DIR = SCRIPT_DIR.parent / 'universe_finder' / 'data'
YF_BARS_CACHE = SCRIPT_DIR / 'cache' / 'yf_bars'

_XNYS = mcal.get_calendar('XNYS')

# =====================================================================
# Basis-check machinery (mirrored from market_state_calculator.py)
# Kept byte-identical with MSC @ lines 1164-1332; any update there must
# be replicated here. See lastDailyClose_AddOn.py for the same block.
# =====================================================================

BASIS_MIN_FACTOR = 2.5
BASIS_RATIO_TOL = 0.02          # how close to a clean rational the factor must sit
BASIS_AGREE_LO = 0.75           # ratios inside this band are "the same price"
BASIS_AGREE_HI = 1.34

# Two feeds quoting the SAME session's official close agree to the cent; anything
# looser is a different session or a different feed. Used only for the session
# question, never for the basis question -- that is what the band above is for.
EXACT_TOL = 0.005               # |log ratio|, ~0.5%


def plausible_split_ratio(ratio: float, tol: float = BASIS_RATIO_TOL) -> float | None:
    """The clean split-like factor `ratio` sits on, or None."""
    if not ratio or not math.isfinite(ratio) or ratio <= 0:
        return None
    big = ratio if ratio >= 1.0 else 1.0 / ratio
    if big < BASIS_MIN_FACTOR:
        return None
    cand = float(Fraction(big).limit_denominator(4))
    if cand <= 0 or abs(math.log(big / cand)) > math.log(1.0 + tol):
        return None
    return cand if ratio >= 1.0 else 1.0 / cand


def forward_split_factor(splits, anchor_date, bars_fetched_at=None) -> float | None:
    """Cumulative split factor for splits AFTER the anchor, or None if unknown."""
    if splits is None:
        return None
    factor = 1.0
    for d, f in splits:
        if d > anchor_date and f > 0:
            if bars_fetched_at is not None and d >= bars_fetched_at:
                continue
            factor *= f
    return factor


def split_prefix_products(splits, anchor_date, bars_fetched_at=None) -> list[float]:
    """Every share basis the symbol passed through after the anchor."""
    out, factor = [], 1.0
    for d, f in sorted(splits or []):
        if d > anchor_date and f > 0:
            if bars_fetched_at is not None and d >= bars_fetched_at:
                break
            factor *= f
            out.append(factor)
    return out


def basis_check(bar_close, fwd, price_ref, price_ref_src, splits, anchor_date,
                bars_fetched_at):
    """(flag, ratio, effective_factor) for one anchor's share basis.

    See market_state_calculator.basis_check for the full rationale. Flags:
    ok / bars_behind / ref_stale / basis_mismatch / bars_stale / ref_disagree / no_ref.
    """
    if not bar_close or bar_close <= 0 or fwd is None or fwd <= 0:
        return "no_ref", None, fwd

    prefixes = split_prefix_products(splits, anchor_date, bars_fetched_at)

    if not price_ref or price_ref <= 0 or not math.isfinite(price_ref):
        newest = max((d for d, f in (splits or []) if d > anchor_date), default=None)
        if newest is not None and bars_fetched_at is not None \
                and bars_fetched_at <= newest:
            return "bars_stale", None, fwd
        return "no_ref", None, fwd

    ratio = (bar_close * fwd) / price_ref
    agree = lambda r: BASIS_AGREE_LO <= r <= BASIS_AGREE_HI

    if price_ref_src == "universe":
        best, best_err = None, None
        for cand in [1.0] + prefixes:
            err = abs(math.log((bar_close * cand) / price_ref))
            if best_err is None or err < best_err:
                best, best_err = cand, err
        if best is not None and agree((bar_close * best) / price_ref):
            if abs(math.log(best / fwd)) < 1e-9:
                return "ok", ratio, fwd
            return "bars_behind", ratio, best
    elif agree(ratio):
        return "ok", ratio, fwd
    else:
        for prefix in prefixes:
            if agree(ratio / prefix):
                return "ref_stale", ratio, fwd

    if agree(ratio):
        return "ok", ratio, fwd
    if plausible_split_ratio(ratio) is not None:
        return "basis_mismatch", ratio, fwd
    return "ref_disagree", ratio, fwd


# =====================================================================
# Calendar / split-cache loading
# =====================================================================

def prev_trading_day(d: date) -> date:
    """The last US equity session strictly before d.

    This is the session LastDailyClosePrice actually describes: the loader fetches a
    3-day window ending at the PR's session and takes the bar BEFORE it.
    """
    sched = _XNYS.valid_days(start_date=d - timedelta(days=12), end_date=d - timedelta(days=1))
    if len(sched) == 0:
        raise ValueError(f"no trading session in the 12 days before {d}")
    return sched[-1].date()


def load_splits(symbol: str, cache_dir: Path, cache: dict):
    """Splits for a symbol from MSC's shared cache. Offline only -- never hits Yahoo.

    Returns [] for a symbol on record as never having split and None when there is no
    cache file, so "no split" and "no data" stay distinguishable exactly as in
    fetch_splits().
    """
    key = symbol.upper()
    if key in cache:
        return cache[key]
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    path = cache_dir / "splits" / f"{safe}.tsv"
    out = None
    if path.exists():
        try:
            df = pd.read_csv(path, sep="\t")
            out = [] if df.empty else [
                (pd.to_datetime(r.ex_date).date(), float(r.factor)) for r in df.itertuples()
            ]
            out.sort()
        except Exception:
            out = None
    cache[key] = out
    return out


# =====================================================================
# Reference loaders (point-in-time sources)
# =====================================================================

def read_universe_file(path: Path) -> dict:
    """{SYMBOL: close} from one stocks_universe_*.tsv, or {} if unreadable."""
    try:
        t = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False,
                        usecols=lambda c: c in ("Symbol", "LastDailyClosePrice"))
    except Exception:
        return {}
    if "Symbol" not in t.columns or "LastDailyClosePrice" not in t.columns:
        return {}
    out = {}
    for sym, px in zip(t["Symbol"], t["LastDailyClosePrice"]):
        try:
            v = float(px)
        except (TypeError, ValueError):
            continue
        if v > 0:
            out.setdefault(str(sym).strip().upper(), v)
    return out


def universe_files(ref_dir: Path) -> list:
    """[(file_date, path)] for the plain snapshots, ignoring _SIback/_mcapBack variants."""
    out = []
    if not ref_dir or not ref_dir.is_dir():
        return out
    for path in sorted(ref_dir.glob("stocks_universe_*.tsv")):
        try:
            out.append((date.fromisoformat(path.stem[len("stocks_universe_"):]), path))
        except ValueError:
            continue          # a suffixed variant, not a plain snapshot
    return out


def calibrate_universe_sessions(ref_dir: Path, cache_path: Path, yf_cache: dict,
                                probes: int = 30, force: bool = False) -> dict:
    """{file_date: session} -- which trading session each universe snapshot describes.

    The filename cannot be trusted to answer this and the answer is NOT constant
    across the corpus. stocks_universe_2026-08-17.tsv holds AIXC at 0.7102 and the
    2026-08-17 session closed at 0.7100, so there the file date IS the session. But
    stocks_universe_2026-06-10.tsv holds CHAI at 1.86, which is the 2026-06-09 close
    (2026-06-10 closed at 1.37), so there it is the session BEFORE. The mtimes explain
    it: the June files were hand-run at 03:00-07:40 while the pipeline was being built,
    and only from ~2026-07-01 does the clean 20:50-22:11 evening cron take over.

    Assuming either convention corrupts the referee on whichever half is wrong, and a
    one-session error hides inside the 0.75-1.34 agreement band on everything except
    the big movers -- which in a PR corpus are exactly the rows that matter. So each
    file is calibrated against yfinance closes for a panel of symbols it contains, and
    the verdict is cached.
    """
    if cache_path.exists() and not force:
        try:
            t = pd.read_csv(cache_path, sep='\t')
            return {date.fromisoformat(str(r.file_date)): date.fromisoformat(str(r.session))
                    for r in t.itertuples() if str(r.session) != 'nan'}
        except Exception:
            pass

    files = universe_files(ref_dir)
    if not files:
        return {}
    # A panel of symbols that appear in most snapshots, so one history fetch serves
    # many files. Frequency is counted over a sample of files to keep this cheap.
    freq = {}
    for _, path in files[::max(1, len(files) // 12)]:
        for sym in read_universe_file(path):
            freq[sym] = freq.get(sym, 0) + 1
    panel = [s for s, _ in sorted(freq.items(), key=lambda kv: -kv[1])[:probes * 3]]

    print(f"[cal] calibrating {len(files)} universe snapshots against yfinance "
          f"(panel of up to {probes} symbols)...")
    hist = {}
    for sym in panel:
        h = load_yf_history(sym, yf_cache, YF_BARS_CACHE)
        if h:
            hist[sym] = h
        if len(hist) >= probes:
            break

    mapping, undecided = {}, 0
    for file_date, path in files:
        vals = read_universe_file(path)
        if not vals:
            continue
        try:
            cands = [file_date, prev_trading_day(file_date)]
        except ValueError:
            continue
        best, best_err, best_n = None, None, 0
        for cand in cands:
            errs = [abs(math.log(vals[s] / hist[s][cand]))
                    for s in hist
                    if s in vals and cand in hist[s] and hist[s][cand] > 0 and vals[s] > 0]
            if len(errs) < 5:
                continue
            err = sorted(errs)[len(errs) // 2]          # median log-ratio
            if best_err is None or err < best_err:
                best, best_err, best_n = cand, err, len(errs)
        # A correct assignment sits near zero; a one-session shift on these names does
        # not. Refuse to guess when the winner is not clearly a match.
        if best is not None and best_err < 0.02:
            mapping[file_date] = best
        else:
            undecided += 1

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({'file_date': [d.isoformat() for d in sorted(mapping)],
                  'session': [mapping[d].isoformat() for d in sorted(mapping)]}
                 ).to_csv(cache_path, sep='\t', index=False)
    shifted = sum(1 for d, s in mapping.items() if s != d)
    print(f"[cal] resolved {len(mapping)} snapshots ({shifted} where the file date is "
          f"NOT the session), {undecided} undecided -> {cache_path}")
    return mapping


def load_universe_refs(ref_dir: Path, anchors: set, session_map: dict) -> dict:
    """{(SYMBOL, session): close} from the nightly universe snapshots.

    Each snapshot is written once and never rewritten, so its price is on the share
    basis in force at the time -- the only reference that settles a basis question
    outright. Sessions come from calibrate_universe_sessions(), not the filename.
    """
    refs = {}
    files = 0
    for file_date, path in universe_files(ref_dir):
        session = session_map.get(file_date)
        if session is None or session not in anchors:
            continue
        vals = read_universe_file(path)
        if not vals:
            continue
        files += 1
        for sym, v in vals.items():
            refs.setdefault((sym, session), v)
    print(f"[ref] universe: {files} snapshots covering "
          f"{len({s for _, s in refs})}/{len(anchors)} needed sessions "
          f"-> {len(refs)} point-in-time closes")
    return refs


def load_orch_refs(dirs, arrival_dates: set) -> dict:
    """{(SYMBOL, arrival_date): LastDailyClose} from the orchestrator daily tables.

    Matched on (Symbol, ArrivalDate) instead of PR ID. The loader joins on ID, so a PR
    absent from the tables falls through to a live fetch even when a DIFFERENT PR for
    the same symbol that day carries the very close it needed.
    """
    refs = {}
    files = 0
    for d in sorted(arrival_dates):
        for base in dirs:
            stem = f'news_output_{d}'
            for name in (f'{stem}.tsv', f'{stem}_rotated.tsv', f'{stem}_2.tsv',
                         f'{stem}_MIXED_SCHEMA.tsv'):
                path = base / name
                if not path.exists():
                    continue
                try:
                    t = pd.read_csv(path, sep='\t', low_memory=False)
                except Exception:
                    continue
                if 'Symbol' not in t.columns or 'LastDailyClose' not in t.columns:
                    continue
                files += 1
                vals = pd.to_numeric(t['LastDailyClose'], errors='coerce')
                for sym, v in zip(t['Symbol'].astype(str).str.strip().str.upper(), vals):
                    if pd.notna(v) and v > 0:
                        refs.setdefault((sym, d), float(v))
    print(f"[ref] orchestrator: {files} tables with a LastDailyClose column "
          f"-> {len(refs)} point-in-time closes")
    return refs


def load_yf_history(symbol: str, cache: dict, cache_dir: Path) -> dict:
    """{session: close} for a symbol from yfinance, split-adjusted to today.

    Cached per symbol on disk as a full history, so the first run pays the network
    cost once and every re-run is offline. {} when Yahoo has nothing.
    """
    key = symbol.upper()
    if key in cache:
        return cache[key]
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in key)
    path = cache_dir / f"{safe}.tsv"
    hist = None
    if path.exists():
        try:
            t = pd.read_csv(path, sep='\t')
            hist = {pd.to_datetime(r.d).date(): float(r.close) for r in t.itertuples()}
        except Exception:
            hist = None
    if hist is None:
        try:
            import yfinance as yf
            h = yf.Ticker(key).history(period='max', auto_adjust=False, actions=False)
            hist = {}
            if h is not None and not h.empty:
                for ts, px in zip(h.index, h['Close']):
                    if pd.notna(px) and float(px) > 0:
                        hist[ts.date()] = float(px)
            cache_dir.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({'d': [d.isoformat() for d in hist],
                          'close': list(hist.values())}).to_csv(path, sep='\t', index=False)
        except Exception:
            hist = {}
    cache[key] = hist
    return hist


# =====================================================================

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', '-i', required=True, dest='input_file',
                    help='TSV carrying LastDailyClosePrice (read-only).')
    ap.add_argument('--row-out', dest='row_out', default=None,
                    help='Optional TSV of the per-row audit verdicts.')
    ap.add_argument('--universe-dir', default=str(UNIVERSE_DIR))
    ap.add_argument('--splits-cache', default=str(MSC_CACHE_DIR))
    ap.add_argument('--yfinance', action='store_true',
                    help='Also referee unrefereed rows against yfinance (slow, network).')
    ap.add_argument('--recalibrate', action='store_true',
                    help='Redo the universe file-date -> session calibration from scratch.')
    ap.add_argument('--asof', default=None,
                    help='Date the bars were fetched (YYYY-MM-DD). Default: the input '
                         "file's mtime, which is when the loader wrote it.")
    args = ap.parse_args()

    if not os.path.isfile(args.input_file):
        print(f"input not found: {args.input_file}", file=sys.stderr)
        return 1

    asof = (date.fromisoformat(args.asof) if args.asof
            else date.fromtimestamp(os.path.getmtime(args.input_file)))
    print(f"[cfg] bars-fetched-at (asof) = {asof}")

    df = pd.read_csv(args.input_file, sep='\t', low_memory=False)
    px = pd.to_numeric(df['LastDailyClosePrice'], errors='coerce')
    bucket = df['LastDailyClosePrice_SplitAdj'].fillna('').replace('', 'ORCH')
    print(f"[in ] {len(df)} rows, {px.notna().sum()} filled, {px.isna().sum()} missing")

    # Resolve the session each stored close actually describes.
    anchors, targets = [], []
    for at, ad in zip(df['ArrivalTime'].astype(str), df['ArrivalDate'].astype(str)):
        try:
            t = get_target_date(at.strip(), ad.strip())
            anchors.append(prev_trading_day(t))
            targets.append(t)
        except Exception:
            anchors.append(None)
            targets.append(None)

    splits_cache, yf_cache = {}, {}
    session_map = calibrate_universe_sessions(
        Path(args.universe_dir), SCRIPT_DIR / 'cache' / 'universe_session_map.tsv',
        yf_cache, force=args.recalibrate)
    uni = load_universe_refs(Path(args.universe_dir),
                             {a for a in anchors if a is not None}, session_map)
    orch = load_orch_refs([ORCH5_TABLES_DIR, ORCH3_TABLES_DIR],
                          set(df['ArrivalDate'].astype(str)))
    rows = []
    for i in range(len(df)):
        sym = str(df['Symbol'].iloc[i]).strip().upper()
        anchor, target = anchors[i], targets[i]
        value = px.iloc[i]
        try:
            arrival_d = date.fromisoformat(str(df['ArrivalDate'].iloc[i]).strip())
        except ValueError:
            arrival_d = None
        rec = {
            'Symbol': sym, 'ArrivalDate': str(df['ArrivalDate'].iloc[i]),
            'anchor': anchor, 'target': target, 'bucket': bucket.iloc[i],
            'price': value, 'ref': None, 'ref_src': '', 'ratio': None,
            'ratio_alt': None, 'after_close': (anchor == arrival_d),
            'F_audit': None, 'F_script': None, 'anchor_offby1': False,
            'basis_flag': '', 'verdict': '',
        }

        if pd.isna(value):
            rec['verdict'] = 'missing'
            rows.append(rec)
            continue
        if anchor is None:
            rec['verdict'] = 'unrefereed'
            rows.append(rec)
            continue

        splits = load_splits(sym, Path(args.splits_cache), splits_cache)
        # F_audit anchors on the session the price describes; F_script reproduces the
        # loader's anchor (the PR's session). They differ only when a split has an
        # ex_date on the PR session itself -- the latent off-by-one.
        f_audit = forward_split_factor(splits, anchor, bars_fetched_at=asof)
        f_script = forward_split_factor(splits, target, bars_fetched_at=asof)
        rec['F_audit'], rec['F_script'] = f_audit, f_script
        rec['anchor_offby1'] = (f_audit is not None and f_script is not None
                                and abs(f_audit - f_script) > 1e-12)

        # The two fill paths do not mean the same thing by "last daily close" when a
        # PR lands after the 16:00 close. The live IBKR path takes the bar before the
        # PR's TRADING session, which for an after-close PR is the day it arrived. The
        # orchestrator recorded, live, the last close available BEFORE that -- one
        # session earlier. Measured on the 2,528 rows where both candidate sessions
        # have a universe price: intraday ORCH rows fit the anchor 1946-37, but
        # after-close ORCH rows fit one-session-earlier 433-5, both at a median
        # |log ratio| of 0.0000. So the alternative is refereed too, and a row that
        # fits it is reported as a convention difference rather than a bad price.
        try:
            anchor_alt = prev_trading_day(anchor)
        except ValueError:
            anchor_alt = None

        ref, src = uni.get((sym, anchor)), 'universe'
        if ref is None:
            ref, src = orch.get((sym, str(df['ArrivalDate'].iloc[i]))), 'orch'
        if ref is None and args.yfinance and f_script:
            yc = load_yf_history(sym, yf_cache, YF_BARS_CACHE).get(anchor)
            if yc is not None:
                # yfinance is on today's basis; F brings it to as-traded, the basis
                # LastDailyClosePrice is on. Same F as the value under test.
                ref, src = yc * f_script, 'yfinance'
        if ref is None:
            rec['verdict'] = 'unrefereed'
            rows.append(rec)
            continue

        rec['ref'], rec['ref_src'] = ref, src
        rec['ratio'] = value / ref if ref else None

        # bar_close is the raw IBKR bar the loader multiplied by F. For orchestrator
        # rows nothing was multiplied, so the value IS the bar and fwd is 1.
        if rec['bucket'] == 'ORCH':
            flag, ratio, _ = basis_check(value, 1.0, ref, src, splits, anchor, asof)
        else:
            fs = f_script if f_script else 1.0
            flag, ratio, _ = basis_check(value / fs, fs, ref, src, splits, anchor, asof)
        rec['basis_flag'] = flag

        # basis_check's 0.75-1.34 agreement band answers "same share basis?", not
        # "same session?" -- on these names a one-session shift usually sits inside it.
        # So the session question is settled separately, by strict comparison against
        # both candidate sessions.
        alt = uni.get((sym, anchor_alt)) if (src == 'universe' and anchor_alt) else None
        if alt:
            rec['ratio_alt'] = value / alt
        e_anchor = abs(math.log(rec['ratio'])) if rec['ratio'] and rec['ratio'] > 0 else None
        e_alt = abs(math.log(rec['ratio_alt'])) if rec['ratio_alt'] and rec['ratio_alt'] > 0 else None

        if flag in ('basis_mismatch', 'bars_behind', 'bars_stale'):
            rec['verdict'] = 'suspect_basis'
        elif e_anchor is not None and e_anchor <= EXACT_TOL:
            rec['verdict'] = 'confirmed_exact'
        elif e_alt is not None and e_alt <= EXACT_TOL:
            # Lands exactly on the session BEFORE the one the live-fetch path uses.
            rec['verdict'] = 'confirmed_prior_session'
        elif flag in ('ok', 'ref_stale'):
            # Right share basis, but no session matches to the cent: the reference and
            # the stored price came off different feeds or different moments.
            rec['verdict'] = 'confirmed_band'
        else:
            rec['verdict'] = 'suspect_price'
        rows.append(rec)

    out = pd.DataFrame(rows)

    print("\n=== verdict x source bucket ===")
    print(pd.crosstab(out['verdict'], out['bucket']))
    print("\n=== referee coverage ===")
    print(out['ref_src'].replace('', '<none>').value_counts().to_string())
    filled = int(px.notna().sum())
    exact = (out['verdict'] == 'confirmed_exact').sum()
    prior = (out['verdict'] == 'confirmed_prior_session').sum()
    band = (out['verdict'] == 'confirmed_band').sum()
    print(f"\nexact match on the expected session : {exact}/{filled} ({100*exact/filled:.1f}%)")
    print(f"exact match one session EARLIER     : {prior}")
    print(f"right basis, no exact session match : {band}")
    print(f"unrefereed                          : {(out['verdict']=='unrefereed').sum()}")
    print(f"missing                             : {(out['verdict']=='missing').sum()}")

    print("\n=== session convention: which session does the stored close describe? ===")
    ac = out[out['ref_src'] != '']
    print(pd.crosstab(ac['after_close'], ac['verdict']))
    n_after = int(out['after_close'].sum())
    if prior:
        print(f"after-close PRs in the file: {n_after}/{len(out)} "
              f"({100*n_after/len(out):.1f}%) -- for these the orchestrator path stores "
              f"the session BEFORE the one the live-fetch path stores. Repair with "
              f"fix_lastdailyclose_sessions.py.")
    else:
        print(f"after-close PRs in the file: {n_after}/{len(out)} "
              f"({100*n_after/len(out):.1f}%), and none of them sit on the earlier "
              f"session -- the two fill paths agree on what 'last close' means.")

    off = out['anchor_offby1'].sum()
    print(f"\nrows hit by the split-anchor off-by-one (F_audit != F_script): {off}")

    sus = out[out['verdict'].str.startswith('suspect')]
    if len(sus):
        print(f"\n=== {len(sus)} suspect rows ===")
        cols = ['Symbol', 'ArrivalDate', 'anchor', 'bucket', 'price', 'ref',
                'ref_src', 'ratio', 'F_script', 'basis_flag', 'verdict']
        with pd.option_context('display.width', 250, 'display.max_rows', 200):
            print(out.loc[sus.index, cols].to_string())

    if args.row_out:
        out.to_csv(args.row_out, sep='\t', index=False)
        print(f"\nper-row verdicts -> {args.row_out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
