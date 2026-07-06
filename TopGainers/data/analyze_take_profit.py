#!/usr/bin/env python3
"""
Analyze optimal take-profit strategies for Gap-Up Short trading

Compares:
1. Percentage-based take-profit (limit orders at X% below entry)
2. Time-based take-profit (exit at specific time)
"""

import pandas as pd
import numpy as np
from datetime import datetime

print("=" * 80)
print("TAKE-PROFIT STRATEGY ANALYSIS")
print("=" * 80)

# Load data
print("\nLoading dataset...")
df = pd.read_csv('gus_stats_ibkr.txt')
print(f"Dataset: {len(df)} historical events")

# Entry levels to analyze
entry_levels = [2, 5, 8, 12]

# ==============================================================================
# PART 1: PERCENTAGE-BASED TAKE-PROFIT ANALYSIS
# ==============================================================================

print("\n" + "=" * 80)
print("PART 1: PERCENTAGE-BASED TAKE-PROFIT ANALYSIS")
print("=" * 80)

def analyze_percentage_take_profit(df, entry_pct, profit_targets):
    """
    Analyze different percentage-based take-profit levels

    For each profit target:
    - Calculate hit rate (% of trades reaching target)
    - Mean return (for those reaching target)
    - Expected return (accounting for misses)
    - Sharpe ratio
    """

    # Filter stocks that reached entry level
    reached_entry = df['High%'] >= entry_pct
    filled_df = df[reached_entry].copy()

    if len(filled_df) == 0:
        return pd.DataFrame()

    results = []

    for target_pct in profit_targets:
        # Target is reached if Low% <= -target_pct from entry
        # Entry is at entry_pct, so we need Low% to drop to (entry_pct - target_pct)
        # Which means: entry_pct - Low% >= target_pct
        # Or: Low% <= entry_pct - target_pct

        target_reached = filled_df['Low%'] <= (entry_pct - target_pct)

        hit_rate = target_reached.sum() / len(filled_df) * 100

        # Returns for those that hit target
        returns_hit = target_pct  # Profit is exactly the target

        # Expected return (accounting for misses)
        # For misses, we could model as "held to close" or "stopped out"
        # For now, let's use actual return to intraday low for misses
        filled_df['Profit'] = np.where(
            target_reached,
            target_pct,  # Hit target = exact profit
            entry_pct - filled_df['Low%']  # Missed target = profit to low
        )

        mean_return = filled_df['Profit'].mean()
        median_return = filled_df['Profit'].median()
        std_return = filled_df['Profit'].std()
        sharpe = mean_return / std_return if std_return > 0 else 0

        # Downside deviation for Sortino
        negative_returns = filled_df['Profit'][filled_df['Profit'] < target_pct]
        downside_dev = negative_returns.std() if len(negative_returns) > 0 else std_return
        sortino = mean_return / downside_dev if downside_dev > 0 else 0

        # Return percentiles
        p10 = np.percentile(filled_df['Profit'], 10)
        p25 = np.percentile(filled_df['Profit'], 25)
        p50 = np.percentile(filled_df['Profit'], 50)
        p75 = np.percentile(filled_df['Profit'], 75)
        p90 = np.percentile(filled_df['Profit'], 90)

        results.append({
            'Target_Pct': target_pct,
            'Hit_Rate': hit_rate,
            'Mean_Return': mean_return,
            'Median_Return': median_return,
            'Std_Return': std_return,
            'Sharpe_Ratio': sharpe,
            'Sortino_Ratio': sortino,
            'P10': p10,
            'P25': p25,
            'P50': p50,
            'P75': p75,
            'P90': p90
        })

    return pd.DataFrame(results)

# Profit targets to test
profit_targets = [5, 7, 10, 12, 15, 17, 20, 22, 25, 30, 35, 40]

all_pct_results = {}

for entry_pct in entry_levels:
    print(f"\n{'=' * 80}")
    print(f"Entry Level: {entry_pct}% above open")
    print(f"{'=' * 80}")

    results_df = analyze_percentage_take_profit(df, entry_pct, profit_targets)

    if len(results_df) == 0:
        print("  No data available")
        continue

    print("\n" + results_df.to_string(index=False))

    all_pct_results[entry_pct] = results_df

    # Find optimal target
    best_idx = results_df['Mean_Return'].idxmax()
    best = results_df.loc[best_idx]

    print(f"\nOptimal Take-Profit Target: -{int(best['Target_Pct'])}% below entry")
    print(f"  Hit Rate: {best['Hit_Rate']:.1f}%")
    print(f"  Mean Return: {best['Mean_Return']:.2f}%")
    print(f"  Sharpe Ratio: {best['Sharpe_Ratio']:.3f}")

