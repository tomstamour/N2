# Best thresholds: `FLANG-BERT_sentiment_score`, `positive`, `FinMarBa_Body_indecisive` vs `fromPR_High(%) >= 30`
**Generated:** 2026-07-28  |  **Input:** `04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv`

## 1. Data & method

- Rows with a `fromPR_High(%)` value: **3434**  |  movers (`fromPR_High(%) >= 30`): **421**  |  base rate **12.3%**.
- Blocks (split at `2026-06-20`): early n=2647 base 10.5%  |  recent n=787 base 18.0%.
- Both FinBERT and FinMarBa are externally trained (never saw this target), so all rows are an honest test. The block split only gates the recommendation: a rule must keep lift >= 1x in BOTH blocks (else it is a window artifact).
- Metrics per cutoff: **fires** (rows firing), **TP** (fires that are movers), **precision** = TP/fires, **recall** = TP/all movers, **lift** = precision / base rate, with Wilson 95% CI on precision.
- Fire direction: `FLANG-BERT_sentiment_score`, `positive` fire HIGH (`>=`); `FinMarBa_Body_indecisive` fire LOW (`<=`).

## 2. Single-column AUC (orientation)

AUC that each column, in its fire direction, ranks a mover above a non-mover. 0.50 = no signal.

| column | dir | AUC |
|---|---|---|
| `FLANG-BERT_sentiment_score` | >= | 0.662 |
| `positive` | >= | 0.653 |
| `FinMarBa_Body_indecisive` | <= | 0.613 |

## 3. Single-column frontier

Two operating points are picked per column: the **knee** (max F0.5, a precision-leaning balance that keeps recall) and the **high-precision** cutoff (strictest cutoff whose Wilson-low still beats base, firing on >= 20 rows).

### `FLANG-BERT_sentiment_score`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.078 | 2407 | 356 | 15% | 85% | 1.21x | [13%, 16%] |
| >= 0.185 | 2060 | 334 | 16% | 79% | 1.32x | [15%, 18%] |
| >= 0.356 | 1716 | 304 | 18% | 72% | 1.45x | [16%, 20%] |
| >= 0.617 | 1374 | 273 | 20% | 65% | 1.62x | [18%, 22%] |
| >= 0.829 | 1032 | 217 | 21% | 52% | 1.72x | [19%, 24%] |
| >= 0.885 | 859 | 177 | 21% | 42% | 1.68x | [18%, 23%] |
| >= 0.924 | 690 | 152 | 22% | 36% | 1.80x | [19%, 25%] |
| >= 0.948 | 512 | 121 | 24% | 29% | 1.93x | [20%, 28%] |
| >= 0.966 | 348 | 85 | 24% | 20% | 1.99x | [20%, 29%] |
| >= 0.98 | 177 | 46 | 26% | 11% | 2.12x | [20%, 33%] |

**Knee:** `FLANG-BERT_sentiment_score >= 0.948` -> precision 24%, recall 29%, lift 1.93x (121/512). Block check: early (<2026-06-20) 1.90x (70/349); recent (>=2026-06-20) 1.73x (51/163)  ->  **PASS**

**High-prec:** `FLANG-BERT_sentiment_score >= 0.98` -> precision 26%, recall 11%, lift 2.12x (46/177). Block check: early (<2026-06-20) 1.97x (26/125); recent (>=2026-06-20) 2.13x (20/52)  ->  **PASS**

### `positive`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.128 | 2348 | 356 | 15% | 85% | 1.24x | [14%, 17%] |
| >= 0.17 | 2014 | 335 | 17% | 80% | 1.36x | [15%, 18%] |
| >= 0.239 | 1682 | 297 | 18% | 71% | 1.44x | [16%, 20%] |
| >= 0.366 | 1342 | 258 | 19% | 61% | 1.57x | [17%, 21%] |
| >= 0.569 | 1008 | 210 | 21% | 50% | 1.70x | [18%, 23%] |
| >= 0.659 | 840 | 167 | 20% | 40% | 1.62x | [17%, 23%] |
| >= 0.734 | 669 | 127 | 19% | 30% | 1.55x | [16%, 22%] |
| >= 0.784 | 505 | 108 | 21% | 26% | 1.74x | [18%, 25%] |
| >= 0.834 | 335 | 64 | 19% | 15% | 1.56x | [15%, 24%] |
| >= 0.887 | 169 | 34 | 20% | 8% | 1.64x | [15%, 27%] |

**Knee:** `positive >= 0.569` -> precision 21%, recall 50%, lift 1.70x (210/1008). Block check: early (<2026-06-20) 1.81x (139/727); recent (>=2026-06-20) 1.40x (71/281)  ->  **PASS**

