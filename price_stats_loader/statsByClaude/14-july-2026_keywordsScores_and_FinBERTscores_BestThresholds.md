# Keyword-score & FinBERT-score trigger thresholds — analysis summary and recommendations
**Date: 2026-07-14**

## 1. Datasets and models

| dataset | period | rows (labeled) | base rate fromPR_High ≥30% | role |
|---|---|---|---|---|
| `01_PR-stats-2026-05-03to06-19-...-excStr2_KeyWordScored_.tsv` | 05-03 → 06-19 | 2,682 | 8.0% (214) | keyword-model TRAINING corpus (in-sample for kw scores, honest for FinBERT) |
| `02_PRstats_2026-06-20-2026-07-09-...-comboScore_ge30ge40_dedup.tsv` | 06-20 → 07-09 | 389 | 12.1% (47) | WALK-FORWARD (honest for everything; all models frozen) |

Models (all trained ONLY on the 05-03→06-19 corpus, body text unless noted):
- **ge15** — canonical keyword model `model_trained_on_PR-stats-2026-05-03to06-19/SELECTED-MODEL_classify_fromPRhigh_ge15_excStr2/` (label fromPR ≥15). Produces `model_proba`, `kw_*` columns. Headline twin → `model_proba_hl`.
- **combo** — `model_proba_PLUS_kw_mult_nb` = frozen logistic of (model_proba, kw_mult_nb), weights in `..._comboScore_model.json`.
- **ge30 / ge40** — same recipe retrained at labels ≥30 / ≥40 (body + headline twins). Columns suffixed `_ge30`, `_ge40`, `_hl_ge30`, `_hl_ge40`.
- **FinBERT / nocoref** — generic sentiment (never trained on either dataset): `positive`, `sentiment_score`, `nocoref_*`.

Scoring recipe (verified byte-exact reproduction of training-file scores): `keyword_scorer.py` with the model dir's artifacts **plus `--prevalence <train pos rate>`** (0.185 / 0.0797 / 0.0589).

## 2. Walk-forward decay of the keyword scores

In-sample numbers were known to be inflated (model trained on that corpus). Measured decay, AUC for fromPR_High ≥15:

| score | in-sample AUC | walk-forward AUC |
|---|---|---|
| model_proba (ge15) | .907 | **.676** |
| model_proba_PLUS_kw_mult_nb | .911 | **.664** |
| model_proba_hl | .864 | **.613** |
| kw_mult_nb_log | .791 | .661 |
| Headline-RBscore (rule-based, honest both sides) | .640 | **.643** (held) |

Key facts:
- Walk-forward lands where honest CV predicted (ge15 CV was .725); everything above that was memorization.
- The combo column adds **nothing** out-of-sample (slightly worse than model_proba alone as a ranker).
- The ge15 score still ranks its own ≥15 label well out-of-sample (combo≥0.9 → 62% precision vs 27% base), but its top tail **selects against** ≥30 movers: the highest-confidence archetype is the private-placement/financing template, which pops 16–28% and almost never 30%+.

## 3. Retraining at ≥30/≥40 did not help

| model | walk-forward AUC≥30 | AUC≥40 |
|---|---|---|
| model_proba_ge30 | 0.686 | 0.694 |
| model_proba (ge15) | 0.685 | 0.684 |
| model_proba_ge40 | 0.638 | 0.641 |
| model_proba_hl_ge30 | 0.623 | 0.622 |

ge30 is a statistical twin of ge15 (score Spearman 0.77 — same signal); ge40 is worse (only 158 training positives).
**Ceiling of body-keyword scoring for ≥30% moves on this corpus ≈ AUC 0.69, regardless of training label.**
`kw_add_log_ge40` as an extra OR trigger leg is net-negative: its unique fires (beyond combo≥0.7) ran 19% precision, mean peak +13.9%.

## 4. Data quality: nGNX/nGNE duplicates (fixed)

Post-06-20 RTPR data contained ~8% duplicate rows: same GlobeNewswire PR delivered twice (`nGNX…` + `nGNE…` IDs, ~200 ms apart, Tickers lists differ so headline+tickers dedup missed them). The training corpus was clean (pattern absent before 06-20). Orchestrator5 now prevents this. All numbers in this document use the deduplicated walk-forward file (`..._ge30ge40_dedup.tsv`, 429 → 396 rows). **Quote dedup numbers only.**

## 5. Trigger cells (walk-forward, dedup, label fromPR_High ≥30, base 12.1%)

