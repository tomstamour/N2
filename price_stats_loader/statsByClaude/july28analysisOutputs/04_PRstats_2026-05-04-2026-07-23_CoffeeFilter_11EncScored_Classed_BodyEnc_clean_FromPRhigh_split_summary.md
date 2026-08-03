# Train / validation split — 04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_Classed_BodyEnc_clean_FromPRhigh

Temporal split on `ArrivalDate` at cutoff **2026-06-20** (train `< cutoff`, validation `>= cutoff`).

- input rows: 3533
- dropped, no `fromPR_High(%)` label: 99
- dropped, duplicate on Symbol+Headline (keep-first): 10
- train: 2647  |  validation: 777  (77/23)

| split | n | date span | ≥15% pos (prevalence) | ≥20% pos (prevalence) | ≥30% pos (prevalence) |
|---|---|---|---|---|---|
| train | 2647 | 2026-05-04 → 2026-06-18 | 605 (22.9%) | 450 (17.0%) | 279 (10.5%) |
| validation | 777 | 2026-06-22 → 2026-07-23 | 263 (33.8%) | 200 (25.7%) | 140 (18.0%) |

## Base-rate drift

The positive rate in this corpus rises steeply over time, so a model calibrated on the training block will under-predict on validation, and precision/lift must be read against the **period-specific** prevalence above — not a pooled or pinned value.

| half-month | n | ≥15% | ≥20% | ≥30% |
|---|---|---|---|---|
| 2026-05a | 821 | 17.7% | 11.7% | 6.3% |
| 2026-05b | 651 | 22.0% | 16.1% | 9.5% |
| 2026-06a | 983 | 25.6% | 20.9% | 13.8% |
| 2026-06b | 370 | 34.3% | 24.1% | 16.8% |
| 2026-07a | 344 | 35.5% | 27.3% | 18.3% |
| 2026-07b | 255 | 31.0% | 23.9% | 17.3% |
