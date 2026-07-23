# fromPR_High(%) all-column predictor analysis — PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv

- Rows: 3306 total -> dropped 53 null-target, 0 outliers (> 500%), 0 duplicate PRs -> **3253 kept**
- fromPR_High(%) on kept rows: median=5.17  mean=13.22  max=455.65
- Big-mover threshold: **fromPR_High(%) >= 30** -> **287** big movers (8.8%)
- Candidates: 91 numeric (auto-detected), 17 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DailyHigh($), DailyHigh(%), DeBERTa_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, KwScoreCompletedAt, LouMcD-SDict_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb | 2740 | 0.892 | higher->bigger | 0.392 | +0.473 | +0.541 |
| 2 | model_proba_pctl | 2675 | 0.892 | higher->bigger | 0.392 | +0.472 | +0.483 |
| 3 | model_proba | 2675 | 0.892 | higher->bigger | 0.392 | +0.472 | +0.520 |
| 4 | model_proba_pctl_hl | 2675 | 0.864 | higher->bigger | 0.364 | +0.396 | +0.412 |
| 5 | model_proba_hl | 2675 | 0.864 | higher->bigger | 0.364 | +0.396 | +0.440 |
| 6 | kw_mult_nb_log_pctl_hl | 2675 | 0.851 | higher->bigger | 0.351 | +0.372 | +0.395 |
| 7 | kw_mult_nb_log_hl | 2675 | 0.851 | higher->bigger | 0.351 | +0.372 | +0.425 |
| 8 | kw_mult_nb_pctl_hl | 2675 | 0.851 | higher->bigger | 0.351 | +0.373 | +0.396 |
| 9 | kw_mult_nb_hl | 2675 | 0.851 | higher->bigger | 0.351 | +0.373 | +0.426 |
| 10 | kw_add_log_hl | 2675 | 0.825 | higher->bigger | 0.325 | +0.356 | +0.377 |
| 11 | kw_add_log_pctl_hl | 2675 | 0.825 | higher->bigger | 0.325 | +0.356 | +0.370 |
| 12 | kw_add_first_hl | 2675 | 0.825 | higher->bigger | 0.325 | +0.358 | +0.380 |
| 13 | kw_add_first_pctl_hl | 2675 | 0.825 | higher->bigger | 0.325 | +0.358 | +0.372 |
| 14 | kw_add_lin_hl | 2675 | 0.825 | higher->bigger | 0.325 | +0.354 | +0.374 |
| 15 | kw_add_lin_pctl_hl | 2675 | 0.825 | higher->bigger | 0.325 | +0.354 | +0.369 |
| 16 | kw_add_first | 2675 | 0.809 | higher->bigger | 0.309 | +0.356 | +0.377 |
| 17 | kw_add_first_pctl | 2675 | 0.809 | higher->bigger | 0.309 | +0.356 | +0.366 |
| 18 | kw_add_log | 2675 | 0.803 | higher->bigger | 0.303 | +0.353 | +0.371 |
| 19 | kw_add_log_pctl | 2675 | 0.803 | higher->bigger | 0.303 | +0.353 | +0.361 |
| 20 | kw_mult_nb | 2675 | 0.797 | higher->bigger | 0.297 | +0.358 | +0.325 |
| 21 | kw_mult_nb_pctl | 2675 | 0.797 | higher->bigger | 0.297 | +0.358 | +0.366 |
| 22 | kw_mult_nb_log_pctl | 2675 | 0.793 | higher->bigger | 0.293 | +0.354 | +0.361 |
| 23 | kw_mult_nb_log | 2675 | 0.791 | higher->bigger | 0.291 | +0.353 | +0.304 |
| 24 | kw_add_lin_pctl | 2675 | 0.782 | higher->bigger | 0.282 | +0.327 | +0.331 |
| 25 | kw_add_lin | 2675 | 0.782 | higher->bigger | 0.282 | +0.327 | +0.334 |
| 26 | nocoref_recommended | 913 | 0.709 | higher->bigger | 0.209 | +0.119 | +0.138 |
| 27 | Float | 3253 | 0.323 | lower->bigger | 0.177 | -0.176 | -0.155 |
| 28 | Headline-RBscore | 2675 | 0.661 | higher->bigger | 0.161 | +0.191 | +0.204 |
| 29 | n_kw_hits | 2675 | 0.653 | higher->bigger | 0.153 | +0.173 | +0.188 |
| 30 | MktCap | 675 | 0.369 | lower->bigger | 0.131 | -0.208 | -0.079 |
| 31 | FinMarBa_indecisive | 3253 | 0.369 | lower->bigger | 0.131 | -0.158 | -0.160 |
| 32 | LastDailyClose | 3251 | 0.369 | lower->bigger | 0.131 | -0.184 | -0.081 |
| 33 | n_kw_hits_hl | 2675 | 0.611 | higher->bigger | 0.111 | +0.111 | +0.108 |
| 34 | positive | 3177 | 0.610 | higher->bigger | 0.110 | +0.151 | +0.154 |
| 35 | nocoref_confidence_weighted | 3147 | 0.610 | higher->bigger | 0.110 | +0.145 | +0.153 |
| 36 | nocoref_net_score | 3147 | 0.610 | higher->bigger | 0.110 | +0.140 | +0.158 |
| 37 | FLANG-BERT_positive | 3253 | 0.610 | higher->bigger | 0.110 | +0.131 | +0.144 |
| 38 | FLANG-BERT_sentiment_score | 3253 | 0.609 | higher->bigger | 0.109 | +0.135 | +0.130 |
| 39 | FinMarBa_positive | 3253 | 0.608 | higher->bigger | 0.108 | +0.129 | +0.136 |
| 40 | FLANG-ELECTRA_sentiment_score | 3253 | 0.607 | higher->bigger | 0.107 | +0.140 | +0.134 |
| 41 | Trading-Hero_sentiment_score | 3253 | 0.607 | higher->bigger | 0.107 | +0.159 | +0.150 |
| 42 | DeBERTa_positive | 3253 | 0.607 | higher->bigger | 0.107 | +0.145 | +0.140 |
| 43 | FLANG-ELECTRA_positive | 3253 | 0.606 | higher->bigger | 0.106 | +0.124 | +0.129 |
| 44 | sentiment_score | 3253 | 0.605 | higher->bigger | 0.105 | +0.149 | +0.150 |
| 45 | nocoref_neutral_filter | 3147 | 0.604 | higher->bigger | 0.104 | +0.142 | +0.153 |
| 46 | ModernBERT_positive | 3253 | 0.604 | higher->bigger | 0.104 | +0.134 | +0.143 |
| 47 | FLANG-BERT_neutral | 3253 | 0.397 | lower->bigger | 0.103 | -0.106 | -0.101 |
| 48 | DeBERTa_sentiment_score | 3253 | 0.603 | higher->bigger | 0.103 | +0.146 | +0.142 |
| 49 | nocoref_positional | 3147 | 0.603 | higher->bigger | 0.103 | +0.139 | +0.148 |
| 50 | Trading-Hero_negative | 3253 | 0.398 | lower->bigger | 0.102 | -0.124 | -0.062 |
| 51 | FLANG-ELECTRA_neutral | 3253 | 0.399 | lower->bigger | 0.101 | -0.106 | -0.107 |
| 52 | ModernBERT_sentiment_score | 3253 | 0.601 | higher->bigger | 0.101 | +0.129 | +0.137 |
| 53 | distilroberta_sentiment_score | 3253 | 0.599 | higher->bigger | 0.099 | +0.145 | +0.118 |
| 54 | FinMarBa_sentiment_score | 3253 | 0.598 | higher->bigger | 0.098 | +0.118 | +0.126 |
| 55 | Trading-Hero_positive | 3253 | 0.597 | higher->bigger | 0.097 | +0.156 | +0.145 |
| 56 | negative | 3253 | 0.403 | lower->bigger | 0.097 | -0.125 | -0.092 |
| 57 | ModernBERT_neutral | 3253 | 0.404 | lower->bigger | 0.096 | -0.127 | -0.136 |
| 58 | nocoref_top_k | 3147 | 0.596 | higher->bigger | 0.096 | +0.126 | +0.130 |
| 59 | DeBERTa_neutral | 3253 | 0.405 | lower->bigger | 0.095 | -0.125 | -0.121 |
| 60 | neutral | 3253 | 0.406 | lower->bigger | 0.094 | -0.107 | -0.114 |
| 61 | finbert-tone_sentiment_score | 3253 | 0.590 | higher->bigger | 0.090 | +0.133 | +0.113 |
| 62 | distilroberta_positive | 3253 | 0.589 | higher->bigger | 0.089 | +0.131 | +0.115 |
| 63 | finbert-tone_positive | 3253 | 0.586 | higher->bigger | 0.086 | +0.123 | +0.104 |
| 64 | FinMarBa_negative | 3253 | 0.418 | lower->bigger | 0.082 | -0.100 | -0.109 |
| 65 | Trading-Hero_neutral | 3253 | 0.418 | lower->bigger | 0.082 | -0.132 | -0.125 |
| 66 | finbert-tone_neutral | 3253 | 0.427 | lower->bigger | 0.073 | -0.102 | -0.087 |
| 67 | distilroberta_neutral | 3253 | 0.430 | lower->bigger | 0.070 | -0.105 | -0.107 |
| 68 | RoBERTa_sentiment_score | 3253 | 0.567 | higher->bigger | 0.067 | +0.077 | +0.080 |
| 69 | FLANG-ELECTRA_negative | 3253 | 0.567 | higher->bigger | 0.067 | +0.059 | -0.058 |
| 70 | RoBERTa_positive | 3253 | 0.565 | higher->bigger | 0.065 | +0.076 | +0.069 |
| 71 | tradeSizeBaseline | 1126 | 0.441 | lower->bigger | 0.059 | -0.062 | -0.035 |
| 72 | FLANG-BERT_negative | 3253 | 0.441 | lower->bigger | 0.059 | -0.078 | -0.065 |
| 73 | RoBERTa_neutral | 3253 | 0.450 | lower->bigger | 0.050 | -0.052 | -0.050 |
| 74 | RoBERTa_negative | 3253 | 0.453 | lower->bigger | 0.047 | -0.032 | -0.068 |
| 75 | distilroberta_negative | 3253 | 0.543 | higher->bigger | 0.043 | +0.073 | -0.032 |
| 76 | itiBaseline | 1126 | 0.462 | lower->bigger | 0.038 | -0.052 | -0.044 |
| 77 | ModernBERT_negative | 3253 | 0.463 | lower->bigger | 0.037 | -0.033 | -0.040 |
| 78 | DeBERTa_negative | 3253 | 0.535 | higher->bigger | 0.035 | -0.006 | -0.053 |
| 79 | LouMcD-SDict_sentiment_score | 3253 | 0.534 | higher->bigger | 0.034 | +0.066 | +0.070 |
| 80 | finbert-tone_negative | 3253 | 0.471 | lower->bigger | 0.029 | -0.044 | -0.060 |
| 81 | LouMcD-SDict_positive | 3253 | 0.523 | higher->bigger | 0.023 | +0.038 | +0.046 |
| 82 | LouMcD-SDict_negative | 3253 | 0.482 | lower->bigger | 0.018 | -0.064 | -0.073 |
| 83 | FinancialBERT_negative | 3253 | 0.482 | lower->bigger | 0.018 | -0.013 | -0.005 |
| 84 | LouMcD-SDict_uncertainty | 3253 | 0.486 | lower->bigger | 0.014 | -0.025 | -0.032 |
| 85 | LouMcD-SDict_litigious | 3253 | 0.486 | lower->bigger | 0.014 | -0.014 | -0.015 |
| 86 | FinancialBERT_neutral | 3253 | 0.510 | higher->bigger | 0.010 | +0.013 | -0.010 |
| 87 | LouMcD-SDict_weak_modal | 3253 | 0.490 | lower->bigger | 0.010 | -0.050 | -0.054 |
| 88 | FinancialBERT_sentiment_score | 3253 | 0.505 | higher->bigger | 0.005 | -0.002 | +0.013 |
| 89 | LouMcD-SDict_constraining | 3253 | 0.504 | higher->bigger | 0.004 | +0.027 | +0.026 |
| 90 | LouMcD-SDict_strong_modal | 3253 | 0.501 | higher->bigger | 0.001 | +0.023 | +0.025 |
| 91 | FinancialBERT_positive | 3253 | 0.500 | higher->bigger | 0.000 | -0.006 | +0.012 |

## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)

| rank | column | n | CV-AUC | |AUC-0.5| |
|---|---|---|---|---|
| 1 | FineClass | 3253 | 0.617 | 0.117 |
| 2 | BroadClass | 3253 | 0.597 | 0.097 |
| 3 | FLANG-ELECTRA_label | 3253 | 0.575 | 0.075 |
| 4 | FLANG-BERT_label | 3253 | 0.570 | 0.070 |
| 5 | DeBERTa_label | 3253 | 0.563 | 0.063 |
| 6 | ModernBERT_label | 3253 | 0.561 | 0.061 |
| 7 | Trading-Hero_label | 3253 | 0.555 | 0.055 |
| 8 | FinancialBERT_label | 3253 | 0.449 | 0.051 |
| 9 | boilerplate_category | 3253 | 0.549 | 0.049 |
| 10 | Author | 3253 | 0.545 | 0.045 |
| 11 | label | 3253 | 0.533 | 0.033 |
| 12 | RoBERTa_label | 3253 | 0.478 | 0.022 |
| 13 | distilroberta_label | 3253 | 0.521 | 0.021 |
| 14 | finbert-tone_label | 3253 | 0.516 | 0.016 |
| 15 | FinMarBa_label | 3253 | 0.484 | 0.016 |
| 16 | LouMcD-SDict_label | 3253 | 0.506 | 0.006 |
| 17 | Exchange | 3253 | 0.505 | 0.005 |

