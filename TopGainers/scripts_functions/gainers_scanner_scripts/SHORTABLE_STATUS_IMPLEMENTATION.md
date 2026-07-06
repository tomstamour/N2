# IBKR-Style Shortable Status Implementation

## Overview

This document details the implementation of IBKR-style shortable status labeling in the Gainers Scanner Script, replacing the previous custom "Easy/Available/Hard" terminology with official IBKR-based labels and adding raw tick data capture.

**Implementation Date**: November 23, 2025
**Version**: GUS3 Gainers Scanner v3.9+

---

## Summary of Changes

### What Changed

**Replaced:**
- Single `Borrow_Status` column with custom labels ("Easy", "Available", "Hard", "N/A")

**With:**
- **Shortable_Status**: IBKR-style interpreted label (HIGH_AVAIL/MODERATE_AVAIL/LOW_AVAIL/NOT_SHORTABLE/N/A)
- **Shortable_Difficulty**: Raw tick 46 value (0.0-3.0+)
- **Shortable_Shares**: Raw tick 89 exact share count

### Why This Was Needed

1. **Technical Accuracy**: Use terminology aligned with IBKR's official API documentation
2. **Data Transparency**: Preserve raw tick values for custom analysis and verification
3. **Enhanced Information**: Capture both difficulty level AND exact share count
4. **Industry Standard**: Match IBKR TWS GUI indicators (light green, dark green, red)

---

## Technical Implementation

### 1. New `tickSize()` Callback

**File**: `Gainers_Scanner_Script.py`
**Lines**: 487-506

Added new callback method to capture tick 89 (exact shortable shares count):

```python
def tickSize(self, reqId: int, tickType: int, size: int):
    """Callback for size tick data (including exact shortable shares count)."""
    super().tickSize(reqId, tickType, size)

    # Get symbol from request ID
    symbol = None
    for sym, rid in getattr(self, 'mkt_request_map', {}).items():
        if rid == reqId:
            symbol = sym
            break

    if symbol:
        with self.market_data_lock:
            if symbol not in app_state["borrow_info"]:
                app_state["borrow_info"][symbol] = {}

            # TickType 89 = Shortable shares count (exact number of shares available)
            if tickType == 89:
                app_state["borrow_info"][symbol]['shares_count'] = size
                logger.debug(f"{symbol}: Shortable shares count (tick 89) = {size:,}")
```

**Purpose**: Capture exact shortable share counts from IBKR API
**Complements**: Existing `tickGeneric()` callback for tick 46 (difficulty level)

---

### 2. Updated GTT Header

**File**: `Gainers_Scanner_Script.py`
**Lines**: 598-616

Modified header in `write_complete_gtt()` function:

```python
header = [
    'Symbol',
    'CheckPoint-A TIME',
    'CheckPoint-A PRICE',
    'CheckPoint-B TIME',
    'CheckPoint-B PRICE',
    'Gain_Percent',
    'live_percentage',
    'Sector',
    'Industry',
    'Shortable_Status',      # NEW: IBKR-style label
    'Shortable_Difficulty',  # NEW: Raw tick 46 value
    'Shortable_Shares',      # NEW: Raw tick 89 count
    'Float',
    'Net_Income_TTM',
    'Institutional_Pct',
    'Borrow_Fee_Rate',
    'Last_Update'
]
```

**Changes**:
- Removed: `Borrow_Status` (single column)
- Added: Three new columns for comprehensive shortable data

---

### 3. Updated Label Mapping Logic

**File**: `Gainers_Scanner_Script.py`
**Lines**: 1464-1487

Replaced custom labels with IBKR-style terminology:

```python
# Step 5: Get borrow info (shortable shares from market data)
if self.args.historical_mode:
    shortable_status = "N/A"
    shortable_difficulty = None
    shortable_shares_count = None
else:
    borrow_info = app_state["borrow_info"].get(symbol, {})
    shortable_difficulty = borrow_info.get('shortable', None)  # tick 46
    shortable_shares_count = borrow_info.get('shares_count', None)  # tick 89

    # Interpret shortable difficulty value for IBKR-style status label
    if shortable_difficulty is not None:
        if shortable_difficulty > 2.5:
            shortable_status = "HIGH_AVAIL"  # ≥1,000 shares
        elif shortable_difficulty > 1.5:
            shortable_status = "MODERATE_AVAIL"  # Locatable
        elif shortable_difficulty > 0:
            shortable_status = "LOW_AVAIL"  # Hard to borrow
        else:
            shortable_status = "NOT_SHORTABLE"  # Not available
    else:
        shortable_status = "N/A"
```

