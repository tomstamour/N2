# fromPR_High(%) all-column predictor analysis — 04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv

- Rows: 3533 total -> dropped 99 null-target, 2 outliers (> 500%), 6 duplicate PRs -> **3426 kept**
- fromPR_High(%) on kept rows: median=6.40  mean=16.97  max=478.18
- Big-mover threshold: **fromPR_High(%) >= 40** -> **317** big movers (9.3%)
- Candidates: 115 numeric (auto-detected), 29 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DeBERTa_Body_elapsed_ms, DeBERTa_elapsed_ms, FLANG-BERT_Body_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_Body_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_Body_elapsed_ms, FinMarBa_elapsed_ms, FinancialBERT_Body_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, LouMcD-SDict_Body_elapsed_ms, LouMcD-SDict_elapsed_ms, ModernBERT_Body_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_Body_elapsed_ms, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_Body_elapsed_ms, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_Body_elapsed_ms, distilroberta_elapsed_ms, finbert-tone_Body_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | nocoref_recommended | 906 | 0.677 | higher->bigger | 0.177 | +0.198 | +0.199 |
| 2 | FLANG-BERT_sentiment_score | 3426 | 0.677 | higher->bigger | 0.177 | +0.282 | +0.273 |
| 3 | FLANG-BERT_positive | 3426 | 0.676 | higher->bigger | 0.176 | +0.270 | +0.274 |
| 4 | Float | 3426 | 0.327 | lower->bigger | 0.173 | -0.236 | -0.200 |
| 5 | nocoref_net_score | 3117 | 0.667 | higher->bigger | 0.167 | +0.273 | +0.273 |
| 6 | FinMarBa_indecisive | 3426 | 0.337 | lower->bigger | 0.163 | -0.251 | -0.245 |
| 7 | FLANG-ELECTRA_sentiment_score | 3426 | 0.658 | higher->bigger | 0.158 | +0.266 | +0.252 |
| 8 | nocoref_confidence_weighted | 3117 | 0.657 | higher->bigger | 0.157 | +0.269 | +0.266 |
| 9 | DeBERTa_positive | 3426 | 0.656 | higher->bigger | 0.156 | +0.287 | +0.264 |
| 10 | FLANG-ELECTRA_positive | 3426 | 0.654 | higher->bigger | 0.154 | +0.257 | +0.260 |
| 11 | positive | 3350 | 0.653 | higher->bigger | 0.153 | +0.291 | +0.277 |
| 12 | DeBERTa_sentiment_score | 3426 | 0.653 | higher->bigger | 0.153 | +0.290 | +0.253 |
| 13 | nocoref_neutral_filter | 3117 | 0.650 | higher->bigger | 0.150 | +0.259 | +0.257 |
| 14 | sentiment_score | 3426 | 0.649 | higher->bigger | 0.149 | +0.278 | +0.257 |
| 15 | nocoref_positional | 3117 | 0.649 | higher->bigger | 0.149 | +0.262 | +0.253 |
| 16 | FLANG-BERT_neutral | 3426 | 0.353 | lower->bigger | 0.147 | -0.176 | -0.153 |
| 17 | nocoref_top_k | 3117 | 0.646 | higher->bigger | 0.146 | +0.234 | +0.238 |
| 18 | LastDailyClose | 3424 | 0.354 | lower->bigger | 0.146 | -0.249 | -0.121 |
| 19 | Trading-Hero_sentiment_score | 3426 | 0.645 | higher->bigger | 0.145 | +0.267 | +0.252 |
| 20 | FLANG-ELECTRA_neutral | 3426 | 0.357 | lower->bigger | 0.143 | -0.229 | -0.235 |
| 21 | DeBERTa_neutral | 3426 | 0.357 | lower->bigger | 0.143 | -0.263 | -0.244 |
| 22 | Trading-Hero_positive | 3426 | 0.641 | higher->bigger | 0.141 | +0.265 | +0.259 |
| 23 | distilroberta_sentiment_score | 3426 | 0.638 | higher->bigger | 0.138 | +0.258 | +0.195 |
| 24 | ModernBERT_positive | 3426 | 0.633 | higher->bigger | 0.133 | +0.228 | +0.231 |
| 25 | neutral | 3426 | 0.368 | lower->bigger | 0.132 | -0.232 | -0.220 |
| 26 | distilroberta_positive | 3426 | 0.630 | higher->bigger | 0.130 | +0.249 | +0.196 |
| 27 | negative | 3426 | 0.370 | lower->bigger | 0.130 | -0.245 | -0.115 |
| 28 | Trading-Hero_neutral | 3426 | 0.371 | lower->bigger | 0.129 | -0.244 | -0.239 |
| 29 | Trading-Hero_Body_positive | 3426 | 0.628 | higher->bigger | 0.128 | +0.197 | +0.161 |
| 30 | Trading-Hero_Body_sentiment_score | 3426 | 0.628 | higher->bigger | 0.128 | +0.215 | +0.181 |
| 31 | FinMarBa_positive | 3426 | 0.627 | higher->bigger | 0.127 | +0.222 | +0.217 |
| 32 | ModernBERT_sentiment_score | 3426 | 0.627 | higher->bigger | 0.127 | +0.222 | +0.218 |
| 33 | ModernBERT_neutral | 3426 | 0.373 | lower->bigger | 0.127 | -0.220 | -0.222 |
| 34 | FinancialBERT_Body_sentiment_score | 3426 | 0.626 | higher->bigger | 0.126 | +0.229 | +0.232 |
| 35 | FinancialBERT_Body_positive | 3426 | 0.626 | higher->bigger | 0.126 | +0.225 | +0.229 |
| 36 | FinancialBERT_Body_neutral | 3426 | 0.379 | lower->bigger | 0.121 | -0.213 | -0.216 |
| 37 | Trading-Hero_negative | 3426 | 0.381 | lower->bigger | 0.119 | -0.226 | -0.062 |
| 38 | FinMarBa_sentiment_score | 3426 | 0.615 | higher->bigger | 0.115 | +0.208 | +0.202 |
| 39 | distilroberta_neutral | 3426 | 0.391 | lower->bigger | 0.109 | -0.226 | -0.189 |
| 40 | Trading-Hero_Body_neutral | 3426 | 0.392 | lower->bigger | 0.108 | -0.159 | -0.131 |
| 41 | FinMarBa_Body_indecisive | 3426 | 0.392 | lower->bigger | 0.108 | -0.235 | -0.228 |
| 42 | finbert-tone_sentiment_score | 3426 | 0.607 | higher->bigger | 0.107 | +0.195 | +0.165 |
| 43 | finbert-tone_positive | 3426 | 0.605 | higher->bigger | 0.105 | +0.189 | +0.157 |
| 44 | FLANG-BERT_Body_sentiment_score | 3426 | 0.601 | higher->bigger | 0.101 | +0.181 | +0.163 |
| 45 | FLANG-BERT_negative | 3426 | 0.399 | lower->bigger | 0.101 | -0.191 | -0.174 |
| 46 | FinancialBERT_Body_negative | 3426 | 0.399 | lower->bigger | 0.101 | -0.212 | -0.074 |
| 47 | FLANG-BERT_Body_positive | 3426 | 0.598 | higher->bigger | 0.098 | +0.170 | +0.154 |
| 48 | RoBERTa_positive | 3426 | 0.597 | higher->bigger | 0.097 | +0.169 | +0.129 |
| 49 | RoBERTa_sentiment_score | 3426 | 0.597 | higher->bigger | 0.097 | +0.167 | +0.139 |
| 50 | MktCap | 875 | 0.403 | lower->bigger | 0.097 | -0.203 | -0.082 |
| 51 | FLANG-ELECTRA_Body_positive | 3426 | 0.595 | higher->bigger | 0.095 | +0.218 | +0.204 |
| 52 | FinMarBa_negative | 3426 | 0.405 | lower->bigger | 0.095 | -0.185 | -0.178 |
| 53 | finbert-tone_neutral | 3426 | 0.407 | lower->bigger | 0.093 | -0.165 | -0.139 |
| 54 | distilroberta_Body_positive | 3426 | 0.593 | higher->bigger | 0.093 | +0.211 | +0.204 |
| 55 | distilroberta_Body_sentiment_score | 3426 | 0.592 | higher->bigger | 0.092 | +0.220 | +0.210 |
| 56 | FLANG-BERT_Body_neutral | 3426 | 0.411 | lower->bigger | 0.089 | -0.152 | -0.137 |
| 57 | FLANG-ELECTRA_negative | 3426 | 0.587 | higher->bigger | 0.087 | +0.139 | -0.061 |
| 58 | FLANG-ELECTRA_Body_neutral | 3426 | 0.418 | lower->bigger | 0.082 | -0.181 | -0.154 |
| 59 | ModernBERT_Body_positive | 3426 | 0.582 | higher->bigger | 0.082 | +0.175 | +0.179 |
| 60 | distilroberta_Body_neutral | 3426 | 0.420 | lower->bigger | 0.080 | -0.174 | -0.174 |
| 61 | FLANG-BERT_Body_negative | 3426 | 0.421 | lower->bigger | 0.079 | -0.117 | -0.075 |
| 62 | RoBERTa_neutral | 3426 | 0.421 | lower->bigger | 0.079 | -0.143 | -0.106 |
| 63 | ModernBERT_Body_sentiment_score | 3426 | 0.577 | higher->bigger | 0.077 | +0.170 | +0.172 |
| 64 | FLANG-ELECTRA_Body_sentiment_score | 3426 | 0.576 | higher->bigger | 0.076 | +0.199 | +0.210 |
| 65 | distilroberta_negative | 3426 | 0.574 | higher->bigger | 0.074 | +0.168 | -0.026 |
| 66 | DeBERTa_Body_positive | 3426 | 0.572 | higher->bigger | 0.072 | +0.187 | +0.185 |
| 67 | DeBERTa_Body_sentiment_score | 3426 | 0.572 | higher->bigger | 0.072 | +0.186 | +0.194 |
| 68 | FMB_indecisive | 135 | 0.429 | lower->bigger | 0.071 | -0.270 | -0.269 |
| 69 | finbert-tone_Body_sentiment_score | 3426 | 0.566 | higher->bigger | 0.066 | +0.174 | +0.136 |
| 70 | ModernBERT_Body_neutral | 3426 | 0.437 | lower->bigger | 0.063 | -0.128 | -0.126 |
| 71 | Trading-Hero_Body_negative | 3426 | 0.439 | lower->bigger | 0.061 | -0.091 | -0.133 |
| 72 | finbert-tone_Body_positive | 3426 | 0.560 | higher->bigger | 0.060 | +0.156 | +0.122 |
| 73 | RoBERTa_negative | 3426 | 0.443 | lower->bigger | 0.057 | -0.082 | -0.088 |
| 74 | tradeSizeBaseline | 1322 | 0.447 | lower->bigger | 0.053 | -0.056 | -0.025 |
| 75 | FLANG-ELECTRA_Body_negative | 3426 | 0.553 | higher->bigger | 0.053 | +0.095 | -0.121 |
| 76 | ModernBERT_Body_negative | 3426 | 0.447 | lower->bigger | 0.053 | -0.140 | -0.140 |
| 77 | finbert-tone_Body_neutral | 3426 | 0.452 | lower->bigger | 0.048 | -0.130 | -0.099 |
| 78 | distilroberta_Body_negative | 3426 | 0.547 | higher->bigger | 0.047 | +0.037 | -0.097 |
| 79 | itiBaseline | 1322 | 0.454 | lower->bigger | 0.046 | -0.059 | -0.053 |
| 80 | FMB_negative | 135 | 0.542 | higher->bigger | 0.042 | -0.183 | -0.159 |
| 81 | LouMcD-SDict_Body_sentiment_score | 3426 | 0.539 | higher->bigger | 0.039 | +0.067 | +0.080 |
| 82 | DeBERTa_Body_neutral | 3426 | 0.461 | lower->bigger | 0.039 | -0.110 | -0.117 |
| 83 | FinMarBa_Body_positive | 3426 | 0.538 | higher->bigger | 0.038 | +0.162 | +0.165 |
| 84 | LouMcD-SDict_Body_weak_modal | 3426 | 0.464 | lower->bigger | 0.036 | -0.083 | -0.081 |
| 85 | LouMcD-SDict_Body_negative | 3426 | 0.465 | lower->bigger | 0.035 | -0.034 | -0.071 |
| 86 | FinancialBERT_negative | 3426 | 0.466 | lower->bigger | 0.034 | -0.061 | -0.013 |
| 87 | DeBERTa_negative | 3426 | 0.532 | higher->bigger | 0.032 | +0.013 | -0.055 |
| 88 | LouMcD-SDict_sentiment_score | 3426 | 0.531 | higher->bigger | 0.031 | +0.103 | +0.099 |
| 89 | DeBERTa_Body_negative | 3426 | 0.472 | lower->bigger | 0.028 | -0.116 | -0.134 |
| 90 | LouMcD-SDict_Body_strong_modal | 3426 | 0.473 | lower->bigger | 0.027 | -0.106 | -0.125 |
| 91 | LouMcD-SDict_positive | 3426 | 0.524 | higher->bigger | 0.024 | +0.078 | +0.081 |
| 92 | FinMarBa_Body_sentiment_score | 3426 | 0.523 | higher->bigger | 0.023 | +0.140 | +0.143 |
| 93 | ModernBERT_negative | 3426 | 0.478 | lower->bigger | 0.022 | -0.071 | -0.056 |
| 94 | FinancialBERT_neutral | 3426 | 0.520 | higher->bigger | 0.020 | +0.032 | -0.018 |
| 95 | FMB_sentiment_score | 135 | 0.480 | lower->bigger | 0.020 | +0.211 | +0.190 |
| 96 | RoBERTa_Body_sentiment_score | 3426 | 0.519 | higher->bigger | 0.019 | +0.070 | +0.055 |
| 97 | RoBERTa_Body_positive | 3426 | 0.519 | higher->bigger | 0.019 | +0.071 | +0.050 |
| 98 | FinancialBERT_sentiment_score | 3426 | 0.516 | higher->bigger | 0.016 | +0.015 | +0.027 |
| 99 | LouMcD-SDict_negative | 3426 | 0.484 | lower->bigger | 0.016 | -0.067 | -0.070 |
| 100 | finbert-tone_negative | 3426 | 0.485 | lower->bigger | 0.015 | -0.056 | -0.063 |
| 101 | RoBERTa_Body_neutral | 3426 | 0.485 | lower->bigger | 0.015 | -0.066 | -0.042 |
| 102 | RoBERTa_Body_negative | 3426 | 0.513 | higher->bigger | 0.013 | +0.002 | -0.046 |
| 103 | LouMcD-SDict_weak_modal | 3426 | 0.489 | lower->bigger | 0.011 | -0.064 | -0.062 |
| 104 | finbert-tone_Body_negative | 3426 | 0.489 | lower->bigger | 0.011 | -0.094 | -0.088 |
| 105 | LouMcD-SDict_Body_litigious | 3426 | 0.489 | lower->bigger | 0.011 | -0.012 | -0.026 |
| 106 | LouMcD-SDict_litigious | 3426 | 0.491 | lower->bigger | 0.009 | -0.001 | -0.003 |
| 107 | LouMcD-SDict_uncertainty | 3426 | 0.494 | lower->bigger | 0.006 | -0.018 | -0.016 |
| 108 | LouMcD-SDict_constraining | 3426 | 0.506 | higher->bigger | 0.006 | +0.026 | +0.026 |
| 109 | LouMcD-SDict_Body_positive | 3426 | 0.505 | higher->bigger | 0.005 | +0.071 | +0.058 |
| 110 | LouMcD-SDict_Body_constraining | 3426 | 0.504 | higher->bigger | 0.004 | +0.036 | +0.028 |
| 111 | LouMcD-SDict_Body_uncertainty | 3426 | 0.504 | higher->bigger | 0.004 | +0.013 | +0.018 |
| 112 | FinancialBERT_positive | 3426 | 0.502 | higher->bigger | 0.002 | -0.001 | +0.024 |
| 113 | LouMcD-SDict_strong_modal | 3426 | 0.502 | higher->bigger | 0.002 | +0.008 | +0.010 |
| 114 | FMB_positive | 135 | 0.498 | lower->bigger | 0.002 | +0.228 | +0.210 |
| 115 | FinMarBa_Body_negative | 3426 | 0.499 | lower->bigger | 0.001 | -0.106 | -0.108 |

## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)

| rank | column | n | CV-AUC | |AUC-0.5| |
|---|---|---|---|---|
| 1 | FineClass | 3426 | 0.628 | 0.128 |
| 2 | FLANG-BERT_label | 3426 | 0.611 | 0.111 |
| 3 | FLANG-ELECTRA_label | 3426 | 0.611 | 0.111 |
| 4 | BroadClass | 3426 | 0.605 | 0.105 |
| 5 | DeBERTa_label | 3426 | 0.596 | 0.096 |
| 6 | Trading-Hero_label | 3426 | 0.596 | 0.096 |
| 7 | ModernBERT_label | 3426 | 0.591 | 0.091 |
| 8 | FinancialBERT_Body_label | 3426 | 0.586 | 0.086 |
| 9 | Trading-Hero_Body_label | 3426 | 0.569 | 0.069 |
| 10 | distilroberta_label | 3426 | 0.566 | 0.066 |
| 11 | distilroberta_Body_label | 3426 | 0.564 | 0.064 |
| 12 | FLANG-ELECTRA_Body_label | 3426 | 0.560 | 0.060 |
| 13 | DeBERTa_Body_label | 3426 | 0.558 | 0.058 |
| 14 | finbert-tone_label | 3426 | 0.554 | 0.054 |
| 15 | label | 3426 | 0.552 | 0.052 |
| 16 | FLANG-BERT_Body_label | 3426 | 0.552 | 0.052 |
| 17 | Author | 3426 | 0.549 | 0.049 |
| 18 | ModernBERT_Body_label | 3426 | 0.531 | 0.031 |
| 19 | finbert-tone_Body_label | 3426 | 0.528 | 0.028 |
| 20 | FMB_label | 3426 | 0.475 | 0.025 |
| 21 | Exchange | 3426 | 0.525 | 0.025 |
| 22 | FinancialBERT_label | 3426 | 0.480 | 0.020 |
| 23 | LouMcD-SDict_label | 3426 | 0.516 | 0.016 |
| 24 | FinMarBa_Body_label | 3426 | 0.484 | 0.016 |
| 25 | RoBERTa_Body_label | 3426 | 0.492 | 0.008 |
| 26 | LouMcD-SDict_Body_label | 3426 | 0.506 | 0.006 |
| 27 | FinMarBa_label | 3426 | 0.505 | 0.005 |
| 28 | boilerplate_category | 3426 | 0.504 | 0.004 |
| 29 | RoBERTa_label | 3426 | 0.500 | 0.000 |