## Categorical breakdowns — per level (min 20 rows; baseline big-mover rate 8.8%)

### `FineClass` (CV-AUC 0.617)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| Offering_Financing | 207 | 16.9% | +8.1% | 9.54 |
| MA | 277 | 16.2% | +7.4% | 7.27 |
| Partnership | 241 | 13.7% | +4.9% | 5.97 |
| Clinical_Data | 119 | 13.4% | +4.6% | 6.40 |
| Business_Update | 98 | 13.3% | +4.4% | 6.14 |
| Patent_IP | 69 | 13.0% | +4.2% | 4.45 |
| <other> | 247 | 8.9% | +0.1% | 4.97 |
| Product_Launch | 270 | 8.5% | -0.3% | 5.16 |
| Crypto_Treasury | 65 | 7.7% | -1.1% | 6.19 |
| FDA_Designation | 266 | 6.0% | -2.8% | 3.79 |
| Contract_Award | 172 | 5.8% | -3.0% | 5.23 |
| Clinical_Update | 138 | 5.8% | -3.0% | 5.46 |
| Other | 328 | 5.8% | -3.0% | 4.56 |
| Earnings_Results | 481 | 5.2% | -3.6% | 4.35 |
| Management_Change | 156 | 3.2% | -5.6% | 4.33 |
| Conference_Presentation | 119 | 2.5% | -6.3% | 3.89 |

