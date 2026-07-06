# GSS Time0 Refactoring Summary

## Overview
Complete refactoring of Gainers Scanner Script (GSS) from "previous close" comparison to "time0 checkpoint" tracking with accumulative monitoring.

**Date**: 2025-11-18
**Version**: 3.10 (Time0 Tracking)
**Previous Version**: 3.9 (Previous Close)

---

## Major Changes

### 1. Argument Changes ✓

**Removed**:
- `--output` (CSV file path)

**Added**:
- `--time0` (required) - Reference time checkpoint in format `YYYY-MM-DD/HH:MM`
- `--output-dir` (optional) - Directory for auto-generated GTT file

**Example Migration**:
```bash
# OLD (v3.9)
python Gainers_Scanner_Script.py \
  --output gainers.csv \
  --gap-percentage 50 \
  --client-id 300

# NEW (v3.10)
python Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:45" \
  --gap-percentage 50 \
  --client-id 300 \
  --output-dir ./data
```

### 2. Core Logic Change ✓

**Before (Previous Close)**:
- Calculated gains from previous trading day's close to current price
- Formula: `((current - prev_close) / prev_close) × 100`
- Method: `get_previous_close()`

**After (Time0 Checkpoint)**:
- Calculates gains from user-specified time0 to current price
- Formula: `((current - time0_price) / time0_price) × 100`
- Method: `get_price_at_time0()`
- Time0 prices are cached for efficiency

**Code Changes**:
- `get_previous_close()` → `get_price_at_time0()` (lines 512-598)
- Uses 1-minute bars around time0 for precision
- Finds closest bar to target time
- Caches results in `app_state["time0_prices"]`

### 3. Tracking Behavior ✓

**Before**:
- Results replaced on each scan
- No accumulation
- No history of threshold crossings

**After**:
- **Accumulative tracking** - Stocks persist once added
- **Checkpoint-B recording** - Captures moment of threshold crossing
- **Live percentage updates** - Continuous monitoring of existing stocks
- **Tracked stocks dictionary** - `app_state["tracked_stocks"]`

**Logic Flow**:
```python
For each scanned stock:
  if stock NOT in tracked_stocks:
    if gain >= threshold:
      - Record Checkpoint-B (time/price)
      - Add to GTT file
      - Add to tracked_stocks
  else:  # Already tracked
    - Calculate new live_percentage
    - Update GTT file
    - Update tracked_stocks
```

### 4. Output Format Change ✓

**Before (CSV)**:
- Filename: User-specified (e.g., `gainers.csv`)
- Format: CSV (comma-separated)
- Columns: `Symbol,Prev_Close,Current_Price,Gain_Percent,Borrow_Status,Scan_Time`

**After (GTT - GusTradeTable)**:
- Filename: Auto-generated as `GusTradeTable_DDmmmYYYY.txt`
- Format: Tab-delimited text
- Columns: `Symbol, CheckPoint-A TIME, CheckPoint-A PRICE, CheckPoint-B TIME, CheckPoint-B PRICE, Gain_Percent, live_percentage, Borrow_Status, Last_Update`

**New Columns**:
1. `CheckPoint-A TIME` - time0 value
2. `CheckPoint-A PRICE` - Price at time0
3. `CheckPoint-B TIME` - Time when threshold first crossed
4. `CheckPoint-B PRICE` - Price at threshold crossing
5. `live_percentage` - Current gain % (updates every scan)

### 5. File Operations ✓

**New Functions**:
- `create_gtt_header()` - Creates tab-delimited header
- `append_stock_to_gtt()` - Adds new stock to file
- `update_stock_live_percentage()` - Updates existing stock's live %
- `load_existing_gtt()` - Resume support from existing file

**File Handling**:
- Tab-delimited for easy Excel import
- Incremental updates (not full rewrites)
- Resume support (loads tracked stocks on restart)

### 6. Display Updates ✓

**New Display Columns**:
- CP-A Price (Checkpoint-A)
- CP-B Time (when crossed threshold)
- CP-B Price (price at crossing)
- Gain % (at Checkpoint-B)
- Live % (current, color-coded)

**Enhanced Color Coding**:
- Live % shows green when increasing above Checkpoint-B
- Live % shows yellow when decreasing
- Helps identify strengthening vs weakening momentum

**Title Update**:
- Shows time0 value: `Threshold: 50% | Time0: 2025-11-14/09:45`

### 7. Utility Functions ✓

