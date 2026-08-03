# fromPR_High(%) all-column predictor analysis — 04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv

- Rows: 3533 total -> dropped 99 null-target, 2 outliers (> 500%), 6 duplicate PRs -> **3426 kept**
- fromPR_High(%) on kept rows: median=6.40  mean=16.97  max=478.18
- Big-mover threshold: **fromPR_High(%) >= 15** -> **866** big movers (25.3%)
- Candidates: 115 numeric (auto-detected), 29 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DeBERTa_Body_elapsed_ms, DeBERTa_elapsed_ms, FLANG-BERT_Body_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_Body_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_Body_elapsed_ms, FinMarBa_elapsed_ms, FinancialBERT_Body_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, LouMcD-SDict_Body_elapsed_ms, LouMcD-SDict_elapsed_ms, ModernBERT_Body_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_Body_elapsed_ms, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_Body_elapsed_ms, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_Body_elapsed_ms, distilroberta_elapsed_ms, finbert-tone_Body_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | positive | 3350 | 0.651 | higher->bigger | 0.151 | +0.291 | +0.277 |
| 2 | sentiment_score | 3426 | 0.644 | higher->bigger | 0.144 | +0.278 | +0.257 |
| 3 | FLANG-ELECTRA_sentiment_score | 3426 | 0.642 | higher->bigger | 0.142 | +0.266 | +0.252 |
| 4 | FLANG-BERT_sentiment_score | 3426 | 0.641 | higher->bigger | 0.141 | +0.282 | +0.273 |
| 5 | FLANG-ELECTRA_positive | 3426 | 0.640 | higher->bigger | 0.140 | +0.257 | +0.260 |
| 6 | FLANG-BERT_positive | 3426 | 0.639 | higher->bigger | 0.139 | +0.270 | +0.274 |
| 7 | DeBERTa_sentiment_score | 3426 | 0.638 | higher->bigger | 0.138 | +0.290 | +0.253 |
| 8 | FMB_indecisive | 135 | 0.362 | lower->bigger | 0.138 | -0.270 | -0.269 |
| 9 | Trading-Hero_positive | 3426 | 0.637 | higher->bigger | 0.137 | +0.265 | +0.259 |
| 10 | DeBERTa_positive | 3426 | 0.637 | higher->bigger | 0.137 | +0.287 | +0.264 |
| 11 | FinMarBa_indecisive | 3426 | 0.364 | lower->bigger | 0.136 | -0.251 | -0.245 |
| 12 | nocoref_net_score | 3117 | 0.635 | higher->bigger | 0.135 | +0.273 | +0.273 |
| 13 | Trading-Hero_sentiment_score | 3426 | 0.635 | higher->bigger | 0.135 | +0.267 | +0.252 |
| 14 | Float | 3426 | 0.365 | lower->bigger | 0.135 | -0.236 | -0.200 |
| 15 | nocoref_confidence_weighted | 3117 | 0.632 | higher->bigger | 0.132 | +0.269 | +0.266 |
| 16 | nocoref_positional | 3117 | 0.632 | higher->bigger | 0.132 | +0.262 | +0.253 |
| 17 | nocoref_top_k | 3117 | 0.629 | higher->bigger | 0.129 | +0.234 | +0.238 |
| 18 | Trading-Hero_neutral | 3426 | 0.372 | lower->bigger | 0.128 | -0.244 | -0.239 |
| 19 | neutral | 3426 | 0.372 | lower->bigger | 0.128 | -0.232 | -0.220 |
| 20 | nocoref_neutral_filter | 3117 | 0.628 | higher->bigger | 0.128 | +0.259 | +0.257 |
| 21 | FLANG-ELECTRA_neutral | 3426 | 0.373 | lower->bigger | 0.127 | -0.229 | -0.235 |
| 22 | DeBERTa_neutral | 3426 | 0.373 | lower->bigger | 0.127 | -0.263 | -0.244 |
| 23 | distilroberta_sentiment_score | 3426 | 0.624 | higher->bigger | 0.124 | +0.258 | +0.195 |
| 24 | Trading-Hero_Body_sentiment_score | 3426 | 0.623 | higher->bigger | 0.123 | +0.215 | +0.181 |
| 25 | distilroberta_positive | 3426 | 0.620 | higher->bigger | 0.120 | +0.249 | +0.196 |
| 26 | negative | 3426 | 0.380 | lower->bigger | 0.120 | -0.245 | -0.115 |
| 27 | Trading-Hero_Body_positive | 3426 | 0.619 | higher->bigger | 0.119 | +0.197 | +0.161 |
| 28 | FinancialBERT_Body_sentiment_score | 3426 | 0.618 | higher->bigger | 0.118 | +0.229 | +0.232 |
| 29 | FinancialBERT_Body_positive | 3426 | 0.618 | higher->bigger | 0.118 | +0.225 | +0.229 |
| 30 | nocoref_recommended | 906 | 0.616 | higher->bigger | 0.116 | +0.198 | +0.199 |
| 31 | LastDailyClose | 3424 | 0.384 | lower->bigger | 0.116 | -0.249 | -0.121 |
| 32 | Trading-Hero_negative | 3426 | 0.386 | lower->bigger | 0.114 | -0.226 | -0.062 |
| 33 | distilroberta_Body_sentiment_score | 3426 | 0.612 | higher->bigger | 0.112 | +0.220 | +0.210 |
| 34 | FinancialBERT_Body_neutral | 3426 | 0.388 | lower->bigger | 0.112 | -0.213 | -0.216 |
| 35 | FLANG-ELECTRA_Body_positive | 3426 | 0.611 | higher->bigger | 0.111 | +0.218 | +0.204 |
| 36 | distilroberta_neutral | 3426 | 0.389 | lower->bigger | 0.111 | -0.226 | -0.189 |
| 37 | distilroberta_Body_positive | 3426 | 0.611 | higher->bigger | 0.111 | +0.211 | +0.204 |
| 38 | FMB_positive | 135 | 0.610 | higher->bigger | 0.110 | +0.228 | +0.210 |
| 39 | FinMarBa_positive | 3426 | 0.608 | higher->bigger | 0.108 | +0.222 | +0.217 |
| 40 | ModernBERT_positive | 3426 | 0.608 | higher->bigger | 0.108 | +0.228 | +0.231 |
| 41 | FinMarBa_Body_indecisive | 3426 | 0.394 | lower->bigger | 0.106 | -0.235 | -0.228 |
| 42 | ModernBERT_sentiment_score | 3426 | 0.606 | higher->bigger | 0.106 | +0.222 | +0.218 |
| 43 | ModernBERT_neutral | 3426 | 0.397 | lower->bigger | 0.103 | -0.220 | -0.222 |
| 44 | MktCap | 875 | 0.397 | lower->bigger | 0.103 | -0.203 | -0.082 |
| 45 | FLANG-BERT_neutral | 3426 | 0.398 | lower->bigger | 0.102 | -0.176 | -0.153 |
| 46 | Trading-Hero_Body_neutral | 3426 | 0.399 | lower->bigger | 0.101 | -0.159 | -0.131 |
| 47 | FLANG-ELECTRA_Body_sentiment_score | 3426 | 0.599 | higher->bigger | 0.099 | +0.199 | +0.210 |
| 48 | FMB_sentiment_score | 135 | 0.599 | higher->bigger | 0.099 | +0.211 | +0.190 |
| 49 | FinMarBa_sentiment_score | 3426 | 0.598 | higher->bigger | 0.098 | +0.208 | +0.202 |
| 50 | finbert-tone_sentiment_score | 3426 | 0.597 | higher->bigger | 0.097 | +0.195 | +0.165 |
| 51 | finbert-tone_positive | 3426 | 0.596 | higher->bigger | 0.096 | +0.189 | +0.157 |
| 52 | distilroberta_Body_neutral | 3426 | 0.405 | lower->bigger | 0.095 | -0.174 | -0.174 |
| 53 | FLANG-BERT_Body_sentiment_score | 3426 | 0.595 | higher->bigger | 0.095 | +0.181 | +0.163 |
| 54 | FLANG-BERT_negative | 3426 | 0.407 | lower->bigger | 0.093 | -0.191 | -0.174 |
| 55 | FLANG-BERT_Body_positive | 3426 | 0.590 | higher->bigger | 0.090 | +0.170 | +0.154 |
| 56 | DeBERTa_Body_positive | 3426 | 0.590 | higher->bigger | 0.090 | +0.187 | +0.185 |
| 57 | FinancialBERT_Body_negative | 3426 | 0.411 | lower->bigger | 0.089 | -0.212 | -0.074 |
| 58 | DeBERTa_Body_sentiment_score | 3426 | 0.589 | higher->bigger | 0.089 | +0.186 | +0.194 |
| 59 | FLANG-ELECTRA_Body_neutral | 3426 | 0.412 | lower->bigger | 0.088 | -0.181 | -0.154 |
| 60 | FMB_negative | 135 | 0.414 | lower->bigger | 0.086 | -0.183 | -0.159 |
| 61 | finbert-tone_neutral | 3426 | 0.414 | lower->bigger | 0.086 | -0.165 | -0.139 |
| 62 | finbert-tone_Body_sentiment_score | 3426 | 0.583 | higher->bigger | 0.083 | +0.174 | +0.136 |
| 63 | FinMarBa_negative | 3426 | 0.418 | lower->bigger | 0.082 | -0.185 | -0.178 |
| 64 | ModernBERT_Body_positive | 3426 | 0.582 | higher->bigger | 0.082 | +0.175 | +0.179 |
| 65 | RoBERTa_positive | 3426 | 0.581 | higher->bigger | 0.081 | +0.169 | +0.129 |
| 66 | FLANG-BERT_Body_neutral | 3426 | 0.419 | lower->bigger | 0.081 | -0.152 | -0.137 |
| 67 | FLANG-ELECTRA_negative | 3426 | 0.581 | higher->bigger | 0.081 | +0.139 | -0.061 |
| 68 | ModernBERT_Body_sentiment_score | 3426 | 0.579 | higher->bigger | 0.079 | +0.170 | +0.172 |
| 69 | RoBERTa_sentiment_score | 3426 | 0.579 | higher->bigger | 0.079 | +0.167 | +0.139 |
| 70 | finbert-tone_Body_positive | 3426 | 0.576 | higher->bigger | 0.076 | +0.156 | +0.122 |
| 71 | distilroberta_negative | 3426 | 0.574 | higher->bigger | 0.074 | +0.168 | -0.026 |
| 72 | RoBERTa_neutral | 3426 | 0.428 | lower->bigger | 0.072 | -0.143 | -0.106 |
| 73 | FLANG-BERT_Body_negative | 3426 | 0.428 | lower->bigger | 0.072 | -0.117 | -0.075 |
| 74 | ModernBERT_Body_neutral | 3426 | 0.435 | lower->bigger | 0.065 | -0.128 | -0.126 |
| 75 | finbert-tone_Body_neutral | 3426 | 0.437 | lower->bigger | 0.063 | -0.130 | -0.099 |
| 76 | FinMarBa_Body_positive | 3426 | 0.563 | higher->bigger | 0.063 | +0.162 | +0.165 |
| 77 | ModernBERT_Body_negative | 3426 | 0.438 | lower->bigger | 0.062 | -0.140 | -0.140 |
| 78 | DeBERTa_Body_neutral | 3426 | 0.441 | lower->bigger | 0.059 | -0.110 | -0.117 |
| 79 | DeBERTa_Body_negative | 3426 | 0.446 | lower->bigger | 0.054 | -0.116 | -0.134 |
| 80 | FinMarBa_Body_sentiment_score | 3426 | 0.551 | higher->bigger | 0.051 | +0.140 | +0.143 |
| 81 | Trading-Hero_Body_negative | 3426 | 0.449 | lower->bigger | 0.051 | -0.091 | -0.133 |
| 82 | FLANG-ELECTRA_Body_negative | 3426 | 0.547 | higher->bigger | 0.047 | +0.095 | -0.121 |
| 83 | tradeSizeBaseline | 1322 | 0.454 | lower->bigger | 0.046 | -0.056 | -0.025 |
| 84 | LouMcD-SDict_Body_strong_modal | 3426 | 0.455 | lower->bigger | 0.045 | -0.106 | -0.125 |
| 85 | LouMcD-SDict_Body_weak_modal | 3426 | 0.464 | lower->bigger | 0.036 | -0.083 | -0.081 |
| 86 | RoBERTa_negative | 3426 | 0.466 | lower->bigger | 0.034 | -0.082 | -0.088 |
| 87 | LouMcD-SDict_sentiment_score | 3426 | 0.533 | higher->bigger | 0.033 | +0.103 | +0.099 |
| 88 | FinMarBa_Body_negative | 3426 | 0.467 | lower->bigger | 0.033 | -0.106 | -0.108 |
| 89 | RoBERTa_Body_positive | 3426 | 0.531 | higher->bigger | 0.031 | +0.071 | +0.050 |
| 90 | RoBERTa_Body_sentiment_score | 3426 | 0.530 | higher->bigger | 0.030 | +0.070 | +0.055 |
| 91 | LouMcD-SDict_Body_sentiment_score | 3426 | 0.530 | higher->bigger | 0.030 | +0.067 | +0.080 |
| 92 | ModernBERT_negative | 3426 | 0.471 | lower->bigger | 0.029 | -0.071 | -0.056 |
| 93 | RoBERTa_Body_neutral | 3426 | 0.471 | lower->bigger | 0.029 | -0.066 | -0.042 |
| 94 | finbert-tone_Body_negative | 3426 | 0.472 | lower->bigger | 0.028 | -0.094 | -0.088 |
| 95 | LouMcD-SDict_Body_positive | 3426 | 0.528 | higher->bigger | 0.028 | +0.071 | +0.058 |
| 96 | distilroberta_Body_negative | 3426 | 0.524 | higher->bigger | 0.024 | +0.037 | -0.097 |
| 97 | LouMcD-SDict_positive | 3426 | 0.522 | higher->bigger | 0.022 | +0.078 | +0.081 |
| 98 | finbert-tone_negative | 3426 | 0.479 | lower->bigger | 0.021 | -0.056 | -0.063 |
| 99 | LouMcD-SDict_Body_negative | 3426 | 0.482 | lower->bigger | 0.018 | -0.034 | -0.071 |
| 100 | itiBaseline | 1322 | 0.483 | lower->bigger | 0.017 | -0.059 | -0.053 |
| 101 | LouMcD-SDict_negative | 3426 | 0.484 | lower->bigger | 0.016 | -0.067 | -0.070 |
| 102 | LouMcD-SDict_Body_uncertainty | 3426 | 0.515 | higher->bigger | 0.015 | +0.013 | +0.018 |
| 103 | RoBERTa_Body_negative | 3426 | 0.514 | higher->bigger | 0.014 | +0.002 | -0.046 |
| 104 | FinancialBERT_negative | 3426 | 0.489 | lower->bigger | 0.011 | -0.061 | -0.013 |
| 105 | LouMcD-SDict_weak_modal | 3426 | 0.489 | lower->bigger | 0.011 | -0.064 | -0.062 |
| 106 | FinancialBERT_neutral | 3426 | 0.511 | higher->bigger | 0.011 | +0.032 | -0.018 |
| 107 | FinancialBERT_sentiment_score | 3426 | 0.509 | higher->bigger | 0.009 | +0.015 | +0.027 |
| 108 | LouMcD-SDict_Body_constraining | 3426 | 0.505 | higher->bigger | 0.005 | +0.036 | +0.028 |
| 109 | LouMcD-SDict_litigious | 3426 | 0.504 | higher->bigger | 0.004 | -0.001 | -0.003 |
| 110 | LouMcD-SDict_constraining | 3426 | 0.503 | higher->bigger | 0.003 | +0.026 | +0.026 |
| 111 | FinancialBERT_positive | 3426 | 0.502 | higher->bigger | 0.002 | -0.001 | +0.024 |
| 112 | LouMcD-SDict_uncertainty | 3426 | 0.502 | higher->bigger | 0.002 | -0.018 | -0.016 |
| 113 | LouMcD-SDict_Body_litigious | 3426 | 0.499 | lower->bigger | 0.001 | -0.012 | -0.026 |
| 114 | DeBERTa_negative | 3426 | 0.501 | higher->bigger | 0.001 | +0.013 | -0.055 |
| 115 | LouMcD-SDict_strong_modal | 3426 | 0.500 | lower->bigger | 0.000 | +0.008 | +0.010 |

## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)

| rank | column | n | CV-AUC | |AUC-0.5| |
|---|---|---|---|---|
| 1 | FineClass | 3426 | 0.619 | 0.119 |
| 2 | BroadClass | 3426 | 0.613 | 0.113 |
| 3 | FLANG-ELECTRA_label | 3426 | 0.605 | 0.105 |
| 4 | Trading-Hero_label | 3426 | 0.602 | 0.102 |
| 5 | DeBERTa_label | 3426 | 0.590 | 0.090 |
| 6 | FinancialBERT_Body_label | 3426 | 0.589 | 0.089 |
| 7 | distilroberta_Body_label | 3426 | 0.585 | 0.085 |
| 8 | Trading-Hero_Body_label | 3426 | 0.582 | 0.082 |
| 9 | FLANG-BERT_label | 3426 | 0.577 | 0.077 |
| 10 | FLANG-ELECTRA_Body_label | 3426 | 0.576 | 0.076 |
| 11 | ModernBERT_label | 3426 | 0.573 | 0.073 |
| 12 | distilroberta_label | 3426 | 0.566 | 0.066 |
| 13 | label | 3426 | 0.565 | 0.065 |
| 14 | DeBERTa_Body_label | 3426 | 0.565 | 0.065 |
| 15 | Author | 3426 | 0.557 | 0.057 |
| 16 | FLANG-BERT_Body_label | 3426 | 0.553 | 0.053 |
| 17 | finbert-tone_label | 3426 | 0.548 | 0.048 |
| 18 | finbert-tone_Body_label | 3426 | 0.544 | 0.044 |
| 19 | ModernBERT_Body_label | 3426 | 0.533 | 0.033 |
| 20 | boilerplate_category | 3426 | 0.521 | 0.021 |
| 21 | LouMcD-SDict_Body_label | 3426 | 0.521 | 0.021 |
| 22 | Exchange | 3426 | 0.519 | 0.019 |
| 23 | LouMcD-SDict_label | 3426 | 0.517 | 0.017 |
| 24 | FinancialBERT_label | 3426 | 0.490 | 0.010 |
| 25 | FMB_label | 3426 | 0.491 | 0.009 |
| 26 | FinMarBa_label | 3426 | 0.507 | 0.007 |
| 27 | RoBERTa_label | 3426 | 0.496 | 0.004 |
| 28 | FinMarBa_Body_label | 3426 | 0.498 | 0.002 |
| 29 | RoBERTa_Body_label | 3426 | 0.498 | 0.002 |

