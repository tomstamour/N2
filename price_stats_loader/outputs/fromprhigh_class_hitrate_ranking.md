# Class predictiveness of `fromPR_High(%)` >= 30%

Input: `04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv`  
Rows: 3533 read -> 3428 kept (dropped 99 null target, 0 outlier, 0 out-of-date-range, 6 duplicate (Symbol, Headline, ArrivalDate))  
Base rate: **419/3428 = 12.22%** of PRs reach fromPR_High(%) >= 30%  
Sample-size floor: n >= 30 to be ranked; smaller classes are shown but flagged `n<MIN`.  
Ranked by the LOWER 95% Wilson bound (not the point estimate), so a tiny class cannot top the table on 3/4 = 75%.  
`sig+` / `sig-` = one-vs-rest Fisher exact survives BH-FDR at q<0.05 within the class column's family.

## BroadClass — hit-rate ranking

| class | n | movers | hit-rate | lift | 95% Wilson CI | median 30%-target | Fisher p | BH q | flag |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| MA | 293 | 75 | 25.6% | 2.09x | [20.9%, 30.9%] | 11.6 | 0.0000 | 0.0000 | **sig+** |
| Commercial | 724 | 115 | 15.9% | 1.30x | [13.4%, 18.7%] | 8.2 | 0.0011 | 0.0033 | **sig+** |
| Clinical | 278 | 37 | 13.3% | 1.09x | [9.8%, 17.8%] | 8.5 | 0.5665 | 0.7111 |  |
| IP | 73 | 12 | 16.4% | 1.34x | [9.7%, 26.6%] | 7.6 | 0.2764 | 0.4738 |  |
| Financing | 240 | 32 | 13.3% | 1.09x | [9.6%, 18.2%] | 7.2 | 0.6090 | 0.7111 |  |
| FDA | 307 | 34 | 11.1% | 0.91x | [8.0%, 15.1%] | 5.7 | 0.5839 | 0.7111 |  |
| Crypto | 68 | 9 | 13.2% | 1.08x | [7.1%, 23.3%] | 8.1 | 0.7111 | 0.7111 |  |
| Compliance | 140 | 15 | 10.7% | 0.88x | [6.6%, 16.9%] | 4.1 | 0.6925 | 0.7111 |  |
| Other | 347 | 27 | 7.8% | 0.64x | [5.4%, 11.1%] | 6.1 | 0.0072 | 0.0143 | **sig-** |
| Earnings | 514 | 36 | 7.0% | 0.57x | [5.1%, 9.5%] | 4.2 | 0.0000 | 0.0002 | **sig-** |
| Corporate | 392 | 27 | 6.9% | 0.56x | [4.8%, 9.8%] | 4.9 | 0.0004 | 0.0016 | **sig-** |
| Legal | 52 | 0 | 0.0% | 0.00x | [0.0%, 6.9%] | 3.0 | 0.0020 | 0.0049 | **sig-** |

## FineClass — hit-rate ranking

| class | n | movers | hit-rate | lift | 95% Wilson CI | median 30%-target | Fisher p | BH q | flag |
|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| MA | 293 | 75 | 25.6% | 2.09x | [20.9%, 30.9%] | 11.6 | 0.0000 | 0.0000 | **sig+** |
| Partnership | 256 | 51 | 19.9% | 1.63x | [15.5%, 25.2%] | 9.7 | 0.0002 | 0.0011 | **sig+** |
| Regain_Compliance | 49 | 11 | 22.4% | 1.84x | [13.0%, 35.9%] | 9.0 | 0.0436 | 0.0921 |  |
| Contract_Award | 180 | 30 | 16.7% | 1.36x | [11.9%, 22.8%] | 8.4 | 0.0782 | 0.1487 |  |
| Clinical_Data | 127 | 22 | 17.3% | 1.42x | [11.7%, 24.8%] | 8.0 | 0.0956 | 0.1652 |  |
| Patent_IP | 73 | 12 | 16.4% | 1.34x | [9.7%, 26.6%] | 7.6 | 0.2764 | 0.4376 |  |
| Offering_Financing | 217 | 29 | 13.4% | 1.09x | [9.5%, 18.5%] | 7.1 | 0.5923 | 0.6929 |  |
| Business_Update | 103 | 15 | 14.6% | 1.19x | [9.0%, 22.6%] | 7.7 | 0.4452 | 0.6056 |  |
| Product_Launch | 288 | 34 | 11.8% | 0.97x | [8.6%, 16.0%] | 7.1 | 0.9251 | 0.9251 |  |
| FDA_Designation | 283 | 31 | 11.0% | 0.90x | [7.8%, 15.1%] | 5.5 | 0.5696 | 0.6929 |  |
| Crypto_Treasury | 68 | 9 | 13.2% | 1.08x | [7.1%, 23.3%] | 8.1 | 0.7111 | 0.7506 |  |
| Clinical_Update | 151 | 15 | 9.9% | 0.81x | [6.1%, 15.7%] | 8.6 | 0.4463 | 0.6056 |  |
| Other | 347 | 27 | 7.8% | 0.64x | [5.4%, 11.1%] | 6.1 | 0.0072 | 0.0205 | **sig-** |
| Earnings_Results | 487 | 35 | 7.2% | 0.59x | [5.2%, 9.8%] | 4.3 | 0.0001 | 0.0009 | **sig-** |
| Deficiency_Notice | 39 | 3 | 7.7% | 0.63x | [2.7%, 20.3%] | 5.8 | 0.6199 | 0.6929 |  |
| Conference_Presentation | 123 | 6 | 4.9% | 0.40x | [2.3%, 10.2%] | 4.3 | 0.0075 | 0.0205 | **sig-** |
| Management_Change | 166 | 6 | 3.6% | 0.30x | [1.7%, 7.7%] | 4.5 | 0.0001 | 0.0009 | **sig-** |
| Reverse_Split | 52 | 1 | 1.9% | 0.16x | [0.3%, 10.1%] | 0.1 | 0.0170 | 0.0404 | **sig-** |
| Legal | 52 | 0 | 0.0% | 0.00x | [0.0%, 6.9%] | 3.0 | 0.0020 | 0.0077 | **sig-** |
| Buyback_Dividend | 23 | 3 | 13.0% | 1.07x | [4.5%, 32.1%] | 7.7 | 0.7550 | — | n<MIN |
| FDA_Approval | 24 | 3 | 12.5% | 1.02x | [4.3%, 31.0%] | 8.6 | 1.0000 | — | n<MIN |
| Earnings_Date | 27 | 1 | 3.7% | 0.30x | [0.7%, 18.3%] | 2.6 | 0.2425 | — | n<MIN |

