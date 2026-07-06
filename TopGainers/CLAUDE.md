# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

GUS3 (Gap Up Short - Algorithmic Trading System) is a Python-based trading system that identifies and tracks stocks with significant price gains for the "Gap Up Short" trading strategy. The system uses Interactive Brokers (IBKR) API to scan markets, retrieve historical data, and monitor real-time prices.

**Trading Strategy**: The Gap Up Short strategy involves shorting stocks that show significant price increases from a baseline time, anticipating a price reversal.

## Architecture

### Core Pipeline Components

The system operates as a multi-stage pipeline located in `scripts_functions/`:

1. **Gainers Scanner Script (GSS)** - `gainers_scanner_scripts/Gainers_Scanner_Script.py`
   - Scans for stocks crossing gain thresholds from a time0 checkpoint
   - Tracks stocks using "Checkpoint-A" (time0 baseline) and "Checkpoint-B" (threshold crossing moment)
   - Outputs tab-delimited GusTradeTable files (e.g., `GusTradeTable_19nov2025.txt`)
   - Supports accumulative tracking with live percentage updates
   - Displays real-time results using Rich library tables

2. **Fundamentals Data Fetcher (FDF)** - `fundamentals_data_fetcher/fundamentals_data_fetcher.py`
   - Enriches GusTradeTable with fundamental metrics from IBKR API
   - Critical metrics: Float, Institutional %, Short Interest, Financial Health
   - Tab-delimited format compatible with GTT files
   - CLI-driven with --input and --client-id arguments
   - Documentation: `fundamentals_data_fetcher/FDF_README.md`

3. **Borrow Fee Fetch Functions** - `borrow_fee_fetch_functions/`
   - Multi-source borrow fee data enrichment system
   - **FTP Fetcher**: Downloads IBKR's official borrow fee data from FTP server
   - **iBorrowDesk Fetcher**: Fetches data from iBorrowDesk's free JSON API
   - **Orchestrator**: Coordinates multiple sources, adds BF1, BF2, BF_BEST, BF_Source columns
   - Automatically called by GSS after scanner completion
   - Cache files stored in `borrow_fee_fetch_functions/cache/`

4. **Stock Sector Finder (SSF)** - `Stock_Sector_Finder.py`
   - Enriches scanner results with sector/industry information
   - Uses yfinance API for sector data

5. **Trend Verifier (TVR)** - `trend_verifier/Trend_Verifier.py`
   - Enriches GusTradeTable with trend slope analysis
   - Calculates 1-year, 6-month, and 3-month trend slopes using linear regression
   - Adds columns: 1yrT, 6moT, 3moT (positioned after BF_BEST, before LastUpdate)
   - CLI-driven with caching (24-hour TTL)
   - Automatically called by GSS after borrow fee enrichment
   - Documentation: `trend_verifier/TREND_VERIFIER_README.md`

6. **Previous Day Volume Calculator (PDV)** - Integrated in `Gainers_Scanner_Script.py`
   - Calculates volume ratio (xVol) comparing date-A volume to 10-day average
   - Formula: xVol = (date-A volume) / (average of 10 prior days)
   - Uses yfinance API for daily volume data
   - Adds column: xVol (positioned after BF_BEST, before Trade)
   - Integrated as 6th trade decision criterion
   - 24-hour caching to minimize API calls
   - Documentation: `documentation/PDV_README.md`

### Time-based Execution

- **timer.sh** - Scheduling utility for executing scripts at specific times
  - Supports both script files and command-line execution
  - Shows countdown timer in foreground
  - Format: `./timer.sh --time HH:MM:SS --execute <script|command> [args]`

### Directory Structure

