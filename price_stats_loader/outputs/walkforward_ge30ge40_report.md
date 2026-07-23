# Walk-forward: ge30/ge40 keyword models vs ge15 baseline — 2026-06-20 → 07-09

File: `02_PRstats_2026-06-20-2026-07-09_unfiltered_fromPRhigh_excStr2_RBscored_scoredFromPRmodel_comboScore_ge30ge40.tsv` — 421 labeled rows; base rate ≥30: 12.8% (54), ≥40: 9.5% (40). All models frozen, trained only on 05-03→06-19.

## AUC / Spearman / lift@top-10%

| column | AUC≥30 | AUC≥40 | Spearman | lift@10% (≥30) |
|---|---|---|---|---|
| model_proba_ge30 | 0.686 | 0.694 | 0.183 | 2.41x |
| model_proba_ge40 | 0.638 | 0.641 | 0.201 | 1.86x |
| model_proba_hl_ge30 | 0.623 | 0.622 | 0.190 | 2.04x |
| model_proba_hl_ge40 | 0.615 | 0.654 | 0.100 | 1.30x |
| kw_add_log_ge30 | 0.666 | 0.657 | 0.111 | 2.41x |
| kw_mult_nb_ge30 | 0.626 | 0.630 | 0.155 | 2.04x |
| model_proba | 0.685 | 0.684 | 0.203 | 2.41x |
| model_proba_hl | 0.619 | 0.612 | 0.156 | 1.86x |
| model_proba_PLUS_kw_mult_nb | 0.679 | 0.679 | 0.180 | 2.41x |
| Headline-RBscore | 0.636 | 0.640 | 0.241 | 1.49x |

## Trigger precision at fromPR_High(%) ≥ 30 (TP/FP, precision, Wilson 95% CI)

| level | model_proba_ge30 | model_proba_ge40 | model_proba_hl_ge30 | model_proba_hl_ge40 | model_proba |
|---|---|---|---|---|---|
| 0.5 | 24/76 (24%, 17%–33%) | 30/113 (21%, 15%–28%) | 28/134 (17%, 12%–24%) | 11/59 (16%, 9%–26%) | 36/159 (18%, 14%–24%) |
| 0.6 | 10/21 (32%, 19%–50%) | 0/0 | 1/3 (25%, 5%–70%) | 9/47 (16%, 9%–28%) | 23/50 (32%, 22%–43%) |
| 0.7 | 4/1 (80%, 38%–96%) | 0/0 | 0/0 | 7/37 (16%, 8%–29%) | 2/12 (14%, 4%–40%) |
| 0.8 | 0/0 | 0/0 | 0/0 | 7/26 (21%, 11%–38%) | 0/0 |
| 0.9 | 0/0 | 0/0 | 0/0 | 5/14 (26%, 12%–49%) | 0/0 |
| 0.95 | 0/0 | 0/0 | 0/0 | 4/10 (29%, 12%–55%) | 0/0 |

## Trigger precision at fromPR_High(%) ≥ 40 (TP/FP, precision, Wilson 95% CI)

| level | model_proba_ge30 | model_proba_ge40 | model_proba_hl_ge30 | model_proba_hl_ge40 | model_proba |
|---|---|---|---|---|---|
| 0.5 | 17/83 (17%, 11%–26%) | 22/121 (15%, 10%–22%) | 20/142 (12%, 8%–18%) | 9/61 (13%, 7%–23%) | 26/169 (13%, 9%–19%) |
| 0.6 | 10/21 (32%, 19%–50%) | 0/0 | 1/3 (25%, 5%–70%) | 7/49 (12%, 6%–24%) | 19/54 (26%, 17%–37%) |
| 0.7 | 4/1 (80%, 38%–96%) | 0/0 | 0/0 | 5/39 (11%, 5%–24%) | 2/12 (14%, 4%–40%) |
| 0.8 | 0/0 | 0/0 | 0/0 | 5/28 (15%, 7%–31%) | 0/0 |
| 0.9 | 0/0 | 0/0 | 0/0 | 4/15 (21%, 9%–43%) | 0/0 |
| 0.95 | 0/0 | 0/0 | 0/0 | 4/10 (29%, 12%–55%) | 0/0 |

## Notes

- 421 rows with 54 / 40 positives — CIs are wide; treat level-cell differences under ~15 points as noise.
- ge15 references (2026-07-13 report): model_proba AUC≥40 .684, combo .679, Headline-RBscore .640.
