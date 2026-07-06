# Gainers Scanner Script (GSS) - Time0 Checkpoint Tracking

## Overview
The Gainers Scanner Script (GSS) tracks stocks that cross a specified gain threshold from a user-defined time checkpoint (time0). It continuously monitors the market and accumulates stocks as they reach the threshold, updating their live percentages in real-time.

## Key Features
- **Time0 Checkpoint**: Define a specific reference time to measure gains from (Checkpoint-A)
- **Threshold Crossing Detection**: Automatically detects when stocks first cross the gain threshold (Checkpoint-B)
- **Accumulative Tracking**: Stocks are added to the table as they cross threshold, not replaced
- **Live Percentage Updates**: Existing stocks get updated percentages on every scan
- **Auto-Generated Output**: GusTradeTable (GTT) file named with current date
- **Tab-Delimited Format**: Easy to import into Excel/spreadsheets
- **Resume Support**: Can resume from existing GTT file if restarted

## Installation

### Dependencies
```bash
pip install rich pandas pytz ibapi
```

### Requirements
- Python 3.7+
- IB Gateway or TWS running
- Linux (for terminal window feature)

## Core Concepts

### Checkpoint-A (time0)
The reference time point you specify with `--time0`. This is when the baseline price is recorded.
- Format: `YYYY-MM-DD/HH:MM` (US/Eastern timezone)
- Example: `2025-11-14/09:45`

### Checkpoint-B
The moment when a stock first crosses the gain threshold. This is automatically detected and recorded.
- Includes the time and price when threshold was crossed
- Recorded only once per stock (first crossing)

### Live Percentage
The current gain percentage from time0, updated on every scan. This continues to update even after Checkpoint-B is recorded.

## Usage

### Basic Command
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300
```

This will:
1. Use 09:45 ET as the reference time (Checkpoint-A)
2. Track stocks that gain 50%+ from that time
3. Output to `./GusTradeTable_14nov2025.txt`
4. Open in a new terminal window
5. Refresh every 3 seconds

### Custom Output Directory
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/10:00" \
  --gap-percentage 30 \
  --client-id 300 \
  --output-dir ./trading_data
```

Output: `./trading_data/GusTradeTable_14nov2025.txt`

### Paper Trading Account
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 50 \
  --client-id 300 \
  --port 4002
```

### Run in Current Terminal
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300 \
  --no-new-window
```

### Custom Refresh Rate
```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300 \
  --refresh-interval 10
```

## Command-Line Arguments

### Required
| Argument | Type | Description | Example |
|----------|------|-------------|---------|
| `--time0` | string | Reference time checkpoint (US/Eastern) | `"2025-11-14/09:45"` |
| `--gap-percentage` | float | Minimum gain % threshold | `50` |
| `--client-id` | int | IB Gateway client ID | `300` |

