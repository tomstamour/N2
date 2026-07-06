# Gainers Scanner Script - Usage Guide

## Overview
The Gainers Scanner Script is a command-line tool that continuously scans for stocks with significant price gains from the previous day's close. It displays results in a live-updating terminal table and exports data to CSV.

## Features
- **Command-line driven**: No config files needed, all parameters via arguments
- **Real-time scanning**: Continuous monitoring with configurable refresh intervals
- **Live display**: Beautiful terminal table using the `rich` library with color-coded data
- **IBKR-style shortable status**: Shows stock availability for shorting with official IBKR labels (HIGH_AVAIL/MODERATE_AVAIL/LOW_AVAIL/NOT_SHORTABLE/N/A)
- **Raw tick data**: Captures both tick 46 (difficulty level) and tick 89 (exact share count) from IBKR API
- **GTT export**: Automatically updates tab-delimited GusTradeTable file with latest results
- **Terminal window**: Optionally launches in a new terminal window

## Installation

### Requirements
```bash
pip install rich pandas pytz ibapi
```

### System Requirements
- Python 3.7+
- IB Gateway or TWS running
- Linux terminal emulator (gnome-terminal, xterm, konsole, or xfce4-terminal)

## Usage

### Basic Command
```bash
python Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 50 \
  --client-id 300
```

### All Options
```bash
python Gainers_Scanner_Script.py \
  --output ./output/gainers.csv \
  --gap-percentage 25 \
  --client-id 300 \
  --host 127.0.0.1 \
  --port 4001 \
  --log-dir ./logs \
  --max-stocks 100 \
  --min-price 0.50 \
  --refresh-interval 5 \
  --no-new-window
```

## Arguments

### Required Arguments
| Argument | Type | Description | Example |
|----------|------|-------------|---------|
| `--output` | string | Output CSV file path | `./gainers.csv` |
| `--gap-percentage` | float | Minimum gain % threshold | `50` (for 50%) |
| `--client-id` | int | IB Gateway client ID | `300` |