# ==============================================================================
# PART 2: TIME-BASED TAKE-PROFIT ANALYSIS
# ==============================================================================

print("\n" + "=" * 80)
print("PART 2: TIME-BASED TAKE-PROFIT ANALYSIS")
print("=" * 80)

def analyze_time_based_exit(df, entry_pct, exit_times):
    """
    Analyze exiting at specific times throughout the day

    For each exit time:
    - Calculate mean return (from entry to that time's close)
    - Win rate (positive returns)
    - Risk metrics
    """

    # Filter stocks that reached entry level
    reached_entry = df['High%'] >= entry_pct
    filled_df = df[reached_entry].copy()

    if len(filled_df) == 0:
        return pd.DataFrame()

    results = []

    for exit_time in exit_times:
        close_col = f'{exit_time}_C'

        if close_col not in df.columns:
            continue

        # Calculate return from entry to exit time close
        # Entry is at entry_pct above open
        # Return = (entry_pct - close_pct)
        # where close_pct = ((close / open) - 1) * 100

        filled_df['Exit_Price_Pct'] = ((filled_df[close_col] / filled_df['09:30_O']) - 1) * 100
        filled_df['Return'] = entry_pct - filled_df['Exit_Price_Pct']

        # Handle missing data
        valid_returns = filled_df['Return'].dropna()

        if len(valid_returns) == 0:
            continue

        mean_return = valid_returns.mean()
        median_return = valid_returns.median()
        std_return = valid_returns.std()
        sharpe = mean_return / std_return if std_return > 0 else 0

        # Win rate (positive returns)
        win_rate = (valid_returns > 0).sum() / len(valid_returns) * 100

        # Specific profit target hit rates
        hit_5 = (valid_returns >= 5).sum() / len(valid_returns) * 100
        hit_10 = (valid_returns >= 10).sum() / len(valid_returns) * 100
        hit_15 = (valid_returns >= 15).sum() / len(valid_returns) * 100
        hit_20 = (valid_returns >= 20).sum() / len(valid_returns) * 100

        # Downside deviation
        negative_returns = valid_returns[valid_returns < 0]
        downside_dev = negative_returns.std() if len(negative_returns) > 0 else std_return
        sortino = mean_return / downside_dev if downside_dev > 0 else 0

        # Percentiles
        p10 = np.percentile(valid_returns, 10)
        p25 = np.percentile(valid_returns, 25)
        p50 = np.percentile(valid_returns, 50)
        p75 = np.percentile(valid_returns, 75)
        p90 = np.percentile(valid_returns, 90)

        results.append({
            'Exit_Time': exit_time,
            'Count': len(valid_returns),
            'Mean_Return': mean_return,
            'Median_Return': median_return,
            'Std_Return': std_return,
            'Sharpe_Ratio': sharpe,
            'Sortino_Ratio': sortino,
            'Win_Rate': win_rate,
            'Hit_5pct': hit_5,
            'Hit_10pct': hit_10,
            'Hit_15pct': hit_15,
            'Hit_20pct': hit_20,
            'P10': p10,
            'P25': p25,
            'P50': p50,
            'P75': p75,
            'P90': p90
        })

    return pd.DataFrame(results)

# Exit times to test (30-minute intervals through the day)
exit_times = [
    '09:30', '10:00', '10:30', '11:00', '11:30', '12:00',
    '12:30', '13:00', '13:30', '14:00', '14:30', '15:00', '15:30', '15:55'
]

all_time_results = {}

for entry_pct in entry_levels:
    print(f"\n{'=' * 80}")
    print(f"Entry Level: {entry_pct}% above open")
    print(f"{'=' * 80}")

    results_df = analyze_time_based_exit(df, entry_pct, exit_times)

    if len(results_df) == 0:
        print("  No data available")
        continue

    print("\n" + results_df.to_string(index=False))

    all_time_results[entry_pct] = results_df

    # Find optimal exit time
    best_idx = results_df['Mean_Return'].idxmax()
    best = results_df.loc[best_idx]

    print(f"\nOptimal Exit Time: {best['Exit_Time']}")
    print(f"  Mean Return: {best['Mean_Return']:.2f}%")
    print(f"  Sharpe Ratio: {best['Sharpe_Ratio']:.3f}")
    print(f"  Win Rate: {best['Win_Rate']:.1f}%")

