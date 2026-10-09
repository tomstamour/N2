#!/usr/bin/env python3
"""
kw15_ft_train.py -- kw15 "_FT" (Full Training set): the live kw15 recipe refit on EVERY
labelled row, with no held-out split.

The live kw15 (model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN/, loaded by
orchestrator5/o54_engine.py) came out of `keyword_finder.py --task classification
--binarize-threshold 15`. Its run_variant_clf ALWAYS holds out 20% as a test split and
saves a pipeline fit on the other 80% (2,117 of 2,647 rows). keyword_finder.py is a live
file and is never edited, so this script imports its pieces (make_vectorizer, the CLF
grids, clf_stability, load_and_prepare) and repeats run_variant_clf minus the split.

  repro   keyword_finder's own prep + 80/20 split on the OLD train corpus, compared with
          the live pipeline: must be identical (proves this is the same recipe).
  train   FM3's frozen corpus snapshot (model-FM3/fm3_common.load_corpus: fromPR_status
          == ok, target sessions 2026-05-04 -> 09-24), text = the EXACT live kw15 prep
          (o54_engine._kw15_prep), label fromPR_High(%) >= 15, every row
          -> model_trained_on_2026-05-04-2026-09-24/ (outputs *_FT, see card.json).

⚠ An _FT model has NO honest score: it trained on every labelled row. Its thresholds
  come from live data (Orchestrator5.5 logs kw15_proba_bd_FT / kw15_proba_hd_FT).

Live trading shares this machine -- run at idle priority on one physical core:
  nice -n 19 chrt -i 0 taskset -c 3,7 /home/tom/venv/bin/python -u kw15_ft_train.py repro --channel hd
  nice -n 19 chrt -i 0 taskset -c 3,7 /home/tom/venv/bin/python -u kw15_ft_train.py train --n-jobs 2
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                    # tools/keyword/
sys.path.insert(0, str(HERE / "model-FM3"))
import fm3_common as C  # noqa: E402  (FIRST: caps BLAS threads before numpy loads)

import argparse
import hashlib
import json
import platform
import shutil
import time
import warnings

import joblib
import numpy as np
import pandas as pd
import scipy
import sklearn
from sklearn.exceptions import ConvergenceWarning
from sklearn.feature_selection import f_regression, r_regression
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.model_selection import cross_validate, train_test_split
from sklearn.pipeline import Pipeline
from sklearn.utils import shuffle as sk_shuffle

import keyword_finder as kf  # tools/keyword/ is the script dir, so already importable

OUT_DIR = HERE / "model_trained_on_2026-05-04-2026-09-24"
SUFFIX = "_FT"
THRESHOLD = 15.0
TARGET_COL = "fromPR_High(%)"
# channel -> (output subdir, fm3_common corpus text column, live output column)
CHANNELS = {
    "hd": ("classify_fromPRhigh_ge15_headline_FT", "text_hd", "kw15_proba_hd_FT"),
    "bd": ("classify_fromPRhigh_ge15_FT", "text_bd400", "kw15_proba_bd_FT"),
}
# keyword_finder.py's CLI defaults -- what the live kw15 was trained with.
VEC_KWARGS = dict(min_df=5, max_df=0.9, max_features=None, stop_words=None)
CV, MAX_ITER, SEED = 5, 3000, 42
STAB_SPLITS, STAB_REPEATS = 5, 1

OLD_DIR = HERE / "model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN"
OLD_TRAIN = HERE / "inputs" / ("04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_"
                               "Classed_BodyEnc_clean_FromPRhigh_TRAIN_pre20260620_cap400.tsv")
REPRO = {"hd": ("Headline", OLD_DIR / "classify_fromPRhigh_ge15_headline"),
         "bd": ("Body", OLD_DIR / "classify_fromPRhigh_ge15")}

N_REF_POS, N_REF_NEG = 15, 25
# Edge cases the live path must also reproduce: HTML + no body, empty everything, and a
# body full of the newswire plumbing strip_wire_boilerplate removes.
REF_PROBES = [
    {"ID": "probe-html-no-body",
     "Headline": "<b>Acme Corp</b> Announces FDA Approval of its Lead Drug Candidate",
     "Body": ""},
    {"ID": "probe-empty", "Headline": "", "Body": ""},
    {"ID": "probe-wire",
     "Headline": "Acme Corp Announces $10 Million Registered Direct Offering",
     "Body": ("NEW YORK, Sept. 28, 2026 (GLOBE NEWSWIRE) -- Acme Corp (NASDAQ: ACME) today "
              "announced a registered direct offering of 2,000,000 shares at $5.00 per share. "
              "https://www.globenewswire.com/Tracker?data=abc123 H.C. Wainwright & Co. is "
              "acting as the exclusive placement agent. Contact: ir@acme.com www.acme.com")},
]


# ----------------------------------------------------------------------------
# keyword_finder.run_variant_clf, minus the split
# ----------------------------------------------------------------------------
def make_pipe(variant: str, n_jobs: int) -> Pipeline:
    """run_variant_clf's pipeline, verbatim. n_jobs only sets how many CV folds run at
    once (keyword_finder used -1); every fold is seeded, so no coefficient depends on it."""
    return Pipeline([
        ("vec", kf.make_vectorizer(variant, **VEC_KWARGS)),
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


def cv_scores(texts, y, variant: str, C_: float, l1r: float, n_jobs: int) -> dict:
    """run_variant_clf's CV report (selected C / l1_ratio, fresh vectorizer per fold) --
    on every row here, so it is NOT an out-of-sample estimate: C / l1_ratio were picked
    on these same rows."""
    cv_pipe = Pipeline([
        ("vec", kf.make_vectorizer(variant, **VEC_KWARGS)),
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


def keyword_table(pipe: Pipeline, texts, y, variant: str, C_: float, l1r: float) -> pd.DataFrame:
    """run_variant_clf's keyword-impact table (same columns, same order) + stability."""
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
    kt["model_C"] = C_
    kt["model_l1_ratio"] = l1r
    print("  running stability refits...", flush=True)
    stab = kf.clf_stability(texts, y, variant, VEC_KWARGS, C_, l1r,
                            n_splits=STAB_SPLITS, n_repeats=STAB_REPEATS,
                            seed=SEED, max_iter=MAX_ITER)
    kt = kt.merge(stab, left_on="term", right_index=True, how="left")
    return kt.sort_values("abs_logit_coef", ascending=False).reset_index(drop=True)


