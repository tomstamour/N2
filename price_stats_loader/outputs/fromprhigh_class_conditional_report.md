# Per-class predictability — BroadClass × 14 single columns — PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored.tsv

- Rows kept: **3253**; classes with >= 40 rows: 12 of 12
- Thresholds: primary **>= 30%**, companion >= 15%
- Columns auto-oriented on the full corpus; `(inv)` = used negated. Within-class AUC < 0.5 then means the signal reverses in that class.
- `~` marks cells with < 8 movers in the class (unreliable).

## Within-class AUC matrix — movers = fromPR_High(%) >= 30%

| class | n | movers | base | FinMarBa_indec | positive | FLANG-BERT_pos | FLANG-ELECTRA_ | Trading-Hero_s | DeBERTa_positi | sentiment_scor | ModernBERT_pos | nocoref_neutra | distilroberta_ | finbert-tone_s | RoBERTa_sentim | LouMcD-SDict_s | FinancialBERT_ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 683 | 66 | 9.7% | 0.55 | 0.59 | 0.59 | 0.62 | 0.58 | 0.54 | 0.59 | 0.58 | 0.50 | 0.59 | 0.55 | 0.52 | 0.53 | 0.53 |
| Earnings | 508 | 26 | 5.1% | 0.57 | 0.55 | 0.40 | 0.63 | 0.48 | 0.50 | 0.52 | 0.46 | 0.65 | 0.69 | 0.55 | 0.56 | 0.53 | 0.47 |
| Corporate | 373 | 21 | 5.6% | 0.68 | 0.63 | 0.68 | 0.58 | 0.55 | 0.68 | 0.64 | 0.64 | 0.63 | 0.58 | 0.64 | 0.61 | 0.55 | 0.57 |
| Other | 328 | 19 | 5.8% | 0.59 | 0.61 | 0.64 | 0.57 | 0.63 | 0.60 | 0.62 | 0.61 | 0.66 | 0.55 | 0.55 | 0.56 | 0.51 | 0.51 |
| FDA | 287 | 19 | 6.6% | 0.72 | 0.68 | 0.65 | 0.67 | 0.66 | 0.71 | 0.68 | 0.67 | 0.65 | 0.71 | 0.68 | 0.70 | 0.57 | 0.52 |
| MA | 277 | 45 | 16.2% | 0.65 | 0.56 | 0.59 | 0.59 | 0.59 | 0.55 | 0.56 | 0.59 | 0.62 | 0.56 | 0.56 | 0.55 | 0.48 | 0.43 |
| Clinical | 257 | 24 | 9.3% | 0.59 | 0.62 | 0.59 | 0.57 | 0.57 | 0.62 | 0.63 | 0.65 | 0.63 | 0.66 | 0.61 | 0.63 | 0.54 | 0.55 |
| Financing | 229 | 38 | 16.6% | 0.52 | 0.54 | 0.57 | 0.55 | 0.55 | 0.53 | 0.53 | 0.47 | 0.52 | 0.49 | 0.49 | 0.54 | 0.53 | 0.56 |
| Compliance | 127 | 15 | 11.8% | 0.61 | 0.57 | 0.51 | 0.68 | 0.51 | 0.61 | 0.52 | 0.65 | 0.49 | 0.55 | 0.57 | 0.48 | 0.53 | 0.56 |
| IP | 69 | 9 | 13.0% | 0.67 | 0.54 | 0.60 | 0.56 | 0.56 | 0.47 | 0.52 | 0.69 | 0.57 | 0.57 | 0.75 | 0.59 | 0.55 | 0.48 |
| Crypto | 65 | 5 | 7.7% | 0.87~ | 0.78~ | 0.73~ | 0.84~ | 0.76~ | 0.67~ | 0.70~ | 0.75~ | 0.89~ | 0.61~ | 0.64~ | 0.57~ | 0.71~ | 0.62~ |
| Legal | 50 | 0 | 0.0% | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

## Within-class AUC matrix — movers = fromPR_High(%) >= 15%