### Optional
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--output-dir` | string | `.` | Directory for GTT output |
| `--host` | string | `127.0.0.1` | IB Gateway host |
| `--port` | int | `4001` | IB Gateway port (4002 for paper) |
| `--log-dir` | string | `logs` | Log file directory |
| `--max-stocks` | int | `50` | Max stocks to scan |
| `--min-price` | float | `5.0` | Min stock price filter |
| `--refresh-interval` | int | `3` | Seconds between scans |
| `--no-new-window` | flag | False | Run in current terminal |

## Output

### GusTradeTable (GTT) File

**Filename Format**: `GusTradeTable_DDmmmYYYY.txt`
- Example: `GusTradeTable_14nov2025.txt`
- Format: Tab-delimited text file
- New file created each day

**Columns**:
1. **Symbol** - Stock ticker
2. **CheckPoint-A TIME** - time0 value (e.g., `2025-11-14/09:45`)
3. **CheckPoint-A PRICE** - Price at time0 (USD)
4. **CheckPoint-B TIME** - Time when threshold first crossed
5. **CheckPoint-B PRICE** - Price when threshold crossed
6. **Gain_Percent** - Gain % at Checkpoint-B crossing
7. **live_percentage** - Current gain % (updated every refresh)
8. **Sector** - Stock sector (e.g., Technology, Healthcare)
9. **Industry** - Specific industry classification
10. **Borrow_Status** - Shorting availability: Easy/Available/Hard/N/A (from IBKR API)
11. **Float** - Shares outstanding available for trading
12. **Net_Income_TTM** - Net income trailing twelve months
13. **Institutional_Pct** - Percentage of shares held by institutions
14. **Borrow_Fee_Rate** - Borrow/locate fee rate percentage (from IBKR FTP data)
15. **Shortable_Shares** - Shortable shares indicator from IBKR (0.0-3.0 scale)
16. **Last_Update** - Last scan timestamp

**Example Content**:
```
Symbol	CheckPoint-A TIME	CheckPoint-A PRICE	CheckPoint-B TIME	CheckPoint-B PRICE	Gain_Percent	live_percentage	Borrow_Status	Last_Update
NVDA	2025-11-14/09:45	120.50	2025-11-14/10:15	181.20	50.37	52.45	Easy	2025-11-14 10:30:15
TSLA	2025-11-14/09:45	245.30	2025-11-14/10:22	380.15	54.95	58.12	Available	2025-11-14 10:30:15
```

### Terminal Display

The live display shows:
```
┏━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┓
┃ Symbol ┃ CP-A Price ┃ CP-B Time      ┃ CP-B Price ┃ Gain %   ┃ Live %   ┃ Borrow   ┃ Updated           ┃
┡━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━┩
│ NVDA   │ $120.50    │ 10:15          │ $181.20    │ 50.37%   │ 52.45%   │ Easy     │ 2025-11-14 10:30  │
│ TSLA   │ $245.30    │ 10:22          │ $380.15    │ 54.95%   │ 58.12%   │ Available│ 2025-11-14 10:30  │
└────────┴────────────┴────────────────┴────────────┴──────────┴──────────┴──────────┴───────────────────┘
```

**Color Coding**:
- **Gain %**: Green (<75%) → Yellow (<100%) → Red (≥100%)
- **Live %**:
  - Green: Increasing (live % > gain %)
  - Yellow: Decreasing
  - Red: ≥100%
- **Borrow**: Green (Easy) → Yellow (Available) → Red (Hard) → Gray (N/A)

## How It Works

### Scan Flow
```
1. Connect to IB Gateway
2. Parse time0 and set Checkpoint-A
3. For each scan iteration:
   a. Run IB Scanner (TOP_PERC_GAIN)
   b. For each stock from scanner:
      - If NOT tracked yet:
        * Get price at time0 (Checkpoint-A)
        * Get current price
        * Calculate gain %
        * If gain >= threshold:
          · Record Checkpoint-B (current time/price)
          · Add to GTT file
          · Add to tracked_stocks
      - If ALREADY tracked:
        * Get current price
        * Calculate new live_percentage
        * Update GTT file with new live %
   c. Update display
   d. Sleep for refresh_interval
4. Repeat step 3 until Ctrl+C
```

### Key Behaviors
1. **Stocks accumulate** - Once added, they stay in the GTT
2. **Checkpoint-B recorded once** - Only when first crossing threshold
3. **Live % updates continuously** - Every scan updates the percentage
4. **Time0 price cached** - Fetched once per stock for efficiency
5. **Resume support** - Can restart and continue from existing GTT

## Borrow Fee and Short Selling Data

The scanner automatically fetches shorting-related data for Gap Up Short trading:

### Data Sources

**IBKR API (Generic Tick 236)**:
- **Borrow_Status**: Easy/Available/Hard/N/A
- **Shortable_Shares**: Indicator value (0.0-3.0 scale)
- Interpretation:
  - Easy: > 2.5 (abundant shares available)
  - Available: 1.5-2.5 (shares available)
  - Hard: 0-1.5 (limited shares)
  - N/A: No data available

**IBKR FTP Server (Automatic Enrichment)**:
- **Borrow_Fee_Rate**: Percentage fee to borrow shares
- Downloaded from `ftp2.interactivebrokers.com`
- Contains 17,980+ US stocks
- Updated multiple times per day by IBKR
- Automatically enriches GTT file after each scan
- Cached to `./scripts_functions/borrow_fee_fetch_functions/cache/borrow_fees.csv` for performance

### How It Works

1. **During Scan**: GSS fetches shortable shares indicator (tick 236) for each stock
2. **After GTT Write**: Automatically calls `borrow_fee_orchestrator.py` to enrich data with multi-source data (FTP + iBorrowDesk)
3. **Multi-Source Enrichment**: Downloads latest borrow fee rates from multiple sources and updates GTT file with BF1, BF2, BF_BEST, BF_Source columns
4. **Caching**: Both FTP and iBorrowDesk data cached locally to avoid repeated downloads (60s timeout)
5. **Graceful Degradation**: If enrichment fails, scanner continues with API data only

### Expected Coverage

- **Borrow_Status**: 95-100% (from IBKR API tick 236)
- **Shortable_Shares**: 90-95% (from IBKR API tick 236)
- **Borrow_Fee_Rate**: 70-90% (from IBKR FTP data)

### Important Notes

- **Tick 499 is INVALID**: Do not use generic tick 499 for stocks (causes Error 321)
- **FTP is Primary Source**: Borrow fee rates only available via FTP, not API
- **Non-blocking**: FTP enrichment runs in background with 60s timeout
- **Error Handling**: Scanner continues if FTP fails, data shows N/A

For detailed technical information, see: `BORROW_FEE_IMPROVEMENT_SUMMARY.md`

## Use Cases

### Morning Gap Scanner
Track stocks that gap up from market open:
```bash
# Start at market open (9:30 AM ET)
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 30 \
  --client-id 300
