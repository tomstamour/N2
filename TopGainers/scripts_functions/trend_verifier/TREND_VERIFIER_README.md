# Trend Verifier (TVR) - GUS3

## Overview

The Trend Verifier is a CLI-driven enrichment script that adds trend slope analysis to GusTradeTable (GTT) files. It calculates linear regression slopes for 1-year, 6-month, and 3-month periods using daily closing prices from IBKR historical data.

**Key Features:**
- CLI-driven with argparse (no config files)
- GTT-native tab-delimited format support
- In-place file modification (overwrites original GTT)
- Persistent caching with configurable TTL (default: 24 hours)
- Parallel processing (10 concurrent workers)
- Non-blocking integration with GSS
- Automatic orchestration by Gainers Scanner Script

## Columns Added

The script adds three columns to the GTT file, positioned **after BF_BEST and before LastUpdate**:

| Column | Description | Format | Example |
|--------|-------------|--------|---------|
| **1yrT** | 1-year trend slope | 2 decimals | `2.23`, `-0.87` |
| **6moT** | 6-month trend slope | 2 decimals | `1.45`, `-1.23` |
| **3moT** | 3-month trend slope | 2 decimals | `0.98`, `-0.45` |

**Value Interpretation:**
- **Positive values**: Upward trend (stock price increasing over time)
- **Negative values**: Downward trend (stock price decreasing over time)
- **N/A**: Insufficient data or calculation error

## Usage

### Basic Usage

```bash
python3 Trend_Verifier.py --input <GTT_file> --client-id <id>
```

### Common Examples

```bash
# Basic enrichment
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/21-Nov-2025/GusTradeTable.txt \
  --client-id 600

# With custom cache location
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/21-Nov-2025/GusTradeTable.txt \
  --client-id 600 \
  --cache ./custom_cache/trends.csv

# Paper trading account (port 4002)
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/21-Nov-2025/GusTradeTable.txt \
  --client-id 600 \
  --port 4002

# Verbose logging
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/21-Nov-2025/GusTradeTable.txt \
  --client-id 600 \
  --verbose
```

## Command-Line Arguments

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--input` | Path to GusTradeTable file (tab-delimited) |
| `--client-id` | IB Gateway client ID (use 600 for trend verifier) |

### Optional Arguments

| Argument | Default | Description |
|----------|---------|-------------|
| `--cache` | `./scripts_functions/trend_verifier/cache/trend_slopes_cache.csv` | Path to trend cache file |
| `--cache-ttl` | `86400` (24 hours) | Cache TTL in seconds |
| `--host` | `127.0.0.1` | IB Gateway host |
| `--port` | `4001` | IB Gateway port (4001=live, 4002=paper) |
| `--log` | Auto-generated | Log file path |
| `--verbose` | `False` | Enable verbose logging |

## Caching System

### Cache Structure

The cache is stored as a CSV file with the following structure:

```csv
symbol,trend_1y,trend_6m,trend_3m,cached_at,bars_count
AAPL,0.23,0.34,0.45,2025-11-24T10:30:00,252
TSLA,-0.12,-0.23,-0.34,2025-11-24T10:31:00,252
```

### Cache Behavior

- **TTL**: Default 24 hours (configurable via `--cache-ttl`)
- **Invalidation**: Automatic based on timestamp
- **Location**: `scripts_functions/trend_verifier/cache/trend_slopes_cache.csv`
- **Persistence**: Saved to disk after each run
- **Performance**: Reduces IBKR API calls significantly

### Cache Management

```bash
# View cache contents
cat scripts_functions/trend_verifier/cache/trend_slopes_cache.csv

# Clear cache
rm scripts_functions/trend_verifier/cache/trend_slopes_cache.csv

# Custom cache location
python3 Trend_Verifier.py --input <GTT> --client-id 600 \
  --cache /path/to/custom_cache.csv
