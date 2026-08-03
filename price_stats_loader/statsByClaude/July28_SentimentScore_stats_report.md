# Sentiment & keyword score statistics — 2026-07-28

Everything below is measured on the **corrected** `fromPR_High(%)` label. The
previous label was defective (§1), so no figure in this report is comparable to
one produced before 2026-07-26 unless explicitly noted.

**Honest-read rule used throughout:** all headline numbers come from the
held-out VAL block (2026-06-22 → 07-23, 777 rows) which no model saw in
training. CV and in-sample numbers are labelled as such and are not the arbiter.

---

## 1. The label defect that invalidated the previous corpus

`fromPR_HighOfDay_addOn.py` divided by the close of the 1-min bar **containing**
the PR rather than the bar before it, so the denominator already priced the news
in. The test `start < created_dt` accepts the PR's own bar (09:00:00 <
09:00:05.467). Fixed 2026-07-26 → `start < created_dt.replace(second=0, microsecond=0)`.

Reference case — ZCMD 2026-07-23, a $5.0M dilution offering:

| 1-min bar | O | H | L | C | volume |
|---|---|---|---|---|---|
| 08:59 | 2.62 | 2.62 | 2.585 | **2.599** | 6,081 |
| 09:00 (PR at 09:00:05) | 2.58 | 2.58 | 1.41 | **1.65** | 544,910 |
| 09:08 | 2.95 | **7.8097** | 2.24 | 3.69 | 1,507,530 |

`(7.8097 − 1.65)/1.65 = 373.32%` reproduced the stored value exactly. Against the
true pre-PR price of 2.599 the correct answer is **+200.5%**.

### Corpus-wide effect (3213 comparable rows)

| | rows | share |
|---|---|---|
| unchanged | 1605 | 50.0% |
| **corrected upward** (were understated) | 1222 | **38.0%** |
| corrected downward (were overstated) | 386 | 12.0% |

Median change 4.58pp · p90 23.71pp · max 824pp.

| label cut | before | after | rows changing class |
|---|---|---|---|
| ≥15% | 640 | **802** | 282 (8.8%) |
| ≥20% | 478 | 598 | 214 (6.7%) |
| ≥30% | 285 | 383 | 140 (4.4%) |

The bias was **directional**: the error equals the price move inside the PR's own
minute, i.e. exactly the reaction being measured. Fast movers were
systematically hidden.

---

## 2. Corpus and split

`04_PRstats_2026-05-04-2026-07-23_..._FromPRhigh.tsv` — 3533 rows × 190 cols.
Temporal split at **2026-06-20** (train `<`, validation `>=`); dropped 99
unlabeled and 10 Symbol+Headline duplicates first.

| split | rows | dates | ≥15% | ≥20% | ≥30% | ≥40% |
|---|---|---|---|---|---|---|
| TRAIN | 2647 | 05-04 → 06-18 | 605 (22.9%) | 450 (17.0%) | 279 (10.5%) | 212 (8.0%) |
| VAL | 777 | 06-22 → 07-23 | 263 (33.8%) | 200 (25.7%) | 140 (18.0%) | 107 (13.8%) |

### ⚠ Base-rate drift — the most consequential finding for calibration

| half-month | n | ≥15% | ≥20% | ≥30% |
|---|---|---|---|---|
| 2026-05a | 821 | 17.7% | 11.7% | 6.3% |
| 2026-05b | 651 | 22.0% | 16.1% | 9.5% |
| 2026-06a | 983 | 25.6% | 20.9% | 13.8% |
| 2026-06b | 370 | **34.3%** | 24.1% | 16.8% |
| 2026-07a | 344 | **35.5%** | 27.3% | 18.3% |
| 2026-07b | 255 | 31.0% | 23.9% | 17.3% |

The ≥15% rate roughly **doubles** across the corpus. Verified not an artifact of
the 227 late-appended rows (appended 31.0% vs original 34.1% over the same
window). Consequences:

- `keyword_scorer-2.py` pins `DEFAULT_PREVALENCE = 0.185` — **stale for both
  blocks**. Correct values: TRAIN ge15 = 605/2647 = **0.228560**, ge40 = **0.080091**.
- A model calibrated on TRAIN under-predicts on VAL; thresholds do not transfer.
- Precision/lift must be read against the **period-specific** base rate. On VAL a
  30% precision result would be *below* the 33.8% base rate.

---

## 3. Split-strategy diagnostic (TF-IDF + logistic proxy, 20 seeds)

Does a random split flatter the result?

| split | AUC | prec@10% | val base | lift |
|---|---|---|---|---|
| A. temporal (honest) | 0.7116 | 67.5% | 33.8% | 2.00× |
| B. random | 0.7277 ± 0.0201 | 59.1% | 24.4% | 2.42× |
| C. random, grouped by Symbol | 0.7064 ± 0.0143 | 57.5% | 25.4% | 2.26× |

```
B − A = +0.016   total optimism from a random split (0.8 seed-sd; 16/20 seeds beat temporal)
B − C = +0.025   attributable to ISSUER memorisation
C − A = −0.005   attributable to DAY / REGIME effects (nil, inside noise)
```

**Conclusion:** a random split is *mildly* optimistic, not catastrophic — the
whole effect is issuer memorisation (87% of rows share a symbol with another
row; COSM appears 36×). The stronger reason to keep the temporal split is
**population match**: the random split evaluates on a 24.4% mover population vs
33.8% for temporal, and production faces the recent regime. Note how lift
inverts (2.42× vs 2.00×) purely through the base-rate denominator while absolute
precision is worse.

**For tuning inside TRAIN, group CV folds by `Symbol`** — plain k-fold there is
measurably optimistic (C vs B).

---

## 4. Model training — 4 models, canonical recipe

`--task classification`, `--target-col "fromPR_High(%)"`, no `--strip-boilerplate`,
wire-boilerplate stripping default-ON. **Bodies truncated to 400 words
corpus-wide** (see §7).

| model | text | CV AUC | test AUC | train pos rate |
|---|---|---|---|---|
| ge15 | Body | **0.7528 ± 0.0259** | 0.7624 | 22.9% |
| ge40 | Body | 0.7500 ± **0.0486** | 0.6842 | 8.0% |
| ge15 | Headline | 0.7172 ± 0.0204 | 0.6589 | 22.9% |
| ge40 | Headline | 0.6985 ± 0.0438 | 0.7414 | 8.0% |

ge15 body CV 0.753 vs 0.725 for the old biased-label model.

### Walk-forward on VAL (777 held-out rows)

| model | AUC≥15 | AUC≥30 | AUC≥40 |
|---|---|---|---|
| **ge15 body** | **0.6990** | **0.7426** | **0.7405** |
| ge40 body | 0.6831 | 0.7033 | 0.7073 |
| ge15 headline | 0.6954 | 0.7139 | 0.7038 |
| ge40 headline | 0.6659 | 0.6740 | 0.6892 |

**ge40 is dead — confirmed on corrected labels.** ge15 beats ge40 *at the ≥40%
target itself* (0.7405 vs 0.7073). Spearman between their VAL scores = **0.813**:
ge40 learns the same ranking from a third of the positives with double the CV
variance. This reproduces the 2026-07-13 ge30 result (Spearman 0.771).

**Models rank the HARDER targets better than ≥15** — ge15 body: 0.699 @≥15 but
0.743 @≥30 and 0.740 @≥40. Practical read: **train at ge15, trade a higher bar.**

---

## 5. Scoring-method league table on VAL

Base rates: ≥15% 33.8% · ≥30% 18.0% · ≥40% 13.8%. Top decile n=77.

| method | channel | AUC≥15 | AUC≥30 | AUC≥40 | prec@10% | lift |
|---|---|---|---|---|---|---|
| **model_proba** | body | **0.6990** | **0.7426** | **0.7405** | 67.5% | 2.00× |
| combo_proba | body | 0.6984 | 0.7361 | 0.7305 | 68.8% | 2.03× |
| kw_add_log | head | 0.6962 | 0.7158 | 0.7071 | 63.6% | 1.88× |
| kw_add_first | head | 0.6957 | 0.7161 | 0.7085 | 62.3% | 1.84× |
| model_proba | head | 0.6954 | 0.7139 | 0.7038 | 64.9% | 1.92× |
| kw_mult_nb_log | head | 0.6888 | 0.6723 | 0.6876 | **70.1%** | **2.07×** |
| kw_mult_nb | head | 0.6882 | 0.6723 | 0.6873 | **70.1%** | **2.07×** |
| kw_mult_nb | body | 0.6754 | 0.6929 | 0.6769 | 53.2% | 1.57× |
| kw_mult_nb_log | body | 0.6737 | 0.7011 | 0.6903 | 48.1% | 1.42× |
| kw_add_lin | body | 0.6601 | 0.7033 | 0.7004 | 57.1% | 1.69× |
| kw_add_log | body | 0.6585 | 0.7031 | 0.7069 | 62.3% | 1.84× |
| kw_add_first | body | 0.6451 | 0.6930 | 0.7016 | 59.7% | 1.76× |

