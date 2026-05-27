# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**jsonDetector** is a real-time JSON file monitoring utility that continuously watches a directory for new JSON files and executes a specified script when files are detected. It tracks processed files to prevent duplicate executions (configurable), integrating seamlessly with the broader IBKR trading scripts ecosystem.

**Primary Use Case:** Monitor output directories from tools like NewsWatcher, automatically trigger processing scripts when new JSON data arrives, maintain audit logs of processing activity.

**Secondary Uses:**
- Real-time integration between multiple scripts in a data pipeline
- Event-driven automation for trading signal processing
- Audit trail of file processing with state persistence across restarts

## Architecture & Key Components

### Core Classes (jsonDetector.py)

1. **StateManager**
   - Tracks processed JSON filenames to prevent duplicate executions
   - Atomic writes using temp file + `os.replace()` pattern (safe for concurrent access)
   - File locking with `fcntl.flock()` to prevent corruption on Unix systems
   - Persistent state file format: `processed_files.json`
   - Graceful recovery from corrupted state files (starts fresh with warning)

2. **ScriptExecutor**
   - Executes target scripts via `subprocess.run()`
   - Passes JSON file absolute path as first command-line argument
   - Captures stdout/stderr and logs output
   - Configurable timeout (default 300s) with automatic process killing
   - Handles edge cases: missing files, non-zero exits, timeouts

3. **JSONFileEventHandler** (watchdog.events.FileSystemEventHandler)
   - Receives file system events from watchdog Observer
   - Filters for `.json` file creation events only (ignores directories)
   - Invokes callback immediately upon detection

4. **JSONDetector** (Main Orchestrator)
   - Initializes and manages all components
   - Configures dual logging: console (INFO) + file (DEBUG)
   - Sets up watchdog Observer for non-recursive directory monitoring
   - Manages processed files state with atomic updates
   - Implements `--multiple-trigger` logic for flexible execution modes
   - Graceful shutdown with signal handlers (SIGINT, SIGTERM)
   - Pre-flight validation of paths, permissions, and configuration

### Data Flow

```
File System Event (JSON created)
       ↓
watchdog Observer detects event
       ↓
JSONFileEventHandler.on_created()
       ↓
JSONDetector.on_json_detected()
       ├→ Check if already processed (unless --multiple-trigger YES)
       ├→ ScriptExecutor.execute(json_file_path)
       ├→ Log execution result
       └→ StateManager.mark_processed()
          ↓
      processed_files.json (atomically updated)
```

## Running the Application

### Standard Execution (Single Trigger Per File)

```bash
python3 jsonDetector.py \
    --watch-directory /path/to/watch \
    --script-to-launch /path/to/handler.sh
```

This monitors `/path/to/watch` for new JSON files and executes `/path/to/handler.sh` once per unique filename.

### Allow Multiple Triggers (Same Filename Processed Multiple Times)

```bash
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/handler.sh \
    --multiple-trigger YES
```

With `YES`, a file created, deleted, and recreated with the same name will trigger execution both times.

### With Custom Timeout and Directories

```bash
python3 jsonDetector.py \
    --watch-directory ~/data/incoming \
    --script-to-launch ./process_json.py \
    --state-file ~/data/.detector_state.json \
    --log-dir ~/logs/detector \
    --timeout 60
```

### Integration with NewsWatcher (Typical Workflow)

```bash
# In one terminal: Run NewsWatcher
python3 ../newsWatcher/NewsWatcher.py \
    --input-table ntt-test.tsv \
    --temporary-list temp_watchlist.json \
    --output-dir outputs \
    --api-keys alpaca_API-Keys.txt \
    --log-dir logs

# In another terminal: Run jsonDetector to process NewsWatcher output
python3 jsonDetector.py \
    --watch-directory ../newsWatcher/outputs \
    --script-to-launch ./analyze_news.py \
    --multiple-trigger NO
```

Each news JSON file from NewsWatcher automatically triggers your analysis script.

### Command-Line Arguments

- `--watch-directory` (required): Directory to monitor for new JSON files
  - Must exist and be readable before startup
  - Only monitors immediate directory (non-recursive)
  - Creates if doesn't exist: NO (fails validation)

- `--script-to-launch` (required): Script to execute on JSON detection
  - Must exist, be a file, and be executable before startup
  - Receives JSON file absolute path as first argument: `script.sh /path/to/file.json`
  - Runs in subprocess (stdout/stderr captured and logged)
  - Exit code 0 = success; non-zero = failure (logged as WARNING, processing continues)

