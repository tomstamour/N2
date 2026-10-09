"""Repair the two LastDailyClosePrice defects audit_lastdailyclose.py found.

This is a data-repair pass over a finished file, not a change to how prices are
fetched, which is why it lives here rather than in lastDailyClose_AddOn.py. It is
fully offline: every correction it makes is already sitting in the nightly universe
snapshots. Nothing is written back over the input.

FIX 1 -- one definition of "last daily close".

  The column arrived holding two. For a PR that lands after the 16:00 close the
  orchestrator recorded the last close available WHEN THE PR ARRIVED, which is the
  session before its ArrivalDate; the live IBKR path returns the close before the
  PR's TRADING session, which is the ArrivalDate itself. Measured against
  point-in-time universe snapshots, after-close orchestrator rows fit one session
  earlier 383-5 while intraday ones fit the anchor 1946-37, both at a median
  |log ratio| of 0.0000, and no live-fetch row matches the earlier session at all.

  The ArrivalDate convention wins: it is the most recent close before the move being
  measured, and it is what every live-fetch row already uses. A row is rewritten only
  when its stored value matches the earlier session to the cent AND a universe close
  exists for the right one -- never on a guess.

FIX 2 -- two values that are simply wrong.

  Screening every row against its OWN point-in-time closes across the corpus turns up
  exactly two sitting outside their symbol's entire observed range by a clean
  split-like factor: HUBC at 5000.00 (2463x) and CCG at 18.45 (36x). Both are the
  failure mode market_state_calculator.py names these very tickers for -- IBKR
  back-adjusted a split yfinance has no record of, so F could not undo it.

  The screen only FINDS rows. What gets written comes from CORRECTIONS below, an
  explicit reviewed table, so a heuristic can never silently rewrite a price.

Run with the ibapi venv:
    /home/tom/venv/bin/python fix_lastdailyclose_sessions.py \\
        -i outputs/..._DailyClose_NaNfilled.tsv
"""

import argparse
import math
import os
import sys
from pathlib import Path

import pandas as pd

from audit_lastdailyclose import (
    EXACT_TOL, UNIVERSE_DIR, SCRIPT_DIR,
    prev_trading_day, calibrate_universe_sessions, load_universe_refs,
    plausible_split_ratio,
)
from highOfDay_addON import get_target_date

# Reviewed, corroborated replacements. Keyed (Symbol, ArrivalDate); the screen below
# must find exactly these and nothing else, or the run stops without writing.
CORRECTIONS = {
    ('HUBC', '2026-06-03'): (
        0.65,
        "IBKR bar 100,000 = 2026-09-04 shares; yfinance close 13.00 x F 0.05 = 0.65, "
        "and HUBC's own universe closes bottom out at 0.6748. Stored 5000.00."),
    ('CCG', '2026-05-28'): (
        0.505,
        "yfinance has NO split on record for CCG (F=1) and a smooth series across the "
        "date; its universe closes run 0.3724-0.51. Stored 18.45, a clean ~36x."),
}

# Outside their symbol's range but not by a split-like factor, and not implausible on
# their face. Reported for review; deliberately NOT corrected.
REVIEW_ONLY = {('OPAD', '2026-06-04'), ('LIMN', '2026-06-03'), ('XOS', '2026-06-02')}

IMPLAUSIBLE_FACTOR = 5.0        # how far outside its own range before we look harder


