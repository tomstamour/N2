# Previous Day Volume Calculator (PDV) - Documentation

## Overview

The **Previous Day Volume Calculator (PDV)** is a volume analysis feature integrated into the Gainers Scanner Script. It calculates a volume ratio (xVol) that compares the trading volume on the --date-A (time0) day to the average volume of the 10 preceding trading days.

**Purpose**: Help identify stocks with unusual volume activity, which may indicate increased interest, momentum, or volatility. High volume relative to the average can signal trading opportunities or potential risks for short positions.

**Key Features**:
- Calculates xVol ratio: (date-A volume) / (10-day average volume)
- Uses yfinance API for reliable daily volume data
- 24-hour cache to minimize API calls
- Integrated as 6th trade decision criterion
- Automatically updates with every scanner refresh

---

## Calculation Formula

### Step-by-Step Calculation

1. **Extract date from --time0 argument**
   - Example: `--time0 "2025-11-27/09:30"` → date-A = November 27, 2025

2. **Fetch 11 days of volume data**
   - Days -11 to -1 relative to date-A (the 10 preceding days)
   - Day 0 (date-A itself)
   - Uses yfinance API: `ticker.history(start, end, interval='1d')`

3. **Calculate Average 10 Days Volume**
   - Average = SUM(volumes from days -11 to -1) / 10
   - **Note**: date-A day volume is excluded from the average

4. **Extract date-A volume**
   - The actual trading volume on the --date-A day

5. **Calculate xVol ratio**
   - xVol = (date-A volume) / (Average 10 Days Volume)
   - Rounded to 2 decimal places

6. **Interpret the result**
   - xVol = 1.00: date-A volume equals average (normal)
   - xVol = 2.50: date-A volume is 2.5x average (250% of normal)
   - xVol = 0.50: date-A volume is half the average (50% of normal)

### Example Calculation

```
Symbol: AAPL
Date-A: November 27, 2025

Volume data (last 11 days):
  Nov 14: 45,000,000
  Nov 15: 50,000,000
  Nov 18: 48,000,000
  Nov 19: 52,000,000
  Nov 20: 47,000,000
  Nov 21: 49,000,000
  Nov 22: 51,000,000
  Nov 25: 46,000,000
  Nov 26: 50,000,000
  Nov 27: 120,000,000 ← date-A

Average 10 Days Volume = (45M + 50M + 48M + 52M + 47M + 49M + 51M + 46M + 50M) / 10
                       = 488,000,000 / 10
                       = 48,800,000

xVol = 120,000,000 / 48,800,000 = 2.46

Interpretation: AAPL's volume on Nov 27 is 2.46x its 10-day average (unusually high)
```

---

## Command-Line Arguments

### --xVolume-smaller-then

**Type**: `float`
**Required**: Yes, when using trade decision feature (must be specified with all 6 TD arguments)
**Purpose**: Sets the maximum acceptable xVol ratio for trade decision
**Logic**: Stock xVol must be SMALLER than this threshold for Trade="YES"

**Syntax**:
```bash
--xVolume-smaller-then <value>
```

**Examples**:
```bash
# Conservative filter: Only stocks with xVol < 1.5 (less than 150% of average volume)
--xVolume-smaller-then 1.5

# Moderate filter: Only stocks with xVol < 3.0 (less than 300% of average volume)
--xVolume-smaller-then 3.0

# Aggressive filter: Only stocks with xVol < 5.0 (less than 500% of average volume)
--xVolume-smaller-then 5.0
```

**Trade Decision Integration**:
- PDV is the **6th criterion** in the trade decision logic
- ALL 6 criteria must be met for Trade="YES":
  1. GapL(%) > --gap_larger_then
  2. Instit(%) < --institution_smaller_then
  3. Float(M$) < --float_smaller_then
  4. CP-B($) < --price_smaller_then
  5. BF_BEST < --borrow_fee_smaller_then
  6. **xVol < --xVolume-smaller-then** ← NEW

---

## Usage Examples

### Example 1: Conservative Volume Filter

**Goal**: Find stocks with moderate volume increase (1.5x - 3x average)

```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-27/09:30" \
  --gap-percentage 10 \
  --client-id 300 \
  --gap_larger_then 15 \
  --institution_smaller_then 40 \
  --float_smaller_then 100 \
  --price_smaller_then 50 \
  --borrow_fee_smaller_then 5.0 \
  --xVolume-smaller-then 1.5
```

