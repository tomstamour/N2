# Headline vs body — the 11 encoders on `fromPR_High(%)`

**Corpus:** `PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv` — 3253 rows with a target (outliers >500% dropped).
**Pairs compared:** 48 head/body channel pairs across 11 encoders.
**Bootstrap:** 2000 paired row resamples, seed 42.

Orientation is **a priori** from each channel's semantics, applied identically to both members of a pair — never fitted to the target. `(inv)` marks a channel used negated. AUC below 0.50 after that fixed orientation means the column runs backwards.


## Section A — paired AUC, movers = `fromPR_High(%) >= 30`

287 movers / 3253 rows = base rate 8.8%. Single-AUC Hanley-McNeil SE ≈ ±0.018.

Bonferroni column uses z = 3.28 (alpha 0.05 split over 48 pairs).

| encoder | channel | HEAD | BODY | Δ (body−head) | 95% CI (paired) | Bonferroni CI | rho(h,b) | verdict |
|---|---|---|---|---|---|---|---|---|
| FinancialBERT | `neutral` (inv) | 0.490 | 0.568 | +0.078 | [+0.034, +0.125] | [+0.002, +0.155] | 0.05 | **body wins** |
| FinancialBERT | `positive`  | 0.500 | 0.574 | +0.074 | [+0.031, +0.120] | [-0.000, +0.148] | 0.08 | body (nominal only) |
| FinancialBERT | `sentiment_score`  | 0.505 | 0.576 | +0.070 | [+0.027, +0.115] | [-0.002, +0.143] | 0.09 | body (nominal only) |
| FLANG-ELECTRA | `negative` (inv) | 0.433 | 0.488 | +0.055 | [+0.013, +0.099] | [-0.016, +0.126] | 0.25 | body (nominal only) |
| DeBERTa | `negative` (inv) | 0.465 | 0.518 | +0.053 | [+0.014, +0.090] | [-0.011, +0.117] | 0.27 | body (nominal only) |
| FinancialBERT | `negative` (inv) | 0.518 | 0.567 | +0.049 | [+0.007, +0.090] | [-0.022, +0.120] | 0.00 | body (nominal only) |
| LouMcD-SDict | `negative` (inv) | 0.518 | 0.562 | +0.044 | [+0.015, +0.073] | [-0.005, +0.093] | 0.38 | body (nominal only) |
| LouMcD-SDict | `weak_modal` (inv) | 0.510 | 0.537 | +0.028 | [-0.001, +0.056] | [-0.020, +0.075] | 0.27 | tie |
| LouMcD-SDict | `strong_modal` (inv) | 0.499 | 0.522 | +0.023 | [-0.007, +0.052] | [-0.028, +0.073] | 0.05 | tie |
| FLANG-BERT | `negative` (inv) | 0.559 | 0.581 | +0.022 | [-0.019, +0.062] | [-0.045, +0.089] | 0.25 | tie |
| LouMcD-SDict | `sentiment_score`  | 0.534 | 0.554 | +0.020 | [-0.016, +0.057] | [-0.042, +0.083] | 0.24 | tie |
| distilroberta | `negative` (inv) | 0.457 | 0.469 | +0.012 | [-0.032, +0.057] | [-0.063, +0.087] | 0.13 | tie |
| LouMcD-SDict | `constraining` (inv) | 0.496 | 0.501 | +0.004 | [-0.024, +0.030] | [-0.041, +0.050] | 0.18 | tie |
| LouMcD-SDict | `litigious` (inv) | 0.514 | 0.511 | -0.002 | [-0.029, +0.021] | [-0.044, +0.039] | 0.41 | tie |
| finbert-tone | `negative` (inv) | 0.529 | 0.523 | -0.006 | [-0.036, +0.023] | [-0.056, +0.043] | 0.34 | tie |
| ModernBERT | `negative` (inv) | 0.537 | 0.531 | -0.006 | [-0.050, +0.039] | [-0.081, +0.069] | 0.27 | tie |
| Trading-Hero | `sentiment_score`  | 0.607 | 0.595 | -0.012 | [-0.052, +0.026] | [-0.077, +0.053] | 0.36 | tie |
| Trading-Hero | `positive`  | 0.597 | 0.584 | -0.013 | [-0.052, +0.025] | [-0.078, +0.052] | 0.32 | tie |
| LouMcD-SDict | `uncertainty` (inv) | 0.514 | 0.501 | -0.013 | [-0.046, +0.021] | [-0.070, +0.043] | 0.25 | tie |
| Trading-Hero | `neutral` (inv) | 0.582 | 0.566 | -0.016 | [-0.057, +0.025] | [-0.084, +0.052] | 0.30 | tie |
| LouMcD-SDict | `positive`  | 0.523 | 0.499 | -0.024 | [-0.060, +0.012] | [-0.084, +0.037] | 0.26 | tie |
| distilroberta | `neutral` (inv) | 0.570 | 0.542 | -0.028 | [-0.065, +0.011] | [-0.092, +0.037] | 0.34 | tie |
| distilroberta | `positive`  | 0.589 | 0.557 | -0.032 | [-0.067, +0.003] | [-0.092, +0.028] | 0.40 | tie |
| Trading-Hero | `negative` (inv) | 0.602 | 0.568 | -0.034 | [-0.076, +0.008] | [-0.105, +0.037] | 0.21 | tie |
| distilroberta | `sentiment_score`  | 0.599 | 0.559 | -0.040 | [-0.075, -0.006] | [-0.098, +0.018] | 0.44 | head (nominal only) |
| FLANG-ELECTRA | `positive`  | 0.606 | 0.560 | -0.046 | [-0.085, -0.009] | [-0.109, +0.017] | 0.43 | head (nominal only) |
| ModernBERT | `positive`  | 0.604 | 0.558 | -0.046 | [-0.086, -0.006] | [-0.114, +0.021] | 0.34 | head (nominal only) |
| ModernBERT | `sentiment_score`  | 0.601 | 0.553 | -0.048 | [-0.089, -0.007] | [-0.117, +0.020] | 0.36 | head (nominal only) |
| FLANG-BERT | `sentiment_score`  | 0.609 | 0.559 | -0.050 | [-0.092, -0.008] | [-0.120, +0.021] | 0.37 | head (nominal only) |
| FinMarBa | `indecisive` (inv) | 0.631 | 0.581 | -0.051 | [-0.084, -0.018] | [-0.107, +0.006] | 0.44 | head (nominal only) |
| finbert-tone | `sentiment_score`  | 0.590 | 0.537 | -0.053 | [-0.092, -0.016] | [-0.117, +0.011] | 0.37 | head (nominal only) |
| FLANG-ELECTRA | `neutral` (inv) | 0.601 | 0.547 | -0.054 | [-0.096, -0.014] | [-0.122, +0.014] | 0.39 | head (nominal only) |
| FLANG-BERT | `positive`  | 0.610 | 0.554 | -0.056 | [-0.098, -0.015] | [-0.125, +0.013] | 0.38 | head (nominal only) |
| finbert-tone | `neutral` (inv) | 0.573 | 0.516 | -0.057 | [-0.094, -0.020] | [-0.120, +0.007] | 0.34 | head (nominal only) |
| finbert-tone | `positive`  | 0.586 | 0.529 | -0.057 | [-0.094, -0.022] | [-0.119, +0.005] | 0.36 | head (nominal only) |
| ModernBERT | `neutral` (inv) | 0.596 | 0.538 | -0.058 | [-0.102, -0.014] | [-0.128, +0.013] | 0.22 | head (nominal only) |
| FLANG-ELECTRA | `sentiment_score`  | 0.607 | 0.549 | -0.058 | [-0.096, -0.023] | [-0.118, +0.002] | 0.43 | head (nominal only) |
| RoBERTa | `neutral` (inv) | 0.550 | 0.491 | -0.059 | [-0.097, -0.020] | [-0.124, +0.006] | 0.37 | head (nominal only) |
| FLANG-BERT | `neutral` (inv) | 0.603 | 0.541 | -0.062 | [-0.100, -0.023] | [-0.125, +0.001] | 0.30 | head (nominal only) |
| DeBERTa | `sentiment_score`  | 0.603 | 0.540 | -0.063 | [-0.098, -0.026] | [-0.124, -0.001] | 0.46 | head wins |
| RoBERTa | `negative` (inv) | 0.547 | 0.483 | -0.064 | [-0.101, -0.027] | [-0.127, -0.001] | 0.40 | head wins |
| DeBERTa | `positive`  | 0.607 | 0.540 | -0.066 | [-0.102, -0.028] | [-0.129, -0.004] | 0.42 | head wins |
| RoBERTa | `positive`  | 0.565 | 0.498 | -0.067 | [-0.106, -0.031] | [-0.131, -0.004] | 0.45 | head wins |
| RoBERTa | `sentiment_score`  | 0.567 | 0.498 | -0.069 | [-0.108, -0.032] | [-0.133, -0.006] | 0.44 | head wins |
| FinMarBa | `positive`  | 0.608 | 0.534 | -0.073 | [-0.111, -0.038] | [-0.135, -0.012] | 0.41 | head wins |
| FinMarBa | `negative` (inv) | 0.582 | 0.509 | -0.073 | [-0.113, -0.035] | [-0.139, -0.008] | 0.36 | head wins |
| FinMarBa | `sentiment_score`  | 0.598 | 0.524 | -0.074 | [-0.113, -0.037] | [-0.137, -0.011] | 0.39 | head wins |
| DeBERTa | `neutral` (inv) | 0.595 | 0.504 | -0.091 | [-0.130, -0.050] | [-0.158, -0.024] | 0.33 | head wins |

