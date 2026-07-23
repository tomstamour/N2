# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

Keyword-model toolkit for small-cap press releases (PRs): trains TF-IDF/logistic
models that rank unigrams/bigrams by association with a post-PR price-move target,
and scores new PRs on a 0–1 scale live. Part of the larger N2 IBKR trading-scripts
tree; `../../orchestrator5/` loads the scorer in-process for live trading decisions.

## Interpreter

Always run with the venv interpreter, never system python3:
`/home/tom/venv/bin/python`

## Scripts

- **keyword_finder.py** — trainer. Corpus TSV in → model dir out
  (`tfidf_`/`binary_fitted_pipeline.joblib`, `*_keyword_impact_*.csv`,
  `model_performance_summary.csv`). Use `--task classification` (binarized
  target); regression on the raw % target is a dead end, don't revisit it.
- **keyword_scorer-2.py** — **the LIVE scorer** (CLI + importable module).
  Defaults frozen to the canonical ge15 fromPR model: prevalence pinned at
  exactly 0.185, frozen 2-feature combo `model_proba_PLUS_kw_mult_nb`
  auto-loaded (live trigger: combo ≥ 0.6). Only scorer entry point for live work.
- **keyword_scorer.py** — obsolete predecessor; still defaults to the old ge20
  DailyHigh model. Kept because keywords_scorer_backtester.py imports its engine.
  Never use for live scoring.
- **keywords_scorer_backtester.py** — batch-scores a historical TSV
  (appends score columns, writes `*_keywordScored.tsv`; no metrics report).

The `-2` filename means `import keyword_scorer-2` is a syntax error; load as a
module via `importlib.import_module("keyword_scorer-2")` (verified to work).

## Common commands

```bash
# Score a TSV (appends score columns, writes <input>_scored.tsv)
/home/tom/venv/bin/python keyword_scorer-2.py --input file.tsv --text-col Body

# Score ONE PR from stdin (prints key\tvalue lines)
echo "body text" | /home/tom/venv/bin/python keyword_scorer-2.py --stdin --headline-text "Headline"

# Benchmark all scoring methods + (re)build the percentile reference
# --no-strip-boilerplate is MANDATORY on the ge15 default model (see gotchas)
/home/tom/venv/bin/python keyword_scorer-2.py --evaluate --no-strip-boilerplate

# Train (canonical recipe — read Training_guide_for_keyword_finder.md first)
/home/tom/venv/bin/python keyword_finder.py \
  --input <CORPUS.tsv> \
  --outdir model_trained_on_<CORPUS-STAMP>/classify_fromPRhigh_ge15_excStr2 \
  --task classification --text-col body --target-col "fromPR_High(%)" \
  --binarize-threshold 15 --variants tfidf binary
```

There is no test suite; correctness is verified by byte-exact score reproduction
(score ~20 rows of the scored reference TSV via `score_text()`, require
max |diff| ≤ 1e-5 vs stored columns).

## Canonical model & frozen-artifact rules

- Live model: `model_trained_on_PR-stats-2026-05-03to06-19/SELECTED-MODEL_classify_fromPRhigh_ge15_excStr2/`
  (headline twin `..._headline/`). The `SELECTED-MODEL_` prefix marks the promoted
  live model and is applied only at promotion time — never name a fresh run that.
- Model dirs are **frozen** once anything downstream references them: never re-run
  keyword_finder into an existing outdir. New corpus ⇒ new dataset-stamped parent
  (`model_trained_on_<corpus>/`, already gitignored). Old dirs stay on disk — they
  are the reproduction record for past backtests.
- Retraining/promotion is a checklist (dedup, threshold table, scorer_reference
  rebuild, prevalence pinning, combo refit, walk-forward gate vs incumbent):
  follow `Training_guide_for_keyword_finder.md`. Do not improvise a retrain.
- Switching the live model = editing `CANON_DIR`, `DEFAULT_PREVALENCE`,
  `DEFAULT_COMBO_MODEL` in keyword_scorer-2.py, then re-verifying byte-exactness.

## Gotchas (each cost a debugging round)

- Text-column case matters and is not matched case-insensitively: training corpus
  uses `body`, live Orchestrator5 files use `Body`.
- `--evaluate` on the ge15 model without `--no-strip-boilerplate` shifts the
  seed-42 split, leaks train rows into test, and produces a fake ~0.92 AUC.
  Any too-good number = suspect a split mismatch first.
- In-sample scores are wildly inflated (model_proba 0.907 in-sample → 0.676
  walk-forward). Report CV mean ± std, never the single test split (±0.05 noise
  at this corpus size); only walk-forward on held-out weeks is honest.
- `--strip-wire-boilerplate` is default-ON — leave it. `--strip-boilerplate`
  (row-dropping) is deliberately NOT used for the canonical model; the
  `boilerplate_category` column is a live veto flag, not a training filter.
- The frozen combo weights are ge15-specific. The scorer's interlock disables
  combo when `--keywords`/`--pipeline` are overridden without an explicit
  `--combo-model` — that is intentional, don't "fix" it.
- Scores are ranking scores, not calibrated probabilities
  (class_weight='balanced').