## FineClass nested inside BroadClass

Where a broad class hides a strong or dead sub-class.

| BroadClass | FineClass | n | movers | hit-rate | 95% CI | lift vs broad | lift vs corpus |
|---|---|---:|---:|---:|---|---:|---:|
| Clinical | Clinical_Data | 127 | 22 | 17.3% | [11.7%, 24.8%] | 1.30x | 1.42x |
| Clinical | Clinical_Update | 151 | 15 | 9.9% | [6.1%, 15.7%] | 0.75x | 0.81x |
| Commercial | Contract_Award | 180 | 30 | 16.7% | [11.9%, 22.8%] | 1.05x | 1.36x |
| Commercial | Partnership | 256 | 51 | 19.9% | [15.5%, 25.2%] | 1.25x | 1.63x |
| Commercial | Product_Launch | 288 | 34 | 11.8% | [8.6%, 16.0%] | 0.74x | 0.97x |
| Compliance | Deficiency_Notice | 39 | 3 | 7.7% | [2.7%, 20.3%] | 0.72x | 0.63x |
| Compliance | Regain_Compliance | 49 | 11 | 22.4% | [13.0%, 35.9%] | 2.10x | 1.84x |
| Compliance | Reverse_Split | 52 | 1 | 1.9% | [0.3%, 10.1%] | 0.18x | 0.16x |
| Corporate | Business_Update | 103 | 15 | 14.6% | [9.0%, 22.6%] | 2.11x | 1.19x |
| Corporate | Conference_Presentation | 123 | 6 | 4.9% | [2.3%, 10.2%] | 0.71x | 0.40x |
| Corporate | Management_Change | 166 | 6 | 3.6% | [1.7%, 7.7%] | 0.52x | 0.30x |
| Earnings | Earnings_Date `n<MIN` | 27 | 1 | 3.7% | [0.7%, 18.3%] | 0.53x | 0.30x |
| Earnings | Earnings_Results | 487 | 35 | 7.2% | [5.2%, 9.8%] | 1.03x | 0.59x |
| FDA | FDA_Approval `n<MIN` | 24 | 3 | 12.5% | [4.3%, 31.0%] | 1.13x | 1.02x |
| FDA | FDA_Designation | 283 | 31 | 11.0% | [7.8%, 15.1%] | 0.99x | 0.90x |
| Financing | Buyback_Dividend `n<MIN` | 23 | 3 | 13.0% | [4.5%, 32.1%] | 0.98x | 1.07x |
| Financing | Offering_Financing | 217 | 29 | 13.4% | [9.5%, 18.5%] | 1.00x | 1.09x |

## Class x encoder interaction

99 encoder score channels auto-detected. Inside each class with n >= 30 and >= 8 movers, each channel is oriented on the full corpus (a channel with corpus AUC < 0.5 is negated and shown with a `<=` operator), scored by within-class AUC, and cut at within-class quantiles top 50%, top 33%, top 25%, top 10%. Cuts firing on < 10 rows are dropped. BH-FDR `q` is over the whole (class x channel x cut) family — treat any cell with q >= 0.05 as noise.

**Two caveats on every number in this section.** (1) The cut shown is the best of 4 quantiles x ~99 channels *inside that class*, so the hit-rate is the maximum of a large in-sample search and is optimistically biased even where q < 0.05 — the FDR controls the claim 'this channel separates', not the claim 'this hit-rate will replicate'. (2) The cut VALUES are within-class quantiles, so they are class-specific and are not portable global constants; a live gate leg needs the cut re-fit on a held-out period.

A corpus AUC printed with a trailing `?` means |AUC - 0.50| < 0.04: the channel's SIGN is not determined by the data, so the direction of its cut is close to arbitrary. Such rows are kept in the tables but excluded from the conclusion.

_9114 cells tested._

### BroadClass


