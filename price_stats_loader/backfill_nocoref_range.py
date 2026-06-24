"""Backfill the five `nocoref_*` sentiment columns for an ArrivalTime range.

The RBscored table already contains the columns
  nocoref_neutral_filter, nocoref_confidence_weighted, nocoref_net_score,
  nocoref_top_k, nocoref_positional
but they are empty for the early rows (ArrivalTime 2026-05-19 -> 2026-05-27
10:44:02). `finBERT_noCoref_AddON.py` was designed to *append* differently-named
columns (the bare METHOD_SLUGS, plus `recommended`) to a table that lacks them, so
running it directly would not fill these prefixed columns.

This thin driver reuses the addon's proven helpers (build_id_index,
_import_finbert_pipeline, _import_management_module, run_pipeline_and_cache) and the
neutral-management module, but writes results straight into the existing
`nocoref_<slug>` columns for just the rows in the requested ArrivalTime window. It
skips method 6 (`recommended`) because the table has no such column, never touches
`NoCorefCompletedAt`, and writes a new file alongside the original.
"""

import json
import logging
from pathlib import Path

import pandas as pd

import finBERT_noCoref_AddON as addon

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("backfill_nocoref_range")

# --- Config ----------------------------------------------------------------
INPUT = Path("outputs/PR-stats-2026-05-19to06-19-filtered-50float_dailyHigh_exStrings_RBscored.tsv")
OUTPUT = INPUT.with_name(INPUT.stem + "_nocorefFilled.tsv")
ORCHESTRATOR_OUTPUTS = addon._ORCHESTRATOR3_OUTPUTS
INTERMEDIATES = Path("outputs/finBERT_noCoref_intermediates")

RANGE_START = pd.Timestamp("2026-05-19 00:00:00")
RANGE_END = pd.Timestamp("2026-05-27 10:44:02")  # inclusive

# Methods 1..5 only (skip 6 = recommended); column = "nocoref_" + slug.
TARGET_METHODS = (1, 2, 3, 4, 5)
NOCOREF_COLS = [
    "nocoref_neutral_filter",
    "nocoref_confidence_weighted",
    "nocoref_net_score",
    "nocoref_top_k",
    "nocoref_positional",
]


def main():
    input_path = INPUT.resolve()
    if not input_path.is_file():
        raise SystemExit(f"Input TSV not found: {input_path}")

    df = pd.read_csv(input_path, sep="\t")
    orig_rows, orig_cols = df.shape

    # Rows in the ArrivalTime window that are still empty across all nocoref columns.
    at = pd.to_datetime(df["ArrivalTime"], errors="coerce")
    in_range = (at >= RANGE_START) & (at <= RANGE_END)
    empty = df[NOCOREF_COLS].isna().all(axis=1)
    mask = in_range & empty
    target_idx = df.index[mask].tolist()
    logger.info(
        f"{len(target_idx)} rows to backfill "
        f"(in_range={int(in_range.sum())}, empty={int(empty.sum())})"
    )
    if not target_idx:
        logger.info("Nothing to do.")
        return

    id_index = addon.build_id_index(Path(ORCHESTRATOR_OUTPUTS))
    mgmt = addon._import_management_module()
    slug_for = mgmt.METHOD_SLUGS  # {1: "neutral_filter", ...}

    FinBERTBodyPipeline = addon._import_finbert_pipeline()
    pipeline = FinBERTBodyPipeline(
        output_dir=INTERMEDIATES,
        write_outputs=False,
        log_source=False,
    )
    pipeline.load_models()

    processed = missing_json = missing_ticker = 0
    try:
        for n, idx in enumerate(target_idx, 1):
            symbol = str(df.at[idx, "Symbol"]).strip()
            article_id = str(df.at[idx, "ID"]).strip()

            if not article_id or article_id == "nan":
                logger.warning(f"Row {idx}: empty ID — skipping.")
                missing_json += 1
                continue

            json_path = id_index.get(article_id)
            if json_path is None:
                logger.warning(f"Row {idx} ({symbol}, id={article_id}): no JSON — skipping.")
                missing_json += 1
                continue

            logger.info(f"[{n}/{len(target_idx)}] Row {idx} ({symbol}, id={article_id}): {json_path.name}")
            try:
                finbert_path = addon.run_pipeline_and_cache(pipeline, json_path, INTERMEDIATES)
            except Exception as e:
                logger.error(f"Row {idx} ({symbol}): FinBERT pipeline failed — {e}")
                continue

            symbol_seen = False
            for method_num in TARGET_METHODS:
                slug = slug_for[method_num]
                try:
                    out_json_path = mgmt.process(
                        finbert_path,
                        INTERMEDIATES,
                        method_num,
                        neutral_threshold=0.85,
                        top_k=3,
                        positional_decay=0.1,
                    )
                except Exception as e:
                    logger.error(f"Row {idx} ({symbol}) method={slug}: aggregation failed — {e}")
                    continue

                with out_json_path.open("r", encoding="utf-8") as f:
                    method_data = json.load(f)
                ticker_block = method_data.get("ticker_sentiments", {}).get(symbol)
                if ticker_block is None:
                    continue
                symbol_seen = True
                df.at[idx, f"nocoref_{slug}"] = ticker_block.get("adjusted_sentiment_score")

            if symbol_seen:
                processed += 1
            else:
                logger.info(f"Row {idx} ({symbol}): ticker not in FinBERT entities — left empty.")
                missing_ticker += 1
    finally:
        pipeline.shutdown()

    # Guard: shape must be unchanged (no stray appended columns).
    assert df.shape == (orig_rows, orig_cols), f"shape changed: {df.shape} != {(orig_rows, orig_cols)}"
    df.to_csv(OUTPUT, sep="\t", index=False)
    logger.info(f"Saved {OUTPUT}")
    logger.info(
        f"Targeted: {len(target_idx)}, processed: {processed}, "
        f"missing JSON: {missing_json}, ticker not resolved: {missing_ticker}"
    )


if __name__ == "__main__":
    main()