def write_variant(outdir: Path, variant: str, pipe: Pipeline, kt: pd.DataFrame) -> None:
    prefix = outdir / variant
    kt.to_csv(f"{prefix}_keyword_impact_all_terms.csv", index=False)
    kt[kt["logit_coef"] > 0].sort_values("logit_coef", ascending=False).to_csv(
        f"{prefix}_keyword_impact_positive_terms.csv", index=False)
    kt[kt["logit_coef"] < 0].sort_values("logit_coef", ascending=True).to_csv(
        f"{prefix}_keyword_impact_negative_terms.csv", index=False)
    joblib.dump(pipe, f"{prefix}_fitted_pipeline.joblib")


# ----------------------------------------------------------------------------
# data
# ----------------------------------------------------------------------------
def channel_rows(df: pd.DataFrame, ch: str) -> pd.DataFrame:
    """keyword_finder.load_and_prepare's row rules, applied to the live-prepped text:
    drop empty texts, then exact (text, target) duplicates. Then shuffle (seed 42):
    keyword_finder's train_test_split hands the fit a shuffled order too, and
    LogisticRegressionCV's inner 5 folds are NOT shuffled, so row order picks the folds
    and a date-sorted corpus would turn them into time blocks."""
    col = CHANNELS[ch][1]
    d = pd.DataFrame({"ID": df["ID"].astype(str).to_numpy(),
                      "text": df[col].fillna("").astype(str).to_numpy(),
                      "y": df["y"].astype(float).to_numpy()})
    d = d[d["text"].str.strip() != ""]
    d = d.drop_duplicates(subset=["text", "y"])
    return sk_shuffle(d, random_state=SEED).reset_index(drop=True)


