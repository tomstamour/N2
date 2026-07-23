#!/usr/bin/env python3
"""Rank every variable (and 2-variable combination) by how well it predicts
the target column (--target, default DailyHigh(%); e.g. fromPR_High(%)).

The target is heavy-tailed (median ~6%, max >400%), so predictive power is
measured from two complementary angles and cross-validated so nothing ranks
high just by overfitting:

  Continuous view   how well does the variable order the raw DailyHigh(%) value?
                    -> Spearman rho, Pearson on a signed-log target, mutual
                       information, and an out-of-sample CV Spearman from a tiny
                       gradient-boosted model (catches non-monotone shapes).
  Spike view        how well does it separate "spike" rows (DailyHigh(%) >=
                    --threshold, default 30) from the rest?
                    -> rank AUC and lift@top-decile (spike rate among the top
                       10% of rows ranked by the variable, divided by the base
                       spike rate -- i.e. how much better than random you'd do
                       by acting on the strongest 10% of signals).

Stages
  1. Single variables: numeric columns scored directly; categorical columns
     (Author, Trigger, Exchange, ...) scored via out-of-fold target encoding so
     high-cardinality columns cannot cheat.
  2. All pairs among the best singles (--max-pair-features): a 2-feature
     depth-3 gradient-boosted model, CV-scored, with a `synergy` column =
     pair score minus its best member's solo score. Synergy > 0 means the
     combination adds real out-of-sample information.
  3. Full model on all variables: its CV score is the predictability ceiling
     of this dataset (row `FULL_MODEL`), and per-feature permutation
     importances (`full_model:<var>` rows) surface variables that only matter
     in context.

Output TSV columns
  rank             position after sorting by power_score (best first)
  predictor        variable, "a + b" pair, FULL_MODEL, or full_model:<var>
  kind             numeric | categorical | pair | full_model | full_model_importance
  n_used           rows with data for this predictor
  coverage_pct     n_used as % of all rows
  direction        + / - sign of the association (numeric singles only)
  spearman         Spearman rho vs raw DailyHigh(%)
  spearman_p       its p-value
  pearson_log      Pearson r vs sign(y)*log1p(|y|)
  mutual_info      mutual information with the log target (nats)
  cv_spearman      out-of-sample Spearman of cross-validated model predictions
  auc_spike        AUC for spike/no-spike (rank AUC for singles, CV model AUC otherwise)
  lift_top_decile  spike rate in top 10% of rows by this signal / base spike rate
  synergy          (pairs) cv_spearman minus best member's solo cv_spearman
  perm_importance  (full_model:<var>) permutation importance in the full model
  power_score      mean percentile-rank of cv_spearman, |auc-0.5| and lift;
                   the sort key combining both views

Run:
    /home/tom/venv/bin/python power_predictor.py \
        --input ../PR-stats-...-body_pricefilled_clean.tsv --output power.tsv
"""

import argparse
import itertools
import pathlib
import re
import sys
import warnings

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.ensemble import (HistGradientBoostingClassifier,
                              HistGradientBoostingRegressor)
from sklearn.feature_selection import mutual_info_regression
from sklearn.inspection import permutation_importance
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import KFold, StratifiedKFold, train_test_split

TARGET = "DailyHigh(%)"

# Outcome columns: never predictors regardless of which one is the target —
# they are all measured after the PR and would leak the answer.
OUTCOME_COLS = {
    "DailyHigh(%)", "DailyHigh($)", "fromPR_High(%)", "fromPR_HighTime(m:s)",
}

# Never used as predictors: identifiers and free text.
EXCLUDE = {"ID", "Symbol", "Tickers", "Headline", "body"}
TIMESTAMP_RE = re.compile(r"(Time|Date|At|Created)$")

MAX_CAT_LEVELS = 150
GBM_PARAMS = dict(max_depth=3, max_iter=120, learning_rate=0.1,
                  min_samples_leaf=30, l2_regularization=1.0,
                  early_stopping=False)


def parse_args():
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--input", required=True,
                   help="input TSV (PR-stats format with the target column)")
    p.add_argument("--target", default=TARGET,
                   help=f"target column to predict (default {TARGET})")
    p.add_argument("--output", default=None,
                   help="output TSV path (default: <input stem>_predictive_power.tsv)")
    p.add_argument("--threshold", type=float, default=30.0,
                   help="target >= THRESHOLD defines a 'spike' (default 30)")
    p.add_argument("--cv-folds", type=int, default=5,
                   help="cross-validation folds (default 5)")
    p.add_argument("--seed", type=int, default=42, help="random seed (default 42)")
    p.add_argument("--min-coverage", type=int, default=100,
                   help="skip variables with fewer non-null rows than this (default 100)")
    p.add_argument("--max-pair-features", type=int, default=20,
                   help="test all pairs among the top-N single variables (default 20)")
    return p.parse_args()


