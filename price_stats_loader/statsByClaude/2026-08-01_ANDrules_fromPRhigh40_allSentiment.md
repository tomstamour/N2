# Best AND thresholds: all sentiment scores vs `fromPR_High(%) >= 40`
**Generated:** 2026-08-01  |  **Input:** `05_PRstats_2026-07-24-2026-07-30_kwScored_FromPRhigh_11EncBodyHeadScored_CoffeeF.tsv`

## 1. Data & method

- Rows with a `fromPR_High(%)` value: **275**  |  movers (`fromPR_High(%) >= 40`): **29**  |  base rate **10.5%**.
- Window: `2026-07-24` -> `2026-07-30` (6 trading days).
- Leg columns: **25** auto-detected score columns -> **102** distinct (column, cutoff) legs after collapsing duplicate quantile cutoffs.
- Rules searched: **740,950** (1..4 legs, one leg per column) in 8.6s, pruning any prefix below 10 fires or 2 TP.
- Metrics: **fires** (rows firing), **TP** (fires that are movers), **precision** = TP/fires, **recall** = TP/29, **lift** = precision / 10.5%, Wilson 95% CI on precision, plus the median and mean `fromPR_High(%)` among fires.

> **Read every precision below as an upper bound, not an estimate.** The cutoffs were chosen on these same 275 rows: 740,950 candidate rules were scored against 29 movers, so the best few clear any bar partly by chance. The model *scores* are honest out-of-sample (all encoders are externally trained, and the kw15 model trained on pre-2026-06-20 data) — it is the threshold search that is fitted here. No null-model correction, holdout, or consistency gate is applied, per the requested configuration.

## 2. Saturated / degenerate columns

Columns whose values pile up at one end carry little threshold information: their quantile grid collapses to a few distinct cutoffs, and the resulting legs fire on a large fraction of rows, so they ride along in rules without selecting anything. Rule minimization (below) strips them, but read any that survive with this table in mind.

| column | uniq | q50 | q94 | max | % at max | cutoffs in grid |
|---|---|---|---|---|---|---|
| `FinMarBa_sentiment_score` | 272 | 0.368 | 0.725 | 0.784 | 0% | 5 |
| `FLANG-BERT_sentiment_score` | 271 | 0.586 | 0.983 | 0.993 | 1% | 5 |
| `FLANG-ELECTRA_sentiment_score` **<-** | 59 | 0.652 | 0.998 | 0.998 | 44% | 2 |
| `RoBERTa_sentiment_score` | 267 | 0.131 | 0.477 | 0.801 | 0% | 5 |
| `ModernBERT_sentiment_score` | 273 | 0.362 | 0.945 | 0.978 | 0% | 5 |
| `DeBERTa_sentiment_score` | 225 | 0.350 | 0.999 | 1.000 | 8% | 5 |
| `finbert-tone_sentiment_score` | 131 | 0.002 | 1.000 | 1.000 | 15% | 4 |
| `FinancialBERT_sentiment_score` | 47 | 0.000 | 0.004 | 0.169 | 0% | 3 |
| `distilroberta_sentiment_score` | 108 | 0.000 | 0.999 | 0.999 | 10% | 5 |
| `Trading-Hero_sentiment_score` | 143 | 0.735 | 1.000 | 1.000 | 28% | 4 |
| `LouMcD-SDict_sentiment_score` **<-** | 4 | 0.000 | 1.000 | 1.000 | 21% | 2 |
| `kw15_body_proba` | 270 | 0.469 | 0.680 | 0.799 | 0% | 5 |
| `kw15_head_proba` | 273 | 0.463 | 0.786 | 0.942 | 0% | 5 |
| `FinMarBa_Body_sentiment_score` | 270 | 0.454 | 0.666 | 0.746 | 0% | 5 |
| `FLANG-BERT_Body_sentiment_score` | 269 | 0.362 | 0.965 | 0.990 | 0% | 5 |
| `FLANG-ELECTRA_Body_sentiment_score` **<-** | 63 | 0.998 | 0.998 | 0.998 | 60% | 1 |
| `RoBERTa_Body_sentiment_score` | 268 | 0.267 | 0.558 | 0.762 | 0% | 5 |
| `ModernBERT_Body_sentiment_score` | 271 | 0.690 | 0.951 | 0.987 | 1% | 5 |
| `DeBERTa_Body_sentiment_score` | 260 | 0.848 | 0.997 | 0.999 | 2% | 5 |
| `finbert-tone_Body_sentiment_score` | 144 | 0.043 | 1.000 | 1.000 | 19% | 4 |
| `FinancialBERT_Body_sentiment_score` | 167 | 0.989 | 1.000 | 1.000 | 34% | 3 |
| `distilroberta_Body_sentiment_score` **<-** | 126 | 0.990 | 0.999 | 1.000 | 38% | 2 |
| `Trading-Hero_Body_sentiment_score` | 136 | 0.001 | 0.999 | 1.000 | 11% | 5 |
| `LouMcD-SDict_Body_sentiment_score` **<-** | 34 | 0.600 | 1.000 | 1.000 | 44% | 2 |
| `FinBERT_sentiment_score` | 270 | 0.241 | 0.858 | 0.925 | 1% | 5 |