**High-prec:** `positive >= 0.784` -> precision 21%, recall 26%, lift 1.74x (108/505). Block check: early (<2026-06-20) 1.91x (73/362); recent (>=2026-06-20) 1.36x (35/143)  ->  **PASS**

### `FinMarBa_Body_indecisive`  (fire <=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| <= 0.165 | 2411 | 347 | 14% | 82% | 1.17x | [13%, 16%] |
| <= 0.148 | 2043 | 308 | 15% | 73% | 1.23x | [14%, 17%] |
| <= 0.136 | 1716 | 270 | 16% | 64% | 1.28x | [14%, 18%] |
| <= 0.124 | 1367 | 227 | 17% | 54% | 1.35x | [15%, 19%] |
| <= 0.113 | 1032 | 181 | 18% | 43% | 1.43x | [15%, 20%] |
| <= 0.107 | 844 | 157 | 19% | 37% | 1.52x | [16%, 21%] |
| <= 0.102 | 683 | 136 | 20% | 32% | 1.62x | [17%, 23%] |
| <= 0.097 | 527 | 108 | 20% | 26% | 1.67x | [17%, 24%] |
| <= 0.091 | 352 | 76 | 22% | 18% | 1.76x | [18%, 26%] |
| <= 0.083 | 172 | 42 | 24% | 10% | 1.99x | [19%, 31%] |

**Knee:** `FinMarBa_Body_indecisive <= 0.102` -> precision 20%, recall 32%, lift 1.62x (136/683). Block check: early (<2026-06-20) 1.57x (83/503); recent (>=2026-06-20) 1.63x (53/180)  ->  **PASS**

**High-prec:** `FinMarBa_Body_indecisive <= 0.083` -> precision 24%, recall 10%, lift 1.99x (42/172). Block check: early (<2026-06-20) 1.70x (22/123); recent (>=2026-06-20) 2.26x (20/49)  ->  **PASS**

## 4. AND combinations (precision-ranked)

All column pairs & triples, over candidate cutoffs (per-column quantiles plus round anchor levels such as `positive` 0.5/0.6/0.7/0.8 and `indecisive` 0.08/0.10/0.12/0.15). De-cloned: a longer rule whose fire set is identical to a simpler one is dropped. Kept: fires >= 12 and Wilson-low > base (12.3%). `consist` = both-block lift>=1x guard.

