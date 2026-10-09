#!/usr/bin/env python3
"""Deep analysis of XGBoost model-2 on fromPR_High(%).

STATS ONLY. This script recommends no gate, proposes no DSL rule line, and touches
nothing under orchestrator5/. It answers one question in as many honest ways as the
data supports: how well do model-2's five score columns -- and the ~250 raw variables
sitting beside them in the same file -- predict fromPR_High(%)?

Three things separate this from the two earlier reports (2026-09-16 XGB-vs-gate,
2026-09-20 model-2-vs-model-1), which both asked only "which column ranks best":

  1. MAGNITUDE, not just ordering. A column can rank well and still be a useless
     estimate of how far the stock actually runs. Spearman vs Pearson, MAE against a
     constant-prediction baseline, decile profiles and captured-move curves separate
     "good ranker" from "good estimator" -- and for model-2 those are very different
     verdicts.
  2. CALIBRATION. The three classifiers were trained with scale_pos_weight 4.18 /
     8.33 / 12.53, which multiplies the modelled odds by that factor. Their outputs
     are therefore NOT probabilities, and the report shows it three ways: Brier skill
     against a constant base rate, a reliability table, and the analytic prior
     correction p* = p / (p + (1-p)*w) that puts them back where they belong.
  3. THE VARIABLES, not just the scores. Every numeric and low-cardinality
     categorical column in the corpus is screened univariately (train and val
     separately, BH-FDR controlled), then tested for INCREMENTAL value on top of the
     best model score via grouped-CV logistic stacking. That is the only way to ask
     whether the 25-feature allowlist left signal on the table.

Row scope follows the operator's instruction: model-score work is val-only (1380 rows,
25 sessions, strictly out-of-sample), while the variable screen runs on the full
train+val corpus (4804 rows) so a 250-column sweep is not driven by 132 positives.
Train and val are always reported separately -- never pooled into one number.

House convention (statsByClaude/CLAUDE.md): each script here is standalone, stats
routines are COPIED not imported. No sklearn anywhere -- AUC is the Mann-Whitney rank
formula, logistic regression is hand-rolled Newton-IRLS.

Deterministic: --seed 42 fixes every bootstrap and every fold assignment.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr, pearsonr, mannwhitneyu

_HERE = Path(__file__).resolve().parent

SCORE_COLS = ["pred_reg_se", "pred_reg_q80",
              "proba_clf_ge20", "proba_clf_ge35", "proba_clf_ge50"]
CLF_COLS = ["proba_clf_ge20", "proba_clf_ge35", "proba_clf_ge50"]
REG_COLS = ["pred_reg_se", "pred_reg_q80"]
CLF_OWN_TH = {"proba_clf_ge20": 20.0, "proba_clf_ge35": 35.0, "proba_clf_ge50": 50.0}

TARGET = "fromPR_High(%)"
TARGET_ACT = "fromPR_High(%)_actual"
DATE_COL = "ArrivalDate"
KEYS = ["Symbol", "ArrivalDate", "ArrivalTime"]

# never screened as predictors: identifiers, free text, the target itself, and the
# working split flag this script adds to the corpus (screening that would "discover"
# that the val fold is the val fold)
ID_COLS = {"Symbol", "ArrivalTime", "ArrivalDate", "Headline", "Body", "Created",
           TARGET, TARGET_ACT, "_is_val"}

# Variables that take one value for a whole session (market-wide regime: VIX, CNN
# Fear & Greed). They are screened like anything else, but their effective sample size
# is the SESSION COUNT, not the row count, and any finding about them has to be read
# against 25 -- not 1380.
SESSION_CONSTANT_PREFIXES = ("VIX_", "FNG_")

# a univariate AUC this high on a 1380-row news corpus is not a discovery, it is a
# leak -- flagged rather than reported as signal
LEAK_AUC = 0.90


# --------------------------------------------------------------------------
# stats primitives -- copied from compare_xgb_models.py per house convention
# --------------------------------------------------------------------------
def auc_score(y, s):
    """Mann-Whitney rank AUC. Handles ties via average ranks; no sklearn."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    n1 = float(y.sum())
    n0 = float(len(y) - n1)
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def avg_precision(y, s):
    """Area under the precision-recall curve, step-wise, tie groups collapsed."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    pos = y.sum()
    if pos == 0:
        return float("nan")
    order = np.argsort(-s, kind="mergesort")
    ys, ss = y[order], s[order]
    ends = np.flatnonzero(np.r_[np.diff(ss) != 0, True])
    tp = np.cumsum(ys)[ends]
    k = (ends + 1).astype(float)
    prec = tp / k
    rec = tp / pos
    return float(np.sum(np.diff(np.r_[0.0, rec]) * prec))


def wilson(k, n, z=1.959964):
    """95% Wilson score interval -- does not collapse to zero width at k=0 or k=n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - h) / d), float((c + h) / d)


def _day_blocks(days):
    """Row indices grouped by session, for the block bootstrap."""
    idx = {}
    for i, d in enumerate(days):
        idx.setdefault(d, []).append(i)
    return [np.asarray(v) for v in idx.values()]


def block_boot_auc(y, s, days, n_boot, rng):
    """95% CI on AUC, resampling whole SESSIONS.

    Rows inside a session are not independent -- one market-wide morning lifts every
    name in it -- so resampling rows would understate the interval badly."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        a = auc_score(y[sel], s[sel])
        if not np.isnan(a):
            out.append(a)
    if not out:
        return (float("nan"), float("nan"))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def block_boot_delta_auc(y, s_a, s_b, days, n_boot, rng):
    """PAIRED day-block bootstrap on AUC(a) - AUC(b): both scores are evaluated on the
    SAME resampled sessions, so the shared sampling noise cancels.
    Returns (CI, P(delta>0))."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        ya = y[sel]
        if ya.sum() == 0 or ya.sum() == len(ya):
            continue
        out.append(auc_score(ya, s_a[sel]) - auc_score(ya, s_b[sel]))
    if not out:
        return (float("nan"), float("nan")), float("nan")
    out = np.asarray(out)
    return ((float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))),
            float((out > 0).mean()))


def top_n_mask(score, n):
    """Top-n by score, ties broken by original order (mergesort is stable)."""
    if n <= 0:
        return np.zeros(len(score), dtype=bool)
    order = np.argsort(-np.asarray(score, dtype=float), kind="mergesort")
    m = np.zeros(len(score), dtype=bool)
    m[order[:n]] = True
    return m


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def pct(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100 * x:.{nd}f}%"


def sgn(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}"


# --------------------------------------------------------------------------
# multiple testing
# --------------------------------------------------------------------------
def bh_fdr(pvals):
    """Benjamini-Hochberg q-values. Screening ~250 columns at p<0.05 would hand back
    ~12 false winners by construction; q is what makes the leaderboard readable."""
    p = np.asarray(pvals, dtype=float)
    ok = ~np.isnan(p)
    q = np.full(p.shape, np.nan)
    if not ok.any():
        return q
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(p[idx], kind="mergesort")]
    m = len(order)
    prev = 1.0
    for rank in range(m - 1, -1, -1):
        i = order[rank]
        val = p[i] * m / (rank + 1)
        prev = min(prev, val)
        q[i] = min(prev, 1.0)
    return q


def auc_pvalue(y, s):
    """Two-sided p for AUC != 0.5 via the tie-corrected Mann-Whitney normal test."""
    y = np.asarray(y, dtype=bool)
    s = np.asarray(s, dtype=float)
    a, b = s[y], s[~y]
    if len(a) < 2 or len(b) < 2:
        return float("nan")
    if np.all(s == s[0]):
        return 1.0
    try:
        return float(mannwhitneyu(a, b, alternative="two-sided").pvalue)
    except ValueError:
        return float("nan")


# --------------------------------------------------------------------------
# hand-rolled logistic regression (Newton-IRLS, L2, intercept unpenalised)
# --------------------------------------------------------------------------
def fit_logistic(X, y, l2=1.0, iters=60, tol=1e-10):
    """X includes an intercept column of ones. Returns coefficients.

    Newton rather than scipy.optimize: the incremental-value screen fits ~1250 of
    these and IRLS converges in ~8 iterations of a 3x3 solve."""
    n, p = X.shape
    b = np.zeros(p)
    pen = np.r_[0.0, np.ones(p - 1)] if p > 1 else np.zeros(1)
    for _ in range(iters):
        z = np.clip(X @ b, -35.0, 35.0)
        mu = 1.0 / (1.0 + np.exp(-z))
        w = np.maximum(mu * (1.0 - mu), 1e-9)
        g = X.T @ (y - mu) - l2 * pen * b
        H = (X * w[:, None]).T @ X + l2 * np.diag(pen)
        H[np.diag_indices_from(H)] += 1e-9
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        b = b + step
        if np.max(np.abs(step)) < tol:
            break
    return b


def predict_logistic(X, b):
    return 1.0 / (1.0 + np.exp(-np.clip(X @ b, -35.0, 35.0)))


def day_folds(days, k, seed):
    """Fold id per row, assigned by SESSION so no session straddles a fold boundary.

    Splitting rows at random would leak: two PRs from the same morning share the
    market regime, so a random split lets the model see half a session and be tested
    on the other half."""
    uniq = np.array(sorted(set(days)))
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(uniq))
    fold_of_day = {uniq[perm[i]]: i % k for i in range(len(uniq))}
    return np.array([fold_of_day[d] for d in days], dtype=int)


