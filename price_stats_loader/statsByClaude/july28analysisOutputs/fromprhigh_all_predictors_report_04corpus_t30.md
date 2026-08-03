# fromPR_High(%) all-column predictor analysis — 04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv

- Rows: 3533 total -> dropped 99 null-target, 2 outliers (> 500%), 6 duplicate PRs -> **3426 kept**
- fromPR_High(%) on kept rows: median=6.40  mean=16.97  max=478.18
- Big-mover threshold: **fromPR_High(%) >= 30** -> **417** big movers (12.2%)
- Candidates: 115 numeric (auto-detected), 29 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DeBERTa_Body_elapsed_ms, DeBERTa_elapsed_ms, FLANG-BERT_Body_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_Body_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_Body_elapsed_ms, FinMarBa_elapsed_ms, FinancialBERT_Body_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, LouMcD-SDict_Body_elapsed_ms, LouMcD-SDict_elapsed_ms, ModernBERT_Body_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_Body_elapsed_ms, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_Body_elapsed_ms, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_Body_elapsed_ms, distilroberta_elapsed_ms, finbert-tone_Body_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | nocoref_recommended | 906 | 0.670 | higher->bigger | 0.170 | +0.198 | +0.199 |
| 2 | Float | 3426 | 0.330 | lower->bigger | 0.170 | -0.236 | -0.200 |
| 3 | FLANG-BERT_sentiment_score | 3426 | 0.661 | higher->bigger | 0.161 | +0.282 | +0.273 |
| 4 | FLANG-BERT_positive | 3426 | 0.660 | higher->bigger | 0.160 | +0.270 | +0.274 |
| 5 | nocoref_net_score | 3117 | 0.657 | higher->bigger | 0.157 | +0.273 | +0.273 |
| 6 | FLANG-ELECTRA_positive | 3426 | 0.655 | higher->bigger | 0.155 | +0.257 | +0.260 |
| 7 | positive | 3350 | 0.654 | higher->bigger | 0.154 | +0.291 | +0.277 |
| 8 | FLANG-ELECTRA_sentiment_score | 3426 | 0.653 | higher->bigger | 0.153 | +0.266 | +0.252 |
| 9 | FinMarBa_indecisive | 3426 | 0.348 | lower->bigger | 0.152 | -0.251 | -0.245 |
| 10 | nocoref_confidence_weighted | 3117 | 0.651 | higher->bigger | 0.151 | +0.269 | +0.266 |
| 11 | DeBERTa_positive | 3426 | 0.650 | higher->bigger | 0.150 | +0.287 | +0.264 |
| 12 | sentiment_score | 3426 | 0.650 | higher->bigger | 0.150 | +0.278 | +0.257 |
| 13 | DeBERTa_sentiment_score | 3426 | 0.649 | higher->bigger | 0.149 | +0.290 | +0.253 |
| 14 | nocoref_neutral_filter | 3117 | 0.646 | higher->bigger | 0.146 | +0.259 | +0.257 |
| 15 | Trading-Hero_sentiment_score | 3426 | 0.646 | higher->bigger | 0.146 | +0.267 | +0.252 |
| 16 | LastDailyClose | 3424 | 0.356 | lower->bigger | 0.144 | -0.249 | -0.121 |
| 17 | FLANG-ELECTRA_neutral | 3426 | 0.359 | lower->bigger | 0.141 | -0.229 | -0.235 |
| 18 | Trading-Hero_positive | 3426 | 0.641 | higher->bigger | 0.141 | +0.265 | +0.259 |
| 19 | nocoref_positional | 3117 | 0.640 | higher->bigger | 0.140 | +0.262 | +0.253 |
| 20 | DeBERTa_neutral | 3426 | 0.363 | lower->bigger | 0.137 | -0.263 | -0.244 |
| 21 | distilroberta_sentiment_score | 3426 | 0.634 | higher->bigger | 0.134 | +0.258 | +0.195 |
| 22 | neutral | 3426 | 0.366 | lower->bigger | 0.134 | -0.232 | -0.220 |
| 23 | ModernBERT_positive | 3426 | 0.634 | higher->bigger | 0.134 | +0.228 | +0.231 |
| 24 | nocoref_top_k | 3117 | 0.633 | higher->bigger | 0.133 | +0.234 | +0.238 |
| 25 | Trading-Hero_neutral | 3426 | 0.368 | lower->bigger | 0.132 | -0.244 | -0.239 |
| 26 | ModernBERT_sentiment_score | 3426 | 0.630 | higher->bigger | 0.130 | +0.222 | +0.218 |
| 27 | FLANG-BERT_neutral | 3426 | 0.371 | lower->bigger | 0.129 | -0.176 | -0.153 |
| 28 | negative | 3426 | 0.372 | lower->bigger | 0.128 | -0.245 | -0.115 |
| 29 | Trading-Hero_negative | 3426 | 0.374 | lower->bigger | 0.126 | -0.226 | -0.062 |
| 30 | distilroberta_positive | 3426 | 0.626 | higher->bigger | 0.126 | +0.249 | +0.196 |
| 31 | ModernBERT_neutral | 3426 | 0.376 | lower->bigger | 0.124 | -0.220 | -0.222 |
| 32 | Trading-Hero_Body_sentiment_score | 3426 | 0.623 | higher->bigger | 0.123 | +0.215 | +0.181 |
| 33 | Trading-Hero_Body_positive | 3426 | 0.621 | higher->bigger | 0.121 | +0.197 | +0.161 |
| 34 | FinMarBa_positive | 3426 | 0.620 | higher->bigger | 0.120 | +0.222 | +0.217 |
| 35 | FinancialBERT_Body_sentiment_score | 3426 | 0.617 | higher->bigger | 0.117 | +0.229 | +0.232 |
| 36 | FinancialBERT_Body_positive | 3426 | 0.616 | higher->bigger | 0.116 | +0.225 | +0.229 |
| 37 | FinMarBa_Body_indecisive | 3426 | 0.387 | lower->bigger | 0.113 | -0.235 | -0.228 |
| 38 | FinancialBERT_Body_neutral | 3426 | 0.389 | lower->bigger | 0.111 | -0.213 | -0.216 |
| 39 | distilroberta_neutral | 3426 | 0.391 | lower->bigger | 0.109 | -0.226 | -0.189 |
| 40 | FinMarBa_sentiment_score | 3426 | 0.608 | higher->bigger | 0.108 | +0.208 | +0.202 |
| 41 | finbert-tone_sentiment_score | 3426 | 0.607 | higher->bigger | 0.107 | +0.195 | +0.165 |
| 42 | MktCap | 875 | 0.394 | lower->bigger | 0.106 | -0.203 | -0.082 |
| 43 | finbert-tone_positive | 3426 | 0.605 | higher->bigger | 0.105 | +0.189 | +0.157 |
| 44 | Trading-Hero_Body_neutral | 3426 | 0.397 | lower->bigger | 0.103 | -0.159 | -0.131 |
| 45 | FLANG-BERT_negative | 3426 | 0.402 | lower->bigger | 0.098 | -0.191 | -0.174 |
| 46 | FLANG-BERT_Body_sentiment_score | 3426 | 0.597 | higher->bigger | 0.097 | +0.181 | +0.163 |
| 47 | FLANG-ELECTRA_Body_positive | 3426 | 0.597 | higher->bigger | 0.097 | +0.218 | +0.204 |
| 48 | distilroberta_Body_sentiment_score | 3426 | 0.595 | higher->bigger | 0.095 | +0.220 | +0.210 |
| 49 | FinancialBERT_Body_negative | 3426 | 0.406 | lower->bigger | 0.094 | -0.212 | -0.074 |
| 50 | distilroberta_Body_positive | 3426 | 0.593 | higher->bigger | 0.093 | +0.211 | +0.204 |
| 51 | FLANG-ELECTRA_negative | 3426 | 0.593 | higher->bigger | 0.093 | +0.139 | -0.061 |
| 52 | RoBERTa_positive | 3426 | 0.592 | higher->bigger | 0.092 | +0.169 | +0.129 |
| 53 | RoBERTa_sentiment_score | 3426 | 0.592 | higher->bigger | 0.092 | +0.167 | +0.139 |
| 54 | finbert-tone_neutral | 3426 | 0.408 | lower->bigger | 0.092 | -0.165 | -0.139 |
| 55 | FLANG-BERT_Body_positive | 3426 | 0.592 | higher->bigger | 0.092 | +0.170 | +0.154 |
| 56 | FinMarBa_negative | 3426 | 0.410 | lower->bigger | 0.090 | -0.185 | -0.178 |
| 57 | FLANG-BERT_Body_neutral | 3426 | 0.418 | lower->bigger | 0.082 | -0.152 | -0.137 |
| 58 | FLANG-ELECTRA_Body_sentiment_score | 3426 | 0.581 | higher->bigger | 0.081 | +0.199 | +0.210 |
| 59 | distilroberta_Body_neutral | 3426 | 0.422 | lower->bigger | 0.078 | -0.174 | -0.174 |
| 60 | FLANG-ELECTRA_Body_neutral | 3426 | 0.424 | lower->bigger | 0.076 | -0.181 | -0.154 |
| 61 | ModernBERT_Body_positive | 3426 | 0.575 | higher->bigger | 0.075 | +0.175 | +0.179 |
| 62 | FLANG-BERT_Body_negative | 3426 | 0.425 | lower->bigger | 0.075 | -0.117 | -0.075 |
| 63 | RoBERTa_neutral | 3426 | 0.428 | lower->bigger | 0.072 | -0.143 | -0.106 |
| 64 | FMB_indecisive | 135 | 0.428 | lower->bigger | 0.072 | -0.270 | -0.269 |
| 65 | ModernBERT_Body_sentiment_score | 3426 | 0.571 | higher->bigger | 0.071 | +0.170 | +0.172 |
| 66 | DeBERTa_Body_positive | 3426 | 0.570 | higher->bigger | 0.070 | +0.187 | +0.185 |
| 67 | DeBERTa_Body_sentiment_score | 3426 | 0.569 | higher->bigger | 0.069 | +0.186 | +0.194 |
| 68 | distilroberta_negative | 3426 | 0.568 | higher->bigger | 0.068 | +0.168 | -0.026 |
| 69 | finbert-tone_Body_sentiment_score | 3426 | 0.564 | higher->bigger | 0.064 | +0.174 | +0.136 |
| 70 | Trading-Hero_Body_negative | 3426 | 0.438 | lower->bigger | 0.062 | -0.091 | -0.133 |
| 71 | finbert-tone_Body_positive | 3426 | 0.558 | higher->bigger | 0.058 | +0.156 | +0.122 |
| 72 | tradeSizeBaseline | 1322 | 0.444 | lower->bigger | 0.056 | -0.056 | -0.025 |
| 73 | ModernBERT_Body_neutral | 3426 | 0.444 | lower->bigger | 0.056 | -0.128 | -0.126 |
| 74 | RoBERTa_negative | 3426 | 0.446 | lower->bigger | 0.054 | -0.082 | -0.088 |
| 75 | ModernBERT_Body_negative | 3426 | 0.449 | lower->bigger | 0.051 | -0.140 | -0.140 |
| 76 | FinMarBa_Body_positive | 3426 | 0.549 | higher->bigger | 0.049 | +0.162 | +0.165 |
| 77 | finbert-tone_Body_neutral | 3426 | 0.455 | lower->bigger | 0.045 | -0.130 | -0.099 |
| 78 | itiBaseline | 1322 | 0.455 | lower->bigger | 0.045 | -0.059 | -0.053 |
| 79 | FLANG-ELECTRA_Body_negative | 3426 | 0.544 | higher->bigger | 0.044 | +0.095 | -0.121 |
| 80 | LouMcD-SDict_Body_sentiment_score | 3426 | 0.538 | higher->bigger | 0.038 | +0.067 | +0.080 |
| 81 | distilroberta_Body_negative | 3426 | 0.538 | higher->bigger | 0.038 | +0.037 | -0.097 |
| 82 | FMB_positive | 135 | 0.537 | higher->bigger | 0.037 | +0.228 | +0.210 |
| 83 | DeBERTa_Body_neutral | 3426 | 0.464 | lower->bigger | 0.036 | -0.110 | -0.117 |
| 84 | ModernBERT_negative | 3426 | 0.464 | lower->bigger | 0.036 | -0.071 | -0.056 |
| 85 | LouMcD-SDict_Body_negative | 3426 | 0.465 | lower->bigger | 0.035 | -0.034 | -0.071 |
| 86 | LouMcD-SDict_Body_weak_modal | 3426 | 0.466 | lower->bigger | 0.034 | -0.083 | -0.081 |
| 87 | FinMarBa_Body_sentiment_score | 3426 | 0.534 | higher->bigger | 0.034 | +0.140 | +0.143 |
| 88 | LouMcD-SDict_sentiment_score | 3426 | 0.534 | higher->bigger | 0.034 | +0.103 | +0.099 |
| 89 | DeBERTa_negative | 3426 | 0.530 | higher->bigger | 0.030 | +0.013 | -0.055 |
| 90 | LouMcD-SDict_Body_strong_modal | 3426 | 0.471 | lower->bigger | 0.029 | -0.106 | -0.125 |
| 91 | FinancialBERT_negative | 3426 | 0.472 | lower->bigger | 0.028 | -0.061 | -0.013 |
| 92 | DeBERTa_Body_negative | 3426 | 0.472 | lower->bigger | 0.028 | -0.116 | -0.134 |
| 93 | FMB_sentiment_score | 135 | 0.525 | higher->bigger | 0.025 | +0.211 | +0.190 |
| 94 | FinancialBERT_sentiment_score | 3426 | 0.522 | higher->bigger | 0.022 | +0.015 | +0.027 |
| 95 | LouMcD-SDict_positive | 3426 | 0.522 | higher->bigger | 0.022 | +0.078 | +0.081 |
| 96 | LouMcD-SDict_negative | 3426 | 0.481 | lower->bigger | 0.019 | -0.067 | -0.070 |
| 97 | finbert-tone_negative | 3426 | 0.481 | lower->bigger | 0.019 | -0.056 | -0.063 |
| 98 | RoBERTa_Body_positive | 3426 | 0.517 | higher->bigger | 0.017 | +0.071 | +0.050 |
| 99 | RoBERTa_Body_sentiment_score | 3426 | 0.517 | higher->bigger | 0.017 | +0.070 | +0.055 |
| 100 | RoBERTa_Body_neutral | 3426 | 0.484 | lower->bigger | 0.016 | -0.066 | -0.042 |
| 101 | finbert-tone_Body_negative | 3426 | 0.486 | lower->bigger | 0.014 | -0.094 | -0.088 |
| 102 | LouMcD-SDict_Body_positive | 3426 | 0.513 | higher->bigger | 0.013 | +0.071 | +0.058 |
| 103 | LouMcD-SDict_weak_modal | 3426 | 0.487 | lower->bigger | 0.013 | -0.064 | -0.062 |
| 104 | FinancialBERT_neutral | 3426 | 0.512 | higher->bigger | 0.012 | +0.032 | -0.018 |
| 105 | FMB_negative | 135 | 0.488 | lower->bigger | 0.012 | -0.183 | -0.159 |
| 106 | FinMarBa_Body_negative | 3426 | 0.488 | lower->bigger | 0.012 | -0.106 | -0.108 |
| 107 | RoBERTa_Body_negative | 3426 | 0.511 | higher->bigger | 0.011 | +0.002 | -0.046 |
| 108 | LouMcD-SDict_Body_uncertainty | 3426 | 0.511 | higher->bigger | 0.011 | +0.013 | +0.018 |
| 109 | FinancialBERT_positive | 3426 | 0.510 | higher->bigger | 0.010 | -0.001 | +0.024 |
| 110 | LouMcD-SDict_Body_litigious | 3426 | 0.491 | lower->bigger | 0.009 | -0.012 | -0.026 |
| 111 | LouMcD-SDict_litigious | 3426 | 0.492 | lower->bigger | 0.008 | -0.001 | -0.003 |
| 112 | LouMcD-SDict_uncertainty | 3426 | 0.495 | lower->bigger | 0.005 | -0.018 | -0.016 |
| 113 | LouMcD-SDict_constraining | 3426 | 0.504 | higher->bigger | 0.004 | +0.026 | +0.026 |
| 114 | LouMcD-SDict_Body_constraining | 3426 | 0.498 | lower->bigger | 0.002 | +0.036 | +0.028 |
| 115 | LouMcD-SDict_strong_modal | 3426 | 0.501 | higher->bigger | 0.001 | +0.008 | +0.010 |

## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)

| rank | column | n | CV-AUC | |AUC-0.5| |
|---|---|---|---|---|
| 1 | FineClass | 3426 | 0.630 | 0.130 |
| 2 | FLANG-ELECTRA_label | 3426 | 0.618 | 0.118 |
| 3 | BroadClass | 3426 | 0.618 | 0.118 |
| 4 | DeBERTa_label | 3426 | 0.605 | 0.105 |
| 5 | FLANG-BERT_label | 3426 | 0.604 | 0.104 |
| 6 | Trading-Hero_label | 3426 | 0.593 | 0.093 |
| 7 | ModernBERT_label | 3426 | 0.593 | 0.093 |
| 8 | FinancialBERT_Body_label | 3426 | 0.576 | 0.076 |
| 9 | Trading-Hero_Body_label | 3426 | 0.571 | 0.071 |
| 10 | distilroberta_label | 3426 | 0.567 | 0.067 |
| 11 | distilroberta_Body_label | 3426 | 0.564 | 0.064 |
| 12 | finbert-tone_label | 3426 | 0.558 | 0.058 |
| 13 | label | 3426 | 0.557 | 0.057 |
| 14 | FLANG-ELECTRA_Body_label | 3426 | 0.556 | 0.056 |
| 15 | FLANG-BERT_Body_label | 3426 | 0.555 | 0.055 |
| 16 | DeBERTa_Body_label | 3426 | 0.550 | 0.050 |
| 17 | Author | 3426 | 0.549 | 0.049 |
| 18 | Exchange | 3426 | 0.540 | 0.040 |
| 19 | ModernBERT_Body_label | 3426 | 0.531 | 0.031 |
| 20 | finbert-tone_Body_label | 3426 | 0.525 | 0.025 |
| 21 | boilerplate_category | 3426 | 0.521 | 0.021 |
| 22 | LouMcD-SDict_label | 3426 | 0.520 | 0.020 |
| 23 | LouMcD-SDict_Body_label | 3426 | 0.515 | 0.015 |
| 24 | FinancialBERT_label | 3426 | 0.486 | 0.014 |
| 25 | FinMarBa_label | 3426 | 0.507 | 0.007 |
| 26 | FinMarBa_Body_label | 3426 | 0.494 | 0.006 |
| 27 | RoBERTa_Body_label | 3426 | 0.504 | 0.004 |
| 28 | FMB_label | 3426 | 0.498 | 0.002 |
| 29 | RoBERTa_label | 3426 | 0.501 | 0.001 |

