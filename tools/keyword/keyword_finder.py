#!/usr/bin/env python3
"""
keyword_finder.py

Supervised text-regression pipeline that ranks unigrams/bigrams in a text field
by their association with a numeric target.

Workflow (see keywords_finder_plan.txt):
    text column -> vectorizer -> ElasticNetCV -> cross-validation
    -> coefficient extraction -> ranked keyword-impact files

For each vectorizer variant (TF-IDF and binary Count) it produces:
    - <variant>_keyword_impact_all_terms.csv
    - <variant>_keyword_impact_positive_terms.csv
    - <variant>_keyword_impact_negative_terms.csv
    - <variant>_fitted_pipeline.joblib
plus a shared model_performance_summary.csv.

Run with the project venv, e.g.:
    /home/tom/venv/bin/python keyword_finder.py
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import re

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_extraction.text import CountVectorizer, TfidfVectorizer
from sklearn.feature_selection import f_regression, r_regression
from sklearn.linear_model import (ElasticNet, ElasticNetCV, LogisticRegression,
                                  LogisticRegressionCV)
from sklearn.model_selection import (KFold, StratifiedKFold, cross_validate,
                                     train_test_split)
from sklearn.pipeline import Pipeline

DEFAULT_INPUT = "inputs/PR-stats-2026-05-03to06-19-filtered-50float-12high_RBscore_body_clean.tsv"
L1_RATIO_GRID = [0.1, 0.3, 0.5, 0.7, 0.9, 0.95]
# smaller grids for the (slower) saga logistic solver
CLF_L1_RATIO_GRID = [0.1, 0.5, 0.9]
CLF_C_GRID = [0.01, 0.1, 1.0]


# --------------------------------------------------------------------------- #
# Boilerplate detection (law-firm notices / conference & earnings-call PRs)
# --------------------------------------------------------------------------- #
LAWFIRM_RE = re.compile(
    r"(?:rosen law|pomerantz|bragar eagel|glancy prongay|robbins geller|"
    r"kessler topaz|bernstein liebhard|faruqi|levi & korsinsky|kahn swick|"
    r"schall law|hagens berman|scott\+scott|gross law|portnoy law|"
    r"howard g\.? smith|wolf haldenstein|block & leviton|labaton|"
    r"class action|class period|lead plaintiff|shareholder rights|"
    r"securities (?:fraud|litigation)|investors who (?:purchased|lost)|"
    r"(?:encourages|notifies|reminds) (?:investors|shareholders)|"
    r"law firm reminds|deadline to|investor alert|shareholder alert|"
    r"on behalf of (?:all )?(?:persons|investors|shareholders|purchasers)|"
    r"recover (?:their )?losses|no cost to you|been sued|lawsuit)")
CONF_RE = re.compile(
    r"(?:will present at|to present at|to participate in|fireside chat|"
    r"investor (?:conference|presentation|day)|corporate presentation|"
    r"will host|to host|webcast|conference call|earnings call|"
    r"non[- ]?deal roadshow|to attend|annual (?:general )?meeting|"
    r"to report .{0,30}financial results|will report .{0,30}results|"
    r"to discuss .{0,30}results|schedule[sd]? .{0,20}conference)")
EARNINGS_RE = re.compile(
    r"(?:reports? .{0,30}financial results|announces? .{0,30}financial results|"
    r"financial results for|(?:first|second|third|fourth) quarter .{0,25}"
    r"(?:results|financial)|(?:full[- ]?year|fiscal .{0,10}year) .{0,20}results|"
    r"quarterly (?:financial )?results|(?:quarter|year|period) ended|"
    r"reports? (?:record )?(?:revenue|results|operating results)|"
    r"provides? (?:a )?(?:business|corporate) update)")
COMPLIANCE_RE = re.compile(
    r"(?:nasdaq listing rule|nyse .{0,15}listing|listing rule 56|"
    r"inducement (?:grant|award)|regains? compliance|regain compliance|"
    r"notice of (?:non[- ]?compliance|delisting|deficiency)|"
    r"listing (?:deficiency|requirement|standard)|minimum bid price|"
    r"cure period|continued listing|granted (?:an )?extension|"
    r"reverse (?:stock )?split)")

_BOILER_RULES = (("lawfirm", LAWFIRM_RE), ("compliance", COMPLIANCE_RE),
                 ("earnings", EARNINGS_RE), ("conference", CONF_RE))


def boilerplate_category(text: str) -> str:
    """Return the first matching boilerplate category, or '' if none.

    Categories: lawfirm | compliance | earnings | conference.
    """
    t = text.lower()
    for name, rx in _BOILER_RULES:
        if rx.search(t):
            return name
    return ""


# tracking URLs (incl. Business Wire cts.* smartlink links) + newswire vendor tokens
_WIRE_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
_WIRE_DOMAIN_RE = re.compile(r"\b[\w-]+\.(?:com|net|org|io|us)\b", re.I)
_WIRE_TOKEN_RE = re.compile(
    r"\b(?:business\s?wire|globe\s?newswire|access\s?newswire|accesswire|"
    r"pr\s?newswire|prnewswire|newsfile(?:\s?corp)?|market\s?wired|newswire|"
    r"smartlink|esheet|anchor|u00[0-9a-f]{2}|utm_[a-z]+|lan\s?en)\b", re.I)
_WS_RE = re.compile(r"\s+")


def strip_wire_boilerplate(text: str) -> str:
    """Remove distributor plumbing so the model can't fingerprint the newswire:
    tracking URLs / smartlink query debris and vendor names."""
    t = _WIRE_URL_RE.sub(" ", text)
    t = _WIRE_DOMAIN_RE.sub(" ", t)
    t = _WIRE_TOKEN_RE.sub(" ", t)
    return _WS_RE.sub(" ", t).strip()


# --------------------------------------------------------------------------- #
# Target transforms
# --------------------------------------------------------------------------- #
def signed_log(y: np.ndarray) -> np.ndarray:
    """sign(y) * log1p(|y|) -- tames heavy skew while keeping sign/zero."""
    return np.sign(y) * np.log1p(np.abs(y))


def inv_signed_log(z: np.ndarray) -> np.ndarray:
    return np.sign(z) * np.expm1(np.abs(z))


def apply_transform(y: np.ndarray, kind: str) -> np.ndarray:
    if kind == "none":
        return y
    if kind == "signed_log":
        return signed_log(y)
    raise ValueError(f"unknown target transform: {kind}")


def invert_transform(z: np.ndarray, kind: str) -> np.ndarray:
    if kind == "none":
        return z
    if kind == "signed_log":
        return inv_signed_log(z)
    raise ValueError(f"unknown target transform: {kind}")


# --------------------------------------------------------------------------- #
# Phase 1 -- data preparation
# --------------------------------------------------------------------------- #
def load_and_prepare(path: Path, text_col: str, target_col: str, id_col: str,
                     drop_duplicates: bool, strip_boilerplate: bool,
                     strip_wire: bool = False, headline_col: str = "Headline"):
    df = pd.read_csv(path, sep="\t", dtype=str)
    for col in (text_col, target_col):
        if col not in df.columns:
            sys.exit(f"ERROR: column '{col}' not found. Available: {list(df.columns)}")

    n0 = len(df)
    df = df.copy()
    df["_text"] = df[text_col].fillna("").astype(str)
    df["_target"] = pd.to_numeric(df[target_col], errors="coerce")
    df["_id"] = df[id_col] if id_col in df.columns else np.arange(n0).astype(str)

    # drop invalid rows
    df = df[df["_text"].str.strip() != ""]
    df = df[df["_target"].notna()]
    n_valid = len(df)

    if drop_duplicates:
        df = df.drop_duplicates(subset=["_text", "_target"])
    n_dedup = len(df)

    print("=== Phase 1: data preparation ===")
    print(f"  loaded rows        : {n0}")
    print(f"  after drop invalid : {n_valid}  (removed {n0 - n_valid})")
    print(f"  after drop dupes   : {n_dedup}  (removed {n_valid - n_dedup})")

    if strip_boilerplate:
        # classify on headline + body so short-form notices are caught too
        head = df[headline_col].fillna("") if headline_col in df.columns else ""
        detect_txt = (head + " \n " + df["_text"]) if headline_col in df.columns else df["_text"]
        cats = detect_txt.map(boilerplate_category)
        counts = {c: int((cats == c).sum())
                  for c in ("lawfirm", "compliance", "earnings", "conference")}
        df = df[cats == ""]
        print(f"  strip boilerplate  : removed {sum(counts.values())} "
              + ", ".join(f"{k}={v}" for k, v in counts.items()))
    n_final = len(df)
    print(f"  final rows         : {n_final}")

    if strip_wire:
        before = df["_text"].str.split().str.len()
        df["_text"] = df["_text"].map(strip_wire_boilerplate)
        after = df["_text"].str.split().str.len()
        print(f"  strip wire tokens  : median words {int(before.median())} "
              f"-> {int(after.median())} (removed newswire URLs/vendor tokens)")

    y = df["_target"].to_numpy(dtype=float)
    print(f"  target '{target_col}' distribution:")
    print(f"    n={len(y)} min={y.min():.3f} max={y.max():.3f} "
          f"mean={y.mean():.3f} median={np.median(y):.3f}")
    print(f"    std={y.std():.3f} skew={pd.Series(y).skew():.3f} "
          f"negatives={(y < 0).sum()} zeros={(y == 0).sum()}")
    return df.reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Vectorizers
# --------------------------------------------------------------------------- #
def make_vectorizer(variant: str, min_df: int, max_df: float,
                    max_features, stop_words):
    common = dict(
        lowercase=True,
        ngram_range=(1, 2),
        min_df=min_df,
        max_df=max_df,
        max_features=max_features,
        stop_words=stop_words,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z]+\b",  # words >=2 letters, drop pure numbers
    )
    if variant == "tfidf":
        return TfidfVectorizer(sublinear_tf=True, **common)
    if variant == "binary":
        return CountVectorizer(binary=True, **common)
    raise ValueError(variant)


def build_enetcv(cv: int, seed: int, max_iter: int):
    return ElasticNetCV(
        l1_ratio=L1_RATIO_GRID,
        alphas=50,  # sklearn>=1.9: int => number of alphas along the path
        cv=cv,
        random_state=seed,
        max_iter=max_iter,
        tol=1e-3,
        n_jobs=-1,
    )


# --------------------------------------------------------------------------- #
# Metrics helpers
# --------------------------------------------------------------------------- #
def regression_metrics(y_true, y_pred):
    from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
    return dict(
        r2=r2_score(y_true, y_pred),
        mae=mean_absolute_error(y_true, y_pred),
        rmse=float(np.sqrt(mean_squared_error(y_true, y_pred))),
    )


# --------------------------------------------------------------------------- #
# Phase 6 -- coefficient stability across folds
# --------------------------------------------------------------------------- #
def stability_analysis(texts, y, variant, vec_kwargs, alpha, l1_ratio,
                       n_splits, n_repeats, seed, max_iter):
    """Refit vectorizer + ElasticNet(alpha, l1_ratio) on each fold; align coefs
    by term name and summarise mean / std / sign-stability / selection-freq."""
    from collections import defaultdict
    coef_lists = defaultdict(list)   # term -> [coef per fold it appears in]
    present = defaultdict(int)       # term -> #folds vocab contains it
    n_folds_total = 0

    rng = np.random.RandomState(seed)
    for rep in range(n_repeats):
        kf = KFold(n_splits=n_splits, shuffle=True,
                   random_state=rng.randint(0, 1_000_000))
        for train_idx, _ in kf.split(texts):
            n_folds_total += 1
            vec = make_vectorizer(variant, **vec_kwargs)
            Xtr = vec.fit_transform(texts[train_idx])
            model = ElasticNet(alpha=alpha, l1_ratio=l1_ratio,
                               random_state=seed, max_iter=max_iter, tol=1e-3)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                warnings.simplefilter("ignore", FutureWarning)
                model.fit(Xtr, y[train_idx])
            for term, c in zip(vec.get_feature_names_out(), model.coef_):
                coef_lists[term].append(c)
                present[term] += 1

    rows = {}
    for term, coefs in coef_lists.items():
        arr = np.asarray(coefs, dtype=float)
        nonzero = arr[arr != 0]
        if nonzero.size:
            majority_sign = np.sign(np.sum(np.sign(nonzero)))
            sign_stability = float(np.mean(np.sign(nonzero) == majority_sign))
        else:
            sign_stability = np.nan
        rows[term] = dict(
            coef_mean_across_folds=float(arr.mean()),
            coef_std_across_folds=float(arr.std()),
            sign_stability=sign_stability,
            selection_frequency=float((arr != 0).sum() / present[term]),
            present_in_folds=present[term],
        )
    print(f"    stability: {n_folds_total} fold-refits, "
          f"{len(rows)} distinct terms observed")
    return pd.DataFrame.from_dict(rows, orient="index")


# --------------------------------------------------------------------------- #
# Per-variant run
# --------------------------------------------------------------------------- #
def run_variant(variant, df, args, outdir):
    print(f"\n=== Variant: {variant} ===")
    texts = df["_text"].to_numpy()
    ids = df["_id"].to_numpy()
    y_raw = df["_target"].to_numpy(dtype=float)
    y = apply_transform(y_raw, args.target_transform)

    vec_kwargs = dict(min_df=args.min_df, max_df=args.max_df,
                      max_features=args.max_features,
                      stop_words=(args.stop_words or None))

    # ---- Phase 2/3: split + fit pipeline ----
    (tr_txt, te_txt, tr_y, te_y, tr_yraw, te_yraw,
     tr_id, te_id) = train_test_split(
        texts, y, y_raw, ids, test_size=args.test_size, random_state=args.seed)

    pipe = Pipeline([
        ("vec", make_vectorizer(variant, **vec_kwargs)),
        ("enet", build_enetcv(args.cv, args.seed, args.max_iter)),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", FutureWarning)
        pipe.fit(tr_txt, tr_y)

    enet = pipe.named_steps["enet"]
    vec = pipe.named_steps["vec"]
    alpha, l1r = float(enet.alpha_), float(enet.l1_ratio_)
    n_features = len(vec.get_feature_names_out())
    print(f"  fitted: {n_features} features | selected alpha={alpha:.5g} "
          f"l1_ratio={l1r}")

    # ---- Phase 4: validation ----
    te_pred = pipe.predict(te_txt)
    m_trans = regression_metrics(te_y, te_pred)
    # original-unit metrics (back-transform)
    m_orig = regression_metrics(te_yraw, invert_transform(te_pred, args.target_transform))
    # baseline
    dummy = DummyRegressor(strategy="mean").fit(tr_txt, tr_y)
    base = regression_metrics(te_y, dummy.predict(te_txt))
    # cross-val on full training set with the SELECTED params (fold stability of score)
    cv_pipe = Pipeline([
        ("vec", make_vectorizer(variant, **vec_kwargs)),
        ("enet", ElasticNet(alpha=alpha, l1_ratio=l1r,
                            random_state=args.seed, max_iter=args.max_iter, tol=1e-3)),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", FutureWarning)
        cvres = cross_validate(cv_pipe, tr_txt, tr_y, cv=args.cv,
                               scoring=("r2", "neg_mean_absolute_error",
                                        "neg_root_mean_squared_error"))
    cv_r2 = cvres["test_r2"]
    print(f"  TEST (transformed space): R2={m_trans['r2']:.4f} "
          f"MAE={m_trans['mae']:.4f} RMSE={m_trans['rmse']:.4f}")
    print(f"  TEST (original % units) : R2={m_orig['r2']:.4f} "
          f"MAE={m_orig['mae']:.4f} RMSE={m_orig['rmse']:.4f}")
    print(f"  BASELINE(mean) test R2  : {base['r2']:.4f} "
          f"MAE={base['mae']:.4f} RMSE={base['rmse']:.4f}")
    print(f"  CV R2 (train, {args.cv}-fold)  : "
          f"mean={cv_r2.mean():.4f} std={cv_r2.std():.4f} folds={np.round(cv_r2,3)}")

    perf = dict(
        variant=variant, n_train=len(tr_txt), n_test=len(te_txt),
        n_features=n_features, target_transform=args.target_transform,
        model_alpha=alpha, model_l1_ratio=l1r,
        test_r2_transformed=m_trans["r2"], test_mae_transformed=m_trans["mae"],
        test_rmse_transformed=m_trans["rmse"],
        test_r2_original=m_orig["r2"], test_mae_original=m_orig["mae"],
        test_rmse_original=m_orig["rmse"],
        baseline_test_r2=base["r2"], baseline_test_mae=base["mae"],
        baseline_test_rmse=base["rmse"],
        cv_r2_mean=float(cv_r2.mean()), cv_r2_std=float(cv_r2.std()),
        cv_mae_mean=float(-cvres["test_neg_mean_absolute_error"].mean()),
        cv_rmse_mean=float(-cvres["test_neg_root_mean_squared_error"].mean()),
    )

    # ---- Phase 5: keyword scoring on training data ----
    Xtr = vec.transform(tr_txt)
    terms = vec.get_feature_names_out()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        uni_r = r_regression(Xtr, tr_y)
        f_scores, p_values = f_regression(Xtr, tr_y)
    doc_freq = np.asarray((Xtr > 0).sum(axis=0)).ravel()
    mean_feat = np.asarray(Xtr.mean(axis=0)).ravel()
    coef = enet.coef_

    kt = pd.DataFrame({
        "term": terms,
        "ngram_type": np.where(pd.Series(terms).str.contains(" "), "bigram", "unigram"),
        "document_frequency": doc_freq,
        "document_frequency_pct": 100.0 * doc_freq / Xtr.shape[0],
        "mean_feature_value": mean_feat,
        "univariate_r": uni_r,
        "univariate_f_score": f_scores,
        "univariate_p_value": p_values,
        "elasticnet_coef": coef,
        "abs_elasticnet_coef": np.abs(coef),
    })
    kt["direction"] = np.select(
        [kt["elasticnet_coef"] > 0, kt["elasticnet_coef"] < 0],
        ["increases_value", "decreases_value"], default="none")
    kt["selected_by_model"] = kt["elasticnet_coef"] != 0
    kt["rank_by_abs_coef"] = kt["abs_elasticnet_coef"].rank(ascending=False, method="min").astype(int)
    kt["rank_by_abs_r"] = kt["univariate_r"].abs().rank(ascending=False, method="min").astype(int)
    kt["model_alpha"] = alpha
    kt["model_l1_ratio"] = l1r

    # ---- Phase 6: stability ----
    print("  running stability refits...")
    stab = stability_analysis(
        tr_txt, tr_y, variant, vec_kwargs, alpha, l1r,
        n_splits=args.stability_splits, n_repeats=args.stability_repeats,
        seed=args.seed, max_iter=args.max_iter)
    kt = kt.merge(stab, left_on="term", right_index=True, how="left")

    # ---- Phase 7: export ----
    kt = kt.sort_values("abs_elasticnet_coef", ascending=False).reset_index(drop=True)
    prefix = outdir / variant
    kt.to_csv(f"{prefix}_keyword_impact_all_terms.csv", index=False)

    pos = kt[kt["elasticnet_coef"] > 0].sort_values("elasticnet_coef", ascending=False)
    neg = kt[kt["elasticnet_coef"] < 0].sort_values("elasticnet_coef", ascending=True)
    pos.to_csv(f"{prefix}_keyword_impact_positive_terms.csv", index=False)
    neg.to_csv(f"{prefix}_keyword_impact_negative_terms.csv", index=False)
    joblib.dump(pipe, f"{prefix}_fitted_pipeline.joblib")

    n_sel = int(kt["selected_by_model"].sum())
    print(f"  selected (non-zero) terms: {n_sel} / {len(kt)}  "
          f"(pos={len(pos)}, neg={len(neg)})")
    print(f"  wrote {prefix}_keyword_impact_*.csv and _fitted_pipeline.joblib")
    if n_sel:
        top = kt[kt["selected_by_model"]].head(8)
        print("  top terms by |coef|:")
        for _, r in top.iterrows():
            print(f"    {r['elasticnet_coef']:+.4f}  df={int(r['document_frequency']):<4d} "
                  f"r={r['univariate_r']:+.3f}  {r['term']}")
    return perf


# --------------------------------------------------------------------------- #
# Classification variant: predict P(target >= threshold) with logistic ElasticNet
# --------------------------------------------------------------------------- #
def clf_stability(texts, y, variant, vec_kwargs, C, l1_ratio,
                  n_splits, n_repeats, seed, max_iter):
    from collections import defaultdict
    coef_lists = defaultdict(list)
    present = defaultdict(int)
    n_folds_total = 0
    rng = np.random.RandomState(seed)
    for _ in range(n_repeats):
        skf = StratifiedKFold(n_splits=n_splits, shuffle=True,
                              random_state=rng.randint(0, 1_000_000))
        for train_idx, _ in skf.split(texts, y):
            n_folds_total += 1
            vec = make_vectorizer(variant, **vec_kwargs)
            Xtr = vec.fit_transform(texts[train_idx])
            model = LogisticRegression(
                penalty="elasticnet", solver="saga", C=C, l1_ratio=l1_ratio,
                class_weight="balanced", max_iter=max_iter, tol=1e-3,
                random_state=seed)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", ConvergenceWarning)
                warnings.simplefilter("ignore", FutureWarning)
                model.fit(Xtr, y[train_idx])
            for term, c in zip(vec.get_feature_names_out(), model.coef_[0]):
                coef_lists[term].append(c)
                present[term] += 1
    rows = {}
    for term, coefs in coef_lists.items():
        arr = np.asarray(coefs, dtype=float)
        nz = arr[arr != 0]
        if nz.size:
            maj = np.sign(np.sum(np.sign(nz)))
            sign_stab = float(np.mean(np.sign(nz) == maj))
        else:
            sign_stab = np.nan
        rows[term] = dict(
            coef_mean_across_folds=float(arr.mean()),
            coef_std_across_folds=float(arr.std()),
            sign_stability=sign_stab,
            selection_frequency=float((arr != 0).sum() / present[term]),
            present_in_folds=present[term])
    print(f"    stability: {n_folds_total} fold-refits, {len(rows)} terms")
    return pd.DataFrame.from_dict(rows, orient="index")


def clf_metrics(y_true, proba, thr=0.5):
    from sklearn.metrics import (accuracy_score, average_precision_score,
                                 precision_recall_fscore_support, roc_auc_score)
    pred = (proba >= thr).astype(int)
    p, r, f, _ = precision_recall_fscore_support(
        y_true, pred, average="binary", zero_division=0)
    return dict(roc_auc=roc_auc_score(y_true, proba),
                avg_precision=average_precision_score(y_true, proba),
                accuracy=accuracy_score(y_true, pred),
                precision=p, recall=r, f1=f)


def run_variant_clf(variant, df, args, outdir):
    print(f"\n=== Variant: {variant} (classification, target >= {args.binarize_threshold}) ===")
    texts = df["_text"].to_numpy()
    y_raw = df["_target"].to_numpy(dtype=float)
    y = (y_raw >= args.binarize_threshold).astype(int)
    pos_rate = y.mean()
    print(f"  positives: {int(y.sum())}/{len(y)} ({100*pos_rate:.1f}%)")

    vec_kwargs = dict(min_df=args.min_df, max_df=args.max_df,
                      max_features=args.max_features,
                      stop_words=(args.stop_words or None))

    tr_txt, te_txt, tr_y, te_y = train_test_split(
        texts, y, test_size=args.test_size, random_state=args.seed, stratify=y)

    pipe = Pipeline([
        ("vec", make_vectorizer(variant, **vec_kwargs)),
        ("clf", LogisticRegressionCV(
            penalty="elasticnet", solver="saga", l1_ratios=CLF_L1_RATIO_GRID,
            Cs=CLF_C_GRID, cv=args.cv, scoring="roc_auc",
            class_weight="balanced", max_iter=args.max_iter, tol=1e-3,
            random_state=args.seed, n_jobs=-1)),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", FutureWarning)
        pipe.fit(tr_txt, tr_y)

    clf = pipe.named_steps["clf"]
    vec = pipe.named_steps["vec"]
    C, l1r = float(clf.C_[0]), float(clf.l1_ratio_[0])
    n_features = len(vec.get_feature_names_out())
    print(f"  fitted: {n_features} features | selected C={C:.5g} l1_ratio={l1r}")

    # ---- validation ----
    te_proba = pipe.predict_proba(te_txt)[:, 1]
    m = clf_metrics(te_y, te_proba)
    base = DummyClassifier(strategy="prior").fit(tr_txt, tr_y)
    base_proba = base.predict_proba(te_txt)[:, 1]
    from sklearn.metrics import average_precision_score
    base_ap = average_precision_score(te_y, base_proba)
    cv_pipe = Pipeline([
        ("vec", make_vectorizer(variant, **vec_kwargs)),
        ("clf", LogisticRegression(
            penalty="elasticnet", solver="saga", C=C, l1_ratio=l1r,
            class_weight="balanced", max_iter=args.max_iter, tol=1e-3,
            random_state=args.seed)),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", FutureWarning)
        cvres = cross_validate(cv_pipe, tr_txt, tr_y, cv=args.cv,
                               scoring=("roc_auc", "average_precision"))
    cv_auc = cvres["test_roc_auc"]
    print(f"  TEST: ROC-AUC={m['roc_auc']:.4f}  AP={m['avg_precision']:.4f}  "
          f"(baseline AP={base_ap:.4f}=pos-rate)")
    print(f"  TEST @0.5: precision={m['precision']:.3f} recall={m['recall']:.3f} "
          f"f1={m['f1']:.3f} acc={m['accuracy']:.3f}")
    print(f"  CV ROC-AUC (train, {args.cv}-fold): mean={cv_auc.mean():.4f} "
          f"std={cv_auc.std():.4f} folds={np.round(cv_auc,3)}")

    perf = dict(
        variant=variant, task="classification",
        binarize_threshold=args.binarize_threshold,
        n_train=len(tr_txt), n_test=len(te_txt), n_features=n_features,
        train_pos_rate=float(tr_y.mean()), test_pos_rate=float(te_y.mean()),
        model_C=C, model_l1_ratio=l1r,
        test_roc_auc=m["roc_auc"], test_avg_precision=m["avg_precision"],
        test_precision=m["precision"], test_recall=m["recall"], test_f1=m["f1"],
        baseline_avg_precision=float(base_ap),
        cv_roc_auc_mean=float(cv_auc.mean()), cv_roc_auc_std=float(cv_auc.std()),
        cv_avg_precision_mean=float(cvres["test_average_precision"].mean()))

    # ---- keyword scoring ----
    Xtr = vec.transform(tr_txt)
    terms = vec.get_feature_names_out()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        uni_r = r_regression(Xtr, tr_y)              # point-biserial with 0/1 target
        f_scores, p_values = f_regression(Xtr, tr_y)
    doc_freq = np.asarray((Xtr > 0).sum(axis=0)).ravel()
    mean_feat = np.asarray(Xtr.mean(axis=0)).ravel()
    coef = clf.coef_[0]

    kt = pd.DataFrame({
        "term": terms,
        "ngram_type": np.where(pd.Series(terms).str.contains(" "), "bigram", "unigram"),
        "document_frequency": doc_freq,
        "document_frequency_pct": 100.0 * doc_freq / Xtr.shape[0],
        "mean_feature_value": mean_feat,
        "univariate_r": uni_r,
        "univariate_f_score": f_scores,
        "univariate_p_value": p_values,
        "logit_coef": coef,
        "abs_logit_coef": np.abs(coef),
        "odds_ratio": np.exp(coef),
    })
    kt["direction"] = np.select(
        [kt["logit_coef"] > 0, kt["logit_coef"] < 0],
        ["increases_prob", "decreases_prob"], default="none")
    kt["selected_by_model"] = kt["logit_coef"] != 0
    kt["rank_by_abs_coef"] = kt["abs_logit_coef"].rank(ascending=False, method="min").astype(int)
    kt["rank_by_abs_r"] = kt["univariate_r"].abs().rank(ascending=False, method="min").astype(int)
    kt["model_C"] = C
    kt["model_l1_ratio"] = l1r

    print("  running stability refits...")
    stab = clf_stability(tr_txt, tr_y, variant, vec_kwargs, C, l1r,
                         n_splits=args.stability_splits,
                         n_repeats=args.stability_repeats,
                         seed=args.seed, max_iter=args.max_iter)
    kt = kt.merge(stab, left_on="term", right_index=True, how="left")

    kt = kt.sort_values("abs_logit_coef", ascending=False).reset_index(drop=True)
    prefix = outdir / variant
    kt.to_csv(f"{prefix}_keyword_impact_all_terms.csv", index=False)
    pos = kt[kt["logit_coef"] > 0].sort_values("logit_coef", ascending=False)
    neg = kt[kt["logit_coef"] < 0].sort_values("logit_coef", ascending=True)
    pos.to_csv(f"{prefix}_keyword_impact_positive_terms.csv", index=False)
    neg.to_csv(f"{prefix}_keyword_impact_negative_terms.csv", index=False)
    joblib.dump(pipe, f"{prefix}_fitted_pipeline.joblib")

    n_sel = int(kt["selected_by_model"].sum())
    print(f"  selected (non-zero) terms: {n_sel} / {len(kt)} "
          f"(pos={len(pos)}, neg={len(neg)})")
    print(f"  wrote {prefix}_keyword_impact_*.csv and _fitted_pipeline.joblib")
    if n_sel:
        # show top positive terms with reasonable document frequency
        show = pos[pos["document_frequency"] >= 15].head(10)
        print("  top POSITIVE terms (df>=15) -> push toward a big move:")
        for _, r in show.iterrows():
            print(f"    {r['logit_coef']:+.3f} OR={r['odds_ratio']:.2f} "
                  f"df={int(r['document_frequency']):<4d} r={r['univariate_r']:+.3f}  {r['term']}")
    return perf


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--text-col", default="body")
    ap.add_argument("--target-col", default="DailyHigh(%)")
    ap.add_argument("--id-col", default="ID")
    ap.add_argument("--variants", nargs="+", default=["tfidf", "binary"],
                    choices=["tfidf", "binary"])
    ap.add_argument("--task", default="regression",
                    choices=["regression", "classification"],
                    help="classification predicts P(target >= --binarize-threshold)")
    ap.add_argument("--binarize-threshold", type=float, default=30.0,
                    help="classification only: positive class is target >= this value")
    ap.add_argument("--strip-boilerplate", action="store_true",
                    help="drop law-firm/shareholder-notice and conference/earnings-call PRs")
    ap.add_argument("--strip-wire-boilerplate", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="scrub tracking URLs + newswire vendor tokens from text "
                         "(anti-fingerprinting; ON by default, use --no-strip-wire-boilerplate to disable)")
    ap.add_argument("--target-transform", default="signed_log",
                    choices=["none", "signed_log"],
                    help="regression only: signed_log = sign(y)*log1p(|y|) for heavy skew")
    ap.add_argument("--test-size", type=float, default=0.2)
    ap.add_argument("--cv", type=int, default=5)
    ap.add_argument("--min-df", type=int, default=5)
    ap.add_argument("--max-df", type=float, default=0.9)
    ap.add_argument("--max-features", type=int, default=None)
    ap.add_argument("--stop-words", default=None,
                    help="pass 'english' to strip English stop words")
    ap.add_argument("--max-iter", type=int, default=3000)
    ap.add_argument("--stability-splits", type=int, default=5)
    ap.add_argument("--stability-repeats", type=int, default=1)
    ap.add_argument("--no-drop-duplicates", action="store_true")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.is_absolute():
        inp = Path(__file__).resolve().parent / inp
    outdir = Path(args.outdir)
    if not outdir.is_absolute():
        outdir = Path(__file__).resolve().parent / outdir
    outdir.mkdir(parents=True, exist_ok=True)

    df = load_and_prepare(inp, args.text_col, args.target_col, args.id_col,
                          drop_duplicates=not args.no_drop_duplicates,
                          strip_boilerplate=args.strip_boilerplate,
                          strip_wire=args.strip_wire_boilerplate)

    runner = run_variant_clf if args.task == "classification" else run_variant
    perfs = [runner(v, df, args, outdir) for v in args.variants]
    perf_df = pd.DataFrame(perfs)
    perf_path = outdir / "model_performance_summary.csv"
    perf_df.to_csv(perf_path, index=False)
    print(f"\n=== Done. Performance summary -> {perf_path} ===")
    print(perf_df.to_string(index=False))


if __name__ == "__main__":
    main()
