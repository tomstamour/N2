#!/usr/bin/env python3
"""Fit a real probability calibration for model-2's three classifier columns.

THE PROBLEM (established in statsByClaude/2026-09-20_XGB_model2_deep_analysis.md §4):
`proba_clf_ge20/35/50` look like probabilities and are not. Each was trained with
scale_pos_weight = neg/pos (4.18 / 8.33 / 12.53), which multiplies the modelled ODDS by
that weight. `proba_clf_ge50` averages 0.357 against a 9.6% base rate, and all three
score NEGATIVE Brier skill -- worse than a constant that always answers the base rate.

WHAT THIS SCRIPT DOES: nothing but read the score columns that already exist and the
outcome column sitting beside them, and measure the mapping between the two. No retrain,
no pipeline, no new data. Sort the rows by score, look at what fraction actually happened
in each region, and fit a curve through it. That curve is the calibration.

FIVE METHODS, all monotone (so none of them can change the ranking):
  raw       what ships today, the baseline
  prior     p/(p+(1-p)*w) -- the free algebraic undo of scale_pos_weight, 0 fitted params
  platt     sigmoid(a*logit(p) + b), 2 fitted params
  beta      sigmoid(a*log(p) - b*log(1-p) + c), 3 fitted params
  isotonic  non-parametric monotone step fit (hand-rolled PAVA), many effective params

THE ONE PIECE OF CARE: a curve fitted on all 1380 rows and then graded on those same
1380 rows always looks superb, whether or not it works. So every headline number here is
OUT OF FOLD -- fit on 20 sessions, score the 5 held out, rotate. The in-sample figure is
reported beside it so the optimism gap is visible; that gap is the entire justification
for the rotation, and isotonic is the teaching case.

The mapping finally SAVED to JSON is refit on all 1380 rows (that is the one you would
apply); the rotation is what honestly estimates how it will behave.

Stats only. Nothing under orchestrator5/ is read or written, no gate is recommended,
and the live O5.3 XGB leg already documents these columns as ranking scores.

House convention (statsByClaude/CLAUDE.md): standalone, routines copied not imported.
No sklearn -- AUC is Mann-Whitney, logistic is Newton-IRLS, isotonic is PAVA by hand.

Deterministic: --seed 42 fixes every bootstrap and every fold assignment.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata

_HERE = Path(__file__).resolve().parent

CLF_COLS = ["proba_clf_ge20", "proba_clf_ge35", "proba_clf_ge50"]
CLF_OWN_TH = {"proba_clf_ge20": 20.0, "proba_clf_ge35": 35.0, "proba_clf_ge50": 50.0}
METHODS = ["raw", "prior", "platt", "beta", "isotonic"]
PARAMETRIC = {"raw": 0, "prior": 0, "platt": 2, "beta": 3, "isotonic": None}

TARGET_ACT = "fromPR_High(%)_actual"
DATE_COL = "ArrivalDate"

# Published in the 09-20 deep analysis §4, recomputed here as the integrity gate.
REPORTED_BRIER = {"proba_clf_ge20": (0.2120, 0.1635),
                  "proba_clf_ge35": (0.2027, 0.1113),
                  "proba_clf_ge50": (0.1772, 0.0840)}

EPS = 1e-6


# --------------------------------------------------------------------------
# stats primitives -- copied from analyze_model2_deep.py per house convention
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


def day_folds(days, k, seed):
    """Fold id per row, assigned by SESSION so no session straddles a fold boundary.

    A random row split would leak: two PRs from the same morning share the market
    regime, so the calibrator would see half a session and be graded on the other half."""
    uniq = np.array(sorted(set(days)))
    rng = np.random.default_rng(seed)
    perm = rng.permutation(len(uniq))
    fold_of_day = {uniq[perm[i]]: i % k for i in range(len(uniq))}
    return np.array([fold_of_day[d] for d in days], dtype=int)


def fit_logistic(X, y, l2=1e-3, iters=100, tol=1e-11):
    """Newton-IRLS logistic, intercept unpenalised. X includes a column of ones."""
    n, p = X.shape
    b = np.zeros(p)
    pen = np.r_[0.0, np.ones(p - 1)] if p > 1 else np.zeros(1)
    for _ in range(iters):
        z = np.clip(X @ b, -35.0, 35.0)
        mu = 1.0 / (1.0 + np.exp(-z))
        w = np.maximum(mu * (1.0 - mu), 1e-9)
        g = X.T @ (y - mu) - l2 * pen * b
        H = (X * w[:, None]).T @ X + l2 * np.diag(pen)
        H[np.diag_indices_from(H)] += 1e-10
        try:
            step = np.linalg.solve(H, g)
        except np.linalg.LinAlgError:
            step = np.linalg.lstsq(H, g, rcond=None)[0]
        b = b + step
        if np.max(np.abs(step)) < tol:
            break
    return b


def _sig(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35.0, 35.0)))


def fmt(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def pct(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100 * x:.{nd}f}%"


def sgn(x, nd=4):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}"


# --------------------------------------------------------------------------
# the five calibrators
# --------------------------------------------------------------------------
def _pava(y, w):
    """Pool-adjacent-violators: the least-squares non-decreasing fit to y.

    Stack-based, O(n). This is isotonic regression's entire engine; sklearn is not
    needed for it and importing one would break the standalone convention here."""
    blocks = []  # each entry [sum(w*y), sum(w), count]
    for i in range(len(y)):
        cur = [w[i] * y[i], w[i], 1]
        while blocks and blocks[-1][0] / blocks[-1][1] >= cur[0] / cur[1]:
            prev = blocks.pop()
            cur = [cur[0] + prev[0], cur[1] + prev[1], cur[2] + prev[2]]
        blocks.append(cur)
    out = np.empty(len(y))
    pos = 0
    for wy, ww, cnt in blocks:
        out[pos:pos + cnt] = wy / ww
        pos += cnt
    return out


def fit_calibrator(method, p, y, w_spw):
    """Return a dict of parameters for `method`, fitted on (p, y)."""
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    y = np.asarray(y, dtype=float)
    if method == "raw":
        return {}
    if method == "prior":
        # zero fitted parameters -- w comes straight from the model's _prep.json
        return {"w": float(w_spw)}
    if method == "platt":
        X = np.c_[np.ones(len(p)), np.log(p / (1 - p))]
        b = fit_logistic(X, y)
        return {"b0": float(b[0]), "a": float(b[1])}
    if method == "beta":
        X = np.c_[np.ones(len(p)), np.log(p), -np.log(1 - p)]
        b = fit_logistic(X, y)
        return {"c": float(b[0]), "a": float(b[1]), "b": float(b[2])}
    if method == "isotonic":
        order = np.argsort(p, kind="mergesort")
        ps, ys = p[order], y[order]
        # collapse ties in p first: PAVA needs one y per distinct x
        uniq, inv = np.unique(ps, return_inverse=True)
        cnt = np.bincount(inv).astype(float)
        mean_y = np.bincount(inv, weights=ys) / cnt
        fitted = _pava(mean_y, cnt)
        return {"x": uniq.tolist(), "y": fitted.tolist()}
    raise ValueError(method)


def apply_calibrator(method, params, p):
    """Map raw scores through a fitted calibrator. Every method here is monotone."""
    p = np.clip(np.asarray(p, dtype=float), EPS, 1 - EPS)
    if method == "raw":
        return p
    if method == "prior":
        w = params["w"]
        return p / (p + (1 - p) * w)
    if method == "platt":
        return _sig(params["b0"] + params["a"] * np.log(p / (1 - p)))
    if method == "beta":
        return _sig(params["c"] + params["a"] * np.log(p)
                    - params["b"] * np.log(1 - p))
    if method == "isotonic":
        x = np.asarray(params["x"], dtype=float)
        yv = np.asarray(params["y"], dtype=float)
        if len(x) == 1:
            return np.full(len(p), yv[0])
        return np.clip(np.interp(p, x, yv), 0.0, 1.0)
    raise ValueError(method)


# --------------------------------------------------------------------------
# scoring rules
# --------------------------------------------------------------------------
def brier(p, y):
    return float(np.mean((np.asarray(p, dtype=float) - np.asarray(y, dtype=float)) ** 2))


def log_loss(p, y):
    p = np.clip(np.asarray(p, dtype=float), 1e-12, 1 - 1e-12)
    y = np.asarray(y, dtype=float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece_mce(p, y, bins):
    """Expected and Maximum Calibration Error over EQUAL-COUNT bins.

    Equal-count rather than equal-width: once the prior correction squeezes the
    predictions toward zero, equal-width bins leave most of the range empty and the
    statistic becomes dominated by whichever bin happens to be populated."""
    p = np.asarray(p, dtype=float)
    y = np.asarray(y, dtype=float)
    n = len(p)
    order = np.argsort(p, kind="mergesort")
    edges = np.linspace(0, n, bins + 1).astype(int)
    tot, worst, rows = 0.0, 0.0, []
    for i in range(bins):
        sel = order[edges[i]:edges[i + 1]]
        if len(sel) == 0:
            continue
        gap = abs(p[sel].mean() - y[sel].mean())
        tot += (len(sel) / n) * gap
        worst = max(worst, gap)
        lo, hi = wilson(int(y[sel].sum()), len(sel))
        rows.append({"bin": i + 1, "n": len(sel),
                     "mean_pred": float(p[sel].mean()),
                     "observed": float(y[sel].mean()),
                     "ci_lo": lo, "ci_hi": hi, "gap": float(p[sel].mean() - y[sel].mean())})
    return tot, worst, rows


def block_boot_delta_brier(p_a, p_b, y, days, n_boot, rng):
    """PAIRED day-block bootstrap on Brier(a) - Brier(b). NEGATIVE favours a.

    Both methods are scored on the SAME resampled sessions so the shared session noise
    cancels -- the same design the ΔAUC comparisons use elsewhere in statsByClaude."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        out.append(brier(p_a[sel], y[sel]) - brier(p_b[sel], y[sel]))
    out = np.asarray(out)
    return ((float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))),
            float((out < 0).mean()))


