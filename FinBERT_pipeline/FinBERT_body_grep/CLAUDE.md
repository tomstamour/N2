# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Local scripts only — mandatory rule

**All sibling-script paths in every script in this directory must be local (`./`) paths only.** No script may reference a sibling via `_THIS_DIR.parent`, `_THIS_DIR.parent.parent`, or any path that escapes the current directory.

Specifically, the four required local subdirectories are:

| Subdirectory | Status |
|---|---|
| `./jsonCleaner/` | present — local copy |
| `./SentenceSplitter/` | present — local copy |
| `./NerSecDictionary/` | present — local copy |
| `./FinBERT/` | **must exist as a local directory or symlink** |

`FinBERT/` is not checked in as a full copy. Create it once with:
```bash
# Run from this directory (FinBERT_body_grep/)
mkdir -p FinBERT
cp ../../../FinBERT/FinBERT-analysis.py FinBERT/
ln -s ../../../FinBERT/finbert_onnx FinBERT/finbert_onnx
```
This mirrors the pattern used in the sibling `FinBERT_body_coref/` directory.

When writing or reviewing scripts here, verify that `_FINBERT_DIR` is set to `_THIS_DIR / "FinBERT"`, not `_THIS_DIR.parent.parent / "FinBERT"`.

---

## Purpose

This directory contains two pipeline scripts:

- **`FinBERT_body_noCoref.py`** — fastest variant; coreference resolution is entirely omitted.
- **`FinBERT_body_grepCoref.py`** — lightweight grep-based coreference; replaces a configurable list of pronoun/reference strings (e.g. "the company", "we", "our") with the primary entity name before NER and FinBERT, so entity-adjacent sentences are captured without requiring fastcoref.

Use `FinBERT_body_noCoref.py` when latency is the only concern.
Use `FinBERT_body_grepCoref.py` when you want cheap pronoun resolution with no ML overhead.

### FinBERT_body_noCoref.py — four-step pipeline
1. **jsonCleaner** — HTML/entity/whitespace/URL/ticker scrub (`TextCleaner.clean()`)
2. **SentenceSplitter** — spaCy sentence boundary detection (inline)
3. **NerSecDicCreator** — SEC EDGAR ticker resolution NER (`TickerResolver` + `NERProcessor`)
4. **FinBERT-analysis** — entity-targeted FinBERT ONNX INT8 sentiment inference

### FinBERT_body_grepCoref.py — five-step pipeline
1. **jsonCleaner** — HTML/entity/whitespace/URL/ticker scrub
2. **grepCoref** — replace pronoun/reference strings with entity name (from `coreference_grep_strings.txt` or `--strings-to-grep`)
3. **SentenceSplitter** — spaCy sentence boundary detection
4. **NerSecDicCreator** — SEC EDGAR ticker resolution NER
5. **FinBERT-analysis** — entity-targeted FinBERT ONNX INT8 sentiment inference

---

## Common commands

Activate the venv first (`optimum[onnxruntime]` lives there):
```bash
source ~/venv/bin/activate
```

Run the no-coref pipeline:
```bash
python FinBERT_body_noCoref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir outputs/ \
  --log-file outputs/ \
  --sentences-to-analyse 10
```

Run the grep-coref pipeline:
```bash
python FinBERT_body_grepCoref.py \
  --input ../jsons/BWEN-2026-05-12.json \
  --output-dir outputs/ \
  --log-file outputs/ \
  --strings-to-grep coreference_grep_strings.txt \
  --sentences-to-analyse 10
```

CLI flags (both scripts):
- `--input` (required) — path to JSON file with an `article_body` field
- `--output-dir` (default `./finBERT_outputs`) — destination for intermediate + final JSONs
- `--no-write` — skip writing files; result dict still returned in memory
- `--no-log-source` — skip writing a verbatim source copy to output dir
- `--finbert-model-dir` — override the ONNX model directory
- `--log-file` — file **or directory**; if a directory, per-article log files are created as `{stem}_pipeline.log`
- `--sentences-to-analyse N` — truncate to first N sentences before NER/FinBERT (saves significant latency)

Additional flag for `FinBERT_body_grepCoref.py` only:
- `--strings-to-grep FILE` — file with one pronoun/string pattern per line (default: `coreference_grep_strings.txt` next to the script); pass empty string to disable grep coref

One-time FinBERT ONNX export (only if `./FinBERT/finbert_onnx/` is missing):
```bash
python ./FinBERT/FinBERT-analysis.py --export
```