**Commercial** — n=724, class hit-rate 15.9% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-ELECTRA_positive >= 0.9982` (top 33%) | 0.654 | 0.644 | 255 | 67 | 26.3% | [21.3%, 32.0%] | 1.65x | 2.15x | 0.0000 | 0.0001 |
| `FLANG-ELECTRA_sentiment_score >= 0.9977` (top 10%) | 0.653 | 0.647 | 116 | 33 | 28.4% | [21.0%, 37.2%] | 1.79x | 2.33x | 0.0001 | 0.0094 |
| `Trading-Hero_sentiment_score >= 0.9996` (top 10%) | 0.646 | 0.616 | 74 | 21 | 28.4% | [19.4%, 39.5%] | 1.79x | 2.32x | 0.0038 | 0.0652 |
| `Trading-Hero_positive >= 0.9997` (top 10%) | 0.642 | 0.614 | 74 | 21 | 28.4% | [19.4%, 39.5%] | 1.79x | 2.32x | 0.0038 | 0.0652 |
| `FLANG-ELECTRA_neutral <= 0.0013` (top 33%) | 0.359 | 0.620 | 289 | 69 | 23.9% | [19.3%, 29.1%] | 1.50x | 1.95x | 0.0000 | 0.0007 |

**Earnings** — n=514, class hit-rate 7.0% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `distilroberta_Body_positive >= 0.9997` (top 10%) | 0.592 | 0.774 | 60 | 16 | 26.7% | [17.1%, 39.0%] | 3.81x | 2.18x | 0.0000 | 0.0002 |
| `nocoref_top_k >= 0.9108` (top 10%) | 0.633 | 0.721 | 49 | 13 | 26.5% | [16.2%, 40.3%] | 3.79x | 2.17x | 0.0000 | 0.0014 |
| `nocoref_net_score >= 0.3949` (top 10%) | 0.656 | 0.693 | 49 | 12 | 24.5% | [14.6%, 38.1%] | 3.50x | 2.00x | 0.0000 | 0.0047 |
| `finbert-tone_sentiment_score >= 0.01773` (top 10%) | 0.607 | 0.589 | 52 | 12 | 23.1% | [13.7%, 36.1%] | 3.29x | 1.89x | 0.0001 | 0.0059 |
| `Trading-Hero_positive >= 0.01969` (top 10%) | 0.642 | 0.605 | 52 | 12 | 23.1% | [13.7%, 36.1%] | 3.29x | 1.89x | 0.0001 | 0.0059 |

**Corporate** — n=392, class hit-rate 6.9% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `distilroberta_Body_sentiment_score >= 0.9993` (top 10%) | 0.594 | 0.625 | 48 | 9 | 18.8% | [10.2%, 31.9%] | 2.72x | 1.53x | 0.0025 | 0.0509 |
| `FLANG-ELECTRA_sentiment_score >= 0.9871` (top 25%) | 0.653 | 0.585 | 98 | 15 | 15.3% | [9.5%, 23.7%] | 2.22x | 1.25x | 0.0007 | 0.0245 |
| `FLANG-ELECTRA_positive >= 0.9875` (top 25%) | 0.654 | 0.606 | 98 | 15 | 15.3% | [9.5%, 23.7%] | 2.22x | 1.25x | 0.0007 | 0.0245 |
| `nocoref_top_k >= 0.8372` (top 10%) | 0.633 | 0.645 | 37 | 7 | 18.9% | [9.5%, 34.2%] | 2.75x | 1.55x | 0.0082 | 0.0981 |
| `ModernBERT_Body_positive >= 0.9604` (top 10%) | 0.574 | 0.617 | 40 | 7 | 17.5% | [8.7%, 31.9%] | 2.54x | 1.43x | 0.0129 | 0.1279 |

**Other** — n=347, class hit-rate 7.8% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_sentiment_score >= 0.9546` (top 10%) | 0.661 | 0.675 | 35 | 9 | 25.7% | [14.2%, 42.1%] | 3.30x | 2.10x | 0.0005 | 0.0182 |
| `FLANG-BERT_positive >= 0.9706` (top 10%) | 0.661 | 0.681 | 35 | 8 | 22.9% | [12.1%, 39.0%] | 2.94x | 1.87x | 0.0026 | 0.0529 |
| `Trading-Hero_Body_sentiment_score >= 0.9983` (top 10%) | 0.623 | 0.593 | 35 | 7 | 20.0% | [10.0%, 35.9%] | 2.57x | 1.64x | 0.0116 | 0.1212 |
| `Trading-Hero_Body_neutral <= 0.00146` (top 10%) | 0.398 | 0.567 | 35 | 7 | 20.0% | [10.0%, 35.9%] | 2.57x | 1.64x | 0.0116 | 0.1212 |
| `Trading-Hero_Body_positive >= 0.9984` (top 10%) | 0.621 | 0.573 | 35 | 7 | 20.0% | [10.0%, 35.9%] | 2.57x | 1.64x | 0.0116 | 0.1212 |

**FDA** — n=307, class hit-rate 11.1% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `nocoref_neutral_filter >= 0.5402` (top 10%) | 0.646 | 0.693 | 28 | 10 | 35.7% | [20.7%, 54.2%] | 3.22x | 2.92x | 0.0002 | 0.0127 |
| `FLANG-BERT_sentiment_score >= 0.9725` (top 10%) | 0.661 | 0.692 | 31 | 10 | 32.3% | [18.6%, 49.9%] | 2.91x | 2.64x | 0.0006 | 0.0222 |
| `nocoref_net_score >= 0.4657` (top 10%) | 0.656 | 0.699 | 28 | 9 | 32.1% | [17.9%, 50.7%] | 2.90x | 2.63x | 0.0013 | 0.0336 |
| `FLANG-BERT_positive >= 0.9794` (top 10%) | 0.661 | 0.678 | 31 | 9 | 29.0% | [16.1%, 46.6%] | 2.62x | 2.38x | 0.0030 | 0.0556 |
| `FinMarBa_indecisive <= 0.1482` (top 25%) | 0.347 | 0.712 | 77 | 18 | 23.4% | [15.3%, 34.0%] | 2.11x | 1.91x | 0.0002 | 0.0123 |

