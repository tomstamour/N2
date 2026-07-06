
## Overview

This directory is a **Quantitative Analysis Workspace** for optimizing Gap-Up Short (GUS) trading strategy parameters using historical trade data.

**Purpose**: Analyze historical Gap-Up Short Events (GUSE) to determine optimal:
- **Entry levels**: Short Entry Limit Order percentage above open price
- **Take-profit levels**: Cover/Buy Limit percentage below open price
- **Stop-loss levels**: Stop Loss Market Order percentage above open price

## Dataset

### gus_stats_ibkr.txt

**Format**: CSV (comma-delimited)
**Records**: 304 historical Gap-Up Short Events
**Total Columns**: 403

**Column Structure**:

1. **Metadata Columns (13 total)**:
   - `Date`: Trading date (YYYY-MM-DD)
   - `Ticker`: Stock symbol
   - `Gap%`: Gap percentage from previous day's close to 09:30 open
   - `Inst%`: Institutional ownership percentage
   - `Float`: Float in millions of shares
   - `MCap`: Market capitalization
   - `Vol J-1/Avg10%`: Volume ratio (previous day vs 10-day average)
   - `Vol Open`: Volume at market open
   - `High%`: Highest percentage gain during the day (relative to 09:30 open)
   - `Close vs Open%`: Closing price percentage change vs 09:30 open
   - `Time High`: Time when high was reached (HH:MM format)
   - `Low%`: Lowest percentage change during the day (relative to 09:30 open)
   - `Time Low`: Time when low was reached (HH:MM format)

2. **Intraday OHLCV Data (390 columns)**:
   - 5-minute bars from 09:30 to 15:55 (78 time periods)
   - Each period has 5 values: Open, High, Low, Close, Volume
   - Column naming pattern: `HH:MM_O`, `HH:MM_H`, `HH:MM_L`, `HH:MM_C`, `HH:MM_V`
   - Example: `09:30_O`, `09:30_H`, `09:30_L`, `09:30_C`, `09:30_V`

## Gap-Up Short Trading Strategy

### Current Strategy Parameters

**Entry Strategy**:
- Wait for 5-10% price push after market opens at 09:30
- Set Short Limit Order at 5-10% above the 09:30 open price

**Take-Profit Strategy**:
- Place Cover/Buy Limit Order at 15% below the 09:30 open price
- Exit when price drops to target level

**Stop-Loss Protection**:
- Place Stop Loss Market Order at 30-40% above the 09:30 open price
- Protect against runaway losses

### Strategy Rationale

The GUS strategy anticipates that stocks showing significant pre-market gaps (~ +50% gain during pre-market) will reverse intraday, creating shorting opportunities. The strategy assumes:
- Gap-up momentum often exhausts shortly after market open
- Price typically retraces toward previous support levels
- Early morning volatility creates optimal entry points

## Analysis Objectives

Use quantitative methods to optimize the three key parameters:

1. **Entry Timing and Level**:
   - Analyze `High%` and `Time High` to determine optimal entry percentage
   - Study patterns in early morning price pushes (09:30 - 10:30)
   - Calculate success rate at different entry levels (5%, 7.5%, 10%, 12.5%, etc.)

2. **Take-Profit Optimization**:
   - Analyze `Low%` and `Time Low` to determine realistic profit targets
   - Study intraday retracement patterns
   - Calculate optimal exit percentages balancing win rate and profit size

3. **Stop-Loss Optimization**:
   - Analyze extreme `High%` values to set protective stops
   - Balance between avoiding false stops and protecting capital
   - Study distribution of failed trades to minimize catastrophic losses


## Best Practices

1. **Data Quality**: Check for missing values and outliers before analysis
2. **Sample Size**: Consider statistical significance (304 records total)
3. **Time Context**: Account for market conditions, volatility regimes
4. **Overfitting**: Avoid optimizing too precisely on historical data
5. **Risk Management**: Balance win rate, profit targets, and stop levels
6. **Validation**: Consider out-of-sample testing if splitting dataset

## Related Files

- **README.txt**: High-level description of analysis objectives
- **gus_stats_ibkr.txt**: The dataset (do not modify directly)

When working with GEMINI in this directory:
- Think as a **Quantitative Trader** analyzing trading strategies
- Focus on **data-driven optimization** of entry, exit, and stop-loss parameters
- Use **statistical methods** to validate findings
- Consider **risk-reward ratios** and **position sizing** implications
- Present findings with **visualizations** and **clear metrics**
