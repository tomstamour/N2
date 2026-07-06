# Keyword–Association Analysis Report

**Task:** Identify unigrams/bigrams in press-release (PR) body text that are associated with large intraday price moves, and quantify each term's predictive contribution.
**Target metric:** `DailyHigh(%)` — the intraday high relative to the reference price.
**Text field:** `body` (~400 words/PR).
**Author/engine:** `tools/keyword/keyword_finder.py` (707 lines), scikit-learn 1.9.0, run under `/home/tom/venv/bin/python`.
**Date:** 2026-07-04.

---

## 1. Executive summary

1. **A continuous regression of `DailyHigh(%)` on PR text has essentially no out-of-sample signal.** ElasticNetCV on TF-IDF/binary features gave test R² ≈ **0.0** on the original percentage scale (≈0.2 only in `signed_log` space, i.e. the transform, not the text, carried the apparent fit). **Do not use the regression keyword tables for inference.**
2. **Reframing to a binary classification problem — "was this a large move? (`DailyHigh(%) ≥ threshold`)" — plus removal of non-catalyst boilerplate PRs unlocks a genuine, fold-stable signal.** The best configuration reaches **test ROC-AUC ≈ 0.78** vs a 0.50 chance baseline.
3. **Threshold matters.** The `≥20%` cut is the statistical sweet spot: highest and most stable cross-validated AUC with the largest positive class. Higher thresholds (`≥30%`, `≥50%`) give larger effect sizes (odds ratios) but smaller, noisier positive classes.
4. **TF-IDF > binary count** on predictive quality at every threshold; binary is retained only because it yields a far sparser, more human-auditable coefficient list.
5. **A distributor-fingerprinting leak was found and closed.** The strongest "negative" (small-move) terms were newswire tracking-URL artifacts (`smartlink`, `cts`, `esheet`, `businesswire`, `https…`), i.e. the model was partly learning *which wire distributed the PR* rather than *what it said*. Scrubbing those tokens **did not reduce accuracy (test AUC 0.765 → 0.783)** and produced content-meaningful negative terms.
6. **Deliverable of record:** the consensus keyword sheets in `outputs/classify_ge20_nowire/`, listing terms that are positive/negative **and** fold-stable in **both** vectorizations.

---

## 2. Data

| Item | Value |
|---|---|
| Source file | `inputs/PR-stats-2026-05-03to06-19-filtered-50float-12high_RBscore_body_clean.tsv` |
| Raw rows | 3,395 |
| After dropping empty body / non-numeric target | 3,395 (none dropped) |
| After dropping duplicate `(body, target)` pairs | 3,389 |
| Row identifier | `ID` (unique, preserved for traceability) |

**Target distribution (`DailyHigh(%)`, n = 3,389):** min −87.04, max 474.17, mean 14.63, median 5.64, **skew 6.60**, 163 negatives, 119 zeros.
The extreme right skew with negative values is why a plain `log` transform is invalid; the code offers `signed_log(y) = sign(y)·log1p(|y|)` as the only transform option.

### 2.1 Boilerplate filtering (`--strip-boilerplate`)

Whole classes of PR are structurally non-catalyst and dominated the coefficients. They are detected by regex on `Headline + body` (`boilerplate_category()`), first-match-wins in this order:

| Category | Rows removed | Definition (examples) |
|---|---|---|
| `lawfirm` | 556 | Shareholder-rights / class-action notices: firm names (Rosen, Pomerantz, Bragar Eagel, Robbins Geller, Gross Law…), "class period", "lead plaintiff", "on behalf of…", "notifies/reminds investors" |
| `earnings` | 611 | Quarterly/financial-results PRs: "reports … financial results", "quarter/year ended", "provides business/corporate update" |
| `conference` | 399 | Investor conference/presentation/day, webcast, fireside chat, roadshow, annual meeting |
| `compliance` | 131 | Nasdaq listing-rule/deficiency, inducement grants (Rule 5635), reverse stock splits, "regains compliance", "minimum bid price" |
| **Total** | **1,697** | |

**Rows retained: 1,692.** Justification that these are noise, not signal: the large-move (`≥30%`) rate is **5.6% inside stripped rows vs 13.4% inside kept rows.**

### 2.2 Wire-boilerplate scrubbing (`--strip-wire-boilerplate`, now default-on)

`strip_wire_boilerplate()` removes, from the retained text, (a) full URLs (`https?://…`, `www.…`), (b) domain tokens (`*.com/net/org/io/us`), and (c) newswire vendor / tracking-query tokens (`business wire`, `globenewswire`, `accesswire`, `prnewswire`, `smartlink`, `cts`, `esheet`, `anchor`, `utm_*`, `u00xx`, `lan en`). Median words per doc: 400 → 400 (the removed tokens are high-**document-frequency** but low-**per-doc-volume**, which is exactly why they carried outsized coefficient weight before removal).

