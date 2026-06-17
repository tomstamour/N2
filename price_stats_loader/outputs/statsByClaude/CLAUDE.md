# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This directory holds a single self-contained statistics task: rank the 6
FinBERT-derived sentiment columns (`sentiment_score`, `neutral_filter`,
`confidence_weighted`, `net_score`, `top_k`, `positional`) on how well each —
and each 2- and 3-column combination — predicts a binary "big mover" event
defined as `DailyHigh(%) >= threshold` (default 20). The script
`analyze_dailyhigh_predictors.py` is the entire analysis pipeline; everything
else in this directory is either its input TSV or one of its outputs.

## Run

Default run (threshold 20%, 5-fold CV, no plots):

```
python analyze_dailyhigh_predictors.py
```

Custom TSV, threshold, and terminal plots:

```
python analyze_dailyhigh_predictors.py <tsv> --threshold 30 --folds 5 --plots
```

Dependencies: `numpy`, `pandas`, `scipy`. `plotext` is only needed when
`--plots` is passed. No test suite, no linter config, no build step.

## Inputs / outputs

- **Input TSV** (default
  `fconcatenated_enriched_FinBERT_filtered-50float-12high.tsv`): tab-separated,
  read as `dtype=str` then coerced to numeric. Must contain the target column
  `DailyHigh(%)` and the 6 sentiment columns above. Rows are dropped only if
  the target is NaN or *all* 6 sentiment columns are NaN.
- **Outputs** (written to the TSV's parent directory):
  - `dailyhigh_predictor_ranking.md` — full report: singles, combos, combined
    leaderboard, Spearman collinearity matrix, threshold sweep, conclusion.
  - With `--plots`: `roc_top_singles.plotext`, `strip_<col>.plotext`,
    `pair_<c0>_<c1>.plotext`. These are plotext text files (terminal-readable),
    not images.

## Non-obvious design decisions

- The target is **binarized** at `--threshold` rather than fit as a regression.
  Raw `DailyHigh(%)` is heavily right-skewed (max ~750%), so AUC on the binary
  target is the primary metric and is what the leaderboard sorts on.
- AUC is computed via the **Mann-Whitney rank formula** (`auc_score`) — handles
  ties; no sklearn anywhere in the pipeline.
- Logistic regression is hand-rolled with `scipy.optimize.minimize` plus a
  small L2 penalty (`l2=1e-3`). Features are standardized **per fold** using
  the train fold's mean/std to avoid leakage. The coefficients shown in the
  report come from a separate fit on the full standardized data and exist for
  interpretation only — they are not used to score the OOF predictions.
- Cross-validation reports **pooled out-of-fold AUC** (one AUC over all OOF
  predictions concatenated), not mean-of-fold-AUCs.
- Combos enumerate sizes 2 and 3 only; size-1 is handled by the singles table.
- The 6 sentiment columns are derivatives of the same FinBERT output and are
  highly collinear (e.g. `neutral_filter`–`confidence_weighted` Spearman ≈
  0.98). Combos rarely beat the best single by more than ~0.02 AUC — don't
  expect adding columns to "fix" weak signal.
- `save_plotext` both prints the plot to the terminal **and** writes the
  plotext text to disk. There is no PNG output from this script.

## Sanity check after edits

Re-run the default command and confirm the regenerated
`dailyhigh_predictor_ranking.md` still shows `neutral_filter` single-column
AUC near ~0.68 at threshold 20 on the existing input TSV.
