#!/usr/bin/env python3
"""Find the best predictors of DailyHigh(%) in a PR-stats TSV.

The raw DailyHigh(%) column is heavily contaminated (null rows, data-error
outliers in the thousands of percent, and duplicated press releases). Any
correlation computed before cleaning is misleading, so this script:

  1. Loads the TSV and coerces the numeric columns.
  2. Cleans the target: drop NaN rows, drop/cap extreme outliers, and
     deduplicate rows that share (Symbol, Headline, ArrivalDate). The cleaned
     frame is written next to the input as ``<stem>_clean.tsv``.
  3. Ranks every numeric feature by Pearson + Spearman correlation against
     log1p(DailyHigh(%)) (the target is right-skewed, so the log lifts the
     signal), and prints grouped medians for the useful categoricals.
  4. Fits a gradient-boosted regressor on the always-present features and
     reports cross-validated R^2 plus permutation feature importances -- this
     captures the low-float x sentiment interaction a single correlation cannot.

Run:
    /home/tom/venv/bin/python outputs/statsByClaude/dailyhigh_predictor.py \
        --input outputs/PR-stats-...-RBscore_body.tsv
"""

import argparse
import pathlib

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance
from sklearn.model_selection import cross_val_score

TARGET_COL = "DailyHigh(%)"

# Columns coerced to numeric for correlation/modeling.
NUMERIC_COLS = [
    "Float", "positive", "negative", "neutral", "sentiment_score",
    "nocoref_neutral_filter", "nocoref_confidence_weighted", "nocoref_net_score",
    "nocoref_top_k", "nocoref_positional", "nocoref_recommended", "Headline-RBscore",
    "Trades/sec", "body_sentiment", "body_duration_ms", "LastDailyClose",
    "itiBaseline", "tradeSizeBaseline", "MktCap",
    "kw_add_first", "kw_add_log", "kw_add_lin", "kw_mult_nb", "kw_mult_nb_log",
    "n_kw_hits", "model_proba",
    "kw_add_first_pctl", "kw_add_log_pctl", "kw_add_lin_pctl",
    "kw_mult_nb_pctl", "kw_mult_nb_log_pctl", "model_proba_pctl",
    "kw_add_first_hl", "kw_add_log_hl", "kw_add_lin_hl", "kw_mult_nb_hl",
    "kw_mult_nb_log_hl", "n_kw_hits_hl", "model_proba_hl",
    "kw_add_first_pctl_hl", "kw_add_log_pctl_hl", "kw_add_lin_pctl_hl",
    "kw_mult_nb_pctl_hl", "kw_mult_nb_log_pctl_hl", "model_proba_pctl_hl",
]

# Always-present signals used for the model (see NaN-coverage analysis).
MODEL_FEATURES = [
    "Float", "LastDailyClose", "positive", "negative", "neutral",
    "sentiment_score", "nocoref_net_score", "nocoref_positional",
    "nocoref_confidence_weighted", "Headline-RBscore",
    "model_proba", "kw_add_lin", "kw_mult_nb_log", "n_kw_hits",
    "model_proba_hl",
]

CATEGORICAL_COLS = ["label", "Trigger", "Exchange", "Author", "Sentiment",
                    "ClerkSentiment", "boilerplate_category"]

DEDUP_KEYS = ["Symbol", "Headline", "ArrivalDate"]


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", required=True, type=pathlib.Path,
                   help="Path to the PR-stats TSV (must contain the target column).")
    p.add_argument("--target", default=TARGET_COL,
                   help=f"Target column to predict. Default {TARGET_COL}.")
    p.add_argument("--outlier-cap", type=float, default=500.0,
                   help="Drop rows with DailyHigh(%%) above this (data errors). "
                        "Default 500.")
    p.add_argument("--min-group", type=int, default=15,
                   help="Minimum group size to show a categorical level. "
                        "Default 15.")
    return p.parse_args(argv)


