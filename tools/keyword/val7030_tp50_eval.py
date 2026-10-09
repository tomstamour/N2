"""val7030_tp50_eval.py

Common-yardstick follow-up to val7030_experiment.py: rank all 12 val7030 models
(ge15/ge30/ge50 x hd/bd x tfidf/binary) on their OWN honest validation fold, but
as predictors of a FIXED target -- fromPR_High(%) >= 50 (TP definition) -- so the
three thresholds are directly comparable on one scale.

Reproduces each run's held-out split by re-running keyword_finder.load_and_prepare()
+ the identical train_test_split() call (same seed/test_size/stratify) used inside
keyword_finder.py's run_variant_clf(). The split is deterministic given (n, stratify
array), so an index-only split reproduces the same held-out rows without needing the
original run to have saved them. hd and bd channels drop a different number of
duplicate rows, so the split is redone per (threshold, channel), not shared.

Writes model_trained_on_2026-05-04-2026-09-24_val7030/tp50_val_scores.tsv: ID,
fromPR_High(%), and 12 sparse columns (val_ge{T}_{ch}_{variant}), each non-null only
for that model's own held-out rows -- so analyze_frompr_predictors.py's per-column
dropna() automatically restricts each model's evaluation to its own honest fold.

Usage:
    /home/tom/venv/bin/python val7030_tp50_eval.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import keyword_finder as KF  # noqa: E402

OUT_DIR = HERE / "model_trained_on_2026-05-04-2026-09-24_val7030"
CORPUS_TSV = OUT_DIR / "corpus_export.tsv"
TARGET_COL = "fromPR_High(%)"
THRESHOLDS = (15, 30, 50)
CHANNELS = {"hd": "text_hd", "bd": "text_bd400"}
VARIANTS = ("tfidf", "binary")
TEST_SIZE = 0.30
SEED = 42


def recover_test_rows(threshold: int, ch: str) -> pd.DataFrame:
    """Reproduce load_and_prepare() + the classification split for one
    (threshold, channel) run; return the held-out rows (ID, _target)."""
    df = KF.load_and_prepare(
        CORPUS_TSV, text_col=CHANNELS[ch], target_col=TARGET_COL, id_col="ID",
        drop_duplicates=True, strip_boilerplate=False, strip_wire=False,
    )
    y = (df["_target"].to_numpy(dtype=float) >= threshold).astype(int)
    idx = np.arange(len(y))
    _, te_idx = train_test_split(
        idx, test_size=TEST_SIZE, random_state=SEED, stratify=y)
    test_df = df.iloc[te_idx][["_id", "_target", "_text"]].copy()
    test_df = test_df.rename(columns={"_id": "ID"})
    print(f"  ge{threshold}/{ch}: recovered {len(test_df)} held-out rows "
          f"(pos@50%={(test_df['_target'] >= 50).mean():.3f})")
    return test_df


def score_run(threshold: int, ch: str, test_df: pd.DataFrame) -> pd.DataFrame:
    outdir = OUT_DIR / f"ge{threshold}_{ch}"
    out = test_df[["ID"]].copy()
    for variant in VARIANTS:
        pipe = joblib.load(outdir / f"{variant}_fitted_pipeline.joblib")
        proba = pipe.predict_proba(test_df["_text"].to_numpy())[:, 1]
        out[f"val_ge{threshold}_{ch}_{variant}"] = proba
    return out


def main() -> None:
    base = None
    score_frames = []
    for threshold in THRESHOLDS:
        for ch in CHANNELS:
            test_df = recover_test_rows(threshold, ch)
            score_frames.append(score_run(threshold, ch, test_df))
            if base is None:
                full = KF.load_and_prepare(
                    CORPUS_TSV, text_col=CHANNELS[ch], target_col=TARGET_COL,
                    id_col="ID", drop_duplicates=False, strip_boilerplate=False,
                    strip_wire=False,
                )
                base = full[["_id", "_target"]].rename(
                    columns={"_id": "ID", "_target": TARGET_COL}
                ).drop_duplicates(subset=["ID"])

    wide = base
    for sf in score_frames:
        wide = wide.merge(sf, on="ID", how="left")

    wide.to_csv(OUT_DIR / "tp50_val_scores.tsv", sep="\t", index=False)
    n_cols = [c for c in wide.columns if c.startswith("val_")]
    print(f"\nwrote {OUT_DIR / 'tp50_val_scores.tsv'} "
          f"({len(wide)} rows, {len(n_cols)} model columns)")
    for c in n_cols:
        print(f"  {c}: n={wide[c].notna().sum()}")


if __name__ == "__main__":
    main()