def detect_features(df, min_coverage, exclude):
    """Split usable columns into numeric and categorical predictors."""
    numeric, categorical, skipped = {}, {}, []
    for col in df.columns:
        if col in exclude or TIMESTAMP_RE.search(col):
            continue
        s = df[col]
        n_nonnull = s.notna().sum()
        if n_nonnull < min_coverage:
            skipped.append((col, f"only {n_nonnull} non-null rows"))
            continue
        as_num = pd.to_numeric(s, errors="coerce")
        if as_num.notna().sum() >= 0.9 * n_nonnull:
            if as_num.nunique() < 2:
                skipped.append((col, "constant"))
            else:
                numeric[col] = as_num.astype(float)
        elif s.nunique() <= MAX_CAT_LEVELS:
            categorical[col] = s.astype("string")
        else:
            skipped.append((col, f"{s.nunique()} distinct text values"))
    return numeric, categorical, skipped


def signed_log(y):
    return np.sign(y) * np.log1p(np.abs(y))


def safe_spearman(a, b):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        r = stats.spearmanr(a, b)
    return (np.nan, np.nan) if np.isnan(r.statistic) else (r.statistic, r.pvalue)


def lift_at_top_decile(score, spike):
    """Spike rate among the top 10% of rows by score, over the base rate."""
    base = spike.mean()
    if base == 0:
        return np.nan
    k = max(1, int(round(0.10 * len(score))))
    top = np.argsort(-score, kind="stable")[:k]
    return spike[top].mean() / base


def oof_gbm_regression(X, y_log, cat_mask, folds, seed):
    """Out-of-fold predictions of a small gradient-boosted regressor."""
    oof = np.full(len(y_log), np.nan)
    for tr, te in KFold(folds, shuffle=True, random_state=seed).split(X):
        m = HistGradientBoostingRegressor(random_state=seed,
                                          categorical_features=cat_mask,
                                          **GBM_PARAMS)
        m.fit(X.iloc[tr], y_log[tr])
        oof[te] = m.predict(X.iloc[te])
    return oof


def oof_gbm_classifier(X, spike, cat_mask, folds, seed):
    """Out-of-fold spike probabilities; None when spikes are too rare."""
    if spike.sum() < folds or (~spike).sum() < folds:
        return None
    oof = np.full(len(spike), np.nan)
    skf = StratifiedKFold(folds, shuffle=True, random_state=seed)
    for tr, te in skf.split(X, spike):
        m = HistGradientBoostingClassifier(random_state=seed,
                                           categorical_features=cat_mask,
                                           **GBM_PARAMS)
        m.fit(X.iloc[tr], spike[tr])
        oof[te] = m.predict_proba(X.iloc[te])[:, 1]
    return oof


def oof_target_encode(codes, y_vals, folds, seed, smooth=10.0):
    """Out-of-fold smoothed target encoding of a categorical column."""
    oof = np.full(len(y_vals), np.nan)
    for tr, te in KFold(folds, shuffle=True, random_state=seed).split(codes):
        gm = y_vals[tr].mean()
        agg = pd.DataFrame({"c": codes[tr], "y": y_vals[tr]}).groupby("c")["y"]
        enc = (agg.sum() + gm * smooth) / (agg.count() + smooth)
        oof[te] = pd.Series(codes[te]).map(enc).fillna(gm).to_numpy()
    return oof


def score_numeric_single(name, x, y_raw, y_log, spike, args):
    mask = x.notna().to_numpy()
    xs, yr, yl, sp = (x.to_numpy()[mask], y_raw[mask], y_log[mask], spike[mask])
    rho, p = safe_spearman(xs, yr)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        pearson = stats.pearsonr(xs, yl).statistic if len(xs) > 2 else np.nan
    mi = mutual_info_regression(xs.reshape(-1, 1), yl,
                                random_state=args.seed)[0]
    oriented = xs if (np.isnan(rho) or rho >= 0) else -xs
    auc = lift = np.nan
    if sp.sum() >= 5 and (~sp).sum() >= 5:
        auc = roc_auc_score(sp, oriented)
        lift = lift_at_top_decile(oriented, sp)
    oof = oof_gbm_regression(pd.DataFrame({name: xs}), yl, [False],
                             args.cv_folds, args.seed)
    cv_rho, _ = safe_spearman(oof, yr)
    return dict(predictor=name, kind="numeric", n_used=int(mask.sum()),
                direction="+" if (not np.isnan(rho) and rho >= 0) else "-",
                spearman=rho, spearman_p=p, pearson_log=pearson,
                mutual_info=mi, cv_spearman=cv_rho, auc_spike=auc,
                lift_top_decile=lift)


