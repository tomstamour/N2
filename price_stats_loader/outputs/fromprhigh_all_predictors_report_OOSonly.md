# fromPR_High(%) all-column predictor analysis — PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv

- **SUBSET RUN**: only the 578 rows with `ArrivalDate` >= 2026-06-20 — the keyword model's out-of-sample block.
- Rows: 3306 total -> dropped 53 null-target, 0 outliers (> 500%), 0 duplicate PRs -> **578 kept**
- fromPR_High(%) on kept rows: median=7.01  mean=16.94  max=371.00
- Big-mover threshold: **fromPR_High(%) >= 30** -> **74** big movers (12.8%)
- Candidates: 137 numeric (auto-detected), 28 categorical
- Excluded (outcome/leakage/ops/id): ArrivalDate, ArrivalTime, Body, BodyCompletedAt, ClerkArm, ClerkSentiment, Created, CurlTime, DailyHigh($), DailyHigh(%), DeBERTa_Body_elapsed_ms, DeBERTa_elapsed_ms, FLANG-BERT_Body_elapsed_ms, FLANG-BERT_elapsed_ms, FLANG-ELECTRA_Body_elapsed_ms, FLANG-ELECTRA_elapsed_ms, FinBERTCompletedAt, FinMarBa_Body_elapsed_ms, FinMarBa_elapsed_ms, FinancialBERT_Body_elapsed_ms, FinancialBERT_elapsed_ms, Headline, ID, KwScoreCompletedAt, LouMcD-SDict_Body_elapsed_ms, LouMcD-SDict_elapsed_ms, ModernBERT_Body_elapsed_ms, ModernBERT_elapsed_ms, NoCorefCompletedAt, OrchTime, RoBERTa_Body_elapsed_ms, RoBERTa_elapsed_ms, Sentiment, Symbol, Tickers, Trades/sec, Trading-Hero_Body_elapsed_ms, Trading-Hero_elapsed_ms, Trigger, body_duration_ms, body_sentiment, distilroberta_Body_elapsed_ms, distilroberta_elapsed_ms, finbert-tone_Body_elapsed_ms, finbert-tone_elapsed_ms, fromPR_High(%), fromPR_HighTime(m:s), fromPR_status
- Metrics: numeric = Mann-Whitney rank AUC (0.50 = no signal); categorical & combos = pooled out-of-fold logistic AUC (5-fold). Spearman is vs the raw target, Pearson vs sign(t)*log1p(|t|).

## Numeric singles — every auto-detected column