### Optional Arguments
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--host` | string | `127.0.0.1` | IB Gateway host |
| `--port` | int | `4001` | IB Gateway port |
| `--log-dir` | string | `logs` | Directory for log files |
| `--max-stocks` | int | `50` | Maximum stocks to scan |
| `--min-price` | float | `0.50` | Minimum stock price filter |
| `--refresh-interval` | int | `3` | Seconds between scans |
| `--no-new-window` | flag | False | Run in current terminal |

## Output

### Terminal Display
The script displays a live-updating table with the following columns:
- **Symbol**: Stock ticker
- **Prev Close**: Previous day's closing price
- **Current**: Current market price
- **Gain %**: Percentage gain (color-coded: green < 75% < yellow < 100% < red)
- **Borrow**: Shorting availability (Easy/Available/Hard/N/A)
- **Updated**: Timestamp of last scan

### CSV File
The CSV file contains the same data and is updated after each scan:
```csv
Symbol,Prev_Close,Current_Price,Gain_Percent,Borrow_Status,Scan_Time
NVDA,120.50,181.20,50.37,Easy,2025-11-14 09:45:23
TSLA,245.30,380.15,54.95,Available,2025-11-14 09:45:23
```

## How It Works

### Gain Calculation
The script calculates gains from the **previous trading day's close** to the **current market price**:
```
Gain % = ((Current Price - Previous Close) / Previous Close) × 100
```

### Scanning Process
1. Connects to IB Gateway/TWS
2. Requests top percentage gainers from IB Scanner
3. For each stock:
   - Fetches previous day's closing price
   - Fetches current market price
   - Requests borrow/locate information
   - Calculates gain percentage
4. Filters stocks meeting the threshold
5. Updates display and CSV file
6. Waits for refresh interval
7. Repeats from step 2

### Shortable Status Data (Two-Tier Detection System)

The scanner uses a **two-tier shortability detection system** to provide both SLB (Stock Loan/Borrow) availability and definitive margin eligibility:

#### Four GTT Columns for Shortable Data:

**Tier 1: SLB Availability (IBKR Market Data - Tick 46/89)**

1. **ShortStatus**: IBKR-style interpreted label
   - `HIGH_AVAIL`: > 2.5 difficulty level (at least 1,000 shares available - equivalent to IBKR's light green indicator)
   - `MODERATE_AVAIL`: 1.5-2.5 difficulty level (shares locatable but fewer available - equivalent to IBKR's dark green)
   - `LOW_AVAIL`: 0-1.5 difficulty level (hard to borrow - limited availability)
   - `NOT_SHORTABLE`: ≤ 0 difficulty level (not available for shorting at IBKR)
   - `N/A`: No data available from IBKR API

2. **ShortDiff**: Raw tick 46 value (0.0-3.0+)
   - Numeric difficulty level directly from IBKR API
   - Higher values = easier to borrow
   - Preserved for transparency and custom analysis

3. **ShortableShares**: Raw tick 89 count
   - Exact number of shares available for shorting
   - Integer value from IBKR API
   - Only available when IBKR provides this data

**Tier 2: Margin Eligibility (WhatIf Order Preview)**

4. **Shortable**: Definitive margin eligibility via WhatIf check (YES/NO/N/A)
   - `YES`: Stock can be shorted (IBKR confirmed via margin calculation)
   - `NO`: Stock cannot be shorted (margin restrictions, account limitations, or regulatory blocks)
   - `N/A`: Check failed, timeout, or historical mode
   - **Performance**: Adds ~50-200ms per stock (~7-10 seconds for 50 stocks)
   - **Accuracy**: Eliminates false positives where Tick 46/89 show availability but orders are rejected

#### Why Two Tiers?

**Tick 46/89 (Tier 1)** shows if IBKR has shares to lend (SLB availability) but **does NOT indicate**:
- Margin account eligibility
- Account-specific restrictions
- Regulatory short sale restrictions

**WhatIf Order Preview (Tier 2)** tests actual margin requirements for your account, providing definitive shortability status.

#### IBKR API Technical Details:

- **Tick 46** (Shortable Difficulty): Requested via `reqMktData()` with `genericTickList='236'`
- **Tick 89** (Shortable Shares Count): Captured via `tickSize()` callback
- **WhatIf Check**: `placeOrder()` with `order.whatIf = True` (order IDs 90000+)
- **Live Mode**: All data available during market hours for most stocks
- **Historical Mode**: Tick 46/89 and WhatIf set to N/A (IBKR doesn't provide historical data)

#### Data Availability Notes:

- IBKR API has limited shortable data for some stock types:
  - OTC stocks may return N/A
  - Foreign ADRs may have limited data
  - Micro-cap stocks often unavailable
  - New IPOs may not have borrow data yet
- Even in live mode during market hours, some stocks will show N/A
- This is an IBKR API limitation, not a script issue

## Examples

### Monitor NVIDIA and other gainers with 30% threshold
```bash
python Gainers_Scanner_Script.py \
  --output nvda_gainers.csv \
  --gap-percentage 30 \
  --client-id 301
```

### High-frequency scanning (1-second refresh)
```bash
python Gainers_Scanner_Script.py \
  --output fast_scan.csv \
  --gap-percentage 75 \
  --client-id 302 \
  --refresh-interval 1 \
  --max-stocks 20
```

### Paper trading account (port 4002)
```bash
python Gainers_Scanner_Script.py \
  --output paper_gainers.csv \
  --gap-percentage 50 \
  --client-id 300 \
  --port 4002
```

### Run in current terminal (no new window)
```bash
python Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 40 \
  --client-id 300 \
  --no-new-window