def score_categorical_single(name, s, y_raw, y_log, spike, args):
    mask = s.notna().to_numpy()
    codes = pd.Categorical(s[mask]).codes
    yr, yl, sp = y_raw[mask], y_log[mask], spike[mask]
    # Kruskal-Wallis across levels with enough rows.
    groups = [yr[codes == c] for c in np.unique(codes)
              if (codes == c).sum() >= 5]
    kw_p = np.nan
    if len(groups) >= 2:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            kw_p = stats.kruskal(*groups).pvalue
    mi = mutual_info_regression(codes.reshape(-1, 1), yl, discrete_features=[True],
                                random_state=args.seed)[0]
    # Out-of-fold target encodings act as the "prediction" of this column.
    enc_reg = oof_target_encode(codes, yl, args.cv_folds, args.seed)
    cv_rho, _ = safe_spearman(enc_reg, yr)
    auc = lift = np.nan
    if sp.sum() >= 5 and (~sp).sum() >= 5:
        enc_clf = oof_target_encode(codes, sp.astype(float),
                                    args.cv_folds, args.seed)
        auc = roc_auc_score(sp, enc_clf)
        lift = lift_at_top_decile(enc_clf, sp)
    return dict(predictor=name, kind="categorical", n_used=int(mask.sum()),
                direction="", spearman=np.nan, spearman_p=kw_p,
                pearson_log=np.nan, mutual_info=mi, cv_spearman=cv_rho,
                auc_spike=auc, lift_top_decile=lift)


def build_matrix(names, numeric, categorical):
    """Model matrix for GBMs: floats as-is, categoricals as float codes."""
    cols, cat_mask = {}, []
    for n in names:
        if n in numeric:
            cols[n] = numeric[n].to_numpy()
            cat_mask.append(False)
        else:
            codes = pd.Categorical(categorical[n]).codes.astype(float)
            codes[codes < 0] = np.nan
            cols[n] = codes
            cat_mask.append(True)
    return pd.DataFrame(cols), cat_mask


def score_model(name, kind, X, cat_mask, mask, y_raw, y_log, spike, args):
    """CV-scored GBM on the rows selected by `mask`."""
    Xs = X.loc[mask].reset_index(drop=True)
    yr, yl, sp = y_raw[mask], y_log[mask], spike[mask]
    oof = oof_gbm_regression(Xs, yl, cat_mask, args.cv_folds, args.seed)
    cv_rho, _ = safe_spearman(oof, yr)
    auc = np.nan
    lift = lift_at_top_decile(oof, sp) if sp.mean() > 0 else np.nan
    proba = oof_gbm_classifier(Xs, sp, cat_mask, args.cv_folds, args.seed)
    if proba is not None:
        auc = roc_auc_score(sp, proba)
    return dict(predictor=name, kind=kind, n_used=int(mask.sum()),
                direction="", spearman=np.nan, spearman_p=np.nan,
                pearson_log=np.nan, mutual_info=np.nan, cv_spearman=cv_rho,
                auc_spike=auc, lift_top_decile=lift)


def provisional_rank(rows):
    """Rank singles before the pair stage, using the same recipe as power_score."""
    d = pd.DataFrame(rows)
    comp = pd.concat([d["cv_spearman"].rank(pct=True),
                      (d["auc_spike"] - 0.5).abs().rank(pct=True),
                      d["lift_top_decile"].rank(pct=True)], axis=1)
    return d.assign(score=comp.mean(axis=1)).sort_values(
        "score", ascending=False)["predictor"].tolist()


