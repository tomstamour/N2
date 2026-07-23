# Best thresholds: FinBERT `positive` + FinMarBa vs `fromPR_High(%) >= 30`
**Generated:** 2026-07-21  |  **Input:** `03_PRstats_2026-05-04-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv`

## 1. Data & method

- Rows with a `fromPR_High(%)` value: **3253**  |  movers (`fromPR_High(%) >= 30`): **287**  |  base rate **8.8%**.
- Blocks (split at `2026-06-20`): early n=2675 base 8.0%  |  recent n=578 base 12.8%.
- Both FinBERT and FinMarBa are externally trained (never saw this target), so all rows are an honest test. The block split only gates the recommendation: a rule must keep lift >= 1x in BOTH blocks (else it is a window artifact).
- Metrics per cutoff: **fires** (rows firing), **TP** (fires that are movers), **precision** = TP/fires, **recall** = TP/all movers, **lift** = precision / base rate, with Wilson 95% CI on precision.
- Fire direction: `positive`, `FinMarBa_positive`, `FinMarBa_sentiment_score` fire HIGH (`>=`); `FinMarBa_negative`, `FinMarBa_indecisive` fire LOW (`<=`, a real mover PR is decisive and not negative).

## 2. Single-column AUC (orientation)

AUC that each column, in its fire direction, ranks a mover above a non-mover. 0.50 = no signal.

| column | dir | AUC |
|---|---|---|
| `positive` | >= | 0.610 |
| `FinMarBa_positive` | >= | 0.608 |
| `FinMarBa_sentiment_score` | >= | 0.598 |
| `FinMarBa_negative` | <= | 0.582 |
| `FinMarBa_indecisive` | <= | 0.631 |

## 3. Single-column frontier

Two operating points are picked per column: the **knee** (max F0.5, a precision-leaning balance that keeps recall) and the **high-precision** cutoff (strictest cutoff whose Wilson-low still beats base, firing on >= 20 rows).

### `positive`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.126 | 2224 | 230 | 10% | 80% | 1.17x | [9%, 12%] |
| >= 0.165 | 1908 | 213 | 11% | 74% | 1.27x | [10%, 13%] |
| >= 0.231 | 1591 | 184 | 12% | 64% | 1.31x | [10%, 13%] |
| >= 0.35 | 1271 | 155 | 12% | 54% | 1.38x | [11%, 14%] |
| >= 0.559 | 954 | 130 | 14% | 45% | 1.54x | [12%, 16%] |
| >= 0.655 | 793 | 105 | 13% | 37% | 1.50x | [11%, 16%] |
| >= 0.729 | 637 | 81 | 13% | 28% | 1.44x | [10%, 16%] |
| >= 0.781 | 477 | 67 | 14% | 23% | 1.59x | [11%, 17%] |
| >= 0.831 | 317 | 41 | 13% | 14% | 1.47x | [10%, 17%] |
| >= 0.888 | 161 | 21 | 13% | 7% | 1.48x | [9%, 19%] |

**Knee:** `positive >= 0.559` -> precision 14%, recall 45%, lift 1.54x (130/954). Block check: early (<2026-06-20) 1.61x (96/747); recent (>=2026-06-20) 1.28x (34/207)  ->  **PASS**

**High-prec:** `positive >= 0.781` -> precision 14%, recall 23%, lift 1.59x (67/477). Block check: early (<2026-06-20) 1.65x (49/373); recent (>=2026-06-20) 1.35x (18/104)  ->  **PASS**

