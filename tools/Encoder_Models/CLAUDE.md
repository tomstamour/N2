# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Standalone headline-sentiment scorers ("headliners") — one script per encoder
model, each scoring a single PR headline and returning the same sentiment
metrics. Hard budget: **<150 ms per headline with a pre-warmed model**. Used to
compare encoders for the N2 news pipeline. Run everything with
`/home/tom/venv/bin/python` (root CLAUDE.md rule).

## Commands

```bash
# One-time per model: HuggingFace download → ONNX export → (usually) INT8 quantize
/home/tom/venv/bin/python FinMarBa_headliner.py --export

# Score a headline string / a file's first non-empty line
/home/tom/venv/bin/python FinMarBa_headliner.py --headline "Apple reports record earnings"
/home/tom/venv/bin/python FinMarBa_headliner.py --file ./headline-example.txt

# After any --export: verify the fresh models replicate the validated reference
# (golden probe headlines + expected scores live in verify_goldens.txt)
/home/tom/venv/bin/python verify_headliners.py            # all models; exit 1 on FAIL
/home/tom/venv/bin/python verify_headliners.py --model DeBERTa

# Batch-score a TSV's Body with all 12 encoders (the 11 here + ProsusAI FinBERT)
/home/tom/venv/bin/python BodyScore12encoders.py --input ../../price_stats_loader/outputs/FILE.tsv

# Re-score an already-scored file IN PLACE (header untouched, values overwritten)
/home/tom/venv/bin/python BodyScore12encoders.py --input FILE.tsv \
    --cap-words 200 --replace-existing --out-suffix _prosusReScore.tsv
```

Model dirs (`*_onnx/`, `loumcd_sdict/`) are gitignored build artifacts — on a
fresh clone rebuild them with `--export`; never force-add them.

## Architecture

Every script is the same skeleton: an `ONNXExporter` (download/export/quantize),
an inferencer class, and a module-level singleton exposed as a library API —
`load_model()` to pre-warm, `score_headline(str)` / `score_headline_file(path)`
returning `{neg, neu|ind, pos, ss, lab, ms}`. `ss` = positive − negative
(LouMcD-SDict: (pos − neg)/(pos + neg) over lexicon hits). The zero-shot scripts
(ModernBERT, DeBERTa) have no classification head — they score via the NLI
hypothesis template `"This example is {}."`.

To add a model: copy the closest existing headliner, then probe-verify the
label map with known-sentiment headlines before trusting any output.

## ⚠ Column / key naming — unified 2026-08-09

One name per score, everywhere: standalone script, batch scorer, and live
`Orchestrator5.2`. Columns are

```
{Model}_{channel}_{hd|bd}       e.g.  FLANG_pos_hd   LouMcD_smod_bd
```

`hd` = the model scored the **headline**, `bd` = the **body**.

- **Channels**: `pos` `neu` `neg` `ss` `lab` `ms`; FinMarBa uses **`ind`** in place
  of `neu`; LouMcD-SDict adds `unc` `lit` `smod` `wmod` `cons`.
- **Model tokens** (hyphen-free, so a column is a legal pandas attribute):
  `FinMarBa` `FLANG` `FLElectra` `RoBERTa` `ModernB` `DeBERTa` `FinTone`
  `FinancialB` `DistilRo` `TradeHero` `LouMcD`, plus **`Prosus`** for ProsusAI
  FinBERT (`../../FinBERT/FinBERT-headliner.py`, the one headliner outside this
  directory and not in `MODELS`).
- The **`hd`/`bd` suffix is NOT in the result dict** — a headliner handed a body
  string cannot know which it got. `HeadScore11encoders` appends `_hd`,
  `BodyScore11encoders` appends `_bd`, and that is the whole reason the body twin
  can reuse the head registry untouched.
- **`BodyScore12encoders`** is `BodyScore11encoders` + **`Prosus`** (ProsusAI
  FinBERT, the headliner outside this directory), scoring the SAME prepped
  200-word body text as the other 11 → 76 `_bd` columns, Prosus last. It extends
  the registry locally (`MODELS12 = {**MODELS, "Prosus": ...}`, whose entry
  carries a `path` key so `score_table` imports from `../../FinBERT/`); `MODELS`
  itself gains no entry, so the `_hd` path and its 70 columns are unchanged.
  `FinBERT_TSV_scorer.py` still scores the RAW body — its `Prosus_*_bd` values
  are not comparable with these. `--replace-existing` overwrites the `_bd`
  columns a file ALREADY has instead of appending — it matches a pandas
  duplicate-name suffix too (`FinMarBa_ss_bd.1`), never renames or reorders a
  column, and skips (does not add) a channel the file no longer carries, so a
  column-pruned research file keeps its exact shape.
- **`ms` replaced `elapsed_ms` (7 scripts) and `inference_ms` (4)**, which removed
  a `setdefault` papering over the split in `HeadScore11encoders` and a two-key
  lookup in `verify_headliners`.