**Mapping Table**:

| Tick 46 Value | Old Label | New Label | IBKR TWS Equivalent |
|---------------|-----------|-----------|---------------------|
| > 2.5 | Easy | HIGH_AVAIL | Light green indicator |
| 1.5-2.5 | Available | MODERATE_AVAIL | Dark green indicator |
| 0-1.5 | Hard | LOW_AVAIL | Red indicator |
| ≤ 0 | N/A | NOT_SHORTABLE | Not shortable |
| No data | N/A | N/A | No data available |

---

### 4. Updated Data Collection

**File**: `Gainers_Scanner_Script.py`
**Lines**: 1511-1533

Modified return statement in `process_single_stock()` to include new fields:

```python
return {
    'symbol': symbol,
    'checkpoint_a_price': checkpoint_a_price,
    'current_price': current_price,
    'peak_price': peak_price,
    'peak_time': peak_time,
    'gain_percent': live_pct,
    'peak_percent': peak_pct,
    'crossed_threshold': peak_pct >= percent_threshold,
    # FUNDAMENTAL FIELDS:
    'float_shares': yfinance_data.get('float_shares'),
    'net_income_ttm': yfinance_data.get('net_income_ttm'),
    'institutional_pct': yfinance_data.get('institutional_pct'),
    'sector': yfinance_data.get('sector'),
    'industry': yfinance_data.get('industry'),
    # SHORTABLE INFO (NEW):
    'shortable_status': shortable_status,           # Interpreted label
    'shortable_difficulty': shortable_difficulty,   # Raw tick 46
    'shortable_shares': shortable_shares_count,     # Raw tick 89
    # BORROW FEE:
    'borrow_fee_rate': borrow_fee_rate
}
```

---

### 5. Updated GTT Write Logic

**File**: `Gainers_Scanner_Script.py`
**Lines**: 645-672

Modified row data formatting:

```python
# NEW: Get shortable status and raw tick values
shortable_status = stock.get('shortable_status', 'N/A')
shortable_difficulty = stock.get('shortable_difficulty')
shortable_shares = stock.get('shortable_shares')

row = [
    stock['symbol'],
    stock['checkpoint_a_time'],
    f"{stock['checkpoint_a_price']:.2f}",
    stock['checkpoint_b_time'],
    f"{stock['checkpoint_b_price']:.2f}",
    f"{stock['gain_percent']:.2f}",
    f"{stock['live_percentage']:.2f}",
    str(sector_val) if sector_val is not None else 'N/A',
    str(industry_val) if industry_val is not None else 'N/A',
    # SHORTABLE DATA (IBKR-style):
    shortable_status,
    f"{shortable_difficulty:.2f}" if shortable_difficulty is not None else 'N/A',
    str(shortable_shares) if shortable_shares is not None else 'N/A',
    # FUNDAMENTAL FIELDS:
    str(float_val) if float_val is not None else 'N/A',
    str(net_income_val) if net_income_val is not None else 'N/A',
    f"{inst_pct_val:.2f}" if inst_pct_val is not None else 'N/A',
    f"{borrow_fee_val:.2f}" if borrow_fee_val is not None else 'N/A',
    stock['last_update']
]
```

---

## IBKR API Reference

### Tick 46: Shortable Difficulty

**Type**: `tickGeneric()`
**Format**: Float (0.0-3.0+)
**Meaning**:
- Numeric indicator of borrow difficulty
- Higher values = easier to borrow
- Based on IBKR's internal availability calculation

**Request Method**:
```python
self.client.reqMktData(
    reqId=req_id,
    contract=contract,
    genericTickList='236',  # Requests tick 46
    snapshot=False,
    regulatorySnapshot=False,
    mktDataOptions=[]
)
```

**Callback**:
```python
def tickGeneric(self, reqId: int, tickType: int, value: float):
    if tickType == 46:  # Shortable difficulty
        app_state["borrow_info"][symbol]['shortable'] = value
```