**MA** — n=293, class hit-rate 25.6% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_Body_negative <= 0.002` (top 10%) | 0.424 | 0.603 | 31 | 14 | 45.2% | [29.2%, 62.2%] | 1.76x | 3.69x | 0.0149 | 0.1389 |
| `distilroberta_Body_negative >= 0.0003` (top 33%) | 0.536? | 0.606 | 134 | 49 | 36.6% | [28.9%, 45.0%] | 1.43x | 2.99x | 0.0001 | 0.0070 |
| `FinMarBa_indecisive <= 0.1353` (top 25%) | 0.347 | 0.647 | 74 | 29 | 39.2% | [28.9%, 50.6%] | 1.53x | 3.21x | 0.0032 | 0.0566 |
| `nocoref_net_score >= 0.404` (top 25%) | 0.656 | 0.629 | 64 | 25 | 39.1% | [28.1%, 51.3%] | 1.53x | 3.20x | 0.0090 | 0.1018 |
| `FLANG-ELECTRA_positive >= 0.9982` (top 10%) | 0.654 | 0.590 | 69 | 26 | 37.7% | [27.2%, 49.5%] | 1.47x | 3.08x | 0.0114 | 0.1208 |

**Clinical** — n=278, class hit-rate 13.3% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `ModernBERT_positive >= 0.9193` (top 25%) | 0.633 | 0.645 | 70 | 19 | 27.1% | [18.1%, 38.5%] | 2.04x | 2.22x | 0.0003 | 0.0151 |
| `ModernBERT_neutral <= 0.05335` (top 25%) | 0.376 | 0.638 | 70 | 18 | 25.7% | [16.9%, 37.0%] | 1.93x | 2.10x | 0.0009 | 0.0278 |
| `ModernBERT_sentiment_score >= 0.876` (top 25%) | 0.629 | 0.641 | 70 | 18 | 25.7% | [16.9%, 37.0%] | 1.93x | 2.10x | 0.0009 | 0.0278 |
| `nocoref_confidence_weighted >= 0.6043` (top 10%) | 0.651 | 0.616 | 26 | 8 | 30.8% | [16.5%, 50.0%] | 2.31x | 2.52x | 0.0120 | 0.1232 |
| `ModernBERT_negative <= 0.02564` (top 33%) | 0.465? | 0.611 | 92 | 21 | 22.8% | [15.4%, 32.4%] | 1.72x | 1.87x | 0.0022 | 0.0467 |

**Financing** — n=240, class hit-rate 13.3% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `nocoref_neutral_filter >= 0.5263` (top 10%) | 0.646 | 0.696 | 23 | 10 | 43.5% | [25.6%, 63.2%] | 3.26x | 3.56x | 0.0002 | 0.0094 |
| `nocoref_net_score >= 0.4052` (top 10%) | 0.656 | 0.724 | 23 | 9 | 39.1% | [22.2%, 59.2%] | 2.93x | 3.20x | 0.0010 | 0.0288 |
| `nocoref_confidence_weighted >= 0.6166` (top 10%) | 0.651 | 0.719 | 23 | 9 | 39.1% | [22.2%, 59.2%] | 2.93x | 3.20x | 0.0010 | 0.0288 |
| `nocoref_positional >= 0.3202` (top 25%) | 0.640 | 0.729 | 56 | 18 | 32.1% | [21.4%, 45.2%] | 2.41x | 2.63x | 0.0000 | 0.0025 |
| `FinancialBERT_neutral >= 0.9998` (top 10%) | 0.512? | 0.558 | 40 | 12 | 30.0% | [18.1%, 45.4%] | 2.25x | 2.45x | 0.0018 | 0.0412 |

**Compliance** — n=140, class hit-rate 10.7% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FinancialBERT_Body_positive >= 0.9969` (top 10%) | 0.615 | 0.645 | 14 | 5 | 35.7% | [16.3%, 61.2%] | 3.33x | 2.92x | 0.0080 | 0.0963 |
| `FinancialBERT_Body_neutral <= 0.00303` (top 10%) | 0.390 | 0.645 | 14 | 5 | 35.7% | [16.3%, 61.2%] | 3.33x | 2.92x | 0.0080 | 0.0963 |
| `FinancialBERT_Body_sentiment_score >= 0.9967` (top 10%) | 0.616 | 0.629 | 14 | 5 | 35.7% | [16.3%, 61.2%] | 3.33x | 2.92x | 0.0080 | 0.0963 |
| `FinMarBa_positive >= 0.6751` (top 25%) | 0.620 | 0.795 | 35 | 10 | 28.6% | [16.3%, 45.1%] | 2.67x | 2.34x | 0.0004 | 0.0155 |
| `DeBERTa_sentiment_score >= 0.7833` (top 25%) | 0.648 | 0.785 | 35 | 10 | 28.6% | [16.3%, 45.1%] | 2.67x | 2.34x | 0.0004 | 0.0155 |

