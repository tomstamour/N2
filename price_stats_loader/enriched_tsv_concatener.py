"""Concatenate multiple enriched TSV files into one, aligning columns and filling missing with NA."""

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("enriched_tsv_concatener")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Concatenate enriched TSV files listed in a text file into a single TSV.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list-file", "-l",
        required=True,
        metavar="FILE",
        dest="list_file",
        help="Text file with one TSV filename per line.",
    )
    parser.add_argument(
        "--input-dir",
        metavar="DIR",
        dest="input_dir",
        default=None,
        help="Directory containing the TSV files. Defaults to the list file's directory.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        dest="output",
        default=None,
        help="Output TSV path. Defaults to <list-file-dir>/concatenated.tsv.",
    )
    args = parser.parse_args()

    list_path = Path(args.list_file).resolve()
    if not list_path.is_file():
        sys.exit(f"Error: list file not found: {list_path}")

    input_dir = Path(args.input_dir).resolve() if args.input_dir else list_path.parent
    out_path = Path(args.output).resolve() if args.output else list_path.parent / "concatenated.tsv"

    filenames = [
        line.strip()
        for line in list_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    if not filenames:
        sys.exit("Error: list file contains no entries.")

    dfs = []
    all_cols: list[str] = []
    seen_cols: set[str] = set()

    for name in filenames:
        tsv_path = input_dir / name
        if not tsv_path.is_file():
            logger.warning("TSV not found, skipping: %s", tsv_path)
            continue
        df = pd.read_csv(tsv_path, sep="\t", dtype=str)
        for col in df.columns:
            if col not in seen_cols:
                all_cols.append(col)
                seen_cols.add(col)
        dfs.append(df)
        logger.info("Loaded %d rows from %s", len(df), name)

    if not dfs:
        sys.exit("Error: no TSV files could be loaded.")

    aligned = [df.reindex(columns=all_cols) for df in dfs]
    merged = pd.concat(aligned, ignore_index=True)
    merged.to_csv(out_path, sep="\t", index=False)

    logger.info(
        "Wrote %d rows across %d files → %s",
        len(merged), len(dfs), out_path,
    )


if __name__ == "__main__":
    main()
