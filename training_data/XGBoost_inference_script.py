#!/usr/bin/env python
"""
XGBoost_inference_script.py -- score ONE PR row with the trained regressor
and/or the 3 threshold classifiers.

This is the missing "apply saved parameters" counterpart to the fit-time
preprocessing in train_xgb_fromPRhigh.build_xy: it replays, per model's own
_prep.json, the sentinel substitution, winsorisation, categorical casting and
feature selection, re-embeds + PCA-projects Headline/Body, predicts, and (for
the regressor) inverts the target transform.

Input: one row from an existing base-schema TSV (208 cols: Headline/Body +
all structured columns), selected by --row-idx or by --symbol + --arrival-time.
This does NOT compute structured features (ATR, MktCap, sentiment scores, ...)
from scratch -- those come from the upstream pipeline, not from PR text alone.

    /home/tom/venv/bin/python XGBoost_inference_script.py \\
        --symbol ABCD --arrival-time "2026-08-01 09:31:00" --kind all
    /home/tom/venv/bin/python XGBoost_inference_script.py --row-idx 4200 --kind reg_se
"""

import argparse
import csv
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

HERE = Path(__file__).resolve().parent
csv.field_size_limit(16 * 1024 * 1024)

SENTINEL = 44444.0
KIND_NAMES = ("reg_se", "reg_q80", "clf_ge20", "clf_ge35", "clf_ge50")
ALL_KINDS = ("reg_se", "clf_ge20", "clf_ge35", "clf_ge50")

# model-dir -> kind -> (model file, prep file, "regressor" | "classifier").
# Kind names stay stable across generations even though the underlying .ubj
# stems differ (model-1's 49-feature "cons49" vs model-2/2FT's 25-feature "cons").
MODEL_SPECS = {
    "model-1": {
        "reg_se": ("xgb_cons49_se.ubj", "xgb_cons49_se_prep.json", "regressor"),
        "reg_q80": ("xgb_cons49_q80.ubj", "xgb_cons49_q80_prep.json", "regressor"),
        "clf_ge20": ("xgb_fromPRhigh_clf_ge20.ubj", "xgb_fromPRhigh_clf_ge20_prep.json", "classifier"),
        "clf_ge35": ("xgb_fromPRhigh_clf_ge35.ubj", "xgb_fromPRhigh_clf_ge35_prep.json", "classifier"),
        "clf_ge50": ("xgb_fromPRhigh_clf_ge50.ubj", "xgb_fromPRhigh_clf_ge50_prep.json", "classifier"),
    },
    "model-2": {
        "reg_se": ("xgb_cons_se.ubj", "xgb_cons_se_prep.json", "regressor"),
        "reg_q80": ("xgb_cons_q80.ubj", "xgb_cons_q80_prep.json", "regressor"),
        "clf_ge20": ("xgb_fromPRhigh_clf_ge20.ubj", "xgb_fromPRhigh_clf_ge20_prep.json", "classifier"),
        "clf_ge35": ("xgb_fromPRhigh_clf_ge35.ubj", "xgb_fromPRhigh_clf_ge35_prep.json", "classifier"),
        "clf_ge50": ("xgb_fromPRhigh_clf_ge50.ubj", "xgb_fromPRhigh_clf_ge50_prep.json", "classifier"),
    },
    "model-2FT": {  # same stems as model-2 -- only the dir differs
        "reg_se": ("xgb_cons_se.ubj", "xgb_cons_se_prep.json", "regressor"),
        "reg_q80": ("xgb_cons_q80.ubj", "xgb_cons_q80_prep.json", "regressor"),
        "clf_ge20": ("xgb_fromPRhigh_clf_ge20.ubj", "xgb_fromPRhigh_clf_ge20_prep.json", "classifier"),
        "clf_ge35": ("xgb_fromPRhigh_clf_ge35.ubj", "xgb_fromPRhigh_clf_ge35_prep.json", "classifier"),
        "clf_ge50": ("xgb_fromPRhigh_clf_ge50.ubj", "xgb_fromPRhigh_clf_ge50_prep.json", "classifier"),
    },
}

# default base TSV (raw Headline/Body + structured cols, pre-PCA) per model-dir --
# explicit, not "newest by mtime": several similarly-suffixed files now coexist.
DEFAULT_BASE_TSV = {
    "model-1": "08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol.tsv",
    "model-2": "08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_prosusReScore_reCat.tsv",
    "model-2FT": "08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_prosusReScore_reCat.tsv",
}

MODEL_DIR = HERE / "model-1"
MODEL_MAP = MODEL_SPECS["model-1"]


def load_prep(kind):
    _, prep_name, _ = MODEL_MAP[kind]
    return json.loads((MODEL_DIR / prep_name).read_text(encoding="utf-8"))


def load_booster(kind):
    ubj_name, _, _ = MODEL_MAP[kind]
    b = xgb.Booster()
    b.load_model(str(MODEL_DIR / ubj_name))
    return b


def read_base_row(tsv_path, row_idx=None, symbol=None, arrival_time=None):
    with open(tsv_path, newline="", encoding="utf-8") as fh:
        r = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(r)
        rows = list(r)
    if row_idx is not None:
        if not (0 <= row_idx < len(rows)):
            sys.exit(f"--row-idx {row_idx} out of range (0..{len(rows) - 1})")
        idx, row = row_idx, rows[row_idx]
    else:
        si, ai = header.index("Symbol"), header.index("ArrivalTime")
        matches = [(i, row) for i, row in enumerate(rows)
                   if row[si] == symbol and row[ai] == arrival_time]
        if len(matches) != 1:
            sys.exit(f"expected exactly 1 row matching Symbol={symbol!r} "
                     f"ArrivalTime={arrival_time!r}, found {len(matches)}")
        idx, row = matches[0]
    return dict(zip(header, row)), idx


