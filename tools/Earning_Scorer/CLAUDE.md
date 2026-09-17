# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

Rule-based (regex) scorer for **Earnings_Results press releases**: extracts
earnings metrics from Headline/Body sentence-by-sentence and turns them into
interpretable scores aimed at predicting large `fromPR_High(%)` moves
(calibrated on >=30%). Research tool in the N2 tree — but **one of its outputs
IS read by the live chain**: `Orchestrator5.2.py` imports `earning_scorer.py`
and its trade gate tests `EarningScore >= 4`. Findings live in
`Earnings_text_structures_report.md`; every number there is reproducible by
`evaluate_earning_scorer.py`.

## Interpreter

Always run with the venv interpreter, never system python3:
`/home/tom/venv/bin/python` (pandas/sklearn live there).

## TWO SCORES — read this before touching anything

| | `EarningScore` | `EarnScoreNorm` |
|---|---|---|
| type | **int** points, ~ -7..+9 | **float** 0-1 |
| families | A–I | A–I (same extraction) |
| params | `earn_score_params.json` | `earn_norm_params.json` |
| calibrated | 2026-08-01 | 2026-08-14 |
| **read by live chain** | **YES** | no |
| status | **FROZEN** | current research score |

**`EarningScore` is frozen and `evaluate_earning_scorer.py` never writes
`earn_score_params.json`.** The live gate line is:

```
EARNINGS: FineClass == Earnings_Results AND EarningScore >= 4 AND kw15_proba_bd >= 0.50
```

If the score is rescaled to 0-1, that rule still *parses*, and then `0.62 >= 4`
never fires — the earnings trade branch dies silently on the next O5.2 restart,
no error, no log line. The "obvious" fix of writing `>= 0.62` instead makes O5.2
**refuse to start**, because `EarningScore` is typed `_KIND_INT` and rejects a
fractional threshold (`Orchestrator5.2.py:920`). So the freeze is enforced, not
documented: `assert_frozen_path()` checks 10 synthetic golden cases at the top of
every evaluator run and aborts with instructions if any score moved.

Changing `EarningScore` on purpose is a three-part commit: the scorer, the
goldens in `evaluate_earning_scorer.py`, and the live rules file — plus an O5.2
restart, or the running process keeps the old gate anyway.

## Scripts & artifacts

- **earning_scorer.py** — extractor + both scorers. Extraction families A–I
  (explicit growth, dollar-pair implicit growth, record claims, guidance,
  profit flip, margin, negative structures, scheduling-only, size-relative);
  each is a commented block in the source. Module API: `extract_metrics`,
  `score_metrics` (frozen int), `score_norm` (0-1), `score_text`,
  `score_dataframe`. `NORM_FEATURES` / `norm_indicators` define the binary
  indicator set **here** so the evaluator fits against exactly what runtime
  computes — add a feature in one place, not two.
- **evaluate_earning_scorer.py** — calibrates `EarnScoreNorm` and honestly
  evaluates both scores. `--from-master` rebuilds the Earnings_Results slice
  from the PR-stats master first.
- **earn_score_params.json** (frozen 2026-08-01) / **earn_norm_params.json**
  (2026-08-14). Both are **gitignored** (the repo whitelist tracks no .json), so
  they exist only on this disk; `BUILTIN_PARAMS` and `BUILTIN_NORM` in the
  scorer mirror them so a fresh clone still ranks correctly. **If you re-run the
  evaluator, paste the new weights into `BUILTIN_NORM` too** or clone and disk
  will disagree.
- **PRstats_2026-05-04-2026-08-13_EarningResults*.tsv / earn_qa_dump.tsv** —
  input slice (604 rows), scored output, QA dump. Gitignored per root rules,
  never force-add.

## Common commands

```bash
# Recalibrate EarnScoreNorm + full evaluation report (~2 min).
# Rebuilds the slice; OVERWRITES earn_norm_params.json, *_earnScored.tsv,
# earn_qa_dump.tsv.  Never touches earn_score_params.json.
/home/tom/venv/bin/python evaluate_earning_scorer.py --from-master

# Same, reusing the existing slice
/home/tom/venv/bin/python evaluate_earning_scorer.py

# Batch-score a TSV (needs Headline/Body; Float/LastDailyClose/MktCap optional
# for the size-relative family) -> <input>_earnScored.tsv with earn_*/earnH_*
# columns + EarningScore/EarnBreakdown + EarnScoreNorm/EarnNormBreakdown
/home/tom/venv/bin/python earning_scorer.py --input FILE.tsv

# Score ONE PR from stdin -> JSON on stdout (CLI mirrors keyword_scorer-2.py)
echo "body text" | /home/tom/venv/bin/python earning_scorer.py --stdin --headline-text "Headline"
```

