# GTTviewer - GusTradeTable Viewer

## Overview
GTTviewer is a live terminal viewer for GusTradeTable (GTT) files with color-coded display, auto-refreshing, filtering, sorting, and summary statistics.

## Features
- 📊 **Live auto-refreshing display** - Automatically updates as file changes
- 🎨 **Color-coded values** - Gain percentages, borrow status, timestamp freshness
- 📋 **Three display modes** - Compact, Normal, Detailed
- 🔍 **Filtering** - By gain percentage, borrow status
- ⬆️ **Sorting** - By any column, ascending/descending
- 📈 **Summary statistics** - Averages, min/max, borrow distribution
- 🎯 **Symbol highlighting** - Highlight specific symbols
- 🖥️ **Responsive layout** - Adapts to terminal size

## Installation

### Requirements
```bash
pip install rich pandas
```

These dependencies are already included in the GUS3 project.

## Usage

### Basic Usage
```bash
python GTTviewer.py --input-GTT <path_to_GTT_file>
```

### Examples

#### Basic viewing with default settings
```bash
python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt
```

#### Fast refresh (2 seconds) with gain filter
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --refresh-interval 2 \
  --filter-min-gain 10
```

#### Compact mode, top 20 gainers
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode compact \
  --max-rows 20 \
  --sort-by live_percentage
```

#### Monitor specific columns with highlighting
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --show-columns Symbol,live_percentage,Gain_Percent,Borrow_Status \
  --highlight NVRI,AZTA
```

#### Show only easy-to-borrow stocks above 5% gain
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --filter-borrow Easy \
  --filter-min-gain 5
```

#### Detailed mode with FDF enrichment data
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode detailed \
  --sort-by Institutional_Pct
```

## Command-Line Arguments

### Required
| Argument | Type | Description |
|----------|------|-------------|
| `--input-GTT` | string | Path to GusTradeTable file (tab-delimited) |

### Display Options
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--refresh-interval` | int | 5 | Refresh rate in seconds |
| `--display-mode` | string | normal | Display mode: compact\|normal\|detailed |
| `--no-colors` | flag | False | Disable color coding |
| `--no-borders` | flag | False | Minimal table borders |
| `--max-rows` | int | None | Limit display to N rows |

### Sorting Options
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--sort-by` | string | live_percentage | Column name to sort by |
| `--sort-order` | string | desc | Sort order: asc\|desc |

### Filtering Options
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--filter-min-gain` | float | None | Minimum gain % to show |
| `--filter-max-gain` | float | None | Maximum gain % to show |
| `--filter-borrow` | string | None | Filter by borrow status (Easy\|Available\|Hard) |

### Column Control
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--show-columns` | string | None | Comma-separated list of columns to show |
| `--hide-columns` | string | None | Comma-separated list of columns to hide |
| `--highlight` | string | None | Comma-separated list of symbols to highlight |

### Logging
| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--log` | string | None | Log file path |
| `--verbose` | flag | False | Enable verbose logging |

## Display Modes

### Compact Mode
- Minimal spacing and borders
- Essential columns only:
  - Symbol
  - CheckPoint-A PRICE
  - Gain_Percent
  - live_percentage
  - Borrow_Status
  - Last_Update (short format)
- More rows per screen
- Best for: Quick monitoring, many stocks

### Normal Mode (Default)
- Balanced spacing
- Standard columns:
  - Symbol
  - CheckPoint-A TIME
  - CheckPoint-A PRICE
  - CheckPoint-B TIME
  - CheckPoint-B PRICE
  - Gain_Percent
  - live_percentage
  - Borrow_Status
  - Last_Update
- **Auto-includes FDF enrichment columns** (if present):
  - Float
  - Institutional_Pct
  - Short_Interest
  - Short_Interest_Ratio
  - Shortable_Shares
  - Borrow_Fee_Rate
- Readable layout
- Best for: General use, enriched GTT files

### Detailed Mode
- Maximum information
- All available columns (including FDF enrichment if present)
- Full column widths
- Best for: In-depth analysis, FDF enriched data

## Color Coding Scheme

