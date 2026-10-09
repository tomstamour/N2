#!/usr/bin/env python
"""
score_val_predictions.py -- write one row per validation PR with every model's
score: the regressor (SE point estimate + q80 upper-quantile estimate) and the
three threshold classifiers' probabilities, alongside the actual target.

Confirmed (this session) that all 5 models share identical preprocessing
(same input file, cutoff, winsor bounds, sentinel constants, categories,
49-feature list) -- so build_xy() is called ONCE and each model's booster is
scored on the same X, sliced to its own features_used (all 49, same order).

!! PREDICTS AT best_iteration, NOT AT ALL ROUNDS. Four of the five models early-stopped
   (clf ge20 145/246, ge35 163/264, ge50 209/310, cons49_se 235/336; cons49_q80 2999/3000
   never stopped). Booster.predict() with no iteration_range uses EVERY tree, including the
   ~100 rounds past the point early stopping chose -- a different model from the one each
   *_report.md characterises. That is what this script did until 2026-09-16, and it was
   visible only as a cross-check failure: the shipped columns gave top-10% precision 0.572
   @>=20 / 0.232 @>=50 where xgb_fromPRhigh_clf_report.md says 0.551 / 0.239. Passing
   iteration_range reproduces the report EXACTLY. Use --all-trees to get the old behaviour
   back (only useful for documenting the difference).

--model-dir picks which generation's models to score (model-1, model-2, ...
see MODEL_SPECS) and its matching default --input (see DEFAULT_INPUT) --
3 files now match 08_*_PCA.tsv so there is no safe "newest" guess.

Output: <input_stem>_val_scores.tsv (gitignored), val fold only (1380 rows).
Default columns: Symbol, ArrivalDate, ArrivalTime, DayOfWeek,
fromPR_High(%)_actual, pred_reg_se, pred_reg_q80, proba_clf_ge20/35/50.
--all-columns keeps every input column instead of the slim 4-column subset.

    /home/tom/venv/bin/python score_val_predictions.py
    /home/tom/venv/bin/python score_val_predictions.py --model-dir model-2 --all-columns \\
        --out model-2/scores.tsv
"""

import argparse
import sys
from argparse import Namespace
from pathlib import Path

import numpy as np
import pandas as pd
import xgboost as xgb

from train_xgb_fromPRhigh import build_xy, _newest, INPUT_GLOB, _inverse_target

HERE = Path(__file__).resolve().parent

# column name -> (ubj, prep json, "regressor"|"classifier"), per model-dir generation.
# Column names stay semantically stable across generations even though the underlying
# .ubj stems differ (model-1's 49-feature "cons49" vs model-2's 25-feature "cons").
MODEL_SPECS = {
    "model-1": [
        ("pred_reg_se", "xgb_cons49_se.ubj", "xgb_cons49_se_prep.json", "regressor"),
        ("pred_reg_q80", "xgb_cons49_q80.ubj", "xgb_cons49_q80_prep.json", "regressor"),
        ("proba_clf_ge20", "xgb_fromPRhigh_clf_ge20.ubj", "xgb_fromPRhigh_clf_ge20_prep.json", "classifier"),
        ("proba_clf_ge35", "xgb_fromPRhigh_clf_ge35.ubj", "xgb_fromPRhigh_clf_ge35_prep.json", "classifier"),
        ("proba_clf_ge50", "xgb_fromPRhigh_clf_ge50.ubj", "xgb_fromPRhigh_clf_ge50_prep.json", "classifier"),
    ],
    "model-2": [
        ("pred_reg_se", "xgb_cons_se.ubj", "xgb_cons_se_prep.json", "regressor"),
        ("pred_reg_q80", "xgb_cons_q80.ubj", "xgb_cons_q80_prep.json", "regressor"),
        ("proba_clf_ge20", "xgb_fromPRhigh_clf_ge20.ubj", "xgb_fromPRhigh_clf_ge20_prep.json", "classifier"),
        ("proba_clf_ge35", "xgb_fromPRhigh_clf_ge35.ubj", "xgb_fromPRhigh_clf_ge35_prep.json", "classifier"),
        ("proba_clf_ge50", "xgb_fromPRhigh_clf_ge50.ubj", "xgb_fromPRhigh_clf_ge50_prep.json", "classifier"),
    ],
}

# default input file per model-dir -- explicit, since 3 files now match INPUT_GLOB
# (08_*_PCA.tsv) and "newest by mtime" is no longer a safe guess.
DEFAULT_INPUT = {
    "model-1": "08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_PCA.tsv",
    "model-2": "08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_prosusReScore_reCat_PCA.tsv",
}