## Categorical breakdowns — per level (min 20 rows; baseline big-mover rate 9.3%)

### `FineClass` (CV-AUC 0.628)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 291 | 20.3% | +11.0% | 11.28 |
| Clinical_Data | 127 | 15.7% | +6.5% | 7.96 |
| Partnership | 256 | 15.2% | +6.0% | 9.66 |
| Contract_Award | 180 | 12.8% | +3.5% | 8.45 |
| Business_Update | 103 | 12.6% | +3.4% | 7.67 |
| Patent_IP | 73 | 11.0% | +1.7% | 7.63 |
| Offering_Financing | 217 | 10.1% | +0.9% | 7.14 |
| Crypto_Treasury | 68 | 8.8% | -0.4% | 8.14 |
| FDA_Designation | 283 | 8.5% | -0.8% | 5.45 |
| Product_Launch | 288 | 8.0% | -1.3% | 7.10 |
| Clinical_Update | 151 | 7.3% | -2.0% | 8.59 |
| <other> | 266 | 6.8% | -2.5% | 4.06 |
| Other | 347 | 5.2% | -4.1% | 6.12 |
| Earnings_Results | 487 | 4.9% | -4.3% | 4.32 |
| Conference_Presentation | 123 | 3.3% | -6.0% | 4.32 |
| Management_Change | 166 | 3.0% | -6.2% | 4.50 |

