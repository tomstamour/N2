#!/usr/bin/env python3
"""Rank every reasonable column in the enriched FinBERT TSV by how well it
predicts a binary big mover in DailyHigh(%).

Target: DailyHigh(%) >= --threshold (default 20). Raw % is heavily right-skewed,
so binary AUC is the primary metric — same rationale as
analyze_dailyhigh_predictors.py.

Three families of candidates:
  * Numeric — scored parameter-free with the Mann-Whitney rank AUC.
  * Categorical (Author, Exchange, label) — top-K levels kept, rest grouped to
    <other>, one-hot encoded, scored with pooled out-of-fold logistic AUC.
  * Headline text — engineered features: word count, char count, and presence
    of a small fixed keyword list. Scored as numeric AUCs.

Excluded by request: DailyHigh($), Trades/sec, Trigger.
Pure numpy / scipy — no sklearn, no matplotlib.
"""
import argparse
import pathlib
import re
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import rankdata, pointbiserialr, spearmanr

TARGET = "DailyHigh(%)"

NUMERIC_COLS = [
    "Float",
    "positive", "negative", "neutral", "sentiment_score",
    "body_duration_ms",
    "neutral_filter", "confidence_weighted", "net_score", "top_k", "positional",
    "recommended",
]

CATEGORICAL_COLS = ["Author", "Exchange", "label"]

HEADLINE_KEYWORDS = [
    "class action", "investor alert", "shareholder alert", "lawsuit",
    "fda", "approval", "phase",
    "acquisition", "merger", "partnership", "collaboration",
    "earnings", "results", "revenue",
    "offering", "placement", "dividend", "buyback",
    "contract", "award",
    "patent",
    "announces", "announcement",
]

MIN_N = 30


# ----------------------------- data -----------------------------------------
def load_data(tsv_path):
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    for c in NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=[TARGET])
    return df


# ----------------------------- metrics --------------------------------------
def auc_score(scores, labels):
    """Mann-Whitney rank AUC, tie-safe."""
    labels = labels.astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    sum_ranks_pos = ranks[labels].sum()
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def fit_logistic(X, y, l2=1e-3):
    n, d = X.shape

    def nll(w):
        z = X @ w[1:] + w[0]
        loss = np.mean(np.logaddexp(0, z) - y * z) + l2 * np.sum(w[1:] ** 2)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = np.empty_like(w)
        grad[0] = np.mean(p - y)
        grad[1:] = X.T @ (p - y) / n + 2 * l2 * w[1:]
        return loss, grad

    res = minimize(nll, np.zeros(d + 1), jac=True, method="L-BFGS-B")
    return res.x


def predict_proba(X, w):
    z = X @ w[1:] + w[0]
    return 1.0 / (1.0 + np.exp(-z))


def cv_auc_logistic(X, y, folds=5, seed=42):
    n = len(y)
    folds = min(folds, n)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    oof = np.empty(n)
    for k in range(folds):
        test_idx = idx[k::folds]
        train_idx = np.setdiff1d(idx, test_idx, assume_unique=False)
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        mu = X[train_idx].mean(axis=0)
        sd = X[train_idx].std(axis=0)
        sd[sd == 0] = 1.0
        Xtr = (X[train_idx] - mu) / sd
        Xte = (X[test_idx] - mu) / sd
        w = fit_logistic(Xtr, y[train_idx])
        oof[test_idx] = predict_proba(Xte, w)
    return auc_score(oof, y)


# ----------------------------- scoring families -----------------------------
def score_numeric(df, threshold):
    rows = []
    for col in NUMERIC_COLS:
        if col not in df.columns:
            continue
        sub = df.dropna(subset=[col])
        if len(sub) < MIN_N:
            continue
        x = sub[col].values.astype(float)
        y = (sub[TARGET].values >= threshold).astype(float)
        if y.sum() == 0 or y.sum() == len(y):
            continue
        auc = auc_score(x, y)
        try:
            pb = pointbiserialr(y, x).correlation
        except Exception:
            pb = float("nan")
        try:
            sp = spearmanr(x, sub[TARGET].values).correlation
        except Exception:
            sp = float("nan")
        rows.append({
            "kind": "numeric", "feature": col, "n": len(sub),
            "auc": auc, "strength": abs(auc - 0.5),
            "direction": "higher->bigger" if auc >= 0.5 else "lower->bigger",
            "pointbiserial": pb, "spearman_raw": sp,
        })
    return rows


def cap_levels(series, top_k):
    s = series.fillna("<missing>").astype(str)
    top = s.value_counts().head(top_k).index
    return s.where(s.isin(top), "<other>")


def score_categorical(df, threshold, folds, top_k):
    rows = []
    y_full = (df[TARGET].values >= threshold).astype(float)
    for col in CATEGORICAL_COLS:
        if col not in df.columns:
            continue
        s = cap_levels(df[col], top_k=top_k)
        dummies = pd.get_dummies(s, drop_first=True)
        if dummies.shape[1] == 0:
            continue
        X = dummies.values.astype(float)
        y = y_full
        if y.sum() == 0 or y.sum() == len(y):
            continue
        auc = cv_auc_logistic(X, y, folds=folds)

        # Per-level positive rate (for the report's "where does the signal live" view)
        level_stats = []
        df_tmp = pd.DataFrame({"lvl": s, "y": y})
        for lvl, g in df_tmp.groupby("lvl"):
            if len(g) < 20:
                continue
            level_stats.append((str(lvl), int(len(g)), float(g["y"].mean())))
        level_stats.sort(key=lambda r: -r[2])

        rows.append({
            "kind": "categorical", "feature": col, "n": int(len(df)),
            "auc": auc, "strength": abs(auc - 0.5),
            "direction": "—",
            "pointbiserial": float("nan"), "spearman_raw": float("nan"),
            "top_levels": level_stats[:5],
            "bot_levels": level_stats[-3:] if len(level_stats) >= 3 else [],
        })
    return rows