## Categorical breakdowns — per level (min 20 rows; baseline big-mover rate 12.2%)

### `FineClass` (CV-AUC 0.630)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 291 | 25.1% | +12.9% | 11.28 |
| Partnership | 256 | 19.9% | +7.8% | 9.66 |
| Clinical_Data | 127 | 17.3% | +5.2% | 7.96 |
| Contract_Award | 180 | 16.7% | +4.5% | 8.45 |
| Patent_IP | 73 | 16.4% | +4.3% | 7.63 |
| Business_Update | 103 | 14.6% | +2.4% | 7.67 |
| Offering_Financing | 217 | 13.4% | +1.2% | 7.14 |
| Crypto_Treasury | 68 | 13.2% | +1.1% | 8.14 |
| Product_Launch | 288 | 11.8% | -0.4% | 7.10 |
| FDA_Designation | 283 | 11.0% | -1.2% | 5.45 |
| Clinical_Update | 151 | 9.9% | -2.2% | 8.59 |
| <other> | 266 | 8.3% | -3.9% | 4.06 |
| Other | 347 | 7.8% | -4.4% | 6.12 |
| Earnings_Results | 487 | 7.2% | -5.0% | 4.32 |
| Conference_Presentation | 123 | 4.9% | -7.3% | 4.32 |
| Management_Change | 166 | 3.6% | -8.6% | 4.50 |

