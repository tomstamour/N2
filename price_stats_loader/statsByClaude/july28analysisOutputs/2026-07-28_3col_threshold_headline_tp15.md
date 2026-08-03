# Best thresholds: `FLANG-BERT_sentiment_score`, `positive`, `FinMarBa_indecisive` vs `fromPR_High(%) >= 15`
**Generated:** 2026-07-28  |  **Input:** `04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv`

## 1. Data & method

- Rows with a `fromPR_High(%)` value: **3434**  |  movers (`fromPR_High(%) >= 15`): **872**  |  base rate **25.4%**.
- Blocks (split at `2026-06-20`): early n=2647 base 22.9%  |  recent n=787 base 33.9%.
- Both FinBERT and FinMarBa are externally trained (never saw this target), so all rows are an honest test. The block split only gates the recommendation: a rule must keep lift >= 1x in BOTH blocks (else it is a window artifact).
- Metrics per cutoff: **fires** (rows firing), **TP** (fires that are movers), **precision** = TP/fires, **recall** = TP/all movers, **lift** = precision / base rate, with Wilson 95% CI on precision.
- Fire direction: `FLANG-BERT_sentiment_score`, `positive` fire HIGH (`>=`); `FinMarBa_indecisive` fire LOW (`<=`).

## 2. Single-column AUC (orientation)

AUC that each column, in its fire direction, ranks a mover above a non-mover. 0.50 = no signal.

| column | dir | AUC |
|---|---|---|
| `FLANG-BERT_sentiment_score` | >= | 0.643 |
| `positive` | >= | 0.652 |
| `FinMarBa_indecisive` | <= | 0.637 |

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

### `FinMarBa_indecisive`  (fire <=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| <= 0.214 | 2403 | 722 | 30% | 83% | 1.18x | [28%, 32%] |
| <= 0.201 | 2071 | 652 | 31% | 75% | 1.24x | [30%, 34%] |
| <= 0.186 | 1723 | 571 | 33% | 65% | 1.31x | [31%, 35%] |
| <= 0.172 | 1388 | 490 | 35% | 56% | 1.39x | [33%, 38%] |
| <= 0.155 | 1028 | 383 | 37% | 44% | 1.47x | [34%, 40%] |
| <= 0.148 | 873 | 325 | 37% | 37% | 1.47x | [34%, 40%] |
| <= 0.14 | 692 | 258 | 37% | 30% | 1.47x | [34%, 41%] |
| <= 0.131 | 509 | 188 | 37% | 22% | 1.45x | [33%, 41%] |
| <= 0.121 | 343 | 134 | 39% | 15% | 1.54x | [34%, 44%] |
| <= 0.105 | 171 | 62 | 36% | 7% | 1.43x | [29%, 44%] |

**Knee:** `FinMarBa_indecisive <= 0.155` -> precision 37%, recall 44%, lift 1.47x (383/1028). Block check: early (<2026-06-20) 1.55x (259/732); recent (>=2026-06-20) 1.23x (124/296)  ->  **PASS**

**High-prec:** `FinMarBa_indecisive <= 0.121` -> precision 39%, recall 15%, lift 1.54x (134/343). Block check: early (<2026-06-20) 1.71x (96/245); recent (>=2026-06-20) 1.14x (38/98)  ->  **PASS**

## 4. AND combinations (precision-ranked)

All column pairs & triples, over candidate cutoffs (per-column quantiles plus round anchor levels such as `positive` 0.5/0.6/0.7/0.8 and `indecisive` 0.08/0.10/0.12/0.15). De-cloned: a longer rule whose fire set is identical to a simpler one is dropped. Kept: fires >= 12 and Wilson-low > base (25.4%). `consist` = both-block lift>=1x guard.

