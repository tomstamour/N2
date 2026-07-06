# GSS yfinance Integration Summary

## Overview
Successfully refactored the Gainers Scanner Script (GSS) to use yfinance API for fetching Float, Net_Income_TTM, and Institutional_Pct metrics, while adding Sector and Industry classification.

## Changes Made

### 1. **Added yfinance Import** (Line 48-53)
```python
# yfinance import for sector/industry and fundamental data
try:
    import yfinance as yf
except ImportError:
    print("Error: yfinance module not found. Please install it with: pip install yfinance")
    exit(1)
```

### 2. **Updated Global State** (Line 379-380)
Added yfinance caching to `app_state`:
```python
"yfinance_cache": {},  # Cache yfinance data: {symbol: {data, timestamp}}
"yfinance_cache_ttl": 3600,  # Cache TTL in seconds (1 hour)
```

### 3. **Created `get_yfinance_data()` Method** (Line 1209-1303)
New method that fetches 5 metrics from yfinance:
- **float_shares**: Total float (from `info['floatShares']`)
- **net_income_ttm**: Net income TTM (from `info['netIncomeToCommon']`)
- **institutional_pct**: Institutional ownership % (from `info['heldPercentInstitutions']`)
- **sector**: Sector classification (from `info['sector']`)
- **industry**: Industry classification (from `info['industry']`)

Features:
- 1-hour caching (same as IBKR fundamental data)
- 0.1s rate limiting between requests
- Graceful error handling (returns None for missing data)

### 4. **Modified `parse_fundamental_metrics()` Function** (Line 209-247)
**REMOVED** parsing for:
- Float (now from yfinance)
- Net Income TTM (now from yfinance)
- Institutional % (now from yfinance)

**KEPT** parsing for:
- Borrow Fee Rate (IBKR-only metric)

### 5. **Modified `get_fundamental_data()` Method** (Line 1037-1130)
- Reduced IBKR API report requests from 4 to 1 (only ReportsFinSummary)
- Updated to only fetch Borrow_Fee_Rate
- Float, Net_Income_TTM, Institutional_Pct now handled by yfinance

### 6. **Updated `process_single_stock()` Method** (Line 1314-1343)
Added yfinance data fetching:
```python
# Step 6: Get fundamental data from IBKR (WITH CACHING) - only borrow fee rate
fundamental_data = self.get_fundamental_data(symbol, contract)

# Step 7: Get yfinance data (WITH CACHING) - Float, Net Income TTM, Institutional %, Sector, Industry
yfinance_data = self.get_yfinance_data(symbol)
```

Return dict now includes:
- `sector`: From yfinance
- `industry`: From yfinance
- `float_shares`: From yfinance (replaces IBKR)
- `net_income_ttm`: From yfinance (replaces IBKR)
- `institutional_pct`: From yfinance (replaces IBKR)
- `borrow_fee_rate`: From IBKR (unchanged)
- `shortable_shares`: From IBKR (unchanged)

### 7. **Updated GTT File Format** (Line 565-632)

**New Header (16 columns):**
```
Symbol | CheckPoint-A TIME | CheckPoint-A PRICE | CheckPoint-B TIME | CheckPoint-B PRICE |
Gain_Percent | live_percentage | Sector | Industry | Borrow_Status | Float | Net_Income_TTM |
Institutional_Pct | Borrow_Fee_Rate | Shortable_Shares | Last_Update
```

**Changes:**
- Position 7: **Sector** (NEW)
- Position 8: **Industry** (NEW)
- Total columns: 9 → 16

### 8. **Updated `load_existing_gtt()` Function** (Line 640-744)
Added backward compatibility for 3 formats:
- **Old format** (9 columns): No fundamental data, no sector/industry
- **Mid format** (14 columns): With fundamentals, no sector/industry
- **New format** (16 columns): With fundamentals and sector/industry

### 9. **Updated `process_stocks()` Method**
Added sector/industry to stock data tracking in two locations:

**New stock creation** (Line 1457-1459):
```python
'sector': result.get('sector'),
'industry': result.get('industry'),
```

**Existing stock update** (Line 1488-1490, 1559-1561):
```python
# Update sector/industry data (may have changed or been enriched)
tracked_stocks[symbol]['sector'] = result.get('sector')
tracked_stocks[symbol]['industry'] = result.get('industry')
```

## Benefits

### Performance Improvements
1. **Reduced IBKR API Calls**: From 4 fundamental reports down to 1
2. **Faster Data Fetching**: yfinance is generally faster than IBKR for fundamental data
3. **Parallel Processing**: Yfinance calls benefit from existing ThreadPoolExecutor batching

### Data Quality Improvements
1. **More Reliable Float Data**: yfinance typically has more consistent float share counts
2. **Better Institutional Ownership**: Direct from yfinance vs. calculated from IBKR ownership data
3. **Added Context**: Sector/Industry classification helps with Gap Up Short strategy analysis

### Code Maintainability
1. **Simpler IBKR Integration**: Only fetch borrow-specific metrics from IBKR
2. **Better Separation of Concerns**: Each API fetches what it's best at
3. **Maintained Caching**: Both IBKR and yfinance use 1-hour cache TTL

## Testing Notes

### Syntax Validation
✓ Python syntax check passed

### Manual Testing Required
Since the system Python environment doesn't have the full dependency stack, manual testing is recommended:

1. **Basic Functionality Test**:
   ```bash
   python3 ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
     --time0 "2025-11-21/09:30" \
     --gap-percentage 10 \
     --client-id 702 \
     --output-dir ./runs/test/ \
     --max-stocks 3 \
     --no-new-window
   ```

2. **Verify GTT Output**:
   - Check that GTT file has 16 columns
   - Verify Sector and Industry columns are populated
   - Confirm Float, Net_Income_TTM, Institutional_Pct have data

3. **Check Logs**:
   - Look for "yfinance data complete" messages
   - Verify no errors fetching from yfinance API
   - Confirm caching is working (age messages)

4. **Backward Compatibility**:
   - Test loading old GTT files (9 or 14 columns)
   - Verify they upgrade to new format on save

## Migration Notes

### For Existing Users
- **Old GTT files remain compatible**: Script will auto-upgrade format on next write
- **No config changes needed**: All settings remain command-line based
- **Dependencies**: Ensure `yfinance` is installed (`pip install yfinance`)

### Data Source Summary
| Metric | Old Source | New Source | Notes |
|--------|-----------|-----------|-------|
| Float | IBKR | yfinance | More reliable |
| Net_Income_TTM | IBKR | yfinance | Direct field vs. parsed XML |
| Institutional_Pct | IBKR (calculated) | yfinance | Direct field |
| Sector | N/A | yfinance | NEW |
| Industry | N/A | yfinance | NEW |
| Borrow_Fee_Rate | IBKR | IBKR | Unchanged |
| Shortable_Shares | IBKR | IBKR | Unchanged |

## Files Modified
- `scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py`

## Files Created
- `test_yfinance_integration.py` (test helper)
- `YFINANCE_INTEGRATION_SUMMARY.md` (this file)
