# XGBoost_inference_script.py — model-2 guide

Scores one PR row against model-2's trained regressor and/or the three
threshold classifiers. This is the model-2 counterpart of
`XGBoost_inference_script_user_guide.md` (model-1) — same script, same
mechanism, different `--model-dir`. Read this before scoring anything
against model-2, especially the two gotchas in §9: the default model-dir
is still `model-1`, and the PCA transform is **not** re-derived per
model-dir.

## 1. What problem this solves, and why model-2 exists

`train_xgb_fromPRhigh.py`/`train_xgb_fromPRhigh_clf.py` fit preprocessing
parameters on the training fold and apply them in-process via `build_xy()`.
Those parameters are serialized into each model's `*_prep.json`, but nothing
reads that JSON back and applies it to a *new* row outside the training
script. `XGBoost_inference_script.py` is that "load parameters, apply
transform" counterpart.

model-2 exists because model-1 had a real, since-fixed divergence:
`Prosus_neg_bd`/`Prosus_ss_bd` were scored on the **raw, uncapped Body**
text (`FinBERT_TSV_scorer.py` has no `body_prep` import), while every other
body-derived feature — including the FinBERT embeddings that feed
`bd_pca_*` — used the 200-word `prep_body` cap. model-2 rescored those two
columns (and, separately, `FineClass`/`BroadClass` were also recategorized)
and retrained on the corrected data. The mechanism below is otherwise
identical to model-1's.

## 2. Data flow

```
base TSV row (Symbol, ArrivalTime, Headline, Body, ~200 structured cols)
        │
        ├─ Headline, Body ──► finbert_embed_infer.embed_pr()
        │                     ONNX FinBERT, mean-pooled last hidden layer,
        │                     body capped at 200 words (finbert_embed_core.BODY_CAP_WORDS)
        │                     → {"hd": float32[768], "bd": float32[768]}
        │
        │                     ▼
        │                  finbert_pca_transform.transform_pr()
        │                  (v - mean_) @ components_.T
        │                  ⚠ ALWAYS loads model-1/finbert_pca.pkl, regardless of
        │                    --model-dir -- see §9. Mathematically correct for
        │                    model-2/model-2FT too (embeddings are unchanged;
        │                    only Prosus_*_bd and FineClass/BroadClass moved).
        │                     → {"hd_pca": float64[40], "bd_pca": float64[40]}
        │
        └─ structured cols ──► build_row_features()  [per requested model's own prep.json]
                                1. sentinel substitution (44444.0 → stored constant + __nodata flag)
                                2. feature selection (prep["features_used"], 25 names)
                                3. numeric cast (float64) or categorical cast (prep["categories"])
                                4. winsorisation (clip to stored [lo, hi], numeric cols only)
                                     ▼
                                1-row pandas DataFrame, column order == features_used
                                     ▼
                                xgboost.DMatrix(df, enable_categorical=True)
                                     ▼
                                Booster.predict(dm, iteration_range=(0, best_iteration+1))
                                     ▼
                     regressor: sinh(raw)  (or expm1(raw)+shift for log1p)
                     classifier: raw is already sigmoid(logit) = probability
```

Each `--kind` loads its **own** `_prep.json` independently. model-2's 5
models (`reg_se`, `reg_q80`, `clf_ge20/35/50`) share the same 25-feature
allowlist and the same sentinel/winsor parameters — verified by reading
`model-2/xgb_cons_se_prep.json` this session — but the script re-reads each
model's own file every time; it does not assume this.

## 3. Variables to feed

The base TSV row must contain: `Headline`, `Body`, and these 25 structured
columns (`model-2/xgb_fromPRhigh_features_conservative.txt`, verbatim):

```
atr_pct_14, MktCap, DeBERTa_ss_hd.1, FineClass, TimeToOpen, Prosus_ss_hd.1,
FLANG_ss_hd.1, Float, TradeHero_ss_hd.1, msc_basis_ratio, bd_pca_007,
float_turnover_max_20d, bd_pca_013, TradeHero_pos_bd.1,
dist_swing_high_20d_pct, bd_pca_026, FinancialB_ss_bd.1, SI_ChangePct,
range_pct_1d, bd_pca_003, bd_pca_000, bd_pca_021, amihud_63d, hd_pca_004,
bd_pca_005
```

