# Per-class predictability — BroadClass × 11 single columns — PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv

- Rows kept: **3253**; classes with >= 40 rows: 12 of 12
- Thresholds: primary **>= 30%**, companion >= 15%
- Columns auto-oriented on the full corpus; `(inv)` = used negated. Within-class AUC < 0.5 then means the signal reverses in that class.
- `~` marks cells with < 8 movers in the class (unreliable).

## Within-class AUC matrix — movers = fromPR_High(%) >= 30%

| class | n | movers | base | FinMarBa_Body_ | FLANG-BERT_Bod | Trading-Hero_B | FLANG-ELECTRA_ | DeBERTa_Body_s | ModernBERT_Bod | distilroberta_ | finbert-tone_B | RoBERTa_Body_s | FinancialBERT_ | LouMcD-SDict_B |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 683 | 66 | 9.7% | 0.53 | 0.54 | 0.54 | 0.59 | 0.44 | 0.47 | 0.47 | 0.44 | 0.60 | 0.49 | 0.54 |
| Earnings | 508 | 26 | 5.1% | 0.46 | 0.40 | 0.72 | 0.67 | 0.74 | 0.65 | 0.74 | 0.69 | 0.40 | 0.69 | 0.52 |
| Corporate | 373 | 21 | 5.6% | 0.60 | 0.61 | 0.60 | 0.60 | 0.57 | 0.61 | 0.59 | 0.61 | 0.39 | 0.62 | 0.47 |
| Other | 328 | 19 | 5.8% | 0.63 | 0.61 | 0.64 | 0.62 | 0.52 | 0.55 | 0.57 | 0.53 | 0.48 | 0.58 | 0.63 |
| FDA | 287 | 19 | 6.6% | 0.61 | 0.60 | 0.61 | 0.67 | 0.63 | 0.65 | 0.62 | 0.59 | 0.38 | 0.66 | 0.53 |
| MA | 277 | 45 | 16.2% | 0.61 | 0.61 | 0.63 | 0.50 | 0.51 | 0.58 | 0.50 | 0.50 | 0.49 | 0.59 | 0.55 |
| Clinical | 257 | 24 | 9.3% | 0.59 | 0.60 | 0.54 | 0.49 | 0.58 | 0.67 | 0.61 | 0.61 | 0.49 | 0.61 | 0.55 |
| Financing | 229 | 38 | 16.6% | 0.51 | 0.52 | 0.61 | 0.52 | 0.49 | 0.51 | 0.52 | 0.56 | 0.52 | 0.50 | 0.56 |
| Compliance | 127 | 15 | 11.8% | 0.46 | 0.42 | 0.52 | 0.56 | 0.63 | 0.55 | 0.65 | 0.49 | 0.35 | 0.55 | 0.50 |
| IP | 69 | 9 | 13.0% | 0.52 | 0.52 | 0.60 | 0.71 | 0.56 | 0.75 | 0.64 | 0.65 | 0.47 | 0.64 | 0.60 |
| Crypto | 65 | 5 | 7.7% | 0.85~ | 0.67~ | 0.70~ | 0.57~ | 0.55~ | 0.78~ | 0.61~ | 0.66~ | 0.43~ | 0.69~ | 0.72~ |
| Legal | 50 | 0 | 0.0% | — | — | — | — | — | — | — | — | — | — | — |

## Within-class AUC matrix — movers = fromPR_High(%) >= 15%