# ==============================================================================
# PART 3: COMPARISON AND RECOMMENDATIONS
# ==============================================================================

print("\n" + "=" * 80)
print("PART 3: STRATEGY COMPARISON")
print("=" * 80)

comparison_results = []

for entry_pct in entry_levels:
    if entry_pct not in all_pct_results or entry_pct not in all_time_results:
        continue

    pct_df = all_pct_results[entry_pct]
    time_df = all_time_results[entry_pct]

    # Best percentage-based
    best_pct_idx = pct_df['Mean_Return'].idxmax()
    best_pct = pct_df.loc[best_pct_idx]

    # Best time-based
    best_time_idx = time_df['Mean_Return'].idxmax()
    best_time = time_df.loc[best_time_idx]

    # Compare at -15% target (commonly used)
    pct_15 = pct_df[pct_df['Target_Pct'] == 15].iloc[0] if len(pct_df[pct_df['Target_Pct'] == 15]) > 0 else None

    comparison_results.append({
        'Entry_Pct': entry_pct,
        'Best_Pct_Target': int(best_pct['Target_Pct']),
        'Best_Pct_Return': best_pct['Mean_Return'],
        'Best_Pct_Sharpe': best_pct['Sharpe_Ratio'],
        'Best_Pct_HitRate': best_pct['Hit_Rate'],
        'Best_Time': best_time['Exit_Time'],
        'Best_Time_Return': best_time['Mean_Return'],
        'Best_Time_Sharpe': best_time['Sharpe_Ratio'],
        'Best_Time_WinRate': best_time['Win_Rate'],
        'Standard_15pct_Return': pct_15['Mean_Return'] if pct_15 is not None else None,
        'Standard_15pct_HitRate': pct_15['Hit_Rate'] if pct_15 is not None else None
    })

comparison_df = pd.DataFrame(comparison_results)

print("\n" + "=" * 80)
print("OPTIMAL STRATEGY COMPARISON BY ENTRY LEVEL")
print("=" * 80)
print("\n" + comparison_df.to_string(index=False))

# Overall recommendations
print("\n" + "=" * 80)
print("RECOMMENDATIONS")
print("=" * 80)

print("\nBased on mean return optimization:\n")

for _, row in comparison_df.iterrows():
    entry = int(row['Entry_Pct'])

    if row['Best_Pct_Return'] > row['Best_Time_Return']:
        winner = "Percentage-Based"
        advantage = row['Best_Pct_Return'] - row['Best_Time_Return']
    else:
        winner = "Time-Based"
        advantage = row['Best_Time_Return'] - row['Best_Pct_Return']

    print(f"{entry}% Entry:")
    print(f"  Winner: {winner} (+{advantage:.2f}% advantage)")
    print(f"  Percentage-Based Best: -{int(row['Best_Pct_Target'])}% target, {row['Best_Pct_Return']:.2f}% return, {row['Best_Pct_HitRate']:.1f}% hit rate")
    print(f"  Time-Based Best: {row['Best_Time']} exit, {row['Best_Time_Return']:.2f}% return, {row['Best_Time_WinRate']:.1f}% win rate")
    print(f"  Standard -15% Target: {row['Standard_15pct_Return']:.2f}% return, {row['Standard_15pct_HitRate']:.1f}% hit rate")
    print()

# Save results
print("\nSaving analysis results...")

for entry_pct in entry_levels:
    if entry_pct in all_pct_results:
        all_pct_results[entry_pct].to_csv(f'take_profit_pct_{entry_pct}pct.csv', index=False)
        print(f"  take_profit_pct_{entry_pct}pct.csv")

    if entry_pct in all_time_results:
        all_time_results[entry_pct].to_csv(f'take_profit_time_{entry_pct}pct.csv', index=False)
        print(f"  take_profit_time_{entry_pct}pct.csv")

comparison_df.to_csv('take_profit_comparison.csv', index=False)
print(f"  take_profit_comparison.csv")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