### The combo adds nothing

```
combo − model_proba   @≥15: −0.0006   @≥30: −0.0065   @≥40: −0.0100
```

Identical average precision (0.5448 vs 0.5453). Reproduces the 2026-07-13
verdict on an independent model generation. **Structural cause:** the combo is
fit on TRAIN where `model_proba` is in-sample (train AUC 0.955 vs 0.762 held
out), so it learns weights for a feature discriminating at 0.95 and then meets
one discriminating at 0.70. Its reported TRAIN OOF AUC of 0.933 is inflated and
should not be quoted. A useful combo needs out-of-fold `model_proba` on TRAIN.

### Head vs body — the guide's rule does not survive walk-forward

| method | Δ (head − body) @≥15 |
|---|---|
| kw_add_first | **+0.051** |
| kw_add_log | **+0.038** |
| kw_add_lin | +0.035 |
| kw_mult_nb_log | +0.015 |
| kw_mult_nb | +0.013 |
| model_proba | −0.004 |

Body wins on training CV (0.753 vs 0.717, matching the documented 0.03–0.05
band) but **every `kw_*` method reverses out-of-sample**. `kw_mult_nb*` are the
*worst* body methods and the *best* headline methods by precision — the channel
flips their ranking entirely. Body/headline `model_proba` correlate at only
Spearman **0.592**, i.e. genuinely different signals.

⚠ The top decile is 77 rows, so 70.1% vs 67.5% is two PRs. The significance test
this asked for is below, and it does not confirm the table.

---

### 5.1 Precision at deployable volume — the four models head-to-head

Added 2026-07-28. The tables above rank *methods* at one volume; this ranks the
four **models** across four volumes with Wilson 95% intervals, using
`tfidf` `model_proba` only. Tool: `tools/keyword/walkforward_eval.py`, extended
with `--top-fracs`, Wilson/Newcombe intervals, a matched-volume paired block and
an absolute-cutoff view.

Scope is the four models in `model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN/`
— the only ones trained on the **corrected** `fromPR_High(%)` label (each
`model_performance_summary.csv`: n_train 2117 + n_test 530 = 2647, train pos rate
0.2286). The twelve directories under `model_trained_on_PR-stats-2026-05-03to06-19/`
learned the pre-2026-07-26 biased label or the legacy `DailyHigh` target and are
excluded. VAL (777 rows from 2026-06-20) is the only honest test set for all four.

**Target ≥15% (base 33.8%, 263 positives)** — precision, lift, Wilson 95%:

| model | top 5% (n=38) | top 10% (n=77) | top 20% (n=155) | top 30% (n=233) |
|---|---|---|---|---|
| **ge15 body** | 71.1% 2.10× [55,83] | **67.5% 2.00×** [57,77] | 59.4% 1.75× [52,67] | **55.8% 1.65×** [49,62] |
| ge15 headline | 65.8% 1.94× [50,79] | 64.9% 1.92× [54,75] | 59.4% 1.75× [52,67] | 53.2% 1.57× [47,60] |
| ge40 body | 65.8% 1.94× [50,79] | 59.7% 1.76× [49,70] | **60.6% 1.79×** [53,68] | 54.9% 1.62× [49,61] |
| ge40 headline | **76.3% 2.25×** [61,87] | 66.2% 1.96× [55,76] | 58.1% 1.72× [50,66] | 51.5% 1.52× [45,58] |

**Target ≥30% (base 18.0%, 140 positives):**

| model | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|
| **ge15 body** | 55.3% 3.07× [40,70] | **53.2% 2.96×** [42,64] | **40.6% 2.26×** [33,49] | **34.3% 1.91×** [29,41] |
| ge15 headline | 50.0% 2.77× [35,65] | 45.5% 2.52× [35,57] | 38.7% 2.15× [31,47] | 32.2% 1.79× [27,38] |
| ge40 body | 52.6% 2.92× [37,68] | 44.2% 2.45× [34,55] | 39.4% 2.18× [32,47] | 33.9% 1.88× [28,40] |
| ge40 headline | 55.3% 3.07× [40,70] | 49.4% 2.74× [39,60] | 35.5% 1.97× [28,43] | 31.8% 1.76× [26,38] |

**Target ≥40% (base 13.8%, 107 positives):**

| model | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|
| **ge15 body** | **47.4% 3.44×** [33,63] | **40.3% 2.92×** [30,51] | **32.3% 2.34×** [25,40] | **26.6% 1.93×** [21,33] |
| ge15 headline | 42.1% 3.06× [28,58] | 35.1% 2.55× [25,46] | 29.0% 2.11× [23,37] | 24.5% 1.78× [19,30] |
| ge40 body | 44.7% 3.25× [30,60] | 39.0% 2.83× [29,50] | **32.3% 2.34×** [25,40] | **26.6% 1.93×** [21,33] |
| ge40 headline | 44.7% 3.25× [30,60] | 40.3% 2.92× [30,51] | 28.4% 2.06× [22,36] | 26.2% 1.90× [21,32] |

**ge15 body wins or ties every cell from top-10% down, at all three targets.**
The one place it loses is top-5% at ≥15%, where ge40 headline hits 76.3% — 29
PRs out of 38, on an interval [61,87] that overlaps everything else in the
column. Treat that cell as noise.

**ge40 does not beat ge15 even at its own target.** At ≥40% top-10%, ge15 body
takes 31/77 = 40.3% vs ge40 body's 30/77 = 39.0%, and ge15 body leads at every
other volume too. Training at the trade threshold buys nothing;
it only cuts the positives from 605 to 213 and doubles the CV variance (§4).

### 5.2 Head vs body, paired at matched volume

Both channels select the **same number of rows**, so the precision difference is
a difference of two proportions on equal denominators and Newcombe method-10
applies. Δ = headline − body. 24 comparisons:

**ge15** — body ahead in 11 of 12 cells, tied in the 12th, **none significant**:

| target | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|
| ≥15 | −5.3pp [−25.1,+15.2] | −2.6pp [−17.2,+12.1] | +0.0pp [−10.8,+10.8] | −2.6pp [−11.5,+6.4] |
| ≥30 | −5.3pp [−26.3,+16.5] | −7.8pp [−22.9,+7.8] | −1.9pp [−12.7,+8.9] | −2.1pp [−10.6,+6.4] |
| ≥40 | −5.3pp [−26.2,+16.4] | −5.2pp [−20.0,+9.9] | −3.2pp [−13.4,+7.0] | −2.1pp [−10.0,+5.8] |

**ge40** — sign flips with volume, **none significant**:

| target | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|
| ≥15 | +10.5pp [−9.7,+29.7] | +6.5pp [−8.6,+21.2] | −2.6pp [−13.3,+8.3] | −3.4pp [−12.4,+5.6] |
| ≥30 | +2.6pp [−18.9,+23.8] | +5.2pp [−10.3,+20.4] | −3.9pp [−14.5,+6.8] | −2.1pp [−10.6,+6.3] |
| ≥40 | +0.0pp [−21.3,+21.3] | +1.3pp [−13.9,+16.4] | −3.9pp [−14.0,+6.3] | −0.4pp [−8.4,+7.5] |

**Every one of the 24 intervals contains zero.** On precision at deployable
volume, headline and body are **not distinguishable** on 777 rows — in either
direction, for either model. That is the answer to the §5 caveat: the 70.1% vs
67.5% decile gap was not a result, and neither is its reversal.

What survives is weaker and only *suggestive*: for ge15, 11 of 12 signs favour
body with one tie, and the AUC ordering agrees (0.699/0.743/0.740 vs
0.695/0.714/0.704). The cells are nested across volumes and thresholds so this is
not 12 independent trials and no sign-test p-value is quotable from it — but a
consistent direction across a nested family is still worth more than one cell.

