#!/usr/bin/env python3
"""
trade_size_imputer.py
---------------------
Predict (impute) the missing RTH_tradeSize / ETH_TradeSize rows in the
trade-size-enabled pipeline output
(`data/nasdaq_symbols_data_priced_sized_YYYY-MM-DD.tsv`). The predicted values
are written straight into the canonical RTH_tradeSize / ETH_TradeSize columns;
two NEW provenance sidecars (`TradeSize_impute_flag`,
`TradeSize_impute_method`) record which rows were predicted and how.

Relationship to ITI_imputer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
This module is the trade-size analogue of `ITI_imputer.py`. It deliberately
does NOT touch `ITI_imputer.py` — that module is imported by the production
cron `pipeline_daily.py` and enforces a strict 8-column schema, so it must
stay frozen. Instead, this module *imports* ITI_imputer's reusable, schema-
agnostic helpers (`_build_feature_frame`, `_fit_predict_hgb`,
`_clip_to_p1_p99`, `_bin_counts`, `_is_feature_degenerate`) so the two
imputers share the same feature engineering and HGB engine and cannot drift
apart. Only the target columns, the log transform, and the sidecars differ.

Differences from ITI_imputer.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
- Targets are RTH_tradeSize / ETH_TradeSize (shares/trade) instead of the ITI
  columns.
- Target transform is log1p / expm1 (not bare log/exp): trade size can be
  fractional or near-zero, where log(0) explodes. Predictions are floored at 0.
- Features are the BASE feature frame only (float / mcap / price / exchange /
  source). The imputed ITI columns are intentionally NOT used as predictors,
  to avoid model-on-model-feature coupling.
- The Stage B cascade feature is log1p(RTH_tradeSize) (actual where present,
  Stage-A prediction for both-failed rows), mirroring ITI's log_rth cascade.
- Sidecars are NEW columns `TradeSize_impute_flag` / `TradeSize_impute_method`
  (the ITI sidecars are left untouched — size and ITI fetches fail
  independently even though they ride on one TRADES request).
- Input is the 12-column `_sized` file (or an already-imputed 14-column file on
  a re-run); only the two size columns + the base feature columns are required.
  A clean re-run is a no-op (no 44444 left to impute).

Sentinel handling
~~~~~~~~~~~~~~~~~
Identical policy to ITI_imputer: the pipeline writes 44444 in the size columns
when the IBKR TRADES fetch fails. This script converts those to NaN at load,
remembers which rows were sentinel, predicts only those, and leaves the
`--max-float`-skipped (never-fetched, blank) rows blank.

Output
~~~~~~
14-column tab-separated file written atomically in place (temp + os.replace).
Columns 1–12 are byte-preserved from the input (the file is re-read as strings);
only the imputed size cells change, and the 2 sidecars are appended.

Usage
~~~~~
    python3 trade_size_imputer.py --input PATH
                                  [--seed N] [--cv-folds N]
                                  [--min-train-rows N] [--quiet]
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# Reuse ITI_imputer's schema-agnostic engine. ITI_imputer.py is NOT modified.
from ITI_imputer import (
    _build_feature_frame,
    _fit_predict_hgb,
    _clip_to_p1_p99,
    _bin_counts,
    _is_feature_degenerate,
    _PIPELINE_SENTINEL,
    DEGENERATE_BIN_THRESHOLD,
)

_SCRIPT_DIR = Path(__file__).parent
_DATA_DIR   = _SCRIPT_DIR / "data"
_RUNS_DIR   = _SCRIPT_DIR / "runs"

# Target columns this imputer predicts.
RTH_COL = "RTH_tradeSize"
ETH_COL = "ETH_TradeSize"
SIZE_COLS = [RTH_COL, ETH_COL]

# Columns the model needs present in the input file (the base feature frame +
# the two targets). We do NOT require the full 12/14-column schema so a 12-col
# or already-imputed 14-col file both load.
REQUIRED_COLS = [
    "Symbol", "Exchange", "Float_M", "MarketCap_M", "Float_Source",
    "LastDailyClosePrice", RTH_COL, ETH_COL,
]

SIDECAR_COLS = ["TradeSize_impute_flag", "TradeSize_impute_method"]

GLOBAL_SEED_DEFAULT    = 42
CV_FOLDS_DEFAULT       = 5
MIN_TRAIN_ROWS_DEFAULT = 200

log = logging.getLogger("trade_size_imputer")


# ─── logging ───────────────────────────────────────────────────────────────

def _setup_logging(quiet: bool = False) -> Path:
    """Per-day runs/{DD-MMM-YYYY}/trade_size_imputer.log; mirror ITI_imputer."""
    date_str = datetime.now().strftime("%d-%b-%Y")
    log_dir  = _RUNS_DIR / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "trade_size_imputer.log"

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

def _sha256_short(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _load_input(path: Path) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Load the _sized TSV; convert pipeline sentinel (44444) to NaN.

    Accepts the 12-column file or an already-imputed 14-column file — we only
    require the base feature columns and the two size columns to be present.

    Returns (df, was_rth_sentinel, was_eth_sentinel). The was-sentinel masks
    are the only way the rest of the script distinguishes "attempted but
    failed" (predict target) from "intentionally not fetched" (leave blank);
    both look like NaN otherwise.
    """
    df = pd.read_csv(path, sep="\t")
    missing = [c for c in REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"Input is missing required columns {missing}.\n"
            f"  required: {REQUIRED_COLS}\n"
            f"  got     : {list(df.columns)}")

    for col in ("Float_M", "MarketCap_M", "LastDailyClosePrice", RTH_COL, ETH_COL):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # 44444.0000 and 44444 both parse to exactly 44444.0, so .eq is robust.
    was_rth_sentinel = df[RTH_COL].eq(_PIPELINE_SENTINEL)
    was_eth_sentinel = df[ETH_COL].eq(_PIPELINE_SENTINEL)
    df.loc[was_rth_sentinel, RTH_COL] = np.nan
    df.loc[was_eth_sentinel, ETH_COL] = np.nan
    return df, was_rth_sentinel, was_eth_sentinel