## 3. Single-column AUC (orientation)

AUC that each column, in its fire direction, ranks a mover above a non-mover. 0.50 = no signal. This is the bar any AND rule has to beat.

| column | dir | AUC |
|---|---|---|
| `kw15_body_proba` | >= | 0.695 |
| `FLANG-BERT_sentiment_score` | >= | 0.674 |
| `kw15_head_proba` | >= | 0.674 |
| `FinBERT_sentiment_score` | >= | 0.653 |
| `Trading-Hero_sentiment_score` | >= | 0.632 |
| `FLANG-ELECTRA_sentiment_score` | >= | 0.621 |
| `distilroberta_sentiment_score` | >= | 0.615 |
| `DeBERTa_sentiment_score` | >= | 0.608 |
| `FLANG-ELECTRA_Body_sentiment_score` | >= | 0.606 |
| `FinMarBa_sentiment_score` | >= | 0.583 |
| `FinancialBERT_Body_sentiment_score` | >= | 0.576 |
| `ModernBERT_sentiment_score` | >= | 0.564 |
| `finbert-tone_sentiment_score` | >= | 0.561 |
| `DeBERTa_Body_sentiment_score` | >= | 0.553 |
| `ModernBERT_Body_sentiment_score` | >= | 0.551 |
| `FinMarBa_Body_sentiment_score` | >= | 0.546 |
| `finbert-tone_Body_sentiment_score` | >= | 0.544 |
| `distilroberta_Body_sentiment_score` | >= | 0.534 |
| `Trading-Hero_Body_sentiment_score` | >= | 0.531 |
| `RoBERTa_sentiment_score` | >= | 0.525 |
| `FLANG-BERT_Body_sentiment_score` | >= | 0.508 |
| `LouMcD-SDict_Body_sentiment_score` | >= | 0.498 |
| `LouMcD-SDict_sentiment_score` | >= | 0.471 |
| `FinancialBERT_sentiment_score` | >= | 0.435 |
| `RoBERTa_Body_sentiment_score` | >= | 0.413 |

## 4. Best rule at each depth

Precision-ranked best rule using exactly 1, 2, ... 4 legs (before minimization, floor 10 fires). This is the direct answer to whether extra legs buy real precision or just shrink the fire count into noise.

| legs | rule | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|---|
| 1 | `FinMarBa_Body_sentiment_score` >= 0.666 | 17 | 5 | 29% | 17% | 2.79x | [13%, 53%] |
| 2 | `FinMarBa_Body_sentiment_score` >= 0.666 AND `ModernBERT_Body_sentiment_score` >= 0.88 | 10 | 5 | 50% | 17% | 4.74x | [24%, 76%] |
| 3 | `FLANG-BERT_sentiment_score` >= 0.955 AND `RoBERTa_Body_sentiment_score` >= 0.346 AND `FinBERT_sentiment_score` >= 0.792 | 10 | 5 | 50% | 17% | 4.74x | [24%, 76%] |
| 4 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.792 | 11 | 6 | 55% | 21% | 5.17x | [28%, 79%] |

## 5. Top AND rules (precision-ranked, minimized, de-cloned)

