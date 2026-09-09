#!/usr/bin/env python
"""
make_finbert_embeddings.py -- batch FinBERT embeddings for the training set (back-testing).

Reads the current training TSV (default: the most recently modified
08_*_RawData_splits*rmCol*.tsv in this directory) and writes a WIDE sidecar TSV
in the SAME row order:

    <input_stem>_FinBERTemb.tsv

Columns:
    row_idx, Symbol, ArrivalTime,
    hd_emb_000 .. hd_emb_767   -- Headline, mean-pooled FinBERT last hidden layer
    bd_emb_000 .. bd_emb_767   -- Body, body_prep (200-word cap), mean-pooled

Blank Headline / Body -> all-zero vector for that stream.
768 columns per stream, one value per column (never a packed vector) -- ready for
PCA later. PCA / any transform is fit on the training fold only, NOT here.

    /home/tom/venv/bin/python make_finbert_embeddings.py
    /home/tom/venv/bin/python make_finbert_embeddings.py --input FILE.tsv --limit 50

One-time model export (if not done yet):
    /home/tom/venv/bin/python finbert_embed_core.py --export
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import finbert_embed_core as core

HERE = Path(__file__).resolve().parent
csv.field_size_limit(16 * 1024 * 1024)


def newest_training_tsv():
    cands = [
        p for p in HERE.glob("08_*_RawData_splits*.tsv")
        if not p.name.endswith("_FinBERTemb.tsv")
    ]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--input", help="training TSV (default: newest 08_*_RawData_splits*.tsv here)")
    ap.add_argument("--output", help="output sidecar TSV (default: <input_stem>_FinBERTemb.tsv)")
    ap.add_argument("--headline-col", default="Headline")
    ap.add_argument("--body-col", default="Body")
    ap.add_argument("--symbol-col", default="Symbol")
    ap.add_argument("--time-col", default="ArrivalTime")
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--limit", type=int, metavar="N", help="only the first N rows (smoke test)")
    args = ap.parse_args()

    in_path = Path(args.input).expanduser().resolve() if args.input else newest_training_tsv()
    if not in_path or not in_path.is_file():
        ap.error(f"input TSV not found: {in_path}")
    out_path = (Path(args.output).expanduser().resolve() if args.output
                else in_path.with_name(in_path.stem + "_FinBERTemb.tsv"))
    if out_path == in_path:
        ap.error("output would overwrite the input")

    with open(in_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            ap.error("input file is empty")
        rows = list(reader)

    for col in (args.headline_col, args.body_col, args.symbol_col, args.time_col):
        if col not in header:
            ap.error(f"column {col!r} not found in {in_path.name}")
    hi = header.index(args.headline_col)
    bi = header.index(args.body_col)
    si = header.index(args.symbol_col)
    ti = header.index(args.time_col)

    if args.limit is not None:
        rows = rows[:args.limit]

    print(f"input : {in_path.name}  ({len(rows)} rows, {len(header)} cols)")
    print(f"output: {out_path.name}")

    core.load_model()

    def cell(row, i):
        return row[i] if i < len(row) else ""

    headlines = [core.prep_headline(cell(r, hi)) for r in rows]
    bodies = [core.prep_body(cell(r, bi), cell(r, hi)) for r in rows]

    t0 = time.perf_counter()
    print("[emb] headlines ...", flush=True)
    hd = core.embed_texts(headlines, batch_size=args.batch_size, progress_every=500)
    print("[emb] bodies ...", flush=True)
    bd = core.embed_texts(bodies, batch_size=args.batch_size, progress_every=500)
    print(f"[emb] done in {time.perf_counter() - t0:.1f}s", flush=True)

    out_header = (
        ["row_idx", "Symbol", "ArrivalTime"]
        + [f"hd_emb_{i:03d}" for i in range(core.EMBED_DIM)]
        + [f"bd_emb_{i:03d}" for i in range(core.EMBED_DIM)]
    )
    print(f"writing {out_path.name}  ({len(rows)} rows, {len(out_header)} cols) ...", flush=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writerow(out_header)
        for k, row in enumerate(rows):
            writer.writerow([
                k, cell(row, si), cell(row, ti),
                *(f"{v:.6f}" for v in hd[k]),
                *(f"{v:.6f}" for v in bd[k]),
            ])
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