```

## Integration with GSS

The Trend Verifier is automatically called by the Gainers Scanner Script (GSS) after borrow fee enrichment.

**Orchestration in GSS:**

```python
# In run_continuous_scan() and run_single_scan()
write_complete_gtt(tracked_stocks, gtt_path)
enrich_gtt_with_ftp_data(gtt_path)       # Borrow fee enrichment
enrich_gtt_with_trend_data(gtt_path)     # Trend enrichment (NEW)
```

**Key Points:**
- **Non-blocking**: GSS continues if trend enrichment fails
- **Automatic**: No manual intervention required
- **Client ID**: Uses 600 (separate from GSS's 300)
- **Timeout**: 300 seconds (5 minutes)

## Technical Details

### Trend Slope Calculation

The script uses **linear regression** (numpy's `polyfit`) to calculate trend slopes:

```python
# 1-Year: All available daily bars (up to 252 trading days)
slope_1y = np.polyfit(x_1y, close_prices, 1)[0]

# 6-Month: Last ~126 trading days
slope_6m = np.polyfit(x_6m, close_prices[-126:], 1)[0]

# 3-Month: Last ~63 trading days
slope_3m = np.polyfit(x_3m, close_prices[-63:], 1)[0]
```

**Requirements:**
- Minimum 63 daily bars required for calculation
- Uses daily closing prices only
- Regular trading hours (RTH) data only

### Parallel Processing

- **Workers**: 10 concurrent threads (ThreadPoolExecutor)
- **Request delay**: 50ms between requests to avoid rate limiting
- **Timeout**: 10 seconds per historical data request

### Error Handling

| Error Type | Behavior |
|------------|----------|
| Missing historical data | Column value = `N/A` |
| Insufficient bars (< 63) | Column value = `N/A` |
| IBKR API error | Column value = `N/A`, logged |
| Connection timeout | Script exits with error |
| Cache read/write error | Warning logged, continues |

## Column Ordering

The trend columns are inserted **before LastUpdate** in the GTT file:

**Before:**
```
Symbol  CP-A(t)  ...  BorrowFee  BF1  BF2  BF_BEST  LastUpdate
```

**After:**
```
Symbol  CP-A(t)  ...  BorrowFee  BF1  BF2  BF_BEST  1yrT  6moT  3moT  LastUpdate
```

## Logging

### Log Locations

- **Default**: `scripts_functions/trend_verifier/logs/trend_verifier_YYYYMMDD_HHMMSS.log`
- **Custom**: Specify with `--log` argument

### Log Levels

- **INFO** (default): Standard execution flow
- **DEBUG** (--verbose): Detailed bar-by-bar data

### Example Log Output

```
2025-11-24 10:30:15 - INFO - ============================================================
2025-11-24 10:30:15 - INFO - Trend Verifier (TVR) - GUS3
2025-11-24 10:30:15 - INFO - ============================================================
2025-11-24 10:30:15 - INFO - Input GTT: ./runs/21-Nov-2025/GusTradeTable.txt
2025-11-24 10:30:15 - INFO - Cache: ./scripts_functions/trend_verifier/cache/trend_slopes_cache.csv (TTL: 86400s)
2025-11-24 10:30:15 - INFO - IB Gateway: 127.0.0.1:4001 (Client ID: 600)
2025-11-24 10:30:15 - INFO - Loaded 150 cached symbols
2025-11-24 10:30:15 - INFO - Connecting to IB Gateway...
2025-11-24 10:30:16 - INFO - Connected to IB. Next valid order ID: 1
2025-11-24 10:30:16 - INFO - Connected successfully
2025-11-24 10:30:16 - INFO - Read 25 stocks from ./runs/21-Nov-2025/GusTradeTable.txt
2025-11-24 10:30:16 - INFO - Added trend columns at positions 19, 20, 21
2025-11-24 10:30:16 - INFO - Processing trends for 25 stocks...
2025-11-24 10:30:18 - INFO - AAPL: Using cached trend data (age: 3600s)
2025-11-24 10:30:22 - INFO - TSLA: Trends calculated - 1Y: 2.23, 6M: 1.45, 3M: 0.98
2025-11-24 10:30:45 - INFO - Successfully enriched ./runs/21-Nov-2025/GusTradeTable.txt with trend data
2025-11-24 10:30:45 - INFO - ============================================================
2025-11-24 10:30:45 - INFO - ✓ Trend enrichment completed successfully!
2025-11-24 10:30:45 - INFO - ============================================================
```

## Performance

### Typical Execution Times

| Stocks | Cached | Uncached | Notes |
|--------|--------|----------|-------|
| 10 | ~2s | ~15s | Mostly cache hits |
| 25 | ~3s | ~35s | Mixed cache/fetch |
| 50 | ~5s | ~60s | Parallel processing |

**Factors affecting speed:**
- Cache hit rate
- IBKR API response time
- Network latency
- Number of concurrent requests

## Troubleshooting

### "Connection timeout. Ensure IB Gateway is running."

**Cause**: IB Gateway not running or wrong port/host

**Solution**:
1. Start IB Gateway or TWS
2. Verify port (4001 for live, 4002 for paper)
3. Enable API connections in Gateway settings
4. Check client ID is unique (not already in use)

### "No historical data received"

**Cause**: IBKR doesn't have data for this symbol

**Solution**:
- Verify symbol exists and trades on US exchanges
- Check if symbol is delisted or suspended
- Column will show `N/A` for this stock

### "Insufficient data for trend calculation: X bars"

**Cause**: Less than 63 daily bars available

**Solution**:
- IPO or newly listed stock (< 3 months old)
- Column will show `N/A` for this stock
- Normal behavior for very new stocks

### "Trend enrichment timed out (300s)"

**Cause**: Too many stocks or slow API responses

**Solution**:
- Reduce number of stocks in GTT
- Increase timeout in GSS (line 813)
- Pre-populate cache by running manually

## Dependencies

```bash
pip install numpy ibapi
```

**Required packages:**
- `numpy`: Linear regression calculations
- `ibapi`: Interactive Brokers Python API
- `concurrent.futures`: Parallel processing (built-in)

## Client ID Assignment

When running alongside other GUS3 scripts:

| Script | Client ID |
|--------|-----------|
| Gainers Scanner (GSS) | 300 |
| Fundamentals Fetcher (FDF) | 400 |
| Trend Verifier (TVR) | **600** |

**Important**: Each script must use a unique client ID to avoid conflicts.

## Best Practices

1. **Cache Management**: Let cache TTL expire naturally (24h) for best balance of freshness/performance
2. **Parallel Execution**: Don't run multiple instances simultaneously (client ID conflict)
3. **Error Tolerance**: Review `N/A` values in GTT - usually indicates new/delisted stocks
4. **Port Selection**: Use 4002 for testing, 4001 for production
5. **Monitoring**: Check log files for API errors or rate limiting issues

## Examples of Use Cases

### Daily Scanner Workflow

```bash
# 1. Run GSS (automatic trend enrichment)
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-24/09:30" \
  --gap-percentage 5 \
  --client-id 300