Body and headline `model_proba` correlate at Spearman **0.592** — genuinely
different signals that happen to perform the same. That is the case *for*
eventually combining them, and against picking one on this evidence.

### 5.3 Absolute cutoffs — which model can be wired to a gate

Top-k% is scale-free but undeployable: a live gate needs a constant. Fire counts
and precision on VAL's 777 rows:

| model | ≥0.5 | ≥0.6 | ≥0.7 | ≥0.8 | ≥0.9 |
|---|---|---|---|---|---|
| **ge15 body** | 177/362 = 49% | 92/154 = 60% | **33/46 = 72%** | 3/4 = 75% | — |
| ge15 headline | 161/342 = 47% | 111/207 = 54% | 55/87 = 63% | 19/28 = 68% | 2/2 |
| ge40 body | 151/291 = 52% | 89/147 = 61% | 40/61 = 66% | 15/21 = 71% | 4/6 |
| ge40 headline | 147/311 = 47% | 13/13 = 100% | — | — | — |

*(target ≥15%; the ≥30% and ≥40% versions are in the tool output.)*

**ge40 headline is not deployable at any constant.** Its probabilities are
compressed — `C=0.1` regularization at 8% prevalence — so it jumps from 311 fires
at ≥0.5 to 13 at ≥0.6 and zero above. The 100% at ≥0.6 is 13 PRs and the
76.3% top-5% cell in §5.1 comes from the same corner. There is no cutoff between
"fires on 40% of everything" and "fires 13 times."

**ge15 headline has the most usable high end**: 87 fires at ≥0.7 / 63%, 28 at
≥0.8 / 68%. ge15 body is more precise but thinner there (46 at ≥0.7, then falls
off a cliff to 4). This is the one dimension where the headline model is
*preferable* — not because it ranks better, but because its probability scale is
spread enough to place a threshold on.

### 5.4 Verdict

- **Best model: `classify_fromPRhigh_ge15` (body).** Wins or ties every
  precision cell at top-10% and below, at all three targets, and leads on AUC at
  all three. *Demonstrated* against ge40; *not established* against its own
  headline twin.
- **ge40 is confirmed dead on corrected labels**, on precision as it already was
  on AUC (§4). Do not train at the trade threshold.
- **Headline vs body is a coin flip on this evidence** — all 24 paired intervals
  contain zero. The lean is toward body for ge15 (11/12 signs) and that is all
  that can be said. *Not established.*
- **If a constant-threshold gate is wanted**, ge15 headline at ≥0.7 is the only
  configuration with both usable precision (63%, 1.86×) and non-trivial volume
  (87 fires / 777 = 11%). ge15 body at ≥0.7 is better per-fire (72%) on 46 fires.
- Standing constraint respected: nothing here promotes a model or changes a live
  constant.

---

## 6. Sentiment encoders, nocoref and FinBERT — full-corpus evaluation

**This section replaces an earlier VAL-only (777-row) table.** That restriction
was a mistake: it applied the keyword model's split logic to columns that never
saw TRAIN. Every column below — the 11 encoders on headline and body, the five
`nocoref_*` aggregations, and the four live FinBERT columns — is **externally
trained**, so all 3434 labelled rows of the 04 corpus are an honest test set
(the 2026-07-21 provenance rule; former open question 5, now closed).

Six tools in `statsByClaude/` were run against `--target "fromPR_High(%)"`.
Note `power_predictor.py` defaults to the legacy `DailyHigh(%)`, which this
corpus no longer carries — the target was passed explicitly to every tool.

Row basis: 3533 → 3426 kept (99 null-target, 2 outliers >500%, 6 duplicate PRs).
Movers: **866 at ≥15% (25.3%), 417 at ≥30% (12.2%), 316 at ≥40% (9.2%)**.
Each column is scored on its own maximum coverage; the `n` column matters.

### 6.1 League table — best per family

| family | column | n | AUC≥15 | AUC≥30 | AUC≥40 |
|---|---|---|---|---|---|
| FinBERT (live) | `positive` | 3350 | **0.651** | 0.654 | 0.654 |
| encoder (head) | `FLANG-BERT_sentiment_score` | 3426 | 0.641 | **0.661** | **0.674** |
| encoder (head) | `FLANG-ELECTRA_sentiment_score` | 3426 | 0.642 | 0.653 | 0.659 |
| encoder (head) | `DeBERTa_sentiment_score` | 3426 | 0.638 | 0.649 | 0.652 |
| **nocoref** | `nocoref_net_score` | 3117 | 0.635 | 0.657 | 0.666 |
| nocoref | `nocoref_confidence_weighted` | 3117 | 0.632 | 0.651 | 0.657 |
| FinBERT (live) | `sentiment_score` | 3426 | 0.647 | 0.650 | 0.649 |
| encoder (body) | `Trading-Hero_Body_sentiment_score` | 3426 | 0.623 | 0.623 | 0.624 |
| encoder (body) | `FinancialBERT_Body_sentiment_score` | 3426 | 0.617 | 0.617 | 0.617 |
| non-text | `Float` (inverted) | 3426 | 0.365 → 0.635 | 0.330 → **0.670** | 0.316 → 0.684 |
| non-text | `LastDailyClose` (inv) | 3424 | 0.384 → 0.616 | 0.356 → 0.644 | 0.339 → 0.661 |
| coverage-limited | `nocoref_recommended` | **906** | 0.616 | 0.670 | 0.677 |
| coverage-limited | `MktCap` (inverted) | **875** | — | 0.394 → 0.606 | — |

**`nocoref_recommended` ranks #1 at ≥30 and ≥40 but on 906 of 3426 rows — 26%
coverage. Treat as unproven, not as a finding.** It is the method the existing
backfill driver deliberately skips.

Nothing beats FinBERT `positive` at ≥15. The incumbent live signal is still the
best single sentiment column at the threshold the live gate uses.

### 6.2 The five nocoref methods

| method | n | AUC≥15 | AUC≥30 | AUC≥40 |
|---|---|---|---|---|
| `nocoref_net_score` | 3117 | 0.635 | 0.657 | **0.666** |
| `nocoref_confidence_weighted` | 3117 | 0.632 | 0.651 | 0.657 |
| `nocoref_positional` | 3117 | 0.632 | 0.640 | 0.648 |
| `nocoref_top_k` | 3117 | 0.629 | 0.633 | 0.647 |
| `nocoref_neutral_filter` | 3117 | 0.628 | 0.646 | 0.650 |

`net_score` is the best of the five at every threshold and gains most as the bar
rises. `neutral_filter` — the column wired into the default tool column-set — is
the weakest at ≥15: discarding neutral sentences loses information rather than
denoising it. **If any nocoref column is used as a feature, it should be
`net_score`, not `neutral_filter`.**

nocoref is body-derived yet outranks every `*_Body_*` encoder column (0.635 vs
0.623 at ≥15, 0.657 vs 0.623 at ≥30). Ticker-scoped sentence aggregation
extracts something the flat body pass does not.

⚠️ nocoref covers 3117 rows and **stops at 2026-07-14** (O5.1 dropped the body
pipeline). Its AUCs exclude the last nine days; encoder AUCs do not.

### 6.3 Headline vs body — paired bootstrap, 2000 resamples

Paired CI on the same rows, so shared noise cancels. **Bonferroni tally over 48
pairs: head wins 14, body wins 4, 30 ties.**

| encoder | channel | head | body | Δ | Bonferroni CI | verdict |
|---|---|---|---|---|---|---|
| DeBERTa | `neutral`(inv) | 0.643 | 0.537 | −0.105 | [−0.170, −0.041] | head |
| FinMarBa | `sentiment_score` | 0.614 | 0.521 | −0.094 | [−0.154, −0.033] | head |
| RoBERTa | `positive` | 0.597 | 0.517 | −0.080 | [−0.141, −0.020] | head |
| FLANG-BERT | `positive` | 0.677 | 0.596 | −0.080 | [−0.142, −0.018] | head |
| FinancialBERT | `sentiment_score` | 0.511 | **0.619** | **+0.108** | body wins | **body** |

But **body ADDS on top of headline for 6 of 11 encoders** (pooled OOF, synergy
>+0.01), because the two channels are only loosely correlated (ρ 0.34–0.47):