| class | n | movers | base | FinMarBa_indec | positive | FLANG-BERT_pos | FLANG-ELECTRA_ | Trading-Hero_s | DeBERTa_positi | sentiment_scor | ModernBERT_pos | nocoref_neutra | distilroberta_ | finbert-tone_s | RoBERTa_sentim | LouMcD-SDict_s | FinancialBERT_ |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 683 | 151 | 22.1% | 0.54 | 0.56 | 0.56 | 0.55 | 0.58 | 0.53 | 0.56 | 0.53 | 0.51 | 0.54 | 0.52 | 0.53 | 0.51 | 0.49 |
| Earnings | 508 | 80 | 15.7% | 0.59 | 0.59 | 0.50 | 0.59 | 0.51 | 0.48 | 0.55 | 0.50 | 0.60 | 0.61 | 0.58 | 0.50 | 0.52 | 0.47 |
| Corporate | 373 | 44 | 11.8% | 0.67 | 0.64 | 0.63 | 0.62 | 0.56 | 0.65 | 0.65 | 0.62 | 0.65 | 0.62 | 0.59 | 0.61 | 0.54 | 0.57 |
| Other | 328 | 45 | 13.7% | 0.57 | 0.58 | 0.60 | 0.55 | 0.56 | 0.60 | 0.58 | 0.62 | 0.53 | 0.56 | 0.55 | 0.57 | 0.55 | 0.53 |
| FDA | 287 | 49 | 17.1% | 0.69 | 0.64 | 0.62 | 0.60 | 0.60 | 0.62 | 0.65 | 0.63 | 0.62 | 0.63 | 0.59 | 0.58 | 0.52 | 0.55 |
| MA | 277 | 87 | 31.4% | 0.60 | 0.58 | 0.52 | 0.57 | 0.57 | 0.57 | 0.58 | 0.58 | 0.62 | 0.57 | 0.57 | 0.56 | 0.52 | 0.50 |
| Clinical | 257 | 52 | 20.2% | 0.63 | 0.61 | 0.59 | 0.61 | 0.65 | 0.59 | 0.61 | 0.63 | 0.61 | 0.61 | 0.62 | 0.56 | 0.54 | 0.55 |
| Financing | 229 | 77 | 33.6% | 0.57 | 0.59 | 0.53 | 0.58 | 0.55 | 0.52 | 0.59 | 0.53 | 0.57 | 0.55 | 0.50 | 0.58 | 0.53 | 0.48 |
| Compliance | 127 | 35 | 27.6% | 0.44 | 0.45 | 0.47 | 0.54 | 0.51 | 0.44 | 0.45 | 0.54 | 0.48 | 0.56 | 0.46 | 0.48 | 0.54 | 0.49 |
| IP | 69 | 13 | 18.8% | 0.56 | 0.51 | 0.63 | 0.52 | 0.56 | 0.45 | 0.49 | 0.65 | 0.54 | 0.53 | 0.72 | 0.57 | 0.53 | 0.47 |
| Crypto | 65 | 13 | 20.0% | 0.59 | 0.54 | 0.57 | 0.69 | 0.65 | 0.51 | 0.52 | 0.57 | 0.66 | 0.38 | 0.49 | 0.36 | 0.58 | 0.49 |
| Legal | 50 | 3 | 6.0% | 0.89~ | 0.58~ | 0.84~ | 0.81~ | 0.77~ | 0.59~ | 0.70~ | 0.41~ | 0.90~ | 0.67~ | 0.85~ | 0.79~ | 0.67~ | 0.64~ |

## Class hardness — best achievable single-column AUC (>= 30%)

| class | n | movers | best column | best AUC | median AUC (14 cols) |
|---|---|---|---|---|---|
| Financing | 229 | 38 | FLANG-BERT_positive | 0.57 | 0.53 |
| Commercial | 683 | 66 | FLANG-ELECTRA_sentiment_score | 0.62 | 0.57 |
| MA | 277 | 45 | FinMarBa_indecisive(inv) | 0.65 | 0.56 |
| Clinical | 257 | 24 | distilroberta_sentiment_score | 0.66 | 0.61 |
| Other | 328 | 19 | nocoref_neutral_filter | 0.66 | 0.59 |
| Compliance | 127 | 15 | FLANG-ELECTRA_sentiment_score | 0.68 | 0.55 |
| Corporate | 373 | 21 | FinMarBa_indecisive(inv) | 0.68 | 0.63 |
| Earnings | 508 | 26 | distilroberta_sentiment_score | 0.69 | 0.54 |
| FDA | 287 | 19 | FinMarBa_indecisive(inv) | 0.72 | 0.68 |
| IP | 69 | 9 | finbert-tone_sentiment_score | 0.75 | 0.57 |
| Crypto | 65 | 5 | nocoref_neutral_filter | 0.89 ~ | 0.72 |
| Legal | 50 | 0 | — | — | — |