## Categorical breakdowns — per level (min 20 rows; baseline big-mover rate 25.3%)

### `FineClass` (CV-AUC 0.619)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 291 | 41.6% | +16.3% | 11.28 |
| Partnership | 256 | 37.9% | +12.6% | 9.66 |
| Clinical_Data | 127 | 37.8% | +12.5% | 7.96 |
| Contract_Award | 180 | 34.4% | +9.2% | 8.45 |
| Patent_IP | 73 | 27.4% | +2.1% | 7.63 |
| Offering_Financing | 217 | 27.2% | +1.9% | 7.14 |
| Business_Update | 103 | 27.2% | +1.9% | 7.67 |
| Product_Launch | 288 | 27.1% | +1.8% | 7.10 |
| Clinical_Update | 151 | 26.5% | +1.2% | 8.59 |
| Crypto_Treasury | 68 | 25.0% | -0.3% | 8.14 |
| FDA_Designation | 283 | 23.0% | -2.3% | 5.45 |
| Other | 347 | 20.2% | -5.1% | 6.12 |
| Earnings_Results | 487 | 18.7% | -6.6% | 4.32 |
| <other> | 266 | 16.2% | -9.1% | 4.06 |
| Management_Change | 166 | 9.6% | -15.6% | 4.50 |
| Conference_Presentation | 123 | 8.9% | -16.3% | 4.32 |