---

### Tick 89: Shortable Shares Count

**Type**: `tickSize()`
**Format**: Integer
**Meaning**:
- Exact number of shares available for shorting
- Direct count from IBKR's locate system

**Request Method**:
Same as tick 46 (`genericTickList='236'`)

**Callback** (NEW):
```python
def tickSize(self, reqId: int, tickType: int, size: int):
    if tickType == 89:  # Exact share count
        app_state["borrow_info"][symbol]['shares_count'] = size
```

---

## Data Availability

### Live Mode (During Market Hours)

**When Data is Available**:
- Most US-listed stocks on major exchanges (NYSE, NASDAQ)
- Large-cap and mid-cap stocks
- Stocks with active short interest

**When Data May Be N/A**:
- OTC stocks (OTCBB, Pink Sheets)
- Foreign ADRs (especially thinly traded)
- Micro-cap stocks (< $50M market cap)
- Recently IPO'd stocks (< 30 days)
- Stocks with no borrow availability at IBKR

---

### Historical Mode

**All Shortable Columns Set to N/A**:
- `Shortable_Status`: N/A
- `Shortable_Difficulty`: N/A
- `Shortable_Shares`: N/A

**Reason**: IBKR does not provide historical borrow status data via API

**Available in Historical Mode**:
- `Borrow_Fee_Rate`: YES (from multi-source enrichment: FTP + iBorrowDesk)
- Fundamental data: YES (from yfinance)
- Price data: YES (from IBKR historical bars)

---

## Example GTT Output

### Live Mode (Market Hours)

```
Symbol	Shortable_Status	Shortable_Difficulty	Shortable_Shares	Borrow_Fee_Rate
AAPL	HIGH_AVAIL	2.85	50000	0.30
TSLA	MODERATE_AVAIL	2.10	5000	1.25
NVDA	LOW_AVAIL	1.20	500	8.50
RBTK	N/A	N/A	N/A	6.49
```

### Historical Mode (After Hours / Weekend)

```
Symbol	Shortable_Status	Shortable_Difficulty	Shortable_Shares	Borrow_Fee_Rate
AAPL	N/A	N/A	N/A	0.30
TSLA	N/A	N/A	N/A	1.25
NVDA	N/A	N/A	N/A	8.50
RBTK	N/A	N/A	N/A	6.49
```

**Note**: Borrow_Fee_Rate is still populated via multi-source enrichment even in historical mode.

---

## Migration Guide

### For Existing GTT Files

**Old Format (Before v3.9)**:
```
Symbol	...	Borrow_Status	...
AAPL	...	Easy	...
TSLA	...	Available	...
NVDA	...	Hard	...
```

**New Format (v3.9+)**:
```
Symbol	...	Shortable_Status	Shortable_Difficulty	Shortable_Shares	...
AAPL	...	HIGH_AVAIL	2.85	50000	...
TSLA	...	MODERATE_AVAIL	2.10	5000	...
NVDA	...	LOW_AVAIL	1.20	500	...
```

### Label Conversion

If you have old GTT files with `Borrow_Status` column:

| Old Label | New Label | Estimated Tick 46 Range |
|-----------|-----------|------------------------|
| Easy | HIGH_AVAIL | 2.5-3.0+ |
| Available | MODERATE_AVAIL | 1.5-2.5 |
| Hard | LOW_AVAIL | 0.0-1.5 |
| N/A | N/A | No data |

**Script Migration**: New scanner runs will automatically use the new format. No changes needed to command-line arguments.

---

## Code Locations Summary

### Files Modified

**Primary File**: `scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py`

**Key Sections**:

1. **tickSize() Callback Added**: Lines 487-506
   - Captures tick 89 (exact share count)

2. **GTT Header Updated**: Lines 598-616
   - Three new columns: Shortable_Status, Shortable_Difficulty, Shortable_Shares

3. **Label Mapping Logic**: Lines 1464-1487
   - IBKR-style label interpretation

4. **Data Collection**: Lines 1511-1533
   - Return statement with new fields

5. **GTT Write Logic**: Lines 645-672
   - Row formatting for new columns

6. **Legacy Function Updated**: Lines 559-578
   - `create_gtt_header()` (for consistency, though not actively used)

