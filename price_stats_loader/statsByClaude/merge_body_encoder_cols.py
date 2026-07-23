#!/usr/bin/env python3
"""Attach the 70 body-scored encoder columns onto the canonical kw-scored TSV.

The 11-encoder body pass (`BodyScore11encoders`) was run on
`..._11EncScored.tsv`, which branched off the pipeline BEFORE the keyword
scoring and the FineClass/BroadClass pass. So the body file carries:

  * the 70 new `<enc>_Body_<channel>` columns   <- the only thing we want
  * a STALE copy of the 28 keyword columns (model_proba filled on 2712 rows
    only, i.e. the 594 recent rows still unscored)
  * no BroadClass / FineClass at all

The canonical file `..._Classed_kwScored.tsv` has the opposite profile. So this
merges one way only: canonical wins every shared column, the body file
contributes exclusively its `_Body_` columns. Anything else would silently
regress the keyword scoring.

Both files are the same 3306 rows in the same order with a unique `ID`, so the
join is checked rather than guessed: identical ID sets, identical ID order, and
no `_Body_` column may collide with an existing canonical column.

Pure numpy / pandas.

Run:
    /home/tom/venv/bin/python merge_body_encoder_cols.py
"""
import argparse
import pathlib
import sys

import pandas as pd

CANONICAL_DEFAULT = ("../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_"
                     "11EncScored_Classed_kwScored.tsv")
BODY_DEFAULT = ("../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_"
                "11EncScored_11EncBodyScored.tsv")
OUT_DEFAULT = ("../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_"
               "11EncScored_Classed_kwScored_BodyEnc.tsv")

BODY_MARKER = "_Body_"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canonical", default=CANONICAL_DEFAULT)
    ap.add_argument("--body", default=BODY_DEFAULT)
    ap.add_argument("--out", default=OUT_DEFAULT)
    ap.add_argument("--key", default="ID")
    args = ap.parse_args()

    can_path, body_path = pathlib.Path(args.canonical), pathlib.Path(args.body)
    for p in (can_path, body_path):
        if not p.exists():
            sys.exit(f"File not found: {p}")

    can = pd.read_csv(can_path, sep="\t", dtype=str)
    body = pd.read_csv(body_path, sep="\t", dtype=str)
    print(f"canonical {can_path.name}: {len(can)} rows x {len(can.columns)} cols")
    print(f"body      {body_path.name}: {len(body)} rows x {len(body.columns)} cols")

    key = args.key
    for name, df in (("canonical", can), ("body", body)):
        if key not in df.columns:
            sys.exit(f"key column {key!r} missing from {name}")
        if df[key].duplicated().any():
            sys.exit(f"key column {key!r} is not unique in {name}")

    if not can[key].equals(body[key]):
        sys.exit(f"{key} order/content differs between the two files; this "
                 f"script only handles the aligned case")

    body_cols = [c for c in body.columns if BODY_MARKER in c]
    if not body_cols:
        sys.exit(f"no columns containing {BODY_MARKER!r} found in the body file")
    collisions = [c for c in body_cols if c in can.columns]
    if collisions:
        sys.exit(f"body columns already present in canonical: {collisions}")

    merged = pd.concat([can, body[body_cols]], axis=1)

    # The body file's stale keyword columns must NOT have leaked through.
    stale = [c for c in ("model_proba", "model_proba_PLUS_kw_mult_nb")
             if c in merged.columns]
    for c in stale:
        if not merged[c].equals(can[c]):
            sys.exit(f"internal error: {c} no longer matches the canonical file")

    out_path = pathlib.Path(args.out)
    merged.to_csv(out_path, sep="\t", index=False)

    print(f"\nadded {len(body_cols)} body columns "
          f"({len(body_cols) // 11} channels x 11 encoders, approx)")
    print(f"wrote {out_path}: {len(merged)} rows x {len(merged.columns)} cols")
    filled = merged[body_cols].notna().all(axis=1).sum()
    print(f"rows with every body column populated: {filled}/{len(merged)}")
    for c in ("BroadClass", "FineClass", "model_proba"):
        if c in merged.columns:
            print(f"  {c}: {merged[c].notna().sum()} non-null")


if __name__ == "__main__":
    main()