- `--multiple-trigger` (default: NO): Allow multiple triggers for same filename
  - `NO`: Track processed filenames, skip if already seen (prevents duplicates)
  - `YES`: Ignore state tracking, always execute (useful for re-processing or testing)
  - State still persists when `YES` (for reference), just not checked

- `--state-file` (default: `processed_files.json`): JSON file for state persistence
  - Stores processed filenames (basenames only, not full paths)
  - Auto-created on first run if parent directory exists
  - Format includes: `processed_files`, `last_updated` (ISO timestamp), `total_processed`
  - Can be deleted to reset and re-process all files

- `--log-dir` (default: `logs`): Directory for log files
  - Auto-created if doesn't exist
  - Daily log files: `jsonDetector_YYYY-MM-DD.log`
  - Dual output: file (DEBUG) + console (INFO)

- `--timeout` (default: 300): Script execution timeout in seconds
  - If script takes longer, process is killed
  - Logged as ERROR, file still marked as processed (prevents infinite retries)
  - Set higher for long-running scripts (e.g., 3600 for hourly processing)

## Dependencies & Environment

**Python Version:** 3.8+ (3.10+ recommended)

**Required Package:**
```bash
pip install watchdog
```

This provides `watchdog.observers.Observer` for cross-platform file system event detection.

**Optional Packages:**
- None (uses only Python standard library + watchdog)

**Unix-Only Considerations:**
- File locking uses `fcntl.flock()` - Unix/Linux/macOS only
- Windows users can run but will not have file locking protection (risk of concurrent corruption)
- Consider using a network file system with proper locking on shared drives

## State File Format

**processed_files.json** (example):
```json
{
  "processed_files": [
    "NVDA_26-jan-2026.json",
    "TSLA_26-jan-2026.json"
  ],
  "last_updated": "2026-01-27T10:35:42.123456",
  "total_processed": 2
}
```

**Behavior:**
- `processed_files`: Sorted list of all processed JSON basenames
- `last_updated`: ISO format timestamp of last state update
- `total_processed`: Total count of unique files processed

**Reset Processing:**
```bash
# Delete state file to re-process all files
rm processed_files.json

# Or manually edit to remove specific files
# Then restart jsonDetector
```

## Validation & Error Handling

### Pre-Flight Validation (Fail Fast)

Validated before monitoring starts:
1. **Watch directory:** exists, is a directory, is readable
2. **Script:** exists, is a file, is executable (`os.access(..., os.X_OK)`)
3. **State file directory:** exists/creatable, writable if exists
4. **Log directory:** exists/creatable, writable

All validation errors printed to stderr with EXIT 1. Example:
```
ERROR: Script is not executable: /tmp/nonexistent.sh
ERROR: Watch directory does not exist: /invalid/path
```

### Runtime Error Handling

**Script Timeout:** If script runs > timeout seconds
- Process is killed
- Logged as ERROR
- File still marked as processed (avoids infinite retry loop)
- Monitor continues

**Script Crash (Non-zero Exit):** If script exits with code != 0
- Logged as WARNING with exit code
- stdout/stderr captured and logged (first 500 chars)
- File still marked as processed
- Monitor continues

**File Disappeared:** If JSON file is deleted before execution
- Logged as WARNING
- File NOT marked as processed (may retry if recreated)
- Monitor continues

**State File Corruption:** If JSON state file is malformed
- Logged as WARNING
- Starts fresh with empty set
- Previous state lost, but detector continues normally
- Useful for recovery from concurrent access issues

**Permission Errors:** If log directory or state file becomes unwritable
- Logged as ERROR
- Monitor continues (logging to console only)
- Ensure proper permissions before running

## Logging

**Location:** `logs/jsonDetector_YYYY-MM-DD.log`

**Dual Output:**
- **File:** DEBUG level (all details)
- **Console:** INFO level (key events only)

**Key Log Messages:**

| Level | Message | Meaning |
|-------|---------|---------|
| INFO | `jsonDetector logging initialized` | Setup complete |
| INFO | `Starting JSON detector` | Monitor starting with configuration |
| INFO | `Starting file system observer` | Watchdog Observer activated |
| INFO | `JSON file detected: {path}` | File creation detected (DEBUG level) |
| INFO | `Processing JSON file: {name}` | Execution starting |
| INFO | `Successfully processed: {name}` | Execution completed, file marked processed |
| INFO | `Skipping already-processed file: {name}` | File seen before (--multiple-trigger NO) |
| WARNING | `Script exited with code {N}: {path}` | Non-zero exit code |
| WARNING | `JSON file disappeared before execution: {path}` | File deleted before script could run |
| ERROR | `Script execution timeout ({N}s): {path}` | Exceeded timeout limit, process killed |
| ERROR | `Failed to process {name}: {reason}` | Execution failed |
| ERROR | `Error saving state file: {error}` | State persistence issue |
| INFO | `Received signal {N}, initiating graceful shutdown` | Ctrl+C pressed |
| INFO | `Processed files: {count}` | Final statistics on shutdown |
| INFO | `jsonDetector shutdown complete` | Shutdown finished cleanly |