### `BroadClass` (CV-AUC 0.597)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| Financing | 229 | 16.6% | +7.8% | 8.53 |
| MA | 277 | 16.2% | +7.4% | 7.27 |
| IP | 69 | 13.0% | +4.2% | 4.45 |
| Compliance | 127 | 11.8% | +3.0% | 8.46 |
| Commercial | 683 | 9.7% | +0.8% | 5.46 |
| Clinical | 257 | 9.3% | +0.5% | 5.71 |
| Crypto | 65 | 7.7% | -1.1% | 6.19 |
| FDA | 287 | 6.6% | -2.2% | 4.00 |
| Other | 328 | 5.8% | -3.0% | 4.56 |
| Corporate | 373 | 5.6% | -3.2% | 4.48 |
| Earnings | 508 | 5.1% | -3.7% | 4.27 |
| Legal | 50 | 0.0% | -8.8% | 2.71 |

### `FLANG-ELECTRA_label` (CV-AUC 0.575)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1287 | 12.7% | +3.8% | 6.02 |
| neutral | 1852 | 6.4% | -2.4% | 4.70 |
| negative | 114 | 4.4% | -4.4% | 3.15 |

### `FLANG-BERT_label` (CV-AUC 0.570)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1703 | 11.5% | +2.6% | 5.69 |
| negative | 500 | 7.4% | -1.4% | 4.49 |
| neutral | 1050 | 5.2% | -3.6% | 4.57 |

