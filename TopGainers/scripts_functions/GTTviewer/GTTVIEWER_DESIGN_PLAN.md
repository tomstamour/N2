# GTTviewer (GTTV) - Design Plan

## Overview
GTTviewer is a terminal-based viewer for GusTradeTable (GTT) files that provides live-refreshing, color-coded display with sorting, filtering, and summary statistics.

## Architecture

### Core Components
1. **GTT File Parser** - Reads tab-delimited GTT files
2. **Display Engine** - Rich library table renderer with live updates
3. **Data Processor** - Sorting, filtering, and statistics calculations
4. **File Monitor** - Detects file changes and auto-reloads
5. **Color Engine** - Context-aware color coding

### Technology Stack
- **Rich Library**: Terminal UI (tables, colors, live display)
- **Pandas**: Data manipulation and filtering
- **Watchdog** (optional): File system monitoring
- **Pytz**: Timestamp handling

## Features

### Core Features (v1.0)
1. ✅ Live auto-refreshing display
2. ✅ Tab-delimited GTT file parsing
3. ✅ Color-coded values (gains, borrow status, metrics)
4. ✅ Responsive table layout with borders
5. ✅ Auto-reload on file changes
6. ✅ Summary statistics row
7. ✅ Last update timestamp display

### Advanced Features (v1.1+)
8. ✅ Column sorting (by any column)
9. ✅ Filtering (min/max thresholds)
10. ✅ Column visibility control
11. ✅ Multiple display modes (compact/normal/detailed)
12. ✅ Export filtered view
13. ✅ Search/highlight symbols

## Command-Line Interface

### Required Arguments
```bash
--input-GTT <path>           # Path to GusTradeTable file
```

### Optional Arguments
```bash
--refresh-interval <seconds> # Refresh rate (default: 5)
--display-mode <mode>        # compact|normal|detailed (default: normal)
--sort-by <column>           # Sort by column name (default: live_percentage)
--sort-order <order>         # asc|desc (default: desc)
--filter-min-gain <percent>  # Minimum gain % to show (default: none)
--filter-max-gain <percent>  # Maximum gain % to show (default: none)
--filter-borrow <status>     # Filter by borrow status (Easy|Available|Hard)
--show-columns <cols>        # Comma-separated column list (default: all)
--hide-columns <cols>        # Columns to hide (default: none)
--no-colors                  # Disable color coding
--no-borders                 # Minimal table borders
--max-rows <n>               # Limit display to N rows (default: all)
--highlight <symbol>         # Highlight specific symbol(s)
--log <path>                 # Log file path (default: none)
```

### Usage Examples
```bash
# Basic usage
python GTTviewer.py --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt

# Fast refresh with filtering
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --refresh-interval 2 \
  --filter-min-gain 10 \
  --sort-by live_percentage

# Compact mode, top 20 gainers
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --display-mode compact \
  --max-rows 20 \
  --sort-by live_percentage \
  --sort-order desc

# Monitor specific columns with highlighting
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --show-columns Symbol,live_percentage,Gain_Percent,Borrow_Status \
  --highlight NVRI,AZTA

# Show only easy-to-borrow stocks
python GTTviewer.py \
  --input-GTT ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt \
  --filter-borrow Easy \
  --filter-min-gain 5
```

## Display Modes

### Compact Mode
- Minimal spacing and borders
- Essential columns only: Symbol, Checkpoint-A Price, Gain%, Live%, Borrow, Update
- Smaller column widths
- More rows per screen

### Normal Mode (Default)
- Balanced spacing
- Standard columns: Symbol, Checkpoint-A, Checkpoint-B, Gain%, Live%, Borrow, Update
- Readable column widths
- Standard borders

### Detailed Mode
- Maximum information
- All available columns (including FDF enrichment if present)
- Wide column widths
- Full borders and separators

## Color Coding Scheme

### Gain Percentages (Gain_Percent, live_percentage)
```
Negative values:     Red (bold)
0-5%:               White
5-15%:              Green
15-30%:             Bright Green
30-50%:             Yellow
50-75%:             Bright Yellow
75-100%:            Orange
>100%:              Red (bright) - extreme gain
```

### Borrow Status
```
Easy:        Green
Available:   Yellow
Hard:        Red
N/A:         Dim Gray
```

### Timestamp Freshness (Last_Update)
```
< 1 minute old:     Green
1-5 minutes old:    White
5-15 minutes old:   Yellow
15-30 minutes old:  Orange
> 30 minutes old:   Red (stale data warning)
```