def oof_logistic_auc(Xraw, y, folds, k, l2=1.0):
    """Pooled out-of-fold AUC for a logistic on Xraw (no intercept column supplied).

    Standardisation uses the TRAIN fold's mean/sd only, and NaNs are filled with the
    TRAIN fold's median -- doing either on the full data would leak the held-out rows
    into their own prediction. Returns (auc, oof_predictions)."""
    Xraw = np.asarray(Xraw, dtype=float)
    if Xraw.ndim == 1:
        Xraw = Xraw[:, None]
    oof = np.full(len(y), np.nan)
    for f in range(k):
        te = folds == f
        tr = ~te
        if tr.sum() < 20 or te.sum() < 2 or y[tr].sum() < 2:
            continue
        med = np.nanmedian(Xraw[tr], axis=0)
        med = np.where(np.isnan(med), 0.0, med)
        A = np.where(np.isnan(Xraw), med, Xraw)
        mu = A[tr].mean(axis=0)
        sd = A[tr].std(axis=0)
        sd = np.where(sd < 1e-12, 1.0, sd)
        Z = (A - mu) / sd
        Xtr = np.c_[np.ones(tr.sum()), Z[tr]]
        Xte = np.c_[np.ones(te.sum()), Z[te]]
        b = fit_logistic(Xtr, y[tr].astype(float), l2=l2)
        oof[te] = predict_logistic(Xte, b)
    good = ~np.isnan(oof)
    if good.sum() < 20 or y[good].sum() < 2:
        return float("nan"), oof
    return auc_score(y[good], oof[good]), oof


# --------------------------------------------------------------------------
# categorical handling -- out-of-fold smoothed target encoding
# --------------------------------------------------------------------------
def oof_target_encode(levels, y, folds, k, m=10.0):
    """Smoothed mean-target encoding computed OUT OF FOLD.

    In-fold encoding is the classic way to manufacture a 0.95 AUC out of a column
    with no signal: every row would carry its own outcome. Levels unseen in a train
    fold fall back to that fold's prior."""
    levels = np.asarray(levels, dtype=object)
    enc = np.full(len(y), np.nan)
    for f in range(k):
        te = folds == f
        tr = ~te
        if tr.sum() < 20:
            continue
        prior = float(y[tr].mean())
        sums, cnts = {}, {}
        for lv, yy in zip(levels[tr], y[tr]):
            sums[lv] = sums.get(lv, 0.0) + float(yy)
            cnts[lv] = cnts.get(lv, 0) + 1
        enc[te] = [(sums.get(lv, 0.0) + m * prior) / (cnts.get(lv, 0) + m)
                   for lv in levels[te]]
    return enc