| encoder | OOF head | OOF body | OOF head+body | synergy | ρ |
|---|---|---|---|---|---|
| Trading-Hero | 0.637 | 0.622 | **0.660** | +0.023 | 0.34 |
| LouMcD-SDict | 0.520 | 0.532 | 0.552 | +0.020 | 0.05 |
| distilroberta | 0.623 | 0.610 | 0.642 | +0.019 | 0.45 |
| finbert-tone | 0.595 | 0.581 | 0.609 | +0.015 | 0.37 |
| FLANG-ELECTRA | 0.640 | 0.606 | 0.653 | +0.012 | 0.45 |
| ModernBERT | 0.606 | 0.580 | 0.617 | +0.011 | 0.35 |

Best cross-encoder pair: `FLANG-ELECTRA_sentiment_score` +
`Trading-Hero_Body_sentiment_score` → OOF **0.660**. So "headline wins" and
"body is worthless" are different claims, and only the first is supported.

### 6.4 Per-class — no universal winner

Within-class AUC at ≥15, classes with ≥8 movers. **Ten different columns win
across eleven classes.**

| BroadClass | n | movers | best column | AUC |
|---|---|---|---|---|
| Crypto | 68 | 17 | `FLANG-BERT_sentiment_score` | 0.80 |
| Earnings | 514 | 94 | `distilroberta_Body_sentiment_score` | 0.73 |
| IP | 73 | 20 | `FLANG-ELECTRA_Body_positive` | 0.71 |
| Corporate | 392 | 55 | `nocoref_positional` | 0.69 |
| Compliance | 140 | 24 | `RoBERTa_positive` | 0.68 |
| MA | — | 121 | `nocoref_top_k` | 0.67 |
| Clinical | — | 88 | `FinMarBa_indecisive`(inv) | 0.67 |
| Financing | — | 64 | `FLANG-BERT_Body_sentiment_score` | 0.67 |
| FDA | — | 71 | `nocoref_net_score` | 0.66 |
| Commercial | — | — | `FLANG-BERT_sentiment_score` | 0.61 |

Two patterns worth noting: **Earnings and IP are body-favouring** (a body column
wins both), and **nocoref wins the three deal/announcement classes** (Corporate,
MA, FDA). `FineClass` is the strongest categorical overall (CV-AUC 0.630 at ≥30).

### 6.5 Trigger frontier — good AUC ≠ usable trigger

TP/FP at ≥15, full corpus:

| trigger | TP/FP | precision |
|---|---|---|
| `positive>=0.8` | 197/248 | **44%** |
| `FLANG-BERT_positive>=0.95` | 258/379 | 40% |
| `nocoref_neutral_filter>=0.5` | 190/307 | 38% |
| `FinMarBa_indecisive<=0.1` (inv) | 641/1402 | 31% |
| `FLANG-ELECTRA_positive>=0.8` | 498/849 | 37% |

**`FLANG-ELECTRA_positive` is saturated**: raising the cutoff from 0.5 to 0.95
drops TP only 504→487. It has a competitive AUC but almost no usable dynamic
range, so it cannot function as a threshold trigger. AUC and thresholdability
are separate properties, and only the second matters for a live gate.

### 6.6 Does anything ADD to the live gate? No.

`positive>=0.8` alone: 197/248 = 44% (1.75×). Every candidate condition, with a
Newcombe 95% CI on the precision delta, at both ≥15 and ≥30:

| condition | gate+cond (≥15) | Δ precision | verdict |
|---|---|---|---|
| `Trading-Hero_Body_sentiment_score>=0.7` | 119/130 = 48% | +3.5% [−4.2, +11.2] | n.s. |
| `nocoref_confidence_weighted>=0.5` | 74/100 = 43% | +0.1% [−8.8, +9.2] | n.s. |
| `FinancialBERT_Body_sentiment_score>=0.8` | 180/222 = 45% | +0.5% [−6.2, +7.2] | n.s. |
| `FLANG-ELECTRA_sentiment_score>=0.9` | 186/231 = 45% | +0.3% [−6.3, +6.9] | n.s. |
| `neutral<=0.2` | 197/248 = 44% | +0.0% [−6.5, +6.5] | n.s. (never binds) |

**Not one condition is demonstrated to improve the live gate**, in either period
block, at either threshold. The best candidate (Trading-Hero body, +3.5%) has a
CI comfortably spanning zero. This is a clean negative result and the most
directly actionable finding here: adding any of these as an AND-condition is
not supported by this corpus.

### 6.7 The ceiling — how much is in this data at all?

`power_predictor.py`, GBM with CV, ≥30:

| model | CV Spearman | AUC | lift@top-decile |
|---|---|---|---|
| **FULL_MODEL** (all features) | 0.461 | **0.772** | **3.21×** |
| `Float + sentiment_score` | 0.342 | 0.709 | 2.83× |
| `Float + FLANG-ELECTRA_positive` | 0.329 | 0.711 | 2.85× |
| best single (`Float`) | 0.228 | 0.672 | 2.45× |

**Every one of the top ten pairs is `Float` + a sentiment column** — and
permutation importance puts `LastDailyClose` (0.074) and `Float` (0.067) an
order of magnitude above the best sentiment feature (`FinMarBa_Body_indecisive`,
0.009). The dominant signal in this corpus is share structure and price level,
not sentiment. Sentiment adds on top of it; it does not lead.

### 6.8 Why the keyword model is not in these tables

`model_proba` trained on 2647 of these rows, so it cannot be scored on the full
corpus without going in-sample. Its **0.699 / 0.743 / 0.741** stands on VAL's 777
rows only (§5) and is not comparable, row-for-row, to anything above. The honest
summary is that on their respective valid test sets the keyword model reads
~0.05–0.09 higher than the best encoder — suggestive, but never yet measured on
one common row set.

That exclusion is unchanged, but VAL does support a *within-family* comparison:
**§5.1–5.4** rank the four corrected-label keyword models against each other on
precision at deployable volume, with intervals. Short version: `ge15` body is
best, `ge40` is dead, headline-vs-body is not separable on 777 rows.

---

### 6.9 TP/FP tables — best column per model

Two views, because a single one misleads. **Top-k% selection** is the ranking
view: take the k% of rows a column scores highest. It is scale-free, so columns
with wildly different distributions are compared at equal selectivity — this is
the honest cross-model comparison. **Absolute cutoffs** is the live-trigger
view: what you actually get wiring `col >= 0.8` into a gate.

Rows: 3432 labelled, outliers >500% removed. `(inv)` columns fire low and are
negated before ranking; their absolute cutoffs mirror to `<= 1-level`.
Generated by `statsByClaude/tpfp_best_columns.py`.

### FinBERT (live) + nocoref


**Target `fromPR_High(%) >= 15%` — base rate 25.3% (870 of 3432 rows).** Cells are TP/FP, then precision (lift).

| column | n | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|---|
| `positive` | 3356 | 71/96 = 43% (1.66x) | 146/189 = 44% (1.71x) | 261/410 = 39% (1.52x) | 379/627 = 38% (1.48x) |
| `sentiment_score` | 3432 | 70/101 = 41% (1.61x) | 135/208 = 39% (1.55x) | 259/427 = 38% (1.49x) | 377/652 = 37% (1.45x) |
| `neutral(inv)` | 3432 | 59/112 = 35% (1.36x) | 127/216 = 37% (1.46x) | 255/431 = 37% (1.47x) | 363/666 = 35% (1.39x) |
| `negative(inv)` | 3432 | 59/112 = 35% (1.36x) | 122/221 = 36% (1.40x) | 248/438 = 36% (1.43x) | 354/675 = 34% (1.36x) |
| `nocoref_net_score` | 3123 | 70/86 = 45% (1.79x) | 134/178 = 43% (1.71x) | 249/375 = 40% (1.59x) | 335/601 = 36% (1.43x) |
| `nocoref_confidence_weighted` | 3123 | 63/93 = 40% (1.61x) | 135/177 = 43% (1.72x) | 232/392 = 37% (1.48x) | 337/599 = 36% (1.43x) |
| `nocoref_neutral_filter` | 3123 | 56/100 = 36% (1.43x) | 124/188 = 40% (1.58x) | 234/390 = 38% (1.49x) | 336/600 = 36% (1.43x) |

**Target `fromPR_High(%) >= 30%` — base rate 12.2% (419 of 3432 rows).** Cells are TP/FP, then precision (lift).