### `FLANG-BERT_label` (CV-AUC 0.611)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1806 | 13.4% | +4.1% | 8.33 |
| negative | 522 | 5.6% | -3.7% | 4.33 |
| neutral | 1098 | 4.2% | -5.1% | 5.22 |

### `FLANG-ELECTRA_label` (CV-AUC 0.611)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1376 | 14.8% | +5.5% | 9.44 |
| neutral | 1930 | 5.6% | -3.6% | 5.01 |
| negative | 120 | 4.2% | -5.1% | 4.00 |

### `BroadClass` (CV-AUC 0.605)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 291 | 20.3% | +11.0% | 11.28 |
| Commercial | 724 | 11.7% | +2.5% | 8.20 |
| Clinical | 278 | 11.2% | +1.9% | 8.46 |
| IP | 73 | 11.0% | +1.7% | 7.63 |
| Financing | 240 | 10.4% | +1.2% | 7.21 |
| Crypto | 68 | 8.8% | -0.4% | 8.14 |
| Compliance | 140 | 8.6% | -0.7% | 4.06 |
| FDA | 307 | 8.5% | -0.8% | 5.71 |
| Corporate | 392 | 5.6% | -3.6% | 4.95 |
| Other | 347 | 5.2% | -4.1% | 6.12 |
| Earnings | 514 | 4.9% | -4.4% | 4.21 |
| Legal | 52 | 0.0% | -9.3% | 2.95 |