### `FinMarBa_positive`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.489 | 2283 | 243 | 11% | 85% | 1.21x | [9%, 12%] |
| >= 0.531 | 1954 | 214 | 11% | 75% | 1.24x | [10%, 12%] |
| >= 0.572 | 1629 | 184 | 11% | 64% | 1.28x | [10%, 13%] |
| >= 0.612 | 1293 | 154 | 12% | 54% | 1.35x | [10%, 14%] |
| >= 0.655 | 976 | 113 | 12% | 39% | 1.31x | [10%, 14%] |
| >= 0.677 | 813 | 92 | 11% | 32% | 1.28x | [9%, 14%] |
| >= 0.7 | 655 | 79 | 12% | 28% | 1.37x | [10%, 15%] |
| >= 0.728 | 490 | 66 | 13% | 23% | 1.53x | [11%, 17%] |
| >= 0.76 | 324 | 47 | 15% | 16% | 1.64x | [11%, 19%] |
| >= 0.794 | 164 | 18 | 11% | 6% | 1.24x | [7%, 17%] |

**Knee:** `FinMarBa_positive >= 0.76` -> precision 15%, recall 16%, lift 1.64x (47/324). Block check: early (<2026-06-20) 1.82x (37/256); recent (>=2026-06-20) 1.15x (10/68)  ->  **PASS**

**High-prec:** `FinMarBa_positive >= 0.76` -> precision 15%, recall 16%, lift 1.64x (47/324). Block check: early (<2026-06-20) 1.82x (37/256); recent (>=2026-06-20) 1.15x (10/68)  ->  **PASS**

### `FinMarBa_sentiment_score`  (fire >=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| >= 0.194 | 2276 | 235 | 10% | 82% | 1.17x | [9%, 12%] |
| >= 0.264 | 1955 | 209 | 11% | 73% | 1.21x | [9%, 12%] |
| >= 0.329 | 1627 | 186 | 11% | 65% | 1.30x | [10%, 13%] |
| >= 0.398 | 1299 | 154 | 12% | 54% | 1.34x | [10%, 14%] |
| >= 0.47 | 977 | 111 | 11% | 39% | 1.29x | [10%, 14%] |
| >= 0.508 | 812 | 89 | 11% | 31% | 1.24x | [9%, 13%] |
| >= 0.551 | 651 | 75 | 12% | 26% | 1.31x | [9%, 14%] |
| >= 0.592 | 489 | 64 | 13% | 22% | 1.48x | [10%, 16%] |
| >= 0.645 | 327 | 46 | 14% | 16% | 1.59x | [11%, 18%] |
| >= 0.706 | 163 | 18 | 11% | 6% | 1.25x | [7%, 17%] |

**Knee:** `FinMarBa_sentiment_score >= 0.645` -> precision 14%, recall 16%, lift 1.59x (46/327). Block check: early (<2026-06-20) 1.76x (36/257); recent (>=2026-06-20) 1.12x (10/70)  ->  **PASS**

**High-prec:** `FinMarBa_sentiment_score >= 0.645` -> precision 14%, recall 16%, lift 1.59x (46/327). Block check: early (<2026-06-20) 1.76x (36/257); recent (>=2026-06-20) 1.12x (10/70)  ->  **PASS**

### `FinMarBa_negative`  (fire <=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| <= 0.295 | 2272 | 230 | 10% | 80% | 1.15x | [9%, 11%] |
| <= 0.268 | 1951 | 205 | 11% | 71% | 1.19x | [9%, 12%] |
| <= 0.241 | 1627 | 178 | 11% | 62% | 1.24x | [10%, 13%] |
| <= 0.214 | 1301 | 145 | 11% | 51% | 1.26x | [10%, 13%] |
| <= 0.181 | 971 | 105 | 11% | 37% | 1.23x | [9%, 13%] |
| <= 0.166 | 816 | 87 | 11% | 30% | 1.21x | [9%, 13%] |
| <= 0.149 | 656 | 70 | 11% | 24% | 1.21x | [9%, 13%] |
| <= 0.133 | 494 | 59 | 12% | 21% | 1.35x | [9%, 15%] |
| <= 0.111 | 321 | 43 | 13% | 15% | 1.52x | [10%, 18%] |
| <= 0.086 | 164 | 21 | 13% | 7% | 1.45x | [9%, 19%] |

**Knee:** `FinMarBa_negative <= 0.111` -> precision 13%, recall 15%, lift 1.52x (43/321). Block check: early (<2026-06-20) 1.59x (32/253); recent (>=2026-06-20) 1.26x (11/68)  ->  **PASS**

