# Gainers Scanner - Quick Start Guide

## Installation

```bash
# Install required Python libraries
pip install rich pandas pytz ibapi
```

## Basic Usage

### 1. Most Common Command
```bash
python3 Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 50 \
  --client-id 300
```
This will:
- Scan for stocks with 50%+ gains from previous close
- Display results in a new terminal window
- Auto-refresh every 3 seconds
- Save results to `gainers.csv`

### 2. Run in Current Terminal (No New Window)
```bash
python3 Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 50 \
  --client-id 300 \
  --no-new-window
```

### 3. Paper Trading Account (Port 4002)
```bash
python3 Gainers_Scanner_Script.py \
  --output paper_gainers.csv \
  --gap-percentage 50 \
  --client-id 300 \
  --port 4002
```

### 4. Slower Refresh (Every 10 seconds)
```bash
python3 Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 50 \
  --client-id 300 \
  --refresh-interval 10
```

### 5. Lower Threshold (30%+ gainers)
```bash
python3 Gainers_Scanner_Script.py \
  --output gainers_30.csv \
  --gap-percentage 30 \
  --client-id 300
```

### 6. Scan More Stocks (100 instead of 50)
```bash
python3 Gainers_Scanner_Script.py \
  --output gainers_large.csv \
  --gap-percentage 50 \
  --client-id 300 \
  --max-stocks 100
```

### 7. Custom Output Directory
```bash
python3 Gainers_Scanner_Script.py \
  --output ./data/gainers_$(date +%Y%m%d).csv \
  --gap-percentage 50 \
  --client-id 300
```

## Understanding the Output

### Terminal Display
```
┏━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Symbol  ┃ Prev Close ┃ Current    ┃ Gain %   ┃ Borrow     ┃ Updated           ┃
┡━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ NVDA    │ $120.50    │ $181.20    │ 50.37%   │ Easy       │ 2025-11-14 09:45  │
│ TSLA    │ $245.30    │ $380.15    │ 54.95%   │ Available  │ 2025-11-14 09:45  │
└─────────┴────────────┴────────────┴──────────┴────────────┴───────────────────┘
```

**Color Coding:**
- **Gain %**: Green (<75%) → Yellow (<100%) → Red (≥100%)
- **Borrow**: Green (Easy) → Yellow (Available) → Red (Hard) → Gray (N/A)

### CSV Output
```csv
Symbol,Prev_Close,Current_Price,Gain_Percent,Borrow_Status,Scan_Time
NVDA,120.50,181.20,50.37,Easy,2025-11-14 09:45:23
TSLA,245.30,380.15,54.95,Available,2025-11-14 09:45:23
```

## Stopping the Scanner

Press `Ctrl+C` to stop the scanner gracefully.

## Troubleshooting

### "Connection timeout" Error
```bash
# Make sure IB Gateway is running on the correct port
# Default: 4001 (live), 4002 (paper)
# Check IB Gateway API settings
```

### "Client ID already in use" Error
```bash
# Use a different client ID
python3 Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 50 \
  --client-id 301  # Changed from 300
```

### "No compatible terminal emulator found"
```bash
# Install a terminal emulator
sudo apt install gnome-terminal  # Ubuntu/Debian
# OR run in current terminal
python3 Gainers_Scanner_Script.py ... --no-new-window
```

### No Results Found
```bash
# Try lowering the threshold
python3 Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 25  # Lower threshold
  --client-id 300
```

## Command-Line Arguments Reference

### Required
- `--output`: CSV output file path
- `--gap-percentage`: Minimum gain % (e.g., 50)
- `--client-id`: IB Gateway client ID

### Optional
- `--host`: IB host (default: 127.0.0.1)
- `--port`: IB port (default: 4001)
- `--log-dir`: Log directory (default: logs)
- `--max-stocks`: Max stocks to scan (default: 50)
- `--min-price`: Min stock price (default: 5.0)
- `--refresh-interval`: Seconds between scans (default: 3)
- `--no-new-window`: Run in current terminal

## Full Documentation

For complete documentation, see:
- **GAINERS_SCANNER_README.md** - Full usage guide
- **REFACTORING_SUMMARY.md** - Technical details and changes

## Log Files

Logs are saved to `./logs/scanner_YYYYMMDD_HHMMSS.log`

To view logs in real-time:
```bash
tail -f logs/scanner_*.log
```

## Example Workflow

### Morning Routine (Find Pre-Market Gainers)
```bash
# 1. Start IB Gateway
# 2. Wait for connection
# 3. Run scanner
python3 Gainers_Scanner_Script.py \
  --output morning_gainers.csv \
  --gap-percentage 30 \
  --client-id 300 \
  --max-stocks 100

# 4. Monitor in terminal
# 5. Check CSV for detailed data
# 6. Stop with Ctrl+C when done
```

### Multiple Instances
```bash
# Terminal 1: High gainers (50%+)
python3 Gainers_Scanner_Script.py \
  --output high_gainers.csv \
  --gap-percentage 50 \
  --client-id 300

# Terminal 2: Moderate gainers (25%+)
python3 Gainers_Scanner_Script.py \
  --output moderate_gainers.csv \
  --gap-percentage 25 \
  --client-id 301

# Terminal 3: Paper account test
python3 Gainers_Scanner_Script.py \
  --output paper_test.csv \
  --gap-percentage 40 \
  --client-id 302 \
  --port 4002
```

## Tips

1. **Start Conservative**: Begin with `--gap-percentage 50` and adjust based on results
2. **Multiple Instances**: Use different client IDs for each instance
3. **Log Monitoring**: Keep logs for troubleshooting API issues
4. **Refresh Rate**: Don't go below 3 seconds to avoid rate limits
5. **CSV Analysis**: Import CSV into Excel/Pandas for further analysis
6. **Borrow Status**: "N/A" is common; IB API has limited borrow data

## Need Help?

Run with `--help` to see all options:
```bash
python3 Gainers_Scanner_Script.py --help
```

Check log files for detailed error messages:
```bash
ls -ltr logs/
cat logs/scanner_*.log
```