`hd_pca_*`/`bd_pca_*` are **not** raw input columns — they're derived from
`Headline`/`Body` by the embed+PCA step above, so the base TSV does not need
them precomputed (the script re-derives all 80 available components and
slices the 4 it needs; feeding a base TSV that already has stale `bd_pca_*`
columns is harmless, they're ignored). Every other name in the list above
must already exist in the row — this script has no access to price history,
short-interest feeds, or the sentiment encoders that produce these values.
That upstream computation is a separate pipeline (see
`price_stats_loader/training-set-construction-steps.md`, "Variables needed
for the models").

`FineClass` is categorical (`prep["cat_cols"]`); the fixed vocabulary is in
`prep["categories"]["FineClass"]` — an unseen category becomes `NaN`, not a
new category.

## 4. `prep.json` contract this script depends on

| key | type | used for |
|---|---|---|
| `features_used` | `list[str]`, len 25 | column set + order fed to the model |
| `cat_cols` | `list[str]` | which `features_used` entries are categorical |
| `categories` | `dict[str, list[str]]` | fixed category vocabulary per cat column — unseen category becomes NaN |
| `sentinel_high` | `dict[str, float]` | `{"RTH_avgITI_sec": ..., "ETH_avgITI_sec": ...}` — column → replacement when raw == 44444.0 |
| `sentinel_zero` | `list[str]` | `["RTH_tradeSize"]` — replaced with 0.0 when raw == 44444.0 |
| `winsor` | `dict[str, [lo, hi]]` | clip bounds, train-fold 0.5/99.5 percentiles (55 cols computed, only the ones in `features_used` apply) |
| `target` | `{"transform": "asinh", "winsor_cap": float}` | regressor output inversion; absent/unused for classifiers |
| `threshold` | `float` | classifier only — the `>=` cutoff this model was trained against |
| `full_train` | `bool` | `false` for model-2, `true` for model-2FT — whether this model was validated on a held-out fold at all |

Verified (this session, reading `model-2/xgb_cons_se_prep.json` directly):
none of the 3 ITI/trade-size sentinel columns or their `__nodata` flags
survived into `features_used` — same as model-1, this step is presently a
no-op for all 5 model-2 models, but the code does not assume that stays
true after a re-prune.

## 5. Sentinel semantics (unchanged from model-1)

`44444.0` is a fill value written upstream when IBKR had no bars to compute
inter-trade-interval or trade size. It is deliberately mapped to an
out-of-range *high* value for `RTH_avgITI_sec`/`ETH_avgITI_sec`
("illiquid" reads as "more illiquid than anything observed") and to `0.0`
for `RTH_tradeSize`. Do not replace with `NaN`-based handling without
re-deriving the constants from a fresh `build_xy` run.

## 6. CLI surface

```
XGBoost_inference_script.py
  --model-dir {model-1,model-2,model-2FT}   default: model-1 -- PASS EXPLICITLY for model-2
  --input-tsv PATH        default: DEFAULT_BASE_TSV[--model-dir]
  --row-idx INT            0-based data row (header excluded)
  --symbol STR             \
  --arrival-time STR        }  alternative to --row-idx; must match exactly 1 row
  --kind {reg_se,reg_q80,clf_ge20,clf_ge35,clf_ge50,all}   default: all
```

`--model-dir` defaults to `model-1` — **it is easy to forget to pass
`--model-dir model-2` and silently score against model-1 instead.** There is
no warning; both generations share the same `--kind` names and print the
same output shape.

model-2's default `--input-tsv` is
`08_2026-05-04-2026-08-28_RawData_splits_rmCol_NaN_rmCol_rmCol_prosusReScore_reCat.tsv`
(the pre-PCA base file with the corrected Prosus/Class values, 4804×208,
verified this session) — **not** the same file model-1 defaults to.

`all` expands to `reg_se, clf_ge20, clf_ge35, clf_ge50` (`reg_q80` excluded
from `all`, same reasoning as model-1: it targets the 80th percentile, not
a point estimate).

`--symbol`/`--arrival-time` match is exact string equality against the raw
TSV field — no datetime normalization.

## 7. model-2FT: same script, `--model-dir model-2FT`

model-2FT uses the identical 25-feature list, preprocessing, and PCA reuse
as model-2 — only the training data differs: **100% of rows, 0 held out**,
with the round count (`n_estimators`) fixed from model-2's own
early-stopped `best_iteration` rather than re-derived (there is no
validation fold left to early-stop against). Treat model-2FT's predictions
with correspondingly less confidence than model-2's — there is no held-out
score to point to for it at all, its claim rests entirely on model-2's
earlier validation of this same configuration. `cons_q80` specifically was
given a raised 4000-round cap (model-2's q80 hadn't converged at 3000) with
**no early-stop safety net**, so it carries the most unverified overfit
risk of the five.

## 8. What this script does NOT do

- **It does not compute structured features.** See §3.
- **It does not calibrate classifier probabilities.** `scale_pos_weight =
  neg/pos` corrects class imbalance but leaves the output uncalibrated —
  use it for ranking, not as a literal P(event) in an EV calculation.
- **No batch mode.** One row per invocation; each call reloads the ONNX
  FinBERT model and the requested `.ubj` models from disk. For scoring many
  rows, call `build_row_features` + `Booster.predict` directly in a loop.
- **No output file.** Prints to stdout only; pipe/redirect if you need
  persistence.
- **It does not refit PCA per model-dir.** See §9.

## 9. Known footguns

1. **PCA is not model-dir-aware.** `finbert_pca_transform.py`'s
   `MODEL_PATH` is a hardcoded module constant, `model-1/finbert_pca.pkl`,
   and this script calls `transform_pr(embed_pr(...))` with no override.
   Under `--model-dir model-2` (or `model-2FT`), the boosters/prep.json
   come from `model-2/`, but the PCA projection **still** comes from
   `model-1/`. This is intentional and verified correct (refitting PCA on
   model-2's data would be bit-identical — the embeddings never changed,
   only 2 structured columns and the class labels did — so it was never
   refit), but it means there is no `model-2/finbert_pca.pkl` file at all;
   don't go looking for one, and don't "fix" this by pointing it at a
   nonexistent path.
2. **`profile.py` shadowing.** A file literally named `profile.py` sitting
   in a directory on `sys.path` (e.g. a scratchpad next to this script)
   shadows Python's stdlib `profile` module. `transformers`/`optimum`'s
   import chain does `import profile` internally; picking up a stray
   same-named script instead surfaces as an unrelated-looking
   `ImportError: cannot import name 'GenerationMixin' from
   'transformers.generation'` deep in `optimum.onnxruntime.modeling`. If
   you see that exact error, check for a stray `profile.py` before
   suspecting the model files.
3. **`--model-dir` default is `model-1`, not `model-2`.** Repeated from §6
   because it's the single easiest mistake to make with this script.

## 10. Verified correctness

This session, after adding `--model-dir`/`MODEL_SPECS` to the script and a
`best_iteration`-aware `iteration_range` fix (the old code scored with
*every* boosted round, not the early-stopped one — same bug
`score_val_predictions.py` had until 2026-09-16):

- **model-1 regression check**: `--model-dir model-1 --row-idx 4200`
  (Symbol=OXBR) now gives `reg_se=12.18, clf_ge20=0.683, clf_ge35=0.595,
  clf_ge50=0.430` — matches `model-1/..._val_scores.tsv`'s stored row for
  OXBR **exactly** (that file was regenerated with the same fix earlier
  this session). Confirms the fix, not a regression.
- **model-2 parity**: 3 val-fold rows (VMAR/05:30:00.113,
  SNES/08:30:03.526, PN/08:00:00.205) scored via `--model-dir model-2
  --symbol ... --arrival-time ... --kind all` reproduced
  `model-2/..._val_M2-scores.tsv`'s stored `pred_reg_se`/`proba_clf_ge20/
  35/50` **exactly** (16.17/0.615/0.770/0.780,
  8.63/0.488/0.259/0.139, 31.28/0.899/0.876/0.815).
- **PCA gotcha confirmed directly**: `finbert_pca_transform.MODEL_PATH`
  resolves to `.../model-1/finbert_pca.pkl` regardless of `--model-dir`.
- **model-2FT sanity check**: `--model-dir model-2FT` on the same VMAR row
  runs end-to-end and returns a different (in-sample, since this row was
  part of model-2FT's training set) but plausible prediction — no crash,
  correct shape.

## 11. Example

```bash
/home/tom/venv/bin/python XGBoost_inference_script.py \
  --model-dir model-2 --symbol VMAR --arrival-time "05:30:00.113" --kind all
```
```
row_idx=3425  Symbol=VMAR  ArrivalTime=05:30:00.113  actual fromPR_High(%)=16.6
  [reg_se] predicted fromPR_High(%) = 16.17
  [clf_ge20] P(fromPR_High(%) >= 20) = 0.615
  [clf_ge35] P(fromPR_High(%) >= 35) = 0.770
  [clf_ge50] P(fromPR_High(%) >= 50) = 0.780
```
