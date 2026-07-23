"""Concatenate multiple enriched TSV files into one, aligning columns and filling missing with NA."""

import argparse
import logging
import os
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
        description=(
            "Concatenate several enriched TSV files into a single TSV, aligning columns\n"
            "across files so nothing is lost.\n"
            "\n"
            "What it does:\n"
            "  1. Takes the TSVs to combine either from a list file (-l, one filename\n"
            "     per line) or as an existing base file plus files to append to it\n"
            "     (--base + --add). In -l mode, missing or unreadable files are\n"
            "     skipped with a warning, not treated as fatal; in --base/--add mode\n"
            "     a missing file is fatal (silently dropping a day would corrupt the\n"
            "     growing master).\n"
            "  2. Normalizes a lowercase 'body' column to 'Body' in each file so the\n"
            "     two never end up as separate columns in the output.\n"
            "  3. Builds the union of all columns seen, preserving first-seen order\n"
            "     (the base/first file's column order wins).\n"
            "  4. Reindexes every file to that full column set; columns a file lacks\n"
            "     are filled with NA.\n"
            "  5. Concatenates all rows and writes one combined TSV (written to a\n"
            "     temp file, then atomically renamed into place)."
        ),
        epilog=(
            "Examples:\n"
            "  enriched_tsv_concatener.py -l files.txt\n"
            "  enriched_tsv_concatener.py -l files.txt --input-dir ./tsvs -o merged.tsv\n"
            "  enriched_tsv_concatener.py --base master.tsv --add day1.tsv day2.tsv -o new_master.tsv"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list-file", "-l",
        metavar="FILE",
        dest="list_file",
        help="Text file with one TSV filename per line; blank lines are ignored. "
             "Mutually exclusive with --base/--add.",
    )
    parser.add_argument(
        "--base",
        metavar="FILE",
        dest="base",
        help="Existing combined TSV to append to; loaded first so its column order wins. "
             "Requires --add and -o.",
    )
    parser.add_argument(
        "--add",
        metavar="FILE",
        dest="add",
        nargs="+",
        help="One or more TSV files (paths) to append after --base.",
    )
    parser.add_argument(
        "--input-dir",
        metavar="DIR",
        dest="input_dir",
        default=None,
        help="Directory the TSV filenames are resolved against. Default: the list file's own directory.",
    )
    parser.add_argument(
        "--output", "-o",
        metavar="FILE",
        dest="output",
        default=None,
        help="Path for the combined TSV. Default: concatenated.tsv next to the list file.",
    )
    args = parser.parse_args()

    if args.list_file and (args.base or args.add):
        sys.exit("Error: -l/--list-file and --base/--add are mutually exclusive.")

    if args.base or args.add:
        if not (args.base and args.add):
            sys.exit("Error: --base and --add must be used together.")
        if not args.output:
            sys.exit("Error: -o/--output is required with --base/--add.")
        tsv_paths = [Path(args.base).resolve()] + [Path(p).resolve() for p in args.add]
        for p in tsv_paths:
            if not p.is_file():
                sys.exit(f"Error: TSV not found: {p}")
        out_path = Path(args.output).resolve()
    elif args.list_file:
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
        tsv_paths = [input_dir / name for name in filenames]
    else:
        sys.exit("Error: provide either -l/--list-file or --base/--add.")

    dfs = []
    all_cols: list[str] = []
    seen_cols: set[str] = set()

    for tsv_path in tsv_paths:
        if not tsv_path.is_file():
            logger.warning("TSV not found, skipping: %s", tsv_path)
            continue
        df = pd.read_csv(tsv_path, sep="\t", dtype=str)
        if "body" in df.columns:
            if "Body" in df.columns:
                df["Body"] = df["Body"].fillna(df["body"])
                df = df.drop(columns=["body"])
            else:
                df = df.rename(columns={"body": "Body"})
        for col in df.columns:
            if col not in seen_cols:
                all_cols.append(col)
                seen_cols.add(col)
        dfs.append(df)
        logger.info("Loaded %d rows from %s", len(df), tsv_path.name)

    if not dfs:
        sys.exit("Error: no TSV files could be loaded.")

    aligned = [df.reindex(columns=all_cols) for df in dfs]
    merged = pd.concat(aligned, ignore_index=True)
    tmp_path = out_path.with_name(out_path.name + ".tmp")
    merged.to_csv(tmp_path, sep="\t", index=False)
    os.replace(tmp_path, out_path)

    logger.info(
        "Wrote %d rows across %d files → %s",
        len(merged), len(dfs), out_path,
    )


if __name__ == "__main__":
    main()