| # | rule | fires | TP | precision | recall | lift | Wilson95 | median fromPR_High(%) | mean | clones |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `FLANG-BERT_sentiment_score` >= 0.955 AND `ModernBERT_sentiment_score` >= 0.362 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 40.3 | 56.0 | 1 |
| 2 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.848 AND `FinBERT_sentiment_score` >= 0.792 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 41.9 | 131.6 | 2 |
| 3 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_body_proba` >= 0.529 AND `kw15_head_proba` >= 0.707 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 40.3 | 57.4 | 0 |
| 4 | `FLANG-BERT_sentiment_score` >= 0.955 AND `kw15_body_proba` >= 0.469 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.792 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 41.9 | 131.1 | 1 |
| 5 | `FinMarBa_sentiment_score` >= 0.368 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.089 AND `kw15_head_proba` >= 0.707 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 36.2 | 59.6 | 0 |
| 6 | `FLANG-BERT_sentiment_score` >= 0.876 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.792 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 39.5 | 121.4 | 1 |
| 7 | `FLANG-BERT_sentiment_score` >= 0.955 AND `DeBERTa_sentiment_score` >= 0.35 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 36.2 | 53.2 | 1 |
| 8 | `FLANG-BERT_sentiment_score` >= 0.955 AND `RoBERTa_Body_sentiment_score` >= 0.346 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.719 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 39.5 | 126.1 | 1 |
| 9 | `FLANG-BERT_sentiment_score` >= 0.973 AND `ModernBERT_sentiment_score` >= 0.362 AND `DeBERTa_sentiment_score` >= 0.929 AND `kw15_head_proba` >= 0.615 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 36.2 | 48.3 | 2 |
| 10 | `distilroberta_sentiment_score` >= 0.089 AND `kw15_body_proba` >= 0.469 AND `FinMarBa_Body_sentiment_score` >= 0.585 AND `FLANG-BERT_Body_sentiment_score` >= 0.62 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 29.6 | 122.3 | 1 |
| 11 | `FLANG-BERT_sentiment_score` >= 0.955 AND `DeBERTa_sentiment_score` >= 0.929 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `FinBERT_sentiment_score` >= 0.792 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 39.5 | 123.4 | 1 |
| 12 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `LouMcD-SDict_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 25.5 | 51.7 | 0 |
| 13 | `FLANG-BERT_sentiment_score` >= 0.955 AND `DeBERTa_sentiment_score` >= 0.35 AND `kw15_body_proba` >= 0.529 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 32.9 | 52.3 | 2 |
| 14 | `FinMarBa_sentiment_score` >= 0.368 AND `distilroberta_sentiment_score` >= 0.089 AND `Trading-Hero_sentiment_score` >= 0.735 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 35.8 | 55.9 | 1 |
| 15 | `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.089 AND `Trading-Hero_sentiment_score` >= 0.735 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 35.8 | 55.6 | 1 |
| 16 | `FinancialBERT_sentiment_score` >= 0 AND `kw15_body_proba` >= 0.585 AND `kw15_head_proba` >= 0.707 AND `FinBERT_sentiment_score` >= 0.241 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 35.8 | 55.4 | 1 |
| 17 | `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.089 AND `kw15_body_proba` >= 0.529 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 35.8 | 57.2 | 0 |
| 18 | `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.089 AND `LouMcD-SDict_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 35.2 | 55.5 | 0 |
| 19 | `FinMarBa_sentiment_score` >= 0.368 AND `distilroberta_sentiment_score` >= 0.089 AND `LouMcD-SDict_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 35.2 | 55.8 | 0 |
| 20 | `FLANG-BERT_sentiment_score` >= 0.955 AND `LouMcD-SDict_sentiment_score` >= 0 AND `kw15_body_proba` >= 0.529 AND `kw15_head_proba` >= 0.707 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 27.6 | 50.9 | 1 |

Legs whose removal does not change the fire set have been dropped, so each rule shown is the shortest expression of its own fire set. `clones` counts further searched rules whose fire set overlaps this one at Jaccard >= 0.9 — a high count means many apparently different rules are restatements of this one.

## 6. Top AND rules (F0.5-ranked — keeps coverage)

| # | rule | fires | TP | precision | recall | lift | Wilson95 | median fromPR_High(%) | mean | clones |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_body_proba` >= 0.529 AND `kw15_head_proba` >= 0.707 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 40.3 | 57.4 | 1 |
| 2 | `FLANG-BERT_sentiment_score` >= 0.955 AND `kw15_body_proba` >= 0.469 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.792 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 41.9 | 131.1 | 2 |
| 3 | `FLANG-BERT_sentiment_score` >= 0.955 AND `ModernBERT_sentiment_score` >= 0.362 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 40.3 | 56.0 | 0 |
| 4 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.848 AND `FinBERT_sentiment_score` >= 0.792 | 12 | 6 | 50% | 21% | 4.74x | [25%, 75%] | 41.9 | 131.6 | 1 |
| 5 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.991 AND `kw15_body_proba` >= 0.469 | 22 | 9 | 41% | 31% | 3.88x | [23%, 61%] | 35.6 | 57.6 | 0 |
| 6 | `distilroberta_sentiment_score` >= 0.089 AND `kw15_body_proba` >= 0.469 AND `FinMarBa_Body_sentiment_score` >= 0.585 AND `FLANG-BERT_Body_sentiment_score` >= 0.62 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 29.6 | 122.3 | 1 |
| 7 | `FLANG-BERT_sentiment_score` >= 0.876 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.792 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 39.5 | 121.4 | 1 |
| 8 | `FLANG-BERT_sentiment_score` >= 0.955 AND `DeBERTa_sentiment_score` >= 0.35 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 36.2 | 53.2 | 1 |
| 9 | `FLANG-BERT_sentiment_score` >= 0.955 AND `DeBERTa_sentiment_score` >= 0.929 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `FinBERT_sentiment_score` >= 0.792 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 39.5 | 123.4 | 1 |
| 10 | `FinMarBa_sentiment_score` >= 0.368 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.089 AND `kw15_head_proba` >= 0.707 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 36.2 | 59.6 | 0 |
| 11 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `LouMcD-SDict_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 25.5 | 51.7 | 0 |
| 12 | `FLANG-BERT_sentiment_score` >= 0.973 AND `ModernBERT_sentiment_score` >= 0.362 AND `DeBERTa_sentiment_score` >= 0.929 AND `kw15_head_proba` >= 0.615 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 36.2 | 48.3 | 2 |
| 13 | `FLANG-BERT_sentiment_score` >= 0.955 AND `RoBERTa_Body_sentiment_score` >= 0.346 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.719 | 13 | 6 | 46% | 21% | 4.38x | [23%, 71%] | 39.5 | 126.1 | 1 |
| 14 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.991 AND `FinMarBa_Body_sentiment_score` >= 0.454 | 20 | 8 | 40% | 28% | 3.79x | [22%, 61%] | 34.1 | 48.9 | 1 |
| 15 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.991 AND `Trading-Hero_sentiment_score` >= 0.735 | 27 | 10 | 37% | 34% | 3.51x | [22%, 56%] | 32.0 | 49.8 | 5 |
| 16 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.719 | 17 | 7 | 41% | 24% | 3.90x | [22%, 64%] | 39.5 | 103.3 | 1 |
| 17 | `FLANG-BERT_sentiment_score` >= 0.876 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_body_proba` >= 0.529 AND `kw15_head_proba` >= 0.707 | 17 | 7 | 41% | 24% | 3.90x | [22%, 64%] | 35.5 | 50.0 | 0 |
| 18 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FLANG-ELECTRA_sentiment_score` >= 0.652 AND `distilroberta_sentiment_score` >= 0.991 AND `kw15_body_proba` >= 0.469 | 24 | 9 | 38% | 31% | 3.56x | [21%, 57%] | 33.5 | 54.8 | 3 |
| 19 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FLANG-ELECTRA_sentiment_score` >= 0.652 AND `distilroberta_sentiment_score` >= 0.991 AND `FinMarBa_Body_sentiment_score` >= 0.454 | 21 | 8 | 38% | 28% | 3.61x | [21%, 59%] | 32.0 | 47.7 | 4 |
| 20 | `FLANG-BERT_sentiment_score` >= 0.955 AND `DeBERTa_sentiment_score` >= 0.929 AND `DeBERTa_Body_sentiment_score` >= 0.848 AND `FinBERT_sentiment_score` >= 0.792 | 14 | 6 | 43% | 21% | 4.06x | [21%, 67%] | 30.2 | 113.6 | 1 |