**Rationale**: Conservative traders may prefer stocks with modest volume spikes, avoiding "pump and dump" schemes or extreme volatility.

---

### Example 2: Moderate Volume Filter

**Goal**: Find stocks with noticeable volume increase (up to 3x average)

```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-27/09:30" \
  --gap-percentage 5 \
  --client-id 300 \
  --gap_larger_then 10 \
  --institution_smaller_then 50 \
  --float_smaller_then 200 \
  --price_smaller_then 30 \
  --borrow_fee_smaller_then 10.0 \
  --xVolume-smaller-then 3.0
```

**Rationale**: Balanced approach - allows for increased volume activity while filtering out extreme outliers.

---

### Example 3: Aggressive Volume Filter

**Goal**: Allow stocks with high volume spikes (up to 5x average)

```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-27/09:30" \
  --gap-percentage 20 \
  --client-id 300 \
  --gap_larger_then 25 \
  --institution_smaller_then 30 \
  --float_smaller_then 50 \
  --price_smaller_then 20 \
  --borrow_fee_smaller_then 3.0 \
  --xVolume-smaller-then 5.0
```

**Rationale**: Aggressive traders targeting high-momentum stocks with significant volume surges.

---

## Column Format in GusTradeTable

### Column Position

The **xVol** column appears in the GusTradeTable file:
- **Position**: 20 (after BF_BEST, before Trade)
- **Format**: Float with 2 decimal places (e.g., "2.35", "0.87", "1.00")
- **N/A handling**: Shows "N/A" if calculation failed or not in date-A mode

### Column Header

```
Symbol	CP-A(t)	CP-A($)	CP-B(t)	CP-B($)	Gap(%)	GapL(%)	PriceL($)	Sect	Indus	ShortStatus	ShortDiff	ShortableShares	Float(M$)	NetIncome(M$)	Instit(%)	BorrowFee	BF1	BF2	BF_BEST	xVol	Trade	KellyPosition	TradeCash($)	LastUpdate
```

### Sample Data Row

```
AAPL	2025-11-27/09:30	150.00	2025-11-27/10:15	157.50	5.00	4.80	157.20	Technology	Consumer Electronics	HIGH_AVAIL	2.80	1000000	150.25	25.30	75.20	0.50	0.45	0.48	0.45	2.46	YES	100	15000.00	2025-11-27 10:30:00
```

**Interpretation**:
- xVol = 2.46 → AAPL's volume on Nov 27 is 2.46x its 10-day average
- Trade = YES → All 6 criteria are met (including xVol < threshold)

---

## Edge Cases and Error Handling

### 1. Newly Listed Stocks (<11 Days History)

**Scenario**: Stock IPO'd less than 11 days ago

**Behavior**:
- PDV function returns `{'xvol': None, 'error': 'Insufficient volume data'}`
- xVol column shows "N/A"
- Trade decision = "NO" (conservative approach)

**Log Message**:
```
WARNING - AAPL: Insufficient volume data (got 5 days, need 11)
```

---

### 2. Weekend or Holiday date-A

**Scenario**: --time0 falls on a non-trading day

**Behavior**:
- yfinance automatically excludes non-trading days
- Calculation may still succeed if enough prior trading days available
- If insufficient data, xVol = "N/A"

**Example**:
```
--time0 "2025-11-29/09:30"  # Saturday
```
yfinance will use the most recent trading day data.

---

### 3. Zero Average Volume

**Scenario**: All 10 prior days have zero volume (extremely rare)

**Behavior**:
- PDV function returns `{'xvol': None, 'error': 'Zero average volume'}`
- xVol column shows "N/A"
- Trade decision = "NO"

**Log Message**:
```
WARNING - XYZ: Zero average volume
```

---

### 4. Missing yfinance Data

**Scenario**: yfinance API error or symbol not found

**Behavior**:
- PDV function catches exception and returns `{'xvol': None, 'error': 'API error: ...'}`
- xVol column shows "N/A"
- Trade decision = "NO"

**Log Message**:
```
WARNING - INVALID: Error calculating PDV: No data found for symbol INVALID
```

---

### 5. time0 Mode (Not date-A Mode)

**Scenario**: Scanner running in time0 mode (real-time mode without --date-A)

**Behavior**:
- PDV calculation is skipped (only runs in date-A mode)
- xVol column shows "N/A" for all stocks
- Trade decision ignores xVol criterion

