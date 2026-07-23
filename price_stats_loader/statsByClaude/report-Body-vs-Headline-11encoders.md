# Body vs headline — the 11 encoders as predictors of `fromPR_High(%)`

**Date:** 2026-07-20
**Corpus:** `outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv`
— 3306 rows, **3253 with a `fromPR_High(%)` value** after cleaning; 287 movers at
`>= 30%` = **8.8% base rate**.
**Question:** `report-Headline-11encoders.md` scored the 11 encoders on the PR
*headline* and closed with the caveat that a body-scored variant "may behave
differently and is untested here." It now exists. Does scoring the body beat
scoring the headline — and does it add anything on top?
**Scripts written for this:** `merge_body_encoder_cols.py`, `head_vs_body.py`,
plus `--triggers` / `--report-suffix` flags added to
`class_conditional_predictors.py`. Canonical commands in `commands.txt`.

---

> ## Verdict in one paragraph
>
> **Body scoring loses, and it is not close.** Across 48 head/body channel pairs,
> after correcting for multiple comparisons, the **headline wins 9, the body wins
> 1, and 38 are ties**. No body column produces a tradeable trigger: the best body
> precision is 12.3% (1.40× lift) against the headline FinMarBa cell's 19.1%
> (2.17×). Body scoring costs **10× the inference time** (3205 ms vs 319 ms per
> PR, all 11 encoders). Two findings survive and are worth keeping: **FinancialBERT
> is not a broken model — it is a model that cannot work on headlines**, and
> **Earnings PRs are the one class where the body genuinely beats the headline**,
> which is exactly where you would expect it economically.

---

## 0. How to read these numbers

This report reuses the statistical conventions of §0 of
`report-Headline-11encoders.md` (AUC via the Mann-Whitney rank formula; 0.50 =
worthless; below 0.50 = backwards; precision = TP/(TP+FP) compared against the
8.8% base rate). Three things are specific to a head-to-head comparison:

**1. The two AUCs are paired, so the CI must be too.** Head and body scores are
computed on the *same rows* against the *same labels*. Their AUCs therefore rise
and fall together with the luck of which PRs landed in the corpus. Comparing two
independent ±0.036 confidence intervals would be badly over-conservative — it
would call almost every real difference a tie. Instead the difference is
bootstrapped **paired**: resample rows, recompute *both* AUCs on the same
resampled rows, take the difference. The shared noise cancels and what remains is
the disagreement between the two scores. Typical paired CI width here is ±0.04 on
the difference, versus ±0.07 if the pairing were ignored.

**2. Orientation is fixed in advance, not fitted.** Each channel gets a sign from
its meaning — `positive`/`sentiment_score` count up, `negative`/`neutral`/
`indecisive`/the LouMcD hedging counts count down — and that same sign is applied
to both members of a pair. The tempting alternative, orienting each column by
whichever direction scores better (`max(a, 1-a)`), is a **winner's curse**: it can
only ever push an AUC up toward 0.5-and-above, so a pure-noise column reports
~0.51 instead of ~0.50. That matters enormously here, because FinancialBERT's
headline channels *are* pure noise (0.490–0.518), and self-orientation would have
inflated exactly the baseline that the headline-vs-body comparison is measured
against. The a priori rule independently reproduces the `(inv)` choices the
headline report made for `FinMarBa_indecisive` and `FinancialBERT_negative`.

**3. Forty-eight pairs × two thresholds is a lot of tests.** At a nominal 95%
level, 2–4 "significant" results are expected from noise alone. Every table below
carries a **Bonferroni-adjusted** interval next to the nominal one, and only the
Bonferroni column should be read as evidence. Results that clear the nominal bar
but not the corrected one are labeled "nominal only" — suggestive, not
established.

---

## 1. Head-to-head, all 48 channel pairs

Full table in `outputs/head_vs_body_encoders_report.md`. The extremes, at
`>= 30%`:

### Where the body wins