### `BroadClass` (CV-AUC 0.613)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 291 | 41.6% | +16.3% | 11.28 |
| Commercial | 724 | 32.7% | +7.5% | 8.20 |
| Clinical | 278 | 31.7% | +6.4% | 8.46 |
| IP | 73 | 27.4% | +2.1% | 7.63 |
| Financing | 240 | 26.7% | +1.4% | 7.21 |
| Crypto | 68 | 25.0% | -0.3% | 8.14 |
| FDA | 307 | 23.1% | -2.2% | 5.71 |
| Other | 347 | 20.2% | -5.1% | 6.12 |
| Earnings | 514 | 18.3% | -7.0% | 4.21 |
| Compliance | 140 | 17.1% | -8.1% | 4.06 |
| Corporate | 392 | 14.0% | -11.2% | 4.95 |
| Legal | 52 | 9.6% | -15.7% | 2.95 |

### `FLANG-ELECTRA_label` (CV-AUC 0.605)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1376 | 36.3% | +11.0% | 9.44 |
| neutral | 1930 | 18.1% | -7.2% | 5.01 |
| negative | 120 | 15.0% | -10.3% | 4.00 |

### `Trading-Hero_label` (CV-AUC 0.602)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1474 | 35.0% | +9.7% | 9.18 |
| neutral | 1829 | 18.1% | -7.2% | 4.94 |
| negative | 123 | 15.4% | -9.8% | 4.43 |