**Tally at Bonferroni:** body wins 1, head wins 9, tie 38, of 48 pairs.

## Section A — paired AUC, movers = `fromPR_High(%) >= 15`

649 movers / 3253 rows = base rate 20.0%. Single-AUC Hanley-McNeil SE ≈ ±0.013.

Bonferroni column uses z = 3.28 (alpha 0.05 split over 48 pairs).

| encoder | channel | HEAD | BODY | Δ (body−head) | 95% CI (paired) | Bonferroni CI | rho(h,b) | verdict |
|---|---|---|---|---|---|---|---|---|
| FinancialBERT | `neutral` (inv) | 0.497 | 0.561 | +0.064 | [+0.032, +0.099] | [+0.009, +0.119] | 0.05 | **body wins** |
| FinancialBERT | `positive`  | 0.503 | 0.566 | +0.064 | [+0.032, +0.097] | [+0.010, +0.117] | 0.08 | **body wins** |
| FinancialBERT | `sentiment_score`  | 0.505 | 0.567 | +0.062 | [+0.030, +0.094] | [+0.009, +0.115] | 0.09 | **body wins** |
| FLANG-BERT | `negative` (inv) | 0.543 | 0.580 | +0.037 | [+0.009, +0.066] | [-0.011, +0.085] | 0.25 | body (nominal only) |
| FinancialBERT | `negative` (inv) | 0.504 | 0.541 | +0.037 | [+0.006, +0.067] | [-0.015, +0.089] | 0.00 | body (nominal only) |
| distilroberta | `negative` (inv) | 0.463 | 0.493 | +0.030 | [-0.001, +0.059] | [-0.021, +0.081] | 0.13 | tie |
| FLANG-ELECTRA | `negative` (inv) | 0.460 | 0.490 | +0.030 | [+0.001, +0.059] | [-0.019, +0.079] | 0.25 | body (nominal only) |
| DeBERTa | `negative` (inv) | 0.500 | 0.526 | +0.026 | [-0.003, +0.054] | [-0.022, +0.073] | 0.27 | tie |
| LouMcD-SDict | `negative` (inv) | 0.513 | 0.535 | +0.022 | [+0.001, +0.043] | [-0.014, +0.058] | 0.38 | body (nominal only) |
| LouMcD-SDict | `strong_modal` (inv) | 0.498 | 0.516 | +0.017 | [-0.006, +0.039] | [-0.020, +0.055] | 0.05 | tie |
| ModernBERT | `negative` (inv) | 0.514 | 0.526 | +0.011 | [-0.019, +0.041] | [-0.039, +0.062] | 0.27 | tie |
| LouMcD-SDict | `weak_modal` (inv) | 0.510 | 0.519 | +0.009 | [-0.012, +0.030] | [-0.026, +0.044] | 0.27 | tie |
| LouMcD-SDict | `sentiment_score`  | 0.527 | 0.532 | +0.006 | [-0.021, +0.032] | [-0.038, +0.049] | 0.24 | tie |
| Trading-Hero | `sentiment_score`  | 0.593 | 0.594 | +0.001 | [-0.026, +0.029] | [-0.045, +0.048] | 0.36 | tie |
| LouMcD-SDict | `constraining` (inv) | 0.498 | 0.498 | -0.001 | [-0.021, +0.018] | [-0.033, +0.031] | 0.18 | tie |
| LouMcD-SDict | `litigious` (inv) | 0.504 | 0.499 | -0.005 | [-0.024, +0.012] | [-0.035, +0.026] | 0.41 | tie |
| Trading-Hero | `positive`  | 0.589 | 0.584 | -0.006 | [-0.034, +0.023] | [-0.053, +0.041] | 0.32 | tie |
| distilroberta | `neutral` (inv) | 0.557 | 0.548 | -0.009 | [-0.038, +0.020] | [-0.056, +0.038] | 0.34 | tie |
| finbert-tone | `negative` (inv) | 0.535 | 0.526 | -0.009 | [-0.029, +0.012] | [-0.043, +0.024] | 0.34 | tie |
| Trading-Hero | `neutral` (inv) | 0.578 | 0.568 | -0.010 | [-0.038, +0.021] | [-0.058, +0.039] | 0.30 | tie |
| distilroberta | `positive`  | 0.571 | 0.561 | -0.011 | [-0.039, +0.017] | [-0.056, +0.035] | 0.40 | tie |
| distilroberta | `sentiment_score`  | 0.579 | 0.564 | -0.014 | [-0.041, +0.012] | [-0.058, +0.029] | 0.44 | tie |
| FLANG-ELECTRA | `positive`  | 0.574 | 0.559 | -0.015 | [-0.042, +0.011] | [-0.059, +0.030] | 0.43 | tie |
| LouMcD-SDict | `positive`  | 0.517 | 0.500 | -0.017 | [-0.043, +0.008] | [-0.060, +0.026] | 0.26 | tie |
| LouMcD-SDict | `uncertainty` (inv) | 0.502 | 0.483 | -0.019 | [-0.043, +0.005] | [-0.059, +0.022] | 0.25 | tie |
| FLANG-BERT | `sentiment_score`  | 0.574 | 0.552 | -0.022 | [-0.052, +0.007] | [-0.071, +0.027] | 0.37 | tie |
| Trading-Hero | `negative` (inv) | 0.581 | 0.559 | -0.022 | [-0.052, +0.008] | [-0.073, +0.028] | 0.21 | tie |
| FLANG-BERT | `neutral` (inv) | 0.568 | 0.541 | -0.027 | [-0.057, +0.001] | [-0.075, +0.022] | 0.30 | tie |
| FLANG-BERT | `positive`  | 0.575 | 0.548 | -0.027 | [-0.056, +0.001] | [-0.075, +0.021] | 0.38 | tie |
| FLANG-ELECTRA | `sentiment_score`  | 0.580 | 0.552 | -0.029 | [-0.055, -0.003] | [-0.073, +0.015] | 0.43 | head (nominal only) |
| finbert-tone | `sentiment_score`  | 0.573 | 0.542 | -0.030 | [-0.058, -0.002] | [-0.076, +0.016] | 0.37 | head (nominal only) |
| FLANG-ELECTRA | `neutral` (inv) | 0.568 | 0.537 | -0.032 | [-0.059, -0.005] | [-0.077, +0.014] | 0.39 | head (nominal only) |
| finbert-tone | `neutral` (inv) | 0.556 | 0.524 | -0.032 | [-0.060, -0.003] | [-0.079, +0.015] | 0.34 | head (nominal only) |
| DeBERTa | `sentiment_score`  | 0.576 | 0.542 | -0.033 | [-0.059, -0.008] | [-0.077, +0.010] | 0.46 | head (nominal only) |
| RoBERTa | `neutral` (inv) | 0.533 | 0.500 | -0.033 | [-0.063, -0.006] | [-0.081, +0.014] | 0.37 | head (nominal only) |
| finbert-tone | `positive`  | 0.567 | 0.533 | -0.033 | [-0.062, -0.005] | [-0.080, +0.013] | 0.36 | head (nominal only) |
| DeBERTa | `positive`  | 0.576 | 0.542 | -0.034 | [-0.060, -0.007] | [-0.079, +0.011] | 0.42 | head (nominal only) |
| ModernBERT | `positive`  | 0.579 | 0.544 | -0.035 | [-0.065, -0.006] | [-0.084, +0.014] | 0.34 | head (nominal only) |
| ModernBERT | `sentiment_score`  | 0.577 | 0.541 | -0.036 | [-0.066, -0.008] | [-0.084, +0.011] | 0.36 | head (nominal only) |
| RoBERTa | `positive`  | 0.546 | 0.505 | -0.041 | [-0.069, -0.014] | [-0.086, +0.004] | 0.45 | head (nominal only) |
| ModernBERT | `neutral` (inv) | 0.573 | 0.531 | -0.042 | [-0.073, -0.010] | [-0.094, +0.011] | 0.22 | head (nominal only) |
| RoBERTa | `sentiment_score`  | 0.547 | 0.504 | -0.043 | [-0.071, -0.015] | [-0.088, +0.003] | 0.44 | head (nominal only) |
| RoBERTa | `negative` (inv) | 0.527 | 0.482 | -0.045 | [-0.072, -0.017] | [-0.091, +0.001] | 0.40 | head (nominal only) |
| DeBERTa | `neutral` (inv) | 0.568 | 0.521 | -0.047 | [-0.075, -0.019] | [-0.096, +0.001] | 0.33 | head (nominal only) |
| FinMarBa | `indecisive` (inv) | 0.603 | 0.555 | -0.048 | [-0.075, -0.023] | [-0.090, -0.006] | 0.44 | head wins |
| FinMarBa | `negative` (inv) | 0.559 | 0.508 | -0.051 | [-0.078, -0.024] | [-0.096, -0.006] | 0.36 | head wins |
| FinMarBa | `sentiment_score`  | 0.572 | 0.518 | -0.055 | [-0.080, -0.028] | [-0.099, -0.011] | 0.39 | head wins |
| FinMarBa | `positive`  | 0.581 | 0.525 | -0.056 | [-0.082, -0.029] | [-0.099, -0.013] | 0.41 | head wins |

