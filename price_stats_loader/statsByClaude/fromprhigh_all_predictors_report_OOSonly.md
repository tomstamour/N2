# fromPR_High(%) all-column predictor analysis — PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv

- **SUBSET RUN**: only the 578 rows where `model_proba` is empty.
- Rows: 3306 total -> dropped 53 null-target, 0 outliers (> 500%), 0 duplicate PRs -> **578 kept**
- fromPR_High(%) on kept rows: median=7.01  mean=16.94  max=371.00
- Big-mover threshold: **fromPR_High(%) >= 30** -> **74** big movers (12.8%)
- Candidates: 63 numeric (auto-detected), 17 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DailyHigh($), DailyHigh(%), DeBERTa_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, KwScoreCompletedAt, LouMcD-SDict_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb | 65 | 0.707 | higher->bigger | 0.207 | +0.390 | +0.399 |
| 2 | MktCap | 578 | 0.342 | lower->bigger | 0.158 | -0.232 | -0.201 |
| 3 | Float | 578 | 0.347 | lower->bigger | 0.153 | -0.186 | -0.135 |
| 4 | FinMarBa_indecisive | 578 | 0.419 | lower->bigger | 0.081 | -0.099 | -0.095 |
| 5 | nocoref_neutral_filter | 513 | 0.580 | higher->bigger | 0.080 | +0.139 | +0.124 |
| 6 | nocoref_net_score | 513 | 0.578 | higher->bigger | 0.078 | +0.130 | +0.117 |
| 7 | Trading-Hero_negative | 578 | 0.422 | lower->bigger | 0.078 | -0.091 | -0.065 |
| 8 | FLANG-ELECTRA_positive | 578 | 0.570 | higher->bigger | 0.070 | +0.118 | +0.113 |
| 9 | nocoref_top_k | 513 | 0.570 | higher->bigger | 0.070 | +0.118 | +0.100 |
| 10 | nocoref_confidence_weighted | 513 | 0.570 | higher->bigger | 0.070 | +0.138 | +0.120 |
| 11 | FLANG-ELECTRA_neutral | 578 | 0.430 | lower->bigger | 0.070 | -0.099 | -0.081 |
| 12 | nocoref_positional | 513 | 0.570 | higher->bigger | 0.070 | +0.119 | +0.112 |
| 13 | FLANG-ELECTRA_sentiment_score | 578 | 0.568 | higher->bigger | 0.068 | +0.143 | +0.129 |
| 14 | tradeSizeBaseline | 578 | 0.436 | lower->bigger | 0.064 | -0.057 | -0.027 |
| 15 | FLANG-BERT_neutral | 578 | 0.436 | lower->bigger | 0.064 | -0.100 | -0.119 |
| 16 | FLANG-BERT_positive | 578 | 0.558 | higher->bigger | 0.058 | +0.113 | +0.113 |
| 17 | FLANG-ELECTRA_negative | 578 | 0.557 | higher->bigger | 0.057 | +0.062 | -0.087 |
| 18 | neutral | 578 | 0.445 | lower->bigger | 0.055 | -0.084 | -0.094 |
| 19 | FinMarBa_positive | 578 | 0.554 | higher->bigger | 0.054 | +0.071 | +0.063 |
| 20 | positive | 578 | 0.551 | higher->bigger | 0.051 | +0.096 | +0.105 |
| 21 | DeBERTa_sentiment_score | 578 | 0.548 | higher->bigger | 0.048 | +0.083 | +0.081 |
| 22 | sentiment_score | 578 | 0.547 | higher->bigger | 0.047 | +0.100 | +0.111 |
| 23 | FinMarBa_sentiment_score | 578 | 0.545 | higher->bigger | 0.045 | +0.061 | +0.054 |
| 24 | FLANG-BERT_sentiment_score | 578 | 0.545 | higher->bigger | 0.045 | +0.109 | +0.077 |
| 25 | DeBERTa_positive | 578 | 0.544 | higher->bigger | 0.044 | +0.084 | +0.084 |
| 26 | distilroberta_sentiment_score | 578 | 0.544 | higher->bigger | 0.044 | +0.112 | +0.114 |
| 27 | Trading-Hero_sentiment_score | 578 | 0.543 | higher->bigger | 0.043 | +0.091 | +0.068 |
| 28 | RoBERTa_sentiment_score | 578 | 0.543 | higher->bigger | 0.043 | +0.074 | +0.079 |
| 29 | RoBERTa_positive | 578 | 0.541 | higher->bigger | 0.041 | +0.073 | +0.076 |
| 30 | DeBERTa_neutral | 578 | 0.461 | lower->bigger | 0.039 | -0.069 | -0.077 |
| 31 | finbert-tone_negative | 578 | 0.461 | lower->bigger | 0.039 | -0.040 | -0.032 |
| 32 | FLANG-BERT_negative | 578 | 0.465 | lower->bigger | 0.035 | -0.058 | -0.007 |
| 33 | LastDailyClose | 578 | 0.465 | lower->bigger | 0.035 | -0.057 | -0.029 |
| 34 | distilroberta_positive | 578 | 0.534 | higher->bigger | 0.034 | +0.108 | +0.114 |
| 35 | FinMarBa_negative | 578 | 0.468 | lower->bigger | 0.032 | -0.047 | -0.041 |
| 36 | negative | 578 | 0.469 | lower->bigger | 0.031 | -0.040 | -0.073 |
| 37 | FinancialBERT_neutral | 578 | 0.529 | higher->bigger | 0.029 | +0.035 | -0.035 |
| 38 | Trading-Hero_positive | 578 | 0.529 | higher->bigger | 0.029 | +0.083 | +0.055 |
| 39 | RoBERTa_neutral | 578 | 0.472 | lower->bigger | 0.028 | -0.057 | -0.067 |
| 40 | RoBERTa_negative | 578 | 0.474 | lower->bigger | 0.026 | -0.001 | -0.044 |
| 41 | FinancialBERT_negative | 578 | 0.525 | higher->bigger | 0.025 | +0.033 | -0.039 |
| 42 | LouMcD-SDict_sentiment_score | 578 | 0.525 | higher->bigger | 0.025 | +0.063 | +0.054 |
| 43 | distilroberta_neutral | 578 | 0.478 | lower->bigger | 0.022 | -0.086 | -0.109 |
| 44 | DeBERTa_negative | 578 | 0.478 | lower->bigger | 0.022 | -0.019 | -0.021 |
| 45 | ModernBERT_negative | 578 | 0.479 | lower->bigger | 0.021 | +0.006 | +0.014 |
| 46 | Trading-Hero_neutral | 578 | 0.482 | lower->bigger | 0.018 | -0.066 | -0.036 |
| 47 | finbert-tone_sentiment_score | 578 | 0.518 | higher->bigger | 0.018 | +0.083 | +0.055 |
| 48 | FinancialBERT_positive | 578 | 0.482 | lower->bigger | 0.018 | -0.052 | +0.036 |
| 49 | distilroberta_negative | 578 | 0.517 | higher->bigger | 0.017 | +0.062 | -0.019 |
| 50 | LouMcD-SDict_negative | 578 | 0.484 | lower->bigger | 0.016 | -0.055 | -0.069 |
| 51 | FinancialBERT_sentiment_score | 578 | 0.485 | lower->bigger | 0.015 | -0.046 | +0.037 |
| 52 | LouMcD-SDict_litigious | 578 | 0.486 | lower->bigger | 0.014 | -0.016 | -0.042 |
| 53 | finbert-tone_positive | 578 | 0.513 | higher->bigger | 0.013 | +0.075 | +0.052 |
| 54 | LouMcD-SDict_weak_modal | 578 | 0.512 | higher->bigger | 0.012 | +0.036 | +0.038 |
| 55 | ModernBERT_sentiment_score | 578 | 0.510 | higher->bigger | 0.010 | +0.033 | +0.027 |
| 56 | LouMcD-SDict_positive | 578 | 0.510 | higher->bigger | 0.010 | +0.038 | +0.040 |
| 57 | LouMcD-SDict_constraining | 578 | 0.509 | higher->bigger | 0.009 | +0.008 | -0.007 |
| 58 | itiBaseline | 578 | 0.505 | higher->bigger | 0.005 | -0.033 | -0.038 |
| 59 | finbert-tone_neutral | 578 | 0.495 | lower->bigger | 0.005 | -0.062 | -0.048 |
| 60 | LouMcD-SDict_uncertainty | 578 | 0.495 | lower->bigger | 0.005 | +0.022 | -0.011 |
| 61 | LouMcD-SDict_strong_modal | 578 | 0.497 | lower->bigger | 0.003 | -0.042 | -0.051 |
| 62 | ModernBERT_positive | 578 | 0.503 | higher->bigger | 0.003 | +0.037 | +0.034 |
| 63 | ModernBERT_neutral | 578 | 0.500 | lower->bigger | 0.000 | -0.041 | -0.040 |

## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)

| rank | column | n | CV-AUC | |AUC-0.5| |
|---|---|---|---|---|
| 1 | FineClass | 578 | 0.595 | 0.095 |
| 2 | FinancialBERT_label | 578 | 0.428 | 0.072 |
| 3 | finbert-tone_label | 578 | 0.429 | 0.071 |
| 4 | BroadClass | 578 | 0.565 | 0.065 |
| 5 | label | 578 | 0.435 | 0.065 |
| 6 | Exchange | 578 | 0.441 | 0.059 |
| 7 | LouMcD-SDict_label | 578 | 0.448 | 0.052 |
| 8 | FinMarBa_label | 578 | 0.456 | 0.044 |
| 9 | ModernBERT_label | 578 | 0.473 | 0.027 |
| 10 | RoBERTa_label | 578 | 0.473 | 0.027 |
| 11 | FLANG-ELECTRA_label | 578 | 0.523 | 0.023 |
| 12 | DeBERTa_label | 578 | 0.521 | 0.021 |
| 13 | Author | 578 | 0.487 | 0.013 |
| 14 | Trading-Hero_label | 578 | 0.509 | 0.009 |
| 15 | distilroberta_label | 578 | 0.496 | 0.004 |
| 16 | FLANG-BERT_label | 578 | 0.503 | 0.003 |