### `DeBERTa_label` (CV-AUC 0.590)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1449 | 33.7% | +8.5% | 8.99 |
| neutral | 1875 | 19.3% | -6.0% | 5.02 |
| negative | 102 | 14.7% | -10.6% | 4.39 |

### `FinancialBERT_Body_label` (CV-AUC 0.589)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2216 | 30.9% | +5.6% | 7.87 |
| neutral | 1175 | 15.4% | -9.9% | 4.49 |
| negative | 35 | 2.9% | -22.4% | 2.33 |

### `distilroberta_Body_label` (CV-AUC 0.585)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2113 | 31.0% | +5.7% | 7.92 |
| neutral | 1214 | 16.7% | -8.6% | 4.90 |
| negative | 99 | 9.1% | -16.2% | 2.50 |

### `Trading-Hero_Body_label` (CV-AUC 0.582)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1056 | 35.3% | +10.0% | 9.05 |
| neutral | 2283 | 21.4% | -3.9% | 5.74 |
| negative | 87 | 5.7% | -19.5% | 3.00 |

### `FLANG-BERT_label` (CV-AUC 0.577)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1806 | 32.3% | +7.1% | 8.33 |
| neutral | 1098 | 17.9% | -7.4% | 5.22 |
| negative | 522 | 16.5% | -8.8% | 4.33 |

### `FLANG-ELECTRA_Body_label` (CV-AUC 0.576)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2275 | 30.0% | +4.7% | 7.69 |
| neutral | 960 | 17.0% | -8.3% | 4.84 |
| negative | 191 | 11.0% | -14.3% | 3.18 |

### `ModernBERT_label` (CV-AUC 0.573)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1385 | 32.7% | +7.4% | 8.60 |
| neutral | 1966 | 20.5% | -4.7% | 5.28 |
| negative | 75 | 12.0% | -13.3% | 4.43 |

### `distilroberta_label` (CV-AUC 0.566)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 849 | 37.1% | +11.8% | 9.74 |
| neutral | 2547 | 21.4% | -3.8% | 5.55 |
| negative | 30 | 16.7% | -8.6% | 4.55 |

### `label` (CV-AUC 0.565)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2557 | 29.3% | +4.0% | 7.57 |
| <other> | 63 | 15.9% | -9.4% | 3.72 |
| neutral | 687 | 13.2% | -12.0% | 3.74 |
| negative | 106 | 12.3% | -13.0% | 3.21 |

### `DeBERTa_Body_label` (CV-AUC 0.565)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2175 | 29.4% | +4.1% | 7.49 |
| neutral | 994 | 19.7% | -5.6% | 5.34 |
| negative | 257 | 11.7% | -13.6% | 3.33 |

### `Author` (CV-AUC 0.557)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| Globe Newswire | 1870 | 28.9% | +3.7% | 7.56 |
| PR Newswire | 527 | 25.4% | +0.1% | 5.91 |
| ACCESSWIRE | 358 | 22.6% | -2.7% | 6.67 |
| Newsfile Corp | 143 | 18.2% | -7.1% | 6.44 |
| Business Wire | 528 | 15.9% | -9.4% | 4.38 |

### `FLANG-BERT_Body_label` (CV-AUC 0.553)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1450 | 30.8% | +5.5% | 7.79 |
| neutral | 1938 | 21.5% | -3.8% | 5.55 |
| negative | 38 | 10.5% | -14.8% | 3.59 |

