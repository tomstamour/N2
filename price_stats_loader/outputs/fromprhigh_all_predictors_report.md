# fromPR_High(%) all-column predictor analysis — PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv

- Rows: 3306 total -> dropped 53 null-target, 0 outliers (> 500%), 0 duplicate PRs -> **3253 kept**
- fromPR_High(%) on kept rows: median=5.17  mean=13.22  max=455.65
- Big-mover threshold: **fromPR_High(%) >= 30** -> **287** big movers (8.8%)
- Candidates: 139 numeric (auto-detected), 28 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DailyHigh($), DailyHigh(%), DeBERTa_Body_elapsed_ms, DeBERTa_elapsed_ms, FLANG-BERT_Body_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_Body_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_Body_elapsed_ms, FinMarBa_elapsed_ms, FinancialBERT_Body_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, KwScoreCompletedAt, LouMcD-SDict_Body_elapsed_ms, LouMcD-SDict_elapsed_ms, ModernBERT_Body_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_Body_elapsed_ms, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_Body_elapsed_ms, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_Body_elapsed_ms, distilroberta_elapsed_ms, finbert-tone_Body_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb | 3253 | 0.857 | higher->bigger | 0.357 | +0.457 | +0.496 |
| 2 | model_proba_pctl | 3253 | 0.856 | higher->bigger | 0.356 | +0.457 | +0.452 |
| 3 | model_proba | 3253 | 0.856 | higher->bigger | 0.356 | +0.457 | +0.485 |
| 4 | model_proba_pctl_hl | 3253 | 0.827 | higher->bigger | 0.327 | +0.378 | +0.383 |
| 5 | model_proba_hl | 3253 | 0.827 | higher->bigger | 0.327 | +0.378 | +0.408 |
| 6 | kw_mult_nb_log_pctl_hl | 3253 | 0.802 | higher->bigger | 0.302 | +0.350 | +0.359 |
| 7 | kw_mult_nb_pctl_hl | 3253 | 0.802 | higher->bigger | 0.302 | +0.350 | +0.360 |
| 8 | kw_mult_nb_log_hl | 3253 | 0.802 | higher->bigger | 0.302 | +0.349 | +0.381 |
| 9 | kw_mult_nb_hl | 3253 | 0.802 | higher->bigger | 0.302 | +0.350 | +0.382 |
| 10 | kw_add_first_hl | 3253 | 0.794 | higher->bigger | 0.294 | +0.345 | +0.361 |
| 11 | kw_add_first_pctl_hl | 3253 | 0.794 | higher->bigger | 0.294 | +0.345 | +0.351 |
| 12 | kw_add_log_hl | 3253 | 0.793 | higher->bigger | 0.293 | +0.343 | +0.358 |
| 13 | kw_add_log_pctl_hl | 3253 | 0.793 | higher->bigger | 0.293 | +0.343 | +0.349 |
| 14 | kw_add_lin_pctl_hl | 3253 | 0.791 | higher->bigger | 0.291 | +0.341 | +0.347 |
| 15 | kw_add_lin_hl | 3253 | 0.791 | higher->bigger | 0.291 | +0.341 | +0.355 |
| 16 | kw_add_first | 3253 | 0.778 | higher->bigger | 0.278 | +0.341 | +0.358 |
| 17 | kw_add_first_pctl | 3253 | 0.778 | higher->bigger | 0.278 | +0.341 | +0.346 |
| 18 | kw_mult_nb_pctl | 3253 | 0.774 | higher->bigger | 0.274 | +0.352 | +0.353 |
| 19 | kw_mult_nb | 3253 | 0.774 | higher->bigger | 0.274 | +0.352 | +0.314 |
| 20 | kw_mult_nb_log_pctl | 3253 | 0.774 | higher->bigger | 0.274 | +0.350 | +0.351 |
| 21 | kw_add_log | 3253 | 0.773 | higher->bigger | 0.273 | +0.334 | +0.348 |
| 22 | kw_add_log_pctl | 3253 | 0.773 | higher->bigger | 0.273 | +0.334 | +0.338 |
| 23 | kw_mult_nb_log | 3253 | 0.771 | higher->bigger | 0.271 | +0.348 | +0.299 |
| 24 | kw_add_lin_pctl | 3253 | 0.751 | higher->bigger | 0.251 | +0.305 | +0.307 |
| 25 | kw_add_lin | 3253 | 0.751 | higher->bigger | 0.251 | +0.305 | +0.311 |
| 26 | nocoref_recommended | 913 | 0.709 | higher->bigger | 0.209 | +0.119 | +0.138 |
| 27 | Float | 3253 | 0.323 | lower->bigger | 0.177 | -0.176 | -0.155 |
| 28 | Headline-RBscore | 2675 | 0.661 | higher->bigger | 0.161 | +0.191 | +0.204 |
| 29 | n_kw_hits | 3253 | 0.638 | higher->bigger | 0.138 | +0.187 | +0.183 |
| 30 | MktCap | 675 | 0.369 | lower->bigger | 0.131 | -0.208 | -0.079 |
| 31 | FinMarBa_indecisive | 3253 | 0.369 | lower->bigger | 0.131 | -0.158 | -0.160 |
| 32 | LastDailyClose | 3251 | 0.369 | lower->bigger | 0.131 | -0.184 | -0.081 |
| 33 | positive | 3177 | 0.610 | higher->bigger | 0.110 | +0.151 | +0.154 |
| 34 | nocoref_confidence_weighted | 3147 | 0.610 | higher->bigger | 0.110 | +0.145 | +0.153 |
| 35 | nocoref_net_score | 3147 | 0.610 | higher->bigger | 0.110 | +0.140 | +0.158 |
| 36 | FLANG-BERT_positive | 3253 | 0.610 | higher->bigger | 0.110 | +0.131 | +0.144 |
| 37 | FLANG-BERT_sentiment_score | 3253 | 0.609 | higher->bigger | 0.109 | +0.135 | +0.130 |
| 38 | FinMarBa_positive | 3253 | 0.608 | higher->bigger | 0.108 | +0.129 | +0.136 |
| 39 | FLANG-ELECTRA_sentiment_score | 3253 | 0.607 | higher->bigger | 0.107 | +0.140 | +0.134 |
| 40 | Trading-Hero_sentiment_score | 3253 | 0.607 | higher->bigger | 0.107 | +0.159 | +0.150 |
| 41 | DeBERTa_positive | 3253 | 0.607 | higher->bigger | 0.107 | +0.145 | +0.140 |
| 42 | FLANG-ELECTRA_positive | 3253 | 0.606 | higher->bigger | 0.106 | +0.124 | +0.129 |
| 43 | sentiment_score | 3253 | 0.605 | higher->bigger | 0.105 | +0.149 | +0.150 |
| 44 | nocoref_neutral_filter | 3147 | 0.604 | higher->bigger | 0.104 | +0.142 | +0.153 |
| 45 | ModernBERT_positive | 3253 | 0.604 | higher->bigger | 0.104 | +0.134 | +0.143 |
| 46 | FLANG-BERT_neutral | 3253 | 0.397 | lower->bigger | 0.103 | -0.106 | -0.101 |
| 47 | DeBERTa_sentiment_score | 3253 | 0.603 | higher->bigger | 0.103 | +0.146 | +0.142 |
| 48 | n_kw_hits_hl | 3253 | 0.603 | higher->bigger | 0.103 | +0.114 | +0.115 |
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
| 60 | Trading-Hero_Body_sentiment_score | 3253 | 0.595 | higher->bigger | 0.095 | +0.135 | +0.120 |
| 61 | neutral | 3253 | 0.406 | lower->bigger | 0.094 | -0.107 | -0.114 |
| 62 | finbert-tone_sentiment_score | 3253 | 0.590 | higher->bigger | 0.090 | +0.133 | +0.113 |
| 63 | distilroberta_positive | 3253 | 0.589 | higher->bigger | 0.089 | +0.131 | +0.115 |
| 64 | finbert-tone_positive | 3253 | 0.586 | higher->bigger | 0.086 | +0.123 | +0.104 |
| 65 | Trading-Hero_Body_positive | 3253 | 0.584 | higher->bigger | 0.084 | +0.104 | +0.102 |
| 66 | FinMarBa_negative | 3253 | 0.418 | lower->bigger | 0.082 | -0.100 | -0.109 |
| 67 | Trading-Hero_neutral | 3253 | 0.418 | lower->bigger | 0.082 | -0.132 | -0.125 |
| 68 | FLANG-BERT_Body_negative | 3253 | 0.419 | lower->bigger | 0.081 | -0.110 | -0.063 |
| 69 | FinMarBa_Body_indecisive | 3253 | 0.419 | lower->bigger | 0.081 | -0.098 | -0.110 |
| 70 | FinancialBERT_Body_sentiment_score | 3253 | 0.576 | higher->bigger | 0.076 | +0.121 | +0.134 |
| 71 | FinancialBERT_Body_positive | 3253 | 0.574 | higher->bigger | 0.074 | +0.118 | +0.128 |
| 72 | finbert-tone_neutral | 3253 | 0.427 | lower->bigger | 0.073 | -0.102 | -0.087 |
| 73 | distilroberta_neutral | 3253 | 0.430 | lower->bigger | 0.070 | -0.105 | -0.107 |
| 74 | Trading-Hero_Body_negative | 3253 | 0.432 | lower->bigger | 0.068 | -0.090 | -0.106 |
| 75 | FinancialBERT_Body_neutral | 3253 | 0.432 | lower->bigger | 0.068 | -0.108 | -0.117 |
| 76 | RoBERTa_sentiment_score | 3253 | 0.567 | higher->bigger | 0.067 | +0.077 | +0.080 |
| 77 | FLANG-ELECTRA_negative | 3253 | 0.567 | higher->bigger | 0.067 | +0.059 | -0.058 |
| 78 | FinancialBERT_Body_negative | 3253 | 0.433 | lower->bigger | 0.067 | -0.094 | -0.061 |
| 79 | Trading-Hero_Body_neutral | 3253 | 0.434 | lower->bigger | 0.066 | -0.073 | -0.077 |
| 80 | RoBERTa_positive | 3253 | 0.565 | higher->bigger | 0.065 | +0.076 | +0.069 |
| 81 | LouMcD-SDict_Body_negative | 3253 | 0.438 | lower->bigger | 0.062 | -0.070 | -0.089 |
| 82 | FLANG-ELECTRA_Body_positive | 3253 | 0.560 | higher->bigger | 0.060 | +0.101 | +0.084 |
| 83 | tradeSizeBaseline | 1126 | 0.441 | lower->bigger | 0.059 | -0.062 | -0.035 |
| 84 | FLANG-BERT_Body_sentiment_score | 3253 | 0.559 | higher->bigger | 0.059 | +0.100 | +0.097 |
| 85 | FLANG-BERT_negative | 3253 | 0.441 | lower->bigger | 0.059 | -0.078 | -0.065 |
| 86 | distilroberta_Body_sentiment_score | 3253 | 0.559 | higher->bigger | 0.059 | +0.112 | +0.114 |
| 87 | ModernBERT_Body_positive | 3253 | 0.558 | higher->bigger | 0.058 | +0.080 | +0.086 |
| 88 | distilroberta_Body_positive | 3253 | 0.557 | higher->bigger | 0.057 | +0.102 | +0.100 |
| 89 | LouMcD-SDict_Body_sentiment_score | 3253 | 0.554 | higher->bigger | 0.054 | +0.062 | +0.072 |
| 90 | FLANG-BERT_Body_positive | 3253 | 0.554 | higher->bigger | 0.054 | +0.092 | +0.087 |
| 91 | ModernBERT_Body_sentiment_score | 3253 | 0.553 | higher->bigger | 0.053 | +0.078 | +0.091 |
| 92 | RoBERTa_neutral | 3253 | 0.450 | lower->bigger | 0.050 | -0.052 | -0.050 |
| 93 | FLANG-ELECTRA_Body_sentiment_score | 3253 | 0.549 | higher->bigger | 0.049 | +0.089 | +0.106 |
| 94 | FLANG-ELECTRA_Body_neutral | 3253 | 0.453 | lower->bigger | 0.047 | -0.069 | -0.037 |
| 95 | RoBERTa_negative | 3253 | 0.453 | lower->bigger | 0.047 | -0.032 | -0.068 |
| 96 | distilroberta_negative | 3253 | 0.543 | higher->bigger | 0.043 | +0.073 | -0.032 |
| 97 | distilroberta_Body_neutral | 3253 | 0.458 | lower->bigger | 0.042 | -0.077 | -0.072 |
| 98 | FLANG-BERT_Body_neutral | 3253 | 0.459 | lower->bigger | 0.041 | -0.077 | -0.071 |
| 99 | DeBERTa_Body_sentiment_score | 3253 | 0.540 | higher->bigger | 0.040 | +0.071 | +0.091 |
| 100 | DeBERTa_Body_positive | 3253 | 0.540 | higher->bigger | 0.040 | +0.071 | +0.068 |
| 101 | ModernBERT_Body_neutral | 3253 | 0.462 | lower->bigger | 0.038 | -0.048 | -0.040 |
| 102 | itiBaseline | 1126 | 0.462 | lower->bigger | 0.038 | -0.052 | -0.044 |
| 103 | LouMcD-SDict_Body_weak_modal | 3253 | 0.463 | lower->bigger | 0.037 | -0.044 | -0.042 |
| 104 | ModernBERT_negative | 3253 | 0.463 | lower->bigger | 0.037 | -0.033 | -0.040 |
| 105 | finbert-tone_Body_sentiment_score | 3253 | 0.537 | higher->bigger | 0.037 | +0.083 | +0.075 |
| 106 | DeBERTa_negative | 3253 | 0.535 | higher->bigger | 0.035 | -0.006 | -0.053 |
| 107 | FinMarBa_Body_positive | 3253 | 0.534 | higher->bigger | 0.034 | +0.059 | +0.074 |
| 108 | LouMcD-SDict_sentiment_score | 3253 | 0.534 | higher->bigger | 0.034 | +0.066 | +0.070 |
| 109 | distilroberta_Body_negative | 3253 | 0.531 | higher->bigger | 0.031 | +0.003 | -0.084 |
| 110 | ModernBERT_Body_negative | 3253 | 0.469 | lower->bigger | 0.031 | -0.062 | -0.085 |
| 111 | finbert-tone_negative | 3253 | 0.471 | lower->bigger | 0.029 | -0.044 | -0.060 |
| 112 | finbert-tone_Body_positive | 3253 | 0.529 | higher->bigger | 0.029 | +0.064 | +0.060 |
| 113 | FinMarBa_Body_sentiment_score | 3253 | 0.524 | higher->bigger | 0.024 | +0.049 | +0.063 |
| 114 | LouMcD-SDict_positive | 3253 | 0.523 | higher->bigger | 0.023 | +0.038 | +0.046 |
| 115 | finbert-tone_Body_negative | 3253 | 0.477 | lower->bigger | 0.023 | -0.075 | -0.074 |
| 116 | LouMcD-SDict_Body_strong_modal | 3253 | 0.478 | lower->bigger | 0.022 | -0.017 | -0.034 |
| 117 | LouMcD-SDict_negative | 3253 | 0.482 | lower->bigger | 0.018 | -0.064 | -0.073 |
| 118 | DeBERTa_Body_negative | 3253 | 0.482 | lower->bigger | 0.018 | -0.051 | -0.095 |
| 119 | FinancialBERT_negative | 3253 | 0.482 | lower->bigger | 0.018 | -0.013 | -0.005 |
| 120 | RoBERTa_Body_negative | 3253 | 0.517 | higher->bigger | 0.017 | +0.008 | -0.048 |
| 121 | finbert-tone_Body_neutral | 3253 | 0.484 | lower->bigger | 0.016 | -0.043 | -0.039 |
| 122 | LouMcD-SDict_uncertainty | 3253 | 0.486 | lower->bigger | 0.014 | -0.025 | -0.032 |
| 123 | LouMcD-SDict_litigious | 3253 | 0.486 | lower->bigger | 0.014 | -0.014 | -0.015 |
| 124 | FLANG-ELECTRA_Body_negative | 3253 | 0.512 | higher->bigger | 0.012 | +0.017 | -0.101 |
| 125 | LouMcD-SDict_Body_litigious | 3253 | 0.489 | lower->bigger | 0.011 | +0.008 | -0.023 |
| 126 | FinancialBERT_neutral | 3253 | 0.510 | higher->bigger | 0.010 | +0.013 | -0.010 |
| 127 | LouMcD-SDict_weak_modal | 3253 | 0.490 | lower->bigger | 0.010 | -0.050 | -0.054 |
| 128 | RoBERTa_Body_neutral | 3253 | 0.509 | higher->bigger | 0.009 | -0.012 | -0.000 |
| 129 | FinMarBa_Body_negative | 3253 | 0.491 | lower->bigger | 0.009 | -0.034 | -0.045 |
| 130 | FinancialBERT_sentiment_score | 3253 | 0.505 | higher->bigger | 0.005 | -0.002 | +0.013 |
| 131 | DeBERTa_Body_neutral | 3253 | 0.496 | lower->bigger | 0.004 | -0.017 | -0.013 |
| 132 | LouMcD-SDict_constraining | 3253 | 0.504 | higher->bigger | 0.004 | +0.027 | +0.026 |
| 133 | RoBERTa_Body_positive | 3253 | 0.498 | lower->bigger | 0.002 | +0.021 | +0.010 |
| 134 | RoBERTa_Body_sentiment_score | 3253 | 0.498 | lower->bigger | 0.002 | +0.021 | +0.019 |
| 135 | LouMcD-SDict_Body_uncertainty | 3253 | 0.499 | lower->bigger | 0.001 | +0.010 | +0.011 |
| 136 | LouMcD-SDict_strong_modal | 3253 | 0.501 | higher->bigger | 0.001 | +0.023 | +0.025 |
| 137 | LouMcD-SDict_Body_constraining | 3253 | 0.499 | lower->bigger | 0.001 | +0.027 | +0.024 |
| 138 | LouMcD-SDict_Body_positive | 3253 | 0.499 | lower->bigger | 0.001 | +0.007 | +0.011 |
| 139 | FinancialBERT_positive | 3253 | 0.500 | higher->bigger | 0.000 | -0.006 | +0.012 |

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
| 9 | Author | 3253 | 0.545 | 0.045 |
| 10 | Trading-Hero_Body_label | 3253 | 0.542 | 0.042 |
| 11 | boilerplate_category | 3253 | 0.541 | 0.041 |
| 12 | label | 3253 | 0.533 | 0.033 |
| 13 | FinancialBERT_Body_label | 3253 | 0.530 | 0.030 |
| 14 | RoBERTa_Body_label | 3253 | 0.472 | 0.028 |
| 15 | FinMarBa_Body_label | 3253 | 0.473 | 0.027 |
| 16 | distilroberta_Body_label | 3253 | 0.525 | 0.025 |
| 17 | RoBERTa_label | 3253 | 0.478 | 0.022 |
| 18 | distilroberta_label | 3253 | 0.521 | 0.021 |
| 19 | finbert-tone_Body_label | 3253 | 0.481 | 0.019 |
| 20 | FLANG-ELECTRA_Body_label | 3253 | 0.518 | 0.018 |
| 21 | FLANG-BERT_Body_label | 3253 | 0.516 | 0.016 |
| 22 | finbert-tone_label | 3253 | 0.516 | 0.016 |
| 23 | FinMarBa_label | 3253 | 0.484 | 0.016 |
| 24 | DeBERTa_Body_label | 3253 | 0.514 | 0.014 |
| 25 | ModernBERT_Body_label | 3253 | 0.511 | 0.011 |
| 26 | LouMcD-SDict_label | 3253 | 0.506 | 0.006 |
| 27 | Exchange | 3253 | 0.505 | 0.005 |
| 28 | LouMcD-SDict_Body_label | 3253 | 0.499 | 0.001 |

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

