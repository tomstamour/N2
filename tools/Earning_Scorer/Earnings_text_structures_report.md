# Which text structures in an Earnings PR predict a big fromPR_High(%) move?

**Dataset**: `PRstats_2026-05-04-2026-07-30_EarningResults.tsv` — 531 Earnings_Results
PRs, 518 with a valid `fromPR_High(%)` label. **Target**: `fromPR_High(%) >= 30`
(37 positives, 7.1% base rate). **Tooling**: `earning_scorer.py` (regex metric
extractor + additive rule score) evaluated by `evaluate_earning_scorer.py`.
Thresholds calibrated on ArrivalDate < 2026-06-20 only (439 rows / 26 pos);
honest test block = 2026-06-20..07-30 (79 rows / 11 pos). Written 2026-08-01.

⚠ The >=30% base rate is NOT stable across the corpus: May 3.7%, June 19.7%,
July 13.9%. Full-sample lifts are therefore flattering vs what the May regime
would give; the test-block numbers are the honest ones.

## 1. Text structures ranked (full sample, precision vs 7.1% base)

| structure | n fired | precision >=30% | lift | median move |
|---|---|---|---|---|
| **Headline states revenue growth >= 10%** ("Revenue Up 60%") | 6 | 0.500 | 7.0x | +27.3% |
| **"Expects record ..." (forward claim)** | 2 | 0.500 | 7.0x | +27.6% |
| **Headline contains any %** | 17 | 0.294 | 4.1x | +15.2% |
| **Guidance raised** | 19 | 0.263 | 3.7x | +9.3% |
| **Extracted profit $ >= 20% of float-cap** | 48 | 0.229 | 3.2x | +9.7% |
| **Body revenue growth >= 50%** | 46 | 0.217 | 3.0x | +14.2% |
| **Profit metric growth >= 50%** (net income/gross profit/EBITDA/EPS) | 38 | 0.211 | 3.0x | +12.1% |
| **Profitability flip** (income vs year-ago loss) | 21 | 0.190 | 2.7x | +16.0% |
| Body revenue growth >= 10% | 118 | 0.186 | 2.6x | +9.4% |
| Extracted revenue $ >= 50% of float-cap | 148 | 0.162 | 2.3x | +8.0% |
| Record claim ("record quarterly revenue") | 16 | 0.125 | 1.8x | +9.6% |
| Margin expansion >= 300 bps | 16 | 0.125 | 1.8x | +6.2% |
| — negative structures — | | | | |
| **Revenue growth negative** (stated or computed) | 55 | 0.018 | 0.25x | +2.5% |
| **Scheduling-only PR** ("to report results on \<date\>", no numbers) | 93 | 0.011 | 0.15x | +3.3% |
| Guidance cut/withdrawn | 3 | 0.000 | 0.0x | — |

Reading of the table:

- **The strongest single signal is the company putting the growth number in the
  headline.** It is rare (6+17 PRs) but when "Revenue Up 60%" appears in the
  headline itself, half of those PRs ran >=30%. A cheap live filter.
- **Guidance raises out-lift every growth print** (3.7x vs ~3x for >=50%
  revenue or profit growth).
- **Magnitude relative to size matters**: quarterly net profit worth >=20% of the
  float-cap (NIVF: $9.9M profit on a ~$1M float-cap, +81%) fires as often as the
  growth rules and carries the same lift.
- **The negative structures are the cleanest finding.** Stated/computed revenue
  DECLINE almost guarantees no >=30% move (1/55), and the 93 scheduling-only
  date announcements are near-dead (1/93). Together they veto ~28% of the corpus
  at a cost of 2 of 37 positives.
- Record claims and margin bps underperform their reputation (1.8x) — "record"
  is often boilerplate on tiny bases.

## 2. Headline vs Body verdict

**Body carries the signal for earnings PRs** — the opposite of the general
fromPR finding (headline won 9-1 there):

