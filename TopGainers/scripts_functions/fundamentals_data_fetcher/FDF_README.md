# Fundamentals Data Fetcher (FDF) - Usage Guide

## Overview
The Fundamentals Data Fetcher (FDF) enriches GusTradeTable files with comprehensive fundamental metrics from Interactive Brokers API. It integrates seamlessly with the Gainers Scanner Script (GSS) pipeline to provide critical financial data for Gap Up Short trading strategy analysis.

## Features
- **Command-line driven**: No config files needed, all parameters via arguments
- **Tab-delimited format**: Maintains GTT file format compatibility
- **Comprehensive metrics**: Float, institutional ownership, short interest, financial health, and more
- **Market data**: Optional real-time price and volume data
- **Robust error handling**: Continues processing with N/A for missing data
- **Rich logging**: Detailed file logs and live console display
- **Progress tracking**: Visual progress bars for data fetching

## Installation

### Requirements
```bash
pip install rich pandas pytz ibapi
```

### System Requirements
- Python 3.7+
- IB Gateway or TWS running
- Active IB account with market data subscriptions
- Unique client ID (different from GSS and other running scripts)

## Usage

### Basic Command
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400
```

### Complete Options
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --output ./runs/19-Nov-2025/GusTradeTable_enriched.txt \
  --log ./runs/19-Nov-2025/fdf.log \
  --client-id 400 \
  --host 127.0.0.1 \
  --port 4001 \
  --verbose
```

## Arguments

### Required Arguments
| Argument | Type | Description | Example |
|----------|------|-------------|---------|
| `--input` | string | Path to GusTradeTable file (tab-delimited) | `./runs/19-Nov-2025/GusTradeTable_19nov2025.txt` |
| `--client-id` | int | IB Gateway client ID (must be unique) | `400` |

### Optional Arguments
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--output` | string | (overwrites input) | Output file path |
| `--log` | string | `logs/fdf_YYYYMMDD_HHMMSS.log` | Log file path |
| `--host` | string | `127.0.0.1` | IB Gateway host |
| `--port` | int | `4001` | IB Gateway port (4001 live, 4002 paper) |
| `--no-market-data` | flag | False | Skip real-time market data (faster) |
| `--batch-delay` | float | `0.5` | Delay between batches in seconds |
| `--verbose` | flag | False | Enable verbose logging |

## Metrics Added to GusTradeTable

### High Priority Metrics (Gap Up Short Strategy)
| Column Name | Description | Importance |
|-------------|-------------|------------|
| **Float** | Float shares available | Critical - smaller float = more volatile |
| **Institutional_Pct** | % held by institutions | **VERY IMPORTANT** - indicates stability |
| **Short_Interest** | Shares currently shorted | Critical - gauge short squeeze risk |
| **Short_Interest_Ratio** | Days to cover | Risk assessment metric |
| **Shortable_Shares** | Borrow availability (0.0-3.0 indicator) | Critical - can you short this? |
| **Borrow_Fee_Rate** | Cost to borrow (%) - **FTP data only** | Direct cost impact |

**Note on Borrow Data**:
- **Shortable_Shares**: Fetched via IBKR API generic tick 236 (90-95% coverage)
- **Borrow_Fee_Rate**: NOT available via API - must use FTP source (see GSS for automatic enrichment)
- FDF can fetch shortable shares indicator but NOT borrow fee rates
- For complete borrow fee data, use Gainers Scanner Script which includes FTP enrichment

### Financial Health Metrics
| Column Name | Description | Use Case |
|-------------|-------------|----------|
| **Net_Income_TTM** | Trailing 12-month net income | Profitability indicator |
| **Revenue_TTM** | Trailing 12-month revenue | Business scale |
| **Profit_Margin** | Net profit margin % | Efficiency metric |
| **ROE** | Return on equity | Management effectiveness |
| **Debt_To_Equity** | Debt/equity ratio | Financial risk |

### Valuation Metrics
| Column Name | Description | Use Case |
|-------------|-------------|----------|
| **Market_Cap** | Market capitalization | Company size |
| **Shares_Outstanding** | Total shares outstanding | Ownership structure |
| **EPS** | Earnings per share | Profitability per share |
| **PE_Ratio** | Price to earnings ratio | Valuation metric |
| **Beta** | Volatility measure | Risk assessment |

### Real-Time Data (if `--no-market-data` not used)
| Column Name | Description | Use Case |
|-------------|-------------|----------|
| **FDF_Current_Price** | Price at fetch time | Comparison with GTT prices |
| **FDF_Volume** | Current volume | Liquidity indicator |
| **FDF_Bid** | Bid price | Spread analysis |
| **FDF_Ask** | Ask price | Spread analysis |
| **FDF_Timestamp** | When data was fetched | Data freshness indicator |

## Output Format

The enriched GusTradeTable maintains the original tab-delimited format with new columns appended:

```
Symbol	CheckPoint-A TIME	CheckPoint-A PRICE	...	Float	Institutional_Pct	Short_Interest	...
SEMR	2025-11-18/16:30	6.89	...	15000000	45.2	2500000	...
SGML	2025-11-18/16:30	7.76	...	N/A	N/A	N/A	...
```

**Note**: Missing data is marked as `N/A` to maintain table structure.

## Integration with GSS Pipeline

### Complete Workflow

```bash
# Step 1: Run Gainers Scanner Script (GSS)
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-19/09:30" \
  --gap-percentage 5 \
  --client-id 300 \
  --output-dir ./runs/19-Nov-2025

