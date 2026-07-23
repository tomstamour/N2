# fromPR_High(%) predictor analysis — PR-stats-2026-05-03to06-19-filtered-50float-12high_RBscore_body_pricefilled_clean_keywordScored_created_fromPRhigh_excStr2_scoredFromPRmodel.tsv

- Usable rows (target present): **2682**
- Big-mover threshold: **fromPR_High(%) >= 15.0**
- Big movers: **496** (18.5%)  |  rest: 2186
- CV folds for combos: 5
- Metric: ROC AUC (0.50 = no signal, 1.0 = perfect). Combos use pooled out-of-fold AUC.

## Single-column ranking (parameter-free AUC)

| rank | column | n | AUC | direction | point-biserial r | Spearman vs raw % |
|---|---|---|---|---|---|---|
| 1 | model_proba | 2682 | 0.907 | higher->bigger | +0.601 | +0.471 |
| 2 | model_proba_hl | 2682 | 0.864 | higher->bigger | +0.525 | +0.396 |
| 3 | kw_mult_nb_log | 2682 | 0.791 | higher->bigger | +0.329 | +0.352 |
| 4 | kw_add_lin | 2682 | 0.768 | higher->bigger | +0.368 | +0.326 |
| 5 | Headline-RBscore | 2682 | 0.640 | higher->bigger | +0.200 | +0.191 |
| 6 | n_kw_hits | 2682 | 0.629 | higher->bigger | +0.189 | +0.171 |
| 7 | positive | 2606 | 0.600 | higher->bigger | +0.129 | +0.157 |
| 8 | sentiment_score | 2682 | 0.594 | higher->bigger | +0.120 | +0.153 |
| 9 | positive_minus_neutral | 2606 | 0.593 | higher->bigger | +0.122 | +0.137 |
| 10 | negative | 2682 | 0.421 | lower->bigger | -0.066 | -0.133 |
| 11 | nocoref_positional | 2641 | 0.578 | higher->bigger | +0.106 | +0.136 |
| 12 | nocoref_confidence_weighted | 2641 | 0.576 | higher->bigger | +0.102 | +0.135 |
| 13 | nocoref_top_k | 2641 | 0.576 | higher->bigger | +0.098 | +0.112 |
| 14 | neutral | 2682 | 0.425 | lower->bigger | -0.096 | -0.109 |
| 15 | nocoref_neutral_filter | 2641 | 0.575 | higher->bigger | +0.102 | +0.132 |
| 16 | nocoref_net_score | 2641 | 0.573 | higher->bigger | +0.107 | +0.131 |

## Combo ranking (2- & 3-column, cross-validated AUC)

| rank | columns | n | CV-AUC | logistic coefs (standardized) |
|---|---|---|---|---|
| 1 | model_proba + kw_add_lin + n_kw_hits | 2682 | 0.924 | model_proba=+3.10, kw_add_lin=-0.84, n_kw_hits=-0.67 |
| 2 | model_proba + n_kw_hits + model_proba_hl | 2682 | 0.919 | model_proba=+2.10, n_kw_hits=-0.68, model_proba_hl=+0.55 |
| 3 | model_proba + kw_add_lin + model_proba_hl | 2682 | 0.919 | model_proba=+2.34, kw_add_lin=-0.88, model_proba_hl=+0.55 |
| 4 | Headline-RBscore + model_proba + kw_add_lin | 2682 | 0.917 | Headline-RBscore=-0.28, model_proba=+2.83, kw_add_lin=-0.85 |
| 5 | model_proba + kw_mult_nb_log + n_kw_hits | 2682 | 0.917 | model_proba=+2.67, kw_mult_nb_log=-0.38, n_kw_hits=-0.67 |
| 6 | Headline-RBscore + model_proba + n_kw_hits | 2682 | 0.917 | Headline-RBscore=-0.25, model_proba=+2.59, n_kw_hits=-0.66 |
| 7 | nocoref_net_score + model_proba + kw_add_lin | 2641 | 0.917 | nocoref_net_score=+0.11, model_proba=+2.74, kw_add_lin=-0.92 |
| 8 | model_proba + kw_add_lin + kw_mult_nb_log | 2682 | 0.917 | model_proba=+2.80, kw_add_lin=-0.84, kw_mult_nb_log=-0.21 |
| 9 | sentiment_score + model_proba + kw_add_lin | 2682 | 0.917 | sentiment_score=+0.15, model_proba=+2.73, kw_add_lin=-0.91 |
| 10 | nocoref_confidence_weighted + model_proba + kw_add_lin | 2641 | 0.917 | nocoref_confidence_weighted=+0.07, model_proba=+2.74, kw_add_lin=-0.92 |
| 11 | neutral + model_proba + kw_add_lin | 2682 | 0.917 | neutral=-0.14, model_proba=+2.73, kw_add_lin=-0.91 |
| 12 | nocoref_neutral_filter + model_proba + kw_add_lin | 2641 | 0.916 | nocoref_neutral_filter=+0.04, model_proba=+2.73, kw_add_lin=-0.91 |
| 13 | model_proba + kw_add_lin | 2682 | 0.916 | model_proba=+2.73, kw_add_lin=-0.90 |
| 14 | nocoref_positional + model_proba + kw_add_lin | 2641 | 0.916 | nocoref_positional=+0.12, model_proba=+2.74, kw_add_lin=-0.92 |
| 15 | negative + model_proba + kw_add_lin | 2682 | 0.916 | negative=-0.06, model_proba=+2.72, kw_add_lin=-0.89 |