| column | n | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|---|
| `positive` | 3356 | 34/133 = 20% (1.65x) | 64/271 = 19% (1.55x) | 127/544 = 19% (1.54x) | 209/797 = 21% (1.69x) |
| `sentiment_score` | 3432 | 33/138 = 19% (1.58x) | 64/279 = 19% (1.53x) | 128/558 = 19% (1.53x) | 206/823 = 20% (1.64x) |
| `neutral(inv)` | 3432 | 26/145 = 15% (1.25x) | 58/285 = 17% (1.39x) | 126/560 = 18% (1.50x) | 195/834 = 19% (1.55x) |
| `negative(inv)` | 3432 | 33/138 = 19% (1.58x) | 57/286 = 17% (1.36x) | 124/562 = 18% (1.48x) | 186/843 = 18% (1.48x) |
| `nocoref_net_score` | 3123 | 43/113 = 28% (2.31x) | 78/234 = 25% (2.10x) | 137/487 = 22% (1.84x) | 174/762 = 19% (1.56x) |
| `nocoref_confidence_weighted` | 3123 | 40/116 = 26% (2.15x) | 78/234 = 25% (2.10x) | 126/498 = 20% (1.70x) | 174/762 = 19% (1.56x) |
| `nocoref_neutral_filter` | 3123 | 36/120 = 23% (1.94x) | 75/237 = 24% (2.02x) | 124/500 = 20% (1.67x) | 178/758 = 19% (1.60x) |

### Encoders — best HEADLINE channel


**Target `fromPR_High(%) >= 15%` — base rate 25.3% (870 of 3432 rows).** Cells are TP/FP, then precision (lift).

| column | n | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|---|
| `FLANG-BERT_sentiment_score` | 3432 | 83/88 = 49% (1.91x) | 158/185 = 46% (1.82x) | 273/413 = 40% (1.57x) | 390/639 = 38% (1.50x) |
| `FLANG-ELECTRA_sentiment_score` | 3432 | 58/113 = 34% (1.34x) | 143/200 = 42% (1.64x) | 261/425 = 38% (1.50x) | 400/629 = 39% (1.53x) |
| `DeBERTa_sentiment_score` | 3432 | 77/94 = 45% (1.78x) | 144/199 = 42% (1.66x) | 265/421 = 39% (1.52x) | 383/646 = 37% (1.47x) |
| `Trading-Hero_positive` | 3432 | 75/96 = 44% (1.73x) | 146/197 = 43% (1.68x) | 275/411 = 40% (1.58x) | 375/654 = 36% (1.44x) |
| `FinMarBa_indecisive(inv)` | 3432 | 62/109 = 36% (1.43x) | 133/210 = 39% (1.53x) | 256/430 = 37% (1.47x) | 382/647 = 37% (1.46x) |
| `distilroberta_sentiment_score` | 3432 | 64/107 = 37% (1.48x) | 137/206 = 40% (1.58x) | 261/425 = 38% (1.50x) | 380/649 = 37% (1.46x) |
| `ModernBERT_positive` | 3432 | 69/102 = 40% (1.59x) | 124/219 = 36% (1.43x) | 248/438 = 36% (1.43x) | 351/678 = 34% (1.35x) |
| `finbert-tone_sentiment_score` | 3432 | 51/120 = 30% (1.18x) | 113/230 = 33% (1.30x) | 235/451 = 34% (1.35x) | 345/684 = 34% (1.32x) |
| `RoBERTa_positive` | 3432 | 58/113 = 34% (1.34x) | 106/237 = 31% (1.22x) | 211/475 = 31% (1.21x) | 324/705 = 31% (1.24x) |
| `FinancialBERT_negative(inv)` | 3432 | 29/142 = 17% (0.67x) | 63/280 = 18% (0.72x) | 133/553 = 19% (0.76x) | 210/819 = 20% (0.81x) |
| `LouMcD-SDict_sentiment_score` | 3432 | 46/125 = 27% (1.06x) | 95/248 = 28% (1.09x) | 205/481 = 30% (1.18x) | 261/768 = 25% (1.00x) |

**Target `fromPR_High(%) >= 30%` — base rate 12.2% (419 of 3432 rows).** Cells are TP/FP, then precision (lift).

| column | n | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|---|
| `FLANG-BERT_sentiment_score` | 3432 | 44/127 = 26% (2.11x) | 84/259 = 24% (2.01x) | 149/537 = 22% (1.78x) | 215/814 = 21% (1.71x) |
| `FLANG-ELECTRA_sentiment_score` | 3432 | 33/138 = 19% (1.58x) | 81/262 = 24% (1.93x) | 136/550 = 20% (1.62x) | 212/817 = 21% (1.69x) |
| `DeBERTa_sentiment_score` | 3432 | 36/135 = 21% (1.72x) | 60/283 = 17% (1.43x) | 126/560 = 18% (1.50x) | 199/830 = 19% (1.58x) |
| `Trading-Hero_positive` | 3432 | 35/136 = 20% (1.68x) | 76/267 = 22% (1.81x) | 143/543 = 21% (1.71x) | 193/836 = 19% (1.54x) |
| `FinMarBa_indecisive(inv)` | 3432 | 33/138 = 19% (1.58x) | 69/274 = 20% (1.65x) | 135/551 = 20% (1.61x) | 203/826 = 20% (1.62x) |
| `distilroberta_sentiment_score` | 3432 | 38/133 = 22% (1.82x) | 69/274 = 20% (1.65x) | 131/555 = 19% (1.56x) | 194/835 = 19% (1.54x) |
| `ModernBERT_positive` | 3432 | 32/139 = 19% (1.53x) | 63/280 = 18% (1.50x) | 126/560 = 18% (1.50x) | 193/836 = 19% (1.54x) |
| `finbert-tone_sentiment_score` | 3432 | 23/148 = 13% (1.10x) | 48/295 = 14% (1.15x) | 119/567 = 17% (1.42x) | 183/846 = 18% (1.46x) |
| `RoBERTa_positive` | 3432 | 32/139 = 19% (1.53x) | 53/290 = 15% (1.27x) | 106/580 = 15% (1.27x) | 160/869 = 16% (1.27x) |
| `FinancialBERT_negative(inv)` | 3432 | 7/164 = 4% (0.34x) | 20/323 = 6% (0.48x) | 53/633 = 8% (0.63x) | 91/938 = 9% (0.72x) |
| `LouMcD-SDict_sentiment_score` | 3432 | 23/148 = 13% (1.10x) | 47/296 = 14% (1.12x) | 99/587 = 14% (1.18x) | 117/912 = 11% (0.93x) |

### Encoders — best BODY channel


**Target `fromPR_High(%) >= 15%` — base rate 25.3% (870 of 3432 rows).** Cells are TP/FP, then precision (lift).

| column | n | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|---|
| `Trading-Hero_Body_sentiment_score` | 3432 | 62/109 = 36% (1.43x) | 124/219 = 36% (1.43x) | 245/441 = 36% (1.41x) | 366/663 = 36% (1.40x) |
| `FinancialBERT_Body_sentiment_score` | 3432 | 45/126 = 26% (1.04x) | 108/235 = 31% (1.24x) | 226/460 = 33% (1.30x) | 337/692 = 33% (1.29x) |
| `FLANG-ELECTRA_Body_positive` | 3432 | 48/123 = 28% (1.11x) | 103/240 = 30% (1.18x) | 239/447 = 35% (1.37x) | 315/714 = 31% (1.21x) |
| `FLANG-BERT_Body_sentiment_score` | 3432 | 64/107 = 37% (1.48x) | 110/233 = 32% (1.27x) | 218/468 = 32% (1.25x) | 325/704 = 32% (1.25x) |
| `distilroberta_Body_sentiment_score` | 3432 | 62/109 = 36% (1.43x) | 110/233 = 32% (1.27x) | 226/460 = 33% (1.30x) | 323/706 = 31% (1.24x) |
| `DeBERTa_Body_positive` | 3432 | 67/104 = 39% (1.55x) | 114/229 = 33% (1.31x) | 226/460 = 33% (1.30x) | 308/721 = 30% (1.18x) |
| `FinMarBa_Body_indecisive(inv)` | 3432 | 75/96 = 44% (1.73x) | 140/203 = 41% (1.61x) | 255/431 = 37% (1.47x) | 346/683 = 34% (1.33x) |
| `ModernBERT_Body_positive` | 3432 | 59/112 = 35% (1.36x) | 110/233 = 32% (1.27x) | 213/473 = 31% (1.22x) | 324/705 = 31% (1.24x) |
| `RoBERTa_Body_positive` | 3432 | 38/133 = 22% (0.88x) | 81/262 = 24% (0.93x) | 172/514 = 25% (0.99x) | 267/762 = 26% (1.02x) |
| `finbert-tone_Body_sentiment_score` | 3432 | 38/133 = 22% (0.88x) | 90/253 = 26% (1.04x) | 207/479 = 30% (1.19x) | 307/722 = 30% (1.18x) |
| `LouMcD-SDict_Body_strong_modal` | 3432 | 15/156 = 9% (0.35x) | 42/301 = 12% (0.48x) | 122/564 = 18% (0.70x) | 197/832 = 19% (0.76x) |

