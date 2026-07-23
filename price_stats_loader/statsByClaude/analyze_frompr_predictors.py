#!/usr/bin/env python3
"""Rank EVERY usable column of a PR-stats TSV by how well it predicts
fromPR_High(%) (or any --target), in one unified report.

Unlike analyze_dailyhigh_predictors.py (fixed 16-column list) and
analyze_all_columns_dailyhigh.py (fixed lists, DailyHigh only), this script
auto-detects its candidates so new columns — the 11 encoder score blocks,
the _hl/_pctl keyword variants, FineClass/BroadClass — are picked up without
editing a list:

  * Numeric — every non-blocklisted column that coerces to >= --min-n numeric
    values; scored parameter-free with the Mann-Whitney rank AUC on the binary
    target (target >= --threshold), plus Spearman vs the raw target and
    Pearson vs a signed-log target (sign(t)*log1p(|t|), tames the ~450% tail).
  * Categorical — a fixed whitelist (label, Author, Exchange,
    boilerplate_category, FineClass, BroadClass) plus every *_label column;
    top-K levels one-hot encoded, scored with pooled out-of-fold logistic
    CV-AUC, and broken down per level (big-mover rate + median target).
  * Combos — 2- and 3-column logistic combos, but only among the --top-n best
    numeric singles; the pool skips near-clones (|Spearman| >= --pool-dedup-rho
    vs a column already pooled) so it isn't 15 copies of the same score.

The blocklist drops outcome/leakage columns (DailyHigh, fromPR_HighTime,
Trades/sec, Trigger, live clerk artifacts) and operational timestamps —
same exclusions as the sibling scripts.

Target cleaning follows dailyhigh_predictor.py: drop NaN targets, drop
> --max-outlier rows, dedup on (Symbol, Headline, ArrivalDate).

Pure numpy / pandas / scipy — no sklearn, no matplotlib.

Run:
    /home/tom/venv/bin/python analyze_frompr_predictors.py \
        ../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv
"""
import argparse
import pathlib
import re
import sys
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import rankdata, pearsonr, spearmanr

TARGET_DEFAULT = "fromPR_High(%)"

# Outcome / leakage / operational columns — never predictors.
BLOCKLIST = {
    "DailyHigh(%)", "DailyHigh($)", "fromPR_High(%)", "fromPR_HighTime(m:s)",
    "fromPR_status", "Trades/sec", "Trigger", "Sentiment", "ClerkArm",
    "ClerkSentiment",
    "Symbol", "Tickers", "ID", "Headline", "Body",
    "ArrivalTime", "ArrivalDate", "Created", "CurlTime", "OrchTime",
    "body_duration_ms", "body_sentiment",
}
BLOCK_SUFFIXES = ("CompletedAt", "_elapsed_ms")

CATEGORICAL_BASE = ["label", "Author", "Exchange", "boilerplate_category",
                    "FineClass", "BroadClass"]

MIN_LEVEL_N = 20  # per-level breakdown rows


# ----------------------------- data -----------------------------------------
def load_and_clean(tsv_path, target, max_outlier):
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    if target not in df.columns:
        sys.exit(f"target column {target!r} not in {tsv_path}")
    df[target] = pd.to_numeric(df[target], errors="coerce")

    n_total = len(df)
    df = df.dropna(subset=[target])
    n_null = n_total - len(df)

    n_out = int((df[target] > max_outlier).sum())
    df = df[df[target] <= max_outlier]

    dedup_keys = [c for c in ("Symbol", "Headline", "ArrivalDate") if c in df.columns]
    n_dupe = 0
    if dedup_keys:
        before = len(df)
        df = df.drop_duplicates(subset=dedup_keys, keep="first")
        n_dupe = before - len(df)

    df = df.reset_index(drop=True)
    return df, {"total": n_total, "null_target": n_null,
                "outliers": n_out, "dupes": n_dupe}


def is_blocked(col, target):
    return (col == target or col in BLOCKLIST
            or col.endswith(BLOCK_SUFFIXES))


