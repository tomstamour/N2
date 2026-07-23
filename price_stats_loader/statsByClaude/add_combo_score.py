#!/usr/bin/env python3
"""Append a combined 2-feature logistic score column to a PR-stats TSV.

Fits a logistic regression of (target >= --threshold) on --features and writes
the input TSV back with ONE new probability column:

  - rows with a target value get an OUT-OF-FOLD probability (each row is scored
    by a fold model that never trained on it, so the column is honest for
    in-corpus backtests);
  - rows with the features but no target value are scored by the full-data fit;
  - rows missing a feature get an empty value.

The full-data fit (means/stds/weights) is saved to JSON so future TSVs can be
scored with the exact same model via --apply-model — that is the downstream /
walk-forward path.

Fit on this corpus:
    /home/tom/venv/bin/python statsByClaude/add_combo_score.py \
        --input PR-stats-...-scoredFromPRmodel.tsv

Score a new TSV with the saved model:
    /home/tom/venv/bin/python statsByClaude/add_combo_score.py \
        --input <new>.tsv --apply-model PR-stats-..._comboScore_model.json
"""

import argparse
import json
import pathlib

import numpy as np
import pandas as pd

from analyze_dailyhigh_predictors import auc_score, fit_logistic, predict_proba

TARGET = "fromPR_High(%)"
FEATURES = "model_proba,kw_mult_nb"


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True, type=pathlib.Path)
    p.add_argument("--target", default=TARGET,
                   help=f"target column (default {TARGET})")
    p.add_argument("--features", default=FEATURES,
                   help=f"comma-separated feature columns (default {FEATURES})")
    p.add_argument("--threshold", type=float, default=15.0,
                   help="target >= THRESHOLD is the positive class (default 15)")
    p.add_argument("--folds", type=int, default=5)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--column-name", default="combo_proba",
                   help="name of the appended column (default combo_proba)")
    p.add_argument("--output", type=pathlib.Path, default=None,
                   help="output TSV (default <input stem>_comboScore.tsv)")
    p.add_argument("--model-out", type=pathlib.Path, default=None,
                   help="where to save the fitted model JSON "
                        "(default <input stem>_comboScore_model.json)")
    p.add_argument("--apply-model", type=pathlib.Path, default=None,
                   help="score with a previously saved model JSON instead of fitting")
    return p.parse_args()


def standardized_proba(X, mu, sd, w):
    return predict_proba((X - mu) / sd, w)


def fit_full(X, y):
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    w = fit_logistic((X - mu) / sd, y)
    return mu, sd, w


def oof_proba(X, y, folds, seed):
    """Out-of-fold probabilities, same recipe as cv_auc() in the analyzer."""
    n = len(y)
    folds = min(folds, n)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    oof = np.empty(n)
    for k in range(folds):
        test_idx = idx[k::folds]
        train_idx = np.setdiff1d(idx, test_idx, assume_unique=False)
        mu = X[train_idx].mean(axis=0)
        sd = X[train_idx].std(axis=0)
        sd[sd == 0] = 1.0
        w = fit_logistic((X[train_idx] - mu) / sd, y[train_idx])
        oof[test_idx] = standardized_proba(X[test_idx], mu, sd, w)
    return oof


def main():
    args = parse_args()
    feats = [f.strip() for f in args.features.split(",") if f.strip()]
    out_path = args.output or args.input.with_name(
        args.input.stem + "_comboScore.tsv")
    model_path = args.model_out or args.input.with_name(
        args.input.stem + "_comboScore_model.json")

    # dtype=str keeps every existing column byte-identical on write-back.
    df = pd.read_csv(args.input, sep="\t", dtype=str)

    if args.apply_model:
        model = json.loads(args.apply_model.read_text())
        feats = model["features"]
        col_name = args.column_name if args.column_name != "combo_proba" \
            else model["column_name"]
    else:
        col_name = args.column_name
    missing = [f for f in feats if f not in df.columns]
    if missing:
        raise SystemExit(f"error: feature column(s) not in TSV: {missing}")

    X_all = df[feats].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    have_feats = ~np.isnan(X_all).any(axis=1)
    scores = np.full(len(df), np.nan)

    if args.apply_model:
        mu = np.array(model["mu"])
        sd = np.array(model["sd"])
        w = np.array(model["weights"])
        scores[have_feats] = standardized_proba(X_all[have_feats], mu, sd, w)
        print(f"applied model {args.apply_model.name} "
              f"({' + '.join(feats)}, trained n={model['n_train']})")
    else:
        if args.target not in df.columns:
            raise SystemExit(f"error: no '{args.target}' column in {args.input}")
        t = pd.to_numeric(df[args.target], errors="coerce").to_numpy(float)
        labeled = have_feats & ~np.isnan(t)
        y = (t[labeled] >= args.threshold).astype(float)
        X = X_all[labeled]
        print(f"{len(df)} rows | fitting on {labeled.sum()} labeled rows | "
              f"positives ({args.target} >= {args.threshold:g}): "
              f"{int(y.sum())} ({y.mean():.1%})")

        scores[labeled] = oof_proba(X, y, args.folds, args.seed)
        mu, sd, w = fit_full(X, y)
        unlabeled = have_feats & np.isnan(t)
        scores[unlabeled] = standardized_proba(X_all[unlabeled], mu, sd, w)

        auc = auc_score(scores[labeled], y)
        rho = pd.Series(scores[labeled]).corr(pd.Series(t[labeled]),
                                              method="spearman")
        print(f"OOF AUC = {auc:.3f} | OOF Spearman vs raw target = {rho:.3f}")
        print(f"unlabeled rows scored with full fit: {int(unlabeled.sum())}")

        model_path.write_text(json.dumps({
            "features": feats, "column_name": col_name,
            "target": args.target, "threshold": args.threshold,
            "mu": mu.tolist(), "sd": sd.tolist(), "weights": w.tolist(),
            "n_train": int(labeled.sum()),
            "note": "score = sigmoid(w0 + sum(w[i]*(x[i]-mu[i])/sd[i])); "
                    "weights[0] is the intercept",
        }, indent=2))
        print(f"model saved -> {model_path}")

    df[col_name] = ["" if np.isnan(s) else f"{s:.6f}" for s in scores]
    df.to_csv(out_path, sep="\t", index=False)
    print(f"wrote {len(df)} rows (+ column '{col_name}') -> {out_path}")


if __name__ == "__main__":
    main()
