#!/usr/bin/env python3
"""
Convert empty/blank cells in a TSV to explicit 'NaN' strings.
Useful for marking missing values before machine learning pipelines.
"""

import argparse
import csv
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description="Convert blank/empty cells in TSV to 'NaN' strings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python missing_data_to_NaN.py --input data.tsv
  python missing_data_to_NaN.py --input data_splits_rmCol.tsv
        """
    )
    parser.add_argument(
        "--input",
        required=True,
        type=str,
        help="Path to input TSV file"
    )

    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Generate output path by inserting _NaN before .tsv
    output_path = input_path.with_stem(input_path.stem + "_NaN")

    print(f"Reading from: {input_path}")
    print(f"Writing to:   {output_path}")

    row_count = 0
    nan_count = 0

    with open(input_path, newline='', encoding='utf-8', errors='replace') as infile, \
         open(output_path, 'w', newline='', encoding='utf-8') as outfile:

        reader = csv.reader(infile, delimiter='\t')
        writer = csv.writer(outfile, delimiter='\t')

        for row in reader:
            row_count += 1
            # Convert empty strings to 'NaN'
            converted_row = []
            for cell in row:
                if cell == '' or cell is None:
                    converted_row.append('NaN')
                    nan_count += 1
                else:
                    converted_row.append(cell)
            writer.writerow(converted_row)

    print(f"\nCompleted:")
    print(f"  Rows processed:    {row_count}")
    print(f"  Cells converted:   {nan_count}")
    print(f"  Output file:       {output_path}")


if __name__ == "__main__":
    main()
