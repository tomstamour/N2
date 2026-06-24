#!/usr/bin/env python3
"""Enrich a news TSV with a rule-based headline score column.

For every row, the text of the chosen column (e.g. ``Headline``) is scored 0-100
by the rule-based engine in ``../tools/small-cap-news-scorer/fast_signal.py`` and
written to a new ``<column>-RBscore`` column. Boost/penalty words and their common
variations drive the score (see fast_signal.py); no IBKR connection is needed.

Example:
    python3 rule-based-AddOn.py \\
        --input outputs/PR-stats-..._dailyHigh_exStrings.tsv \\
        --column-name Headline \\
        --output outputs/PR-stats-..._RBscore.tsv
"""

import argparse
import logging
import os
import sys
from pathlib import Path

import pandas as pd

# fast_signal.py lives in ../tools/small-cap-news-scorer relative to this script.
SCORER_DIR = Path(__file__).resolve().parent.parent / "tools" / "small-cap-news-scorer"
sys.path.insert(0, str(SCORER_DIR))
from fast_signal import SignalEngine  # noqa: E402


def setup_logging(log_path: str | None = None) -> None:
    """Configure logging to stderr and, optionally, to a file."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path:
        log_dir = os.path.dirname(os.path.abspath(log_path))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Add a rule-based score column to a news TSV.\n\n'
            'Scores the text of --column-name with the rules engine from '
            'fast_signal.py and writes the 0-100 result to a new '
            '"<column>-RBscore" column. Empty cells stay empty.'
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
        '--column-name', '-c',
        required=True,
        metavar='NAME',
        dest='column_name',
        help='Name of the text column to score (e.g. Headline).',
    )
    parser.add_argument(
        '--output', '-o',
        metavar='FILE',
        dest='output_file',
        default=None,
        help=('Path of the output .tsv file. Defaults to '
              '<input_stem>_RBscore.tsv in the input file\'s directory.'),
    )
    parser.add_argument(
        '--threshold', '-t',
        type=int,
        default=50,
        metavar='N',
        help='TAKE threshold passed to the engine (0-100). Does not affect the '
             'score itself, only the engine\'s internal decision. Default: 50.',
    )
    parser.add_argument(
        '--log', '-l',
        metavar='FILE',
        dest='log_file',
        default=None,
        help='Path to a log file (appended), in addition to the console.',
    )
    args = parser.parse_args()

    setup_logging(args.log_file)
    if args.log_file:
        logging.info(f"Logging to {os.path.abspath(args.log_file)}")

    df = pd.read_csv(args.input_file, sep='\t')

    if args.column_name not in df.columns:
        logging.error(
            f"Column '{args.column_name}' not found. Available columns: "
            f"{list(df.columns)}"
        )
        return

    score_col = f"{args.column_name}-RBscore"
    engine = SignalEngine(mode='rules', threshold=args.threshold)

    scores = []
    scored = 0
    for value in df[args.column_name]:
        # Leave blank for missing/empty cells (NaN or whitespace-only).
        if pd.isna(value) or not str(value).strip():
            scores.append(None)
            continue
        scores.append(engine.check(str(value))['score'])
        scored += 1

    df[score_col] = scores

    input_stem = os.path.splitext(os.path.basename(args.input_file))[0]
    out_path = args.output_file or os.path.join(
        os.path.dirname(os.path.abspath(args.input_file)),
        f"{input_stem}_RBscore.tsv",
    )
    out_dir = os.path.dirname(os.path.abspath(out_path))
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    df.to_csv(out_path, sep='\t', index=False)
    logging.info(f"Saved scored file to {out_path}")
    logging.info(
        f"Rows: {len(df)} | scored '{args.column_name}': {scored} | "
        f"blank (empty cell): {len(df) - scored} | new column: '{score_col}'"
    )


if __name__ == '__main__':
    main()
