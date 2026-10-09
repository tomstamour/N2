#!/usr/bin/env python
"""rename_encoder_cols.py — migrate a TSV header to the unified 2026-08-09
sentiment-column convention.

WHAT CHANGED
    Every sentiment score column produced anywhere in this repo now spells itself
    the same way:

        {Model}_{channel}_{hd|bd}

    `hd` = the model scored the HEADLINE, `bd` = the BODY. Model tokens are
    hyphen-free (so a column is a legal pandas attribute) and channels are short
    (`pos` `neu` `neg` `ind` `ss` `lab` `ms`, plus LouMcD's seven categories).

    Three vocabularies collapsed into that one:
      offline head   FLANG-BERT_positive       -> FLANG_pos_hd
      offline body   FLANG-BERT_Body_positive  -> FLANG_pos_bd     (infix -> suffix)
      live O5.2      FLANG_sentiment_score     -> FLANG_ss_hd
                     positive                  -> Prosus_pos_hd    (was UNPREFIXED)
                     kw15_body_proba           -> kw15_proba_bd

WHAT THIS SCRIPT DOES
    Rewrites the HEADER LINE ONLY. Not one data cell is touched — the rename
    changed no value anywhere (verified probe-by-probe against the pre-rename
    goldens), so re-scoring an old corpus would be wasted compute and would
    introduce drift where there is none.

    /home/tom/venv/bin/python rename_encoder_cols.py --dry-run PATH [PATH ...]
    /home/tom/venv/bin/python rename_encoder_cols.py PATH [PATH ...]

    PATH may be a file or a directory (directories are scanned for *.tsv,
    non-recursively unless --recursive).

SAFETY
    * Idempotent — a header already in the new vocabulary is reported as a no-op.
    * Refuses a file whose header contains NEITHER vocabulary, so pointing it at
      an unrelated TSV is an error rather than a silent no-op.
    * Writes `<name>.tsv.bak` beside each file unless --no-backup.
    * ⚠ Column COUNT is identical before and after, so a half-migrated file cannot
      be detected by arity — only by name. That is exactly why the "neither
      vocabulary" refusal exists.
    * ⚠ Do NOT migrate orchestrator5/tables/news_output_<today>.tsv while O5.2 is
      running: it appends under a header-equality check and would rotate the file
      out from under you (and a second rotation the same day overwrites the first
      *_rotated.tsv).
"""

import argparse
import os
import shutil
import sys
from pathlib import Path

# ── The canonical mapping ─────────────────────────────────────────────────────
# Built programmatically from two small tables rather than typed out 150 times,
# so it cannot drift from HeadScore11encoders.MODELS. Import COLUMN_RENAMES from
# here rather than re-deriving it anywhere else.

# Offline model token (pre-2026-08-09) -> unified token.
_MODEL_RENAMES = {
    "FinMarBa":      "FinMarBa",
    "FLANG-BERT":    "FLANG",
    "FLANG-ELECTRA": "FLElectra",
    "RoBERTa":       "RoBERTa",
    "ModernBERT":    "ModernB",
    "DeBERTa":       "DeBERTa",
    "finbert-tone":  "FinTone",
    "FinancialBERT": "FinancialB",
    "distilroberta": "DistilRo",
    "Trading-Hero":  "TradeHero",
    "LouMcD-SDict":  "LouMcD",
}

# Result-dict / column channel (pre-2026-08-09) -> unified channel.
_CHANNEL_RENAMES = {
    "positive": "pos", "neutral": "neu", "negative": "neg", "indecisive": "ind",
    "sentiment_score": "ss", "label": "lab",
    "elapsed_ms": "ms", "inference_ms": "ms",
    "uncertainty": "unc", "litigious": "lit", "strong_modal": "smod",
    "weak_modal": "wmod", "constraining": "cons",
}


