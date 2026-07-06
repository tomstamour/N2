# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## Common Analysis Tasks

### Loading the Dataset

```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# Load data
df = pd.read_csv('gus_stats_ibkr.txt')

# Display basic info
print(f"Total GUSE records: {len(df)}")
print(f"Date range: {df['Date'].min()} to {df['Date'].max()}")
print(f"\nFirst few rows:")
print(df.head())
```

### Exploring Key Metrics

```python
# Summary statistics for key columns
print(df[['Gap%', 'High%', 'Low%', 'Close vs Open%']].describe())

# Distribution of gap percentages
plt.figure(figsize=(10, 6))
plt.hist(df['Gap%'], bins=30, edgecolor='black')
plt.xlabel('Gap %')
plt.ylabel('Frequency')
plt.title('Distribution of Gap Percentages')
plt.show()

# High vs Low analysis
plt.figure(figsize=(10, 6))
plt.scatter(df['High%'], df['Low%'], alpha=0.5)
plt.xlabel('High % (above open)')
plt.ylabel('Low % (below open)')
plt.title('Intraday High vs Low')
plt.axhline(y=0, color='r', linestyle='--')
plt.axvline(x=0, color='r', linestyle='--')
plt.grid(True, alpha=0.3)
plt.show()
```

### Analyzing Entry Levels

```python
# Analyze what percentage of stocks reached different entry levels
entry_levels = [5, 7.5, 10, 12.5, 15]
for level in entry_levels:
    reached = (df['High%'] >= level).sum()
    pct = (reached / len(df)) * 100
    print(f"Entry at +{level}%: {reached} stocks ({pct:.1f}%)")

# Timing analysis: when did highs occur?
df['Time High (minutes)'] = pd.to_datetime(df['Time High'], format='%H:%M').dt.hour * 60 + pd.to_datetime(df['Time High'], format='%H:%M').dt.minute
plt.figure(figsize=(10, 6))
plt.hist(df['Time High (minutes)'], bins=50, edgecolor='black')
plt.xlabel('Minutes from midnight')
plt.ylabel('Frequency')
plt.title('Distribution of When Daily High Occurred')
plt.show()
```

### Analyzing Take-Profit Levels

```python
# Analyze retracement depth
take_profit_levels = [-10, -15, -20, -25, -30]
for level in take_profit_levels:
    reached = (df['Low%'] <= level).sum()
    pct = (reached / len(df)) * 100
    print(f"Take-profit at {level}%: {reached} stocks ({pct:.1f}%)")

# Win rate if entered at +10% and exited at -15%
entry = 10
exit = -15
# Can enter if High% >= entry
can_enter = df['High%'] >= entry
# Win if Low% <= exit after entry
wins = can_enter & (df['Low%'] <= exit)
win_rate = (wins.sum() / can_enter.sum()) * 100 if can_enter.sum() > 0 else 0
print(f"\nWin rate (entry +{entry}%, exit {exit}%): {win_rate:.1f}%")
```

### Analyzing Stop-Loss Levels

```python
# Distribution of worst-case scenarios
plt.figure(figsize=(10, 6))
plt.hist(df['High%'], bins=50, edgecolor='black')
plt.xlabel('High % (maximum upside)')
plt.ylabel('Frequency')
plt.title('Distribution of Daily High (Worst Case for Shorts)')
plt.axvline(x=30, color='r', linestyle='--', label='Current Stop: +30%')
plt.axvline(x=40, color='orange', linestyle='--', label='Current Stop: +40%')
plt.legend()
plt.show()

# Calculate stop-out rates at different levels
stop_levels = [20, 25, 30, 35, 40, 45, 50]
for level in stop_levels:
    stopped = (df['High%'] >= level).sum()
    pct = (stopped / len(df)) * 100
    print(f"Stopped out at +{level}%: {stopped} stocks ({pct:.1f}%)")
```

### Strategy Performance Simulation

```python
# Simulate a complete strategy
entry_pct = 10  # Enter at +10% above open
exit_pct = -15  # Exit at -15% below open
stop_pct = 35   # Stop at +35% above open

def simulate_trade(row, entry, exit, stop):
    """Simulate a single trade"""
    # Check if entry was reached
    if row['High%'] < entry:
        return 'NO_ENTRY'

    # Check if stopped out first (before reaching profit target)
    # This requires analyzing intraday bars
    # Simplified: assume if High% >= stop, got stopped out
    if row['High%'] >= stop:
        return 'STOP_LOSS'

    # Check if profit target reached
    if row['Low%'] <= exit:
        return 'PROFIT'

    # Neither stop nor profit reached (held to close)
    return 'HELD'

# Apply simulation
df['Trade_Result'] = df.apply(lambda row: simulate_trade(row, entry_pct, exit_pct, stop_pct), axis=1)

# Calculate results
results = df['Trade_Result'].value_counts()
print(f"\nStrategy Results (Entry: +{entry_pct}%, Exit: {exit_pct}%, Stop: +{stop_pct}%):")
print(results)
print(f"\nTrade entry rate: {((len(df) - results.get('NO_ENTRY', 0)) / len(df)) * 100:.1f}%")
if results.get('PROFIT', 0) + results.get('STOP_LOSS', 0) > 0:
    win_rate = (results.get('PROFIT', 0) / (results.get('PROFIT', 0) + results.get('STOP_LOSS', 0))) * 100
    print(f"Win rate (when traded): {win_rate:.1f}%")
```

### Intraday Pattern Analysis

```python
# Analyze specific 5-minute bars
# Example: Analyze price movement in first 30 minutes
first_bars = ['09:30', '09:35', '09:40', '09:45', '09:50', '09:55']

for time in first_bars:
    high_col = f'{time}_H'
    low_col = f'{time}_L'
    if high_col in df.columns and low_col in df.columns:
        avg_high = ((df[high_col] / df['09:30_O'] - 1) * 100).mean()
        avg_low = ((df[low_col] / df['09:30_O'] - 1) * 100).mean()
        print(f"{time}: Avg High: +{avg_high:.2f}%, Avg Low: {avg_low:.2f}%")
```

## Python Dependencies

```bash
pip install pandas numpy matplotlib seaborn scipy
```

**Common libraries for quantitative analysis**:
- `pandas`: Data manipulation and analysis
- `numpy`: Numerical computing
- `matplotlib`: Plotting and visualization
- `seaborn`: Statistical data visualization
- `scipy`: Statistical analysis and optimization

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
- **../scripts_functions/historical_gap_finder/**: Tool for generating similar datasets

## Notes

When working with Claude Code in this directory:
- Think as a **Quantitative Trader** analyzing trading strategies
- Focus on **data-driven optimization** of entry, exit, and stop-loss parameters
- Use **statistical methods** to validate findings
- Consider **risk-reward ratios** and **position sizing** implications
- Present findings with **visualizations** and **clear metrics**