## Combined leaderboard (top 12, all sizes — singles use raw AUC, combos use CV-AUC)

| rank | features | size | AUC |
|---|---|---|---|
| 1 | model_proba + kw_add_lin + n_kw_hits | 3 | 0.924 |
| 2 | model_proba + n_kw_hits + model_proba_hl | 3 | 0.919 |
| 3 | model_proba + kw_add_lin + model_proba_hl | 3 | 0.919 |
| 4 | Headline-RBscore + model_proba + kw_add_lin | 3 | 0.917 |
| 5 | model_proba + kw_mult_nb_log + n_kw_hits | 3 | 0.917 |
| 6 | Headline-RBscore + model_proba + n_kw_hits | 3 | 0.917 |
| 7 | nocoref_net_score + model_proba + kw_add_lin | 3 | 0.917 |
| 8 | model_proba + kw_add_lin + kw_mult_nb_log | 3 | 0.917 |
| 9 | sentiment_score + model_proba + kw_add_lin | 3 | 0.917 |
| 10 | nocoref_confidence_weighted + model_proba + kw_add_lin | 3 | 0.917 |
| 11 | neutral + model_proba + kw_add_lin | 3 | 0.917 |
| 12 | nocoref_neutral_filter + model_proba + kw_add_lin | 3 | 0.916 |

## Collinearity among the 16 scores (Spearman)