### `Author` (CV-AUC 0.545)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| PR Newswire | 509 | 11.4% | +2.6% | 5.05 |
| Newsfile Corp | 129 | 10.1% | +1.3% | 5.80 |
| Globe Newswire | 1764 | 9.6% | +0.8% | 5.77 |
| ACCESSWIRE | 336 | 7.7% | -1.1% | 5.24 |
| Business Wire | 515 | 4.1% | -4.7% | 3.66 |

### `Trading-Hero_Body_label` (CV-AUC 0.542)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1005 | 11.9% | +3.1% | 6.24 |
| neutral | 2161 | 7.7% | -1.1% | 4.83 |
| negative | 87 | 0.0% | -8.8% | 2.50 |

### `boilerplate_category` (CV-AUC 0.541)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| compliance | 158 | 13.3% | +4.5% | 8.51 |
| <missing> | 1816 | 10.0% | +1.1% | 5.25 |
| lawfirm | 473 | 8.7% | -0.2% | 5.46 |
| conference | 252 | 6.3% | -2.5% | 4.27 |
| earnings | 554 | 5.1% | -3.8% | 4.45 |

### `label` (CV-AUC 0.533)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2415 | 10.1% | +1.3% | 5.53 |
| negative | 99 | 5.1% | -3.8% | 3.05 |
| neutral | 663 | 5.0% | -3.8% | 4.32 |
| <other> | 63 | 4.8% | -4.1% | 4.08 |