def build_output_path(input_path: str, output_dir: str | None) -> str:
    d = os.path.abspath(output_dir) if output_dir else os.path.dirname(os.path.abspath(input_path))
    stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(d, f'{stem}_SessionFix.tsv')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', '-i', required=True, dest='input_file',
                    help='TSV carrying LastDailyClosePrice. Never modified.')
    ap.add_argument('--output-dir', '-o', dest='output_dir', default=None)
    ap.add_argument('--universe-dir', default=str(UNIVERSE_DIR))
    ap.add_argument('--audit-rows', default=str(SCRIPT_DIR / 'cache' / 'audit_rows.tsv'),
                    help='Per-row verdicts from audit_lastdailyclose.py --row-out. '
                         'Required for the bad-basis screen: the basis verdict is the '
                         'second witness that separates a wrong price from a real split.')
    ap.add_argument('--dry-run', action='store_true',
                    help='Report what would change and write nothing.')
    args = ap.parse_args()

    if not os.path.isfile(args.input_file):
        print(f"input not found: {args.input_file}", file=sys.stderr)
        return 1

    df = pd.read_csv(args.input_file, sep='\t', low_memory=False)
    px = pd.to_numeric(df['LastDailyClosePrice'], errors='coerce')
    print(f"[in ] {len(df)} rows, {px.notna().sum()} filled, {px.isna().sum()} empty")

    # The session each stored close should describe.
    anchors = []
    for at, ad in zip(df['ArrivalTime'].astype(str), df['ArrivalDate'].astype(str)):
        try:
            anchors.append(prev_trading_day(get_target_date(at.strip(), ad.strip())))
        except Exception:
            anchors.append(None)

    yf_cache: dict = {}
    session_map = calibrate_universe_sessions(
        Path(args.universe_dir), SCRIPT_DIR / 'cache' / 'universe_session_map.tsv',
        yf_cache)
    wanted = {a for a in anchors if a is not None}
    for a in list(wanted):
        try:
            wanted.add(prev_trading_day(a))
        except ValueError:
            pass
    uni = load_universe_refs(Path(args.universe_dir), wanted, session_map)

    # ---------------- FIX 1: session normalization ----------------
    if 'LastDailyClosePrice_AtArrival' not in df.columns:
        df['LastDailyClosePrice_AtArrival'] = df['LastDailyClosePrice']
    if 'LastDailyClosePrice_SessionFix' not in df.columns:
        df['LastDailyClosePrice_SessionFix'] = ""
    if 'LastDailyClosePrice_Source' not in df.columns:
        df['LastDailyClosePrice_Source'] = ""

    normalized = no_ref = 0
    for i in range(len(df)):
        anchor, value = anchors[i], px.iloc[i]
        if anchor is None or pd.isna(value):
            continue
        sym = str(df['Symbol'].iloc[i]).strip().upper()
        try:
            earlier = prev_trading_day(anchor)
        except ValueError:
            continue

        ref_earlier = uni.get((sym, earlier))
        if not ref_earlier or ref_earlier <= 0:
            continue
        # Does the stored value describe the EARLIER session, to the cent?
        if abs(math.log(value / ref_earlier)) > EXACT_TOL:
            continue

        ref_anchor = uni.get((sym, anchor))
        # When the price did not move between the two sessions the value matches both,
        # and there is nothing to repair. Without this the pass "fixes" ~255 rows whose
        # numbers never change, and the count stops meaning anything.
        if ref_anchor and ref_anchor > 0 and abs(math.log(value / ref_anchor)) <= EXACT_TOL:
            continue

        if not ref_anchor or ref_anchor <= 0:
            # Stale, but there is no point-in-time close for the right session to put
            # in its place. Flag it rather than invent one.
            df.at[df.index[i], 'LastDailyClosePrice_SessionFix'] = 'no_ref'
            no_ref += 1
            continue

        df.at[df.index[i], 'LastDailyClosePrice'] = ref_anchor
        df.at[df.index[i], 'LastDailyClosePrice_SessionFix'] = 'normalized'
        normalized += 1

    print(f"\n[fix1] {normalized} rows re-based onto the session before the PR's "
          f"trading session; {no_ref} stale but unfixable (no universe close).")

    # ---------------- FIX 2: bad-basis screen + reviewed corrections ----------------
    # A price outside its symbol's range is USUALLY just a split -- a name that
    # reverse-split mid-corpus really did trade 10x lower in May than the June-August
    # snapshots show. The range test alone flags 25 such rows, and normalising both
    # sides onto today's basis does not separate them either, because the split factor
    # is exactly what is unreliable on these names. What distinguishes a genuine basis
    # error is a SECOND, independent witness: basis_check() finding that IBKR's bar and
    # Yahoo's close disagree by a clean split factor. That is what audit_lastdailyclose
    # computes, so this pass consumes its verdicts rather than re-deriving them.
    audit = None
    if os.path.isfile(args.audit_rows):
        audit = pd.read_csv(args.audit_rows, sep='\t')
        suspect = {(str(r.Symbol).strip().upper(), str(r.ArrivalDate).strip())
                   for r in audit.itertuples() if str(r.verdict) == 'suspect_basis'}
        print(f"\n[fix2] {len(suspect)} rows carry a suspect_basis verdict from "
              f"{args.audit_rows}")
    else:
        print(f"\n[fix2] no audit verdicts at {args.audit_rows} — SKIPPING the "
              f"bad-basis screen. Produce them with:\n"
              f"       audit_lastdailyclose.py -i <file> --yfinance --row-out "
              f"{args.audit_rows}", file=sys.stderr)
        suspect = None

    found, borderline = [], []
    if suspect is not None:
        bysym: dict = {}
        for (sym, _sess), v in uni.items():
            bysym.setdefault(sym, []).append(v)

        px2 = pd.to_numeric(df['LastDailyClosePrice'], errors='coerce')
        for i in range(len(df)):
            value = px2.iloc[i]
            if pd.isna(value) or value <= 0:
                continue
            sym = str(df['Symbol'].iloc[i]).strip().upper()
            key = (sym, str(df['ArrivalDate'].iloc[i]).strip())
            if key not in suspect:
                continue
            g = bysym.get(sym)
            if not g:
                continue
            lo, hi = min(g), max(g)
            if value > hi:
                factor = value / hi
            elif value < lo:
                factor = lo / value
            else:
                continue
            if factor >= IMPLAUSIBLE_FACTOR and plausible_split_ratio(factor) is not None:
                found.append((i, key, value, lo, hi, factor))
            elif factor > 1.5:
                borderline.append((key, value, lo, hi, factor))

    if suspect is None:
        print("[fix2] screen skipped; fix 1 alone will be written.")
    print(f"\n[fix2] screen: {len(found)} rows both flagged suspect_basis AND outside "
          f"their symbol's own point-in-time range by >={IMPLAUSIBLE_FACTOR}x on a "
          f"split-like factor.")
    for _i, key, value, lo, hi, factor in found:
        print(f"       {key[0]} {key[1]}: {value:.4f} vs range {lo:.4f}-{hi:.4f} ({factor:.0f}x)")

    screened = {k for _i, k, *_ in found}
    if suspect is not None and screened != set(CORRECTIONS):
        print("\n*** The screen does not match the reviewed CORRECTIONS table. ***",
              file=sys.stderr)
        print(f"    screen found : {sorted(screened)}", file=sys.stderr)
        print(f"    table expects: {sorted(CORRECTIONS)}", file=sys.stderr)
        print("    Refusing to write. Review the new rows and update CORRECTIONS.",
              file=sys.stderr)
        return 2

    corrected = 0
    for i, key, value, _lo, _hi, _f in found:
        new_value, why = CORRECTIONS[key]
        df.at[df.index[i], 'LastDailyClosePrice'] = new_value
        df.at[df.index[i], 'LastDailyClosePrice_Source'] = 'corrected_bad_basis'
        corrected += 1
        print(f"       -> {key[0]} {key[1]}: {value:.4f} -> {new_value}  ({why})")
    print(f"[fix2] {corrected} corrected from the reviewed table.")

    if borderline:
        print(f"\n[fix2] {len(borderline)} borderline rows (1.5-{IMPLAUSIBLE_FACTOR}x "
              f"outside range, no clean split factor) — REVIEW ONLY, unchanged:")
        for key, value, lo, hi, factor in sorted(borderline, key=lambda r: -r[4]):
            mark = '' if key in REVIEW_ONLY else '   <- not in REVIEW_ONLY'
            print(f"       {key[0]} {key[1]}: {value:.4f} vs {lo:.4f}-{hi:.4f} "
                  f"({factor:.1f}x){mark}")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    out_path = build_output_path(args.input_file, args.output_dir)
    df.to_csv(out_path, sep='\t', index=False)
    print(f"\nSaved to {out_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
