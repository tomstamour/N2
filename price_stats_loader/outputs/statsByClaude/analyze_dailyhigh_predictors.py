#!/usr/bin/env python3
"""Rank the 6 FinBERT sentiment scores (singly and in 2- & 3-column combos)
by how well they predict a "big mover" in DailyHigh(%).

Target is binary (DailyHigh(%) >= --threshold) because the raw % is wildly
skewed (median ~5%, max ~750%). Primary metric is ROC AUC: threshold-free,
outlier-robust separation of big movers from the rest. Combos are scored with
k-fold cross-validated AUC from a logistic fit so multi-feature models are
compared fairly against single features.

Pure numpy/scipy/plotext — no sklearn, no matplotlib.
"""
import sys
import argparse
import pathlib
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import rankdata, pointbiserialr, spearmanr

try:
    import plotext as plt
except ImportError:
    plt = None

TARGET = "DailyHigh(%)"
SENTIMENT_COLUMNS = [
    "sentiment_score",
    "neutral_filter",
    "confidence_weighted",
    "net_score",
    "top_k",
    "positional",
    "positive",
    "negative",
    "neutral",
    "positive_minus_neutral",
]


# ----------------------------- data -----------------------------------------
def load_data(tsv_path: pathlib.Path) -> pd.DataFrame:
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    for col in SENTIMENT_COLUMNS:
        if col == "positive_minus_neutral":
            continue
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["positive_minus_neutral"] = df["positive"] - df["neutral"]
    df = df.dropna(subset=[TARGET])
    df = df.dropna(subset=SENTIMENT_COLUMNS, how="all")
    return df