| rank | column | n | AUC | direction | |AUC-0.5| | Spearman | Pearson(slog) |
|---|---|---|---|---|---|---|---|
| 1 | model_proba_pctl_hl | 578 | 0.672 | higher->bigger | 0.172 | +0.203 | +0.204 |
| 2 | model_proba_hl | 578 | 0.672 | higher->bigger | 0.172 | +0.203 | +0.228 |
| 3 | kw_mult_nb_log_pctl | 578 | 0.671 | higher->bigger | 0.171 | +0.262 | +0.248 |
| 4 | kw_add_first_hl | 578 | 0.670 | higher->bigger | 0.170 | +0.211 | +0.234 |
| 5 | kw_add_first_pctl_hl | 578 | 0.670 | higher->bigger | 0.170 | +0.211 | +0.212 |
| 6 | model_proba_pctl | 578 | 0.669 | higher->bigger | 0.169 | +0.253 | +0.244 |
| 7 | model_proba | 578 | 0.669 | higher->bigger | 0.169 | +0.253 | +0.265 |
| 8 | kw_mult_nb_log | 578 | 0.669 | higher->bigger | 0.169 | +0.254 | +0.227 |
| 9 | kw_mult_nb_pctl | 578 | 0.667 | higher->bigger | 0.167 | +0.252 | +0.240 |
| 10 | kw_mult_nb | 578 | 0.666 | higher->bigger | 0.166 | +0.252 | +0.210 |
| 11 | kw_add_log | 578 | 0.664 | higher->bigger | 0.164 | +0.215 | +0.226 |
| 12 | kw_add_log_pctl | 578 | 0.664 | higher->bigger | 0.164 | +0.215 | +0.210 |
| 13 | kw_add_log_pctl_hl | 578 | 0.663 | higher->bigger | 0.163 | +0.211 | +0.211 |
| 14 | kw_add_log_hl | 578 | 0.663 | higher->bigger | 0.163 | +0.211 | +0.229 |
| 15 | kw_add_lin_pctl | 578 | 0.661 | higher->bigger | 0.161 | +0.210 | +0.209 |
| 16 | kw_add_lin | 578 | 0.661 | higher->bigger | 0.161 | +0.209 | +0.219 |
| 17 | kw_add_lin_pctl_hl | 578 | 0.659 | higher->bigger | 0.159 | +0.209 | +0.209 |
| 18 | kw_add_lin_hl | 578 | 0.659 | higher->bigger | 0.159 | +0.209 | +0.225 |
| 19 | MktCap | 578 | 0.342 | lower->bigger | 0.158 | -0.232 | -0.201 |
| 20 | model_proba_PLUS_kw_mult_nb | 578 | 0.657 | higher->bigger | 0.157 | +0.230 | +0.252 |
| 21 | Float | 578 | 0.347 | lower->bigger | 0.153 | -0.186 | -0.135 |
| 22 | kw_add_first | 578 | 0.653 | higher->bigger | 0.153 | +0.211 | +0.227 |
| 23 | kw_add_first_pctl | 578 | 0.652 | higher->bigger | 0.152 | +0.211 | +0.208 |
| 24 | kw_mult_nb_pctl_hl | 578 | 0.619 | higher->bigger | 0.119 | +0.169 | +0.155 |
| 25 | kw_mult_nb_hl | 578 | 0.619 | higher->bigger | 0.119 | +0.169 | +0.167 |
| 26 | kw_mult_nb_log_pctl_hl | 578 | 0.617 | higher->bigger | 0.117 | +0.169 | +0.155 |
| 27 | kw_mult_nb_log_hl | 578 | 0.617 | higher->bigger | 0.117 | +0.169 | +0.167 |
| 28 | Trading-Hero_Body_sentiment_score | 578 | 0.603 | higher->bigger | 0.103 | +0.134 | +0.122 |
| 29 | Trading-Hero_Body_positive | 578 | 0.595 | higher->bigger | 0.095 | +0.108 | +0.112 |
| 30 | n_kw_hits_hl | 578 | 0.590 | higher->bigger | 0.090 | +0.137 | +0.160 |
| 31 | Trading-Hero_Body_neutral | 578 | 0.414 | lower->bigger | 0.086 | -0.087 | -0.100 |
| 32 | LouMcD-SDict_Body_negative | 578 | 0.415 | lower->bigger | 0.085 | -0.137 | -0.142 |
| 33 | FinMarBa_indecisive | 578 | 0.419 | lower->bigger | 0.081 | -0.099 | -0.095 |
| 34 | nocoref_neutral_filter | 513 | 0.580 | higher->bigger | 0.080 | +0.139 | +0.124 |
| 35 | nocoref_net_score | 513 | 0.578 | higher->bigger | 0.078 | +0.130 | +0.117 |
| 36 | Trading-Hero_negative | 578 | 0.422 | lower->bigger | 0.078 | -0.091 | -0.065 |
| 37 | FinMarBa_Body_indecisive | 578 | 0.429 | lower->bigger | 0.071 | -0.066 | -0.028 |
| 38 | FLANG-ELECTRA_positive | 578 | 0.570 | higher->bigger | 0.070 | +0.118 | +0.113 |
| 39 | nocoref_top_k | 513 | 0.570 | higher->bigger | 0.070 | +0.118 | +0.100 |
| 40 | nocoref_confidence_weighted | 513 | 0.570 | higher->bigger | 0.070 | +0.138 | +0.120 |
| 41 | FLANG-ELECTRA_neutral | 578 | 0.430 | lower->bigger | 0.070 | -0.099 | -0.081 |
| 42 | nocoref_positional | 513 | 0.570 | higher->bigger | 0.070 | +0.119 | +0.112 |
| 43 | LouMcD-SDict_Body_sentiment_score | 578 | 0.569 | higher->bigger | 0.069 | +0.107 | +0.087 |
| 44 | FLANG-BERT_Body_negative | 578 | 0.431 | lower->bigger | 0.069 | -0.077 | +0.001 |
| 45 | FLANG-ELECTRA_sentiment_score | 578 | 0.568 | higher->bigger | 0.068 | +0.143 | +0.129 |
| 46 | tradeSizeBaseline | 578 | 0.436 | lower->bigger | 0.064 | -0.057 | -0.027 |
| 47 | FLANG-BERT_neutral | 578 | 0.436 | lower->bigger | 0.064 | -0.100 | -0.119 |
| 48 | FLANG-ELECTRA_Body_positive | 578 | 0.564 | higher->bigger | 0.064 | +0.050 | +0.031 |
| 49 | Trading-Hero_Body_negative | 578 | 0.438 | lower->bigger | 0.062 | -0.093 | -0.079 |
| 50 | LouMcD-SDict_Body_constraining | 578 | 0.440 | lower->bigger | 0.060 | -0.051 | -0.048 |
| 51 | FLANG-BERT_positive | 578 | 0.558 | higher->bigger | 0.058 | +0.113 | +0.113 |
| 52 | FLANG-ELECTRA_negative | 578 | 0.557 | higher->bigger | 0.057 | +0.062 | -0.087 |
| 53 | neutral | 578 | 0.445 | lower->bigger | 0.055 | -0.084 | -0.094 |
| 54 | FinMarBa_positive | 578 | 0.554 | higher->bigger | 0.054 | +0.071 | +0.063 |
| 55 | FLANG-ELECTRA_Body_neutral | 578 | 0.447 | lower->bigger | 0.053 | -0.055 | -0.019 |
| 56 | positive | 578 | 0.551 | higher->bigger | 0.051 | +0.096 | +0.105 |
| 57 | distilroberta_Body_neutral | 578 | 0.449 | lower->bigger | 0.051 | -0.061 | -0.075 |
| 58 | distilroberta_Body_positive | 578 | 0.550 | higher->bigger | 0.050 | +0.067 | +0.080 |
| 59 | n_kw_hits | 578 | 0.550 | higher->bigger | 0.050 | +0.155 | +0.170 |
| 60 | DeBERTa_sentiment_score | 578 | 0.548 | higher->bigger | 0.048 | +0.083 | +0.081 |
| 61 | sentiment_score | 578 | 0.547 | higher->bigger | 0.047 | +0.100 | +0.111 |
| 62 | FLANG-BERT_Body_sentiment_score | 578 | 0.547 | higher->bigger | 0.047 | +0.069 | +0.049 |
| 63 | distilroberta_Body_sentiment_score | 578 | 0.546 | higher->bigger | 0.046 | +0.068 | +0.078 |
| 64 | FinMarBa_sentiment_score | 578 | 0.545 | higher->bigger | 0.045 | +0.061 | +0.054 |
| 65 | FLANG-BERT_sentiment_score | 578 | 0.545 | higher->bigger | 0.045 | +0.109 | +0.077 |
| 66 | DeBERTa_positive | 578 | 0.544 | higher->bigger | 0.044 | +0.084 | +0.084 |
| 67 | distilroberta_sentiment_score | 578 | 0.544 | higher->bigger | 0.044 | +0.112 | +0.114 |
| 68 | FinancialBERT_Body_negative | 578 | 0.456 | lower->bigger | 0.044 | -0.044 | -0.050 |
| 69 | Trading-Hero_sentiment_score | 578 | 0.543 | higher->bigger | 0.043 | +0.091 | +0.068 |
| 70 | FinMarBa_Body_negative | 578 | 0.543 | higher->bigger | 0.043 | +0.047 | +0.068 |
| 71 | FLANG-BERT_Body_positive | 578 | 0.543 | higher->bigger | 0.043 | +0.065 | +0.052 |
| 72 | RoBERTa_sentiment_score | 578 | 0.543 | higher->bigger | 0.043 | +0.074 | +0.079 |
| 73 | RoBERTa_positive | 578 | 0.541 | higher->bigger | 0.041 | +0.073 | +0.076 |
| 74 | ModernBERT_Body_neutral | 578 | 0.460 | lower->bigger | 0.040 | -0.048 | -0.040 |
| 75 | DeBERTa_neutral | 578 | 0.461 | lower->bigger | 0.039 | -0.069 | -0.077 |
| 76 | finbert-tone_negative | 578 | 0.461 | lower->bigger | 0.039 | -0.040 | -0.032 |
| 77 | FLANG-ELECTRA_Body_sentiment_score | 578 | 0.539 | higher->bigger | 0.039 | +0.013 | +0.037 |
| 78 | ModernBERT_Body_positive | 578 | 0.538 | higher->bigger | 0.038 | +0.048 | +0.041 |
| 79 | FLANG-BERT_negative | 578 | 0.465 | lower->bigger | 0.035 | -0.058 | -0.007 |
| 80 | LastDailyClose | 578 | 0.465 | lower->bigger | 0.035 | -0.057 | -0.029 |
| 81 | FLANG-BERT_Body_neutral | 578 | 0.466 | lower->bigger | 0.034 | -0.064 | -0.052 |
| 82 | distilroberta_positive | 578 | 0.534 | higher->bigger | 0.034 | +0.108 | +0.114 |
| 83 | FinMarBa_negative | 578 | 0.468 | lower->bigger | 0.032 | -0.047 | -0.041 |
| 84 | FinancialBERT_Body_neutral | 578 | 0.468 | lower->bigger | 0.032 | -0.061 | -0.103 |
| 85 | ModernBERT_Body_sentiment_score | 578 | 0.532 | higher->bigger | 0.032 | +0.047 | +0.036 |
| 86 | negative | 578 | 0.469 | lower->bigger | 0.031 | -0.040 | -0.073 |
| 87 | FinancialBERT_Body_positive | 578 | 0.531 | higher->bigger | 0.031 | +0.065 | +0.108 |
| 88 | FinancialBERT_Body_sentiment_score | 578 | 0.530 | higher->bigger | 0.030 | +0.063 | +0.111 |
| 89 | finbert-tone_Body_sentiment_score | 578 | 0.529 | higher->bigger | 0.029 | +0.058 | +0.067 |
| 90 | FinancialBERT_neutral | 578 | 0.529 | higher->bigger | 0.029 | +0.035 | -0.035 |
| 91 | Trading-Hero_positive | 578 | 0.529 | higher->bigger | 0.029 | +0.083 | +0.055 |
| 92 | FLANG-ELECTRA_Body_negative | 578 | 0.528 | higher->bigger | 0.028 | +0.028 | -0.032 |
| 93 | RoBERTa_neutral | 578 | 0.472 | lower->bigger | 0.028 | -0.057 | -0.067 |
| 94 | FinMarBa_Body_sentiment_score | 578 | 0.472 | lower->bigger | 0.028 | -0.030 | -0.049 |
| 95 | LouMcD-SDict_Body_strong_modal | 578 | 0.473 | lower->bigger | 0.027 | +0.035 | +0.026 |
| 96 | RoBERTa_negative | 578 | 0.474 | lower->bigger | 0.026 | -0.001 | -0.044 |
| 97 | FinancialBERT_negative | 578 | 0.525 | higher->bigger | 0.025 | +0.033 | -0.039 |
| 98 | LouMcD-SDict_sentiment_score | 578 | 0.525 | higher->bigger | 0.025 | +0.063 | +0.054 |
| 99 | DeBERTa_Body_negative | 578 | 0.523 | higher->bigger | 0.023 | +0.020 | -0.011 |
| 100 | distilroberta_neutral | 578 | 0.478 | lower->bigger | 0.022 | -0.086 | -0.109 |
| 101 | DeBERTa_negative | 578 | 0.478 | lower->bigger | 0.022 | -0.019 | -0.021 |
| 102 | finbert-tone_Body_positive | 578 | 0.521 | higher->bigger | 0.021 | +0.048 | +0.059 |
| 103 | ModernBERT_negative | 578 | 0.479 | lower->bigger | 0.021 | +0.006 | +0.014 |
| 104 | finbert-tone_Body_negative | 578 | 0.481 | lower->bigger | 0.019 | -0.078 | -0.052 |
| 105 | Trading-Hero_neutral | 578 | 0.482 | lower->bigger | 0.018 | -0.066 | -0.036 |
| 106 | finbert-tone_sentiment_score | 578 | 0.518 | higher->bigger | 0.018 | +0.083 | +0.055 |
| 107 | FinancialBERT_positive | 578 | 0.482 | lower->bigger | 0.018 | -0.052 | +0.036 |
| 108 | distilroberta_Body_negative | 578 | 0.517 | higher->bigger | 0.017 | -0.050 | -0.022 |
| 109 | distilroberta_negative | 578 | 0.517 | higher->bigger | 0.017 | +0.062 | -0.019 |
| 110 | LouMcD-SDict_negative | 578 | 0.484 | lower->bigger | 0.016 | -0.055 | -0.069 |
| 111 | FinancialBERT_sentiment_score | 578 | 0.485 | lower->bigger | 0.015 | -0.046 | +0.037 |
| 112 | LouMcD-SDict_litigious | 578 | 0.486 | lower->bigger | 0.014 | -0.016 | -0.042 |
| 113 | FinMarBa_Body_positive | 578 | 0.486 | lower->bigger | 0.014 | -0.013 | -0.035 |
| 114 | RoBERTa_Body_negative | 578 | 0.487 | lower->bigger | 0.013 | +0.028 | -0.028 |
| 115 | DeBERTa_Body_neutral | 578 | 0.513 | higher->bigger | 0.013 | +0.022 | +0.012 |
| 116 | finbert-tone_Body_neutral | 578 | 0.487 | lower->bigger | 0.013 | -0.031 | -0.048 |
| 117 | finbert-tone_positive | 578 | 0.513 | higher->bigger | 0.013 | +0.075 | +0.052 |
| 118 | LouMcD-SDict_Body_weak_modal | 578 | 0.488 | lower->bigger | 0.012 | +0.042 | +0.066 |
| 119 | LouMcD-SDict_weak_modal | 578 | 0.512 | higher->bigger | 0.012 | +0.036 | +0.038 |
| 120 | ModernBERT_sentiment_score | 578 | 0.510 | higher->bigger | 0.010 | +0.033 | +0.027 |
| 121 | LouMcD-SDict_positive | 578 | 0.510 | higher->bigger | 0.010 | +0.038 | +0.040 |
| 122 | LouMcD-SDict_constraining | 578 | 0.509 | higher->bigger | 0.009 | +0.008 | -0.007 |
| 123 | RoBERTa_Body_neutral | 578 | 0.507 | higher->bigger | 0.007 | +0.028 | +0.034 |
| 124 | itiBaseline | 578 | 0.505 | higher->bigger | 0.005 | -0.033 | -0.038 |
| 125 | finbert-tone_neutral | 578 | 0.495 | lower->bigger | 0.005 | -0.062 | -0.048 |
| 126 | LouMcD-SDict_uncertainty | 578 | 0.495 | lower->bigger | 0.005 | +0.022 | -0.011 |
| 127 | ModernBERT_Body_negative | 578 | 0.496 | lower->bigger | 0.004 | -0.035 | -0.024 |
| 128 | LouMcD-SDict_Body_uncertainty | 578 | 0.504 | higher->bigger | 0.004 | +0.069 | +0.105 |
| 129 | LouMcD-SDict_Body_litigious | 578 | 0.503 | higher->bigger | 0.003 | +0.052 | +0.007 |
| 130 | LouMcD-SDict_strong_modal | 578 | 0.497 | lower->bigger | 0.003 | -0.042 | -0.051 |
| 131 | ModernBERT_positive | 578 | 0.503 | higher->bigger | 0.003 | +0.037 | +0.034 |
| 132 | RoBERTa_Body_sentiment_score | 578 | 0.502 | higher->bigger | 0.002 | -0.018 | -0.020 |
| 133 | LouMcD-SDict_Body_positive | 578 | 0.502 | higher->bigger | 0.002 | -0.008 | +0.003 |
| 134 | DeBERTa_Body_positive | 578 | 0.501 | higher->bigger | 0.001 | -0.012 | -0.005 |
| 135 | RoBERTa_Body_positive | 578 | 0.501 | higher->bigger | 0.001 | -0.019 | -0.027 |
| 136 | ModernBERT_neutral | 578 | 0.500 | lower->bigger | 0.000 | -0.041 | -0.040 |
| 137 | DeBERTa_Body_sentiment_score | 578 | 0.500 | lower->bigger | 0.000 | -0.013 | +0.000 |

