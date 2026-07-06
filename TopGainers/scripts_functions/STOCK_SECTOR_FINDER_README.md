# Stock Sector Finder (SSF) - README

## Overview

The Stock Sector Finder (SSF) is a component of the GUS-ATS (Gap Up Short - Algorithmic Trading System) pipeline. It enriches the output from the Gainers Scanner Script by adding sector and industry classification information for each stock using the Yahoo Finance API.

## Purpose

This script reads the CSV output from `Gainers_Scanner_Script.py` and adds a new "Sector" column containing comma-separated sector and industry information for each stock symbol.

## Installation

### Prerequisites

Install required Python packages:

```bash
# Option 1: Install all GUS-ATS requirements
pip install -r requirements.txt

# Option 2: Install yfinance only (if other packages are already installed)
pip install yfinance
```

**Note:** If you encounter `externally-managed-environment` errors, you may need to:
- Use a virtual environment: `python3 -m venv venv && source venv/bin/activate`
- Or use `pipx`: `pipx install yfinance`

## Usage

### Automatic Mode (Find Latest CSV)

```bash
python3 Stock_Sector_Finder.py
```

The script will automatically find and process the most recent `gainers_scan_*.csv` file in the output directory.

### Manual Mode (Specify CSV File)

```bash
python3 Stock_Sector_Finder.py /path/to/gainers_scan_20251007_093000.csv
```

## Input Format

The script expects a CSV file with at least the following column:
- `Symbol` - Stock ticker symbol

Typical input from Gainers_Scanner_Script.py:
```csv
Symbol,ETH_Open_4AM,Current_Price,Gain_Percent,Scan_Time
AAPL,150.00,180.00,20.00,2025-10-07 09:30:00
TSLA,200.00,260.00,30.00,2025-10-07 09:30:00
```

## Output Format

The script creates a new CSV file with `_sectors` suffix containing all original columns plus a new "Sector" column:

```csv
Symbol,ETH_Open_4AM,Current_Price,Gain_Percent,Scan_Time,Sector
AAPL,150.00,180.00,20.00,2025-10-07 09:30:00,"Technology,Consumer Electronics"
TSLA,200.00,260.00,30.00,2025-10-07 09:30:00,"Consumer Cyclical,Auto Manufacturers"
```

### Sector Column Format

The "Sector" column contains:
- `"Sector,Industry"` - Both sector and industry (e.g., "Healthcare,Biotechnology")
- `"Sector"` - Only sector if industry is not available
- `"Industry"` - Only industry if sector is not available
- `"N/A"` - If neither sector nor industry information is available

## Configuration

The script uses settings from `GUS_Config_File.txt`:

```ini
# Delay between Yahoo Finance API requests (seconds)
SECTOR_REQUEST_DELAY = 0.1

# Output directory for CSV files
OUTPUT_DIR = output

# Log directory
LOG_DIR = logs
```

## Logging

Logs are written to:
- Console (stdout)
- Log file: `logs/sector_finder_{timestamp}.log`

Log files include:
- Processing status for each stock
- API request details
- Success/failure statistics
- Any errors encountered

## Integration in GUS-ATS Pipeline

The Stock Sector Finder fits into the GUS-ATS pipeline as follows:

```
1. Gainers_Scanner_Script.py
   ↓ (produces gainers_scan_{timestamp}.csv)

2. Stock_Sector_Finder.py  ← YOU ARE HERE
   ↓ (produces gainers_scan_{timestamp}_sectors.csv)

3. Trend_Verifier.py
   ↓ (produces gainers_scan_{timestamp}_sectors_trends.csv)

4. [Next pipeline stage...]
```

## Error Handling

The script handles various error conditions gracefully:

- **Missing yfinance library**: Exits with installation instructions
- **CSV file not found**: Logs error and exits
- **Invalid stock symbols**: Logs warning and continues with "N/A"
- **Network errors**: Logs error and continues with "N/A"
- **Rate limiting**: Built-in delay between requests (configurable)

## Examples

### Example 1: Process Latest Scan

```bash
$ python3 Stock_Sector_Finder.py

============================================================
Stock Sector Finder (SSF) - GUS-ATS
============================================================
Searching for latest gainers_scan CSV...
Found latest CSV: output/gainers_scan_20251007_093000.csv
Loaded 15 stocks from output/gainers_scan_20251007_093000.csv

Processing 15 stocks for sector/industry information...
Fetching sector/industry for AAPL...
AAPL: Sector/Industry = Technology,Consumer Electronics
[...]
Successfully retrieved sector info for 14/15 stocks (1 N/A)

Enhanced results exported to: output/gainers_scan_20251007_093000_sectors.csv
============================================================
✓ Sector finding complete! Results saved to: output/gainers_scan_20251007_093000_sectors.csv
============================================================
```

### Example 2: Process Specific File

```bash
$ python3 Stock_Sector_Finder.py output/gainers_scan_20251007_120000.csv

============================================================
Stock Sector Finder (SSF) - GUS-ATS
============================================================
Using specified CSV file: output/gainers_scan_20251007_120000.csv
[...]
```

## Troubleshooting

### Issue: "No module named 'yfinance'"
**Solution**: Install yfinance: `pip install yfinance`

### Issue: "No gainers_scan CSV files found"
**Solution**: Run `Gainers_Scanner_Script.py` first to generate input data

### Issue: All stocks show "N/A" for sector
**Solution**:
- Check internet connection
- Verify stock symbols are valid
- Check if Yahoo Finance API is accessible
- Review log file for specific errors

### Issue: Rate limiting errors
**Solution**: Increase `SECTOR_REQUEST_DELAY` in `GUS_Config_File.txt`

## Technical Notes

- **API**: Uses yfinance library (unofficial Yahoo Finance API)
- **Rate Limiting**: Built-in delay between requests (default: 0.1 seconds)
- **Data Source**: Yahoo Finance (free, no API key required)
- **Reliability**: Some stocks may not have sector/industry data available
- **Performance**: ~0.1-0.2 seconds per stock (configurable via SECTOR_REQUEST_DELAY)

## Future Enhancements

Potential improvements for future versions:
- Caching of sector/industry data to reduce API calls
- Alternative data sources if Yahoo Finance fails
- More detailed industry classifications
- Sector performance metrics
- Multi-threading for faster processing

## Support

For issues or questions:
- Check log files in `logs/` directory
- Review Yahoo Finance API status
- Verify input CSV format matches expected structure
- Ensure all dependencies are installed

## Version History

- **v1.0** (2025-10-07): Initial release
  - Yahoo Finance integration
  - Automatic CSV discovery
  - Comma-separated sector/industry format
  - Comprehensive error handling and logging
