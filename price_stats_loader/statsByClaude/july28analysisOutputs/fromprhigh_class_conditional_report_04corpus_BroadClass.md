# Per-class predictability — BroadClass × 32 single columns — 04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv

- Rows kept: **3426**; classes with >= 40 rows: 12 of 12
- Thresholds: primary **>= 15%**, companion >= 30%, >= 40%
- Columns auto-oriented on the full corpus; `(inv)` = used negated. Within-class AUC < 0.5 then means the signal reverses in that class.
- `~` marks cells with < 8 movers in the class (unreliable).

## Within-class AUC matrix — movers = fromPR_High(%) >= 15%

| class | n | movers | base | FinMarBa_indec | FLANG-BERT_sen | FLANG-BERT_pos | FLANG-ELECTRA_ | DeBERTa_sentim | Trading-Hero_p | distilroberta_ | ModernBERT_pos | FinancialBERT_ | finbert-tone_s | RoBERTa_positi | LouMcD-SDict_s | FinMarBa_Body_ | FLANG-BERT_Bod | FLANG-ELECTRA_ | DeBERTa_Body_p | Trading-Hero_B | distilroberta_ | ModernBERT_Bod | FinancialBERT_ | finbert-tone_B | RoBERTa_Body_p | LouMcD-SDict_B | positive | negative(inv) | neutral(inv) | sentiment_scor | nocoref_neutra | nocoref_confid | nocoref_net_sc | nocoref_top_k | nocoref_positi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 724 | 237 | 32.7% | 0.56 | 0.61 | 0.60 | 0.60 | 0.56 | 0.59 | 0.55 | 0.53 | 0.48 | 0.51 | 0.50 | 0.50 | 0.59 | 0.52 | 0.55 | 0.47 | 0.57 | 0.51 | 0.49 | 0.54 | 0.48 | 0.42 | 0.49 | 0.60 | 0.60 | 0.58 | 0.60 | 0.55 | 0.55 | 0.55 | 0.54 | 0.54 |
| Earnings | 514 | 94 | 18.3% | 0.60 | 0.49 | 0.54 | 0.63 | 0.51 | 0.57 | 0.62 | 0.52 | 0.45 | 0.58 | 0.54 | 0.54 | 0.50 | 0.62 | 0.69 | 0.70 | 0.71 | 0.73 | 0.68 | 0.69 | 0.67 | 0.62 | 0.59 | 0.60 | 0.54 | 0.61 | 0.56 | 0.63 | 0.63 | 0.64 | 0.66 | 0.63 |
| Corporate | 392 | 55 | 14.0% | 0.66 | 0.64 | 0.64 | 0.64 | 0.65 | 0.59 | 0.63 | 0.60 | 0.57 | 0.61 | 0.58 | 0.54 | 0.56 | 0.61 | 0.62 | 0.61 | 0.63 | 0.66 | 0.62 | 0.65 | 0.64 | 0.58 | 0.63 | 0.64 | 0.62 | 0.62 | 0.65 | 0.64 | 0.67 | 0.67 | 0.66 | 0.69 |
| Other | 347 | 70 | 20.2% | 0.61 | 0.62 | 0.62 | 0.60 | 0.60 | 0.58 | 0.58 | 0.57 | 0.50 | 0.56 | 0.55 | 0.54 | 0.60 | 0.56 | 0.61 | 0.55 | 0.65 | 0.60 | 0.57 | 0.58 | 0.56 | 0.48 | 0.48 | 0.58 | 0.64 | 0.56 | 0.59 | 0.56 | 0.55 | 0.55 | 0.56 | 0.55 |
| FDA | 307 | 71 | 23.1% | 0.65 | 0.61 | 0.62 | 0.59 | 0.60 | 0.58 | 0.59 | 0.60 | 0.55 | 0.57 | 0.57 | 0.48 | 0.58 | 0.56 | 0.61 | 0.55 | 0.57 | 0.56 | 0.56 | 0.60 | 0.53 | 0.53 | 0.58 | 0.62 | 0.60 | 0.61 | 0.62 | 0.63 | 0.64 | 0.66 | 0.60 | 0.63 |
| MA | 291 | 121 | 41.6% | 0.61 | 0.60 | 0.58 | 0.59 | 0.60 | 0.58 | 0.59 | 0.63 | 0.49 | 0.57 | 0.54 | 0.52 | 0.58 | 0.56 | 0.52 | 0.54 | 0.56 | 0.54 | 0.54 | 0.56 | 0.52 | 0.54 | 0.47 | 0.63 | 0.57 | 0.60 | 0.62 | 0.64 | 0.64 | 0.65 | 0.67 | 0.63 |
| Clinical | 278 | 88 | 31.7% | 0.67 | 0.63 | 0.63 | 0.64 | 0.65 | 0.67 | 0.66 | 0.65 | 0.54 | 0.63 | 0.60 | 0.55 | 0.58 | 0.56 | 0.58 | 0.62 | 0.58 | 0.58 | 0.60 | 0.61 | 0.57 | 0.54 | 0.58 | 0.65 | 0.58 | 0.63 | 0.65 | 0.63 | 0.62 | 0.60 | 0.60 | 0.66 |
| Financing | 240 | 64 | 26.7% | 0.57 | 0.59 | 0.58 | 0.64 | 0.66 | 0.61 | 0.64 | 0.59 | 0.56 | 0.59 | 0.62 | 0.54 | 0.54 | 0.67 | 0.62 | 0.63 | 0.65 | 0.61 | 0.59 | 0.60 | 0.65 | 0.60 | 0.56 | 0.64 | 0.58 | 0.65 | 0.63 | 0.62 | 0.63 | 0.64 | 0.62 | 0.66 |
| Compliance | 140 | 24 | 17.1% | 0.59 | 0.57 | 0.60 | 0.55 | 0.61 | 0.47 | 0.57 | 0.49 | 0.46 | 0.46 | 0.68 | 0.49 | 0.50 | 0.67 | 0.63 | 0.56 | 0.48 | 0.61 | 0.51 | 0.58 | 0.51 | 0.59 | 0.59 | 0.62 | 0.51 | 0.61 | 0.64 | 0.49 | 0.53 | 0.53 | 0.62 | 0.50 |
| IP | 73 | 20 | 27.4% | 0.50 | 0.63 | 0.65 | 0.65 | 0.57 | 0.66 | 0.57 | 0.68 | 0.48 | 0.66 | 0.60 | 0.52 | 0.65 | 0.59 | 0.71 | 0.61 | 0.58 | 0.69 | 0.67 | 0.63 | 0.67 | 0.58 | 0.55 | 0.59 | 0.53 | 0.55 | 0.55 | 0.56 | 0.60 | 0.54 | 0.67 | 0.57 |
| Crypto | 68 | 17 | 25.0% | 0.71 | 0.80 | 0.78 | 0.74 | 0.63 | 0.72 | 0.49 | 0.58 | 0.53 | 0.51 | 0.51 | 0.58 | 0.75 | 0.61 | 0.57 | 0.50 | 0.68 | 0.63 | 0.58 | 0.66 | 0.61 | 0.53 | 0.51 | 0.72 | 0.63 | 0.75 | 0.70 | 0.65 | 0.62 | 0.63 | 0.52 | 0.64 |
| Legal | 52 | 5 | 9.6% | 0.95~ | 0.97~ | 0.96~ | 1.00~ | 0.93~ | 0.99~ | 0.87~ | 0.74~ | 0.45~ | 0.98~ | 0.91~ | 0.86~ | 0.86~ | 0.97~ | 0.81~ | 0.92~ | 0.97~ | 0.97~ | 0.93~ | 0.88~ | 0.96~ | 0.95~ | 0.44~ | 0.85~ | 0.85~ | 0.60~ | 0.85~ | 0.97~ | 0.95~ | 0.95~ | 0.97~ | 0.97~ |

