#!/usr/bin/env python3
"""Split a PRstats corpus into a temporal Training / Validation pair.

The split is TEMPORAL, on ``ArrivalDate`` against a cutoff — never on row
position. Two reasons row position is unsafe here:

  * corpus files are only accidentally in date order (appending a later batch
    puts newer rows after older ones, so "the first N lines" silently changes
    meaning after any re-append or re-sort);
  * a positional cut lands mid-day, putting the same date on both sides.

Random splits are also wrong for this corpus: the keyword model's in-sample AUC
of .907 collapsed to .676 walk-forward, so only a time-ordered holdout gives an
honest read.

Rows are prepared before splitting:

  * rows with a blank label are DROPPED (they are ``fromPR_status = skip`` —
    contracts with no fetchable history — and belong in neither set);
  * duplicates on ``Symbol`` + ``Headline`` are reduced keep-first, so a
    double-delivery echo of the same PR cannot appear on both sides of the
    boundary.

Alongside the two TSVs it writes a summary reporting n / positives / prevalence
per split at each threshold. That is not decoration: the positive rate in this
corpus drifts strongly upward over time (17.7% in early May to ~35% in July), so
evaluating a model against a stale or pooled base rate flatters it.

Run:
    split_train_val.py -i outputs/04_PRstats_....tsv --cutoff 2026-06-20
"""

import argparse
import collections
import csv
import os
import sys

csv.field_size_limit(10**9)

BLANK = ('', 'nan', 'NaN')


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', '-i', required=True, dest='input_file')
    ap.add_argument('--cutoff', required=True,
                    help='YYYY-MM-DD; train is < cutoff, validation is >= cutoff')
    ap.add_argument('--out-dir', default=None,
                    help="output directory (default: the input file's directory)")
    ap.add_argument('--label-col', default='fromPR_High(%)')
    ap.add_argument('--date-col', default='ArrivalDate')
    ap.add_argument('--thresholds', default='15,20,30',
                    help='comma-separated label cuts to report (default 15,20,30)')
    ap.add_argument('--dedup-cols', default='Symbol,Headline',
                    help="comma-separated keep-first dedup key ('' to disable)")
    ap.add_argument('--force', action='store_true')
    args = ap.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(',') if t.strip()]
    dedup_cols = [c.strip() for c in args.dedup_cols.split(',') if c.strip()]

    with open(args.input_file, newline='', encoding='utf-8') as fh:
        rd = csv.DictReader(fh, delimiter='\t')
        cols = rd.fieldnames
        rows = list(rd)
    total = len(rows)

    for c in [args.label_col, args.date_col] + dedup_cols:
        if c not in cols:
            print(f"[fatal] column {c!r} not in input", file=sys.stderr)
            return 1

    labeled = [r for r in rows if r[args.label_col] not in BLANK]
    dropped_unlabeled = total - len(labeled)

    kept, seen = [], set()
    for r in labeled:
        k = tuple(r[c] for c in dedup_cols) if dedup_cols else None
        if k is not None and k in seen:
            continue
        if k is not None:
            seen.add(k)
        kept.append(r)
    dropped_dupes = len(labeled) - len(kept)

    train = [r for r in kept if r[args.date_col] < args.cutoff]
    val = [r for r in kept if r[args.date_col] >= args.cutoff]

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.input_file))
    stem = os.path.splitext(os.path.basename(args.input_file))[0]
    tag = args.cutoff.replace('-', '')
    paths = {
        'train': os.path.join(out_dir, f"{stem}_TRAIN_pre{tag}.tsv"),
        'val': os.path.join(out_dir, f"{stem}_VAL_from{tag}.tsv"),
        'summary': os.path.join(out_dir, f"{stem}_split_summary.md"),
    }
    for p in paths.values():
        if os.path.exists(p) and not args.force:
            print(f"[fatal] exists: {p} (pass --force)", file=sys.stderr)
            return 1

    for key, data in (('train', train), ('val', val)):
        with open(paths[key], 'w', newline='', encoding='utf-8') as fh:
            w = csv.DictWriter(fh, fieldnames=cols, delimiter='\t')
            w.writeheader()
            w.writerows(data)

    def stats(data):
        out = []
        for t in thresholds:
            p = sum(1 for r in data if float(r[args.label_col]) >= t)
            out.append((t, p, p / len(data) * 100 if data else 0.0))
        return out

    def span(data):
        d = [r[args.date_col] for r in data if r[args.date_col]]
        return (min(d), max(d)) if d else ('-', '-')

    lines = [
        f"# Train / validation split — {stem}", '',
        f"Temporal split on `{args.date_col}` at cutoff **{args.cutoff}** "
        f"(train `< cutoff`, validation `>= cutoff`).", '',
        f"- input rows: {total}",
        f"- dropped, no `{args.label_col}` label: {dropped_unlabeled}",
        f"- dropped, duplicate on {'+'.join(dedup_cols) or 'n/a'} (keep-first): {dropped_dupes}",
        f"- train: {len(train)}  |  validation: {len(val)}"
        f"  ({len(train)/max(1,len(train)+len(val))*100:.0f}/"
        f"{len(val)/max(1,len(train)+len(val))*100:.0f})", '',
        '| split | n | date span | ' + ' | '.join(
            f'≥{t:g}% pos (prevalence)' for t in thresholds) + ' |',
        '|---|---|---|' + '---|' * len(thresholds),
    ]
    for name, data in (('train', train), ('validation', val)):
        lo, hi = span(data)
        cells = ' | '.join(f"{p} ({r:.1f}%)" for _, p, r in stats(data))
        lines.append(f"| {name} | {len(data)} | {lo} → {hi} | {cells} |")
    lines += ['', '## Base-rate drift', '',
              'The positive rate in this corpus rises steeply over time, so a '
              'model calibrated on the training block will under-predict on '
              'validation, and precision/lift must be read against the '
              '**period-specific** prevalence above — not a pooled or pinned '
              'value.', '',
              '| half-month | n | ' + ' | '.join(f'≥{t:g}%' for t in thresholds) + ' |',
              '|---|---|' + '---|' * len(thresholds)]
    b = collections.defaultdict(list)
    for r in kept:
        d = r[args.date_col]
        if d:
            b[d[:7] + ('a' if d[8:10] <= '15' else 'b')].append(r)
    for k in sorted(b):
        cells = ' | '.join(f"{r:.1f}%" for _, _, r in stats(b[k]))
        lines.append(f"| {k} | {len(b[k])} | {cells} |")
    with open(paths['summary'], 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')

    print(f"input {total} rows x {len(cols)} cols")
    print(f"  dropped unlabeled : {dropped_unlabeled}")
    print(f"  dropped duplicates: {dropped_dupes}")
    print(f"  train {len(train)}  ({span(train)[0]} -> {span(train)[1]})  -> {paths['train']}")
    print(f"  val   {len(val)}  ({span(val)[0]} -> {span(val)[1]})  -> {paths['val']}")
    for name, data in (('train', train), ('val', val)):
        print(f"  {name}: " + ', '.join(
            f"≥{t:g}% {p} ({r:.1f}%)" for t, p, r in stats(data)))
    print(f"  summary -> {paths['summary']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