### `finbert-tone_label` (CV-AUC 0.548)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 850 | 33.8% | +8.5% | 8.65 |
| neutral | 2537 | 22.8% | -2.5% | 5.75 |
| negative | 39 | 2.6% | -22.7% | 1.90 |

### `finbert-tone_Body_label` (CV-AUC 0.544)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1480 | 29.5% | +4.2% | 7.69 |
| neutral | 1883 | 22.6% | -2.7% | 5.64 |
| negative | 63 | 7.9% | -17.3% | 2.29 |

### `ModernBERT_Body_label` (CV-AUC 0.533)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2725 | 27.5% | +2.2% | 6.93 |
| neutral | 389 | 17.0% | -8.3% | 4.90 |
| negative | 312 | 16.0% | -9.3% | 4.00 |

### `boilerplate_category` (CV-AUC 0.521)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| lawfirm | 467 | 27.8% | +2.6% | 7.20 |
| <missing> | 2002 | 27.1% | +1.8% | 7.14 |
| compliance | 156 | 21.2% | -4.1% | 4.88 |
| conference | 251 | 20.7% | -4.6% | 5.38 |
| earnings | 550 | 19.8% | -5.5% | 4.56 |

### `LouMcD-SDict_Body_label` (CV-AUC 0.521)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2277 | 26.8% | +1.5% | 6.70 |
| neutral | 485 | 24.9% | -0.3% | 5.81 |
| negative | 664 | 20.3% | -4.9% | 5.57 |

### `Exchange` (CV-AUC 0.519)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| NYSE AMERICAN | 192 | 29.7% | +4.4% | 6.48 |
| NYSE American | 149 | 27.5% | +2.2% | 8.32 |
| NASDAQ | 2805 | 26.4% | +1.1% | 6.82 |
| NYSE | 276 | 9.4% | -15.9% | 3.11 |

### `LouMcD-SDict_label` (CV-AUC 0.517)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 649 | 30.4% | +5.1% | 7.96 |
| neutral | 2548 | 24.6% | -0.7% | 6.12 |
| negative | 229 | 18.8% | -6.5% | 4.61 |

### `FinancialBERT_label` (CV-AUC 0.490)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 3407 | 25.2% | -0.1% | 6.40 |

### `FMB_label` (CV-AUC 0.491)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 129 | 30.2% | +5.0% | 9.07 |
| <missing> | 3291 | 25.1% | -0.2% | 6.25 |

### `FinMarBa_label` (CV-AUC 0.507)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3204 | 26.1% | +0.8% | 6.58 |
| negative | 221 | 13.1% | -12.2% | 4.36 |

### `RoBERTa_label` (CV-AUC 0.496)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 254 | 32.3% | +7.0% | 8.15 |
| neutral | 3161 | 24.7% | -0.5% | 6.21 |

### `FinMarBa_Body_label` (CV-AUC 0.498)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3177 | 25.7% | +0.5% | 6.56 |
| negative | 245 | 19.6% | -5.7% | 4.42 |