def load_and_clean(args):
    df = pd.read_csv(args.input, sep="\t", dtype=str)
    n_total = len(df)
    df[TARGET_COL] = pd.to_numeric(df[TARGET_COL], errors="coerce")
    for c in NUMERIC_COLS:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    n_null = df[TARGET_COL].isna().sum()
    df = df[df[TARGET_COL].notna()].copy()

    n_before_cap = len(df)
    df = df[df[TARGET_COL] <= args.outlier_cap].copy()
    n_outliers = n_before_cap - len(df)

    n_before_dedup = len(df)
    have_keys = [k for k in DEDUP_KEYS if k in df]
    df = df.drop_duplicates(subset=have_keys, keep="first").copy()
    n_dups = n_before_dedup - len(df)

    stats = dict(n_total=n_total, n_null=int(n_null), n_outliers=int(n_outliers),
                 n_dups=int(n_dups), n_kept=len(df))
    return df, stats


def print_correlations(df):
    """Rank numeric features by correlation vs log1p(target)."""
    logt = np.log1p(df[TARGET_COL].clip(lower=0))
    rows = []
    for c in NUMERIC_COLS:
        if c not in df:
            continue
        s = pd.concat([df[c], logt], axis=1).dropna()
        if len(s) < 30:
            continue
        pear = s[c].corr(s[TARGET_COL])
        spear = s[c].corr(s[TARGET_COL], method="spearman")
        rows.append((c, len(s), pear, spear))
    rows.sort(key=lambda r: -abs(r[3]))

    print(f"\n# Correlation vs log1p({TARGET_COL})  (ranked by |Spearman|)")
    print(f"{'feature':30s}{'n':>7}{'pearson':>10}{'spearman':>10}")
    print("-" * 57)
    for c, n, p, sp in rows:
        print(f"{c:30s}{n:7d}{p:10.3f}{sp:10.3f}")


def print_categoricals(df, min_group):
    print(f"\n# Median {TARGET_COL} by categorical group (min group {min_group})")
    for c in CATEGORICAL_COLS:
        if c not in df:
            continue
        g = df.groupby(df[c])[TARGET_COL].agg(["count", "mean", "median"])
        g = g[g["count"] >= min_group].sort_values("median", ascending=False)
        if g.empty:
            continue
        print(f"\n[{c}]")
        print(g.round(2).head(10).to_string())


def fit_model(df):
    feats = [c for c in MODEL_FEATURES if c in df]
    data = df[feats + [TARGET_COL]].copy()
    # HistGradientBoosting handles NaNs natively, so no imputation needed.
    y = np.log1p(data[TARGET_COL].clip(lower=0))
    X = data[feats]

    model = HistGradientBoostingRegressor(
        max_depth=4, learning_rate=0.05, max_iter=400,
        l2_regularization=1.0, random_state=0,
    )
    r2 = cross_val_score(model, X, y, cv=5, scoring="r2")
    print(f"\n# Gradient-boosted model on log1p({TARGET_COL})")
    print(f"features: {', '.join(feats)}")
    print(f"5-fold CV R^2: mean={r2.mean():.3f}  folds={np.round(r2, 3).tolist()}")

    model.fit(X, y)
    perm = permutation_importance(model, X, y, n_repeats=10, random_state=0)
    order = np.argsort(perm.importances_mean)[::-1]
    print(f"\n{'feature':30s}{'perm_importance':>16}{'std':>10}")
    print("-" * 56)
    for i in order:
        print(f"{feats[i]:30s}{perm.importances_mean[i]:16.4f}"
              f"{perm.importances_std[i]:10.4f}")


def main(argv=None):
    global TARGET_COL
    args = parse_args(argv)
    TARGET_COL = args.target
    df, s = load_and_clean(args)

    print(f"# input: {args.input}")
    print(f"# rows: {s['n_total']} total -> dropped {s['n_null']} null-target, "
          f"{s['n_outliers']} outliers (>{args.outlier_cap:g}%), "
          f"{s['n_dups']} duplicate PRs -> {s['n_kept']} kept")
    print(f"# {TARGET_COL} on kept rows: median="
          f"{df[TARGET_COL].median():.2f}  mean={df[TARGET_COL].mean():.2f}  "
          f"max={df[TARGET_COL].max():.2f}")

    print_correlations(df)
    print_categoricals(df, args.min_group)
    fit_model(df)

    out_path = args.input.parent / f"{args.input.stem}_clean.tsv"
    df.to_csv(out_path, sep="\t", index=False)
    print(f"\nwrote cleaned dataset to: {out_path}")


if __name__ == "__main__":
    main()
