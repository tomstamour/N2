#!/usr/bin/env python3
"""
keywords_scorer_backtester.py

Apply keyword_scorer.py's PR-text scoring to a historical table (back-testing
= scoring past PR tables so the score columns can be studied against the
recorded outcomes).  Score-only: it appends columns and writes
<input stem>_keywordScored.tsv -- no metrics report (use
keyword_scorer.py --evaluate for the method league table).

Body filtering: the input 'body' column may carry URLs, newswire vendor
tokens and HTML tags/entities (the dev file has all three).  Nothing needs
to be pre-filtered here -- KeywordScorer.score_texts() normalizes the text
internally exactly like training did (_strip_html + strip_wire_boilerplate)
before tokenizing.  Boilerplate PRs (lawfirm/compliance/earnings/conference)
are scored like every other row and flagged in the boilerplate_category
column; gate on it downstream.

Appended columns: boilerplate_category, kw_add_first, kw_add_log, kw_add_lin,
kw_mult_nb, kw_mult_nb_log, model_proba, their *_pctl percentiles (vs the
scorer_reference.json corpus), n_kw_hits (+ kw_top_terms with --explain).

Usage (run with the project venv):
    /home/tom/venv/bin/python keywords_scorer_backtester.py
    /home/tom/venv/bin/python keywords_scorer_backtester.py \\
        --input some_table.tsv --text-col Body --explain
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from keyword_scorer import (COEF_COLUMNS, DEFAULT_KEYWORDS, DEFAULT_PIPELINE,
                            DEFAULT_REFERENCE, KeywordScorer, run_score)

DEFAULT_INPUT = ("/home/tom/Documents/ibkr_scripts/N2/scripts/price_stats_loader/"
                 "outputs/PR-stats-2026-05-03to06-19-filtered-50float-12high_"
                 "RBscore_body_pricefilled_clean.tsv")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", default=DEFAULT_INPUT,
                    help="PR table (TSV) to score")
    ap.add_argument("--out", default=None,
                    help="output path (default: <input stem>_keywordScored.tsv)")
    ap.add_argument("--text-col", default="body",
                    help="text column, matched case-insensitively "
                         "(so Orchestrator5's 'Body' works too)")
    ap.add_argument("--headline-col", default="Headline",
                    help="used only for boilerplate_category detection")
    ap.add_argument("--id-col", default="ID")
    ap.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS,
                    help="keyword/power CSV(s) (keyword_finder tables, "
                         "consensus sheets, or any CSV with term+power columns)")
    ap.add_argument("--coef-col", default=None,
                    help=f"power column (default: first of {COEF_COLUMNS})")
    ap.add_argument("--pipeline", default=DEFAULT_PIPELINE,
                    help="fitted joblib pipeline for the model_proba column")
    ap.add_argument("--no-pipeline", action="store_true",
                    help="skip the joblib model (pure keyword-table scoring)")
    ap.add_argument("--reference", default=DEFAULT_REFERENCE,
                    help="scorer_reference.json for the *_pctl columns")
    ap.add_argument("--no-percentile", action="store_true")
    ap.add_argument("--prevalence", type=float, default=None,
                    help="P(positive) anchor for kw_mult_nb* "
                         "(default: from model_performance_summary.csv)")
    ap.add_argument("--min-doc-freq", type=int, default=0,
                    help="drop keyword-table terms below this document_frequency")
    ap.add_argument("--explain", action="store_true",
                    help="add kw_top_terms column (top 5 signed contributors)")
    args = ap.parse_args()

    inp = Path(args.input)
    if not inp.exists():
        sys.exit(f"ERROR: input not found: {inp}")
    if args.out is None:
        args.out = str(inp.with_name(inp.stem + "_keywordScored.tsv"))
    args.words_detail = "false"          # run_score expects the attribute

    scorer = KeywordScorer.load(
        keywords=args.keywords, coef_col=args.coef_col,
        pipeline_path=None if args.no_pipeline else args.pipeline,
        reference_path=None if args.no_percentile else args.reference,
        prevalence=args.prevalence, min_doc_freq=args.min_doc_freq)

    run_score(scorer, args)


if __name__ == "__main__":
    main()
