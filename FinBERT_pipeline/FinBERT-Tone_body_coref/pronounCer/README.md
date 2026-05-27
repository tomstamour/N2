# pronounCer: Pronoun and Coreference Resolution System

A high-performance pronoun and coreference resolution system built with a persistent background service and lightweight client script.

## System Architecture

pronounCer uses a **two-component architecture**:

1. **Service** (`pronounCer_service.py`): A persistent Flask-based HTTP service that keeps the NLP model loaded in memory
2. **Client** (`pronounCer.py`): A command-line tool that sends text to the service for analysis

This architecture eliminates the 1-3 second model loading overhead on each run, making it ideal for frequent use.

## Installation

### Prerequisites
- Python 3.8+
- pip (Python package manager)

### Step 1: Install Core Dependencies

```bash
pip3 install --break-system-packages spacy flask requests
```

### Step 2: Download spaCy Model

```bash
python3 -m spacy download en_core_web_sm --break-system-packages
```

Verify the model loads:
```bash
python3 -c "import spacy; nlp = spacy.load('en_core_web_sm'); print('✓ Model ready')"
```

### Step 3: Optional - Install fastcoref for Full Coreference Mode

For enhanced coreference resolution that handles both pronouns and definite noun phrases (e.g., "the company" → "ENvue Medical"):

```bash
pip3 install --break-system-packages fastcoref
```

**Note**: This will download ~2GB of dependencies including PyTorch and transformer models. Installation takes 5-10 minutes depending on network speed.

Verify installation:
```bash
python3 -c "from fastcoref import FCoref; print('✓ fastcoref installed')"
```

If fastcoref is not installed, the system will automatically fall back to simple mode.

## Quick Start

### Terminal 1: Start the Service

```bash
cd /home/tom/Documents/ibkr_scripts/N1/scripts/pronounCer
python3 pronounCer_service.py
```

Expected output:
```
2026-01-30 13:18:36,957 - INFO - Starting pronounCer Service...
2026-01-30 13:18:36,957 - INFO - Loading spaCy model: en_core_web_sm...
2026-01-30 13:18:37,247 - INFO - Model loaded successfully!
2026-01-30 13:18:37,247 - INFO - Starting Flask server on http://localhost:5050
2026-01-30 13:18:37,247 - INFO - Press Ctrl+C to stop the service
```

The service will keep running. You can leave this terminal open or run it in the background.

### Terminal 2: Run the Client (Simple Mode - Default)

```bash
cd /home/tom/Documents/ibkr_scripts/N1/scripts/pronounCer
python3 pronounCer.py --inputs FEED_28-jan-2026
```

Expected output:
```
2026-01-30 13:18:41,865 - INFO - pronounCer Client - Processing: FEED_28-jan-2026
2026-01-30 13:18:41,867 - INFO - Resolution mode: simple
2026-01-30 13:18:41,867 - INFO - Service health check: OK
2026-01-30 13:18:41,868 - INFO - Service configured to 'simple' mode
2026-01-30 13:18:41,867 - INFO - Input files validated
2026-01-30 13:18:41,867 - INFO - Processing 3 files in parallel...
2026-01-30 13:18:41,887 - INFO - ✓ headline: Processed headline: 91 chars
2026-01-30 13:18:41,888 - INFO - ✓ summary: Processed summary: 136 chars
2026-01-30 13:18:41,926 - INFO - ✓ content: Processed content: 1984 chars

Results: 3 succeeded, 0 failed
All files processed successfully!
```

## Usage

### Dual-Mode Architecture

pronounCer supports two resolution modes:

#### Simple Mode (Default)
- Resolves pronouns only (he, she, it, they, we, etc.)
- Fast and lightweight
- No heavy dependencies required
- Typical processing: 0.3-0.5 seconds per file

#### Full Mode
- Resolves all coreferences (pronouns + definite noun phrases)
- Handles "the company" → "ENvue Medical" transformations
- Requires fastcoref installation
- Typical processing: 1-2 seconds per file

### Basic Usage

