#!/usr/bin/env python
"""
BodyScore11encoders.py — batch-score a PR-stats TSV's Body column with all 11
headliners. Twin of HeadScore11encoders.py (which scores the Headline column).

Each Body cell is prepped with body_prep.prep_body(): newswire cleaning
(CSV-escape artifact, repeated headline, "CITY, Date (WIRE) --" dateline,
tracker URLs/vendor tokens) then capped at the first 200 words (~260–290
tokens; each model's own tokenizer enforces its final 512/256/128-token max).
Model outputs are appended as `{Model}_{channel}_bd` columns so they never
collide with the `{Model}_{channel}_hd` headline columns of an already-scored
file. Output file is the input filename with `.tsv` replaced by
`_11EncBodyScored.tsv`.

⚠ 2026-08-09: the body marker moved from the `_Body_` INFIX to a terminal `_bd`
SUFFIX, so head and body columns now differ only in their last token and every
column matches what Orchestrator5.2 writes live. Files scored before that date
carry the old `{Model}_Body_{key}` names — migrate them with
rename_encoder_cols.py rather than re-scoring.

Usage:
    /home/tom/venv/bin/python BodyScore11encoders.py \
        --input ../../price_stats_loader/outputs/PRstats_..._11EncScored.tsv
    # quick test on a subset:
    /home/tom/venv/bin/python BodyScore11encoders.py --input FILE \
        --limit 5 --models DistilRo,LouMcD
"""

import argparse
import csv
import sys
import time
from pathlib import Path

import body_prep
from HeadScore11encoders import MODELS, score_table

OUT_SUFFIX = "_11EncBodyScored.tsv"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score every Body (cleaned, first 200 words) of a TSV with "
                    "the 11 encoder headliners.")
    parser.add_argument("--input", required=True, help="input .tsv path")
    parser.add_argument("--models", metavar="NAME[,NAME...]",
                        help=f"subset of models (default: all). Choices: {', '.join(MODELS)}")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="score only the first N rows (quick test)")
    parser.add_argument("--body-col", default="Body",
                        help="header name of the body column (default: Body)")
    parser.add_argument("--headline-col", default="Headline",
                        help="headline column, used to strip its repeat from the "
                             "body (default: Headline)")
    parser.add_argument("--cap-words", type=int, default=body_prep.DEFAULT_CAP_WORDS,
                        help="max words of cleaned body to score (default: "
                             f"{body_prep.DEFAULT_CAP_WORDS})")
    args = parser.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.is_file():
        parser.error(f"input not found: {in_path}")
    if not in_path.name.endswith(".tsv"):
        parser.error("input must be a .tsv file")
    out_path = in_path.with_name(in_path.name[:-len(".tsv")] + OUT_SUFFIX)
    if out_path == in_path:
        parser.error("output would overwrite the input")

    if args.models:
        model_names = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = [m for m in model_names if m not in MODELS]
        if unknown:
            parser.error(f"unknown model(s): {', '.join(unknown)}. Choices: {', '.join(MODELS)}")
    else:
        model_names = list(MODELS)

    with open(in_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            parser.error("input file is empty")
        rows = list(reader)
    for col in (args.body_col, args.headline_col):
        if col not in header:
            parser.error(f"column '{col}' not found in {in_path.name}")
    body_idx = header.index(args.body_col)
    headline_idx = header.index(args.headline_col)
    if args.limit is not None:
        rows = rows[:args.limit]

    print(f"Input : {in_path.name}  ({len(rows)} rows, {len(header)} cols)")
    print(f"Models: {', '.join(model_names)}")

    # Prep every body once, up front, into a scratch column at the end of each
    # row; score_table() reads it, then it is dropped before writing.
    print(f"Prepping bodies (clean + first {args.cap_words} words) …", flush=True)
    t0 = time.perf_counter()
    prep_idx = len(header)
    for row in rows:
        body = row[body_idx] if body_idx < len(row) else ""
        headline = row[headline_idx] if headline_idx < len(row) else ""
        while len(row) < prep_idx:
            row.append("")
        row.append(body_prep.prep_body(body, headline, args.cap_words))
    print(f"Prep done in {time.perf_counter() - t0:.1f} s")

    for name in model_names:
        # `_bd` = the scored text was the BODY. Since 2026-08-09 this is a terminal
        # SUFFIX matching HeadScore's `_hd`, replacing the old `_Body_` infix — which
        # is why head_vs_body.py / merge_body_encoder_cols.py no longer need a
        # BODY_MARKER split.
        header.extend(f"{name}_{k}_bd" for k in MODELS[name]["keys"])

    t0 = time.perf_counter()
    score_table(rows, prep_idx, model_names)
    print(f"Scoring done in {time.perf_counter() - t0:.1f} s")

    # drop the scratch prep column (header never contained it)
    for row in rows:
        del row[prep_idx]

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Wrote {out_path}  ({len(rows)} rows, {len(header)} cols)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
