# Which text structures in an Earnings PR predict a big fromPR_High(%) move?

**Dataset**: `PRstats_2026-05-04-2026-08-13_EarningResults.tsv` — the 604 rows of
the PR-stats master with `fromPR_status == "ok"` AND `FineClass ==
"Earnings_Results"`. **Target**: `fromPR_High(%) >= 30` (62 positives, 10.3% base
rate); `>= 15` (149 positives, 24.7%) as a stability check. **Tooling**:
`earning_scorer.py` (regex extractor + scores) evaluated by
`evaluate_earning_scorer.py`. Everything is fitted on `ArrivalDate < 2026-06-20`
(396 rows / 30 pos); the honest test block is 2026-06-20..08-13 (208 rows / 32
pos). Rebuilt 2026-08-14; supersedes the 2026-08-01 report (git has that version).

⚠ The `>=30%` base rate is still not stable: **May 5.0%, June 20.9%, July 20.0%,
August 13.4%**. Full-sample lifts are flattering; **test-block numbers are the
honest ones**, and no score here is a calibrated probability.

## 0. What changed since 2026-08-01, and the one-line answer

The corpus was rebuilt: `FineClass`/`BroadClass` redefined, PRs added through
08-13, `fromPR_High(%)` recalculated. Positives went 37 → **62**, and the honest
block went from 11 positives to **32** — the first time these numbers have had
usable error bars.

**The retrain did not produce a better score.** On the test block:

| score | AUC >=30 | AUC >=15 | @top10 | @top20 | @top40 |
|---|---|---|---|---|---|
| `EarningScore` (frozen, 2026-08-01 calibration) | **0.599** | 0.614 | 0.400 (4) | 0.250 (5) | 0.225 (9) |
| `EarnScoreNorm` (rebuilt 0-1, this report) | 0.593 | 0.597 | 0.400 (4) | 0.250 (5) | 0.250 (10) |
| learned challenger (logistic on continuous `earn_*`) | 0.535 | — | 0.300 (3) | 0.250 (5) | — |

Three different fitting methods, three years of feature engineering apart, all
land on ~0.59 test AUC and are separated by at most one hit at top-10/20. **The
ceiling on this corpus is ~0.6 AUC, and it is not a weighting problem.** The
richer learned model is the *worst* of the three, which is the usual signature of
a small-n problem rather than an under-parameterised one.

What the rebuild *did* buy: the noise families are now identified and zeroed
(§2), the `no_structure` veto was found (§1), two candidate families were tested
and rejected (§4), and the thresholds are now chosen by inner CV rather than by
maximising the number they are later judged on (§3).

## 1. Text structures ranked (full sample, precision vs 10.3% base)

| structure | n fired | rate | precision >=30% | lift | median move |
|---|---|---|---|---|---|
| **Headline states revenue growth >= 10%** ("Revenue Up 60%") | 21 | 3.5% | **0.476** | **4.64x** | +20.5% |
| **Headline contains any %** | 51 | 8.4% | 0.275 | 2.67x | +15.3% |
| **Headline names any metric** (revenue/EPS/EBITDA…) | 88 | 14.6% | 0.227 | 2.21x | +11.8% |
| **Guidance raised** | 24 | 4.0% | 0.208 | 2.03x | +8.9% |
| Body revenue growth >= 10% | 194 | 32.1% | 0.191 | 1.86x | +10.1% |
| Profit metric growth >= 30% | 88 | 14.6% | 0.182 | 1.77x | +11.5% |
| Profitability flip (income vs year-ago loss) | 34 | 5.6% | 0.176 | 1.72x | +13.1% |
| Body revenue growth >= 75% | 54 | 8.9% | 0.167 | 1.62x | +11.8% |
| Profit $ >= 10% of float-cap | 86 | 14.2% | 0.163 | 1.59x | +8.5% |
| Revenue $ >= 25% of float-cap | 281 | 46.5% | 0.135 | 1.32x | +7.4% |
| Margin expansion >= 200 bps | 37 | 6.1% | 0.135 | 1.32x | +9.9% |
| Any negative flag | 34 | 5.6% | 0.118 | 1.15x | +8.2% |
| Record claim | 28 | 4.6% | 0.107 | 1.04x | +11.2% |
| — vetoes — | | | | | |
| **Nothing extractable at all** (`n_hits == 0`) | 181 | 30.0% | 0.055 | **0.54x** | +4.1% |
| **Revenue decline** (stated or computed) | 71 | 11.8% | 0.028 | **0.27x** | +3.1% |
| Guidance cut/withdrawn | 3 | 0.5% | 0.000 | 0.0x | +2.0% |

