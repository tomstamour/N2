#!/usr/bin/env python
"""Add EarningScore (+ ID, Headline) to the XGB validation-scores dataset, so the
O5.2 gate can be replayed over it.

Why this exists: the live gate's EARNINGS rule is

    EARNINGS: FineClass == Earnings_Results AND EarningScore >= 4 AND kw15_proba_bd >= 0.50

and EarningScore is NOT in the XGBscores file -- orch52cols_backfill_addOn.py dropped
it because an upstream purge step removed it from the canonical corpus. replay_gate.py
SILENTLY skips a gate field whose column is absent (its `keep` dict filters on
`c in df.columns`), so replaying without this backfill does not error -- it just makes
EARNINGS unable to fire and quietly under-counts O5.2's trades. Earnings_Results is
253/1380 rows here and is deliberately in the ClassWhitelist, so that is not a rounding
error. `ID` is added for the same reason: replay_gate.py --verify-against joins on it.

Source is the canonical Classed corpus (the same file orch52cols_backfill_addOn.py pulled
the six gate columns from), joined on (Symbol, ArrivalDate, ArrivalTime) -- the XGBscores
file has no ID of its own, which is the whole reason that key is used here too.

!! The size denominator is `Float * LastDailyClosePrice`, NOT MktCap. earning_scorer's
   family I (size-relative revenue/profit) divides by a float-cap, and the canonical
   corpus spells that close `LastDailyClosePrice` while the older earnScored corpus spells
   it `LastDailyClose`. Verified this session on the 155 rows that carry a reference score:
   LastDailyClosePrice reproduces EarningScore EXACTLY (155/155); falling back to MktCap
   reproduces only 92.3% (98.7% at the >= 4 gate cut). The --cross-check below is what
   keeps that honest.

!! EarningScore here is an ESTIMATE on earnings rows, and replay_gate.py says why: the
   corpus Body is hard-capped at 400 words (1311 of these 1380 rows sit at exactly 400)
   while the live scorer reads the whole body. Measured fidelity 2026-08-12..14 was
   91.4% on Earnings_Results vs 100.0% everywhere else. It is the best available and it
   is far better than no EARNINGS branch at all -- but never calibrate that rule here.

Input is never modified in place; output is written to <input_stem>_gateReplay.tsv

Usage:
    /home/tom/venv/bin/python earnscore_backfill_addOn.py \
        --input outputs/2026-07-28_2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_PCA_val_XGBscores_orch52cols.tsv
"""

import argparse
import importlib.util as ilu
import logging
import sys
import tempfile
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
log = logging.getLogger(__name__)

_SCRIPT_DIR = Path(__file__).resolve().parent
_SCRIPTS_DIR = _SCRIPT_DIR.parent

_DEFAULT_SOURCE = (
    _SCRIPT_DIR / 'outputs'
    / '08_PRstats_2026-05-04-2026-08-28-CoffeeFltrd_fromPRhigh_noSkip_11EncBodyScored'
    '_11EncScored_mktstate_SIback_noDupCol_FinBERTScored_freqSizeBack_DailyClose'
    '_mcapBack_vixfng_dow_Classed.tsv'
)
# The corpus that already carries a frozen EarningScore, used only to prove this
# rescoring reproduces it. Partial coverage (it stops at 2026-08-13) is expected.
_DEFAULT_REFERENCE = (
    _SCRIPT_DIR / 'outputs'
    / '07_PRstats_2026-05-04-2026-08-13_CoffeeFilter_fromPRhigh_11EncScored_kw15Scored'
    '_BodyEnc_earnScored.tsv'
)
_EARNING_SCORER = _SCRIPTS_DIR / 'tools' / 'Earning_Scorer' / 'earning_scorer.py'

JOIN_KEY = ['Symbol', 'ArrivalDate', 'ArrivalTime']
# source column -> name score_dataframe()/_row_size_dollars() expects
SOURCE_COLUMNS = {
    'ID': 'ID',
    'Headline': 'Headline',
    'Body': 'Body',
    'Float': 'Float',
    'MktCap': 'MktCap',
    'LastDailyClosePrice': 'LastDailyClose',
}
# what actually lands in the output, on top of the input's own columns
ADDED_COLUMNS = ['ID', 'Headline', 'EarningScore']