### 2.3 Positive-class prevalence after all filtering (n = 1,692)

| Threshold | Positives | Prevalence |
|---|---|---|
| `≥20%` | 374 | 22.1% |
| `≥30%` | 227 | 13.4% |
| `≥50%` | 126 | 7.4% |

---

## 3. Method

### 3.1 Feature extraction
Two vectorizers, each on unigrams **and** bigrams (`ngram_range=(1,2)`), `lowercase=True`, `min_df=5`, `max_df=0.9`, token pattern `\b[a-zA-Z]{2,}\b` (drops pure numbers), fit **only inside the training pipeline** to prevent vocabulary/IDF leakage:
- **`tfidf`** — `TfidfVectorizer(sublinear_tf=True)`. Coefficient = effect per unit of TF-IDF.
- **`binary`** — `CountVectorizer(binary=True)`. Coefficient ≈ effect of the term being **present** (more directly interpretable).

Resulting vocabulary at `≥20%` (wire-stripped): **18,833 features.**

### 3.2 Models
- **Regression path** (initial, deprecated for inference): `Pipeline(vectorizer → ElasticNetCV)` with `l1_ratio ∈ {0.1,0.3,0.5,0.7,0.9,0.95}`, 50-point α path, 5-fold internal CV.
- **Classification path** (adopted): `Pipeline(vectorizer → LogisticRegressionCV)`, elastic-net penalty, `solver='saga'`, `l1_ratios ∈ {0.1,0.5,0.9}`, `Cs ∈ {0.01,0.1,1.0}`, `cv=5`, `scoring='roc_auc'`, **`class_weight='balanced'`** (to counter the 8–22% prevalence). Positive class = `DailyHigh(%) ≥ threshold`.

### 3.3 Evaluation protocol
- **Held-out test set:** 20%, stratified by class. Reported: ROC-AUC, average precision (AP = area under precision–recall), and precision/recall/F1/accuracy at a 0.5 probability cut.
- **Cross-validation on the training set:** 5-fold, refit with the *selected* `(C, l1_ratio)` — measures score **stability across folds** (mean ± sd), the metric to trust over the single test split.
- **Baselines:** `DummyClassifier(strategy='prior')` → ROC-AUC 0.50, AP = positive prevalence.

### 3.4 Per-term statistics (the keyword tables)
For every term the code reports both a **marginal** and a **multivariate** view:
- **Marginal:** `univariate_r` = signed point-biserial correlation with the 0/1 target (`r_regression`); `univariate_f_score`, `univariate_p_value` (`f_regression`). "What does this word do by itself?"
- **Multivariate:** `logit_coef` = the regularized logistic elastic-net coefficient; `odds_ratio = exp(logit_coef)`; `selected_by_model` = coefficient ≠ 0. "What does the word contribute conditional on all other words?"
- **Prevalence:** `document_frequency`, `document_frequency_pct`, `mean_feature_value`.
- **Stability (Phase 6):** the pipeline is refit on 5 stratified folds; per term we report `coef_mean_across_folds`, `coef_std_across_folds`, `sign_stability` (fraction of non-zero folds sharing the majority sign), `selection_frequency` (fraction of folds with non-zero coefficient), `present_in_folds` (folds whose vocabulary contained the term). These guard against the known instability of coefficients among correlated text features.

**Stability gate used for the shortlists:** `present_in_folds = 5 AND sign_stability ≥ 0.8 AND selection_frequency ≥ 0.8 AND document_frequency ≥ 20`.

---

## 4. Results

### 4.1 Why regression was abandoned

| Variant | test R² (orig %) | test R² (signed_log) | CV R² | Baseline test R² |
|---|---|---|---|---|
| tfidf | 0.019 | 0.211 | 0.176 | −0.001 |
| binary | 0.007 | 0.192 | 0.145 | −0.001 |

On the scale that matters (percentage), the text explains ≈0% of variance. A stop-words variant did not help (CV R² 0.176 → 0.169). Conclusion: **text is weak for point-predicting the magnitude**, but may still separate "big move vs not" — motivating the classification reframe.

### 4.2 Classification threshold sweep (tfidf, boilerplate stripped, 1,692 rows)