## Categorical breakdowns — per level (min 20 rows; baseline big-mover rate 12.8%)

### `FineClass` (CV-AUC 0.595)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 63 | 23.8% | +11.0% | 9.24 |
| Partnership | 56 | 21.4% | +8.6% | 9.13 |
| Earnings_Results | 38 | 15.8% | +3.0% | 4.20 |
| Offering_Financing | 39 | 15.4% | +2.6% | 10.22 |
| Clinical_Data | 20 | 15.0% | +2.2% | 9.29 |
| Other | 58 | 13.8% | +1.0% | 6.54 |
| <other> | 56 | 12.5% | -0.3% | 4.82 |
| FDA_Designation | 46 | 8.7% | -4.1% | 6.48 |
| Contract_Award | 37 | 8.1% | -4.7% | 7.48 |
| Product_Launch | 52 | 7.7% | -5.1% | 6.12 |
| Clinical_Update | 23 | 4.3% | -8.5% | 5.72 |
| Management_Change | 24 | 0.0% | -12.8% | 4.33 |

### `FinancialBERT_label` (CV-AUC 0.428)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 577 | 12.8% | +0.0% | 7.00 |

### `finbert-tone_label` (CV-AUC 0.429)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 158 | 13.9% | +1.1% | 7.16 |
| neutral | 418 | 12.4% | -0.4% | 7.04 |

