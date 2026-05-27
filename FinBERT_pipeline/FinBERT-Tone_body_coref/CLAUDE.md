# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This directory contains two parallel pipeline scripts for entity-targeted financial sentiment analysis of the `article_body` field of news JSON files:

- **`FinBERT-tone_body_coref.py`** — uses `yiyanghkust/finbert-tone` (trained on 10-K/10-Q filings and analyst reports; preferred for formal financial language)
- **`FinBERT_body_coref.py`** — uses `ProsusAI/finbert` (the original version)

Both are structured identically and designed for embedding in a long-running WebSocket service: `load_models()` once at startup, then `process()` per article.

## Common commands

Activate the project venv first:
```bash
source ~/venv/bin/activate
```

Run the FinBERT-tone pipeline (default: writes 6 files to `./outputs/`):
```bash
python FinBERT-tone_body_coref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir ./outputs/ \
  --log-file ./outputs/
```

Limit to first N sentences (pre-truncates before fastcoref — significant speed-up):
```bash
python FinBERT-tone_body_coref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir ./outputs/ \
  --log-file ./outputs/ \
  --sentences-to-analyse 10
```

One-time ONNX export (only needed if `FinBERT-tone/finbert_tone_onnx/` is missing):
```bash
python FinBERT-tone/FinBERT-tone-analysis.py --export
```

## Pipeline steps (both scripts)

1. `jsonCleaner/jsonCleaner.py` (`TextCleaner.clean`) — HTML / entity / whitespace / URL / ticker scrub
2. `pronounCer/pronounCer_service.py` (`FastCorefResolver`) — full coreference via fastcoref (FCoref model); resolves "the company" → ticker entity name
3. spaCy `en_core_web_sm` — sentence segmentation (inline, not a sibling script)
4. `NerSecDictionary/NerSecDicCreator.py` (`NERProcessor`) — SEC-EDGAR ticker resolution NER
5. `FinBERT-tone/FinBERT-tone-analysis.py` (`FinBERTInferencer`) — entity-targeted ONNX INT8 inference; each sentence gets one inference per resolved ticker with entity spans rewritten as `[TARGET]` / `[OTHER]`

Sibling script directories are injected into `sys.path` at module load. `FinBERT-tone-analysis.py` is loaded via `importlib.util` (hyphen in filename prevents normal import) and deferred to `load_models()`.

## Output files

Written to `output_dir/` (async via a daemon thread):
- `{stem}_cleaned.json`
- `{stem}_pronouns.json`
- `{stem}_sentences.json`
- `{stem}_NER.json`
- `{stem}_FinBERT-tone.json` (FinBERT-tone script) or `{stem}_FinBERT.json` (ProsusAI script)
- `{stem}_pipeline_source.py` — verbatim copy of the pipeline script (disable with `--no-log-source`)
- `{stem}_pipeline.log` — per-step timing log (when `--log-file <dir>` is a directory)

Stem is derived from the input filename, or `{ticker}-{YYYY-MM-DD}` when called with a dict.

## Key architectural facts

**`allowed_tickers` filter.** If the input dict contains a `"tickers"` list, only those tickers are scored in the FinBERT step. If none of the NER-resolved tickers appear in `allowed_tickers`, the pipeline falls back to scoring all tickers without entity substitution (no `[TARGET]`/`[OTHER]` rewriting).

**`sentences_to_analyse`.** Pre-truncates text at the spaCy sentence boundary *before* coreference runs. This is intentional — it keeps fastcoref from processing the full article when only N sentences are needed, which is the main latency knob.

**`EntityDeduplicator`.** Filters out `ticker=null` entities and uses greedy longest-span deduplication within each ticker group before inference. Prevents double-scoring when NER emits overlapping spans for the same entity.

**`SENTIMENT_THRESHOLD = 0.05`.** `sentiment_score` (pos − neg) values in (−0.05, +0.05) are labelled `"neutral"`.

**Test input.** `../jsons/BWEN-2026-05-12.json` and other files in `../jsons/` are the canonical dev inputs.

## Dependencies

```
spacy + en_core_web_sm
fastcoref                           # required; not optional in these scripts
transformers
optimum[onnxruntime]
numpy
flask, requests                     # pulled transitively by pronounCer_service.py
```

Install missing deps:
```bash
pip install --break-system-packages fastcoref
pip install --break-system-packages numpy transformers optimum[onnxruntime]
```