**Tally at Bonferroni:** body wins 3, head wins 4, tie 41, of 48 pairs.

## Section B — does body ADD on top of headline? (movers >= 30)

Pooled **out-of-fold** logistic AUC, 5 folds, features percentile-ranked then standardized per fold on the train fold only. Every number in this table is computed the same way — **the singles are pooled-OOF too**, not the fit-free rank AUCs of Section A, so the pair is not charged a cross-validation penalty its baselines avoided. Synergy = pair − better OOF single; `rank A` columns repeat Section A's fit-free AUCs for reference.

| encoder | head col | body col | rank A head | rank A body | OOF head | OOF body | OOF head+body | synergy | rho |
|---|---|---|---|---|---|---|---|---|---|
| FinMarBa | `FinMarBa_indecisive` | `FinMarBa_Body_indecisive` | 0.631 | 0.581 | 0.623 | 0.571 | **0.623** | -0.001 | 0.44 |
| FLANG-BERT | `FLANG-BERT_positive` | `FLANG-BERT_Body_negative` | 0.610 | 0.581 | 0.603 | 0.569 | **0.621** | +0.019 | 0.14 |
| Trading-Hero | `Trading-Hero_sentiment_score` | `Trading-Hero_Body_sentiment_score` | 0.607 | 0.595 | 0.598 | 0.586 | **0.612** | +0.014 | 0.36 |
| FLANG-ELECTRA | `FLANG-ELECTRA_sentiment_score` | `FLANG-ELECTRA_Body_positive` | 0.607 | 0.560 | 0.600 | 0.548 | **0.600** | -0.000 | 0.45 |
| DeBERTa | `DeBERTa_positive` | `DeBERTa_Body_sentiment_score` | 0.607 | 0.540 | 0.596 | 0.512 | **0.594** | -0.002 | 0.42 |
| ModernBERT | `ModernBERT_positive` | `ModernBERT_Body_positive` | 0.604 | 0.558 | 0.594 | 0.536 | **0.588** | -0.005 | 0.34 |
| distilroberta | `distilroberta_sentiment_score` | `distilroberta_Body_sentiment_score` | 0.599 | 0.559 | 0.585 | 0.543 | **0.585** | +0.000 | 0.44 |
| finbert-tone | `finbert-tone_sentiment_score` | `finbert-tone_Body_sentiment_score` | 0.590 | 0.537 | 0.576 | 0.512 | **0.573** | -0.003 | 0.37 |
| RoBERTa | `RoBERTa_sentiment_score` | `RoBERTa_Body_sentiment_score` | 0.567 | 0.498 | 0.548 | 0.444 | **0.561** | +0.013 | 0.44 |
| FinancialBERT | `FinancialBERT_negative` | `FinancialBERT_Body_sentiment_score` | 0.518 | 0.576 | 0.483 | 0.561 | **0.561** | -0.001 | 0.06 |
| LouMcD-SDict | `LouMcD-SDict_sentiment_score` | `LouMcD-SDict_Body_negative` | 0.534 | 0.562 | 0.507 | 0.536 | **0.548** | +0.011 | 0.18 |