def _iteration_range(booster, all_trees):
    """The tree slice to predict with, and a human note for the log.

    A booster that early-stopped stores best_iteration as an attribute but keeps every tree
    it built, and predict() defaults to using all of them. Scoring past the stopping point
    is not 'a bit more model' -- it is the overfit tail early stopping exists to discard."""
    n = booster.num_boosted_rounds()
    bi = booster.attributes().get("best_iteration")
    if all_trees or bi is None:
        return None, f"rounds {n}, best_iteration {bi}, USING ALL {n}"
    bi = int(bi)
    return (0, bi + 1), f"rounds {n}, best_iteration {bi}, using {bi + 1}"


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--out", default=None,
                    help="output TSV (default: <input_stem>_val_scores.tsv next to this "
                         "script)")
    ap.add_argument("--all-trees", action="store_true",
                    help="predict with every boosted round instead of stopping at "
                         "best_iteration -- reproduces the pre-2026-09-16 behaviour. "
                         "Only useful for quantifying the difference; not correct.")
    ap.add_argument("--model-dir", default="model-1",
                    help="subdirectory (under this script) holding the .ubj/_prep.json "
                         "files, e.g. model-1 or model-2 (default: model-1)")
    ap.add_argument("--input", type=Path, default=None,
                    help="input TSV; default is the known-good file for --model-dir "
                         "(see DEFAULT_INPUT) -- 3 files now match 08_*_PCA.tsv, so "
                         "there is no safe 'newest' guess")
    ap.add_argument("--all-columns", action="store_true",
                    help="keep every column from the input TSV instead of just "
                         "Symbol/ArrivalDate/ArrivalTime/DayOfWeek")
    cli = ap.parse_args()

    if cli.model_dir not in MODEL_SPECS:
        sys.exit(f"--model-dir {cli.model_dir!r} not in MODEL_SPECS ({list(MODEL_SPECS)})")
    model_dir = HERE / cli.model_dir
    models = MODEL_SPECS[cli.model_dir]
    input_path = cli.input or (HERE / DEFAULT_INPUT[cli.model_dir])
    if not input_path.exists():
        sys.exit(f"input TSV not found: {input_path}")

    args = Namespace(
        input=input_path, date_col="ArrivalDate", cutoff="2026-07-28",
        label_col="fromPR_High(%)", winsor_lo=0.005, winsor_hi=0.995,
        skew_thresh=4.0, target_transform="asinh", target_winsor=0.995,
    )

    inp, X, _yt, y_raw, train_mask, val_mask, prep, n_tr, n_va = build_xy(args)
    print(f"input: {inp.name}  model-dir: {cli.model_dir}  train={n_tr}  val={n_va}")

    raw = pd.read_csv(inp, sep="\t", dtype=str, keep_default_na=False, na_values=[""])
    if cli.all_columns:
        val = raw.loc[val_mask].reset_index(drop=True)
    else:
        val = raw.loc[val_mask, ["Symbol", "ArrivalDate", "ArrivalTime", "DayOfWeek"]].reset_index(drop=True)
    val["fromPR_High(%)_actual"] = y_raw[val_mask].to_numpy(dtype="float64")

    Xva_full = X.loc[val_mask]
    for col, ubj, prep_name, mtype in models:
        with open(model_dir / prep_name) as fh:
            import json
            model_prep = json.load(fh)
        feats = model_prep["features_used"]
        booster = xgb.Booster()
        booster.load_model(str(model_dir / ubj))
        dm = xgb.DMatrix(Xva_full[feats], enable_categorical=True)
        itr, note = _iteration_range(booster, cli.all_trees)
        raw_pred = (booster.predict(dm, iteration_range=itr) if itr
                    else booster.predict(dm))
        if mtype == "regressor":
            val[col] = _inverse_target(raw_pred, model_prep)
        else:
            val[col] = raw_pred
        print(f"  scored {col}  ({ubj})  {note}")

    out = Path(cli.out) if cli.out else HERE / (inp.stem + "_val_scores.tsv")
    val.to_csv(out, sep="\t", index=False, float_format="%.6f")
    print(f"wrote {out}  ({len(val)} rows, {len(val.columns)} cols)"
          + ("  [ALL TREES -- not the early-stopped model]" if cli.all_trees else ""))


if __name__ == "__main__":
    main()