| trigger | fires (13 days) | TP | precision | lift | TP median peak |
|---|---|---|---|---|---|
| combo ≥ 0.5 | 74 | 19 | 26% | 2.1× | +68% |
| **combo ≥ 0.6** | **60** | **17** | **28%** | **2.3×** | +68% |
| combo ≥ 0.7 | 45 | 12 | 27% | 2.2× | — |
| nocoref_neutral_filter ≥ 0.5 | 81 | 16 | 20% | 1.6× | +54% |
| combo ≥ 0.5 AND neutral_filter ≥ 0.5 | 18 | 7 | 39% | 3.2× | +88% (UPC +371, MSS +100, TDTH +90, KUST +88, SHPH +68) |
| kw_mult_nb_log ≥ any level | ~290+ | — | ≈ base | ~1.3× | useless as trigger (median score = 1.0, saturated); usable inverted: score < 0.5 vetoes ~21% of stream at cost of ~4% of winners |

`model_proba` alone dies above level 0.7 (max ≈ 0.79 walk-forward); its 0.8+ cells are structurally empty, not clean.

## 6. FinBERT/nocoref reevaluation on the big (2,682-row) backtest

FinBERT was never trained on either dataset → both windows are honest. Lift = precision ÷ that window's base rate:

| sentiment leg | BIG lift (n fires) | WF lift (n fires) | verdict |
|---|---|---|---|
| **nconf ≥ 0.5 AND positive ≥ 0.8** | **1.8× (191)** | 2.2× (41) | consistent, modest — best stable sentiment signal |
| positive ≥ 0.8 | 1.7× (325) | 1.6× (61) | consistent, modest |
| nocoref_neutral_filter ≥ 0.5 | 1.5× (390) | 1.6× (81) | consistent, weak |
| nocoref_net_score ≥ 0.5 / 0.6 | 2.3× / 2.6× | **1.0× / 0.8×** | FLIPS across windows — window artifact, do not use |
| nocoref_neutral_filter ≥ 0.6 | 2.3× | **0.8×** | FLIPS — do not use |

Conclusion: real sentiment lift is **~1.5–1.8×** — genuine but too weak to trigger alone (≈14% precision at a normal 8% base rate = 6 duds per winner). Any sentiment leg showing 2.5×+ in a single window has so far mean-reverted in the other.

## 7. RECOMMENDED TRIGGER RULES

| tier | rule | expected (walk-forward evidence) | role |
|---|---|---|---|
| **Standalone trigger** | `model_proba_PLUS_kw_mult_nb ≥ 0.6` | ~60 fires/13 days (~4.6/day), 28% precision at ≥30, TP median peak +68% | the only leg with solid out-of-sample evidence |
| **Conviction upgrade** | trigger **AND** (`nocoref_confidence_weighted ≥ 0.5` AND `positive ≥ 0.8`) | ~1.8× precision multiplier (stable across 3,000+ PRs) | size up when passed, size down/skip when failed — NOT a standalone trigger |
| **Veto (optional)** | skip if `kw_mult_nb_log < 0.5` | removes ~21% of stream, costs ~4% of winners | cheap negative filter in front of everything |
| **Do NOT use** | `kw_add_log_ge40` OR-leg; `net_score`/`neutral_filter ≥ 0.6`; raising combo threshold to catch ≥30 movers | — | net-negative adds / window artifacts / top tail selects placement templates |

Practical notes:
- Prices: several fires are sub-$0.50 stocks (GDC $0.02, SBEV $0.15) — spread can eat 5–10%; consider a `LastDailyClose ≥ $0.50` tradeability filter.
- Live dedup: same PR can arrive twice ~200 ms apart (see §4); guard on Symbol+Headline, ignoring the tickers list.
- `fromPR_High(%)` is **peak excursion**, not capturable P&L. Profitability estimates used crude models (+15 if peak ≥15 else −7; +30 if ≥30 else −8) under which combo≥0.6 was clearly positive in both half-windows — but the loss side (max adverse excursion) is not in the data.

## 8. Caveats and next steps

1. All trigger thresholds were read off the same 389-row walk-forward window (13 trading days, one regime, 47 movers). Wilson CIs on the key cells are wide (combo≥0.6: 28% has CI ≈ 18–41%). Expect regression.
2. Honest validation path: run the frozen rules unchanged on the NEXT weeks of PRs (`add_combo_score.py --apply-model ..._comboScore_model.json` + this document's thresholds) and compare.
3. The real profitability question needs an exit simulation: post-PR intraday bars for the fire events, entry at PR+slippage, take-profit/stop grid (bulk historical on the idle paper GW, port 4002).
4. Files: TP/FP matrices `..._dedup_TPFP_fromprhigh_tp30/tp40.tsv`; AUC reports `outputs/walkforward_2026-06-20to07-09_report.md`, `outputs/walkforward_ge30ge40_report.md`.
