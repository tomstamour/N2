# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**pronounCer** is a high-performance pronoun and coreference resolution system with a dual-mode architecture:
- **Simple Mode**: Fast pronoun-only resolution using spaCy (0.3-0.5s per file)
- **Full Mode**: Complete coreference resolution using fastcoref (1-2s per file)

The system uses a persistent background service (Flask) that keeps the NLP model loaded in memory, eliminating 1-3 second model loading overhead on each run.

## Architecture

### Two-Component Design

1. **Service** (`pronounCer_service.py`): Flask HTTP server on localhost:5050
   - Loads and maintains spaCy `en_core_web_sm` model in memory
   - Optionally loads fastcoref model for full coreference resolution
   - Exposes `/health`, `/resolve`, `/config`, and root endpoints
   - Single instance processes all requests

2. **Client** (`pronounCer.py`): Command-line tool for batch file processing
   - Validates input files and service connectivity
   - Sends text to service via HTTP POST requests
   - Processes 3 files in parallel using `ThreadPoolExecutor`
   - Creates output files with `_pronouns.txt` suffix

### Dual-Mode Resolution

**Simple Mode** (default):
- `PronounResolver` class in service uses spaCy's NER and dependency parser
- Algorithm: Find pronouns → Look backward for nearest noun → Replace pronoun with noun
- Also handles corporate noun phrases ("The company" / "The firm" etc. → main entity)
- Skips first-person pronouns (we/our/us) inside quoted regions (they refer to the speaker)
- Backward scan prefers the main (first ORG) entity over subsidiary entities at equal score
- No additional dependencies beyond spacy/flask

**Full Mode** (optional):
- `FastCorefResolver` class uses fastcoref transformer model
- Handles all coreferences: pronouns AND noun phrases
- Requires ~2GB additional dependencies (PyTorch, transformer models)
- More accurate but slower, requires explicit installation and configuration

Both resolvers share the same interface: `resolve_text(text) -> str`

## Installation & Setup

### Prerequisites
- Python 3.8+
- pip (Python package manager)

### Core Installation (Simple Mode)
```bash
pip3 install --break-system-packages spacy flask requests
python3 -m spacy download en_core_web_sm --break-system-packages
```

Verify:
```bash
python3 -c "import spacy; nlp = spacy.load('en_core_web_sm'); print('✓ Model ready')"
```

### Optional: Full Mode Support

**CRITICAL:** `fastcoref 2.1.6` is incompatible with `transformers >= 4.44`.
Pin transformers to 4.39.3 after installing fastcoref:

```bash
pip3 install --break-system-packages fastcoref
pip3 install --break-system-packages "transformers==4.39.3"
```

Verify (import alone is not enough — the model must actually load):
```bash
python3 -c "from fastcoref import FCoref; m = FCoref(); print('✓ fastcoref ready')"
```

## Running the System

### Quick Start

**Terminal 1: Start service**
```bash
python3 pronounCer_service.py
```

**Terminal 2: Process files (simple mode - default)**
```bash
python3 pronounCer.py --inputs FEED_28-jan-2026
```

**Or with full mode (requires fastcoref)**
```bash
python3 pronounCer.py --inputs FEED_28-jan-2026 --mode full
```

### Running Service in Background

```bash
# With nohup
nohup python3 pronounCer_service.py > service.log 2>&1 &

# With screen
screen -S pronouncer -d -m python3 pronounCer_service.py

# With tmux
tmux new-session -d -s pronouncer "python3 pronounCer_service.py"
```

## File Structure

### Core Implementation
- **pronounCer_service.py**: Flask service (~950 lines)
  - `PronounResolver`: Simple pronoun + corporate-phrase resolution (lines 61-242)
  - Helper functions for FastCoref (overlap removal, canonical selection, etc.) (lines 243-541)
  - `FastCorefResolver`: Full coreference resolution (lines 542-781)
  - `initialize_model()`: Model loading and resolver setup (lines 782-827)
  - HTTP endpoints: `/health`, `/resolve`, `/config`, `/` (lines 828-924)
  - Main entry point (lines 925-949)

- **pronounCer.py**: Client script (~330 lines)
  - `check_service_running()`: Health check (lines 52-63)
  - `configure_service(mode)`: Set resolver mode (lines 66-95)
  - `process_file()`: Single file handler with parallel support (lines 98-213)
  - `validate_inputs()`: Verify 3 input files exist (lines 216-232)
  - `main()`: Orchestration and argument parsing (lines 235-331)

### Input/Output Files
- **Format**: `{base}_{headline|summary|content}.txt` (input) → `{base}_{headline|summary|content}_pronouns.txt` (output)
- **Example**: `FEED_28-jan-2026_headline.txt` → `FEED_28-jan-2026_headline_pronouns.txt`

### Test Data
- `FEED_28-jan-2026_*.txt`: Sample inputs for manual testing (3 files)
- `FEED_28-jan-2026_*_pronouns.txt`: Expected outputs after processing (3 files)