**IP** — n=73, class hit-rate 16.4% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-ELECTRA_Body_positive >= 0.9983` (top 10%) | 0.595 | 0.776 | 14 | 7 | 50.0% | [26.8%, 73.2%] | 3.04x | 4.09x | 0.0010 | 0.0297 |
| `finbert-tone_Body_sentiment_score >= 1` (top 10%) | 0.563 | 0.695 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.81x | 3.78x | 0.0052 | 0.0750 |
| `finbert-tone_Body_positive >= 1` (top 10%) | 0.557 | 0.689 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.81x | 3.78x | 0.0052 | 0.0750 |
| `finbert-tone_Body_neutral <= 0` (top 10%) | 0.455 | 0.682 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.81x | 3.78x | 0.0052 | 0.0750 |
| `finbert-tone_neutral <= 0` (top 10%) | 0.408 | 0.714 | 11 | 5 | 45.5% | [21.3%, 72.0%] | 2.77x | 3.72x | 0.0140 | 0.1370 |

**Crypto** — n=68, class hit-rate 13.2% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_negative <= 0.0084` (top 33%) | 0.402 | 0.848 | 24 | 9 | 37.5% | [21.2%, 57.3%] | 2.83x | 3.07x | 0.0000 | 0.0040 |
| `Trading-Hero_sentiment_score >= 0.9914` (top 33%) | 0.646 | 0.791 | 23 | 8 | 34.8% | [18.8%, 55.1%] | 2.63x | 2.85x | 0.0005 | 0.0179 |
| `Trading-Hero_positive >= 0.994` (top 33%) | 0.642 | 0.793 | 23 | 8 | 34.8% | [18.8%, 55.1%] | 2.63x | 2.85x | 0.0005 | 0.0179 |
| `Trading-Hero_neutral <= 0.003579` (top 33%) | 0.368 | 0.789 | 23 | 8 | 34.8% | [18.8%, 55.1%] | 2.63x | 2.85x | 0.0005 | 0.0179 |
| `FLANG-ELECTRA_positive >= 0.9982` (top 25%) | 0.654 | 0.798 | 20 | 7 | 35.0% | [18.1%, 56.7%] | 2.64x | 2.86x | 0.0019 | 0.0425 |

### FineClass


**Earnings_Results** — n=487, class hit-rate 7.2% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `nocoref_top_k >= 0.9119` (top 10%) | 0.633 | 0.716 | 46 | 13 | 28.3% | [17.3%, 42.5%] | 3.93x | 2.31x | 0.0000 | 0.0008 |
| `distilroberta_Body_positive >= 0.9997` (top 10%) | 0.592 | 0.783 | 60 | 16 | 26.7% | [17.1%, 39.0%] | 3.71x | 2.18x | 0.0000 | 0.0002 |
| `FLANG-BERT_neutral <= 0.02144` (top 10%) | 0.370 | 0.751 | 49 | 13 | 26.5% | [16.2%, 40.3%] | 3.69x | 2.17x | 0.0000 | 0.0014 |
| `nocoref_net_score >= 0.4012` (top 10%) | 0.656 | 0.691 | 46 | 12 | 26.1% | [15.6%, 40.3%] | 3.63x | 2.13x | 0.0000 | 0.0035 |
| `finbert-tone_sentiment_score >= 0.01746` (top 10%) | 0.607 | 0.592 | 49 | 12 | 24.5% | [14.6%, 38.1%] | 3.41x | 2.00x | 0.0000 | 0.0049 |

**Other** — n=347, class hit-rate 7.8% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_sentiment_score >= 0.9546` (top 10%) | 0.661 | 0.675 | 35 | 9 | 25.7% | [14.2%, 42.1%] | 3.30x | 2.10x | 0.0005 | 0.0182 |
| `FLANG-BERT_positive >= 0.9706` (top 10%) | 0.661 | 0.681 | 35 | 8 | 22.9% | [12.1%, 39.0%] | 2.94x | 1.87x | 0.0026 | 0.0529 |
| `Trading-Hero_Body_sentiment_score >= 0.9983` (top 10%) | 0.623 | 0.593 | 35 | 7 | 20.0% | [10.0%, 35.9%] | 2.57x | 1.64x | 0.0116 | 0.1212 |
| `Trading-Hero_Body_neutral <= 0.00146` (top 10%) | 0.398 | 0.567 | 35 | 7 | 20.0% | [10.0%, 35.9%] | 2.57x | 1.64x | 0.0116 | 0.1212 |
| `Trading-Hero_Body_positive >= 0.9984` (top 10%) | 0.621 | 0.573 | 35 | 7 | 20.0% | [10.0%, 35.9%] | 2.57x | 1.64x | 0.0116 | 0.1212 |

**MA** — n=293, class hit-rate 25.6% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_Body_negative <= 0.002` (top 10%) | 0.424 | 0.603 | 31 | 14 | 45.2% | [29.2%, 62.2%] | 1.76x | 3.69x | 0.0149 | 0.1389 |
| `distilroberta_Body_negative >= 0.0003` (top 33%) | 0.536? | 0.606 | 134 | 49 | 36.6% | [28.9%, 45.0%] | 1.43x | 2.99x | 0.0001 | 0.0070 |
| `FinMarBa_indecisive <= 0.1353` (top 25%) | 0.347 | 0.647 | 74 | 29 | 39.2% | [28.9%, 50.6%] | 1.53x | 3.21x | 0.0032 | 0.0566 |
| `nocoref_net_score >= 0.404` (top 25%) | 0.656 | 0.629 | 64 | 25 | 39.1% | [28.1%, 51.3%] | 1.53x | 3.20x | 0.0090 | 0.1018 |
| `FLANG-ELECTRA_positive >= 0.9982` (top 10%) | 0.654 | 0.590 | 69 | 26 | 37.7% | [27.2%, 49.5%] | 1.47x | 3.08x | 0.0114 | 0.1208 |

