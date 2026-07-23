import argparse
import re
import sys
import pandas as pd
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Filter a news TSV by Float and/or DailyHigh($).\n\n'
            'Rows that exceed the specified upper bounds are removed.\n'
            'When a filter is active, rows with a missing value in that column are also dropped.\n'
            'If neither filter flag is supplied, the file is written unchanged.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='FILE',
        dest='input_file',
        help='Path to the input .tsv file.',
    )
    parser.add_argument(
        '--output', '-o',
        required=True,
        metavar='FILE',
        dest='output_file',
        help='Path for the filtered output .tsv file.',
    )
    parser.add_argument(
        '--MaxFloat',
        type=float,
        default=None,
        metavar='N',
        help='Keep rows where Float <= N (millions). Rows with no Float value are dropped.',
    )
    parser.add_argument(
        '--maxDailyHigh',
        type=float,
        default=None,
        metavar='N',
        help='Keep rows where DailyHigh($) <= N. Rows with no DailyHigh($) value are dropped.',
    )
    parser.add_argument(
        '--string-to-exclude',
        default=None,
        metavar='FILE',
        dest='exclude_file',
        help=(
            'Path to a text file with one string per line. Rows whose Headline '
            'contains any of these strings (case-insensitive) are dropped.'
        ),
    )
    args = parser.parse_args()

    input_path = Path(args.input_file)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    df = pd.read_csv(input_path, sep='\t', dtype=str)
    total = len(df)

    if args.MaxFloat is not None:
        if 'Float' not in df.columns:
            print("ERROR: column 'Float' not found in input file.", file=sys.stderr)
            sys.exit(1)
        df['Float'] = pd.to_numeric(df['Float'], errors='coerce')
        df = df[df['Float'] <= args.MaxFloat]

    if args.maxDailyHigh is not None:
        col = 'DailyHigh($)'
        if col not in df.columns:
            print(f"ERROR: column '{col}' not found in input file.", file=sys.stderr)
            sys.exit(1)
        df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df[df[col] <= args.maxDailyHigh]

    if args.exclude_file is not None:
        exclude_path = Path(args.exclude_file)
        if not exclude_path.is_file():
            print(f"ERROR: exclude file not found: {exclude_path}", file=sys.stderr)
            sys.exit(1)
        if 'Headline' not in df.columns:
            print("ERROR: column 'Headline' not found in input file.", file=sys.stderr)
            sys.exit(1)
        excluded = [
            line.strip() for line in exclude_path.read_text().splitlines()
            if line.strip()
        ]
        if excluded:
            pattern = '|'.join(re.escape(s) for s in excluded)
            mask = df['Headline'].fillna('').str.contains(pattern, case=False, regex=True)
            df = df[~mask]

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, sep='\t', index=False)

    print(f"{total} rows in → {len(df)} rows out → {output_path}")


if __name__ == '__main__':
    main()