## Categorical ranking (out-of-fold logistic CV-AUC on one-hot levels)

| rank | column | n | CV-AUC | |AUC-0.5| |
|---|---|---|---|---|
| 1 | boilerplate_category | 578 | 0.372 | 0.128 |
| 2 | finbert-tone_Body_label | 578 | 0.398 | 0.102 |
| 3 | FineClass | 578 | 0.595 | 0.095 |
| 4 | ModernBERT_Body_label | 578 | 0.413 | 0.087 |
| 5 | RoBERTa_Body_label | 578 | 0.414 | 0.086 |
| 6 | FinMarBa_Body_label | 578 | 0.424 | 0.076 |
| 7 | FinancialBERT_label | 578 | 0.428 | 0.072 |
| 8 | finbert-tone_label | 578 | 0.429 | 0.071 |
| 9 | BroadClass | 578 | 0.565 | 0.065 |
| 10 | label | 578 | 0.435 | 0.065 |
| 11 | Exchange | 578 | 0.441 | 0.059 |
| 12 | DeBERTa_Body_label | 578 | 0.443 | 0.057 |
| 13 | LouMcD-SDict_label | 578 | 0.448 | 0.052 |
| 14 | FLANG-BERT_Body_label | 578 | 0.453 | 0.047 |
| 15 | FinMarBa_label | 578 | 0.456 | 0.044 |
| 16 | ModernBERT_label | 578 | 0.473 | 0.027 |
| 17 | RoBERTa_label | 578 | 0.473 | 0.027 |
| 18 | FLANG-ELECTRA_label | 578 | 0.523 | 0.023 |
| 19 | LouMcD-SDict_Body_label | 578 | 0.478 | 0.022 |
| 20 | DeBERTa_label | 578 | 0.521 | 0.021 |
| 21 | FLANG-ELECTRA_Body_label | 578 | 0.480 | 0.020 |
| 22 | distilroberta_Body_label | 578 | 0.518 | 0.018 |
| 23 | Author | 578 | 0.487 | 0.013 |
| 24 | Trading-Hero_label | 578 | 0.509 | 0.009 |
| 25 | Trading-Hero_Body_label | 578 | 0.507 | 0.007 |
| 26 | distilroberta_label | 578 | 0.496 | 0.004 |
| 27 | FinancialBERT_Body_label | 578 | 0.496 | 0.004 |
| 28 | FLANG-BERT_label | 578 | 0.503 | 0.003 |

