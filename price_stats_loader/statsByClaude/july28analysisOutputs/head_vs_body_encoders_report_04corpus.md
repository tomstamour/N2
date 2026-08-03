# Headline vs body — the 11 encoders on `fromPR_High(%)`

**Corpus:** `04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv` — 3432 rows with a target (outliers >500% dropped).
**Pairs compared:** 48 head/body channel pairs across 11 encoders.
**Bootstrap:** 2000 paired row resamples, seed 42.

Orientation is **a priori** from each channel's semantics, applied identically to both members of a pair — never fitted to the target. `(inv)` marks a channel used negated. AUC below 0.50 after that fixed orientation means the column runs backwards.


## Section A — paired AUC, movers = `fromPR_High(%) >= 15`

870 movers / 3432 rows = base rate 25.3%. Single-AUC Hanley-McNeil SE ≈ ±0.011.

Bonferroni column uses z = 3.28 (alpha 0.05 split over 48 pairs).

| encoder | channel | HEAD | BODY | Δ (body−head) | 95% CI (paired) | Bonferroni CI | rho(h,b) | verdict |
|---|---|---|---|---|---|---|---|---|
| FinancialBERT | `neutral` (inv) | 0.490 | 0.613 | +0.123 | [+0.095, +0.152] | [+0.075, +0.172] | 0.05 | **body wins** |
| FinancialBERT | `positive`  | 0.502 | 0.618 | +0.116 | [+0.088, +0.144] | [+0.069, +0.163] | 0.08 | **body wins** |
| FinancialBERT | `sentiment_score`  | 0.509 | 0.619 | +0.109 | [+0.082, +0.137] | [+0.063, +0.156] | 0.09 | **body wins** |
| FinancialBERT | `negative` (inv) | 0.511 | 0.590 | +0.080 | [+0.053, +0.107] | [+0.035, +0.124] | 0.01 | **body wins** |
| DeBERTa | `negative` (inv) | 0.499 | 0.554 | +0.055 | [+0.030, +0.081] | [+0.012, +0.098] | 0.28 | **body wins** |
| distilroberta | `negative` (inv) | 0.424 | 0.476 | +0.052 | [+0.025, +0.079] | [+0.006, +0.097] | 0.13 | **body wins** |
| LouMcD-SDict | `strong_modal` (inv) | 0.500 | 0.546 | +0.046 | [+0.026, +0.065] | [+0.014, +0.078] | 0.06 | **body wins** |
| ModernBERT | `negative` (inv) | 0.529 | 0.563 | +0.034 | [+0.007, +0.060] | [-0.011, +0.079] | 0.27 | body (nominal only) |
| FLANG-ELECTRA | `negative` (inv) | 0.419 | 0.452 | +0.033 | [+0.007, +0.060] | [-0.012, +0.079] | 0.25 | body (nominal only) |
| LouMcD-SDict | `weak_modal` (inv) | 0.511 | 0.537 | +0.026 | [+0.007, +0.043] | [-0.003, +0.055] | 0.27 | body (nominal only) |
| finbert-tone | `negative` (inv) | 0.520 | 0.528 | +0.007 | [-0.012, +0.027] | [-0.025, +0.040] | 0.33 | tie |
| LouMcD-SDict | `positive`  | 0.523 | 0.529 | +0.006 | [-0.017, +0.028] | [-0.032, +0.045] | 0.27 | tie |
| LouMcD-SDict | `litigious` (inv) | 0.496 | 0.500 | +0.004 | [-0.012, +0.020] | [-0.022, +0.031] | 0.40 | tie |
| LouMcD-SDict | `negative` (inv) | 0.516 | 0.518 | +0.003 | [-0.017, +0.022] | [-0.030, +0.035] | 0.38 | tie |
| LouMcD-SDict | `constraining` (inv) | 0.497 | 0.495 | -0.002 | [-0.020, +0.014] | [-0.031, +0.027] | 0.18 | tie |
| LouMcD-SDict | `sentiment_score`  | 0.534 | 0.530 | -0.004 | [-0.027, +0.019] | [-0.043, +0.036] | 0.24 | tie |
| distilroberta | `positive`  | 0.621 | 0.611 | -0.010 | [-0.035, +0.013] | [-0.050, +0.030] | 0.41 | tie |
| distilroberta | `sentiment_score`  | 0.625 | 0.613 | -0.012 | [-0.036, +0.011] | [-0.051, +0.027] | 0.45 | tie |
| LouMcD-SDict | `uncertainty` (inv) | 0.499 | 0.486 | -0.013 | [-0.034, +0.008] | [-0.048, +0.022] | 0.26 | tie |
| Trading-Hero | `sentiment_score`  | 0.636 | 0.623 | -0.013 | [-0.039, +0.012] | [-0.055, +0.029] | 0.36 | tie |
| finbert-tone | `sentiment_score`  | 0.598 | 0.584 | -0.014 | [-0.040, +0.009] | [-0.054, +0.026] | 0.37 | tie |
| distilroberta | `neutral` (inv) | 0.612 | 0.596 | -0.016 | [-0.042, +0.008] | [-0.058, +0.025] | 0.35 | tie |
| Trading-Hero | `positive`  | 0.639 | 0.619 | -0.019 | [-0.044, +0.005] | [-0.061, +0.023] | 0.33 | tie |
| finbert-tone | `positive`  | 0.597 | 0.576 | -0.021 | [-0.046, +0.002] | [-0.061, +0.020] | 0.36 | tie |
| FLANG-BERT | `negative` (inv) | 0.593 | 0.572 | -0.021 | [-0.049, +0.005] | [-0.065, +0.023] | 0.24 | tie |
| FLANG-BERT | `neutral` (inv) | 0.603 | 0.581 | -0.022 | [-0.048, +0.003] | [-0.065, +0.021] | 0.31 | tie |
| finbert-tone | `neutral` (inv) | 0.587 | 0.563 | -0.023 | [-0.049, +0.000] | [-0.065, +0.018] | 0.34 | tie |
| ModernBERT | `positive`  | 0.609 | 0.583 | -0.026 | [-0.052, -0.000] | [-0.069, +0.017] | 0.35 | head (nominal only) |
| ModernBERT | `sentiment_score`  | 0.607 | 0.580 | -0.027 | [-0.052, -0.002] | [-0.069, +0.015] | 0.37 | head (nominal only) |
| Trading-Hero | `neutral` (inv) | 0.630 | 0.602 | -0.028 | [-0.054, -0.003] | [-0.072, +0.015] | 0.31 | head (nominal only) |
| FLANG-ELECTRA | `positive`  | 0.641 | 0.612 | -0.029 | [-0.052, -0.006] | [-0.067, +0.010] | 0.43 | head (nominal only) |
| FinMarBa | `indecisive` (inv) | 0.636 | 0.606 | -0.030 | [-0.053, -0.006] | [-0.069, +0.009] | 0.44 | head (nominal only) |
| FLANG-ELECTRA | `neutral` (inv) | 0.628 | 0.590 | -0.038 | [-0.063, -0.014] | [-0.080, +0.003] | 0.38 | head (nominal only) |
| ModernBERT | `neutral` (inv) | 0.604 | 0.565 | -0.039 | [-0.066, -0.013] | [-0.084, +0.006] | 0.23 | head (nominal only) |
| FLANG-ELECTRA | `sentiment_score`  | 0.643 | 0.600 | -0.043 | [-0.068, -0.021] | [-0.083, -0.004] | 0.43 | head wins |
| RoBERTa | `neutral` (inv) | 0.573 | 0.528 | -0.045 | [-0.070, -0.020] | [-0.088, -0.002] | 0.38 | head wins |
| FinMarBa | `positive`  | 0.608 | 0.562 | -0.047 | [-0.071, -0.022] | [-0.087, -0.006] | 0.41 | head wins |
| FLANG-BERT | `sentiment_score`  | 0.642 | 0.595 | -0.047 | [-0.074, -0.023] | [-0.089, -0.005] | 0.38 | head wins |
| DeBERTa | `positive`  | 0.638 | 0.590 | -0.048 | [-0.073, -0.025] | [-0.087, -0.008] | 0.44 | head wins |
| RoBERTa | `negative` (inv) | 0.534 | 0.486 | -0.048 | [-0.071, -0.023] | [-0.088, -0.007] | 0.39 | head wins |
| FinMarBa | `sentiment_score`  | 0.598 | 0.550 | -0.048 | [-0.073, -0.023] | [-0.090, -0.007] | 0.39 | head wins |
| RoBERTa | `sentiment_score`  | 0.580 | 0.530 | -0.049 | [-0.074, -0.025] | [-0.090, -0.008] | 0.45 | head wins |
| FLANG-BERT | `positive`  | 0.640 | 0.590 | -0.049 | [-0.075, -0.026] | [-0.092, -0.007] | 0.39 | head wins |
| DeBERTa | `sentiment_score`  | 0.638 | 0.589 | -0.050 | [-0.074, -0.028] | [-0.088, -0.011] | 0.48 | head wins |
| FinMarBa | `negative` (inv) | 0.582 | 0.532 | -0.050 | [-0.075, -0.024] | [-0.092, -0.008] | 0.36 | head wins |
| RoBERTa | `positive`  | 0.582 | 0.531 | -0.051 | [-0.075, -0.027] | [-0.091, -0.010] | 0.46 | head wins |
| Trading-Hero | `negative` (inv) | 0.615 | 0.551 | -0.064 | [-0.091, -0.038] | [-0.110, -0.018] | 0.21 | head wins |
| DeBERTa | `neutral` (inv) | 0.628 | 0.559 | -0.069 | [-0.095, -0.044] | [-0.111, -0.027] | 0.34 | head wins |

