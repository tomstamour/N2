#!/usr/bin/env python3
"""Backfill Prosus_*_hd (ProsusAI/FinBERT headline) onto a PRstats TSV.

ProsusAI/finbert is the one headliner that lives OUTSIDE tools/Encoder_Models/, so
HeadScore11encoders.py never scored it - yet CORE and FinBERTSolo both read
`Prosus_pos_hd`. Without these columns the O5.2 gate cannot be replayed offline.

Uses the exact module Orchestrator5.2.py imports (FinBERT/FinBERT-headliner.py ->
analyze_headline), so a replayed score is the score live would have produced.

  /home/tom/venv/bin/python score_prosus_headlines.py \
      --input  ../tools/Earning_Scorer/07_..._earnScored.tsv \
      --output outputs/07_..._earnScored_Prosus.tsv
"""
from __future__ import annotations

import argparse
import importlib.util as ilu
import sys
import time
from pathlib import Path

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
HEADLINER = SCRIPTS_DIR / "FinBERT" / "FinBERT-headliner.py"

# Hyphen in the filename blocks a normal import - same trick O5.2 uses (line 36).
_spec = ilu.spec_from_file_location("FinBERT_headliner", str(HEADLINER))
_mod = ilu.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
analyze_headline = _mod.analyze_headline
load_model = _mod.load_model

# 2026-08-09 naming convention: {Model}_{channel}_{hd|bd}.
OUT_COLS = ["Prosus_neg_hd", "Prosus_neu_hd", "Prosus_pos_hd",
            "Prosus_ss_hd", "Prosus_lab_hd", "Prosus_ms_hd"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--headline-col", default="Headline")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    if a.headline_col not in df.columns:
        sys.exit(f"no '{a.headline_col}' column in {a.input}")
    if any(c in df.columns for c in OUT_COLS):
        sys.exit(f"{a.input} already carries Prosus_* columns - refusing to overwrite")

    load_model()  # warm once; analyze_headline would do it per call anyway
    heads = df[a.headline_col].fillna("").astype(str)
    rows, t0 = [], time.perf_counter()
    for i, h in enumerate(heads, start=1):
        r = analyze_headline(h)
        rows.append((r["neg"], r["neu"], r["pos"], r["ss"], r["lab"], r["ms"]))
        if i % 500 == 0:
            rate = (time.perf_counter() - t0) / i * 1000
            print(f"  {i}/{len(heads)}  {rate:.1f} ms/row", flush=True)

    df[OUT_COLS] = pd.DataFrame(rows, index=df.index, columns=OUT_COLS)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(a.output, sep="\t", index=False)
    took = time.perf_counter() - t0
    print(f"scored {len(heads)} headlines in {took:.1f}s "
          f"({took / len(heads) * 1000:.1f} ms/row)")
    print(f"label mix: {df['Prosus_lab_hd'].value_counts().to_dict()}")
    print(f"wrote {a.output}  ({df.shape[0]} rows x {df.shape[1]} cols)")


if __name__ == "__main__":
    main()