def _load_earning_scorer():
    """importlib, because tools/Earning_Scorer is not a package on sys.path."""
    if not _EARNING_SCORER.exists():
        sys.exit(f"earning_scorer.py not found at {_EARNING_SCORER}")
    spec = ilu.spec_from_file_location('earning_scorer', str(_EARNING_SCORER))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _atomic_write(df, out_path):
    """Write via a temp file in the destination dir, then rename -- a half-written
    TSV that still parses is worse than no TSV."""
    out_path = Path(out_path)
    fd, tmp = tempfile.mkstemp(dir=str(out_path.parent), suffix='.tsv')
    try:
        with open(fd, 'w', newline='') as fh:
            df.to_csv(fh, sep='\t', index=False)
        Path(tmp).replace(out_path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', required=True,
                    help='the XGBscores + orch52cols TSV to enrich')
    ap.add_argument('--source', default=str(_DEFAULT_SOURCE),
                    help='canonical Classed corpus to pull Headline/Body/Float/close from')
    ap.add_argument('--reference', default=str(_DEFAULT_REFERENCE),
                    help='corpus with a frozen EarningScore, for the cross-check')
    ap.add_argument('--out', default=None,
                    help='output TSV (default: <input_stem>_gateReplay.tsv)')
    ap.add_argument('--no-cross-check', action='store_true',
                    help='skip the reference-agreement check (not recommended)')
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit(f"input not found: {inp}")
    df = pd.read_csv(inp, sep='\t', dtype=str, keep_default_na=False, na_values=[''])
    log.info("input %s: %d rows, %d cols", inp.name, len(df), len(df.columns))

    missing = [c for c in JOIN_KEY if c not in df.columns]
    if missing:
        sys.exit(f"input is missing join columns: {missing}")
    already = [c for c in ADDED_COLUMNS if c in df.columns]
    if already:
        sys.exit(f"input already has {already} -- refusing to overwrite")
    if df.duplicated(JOIN_KEY).any():
        sys.exit(f"input has {int(df.duplicated(JOIN_KEY).sum())} duplicate join keys")

    src_path = Path(args.source)
    if not src_path.exists():
        sys.exit(f"source not found: {src_path}")
    src = pd.read_csv(src_path, sep='\t', dtype=str, keep_default_na=False,
                      na_values=[''], usecols=JOIN_KEY + list(SOURCE_COLUMNS))
    if src.duplicated(JOIN_KEY).any():
        sys.exit(f"source has {int(src.duplicated(JOIN_KEY).sum())} duplicate join keys")
    src = src.rename(columns=SOURCE_COLUMNS)

    merged = df.merge(src, on=JOIN_KEY, how='left', validate='one_to_one')
    matched = int(merged['ID'].notna().sum())
    log.info("joined to %s: %d/%d rows matched", src_path.name, matched, len(df))
    if matched != len(df):
        # A partial join would score the unmatched rows on empty text and hand the
        # gate a confident EarningScore of 0. Nothing downstream could tell.
        sys.exit(f"join is not total ({matched}/{len(df)}) -- refusing to score")

    es = _load_earning_scorer()
    log.info("scoring %d rows with %s (frozen params)", len(merged), _EARNING_SCORER.name)
    scored = es.score_dataframe(
        merged[['Headline', 'Body', 'Float', 'LastDailyClose', 'MktCap']])
    merged['EarningScore'] = pd.to_numeric(scored['EarningScore']).astype(int)

    sc = merged['EarningScore']
    log.info("EarningScore: min %d  median %.1f  max %d  >0 %d  >=4 %d",
             sc.min(), sc.median(), sc.max(), int((sc > 0).sum()), int((sc >= 4).sum()))
    if 'FineClass' in merged.columns:
        er = merged[merged['FineClass'] == 'Earnings_Results']
        log.info("Earnings_Results rows: %d, of which EarningScore >= 4: %d",
                 len(er), int((er['EarningScore'] >= 4).sum()))

    if not args.no_cross_check:
        _cross_check(merged, Path(args.reference))

    out = Path(args.out) if args.out else inp.with_name(inp.stem + '_gateReplay.tsv')
    keep = list(df.columns) + ADDED_COLUMNS
    _atomic_write(merged[keep], out)
    log.info("wrote %s (%d rows, %d cols)", out, len(merged), len(keep))


def _cross_check(merged, ref_path):
    """Prove the rescoring reproduces the frozen EarningScore wherever one exists.

    A disagreement means earn_score_params.json moved, the source corpus changed, or
    the size denominator is wrong -- all of which would silently move the EARNINGS
    branch's fire set, so this stops the run rather than warning."""
    if not ref_path.exists():
        log.warning("reference %s not found -- cross-check SKIPPED", ref_path.name)
        return
    ref = pd.read_csv(ref_path, sep='\t', dtype=str, keep_default_na=False,
                      na_values=[''], usecols=['ID', 'EarningScore'])
    ref = ref[ref['EarningScore'].notna()].drop_duplicates('ID')
    ref['ref_score'] = pd.to_numeric(ref['EarningScore'], errors='coerce')
    ref = ref.drop(columns=['EarningScore'])

    m = merged[['ID', 'EarningScore', 'FineClass']].merge(ref, on='ID', how='inner')
    m = m[m['ref_score'].notna()]
    if m.empty:
        log.warning("no overlapping IDs with %s -- cross-check SKIPPED", ref_path.name)
        return
    exact = m['EarningScore'].eq(m['ref_score'].astype(int))
    gate = m['EarningScore'].ge(4).eq(m['ref_score'].ge(4))
    log.info("cross-check vs %s: %d overlapping rows, exact %d (%.1f%%), "
             ">=4 agreement %d (%.1f%%)", ref_path.name, len(m), int(exact.sum()),
             100 * exact.mean(), int(gate.sum()), 100 * gate.mean())
    # Only Earnings_Results rows can reach the gate: the EARNINGS rule's first
    # condition is `FineClass == Earnings_Results`, so a disagreement anywhere else
    # cannot change a single fire. Those are reported, not fatal -- the known cause is
    # that the two corpora carry different LastDailyClose bases on a handful of rows,
    # which moves the float-cap denominator family I divides by.
    er = m[m['FineClass'] == 'Earnings_Results']
    er_exact = er['EarningScore'].eq(er['ref_score'].astype(int))
    if len(er):
        log.info("  of which Earnings_Results (the only rows the gate reads): "
                 "%d rows, exact %.1f%%", len(er), 100 * er_exact.mean())
    for _, r in m[~exact].head(10).iterrows():
        log.warning("  %s (%s): rescored %d, reference %d",
                    r['ID'], r['FineClass'], r['EarningScore'], int(r['ref_score']))
    if len(er) and not er_exact.all():
        sys.exit("cross-check FAILED -- rescoring does not reproduce the frozen "
                 "EarningScore on Earnings_Results rows; the EARNINGS branch would "
                 "replay wrong")


if __name__ == '__main__':
    main()