### `FLANG-ELECTRA_label` (CV-AUC 0.618)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1376 | 19.2% | +7.0% | 9.44 |
| neutral | 1930 | 7.7% | -4.5% | 5.01 |
| negative | 120 | 4.2% | -8.0% | 4.00 |

### `BroadClass` (CV-AUC 0.618)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| MA | 291 | 25.1% | +12.9% | 11.28 |
| IP | 73 | 16.4% | +4.3% | 7.63 |
| Commercial | 724 | 15.9% | +3.7% | 8.20 |
| Financing | 240 | 13.3% | +1.2% | 7.21 |
| Clinical | 278 | 13.3% | +1.1% | 8.46 |
| Crypto | 68 | 13.2% | +1.1% | 8.14 |
| FDA | 307 | 11.1% | -1.1% | 5.71 |
| Compliance | 140 | 10.7% | -1.5% | 4.06 |
| Other | 347 | 7.8% | -4.4% | 6.12 |
| Earnings | 514 | 7.0% | -5.2% | 4.21 |
| Corporate | 392 | 6.9% | -5.3% | 4.95 |
| Legal | 52 | 0.0% | -12.2% | 2.95 |

### `DeBERTa_label` (CV-AUC 0.605)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1449 | 18.1% | +5.9% | 8.99 |
| neutral | 1875 | 8.1% | -4.1% | 5.02 |
| negative | 102 | 3.9% | -8.3% | 4.39 |

