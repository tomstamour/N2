# GSS Peak-Based Tracking Refactor

## Summary

The Gainers Scanner Script (GSS) has been refactored to use **highest price in time window** for gap-percentage threshold checking instead of current price at threshold crossing.

## What Changed

### Previous Behavior
- Stocks were selected if **current price** crossed the gap-percentage threshold
- Checkpoint-B captured the moment when threshold was first crossed
- Once selected, stocks remained tracked regardless of price drops

### New Behavior
- Stocks are selected if **highest price in the time window** (time0 → now) crossed the gap-percentage threshold
- Checkpoint-B captures the **time and price of the peak** (highest point)
- Hybrid approach: First scan fetches historical peak from IBKR, subsequent scans track live peaks
- Peak is continuously updated if a new high is reached
- `live_percentage` continues to show current price vs time0 (unchanged)
- `Gain_Percent` in GTT now shows peak percentage (frozen at peak value)

## Implementation Details

### 1. New Method: `get_highest_price_in_window()`
**Location:** After `get_price_at_time0()` method (~line 943)

**Purpose:** Fetch historical data from time0 to current time and find the bar with the highest price.

**Features:**
- Automatically adjusts bar size and duration based on time window length
- Uses 1-minute bars for windows ≤ 1 day
- Uses 5-minute bars for longer windows (up to 30 days)
- Returns dict with `{peak_price, peak_time, bars}`

**Example:**
```python
peak_data = self.get_highest_price_in_window(symbol, contract)
# Returns: {'peak_price': 125.50, 'peak_time': datetime(...), 'bars': [...]}
```

### 2. Updated: `process_single_stock()` Method
**Location:** ~line 1200

**New Parameters:**
- Added `is_first_scan` parameter (default: False)

**New Logic:**
- **Hybrid Approach:**
  - If `symbol in peak_cache` and not first scan: Use cached peak, compare with current price, update if higher
  - If first scan or not in cache: Fetch historical peak from IBKR via `get_highest_price_in_window()`
- **Two Percentages:**
  - `live_pct`: Current price vs time0 (for `live_percentage` column)
  - `peak_pct`: Peak price vs time0 (for threshold checking and `Gain_Percent` column)
- **Threshold Check:** Now uses `peak_pct >= percent_threshold` (instead of current price)

**New Return Fields:**
```python
{
    'peak_price': 125.50,         # NEW
    'peak_time': datetime(...),   # NEW
    'peak_percent': 25.5,         # NEW
    'gain_percent': 20.0,         # Now represents live percentage
    'crossed_threshold': True     # Now based on peak_pct
}
```

### 3. Updated: `process_stocks()` Method
**Location:** ~line 1317

**New Parameters:**
- Added `scan_iteration` parameter (default: 1)

**New Logic:**
- **Per-Stock First Scan Detection:**
  - Checks if symbol is in `app_state["peak_prices"]` cache
  - Passes appropriate `is_first_scan` value to `process_single_stock()`

- **New Stock Tracking:**
  - Checkpoint-B now uses `result['peak_time']` and `result['peak_price']`
  - `gain_percent` field stores peak percentage (frozen at peak)
  - `live_percentage` stores current percentage

- **Existing Stock Updates:**
  - Checks if `result['peak_price'] > tracked_stocks[symbol]['checkpoint_b_price']`
  - If true: Updates Checkpoint-B with new peak time and price
  - Logs: `↑ PEAK UPDATE: {symbol} new peak at {peak_percent}%`

- **Tracked-Only Stocks:**
  - Also checks for peak updates
  - Uses cached peaks (passes `is_first_scan=False`)

### 4. Updated: `run_continuous_scan()` and `run_single_scan()`
**Changes:**
- Both methods now pass `scan_iteration` parameter to `process_stocks()`
- Continuous scan increments iteration counter: `scan_iteration += 1`
- Single scan uses `scan_iteration=1`

### 5. Global State
**New Field:**
```python
app_state["peak_prices"] = {}  # Cache: {symbol: {peak_price, peak_time}}
```

**Purpose:** Store peak prices between scans to enable hybrid tracking approach.

## GTT File Format

### Unchanged Columns
- Symbol
- CheckPoint-A TIME (time0)
- CheckPoint-A PRICE (price at time0)
- live_percentage (current price vs time0)
- Borrow_Status
- Float, Net_Income_TTM, Institutional_Pct, etc.

### Changed Columns
- **CheckPoint-B TIME:** Now shows time of peak (not threshold crossing)
- **CheckPoint-B PRICE:** Now shows peak price (not price at crossing)
- **Gain_Percent:** Now shows peak percentage (frozen at peak value)

## Example Scenario

### Time Window: 09:30 → 10:15

| Time  | Price | Current % | Peak % | Status |
|-------|-------|-----------|--------|--------|
| 09:30 | $100  | 0%        | 0%     | time0 (Checkpoint-A) |
| 09:45 | $115  | +15%      | +15%   | ✓ Crosses 10% threshold → **TRACKED** |
| 10:00 | $120  | +20%      | +20%   | ↑ New peak → Checkpoint-B updated |
| 10:15 | $110  | +10%      | +20%   | Price dropped, but still tracked |

**GTT Output:**
- Checkpoint-A: 09:30, $100
- **Checkpoint-B: 10:00, $120** (peak moment)
- **Gain_Percent: 20.00%** (peak percentage)
- **live_percentage: 10.00%** (current percentage)

### Old Behavior (for comparison)
With old logic, Checkpoint-B would be 09:45, $115 (threshold crossing moment) and would never update.

## Benefits

1. **More Accurate Selection:** Captures stocks that peaked above threshold even if they dropped before current scan
2. **Peak Tracking:** Checkpoint-B always shows the highest point reached
3. **Hybrid Efficiency:** First scan uses historical data, subsequent scans use cached peaks
4. **Live Monitoring:** `live_percentage` still shows real-time status
5. **Forever Tracking:** Once threshold crossed at peak, stock stays tracked (unchanged behavior)

## Testing Recommendations

1. **Test with early peakers:** Stocks that peak in first 30 minutes then drop
2. **Test with continuous risers:** Stocks that keep rising throughout session
3. **Test time window edge cases:**
   - Short windows (< 30 min)
   - Long windows (> 2 hours)
4. **Test peak updates:** Verify Checkpoint-B updates when new peaks occur
5. **Test resume from GTT:** Ensure existing tracked stocks load correctly

## Backward Compatibility

The refactored script can load GTT files from the old format (without peak data). When resuming:
- Old stocks will have their peak recalculated on first scan
- Checkpoint-B may be updated to reflect actual peak
- All new tracking will use peak-based logic

## Performance Considerations

- **First scan:** Slower due to historical data fetching for peak calculation
- **Subsequent scans:** Faster - uses cached peaks and only compares with current price
- **API calls:** One additional historical data request per new stock on first scan
- **Time window size:** Larger windows may require coarser bar sizes (5-min instead of 1-min)