```

## Troubleshooting

### Connection Issues
- Ensure IB Gateway or TWS is running
- Check that the port matches your IB Gateway configuration
- Verify client ID is not already in use
- Check firewall settings

### No Results Found
- Lower the `--gap-percentage` threshold
- Increase `--max-stocks` to scan more stocks
- Lower `--min-price` to include cheaper stocks
- Check market hours (scanner may return fewer results outside market hours)

### Scanner Timeouts / Service Unavailable
If you see repeated scanner timeouts with 0 results:

**Symptoms:**
- Log shows: "Scanner timeout (no response from IBKR)"
- Warning: "⚠ IBKR Scanner Service Unavailable"
- Scanner timeout count increases: 1, 2, 3+

**Common Causes:**
1. **Early Pre-Market Hours (before 7:30 AM EST)**
   - IBKR's `TOP_PERC_GAIN` scanner has limited availability before ~7:30 AM
   - The service may not respond during early pre-market (4:00-7:30 AM EST)
   - This is an IBKR infrastructure limitation, not a script issue

2. **IBKR Service Issues**
   - Occasional IBKR scanner service interruptions
   - Usually resolves within 30-60 minutes

**What the Script Does:**
- **Tracks timeouts**: Counts consecutive scanner timeouts
- **Distinguishes failures**: Reports "service timeout" vs "0 legitimate results"
- **Exponential backoff**: After 3+ timeouts, increases wait time (6s → 12s → 24s → 48s → 60s max)
- **Auto-recovery**: Resets to normal interval when service recovers
- **User warnings**: Displays clear messages after 3+ consecutive timeouts

**Solutions:**
- **Wait until 7:30 AM EST**: Scanner service is more reliable after this time
- **Use --historical-mode**: Bypasses live scanner, uses historical data instead
- **Be patient**: Script will auto-recover when IBKR service resumes
- **Monitor logs**: Check for "Scanner service recovered" message

**Example Output:**
```
⚠ IBKR Scanner Service Unavailable
  The scanner has timed out 3+ times. This is common before 7:30 AM EST.
  Consider waiting until 7:30 AM or using --historical-mode.
Scanner service unavailable. Using backoff interval: 12s
```

### Missing Shortable Data
- IBKR API has limited shortable status data for certain stock types
- Some stocks will show "N/A" even in live mode during market hours
- OTC stocks, foreign ADRs, micro-caps often have no borrow data
- Historical mode always shows N/A (IBKR doesn't provide historical borrow status)
- This is an IBKR API limitation, not a script issue

### OTC Detection

The scanner uses a multi-layer approach to detect OTC (Over-The-Counter) stocks:

**Detection Layers:**
1. **IBKR Contract Exchange** (Primary): Checks contract.exchange and contract.primaryExchange
   - Standard OTC codes: PINK, OTCBB, OTCMKTS, OTC, OTCQX, OTCQB
   - IBKR-specific codes: EXPERT (OTC Markets), VENTURE, GREY
   - **Note:** SCM (Nasdaq Capital Market) is NOT OTC - it's a legitimate NASDAQ tier exchange

2. **yfinance Exchange** (Fallback): Uses yfinance exchange data when IBKR data is ambiguous
   - yfinance OTC codes: OTCMKTS, PINK, OTC, OTCBB, OTCQB, OTCQX, OQX, OQB
   - Activated when IBKR returns empty or "SMART" routing

3. **Symbol Pattern** (Logging Only): 5-letter symbols ending in F flagged for review
   - NOT used for detection (too many false positives)
   - Logs warning for manual review only

**OTC Column Values:**
- "OTC": Stock is over-the-counter
- "-": Stock trades on major exchange (NASDAQ, NYSE, AMEX, etc.)

**Known Limitations:**
- IBKR scanner may return empty primaryExchange for some OTC stocks
- Detection relies on EXPERT exchange code for OTC Markets stocks
- yfinance API may be unavailable (falls back to IBKR-only detection)
- Symbol patterns are NOT used for detection (too many false positives like STAFF, DISCA)

### Terminal Window Not Opening
- Install a compatible terminal emulator: `gnome-terminal`, `xterm`, `konsole`, or `xfce4-terminal`
- Or use `--no-new-window` to run in current terminal

## Stopping the Scanner
Press `Ctrl+C` to gracefully stop the scanner. It will:
1. Stop the scanning loop
2. Disconnect from IB Gateway
3. Save final results to CSV
4. Exit cleanly

## Log Files
Log files are saved in the directory specified by `--log-dir` (default: `./logs/`):
- Filename format: `scanner_YYYYMMDD_HHMMSS.log`
- Contains detailed debugging information
- Useful for troubleshooting connection and data issues

## Performance Notes
- Continuous scanning is API-intensive
- IB may rate-limit requests if refresh interval is too short
- Recommended minimum refresh interval: 3 seconds
- Higher `--max-stocks` values increase scan time
- Each stock requires 2 API calls (historical + market data)

## Changes from Original Version
1. **Removed config file dependency** - All parameters via command-line arguments
2. **Changed gain calculation** - Now uses previous day close instead of 4:00 AM price
3. **Added borrow information** - Shows stock availability for shorting
4. **Live terminal display** - Real-time updating table with rich formatting
5. **Continuous scanning** - Automatic refresh every N seconds
6. **Terminal window launcher** - Optional new window for display
7. **Updated CSV format** - New columns for previous close and borrow status

## Historical Mode

### Overview
Historical mode allows you to run the Gainers Scanner on **weekends or after market hours** to analyze stocks that crossed the threshold during **previous trading days**. This is perfect for:
- Weekend development and debugging
- Backtesting historical trading days
- Analyzing past gap-up events without requiring live market data

### Key Differences from Live Mode

| Feature | Live Mode | Historical Mode |
|---------|-----------|-----------------|
| **Current Price** | Required (from live market data) | Not required (uses last historical bar) |
| **Time Window** | time0 → now | time0 → (time0 + 1 day) at 16:00 ET |
| **Shortable Status** | Available (tick 46/89 from IBKR API) | Set to "N/A" |
| **Shortable Difficulty** | Available (tick 46 raw value) | Set to "N/A" |
| **Shortable Shares** | Available (tick 89 exact count) | Set to "N/A" |
| **Borrow Fee Rate** | Available (from multi-source enrichment) | Available (from multi-source enrichment) |
| **Fundamental Data** | Available (Float, Institutional %, etc. from yfinance) | Available (Float, Institutional %, etc. from yfinance) |
| **Use Case** | Real-time monitoring during market hours | Weekend/after-hours analysis of historical data |

### Usage

Enable historical mode by adding the `--historical-mode` flag:

```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-20/16:00" \
  --gap-percentage 40 \
  --client-id 702 \
  --output-dir ./runs/20-Nov-2025/ \
  --historical-mode \
  --no-new-window
