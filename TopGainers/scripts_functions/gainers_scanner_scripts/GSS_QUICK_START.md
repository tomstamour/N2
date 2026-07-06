# GSS Quick Start Guide

## Installation
```bash
pip install rich pandas pytz ibapi
```

## Basic Usage

### 1. Most Common Command
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300
```

**What this does**:
- Tracks stocks from 9:45 AM ET as the reference point
- Finds stocks with 50%+ gain from that time
- Auto-saves to `./GusTradeTable_14nov2025.txt`
- Opens in new terminal window
- Refreshes every 3 seconds

### 2. Market Open Scanner
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 30 \
  --client-id 300
```

### 3. Custom Directory
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/10:00" \
  --gap-percentage 50 \
  --client-id 300 \
  --output-dir ./trading_data
```
Output: `./trading_data/GusTradeTable_14nov2025.txt`

### 4. Paper Trading (Port 4002)
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 50 \
  --client-id 300 \
  --port 4002
```

### 5. Run in Current Terminal
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300 \
  --no-new-window
```

### 6. Slower Refresh (10 seconds)
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300 \
  --refresh-interval 10
```

## Understanding time0

**time0** is your reference time - the moment you want to measure gains from.

**Format**: `YYYY-MM-DD/HH:MM` (US/Eastern timezone)

**Examples**:
- `2025-11-14/09:30` - Market open
- `2025-11-14/09:45` - 15 minutes after open
- `2025-11-14/10:00` - Mid-morning
- `2025-11-14/14:00` - 2:00 PM (news event)

**Rules**:
- Must be in the past (not future)
- US/Eastern timezone
- Format must be exact with slash

## Output Table

### Filename
Automatically generated as: `GusTradeTable_DDmmmYYYY.txt`
- Example: `GusTradeTable_14nov2025.txt`
- One file per day
- Tab-delimited text

### Columns
```
Symbol | CP-A TIME  | CP-A PRICE | CP-B TIME | CP-B PRICE | Gain % | Live % | Borrow | Updated
NVDA   | 09:45      | $120.50    | 10:15     | $181.20    | 50.37  | 52.45  | Easy   | 10:30:15
TSLA   | 09:45      | $245.30    | 10:22     | $380.15    | 54.95  | 58.12  | Avail  | 10:30:15
```

**CP-A (Checkpoint-A)**: Your time0 reference point
**CP-B (Checkpoint-B)**: When stock first crossed threshold
**Gain %**: Gain at Checkpoint-B
**Live %**: Current gain (updates every refresh)

## Terminal Display

Live updating table shows:
- All stocks that crossed threshold
- Current gain percentages
- Borrow status for shorting
- Last update time

**Color Coding**:
- **Green**: Good (Easy borrow, <75% gain, increasing)
- **Yellow**: Moderate (Available borrow, 75-100% gain)
- **Red**: Extreme (Hard borrow, >100% gain)

## How It Works

1. **Set time0**: You specify reference time (e.g., 9:30 AM)
2. **Scanner runs**: Finds top gaining stocks every 3 seconds
3. **Track crossings**: When stock crosses threshold, record it
4. **Update live %**: Existing stocks get updated percentages
5. **Accumulate**: Stocks stay in table once added
6. **Save to file**: GTT file updates automatically

## Stopping the Scanner

Press **Ctrl+C** to stop gracefully.

The GTT file will have all stocks that crossed the threshold.

## Resume Support

If you stop and restart with same command:
- Loads existing GTT file
- Continues tracking existing stocks
- Adds new stocks that cross threshold

## Quick Troubleshooting

### "time0 cannot be in the future"
✓ Use a time in the past: `2025-11-14/09:30`
✗ Don't use future times

### "Invalid time0 format"
✓ Use: `2025-11-14/09:45` (with slash)
✗ Not: `2025-11-14 09:45` (space)

### "Connection timeout"
- Check IB Gateway is running
- Verify port: 4001 (live) or 4002 (paper)
- Try different client ID if in use

### No Stocks Appearing
- Lower threshold: `--gap-percentage 25`
- Increase stocks scanned: `--max-stocks 100`
- Wait longer (stocks need time to move)

## Arguments Quick Reference

### Required
- `--time0` - Reference time (format: `YYYY-MM-DD/HH:MM`)
- `--gap-percentage` - Threshold % (e.g., 50)
- `--client-id` - IB client ID (e.g., 300)

### Common Optional
- `--output-dir` - Output directory (default: current dir)
- `--port` - IB port (4001 or 4002)
- `--no-new-window` - Run in current terminal
- `--refresh-interval` - Seconds between scans (default: 3)

## Daily Workflow Example

```bash
# 1. Start IB Gateway at 9:25 AM

# 2. At market open (9:30 AM), run:
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 40 \
  --client-id 300

# 3. Monitor terminal for stocks crossing 40%

# 4. At end of session, press Ctrl+C

# 5. Review GusTradeTable_14nov2025.txt
cat GusTradeTable_14nov2025.txt

# 6. Import into Excel for analysis
```

## Multiple Time Windows

Track different timeframes simultaneously:

```bash
# Terminal 1: From market open
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 50 \
  --client-id 300 \
  --output-dir ./data/0930

# Terminal 2: From 10 AM
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/10:00" \
  --gap-percentage 50 \
  --client-id 301 \
  --output-dir ./data/1000
```

**Note**: Use different client IDs for each instance!

## Tips

1. **Start early**: Run from market open to catch morning movers
2. **Lower threshold**: 30-40% is good for volatile days
3. **Check live %**: Green (increasing) is stronger momentum
4. **Archive daily**: Save GTT files for historical analysis
5. **Use multiple windows**: Track different time periods
6. **Monitor logs**: Check `./logs/` for errors

## Import to Excel

1. Open Excel
2. Data → From Text/CSV
3. Select `GusTradeTable_14nov2025.txt`
4. Delimiter: Tab
5. Import

Now you can analyze, sort, and chart the data!

## Need More Help?

Full documentation: `GSS_TIME0_README.md`

View logs:
```bash
ls -ltr logs/
tail -f logs/scanner_*.log
```

## Example Output

**Terminal**:
```
╔════════════════════════════════════════════════════════════════════════════╗
║ Gainers Scanner (GSS) - Threshold: 50% | Time0: 2025-11-14/09:45          ║
╠════════════════════════════════════════════════════════════════════════════╣
║ Symbol │ CP-A Price │ CP-B Time │ CP-B Price │ Gain %  │ Live %  │ Borrow ║
╟────────┼────────────┼───────────┼────────────┼─────────┼─────────┼────────╢
║ NVDA   │ $120.50    │ 10:15     │ $181.20    │ 50.37%  │ 52.45%  │ Easy   ║
║ TSLA   │ $245.30    │ 10:22     │ $380.15    │ 54.95%  │ 58.12%  │ Avail  ║
╚════════════════════════════════════════════════════════════════════════════╝
```

**File** (`GusTradeTable_14nov2025.txt`):
```
Symbol	CheckPoint-A TIME	CheckPoint-A PRICE	CheckPoint-B TIME	CheckPoint-B PRICE	Gain_Percent	live_percentage	Borrow_Status	Last_Update
NVDA	2025-11-14/09:45	120.50	2025-11-14/10:15	181.20	50.37	52.45	Easy	2025-11-14 10:30:15
TSLA	2025-11-14/09:45	245.30	2025-11-14/10:22	380.15	54.95	58.12	Available	2025-11-14 10:30:15
```

Ready to start scanning! 🚀