### `BroadClass` (CV-AUC 0.565)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 63 | 23.8% | +11.0% | 9.24 |
| Earnings | 42 | 16.7% | +3.9% | 4.20 |
| Financing | 50 | 14.0% | +1.2% | 8.56 |
| Other | 58 | 13.8% | +1.0% | 6.54 |
| Commercial | 145 | 13.1% | +0.3% | 7.88 |
| Compliance | 46 | 13.0% | +0.2% | 8.41 |
| Clinical | 43 | 9.3% | -3.5% | 7.41 |
| FDA | 49 | 8.2% | -4.6% | 7.38 |
| Corporate | 55 | 0.0% | -12.8% | 5.39 |

### `label` (CV-AUC 0.435)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 466 | 13.3% | +0.5% | 7.33 |
| neutral | 105 | 10.5% | -2.3% | 6.32 |

### `Exchange` (CV-AUC 0.441)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| NASDAQ | 504 | 13.1% | +0.3% | 7.19 |
| NYSE American | 61 | 9.8% | -3.0% | 5.86 |

### `LouMcD-SDict_label` (CV-AUC 0.448)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 118 | 14.4% | +1.6% | 8.23 |
| neutral | 421 | 12.8% | +0.0% | 6.92 |
| negative | 39 | 7.7% | -5.1% | 5.75 |

### `FinMarBa_label` (CV-AUC 0.456)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 557 | 13.3% | +0.5% | 7.30 |
| negative | 21 | 0.0% | -12.8% | 4.43 |

### `ModernBERT_label` (CV-AUC 0.473)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 274 | 14.2% | +1.4% | 7.01 |
| neutral | 298 | 11.7% | -1.1% | 7.01 |

### `RoBERTa_label` (CV-AUC 0.473)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 43 | 20.9% | +8.1% | 7.43 |
| neutral | 533 | 12.2% | -0.6% | 7.02 |

### `FLANG-ELECTRA_label` (CV-AUC 0.523)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 255 | 15.7% | +2.9% | 8.28 |
| neutral | 302 | 10.9% | -1.9% | 6.25 |
| negative | 21 | 4.8% | -8.0% | 3.10 |

### `DeBERTa_label` (CV-AUC 0.521)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 270 | 15.2% | +2.4% | 7.77 |
| neutral | 291 | 11.0% | -1.8% | 6.47 |

### `Author` (CV-AUC 0.487)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| Newsfile Corp | 31 | 19.4% | +6.6% | 10.22 |
| PR Newswire | 89 | 18.0% | +5.2% | 9.56 |
| Globe Newswire | 338 | 12.1% | -0.7% | 6.82 |
| ACCESSWIRE | 73 | 11.0% | -1.8% | 6.47 |
| Business Wire | 47 | 6.4% | -6.4% | 4.43 |

### `Trading-Hero_label` (CV-AUC 0.509)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 295 | 14.6% | +1.8% | 7.02 |
| neutral | 269 | 11.5% | -1.3% | 7.38 |

### `distilroberta_label` (CV-AUC 0.496)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 167 | 16.2% | +3.4% | 8.11 |
| neutral | 407 | 11.5% | -1.3% | 6.76 |

