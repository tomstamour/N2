# Walk-forward test — fromPR_High(%) scores on 2026-06-20 → 07-09 PRs

- In-sample: `PR-stats-2026-05-03to06-19-filtered-50float-12high_RBscore_body_pricefilled_clean_keywordScored_created_fromPRhigh_excStr2_scoredFromPRmodel.tsv` — 2682 labeled rows, base rate ≥15: 18.5%, ≥40: 5.9%.
- Walk-forward: `02_PRstats_2026-06-20-2026-07-09_unfiltered_fromPRhigh_excStr2_RBscored_scoredFromPRmodel_comboScore.tsv` — 421 labeled rows (excStr2-filtered, scored with the FROZEN 05-03→06-19 models; zero ID overlap verified), base rate ≥15: 26.8%, ≥40: 9.5%.
- `model_proba_PLUS_kw_mult_nb` in-sample here = frozen `_comboScore_model.json` applied to training rows (full-fit); the precision table's in-sample cells come from the original OOF-based TPFP file.

## AUC / Spearman / lift@top-10% — in-sample vs walk-forward

| column | AUC≥15 in | AUC≥15 **wf** | AUC≥40 in | AUC≥40 **wf** | ρ in | ρ **wf** | lift15 in | lift15 **wf** |
|---|---|---|---|---|---|---|---|---|
| model_proba_PLUS_kw_mult_nb | 0.911 | **0.664** | 0.885 | **0.679** | 0.476 | **0.180** | 4.26x | **2.22x** |
| model_proba | 0.907 | **0.676** | 0.884 | **0.684** | 0.471 | **0.203** | 4.22x | **2.22x** |
| model_proba_hl | 0.864 | **0.613** | 0.853 | **0.612** | 0.396 | **0.156** | 3.75x | **1.86x** |
| kw_mult_nb | 0.797 | **0.656** | 0.798 | **0.663** | 0.357 | **0.215** | 2.80x | **2.04x** |
| kw_mult_nb_log | 0.791 | **0.661** | 0.791 | **0.651** | 0.352 | **0.206** | 2.58x | **1.69x** |
| kw_add_lin | 0.768 | **0.625** | 0.778 | **0.639** | 0.326 | **0.148** | 2.58x | **1.86x** |
| Headline-RBscore | 0.640 | **0.643** | 0.662 | **0.640** | 0.191 | **0.241** | 2.06x | **1.95x** |
| positive | 0.600 | **0.589** | 0.621 | **0.593** | 0.157 | **0.130** | 1.49x | **1.33x** |
| nocoref_neutral_filter | 0.575 | **0.595** | 0.594 | **0.566** | 0.132 | **0.132** | 1.48x | **0.89x** |

## Trigger precision at fromPR_High(%) ≥ 40 (cells TP/FP; precision = TP/(TP+FP))

### model_proba_PLUS_kw_mult_nb

| level | in-sample TP/FP | in prec | walk-fwd TP/FP | wf prec | wf 95% CI |
|---|---|---|---|---|---|
| 0.5 | 107/259 | 29.2% | 20/61 | 24.7% | 17%–35% |
| 0.6 | 89/212 | 29.6% | 17/49 | 25.8% | 17%–37% |
| 0.7 | 74/161 | 31.5% | 13/37 | 26.0% | 16%–40% |
| 0.8 | 52/98 | 34.7% | 7/25 | 21.9% | 11%–39% |
| 0.9 | 27/40 | 40.3% | 1/12 | 7.7% | 1%–33% |
| 0.95 | 15/18 | 45.5% | 0/3 | 0.0% | 0%–56% |

### model_proba

| level | in-sample TP/FP | in prec | walk-fwd TP/FP | wf prec | wf 95% CI |
|---|---|---|---|---|---|
| 0.5 | 135/569 | 19.2% | 26/169 | 13.3% | 9%–19% |
| 0.6 | 99/230 | 30.1% | 19/54 | 26.0% | 17%–37% |
| 0.7 | 31/58 | 34.8% | 2/12 | 14.3% | 4%–40% |
| 0.8 | 2/4 | 33.3% | 0/0 | — | — |
| 0.9 | 0/0 | — | 0/0 | — | — |
| 0.95 | 0/0 | — | 0/0 | — | — |

### model_proba_hl

| level | in-sample TP/FP | in prec | walk-fwd TP/FP | wf prec | wf 95% CI |
|---|---|---|---|---|---|
| 0.5 | 132/659 | 16.7% | 17/148 | 10.3% | 7%–16% |
| 0.6 | 103/337 | 23.4% | 14/74 | 15.9% | 10%–25% |
| 0.7 | 49/135 | 26.6% | 9/26 | 25.7% | 14%–42% |
| 0.8 | 11/32 | 25.6% | 3/2 | 60.0% | 23%–88% |
| 0.9 | 0/0 | — | 0/0 | — | — |
| 0.95 | 0/0 | — | 0/0 | — | — |

## Caveats

- Small sample: 421 labeled walk-forward rows, only 40 positives at ≥40 — Wilson CIs are wide; single-cell precisions are noisy.
- Base rates differ (≥15: 26.8% wf vs 18.5% in-sample) — compare AUC/lift, not raw precision alone.
- In-sample keyword-model numbers were known to be inflated (model trained on that corpus); this walk-forward is the honest measurement.
