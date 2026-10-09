# XGBoost_inference_script.py — engineering guide

Scores one PR row against the trained regressor and/or the three threshold
classifiers. This document specifies exact behavior, data contracts, and
failure modes — read it before modifying the script or building on top of it.

## 1. What problem this solves

Training (`train_xgb_fromPRhigh.py`, `train_xgb_fromPRhigh_clf.py`) fits
preprocessing parameters on the training fold and applies them in the same
process, in memory, via `build_xy()`. Those parameters are serialized into each
model's `*_prep.json`, but nothing previously *read that JSON back* and applied
it to a new row. `XGBoost_inference_script.py` is that missing "load parameters,
apply transform" counterpart — the only supported way to score a row that
was not part of the original `_PCA.tsv` build.

## 2. Data flow

```
base TSV row (Symbol, ArrivalTime, Headline, Body, ~200 structured cols)
        │
        ├─ Headline, Body ──► finbert_embed_infer.embed_pr()
        │                     ONNX FinBERT, mean-pooled last hidden layer
        │                     → {"hd": float32[768], "bd": float32[768]}
        │
        │                     ▼
        │                  finbert_pca_transform.transform_pr()
        │                  (v - mean_) @ components_.T   [per stream, from finbert_pca.pkl]
        │                     → {"hd_pca": float64[40], "bd_pca": float64[40]}
        │
        └─ structured cols ──► build_row_features()  [per requested model's own prep.json]
                                1. sentinel substitution (44444.0 → stored constant + __nodata flag)
                                2. feature selection (prep["features_used"], 49 names)
                                3. numeric cast (float64) or categorical cast (prep["categories"])
                                4. winsorisation (clip to stored [lo, hi], numeric cols only)
                                     ▼
                                1-row pandas DataFrame, column order == features_used
                                     ▼
                                xgboost.DMatrix(df, enable_categorical=True)
                                     ▼
                                Booster.predict()  →  raw model output
                                     ▼
                     regressor: sinh(raw)  (or expm1(raw)+shift for log1p)
                     classifier: raw is already sigmoid(logit) = probability
```

Each `--kind` loads its **own** `_prep.json` independently — there is no shared
global preprocessing state. The regressor and the three classifiers were fit
on the same 49-feature allowlist and the same sentinel/winsor parameters (they
share the same `build_xy()` call ancestry), but the script does not assume
that; it re-reads each model's own file every time.

## 3. Column-processing order — read this before editing `build_row_features`

The three preprocessing steps are applied in a specific order, and it matters:

1. **Sentinel substitution runs on `sentinel_high`/`sentinel_zero` columns
   unconditionally**, whether or not those columns survived into
   `features_used`. This matches training: `_fit_sentinels` runs on the full
   `X` before the 49-feature slice is taken. In the current shipped models the
   three ITI/trade-size columns and their `__nodata` flags did **not** survive
   SHAP pruning (`grep nodata xgb_cons49_se_prep.json` → empty), so this step
   is presently a no-op for all five models, but the code must not assume that
   stays true after a re-prune.