### `FLANG-BERT_label` (CV-AUC 0.503)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| negative | 85 | 15.3% | +2.5% | 6.52 |
| positive | 346 | 14.5% | +1.6% | 7.47 |
| neutral | 147 | 7.5% | -5.3% | 6.09 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: model_proba_PLUS_kw_mult_nb, MktCap, Float, FinMarBa_indecisive, nocoref_neutral_filter, nocoref_net_score, Trading-Hero_negative, FLANG-ELECTRA_positive, nocoref_top_k, nocoref_positional, FLANG-ELECTRA_sentiment_score, tradeSizeBaseline, FLANG-BERT_neutral, FLANG-BERT_positive, FLANG-ELECTRA_negative

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): nocoref_confidence_weighted ~ nocoref_neutral_filter; FLANG-ELECTRA_neutral ~ FLANG-ELECTRA_positive

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | MktCap + Float + FinMarBa_indecisive | 578 | 0.658 | MktCap=-0.35, Float=-0.45, FinMarBa_indecisive=-0.28 |
| 2 | MktCap + FinMarBa_indecisive + Trading-Hero_negative | 578 | 0.648 | MktCap=-0.53, FinMarBa_indecisive=-0.23, Trading-Hero_negative=-0.60 |
| 3 | Float + nocoref_neutral_filter + FLANG-BERT_neutral | 513 | 0.647 | Float=-0.66, nocoref_neutral_filter=+0.26, FLANG-BERT_neutral=-0.21 |
| 4 | Float + Trading-Hero_negative + FLANG-BERT_neutral | 578 | 0.646 | Float=-0.62, Trading-Hero_negative=-0.65, FLANG-BERT_neutral=-0.30 |
| 5 | Float + nocoref_neutral_filter + Trading-Hero_negative | 513 | 0.646 | Float=-0.66, nocoref_neutral_filter=+0.25, Trading-Hero_negative=-0.58 |
| 6 | Float + FinMarBa_indecisive + Trading-Hero_negative | 578 | 0.645 | Float=-0.63, FinMarBa_indecisive=-0.27, Trading-Hero_negative=-0.57 |
| 7 | Float + FinMarBa_indecisive + FLANG-BERT_neutral | 578 | 0.645 | Float=-0.64, FinMarBa_indecisive=-0.26, FLANG-BERT_neutral=-0.24 |
| 8 | Float + nocoref_positional + FLANG-BERT_neutral | 513 | 0.644 | Float=-0.65, nocoref_positional=+0.23, FLANG-BERT_neutral=-0.21 |
| 9 | MktCap + Float + FLANG-ELECTRA_sentiment_score | 578 | 0.644 | MktCap=-0.37, Float=-0.43, FLANG-ELECTRA_sentiment_score=+0.24 |
| 10 | MktCap + Float + FLANG-ELECTRA_positive | 578 | 0.643 | MktCap=-0.36, Float=-0.43, FLANG-ELECTRA_positive=+0.21 |
| 11 | MktCap + Float + FLANG-BERT_neutral | 578 | 0.643 | MktCap=-0.35, Float=-0.44, FLANG-BERT_neutral=-0.26 |
| 12 | Float + nocoref_net_score + Trading-Hero_negative | 513 | 0.643 | Float=-0.66, nocoref_net_score=+0.25, Trading-Hero_negative=-0.58 |
| 13 | MktCap + Float + nocoref_neutral_filter | 513 | 0.643 | MktCap=-0.28, Float=-0.51, nocoref_neutral_filter=+0.28 |
| 14 | Float + Trading-Hero_negative + nocoref_positional | 513 | 0.642 | Float=-0.66, Trading-Hero_negative=-0.57, nocoref_positional=+0.23 |
| 15 | MktCap + Float + Trading-Hero_negative | 578 | 0.642 | MktCap=-0.38, Float=-0.42, Trading-Hero_negative=-0.64 |
| 16 | Float + nocoref_net_score + FLANG-BERT_neutral | 513 | 0.641 | Float=-0.65, nocoref_net_score=+0.25, FLANG-BERT_neutral=-0.21 |
| 17 | MktCap + FinMarBa_indecisive + tradeSizeBaseline | 578 | 0.641 | MktCap=-0.53, FinMarBa_indecisive=-0.27, tradeSizeBaseline=-0.20 |
| 18 | MktCap + Trading-Hero_negative + FLANG-BERT_neutral | 578 | 0.641 | MktCap=-0.52, Trading-Hero_negative=-0.66, FLANG-BERT_neutral=-0.27 |
| 19 | MktCap + Float + nocoref_net_score | 513 | 0.641 | MktCap=-0.28, Float=-0.50, nocoref_net_score=+0.27 |
| 20 | MktCap + Float + nocoref_positional | 513 | 0.640 | MktCap=-0.27, Float=-0.51, nocoref_positional=+0.25 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb + tradeSizeBaseline | 2 | 0.286 |
| 2 | model_proba_PLUS_kw_mult_nb | 1 | 0.707 |
| 3 | model_proba_PLUS_kw_mult_nb + Trading-Hero_negative + tradeSizeBaseline | 3 | 0.303 |
| 4 | model_proba_PLUS_kw_mult_nb + tradeSizeBaseline + FLANG-ELECTRA_negative | 3 | 0.305 |
| 5 | model_proba_PLUS_kw_mult_nb + FLANG-ELECTRA_positive + tradeSizeBaseline | 3 | 0.313 |
| 6 | model_proba_PLUS_kw_mult_nb + tradeSizeBaseline + FLANG-BERT_positive | 3 | 0.313 |
| 7 | model_proba_PLUS_kw_mult_nb + FLANG-ELECTRA_sentiment_score + tradeSizeBaseline | 3 | 0.328 |
| 8 | MktCap + Float + FinMarBa_indecisive | 3 | 0.658 |
| 9 | MktCap | 1 | 0.342 |
| 10 | Float | 1 | 0.347 |
| 11 | MktCap + FinMarBa_indecisive + Trading-Hero_negative | 3 | 0.648 |
| 12 | Float + nocoref_neutral_filter + FLANG-BERT_neutral | 3 | 0.647 |
| 13 | Float + Trading-Hero_negative + FLANG-BERT_neutral | 3 | 0.646 |
| 14 | Float + nocoref_neutral_filter + Trading-Hero_negative | 3 | 0.646 |
| 15 | Float + FinMarBa_indecisive + Trading-Hero_negative | 3 | 0.645 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | model_proba_ | MktCap | Float | FinMarBa_ind | nocoref_neut | nocoref_net_ | Trading-Hero | FLANG-ELECTR | nocoref_top_ | nocoref_posi | FLANG-ELECTR | tradeSizeBas | FLANG-BERT_n | FLANG-BERT_p | FLANG-ELECTR |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| model_proba_PLUS | 1.00 | -0.12 | -0.01 | -0.13 | — | — | -0.27 | -0.09 | — | — | -0.04 | 0.09 | -0.12 | 0.14 | -0.09 |
| MktCap | -0.12 | 1.00 | 0.55 | 0.06 | -0.06 | -0.05 | 0.03 | -0.06 | -0.05 | -0.07 | -0.04 | 0.08 | 0.06 | -0.07 | -0.06 |
| Float | -0.01 | 0.55 | 1.00 | 0.00 | 0.00 | -0.01 | 0.04 | -0.06 | -0.01 | 0.00 | -0.06 | 0.50 | -0.01 | -0.04 | -0.03 |
| FinMarBa_indecis | -0.13 | 0.06 | 0.00 | 1.00 | -0.27 | -0.29 | 0.44 | -0.41 | -0.19 | -0.31 | -0.44 | -0.05 | 0.21 | -0.49 | -0.20 |
| nocoref_neutral_ | — | -0.06 | 0.00 | -0.27 | 1.00 | 0.94 | -0.23 | 0.37 | 0.79 | 0.92 | 0.41 | 0.10 | -0.28 | 0.43 | 0.20 |
| nocoref_net_scor | — | -0.05 | -0.01 | -0.29 | 0.94 | 1.00 | -0.24 | 0.40 | 0.80 | 0.95 | 0.44 | 0.07 | -0.27 | 0.45 | 0.21 |
| Trading-Hero_neg | -0.27 | 0.03 | 0.04 | 0.44 | -0.23 | -0.24 | 1.00 | -0.46 | -0.18 | -0.30 | -0.52 | 0.04 | 0.26 | -0.53 | -0.21 |
| FLANG-ELECTRA_po | -0.09 | -0.06 | -0.06 | -0.41 | 0.37 | 0.40 | -0.46 | 1.00 | 0.37 | 0.44 | 0.91 | 0.02 | -0.56 | 0.66 | 0.78 |
| nocoref_top_k | — | -0.05 | -0.01 | -0.19 | 0.79 | 0.80 | -0.18 | 0.37 | 1.00 | 0.80 | 0.40 | 0.05 | -0.30 | 0.40 | 0.24 |
| nocoref_position | — | -0.07 | 0.00 | -0.31 | 0.92 | 0.95 | -0.30 | 0.44 | 0.80 | 1.00 | 0.48 | 0.09 | -0.29 | 0.47 | 0.24 |
| FLANG-ELECTRA_se | -0.04 | -0.04 | -0.06 | -0.44 | 0.41 | 0.44 | -0.52 | 0.91 | 0.40 | 0.48 | 1.00 | 0.03 | -0.48 | 0.68 | 0.51 |
| tradeSizeBaselin | 0.09 | 0.08 | 0.50 | -0.05 | 0.10 | 0.07 | 0.04 | 0.02 | 0.05 | 0.09 | 0.03 | 1.00 | -0.07 | 0.04 | 0.01 |
| FLANG-BERT_neutr | -0.12 | 0.06 | -0.01 | 0.21 | -0.28 | -0.27 | 0.26 | -0.56 | -0.30 | -0.29 | -0.48 | -0.07 | 1.00 | -0.69 | -0.45 |
| FLANG-BERT_posit | 0.14 | -0.07 | -0.04 | -0.49 | 0.43 | 0.45 | -0.53 | 0.66 | 0.40 | 0.47 | 0.68 | 0.04 | -0.69 | 1.00 | 0.38 |
| FLANG-ELECTRA_ne | -0.09 | -0.06 | -0.03 | -0.20 | 0.20 | 0.21 | -0.21 | 0.78 | 0.24 | 0.24 | 0.51 | 0.01 | -0.45 | 0.38 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| model_proba_PLUS_kw_mult_nb | 0.664 | 0.694 | 0.664 | 0.707 | 0.629 |
| MktCap | 0.378 | 0.375 | 0.347 | 0.342 | 0.305 |
| Float | 0.402 | 0.387 | 0.365 | 0.347 | 0.319 |
| FinMarBa_indecisive | 0.440 | 0.420 | 0.458 | 0.419 | 0.434 |
| nocoref_neutral_filter | 0.570 | 0.582 | 0.569 | 0.580 | 0.568 |
| nocoref_net_score | 0.567 | 0.571 | 0.555 | 0.578 | 0.575 |
| Trading-Hero_negative | 0.451 | 0.442 | 0.454 | 0.422 | 0.447 |
| FLANG-ELECTRA_positive | 0.563 | 0.568 | 0.553 | 0.570 | 0.581 |
| nocoref_top_k | 0.559 | 0.560 | 0.547 | 0.570 | 0.577 |
| nocoref_positional | 0.563 | 0.566 | 0.545 | 0.570 | 0.568 |
| FLANG-ELECTRA_sentiment_score | 0.573 | 0.575 | 0.554 | 0.568 | 0.570 |
| tradeSizeBaseline | 0.461 | 0.445 | 0.436 | 0.436 | 0.423 |
| FLANG-BERT_neutral | 0.444 | 0.450 | 0.459 | 0.436 | 0.433 |
| FLANG-BERT_positive | 0.570 | 0.565 | 0.535 | 0.558 | 0.576 |
| FLANG-ELECTRA_negative | 0.534 | 0.543 | 0.538 | 0.557 | 0.554 |
| _n big movers_ | 225 | 155 | 117 | 74 | 44 |

## Conclusion

- **Best numeric single:** `model_proba_PLUS_kw_mult_nb` (AUC 0.707, higher->bigger).
- **Best categorical:** `FineClass` (CV-AUC 0.595).
- **Best combo:** `MktCap + Float + FinMarBa_indecisive` (CV-AUC 0.658; lift over best single: -0.049).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