## Categorical breakdowns — per level (min 20 rows; baseline big-mover rate 12.8%)

### `boilerplate_category` (CV-AUC 0.372)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| lawfirm | 248 | 13.3% | +0.5% | 8.13 |
| compliance | 38 | 13.2% | +0.4% | 12.45 |
| conference | 23 | 13.0% | +0.2% | 5.50 |
| <missing> | 219 | 12.8% | -0.0% | 6.58 |
| earnings | 50 | 10.0% | -2.8% | 4.32 |

### `finbert-tone_Body_label` (CV-AUC 0.398)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 241 | 13.7% | +0.9% | 7.84 |
| neutral | 332 | 12.3% | -0.5% | 6.77 |

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

### `ModernBERT_Body_label` (CV-AUC 0.413)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 56 | 14.3% | +1.5% | 6.88 |
| positive | 476 | 12.8% | +0.0% | 7.22 |
| negative | 46 | 10.9% | -1.9% | 5.12 |

### `RoBERTa_Body_label` (CV-AUC 0.414)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| neutral | 500 | 13.2% | +0.4% | 7.19 |
| positive | 77 | 10.4% | -2.4% | 6.77 |

### `FinMarBa_Body_label` (CV-AUC 0.424)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| negative | 28 | 14.3% | +1.5% | 5.29 |
| positive | 550 | 12.7% | -0.1% | 7.11 |

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