**Target `fromPR_High(%) >= 30%` — base rate 12.2% (419 of 3432 rows).** Cells are TP/FP, then precision (lift).

| column | n | top 5% | top 10% | top 20% | top 30% |
|---|---|---|---|---|---|
| `Trading-Hero_Body_sentiment_score` | 3432 | 36/135 = 21% (1.72x) | 65/278 = 19% (1.55x) | 122/564 = 18% (1.46x) | 184/845 = 18% (1.46x) |
| `FinancialBERT_Body_sentiment_score` | 3432 | 16/155 = 9% (0.77x) | 54/289 = 16% (1.29x) | 111/575 = 16% (1.33x) | 172/857 = 17% (1.37x) |
| `FLANG-ELECTRA_Body_positive` | 3432 | 14/157 = 8% (0.67x) | 39/304 = 11% (0.93x) | 117/569 = 17% (1.40x) | 153/876 = 15% (1.22x) |
| `FLANG-BERT_Body_sentiment_score` | 3432 | 29/142 = 17% (1.39x) | 50/293 = 15% (1.19x) | 105/581 = 15% (1.25x) | 166/863 = 16% (1.32x) |
| `distilroberta_Body_sentiment_score` | 3432 | 26/145 = 15% (1.25x) | 55/288 = 16% (1.31x) | 100/586 = 15% (1.19x) | 148/881 = 14% (1.18x) |
| `DeBERTa_Body_positive` | 3432 | 35/136 = 20% (1.68x) | 51/292 = 15% (1.22x) | 94/592 = 14% (1.12x) | 134/895 = 13% (1.07x) |
| `FinMarBa_Body_indecisive(inv)` | 3432 | 42/129 = 25% (2.01x) | 73/270 = 21% (1.74x) | 136/550 = 20% (1.62x) | 179/850 = 17% (1.42x) |
| `ModernBERT_Body_positive` | 3432 | 25/146 = 15% (1.20x) | 50/293 = 15% (1.19x) | 94/592 = 14% (1.12x) | 158/871 = 15% (1.26x) |
| `RoBERTa_Body_positive` | 3432 | 16/155 = 9% (0.77x) | 35/308 = 10% (0.84x) | 73/613 = 11% (0.87x) | 126/903 = 12% (1.00x) |
| `finbert-tone_Body_sentiment_score` | 3432 | 11/160 = 6% (0.53x) | 36/307 = 10% (0.86x) | 90/596 = 13% (1.07x) | 140/889 = 14% (1.11x) |
| `LouMcD-SDict_Body_strong_modal` | 3432 | 6/165 = 4% (0.29x) | 19/324 = 6% (0.45x) | 62/624 = 9% (0.74x) | 98/931 = 10% (0.78x) |

### Absolute-cutoff view (live-trigger semantics)


**Absolute cutoffs, target `>= 15%` (base 25.3%).** Inverted columns fire on `value <= 1-level`. Cells are TP/FP = precision.

| column | `>=0.5` | `>=0.6` | `>=0.7` | `>=0.8` | `>=0.9` | `>=0.95` |
|---|---|---|---|---|---|---|
| `positive` | 416/707 = 37% | 361/593 = 38% | 293/456 = 39% | 197/248 = 44% | 58/76 = 43% | 0/1 = 0% |
| `sentiment_score` | 415/732 = 36% | 364/619 = 37% | 287/477 = 38% | 180/257 = 41% | 41/53 = 44% | — |
| `neutral(inv)` | 442/843 = 34% | 386/711 = 35% | 315/573 = 35% | 235/372 = 39% | 88/160 = 35% | 26/56 = 32% |
| `negative(inv)` | 865/2528 = 25% | 863/2516 = 26% | 862/2501 = 26% | 857/2486 = 26% | 823/2293 = 26% | 751/1876 = 29% |
| `nocoref_net_score` | 93/127 = 42% | 30/41 = 42% | 9/11 = 45% | 3/5 = 38% | 0/2 = 0% | — |
| `nocoref_confidence_weighted` | 351/648 = 35% | 166/231 = 42% | 31/49 = 39% | 4/7 = 36% | 0/2 = 0% | — |
| `nocoref_neutral_filter` | 189/307 = 38% | 56/101 = 36% | 16/28 = 36% | 5/8 = 38% | 0/2 = 0% | — |
| `FLANG-BERT_sentiment_score` | 530/990 = 35% | 498/896 = 36% | 460/790 = 37% | 413/672 = 38% | 306/485 = 39% | 211/279 = 43% |
| `FLANG-ELECTRA_sentiment_score` | 503/876 = 36% | 502/871 = 37% | 499/860 = 37% | 497/849 = 37% | 490/841 = 37% | 485/825 = 37% |
| `DeBERTa_sentiment_score` | 487/952 = 34% | 469/905 = 34% | 456/855 = 35% | 430/782 = 35% | 392/667 = 37% | 336/575 = 37% |
| `Trading-Hero_positive` | 520/958 = 35% | 514/947 = 35% | 509/932 = 35% | 501/917 = 35% | 490/893 = 35% | 475/867 = 35% |
| `FinMarBa_indecisive(inv)` | 870/2562 = 25% | 870/2561 = 25% | 870/2553 = 25% | 639/1402 = 31% | 44/81 = 35% | 0/1 = 0% |

**Absolute cutoffs, target `>= 30%` (base 12.2%).** Inverted columns fire on `value <= 1-level`. Cells are TP/FP = precision.

| column | `>=0.5` | `>=0.6` | `>=0.7` | `>=0.8` | `>=0.9` | `>=0.95` |
|---|---|---|---|---|---|---|
| `positive` | 226/897 = 20% | 198/756 = 21% | 149/600 = 20% | 95/350 = 21% | 26/108 = 19% | 0/1 = 0% |
| `sentiment_score` | 226/921 = 20% | 197/786 = 20% | 144/620 = 19% | 85/352 = 19% | 16/78 = 17% | — |
| `neutral(inv)` | 237/1048 = 18% | 211/886 = 19% | 161/727 = 18% | 114/493 = 19% | 41/207 = 17% | 13/69 = 16% |
| `negative(inv)` | 417/2976 = 12% | 416/2963 = 12% | 415/2948 = 12% | 413/2930 = 12% | 400/2716 = 13% | 370/2257 = 14% |
| `nocoref_net_score` | 55/165 = 25% | 20/51 = 28% | 6/14 = 30% | 1/7 = 12% | 0/2 = 0% | — |
| `nocoref_confidence_weighted` | 182/817 = 18% | 91/306 = 23% | 21/59 = 26% | 2/9 = 18% | 0/2 = 0% | — |
| `nocoref_neutral_filter` | 105/391 = 21% | 36/121 = 23% | 11/33 = 25% | 2/11 = 15% | 0/2 = 0% | — |
| `FLANG-BERT_sentiment_score` | 286/1234 = 19% | 273/1121 = 20% | 252/998 = 20% | 225/860 = 21% | 167/624 = 21% | 117/373 = 24% |
| `FLANG-ELECTRA_sentiment_score` | 266/1113 = 19% | 265/1108 = 19% | 265/1094 = 19% | 264/1082 = 20% | 260/1071 = 20% | 257/1053 = 20% |
| `DeBERTa_sentiment_score` | 259/1180 = 18% | 249/1125 = 18% | 240/1071 = 18% | 226/986 = 19% | 205/854 = 19% | 172/739 = 19% |
| `Trading-Hero_positive` | 266/1212 = 18% | 265/1196 = 18% | 263/1178 = 18% | 258/1160 = 18% | 250/1133 = 18% | 239/1103 = 18% |
| `FinMarBa_indecisive(inv)` | 419/3013 = 12% | 419/3012 = 12% | 419/3004 = 12% | 323/1718 = 16% | 25/100 = 20% | 0/1 = 0% |