**Tally at Bonferroni:** body wins 7, head wins 14, tie 27, of 48 pairs.

## Section A — paired AUC, movers = `fromPR_High(%) >= 30`

419 movers / 3432 rows = base rate 12.2%. Single-AUC Hanley-McNeil SE ≈ ±0.015.

Bonferroni column uses z = 3.28 (alpha 0.05 split over 48 pairs).

| encoder | channel | HEAD | BODY | Δ (body−head) | 95% CI (paired) | Bonferroni CI | rho(h,b) | verdict |
|---|---|---|---|---|---|---|---|---|
| FinancialBERT | `neutral` (inv) | 0.488 | 0.611 | +0.123 | [+0.086, +0.162] | [+0.060, +0.187] | 0.05 | **body wins** |
| FinancialBERT | `positive`  | 0.510 | 0.616 | +0.106 | [+0.070, +0.145] | [+0.043, +0.169] | 0.08 | **body wins** |
| FinancialBERT | `sentiment_score`  | 0.522 | 0.617 | +0.095 | [+0.059, +0.132] | [+0.033, +0.156] | 0.09 | **body wins** |
| FinancialBERT | `negative` (inv) | 0.527 | 0.595 | +0.068 | [+0.032, +0.103] | [+0.009, +0.126] | 0.01 | **body wins** |
| DeBERTa | `negative` (inv) | 0.469 | 0.528 | +0.058 | [+0.026, +0.093] | [+0.002, +0.115] | 0.28 | **body wins** |
| FLANG-ELECTRA | `negative` (inv) | 0.407 | 0.454 | +0.047 | [+0.011, +0.082] | [-0.011, +0.106] | 0.25 | body (nominal only) |
| LouMcD-SDict | `strong_modal` (inv) | 0.499 | 0.530 | +0.031 | [+0.005, +0.056] | [-0.012, +0.074] | 0.06 | body (nominal only) |
| distilroberta | `negative` (inv) | 0.431 | 0.462 | +0.031 | [-0.007, +0.068] | [-0.030, +0.092] | 0.13 | tie |
| LouMcD-SDict | `weak_modal` (inv) | 0.513 | 0.535 | +0.022 | [-0.002, +0.045] | [-0.017, +0.061] | 0.27 | tie |
| ModernBERT | `negative` (inv) | 0.534 | 0.551 | +0.016 | [-0.022, +0.055] | [-0.048, +0.081] | 0.27 | tie |
| LouMcD-SDict | `negative` (inv) | 0.520 | 0.534 | +0.015 | [-0.011, +0.040] | [-0.028, +0.058] | 0.38 | tie |
| LouMcD-SDict | `constraining` (inv) | 0.496 | 0.503 | +0.007 | [-0.015, +0.029] | [-0.031, +0.044] | 0.18 | tie |
| LouMcD-SDict | `sentiment_score`  | 0.534 | 0.539 | +0.004 | [-0.028, +0.035] | [-0.049, +0.057] | 0.24 | tie |
| LouMcD-SDict | `litigious` (inv) | 0.508 | 0.509 | +0.001 | [-0.021, +0.020] | [-0.034, +0.035] | 0.40 | tie |
| finbert-tone | `negative` (inv) | 0.520 | 0.515 | -0.005 | [-0.030, +0.020] | [-0.047, +0.037] | 0.33 | tie |
| LouMcD-SDict | `positive`  | 0.522 | 0.515 | -0.007 | [-0.039, +0.023] | [-0.060, +0.046] | 0.27 | tie |
| LouMcD-SDict | `uncertainty` (inv) | 0.506 | 0.491 | -0.015 | [-0.044, +0.014] | [-0.064, +0.034] | 0.26 | tie |
| Trading-Hero | `positive`  | 0.642 | 0.622 | -0.020 | [-0.053, +0.013] | [-0.076, +0.035] | 0.33 | tie |
| Trading-Hero | `sentiment_score`  | 0.646 | 0.624 | -0.023 | [-0.056, +0.011] | [-0.078, +0.032] | 0.36 | tie |
| FLANG-BERT | `negative` (inv) | 0.599 | 0.575 | -0.025 | [-0.058, +0.007] | [-0.081, +0.032] | 0.24 | tie |
| Trading-Hero | `neutral` (inv) | 0.633 | 0.603 | -0.029 | [-0.063, +0.005] | [-0.088, +0.029] | 0.31 | tie |
| distilroberta | `neutral` (inv) | 0.610 | 0.578 | -0.032 | [-0.066, +0.002] | [-0.088, +0.025] | 0.35 | tie |
| distilroberta | `positive`  | 0.626 | 0.593 | -0.033 | [-0.066, -0.001] | [-0.087, +0.021] | 0.41 | head (nominal only) |
| FinMarBa | `indecisive` (inv) | 0.652 | 0.613 | -0.039 | [-0.068, -0.008] | [-0.090, +0.011] | 0.44 | head (nominal only) |
| distilroberta | `sentiment_score`  | 0.635 | 0.595 | -0.040 | [-0.073, -0.010] | [-0.093, +0.012] | 0.45 | head (nominal only) |
| finbert-tone | `sentiment_score`  | 0.607 | 0.564 | -0.043 | [-0.076, -0.011] | [-0.097, +0.011] | 0.37 | head (nominal only) |
| finbert-tone | `neutral` (inv) | 0.592 | 0.546 | -0.047 | [-0.079, -0.015] | [-0.101, +0.008] | 0.34 | head (nominal only) |
| FLANG-BERT | `neutral` (inv) | 0.629 | 0.582 | -0.047 | [-0.082, -0.016] | [-0.102, +0.007] | 0.31 | head (nominal only) |
| finbert-tone | `positive`  | 0.606 | 0.558 | -0.048 | [-0.081, -0.017] | [-0.102, +0.006] | 0.36 | head (nominal only) |
| FLANG-ELECTRA | `positive`  | 0.655 | 0.597 | -0.057 | [-0.089, -0.026] | [-0.110, -0.005] | 0.43 | head wins |
| RoBERTa | `neutral` (inv) | 0.573 | 0.515 | -0.058 | [-0.091, -0.023] | [-0.115, -0.001] | 0.38 | head wins |
| ModernBERT | `sentiment_score`  | 0.629 | 0.571 | -0.058 | [-0.092, -0.022] | [-0.116, -0.000] | 0.37 | head wins |
| ModernBERT | `positive`  | 0.633 | 0.574 | -0.059 | [-0.093, -0.024] | [-0.117, -0.001] | 0.35 | head wins |
| FLANG-ELECTRA | `neutral` (inv) | 0.641 | 0.578 | -0.064 | [-0.095, -0.030] | [-0.120, -0.007] | 0.38 | head wins |
| FLANG-BERT | `sentiment_score`  | 0.661 | 0.596 | -0.065 | [-0.099, -0.030] | [-0.122, -0.009] | 0.38 | head wins |
| RoBERTa | `negative` (inv) | 0.553 | 0.487 | -0.066 | [-0.097, -0.034] | [-0.118, -0.013] | 0.39 | head wins |
| Trading-Hero | `negative` (inv) | 0.627 | 0.561 | -0.066 | [-0.103, -0.030] | [-0.127, -0.005] | 0.21 | head wins |
| ModernBERT | `neutral` (inv) | 0.623 | 0.555 | -0.069 | [-0.105, -0.032] | [-0.129, -0.008] | 0.23 | head wins |
| FLANG-BERT | `positive`  | 0.660 | 0.591 | -0.070 | [-0.103, -0.036] | [-0.126, -0.013] | 0.39 | head wins |
| FinMarBa | `positive`  | 0.620 | 0.547 | -0.072 | [-0.103, -0.041] | [-0.126, -0.019] | 0.41 | head wins |
| FLANG-ELECTRA | `sentiment_score`  | 0.653 | 0.581 | -0.072 | [-0.104, -0.042] | [-0.124, -0.021] | 0.43 | head wins |
| FinMarBa | `sentiment_score`  | 0.608 | 0.532 | -0.075 | [-0.107, -0.043] | [-0.129, -0.021] | 0.39 | head wins |
| RoBERTa | `sentiment_score`  | 0.592 | 0.516 | -0.077 | [-0.108, -0.044] | [-0.130, -0.023] | 0.45 | head wins |
| RoBERTa | `positive`  | 0.593 | 0.516 | -0.077 | [-0.108, -0.044] | [-0.131, -0.023] | 0.46 | head wins |
| FinMarBa | `negative` (inv) | 0.590 | 0.510 | -0.080 | [-0.112, -0.048] | [-0.135, -0.025] | 0.36 | head wins |
| DeBERTa | `sentiment_score`  | 0.649 | 0.568 | -0.080 | [-0.111, -0.048] | [-0.133, -0.027] | 0.48 | head wins |
| DeBERTa | `positive`  | 0.650 | 0.569 | -0.081 | [-0.113, -0.048] | [-0.135, -0.027] | 0.44 | head wins |
| DeBERTa | `neutral` (inv) | 0.636 | 0.535 | -0.101 | [-0.136, -0.068] | [-0.158, -0.043] | 0.34 | head wins |