### `DeBERTa_label` (CV-AUC 0.596)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1449 | 13.9% | +4.7% | 8.99 |
| neutral | 1875 | 5.9% | -3.3% | 5.02 |
| negative | 102 | 3.9% | -5.3% | 4.39 |

### `Trading-Hero_label` (CV-AUC 0.596)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1474 | 13.6% | +4.4% | 9.18 |
| neutral | 1829 | 6.1% | -3.1% | 4.94 |
| negative | 123 | 3.3% | -6.0% | 4.43 |

### `ModernBERT_label` (CV-AUC 0.591)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1385 | 13.6% | +4.3% | 8.60 |
| neutral | 1966 | 6.5% | -2.7% | 5.28 |
| negative | 75 | 1.3% | -7.9% | 4.43 |

### `FinancialBERT_Body_label` (CV-AUC 0.586)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2216 | 11.9% | +2.6% | 7.87 |
| neutral | 1175 | 4.6% | -4.7% | 4.49 |
| negative | 35 | 0.0% | -9.3% | 2.33 |

### `Trading-Hero_Body_label` (CV-AUC 0.569)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1056 | 13.8% | +4.6% | 9.05 |
| neutral | 2283 | 7.5% | -1.8% | 5.74 |
| negative | 87 | 0.0% | -9.3% | 3.00 |

### `distilroberta_label` (CV-AUC 0.566)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 849 | 15.1% | +5.8% | 9.74 |
| neutral | 2547 | 7.4% | -1.8% | 5.55 |
| negative | 30 | 0.0% | -9.3% | 4.55 |

### `distilroberta_Body_label` (CV-AUC 0.564)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2113 | 11.5% | +2.3% | 7.92 |
| neutral | 1214 | 5.7% | -3.6% | 4.90 |
| negative | 99 | 4.0% | -5.2% | 2.50 |

### `FLANG-ELECTRA_Body_label` (CV-AUC 0.560)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2275 | 11.2% | +1.9% | 7.69 |
| neutral | 960 | 5.8% | -3.4% | 4.84 |
| negative | 191 | 3.7% | -5.6% | 3.18 |

### `DeBERTa_Body_label` (CV-AUC 0.558)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2175 | 11.1% | +1.9% | 7.49 |
| neutral | 994 | 6.6% | -2.6% | 5.34 |
| negative | 257 | 3.5% | -5.8% | 3.33 |

### `finbert-tone_label` (CV-AUC 0.554)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 850 | 13.9% | +4.6% | 8.65 |
| neutral | 2537 | 7.8% | -1.4% | 5.75 |
| negative | 39 | 2.6% | -6.7% | 1.90 |

### `label` (CV-AUC 0.552)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2557 | 11.0% | +1.7% | 7.57 |
| negative | 106 | 4.7% | -4.5% | 3.21 |
| neutral | 687 | 4.2% | -5.0% | 3.74 |
| <other> | 63 | 1.6% | -7.7% | 3.72 |

### `FLANG-BERT_Body_label` (CV-AUC 0.552)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1450 | 11.9% | +2.6% | 7.79 |
| neutral | 1938 | 7.4% | -1.8% | 5.55 |
| negative | 38 | 2.6% | -6.6% | 3.59 |

### `Author` (CV-AUC 0.549)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| PR Newswire | 527 | 11.4% | +2.1% | 5.91 |
| Globe Newswire | 1870 | 10.6% | +1.3% | 7.56 |
| ACCESSWIRE | 358 | 7.0% | -2.3% | 6.67 |
| Newsfile Corp | 143 | 5.6% | -3.7% | 6.44 |
| Business Wire | 528 | 4.9% | -4.3% | 4.38 |

### `ModernBERT_Body_label` (CV-AUC 0.531)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2725 | 10.4% | +1.1% | 6.93 |
| neutral | 389 | 5.7% | -3.6% | 4.90 |
| negative | 312 | 3.8% | -5.4% | 4.00 |

### `finbert-tone_Body_label` (CV-AUC 0.528)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1480 | 10.7% | +1.4% | 7.69 |
| neutral | 1883 | 8.3% | -0.9% | 5.64 |
| negative | 63 | 3.2% | -6.1% | 2.29 |