def reference_rows(df: pd.DataFrame, pipes: dict) -> list[dict]:
    """Raw Headline / Body cells -> the two _FT outputs, through the live prep. The
    orchestrator's kw15 task must reproduce every value exactly at startup."""
    rng = np.random.RandomState(7)
    ypos = df["y15"].to_numpy() == 1
    pick = sorted(rng.choice(np.flatnonzero(ypos), N_REF_POS, replace=False).tolist()
                  + rng.choice(np.flatnonzero(~ypos), N_REF_NEG, replace=False).tolist())
    rows = [{"ID": str(df["ID"].iloc[i]),
             "Headline": "" if pd.isna(df["Headline"].iloc[i]) else str(df["Headline"].iloc[i]),
             "Body": "" if pd.isna(df["Body"].iloc[i]) else str(df["Body"].iloc[i])}
            for i in pick] + [dict(p) for p in REF_PROBES]
    for r in rows:
        bd = C.prep_body400_kw(r["Body"])
        hd = C.prep_headline(r["Headline"])
        r[CHANNELS["bd"][2]] = round(float(pipes["bd"].predict_proba([bd])[0][1]), 6)
        r[CHANNELS["hd"][2]] = round(float(pipes["hd"].predict_proba([hd])[0][1]), 6)
    return rows


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
def cmd_repro(a) -> int:
    """Rebuild the LIVE kw15 pipeline of one channel with this script's pipeline code
    and keyword_finder's own prep + split. Identical = same recipe."""
    text_col, old = REPRO[a.channel]
    old_perf = pd.read_csv(old / "model_performance_summary.csv").set_index("variant").loc["tfidf"]
    df = kf.load_and_prepare(OLD_TRAIN, text_col, TARGET_COL, "ID", drop_duplicates=True,
                             strip_boilerplate=False, strip_wire=True)
    texts = df["_text"].to_numpy()
    y = (df["_target"].to_numpy(dtype=float) >= THRESHOLD).astype(int)
    tr_txt, _te_txt, tr_y, _te_y = train_test_split(
        texts, y, test_size=0.2, random_state=SEED, stratify=y)
    t0 = time.time()
    pipe = make_pipe("tfidf", a.n_jobs)
    C_, l1r = fit(pipe, tr_txt, tr_y)
    live = joblib.load(old / "tfidf_fitted_pipeline.joblib")
    vocab_new = list(pipe.named_steps["vec"].get_feature_names_out())
    vocab_live = list(live.named_steps["vec"].get_feature_names_out())
    same_vocab = vocab_new == vocab_live
    coef_diff = (float(np.max(np.abs(pipe.named_steps["clf"].coef_
                                     - live.named_steps["clf"].coef_)))
                 if same_vocab else float("nan"))
    d = np.abs(pipe.predict_proba(texts)[:, 1] - live.predict_proba(texts)[:, 1])
    ok = (same_vocab and float(d.max()) == 0.0 and C_ == float(old_perf["model_C"])
          and l1r == float(old_perf["model_l1_ratio"]))
    print(f"\n=== repro {a.channel} ({text_col}) — fit {time.time() - t0:.0f}s ===")
    print(f"  train rows {len(tr_y)} (live {int(old_perf['n_train'])}) | features "
          f"{len(vocab_new)} (live {int(old_perf['n_features'])}) | same vocabulary: {same_vocab}")
    print(f"  C {C_} (live {old_perf['model_C']}) | l1_ratio {l1r} (live {old_perf['model_l1_ratio']})")
    print(f"  max|coef diff| {coef_diff:.3g} | max|proba diff| over {len(texts)} texts {d.max():.3g}")
    print(f"  REPRO {'PASS — identical to the live pipeline' if ok else 'FAIL'}")
    return 0 if ok else 1


