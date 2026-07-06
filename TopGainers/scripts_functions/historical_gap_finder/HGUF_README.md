# Historical Gap Up Finder (HGUF) - README

## Overview

The Historical Gap Up Finder (HGUF) is a Python script that scans Interactive Brokers' historical database to identify gap-up events across all stocks in a defined time period. It provides detailed OHLC data for each gap event with enriched fundamental metrics.

## What is a Gap-Up Event (GUE)?

A **Gap-Up Event (GUE)** occurs when a stock's price gaps up by a minimum percentage from the previous day's RTH close (Checkpoint-A at 16:00) to the next day's price at exactly 09:29 (Checkpoint-B).

**Example**:
- Day-11 (Nov 24): Stock closes at $5.00 (RTH close at 16:00)
- Day-12 (Nov 25): Stock trades at $7.50 in pre-market (at 09:29)
- **Gap Percentage**: ((7.50 - 5.00) / 5.00) × 100 = **50% gap**

## Features

- **Comprehensive Scanning**: Scans all stocks from a predefined universe file
- **Multi-Day Coverage**: Analyzes entire time windows (weeks/months)
- **Gap Detection**: Identifies gaps using fixed time checkpoints (Day-11 16:00 → Day-12 09:29)
- **Flexible Time Windows**: User-defined pre-gap and post-gap time ranges (in hours) for fetching historical data
- **GUSday Labeling**: Automatically labels each bar by calendar day relative to gap day (GUSday-1, GUSday, GUSday+1, etc.)
- **Detailed OHLC Data**: Fetches intraday bars with configurable time range (including ETH)
- **Fundamental Enrichment**: Adds Float and Institutional Ownership % from yfinance
- **Flexible Filtering**:
  - Price filters (max opening price)
  - Float filters (min/max float in millions)
  - Institution filters (min/max institutional ownership %)
  - Automatic OTC exclusion
- **Progress Indicators**: Real-time progress bars showing scan status
- **Tab-Delimited Output**: Compatible with Excel/spreadsheet tools

## Requirements

### Python Dependencies

```bash
pip install rich pandas pytz ibapi yfinance
```

### IB Gateway Requirements

- IB Gateway or TWS running
- Default ports:
  - Live trading: 4001
  - Paper trading: 4002
- API connections enabled in IB Gateway settings
- Use unique client ID (recommended: 700)

## Installation

1. The script is located at: `scripts_functions/historical_gap_finder/Historical_Gap_Up_Finder.py`
2. Ensure `stock_universe.txt` is populated with US stock symbols (one per line)
3. Make script executable: `chmod +x Historical_Gap_Up_Finder.py`

## Usage

### Basic Usage

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "25-11-2025" "10-12-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 700 \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 6.5
```

**Note**: `--fetch-time-pre-gap 24.0` and `--fetch-time-post-gap 6.5` replicates the old default behavior (day-11 09:30 to day-12 16:00).

### With Filters

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
  --output-dir ./runs/gap_finder
```

### Multi-Day Analysis (Extended Time Windows)

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "15-11-2025" "20-11-2025" \
  --minimum-gap-percentage 25 \
  --candles-size 15 \
  --client-id 700 \
  --fetch-time-pre-gap 48.0 \
  --fetch-time-post-gap 72.0
```

**Result**: This fetches 48 hours before gap time (showing GUSday-2, GUSday-1) and 72 hours after (showing GUSday+1, GUSday+2, GUSday+3).

### Custom Universe File

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

### Paper Trading Mode

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "25-11-2025" "10-12-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 700 \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 6.5 \
  --port 4002
```

## Command-Line Arguments

### Required Arguments

| Argument | Type | Description |
|----------|------|-------------|
| `--time-window START END` | Two dates | Time window in DD-MM-YYYY format (e.g., "25-11-2025" "10-12-2025") |
| `--minimum-gap-percentage` | float | Minimum gap percentage threshold (e.g., 30 for 30%) |
| `--candles-size` | int | Candle size in minutes (valid: 1, 2, 3, 5, 10, 15, 20, 30, 60) |
| `--client-id` | int | IBKR client ID (use unique ID, recommended: 700) |
| `--fetch-time-pre-gap` | float | Hours to fetch before gap time (09:29). Supports decimals (e.g., 6.5 = 6h 30m) |
| `--fetch-time-post-gap` | float | Hours to fetch after gap time (09:29). Supports decimals (e.g., 7.5 = 7h 30m) |