```

### How It Works

1. **Time Window Calculation**:
   - Input: `--time0 "2025-11-20/16:00"`
   - Analysis Window: `2025-11-20/16:00 → 2025-11-21/16:00` (24 hours)
   - The script fetches historical data for this entire window

2. **Peak Detection**:
   - Finds the highest price within the historical window
   - Checks if peak crossed the `--gap-percentage` threshold
   - Records Checkpoint-B as the peak moment

3. **Current Price Substitution**:
   - Uses the **last available historical bar's close price** as the "current" price
   - No live market data connection needed

4. **Output**:
   - Stocks that crossed threshold are added to GusTradeTable
   - `live_percentage` shows gain from time0 to last historical price
   - `Shortable_Status`, `Shortable_Difficulty`, `Shortable_Shares` all set to "N/A" (not available in historical mode)

### Examples

#### Weekend Analysis (Friday → Saturday)
```bash
# Analyze stocks that gapped up on Friday (Nov 22)
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-22/09:30" \
  --gap-percentage 30 \
  --client-id 702 \
  --output-dir ./runs/22-Nov-2025/ \
  --historical-mode \
  --no-new-window
```

#### Multi-Day Historical Window
```bash
# Analyze from Wednesday 4 PM to Thursday 4 PM
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-20/16:00" \
  --gap-percentage 50 \
  --client-id 702 \
  --output-dir ./runs/20-Nov-2025/ \
  --historical-mode \
  --no-new-window
```

#### Testing and Development
```bash
# Small test with only 5 stocks for faster execution
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-21/10:00" \
  --gap-percentage 20 \
  --client-id 702 \
  --output-dir ./test/ \
  --historical-mode \
  --no-new-window \
  --max-stocks 5
```

### Limitations

1. **No Real-Time Shortable Data**: Shortable_Status, Shortable_Difficulty, and Shortable_Shares are set to "N/A" (IBKR doesn't provide historical borrow status)
2. **Borrow Fee Rates Available**: Borrow_Fee_Rate IS available via multi-source enrichment (FTP + iBorrowDesk)
3. **Fixed Time Window**: Always analyzes from time0 to (time0 + 1 day) at 16:00 ET
4. **No Peak Caching**: Historical mode doesn't use peak caching (always fetches fresh data)
5. **IB Gateway Required**: Still needs IB Gateway connection for historical data (but not live market data)

### When to Use Historical Mode

**Use Historical Mode When:**
- Running on weekends or after market close
- Backtesting historical trading days
- Developing and debugging without live data
- Analyzing specific historical events

**Use Live Mode When:**
- Monitoring stocks during market hours
- Need real-time shortable status (tick 46/89 data)
- Want continuous updates with live prices
- Trading in real-time

## Support
For issues or questions, check the log files in the `--log-dir` directory.