## Within-class AUC matrix — movers = fromPR_High(%) >= 30%

| class | n | movers | base | FinMarBa_indec | FLANG-BERT_sen | FLANG-BERT_pos | FLANG-ELECTRA_ | DeBERTa_sentim | Trading-Hero_p | distilroberta_ | ModernBERT_pos | FinancialBERT_ | finbert-tone_s | RoBERTa_positi | LouMcD-SDict_s | FinMarBa_Body_ | FLANG-BERT_Bod | FLANG-ELECTRA_ | DeBERTa_Body_p | Trading-Hero_B | distilroberta_ | ModernBERT_Bod | FinancialBERT_ | finbert-tone_B | RoBERTa_Body_p | LouMcD-SDict_B | positive | negative(inv) | neutral(inv) | sentiment_scor | nocoref_neutra | nocoref_confid | nocoref_net_sc | nocoref_top_k | nocoref_positi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 724 | 115 | 15.9% | 0.55 | 0.61 | 0.60 | 0.65 | 0.59 | 0.61 | 0.58 | 0.61 | 0.51 | 0.53 | 0.51 | 0.52 | 0.59 | 0.55 | 0.60 | 0.47 | 0.62 | 0.52 | 0.49 | 0.55 | 0.46 | 0.41 | 0.47 | 0.62 | 0.61 | 0.60 | 0.63 | 0.55 | 0.55 | 0.55 | 0.53 | 0.52 |
| Earnings | 514 | 36 | 7.0% | 0.60 | 0.43 | 0.50 | 0.64 | 0.51 | 0.61 | 0.72 | 0.51 | 0.47 | 0.59 | 0.58 | 0.53 | 0.44 | 0.62 | 0.69 | 0.74 | 0.74 | 0.77 | 0.65 | 0.71 | 0.71 | 0.61 | 0.63 | 0.60 | 0.55 | 0.63 | 0.56 | 0.66 | 0.66 | 0.69 | 0.72 | 0.64 |
| Corporate | 392 | 27 | 6.9% | 0.65 | 0.63 | 0.64 | 0.58 | 0.65 | 0.51 | 0.59 | 0.59 | 0.55 | 0.60 | 0.56 | 0.52 | 0.56 | 0.59 | 0.61 | 0.57 | 0.63 | 0.63 | 0.62 | 0.63 | 0.63 | 0.61 | 0.57 | 0.61 | 0.62 | 0.60 | 0.61 | 0.63 | 0.67 | 0.68 | 0.64 | 0.67 |
| Other | 347 | 27 | 7.8% | 0.58 | 0.68 | 0.68 | 0.58 | 0.58 | 0.57 | 0.53 | 0.62 | 0.49 | 0.56 | 0.56 | 0.50 | 0.59 | 0.59 | 0.60 | 0.52 | 0.59 | 0.53 | 0.54 | 0.53 | 0.50 | 0.48 | 0.45 | 0.60 | 0.62 | 0.57 | 0.61 | 0.57 | 0.57 | 0.58 | 0.58 | 0.57 |
| FDA | 307 | 34 | 11.1% | 0.71 | 0.69 | 0.68 | 0.64 | 0.66 | 0.63 | 0.65 | 0.65 | 0.55 | 0.63 | 0.63 | 0.53 | 0.65 | 0.66 | 0.67 | 0.61 | 0.66 | 0.62 | 0.61 | 0.66 | 0.60 | 0.60 | 0.63 | 0.64 | 0.63 | 0.63 | 0.65 | 0.69 | 0.69 | 0.70 | 0.63 | 0.70 |
| MA | 291 | 73 | 25.1% | 0.65 | 0.63 | 0.62 | 0.60 | 0.57 | 0.60 | 0.59 | 0.60 | 0.47 | 0.57 | 0.55 | 0.48 | 0.59 | 0.54 | 0.47 | 0.51 | 0.58 | 0.49 | 0.55 | 0.59 | 0.49 | 0.50 | 0.47 | 0.59 | 0.56 | 0.56 | 0.59 | 0.64 | 0.63 | 0.63 | 0.63 | 0.64 |
| Clinical | 278 | 37 | 13.3% | 0.65 | 0.62 | 0.62 | 0.60 | 0.63 | 0.62 | 0.66 | 0.64 | 0.55 | 0.62 | 0.59 | 0.56 | 0.56 | 0.53 | 0.51 | 0.54 | 0.51 | 0.55 | 0.58 | 0.58 | 0.56 | 0.46 | 0.57 | 0.64 | 0.62 | 0.61 | 0.65 | 0.61 | 0.62 | 0.62 | 0.58 | 0.63 |
| Financing | 240 | 32 | 13.3% | 0.60 | 0.62 | 0.63 | 0.65 | 0.68 | 0.63 | 0.59 | 0.54 | 0.62 | 0.57 | 0.60 | 0.54 | 0.59 | 0.63 | 0.54 | 0.61 | 0.64 | 0.60 | 0.58 | 0.57 | 0.62 | 0.55 | 0.53 | 0.61 | 0.58 | 0.65 | 0.59 | 0.70 | 0.72 | 0.72 | 0.69 | 0.73 |
| Compliance | 140 | 15 | 10.7% | 0.73 | 0.65 | 0.68 | 0.69 | 0.79 | 0.46 | 0.66 | 0.66 | 0.56 | 0.61 | 0.70 | 0.54 | 0.48 | 0.76 | 0.63 | 0.67 | 0.44 | 0.71 | 0.56 | 0.63 | 0.51 | 0.66 | 0.59 | 0.72 | 0.57 | 0.72 | 0.73 | 0.47 | 0.51 | 0.51 | 0.59 | 0.50 |
| IP | 73 | 12 | 16.4% | 0.68 | 0.64 | 0.67 | 0.64 | 0.55 | 0.67 | 0.61 | 0.77 | 0.48 | 0.73 | 0.66 | 0.64 | 0.62 | 0.60 | 0.78 | 0.53 | 0.55 | 0.69 | 0.76 | 0.67 | 0.69 | 0.60 | 0.56 | 0.62 | 0.56 | 0.58 | 0.60 | 0.61 | 0.67 | 0.58 | 0.67 | 0.61 |
| Crypto | 68 | 9 | 13.2% | 0.79 | 0.82 | 0.81 | 0.82 | 0.68 | 0.79 | 0.58 | 0.62 | 0.63 | 0.61 | 0.54 | 0.65 | 0.76 | 0.72 | 0.61 | 0.54 | 0.67 | 0.61 | 0.68 | 0.66 | 0.61 | 0.58 | 0.48 | 0.78 | 0.57 | 0.81 | 0.74 | 0.79 | 0.74 | 0.76 | 0.52 | 0.75 |
| Legal | 52 | 0 | 0.0% | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