### `DeBERTa_Body_label` (CV-AUC 0.443)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 376 | 13.8% | +1.0% | 6.54 |
| neutral | 170 | 11.2% | -1.6% | 8.14 |
| negative | 32 | 9.4% | -3.4% | 6.28 |

### `LouMcD-SDict_label` (CV-AUC 0.448)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 118 | 14.4% | +1.6% | 8.23 |
| neutral | 421 | 12.8% | +0.0% | 6.92 |
| negative | 39 | 7.7% | -5.1% | 5.75 |

### `FLANG-BERT_Body_label` (CV-AUC 0.453)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 245 | 14.3% | +1.5% | 6.91 |
| neutral | 327 | 11.9% | -0.9% | 7.07 |

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

### `LouMcD-SDict_Body_label` (CV-AUC 0.478)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 388 | 13.9% | +1.1% | 7.46 |
| neutral | 74 | 12.2% | -0.6% | 5.42 |
| negative | 116 | 9.5% | -3.3% | 6.24 |

### `DeBERTa_label` (CV-AUC 0.521)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 270 | 15.2% | +2.4% | 7.77 |
| neutral | 291 | 11.0% | -1.8% | 6.47 |

### `FLANG-ELECTRA_Body_label` (CV-AUC 0.480)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 388 | 13.9% | +1.1% | 7.31 |
| neutral | 168 | 10.7% | -2.1% | 7.19 |
| negative | 22 | 9.1% | -3.7% | 4.87 |