**High-prec:** `FinMarBa_negative <= 0.111` -> precision 13%, recall 15%, lift 1.52x (43/321). Block check: early (<2026-06-20) 1.59x (32/253); recent (>=2026-06-20) 1.26x (11/68)  ->  **PASS**

### `FinMarBa_indecisive`  (fire <=)

| cutoff | fires | TP | precision | recall | lift | Wilson95 |
|---|---|---|---|---|---|---|
| <= 0.215 | 2289 | 246 | 11% | 86% | 1.22x | [10%, 12%] |
| <= 0.201 | 1947 | 215 | 11% | 75% | 1.25x | [10%, 13%] |
| <= 0.187 | 1637 | 198 | 12% | 69% | 1.37x | [11%, 14%] |
| <= 0.172 | 1299 | 167 | 13% | 58% | 1.46x | [11%, 15%] |
| <= 0.156 | 990 | 134 | 14% | 47% | 1.53x | [12%, 16%] |
| <= 0.148 | 813 | 109 | 13% | 38% | 1.52x | [11%, 16%] |
| <= 0.141 | 658 | 87 | 13% | 30% | 1.50x | [11%, 16%] |
| <= 0.132 | 488 | 61 | 12% | 21% | 1.42x | [10%, 16%] |
| <= 0.121 | 316 | 44 | 14% | 15% | 1.58x | [11%, 18%] |
| <= 0.106 | 161 | 28 | 17% | 10% | 1.97x | [12%, 24%] |

**Knee:** `FinMarBa_indecisive <= 0.156` -> precision 14%, recall 47%, lift 1.53x (134/990). Block check: early (<2026-06-20) 1.55x (94/763); recent (>=2026-06-20) 1.38x (40/227)  ->  **PASS**

**High-prec:** `FinMarBa_indecisive <= 0.106` -> precision 17%, recall 10%, lift 1.97x (28/161). Block check: early (<2026-06-20) 2.13x (21/124); recent (>=2026-06-20) 1.48x (7/37)  ->  **PASS**

## 4. AND combinations (precision-ranked)

All column pairs & triples, over candidate cutoffs (per-column quantiles plus round anchor levels such as `positive` 0.5/0.6/0.7/0.8 and `indecisive` 0.08/0.10/0.12/0.15). De-cloned: a longer rule whose fire set is identical to a simpler one is dropped. Kept: fires >= 12 and Wilson-low > base (8.8%). `consist` = both-block lift>=1x guard.

