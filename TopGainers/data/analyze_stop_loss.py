#!/usr/bin/env python3
"""
Analyze optimal stop-loss levels based on Maximum Adverse Excursion (MAE)
"""

import pandas as pd
import numpy as np

print("=" * 80)
print("STOP-LOSS ANALYSIS: Optimal Levels Based on Historical MAE")
print("=" * 80)

# Load the data
print("\nLoading dataset...")
df = pd.read_csv('gus_stats_ibkr.txt')

# Load percentage-based results
pct_results = pd.read_csv('pct_based_results_12pct.csv')

print(f"Dataset: {len(df)} historical events")
print(f"Entry levels analyzed: {len(pct_results)}")

# Function to simulate with different stop-loss levels
def analyze_stop_loss_levels(df, entry_pct, stop_levels=[20, 25, 30, 35, 40, 45, 50]):
    """
    Analyze impact of different stop-loss levels on strategy performance

    For each stop level, calculate:
    - Stop-out rate (% of trades that hit stop before profit)
    - Adjusted expected return (accounting for stop-outs)
    - MAE coverage (% of adverse excursions contained)
    """

    results = []

    # Filter stocks that reached entry level
    entry_price_mult = 1 + (entry_pct / 100)
    reached_entry = df['High%'] >= entry_pct
    filled_df = df[reached_entry].copy()

    if len(filled_df) == 0:
        return pd.DataFrame()

    # Calculate actual MAE for each trade
    # MAE = (High% - entry_pct) for stocks that reached entry
    filled_df['MAE'] = filled_df['High%'] - entry_pct

    # Calculate profit (if no stop)
    # Profit = entry_pct - Low%
    filled_df['Profit_No_Stop'] = entry_pct - filled_df['Low%']

    for stop_pct in stop_levels:
        # Stop-out occurs if MAE >= stop_pct
        stopped_out = filled_df['MAE'] >= stop_pct

        # Calculate returns
        # If stopped out: loss = -stop_pct
        # If not stopped: profit = entry_pct - Low%
        filled_df['Return_With_Stop'] = np.where(
            stopped_out,
            -stop_pct,  # Loss when stopped out
            filled_df['Profit_No_Stop']  # Original profit
        )

        # Metrics
        stop_out_rate = stopped_out.sum() / len(filled_df) * 100
        mean_return_with_stop = filled_df['Return_With_Stop'].mean()
        median_return_with_stop = filled_df['Return_With_Stop'].median()

        # MAE coverage (what % of adverse excursions are contained)
        mae_contained = (filled_df['MAE'] < stop_pct).sum() / len(filled_df) * 100

        # Win rate at -15% target (accounting for stops)
        wins = (filled_df['Return_With_Stop'] >= 15).sum()
        win_rate = wins / len(filled_df) * 100

        results.append({
            'Stop_Loss_Pct': stop_pct,
            'Stop_Out_Rate': stop_out_rate,
            'Mean_Return': mean_return_with_stop,
            'Median_Return': median_return_with_stop,
            'Win_Rate_15pct': win_rate,
            'MAE_Coverage': mae_contained
        })

    return pd.DataFrame(results)

# Analyze key entry levels
entry_levels_to_analyze = [2, 5, 8, 12]
stop_levels = [20, 25, 30, 35, 40, 45, 50, 60]

print("\n" + "=" * 80)
print("STOP-LOSS IMPACT ANALYSIS")
print("=" * 80)

all_results = {}

for entry_pct in entry_levels_to_analyze:
    print(f"\n{'=' * 80}")
    print(f"Entry Level: {entry_pct}% above open")
    print(f"{'=' * 80}")

    results_df = analyze_stop_loss_levels(df, entry_pct, stop_levels)

    if len(results_df) == 0:
        print("  No data available")
        continue

    print("\n" + results_df.to_string(index=False))

    all_results[entry_pct] = results_df

    # Find optimal stop level (best mean return)
    best_idx = results_df['Mean_Return'].idxmax()
    best_stop = results_df.loc[best_idx]

    print(f"\nOptimal Stop-Loss: {int(best_stop['Stop_Loss_Pct'])}% above entry")
    print(f"  Mean Return: {best_stop['Mean_Return']:.2f}%")
    print(f"  Stop-Out Rate: {best_stop['Stop_Out_Rate']:.1f}%")
    print(f"  Win Rate: {best_stop['Win_Rate_15pct']:.1f}%")
    print(f"  MAE Coverage: {best_stop['MAE_Coverage']:.1f}%")

# Calculate MAE percentiles for each entry level
print("\n" + "=" * 80)
print("MAE PERCENTILE ANALYSIS")
print("=" * 80)

mae_percentiles = []

for entry_pct in entry_levels_to_analyze:
    reached_entry = df['High%'] >= entry_pct
    filled_df = df[reached_entry].copy()

    if len(filled_df) == 0:
        continue

    filled_df['MAE'] = filled_df['High%'] - entry_pct

    percentiles = [50, 75, 90, 95, 99]
    mae_stats = {
        'Entry_Pct': entry_pct,
        'Count': len(filled_df),
        'Mean_MAE': filled_df['MAE'].mean(),
        'Median_MAE': filled_df['MAE'].median(),
        'Max_MAE': filled_df['MAE'].max()
    }

    for p in percentiles:
        mae_stats[f'P{p}'] = np.percentile(filled_df['MAE'], p)

    mae_percentiles.append(mae_stats)

mae_df = pd.DataFrame(mae_percentiles)
print("\n" + mae_df.to_string(index=False))

# Recommendations
print("\n" + "=" * 80)
print("STOP-LOSS RECOMMENDATIONS")
print("=" * 80)

print("\nBased on MAE percentile analysis:\n")

for _, row in mae_df.iterrows():
    entry = int(row['Entry_Pct'])
    print(f"\n{entry}% Entry Level:")
    print(f"  Conservative (95% coverage): +{int(np.ceil(row['P95']))}% stop")
    print(f"  Moderate (90% coverage):     +{int(np.ceil(row['P90']))}% stop")
    print(f"  Aggressive (75% coverage):   +{int(np.ceil(row['P75']))}% stop")
    print(f"  Max observed MAE:            +{row['Max_MAE']:.1f}%")

# Save results
print("\nSaving stop-loss analysis results...")
for entry_pct, results_df in all_results.items():
    filename = f'stop_loss_analysis_{entry_pct}pct.csv'
    results_df.to_csv(filename, index=False)
    print(f"  {filename}")

mae_df.to_csv('mae_percentiles.csv', index=False)
print(f"  mae_percentiles.csv")

print("\n" + "=" * 80)
print("ANALYSIS COMPLETE")
print("=" * 80)
