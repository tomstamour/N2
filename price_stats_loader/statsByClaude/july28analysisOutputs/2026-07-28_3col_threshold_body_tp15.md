# Best thresholds: `FLANG-BERT_sentiment_score`, `positive`, `FinMarBa_Body_indecisive` vs `fromPR_High(%) >= 15`
**Generated:** 2026-07-28  |  **Input:** `04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv`

## 1. Data & method

- Rows with a `fromPR_High(%)` value: **3434**  |  movers (`fromPR_High(%) >= 15`): **872**  |  base rate **25.4%**.
- Blocks (split at `2026-06-20`): early n=2647 base 22.9%  |  recent n=787 base 33.9%.
- Both FinBERT and FinMarBa are externally trained (never saw this target), so all rows are an honest test. The block split only gates the recommendation: a rule must keep lift >= 1x in BOTH blocks (else it is a window artifact).
- Metrics per cutoff: **fires** (rows firing), **TP** (fires that are movers), **precision** = TP/fires, **recall** = TP/all movers, **lift** = precision / base rate, with Wilson 95% CI on precision.
- Fire direction: `FLANG-BERT_sentiment_score`, `positive` fire HIGH (`>=`); `FinMarBa_Body_indecisive` fire LOW (`<=`).

## 2. Single-column AUC (orientation)

AUC that each column, in its fire direction, ranks a mover above a non-mover. 0.50 = no signal.

| column | dir | AUC |
|---|---|---|
| `FLANG-BERT_sentiment_score` | >= | 0.643 |
| `positive` | >= | 0.652 |
| `FinMarBa_Body_indecisive` | <= | 0.607 |

## 3. Single-column frontier

Two operating points are picked per column: the **knee** (max F0.5, a precision-leaning balance that keeps recall) and the **high-precision** cutoff (strictest cutoff whose Wilson-low still beats base, firing on >= 20 rows).

### `FLANG-BERT_sentiment_score`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.078 | 2407 | 706 | 29% | 81% | 1.16x | [28%, 31%] |
| >= 0.185 | 2060 | 654 | 32% | 75% | 1.25x | [30%, 34%] |
| >= 0.356 | 1716 | 581 | 34% | 67% | 1.33x | [32%, 36%] |
| >= 0.617 | 1374 | 496 | 36% | 57% | 1.42x | [34%, 39%] |
| >= 0.829 | 1032 | 392 | 38% | 45% | 1.50x | [35%, 41%] |
| >= 0.885 | 859 | 323 | 38% | 37% | 1.48x | [34%, 41%] |
| >= 0.924 | 690 | 276 | 40% | 32% | 1.58x | [36%, 44%] |
| >= 0.948 | 512 | 219 | 43% | 25% | 1.68x | [39%, 47%] |
| >= 0.966 | 348 | 160 | 46% | 18% | 1.81x | [41%, 51%] |
| >= 0.98 | 177 | 87 | 49% | 10% | 1.94x | [42%, 56%] |

**Knee:** `FLANG-BERT_sentiment_score >= 0.829` -> precision 38%, recall 45%, lift 1.50x (392/1032). Block check: early (<2026-06-20) 1.53x (259/740); recent (>=2026-06-20) 1.34x (133/292)  ->  **PASS**

**High-prec:** `FLANG-BERT_sentiment_score >= 0.98` -> precision 49%, recall 10%, lift 1.94x (87/177). Block check: early (<2026-06-20) 1.96x (56/125); recent (>=2026-06-20) 1.76x (31/52)  ->  **PASS**

### `positive`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.128 | 2348 | 722 | 31% | 83% | 1.21x | [29%, 33%] |
| >= 0.17 | 2014 | 664 | 33% | 76% | 1.30x | [31%, 35%] |
| >= 0.239 | 1682 | 584 | 35% | 67% | 1.37x | [32%, 37%] |
| >= 0.366 | 1342 | 479 | 36% | 55% | 1.41x | [33%, 38%] |
| >= 0.569 | 1008 | 380 | 38% | 44% | 1.48x | [35%, 41%] |
| >= 0.659 | 840 | 321 | 38% | 37% | 1.50x | [35%, 42%] |
| >= 0.734 | 669 | 261 | 39% | 30% | 1.54x | [35%, 43%] |
| >= 0.784 | 505 | 222 | 44% | 25% | 1.73x | [40%, 48%] |
| >= 0.834 | 335 | 146 | 44% | 17% | 1.72x | [38%, 49%] |
| >= 0.887 | 169 | 73 | 43% | 8% | 1.70x | [36%, 51%] |

**Knee:** `positive >= 0.569` -> precision 38%, recall 44%, lift 1.48x (380/1008). Block check: early (<2026-06-20) 1.55x (257/727); recent (>=2026-06-20) 1.29x (123/281)  ->  **PASS**

