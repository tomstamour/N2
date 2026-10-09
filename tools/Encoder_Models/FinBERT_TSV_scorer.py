#!/usr/bin/env python
"""
FinBERT_TSV_scorer.py — Score both Headline and Body columns of a TSV with ProsusAI FinBERT.

Replicates how Orchestrator5.2 runs the FinBERT model. Appends columns following the
unified 2026-08-09 convention: Prosus_{pos,neg,neu,ss,lab,ms}_{hd,bd}

Usage:
    /home/tom/venv/bin/python FinBERT_TSV_scorer.py \\
        --input ../../price_stats_loader/outputs/PRstats_*.tsv

The FinBERT model (ProsusAI/finbert) is loaded from ../../FinBERT/FinBERT-headliner.py
"""

import argparse
import csv
import importlib.util
import sys
import time
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]  # tools/Encoder_Models -> scripts
FINBERT_PATH = SCRIPTS_DIR / "FinBERT" / "FinBERT-headliner.py"
PROGRESS_EVERY = 250


def load_finbert():
    """Load the FinBERT headliner module."""
    spec = importlib.util.spec_from_file_location(
        "FinBERT_headliner",
        str(FINBERT_PATH),
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["FinBERT_headliner"] = module
    spec.loader.exec_module(module)
    return module


def score_table(rows, headline_idx, body_idx):
    """Score both Headline and Body columns for every row."""
    print("[FinBERT] Loading model...", flush=True)
    finbert_mod = load_finbert()
    finbert_mod.load_model()
    analyze = finbert_mod.analyze_headline

    # Warm up
    analyze("warm up")

    n = len(rows)
    t0 = time.perf_counter()

    for i, row in enumerate(rows, 1):
        # Score headline
        headline = row[headline_idx].strip() if headline_idx < len(row) else ""
        if headline:
            result_hd = analyze(headline)
            row.extend([
                result_hd["pos"],
                result_hd["neg"],
                result_hd["neu"],
                result_hd["ss"],
                result_hd["lab"],
                result_hd["ms"],
            ])
        else:
            row.extend([""] * 6)

        # Score body
        body = row[body_idx].strip() if body_idx < len(row) else ""
        if body:
            result_bd = analyze(body)
            row.extend([
                result_bd["pos"],
                result_bd["neg"],
                result_bd["neu"],
                result_bd["ss"],
                result_bd["lab"],
                result_bd["ms"],
            ])
        else:
            row.extend([""] * 6)

        if i % PROGRESS_EVERY == 0 or i == n:
            rate = (time.perf_counter() - t0) / i * 1000
            print(f"[FinBERT] {i}/{n}  ({rate:.1f} ms/row)", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score Headline and Body columns of a TSV with ProsusAI FinBERT."
    )
    parser.add_argument("--input", required=True, help="input .tsv path")
    parser.add_argument("--headline-col", default="Headline",
                        help="header name of the headline column (default: Headline)")
    parser.add_argument("--body-col", default="Body",
                        help="header name of the body column (default: Body)")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="score only the first N rows (quick test)")
    args = parser.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.is_file():
        parser.error(f"input not found: {in_path}")
    if not in_path.name.endswith(".tsv"):
        parser.error("input must be a .tsv file")

    out_path = in_path.with_name(in_path.name[:-len(".tsv")] + "_FinBERTScored.tsv")
    if out_path == in_path:
        parser.error("output would overwrite the input")

    print(f"Reading {in_path.name}...", flush=True)
    with open(in_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            parser.error("input file is empty")
        rows = list(reader)

    if args.headline_col not in header:
        parser.error(f"column '{args.headline_col}' not found in {in_path.name}")
    if args.body_col not in header:
        parser.error(f"column '{args.body_col}' not found in {in_path.name}")

    headline_idx = header.index(args.headline_col)
    body_idx = header.index(args.body_col)

    if args.limit is not None:
        rows = rows[:args.limit]

    print(f"Input : {in_path.name}  ({len(rows)} rows, {len(header)} cols)")

    # Append header columns for Prosus FinBERT (headline + body)
    header.extend([
        "Prosus_pos_hd", "Prosus_neg_hd", "Prosus_neu_hd",
        "Prosus_ss_hd", "Prosus_lab_hd", "Prosus_ms_hd",
        "Prosus_pos_bd", "Prosus_neg_bd", "Prosus_neu_bd",
        "Prosus_ss_bd", "Prosus_lab_bd", "Prosus_ms_bd",
    ])

    t0 = time.perf_counter()
    score_table(rows, headline_idx, body_idx)
    elapsed = time.perf_counter() - t0
    print(f"Scoring done in {elapsed:.1f} s ({elapsed/len(rows)*1000:.1f} ms/row)", flush=True)

    print(f"Writing {out_path.name}...", flush=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writerow(header)
        writer.writerows(rows)

    print(f"Wrote {out_path}  ({len(rows)} rows, {len(header)} cols)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