| encoder | channel | HEAD | BODY | Δ | 95% CI | Bonferroni CI | rho | verdict |
|---|---|---|---|---|---|---|---|---|
| FinancialBERT | `neutral` (inv) | 0.490 | 0.568 | **+0.078** | [+0.034, +0.125] | [+0.002, +0.155] | 0.05 | **body wins** |
| FinancialBERT | `positive` | 0.500 | 0.574 | +0.074 | [+0.031, +0.120] | [−0.000, +0.148] | 0.08 | nominal only |
| FinancialBERT | `sentiment_score` | 0.505 | 0.576 | +0.070 | [+0.027, +0.115] | [−0.002, +0.143] | 0.09 | nominal only |
| LouMcD-SDict | `negative` (inv) | 0.518 | 0.562 | +0.044 | [+0.015, +0.073] | [−0.005, +0.093] | 0.38 | nominal only |

### Where the headline wins (worst 6 of 9)

| encoder | channel | HEAD | BODY | Δ | Bonferroni CI |
|---|---|---|---|---|---|
| DeBERTa | `neutral` (inv) | 0.595 | 0.504 | **−0.091** | [−0.158, −0.024] |
| FinMarBa | `sentiment_score` | 0.598 | 0.524 | −0.074 | [−0.137, −0.011] |
| FinMarBa | `negative` (inv) | 0.582 | 0.509 | −0.073 | [−0.139, −0.008] |
| FinMarBa | `positive` | 0.608 | 0.534 | −0.073 | [−0.135, −0.012] |
| RoBERTa | `sentiment_score` | 0.567 | 0.498 | −0.069 | [−0.133, −0.006] |
| DeBERTa | `positive` | 0.607 | 0.540 | −0.066 | [−0.129, −0.004] |

**Tally at Bonferroni — `>= 30%`: body 1, head 9, tie 38.**
**Tally at Bonferroni — `>= 15%`: body 3, head 4, tie 41.**

### Reading it

1. **The headline is the better text field for 9 encoders and the body for 1.**
   Every FinMarBa channel, every RoBERTa channel, and DeBERTa's best channels are
   significantly *worse* on the body. The single best predictor in the entire
   study remains `FinMarBa_indecisive` on the **headline** at 0.631.
2. **FinancialBERT is the exception, and it is a real one.** All three of its main
   channels move together (+0.070 to +0.078), all three clear Bonferroni at the
   `>= 15%` threshold, and one clears it at `>= 30%` with the other two grazing
   zero. Four channels agreeing is much stronger evidence than one channel
   clearing a bar.
3. **The mechanism is visible in the labels, not just the AUC.**
   `FinancialBERT_label` is `neutral` on **3288 of 3306 headlines** (std 0.071,
   q50 0.0001) — it has nothing to discriminate with. On bodies it splits 2127
   positive / 1143 neutral / 36 negative (std 0.464, 1265 distinct values). Same
   story for LouMcD-SDict (neutral 2467 → 472). **These models were never broken;
   they were starved of text.** A 14-word headline is below their working minimum.
4. **rho tells you where the wins come from.** FinancialBERT's head/body Spearman
   is 0.05–0.09 — the two are essentially unrelated, because one of them is noise.
   Everywhere else rho is 0.34–0.46: head and body agree moderately, and the body
   version is a noisier copy of the same judgement.

---

## 2. Trigger behaviour — no body column is tradeable

AUC measures ranking across the whole population. Precision measures what happens
when you actually fire. Base rate 8.8%. Full matrix in
`outputs/PRstats_..._BodyEnc_TPFP_fromprhigh_tp30.tsv`.

| Trigger | TP/FP | Precision | 95% CI | Lift | Fires |
|---|---|---|---|---|---|
| **`FinMarBa_indecisive <= 0.1` (HEADLINE, for reference)** | 22/93 | **19.1%** | [13.0%, 27.3%] | **2.17×** | 115 |
| `positive >= 0.8` (HEADLINE live gate) | 59/355 | 14.3% | [11.2%, 17.9%] | 1.62× | 414 |
| `FinMarBa_Body_indecisive <= 0.1` | 72/513 | 12.3% | [9.9%, 15.2%] | 1.40× | 585 |
| `Trading-Hero_Body_sentiment_score >= 0.95` | 101/743 | 12.0% | [9.9%, 14.3%] | 1.36× | 844 |
| `FinancialBERT_Body_sentiment_score >= 0.95` | 193/1560 | 11.0% | [9.6%, 12.6%] | 1.25× | **1753** |
| `distilroberta_Body_sentiment_score >= 0.95` | 185/1609 | 10.3% | [9.0%, 11.8%] | 1.17× | 1794 |
| `LouMcD-SDict_Body_sentiment_score >= 0.9` | 146/1265 | 10.3% | [8.9%, 12.0%] | 1.17× | 1411 |
| `ModernBERT_Body_positive >= 0.95` | 42/377 | 10.0% | [7.5%, 13.3%] | 1.14× | 419 |
| `DeBERTa_Body_sentiment_score >= 0.95` | 118/1186 | 9.0% | [7.6%, 10.7%] | 1.03× | 1304 |