**High-prec:** `positive >= 0.784` -> precision 44%, recall 25%, lift 1.73x (222/505). Block check: early (<2026-06-20) 1.84x (152/362); recent (>=2026-06-20) 1.44x (70/143)  ->  **PASS**

### `FinMarBa_Body_indecisive`  (fire <=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| <= 0.165 | 2411 | 697 | 29% | 80% | 1.14x | [27%, 31%] |
| <= 0.148 | 2043 | 615 | 30% | 71% | 1.19x | [28%, 32%] |
| <= 0.136 | 1716 | 548 | 32% | 63% | 1.26x | [30%, 34%] |
| <= 0.124 | 1367 | 446 | 33% | 51% | 1.28x | [30%, 35%] |
| <= 0.113 | 1032 | 348 | 34% | 40% | 1.33x | [31%, 37%] |
| <= 0.107 | 844 | 297 | 35% | 34% | 1.39x | [32%, 38%] |
| <= 0.102 | 683 | 255 | 37% | 29% | 1.47x | [34%, 41%] |
| <= 0.097 | 527 | 199 | 38% | 23% | 1.49x | [34%, 42%] |
| <= 0.091 | 352 | 143 | 41% | 16% | 1.60x | [36%, 46%] |
| <= 0.083 | 172 | 75 | 44% | 9% | 1.72x | [36%, 51%] |

**Knee:** `FinMarBa_Body_indecisive <= 0.136` -> precision 32%, recall 63%, lift 1.26x (548/1716). Block check: early (<2026-06-20) 1.26x (363/1261); recent (>=2026-06-20) 1.20x (185/455)  ->  **PASS**

**High-prec:** `FinMarBa_Body_indecisive <= 0.083` -> precision 44%, recall 9%, lift 1.72x (75/172). Block check: early (<2026-06-20) 1.53x (43/123); recent (>=2026-06-20) 1.92x (32/49)  ->  **PASS**

## 4. AND combinations (precision-ranked)

All column pairs & triples, over candidate cutoffs (per-column quantiles plus round anchor levels such as `positive` 0.5/0.6/0.7/0.8 and `indecisive` 0.08/0.10/0.12/0.15). De-cloned: a longer rule whose fire set is identical to a simpler one is dropped. Kept: fires >= 12 and Wilson-low > base (25.4%). `consist` = both-block lift>=1x guard.

