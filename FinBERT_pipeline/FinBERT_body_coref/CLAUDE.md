# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`FinBERT_body_coref.py` is the **full coreference** variant of the FinBERT body pipeline. It always runs fastcoref — unlike the parent `FinBERT_body_pipeline.py` which can fall back to simple mode. Use `FinBERT_body_noCoref.py` when speed matters more than coreference quality.

Pipeline order: **clean → corefs (fastcoref) → split → NER → FinBERT ONNX**

## Common commands

```bash
# Activate venv first (ONNX runtime lives there)
source ~/venv/bin/activate

# Full run with output files and per-step log
python FinBERT_body_coref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir ./outputs/ \
  --log-file ./outputs/

# Fast: limit to first 10 sentences (text is pre-truncated before coref)
python FinBERT_body_coref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir ./outputs/ \
  --sentences-to-analyse 10

# No disk writes (in-memory only)
python FinBERT_body_coref.py --input ../jsons/BWEN-2026-05-12.json --no-write
```

### All CLI flags
| Flag | Default | Notes |
|---|---|---|
| `--input` | required | JSON file with `article_body` field |
| `--output-dir` | `./finBERT_outputs` | Directory for 5 intermediate JSONs |
| `--no-write` | off | Skip disk writes |
| `--no-log-source` | off | Skip writing verbatim script copy to output dir |
| `--finbert-model-dir` | `./FinBERT/finbert_onnx` | Override ONNX model path |
| `--log-file` | none | File path **or** directory; directory → per-stem `{stem}_pipeline.log` |
| `--coref-device` | `auto` | `auto`, `cpu`, or `cuda` |
| `--sentences-to-analyse` | all | Pre-truncate before coref; spaCy splits are used for truncation |

### Library use (WebSocket integration)

```python
from FinBERT_body_coref import FinBERTBodyPipeline

pipeline = FinBERTBodyPipeline(
    output_dir="./outputs",
    write_outputs=False,
    coref_device=None,            # auto-select
    sentences_to_analyse=15,      # cap for latency
)
pipeline.load_models()            # raises RuntimeError if fastcoref missing
result = pipeline.process(article_dict)   # dict with article_body key
pipeline.shutdown()               # flushes ticker cache to disk
```

`process()` returns: `{"cleaned", "pronouns", "sentences", "ner", "finbert", "stem", "timings"}`.

## Architecture

### Local-only script policy

**All sibling scripts are local copies inside this directory.** Do not edit the shared originals under `N2/scripts/` — changes must be made here. The local subdirectories are:

```
./jsonCleaner/jsonCleaner.py
./pronounCer/pronounCer_service.py
./SentenceSplitter/SentenceSplitter.py
./NerSecDictionary/NerSecDicCreator.py
./FinBERT/FinBERT-analysis.py          ← local copy; model symlinked below
./FinBERT/finbert_onnx -> ../../../FinBERT/finbert_onnx  (symlink, not duplicated)
```

`FinBERT_body_coref.py` injects these local paths into `sys.path` at import time and calls their inner classes directly — no Flask server, no inter-process I/O. Each subdirectory has its own CLAUDE.md with class-level detail.

| Import | Class used | Purpose |
|---|---|---|
| `jsonCleaner` | `TextCleaner.clean()` | HTML/whitespace/URL/ticker scrub |
| `pronounCer_service` | `FastCorefResolver` | Full coreference (fastcoref FCoref) |
| `NerSecDicCreator` | `TickerResolver`, `NERProcessor` | SEC-EDGAR ticker NER |
| `FinBERT-analysis.py` | `FinBERTInferencer`, `EntityDeduplicator`, `TextSubstitutor`, `SentimentAggregator` | ONNX INT8 inference |

`FinBERT-analysis.py` (hyphen — loaded via `importlib.util`) is deferred to `load_models()` so the module is importable without ML deps.

### Hard fastcoref requirement

`load_models()` raises `RuntimeError` immediately if `fastcoref` is not importable — there is no silent fallback here, unlike the parent pipeline.

### `--sentences-to-analyse` pre-truncation

When set, spaCy splits the cleaned text and slices to the Nth sentence boundary **before** coreference runs. This means fastcoref only processes the subset that will be analysed — significant latency reduction for long articles.

### Output files (written in a background thread)

```
{stem}_cleaned.json
{stem}_pronouns.json
{stem}_sentences.json
{stem}_NER.json
{stem}_FinBERT.json
{stem}_pipeline_source.py   (unless --no-log-source)
```

`shutdown()` must be called if `write_outputs=True` to ensure the background dump thread finishes and the ticker resolver cache is flushed.

## fastcoref dependency notes

`fastcoref 2.1.6` is incompatible with `transformers >= 4.44`. Pin after install:

```bash
pip install --break-system-packages fastcoref
pip install --break-system-packages "transformers==4.39.3"
```

Always verify with model instantiation, not just import:

```bash
python -c "from fastcoref import FCoref; FCoref(); print('OK')"
```

Common crash `'FCorefModel' has no attribute 'all_tied_weights_keys'` means transformers is too new — re-pin to 4.39.3.