```
GUS3/
├── scripts_functions/          # Core pipeline scripts
│   ├── gainers_scanner_scripts/
│   │   ├── Gainers_Scanner_Script.py   # Main scanner
│   │   ├── test_gainers_scanner_args.py # Argument tests
│   │   ├── GAINERS_SCANNER_README.md
│   │   └── GSS_TIME0_README.md
│   ├── fundamentals_data_fetcher/
│   │   ├── fundamentals_data_fetcher.py # Fundamental metrics enrichment
│   │   └── FDF_README.md
│   ├── borrow_fee_fetch_functions/
│   │   ├── borrow_fee_ftp_fetcher.py   # IBKR FTP borrow fee fetcher
│   │   ├── borrow_fee_iborrowdesk_fetcher.py # iBorrowDesk API fetcher
│   │   ├── borrow_fee_orchestrator.py  # Multi-source orchestrator
│   │   ├── stock_borrow_fees_data_sources.txt # Documentation
│   │   └── cache/                      # Borrow fee cache files
│   │       ├── borrow_fees.csv
│   │       └── iborrowdesk_cache.csv
│   ├── trend_verifier/
│   │   ├── Trend_Verifier.py           # Trend slope enrichment
│   │   ├── TREND_VERIFIER_README.md
│   │   ├── cache/                      # Trend slopes cache
│   │   │   └── trend_slopes_cache.csv
│   │   └── logs/                       # Trend verifier logs
│   ├── Stock_Sector_Finder.py
│   └── timer.sh
├── runs/                       # Daily run outputs (organized by date)
│   └── DD-MMM-YYYY/           # e.g., 19-Nov-2025/
│       ├── GusTradeTable_DDmmmYYYY.txt
│       └── *.log
├── logs/                       # Application logs
├── output/                     # Additional output files
└── gus-launcher.sh            # Main launcher script
```

## Common Development Tasks

### Running the Gainers Scanner

The scanner is command-line driven (no config files):

```bash
# Basic usage
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-19/09:45" \
  --gap-percentage 50 \
  --client-id 300

# With custom output and logging
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-19/09:30" \
  --gap-percentage 5 \
  --client-id 702 \
  --output-dir ./runs/$(date +%d-%b-%Y) \
  --log ./runs/$(date +%d-%b-%Y)/scanner.log \
  --no-new-window

```

**Required arguments:**
- `--time0`: Reference time in format `YYYY-MM-DD/HH:MM` (US/Eastern timezone)
- `--gap-percentage`: Minimum gain threshold percentage
- `--client-id`: Unique IB Gateway client ID

**Common optional arguments:**
- `--output-dir`: Directory for GusTradeTable output (default: current directory)
- `--log`: Log file path (default: auto-generated in ./logs/)
- `--port`: IB Gateway port (4001 for live, 4002 for paper)
- `--host`: IB Gateway host (default: 127.0.0.1)
- `--no-new-window`: Run in current terminal instead of new window
- `--refresh-interval`: Seconds between scans (default: 3)
- `--max-stocks`: Maximum stocks to scan (default: 50)
- `--min-price`: Minimum stock price filter (default: 0.50)
- `--discard-otc`: Discard OTC stocks from trade decisions (YES/NO, default: NO)

**Kelly Criterion Position Calculator arguments (optional):**
- `--cash-to-play`: Total dollar amount for position calculations (optional, feature disabled if not provided)
- `--max-tradeCash-proportion-per-stock`: Max proportion per stock, 0-1 (default: 0.33)

**Note:** Kelly Calculator requires Trade Decision arguments to function. See `KELLY_CALCULATOR.md` for details.

### Kelly Criterion Position Calculator

**Optional feature** that calculates position sizes for stocks meeting trade criteria.

**Arguments:**
- `--cash-to-play`: Total dollar amount for position calculations (optional)
- `--max-tradeCash-proportion-per-stock`: Max proportion per stock, 0-1 (default: 0.33)

**Columns Added:**
- `KellyPosition`: Number of shares (integer)
- `TradeCash($)`: Allocated cash amount (2 decimals)

**Calculation Logic:**
1. Count stocks with Trade="YES"
2. Base allocation = cash_to_play ÷ yes_count
3. Apply cap = MIN(base allocation, cash_to_play × max_proportion)
4. Position = FLOOR(capped_cash ÷ stock_price)

**Example:**
```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-27/09:30" \
  --gap-percentage 10 \
  --client-id 300 \
  --gap_larger_then 15 \
  --institution_smaller_then 40 \
  --float_smaller_then 100 \
  --price_smaller_then 50 \
  --borrow_fee_smaller_then 5.0 \
  --xVolume-smaller-then 3.0 \
  --cash-to-play 10000 \
  --max-tradeCash-proportion-per-stock 0.25
```

**Documentation:** See `scripts_functions/gainers_scanner_scripts/KELLY_CALCULATOR.md`

### Using the Main Launcher

```bash
# Edit gus-launcher.sh to set parameters, then run:
./gus-launcher.sh
```

The launcher uses timer.sh to schedule directory creation and scanner execution.

### Enriching with Fundamentals Data

After running GSS, enrich the GusTradeTable with fundamental metrics:

