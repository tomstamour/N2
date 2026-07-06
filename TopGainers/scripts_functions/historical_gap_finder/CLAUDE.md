# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

The Historical Gap Up Finder (HGUF) is a Python script that scans Interactive Brokers' historical database to identify gap-up events across stocks in a defined time period. A gap-up event occurs when a stock's price gaps up by a minimum percentage from the previous day's RTH close (Checkpoint-A at 16:00) to the next day's price at exactly 09:29 (Checkpoint-B).

**Part of GUS3 Trading System**: This is a component of the larger Gap Up Short (GUS3) algorithmic trading system. The parent project is located at `../../` and includes real-time scanners and enrichment tools. HGUF provides historical analysis capabilities.

## Running the Script

### Basic Usage

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "01-12-2025" "20-12-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 700 \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 6.5
```

### With Filters and Custom Time Windows

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "01-11-2025" "30-11-2025" \
  --minimum-gap-percentage 50 \
  --max-open-price 20.0 \
  --max-float 100.0 \
  --max-institution 40.0 \
  --candles-size 5 \
  --client-id 700 \
  --fetch-time-pre-gap 48.0 \
  --fetch-time-post-gap 24.0 \
  --output-dir ./hguf-tables
```

### Multi-Day Analysis (Extended Windows)

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "15-11-2025" "20-11-2025" \
  --minimum-gap-percentage 25 \
  --candles-size 15 \
  --client-id 700 \
  --fetch-time-pre-gap 48.0 \
  --fetch-time-post-gap 72.0
```

**Result**: Fetches 48h pre-gap (GUSday-2, GUSday-1) and 72h post-gap (GUSday+1, GUSday+2, GUSday+3).

### Custom Universe

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "15-11-2025" "20-11-2025" \
  --minimum-gap-percentage 25 \
  --universe-file ./my_watchlist.txt \
  --candles-size 15 \
  --client-id 700 \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 24.0
```

### Required Arguments

- `--time-window START END`: Date range in DD-MM-YYYY format (e.g., "25-11-2025" "10-12-2025")
- `--minimum-gap-percentage`: Minimum gap threshold percentage (e.g., 30 for 30%)
- `--candles-size`: Candle interval in minutes (valid: 1, 2, 3, 5, 10, 15, 20, 30, 60)
- `--client-id`: IBKR client ID (recommended: 700)
- `--fetch-time-pre-gap`: Hours to fetch before gap time (09:29). Supports decimals (e.g., 6.5 = 6h 30m)
- `--fetch-time-post-gap`: Hours to fetch after gap time (09:29). Supports decimals (e.g., 7.5 = 7h 30m)

### Common Optional Arguments

- `--max-open-price`: Maximum opening price filter (day-12 RTH open)
- `--max-float`: Maximum float in millions
- `--min-float`: Minimum float in millions
- `--max-institution`: Maximum institutional ownership % (0-100)
- `--min-institution`: Minimum institutional ownership % (0-100)
- `--output-dir`: Output directory (default: current directory)
- `--universe-file`: Stock universe file (default: stock_universe.txt)
- `--port`: IB Gateway port (4001 for live, 4002 for paper)
- `--verbose`: Enable verbose logging

## Architecture

### Four-Phase Processing Pipeline

**Phase 1: Gap Detection**
- Fetches daily historical data for each stock in universe file
- Identifies potential gap-up events by comparing previous day's RTH close to next day's price at 09:29
- Applies initial filtering (OTC exclusion, gap percentage threshold)

**Phase 2: Enrichment and Filtering**
- Fetches Float and Institution% from yfinance (24-hour cache)
- Applies user-defined fundamental filters
- Filters out events that don't meet criteria

**Phase 3: Detailed OHLC Data**
- For filtered gap events, fetches intraday bars using flexible time windows
- Time range: gap_time (09:29) - fetch_time_pre_gap hours to gap_time + fetch_time_post_gap hours
- Uses user-specified candle size
- Includes extended hours (ETH) data
- Automatically adds GUSday labels based on calendar dates

**Phase 4: Output Generation**
- Calculates GAP% (pre-market only), Push% (RTH only), and Low% (RTH only) metrics
- Identifies MAX marker (highest Push% per GUE) and MIN marker (lowest Low% per GUE)
- Writes tab-delimited HGUF-table file with GUSday column for day-relative filtering

### Threading and State Management

- Uses `app_state` dict for thread-safe cross-thread communication
- Threading events for synchronization: `connection_ready`, `connection_lost`, `shutdown`
- ThreadPoolExecutor for parallel processing (not currently used but available)
- IBKR API client runs in separate thread via `client.run()`

### IBKR API Integration

- `HGUFWrapper`: Handles IBKR API callbacks (error handling, historical data)
- `HGUFClient`: IBKR API client wrapper
- `HGUFApp`: Combined wrapper/client application
- Request management via `pending_requests` set and `request_map` dict
- Auto-incrementing request IDs and GUE IDs