Reading of the table:

- **The headline channel is the whole story, and this reverses the old report.**
  The three headline structures are the top three lifts (4.6x / 2.7x / 2.2x)
  while body revenue growth manages 1.9x. The 2026-08-01 report concluded "body
  carries the signal for earnings PRs" — that conclusion was about *encoder*
  columns, and it does not hold for the regex rules on the rebuilt labels.
  Merely **naming a metric in the headline** (no number needed) is a 2.2x signal
  covering 15% of the corpus.
- **"Nothing extractable" is a real veto and it is new.** 30% of earnings PRs
  yield zero hits, and they run >=30% at less than half the base rate. Silence is
  informative: a company with a number worth printing prints it.
- **The revenue-decline veto survived the relabelling intact** (2/71, 0.27x) —
  the single most reliable finding across both reports.
- **Record claims and margin bps are now noise** (1.04x, 1.32x). They were 1.8x
  in the old report. Both are boilerplate on tiny bases.
- **Guidance raise fell from 3.7x to 2.0x** but stays clearly positive.

## 2. The rebuilt score (`EarnScoreNorm`)

Weights are an L2 logistic over the binary structure indicators, fitted on the
train block, rounded to 0.05 (rounding moved train AUC 0.7912 → 0.7934, i.e.
nothing). Thresholds `rev_lo=10, rev_hi=75, prof_thr=30, margin=200bps,
rev/fcap=0.25, prof/fcap=0.10`, bias −0.70, output `sigmoid(bias + Σw)`.

| indicator | weight | | indicator | weight |
|---|---|---|---|---|
| rev_growth_lo | +0.60 | | profit_flip | +0.20 |
| hl_has_metric | +0.40 | | prof_fcap | +0.10 |
| hl_rev_growth | +0.35 | | neg_flag | +0.05 |
| prof_growth | +0.35 | | record / guid_cut / expects_record / sched_only | **0.00** |
| rev_fcap | +0.35 | | rev_growth_hi | −0.10 |
| hl_has_pct | +0.30 | | margin_up | −0.10 |
| guid_raise | +0.25 | | rev_decline / no_structure | −0.25 |

The fit zeroed exactly the families §1 shows as noise, without being told to.

| block | AUC | operating points |
|---|---|---|
| train (396, 30 pos, base 7.6%) | 0.793 | >=0.60: prec 0.308 (4.1x); >=0.70: 0.400 (5.3x) |
| **test (208, 32 pos, base 15.4%)** | **0.593** | >=0.60: prec 0.250 (1.6x, 10/40) |
| full (604, 62 pos, base 10.3%) | 0.698 | >=0.60: prec 0.283 (2.8x) |

Stability vs `>=15%` on the test block is monotone through 0.60 (prec 0.500 at
>=0.60, 1.65x) and then breaks down — the same shape as the old report.

**Two weights have the wrong sign and it is worth knowing why.** `rev_growth_hi`
(−0.10) and `margin_up` (−0.10) both have *positive* univariate lift in §1. They
go negative because the indicators are nested/correlated: `rev_growth_hi` only
ever fires on rows where `rev_growth_lo` (+0.60) already fired, so the fit uses
it as a small correction, not as "high growth is bad". Do not read a single
weight as a standalone claim about the structure — read §1 for that.

## 3. Calibration protocol (changed)

The 2026-08-01 run picked thresholds by maximising train-block AUC over 972
combos with 26 positives, then reported train AUC as if it meant something
(0.848 → 0.597 test). Here the same 972-combo grid × 5 regularisation strengths
is scored by **5-fold CV inside the train block**, so selection is itself
out-of-sample: best inner-CV AUC **0.766** vs 0.793 refit-on-train — a 0.027 gap
where the old protocol had 0.25. The test block is scored once, after every
parameter is frozen.