# ─── row classification ───────────────────────────────────────────────────

def _classify_rows(
    df: pd.DataFrame,
    was_rth_sentinel: pd.Series,
    was_eth_sentinel: pd.Series,
) -> dict[str, pd.Series]:
    """Boolean masks for the 5 cohorts (same partition logic as ITI_imputer)."""
    rth_valid = df[RTH_COL].notna()
    eth_valid = df[ETH_COL].notna()

    masks = {
        "complete":    rth_valid & eth_valid,
        "rth_target":  was_rth_sentinel & eth_valid,
        "eth_target":  rth_valid & was_eth_sentinel,
        "both_target": was_rth_sentinel & was_eth_sentinel,
        # nan_skipped = NaN somewhere but not 44444 — the --max-float skips.
        "nan_skipped": (~rth_valid & ~was_rth_sentinel)
                     | (~eth_valid & ~was_eth_sentinel),
    }
    total = sum(int(m.sum()) for m in masks.values())
    if total != len(df):
        raise AssertionError(f"Cohort masks do not partition df ({total} != {len(df)})")
    return masks


def _combine_methods(r: str, e: str) -> str:
    if r and e:
        return r if r == e else f"{r}+{e}"
    return r or e


# ─── one-stage fit/predict on log1p(size) ──────────────────────────────────

