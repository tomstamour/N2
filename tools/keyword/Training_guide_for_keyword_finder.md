# Training guide — retraining the PR keyword model with keyword_finder.py

Audience: a future session (or developer) retraining the fromPR keyword model on a **bigger,
newer training corpus**. This distills the good practices and hard-won lessons from the
May–July 2026 training/backtesting campaign that produced the current canonical model
(`model_trained_on_PR-stats-2026-05-03to06-19/SELECTED-MODEL_classify_fromPRhigh_ge15_excStr2/`)
and its live scorer (`keyword_scorer-2.py`).

Everything below was learned the expensive way at least once. Read §2 (data prep) and
§5 (evaluation traps) even if you skim the rest.

---

## 1. The canonical recipe (starting point for any retrain)

Run from `tools/keyword/`, always with the venv interpreter:

```bash
/home/tom/venv/bin/python keyword_finder.py \
  --input  <NEW_CORPUS.tsv> \
  --outdir model_trained_on_<CORPUS-STAMP>/classify_fromPRhigh_ge15_excStr2 \
  --task classification \
  --text-col body \
  --target-col "fromPR_High(%)" \
  --binarize-threshold 15 \
  --variants tfidf binary
```

Notes on each choice (details in later sections):

- `--task classification` — regression on the raw % target is near-useless (~0 real-scale R²).
  Binarizing "big move yes/no" is what unlocked out-of-sample signal. Don't revisit regression.
- `--text-col body` — the training corpus uses lowercase `body`; O5 live files use `Body`.
  keyword_finder does NOT match case-insensitively; passing the wrong case cost a debugging
  round on 2026-07-13.
- `--target-col "fromPR_High(%)"` — the honest tradeable target (see §3). Not `DailyHigh(%)`.
- NO `--strip-boilerplate` — deliberate user decision for the canonical model (see §4).
  `--strip-wire-boilerplate` stays default-ON (leave it).
- `--outdir` is the **leaf** directory: artifacts (`tfidf_fitted_pipeline.joblib`,
  `*_keyword_impact_*.csv`, `model_performance_summary.csv`) land directly in it.
  Always dataset-stamp the parent (`model_trained_on_<corpus>/`) — never write into or
  overwrite a frozen model dir. `.gitignore` already covers `model_trained_on_*/` (and the
  older `outputs_*/`). The `SELECTED-MODEL_` prefix on a subdir marks the promoted live
  model — never give it to a fresh training run; it's applied at promotion time (§6).
- Train the headline twin too (same command, `--text-col Headline`, outdir `..._headline`) —
  it has consistently run 0.03–0.05 CV-AUC below body, but it's cheap and useful as a
  sanity cross-check.

---

## 2. Data preparation — where most of the real work is

The model is only as honest as the corpus. In order:

### 2.1 Deduplicate FIRST (the nGNX/nGNE lesson)

Post-2026-06-20 RTPR data contained **~8% double-delivery echoes**: the same GlobeNewswire
PR delivered twice with two different IDs ~200 ms apart (nGNX…/nGNE… pairs). Their `Tickers`
lists differ slightly, so headline+tickers dedup missed them; **Symbol+Headline keep-first**
caught them (429 → 396 rows on the walk-forward file).

- Duplicates in a training set double-weight those PRs AND, worse, if one copy lands in
  train and the other in test, the split leaks and every metric is inflated.
- Orchestrator5 now dedups at source, but **verify anyway** on any new corpus:
  `df.duplicated(subset=['Symbol','Headline']).sum()` should be ~0. Also check
  near-duplicates ~seconds apart from the same symbol.
- The pre-06-20 training corpus was clean; assume nothing about newer data.

### 2.2 Row exclusion: excluded-strings filter (excStr2), not --strip-boilerplate

The canonical corpus was filtered by the user's own list
(`orchestrator5/excluded_strings-2.txt`, applied via
`coffee_filter.py --MaxFloat 50 --string-to-exclude …`, formerly
`filter_float_dailyhigh.py`) which removes untradable
PRs (mostly lawfirm notices). This keeps **row-exclusion control with the user**, versus
the script's `--strip-boilerplate` which is coarser and has known false positives (§4).

