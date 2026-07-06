"""Enrich a PR-stats TSV with the article body text.

For each row in the input TSV, this script:
  1. Looks up the article JSON in orchestrator3/outputs/ by the row's `ID`
     (matching the JSON's internal "id" field, via a cached id->path index).
  2. Reads the "article_body" text from that JSON.
  3. Writes the first N words of that text into a new single column named
     `body`, with all whitespace collapsed so it stays in one TSV cell.

Rows whose JSON cannot be found get the marker `NOT_FOUND` in the `body`
column. Output: <input_stem>_body.tsv in --output-dir.
"""

import argparse
import json
import re
import sys
from pathlib import Path

import pandas as pd

# Defaults are relative to this script's location so the tool works from anywhere.
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = SCRIPT_DIR.parent / 'orchestrator3' / 'outputs'
DEFAULT_INDEX_FILE = SCRIPT_DIR / '.json_id_index.json'
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / 'outputs'

NOT_FOUND = 'NOT_FOUND'


def build_index(source_dir, index_file):
    """Scan every *.json under source_dir, map each file's "id" -> its path."""
    index = {}
    scanned = 0
    skipped = 0
    for path in source_dir.rglob('*.json'):
        scanned += 1
        try:
            with path.open(encoding='utf-8') as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            skipped += 1
            continue
        if not isinstance(data, dict):
            skipped += 1
            continue
        file_id = data.get('id')
        if file_id:
            # First occurrence wins; later duplicates are ignored.
            index.setdefault(str(file_id), str(path))
    index_file.write_text(json.dumps(index), encoding='utf-8')
    print(f"Indexed {len(index)} ids from {scanned} json files "
          f"({skipped} unreadable/skipped) → {index_file}")
    return index


def load_index(source_dir, index_file, rebuild):
    if rebuild or not index_file.is_file():
        if rebuild:
            print("Rebuilding id index ...")
        else:
            print("No cached index found, building one (first run is slow) ...")
        return build_index(source_dir, index_file)
    try:
        index = json.loads(index_file.read_text(encoding='utf-8'))
        print(f"Loaded cached index of {len(index)} ids from {index_file} "
              f"(use --rebuild-index to refresh).")
        return index
    except (json.JSONDecodeError, OSError):
        print("Cached index unreadable, rebuilding ...")
        return build_index(source_dir, index_file)


def extract_body(json_path, n_words):
    """Return the first n_words words of the JSON's article_body, with all
    whitespace collapsed to single spaces. Returns '' on read/parse error."""
    try:
        with Path(json_path).open(encoding='utf-8') as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"WARNING: could not read {json_path}: {exc}", file=sys.stderr)
        return ''
    if not isinstance(data, dict):
        return ''
    text = data.get('article_body') or ''
    # Collapse all newlines/tabs/whitespace runs so the body stays in one cell.
    text = re.sub(r'\s+', ' ', str(text)).strip()
    if not text:
        return ''
    words = text.split(' ')
    return ' '.join(words[:n_words])


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a PR-stats TSV with a single "body" column holding the\n'
            'first N words of each row\'s article_body text. The article JSON\n'
            'is located by matching its internal "id" field to the row\'s "ID"\n'
            'column, using a cached id->path index for speed.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--input', '-i',
        required=True,
        metavar='FILE',
        dest='input_file',
        help='Path to the stats .tsv file containing an "ID" column.',
    )
    parser.add_argument(
        '--number-of-words',
        required=True,
        type=float,
        metavar='N',
        dest='number_of_words',
        help='Number of words to include from the start of the article body.',
    )
    parser.add_argument(
        '--output-dir', '-o',
        default=str(DEFAULT_OUTPUT_DIR),
        metavar='DIR',
        help=f'Directory for the output TSV (default: {DEFAULT_OUTPUT_DIR}).',
    )
    parser.add_argument(
        '--source-dir', '-s',
        default=str(DEFAULT_SOURCE_DIR),
        metavar='DIR',
        help=f'Directory holding the json files (default: {DEFAULT_SOURCE_DIR}).',
    )
    parser.add_argument(
        '--index-file',
        default=str(DEFAULT_INDEX_FILE),
        metavar='FILE',
        help=f'Path to the cached id->path index (default: {DEFAULT_INDEX_FILE}).',
    )
    parser.add_argument(
        '--rebuild-index',
        action='store_true',
        help='Force a fresh scan of the source directory before matching.',
    )
    args = parser.parse_args()

    n_words = int(args.number_of_words)
    if n_words <= 0:
        print("ERROR: --number-of-words must be greater than 0.", file=sys.stderr)
        sys.exit(1)

    input_path = Path(args.input_file)
    if not input_path.is_file():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    source_dir = Path(args.source_dir)
    if not source_dir.is_dir():
        print(f"ERROR: source directory not found: {source_dir}", file=sys.stderr)
        sys.exit(1)

    index_file = Path(args.index_file)
    out_dir = Path(args.output_dir)

    df = pd.read_csv(input_path, sep='\t', dtype=str)
    if 'ID' not in df.columns:
        print("ERROR: column 'ID' not found in input file.", file=sys.stderr)
        sys.exit(1)

    index = load_index(source_dir, index_file, args.rebuild_index)

    # Resolve each ID once; duplicate IDs reuse the same extracted body.
    body_cache = {}
    matched = 0
    not_found = 0
    bodies = []
    for raw_id in df['ID'].fillna('').tolist():
        file_id = raw_id.strip()
        if not file_id:
            bodies.append(NOT_FOUND)
            not_found += 1
            continue
        if file_id not in body_cache:
            src_str = index.get(file_id)
            if src_str and Path(src_str).is_file():
                body_cache[file_id] = extract_body(src_str, n_words)
            else:
                body_cache[file_id] = NOT_FOUND
        value = body_cache[file_id]
        if value == NOT_FOUND:
            not_found += 1
        else:
            matched += 1
        bodies.append(value)

    df['body'] = bodies

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{input_path.stem}_body.tsv"
    df.to_csv(out_path, sep='\t', index=False)

    print()
    print(f"Rows written: {len(df)}")
    print(f"Matched (body filled): {matched}")
    print(f"Unmatched ({NOT_FOUND}): {not_found}")
    print(f"Output: {out_path}")


if __name__ == '__main__':
    main()