**Reason**: PDV requires a fixed date-A reference for meaningful comparison. In time0 mode, the "current day" is constantly changing.

---

## Performance Considerations

### API Rate Limiting

- **yfinance delay**: 0.1 seconds between requests (matches existing pattern)
- **Impact**: +0.1s per stock without caching
- **Mitigation**: 24-hour cache minimizes repeated API calls

### Caching Strategy

**Cache Structure**:
```python
app_state["pdv_cache"] = {
    'AAPL': {
        'data': {'xvol': 2.46, 'date_a_volume': 120000000, 'avg_volume': 48800000, 'error': None},
        'timestamp': 1732723456.789
    },
    'TSLA': { ... }
}
```

**Cache Behavior**:
- **TTL**: 24 hours (86400 seconds)
- **Rationale**: Daily volume data doesn't change intraday
- **Cache hit rate**: ~100% for same-day scans
- **Cache miss**: First calculation per stock per day

### Scanner Speed Impact

**Without caching** (first scan of the day):
- 50 stocks × 0.1s delay = +5 seconds total scan time

**With caching** (subsequent scans):
- Negligible impact (<0.01s per stock for cache lookup)

**Best Practice**: Run scanner multiple times during the day to leverage caching.

---

## Troubleshooting

### Issue 1: xVol shows "N/A" for all stocks

**Possible Causes**:
1. Scanner running in time0 mode (not date-A mode)
2. yfinance API is down or blocked
3. Internet connection issue

**Solution**:
1. Verify you're using `--time0 "YYYY-MM-DD/HH:MM"` (date-A mode)
2. Check yfinance availability: `pip3 show yfinance`
3. Test manually: `python3 -c "import yfinance as yf; print(yf.Ticker('AAPL').history(period='1mo'))"`

---

### Issue 2: Trade decision always "NO" despite meeting other criteria

**Possible Cause**: xVol threshold too restrictive or xVol data missing

**Solution**:
1. Check xVol values in GTT file - are they all "N/A" or above threshold?
2. Increase --xVolume-smaller-then value (e.g., from 2.0 to 5.0)
3. Check logs for PDV calculation errors

**Debug Command**:
```bash
grep "PDV" ./logs/scanner_*.log
```

---

### Issue 3: "Insufficient volume data" errors for most stocks

