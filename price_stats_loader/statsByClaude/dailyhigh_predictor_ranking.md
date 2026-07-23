# DailyHigh(%) predictor analysis — fconcatenated_enriched_FinBERT_filtered-50float-12high.tsv

- Usable rows (target present): **1427**
- Big-mover threshold: **DailyHigh(%) >= 20.0**
- Big movers: **151** (10.6%)  |  rest: 1276
- CV folds for combos: 5
- Metric: ROC AUC (0.50 = no signal, 1.0 = perfect). Combos use pooled out-of-fold AUC.

## Single-column ranking (parameter-free AUC)

| rank | column | n | AUC | direction | point-biserial r | Spearman vs raw % |
|---|---|---|---|---|---|---|
| 1 | positive | 1324 | 0.703 | higher->bigger | +0.237 | +0.263 |
| 2 | positive_minus_neutral | 1324 | 0.692 | higher->bigger | +0.229 | +0.256 |
| 3 | neutral_filter | 1374 | 0.682 | higher->bigger | +0.194 | +0.196 |
| 4 | sentiment_score | 1427 | 0.680 | higher->bigger | +0.183 | +0.222 |
| 5 | positional | 1374 | 0.680 | higher->bigger | +0.196 | +0.205 |
| 6 | net_score | 1374 | 0.674 | higher->bigger | +0.192 | +0.194 |
| 7 | confidence_weighted | 1374 | 0.673 | higher->bigger | +0.188 | +0.191 |
| 8 | neutral | 1427 | 0.343 | lower->bigger | -0.173 | -0.204 |
| 9 | negative | 1427 | 0.356 | lower->bigger | -0.024 | -0.192 |
| 10 | top_k | 1374 | 0.606 | higher->bigger | +0.113 | +0.139 |

## Combo ranking (2- & 3-column, cross-validated AUC)

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | neutral_filter + positive | 1275 | 0.728 | neutral_filter=+0.35, positive=+0.47 |
| 2 | neutral_filter + net_score + positive | 1275 | 0.728 | neutral_filter=+0.34, net_score=+0.01, positive=+0.47 |
| 3 | neutral_filter + positional + positive | 1275 | 0.727 | neutral_filter=+0.24, positional=+0.13, positive=+0.45 |
| 4 | neutral_filter + positive + positive_minus_neutral | 1275 | 0.727 | neutral_filter=+0.35, positive=+0.44, positive_minus_neutral=+0.04 |
| 5 | neutral_filter + confidence_weighted + positive | 1275 | 0.726 | neutral_filter=+0.30, confidence_weighted=+0.05, positive=+0.47 |
| 6 | neutral_filter + neutral + positive_minus_neutral | 1275 | 0.725 | neutral_filter=+0.37, neutral=+0.13, positive_minus_neutral=+0.58 |
| 7 | top_k + positional + positive | 1275 | 0.725 | top_k=-0.13, positional=+0.41, positive=+0.46 |
| 8 | sentiment_score + neutral_filter + positive | 1275 | 0.725 | sentiment_score=+0.27, neutral_filter=+0.33, positive=+0.23 |
| 9 | sentiment_score + neutral_filter + positive_minus_neutral | 1275 | 0.725 | sentiment_score=+0.34, neutral_filter=+0.33, positive_minus_neutral=+0.17 |
| 10 | confidence_weighted + positional + positive | 1275 | 0.724 | confidence_weighted=+0.19, positional=+0.17, positive=+0.45 |
| 11 | positional + positive | 1275 | 0.724 | positional=+0.32, positive=+0.46 |
| 12 | neutral_filter + positive + negative | 1275 | 0.724 | neutral_filter=+0.33, positive=+0.46, negative=-0.09 |
| 13 | neutral_filter + negative + positive_minus_neutral | 1275 | 0.724 | neutral_filter=+0.33, negative=-0.17, positive_minus_neutral=+0.45 |
| 14 | net_score + positional + positive | 1275 | 0.724 | net_score=+0.09, positional=+0.24, positive=+0.46 |
| 15 | neutral_filter + positive + neutral | 1275 | 0.724 | neutral_filter=+0.34, positive=+0.53, neutral=+0.05 |