```bash
# Basic usage - overwrites original GTT file
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400

# Custom output file
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --output ./runs/19-Nov-2025/GusTradeTable_enriched.txt \
  --client-id 400 \
  --log ./runs/19-Nov-2025/fdf.log

# Skip market data for faster execution
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400 \
  --no-market-data

# Paper trading (port 4002)
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400 \
  --port 4002
```

**Required arguments:**
- `--input`: Path to GusTradeTable file
- `--client-id`: Unique IB Gateway client ID (use different ID from GSS)

**Common optional arguments:**
- `--output`: Output file path (default: overwrites input file)
- `--log`: Log file path (default: auto-generated in ./logs/)
- `--port`: IB Gateway port (4001 for live, 4002 for paper)
- `--host`: IB Gateway host (default: 127.0.0.1)
- `--no-market-data`: Skip real-time market data (faster)
- `--verbose`: Enable verbose logging

**Metrics Added:**
- **Critical for shorting**: Float, Institutional_Pct, Short_Interest, Short_Interest_Ratio, Shortable_Shares, Borrow_Fee_Rate
- **Financial health**: Net_Income_TTM, Revenue_TTM, Profit_Margin, ROE, Debt_To_Equity
- **Valuation**: Market_Cap, Shares_Outstanding, EPS, PE_Ratio, Beta
- **Market data** (optional): FDF_Current_Price, FDF_Volume, FDF_Bid, FDF_Ask, FDF_Timestamp

### Enriching with Trend Analysis

The Trend Verifier is **automatically called by GSS** after borrow fee enrichment. Manual usage is typically not needed.

**Manual enrichment** (if needed):

```bash
# Basic usage
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 600

# With custom cache location
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 600 \
  --cache ./custom_cache/trends.csv

# Paper trading (port 4002)
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 600 \
  --port 4002
```

**Required arguments:**
- `--input`: Path to GusTradeTable file
- `--client-id`: Unique IB Gateway client ID (use 600 for trend verifier)

**Common optional arguments:**
- `--cache`: Path to trend cache file (default: `./scripts_functions/trend_verifier/cache/trend_slopes_cache.csv`)
- `--cache-ttl`: Cache TTL in seconds (default: 86400 = 24 hours)
- `--port`: IB Gateway port (4001 for live, 4002 for paper)
- `--host`: IB Gateway host (default: 127.0.0.1)
- `--log`: Log file path (default: auto-generated in trend_verifier/logs/)
- `--verbose`: Enable verbose logging

**Columns Added:**
- **1yrT**: 1-year trend slope (2 decimals, e.g., "2.23" or "-0.87")
- **6moT**: 6-month trend slope (2 decimals)
- **3moT**: 3-month trend slope (2 decimals)
- Positioned after BF_BEST, before LastUpdate

### Complete Pipeline Example

```bash
# Step 1: Run Gainers Scanner (automatic enrichment)
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-19/09:30" \
  --gap-percentage 5 \
  --client-id 300 \
  --output-dir ./runs/$(date +%d-%b-%Y)

# GSS automatically performs these enrichments:
#   - Borrow fee data (FTP + iBorrowDesk)
#   - Trend slope analysis (1Y, 6M, 3M)

# Step 2: Enrich with Fundamentals (optional, manual)
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input ./runs/$(date +%d-%b-%Y)/GusTradeTable_$(date +%dmm%Y).txt \
  --client-id 400

# Step 3: Add Sector Information (optional, manual)
# python3 scripts_functions/Stock_Sector_Finder.py --input <GTT file>
```

**Client ID Assignment:**
- GSS: 300
- FDF: 400
- Sector Finder: 500
- Trend Verifier: 600

### Testing
#### Test script shall be located here:
./claude_test_script/

```bash
# Test argument parsing
python3 scripts_functions/gainers_scanner_scripts/test_gainers_scanner_args.py

# Test FDF help
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py --help
```

## Key Technical Concepts

### Time0 and Checkpoint System

- **Checkpoint-A (time0)**: User-defined baseline time for price measurement
- **Checkpoint-B**: Automatically detected threshold crossing moment
- **Live Percentage**: Continuously updated gain percentage from time0

### GusTradeTable (GTT) Format