**Tally at Bonferroni:** body wins 5, head wins 19, tie 24, of 48 pairs.

## Section A — paired AUC, movers = `fromPR_High(%) >= 40`

319 movers / 3432 rows = base rate 9.3%. Single-AUC Hanley-McNeil SE ≈ ±0.017.

Bonferroni column uses z = 3.28 (alpha 0.05 split over 48 pairs).

| encoder | channel | HEAD | BODY | Δ (body−head) | 95% CI (paired) | Bonferroni CI | rho(h,b) | verdict |
|---|---|---|---|---|---|---|---|---|
| FinancialBERT | `neutral` (inv) | 0.480 | 0.620 | +0.140 | [+0.101, +0.183] | [+0.069, +0.212] | 0.05 | **body wins** |
| FinancialBERT | `positive`  | 0.503 | 0.626 | +0.123 | [+0.083, +0.166] | [+0.053, +0.192] | 0.08 | **body wins** |
| FinancialBERT | `sentiment_score`  | 0.516 | 0.626 | +0.110 | [+0.071, +0.151] | [+0.042, +0.177] | 0.09 | **body wins** |
| FinancialBERT | `negative` (inv) | 0.533 | 0.602 | +0.069 | [+0.030, +0.105] | [+0.005, +0.133] | 0.01 | **body wins** |
| DeBERTa | `negative` (inv) | 0.467 | 0.528 | +0.061 | [+0.025, +0.098] | [-0.002, +0.124] | 0.28 | body (nominal only) |
| FLANG-ELECTRA | `negative` (inv) | 0.413 | 0.446 | +0.033 | [-0.005, +0.072] | [-0.034, +0.100] | 0.25 | tie |
| ModernBERT | `negative` (inv) | 0.520 | 0.552 | +0.032 | [-0.011, +0.075] | [-0.041, +0.104] | 0.27 | tie |
| LouMcD-SDict | `strong_modal` (inv) | 0.498 | 0.529 | +0.031 | [+0.001, +0.058] | [-0.017, +0.078] | 0.06 | body (nominal only) |
| distilroberta | `negative` (inv) | 0.424 | 0.452 | +0.028 | [-0.014, +0.069] | [-0.041, +0.097] | 0.13 | tie |
| LouMcD-SDict | `weak_modal` (inv) | 0.511 | 0.537 | +0.026 | [-0.000, +0.052] | [-0.018, +0.069] | 0.27 | tie |
| LouMcD-SDict | `negative` (inv) | 0.516 | 0.535 | +0.019 | [-0.010, +0.048] | [-0.028, +0.067] | 0.38 | tie |
| LouMcD-SDict | `sentiment_score`  | 0.532 | 0.539 | +0.007 | [-0.028, +0.042] | [-0.052, +0.066] | 0.24 | tie |
| LouMcD-SDict | `constraining` (inv) | 0.495 | 0.497 | +0.002 | [-0.023, +0.027] | [-0.041, +0.045] | 0.18 | tie |
| LouMcD-SDict | `litigious` (inv) | 0.509 | 0.511 | +0.001 | [-0.022, +0.024] | [-0.038, +0.041] | 0.40 | tie |
| finbert-tone | `negative` (inv) | 0.516 | 0.511 | -0.005 | [-0.036, +0.023] | [-0.055, +0.044] | 0.33 | tie |
| LouMcD-SDict | `uncertainty` (inv) | 0.506 | 0.498 | -0.008 | [-0.042, +0.024] | [-0.063, +0.047] | 0.26 | tie |
| Trading-Hero | `positive`  | 0.642 | 0.629 | -0.013 | [-0.050, +0.024] | [-0.075, +0.049] | 0.33 | tie |
| LouMcD-SDict | `positive`  | 0.524 | 0.507 | -0.017 | [-0.051, +0.017] | [-0.074, +0.040] | 0.27 | tie |
| Trading-Hero | `sentiment_score`  | 0.646 | 0.628 | -0.018 | [-0.054, +0.020] | [-0.080, +0.045] | 0.36 | tie |
| Trading-Hero | `neutral` (inv) | 0.630 | 0.609 | -0.021 | [-0.060, +0.018] | [-0.086, +0.043] | 0.31 | tie |
| FLANG-BERT | `negative` (inv) | 0.603 | 0.579 | -0.024 | [-0.062, +0.011] | [-0.086, +0.039] | 0.24 | tie |
| distilroberta | `neutral` (inv) | 0.610 | 0.580 | -0.030 | [-0.065, +0.007] | [-0.092, +0.032] | 0.35 | tie |
| distilroberta | `positive`  | 0.631 | 0.593 | -0.038 | [-0.072, -0.003] | [-0.096, +0.020] | 0.41 | head (nominal only) |
| finbert-tone | `sentiment_score`  | 0.608 | 0.566 | -0.042 | [-0.077, -0.005] | [-0.102, +0.019] | 0.37 | head (nominal only) |
| finbert-tone | `neutral` (inv) | 0.594 | 0.549 | -0.045 | [-0.081, -0.009] | [-0.106, +0.016] | 0.34 | head (nominal only) |
| finbert-tone | `positive`  | 0.606 | 0.560 | -0.046 | [-0.082, -0.010] | [-0.107, +0.015] | 0.36 | head (nominal only) |
| distilroberta | `sentiment_score`  | 0.639 | 0.592 | -0.047 | [-0.081, -0.014] | [-0.103, +0.010] | 0.45 | head (nominal only) |
| ModernBERT | `sentiment_score`  | 0.626 | 0.576 | -0.050 | [-0.086, -0.011] | [-0.113, +0.014] | 0.37 | head (nominal only) |
| ModernBERT | `positive`  | 0.632 | 0.581 | -0.052 | [-0.090, -0.013] | [-0.115, +0.012] | 0.35 | head (nominal only) |
| FinMarBa | `indecisive` (inv) | 0.663 | 0.607 | -0.056 | [-0.088, -0.022] | [-0.111, -0.002] | 0.44 | head wins |
| FLANG-ELECTRA | `positive`  | 0.654 | 0.596 | -0.058 | [-0.093, -0.022] | [-0.116, +0.001] | 0.43 | head (nominal only) |
| FLANG-BERT | `neutral` (inv) | 0.647 | 0.588 | -0.059 | [-0.095, -0.023] | [-0.118, +0.001] | 0.31 | head (nominal only) |
| FLANG-ELECTRA | `neutral` (inv) | 0.644 | 0.584 | -0.059 | [-0.096, -0.021] | [-0.122, +0.004] | 0.38 | head (nominal only) |
| Trading-Hero | `negative` (inv) | 0.620 | 0.560 | -0.060 | [-0.101, -0.019] | [-0.129, +0.008] | 0.21 | head (nominal only) |
| ModernBERT | `neutral` (inv) | 0.626 | 0.562 | -0.064 | [-0.105, -0.024] | [-0.131, +0.004] | 0.23 | head (nominal only) |
| RoBERTa | `neutral` (inv) | 0.579 | 0.514 | -0.065 | [-0.102, -0.026] | [-0.128, -0.002] | 0.38 | head wins |
| RoBERTa | `negative` (inv) | 0.556 | 0.485 | -0.071 | [-0.106, -0.034] | [-0.130, -0.011] | 0.39 | head wins |
| FLANG-BERT | `sentiment_score`  | 0.677 | 0.600 | -0.077 | [-0.113, -0.040] | [-0.139, -0.015] | 0.38 | head wins |
| RoBERTa | `sentiment_score`  | 0.597 | 0.517 | -0.080 | [-0.115, -0.042] | [-0.140, -0.019] | 0.45 | head wins |
| FLANG-BERT | `positive`  | 0.677 | 0.596 | -0.080 | [-0.116, -0.042] | [-0.142, -0.018] | 0.39 | head wins |
| RoBERTa | `positive`  | 0.597 | 0.517 | -0.080 | [-0.116, -0.042] | [-0.141, -0.020] | 0.46 | head wins |
| FLANG-ELECTRA | `sentiment_score`  | 0.658 | 0.576 | -0.081 | [-0.115, -0.047] | [-0.138, -0.025] | 0.43 | head wins |
| DeBERTa | `sentiment_score`  | 0.653 | 0.571 | -0.082 | [-0.118, -0.046] | [-0.141, -0.024] | 0.48 | head wins |
| DeBERTa | `positive`  | 0.655 | 0.571 | -0.084 | [-0.121, -0.048] | [-0.144, -0.025] | 0.44 | head wins |
| FinMarBa | `positive`  | 0.627 | 0.536 | -0.091 | [-0.125, -0.055] | [-0.150, -0.031] | 0.41 | head wins |
| FinMarBa | `sentiment_score`  | 0.614 | 0.521 | -0.094 | [-0.129, -0.056] | [-0.154, -0.033] | 0.39 | head wins |
| FinMarBa | `negative` (inv) | 0.595 | 0.498 | -0.096 | [-0.133, -0.059] | [-0.158, -0.035] | 0.36 | head wins |
| DeBERTa | `neutral` (inv) | 0.643 | 0.537 | -0.105 | [-0.145, -0.068] | [-0.170, -0.041] | 0.34 | head wins |