# ----------------------------- metrics --------------------------------------
def auc_score(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC AUC via the Mann-Whitney rank formula (handles ties)."""
    labels = labels.astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    sum_ranks_pos = ranks[labels].sum()
    return (sum_ranks_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def roc_curve(scores: np.ndarray, labels: np.ndarray):
    labels = labels.astype(bool)
    order = np.argsort(-scores)
    y = labels[order]
    tps = np.cumsum(y)
    fps = np.cumsum(~y)
    n_pos = max(int(labels.sum()), 1)
    n_neg = max(int((~labels).sum()), 1)
    tpr = np.concatenate(([0.0], tps / n_pos))
    fpr = np.concatenate(([0.0], fps / n_neg))
    return fpr, tpr


# ----------------------------- logistic --------------------------------------
def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = 1e-3) -> np.ndarray:
    """Logistic regression by minimizing penalized cross-entropy.
    w[0] is the intercept; X is assumed already standardized."""
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


def predict_proba(X: np.ndarray, w: np.ndarray) -> np.ndarray:
    z = X @ w[1:] + w[0]
    return 1.0 / (1.0 + np.exp(-z))


def cv_auc(X: np.ndarray, y: np.ndarray, folds: int, seed: int = 42):
    """Pooled out-of-fold AUC. Returns (auc, coefficients_on_full_fit)."""
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
        Xtr = (X[train_idx] - mu) / sd
        Xte = (X[test_idx] - mu) / sd
        w = fit_logistic(Xtr, y[train_idx])
        oof[test_idx] = predict_proba(Xte, w)
    # coefficients on the full standardized data (for interpretation)
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    w_full = fit_logistic((X - mu) / sd, y)
    return auc_score(oof, y), w_full[1:]


# ----------------------------- analysis --------------------------------------
def evaluate_combo(df: pd.DataFrame, cols, y_full_mask_col, threshold, folds):
    sub = df.dropna(subset=list(cols))
    y = (sub[TARGET].values >= threshold).astype(float)
    X = sub[list(cols)].values.astype(float)
    if y.sum() == 0 or y.sum() == len(y):
        return None
    auc, coefs = cv_auc(X, y, folds)
    return {"cols": cols, "n": len(sub), "auc": auc, "coefs": coefs}


def single_table(df: pd.DataFrame, threshold: float):
    rows = []
    for col in SENTIMENT_COLUMNS:
        sub = df.dropna(subset=[col])
        y = (sub[TARGET].values >= threshold).astype(float)
        x = sub[col].values.astype(float)
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
            "col": col, "n": len(sub), "auc": auc,
            "direction": "higher->bigger" if auc >= 0.5 else "lower->bigger",
            "strength": abs(auc - 0.5),
            "pointbiserial": pb, "spearman_raw": sp,
        })
    rows.sort(key=lambda r: r["strength"], reverse=True)
    return rows


def threshold_sweep(df: pd.DataFrame, thresholds):
    table = {}
    for col in SENTIMENT_COLUMNS:
        sub = df.dropna(subset=[col])
        x = sub[col].values.astype(float)
        table[col] = {}
        for t in thresholds:
            y = (sub[TARGET].values >= t).astype(float)
            table[col][t] = auc_score(x, y) if 0 < y.sum() < len(y) else float("nan")
    counts = {t: int((df[TARGET].values >= t).sum()) for t in thresholds}
    return table, counts


def collinearity(df: pd.DataFrame):
    sub = df.dropna(subset=SENTIMENT_COLUMNS)
    return sub[SENTIMENT_COLUMNS].corr(method="spearman")


# ----------------------------- plots -----------------------------------------
def save_plotext(out_path: pathlib.Path):
    txt = plt.build()
    plt.show()
    print()
    out_path.write_text(txt, encoding="utf-8")
    print(f"  saved -> {out_path}")


def plot_roc(df, cols, threshold, out_dir):
    if plt is None:
        return
    plt.clf()
    plt.title(f"ROC curves (big mover = DailyHigh%% >= {threshold})")
    plt.xlabel("False positive rate")
    plt.ylabel("True positive rate")
    plt.plotsize(100, 30)
    plt.plot([0, 1], [0, 1], color="gray")
    for col in cols:
        sub = df.dropna(subset=[col])
        y = (sub[TARGET].values >= threshold).astype(float)
        x = sub[col].values.astype(float)
        if auc_score(x, y) < 0.5:
            x = -x  # orient so the curve bows above the diagonal
        fpr, tpr = roc_curve(x, y)
        plt.plot(fpr.tolist(), tpr.tolist(), label=col)
    save_plotext(out_dir / "roc_top_singles.plotext")


def plot_strip(df, col, threshold, out_dir):
    if plt is None:
        return
    sub = df.dropna(subset=[col])
    y = sub[TARGET].values >= threshold
    x = sub[col].values.astype(float)
    rng = np.random.default_rng(0)
    plt.clf()
    plt.title(f"{col} by group (big mover >= {threshold}%)")
    plt.xlabel(col)
    plt.ylabel("group (jittered)")
    plt.plotsize(100, 25)
    plt.scatter(x[~y].tolist(), (0 + rng.uniform(-0.15, 0.15, (~y).sum())).tolist(),
                color="blue", marker="dot", label="not big")
    plt.scatter(x[y].tolist(), (1 + rng.uniform(-0.15, 0.15, y.sum())).tolist(),
                color="red", marker="dot", label="big mover")
    save_plotext(out_dir / f"strip_{col}.plotext")


def plot_pair(df, cols, threshold, out_dir):
    if plt is None or len(cols) != 2:
        return
    sub = df.dropna(subset=list(cols))
    y = sub[TARGET].values >= threshold
    c0, c1 = cols
    plt.clf()
    plt.title(f"{c0} vs {c1} (big mover >= {threshold}%)")
    plt.xlabel(c0)
    plt.ylabel(c1)
    plt.plotsize(100, 30)
    plt.scatter(sub[c0][~y].tolist(), sub[c1][~y].tolist(),
                color="blue", marker="dot", label="not big")
    plt.scatter(sub[c0][y].tolist(), sub[c1][y].tolist(),
                color="red", marker="dot", label="big mover")
    save_plotext(out_dir / f"pair_{c0}_{c1}.plotext")


# ----------------------------- report ----------------------------------------
def fmt_combined(singles, combos):
    leaderboard = []
    for r in singles:
        leaderboard.append((r["auc"], 1, " + ".join([r["col"]]), r["n"], None))
    for r in combos:
        leaderboard.append((r["auc"], len(r["cols"]), " + ".join(r["cols"]),
                            r["n"], r["coefs"]))
    leaderboard.sort(key=lambda t: (t[0] if not np.isnan(t[0]) else -1), reverse=True)
    return leaderboard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="?",
                    default="fconcatenated_enriched_FinBERT_filtered-50float-12high.tsv")
    ap.add_argument("--threshold", type=float, default=20.0,
                    help="DailyHigh(%%) cutoff defining a 'big mover'")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--plots", action="store_true")
    args = ap.parse_args()

    tsv_path = pathlib.Path(args.tsv)
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")
    out_dir = tsv_path.parent if str(tsv_path.parent) != "" else pathlib.Path(".")

    df = load_data(tsv_path)
    thr = args.threshold
    n_big = int((df[TARGET].values >= thr).sum())

    L = []  # report lines

    def emit(line=""):
        print(line)
        L.append(line)

    emit(f"# DailyHigh(%) predictor analysis — {tsv_path.name}")
    emit()
    emit(f"- Usable rows (target present): **{len(df)}**")
    emit(f"- Big-mover threshold: **DailyHigh(%) >= {thr}**")
    emit(f"- Big movers: **{n_big}** ({100*n_big/len(df):.1f}%)  |  rest: {len(df)-n_big}")
    emit(f"- CV folds for combos: {args.folds}")
    emit(f"- Metric: ROC AUC (0.50 = no signal, 1.0 = perfect). Combos use pooled out-of-fold AUC.")
    emit()

    # singles
    singles = single_table(df, thr)
    emit("## Single-column ranking (parameter-free AUC)")
    emit()
    emit("| rank | column | n | AUC | direction | point-biserial r | Spearman vs raw % |")
    emit("|---|---|---|---|---|---|---|")
    for i, r in enumerate(singles, 1):
        emit(f"| {i} | {r['col']} | {r['n']} | {r['auc']:.3f} | {r['direction']} "
             f"| {r['pointbiserial']:+.3f} | {r['spearman_raw']:+.3f} |")
    emit()

    # combos
    combos = []
    for k in (2, 3):
        for cols in combinations(SENTIMENT_COLUMNS, k):
            res = evaluate_combo(df, cols, None, thr, args.folds)
            if res:
                combos.append(res)
    combos.sort(key=lambda r: (r["auc"] if not np.isnan(r["auc"]) else -1), reverse=True)

    emit("## Combo ranking (2- & 3-column, cross-validated AUC)")
    emit()
    emit("| rank | columns | n | CV-AUC | logistic coefs (standardized) |")
    emit("|---|---|---|---|---|")
    for i, r in enumerate(combos[:15], 1):
        coef_str = ", ".join(f"{c}={w:+.2f}" for c, w in zip(r["cols"], r["coefs"]))
        emit(f"| {i} | {' + '.join(r['cols'])} | {r['n']} | {r['auc']:.3f} | {coef_str} |")
    emit()

    # combined leaderboard
    emit("## Combined leaderboard (top 12, all sizes — singles use raw AUC, combos use CV-AUC)")
    emit()
    emit("| rank | features | size | AUC |")
    emit("|---|---|---|---|")
    for i, (auc, size, name, n, coefs) in enumerate(fmt_combined(singles, combos)[:12], 1):
        emit(f"| {i} | {name} | {size} | {auc:.3f} |")
    emit()

    # collinearity
    emit(f"## Collinearity among the {len(SENTIMENT_COLUMNS)} scores (Spearman)")
    emit()
    corr = collinearity(df)
    header = "| | " + " | ".join(c[:10] for c in SENTIMENT_COLUMNS) + " |"
    emit(header)
    emit("|" + "---|" * (len(SENTIMENT_COLUMNS) + 1))
    for c in SENTIMENT_COLUMNS:
        emit(f"| {c[:14]} | " + " | ".join(f"{corr.loc[c, c2]:.2f}" for c2 in SENTIMENT_COLUMNS) + " |")
    emit()

    # threshold sensitivity
    thresholds = [10, 20, 30, 50]
    sweep, counts = threshold_sweep(df, thresholds)
    emit("## Threshold sensitivity — single-column AUC at different cutoffs")
    emit()
    emit("| column | " + " | ".join(f">= {t}%" for t in thresholds) + " |")
    emit("|---|" + "---|" * len(thresholds))
    for col in SENTIMENT_COLUMNS:
        emit(f"| {col} | " + " | ".join(f"{sweep[col][t]:.3f}" for t in thresholds) + " |")
    emit(f"| _n big movers_ | " + " | ".join(str(counts[t]) for t in thresholds) + " |")
    emit()

    # conclusion
    best_single = singles[0] if singles else None
    best_combo = combos[0] if combos else None
    emit("## Conclusion")
    emit()
    if best_single:
        emit(f"- **Best single predictor:** `{best_single['col']}` "
             f"(AUC {best_single['auc']:.3f}, {best_single['direction']}).")
    if best_combo:
        lift = (best_combo["auc"] - best_single["auc"]) if best_single else float("nan")
        emit(f"- **Best combo:** `{' + '.join(best_combo['cols'])}` "
             f"(CV-AUC {best_combo['auc']:.3f}; lift over best single: {lift:+.3f}).")
    if best_single and abs(best_single["auc"] - 0.5) < 0.05:
        emit("- ⚠️ All AUCs sit near 0.50 — these sentiment scores carry **little signal** "
             "for DailyHigh(%) at this threshold. Treat any ranking below as weak.")
    emit("- The 6 scores are derivatives of the same FinBERT output, so they are highly "
         "collinear (see matrix); combos rarely beat the best single by much.")
    emit()

    report_path = out_dir / "dailyhigh_predictor_ranking.md"
    report_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nReport written -> {report_path}")

    # plots
    if args.plots:
        if plt is None:
            print("plotext not installed — skipping plots.")
        else:
            top_single_cols = [r["col"] for r in singles[:3]]
            print("\n=== plots ===")
            plot_roc(df, top_single_cols, thr, out_dir)
            if singles:
                plot_strip(df, singles[0]["col"], thr, out_dir)
            best_pair = next((r["cols"] for r in combos if len(r["cols"]) == 2), None)
            if best_pair:
                plot_pair(df, best_pair, thr, out_dir)


if __name__ == "__main__":
    main()
