# Trade Decider (TD) Feature - README

## Overview

The **Trade Decider (TD)** feature is an automated decision-making system integrated into the Gainers Scanner Script (GSS). It evaluates stocks against multiple criteria in real-time and flags stocks that meet ALL trading requirements.

## Purpose

The TD feature helps traders quickly identify stocks that meet specific trading criteria for the "Gap Up Short" strategy by automatically evaluating:
- Price movement (gap percentage)
- Institutional ownership
- Float size
- Stock price
- Borrow fee costs

## How It Works

### Core Logic

The TD function evaluates each stock against **7 criteria** (6 configurable + 1 automatic):

**Automatic Hard Requirements** (always evaluated):
- **Criterion 0**: OTC Filter - if `--discard-otc YES` is set, reject OTC stocks
- **Criterion 0.5**: **Shortable = YES** (margin eligibility via WhatIf) - stock must be confirmed shortable by IBKR

**Configurable Criteria** (based on command-line arguments):
1. **GapL(%) > Threshold** - Live gap percentage must be LARGER than specified value
2. **Instit(%) < Threshold** - Institutional percentage must be SMALLER than specified value
3. **Float(M$) < Threshold** - Float in millions must be SMALLER than specified value
4. **CP-B($) < Threshold** - Checkpoint-B price must be SMALLER than specified value
5. **BF_BEST < Threshold** - Best borrow fee must be SMALLER than specified value
6. **xVol < Threshold** - Volume ratio must be SMALLER than specified value

**Decision Rule**: A stock receives "YES" in the Trade column **ONLY** if:
- The stock passes Criterion 0.5 (Shortable = YES) - **HARD REQUIREMENT**
- ALL enabled criteria (1-6) are met

If any criterion fails or data is missing, the stock receives "NO" (conservative approach).

**Important**: Criterion 0.5 (Shortable) is always checked regardless of other settings. This prevents trading on stocks that appear shortable but would be rejected by IBKR.

### Re-Evaluation

The TD function re-evaluates ALL tracked stocks at every scanner refresh cycle (default: 3 seconds). This ensures the Trade column stays current as:
- Live percentages change
- Borrow fees update
- Fundamental data is enriched

## Command-Line Usage

### Required Arguments (All 5 Must Be Specified)

```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-21/09:30" \
  --gap-percentage 10 \
  --client-id 300 \
  --gap_larger_then 15 \
  --institution_smaller_then 40 \
  --float_smaller_then 50 \
  --price_smaller_then 10 \
  --borrow_fee_smaller_then 5
```

### Argument Descriptions

| Argument | Type | Description | Example |
|----------|------|-------------|---------|
| `--gap_larger_then` | float | Minimum live gap % required | `15` (stock must have >15% gain) |
| `--institution_smaller_then` | float | Maximum institutional ownership % | `40` (stock must have <40% institutional) |
| `--float_smaller_then` | float | Maximum float in millions | `50` (stock must have <50M float) |
| `--price_smaller_then` | float | Maximum stock price at CP-B | `10` (stock must be <$10) |
| `--borrow_fee_smaller_then` | float | Maximum borrow fee % | `5` (stock must have <5% borrow fee) |

### Validation Rules

1. **All-or-None**: You must specify all 5 arguments or none. Partial specification will cause an error.
2. **Conservative Approach**: Missing or N/A data automatically results in "NO".
3. **Boundary Behavior**:
   - "LARGER than" means strictly greater (> not >=)
   - "SMALLER than" means strictly less (< not >=)

## Output

### GTT File Column

A new "Trade" column is added to the GusTradeTable output:

```
Symbol  CP-A(t)  ...  BF_BEST  Trade  LastUpdate
AAPL    ...      ...  3.50     YES    2025-11-21 10:30:15
TSLA    ...      ...  8.25     NO     2025-11-21 10:30:15
```

### Visual Highlighting

Stocks with "YES" trade decision receive visual highlighting:

**Log Messages:**
```
✓ NEW: AAPL crossed threshold - PEAK: 18.50% ($8.25 at 2025-11-21/10:15), LIVE: 16.20% 🟢 TRADE: YES
```

**Scan Summary:**
```
Scan complete: 3 new, 5 updated (scanner), 2 updated (tracked-only), Total tracked: 10
🟢 TRADE DECISION: 2 stocks meet all criteria (YES)
```

## Examples

### Example 1: Basic Usage

Find stocks with >20% gap, <30% institutional, <40M float, <$8 price, <4% borrow fee:

```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-21/09:30" \
  --gap-percentage 10 \
  --client-id 300 \
  --output-dir ./runs/21-Nov-2025/ \
  --gap_larger_then 20 \
  --institution_smaller_then 30 \
  --float_smaller_then 40 \
  --price_smaller_then 8 \
  --borrow_fee_smaller_then 4
```

### Example 2: More Permissive Criteria

```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-21/09:30" \
  --gap-percentage 5 \
  --client-id 300 \
  --gap_larger_then 10 \
  --institution_smaller_then 50 \
  --float_smaller_then 100 \
  --price_smaller_then 15 \
  --borrow_fee_smaller_then 10
```

### Example 3: Without Trade Decider

To run the scanner without Trade Decider, simply omit all 5 TD arguments:

```bash
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-21/09:30" \
  --gap-percentage 10 \
  --client-id 300
```

All stocks will have "NO" in the Trade column.

## Decision Flow Diagram

