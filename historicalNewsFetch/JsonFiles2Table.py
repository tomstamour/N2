#!/usr/bin/env python3
"""
JsonFiles2Table.py

Batch-processes historical news JSON files into a single TSV table enriched
with float share data and FinBERT sentiment scores.

Usage:
    python JsonFiles2Table.py \
        --directory ./outputs \
        --output_name table.tsv \
        --float_table ./floats.tsv
"""

import argparse
import glob
import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

# Repo-relative anchor: this file is scripts/historicalNewsFetch/, so .parent.parent
# is scripts/ — resolve N2's own FinBERT headliner so a fresh clone runs as-is.
FINBERT_SCRIPT = str(Path(__file__).resolve().parent.parent / "FinBERT" / "FinBERT-headliner.py")

UTC_OFFSET = -4  # UTC-4


def load_finbert():
    spec = importlib.util.spec_from_file_location("finbert_headliner", FINBERT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.analyze_headline


def load_float_table(path):
    df = pd.read_csv(path, sep="\t", dtype={"Symbol": str})
    return dict(zip(df["Symbol"].str.upper(), df["Float_M"]))


def parse_created_at(created_at_str):
    dt = datetime.fromisoformat(created_at_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt_adjusted = dt + timedelta(hours=UTC_OFFSET)
    return dt_adjusted.strftime("%Y-%m-%d"), dt_adjusted.strftime("%H:%M:%S")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Convert news JSON files to a TSV table with sentiment scores."
    )
    parser.add_argument("--directory", required=True, help="Directory containing JSON news files")
    parser.add_argument("--output_name", required=True, help="Output TSV filename")
    parser.add_argument("--float_table", required=True, help="TSV file with Symbol and Float_M columns")
    return parser.parse_args()


def main():
    args = parse_args()

    if not os.path.isdir(args.directory):
        sys.exit(f"ERROR: --directory '{args.directory}' does not exist.")
    if not os.path.isfile(args.float_table):
        sys.exit(f"ERROR: --float_table '{args.float_table}' does not exist.")

    print("Loading FinBERT model...")
    analyze_headline = load_finbert()

    print(f"Loading float table from {args.float_table}...")
    float_dict = load_float_table(args.float_table)

    json_files = sorted(glob.glob(os.path.join(args.directory, "*.json")))
    print(f"Found {len(json_files)} JSON files in {args.directory}")

    rows = []
    skipped = 0

    for i, filepath in enumerate(json_files, 1):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                article = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: skipping {os.path.basename(filepath)}: {e}")
            skipped += 1
            continue

        headline = article.get("headline", "")
        created_at = article.get("created_at", "")
        symbols = article.get("symbols", [])

        if not headline or not created_at or not symbols:
            print(f"  WARNING: skipping {os.path.basename(filepath)}: missing required fields")
            skipped += 1
            continue

        try:
            date_str, time_str = parse_created_at(created_at)
        except (ValueError, TypeError) as e:
            print(f"  WARNING: skipping {os.path.basename(filepath)}: bad created_at '{created_at}': {e}")
            skipped += 1
            continue

        sentiment = analyze_headline(headline)

        for symbol in symbols:
            symbol_upper = symbol.upper()
            float_val = float_dict.get(symbol_upper, float("nan"))
            rows.append({
                "Symbol": symbol_upper,
                "Date": date_str,
                "Time": time_str,
                "Headline": headline,
                "Float": float_val,
                "positive": sentiment["positive"],
                "negative": sentiment["negative"],
                "neutral": sentiment["neutral"],
                "sentiment_score": sentiment["sentiment_score"],
                "label": sentiment["label"],
            })

        if i % 50 == 0 or i == len(json_files):
            print(f"  Processed {i}/{len(json_files)} files ({len(rows)} rows so far)...")

    df = pd.DataFrame(rows, columns=[
        "Symbol", "Date", "Time", "Headline", "Float",
        "positive", "negative", "neutral", "sentiment_score", "label"
    ])

    df.to_csv(args.output_name, sep="\t", index=False)
    print(f"\nDone. {len(rows)} rows written to {args.output_name}")
    if skipped:
        print(f"Skipped {skipped} files due to errors.")


if __name__ == "__main__":
    main()