- Big winners and duds share near-identical headlines ("X Reports Q1 2026
  Financial Results"); the separating numbers are in the body.
- All body-encoder sentiment scores out-rank their headline twins (below);
  best headline encoder test AUC 0.574 vs body 0.661.
- The exception: *when* the headline contains numbers, it is the single most
  precise structure (headline growth 7x, headline-has-% 4.1x). The headline is
  a high-precision/low-recall channel; the body is the workhorse.

## 3. Rule score (EarningScore) performance

Additive points over the families above; calibrated thresholds (train-only grid,
`earn_score_params.json`): rev_lo=10, rev_hi=50, prof_thr=50, margin=300 bps,
rev/fcap=0.5, prof/fcap=0.2. Scheduling-only PRs are capped at 0.

| block | AUC | best operating points |
|---|---|---|
| train (439 rows, 26 pos, base 5.9%) | 0.848 | score>=5: prec 0.333 (5.6x); score>=6: prec 0.500 (8.4x) |
| **test (79 rows, 11 pos, base 13.9%)** | **0.597** | score>=4: prec 0.267 (1.9x, 4/15) |
| full (518 rows, 37 pos, base 7.1%) | 0.775 | score>=5: prec 0.311 (4.4x); score>=7: prec 0.500 (7.0x) |

Stability check vs the softer >=15% target on the test block: monotone and
healthier — score>=4: prec 0.467 (1.9x), score>=6: 0.571 (2.4x). The ranking
survives out of sample; the >=30 cut is simply very noisy with 11 positives.

Honest read: the train->test AUC drop (0.848 -> 0.597) is partly threshold
overfit (972-combo grid on 26 positives) and partly the June/July regime where
the base rate doubled (many non-flagged PRs mooned). Treat test lift ~1.9-2x at
score>=4 as the defensible claim, not the full-sample 4-6x.

## 4. Do the 11 encoder sentiment columns add anything?

Top of the AUC table (target >=30%, same split):

| feature | train AUC | test AUC |
|---|---|---|
| EarningScore (rules) | 0.848 | 0.597 |
| distilroberta_**Body** | 0.803 | **0.661** |
| Trading-Hero_Body | 0.764 | 0.649 |
| finbert-tone_Body | 0.742 | 0.633 |
| DeBERTa_Body | 0.774 | 0.596 |
| best headline encoder (FinancialBERT) | 0.606 | 0.574 |
| FinBERT (live gate model, headline) | 0.595 | 0.297 |

- **Yes**: body-encoder sentiment is complementary. A 2-feature logistic
  (EarningScore + distilroberta_Body, fit on train) gives test AUC 0.626 and
  test precision@top10 0.400 vs 0.200 for rules alone (top20: 0.250 vs 0.200).

**But no single combo is demonstrably "the best"** — a head-to-head of the
plausible candidates on the test block (2026-08-01 follow-up):

| candidate | train AUC | test AUC | test @top10 | test @top20 |
|---|---|---|---|---|
| rules alone (EarningScore) | 0.848 | 0.597 | 0.20 (2) | 0.20 (4) |
| distilroberta_Body alone | 0.803 | 0.661 | 0.30 (3) | 0.20 (4) |
| Trading-Hero_Body alone | 0.764 | 0.649 | 0.30 (3) | 0.25 (5) |
| rules + distilroberta_Body | 0.862 | 0.626 | **0.40 (4)** | 0.25 (5) |
| rules + finbert-tone_Body | 0.862 | 0.630 | **0.40 (4)** | 0.25 (5) |
| distilrob + TradingHero (no rules) | 0.783 | **0.677** | 0.30 (3) | 0.25 (5) |
| rules + distilrob + TradingHero | 0.868 | 0.630 | 0.30 (3) | 0.25 (5) |

Every candidate is within ONE hit of the others at top10/top20 (11 test
positives). Robust conclusion: adding a body encoder to the rules always helps
vs rules alone; picking WHICH encoder/combo must wait for more positives.
Working default: rules + distilroberta_Body (top-tied on train and top10, and
distilroberta is the cheapest transformer at ~6.5 ms).
- The live-gate FinBERT headline score is useless here (test AUC 0.297 —
  *anti*-predictive on earnings PRs).
- LouMcD lexicon and FinMarBa are noise on this class.

## 5. Extractor coverage and known misses

Fire rates over 531 rows: revenue growth 44%, profit growth 30%, rev-$/float-cap
47%, profit flip 4%, guidance 4%, record 3%, margin 5%, negative flags 8%,
scheduling 18%. QA dump (`earn_qa_dump.tsv`: all 37 positives + 30 random rows,
each hit with its source sentence) shows extracted values match the sentences;
the dollar-pair computation correctly handles "increased to $24.3M compared to
$23.8M" (GAIA: +2.1%, correctly scored 0).

4 of 37 positives have zero extractable structure — irreducible by text rules:
GLBS/LGPS/BGDE are pure date announcements that still ran (+40/+51/+31%), and
RFL's "earnings" body is a Phase 3 trial update (biotech catalyst, not an
earnings structure).

## 6. Limitations & next steps

- 37 positives total, 11 in the honest block — every precision number here has
  wide error bars; re-run `evaluate_earning_scorer.py` as the master file grows.
- Body is capped at ~400 words for most rows; late-body financial tables are
  invisible to the extractor.
- The base-rate regime shift (May 3.7% -> June/July ~15-20%) means score
  thresholds should be treated as ranking cuts, not calibrated probabilities.
- Candidate next steps: (a) add the two headline structures + revenue-decline /
  scheduling vetoes as cheap live features; (b) retest the combo with
  distilroberta_Body once ~30 more positives accumulate; (c) consider scoring
  the FULL body (uncapped) offline to reach the financial tables.

## Files

- `earning_scorer.py` — extractor + scorer (batch `--input`, single `--stdin`);
  loads `earn_score_params.json` (calibrated 2026-08-01) automatically.
- `evaluate_earning_scorer.py` — reproduces every number in this report.
- `PRstats_..._EarningResults_earnScored.tsv` — dataset + earn_* features + score.
- `earn_qa_dump.tsv` — per-hit sentences for the 37 positives + 30 random rows.