This did not rescue test AUC (0.593), which is the point: the old drop was
*mostly* the regime shift and small-n, not only grid overfit.

## 4. Rejected families (tested, not shipped)

13 of the 62 positives score 0 on the frozen extractor. Reading them suggests a
family — and the train block refuses it both times:

- **Bundled non-earnings catalyst.** ~6 positives are an earnings headline whose
  second clause announces something else: OXBR *"…Results and Launches AI
  GridWorks"*, DARE *"…Results and Highlights Commercial Inflection"*, AIMD, JZXN
  *"Intends to Sign Cooperation Agreement"*, DRMA. Tested as headline
  co-occurrence and as a strict `<results> and <catalyst verb>` clause: the
  strict form fires **29 times in the train block on 0 positives** (lift 0.00);
  the loose forms are no better. All the supporting examples are in the *test*
  block — the hypothesis was generated by the rows it would be validated on.
  **Rejected.**
- **Bullet-table bodies.** GLBS (+40%, train) hides its numbers in
  `* Revenue * $12.2 million in Q1 2026 compared to $8.6 million`, where the
  bullet splitter separates the metric name from its value so family B cannot
  link them. Real defect — but it occurs on **1 of 604 rows**. Fixing it is
  fitting a single row. **Rejected**, documented here instead.

Extraction is not the bottleneck: only **2 of the 30 train positives** have
nothing to extract (GLBS above, and RFL, whose "earnings" body is a Phase 3
trial update — a biotech catalyst in an earnings wrapper, irreducible by
earnings rules).

## 5. Two scores, and why `EarningScore` did not move

`earning_scorer.py` emits both:

- **`EarningScore`** — integer points, families A–I, `earn_score_params.json`.
  **Frozen and unchanged by this rebuild.** The live gate reads it
  (`EARNINGS: FineClass == Earnings_Results AND EarningScore >= 4 AND
  kw15_proba_bd >= 0.50`). Re-scaling it to 0-1 would leave the rule parsing
  fine while `0.62 >= 4` silently never fires — a dead trade branch with no
  error — and writing `>= 0.62` instead makes O5.2 refuse to start, because that
  field is typed integer-points. `evaluate_earning_scorer.py` therefore never
  writes `earn_score_params.json`, and asserts 10 golden cases at startup so the
  freeze is checked rather than assumed.
- **`EarnScoreNorm`** — the 0-1 rebuild, `earn_norm_params.json`. Nothing live
  reads it.

Since §0 shows the rebuild does not beat the frozen score out of sample, **there
is no evidence-based reason to retune the live gate**, and it was left alone.

## 6. Limitations

- 62 positives, 32 in the honest block. At top-10 one hit is 0.10 of precision;
  every comparison in §0 is inside noise.
- The base-rate regime shift (5.0% → 20.9% → 20.0% → 13.4%) means cutoffs are
  ranking cuts, not probabilities. `EarnScoreNorm` looks like a probability and
  is not one.
- **Body is hard-capped at 400 words** for every row in this corpus (median = p90
  = max = 400). Late-body financial tables are invisible; ~7 of the 13
  zero-score positives are this, not a regex gap. Lifting the cap needs a
  re-fetch of the corpus, not a scorer change — it is the single highest-value
  next step.
- No encoder section: the 2026-08-13 master carries only headline encoder columns
  (`*_ss_hd`), no `*_Body_*` and no FinBERT/Prosus column, so the old report's
  "rules + distilroberta_Body" finding is not reproducible here and was not
  re-derived from a substitute feature.

## Files

- `earning_scorer.py` — extractor + both scores (batch `--input`, single `--stdin`).
- `evaluate_earning_scorer.py` — reproduces every number above;
  `--from-master` rebuilds the slice from the PR-stats master.
- `earn_score_params.json` (frozen) / `earn_norm_params.json` (rebuilt) — both
  gitignored; `BUILTIN_PARAMS` / `BUILTIN_NORM` mirror them for fresh clones.
- `PRstats_2026-05-04-2026-08-13_EarningResults[_earnScored].tsv`, `earn_qa_dump.tsv`
  (all 62 positives + 30 random, every hit with its source sentence and block label).
