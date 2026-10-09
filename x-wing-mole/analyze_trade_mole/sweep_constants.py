#!/usr/bin/env python3
"""One-constant-at-a-time sweeps through the REAL mole, scored on the labelled
corpus (2026-10-08).

For each constant it prints, per value: pos / neu / neg / late fired, the operator's
total cost, how many bad / grey / good files were ADDED or REMOVED against the
module as it stands, and how many late files fire earlier. `--files` lists the
files behind every step, so a threshold is never judged on a count alone (the
2.4/2.5 passes rejected every constant whose plateau was one file wide).

It scores ONLY labelled files unless `--unlabelled` is given; run both before
trusting a loosening step (a rule that scores perfectly on labels can still be
measuring the wrong thing — branch NF on 2026-09-18).

Usage:
    python sweep_constants.py --baselines ../mole-outputs/baseline-overrides-2026-10-08.csv
    python sweep_constants.py --only SURGE_N_MIN_PRICE_RISE_PCT --files
"""
import argparse
import logging
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_rules as E  # noqa: E402
import replay_mole as R  # noqa: E402
import session_context as SC  # noqa: E402

SWEEPS = {
    "SURGE_N_MIN_PRICE_RISE_PCT": [0.04, 0.035, 0.03, 0.025, 0.02, 0.015, 0.01],
    "SURGE_FLOW_MIN_PRICE_RISE_PCT": [0.012, 0.011, 0.010, 0.009, 0.008, 0.006, 0.004],
    "SURGE_E_MIN_FLOW_X_BASELINE": [10.0, 8.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
    "SURGE_FLOW_MIN_DOLLAR_RATE_5S": [3.0, 2.5, 2.0, 1.5, 1.0, 0.5],
    "PRETRADE_MAX_GAP_SEC": [1.25, 1.3, 1.4, 1.5, 1.75, 2.0, 3.0],
    "PRETRADE_MAX_RATIO": [5000.0, 10000.0, 20000.0, 50000.0],
    "PRETRADE_MIN_GAP_VS_CLOSE_PCT": [5.0, 4.0, 3.0, 2.0, 1.0, 0.0],
    "PRETRADE_STALENESS_THRESHOLD": [100.0, 70.0, 50.0, 30.0],
    "BSLN_ITI_LAST3_THRESHOLD": [1550.0, 1200.0, 900.0, 600.0, 300.0],
    "SURGE_C_MIN_CUM_DOLLAR": [80.0, 60.0, 40.0, 20.0],
    "SURGE_ITI_COLLAPSE_MAX_SEC": [0.28, 0.30, 0.35, 0.40, 0.50],
    "SURGE_ITI_COLLAPSE_MIN_TRADES_5S": [3, 2],
    "SURGE_D_MIN_VOLUME_5S": [20.0, 15.0, 10.0, 5.0],
    "SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC": [1.5, 2.0, 3.0, 5.0, 10.0],
    "SURGE_MIN_PX_VS_VWAP_PCT": [-1.0, -2.0, -3.0, -5.0, -10.0, None],
    "SURGE_NF_MIN_FLOW_X_BASELINE": [None, 1000.0, 300.0, 100.0, 50.0, 30.0],
    "DEFAULT_SURGE_MAX_PRICE_DROP_PCT": [0.005, 0.01, 0.02, 0.05],
}


def short(name):
    for pre in ("_pos_", "_neg_", "_neu_", "_late_", "_neu-late_"):
        if name.startswith(pre):
            name = name[len(pre):]
    return name.replace("trade-mole-table_", "").replace(".csv", "")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", default="trade-mole-2.5.py")
    ap.add_argument("--baselines", default=None)
    ap.add_argument("--only", default=None, help="one constant name")
    ap.add_argument("--files", action="store_true", help="list the files behind each step")
    ap.add_argument("--unlabelled", action="store_true")
    args = ap.parse_args()

    logging.disable(logging.CRITICAL)
    log = logging.getLogger("sweep")
    mole = R.load_mole(args.module)
    if args.baselines:
        R.load_baseline_overrides(args.baselines)
        SC.BASELINE_OVERRIDES = R.BASELINE_OVERRIDES
    files = E.unlabelled_files() if args.unlabelled else E.labelled_files()

    base_res = E.run_corpus(mole, {}, files, log)
    base = {d["file"]: d for d in base_res}
    bagg, _ = E.aggregate(base_res)
    if args.unlabelled:
        print(f"unlabelled control, 2.5 fires: {bagg['unl_fire']}")
    else:
        print(f"base: {E.fmt(bagg)}")

    for name, values in SWEEPS.items():
        if args.only and name != args.only:
            continue
        if not hasattr(mole, name):
            continue
        cur = getattr(mole, name)
        print(f"\n{name}   (module value {cur})")
        print(f"  {'value':>9}  {'pos':>5} {'neu':>5} {'neg':>5} {'late':>5} {'total':>7} {'d_tot':>6}"
              f"  +bad +grey +good  -bad -grey -good  lateEarlier")
        for v in values:
            res = E.run_corpus(mole, {name: v}, files, log)
            agg, _ = E.aggregate(res)
            by = {d["file"]: d for d in res}
            added = [n for n in by if by[n]["fired"] and not base[n]["fired"]]
            removed = [n for n in by if base[n]["fired"] and not by[n]["fired"]]
            earlier = [n for n in by if by[n]["fired"] and base[n]["fired"]
                       and by[n]["t_fire"] < base[n]["t_fire"] - 1e-6 and by[n]["lab"] == "late"]
            cnt = lambda lst, labs: sum(1 for n in lst if by[n]["lab"] in labs)
            if args.unlabelled:
                print(f"  {str(v):>9}  fires {agg['unl_fire']:3}  added {len(added)}  removed {len(removed)}")
            else:
                tot = E.total_cost(agg)
                print(f"  {str(v):>9}  {agg['pos_fire']:2}/{agg['pos_n']:<2} {agg['neu_fire']:2}/{agg['neu_n']:<2} "
                      f"{agg['neg_fire']:2}/{agg['neg_n']:<3} {agg['late_fire']:2}/{agg['late_n']:<2} "
                      f"{tot:7.1f} {tot - E.total_cost(bagg):+6.1f}  "
                      f"{cnt(added, ('neg',)):4} {cnt(added, ('neu',)):5} {cnt(added, ('pos', 'late')):5}  "
                      f"{cnt(removed, ('neg',)):4} {cnt(removed, ('neu',)):5} {cnt(removed, ('pos', 'late')):6}  "
                      f"{len(earlier):6}")
            if args.files and (added or removed or earlier):
                for n in added:
                    print(f"            ADDED   {by[n]['lab']:5}{short(n)} ({by[n]['rule']} @{by[n]['t_fire']:.2f}s)"
                          if by[n]['t_fire'] is not None else f"            ADDED   {by[n]['lab']:5}{short(n)}")
                for n in removed:
                    print(f"            REMOVED {by[n]['lab']:5}{short(n)}")
                for n in earlier:
                    print(f"            EARLIER {by[n]['lab']:5}{short(n)} {base[n]['t_fire']:.2f}s -> {by[n]['t_fire']:.2f}s")


if __name__ == "__main__":
    main()