| rule | fires | TP | precision | recall | lift | Wilson95 | consist |
|---|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.466 AND FinMarBa_Body_indecisive <= 0.1 | 85 | 27 | 32% | 6% | 2.59x | [23%, 42%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_Body_indecisive <= 0.091 | 51 | 16 | 31% | 4% | 2.56x | [20%, 45%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.5 AND FinMarBa_Body_indecisive <= 0.1 | 83 | 26 | 31% | 6% | 2.56x | [22%, 42%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_Body_indecisive <= 0.083 | 29 | 9 | 31% | 2% | 2.53x | [17%, 49%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_Body_indecisive <= 0.1 | 91 | 28 | 31% | 7% | 2.51x | [22%, 41%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.239 AND FinMarBa_Body_indecisive <= 0.1 | 88 | 27 | 31% | 6% | 2.50x | [22%, 41%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.5 AND FinMarBa_Body_indecisive <= 0.091 | 49 | 15 | 31% | 4% | 2.50x | [20%, 45%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND FinMarBa_Body_indecisive <= 0.083 | 56 | 17 | 30% | 4% | 2.48x | [20%, 43%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND FinMarBa_Body_indecisive <= 0.08 | 43 | 13 | 30% | 3% | 2.47x | [19%, 45%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.6 AND FinMarBa_Body_indecisive <= 0.1 | 74 | 22 | 30% | 5% | 2.42x | [21%, 41%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.6 AND FinMarBa_Body_indecisive <= 0.083 | 27 | 8 | 30% | 2% | 2.42x | [16%, 48%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.6 AND FinMarBa_Body_indecisive <= 0.091 | 44 | 13 | 30% | 3% | 2.41x | [18%, 44%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.873 AND FinMarBa_Body_indecisive <= 0.136 | 44 | 13 | 30% | 3% | 2.41x | [18%, 44%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.239 AND FinMarBa_Body_indecisive <= 0.083 | 51 | 15 | 29% | 4% | 2.40x | [19%, 43%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.7 AND FinMarBa_Body_indecisive <= 0.083 | 24 | 7 | 29% | 2% | 2.38x | [15%, 49%] | PASS |

### Focused sweep: `positive` (>=) x `FinMarBa_Body_indecisive` (<=)

Cell = precision% / recall% (fires) for `(positive >= p AND FinMarBa_Body_indecisive <= q)`.

| positive \ FinMarBa_Body_indecisive | <= 0.15 | <= 0.12 | <= 0.1 | <= 0.08 |
|---|---|---|---|---|
| >= 0.5 | 21% / 43% (855) | 21% / 28% (566) | 23% / 18% (320) | 22% / 4% (78) |
| >= 0.6 | 22% / 37% (721) | 21% / 24% (487) | 23% / 15% (273) | 22% / 4% (68) |
| >= 0.7 | 21% / 27% (552) | 19% / 18% (380) | 21% / 11% (216) | 21% / 3% (57) |
| >= 0.8 | 22% / 17% (330) | 19% / 10% (224) | 20% / 6% (120) | 25% / 2% (32) |

Highest precision at >= 12 fires: `positive >= 0.8 AND FinMarBa_Body_indecisive <= 0.08` -> 25% precision, 2% recall, 32 fires (2.04x). Widest coverage: `positive >= 0.5 AND FinMarBa_Body_indecisive <= 0.15` -> 21% / 43% (855 fires).

## 5. OR combinations (recall-ranked)

Same candidate legs, unioned. OR trades precision for coverage; kept: precision >= base (12.3%). Ranked by recall.

| rule | fires | TP | precision | recall | lift | consist |
|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2589 | 380 | 15% | 90% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2633 | 380 | 14% | 90% | 1.18x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.6 OR FinMarBa_Body_indecisive <= 0.15 | 2553 | 377 | 15% | 90% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.5 OR FinMarBa_Body_indecisive <= 0.15 | 2566 | 377 | 15% | 90% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.466 OR FinMarBa_Body_indecisive <= 0.15 | 2570 | 377 | 15% | 90% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.731 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2550 | 376 | 15% | 89% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.6 OR FinMarBa_Body_indecisive <= 0.15 | 2483 | 375 | 15% | 89% | 1.23x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.5 OR FinMarBa_Body_indecisive <= 0.15 | 2500 | 375 | 15% | 89% | 1.22x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.466 OR FinMarBa_Body_indecisive <= 0.15 | 2508 | 375 | 15% | 89% | 1.22x | PASS |
| FLANG-BERT_sentiment_score >= 0.8 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2545 | 375 | 15% | 89% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.7 OR FinMarBa_Body_indecisive <= 0.15 | 2538 | 374 | 15% | 89% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.95 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2530 | 373 | 15% | 89% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.91 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2532 | 373 | 15% | 89% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.9 OR positive >= 0.239 OR FinMarBa_Body_indecisive <= 0.15 | 2533 | 373 | 15% | 89% | 1.20x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.702 OR FinMarBa_Body_indecisive <= 0.15 | 2537 | 373 | 15% | 89% | 1.20x | PASS |

## 6. Recommended rules

Only rules passing the both-block consistency guard are listed. Precision/recall are full-corpus.

### Best single-column cutoffs

Knee = balanced (max F0.5); High-prec = strictest cutoff still beating base. Both must pass the both-block guard.

| column | knee rule | knee P / R | high-prec rule | high-prec P / R |
|---|---|---|---|---|
| `FLANG-BERT_sentiment_score` | >= 0.948 (PASS) | 24% / 29% | >= 0.98 (PASS) | 26% / 11% |
| `positive` | >= 0.569 (PASS) | 21% / 50% | >= 0.784 (PASS) | 21% / 26% |
| `FinMarBa_Body_indecisive` | <= 0.102 (PASS) | 20% / 32% | <= 0.083 (PASS) | 24% / 10% |

### Best AND rules

_Purity_ = highest precision that passes the guard (few fires, size-up conviction signal). _Balanced_ = best F0.5 (more fires, still precise).

- **Purity:** `FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.466 AND FinMarBa_Body_indecisive <= 0.1` -> precision **32%**, recall **6%**, lift **2.59x** (27/85).
- **Balanced:** `FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.239` -> precision **26%**, recall **24%**, lift **2.11x** (102/394).

### Best OR rule (coverage)
- `FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.6 OR FinMarBa_Body_indecisive <= 0.136` -> precision **16%**, recall **86%**, lift **1.30x** (362/2263), consistency PASS.

_Caveat: `fromPR_High(%)` is peak excursion, not capturable P&L; precision here is 'fraction of fires that touched +30%', an upper bound on tradeable hit-rate._

## 7. Folding in the live `model_proba_PLUS_kw_mult_nb >= 0.67` leg (honest block only)

_Column `model_proba_PLUS_kw_mult_nb` not in input; section skipped._