### `DeBERTa_label` (CV-AUC 0.563)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1359 | 12.1% | +3.3% | 5.94 |
| neutral | 1798 | 6.6% | -2.3% | 4.75 |
| negative | 96 | 4.2% | -4.7% | 3.53 |

### `ModernBERT_label` (CV-AUC 0.561)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1294 | 12.3% | +3.5% | 5.75 |
| neutral | 1890 | 6.8% | -2.1% | 4.72 |
| negative | 69 | 0.0% | -8.8% | 4.27 |

### `Trading-Hero_label` (CV-AUC 0.555)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1390 | 11.7% | +2.9% | 6.04 |
| neutral | 1749 | 6.9% | -2.0% | 4.57 |
| negative | 114 | 3.5% | -5.3% | 3.98 |

### `FinancialBERT_label` (CV-AUC 0.449)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 3236 | 8.8% | -0.0% | 5.17 |

### `boilerplate_category` (CV-AUC 0.549)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| compliance | 120 | 13.3% | +4.5% | 7.62 |
| <missing> | 2175 | 10.4% | +1.6% | 5.50 |
| conference | 229 | 5.7% | -3.1% | 4.22 |
| earnings | 504 | 4.6% | -4.3% | 4.47 |
| lawfirm | 225 | 3.6% | -5.3% | 3.94 |

### `Author` (CV-AUC 0.545)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| PR Newswire | 509 | 11.4% | +2.6% | 5.05 |
| Newsfile Corp | 129 | 10.1% | +1.3% | 5.80 |
| Globe Newswire | 1764 | 9.6% | +0.8% | 5.77 |
| ACCESSWIRE | 336 | 7.7% | -1.1% | 5.24 |
| Business Wire | 515 | 4.1% | -4.7% | 3.66 |

### `label` (CV-AUC 0.533)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2415 | 10.1% | +1.3% | 5.53 |
| negative | 99 | 5.1% | -3.8% | 3.05 |
| neutral | 663 | 5.0% | -3.8% | 4.32 |
| <other> | 63 | 4.8% | -4.1% | 4.08 |

### `RoBERTa_label` (CV-AUC 0.478)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 238 | 13.9% | +5.0% | 5.35 |
| neutral | 3004 | 8.5% | -0.4% | 5.12 |

### `distilroberta_label` (CV-AUC 0.521)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 790 | 12.8% | +4.0% | 6.08 |
| neutral | 2434 | 7.6% | -1.2% | 4.88 |
| negative | 29 | 0.0% | -8.8% | 4.15 |

### `finbert-tone_label` (CV-AUC 0.516)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 794 | 12.3% | +3.5% | 6.04 |
| neutral | 2422 | 7.8% | -1.0% | 4.98 |
| negative | 37 | 0.0% | -8.8% | 1.64 |

### `FinMarBa_label` (CV-AUC 0.484)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3042 | 9.2% | +0.4% | 5.29 |
| negative | 210 | 2.9% | -6.0% | 3.99 |

### `LouMcD-SDict_label` (CV-AUC 0.506)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 603 | 10.8% | +2.0% | 5.63 |
| neutral | 2433 | 8.7% | -0.2% | 5.17 |
| negative | 217 | 5.1% | -3.8% | 4.29 |

