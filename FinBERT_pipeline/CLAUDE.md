# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

This directory contains a single end-to-end pipeline, **`FinBERT_body_pipeline.py`**, that takes the `article_body` field of a news JSON and produces per-ticker FinBERT sentiment scores. It is designed to be embedded in a long-running WebSocket service: models are loaded once, then `process()` is called per incoming article on a hot path.

The pipeline does **not** reimplement the underlying NLP steps — it orchestrates five sibling scripts under `N2/scripts/`:

1. `jsonCleaner/jsonCleaner.py`        — HTML / entity / whitespace / URL / ticker scrub
2. `pronounCer/pronounCer_service.py`  — coreference resolution (fastcoref or spaCy fallback)
3. `SentenceSplitter/SentenceSplitter.py` — spaCy sentence segmentation
4. `NerSecDictionary/NerSecDicCreator.py` — SEC-EDGAR ticker resolution NER
5. `FinBERT/FinBERT-analysis.py`       — entity-targeted FinBERT inference (ONNX INT8)

`FinBERT_pipeline.py` (no `_body_` infix) is **not** a script — it is the original spec text the pipeline was built from. Don't edit it as if it were code.

## Common commands

Activate the project venv first — `optimum[onnxruntime]` lives there:
```bash
source ~/venv/bin/activate
```

Run the full pipeline (writes 5 intermediate JSONs + summary to stdout):
```bash
python FinBERT_body_pipeline.py --input AEHL-2026-05-08.json
```

Fast mode (no disk writes, no fastcoref):
```bash
python FinBERT_body_pipeline.py --input AEHL-2026-05-08.json --no-write --coref-mode simple
```

CLI flags: `--input`, `--output-dir` (default `./finBERT_outputs`), `--coref-mode {simple,full}` (default `full`), `--no-write`, `--finbert-model-dir` (override).

### Library use (intended WebSocket integration)
```python
from FinBERT_body_pipeline import FinBERTBodyPipeline

pipeline = FinBERTBodyPipeline(coref_mode="full", write_outputs=False)
pipeline.load_models()                # heavy: spaCy + fastcoref + SEC cache + FinBERT ONNX
result = pipeline.process(article)    # dict OR path; returns all 5 step outputs
```

`process()` accepts either a Python dict (WebSocket payload) or a path to a JSON file. The dict must contain a non-empty `article_body` key.

### One-time FinBERT ONNX export (only if `../FinBERT/finbert_onnx/` is missing)
```bash
python ../FinBERT/FinBERT-analysis.py --export
```

## Architecture notes that matter

**Sibling-script imports.** The pipeline injects `../jsonCleaner`, `../pronounCer`, `../SentenceSplitter`, `../NerSecDictionary`, `../FinBERT` into `sys.path` at module-load time, then imports `TextCleaner`, `PronounResolver`/`FastCorefResolver`, `TickerResolver`/`NERProcessor` directly. The pronounCer Flask service is **not** used — its resolver classes are called in-process.

**Deferred FinBERT import.** `FinBERT-analysis.py` (note the hyphen — loaded via `importlib.util`) calls `sys.exit(1)` at import time if `transformers` or `optimum[onnxruntime]` is missing. The pipeline defers that import to `load_models()` so this module is importable even when those deps are absent. Keep this pattern if you refactor — it lets tests and tooling import the pipeline without ML deps.

**Field-name convention.** Downstream scripts (`jsonCleaner`, `SentenceSplitter`, `pronounCer`) hard-code `headline`/`summary`/`content` field names. This pipeline deliberately uses `article_body` as-is and bypasses those scripts' file-loading entry points by calling their inner classes (`TextCleaner.clean`, `NERProcessor.process_all_sentences`, etc.) directly with manually constructed sentence dicts that set `source="article_body"`.

**Output stem derivation.** When `process()` is called with a file path, the stem is the input filename. When called with a dict, the stem is `{ticker}-{YYYY-MM-DD}` parsed from the dict's `ticker` and `created` fields (the standard schema in `orchestrator3/outputs/`), falling back to `article-{timestamp}` if either is missing.

**Output files** are flat in `output_dir/`: `{stem}_cleaned.json`, `{stem}_pronouns.json`, `{stem}_sentences.json`, `{stem}_NER.json`, `{stem}_FinBERT.json` — same naming convention as the standalone scripts produce.

**Coref mode fallback.** `coref_mode="full"` silently falls back to `simple` if `fastcoref` is not importable. The instance attribute `self.coref_mode` is updated after the fallback so the chosen mode is observable.

## Dependencies

Required in the active Python environment:
- `spacy` + `en_core_web_sm` — sentence splitting and simple corefs
- `flask`, `requests` — pulled transitively by `pronounCer_service.py` even though the Flask app is never started
- `fastcoref` — required for `--coref-mode full`; pipeline degrades to simple if missing
- `transformers`, `optimum[onnxruntime]`, `numpy` — required at `load_models()` for the FinBERT step

The FinBERT ONNX model is expected at `../FinBERT/finbert_onnx/` (override with `finbert_model_dir=` or `--finbert-model-dir`).

## Test asset

`AEHL-2026-05-08.json` is the canonical dev/smoke-test article (real Globe Newswire content with `article_body`, `ticker`, `created` fields). Expected after a successful run: ~32 sentences, tickers `AEHL`, `BTC`, `SOC` resolved (the latter two are known false positives from the underlying NER — not pipeline bugs).