| rule | fires | TP | precision | recall | lift | Wilson95 | consist |
|---|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08 | 24 | 18 | 75% | 2% | 2.95x | [55%, 88%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.083 | 16 | 12 | 75% | 1% | 2.95x | [51%, 90%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08 | 12 | 9 | 75% | 1% | 2.95x | [47%, 91%] | PASS |
| FLANG-BERT_sentiment_score >= 0.9 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08 | 27 | 20 | 74% | 2% | 2.92x | [55%, 87%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.083 | 27 | 20 | 74% | 2% | 2.92x | [55%, 87%] | PASS |
| FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.812 AND FinMarBa_Body_indecisive <= 0.08 | 23 | 17 | 74% | 2% | 2.91x | [54%, 87%] | PASS |
| FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.083 | 30 | 22 | 73% | 3% | 2.89x | [56%, 86%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.812 AND FinMarBa_Body_indecisive <= 0.083 | 26 | 19 | 73% | 2% | 2.88x | [54%, 86%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08 | 22 | 16 | 73% | 2% | 2.86x | [52%, 87%] | PASS |
| FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.812 AND FinMarBa_Body_indecisive <= 0.083 | 29 | 21 | 72% | 2% | 2.85x | [54%, 85%] | PASS |
| FLANG-BERT_sentiment_score >= 0.9 AND positive >= 0.812 AND FinMarBa_Body_indecisive <= 0.08 | 25 | 18 | 72% | 2% | 2.84x | [52%, 86%] | PASS |
| positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08 | 32 | 23 | 72% | 3% | 2.83x | [55%, 84%] | PASS |
| positive >= 0.812 AND FinMarBa_Body_indecisive <= 0.08 | 28 | 20 | 71% | 2% | 2.81x | [53%, 85%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.812 AND FinMarBa_Body_indecisive <= 0.08 | 21 | 15 | 71% | 2% | 2.81x | [50%, 86%] | PASS |
| FLANG-BERT_sentiment_score >= 0.9 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.083 | 36 | 25 | 69% | 3% | 2.73x | [53%, 82%] | PASS |

### Focused sweep: `positive` (>=) x `FinMarBa_Body_indecisive` (<=)

Cell = precision% / recall% (fires) for `(positive >= p AND FinMarBa_Body_indecisive <= q)`.

| positive \ FinMarBa_Body_indecisive | <= 0.15 | <= 0.12 | <= 0.1 | <= 0.08 |
|---|---|---|---|---|
| >= 0.5 | 39% / 38% (855) | 38% / 25% (566) | 41% / 15% (320) | 44% / 4% (78) |
| >= 0.6 | 39% / 33% (721) | 38% / 21% (487) | 41% / 13% (273) | 47% / 4% (68) |
| >= 0.7 | 41% / 26% (552) | 39% / 17% (380) | 41% / 10% (216) | 49% / 3% (57) |
| >= 0.8 | 46% / 17% (330) | 45% / 11% (224) | 50% / 7% (120) | 72% / 3% (32) |

Highest precision at >= 12 fires: `positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08` -> 72% precision, 3% recall, 32 fires (2.83x). Widest coverage: `positive >= 0.5 AND FinMarBa_Body_indecisive <= 0.15` -> 39% / 38% (855 fires).

## 5. OR combinations (recall-ranked)

Same candidate legs, unioned. OR trades precision for coverage; kept: precision >= base (25.4%). Ranked by recall.

| rule | fires | TP | precision | recall | lift | consist |
|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2633 | 769 | 29% | 88% | 1.15x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2589 | 764 | 30% | 88% | 1.16x | PASS |
| FLANG-BERT_sentiment_score >= 0.731 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2550 | 756 | 30% | 87% | 1.17x | PASS |
| FLANG-BERT_sentiment_score >= 0.8 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2545 | 755 | 30% | 87% | 1.17x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.5 OR FinMarBa_Body_indecisive <= 0.15 | 2566 | 753 | 29% | 86% | 1.16x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.466 OR FinMarBa_Body_indecisive <= 0.15 | 2570 | 753 | 29% | 86% | 1.15x | PASS |
| FLANG-BERT_sentiment_score >= 0.9 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2533 | 751 | 30% | 86% | 1.17x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.6 OR FinMarBa_Body_indecisive <= 0.15 | 2553 | 751 | 29% | 86% | 1.16x | PASS |
| FLANG-BERT_sentiment_score >= 0.95 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2530 | 750 | 30% | 86% | 1.17x | PASS |
| FLANG-BERT_sentiment_score >= 0.91 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2532 | 750 | 30% | 86% | 1.17x | PASS |
| positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2528 | 749 | 30% | 86% | 1.17x | PASS |
| FLANG-BERT_sentiment_score >= 0.958 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2529 | 749 | 30% | 86% | 1.17x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.7 OR FinMarBa_Body_indecisive <= 0.15 | 2538 | 748 | 29% | 86% | 1.16x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.702 OR FinMarBa_Body_indecisive <= 0.15 | 2537 | 747 | 29% | 86% | 1.16x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.812 OR FinMarBa_Body_indecisive <= 0.15 | 2518 | 744 | 30% | 85% | 1.16x | PASS |

## 6. Recommended rules

Only rules passing the both-block consistency guard are listed. Precision/recall are full-corpus.

### Best single-column cutoffs

Knee = balanced (max F0.5); High-prec = strictest cutoff still beating base. Both must pass the both-block guard.

| column | knee rule | knee P / R | high-prec rule | high-prec P / R |
|---|---|---|---|---|
| `FLANG-BERT_sentiment_score` | >= 0.829 (PASS) | 38% / 45% | >= 0.98 (PASS) | 49% / 10% |
| `positive` | >= 0.569 (PASS) | 38% / 44% | >= 0.784 (PASS) | 44% / 25% |
| `FinMarBa_Body_indecisive` | <= 0.136 (PASS) | 32% / 63% | <= 0.083 (PASS) | 44% / 9% |

### Best AND rules

_Purity_ = highest precision that passes the guard (few fires, size-up conviction signal). _Balanced_ = best F0.5 (more fires, still precise).

- **Purity:** `FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08` -> precision **75%**, recall **2%**, lift **2.95x** (18/24).
- **Balanced:** `FLANG-BERT_sentiment_score >= 0.8 AND positive >= 0.239 AND FinMarBa_Body_indecisive <= 0.15` -> precision **41%**, recall **38%**, lift **1.61x** (330/809).

### Best OR rule (coverage)
- `FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.1` -> precision **33%**, recall **79%**, lift **1.31x** (685/2057), consistency PASS.

_Caveat: `fromPR_High(%)` is peak excursion, not capturable P&L; precision here is 'fraction of fires that touched +30%', an upper bound on tradeable hit-rate._

## 7. Folding in the live `model_proba_PLUS_kw_mult_nb >= 0.67` leg (honest block only)

_Column `model_proba_PLUS_kw_mult_nb` not in input; section skipped._