## Winners — within-class best column counts (classes with >= 8 movers, >= 30%)

- **Commercial** top-3: FLANG-ELECTRA_sentiment_score 0.62, sentiment_score 0.59, FLANG-BERT_positive 0.59
- **Earnings** top-3: distilroberta_sentiment_score 0.69, nocoref_neutral_filter 0.65, FLANG-ELECTRA_sentiment_score 0.63
- **Corporate** top-3: FinMarBa_indecisive(inv) 0.68, DeBERTa_positive 0.68, FLANG-BERT_positive 0.68
- **Other** top-3: nocoref_neutral_filter 0.66, FLANG-BERT_positive 0.64, Trading-Hero_sentiment_score 0.63
- **FDA** top-3: FinMarBa_indecisive(inv) 0.72, distilroberta_sentiment_score 0.71, DeBERTa_positive 0.71
- **MA** top-3: FinMarBa_indecisive(inv) 0.65, nocoref_neutral_filter 0.62, FLANG-BERT_positive 0.59
- **Clinical** top-3: distilroberta_sentiment_score 0.66, ModernBERT_positive 0.65, RoBERTa_sentiment_score 0.63
- **Financing** top-3: FLANG-BERT_positive 0.57, FinancialBERT_negative(inv) 0.56, Trading-Hero_sentiment_score 0.55
- **Compliance** top-3: FLANG-ELECTRA_sentiment_score 0.68, ModernBERT_positive 0.65, FinMarBa_indecisive(inv) 0.61
- **IP** top-3: finbert-tone_sentiment_score 0.75, ModernBERT_positive 0.69, FinMarBa_indecisive(inv) 0.67

| column | classes won | where |
|---|---|---|
| FinMarBa_indecisive(inv) | 3 | Corporate (0.68), FDA (0.72), MA (0.65) |
| FLANG-ELECTRA_sentiment_score | 2 | Commercial (0.62), Compliance (0.68) |
| distilroberta_sentiment_score | 2 | Earnings (0.69), Clinical (0.66) |
| FLANG-BERT_positive | 1 | Financing (0.57) |
| nocoref_neutral_filter | 1 | Other (0.66) |
| finbert-tone_sentiment_score | 1 | IP (0.75) |

## TP/FP per class — fixed triggers, movers >= 30% (cells: TP/FP = precision)

| class | movers | positive>=0.8 | FLANG-BERT_positive>=0.95 | FinMarBa_indecisive<=0.1 | nocoref_neutral_filter>=0.5 |
|---|---|---|---|---|---|
| Commercial | 66 | 18/116 = 13% | 26/196 = 12% | 8/35 = 19% | 14/149 = 9% |
| Earnings | 26 | 3/28 = 10% | 1/16 = 6% | 0/2 = 0% | 7/40 = 15% |
| Corporate | 21 | 4/28 = 12% | 5/44 = 10% | 3/6 = 33% | 4/53 = 7% |
| Other | 19 | 6/41 = 13% | 6/57 = 10% | 0/11 = 0% | 5/48 = 9% |
| FDA | 19 | 5/29 = 15% | 7/48 = 13% | 2/12 = 14% | 9/37 = 20% |
| MA | 45 | 6/27 = 18% | 16/53 = 23% | 4/6 = 40% | 11/45 = 20% |
| Clinical | 24 | 9/45 = 17% | 7/33 = 18% | 2/7 = 22% | 7/23 = 23% |
| Financing | 38 | 5/17 = 23% | 8/22 = 27% | 2/9 = 18% | 8/21 = 28% |
| Compliance | 15 | 3/14 = 18% | 0/9 = 0% | 0/0 | 1/2 = 33% |
| IP | 9 | 0/4 = 0% | 4/15 = 21% | 1/3 = 25% | 2/5 = 29% |
| Crypto | 5 | 0/5 = 0% | 3/12 = 20% | 0/2 = 0% | 3/4 = 43% |
| Legal | 0 | 0/1 = 0% | 0/1 = 0% | 0/0 | 0/2 = 0% |
| <ALL> | 287 | 59/355 = 14% | 83/506 = 14% | 22/93 = 19% | 71/429 = 14% |

