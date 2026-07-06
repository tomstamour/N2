#!/usr/bin/env python3
"""
Gap-Up Short Entry Strategy Analysis

Quantitative analysis to determine optimal entry strategy:
- Time-based entry (enter at specific time's HIGH price)
- Percentage-based entry (enter when price reaches X% above open)

Author: Quantitative Analysis
Date: 2026-01-10
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from scipy import stats
import warnings
warnings.filterwarnings('ignore')

# Set style for visualizations
sns.set_style('whitegrid')
plt.rcParams['figure.figsize'] = (12, 8)
plt.rcParams['font.size'] = 10

# ==============================================================================
# PHASE 1: DATA LOADING & VALIDATION
# ==============================================================================

print("=" * 80)
print("GAP-UP SHORT ENTRY STRATEGY ANALYSIS")
print("=" * 80)
print("\nPhase 1: Loading and validating dataset...")

# Load dataset
data_file = '/home/tom/Documents/ibkr_scripts/GUS3/data/gus_stats_ibkr.txt'
df = pd.read_csv(data_file)

print(f"\nDataset loaded: {len(df)} records, {len(df.columns)} columns")
print(f"Date range: {df['Date'].min()} to {df['Date'].max()}")

# Validate critical columns
critical_cols = ['Date', 'Ticker', 'Gap%', 'High%', 'Low%', 'Time High', 'Time Low', '09:30_O']
missing_counts = df[critical_cols].isnull().sum()
print(f"\nMissing values in critical columns:")
for col, count in missing_counts.items():
    pct = (count / len(df)) * 100
    print(f"  {col}: {count} ({pct:.2f}%)")

# Data consistency checks
print(f"\nData consistency checks:")
high_low_consistent = (df['High%'] >= df['Low%']).all()
print(f"  High% >= Low% for all records: {high_low_consistent}")

positive_open = (df['09:30_O'] > 0).all()
print(f"  09:30 open price > 0 for all records: {positive_open}")

# Summary statistics
print(f"\nKey metric summary statistics:")
print(df[['Gap%', 'High%', 'Low%', 'Close vs Open%']].describe())

# ==============================================================================
# PHASE 2: FEATURE ENGINEERING
# ==============================================================================

print("\n" + "=" * 80)
print("Phase 2: Feature engineering...")

# Convert Time High and Time Low to minutes from 09:30
def time_to_minutes(time_str):
    """Convert HH:MM time string to minutes from 09:30"""
    try:
        h, m = map(int, time_str.split(':'))
        return (h - 9) * 60 + (m - 30)
    except:
        return np.nan

df['Time_High_Minutes'] = df['Time High'].apply(time_to_minutes)
df['Time_Low_Minutes'] = df['Time Low'].apply(time_to_minutes)

# Calculate intraday range
df['Intraday_Range'] = df['High%'] - df['Low%']

# Generate list of 5-minute bar time periods
time_periods = []
for hour in range(9, 16):
    for minute in range(0, 60, 5):
        if hour == 9 and minute < 30:
            continue
        if hour == 15 and minute > 55:
            continue
        time_periods.append(f"{hour:02d}:{minute:02d}")

print(f"Generated {len(time_periods)} time periods from 09:30 to 15:55")

# Normalize prices to percentage change from 09:30 open
print("Normalizing prices to percentage change from 09:30 open...")
for period in time_periods:
    for price_type in ['H', 'L', 'C']:
        col_name = f'{period}_{price_type}'
        pct_col_name = f'{period}_{price_type}_pct'
        if col_name in df.columns:
            df[pct_col_name] = ((df[col_name] / df['09:30_O']) - 1) * 100

print("Feature engineering complete.")

# ==============================================================================
# PHASE 3: REMOVED - TIME-BASED ANALYSIS (NOT EXECUTABLE IN REAL TRADING)
# ==============================================================================
# Time-based analysis removed because it suffers from hindsight bias:
# - Cannot predict the high of a bar before it happens
# - Requires setting predetermined percentage-based limit orders
# - "100% fill rate" is misleading - only achievable with future knowledge
#
# Only percentage-based strategies (Phase 4) are executable in real trading.

# ==============================================================================
# PHASE 4: PERCENTAGE-BASED ENTRY STRATEGY ANALYSIS
# ==============================================================================

print("\n" + "=" * 80)
print("Phase 4: Percentage-based entry strategy analysis...")

def simulate_percentage_based_entry(df, entry_pct):
    """
    Simulate percentage-based entry strategy with limit orders

    Parameters:
    - df: DataFrame with GUSE data
    - entry_pct: Entry percentage above 09:30 open (e.g., 10 for 10%)

    Returns:
    - DataFrame with entry analysis results
    """
    results = df.copy()

    # Entry price = 09:30 open * (1 + entry_pct/100)
    results['Entry_Price'] = df['09:30_O'] * (1 + entry_pct / 100)
    results['Entry_Level_Pct'] = entry_pct

    # Entry filled = True if High% >= entry_pct
    results['Entry_Filled'] = df['High%'] >= entry_pct

    # Calculate returns from entry to intraday low (for filled orders only)
    intraday_low_price = df['09:30_O'] * (1 + df['Low%'] / 100)
    results['Best_Return_Pct'] = np.where(
        results['Entry_Filled'],
        ((results['Entry_Price'] - intraday_low_price) / results['Entry_Price']) * 100,
        np.nan  # No return if order didn't fill
    )

    # Calculate worst adverse excursion (for filled orders)
    # Max adverse = (Intraday_High - Entry_Price) / Entry_Price
    intraday_high_price = df['09:30_O'] * (1 + df['High%'] / 100)
    results['Worst_Excursion_Pct'] = np.where(
        results['Entry_Filled'],
        ((intraday_high_price - results['Entry_Price']) / results['Entry_Price']) * 100,
        np.nan
    )

    # Return to close (for filled orders only)
    close_price = df['15:55_C']
    results['Return_to_Close_Pct'] = np.where(
        results['Entry_Filled'],
        ((results['Entry_Price'] - close_price) / results['Entry_Price']) * 100,
        np.nan
    )

    # Estimate entry time by finding first bar where High >= entry_pct
    results['Entry_Time_Estimated'] = None
    for idx, row in results.iterrows():
        if row['Entry_Filled']:
            for period in time_periods:
                period_high_col = f'{period}_H'
                if period_high_col in df.columns:
                    period_high_pct = ((row[period_high_col] / row['09:30_O']) - 1) * 100
                    if period_high_pct >= entry_pct:
                        results.loc[idx, 'Entry_Time_Estimated'] = period
                        break

    return results

# Percentage levels to test (11 levels - max 12% constraint)
pct_entry_levels = [2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]

pct_based_summary = []

for entry_pct in pct_entry_levels:
    print(f"\nAnalyzing percentage-based entry at +{entry_pct}%...")

    results = simulate_percentage_based_entry(df, entry_pct)

    # Calculate fill rate
    fill_rate = results['Entry_Filled'].sum() / len(results) * 100

    # Get filled orders only
    filled = results[results['Entry_Filled']]

    if len(filled) == 0:
        print(f"  Warning: No entries filled at {entry_pct}%")
        continue

    # Calculate performance metrics
    metrics = {
        'Entry_Pct': entry_pct,
        'Fill_Rate': fill_rate,
        'Conditional_Mean_Return': filled['Best_Return_Pct'].mean(),
        'Conditional_Median_Return': filled['Best_Return_Pct'].median(),
        'Conditional_Std_Return': filled['Best_Return_Pct'].std(),
        'Conditional_Sharpe': filled['Best_Return_Pct'].mean() / filled['Best_Return_Pct'].std() if filled['Best_Return_Pct'].std() > 0 else 0,
        'Conditional_Positive_Rate': (filled['Best_Return_Pct'] > 0).sum() / len(filled) * 100 if len(filled) > 0 else 0,
        'Conditional_Mean_WAE': filled['Worst_Excursion_Pct'].mean(),
        'Conditional_Max_WAE': filled['Worst_Excursion_Pct'].max(),
    }

    # Calculate expected metrics (accounting for fill rate)
    # For non-fills, assign 0 return
    all_returns = results['Best_Return_Pct'].fillna(0)
    metrics['Expected_Mean_Return'] = all_returns.mean()
    metrics['Expected_Std_Return'] = all_returns.std()
    metrics['Expected_Sharpe'] = all_returns.mean() / all_returns.std() if all_returns.std() > 0 else 0

    # Calculate downside deviation for Sortino ratio
    negative_returns = filled['Best_Return_Pct'][filled['Best_Return_Pct'] < 0]
    downside_dev = negative_returns.std() if len(negative_returns) > 0 else filled['Best_Return_Pct'].std()
    metrics['Conditional_Sortino'] = metrics['Conditional_Mean_Return'] / downside_dev if downside_dev > 0 else 0

    # Calculate win rates at different profit targets
    profit_targets = [5, 10, 15, 20, 25, 30]
    for target in profit_targets:
        conditional_win_rate = (filled['Best_Return_Pct'] >= target).sum() / len(filled) * 100 if len(filled) > 0 else 0
        expected_win_rate = (filled['Best_Return_Pct'] >= target).sum() / len(results) * 100
        metrics[f'Conditional_Win_Rate_{target}pct'] = conditional_win_rate
        metrics[f'Expected_Win_Rate_{target}pct'] = expected_win_rate

    # Calculate percentiles (conditional)
    for percentile in [10, 25, 50, 75, 90]:
        metrics[f'P{percentile}_Return'] = np.percentile(filled['Best_Return_Pct'], percentile) if len(filled) > 0 else 0

    # Entry timing analysis
    if len(filled) > 0:
        entry_time_counts = filled['Entry_Time_Estimated'].value_counts()
        if len(entry_time_counts) > 0:
            most_common_entry = entry_time_counts.index[0]
            metrics['Most_Common_Entry_Time'] = most_common_entry

            # Calculate average entry time in minutes
            filled['Entry_Minutes'] = filled['Entry_Time_Estimated'].apply(
                lambda x: time_to_minutes(x) if pd.notna(x) else np.nan
            )
            metrics['Avg_Entry_Minutes'] = filled['Entry_Minutes'].mean()
        else:
            metrics['Most_Common_Entry_Time'] = 'N/A'
            metrics['Avg_Entry_Minutes'] = np.nan

    pct_based_summary.append(metrics)

    print(f"  Fill Rate: {metrics['Fill_Rate']:.1f}%")
    print(f"  Conditional Mean Return: {metrics['Conditional_Mean_Return']:.2f}%")
    print(f"  Expected Mean Return: {metrics['Expected_Mean_Return']:.2f}%")
    print(f"  Conditional Sharpe Ratio: {metrics['Conditional_Sharpe']:.3f}")
    print(f"  Conditional Win Rate (-15%): {metrics['Conditional_Win_Rate_15pct']:.1f}%")

# Convert to DataFrame
pct_based_df = pd.DataFrame(pct_based_summary)
print(f"\nPercentage-based analysis complete: {len(pct_based_df)} strategies evaluated")

# ==============================================================================
# COMPOSITE SCORING FOR MULTI-OBJECTIVE OPTIMIZATION
# ==============================================================================

print("\nCalculating composite scores for multi-objective optimization...")

def calculate_composite_score(row, df):
    """
    Calculate weighted composite score for strategy optimization

    Considers multiple objectives:
    - Expected Return (30% weight) - Portfolio-level performance
    - Conditional Sharpe (25% weight) - Quality of returns
    - Win Rate at -15% (20% weight) - Consistency
    - Fill Rate (15% weight) - Opportunity frequency
    - Risk Score (10% weight) - Inverse of MAE
    """
    # Normalize metrics to 0-100 scale
    norm_exp_return = (row['Expected_Mean_Return'] / df['Expected_Mean_Return'].max()) * 100
    norm_sharpe = (row['Conditional_Sharpe'] / df['Conditional_Sharpe'].max()) * 100
    norm_win_rate = row['Conditional_Win_Rate_15pct']  # Already 0-100
    norm_fill_rate = row['Fill_Rate']  # Already 0-100

    # Risk score: Inverse of MAE (lower MAE = better)
    # Normalize MAE to 0-100 range, then invert
    max_mae = df['Conditional_Max_WAE'].max()
    norm_risk = 100 - min((row['Conditional_Max_WAE'] / max_mae) * 100, 100)

    # Weights (total = 1.0)
    weights = {
        'expected_return': 0.30,
        'sharpe': 0.25,
        'win_rate': 0.20,
        'fill_rate': 0.15,
        'risk': 0.10
    }

    # Calculate weighted composite score
    score = (norm_exp_return * weights['expected_return'] +
             norm_sharpe * weights['sharpe'] +
             norm_win_rate * weights['win_rate'] +
             norm_fill_rate * weights['fill_rate'] +
             norm_risk * weights['risk'])

    return score

# Apply composite scoring
pct_based_df['Composite_Score'] = pct_based_df.apply(lambda row: calculate_composite_score(row, pct_based_df), axis=1)

print(f"\nTop 3 Strategies by Composite Score:")
top_composite = pct_based_df.nlargest(3, 'Composite_Score')[['Entry_Pct', 'Composite_Score', 'Expected_Mean_Return', 'Conditional_Sharpe', 'Fill_Rate']]
print(top_composite.to_string(index=False))

print(f"\nBest Strategy by Expected Return: {pct_based_df.loc[pct_based_df['Expected_Mean_Return'].idxmax(), 'Entry_Pct']}%")
print(f"Best Strategy by Conditional Sharpe: {pct_based_df.loc[pct_based_df['Conditional_Sharpe'].idxmax(), 'Entry_Pct']}%")
print(f"Best Strategy by Composite Score: {pct_based_df.loc[pct_based_df['Composite_Score'].idxmax(), 'Entry_Pct']}%")

# ==============================================================================
# PHASE 5: STATISTICAL VALIDATION
# ==============================================================================

print("\n" + "=" * 80)
print("Phase 5: Statistical validation of percentage-based strategies...")

# Identify top 3 strategies by different criteria
print("\nTop 3 Strategies by Expected Return:")
top_exp_return = pct_based_df.nlargest(3, 'Expected_Mean_Return')[['Entry_Pct', 'Expected_Mean_Return', 'Fill_Rate', 'Conditional_Sharpe']]
print(top_exp_return.to_string(index=False))

print("\nTop 3 Strategies by Conditional Sharpe:")
top_sharpe = pct_based_df.nlargest(3, 'Conditional_Sharpe')[['Entry_Pct', 'Conditional_Sharpe', 'Conditional_Mean_Return', 'Fill_Rate']]
print(top_sharpe.to_string(index=False))

print("\nTop 3 Strategies by Composite Score:")
top_composite = pct_based_df.nlargest(3, 'Composite_Score')[['Entry_Pct', 'Composite_Score', 'Expected_Mean_Return', 'Conditional_Sharpe', 'Fill_Rate']]
print(top_composite.to_string(index=False))

# Get optimal strategies for detailed analysis
best_exp_return_pct = pct_based_df.loc[pct_based_df['Expected_Mean_Return'].idxmax(), 'Entry_Pct']
best_sharpe_pct = pct_based_df.loc[pct_based_df['Conditional_Sharpe'].idxmax(), 'Entry_Pct']
best_composite_pct = pct_based_df.loc[pct_based_df['Composite_Score'].idxmax(), 'Entry_Pct']

print(f"\n\nOptimal Strategies Identified:")
print(f"  Maximum Expected Return: {best_exp_return_pct}%")
print(f"  Maximum Risk-Adjusted Quality: {best_sharpe_pct}%")
print(f"  Balanced (Composite Score): {best_composite_pct}%")

# Bootstrap confidence intervals for top 3 strategies
def bootstrap_ci(data, metric_func, n_iterations=10000, confidence=0.95):
    """Calculate bootstrap confidence interval"""
    bootstrap_metrics = []
    for _ in range(n_iterations):
        sample = np.random.choice(data, size=len(data), replace=True)
        metric_value = metric_func(sample)
        bootstrap_metrics.append(metric_value)

    alpha = 1 - confidence
    ci_lower = np.percentile(bootstrap_metrics, (alpha / 2) * 100)
    ci_upper = np.percentile(bootstrap_metrics, (1 - alpha / 2) * 100)

    return ci_lower, ci_upper

print(f"\nBootstrap 95% Confidence Intervals (10,000 iterations):")

for entry_pct in [best_exp_return_pct, best_sharpe_pct, best_composite_pct]:
    results = simulate_percentage_based_entry(df, entry_pct)
    filled_returns = results[results['Entry_Filled']]['Best_Return_Pct'].values

    ci_lower, ci_upper = bootstrap_ci(filled_returns, np.mean)
    print(f"  {entry_pct}% Entry - Conditional Mean Return: [{ci_lower:.2f}%, {ci_upper:.2f}%]")

# Expected returns (accounting for fills)
print(f"\nExpected Returns (accounting for fill rate):")
for entry_pct in [best_exp_return_pct, best_sharpe_pct, best_composite_pct]:
    results = simulate_percentage_based_entry(df, entry_pct)
    all_returns = results['Best_Return_Pct'].fillna(0).values
    ci_lower, ci_upper = bootstrap_ci(all_returns, np.mean)
    print(f"  {entry_pct}% Entry - Expected Mean Return: [{ci_lower:.2f}%, {ci_upper:.2f}%]")

# Save analysis results
print("\nSaving analysis results to CSV file...")
pct_based_df.to_csv('/home/tom/Documents/ibkr_scripts/GUS3/data/pct_based_results_12pct.csv', index=False)
print("  pct_based_results_12pct.csv saved")
print(f"  {len(pct_based_df)} strategies analyzed with composite scoring")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
print("\nNext: Run visualization script to generate charts")
print("Then: Generate final markdown report")