| rule | fires | TP | precision | recall | lift | Wilson95 | consist |
|---|---|---|---|---|---|---|---|
| positive >= 0.6 AND FinMarBa_sentiment_score >= 0.625 AND FinMarBa_indecisive <= 0.1 | 58 | 15 | 26% | 5% | 2.93x | [16%, 38%] | PASS |
| positive >= 0.6 AND FinMarBa_negative <= 0.15 AND FinMarBa_indecisive <= 0.1 | 59 | 15 | 25% | 5% | 2.88x | [16%, 38%] | PASS |
| positive >= 0.6 AND FinMarBa_sentiment_score >= 0.536 AND FinMarBa_indecisive <= 0.1 | 64 | 16 | 25% | 6% | 2.83x | [16%, 37%] | PASS |
| positive >= 0.6 AND FinMarBa_positive >= 0.745 AND FinMarBa_indecisive <= 0.1 | 60 | 15 | 25% | 5% | 2.83x | [16%, 37%] | PASS |
| positive >= 0.7 AND FinMarBa_sentiment_score >= 0.625 AND FinMarBa_indecisive <= 0.1 | 48 | 12 | 25% | 4% | 2.83x | [15%, 39%] | PASS |
| positive >= 0.8 AND FinMarBa_indecisive <= 0.1 | 28 | 7 | 25% | 2% | 2.83x | [13%, 43%] | FAIL |
| positive >= 0.6 AND FinMarBa_positive >= 0.692 AND FinMarBa_indecisive <= 0.1 | 65 | 16 | 25% | 6% | 2.79x | [16%, 36%] | PASS |
| positive >= 0.7 AND FinMarBa_sentiment_score >= 0.536 AND FinMarBa_indecisive <= 0.1 | 53 | 13 | 25% | 5% | 2.78x | [15%, 38%] | FAIL |
| positive >= 0.7 AND FinMarBa_negative <= 0.15 AND FinMarBa_indecisive <= 0.1 | 49 | 12 | 24% | 4% | 2.78x | [15%, 38%] | PASS |
| positive >= 0.6 AND FinMarBa_indecisive <= 0.1 | 66 | 16 | 24% | 6% | 2.75x | [16%, 36%] | PASS |
| positive >= 0.7 AND FinMarBa_positive >= 0.692 AND FinMarBa_indecisive <= 0.1 | 54 | 13 | 24% | 5% | 2.73x | [15%, 37%] | FAIL |
| positive >= 0.7 AND FinMarBa_positive >= 0.745 AND FinMarBa_indecisive <= 0.1 | 50 | 12 | 24% | 4% | 2.72x | [14%, 37%] | FAIL |
| positive >= 0.8 AND FinMarBa_sentiment_score >= 0.625 AND FinMarBa_indecisive <= 0.1 | 25 | 6 | 24% | 2% | 2.72x | [11%, 43%] | FAIL |
| positive >= 0.7 AND FinMarBa_sentiment_score >= 0.65 AND FinMarBa_indecisive <= 0.1 | 46 | 11 | 24% | 4% | 2.71x | [14%, 38%] | PASS |
| positive >= 0.7 AND FinMarBa_indecisive <= 0.1 | 55 | 13 | 24% | 5% | 2.68x | [14%, 36%] | FAIL |

### Focused sweep: `positive` (>=) x `FinMarBa_indecisive` (<=)

This is the live-gate pair `(positive >= p AND FinMarBa_indecisive <= q)`. Cell = precision%% / recall%% (fires).

| positive \ indec | <= 0.05 | <= 0.1 | <= 0.15 | <= 0.2 |
|---|---|---|---|---|
| >= 0.5 | 0% / 0% (1) | 21% / 6% (76) | 13% / 24% (509) | 13% / 40% (874) |
| >= 0.6 | 0% / 0% (1) | 24% / 6% (66) | 14% / 23% (450) | 14% / 36% (748) |
| >= 0.7 | 0% / 0% (1) | 24% / 5% (55) | 13% / 17% (371) | 13% / 28% (598) |
| >= 0.8 | 0% / 0% (1) | 25% / 2% (28) | 14% / 11% (223) | 15% / 18% (356) |

The live gate's `positive >= 0.6 AND FinMarBa_indecisive <= 0.1` sits in the top-left of the meaningful range: it is the highest-precision corner (~24%) but fires on very few PRs (recall ~6%). Precision rises sharply as `indecisive` tightens; recall collapses. `indecisive <= 0.05` is below the column's floor and fires on almost nothing.

## 5. OR combinations (recall-ranked)

Same candidate legs, unioned. OR trades precision for coverage; kept: precision >= base (8.8%). Ranked by recall.