**The AUC win does not convert into a trigger.** This is the most important
practical result in the report. FinancialBERT's body channel wins Section 1 on
ranking, but its best trigger fires on **1753 rows — 54% of the entire corpus** —
at 11.0% precision. A signal that flags half your universe is not a signal you can
trade. Its edge is spread thinly across the whole ranking rather than concentrated
in a tail, which is precisely the distinction AUC cannot see and the TP/FP matrix
exists to expose.

Every body column's Wilson interval either overlaps or barely clears the 8.8% base
rate. **No body column is a deployable trigger at any cutoff tested.**

The `DeBERTa_Body` cell at 9.0% (1.03×) is worth naming: after paying ~928 ms of
inference per PR, it fires on 1304 rows and delivers essentially the base rate.

---

## 3. Does the body add *on top of* the headline?

A column can lose head-to-head and still earn its place if it carries independent
information. With head/body rho ≈ 0.4 there was real room for that, so it needed
testing rather than assuming.

Method: pooled **out-of-fold** logistic AUC, 5 folds, features percentile-ranked
then standardized per fold on the train fold only. Synergy = pair − better single.

> **A methodological trap worth recording.** The first version of this test
> compared a pooled-OOF *pair* against the fit-free *rank* AUCs of Section 1 and
> produced negative synergy for all 11 encoders — an implausibly clean result. The
> cause: pooling OOF predictions across folds is itself lossy, because each fold
> fits its own intercept and scale and the resulting probabilities are not strictly
> on a common scale. Measured directly, a **one-feature** logistic — which is
> monotone and *must* preserve AUC exactly — lost 0.057 AUC for finbert-tone
> (0.590 → 0.533) purely from pooling. The pair was being charged a penalty its
> baselines never paid. Both sides are now computed the same pooled-OOF way. The
> percentile-rank transform is a second fix: it is unsupervised (the target is
> never consulted, so it is not leakage) and it stops heavy-tailed columns like
> `LouMcD-SDict_Body_negative` — a raw word count with skew 3.3 and a +6.4σ
> maximum — from letting a couple of long PRs dominate the fit.

| encoder | OOF head | OOF body | OOF head+body | synergy | rho |
|---|---|---|---|---|---|
| FLANG-BERT | 0.603 | 0.569 | **0.621** | **+0.019** | 0.14 |
| Trading-Hero | 0.598 | 0.586 | **0.612** | **+0.014** | 0.36 |
| RoBERTa | 0.548 | 0.444 | 0.561 | +0.013 | 0.44 |
| LouMcD-SDict | 0.507 | 0.536 | 0.548 | +0.011 | 0.18 |
| distilroberta | 0.585 | 0.543 | 0.585 | +0.000 | 0.44 |
| FLANG-ELECTRA | 0.600 | 0.548 | 0.600 | −0.000 | 0.45 |
| FinMarBa | 0.623 | 0.571 | 0.623 | −0.001 | 0.44 |
| FinancialBERT | 0.483 | 0.561 | 0.561 | −0.001 | 0.06 |
| DeBERTa | 0.596 | 0.512 | 0.594 | −0.002 | 0.42 |
| finbert-tone | 0.576 | 0.512 | 0.573 | −0.003 | 0.37 |
| ModernBERT | 0.594 | 0.536 | 0.588 | −0.005 | 0.34 |

**Cross-encoder best pair:** `FinMarBa_indecisive` (OOF 0.623) +
`Trading-Hero_Body_sentiment_score` (OOF 0.586) → **0.640** (synergy +0.017).