### Financial Metrics (FDF columns, if present)
```
Institutional_Pct:
  > 70%:            Green (stable)
  40-70%:           White
  < 40%:            Yellow (retail heavy)

Short_Interest_Ratio:
  < 3 days:         Green (safe)
  3-7 days:         Yellow
  > 7 days:         Red (squeeze risk)

Net_Income_TTM:
  Positive:         Green
  Negative:         Red

Debt_To_Equity:
  < 1.0:            Green (healthy)
  1.0-2.0:          Yellow
  > 2.0:            Red (risky)
```

### Symbol Highlighting
```
Highlighted symbols:  Reverse video or bold + cyan background
```

## Table Layout

### Header Row
```
┏━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━┓
┃ Symbol ┃ Checkpoint-A     ┃ Checkpoint-B    ┃ Gain %  ┃ Live %      ┃ Borrow       ┃ Last Update   ┃                     ┃
┃        ┃ TIME   | PRICE   ┃ TIME   | PRICE  ┃         ┃             ┃ Status       ┃               ┃                     ┃
┡━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━┩
```

### Data Rows (with color coding)
```
│ NVRI   │ 11/20 16:30 | 13.57 │ 11/21 06:24 | 18.01 │ 32.72   │ 39.79 ✓     │ Easy         │ 11/21 09:15:16  │
│ AZTA   │ 11/20 16:30 | 30.00 │ 11/21 06:49 | 33.01 │ 10.03   │ 21.67 ✓     │ N/A          │ 11/21 09:15:16  │
```

### Summary Row
```
├────────┼──────────────────┼─────────────────┼─────────┼─────────────┼──────────────┼───────────────┼─────────────────────┤
│ Total: │ 45 stocks        │ Avg Gain: 24.5% │ Min: 5% │ Max: 150.2% │ Easy: 12     │ Refreshed:    │ 2025-11-21 09:20:45 │
│        │                  │                 │         │             │ Available: 8 │               │                     │
│        │                  │                 │         │             │ Hard: 3      │               │                     │
│        │                  │                 │         │             │ N/A: 22      │               │                     │
└────────┴──────────────────┴─────────────────┴─────────┴─────────────┴──────────────┴───────────────┴─────────────────────┘
```

### Status Bar (below table)
```
📊 GTTviewer | File: GusTradeTable_21nov2025.txt | Rows: 45 | Filtered: 0 | Sort: live_percentage ↓ | Refresh: 5s | Press Ctrl+C to exit
```

## Implementation Details

### File Parsing
```python
def load_gtt_file(file_path: str) -> pd.DataFrame:
    """
    Load GTT file as tab-delimited
    - Auto-detect columns
    - Handle missing values (N/A)
    - Parse timestamps
    - Convert numeric columns
    """
    df = pd.read_csv(file_path, sep='\t')

    # Convert numeric columns
    numeric_cols = ['CheckPoint-A PRICE', 'CheckPoint-B PRICE',
                   'Gain_Percent', 'live_percentage']
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    # Parse timestamps
    if 'Last_Update' in df.columns:
        df['Last_Update'] = pd.to_datetime(df['Last_Update'], errors='coerce')

    return df
```

### Color Coding Logic
```python
def get_gain_color(gain: float) -> str:
    """Return Rich color string based on gain percentage"""
    if pd.isna(gain):
        return "dim white"
    if gain < 0:
        return "bold red"
    elif gain < 5:
        return "white"
    elif gain < 15:
        return "green"
    elif gain < 30:
        return "bright_green"
    elif gain < 50:
        return "yellow"
    elif gain < 75:
        return "bright_yellow"
    elif gain < 100:
        return "orange"
    else:
        return "bright_red"  # Extreme gain

def get_borrow_color(status: str) -> str:
    """Return Rich color string for borrow status"""
    status = str(status).upper()
    if status == "EASY":
        return "green"
    elif status == "AVAILABLE":
        return "yellow"
    elif status == "HARD":
        return "red"
    else:  # N/A
        return "dim white"

def get_timestamp_color(timestamp: pd.Timestamp) -> str:
    """Return color based on timestamp age"""
    if pd.isna(timestamp):
        return "dim white"

    age_minutes = (datetime.now() - timestamp).total_seconds() / 60

    if age_minutes < 1:
        return "green"
    elif age_minutes < 5:
        return "white"
    elif age_minutes < 15:
        return "yellow"
    elif age_minutes < 30:
        return "orange"
    else:
        return "red"
```