## Key Technical Concepts

### Gap-Up Event (GUE) Definition

- **Checkpoint-A (Day-11)**: Previous day RTH close at 16:00
- **Checkpoint-B (Day-12)**: Next day price at exactly 09:29
- **Gap%**: `(checkpoint_b_price - day11_close) / day11_close × 100`

The GUE uses two fixed time checkpoints for precise gap measurement, rather than scanning for the maximum price across a time range.

### Output Metrics

**GAP% Calculation**:
- Time range: Day-12 pre-market (04:00-09:29)
- Formula: `(bar_high - day11_close) / day11_close × 100`
- Only shown during pre-market hours
- Uses bar high to capture maximum gap reached

**Push% Calculation**:
- Time range: Day-12 RTH (09:30-16:00)
- Formula: `(bar_close - day12_open) / day12_open × 100`
- Only shown during RTH hours
- day12_open = first RTH candle open at 09:30

**MAX Marker**:
- Identifies single row with highest Push% per GUE
- Marked with string "max" in MAX column

### Output Format (HGUF-table)

Tab-delimited file: `HGUF-table_<start_date>_to_<end_date>.txt`

**Columns**:
- Symbol, GUE (Gap-Up Event ID), Time, O, H, L, C, Volume
- GAP% (pre-market only), Push% (RTH only), MAX (highest Push% marker)
- Float (millions), Institution% (percentage)

**Row Organization**:
- Grouped by GUE ID
- Ordered by Time within each GUE
- All rows for same GUE share same GUE ID, Symbol, Float, Institution%

### Stock Universe File

**Format**: `stock_universe.txt` (or custom via `--universe-file`)
- One symbol per line
- Lines starting with `#` are comments
- Symbols case-insensitive (converted to uppercase)

**Production files**:
- `stock_universe_12k.txt`: 11,715 US stocks from NASDAQ, NYSE, AMEX
- `test_stock_universe.txt`: Small test set for development

### OTC Detection

Multi-layer detection system (reuses logic from parent GUS3 project):
- Layer 1: IBKR exchange codes (PINK, OTCBB, OTCMKTS, OTC, EXPERT, SCM, VENTURE, GREY)
- Layer 2: yfinance exchange data (OTCMKTS, PINK, PNK, OTC, OTCBB, OTCQB, OTCQX)
- OTC stocks automatically excluded before gap detection

### yfinance Caching

- 24-hour TTL cache for Float and Institution% data
- Stored in `app_state["yfinance_cache"]`
- Reduces API calls and improves performance

## Directory Structure

```
historical_gap_finder/
├── Historical_Gap_Up_Finder.py    # Main script
├── HGUF_README.md                 # Comprehensive documentation
├── GEMINI.md                      # AI-generated docs (Gemini)
├── CLAUDE.md                      # This file (Claude)
├── usefull_HGUF_commands.sh       # Example commands
├── stock_universe.txt             # Small universe file
├── stock_universe_12k.txt         # Production universe (11,715 stocks)
├── test_stock_universe.txt        # Test universe
├── hguf-tables/                   # Output directory
│   └── HGUF-table_*.txt           # Generated tables
└── logs/                          # Log files
    └── HGUF-log-*.txt
```

## Dependencies

```bash
pip install rich pandas pytz ibapi yfinance
```

## IB Gateway Requirements

- IB Gateway or TWS must be running
- Default ports: 4001 (live), 4002 (paper)
- API connections enabled in IB Gateway settings
- Use unique client ID (recommended: 700)

## Performance Considerations

### IBKR API Rate Limits

- Conservative throttling: 20 requests/second (0.05s delay)
- Historical data limit: ~60 requests/second

### Execution Time Estimates

- 10 stocks, 1 week: ~2-5 minutes
- 100 stocks, 2 weeks: ~20-40 minutes
- 1000 stocks, 1 month: ~4-8 hours

### Two-Phase Optimization

Phase 1 fetches lightweight daily bars for all symbols, then Phase 2 only fetches detailed intraday data for confirmed gaps (~99% reduction in data requests).

## Error Handling

### Connection Errors

- Fatal codes: 504, 1100, 1101, 1102, 1300 → script exits
- Connection timeout: 15 seconds
- Informational codes: 2104, 2106, 2158, 2107, 2119, 2176, 162

### Historical Data Errors

- "No data available" (error 162): Stock skipped, logged as warning
- Timeouts: 10 seconds (daily), 20 seconds (intraday)
- Pacing violations: Logged as warning

## Timezone Handling

- All times use US/Eastern timezone (pytz)
- IBKR API expects US/Eastern timestamps
- Output times formatted as YYYY-MM-DD HH:MM:SS

## Logging

- Default location: `../../logs/hguf_YYYYMMDD_HHMMSS.log`
- Custom location: Use `--log` argument
- Levels: INFO (default), DEBUG (with `--verbose`)
- Console handler + File handler with different formatters