2. **Feature selection + type cast** iterates `features_used` and fills any
   column not already set by step 1. Numeric columns are cast with
   `float(raw_value)`; a value that fails to parse becomes `np.nan` (matches
   `_coerce`'s "not numeric-coercible" rule at the column level, but note:
   *this script decides numeric vs. categorical per **value**, training decided
   it per **column** based on a `>=50% of the column parses` threshold.* For any
   column that is genuinely all-numeric or all-categorical in production data
   this distinction is invisible; it would only diverge on a column with mixed
   content, which none of the 49 selected features are.
3. **Winsorisation** is applied last, only to columns that are both in
   `features_used` and in `prep["winsor"]`, and only if not in `cat_cols`.
   Clipping after casting, not before, matches `_fit_winsor`'s order in
   training (`_fit_sentinels` then `_fit_winsor` inside `build_xy`).

PCA columns (`hd_pca_*`, `bd_pca_*`) bypass all three steps — they come
pre-computed from `transform_pr` and are inserted directly.

## 4. `prep.json` contract this script depends on

| key | type | used for |
|---|---|---|
| `features_used` | `list[str]`, len 49 | column set + order fed to the model |
| `cat_cols` | `list[str]` | which `features_used` entries are categorical |
| `categories` | `dict[str, list[str]]` | fixed category vocabulary per cat column — **an unseen category becomes NaN**, it is not added |
| `sentinel_high` | `dict[str, float]` | column → replacement value when raw == 44444.0 |
| `sentinel_zero` | `list[str]` | columns replaced with 0.0 when raw == 44444.0 |
| `winsor` | `dict[str, [lo, hi]]` | clip bounds, train-fold 0.5/99.5 percentiles |
| `target` | `{"transform": "asinh"\|"log1p"\|"none", "shift": float?}` | regressor output inversion; absent/unused for classifiers |
| `threshold` | `float` | classifier only — the `>=` cutoff this model was trained against |

If you regenerate a model with a different `--features` list, hyperparameters,
or preprocessing knobs, this script picks up the new `_prep.json` automatically
— it has no hardcoded column lists or constants of its own. The only hardcoded
thing is the file-path map (`MODEL_MAP`), which pins specific `.ubj`/`.json`
filenames per `--kind`. Update `MODEL_MAP` when you retrain and want the new
artifact set to become the default.

## 5. Sentinel semantics (why 44444.0 specifically)

`44444.0` is a fill value written upstream when IBKR had no bars to compute
inter-trade-interval or trade size for a symbol/session. It is **not** a
missing-value code the model should treat as "unknown" — during training it
was deliberately mapped to an out-of-range *high* value for
`RTH_avgITI_sec`/`ETH_avgITI_sec` (order-preserving: "illiquid" reads as
"more illiquid than anything observed"), and to `0.0` for `RTH_tradeSize`
("no bars" implies no traded size). See `train_xgb_fromPRhigh.py` module
docstring and `_fit_sentinels`. Do not replace this with `NaN`-based handling
without re-deriving the constants from a fresh `build_xy` run — the constants
are frozen at fit time (`2 × train-fold max`), not recomputed here.

## 6. CLI surface

```
XGBoost_inference_script.py
  --input-tsv PATH        default: newest 08_*_rmCol_NaN_rmCol_rmCol.tsv in this dir
  --row-idx INT            0-based data row (header excluded)
  --symbol STR             \
  --arrival-time STR        }  alternative to --row-idx; must match exactly 1 row
  --kind {reg_se,reg_q80,clf_ge20,clf_ge35,clf_ge50,all}   default: all
```

`all` expands to `reg_se, clf_ge20, clf_ge35, clf_ge50` (`ALL_KINDS` constant)
— `reg_q80` is available by name but excluded from `all` because it targets
the 80th percentile, not a point estimate; mixing it into a default summary
would be misleading without a caller aware of that distinction.

`--symbol`/`--arrival-time` match is **exact string equality** against the raw
TSV field, post `csv.QUOTE_NONE` parsing — no datetime normalization. If your
caller has a `datetime` object, format it exactly as it appears in the TSV's
`ArrivalTime` column (see a sample row) before passing it in.

## 7. What this script does NOT do

- **It does not compute structured features.** `atr_pct_14`, `MktCap`,
  `float_turnover_max_20d`, sentiment scores, etc. must already exist in the
  input row. This script has no access to price history, short-interest feeds,
  or the encoder models that produce `_ss`/`_lab`/`_ms` columns. Scoring a PR
  that only exists as raw headline+body text requires running the full
  upstream feature pipeline first — this script is the last mile, not the
  whole pipeline.
- **It does not calibrate classifier probabilities.** Each classifier was
  fit with `scale_pos_weight = neg/pos` to correct class imbalance; this
  shifts the decision boundary but leaves the output **not** a calibrated
  P(event). Use it for ranking (e.g. "score all today's PRs, take the top
  decile"), not as a literal probability in a downstream EV calculation
  without separate calibration (Platt scaling / isotonic regression — not
  implemented anywhere in this codebase yet).
- **No batch mode.** One row per invocation. For scoring many rows, prefer
  calling `build_row_features` + `Booster.predict` directly in a loop/vectorized
  form rather than shelling out to this CLI repeatedly (each invocation reloads
  the ONNX FinBERT model and all five `.ubj` models from disk).
- **No output file.** Results print to stdout only, by design (see the
  planning decision in this session's history) — pipe/redirect if you need
  persistence.

## 8. Verified correctness

Checked against `train_xgb_fromPRhigh.build_xy()`'s in-memory computation on
5 validation-fold rows × 4 models (`reg_se`, `clf_ge20/35/50`) = 20 cases:

- **Feature-vector parity**: every one of the 49 columns this script computes
  from the raw row matches the corresponding column in `build_xy`'s output
  DataFrame to within ~1e-6 (float32 write/read rounding in the TSV, not a
  logic difference).
- **Prediction parity**: feeding this script's row into `Booster.predict` and
  feeding the `build_xy`-derived ground-truth row into the same booster
  produced **bit-identical outputs (0.00e+00 diff)** on all 20 cases, after
  applying the same inverse-target transform.

Re-run this check after any change to `build_row_features`,
`train_xgb_fromPRhigh.build_xy`, or any `_fit_*` helper — the verification
script is not checked in (it was a scratchpad script); reconstruct it from
this spec if needed: load `build_xy` on the full `_PCA.tsv`, pick several
`val_mask` row indices, compare `build_row_features(...)` against
`X.loc[idx, features_used]`.

## 9. Known footgun

A file literally named `profile.py` sitting in a directory that ends up on
`sys.path` (e.g. a scratchpad next to this script) will shadow Python's
stdlib `profile` module. `transformers`/`optimum`'s import chain does
`import profile` internally; if it picks up a stray same-named script instead
of the stdlib module, the failure surfaces as an unrelated-looking
`ImportError: cannot import name 'GenerationMixin' from 'transformers.generation'`
deep in `optimum.onnxruntime.modeling`. If you see that exact error, check
`sys.path` and any working directory for a `profile.py` before suspecting the
model files.

## 10. Example

```bash
/home/tom/venv/bin/python XGBoost_inference_script.py \
  --symbol ABCD --arrival-time "2026-08-01 09:31:00" --kind all
```
```
row_idx=4200  Symbol=OXBR  ArrivalTime=16:04:00.403  actual fromPR_High(%)=34.62
  [reg_se] predicted fromPR_High(%) = 11.71
  [clf_ge20] P(fromPR_High(%) >= 20) = 0.730
  [clf_ge35] P(fromPR_High(%) >= 35) = 0.588
  [clf_ge50] P(fromPR_High(%) >= 50) = 0.352
```
Note the regressor (point estimate 11.71%) and the classifiers (which say the
event is *likely*, 73%/59%/35% probability of clearing 20/35/50%) disagree in
magnitude but not in direction — this is expected: the regressor is pulled
toward the conditional mean under a right-skewed target, while the classifiers
directly estimate tail-event probability. Treat them as complementary views,
not as one calibrated system.