def cmd_train(a) -> int:
    if (OUT_DIR / "card.json").exists() and not a.force:
        sys.exit(f"refusing: {OUT_DIR} is complete (card.json) and frozen — model dirs "
                 f"referenced by live code are never re-trained into (--force to override)")
    OUT_DIR.mkdir(exist_ok=True)
    t_all = time.time()
    df = C.load_corpus()
    snap = json.loads(C.SNAPSHOT_META.read_text())
    if df.attrs.get("sha256") != snap["sha256"]:
        sys.exit("refusing: the corpus cache does not match the FM3 snapshot sha256")
    n_rep = int(df.duplicated(subset=["Symbol", "Headline"]).sum())
    print(f"=== corpus: {len(df)} rows, sessions {df['session'].min()} -> {df['session'].max()}, "
          f">= {THRESHOLD:g}%: {int(df['y15'].sum())} ({df['y15'].mean():.4f}); "
          f"Symbol+Headline repeats kept: {n_rep}; snapshot sha256 {snap['sha256'][:12]}", flush=True)

    data = {ch: channel_rows(df, ch) for ch in a.channels}
    perfs = {ch: [] for ch in a.channels}
    fits = {ch: {} for ch in a.channels}
    pipes = {}
    for variant in a.variants:              # tfidf first: it is the one Orchestrator5.5 loads
        for ch in a.channels:
            d = data[ch]
            texts = d["text"].to_numpy()
            y = (d["y"].to_numpy() >= THRESHOLD).astype(int)
            sub = OUT_DIR / CHANNELS[ch][0]
            sub.mkdir(exist_ok=True)
            print(f"\n=== {ch} / {variant}: {len(y)} rows, positives {int(y.sum())} "
                  f"({y.mean():.4f}) ===", flush=True)
            t0 = time.time()
            pipe = make_pipe(variant, a.n_jobs)
            C_, l1r = fit(pipe, texts, y)
            n_feat = len(pipe.named_steps["vec"].get_feature_names_out())
            t_fit = time.time() - t0
            print(f"  fitted in {t_fit:.0f}s: {n_feat} features | selected C={C_:g} "
                  f"l1_ratio={l1r}", flush=True)
            cvs = cv_scores(texts, y, variant, C_, l1r, a.n_jobs)
            print(f"  CV ROC-AUC on every row (NOT out-of-sample: C/l1 picked on them): "
                  f"{cvs['cv_roc_auc_mean']:.4f} ± {cvs['cv_roc_auc_std']:.4f}", flush=True)
            kt = keyword_table(pipe, texts, y, variant, C_, l1r)
            write_variant(sub, variant, pipe, kt)
            n_sel = int(kt["selected_by_model"].sum())
            print(f"  selected terms {n_sel}/{len(kt)}; wrote {sub.name}/{variant}_* "
                  f"({time.time() - t0:.0f}s)", flush=True)
            # keyword_finder's summary columns; the test_* ones have no test split here
            perfs[ch].append(dict(
                variant=variant, task="classification", binarize_threshold=THRESHOLD,
                n_train=len(y), n_test=0, n_features=n_feat,
                train_pos_rate=float(y.mean()), test_pos_rate=np.nan,
                model_C=C_, model_l1_ratio=l1r,
                test_roc_auc=np.nan, test_avg_precision=np.nan, test_precision=np.nan,
                test_recall=np.nan, test_f1=np.nan, baseline_avg_precision=np.nan, **cvs))
            pd.DataFrame(perfs[ch]).to_csv(sub / "model_performance_summary.csv", index=False)
            fits[ch][variant] = dict(C=C_, l1_ratio=l1r, n_features=n_feat,
                                     selected_terms=n_sel, fit_s=round(t_fit, 1), **cvs)
            if variant == "tfidf":
                pipes[ch] = pipe
        if variant == "tfidf" and set(pipes) == set(CHANNELS):
            ref = {"note": "raw Headline / Body cells -> the _FT outputs through the live "
                           "kw15 prep (o54_engine._kw15_prep); Orchestrator5.5 must "
                           "reproduce every value exactly at startup",
                   "outputs": [CHANNELS["bd"][2], CHANNELS["hd"][2]],
                   "rows": reference_rows(df, pipes)}
            (OUT_DIR / "reference_scores.json").write_text(json.dumps(ref, indent=1))
            print(f"\n  wrote reference_scores.json ({len(ref['rows'])} rows)", flush=True)

    used = {ch: set(data[ch]["ID"]) for ch in a.channels}
    rows = pd.DataFrame({"ID": df["ID"].astype(str), "Symbol": df["Symbol"],
                         "session": df["session"].astype(str), "y": df["y"],
                         "y15": df["y15"]})
    for ch in a.channels:
        rows[f"used_{ch}"] = rows["ID"].isin(used[ch]).astype(int)
    rows.to_csv(OUT_DIR / "train_rows.tsv", sep="\t", index=False)
    shutil.copy2(Path(__file__), OUT_DIR / Path(__file__).name)

    card = {
        "release": "kw15_FT",
        "suffix": SUFFIX,
        "outputs": {ch: CHANNELS[ch][2] for ch in CHANNELS},
        "subdirs": {ch: CHANNELS[ch][0] for ch in CHANNELS},
        "live_file": "tfidf_fitted_pipeline.joblib",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "script": "tools/keyword/kw15_ft_train.py (copy in this dir)",
        "note": ("The live kw15 recipe (keyword_finder.py --task classification "
                 "--binarize-threshold 15 --variants tfidf binary) refit on EVERY labelled "
                 "row: no 20% test split, so NO held-out score. Thresholds come from live "
                 "data. Honest proxy: model-FM3/runs/B1_kw15recipe_* (the same recipe "
                 "refit on growing data, 8 out-of-time weekly blocks) vs B0_kw15live_*."),
        "data": {
            "snapshot": snap["snapshot"], "sha256": snap["sha256"], "source": snap["source"],
            "loader": "model-FM3/fm3_common.load_corpus (fromPR_status == ok; TEST rows "
                      "re-issuing a DEV PR dropped) -- the same rows as FM3 R1_FT",
            "rows": int(len(df)),
            "sessions": [str(df["session"].min()), str(df["session"].max())],
            "symbol_headline_repeats_kept": n_rep,
        },
        "label": f"{TARGET_COL} >= {THRESHOLD:g}",
        "text": {
            "bd": "strip_html + strip_wire_boilerplate + first 400 words of the Body cell "
                  "(== o54_engine._kw15_prep(body, 400); fm3_common.prep_body400_kw)",
            "hd": "strip_html + strip_wire_boilerplate of the Headline "
                  "(== o54_engine._kw15_prep(headline, None); fm3_common.prep_headline)",
        },
        "recipe": {
            "vectorizer": "keyword_finder.make_vectorizer: 1-2 grams, letters-only tokens, "
                          "lowercase; tfidf = sublinear_tf; binary = CountVectorizer(binary)",
            **VEC_KWARGS,
            "model": "LogisticRegressionCV(penalty=elasticnet, solver=saga, "
                     "scoring=roc_auc, class_weight=balanced, tol=1e-3)",
            "Cs": list(kf.CLF_C_GRID), "l1_ratios": list(kf.CLF_L1_RATIO_GRID),
            "cv": CV, "max_iter": MAX_ITER, "seed": SEED,
            "rows": "every row; empty texts and exact (text, target) duplicates dropped; "
                    "order shuffled with seed 42 (the inner CV folds are unshuffled)",
            "stability": f"keyword_finder.clf_stability, {STAB_SPLITS} folds x {STAB_REPEATS}",
        },
        "channels": {ch: {"rows": int(len(data[ch])),
                          "positives": int((data[ch]["y"] >= THRESHOLD).sum()),
                          "prevalence": float((data[ch]["y"] >= THRESHOLD).mean()),
                          "prevalence_exact": f"{int((data[ch]['y'] >= THRESHOLD).sum())}/{len(data[ch])}",
                          **{v: fits[ch][v] for v in fits[ch]}}
                     for ch in a.channels},
        "versions": {"python": platform.python_version(), "sklearn": sklearn.__version__,
                     "numpy": np.__version__, "scipy": scipy.__version__,
                     "pandas": pd.__version__, "joblib": joblib.__version__},
        "train_wall_s": round(time.time() - t_all, 1),
    }
    card["files_sha256"] = {str(p.relative_to(OUT_DIR)): sha256(p)
                            for p in sorted(OUT_DIR.rglob("*"))
                            if p.is_file() and p.suffix != ".log" and p.name != "card.json"}
    (OUT_DIR / "card.json").write_text(json.dumps(card, indent=2))
    print(f"\n=== Done in {time.time() - t_all:.0f}s -> {OUT_DIR} ===", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("repro", help="rebuild one LIVE kw15 pipeline; must be identical")
    p.add_argument("--channel", choices=sorted(REPRO), default="hd")
    p.add_argument("--n-jobs", type=int, default=2)
    p = sub.add_parser("train", help="fit the _FT models on every row")
    p.add_argument("--channels", nargs="+", choices=list(CHANNELS), default=list(CHANNELS))
    p.add_argument("--variants", nargs="+", choices=["tfidf", "binary"],
                   default=["tfidf", "binary"])
    p.add_argument("--n-jobs", type=int, default=2)
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    return cmd_repro(a) if a.cmd == "repro" else cmd_train(a)


if __name__ == "__main__":
    sys.exit(main())