def _impute_stage(
    df: pd.DataFrame,
    X_train: pd.DataFrame,
    X_predict: pd.DataFrame,
    train_mask: pd.Series,
    pred_idx: pd.Index,
    target_col: str,
    bin_counts: pd.Series,
    seed: int,
    cv_folds: int,
    min_train_rows: int,
    stage_label: str,
) -> tuple[pd.Series, pd.Series, dict | None]:
    """Train HGB on log1p(target), predict the pred_idx rows.

    Returns (values, methods, report). `values`/`methods` are indexed by
    pred_idx (empty if nothing to predict or the stage was gated). `report` is
    None when the stage was skipped.
    """
    n_train = int(train_mask.sum())
    if n_train < min_train_rows:
        log.warning(f"{stage_label}: only {n_train} training rows "
                    f"(< --min-train-rows={min_train_rows}); skipping this "
                    f"stage — its target rows keep the 44444 sentinel.")
        empty_v = pd.Series(dtype=np.float64)
        empty_m = pd.Series(dtype=object)
        return empty_v, empty_m, None
    if len(pred_idx) == 0:
        log.info(f"{stage_label}: nothing to predict (no sentinel rows).")
        empty_v = pd.Series(dtype=np.float64)
        empty_m = pd.Series(dtype=object)
        return empty_v, empty_m, {"n_train": n_train, "n_predict": 0}

    t0 = time.time()
    y_train_linear = df.loc[train_mask, target_col].to_numpy(dtype=np.float64)
    y_train_log    = np.log1p(y_train_linear)

    log.info(f"{stage_label}: training on {n_train} rows, "
             f"predicting {len(pred_idx)} sentinel rows.")
    res = _fit_predict_hgb(
        X_train     = X_train,
        y_train     = y_train_log,
        X_predict   = X_predict,
        seed        = seed,
        cv_folds    = cv_folds,
        stage_label = stage_label,
    )
    pred_linear = np.expm1(res["pred_log"])
    pred_linear = np.maximum(pred_linear, 0.0)            # shares/trade >= 0
    pred_clipped, p1, p99 = _clip_to_p1_p99(pred_linear, y_train_linear)

    global_median = float(np.median(y_train_linear))
    degenerate = _is_feature_degenerate(df, pred_idx, bin_counts)
    method = pd.Series("hgb", index=pred_idx, dtype=object)
    method.loc[degenerate] = "global_median"
    values = pd.Series(pred_clipped, index=pred_idx, dtype=np.float64)
    values.loc[degenerate] = global_median

    secs = time.time() - t0
    log.info(f"{stage_label} done in {secs:.1f}s. "
             f"cv_log_mae={res['cv_mae_log']:.4f} "
             f"within2x={res['within2x_pct']:.1f}% "
             f"clip=[{p1:.2f},{p99:.2f}] "
             f"degenerate_rows={int(degenerate.sum())}")

    report = {
        "n_train":         n_train,
        "n_predict":       len(pred_idx),
        "cv_mae_log":      res["cv_mae_log"],
        "cv_mae_std":      res["cv_mae_std"],
        "within2x_pct":    res["within2x_pct"],
        "clip_p1":         p1,
        "clip_p99":        p99,
        "global_median":   global_median,
        "degenerate_rows": int(degenerate.sum()),
        "wall_secs":       secs,
    }
    return values, method, report


# ─── orchestration ────────────────────────────────────────────────────────