```bash
python3 pronounCer.py --inputs <base_path> [--mode simple|full]
```

- `<base_path>`: Base filename without suffix
- `--mode`: Resolution mode (default: simple)

### Examples

**Simple Mode (Pronouns Only - Default)**:
```bash
python3 pronounCer.py --inputs FEED_28-jan-2026
# OR explicitly:
python3 pronounCer.py --inputs FEED_28-jan-2026 --mode simple
```

**Full Mode (All Coreferences - Requires fastcoref)**:
```bash
python3 pronounCer.py --inputs FEED_28-jan-2026 --mode full
```

Process files with full path:
```bash
python3 pronounCer.py --inputs /path/to/article --mode full
```

### Input Files

The client expects 3 input files with these suffixes:
- `{base}_headline.txt`
- `{base}_summary.txt`
- `{base}_content.txt`

Example with base path `FEED_28-jan-2026`:
- `FEED_28-jan-2026_headline.txt`
- `FEED_28-jan-2026_summary.txt`
- `FEED_28-jan-2026_content.txt`

### Output Files

For each input file, an output file is created with `_pronouns.txt` suffix:
- `{base}_headline_pronouns.txt`
- `{base}_summary_pronouns.txt`
- `{base}_content_pronouns.txt`

Example output filenames:
- `FEED_28-jan-2026_headline_pronouns.txt`
- `FEED_28-jan-2026_summary_pronouns.txt`
- `FEED_28-jan-2026_content_pronouns.txt`

## Service API

The service exposes a simple HTTP API:

### Health Check

```bash
curl http://localhost:5050/health
```

Response:
```json
{"status": "healthy"}
```

### Configure Resolution Mode

```bash
curl -X POST http://localhost:5050/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "full"}'
```

Response:
```json
{
  "mode": "full",
  "fastcoref_available": true,
  "status": "success"
}
```

### Resolve Pronouns and Coreferences

**Simple Mode (Pronouns Only)**:
```bash
curl -X POST http://localhost:5050/config -H "Content-Type: application/json" -d '{"mode": "simple"}'
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. The company beat expectations."}'
```

Response:
```json
{
  "resolved_text": "ENvue Medical announced earnings. The company beat expectations.",
  "mode": "simple",
  "status": "success"
}
```

**Full Mode (All Coreferences)**:
```bash
curl -X POST http://localhost:5050/config -H "Content-Type: application/json" -d '{"mode": "full"}'
curl -X POST http://localhost:5050/resolve \
  -H "Content-Type: application/json" \
  -d '{"text": "ENvue Medical announced earnings. The company beat expectations."}'
```

Response:
```json
{
  "resolved_text": "ENvue Medical announced earnings. ENvue Medical beat expectations.",
  "mode": "full",
  "status": "success"
}
```

### Service Information

```bash
curl http://localhost:5050/
```

Response includes current mode and available endpoints:
```json
{
  "service": "pronounCer Service",
  "version": "2.0",
  "current_mode": "simple",
  "fastcoref_available": true,
  "endpoints": {
    "GET /health": "Health check",
    "POST /resolve": "Resolve pronouns/coreferences",
    "POST /config": "Configure resolution mode",
    "GET /": "This information"
  }
}
```

## How It Works

### Simple Mode: PronounResolver Algorithm

The simple resolver uses spaCy's built-in NLP capabilities to:

1. **Parse Text**: Uses spaCy's dependency parser and NER (Named Entity Recognition)
2. **Identify Pronouns**: Finds all pronouns (he, she, it, they, their, etc.)
3. **Find Antecedents**: Looks backward to find the nearest noun/entity
4. **Replace**: Replaces pronouns with their antecedent text

Example (Simple Mode):
```
Input:  "ENvue Medical announced earnings. It beat expectations."
Output: "ENvue Medical announced earnings. ENvue Medical beat expectations."
```

**Limitation**: Does not handle definite noun phrases like "the company" or "the firm"

Example (Simple Mode Limitation):
```
Input:  "ENvue Medical announced earnings. The company beat expectations."
Output: "ENvue Medical announced earnings. The company beat expectations."
         ↑ No change - "The company" is not a pronoun
```