**Four of eleven encoders gain more than +0.01, and the largest gain is +0.019.**
Note the pattern: the gains land where rho is *lowest* (FLANG-BERT 0.14,
LouMcD-SDict 0.18) and vanish where head and body are near-duplicates (rho ≥ 0.42
→ synergy ≈ 0). That is the expected shape and a good sign the numbers are real
rather than fitting noise. But +0.019 AUC is far below the ±0.036 resolution of
this corpus — **the synergy is directionally sensible and practically negligible.**

---

## 4. Per-class — the one place the body genuinely wins

Best achievable single-column AUC within each class, headline vs body
(`outputs/fromprhigh_class_conditional_report.md` vs `..._body.md`). Median across
all columns in brackets — the more robust statistic, since "best of 11" is itself
a maximum and biased upward.

| class | n | movers | HEAD best [median] | BODY best [median] | Δ median |
|---|---|---|---|---|---|
| **Earnings** | 508 | 26 | 0.69 [0.54] | **0.74 [0.67]** | **+0.13** |
| IP | 69 | 9 | 0.75 [0.57] | 0.75 [0.60] | +0.03 |
| Financing | 229 | 38 | 0.57 [0.53] | 0.61 [0.52] | −0.01 |
| MA | 277 | 45 | 0.65 [0.56] | 0.63 [0.55] | −0.01 |
| Other | 328 | 19 | 0.66 [0.59] | 0.64 [0.58] | −0.01 |
| Clinical | 257 | 24 | 0.66 [0.61] | 0.67 [0.59] | −0.02 |
| Commercial | 683 | 66 | 0.62 [0.57] | 0.60 [0.53] | −0.04 |
| Compliance | 127 | 15 | 0.68 [0.55] | 0.65 [0.52] | −0.03 |
| Corporate | 373 | 21 | 0.68 [0.63] | 0.62 [0.60] | −0.03 |
| **FDA** | 287 | 19 | **0.72 [0.68]** | 0.67 [0.61] | **−0.07** |

### Earnings: headline sentiment is useless, body sentiment works

Tested directly with the same paired bootstrap, within the Earnings class only:

| encoder | HEAD | BODY | Δ | 95% CI |
|---|---|---|---|---|
| DeBERTa | **0.469** | 0.738 | **+0.269** | [+0.079, +0.452] ✓ |
| Trading-Hero | **0.476** | 0.725 | **+0.249** | [+0.064, +0.426] ✓ |
| finbert-tone | 0.552 | 0.687 | +0.135 | [−0.008, +0.286] |
| distilroberta | 0.688 | 0.741 | +0.053 | [−0.085, +0.193] |

Two of four intervals exclude zero and **all four point the same direction**.
Note the headline AUCs: DeBERTa 0.469 and Trading-Hero 0.476 are *below 0.50* —
on earnings PRs, headline tone is not merely weak, it is slightly backwards. The
explanation is mundane and convincing: an earnings headline is
"XYZ Corp Reports Third Quarter 2026 Financial Results" — a template with no
sentiment content at all. The beat, the miss, and the guidance are in the body.

Caveats: 26 movers, and this is 12 tests (4 encoders × 3 classes) at a nominal
level, so one false positive is plausible. The consistency across four
independently-trained models is what makes it credible, not any single interval.

**FDA moves the opposite way** (median 0.68 → 0.61, all four encoders negative),
and Financing — which I predicted would favour the body, on the theory that
offering terms live in the body — **shows no improvement at all**. That prediction
was wrong.

---

## 5. Cost

Median inference time per PR, measured from the `_elapsed_ms` columns in the
corpus itself:

| | headline | body | ratio |
|---|---|---|---|
| all 11 encoders | **319 ms** | **3205 ms** | **10.0×** |
| FLANG-ELECTRA (worst) | 93 ms | 1021 ms | 10.9× |
| DeBERTa | 99 ms | 928 ms | 9.4× |
| distilroberta (best) | 6 ms | 56 ms | 9.2× |

In a live news→trade path where the whole point is reacting before the move, **3.2
seconds of encoder inference per PR is not affordable** for a signal that does not
beat the headline. This alone settles the deployment question independently of the
statistics.

---

## 6. Recommendations

1. **Do not add body encoder scoring to the live path.** It loses 9 pairs to 1,
   produces no tradeable trigger, adds at most +0.019 AUC in combination, and costs
   10× the inference time. The headline stays the text field of record.