### `FLANG-BERT_label` (CV-AUC 0.604)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1806 | 17.0% | +4.8% | 8.33 |
| negative | 522 | 7.7% | -4.5% | 4.33 |
| neutral | 1098 | 6.4% | -5.8% | 5.22 |

### `Trading-Hero_label` (CV-AUC 0.593)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1474 | 17.9% | +5.7% | 9.18 |
| neutral | 1829 | 8.0% | -4.2% | 4.94 |
| negative | 123 | 5.7% | -6.5% | 4.43 |

### `ModernBERT_label` (CV-AUC 0.593)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1385 | 17.7% | +5.5% | 8.60 |
| neutral | 1966 | 8.7% | -3.5% | 5.28 |
| negative | 75 | 1.3% | -10.8% | 4.43 |

### `FinancialBERT_Body_label` (CV-AUC 0.576)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2216 | 15.1% | +2.9% | 7.87 |
| neutral | 1175 | 7.1% | -5.1% | 4.49 |
| negative | 35 | 0.0% | -12.2% | 2.33 |

### `Trading-Hero_Body_label` (CV-AUC 0.571)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1056 | 17.7% | +5.5% | 9.05 |
| neutral | 2283 | 10.0% | -2.1% | 5.74 |
| negative | 87 | 1.1% | -11.0% | 3.00 |

### `distilroberta_label` (CV-AUC 0.567)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 849 | 18.8% | +6.7% | 9.74 |
| neutral | 2547 | 10.1% | -2.1% | 5.55 |
| negative | 30 | 0.0% | -12.2% | 4.55 |

### `distilroberta_Body_label` (CV-AUC 0.564)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2113 | 15.0% | +2.8% | 7.92 |
| neutral | 1214 | 7.8% | -4.3% | 4.90 |
| negative | 99 | 5.1% | -7.1% | 2.50 |

### `finbert-tone_label` (CV-AUC 0.558)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 850 | 17.9% | +5.7% | 8.65 |
| neutral | 2537 | 10.4% | -1.8% | 5.75 |
| negative | 39 | 2.6% | -9.6% | 1.90 |

### `label` (CV-AUC 0.557)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2557 | 14.4% | +2.2% | 7.57 |
| <other> | 63 | 6.3% | -5.8% | 3.72 |
| negative | 106 | 5.7% | -6.5% | 3.21 |
| neutral | 687 | 5.5% | -6.6% | 3.74 |

### `FLANG-ELECTRA_Body_label` (CV-AUC 0.556)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2275 | 14.4% | +2.2% | 7.69 |
| neutral | 960 | 8.4% | -3.7% | 4.84 |
| negative | 191 | 4.2% | -8.0% | 3.18 |

### `FLANG-BERT_Body_label` (CV-AUC 0.555)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1450 | 15.4% | +3.3% | 7.79 |
| neutral | 1938 | 9.9% | -2.3% | 5.55 |
| negative | 38 | 2.6% | -9.5% | 3.59 |

### `DeBERTa_Body_label` (CV-AUC 0.550)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2175 | 14.3% | +2.1% | 7.49 |
| neutral | 994 | 9.5% | -2.7% | 5.34 |
| negative | 257 | 4.7% | -7.5% | 3.33 |

### `Author` (CV-AUC 0.549)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| Globe Newswire | 1870 | 14.1% | +1.9% | 7.56 |
| PR Newswire | 527 | 14.0% | +1.9% | 5.91 |
| Newsfile Corp | 143 | 10.5% | -1.7% | 6.44 |
| ACCESSWIRE | 358 | 8.9% | -3.2% | 6.67 |
| Business Wire | 528 | 6.1% | -6.1% | 4.38 |

### `Exchange` (CV-AUC 0.540)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| NYSE AMERICAN | 192 | 17.2% | +5.0% | 6.48 |
| NYSE American | 149 | 16.1% | +3.9% | 8.32 |
| NASDAQ | 2805 | 12.5% | +0.4% | 6.82 |
| NYSE | 276 | 2.9% | -9.3% | 3.11 |