### `distilroberta_Body_label` (CV-AUC 0.518)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 371 | 14.8% | +2.0% | 7.41 |
| neutral | 195 | 9.2% | -3.6% | 6.32 |

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

### `Trading-Hero_Body_label` (CV-AUC 0.507)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 181 | 17.7% | +4.9% | 8.78 |
| neutral | 393 | 10.7% | -2.1% | 6.49 |

### `distilroberta_label` (CV-AUC 0.496)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 167 | 16.2% | +3.4% | 8.11 |
| neutral | 407 | 11.5% | -1.3% | 6.76 |

### `FinancialBERT_Body_label` (CV-AUC 0.496)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| positive | 389 | 13.9% | +1.1% | 7.75 |
| neutral | 188 | 10.6% | -2.2% | 5.71 |

### `FLANG-BERT_label` (CV-AUC 0.503)

| level | n | big-mover rate | lift vs baseline | median fromPR_High(%) |
|---|---|---|---|---|
| negative | 85 | 15.3% | +2.5% | 6.52 |
| positive | 346 | 14.5% | +1.6% | 7.47 |
| neutral | 147 | 7.5% | -5.3% | 6.09 |

## Combos (2- & 3-column CV-AUC) among the top-15 de-cloned singles pool

Pool: model_proba_pctl_hl, kw_mult_nb_log_pctl, kw_add_first_hl, model_proba_pctl, kw_add_log, kw_add_lin_pctl, MktCap, Float, kw_add_first, kw_mult_nb_pctl_hl, Trading-Hero_Body_sentiment_score, Trading-Hero_Body_positive, n_kw_hits_hl, LouMcD-SDict_Body_negative, FinMarBa_indecisive

Skipped as near-clones (|Spearman| >= 0.95 vs a pooled column): model_proba_hl ~ model_proba_pctl_hl; kw_add_first_pctl_hl ~ kw_add_first_hl; model_proba ~ model_proba_pctl; kw_mult_nb_log ~ kw_mult_nb_log_pctl; kw_mult_nb_pctl ~ kw_mult_nb_log_pctl; kw_mult_nb ~ kw_mult_nb_log_pctl; kw_add_log_pctl ~ kw_add_log; kw_add_log_pctl_hl ~ kw_add_first_hl; kw_add_log_hl ~ kw_add_first_hl; kw_add_lin ~ kw_add_lin_pctl; kw_add_lin_pctl_hl ~ kw_add_first_hl; kw_add_lin_hl ~ kw_add_first_hl; model_proba_PLUS_kw_mult_nb ~ model_proba_pctl; kw_add_first_pctl ~ kw_add_first; kw_mult_nb_hl ~ kw_mult_nb_pctl_hl; kw_mult_nb_log_pctl_hl ~ kw_mult_nb_pctl_hl; kw_mult_nb_log_hl ~ kw_mult_nb_pctl_hl; Trading-Hero_Body_neutral ~ Trading-Hero_Body_positive

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | model_proba_pctl_hl + kw_mult_nb_log_pctl + Float | 578 | 0.727 | model_proba_pctl_hl=+0.40, kw_mult_nb_log_pctl=+0.56, Float=-0.56 |
| 2 | kw_mult_nb_log_pctl + kw_add_first_hl + Float | 578 | 0.727 | kw_mult_nb_log_pctl=+0.55, kw_add_first_hl=+0.38, Float=-0.55 |
| 3 | kw_mult_nb_log_pctl + Float + FinMarBa_indecisive | 578 | 0.716 | kw_mult_nb_log_pctl=+0.73, Float=-0.60, FinMarBa_indecisive=-0.25 |
| 4 | kw_mult_nb_log_pctl + Float + Trading-Hero_Body_sentiment_score | 578 | 0.713 | kw_mult_nb_log_pctl=+0.75, Float=-0.60, Trading-Hero_Body_sentiment_score=+0.29 |
| 5 | kw_mult_nb_log_pctl + Float + Trading-Hero_Body_positive | 578 | 0.713 | kw_mult_nb_log_pctl=+0.75, Float=-0.60, Trading-Hero_Body_positive=+0.28 |
| 6 | kw_mult_nb_log_pctl + MktCap + Float | 578 | 0.712 | kw_mult_nb_log_pctl=+0.72, MktCap=-0.25, Float=-0.46 |
| 7 | kw_mult_nb_log_pctl + Float + kw_mult_nb_pctl_hl | 578 | 0.711 | kw_mult_nb_log_pctl=+0.69, Float=-0.58, kw_mult_nb_pctl_hl=+0.15 |
| 8 | kw_mult_nb_log_pctl + Float + LouMcD-SDict_Body_negative | 578 | 0.711 | kw_mult_nb_log_pctl=+0.71, Float=-0.59, LouMcD-SDict_Body_negative=-0.24 |
| 9 | kw_mult_nb_log_pctl + Float | 578 | 0.711 | kw_mult_nb_log_pctl=+0.75, Float=-0.60 |
| 10 | model_proba_pctl_hl + kw_mult_nb_log_pctl + MktCap | 578 | 0.707 | model_proba_pctl_hl=+0.41, kw_mult_nb_log_pctl=+0.52, MktCap=-0.40 |
| 11 | model_proba_pctl_hl + Float + kw_add_first | 578 | 0.706 | model_proba_pctl_hl=+0.47, Float=-0.57, kw_add_first=+0.36 |
| 12 | kw_add_first_hl + model_proba_pctl + Float | 578 | 0.706 | kw_add_first_hl=+0.37, model_proba_pctl=+0.46, Float=-0.54 |
| 13 | kw_mult_nb_log_pctl + Float + kw_add_first | 578 | 0.706 | kw_mult_nb_log_pctl=+0.62, Float=-0.60, kw_add_first=+0.17 |
| 14 | kw_mult_nb_log_pctl + kw_add_first_hl + MktCap | 578 | 0.706 | kw_mult_nb_log_pctl=+0.51, kw_add_first_hl=+0.38, MktCap=-0.39 |
| 15 | model_proba_pctl_hl + model_proba_pctl + Float | 578 | 0.705 | model_proba_pctl_hl=+0.39, model_proba_pctl=+0.48, Float=-0.55 |
| 16 | model_proba_pctl_hl + kw_add_log + Float | 578 | 0.704 | model_proba_pctl_hl=+0.45, kw_add_log=+0.36, Float=-0.56 |
| 17 | kw_add_first_hl + Float + kw_add_first | 578 | 0.703 | kw_add_first_hl=+0.43, Float=-0.56, kw_add_first=+0.33 |
| 18 | kw_mult_nb_log_pctl + Float + n_kw_hits_hl | 578 | 0.702 | kw_mult_nb_log_pctl=+0.71, Float=-0.59, n_kw_hits_hl=+0.10 |
| 19 | kw_mult_nb_log_pctl + model_proba_pctl + Float | 578 | 0.701 | kw_mult_nb_log_pctl=+0.53, model_proba_pctl=+0.27, Float=-0.59 |
| 20 | kw_add_first_hl + kw_add_log + Float | 578 | 0.701 | kw_add_first_hl=+0.42, kw_add_log=+0.34, Float=-0.55 |