### `Exchange` (CV-AUC 0.505)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| NYSE AMERICAN | 192 | 12.0% | +3.2% | 5.84 |
| NASDAQ | 2652 | 9.3% | +0.5% | 5.35 |
| NYSE American | 130 | 6.9% | -1.9% | 5.30 |
| NYSE | 277 | 3.2% | -5.6% | 3.23 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: model_proba_PLUS_kw_mult_nb, model_proba_pctl_hl, kw_mult_nb_log_pctl_hl, kw_add_log_hl, kw_add_first, kw_mult_nb, kw_add_lin_pctl, nocoref_recommended, Float, Headline-RBscore, n_kw_hits, MktCap, FinMarBa_indecisive, LastDailyClose, n_kw_hits_hl

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): model_proba_pctl ~ model_proba_PLUS_kw_mult_nb; model_proba ~ model_proba_PLUS_kw_mult_nb; model_proba_hl ~ model_proba_pctl_hl; kw_mult_nb_log_hl ~ kw_mult_nb_log_pctl_hl; kw_mult_nb_pctl_hl ~ kw_mult_nb_log_pctl_hl; kw_mult_nb_hl ~ kw_mult_nb_log_pctl_hl; kw_add_log_pctl_hl ~ kw_add_log_hl; kw_add_first_hl ~ kw_add_log_hl; kw_add_first_pctl_hl ~ kw_add_log_hl; kw_add_lin_hl ~ kw_add_log_hl; kw_add_lin_pctl_hl ~ kw_add_log_hl; kw_add_first_pctl ~ kw_add_first; kw_add_log ~ kw_add_first; kw_add_log_pctl ~ kw_add_first; kw_mult_nb_pctl ~ kw_mult_nb; kw_mult_nb_log_pctl ~ kw_mult_nb; kw_mult_nb_log ~ kw_mult_nb; kw_add_lin ~ kw_add_lin_pctl

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Float | 2675 | 0.896 | model_proba_PLUS_kw_mult_nb=+0.82, model_proba_pctl_hl=+0.83, Float=-0.25 |
| 2 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_add_log_hl | 2675 | 0.896 | model_proba_PLUS_kw_mult_nb=+0.88, model_proba_pctl_hl=+1.31, kw_add_log_hl=-0.49 |
| 3 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + Float | 2675 | 0.895 | model_proba_PLUS_kw_mult_nb=+0.88, kw_mult_nb_log_pctl_hl=+0.68, Float=-0.25 |
| 4 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Headline-RBscore | 2675 | 0.895 | model_proba_PLUS_kw_mult_nb=+0.90, model_proba_pctl_hl=+0.97, Headline-RBscore=-0.28 |
| 5 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + n_kw_hits | 2675 | 0.894 | model_proba_PLUS_kw_mult_nb=+1.00, model_proba_pctl_hl=+0.87, n_kw_hits=-0.34 |
| 6 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + nocoref_recommended | 913 | 0.893 | model_proba_PLUS_kw_mult_nb=+0.83, kw_mult_nb_log_pctl_hl=+0.58, nocoref_recommended=+0.48 |
| 7 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_add_first | 2675 | 0.893 | model_proba_PLUS_kw_mult_nb=+1.01, model_proba_pctl_hl=+0.91, kw_add_first=-0.31 |
| 8 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl | 2675 | 0.893 | model_proba_PLUS_kw_mult_nb=+0.84, model_proba_pctl_hl=+0.86 |
| 9 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + FinMarBa_indecisive | 2675 | 0.893 | model_proba_PLUS_kw_mult_nb=+0.84, model_proba_pctl_hl=+0.83, FinMarBa_indecisive=-0.15 |
| 10 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_mult_nb_log_pctl_hl | 2675 | 0.893 | model_proba_PLUS_kw_mult_nb=+0.80, model_proba_pctl_hl=+0.60, kw_mult_nb_log_pctl_hl=+0.36 |
| 11 | model_proba_PLUS_kw_mult_nb + Float + FinMarBa_indecisive | 2740 | 0.893 | model_proba_PLUS_kw_mult_nb=+1.13, Float=-0.30, FinMarBa_indecisive=-0.25 |
| 12 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_mult_nb | 2675 | 0.892 | model_proba_PLUS_kw_mult_nb=+0.82, model_proba_pctl_hl=+0.84, kw_mult_nb=+0.11 |
| 13 | model_proba_PLUS_kw_mult_nb + Float | 2740 | 0.892 | model_proba_PLUS_kw_mult_nb=+1.16, Float=-0.31 |
| 14 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + n_kw_hits | 2675 | 0.892 | model_proba_PLUS_kw_mult_nb=+1.06, kw_mult_nb_log_pctl_hl=+0.71, n_kw_hits=-0.34 |
| 15 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_add_lin_pctl | 2675 | 0.892 | model_proba_PLUS_kw_mult_nb=+0.92, model_proba_pctl_hl=+0.89, kw_add_lin_pctl=-0.19 |
| 16 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + Headline-RBscore | 2675 | 0.892 | model_proba_PLUS_kw_mult_nb=+0.96, kw_mult_nb_log_pctl_hl=+0.78, Headline-RBscore=-0.24 |
| 17 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + FinMarBa_indecisive | 2675 | 0.891 | model_proba_PLUS_kw_mult_nb=+0.90, kw_mult_nb_log_pctl_hl=+0.68, FinMarBa_indecisive=-0.17 |
| 18 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + n_kw_hits_hl | 2675 | 0.891 | model_proba_PLUS_kw_mult_nb=+0.86, model_proba_pctl_hl=+0.88, n_kw_hits_hl=-0.09 |
| 19 | model_proba_PLUS_kw_mult_nb + kw_add_log_hl + Float | 2675 | 0.891 | model_proba_PLUS_kw_mult_nb=+1.01, kw_add_log_hl=+0.31, Float=-0.28 |
| 20 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + kw_mult_nb | 2675 | 0.891 | model_proba_PLUS_kw_mult_nb=+0.86, kw_mult_nb_log_pctl_hl=+0.68, kw_mult_nb=+0.17 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Float | 3 | 0.896 |
| 2 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_add_log_hl | 3 | 0.896 |
| 3 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + Float | 3 | 0.895 |
| 4 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Headline-RBscore | 3 | 0.895 |
| 5 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + n_kw_hits | 3 | 0.894 |
| 6 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + nocoref_recommended | 3 | 0.893 |
| 7 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_add_first | 3 | 0.893 |
| 8 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl | 2 | 0.893 |
| 9 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + FinMarBa_indecisive | 3 | 0.893 |
| 10 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_mult_nb_log_pctl_hl | 3 | 0.893 |
| 11 | model_proba_PLUS_kw_mult_nb + Float + FinMarBa_indecisive | 3 | 0.893 |
| 12 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + kw_mult_nb | 3 | 0.892 |
| 13 | model_proba_PLUS_kw_mult_nb + Float | 2 | 0.892 |
| 14 | model_proba_PLUS_kw_mult_nb | 1 | 0.892 |
| 15 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + n_kw_hits | 3 | 0.892 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | model_proba_ | model_proba_ | kw_mult_nb_l | kw_add_log_h | kw_add_first | kw_mult_nb | kw_add_lin_p | nocoref_reco | Float | Headline-RBs | n_kw_hits | MktCap | FinMarBa_ind | LastDailyClo | n_kw_hits_hl |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| model_proba_PLUS | 1.00 | 0.72 | 0.64 | 0.67 | 0.79 | 0.79 | 0.78 | 0.26 | -0.24 | 0.41 | 0.43 | -0.18 | -0.29 | -0.29 | 0.13 |
| model_proba_pctl | 0.72 | 1.00 | 0.80 | 0.94 | 0.60 | 0.62 | 0.58 | 0.20 | -0.21 | 0.46 | 0.31 | -0.18 | -0.32 | -0.23 | 0.15 |
| kw_mult_nb_log_p | 0.64 | 0.80 | 1.00 | 0.71 | 0.53 | 0.55 | 0.49 | 0.25 | -0.20 | 0.33 | 0.27 | -0.15 | -0.27 | -0.23 | -0.06 |
| kw_add_log_hl | 0.67 | 0.94 | 0.71 | 1.00 | 0.58 | 0.60 | 0.57 | 0.20 | -0.20 | 0.49 | 0.29 | -0.17 | -0.32 | -0.20 | 0.13 |
| kw_add_first | 0.79 | 0.60 | 0.53 | 0.58 | 1.00 | 0.88 | 0.85 | 0.28 | -0.21 | 0.41 | 0.39 | -0.24 | -0.31 | -0.30 | 0.14 |
| kw_mult_nb | 0.79 | 0.62 | 0.55 | 0.60 | 0.88 | 1.00 | 0.79 | 0.23 | -0.22 | 0.40 | 0.49 | -0.21 | -0.28 | -0.26 | 0.12 |
| kw_add_lin_pctl | 0.78 | 0.58 | 0.49 | 0.57 | 0.85 | 0.79 | 1.00 | 0.19 | -0.18 | 0.36 | 0.38 | -0.22 | -0.22 | -0.24 | 0.15 |
| nocoref_recommen | 0.26 | 0.20 | 0.25 | 0.20 | 0.28 | 0.23 | 0.19 | 1.00 | -0.08 | 0.28 | 0.06 | — | -0.36 | -0.11 | -0.18 |
| Float | -0.24 | -0.21 | -0.20 | -0.20 | -0.21 | -0.22 | -0.18 | -0.08 | 1.00 | -0.12 | -0.14 | 0.57 | 0.08 | 0.06 | -0.04 |
| Headline-RBscore | 0.41 | 0.46 | 0.33 | 0.49 | 0.41 | 0.40 | 0.36 | 0.28 | -0.12 | 1.00 | 0.23 | -0.09 | -0.37 | -0.15 | 0.19 |
| n_kw_hits | 0.43 | 0.31 | 0.27 | 0.29 | 0.39 | 0.49 | 0.38 | 0.06 | -0.14 | 0.23 | 1.00 | -0.17 | -0.07 | -0.13 | 0.16 |
| MktCap | -0.18 | -0.18 | -0.15 | -0.17 | -0.24 | -0.21 | -0.22 | — | 0.57 | -0.09 | -0.17 | 1.00 | 0.08 | 0.25 | -0.19 |
| FinMarBa_indecis | -0.29 | -0.32 | -0.27 | -0.32 | -0.31 | -0.28 | -0.22 | -0.36 | 0.08 | -0.37 | -0.07 | 0.08 | 1.00 | 0.18 | -0.02 |
| LastDailyClose | -0.29 | -0.23 | -0.23 | -0.20 | -0.30 | -0.26 | -0.24 | -0.11 | 0.06 | -0.15 | -0.13 | 0.25 | 0.18 | 1.00 | -0.00 |
| n_kw_hits_hl | 0.13 | 0.15 | -0.06 | 0.13 | 0.14 | 0.12 | 0.15 | -0.18 | -0.04 | 0.19 | 0.16 | -0.19 | -0.02 | -0.00 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| model_proba_PLUS_kw_mult_nb | 0.800 | 0.908 | 0.901 | 0.892 | 0.877 |
| model_proba_pctl_hl | 0.764 | 0.864 | 0.860 | 0.864 | 0.850 |
| kw_mult_nb_log_pctl_hl | 0.750 | 0.848 | 0.848 | 0.851 | 0.838 |
| kw_add_log_hl | 0.737 | 0.822 | 0.821 | 0.825 | 0.814 |
| kw_add_first | 0.721 | 0.796 | 0.796 | 0.809 | 0.815 |
| kw_mult_nb | 0.724 | 0.798 | 0.797 | 0.797 | 0.808 |
| kw_add_lin_pctl | 0.710 | 0.770 | 0.767 | 0.782 | 0.784 |
| nocoref_recommended | 0.551 | 0.592 | 0.618 | 0.709 | 0.698 |
| Float | 0.396 | 0.381 | 0.359 | 0.323 | 0.288 |
| Headline-RBscore | 0.610 | 0.640 | 0.645 | 0.661 | 0.660 |
| n_kw_hits | 0.607 | 0.631 | 0.634 | 0.653 | 0.657 |
| MktCap | 0.389 | 0.387 | 0.364 | 0.369 | 0.358 |
| FinMarBa_indecisive | 0.413 | 0.397 | 0.400 | 0.369 | 0.367 |
| LastDailyClose | 0.405 | 0.396 | 0.386 | 0.369 | 0.350 |
| n_kw_hits_hl | 0.581 | 0.597 | 0.611 | 0.611 | 0.596 |
| _n big movers_ | 989 | 649 | 485 | 287 | 169 |

## Conclusion

- **Best numeric single:** `model_proba_PLUS_kw_mult_nb` (AUC 0.892, higher->bigger).
- **Best categorical:** `FineClass` (CV-AUC 0.617).
- **Best combo:** `model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Float` (CV-AUC 0.896; lift over best single: +0.004).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

