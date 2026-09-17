#!/usr/bin/env python3
"""Does a candidate condition improve a gate that already fired?

The standalone question "is column X predictive?" is answered by AUC and by the
TP/FP matrix (false_positive_count.py).  That is NOT the deployment question.
What matters for a live trigger is the INCREMENT: among the rows the gate
already fires on, does adding the condition raise precision -- and by enough
that the improvement is distinguishable from noise?

For each --gate G and each --condition C this reports, per period block:

    gate alone      TP/FP, precision, lift vs the block's base rate
    gate AND cond   same, on the subset that also satisfies C
    delta           precision(gate AND cond) - precision(gate alone), with a
                    Newcombe 95% CI on the difference of two proportions.
                    If that CI contains 0, the condition is NOT demonstrated to
                    help -- regardless of how good the raw precision looks.

Period blocks matter because a corpus concatenated from several extraction runs
can mix base rates; a pooled precision can then mislead (Simpson's paradox).
Blocks are split on --date-col: rows with a date >= --split-date are "recent",
rows with an earlier or absent date are "early".

IMPORTANT -- match the evaluation set to the model's training provenance.
Externally-trained scorers (the 11 encoders, FinBERT) never saw any row here, so
ALL rows are a fair test for them.  Only the in-house keyword family needs the
recent-block restriction, because it trained on the early block.

Conditions are written as `COLUMN>=VALUE` or `COLUMN<=VALUE`.

Pure numpy / pandas / scipy.

Run:
    /home/tom/venv/bin/python conditional_value.py \
        ../outputs/PRstats_..._kwScored.tsv \
        --gate "model_proba_PLUS_kw_mult_nb>=0.6" \
        --condition "FinMarBa_ind_hd<=0.1"
"""
import argparse
import pathlib
import re
import sys

import numpy as np
import pandas as pd

TARGET_DEFAULT = "fromPR_High(%)"
COND_RE = re.compile(r"^\s*(.+?)\s*(>=|<=)\s*([-+0-9.eE]+)\s*$")


def parse_condition(spec, columns):
    m = COND_RE.match(spec)
    if not m:
        sys.exit(f"bad condition {spec!r}; expected 'COLUMN>=VALUE' or "
                 f"'COLUMN<=VALUE'")
    col, op, val = m.group(1), m.group(2), float(m.group(3))
    if col not in columns:
        sys.exit(f"condition column {col!r} not in file")
    return col, op, val


def apply_condition(df, col, op, val):
    v = pd.to_numeric(df[col], errors="coerce")
    fired = (v >= val) if op == ">=" else (v <= val)
    return (fired & v.notna()).values


def wilson(k, n, z=1.96):
    """Wilson score interval for a single proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def newcombe_diff(k1, n1, k2, n2, z=1.96):
    """Newcombe's method 10 CI for p2 - p1, built from the two Wilson
    intervals.  Robust at the small counts and extreme proportions where the
    normal-approximation CI misbehaves."""
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    p1, p2 = k1 / n1, k2 / n2
    lo = (p2 - p1) - z * np.sqrt(l2 * (1 - l2) / n2 + u1 * (1 - u1) / n1)
    hi = (p2 - p1) + z * np.sqrt(u2 * (1 - u2) / n2 + l1 * (1 - l1) / n1)
    return (lo, hi)


def cell(fired, movers):
    tp = int((fired & movers).sum())
    n = int(fired.sum())
    return tp, n - tp, n


def required_n(base, target_lift, power=0.80, alpha=0.05):
    """Rough per-arm n needed to show precision = base*target_lift differs from
    base, two-proportion normal approximation."""
    from scipy.stats import norm
    p1 = base
    p2 = min(base * target_lift, 0.999)
    if p2 <= p1:
        return float("nan")
    pbar = (p1 + p2) / 2
    za, zb = norm.ppf(1 - alpha / 2), norm.ppf(power)
    return ((za * np.sqrt(2 * pbar * (1 - pbar))
             + zb * np.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
            / (p2 - p1) ** 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv")
    ap.add_argument("--target", default=TARGET_DEFAULT)
    ap.add_argument("--threshold", type=float, default=30.0)
    ap.add_argument("--gate", action="append", required=True,
                    help="repeatable, e.g. 'Prosus_pos_hd>=0.8'")
    ap.add_argument("--condition", action="append", required=True,
                    help="repeatable, e.g. 'FinMarBa_ind_hd<=0.1'")
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--split-date", default="2026-06-20")
    ap.add_argument("--max-outlier", type=float, default=500.0)
    args = ap.parse_args()

    path = pathlib.Path(args.tsv)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    df = pd.read_csv(path, sep="\t", dtype=str)
    t = pd.to_numeric(df[args.target], errors="coerce")
    keep = t.notna() & (t <= args.max_outlier)
    df, t = df[keep].reset_index(drop=True), t[keep].reset_index(drop=True)
    movers = (t.values >= args.threshold)

    d = df[args.date_col].fillna("").astype(str) if args.date_col in df.columns \
        else pd.Series([""] * len(df))
    recent = (d.ne("") & d.ge(args.split_date)).values
    blocks = [("FULL corpus", np.ones(len(df), bool)),
              (f"early (< {args.split_date})", ~recent),
              (f"recent (>= {args.split_date})", recent)]

    print(f"# {path.name}")
    print(f"# target {args.target} >= {args.threshold:g} | rows {len(df)} | "
          f"movers {int(movers.sum())} ({movers.mean():.1%})")
    print(f"# delta CI = Newcombe 95% on precision(gate AND cond) - "
          f"precision(gate). Contains 0 -> not demonstrated.\n")

    for gspec in args.gate:
        gcol, gop, gval = parse_condition(gspec, df.columns)
        gate = apply_condition(df, gcol, gop, gval)
        for cspec in args.condition:
            ccol, cop, cval = parse_condition(cspec, df.columns)
            cond = apply_condition(df, ccol, cop, cval)
            print(f"=== GATE {gspec}   +   COND {cspec} ===")
            print(f"{'block':26s} {'gate alone':>22s} {'gate AND cond':>22s} "
                  f"{'delta precision':>28s}")
            for label, m in blocks:
                base = movers[m].mean() if m.sum() else float("nan")
                tp1, fp1, n1 = cell(gate & m, movers)
                tp2, fp2, n2 = cell(gate & cond & m, movers)
                if n1 == 0:
                    print(f"{label:26s} {'no fires':>22s}")
                    continue
                p1 = tp1 / n1
                s1 = f"{tp1}/{fp1} = {p1:.0%} ({p1/base:.2f}x)" if base else "-"
                if n2 == 0:
                    print(f"{label:26s} {s1:>22s} {'no fires':>22s}")
                    continue
                p2 = tp2 / n2
                s2 = f"{tp2}/{fp2} = {p2:.0%} ({p2/base:.2f}x)" if base else "-"
                lo, hi = newcombe_diff(tp1, n1, tp2, n2)
                verdict = "helps" if lo > 0 else ("hurts" if hi < 0 else "n.s.")
                s3 = f"{p2-p1:+.1%} [{lo:+.1%}, {hi:+.1%}] {verdict}"
                print(f"{label:26s} {s1:>22s} {s2:>22s} {s3:>28s}")
                if label.startswith("FULL"):
                    need = required_n(base, 2.0)
                    print(f"{'':26s} {'':22s} {'':22s} "
                          f"(n per arm for 2x @80% power: {need:.0f}; "
                          f"have {n2})")
            print()


if __name__ == "__main__":
    main()