def _impute(
    df: pd.DataFrame,
    was_rth_sentinel: pd.Series,
    was_eth_sentinel: pd.Series,
    seed: int,
    cv_folds: int,
    min_train_rows: int,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series, dict]:
    """Run both stages.

    Returns (rth_values, eth_values, flag_col, method_col, report) where
    rth_values/eth_values are indexed by the rows actually imputed (may be
    empty), and flag_col/method_col are full-length Series.
    """
    masks = _classify_rows(df, was_rth_sentinel, was_eth_sentinel)
    X, exch_cats, src_cats = _build_feature_frame(df)

    rth_train_mask = df[RTH_COL].notna()
    eth_train_mask = df[ETH_COL].notna()
    rth_pred_mask  = masks["rth_target"] | masks["both_target"]
    eth_pred_mask  = masks["eth_target"] | masks["both_target"]

    log.info(f"Cohort sizes: complete={int(masks['complete'].sum())} "
             f"rth_target={int(masks['rth_target'].sum())} "
             f"eth_target={int(masks['eth_target'].sum())} "
             f"both_target={int(masks['both_target'].sum())} "
             f"nan_skipped={int(masks['nan_skipped'].sum())}")

    bin_counts = _bin_counts(df, rth_train_mask | eth_train_mask)

    # ── Stage A — RTH trade size (base features only) ──
    rth_pred_idx = X.loc[rth_pred_mask].index
    rth_values, rth_method, rep_a = _impute_stage(
        df, X.loc[rth_train_mask], X.loc[rth_pred_mask],
        rth_train_mask, rth_pred_idx, RTH_COL, bin_counts,
        seed, cv_folds, min_train_rows, "Stage A — RTH size",
    )

    # ── Stage B — ETH trade size (base features + log1p(RTH size) cascade) ──
    # log1p(RTH size): actual where present; Stage-A prediction for the rows
    # where RTH size was sentinel and we predicted it. Rows still NaN (Stage A
    # gated, or no prediction) pass through to HGB's native NaN handling.
    log_rth_size_full = np.log1p(df[RTH_COL].to_numpy(dtype=np.float64))
    log_rth_size_series = pd.Series(log_rth_size_full, index=df.index, dtype=np.float64)
    if len(rth_values):
        log_rth_size_series.loc[rth_values.index] = np.log1p(rth_values.values)

    XB = X.copy()
    XB["log_rth_size"] = log_rth_size_series

    # Don't train Stage B on a model-derived cascade feature: scrub log_rth_size
    # to NaN for the rth_target rows (real ETH, sentinel RTH) in the train view.
    train_with_predicted_rth = eth_train_mask & masks["rth_target"]
    XB_train = XB.loc[eth_train_mask].copy()
    XB_train.loc[train_with_predicted_rth.loc[eth_train_mask].to_numpy(),
                 "log_rth_size"] = np.nan

    eth_pred_idx = XB.loc[eth_pred_mask].index
    eth_values, eth_method, rep_b = _impute_stage(
        df, XB_train, XB.loc[eth_pred_mask],
        eth_train_mask, eth_pred_idx, ETH_COL, bin_counts,
        seed, cv_folds, min_train_rows, "Stage B — ETH size",
    )

    # ── Assemble provenance ──
    # flag is cohort-based (matches ITI_imputer's vocabulary); method records
    # what was actually done. A gated target row therefore reads flag=rth/eth/
    # both with method="" — "was a failed fetch, not imputed" — and keeps 44444.
    flag_col = pd.Series("ok", index=df.index, dtype=object)
    flag_col.loc[masks["rth_target"]]  = "rth"
    flag_col.loc[masks["eth_target"]]  = "eth"
    flag_col.loc[masks["both_target"]] = "both"
    flag_col.loc[masks["nan_skipped"]] = "skipped_nan"

    rth_method_col = pd.Series("", index=df.index, dtype=object)
    eth_method_col = pd.Series("", index=df.index, dtype=object)
    if len(rth_method):
        rth_method_col.loc[rth_method.index] = rth_method.values
    if len(eth_method):
        eth_method_col.loc[eth_method.index] = eth_method.values
    method_col = pd.Series(
        [_combine_methods(r, e) for r, e in zip(rth_method_col, eth_method_col)],
        index=df.index, dtype=object,
    )

    report = {
        "n_rows":     len(df),
        "cohort":     {k: int(v.sum()) for k, v in masks.items()},
        "stage_a":    rep_a,
        "stage_b":    rep_b,
        "categories": {"exchange": exch_cats, "float_source": src_cats},
    }
    return rth_values, eth_values, flag_col, method_col, report


# ─── write ─────────────────────────────────────────────────────────────────

def _write_output(
    input_path: Path,
    output_path: Path,
    rth_values: pd.Series,
    eth_values: pd.Series,
    flag_col: pd.Series,
    method_col: pd.Series,
) -> Path:
    """Atomically write the 14-column file.

    The input file is re-read as strings (keep_default_na=False) so columns
    1–12 are byte-preserved; only the imputed size cells are overwritten and
    the 2 sidecars appended. Atomic temp + os.replace guarantees no half-
    written file on crash.
    """
    disk = pd.read_csv(input_path, sep="\t", dtype=str, keep_default_na=False)

    # Drop any pre-existing sidecars (idempotent re-run on a 14-col file).
    disk = disk.drop(columns=[c for c in SIDECAR_COLS if c in disk.columns])

    # Row order is identical to the numeric frame (both read from the same
    # file with a default RangeIndex), so positional .iloc alignment holds.
    for idx, val in rth_values.items():
        disk.iloc[idx, disk.columns.get_loc(RTH_COL)] = f"{float(val):.4f}"
    for idx, val in eth_values.items():
        disk.iloc[idx, disk.columns.get_loc(ETH_COL)] = f"{float(val):.4f}"

    disk[SIDECAR_COLS[0]] = flag_col.to_numpy()
    disk[SIDECAR_COLS[1]] = method_col.to_numpy()

    tmp_path = output_path.with_name(output_path.name + ".tmp")
    disk.to_csv(tmp_path, sep="\t", index=False)
    os.replace(tmp_path, output_path)
    return output_path


