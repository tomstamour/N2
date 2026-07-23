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
```

Model dirs (`*_onnx/`, `loumcd_sdict/`) are gitignored build artifacts — on a
fresh clone rebuild them with `--export`; never force-add them.

## Architecture

Every script is the same skeleton: an `ONNXExporter` (download/export/quantize),
an inferencer class, and a module-level singleton exposed as a library API —
`load_model()` to pre-warm, `score_headline(str)` / `score_headline_file(path)`
returning `{negative, neutral|indecisive, positive, sentiment_score, label,
elapsed_ms}`. `sentiment_score` = positive − negative (LouMcD-SDict:
(pos − neg)/(pos + neg) over lexicon hits). The zero-shot scripts (ModernBERT,
DeBERTa) have no classification head — they score via the NLI hypothesis
template `"This example is {}."`.

To add a model: copy the closest existing headliner, then probe-verify the
label map with known-sentiment headlines before trusting any output.

## Models

| Script | Checkpoint | Precision | Logit order 0/1/2 | Warm | Quirk |
|---|---|---|---|---|---|
| FinMarBa | baptle/FinBERT_market_based | INT8 | neg/indecisive/pos | ~12 ms | config.json id2label is junk numerics |
| FLANG-BERT | avinasht/FLANG-BERT_flang-bert | INT8 | neg/neu/pos | ~12 ms | tokenizer from base SALT-NLP repo (official FLANG-BERT is MLM-only); weak on biotech positives |
| FLANG-ELECTRA | avinasht/FLANG-ELECTRA_Synonym-wordnet | **FP32** | neg/neu/pos | ~110 ms | INT8 flips delisting→neutral; intra_op=2 |
| RoBERTa | cardiffnlp/twitter-roberta-base-sentiment-latest | INT8 | neg/neu/pos | ~7 ms | neutral-heavy on formal PR wording |
| ModernBERT | MoritzLaurer/ModernBERT-base-zeroshot-v2.0 | INT8 | zero-shot NLI | ~70 ms | large variant rejected (INT8 flips labels, FP32 530 ms); intra_op=2 |
| DeBERTa | MoritzLaurer/deberta-v3-base-zeroshot-v2.0 | **FP32** | zero-shot NLI | ~105 ms | INT8 corrupts DeBERTa-v3 (uniform probs, label flips); intra_op=3 |
| finbert-tone | yiyanghkust/finbert-tone | INT8 | neu/pos/neg | ~12 ms | neutral-heavy on regulatory/biotech |
| FinancialBERT | ahmedrachid/FinancialBERT-Sentiment-Analysis | INT8 | neg/neu/pos | ~15 ms | dry bad news (delisting, flat bankruptcy) → neutral |
| distilroberta | mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis | INT8 | neg/neu/pos | ~6.5 ms | fastest transformer; dry bad news → neutral |
| Trading-Hero | fuchenru/Trading-Hero-LLM | INT8 | neu/pos/neg | ~7 ms | delisting → neutral |
| LouMcD-SDict | Loughran-McDonald lexicon 1993-2025 | stdlib | 7-category counts | ~0.02 ms | polarity = (pos−neg)/(pos+neg); pure stdlib, no ONNX |

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