**Product_Launch** — n=288, class hit-rate 11.8% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `Trading-Hero_Body_negative <= 0.0001` (top 10%) | 0.436 | 0.653 | 56 | 15 | 26.8% | [17.0%, 39.6%] | 2.27x | 2.19x | 0.0004 | 0.0157 |
| `Trading-Hero_Body_neutral <= 0.07475` (top 25%) | 0.398 | 0.615 | 72 | 16 | 22.2% | [14.2%, 33.1%] | 1.88x | 1.82x | 0.0028 | 0.0539 |
| `Trading-Hero_Body_positive >= 0.9173` (top 25%) | 0.621 | 0.616 | 72 | 16 | 22.2% | [14.2%, 33.1%] | 1.88x | 1.82x | 0.0028 | 0.0539 |
| `Trading-Hero_Body_sentiment_score >= 0.9168` (top 25%) | 0.623 | 0.642 | 72 | 16 | 22.2% | [14.2%, 33.1%] | 1.88x | 1.82x | 0.0028 | 0.0539 |
| `FLANG-ELECTRA_positive >= 0.9982` (top 10%) | 0.654 | 0.543 | 52 | 12 | 23.1% | [13.7%, 36.1%] | 1.95x | 1.89x | 0.0089 | 0.1018 |

**FDA_Designation** — n=283, class hit-rate 11.0% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_sentiment_score >= 0.9714` (top 10%) | 0.661 | 0.702 | 29 | 11 | 37.9% | [22.7%, 56.0%] | 3.46x | 3.10x | 0.0000 | 0.0049 |
| `FLANG-BERT_positive >= 0.9789` (top 10%) | 0.661 | 0.688 | 29 | 10 | 34.5% | [19.9%, 52.7%] | 3.15x | 2.82x | 0.0003 | 0.0138 |
| `nocoref_confidence_weighted >= 0.5978` (top 10%) | 0.651 | 0.703 | 26 | 9 | 34.6% | [19.4%, 53.8%] | 3.16x | 2.83x | 0.0006 | 0.0212 |
| `nocoref_net_score >= 0.4601` (top 10%) | 0.656 | 0.708 | 26 | 9 | 34.6% | [19.4%, 53.8%] | 3.16x | 2.83x | 0.0006 | 0.0212 |
| `nocoref_neutral_filter >= 0.5358` (top 10%) | 0.646 | 0.701 | 26 | 9 | 34.6% | [19.4%, 53.8%] | 3.16x | 2.83x | 0.0006 | 0.0212 |

**Partnership** — n=256, class hit-rate 19.9% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `Trading-Hero_positive >= 0.9997` (top 10%) | 0.642 | 0.670 | 30 | 12 | 40.0% | [24.6%, 57.7%] | 2.01x | 3.27x | 0.0065 | 0.0849 |
| `Trading-Hero_sentiment_score >= 0.9996` (top 10%) | 0.646 | 0.672 | 30 | 12 | 40.0% | [24.6%, 57.7%] | 2.01x | 3.27x | 0.0065 | 0.0849 |
| `distilroberta_neutral <= 0.0007` (top 10%) | 0.390 | 0.609 | 32 | 12 | 37.5% | [22.9%, 54.7%] | 1.88x | 3.07x | 0.0156 | 0.1418 |
| `FLANG-BERT_Body_negative <= 0.00285` (top 10%) | 0.424 | 0.626 | 26 | 10 | 38.5% | [22.4%, 57.5%] | 1.93x | 3.15x | 0.0191 | 0.1604 |
| `distilroberta_sentiment_score >= 0.9988` (top 10%) | 0.635 | 0.629 | 26 | 10 | 38.5% | [22.4%, 57.5%] | 1.93x | 3.15x | 0.0191 | 0.1604 |

**Offering_Financing** — n=217, class hit-rate 13.4% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `nocoref_neutral_filter >= 0.5263` (top 10%) | 0.646 | 0.690 | 21 | 9 | 42.9% | [24.5%, 63.5%] | 3.21x | 3.51x | 0.0004 | 0.0161 |
| `nocoref_confidence_weighted >= 0.6181` (top 10%) | 0.651 | 0.706 | 21 | 8 | 38.1% | [20.8%, 59.1%] | 2.85x | 3.12x | 0.0024 | 0.0492 |
| `nocoref_net_score >= 0.4138` (top 10%) | 0.656 | 0.713 | 21 | 8 | 38.1% | [20.8%, 59.1%] | 2.85x | 3.12x | 0.0024 | 0.0492 |
| `nocoref_positional >= 0.3256` (top 25%) | 0.640 | 0.710 | 51 | 16 | 31.4% | [20.3%, 45.0%] | 2.35x | 2.57x | 0.0001 | 0.0059 |
| `nocoref_top_k >= 0.756` (top 25%) | 0.633 | 0.691 | 51 | 15 | 29.4% | [18.7%, 43.0%] | 2.20x | 2.41x | 0.0006 | 0.0200 |

**Contract_Award** — n=180, class hit-rate 16.7% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `Trading-Hero_Body_sentiment_score >= 0.9994` (top 10%) | 0.623 | 0.621 | 19 | 7 | 36.8% | [19.1%, 59.0%] | 2.21x | 3.01x | 0.0210 | 0.1682 |
| `Trading-Hero_Body_positive >= 0.9995` (top 10%) | 0.621 | 0.619 | 19 | 7 | 36.8% | [19.1%, 59.0%] | 2.21x | 3.01x | 0.0210 | 0.1682 |
| `Trading-Hero_positive >= 0.9995` (top 25%) | 0.642 | 0.673 | 58 | 16 | 27.6% | [17.8%, 40.2%] | 1.66x | 2.26x | 0.0098 | 0.1098 |
| `Trading-Hero_neutral <= 0.0004` (top 25%) | 0.368 | 0.676 | 58 | 16 | 27.6% | [17.8%, 40.2%] | 1.66x | 2.26x | 0.0098 | 0.1098 |
| `Trading-Hero_sentiment_score >= 0.9994` (top 25%) | 0.646 | 0.674 | 58 | 16 | 27.6% | [17.8%, 40.2%] | 1.66x | 2.26x | 0.0098 | 0.1098 |

**Clinical_Update** — n=151, class hit-rate 9.9% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `Trading-Hero_neutral <= 0.0008` (top 25%) | 0.368 | 0.692 | 39 | 9 | 23.1% | [12.6%, 38.3%] | 2.32x | 1.89x | 0.0033 | 0.0592 |
| `Trading-Hero_sentiment_score >= 0.9989` (top 25%) | 0.646 | 0.681 | 40 | 8 | 20.0% | [10.5%, 34.8%] | 2.01x | 1.64x | 0.0261 | 0.1877 |
| `Trading-Hero_positive >= 0.999` (top 25%) | 0.642 | 0.686 | 41 | 8 | 19.5% | [10.2%, 34.0%] | 1.96x | 1.60x | 0.0284 | 0.1911 |
| `ModernBERT_positive >= 0.9509` (top 10%) | 0.633 | 0.640 | 16 | 4 | 25.0% | [10.2%, 49.5%] | 2.52x | 2.05x | 0.0563 | 0.2708 |
| `ModernBERT_sentiment_score >= 0.9357` (top 10%) | 0.629 | 0.641 | 16 | 4 | 25.0% | [10.2%, 49.5%] | 2.52x | 2.05x | 0.0563 | 0.2708 |

**Clinical_Data** — n=127, class hit-rate 17.3% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `nocoref_confidence_weighted >= 0.6034` (top 10%) | 0.651 | 0.763 | 12 | 6 | 50.0% | [25.4%, 74.6%] | 2.89x | 4.09x | 0.0064 | 0.0843 |
| `nocoref_positional >= 0.5253` (top 10%) | 0.640 | 0.761 | 12 | 6 | 50.0% | [25.4%, 74.6%] | 2.89x | 4.09x | 0.0064 | 0.0843 |
| `FLANG-BERT_positive >= 0.9542` (top 10%) | 0.661 | 0.701 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.66x | 3.78x | 0.0105 | 0.1143 |
| `FLANG-BERT_sentiment_score >= 0.9333` (top 10%) | 0.661 | 0.702 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.66x | 3.78x | 0.0105 | 0.1143 |
| `negative <= 0.0154` (top 25%) | 0.372 | 0.723 | 32 | 12 | 37.5% | [22.9%, 54.7%] | 2.16x | 3.07x | 0.0019 | 0.0426 |

**Business_Update** — n=103, class hit-rate 14.6% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `ModernBERT_sentiment_score >= 0.9489` (top 10%) | 0.629 | 0.565 | 11 | 4 | 36.4% | [15.2%, 64.6%] | 2.50x | 2.98x | 0.0527 | 0.2636 |
| `ModernBERT_Body_neutral <= 0.01314` (top 10%) | 0.445 | 0.580 | 11 | 4 | 36.4% | [15.2%, 64.6%] | 2.50x | 2.98x | 0.0527 | 0.2636 |
| `ModernBERT_Body_positive >= 0.9734` (top 10%) | 0.574 | 0.611 | 11 | 4 | 36.4% | [15.2%, 64.6%] | 2.50x | 2.98x | 0.0527 | 0.2636 |
| `ModernBERT_Body_negative <= 0.00782` (top 10%) | 0.450 | 0.606 | 11 | 4 | 36.4% | [15.2%, 64.6%] | 2.50x | 2.98x | 0.0527 | 0.2636 |
| `ModernBERT_Body_sentiment_score >= 0.9646` (top 10%) | 0.570 | 0.611 | 11 | 4 | 36.4% | [15.2%, 64.6%] | 2.50x | 2.98x | 0.0527 | 0.2636 |

**Patent_IP** — n=73, class hit-rate 16.4% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-ELECTRA_Body_positive >= 0.9983` (top 10%) | 0.595 | 0.776 | 14 | 7 | 50.0% | [26.8%, 73.2%] | 3.04x | 4.09x | 0.0010 | 0.0297 |
| `finbert-tone_Body_sentiment_score >= 1` (top 10%) | 0.563 | 0.695 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.81x | 3.78x | 0.0052 | 0.0750 |
| `finbert-tone_Body_positive >= 1` (top 10%) | 0.557 | 0.689 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.81x | 3.78x | 0.0052 | 0.0750 |
| `finbert-tone_Body_neutral <= 0` (top 10%) | 0.455 | 0.682 | 13 | 6 | 46.2% | [23.2%, 70.9%] | 2.81x | 3.78x | 0.0052 | 0.0750 |
| `finbert-tone_neutral <= 0` (top 10%) | 0.408 | 0.714 | 11 | 5 | 45.5% | [21.3%, 72.0%] | 2.77x | 3.72x | 0.0140 | 0.1370 |