## Combined leaderboard (top 15, all kinds — singles use raw AUC, combos/cats use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | model_proba_pctl_hl + kw_mult_nb_log_pctl + Float | 3 | 0.727 |
| 2 | kw_mult_nb_log_pctl + kw_add_first_hl + Float | 3 | 0.727 |
| 3 | kw_mult_nb_log_pctl + Float + FinMarBa_indecisive | 3 | 0.716 |
| 4 | kw_mult_nb_log_pctl + Float + Trading-Hero_Body_sentiment_score | 3 | 0.713 |
| 5 | kw_mult_nb_log_pctl + Float + Trading-Hero_Body_positive | 3 | 0.713 |
| 6 | kw_mult_nb_log_pctl + MktCap + Float | 3 | 0.712 |
| 7 | kw_mult_nb_log_pctl + Float + kw_mult_nb_pctl_hl | 3 | 0.711 |
| 8 | kw_mult_nb_log_pctl + Float + LouMcD-SDict_Body_negative | 3 | 0.711 |
| 9 | kw_mult_nb_log_pctl + Float | 2 | 0.711 |
| 10 | model_proba_pctl_hl + kw_mult_nb_log_pctl + MktCap | 3 | 0.707 |
| 11 | model_proba_pctl_hl + Float + kw_add_first | 3 | 0.706 |
| 12 | kw_add_first_hl + model_proba_pctl + Float | 3 | 0.706 |
| 13 | kw_mult_nb_log_pctl + Float + kw_add_first | 3 | 0.706 |
| 14 | kw_mult_nb_log_pctl + kw_add_first_hl + MktCap | 3 | 0.706 |
| 15 | model_proba_pctl_hl + model_proba_pctl + Float | 3 | 0.705 |

## Collinearity among the combo pool (Spearman, pairwise rows)