### Reading these tables

- **`FLANG-BERT_sentiment_score` is the best encoder in both views** — 49%
  precision at top-5% (1.91x) and 26% at ≥30% (2.11x), and unlike its peers it
  keeps separating at tight absolute cutoffs (43% at `>=0.95`).
- **`nocoref_net_score` is the best column at ≥30%**: 28% precision at top-5%,
  **2.31x lift** — the highest in the corpus. But its absolute cutoffs are
  useless (`>=0.7` fires on 20 rows) because it lives on a different scale.
  Usable as a ranker, not as a fixed-threshold trigger.
- **Saturation makes several good rankers unusable as triggers.**
  `FLANG-ELECTRA_sentiment_score` is flat across the whole cutoff grid (37% at
  `>=0.5` through 37% at `>=0.95`); `Trading-Hero_positive` likewise (35%
  throughout). Their AUC is fine; their dynamic range is not.
- **`negative(inv)` and `FinMarBa_indecisive(inv)` barely bind.** Both sit at the
  base rate for most of the grid — `FinMarBa_indecisive(inv)` is 25% (= base) at
  `>=0.5` through `>=0.7`, and only separates in the last two columns where n
  collapses to 81. Same tail-cutoff problem as §6.6.
- **Two columns are actively inverted at the top**: `FinancialBERT_negative(inv)`
  (0.34x at top-5%, ≥30%) and `LouMcD-SDict_Body_strong_modal` (0.29x). Their
  a-priori orientation is wrong on this corpus — worth knowing before either is
  used as a veto.
- **Best body column is `FinMarBa_Body_indecisive(inv)`** (44% top-5% at ≥15%,
  2.01x at ≥30%) — beating FinMarBa's own headline channel at top-5%, the only
  encoder where that holds.

---

### 6.10 Combining FLANG-BERT + `positive` + FinMarBa indecisive

Search over the three columns with `threshold_finder_finbert_finmarba.py`
(generalized this session with `--columns` / `--anchors` / `--sweep-pair` /
`--live-gate`). Pure AND and pure OR only; ranked by precision subject to a fire
floor with Wilson-low > base. **771 AND + 871 OR rules** survived screening per
column set. Full corpus, 3434 labelled rows, 80-day span.

#### Answer 1 — AND, decisively. OR is worthless here.

| rule type | best result (≥15%) |
|---|---|
| **AND** | **75.0% precision, 2.95× lift** (24 fires) |
| OR | 33% precision, 1.31× lift (2707 fires) |

The best OR barely clears the 25.4% base rate. These three signals are all
"is this PR bullish" detectors, so unioning them mostly admits everything.
Intersecting them is where the information is.

#### Answer 2 — the winning rule

```
FLANG-BERT_sentiment_score >= 0.95  AND  positive >= 0.8  AND  FinMarBa_Body_indecisive <= 0.08
```

| target | fires | TP | precision | lift | recall |
|---|---|---|---|---|---|
| ≥15% | 24 | 18 | **75.0%** | 2.95× | 2.1% |
| ≥30% | 24 | 6 | 25.0% | 2.04× | 1.4% |
| ≥40% | 24 | 6 | 25.0% | 2.67× | 1.4% |

Against the alternatives at ≥15%:

| rule | fires | precision | lift |
|---|---|---|---|
| **3-col AND (above)** | 24 | **75.0%** | **2.95×** |
| 2-col `fb>=0.95 AND positive>=0.8` | 211 | 46.9% | 1.85× |
| solo `FLANG-BERT >= 0.98` | 177 | 49.2% | 1.94× |
| **live gate** `positive>=0.6 AND indecisive<=0.13` | 286 | 41.3% | 1.62× |

#### Answer 3 — the body channel is the whole edge; the headline twin is not a substitute

Adding each veto to the same 2-column base (211 fires, 46.9%), at **matched fire
counts**:

| fires | + `FinMarBa_Body_indecisive` | + `FinMarBa_indecisive` (headline) |
|---|---|---|
| ~22 | **77.3%** | 40.9% |
| ~32 | **71.9%** | 42.4% |
| ~43 | **67.4%** | 43.2% |
| ~53 | **58.5%** | 43.4% |
| ~64 | **57.8%** | 40.9% |
| *(base)* | *46.9%* | *46.9%* |

**The headline veto sits at or below the 2-column baseline at every volume — it
adds nothing.** The body veto adds 10–30pp. This is the number that decides
whether an O5.2 body pass is worth building, and it says the two channels are
not interchangeable despite measuring the same concept.

Newcombe CIs on the body veto's gain over the 2-col rule:

| cutoff | precision | delta vs 46.9% | verdict |
|---|---|---|---|
| `<=0.08` | 75.0% (24) | **+28.1% [+7.1, +42.7]** | **helps** |
| `<=0.10` | 54.8% (84) | +7.8% [−4.7, +20.0] | n.s. |
| `<=0.12` | 47.6% (147) | +0.7% [−9.7, +11.1] | n.s. |
| `<=0.15` | 48.9% (188) | +2.0% [−7.7, +11.7] | n.s. |

Only the tightest cutoff is significant, and the effect dies immediately as it
loosens — a cliff, not a gradient.

#### Three reasons not to deploy this yet

**1. It does not survive at higher targets.** The 18 TP at ≥15% are only 6 at
≥30%. In the recent block the 3-col rule is *worse* than the simple 2-col rule
at ≥30% (1.39× vs 1.69×) and ≥40% (1.81× vs 1.91×). It detects reliable
*modest* movers, not big ones — the opposite of what a ≥30% trigger wants.

**2. Volume is marginal.** 24 fires over 80 days ≈ **one per 3.3 days**, and 12
per block. The 75% is stable (75.0% in *both* early and recent, 9/12 each) which
is genuinely encouraging, but 12 fires per half cannot distinguish 75% from 55%.

**3. It is post-selection.** This rule won a search over 771 AND rules scored on
the same rows. The Wilson interval [55%, 88%] is a pre-selection interval and
does not account for that; the true precision is below 75%. The top 8 rules are
near-duplicates clustered at 73–75%, which argues it is a plateau rather than a
single lucky spike — but that is reassurance, not proof.

**Not deployable regardless**: `FinMarBa_Body_indecisive` is not computed by
`Orchestrator5.2.py`, which runs `FinMarBa_headliner.py` only. Shipping this
needs a body encoder pass on the trade-critical path.

The cheap test that would settle it: re-run the identical search on shuffled
labels and see what precision the best rule reaches on pure noise.

---

## 7. Data-quality traps found (all corrected in the numbers above)

| trap | detail |
|---|---|
| **400-word body cap** | Bodies capped at exactly 400 words through 2026-06-30, uncapped from 07-01 (median 400 → 930, max 7175). 579/777 VAL rows would have been ~2× longer than anything trained on, inflating `n_kw_hits` and count-based `kw_add_*`. Truncated corpus-wide before training. |
| **`strip_wire_boilerplate` not in the pipeline** | `keyword_finder` applies it before fitting and does NOT bake it into `tfidf_fitted_pipeline.joblib`. Loading the joblib requires applying it manually. |
| **`--evaluate` split leak** | Must pass `--no-strip-boilerplate` to mirror training or the split shifts and AUC inflates to ~0.92. Verified clean: reference build reproduced `keyword_finder` test AUC 0.7624 (body) / 0.6589 (headline) exactly. |
| **Permanent vs transient fetch failure** | CODX (9 rows, retired IBKR contract answering as `CODX.OLD`) and BNBX (PINK/OTC) are unfetchable but were booked `failure`, which makes `--only-missing` retry them every run and trip `CONSECUTIVE_FAILURE_ABORT` (exit 2). Reclassified to `skip`. |
| **32 partially-populated columns** | Live-capture/legacy fields: `nocoref_*` 3209/3533, `Trigger` 2159, `boilerplate_category` 1462, `MktCap` 919, `FMB_*` 143, `body_sentiment` 103. Any feature drawn from these needs its own availability guard. |

---

## 8. Open questions

1. **Is the base-rate drift regime or composition?** Determines whether to
   recalibrate to the recent rate or model the drift. Blocks any threshold work.