### `RoBERTa_Body_label` (CV-AUC 0.498)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 2883 | 25.6% | +0.3% | 6.45 |
| positive | 537 | 23.6% | -1.6% | 6.09 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: positive, FLANG-ELECTRA_sentiment_score, FLANG-BERT_sentiment_score, FLANG-ELECTRA_positive, FLANG-BERT_positive, DeBERTa_sentiment_score, FMB_indecisive, Trading-Hero_positive, nocoref_net_score, Float, nocoref_positional, nocoref_top_k, neutral, nocoref_neutral_filter, DeBERTa_neutral

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): sentiment_score ~ positive; DeBERTa_positive ~ DeBERTa_sentiment_score; FinMarBa_indecisive ~ FMB_indecisive; Trading-Hero_sentiment_score ~ Trading-Hero_positive; nocoref_confidence_weighted ~ nocoref_net_score; Trading-Hero_neutral ~ Trading-Hero_positive; FLANG-ELECTRA_neutral ~ FLANG-ELECTRA_positive

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | positive + Float + nocoref_top_k | 3042 | 0.701 | positive=+0.38, Float=-0.45, nocoref_top_k=+0.32 |
| 2 | positive + nocoref_net_score + Float | 3042 | 0.701 | positive=+0.35, nocoref_net_score=+0.30, Float=-0.46 |
| 3 | positive + Float + nocoref_positional | 3042 | 0.697 | positive=+0.35, Float=-0.47, nocoref_positional=+0.27 |
| 4 | FLANG-ELECTRA_positive + Float + nocoref_top_k | 3117 | 0.697 | FLANG-ELECTRA_positive=+0.35, Float=-0.43, nocoref_top_k=+0.34 |
| 5 | positive + Float + nocoref_neutral_filter | 3042 | 0.697 | positive=+0.38, Float=-0.46, nocoref_neutral_filter=+0.26 |
| 6 | FLANG-ELECTRA_sentiment_score + Float + nocoref_top_k | 3117 | 0.696 | FLANG-ELECTRA_sentiment_score=+0.36, Float=-0.43, nocoref_top_k=+0.34 |
| 7 | FLANG-BERT_positive + Float + nocoref_top_k | 3117 | 0.695 | FLANG-BERT_positive=+0.36, Float=-0.42, nocoref_top_k=+0.34 |
| 8 | FLANG-ELECTRA_positive + nocoref_net_score + Float | 3117 | 0.695 | FLANG-ELECTRA_positive=+0.32, nocoref_net_score=+0.33, Float=-0.43 |
| 9 | FLANG-BERT_positive + nocoref_net_score + Float | 3117 | 0.694 | FLANG-BERT_positive=+0.33, nocoref_net_score=+0.33, Float=-0.43 |
| 10 | nocoref_net_score + Float + neutral | 3117 | 0.694 | nocoref_net_score=+0.35, Float=-0.45, neutral=-0.29 |
| 11 | Trading-Hero_positive + Float + nocoref_top_k | 3117 | 0.694 | Trading-Hero_positive=+0.33, Float=-0.43, nocoref_top_k=+0.35 |
| 12 | Trading-Hero_positive + nocoref_net_score + Float | 3117 | 0.694 | Trading-Hero_positive=+0.30, nocoref_net_score=+0.34, Float=-0.44 |
| 13 | FLANG-BERT_sentiment_score + Float + nocoref_top_k | 3117 | 0.694 | FLANG-BERT_sentiment_score=+0.36, Float=-0.43, nocoref_top_k=+0.34 |
| 14 | FLANG-ELECTRA_sentiment_score + nocoref_net_score + Float | 3117 | 0.694 | FLANG-ELECTRA_sentiment_score=+0.33, nocoref_net_score=+0.33, Float=-0.43 |
| 15 | Float + nocoref_top_k + neutral | 3117 | 0.693 | Float=-0.45, nocoref_top_k=+0.37, neutral=-0.32 |
| 16 | FLANG-BERT_sentiment_score + nocoref_net_score + Float | 3117 | 0.693 | FLANG-BERT_sentiment_score=+0.33, nocoref_net_score=+0.33, Float=-0.44 |
| 17 | FLANG-ELECTRA_positive + Float + nocoref_positional | 3117 | 0.693 | FLANG-ELECTRA_positive=+0.32, Float=-0.44, nocoref_positional=+0.31 |
| 18 | FLANG-ELECTRA_positive + Float + nocoref_neutral_filter | 3117 | 0.692 | FLANG-ELECTRA_positive=+0.34, Float=-0.43, nocoref_neutral_filter=+0.30 |
| 19 | FLANG-BERT_positive + Float + nocoref_positional | 3117 | 0.692 | FLANG-BERT_positive=+0.33, Float=-0.44, nocoref_positional=+0.31 |
| 20 | DeBERTa_sentiment_score + Float + nocoref_top_k | 3117 | 0.692 | DeBERTa_sentiment_score=+0.32, Float=-0.43, nocoref_top_k=+0.35 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | positive + Float + nocoref_top_k | 3 | 0.701 |
| 2 | positive + nocoref_net_score + Float | 3 | 0.701 |
| 3 | positive + Float + nocoref_positional | 3 | 0.697 |
| 4 | FLANG-ELECTRA_positive + Float + nocoref_top_k | 3 | 0.697 |
| 5 | positive + Float + nocoref_neutral_filter | 3 | 0.697 |
| 6 | FLANG-ELECTRA_sentiment_score + Float + nocoref_top_k | 3 | 0.696 |
| 7 | FLANG-BERT_positive + Float + nocoref_top_k | 3 | 0.695 |
| 8 | FLANG-ELECTRA_positive + nocoref_net_score + Float | 3 | 0.695 |
| 9 | FLANG-BERT_positive + nocoref_net_score + Float | 3 | 0.694 |
| 10 | nocoref_net_score + Float + neutral | 3 | 0.694 |
| 11 | Trading-Hero_positive + Float + nocoref_top_k | 3 | 0.694 |
| 12 | Trading-Hero_positive + nocoref_net_score + Float | 3 | 0.694 |
| 13 | FLANG-BERT_sentiment_score + Float + nocoref_top_k | 3 | 0.694 |
| 14 | FLANG-ELECTRA_sentiment_score + nocoref_net_score + Float | 3 | 0.694 |
| 15 | Float + nocoref_top_k + neutral | 3 | 0.693 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | positive | FLANG-ELECTR | FLANG-BERT_s | FLANG-ELECTR | FLANG-BERT_p | DeBERTa_sent | FMB_indecisi | Trading-Hero | nocoref_net_ | Float | nocoref_posi | nocoref_top_ | neutral | nocoref_neut | DeBERTa_neut |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| positive | 1.00 | 0.69 | 0.70 | 0.67 | 0.69 | 0.79 | -0.50 | 0.70 | 0.50 | -0.06 | 0.53 | 0.40 | -0.93 | 0.46 | -0.75 |
| FLANG-ELECTRA_se | 0.69 | 1.00 | 0.64 | 0.90 | 0.65 | 0.69 | -0.39 | 0.65 | 0.49 | -0.09 | 0.52 | 0.39 | -0.58 | 0.47 | -0.59 |
| FLANG-BERT_senti | 0.70 | 0.64 | 1.00 | 0.57 | 0.93 | 0.66 | -0.49 | 0.60 | 0.47 | -0.09 | 0.47 | 0.36 | -0.56 | 0.45 | -0.55 |
| FLANG-ELECTRA_po | 0.67 | 0.90 | 0.57 | 1.00 | 0.62 | 0.63 | -0.37 | 0.63 | 0.43 | -0.08 | 0.46 | 0.35 | -0.62 | 0.41 | -0.64 |
| FLANG-BERT_posit | 0.69 | 0.65 | 0.93 | 0.62 | 1.00 | 0.62 | -0.45 | 0.60 | 0.44 | -0.08 | 0.45 | 0.35 | -0.59 | 0.42 | -0.53 |
| DeBERTa_sentimen | 0.79 | 0.69 | 0.66 | 0.63 | 0.62 | 1.00 | -0.37 | 0.70 | 0.52 | -0.10 | 0.53 | 0.42 | -0.63 | 0.49 | -0.87 |
| FMB_indecisive | -0.50 | -0.39 | -0.49 | -0.37 | -0.45 | -0.37 | 1.00 | -0.44 | — | -0.03 | — | — | 0.42 | — | 0.34 |
| Trading-Hero_pos | 0.70 | 0.65 | 0.60 | 0.63 | 0.60 | 0.70 | -0.44 | 1.00 | 0.45 | -0.08 | 0.48 | 0.36 | -0.58 | 0.43 | -0.65 |
| nocoref_net_scor | 0.50 | 0.49 | 0.47 | 0.43 | 0.44 | 0.52 | — | 0.45 | 1.00 | -0.07 | 0.90 | 0.74 | -0.37 | 0.93 | -0.42 |
| Float | -0.06 | -0.09 | -0.09 | -0.08 | -0.08 | -0.10 | -0.03 | -0.08 | -0.07 | 1.00 | -0.05 | -0.08 | 0.02 | -0.07 | 0.08 |
| nocoref_position | 0.53 | 0.52 | 0.47 | 0.46 | 0.45 | 0.53 | — | 0.48 | 0.90 | -0.05 | 1.00 | 0.71 | -0.42 | 0.87 | -0.42 |
| nocoref_top_k | 0.40 | 0.39 | 0.36 | 0.35 | 0.35 | 0.42 | — | 0.36 | 0.74 | -0.08 | 0.71 | 1.00 | -0.31 | 0.70 | -0.38 |
| neutral | -0.93 | -0.58 | -0.56 | -0.62 | -0.59 | -0.63 | 0.42 | -0.58 | -0.37 | 0.02 | -0.42 | -0.31 | 1.00 | -0.34 | 0.68 |
| nocoref_neutral_ | 0.46 | 0.47 | 0.45 | 0.41 | 0.42 | 0.49 | — | 0.43 | 0.93 | -0.07 | 0.87 | 0.70 | -0.34 | 1.00 | -0.39 |
| DeBERTa_neutral | -0.75 | -0.59 | -0.55 | -0.64 | -0.53 | -0.87 | 0.34 | -0.65 | -0.42 | 0.08 | -0.42 | -0.38 | 0.68 | -0.39 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| positive | 0.637 | 0.651 | 0.647 | 0.654 | 0.649 |
| FLANG-ELECTRA_sentiment_score | 0.624 | 0.642 | 0.647 | 0.653 | 0.641 |
| FLANG-BERT_sentiment_score | 0.633 | 0.641 | 0.648 | 0.661 | 0.676 |
| FLANG-ELECTRA_positive | 0.617 | 0.640 | 0.646 | 0.655 | 0.639 |
| FLANG-BERT_positive | 0.628 | 0.639 | 0.644 | 0.660 | 0.670 |
| DeBERTa_sentiment_score | 0.630 | 0.638 | 0.641 | 0.649 | 0.641 |
| FMB_indecisive | 0.350 | 0.362 | 0.398 | 0.428 | 0.458 |
| Trading-Hero_positive | 0.623 | 0.637 | 0.632 | 0.641 | 0.639 |
| nocoref_net_score | 0.621 | 0.635 | 0.653 | 0.657 | 0.658 |
| Float | 0.379 | 0.365 | 0.353 | 0.330 | 0.314 |
| nocoref_positional | 0.619 | 0.632 | 0.643 | 0.640 | 0.639 |
| nocoref_top_k | 0.608 | 0.629 | 0.636 | 0.633 | 0.640 |
| neutral | 0.387 | 0.372 | 0.373 | 0.366 | 0.373 |
| nocoref_neutral_filter | 0.617 | 0.628 | 0.645 | 0.646 | 0.638 |
| DeBERTa_neutral | 0.383 | 0.373 | 0.370 | 0.363 | 0.369 |
| _n big movers_ | 1269 | 866 | 648 | 417 | 247 |

## Conclusion

- **Best numeric single:** `positive` (AUC 0.651, higher->bigger).
- **Best categorical:** `FineClass` (CV-AUC 0.619).
- **Best combo:** `positive + Float + nocoref_top_k` (CV-AUC 0.701; lift over best single: +0.050).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

