#!/usr/bin/env python
"""Remove specified columns from a TSV file."""

import argparse
import sys
import pandas as pd
from pathlib import Path


def load_columns_to_remove(filepath):
    """Load column names from file, one per line."""
    with open(filepath, 'r') as f:
        cols = [line.strip() for line in f if line.strip()]
    return cols


def remove_columns(input_tsv, columns_file, output_tsv=None):
    """Remove columns from TSV and save to output file."""

    # Load columns to remove
    cols_to_remove = load_columns_to_remove(columns_file)
    print(f"Loaded {len(cols_to_remove)} columns to remove from {columns_file}")

    # Read TSV
    df = pd.read_csv(input_tsv, sep='\t')
    print(f"Loaded TSV: {len(df)} rows × {len(df.columns)} columns from {input_tsv}")

    # Find columns that exist in both the dataframe and removal list
    cols_present = [c for c in cols_to_remove if c in df.columns]
    cols_missing = [c for c in cols_to_remove if c not in df.columns]

    if cols_missing:
        print(f"⚠ {len(cols_missing)} columns not found in TSV (will skip): {cols_missing}")

    if not cols_present:
        print("No columns to remove found in TSV. Exiting.")
        return

    print(f"Removing {len(cols_present)} columns: {cols_present}")

    # Remove columns
    df_filtered = df.drop(columns=cols_present)
    print(f"Result: {len(df_filtered)} rows × {len(df_filtered.columns)} columns")

    # Determine output path
    if output_tsv is None:
        input_path = Path(input_tsv)
        output_tsv = input_path.parent / f"{input_path.stem}_rmCol.tsv"

    # Save to TSV
    df_filtered.to_csv(output_tsv, sep='\t', index=False)
    print(f"✓ Saved to {output_tsv}")


def main():
    parser = argparse.ArgumentParser(
        description="Remove specified columns from a TSV file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --input data.tsv --column-to-remove-file columns_to_delete.txt
  %(prog)s --input data.tsv --column-to-remove-file cols.txt --output result.tsv
        """
    )

    parser.add_argument(
        '--input',
        required=True,
        help='Input TSV file'
    )
    parser.add_argument(
        '--column-to-remove-file',
        required=True,
        help='File containing column names to remove (one per line)'
    )
    parser.add_argument(
        '--output',
        default=None,
        help='Output TSV file (default: <input_name>_rmCol.tsv)'
    )

    args = parser.parse_args()

    # Validate inputs exist
    if not Path(args.input).exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    if not Path(args.column_to_remove_file).exists():
        print(f"Error: Column file not found: {args.column_to_remove_file}", file=sys.stderr)
        sys.exit(1)

    try:
        remove_columns(args.input, args.column_to_remove_file, args.output)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