Same pool ranked by F0.5 (precision-leaning but recall-aware), for an operating point that still fires often enough to matter.

## 7. `--min-fires` sensitivity

Best rule once a rule is required to fire on at least N rows. Headline precision that collapses as N rises was resting on a handful of rows.

| min-fires | rule | fires | TP | precision | lift | Wilson95 |
|---|---|---|---|---|---|---|
| 10 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.792 | 11 | 6 | 55% | 5.17x | [28%, 79%] |
| 12 | `FLANG-BERT_sentiment_score` >= 0.955 AND `ModernBERT_sentiment_score` >= 0.362 AND `FinancialBERT_sentiment_score` >= 0 AND `kw15_head_proba` >= 0.707 | 12 | 6 | 50% | 4.74x | [25%, 75%] |
| 15 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinMarBa_Body_sentiment_score` >= 0.454 AND `DeBERTa_Body_sentiment_score` >= 0.954 AND `FinBERT_sentiment_score` >= 0.719 | 17 | 7 | 41% | 3.90x | [22%, 64%] |
| 20 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.991 AND `kw15_body_proba` >= 0.469 | 22 | 9 | 41% | 3.88x | [23%, 61%] |
| 25 | `FLANG-BERT_sentiment_score` >= 0.955 AND `FLANG-ELECTRA_sentiment_score` >= 0.652 AND `FinancialBERT_sentiment_score` >= 0 AND `distilroberta_sentiment_score` >= 0.991 | 27 | 10 | 37% | 3.51x | [22%, 56%] |

## 8. Per-day fire breakdown (informational)

Fires and TP per trading day for the top rules. **Not a filter and not part of the ranking** — shown so a rule that fires on a single day is visible as such.

| # | rule | 2026-07-24 | 2026-07-26 | 2026-07-27 | 2026-07-28 | 2026-07-29 | 2026-07-30 |
|---|---|---|---|---|---|---|---|
| 1 | FLANG-BERT_sentiment_score>=0.955 AND ModernBERT_sentiment_score>=0.362 AND FinancialBERT_sentiment_score>=0 AND kw15_head_proba>=0.707 | 1/2 | 0/0 | 1/3 | 1/3 | 1/1 | 2/3 |
| 2 | FLANG-BERT_sentiment_score>=0.955 AND FinMarBa_Body_sentiment_score>=0.454 AND DeBERTa_Body_sentiment_score>=0.848 AND FinBERT_sentiment_score>=0.792 | 0/1 | 0/0 | 2/4 | 1/1 | 0/1 | 3/5 |
| 3 | FLANG-BERT_sentiment_score>=0.955 AND FinancialBERT_sentiment_score>=0 AND kw15_body_proba>=0.529 AND kw15_head_proba>=0.707 | 1/2 | 0/0 | 1/3 | 1/3 | 1/1 | 2/3 |
| 4 | FLANG-BERT_sentiment_score>=0.955 AND kw15_body_proba>=0.469 AND DeBERTa_Body_sentiment_score>=0.954 AND FinBERT_sentiment_score>=0.792 | 0/0 | 0/0 | 2/4 | 1/2 | 0/1 | 3/5 |
| 5 | FinMarBa_sentiment_score>=0.368 AND FinancialBERT_sentiment_score>=0 AND distilroberta_sentiment_score>=0.089 AND kw15_head_proba>=0.707 | 1/2 | 0/0 | 1/3 | 1/3 | 2/3 | 1/2 |
| 6 | FLANG-BERT_sentiment_score>=0.876 AND FinMarBa_Body_sentiment_score>=0.454 AND DeBERTa_Body_sentiment_score>=0.954 AND FinBERT_sentiment_score>=0.792 | 0/0 | 0/0 | 2/5 | 1/2 | 0/1 | 3/5 |
| 7 | FLANG-BERT_sentiment_score>=0.955 AND DeBERTa_sentiment_score>=0.35 AND FinancialBERT_sentiment_score>=0 AND kw15_head_proba>=0.707 | 1/2 | 0/0 | 1/3 | 1/4 | 1/1 | 2/3 |
| 8 | FLANG-BERT_sentiment_score>=0.955 AND RoBERTa_Body_sentiment_score>=0.346 AND DeBERTa_Body_sentiment_score>=0.954 AND FinBERT_sentiment_score>=0.719 | 0/1 | 0/0 | 1/3 | 1/1 | 0/2 | 4/6 |
| 9 | FLANG-BERT_sentiment_score>=0.973 AND ModernBERT_sentiment_score>=0.362 AND DeBERTa_sentiment_score>=0.929 AND kw15_head_proba>=0.615 | 0/1 | 0/0 | 2/5 | 1/3 | 0/1 | 3/3 |
| 10 | distilroberta_sentiment_score>=0.089 AND kw15_body_proba>=0.469 AND FinMarBa_Body_sentiment_score>=0.585 AND FLANG-BERT_Body_sentiment_score>=0.62 | 2/3 | 0/0 | 1/2 | 1/3 | 0/1 | 2/4 |

_Cells are TP/fires._

## 9. Passthrough legs in the top rules

Every distinct leg used by the top-20 rules, with what it does **on its own**. A leg that fires on most rows at ~1.0x lift carries no signal by itself: it survived minimization only because it clips a handful of rows, and the search kept whichever clipping happened to help. Legs flagged **<-** (standalone lift < 1.25x) are the ones to distrust first.

| leg | used in | fires alone | % of rows | precision alone | lift alone |
|---|---|---|---|---|---|
| `kw15_head_proba` >= 0.707 | 13 | 33 | 12% | 21% | 2.01x |
| `FLANG-BERT_sentiment_score` >= 0.955 | 10 | 61 | 22% | 25% | 2.33x |
| `FinancialBERT_sentiment_score` >= 0 **<-** | 9 | 239 | 87% | 11% | 1.03x |
| `distilroberta_sentiment_score` >= 0.089 | 7 | 96 | 35% | 17% | 1.58x |
| `FinBERT_sentiment_score` >= 0.792 | 4 | 33 | 12% | 18% | 1.72x |
| `kw15_body_proba` >= 0.529 | 4 | 97 | 35% | 20% | 1.86x |
| `LouMcD-SDict_sentiment_score` >= 0 **<-** | 4 | 254 | 92% | 10% | 0.97x |
| `FinMarBa_Body_sentiment_score` >= 0.454 **<-** | 3 | 137 | 50% | 12% | 1.11x |
| `DeBERTa_Body_sentiment_score` >= 0.954 **<-** | 3 | 97 | 35% | 12% | 1.17x |
| `FinMarBa_sentiment_score` >= 0.368 **<-** | 3 | 137 | 50% | 13% | 1.25x |
| `ModernBERT_sentiment_score` >= 0.362 | 2 | 138 | 50% | 14% | 1.31x |
| `kw15_body_proba` >= 0.469 | 2 | 137 | 50% | 18% | 1.66x |
| `DeBERTa_sentiment_score` >= 0.35 | 2 | 138 | 50% | 14% | 1.37x |
| `DeBERTa_sentiment_score` >= 0.929 **<-** | 2 | 96 | 35% | 12% | 1.19x |
| `Trading-Hero_sentiment_score` >= 0.735 | 2 | 137 | 50% | 15% | 1.38x |
| `DeBERTa_Body_sentiment_score` >= 0.848 **<-** | 1 | 137 | 50% | 12% | 1.18x |
| `FLANG-BERT_sentiment_score` >= 0.876 | 1 | 97 | 35% | 18% | 1.66x |
| `RoBERTa_Body_sentiment_score` >= 0.346 **<-** | 1 | 96 | 35% | 6% | 0.59x |
| `FinBERT_sentiment_score` >= 0.719 | 1 | 61 | 22% | 15% | 1.40x |
| `FLANG-BERT_sentiment_score` >= 0.973 | 1 | 32 | 12% | 25% | 2.37x |
| `kw15_head_proba` >= 0.615 | 1 | 61 | 22% | 21% | 2.02x |
| `FinMarBa_Body_sentiment_score` >= 0.585 **<-** | 1 | 61 | 22% | 11% | 1.09x |
| `FLANG-BERT_Body_sentiment_score` >= 0.62 **<-** | 1 | 96 | 35% | 10% | 0.99x |
| `kw15_body_proba` >= 0.585 | 1 | 59 | 21% | 17% | 1.61x |
| `FinBERT_sentiment_score` >= 0.241 | 1 | 137 | 50% | 15% | 1.45x |