**Possible Cause**: date-A is too recent (stocks don't have 11 days history yet)

**Solution**:
- Ensure date-A is at least 11 trading days after the stock's listing date
- For new IPOs, PDV may not be available - this is expected behavior

---

### Issue 4: PDV calculation very slow

**Possible Cause**: Cache not working or cleared

**Solution**:
1. Check cache status in logs: `grep "Using cached PDV" ./logs/scanner_*.log`
2. Verify cache TTL hasn't been modified
3. Ensure `app_state["pdv_cache"]` is not being cleared unintentionally

---

## Technical Notes

### Data Source

- **API**: yfinance (Python library for Yahoo Finance data)
- **Endpoint**: Daily historical data
- **Reliability**: High (Yahoo Finance is widely used and stable)
- **Cost**: Free (no API key required)

### Assumptions

1. **Trading Days**: yfinance automatically handles market holidays and weekends
2. **Timezone**: All dates are in US/Eastern (scanner's default timezone)
3. **Volume Units**: Shares traded (not dollar volume)
4. **Data Freshness**: yfinance data typically updated end-of-day

### Comparison: PDV vs IBKR Volume Data

**Why yfinance instead of IBKR?**

| Aspect | yfinance | IBKR API |
|--------|----------|----------|
| Historical daily volume | ✅ Easy | ⚠️ Requires aggregation of intraday bars |
| API complexity | ✅ Simple | ⚠️ Complex (historical data requests) |
| Caching | ✅ Built-in | ❌ Manual implementation |
| Rate limits | ✅ Lenient | ⚠️ Strict pacing rules |
| Cost | ✅ Free | ✅ Free (with IB account) |

**Decision**: yfinance is simpler, faster, and more reliable for daily volume calculations.

---

## Integration with Other Features

### Trade Decision Feature

PDV is tightly integrated with the trade decision logic:
- **Criterion 6**: xVol < --xVolume-smaller-then
- **Validation**: All 6 arguments must be specified together
- **Evaluation**: Occurs AFTER borrow fee and trend enrichment

### Kelly Calculator

If --cash-to-play is specified:
- Kelly positions calculated for Trade="YES" stocks only
- PDV criterion can filter out high-volume spikes that may be risky

### Trend Verifier

PDV and Trend Verifier work together:
- **Trend columns** (1yrT, 6moT, 3moT): Long-term price momentum
- **xVol column**: Short-term volume activity
- Combined analysis: Find stocks with strong trends AND volume confirmation

---

## Best Practices

### 1. Volume Threshold Selection

**Conservative Strategy** (xVol < 1.5):
- Target: Stocks with normal to slightly elevated volume
- Risk: Lower (avoid pump and dump)
- Use case: Shorting overbought stocks with sustainable volume

**Moderate Strategy** (xVol < 3.0):
- Target: Stocks with noticeable volume increase
- Risk: Medium (balanced approach)
- Use case: General gap up short candidates

**Aggressive Strategy** (xVol < 5.0):
- Target: High-momentum stocks with volume surges
- Risk: Higher (extreme volatility)
- Use case: Experienced traders targeting "meme stocks" or news-driven events

### 2. Combining with Other Criteria

**Example**: Conservative gap up short setup
```bash
--gap_larger_then 20        # Significant gap up
--institution_smaller_then 30  # Low institutional ownership
--float_smaller_then 50     # Small float
--price_smaller_then 15     # Low price (high volatility)
--borrow_fee_smaller_then 3 # Easy to borrow
--xVolume-smaller-then 2.0  # Moderate volume (avoid pump and dump)
```

**Interpretation**: Find small-cap stocks with big gaps but controlled volume (less likely to be manipulated).

### 3. Monitoring Volume Trends

**Over Multiple Days**:
- Compare xVol values across consecutive scans
- Increasing xVol trend → Growing interest (momentum building)
- Decreasing xVol trend → Waning interest (momentum fading)

**Use Case**: Re-scan same date-A over several days to track volume evolution.

---

## Appendix: Column Definitions

### Complete GusTradeTable Column List (v3.9+)

| Column | Description | Source |
|--------|-------------|--------|
| Symbol | Stock ticker | Scanner |
| CP-A(t) | Checkpoint-A time (time0) | Scanner |
| CP-A($) | Checkpoint-A price | Scanner |
| CP-B(t) | Checkpoint-B time (threshold crossing) | Scanner |
| CP-B($) | Checkpoint-B price | Scanner |
| Gap(%) | Gap percentage at threshold crossing | Scanner |
| GapL(%) | Live gap percentage from time0 | Scanner |
| PriceL($) | Live price for GapL(%) calculation | Scanner |
| Sect | Sector | yfinance |
| Indus | Industry | yfinance |
| ShortStatus | Shortable status (IBKR-style) | IBKR API |
| ShortDiff | Shortable difficulty (tick 46) | IBKR API |
| ShortableShares | Shortable shares count (tick 89) | IBKR API |
| Float(M$) | Float in millions | yfinance |
| NetIncome(M$) | Net income TTM in millions | yfinance |
| Instit(%) | Institutional ownership % | yfinance |
| BorrowFee | Borrow fee rate from IBKR | IBKR API |
| BF1 | Borrow fee from FTP | Enrichment |
| BF2 | Borrow fee from iBorrowDesk | Enrichment |
| BF_BEST | Best borrow fee | Enrichment |
| **xVol** | **Volume ratio (PDV calculator)** | **yfinance** |
| Trade | Trade decision (YES/NO) | Trade Decider |
| KellyPosition | Kelly position size in shares | Kelly Calculator |
| TradeCash($) | Allocated cash for position | Kelly Calculator |
| LastUpdate | Last update timestamp | Scanner |

---

## Version History

**v3.9** (November 2025)
- Initial implementation of PDV feature
- Added xVol column to GusTradeTable
- Added --xVolume-smaller-then argument
- Updated trade decision to 6 criteria
- 24-hour yfinance volume data caching

---

## Support and Feedback

For questions, issues, or feature requests related to PDV:
1. Check scanner logs: `./logs/scanner_*.log`
2. Review this documentation
3. Consult CLAUDE.md for general scanner usage
4. Test with known symbols (e.g., AAPL, TSLA) to verify functionality

**Common Debug Commands**:
```bash
# Check PDV log entries
grep "PDV" ./logs/scanner_*.log

# Check cache status
grep "cached PDV" ./logs/scanner_*.log

# Check xVol values in GTT file
cut -f1,21 runs/*/GusTradeTable_*.txt | head -20
```
