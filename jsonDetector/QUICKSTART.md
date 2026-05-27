# jsonDetector Quick Start Guide

## Installation

```bash
# Install required dependency
pip install watchdog
```

## Basic Usage

### 1. Create a test handler script

```bash
cat > /tmp/handler.sh << 'SCRIPT'
#!/bin/bash
echo "Received JSON file: $1"
echo "Content preview:"
head -3 "$1"
SCRIPT
chmod +x /tmp/handler.sh
```

### 2. Run jsonDetector

```bash
cd /home/tom/Documents/ibkr_scripts/N1/scripts/jsonDetector

python3 jsonDetector.py \
    --watch-directory /tmp/test_watch \
    --script-to-launch /tmp/handler.sh
```

### 3. Test in another terminal

```bash
# Create test directory
mkdir -p /tmp/test_watch

# Create a JSON file
echo '{"symbol": "NVDA", "price": 120.5}' > /tmp/test_watch/test1.json

# Watch the first terminal - you should see the handler execute immediately
```

## Common Use Cases

### Process NewsWatcher Output

```bash
python3 jsonDetector.py \
    --watch-directory ../newsWatcher/outputs \
    --script-to-launch ./analyze_news.py
```

### Allow Multiple Triggers (Re-processing)

```bash
python3 jsonDetector.py \
    --watch-directory /tmp/data \
    --script-to-launch ./process.sh \
    --multiple-trigger YES
```

### With Custom Timeout

```bash
python3 jsonDetector.py \
    --watch-directory /tmp/data \
    --script-to-launch ./slow_processor.py \
    --timeout 3600  # 1 hour for long operations
```

## Verify It's Working

### Check logs while running

```bash
# In another terminal, while jsonDetector is running:
tail -f logs/jsonDetector_*.log
```

### Verify state persistence

```bash
# Check processed files
cat processed_files.json
```

## Graceful Shutdown

Press **Ctrl+C** to stop cleanly. The script will:
1. Stop accepting new files
2. Close the file monitor
3. Save final state
4. Log shutdown summary
5. Exit cleanly

## Troubleshooting

**Script not executing?**
- Check permissions: `ls -l /path/to/script`
- Verify it's executable: `chmod +x /path/to/script`
- Check logs: `tail logs/jsonDetector_*.log`

**"Watch directory does not exist"?**
- Create the directory: `mkdir -p /path/to/watch`

**"Script is not executable"?**
- Make it executable: `chmod +x /path/to/script`

**Multiple instances processing same file?**
- Use `--multiple-trigger NO` (default) to prevent duplicates
- Delete `processed_files.json` to reset state

## For More Details

See **CLAUDE.md** for:
- Complete architecture overview
- All command-line options
- Comprehensive testing procedures
- Integration patterns
- Known limitations and warnings
