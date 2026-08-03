# Best thresholds: `FLANG-BERT_sentiment_score`, `positive`, `FinMarBa_indecisive` vs `fromPR_High(%) >= 30`
**Generated:** 2026-07-28  |  **Input:** `04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv`

## 1. Data & method

- Rows with a `fromPR_High(%)` value: **3434**  |  movers (`fromPR_High(%) >= 30`): **421**  |  base rate **12.3%**.
- Blocks (split at `2026-06-20`): early n=2647 base 10.5%  |  recent n=787 base 18.0%.
- Both FinBERT and FinMarBa are externally trained (never saw this target), so all rows are an honest test. The block split only gates the recommendation: a rule must keep lift >= 1x in BOTH blocks (else it is a window artifact).
- Metrics per cutoff: **fires** (rows firing), **TP** (fires that are movers), **precision** = TP/fires, **recall** = TP/all movers, **lift** = precision / base rate, with Wilson 95% CI on precision.
- Fire direction: `FLANG-BERT_sentiment_score`, `positive` fire HIGH (`>=`); `FinMarBa_indecisive` fire LOW (`<=`).

## 2. Single-column AUC (orientation)

AUC that each column, in its fire direction, ranks a mover above a non-mover. 0.50 = no signal.

| column | dir | AUC |
|---|---|---|
| `FLANG-BERT_sentiment_score` | >= | 0.662 |
| `positive` | >= | 0.653 |
| `FinMarBa_indecisive` | <= | 0.653 |

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

### `FinMarBa_indecisive`  (fire <=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| <= 0.214 | 2403 | 365 | 15% | 87% | 1.24x | [14%, 17%] |
| <= 0.201 | 2071 | 330 | 16% | 78% | 1.30x | [14%, 18%] |
| <= 0.186 | 1723 | 296 | 17% | 70% | 1.40x | [15%, 19%] |
| <= 0.172 | 1388 | 255 | 18% | 61% | 1.50x | [16%, 20%] |
| <= 0.155 | 1028 | 204 | 20% | 48% | 1.62x | [18%, 22%] |
| <= 0.148 | 873 | 173 | 20% | 41% | 1.62x | [17%, 23%] |
| <= 0.14 | 692 | 136 | 20% | 32% | 1.60x | [17%, 23%] |
| <= 0.131 | 509 | 96 | 19% | 23% | 1.54x | [16%, 22%] |
| <= 0.121 | 343 | 70 | 20% | 17% | 1.66x | [16%, 25%] |
| <= 0.105 | 171 | 33 | 19% | 8% | 1.57x | [14%, 26%] |

**Knee:** `FinMarBa_indecisive <= 0.155` -> precision 20%, recall 48%, lift 1.62x (204/1028). Block check: early (<2026-06-20) 1.70x (131/732); recent (>=2026-06-20) 1.37x (73/296)  ->  **PASS**

**High-prec:** `FinMarBa_indecisive <= 0.121` -> precision 20%, recall 17%, lift 1.66x (70/343). Block check: early (<2026-06-20) 1.90x (49/245); recent (>=2026-06-20) 1.19x (21/98)  ->  **PASS**

## 4. AND combinations (precision-ranked)

All column pairs & triples, over candidate cutoffs (per-column quantiles plus round anchor levels such as `positive` 0.5/0.6/0.7/0.8 and `indecisive` 0.08/0.10/0.12/0.15). De-cloned: a longer rule whose fire set is identical to a simpler one is dropped. Kept: fires >= 12 and Wilson-low > base (12.3%). `consist` = both-block lift>=1x guard.