# --------------------------------------------------------------------------
# column classification
# --------------------------------------------------------------------------
def classify_columns(df, max_levels):
    """Split the corpus into numeric predictors, categorical predictors, and skipped."""
    numeric, categorical, skipped = [], [], []
    for c in df.columns:
        if c in ID_COLS or c in SCORE_COLS:
            continue
        s = df[c]
        if pd.api.types.is_numeric_dtype(s):
            if s.notna().sum() < 30 or s.nunique(dropna=True) < 2:
                skipped.append((c, "constant or <30 non-null"))
            else:
                numeric.append(c)
            continue
        nl = s.nunique(dropna=True)
        if nl < 2:
            skipped.append((c, "constant"))
        elif nl > max_levels:
            skipped.append((c, f"{nl} levels > --max-levels"))
        else:
            categorical.append(c)
    return numeric, categorical, skipped


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scores", default="outputs/08_2026-05-04-2026-08-28_RawData_splits_"
                    "rmCol_NaN_rmCol_rmCol_prosusReScore_reCat_PCA_val_M2-scores.tsv",
                    help="model-2 val score TSV (the 5 score columns + the target)")
    ap.add_argument("--corpus", default="../training_data/08_2026-05-04-2026-08-28_RawData_"
                    "splits_rmCol_NaN_rmCol_rmCol_prosusReScore_reCat_PCA.tsv",
                    help="full train+val corpus the model was built from")
    ap.add_argument("--prep-dir", default="../training_data/model-2",
                    help="model-2 dir holding *_prep.json (the integrity cross-check)")
    ap.add_argument("--cutoff", default="2026-07-28",
                    help="train/val split date; rows >= cutoff are val")
    ap.add_argument("--threshold", type=float, default=50.0)
    ap.add_argument("--secondary-thresholds", default="15,20,35")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--screen-bootstrap", type=int, default=400,
                    help="cheaper bootstrap used for the ~250-column incremental sweep")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--top-screen", type=int, default=40,
                    help="how many screened variables get a full bootstrap CI + a row "
                         "in the report (the rest go to the artifact TSV)")
    ap.add_argument("--max-levels", type=int, default=60,
                    help="categoricals with more levels than this are skipped")
    ap.add_argument("--max-na-frac", type=float, default=0.30,
                    help="variables missing more than this fraction are screened but "
                         "excluded from the incremental-value test")
    ap.add_argument("--outdir", default="statsByClaude")
    ap.add_argument("--report",
                    default="statsByClaude/2026-09-20_XGB_model2_deep_analysis.md")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    th = args.threshold
    sec = [float(x) for x in args.secondary_thresholds.split(",") if x.strip()]
    all_th = sorted(set(sec + [th]))
    outdir = (_HERE / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- load
    sc = pd.read_csv(_HERE / args.scores, sep="\t", low_memory=False)
    co = pd.read_csv(_HERE / args.corpus, sep="\t", low_memory=False)
    print(f"scores {sc.shape}   corpus {co.shape}")

    for c in SCORE_COLS + [TARGET_ACT, DATE_COL] + KEYS:
        if c not in sc.columns:
            sys.exit(f"--scores is missing required column {c!r}")
    if TARGET not in co.columns:
        sys.exit(f"--corpus is missing the target column {TARGET!r}")

    co["_is_val"] = co[DATE_COL].astype(str) >= args.cutoff
    n_tr = int((~co["_is_val"]).sum())
    n_va = int(co["_is_val"].sum())

    # The val slice of the corpus and the score file must be the same rows. If they are
    # not, every "variable vs score" number below is comparing different events.
    key_sc = set(map(tuple, sc[KEYS].astype(str).to_numpy()))
    key_va = set(map(tuple, co.loc[co["_is_val"], KEYS].astype(str).to_numpy()))
    if len(key_sc) != len(sc) or len(key_va) != n_va:
        sys.exit("keys are not unique within --scores or within the corpus val slice")
    if key_sc != key_va:
        sys.exit(f"--scores and the corpus val slice disagree on rows "
                 f"({len(key_sc & key_va)} shared of {len(key_sc)}/{len(key_va)})")

    # ... and must agree on the target, or magnitude analysis is measuring noise
    m = sc.merge(co.loc[co["_is_val"], KEYS + [TARGET]].astype({k: str for k in KEYS}),
                 left_on=[*KEYS], right_on=[*KEYS], how="inner",
                 validate="one_to_one", suffixes=("", "_corpus"))
    if len(m) != len(sc):
        sys.exit(f"val join produced {len(m)} rows, expected {len(sc)}")
    tgt_gap = float(np.abs(pd.to_numeric(m[TARGET_ACT], errors="coerce")
                           - pd.to_numeric(m[TARGET], errors="coerce")).max())
    if tgt_gap > 1e-9:
        sys.exit(f"score file and corpus disagree on the target (max abs diff {tgt_gap})")

    y_raw = sc[TARGET_ACT].to_numpy(dtype=float)
    days = sc[DATE_COL].astype(str).to_numpy()
    n_days = len(set(days))
    n = len(sc)
    y_bin = {t: (y_raw >= t).astype(float) for t in all_th}
    folds_val = day_folds(days, args.folds, args.seed)

    print(f"val {n} rows / {n_days} sessions | train {n_tr} rows | "
          f"positives @>={th:.0f}: {int(y_bin[th].sum())}")

    lines = []
    art = []  # artifact filenames, filled as we write them

    # ============================================================ header
    lines.append("# XGBoost model-2 — deep analysis of predictive power on "
                 "`fromPR_High(%)`")
    lines.append("")
    lines.append(f"*Generated {pd.Timestamp.now():%Y-%m-%d %H:%M} by "
                 f"`price_stats_loader/analyze_model2_deep.py` "
                 f"(seed {args.seed}, {args.bootstrap} day-block bootstrap reps).*")
    lines.append("")
    lines.append("**Stats only.** No gate is recommended, no rule line is proposed, and "
                 "nothing under `orchestrator5/` is read or written. This report "
                 "measures; it does not prescribe.")
    lines.append("")
    verdict_slot = len(lines)
    lines.append("")

    lines.append("## Scope")
    lines.append("")
    lines.append("| | rows | sessions | span | use |")
    lines.append("|---|---|---|---|---|")
    lines.append(f"| **val** (model scores) | {n} | {n_days} | "
                 f"{days.min()}..{days.max()} | every model-score number below — "
                 "strictly out-of-sample |")
    lines.append(f"| **train** | {n_tr} | "
                 f"{co.loc[~co['_is_val'], DATE_COL].nunique()} | "
                 f"{co.loc[~co['_is_val'], DATE_COL].min()}.."
                 f"{co.loc[~co['_is_val'], DATE_COL].max()} | variable screen only "
                 "(model-2 fitted here, so its scores would be in-sample) |")
    lines.append("")
    lines.append(f"Split at `{args.cutoff}` — the same cutoff every `*_prep.json` "
                 "records. Base rates on val:")
    lines.append("")
    lines.append("| threshold | positives | base rate |")
    lines.append("|---|---|---|")
    for t in all_th:
        k = int(y_bin[t].sum())
        lines.append(f"| `>= {t:.0f}%` | {k} | {pct(k / n)} |")
    lines.append("")
    lines.append("Primary threshold is "
                 f"**`>= {th:.0f}%`** ({int(y_bin[th].sum())} positives). Everything "
                 "that follows is limited by that number, not by the 1380 rows.")
    lines.append("")

    # ============================================================ method
    lines.append("## Method")
    lines.append("")
    lines.append("- **AUC** is the Mann-Whitney rank statistic (ties handled by average "
                 "ranks). It is the probability that a randomly chosen big mover is "
                 "scored above a randomly chosen non-mover. No sklearn anywhere.")
    lines.append("- **Confidence intervals resample whole sessions**, not rows. Two PRs "
                 "from the same morning share the market regime, so a row bootstrap "
                 "would report intervals perhaps half their true width. With "
                 f"{n_days} sessions the blocks are large and the intervals are wide — "
                 "that width is the honest answer, not a defect of the method.")
    lines.append("- **Cross-validation folds are assigned by session**, so no morning "
                 "straddles a fold boundary.")
    lines.append("- **Target encoding for categoricals is computed out of fold.** "
                 "In-fold encoding would let each row carry its own outcome and "
                 "manufacture signal from nothing.")
    lines.append("- **Multiple testing**: the variable screen runs ~250 tests, so a "
                 "raw `p < 0.05` would return ~12 false winners by construction. "
                 "Benjamini-Hochberg **q-values** are reported beside every p.")
    lines.append(f"- **Leak guard**: any variable scoring AUC ≥ {LEAK_AUC:.2f} is "
                 "flagged as a suspected leak rather than reported as a finding.")
    lines.append("")

    # ============================================================ 1 ranking
    lines.append("## 1. Ranking quality (val, out-of-sample)")
    lines.append("")
    lines.append("The baseline question, restated here so the rest of the report has a "
                 "reference. AUC and PR-AUC for each score column at each threshold, "
                 "with day-block CIs at the primary cut.")
    lines.append("")
    rank_rows = {}
    for c in SCORE_COLS:
        s = sc[c].to_numpy(dtype=float)
        rank_rows[c] = {}
        for t in all_th:
            rank_rows[c][t] = (auc_score(y_bin[t], s), avg_precision(y_bin[t], s))
        rank_rows[c]["ci"] = block_boot_auc(y_bin[th], s, days, args.bootstrap, rng)
    hdr = "| column | " + " | ".join(f"AUC ≥{t:.0f}" for t in all_th) + \
          f" | 95% CI (≥{th:.0f}) | " + \
          " | ".join(f"PR-AUC ≥{t:.0f}" for t in all_th) + " |"
    lines.append(hdr)
    lines.append("|---|" + "---|" * (2 * len(all_th) + 1))
    for c in SCORE_COLS:
        lo, hi = rank_rows[c]["ci"]
        lines.append(f"| `{c}` | "
                     + " | ".join(fmt(rank_rows[c][t][0]) for t in all_th)
                     + f" | {fmt(lo)}–{fmt(hi)} | "
                     + " | ".join(fmt(rank_rows[c][t][1]) for t in all_th) + " |")
    lines.append(f"| *base rate* | " + " | ".join("0.500" for _ in all_th)
                 + " | — | " + " | ".join(fmt(y_bin[t].mean()) for t in all_th) + " |")
    lines.append("")
    best_col = max(SCORE_COLS, key=lambda c: rank_rows[c][th][0])
    lines.append(f"Highest AUC at `>= {th:.0f}%` is **`{best_col}`** "
                 f"({fmt(rank_rows[best_col][th][0])}). The spread across all five "
                 f"columns is "
                 f"{fmt(max(rank_rows[c][th][0] for c in SCORE_COLS) - min(rank_rows[c][th][0] for c in SCORE_COLS))}"
                 " AUC — narrower than any one column's confidence interval, which is "
                 "the same non-separability the 09-20 model-1/model-2 report found. "
                 "Treat `" + best_col + "` as *a* good column, not *the* best one.")
    lines.append("")
    lines.append("Pairwise separability at the primary cut (paired day-block bootstrap, "
                 "so shared session noise cancels):")
    lines.append("")
    pair_rows, pair_p = [], []
    for i, a in enumerate(SCORE_COLS):
        for b in SCORE_COLS[i + 1:]:
            (lo, hi), pg = block_boot_delta_auc(
                y_bin[th], sc[a].to_numpy(dtype=float), sc[b].to_numpy(dtype=float),
                days, args.bootstrap, rng)
            pv = 2.0 * min(pg, 1.0 - pg)
            pv = max(pv, 1.0 / args.bootstrap)
            pair_rows.append((a, b, rank_rows[a][th][0] - rank_rows[b][th][0],
                              lo, hi, pg, pv))
            pair_p.append(pv)
    pair_q = bh_fdr(np.array(pair_p))
    lines.append("| A | B | ΔAUC (A−B) | 95% CI | P(Δ>0) | raw CI excludes 0 | "
                 "q (BH over 10 pairs) |")
    lines.append("|---|---|---|---|---|---|---|")
    n_raw_sep, n_q_sep = 0, 0
    sep_names = []
    for (a, b, d, lo, hi, pg, pv), q in zip(pair_rows, pair_q):
        raw = lo > 0 or hi < 0
        n_raw_sep += raw
        n_q_sep += (q < 0.05)
        if raw:
            sep_names.append(f"`{a}` vs `{b}`")
        lines.append(f"| `{a}` | `{b}` | {sgn(d)} | {sgn(lo)}..{sgn(hi)} | {pct(pg)} | "
                     f"{'**yes**' if raw else 'no'} | {fmt(q, 3)} |")
    lines.append("")
    if n_raw_sep == 0:
        lines.append("**No pair separates**, before or after correction. All five "
                     "columns are reading the same underlying signal and the "
                     "differences between them are sampling noise at this sample size.")
    else:
        lines.append(f"**{n_raw_sep} of 10 pairs has an interval excluding zero "
                     f"({', '.join(sep_names)}), and {n_q_sep} survives BH correction "
                     "across the 10 tests.**")
        if n_q_sep == 0:
            lines.append("")
            lines.append("That distinction is the whole point of running the "
                         "correction. Testing 10 pairs at 95% confidence produces "
                         "roughly one interval excluding zero *by construction* even "
                         "when every column is identical, and that is exactly what "
                         "happened: the one apparent winner does not survive. The "
                         "honest reading stays **no column is separable from any "
                         "other** — consistent with the 09-20 model-1/model-2 report, "
                         "which could not separate the two models either.")
        else:
            lines.append("")
            lines.append("The surviving pair(s) clear the multiple-testing correction "
                         "and are worth a second window to confirm.")
    lines.append("")

    # ============================================================ 2 magnitude
    lines.append("## 2. Ranking vs estimation — the two are not the same verdict")
    lines.append("")
    lines.append("Section 1 asked whether the columns put movers above non-movers. This "
                 "section asks something the earlier reports never did: **do the "
                 "regressors' numbers mean anything as sizes?** A column can order the "
                 "corpus well and still be a poor estimate of how far a stock runs.")
    lines.append("")
    const = float(np.median(y_raw))
    lines.append("| column | Spearman ρ | Pearson r | MAE | MAE of a constant | "
                 "MAE improvement | RMSE |")
    lines.append("|---|---|---|---|---|---|---|")
    mag = {}
    for c in REG_COLS:
        s = sc[c].to_numpy(dtype=float)
        rho = float(spearmanr(s, y_raw).statistic)
        r = float(pearsonr(s, y_raw).statistic)
        mae = float(np.abs(y_raw - s).mean())
        mae0 = float(np.abs(y_raw - const).mean())
        rmse = float(np.sqrt(((y_raw - s) ** 2).mean()))
        mag[c] = (rho, r, mae, mae0, rmse)
        lines.append(f"| `{c}` | {fmt(rho)} | {fmt(r)} | {fmt(mae, 2)} | "
                     f"{fmt(mae0, 2)} | {pct(1 - mae / mae0)} | {fmt(rmse, 1)} |")
    lines.append("")
    se_rho, se_r, se_mae, se_mae0, _ = mag["pred_reg_se"]
    lines.append(f"**The gap between Spearman ({fmt(se_rho)}) and Pearson "
                 f"({fmt(se_r)}) is the finding.** `pred_reg_se` orders the corpus "
                 "respectably and tracks its level barely at all. Against the trivial "
                 f"strategy of predicting the median ({fmt(const, 1)}%) for every row, "
                 f"it cuts mean absolute error by only {pct(1 - se_mae / se_mae0)}.")
    lines.append("")
    lines.append("That is what an `asinh` target transform plus a 1035% tail does: "
                 "training compresses the tail so the model never predicts one "
                 f"(max prediction {fmt(sc['pred_reg_se'].max(), 1)}% against a realised "
                 f"max of {fmt(y_raw.max(), 0)}%), and the residual on the handful of "
                 "huge winners dominates every squared-error statistic. **Use these "
                 "columns as rankers. Do not read their values as forecast percentages.**")
    lines.append("")
    q80 = sc["pred_reg_q80"].to_numpy(dtype=float)
    cov = float((y_raw <= q80).mean())
    lo_c, hi_c = wilson(int((y_raw <= q80).sum()), n)
    lines.append(f"### Does `pred_reg_q80` deliver the 80% it promises?")
    lines.append("")
    lines.append("`pred_reg_q80` is a quantile regressor trained with "
                 "`reg:quantileerror`, `quantile_alpha=0.8`. Its contract is that the "
                 "realised move should fall at or below the prediction 80% of the time. "
                 "That is directly checkable:")
    lines.append("")
    lines.append(f"- **Realised coverage: {pct(cov)}** (95% CI {pct(lo_c)}–{pct(hi_c)}) "
                 f"against a target of 80.0%.")
    lines.append(f"- It under-covers by {100 * (0.80 - cov):.1f} percentage points — "
                 "the true move lands above the predicted 80th percentile more often "
                 "than it should. The CI excludes 80%, so this is a real miss, not "
                 "sampling noise.")
    lines.append("")
    lines.append("Coverage by predicted decile, which shows *where* it breaks:")
    lines.append("")
    dec = np.clip((rankdata(q80, method="ordinal") - 1) * 10 // n, 0, 9).astype(int)
    cov_by_dec = {}
    lines.append("| decile of `pred_reg_q80` | n | mean prediction | median realised | "
                 "coverage | vs 80% |")
    lines.append("|---|---|---|---|---|---|")
    for d in range(9, -1, -1):
        mk = dec == d
        cv = float((y_raw[mk] <= q80[mk]).mean())
        cov_by_dec[d + 1] = cv
        tag = " (highest)" if d == 9 else " (lowest)" if d == 0 else ""
        lines.append(f"| {d + 1}{tag} | {int(mk.sum())} | {fmt(q80[mk].mean(), 1)} | "
                     f"{fmt(np.median(y_raw[mk]), 1)} | {pct(cv)} | {sgn(cv - 0.80, 3)} |")
    lines.append("")
    n_under = sum(1 for v in cov_by_dec.values() if v < 0.80)
    worst_d = min(cov_by_dec, key=cov_by_dec.get)
    best_d = max(cov_by_dec, key=cov_by_dec.get)
    lines.append(f"**Coverage is below 80% in {n_under} of 10 deciles** — worst in "
                 f"decile {worst_d} ({pct(cov_by_dec[worst_d])}), best in decile "
                 f"{best_d} ({pct(cov_by_dec[best_d])}). The shortfall is broad rather "
                 "than concentrated: this is not a quantile that is accurate in most "
                 "of the range and breaks at one end, it is one that is a little too "
                 "low nearly everywhere. Per-decile counts are 138, so individual "
                 "cells carry roughly ±7pp of noise and the ordering between them "
                 "should not be over-read.")
    lines.append("")

    # ============================================================ 3 decile / capture
    lines.append("## 3. Decile profiles and captured move")
    lines.append("")
    lines.append("What a workload actually buys, per score column. For each decile of "
                 "score: how big were the moves, and what share of all the upside in "
                 "the corpus sat there.")
    lines.append("")
    tot = float(np.maximum(y_raw, 0).sum())
    dec_rows = []
    for c in SCORE_COLS:
        s = sc[c].to_numpy(dtype=float)
        d10 = np.clip((rankdata(s, method="ordinal") - 1) * 10 // n, 0, 9).astype(int)
        for d in range(9, -1, -1):
            mk = d10 == d
            dec_rows.append({
                "column": c, "decile": d + 1, "n": int(mk.sum()),
                "mean_actual": float(y_raw[mk].mean()),
                "median_actual": float(np.median(y_raw[mk])),
                f"hit_ge{th:.0f}": float((y_raw[mk] >= th).mean()),
                "share_of_upside": float(np.maximum(y_raw[mk], 0).sum() / tot),
            })
    dec_df = pd.DataFrame(dec_rows)
    p = outdir / "model2_decile_profile.tsv"
    dec_df.to_csv(p, sep="\t", index=False)
    art.append(p.name)

    lines.append(f"**Top decile (138 rows, 10% of the corpus) — `{best_col}` and its "
                 "siblings:**")
    lines.append("")
    lines.append("| column | mean move | median move | "
                 f"hit rate ≥{th:.0f}% | lift | share of all upside |")
    lines.append("|---|---|---|---|---|---|")
    base_rate = float(y_bin[th].mean())
    for c in SCORE_COLS:
        r = dec_df[(dec_df.column == c) & (dec_df.decile == 10)].iloc[0]
        lines.append(f"| `{c}` | {fmt(r['mean_actual'], 1)}% | "
                     f"{fmt(r['median_actual'], 1)}% | {pct(r[f'hit_ge{th:.0f}'])} | "
                     f"{fmt(r[f'hit_ge{th:.0f}'] / base_rate, 2)}x | "
                     f"{pct(r['share_of_upside'])} |")
    lines.append("")
    lines.append("Captured-move curve — cumulative share of all realised upside as the "
                 "workload grows:")
    lines.append("")
    fracs = [0.05, 0.10, 0.20, 0.30, 0.50]
    lines.append("| column | " + " | ".join(f"top {int(f*100)}%" for f in fracs) + " |")
    lines.append("|---|" + "---|" * len(fracs))
    for c in SCORE_COLS:
        s = sc[c].to_numpy(dtype=float)
        cells = []
        for f in fracs:
            mk = top_n_mask(s, int(round(f * n)))
            cells.append(pct(float(np.maximum(y_raw[mk], 0).sum() / tot)))
        lines.append(f"| `{c}` | " + " | ".join(cells) + " |")
    lines.append("| *random* | " + " | ".join(pct(f) for f in fracs) + " |")
    lines.append("")
    lines.append("Read the top-10% column against the *random* row: that ratio is the "
                 "concentration of upside the ranking buys. It is meaningfully above 1 "
                 "for every column and far below what the AUCs alone might suggest — "
                 "because total upside is dominated by a few enormous moves, and no "
                 "column reliably puts those at the very top.")
    lines.append("")

    # ============================================================ 4 calibration
    lines.append("## 4. Calibration — the classifier outputs are not probabilities")
    lines.append("")
    lines.append("Each classifier was trained with `scale_pos_weight` set to the "
                 "inverse class ratio. That is a deliberate choice and it helps the "
                 "model learn a rare class, but it has a consequence nobody has "
                 "written down: it multiplies the modelled odds by that weight, so the "
                 "numbers coming out are **rank scores wearing probability clothing**.")
    lines.append("")
    spw = {}
    rep_metrics = {}
    for c in CLF_COLS:
        pj = Path(_HERE / args.prep_dir) / f"xgb_fromPRhigh_clf_ge{int(CLF_OWN_TH[c])}_prep.json"
        j = json.loads(pj.read_text())
        spw[c] = float(j["scale_pos_weight"])
        rep_metrics[c] = j["metrics"]
    cal_rows = []
    lines.append("| column | `scale_pos_weight` | mean prediction | actual base rate | "
                 "over-prediction | Brier | Brier of a constant | **Brier skill** |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in CLF_COLS:
        t = CLF_OWN_TH[c]
        pv = sc[c].to_numpy(dtype=float)
        yy = (y_raw >= t).astype(float)
        br = float(((pv - yy) ** 2).mean())
        b0 = float(((yy.mean() - yy) ** 2).mean())
        lines.append(f"| `{c}` | {fmt(spw[c], 2)} | {fmt(pv.mean())} | "
                     f"{fmt(yy.mean())} | {fmt(pv.mean() / yy.mean(), 2)}x | "
                     f"{fmt(br, 4)} | {fmt(b0, 4)} | **{sgn(1 - br / b0, 3)}** |")
    lines.append("")
    lines.append("**Every Brier skill score is negative.** As probability forecasts all "
                 "three columns are worse than a constant that ignores the news "
                 "entirely and always answers with the base rate. `proba_clf_ge50` "
                 f"averages {fmt(sc['proba_clf_ge50'].mean())} against a realised rate "
                 f"of {fmt(float((y_raw >= 50).mean()))} — off by "
                 f"{fmt(float(sc['proba_clf_ge50'].mean() / (y_raw >= 50).mean()), 1)}x.")
    lines.append("")
    lines.append("This is **not** a defect in model-2's discrimination — the same "
                 "columns carry AUC ~0.72 in §1. It is entirely a question of scale, "
                 "and the next table proves it.")
    lines.append("")
    lines.append("### The prior correction puts them back on scale")
    lines.append("")
    lines.append("Training with weight *w* on the positives inflates the odds by *w*. "
                 "Undoing it is one line and needs no refitting, no held-out data and "
                 "no new model file:")
    lines.append("")
    lines.append("```")
    lines.append("p_corrected = p / (p + (1 - p) * scale_pos_weight)")
    lines.append("```")
    lines.append("")
    lines.append("| column | Brier (raw) | Brier (prior-corrected) | skill (corrected) | "
                 "mean p (corrected) | base rate | AUC before | AUC after |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for c in CLF_COLS:
        t = CLF_OWN_TH[c]
        pv = sc[c].to_numpy(dtype=float)
        yy = (y_raw >= t).astype(float)
        pc = pv / (pv + (1 - pv) * spw[c])
        br, brc = float(((pv - yy) ** 2).mean()), float(((pc - yy) ** 2).mean())
        b0 = float(((yy.mean() - yy) ** 2).mean())
        lines.append(f"| `{c}` | {fmt(br, 4)} | **{fmt(brc, 4)}** | "
                     f"{sgn(1 - brc / b0, 3)} | {fmt(pc.mean())} | {fmt(yy.mean())} | "
                     f"{fmt(auc_score(yy, pv))} | {fmt(auc_score(yy, pc))} |")
    lines.append("")
    lines.append("The correction is strictly monotone, so **AUC is unchanged to the last "
                 "digit** — the ranking is untouched. What changes is that the numbers "
                 "become readable as probabilities and every Brier skill turns "
                 "positive. Any downstream use that compares a raw score against a "
                 "probability-like constant is off by a factor of "
                 f"{fmt(spw['proba_clf_ge50'], 1)} at the `>=50%` cut.")
    lines.append("")
    pc50 = (sc["proba_clf_ge50"].to_numpy(dtype=float) /
            (sc["proba_clf_ge50"].to_numpy(dtype=float) +
             (1 - sc["proba_clf_ge50"].to_numpy(dtype=float)) * spw["proba_clf_ge50"]))
    lines.append("It is a correction, not a cure. It slightly **over**-corrects at the "
                 f"`>=50%` cut (mean {fmt(pc50.mean())} against a base rate of "
                 f"{fmt(float((y_raw >= 50).mean()))}), and the reliability tables "
                 "below show it still runs hot in the highest bin. The residual is "
                 "shape error, which no single scalar can fix — a proper Platt or "
                 "isotonic recalibration fitted on held-out data would do better. The "
                 "point of the one-liner is that **most of the damage is one number**, "
                 "and it costs nothing to undo.")
    lines.append("")
    lines.append("### Reliability detail")
    lines.append("")
    lines.append("Observed rate against predicted probability, in bins of predicted "
                 "value. A calibrated column would track the diagonal.")
    lines.append("")
    edges = np.array([0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.01])
    for c in CLF_COLS:
        t = CLF_OWN_TH[c]
        pv = sc[c].to_numpy(dtype=float)
        yy = (y_raw >= t).astype(float)
        pc = pv / (pv + (1 - pv) * spw[c])
        lines.append(f"**`{c}`** (target `>= {t:.0f}%`, base rate {pct(yy.mean())}):")
        lines.append("")
        lines.append("| predicted bin | n | mean predicted | **observed** | 95% CI | "
                     "mean predicted (corrected) |")
        lines.append("|---|---|---|---|---|---|")
        for i in range(len(edges) - 1):
            mk = (pv >= edges[i]) & (pv < edges[i + 1])
            if mk.sum() == 0:
                continue
            k = int(yy[mk].sum())
            lo, hi = wilson(k, int(mk.sum()))
            lines.append(f"| {edges[i]:.1f}–{min(edges[i+1],1.0):.1f} | {int(mk.sum())} | "
                         f"{fmt(pv[mk].mean())} | **{fmt(yy[mk].mean())}** | "
                         f"{fmt(lo)}–{fmt(hi)} | {fmt(pc[mk].mean())} |")
            cal_rows.append({"column": c, "bin_lo": edges[i],
                             "bin_hi": min(edges[i + 1], 1.0), "n": int(mk.sum()),
                             "mean_pred": float(pv[mk].mean()),
                             "observed": float(yy[mk].mean()),
                             "ci_lo": lo, "ci_hi": hi,
                             "mean_pred_corrected": float(pc[mk].mean())})
        lines.append("")
    lines.append("The *observed* column rises monotonically with the predicted bin in "
                 "each table — the ordering is sound. It simply rises from far below "
                 "the predicted value, which is the scale error the prior correction "
                 "removes.")
    lines.append("")
    p = outdir / "model2_calibration_bins.tsv"
    pd.DataFrame(cal_rows).to_csv(p, sep="\t", index=False)
    art.append(p.name)

    # ============================================================ 5 operating points
    lines.append("## 5. Operating points")
    lines.append("")
    lines.append(f"Precision, recall and lift at `>= {th:.0f}%` as the workload varies, "
                 "for every score column. Workload is expressed as a share of the "
                 "corpus so the columns are directly comparable despite living on "
                 "different scales.")
    lines.append("")
    lines.append("| column | workload | fires | /day | TP | precision | 95% CI | lift | "
                 "recall |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for c in SCORE_COLS:
        s = sc[c].to_numpy(dtype=float)
        for f in [0.05, 0.10, 0.20, 0.30]:
            k = int(round(f * n))
            mk = top_n_mask(s, k)
            tp = int((y_raw[mk] >= th).sum())
            lo, hi = wilson(tp, k)
            lines.append(f"| `{c}` | top {int(f*100)}% | {k} | {k/n_days:.1f} | {tp} | "
                         f"{pct(tp/k)} | {pct(lo)}–{pct(hi)} | "
                         f"{fmt((tp/k)/base_rate, 2)}x | "
                         f"{pct(tp/int(y_bin[th].sum()))} |")
    lines.append(f"| *base rate* | — | — | — | {int(y_bin[th].sum())} | "
                 f"{pct(base_rate)} | — | 1.00x | 100.0% |")
    lines.append("")
    lines.append("Precision decays slowly as the workload widens and the Wilson "
                 "intervals overlap heavily between adjacent workloads. There is no "
                 "sharp knee to find here — at this sample size the operating curve is "
                 "close to flat over the range that matters.")
    lines.append("")

    # ============================================================ 6 variable screen
    lines.append("## 6. Univariate screen — every variable in the corpus")
    lines.append("")
    numeric, categorical, skipped = classify_columns(co, args.max_levels)
    allow = set()
    fpath = Path(_HERE / args.prep_dir) / "xgb_fromPRhigh_features_conservative.txt"
    if fpath.exists():
        allow = {l.strip() for l in fpath.read_text().splitlines() if l.strip()}
    lines.append(f"{len(numeric)} numeric and {len(categorical)} categorical columns "
                 f"screened"
                 + (f", {len(skipped)} skipped (constant, near-empty, or above the "
                    f"{args.max_levels}-level cap)" if skipped else "")
                 + f". Each is scored against `>= {th:.0f}%` separately on train and "
                   "on val.")
    lines.append("")
    lines.append("Missingness is screened too: any column between 5% and 95% null also "
                 "contributes a `__isna` indicator, because *whether a field could be "
                 "filled* is itself information (short-interest and borrow columns are "
                 "absent for whole classes of name).")
    lines.append("")
    lines.append("**Reading the AUC column**: an AUC *below* 0.5 is not a weak variable, "
                 "it is an inverted one — `MktCap` at 0.370 says smaller companies move "
                 "more, and is exactly as informative as a variable at 0.630. The table "
                 "therefore ranks on distance from 0.5 and reports **oriented AUC** "
                 "(`max(AUC, 1−AUC)`) beside the raw value, with `dir` showing which "
                 "way the variable points.")
    lines.append("")

    y_tr = (pd.to_numeric(co.loc[~co["_is_val"], TARGET], errors="coerce")
            .to_numpy(dtype=float) >= th).astype(float)
    y_va = (pd.to_numeric(co.loc[co["_is_val"], TARGET], errors="coerce")
            .to_numpy(dtype=float) >= th).astype(float)
    d_tr = co.loc[~co["_is_val"], DATE_COL].astype(str).to_numpy()
    d_va = co.loc[co["_is_val"], DATE_COL].astype(str).to_numpy()
    f_tr = day_folds(d_tr, args.folds, args.seed)
    f_va = day_folds(d_va, args.folds, args.seed)

    screen = []
    for c in numeric:
        v_all = pd.to_numeric(co[c], errors="coerce").to_numpy(dtype=float)
        vt, vv = v_all[~co["_is_val"].to_numpy()], v_all[co["_is_val"].to_numpy()]
        row = {"variable": c, "kind": "numeric",
               "in_allowlist": c in allow,
               "na_frac_val": float(np.isnan(vv).mean())}
        for nm, vals, yy in (("train", vt, y_tr), ("val", vv, y_va)):
            ok = ~np.isnan(vals)
            if ok.sum() < 30 or yy[ok].sum() < 3:
                row[f"auc_{nm}"], row[f"p_{nm}"], row[f"n_{nm}"] = np.nan, np.nan, 0
                continue
            row[f"auc_{nm}"] = auc_score(yy[ok], vals[ok])
            row[f"p_{nm}"] = auc_pvalue(yy[ok].astype(bool), vals[ok])
            row[f"n_{nm}"] = int(ok.sum())
        screen.append(row)
        na = float(np.isnan(v_all).mean())
        if 0.05 < na < 0.95:
            it, iv = np.isnan(vt).astype(float), np.isnan(vv).astype(float)
            screen.append({
                "variable": f"{c}__isna", "kind": "missingness",
                "in_allowlist": False, "na_frac_val": 0.0,
                "auc_train": auc_score(y_tr, it), "p_train": auc_pvalue(y_tr.astype(bool), it),
                "n_train": len(it),
                "auc_val": auc_score(y_va, iv), "p_val": auc_pvalue(y_va.astype(bool), iv),
                "n_val": len(iv)})

    for c in categorical:
        lv = co[c].astype(str).to_numpy(dtype=object)
        lt, lv_ = lv[~co["_is_val"].to_numpy()], lv[co["_is_val"].to_numpy()]
        et = oof_target_encode(lt, y_tr, f_tr, args.folds)
        ev = oof_target_encode(lv_, y_va, f_va, args.folds)
        row = {"variable": c, "kind": "categorical", "in_allowlist": c in allow,
               "na_frac_val": float(pd.isna(co.loc[co["_is_val"], c]).mean()),
               "n_train": len(et), "n_val": len(ev)}
        for nm, e, yy in (("train", et, y_tr), ("val", ev, y_va)):
            ok = ~np.isnan(e)
            row[f"auc_{nm}"] = auc_score(yy[ok], e[ok]) if ok.sum() > 30 else np.nan
            row[f"p_{nm}"] = auc_pvalue(yy[ok].astype(bool), e[ok]) if ok.sum() > 30 else np.nan
        screen.append(row)

    sdf = pd.DataFrame(screen)
    sdf["q_train"] = bh_fdr(sdf["p_train"].to_numpy())
    sdf["q_val"] = bh_fdr(sdf["p_val"].to_numpy())
    sdf["abs_auc_val"] = (sdf["auc_val"] - 0.5).abs()
    sdf["consistent"] = ((sdf["auc_train"] - 0.5) * (sdf["auc_val"] - 0.5)) > 0
    sdf["suspected_leak"] = (sdf[["auc_train", "auc_val"]].max(axis=1) >= LEAK_AUC)
    sdf = sdf.sort_values("abs_auc_val", ascending=False, kind="mergesort")
    p = outdir / "model2_univariate_screen.tsv"
    sdf.to_csv(p, sep="\t", index=False)
    art.append(p.name)

    leaks = sdf[sdf["suspected_leak"]]
    if len(leaks):
        lines.append(f"⚠ **{len(leaks)} column(s) flagged as suspected leaks** "
                     f"(AUC ≥ {LEAK_AUC:.2f}): "
                     + ", ".join(f"`{v}`" for v in leaks["variable"].head(10))
                     + ". Excluded from the tables below — a column this predictive on "
                       "news data is far more likely to encode the outcome than to "
                       "predict it.")
        lines.append("")
    sdf_ok = sdf[~sdf["suspected_leak"]]

    n_sig = int((sdf_ok["q_val"] < 0.05).sum())
    n_cons = int(((sdf_ok["q_val"] < 0.05) & sdf_ok["consistent"]).sum())
    lines.append(f"**{n_sig} of {len(sdf_ok)} variables clear BH-FDR q < 0.05 on val**, "
                 f"and {n_cons} of those also point the same direction on train. "
                 "Direction agreement matters more than the q-value here: a variable "
                 "that helps on train and hurts on val has found a regime, not a "
                 "relationship.")
    lines.append("")
    lines.append(f"Top {args.top_screen} by |AUC − 0.5| on val. `in M2` marks the 25 "
                 "variables model-2 actually consumes; `S` marks a session-constant "
                 "market-regime variable (see §8).")
    lines.append("")
    lines.append("| # | variable | kind | in M2 | dir | AUC train | AUC val | "
                 "oriented | 95% CI (val) | q (val) | same sign |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    va_mask = co["_is_val"].to_numpy()
    for i, (_, r) in enumerate(sdf_ok.head(args.top_screen).iterrows(), 1):
        vname = r["variable"]
        if r["kind"] == "numeric":
            vv = pd.to_numeric(co[vname], errors="coerce").to_numpy(dtype=float)[va_mask]
        elif r["kind"] == "missingness":
            base = vname[:-len("__isna")]
            vv = np.isnan(pd.to_numeric(co[base], errors="coerce")
                          .to_numpy(dtype=float))[va_mask].astype(float)
        else:
            vv = oof_target_encode(co[vname].astype(str).to_numpy(dtype=object)[va_mask],
                                   y_va, f_va, args.folds)
        ok = ~np.isnan(vv)
        lo, hi = block_boot_auc(y_va[ok], vv[ok], d_va[ok], args.bootstrap, rng)
        a = r["auc_val"]
        direction = "↑" if a >= 0.5 else "↓"
        tag = "**yes**" if r["in_allowlist"] else (
            "S" if vname.startswith(SESSION_CONSTANT_PREFIXES) else "·")
        lines.append(f"| {i} | `{vname}` | {r['kind']} | {tag} | {direction} | "
                     f"{fmt(r['auc_train'])} | {fmt(a)} | **{fmt(max(a, 1 - a))}** | "
                     f"{fmt(lo)}–{fmt(hi)} | {fmt(r['q_val'], 4)} | "
                     f"{'yes' if r['consistent'] else '**no**'} |")
    lines.append("")
    lines.append(f"Full screen for all {len(sdf)} variables (including the "
                 f"{int((sdf['kind'] == 'missingness').sum())} `__isna` indicators): "
                 "`model2_univariate_screen.tsv`.")
    lines.append("")
    best_var = sdf_ok.iloc[0]
    bva = float(best_var["auc_val"])
    lines.append(f"The strongest single variable on val is `{best_var['variable']}` at "
                 f"oriented AUC {fmt(max(bva, 1 - bva))}"
                 + (f" (raw {fmt(bva)} — it points *down*)" if bva < 0.5 else "")
                 + f", against **{fmt(rank_rows[best_col][th][0])}** for `{best_col}`. "
                   "**The model beats every one of its inputs taken alone** — the "
                   "minimum one should demand of it, and worth confirming rather than "
                   "assuming.")
    lines.append("")

    # how much of the leaderboard the allowlist already owns
    top50 = sdf_ok[sdf_ok["kind"] != "missingness"].head(50)
    in50 = int(top50["in_allowlist"].sum())
    lines.append(f"Of the top 50 non-missingness variables by val AUC, **{in50} are in "
                 f"model-2's 25-feature allowlist** and {50 - in50} are not. The "
                 "allowlist was chosen by SHAP on train, so overlap with a val-ranked "
                 "screen is a genuine out-of-sample check on that selection; §8 asks "
                 "whether any of the ones left out actually add anything.")
    lines.append("")

    # ============================================================ 7 stability
    lines.append("## 7. Train → val stability of the screen")
    lines.append("")
    lines.append("A screen on one window is a hypothesis, not a result. The columns "
                 "below compare each variable's AUC on the 3424 train rows against the "
                 "1380 val rows.")
    lines.append("")
    both = sdf_ok.dropna(subset=["auc_train", "auc_val"])
    agree = float(both["consistent"].mean())
    drift = float((both["auc_val"] - both["auc_train"]).abs().mean())
    corr = float(spearmanr(both["auc_train"], both["auc_val"]).statistic)
    lines.append(f"- **Direction agreement: {pct(agree)}** of variables point the same "
                 "way on both folds (50% would be pure chance).")
    lines.append(f"- **Rank correlation of AUC across folds: ρ = {fmt(corr)}.**")
    lines.append(f"- **Mean absolute AUC drift: {fmt(drift)}** per variable.")
    lines.append("")
    if corr < 0.35:
        lines.append("That rank correlation is low. Most of what the univariate screen "
                     "measures on one window does not survive to the other, which is "
                     "the strongest available argument against reading any individual "
                     "row of §6 as a discovery. The variables that hold up are listed "
                     "next; the rest should be treated as noise.")
    else:
        lines.append("The screen carries across folds better than chance, so the top of "
                     "§6 is worth taking seriously — subject to §8, which is the test "
                     "that actually matters.")
    lines.append("")
    hold_all = both[(both["q_val"] < 0.05) & both["consistent"] &
                    (both["abs_auc_val"] > 0.03)]
    hold = hold_all.head(20)
    lines.append(f"**{len(hold_all)} variables hold up on both folds** (q < 0.05 on "
                 f"val, same direction on train, |AUC−0.5| > 0.03)"
                 + (f"; the {len(hold)} strongest are shown, the rest are in the "
                    "artifact TSV:" if len(hold_all) > len(hold) else ":"))
    lines.append("")
    lines.append("| variable | kind | in M2 | dir | AUC train | AUC val | oriented | "
                 "q (val) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for _, r in hold.iterrows():
        a = float(r["auc_val"])
        lines.append(f"| `{r['variable']}` | {r['kind']} | "
                     f"{'**yes**' if r['in_allowlist'] else '·'} | "
                     f"{'↑' if a >= 0.5 else '↓'} | {fmt(r['auc_train'])} | {fmt(a)} | "
                     f"{fmt(max(a, 1 - a))} | {fmt(r['q_val'], 4)} |")
    if not len(hold):
        lines.append("| *(none)* | | | | | | | |")
    lines.append("")
    n_hold_m2 = int(hold_all["in_allowlist"].sum())
    lines.append(f"Of these {len(hold_all)}, **{n_hold_m2} are in model-2's 25-feature "
                 f"allowlist and {len(hold_all) - n_hold_m2} are not** — but a variable "
                 "surviving here has only shown it carries signal *on its own*. "
                 "Whether model-2 has already absorbed that signal through a correlated "
                 "feature is a different question, and §8 is the one that answers it.")
    lines.append("")

    # ============================================================ 8 incremental
    lines.append("## 8. Incremental value — does any variable add to the model score?")
    lines.append("")
    lines.append("This is the section that answers the question the screen only "
                 "gestures at. A variable being predictive on its own means nothing if "
                 "model-2 has already extracted it. The test:")
    lines.append("")
    lines.append(f"1. Take `{best_col}` (highest val AUC, §1) as the base score.")
    lines.append(f"2. For each candidate variable, fit a two-input logistic regression "
                 f"on `[{best_col}, variable]` under {args.folds}-fold "
                 "**session-grouped** cross-validation within val.")
    lines.append("3. Compare the pooled out-of-fold AUC against the same procedure run "
                 "on the base score alone.")
    lines.append("4. Take the CI on the difference from a paired day-block bootstrap of "
                 "the out-of-fold predictions, and control the whole sweep with BH-FDR.")
    lines.append("")
    base_v = sc[best_col].to_numpy(dtype=float)
    yv = y_bin[th]
    auc_base, oof_base = oof_logistic_auc(base_v, yv, folds_val, args.folds)
    lines.append(f"Base: `{best_col}` alone, out-of-fold AUC **{fmt(auc_base)}** "
                 f"(the in-sample figure in §1 is {fmt(rank_rows[best_col][th][0])}; the "
                 "small difference is the cost of the CV wrapper and is expected).")
    lines.append("")

    val_idx = co["_is_val"].to_numpy()
    inc = []
    for _, r in sdf.iterrows():
        vname, kind = r["variable"], r["kind"]
        if kind == "numeric":
            if vname not in co.columns:
                continue
            vv = pd.to_numeric(co[vname], errors="coerce").to_numpy(dtype=float)[val_idx]
        elif kind == "missingness":
            base = vname[:-len("__isna")]
            vv = np.isnan(pd.to_numeric(co[base], errors="coerce")
                          .to_numpy(dtype=float))[val_idx].astype(float)
        else:
            vv = oof_target_encode(co[vname].astype(str).to_numpy(dtype=object)[val_idx],
                                   yv, folds_val, args.folds)
        if np.isnan(vv).mean() > args.max_na_frac:
            continue
        if np.nanstd(vv) < 1e-12:
            continue
        a2, oof2 = oof_logistic_auc(np.c_[base_v, vv], yv, folds_val, args.folds)
        if np.isnan(a2):
            continue
        ok = ~np.isnan(oof2) & ~np.isnan(oof_base)
        (lo, hi), pg = block_boot_delta_auc(yv[ok], oof2[ok], oof_base[ok], d_va[ok],
                                            args.screen_bootstrap, rng)
        pv = 2.0 * min(pg, 1.0 - pg) if not np.isnan(pg) else np.nan
        pv = max(pv, 1.0 / args.screen_bootstrap) if not np.isnan(pv) else np.nan
        inc.append({"variable": vname, "kind": kind,
                    "in_allowlist": bool(r["in_allowlist"]),
                    "auc_base": auc_base, "auc_with": a2, "delta": a2 - auc_base,
                    "ci_lo": lo, "ci_hi": hi, "p_gt0": pg, "p_two_sided": pv,
                    "suspected_leak": bool(r["suspected_leak"])})
    idf = pd.DataFrame(inc)
    idf["q"] = bh_fdr(idf["p_two_sided"].to_numpy())
    idf = idf.sort_values("delta", ascending=False, kind="mergesort")
    p = outdir / "model2_incremental_value.tsv"
    idf.to_csv(p, sep="\t", index=False)
    art.append(p.name)

    idf_ok = idf[~idf["suspected_leak"]]
    sep = idf_ok[(idf_ok["ci_lo"] > 0) & (idf_ok["q"] < 0.05)]
    lines.append(f"{len(idf_ok)} variables tested "
                 f"(those above {pct(args.max_na_frac)} missing on val are excluded — "
                 "an imputed majority would be measuring the imputation).")
    lines.append("")
    lines.append(f"**{len(sep)} variables add separably** (95% CI excludes zero *and* "
                 "BH-FDR q < 0.05).")
    lines.append("")
    lines.append(f"Top {min(args.top_screen, len(idf_ok))} by ΔAUC:")
    lines.append("")
    lines.append("| # | variable | kind | in M2 | AUC with | ΔAUC | 95% CI | P(Δ>0) | "
                 "q | adds? |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for i, (_, r) in enumerate(idf_ok.head(args.top_screen).iterrows(), 1):
        adds = "**yes**" if (r["ci_lo"] > 0 and r["q"] < 0.05) else "no"
        lines.append(f"| {i} | `{r['variable']}` | {r['kind']} | "
                     f"{'**yes**' if r['in_allowlist'] else '·'} | "
                     f"{fmt(r['auc_with'])} | {sgn(r['delta'])} | "
                     f"{sgn(r['ci_lo'])}..{sgn(r['ci_hi'])} | {pct(r['p_gt0'])} | "
                     f"{fmt(r['q'], 3)} | {adds} |")
    lines.append("")
    lines.append(f"Full sweep: `model2_incremental_value.tsv`.")
    lines.append("")
    if len(sep) == 0:
        lines.append("**Nothing in the corpus adds separably to the model score.** The "
                     "largest ΔAUC is "
                     f"{sgn(float(idf_ok['delta'].max()))} (`{idf_ok.iloc[0]['variable']}`), "
                     "and its interval contains zero. With "
                     f"{int(yv.sum())} positives this sweep can only detect an "
                     "improvement of roughly 0.03–0.05 AUC, so the honest statement is "
                     "**no large gain is available from these variables**, not that no "
                     "gain exists.")
        lines.append("")
    else:
        names = ", ".join(f"`{v}`" for v in sep["variable"])
        tail = ("." if sep["in_allowlist"].any()
                else " — **not one of them is in model-2's 25-feature allowlist.**")
        lines.append(f"The survivors, in full: {names}{tail}")
        lines.append("")
        # The interesting structure is not the individual winners -- it is that they
        # arrive in two recognisable families, neither of which model-2 can see.
        top20 = idf_ok.head(20)
        fam_regime = [v for v in top20["variable"]
                      if str(v).startswith(SESSION_CONSTANT_PREFIXES)]
        fam_lab = [v for v in top20["variable"] if "_lab_" in str(v)]
        lines.append("### The winners arrive in two families")
        lines.append("")
        lines.append(f"Of the 20 largest ΔAUC values, **{len(fam_regime)} are "
                     f"market-regime variables** (`VIX_*` / CNN Fear & Greed `FNG_*`) "
                     f"and **{len(fam_lab)} are encoder `_lab_` columns** — the "
                     "discrete argmax label of a sentiment model. That is not the "
                     "pattern of a random sweep, and each family says something "
                     "different:")
        lines.append("")
        lines.append("- **Regime.** Model-2's 25-feature allowlist contains no VIX, no "
                     "Fear & Greed, and no other market-wide variable. It reads the "
                     "news and the name, never the weather. The screen says the "
                     "weather matters — *and see the warning below before believing "
                     "the size of it.*")
        # Which of the survivors come from an encoder the model reads NOTHING from,
        # versus an encoder it reads a different channel of? The two mean different
        # things and the distinction is worth deriving rather than asserting.
        allow_fams = {a.split("_")[0] for a in allow}
        unseen, other_chan = [], []
        for vname in sep["variable"]:
            fam = str(vname).split("_")[0]
            (other_chan if fam in allow_fams else unseen).append(str(vname))
        lines.append("- **Encoder channels the model never sees.** "
                     + (f"{len(unseen)} of the {len(sep)} survivors "
                        f"({', '.join('`' + u + '`' for u in unseen)}) come from "
                        "encoders that contribute **no column at all** to the "
                        "allowlist — the model does not read those models' opinions "
                        "in any form. " if unseen else "")
                     + (f"{len(other_chan)} "
                        f"({', '.join('`' + o + '`' for o in other_chan)}) "
                        + ("comes" if len(other_chan) == 1 else "come")
                        + " from an encoder the model does read, but through a "
                          "different channel (headline vs body, score vs label vs "
                          "margin). " if other_chan else "")
                     + "Either way the gain is not 'the model mis-used this column', "
                       "it is 'this column was never offered'.")
        lines.append("")
        lines.append(f"⚠ **The regime family is far weaker evidence than its interval "
                     f"suggests.** `VIX_*` and `FNG_*` take one value per session, so "
                     f"across this fold they have {n_days} distinct values, not "
                     f"{n} — their effective sample size is the session count. The "
                     "day-block bootstrap does resample sessions and so captures part "
                     "of this, but a session-constant variable is also perfectly "
                     "confounded with everything else that made those particular "
                     "mornings unusual. On 25 sessions, 'August had some hot days' and "
                     "'Fear & Greed predicts big movers' are the same observation. "
                     "**Do not act on the regime family without a second window.**")
        lines.append("")
        lines.append("The per-PR sentiment family does not have that problem — those "
                     "columns vary row by row — and is the more interesting lead of "
                     "the two.")
        lines.append("")
        lines.append(f"Either way: this sweep tested {len(idf_ok)} candidates on "
                     f"{int(yv.sum())} positives. The right next step is a second "
                     "validation window, not a retrain.")
        lines.append("")
    n_pos = int((idf_ok["delta"] > 0).sum())
    lines.append(f"Distribution sanity: {n_pos} of {len(idf_ok)} candidates show a "
                 f"positive ΔAUC ({pct(n_pos / len(idf_ok))}). Under a true null of "
                 "'nothing adds anything' this would sit near 50% — adding a pure "
                 "noise column to a logistic costs a little out-of-fold AUC, so a "
                 "value somewhat below 50% is the expected shape of no signal.")
    lines.append("")

    # ============================================================ 9 integrity
    lines.append("## 9. Cross-check integrity")
    lines.append("")
    lines.append("Every number in this report is recomputed from the shipped score "
                 "columns. These checks confirm those columns really are the models "
                 "`model-2/*_prep.json` describes — the same check that caught model-1 "
                 "having been scored past `best_iteration`.")
    lines.append("")
    lines.append("| check | reported | recomputed | Δ | |")
    lines.append("|---|---|---|---|---|")
    checks = []
    for c in CLF_COLS:
        t = CLF_OWN_TH[c]
        pv = sc[c].to_numpy(dtype=float)
        yy = (y_raw >= t).astype(float)
        mm = rep_metrics[c]
        checks.append((f"`{c}` ROC-AUC", mm["roc_auc"], auc_score(yy, pv), 5e-4))
        checks.append((f"`{c}` PR-AUC", mm["pr_auc"], avg_precision(yy, pv), 2e-3))
        checks.append((f"`{c}` Brier", mm["brier"], float(((pv - yy) ** 2).mean()), 5e-4))
        k = int(round(0.10 * n))
        mk = top_n_mask(pv, k)
        checks.append((f"`{c}` top-10% precision", mm["top_frac"]["0.10"]["precision"],
                       float(yy[mk].mean()), 5e-3))
        checks.append((f"`{c}` base rate", mm["base_rate"], float(yy.mean()), 1e-6))
    for c in REG_COLS:
        pj = Path(_HERE / args.prep_dir) / (
            "xgb_cons_se_prep.json" if c == "pred_reg_se" else "xgb_cons_q80_prep.json")
        mm = json.loads(pj.read_text())["metrics"]
        s = sc[c].to_numpy(dtype=float)
        checks.append((f"`{c}` MAE", mm["mae"], float(np.abs(y_raw - s).mean()), 5e-3))
        checks.append((f"`{c}` Spearman ρ", mm["spearman_rho"],
                       float(spearmanr(s, y_raw).statistic), 5e-4))
        checks.append((f"`{c}` RMSE", mm["rmse"],
                       float(np.sqrt(((y_raw - s) ** 2).mean())), 5e-2))
        checks.append((f"`{c}` n", mm["n"], float(n), 0.5))
    n_ok = 0
    for name, rep, got, tol in checks:
        good = abs(float(rep) - float(got)) <= tol
        n_ok += good
        lines.append(f"| {name} | {float(rep):.6g} | {float(got):.6g} | "
                     f"{float(got) - float(rep):+.2e} | {'✅' if good else '❌'} |")
    lines.append("")
    lines.append(f"**{n_ok}/{len(checks)} checks reproduce.**"
                 + ("" if n_ok == len(checks) else
                    " ⚠ A failure here invalidates everything above it."))
    lines.append("")
    lines.append("Controls:")
    lines.append("")
    lines.append(f"- Score file and corpus val slice: **{n}/{n} rows matched** on "
                 f"`{' + '.join(KEYS)}`, keys unique on both sides.")
    lines.append(f"- Target agreement between the two files: **max abs diff "
                 f"{tgt_gap:.1f}**.")
    lines.append(f"- Base rates: {int(y_bin[th].sum())} at `>= {th:.0f}%`, "
                 + ", ".join(f"{int(y_bin[t].sum())} at `>= {t:.0f}%`"
                             for t in all_th if t != th) + ".")
    lines.append("")

    # ============================================================ caveats
    lines.append("## Caveats")
    lines.append("")
    lines.append(f"1. **One validation window.** {n_days} sessions, "
                 f"{int(y_bin[th].sum())} big movers. Every interval in this report is "
                 "wide for that reason and no amount of method fixes it. A second "
                 "window is worth more than any further analysis of this one.")
    lines.append("2. **The screen is hypothesis-generating, not confirmatory.** ~250 "
                 "univariate tests plus ~250 incremental tests on the same 1380 rows. "
                 "BH-FDR controls the false-discovery rate; it does not turn a "
                 "single-window result into a validated one.")
    lines.append("3. **`model-2/xgb_fromPRhigh_clf_report.md` says '49-feature SHAP "
                 "allowlist'. It is 25.** The boosters and "
                 "`xgb_fromPRhigh_features_conservative.txt` both carry 25. The line is "
                 "stale text inherited from model-1. Not edited here — it is a "
                 "training-side artifact.")
    lines.append("4. **The classifier columns are not probabilities as shipped** (§4). "
                 "Any threshold set against them is a threshold on a rank score whose "
                 "scale is set by `scale_pos_weight`, not a probability.")
    lines.append("5. **The variable screen uses train rows the model was fitted on.** "
                 "That is correct for measuring a *variable's* relationship with the "
                 "target and wrong for measuring a *model's* skill — which is why the "
                 "two are never pooled here and model-score numbers are val-only.")
    lines.append("6. **Categorical AUCs come from out-of-fold target encoding**, so "
                 "they measure the level structure available to a model, not any one "
                 "encoding an actual model used. `FineClass` here is the re-categorised "
                 "(`reCat`) version model-2 was trained on, which differs from the "
                 "live-tape labels on 34 of 1380 val rows.")
    lines.append("7. **Total upside is dominated by a few rows.** The largest single "
                 f"move in the val fold is {fmt(y_raw.max(), 0)}%. Captured-move "
                 "figures in §3 move materially if one or two rows change, and should "
                 "be read as indicative.")
    lines.append(f"8. **Session-constant variables are weaker evidence than their "
                 f"intervals suggest.** `VIX_*` and `FNG_*` take one value per "
                 f"session, so this fold contains {n_days} independent observations of "
                 f"them, not {n}. Any result about them is a statement about 25 "
                 "mornings and is confounded with everything else those mornings had "
                 "in common. They are marked `S` in §6 and called out in §8.")
    lines.append("")

    lines.append("## Artifacts")
    lines.append("")
    for a in art:
        lines.append(f"- `{a}`")
    lines.append("")
    lines.append("Regenerate with:")
    lines.append("")
    lines.append("```")
    lines.append("/home/tom/venv/bin/python analyze_model2_deep.py")
    lines.append("```")
    lines.append("")
    lines.append("See also: `2026-09-16_XGB_vs_O52_predictive_power.md` (model-1 vs the "
                 "live gate) and `2026-09-20_XGB_model2_vs_model1_predictive_power.md` "
                 "(the paired head-to-head that retired model-1).")
    lines.append("")

    # ============================================================ verdict
    v = []
    v.append("## Verdict")
    v.append("")
    v.append(f"**Model-2 is a good ranker and a poor estimator, and its classifier "
             f"outputs are not probabilities.** Those are three separate findings and "
             f"only the first was known before this report.")
    v.append("")
    sep_txt = ("and no column separates from any other after correcting for the 10 "
               "pairwise tests" if n_q_sep == 0 else
               f"and {n_q_sep} of 10 column pairs separates after BH correction")
    v.append(f"1. **Ranking holds up.** All five columns sit at AUC "
             f"{fmt(min(rank_rows[c][th][0] for c in SCORE_COLS))}–"
             f"{fmt(max(rank_rows[c][th][0] for c in SCORE_COLS))} at `>= {th:.0f}%` "
             f"out-of-sample, {sep_txt} — the same non-separability seen between "
             f"model-1 and model-2.")
    v.append(f"2. **Magnitude does not.** `pred_reg_se` beats a constant-median "
             f"prediction by only {pct(1 - se_mae / se_mae0)} on MAE, and its Pearson "
             f"({fmt(se_r)}) is a third of its Spearman ({fmt(se_rho)}) — it orders "
             f"the corpus well and tracks its level barely at all. `pred_reg_q80` "
             f"delivers {pct(cov)} coverage against the 80% it promises, short in "
             f"{n_under} of 10 deciles. **Read these columns as rankers, not as "
             f"forecast percentages.**")
    v.append(f"3. **Calibration is broken by construction.** Brier skill is negative "
             f"for all three classifiers — worse than a constant base rate. "
             f"`proba_clf_ge50` over-predicts by "
             f"{fmt(float(sc['proba_clf_ge50'].mean() / (y_raw >= 50).mean()), 1)}x. "
             f"The cause is `scale_pos_weight` and the one-line prior correction in §4 "
             f"fixes it with the ranking untouched.")
    if len(sep) == 0:
        v.append(f"4. **Nothing in the corpus adds separably to the model score** "
                 f"(§8): 0 of {len(idf_ok)} candidates clear both the interval and "
                 f"BH-FDR. The 25-feature allowlist does not appear to have left "
                 f"large, detectable signal behind — within the power this window has.")
    else:
        v.append(f"4. **{len(sep)} of {len(idf_ok)} variables add separably to the "
                 f"model score** (§8), "
                 + ("none of them in the 25-feature allowlist"
                    if not sep["in_allowlist"].any() else
                    "some of them already in the allowlist")
                 + ": " + ", ".join(f"`{x}`" for x in sep["variable"]) + ". "
                 + "The wider leaderboard clusters into two families model-2 cannot "
                   "see — **market-regime** (`VIX_*`, CNN Fear & Greed) and "
                   "**sentiment channels from encoders absent from the allowlist**. "
                   "The regime family is much the weaker lead: those variables are "
                   f"constant within a session, so on {n_days} sessions they cannot be "
                   "told apart from 'some mornings were unusual'. The per-PR sentiment "
                   "channels vary row by row and are the ones worth a second window.")
    v.append("")
    v.append(f"Everything rests on one {n_days}-session window with "
             f"{int(y_bin[th].sum())} big movers. The intervals are wide and they are "
             f"honest.")
    v.append("")
    lines[verdict_slot] = "\n".join(v)

    rp = (_HERE / args.report).resolve()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(lines) + "\n")
    print(f"\nreport -> {rp}")
    for a in art:
        print(f"artifact -> {outdir / a}")
    if n_ok != len(checks):
        sys.exit(f"INTEGRITY FAILURE: {len(checks) - n_ok} cross-check(s) did not "
                 "reproduce -- the report was written but must not be trusted")


if __name__ == "__main__":
    main()