### Gain Percentages (Gain_Percent, live_percentage)
| Range | Color | Meaning |
|-------|-------|---------|
| Negative | **Bold Red** | Loss |
| 0-5% | White | Low gain |
| 5-15% | Green | Moderate gain |
| 15-30% | Bright Green | Good gain |
| 30-50% | Yellow | High gain |
| 50-75% | Bright Yellow | Very high gain |
| 75-100% | Orange | Extreme gain |
| >100% | Bright Red | Extraordinary gain |

### Borrow Status
| Status | Color | Meaning |
|--------|-------|---------|
| Easy | Green | Abundant shares available |
| Available | Yellow | Shares available |
| Hard | Red | Limited shares |
| N/A | Dim Gray | No data |

### Timestamp Freshness (Last_Update)
| Age | Color | Status |
|-----|-------|--------|
| < 1 minute | Green | Very fresh |
| 1-5 minutes | White | Fresh |
| 5-15 minutes | Yellow | Getting old |
| 15-30 minutes | Orange | Old |
| > 30 minutes | Red | Stale data warning |

## Table Layout

The viewer displays three sections:

### 1. Main Table
- Header row with column names
- Data rows with color-coded values
- Highlighted symbols (if specified)
- Sorted and filtered based on arguments

### 2. Summary Statistics Panel
- Total stocks displayed
- Filtered count (if any)
- Gain statistics: Average, Min, Max, Median
- Borrow status distribution
- File information and modification time
- Last refresh timestamp

### 3. Status Bar
- File name
- Total row count
- Filtered count
- Current sort column and order
- Refresh interval
- Exit instructions

## Integration with GUS3 Pipeline

GTTviewer works with GTT files from any stage of the pipeline:

### 1. View Gainers Scanner Output
```bash
# After running Gainers Scanner
python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt
```

### 2. View FDF Enriched Data
```bash
# After running Fundamentals Data Fetcher
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode detailed \
  --show-columns Symbol,live_percentage,Float,Institutional_Pct,Short_Interest
```

### 3. Live Monitoring During Scanner Run
```bash
# Open in separate terminal while Gainers Scanner is running
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --refresh-interval 3
```

## Advanced Usage

### Custom Column Selection
Show only specific columns:
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --show-columns "Symbol,live_percentage,Borrow_Status,Float,Institutional_Pct"
```

### Hide Certain Columns
Hide columns from normal view:
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --hide-columns "CheckPoint-B TIME,CheckPoint-B PRICE"
```

### Multi-Criteria Filtering
Combine filters:
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --filter-min-gain 10 \
  --filter-max-gain 50 \
  --filter-borrow Easy
```

### Sort by FDF Metrics
Sort by fundamental data:
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode detailed \
  --sort-by Institutional_Pct \
  --sort-order asc
```

### Top N Stocks
Show only top performers:
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --max-rows 10 \
  --sort-by live_percentage \
  --sort-order desc
```

### With Logging
Enable logging for troubleshooting:
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --log ./logs/gttviewer.log \
  --verbose
```

## Keyboard Controls

- **Ctrl+C** - Exit viewer gracefully

## Error Handling

### File Not Found
```
❌ Error: GTT file not found
   Path: ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt

   Please check:
   - File path is correct
   - File exists
   - You have read permissions
```

### Empty File
```
⚠️  Warning: GTT file is empty
   Waiting for data... (refreshing every 5 seconds)
```

The viewer will continue checking until data appears.

### Invalid Format
```
❌ Error: Invalid GTT file format

   Missing required columns: Symbol
   Found columns: Stock, Price, Gain
```

### Parse Errors
The viewer handles parse errors gracefully:
- Skips problematic rows
- Logs errors if `--log` is specified
- Continues processing remaining data

## Performance Notes

### Refresh Rate
- **Minimum recommended**: 1 second
- **Default**: 5 seconds (good balance)
- **High-frequency**: 1-3 seconds (may cause flicker)
- **Low-frequency**: 10+ seconds (very stable)

### Large Files (>1000 rows)
- Use `--max-rows` to limit display
- Consider filtering to reduce rows
- Increase refresh interval to reduce CPU usage

### Terminal Size
- Works best with terminals 100+ columns wide
- Auto-detects terminal size
- Adjusts layout dynamically
- Use `compact` mode for narrower terminals