## Combined leaderboard (top 12, all sizes — singles use raw AUC, combos use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | neutral_filter + positive | 2 | 0.728 |
| 2 | neutral_filter + net_score + positive | 3 | 0.728 |
| 3 | neutral_filter + positional + positive | 3 | 0.727 |
| 4 | neutral_filter + positive + positive_minus_neutral | 3 | 0.727 |
| 5 | neutral_filter + confidence_weighted + positive | 3 | 0.726 |
| 6 | neutral_filter + neutral + positive_minus_neutral | 3 | 0.725 |
| 7 | top_k + positional + positive | 3 | 0.725 |
| 8 | sentiment_score + neutral_filter + positive | 3 | 0.725 |
| 9 | sentiment_score + neutral_filter + positive_minus_neutral | 3 | 0.725 |
| 10 | confidence_weighted + positional + positive | 3 | 0.724 |
| 11 | positional + positive | 2 | 0.724 |
| 12 | neutral_filter + positive + negative | 3 | 0.724 |

## Collinearity among the 10 scores (Spearman)

| | sentiment_ | neutral_fi | confidence | net_score | top_k | positional | positive | negative | neutral | positive_m |
|---|---|---|---|---|---|---|---|---|---|---|
| sentiment_scor | 1.00 | 0.50 | 0.50 | 0.52 | 0.31 | 0.53 | 0.83 | -0.78 | -0.55 | 0.65 |
| neutral_filter | 0.50 | 1.00 | 0.97 | 0.96 | 0.71 | 0.90 | 0.34 | -0.54 | -0.21 | 0.26 |
| confidence_wei | 0.50 | 0.97 | 1.00 | 0.97 | 0.73 | 0.92 | 0.35 | -0.52 | -0.22 | 0.27 |
| net_score | 0.52 | 0.96 | 0.97 | 1.00 | 0.72 | 0.92 | 0.36 | -0.54 | -0.22 | 0.27 |
| top_k | 0.31 | 0.71 | 0.73 | 0.72 | 1.00 | 0.69 | 0.31 | -0.27 | -0.28 | 0.30 |
| positional | 0.53 | 0.90 | 0.92 | 0.92 | 0.69 | 1.00 | 0.37 | -0.54 | -0.23 | 0.28 |
| positive | 0.83 | 0.34 | 0.35 | 0.36 | 0.31 | 0.37 | 1.00 | -0.44 | -0.88 | 0.94 |
| negative | -0.78 | -0.54 | -0.52 | -0.54 | -0.27 | -0.54 | -0.44 | 1.00 | 0.12 | -0.24 |
| neutral | -0.55 | -0.21 | -0.22 | -0.22 | -0.28 | -0.23 | -0.88 | 0.12 | 1.00 | -0.99 |
| positive_minus | 0.65 | 0.26 | 0.27 | 0.27 | 0.30 | 0.28 | 0.94 | -0.24 | -0.99 | 1.00 |

## Threshold sensitivity — single-column AUC at different cutoffs

| column | >= 10% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|
| sentiment_score | 0.630 | 0.680 | 0.679 | 0.657 |
| neutral_filter | 0.626 | 0.682 | 0.670 | 0.709 |
| confidence_weighted | 0.625 | 0.673 | 0.658 | 0.709 |
| net_score | 0.620 | 0.674 | 0.658 | 0.710 |
| top_k | 0.572 | 0.606 | 0.590 | 0.665 |
| positional | 0.624 | 0.680 | 0.660 | 0.717 |
| positive | 0.653 | 0.703 | 0.717 | 0.692 |
| negative | 0.387 | 0.356 | 0.352 | 0.377 |
| neutral | 0.378 | 0.343 | 0.313 | 0.334 |
| positive_minus_neutral | 0.650 | 0.692 | 0.720 | 0.704 |
| _n big movers_ | 368 | 151 | 86 | 40 |

## Conclusion

- **Best single predictor:** `positive` (AUC 0.703, higher->bigger).
- **Best combo:** `neutral_filter + positive` (CV-AUC 0.728; lift over best single: +0.025).
- The 6 scores are derivatives of the same FinBERT output, so they are highly collinear (see matrix); combos rarely beat the best single by much.

