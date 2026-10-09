#!/usr/bin/env python
"""
train_xgb_fromPRhigh_clf.py -- binary XGBoost classifiers for fromPR_High(%).

Trains one classifier per --threshold (default 20, 35, 50): target is
`fromPR_High(%) >= t`. Shares the regressor's preprocessing via
`train_xgb_fromPRhigh.build_xy` (temporal split at --cutoff, train-fold-only
sentinel/winsor fit) and the same 49-feature SHAP allowlist by default.

Class imbalance -> `scale_pos_weight = neg/pos` on the train fold per threshold.
That ranks well but leaves predicted probabilities miscalibrated (no Platt /
isotonic here) -- rank by proba, don't read it as a true probability.

Outputs (gitignored except the .md):
  xgb_fromPRhigh_clf_ge{t}.ubj        native model, per threshold
  xgb_fromPRhigh_clf_ge{t}_prep.json  preprocessing + metrics, per threshold
  xgb_fromPRhigh_clf_report.md        combined summary + per-threshold detail

    /home/tom/venv/bin/python train_xgb_fromPRhigh_clf.py \
      --input 08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_PCA.tsv
"""

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

from train_xgb_fromPRhigh import build_xy, _newest, INPUT_GLOB

HERE = Path(__file__).resolve().parent
DEFAULT_FEATURES = HERE / "xgb_fromPRhigh_features_conservative.txt"
TOP_FRACS = (0.05, 0.10, 0.20)


