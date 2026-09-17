"""Backfill the kw15 body/headline probabilities and the Prosus_*_hd headline columns
onto an older enriched TSV, so it lines up with the post-2026-07-24 corpus.

Why this exists: Orchestrator5.2 only started writing the kw15 probabilities on
2026-07-29, and the ProsusAI-FinBERT columns are an offline rescore that never
touched the older files.  Rows captured before then are missing both, which makes
them unusable in an AND-rule search alongside the newer rows.

⚠ 2026-08-09: the emitted column names changed to the unified
`{Model}_{channel}_{hd|bd}` convention — `FinBERT_positive` -> `Prosus_pos_hd`,
`kw15_body_proba` -> `kw15_proba_bd`, and so on. Values are unchanged. A TSV
backfilled before that date carries the old names; migrate it with
tools/Encoder_Models/rename_encoder_cols.py rather than re-running this.

Both scorers are the SAME artefacts the live orchestrator uses:
  * kw15  — the two frozen joblib pipelines named in Orchestrator5.2.py:109-112,
            driven through the verbatim `_kw15_prep` of Orchestrator5.2.py:447-453.
  * FinBERT — FinBERT/FinBERT-headliner.py's analyze_headline, the module
            Orchestrator5.2.py:32-39 imports.
so a backfilled cell equals what the live pipeline would have written.
"""

import argparse
import importlib.util as ilu
import logging
import os
import sys
import time
from pathlib import Path

import joblib
import pandas as pd

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("kw15_finbert_backfill")

SCRIPTS_DIR = Path(__file__).resolve().parent.parent

# ── kw15: the corrected-label ge15 pair (Orchestrator5.2.py:105-121) ──────────
KW15_DIR = (SCRIPTS_DIR / "tools" / "keyword" /
            "model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN")
KW15_BODY_PIPELINE = KW15_DIR / "classify_fromPRhigh_ge15" / "tfidf_fitted_pipeline.joblib"
KW15_HEAD_PIPELINE = KW15_DIR / "classify_fromPRhigh_ge15_headline" / "tfidf_fitted_pipeline.joblib"
# ⚠ NOT optional — the models only ever saw the first 400 words of a body, and
# dropping the cap flips fire decisions.  Headlines are never capped.
KW15_BODY_MAX_WORDS = 400

# Unified {Model}_{channel}_{hd|bd} column names (2026-08-09) — the same names
# Orchestrator5.2 and tools/Encoder_Models/ emit. `Prosus` = ProsusAI/finbert.
FINBERT_COLS = ["Prosus_neg_hd", "Prosus_neu_hd", "Prosus_pos_hd",
                "Prosus_ss_hd", "Prosus_lab_hd", "Prosus_ms_hd"]
KW15_COLS = ["kw15_proba_bd", "kw15_proba_hd"]