No test suite. Verification = run the evaluator (its two startup guards catch
the dangerous regressions) and diff its printed tables against
`Earnings_text_structures_report.md`; extraction QA = spot-check
`earn_qa_dump.tsv` (every hit carries its source sentence and its train/test
block).

## Calibration protocol (spans both scripts — don't break it)

- Population is `fromPR_status == "ok"` AND **`FineClass == "Earnings_Results"`**
  — exactly what the live gate leg sees. The TSV's FineClass reproduces at
  99.98% from `tools/categorizer/PrClassifier.classify_row`, so calibrating on
  the column matches what the orchestrator computes.
- Honest split on `ArrivalDate` at **2026-06-20** (repo-wide convention): train
  396 rows / 30 pos, test 208 / 32 pos. Target >=30%, >=15% as a stability check.
- **Thresholds and regularisation are chosen by 5-fold CV *inside* the train
  block**, not by maximising train-block fit — that was the 2026-08-01 mistake
  (972 combos scored on the same 26 positives they were judged on). The test
  block is scored once, after everything is frozen.
- The >=30% base rate is regime-shifted (May 5.0% → June 20.9% → July 20.0% →
  Aug 13.4%): full-sample lifts are flattering; **test-block numbers are the
  honest ones**. Both scores are ranking scores; `EarnScoreNorm` looks like a
  probability and is not one.
- Size denominator for the size-relative family is **float-cap**
  (`Float × LastDailyClose`), used for ALL rows for consistency; `MktCap` is only
  a fallback (populated on ~35% of rows).

## Key findings (digest — details and tables in the report)

- **The 2026-08-14 retrain did not beat the 2026-08-01 score.** Test AUC:
  frozen `EarningScore` 0.599, rebuilt `EarnScoreNorm` 0.593, learned logistic
  challenger 0.535 — all within one hit of each other at top-10/20. **~0.6 test
  AUC is the ceiling on this corpus**, and the richest model is the worst.
  That is why the live gate was not retuned.
- **The headline channel wins, reversing the old report.** Headline revenue
  growth 4.6x, headline-has-% 2.7x, headline-names-a-metric 2.2x, vs body
  revenue growth 1.9x. (The old "body carries the signal" line was about
  *encoder* columns and does not hold for the rules on the rebuilt labels.)
- **Vetoes are the most reliable part**: revenue decline 2/71 (0.27x) and the
  new `no_structure` flag — 30% of earnings PRs yield zero extractable hits and
  run at 0.54x. Silence is informative.
- **Record claims and margin-bps are now noise** (1.04x / 1.32x, were ~1.8x);
  the L2 fit zeroed `record`, `guid_cut`, `expects_record` on its own.
- Two candidate families were tested against the train block and **rejected** —
  see "rejected families" in the report before re-proposing either.

## Gotchas

- Body is capped at **400 words on every row** of this corpus (median = p90 =
  max = 400) — late-body financial tables are invisible to the extractor.
  Extraction "misses" there are a data limitation, not a regex bug, and lifting
  the cap needs a corpus re-fetch. This is the highest-value next step.
- **The scheduling family (H) is dead in this population** — it fires on 1 of
  604 rows. Date announcements now classify as their own `Earnings_Date`
  FineClass and never reach the scorer. Family H is kept as a safety net (the
  classifier could change), not because it contributes. The old report's "1/93
  scheduling veto" finding has moved upstream into `PrClassifier.py`.
- A negative weight in `earn_norm_params.json` is not a claim that the structure
  is bearish. `rev_growth_hi` and `margin_up` are negative because they are
  nested inside indicators that already fired (`rev_growth_lo` at +0.60); read
  the univariate table in the report for standalone lift.
- The evaluator's calibration grid uses a **vectorised** indicator path for
  speed (~15k logistic fits). `assert_fast_path_matches()` proves it equals
  `earning_scorer.norm_indicators` on two threshold sets — if you add an
  indicator, add it to `fast_matrix` too or that guard aborts the run.
- Family B (dollar pairs) borrows a missing unit from the other side
  ("$24.3 million ... $23.8") and drops pairs with ratio >100 or <0.01 as
  unit garbage; income-vs-loss comparisons are skipped there because family E
  (profit flip) owns them — a % change vs a loss is meaningless.
- Bullet-table bodies (`* Revenue * $12.2 million in Q1 2026 compared to $8.6
  million`) defeat family B: the splitter separates the metric name from its
  value. Real, but it occurs on 1 of 604 rows — deliberately not fixed.