2. **Correct the record on FinancialBERT and LouMcD-SDict.**
   `report-Headline-11encoders.md` recommendation #2 called them statistically dead
   and advised retiring them. That is right *for headlines* and wrong as a
   statement about the models. FinancialBERT labels 99.5% of headlines neutral but
   discriminates normally on bodies (AUC 0.505 → 0.576, the one Bonferroni-clean
   body win). **Retire them from headline scoring; do not conclude they are
   broken.** If a body-scoring context ever appears, FinancialBERT is a reasonable
   candidate there.
3. **The only body result worth pursuing is Earnings.** It is the one class where
   the body beats the headline decisively, headline tone is actively backwards
   there, and the effect is corroborated across four independent encoders. If body
   scoring is ever run selectively, run it **only on `BroadClass == Earnings`** —
   508 rows here, ~15% of PRs, which cuts the 10× cost to roughly 1.5× overall.
   Treat it as a hypothesis to validate forward, not a rule to deploy: 26 movers.
4. **Keep using the headline for FinMarBa.** `FinMarBa_indecisive` on the headline
   (0.631) is still the strongest encoder-derived signal in the study, and every
   one of its body channels is significantly worse. The open question about it —
   whether it adds incrementally to the live combo gate — is unaffected by anything
   here and still needs ~200 more gate-fires (see §2.6 of the headline report).
5. **Do not read the "body wins" cells as trading signals.** FinancialBERT's body
   channel wins on AUC and fires on 54% of the corpus at 11% precision. Ranking
   ability and trigger quality are different properties; this study contains a
   clean example of a column that has the first and not the second.

---

## 7. Caveats

- **The body was truncated to its first 200 words** by `BodyScore11encoders.py`
  (~260–290 tokens), while the median PR body in this corpus is **400 words** and
  the 90th percentile is 673. So roughly **half of a typical PR was never seen** by
  these encoders. Everything here measures a *truncated-body* encoder. A
  full-document approach (chunking and pooling, or a long-context model) is
  untested and could change the picture — most plausibly for Earnings, where the
  financial tables tend to sit past the 200-word mark.
- 287 movers set the resolution: AUC differences below ~0.036 globally, and ~0.10
  within a class, are noise. The paired bootstrap tightens the *difference* CI to
  roughly ±0.04 but cannot rescue per-class cells with 9–26 movers.
- Per-class cells marked `~` (Crypto, Legal) rest on fewer than 8 movers and must
  not be quoted.
- Everything is in-corpus ranking after the fact; real deployment adds slippage,
  fill and timing costs a rank test cannot see.
- All 11 encoders are externally trained and have never seen any row here, so the
  full 3253 rows are their valid test set. The `ArrivalDate >= 2026-06-20` block is
  the keyword family's leakage correction and is reported only as a small-sample
  stability check.

---

## Reproduce

```
# 1. merge the 70 _Body_ columns onto the canonical kw-scored TSV
/home/tom/venv/bin/python merge_body_encoder_cols.py

# 2. paired head-vs-body AUC + synergy
/home/tom/venv/bin/python head_vs_body.py --n-boot 2000

# 3. body trigger matrix
/home/tom/venv/bin/python false_positive_count.py \
  --input ../outputs/PRstats_..._kwScored_BodyEnc.tsv \
  --columns-name <11 best body channels> --true-positive-threshold 30 \
  --levels 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 0.95 \
  --invert FinMarBa_Body_indecisive FLANG-BERT_Body_negative

# 4. per-class on body columns (--report-suffix avoids overwriting the headline run)
/home/tom/venv/bin/python class_conditional_predictors.py \
  ../outputs/PRstats_..._kwScored_BodyEnc.tsv \
  --columns <11 best body channels> --triggers <specs> --report-suffix _body

# 5. full auto-detect ranking (139 numeric / 28 categorical)
/home/tom/venv/bin/python analyze_frompr_predictors.py \
  ../outputs/PRstats_..._kwScored_BodyEnc.tsv
```

Full invocations in `commands.txt`. Generated reports:
`outputs/head_vs_body_encoders_report.md`,
`outputs/fromprhigh_class_conditional_report_body.md`,
`outputs/fromprhigh_all_predictors_report[_OOSonly].md`,
`outputs/PRstats_..._BodyEnc_TPFP_fromprhigh_tp30.tsv`.