## Troubleshooting

### Colors Not Showing
- Ensure your terminal supports colors
- Try disabling `--no-colors` flag
- Check terminal color settings

### Table Too Wide
- Use `--display-mode compact`
- Use `--show-columns` to select fewer columns
- Maximize terminal window
- Use `--hide-columns` to remove unwanted columns

### Viewer Not Updating
- Check that file is being modified
- Verify `--refresh-interval` setting
- Check file permissions
- Look at logs with `--log` and `--verbose`

### High CPU Usage
- Increase `--refresh-interval`
- Reduce number of displayed rows with `--max-rows`
- Use `--filter-min-gain` to reduce rows

## Use Cases

### 1. Live Monitoring During Trading Hours
```bash
python GTTviewer.py \
  --input-GTT ./runs/$(date +%d-%b-%Y)/GusTradeTable_$(date +%dmm%Y).txt \
  --refresh-interval 3 \
  --filter-min-gain 5
```

### 2. Post-Market Analysis
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode detailed \
  --sort-by live_percentage \
  --max-rows 50
```

### 3. Identifying Easy Shorts
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --filter-borrow Easy \
  --filter-min-gain 20 \
  --sort-by live_percentage
```

### 4. Fundamental Analysis
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode detailed \
  --show-columns "Symbol,live_percentage,Float,Institutional_Pct,Short_Interest,Debt_To_Equity" \
  --sort-by Institutional_Pct
```

### 5. Watching Specific Stocks
```bash
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --highlight NVRI,AZTA,MFI \
  --refresh-interval 2
```

## Tips and Best Practices

### 1. Terminal Setup
- Use a terminal with good color support (iTerm2, Konsole, GNOME Terminal)
- Set dark background for better color contrast
- Maximize window for detailed mode

### 2. Workflow Integration
- Open GTTviewer in separate terminal while scanner runs
- Use filters to focus on actionable stocks
- Sort by different metrics to find opportunities

### 3. Multiple Views
Open multiple viewers with different filters:
```bash
# Terminal 1: All gainers
python GTTviewer.py --input-GTT ./runs/today/GusTradeTable.txt

# Terminal 2: Easy shorts only
python GTTviewer.py --input-GTT ./runs/today/GusTradeTable.txt --filter-borrow Easy

# Terminal 3: Extreme gainers
python GTTviewer.py --input-GTT ./runs/today/GusTradeTable.txt --filter-min-gain 50
```

### 4. Export Filtered View
While GTTviewer doesn't modify files, you can use it to identify stocks, then manually filter the GTT file with pandas or other tools.

## Related Tools

- **Gainers Scanner Script (GSS)**: Generates GTT files
  - Location: `scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py`
  - Documentation: `scripts_functions/gainers_scanner_scripts/GAINERS_SCANNER_README.md`

- **Fundamentals Data Fetcher (FDF)**: Enriches GTT with fundamental metrics
  - Location: `scripts_functions/fundamentals_data_fetcher/fundamentals_data_fetcher.py`
  - Documentation: `scripts_functions/fundamentals_data_fetcher/FDF_README.md`

## Version History

### Version 1.0 (Current)
- Initial release
- Live auto-refreshing display
- Three display modes (compact, normal, detailed)
- Color coding for gains, borrow status, timestamps
- Filtering by gain and borrow status
- Sorting by any column
- Summary statistics
- Column visibility control
- Symbol highlighting
- Error handling and logging

## Future Enhancements (Planned)

### Version 1.1
- Interactive keyboard controls (press 's' to sort, 'f' to filter)
- Export filtered view to new GTT file
- Alert notifications when thresholds crossed

### Version 2.0
- Multi-file comparison mode
- Charts and sparklines
- HTML export
- Real-time file monitoring with watchdog library

## Support

For issues or questions:
1. Check this README
2. Check the design plan: `GTTVIEWER_DESIGN_PLAN.md`
3. Enable verbose logging: `--log ./gttviewer.log --verbose`
4. Check project CLAUDE.md for overall system documentation

## License

This tool is part of the GUS3 (Gap Up Short) trading system project.

---

**Last Updated**: 2025-11-21
**Version**: 1.0