def headline_features(df):
    feats = pd.DataFrame(index=df.index)
    heads = df["Headline"].fillna("").astype(str)
    feats["headline_word_count"] = heads.str.split().str.len()
    feats["headline_char_count"] = heads.str.len()
    for kw in HEADLINE_KEYWORDS:
        slug = "kw__" + re.sub(r"\s+", "_", kw)
        feats[slug] = heads.str.contains(re.escape(kw), case=False, regex=True).astype(int)
    return feats


def score_text(df, threshold):
    rows = []
    feats = headline_features(df)
    y = (df[TARGET].values >= threshold).astype(float)
    for col in feats.columns:
        x = feats[col].values.astype(float)
        if np.all(x == x[0]):  # zero variance
            continue
        auc = auc_score(x, y)
        rows.append({
            "kind": "text", "feature": col, "n": int(len(df)),
            "auc": auc, "strength": abs(auc - 0.5),
            "direction": "higher->bigger" if auc >= 0.5 else "lower->bigger",
            "pointbiserial": float("nan"), "spearman_raw": float("nan"),
        })
    return rows


# ----------------------------- main -----------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="?",
                    default="concatenated_enriched_finBERT_noCoref_AddON.tsv")
    ap.add_argument("--threshold", type=float, default=20.0)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--top-k-levels", type=int, default=15,
                    help="Categoricals: keep this many most-frequent levels, rest -> <other>")
    args = ap.parse_args()

    tsv_path = pathlib.Path(args.tsv)
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")
    out_dir = tsv_path.parent if str(tsv_path.parent) else pathlib.Path(".")

    df = load_data(tsv_path)
    thr = args.threshold
    n_big = int((df[TARGET].values >= thr).sum())

    L = []

    def emit(line=""):
        print(line)
        L.append(line)

    emit(f"# DailyHigh(%) all-column predictor analysis — {tsv_path.name}")
    emit()
    emit(f"- Usable rows (target present): **{len(df)}**")
    emit(f"- Big-mover threshold: **DailyHigh(%) >= {thr}**")
    emit(f"- Big movers: **{n_big}** ({100 * n_big / len(df):.1f}%)  |  rest: {len(df) - n_big}")
    emit(f"- Numeric: Mann-Whitney rank AUC. Categorical: pooled OOF logistic AUC ({args.folds}-fold, top-{args.top_k_levels} levels). Text: same as numeric on engineered headline features.")
    emit(f"- Excluded per request: DailyHigh($), Trades/sec, Trigger.")
    emit()

    num_rows = score_numeric(df, thr)
    cat_rows = score_categorical(df, thr, folds=args.folds, top_k=args.top_k_levels)
    txt_rows = score_text(df, thr)
    all_rows = num_rows + cat_rows + txt_rows
    all_rows.sort(key=lambda r: r["strength"], reverse=True)

    emit("## Combined ranking — every candidate predictor")
    emit()
    emit("| rank | kind | feature | n | AUC | direction | |AUC-0.5| |")
    emit("|---|---|---|---|---|---|---|")
    for i, r in enumerate(all_rows, 1):
        emit(f"| {i} | {r['kind']} | {r['feature']} | {r['n']} | {r['auc']:.3f} | {r['direction']} | {r['strength']:.3f} |")
    emit()

    emit("## Numeric — detail (with Spearman vs raw %)")
    emit()
    emit("| feature | n | AUC | point-biserial r | Spearman vs raw % |")
    emit("|---|---|---|---|---|")
    for r in sorted(num_rows, key=lambda r: -r["strength"]):
        emit(f"| {r['feature']} | {r['n']} | {r['auc']:.3f} | {r['pointbiserial']:+.3f} | {r['spearman_raw']:+.3f} |")
    emit()

    if cat_rows:
        emit("## Categorical — top levels by big-mover rate")
        emit()
        base_rate = n_big / len(df)
        emit(f"Baseline big-mover rate across the dataset: **{base_rate:.1%}**")
        emit()
        for r in cat_rows:
            emit(f"### `{r['feature']}` (CV-AUC {r['auc']:.3f})")
            emit()
            emit("| level | n | big-mover rate | lift vs baseline |")
            emit("|---|---|---|---|")
            for lvl, n, p in r["top_levels"]:
                emit(f"| {lvl} | {n} | {p:.1%} | {p - base_rate:+.1%} |")
            if r["bot_levels"]:
                emit("")
                emit("Bottom levels (for contrast):")
                emit("")
                emit("| level | n | big-mover rate | lift vs baseline |")
                emit("|---|---|---|---|")
                for lvl, n, p in r["bot_levels"]:
                    emit(f"| {lvl} | {n} | {p:.1%} | {p - base_rate:+.1%} |")
            emit()

    emit("## Conclusion")
    emit()
    if all_rows:
        best = all_rows[0]
        emit(f"- **Best single predictor:** `{best['feature']}` "
             f"({best['kind']}, AUC {best['auc']:.3f}, |AUC-0.5| = {best['strength']:.3f}).")
        weak_top = sum(1 for r in all_rows[:5] if r["strength"] < 0.05)
        if weak_top >= 3:
            emit("- ⚠️ Most top features sit near AUC 0.50 — overall signal is weak; "
                 "treat the ranking as relative, not absolute.")
        emit("- See per-section tables for direction and per-level breakdowns.")
    emit()

    out_path = out_dir / "dailyhigh_all_columns_ranking.md"
    out_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nReport written -> {out_path}")


if __name__ == "__main__":
    main()