## Within-class AUC matrix — movers = fromPR_High(%) >= 40%

| class | n | movers | base | FinMarBa_indec | FLANG-BERT_sen | FLANG-BERT_pos | FLANG-ELECTRA_ | DeBERTa_sentim | Trading-Hero_p | distilroberta_ | ModernBERT_pos | FinancialBERT_ | finbert-tone_s | RoBERTa_positi | LouMcD-SDict_s | FinMarBa_Body_ | FLANG-BERT_Bod | FLANG-ELECTRA_ | DeBERTa_Body_p | Trading-Hero_B | distilroberta_ | ModernBERT_Bod | FinancialBERT_ | finbert-tone_B | RoBERTa_Body_p | LouMcD-SDict_B | positive | negative(inv) | neutral(inv) | sentiment_scor | nocoref_neutra | nocoref_confid | nocoref_net_sc | nocoref_top_k | nocoref_positi |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Commercial | 724 | 85 | 11.7% | 0.56 | 0.63 | 0.63 | 0.65 | 0.59 | 0.60 | 0.58 | 0.59 | 0.53 | 0.53 | 0.51 | 0.53 | 0.58 | 0.56 | 0.59 | 0.47 | 0.62 | 0.52 | 0.49 | 0.56 | 0.46 | 0.42 | 0.44 | 0.62 | 0.61 | 0.60 | 0.62 | 0.53 | 0.54 | 0.54 | 0.53 | 0.52 |
| Earnings | 514 | 25 | 4.9% | 0.58 | 0.41 | 0.45 | 0.63 | 0.51 | 0.60 | 0.74 | 0.46 | 0.50 | 0.56 | 0.60 | 0.53 | 0.42 | 0.58 | 0.65 | 0.69 | 0.73 | 0.72 | 0.59 | 0.66 | 0.65 | 0.50 | 0.64 | 0.56 | 0.61 | 0.56 | 0.55 | 0.67 | 0.67 | 0.71 | 0.73 | 0.65 |
| Corporate | 392 | 22 | 5.6% | 0.67 | 0.69 | 0.68 | 0.61 | 0.70 | 0.54 | 0.63 | 0.65 | 0.53 | 0.61 | 0.59 | 0.54 | 0.54 | 0.61 | 0.62 | 0.58 | 0.63 | 0.64 | 0.64 | 0.64 | 0.65 | 0.62 | 0.59 | 0.63 | 0.66 | 0.62 | 0.64 | 0.65 | 0.70 | 0.72 | 0.65 | 0.69 |
| Other | 347 | 18 | 5.2% | 0.55 | 0.68 | 0.68 | 0.61 | 0.63 | 0.63 | 0.54 | 0.60 | 0.45 | 0.59 | 0.60 | 0.48 | 0.56 | 0.59 | 0.67 | 0.60 | 0.68 | 0.61 | 0.57 | 0.65 | 0.59 | 0.52 | 0.48 | 0.63 | 0.54 | 0.61 | 0.63 | 0.62 | 0.61 | 0.63 | 0.62 | 0.63 |
| FDA | 307 | 26 | 8.5% | 0.75 | 0.72 | 0.72 | 0.67 | 0.69 | 0.64 | 0.68 | 0.65 | 0.56 | 0.62 | 0.64 | 0.55 | 0.69 | 0.68 | 0.69 | 0.62 | 0.70 | 0.63 | 0.63 | 0.69 | 0.61 | 0.60 | 0.63 | 0.65 | 0.63 | 0.63 | 0.65 | 0.68 | 0.70 | 0.71 | 0.70 | 0.67 |
| MA | 291 | 59 | 20.3% | 0.66 | 0.67 | 0.67 | 0.63 | 0.58 | 0.62 | 0.58 | 0.61 | 0.48 | 0.59 | 0.56 | 0.48 | 0.58 | 0.57 | 0.50 | 0.52 | 0.60 | 0.51 | 0.58 | 0.60 | 0.51 | 0.52 | 0.48 | 0.59 | 0.58 | 0.57 | 0.60 | 0.65 | 0.64 | 0.64 | 0.63 | 0.65 |
| Clinical | 278 | 31 | 11.2% | 0.66 | 0.61 | 0.60 | 0.59 | 0.63 | 0.58 | 0.68 | 0.65 | 0.54 | 0.61 | 0.60 | 0.54 | 0.57 | 0.56 | 0.52 | 0.56 | 0.52 | 0.59 | 0.64 | 0.61 | 0.56 | 0.52 | 0.56 | 0.65 | 0.61 | 0.62 | 0.66 | 0.65 | 0.65 | 0.66 | 0.60 | 0.67 |
| Financing | 240 | 25 | 10.4% | 0.62 | 0.63 | 0.65 | 0.65 | 0.66 | 0.63 | 0.59 | 0.58 | 0.63 | 0.57 | 0.59 | 0.55 | 0.60 | 0.63 | 0.55 | 0.63 | 0.61 | 0.60 | 0.59 | 0.61 | 0.66 | 0.54 | 0.54 | 0.61 | 0.55 | 0.66 | 0.60 | 0.68 | 0.71 | 0.72 | 0.71 | 0.73 |
| Compliance | 140 | 12 | 8.6% | 0.72 | 0.66 | 0.68 | 0.67 | 0.77 | 0.41 | 0.63 | 0.67 | 0.54 | 0.61 | 0.71 | 0.49 | 0.50 | 0.72 | 0.63 | 0.68 | 0.49 | 0.68 | 0.61 | 0.62 | 0.51 | 0.71 | 0.65 | 0.69 | 0.59 | 0.70 | 0.71 | 0.46 | 0.48 | 0.47 | 0.57 | 0.45 |
| IP | 73 | 8 | 11.0% | 0.68 | 0.63 | 0.67 | 0.61 | 0.52 | 0.60 | 0.62 | 0.72 | 0.51 | 0.76 | 0.69 | 0.62 | 0.50 | 0.61 | 0.75 | 0.54 | 0.53 | 0.65 | 0.73 | 0.65 | 0.66 | 0.68 | 0.58 | 0.64 | 0.64 | 0.59 | 0.62 | 0.60 | 0.64 | 0.58 | 0.70 | 0.59 |
| Crypto | 68 | 6 | 8.8% | 0.82~ | 0.84~ | 0.83~ | 0.81~ | 0.65~ | 0.80~ | 0.63~ | 0.62~ | 0.62~ | 0.54~ | 0.53~ | 0.58~ | 0.82~ | 0.66~ | 0.65~ | 0.57~ | 0.68~ | 0.58~ | 0.65~ | 0.66~ | 0.61~ | 0.60~ | 0.44~ | 0.84~ | 0.48~ | 0.86~ | 0.76~ | 0.81~ | 0.76~ | 0.75~ | 0.50~ | 0.79~ |
| Legal | 52 | 0 | 0.0% | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — | — |

## Class hardness — best achievable single-column AUC (>= 15%)

| class | n | movers | best column | best AUC | median AUC (14 cols) |
|---|---|---|---|---|---|
| Commercial | 724 | 237 | FLANG-BERT_sentiment_score | 0.61 | 0.55 |
| Other | 347 | 70 | Trading-Hero_Body_sentiment_score | 0.65 | 0.57 |
| FDA | 307 | 71 | nocoref_net_score | 0.66 | 0.59 |
| MA | 291 | 121 | nocoref_top_k | 0.67 | 0.58 |
| Clinical | 278 | 88 | FinMarBa_indecisive(inv) | 0.67 | 0.62 |
| Financing | 240 | 64 | FLANG-BERT_Body_sentiment_score | 0.67 | 0.62 |
| Compliance | 140 | 24 | RoBERTa_positive | 0.68 | 0.57 |
| Corporate | 392 | 55 | nocoref_positional | 0.69 | 0.63 |
| IP | 73 | 20 | FLANG-ELECTRA_Body_positive | 0.71 | 0.60 |
| Earnings | 514 | 94 | distilroberta_Body_sentiment_score | 0.73 | 0.61 |
| Crypto | 68 | 17 | FLANG-BERT_sentiment_score | 0.80 | 0.63 |
| Legal | 52 | 5 | FLANG-ELECTRA_sentiment_score | 1.00 ~ | 0.94 |