### Optional Price/Fundamental Filters

| Argument | Type | Description |
|----------|------|-------------|
| `--max-open-price` | float | Maximum opening price filter (day-12 RTH open at 09:30) |
| `--max-float` | float | Maximum float in millions (e.g., 100 = 100M shares) |
| `--min-float` | float | Minimum float in millions |
| `--max-institution` | float | Maximum institutional ownership % (0-100) |
| `--min-institution` | float | Minimum institutional ownership % (0-100) |

### Other Optional Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--output-dir` | path | Current directory | Output directory for HGUF-table |
| `--universe-file` | path | `stock_universe.txt` | Stock universe file path |
| `--log` | path | Auto-generated | Log file path |
| `--port` | int | 4001 | IB Gateway port (4001=live, 4002=paper) |
| `--host` | string | 127.0.0.1 | IB Gateway host |
| `--verbose` | flag | False | Enable verbose logging |

## Output Format (HGUF-table)

The script generates a tab-delimited file named `HGUF-table_<start_date>_to_<end_date>.txt`.

### Columns

| Column | Description |
|--------|-------------|
| **Symbol** | Stock ticker symbol |
| **GUE** | Gap-Up Event ID (integer, auto-incremented starting from 1) |
| **Time** | Candle timestamp (YYYY-MM-DD HH:MM:SS US/Eastern) |
| **O** | Open price (4 decimals) |
| **H** | High price (4 decimals) |
| **L** | Low price (4 decimals) |
| **C** | Close price (4 decimals) |
| **Volume** | Candle volume (integer) |
| **GAP%** | Gap percentage from day-11 close (2 decimals, only during day-12 pre-market, NA otherwise) |
| **Push%** | Push percentage from day-12 RTH open (2 decimals, only during day-12 RTH, NA otherwise) |
| **Low%** | Low percentage from day-12 RTH open (2 decimals, only during day-12 RTH, NA otherwise) |
| **MAX** | String "max" where Push% was highest for that GUE (once per GUE), NA otherwise |
| **MIN** | String "min" where Low% was lowest for that GUE (once per GUE), NA otherwise |
| **GUSday** | Day label relative to gap day: 'GUSday' (gap day), 'GUSday-1' (previous day), 'GUSday+1' (next day), etc. |
| **Float** | Stock float in millions (2 decimals, from yfinance) |
| **Institution%** | Institutional ownership % (2 decimals, from yfinance) |

### Example Output

```
Symbol	GUE	SGUE	Time	O	H	L	C	Volume	GAP%	Push%	Low%	MAX	MIN	GUSday	Float	Institution%
TSLA	1	1	2025-11-24 09:30:00	150.2500	152.3400	149.8000	151.5000	1200000	NA	NA	NA	NA	NA	GUSday-1	250.50	45.67
TSLA	1	1	2025-11-24 09:40:00	151.5000	153.1000	151.2000	152.8000	980000	NA	NA	NA	NA	NA	GUSday-1	250.50	45.67
TSLA	1	1	2025-11-25 04:00:00	198.5000	201.2000	197.8000	200.0000	850000	32.45	NA	NA	NA	NA	GUSday	250.50	45.67
TSLA	1	1	2025-11-25 09:30:00	205.0000	207.5000	204.2000	206.5000	1500000	NA	0.73	-0.12	NA	NA	GUSday	250.50	45.67
TSLA	1	1	2025-11-25 10:00:00	206.5000	210.0000	205.8000	209.2000	1350000	NA	2.05	0.28	max	NA	GUSday	250.50	45.67
TSLA	1	1	2025-11-26 10:00:00	208.0000	211.5000	207.2000	210.5000	1100000	NA	NA	NA	NA	NA	GUSday+1	250.50	45.67
```

**Note**: The GUSday column shows:
- `GUSday-1`: Bars from Nov 24 (day before gap day)
- `GUSday`: Bars from Nov 25 (the gap day itself - day-12)
- `GUSday+1`: Bars from Nov 26 (day after gap day)

This labeling is based on calendar dates (00:00-23:59 boundaries), making it easy to filter and analyze data by relative day position.

### Row Ordering

- Rows are grouped by GUE ID
- Within each GUE, rows are ordered by Time ascending
- All rows for the same GUE share the same GUE ID, Symbol, Float, and Institution% values

### Value Formatting