| rule | fires | TP | precision | recall | lift | consist |
|---|---|---|---|---|---|---|
| positive >= 0.231 OR FinMarBa_sentiment_score >= 0.3 OR FinMarBa_indecisive <= 0.187 | 2251 | 240 | 11% | 84% | 1.21x | PASS |
| positive >= 0.5 OR FinMarBa_sentiment_score >= 0.3 OR FinMarBa_indecisive <= 0.187 | 2126 | 237 | 11% | 83% | 1.26x | PASS |
| positive >= 0.453 OR FinMarBa_sentiment_score >= 0.3 OR FinMarBa_indecisive <= 0.187 | 2137 | 237 | 11% | 83% | 1.26x | PASS |
| positive >= 0.231 OR FinMarBa_sentiment_score >= 0.329 OR FinMarBa_indecisive <= 0.187 | 2195 | 237 | 11% | 83% | 1.22x | PASS |
| positive >= 0.231 OR FinMarBa_negative <= 0.241 OR FinMarBa_indecisive <= 0.187 | 2239 | 237 | 11% | 83% | 1.20x | PASS |
| positive >= 0.6 OR FinMarBa_sentiment_score >= 0.3 OR FinMarBa_indecisive <= 0.187 | 2090 | 235 | 11% | 82% | 1.27x | PASS |
| positive >= 0.5 OR FinMarBa_negative <= 0.241 OR FinMarBa_indecisive <= 0.187 | 2112 | 234 | 11% | 82% | 1.26x | PASS |
| positive >= 0.453 OR FinMarBa_negative <= 0.241 OR FinMarBa_indecisive <= 0.187 | 2124 | 234 | 11% | 82% | 1.25x | PASS |
| positive >= 0.231 OR FinMarBa_positive >= 0.572 OR FinMarBa_indecisive <= 0.187 | 2169 | 234 | 11% | 82% | 1.22x | PASS |
| positive >= 0.7 OR FinMarBa_sentiment_score >= 0.3 OR FinMarBa_indecisive <= 0.187 | 2061 | 233 | 11% | 81% | 1.28x | PASS |
| positive >= 0.453 OR FinMarBa_sentiment_score >= 0.329 OR FinMarBa_indecisive <= 0.187 | 2064 | 233 | 11% | 81% | 1.28x | PASS |
| positive >= 0.231 OR FinMarBa_positive >= 0.6 OR FinMarBa_indecisive <= 0.187 | 2116 | 233 | 11% | 81% | 1.25x | PASS |
| positive >= 0.231 OR FinMarBa_negative <= 0.2 OR FinMarBa_indecisive <= 0.187 | 2126 | 233 | 11% | 81% | 1.24x | PASS |
| positive >= 0.5 OR FinMarBa_sentiment_score >= 0.329 OR FinMarBa_indecisive <= 0.187 | 2048 | 232 | 11% | 81% | 1.28x | PASS |
| positive >= 0.6 OR FinMarBa_negative <= 0.241 OR FinMarBa_indecisive <= 0.187 | 2074 | 232 | 11% | 81% | 1.27x | PASS |

## 6. Recommended rules

Only rules passing the both-block consistency guard are listed. Precision/recall are full-corpus.

### Best single-column cutoffs

Knee = balanced (max F0.5); High-prec = strictest cutoff still beating base. Both must pass the both-block guard.

| column | knee rule | knee P / R | high-prec rule | high-prec P / R |
|---|---|---|---|---|
| `positive` | >= 0.559 (PASS) | 14% / 45% | >= 0.781 (PASS) | 14% / 23% |
| `FinMarBa_positive` | >= 0.76 (PASS) | 15% / 16% | >= 0.76 (PASS) | 15% / 16% |
| `FinMarBa_sentiment_score` | >= 0.645 (PASS) | 14% / 16% | >= 0.645 (PASS) | 14% / 16% |
| `FinMarBa_negative` | <= 0.111 (PASS) | 13% / 15% | <= 0.111 (PASS) | 13% / 15% |
| `FinMarBa_indecisive` | <= 0.156 (PASS) | 14% / 47% | <= 0.106 (PASS) | 17% / 10% |

### Best AND rules

_Purity_ = highest precision that passes the guard (few fires, size-up conviction signal). _Balanced_ = best F0.5 (more fires, still precise).

- **Purity:** `positive >= 0.6 AND FinMarBa_sentiment_score >= 0.625 AND FinMarBa_indecisive <= 0.1` -> precision **26%**, recall **5%**, lift **2.93x** (15/58).
- **Balanced:** `positive >= 0.6 AND FinMarBa_positive >= 0.745 AND FinMarBa_sentiment_score >= 0.625` -> precision **18%**, recall **15%**, lift **1.99x** (42/239).

