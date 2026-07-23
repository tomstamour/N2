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