### `ModernBERT_Body_label` (CV-AUC 0.531)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2725 | 13.5% | +1.3% | 6.93 |
| neutral | 389 | 7.5% | -4.7% | 4.90 |
| negative | 312 | 6.4% | -5.8% | 4.00 |

### `finbert-tone_Body_label` (CV-AUC 0.525)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1480 | 13.7% | +1.5% | 7.69 |
| neutral | 1883 | 11.3% | -0.9% | 5.64 |
| negative | 63 | 3.2% | -9.0% | 2.29 |

### `boilerplate_category` (CV-AUC 0.521)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| lawfirm | 467 | 14.1% | +2.0% | 7.20 |
| <missing> | 2002 | 13.4% | +1.3% | 7.14 |
| compliance | 156 | 12.8% | +0.6% | 4.88 |
| conference | 251 | 10.0% | -2.2% | 5.38 |
| earnings | 550 | 6.7% | -5.4% | 4.56 |

### `LouMcD-SDict_label` (CV-AUC 0.520)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 649 | 14.8% | +2.6% | 7.96 |
| neutral | 2548 | 11.9% | -0.2% | 6.12 |
| negative | 229 | 7.4% | -4.7% | 4.61 |

### `LouMcD-SDict_Body_label` (CV-AUC 0.515)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2277 | 13.0% | +0.8% | 6.70 |
| neutral | 485 | 12.0% | -0.2% | 5.81 |
| negative | 664 | 9.5% | -2.7% | 5.57 |

### `FinancialBERT_label` (CV-AUC 0.486)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 3407 | 12.1% | -0.1% | 6.40 |

### `FinMarBa_label` (CV-AUC 0.507)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3204 | 12.8% | +0.6% | 6.58 |
| negative | 221 | 3.6% | -8.6% | 4.36 |

### `FinMarBa_Body_label` (CV-AUC 0.494)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3177 | 12.4% | +0.2% | 6.56 |
| negative | 245 | 9.4% | -2.8% | 4.42 |

### `RoBERTa_Body_label` (CV-AUC 0.504)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 2883 | 12.5% | +0.3% | 6.45 |
| positive | 537 | 10.2% | -1.9% | 6.09 |

### `FMB_label` (CV-AUC 0.498)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 129 | 17.1% | +4.9% | 9.07 |
| <missing> | 3291 | 12.0% | -0.2% | 6.25 |