### Best OR rule (coverage)
- `positive >= 0.7 OR FinMarBa_sentiment_score >= 0.329 OR FinMarBa_indecisive <= 0.187` -> precision **12%**, recall **79%**, lift **1.31x** (228/1980), consistency PASS.

_Caveat: `fromPR_High(%)` is peak excursion, not capturable P&L; precision here is 'fraction of fires that touched +30%', an upper bound on tradeable hit-rate._

## 7. Folding in the live `model_proba_PLUS_kw_mult_nb >= 0.6` leg (honest block only)

The `model_proba_PLUS_kw_mult_nb` keyword model trained on the early (<2026-06-20) block, so only the recent block is an honest test for it. All of section 7 is on that block: **n=578, movers=74, base 12.8%**.

**Baseline — `model_proba_PLUS_kw_mult_nb >= 0.6` alone:** fires **92**, TP **28**, precision **30%**, recall 38%, lift **2.38x**.

For each sentiment rule S, three deployment views:

- **OR union** `combo OR S` — the combined trigger.
- **marginal** = fires where **combo misses but S fires** — the movers S would *add*, and the precision of those added fires. This is the real 'does S earn an OR slot' test: marginal lift <= 1x means S only adds noise on top of combo.
- **AND upgrade** `combo AND S` — S as a conviction filter on combo's own fires; delta = precision(combo AND S) − precision(combo), with a Newcombe 95% CI (contains 0 -> not demonstrated).

| sentiment rule S | S alone (P/R) | combo OR S (P/R) | marginal adds (TP/fires, P, lift) | combo AND S (P/R) | AND delta [95% CI] |
|---|---|---|---|---|---|
| live sentiment leg: positive>=0.6 AND indecisive<=0.1 | 16%/4% | 28%/42% | 3/19, 16%, 1.23x | -/0% | no fires |
| positive >= 0.559 (knee) | 16%/46% | 19%/68% | 22/171, 13%, 1.00x | 33%/16% | +3% [-14, +21] n.s. |
| positive >= 0.781 (high-prec) | 17%/24% | 23%/57% | 14/90, 16%, 1.22x | 29%/5% | -2% [-21, +26] n.s. |
| FinMarBa_indecisive <= 0.156 (knee) | 18%/54% | 18%/68% | 22/180, 12%, 0.95x | 38%/24% | +8% [-8, +24] n.s. |
| FinMarBa_indecisive <= 0.106 (high-prec) | 19%/9% | 26%/43% | 4/33, 12%, 0.95x | 75%/4% | +45% [-1, +67] n.s. |
| AND-purity: positive >= 0.6 AND FinMarBa_sentiment_score >= 0.625 AND FinMarBa_indecisive <= 0.1 | 19%/4% | 29%/42% | 3/16, 19%, 1.46x | -/0% | no fires |
| AND-balanced: positive >= 0.6 AND FinMarBa_positive >= 0.745 AND FinMarBa_sentiment_score >= 0.625 | 17%/12% | 25%/47% | 7/46, 15%, 1.19x | 29%/3% | -2% [-25, +35] n.s. |

### Deployment verdict

- `model_proba_PLUS_kw_mult_nb >= 0.6` is the strong leg (~30% precision, 2.38x). None of the sentiment rules match it standalone.
- As an **extra OR leg they earn little**: the marginal (combo-missed) fires run at roughly base rate (lift ~1x), i.e. the sentiment rules mostly re-select combo's own winners or add fires with no edge. The narrow live `positive>=0.6 AND indecisive<=0.1` adds a few movers slightly above base but within noise, and it is disjoint from combo (0 overlap) — which is why the live gate ORs them rather than duplicating.
- As an **AND conviction filter they do help**: tightening combo's fires with `FinMarBa_indecisive <= 0.156` (or `positive` knee) raises precision above combo alone while keeping useful volume — but on this honest block (n small) the Newcombe CI on that gain typically still spans 0, so treat it as a size-up signal, not a proven precision jump. This matches the standing recommendation: sentiment = conviction upgrade, `combo >= 0.6` = the trigger.

