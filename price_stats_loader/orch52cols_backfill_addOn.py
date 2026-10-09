"""Add Orchestrator5.2 sentiment/class columns to the XGBscores dataset.

This script backfills 6 sentiment and classification columns that Orchestrator5.2
now outputs live. These columns are pulled from the canonical processed PR-stats
file and joined by the composite key (Symbol, ArrivalDate, ArrivalTime).

Columns added:
    FineClass          News PR class (FDA_Approval, M&A, Earnings, etc.)
    Prosus_pos_hd      FinBERT headline sentiment score (0..1, pos)
    FinMarBa_ind_hd    FinMarBa headline sentiment (indicator form)
    kw15_proba_bd      kw15 body-text probability (0..1)
    kw15_proba_hd      kw15 headline probability (0..1)
    FLANG_ss_hd        FLANG-BERT headline sentiment score

Note: EarningScore was excluded because it was dropped in an upstream purge step
(see training-set-construction-steps.md step 10) and is not available in the
canonical source file.

The join key (Symbol, ArrivalDate, ArrivalTime) is guaranteed to be unique on
both sides and match 100% (1380 rows) per exploration-phase verification. This is
an exception to the pipeline's usual canonical join on PR ID (which the target
file lacks).

The source columns Prosus_pos_hd.1, FinMarBa_ind_hd.1, FLANG_ss_hd.1 are renamed
to their bare names on output (the .1 suffix indicates they were the kept/fresh
copies in the pipeline's earlier duplicate encoder column purge step).

Input is never modified in place; output is written to
    <input_stem>_orch52cols.tsv

Usage:
    /home/tom/venv/bin/python orch52cols_backfill_addOn.py \\
        --input outputs/2026-07-28_2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_PCA_val_XGBscores.tsv
"""

import argparse
import logging
import os
import sys
import tempfile
from pathlib import Path

import pandas as pd

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')

# Hardcoded defaults for the canonical source file
_SCRIPT_DIR = Path(__file__).resolve().parent
_DEFAULT_SOURCE = (
    _SCRIPT_DIR / 'outputs'
    / '08_PRstats_2026-05-04-2026-08-28-CoffeeFltrd_fromPRhigh_noSkip_11EncBodyScored'
    '_11EncScored_mktstate_SIback_noDupCol_FinBERTScored_freqSizeBack_DailyClose'
    '_mcapBack_vixfng_dow_Classed.tsv'
)
_DEFAULT_OUTPUT_DIR = _SCRIPT_DIR / 'outputs'

