# DailyHigh(%) all-column predictor analysis — concatenated_enriched_finBERT_noCoref_AddON.tsv

- Usable rows (target present): **2781**
- Big-mover threshold: **DailyHigh(%) >= 20.0**
- Big movers: **268** (9.6%)  |  rest: 2513
- Numeric: Mann-Whitney rank AUC. Categorical: pooled OOF logistic AUC (5-fold, top-15 levels). Text: same as numeric on engineered headline features.
- Excluded per request: DailyHigh($), Trades/sec, Trigger.

## Combined ranking — every candidate predictor

| rank | kind | feature | n | AUC | direction | |AUC-0.5| |
|---|---|---|---|---|---|---|
| 1 | numeric | positive | 2539 | 0.643 | higher->bigger | 0.143 |
| 2 | numeric | neutral_filter | 2692 | 0.621 | higher->bigger | 0.121 |
| 3 | numeric | recommended | 2692 | 0.620 | higher->bigger | 0.120 |
| 4 | numeric | confidence_weighted | 2692 | 0.619 | higher->bigger | 0.119 |
| 5 | numeric | Float | 2773 | 0.384 | lower->bigger | 0.116 |
| 6 | numeric | net_score | 2692 | 0.614 | higher->bigger | 0.114 |
| 7 | numeric | positional | 2692 | 0.614 | higher->bigger | 0.114 |
| 8 | numeric | sentiment_score | 2781 | 0.609 | higher->bigger | 0.109 |
| 9 | numeric | neutral | 2781 | 0.392 | lower->bigger | 0.108 |
| 10 | numeric | top_k | 2692 | 0.586 | higher->bigger | 0.086 |
| 11 | numeric | negative | 2781 | 0.415 | lower->bigger | 0.085 |
| 12 | numeric | body_duration_ms | 201 | 0.569 | higher->bigger | 0.069 |
| 13 | categorical | label | 2781 | 0.569 | — | 0.069 |
| 14 | categorical | Author | 2781 | 0.557 | — | 0.057 |
| 15 | text | headline_char_count | 2781 | 0.546 | higher->bigger | 0.046 |
| 16 | text | kw__results | 2781 | 0.464 | lower->bigger | 0.036 |
| 17 | categorical | Exchange | 2781 | 0.533 | — | 0.033 |
| 18 | text | kw__announces | 2781 | 0.533 | higher->bigger | 0.033 |
| 19 | text | kw__investor_alert | 2781 | 0.469 | lower->bigger | 0.031 |
| 20 | text | headline_word_count | 2781 | 0.519 | higher->bigger | 0.019 |
| 21 | text | kw__phase | 2781 | 0.518 | higher->bigger | 0.018 |
| 22 | text | kw__class_action | 2781 | 0.483 | lower->bigger | 0.017 |
| 23 | text | kw__revenue | 2781 | 0.514 | higher->bigger | 0.014 |
| 24 | text | kw__offering | 2781 | 0.511 | higher->bigger | 0.011 |
| 25 | text | kw__lawsuit | 2781 | 0.489 | lower->bigger | 0.011 |
| 26 | text | kw__fda | 2781 | 0.511 | higher->bigger | 0.011 |
| 27 | text | kw__acquisition | 2781 | 0.510 | higher->bigger | 0.010 |
| 28 | text | kw__earnings | 2781 | 0.490 | lower->bigger | 0.010 |
| 29 | text | kw__shareholder_alert | 2781 | 0.508 | higher->bigger | 0.008 |
| 30 | text | kw__contract | 2781 | 0.507 | higher->bigger | 0.007 |
| 31 | text | kw__placement | 2781 | 0.505 | higher->bigger | 0.005 |
| 32 | text | kw__dividend | 2781 | 0.496 | lower->bigger | 0.004 |
| 33 | text | kw__award | 2781 | 0.503 | higher->bigger | 0.003 |
| 34 | text | kw__approval | 2781 | 0.502 | higher->bigger | 0.002 |
| 35 | text | kw__announcement | 2781 | 0.499 | lower->bigger | 0.001 |
| 36 | text | kw__merger | 2781 | 0.499 | lower->bigger | 0.001 |
| 37 | text | kw__partnership | 2781 | 0.499 | lower->bigger | 0.001 |
| 38 | text | kw__patent | 2781 | 0.499 | lower->bigger | 0.001 |
| 39 | text | kw__collaboration | 2781 | 0.500 | higher->bigger | 0.000 |