def _load_hyphenated(name: str, path: Path):
    """importlib-load a module whose filename has a hyphen (so `import` can't)."""
    spec = ilu.spec_from_file_location(name, str(path))
    mod = ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _kw15_prep(text, max_words, kw2, strip_wire_boilerplate) -> str:
    """The EXACT training text prep, in the order keyword_scorer-2.score_texts uses
    it: strip HTML, strip wire boilerplate, then optionally cap.  Verbatim from
    Orchestrator5.2.py:447-453."""
    out = strip_wire_boilerplate(kw2._strip_html(text or ""))
    return " ".join(out.split()[:max_words]) if max_words else out


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  kw15_finbert_backfill_addOn.py \\\n"
            "    --input     outputs/04_PRstats_....tsv \\\n"
            "    --id-filter outputs/02_PRstats_....tsv \\\n"
            "    --output    outputs/04sub_....tsv"
        ),
    )
    parser.add_argument("--input", required=True, help="TSV to backfill.")
    parser.add_argument("--output", required=True, help="Path for the backfilled TSV.")
    parser.add_argument(
        "--id-filter", dest="id_filter", default=None,
        help="Optional TSV whose ID column restricts which input rows are kept "
             "(row order follows the input, not the filter).",
    )
    parser.add_argument("--id-col", dest="id_col", default="ID")
    parser.add_argument("--headline-col", dest="headline_col", default="Headline")
    parser.add_argument("--body-col", dest="body_col", default="Body")
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Recompute columns that already exist instead of refusing.",
    )
    args = parser.parse_args()

    in_path = Path(args.input).resolve()
    out_path = Path(args.output).resolve()
    if not in_path.is_file():
        sys.exit(f"Error: input TSV not found: {in_path}")

    df = pd.read_csv(in_path, sep="\t", dtype=str)
    logger.info("Loaded %d rows x %d cols from %s", len(df), len(df.columns), in_path.name)

    for col in (args.headline_col, args.body_col, args.id_col):
        if col not in df.columns:
            sys.exit(f"Error: input lacks a '{col}' column.")

    if args.id_filter:
        f_path = Path(args.id_filter).resolve()
        if not f_path.is_file():
            sys.exit(f"Error: --id-filter TSV not found: {f_path}")
        keep = set(pd.read_csv(f_path, sep="\t", dtype=str, usecols=[args.id_col])[args.id_col])
        before = len(df)
        df = df[df[args.id_col].isin(keep)].reset_index(drop=True)
        missing = keep - set(df[args.id_col])
        logger.info("--id-filter %s: %d of %d rows kept (%d filter IDs absent from input)",
                    f_path.name, len(df), before, len(missing))
        if missing:
            logger.warning("filter IDs not present in input: %s",
                           sorted(missing)[:10])

    clashes = [c for c in KW15_COLS + FINBERT_COLS if c in df.columns]
    if clashes and not args.overwrite:
        sys.exit(f"Error: input already has {clashes}; pass --overwrite to recompute.")

    # ── kw15 ─────────────────────────────────────────────────────────────────
    for p in (KW15_BODY_PIPELINE, KW15_HEAD_PIPELINE):
        if not p.is_file():
            sys.exit(f"Error: kw15 pipeline not found: {p}")
    kw2 = _load_hyphenated(
        "keyword_scorer_2", SCRIPTS_DIR / "tools" / "keyword" / "keyword_scorer-2.py")
    from keyword_finder import strip_wire_boilerplate  # kw2 put its dir on sys.path

    body_pipe = joblib.load(KW15_BODY_PIPELINE)
    head_pipe = joblib.load(KW15_HEAD_PIPELINE)
    logger.info("kw15 pipelines loaded from %s", KW15_DIR.name)

    bodies = [_kw15_prep(t, KW15_BODY_MAX_WORDS, kw2, strip_wire_boilerplate)
              for t in df[args.body_col].fillna("")]
    heads = [_kw15_prep(t, None, kw2, strip_wire_boilerplate)
             for t in df[args.headline_col].fillna("")]

    t0 = time.monotonic()
    # 6 decimals matches the live orchestrator and the backtest TSVs.
    df["kw15_proba_bd"] = [round(float(p), 6) for p in body_pipe.predict_proba(bodies)[:, 1]]
    df["kw15_proba_hd"] = [round(float(p), 6) for p in head_pipe.predict_proba(heads)[:, 1]]
    logger.info("kw15 scored %d rows in %.1fs", len(df), time.monotonic() - t0)

    # ── FinBERT ──────────────────────────────────────────────────────────────
    fb = _load_hyphenated("finbert_headliner",
                          SCRIPTS_DIR / "FinBERT" / "FinBERT-headliner.py")
    fb.load_model()

    neg, neu, pos, score, label, ms = [], [], [], [], [], []
    t0 = time.monotonic()
    for headline in df[args.headline_col].fillna(""):
        t1 = time.monotonic()
        r = fb.analyze_headline(headline)
        ms.append(round((time.monotonic() - t1) * 1000.0, 1))
        neg.append(r["neg"])
        neu.append(r["neu"])
        pos.append(r["pos"])
        score.append(r["ss"])
        label.append(r["lab"])
    df["Prosus_neg_hd"] = neg
    df["Prosus_neu_hd"] = neu
    df["Prosus_pos_hd"] = pos
    df["Prosus_ss_hd"] = score
    df["Prosus_lab_hd"] = label
    # Wall time measured HERE, not r["ms"]: this loop's timing has always included
    # the call overhead, and changing it would silently shift the column's meaning.
    df["Prosus_ms_hd"] = ms
    logger.info("FinBERT scored %d rows in %.1fs", len(df), time.monotonic() - t0)

    tmp_path = out_path.with_name(out_path.name + ".tmp")
    df.to_csv(tmp_path, sep="\t", index=False)
    os.replace(tmp_path, out_path)
    logger.info("Wrote %d rows x %d cols → %s", len(df), len(df.columns), out_path)


if __name__ == "__main__":
    main()