# Columns to pull from source (note the .1 suffixes on the encoder sentiments)
SOURCE_COLUMNS_MAP = {
    'Symbol': 'Symbol',
    'ArrivalDate': 'ArrivalDate',
    'ArrivalTime': 'ArrivalTime',
    'FineClass': 'FineClass',
    'Prosus_pos_hd.1': 'Prosus_pos_hd',       # Renamed: .1 -> bare
    'FinMarBa_ind_hd.1': 'FinMarBa_ind_hd',   # Renamed: .1 -> bare
    'kw15_proba_bd': 'kw15_proba_bd',
    'kw15_proba_hd': 'kw15_proba_hd',
    'FLANG_ss_hd.1': 'FLANG_ss_hd',           # Renamed: .1 -> bare
}


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Backfill Orchestrator5.2 sentiment and class columns (FineClass, '
            'Prosus_pos_hd, FinMarBa_ind_hd, kw15_proba_bd, kw15_proba_hd, '
            'FLANG_ss_hd) into the XGBscores TSV. Joins by composite key '
            '(Symbol, ArrivalDate, ArrivalTime); input is untouched, output is '
            '<input_stem>_orch52cols.tsv.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--input', '-i',
        dest='input_file',
        metavar='FILE',
        help=(
            'Input .tsv file (defaults to the RawData/XGBscores file in this dir).'
        ),
    )
    parser.add_argument(
        '--source', '-s',
        dest='source_file',
        metavar='FILE',
        help=(
            f'Source file to pull columns from (defaults to '
            f'{_DEFAULT_SOURCE.name}).'
        ),
    )
    parser.add_argument(
        '--output-dir', '-o',
        dest='output_dir',
        metavar='DIR',
        default=str(_DEFAULT_OUTPUT_DIR),
        help=(
            f'Output directory (defaults to {_DEFAULT_OUTPUT_DIR}).'
        ),
    )
    args = parser.parse_args()

    # Resolve paths
    if args.input_file:
        input_path = Path(args.input_file)
    else:
        # Auto-find the RawData file in outputs/
        candidates = list(_DEFAULT_OUTPUT_DIR.glob(
            '2026-07-28_2026-08-28_RawData*XGBscores.tsv'
        ))
        if len(candidates) == 1:
            input_path = candidates[0]
        elif len(candidates) > 1:
            raise SystemExit(
                f'Multiple RawData/XGBscores files found in {_DEFAULT_OUTPUT_DIR}; '
                f'pass --input explicitly.'
            )
        else:
            raise SystemExit(
                f'No RawData/XGBscores file found in {_DEFAULT_OUTPUT_DIR}; '
                f'pass --input explicitly.'
            )

    source_path = Path(args.source_file or _DEFAULT_SOURCE)
    output_dir = Path(args.output_dir)

    # Validate inputs
    if not input_path.is_file():
        raise SystemExit(f'Input file not found: {input_path}')
    if not source_path.is_file():
        raise SystemExit(f'Source file not found: {source_path}')
    if not output_dir.is_dir():
        raise SystemExit(f'Output directory not found: {output_dir}')

    logging.info(f'Input: {input_path}')
    logging.info(f'Source: {source_path}')

    # Read target file
    target = pd.read_csv(
        input_path,
        sep='\t',
        dtype=str,
        low_memory=False
    )
    logging.info(
        f'Read target: {len(target)} rows, {len(target.columns)} columns'
    )

    # Read source file, pulling only the columns we need
    source_cols = list(SOURCE_COLUMNS_MAP.keys())
    try:
        source = pd.read_csv(
            source_path,
            sep='\t',
            usecols=source_cols,
            dtype=str,
            low_memory=False
        )
    except ValueError as e:
        # If a column is missing, give a helpful error
        if 'usecols' in str(e):
            raise SystemExit(
                f'One or more required columns missing from source file: {e}'
            )
        raise

    logging.info(f'Read source: {len(source)} rows, {len(source.columns)} columns')

    # Rename the .1-suffixed columns to bare names
    source = source.rename(columns=SOURCE_COLUMNS_MAP)

    # Join: left merge on the composite key
    try:
        merged = target.merge(
            source,
            on=['Symbol', 'ArrivalDate', 'ArrivalTime'],
            how='left',
            validate='one_to_one'
        )
    except pd.errors.MergeError as e:
        raise SystemExit(f'Merge failed (non-unique keys): {e}')

    logging.info(
        f'Merged: {len(merged)} rows, {len(merged.columns)} columns'
    )

    # Check for unmatched rows (should be 0)
    new_cols = set(SOURCE_COLUMNS_MAP.values()) - {'Symbol', 'ArrivalDate', 'ArrivalTime'}
    unmatched = merged[merged[list(new_cols)[0]].isna()]
    if len(unmatched) > 0:
        logging.warning(
            f'{len(unmatched)} rows from the target did not match in the source. '
            f'These rows will have NaN in the new columns.'
        )
        for _, row in unmatched.iterrows():
            logging.warning(
                f"  Unmatched: {row['Symbol']} {row['ArrivalDate']} {row['ArrivalTime']}"
            )
    else:
        logging.info('All target rows matched successfully.')

    # Write output atomically (write to temp, then rename)
    output_stem = input_path.stem
    output_name = f'{output_stem}_orch52cols.tsv'
    output_path = output_dir / output_name

    with tempfile.NamedTemporaryFile(
        mode='w',
        dir=output_dir,
        suffix='.tsv',
        delete=False
    ) as tmp:
        tmp_path = tmp.name

    try:
        merged.to_csv(
            tmp_path,
            sep='\t',
            index=False
        )
        Path(tmp_path).replace(output_path)
    except Exception as e:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
        raise SystemExit(f'Write failed: {e}')

    logging.info(f'Wrote {output_path}')
    logging.info(
        f'Summary: {len(target)} input rows + {len(new_cols)} new columns '
        f'= {len(merged)} output rows × {len(merged.columns)} total columns'
    )

    return 0


if __name__ == '__main__':
    sys.exit(main())
