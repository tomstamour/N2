import csv
import json
import os
import argparse
import logging
import pandas as pd
from pathlib import Path

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

SCRIPT_DIR = Path(__file__).parent
DEFAULT_ORCHESTRATOR_DIR = SCRIPT_DIR / '..' / 'orchestrator3'


def build_id_index(orchestrator_dir: Path) -> dict:
    """Scan accepted_PRs/ and blocked_PRs/ and return {article_id: filepath}."""
    index = {}
    for subdir in ('outputs/accepted_PRs', 'outputs/blocked_PRs'):
        pr_dir = orchestrator_dir / subdir
        if not pr_dir.is_dir():
            logging.warning(f"Directory not found, skipping: {pr_dir}")
            continue
        for entry in pr_dir.iterdir():
            if entry.suffix == '.json':
                article_id = entry.name.split('-')[0]
                if article_id not in index:
                    index[article_id] = entry
    logging.info(f"Index built: {len(index)} unique article IDs found.")
    return index


def read_tsv_flexible(path: str) -> pd.DataFrame:
    """Read a TSV where some rows may have more fields than the header declares."""
    with open(path, newline='') as f:
        reader = csv.reader(f, delimiter='\t')
        header = next(reader)
        rows = [dict(zip(header, fields)) for fields in reader]
    return pd.DataFrame(rows, columns=header)


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a news TSV with an Exchange column.\n\n'
            'Looks up each article ID from the ID column in the PR JSON files '
            'produced by Orchestrator3 and writes the exchange name (e.g. NYSE, NASDAQ) '
            'into a new Exchange column. Exchange names are compatible with highOfDay_addON.py.\n\n'
            'Output file is named <input_stem>_enriched.tsv and written to '
            '--output-dir (defaults to the same directory as the input file).'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='FILE',
        dest='input_file',
        help='Path to the input .tsv file (must contain an ID column).',
    )
    parser.add_argument(
        '--output-dir', '-o',
        metavar='DIR',
        dest='output_dir',
        default=None,
        help="Directory where the enriched TSV will be written. Defaults to the input file's directory.",
    )
    parser.add_argument(
        '--orchestrator-dir',
        metavar='DIR',
        dest='orchestrator_dir',
        default=str(DEFAULT_ORCHESTRATOR_DIR),
        help='Path to the orchestrator3 directory. Defaults to ../orchestrator3 relative to this script.',
    )
    args = parser.parse_args()

    orchestrator_dir = Path(args.orchestrator_dir).resolve()
    id_index = build_id_index(orchestrator_dir)

    df = read_tsv_flexible(args.input_file)
    if 'ID' not in df.columns:
        logging.error("Input TSV does not have an 'ID' column. Exiting.")
        return

    exchange_cache: dict = {}
    exchanges = []
    not_found = 0

    for _, row in df.iterrows():
        article_id = str(row['ID']).strip()

        if article_id in exchange_cache:
            exchanges.append(exchange_cache[article_id])
            continue

        filepath = id_index.get(article_id)
        if filepath is None:
            logging.warning(f"ID '{article_id}' not found in PR files — Exchange left empty.")
            exchange_cache[article_id] = ''
            exchanges.append('')
            not_found += 1
            continue

        try:
            with open(filepath) as f:
                data = json.load(f)
            exchange = data.get('exchange', '')
        except Exception as e:
            logging.warning(f"Could not read {filepath}: {e}")
            exchange = ''

        exchange_cache[article_id] = exchange
        exchanges.append(exchange)

    df['Exchange'] = exchanges

    input_stem = os.path.splitext(os.path.basename(args.input_file))[0]
    out_dir = args.output_dir if args.output_dir else os.path.dirname(os.path.abspath(args.input_file))
    out_path = os.path.join(out_dir, f"{input_stem}_enriched.tsv")
    df.to_csv(out_path, sep='\t', index=False)

    logging.info(f"Saved enriched file to {out_path}")
    logging.info(f"Rows: {len(df)}, unique IDs resolved: {len(exchange_cache) - not_found}, not found: {not_found}")


if __name__ == '__main__':
    main()
