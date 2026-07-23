import argparse
import sys
import pandas as pd
from pathlib import Path


def _ticker_set(symbol, tickers):
    """Uppercased set of tickers from the Symbol and Tickers fields.

    Tickers may be plain comma-separated ("APPL, APPLW") or a JSON-style list
    string ('["APPL", "APPLW"]'); brackets and quotes are stripped either way.
    """
    out = set()
    for raw in [symbol] + str(tickers if pd.notna(tickers) else '').split(','):
        t = str(raw if pd.notna(raw) else '').strip().strip('[]"\'').strip().upper()
        if t:
            out.add(t)
    return out


def _tickers_overlap(a, b):
    """True when the two ticker sets refer to the same company: any identical
    ticker, or one ticker being the warrant of the other (exact base+'W' only,
    so e.g. SNOW never matches SNO)."""
    if not a or not b:
        return True
    if a & b:
        return True
    return any(t + 'W' in b for t in a) or any(t + 'W' in a for t in b)


def remove_duplicates(df):
    """Drop duplicate PRs, keeping the earliest-ArrivalTime row (ties -> file order).

    Pass A: rows sharing a non-empty ID.
    Pass B: rows sharing a normalized (trimmed, lowercased) Headline whose ticker
    sets overlap (a warrant like APPLW counts as its base APPL).
    Returns (deduped df in original file order, n_dropped_id, n_dropped_headline).
    """
    if 'ArrivalTime' in df.columns:
        arrival = pd.to_datetime(df['ArrivalTime'], errors='coerce')
    else:
        arrival = pd.Series(pd.NaT, index=df.index)
    order = arrival.sort_values(kind='stable', na_position='last').index
    sdf = df.loc[order]

    dropped_id = 0
    if 'ID' in df.columns:
        ids = sdf['ID'].fillna('').astype(str).str.strip()
        dup_mask = ids.duplicated(keep='first') & (ids != '')
        dropped_id = int(dup_mask.sum())
        sdf = sdf[~dup_mask]

    dropped_hl = 0
    if 'Headline' in df.columns:
        hl_norm = sdf['Headline'].fillna('').astype(str).str.strip().str.lower()
        symbols = sdf['Symbol'] if 'Symbol' in sdf.columns else pd.Series('', index=sdf.index)
        tickers = sdf['Tickers'] if 'Tickers' in sdf.columns else pd.Series('', index=sdf.index)
        keep_idx = []
        kept_by_headline = {}  # normalized headline -> ticker sets of kept rows
        for idx, hl, sym, tks in zip(sdf.index, hl_norm, symbols, tickers):
            tset = _ticker_set(sym, tks)
            kept_sets = kept_by_headline.setdefault(hl, [])
            if hl and any(_tickers_overlap(tset, k) for k in kept_sets):
                dropped_hl += 1
                continue
            kept_sets.append(tset)
            keep_idx.append(idx)
        sdf = sdf.loc[keep_idx]

    return df[df.index.isin(sdf.index)], dropped_id, dropped_hl


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Filter a news TSV: duplicate removal, Float and/or DailyHigh($) caps, '
            'excluded-strings on Headline.\n\n'
            'Rows that exceed the specified upper bounds are removed.\n'
            'When a filter is active, rows with a missing value in that column are also dropped.\n'
            'Duplicates (same ID, or same Headline for the same company incl. warrant '
            'tickers) are removed by default; the earliest-ArrivalTime row is kept.'
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
        '--remove-duplicates',
        choices=['true', 'false'],
        default='true',
        metavar='<true|false>',
        help=(
            'Remove duplicate rows (default: true). Two passes, both keeping the '
            'earliest-ArrivalTime row: same ID, then same Headline '
            '(case-insensitive, trimmed) with overlapping Symbol/Tickers, where a '
            'warrant ticker (base+W) matches its base company.'
        ),
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

    dropped_id = dropped_hl = 0
    if args.remove_duplicates == 'true':
        df, dropped_id, dropped_hl = remove_duplicates(df)

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
        # Load excluded strings the same way Orchestrator5.0 does: one per line,
        # stripped, blanks skipped, deduped into a set.
        excluded = {
            line.strip() for line in exclude_path.read_text().splitlines()
            if line.strip()
        }
        if excluded:
            # Match exactly like O5: case-insensitive substring on the Headline
            # only (never the body) — `s.lower() in headline.lower()`.
            excluded_lower = [s.lower() for s in excluded]
            hl_lower = df['Headline'].fillna('').str.lower()
            mask = hl_lower.apply(lambda h: any(s in h for s in excluded_lower))
            df = df[~mask]

    output_path = Path(args.output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, sep='\t', index=False)

    dedup_note = (
        f"{dropped_id + dropped_hl} duplicates dropped "
        f"({dropped_id} dup-ID, {dropped_hl} dup-headline) → "
        if args.remove_duplicates == 'true' else ''
    )
    print(f"{total} rows in → {dedup_note}{len(df)} rows out → {output_path}")


if __name__ == '__main__':
    main()