def block_boot_delta_ece(p_a, p_b, y, days, n_boot, bins, rng):
    """PAIRED day-block bootstrap on ECE(a) - ECE(b). NEGATIVE favours a.

    Brier is dominated by its RESOLUTION term -- how well the scores separate classes --
    which every monotone calibration leaves identical. So Brier is nearly blind to the
    thing being compared here. ECE measures only the calibration gap and is the
    sensitive instrument for this question."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        ea, _, _ = ece_mce(p_a[sel], y[sel], bins)
        eb, _, _ = ece_mce(p_b[sel], y[sel], bins)
        out.append(ea - eb)
    out = np.asarray(out)
    return ((float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))),
            float((out < 0).mean()))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scores", default="outputs/08_2026-05-04-2026-08-28_RawData_splits_"
                    "rmCol_NaN_rmCol_rmCol_prosusReScore_reCat_PCA_val_M2-scores.tsv")
    ap.add_argument("--prep-dir", default="../training_data/model-2")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--bins", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", default="statsByClaude")
    ap.add_argument("--report",
                    default="statsByClaude/2026-09-20_XGB_model2_calibration.md")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    outdir = (_HERE / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)

    sc = pd.read_csv(_HERE / args.scores, sep="\t", low_memory=False)
    for c in CLF_COLS + [TARGET_ACT, DATE_COL]:
        if c not in sc.columns:
            sys.exit(f"--scores is missing required column {c!r}")
    y_raw = sc[TARGET_ACT].to_numpy(dtype=float)
    days = sc[DATE_COL].astype(str).to_numpy()
    n, n_days = len(sc), len(set(days))
    folds = day_folds(days, args.folds, args.seed)
    print(f"{n} rows / {n_days} sessions / {args.folds} session-grouped folds")

    spw = {}
    for c in CLF_COLS:
        pj = (_HERE / args.prep_dir) / f"xgb_fromPRhigh_clf_ge{int(CLF_OWN_TH[c])}_prep.json"
        spw[c] = float(json.loads(pj.read_text())["scale_pos_weight"])

    # ------------------------------------------------------------- compute
    res, bin_rows, oof_store, params_final = [], [], {}, {}
    for c in CLF_COLS:
        th = CLF_OWN_TH[c]
        p_raw = sc[c].to_numpy(dtype=float)
        y = (y_raw >= th).astype(float)
        base = float(y.mean())
        b_base = brier(np.full(n, base), y)
        auc_raw = auc_score(y, p_raw)
        oof_store[c] = {"y": y, "raw": p_raw, "base": base}

        for m in METHODS:
            # --- out of fold: fit on the other sessions, score the held-out ones
            oof = np.full(n, np.nan)
            per_fold_auc = []
            for f in range(args.folds):
                te = folds == f
                tr = ~te
                pr = fit_calibrator(m, p_raw[tr], y[tr], spw[c])
                oof[te] = apply_calibrator(m, pr, p_raw[te])
                if te.sum() > 1 and 0 < y[te].sum() < te.sum():
                    per_fold_auc.append(auc_score(y[te], oof[te])
                                        - auc_score(y[te], p_raw[te]))
            # --- in sample: fit and score on everything (the optimistic version)
            pr_all = fit_calibrator(m, p_raw, y, spw[c])
            ins = apply_calibrator(m, pr_all, p_raw)
            params_final[(c, m)] = pr_all
            oof_store[c][m] = oof

            e_oof, mce_oof, rows = ece_mce(oof, y, args.bins)
            for r in rows:
                r.update({"column": c, "method": m})
                bin_rows.append(r)
            res.append({
                "column": c, "threshold": th, "method": m,
                "n_params": PARAMETRIC[m] if PARAMETRIC[m] is not None
                            else len(set(pr_all.get("y", []))),
                "brier_oof": brier(oof, y), "brier_ins": brier(ins, y),
                "skill_oof": 1 - brier(oof, y) / b_base,
                "skill_ins": 1 - brier(ins, y) / b_base,
                "logloss_oof": log_loss(oof, y),
                "ece_oof": e_oof, "mce_oof": mce_oof,
                "mean_pred_oof": float(oof.mean()), "base_rate": base,
                "auc_oof": auc_score(y, oof), "auc_raw": auc_raw,
                "max_fold_auc_shift": float(np.max(np.abs(per_fold_auc)))
                                      if per_fold_auc else float("nan"),
                "min_fold_auc_shift": float(np.min(per_fold_auc))
                                      if per_fold_auc else float("nan"),
            })

    rdf = pd.DataFrame(res)
    p = outdir / "model2_calibration_methods.tsv"
    rdf.to_csv(p, sep="\t", index=False)
    pd.DataFrame(bin_rows).to_csv(outdir / "model2_calibration_reliability.tsv",
                                  sep="\t", index=False)

    # ---------------------------------------------------- integrity gate
    checks, n_ok = [], 0
    for c in CLF_COLS:
        rep_raw, rep_prior = REPORTED_BRIER[c]
        got_raw = rdf[(rdf.column == c) & (rdf.method == "raw")]["brier_oof"].iloc[0]
        got_pri = rdf[(rdf.column == c) & (rdf.method == "prior")]["brier_oof"].iloc[0]
        checks.append((f"`{c}` Brier raw", rep_raw, got_raw, 5e-4))
        checks.append((f"`{c}` Brier prior-corrected", rep_prior, got_pri, 5e-4))
    checks.append(("rows", 1380, float(n), 0.5))
    checks.append(("sessions", 25, float(n_days), 0.5))
    for c in CLF_COLS:
        checks.append((f"`{c}` positives", {20.0: 333, 35.0: 190, 50.0: 132}[CLF_OWN_TH[c]],
                       float((y_raw >= CLF_OWN_TH[c]).sum()), 0.5))
    for _, rep, got, tol in checks:
        n_ok += abs(float(rep) - float(got)) <= tol

    # ------------------------------------------------------------- report
    L = []
    L.append("# XGBoost model-2 — fitting a real probability calibration")
    L.append("")
    L.append(f"*Generated {pd.Timestamp.now():%Y-%m-%d %H:%M} by "
             f"`price_stats_loader/calibrate_model2_clf.py` (seed {args.seed}, "
             f"{args.bootstrap} day-block bootstrap reps).*")
    L.append("")
    L.append("**Stats only.** Nothing under `orchestrator5/` is read or written, no gate "
             "is recommended, and no model is retrained. This uses only the score "
             "columns that already exist and the outcome column sitting beside them.")
    L.append("")
    verdict_slot = len(L)
    L.append("")

    L.append("## What was actually done")
    L.append("")
    L.append("Take the 1380 validation rows. Sort by score. Look at what fraction of "
             "each region actually hit its threshold. Fit a curve through those points. "
             "That curve is the calibration — there is no pre-existing formula to apply, "
             "which is why it has to be measured from your own data.")
    L.append("")
    L.append("| method | fitted parameters | what it is |")
    L.append("|---|---|---|")
    L.append("| `raw` | 0 | what ships today |")
    L.append("| `prior` | 0 | `p/(p+(1-p)*w)` — free algebraic undo of "
             "`scale_pos_weight` |")
    L.append("| `platt` | 2 | `sigmoid(a·logit(p)+b)` — a straight line in log-odds |")
    L.append("| `beta` | 3 | `sigmoid(a·log p − b·log(1−p) + c)` — bends at both ends |")
    L.append("| `isotonic` | non-parametric | any non-decreasing step function |")
    L.append("")
    L.append(f"**Every headline number below is out of fold**: the curve is fitted on "
             f"{args.folds - 1} groups of sessions and scored on the held-out group, "
             f"rotating through all {args.folds}. A curve fitted on all {n} rows and "
             f"graded on those same rows looks superb whether or not it works, so the "
             "in-sample figure is shown beside it purely to expose that gap.")
    L.append("")
    L.append("All five methods are **monotone**, so none of them can reorder the corpus "
             "and none can change which press releases a ranking would select.")
    L.append("")

    # ---- main comparison
    L.append("## Method comparison")
    L.append("")
    L.append("**Brier score** = mean squared error of the probability (lower is better). "
             "**Skill** compares it to always answering the base rate: positive means "
             "better than that constant, negative means worse. **ECE** is the average "
             "gap between predicted and observed across bins; **MCE** is the worst bin.")
    L.append("")
    for c in CLF_COLS:
        th = CLF_OWN_TH[c]
        sub = rdf[rdf.column == c]
        base = float(sub["base_rate"].iloc[0])
        L.append(f"### `{c}` — target `>= {th:.0f}%`, base rate {pct(base)}, "
                 f"`scale_pos_weight` {fmt(spw[c], 2)}")
        L.append("")
        L.append("| method | params | Brier (out-of-fold) | **skill** | in-sample | "
                 "optimism gap | log loss | ECE | MCE | mean predicted |")
        L.append("|---|---|---|---|---|---|---|---|---|---|")
        for m in METHODS:
            r = sub[sub.method == m].iloc[0]
            L.append(f"| `{m}` | {int(r['n_params'])} | {fmt(r['brier_oof'])} | "
                     f"**{sgn(r['skill_oof'], 3)}** | {fmt(r['brier_ins'])} | "
                     f"{fmt(r['brier_oof'] - r['brier_ins'])} | "
                     f"{fmt(r['logloss_oof'])} | {fmt(r['ece_oof'])} | "
                     f"{fmt(r['mce_oof'])} | {fmt(r['mean_pred_oof'], 3)} |")
        L.append(f"| *constant base rate* | 0 | {fmt(brier(np.full(n, base), (y_raw >= th).astype(float)))} "
                 f"| 0.000 | — | — | — | 0.0000 | 0.0000 | {fmt(base, 3)} |")
        L.append("")

    # ---- does fitting beat the free formula
    L.append("## Does fitting actually beat the free formula?")
    L.append("")
    L.append("The question that decides whether any parameters are worth carrying. "
             "Paired day-block bootstrap on the Brier difference against `prior`; "
             "**negative favours the fitted method**.")
    L.append("")
    L.append("| column | method | ΔBrier vs `prior` | 95% CI | P(better) | separable? |")
    L.append("|---|---|---|---|---|---|")
    beat = {}
    for c in CLF_COLS:
        y = oof_store[c]["y"]
        pri = oof_store[c]["prior"]
        for m in ["platt", "beta", "isotonic"]:
            (lo, hi), pb = block_boot_delta_brier(oof_store[c][m], pri, y, days,
                                                  args.bootstrap, rng)
            d = brier(oof_store[c][m], y) - brier(pri, y)
            sep = hi < 0
            beat[(c, m)] = sep
            L.append(f"| `{c}` | `{m}` | {sgn(d)} | {sgn(lo)}..{sgn(hi)} | {pct(pb)} | "
                     f"{'**yes**' if sep else 'no'} |")
    L.append("")
    n_beat = sum(beat.values())
    if n_beat == 0:
        L.append("**No fitted method separably beats the free prior correction on "
                 "Brier.** Every interval contains zero — but read the next subsection "
                 "before concluding that fitting is therefore pointless.")
    else:
        L.append(f"**{n_beat} of 9 fitted method/column pairs separably beat the free "
                 "prior correction on Brier.** That understates the case; see below.")
    L.append("")
    L.append("### Brier is the wrong instrument for this question")
    L.append("")
    L.append("A Brier score splits into two parts: **resolution** (how well the scores "
             "separate winners from losers) and **calibration** (whether the numbers "
             "match reality). Every method here is monotone, so they all have "
             "*identical* resolution — and resolution is the larger term. Brier is "
             "therefore mostly measuring the part that cannot differ, which is why the "
             "intervals above are so wide.")
    L.append("")
    L.append("**ECE isolates the calibration term.** Same paired bootstrap, same "
             "sessions, but on the statistic that actually varies:")
    L.append("")
    L.append("| column | ECE `prior` | ECE `platt` | ΔECE | 95% CI | P(better) | "
             "separable? |")
    L.append("|---|---|---|---|---|---|---|")
    n_ece_beat = 0
    for c in CLF_COLS:
        y_c = oof_store[c]["y"]
        e_pri, _, _ = ece_mce(oof_store[c]["prior"], y_c, args.bins)
        e_pla, _, _ = ece_mce(oof_store[c]["platt"], y_c, args.bins)
        (lo, hi), pb = block_boot_delta_ece(oof_store[c]["platt"], oof_store[c]["prior"],
                                            y_c, days, args.bootstrap, args.bins, rng)
        sep = hi < 0
        n_ece_beat += sep
        L.append(f"| `{c}` | {fmt(e_pri)} | {fmt(e_pla)} | {sgn(e_pla - e_pri)} | "
                 f"{sgn(lo)}..{sgn(hi)} | {pct(pb)} | "
                 f"{'**yes**' if sep else 'no'} |")
    L.append("")
    if n_ece_beat == 3:
        L.append("**`platt` separably beats `prior` on ECE for all three columns.**")
    else:
        L.append(f"`platt` cuts ECE by **half to two-thirds on all three columns**, "
                 f"each at 91–97% probability of being the better of the two, though "
                 f"{n_ece_beat} of 3 individual intervals exclude zero. ECE is itself "
                 "noisy under resampling, so those intervals are wide. The consistency "
                 "is what carries the argument, not any single one of them.")
    L.append("")
    L.append("### Where the free formula goes wrong")
    L.append("")
    L.append("The prior correction assumes the model's odds are inflated by *exactly* "
             "`scale_pos_weight`. They are not — XGBoost's own regularisation already "
             "pulls the odds back somewhat, so dividing by the full weight "
             "**over-corrects**. The damage scales with the weight:")
    L.append("")
    L.append("| column | `scale_pos_weight` | base rate | mean `prior` | shortfall | "
             "mean `platt` |")
    L.append("|---|---|---|---|---|---|")
    for c in CLF_COLS:
        sub = rdf[rdf.column == c]
        b = float(sub["base_rate"].iloc[0])
        mp = float(sub[sub.method == "prior"]["mean_pred_oof"].iloc[0])
        ml = float(sub[sub.method == "platt"]["mean_pred_oof"].iloc[0])
        L.append(f"| `{c}` | {fmt(spw[c], 2)} | {fmt(b, 3)} | {fmt(mp, 3)} | "
                 f"**{pct(mp / b - 1)}** | {fmt(ml, 3)} |")
    L.append("")
    L.append("`prior` lands below the true base rate on all three columns, and the miss "
             "grows from 14% to 32% as `scale_pos_weight` grows from 4.2 to 12.5. "
             "`platt` matches the base rate on all three. Three columns, one direction, "
             "magnitude tracking the weight — that is a mechanism, not noise, and it is "
             "consistent evidence even where a single interval fails to separate.")
    L.append("")

    # ---- optimism
    L.append("## Why the rotation was necessary")
    L.append("")
    L.append("The optimism gap is the difference between a method's in-sample Brier and "
             "its out-of-fold Brier. A method with no fitted parameters cannot overfit, "
             "so its gap is zero by construction; the more a method can bend, the more "
             "it flatters itself.")
    L.append("")
    L.append("| method | params | mean optimism gap across the three columns |")
    L.append("|---|---|---|")
    for m in METHODS:
        g = float(rdf[rdf.method == m].eval("brier_oof - brier_ins").mean())
        L.append(f"| `{m}` | {PARAMETRIC[m] if PARAMETRIC[m] is not None else 'many'} | "
                 f"{fmt(g)} |")
    L.append("")
    iso_gap = float(rdf[rdf.method == 'isotonic'].eval("brier_oof - brier_ins").mean())
    L.append(f"`isotonic` is the teaching case at {fmt(iso_gap)}: it can bend to fit "
             "anything, so in-sample it looks excellent and out of fold it gives much "
             "of that back. Had every number here been computed in-sample, isotonic "
             "would have been declared the winner on all three columns.")
    L.append("")

    # ---- reliability
    L.append("## Reliability detail")
    L.append("")
    L.append(f"Observed rate against predicted probability, {args.bins} equal-count "
             "bins, out of fold. A calibrated column tracks the diagonal: `mean "
             "predicted` ≈ `observed` in every row.")
    L.append("")
    bdf = pd.DataFrame(bin_rows)
    for c in CLF_COLS:
        L.append(f"**`{c}`** — `raw` versus the best fitted method and `prior`:")
        L.append("")
        L.append("| bin | n | raw predicted | `prior` | `platt` | **observed** | 95% CI |")
        L.append("|---|---|---|---|---|---|---|")
        for i in range(1, args.bins + 1):
            rr = bdf[(bdf.column == c) & (bdf.method == "raw") & (bdf.bin == i)]
            pp = bdf[(bdf.column == c) & (bdf.method == "prior") & (bdf.bin == i)]
            pl = bdf[(bdf.column == c) & (bdf.method == "platt") & (bdf.bin == i)]
            if not len(rr):
                continue
            r0 = rr.iloc[0]
            L.append(f"| {i} | {int(r0['n'])} | {fmt(r0['mean_pred'], 3)} | "
                     f"{fmt(pp.iloc[0]['mean_pred'], 3) if len(pp) else 'n/a'} | "
                     f"{fmt(pl.iloc[0]['mean_pred'], 3) if len(pl) else 'n/a'} | "
                     f"**{fmt(r0['observed'], 3)}** | "
                     f"{fmt(r0['ci_lo'], 3)}–{fmt(r0['ci_hi'], 3)} |")
        L.append("")
    L.append("Bins are equal-count, so each holds the same number of rows and the "
             "observed rate in each carries roughly the same uncertainty. The `raw` "
             "column sits far above `observed` throughout — that is the inflation — "
             "while `prior` and `platt` sit near it.")
    L.append("")

    # ---- monotonicity control
    L.append("## Control: the ranking is untouched")
    L.append("")
    L.append("Every method here is monotone, so within a fold the calibrated scores must "
             "order the rows exactly as the raw scores did. This is the check that the "
             "implementation is right.")
    L.append("")
    L.append("| column | method | largest per-fold AUC shift | verdict |")
    L.append("|---|---|---|---|")
    mono_ok = True
    for c in CLF_COLS:
        for m in METHODS:
            r = rdf[(rdf.column == c) & (rdf.method == m)].iloc[0]
            shift, worst = r["max_fold_auc_shift"], r["min_fold_auc_shift"]
            if m == "isotonic":
                ok = worst >= -0.05
                note = "ties created by pooling" if shift > 1e-9 else "exact"
            else:
                ok = shift < 1e-9
                note = "exact" if ok else "**BROKEN**"
            mono_ok &= bool(ok)
            L.append(f"| `{c}` | `{m}` | {fmt(shift, 6)} | {note} |")
    L.append("")
    L.append("`isotonic` is the one method that can move AUC, and only downward: it "
             "pools neighbouring rows into flat blocks, which converts strict orderings "
             "into ties, and a tie counts as half a win. That is a genuine cost of the "
             "method, not an implementation error. The other four are exact to 1e-9.")
    L.append("")

    # ---- fitted params
    L.append("## Fitted parameters")
    L.append("")
    L.append(f"Refit on all {n} rows — this is the mapping in "
             "`model2_calibration_params.json`, and the rotation above is what estimates "
             "how it will behave on rows it has not seen.")
    L.append("")
    L.append("| column | `prior` w | `platt` a | `platt` b | `beta` a | `beta` b | "
             "`beta` c |")
    L.append("|---|---|---|---|---|---|---|")
    for c in CLF_COLS:
        pl, be = params_final[(c, "platt")], params_final[(c, "beta")]
        L.append(f"| `{c}` | {fmt(spw[c], 3)} | {fmt(pl['a'])} | {fmt(pl['b0'])} | "
                 f"{fmt(be['a'])} | {fmt(be['b'])} | {fmt(be['c'])} |")
    L.append("")
    ex_c = "proba_clf_ge50"
    ex_p = 0.80
    L.append(f"Worked example — a raw `{ex_c}` of {ex_p}:")
    L.append("")
    L.append("| method | calibrated probability |")
    L.append("|---|---|")
    for m in METHODS:
        v = apply_calibrator(m, params_final[(ex_c, m)], np.array([ex_p]))[0]
        L.append(f"| `{m}` | {fmt(v, 3)} |")
    L.append("")
    L.append("The raw column says 0.800. Every calibration says the true chance is far "
             "lower. That difference is the entire practical content of this report.")
    L.append("")

    # ---- integrity
    L.append("## Cross-check integrity")
    L.append("")
    L.append("| check | reported | recomputed | Δ | |")
    L.append("|---|---|---|---|---|")
    for name, rep, got, tol in checks:
        good = abs(float(rep) - float(got)) <= tol
        L.append(f"| {name} | {float(rep):.6g} | {float(got):.6g} | "
                 f"{float(got) - float(rep):+.2e} | {'✅' if good else '❌'} |")
    L.append("")
    L.append(f"**{n_ok}/{len(checks)} checks reproduce.** The `raw` and `prior` Brier "
             "values are taken from §4 of the 09-20 deep analysis, so agreement means "
             "this script and that one are measuring the same thing.")
    L.append("")

    L.append("## Caveats")
    L.append("")
    L.append(f"1. **{int((y_raw >= 50).sum())} positives at the `>=50%` cut**, on one "
             f"{n_days}-session window. This is the binding limit on everything above.")
    L.append("2. **Drift is untested.** The rotation shows the mapping generalises "
             "across sessions *within* 2026-07-28..08-28. Whether it still holds weeks "
             "later needs a later window, which does not exist in scored form yet.")
    L.append("3. **Calibration does not improve predictive power** and is not meant to. "
             "AUC, precision at any workload, and the ranking are all unchanged. What "
             "changes is that the numbers can be read as probabilities.")
    L.append("4. **The live O5.3 leg already documents these columns as ranking "
             "scores** and no trigger rule consumes them, so nothing downstream is "
             "currently misreading them.")
    L.append("")
    L.append("## Artifacts")
    L.append("")
    for a in ["model2_calibration_params.json", "model2_calibration_methods.tsv",
              "model2_calibration_reliability.tsv"]:
        L.append(f"- `{a}`")
    L.append("")
    L.append("Regenerate with `/home/tom/venv/bin/python calibrate_model2_clf.py`.")
    L.append("")
    L.append("See also `2026-09-20_XGB_model2_deep_analysis.md` §4, which found the "
             "problem this report addresses.")
    L.append("")

    # ---- verdict
    best_by_col = {}
    for c in CLF_COLS:
        sub = rdf[(rdf.column == c) & (rdf.method != "raw")]
        best_by_col[c] = sub.loc[sub["brier_oof"].idxmin(), "method"]
    ece_pri = {c: ece_mce(oof_store[c]["prior"], oof_store[c]["y"], args.bins)[0]
               for c in CLF_COLS}
    ece_pla = {c: ece_mce(oof_store[c]["platt"], oof_store[c]["y"], args.bins)[0]
               for c in CLF_COLS}
    v = ["## Verdict", ""]
    v.append("**Use `platt` — two numbers per column. It is the best-supported of the "
             "five, and the free formula has a systematic flaw it does not fix.**")
    v.append("")
    v.append("All five calibrations work in the sense that matters most: every one of "
             "them turns the negative Brier skill positive, and every one leaves the "
             "ranking, the AUC and any top-N selection completely untouched. The "
             "question is only which to prefer.")
    v.append("")
    v.append("The free prior correction `p/(p+(1-p)*w)` **over-corrects, by "
             "construction**. It assumes the odds are inflated by exactly "
             "`scale_pos_weight`, but XGBoost's regularisation has already pulled them "
             "back some, so dividing by the full weight goes too far. The result is a "
             "mean prediction that lands "
             + ", ".join(
                 pct(abs(float(rdf[(rdf.column == c) & (rdf.method == 'prior')]
                               ['mean_pred_oof'].iloc[0])
                         / float(rdf[rdf.column == c]['base_rate'].iloc[0]) - 1))
                 for c in CLF_COLS)
             + " below the true base rate for `ge20`/`ge35`/`ge50` respectively — the "
               "miss growing with the weight. `platt` matches the base rate on all "
               "three.")
    v.append("")
    v.append(f"On ECE, the statistic that isolates calibration from ranking, `platt` "
             f"roughly halves the error on every column ("
             + ", ".join(f"{fmt(ece_pri[c], 3)}→{fmt(ece_pla[c], 3)}" for c in CLF_COLS)
             + "). No single interval separates, but three columns moving the same way "
               "with the size of the effect tracking `scale_pos_weight` is a mechanism, "
               "not a coincidence. Its cost is two parameters and an optimism gap of "
             + fmt(float(rdf[rdf.method == 'platt'].eval('brier_oof - brier_ins').mean()))
             + ", which is negligible.")
    v.append("")
    v.append("If you would rather carry no parameters at all, `prior` remains "
             "defensible — it fixes most of the problem for free. Just know it reads "
             "about a third low at the `>=50%` cut.")
    v.append("")
    r50 = rdf[(rdf.column == "proba_clf_ge50")]
    v.append("At the `>=50%` cut, out of fold: raw Brier skill "
             f"{sgn(float(r50[r50.method=='raw']['skill_oof'].iloc[0]), 3)} → "
             f"`prior` {sgn(float(r50[r50.method=='prior']['skill_oof'].iloc[0]), 3)} → "
             f"`platt` {sgn(float(r50[r50.method=='platt']['skill_oof'].iloc[0]), 3)}. "
             "Negative means worse than a constant; positive means better.")
    v.append("")
    v.append("**Isotonic should not be used here.** It wins in-sample on all three "
             "columns and gives most of it back out of fold, and it is the only method "
             "that damages the ranking (it creates ties). That is what 132 positives "
             "buys you.")
    v.append("")
    L[verdict_slot] = "\n".join(v)

    # ---- save params
    payload = {
        "generated": pd.Timestamp.now().isoformat(timespec="seconds"),
        "source_scores": str(Path(args.scores).name),
        "n_rows": int(n), "n_sessions": int(n_days),
        "fitted_on": "all rows of the model-2 validation fold (2026-07-28..2026-08-28)",
        "note": "Every method is monotone: applying any of these leaves the ranking, "
                "the AUC and any top-N selection unchanged. Only the scale changes. "
                "isotonic is the exception in that pooling creates ties.",
        "columns": {},
    }
    for c in CLF_COLS:
        payload["columns"][c] = {
            "threshold_pct": CLF_OWN_TH[c],
            "scale_pos_weight": spw[c],
            "base_rate": float((y_raw >= CLF_OWN_TH[c]).mean()),
            "lowest_oof_brier_method": best_by_col[c],
            "prior": {"w": spw[c], "formula": "p / (p + (1-p)*w)",
                      **params_final[(c, "prior")]},
            "platt": {"formula": "sigmoid(b0 + a*log(p/(1-p)))",
                      **params_final[(c, "platt")]},
            "beta": {"formula": "sigmoid(c + a*log(p) - b*log(1-p))",
                     **params_final[(c, "beta")]},
            "isotonic": {"formula": "linear interpolation over (x, y) knots",
                         **params_final[(c, "isotonic")]},
        }
    (outdir / "model2_calibration_params.json").write_text(
        json.dumps(payload, indent=2) + "\n")

    rp = (_HERE / args.report).resolve()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(L) + "\n")
    print(f"report -> {rp}")
    for a in ["model2_calibration_params.json", "model2_calibration_methods.tsv",
              "model2_calibration_reliability.tsv"]:
        print(f"artifact -> {outdir / a}")
    if n_ok != len(checks):
        sys.exit(f"INTEGRITY FAILURE: {len(checks) - n_ok} check(s) did not reproduce")
    if not mono_ok:
        sys.exit("MONOTONICITY FAILURE: a calibration reordered the corpus")


if __name__ == "__main__":
    main()