def main():
    args = parse_args()
    in_path = pathlib.Path(args.input)
    out_path = pathlib.Path(args.output) if args.output else \
        in_path.with_name(in_path.stem + "_predictive_power.tsv")

    df = pd.read_csv(in_path, sep="\t", low_memory=False)
    target = args.target
    if target not in df.columns:
        sys.exit(f"error: no '{target}' column in {in_path}")
    df = df[pd.to_numeric(df[target], errors="coerce").notna()].reset_index(drop=True)
    y_raw = df[target].astype(float).to_numpy()
    y_log = signed_log(y_raw)
    spike = y_raw >= args.threshold
    print(f"{len(df)} rows | spike = {target} >= {args.threshold:g} "
          f"-> {spike.sum()} spikes ({spike.mean():.1%})")

    exclude = EXCLUDE | OUTCOME_COLS | {target}
    numeric, categorical, skipped = detect_features(df, args.min_coverage, exclude)
    for col, why in skipped:
        print(f"  skipping {col}: {why}")
    print(f"stage 1: {len(numeric)} numeric + {len(categorical)} categorical singles")

    rows = []
    for name, x in numeric.items():
        rows.append(score_numeric_single(name, x, y_raw, y_log, spike, args))
    for name, s in categorical.items():
        rows.append(score_categorical_single(name, s, y_raw, y_log, spike, args))
    solo_cv = {r["predictor"]: r["cv_spearman"] for r in rows}

    top = provisional_rank(rows)[:args.max_pair_features]
    pairs = list(itertools.combinations(top, 2))
    print(f"stage 2: {len(pairs)} pairs among top {len(top)} singles")
    for i, (a, b) in enumerate(pairs, 1):
        X, cat_mask = build_matrix([a, b], numeric, categorical)
        mask = X.notna().any(axis=1).to_numpy()
        r = score_model(f"{a} + {b}", "pair", X, cat_mask, mask,
                        y_raw, y_log, spike, args)
        best_solo = np.nanmax([solo_cv.get(a, np.nan), solo_cv.get(b, np.nan)])
        r["synergy"] = r["cv_spearman"] - best_solo
        rows.append(r)
        if i % 50 == 0:
            print(f"  ... {i}/{len(pairs)} pairs")

    print("stage 3: full model on all variables + permutation importance")
    all_feats = list(numeric) + list(categorical)
    X, cat_mask = build_matrix(all_feats, numeric, categorical)
    full_mask = np.ones(len(df), dtype=bool)
    rows.append(score_model("FULL_MODEL", "full_model", X, cat_mask,
                            full_mask, y_raw, y_log, spike, args))

    Xtr, Xte, ytr, yte, yrtr, yrte = train_test_split(
        X, y_log, y_raw, test_size=0.25, random_state=args.seed)
    full = HistGradientBoostingRegressor(random_state=args.seed,
                                         categorical_features=cat_mask,
                                         **GBM_PARAMS).fit(Xtr, ytr)

    def spearman_scorer(est, Xv, yv):
        rho, _ = safe_spearman(est.predict(Xv), yv)
        return 0.0 if np.isnan(rho) else rho

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        imp = permutation_importance(full, Xte, yrte, scoring=spearman_scorer,
                                     n_repeats=8, random_state=args.seed)
    for feat, val in zip(all_feats, imp.importances_mean):
        rows.append(dict(predictor=f"full_model:{feat}",
                         kind="full_model_importance",
                         n_used=len(df), perm_importance=val))

    out = pd.DataFrame(rows)
    for col in ("synergy", "perm_importance"):
        if col not in out.columns:
            out[col] = np.nan
    out["coverage_pct"] = 100.0 * out["n_used"] / len(df)
    components = pd.concat([out["cv_spearman"].rank(pct=True),
                            (out["auc_spike"] - 0.5).abs().rank(pct=True),
                            out["lift_top_decile"].rank(pct=True)], axis=1)
    out["power_score"] = components.mean(axis=1)
    out = out.sort_values(["power_score", "perm_importance"],
                          ascending=False, na_position="last").reset_index(drop=True)
    out.insert(0, "rank", np.arange(1, len(out) + 1))
    cols = ["rank", "predictor", "kind", "n_used", "coverage_pct", "direction",
            "spearman", "spearman_p", "pearson_log", "mutual_info",
            "cv_spearman", "auc_spike", "lift_top_decile", "synergy",
            "perm_importance", "power_score"]
    out[cols].to_csv(out_path, sep="\t", index=False, float_format="%.4f")
    print(f"\nwrote {len(out)} rows -> {out_path}\n")

    show = out[out["kind"] != "full_model_importance"].head(10)
    print("top 10 predictors (see TSV for full table):")
    print(show[["rank", "predictor", "kind", "cv_spearman", "auc_spike",
                "lift_top_decile", "power_score"]]
          .to_string(index=False, float_format=lambda v: f"{v:.3f}"))


if __name__ == "__main__":
    main()
