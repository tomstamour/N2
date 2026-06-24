#!/usr/bin/env python3
"""Count true/false positives for one or more score columns used as triggers.

For each requested score column we treat it as a "big mover" trigger: a row
*fires* (predicted positive) when its value clears a cutoff level. The ground
truth is set by --true-positive-threshold on DailyHigh(%): a fired row is a
true positive when DailyHigh(%) >= threshold, otherwise a false positive.

The output is a matrix whose columns are the requested score columns and whose
rows are the cutoff levels (default 0.5 0.6 0.7 0.8 0.9 0.95). Each cell holds
the raw "TP/FP" counts. Columns on a 0-100 scale (e.g. Headline-RBscore) have
the level multiplied by 100, so level 0.7 -> cutoff 70 for those columns while
staying 0.7 for the FinBERT-style [-1, 1] columns.
"""

import sys
import argparse
import pathlib

import pandas as pd

TARGET_COL = "DailyHigh(%)"
DEFAULT_LEVELS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
# A column whose max exceeds this is treated as 0-100 scale -> level * 100.
SCALE_DETECT_MAX = 1.5


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--input",
        required=True,
        type=pathlib.Path,
        help="Path to the input TSV (must contain DailyHigh(%%) and the score columns).",
    )
    p.add_argument(
        "--columns-name",
        required=True,
        nargs="+",
        metavar="COLUMN",
        help="One or more score column names to evaluate as triggers.",
    )
    p.add_argument(
        "--true-positive-threshold",
        required=True,
        type=float,
        help="DailyHigh(%%) >= this value is a true big mover (ground-truth positive).",
    )
    p.add_argument(
        "--levels",
        nargs="+",
        type=float,
        default=DEFAULT_LEVELS,
        metavar="LEVEL",
        help="Cutoff levels (matrix rows). Default: %(default)s. "
        "A fired row has column_value >= cutoff, where cutoff is the level "
        "(or level*100 for 0-100 scale columns).",
    )
    return p.parse_args(argv)


def load_data(tsv_path: pathlib.Path) -> pd.DataFrame:
    return pd.read_csv(tsv_path, sep="\t", dtype=str)


def main(argv=None):
    args = parse_args(argv)

    if not args.input.exists():
        sys.exit(f"error: input file not found: {args.input}")

    df = load_data(args.input)

    if TARGET_COL not in df.columns:
        sys.exit(f"error: required target column {TARGET_COL!r} not in input.")

    missing = [c for c in args.columns_name if c not in df.columns]
    if missing:
        sys.exit(
            f"error: column(s) not found: {', '.join(missing)}\n"
            f"available columns: {', '.join(df.columns)}"
        )

    target = pd.to_numeric(df[TARGET_COL], errors="coerce")
    keep = target.notna()
    n_total = len(df)
    n_dropped = int((~keep).sum())
    target = target[keep]
    df = df[keep]
    n_kept = len(df)

    tp_thr = args.true_positive_threshold
    is_mover = target >= tp_thr  # ground-truth positive label

    levels = list(args.levels)

    # Build matrix: rows = levels, columns = requested column names, cell = "TP/FP".
    matrix = {}
    for col in args.columns_name:
        values = pd.to_numeric(df[col], errors="coerce")
        col_max = values.max()
        scaled = bool(pd.notna(col_max) and col_max > SCALE_DETECT_MAX)
        if pd.isna(col_max):
            print(f"warning: column {col!r} is all-NaN; cells will be 0/0.", file=sys.stderr)

        cells = []
        for level in levels:
            cutoff = level * 100 if scaled else level
            fired = values >= cutoff
            tp = int((fired & is_mover).sum())
            fp = int((fired & ~is_mover).sum())
            cells.append(f"{tp}/{fp}")
        matrix[col] = cells

    # --- Terminal output ---
    print(
        f"# input: {args.input}\n"
        f"# rows kept: {n_kept} (dropped {n_dropped} of {n_total} for NaN {TARGET_COL})\n"
        f"# true-positive label: {TARGET_COL} >= {tp_thr:g}  |  cells are TP/FP\n"
    )

    level_labels = [f"{lv:g}" for lv in levels]
    col_widths = {
        col: max(len(col), *(len(c) for c in matrix[col])) for col in args.columns_name
    }
    lvl_width = max(len("level"), *(len(l) for l in level_labels))

    header = "level".ljust(lvl_width) + "  " + "  ".join(
        col.rjust(col_widths[col]) for col in args.columns_name
    )
    print(header)
    print("-" * len(header))
    for i, label in enumerate(level_labels):
        row = label.ljust(lvl_width) + "  " + "  ".join(
            matrix[col][i].rjust(col_widths[col]) for col in args.columns_name
        )
        print(row)

    # --- TSV output, next to the input file ---
    out_path = args.input.parent / f"{args.input.stem}_TPFP_tp{tp_thr:g}.tsv"
    out_df = pd.DataFrame({"level": level_labels})
    for col in args.columns_name:
        out_df[col] = matrix[col]
    out_df.to_csv(out_path, sep="\t", index=False)
    print(f"\nwrote matrix to: {out_path}")


if __name__ == "__main__":
    main()