def build_row_features(raw_row, prep, hd_pca, bd_pca):
    """Replay sentinel/winsor/categorical/feature-select for ONE row.
    raw_row: dict[col] = raw string value from the base TSV.
    hd_pca / bd_pca: lists of floats (already PCA-projected embeddings).
    Returns a 1-row DataFrame with dtypes matching training."""
    values = {}
    for col, hi in prep["sentinel_high"].items():
        v = float(raw_row[col])
        values[f"{col}__nodata"] = 1.0 if v == SENTINEL else 0.0
        values[col] = hi if v == SENTINEL else v
    for col in prep["sentinel_zero"]:
        v = float(raw_row[col])
        values[f"{col}__nodata"] = 1.0 if v == SENTINEL else 0.0
        values[col] = 0.0 if v == SENTINEL else v

    cat_set = set(prep["cat_cols"])
    feats = prep["features_used"]
    for col in feats:
        if col in values:
            continue
        if col.startswith("hd_pca_"):
            values[col] = float(hd_pca[int(col.rsplit("_", 1)[1])])
        elif col.startswith("bd_pca_"):
            values[col] = float(bd_pca[int(col.rsplit("_", 1)[1])])
        elif col in cat_set:
            raw = raw_row.get(col, "")
            values[col] = raw if raw not in ("", None) else None
        else:
            raw = raw_row.get(col, "")
            try:
                values[col] = float(raw)
            except (TypeError, ValueError):
                values[col] = np.nan

    for col, (lo, hi) in prep["winsor"].items():
        if col in feats and col not in cat_set:
            values[col] = float(min(max(values[col], lo), hi))

    df = pd.DataFrame([{c: values[c] for c in feats}], columns=feats)
    for col in feats:
        if col in cat_set:
            df[col] = pd.Categorical(df[col], categories=prep["categories"][col])
        else:
            df[col] = df[col].astype("float64")
    return df


def _invert_target(raw_pred, prep):
    t = prep["target"]["transform"]
    if t == "asinh":
        return float(np.sinh(raw_pred))
    if t == "log1p":
        return float(np.expm1(raw_pred) + prep["target"]["shift"])
    return float(raw_pred)


def _iteration_range(booster):
    """Predict only up to best_iteration, not every boosted round -- a booster that
    early-stopped keeps every tree it built, and predict() defaults to using all of
    them, scoring a different (overfit-tail) model than the one its _report.md quotes."""
    bi = booster.attributes().get("best_iteration")
    return (0, int(bi) + 1) if bi is not None else None


def predict_one(raw_row, hd_pca, bd_pca, kind):
    prep = load_prep(kind)
    df = build_row_features(raw_row, prep, hd_pca, bd_pca)
    booster = load_booster(kind)
    dm = xgb.DMatrix(df, enable_categorical=True)
    itr = _iteration_range(booster)
    raw_pred = float(booster.predict(dm, iteration_range=itr)[0] if itr
                      else booster.predict(dm)[0])
    mtype = MODEL_MAP[kind][2]
    pred = _invert_target(raw_pred, prep) if mtype == "regressor" else raw_pred
    return pred, df, prep


def main():
    global MODEL_DIR, MODEL_MAP
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model-dir", default="model-1", choices=list(MODEL_SPECS),
                    help="which trained generation to score against (default: model-1)")
    ap.add_argument("--input-tsv", type=Path, default=None,
                    help="default: DEFAULT_BASE_TSV[--model-dir]")
    ap.add_argument("--row-idx", type=int, default=None)
    ap.add_argument("--symbol")
    ap.add_argument("--arrival-time")
    ap.add_argument("--kind", default="all", choices=list(KIND_NAMES) + ["all"])
    args = ap.parse_args()

    MODEL_DIR = HERE / args.model_dir
    MODEL_MAP = MODEL_SPECS[args.model_dir]

    tsv = args.input_tsv or (HERE / DEFAULT_BASE_TSV[args.model_dir])
    if not tsv or not tsv.exists():
        sys.exit(f"base TSV not found: {tsv}")
    if args.row_idx is None and not (args.symbol and args.arrival_time):
        sys.exit("need --row-idx OR both --symbol and --arrival-time")

    raw_row, idx = read_base_row(tsv, args.row_idx, args.symbol, args.arrival_time)

    from finbert_embed_infer import embed_pr
    from finbert_pca_transform import transform_pr
    pcs = transform_pr(embed_pr(raw_row.get("Headline", ""), raw_row.get("Body", "")))

    kinds = list(ALL_KINDS) if args.kind == "all" else [args.kind]
    actual = raw_row.get("fromPR_High(%)", "")
    print(f"row_idx={idx}  Symbol={raw_row.get('Symbol')}  "
          f"ArrivalTime={raw_row.get('ArrivalTime')}  actual fromPR_High(%)={actual}")
    for k in kinds:
        pred, _, prep = predict_one(raw_row, pcs["hd_pca"], pcs["bd_pca"], k)
        if MODEL_MAP[k][2] == "regressor":
            print(f"  [{k}] predicted fromPR_High(%) = {pred:.2f}")
        else:
            print(f"  [{k}] P(fromPR_High(%) >= {int(prep['threshold'])}) = {pred:.3f}")


if __name__ == "__main__":
    main()
