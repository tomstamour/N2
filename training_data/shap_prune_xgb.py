#!/usr/bin/env python
"""
shap_prune_xgb.py -- TreeSHAP feature pruning for train_xgb_fromPRhigh.py.

Ranks the ~285 candidate features by mean(|SHAP|) on the TRAIN FOLD (averaged
over several seeds for stability), then walks a feature-count grid -- retraining
and re-scoring the val fold at each K -- to show how few features can be kept
with no val-metric loss. Emits two allowlists (conservative / aggressive) that
train_xgb_fromPRhigh.py consumes via --features.

SHAP is XGBoost's exact TreeSHAP (`pred_contribs=True`); no `shap` package.
Ranking is done on the reg:squarederror model (stable); confirm the chosen list
on the quantile model with a --features run afterwards.

    /home/tom/venv/bin/python shap_prune_xgb.py
    /home/tom/venv/bin/python shap_prune_xgb.py --seeds 5 --grid 285,200,150,120,90,70,50,35,25,18,12,8
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from train_xgb_fromPRhigh import build_xy, _metrics, _inverse_target

HERE = Path(__file__).resolve().parent
INPUT_GLOB = "08_*_PCA.tsv"


def _make_model(args, seed, XGBRegressor):
    extra = {}
    if args.objective == "reg:quantileerror":
        extra["quantile_alpha"] = args.quantile_alpha
    return XGBRegressor(
        objective=args.objective,
        n_estimators=args.n_estimators,
        **extra,
        learning_rate=args.eta,
        max_depth=args.max_depth,
        min_child_weight=args.min_child_weight,
        subsample=args.subsample,
        colsample_bytree=args.colsample_bytree,
        reg_lambda=args.reg_lambda,
        tree_method="hist",
        enable_categorical=True,
        eval_metric=["rmse", "mae"],
        early_stopping_rounds=args.early_stop,
        random_state=seed,
        n_jobs=args.jobs,
    )


def _fit(args, XGBRegressor, seed, Xtr, ytr_t, Xva, yva_t):
    m = _make_model(args, seed, XGBRegressor)
    m.fit(Xtr, ytr_t, eval_set=[(Xtr, ytr_t), (Xva, yva_t)], verbose=0)
    return m


def _val_row(args, XGBRegressor, feats, Xtr, ytr_t, Xva, yva_t, y_true, train_median, prep):
    m = _fit(args, XGBRegressor, 0, Xtr[feats], ytr_t, Xva[feats], yva_t)
    pred = _inverse_target(m.predict(Xva[feats]), prep)
    mm = _metrics(y_true, pred, train_median)
    tf = mm["top_frac"]["0.10"]
    return {
        "k": len(feats),
        "spearman": mm["spearman_rho"],
        "mae": mm["mae"],
        "top10_mean": tf["mean_actual"],
        "lift15": tf["lift"][15] or 0.0,
        "lift20": tf["lift"][20] or 0.0,
        "lift30": tf["lift"][30] or 0.0,
        "best_iter": int(getattr(m, "best_iteration", args.n_estimators)),
    }


def _pick(rows, full, s_tol, l_tol, mae_slack):
    """smallest k meeting the tolerance gates (rows: list of dicts, any order)."""
    ok = [r for r in rows
          if r["spearman"] >= full["spearman"] - s_tol
          and r["lift20"] >= full["lift20"] - l_tol
          and r["mae"] <= full["mae"] + mae_slack]
    return min(ok, key=lambda r: r["k"]) if ok else full


def _families_gone(all_feats, kept_feats):
    """prefix (token before first '_') groups where every member was dropped."""
    from collections import defaultdict
    grp = defaultdict(list)
    for f in all_feats:
        grp[f.split("_")[0]].append(f)
    kept = set(kept_feats)
    return sorted(p for p, members in grp.items()
                  if len(members) >= 2 and not any(f in kept for f in members))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=None, help=f"default: newest {INPUT_GLOB}")
    ap.add_argument("--cutoff", default="2026-07-28")
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--label-col", default="fromPR_High(%)")
    ap.add_argument("--target-transform", default="asinh", choices=["asinh", "log1p", "none"])
    ap.add_argument("--target-winsor", type=float, default=0.995)
    ap.add_argument("--winsor-lo", type=float, default=0.005)
    ap.add_argument("--winsor-hi", type=float, default=0.995)
    ap.add_argument("--skew-thresh", type=float, default=4.0)
    ap.add_argument("--objective", default="reg:squarederror")
    ap.add_argument("--quantile-alpha", type=float, default=0.8)
    ap.add_argument("--n-estimators", type=int, default=3000)
    ap.add_argument("--eta", type=float, default=0.03)
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--min-child-weight", type=float, default=5.0)
    ap.add_argument("--subsample", type=float, default=0.8)
    ap.add_argument("--colsample-bytree", type=float, default=0.7)
    ap.add_argument("--reg-lambda", type=float, default=1.0)
    ap.add_argument("--early-stop", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=-1)
    ap.add_argument("--seeds", type=int, default=5, help="ranking fits to average mean|SHAP| over")
    ap.add_argument("--grid", default="285,200,150,120,90,70,50,35,25,18,12,8")
    ap.add_argument("--tol", type=float, default=0.01, help="allowed val Spearman drop vs full")
    ap.add_argument("--lift-tol", type=float, default=0.1, help="allowed top-10% lift@20 drop vs full")
    ap.add_argument("--mae-slack", type=float, default=0.5)
    ap.add_argument("--out", type=Path, default=HERE / "xgb_fromPRhigh_shap.md")
    ap.add_argument("--keep-conservative-out", type=Path,
                    default=HERE / "xgb_fromPRhigh_features_conservative.txt")
    ap.add_argument("--keep-aggressive-out", type=Path,
                    default=HERE / "xgb_fromPRhigh_features_aggressive.txt")
    args = ap.parse_args()

    try:
        import xgboost as xgb
        from xgboost import XGBRegressor
    except ImportError:
        sys.exit("xgboost not installed -- run: /home/tom/venv/bin/pip install xgboost")

    if args.input is None:
        cands = list(HERE.glob(INPUT_GLOB))
        args.input = max(cands, key=lambda p: p.stat().st_mtime) if cands else None
    inp, X, yt, y_raw, train_mask, val_mask, prep, n_tr, n_va = build_xy(args)

    cols = list(X.columns)
    n_feat = len(cols)
    Xtr, Xva = X.loc[train_mask], X.loc[val_mask]
    ytr_t, yva_t = yt[train_mask], yt[val_mask]
    y_true = y_raw[val_mask].to_numpy(dtype="float64")
    train_median = float(np.median(y_raw[train_mask]))

    # ---- 1. SHAP ranking, averaged over seeds -------------------------------
    print(f"ranking {n_feat} features over {args.seeds} seeds ...")
    imp_sum = np.zeros(n_feat)
    rank_mat = np.zeros((args.seeds, n_feat))
    t0 = time.time()
    for s in range(args.seeds):
        m = _fit(args, XGBRegressor, s, Xtr, ytr_t, Xva, yva_t)
        bi = int(getattr(m, "best_iteration", args.n_estimators))
        dtr = xgb.DMatrix(Xtr, enable_categorical=True)
        contribs = m.get_booster().predict(dtr, pred_contribs=True,
                                           iteration_range=(0, bi + 1))
        mean_abs = np.abs(contribs[:, :-1]).mean(axis=0)   # drop bias col
        imp_sum += mean_abs
        rank_mat[s] = (-mean_abs).argsort().argsort()       # 0 = most important
        print(f"  seed {s}: best_iter={bi}")
    imp = imp_sum / args.seeds
    rank_std = rank_mat.std(axis=0)
    order = np.argsort(-imp)
    feat_sorted = [cols[i] for i in order]
    print(f"ranking done in {time.time() - t0:.1f}s")

    # ---- 2. RFE curve -----------------------------------------------------
    grid = sorted({min(int(g), n_feat) for g in args.grid.split(",") if g.strip()}, reverse=True)
    if n_feat not in grid:
        grid = [n_feat] + [g for g in grid if g < n_feat]
    print(f"RFE curve over K = {grid}")
    rows = []
    for k in grid:
        r = _val_row(args, XGBRegressor, feat_sorted[:k],
                     Xtr, ytr_t, Xva, yva_t, y_true, train_median, prep)
        rows.append(r)
        print(f"  K={k:4d}  spearman={r['spearman']:.3f}  mae={r['mae']:.2f}  "
              f"top10_mean={r['top10_mean']:.1f}  lift20={r['lift20']:.2f}  best_iter={r['best_iter']}")
    full = next(r for r in rows if r["k"] == n_feat)

    cons = _pick(rows, full, args.tol, args.lift_tol, args.mae_slack)
    aggr = _pick(rows, full, 3 * args.tol, 2 * args.lift_tol, 2 * args.mae_slack)
    cons_feats = feat_sorted[:cons["k"]]
    aggr_feats = feat_sorted[:aggr["k"]]

    args.keep_conservative_out.write_text("\n".join(cons_feats) + "\n", encoding="utf-8")
    args.keep_aggressive_out.write_text("\n".join(aggr_feats) + "\n", encoding="utf-8")

    # ---- 3. report ------------------------------------------------------
    L = []
    L.append("# SHAP feature pruning -- fromPR_High(%) XGBoost\n")
    L.append(f"- input: `{inp.name}`  (train {n_tr} / val {n_va}, cutoff {args.cutoff})")
    L.append(f"- ranking objective: `{args.objective}`, mean|SHAP| on the train fold, "
             f"averaged over {args.seeds} seeds")
    L.append(f"- tolerance gates vs full ({n_feat} feats): Spearman drop <= {args.tol} "
             f"(aggressive {3*args.tol:g}), top-10% lift@20 drop <= {args.lift_tol:g} "
             f"(aggressive {2*args.lift_tol:g})\n")

    L.append("## RFE curve (retrain + val at each K)\n")
    L.append("| K | val Spearman | val MAE | top-10% mean actual | lift@15 | lift@20 | lift@30 | best_iter | |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda r: -r["k"]):
        tag = []
        if r["k"] == n_feat:
            tag.append("full")
        if r["k"] == cons["k"]:
            tag.append("**conservative**")
        if r["k"] == aggr["k"]:
            tag.append("**aggressive**")
        L.append(f"| {r['k']} | {r['spearman']:.3f} | {r['mae']:.2f} | {r['top10_mean']:.1f} | "
                 f"{r['lift15']:.2f}x | {r['lift20']:.2f}x | {r['lift30']:.2f}x | {r['best_iter']} | "
                 f"{' '.join(tag)} |")
    L.append("")

    L.append("## Recommendation\n")
    L.append(f"- **conservative: keep {cons['k']}** -> `{args.keep_conservative_out.name}`  "
             f"(Spearman {cons['spearman']:.3f} vs {full['spearman']:.3f}, "
             f"MAE {cons['mae']:.2f} vs {full['mae']:.2f}, "
             f"lift@20 {cons['lift20']:.2f}x vs {full['lift20']:.2f}x)")
    L.append(f"- **aggressive: keep {aggr['k']}** -> `{args.keep_aggressive_out.name}`  "
             f"(Spearman {aggr['spearman']:.3f}, MAE {aggr['mae']:.2f}, lift@20 {aggr['lift20']:.2f}x)")
    L.append(f"\nConfirm on the quantile model:\n")
    L.append("```")
    L.append(f"train_xgb_fromPRhigh.py --input {inp.name} --features {args.keep_conservative_out.name} \\")
    L.append("  --objective reg:quantileerror --quantile-alpha 0.8 \\")
    L.append("  --model-out xgb_fromPRhigh_reg_q80_cons.ubj --prep-out xgb_fromPRhigh_reg_q80_cons_prep.json \\")
    L.append("  --report-out xgb_fromPRhigh_reg_q80_cons_report.md")
    L.append("```\n")

    gone_c = _families_gone(cols, cons_feats)
    gone_a = _families_gone(cols, aggr_feats)
    L.append("## Whole name-prefix families dropped\n")
    L.append(f"- at conservative K={cons['k']}: {', '.join(gone_c) if gone_c else '(none)'}")
    L.append(f"- at aggressive K={aggr['k']}: {', '.join(gone_a) if gone_a else '(none)'}")
    L.append("\n(TreeSHAP splits credit across correlated columns, so an encoder family can look "
             "individually weak; the RFE retrain-and-check above is the real guard.)\n")

    show = min(60, n_feat)
    L.append(f"## SHAP ranking -- top {show} of {n_feat} (train fold, mean|SHAP|)\n")
    L.append("| # | feature | mean|SHAP| | rank_std |")
    L.append("|---|---|---|---|")
    for i, j in enumerate(order[:show], 1):
        L.append(f"| {i} | `{cols[j]}` | {imp[j]:.4f} | {rank_std[j]:.1f} |")
    if n_feat > show:
        L.append(f"\n... {n_feat - show} more (full order is the head of "
                 f"`{args.keep_conservative_out.name}` / `{args.keep_aggressive_out.name}`)")
    L.append("")
    args.out.write_text("\n".join(L), encoding="utf-8")

    print(f"\nfull={n_feat}  conservative={cons['k']}  aggressive={aggr['k']}")
    print(f"wrote {args.out.name}, {args.keep_conservative_out.name}, {args.keep_aggressive_out.name}")


if __name__ == "__main__":
    main()