| rule | fires | TP | precision | recall | lift | Wilson95 | consist |
|---|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.1 | 15 | 8 | 53% | 1% | 2.10x | [30%, 75%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.15 | 80 | 40 | 50% | 5% | 1.97x | [39%, 61%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_indecisive <= 0.121 | 70 | 35 | 50% | 4% | 1.97x | [39%, 61%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.466 AND FinMarBa_indecisive <= 0.121 | 68 | 34 | 50% | 4% | 1.97x | [38%, 62%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.13 | 58 | 29 | 50% | 3% | 1.97x | [38%, 62%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.7 AND FinMarBa_indecisive <= 0.1 | 20 | 10 | 50% | 1% | 1.97x | [30%, 70%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.1 | 14 | 7 | 50% | 1% | 1.97x | [27%, 73%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.186 | 165 | 82 | 50% | 9% | 1.96x | [42%, 57%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.466 AND FinMarBa_indecisive <= 0.13 | 91 | 45 | 49% | 5% | 1.95x | [39%, 60%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.5 AND FinMarBa_indecisive <= 0.13 | 89 | 44 | 49% | 5% | 1.95x | [39%, 60%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.6 AND FinMarBa_indecisive <= 0.13 | 83 | 41 | 49% | 5% | 1.95x | [39%, 60%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.812 AND FinMarBa_indecisive <= 0.15 | 77 | 38 | 49% | 4% | 1.94x | [38%, 60%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.7 AND FinMarBa_indecisive <= 0.13 | 75 | 37 | 49% | 4% | 1.94x | [38%, 60%] | PASS |
| FLANG-BERT_sentiment_score >= 0.731 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.15 | 217 | 107 | 49% | 12% | 1.94x | [43%, 56%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.239 AND FinMarBa_indecisive <= 0.121 | 69 | 34 | 49% | 4% | 1.94x | [38%, 61%] | PASS |

### Focused sweep: `positive` (>=) x `FinMarBa_indecisive` (<=)

Cell = precision% / recall% (fires) for `(positive >= p AND FinMarBa_indecisive <= q)`.

| positive \ FinMarBa_indecisive | <= 0.15 | <= 0.13 | <= 0.1 | <= 0.08 |
|---|---|---|---|---|
| >= 0.5 | 41% / 26% (550) | 41% / 15% (318) | 38% / 4% (85) | 0% / 0% (8) |
| >= 0.6 | 41% / 23% (489) | 41% / 14% (286) | 38% / 3% (74) | 0% / 0% (5) |
| >= 0.7 | 42% / 19% (400) | 42% / 11% (234) | 39% / 3% (61) | 0% / 0% (4) |
| >= 0.8 | 48% / 13% (241) | 47% / 8% (145) | 44% / 2% (32) | 0% / 0% (3) |

Highest precision at >= 12 fires: `positive >= 0.8 AND FinMarBa_indecisive <= 0.15` -> 48% precision, 13% recall, 241 fires (1.90x). Widest coverage: `positive >= 0.5 AND FinMarBa_indecisive <= 0.15` -> 41% / 26% (550 fires).

## 5. OR combinations (recall-ranked)

Same candidate legs, unioned. OR trades precision for coverage; kept: precision >= base (25.4%). Ranked by recall.

| rule | fires | TP | precision | recall | lift | consist |
|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2340 | 724 | 31% | 83% | 1.22x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2287 | 716 | 31% | 82% | 1.23x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.5 OR FinMarBa_indecisive <= 0.186 | 2260 | 707 | 31% | 81% | 1.23x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.466 OR FinMarBa_indecisive <= 0.186 | 2264 | 707 | 31% | 81% | 1.23x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.6 OR FinMarBa_indecisive <= 0.186 | 2247 | 706 | 31% | 81% | 1.24x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.7 OR FinMarBa_indecisive <= 0.186 | 2229 | 704 | 32% | 81% | 1.24x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.702 OR FinMarBa_indecisive <= 0.186 | 2226 | 703 | 32% | 81% | 1.24x | PASS |
| FLANG-BERT_sentiment_score >= 0.731 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2221 | 702 | 32% | 81% | 1.24x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.873 OR FinMarBa_indecisive <= 0.186 | 2208 | 700 | 32% | 80% | 1.25x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.8 OR FinMarBa_indecisive <= 0.186 | 2211 | 700 | 32% | 80% | 1.25x | PASS |
| FLANG-BERT_sentiment_score >= 0.8 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2212 | 700 | 32% | 80% | 1.25x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR FinMarBa_indecisive <= 0.186 | 2203 | 697 | 32% | 80% | 1.25x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.155 | 2148 | 694 | 32% | 80% | 1.27x | PASS |
| FLANG-BERT_sentiment_score >= 0.9 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2194 | 693 | 32% | 79% | 1.24x | PASS |
| FLANG-BERT_sentiment_score >= 0.95 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2183 | 692 | 32% | 79% | 1.25x | PASS |

## 6. Recommended rules

Only rules passing the both-block consistency guard are listed. Precision/recall are full-corpus.

### Best single-column cutoffs

Knee = balanced (max F0.5); High-prec = strictest cutoff still beating base. Both must pass the both-block guard.

| column | knee rule | knee P / R | high-prec rule | high-prec P / R |
|---|---|---|---|---|
| `FLANG-BERT_sentiment_score` | >= 0.829 (PASS) | 38% / 45% | >= 0.98 (PASS) | 49% / 10% |
| `positive` | >= 0.569 (PASS) | 38% / 44% | >= 0.784 (PASS) | 44% / 25% |
| `FinMarBa_indecisive` | <= 0.155 (PASS) | 37% / 44% | <= 0.121 (PASS) | 39% / 15% |

### Best AND rules

_Purity_ = highest precision that passes the guard (few fires, size-up conviction signal). _Balanced_ = best F0.5 (more fires, still precise).

- **Purity:** `FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.8 AND FinMarBa_indecisive <= 0.1` -> precision **53%**, recall **1%**, lift **2.10x** (8/15).
- **Balanced:** `FLANG-BERT_sentiment_score >= 0.731 AND positive >= 0.239 AND FinMarBa_indecisive <= 0.186` -> precision **40%**, recall **40%**, lift **1.59x** (347/859).

### Best OR rule (coverage)
- `FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.15` -> precision **33%**, recall **78%**, lift **1.30x** (677/2050), consistency PASS.

_Caveat: `fromPR_High(%)` is peak excursion, not capturable P&L; precision here is 'fraction of fires that touched +30%', an upper bound on tradeable hit-rate._

## 7. Folding in the live `model_proba_PLUS_kw_mult_nb >= 0.67` leg (honest block only)

_Column `model_proba_PLUS_kw_mult_nb` not in input; section skipped._

