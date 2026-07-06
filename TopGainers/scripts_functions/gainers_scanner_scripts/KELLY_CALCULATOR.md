# Kelly Criterion Position Calculator

## Overview

The Kelly Criterion Position Calculator is an optional feature of the Gainers Scanner Script (GSS) that automatically calculates position sizes for stocks meeting trade criteria.

## Purpose

- **Position Sizing**: Calculates number of shares to buy/short
- **Cash Allocation**: Distributes available capital across qualifying stocks
- **Risk Management**: Enforces concentration limits per stock
- **Dynamic Updates**: Recalculates positions on every scan refresh

## Command-Line Arguments

### --cash-to-play (Optional)

Total dollar amount available for position calculations.

**Type:** float
**Default:** None (feature disabled)
**Example:** `--cash-to-play 10000`

If not provided, KellyPosition and TradeCash($) columns will show "N/A".

### --max-tradeCash-proportion-per-stock (Optional)

Maximum proportion of total cash that can be allocated to a single stock.

**Type:** float (0.0 to 1.0)
**Default:** 0.33 (33% concentration limit)
**Example:** `--max-tradeCash-proportion-per-stock 0.25`

Prevents over-concentration in any single position.

## Calculation Logic

### Step 1: Identify Qualifying Stocks

Only stocks with `Trade="YES"` are eligible for position calculations.

### Step 2: Calculate Base Allocation

```
Base Cash Per Stock = cash_to_play ÷ number_of_YES_stocks
```

### Step 3: Apply Concentration Limit

```
Max Cash Per Stock = cash_to_play × max_tradeCash_proportion_per_stock
Cash Available = MIN(Base Cash Per Stock, Max Cash Per Stock)
```

### Step 4: Calculate Position Size

```
Kelly Position = FLOOR(Cash Available ÷ Stock Price)
Trade Cash($) = Cash Available
```

**Important:** TradeCash($) stores the allocated cash (after cap), NOT the position value (KellyPosition × Price).

## Examples

### Scenario A: Base < Cap (4 YES stocks)

```
--cash-to-play: 1000
--max-tradeCash-proportion-per-stock: 0.33
Stocks with Trade="YES": 4

Calculation:
  Base = 1000 ÷ 4 = 250
  Cap = 1000 × 0.33 = 330
  Cash Available = MIN(250, 330) = 250 per stock

Results:
  Stock A (Price $150): KellyPosition=1, TradeCash=$250.00
  Stock B (Price $100): KellyPosition=2, TradeCash=$250.00
  Stock C (Price $200): KellyPosition=1, TradeCash=$250.00
  Stock D (Price $250): KellyPosition=1, TradeCash=$250.00
```

### Scenario B: Base > Cap (1 YES stock)

```
--cash-to-play: 1000
--max-tradeCash-proportion-per-stock: 0.33
Stocks with Trade="YES": 1

Calculation:
  Base = 1000 ÷ 1 = 1000
  Cap = 1000 × 0.33 = 330
  Cash Available = MIN(1000, 330) = 330 per stock

Results:
  Stock A (Price $150): KellyPosition=2, TradeCash=$330.00
```

### Scenario C: Base > Cap (2 YES stocks)

```
--cash-to-play: 1000
--max-tradeCash-proportion-per-stock: 0.33
Stocks with Trade="YES": 2

Calculation:
  Base = 1000 ÷ 2 = 500
  Cap = 1000 × 0.33 = 330
  Cash Available = MIN(500, 330) = 330 per stock

Results:
  Stock A (Price $100): KellyPosition=3, TradeCash=$330.00
  Stock B (Price $150): KellyPosition=2, TradeCash=$330.00
```

## GTT Columns

### KellyPosition

- **Type:** Integer (whole shares)
- **Position:** Column 21 (after Trade, before LastUpdate)
- **Values:**
  - Integer (0, 1, 2, ...): Calculated position size
  - "N/A": Trade="NO", feature disabled, or invalid data

### TradeCash($)

- **Type:** Float (2 decimal places)
- **Position:** Column 22 (after KellyPosition, before LastUpdate)
- **Values:**
  - Dollar amount (e.g., "250.00", "330.00"): Allocated cash after cap
  - "N/A": Trade="NO", feature disabled, or invalid data

**Note:** TradeCash($) shows the ALLOCATED cash, not the position value. Calculate position value as: KellyPosition × Stock Price.

## Edge Cases

### Trade="NO" Stocks

Stocks not meeting trade criteria show "N/A" for both Kelly columns.

### Feature Disabled

If `--cash-to-play` is not provided, both columns show "N/A" for all stocks.

### Zero YES Stocks

If no stocks meet trade criteria, all Kelly columns show "N/A".

### Invalid Price Data

Stocks with missing or invalid price (≤ 0) show "N/A" for Kelly columns.

### Valid Zero Position

If calculated position is 0 (price > cash available), displays "0" and "XXX.XX", not "N/A".

## Dynamic Recalculation

Positions are recalculated on **every refresh cycle**:

- Stock moves from Trade="NO" → "YES": Gets position allocated
- Stock moves from Trade="YES" → "NO": Position removed, cash redistributed
- New stock crosses threshold with Trade="YES": Cash redistributed across all YES stocks
- Prices update: Position sizes recalculate using new prices

This ensures optimal allocation as market conditions change.

## Integration with Trade Decision

Kelly Calculator requires the Trade Decision feature to function:

```bash
python3 Gainers_Scanner_Script.py \
  --time0 "2025-11-27/09:30" \
  --gap-percentage 10 \
  --client-id 300 \
  --gap_larger_then 15 \
  --institution_smaller_then 40 \
  --float_smaller_then 100 \
  --price_smaller_then 50 \
  --borrow_fee_smaller_then 5.0 \
  --cash-to-play 10000 \
  --max-tradeCash-proportion-per-stock 0.25
```

Without Trade Decision arguments, all stocks show Trade="N/A", so Kelly columns also show "N/A".

## Best Practices

### Concentration Limits

- **Conservative:** 0.20 (20% max per stock) - Good for volatile markets
- **Moderate:** 0.33 (33% max per stock) - Default, balanced approach
- **Aggressive:** 0.50 (50% max per stock) - Only if confident in selection

### Cash-to-Play Amount

- Use only risk capital you're willing to lose
- Account for margin requirements for short positions
- Leave buffer for adverse price movements
- Consider borrow fees in total cost calculation

### Position Monitoring

- Monitor TradeCash($) to see actual cash deployed
- Calculate total exposure: Sum of all TradeCash($) values
- Verify total ≤ cash_to_play (should never exceed)
- Watch for concentration in single stock (should not exceed max proportion)

## Price Source

Positions are calculated using `checkpoint_b_price` (peak price), which is continuously updated during the scan. This provides conservative position sizing based on the highest price observed since the stock crossed the threshold.

## Implementation Note

The Kelly Calculator uses a simplified "equal allocation with cap" approach rather than true Kelly Criterion (optimal fraction). True Kelly requires win probability and payoff ratio estimation, which are difficult to predict accurately. The implemented approach provides systematic position sizing with risk controls.