**Tally at Bonferroni:** body wins 4, head wins 14, tie 30, of 48 pairs.

## Section B — does body ADD on top of headline? (movers >= 15)

Pooled **out-of-fold** logistic AUC, 5 folds, features percentile-ranked then standardized per fold on the train fold only. Every number in this table is computed the same way — **the singles are pooled-OOF too**, not the fit-free rank AUCs of Section A, so the pair is not charged a cross-validation penalty its baselines avoided. Synergy = pair − better OOF single; `rank A` columns repeat Section A's fit-free AUCs for reference.

| encoder | head col | body col | rank A head | rank A body | OOF head | OOF body | OOF head+body | synergy | rho |
|---|---|---|---|---|---|---|---|---|---|
| Trading-Hero | `Trading-Hero_positive` | `Trading-Hero_Body_sentiment_score` | 0.639 | 0.623 | 0.637 | 0.622 | **0.660** | +0.023 | 0.34 |
| FLANG-ELECTRA | `FLANG-ELECTRA_sentiment_score` | `FLANG-ELECTRA_Body_positive` | 0.643 | 0.612 | 0.640 | 0.606 | **0.653** | +0.012 | 0.45 |
| FLANG-BERT | `FLANG-BERT_sentiment_score` | `FLANG-BERT_Body_sentiment_score` | 0.642 | 0.595 | 0.639 | 0.593 | **0.646** | +0.007 | 0.38 |
| FinMarBa | `FinMarBa_indecisive` | `FinMarBa_Body_indecisive` | 0.636 | 0.606 | 0.634 | 0.603 | **0.643** | +0.008 | 0.44 |
| distilroberta | `distilroberta_sentiment_score` | `distilroberta_Body_sentiment_score` | 0.625 | 0.613 | 0.623 | 0.610 | **0.642** | +0.019 | 0.45 |
| DeBERTa | `DeBERTa_sentiment_score` | `DeBERTa_Body_positive` | 0.638 | 0.590 | 0.637 | 0.587 | **0.639** | +0.002 | 0.47 |
| ModernBERT | `ModernBERT_positive` | `ModernBERT_Body_positive` | 0.609 | 0.583 | 0.606 | 0.580 | **0.617** | +0.011 | 0.35 |
| FinancialBERT | `FinancialBERT_negative` | `FinancialBERT_Body_sentiment_score` | 0.511 | 0.619 | 0.493 | 0.617 | **0.616** | -0.000 | 0.06 |
| finbert-tone | `finbert-tone_sentiment_score` | `finbert-tone_Body_sentiment_score` | 0.598 | 0.584 | 0.595 | 0.581 | **0.609** | +0.015 | 0.37 |
| RoBERTa | `RoBERTa_positive` | `RoBERTa_Body_positive` | 0.582 | 0.531 | 0.578 | 0.525 | **0.578** | +0.001 | 0.46 |
| LouMcD-SDict | `LouMcD-SDict_sentiment_score` | `LouMcD-SDict_Body_strong_modal` | 0.534 | 0.546 | 0.520 | 0.532 | **0.552** | +0.020 | 0.05 |