# GTT is automatically enriched with trends by GSS
```

### Manual Re-enrichment

```bash
# Re-calculate trends for existing GTT
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./runs/21-Nov-2025/GusTradeTable.txt \
  --client-id 600
```

### Cache Pre-population

```bash
# Build cache for known symbols before market open
python3 scripts_functions/trend_verifier/Trend_Verifier.py \
  --input ./historical_gtts/watchlist.txt \
  --client-id 600
```

## Output Format

The enriched GTT maintains tab-delimited format:

```
Symbol	CP-A(t)	CP-A($)	CP-B(t)	CP-B($)	Gap(%)	GapL(%)	Sect	Indus	ShortStatus	ShortDiff	ShortableShares	Float(M$)	NetIncome(M$)	Instit(%)	BorrowFee	BF1	BF2	BF_BEST	1yrT	6moT	3moT	LastUpdate
AAPL	2025-11-24/09:30	150.25	2025-11-24/10:15	157.50	4.82	5.12	Technology	Consumer Electronics	HIGH_AVAIL	2.8	50000	2500000	95000	65.5	0.25	0.25	0.30	0.25	2.23	1.45	0.98	2025-11-24/10:30:00
```

## Version History

- **v1.0** (2025-11-24): Initial CLI-driven implementation with caching and GTT integration

## Support

For issues or questions:
1. Check log files in `scripts_functions/trend_verifier/logs/`
2. Verify IB Gateway connection
3. Review CLAUDE.md for pipeline documentation
4. Test with `--verbose` flag for detailed output