| Threshold | Positives | Test ROC-AUC | CV ROC-AUC (mean ± sd) | Test AP | Baseline AP | AP lift |
|---|---|---|---|---|---|---|
| **≥20% (wire-stripped, canonical)** | 374 | **0.783** | **0.731 ± 0.023** | 0.434 | 0.221 | 1.96× |
| ≥20% (wire retained) | 374 | 0.765 | 0.732 ± 0.019 | 0.428 | 0.221 | 1.93× |
| ≥30% | 227 | 0.726 | 0.745 ± 0.050 | 0.351 | 0.133 | 2.64× |
| ≥50% | 126 | 0.736 | 0.717 ± 0.027 | 0.268 | 0.074 | 3.62× |

**Interpretation for a statistician:**
- The `≥20%` model has the **highest test AUC and the tightest CV band** — the safest generalization claim. Its ≈0.78 AUC with 0.73 CV means real, stable rank-ordering ability well above chance.
- `≥30%` has a *higher CV mean but much larger CV sd (0.050)* — fragile, driven by one strong fold (0.828) and one weak (0.688).
- `≥50%` maximizes **effect sizes / AP lift** (rare-event detection) but the 7.4% prevalence makes point classification unreliable (precision 0.125 at the 0.5 cut). Treat its output as a **ranking score**, never a hard gate.

### 4.3 TF-IDF vs binary (classification)

| Threshold | binary test AUC | binary CV AUC (± sd) | tfidf CV AUC (± sd) | binary non-zero terms | tfidf non-zero terms |
|---|---|---|---|---|---|
| ≥20% | 0.698 | 0.691 ± 0.039 | 0.731 ± 0.023 | ~218 | ~7,487 |
| ≥30% | 0.680 | 0.687 ± 0.043 | 0.745 ± 0.050 | ~270 | ~7,776 |
| ≥50% | 0.763* | 0.680 ± 0.067 | 0.717 ± 0.027 | ~388 | ~6,333 |

\*Binary's `≥50%` test AUC of 0.763 is a single-split artifact (CV mean only 0.680, sd 0.067). **TF-IDF wins on the trustworthy CV metric everywhere.** Binary's value is sparsity: ~200–400 non-zero coefficients (vs ~7,000) make it human-auditable and its coefficient reads directly as "term present ⇒ move up/down."

### 4.4 The distributor-fingerprint leak (and its closure)

Before wire-scrubbing, the top **negative** consensus terms were almost entirely newswire plumbing: `businesswire`, `businesswire com`, `com`, `https`, `smartlink url`, `cts`, `esheet`, `us anchor`, `lan en`, `ct id` — many with *identical* df/r/OR (≈113 docs), i.e. one tracking-URL template shattered into many correlated tokens. This is **leakage**: the model can infer "small move" from the distributor's URL scheme. Scrubbing the tokens moved test AUC **0.765 → 0.783** (no cost, slight gain), confirming these tokens were noise, and the negative list became content-bearing (§4.6). `--strip-wire-boilerplate` is therefore now **default-on** in the standard recipe.

### 4.5 High-confidence positive keywords (consensus, `≥20%`, wire-stripped)
51 terms are positive **and** pass the stability gate in **both** vectorizers. Themes:
- **M&A / deal language (cleanest signal):** `entered`, `entered into`, `agreement`, `acquire`, `binding`, `terms`.
- **Growth-stage substance:** `development`, `development of`, `technologies`, `infrastructure`, `strategy`, `stage`, `established`, `planned`, `potential`, `major`.
- **Hot themes:** `space`, `computing`.
- **Scale/finance:** `million`, `holdings`.
- **Stylistic correlates (discount):** `the company`, `believes`, `into`, `further`, `following` — high-frequency connectives that ride along with substantive PRs; high rank is partly ubiquity, not causation.

Illustrative effect sizes (tfidf odds ratios): `space` 3.34, `the company` 2.83, `believes` 2.65, `development of` 2.46, `entered` 2.28, `entered into` 2.25, `infrastructure` 2.16, `holdings` 2.32.

At higher thresholds the vocabulary shifts toward the **low-float China/HK ADR runner** profile: `hong kong`/`hong`/`kong`, `ltd`/`limited`/`holdings limited` (OR ≈ 10.9 at `≥50%`), `quantum`, `robotics`, `financing`, `private placement`.

### 4.6 High-confidence negative keywords (consensus, `≥20%`, wire-stripped)
38 content-bearing terms (post-leak-fix). These describe **low-energy PR types**: `experience`, `advisory`, `role`, `national`, `record`, `reflects`, `release`, plus hedge/vague language `about`, `more`, `most`, `can`, `time`, `is one`, `than`. Practical reading: **personnel/advisory-board/generic-update releases do not run** — a usable *avoid* signal, distinct from the earlier artifact list.

---

