#!/usr/bin/env python
"""
train_xgb_fromPRhigh.py -- XGBoost regressor for fromPR_High(%).

Reads the PCA-augmented training TSV (base cols + hd_pca_* + bd_pca_*), does a
temporal train/val split at --cutoff (ArrivalDate < cutoff = train), fits all
preprocessing on the TRAIN FOLD ONLY, trains an XGBRegressor with early stopping
on the val fold, and writes the model + a preprocessing spec + a metrics report.

Preprocessing (all params fit on train fold, applied to both folds):
  * ITI / trade-size sentinel 44444 ("IBKR had no bars"):
      RTH_avgITI_sec, ETH_avgITI_sec -> replace with 2 x train-max (order-
        preserving "very illiquid"), add <col>__nodata flag.
      RTH_tradeSize -> replace with 0 (no bars => no size), add __nodata flag.
  * heavy-tailed numeric features (train |skew| > --skew-thresh, > 20 uniques,
    excludes pca_/flag cols) -> winsorised to train [--winsor-lo, --winsor-hi].
  * non-numeric columns -> pandas category dtype (native XGBoost categoricals).
  * NaN passed through untouched (XGBoost learns a default direction).
  * target fromPR_High(%) -> upper-capped at train --target-winsor quantile,
    then --target-transform (asinh default); predictions inverted before scoring.

Trees are scale/monotone-invariant, so features are NOT standardised.

Outputs (all gitignored except the .md):
  xgb_fromPRhigh_reg.ubj          native XGBoost model
  xgb_fromPRhigh_reg_prep.json    preprocessing spec for inference parity
  xgb_fromPRhigh_reg_report.md    val-fold metrics + top feature importances

    /home/tom/venv/bin/python train_xgb_fromPRhigh.py
    /home/tom/venv/bin/python train_xgb_fromPRhigh.py --cutoff 2026-07-28 --target-transform asinh
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

HERE = Path(__file__).resolve().parent
csv.field_size_limit(16 * 1024 * 1024)

INPUT_GLOB = "08_*_PCA.tsv"
DEFAULT_CUTOFF = "2026-07-28"
THRESHOLDS = (15, 20, 30)
SENTINEL = 44444.0

# columns that are never features (identifiers / free text / leakage / the target)
DROP_COLS = {
    "Symbol", "ArrivalTime", "Headline", "Author", "Created", "Body", "ArrivalDate",
}
# sentinel -> "high" replacement + flag (order-preserving illiquidity signal)
ITI_HIGH_COLS = ("RTH_avgITI_sec", "ETH_avgITI_sec")
# sentinel -> 0 replacement + flag (no bars => no trade size)
ITI_ZERO_COLS = ("RTH_tradeSize",)


def _newest(glob):
    cands = list(HERE.glob(glob))
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _load(path, date_col):
    df = pd.read_csv(
        path, sep="\t", dtype=str, quoting=csv.QUOTE_NONE,
        keep_default_na=False, na_values=[""],
    )
    if date_col not in df.columns:
        sys.exit(f"date column {date_col!r} not in {path.name}")
    return df


def _coerce(df, label_col):
    """Return (num_df, cat_cols): every column numeric-coercible becomes float;
    the rest (except drops) become pandas categoricals."""
    feat_cols = [c for c in df.columns if c not in DROP_COLS and c != label_col]
    out = {}
    cat_cols = []
    for c in feat_cols:
        s = pd.to_numeric(df[c], errors="coerce")
        # treat as numeric only if it mostly parses
        if s.notna().mean() >= 0.5:
            out[c] = s.astype("float64")
        else:
            cat_cols.append(c)
            out[c] = df[c].where(df[c].notna(), None).astype("category")
    return pd.DataFrame(out, index=df.index), cat_cols


def _fit_sentinels(X, train_mask, prep):
    """Replace the 44444 sentinel and add __nodata flags. Mutates X, fills prep."""
    prep["sentinel_high"] = {}
    new_flags = []
    for c in ITI_HIGH_COLS:
        if c not in X.columns:
            continue
        is_sent = X[c].eq(SENTINEL)
        flag = f"{c}__nodata"
        X[flag] = is_sent.astype("float64")
        new_flags.append(flag)
        real_max = X.loc[train_mask & ~is_sent, c].max()
        hi = float(real_max) * 2.0 if np.isfinite(real_max) else SENTINEL
        X.loc[is_sent, c] = hi
        prep["sentinel_high"][c] = hi
    for c in ITI_ZERO_COLS:
        if c not in X.columns:
            continue
        is_sent = X[c].eq(SENTINEL)
        flag = f"{c}__nodata"
        X[flag] = is_sent.astype("float64")
        new_flags.append(flag)
        X.loc[is_sent, c] = 0.0
    prep["sentinel_zero"] = list(ITI_ZERO_COLS)
    return new_flags


def _fit_winsor(X, train_mask, num_cols, prep, skip_prefixes, lo_q, hi_q, skew_thresh):
    prep["winsor"] = {}
    tr = X.loc[train_mask]
    for c in num_cols:
        if any(c.startswith(p) for p in skip_prefixes) or c.endswith("__nodata"):
            continue
        col = tr[c].dropna()
        if col.nunique() <= 20 or len(col) < 100:
            continue
        if abs(float(stats.skew(col))) <= skew_thresh:
            continue
        lo, hi = np.quantile(col, [lo_q, hi_q])
        if lo == hi:
            continue
        X[c] = X[c].clip(lower=lo, upper=hi)
        prep["winsor"][c] = [float(lo), float(hi)]


def _fit_target(y_raw, train_mask, transform, winsor_q, prep):
    ytr = y_raw[train_mask].dropna()
    cap = float(np.quantile(ytr, winsor_q))
    y = np.minimum(y_raw.to_numpy(dtype="float64"), cap)
    prep["target"] = {"col": prep["label_col"], "winsor_cap": cap, "transform": transform}
    if transform == "asinh":
        yt = np.arcsinh(y)
    elif transform == "log1p":
        shift = float(np.nanmin(y[train_mask])) - 1.0
        prep["target"]["shift"] = shift
        yt = np.log1p(y - shift)
    elif transform == "none":
        yt = y
    else:
        sys.exit(f"unknown --target-transform {transform!r}")
    return yt


def _inverse_target(pred_t, prep):
    t = prep["target"]["transform"]
    if t == "asinh":
        return np.sinh(pred_t)
    if t == "log1p":
        return np.expm1(pred_t) + prep["target"]["shift"]
    return pred_t


def _metrics(y_true, y_pred, train_median):
    err = y_pred - y_true
    m = {
        "n": int(len(y_true)),
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "medae": float(np.median(np.abs(err))),
        "mae_baseline_trainmedian": float(np.mean(np.abs(y_true - train_median))),
        "pearson_r": float(stats.pearsonr(y_true, y_pred)[0]),
        "spearman_rho": float(stats.spearmanr(y_true, y_pred)[0]),
    }
    order = np.argsort(-y_pred)
    base = {thr: float(np.mean(y_true >= thr)) for thr in THRESHOLDS}
    m["base_rate"] = base
    m["top_frac"] = {}
    for frac in (0.05, 0.10, 0.20):
        k = max(1, int(round(frac * len(y_true))))
        sel = y_true[order[:k]]
        m["top_frac"][f"{frac:.2f}"] = {
            "k": k,
            "mean_actual": float(np.mean(sel)),
            "hit": {thr: float(np.mean(sel >= thr)) for thr in THRESHOLDS},
            "lift": {thr: (float(np.mean(sel >= thr)) / base[thr] if base[thr] else None)
                     for thr in THRESHOLDS},
        }
    return m


def _write_report(path, args, inp, n_tr, n_va, prep, m, best_iter, importances):
    full_train = bool(getattr(args, "full_train", False))
    L = []
    L.append("# XGBoost regressor -- fromPR_High(%)\n")
    L.append(f"- input: `{inp.name}`")
    if full_train:
        L.append(f"- **--full-train**: 100% of data used for training, 0 held out -> train {n_tr} / val {n_va}")
    else:
        L.append(f"- temporal split: **{args.cutoff}** (ArrivalDate < cutoff) -> train {n_tr} / val {n_va}")
    L.append(f"- target transform: **{prep['target']['transform']}**, "
             f"train upper-cap at q{args.target_winsor:g} = {prep['target']['winsor_cap']:.1f}%")
    L.append(f"- winsorised features: {len(prep['winsor'])} "
             f"(train |skew| > {args.skew_thresh:g}, clip [{args.winsor_lo:g}, {args.winsor_hi:g}])")
    L.append(f"- sentinel 44444 fixed: {', '.join(prep['sentinel_high'])} -> 2x train-max + flag; "
             f"{', '.join(prep['sentinel_zero'])} -> 0 + flag")
    if full_train:
        L.append(f"- n_estimators: **{best_iter}** (fixed, no early stopping -- no held-out "
                  f"fold to stop against; round count set from a prior validated run)\n")
    else:
        L.append(f"- best_iteration: **{best_iter}**  (early stopping)\n")

    if full_train:
        L.append("## No validation fold\n")
        L.append("This run used 100% of rows for training (`--full-train`), so there is no "
                  "held-out set to score against -- no MAE/Spearman/lift table here. Its quality "
                  "claim rests on whatever prior run (feature set + hyperparameters) validated "
                  "this configuration on a real holdout.\n")
    else:
        L.append("## Val-fold metrics (original % units, uncapped target)\n")
        L.append(f"| MAE | RMSE | MedAE | MAE (predict train median) | Pearson r | Spearman rho |")
        L.append("|---|---|---|---|---|---|")
        L.append(f"| {m['mae']:.2f} | {m['rmse']:.2f} | {m['medae']:.2f} | "
                 f"{m['mae_baseline_trainmedian']:.2f} | {m['pearson_r']:.3f} | {m['spearman_rho']:.3f} |\n")

        L.append("## Ranking / trading lift (val)\n")
        L.append("| top predicted | k | mean actual % | hit >=15 | hit >=20 | hit >=30 | lift >=15 | lift >=20 | lift >=30 |")
        L.append("|---|---|---|---|---|---|---|---|---|")
        for frac, d in m["top_frac"].items():
            h, lf = d["hit"], d["lift"]
            L.append(f"| {float(frac):.0%} | {d['k']} | {d['mean_actual']:.1f} | "
                     f"{h[15]:.3f} | {h[20]:.3f} | {h[30]:.3f} | "
                     f"{(lf[15] or 0):.2f}x | {(lf[20] or 0):.2f}x | {(lf[30] or 0):.2f}x |")
        br = m["base_rate"]
        L.append(f"\nval base rates: >=15 {br[15]:.3f}, >=20 {br[20]:.3f}, >=30 {br[30]:.3f}\n")

    L.append("## Top 40 features by gain\n")
    L.append("| # | feature | gain |")
    L.append("|---|---|---|")
    for i, (f, g) in enumerate(importances[:40], 1):
        L.append(f"| {i} | `{f}` | {g:.1f} |")
    L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def build_xy(args):
    """Resolve input, temporal split, fit all preprocessing on the TRAIN FOLD.
    Returns (inp, X, yt, y_raw, train_mask, val_mask, prep, n_tr, n_va).
    X holds every candidate feature (no allowlist applied here)."""
    inp = args.input or _newest(INPUT_GLOB)
    if not inp or not inp.exists():
        sys.exit(f"no input TSV (looked for {INPUT_GLOB})")
    print(f"input: {inp.name}")

    df = _load(inp, args.date_col)
    dates = pd.to_datetime(df[args.date_col], errors="coerce")
    y_raw = pd.to_numeric(df[args.label_col], errors="coerce")
    keep = y_raw.notna().to_numpy() & dates.notna().to_numpy()
    full_train = bool(getattr(args, "full_train", False))
    if full_train:
        train_mask = keep.copy()
        val_mask = np.zeros_like(train_mask, dtype=bool)
    else:
        train_mask = (dates < pd.Timestamp(args.cutoff)).to_numpy() & keep
        val_mask = ~(dates < pd.Timestamp(args.cutoff)).to_numpy() & dates.notna().to_numpy() & keep
    n_tr, n_va = int(train_mask.sum()), int(val_mask.sum())
    print(f"rows: train {n_tr} / val {n_va}"
          + (" (--full-train, no held-out fold)" if full_train else f" (cutoff {args.cutoff})"))
    if n_tr < 200 or (not full_train and n_va < 100):
        sys.exit("split too small -- check --cutoff / --date-col")

    X, cat_cols = _coerce(df, args.label_col)
    num_cols = [c for c in X.columns if c not in cat_cols]

    prep = {
        "input": inp.name,
        "cutoff": args.cutoff,
        "label_col": args.label_col,
        "cat_cols": cat_cols,
        "categories": {c: [x for x in X[c].cat.categories.tolist()] for c in cat_cols},
    }
    flags = _fit_sentinels(X, train_mask, prep)
    num_cols += flags
    _fit_winsor(X, train_mask, num_cols, prep, ("hd_pca_", "bd_pca_"),
                args.winsor_lo, args.winsor_hi, args.skew_thresh)
    prep["feature_order"] = list(X.columns)

    yt = _fit_target(y_raw, train_mask, args.target_transform, args.target_winsor, prep)
    return inp, X, yt, y_raw, train_mask, val_mask, prep, n_tr, n_va


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", type=Path, default=None, help=f"default: newest {INPUT_GLOB}")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF)
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--label-col", default="fromPR_High(%)")
    ap.add_argument("--target-transform", default="asinh", choices=["asinh", "log1p", "none"])
    ap.add_argument("--target-winsor", type=float, default=0.995, help="train quantile for upper cap")
    ap.add_argument("--winsor-lo", type=float, default=0.005)
    ap.add_argument("--winsor-hi", type=float, default=0.995)
    ap.add_argument("--skew-thresh", type=float, default=4.0)
    ap.add_argument("--objective", default="reg:squarederror")
    ap.add_argument("--quantile-alpha", type=float, default=0.8,
                    help="target quantile when --objective reg:quantileerror")
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
    ap.add_argument("--features", type=Path, default=None,
                    help="newline file of feature names to keep (allowlist); default = all")
    ap.add_argument("--full-train", action="store_true",
                    help="use 100%% of rows for training (0 held out); disables early "
                         "stopping (no eval_set) -- --n-estimators becomes a FIXED round "
                         "count instead of a cap, and no val metrics are written")
    ap.add_argument("--model-out", type=Path, default=HERE / "xgb_fromPRhigh_reg.ubj")
    ap.add_argument("--prep-out", type=Path, default=HERE / "xgb_fromPRhigh_reg_prep.json")
    ap.add_argument("--report-out", type=Path, default=HERE / "xgb_fromPRhigh_reg_report.md")
    args = ap.parse_args()

    try:
        from xgboost import XGBRegressor
    except ImportError:
        sys.exit("xgboost not installed -- run: /home/tom/venv/bin/pip install xgboost")

    inp, X, yt, y_raw, train_mask, val_mask, prep, n_tr, n_va = build_xy(args)

    if args.features:
        want = [ln.strip() for ln in args.features.read_text().splitlines() if ln.strip()]
        want_set = set(want)
        kept = [c for c in X.columns if c in want_set]
        missing = [c for c in want if c not in X.columns]
        if not kept:
            sys.exit(f"--features {args.features.name}: no listed name matches a feature column")
        if missing:
            print(f"  --features: {len(missing)} listed names not in X (skipped): {missing[:5]}")
        X = X[kept]
        prep["features_file"] = args.features.name
        prep["features_used"] = kept
        prep["n_features_used"] = len(kept)
        print(f"  --features: kept {len(kept)}/{len(prep['feature_order'])}")

    Xtr, Xva = X.loc[train_mask], X.loc[val_mask]
    ytr_t, yva_t = yt[train_mask], yt[val_mask]

    extra = {}
    if args.objective == "reg:quantileerror":
        extra["quantile_alpha"] = args.quantile_alpha
    model = XGBRegressor(
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
        early_stopping_rounds=None if args.full_train else args.early_stop,
        random_state=0,
        n_jobs=args.jobs,
    )
    t0 = time.time()
    if args.full_train:
        model.fit(Xtr, ytr_t, verbose=args.verbose_eval)
        best_iter = args.n_estimators   # fixed round count, not early-stopped
        print(f"trained in {time.time() - t0:.1f}s  n_estimators={best_iter} (--full-train, fixed)")
        m = None
    else:
        model.fit(Xtr, ytr_t, eval_set=[(Xtr, ytr_t), (Xva, yva_t)], verbose=args.verbose_eval)
        best_iter = int(getattr(model, "best_iteration", args.n_estimators))
        print(f"trained in {time.time() - t0:.1f}s  best_iteration={best_iter}")

        pred_va = _inverse_target(model.predict(Xva), prep)
        y_true = y_raw[val_mask].to_numpy(dtype="float64")   # uncapped, real target
        train_median = float(np.median(y_raw[train_mask]))
        m = _metrics(y_true, pred_va, train_median)
        print(f"val MAE={m['mae']:.2f} (baseline {m['mae_baseline_trainmedian']:.2f})  "
              f"Spearman={m['spearman_rho']:.3f}  "
              f"top10% mean actual={m['top_frac']['0.10']['mean_actual']:.1f}%  "
              f"hit>=20={m['top_frac']['0.10']['hit'][20]:.3f} "
              f"(lift {m['top_frac']['0.10']['lift'][20]:.2f}x)")

    booster = model.get_booster()
    gain = booster.get_score(importance_type="gain")
    importances = sorted(gain.items(), key=lambda kv: -kv[1])

    args.model_out.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(args.model_out))
    prep_out = dict(prep)
    prep_out["metrics"] = m
    prep_out["best_iteration"] = best_iter
    prep_out["full_train"] = args.full_train
    if args.full_train:
        prep_out["n_estimators_fixed"] = args.n_estimators
    prep_out["params"] = model.get_params()
    args.prep_out.write_text(json.dumps(prep_out, indent=2, default=str), encoding="utf-8")
    _write_report(args.report_out, args, inp, n_tr, n_va, prep, m, best_iter, importances)
    print(f"wrote {args.model_out.name}, {args.prep_out.name}, {args.report_out.name}")


if __name__ == "__main__":
    main()