### `FinancialBERT_Body_label` (CV-AUC 0.530)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2102 | 10.4% | +1.5% | 5.80 |
| neutral | 1116 | 6.2% | -2.6% | 4.24 |
| negative | 35 | 0.0% | -8.8% | 2.33 |

### `RoBERTa_Body_label` (CV-AUC 0.472)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 2735 | 9.0% | +0.2% | 5.24 |
| positive | 512 | 7.8% | -1.0% | 4.89 |

### `FinMarBa_Body_label` (CV-AUC 0.473)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 3008 | 9.0% | +0.2% | 5.24 |
| negative | 241 | 6.6% | -2.2% | 4.29 |

### `distilroberta_Body_label` (CV-AUC 0.525)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2002 | 10.1% | +1.3% | 5.63 |
| neutral | 1154 | 7.0% | -1.8% | 4.67 |
| negative | 97 | 3.1% | -5.7% | 2.50 |

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

### `finbert-tone_Body_label` (CV-AUC 0.481)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1409 | 9.4% | +0.5% | 5.73 |
| neutral | 1780 | 8.6% | -0.2% | 4.87 |
| negative | 64 | 3.1% | -5.7% | 2.77 |

### `FLANG-ELECTRA_Body_label` (CV-AUC 0.518)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2153 | 9.8% | +1.0% | 5.55 |
| neutral | 914 | 7.7% | -1.2% | 4.89 |
| negative | 186 | 2.7% | -6.1% | 3.12 |