### Full Mode: FastCoref Algorithm

The full resolver uses fastcoref, a state-of-the-art coreference resolution model:

1. **Identify All Mentions**: Finds pronouns AND noun phrases that refer to same entity
2. **Build Coreference Clusters**: Groups all mentions that refer to the same entity
3. **Select Canonical Form**: Uses first mention as canonical (usually the full name)
4. **Replace**: Replaces all other mentions with the canonical form

Example (Full Mode):
```
Input:  "ENvue Medical announced earnings. The company beat expectations."
Output: "ENvue Medical announced earnings. ENvue Medical beat expectations."
         ↑ "The company" correctly resolved to "ENvue Medical"
```

**Advantages**:
- Resolves complex coreference chains
- Handles definite noun phrases ("the company", "the firm")
- Uses transformer-based neural models for high accuracy
- Handles ambiguous references intelligently

**Trade-off**: Slower processing (1-2 sec/file vs 0.3-0.5 sec/file)

### Performance Benefits

**Model Loading**: Only happens once when the service starts (~1-2 seconds)
**Subsequent Requests**: Fast processing with no model loading overhead

Typical timings:
- First client run: ~0.5-1 second (service already running)
- Subsequent runs: ~0.3-0.5 seconds per file
- Processing all 3 files in parallel: ~0.5-1 second total

### Parallel Processing

The client processes all 3 input files **concurrently** using Python's `ThreadPoolExecutor`:
- Each file is processed in parallel
- Results are aggregated when all complete
- Much faster than sequential processing

## Troubleshooting

### "Service not running" Error

If you see this error when running the client:
```
2026-01-30 13:18:41,867 - ERROR - pronounCer service is not running!
```

**Solution**: Start the service in another terminal:
```bash
python3 pronounCer_service.py
```

### "Model not found" Error

If you see:
```
OSError: [E050] Can't find model 'en_core_web_sm'
```

**Solution**: Download the spaCy model:
```bash
python3 -m spacy download en_core_web_sm --break-system-packages
```

### "Input file not found" Error

If you see:
```
ERROR - Missing input files:
  - FEED_28-jan-2026_headline.txt
```

**Solution**: Check that all 3 input files exist in the current directory:
```bash
ls FEED_28-jan-2026_*.txt
```

### Service Crashes on Startup

**Solution**: Try running with verbose logging:
```bash
python3 pronounCer_service.py 2>&1 | head -20
```

Check that Flask and spaCy are properly installed:
```bash
python3 -c "import flask; import spacy; print('✓ OK')"
```

## Running in Background

### Using `screen` (Recommended)

```bash
# Start a new screen session
screen -S pronouncer

# Inside the screen session, start the service
python3 pronounCer_service.py

# Detach the screen (Ctrl+A then D)
# Ctrl+A then D

# Later, reattach to the service
screen -r pronouncer

# Kill the session when done
screen -X -S pronouncer quit
```

### Using `tmux`

```bash
# Start a new tmux session
tmux new-session -d -s pronouncer "cd /path/to/pronounCer && python3 pronounCer_service.py"

# Run the client
python3 pronounCer.py --inputs FEED_28-jan-2026

# Kill the service later
tmux kill-session -t pronouncer
```

### Using `nohup`

```bash
nohup python3 pronounCer_service.py > service.log 2>&1 &

# Check status
ps aux | grep pronounCer_service

# Later stop it
pkill -f pronounCer_service.py
```

## Development

### Files Structure

```
pronounCer/
├── README.md                          # This file
├── pronounCer_service.py              # Background service
├── pronounCer.py                      # Client script
├── FEED_28-jan-2026_headline.txt      # Sample input
├── FEED_28-jan-2026_summary.txt       # Sample input
├── FEED_28-jan-2026_content.txt       # Sample input
├── FEED_28-jan-2026_headline_pronouns.txt    # Sample output
├── FEED_28-jan-2026_summary_pronouns.txt     # Sample output
└── FEED_28-jan-2026_content_pronouns.txt     # Sample output
```