### `RoBERTa_label` (CV-AUC 0.501)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 254 | 16.5% | +4.4% | 8.15 |
| neutral | 3161 | 11.9% | -0.3% | 6.21 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: nocoref_recommended, Float, FLANG-BERT_sentiment_score, FLANG-BERT_positive, nocoref_net_score, FLANG-ELECTRA_positive, positive, FLANG-ELECTRA_sentiment_score, FinMarBa_indecisive, DeBERTa_positive, Trading-Hero_sentiment_score, LastDailyClose, nocoref_positional, DeBERTa_neutral, distilroberta_sentiment_score

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): nocoref_confidence_weighted ~ nocoref_recommended; sentiment_score ~ positive; DeBERTa_sentiment_score ~ DeBERTa_positive; nocoref_neutral_filter ~ nocoref_recommended; FLANG-ELECTRA_neutral ~ FLANG-ELECTRA_positive; Trading-Hero_positive ~ Trading-Hero_sentiment_score

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | nocoref_recommended + Float + positive | 831 | 0.764 | nocoref_recommended=+0.28, Float=-0.63, positive=+0.65 |
| 2 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + FinMarBa_indecisive | 906 | 0.756 | nocoref_recommended=+0.26, FLANG-ELECTRA_sentiment_score=+0.60, FinMarBa_indecisive=-0.41 |
| 3 | nocoref_recommended + positive + FinMarBa_indecisive | 831 | 0.756 | nocoref_recommended=+0.21, positive=+0.45, FinMarBa_indecisive=-0.55 |
| 4 | nocoref_recommended + Float + FLANG-ELECTRA_sentiment_score | 906 | 0.755 | nocoref_recommended=+0.33, Float=-0.44, FLANG-ELECTRA_sentiment_score=+0.75 |
| 5 | nocoref_recommended + FLANG-ELECTRA_positive + FinMarBa_indecisive | 906 | 0.753 | nocoref_recommended=+0.28, FLANG-ELECTRA_positive=+0.55, FinMarBa_indecisive=-0.41 |
| 6 | nocoref_recommended + Float + FLANG-ELECTRA_positive | 906 | 0.752 | nocoref_recommended=+0.34, Float=-0.44, FLANG-ELECTRA_positive=+0.69 |
| 7 | nocoref_recommended + positive + DeBERTa_positive | 831 | 0.742 | nocoref_recommended=+0.27, positive=+0.41, DeBERTa_positive=+0.37 |
| 8 | nocoref_recommended + FinMarBa_indecisive + Trading-Hero_sentiment_score | 906 | 0.740 | nocoref_recommended=+0.29, FinMarBa_indecisive=-0.48, Trading-Hero_sentiment_score=+0.42 |
| 9 | nocoref_recommended + FinMarBa_indecisive + distilroberta_sentiment_score | 906 | 0.740 | nocoref_recommended=+0.34, FinMarBa_indecisive=-0.54, distilroberta_sentiment_score=+0.29 |
| 10 | nocoref_recommended + FLANG-ELECTRA_sentiment_score | 906 | 0.739 | nocoref_recommended=+0.34, FLANG-ELECTRA_sentiment_score=+0.80 |
| 11 | nocoref_recommended + FLANG-BERT_sentiment_score + FLANG-ELECTRA_sentiment_score | 906 | 0.738 | nocoref_recommended=+0.31, FLANG-BERT_sentiment_score=+0.20, FLANG-ELECTRA_sentiment_score=+0.70 |
| 12 | nocoref_recommended + positive + DeBERTa_neutral | 831 | 0.738 | nocoref_recommended=+0.29, positive=+0.48, DeBERTa_neutral=-0.29 |
| 13 | nocoref_recommended + FLANG-ELECTRA_positive + FLANG-ELECTRA_sentiment_score | 906 | 0.738 | nocoref_recommended=+0.34, FLANG-ELECTRA_positive=+0.25, FLANG-ELECTRA_sentiment_score=+0.54 |
| 14 | nocoref_recommended + FLANG-BERT_sentiment_score + positive | 831 | 0.738 | nocoref_recommended=+0.25, FLANG-BERT_sentiment_score=+0.41, positive=+0.49 |
| 15 | nocoref_recommended + positive + FLANG-ELECTRA_sentiment_score | 831 | 0.738 | nocoref_recommended=+0.23, positive=+0.28, FLANG-ELECTRA_sentiment_score=+0.70 |
| 16 | nocoref_recommended + Float + Trading-Hero_sentiment_score | 906 | 0.738 | nocoref_recommended=+0.36, Float=-0.46, Trading-Hero_sentiment_score=+0.63 |
| 17 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + LastDailyClose | 906 | 0.738 | nocoref_recommended=+0.35, FLANG-ELECTRA_sentiment_score=+0.79, LastDailyClose=+0.14 |
| 18 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + distilroberta_sentiment_score | 906 | 0.738 | nocoref_recommended=+0.32, FLANG-ELECTRA_sentiment_score=+0.70, distilroberta_sentiment_score=+0.15 |
| 19 | nocoref_recommended + FinMarBa_indecisive + DeBERTa_positive | 906 | 0.737 | nocoref_recommended=+0.29, FinMarBa_indecisive=-0.49, DeBERTa_positive=+0.40 |
| 20 | nocoref_recommended + Float + FinMarBa_indecisive | 906 | 0.737 | nocoref_recommended=+0.38, Float=-0.46, FinMarBa_indecisive=-0.63 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | nocoref_recommended + Float + positive | 3 | 0.764 |
| 2 | nocoref_recommended + FLANG-ELECTRA_sentiment_score + FinMarBa_indecisive | 3 | 0.756 |
| 3 | nocoref_recommended + positive + FinMarBa_indecisive | 3 | 0.756 |
| 4 | nocoref_recommended + Float + FLANG-ELECTRA_sentiment_score | 3 | 0.755 |
| 5 | nocoref_recommended + FLANG-ELECTRA_positive + FinMarBa_indecisive | 3 | 0.753 |
| 6 | nocoref_recommended + Float + FLANG-ELECTRA_positive | 3 | 0.752 |
| 7 | nocoref_recommended + positive + DeBERTa_positive | 3 | 0.742 |
| 8 | nocoref_recommended + FinMarBa_indecisive + Trading-Hero_sentiment_score | 3 | 0.740 |
| 9 | nocoref_recommended + FinMarBa_indecisive + distilroberta_sentiment_score | 3 | 0.740 |
| 10 | nocoref_recommended + FLANG-ELECTRA_sentiment_score | 2 | 0.739 |
| 11 | nocoref_recommended + FLANG-BERT_sentiment_score + FLANG-ELECTRA_sentiment_score | 3 | 0.738 |
| 12 | nocoref_recommended + positive + DeBERTa_neutral | 3 | 0.738 |
| 13 | nocoref_recommended + FLANG-ELECTRA_positive + FLANG-ELECTRA_sentiment_score | 3 | 0.738 |
| 14 | nocoref_recommended + FLANG-BERT_sentiment_score + positive | 3 | 0.738 |
| 15 | nocoref_recommended + positive + FLANG-ELECTRA_sentiment_score | 3 | 0.738 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | nocoref_reco | Float | FLANG-BERT_s | FLANG-BERT_p | nocoref_net_ | FLANG-ELECTR | positive | FLANG-ELECTR | FinMarBa_ind | DeBERTa_posi | Trading-Hero | LastDailyClo | nocoref_posi | DeBERTa_neut | distilrobert |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| nocoref_recommen | 1.00 | -0.08 | 0.34 | 0.29 | 0.91 | 0.36 | 0.41 | 0.42 | -0.36 | 0.41 | 0.37 | -0.12 | 0.85 | -0.36 | 0.40 |
| Float | -0.08 | 1.00 | -0.09 | -0.08 | -0.07 | -0.08 | -0.06 | -0.09 | 0.08 | -0.10 | -0.10 | 0.06 | -0.05 | 0.08 | -0.10 |
| FLANG-BERT_senti | 0.34 | -0.09 | 1.00 | 0.93 | 0.47 | 0.57 | 0.70 | 0.64 | -0.53 | 0.64 | 0.63 | -0.13 | 0.47 | -0.55 | 0.62 |
| FLANG-BERT_posit | 0.29 | -0.08 | 0.93 | 1.00 | 0.44 | 0.62 | 0.69 | 0.65 | -0.47 | 0.61 | 0.60 | -0.12 | 0.45 | -0.53 | 0.60 |
| nocoref_net_scor | 0.91 | -0.07 | 0.47 | 0.44 | 1.00 | 0.43 | 0.50 | 0.49 | -0.38 | 0.50 | 0.46 | -0.16 | 0.90 | -0.42 | 0.48 |
| FLANG-ELECTRA_po | 0.36 | -0.08 | 0.57 | 0.62 | 0.43 | 1.00 | 0.67 | 0.90 | -0.42 | 0.65 | 0.60 | -0.12 | 0.46 | -0.64 | 0.62 |
| positive | 0.41 | -0.06 | 0.70 | 0.69 | 0.50 | 0.67 | 1.00 | 0.69 | -0.54 | 0.80 | 0.70 | -0.16 | 0.53 | -0.75 | 0.73 |
| FLANG-ELECTRA_se | 0.42 | -0.09 | 0.64 | 0.65 | 0.49 | 0.90 | 0.69 | 1.00 | -0.45 | 0.67 | 0.65 | -0.12 | 0.52 | -0.59 | 0.66 |
| FinMarBa_indecis | -0.36 | 0.08 | -0.53 | -0.47 | -0.38 | -0.42 | -0.54 | -0.45 | 1.00 | -0.54 | -0.52 | 0.18 | -0.38 | 0.50 | -0.52 |
| DeBERTa_positive | 0.41 | -0.10 | 0.64 | 0.61 | 0.50 | 0.65 | 0.80 | 0.67 | -0.54 | 1.00 | 0.72 | -0.17 | 0.50 | -0.93 | 0.75 |
| Trading-Hero_sen | 0.37 | -0.10 | 0.63 | 0.60 | 0.46 | 0.60 | 0.70 | 0.65 | -0.52 | 0.72 | 1.00 | -0.16 | 0.48 | -0.64 | 0.69 |
| LastDailyClose | -0.12 | 0.06 | -0.13 | -0.12 | -0.16 | -0.12 | -0.16 | -0.12 | 0.18 | -0.17 | -0.16 | 1.00 | -0.14 | 0.16 | -0.16 |
| nocoref_position | 0.85 | -0.05 | 0.47 | 0.45 | 0.90 | 0.46 | 0.53 | 0.52 | -0.38 | 0.50 | 0.48 | -0.14 | 1.00 | -0.42 | 0.49 |
| DeBERTa_neutral | -0.36 | 0.08 | -0.55 | -0.53 | -0.42 | -0.64 | -0.75 | -0.59 | 0.50 | -0.93 | -0.64 | 0.16 | -0.42 | 1.00 | -0.67 |
| distilroberta_se | 0.40 | -0.10 | 0.62 | 0.60 | 0.48 | 0.62 | 0.73 | 0.66 | -0.52 | 0.75 | 0.69 | -0.16 | 0.49 | -0.67 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| nocoref_recommended | 0.594 | 0.616 | 0.625 | 0.670 | 0.699 |
| Float | 0.379 | 0.365 | 0.353 | 0.330 | 0.314 |
| FLANG-BERT_sentiment_score | 0.633 | 0.641 | 0.648 | 0.661 | 0.676 |
| FLANG-BERT_positive | 0.628 | 0.639 | 0.644 | 0.660 | 0.670 |
| nocoref_net_score | 0.621 | 0.635 | 0.653 | 0.657 | 0.658 |
| FLANG-ELECTRA_positive | 0.617 | 0.640 | 0.646 | 0.655 | 0.639 |
| positive | 0.637 | 0.651 | 0.647 | 0.654 | 0.649 |
| FLANG-ELECTRA_sentiment_score | 0.624 | 0.642 | 0.647 | 0.653 | 0.641 |
| FinMarBa_indecisive | 0.374 | 0.364 | 0.358 | 0.348 | 0.344 |
| DeBERTa_positive | 0.628 | 0.637 | 0.640 | 0.650 | 0.644 |
| Trading-Hero_sentiment_score | 0.622 | 0.635 | 0.633 | 0.646 | 0.643 |
| LastDailyClose | 0.376 | 0.384 | 0.382 | 0.356 | 0.339 |
| nocoref_positional | 0.619 | 0.632 | 0.643 | 0.640 | 0.639 |
| DeBERTa_neutral | 0.383 | 0.373 | 0.370 | 0.363 | 0.369 |
| distilroberta_sentiment_score | 0.617 | 0.624 | 0.621 | 0.634 | 0.627 |
| _n big movers_ | 1269 | 866 | 648 | 417 | 247 |

## Conclusion

- **Best numeric single:** `nocoref_recommended` (AUC 0.670, higher->bigger).
- **Best categorical:** `FineClass` (CV-AUC 0.630).
- **Best combo:** `nocoref_recommended + Float + positive` (CV-AUC 0.764; lift over best single: +0.094).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