7. **Docstring Updated**: Lines 580-602
   - `write_complete_gtt()` documentation

---

## Performance Impact

**Minimal Performance Change**:
- Same API request (`genericTickList='236'`)
- Additional callback handler (`tickSize()`) - negligible overhead
- Same network latency (tick 89 comes with tick 46 in same request)
- Slightly larger GTT files (two additional columns)

**Storage Impact**:
- Old format: ~80 bytes per row
- New format: ~120 bytes per row (+50% due to three columns vs one)
- For 50 stocks: ~2 KB additional per GTT file

---

## Testing

### Syntax Validation

```bash
python3 -m py_compile ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py
```

**Result**: ✓ All syntax checks passed

### Live Testing

**Test Command**:
```bash
python3 ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-25/09:30" \
  --gap-percentage 10 \
  --client-id 702 \
  --output-dir ./runs/$(date +%d-%b-%Y) \
  --no-new-window
```

**Expected Behavior**:
- GTT file contains three new columns
- Live mode: Shortable columns populated for most stocks
- Historical mode: Shortable columns show N/A
- Borrow_Fee_Rate still populated via enrichment

---

## Troubleshooting

### Issue: All Shortable Columns Show N/A (Live Mode)

**Possible Causes**:
1. Running outside market hours (9:30 AM - 4:00 PM ET)
2. Stock types not supported (OTC, foreign ADRs)
3. IBKR API subscription issues
4. Client ID conflict

**Solutions**:
- Verify market is open
- Test with well-known stocks (AAPL, TSLA, MSFT)
- Check IB Gateway API settings
- Use unique client ID

---

### Issue: Tick 89 Always N/A (But Tick 46 Works)

**Possible Cause**: IBKR doesn't provide tick 89 for all stocks

**Note**: Tick 89 requires TWS Build 974+. Some stocks may return tick 46 but not tick 89.

**Solution**: This is normal behavior. Tick 46 (difficulty) is more commonly available than tick 89 (exact count).

---

### Issue: Historical Mode Shows N/A for Everything

**This is Expected Behavior**:
- IBKR API does not provide historical borrow status
- All three shortable columns will be N/A in historical mode
- Borrow_Fee_Rate should still be populated (via FTP/iBorrowDesk enrichment)

---

## Future Enhancements

### Potential Improvements

1. **Historical Borrow Status Logging**:
   - Create separate logging system to capture tick 46/89 values in real-time
   - Build internal historical database for future reference
   - Enable backtesting with borrow status data

2. **Third-Party Data Integration**:
   - Integrate iBorrowDesk for historical borrow fee trends
   - Use borrow fee rates as proxy for shortable status in historical mode

3. **Enhanced Filtering**:
   - Add command-line option to filter by shortable status
   - Example: `--min-shortable-difficulty 2.0`

4. **Status Alerts**:
   - Alert when stock changes from HIGH_AVAIL to LOW_AVAIL
   - Notify when exact share count drops below threshold

---

## References

### IBKR API Documentation

- **Official API Docs**: https://interactivebrokers.github.io/tws-api/
- **Tick Types**: https://interactivebrokers.github.io/tws-api/tick_types.html
- **Market Data**: https://interactivebrokers.github.io/tws-api/market_data.html

### Related Documentation

- **GAINERS_SCANNER_README.md**: User-facing scanner documentation
- **CLAUDE.md**: Project overview and GTT format specification
- **GSS_TIME0_README.md**: time0 checkpoint system documentation

---

## Changelog

**Version 3.9** (November 23, 2025):
- ✅ Added `tickSize()` callback for tick 89 capture
- ✅ Replaced `Borrow_Status` with three new columns
- ✅ Implemented IBKR-style label mapping
- ✅ Updated GTT header and write logic
- ✅ Updated documentation (GAINERS_SCANNER_README.md, CLAUDE.md)
- ✅ All syntax validation passed

---

## Support

For questions or issues:
1. Check log files in `./logs/` directory
2. Review this technical documentation
3. Consult GAINERS_SCANNER_README.md for user-facing guidance
4. Test with well-known stocks (AAPL, MSFT, TSLA) during market hours

---

**Document Version**: 1.0
**Last Updated**: November 23, 2025
**Author**: GUS3 Development Team