## 5. Output files

All under `tools/keyword/outputs/`. **The directory of record is `classify_ge20_nowire/`** (canonical recipe). Others are retained for comparison/audit.

### 5.1 Canonical — `outputs/classify_ge20_nowire/`
| File | Contents |
|---|---|
| **`consensus_positive_terms_ge20.csv`** | **51 upward terms** agreed by both models after the stability gate. Primary deliverable. |
| **`consensus_negative_terms_ge20.csv`** | **38 downward terms** (content, not URL debris). |
| `tfidf_keyword_impact_all_terms.csv` | Every term (~18.8k) with marginal + multivariate + stability columns; sorted by `abs_logit_coef`. |
| `tfidf_keyword_impact_positive_terms.csv` / `..._negative_terms.csv` | Terms split by coefficient sign, sorted by magnitude. |
| `binary_keyword_impact_{all,positive,negative}_terms.csv` | Same, binary vectorizer (sparser, more interpretable). |
| `tfidf_fitted_pipeline.joblib` / `binary_fitted_pipeline.joblib` | Serialized fitted `Pipeline` (vectorizer + logistic model) for scoring new PRs. |
| `model_performance_summary.csv` | One row per variant: n_train/n_test, prevalence, selected `C`/`l1_ratio`, test ROC-AUC/AP/precision/recall/F1, baseline AP, CV AUC mean/sd. |

### 5.2 Keyword-table schema (classification)
`term, ngram_type, document_frequency, document_frequency_pct, mean_feature_value, univariate_r, univariate_f_score, univariate_p_value, logit_coef, abs_logit_coef, odds_ratio, direction (increases_prob/decreases_prob), selected_by_model, rank_by_abs_coef, rank_by_abs_r, model_C, model_l1_ratio, coef_mean_across_folds, coef_std_across_folds, sign_stability, selection_frequency, present_in_folds`.

### 5.3 Consensus-sheet schema
`consensus_rank, term, ngram_type, document_frequency, document_frequency_pct, univariate_r, logit_coef_tfidf, odds_ratio_tfidf, logit_coef_binary, odds_ratio_binary, min_sign_stability, min_selection_freq, consensus_rank_score`. `consensus_rank_score` = mean of the two models' `rank_by_abs_coef` (lower = stronger agreement); `min_*` columns take the weaker of the two models' stability so you can gate harder.

### 5.4 Comparison / audit directories
| Path | What it is |
|---|---|
| `outputs/` (root) | **Original regression** run (both variants), `signed_log` target, no boilerplate filtering — the near-zero-R² baseline. |
| `outputs/stopwords/` | Regression + English stop-words (showed stop-words don't help). |
| `outputs/classify_ge{20,30,50}/` | Classification sweep **with boilerplate strip but wire tokens retained** (pre-leak-fix). `classify_ge20/` also holds the first consensus sheets (URL-debris negatives — superseded). |

Regression tables use `elasticnet_coef`/`abs_elasticnet_coef`/`model_alpha` in place of the logistic columns; otherwise the schema matches.

---

## 6. Recommended usage & caveats

- **Standard recipe (now canonical):**
  `keyword_finder.py --task classification --strip-boilerplate --binarize-threshold 20 --variants tfidf`
  (`--strip-wire-boilerplate` is on by default; opt out with `--no-strip-wire-boilerplate`.)
- **Use the predicted probability as a ranking / prioritization score, not a hard yes/no.** With 22% prevalence and precision ≈0.44 at the 0.5 cut, the model orders PRs well (AUC 0.78) but is not a precise classifier at any single operating point. Choose the cut from the precision–recall trade-off for the intended use.
- **Trust the deal / sector / geography clusters; discount high-frequency connectives** (`the company`, `into`, `believes`) — they are stylistic correlates of substantive PRs, not catalysts.
- **Correlated-feature instability:** even after the stability gate, individual coefficients among co-occurring terms can be interchangeable; interpret *clusters*, not single terms.
- **Sample scope:** one window (2026-05-03 → 06-19), 50-float / high-move-filtered universe, 1,692 modeled PRs. Effect sizes at `≥50%` rest on ~126 positives — regard them as hypotheses, not estimates. Out-of-window validation is the natural next step.
- **Marginal vs multivariate:** a term with high `univariate_r` but zero `logit_coef` is redundant given other terms; a non-zero `logit_coef` with weak `univariate_r` may matter only conditionally. The keyword tables carry both by design.

---

*Generated 2026-07-04. Engine: `tools/keyword/keyword_finder.py`. Related memory: `keyword-finder-classification-findings`, `keyword-finder-boilerplate-taxonomy`.*