**New Functions**:
- `parse_time0(time0_str)` - Parse and validate time0 argument
- `generate_gtt_filename(output_dir)` - Create dated filename
- `format_time_for_gtt(dt)` - Format datetime for GTT output

**Validation**:
- time0 cannot be in future
- time0 must be valid datetime
- time0 localized to US/Eastern timezone

### 8. Global State Changes ✓

**Added to app_state**:
```python
{
    "tracked_stocks": {},        # Dict of stocks that crossed threshold
    "time0_datetime": None,      # The reference time checkpoint
    "time0_prices": {},          # Cache of prices at time0
    "gtt_file_path": None,       # Path to GusTradeTable file
}
```

### 9. Method Changes ✓

**Replaced**:
- `calculate_gains()` → `process_stocks()`

**Updated**:
- `run_continuous_scan()` - Now uses accumulative tracking
- `create_display_table()` - New columns and structure
- `__init__()` - Initializes tracked_stocks

**Removed**:
- `export_to_csv()` - Replaced by inline GTT operations
- `get_previous_close()` - Replaced by `get_price_at_time0()`

**Renamed**:
- `run()` → `run_single_scan()` - Clarifies single scan mode

---

## Code Statistics

### Lines of Code
- **Before**: ~820 lines
- **After**: ~1150 lines
- **Added**: ~330 lines (GTT operations, time0 handling, tracking logic)

### Functions Added
1. `parse_time0()` - Parse time0 argument
2. `generate_gtt_filename()` - Generate dated filename
3. `format_time_for_gtt()` - Format datetime for output
4. `create_gtt_header()` - Create GTT header
5. `append_stock_to_gtt()` - Add new stock
6. `update_stock_live_percentage()` - Update existing stock
7. `load_existing_gtt()` - Resume support

### Functions Modified
1. `parse_arguments()` - New time0 and output-dir args
2. `GainersScanner.__init__()` - Initialize tracking state
3. `run_continuous_scan()` - Accumulative logic
4. `create_display_table()` - New columns
5. `main()` - Parse time0, generate GTT filename

### Functions Removed
1. `get_previous_close()` - No longer needed
2. `export_to_csv()` - Replaced by GTT operations

### Functions Replaced
1. `get_previous_close()` → `get_price_at_time0()`
2. `calculate_gains()` → `process_stocks()`
3. `run()` → `run_single_scan()`

---

## Breaking Changes

⚠️ **BREAKING CHANGES - NOT BACKWARD COMPATIBLE**

1. **Arguments Changed**:
   - `--output` removed → must use `--output-dir`
   - `--time0` now required (no default)

2. **Output Format Changed**:
   - CSV → Tab-delimited text
   - Different columns
   - Different filename structure

3. **Logic Completely Different**:
   - Previous close → time0 checkpoint
   - Replace mode → Accumulative mode
   - Single scan focus → Continuous tracking focus

4. **File Naming**:
   - User-specified → Auto-generated with date
   - `gainers.csv` → `GusTradeTable_18nov2025.txt`

5. **Behavior**:
   - Results replaced → Results accumulated
   - No state → Stateful tracking
   - No resume → Resume support

---

## Migration Guide

### For Users

**Old workflow**:
```bash
# Run scanner
python Gainers_Scanner_Script.py \
  --output daily_gainers.csv \
  --gap-percentage 50 \
  --client-id 300

# Results in: daily_gainers.csv (replaced each run)
```

**New workflow**:
```bash
# Run scanner with time0
python Gainers_Scanner_Script.py \
  --time0 "2025-11-14/09:30" \
  --gap-percentage 50 \
  --client-id 300 \
  --output-dir ./data

# Results in: ./data/GusTradeTable_14nov2025.txt (accumulated)
```

### For Developers

**Key refactoring points**:
1. Replace all `CONFIG.get('OUTPUT')` with auto-generated GTT path
2. Replace `get_previous_close()` calls with `get_price_at_time0()`
3. Replace `calculate_gains()` with `process_stocks()`
4. Update display functions to use tracked_stocks dict
5. Add GTT file operations instead of CSV export

---

## New Features

### 1. Checkpoint-A (time0)
- User-defined reference time
- Cached prices for efficiency
- Validates against future times

### 2. Checkpoint-B
- Automatically detected threshold crossing
- Records exact time and price
- Persists for historical analysis

### 3. Live Percentage
- Updates every scan
- Shows momentum (increasing/decreasing)
- Color-coded in display

### 4. Resume Support
- Loads existing GTT on restart
- Continues tracking existing stocks
- Adds new stocks as they qualify

### 5. Daily File Rotation
- New GTT file each day
- Previous days preserved
- Automatic date in filename