Tab-delimited output with columns:
- **Symbol**: Stock ticker symbol
- **CP-A(t)**: Checkpoint-A TIME (format: YYYY-MM-DD/HH:MM)
- **CP-A($)**: Checkpoint-A PRICE
- **CP-B(t)**: Checkpoint-B TIME
- **CP-B($)**: Checkpoint-B PRICE
- **Gap(%)**: Gap percentage at threshold crossing
- **GapL(%)**: Live gap percentage (current gain from time0)
- **PriceL($)**: Live stock price used for GapL(%) calculation (refreshes with each scan cycle)
- **Sect**: Sector (from yfinance)
- **Indus**: Industry (from yfinance)
- **OTC**: OTC status ("OTC" if over-the-counter, "-" if major exchange). Detection based on IBKR contract.primaryExchange field. OTC exchanges include: PINK, OTCBB, OTCMKTS, OTC
- **ShortStatus**: IBKR-style shortable status (HIGH_AVAIL/MODERATE_AVAIL/LOW_AVAIL/NOT_SHORTABLE/N/A)
- **ShortDiff**: Shortable difficulty (Raw tick 46 value: 0.0-3.0+)
- **ShortableShares**: Exact shortable shares count (Raw tick 89)
- **Shortable**: Margin eligibility via WhatIf order preview (YES/NO/N/A) - definitive shortability status
- **Float(M$)**: Float in millions of dollars (from yfinance)
- **NetIncome(M$)**: Net income TTM in millions of dollars (from yfinance)
- **Instit(%)**: Institutional percentage (from yfinance)
- **BorrowFee**: Borrow fee rate from IBKR API
- **BF1**: Borrow fee from IBKR FTP (added by enrichment)
- **BF2**: Borrow fee from iBorrowDesk (added by enrichment)
- **BF_BEST**: Best borrow fee from available sources (added by enrichment)
- **xVol**: Volume ratio - (date-A volume) / (10-day average volume) from PDV calculator (2 decimals, e.g., "2.35" for 235% of average)
- **Trade**: Trade decision (YES/NO/N/A from evaluate_trade_decision)
- **KellyPosition**: Number of shares for position (from Kelly Calculator, optional)
- **TradeCash($)**: Allocated cash amount (from Kelly Calculator, optional)
- **LastUpdate**: Last update timestamp

**Note:** Trend enrichment columns (1yrT, 6moT, 3moT) are added by the Trend Verifier after the GTT is written, positioned after BF_BEST in the enriched file.