The user additionally hand-purged headline-phrase groups in two passes (business update,
dividend, stockholders, leadership, annual meeting…). Before purging a phrase group, check
what it costs: "announces closing" and "annual report" were KEPT because 5 of 9 lost
>20% movers were offering-closings. **Count the positives you'd delete before deleting.**

### 2.3 Target column: fromPR_High(%), and why not DailyHigh(%)

- `DailyHigh(%)` = (whole-session high − prior close) / prior close. It **bakes in the
  pre-PR run-up** → the model partly fingerprints already-moving stocks (mild leakage).
  Its higher AUC (~0.78 test) is flattering, not real.
- `fromPR_High(%)` = (post-PR high − price at PR instant) / price at PR instant
  (1-min bars, window Created→8pm ET; added by `fromPR_HighOfDay_addOn.py`). Lower AUC
  (~0.65–0.73 CV) is the **true difficulty of the tradeable question**. Use this.
- Guardrail: do not switch targets because one shows a better AUC. The easier target is
  easier because it leaks.

### 2.4 Body text facts

- Historical corpus bodies are already capped at **400 words** upstream (median = max = 400).
  If the new corpus pipeline changes this, live scoring consistency matters:
  `keyword_scorer-2.py --max-words` exists for that (percentiles only line up if
  `scorer_reference.json` was built with the same cap).
- Wire-vendor tokens/tracking URLs are stripped by default (`strip_wire_boilerplate()`):
  removing them had NO accuracy cost (test AUC actually rose 0.765→0.783 on the old target)
  and killed a real leakage vector (the model was fingerprinting the distributor). Never
  turn this off.

---

## 3. Choosing the binarize threshold

Balance-match the threshold to the corpus — the sweet spot is a **15–25% positive rate**:

| Label | Positives | Verdict (measured) |
|---|---|---|
| fromPR ≥12 | ~24.7% | first canonical; superseded by ≥15 |
| **fromPR ≥15** | **18.5% (496/2682)** | **canonical** — CV 0.725 ±0.044 |
| fromPR ≥30 | thinner | walk-forward: **identical signal to ge15** (AUC≥30 0.686 vs 0.685, score Spearman 0.771) |
| fromPR ≥40 | 158 pos | walk-forward: **worse** (0.638) — too few positives |

Lesson (2026-07-13 ge30/ge40 experiment): training on a higher threshold does NOT teach the
model to find bigger movers — ge30 learned the same ranking as ge15, ge40 just starved.
The keyword ceiling for predicting ≥30% moves on this corpus is **AUC ≈ 0.69 regardless of
label**. With a bigger corpus you can re-test this (more positives may let ge30 finally
differentiate), but expect the ge15-style balanced label to remain the workhorse. Re-derive
the threshold from the new corpus's positive-rate table, don't copy 15 blindly.

---

## 4. The --strip-boilerplate decision

`--strip-boilerplate` drops lawfirm/compliance/earnings/conference rows via regex
(`boilerplate_category()`, first-match-wins on Headline+body). Current canonical choice:
**do NOT use it for training** — measured facts:

- Keeping the boilerplate rows **HELPED** on the fromPR target: CV 0.64 → 0.72. Part of the
  model's job is re-learning boilerplate-vs-catalyst separation itself ("announces",
  "board", "ended march" come out strongly negative) — that's fine for a ranking score.
- The `compliance` category has confirmed **false positives**: any catalyst PR whose body
  mentions "nasdaq listing rule" once (the Rule 5635 footer riding on M&A/financings) gets
  vetoed. Confirmed casualty: nNFC52BqQD, a $119M M&A that ran **+74%** fromPR. Class-level,
  compliance-bucket rows move MORE than kept rows on the fromPR target (32% vs 24.7% ≥12) —
  the old "boilerplate is low-signal" justification came from the DailyHigh target and does
  not generalize.
