#!/usr/bin/env python3
"""
s4_ft_train.py -- Series-4 Full Train: the two best val7030 configs
(val_ge30_hd_tfidf, val_ge50_bd_tfidf -- see tp50_final_ranking.csv) refit on
EVERY row of their corpus, with no held-out split, the same idea as kw15_FT
(kw15_ft_train.py) but for these two research models.

val7030_experiment.py already fit each config with an honest 70/30 split via
keyword_finder.py's own CLI (--no-strip-wire-boilerplate, text_col=text_hd or
text_bd400, target=fromPR_High(%), tfidf+binary variants). This script imports
keyword_finder's pieces directly (make_vectorizer, the CLF grids, clf_stability,
load_and_prepare) and repeats run_variant_clf minus the split -- tfidf only, one
model per config. keyword_finder.py is never edited.

keyword_finder.load_and_prepare() does NOT shuffle rows (it returns corpus_export.tsv's
own row order). run_variant_clf's train_test_split(random_state=seed) incidentally
shuffles the train subset before LogisticRegressionCV(cv=5)'s internal (unshuffled)
StratifiedKFold runs. Fitting on 100% skips that split, so this script shuffles the
prepared rows itself (seed 42) before fitting -- otherwise the internal CV that picks
C/l1_ratio would see time-correlated blocks instead of random subsamples. Same fix
kw15_ft_train.py's channel_rows() applies.

An _FT model has NO honest score: it trained on every labelled row. cv_roc_auc_mean is
an IN-SAMPLE CV number (C/l1_ratio were picked on these same rows) -- not out-of-sample.

Live trading shares this machine -- run at idle priority on one physical core:
  nice -n 19 chrt -i 0 taskset -c 3,7 /home/tom/venv/bin/python -u s4_ft_train.py --model all
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import warnings
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_selection import f_regression, r_regression
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.model_selection import cross_validate
from sklearn.pipeline import Pipeline
from sklearn.utils import shuffle as sk_shuffle

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keyword_finder as kf  # noqa: E402

OUT_DIR = HERE / "S4_model_trained_on_2026-05-04-2026-09-24_val7030"
CORPUS_TSV = OUT_DIR / "corpus_export.tsv"
TARGET_COL = "fromPR_High(%)"
ID_COL = "ID"
VEC_KWARGS = dict(min_df=5, max_df=0.9, max_features=None, stop_words=None)
CV, MAX_ITER, SEED = 5, 3000, 42
STAB_SPLITS, STAB_REPEATS = 5, 1

CONFIGS = {
    "kw30_tfidf_S4_FT_hd": dict(threshold=30.0, text_col="text_hd",
                                source_model="val_ge30_hd_tfidf"),
    "kw50_tfidf_S4_FT_bd": dict(threshold=50.0, text_col="text_bd400",
                                source_model="val_ge50_bd_tfidf"),
}


# ----------------------------------------------------------------------------
# keyword_finder.run_variant_clf, minus the split (verbatim recipe, per kw15_ft_train.py)
# ----------------------------------------------------------------------------
def make_pipe(n_jobs: int) -> Pipeline:
    return Pipeline([
        ("vec", kf.make_vectorizer("tfidf", **VEC_KWARGS)),
        ("clf", LogisticRegressionCV(
            penalty="elasticnet", solver="saga", l1_ratios=kf.CLF_L1_RATIO_GRID,
            Cs=kf.CLF_C_GRID, cv=CV, scoring="roc_auc",
            class_weight="balanced", max_iter=MAX_ITER, tol=1e-3,
            random_state=SEED, n_jobs=n_jobs)),
    ])


def fit(pipe: Pipeline, texts, y) -> tuple[float, float]:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", FutureWarning)
        pipe.fit(texts, y)
    clf = pipe.named_steps["clf"]
    return float(np.ravel(clf.C_)[0]), float(np.ravel(clf.l1_ratio_)[0])


def cv_scores(texts, y, C_: float, l1r: float, n_jobs: int) -> dict:
    cv_pipe = Pipeline([
        ("vec", kf.make_vectorizer("tfidf", **VEC_KWARGS)),
        ("clf", LogisticRegression(
            penalty="elasticnet", solver="saga", C=C_, l1_ratio=l1r,
            class_weight="balanced", max_iter=MAX_ITER, tol=1e-3, random_state=SEED)),
    ])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", ConvergenceWarning)
        warnings.simplefilter("ignore", FutureWarning)
        res = cross_validate(cv_pipe, texts, y, cv=CV, n_jobs=n_jobs,
                             scoring=("roc_auc", "average_precision"))
    return {"cv_roc_auc_mean": float(res["test_roc_auc"].mean()),
            "cv_roc_auc_std": float(res["test_roc_auc"].std()),
            "cv_avg_precision_mean": float(res["test_average_precision"].mean())}


def keyword_table(pipe: Pipeline, texts, y) -> pd.DataFrame:
    vec, clf = pipe.named_steps["vec"], pipe.named_steps["clf"]
    X = vec.transform(texts)
    terms = vec.get_feature_names_out()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        uni_r = r_regression(X, y)
        f_scores, p_values = f_regression(X, y)
    doc_freq = np.asarray((X > 0).sum(axis=0)).ravel()
    mean_feat = np.asarray(X.mean(axis=0)).ravel()
    coef = clf.coef_[0]
    kt = pd.DataFrame({
        "term": terms,
        "ngram_type": np.where(pd.Series(terms).str.contains(" "), "bigram", "unigram"),
        "document_frequency": doc_freq,
        "document_frequency_pct": 100.0 * doc_freq / X.shape[0],
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
    print("  running stability refits...", flush=True)
    stab = kf.clf_stability(texts, y, "tfidf", VEC_KWARGS, clf.C_[0], clf.l1_ratio_[0],
                            n_splits=STAB_SPLITS, n_repeats=STAB_REPEATS,
                            seed=SEED, max_iter=MAX_ITER)
    kt = kt.merge(stab, left_on="term", right_index=True, how="left")
    return kt.sort_values("abs_logit_coef", ascending=False).reset_index(drop=True)


def write_outputs(outdir: Path, pipe: Pipeline, kt: pd.DataFrame) -> None:
    prefix = outdir / "tfidf"
    kt.to_csv(f"{prefix}_keyword_impact_all_terms.csv", index=False)
    kt[kt["logit_coef"] > 0].sort_values("logit_coef", ascending=False).to_csv(
        f"{prefix}_keyword_impact_positive_terms.csv", index=False)
    kt[kt["logit_coef"] < 0].sort_values("logit_coef", ascending=True).to_csv(
        f"{prefix}_keyword_impact_negative_terms.csv", index=False)
    joblib.dump(pipe, f"{prefix}_fitted_pipeline.joblib")


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# per-config train
# ----------------------------------------------------------------------------
def train_one(name: str, cfg: dict, n_jobs: int, force: bool) -> int:
    outdir = OUT_DIR / name
    if (outdir / "card.json").exists() and not force:
        print(f"refusing: {outdir} is complete (card.json) and frozen -- Full Train "
              f"dirs are never re-trained into (--force to override)")
        return 1
    outdir.mkdir(exist_ok=True)
    t0 = time.time()

    df = kf.load_and_prepare(
        CORPUS_TSV, text_col=cfg["text_col"], target_col=TARGET_COL, id_col=ID_COL,
        drop_duplicates=True, strip_boilerplate=False, strip_wire=False)
    # keyword_finder's load_and_prepare does not shuffle; fitting on 100% of rows
    # skips run_variant_clf's train_test_split (which incidentally shuffles the train
    # subset) -- shuffle here so LogisticRegressionCV's internal unshuffled
    # StratifiedKFold(cv=5) sees random subsamples, not time-correlated blocks.
    df = sk_shuffle(df, random_state=SEED).reset_index(drop=True)
    texts = df["_text"].to_numpy()
    y = (df["_target"].to_numpy(dtype=float) >= cfg["threshold"]).astype(int)
    n = len(y)
    n_pos = int(y.sum())
    print(f"\n=== {name} ({cfg['text_col']}, threshold>={cfg['threshold']:g}) "
          f"===\n  rows {n}, positives {n_pos} ({y.mean():.4f})", flush=True)

    pipe = make_pipe(n_jobs)
    C_, l1r = fit(pipe, texts, y)
    n_feat = len(pipe.named_steps["vec"].get_feature_names_out())
    t_fit = time.time() - t0
    print(f"  fitted in {t_fit:.0f}s: {n_feat} features | selected C={C_:g} "
          f"l1_ratio={l1r}", flush=True)

    cvs = cv_scores(texts, y, C_, l1r, n_jobs)
    print(f"  CV ROC-AUC on every row (NOT out-of-sample: C/l1 picked on them): "
          f"{cvs['cv_roc_auc_mean']:.4f} +/- {cvs['cv_roc_auc_std']:.4f}", flush=True)

    kt = keyword_table(pipe, texts, y)
    write_outputs(outdir, pipe, kt)
    n_sel = int(kt["selected_by_model"].sum())
    print(f"  selected terms {n_sel}/{len(kt)}; wrote {name}/tfidf_* "
          f"({time.time() - t0:.0f}s)", flush=True)

    perf = pd.DataFrame([dict(
        variant="tfidf", task="classification", binarize_threshold=cfg["threshold"],
        n_train=n, n_test=0, n_features=n_feat,
        train_pos_rate=float(y.mean()), test_pos_rate=np.nan,
        model_C=C_, model_l1_ratio=l1r,
        test_roc_auc=np.nan, test_avg_precision=np.nan, test_precision=np.nan,
        test_recall=np.nan, test_f1=np.nan, baseline_avg_precision=np.nan, **cvs)])
    perf.to_csv(outdir / "model_performance_summary.csv", index=False)

    card = {
        "release": name,
        "source_model": cfg["source_model"],
        "note": ("val7030_experiment.py's SAME recipe (keyword_finder.py --task "
                 "classification --no-strip-wire-boilerplate, tfidf only) refit on EVERY "
                 "row of its channel: no held-out split, so NO held-out score -- same "
                 "idea as kw15_FT. No reference_scores.json: unlike kw15_FT, nothing "
                 "loads this model live, so there is no consumer to byte-verify against."),
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "script": "tools/keyword/s4_ft_train.py (copy in this dir)",
        "data": {
            "corpus": "corpus_export.tsv (S4_model_trained_on_2026-05-04-2026-09-24_val7030)",
            "corpus_sha256": sha256(CORPUS_TSV),
            "rows": n, "positives": n_pos, "prevalence": float(y.mean()),
            "prevalence_exact": f"{n_pos}/{n}",
        },
        "label": f"{TARGET_COL} >= {cfg['threshold']:g}",
        "text_col": cfg["text_col"],
        "recipe": {
            "vectorizer": "keyword_finder.make_vectorizer: 1-2 grams, letters-only tokens, "
                          "lowercase, tfidf = sublinear_tf",
            **VEC_KWARGS,
            "model": "LogisticRegressionCV(penalty=elasticnet, solver=saga, "
                     "scoring=roc_auc, class_weight=balanced, tol=1e-3)",
            "Cs": list(kf.CLF_C_GRID), "l1_ratios": list(kf.CLF_L1_RATIO_GRID),
            "cv": CV, "max_iter": MAX_ITER, "seed": SEED,
            "rows": "every row from load_and_prepare (drop_duplicates=True, "
                    "strip_boilerplate=False, strip_wire=False); order shuffled with "
                    "seed 42 before fitting (see script docstring)",
            "stability": f"keyword_finder.clf_stability, {STAB_SPLITS} folds x {STAB_REPEATS}",
        },
        "fit": {
            "model_C": C_, "model_l1_ratio": l1r, "n_features": n_feat,
            "selected_terms": n_sel, "fit_wall_s": round(t_fit, 1), **cvs,
        },
        "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                     "numpy": np.__version__, "scipy": scipy.__version__,
                     "pandas": pd.__version__, "joblib": joblib.__version__},
        "train_wall_s": round(time.time() - t0, 1),
    }
    card["files_sha256"] = {p.name: sha256(p) for p in sorted(outdir.iterdir())
                            if p.is_file() and p.suffix != ".log"}
    (outdir / "card.json").write_text(json.dumps(card, indent=2))
    print(f"  wrote card.json -> {outdir}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=[*CONFIGS, "all"], default="all")
    ap.add_argument("--n-jobs", type=int, default=2)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    names = list(CONFIGS) if a.model == "all" else [a.model]
    rc = 0
    for name in names:
        rc |= train_one(name, CONFIGS[name], a.n_jobs, a.force)
    return rc


if __name__ == "__main__":
    sys.exit(main())
