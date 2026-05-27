# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`FinBERT_body_noCoref.py` is the fast variant of the FinBERT body pipeline — coreference resolution is intentionally omitted. Use this when latency matters more than pronoun-chain resolution. The sibling `FinBERT_body_coref/` directory adds a fastcoref step between cleaning and sentence splitting.

Four-step pipeline:
1. **jsonCleaner** — HTML/entity/whitespace/URL/ticker scrub (`TextCleaner.clean()`)
2. **SentenceSplitter** — spaCy sentence boundary detection (inline, not calling the standalone script)
3. **NerSecDicCreator** — SEC EDGAR ticker resolution NER (`TickerResolver` + `NERProcessor`)
4. **FinBERT-analysis** — entity-targeted FinBERT ONNX INT8 sentiment inference

## Common commands

Activate the venv first (`optimum[onnxruntime]` lives there):
```bash
source ~/venv/bin/activate
```

Run the pipeline:
```bash
python FinBERT_body_noCoref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir outputs/ \
  --log-file outputs/ \
  --sentences-to-analyse 10
```

CLI flags:
- `--input` (required) — path to JSON file with an `article_body` field
- `--output-dir` (default `./finBERT_outputs`) — destination for intermediate + final JSONs
- `--no-write` — skip writing files; result dict still returned in memory
- `--no-log-source` — skip writing a verbatim source copy to output dir
- `--finbert-model-dir` — override the ONNX model directory
- `--log-file` — file **or directory**; if a directory, per-article log files are created as `{stem}_pipeline.log`
- `--sentences-to-analyse N` — truncate to first N sentences before NER/FinBERT (saves significant latency)

One-time FinBERT ONNX export (only if `../FinBERT/finbert_onnx/` is missing):
```bash
python ../FinBERT/FinBERT-analysis.py --export
```

## Library use (WebSocket integration)

```python
from FinBERT_body_noCoref import FinBERTBodyPipeline

pipeline = FinBERTBodyPipeline(write_outputs=False, sentences_to_analyse=10)
pipeline.load_models()          # heavy: spaCy + SEC cache + FinBERT ONNX — do once
result = pipeline.process(article_dict)   # fast per-article
pipeline.shutdown()             # flushes ticker resolver cache to disk
```

`process()` accepts a `dict` (must have non-empty `article_body`) or a file path. It returns:
```python
{
    "cleaned":   {"article_body": str},
    "sentences": {"metadata": {...}, "sentences": [...]},
    "ner":       {"metadata": {...}, "sentences": [...]},
    "finbert":   {"metadata": {...}, "ticker_sentiments": {...}},
    "stem":      str,
    "timings":   [{"step": str, "elapsed_ms": float, ...}],
}
```

`write_outputs` and `sentences_to_analyse` can be overridden per `process()` call.

## Architecture notes

**Sibling script imports.** The pipeline adds the parent directory's `jsonCleaner/`, `SentenceSplitter/`, `NerSecDictionary/`, and `FinBERT/` subdirectories to `sys.path` at import time, then calls inner classes (`TextCleaner`, `TickerResolver`, `NERProcessor`) directly — the standalone CLIs of those scripts are not invoked.

**Deferred FinBERT import.** `FinBERT-analysis.py` (hyphen — loaded via `importlib.util`) calls `sys.exit(1)` if ML deps are missing. The import is deferred to `load_models()` so the module is importable without `transformers`/`optimum` installed.

**`allowed_tickers` filter.** If the input dict contains a `"tickers"` list, FinBERT inference is restricted to those tickers. Sentences where NER found no matching ticker still contribute to the aggregate if the raw text is scored against the `allowed_tickers` set directly.

**Disk writes are async.** When `write_outputs=True`, the four output JSONs are written in a daemon thread (`_dump_thread`). Call `shutdown()` (or join `_dump_thread`) before exiting if the write must complete.

**Output stem.** File path input → stem from filename. Dict input → `{ticker}-{YYYY-MM-DD}` from `ticker`/`created` fields, or `article-{timestamp}` fallback.

## Dependencies

- `spacy` + `en_core_web_sm`
- `transformers`, `optimum[onnxruntime]`, `numpy` — only needed at `load_models()` time
- `lxml` — optional, used by `TextCleaner`; falls back to regex if missing

SEC EDGAR ticker cache is stored at `~/.cache/NerSecDictionary/` with a 7-day TTL.
