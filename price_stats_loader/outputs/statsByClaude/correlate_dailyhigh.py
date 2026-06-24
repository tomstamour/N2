#!/usr/bin/env python3
"""Correlate chosen columns against the continuous DailyHigh(%) target.

Unlike analyze_dailyhigh_predictors.py / analyze_all_columns_dailyhigh.py, this
does NOT binarize the target. It reports, for each user-chosen column, a set of
association measures against raw DailyHigh(%):

  * Spearman rho (+p)   — rank correlation; primary, robust to right-skew.
  * Kendall tau  (+p)   — rank correlation; tie-robust.
  * Pearson r    (+p)   — vs a signed-log target, sign(t)*log1p(|t|); the log
                          tames the ~750% right tail so this linear metric is
                          meaningful ("linear on the log scale?"). Signed-log is
                          used (not plain log1p) because DailyHigh(%) has a few
                          small negative values. Rank metrics are invariant to
                          this monotonic transform, so only Pearson uses it.
  * Mutual information  — histogram estimate on the continuous target (quantile
                          bins); catches non-linear dependence. No threshold.

Pure numpy / pandas / scipy — no sklearn (matches the sibling scripts).

Usage:
    python correlate_dailyhigh.py --input <tsv> \
        --columns colA colB ... --output <name>[.tsv|.csv]
"""
import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr, kendalltau, pearsonr

TARGET = "DailyHigh(%)"


def load_data(tsv_path):
    """Read TSV as str, coerce the target to numeric, drop NaN-target rows."""
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    if TARGET not in df.columns:
        sys.exit(f"Target column {TARGET!r} not found in {tsv_path}")
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df.dropna(subset=[TARGET])
    return df


def mutual_info_bits(x, y, bins):
    """Histogram MI between two continuous arrays, in bits.

    Both vectors are discretized into `bins` quantile bins (equal-frequency),
    so the estimate is reasonably stable to the right-skew. Returns
    (mi_bits, normalized_mi) where normalized_mi = MI / min(H(x), H(y)) in
    [0, 1]. Histogram MI is bin-count sensitive — quantile binning keeps it
    comparable across columns at a fixed --mi-bins.
    """
    # Equal-frequency bins; duplicates="drop" collapses ties (e.g. many zeros).
    try:
        xb = pd.qcut(x, bins, labels=False, duplicates="drop")
        yb = pd.qcut(y, bins, labels=False, duplicates="drop")
    except (ValueError, IndexError):
        return float("nan"), float("nan")
    if xb is None or yb is None:
        return float("nan"), float("nan")
    nx = pd.Series(xb).nunique()
    ny = pd.Series(yb).nunique()
    if nx < 2 or ny < 2:
        return 0.0, 0.0

    joint = pd.crosstab(xb, yb).values.astype(float)
    total = joint.sum()
    pxy = joint / total
    px = pxy.sum(axis=1, keepdims=True)
    py = pxy.sum(axis=0, keepdims=True)

    nz = pxy > 0
    mi = float(np.sum(pxy[nz] * np.log2(pxy[nz] / (px @ py)[nz])))

    hx = float(-np.sum(px[px > 0] * np.log2(px[px > 0])))
    hy = float(-np.sum(py[py > 0] * np.log2(py[py > 0])))
    denom = min(hx, hy)
    nmi = mi / denom if denom > 0 else 0.0
    return max(mi, 0.0), max(min(nmi, 1.0), 0.0)


def score_column(df, col, log_target, mi_bins):
    """Return a result dict for one column, or None if it can't be scored."""
    x_raw = pd.to_numeric(df[col], errors="coerce")
    if x_raw.notna().sum() == 0:
        print(f"  [skip] {col!r}: not numeric / all-NaN — skipped.",
              file=sys.stderr)
        return None

    # Pairwise-drop NaNs across feature, raw target, and log target together.
    frame = pd.DataFrame({
        "x": x_raw,
        "y": df[TARGET].values,
        "ylog": log_target,
    }).dropna()
    n = len(frame)
    if n < 3:
        print(f"  [skip] {col!r}: only {n} usable rows — skipped.",
              file=sys.stderr)
        return None
    if frame["x"].nunique() < 2:
        print(f"  [skip] {col!r}: constant after dropping NaNs — skipped.",
              file=sys.stderr)
        return None

    x, y, ylog = frame["x"].values, frame["y"].values, frame["ylog"].values

    sp = spearmanr(x, y)
    kt = kendalltau(x, y)
    pr = pearsonr(x, ylog)
    mi, nmi = mutual_info_bits(x, y, mi_bins)

    return {
        "column": col,
        "n": n,
        "spearman_rho": float(sp.correlation),
        "spearman_p": float(sp.pvalue),
        "kendall_tau": float(kt.correlation),
        "kendall_p": float(kt.pvalue),
        "pearson_log_r": float(pr[0]),
        "pearson_log_p": float(pr[1]),
        "mutual_info_bits": mi,
        "norm_mutual_info": nmi,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Correlate chosen columns against continuous DailyHigh(%).")
    ap.add_argument("--input", required=True, help="Path to the PR-stats TSV.")
    ap.add_argument("--columns", nargs="+", required=True,
                    help="Columns to correlate against DailyHigh(%).")
    ap.add_argument("--output", required=True,
                    help="Output table path; .csv => comma-separated, else TSV.")
    ap.add_argument("--mi-bins", type=int, default=8,
                    help="Quantile bins per variable for mutual information.")
    args = ap.parse_args()

    in_path = pathlib.Path(args.input)
    if not in_path.exists():
        sys.exit(f"File not found: {in_path}")

    df = load_data(in_path)
    # Signed log: monotonic, defined for the small negative DailyHigh(%) values,
    # and still compresses the heavy ~750% right tail for the Pearson metric.
    t = df[TARGET].values
    log_target = np.sign(t) * np.log1p(np.abs(t))
    print(f"Loaded {len(df)} rows with a numeric {TARGET}.", file=sys.stderr)

    rows = []
    for col in args.columns:
        if col not in df.columns:
            print(f"  [skip] {col!r}: not a column in the input — skipped.",
                  file=sys.stderr)
            continue
        r = score_column(df, col, log_target, args.mi_bins)
        if r is not None:
            rows.append(r)

    if not rows:
        sys.exit("No scorable columns — nothing written.")

    out = pd.DataFrame(rows)
    out = out.reindex(out["spearman_rho"].abs().sort_values(ascending=False).index)
    out = out.reset_index(drop=True)

    out_path = pathlib.Path(args.output)
    sep = "," if out_path.suffix.lower() == ".csv" else "\t"
    out.to_csv(out_path, sep=sep, index=False, float_format="%.6g")

    # Echo to stdout for quick reading (tab-aligned regardless of file format).
    print(out.to_string(index=False))
    print(f"\nWrote {len(out)} rows -> {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