## Graceful Shutdown

Press **Ctrl+C** to trigger graceful shutdown:
1. Signal handler sets `shutdown_event` flag
2. Main loop detects flag and stops
3. Stop watchdog Observer (waits up to 5 seconds)
4. Save final state (if modified)
5. Log shutdown statistics
6. Clean exit (no stack trace)

**During shutdown:** Any JSON files detected are ignored (logged as DEBUG).

## Known Limitations & Critical Warnings

### CRITICAL: Infinite Loop Risk

**If your executed script writes JSON files to the watched directory, you will create an infinite loop.**

Example of problematic setup:
```bash
python3 jsonDetector.py \
    --watch-directory ./outputs \
    --script-to-launch ./transform_and_save.py
```

If `transform_and_save.py` reads a JSON from `./outputs/` and writes a new JSON back to `./outputs/`, infinite loop occurs:
```
input.json → transform_and_save.py → output.json (detected!) → transform_and_save.py → output2.json (detected!) → ...
```

**Safe patterns:**
1. Write to different directory: `handler.sh` writes to `./processed/` not `./incoming/`
2. Use subdirectories: Monitor `./incoming/`, write to `./incoming/processed/`
3. Use file renaming: Rename processed files `.json.done` instead of recreating

### Unix-Only File Locking

- `fcntl.flock()` is Unix/Linux/macOS only
- Windows users: No file locking, risk of corruption if multiple instances run concurrently
- Use process locks (mutex files) or network file system for multi-process scenarios

### No Automatic Retry

- Failed executions are NOT retried
- File marked as processed after ANY execution attempt (success or failure)
- Prevents infinite retries on broken scripts
- For retry logic, implement in your handler script or use external job queue

### Basename Tracking

- State file stores only filename basenames, not full paths
- Two JSON files with same name in different directories would conflict
- Assumes watch directory contains unique filenames (typical for dated outputs)

### Non-Recursive Monitoring

- Only watches immediate directory specified in `--watch-directory`
- Subdirectories NOT monitored
- Design your data pipeline accordingly

### Race Conditions in YES Mode

- With `--multiple-trigger YES`, rapid file create/delete/recreate may trigger multiple times
- Watchdog buffer may combine events
- No deduplication window (immediate processing)
- Use for testing/one-off processing, not production bulk operations

## Testing & Verification

### Basic Test Setup

```bash
# 1. Install dependencies
pip install watchdog

# 2. Create test handler script
cat > /tmp/test_handler.sh << 'EOF'
#!/bin/bash
echo "Processing JSON file: $1"
cat "$1" | head -5
EOF
chmod +x /tmp/test_handler.sh

# 3. Create test directory
mkdir -p /tmp/test_watch

# 4. Verify script is executable
ls -l /tmp/test_handler.sh  # Should show 'x' permission

# 5. Start detector in one terminal
cd /home/tom/Documents/ibkr_scripts/N1/scripts/jsonDetector
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/test_handler.sh
```

### Test 1: Basic Detection (NO Duplicate Trigger)

```bash
# In second terminal, while detector is running:

# Create first JSON file
echo '{"symbol": "NVDA", "price": 120.5}' > /tmp/test_watch/test1.json
sleep 1

# Create second JSON file
echo '{"symbol": "TSLA", "price": 245.3}' > /tmp/test_watch/test2.json
sleep 1

# Try creating same filename again (should skip)
rm /tmp/test_watch/test1.json
echo '{"symbol": "NVDA", "price": 121.0}' > /tmp/test_watch/test1.json
sleep 1

# Verify in logs:
# - Two successful executions (test1.json, test2.json)
# - Third creation skipped (already processed)
# - Check processed_files.json contains both filenames

cat processed_files.json
# Expected: ["test1.json", "test2.json"]
```

### Test 2: Multiple Trigger Mode (YES)

```bash
# Stop previous detector (Ctrl+C)

# Restart with --multiple-trigger YES
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/test_handler.sh \
    --multiple-trigger YES

# Clear old test files
rm /tmp/test_watch/*.json

# In second terminal:
echo '{"test": 1}' > /tmp/test_watch/repeat.json
sleep 1
rm /tmp/test_watch/repeat.json
echo '{"test": 2}' > /tmp/test_watch/repeat.json
sleep 1

# Verify in logs:
# - Script executed BOTH times (even though same filename)
# - processed_files.json contains "repeat.json"
```