def _clf_metrics(y_true, p):
    from sklearn.metrics import (roc_auc_score, average_precision_score,
                                 brier_score_loss, log_loss)
    y_true = y_true.astype(int)
    base = float(y_true.mean())
    order = np.argsort(-p)
    n_pos = int(y_true.sum())
    m = {
        "n": int(len(y_true)),
        "base_rate": base,
        "roc_auc": float(roc_auc_score(y_true, p)),
        "pr_auc": float(average_precision_score(y_true, p)),
        "brier": float(brier_score_loss(y_true, p)),
        "log_loss": float(log_loss(y_true, np.clip(p, 1e-7, 1 - 1e-7))),
        "top_frac": {},
    }
    for frac in TOP_FRACS:
        k = max(1, int(round(frac * len(y_true))))
        sel = y_true[order[:k]]
        prec = float(sel.mean())
        m["top_frac"][f"{frac:.2f}"] = {
            "k": k,
            "precision": prec,
            "recall": float(sel.sum() / n_pos) if n_pos else 0.0,
            "lift": prec / base if base else None,
        }
    # p >= 0.5 operating point (high-recall after reweighting)
    pred = (p >= 0.5).astype(int)
    tp = int(((pred == 1) & (y_true == 1)).sum())
    fp = int(((pred == 1) & (y_true == 0)).sum())
    fn = int(((pred == 0) & (y_true == 1)).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    m["p50"] = {
        "precision": prec, "recall": rec,
        "f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "n_flagged": tp + fp,
    }
    return m


def _write_report(path, inp, cutoff, n_tr, n_va, rows, full_train=False):
    L = []
    L.append("# XGBoost binary classifiers -- fromPR_High(%) thresholds\n")
    if full_train:
        L.append(f"- input: `{inp.name}`  (**--full-train**: train {n_tr} / val {n_va}, 0 held out)")
    else:
        L.append(f"- input: `{inp.name}`  (train {n_tr} / val {n_va}, cutoff {cutoff})")
    L.append("- features: SHAP-pruned allowlist  |  imbalance: "
             "`scale_pos_weight = neg/pos` (train fold)")
    L.append("- probabilities are **not calibrated** (reweighted) -- use as a ranking score\n")

    if full_train:
        L.append("## No validation fold\n")
        L.append("This run used 100% of rows for training (`--full-train`), so there is no "
                  "held-out set to score AUC/precision/lift against. `best_iteration` below is "
                  "a **fixed** round count (from a prior validated run), not early-stopped.\n")
        L.append("| >= | scale_pos_weight | n_estimators (fixed) |")
        L.append("|---|---|---|")
        for r in rows:
            L.append(f"| {r['threshold']} | {r['scale_pos_weight']:.2f} | {r['best_iteration']} |")
        L.append("")
    else:
        L.append("## Summary (val fold)\n")
        L.append("| >= | base rate | ROC-AUC | PR-AUC | top-10% precision | lift | Brier | best_iter |")
        L.append("|---|---|---|---|---|---|---|---|")
        for r in rows:
            t10 = r["metrics"]["top_frac"]["0.10"]
            L.append(f"| {r['threshold']} | {r['metrics']['base_rate']:.3f} | "
                     f"{r['metrics']['roc_auc']:.3f} | {r['metrics']['pr_auc']:.3f} | "
                     f"{t10['precision']:.3f} | {t10['lift']:.2f}x | "
                     f"{r['metrics']['brier']:.3f} | {r['best_iteration']} |")
        L.append("")

        for r in rows:
            mm = r["metrics"]
            L.append(f"## fromPR_High(%) >= {r['threshold']}  "
                     f"(scale_pos_weight {r['scale_pos_weight']:.2f})\n")
            L.append("| top predicted | k | precision | recall | lift |")
            L.append("|---|---|---|---|---|")
            for frac, d in mm["top_frac"].items():
                L.append(f"| {float(frac):.0%} | {d['k']} | {d['precision']:.3f} | "
                         f"{d['recall']:.3f} | {(d['lift'] or 0):.2f}x |")
            p50 = mm["p50"]
            L.append(f"\n`p>=0.5`: precision {p50['precision']:.3f}, recall {p50['recall']:.3f}, "
                     f"F1 {p50['f1']:.3f}, flagged {p50['n_flagged']}/{mm['n']}\n")
            L.append("Top 20 features by gain:\n")
            L.append("| # | feature | gain |")
            L.append("|---|---|---|")
            for i, (f, g) in enumerate(r["importances"][:20], 1):
                L.append(f"| {i} | `{f}` | {g:.1f} |")
            L.append("")

    if full_train:
        for r in rows:
            L.append(f"## fromPR_High(%) >= {r['threshold']} -- top 20 features by gain\n")
            L.append("| # | feature | gain |")
            L.append("|---|---|---|")
            for i, (f, g) in enumerate(r["importances"][:20], 1):
                L.append(f"| {i} | `{f}` | {g:.1f} |")
            L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=None, help=f"default: newest {INPUT_GLOB}")
    ap.add_argument("--cutoff", default="2026-07-28")
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--label-col", default="fromPR_High(%)")
    ap.add_argument("--thresholds", default="20,35,50")
    ap.add_argument("--features", type=Path, default=DEFAULT_FEATURES,
                    help="newline allowlist of feature names")
    # preprocessing knobs consumed by build_xy (kept identical to the regressor)
    ap.add_argument("--target-transform", default="asinh")   # unused here, build_xy needs it
    ap.add_argument("--target-winsor", type=float, default=0.995)
    ap.add_argument("--winsor-lo", type=float, default=0.005)
    ap.add_argument("--winsor-hi", type=float, default=0.995)
    ap.add_argument("--skew-thresh", type=float, default=4.0)
    # model hyperparams (match train_xgb_fromPRhigh defaults)
    ap.add_argument("--n-estimators", type=int, default=3000)
    ap.add_argument("--eta", type=float, default=0.03)
    ap.add_argument("--max-depth", type=int, default=4)
    ap.add_argument("--min-child-weight", type=float, default=5.0)
    ap.add_argument("--subsample", type=float, default=0.8)
    ap.add_argument("--colsample-bytree", type=float, default=0.7)
    ap.add_argument("--reg-lambda", type=float, default=1.0)
    ap.add_argument("--early-stop", type=int, default=100)
    ap.add_argument("--jobs", type=int, default=-1)
    ap.add_argument("--verbose-eval", type=int, default=0)
    ap.add_argument("--full-train", action="store_true",
                    help="use 100%% of rows for training (0 held out); disables early "
                         "stopping -- --n-estimators becomes a FIXED round count and no "
                         "val metrics are written")
    ap.add_argument("--out", type=Path, default=HERE / "xgb_fromPRhigh_clf_report.md")
    args = ap.parse_args()

    try:
        from xgboost import XGBClassifier
    except ImportError:
        sys.exit("xgboost not installed -- run: /home/tom/venv/bin/pip install xgboost")

    if args.input is None:
        args.input = _newest(INPUT_GLOB)
    thresholds = [float(t) for t in args.thresholds.split(",") if t.strip()]

    inp, X, _yt, y_raw, train_mask, val_mask, prep, n_tr, n_va = build_xy(args)

    want = [ln.strip() for ln in args.features.read_text().splitlines() if ln.strip()]
    want_set = set(want)
    kept = [c for c in X.columns if c in want_set]
    missing = [c for c in want if c not in X.columns]
    if not kept:
        sys.exit(f"--features {args.features.name}: no name matches a feature column")
    if missing:
        print(f"  --features: {len(missing)} listed names not in X (skipped): {missing[:5]}")
    X = X[kept]
    print(f"  --features: kept {len(kept)}/{len(prep['feature_order'])}")
    prep = dict(prep)
    prep["features_file"] = args.features.name
    prep["features_used"] = kept

    Xtr, Xva = X.loc[train_mask], X.loc[val_mask]
    y_np = y_raw.to_numpy(dtype="float64")
    ytr_raw, yva_raw = y_np[train_mask], y_np[val_mask]

    rows = []
    for t in thresholds:
        ytr = (ytr_raw >= t).astype(int)
        yva = (yva_raw >= t).astype(int)
        pos = int(ytr.sum())
        spw = float((len(ytr) - pos) / pos) if pos else 1.0
        model = XGBClassifier(
            objective="binary:logistic",
            eval_metric=["auc", "aucpr"],
            scale_pos_weight=spw,
            n_estimators=args.n_estimators,
            learning_rate=args.eta,
            max_depth=args.max_depth,
            min_child_weight=args.min_child_weight,
            subsample=args.subsample,
            colsample_bytree=args.colsample_bytree,
            reg_lambda=args.reg_lambda,
            tree_method="hist",
            enable_categorical=True,
            early_stopping_rounds=None if args.full_train else args.early_stop,
            random_state=0,
            n_jobs=args.jobs,
        )
        t0 = time.time()
        if args.full_train:
            model.fit(Xtr, ytr, verbose=args.verbose_eval)
            best_iter = args.n_estimators   # fixed round count, not early-stopped
            m = None
            print(f"ge{t:g}: trained {time.time() - t0:.1f}s  n_estimators={best_iter} "
                  f"(--full-train, fixed)")
        else:
            model.fit(Xtr, ytr, eval_set=[(Xtr, ytr), (Xva, yva)], verbose=args.verbose_eval)
            best_iter = int(getattr(model, "best_iteration", args.n_estimators))
            p_va = model.predict_proba(Xva)[:, 1]
            m = _clf_metrics(yva, p_va)
            t10 = m["top_frac"]["0.10"]
            print(f"ge{t:g}: AUC={m['roc_auc']:.3f} PR-AUC={m['pr_auc']:.3f} "
                  f"top10% precision={t10['precision']:.3f} (lift {t10['lift']:.2f}x) "
                  f"base={m['base_rate']:.3f}  [{time.time() - t0:.1f}s, best_iter={best_iter}]")

        booster = model.get_booster()
        importances = sorted(booster.get_score(importance_type="gain").items(),
                             key=lambda kv: -kv[1])
        tag = f"ge{int(t)}" if float(t).is_integer() else f"ge{t:g}"
        booster.save_model(str(HERE / f"xgb_fromPRhigh_clf_{tag}.ubj"))
        (HERE / f"xgb_fromPRhigh_clf_{tag}_prep.json").write_text(
            json.dumps({**prep, "threshold": t, "scale_pos_weight": spw,
                        "metrics": m, "best_iteration": best_iter,
                        "full_train": args.full_train,
                        "params": model.get_params()}, indent=2, default=str),
            encoding="utf-8")
        rows.append({"threshold": (int(t) if float(t).is_integer() else t),
                     "scale_pos_weight": spw, "metrics": m,
                     "best_iteration": best_iter, "importances": importances})

    _write_report(args.out, inp, args.cutoff, n_tr, n_va, rows, full_train=args.full_train)
    print(f"wrote {args.out.name} + {len(rows)} model/prep pairs")


if __name__ == "__main__":
    main()
