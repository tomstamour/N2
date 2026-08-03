# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

Rule-based (regex) scorer for **Earnings_Results press releases**: extracts
earnings metrics from Headline/Body sentence-by-sentence, turns them into an
interpretable additive `EarningScore` aimed at predicting large
`fromPR_High(%)` moves (calibrated on >=30%). Research tool in the N2 tree —
**not wired into the live trading chain** (orchestrator/clerk do not load it).
Findings live in `Earnings_text_structures_report.md`; every number there is
reproducible by `evaluate_earning_scorer.py`.

## Interpreter

Always run with the venv interpreter, never system python3:
`/home/tom/venv/bin/python` (pandas/sklearn live there).

## Scripts & artifacts

- **earning_scorer.py** — extractor + scorer. Extraction families A–I
  (explicit growth, dollar-pair implicit growth, record claims, guidance,
  profit flip, margin, negative structures, scheduling-only, size-relative);
  each is a commented block in the source. Module API:
  `extract_metrics`, `score_metrics`, `score_text`, `score_dataframe`.
  Auto-loads `earn_score_params.json` next to the script; falls back to
  `BUILTIN_PARAMS` (provisional, pre-calibration) when absent.
- **evaluate_earning_scorer.py** — calibrates + honestly evaluates. Grid-search
  over rule thresholds on the train block ONLY, freezes the winner into
  `earn_score_params.json`, then prints the full report (score buckets,
  per-family univariate stats, 11-encoder AUC comparison, logistic combo) and
  writes the scored TSV + `earn_qa_dump.tsv`.
- **earn_score_params.json** — calibrated thresholds (2026-08-01). Written
  ONLY by the evaluator; don't hand-edit unless deliberately experimenting.
  NOTE: gitignored (the repo whitelist tracks no .json), so it exists only on
  this disk — on a fresh clone the scorer silently falls back to the
  provisional `BUILTIN_PARAMS` until the evaluator is re-run.
- **PRstats_..._EarningResults*.tsv / earn_qa_dump.tsv** — input corpus
  (531 Earnings_Results PRs sliced from the PR-stats master), scored output,
  QA dump. Data outputs — gitignored per root rules, never force-add.

## Common commands

```bash
# Batch-score a TSV (needs Headline/Body; Float/LastDailyClose/MktCap optional
# for the size-relative family) -> <input>_earnScored.tsv with earn_*/earnH_*
# feature columns + EarningScore + EarnBreakdown appended
/home/tom/venv/bin/python earning_scorer.py --input FILE.tsv

# Score ONE PR from stdin -> JSON on stdout (CLI mirrors keyword_scorer-2.py)
echo "body text" | /home/tom/venv/bin/python earning_scorer.py --stdin --headline-text "Headline"

# Recalibrate + full evaluation report
# OVERWRITES earn_score_params.json, the *_earnScored.tsv and earn_qa_dump.tsv
/home/tom/venv/bin/python evaluate_earning_scorer.py
```

No test suite. Verification = run the evaluator and diff its printed tables
against `Earnings_text_structures_report.md`; extraction QA = spot-check
`earn_qa_dump.tsv` (every hit carries its source sentence).

## Calibration protocol (spans both scripts — don't break it)

- Honest split on `ArrivalDate` at **2026-06-20** (repo-wide convention):
  thresholds are tuned on the train block only; the test block is never touched
  during calibration. Target >=30%, with >=15% as a stability check.
- Rows are filtered to `fromPR_status == "ok"` before any evaluation.
- The >=30% base rate is regime-shifted (May 3.7% → June/July 14–20%):
  full-sample lifts are flattering; **test-block numbers are the honest ones**.
  EarningScore is a ranking score, not a calibrated probability.
- Size denominator for the size-relative family is **float-cap**
  (`Float × LastDailyClose`), used for ALL rows for consistency; `MktCap` is
  only a fallback (populated on ~16% of PR-stats rows).
- Re-run the evaluator as the PR-stats master grows: only 37 positives total
  (11 in the test block), so every precision number has wide error bars.

## Key findings (digest — details and tables in the report)

- Top signals: **growth number stated in the headline** (7x lift, rare) and
  **guidance raise** (3.7x, out-lifts every growth print).
- Near-perfect cheap vetoes: **stated/computed revenue decline** (1/55 positive)
  and **scheduling-only date announcements** (1/93).
- **Body carries the signal for earnings PRs** — the opposite of the general
  fromPR finding where headline won 9-1. Winners and duds share boilerplate
  headlines; the separating numbers are in the body.
- The live-gate FinBERT headline score is *anti*-predictive here (test AUC 0.297).
- Working combo default: rules + `distilroberta_Body` (complementary, cheapest
  transformer). Which combo is best is NOT settled — wait for ~30 more positives.

## Gotchas

- Train AUC 0.848 vs test 0.597: partly grid overfit (972 combos on 26
  positives), partly the base-rate regime shift. The defensible live claim is
  ~2x lift at score>=4, not the full-sample 4–6x.
- Body is capped at ~400 words for most corpus rows — late-body financial
  tables are invisible to the extractor; extraction "misses" there are a data
  limitation, not a regex bug.
- Scheduling-only PRs have positive scores capped at 0 (`scheduling_zeroes`),
  shown as `sched:capped0` in `EarnBreakdown` — intentional, not a bug.
- Family B (dollar pairs) borrows a missing unit from the other side
  ("$24.3 million ... $23.8") and drops pairs with ratio >100 or <0.01 as
  unit garbage; income-vs-loss comparisons are skipped there because family E
  (profit flip) owns them — a % change vs a loss is meaningless.