- If you ever revisit stripping: detect on Headline only, or remove the listing-rule
  patterns from COMPLIANCE_RE.
- Downstream, `boilerplate_category` remains useful as a **live veto flag** (the scorer
  emits it) — just not as a training-row filter.

Consequence for evaluation: since the model is trained no-strip, every later `--evaluate`
run on the scorer must pass `--no-strip-boilerplate` (see §5.3).

---

## 5. Evaluation — the traps that produced fake numbers

### 5.1 Trust CV, not the single test split

The 20% test split at these corpus sizes (~540 rows) is noisy at **±0.05 AUC**. Measured:
same-day refits moved test AUC 0.720 → 0.655 while CV stayed 0.722 → 0.725. Report
**CV mean ± std** as the headline number; treat the test split as a smoke check only.
A bigger corpus shrinks this, but re-check the split size before trusting test numbers.

### 5.2 In-sample scores are wildly inflated — walk-forward is the only honest arbiter

Every score computed on the training corpus is in-sample. Measured decay on truly
out-of-time data (06-20→07-09, frozen models):

- model_proba AUC 0.907 (in-sample) → **0.676** (walk-forward)
- combo 0.911 → 0.664; model_proba_hl 0.864 → 0.613
- Meanwhile the honest non-keyword baseline (Headline-RBscore 0.640) **held** at 0.643.

Rules that follow:
- Never quote in-sample numbers as expected performance.
- Any scored version of the training corpus (`*_scoredFromPRmodel*.tsv`) is IN-SAMPLE;
  use out-of-fold scores before any downstream backtest on it.
- **Hold out the most recent weeks of the new corpus entirely** and walk-forward the frozen
  new model on them before believing anything. If you train on "everything up to today",
  you have no honest test until new data accumulates — plan for that gap.
- Trigger thresholds calibrated in-sample collapse out-of-sample (combo@0.9: 27/40 hits
  in-sample → 1/12 walk-forward). Re-derive live thresholds ONLY from walk-forward tables.

### 5.3 The --evaluate split-leak trap (the fake 0.92)

`keyword_scorer-2.py --evaluate` reconstructs the train/test split to benchmark scoring
methods. The split must **mirror training exactly**: for the no-strip ge15 model you MUST
pass `--no-strip-boilerplate`, otherwise the evaluator strips rows, the seed-42 stratified
split shifts, training rows leak into "test", and the AUC inflates to ~0.92. If a number
looks too good, the first suspect is a split mismatch, not a breakthrough.

### 5.4 Method ranking is UNSTABLE across corpus versions

Across refits, the league order of scoring methods (model_proba, kw_mult_nb_log,
kw_add_lin, …) reshuffled repeatedly — kw_mult_nb_log went worst→2nd between two same-day
fits; single-split differences of 0.01–0.03 are noise. On the ge12 fromPR model,
model_proba OVERFIT (0.950 train / 0.777 test) while kw_mult_nb_log was rock-stable.

Do NOT crown a method from one split. model_proba, kw_mult_nb_log and kw_add_lin are all
defensible; let the walk-forward on held-out weeks decide, and expect the frozen combo
(model_proba + kw_mult_nb) to need refitting per model generation (§6.4).

### 5.5 tfidf > binary, consistently

Every fit on every target: tfidf CV beat binary by 0.03–0.06. Keep training both variants
(binary feeds the NB-marginal kw_* table methods) but the deployed pipeline is tfidf.

---

## 6. After training — artifact and deployment checklist

Training is the easy half. The scorer/live chain has exact-reproducibility requirements:

1. **Freeze the dir.** Once anything downstream (scored TSVs, combo fit, walk-forward
   report) references the artifacts, never re-run keyword_finder into the same outdir —
   new dir, new stamp.