**Crypto_Treasury** — n=68, class hit-rate 13.2% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `FLANG-BERT_negative <= 0.0084` (top 33%) | 0.402 | 0.848 | 24 | 9 | 37.5% | [21.2%, 57.3%] | 2.83x | 3.07x | 0.0000 | 0.0040 |
| `Trading-Hero_sentiment_score >= 0.9914` (top 33%) | 0.646 | 0.791 | 23 | 8 | 34.8% | [18.8%, 55.1%] | 2.63x | 2.85x | 0.0005 | 0.0179 |
| `Trading-Hero_positive >= 0.994` (top 33%) | 0.642 | 0.793 | 23 | 8 | 34.8% | [18.8%, 55.1%] | 2.63x | 2.85x | 0.0005 | 0.0179 |
| `Trading-Hero_neutral <= 0.003579` (top 33%) | 0.368 | 0.789 | 23 | 8 | 34.8% | [18.8%, 55.1%] | 2.63x | 2.85x | 0.0005 | 0.0179 |
| `FLANG-ELECTRA_positive >= 0.9982` (top 25%) | 0.654 | 0.798 | 20 | 7 | 35.0% | [18.1%, 56.7%] | 2.64x | 2.86x | 0.0019 | 0.0425 |

**Regain_Compliance** — n=49, class hit-rate 22.4% (corpus 12.2%)

