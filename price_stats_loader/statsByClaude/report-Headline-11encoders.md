# Encoder sentiment vs `fromPR_High(%)` — the 11 encoders evaluated (headline **and** body)

**Date:** 2026-07-20 (body results merged in 2026-07-20)
**Corpus:** `outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv`
— 3306 rows, 3253 with a `fromPR_High(%)` value after cleaning (53 null targets
dropped, 0 outliers >500%, 0 duplicate PRs).
**Question:** does any of the 11 encoders predict the post-PR spike, alone, well
enough to trade on — does the answer change by PR class, and does scoring the PR
**body** beat scoring the headline?
**Scripts written for this:** `analyze_frompr_predictors.py`,
`class_conditional_predictors.py` (now with `--triggers` / `--report-suffix`),
`merge_body_encoder_cols.py`, `head_vs_body.py`, plus an `--invert` flag added to
`false_positive_count.py`. Canonical commands are in `commands.txt`.

> **Companion report:** the full head-vs-body analysis lives in
> `report-Body-vs-Headline-11encoders.md`. Its verdict: **the headline is the
> better text field** (9 channel pairs to 1 at Bonferroni, 38 ties), body scoring
> costs **10× the inference time**, and no body column is a tradeable trigger.
> Two body findings do carry over into this report and are reflected below —
> the **FinancialBERT / LouMcD-SDict correction (recommendation #2)** and the
> **Earnings class exception (§3)**.

---

> ## ⚠️ REVISION 2026-07-20 (corrected 2026-07-21) — read before acting
>
> The 594 previously-unscored rows have been scored with `keyword_scorer-2.py`
> (body and headline models), producing `PRstats_..._Classed_kwScored.tsv`.
> That closed the gap this report flagged. **Two conclusions changed:**
>
> 1. **The keyword model is the strongest honest predictor**, at AUC 0.669–0.672
>    out-of-sample — above `MktCap` (0.658) and `Float` (0.653), and well above
>    every encoder (≤0.58). The live combo trigger `model_proba_PLUS_kw_mult_nb
>    ≥ 0.6` delivers **30.4% precision at 2.38× lift** on data it never saw.
> 2. **The FinMarBa veto stands, with a narrower claim than originally made.**
>    See the correction note below.
>
> ### Correction to the 2026-07-20 retraction
>
> On 2026-07-20 this report retracted the FinMarBa veto, claiming it had "no
> edge at all" because it scored 13.3% vs a 12.8% base on the 578 post-06-20
> rows. **That reasoning was wrong.** It applied the keyword model's leakage
> correction to a model that has no leakage: FinMarBa is trained on an external
> corpus and has never seen any row here, so **all 3253 rows are a valid test
> for it.** Restricting to 578 discarded 82% of legitimate evidence in favour of
> an underpowered subset (30 fires, 4 true positives). Stratified:
>
> | block | n | movers | base | AUC (inv) | `indec ≤ 0.1` | lift |
> |---|---|---|---|---|---|---|
> | FULL | 3253 | 287 | 8.8% | 0.631 [0.595, 0.667] | 22/93, 19.1% [13.0, 27.3] | 2.17× |
> | early | 2675 | 213 | 8.0% | 0.639 [0.597, 0.680] | 18/67, 21.2% [13.8, 31.0] | 2.66× |
> | recent | 578 | 74 | 12.8% | 0.581 [0.509, 0.653] | 4/26, 13.3% [5.3, 29.7] | 1.04× |
>
> The recent block's CI **contains** both other estimates, and the AUC CIs
> overlap heavily. That subset is uninformative, not contradictory — there is no
> evidence of regime failure, only of a small sample. **Methodological rule going
> forward: match the evaluation set to the model's training provenance.** The 11
> encoders and FinBERT are fairly tested on all 3253 rows; only the keyword
> family needs the recent-block restriction. Detail and the incremental test in
> §2.6.
>
> The verdict on the 11 encoders is **unchanged**: none is competitive alone.

---

## 0. How to read these numbers (plain statistics)

**The target.** `fromPR_High(%)` is how far the stock ran above its price at the
moment the PR hit. It is wildly right-skewed: median 5.17%, mean 13.22%, max
455.65%. A mean or a linear correlation on a variable shaped like that is
dominated by a handful of monsters, so instead we **binarize**: a row is a
"mover" if `fromPR_High(%) >= 30`. At that cutoff there are **287 movers out of
3253 rows = 8.8%**. That 8.8% is the **base rate** — the number every result
below has to beat to be worth anything.

**AUC (area under the ROC curve).** Pick one mover and one non-mover at random.
AUC is the probability the score ranks the mover higher. So:

- **0.50 = the score is worthless** (a coin flip / random ordering).
- 0.60 = ranks the mover higher 60 times out of 100 — a real but weak edge.
- 0.70+ = a genuinely useful ranking signal.
- **Below 0.50 = the signal is backwards** (higher score → *less* likely to move).
  That is still information — you just flip the sign. Columns used flipped are
  labeled `(inv)` below.

AUC is computed here with the Mann-Whitney rank formula, which only looks at the
*ordering* of scores, not their scale. That is why it is immune to the skewed
target and to the fact that `_sentiment_score` columns live on [-1, 1] while
`_positive` columns live on [0, 1] — no rescaling needed, no threshold chosen.

**How much AUC noise is there?** With 287 movers and 2966 non-movers, the
Hanley-McNeil standard error of an AUC is about **±0.018**, so a 95% confidence
interval is roughly **±0.036**. Concretely:

| Column | AUC @≥30 | 95% CI |
|---|---|---|
| FinMarBa_indecisive (inv) | 0.631 | [0.595, 0.667] |
| positive | 0.610 | [0.574, 0.646] |
| FLANG-BERT_positive | 0.610 | [0.574, 0.646] |
| nocoref_neutral_filter | 0.604 | [0.568, 0.641] |
| LouMcD-SDict_sentiment_score | 0.534 | [0.498, 0.569] |
| FinancialBERT_positive | 0.500 | [0.465, 0.535] |

**The single most important consequence:** any two columns within ~0.04 AUC of
each other are statistically indistinguishable on this dataset. All the
"0.610 vs 0.607 vs 0.604" orderings below are **ties**, not rankings. Only gaps
larger than that (e.g. 0.63 vs 0.50) are real.

**Precision (TP/FP).** AUC measures ranking over the whole population; it does
not tell you what happens when you actually fire a trigger. For that we count,
among rows where the score clears a cutoff, how many were movers (TP) and how
many were not (FP). **Precision = TP/(TP+FP)** — the hit rate you'd experience.
Compare it to the 8.8% base rate; the ratio is the **lift**. Precision on small
counts is very noisy, so Wilson confidence intervals are quoted where it matters.

**In-sample vs out-of-sample — the trap in this file.** Per
`inputs/concat-list.txt`, this TSV is a concatenation of the **05-03→06-19
keyword-model training corpus** (2712 rows, already keyword-scored) plus **594
newer post-06-20 rows that were never keyword-scored**. So `model_proba` and the
`kw_*` family are being graded on the very rows they learned from — that is
**in-sample** and inflated (the known fake ~0.9). The 11 encoders and FinBERT
were trained on *external* corpora (financial phrase banks etc.) and have never
seen any of these rows, so **for them the full 3253 rows are a fair, honest test**
and full-corpus numbers are the ones to quote. Every encoder number in this
report is full-corpus. An OOS-only companion run (`--subset-null model_proba`,
578 rows) is included for the newest-period check.

---

## 1. Global ranking — 11 encoders + the 3 in-house FinBERT columns

Each encoder is represented by its best-performing output channel. `(inv)` means
the raw AUC was below 0.5 and the column is used negated. `OOS@30` restricts to
the 578 newest un-keyword-scored rows. **`BODY@30`** is the *same channel* scored
on the PR body instead of the headline, and **Δ** is body − headline (paired
bootstrap CIs for every pair are in the companion report). `—` = in-house FinBERT
columns, which have no 11-encoder body twin.

| Rank | Column | AUC @≥15% | AUC @≥30% | OOS @≥30% | **BODY@30** | **Δ** | n |
|---|---|---|---|---|---|---|---|
| 1 | FinMarBa `_indecisive` **(inv)** | 0.397 → **0.603** | 0.369 → **0.631** | 0.419 → 0.581 | 0.581 | −0.051 | 3253 |
| 2 | ★ `positive` (in-house FinBERT) | 0.592 | 0.610 | 0.551 | — | — | 3177 |
| 2 | FLANG-BERT `_positive` | 0.575 | 0.610 | 0.558 | 0.554 | −0.056 | 3253 |
| 4 | FLANG-ELECTRA `_sentiment_score` | 0.580 | 0.607 | 0.568 | 0.549 | −0.058 | 3253 |
| 4 | Trading-Hero `_sentiment_score` | 0.593 | 0.607 | 0.543 | 0.595 | −0.012 | 3253 |
| 4 | DeBERTa `_positive` | 0.576 | 0.607 | 0.544 | 0.540 | −0.066 | 3253 |
| 7 | ★ `sentiment_score` (in-house) | 0.587 | 0.605 | 0.547 | — | — | 3253 |
| 8 | ModernBERT `_positive` | 0.579 | 0.604 | 0.503 | 0.558 | −0.046 | 3253 |
| 8 | ★ `nocoref_neutral_filter` (in-house) | 0.581 | 0.604 | **0.580** | — | — | 3147 |
| 10 | distilroberta `_sentiment_score` | 0.579 | 0.599 | 0.544 | 0.559 | −0.040 | 3253 |
| 11 | finbert-tone `_sentiment_score` | 0.573 | 0.590 | 0.518 | 0.537 | −0.053 | 3253 |
| 12 | RoBERTa `_sentiment_score` | 0.547 | 0.567 | 0.543 | 0.498 | −0.069 | 3253 |
| 13 | LouMcD-SDict `_sentiment_score` | 0.527 | 0.534 | 0.525 | 0.554 | **+0.020** | 3253 |
| 14 | FinancialBERT `_negative` (inv) | 0.496 | 0.482 → 0.518 | 0.525 | 0.567 | **+0.049** | 3253 |

**Non-sentiment benchmarks on the same rows** (for scale):
`Float` inverted **0.677** (small float → bigger spike), `MktCap` inverted 0.631,
`Headline-RBscore` 0.661. The keyword model's 0.892 is in-sample and not
comparable — its measured walk-forward value was 0.676.

### Reading the table

1. **Ranks 2 through 10 are one big statistical tie.** Nine columns sit between
   0.599 and 0.610 with confidence intervals of ±0.036. There is no
   "best encoder" among them; picking FLANG-BERT over DeBERTa over your own
   `positive` is choosing between indistinguishable options.
2. **The ten transformer encoders bought nothing.** Your existing in-house
   `positive` (0.610) matches the best of them exactly. Roughly 70 new columns of
   model output did not improve on the FinBERT score the live chain already
   computes.
3. **The one genuine outlier is FinMarBa's `indecisive` channel, used inverted**
   (0.631). Its confidence interval [0.595, 0.667] sits clear of the pack's
   center. It is an **anti-signal**: high model indecisiveness → the spike does
   *not* happen. That makes it a natural **veto**, not a trigger.
4. **Two encoders are statistically dead.** FinancialBERT's CI includes 0.500 —
   it labels ~98% of rows neutral, so it carries essentially no information here.
   LouMcD-SDict (0.534) is barely distinguishable from noise.
5. **Sentiment loses to plain size.** Inverted `Float` (0.677) beats every
   sentiment column in the table. The strongest single thing about a small-cap PR
   pop in this data is that the float is small, not that the wording is upbeat.
6. **OOS column is the sanity check, not a penalty.** On the newest 578 rows the
   whole field shrinks (best ~0.57–0.58) — expected, since 74 movers give a much
   wider CI. Notable: your `nocoref_neutral_filter` holds up *best* there (0.580),
   while ModernBERT collapses to 0.503.
7. **The Δ column is almost entirely negative — the headline is the better text
   field.** Nine of the eleven encoders score *worse* on the body, several by more
   than the ±0.036 noise floor (DeBERTa −0.066, RoBERTa −0.069). The two positive
   Δs belong to the two encoders that were dead on headlines — see recommendation
   #2, which this reverses. Full paired-bootstrap testing, the trigger matrix, the
   incremental-value test and the 10× cost figure are in
   `report-Body-vs-Headline-11encoders.md`.

---

## 2. Trigger behaviour — TP/FP matrix

AUC says "can it rank." This says "what happens if you fire." Ground truth is
`fromPR_High(%) >= 30`; cells are **TP/FP**. Levels are cutoffs; for the two
inverted columns the cutoff is mirrored (`value <= 1 − level`) so that a higher
level always means a stricter trigger.

| level | FinMarBa_indec(inv) | positive | FLANG-BERT_pos | FLANG-ELECTRA_ss | Trading-Hero_ss | DeBERTa_pos | sentiment_score | ModernBERT_pos | nocoref_nf | distilroberta_ss | finbert-tone_ss | RoBERTa_ss | LouMcD_ss | FinancialBERT_neg(inv) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.2 | 287/2966 | 200/1541 | 231/2146 | 163/1143 | 168/1278 | 186/1397 | 196/1493 | 214/1756 | 227/2090 | 104/745 | 107/789 | 104/899 | 65/538 | 287/2964 |
| 0.3 | 287/2966 | 163/1195 | 215/1862 | 163/1135 | 166/1254 | 179/1318 | 164/1213 | 191/1472 | 191/1585 | 101/720 | 103/751 | 63/501 | 65/538 | 287/2964 |
| 0.4 | 287/2966 | 147/1044 | 203/1596 | 163/1127 | 163/1240 | 171/1256 | 148/1076 | 169/1260 | 133/940 | 101/704 | 102/722 | 39/306 | 64/532 | 287/2964 |
| 0.5 | 287/2966 | 135/910 | 194/1392 | 163/1123 | 163/1225 | 165/1189 | 136/934 | 151/1076 | 71/429 | 101/688 | 97/695 | 32/194 | 64/532 | 287/2964 |
| 0.6 | 287/2965 | 121/765 | 184/1245 | 163/1117 | 162/1210 | 157/1135 | 119/801 | 123/893 | 26/131 | 101/673 | 97/668 | 22/112 | 64/532 | 287/2964 |
| 0.7 | 287/2957 | 94/606 | 165/1118 | 163/1103 | 161/1192 | 150/1080 | 89/628 | 108/727 | 9/36 | 101/655 | 92/650 | 12/61 | 64/532 | 287/2964 |
| 0.8 | 212/1709 | 59/355 | 146/971 | 163/1092 | 159/1170 | 140/1001 | 54/355 | 76/543 | 2/11 | 98/629 | 84/624 | 2/25 | 64/532 | 287/2964 |
| 0.9 | **22/93** | 17/112 | 115/748 | 162/1080 | 151/1144 | 124/872 | 10/81 | 52/328 | 0/2 | 91/596 | 78/577 | 0/4 | 64/532 | 287/2963 |
| 0.95 | 0/1 | 0/1 | 83/506 | 159/1062 | 144/1112 | 105/754 | 0/0 | 28/184 | 0/0 | 87/565 | 73/541 | 0/0 | 64/532 | 287/2963 |

### Best cells, converted to precision (base rate 8.8%)

| Trigger | TP/FP | Precision | 95% CI | Lift |
|---|---|---|---|---|
| **FinMarBa_indecisive ≤ 0.1** | 22/93 | **19.1%** | [13.0%, 27.3%] | **2.2×** |
| positive ≥ 0.8 (current live gate) | 59/355 | 14.3% | [11.2%, 17.9%] | 1.6× |
| nocoref_neutral_filter ≥ 0.5 | 71/429 | 14.2% | [11.4%, 17.5%] | 1.6× |
| FLANG-BERT_positive ≥ 0.95 | 83/506 | 14.1% | [11.5%, 17.1%] | 1.6× |
| distilroberta ≥ 0.95 | 87/565 | 13.3% | [10.9%, 16.2%] | 1.5× |
| DeBERTa_positive ≥ 0.95 | 105/754 | 12.2% | — | 1.4× |
| Trading-Hero ≥ 0.95 | 144/1112 | 11.5% | — | 1.3× |

### What the matrix exposes that AUC hides

- **Even the best cell is 81% false positives.** Nothing here is a standalone
  trigger. The FinMarBa cell's CI [13%, 27%] does *not* overlap the 8.8% base
  rate, so the edge is real — but it is an edge from 8.8% to ~19%, not a signal.
- **FinancialBERT_negative(inv) is degenerate:** it fires on essentially every
  row at every level (287/2964 even at the strictest cutoff). Its score has
  almost no spread — confirmation that the column is unusable, visible here in a
  way AUC alone would not show.
- **FLANG-ELECTRA has no usable dial.** From level 0.5 to 0.95 its counts barely
  move (163/1123 → 159/1062): its probabilities pile up at the extremes, so
  raising the cutoff does not increase selectivity. A trigger you cannot tighten
  is not a trigger.
- **LouMcD-SDict saturates** at ~64/532 from level 0.4 upward — a lexicon count
  has too few distinct values to act as a graded threshold.
- **Several columns run dry:** `sentiment_score`, `RoBERTa`, and
  `nocoref_neutral_filter` hit 0/0 or 0/2 at level 0.95. Strict cutoffs on these
  produce no trades at all.
- **No *body* column is tradeable either** (full matrix in the companion report).
  The best body cell is `FinMarBa_Body_indecisive ≤ 0.1` at 72/513 = **12.3%**
  (1.40×) — well below the headline FinMarBa cell's 19.1% (2.17×). The instructive
  failure is `FinancialBERT_Body_sentiment_score ≥ 0.95`: it wins the body-vs-head
  AUC comparison outright, yet fires on **1753 rows — 54% of the corpus** — at
  11.0% precision. Ranking ability and trigger quality are different properties,
  and this is a clean example of a column with the first and not the second.

---

### The veto test: FinMarBa as a second condition on the live gate

Since FinMarBa's value is as an anti-signal, the direct test is whether adding it
to the existing gate improves the fire quality. Base rate 8.8%:

| Trigger | TP/FP | Precision | 95% CI | Lift | Fires |
|---|---|---|---|---|---|
| `positive ≥ 0.8` alone (current gate) | 59/355 | 14.3% | [11.2%, 17.9%] | 1.62× | 414 |
| `indecisive ≤ 0.1` alone | 22/93 | 19.1% | [13.0%, 27.3%] | 2.17× | 115 |
| **`positive ≥ 0.8` AND `indecisive ≤ 0.1`** | **7/21** | **25.0%** | [12.7%, 43.4%] | **2.83×** | 28 |
| **`positive ≥ 0.6` AND `indecisive ≤ 0.1`** | **16/50** | **24.2%** | [15.5%, 35.8%] | **2.75×** | 66 |
| `positive ≥ 0.8` AND `indecisive ≤ 0.2` | 53/303 | 14.9% | [11.6%, 19.0%] | 1.69× | 356 |
| `positive ≥ 0.8` AND `indecisive ≤ 0.3` | 59/354 | 14.3% | [11.2%, 18.0%] | 1.62× | 413 |

> **Note (2026-07-21):** the full-corpus numbers below are valid — FinMarBa and
> FinBERT are both externally trained, so no row here is in-sample for either.
> §2.6 confirms this increment holds (+10.6pp, CI excluding zero) and separates
> what is established from what is still unknown.

Adding the FinMarBa veto to the `positive` gate raises precision from 14.3% to
25.0%, cutting fires by 93% (414 → 28) while retaining the better ones. Note two
things a statistician would insist on:

- The veto only works at a **tight** cutoff. At `indecisive ≤ 0.2` or `≤ 0.3` the
  benefit vanishes (14.9%, 14.3%) because almost nothing is being filtered. The
  effect lives entirely in the extreme tail of the indecisive distribution.
- The evidence is thin in absolute terms: 28 fires, CI reaching down to 12.7%.
  The `≥ 0.6` variant (66 fires) is the better-supported version and is the one
  §2.6 tests formally.

---

## 2.5 Out-of-sample test (added 2026-07-20)

The 594 rows arriving after 2026-06-20 were never part of the keyword model's
training set, and `ArrivalDate >= 2026-06-20` selects them exactly (0 false
includes, 0 misses — the training corpus carries no ArrivalD ate). **578 have a
target, with 74 movers ≥30% = 12.8% base rate.** This is the only subset where
every predictor — keyword model included — is being graded on unseen data.

### Honest AUC ranking

| Column | AUC @≥30 | 95% CI | vs full corpus |
|---|---|---|---|
| **model_proba_hl** (headline kw model) | **0.672** | [0.602, 0.743] | 0.864 → in-sample was inflated |
| **model_proba** (body kw model) | **0.669** | [0.598, 0.740] | 0.892 → in-sample was inflated |
| MktCap (inv) | 0.658 | [0.586, 0.729] | 0.631 |
| model_proba_PLUS_kw_mult_nb (combo) | 0.657 | [0.585, 0.728] | 0.892 |
| Float (inv) | 0.653 | [0.581, 0.724] | 0.677 |
| FinMarBa_indecisive (inv) | 0.581 | [0.509, 0.653] | 0.631 |
| nocoref_neutral_filter | 0.580 | [0.504, 0.656] | 0.604 |
| FLANG-ELECTRA_sentiment_score | 0.568 | [0.496, 0.640] | 0.607 |

The keyword model lands at **0.669–0.672**, almost exactly the 0.65–0.69 this
report originally predicted from the 07-13 walk-forward, and consistent with the
0.676 measured then. It is the best single predictor on honest data — though note
its CI overlaps MktCap's and Float's, so "keyword model beats float" is suggested,
not established. What *is* established is that it beats every encoder: the
encoder CIs (upper bound ≤0.66) sit almost entirely below the keyword point
estimates.

### Honest trigger precisions (base rate 12.8%)

| Trigger | TP/FP | Precision | 95% CI | Lift |
|---|---|---|---|---|
| **model_proba_hl ≥ 0.7** | 17/36 | **32.1%** | [21.1%, 45.5%] | **2.51×** |
| **combo ≥ 0.6 — the LIVE rule** | 28/64 | **30.4%** | [22.0%, 40.5%] | **2.38×** |
| model_proba ≥ 0.6 | 29/70 | 29.3% | [21.2%, 38.9%] | 2.29× |
| nocoref_neutral_filter ≥ 0.5 | 24/86 | 21.8% | [15.1%, 30.4%] | 1.70× |
| positive ≥ 0.8 (FinBERT gate) | 16/73 | 18.0% | [11.4%, 27.2%] | 1.40× |
| **FinMarBa_indecisive ≤ 0.1** | 4/26 | **13.3%** | [5.3%, 29.7%] | **1.04×** |

**The live combo gate is validated.** `model_proba_PLUS_kw_mult_nb ≥ 0.6` — the
rule Orchestrator 5.1 already runs — converts a 12.8% base rate into 30.4%
precision on data the model never saw, with a CI whose lower bound (22.0%) sits
clearly above the base rate. That is the one trigger in this entire study with
both a real effect and enough fires (92) to trust it.

**On FinMarBa in this block** (corrected 2026-07-21): the cells below are what
originally prompted the retraction. They are **too small to support any
conclusion** — see §2.6. Note also that this block is *not* the right test set
for FinMarBa, which is externally trained and therefore fairly evaluated on all
3253 rows.

| Trigger | TP/FP | Precision | Lift | fires |
|---|---|---|---|---|
| positive ≥ 0.6 alone | 32/162 | 16.5% | 1.29× | 194 |
| positive ≥ 0.6 **AND** indecisive ≤ 0.1 | 3/16 | 15.8% | 1.23× | **19** |
| combo ≥ 0.6 alone | 28/64 | 30.4% | 2.38× | 92 |
| combo ≥ 0.6 **AND** indecisive ≤ 0.1 | 0/1 | 0% | — | **1** |

Nineteen fires and one fire respectively. Reading "the veto fails" out of a
single observation was the error; the correct statement is that this block
cannot answer the question.

### Scoring provenance (verification)

- Body pass reproduced the 2712 existing rows' `model_proba`, `kw_mult_nb`,
  `kw_add_*`, `n_kw_hits` and `model_proba_pctl` at **max |diff| = 0.00000000**.
- The one column that differed, `model_proba_PLUS_kw_mult_nb`, differs on exactly
  the 2675 rows *with* a target and matches on the 37 without — the documented
  **out-of-fold vs full-fit** distinction from `add_combo_score.py`, not an
  error. Existing OOF values were preserved; only empty cells were filled.
- 67 rows carried a **live combo written by Orchestrator 5.1** (07-15/07-16).
  These matched the offline recomputation at **max |diff| = 0.00000000** —
  an end-to-end confirmation that the live scoring path is faithful.
- Headline pass reproduced `model_proba_hl` and the `kw_add_*_hl` family exactly;
  the `kw_mult_nb_hl` family drifts by ≤0.0006 (the documented prevalence-source
  difference — monotone across rows, so rank-based AUC is unaffected).

---

## 2.6 Does FinMarBa add value *on top of* a gate? (added 2026-07-21)

Standalone predictiveness and incremental value are different questions. The
deployment-relevant one is: among rows a gate already fires on, does the
condition raise precision by more than noise? Script:
`statsByClaude/conditional_value.py`; delta CIs are Newcombe intervals on the
difference of two proportions, which behave properly at small counts.

**Which block is valid depends on the gate**, because provenance differs:

- `positive` gate + FinMarBa condition — **both externally trained, so the FULL
  corpus is the valid test.**
- `combo` gate + FinMarBa condition — the *gate* is the in-house keyword model,
  which trained on the early block, so only the **recent** block is valid there.

| gate | block | gate alone | gate AND FinMarBa ≤ 0.1 | Δ precision (95% CI) |
|---|---|---|---|---|
| **positive ≥ 0.6** | **FULL (valid)** | 121/765 = 14% (1.55×) | **16/50 = 24% (2.75×)** | **+10.6% [+1.5%, +22.3%] → helps** |
| positive ≥ 0.6 | early | 89/603 = 13% | 13/34 = 28% | +14.8% [+3.7%, +29.1%] helps |
| positive ≥ 0.6 | recent | 32/162 = 16% | 3/16 = 16% | −0.7% [−12.5%, +21.5%] n.s. |
| positive ≥ 0.8 | FULL (valid) | 59/355 = 14% | 7/21 = 25% | +10.7% [−2.1%, +29.4%] n.s. |
| combo ≥ 0.6 | FULL (gate inflated) | 148/245 = 38% | 10/3 = 77% | +39.3% [+11.6%, +54.9%] — *not usable* |
| **combo ≥ 0.6** | **recent (valid)** | 28/64 = 30% | **0/1** | −30.4% [−40.5%, +49.4%] **n.s.** |

### What this establishes

1. **On the FinBERT `positive ≥ 0.6` gate, FinMarBa genuinely helps.** On the
   full corpus — the correct test set for two externally-trained models —
   precision rises 14% → 24%, and the Newcombe CI on the difference
   **[+1.5%, +22.3%] excludes zero**. 66 fires behind it. This is a real,
   modest, properly-evidenced increment. The original recommendation was sound.
2. **Its value on the live combo gate is unknown, not disproven.** The full-corpus
   figure (77%) is unusable because the *gate* is in-sample there; the valid
   recent block has **one fire**. Nothing can be concluded in either direction.
3. **The recent block was never capable of settling this.** A two-proportion
   power calculation says detecting a 2× lift at 80% power needs roughly **230
   fires per arm**; the recent block supplies 1–19. My 2026-07-20 retraction
   read a negative result out of a sample that could not have produced a
   positive one.

### Consequence

The veto is supported where it has been properly tested (on the FinBERT gate)
and untested where it would actually be deployed (on the combo gate). Since the
combo gate already achieves 30.4% precision alone, and the veto's only observed
interaction with it is a single suppressed fire, **the correct action is to
collect more data before layering it on the live gate** — not to deploy it and
not to discard it.

---

## 3. Per-class analysis (BroadClass)

Within-class AUC: the same rank test computed **inside** each PR class, which
asks "given that this is an FDA PR, does the score separate the movers?" Columns
are pre-oriented globally, so a within-class value **below 0.50 means the signal
reverses inside that class**. `~` = fewer than 8 movers in the class → unreliable.

### AUC matrix, movers = `fromPR_High(%) >= 30%`

| class | n | movers | base | FinMarBa(inv) | positive | FLANG-BERT | FLANG-ELECTRA | Trading-Hero | DeBERTa | sentiment_score | ModernBERT | nocoref_nf | distilroberta | finbert-tone | RoBERTa | LouMcD | FinancialBERT(inv) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 683 | 66 | 9.7% | 0.55 | 0.59 | 0.59 | **0.62** | 0.58 | 0.54 | 0.59 | 0.58 | 0.50 | 0.59 | 0.55 | 0.52 | 0.53 | 0.53 |
| Earnings | 508 | 26 | 5.1% | 0.57 | 0.55 | 0.40 | 0.63 | 0.48 | 0.50 | 0.52 | 0.46 | 0.65 | **0.69** | 0.55 | 0.56 | 0.53 | 0.47 |
| Corporate | 373 | 21 | 5.6% | **0.68** | 0.63 | 0.68 | 0.58 | 0.55 | 0.68 | 0.64 | 0.64 | 0.63 | 0.58 | 0.64 | 0.61 | 0.55 | 0.57 |
| Other | 328 | 19 | 5.8% | 0.59 | 0.61 | 0.64 | 0.57 | 0.63 | 0.60 | 0.62 | 0.61 | **0.66** | 0.55 | 0.55 | 0.56 | 0.51 | 0.51 |
| FDA | 287 | 19 | 6.6% | **0.72** | 0.68 | 0.65 | 0.67 | 0.66 | 0.71 | 0.68 | 0.67 | 0.65 | 0.71 | 0.68 | 0.70 | 0.57 | 0.52 |
| MA | 277 | 45 | 16.2% | **0.65** | 0.56 | 0.59 | 0.59 | 0.59 | 0.55 | 0.56 | 0.59 | 0.62 | 0.56 | 0.56 | 0.55 | 0.48 | 0.43 |
| Clinical | 257 | 24 | 9.3% | 0.59 | 0.62 | 0.59 | 0.57 | 0.57 | 0.62 | 0.63 | 0.65 | 0.63 | **0.66** | 0.61 | 0.63 | 0.54 | 0.55 |
| Financing | 229 | 38 | 16.6% | 0.52 | 0.54 | **0.57** | 0.55 | 0.55 | 0.53 | 0.53 | 0.47 | 0.52 | 0.49 | 0.49 | 0.54 | 0.53 | 0.56 |
| Compliance | 127 | 15 | 11.8% | 0.61 | 0.57 | 0.51 | **0.68** | 0.51 | 0.61 | 0.52 | 0.65 | 0.49 | 0.55 | 0.57 | 0.48 | 0.53 | 0.56 |
| IP | 69 | 9 | 13.0% | 0.67 | 0.54 | 0.60 | 0.56 | 0.56 | 0.47 | 0.52 | 0.69 | 0.57 | 0.57 | **0.75** | 0.59 | 0.55 | 0.48 |
| Crypto | 65 | 5 | 7.7% | 0.87~ | 0.78~ | 0.73~ | 0.84~ | 0.76~ | 0.67~ | 0.70~ | 0.75~ | 0.89~ | 0.61~ | 0.64~ | 0.57~ | 0.71~ | 0.62~ |
| Legal | 50 | 0 | 0.0% | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

### AUC matrix, companion threshold `>= 15%` (stability check)

| class | n | movers | base | FinMarBa(inv) | positive | FLANG-BERT | FLANG-ELECTRA | Trading-Hero | DeBERTa | sentiment_score | ModernBERT | nocoref_nf | distilroberta | finbert-tone | RoBERTa | LouMcD | FinancialBERT(inv) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 683 | 151 | 22.1% | 0.54 | 0.56 | 0.56 | 0.55 | 0.58 | 0.53 | 0.56 | 0.53 | 0.51 | 0.54 | 0.52 | 0.53 | 0.51 | 0.49 |
| Earnings | 508 | 80 | 15.7% | 0.59 | 0.59 | 0.50 | 0.59 | 0.51 | 0.48 | 0.55 | 0.50 | 0.60 | 0.61 | 0.58 | 0.50 | 0.52 | 0.47 |
| Corporate | 373 | 44 | 11.8% | 0.67 | 0.64 | 0.63 | 0.62 | 0.56 | 0.65 | 0.65 | 0.62 | 0.65 | 0.62 | 0.59 | 0.61 | 0.54 | 0.57 |
| Other | 328 | 45 | 13.7% | 0.57 | 0.58 | 0.60 | 0.55 | 0.56 | 0.60 | 0.58 | 0.62 | 0.53 | 0.56 | 0.55 | 0.57 | 0.55 | 0.53 |
| FDA | 287 | 49 | 17.1% | 0.69 | 0.64 | 0.62 | 0.60 | 0.60 | 0.62 | 0.65 | 0.63 | 0.62 | 0.63 | 0.59 | 0.58 | 0.52 | 0.55 |
| MA | 277 | 87 | 31.4% | 0.60 | 0.58 | 0.52 | 0.57 | 0.57 | 0.57 | 0.58 | 0.58 | 0.62 | 0.57 | 0.57 | 0.56 | 0.52 | 0.50 |
| Clinical | 257 | 52 | 20.2% | 0.63 | 0.61 | 0.59 | 0.61 | 0.65 | 0.59 | 0.61 | 0.63 | 0.61 | 0.61 | 0.62 | 0.56 | 0.54 | 0.55 |
| Financing | 229 | 77 | 33.6% | 0.57 | 0.59 | 0.53 | 0.58 | 0.55 | 0.52 | 0.59 | 0.53 | 0.57 | 0.55 | 0.50 | 0.58 | 0.53 | 0.48 |
| Compliance | 127 | 35 | 27.6% | 0.44 | 0.45 | 0.47 | 0.54 | 0.51 | 0.44 | 0.45 | 0.54 | 0.48 | 0.56 | 0.46 | 0.48 | 0.54 | 0.49 |
| IP | 69 | 13 | 18.8% | 0.56 | 0.51 | 0.63 | 0.52 | 0.56 | 0.45 | 0.49 | 0.65 | 0.54 | 0.53 | 0.72 | 0.57 | 0.53 | 0.47 |
| Crypto | 65 | 13 | 20.0% | 0.59 | 0.54 | 0.57 | 0.69 | 0.65 | 0.51 | 0.52 | 0.57 | 0.66 | 0.38 | 0.49 | 0.36 | 0.58 | 0.49 |
| Legal | 50 | 3 | 6.0% | 0.89~ | 0.58~ | 0.84~ | 0.81~ | 0.77~ | 0.59~ | 0.70~ | 0.41~ | 0.90~ | 0.67~ | 0.85~ | 0.79~ | 0.67~ | 0.64~ |

### Class hardness (@≥30%)

| class | n | movers | best column | best AUC | median AUC across 14 |
|---|---|---|---|---|---|
| **Financing** | 229 | 38 | FLANG-BERT_positive | **0.57** | 0.53 |
| **Commercial** | 683 | 66 | FLANG-ELECTRA | **0.62** | 0.57 |
| MA | 277 | 45 | FinMarBa(inv) | 0.65 | 0.56 |
| Clinical | 257 | 24 | distilroberta | 0.66 | 0.61 |
| Other | 328 | 19 | nocoref_nf | 0.66 | 0.59 |
| Compliance | 127 | 15 | FLANG-ELECTRA | 0.68 | 0.55 |
| Corporate | 373 | 21 | FinMarBa(inv) | 0.68 | 0.63 |
| Earnings | 508 | 26 | distilroberta | 0.69 | 0.54 |
| **FDA** | 287 | 19 | FinMarBa(inv) | **0.72** | **0.68** |
| IP | 69 | 9 | finbert-tone | 0.75 | 0.57 |
| Crypto | 65 | 5 | nocoref_nf | 0.89~ | 0.72 |
| Legal | 50 | 0 | — | — | — |

### Which column wins which class

| column | classes won | where |
|---|---|---|
| FinMarBa_indecisive (inv) | **3** | Corporate (0.68), FDA (0.72), MA (0.65) |
| FLANG-ELECTRA_sentiment_score | 2 | Commercial (0.62), Compliance (0.68) |
| distilroberta_sentiment_score | 2 | Earnings (0.69), Clinical (0.66) |
| FLANG-BERT_positive | 1 | Financing (0.57) |
| nocoref_neutral_filter | 1 | Other (0.66) |
| finbert-tone_sentiment_score | 1 | IP (0.75) |

### Per-class TP/FP for four fixed triggers (@≥30%)

| class | movers | positive≥0.8 | FLANG-BERT_pos≥0.95 | FinMarBa_indec≤0.1 | nocoref_nf≥0.5 |
|---|---|---|---|---|---|
| Commercial | 66 | 18/116 = 13% | 26/196 = 12% | 8/35 = 19% | 14/149 = 9% |
| Earnings | 26 | 3/28 = 10% | 1/16 = 6% | 0/2 = 0% | 7/40 = 15% |
| Corporate | 21 | 4/28 = 12% | 5/44 = 10% | **3/6 = 33%** | 4/53 = 7% |
| Other | 19 | 6/41 = 13% | 6/57 = 10% | 0/11 = 0% | 5/48 = 9% |
| FDA | 19 | 5/29 = 15% | 7/48 = 13% | 2/12 = 14% | 9/37 = 20% |
| MA | 45 | 6/27 = 18% | 16/53 = 23% | **4/6 = 40%** | 11/45 = 20% |
| Clinical | 24 | 9/45 = 17% | 7/33 = 18% | 2/7 = 22% | 7/23 = 23% |
| Financing | 38 | 5/17 = 23% | 8/22 = 27% | 2/9 = 18% | **8/21 = 28%** |
| Compliance | 15 | 3/14 = 18% | 0/9 = 0% | 0/0 | 1/2 = 33% |
| IP | 9 | 0/4 = 0% | 4/15 = 21% | 1/3 = 25% | 2/5 = 29% |
| Crypto | 5 | 0/5 = 0% | 3/12 = 20% | 0/2 = 0% | 3/4 = 43% |
| Legal | 0 | 0/1 = 0% | 0/1 = 0% | 0/0 | 0/2 = 0% |
| **ALL** | 287 | 59/355 = 14% | 83/506 = 14% | 22/93 = 19% | 71/429 = 14% |

### Per-class findings

- **Financing is the hardest class to score** — best column only 0.57, median
  0.53, and it stays weak at ≥15% (best 0.59). This is economically coherent:
  offerings and financings have one of the *highest* mover rates (16.6%) but
  whether the stock runs has nothing to do with how upbeat the wording is. Tone
  carries no information here; something else drives the move.
- **Commercial is the second hardest and the most expensive.** Best 0.62, and
  because it is the largest class (683 rows) it generates the largest false
  positive load in every trigger column (FLANG-BERT fires 26/196 there).
- **Compliance is deceptive.** It looks acceptable at ≥30 (0.68) but that rests
  on 15 movers; at ≥15 it collapses to a best of 0.56 with **several columns
  below 0.50** — the sentiment signal partially reverses. Treat as unreliable.
- **FDA is the *easiest* major class**, not the hardest — median 0.68 with all
  14 columns between 0.52 and 0.72, and it holds at ≥15 (median ~0.62). This
  directly contradicts the prior worry that FPB-trained encoders are blind to
  biotech: whatever their weakness on individual headlines, FDA PRs as a class
  are where sentiment separates movers best.
- **No model dominates; there are per-class specialists.** FinMarBa(inv) wins
  three classes, distilroberta and FLANG-ELECTRA two each. Caution: with 19–66
  movers per class, per-class AUCs carry roughly ±0.10 of noise, so these
  "wins" are suggestive, not established.
- **TP/FP confirms the class effect where it counts.** `FinMarBa_indecisive ≤
  0.1` inside MA gives 4/6 = 40% precision (4.5× base) and inside Corporate 3/6
  = 33% — versus 19% globally. But the Wilson CI on 4/6 is **[17%, 69%]**: this
  is six fires. It is a hypothesis to test forward, not a validated rule.
- Meanwhile the current live gate `positive ≥ 0.8` goes **0-for-4 on IP** and
  **3/28 on Earnings** — it is firing in classes where it has no edge.

### Earnings: the one class where the BODY beats the headline (added 2026-07-20)

Re-running the per-class analysis on the body-scored columns
(`class_conditional_predictors.py --report-suffix _body`) reverses the picture for
exactly one class. **Earnings** median AUC across columns goes **0.54 (headline) →
0.67 (body)**, best 0.69 → 0.74. Tested with the same paired bootstrap, inside the
Earnings class only:

| encoder | HEAD | BODY | Δ | 95% CI |
|---|---|---|---|---|
| DeBERTa | **0.469** | 0.738 | **+0.269** | [+0.079, +0.452] ✓ |
| Trading-Hero | **0.476** | 0.725 | **+0.249** | [+0.064, +0.426] ✓ |
| finbert-tone | 0.552 | 0.687 | +0.135 | [−0.008, +0.286] |
| distilroberta | 0.688 | 0.741 | +0.053 | [−0.085, +0.193] |

Two of four CIs exclude zero and all four point the same way. Note the headline
AUCs **below 0.50**: on earnings PRs, headline tone is not just weak, it is
slightly *backwards*. The reason is mundane — an earnings headline is
"XYZ Corp Reports Third Quarter 2026 Financial Results", a template with no
sentiment content; the beat, the miss and the guidance are in the body. This is
the only class where body scoring is worth pursuing, and with 26 movers it is a
hypothesis to validate forward, not a rule to deploy. **FDA moves the opposite
way** (median 0.68 → 0.61), and **Financing — which I predicted would favour the
body, since offering terms live there — showed no improvement at all.**

---

## 4. Main recommendations

1. **Do not deploy any of the 11 encoders as a standalone trigger.** The best of
   them ties your existing FinBERT `positive` at AUC 0.610 and delivers 14%
   precision — a 1.6× lift over an 8.8% base rate, with 86% false positives.
   ~70 columns of new model output produced no measurable improvement.
2. **Retire FinancialBERT and LouMcD-SDict from *headline* scoring — but they are
   not broken models** *(revised 2026-07-20; supersedes the original "retire them
   outright")*. On headlines the original finding stands: FinancialBERT's AUC CI
   includes 0.500 and it fires on ~100% of rows at every cutoff; LouMcD-SDict
   saturates and cannot be thresholded. **What was wrong was the diagnosis.** Both
   come alive on body text: FinancialBERT goes **0.505 → 0.576** (the one head-vs-
   body win that survives Bonferroni correction, with all three of its channels
   agreeing) and LouMcD-SDict 0.518 → 0.562. The mechanism is visible in the
   labels: `FinancialBERT_label` is `neutral` on **3288 of 3306 headlines**
   (std 0.071) versus 2127 positive / 1143 neutral on bodies (std 0.464, 1265
   distinct values). **They were starved of text, not defective** — a 14-word
   headline is below their working minimum. Retire them from the headline path;
   keep FinancialBERT in mind if a body-scoring context ever arises. This does not
   make it deployable: its best body trigger fires on 54% of the corpus at 11.0%
   precision (§2).
3. **FinMarBa's `indecisive` channel: supported, but hold deployment pending
   data** *(reinstated 2026-07-21 — the 07-20 retraction was itself wrong; see
   §2.6)*. Standalone it is the strongest encoder-derived signal (AUC 0.631
   [0.595, 0.667] on the full corpus, which **is** its valid test set — FinMarBa
   is externally trained). As a condition on the FinBERT `positive ≥ 0.6` gate it
   delivers a real increment: **14% → 24% precision, Δ +10.6%, CI [+1.5%,
   +22.3%]** on 66 fires. But its effect on the *combo* gate — where you would
   actually deploy it — rests on a single observation and is genuinely unknown.
   Detecting a 2× effect needs ~230 fires per arm; we have 1.
   **Action: keep the live combo gate `model_proba_PLUS_kw_mult_nb ≥ 0.6`
   unchanged** (validated out-of-sample at **30.4% precision, 2.38× lift, 92
   fires**), log the FinMarBa value alongside each fire, and revisit once ~200+
   gate-fires have accumulated. `model_proba_hl ≥ 0.7` (32.1%, 2.51×, 53 fires)
   is a candidate complement.
4. **Make the gate class-aware.** The single most actionable pattern:
   - Suppress or heavily discount sentiment fires in **Financing** and
     **Commercial** (sentiment has no edge there, and Commercial supplies the
     bulk of the false positives).
   - In **FDA, Corporate, MA**, require low FinMarBa indecisiveness.
   - Do not fire on `positive ≥ 0.8` in **IP** or **Earnings** (0-for-4 and
     3/28 respectively).
   Because the per-class samples are small, implement this as a *veto layer*
   (removing bad fires) rather than a new trigger — vetoes need much less
   statistical evidence to be safe than triggers do.
5. **Stop optimizing encoder sentiment; the ceiling is here — and it is not a
   headline-vs-body problem.** Fourteen independently-trained models all land in
   AUC 0.53–0.63 on headlines, and plain **`Float` inverted scores 0.677** — higher
   than every one of them. Scoring the *body* with the same 11 encoders does not
   lift that ceiling: the best body column reaches 0.595 and the best head+body
   combination 0.640, still below `Float` alone. The information about which
   small-cap PR pops is largely in float/market-cap/liquidity structure, not in
   PR tone in either field. Effort is better spent on the size/liquidity feature
   family and on the event class than on a twelfth encoder or a third text field.
8. **Do not add body encoder scoring to the live path** *(added 2026-07-20)*. It
   loses 9 channel pairs to 1 at Bonferroni, yields no tradeable trigger, adds at
   most **+0.019 AUC** in combination with the headline, and costs **10× the
   inference time** (measured from the corpus's own `_elapsed_ms` columns: 3205 ms
   vs 319 ms per PR for all 11 encoders). In a path whose entire purpose is
   reacting before the move, 3.2 s of encoder inference settles the question on
   its own. The single exception worth exploring is **Earnings-only** body scoring
   (§3) — ~15% of PRs, which would cut the 10× penalty to roughly 1.5× overall.
6. **~~Score the 594 new rows~~ — DONE 2026-07-20.** Both passes complete;
   output is `PRstats_..._Classed_kwScored.tsv`. The prediction held: the keyword
   model landed at **0.669–0.672**, inside the forecast 0.65–0.69 band. It is the
   best honest single predictor — modestly ahead of `MktCap`/`Float` (CIs
   overlap) and clearly ahead of every encoder. See §2.5.
7. **Match the evaluation set to each model's training provenance.** This is the
   methodological lesson of the 07-20/07-21 correction cycle. The 578-row recent
   block is the valid test set for the **keyword family only**, because that is
   the only model trained on rows in this file. For the 11 encoders and FinBERT —
   all externally trained — **the full 3253 rows are valid**, and restricting
   them to 578 throws away 82% of the evidence and produces underpowered,
   misleading nulls. When a gate and a condition have different provenance, the
   valid block is the stricter of the two. Before reading any future result as a
   negative, check the fire count: a cell with 1–19 fires cannot refute anything
   (see the power calculation in §2.6).

---

### Priority order (revised 2026-07-21)

**Leave the live gate unchanged for now** — it is validated at 2.38× lift
out-of-sample, and the one candidate addition (FinMarBa) is supported on a
different gate but untested on this one. Start logging FinMarBa's value with
each fire so the question becomes answerable. Then, in order: **recommendation
4's Financing/Commercial suppression** (best open idea; test it with
`conditional_value.py` before deploying), followed by recommendations 1, 2, 5
and 7 — decisions to *stop* spending effort, which cost nothing to adopt.

---

## 5. Caveats

- **287 movers** set the resolution of this study. AUC differences below ~0.04
  globally, and below ~0.10 within a class, are not distinguishable from noise.
- **Per-class cells with `~` (Crypto, Legal, and any <8 movers) must not be
  quoted.** Crypto's 0.89 rests on 5 movers.
- Everything here is **in-corpus**: rows are ranked against each other after the
  fact. Real deployment adds slippage, fill, and timing costs that a rank test
  cannot see; the 07-13 walk-forward showed a keyword AUC drop of 0.907 → 0.676
  when moved to unseen dates. Expect similar decay from these numbers.
- ~~Encoder scores are computed on the **headline** only. A body-scored variant
  may behave differently and is untested here.~~ **Resolved 2026-07-20** — the
  body variant was scored and tested; see `report-Body-vs-Headline-11encoders.md`.
  The headline wins.
- **The body variant was truncated to its first 200 words** by
  `BodyScore11encoders.py` (~260–290 tokens), while the median PR body in this
  corpus is **400 words** (p90 673). Roughly **half of a typical PR was never
  seen** by those encoders, so the body results measure a *truncated-body* encoder,
  not a full-document one. A chunk-and-pool or long-context approach is untested
  and could plausibly change the Earnings result, where financial tables tend to
  sit past the 200-word mark.

---

## Reproduce

```
# global all-column ranking (+ OOS-only companion)
/home/tom/venv/bin/python analyze_frompr_predictors.py \
  ../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv
/home/tom/venv/bin/python analyze_frompr_predictors.py \
  ../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv \
  --subset-null model_proba --report-suffix _OOSonly

# TP/FP matrix (see commands.txt for the full 14-column invocation)
/home/tom/venv/bin/python false_positive_count.py --input <tsv> \
  --columns-name <14 cols> --true-positive-threshold 30 \
  --levels 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 0.95 \
  --invert FinMarBa_indecisive FinancialBERT_negative

# per-class analysis
/home/tom/venv/bin/python class_conditional_predictors.py \
  ../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv

# --- body-vs-headline (2026-07-20) ---
# merge the 70 _Body_ columns onto the canonical kw-scored TSV, then compare
/home/tom/venv/bin/python merge_body_encoder_cols.py
/home/tom/venv/bin/python head_vs_body.py --n-boot 2000
```

Generated reports: `outputs/fromprhigh_all_predictors_report.md`,
`outputs/fromprhigh_all_predictors_report_OOSonly.md`,
`outputs/PRstats_..._TPFP_fromprhigh_tp30.tsv`,
`outputs/fromprhigh_class_conditional_report.md`,
`outputs/head_vs_body_encoders_report.md`,
`outputs/fromprhigh_class_conditional_report_body.md`.