2. **Build `scorer_reference.json`** (percentile mapping for the `*_pctl` columns) by
   running the scorer in evaluate mode against the training corpus with ALL paths pointed
   at the new dir, mirroring the training config:
   ```bash
   /home/tom/venv/bin/python keyword_scorer-2.py --evaluate \
     --keywords  <newdir>/binary_keyword_impact_all_terms.csv \
     --pipeline  <newdir>/tfidf_fitted_pipeline.joblib \
     --reference <newdir>/scorer_reference.json \
     --input <NEW_CORPUS.tsv> --text-col body \
     --target-col "fromPR_High(%)" --threshold 15 \
     --no-strip-boilerplate
   ```
   (Only `--evaluate` writes the reference file.)

3. **Pin the prevalence.** The NB-marginal methods (kw_mult_nb*) need the train positive
   rate. Reading it back from `model_performance_summary.csv` differs in the 4th decimal
   from the true value and produced a ~5e-4 kw_mult_nb drift that broke byte-exact
   backtest reproduction. Record the EXACT rate at training time (e.g. 496/2682 = 0.185)
   and hard-pin it wherever the new model becomes default (keyword_scorer-2 pins
   `DEFAULT_PREVALENCE = 0.185` for the current one).

4. **Refit the combo.** `model_proba_PLUS_kw_mult_nb` is a frozen 2-feature logistic
   (`statsByClaude/add_combo_score.py`, out-of-fold fit, `--apply-model <json>` for
   scoring). Its mu/sd/weights are **specific to one model generation** — ge15 weights on
   ge30 scores are silently wrong, which is why keyword_scorer-2 has an interlock that
   disables combo when artifacts are overridden without an explicit `--combo-model`.
   Retrain ⇒ refit the combo JSON on the new model's out-of-fold scores.

5. **Walk-forward before switching live.** Score the held-out recent weeks with the frozen
   new model + new combo, rebuild the trigger tables (fires/day, precision at ≥30, TP
   median peak), and compare against the incumbent's walk-forward numbers — the incumbent's
   bar is combo≥0.6: ~4.6 fires/day, 28% precision on fromPR_High≥30 vs 12.1% base. If the
   new model doesn't beat that honestly, keep the old one; bigger training set ≠ better.

6. **Promote deliberately.** Rename the winning subdir with the `SELECTED-MODEL_` prefix
   (and remove the prefix from the dethroned one). Switching live scoring = editing
   `keyword_scorer-2.py`'s `CANON_DIR`, `DEFAULT_PREVALENCE`, `DEFAULT_COMBO_MODEL` (and
   the trigger constant in Orchestrator5 if the walk-forward moved it). Then re-verify byte-exactness the way the
   -2 merge was verified: score ~20 rows of the scored reference TSV via `score_text()` and
   require max |diff| ≤ 1e-5 vs stored columns. Old model dirs stay on disk untouched —
   they are the reproduction record for every past backtest.

---

## 7. Current canonical facts (for comparison baselines)

- Model: `model_trained_on_PR-stats-2026-05-03to06-19/SELECTED-MODEL_classify_fromPRhigh_ge15_excStr2/`
  (+ `..._headline/` twin). Corpus: 2,719 rows / 2,682 docs, 496 pos (prevalence 0.185
  pinned), excStr2-filtered, 2 headline purges, NO strip-boilerplate, wire-strip on.
- Body tfidf: CV 0.725 ±0.044, test 0.655 (test split noisy — trust CV).
- Walk-forward (06-20→07-09, 396 deduped rows): model_proba 0.676, combo 0.664,
  Headline-RBscore 0.643. Live trigger: combo ≥ 0.6.
- Live scorer: `keyword_scorer-2.py` (ge15+combo defaults). Old `keyword_scorer.py`
  still defaults to the obsolete ge20 DailyHigh model — never use it for live work.
- Deeper analysis: `price_stats_loader/statsByClaude/14-july-2026_keywordsScores_and_FinBERTscores_BestThresholds.md`,
  `outputs/walkforward_2026-06-20to07-09_report.md`, `outputs/walkforward_ge30ge40_report.md`.