### `FLANG-BERT_Body_label` (CV-AUC 0.516)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 1374 | 10.3% | +1.5% | 5.78 |
| neutral | 1839 | 7.9% | -0.9% | 4.84 |
| negative | 40 | 0.0% | -8.8% | 3.29 |

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

### `DeBERTa_Body_label` (CV-AUC 0.514)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2065 | 9.8% | +1.0% | 5.35 |
| neutral | 941 | 8.1% | -0.7% | 5.24 |
| negative | 247 | 3.6% | -5.2% | 3.66 |

### `ModernBERT_Body_label` (CV-AUC 0.511)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2589 | 9.7% | +0.9% | 5.37 |
| neutral | 368 | 7.1% | -1.8% | 5.08 |
| negative | 296 | 3.4% | -5.4% | 3.83 |

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

### `LouMcD-SDict_Body_label` (CV-AUC 0.499)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 2151 | 9.7% | +0.9% | 5.41 |
| neutral | 464 | 9.3% | +0.4% | 5.21 |
| negative | 638 | 5.5% | -3.3% | 4.39 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: model_proba_PLUS_kw_mult_nb, model_proba_pctl_hl, kw_mult_nb_log_pctl_hl, kw_add_first_hl, kw_add_first, kw_mult_nb_pctl, kw_add_lin_pctl, nocoref_recommended, Float, Headline-RBscore, n_kw_hits, MktCap, FinMarBa_indecisive, LastDailyClose, positive

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): model_proba_pctl ~ model_proba_PLUS_kw_mult_nb; model_proba ~ model_proba_PLUS_kw_mult_nb; model_proba_hl ~ model_proba_pctl_hl; kw_mult_nb_pctl_hl ~ kw_mult_nb_log_pctl_hl; kw_mult_nb_log_hl ~ kw_mult_nb_log_pctl_hl; kw_mult_nb_hl ~ kw_mult_nb_log_pctl_hl; kw_add_first_pctl_hl ~ kw_add_first_hl; kw_add_log_hl ~ kw_add_first_hl; kw_add_log_pctl_hl ~ kw_add_first_hl; kw_add_lin_pctl_hl ~ kw_add_first_hl; kw_add_lin_hl ~ kw_add_first_hl; kw_add_first_pctl ~ kw_add_first; kw_mult_nb ~ kw_mult_nb_pctl; kw_mult_nb_log_pctl ~ kw_mult_nb_pctl; kw_add_log ~ kw_add_first; kw_add_log_pctl ~ kw_add_first; kw_mult_nb_log ~ kw_mult_nb_pctl; kw_add_lin ~ kw_add_lin_pctl

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Headline-RBscore | 2675 | 0.895 | model_proba_PLUS_kw_mult_nb=+0.90, model_proba_pctl_hl=+0.97, Headline-RBscore=-0.28 |
| 2 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + nocoref_recommended | 913 | 0.893 | model_proba_PLUS_kw_mult_nb=+0.83, kw_mult_nb_log_pctl_hl=+0.58, nocoref_recommended=+0.48 |
| 3 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + Headline-RBscore | 2675 | 0.892 | model_proba_PLUS_kw_mult_nb=+0.96, kw_mult_nb_log_pctl_hl=+0.78, Headline-RBscore=-0.24 |
| 4 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + positive | 2599 | 0.890 | model_proba_PLUS_kw_mult_nb=+1.26, Headline-RBscore=-0.20, positive=+0.29 |
| 5 | model_proba_PLUS_kw_mult_nb + Float + Headline-RBscore | 2675 | 0.889 | model_proba_PLUS_kw_mult_nb=+1.23, Float=-0.31, Headline-RBscore=-0.16 |
| 6 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + FinMarBa_indecisive | 2675 | 0.888 | model_proba_PLUS_kw_mult_nb=+1.25, Headline-RBscore=-0.20, FinMarBa_indecisive=-0.29 |
| 7 | model_proba_PLUS_kw_mult_nb + kw_add_first_hl + Headline-RBscore | 2675 | 0.887 | model_proba_PLUS_kw_mult_nb=+1.07, kw_add_first_hl=+0.50, Headline-RBscore=-0.29 |
| 8 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + nocoref_recommended | 913 | 0.886 | model_proba_PLUS_kw_mult_nb=+0.78, model_proba_pctl_hl=+0.69, nocoref_recommended=+0.51 |
| 9 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + Headline-RBscore | 913 | 0.886 | model_proba_PLUS_kw_mult_nb=+1.16, nocoref_recommended=+0.64, Headline-RBscore=-0.27 |
| 10 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + positive | 838 | 0.885 | model_proba_PLUS_kw_mult_nb=+0.99, nocoref_recommended=+0.37, positive=+0.28 |
| 11 | model_proba_PLUS_kw_mult_nb + Headline-RBscore | 2675 | 0.884 | model_proba_PLUS_kw_mult_nb=+1.27, Headline-RBscore=-0.14 |
| 12 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + n_kw_hits | 2675 | 0.883 | model_proba_PLUS_kw_mult_nb=+1.40, Headline-RBscore=-0.09, n_kw_hits=-0.33 |
| 13 | model_proba_PLUS_kw_mult_nb + kw_add_lin_pctl + Headline-RBscore | 2675 | 0.883 | model_proba_PLUS_kw_mult_nb=+1.28, kw_add_lin_pctl=-0.03, Headline-RBscore=-0.14 |
| 14 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_pctl + Headline-RBscore | 2675 | 0.882 | model_proba_PLUS_kw_mult_nb=+1.29, kw_mult_nb_pctl=-0.05, Headline-RBscore=-0.14 |
| 15 | model_proba_PLUS_kw_mult_nb + kw_add_first + Headline-RBscore | 2675 | 0.881 | model_proba_PLUS_kw_mult_nb=+1.33, kw_add_first=-0.12, Headline-RBscore=-0.12 |
| 16 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + LastDailyClose | 2673 | 0.880 | model_proba_PLUS_kw_mult_nb=+1.26, Headline-RBscore=-0.14, LastDailyClose=-0.06 |
| 17 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + n_kw_hits | 913 | 0.878 | model_proba_PLUS_kw_mult_nb=+1.18, nocoref_recommended=+0.56, n_kw_hits=-0.39 |
| 18 | model_proba_PLUS_kw_mult_nb + nocoref_recommended | 913 | 0.875 | model_proba_PLUS_kw_mult_nb=+1.05, nocoref_recommended=+0.55 |
| 19 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + FinMarBa_indecisive | 913 | 0.875 | model_proba_PLUS_kw_mult_nb=+1.02, nocoref_recommended=+0.50, FinMarBa_indecisive=-0.18 |
| 20 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + LastDailyClose | 913 | 0.874 | model_proba_PLUS_kw_mult_nb=+1.05, nocoref_recommended=+0.55, LastDailyClose=+0.07 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Headline-RBscore | 3 | 0.895 |
| 2 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + nocoref_recommended | 3 | 0.893 |
| 3 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_log_pctl_hl + Headline-RBscore | 3 | 0.892 |
| 4 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + positive | 3 | 0.890 |
| 5 | model_proba_PLUS_kw_mult_nb + Float + Headline-RBscore | 3 | 0.889 |
| 6 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + FinMarBa_indecisive | 3 | 0.888 |
| 7 | model_proba_PLUS_kw_mult_nb + kw_add_first_hl + Headline-RBscore | 3 | 0.887 |
| 8 | model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + nocoref_recommended | 3 | 0.886 |
| 9 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + Headline-RBscore | 3 | 0.886 |
| 10 | model_proba_PLUS_kw_mult_nb + nocoref_recommended + positive | 3 | 0.885 |
| 11 | model_proba_PLUS_kw_mult_nb + Headline-RBscore | 2 | 0.884 |
| 12 | model_proba_PLUS_kw_mult_nb + Headline-RBscore + n_kw_hits | 3 | 0.883 |
| 13 | model_proba_PLUS_kw_mult_nb + kw_add_lin_pctl + Headline-RBscore | 3 | 0.883 |
| 14 | model_proba_PLUS_kw_mult_nb + kw_mult_nb_pctl + Headline-RBscore | 3 | 0.882 |
| 15 | model_proba_PLUS_kw_mult_nb + kw_add_first + Headline-RBscore | 3 | 0.881 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | model_proba_ | model_proba_ | kw_mult_nb_l | kw_add_first | kw_add_first | kw_mult_nb_p | kw_add_lin_p | nocoref_reco | Float | Headline-RBs | n_kw_hits | MktCap | FinMarBa_ind | LastDailyClo | positive |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| model_proba_PLUS | 1.00 | 0.70 | 0.63 | 0.66 | 0.78 | 0.79 | 0.73 | 0.26 | -0.26 | 0.41 | 0.48 | -0.21 | -0.30 | -0.32 | 0.25 |
| model_proba_pctl | 0.70 | 1.00 | 0.79 | 0.94 | 0.58 | 0.60 | 0.55 | 0.20 | -0.22 | 0.46 | 0.32 | -0.18 | -0.31 | -0.24 | 0.27 |
| kw_mult_nb_log_p | 0.63 | 0.79 | 1.00 | 0.70 | 0.51 | 0.53 | 0.46 | 0.25 | -0.20 | 0.33 | 0.27 | -0.18 | -0.26 | -0.23 | 0.18 |
| kw_add_first_hl | 0.66 | 0.94 | 0.70 | 1.00 | 0.57 | 0.58 | 0.54 | 0.20 | -0.21 | 0.50 | 0.30 | -0.18 | -0.32 | -0.21 | 0.28 |
| kw_add_first | 0.78 | 0.58 | 0.51 | 0.57 | 1.00 | 0.87 | 0.83 | 0.28 | -0.21 | 0.41 | 0.37 | -0.17 | -0.31 | -0.30 | 0.25 |
| kw_mult_nb_pctl | 0.79 | 0.60 | 0.53 | 0.58 | 0.87 | 1.00 | 0.75 | 0.23 | -0.22 | 0.40 | 0.50 | -0.19 | -0.27 | -0.29 | 0.18 |
| kw_add_lin_pctl | 0.73 | 0.55 | 0.46 | 0.54 | 0.83 | 0.75 | 1.00 | 0.19 | -0.16 | 0.36 | 0.28 | -0.17 | -0.21 | -0.21 | 0.19 |
| nocoref_recommen | 0.26 | 0.20 | 0.25 | 0.20 | 0.28 | 0.23 | 0.19 | 1.00 | -0.08 | 0.28 | 0.06 | — | -0.36 | -0.11 | 0.41 |
| Float | -0.26 | -0.22 | -0.20 | -0.21 | -0.21 | -0.22 | -0.16 | -0.08 | 1.00 | -0.12 | -0.19 | 0.57 | 0.08 | 0.06 | -0.06 |
| Headline-RBscore | 0.41 | 0.46 | 0.33 | 0.50 | 0.41 | 0.40 | 0.36 | 0.28 | -0.12 | 1.00 | 0.23 | -0.09 | -0.37 | -0.15 | 0.39 |
| n_kw_hits | 0.48 | 0.32 | 0.27 | 0.30 | 0.37 | 0.50 | 0.28 | 0.06 | -0.19 | 0.23 | 1.00 | -0.06 | -0.12 | -0.24 | 0.05 |
| MktCap | -0.21 | -0.18 | -0.18 | -0.18 | -0.17 | -0.19 | -0.17 | — | 0.57 | -0.09 | -0.06 | 1.00 | 0.08 | 0.25 | -0.06 |
| FinMarBa_indecis | -0.30 | -0.31 | -0.26 | -0.32 | -0.31 | -0.27 | -0.21 | -0.36 | 0.08 | -0.37 | -0.12 | 0.08 | 1.00 | 0.18 | -0.55 |
| LastDailyClose | -0.32 | -0.24 | -0.23 | -0.21 | -0.30 | -0.29 | -0.21 | -0.11 | 0.06 | -0.15 | -0.24 | 0.25 | 0.18 | 1.00 | -0.16 |
| positive | 0.25 | 0.27 | 0.18 | 0.28 | 0.25 | 0.18 | 0.19 | 0.41 | -0.06 | 0.39 | 0.05 | -0.06 | -0.55 | -0.16 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| model_proba_PLUS_kw_mult_nb | 0.782 | 0.875 | 0.867 | 0.857 | 0.843 |
| model_proba_pctl_hl | 0.739 | 0.821 | 0.816 | 0.827 | 0.803 |
| kw_mult_nb_log_pctl_hl | 0.722 | 0.801 | 0.797 | 0.802 | 0.778 |
| kw_add_first_hl | 0.717 | 0.788 | 0.785 | 0.794 | 0.774 |
| kw_add_first | 0.705 | 0.769 | 0.766 | 0.778 | 0.782 |
| kw_mult_nb_pctl | 0.711 | 0.775 | 0.771 | 0.774 | 0.780 |
| kw_add_lin_pctl | 0.690 | 0.740 | 0.739 | 0.751 | 0.751 |
| nocoref_recommended | 0.551 | 0.592 | 0.618 | 0.709 | 0.698 |
| Float | 0.396 | 0.381 | 0.359 | 0.323 | 0.288 |
| Headline-RBscore | 0.610 | 0.640 | 0.645 | 0.661 | 0.660 |
| n_kw_hits | 0.610 | 0.629 | 0.626 | 0.638 | 0.641 |
| MktCap | 0.389 | 0.387 | 0.364 | 0.369 | 0.358 |
| FinMarBa_indecisive | 0.413 | 0.397 | 0.400 | 0.369 | 0.367 |
| LastDailyClose | 0.405 | 0.396 | 0.386 | 0.369 | 0.350 |
| positive | 0.577 | 0.592 | 0.584 | 0.610 | 0.607 |
| _n big movers_ | 989 | 649 | 485 | 287 | 169 |

## Conclusion

- **Best numeric single:** `model_proba_PLUS_kw_mult_nb` (AUC 0.857, higher->bigger).
- **Best categorical:** `FineClass` (CV-AUC 0.617).
- **Best combo:** `model_proba_PLUS_kw_mult_nb + model_proba_pctl_hl + Headline-RBscore` (CV-AUC 0.895; lift over best single: +0.038).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