| class | n | movers | base | FinMarBa_Body_ | FLANG-BERT_Bod | Trading-Hero_B | FLANG-ELECTRA_ | DeBERTa_Body_s | ModernBERT_Bod | distilroberta_ | finbert-tone_B | RoBERTa_Body_s | FinancialBERT_ | LouMcD-SDict_B |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 683 | 151 | 22.1% | 0.55 | 0.57 | 0.56 | 0.55 | 0.47 | 0.47 | 0.51 | 0.48 | 0.56 | 0.51 | 0.52 |
| Earnings | 508 | 80 | 15.7% | 0.48 | 0.48 | 0.69 | 0.66 | 0.69 | 0.65 | 0.71 | 0.66 | 0.39 | 0.66 | 0.51 |
| Corporate | 373 | 44 | 11.8% | 0.59 | 0.58 | 0.65 | 0.64 | 0.63 | 0.65 | 0.66 | 0.66 | 0.39 | 0.65 | 0.50 |
| Other | 328 | 45 | 13.7% | 0.58 | 0.60 | 0.61 | 0.60 | 0.52 | 0.55 | 0.54 | 0.53 | 0.54 | 0.52 | 0.55 |
| FDA | 287 | 49 | 17.1% | 0.58 | 0.56 | 0.57 | 0.60 | 0.59 | 0.59 | 0.58 | 0.55 | 0.45 | 0.64 | 0.49 |
| MA | 277 | 87 | 31.4% | 0.55 | 0.61 | 0.59 | 0.52 | 0.54 | 0.57 | 0.53 | 0.52 | 0.43 | 0.56 | 0.57 |
| Clinical | 257 | 52 | 20.2% | 0.57 | 0.57 | 0.56 | 0.50 | 0.57 | 0.55 | 0.57 | 0.54 | 0.49 | 0.58 | 0.52 |
| Financing | 229 | 77 | 33.6% | 0.49 | 0.55 | 0.64 | 0.51 | 0.47 | 0.54 | 0.53 | 0.55 | 0.50 | 0.54 | 0.56 |
| Compliance | 127 | 35 | 27.6% | 0.45 | 0.47 | 0.57 | 0.48 | 0.52 | 0.48 | 0.52 | 0.47 | 0.50 | 0.48 | 0.45 |
| IP | 69 | 13 | 18.8% | 0.57 | 0.46 | 0.58 | 0.70 | 0.53 | 0.68 | 0.60 | 0.67 | 0.46 | 0.61 | 0.59 |
| Crypto | 65 | 13 | 20.0% | 0.68 | 0.59 | 0.53 | 0.43 | 0.34 | 0.48 | 0.47 | 0.44 | 0.61 | 0.51 | 0.59 |
| Legal | 50 | 3 | 6.0% | 0.82~ | 0.70~ | 0.85~ | 0.68~ | 0.91~ | 0.86~ | 0.87~ | 0.66~ | 0.30~ | 0.66~ | 0.64~ |

## Class hardness — best achievable single-column AUC (>= 30%)

| class | n | movers | best column | best AUC | median AUC (14 cols) |
|---|---|---|---|---|---|
| Commercial | 683 | 66 | RoBERTa_Body_sentiment_score(inv) | 0.60 | 0.53 |
| Financing | 229 | 38 | Trading-Hero_Body_sentiment_score | 0.61 | 0.52 |
| Corporate | 373 | 21 | FinancialBERT_Body_sentiment_score | 0.62 | 0.60 |
| MA | 277 | 45 | Trading-Hero_Body_sentiment_score | 0.63 | 0.55 |
| Other | 328 | 19 | Trading-Hero_Body_sentiment_score | 0.64 | 0.58 |
| Compliance | 127 | 15 | distilroberta_Body_sentiment_score | 0.65 | 0.52 |
| FDA | 287 | 19 | FLANG-ELECTRA_Body_positive | 0.67 | 0.61 |
| Clinical | 257 | 24 | ModernBERT_Body_positive | 0.67 | 0.59 |
| Earnings | 508 | 26 | distilroberta_Body_sentiment_score | 0.74 | 0.67 |
| IP | 69 | 9 | ModernBERT_Body_positive | 0.75 | 0.60 |
| Crypto | 65 | 5 | FinMarBa_Body_indecisive(inv) | 0.85 ~ | 0.67 |
| Legal | 50 | 0 | — | — | — |

## Winners — within-class best column counts (classes with >= 8 movers, >= 30%)