def detect_columns(df, target, min_n):
    """Split columns into (numeric candidates, categorical candidates)."""
    categorical = [c for c in df.columns
                   if not is_blocked(c, target)
                   and (c in CATEGORICAL_BASE or c.endswith("_label"))]
    numeric = []
    for col in df.columns:
        if is_blocked(col, target) or col in categorical:
            continue
        vals = pd.to_numeric(df[col], errors="coerce")
        if vals.notna().sum() >= min_n and vals.nunique() > 1:
            df[col] = vals
            numeric.append(col)
    return numeric, categorical


# ----------------------------- metrics --------------------------------------
def auc_score(scores, labels):
    """ROC AUC via the Mann-Whitney rank formula (handles ties)."""
    labels = labels.astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    return (ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def signed_log(t):
    return np.sign(t) * np.log1p(np.abs(t))


# ----------------------------- logistic --------------------------------------
def fit_logistic(X, y, l2=1e-3):
    """Penalized logistic regression; w[0] = intercept, X pre-standardized."""
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


def cv_auc(X, y, folds, seed=42):
    """Pooled out-of-fold AUC + full-data coefficients (interpretation only)."""
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
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd[sd == 0] = 1.0
    w_full = fit_logistic((X - mu) / sd, y)
    return auc_score(oof, y), w_full[1:]


# ----------------------------- scoring families -----------------------------
def score_numeric(df, numeric_cols, target, threshold, min_n):
    rows = []
    t_all = df[target].values
    for col in numeric_cols:
        sub = df.dropna(subset=[col])
        if len(sub) < min_n:
            continue
        x = sub[col].values.astype(float)
        t = sub[target].values
        y = (t >= threshold).astype(float)
        if y.sum() == 0 or y.sum() == len(y):
            continue
        auc = auc_score(x, y)
        try:
            sp = spearmanr(x, t).correlation
        except Exception:
            sp = float("nan")
        try:
            pr = pearsonr(x, signed_log(t))[0]
        except Exception:
            pr = float("nan")
        rows.append({
            "kind": "numeric", "feature": col, "n": len(sub),
            "auc": auc, "strength": abs(auc - 0.5),
            "direction": "higher->bigger" if auc >= 0.5 else "lower->bigger",
            "spearman": sp, "pearson_slog": pr,
        })
    rows.sort(key=lambda r: r["strength"], reverse=True)
    return rows


def cap_levels(series, top_k):
    s = series.fillna("<missing>").astype(str)
    top = s.value_counts().head(top_k).index
    return s.where(s.isin(top), "<other>")


def score_categorical(df, cat_cols, target, threshold, folds, top_k):
    rows = []
    y_full = (df[target].values >= threshold).astype(float)
    for col in cat_cols:
        s = cap_levels(df[col], top_k=top_k)
        dummies = pd.get_dummies(s, drop_first=True)
        if dummies.shape[1] == 0 or y_full.sum() in (0, len(y_full)):
            continue
        auc, _ = cv_auc(dummies.values.astype(float), y_full, folds)

        level_stats = []
        tmp = pd.DataFrame({"lvl": s, "y": y_full, "t": df[target].values})
        for lvl, g in tmp.groupby("lvl"):
            if len(g) < MIN_LEVEL_N:
                continue
            level_stats.append((str(lvl), int(len(g)),
                                float(g["y"].mean()), float(g["t"].median())))
        level_stats.sort(key=lambda r: -r[2])

        rows.append({
            "kind": "categorical", "feature": col, "n": int(len(df)),
            "auc": auc, "strength": abs(auc - 0.5), "direction": "—",
            "levels": level_stats,
        })
    rows.sort(key=lambda r: r["strength"], reverse=True)
    return rows


def build_combo_pool(df, singles, top_n, dedup_rho):
    """Top-N numeric singles, skipping near-clones of already-pooled columns."""
    pool, skipped = [], []
    for r in singles:
        if len(pool) >= top_n:
            break
        col = r["feature"]
        clone_of = None
        if dedup_rho < 1.0:
            for p in pool:
                sub = df.dropna(subset=[col, p])
                if len(sub) < 3:
                    continue
                rho = spearmanr(sub[col].values, sub[p].values).correlation
                if not np.isnan(rho) and abs(rho) >= dedup_rho:
                    clone_of = p
                    break
        if clone_of is None:
            pool.append(col)
        else:
            skipped.append((col, clone_of))
    return pool, skipped


def score_combos(df, pool, target, threshold, folds):
    combos = []
    for k in (2, 3):
        for cols in combinations(pool, k):
            sub = df.dropna(subset=list(cols))
            y = (sub[target].values >= threshold).astype(float)
            if y.sum() == 0 or y.sum() == len(y):
                continue
            auc, coefs = cv_auc(sub[list(cols)].values.astype(float), y, folds)
            combos.append({"cols": cols, "n": len(sub), "auc": auc, "coefs": coefs})
    combos.sort(key=lambda r: (r["auc"] if not np.isnan(r["auc"]) else -1),
                reverse=True)
    return combos


def threshold_sweep(df, cols, target, thresholds):
    table = {}
    for col in cols:
        sub = df.dropna(subset=[col])
        x = sub[col].values.astype(float)
        t = sub[target].values
        table[col] = {}
        for thr in thresholds:
            y = (t >= thr).astype(float)
            table[col][thr] = (auc_score(x, y)
                               if 0 < y.sum() < len(y) else float("nan"))
    counts = {thr: int((df[target].values >= thr).sum()) for thr in thresholds}
    return table, counts


# ----------------------------- main -----------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="?",
                    default="../outputs/PRstats_2026-06-20-2026-07-16_"
                            "CoffeeFilter_11EncScored_Classed.tsv")
    ap.add_argument("--target", default=TARGET_DEFAULT)
    ap.add_argument("--threshold", type=float, default=30.0,
                    help="target cutoff defining a 'big mover'")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--top-n", type=int, default=15,
                    help="combo pool size (best numeric singles)")
    ap.add_argument("--top-k-levels", type=int, default=15,
                    help="categoricals: most-frequent levels kept, rest -> <other>")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--max-outlier", type=float, default=500.0,
                    help="drop rows with target above this (data errors)")
    ap.add_argument("--pool-dedup-rho", type=float, default=0.95,
                    help="skip pool candidates with |Spearman| >= this vs an "
                         "already-pooled column; >=1 disables")
    ap.add_argument("--subset-null", metavar="COL",
                    help="analyze ONLY rows where COL is empty/non-numeric "
                         "(e.g. model_proba -> the un-keyword-scored block of "
                         "a concatenated corpus)")
    ap.add_argument("--subset-date-from", metavar="YYYY-MM-DD",
                    help="analyze ONLY rows whose --date-col is >= this date. "
                         "Use to isolate the keyword model's out-of-sample "
                         "block once every row has been scored (the training "
                         "corpus carries no ArrivalDate).")
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--report-suffix", default="",
                    help="appended to the report filename (for subset runs)")
    args = ap.parse_args()

    tsv_path = pathlib.Path(args.tsv)
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")
    out_dir = tsv_path.parent if str(tsv_path.parent) else pathlib.Path(".")
    target, thr = args.target, args.threshold

    df, clean = load_and_clean(tsv_path, target, args.max_outlier)
    subset_note = ""
    if args.subset_null:
        if args.subset_null not in df.columns:
            sys.exit(f"--subset-null column {args.subset_null!r} not in file")
        mask = pd.to_numeric(df[args.subset_null], errors="coerce").isna()
        df = df[mask].reset_index(drop=True)
        subset_note = (f"- **SUBSET RUN**: only the {len(df)} rows where "
                       f"`{args.subset_null}` is empty.")
    if args.subset_date_from:
        if args.date_col not in df.columns:
            sys.exit(f"--date-col {args.date_col!r} not in file")
        d = df[args.date_col].fillna("").astype(str)
        df = df[d.ne("") & d.ge(args.subset_date_from)].reset_index(drop=True)
        subset_note = (f"- **SUBSET RUN**: only the {len(df)} rows with "
                       f"`{args.date_col}` >= {args.subset_date_from} — the "
                       f"keyword model's out-of-sample block.")
    numeric_cols, cat_cols = detect_columns(df, target, args.min_n)
    excluded = sorted(c for c in df.columns if is_blocked(c, target))

    t = df[target].values
    n_big = int((t >= thr).sum())
    base_rate = n_big / len(df)

    L = []

    def emit(line=""):
        print(line)
        L.append(line)

    emit(f"# {target} all-column predictor analysis — {tsv_path.name}")
    emit()
    if subset_note:
        emit(subset_note)
    emit(f"- Rows: {clean['total']} total -> dropped {clean['null_target']} "
         f"null-target, {clean['outliers']} outliers (> {args.max_outlier:g}%), "
         f"{clean['dupes']} duplicate PRs -> **{len(df)} kept**")
    emit(f"- {target} on kept rows: median={np.median(t):.2f}  "
         f"mean={t.mean():.2f}  max={t.max():.2f}")
    emit(f"- Big-mover threshold: **{target} >= {thr:g}** -> "
         f"**{n_big}** big movers ({100 * base_rate:.1f}%)")
    emit(f"- Candidates: {len(numeric_cols)} numeric (auto-detected), "
         f"{len(cat_cols)} categorical")
    emit(f"- Excluded (outcome/leakage/ops/id): {', '.join(excluded)}")
    emit(f"- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); "
         f"categorical & combos = pooled out-of-fold logistic AUC "
         f"({args.folds}-fold). Spearman is vs the raw target, Pearson vs "
         f"sign(t)*log1p(|t|).")
    emit()

    # --- numeric singles
    singles = score_numeric(df, numeric_cols, target, thr, args.min_n)
    emit("## Numeric singles — every auto-detected column")
    emit()
    emit("| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |")
    emit("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(singles, 1):
        emit(f"| {i} | {r['feature']} | {r['n']} | {r['auc']:.3f} "
             f"| {r['direction']} | {r['strength']:.3f} "
             f"| {r['spearman']:+.3f} | {r['pearson_slog']:+.3f} |")
    emit()

    # --- categoricals
    cats = score_categorical(df, cat_cols, target, thr, args.folds,
                             args.top_k_levels)
    emit("## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)")
    emit()
    emit("| rank | column | n | CV-AUC | |AUC-0.5| |")
    emit("|---|---|---|---|---|")
    for i, r in enumerate(cats, 1):
        emit(f"| {i} | {r['feature']} | {r['n']} | {r['auc']:.3f} "
             f"| {r['strength']:.3f} |")
    emit()

    emit("## Categorical breakdowns — per level "
         f"(min {MIN_LEVEL_N} rows; baseline big-mover rate {base_rate:.1%})")
    emit()
    for r in cats:
        emit(f"### `{r['feature']}` (CV-AUC {r['auc']:.3f})")
        emit()
        emit(f"| level | n | big-mover rate | lift vs baseline | median {target} |")
        emit("|---|---|---|---|---|")
        for lvl, n, p, med in r["levels"]:
            emit(f"| {lvl} | {n} | {p:.1%} | {p - base_rate:+.1%} | {med:.2f} |")
        emit()

    # --- combos
    pool, skipped = build_combo_pool(df, singles, args.top_n,
                                     args.pool_dedup_rho)
    combos = score_combos(df, pool, target, thr, args.folds)
    emit(f"## Combos (2- & 3-column CV-AUC) among the top-{len(pool)} "
         f"de-cloned singles pool")
    emit()
    emit(f"Pool: {', '.join(pool)}")
    if skipped:
        emit()
        emit("Skipped as near-clones (|Spearman| >= "
             f"{args.pool_dedup_rho:g} vs a pooled column): "
             + "; ".join(f"{c} ~ {p}" for c, p in skipped))
    emit()
    emit("| rank | columns | n | CV-AUC | logistic coefs (standardized) |")
    emit("|---|---|---|---|---|")
    for i, r in enumerate(combos[:20], 1):
        coef_str = ", ".join(f"{c}={w:+.2f}"
                             for c, w in zip(r["cols"], r["coefs"]))
        emit(f"| {i} | {' + '.join(r['cols'])} | {r['n']} | {r['auc']:.3f} "
             f"| {coef_str} |")
    emit()

    # --- combined leaderboard
    board = ([(r["auc"], 1, r["feature"] + (" (cat)" if r["kind"] == "categorical"
                                            else "")) for r in singles + cats]
             + [(r["auc"], len(r["cols"]), " + ".join(r["cols"])) for r in combos])
    board.sort(key=lambda x: abs((x[0] if not np.isnan(x[0]) else 0.5) - 0.5),
               reverse=True)
    emit("## Combined leaderboard (top 15, all kinds — singles use raw AUC, "
         "combos/cats use CV-AUC)")
    emit()
    emit("| rank | features | size | AUC |")
    emit("|---|---|---|---|")
    for i, (auc, size, name) in enumerate(board[:15], 1):
        emit(f"| {i} | {name} | {size} | {auc:.3f} |")
    emit()

    # --- collinearity among the pool
    emit("## Collinearity among the combo pool (Spearman, pairwise rows)")
    emit()
    corr = df[pool].corr(method="spearman", min_periods=args.min_n)
    emit("| | " + " | ".join(c[:12] for c in pool) + " |")
    emit("|" + "---|" * (len(pool) + 1))
    for c in pool:
        emit(f"| {c[:16]} | "
             + " | ".join("—" if np.isnan(corr.loc[c, c2]) else
                          f"{corr.loc[c, c2]:.2f}" for c2 in pool) + " |")
    emit()

    # --- threshold sweep
    thresholds = [10, 15, 20, 30, 50]
    sweep, counts = threshold_sweep(df, pool, target, thresholds)
    emit("## Threshold sensitivity — pool columns' AUC at different cutoffs")
    emit()
    emit("| column | " + " | ".join(f">= {x}%" for x in thresholds) + " |")
    emit("|---|" + "---|" * len(thresholds))
    for col in pool:
        emit(f"| {col} | "
             + " | ".join(f"{sweep[col][x]:.3f}" for x in thresholds) + " |")
    emit("| _n big movers_ | "
         + " | ".join(str(counts[x]) for x in thresholds) + " |")
    emit()

    # --- conclusion
    best_single = singles[0] if singles else None
    best_cat = cats[0] if cats else None
    best_combo = combos[0] if combos else None
    emit("## Conclusion")
    emit()
    if best_single:
        emit(f"- **Best numeric single:** `{best_single['feature']}` "
             f"(AUC {best_single['auc']:.3f}, {best_single['direction']}).")
    if best_cat:
        emit(f"- **Best categorical:** `{best_cat['feature']}` "
             f"(CV-AUC {best_cat['auc']:.3f}).")
    if best_combo and best_single:
        lift = best_combo["auc"] - best_single["auc"]
        emit(f"- **Best combo:** `{' + '.join(best_combo['cols'])}` "
             f"(CV-AUC {best_combo['auc']:.3f}; lift over best single: "
             f"{lift:+.3f}).")
    if best_single and best_single["strength"] < 0.05:
        emit("- ⚠️ All AUCs sit near 0.50 — little signal at this threshold; "
             "treat the ranking as relative, not absolute.")
    emit("- Many numeric columns are monotone derivatives of the same score "
         "(pctl twins, encoder families) — see the de-cloned pool and the "
         "collinearity matrix before reading much into small AUC gaps.")
    emit()

    target_slug = re.sub(r"[^A-Za-z0-9]+", "", target).lower()
    report_path = out_dir / (f"{target_slug}_all_predictors_report"
                             f"{args.report_suffix}.md")
    report_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nReport written -> {report_path}")


if __name__ == "__main__":
    main()
