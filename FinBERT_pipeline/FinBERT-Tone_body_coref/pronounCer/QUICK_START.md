# pronounCer Quick Start Guide

## 60-Second Setup

### 1. Install Core Dependencies (One Time)
```bash
pip3 install --break-system-packages spacy flask requests
python3 -m spacy download en_core_web_sm --break-system-packages
```

### 2. Optional: Install Full Mode Support (One Time)
```bash
pip3 install --break-system-packages fastcoref
```

## Running the System

### Start Service (One Terminal)
```bash
cd /home/tom/Documents/ibkr_scripts/N1/scripts/pronounCer
python3 pronounCer_service.py
```

Keep this running. You'll see:
```
Starting pronounCer Service...
Loading spaCy model: en_core_web_sm...
Model loaded successfully!
Using simple pronoun resolution with spaCy NER
Starting Flask server on http://localhost:5050
```

### Process Files (Another Terminal)
```bash
cd /home/tom/Documents/ibkr_scripts/N1/scripts/pronounCer

# Simple mode (fast, pronouns only)
python3 pronounCer.py --inputs FEED_28-jan-2026

# OR Full mode (slow, all coreferences)
python3 pronounCer.py --inputs FEED_28-jan-2026 --mode full
```

## Output Files

For input `FEED_28-jan-2026`:
- Input: `FEED_28-jan-2026_{headline|summary|content}.txt`
- Output: `FEED_28-jan-2026_{headline|summary|content}_pronouns.txt`

## Simple vs Full Mode

| Need | Use |
|------|-----|
| Fast processing (0.3-0.5 sec/file) | **Simple** (default) |
| Lightweight (~200MB) | **Simple** |
| Just resolve "he", "she", "it", "they" | **Simple** |
| Resolve "the company" → "ENvue Medical" | **Full** |
| Handle complex coreference chains | **Full** |
| Don't mind 1-2 sec/file processing | **Full** |

## Examples

### Example 1: Simple Mode (Pronouns)
Input:
```
ENvue Medical announced earnings. It beat expectations.
```

Output (Simple Mode):
```
ENvue Medical announced earnings. ENvue Medical beat expectations.
```

**Notice**: "The company" is NOT changed in simple mode ⚠️

### Example 2: Full Mode (All Coreferences)
Input:
```
ENvue Medical announced earnings. It beat expectations. The company grew revenue.
```

Output (Full Mode):
```
ENvue Medical announced earnings. ENvue Medical beat expectations. ENvue Medical grew revenue.
```

**Notice**: Both "It" and "The company" are resolved ✅

## Troubleshooting

### "Service not running"
```bash
# In Terminal 1, make sure service is running:
python3 pronounCer_service.py

# Then try client again in Terminal 2:
python3 pronounCer.py --inputs FEED_28-jan-2026
```

### "Model not found"
```bash
# Download the spaCy model:
python3 -m spacy download en_core_web_sm --break-system-packages
```

### "Missing input files"
```bash
# Check all 3 files exist:
ls FEED_28-jan-2026_*.txt

# Should show:
# FEED_28-jan-2026_headline.txt
# FEED_28-jan-2026_summary.txt
# FEED_28-jan-2026_content.txt
```

### Full mode doesn't improve results
```bash
# Check if fastcoref is installed:
python3 -c "from fastcoref import FCoref; print('✓ fastcoref installed')"

# If not installed:
pip3 install --break-system-packages fastcoref

# Restart service:
# Kill current service (Ctrl+C)
# Run: python3 pronounCer_service.py
```

## Running in Background

### Using screen (Recommended)
```bash
# Start service in background
screen -S pronouncer
python3 pronounCer_service.py
# Press Ctrl+A then D to detach

# Later, reattach:
screen -r pronouncer

# Kill session:
screen -X -S pronouncer quit
```

### Using nohup
```bash
nohup python3 pronounCer_service.py > service.log 2>&1 &

# Check logs:
tail -f service.log

# Stop service:
pkill -f pronounCer_service.py
```

## Next Steps

1. **Read Full README**: `README.md` has complete documentation
2. **Test API Directly**: See "Service API" section in README
3. **Advanced Config**: Check `IMPLEMENTATION_COMPLETE.md` for architecture details

## Support

All common issues covered in `README.md` under Troubleshooting section.

Happy pronoun resolving! 🚀