### Documentation
- **README.md**: Complete usage guide, API docs, troubleshooting, performance metrics
- **IMPLEMENTATION_SUMMARY.md**: Technical implementation details
- **QUICK_START.md**: Getting started guide
- **API_TESTING_GUIDE.md**: Curl examples for manual API testing

## Common Development Tasks

### Start Service for Development
```bash
python3 pronounCer_service.py
```
Logs show model loading progress and HTTP startup. Output: `Starting Flask server on http://localhost:5050`

### Test with Sample Data
```bash
python3 pronounCer.py --inputs FEED_28-jan-2026
```
Processes 3 files in parallel, creates `*_pronouns.txt` outputs

### Manual API Testing

Health check:
```bash
curl http://localhost:5050/health
```

Configure mode:
```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "simple"}'
```

Test resolution:
```bash
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "Apple released a product. It was successful."}'
```

### Check Service Status
```bash
ps aux | grep pronounCer_service
curl http://localhost:5050/
```

### Debug Client Issues

Run with strace to see HTTP requests:
```bash
python3 pronounCer.py --inputs FEED_28-jan-2026
```

Check file permissions:
```bash
ls -la FEED_28-jan-2026_*.txt
```

### Stop Service

Foreground (Ctrl+C):
```
Ctrl+C
```

Background:
```bash
pkill -f pronounCer_service.py
screen -X -S pronouncer quit
tmux kill-session -t pronouncer
```

## Code Patterns

### Text Processing Pipeline
1. **spaCy tokenization**: `nlp(text)` returns doc with tokens, POS tags, entities
2. **Token-based replacement**: Build `replacements` dict (token index → text), then reconstruct via token iteration preserving whitespace
3. **No regex on text**: Avoids substring issues, uses token boundaries instead

### Service Error Handling
- All endpoints return JSON with `"status"` field ("success" or "error")
- Service gracefully falls back to simple mode if full mode unavailable
- HTTP 400 for bad request, 500 for processing errors

### Client Parallel Processing
- `ThreadPoolExecutor(max_workers=3)` for the 3 fixed file types
- `as_completed()` to handle variable completion times
- Tracks success/failure per file in results list

## Troubleshooting

### Service not responding
```bash
curl http://localhost:5050/health
# Expected: {"status": "healthy"}
```

### Model loading fails
```bash
python3 -m spacy download en_core_web_sm --break-system-packages
```

### Missing input files
```bash
ls FEED_28-jan-2026_*.txt
# Should show: headline.txt, summary.txt, content.txt
```

### fastcoref installation issues
- ~2GB disk space required
- PyTorch installation may take several minutes
- On failure, simple mode works without fastcoref

### fastcoref silently falls back to simple mode
This is the most common failure. Symptoms: you pass `--mode full` but the log
shows `Service configured to 'simple' mode`, and noun-phrase replacements are missing.

**Root cause:** `fastcoref` import succeeds but `FCoref()` crashes at runtime.
The service catches the exception and falls back silently. The client warning
checks `fastcoref_available` (set from the import) rather than the actual mode
returned — so no warning fires.

**Known crashes and fixes:**
| Error | Cause | Fix |
|---|---|---|
| `'FCorefModel' has no attribute 'all_tied_weights_keys'` | transformers ≥ 5.0 | `pip3 install "transformers==4.39.3"` |
| `RobertaModel does not support scaled_dot_product_attention` | transformers 4.40–4.44 + torch 2.10 | Same — pin to 4.39.3 |

**Diagnostic:** Always verify with model instantiation, not just import:
```bash
python3 -c "from fastcoref import FCoref; FCoref(); print('OK')"
```

### Full mode slower than expected
- First request to full mode loads ~1.5GB into memory
- Subsequent requests are faster (model stays loaded)
- Check available memory: `free -h`

## Design Decisions

**Two-Component Architecture**
- Service eliminates model loading overhead on each run
- Trade-off: Requires keeping process in background
- Benefit: ~75-80% faster for repeated processing

**HTTP-Based Communication**
- Decouples service from client (could run on different machines)
- Minimal overhead for local requests
- Simple request/response handling

**Dual-Mode System**
- Simple mode for speed and minimal dependencies
- Full mode for accuracy when needed
- Runtime configuration via `/config` endpoint allows switching modes without restart

**Token-Based Replacement**
- Respects word boundaries and whitespace
- Avoids substring replacement bugs (e.g., "his" matching inside "this")
- Required by spaCy's token architecture

## Future Improvements

1. **Fallback Strategy**: Automatic retry to simple mode if full mode fails
2. **Configuration File**: Support custom ports, default mode, logging options
3. **Batch Processing**: Accept multiple texts in single request
4. **Caching**: Memoize results for identical inputs
5. **Production Ready**: Use gunicorn instead of Flask dev server, structured logging
6. **Performance**: GPU acceleration for fastcoref, streaming for large documents
7. **Multi-language**: Language detection and model selection