**Encoders where body adds more than +0.01 AUC over the better single: 6 of 11.**

**Cross-encoder best pair:** `FLANG-ELECTRA_sentiment_score` (OOF 0.640) + `Trading-Hero_Body_sentiment_score` (OOF 0.622) → OOF AUC **0.660** (synergy +0.019, rho 0.40).

## Stability check — rows with ArrivalDate >= 2026-06-20

265 movers / 785 rows = 33.8% base. All 11 encoders are externally trained, so the FULL corpus above is their valid test set — this block is a small-sample stability check only, never the primary number.

| encoder | best head | AUC | best body | AUC |
|---|---|---|---|---|
| DeBERTa | `DeBERTa_sentiment_score` | 0.611 | `DeBERTa_Body_positive` | 0.556 |
| FLANG-BERT | `FLANG-BERT_sentiment_score` | 0.633 | `FLANG-BERT_Body_sentiment_score` | 0.601 |
| FLANG-ELECTRA | `FLANG-ELECTRA_sentiment_score` | 0.645 | `FLANG-ELECTRA_Body_positive` | 0.589 |
| FinMarBa | `FinMarBa_indecisive` | 0.602 | `FinMarBa_Body_indecisive` | 0.624 |
| FinancialBERT | `FinancialBERT_negative` | 0.476 | `FinancialBERT_Body_sentiment_score` | 0.618 |
| LouMcD-SDict | `LouMcD-SDict_sentiment_score` | 0.519 | `LouMcD-SDict_Body_strong_modal` | 0.526 |
| ModernBERT | `ModernBERT_positive` | 0.564 | `ModernBERT_Body_positive` | 0.571 |
| RoBERTa | `RoBERTa_positive` | 0.591 | `RoBERTa_Body_positive` | 0.528 |
| Trading-Hero | `Trading-Hero_positive` | 0.621 | `Trading-Hero_Body_sentiment_score` | 0.614 |
| distilroberta | `distilroberta_sentiment_score` | 0.597 | `distilroberta_Body_sentiment_score` | 0.597 |
| finbert-tone | `finbert-tone_sentiment_score` | 0.565 | `finbert-tone_Body_sentiment_score` | 0.581 |