| rule | fires | TP | precision | recall | lift | Wilson95 | consist |
|---|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_indecisive <= 0.1 | 28 | 9 | 32% | 2% | 2.62x | [18%, 51%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_indecisive <= 0.121 | 70 | 21 | 30% | 5% | 2.45x | [21%, 42%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.7 AND FinMarBa_indecisive <= 0.1 | 20 | 6 | 30% | 1% | 2.45x | [15%, 52%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.5 AND FinMarBa_indecisive <= 0.121 | 67 | 20 | 30% | 5% | 2.43x | [20%, 42%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.239 AND FinMarBa_indecisive <= 0.1 | 27 | 8 | 30% | 2% | 2.42x | [16%, 48%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.6 AND FinMarBa_indecisive <= 0.121 | 61 | 18 | 30% | 4% | 2.41x | [20%, 42%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.466 AND FinMarBa_indecisive <= 0.121 | 68 | 20 | 29% | 5% | 2.40x | [20%, 41%] | PASS |
| FLANG-BERT_sentiment_score >= 0.95 AND FinMarBa_indecisive <= 0.1 | 41 | 12 | 29% | 3% | 2.39x | [18%, 44%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.239 AND FinMarBa_indecisive <= 0.121 | 69 | 20 | 29% | 5% | 2.36x | [20%, 41%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_indecisive <= 0.105 | 42 | 12 | 29% | 3% | 2.33x | [17%, 44%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND FinMarBa_indecisive <= 0.1 | 35 | 10 | 29% | 2% | 2.33x | [16%, 45%] | PASS |
| FLANG-BERT_sentiment_score >= 0.95 AND positive >= 0.239 AND FinMarBa_indecisive <= 0.1 | 39 | 11 | 28% | 3% | 2.30x | [17%, 44%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.466 AND FinMarBa_indecisive <= 0.105 | 40 | 11 | 28% | 3% | 2.24x | [16%, 43%] | PASS |
| FLANG-BERT_sentiment_score >= 0.978 AND positive >= 0.7 AND FinMarBa_indecisive <= 0.121 | 55 | 15 | 27% | 4% | 2.22x | [17%, 40%] | PASS |
| FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.239 AND FinMarBa_indecisive <= 0.1 | 33 | 9 | 27% | 2% | 2.22x | [15%, 44%] | PASS |

### Focused sweep: `positive` (>=) x `FinMarBa_indecisive` (<=)

Cell = precision% / recall% (fires) for `(positive >= p AND FinMarBa_indecisive <= q)`.

| positive \ FinMarBa_indecisive | <= 0.15 | <= 0.13 | <= 0.1 | <= 0.08 |
|---|---|---|---|---|
| >= 0.5 | 22% / 29% (550) | 22% / 16% (318) | 24% / 5% (85) | 0% / 0% (8) |
| >= 0.6 | 23% / 26% (489) | 22% / 15% (286) | 23% / 4% (74) | 0% / 0% (5) |
| >= 0.7 | 21% / 20% (400) | 19% / 11% (234) | 23% / 3% (61) | 0% / 0% (4) |
| >= 0.8 | 22% / 13% (241) | 20% / 7% (145) | 22% / 2% (32) | 0% / 0% (3) |

Highest precision at >= 12 fires: `positive >= 0.5 AND FinMarBa_indecisive <= 0.1` -> 24% precision, 5% recall, 85 fires (1.92x). Widest coverage: `positive >= 0.5 AND FinMarBa_indecisive <= 0.15` -> 22% / 29% (550 fires).

## 5. OR combinations (recall-ranked)

Same candidate legs, unioned. OR trades precision for coverage; kept: precision >= base (12.3%). Ranked by recall.

| rule | fires | TP | precision | recall | lift | consist |
|---|---|---|---|---|---|---|
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2340 | 367 | 16% | 87% | 1.28x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2287 | 364 | 16% | 86% | 1.30x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.6 OR FinMarBa_indecisive <= 0.186 | 2247 | 362 | 16% | 86% | 1.31x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.5 OR FinMarBa_indecisive <= 0.186 | 2260 | 362 | 16% | 86% | 1.31x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.466 OR FinMarBa_indecisive <= 0.186 | 2264 | 362 | 16% | 86% | 1.30x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.7 OR FinMarBa_indecisive <= 0.186 | 2229 | 361 | 16% | 86% | 1.32x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.702 OR FinMarBa_indecisive <= 0.186 | 2226 | 360 | 16% | 86% | 1.32x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.873 OR FinMarBa_indecisive <= 0.186 | 2208 | 359 | 16% | 85% | 1.33x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.8 OR FinMarBa_indecisive <= 0.186 | 2211 | 359 | 16% | 85% | 1.32x | PASS |
| FLANG-BERT_sentiment_score >= 0.731 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2221 | 359 | 16% | 85% | 1.32x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR FinMarBa_indecisive <= 0.186 | 2203 | 358 | 16% | 85% | 1.33x | PASS |
| FLANG-BERT_sentiment_score >= 0.8 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.186 | 2212 | 358 | 16% | 85% | 1.32x | PASS |
| FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.239 OR FinMarBa_indecisive <= 0.155 | 2148 | 354 | 16% | 84% | 1.34x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.6 OR FinMarBa_indecisive <= 0.186 | 2167 | 353 | 16% | 84% | 1.33x | PASS |
| FLANG-BERT_sentiment_score >= 0.5 OR positive >= 0.5 OR FinMarBa_indecisive <= 0.186 | 2183 | 353 | 16% | 84% | 1.32x | PASS |

## 6. Recommended rules

Only rules passing the both-block consistency guard are listed. Precision/recall are full-corpus.

### Best single-column cutoffs

Knee = balanced (max F0.5); High-prec = strictest cutoff still beating base. Both must pass the both-block guard.

| column | knee rule | knee P / R | high-prec rule | high-prec P / R |
|---|---|---|---|---|
| `FLANG-BERT_sentiment_score` | >= 0.948 (PASS) | 24% / 29% | >= 0.98 (PASS) | 26% / 11% |
| `positive` | >= 0.569 (PASS) | 21% / 50% | >= 0.784 (PASS) | 21% / 26% |
| `FinMarBa_indecisive` | <= 0.155 (PASS) | 20% / 48% | <= 0.121 (PASS) | 20% / 17% |

### Best AND rules

_Purity_ = highest precision that passes the guard (few fires, size-up conviction signal). _Balanced_ = best F0.5 (more fires, still precise).

- **Purity:** `FLANG-BERT_sentiment_score >= 0.978 AND FinMarBa_indecisive <= 0.1` -> precision **32%**, recall **2%**, lift **2.62x** (9/28).
- **Balanced:** `FLANG-BERT_sentiment_score >= 0.958 AND positive >= 0.239` -> precision **26%**, recall **24%**, lift **2.11x** (102/394).

### Best OR rule (coverage)
- `FLANG-BERT_sentiment_score >= 0.356 OR positive >= 0.6 OR FinMarBa_indecisive <= 0.186` -> precision **16%**, recall **86%**, lift **1.31x** (362/2247), consistency PASS.

_Caveat: `fromPR_High(%)` is peak excursion, not capturable P&L; precision here is 'fraction of fires that touched +30%', an upper bound on tradeable hit-rate._

## 7. Folding in the live `model_proba_PLUS_kw_mult_nb >= 0.67` leg (honest block only)

_Column `model_proba_PLUS_kw_mult_nb` not in input; section skipped._