### Test 3: State Persistence Across Restarts

```bash
# 1. Start detector and create a file
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/test_handler.sh

# In second terminal:
echo '{"persist": 1}' > /tmp/test_watch/persist.json
sleep 2

# 2. Stop detector with Ctrl+C
# 3. Verify state file was created
cat processed_files.json  # Should contain "persist.json"

# 4. Restart detector
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/test_handler.sh

# 5. Create same file again
rm /tmp/test_watch/persist.json
echo '{"persist": 2}' > /tmp/test_watch/persist.json
sleep 2

# Verify in logs:
# - Script NOT executed (loaded from saved state)
# - Logged as "Skipping already-processed file"
```

### Test 4: Script Timeout

```bash
# Create slow script
cat > /tmp/slow_script.sh << 'EOF'
#!/bin/bash
echo "Starting slow operation..."
sleep 600  # 10 minutes
echo "Done"
EOF
chmod +x /tmp/slow_script.sh

# Start detector with 5 second timeout
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/slow_script.sh \
    --timeout 5

# In second terminal, create JSON file
echo '{"slow": 1}' > /tmp/test_watch/slow.json

# Verify in logs after 5 seconds:
# - "Script execution timeout (5s): /tmp/test_watch/slow.json"
# - File still marked as processed
# - Detector continues running
```

### Test 5: Script Failure (Non-zero Exit)

```bash
# Create failing script
cat > /tmp/fail_script.sh << 'EOF'
#!/bin/bash
echo "Processing file: $1" >&2
exit 1  # Non-zero exit
EOF
chmod +x /tmp/fail_script.sh

# Start detector
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/fail_script.sh

# In second terminal:
echo '{"fail": 1}' > /tmp/test_watch/fail.json
sleep 1

# Verify in logs:
# - "Script exited with code 1: /tmp/test_watch/fail.json" (WARNING)
# - File still marked as processed
# - Detector continues running
```

### Test 6: Validation Errors (Pre-Flight)

```bash
# Test 1: Non-existent watch directory
python3 jsonDetector.py \
    --watch-directory /invalid/path \
    --script-to-launch /tmp/test_handler.sh
# Expected: EXIT 1 with "Watch directory does not exist"

# Test 2: Non-executable script
echo '#!/bin/bash' > /tmp/noexec.sh
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/noexec.sh
# Expected: EXIT 1 with "Script is not executable"

# Test 3: Non-existent script
python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/doesnotexist.sh
# Expected: EXIT 1 with "Script does not exist"
```

### Test 7: Integration with NewsWatcher (Real-World)

```bash
# Assuming NewsWatcher is running and producing output files:
cd /home/tom/Documents/ibkr_scripts/N1/scripts/jsonDetector

# Create a simple analysis script
cat > analyze_news.py << 'EOF'
#!/usr/bin/env python3
import json
import sys

if len(sys.argv) < 2:
    sys.exit(1)

try:
    with open(sys.argv[1], 'r') as f:
        data = json.load(f)
    print(f"Analyzed: {sys.argv[1]}")
except Exception as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
EOF
chmod +x analyze_news.py

# Start detector on NewsWatcher output
python3 jsonDetector.py \
    --watch-directory ../newsWatcher/outputs \
    --script-to-launch ./analyze_news.py \
    --multiple-trigger NO

# In another terminal, generate news (or wait for actual news to arrive)
# Monitor logs to see JSON files being automatically processed
```

## Integration Patterns

### Pattern 1: Sequential Processing Pipeline

```
NewsWatcher → outputs/ → jsonDetector → analyze_news.py → results/
                                      → database_insert.py → database
```

Multiple detectors can watch the same directory with different scripts:
```bash
# Terminal 1: Analyze news
python3 jsonDetector.py \
    --watch-directory ../newsWatcher/outputs \
    --script-to-launch ./analyze_news.py

# Terminal 2: Store in database
python3 jsonDetector.py \
    --watch-directory ../newsWatcher/outputs \
    --script-to-launch ./store_news.py
```

### Pattern 2: Conditional Processing with Wrapper Script

Create a wrapper that decides which processor to run:

```bash
#!/bin/bash
JSON_FILE="$1"

# Read JSON and decide
if grep -q "URGENT" "$JSON_FILE"; then
    python3 urgent_processor.py "$JSON_FILE"
else
    python3 normal_processor.py "$JSON_FILE"
fi
```

### Pattern 3: Batch Processing with Output Queue