**Encoders where body adds more than +0.01 AUC over the better single: 4 of 11.**

**Cross-encoder best pair:** `FinMarBa_indecisive` (OOF 0.623) + `Trading-Hero_Body_sentiment_score` (OOF 0.586) → OOF AUC **0.640** (synergy +0.017, rho 0.22).

## Stability check — rows with ArrivalDate >= 2026-06-20

74 movers / 578 rows = 12.8% base. All 11 encoders are externally trained, so the FULL corpus above is their valid test set — this block is a small-sample stability check only, never the primary number.

| encoder | best head | AUC | best body | AUC |
|---|---|---|---|---|
| DeBERTa | `DeBERTa_positive` | 0.544 | `DeBERTa_Body_sentiment_score` | 0.500 |
| FLANG-BERT | `FLANG-BERT_positive` | 0.558 | `FLANG-BERT_Body_negative` | 0.569 |
| FLANG-ELECTRA | `FLANG-ELECTRA_sentiment_score` | 0.568 | `FLANG-ELECTRA_Body_positive` | 0.564 |
| FinMarBa | `FinMarBa_indecisive` | 0.581 | `FinMarBa_Body_indecisive` | 0.571 |
| FinancialBERT | `FinancialBERT_negative` | 0.475 | `FinancialBERT_Body_sentiment_score` | 0.530 |
| LouMcD-SDict | `LouMcD-SDict_sentiment_score` | 0.525 | `LouMcD-SDict_Body_negative` | 0.585 |
| ModernBERT | `ModernBERT_positive` | 0.503 | `ModernBERT_Body_positive` | 0.538 |
| RoBERTa | `RoBERTa_sentiment_score` | 0.543 | `RoBERTa_Body_sentiment_score` | 0.502 |
| Trading-Hero | `Trading-Hero_sentiment_score` | 0.543 | `Trading-Hero_Body_sentiment_score` | 0.603 |
| distilroberta | `distilroberta_sentiment_score` | 0.544 | `distilroberta_Body_sentiment_score` | 0.546 |
| finbert-tone | `finbert-tone_sentiment_score` | 0.518 | `finbert-tone_Body_sentiment_score` | 0.529 |