| | model_proba_ | kw_mult_nb_l | kw_add_first | model_proba_ | kw_add_log | kw_add_lin_p | MktCap | Float | kw_add_first | kw_mult_nb_p | Trading-Hero | Trading-Hero | n_kw_hits_hl | LouMcD-SDict | FinMarBa_ind |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| model_proba_pctl | 1.00 | 0.48 | 0.93 | 0.55 | 0.45 | 0.42 | -0.19 | -0.12 | 0.41 | 0.73 | 0.09 | 0.08 | 0.29 | -0.17 | -0.22 |
| kw_mult_nb_log_p | 0.48 | 1.00 | 0.48 | 0.84 | 0.80 | 0.70 | -0.18 | -0.08 | 0.77 | 0.39 | 0.09 | 0.06 | 0.29 | -0.21 | -0.13 |
| kw_add_first_hl | 0.93 | 0.48 | 1.00 | 0.53 | 0.46 | 0.43 | -0.18 | -0.13 | 0.43 | 0.61 | 0.12 | 0.09 | 0.35 | -0.19 | -0.25 |
| model_proba_pctl | 0.55 | 0.84 | 0.53 | 1.00 | 0.81 | 0.71 | -0.21 | -0.11 | 0.78 | 0.47 | 0.16 | 0.14 | 0.34 | -0.19 | -0.21 |
| kw_add_log | 0.45 | 0.80 | 0.46 | 0.81 | 1.00 | 0.93 | -0.18 | -0.07 | 0.93 | 0.35 | 0.16 | 0.14 | 0.32 | -0.20 | -0.19 |
| kw_add_lin_pctl | 0.42 | 0.70 | 0.43 | 0.71 | 0.93 | 1.00 | -0.17 | -0.09 | 0.77 | 0.32 | 0.16 | 0.15 | 0.30 | -0.19 | -0.14 |
| MktCap | -0.19 | -0.18 | -0.18 | -0.21 | -0.18 | -0.17 | 1.00 | 0.55 | -0.15 | -0.19 | -0.06 | -0.02 | -0.08 | 0.04 | 0.06 |
| Float | -0.12 | -0.08 | -0.13 | -0.11 | -0.07 | -0.09 | 0.55 | 1.00 | -0.04 | -0.10 | -0.01 | -0.01 | -0.07 | 0.03 | 0.00 |
| kw_add_first | 0.41 | 0.77 | 0.43 | 0.78 | 0.93 | 0.77 | -0.15 | -0.04 | 1.00 | 0.32 | 0.14 | 0.12 | 0.30 | -0.19 | -0.24 |
| kw_mult_nb_pctl_ | 0.73 | 0.39 | 0.61 | 0.47 | 0.35 | 0.32 | -0.19 | -0.10 | 0.32 | 1.00 | 0.06 | 0.03 | 0.10 | -0.18 | -0.15 |
| Trading-Hero_Bod | 0.09 | 0.09 | 0.12 | 0.16 | 0.16 | 0.16 | -0.06 | -0.01 | 0.14 | 0.06 | 1.00 | 0.89 | 0.10 | -0.28 | -0.26 |
| Trading-Hero_Bod | 0.08 | 0.06 | 0.09 | 0.14 | 0.14 | 0.15 | -0.02 | -0.01 | 0.12 | 0.03 | 0.89 | 1.00 | 0.09 | -0.11 | -0.24 |
| n_kw_hits_hl | 0.29 | 0.29 | 0.35 | 0.34 | 0.32 | 0.30 | -0.08 | -0.07 | 0.30 | 0.10 | 0.10 | 0.09 | 1.00 | -0.10 | -0.16 |
| LouMcD-SDict_Bod | -0.17 | -0.21 | -0.19 | -0.19 | -0.20 | -0.19 | 0.04 | 0.03 | -0.19 | -0.18 | -0.28 | -0.11 | -0.10 | 1.00 | 0.09 |
| FinMarBa_indecis | -0.22 | -0.13 | -0.25 | -0.21 | -0.19 | -0.14 | 0.06 | 0.00 | -0.24 | -0.15 | -0.26 | -0.24 | -0.16 | 0.09 | 1.00 |

## Threshold sensitivity — pool columns' AUC at different cutoffs

| column | >= 10% | >= 15% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|---|
| model_proba_pctl_hl | 0.603 | 0.628 | 0.625 | 0.672 | 0.615 |
| kw_mult_nb_log_pctl | 0.632 | 0.680 | 0.672 | 0.671 | 0.675 |
| kw_add_first_hl | 0.605 | 0.634 | 0.636 | 0.670 | 0.630 |
| model_proba_pctl | 0.635 | 0.676 | 0.667 | 0.669 | 0.658 |
| kw_add_log | 0.621 | 0.656 | 0.652 | 0.664 | 0.657 |
| kw_add_lin_pctl | 0.621 | 0.644 | 0.652 | 0.661 | 0.654 |
| MktCap | 0.378 | 0.375 | 0.347 | 0.342 | 0.305 |
| Float | 0.402 | 0.387 | 0.365 | 0.347 | 0.319 |
| kw_add_first | 0.616 | 0.650 | 0.637 | 0.653 | 0.651 |
| kw_mult_nb_pctl_hl | 0.581 | 0.605 | 0.592 | 0.619 | 0.562 |
| Trading-Hero_Body_sentiment_score | 0.574 | 0.607 | 0.592 | 0.603 | 0.625 |
| Trading-Hero_Body_positive | 0.562 | 0.592 | 0.577 | 0.595 | 0.627 |
| n_kw_hits_hl | 0.584 | 0.604 | 0.598 | 0.590 | 0.614 |
| LouMcD-SDict_Body_negative | 0.423 | 0.413 | 0.404 | 0.415 | 0.466 |
| FinMarBa_indecisive | 0.440 | 0.420 | 0.458 | 0.419 | 0.434 |
| _n big movers_ | 225 | 155 | 117 | 74 | 44 |

## Conclusion

- **Best numeric single:** `model_proba_pctl_hl` (AUC 0.672, higher->bigger).
- **Best categorical:** `boilerplate_category` (CV-AUC 0.372).
- **Best combo:** `model_proba_pctl_hl + kw_mult_nb_log_pctl + Float` (CV-AUC 0.727; lift over best single: +0.055).
- Many numeric columns are monotone derivatives of the same score (pctl twins, encoder families) — see the de-cloned pool and the collinearity matrix before reading much into small AUC gaps.