```

### Intraday Momentum Scanner
Track stocks that surge from a specific time:
```bash
# Track from 10:00 AM ET
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/10:00" \
  --gap-percentage 50 \
  --client-id 300
```

### News Event Scanner
Track stocks moving after a scheduled news event:
```bash
# Track from 2:00 PM ET (Fed announcement)
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/14:00" \
  --gap-percentage 25 \
  --client-id 300
```

### Multiple Time Windows
Run multiple instances to track different timeframes:
```bash
# Terminal 1: From 9:30 AM
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 50 \
  --client-id 300 \
  --output-dir ./data/0930

# Terminal 2: From 10:00 AM
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/10:00" \
  --gap-percentage 50 \
  --client-id 301 \
  --output-dir ./data/1000
```

## Troubleshooting

### "time0 cannot be in the future"
- time0 must be in the past
- Check your system clock
- Ensure format is correct: `YYYY-MM-DD/HH:MM`

### "Invalid time0 format"
- Use exact format: `YYYY-MM-DD/HH:MM`
- Example: `2025-11-14/09:45`
- Don't forget the slash between date and time

### "No data available at time0"
- Stock may not have been trading at that time
- Try a different time0 closer to market hours
- Check if stock existed at that date/time

### "Connection timeout"
- Ensure IB Gateway is running
- Check port (4001 for live, 4002 for paper)
- Verify client ID is not in use

### No Stocks Appearing
- Lower --gap-percentage threshold
- Increase --max-stocks to scan more
- Check if time0 is too recent (prices haven't moved much yet)
- Verify scanner is finding stocks (check logs)

## Resume Support

If the script crashes or you restart it:

1. Run the same command again
2. Script will load existing GTT file
3. Previously tracked stocks continue being updated
4. New stocks that cross threshold will be added

**Important**: Use the same `--output-dir` and time0 will determine the same GTT filename.

## Performance Notes

- **API Calls**: ~2 calls per stock (time0 price + current price)
- **Time0 Price Cached**: Only fetched once per stock
- **File Updates**: Incremental (append for new, modify for updates)
- **Recommended Refresh**: 3+ seconds to avoid rate limits
- **Scan Time**: Scales with --max-stocks parameter

## Differences from Previous Version

### Before (Previous Close Version)
- ❌ Calculated gains from previous day's close
- ❌ Replaced results on each scan
- ❌ CSV format with different columns
- ❌ No live percentage tracking
- ❌ No checkpoint recording

### After (Time0 Version)
- ✅ Calculates gains from user-defined time0
- ✅ Accumulates stocks as they cross threshold
- ✅ Tab-delimited GTT format
- ✅ Live percentage updates every scan
- ✅ Records Checkpoint-A and Checkpoint-B

## Tips & Best Practices

1. **Set time0 strategically**: Market open, news events, or technical breakout times
2. **Use multiple instances**: Track different time windows simultaneously
3. **Monitor live %**: Stocks with increasing live_percentage are strengthening
4. **Check borrow status**: Important for short selling opportunities
5. **Save GTT files**: Archive daily for historical analysis
6. **Adjust threshold**: Start with 30-50%, adjust based on market volatility
7. **Review logs**: Check log files for API errors or connection issues

## Example Workflow

### Daily Morning Routine
```bash
# 1. Start IB Gateway at 9:25 AM

# 2. At 9:30 AM market open, start scanner
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 40 \
  --client-id 300 \
  --output-dir ./daily_scans

# 3. Monitor terminal for new stocks crossing threshold

# 4. At end of day, stop with Ctrl+C

# 5. Review GusTradeTable_14nov2025.txt for all stocks that crossed 40%

# 6. Archive the GTT file for records
mv ./daily_scans/GusTradeTable_14nov2025.txt ./archive/
```

## Log Files

Logs saved to `./logs/scanner_YYYYMMDD_HHMMSS.log`

Contains:
- Connection events
- time0 parsing
- Stock processing details
- API errors
- Checkpoint recordings

View in real-time:
```bash
tail -f logs/scanner_*.log
```

## Support

For issues or questions:
- Check log files first
- Verify IB Gateway is running and connected
- Ensure time0 is valid and in the past
- Check GTT file permissions

## Quick Reference Card

```bash
# Basic usage
python3 Gainers_Scanner_Script.py --time0 "YYYY-MM-DD/HH:MM" --gap-percentage NN --client-id NNN

# Custom output directory
--output-dir ./path/to/dir

# Paper trading
--port 4002

# Current terminal
--no-new-window

# Slower refresh
--refresh-interval 10

# More stocks
--max-stocks 100
```

**Output**: `GusTradeTable_DDmmmYYYY.txt` (tab-delimited)

**Stops**: Press Ctrl+C

**Resumes**: Run same command again