2. **Is the headline `kw_mult_nb` precision edge real?** 70.1% vs 67.5% on 77
   rows. Needs a bootstrap CI before acting.
3. **Would an out-of-fold `model_proba` make the combo work?** Current combo is
   structurally handicapped; untested.
4. **Head+body pair** — not fitted. Spearman 0.592 makes it the most promising
   untested combination.
5. ~~Encoder re-evaluation on the full corpus.~~ **CLOSED 2026-07-28** — §6 is
   now the full 3426-row evaluation.
6. **Can the keyword model and the encoders be compared on one row set?**
   Currently impossible without an out-of-fold `model_proba` over all 3426 rows.
   Until then the §5 vs §6 gap is suggestive, not measured (§6.8).
7. **Is `Float` doing the work?** Permutation importance puts `Float` and
   `LastDailyClose` ~10× above the best sentiment feature, and all top-10 pairs
   are `Float` + sentiment. Any sentiment result should be re-checked
   *conditional on* Float before it is believed.
8. **Should `nocoref_neutral_filter` be replaced by `nocoref_net_score`?**
   `net_score` is better at every threshold, but `neutral_filter` is what
   `class_conditional_predictors.py` and the live trigger set default to.
9. **Is the per-class winner spread real or noise?** Ten columns win eleven
   classes, several on <25 movers. Needs a within-class bootstrap before any
   per-class routing is considered.
10. **Should the head+body pair be built?** 6 of 11 encoders show positive
    synergy and the best cross-encoder pair reaches OOF 0.660 vs 0.640 for the
    best single. Untested as a deployable feature.

---

## 9. Recommendations

Confidence is marked on every item: **[demonstrated]** = a confidence interval
excludes the null; **[suggestive]** = point estimate only, could be noise;
**[not established]** = measured but does not clear either bar. Only two things
in this whole report are demonstrated — the label defect (§1) and the body-veto
gain at `<=0.08` (§6.10). Everything else is weaker than it looks.

### A. Do not change the live gate

**No rule measured here is demonstrated better than the deployed
`positive>=0.6 AND FinMarBa_indecisive<=0.13`** (286 fires, 41.3% precision,
1.62× at ≥15%). **[not established]**

One candidate deserves a proper test — but it is not a recommendation:

| rule | fires | precision | lift | recall |
|---|---|---|---|---|
| live gate | 286 | 41.3% | 1.62× | 13.5% |
| `FLANG-BERT_sentiment_score>=0.95 AND positive>=0.8` | 211 | **46.9%** | **1.85×** | 11.4% |

Comparable volume, ~6pp better precision (§6.10). **[suggestive]** — it was never
searched for on its own terms; it fell out of the 3-column search as a by-product,
so it has had no independent validation and no significance test against the live
gate. Treat as a hypothesis to test on a holdout, not a change to make.

### B. Reject outright

| rejected | why |
|---|---|
| the 24-fire 3-column rule of §6.10 | **2.1% recall** — 24 fires in 3434 PRs, ~1 per 3.3 days. 75% precision is irrelevant at that volume. **[not established]** |
| adding any tested condition to the live gate | all n.s. with CIs spanning zero, both period blocks, both thresholds (§6.6) |
| `FinMarBa_indecisive` as a *headline* veto | adds nothing over the 2-column base at any matched volume — 40–43% vs a 46.9% baseline (§6.10) |
| `FinancialBERT_negative`, `LouMcD-SDict_Body_strong_modal` as vetoes | **inverted** on this corpus: 0.34× and 0.29× lift at top-5%, ≥30% (§6.9). They would fire hardest on the best PRs. |
| `FLANG-ELECTRA_positive`, `Trading-Hero_positive` as threshold triggers | saturated — precision flat from cutoff 0.5 to 0.95 (§6.5, §6.9). Fine as rankers, useless as gates. |

### C. Cheap fixes, worth doing regardless

1. **Re-pin the prevalence.** `keyword_scorer-2.py` `DEFAULT_PREVALENCE = 0.185`
   is stale for both blocks; TRAIN ge15 is **0.228560** (§2). **[demonstrated]**
2. **If any nocoref column is used, use `net_score`.** It beats
   `neutral_filter` at every threshold, yet `neutral_filter` is what
   `class_conditional_predictors.py` and the live trigger list default to
   (§6.2). **[suggestive]**
3. **Port the permanent-vs-transient Error-162 classification** from
   `ohlc_minute_fetch.py` into `fromPR_HighOfDay_addOn.py`, so unfetchable
   contracts self-classify as `skip` instead of tripping
   `CONSECUTIVE_FAILURE_ABORT` on every nightly run (§7).

### D. Blocked — and by what

| goal | blocked on |
|---|---|
| any threshold calibration | whether the base-rate drift is **regime or composition** (§2). Thresholds fitted on a 22.9% block do not transfer to a 33.9% one, so this gates all gate work. |
| adding a body-encoder pass to O5.2 | measuring the body channel's edge at **deployable volume**. The +28pp gain is real but exists only at 24 fires; by 84 fires it is +7.8% n.s. (§6.10). Build only if the edge survives at usable volume. |
| promoting the keyword model | the user's standing instruction — no promotion pending further analysis. Unchanged. |

### E. The single highest-value next experiment

**A permutation null on the §6.10 search.** Re-run the identical search on
shuffled labels and record what precision the best rule reaches on pure noise.
771 AND rules were screened on the same rows that selected the winner, so every
precision figure in §6.10 is an upper bound of unknown tightness. This one re-run
converts them into estimates, and costs nothing but compute.

Second priority: an **out-of-fold `model_proba` over all 3426 rows**, which would
let the keyword model and the encoders finally be compared on one row set (§6.8).

---

## Appendix — artifacts

| item | path (relative to `scripts/`) |
|---|---|
| corpus | `price_stats_loader/outputs/04_PRstats_2026-05-04-2026-07-23_..._FromPRhigh.tsv` |
| split summary | `price_stats_loader/outputs/04_..._split_summary.md` |
| models | `tools/keyword/model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN/` |
| walk-forward report | `…/model_trained_on_04-…_TRAIN/walkforward_report.md` |
| scored TRAIN | `tools/keyword/inputs/scored_ge15_TRAIN_pre20260620_comboScore.tsv` (2647 × 217) |
| scored VAL | `tools/keyword/inputs/scored_ge15_VAL_from20260620_comboScore.tsv` (777 × 217) |
| combo weights | `tools/keyword/inputs/scored_ge15_TRAIN_pre20260620_comboScore_model.json` |
| new tooling | `strip_label_polluted_cols.py`, `split_train_val.py`, `statsByClaude/merge_scores_by_id.py`, `tools/keyword/walkforward_eval.py` |

### §6 full-corpus run — outputs (all in `price_stats_loader/outputs/`)

| tool | output |
|---|---|
| `analyze_frompr_predictors.py` | `fromprhigh_all_predictors_report_04corpus_t{15,30,40}.md` |
| `head_vs_body.py` | `head_vs_body_encoders_report_04corpus.md` |
| `class_conditional_predictors.py` | `fromprhigh_class_conditional_report_04corpus_{BroadClass,FineClass}.md` |
| `false_positive_count.py` | `04_..._TPFP_fromprhigh_tp{15,30,40}.tsv` |
| `power_predictor.py` | `power_04corpus_tp30.tsv` |
| `conditional_value.py` | stdout only (no report file) |

Two tool defects hit during the run, worked around rather than patched:

- **`class_conditional_predictors.py`** coerces only `--columns` to numeric, but
  the default `--triggers` reference `FLANG-BERT_positive`. If a trigger column
  is not also in `--columns` it stays a string and the TP/FP section dies with
  `TypeError: '>=' not supported between 'str' and 'float'`. Workaround: include
  every trigger column in `--columns`.
- **`power_predictor.py`** crashes in `mutual_info_regression`
  (`Found array with 0 sample(s)`) on sparse columns at the default
  `--min-coverage 100`. Run used `--min-coverage 1000`, which also drops
  `MktCap`/`FMB_*` from that tool's tables. Its `--target` also still defaults to
  the legacy `DailyHigh(%)` — **always pass `--target` explicitly.**

Bare score columns = **body**; `_hl` suffix = **headline**. Nothing promoted —
no `SELECTED-MODEL_` prefix applied, live scorer still on the old model.
