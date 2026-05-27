#!/usr/bin/env python3
"""
ITI_imputer.py
--------------
Predict (impute) the missing RTH_avgITI_sec / ETH_avgITI_sec rows in
`data/nasdaq_symbols_data_priced_YYYY-MM-DD.tsv`. The predicted values are
written straight into the canonical RTH_avgITI_sec / ETH_avgITI_sec columns
(rounded to 1 decimal); two provenance sidecars (`ITI_impute_flag`,
`ITI_impute_method`) record which rows were predicted and how.

Sentinel handling
~~~~~~~~~~~~~~~~~
The pipeline writes the literal value `44444` in the two ITI columns when
IBKR fails to return data. This script reads those values and immediately
converts them to NaN. From that point on, the literal `44444` does not
appear anywhere in the script — NaN is the universal missing indicator,
just like for the `--max-float` skip cohort. The recorded `was_*_sentinel`
boolean Series is the only mechanism that distinguishes "attempted but
failed" (predict target) from "intentionally not fetched" (leave alone).

In the output, the predicted (was-44444) rows now carry the model's value
in the canonical RTH/ETH columns. Only the `nan_skipped` cohort (rows never
fetched, e.g. the `--max-float` skips) stays NaN/empty — the orchestrator's
downstream `if pd.isna(val): fallback` path applies to those alone.

Method
~~~~~~
Two-stage HistGradientBoostingRegressor on log-ITI:

  Stage A — RTH model.
    Train on rows where RTH is non-NaN. Features: log1p(Float_M),
    log1p(MarketCap_M), log(LastDailyClosePrice+0.01), one-hot Exchange,
    one-hot Float_Source. Predict log-RTH for rows where input was 44444.

  Stage B — ETH model.
    Same as Stage A plus a `log_rth` feature. At PREDICT time, `log_rth`
    is the actual log(RTH) for `eth_target` rows and the Stage-A
    prediction for `both_target` rows. At TRAIN time, the 2 rows in
    `rth_target` (sentinel RTH, real ETH) get `log_rth = NaN` so the
    model is never trained on a model-derived feature.

Predictions are exponentiated and clipped to the [P1, P99] of the
training target. When a sentinel row has all numeric predictors NaN AND
its (Exchange, Float_Source) bin has < 30 training rows, the global
median ITI is emitted instead and tagged `ITI_impute_method='global_median'`.

Output
~~~~~~
`data/nasdaq_symbols_data_priced_YYYY-MM-DD.tsv` — 10 columns, tab-
separated. The two ITI columns are written at 1 decimal; the other floats
keep %.4f. **By default the imputer overwrites its input file in-place**,
upgrading the canonical 8-column pipeline output to a 10-column file that
carries the 2 provenance sidecars. Pass `--output PATH` to write elsewhere
without touching the input.

The orchestrator's glob (`nasdaq_symbols_data_priced_????-??-??.tsv`)
now matches the imputed file directly, and it reads the canonical RTH/ETH
columns — which now hold the model-predicted values for the formerly-44444
rows. Its `if pd.isna(val): TM_DEFAULT_BASELINE_ITI` fallback now fires only
for the `nan_skipped` rows that were never fetched.

Crash safety: the write is atomic (temp file + os.replace) so an
interrupted run cannot leave a half-written canonical file. A standalone
re-run on an already-imputed file fails the strict column-schema check
in `_load_input` — re-imputation requires re-running `pipeline_daily.py`
to regenerate the 8-column input first.

Usage
~~~~~
    python3 ITI_imputer.py [--date YYYY-MM-DD] [--input PATH] [--output PATH]
                           [--seed N] [--cv-folds N] [--min-train-rows N]
                           [--quiet]

Defaults: --date = most recent canonical date found in data/; --seed = 42;
--cv-folds = 5; --min-train-rows = 200.
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.model_selection import KFold


_SCRIPT_DIR = Path(__file__).parent
_DATA_DIR   = _SCRIPT_DIR / "data"
_RUNS_DIR   = _SCRIPT_DIR / "runs"

# The pipeline's failure sentinel. Used in exactly one place: load-time
# conversion to NaN. Do not propagate this value elsewhere in the script.
_PIPELINE_SENTINEL = 44444.0

ORIG_COLS = [
    "Symbol", "Exchange", "Float_M", "MarketCap_M", "Float_Source",
    "LastDailyClosePrice", "RTH_avgITI_sec", "ETH_avgITI_sec",
]
SIDECAR_COLS = [
    "ITI_impute_flag", "ITI_impute_method",
]

GLOBAL_SEED_DEFAULT       = 42
CV_FOLDS_DEFAULT          = 5
MIN_TRAIN_ROWS_DEFAULT    = 200
DEGENERATE_BIN_THRESHOLD  = 30   # rows/bin below which we don't trust HGB on a feature-empty row

# Canonical pipeline-output filename pattern, no suffix. Used by
# `_find_latest_input` so we don't accidentally select `_imputed`, `_limit`,
# `_superseded`, or `_HHMM` sidecar files.
_CANONICAL_NAME_RE = re.compile(r"^nasdaq_symbols_data_priced_(\d{4}-\d{2}-\d{2})\.tsv$")

log = logging.getLogger("ITI_imputer")


# ─── logging ───────────────────────────────────────────────────────────────

def _setup_logging(quiet: bool = False) -> Path:
    """Per-day runs/{DD-MMM-YYYY}/ITI_imputer.log; mirror pipeline_daily.py:98."""
    date_str = datetime.now().strftime("%d-%b-%Y")
    log_dir  = _RUNS_DIR / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "ITI_imputer.log"

    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        log.addHandler(fh)

        if not quiet:
            ch = logging.StreamHandler()
            ch.setLevel(logging.INFO)
            ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
            log.addHandler(ch)

    return log_path


# ─── I/O ──────────────────────────────────────────────────────────────────

def _find_latest_input(date_str: str | None) -> Path:
    """Resolve --date to a canonical pipeline TSV (no suffix)."""
    if date_str is not None:
        path = _DATA_DIR / f"nasdaq_symbols_data_priced_{date_str}.tsv"
        if not path.exists():
            raise FileNotFoundError(f"No canonical file for date {date_str}: {path}")
        return path

    candidates = sorted(
        p for p in _DATA_DIR.glob("nasdaq_symbols_data_priced_*.tsv")
        if _CANONICAL_NAME_RE.match(p.name)
    )
    if not candidates:
        raise FileNotFoundError(
            f"No canonical nasdaq_symbols_data_priced_YYYY-MM-DD.tsv found in {_DATA_DIR}")
    return candidates[-1]


def _sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _load_input(path: Path) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Load the canonical TSV; convert pipeline sentinel (44444) to NaN.

    Returns (df, was_rth_sentinel, was_eth_sentinel). The was-sentinel masks
    are the only way the rest of the script distinguishes "attempted but
    failed" from "intentionally not fetched"; both look like NaN otherwise.
    """
    df = pd.read_csv(path, sep="\t")
    if list(df.columns) != ORIG_COLS:
        raise ValueError(
            f"Input columns do not match expected schema.\n"
            f"  expected: {ORIG_COLS}\n"
            f"  got     : {list(df.columns)}")
    for col in ("Float_M", "MarketCap_M", "LastDailyClosePrice",
                "RTH_avgITI_sec", "ETH_avgITI_sec"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    was_rth_sentinel = df["RTH_avgITI_sec"].eq(_PIPELINE_SENTINEL)
    was_eth_sentinel = df["ETH_avgITI_sec"].eq(_PIPELINE_SENTINEL)
    df.loc[was_rth_sentinel, "RTH_avgITI_sec"] = np.nan
    df.loc[was_eth_sentinel, "ETH_avgITI_sec"] = np.nan
    return df, was_rth_sentinel, was_eth_sentinel


def _resolve_output_path(input_path: Path, override: str | None) -> Path:
    """Default behavior: overwrite the input file in-place with the 10-col
    imputed version. The orchestrator's glob then naturally matches the
    resulting file (no suffix to exclude).

    Pass `--output PATH` to redirect elsewhere without touching the input.
    """
    if override:
        return Path(override)
    return input_path


# ─── row classification ───────────────────────────────────────────────────

def _classify_rows(
    df: pd.DataFrame,
    was_rth_sentinel: pd.Series,
    was_eth_sentinel: pd.Series,
) -> dict[str, pd.Series]:
    """Return boolean masks for the 5 cohorts. After load-time conversion,
    every "missing" ITI value is NaN; the was-sentinel masks split that NaN
    into "predict target" vs "leave alone".
    """
    rth_valid = df["RTH_avgITI_sec"].notna()
    eth_valid = df["ETH_avgITI_sec"].notna()

    masks = {
        "complete":     rth_valid & eth_valid,
        "rth_target":   was_rth_sentinel & eth_valid,
        "eth_target":   rth_valid & was_eth_sentinel,
        "both_target":  was_rth_sentinel & was_eth_sentinel,
        # nan_skipped = everything else: rows that have NaN somewhere but
        # were not 44444 — i.e. the --max-float skip cohort in pipeline_daily.py.
        "nan_skipped":  (~rth_valid & ~was_rth_sentinel)
                      | (~eth_valid & ~was_eth_sentinel),
    }
    # Sanity: cohorts partition the dataframe.
    total = sum(int(m.sum()) for m in masks.values())
    if total != len(df):
        raise AssertionError(f"Cohort masks do not partition df ({total} != {len(df)})")
    return masks


# ─── feature engineering ──────────────────────────────────────────────────

def _build_feature_frame(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Return (X, exch_cats, src_cats).

    X has float dtype throughout (NaN passthrough for the numerics, 0/1 for the
    one-hots). Stage B will append a `log_rth` column to this; we do NOT include
    it here so the same X can drive Stage A.
    """
    # Numerics, log-transformed. NaN passes through cleanly; HGB handles it.
    log_float = np.log1p(df["Float_M"])
    log_mcap  = np.log1p(df["MarketCap_M"])
    # Price can be near-zero; +0.01 prevents -inf for $0.00 listings.
    log_price = np.log(df["LastDailyClosePrice"] + 0.01)

    # Categoricals — pull the universe of values from the FULL df so unseen-
    # at-train categories at predict time still encode correctly.
    exch_cats = sorted(df["Exchange"].dropna().unique().tolist())
    src_raw   = df["Float_Source"].fillna("missing").astype(str)
    src_cats  = sorted(src_raw.unique().tolist())

    pieces = {
        "log_float": log_float,
        "log_mcap":  log_mcap,
        "log_price": log_price,
    }
    for cat in exch_cats:
        pieces[f"exch_{cat}"] = (df["Exchange"] == cat).astype(np.float64)
    for cat in src_cats:
        pieces[f"src_{cat}"]  = (src_raw == cat).astype(np.float64)

    X = pd.DataFrame(pieces, index=df.index)
    return X, exch_cats, src_cats


# ─── HGB engine ───────────────────────────────────────────────────────────

def _fit_predict_hgb(
    X_train: pd.DataFrame,
    y_train: np.ndarray,
    X_predict: pd.DataFrame,
    seed: int,
    cv_folds: int,
    stage_label: str,
) -> dict:
    """Train HGB on log-target, return predictions on X_predict + CV stats."""
    # Defensive — by construction y_train cannot contain log(44444), but a
    # future refactor could re-introduce leakage. Fail loudly if it does.
    assert not np.isclose(y_train, np.log(_PIPELINE_SENTINEL)).any(), (
        f"{stage_label} training target appears to contain log({int(_PIPELINE_SENTINEL)}) — "
        "sentinel leakage. Aborting.")

    kf = KFold(n_splits=cv_folds, shuffle=True, random_state=seed)
    fold_maes, within2x_hits, within2x_n = [], 0, 0
    for fold_idx, (tr_idx, te_idx) in enumerate(kf.split(X_train)):
        m = HistGradientBoostingRegressor(random_state=seed)
        m.fit(X_train.iloc[tr_idx], y_train[tr_idx])
        pred = m.predict(X_train.iloc[te_idx])
        truth = y_train[te_idx]
        fold_mae = float(np.mean(np.abs(pred - truth)))
        fold_maes.append(fold_mae)
        within2x_hits += int(np.sum(np.abs(pred - truth) < np.log(2.0)))
        within2x_n    += int(len(truth))
        log.debug(f"{stage_label} CV fold {fold_idx + 1}/{cv_folds}: "
                  f"log-MAE={fold_mae:.4f}")

    cv_mae_log   = float(np.mean(fold_maes))
    cv_mae_std   = float(np.std(fold_maes))
    within2x_pct = 100.0 * within2x_hits / max(1, within2x_n)

    # Refit on full training set, predict for the imputation cohort.
    model = HistGradientBoostingRegressor(random_state=seed)
    model.fit(X_train, y_train)
    pred_log = model.predict(X_predict) if len(X_predict) else np.empty(0)

    return {
        "model":         model,
        "pred_log":      pred_log,
        "cv_mae_log":    cv_mae_log,
        "cv_mae_std":    cv_mae_std,
        "within2x_pct":  within2x_pct,
    }


# ─── post-processing ──────────────────────────────────────────────────────

def _clip_to_p1_p99(values_linear: np.ndarray, y_train_linear: np.ndarray) -> tuple[np.ndarray, float, float]:
    lo = float(np.percentile(y_train_linear, 1))
    hi = float(np.percentile(y_train_linear, 99))
    return np.clip(values_linear, lo, hi), lo, hi


def _bin_counts(
    df: pd.DataFrame,
    training_mask: pd.Series,
) -> pd.Series:
    """Per-row count of training observations sharing this row's
    (Exchange, Float_Source) bin. Used to detect 'degenerate' sentinel rows
    where neither HGB nor the bin can produce a meaningful prediction.
    """
    train_bins = (
        df.loc[training_mask, ["Exchange", "Float_Source"]]
        .fillna("missing").astype(str)
        .groupby(["Exchange", "Float_Source"])
        .size()
    )
    keys = pd.MultiIndex.from_frame(
        df[["Exchange", "Float_Source"]].fillna("missing").astype(str))
    return pd.Series(
        train_bins.reindex(keys).fillna(0).to_numpy(),
        index=df.index,
        dtype=np.int64,
    )


def _is_feature_degenerate(
    df: pd.DataFrame,
    target_idx: pd.Index,
    bin_counts: pd.Series,
) -> pd.Series:
    """A target row is degenerate iff all three numeric predictors are NaN AND
    its (Exchange, Float_Source) bin has < DEGENERATE_BIN_THRESHOLD training rows.
    Returns a bool Series indexed like target_idx.
    """
    sub = df.loc[target_idx]
    all_numerics_nan = (
        sub["Float_M"].isna()
        & sub["MarketCap_M"].isna()
        & sub["LastDailyClosePrice"].isna()
    )
    weak_bin = bin_counts.loc[target_idx] < DEGENERATE_BIN_THRESHOLD
    return all_numerics_nan & weak_bin


# ─── orchestration ────────────────────────────────────────────────────────

def _impute(
    df: pd.DataFrame,
    was_rth_sentinel: pd.Series,
    was_eth_sentinel: pd.Series,
    seed: int,
    cv_folds: int,
) -> tuple[pd.DataFrame, dict]:
    """Run both stages, return (df_with_sidecars, report_dict)."""
    masks   = _classify_rows(df, was_rth_sentinel, was_eth_sentinel)
    X, exch_cats, src_cats = _build_feature_frame(df)

    rth = df["RTH_avgITI_sec"].to_numpy()
    eth = df["ETH_avgITI_sec"].to_numpy()

    # Training cohorts: every row with a real target. After the load-time
    # conversion, "real" means "not NaN" — no separate sentinel check needed.
    rth_train_mask = df["RTH_avgITI_sec"].notna()
    eth_train_mask = df["ETH_avgITI_sec"].notna()
    rth_pred_mask  = masks["rth_target"]  | masks["both_target"]
    eth_pred_mask  = masks["eth_target"]  | masks["both_target"]

    log.info(f"Cohort sizes: complete={int(masks['complete'].sum())} "
             f"rth_target={int(masks['rth_target'].sum())} "
             f"eth_target={int(masks['eth_target'].sum())} "
             f"both_target={int(masks['both_target'].sum())} "
             f"nan_skipped={int(masks['nan_skipped'].sum())}")

    y_rth_train_linear = rth[rth_train_mask.to_numpy()]
    y_eth_train_linear = eth[eth_train_mask.to_numpy()]
    log_rth_train      = np.log(y_rth_train_linear)
    log_eth_train      = np.log(y_eth_train_linear)

    # Per-row bin counts based on the union of valid-target rows.
    bin_counts = _bin_counts(df, rth_train_mask | eth_train_mask)

    # ───── Stage A — RTH ─────
    t0 = time.time()
    log.info(f"Stage A — RTH: training on {int(rth_train_mask.sum())} rows, "
             f"predicting {int(rth_pred_mask.sum())} sentinel rows.")
    stage_a = _fit_predict_hgb(
        X_train   = X.loc[rth_train_mask],
        y_train   = log_rth_train,
        X_predict = X.loc[rth_pred_mask],
        seed      = seed,
        cv_folds  = cv_folds,
        stage_label = "Stage A",
    )
    rth_pred_linear = np.exp(stage_a["pred_log"])
    rth_pred_clipped, rth_p1, rth_p99 = _clip_to_p1_p99(rth_pred_linear, y_rth_train_linear)

    rth_global_median = float(np.median(y_rth_train_linear))
    rth_pred_idx = X.loc[rth_pred_mask].index
    rth_degenerate = _is_feature_degenerate(df, rth_pred_idx, bin_counts)
    rth_method = pd.Series("hgb", index=rth_pred_idx, dtype=object)
    rth_method.loc[rth_degenerate] = "global_median"
    rth_values = pd.Series(rth_pred_clipped, index=rth_pred_idx, dtype=np.float64)
    rth_values.loc[rth_degenerate] = rth_global_median
    stage_a_secs = time.time() - t0

    log.info(f"Stage A done in {stage_a_secs:.1f}s. "
             f"cv_log_mae={stage_a['cv_mae_log']:.4f} "
             f"within2x={stage_a['within2x_pct']:.1f}% "
             f"clip=[{rth_p1:.2f},{rth_p99:.2f}] "
             f"degenerate_rows={int(rth_degenerate.sum())}")

    # ───── Stage B — ETH ─────
    # log_rth feature: actual log(RTH) where RTH.notna(); Stage-A prediction
    # for rows in rth_pred_idx (i.e. the rows where RTH was sentinel). NaN
    # rows pass through to HGB's native missing-value handling.
    log_rth_full = np.log(df["RTH_avgITI_sec"].to_numpy())
    log_rth_full_series = pd.Series(log_rth_full, index=df.index, dtype=np.float64)
    a_imputed_log_rth = pd.Series(stage_a["pred_log"], index=rth_pred_idx, dtype=np.float64)
    log_rth_full_series.loc[rth_pred_idx] = a_imputed_log_rth.values

    XB = X.copy()
    XB["log_rth"] = log_rth_full_series

    # Belt-and-suspenders: scrub the 2 rows in `rth_target` from the Stage B
    # TRAINING-feature view. They're in eth_train_mask, and their log_rth is
    # the Stage-A prediction; we don't want the model trained on a model-
    # derived feature even for 0.04% of training data. HGB handles NaN
    # natively, so this just tells the model "no usable RTH signal here".
    train_with_predicted_rth = eth_train_mask & masks["rth_target"]
    XB_train = XB.loc[eth_train_mask].copy()
    XB_train.loc[train_with_predicted_rth.loc[eth_train_mask].to_numpy(), "log_rth"] = np.nan

    t0 = time.time()
    log.info(f"Stage B — ETH: training on {int(eth_train_mask.sum())} rows "
             f"({int(train_with_predicted_rth.sum())} with log_rth scrubbed to NaN), "
             f"predicting {int(eth_pred_mask.sum())} sentinel rows.")
    stage_b = _fit_predict_hgb(
        X_train   = XB_train,
        y_train   = log_eth_train,
        X_predict = XB.loc[eth_pred_mask],
        seed      = seed,
        cv_folds  = cv_folds,
        stage_label = "Stage B",
    )
    eth_pred_linear = np.exp(stage_b["pred_log"])
    eth_pred_clipped, eth_p1, eth_p99 = _clip_to_p1_p99(eth_pred_linear, y_eth_train_linear)

    eth_global_median = float(np.median(y_eth_train_linear))
    eth_pred_idx = XB.loc[eth_pred_mask].index
    eth_degenerate = _is_feature_degenerate(df, eth_pred_idx, bin_counts)
    eth_method = pd.Series("hgb", index=eth_pred_idx, dtype=object)
    eth_method.loc[eth_degenerate] = "global_median"
    eth_values = pd.Series(eth_pred_clipped, index=eth_pred_idx, dtype=np.float64)
    eth_values.loc[eth_degenerate] = eth_global_median
    stage_b_secs = time.time() - t0

    log.info(f"Stage B done in {stage_b_secs:.1f}s. "
             f"cv_log_mae={stage_b['cv_mae_log']:.4f} "
             f"within2x={stage_b['within2x_pct']:.1f}% "
             f"clip=[{eth_p1:.2f},{eth_p99:.2f}] "
             f"degenerate_rows={int(eth_degenerate.sum())}")

    # ───── Assemble output ─────
    # Imputed values are written straight into the canonical RTH/ETH columns
    # (the orchestrator reads those directly); the only sidecars retained are
    # the provenance flag + method.
    flag_col    = pd.Series("ok", index=df.index, dtype=object)

    flag_col.loc[masks["rth_target"]]   = "rth"
    flag_col.loc[masks["eth_target"]]   = "eth"
    flag_col.loc[masks["both_target"]]  = "both"
    flag_col.loc[masks["nan_skipped"]]  = "skipped_nan"

    rth_method_col = pd.Series("", index=df.index, dtype=object)
    eth_method_col = pd.Series("", index=df.index, dtype=object)
    rth_method_col.loc[rth_pred_idx] = rth_method.values
    eth_method_col.loc[eth_pred_idx] = eth_method.values

    def _combine_methods(r: str, e: str) -> str:
        if r and e:
            return r if r == e else f"{r}+{e}"
        return r or e

    method_col = pd.Series(
        [_combine_methods(r, e) for r, e in zip(rth_method_col, eth_method_col)],
        index=df.index, dtype=object,
    )

    df_out = df.copy()
    df_out.loc[rth_pred_idx, "RTH_avgITI_sec"] = rth_values.values
    df_out.loc[eth_pred_idx, "ETH_avgITI_sec"] = eth_values.values
    df_out["ITI_impute_flag"]        = flag_col
    df_out["ITI_impute_method"]      = method_col
    df_out = df_out[ORIG_COLS + SIDECAR_COLS]

    report = {
        "n_rows":                  len(df),
        "cohort":                  {k: int(v.sum()) for k, v in masks.items()},
        "stage_a": {
            "n_train":             int(rth_train_mask.sum()),
            "n_predict":           int(rth_pred_mask.sum()),
            "cv_mae_log":          stage_a["cv_mae_log"],
            "cv_mae_std":          stage_a["cv_mae_std"],
            "within2x_pct":        stage_a["within2x_pct"],
            "clip_p1":             rth_p1,
            "clip_p99":            rth_p99,
            "global_median":       rth_global_median,
            "degenerate_rows":     int(rth_degenerate.sum()),
            "n_iter_":             int(stage_a["model"].n_iter_),
            "wall_secs":           stage_a_secs,
        },
        "stage_b": {
            "n_train":             int(eth_train_mask.sum()),
            "n_train_scrubbed":    int(train_with_predicted_rth.sum()),
            "n_predict":           int(eth_pred_mask.sum()),
            "cv_mae_log":          stage_b["cv_mae_log"],
            "cv_mae_std":          stage_b["cv_mae_std"],
            "within2x_pct":        stage_b["within2x_pct"],
            "clip_p1":             eth_p1,
            "clip_p99":            eth_p99,
            "global_median":       eth_global_median,
            "degenerate_rows":     int(eth_degenerate.sum()),
            "n_iter_":             int(stage_b["model"].n_iter_),
            "wall_secs":           stage_b_secs,
        },
        "categories": {
            "exchange":            exch_cats,
            "float_source":        src_cats,
        },
    }
    return df_out, report


# ─── invariants & write ───────────────────────────────────────────────────

def _verify_output_invariants(
    df_out: pd.DataFrame,
    df_in_postconv: pd.DataFrame,
    was_rth_sentinel: pd.Series,
    was_eth_sentinel: pd.Series,
) -> None:
    """Check the output frame against the post-conversion input frame.

    `df_in_postconv` is the loaded TSV with 44444 already replaced by NaN. The
    imputer now writes predictions straight into the canonical RTH/ETH columns,
    so those two columns are *expected* to differ from the input; the static
    originals and the predicted/real cohorts are what we verify here.
    """
    static_cols = [c for c in ORIG_COLS if c not in ("RTH_avgITI_sec", "ETH_avgITI_sec")]

    if list(df_out.columns) != ORIG_COLS + SIDECAR_COLS:
        raise AssertionError(f"Output column order wrong: {list(df_out.columns)}")
    if len(df_out) != len(df_in_postconv):
        raise AssertionError(f"Row count changed: {len(df_in_postconv)} -> {len(df_out)}")
    if not df_out[static_cols].equals(df_in_postconv[static_cols]):
        raise AssertionError(
            "Static original columns do not match the post-conversion input frame.")

    masks = _classify_rows(df_in_postconv, was_rth_sentinel, was_eth_sentinel)
    rth_pred = masks["rth_target"] | masks["both_target"]
    eth_pred = masks["eth_target"] | masks["both_target"]

    # Every predicted sentinel row must now carry a value in the canonical column.
    if not df_out.loc[rth_pred, "RTH_avgITI_sec"].notna().all():
        raise AssertionError("Some predicted RTH rows are still NaN in RTH_avgITI_sec.")
    if not df_out.loc[eth_pred, "ETH_avgITI_sec"].notna().all():
        raise AssertionError("Some predicted ETH rows are still NaN in ETH_avgITI_sec.")

    # Real-fetched rows must be preserved (no data dropped during fill).
    rth_real = df_in_postconv["RTH_avgITI_sec"].notna()
    eth_real = df_in_postconv["ETH_avgITI_sec"].notna()
    if not df_out.loc[rth_real, "RTH_avgITI_sec"].notna().all():
        raise AssertionError("A real-fetched RTH value was lost in the output.")
    if not df_out.loc[eth_real, "ETH_avgITI_sec"].notna().all():
        raise AssertionError("A real-fetched ETH value was lost in the output.")


def _write_output(df: pd.DataFrame, output_path: Path, input_path: Path) -> Path:
    """Write the imputed TSV atomically.

    Two modes:

    1. **In-place upgrade (default)** — `output_path == input_path`. The
       existing file IS our input; the same-originals check would always
       trip on the 44444→NaN conversion we deliberately applied in
       `_load_input`, so we skip it and overwrite directly. This is the
       whole point of the call.

    2. **Out-of-place write** — `--output PATH` overrides. If the target
       exists and its first 8 columns disagree with the current
       (post-conversion) input frame, the new file is diverted to a
       timestamped sidecar to avoid clobbering a different upstream run.

    In both modes the write is atomic: pandas writes to a `.tmp` sibling
    and then `os.replace` swaps it in. A SIGKILL / power loss / Python
    crash mid-write cannot leave a half-written canonical file behind —
    important now that the default behavior overwrites a file the
    orchestrator reads.
    """
    actual_path = output_path
    in_place = output_path.resolve() == input_path.resolve()

    if not in_place and output_path.exists():
        try:
            existing = pd.read_csv(output_path, sep="\t")
            same_originals = (
                list(existing.columns)[: len(ORIG_COLS)] == ORIG_COLS
                and existing[ORIG_COLS].reset_index(drop=True).equals(
                    df[ORIG_COLS].reset_index(drop=True))
            )
        except Exception as exc:
            log.warning(f"Could not read existing {output_path.name} to compare "
                        f"({exc!r}); proceeding to overwrite.")
            same_originals = True

        if not same_originals:
            ts = datetime.now().strftime("%H%M")
            actual_path = output_path.with_name(
                output_path.stem + f"_{ts}" + output_path.suffix)
            log.warning(
                f"Existing {output_path.name} has different originals than the "
                f"current input — writing this run to {actual_path.name} instead.")

    if in_place:
        log.info(f"Overwriting input in-place with 10-col imputed version: "
                 f"{actual_path.name}")

    # Render the two ITI columns at one decimal as strings so the global
    # float_format="%.4f" (kept for Float_M / MarketCap_M / LastDailyClosePrice)
    # doesn't re-pad them. NaN → "" matches pandas' default empty-cell output.
    df = df.copy()
    for c in ("RTH_avgITI_sec", "ETH_avgITI_sec"):
        df[c] = df[c].map(lambda v: "" if pd.isna(v) else f"{v:.1f}")

    tmp_path = actual_path.with_name(actual_path.name + ".tmp")
    df.to_csv(tmp_path, sep="\t", index=False, float_format="%.4f")
    os.replace(tmp_path, actual_path)
    return actual_path


def _emit_quality_report(report: dict, output_path: Path) -> None:
    a, b = report["stage_a"], report["stage_b"]
    log.info(
        f"ITI_imputer done. "
        f"n_train_rth={a['n_train']} n_train_eth={b['n_train']} "
        f"imputed_rth={a['n_predict']} imputed_eth={b['n_predict']} "
        f"cv_mae_log_rth={a['cv_mae_log']:.3f} cv_mae_log_eth={b['cv_mae_log']:.3f} "
        f"within2x_rth={a['within2x_pct']:.0f}% within2x_eth={b['within2x_pct']:.0f}% "
        f"output={output_path.name}")
    log.debug(f"Quality report (structured):\n"
              f"  cohort={report['cohort']}\n"
              f"  stage_a={a}\n"
              f"  stage_b={b}\n"
              f"  categories.exchange={report['categories']['exchange']}\n"
              f"  categories.float_source={report['categories']['float_source']}")


# ─── CLI ─────────────────────────────────────────────────────────────────-

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Impute missing-ITI rows in nasdaq_symbols_data_priced TSVs.")
    p.add_argument("--date", default=None, help="YYYY-MM-DD; defaults to latest canonical file in data/.")
    p.add_argument("--input", default=None, help="Override input path (mutually exclusive with --date).")
    p.add_argument("--output", default=None, help="Override output path; default overwrites the input file in-place.")
    p.add_argument("--seed", type=int, default=GLOBAL_SEED_DEFAULT)
    p.add_argument("--cv-folds", type=int, default=CV_FOLDS_DEFAULT)
    p.add_argument("--min-train-rows", type=int, default=MIN_TRAIN_ROWS_DEFAULT)
    p.add_argument("--quiet", action="store_true", help="Suppress console output (file log still written).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log_path = _setup_logging(quiet=args.quiet)

    input_path = Path(args.input) if args.input else _find_latest_input(args.date)
    output_path = _resolve_output_path(input_path, args.output)

    log.info(f"Input : {input_path}  (sha256[:16]={_sha256_short(input_path)})")
    log.info(f"Output: {output_path}")
    log.debug(f"Versions: sklearn={__import__('sklearn').__version__} "
              f"pandas={pd.__version__} numpy={np.__version__}")
    log.debug(f"Log file: {log_path}")

    df_in, was_rth_sentinel, was_eth_sentinel = _load_input(input_path)
    log.info(f"Loaded {len(df_in)} rows. "
             f"was_rth_sentinel={int(was_rth_sentinel.sum())} "
             f"was_eth_sentinel={int(was_eth_sentinel.sum())}. "
             f"(converted to NaN internally)")

    # Sanity gate: enough training rows?
    masks = _classify_rows(df_in, was_rth_sentinel, was_eth_sentinel)
    n_complete = int(masks["complete"].sum())
    if n_complete < args.min_train_rows:
        log.error(f"Only {n_complete} complete rows (< --min-train-rows={args.min_train_rows}). "
                  f"Writing input copy with empty sidecars and aborting.")
        df_out = df_in.copy()
        df_out["ITI_impute_flag"]        = "skipped_nan"
        df_out["ITI_impute_method"]      = ""
        df_out = df_out[ORIG_COLS + SIDECAR_COLS]
        _write_output(df_out, output_path, input_path)
        return 1

    df_out, report = _impute(
        df_in, was_rth_sentinel, was_eth_sentinel,
        seed=args.seed, cv_folds=args.cv_folds,
    )
    _verify_output_invariants(df_out, df_in, was_rth_sentinel, was_eth_sentinel)
    actual_path = _write_output(df_out, output_path, input_path)
    _emit_quality_report(report, actual_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