- **GUE**: Integer (1, 2, 3, ...) - all OHLC rows for same gap event share same ID
- **GAP%**: Only shown during day-12 pre-market (04:00-09:29), NA otherwise
- **Push%**: Only shown during day-12 RTH (09:30-16:00), NA otherwise
- **MAX**: String "max" marks the row with highest Push% value (once per GUE)
- **Float**: In millions (e.g., 150.25 = 150.25M shares)

## Workflow

The script operates in 4 phases:

### Phase 1: Fetch Daily Bars and Detect Gap Events

1. Loads stock universe from `stock_universe.txt`
2. For each symbol:
   - Fetches daily bars covering time window
   - Checks for OTC status (filters out OTC stocks)
   - Detects gap events (consecutive day pairs)
   - For each potential gap:
     - Fetches pre-market bars (04:00-09:29)
     - Calculates gap percentage
     - If gap >= minimum-gap-percentage:
       - Fetches RTH open bar (09:30)
       - Applies max-open-price filter
       - Creates GUE record

### Phase 2: Enrich with Fundamentals and Apply Filters

1. For each detected gap event:
   - Fetches Float and Institution% from yfinance (24-hour cache)
   - Applies fundamental filters:
     - Float range filter (min-float, max-float)
     - Institution range filter (min-institution, max-institution)
   - Filters out events that don't meet criteria

### Phase 3: Fetch Detailed Intraday OHLC Data

1. For each filtered gap event:
   - Fetches intraday bars from day-11 09:30 to day-12 16:00
   - Uses user-specified candle size
   - Includes extended hours (ETH) between the two days
   - Calculates metrics:
     - **GAP%**: (bar_high - day11_close) / day11_close × 100 (pre-market only)
     - **Push%**: (bar_close - day12_open) / day12_open × 100 (RTH only)
     - **MAX**: Identifies row with highest Push% value

### Phase 4: Write HGUF-table

1. Writes all gap events to tab-delimited file
2. Sorted by GUE ID, then by Time
3. Displays summary statistics

## Stock Universe File

**Location**: `stock_universe.txt` (default, customizable via `--universe-file`)

**Format**:
- One symbol per line
- Lines starting with `#` are ignored (comments)
- Symbols are case-insensitive (converted to uppercase)

**Sample Content**:
```
# Sample universe file
AAPL
TSLA
NVDA
AMD
MSFT
```

**Production Usage**:
- Populate with ALL US stocks from NASDAQ, NYSE, AMEX exchanges
- Recommended sources:
  - NASDAQ FTP: ftp://ftp.nasdaqtrader.com/symboldirectory/
  - NYSE data feeds
  - Financial data providers (Polygon.io, Alpha Vantage)

## Performance Considerations

### IBKR API Rate Limits

- IBKR historical data limit: ~60 requests/second
- Script uses conservative throttling: 20 requests/second (0.05s delay)
- Large time windows + large universe = long execution time

### Two-Phase Optimization

1. **Phase 1**: Fetch daily bars for all symbols (lightweight, fast)
2. **Phase 2**: Only fetch intraday data for stocks with confirmed gaps (~99% reduction)

### Estimated Execution Times

- 10 stocks, 1 week: ~2-5 minutes
- 100 stocks, 2 weeks: ~20-40 minutes
- 1000 stocks, 1 month: ~4-8 hours

**Tip**: Start with small test runs before large scans.

## OTC Detection

The script automatically filters out OTC stocks using multi-layer detection:

1. **Layer 1**: IBKR contract exchange codes
   - PINK, OTCBB, OTCMKTS, OTC, EXPERT, SCM, VENTURE, GREY
2. **Layer 2**: yfinance exchange data
   - OTCMKTS, PINK, PNK, OTC, OTCBB, OTCQB, OTCQX

OTC stocks are excluded before gap detection to improve data quality.

## Logging

### Log Levels

- **INFO**: Major events (connection, phase completion, gap detection)
- **DEBUG**: Detailed request/response info (enable with `--verbose`)
- **WARNING**: Timeouts, missing data, filter exclusions
- **ERROR**: Fatal errors, connection failures

### Log File Location

- Default: `../../logs/hguf_YYYYMMDD_HHMMSS.log`
- Custom: Use `--log` argument

## Error Handling

### Connection Errors

- Fatal codes: 504, 1100, 1101, 1102, 1300 → script exits
- Connection timeout: 15 seconds