def _emit_quality_report(report: dict, output_path: Path) -> None:
    a, b = report["stage_a"], report["stage_b"]

    def _fmt(stage):
        if not stage:
            return "skipped"
        if stage.get("n_predict", 0) == 0:
            return f"n_train={stage['n_train']} imputed=0"
        return (f"n_train={stage['n_train']} imputed={stage['n_predict']} "
                f"cv_mae_log={stage['cv_mae_log']:.3f} "
                f"within2x={stage['within2x_pct']:.0f}%")

    log.info(f"trade_size_imputer done. RTH[{_fmt(a)}] ETH[{_fmt(b)}] "
             f"output={output_path.name}")
    log.debug(f"Quality report (structured):\n"
              f"  cohort={report['cohort']}\n"
              f"  stage_a={a}\n"
              f"  stage_b={b}\n"
              f"  categories.exchange={report['categories']['exchange']}\n"
              f"  categories.float_source={report['categories']['float_source']}")


# ─── CLI ─────────────────────────────────────────────────────────────────-

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Impute missing RTH_tradeSize/ETH_TradeSize rows in the "
                    "_sized pipeline TSV.")
    p.add_argument("--input", required=True,
                   help="Path to the _sized TSV (12-col, or already-imputed 14-col).")
    p.add_argument("--output", default=None,
                   help="Override output path; default overwrites the input in place.")
    p.add_argument("--seed", type=int, default=GLOBAL_SEED_DEFAULT)
    p.add_argument("--cv-folds", type=int, default=CV_FOLDS_DEFAULT)
    p.add_argument("--min-train-rows", type=int, default=MIN_TRAIN_ROWS_DEFAULT)
    p.add_argument("--quiet", action="store_true",
                   help="Suppress console output (file log still written).")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log_path = _setup_logging(quiet=args.quiet)

    input_path  = Path(args.input)
    output_path = Path(args.output) if args.output else input_path

    log.info(f"Input : {input_path}  (sha256[:16]={_sha256_short(input_path)})")
    log.info(f"Output: {output_path}")
    log.debug(f"Log file: {log_path}")

    df_in, was_rth_sentinel, was_eth_sentinel = _load_input(input_path)
    log.info(f"Loaded {len(df_in)} rows. "
             f"was_rth_size_sentinel={int(was_rth_sentinel.sum())} "
             f"was_eth_size_sentinel={int(was_eth_sentinel.sum())}. "
             f"(converted to NaN internally)")

    # True-idempotent re-run guard: if the file already carries the sidecars
    # and has no 44444 left to impute, there is nothing to do — return without
    # rewriting so the existing provenance (which rows were imputed) is not
    # wiped by reclassifying the already-imputed values as real data.
    had_sidecars = all(c in df_in.columns for c in SIDECAR_COLS)
    no_sentinels = int(was_rth_sentinel.sum()) == 0 and int(was_eth_sentinel.sum()) == 0
    if had_sidecars and no_sentinels:
        log.info("Already imputed (sidecars present, no 44444 sentinels left) — "
                 "no-op; leaving the file untouched.")
        return 0

    rth_values, eth_values, flag_col, method_col, report = _impute(
        df_in, was_rth_sentinel, was_eth_sentinel,
        seed=args.seed, cv_folds=args.cv_folds,
        min_train_rows=args.min_train_rows,
    )

    actual_path = _write_output(
        input_path, output_path, rth_values, eth_values, flag_col, method_col)
    _emit_quality_report(report, actual_path)

    # rc=1 if neither stage produced any prediction (both gated / nothing to do
    # with too little data); the 14-col file is still written either way.
    both_skipped = report["stage_a"] is None and report["stage_b"] is None
    return 1 if both_skipped else 0


if __name__ == "__main__":
    sys.exit(main())