## Winners — within-class best column counts (classes with >= 8 movers, >= 15%)

- **Commercial** top-3: FLANG-BERT_sentiment_score 0.61, sentiment_score 0.60, FLANG-BERT_positive 0.60
- **Earnings** top-3: distilroberta_Body_sentiment_score 0.73, Trading-Hero_Body_sentiment_score 0.71, DeBERTa_Body_positive 0.70
- **Corporate** top-3: nocoref_positional 0.69, nocoref_net_score 0.67, nocoref_confidence_weighted 0.67
- **Other** top-3: Trading-Hero_Body_sentiment_score 0.65, negative(inv) 0.64, FLANG-BERT_sentiment_score 0.62
- **FDA** top-3: nocoref_net_score 0.66, FinMarBa_indecisive(inv) 0.65, nocoref_confidence_weighted 0.64
- **MA** top-3: nocoref_top_k 0.67, nocoref_net_score 0.65, nocoref_confidence_weighted 0.64
- **Clinical** top-3: FinMarBa_indecisive(inv) 0.67, Trading-Hero_positive 0.67, distilroberta_sentiment_score 0.66
- **Financing** top-3: FLANG-BERT_Body_sentiment_score 0.67, DeBERTa_sentiment_score 0.66, nocoref_positional 0.66
- **Compliance** top-3: RoBERTa_positive 0.68, FLANG-BERT_Body_sentiment_score 0.67, sentiment_score 0.64
- **IP** top-3: FLANG-ELECTRA_Body_positive 0.71, distilroberta_Body_sentiment_score 0.69, ModernBERT_positive 0.68
- **Crypto** top-3: FLANG-BERT_sentiment_score 0.80, FLANG-BERT_positive 0.78, FinMarBa_Body_indecisive(inv) 0.75