### Historical Data Errors

- "No data available" (error 162): Stock skipped, logged as warning
- Timeout (10 seconds for daily, 20 seconds for intraday): Request failed, stock skipped

### Rate Limiting

- Pacing violations detected: Logged as warning
- Script uses conservative 20 req/sec to avoid violations

## Troubleshooting

### "Failed to connect to IB Gateway"

- Verify IB Gateway/TWS is running
- Check port (4001 for live, 4002 for paper)
- Ensure API connections enabled in IB Gateway settings
- Try different client ID if current one is in use

### "No gap events found matching criteria"

- Lower `--minimum-gap-percentage` threshold
- Expand time window
- Remove price/fundamental filters to see unfiltered results
- Check if universe file contains active symbols

### "Pacing violation" warnings

- Script already throttles conservatively
- If frequent warnings, increase sleep delay in code (line 602, 901, 1023)
- IBKR may have stricter limits during market hours

### "No pre-market data for [date]"

- IBKR doesn't provide pre-market data for all dates/symbols
- Weekends/holidays have no trading data
- Some stocks may have limited extended hours data

## Example Use Cases

### 1. Find High-Percentage Gaps on Small-Cap Stocks

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "01-11-2025" "30-11-2025" \
  --minimum-gap-percentage 100 \
  --max-float 50.0 \
  --candles-size 10 \
  --client-id 700
```

### 2. Find Moderate Gaps with Low Institution%

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "01-10-2025" "31-10-2025" \
  --minimum-gap-percentage 30 \
  --max-institution 30.0 \
  --max-open-price 15.0 \
  --candles-size 5 \
  --client-id 700
```

### 3. Custom Watchlist Scan

Create `my_watchlist.txt`:
```
RBLX
SNAP
UBER
DASH
COIN
```

Run scan:
```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "15-11-2025" "20-11-2025" \
  --minimum-gap-percentage 25 \
  --universe-file ./my_watchlist.txt \
  --candles-size 15 \
  --client-id 700
```

## Integration with GUS3 Trading System

The HGUF complements the existing GUS3 components:

- **Gainers Scanner**: Real-time scanning vs. HGUF historical scanning
- **Fundamentals Data Fetcher**: Similar yfinance integration patterns
- **Trend Verifier**: Similar IBKR historical data fetching
- **Borrow Fee Fetchers**: Can be extended to add borrow fee data to HGUF output

**Future Enhancement**: Add borrow fee columns to HGUF-table by integrating borrow fee fetchers.

## Technical Notes

### Timezone Handling

- All times use US/Eastern timezone (pytz)
- IBKR API expects US/Eastern timestamps
- Output times are in US/Eastern

### Gap Detection Logic

```
Day-11 (previous day):
  - RTH close = daily bar close at 16:00

Day-12 (gap day):
  - Pre-market end = 09:29:00
  - Pre-market high = max(high) from all 1-min bars between 04:00-09:29

Gap% = (pre_market_high - day11_close) / day11_close × 100
```

### Metric Calculations

**GAP% Calculation**:
- Time range: Day-12 pre-market (04:00-09:29)
- Formula: `(bar_high - day11_close) / day11_close × 100`
- Only shown during pre-market hours
- Uses bar high to capture maximum gap percentage reached during each time period

**Push% Calculation**:
- Time range: Day-12 RTH (09:30-16:00)
- Formula: `(bar_close - day12_open) / day12_open × 100`
- Only shown during RTH hours
- day12_open = first RTH candle open price at 09:30

**MAX Marker**:
- Identifies the single row with highest Push% value per GUE
- Marked with string "max" in MAX column
- All other rows show "NA"

## Limitations

1. **Historical Data Availability**: IBKR historical data may not be complete for all symbols/dates
2. **Pre-Market Data**: Not all stocks have robust pre-market trading data
3. **OTC Detection**: Some OTC stocks may slip through if not properly tagged by IBKR/yfinance
4. **yfinance Reliability**: Float and Institution% data may be missing/outdated for some stocks
5. **Execution Time**: Large scans (1000+ symbols × 30+ days) can take hours

## Support and Feedback

For issues, enhancements, or questions:
- Check logs for detailed error messages
- Verify IB Gateway connection and API settings
- Test with small dataset first (10 stocks, 1 week)
- Review HGUF_README.md for usage guidance

## License

Part of the GUS3 (Gap Up Short - Algorithmic Trading System).