- **Commercial** top-3: RoBERTa_Body_sentiment_score(inv) 0.60, FLANG-ELECTRA_Body_positive 0.59, Trading-Hero_Body_sentiment_score 0.54
- **Earnings** top-3: distilroberta_Body_sentiment_score 0.74, DeBERTa_Body_sentiment_score 0.74, Trading-Hero_Body_sentiment_score 0.72
- **Corporate** top-3: FinancialBERT_Body_sentiment_score 0.62, finbert-tone_Body_sentiment_score 0.61, FLANG-BERT_Body_negative(inv) 0.61
- **Other** top-3: Trading-Hero_Body_sentiment_score 0.64, LouMcD-SDict_Body_sentiment_score 0.63, FinMarBa_Body_indecisive(inv) 0.63
- **FDA** top-3: FLANG-ELECTRA_Body_positive 0.67, FinancialBERT_Body_sentiment_score 0.66, ModernBERT_Body_positive 0.65
- **MA** top-3: Trading-Hero_Body_sentiment_score 0.63, FLANG-BERT_Body_negative(inv) 0.61, FinMarBa_Body_indecisive(inv) 0.61
- **Clinical** top-3: ModernBERT_Body_positive 0.67, FinancialBERT_Body_sentiment_score 0.61, finbert-tone_Body_sentiment_score 0.61
- **Financing** top-3: Trading-Hero_Body_sentiment_score 0.61, LouMcD-SDict_Body_sentiment_score 0.56, finbert-tone_Body_sentiment_score 0.56
- **Compliance** top-3: distilroberta_Body_sentiment_score 0.65, DeBERTa_Body_sentiment_score 0.63, FLANG-ELECTRA_Body_positive 0.56
- **IP** top-3: ModernBERT_Body_positive 0.75, FLANG-ELECTRA_Body_positive 0.71, finbert-tone_Body_sentiment_score 0.65

| column | classes won | where |
|---|---|---|
| Trading-Hero_Body_sentiment_score | 3 | Other (0.64), MA (0.63), Financing (0.61) |
| ModernBERT_Body_positive | 2 | Clinical (0.67), IP (0.75) |
| distilroberta_Body_sentiment_score | 2 | Earnings (0.74), Compliance (0.65) |
| FLANG-ELECTRA_Body_positive | 1 | FDA (0.67) |
| RoBERTa_Body_sentiment_score(inv) | 1 | Commercial (0.60) |
| FinancialBERT_Body_sentiment_score | 1 | Corporate (0.62) |

## TP/FP per class — fixed triggers, movers >= 30% (cells: TP/FP = precision)

| class | movers | FinMarBa_Body_indecisive<=0.1 | Trading-Hero_Body_sentiment_score>=0.95 | FinancialBERT_Body_sentiment_score>=0.95 | ModernBERT_Body_positive>=0.95 |
|---|---|---|---|---|---|
| Commercial | 66 | 25/229 = 10% | 30/217 = 12% | 54/469 = 10% | 8/102 = 7% |
| Earnings | 26 | 1/12 = 8% | 20/185 = 10% | 23/248 = 8% | 8/77 = 9% |
| Corporate | 21 | 4/53 = 7% | 6/66 = 8% | 13/157 = 8% | 4/49 = 8% |
| Other | 19 | 7/48 = 13% | 8/67 = 11% | 14/181 = 7% | 3/50 = 6% |
| FDA | 19 | 4/45 = 8% | 5/49 = 9% | 13/118 = 10% | 4/30 = 12% |
| MA | 45 | 19/57 = 25% | 18/72 = 20% | 36/124 = 22% | 4/25 = 14% |
| Clinical | 24 | 4/26 = 13% | 6/32 = 16% | 19/123 = 13% | 8/22 = 27% |
| Financing | 38 | 3/17 = 15% | 4/17 = 19% | 7/51 = 12% | 1/11 = 8% |
| Compliance | 15 | 0/0 | 0/5 = 0% | 3/18 = 14% | 0/3 = 0% |
| IP | 9 | 2/11 = 15% | 2/15 = 12% | 7/37 = 16% | 0/3 = 0% |
| Crypto | 5 | 3/14 = 18% | 2/14 = 12% | 4/30 = 12% | 2/4 = 33% |
| Legal | 0 | 0/1 = 0% | 0/4 = 0% | 0/4 = 0% | 0/1 = 0% |
| <ALL> | 287 | 72/513 = 12% | 101/743 = 12% | 193/1560 = 11% | 42/377 = 10% |

