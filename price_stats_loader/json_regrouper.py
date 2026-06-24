import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

# Defaults are relative to this script's location so the tool works from anywhere.
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_DIR = SCRIPT_DIR.parent / 'orchestrator3' / 'outputs'
DEFAULT_DEST_DIR = SCRIPT_DIR / 'inputs' / 'jsons'
DEFAULT_INDEX_FILE = SCRIPT_DIR / '.json_id_index.json'


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


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Regroup orchestrator3 json files by the "ID" column of a stats TSV.\n\n'
            'For each ID in the table, the matching json (whose internal "id" field\n'
            'equals that ID) is moved from the source directory into the destination.\n'
            'Matching uses a cached id->path index so repeat runs are fast.'
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
        '--source-dir', '-s',
        default=str(DEFAULT_SOURCE_DIR),
        metavar='DIR',
        help=f'Directory holding the json files (default: {DEFAULT_SOURCE_DIR}).',
    )
    parser.add_argument(
        '--dest-dir', '-d',
        default=str(DEFAULT_DEST_DIR),
        metavar='DIR',
        help=f'Directory to move matched json files into (default: {DEFAULT_DEST_DIR}).',
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
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Report what would be moved without moving anything.',
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

    dest_dir = Path(args.dest_dir)
    index_file = Path(args.index_file)

    df = pd.read_csv(input_path, sep='\t', dtype=str)
    if 'ID' not in df.columns:
        print("ERROR: column 'ID' not found in input file.", file=sys.stderr)
        sys.exit(1)

    # Preserve order, drop blanks and duplicates.
    ids = [i for i in df['ID'].fillna('').tolist() if i.strip()]
    unique_ids = list(dict.fromkeys(ids))
    print(f"{len(ids)} IDs in table → {len(unique_ids)} unique IDs.")

    index = load_index(source_dir, index_file, args.rebuild_index)

    if not args.dry_run:
        dest_dir.mkdir(parents=True, exist_ok=True)

    moved = 0
    already_present = []   # already in dest (e.g. moved on a previous run)
    missing = []           # no matching json found
    for file_id in unique_ids:
        src_str = index.get(file_id)
        src = Path(src_str) if src_str else None

        if src is None or not src.is_file():
            # Source gone: maybe it was already moved into dest on a prior run.
            if src is not None and (dest_dir / src.name).is_file():
                already_present.append(file_id)
            else:
                missing.append(file_id)
            continue

        target = dest_dir / src.name
        if target.exists():
            already_present.append(file_id)
            continue

        if args.dry_run:
            print(f"[dry-run] would move {src} → {target}")
            moved += 1
            continue

        shutil.move(str(src), str(target))
        moved += 1

    verb = "Would move" if args.dry_run else "Moved"
    print()
    print(f"{verb}: {moved}")
    print(f"Already in destination: {len(already_present)}")
    print(f"Unmatched IDs (no json found): {len(missing)}")
    if missing:
        print("\nUnmatched IDs:")
        for file_id in missing:
            print(f"  {file_id}")
        if not args.rebuild_index:
            print("\n(If these json files do exist, the index may be stale — "
                  "re-run with --rebuild-index.)")


if __name__ == '__main__':
    main()
