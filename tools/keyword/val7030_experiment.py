"""val7030_experiment.py

Research side-run: is kw15_FT's methodology (fit on 100% of rows, threshold >=15%,
no held-out score) actually the best config? Retrains the SAME kw15 recipe on the
SAME corpus as model_trained_on_2026-05-04-2026-09-24/ (frozen, never re-trained into)
but with an honest 70/30 train/validation split, at three thresholds: >=15/30/50%.

Reuses keyword_finder.py (the canonical trainer, never edited) via its CLI, which
already supports --test-size and --binarize-threshold and already writes real
held-out test_roc_auc. Text channels are fm3_common's text_hd / text_bd400 columns
(HTML-stripped, wire-boilerplate-stripped, body capped at 400 words) -- exported
once to a TSV and fed in with --no-strip-wire-boilerplate since they are already
prepped (matches kw15_ft_train.py's channel_rows, which applies zero extra prep).

Usage:
    /home/tom/venv/bin/python val7030_experiment.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
FM3_DIR = HERE / "model-FM3"
sys.path.insert(0, str(FM3_DIR))

OUT_DIR = HERE / "model_trained_on_2026-05-04-2026-09-24_val7030"
CORPUS_TSV = OUT_DIR / "corpus_export.tsv"
TARGET_COL = "fromPR_High(%)"
THRESHOLDS = (15, 30, 50)
CHANNELS = {"hd": "text_hd", "bd": "text_bd400"}
TEST_SIZE = 0.30
SEED = 42
VARIANTS = ["tfidf", "binary"]
PYTHON = sys.executable


def export_corpus() -> None:
    import fm3_common as C

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = C.load_corpus()
    cols = ["ID", TARGET_COL, "text_hd", "text_bd400"]
    df[cols].to_csv(CORPUS_TSV, sep="\t", index=False)
    print(f"exported corpus: {len(df)} rows -> {CORPUS_TSV}")


def run_one(threshold: int, ch: str) -> Path:
    outdir = OUT_DIR / f"ge{threshold}_{ch}"
    cmd = [
        PYTHON, str(HERE / "keyword_finder.py"),
        "--input", str(CORPUS_TSV),
        "--text-col", CHANNELS[ch],
        "--target-col", TARGET_COL,
        "--id-col", "ID",
        "--task", "classification",
        "--binarize-threshold", str(threshold),
        "--test-size", str(TEST_SIZE),
        "--no-strip-wire-boilerplate",
        "--variants", *VARIANTS,
        "--seed", str(SEED),
        "--outdir", str(outdir),
    ]
    print(f"\n=== ge{threshold} / {ch} ===")
    subprocess.run(cmd, check=True, cwd=HERE)
    return outdir


def collect_summary(runs: list[tuple[int, str, Path]]) -> pd.DataFrame:
    rows = []
    for threshold, ch, outdir in runs:
        perf = pd.read_csv(outdir / "model_performance_summary.csv")
        perf.insert(0, "channel", ch)
        perf.insert(0, "threshold", threshold)
        rows.append(perf)
    summary = pd.concat(rows, ignore_index=True)
    summary = summary.sort_values("test_roc_auc", ascending=False).reset_index(drop=True)
    return summary


def main() -> None:
    export_corpus()
    runs = []
    for threshold in THRESHOLDS:
        for ch in CHANNELS:
            outdir = run_one(threshold, ch)
            runs.append((threshold, ch, outdir))

    summary = collect_summary(runs)
    summary_path = OUT_DIR / "comparison_summary.csv"
    summary.to_csv(summary_path, index=False)

    cols = ["threshold", "channel", "variant", "n_train", "n_test", "train_pos_rate",
            "test_pos_rate", "test_roc_auc", "test_avg_precision", "test_precision",
            "test_recall", "test_f1"]
    print("\n=== ranked by test_roc_auc (held-out 30%) ===")
    print(summary[cols].to_string(index=False))
    print(f"\nwrote {summary_path}")


if __name__ == "__main__":
    main()