**Important Notes:**
- **Value Conversions**: Float(M$) and NetIncome(M$) are stored in millions for readability
- **Live Mode**: ShortStatus, ShortDiff, ShortableShares, Shortable populated from IBKR API during market hours
- **Historical Mode**: All shortable columns show "N/A" (IBKR doesn't provide historical borrow status or margin data)
- **Two-Tier Shortability Detection**:
  - **Tier 1** (ShortStatus/ShortDiff/ShortableShares): SLB availability via Tick 46/89 - shows if IBKR has shares to lend
  - **Tier 2** (Shortable): Margin eligibility via WhatIf order preview - definitive shortability check for your account
  - **Performance**: WhatIf adds ~50-200ms per stock (~7-10 seconds for 50 stocks)
- **OTC Detection**: Multi-layer OTC detection system. Layer 1: IBKR contract exchange codes (EXPERT, SCM, PINK, OTCBB, etc.). Layer 2: yfinance exchange data fallback. Layer 3: Symbol patterns (logging only). Detection is cached per stock (no performance impact). See Gainers Scanner README for full details
- **Borrow Fee Enrichment**: BF1, BF2, and BF_BEST columns are added by the multi-source borrow fee orchestrator
- **Trend Enrichment**: 1yrT, 6moT, 3moT columns are added by the trend verifier using linear regression on IBKR historical data (cached for 24 hours)
- **Volume Enrichment**: xVol column is calculated by the PDV (Previous Day Volume) calculator using yfinance API (cached for 24 hours). Only populated in date-A mode; shows "N/A" in time0 mode.
- **Kelly Calculator**: KellyPosition and TradeCash($) columns are added when --cash-to-play argument is provided. Shows N/A if feature is disabled or stock has Trade="NO"

### IBKR API Integration

All scripts use `ibapi` (Interactive Brokers Python API):
- Connection requires IB Gateway or TWS running
- Each script needs unique `client_id`
- Historical data requests use 1-minute bars for precision
- Market data includes bid/ask spreads and shortable shares (generic tick 236)
- Scanner uses `TOP_PERC_GAIN` scan code

### Parallel Processing

The Gainers Scanner uses ThreadPoolExecutor for batch processing:
- Processes stocks in batches of 20
- Up to 20 concurrent workers per batch
- Reduces total scan time significantly

### Shortable Status Interpretation (IBKR-Style)

Based on IBKR's tick 46 (shortable difficulty) value:
- **HIGH_AVAIL**: > 2.5 (at least 1,000 shares available - equivalent to IBKR's light green indicator)
- **MODERATE_AVAIL**: 1.5-2.5 (shares locatable but fewer available - equivalent to IBKR's dark green)
- **LOW_AVAIL**: 0-1.5 (hard to borrow - limited availability)
- **NOT_SHORTABLE**: ≤ 0 (not available for shorting at IBKR)
- **N/A**: No data available from IBKR API

**IBKR API Data Sources:**
- **Tick 46** (Shortable Difficulty): Float value 0.0-3.0+ captured via `tickGeneric()` callback
- **Tick 89** (Shortable Shares Count): Integer count captured via `tickSize()` callback
- **Requested via**: `reqMktData()` with `genericTickList='236'`

**Old Format Migration:**
- Previous versions used custom labels: "Easy", "Available", "Hard"
- New implementation uses IBKR-style terminology for technical accuracy
- Raw tick values now preserved in separate columns for transparency

## Python Dependencies

```bash
pip install rich pandas pytz ibapi yfinance numpy
```

## IB Gateway Requirements

- IB Gateway or TWS must be running before executing scripts
- Default ports:
  - Live trading: 4001
  - Paper trading: 4002
- Enable API connections in IB Gateway settings
- Each concurrent script needs a unique client ID

## Output Files

### Documentation files should be place here:
./documentation
### Daily Run Directory

Created by launcher: `runs/DD-MMM-YYYY/`
- Contains all outputs for a single trading day
- Includes GusTradeTable and log files

### Log Files

- Scanner: Auto-generated as `logs/scanner_YYYYMMDD_HHMMSS.log` or custom via `--log`
- Sector Finder: `logs/sector_finder_YYYYMMDD_HHMMSS.log`
- Trend Verifier: `logs/trend_verifier_YYYYMMDD_HHMMSS.log`

## Development Notes

### Working with the Scanner

- The scanner runs continuously with a refresh loop (default 3 seconds)
- Uses Rich library's Live display for real-time terminal updates
- Supports resume from existing GTT files if restarted
- Can launch in new terminal window (requires gnome-terminal, xterm, konsole, or xfce4-terminal)

### Time Handling

- All times use US/Eastern timezone (pytz)
- time0 must be in the past (not future)
- Historical data requests handle extended hours (useRTH=0)

### Error Handling

- IB error codes 2104, 2106, 2158, 2107, 2119, 2176, 162 are informational (logged as debug/info)
- Error code 162 ("Scanner subscription cancelled") is expected after cancelScannerSubscription()
- Market data status codes (2104, 2106, 2158) are logged as INFO for diagnostics
- Fatal connection errors: 504, 1100, 1101, 1102, 1300 trigger shutdown
- Connection timeout: 15 seconds

### Scanner Timeout Handling

**IBKR Scanner Service Availability:**
- IBKR's `TOP_PERC_GAIN` scanner has variable availability during early pre-market hours (4:00-7:30 AM EST)
- Scanner service may not respond before ~7:30 AM EST
- This is an IBKR infrastructure limitation, not a script issue

**Timeout Detection & Recovery:**
- Scanner timeout = IBKR never sends `scannerDataEnd()` callback
- Script distinguishes between:
  - **Service timeout**: IBKR not responding (no callback)
  - **0 legitimate results**: IBKR responds with scannerDataEnd but 0 stocks
- Tracks consecutive timeout count in `app_state["scanner_timeout_count"]`
- Auto-resets counter when service recovers

**Exponential Backoff:**
- Triggered after 3+ consecutive timeouts
- Backoff schedule: 3s (base) → 6s → 12s → 24s → 48s → 60s (max)
- Reduces API load during service outages
- Formula: `min(base_interval * 2^(timeout_count-2), 60)`

**User Notifications:**
- After 3+ timeouts, displays visible warning
- Pre-market warning (before 9:30 AM): Suggests waiting until 7:30 AM or using --historical-mode
- Regular hours warning: Indicates IBKR service issue
- Shows "Scanner service recovered" message when service resumes

**Best Practices:**
- Start scans after 7:30 AM EST for reliable scanner availability
- Use --historical-mode during early pre-market if needed
- Monitor logs for "Scanner timeout" vs "0 legitimate results" distinction

### Threading

- Each script uses threading for IB API client loop
- Global `app_state` dict for cross-thread communication
- Threading Events for synchronization (connection_ready, scanner_complete, shutdown)
