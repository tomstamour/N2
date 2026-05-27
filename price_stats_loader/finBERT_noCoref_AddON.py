"""Enrich a news TSV with FinBERT sentiment scores computed via the
no-coref pipeline and re-aggregated with all six neutral-management
strategies.

For each row in the input TSV, this script:
  1. Looks up the article JSON in orchestrator3/outputs/ by the row's `ID`.
  2. Runs FinBERT_body_noCoref.FinBERTBodyPipeline on the article body to
     produce per-sentence positive/negative/neutral probabilities.
  3. Runs finBERT_neutral_management_addON.process() for methods 1..6,
     re-aggregating into an `adjusted_sentiment_score` per ticker.
  4. Pulls the score for the row's `Symbol` and writes it into a new
     column named after the method slug.

Output: <input_stem>_finBERT_noCoref_AddON.tsv in --output-dir.
"""

import argparse
import importlib.util
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd


# --- Locate sibling scripts on disk ----------------------------------------
_THIS_FILE = Path(__file__).resolve()
_REPO_SCRIPTS = _THIS_FILE.parent.parent  # /home/tom/Documents/ibkr_scripts/N2/scripts
_FINBERT_NOCOREF_DIR = _REPO_SCRIPTS / "FinBERT_pipeline" / "FinBERT_body_noCoref"
_ORCHESTRATOR3_OUTPUTS = _REPO_SCRIPTS / "orchestrator3" / "outputs"

if str(_FINBERT_NOCOREF_DIR) not in sys.path:
    sys.path.insert(0, str(_FINBERT_NOCOREF_DIR))


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("finBERT_noCoref_AddON")


def _import_finbert_pipeline():
    from FinBERT_body_noCoref import FinBERTBodyPipeline  # noqa: E402
    return FinBERTBodyPipeline


def _import_management_module():
    # The management script lives next to FinBERT_body_noCoref.py and has a
    # camelCase filename. Load it by path so the module name is stable.
    path = _FINBERT_NOCOREF_DIR / "finBERT_neutral_management_addON.py"
    spec = importlib.util.spec_from_file_location("finBERT_neutral_management_addON", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_id_index(orchestrator_outputs_dir: Path) -> dict:
    """Build {article_id: json_path} for every JSON in orchestrator_outputs_dir."""
    index = {}
    collisions = 0
    for path in sorted(orchestrator_outputs_dir.rglob("*.json")):
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning(f"Skipping unreadable JSON {path.name}: {e}")
            continue
        article_id = data.get("id")
        if not article_id:
            continue
        if article_id in index:
            collisions += 1
            continue
        index[article_id] = path
    logger.info(
        f"Indexed {len(index)} article JSONs from {orchestrator_outputs_dir}"
        + (f" ({collisions} duplicate id collisions ignored)" if collisions else "")
    )
    return index


def run_pipeline_and_cache(pipeline, json_path: Path, intermediates_dir: Path) -> Path:
    """Run FinBERTBodyPipeline on json_path and write <stem>_FinBERT.json to
    intermediates_dir. Returns the path to that file. If it already exists,
    skip the model run and return the cached path."""
    stem = json_path.stem
    finbert_path = intermediates_dir / f"{stem}_FinBERT.json"
    if finbert_path.is_file():
        logger.info(f"  reusing cached {finbert_path.name}")
        return finbert_path

    result = pipeline.process(json_path, write_outputs=False)
    finbert_payload = result["finbert"]

    intermediates_dir.mkdir(parents=True, exist_ok=True)
    with finbert_path.open("w", encoding="utf-8") as f:
        json.dump(finbert_payload, f, indent=2, ensure_ascii=False)
    logger.info(f"  wrote {finbert_path.name}")
    return finbert_path


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Enrich a news TSV with six FinBERT sentiment columns (one per "
            "neutral-management method) computed via the no-coref pipeline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        metavar="FILE",
        dest="input_file",
        help="Path to the input enriched .tsv file (must contain Symbol and ID columns).",
    )
    parser.add_argument(
        "--output-dir", "-o",
        metavar="DIR",
        dest="output_dir",
        default=None,
        help="Directory for the output TSV. Defaults to the input file's directory.",
    )
    parser.add_argument(
        "--orchestrator-outputs",
        metavar="DIR",
        default=str(_ORCHESTRATOR3_OUTPUTS),
        help=f"Directory of article JSONs (default: {_ORCHESTRATOR3_OUTPUTS}).",
    )
    args = parser.parse_args()

    input_path = Path(args.input_file).resolve()
    if not input_path.is_file():
        sys.exit(f"Error: input TSV not found: {input_path}")

    out_dir = Path(args.output_dir).resolve() if args.output_dir else input_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    intermediates_dir = out_dir / "finBERT_noCoref_intermediates"

    df = pd.read_csv(input_path, sep="\t")
    for required in ("Symbol", "ID"):
        if required not in df.columns:
            sys.exit(f"Error: input TSV missing required column '{required}'.")

    # Build JSON index
    id_index = build_id_index(Path(args.orchestrator_outputs))

    # Import the pipeline and management modules
    FinBERTBodyPipeline = _import_finbert_pipeline()
    mgmt = _import_management_module()
    method_slugs = mgmt.METHOD_SLUGS  # {1: "neutral_filter", ..., 6: "recommended"}

    # Pre-allocate the six new columns
    for slug in method_slugs.values():
        df[slug] = pd.NA

    # Load models once
    pipeline = FinBERTBodyPipeline(
        output_dir=intermediates_dir,
        write_outputs=False,
        log_source=False,
    )
    pipeline.load_models()

    rows_processed = 0
    rows_missing_json = 0
    rows_missing_ticker = 0

    try:
        for idx, row in df.iterrows():
            symbol = str(row["Symbol"]).strip()
            article_id = str(row["ID"]).strip()

            if not article_id or article_id == "nan":
                logger.warning(f"Row {idx}: empty ID — skipping.")
                rows_missing_json += 1
                continue

            json_path = id_index.get(article_id)
            if json_path is None:
                logger.warning(f"Row {idx} ({symbol}, id={article_id}): no JSON in index — skipping.")
                rows_missing_json += 1
                continue

            logger.info(f"Row {idx} ({symbol}, id={article_id}): processing {json_path.name}")

            try:
                finbert_path = run_pipeline_and_cache(pipeline, json_path, intermediates_dir)
            except Exception as e:
                logger.error(f"Row {idx} ({symbol}): FinBERT pipeline failed — {e}")
                continue

            # Run each of the six management methods (fast — pure re-aggregation)
            symbol_seen = False
            for method_num, slug in method_slugs.items():
                try:
                    out_json_path = mgmt.process(
                        finbert_path,
                        intermediates_dir,
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
                df.at[idx, slug] = ticker_block.get("adjusted_sentiment_score")

            if not symbol_seen:
                logger.info(f"Row {idx} ({symbol}): ticker not present in FinBERT entities — columns left empty.")
                rows_missing_ticker += 1
            else:
                rows_processed += 1
    finally:
        pipeline.shutdown()

    input_stem = os.path.splitext(os.path.basename(input_path))[0]
    out_path = out_dir / f"{input_stem}_finBERT_noCoref_AddON.tsv"
    df.to_csv(out_path, sep="\t", index=False)
    logger.info(f"Saved enriched file to {out_path}")
    logger.info(
        f"Rows total: {len(df)}, processed: {rows_processed}, "
        f"missing JSON: {rows_missing_json}, ticker not resolved: {rows_missing_ticker}"
    )


if __name__ == "__main__":
    main()