- ⚠ **`ind` is not a neutral probability.** FinMarBa's classes are
  negative/indecisive/positive, so a LOW `ind` means confident in *either*
  direction. It keeps a distinct token so no one reads it as `neu` — see the ⚠⚠
  warning in `orchestrator_trigger_rules.txt` about the rule this cost.
- ⚠ **`LABEL_ORDER` and the internal `scores` dicts deliberately keep FULL words.**
  `lab`'s VALUE is one of those words (`max(scores, key=...)`), so shortening them
  would change cell contents, not just names. Only the public return is renamed.
- **Migrating an old TSV**: `rename_encoder_cols.py` rewrites the header line only
  (values did not change). It is idempotent and refuses a file whose header
  matches neither vocabulary — which matters because the column COUNT is identical
  before and after, so a half-migrated file cannot be caught by arity.
- Old spellings, for reading pre-08-09 files: `FLANG-BERT_positive` (head),
  `FLANG-BERT_Body_positive` (body — an **infix**, now a suffix),
  `FinMarBa_indecisive`, `{M}_sentiment_score`, `{M}_elapsed_ms`.

## Models

| Script (column token) | Checkpoint | Precision | Logit order 0/1/2 | Warm | Quirk |
|---|---|---|---|---|---|
| FinMarBa | baptle/FinBERT_market_based | INT8 | neg/indecisive/pos | ~12 ms | config.json id2label is junk numerics |
| FLANG-BERT (`FLANG`) | avinasht/FLANG-BERT_flang-bert | INT8 | neg/neu/pos | ~12 ms | tokenizer from base SALT-NLP repo (official FLANG-BERT is MLM-only); weak on biotech positives |
| FLANG-ELECTRA (`FLElectra`) | avinasht/FLANG-ELECTRA_Synonym-wordnet | **FP32** | neg/neu/pos | ~110 ms | INT8 flips delisting→neutral; intra_op=2 |
| RoBERTa | cardiffnlp/twitter-roberta-base-sentiment-latest | INT8 | neg/neu/pos | ~7 ms | neutral-heavy on formal PR wording |
| ModernBERT (`ModernB`) | MoritzLaurer/ModernBERT-base-zeroshot-v2.0 | INT8 | zero-shot NLI | ~70 ms | large variant rejected (INT8 flips labels, FP32 530 ms); intra_op=2 |
| DeBERTa | MoritzLaurer/deberta-v3-base-zeroshot-v2.0 | **FP32** | zero-shot NLI | ~105 ms | INT8 corrupts DeBERTa-v3 (uniform probs, label flips); intra_op=3 |
| finbert-tone (`FinTone`) | yiyanghkust/finbert-tone | INT8 | neu/pos/neg | ~12 ms | neutral-heavy on regulatory/biotech |
| FinancialBERT (`FinancialB`) | ahmedrachid/FinancialBERT-Sentiment-Analysis | INT8 | neg/neu/pos | ~15 ms | dry bad news (delisting, flat bankruptcy) → neutral |
| distilroberta (`DistilRo`) | mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis | INT8 | neg/neu/pos | ~6.5 ms | fastest transformer; dry bad news → neutral |
| Trading-Hero (`TradeHero`) | fuchenru/Trading-Hero-LLM | INT8 | neu/pos/neg | ~7 ms | delisting → neutral |
| LouMcD-SDict (`LouMcD`) | Loughran-McDonald lexicon 1993-2025 | stdlib | 7-category counts | ~0.02 ms | polarity = (pos−neg)/(pos+neg); pure stdlib, no ONNX |

## Rules

- `LABEL_ORDER` differs per model and every one was **probe-verified** against
  known headlines — never assume neg/neu/pos and never trust a checkpoint's
  `config.json` id2label (several are wrong or junk).
- FP32 vs INT8 is a deliberate per-model verdict — do not "optimize" the FP32
  models (DeBERTa, FLANG-ELECTRA) down to INT8; quantization corrupts them.
- `intra_op_num_threads` values are tuned for this CPU; keep them.
- `verify_goldens.txt` records **validated behavior including known quirks**
  (e.g. Trading-Hero delisting→neutral is stored as-is). A verify FAIL means
  the fresh export differs from the validated one — not that the golden is
  wrong. Only re-baseline (`verify_headliners.py --capture --force`) after
  deliberately re-validating a model by hand.
- ⚠ **A key RENAME also fails every probe** (goldens compare flattened dot-paths
  and reject unknown/missing keys), which makes `--capture --force` tempting and
  dangerous: it would silently bake in any numeric regression alongside the
  rename. The 2026-08-09 rename was cleared first by scoring all 8 probes × 12
  models with the new keys, mapping them back to the old names, and asserting
  every value byte-identical to the previous goldens (96/96) — *then* capturing.
  Do the same for any future rename; the timing key is exempt (`VOLATILE_KEYS`).