```
Stock Detected
    ↓
Check Criterion 1: GapL(%) > gap_larger_then?
    ├─ NO → Trade = "NO"
    └─ YES → Continue
        ↓
Check Criterion 2: Instit(%) < institution_smaller_then?
    ├─ NO → Trade = "NO"
    └─ YES → Continue
        ↓
Check Criterion 3: Float(M$) < float_smaller_then?
    ├─ NO → Trade = "NO"
    └─ YES → Continue
        ↓
Check Criterion 4: CP-B($) < price_smaller_then?
    ├─ NO → Trade = "NO"
    └─ YES → Continue
        ↓
Check Criterion 5: BF_BEST < borrow_fee_smaller_then?
    ├─ NO → Trade = "NO"
    └─ YES → Trade = "YES" ✓
```

## Data Dependencies

The TD feature relies on the following data being available:

| Criterion | Data Source | When Available |
|-----------|-------------|----------------|
| GapL(%) | Real-time price | Always (live or historical) |
| Instit(%) | yfinance API | After fundamental enrichment |
| Float(M$) | yfinance API | After fundamental enrichment |
| CP-B($) | Real-time price | Always (checkpoint B price) |
| BF_BEST | Multi-source borrow fee | After borrow fee enrichment |

**Note**: BF_BEST is automatically enriched by GSS after each scan using the borrow fee orchestrator (IBKR FTP + iBorrowDesk).

## Edge Cases & Behavior

### Missing Data

If any required data is missing or N/A:
- **Result**: "NO"
- **Rationale**: Conservative approach prevents false positives

Example:
```python
stock = {
    'live_percentage': 25,      # ✓ Meets criterion
    'institutional_pct': None,  # ✗ Missing data
    'float_shares': 30_000_000, # ✓ Meets criterion
    'checkpoint_b_price': 7,    # ✓ Meets criterion
    'bf_best': 3                # ✓ Meets criterion
}
# Result: "NO" (because institutional_pct is missing)
```

### Boundary Values

Thresholds are **exclusive** boundaries:

```python
# gap_larger_then = 15
stock['live_percentage'] = 15.00  # NO (equal, not larger)
stock['live_percentage'] = 15.01  # YES (larger)

# institution_smaller_then = 40
stock['institutional_pct'] = 40.00  # NO (equal, not smaller)
stock['institutional_pct'] = 39.99  # YES (smaller)
```

### Resume from GTT

When resuming from an existing GTT file:
- Old GTT files (without Trade column): All stocks default to "NO"
- New GTT files (with Trade column): Trade values are loaded and re-evaluated on next refresh

## Integration with GSS Pipeline

The TD feature integrates seamlessly with the GSS enrichment pipeline:

```
1. Scanner detects stock crossing threshold
    ↓
2. Fundamental data fetched (yfinance)
    ↓
3. Borrow fee enrichment (FTP + iBorrowDesk)
    ↓
4. Trade Decision evaluation ← TD RUNS HERE
    ↓
5. Write to GTT file (with Trade column)
```

TD re-runs at every refresh, ensuring decisions stay current.

## Troubleshooting

### Error: "Trade Decision feature requires ALL 5 arguments"

**Cause**: You specified some but not all TD arguments.

**Solution**: Either provide all 5 arguments or remove all TD arguments.

### All stocks show "NO" even though criteria seem met

**Possible Causes**:
1. **Missing data**: Check if BF_BEST, Instit(%), or Float(M$) columns show "N/A"
   - **Solution**: Wait for borrow fee enrichment to complete
2. **Boundary values**: Remember thresholds are exclusive
   - **Solution**: Adjust thresholds slightly (e.g., 15.0 → 14.9)

### Trade column missing from GTT

**Cause**: Running old version of GSS without TD feature.

**Solution**: Update to latest GSS version with TD support.

## Testing

A unit test suite is available:

```bash
python3 test_trade_decider_simple.py
```

This runs 12 test cases covering:
- All criteria met
- Individual criteria failures
- Boundary conditions
- Missing data handling
- Feature disabled scenario

## Technical Implementation

### Function Location

`evaluate_trade_decision(stock_data, args)`

**File**: `scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py` (line ~783)

### Integration Points

1. **New stock creation** (line ~1952)
2. **Existing stock update (from scanner)** (line ~1990)
3. **Tracked stock update (not in scanner)** (line ~2066)

### GTT Format

Column position: **20th column** (after BF_BEST, before LastUpdate)

```
Symbol | CP-A(t) | CP-A($) | ... | BF_BEST | Trade | LastUpdate
   0   |    1    |    2    | ... |   18    |  19   |     20
```

## Best Practices

1. **Start Permissive**: Use wider thresholds initially, then tighten based on results
2. **Monitor Enrichment**: Ensure borrow fee and fundamental data enrichment complete before relying on TD
3. **Combine with Manual Review**: TD is a filter, not a replacement for analysis
4. **Test Thresholds**: Run historical scans to validate your criteria before live trading
5. **Log Monitoring**: Watch for "🟢 TRADE: YES" markers in logs for quick alerts

## Future Enhancements

Potential additions (not yet implemented):
- Customizable logic operators (AND vs OR)
- Range criteria (e.g., gap between X% and Y%)
- Time-based criteria (e.g., "within first 30 minutes")
- Email/SMS alerts for "YES" stocks

## Support

For issues or questions:
- See main GSS documentation: `GAINERS_SCANNER_README.md`
- Review test cases: `test_trade_decider_simple.py`
- Check logs for detailed evaluation traces