---

## Library use (WebSocket integration)

```python
# No-coref variant
from FinBERT_body_noCoref import FinBERTBodyPipeline

pipeline = FinBERTBodyPipeline(write_outputs=False, sentences_to_analyse=10)
pipeline.load_models()
result = pipeline.process(article_dict)
pipeline.shutdown()
```

```python
# Grep-coref variant
from FinBERT_body_grepCoref import FinBERTBodyPipeline

pipeline = FinBERTBodyPipeline(
    write_outputs=False,
    sentences_to_analyse=10,
    grep_strings_file="coreference_grep_strings.txt",
)
pipeline.load_models()
result = pipeline.process(article_dict)
pipeline.shutdown()
```

`process()` accepts a `dict` (must have non-empty `article_body`) or a file path.

Return dict for `FinBERT_body_noCoref.py`:
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

Return dict for `FinBERT_body_grepCoref.py` (adds `"grepcoref"` key):
```python
{
    "cleaned":   {"article_body": str},
    "grepcoref": {
        "article_body": str,
        "metadata": {
            "entity_name": str,
            "patterns_file": str,
            "patterns_loaded": int,
            "replacements_made": int,
            "applied_patterns": [{"pattern": str, "count": int}, ...],
        },
    },
    "sentences": {"metadata": {...}, "sentences": [...]},
    "ner":       {"metadata": {...}, "sentences": [...]},
    "finbert":   {"metadata": {...}, "ticker_sentiments": {...}},
    "stem":      str,
    "timings":   [{"step": str, "elapsed_ms": float, ...}],
}
```

`write_outputs`, `sentences_to_analyse`, and (for grepCoref) `grep_strings_file` can be overridden per `process()` call.

---

## Architecture notes

**Local paths only.** Every `_*_DIR` variable must resolve to a subdirectory of `_THIS_DIR`. Never escape via `..`. See the "Local scripts only" section at the top.

**Sibling script imports.** The pipeline adds `./jsonCleaner/`, `./SentenceSplitter/`, `./NerSecDictionary/`, and `./FinBERT/` to `sys.path` at import time, then calls inner classes (`TextCleaner`, `TickerResolver`, `NERProcessor`) directly — the standalone CLIs of those scripts are not invoked.

**Deferred FinBERT import.** `FinBERT-analysis.py` (hyphen — loaded via `importlib.util`) calls `sys.exit(1)` if ML deps are missing. The import is deferred to `load_models()` so the module is importable without `transformers`/`optimum` installed.

**grep-coref entity name.** The replacement entity name is resolved from the article dict's `ticker` field (or first entry of `tickers`) via `TickerResolver.ticker_to_info[ticker]["official_name"]`. Falls back to the raw ticker symbol if the name is not in the SEC cache.

**`allowed_tickers` filter.** If the input dict contains a `"tickers"` list, FinBERT inference is restricted to those tickers. Sentences where NER found no matching ticker still contribute to the aggregate if the raw text is scored against the `allowed_tickers` set directly.

**Disk writes are async.** When `write_outputs=True`, output JSONs are written in a daemon thread (`_dump_thread`). Call `shutdown()` (or join `_dump_thread`) before exiting if the write must complete.

**Output stem.** File path input → stem from filename. Dict input → `{ticker}-{YYYY-MM-DD}` from `ticker`/`created` fields, or `article-{timestamp}` fallback.

**Output files** for `FinBERT_body_grepCoref.py`:
- `{stem}_cleaned.json`
- `{stem}_grepcoref.json`
- `{stem}_sentences.json`
- `{stem}_NER.json`
- `{stem}_FinBERT.json`
- `{stem}_pipeline.log` (if `--log-file` is a directory)
- `{stem}_pipeline_source.py` (unless `--no-log-source`)

---

## grep pattern file format (`coreference_grep_strings.txt`)

One pattern per line. Surrounding double or single quotes are stripped. Patterns with leading/trailing spaces are matched and replaced with the entity name surrounded by the same whitespace to avoid run-on words.

Example:
```
" we "
"The company"
"the company"
" our "
```

---

## Dependencies

- `spacy` + `en_core_web_sm`
- `transformers`, `optimum[onnxruntime]`, `numpy` — only needed at `load_models()` time
- `lxml` — optional, used by `TextCleaner`; falls back to regex if missing

SEC EDGAR ticker cache is stored at `~/.cache/NerSecDictionary/` with a 7-day TTL.