### Display Rendering
```python
from rich.console import Console
from rich.table import Table
from rich.live import Live

def create_table(df: pd.DataFrame, args) -> Table:
    """
    Create Rich Table from DataFrame
    - Apply filtering
    - Apply sorting
    - Add color coding
    - Calculate summary stats
    """
    # Apply filters
    if args.filter_min_gain:
        df = df[df['live_percentage'] >= args.filter_min_gain]

    if args.filter_borrow:
        df = df[df['Borrow_Status'] == args.filter_borrow]

    # Apply sorting
    if args.sort_by in df.columns:
        df = df.sort_values(
            by=args.sort_by,
            ascending=(args.sort_order == 'asc')
        )

    # Limit rows
    if args.max_rows:
        df = df.head(args.max_rows)

    # Create Rich table
    table = Table(
        title=f"📊 GusTradeTable Viewer",
        show_header=True,
        header_style="bold magenta",
        border_style="blue"
    )

    # Add columns based on display mode
    visible_columns = get_visible_columns(df, args)

    for col in visible_columns:
        table.add_column(col, justify="left")

    # Add rows with color coding
    for _, row in df.iterrows():
        styled_row = []
        for col in visible_columns:
            value = row[col]
            color = get_color_for_column(col, value)
            styled_row.append(f"[{color}]{value}[/{color}]")

        table.add_row(*styled_row)

    # Add summary row
    add_summary_row(table, df, visible_columns)

    return table
```

### Live Refresh Loop
```python
def run_viewer(args):
    """Main viewer loop with live refresh"""
    console = Console()

    with Live(console=console, refresh_per_second=1) as live:
        while True:
            try:
                # Load GTT file
                df = load_gtt_file(args.input_GTT)

                # Create table
                table = create_table(df, args)

                # Update display
                live.update(table)

                # Wait for refresh interval
                time.sleep(args.refresh_interval)

            except KeyboardInterrupt:
                console.print("\n✋ Viewer stopped by user", style="yellow")
                break
            except Exception as e:
                console.print(f"❌ Error: {e}", style="red")
                time.sleep(args.refresh_interval)
```

### File Monitoring (Optional Enhancement)
```python
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class GTTFileHandler(FileSystemEventHandler):
    """Monitor GTT file for changes and trigger reload"""
    def __init__(self, file_path, callback):
        self.file_path = file_path
        self.callback = callback

    def on_modified(self, event):
        if event.src_path == self.file_path:
            self.callback()
```

## Column Management

### Default Columns by Display Mode

**Compact:**
- Symbol
- CheckPoint-A PRICE
- Gain_Percent
- live_percentage
- Borrow_Status
- Last_Update (time only)

**Normal:**
- Symbol
- CheckPoint-A TIME
- CheckPoint-A PRICE
- CheckPoint-B TIME
- CheckPoint-B PRICE
- Gain_Percent
- live_percentage
- Borrow_Status
- Last_Update

**Detailed:**
- All columns from GTT file
- If FDF enriched: Float, Institutional_Pct, Short_Interest, Market_Cap
- Full timestamp precision

### Dynamic Column Detection
```python
def detect_enrichment_columns(df: pd.DataFrame) -> dict:
    """
    Detect which enrichment columns are present
    Returns dict of column categories
    """
    enrichment = {
        'base': ['Symbol', 'CheckPoint-A TIME', 'CheckPoint-A PRICE',
                'CheckPoint-B TIME', 'CheckPoint-B PRICE', 'Gain_Percent',
                'live_percentage', 'Borrow_Status', 'Last_Update'],
        'fdf_critical': ['Float', 'Institutional_Pct', 'Short_Interest',
                        'Short_Interest_Ratio', 'Shortable_Shares'],
        'fdf_financial': ['Net_Income_TTM', 'Revenue_TTM', 'Profit_Margin',
                         'ROE', 'Debt_To_Equity'],
        'fdf_valuation': ['Market_Cap', 'Shares_Outstanding', 'EPS',
                         'PE_Ratio', 'Beta'],
        'fdf_market': ['FDF_Current_Price', 'FDF_Volume', 'FDF_Bid',
                      'FDF_Ask', 'FDF_Timestamp']
    }

    present = {}
    for category, cols in enrichment.items():
        present[category] = [c for c in cols if c in df.columns]

    return present
```

## Summary Statistics

### Calculated Metrics
- Total stocks displayed
- Average gain percentage
- Min/Max gain percentages
- Borrow status distribution (counts)
- Time range (oldest to newest checkpoint)
- Last refresh timestamp
- File size and modification time

### Summary Display Format
```
┌─────────────────────── Summary Statistics ───────────────────────┐
│ Total Stocks: 45                    │ Avg Gain: 24.5%            │
│ Gain Range: 5.2% to 150.3%         │ Median: 18.7%              │
│ Borrow: Easy(12), Avail(8), Hard(3)│ N/A(22)                    │
│ Time Range: 11/21 06:24 - 11/21 09:10                           │
│ File Modified: 11/21 09:15:16      │ Refreshed: 11/21 09:20:45  │
└──────────────────────────────────────────────────────────────────┘
```

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