# Step 2: Enrich with Fundamentals Data Fetcher (FDF)
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400

# Step 3: (Optional) Run Sector Finder
python3 scripts_functions/Stock_Sector_Finder.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt

# Step 4: (Optional) Run Trend Verifier
python3 scripts_functions/Trend_Verifier.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt
```

### Client ID Management

Each script needs a unique client ID:
- GSS: `--client-id 300`
- FDF: `--client-id 400`
- Sector Finder: `--client-id 500`
- Trend Verifier: `--client-id 600`

## Examples

### Basic Enrichment (Overwrites Original)
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400
```

### Custom Output File
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --output ./runs/19-Nov-2025/GusTradeTable_with_fundamentals.txt \
  --client-id 400
```

### Paper Trading Account
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400 \
  --port 4002
```

### Skip Market Data (Faster)
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400 \
  --no-market-data
```

This skips real-time price/volume fetching, focusing only on fundamental data. Useful when:
- You only need fundamental metrics
- Market is closed
- Speed is more important than live data

### With Custom Logging
```bash
python fundamentals_data_fetcher.py \
  --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \
  --client-id 400 \
  --log ./runs/19-Nov-2025/fdf_detailed.log \
  --verbose
```

### Multiple GTT Files (Sequential)
```bash
# Process multiple files sequentially
for file in ./runs/*/GusTradeTable*.txt; do
  echo "Processing $file"
  python fundamentals_data_fetcher.py --input "$file" --client-id 400
  sleep 5  # Wait between runs
done
```

## How It Works

### Data Fetching Process
1. **Connect to IB Gateway**: Establishes connection using provided host/port/client-id
2. **Load GTT File**: Reads tab-delimited GusTradeTable, extracts symbols
3. **Request Fundamental Data**: Batches requests to IBKR API for each symbol
4. **Request Market Data** (optional): Fetches real-time price, volume, shortable info
5. **Parse XML Data**: Extracts metrics from IBKR XML responses
6. **Merge Data**: Adds new columns to GTT DataFrame
7. **Save Output**: Writes enriched table back as tab-delimited file

### Data Sources
- **Fundamental Data**: IBKR ReportSnapshot API (XML format)
- **Market Data**: IBKR Market Data API with generic tick 236
- **Short Interest**: From fundamental data XML
- **Borrow Info**: From market data generic ticks (limited availability)

### Error Handling
- **Connection failures**: Script exits with error message
- **Individual stock failures**: Marked with N/A, script continues
- **Missing fundamental data**: Uses N/A for specific fields
- **Timeout**: 30-second timeout for fundamental data requests
- **All errors logged**: Check log file for detailed troubleshooting

## Performance Notes

### Timing Estimates
- **Fundamental data**: ~0.3 seconds per stock
- **Market data**: ~0.2 seconds per stock
- **Processing**: ~0.1 seconds per stock
- **Total**: Approximately **30-60 seconds for 50 stocks**

### API Rate Limiting
- IBKR API has rate limits
- Script uses delays to prevent throttling:
  - 0.3s between fundamental data requests
  - 0.2s between market data requests
  - 0.5s between batches (configurable with `--batch-delay`)

### Optimization Tips
1. **Use `--no-market-data`**: Saves ~40% time if you don't need live prices
2. **Run outside market hours**: Better API performance
3. **Limit symbols**: Filter GTT before enriching if only specific stocks needed
4. **Increase `--batch-delay`**: If experiencing rate limiting

## Troubleshooting

### Connection Issues
**Problem**: `❌ Failed to connect to IB Gateway`

**Solutions**:
- Ensure IB Gateway or TWS is running
- Check port matches your IB Gateway configuration
- Verify client ID is not already in use
- Check firewall settings

### Missing Data
**Problem**: Many columns show `N/A`

**Reasons**:
- IBKR has limited fundamental data for some stocks
- Small-cap/OTC stocks often have incomplete data
- International stocks may have different data formats
- Short interest data may not be available for all stocks

**This is normal** - IBKR API has varying data coverage.

### Timeout Errors
**Problem**: Script times out waiting for data

**Solutions**:
- Increase timeout in code (modify `timeout = 30` in `fetch_complete_data()`)
- Run during off-peak hours
- Check IB Gateway connection stability
- Reduce number of symbols

### Client ID Conflicts
**Problem**: `Client ID already in use`

**Solution**:
- Use different client ID (e.g., 401, 402, etc.)
- Check other running scripts using `ps aux | grep python`
- Ensure unique client IDs across all IBKR scripts

### Tab Delimiter Issues
**Problem**: Output file not properly formatted

**Solution**:
- Script uses `sep='\t'` for tab-delimited format
- Verify input file is tab-delimited
- Check output with: `cat -A output_file.txt` (shows tabs as `^I`)

## Log Files

### Log Location
- **Default**: `logs/fdf_YYYYMMDD_HHMMSS.log`
- **Custom**: Specify with `--log` argument

### Log Contents
- Connection status
- Data request details
- Error messages and warnings
- Processing summary
- Metric coverage statistics

### Log Levels
- **INFO**: Normal operation events
- **WARNING**: Missing data, non-critical errors
- **ERROR**: Critical failures
- **DEBUG**: Detailed request/response info (use `--verbose`)

## Display Output

### Console Display
The script provides rich console output:
- Banner and configuration
- Progress bars for data fetching
- Processing status messages
- Summary table with metric coverage
- Preview of new columns

### Summary Table
Shows coverage statistics for key metrics:
```
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━┓
┃ Metric             ┃ Stocks with Data  ┃ Missing Data ┃ Coverage % ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━┩
│ Float              │ 45                │ 5            │ 90.0%      │
│ Institutional_Pct  │ 38                │ 12           │ 76.0%      │
│ Short_Interest     │ 42                │ 8            │ 84.0%      │
│ Net_Income_TTM     │ 40                │ 10           │ 80.0%      │
└────────────────────┴───────────────────┴──────────────┴────────────┘
```

## Data Quality and Limitations

### High Quality Data (Usually Available)
- Float shares
- Market cap
- Shares outstanding
- EPS (for reporting companies)

### Medium Quality Data (Varies)
- Institutional ownership %
- Short interest
- Financial metrics (TTM data)
- P/E ratio

### Low Quality Data (Often Missing)
- Borrow fee rate (IB API limitation)
- Detailed financial ratios
- Real-time short interest

### Best Practices
1. **Always check coverage %**: Review summary table after enrichment
2. **Validate critical metrics**: Verify important data points manually for key trades
3. **Use multiple sources**: Consider supplementing with yfinance or other APIs
4. **Filter by data quality**: Exclude stocks with too many N/A values

## Advanced Usage

### Scripting Integration
```bash
#!/bin/bash
# Full daily workflow automation

DATE=$(date +%d-%b-%Y)
RUN_DIR="./runs/${DATE}"
GTT_FILE="${RUN_DIR}/GusTradeTable_$(date +%dmm%Y).txt"

# Check if GTT exists
if [ ! -f "$GTT_FILE" ]; then
  echo "Error: GTT file not found: $GTT_FILE"
  exit 1
fi

# Run FDF
echo "Enriching with fundamentals..."
python3 scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py \
  --input "$GTT_FILE" \
  --client-id 400 \
  --log "${RUN_DIR}/fdf.log"

# Check exit code
if [ $? -eq 0 ]; then
  echo "Success! Enriched GTT: $GTT_FILE"
else
  echo "Error during enrichment"
  exit 1
fi
```

### Python API Usage
While FDF is designed as a CLI tool, you can import its functions:

```python
from fundamentals_data_fetcher import fetch_complete_data, update_gtt_table
import pandas as pd

# Load your GTT
gtt_df = pd.read_csv('GusTradeTable.txt', sep='\t')
symbols = gtt_df['Symbol'].tolist()

# Fetch data
metrics_df = fetch_complete_data(
    symbols=symbols,
    host='127.0.0.1',
    port=4001,
    client_id=400,
    get_market_data=True
)

# Merge
enriched = update_gtt_table(gtt_df, metrics_df)

# Save
enriched.to_csv('enriched.txt', sep='\t', index=False)
```

## Understanding the Metrics for Gap Up Short Strategy

### Critical Metrics for Short Strategy

**Float** - Lower float means:
- Higher volatility
- Easier to squeeze
- More risk for shorts
- **Strategy**: Prefer higher float for safer shorts

**Institutional_Pct** - Higher institutional ownership means:
- More stable stock
- Less likely to have extreme moves
- Professional investors holding
- **Strategy**: Higher % = safer to short (less retail frenzy)

**Short_Interest** - High short interest means:
- Many traders already short
- Risk of short squeeze
- Overcrowded trade
- **Strategy**: Be cautious with high short interest

**Short_Interest_Ratio** - Days to cover:
- Higher = longer time to close shorts
- Higher = more squeeze risk
- **Strategy**: Lower ratio preferred (<5 days)

### Financial Health for Risk Assessment

**Net_Income_TTM** - Profitability:
- Negative = unprofitable company
- More volatile, higher risk
- **Strategy**: Negative income supports bearish thesis

**Debt_To_Equity** - Financial risk:
- High ratio = more debt
- More bankruptcy risk
- **Strategy**: High ratio supports bearish thesis

**Profit_Margin** - Efficiency:
- Low margin = struggling business
- **Strategy**: Low margin supports bearish thesis

## Support and Troubleshooting

### Common Issues
1. **Data not appearing**: Check log file for API errors
2. **Script hangs**: Check IB Gateway connection
3. **Rate limiting**: Increase `--batch-delay`
4. **Missing columns**: Ensure input file has Symbol column

### Getting Help
- Check log files in `./logs/`
- Run with `--verbose` for detailed output
- Verify IB Gateway is running and accessible
- Test with small GTT file (5-10 symbols) first

## Version History

### Version 2.0 (Current)
- CLI-driven interface
- Tab-delimited format support
- Integration with GSS pipeline
- Rich console output
- Comprehensive logging
- Progress tracking
- Error handling with N/A values

### Version 1.0 (Original)
- Library-style usage
- CSV format
- Example-driven
- Basic error handling

## Related Documentation

- **Gainers Scanner Script**: `scripts_functions/gainers_scanner_scripts/GAINERS_SCANNER_README.md`
- **GUS2 Project**: `CLAUDE.md` in project root
- **IB API Documentation**: [Interactive Brokers API](https://interactivebrokers.github.io/)

## License

This tool is part of the GUS2 (Gap Up Short) trading system.
