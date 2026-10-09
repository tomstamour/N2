#!/usr/bin/env python
"""
BodyScore12encoders.py — batch-score a PR-stats TSV's Body column with the 11
headliners of this directory PLUS ProsusAI FinBERT (`Prosus`), the one headliner
that lives outside it (../../FinBERT/FinBERT-headliner.py) and the model
Orchestrator5.2/5.3 run live.

Same contract as BodyScore11encoders.py, which is kept unchanged for reproducing
older outputs: each Body cell is prepped with body_prep.prep_body() (newswire
cleaning — CSV-escape artifact, repeated headline, "CITY, Date (WIRE) --"
dateline, tracker URLs/vendor tokens) then capped at the first 200 words
(`--cap-words`, ~260–290 tokens; each model's own tokenizer enforces its final
512/256/128-token max). Prosus scores that SAME prepped text, so its columns are
comparable with the other 11 and aligned with O5.3's 200-word body cap — unlike
FinBERT_TSV_scorer.py, which feeds FinBERT the raw Body cell.

Model outputs are appended as `{Model}_{channel}_bd` columns (76 for all 12: 70
for the 11 + 6 for Prosus), Prosus last, so the 11 existing column blocks keep
their positions. Output file is the input filename with `.tsv` replaced by
`_12EncBodyScored.tsv` (`--out-suffix`).

`--replace-existing` instead OVERWRITES the `_bd` columns a file already has,
leaving the header byte-identical — the way to re-score a column-pruned research
file (whose columns may carry a pandas `.N` duplicate suffix, e.g.
`FinMarBa_ss_bd.1`) without changing its shape. Channels with no matching column
are skipped, not appended.

`Prosus` is deliberately NOT added to HeadScore11encoders.MODELS: the head path
and its 70 `_hd` columns are untouched by this script.

Usage:
    /home/tom/venv/bin/python BodyScore12encoders.py \
        --input ../../price_stats_loader/outputs/PRstats_..._11EncScored.tsv
    # quick test on a subset:
    /home/tom/venv/bin/python BodyScore12encoders.py --input FILE \
        --limit 5 --models DistilRo,Prosus
    # re-score an already-scored file in place (header unchanged):
    /home/tom/venv/bin/python BodyScore12encoders.py --input FILE \
        --cap-words 200 --replace-existing --out-suffix _prosusReScore.tsv
"""

import argparse
import csv
import re
import sys
import time
from pathlib import Path

import body_prep
from HeadScore11encoders import MODELS, score_table

DEFAULT_OUT_SUFFIX = "_12EncBodyScored.tsv"

# tools/Encoder_Models -> scripts (same derivation as FinBERT_TSV_scorer.py)
SCRIPTS_DIR = Path(__file__).resolve().parents[2]

# The 12th model. `path` is what makes score_table() import it from outside this
# directory; the key order follows the unified convention (neg/neu/pos/ss/lab/ms),
# NOT the pos/neg/neu order of the older FinBERT_TSV_scorer.py header.
# analyze_headline() also returns a "headline" key — unused here, so no drift.
PROSUS = {"script": "FinBERT-headliner.py",
          "path": SCRIPTS_DIR / "FinBERT",
          "fn": "analyze_headline",
          "keys": ["neg", "neu", "pos", "ss", "lab", "ms"]}

MODELS12 = {**MODELS, "Prosus": PROSUS}


def find_targets(header, model_names, registry=MODELS12):
    """Map each model's keys to the index of an ALREADY-PRESENT `_bd` column.

    Used by --replace-existing. A target is a header entry equal to
    `{Model}_{key}_bd`, or one carrying a pandas duplicate-name suffix
    (`FinMarBa_ss_bd.1` — what a file that has been through pandas looks like).
    Channels with no column are reported and skipped, never appended: a
    column-pruned research file keeps exactly the columns it already has.

    Returns {model: [(offset_in_block, header_idx), ...]}.
    """
    targets = {}
    for name in model_names:
        found, missing = [], []
        for off, key in enumerate(registry[name]["keys"]):
            base = f"{name}_{key}_bd"
            pat = re.compile(re.escape(base) + r"(\.\d+)?$")
            hits = [i for i, col in enumerate(header) if pat.fullmatch(col)]
            if not hits:
                missing.append(base)
                continue
            if len(hits) > 1:
                raise SystemExit(f"ambiguous target for {base}: "
                                 f"{[header[i] for i in hits]}")
            found.append((off, hits[0]))
        if not found:
            raise SystemExit(f"--replace-existing: no existing _bd column for "
                             f"model '{name}' — nothing to replace")
        print(f"  {name:11s} replace {len(found):2d}"
              + (f"   skip (absent): {', '.join(missing)}" if missing else ""))
        targets[name] = found
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score every Body (cleaned, first 200 words) of a TSV with "
                    "the 11 encoder headliners plus ProsusAI FinBERT.")
    parser.add_argument("--input", required=True, help="input .tsv path")
    parser.add_argument("--models", metavar="NAME[,NAME...]",
                        help=f"subset of models (default: all). Choices: {', '.join(MODELS12)}")
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
    parser.add_argument("--replace-existing", action="store_true",
                        help="overwrite the values of the `_bd` columns already "
                             "present (matching a pandas `.N` suffix too) instead "
                             "of appending; the header is left untouched and "
                             "channels with no column are skipped, not added")
    parser.add_argument("--out-suffix", default=DEFAULT_OUT_SUFFIX,
                        help=f"output filename suffix (default: {DEFAULT_OUT_SUFFIX})")
    args = parser.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.is_file():
        parser.error(f"input not found: {in_path}")
    if not in_path.name.endswith(".tsv"):
        parser.error("input must be a .tsv file")
    suffix = args.out_suffix if args.out_suffix.endswith(".tsv") else args.out_suffix + ".tsv"
    out_path = in_path.with_name(in_path.name[:-len(".tsv")] + suffix)
    if out_path == in_path:
        parser.error("output would overwrite the input")

    if args.models:
        model_names = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = [m for m in model_names if m not in MODELS12]
        if unknown:
            parser.error(f"unknown model(s): {', '.join(unknown)}. Choices: {', '.join(MODELS12)}")
    else:
        model_names = list(MODELS12)

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

    # score_table() always APPENDS its blocks past the scratch column; in replace
    # mode they are copied into the existing columns afterwards and the tail is
    # dropped, so the scoring loop itself stays the one in HeadScore11encoders.
    targets = None
    if args.replace_existing:
        print("Replacing existing _bd columns (header unchanged):")
        targets = find_targets(header, model_names)
    else:
        for name in model_names:
            # `_bd` = the scored text was the BODY (HeadScore11encoders emits `_hd`).
            header.extend(f"{name}_{k}_bd" for k in MODELS12[name]["keys"])

    t0 = time.perf_counter()
    score_table(rows, prep_idx, model_names, MODELS12)
    print(f"Scoring done in {time.perf_counter() - t0:.1f} s")

    if targets is not None:
        # block layout: row[prep_idx] is the scratch prep text, then one block per
        # model in model_names order.
        starts, pos = {}, prep_idx + 1
        for name in model_names:
            starts[name] = pos
            pos += len(MODELS12[name]["keys"])
        n_cols = len(header)
        for row in rows:
            for name in model_names:
                base = starts[name]
                for off, dst in targets[name]:
                    row[dst] = row[base + off]
            del row[prep_idx:]          # scratch column + every appended block
            assert len(row) == n_cols
    else:
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