### Service Code

The service (`pronounCer_service.py`) contains:
- **Flask Application**: HTTP server on port 5050
- **PronounResolver Class**: Implements the pronoun resolution algorithm
- **Health Check Endpoint**: `/health` for service status
- **Resolve Endpoint**: `/resolve` for processing text
- **Error Handling**: Graceful error responses with informative messages

Key components:
- `initialize_model()`: Loads spaCy model at startup
- `resolve_coreferences()`: Main HTTP endpoint handler
- `PronounResolver.resolve_text()`: Core resolution algorithm
- `PronounResolver._find_nearest_noun()`: Finds pronoun antecedents

### Client Code

The client (`pronounCer.py`) contains:
- **Argument Parsing**: Handle `--inputs` parameter
- **Service Health Check**: Verify service is running before processing
- **Input Validation**: Check all required files exist
- **Parallel Processing**: Use ThreadPoolExecutor for concurrent file processing
- **Error Handling**: Comprehensive error messages

Key functions:
- `check_service_running()`: Verify service is accessible
- `process_file()`: Handle single file processing
- `validate_inputs()`: Check input file existence
- `main()`: Main entry point with orchestration

## Performance Metrics

### Example Run

```bash
$ time python3 pronounCer.py --inputs FEED_28-jan-2026

2026-01-30 13:18:41,865 - INFO - pronounCer Client - Processing: FEED_28-jan-2026
2026-01-30 13:18:41,867 - INFO - Service health check: OK
2026-01-30 13:18:41,867 - INFO - Input files validated
2026-01-30 13:18:41,867 - INFO - Processing 3 files in parallel...
2026-01-30 13:18:41,887 - INFO - ✓ headline: Processed headline: 91 chars
2026-01-30 13:18:41,888 - INFO - ✓ summary: Processed summary: 136 chars
2026-01-30 13:18:41,926 - INFO - ✓ content: Processed content: 1984 chars

Results: 3 succeeded, 0 failed
All files processed successfully!

real    0m0.093s
user    0m0.053s
sys     0m0.035s
```

### Comparison

**Without Service (Direct Approach)**:
- Each run: 2-3 seconds (model loading included)
- Total for 10 runs: 20-30 seconds

**With Service (Current System)**:
- Service startup: 1-2 seconds (one time)
- Each client run: 0.1-0.5 seconds
- Total for 10 runs: ~5 seconds

**Improvement**: 75-80% faster on repeated runs

## Limitations and Future Improvements

### Current Limitations

**Simple Mode**:
- Only resolves pronouns (not definite noun phrases)
- Uses heuristic matching (nearest noun)
- May fail on complex/ambiguous references

**Full Mode**:
- Slower processing (1-2 seconds per file)
- Requires ~2GB of additional dependencies
- Memory intensive (1.5GB+ when running)

### Trade-offs by Mode

| Feature | Simple | Full |
|---------|--------|------|
| Pronoun resolution | ✅ Yes | ✅ Yes |
| Definite noun phrases | ❌ No | ✅ Yes |
| Processing speed | Fast (0.3-0.5s) | Slower (1-2s) |
| Memory usage | ~200MB | ~1.5GB |
| Dependencies | ~50MB | ~2GB |
| Accuracy | Good | Excellent |

### Future Enhancements
1. **Fallback Strategy**: Automatic fallback to simple mode if full mode fails
2. **Configuration**: Support for custom ports, model selection, etc.
3. **Logging**: Optional file-based logging for production use
4. **Metrics**: Track processing time, success rates, error types
5. **Batch API**: Support for processing multiple files in single request
6. **Caching**: Cache results for repeated texts
7. **GPU Support**: CUDA acceleration for faster transformer inference
8. **Multi-language**: Support for other languages beyond English

## License

Part of the IBKR Scripts project.

## Support

For issues or questions:
1. Check the Troubleshooting section
2. Review the service and client logs
3. Verify all dependencies are installed correctly
4. Test the service health endpoint manually

Happy pronoun resolving!
