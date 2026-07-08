"""Retrofit the PR "created" timestamp into a PR-stats TSV.

For each row in the input TSV whose `Created` cell is empty, this script:
  1. Looks up the article JSON in orchestrator3/outputs/ by the row's `ID`
     (matching the JSON's internal "id" field, via a cached id->path index).
  2. Reads the "created" ISO-UTC timestamp from that JSON (falling back to
     article_published_at / published_at / created_at).
  3. Converts it to US/Eastern and writes the time-of-day as HH:MM:SS.mmm
     into the `Created` column, matching the format of already-filled rows.

Rows that already have a `Created` value are left untouched. Rows whose JSON
(or timestamp) cannot be found stay empty. Output: <input_stem>_created.tsv
in --output-dir.
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

# Defaults are relative to this script's location so the tool works from anywhere.
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = SCRIPT_DIR.parent / 'orchestrator3' / 'outputs'
DEFAULT_INDEX_FILE = SCRIPT_DIR / '.json_id_index.json'
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / 'outputs'

EASTERN = ZoneInfo('America/New_York')

# Same fallback order the firehose connector uses when extracting timestamps.
CREATED_KEYS = ('created', 'article_published_at', 'published_at', 'created_at')


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


def extract_created(json_path):
    """Return the JSON's created timestamp as HH:MM:SS.mmm in US/Eastern,
    or '' if the file/timestamp cannot be read or parsed."""
    try:
        with Path(json_path).open(encoding='utf-8') as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        print(f"WARNING: could not read {json_path}: {exc}", file=sys.stderr)
        return ''
    if not isinstance(data, dict):
        return ''
    raw = ''
    for key in CREATED_KEYS:
        value = data.get(key)
        if value:
            raw = str(value).strip()
            break
    if not raw:
        return ''
    try:
        dt = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        print(f"WARNING: unparseable created value {raw!r} in {json_path}",
              file=sys.stderr)
        return ''
    dt = dt.astimezone(EASTERN)
    return dt.strftime('%H:%M:%S') + f".{dt.microsecond // 1000:03d}"


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Fill the empty cells of the "Created" column of a PR-stats TSV\n'
            'with the article\'s created timestamp (HH:MM:SS.mmm, US/Eastern).\n'
            'The article JSON is located by matching its internal "id" field\n'
            'to the row\'s "ID" column, using a cached id->path index for speed.'
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
    if 'Created' not in df.columns:
        df['Created'] = ''

    index = load_index(source_dir, index_file, args.rebuild_index)

    # Resolve each ID once; duplicate IDs reuse the same extracted timestamp.
    created_cache = {}
    kept = 0
    filled = 0
    missing = 0
    values = []
    for raw_id, existing in zip(df['ID'].fillna('').tolist(),
                                df['Created'].fillna('').tolist()):
        if existing.strip():
            kept += 1
            values.append(existing)
            continue
        file_id = raw_id.strip()
        if not file_id:
            missing += 1
            values.append('')
            continue
        if file_id not in created_cache:
            src_str = index.get(file_id)
            if src_str and Path(src_str).is_file():
                created_cache[file_id] = extract_created(src_str)
            else:
                created_cache[file_id] = ''
        value = created_cache[file_id]
        if value:
            filled += 1
        else:
            missing += 1
        values.append(value)

    df['Created'] = values

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{input_path.stem}_created.tsv"
    df.to_csv(out_path, sep='\t', index=False)

    print()
    print(f"Rows written: {len(df)}")
    print(f"Already filled (kept): {kept}")
    print(f"Newly filled: {filled}")
    print(f"Still empty (not found / bad timestamp): {missing}")
    print(f"Output: {out_path}")


if __name__ == '__main__':
    main()