### `FMB_label` (CV-AUC 0.475)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 129 | 12.4% | +3.2% | 9.07 |
| <missing> | 3291 | 9.1% | -0.1% | 6.25 |

### `Exchange` (CV-AUC 0.525)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| NYSE AMERICAN | 192 | 14.1% | +4.8% | 6.48 |
| NYSE American | 149 | 11.4% | +2.2% | 8.32 |
| NASDAQ | 2805 | 9.5% | +0.3% | 6.82 |
| NYSE | 276 | 2.2% | -7.1% | 3.11 |

### `FinancialBERT_label` (CV-AUC 0.480)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 3407 | 9.2% | -0.1% | 6.40 |

### `LouMcD-SDict_label` (CV-AUC 0.516)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 649 | 11.2% | +2.0% | 7.96 |
| neutral | 2548 | 9.0% | -0.2% | 6.12 |
| negative | 229 | 6.1% | -3.1% | 4.61 |

### `FinMarBa_Body_label` (CV-AUC 0.484)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3177 | 9.4% | +0.1% | 6.56 |
| negative | 245 | 7.8% | -1.5% | 4.42 |

### `RoBERTa_Body_label` (CV-AUC 0.492)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 2883 | 9.5% | +0.3% | 6.45 |
| positive | 537 | 7.8% | -1.4% | 6.09 |

### `LouMcD-SDict_Body_label` (CV-AUC 0.506)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2277 | 9.9% | +0.7% | 6.70 |
| neutral | 485 | 9.5% | +0.2% | 5.81 |
| negative | 664 | 6.8% | -2.5% | 5.57 |

### `FinMarBa_label` (CV-AUC 0.505)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3204 | 9.8% | +0.5% | 6.58 |
| negative | 221 | 1.8% | -7.4% | 4.36 |

### `boilerplate_category` (CV-AUC 0.504)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| compliance | 156 | 11.5% | +2.3% | 4.88 |
| lawfirm | 467 | 10.5% | +1.2% | 7.20 |
| <missing> | 2002 | 10.2% | +1.0% | 7.14 |
| conference | 251 | 8.0% | -1.3% | 5.38 |
| earnings | 550 | 4.5% | -4.7% | 4.56 |

