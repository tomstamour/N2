#!/usr/bin/env python3
"""Fill blank cells in a master TSV from a partial scored TSV, joined on ID.

Written for the case where a corpus grew by appending rows that lacked the
expensive score columns (11-encoder headline/body, FineClass/BroadClass), those
rows were then scored in isolation, and the results must land back in the master
without disturbing anything already there.

Unlike ``merge_body_encoder_cols.py`` — which is hardcoded to a specific pair of
same-length, same-order files — this joins on ``ID`` and merges a SUBSET of rows.

Safety rules, all enforced (the run aborts and writes nothing if any is broken):

  * every column of the patch file must ALREADY exist in the master — the merge
    never invents columns, so a renamed scorer output is caught instead of
    silently adding a near-duplicate column;
  * every patch ID must exist in the master, and be unique on both sides;
  * fill-only-empty: a master cell that is already non-empty is never
    overwritten. Set --overwrite to allow it (off by default);
  * the master's row count, row order and column order are preserved exactly.

Run:
    merge_scores_by_id.py --master corpus.tsv --patch scored_subset.tsv \
        --output corpus_merged.tsv [--id-col ID] [--skip-cols ID,Symbol,...]
"""

import argparse
import csv
import os
import sys

csv.field_size_limit(10**9)

BLANK = ('', 'nan', 'NaN')


def read_tsv(path):
    with open(path, newline='', encoding='utf-8') as fh:
        rd = csv.DictReader(fh, delimiter='\t')
        return rd.fieldnames, list(rd)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--master', required=True, help='the full corpus TSV')
    ap.add_argument('--patch', required=True,
                    help='TSV holding the scored subset (joined on --id-col)')
    ap.add_argument('--output', required=True, help='path to write the merged TSV')
    ap.add_argument('--id-col', default='ID', help='join key (default: ID)')
    ap.add_argument('--skip-cols', default='',
                    help='comma-separated patch columns to ignore (identity '
                         'columns carried along for readability)')
    ap.add_argument('--overwrite', action='store_true',
                    help='allow overwriting non-empty master cells (default: no)')
    ap.add_argument('--allow-new-cols', action='store_true',
                    help='permit patch columns absent from the master, appending '
                         'them to the end of the header. Off by default so a '
                         'renamed scorer output is caught rather than silently '
                         'creating a near-duplicate column.')
    ap.add_argument('--force', action='store_true', help='overwrite --output if it exists')
    args = ap.parse_args()

    if os.path.exists(args.output) and not args.force:
        print(f"[fatal] output exists: {args.output} (pass --force)", file=sys.stderr)
        return 1

    mcols, mrows = read_tsv(args.master)
    pcols, prows = read_tsv(args.patch)
    idc = args.id_col
    skip = {c.strip() for c in args.skip_cols.split(',') if c.strip()} | {idc}

    for name, cols in (('master', mcols), ('patch', pcols)):
        if idc not in cols:
            print(f"[fatal] {name} has no '{idc}' column", file=sys.stderr)
            return 1

    fill_cols = [c for c in pcols if c not in skip]
    unknown = [c for c in fill_cols if c not in mcols]
    if unknown and not args.allow_new_cols:
        print(f"[fatal] {len(unknown)} patch column(s) absent from master — refusing "
              f"to invent columns: {', '.join(unknown[:8])}"
              f"{' ...' if len(unknown) > 8 else ''}", file=sys.stderr)
        print("[fatal] pass --allow-new-cols if adding them is intended.", file=sys.stderr)
        return 1
    if unknown:
        mcols = mcols + unknown
        for r in mrows:
            for c in unknown:
                r[c] = ''
        print(f"[info] adding {len(unknown)} new column(s): {', '.join(unknown)}")

    mids = [r[idc] for r in mrows]
    if len(set(mids)) != len(mids):
        print("[fatal] master has duplicate IDs", file=sys.stderr)
        return 1
    pids = [r[idc] for r in prows]
    if len(set(pids)) != len(pids):
        print("[fatal] patch has duplicate IDs", file=sys.stderr)
        return 1
    by_id = {r[idc]: r for r in mrows}
    missing = [i for i in pids if i not in by_id]
    if missing:
        print(f"[fatal] {len(missing)} patch ID(s) not in master: {missing[:5]}",
              file=sys.stderr)
        return 1

    filled = 0
    skipped_nonempty = 0
    for prow in prows:
        mrow = by_id[prow[idc]]
        for c in fill_cols:
            val = prow[c]
            if val in BLANK:
                continue
            if mrow[c] not in BLANK:
                if not args.overwrite:
                    skipped_nonempty += 1
                    continue
            mrow[c] = val
            filled += 1

    tmp = args.output + '.tmp'
    with open(tmp, 'w', newline='', encoding='utf-8') as fh:
        w = csv.DictWriter(fh, fieldnames=mcols, delimiter='\t')
        w.writeheader()
        w.writerows(mrows)
    os.replace(tmp, args.output)

    print(f"master : {len(mrows)} rows x {len(mcols)} cols")
    print(f"patch  : {len(prows)} rows, {len(fill_cols)} fillable cols")
    print(f"filled : {filled} cells")
    if skipped_nonempty:
        print(f"left alone (already non-empty): {skipped_nonempty} cells")
    print(f"output : {args.output}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