Handler script moves files to a queue:
```bash
#!/bin/bash
JSON_FILE="$1"
QUEUE_DIR="./processing_queue"
mkdir -p "$QUEUE_DIR"
cp "$JSON_FILE" "$QUEUE_DIR/"
# Batch processor picks up from queue
```

### Pattern 4: Error Handling with Retry Directory

Handler script moves failed files to retry:
```bash
#!/bin/bash
JSON_FILE="$1"

if ! python3 process.py "$JSON_FILE"; then
    mkdir -p ./retry
    cp "$JSON_FILE" ./retry/
    exit 1
fi
```

## Troubleshooting

### Issue: "Script is not executable"

**Cause:** Script missing executable bit
**Fix:**
```bash
chmod +x /path/to/script.sh
# Verify:
ls -l /path/to/script.sh  # Should show 'x' in permissions
```

### Issue: JSON files not detected

**Cause:** Watchdog may miss rapid file creation or network file system delays
**Fix:**
- Ensure watch directory is local (not network mount)
- Check logs for any error messages
- Verify `.json` extension (case-sensitive on Linux)
- Restart detector if files existed before starting

### Issue: "ERROR: Cannot create log directory"

**Cause:** Insufficient permissions
**Fix:**
```bash
# Check permissions on parent directory
ls -ld /path/to/log/parent

# Manually create and verify
mkdir -p /path/to/logs
chmod 755 /path/to/logs
```

### Issue: State file corruption errors

**Cause:** Multiple processes writing simultaneously or interrupted write
**Fix:**
```bash
# Delete corrupted state file
rm processed_files.json
# Restart detector (will recreate)
# Note: Files will be re-processed
```

### Issue: Script timeout not working

**Cause:** Timeout is in seconds, default is 300 (5 minutes)
**Fix:**
```bash
# Explicitly set shorter timeout
python3 jsonDetector.py \
    --watch-directory . \
    --script-to-launch ./handler.sh \
    --timeout 10
```

### Issue: Infinite loop of file processing

**Cause:** Your handler script writes JSON to watched directory
**Fix:**
- Have handler write to different directory: `./outputs/processed/`
- Or rename processed files: `file.json.done`
- Or write to parent directory: `../results/`

### Issue: "Watch directory does not exist"

**Cause:** Invalid path provided
**Fix:**
```bash
# Verify directory exists
ls -d /path/to/watch

# Or use absolute path instead of relative
pwd  # Get current directory
python3 jsonDetector.py --watch-directory $PWD/watch ...
```

## Performance Considerations

- **Single-threaded:** Files processed sequentially (ensures deterministic order)
- **Watchdog overhead:** ~1% CPU when idle, <5% while processing files
- **State file:** Atomic writes add ~5-10ms per processed file
- **Disk I/O:** Dominant factor for large JSON files (100MB+)
- **Network mounts:** Slowest (use local storage when possible)

**Optimization tips:**
- Use local SSD storage for watch directory
- Increase `--timeout` for large files (higher timeout = more lenient)
- Use `--multiple-trigger NO` (default) for production (less overhead)

## Version History

- **v1.0 (2026-01-27):** Initial implementation
  - Core file detection and script execution
  - State management with atomic writes
  - Dual logging (console + file)
  - Graceful shutdown with signal handlers
  - Pre-flight validation

## Success Criteria Checklist

After deployment, verify:

- [ ] Script detects new JSON files within 1 second of creation
- [ ] Script ignores non-JSON files (`.txt`, `.log`, etc.)
- [ ] Target script receives correct absolute path as first argument
- [ ] State file prevents duplicate executions (when `--multiple-trigger NO`)
- [ ] Multiple triggers work correctly (when `--multiple-trigger YES`)
- [ ] Graceful shutdown with Ctrl+C preserves state
- [ ] State persists across restarts (test by recreating same filename)
- [ ] Logs written to both console (INFO level) and file (DEBUG level)
- [ ] Log files created daily with correct format: `jsonDetector_YYYY-MM-DD.log`
- [ ] Multiple detector instances don't corrupt state (file locking works)
- [ ] Script timeout kills hanging processes
- [ ] Non-zero exit codes logged but don't stop monitoring
- [ ] File permissions validated at startup
- [ ] Pre-flight validation catches configuration errors with clear messages
- [ ] Integration with NewsWatcher or other tools works end-to-end

## Future Enhancements

Potential improvements for future versions:
- Watch multiple directories simultaneously
- Parallel execution of scripts (with --max-parallel flag)
- Conditional execution based on JSON content
- Retry logic with exponential backoff
- Metrics collection (files/min, avg execution time)
- Web dashboard for monitoring
- Integration with logging services (Syslog, CloudWatch)