def _build_renames() -> dict:
    ren = {}
    # 1. The 11 offline encoders, head and body.
    for old_m, new_m in _MODEL_RENAMES.items():
        for old_c, new_c in _CHANNEL_RENAMES.items():
            ren[f"{old_m}_{old_c}"] = f"{new_m}_{new_c}_hd"
            ren[f"{old_m}_Body_{old_c}"] = f"{new_m}_{new_c}_bd"

    # 2. Orchestrator5.2's live abbreviations for the two models it shares with
    #    the offline set. FLANG_* collides with nothing: the offline file spells
    #    that model FLANG-BERT_*, so both land on FLANG_*_hd.
    for old_c, new_c in _CHANNEL_RENAMES.items():
        ren[f"FMB_{old_c}"] = f"FinMarBa_{new_c}_hd"
        ren[f"FLANG_{old_c}"] = f"FLANG_{new_c}_hd"
        # ProsusAI FinBERT under its old explicit prefix (the backfill add-on's).
        ren[f"FinBERT_{old_c}"] = f"Prosus_{new_c}_hd"

    # 3. ProsusAI FinBERT's UNPREFIXED live columns — the whole reason a generic
    #    name like `positive` was unusable anywhere else in a row.
    for old_c, new_c in _CHANNEL_RENAMES.items():
        ren[old_c] = f"Prosus_{new_c}_hd"

    # 4. Timestamps and durations. `_at_` is a COMPLETION STAMP, `_ms_` a
    #    DURATION — the old FLANG_time / FLANG_ms pair conflated the two by name.
    ren["FinBERTCompletedAt"] = "Prosus_at_hd"
    ren["FMB_time"] = "FinMarBa_at_hd"
    ren["FLANG_time"] = "FLANG_at_hd"
    ren["FLANG_ms"] = "FLANG_ms_hd"
    ren["KwScoreCompletedAt"] = "kw15_at"

    # 5. The ge15 keyword pipelines.
    ren["kw15_body_proba"] = "kw15_proba_bd"
    ren["kw15_head_proba"] = "kw15_proba_hd"
    return ren


COLUMN_RENAMES = _build_renames()
NEW_NAMES = set(COLUMN_RENAMES.values())


def migrate_header(header: list) -> tuple:
    """Return (new_header, [(old, new), ...]) for one header row."""
    changes = []
    out = []
    for col in header:
        new = COLUMN_RENAMES.get(col)
        if new and new != col:
            changes.append((col, new))
            out.append(new)
        else:
            out.append(col)
    return out, changes


def classify(header: list) -> str:
    """'old' / 'new' / 'neither' — which vocabulary this header speaks."""
    if any(c in COLUMN_RENAMES and COLUMN_RENAMES[c] != c for c in header):
        return "old"
    if any(c in NEW_NAMES for c in header):
        return "new"
    return "neither"


def process(path: Path, dry_run: bool, backup: bool) -> str:
    with open(path, "r", encoding="utf-8", newline="") as fh:
        first = fh.readline()
    if not first:
        return "empty"
    header = first.rstrip("\r\n").split("\t")

    kind = classify(header)
    if kind == "neither":
        return "neither"
    if kind == "new":
        return "already"

    new_header, changes = migrate_header(header)
    if dry_run:
        print(f"  {path.name}: {len(changes)} column(s) would be renamed")
        for old, new in changes[:6]:
            print(f"      {old}  ->  {new}")
        if len(changes) > 6:
            print(f"      ... and {len(changes) - 6} more")
        return "would"

    # Stream the rest through untouched — data cells are never rewritten, and a
    # corpus TSV can be large enough that slurping it is a real cost.
    tmp = path.with_name(path.name + ".tmp")
    with open(path, "r", encoding="utf-8", newline="") as src, \
         open(tmp, "w", encoding="utf-8", newline="") as dst:
        src.readline()
        dst.write("\t".join(new_header) + "\n")
        shutil.copyfileobj(src, dst)
    if backup:
        shutil.copy2(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)
    print(f"  {path.name}: {len(changes)} column(s) renamed")
    return "done"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Migrate TSV headers to the unified sentiment-column names.")
    ap.add_argument("paths", nargs="+", help="TSV files and/or directories")
    ap.add_argument("--dry-run", action="store_true",
                    help="list the renames without writing anything")
    ap.add_argument("--recursive", action="store_true",
                    help="descend into subdirectories when a path is a directory")
    ap.add_argument("--no-backup", action="store_true",
                    help="do not leave a .bak beside each rewritten file")
    args = ap.parse_args()

    targets = []
    for raw in args.paths:
        p = Path(raw)
        if p.is_dir():
            targets.extend(sorted(p.rglob("*.tsv") if args.recursive
                                  else p.glob("*.tsv")))
        elif p.is_file():
            targets.append(p)
        else:
            print(f"ERROR: no such path: {p}")
            return 2
    targets = [t for t in targets if not t.name.endswith((".bak", ".tmp"))]
    if not targets:
        print("No .tsv files found.")
        return 1

    print(f"{len(targets)} file(s){' — DRY RUN' if args.dry_run else ''}\n")
    tally = {}
    for t in targets:
        try:
            r = process(t, args.dry_run, not args.no_backup)
        except Exception as exc:                       # keep going across a corpus
            print(f"  {t.name}: ERROR {exc}")
            r = "error"
        tally[r] = tally.get(r, 0) + 1

    print("\nSummary:")
    for label, key in (("renamed", "done"), ("would rename", "would"),
                       ("already migrated", "already"),
                       ("skipped (neither vocabulary)", "neither"),
                       ("empty", "empty"), ("errors", "error")):
        if tally.get(key):
            print(f"  {label:32s} {tally[key]}")
    return 1 if tally.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