| | sentiment_ | nocoref_ne | nocoref_co | nocoref_ne | nocoref_to | nocoref_po | positive | negative | neutral | positive_m | Headline-R | model_prob | kw_add_lin | kw_mult_nb | n_kw_hits | model_prob |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| sentiment_scor | 1.00 | 0.50 | 0.51 | 0.53 | 0.40 | 0.56 | 0.97 | -0.69 | -0.83 | 0.90 | 0.43 | 0.27 | 0.20 | 0.19 | 0.03 | 0.29 |
| nocoref_neutra | 0.50 | 1.00 | 0.96 | 0.93 | 0.68 | 0.87 | 0.46 | -0.42 | -0.38 | 0.42 | 0.29 | 0.24 | 0.21 | 0.19 | 0.14 | 0.19 |
| nocoref_confid | 0.51 | 0.96 | 1.00 | 0.95 | 0.74 | 0.89 | 0.48 | -0.42 | -0.40 | 0.44 | 0.29 | 0.25 | 0.22 | 0.19 | 0.14 | 0.19 |
| nocoref_net_sc | 0.53 | 0.93 | 0.95 | 1.00 | 0.72 | 0.89 | 0.50 | -0.44 | -0.42 | 0.46 | 0.30 | 0.23 | 0.20 | 0.16 | 0.08 | 0.18 |
| nocoref_top_k | 0.40 | 0.68 | 0.74 | 0.72 | 1.00 | 0.69 | 0.40 | -0.30 | -0.36 | 0.38 | 0.22 | 0.23 | 0.23 | 0.20 | 0.10 | 0.19 |
| nocoref_positi | 0.56 | 0.87 | 0.89 | 0.89 | 0.69 | 1.00 | 0.54 | -0.43 | -0.46 | 0.50 | 0.30 | 0.26 | 0.23 | 0.19 | 0.10 | 0.22 |
| positive | 0.97 | 0.46 | 0.48 | 0.50 | 0.40 | 0.54 | 1.00 | -0.58 | -0.92 | 0.97 | 0.39 | 0.25 | 0.20 | 0.18 | 0.02 | 0.29 |
| negative | -0.69 | -0.42 | -0.42 | -0.44 | -0.30 | -0.43 | -0.58 | 1.00 | 0.37 | -0.47 | -0.40 | -0.24 | -0.16 | -0.18 | -0.06 | -0.22 |
| neutral | -0.83 | -0.38 | -0.40 | -0.42 | -0.36 | -0.46 | -0.92 | 0.37 | 1.00 | -0.99 | -0.31 | -0.21 | -0.18 | -0.15 | -0.01 | -0.26 |
| positive_minus | 0.90 | 0.42 | 0.44 | 0.46 | 0.38 | 0.50 | 0.97 | -0.47 | -0.99 | 1.00 | 0.35 | 0.23 | 0.19 | 0.16 | 0.01 | 0.27 |
| Headline-RBsco | 0.43 | 0.29 | 0.29 | 0.30 | 0.22 | 0.30 | 0.39 | -0.40 | -0.31 | 0.35 | 1.00 | 0.41 | 0.36 | 0.39 | 0.24 | 0.46 |
| model_proba | 0.27 | 0.24 | 0.25 | 0.23 | 0.23 | 0.26 | 0.25 | -0.24 | -0.21 | 0.23 | 0.41 | 1.00 | 0.81 | 0.89 | 0.46 | 0.73 |
| kw_add_lin | 0.20 | 0.21 | 0.22 | 0.20 | 0.23 | 0.23 | 0.20 | -0.16 | -0.18 | 0.19 | 0.36 | 0.81 | 1.00 | 0.83 | 0.38 | 0.59 |
| kw_mult_nb_log | 0.19 | 0.19 | 0.19 | 0.16 | 0.20 | 0.19 | 0.18 | -0.18 | -0.15 | 0.16 | 0.39 | 0.89 | 0.83 | 1.00 | 0.50 | 0.62 |
| n_kw_hits | 0.03 | 0.14 | 0.14 | 0.08 | 0.10 | 0.10 | 0.02 | -0.06 | -0.01 | 0.01 | 0.24 | 0.46 | 0.38 | 0.50 | 1.00 | 0.31 |
| model_proba_hl | 0.29 | 0.19 | 0.19 | 0.18 | 0.19 | 0.22 | 0.29 | -0.22 | -0.26 | 0.27 | 0.46 | 0.73 | 0.59 | 0.62 | 0.31 | 1.00 |

## Threshold sensitivity — single-column AUC at different cutoffs

| column | >= 10% | >= 20% | >= 30% | >= 50% |
|---|---|---|---|---|
| sentiment_score | 0.574 | 0.596 | 0.619 | 0.617 |
| nocoref_neutral_filter | 0.557 | 0.593 | 0.604 | 0.588 |
| nocoref_confidence_weighted | 0.558 | 0.596 | 0.613 | 0.603 |
| nocoref_net_score | 0.553 | 0.596 | 0.611 | 0.608 |
| nocoref_top_k | 0.549 | 0.588 | 0.593 | 0.603 |
| nocoref_positional | 0.556 | 0.592 | 0.607 | 0.596 |
| positive | 0.579 | 0.600 | 0.624 | 0.618 |
| negative | 0.435 | 0.416 | 0.390 | 0.367 |
| neutral | 0.441 | 0.426 | 0.396 | 0.412 |
| positive_minus_neutral | 0.573 | 0.592 | 0.620 | 0.614 |
| Headline-RBscore | 0.611 | 0.644 | 0.660 | 0.661 |
| model_proba | 0.801 | 0.899 | 0.891 | 0.882 |
| kw_add_lin | 0.709 | 0.765 | 0.780 | 0.784 |
| kw_mult_nb_log | 0.720 | 0.787 | 0.789 | 0.800 |
| n_kw_hits | 0.606 | 0.631 | 0.651 | 0.657 |
| model_proba_hl | 0.764 | 0.860 | 0.864 | 0.850 |
| _n big movers_ | 766 | 370 | 214 | 125 |

## Conclusion

- **Best single predictor:** `model_proba` (AUC 0.907, higher->bigger).
- **Best combo:** `model_proba + kw_add_lin + n_kw_hits` (CV-AUC 0.924; lift over best single: +0.016).
- The 6 scores are derivatives of the same FinBERT output, so they are highly collinear (see matrix); combos rarely beat the best single by much.