## Numeric — detail (with Spearman vs raw %)

| feature | n | AUC | point-biserial r | Spearman vs raw % |
|---|---|---|---|---|
| positive | 2539 | 0.643 | +0.165 | +0.189 |
| neutral_filter | 2692 | 0.621 | +0.122 | +0.175 |
| recommended | 2692 | 0.620 | +0.118 | +0.174 |
| confidence_weighted | 2692 | 0.619 | +0.122 | +0.173 |
| Float | 2773 | 0.384 | -0.097 | -0.168 |
| net_score | 2692 | 0.614 | +0.128 | +0.167 |
| positional | 2692 | 0.614 | +0.128 | +0.177 |
| sentiment_score | 2781 | 0.609 | +0.117 | +0.168 |
| neutral | 2781 | 0.392 | -0.106 | -0.133 |
| top_k | 2692 | 0.586 | +0.092 | +0.121 |
| negative | 2781 | 0.415 | -0.023 | -0.139 |
| body_duration_ms | 201 | 0.569 | +0.037 | +0.021 |

## Categorical — top levels by big-mover rate

Baseline big-mover rate across the dataset: **9.6%**

### `Author` (CV-AUC 0.557)

| level | n | big-mover rate | lift vs baseline |
|---|---|---|---|
| Globe Newswire | 1131 | 12.6% | +2.9% |
| ACCESSWIRE | 589 | 9.3% | -0.3% |
| PR Newswire | 415 | 7.5% | -2.2% |
| Newsfile Corp | 191 | 7.3% | -2.3% |
| Business Wire | 455 | 5.7% | -3.9% |

Bottom levels (for contrast):

| level | n | big-mover rate | lift vs baseline |
|---|---|---|---|
| PR Newswire | 415 | 7.5% | -2.2% |
| Newsfile Corp | 191 | 7.3% | -2.3% |
| Business Wire | 455 | 5.7% | -3.9% |

### `Exchange` (CV-AUC 0.533)

| level | n | big-mover rate | lift vs baseline |
|---|---|---|---|
| NYSE AMERICAN | 32 | 28.1% | +18.5% |
| NASDAQ | 1997 | 10.9% | +1.3% |
| NYSE American | 175 | 9.1% | -0.5% |
| NYSE | 568 | 4.2% | -5.4% |

Bottom levels (for contrast):

| level | n | big-mover rate | lift vs baseline |
|---|---|---|---|
| NASDAQ | 1997 | 10.9% | +1.3% |
| NYSE American | 175 | 9.1% | -0.5% |
| NYSE | 568 | 4.2% | -5.4% |

### `label` (CV-AUC 0.569)

| level | n | big-mover rate | lift vs baseline |
|---|---|---|---|
| positive | 1320 | 12.7% | +3.0% |
| <other> | 222 | 9.9% | +0.3% |
| negative | 275 | 8.0% | -1.6% |
| neutral | 944 | 6.0% | -3.6% |

Bottom levels (for contrast):

| level | n | big-mover rate | lift vs baseline |
|---|---|---|---|
| <other> | 222 | 9.9% | +0.3% |
| negative | 275 | 8.0% | -1.6% |
| neutral | 944 | 6.0% | -3.6% |

## Conclusion

- **Best single predictor:** `positive` (numeric, AUC 0.643, |AUC-0.5| = 0.143).
- See per-section tables for direction and per-level breakdowns.