### `RoBERTa_label` (CV-AUC 0.500)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 254 | 13.4% | +4.1% | 8.15 |
| neutral | 3161 | 9.0% | -0.3% | 6.21 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: nocoref_recommended, FLANG-BERT_sentiment_score, FLANG-BERT_positive, Float, nocoref_net_score, FinMarBa_indecisive, FLANG-ELECTRA_sentiment_score, DeBERTa_positive, FLANG-ELECTRA_positive, positive, nocoref_positional, FLANG-BERT_neutral, nocoref_top_k, LastDailyClose, Trading-Hero_sentiment_score

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): nocoref_confidence_weighted ~ nocoref_recommended; DeBERTa_sentiment_score ~ DeBERTa_positive; nocoref_neutral_filter ~ nocoref_recommended; sentiment_score ~ positive

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | nocoref_recommended + FinMarBa_indecisive + FLANG-ELECTRA_sentiment_score | 906 | 0.789 | nocoref_recommended=+0.22, FinMarBa_indecisive=-0.57, FLANG-ELECTRA_sentiment_score=+0.61 |
| 2 | nocoref_recommended + FinMarBa_indecisive + FLANG-ELECTRA_positive | 906 | 0.787 | nocoref_recommended=+0.23, FinMarBa_indecisive=-0.57, FLANG-ELECTRA_positive=+0.56 |
| 3 | nocoref_recommended + FLANG-BERT_positive + FinMarBa_indecisive | 906 | 0.772 | nocoref_recommended=+0.27, FLANG-BERT_positive=+0.34, FinMarBa_indecisive=-0.68 |
| 4 | nocoref_recommended + FinMarBa_indecisive + Trading-Hero_sentiment_score | 906 | 0.770 | nocoref_recommended=+0.27, FinMarBa_indecisive=-0.68, Trading-Hero_sentiment_score=+0.33 |
| 5 | nocoref_recommended + FinMarBa_indecisive + DeBERTa_positive | 906 | 0.768 | nocoref_recommended=+0.28, FinMarBa_indecisive=-0.71, DeBERTa_positive=+0.26 |
| 6 | nocoref_recommended + FLANG-BERT_sentiment_score + FinMarBa_indecisive | 906 | 0.768 | nocoref_recommended=+0.27, FLANG-BERT_sentiment_score=+0.38, FinMarBa_indecisive=-0.68 |
| 7 | nocoref_recommended + Float + FLANG-ELECTRA_sentiment_score | 906 | 0.767 | nocoref_recommended=+0.31, Float=-0.60, FLANG-ELECTRA_sentiment_score=+0.83 |
| 8 | nocoref_recommended + Float + FinMarBa_indecisive | 906 | 0.767 | nocoref_recommended=+0.32, Float=-0.60, FinMarBa_indecisive=-0.78 |
| 9 | nocoref_recommended + Float + FLANG-ELECTRA_positive | 906 | 0.765 | nocoref_recommended=+0.32, Float=-0.59, FLANG-ELECTRA_positive=+0.77 |
| 10 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + nocoref_positional | 906 | 0.764 | nocoref_recommended=+0.05, FLANG-ELECTRA_sentiment_score=+0.85, nocoref_positional=+0.32 |
| 11 | nocoref_recommended + FinMarBa_indecisive + FLANG-BERT_neutral | 906 | 0.764 | nocoref_recommended=+0.33, FinMarBa_indecisive=-0.76, FLANG-BERT_neutral=-0.22 |
| 12 | nocoref_recommended + FLANG-BERT_positive + FLANG-ELECTRA_sentiment_score | 906 | 0.764 | nocoref_recommended=+0.29, FLANG-BERT_positive=+0.26, FLANG-ELECTRA_sentiment_score=+0.73 |
| 13 | nocoref_recommended + FinMarBa_indecisive + nocoref_positional | 906 | 0.764 | nocoref_recommended=+0.07, FinMarBa_indecisive=-0.79, nocoref_positional=+0.35 |
| 14 | nocoref_recommended + FLANG-BERT_positive + FLANG-ELECTRA_positive | 906 | 0.763 | nocoref_recommended=+0.30, FLANG-BERT_positive=+0.26, FLANG-ELECTRA_positive=+0.67 |
| 15 | nocoref_recommended + FLANG-ELECTRA_positive + nocoref_positional | 906 | 0.763 | nocoref_recommended=+0.06, FLANG-ELECTRA_positive=+0.79, nocoref_positional=+0.32 |
| 16 | nocoref_recommended + FinMarBa_indecisive | 906 | 0.762 | nocoref_recommended=+0.36, FinMarBa_indecisive=-0.82 |
| 17 | nocoref_recommended + FLANG-BERT_sentiment_score + FLANG-ELECTRA_sentiment_score | 906 | 0.761 | nocoref_recommended=+0.28, FLANG-BERT_sentiment_score=+0.32, FLANG-ELECTRA_sentiment_score=+0.73 |
| 18 | nocoref_recommended + FLANG-BERT_sentiment_score + FLANG-ELECTRA_positive | 906 | 0.760 | nocoref_recommended=+0.28, FLANG-BERT_sentiment_score=+0.33, FLANG-ELECTRA_positive=+0.67 |
| 19 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + Trading-Hero_sentiment_score | 906 | 0.760 | nocoref_recommended=+0.28, FLANG-ELECTRA_sentiment_score=+0.76, Trading-Hero_sentiment_score=+0.22 |
| 20 | nocoref_recommended + FinMarBa_indecisive + positive | 831 | 0.759 | nocoref_recommended=+0.18, FinMarBa_indecisive=-0.69, positive=+0.38 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | nocoref_recommended + FinMarBa_indecisive + FLANG-ELECTRA_sentiment_score | 3 | 0.789 |
| 2 | nocoref_recommended + FinMarBa_indecisive + FLANG-ELECTRA_positive | 3 | 0.787 |
| 3 | nocoref_recommended + FLANG-BERT_positive + FinMarBa_indecisive | 3 | 0.772 |
| 4 | nocoref_recommended + FinMarBa_indecisive + Trading-Hero_sentiment_score | 3 | 0.770 |
| 5 | nocoref_recommended + FinMarBa_indecisive + DeBERTa_positive | 3 | 0.768 |
| 6 | nocoref_recommended + FLANG-BERT_sentiment_score + FinMarBa_indecisive | 3 | 0.768 |
| 7 | nocoref_recommended + Float + FLANG-ELECTRA_sentiment_score | 3 | 0.767 |
| 8 | nocoref_recommended + Float + FinMarBa_indecisive | 3 | 0.767 |
| 9 | nocoref_recommended + Float + FLANG-ELECTRA_positive | 3 | 0.765 |
| 10 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + nocoref_positional | 3 | 0.764 |
| 11 | nocoref_recommended + FinMarBa_indecisive + FLANG-BERT_neutral | 3 | 0.764 |
| 12 | nocoref_recommended + FLANG-BERT_positive + FLANG-ELECTRA_sentiment_score | 3 | 0.764 |
| 13 | nocoref_recommended + FinMarBa_indecisive + nocoref_positional | 3 | 0.764 |
| 14 | nocoref_recommended + FLANG-BERT_positive + FLANG-ELECTRA_positive | 3 | 0.763 |
| 15 | nocoref_recommended + FLANG-ELECTRA_positive + nocoref_positional | 3 | 0.763 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | nocoref_reco | FLANG-BERT_s | FLANG-BERT_p | Float | nocoref_net_ | FinMarBa_ind | FLANG-ELECTR | DeBERTa_posi | FLANG-ELECTR | positive | nocoref_posi | FLANG-BERT_n | nocoref_top_ | LastDailyClo | Trading-Hero |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nocoref_recommen | 1.00 | 0.34 | 0.29 | -0.08 | 0.91 | -0.36 | 0.42 | 0.41 | 0.36 | 0.41 | 0.85 | -0.15 | 0.83 | -0.12 | 0.37 |
| FLANG-BERT_senti | 0.34 | 1.00 | 0.93 | -0.09 | 0.47 | -0.53 | 0.64 | 0.64 | 0.57 | 0.70 | 0.47 | -0.50 | 0.36 | -0.13 | 0.63 |
| FLANG-BERT_posit | 0.29 | 0.93 | 1.00 | -0.08 | 0.44 | -0.47 | 0.65 | 0.61 | 0.62 | 0.69 | 0.45 | -0.70 | 0.35 | -0.12 | 0.60 |
| Float | -0.08 | -0.09 | -0.08 | 1.00 | -0.07 | 0.08 | -0.09 | -0.10 | -0.08 | -0.06 | -0.05 | 0.07 | -0.08 | 0.06 | -0.10 |
| nocoref_net_scor | 0.91 | 0.47 | 0.44 | -0.07 | 1.00 | -0.38 | 0.49 | 0.50 | 0.43 | 0.50 | 0.90 | -0.26 | 0.74 | -0.16 | 0.46 |
| FinMarBa_indecis | -0.36 | -0.53 | -0.47 | 0.08 | -0.38 | 1.00 | -0.45 | -0.54 | -0.42 | -0.54 | -0.38 | 0.28 | -0.30 | 0.18 | -0.52 |
| FLANG-ELECTRA_se | 0.42 | 0.64 | 0.65 | -0.09 | 0.49 | -0.45 | 1.00 | 0.67 | 0.90 | 0.69 | 0.52 | -0.48 | 0.39 | -0.12 | 0.65 |
| DeBERTa_positive | 0.41 | 0.64 | 0.61 | -0.10 | 0.50 | -0.54 | 0.67 | 1.00 | 0.65 | 0.80 | 0.50 | -0.44 | 0.42 | -0.17 | 0.72 |
| FLANG-ELECTRA_po | 0.36 | 0.57 | 0.62 | -0.08 | 0.43 | -0.42 | 0.90 | 0.65 | 1.00 | 0.67 | 0.46 | -0.56 | 0.35 | -0.12 | 0.60 |
| positive | 0.41 | 0.70 | 0.69 | -0.06 | 0.50 | -0.54 | 0.69 | 0.80 | 0.67 | 1.00 | 0.53 | -0.50 | 0.40 | -0.16 | 0.70 |
| nocoref_position | 0.85 | 0.47 | 0.45 | -0.05 | 0.90 | -0.38 | 0.52 | 0.50 | 0.46 | 0.53 | 1.00 | -0.28 | 0.71 | -0.14 | 0.48 |
| FLANG-BERT_neutr | -0.15 | -0.50 | -0.70 | 0.07 | -0.26 | 0.28 | -0.48 | -0.44 | -0.56 | -0.50 | -0.28 | 1.00 | -0.27 | 0.11 | -0.41 |
| nocoref_top_k | 0.83 | 0.36 | 0.35 | -0.08 | 0.74 | -0.30 | 0.39 | 0.42 | 0.35 | 0.40 | 0.71 | -0.27 | 1.00 | -0.17 | 0.36 |
| LastDailyClose | -0.12 | -0.13 | -0.12 | 0.06 | -0.16 | 0.18 | -0.12 | -0.17 | -0.12 | -0.16 | -0.14 | 0.11 | -0.17 | 1.00 | -0.16 |
| Trading-Hero_sen | 0.37 | 0.63 | 0.60 | -0.10 | 0.46 | -0.52 | 0.65 | 0.72 | 0.60 | 0.70 | 0.48 | -0.41 | 0.36 | -0.16 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| nocoref_recommended | 0.594 | 0.616 | 0.625 | 0.670 | 0.699 |
| FLANG-BERT_sentiment_score | 0.633 | 0.641 | 0.648 | 0.661 | 0.676 |
| FLANG-BERT_positive | 0.628 | 0.639 | 0.644 | 0.660 | 0.670 |
| Float | 0.379 | 0.365 | 0.353 | 0.330 | 0.314 |
| nocoref_net_score | 0.621 | 0.635 | 0.653 | 0.657 | 0.658 |
| FinMarBa_indecisive | 0.374 | 0.364 | 0.358 | 0.348 | 0.344 |
| FLANG-ELECTRA_sentiment_score | 0.624 | 0.642 | 0.647 | 0.653 | 0.641 |
| DeBERTa_positive | 0.628 | 0.637 | 0.640 | 0.650 | 0.644 |
| FLANG-ELECTRA_positive | 0.617 | 0.640 | 0.646 | 0.655 | 0.639 |
| positive | 0.637 | 0.651 | 0.647 | 0.654 | 0.649 |
| nocoref_positional | 0.619 | 0.632 | 0.643 | 0.640 | 0.639 |
| FLANG-BERT_neutral | 0.414 | 0.398 | 0.393 | 0.371 | 0.366 |
| nocoref_top_k | 0.608 | 0.629 | 0.636 | 0.633 | 0.640 |
| LastDailyClose | 0.376 | 0.384 | 0.382 | 0.356 | 0.339 |
| Trading-Hero_sentiment_score | 0.622 | 0.635 | 0.633 | 0.646 | 0.643 |
| _n big movers_ | 1269 | 866 | 648 | 417 | 247 |

## Conclusion

- **Best numeric single:** `nocoref_recommended` (AUC 0.677, higher->bigger).
- **Best categorical:** `FineClass` (CV-AUC 0.628).
- **Best combo:** `nocoref_recommended + FinMarBa_indecisive + FLANG-ELECTRA_sentiment_score` (CV-AUC 0.789; lift over best single: +0.112).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