| column | classes won | where |
|---|---|---|
| FLANG-BERT_sentiment_score | 2 | Commercial (0.61), Crypto (0.80) |
| FinMarBa_indecisive(inv) | 1 | Clinical (0.67) |
| RoBERTa_positive | 1 | Compliance (0.68) |
| FLANG-BERT_Body_sentiment_score | 1 | Financing (0.67) |
| FLANG-ELECTRA_Body_positive | 1 | IP (0.71) |
| Trading-Hero_Body_sentiment_score | 1 | Other (0.65) |
| distilroberta_Body_sentiment_score | 1 | Earnings (0.73) |
| nocoref_net_score | 1 | FDA (0.66) |
| nocoref_top_k | 1 | MA (0.67) |
| nocoref_positional | 1 | Corporate (0.69) |

## TP/FP per class — fixed triggers, movers >= 15% (cells: TP/FP = precision)

| class | movers | positive>=0.8 | FLANG-BERT_positive>=0.95 | FinMarBa_indecisive<=0.1 | nocoref_neutral_filter>=0.5 |
|---|---|---|---|---|---|
| Commercial | 237 | 68/74 = 48% | 98/143 = 41% | 15/32 = 32% | 55/106 = 34% |
| Earnings | 94 | 15/16 = 48% | 9/9 = 50% | 1/1 = 50% | 18/29 = 38% |
| Corporate | 55 | 9/25 = 26% | 13/38 = 25% | 3/7 = 30% | 13/43 = 23% |
| Other | 70 | 15/36 = 29% | 22/46 = 32% | 2/9 = 18% | 14/39 = 26% |
| FDA | 71 | 13/24 = 35% | 24/37 = 39% | 5/11 = 31% | 20/26 = 43% |
| MA | 121 | 21/12 = 64% | 36/38 = 49% | 7/4 = 64% | 29/25 = 54% |
| Clinical | 88 | 31/31 = 50% | 20/22 = 48% | 5/5 = 50% | 16/14 = 53% |
| Financing | 64 | 13/10 = 57% | 15/17 = 47% | 3/8 = 27% | 14/15 = 48% |
| Compliance | 24 | 6/12 = 33% | 0/8 = 0% | 0/0 | 1/2 = 33% |
| IP | 20 | 1/5 = 17% | 7/14 = 33% | 1/2 = 33% | 3/4 = 43% |
| Crypto | 17 | 2/3 = 40% | 10/7 = 59% | 1/2 = 33% | 4/3 = 57% |
| Legal | 5 | 1/0 = 100% | 1/0 = 100% | 0/0 | 1/1 = 50% |
| <ALL> | 866 | 195/248 = 44% | 255/379 = 40% | 43/81 = 35% | 188/307 = 38% |