---

## Use Case Changes

### Before (Previous Close)
**Use case**: Daily gap scanner
- Find stocks gapping up from previous close
- Morning screening tool
- One-time snapshot

**Example**: "Show me stocks up 50% from yesterday's close"

### After (Time0 Checkpoint)
**Use case**: Timeline tracking
- Track stocks from specific event times
- Continuous accumulation
- Momentum monitoring

**Examples**:
- "Track stocks moving 50% from market open (9:30 AM)"
- "Monitor gains from Fed announcement (2:00 PM)"
- "Find stocks surging from this morning's level (10:00 AM)"

---

## Performance Impact

### Positive
✓ Time0 price cached (1 historical request per stock total)
✓ Incremental file updates (not full rewrites)
✓ Efficient tracked_stocks lookup (dict)

### Neutral
≈ Same number of scanner calls
≈ Same market data requests per scan
≈ Similar API load overall

### Considerations
⚠ GTT file grows as stocks are added (accumulative)
⚠ File I/O on every update (mitigated by incremental writes)
⚠ Memory grows with tracked_stocks (cleared on restart)

---

## Testing Performed

1. ✓ Syntax validation (`py_compile`)
2. ✓ Argument parsing
3. ✓ time0 validation (past/future, format)
4. ✓ GTT filename generation
5. ✓ Time formatting functions

**Still requires** (needs IB connection):
- [ ] Full scan execution
- [ ] Checkpoint-B recording
- [ ] Live percentage updates
- [ ] GTT file operations
- [ ] Resume support
- [ ] Display table rendering

---

## Documentation Updates

### New Files Created
1. `GSS_TIME0_README.md` - Complete usage guide
2. `GSS_QUICK_START.md` - Quick reference
3. `TIME0_REFACTORING_SUMMARY.md` - This file

### Previous Docs (Now Outdated)
- `GAINERS_SCANNER_README.md` - Previous close version
- `QUICK_START.md` - Previous close version
- `REFACTORING_SUMMARY.md` - Previous refactoring

**Note**: Previous docs retained for reference but apply to v3.9

---

## Future Enhancements

Potential additions for future versions:

1. **Multiple time0 Support**: Track from multiple checkpoints simultaneously
2. **Alerts**: Email/SMS when stocks cross threshold
3. **Historical Analysis**: Compare GTT files across days
4. **Visualization**: Charts showing stock progression from time0
5. **Export Formats**: JSON, XML options
6. **Stop Loss Tracking**: Record when stocks fall below threshold
7. **Volume Filters**: Only track high-volume movers
8. **Sector Analysis**: Group by sector in display

---

## Summary

### What Changed
- 🔄 Core logic: Previous close → time0 checkpoint
- 🔄 Behavior: Replace → Accumulate
- 🔄 Output: CSV → Tab-delimited GTT
- 🔄 Arguments: `--output` → `--time0` + `--output-dir`
- ➕ Features: Checkpoint-B, live_percentage, resume support

### Impact
- **Breaking**: Not backward compatible
- **Use Case**: Different (timeline tracking vs daily gaps)
- **Complexity**: Higher (stateful tracking)
- **Flexibility**: Higher (user-defined checkpoints)
- **Power**: Much higher (accumulative monitoring)

### Status
✅ **All refactoring complete**
✅ **Syntax validated**
✅ **Documentation created**
⏳ **Awaiting live testing with IB connection**

---

## File Locations

**Modified**:
- `/home/tom/Documents/ibkr_scripts/GUS2/scripts_functions/Gainers_Scanner_Script.py`

**Created**:
- `/home/tom/Documents/ibkr_scripts/GUS2/scripts_functions/GSS_TIME0_README.md`
- `/home/tom/Documents/ibkr_scripts/GUS2/scripts_functions/GSS_QUICK_START.md`
- `/home/tom/Documents/ibkr_scripts/GUS2/scripts_functions/TIME0_REFACTORING_SUMMARY.md`

**Previous (Retained)**:
- `GAINERS_SCANNER_README.md` (v3.9 docs)
- `QUICK_START.md` (v3.9 docs)
- `REFACTORING_SUMMARY.md` (v3.9 changes)

---

## Version History

- **v3.9**: Previous close comparison, CSV output, replace mode
- **v3.10**: Time0 checkpoint tracking, GTT output, accumulative mode (current)

---

*Refactoring completed: 2025-11-18*
*Total time: Major refactoring (all phases complete)*
*Status: Ready for testing with live IB connection*