| encoder cut | corpus AUC | within-class AUC | fires | movers | hit-rate | 95% CI | lift vs class | lift vs corpus | Fisher p | BH q |
|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|
| `ModernBERT_positive >= 0.5777` (top 33%) | 0.633 | 0.677 | 16 | 7 | 43.8% | [23.1%, 66.8%] | 1.95x | 3.58x | 0.0253 | 0.1877 |
| `FLANG-BERT_Body_neutral <= 0.6374` (top 50%) | 0.419 | 0.687 | 25 | 9 | 36.0% | [20.2%, 55.5%] | 1.60x | 2.95x | 0.0374 | 0.2244 |
| `finbert-tone_sentiment_score >= 0.7034` (top 33%) | 0.607 | 0.656 | 16 | 6 | 37.5% | [18.5%, 61.4%] | 1.67x | 3.07x | 0.1414 | 0.4383 |
| `FinMarBa_negative <= 0.07191` (top 33%) | 0.409 | 0.667 | 16 | 6 | 37.5% | [18.5%, 61.4%] | 1.67x | 3.07x | 0.1414 | 0.4383 |
| `RoBERTa_negative <= 0.01262` (top 33%) | 0.447 | 0.632 | 16 | 6 | 37.5% | [18.5%, 61.4%] | 1.67x | 3.07x | 0.1414 | 0.4383 |

## Conclusion

**BroadClass** — 2 class(es) significantly ABOVE the 12.2% base rate after FDR: `MA` 25.6% (2.09x, n=293), `Commercial` 15.9% (1.30x, n=724).

&nbsp;&nbsp;4 significantly BELOW (veto candidates): `Legal` 0.0% (0.00x, n=52), `Corporate` 6.9% (0.56x, n=392), `Earnings` 7.0% (0.57x, n=514), `Other` 7.8% (0.64x, n=347).

**FineClass** — 2 class(es) significantly ABOVE the 12.2% base rate after FDR: `MA` 25.6% (2.09x, n=293), `Partnership` 19.9% (1.63x, n=256).

&nbsp;&nbsp;6 significantly BELOW (veto candidates): `Legal` 0.0% (0.00x, n=52), `Reverse_Split` 1.9% (0.16x, n=52), `Management_Change` 3.6% (0.30x, n=166), `Conference_Presentation` 4.9% (0.40x, n=123), `Earnings_Results` 7.2% (0.59x, n=487), `Other` 7.8% (0.64x, n=347).


**Class x encoder** — 422 of 9114 cells survive BH-FDR at q<0.05. Strongest by lower Wilson bound (one row per class, best cut):

| class col | class | cut | fires | hit-rate | 95% CI | lift vs class | q |
|---|---|---|---:|---:|---|---:|---:|
| FineClass | MA | `FinMarBa_indecisive <= 0.1616` | 147 | 35.4% | [28.1%, 43.4%] | 1.38x | 0.0095 |
| BroadClass | MA | `FinMarBa_indecisive <= 0.1616` | 147 | 35.4% | [28.1%, 43.4%] | 1.38x | 0.0095 |
| FineClass | Patent_IP | `FLANG-ELECTRA_Body_positive >= 0.9983` | 14 | 50.0% | [26.8%, 73.2%] | 3.04x | 0.0297 |
| BroadClass | IP | `FLANG-ELECTRA_Body_positive >= 0.9983` | 14 | 50.0% | [26.8%, 73.2%] | 3.04x | 0.0297 |
| BroadClass | Financing | `nocoref_neutral_filter >= 0.5263` | 23 | 43.5% | [25.6%, 63.2%] | 3.26x | 0.0094 |
| FineClass | Offering_Financing | `nocoref_neutral_filter >= 0.5263` | 21 | 42.9% | [24.5%, 63.5%] | 3.21x | 0.0161 |
| FineClass | Clinical_Data | `negative <= 0.0154` | 32 | 37.5% | [22.9%, 54.7%] | 2.16x | 0.0426 |
| FineClass | FDA_Designation | `FLANG-BERT_sentiment_score >= 0.9714` | 29 | 37.9% | [22.7%, 56.0%] | 3.46x | 0.0049 |
| FineClass | Partnership | `FLANG-ELECTRA_sentiment_score >= 0.9976` | 134 | 29.1% | [22.1%, 37.3%] | 1.46x | 0.0094 |
| BroadClass | Commercial | `FLANG-ELECTRA_positive >= 0.9982` | 255 | 26.3% | [21.3%, 32.0%] | 1.65x | 0.0001 |
| FineClass | Crypto_Treasury | `FLANG-BERT_negative <= 0.0084` | 24 | 37.5% | [21.2%, 57.3%] | 2.83x | 0.0040 |
| BroadClass | Crypto | `FLANG-BERT_negative <= 0.0084` | 24 | 37.5% | [21.2%, 57.3%] | 2.83x | 0.0040 |