### Invalid File Format
```
❌ Error: Invalid GTT file format

   Expected tab-delimited file with headers:
   - Symbol (required)
   - CheckPoint-A TIME
   - CheckPoint-A PRICE
   ...

   Found columns: Symbol, Price, Gain
   Missing columns: CheckPoint-A TIME, CheckPoint-B TIME
```

### Empty File
```
⚠️  Warning: GTT file is empty

   Waiting for data... (refreshing every 5 seconds)
```

### Parse Errors
```
⚠️  Warning: Could not parse row 15
   Symbol: NVRI
   Error: Invalid timestamp format

   Skipping row and continuing...
```

## Performance Considerations

### Large Files (>1000 rows)
- Use pagination or max-rows limit
- Optimize DataFrame operations
- Consider caching parsed data

### Refresh Rate
- Minimum recommended: 1 second
- Default: 5 seconds
- High-frequency: 1-3 seconds (may flicker)
- Low-frequency: 10+ seconds (stable display)

### Terminal Size
- Auto-detect terminal dimensions
- Adjust column widths dynamically
- Hide less important columns on narrow terminals
- Provide horizontal scrolling hint if needed

## Testing Plan

### Unit Tests
1. GTT file parsing (valid, invalid, empty)
2. Color coding functions
3. Filtering logic
4. Sorting logic
5. Summary statistics calculations

### Integration Tests
1. End-to-end display rendering
2. File monitoring and reload
3. Argument parsing
4. Error handling flows

### Manual Tests
1. Display on different terminal sizes
2. Refresh with file updates
3. Filter and sort combinations
4. Color rendering in different terminals
5. Performance with large files

## Future Enhancements (v2.0+)

### Interactive Mode
- Keyboard controls for sorting/filtering
- Press 's' to change sort column
- Press 'f' to set filters
- Press 'r' to change refresh rate
- Press 'h' for help overlay

### Multi-File Comparison
```bash
--compare <file1> <file2>  # Side-by-side comparison
```

### Export Features
```bash
--export-filtered <path>   # Save filtered view to new GTT
--export-html <path>       # Export as HTML table
--export-image <path>      # Screenshot as image
```

### Alerts
```bash
--alert-gain <percent>     # Alert when stock crosses threshold
--alert-symbol <symbol>    # Alert on specific symbol activity
```

### Charts and Graphs
- Sparkline gain trends
- Distribution histogram
- Sector breakdown (if sector data present)

## Dependencies

### Required
```
rich>=13.0.0
pandas>=1.5.0
pytz>=2022.1
```

### Optional
```
watchdog>=2.1.0  # File monitoring
```

## File Structure
```
scripts_functions/
└── GTTviewer/
    ├── GTTviewer.py              # Main script
    ├── GTTVIEWER_DESIGN_PLAN.md  # This document
    ├── GTTVIEWER_README.md       # User documentation
    └── tests/
        ├── test_parser.py
        ├── test_colors.py
        ├── test_filters.py
        └── sample_GTT_files/
            ├── basic.txt
            ├── enriched.txt
            └── empty.txt
```

## Development Roadmap

### Phase 1: Core Features (v1.0)
- [x] Design plan
- [ ] Argument parsing
- [ ] GTT file parser
- [ ] Basic table display
- [ ] Color coding
- [ ] Live refresh loop
- [ ] Summary statistics
- [ ] Basic error handling

### Phase 2: Enhanced Features (v1.1)
- [ ] Filtering (gain, borrow status)
- [ ] Sorting (any column)
- [ ] Column visibility control
- [ ] Display modes (compact/normal/detailed)
- [ ] Symbol highlighting
- [ ] File monitoring
- [ ] Comprehensive error messages

### Phase 3: Advanced Features (v1.2)
- [ ] Auto-detect terminal size
- [ ] Dynamic column adjustment
- [ ] Export filtered views
- [ ] Logging
- [ ] Unit tests
- [ ] Documentation

### Phase 4: Interactive Mode (v2.0)
- [ ] Keyboard controls
- [ ] Multi-file comparison
- [ ] Alerts and notifications
- [ ] Charts and sparklines

## Notes

- Uses Rich library extensively (already a project dependency)
- Maintains compatibility with GTT tab-delimited format
- No modification of source GTT files (read-only)
- Graceful degradation for missing columns
- Terminal size awareness for responsive design
- Color scheme optimized for dark terminals (adjustable)

---

**Status**: Design Complete - Ready for Implementation
**Last Updated**: 2025-11-21
**Version**: 1.0 Design
