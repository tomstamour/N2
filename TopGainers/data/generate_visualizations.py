#!/usr/bin/env python3
"""
Generate visualizations for Gap-Up Short Entry Strategy Analysis

Creates 6 key visualizations to support decision-making.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import warnings
warnings.filterwarnings('ignore')

# Set style
sns.set_style('whitegrid')
plt.rcParams['figure.dpi'] = 100

print("Loading analysis results (12% constraint)...")
time_based_df = pd.read_csv('time_based_results_12pct.csv')
pct_based_df = pd.read_csv('pct_based_results_12pct.csv')

# Create output directory for plots
import os
os.makedirs('plots_12pct', exist_ok=True)

print("Generating visualizations...")

# ==============================================================================
# VISUALIZATION 1: Efficient Frontier (Risk vs Return)
# ==============================================================================

print("\n1. Creating Efficient Frontier plot...")
fig, ax = plt.subplots(figsize=(14, 9))

# Time-based strategies
ax.scatter(time_based_df['Std_Return'], time_based_df['Mean_Return'],
          c='blue', marker='o', s=200, alpha=0.7, edgecolors='darkblue',
          linewidths=2, label='Time-Based Entry', zorder=3)

# Percentage-based strategies (conditional metrics)
ax.scatter(pct_based_df['Conditional_Std_Return'], pct_based_df['Conditional_Mean_Return'],
          c='red', marker='s', s=200, alpha=0.7, edgecolors='darkred',
          linewidths=2, label='Percentage-Based Entry (Conditional)', zorder=3)

# Annotate time-based strategies
for idx, row in time_based_df.iterrows():
    ax.annotate(row['Entry_Time'],
               (row['Std_Return'], row['Mean_Return']),
               fontsize=9, ha='center', va='bottom',
               xytext=(0, 5), textcoords='offset points')

# Annotate percentage-based strategies
for idx, row in pct_based_df.iterrows():
    ax.annotate(f"{int(row['Entry_Pct'])}%",
               (row['Conditional_Std_Return'], row['Conditional_Mean_Return']),
               fontsize=9, ha='center', va='bottom',
               xytext=(0, 5), textcoords='offset points')

ax.set_xlabel('Return Volatility (Std Dev %)', fontsize=12, fontweight='bold')
ax.set_ylabel('Mean Return (%)', fontsize=12, fontweight='bold')
ax.set_title('Entry Strategy Efficient Frontier: Risk vs Return\n(Higher Return & Lower Risk = Better)',
            fontsize=14, fontweight='bold', pad=20)
ax.legend(fontsize=11, loc='upper left')
ax.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('plots_12pct/1_efficient_frontier.png', dpi=150, bbox_inches='tight')
print("  Saved: plots_12pct/1_efficient_frontier.png")

# ==============================================================================
# VISUALIZATION 2: Win Rate Heatmap
# ==============================================================================

print("\n2. Creating Win Rate Heatmap...")
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 8))

# Time-based win rates
profit_targets = [5, 10, 15, 20, 25, 30]
time_win_rates = time_based_df[[f'Win_Rate_{t}pct' for t in profit_targets]].values
time_labels = time_based_df['Entry_Time'].values

sns.heatmap(time_win_rates, annot=True, fmt='.1f', cmap='RdYlGn',
           xticklabels=[f'-{t}%' for t in profit_targets],
           yticklabels=time_labels, cbar_kws={'label': 'Win Rate (%)'},
           ax=ax1, vmin=0, vmax=100)
ax1.set_xlabel('Profit Target', fontsize=12, fontweight='bold')
ax1.set_ylabel('Entry Time', fontsize=12, fontweight='bold')
ax1.set_title('Time-Based Entry: Win Rates by Profit Target',
             fontsize=13, fontweight='bold')

# Percentage-based win rates (conditional)
pct_win_rates = pct_based_df[[f'Conditional_Win_Rate_{t}pct' for t in profit_targets]].values
pct_labels = [f"{int(p)}%" for p in pct_based_df['Entry_Pct'].values]

sns.heatmap(pct_win_rates, annot=True, fmt='.1f', cmap='RdYlGn',
           xticklabels=[f'-{t}%' for t in profit_targets],
           yticklabels=pct_labels, cbar_kws={'label': 'Win Rate (%)'},
           ax=ax2, vmin=0, vmax=100)
ax2.set_xlabel('Profit Target', fontsize=12, fontweight='bold')
ax2.set_ylabel('Entry Level', fontsize=12, fontweight='bold')
ax2.set_title('Percentage-Based Entry: Conditional Win Rates by Profit Target',
             fontsize=13, fontweight='bold')

plt.tight_layout()
plt.savefig('plots_12pct/2_win_rate_heatmap.png', dpi=150, bbox_inches='tight')
print("  Saved: plots_12pct/2_win_rate_heatmap.png")

# ==============================================================================
# VISUALIZATION 3: Return Distribution Comparison
# ==============================================================================

print("\n3. Creating Return Distribution plots...")

# Load detailed results for top strategies
df = pd.read_csv('/home/tom/Documents/ibkr_scripts/GUS3/data/gus_stats_ibkr.txt')

def simulate_time_based_entry(df, entry_time):
    """Quick simulation to get returns"""
    entry_col = f'{entry_time}_H'
    entry_price = df[entry_col]
    intraday_low_price = df['09:30_O'] * (1 + df['Low%'] / 100)
    best_return_pct = ((entry_price - intraday_low_price) / entry_price) * 100
    return best_return_pct

def simulate_percentage_based_entry(df, entry_pct):
    """Quick simulation to get returns"""
    entry_price = df['09:30_O'] * (1 + entry_pct / 100)
    entry_filled = df['High%'] >= entry_pct
    intraday_low_price = df['09:30_O'] * (1 + df['Low%'] / 100)
    best_return_pct = np.where(
        entry_filled,
        ((entry_price - intraday_low_price) / entry_price) * 100,
        np.nan
    )
    return best_return_pct

fig, axes = plt.subplots(2, 2, figsize=(16, 12))

# Top time-based strategies
strategies_time = ['09:30', '09:35']
for idx, strategy in enumerate(strategies_time):
    returns = simulate_time_based_entry(df, strategy)
    ax = axes[0, idx]
    ax.hist(returns, bins=30, alpha=0.7, edgecolor='black', color='steelblue')
    ax.axvline(returns.mean(), color='red', linestyle='--', linewidth=2, label=f'Mean: {returns.mean():.2f}%')
    ax.axvline(returns.median(), color='green', linestyle='--', linewidth=2, label=f'Median: {returns.median():.2f}%')
    ax.axvline(15, color='orange', linestyle=':', linewidth=2, label='Target: 15%')
    ax.set_xlabel('Return (%)', fontsize=11, fontweight='bold')
    ax.set_ylabel('Frequency', fontsize=11, fontweight='bold')
    ax.set_title(f'Time-Based {strategy} Return Distribution', fontsize=12, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis='y')

# Top percentage-based strategies
strategies_pct = [10, 15]
for idx, strategy in enumerate(strategies_pct):
    returns = simulate_percentage_based_entry(df, strategy)
    filled_returns = returns[~np.isnan(returns)]
    ax = axes[1, idx]
    ax.hist(filled_returns, bins=30, alpha=0.7, edgecolor='black', color='indianred')
    ax.axvline(filled_returns.mean(), color='red', linestyle='--', linewidth=2, label=f'Mean: {filled_returns.mean():.2f}%')
    ax.axvline(np.median(filled_returns), color='green', linestyle='--', linewidth=2, label=f'Median: {np.median(filled_returns):.2f}%')
    ax.axvline(15, color='orange', linestyle=':', linewidth=2, label='Target: 15%')
    ax.set_xlabel('Return (%)', fontsize=11, fontweight='bold')
    ax.set_ylabel('Frequency', fontsize=11, fontweight='bold')
    fill_rate = (~np.isnan(returns)).sum() / len(returns) * 100
    ax.set_title(f'Percentage-Based {strategy}% Return Distribution (Fill Rate: {fill_rate:.1f}%)',
                fontsize=12, fontweight='bold')
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis='y')

plt.tight_layout()
plt.savefig('plots_12pct/3_return_distributions.png', dpi=150, bbox_inches='tight')
print("  Saved: plots_12pct/3_return_distributions.png")

# ==============================================================================
# VISUALIZATION 4: Fill Rate vs Performance Trade-off
# ==============================================================================

print("\n4. Creating Fill Rate vs Performance plot...")
fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))

# Fill Rate vs Conditional Mean Return
ax1.scatter(pct_based_df['Fill_Rate'], pct_based_df['Conditional_Mean_Return'],
           s=200, alpha=0.7, c=pct_based_df['Entry_Pct'], cmap='coolwarm',
           edgecolors='black', linewidths=2)
for idx, row in pct_based_df.iterrows():
    ax1.annotate(f"{int(row['Entry_Pct'])}%",
                (row['Fill_Rate'], row['Conditional_Mean_Return']),
                fontsize=10, ha='center', va='bottom',
                xytext=(0, 5), textcoords='offset points')
ax1.set_xlabel('Fill Rate (%)', fontsize=12, fontweight='bold')
ax1.set_ylabel('Conditional Mean Return (%)', fontsize=12, fontweight='bold')
ax1.set_title('Percentage-Based Entry: Fill Rate vs Conditional Return\n(Higher Both = Better)',
             fontsize=13, fontweight='bold')
ax1.grid(True, alpha=0.3)

# Fill Rate vs Expected Return
ax2.scatter(pct_based_df['Fill_Rate'], pct_based_df['Expected_Mean_Return'],
           s=200, alpha=0.7, c=pct_based_df['Entry_Pct'], cmap='coolwarm',
           edgecolors='black', linewidths=2)
for idx, row in pct_based_df.iterrows():
    ax2.annotate(f"{int(row['Entry_Pct'])}%",
                (row['Fill_Rate'], row['Expected_Mean_Return']),
                fontsize=10, ha='center', va='bottom',
                xytext=(0, 5), textcoords='offset points')
ax2.set_xlabel('Fill Rate (%)', fontsize=12, fontweight='bold')
ax2.set_ylabel('Expected Mean Return (%)', fontsize=12, fontweight='bold')
ax2.set_title('Percentage-Based Entry: Fill Rate vs Expected Return\n(Accounts for Missed Opportunities)',
             fontsize=13, fontweight='bold')
ax2.grid(True, alpha=0.3)

plt.tight_layout()
plt.savefig('plots_12pct/4_fill_rate_tradeoff.png', dpi=150, bbox_inches='tight')
print("  Saved: plots_12pct/4_fill_rate_tradeoff.png")

# ==============================================================================
# VISUALIZATION 5: Sharpe Ratio Comparison
# ==============================================================================

print("\n5. Creating Sharpe Ratio comparison...")
fig, ax = plt.subplots(figsize=(14, 8))

# Combine both strategies for comparison
x_positions_time = np.arange(len(time_based_df))
x_positions_pct = np.arange(len(pct_based_df)) + len(time_based_df) + 1

# Plot time-based Sharpe ratios
bars1 = ax.bar(x_positions_time, time_based_df['Sharpe_Ratio'],
              color='steelblue', alpha=0.8, label='Time-Based Entry', edgecolor='black')

# Plot percentage-based Sharpe ratios (conditional)
bars2 = ax.bar(x_positions_pct, pct_based_df['Conditional_Sharpe'],
              color='indianred', alpha=0.8, label='Percentage-Based Entry (Conditional)', edgecolor='black')

# Add value labels on bars
for idx, (bar, val) in enumerate(zip(bars1, time_based_df['Sharpe_Ratio'])):
    height = bar.get_height()
    ax.text(bar.get_x() + bar.get_width()/2., height,
           f'{val:.2f}', ha='center', va='bottom', fontsize=9)

for idx, (bar, val) in enumerate(zip(bars2, pct_based_df['Conditional_Sharpe'])):
    height = bar.get_height()
    ax.text(bar.get_x() + bar.get_width()/2., height,
           f'{val:.2f}', ha='center', va='bottom', fontsize=9)

# Set labels
all_labels = list(time_based_df['Entry_Time']) + [''] + [f"{int(p)}%" for p in pct_based_df['Entry_Pct']]
ax.set_xticks(list(x_positions_time) + [len(time_based_df)] + list(x_positions_pct))
ax.set_xticklabels(all_labels, rotation=45, ha='right')
ax.set_ylabel('Sharpe Ratio', fontsize=12, fontweight='bold')
ax.set_xlabel('Entry Strategy', fontsize=12, fontweight='bold')
ax.set_title('Sharpe Ratio Comparison Across All Entry Strategies\n(Higher = Better Risk-Adjusted Returns)',
            fontsize=14, fontweight='bold', pad=20)
ax.legend(fontsize=11)
ax.grid(True, alpha=0.3, axis='y')
ax.axhline(y=1.0, color='green', linestyle='--', alpha=0.5, label='Good (>1.0)')
ax.axhline(y=2.0, color='orange', linestyle='--', alpha=0.5, label='Excellent (>2.0)')

plt.tight_layout()
plt.savefig('plots_12pct/5_sharpe_ratio_comparison.png', dpi=150, bbox_inches='tight')
print("  Saved: plots_12pct/5_sharpe_ratio_comparison.png")

# ==============================================================================
# VISUALIZATION 6: Entry Level vs Return Relationship
# ==============================================================================

print("\n6. Creating Entry Level vs Return scatter plot...")
fig, ax = plt.subplots(figsize=(14, 8))

# Time-based: entry level varies with time
ax.scatter(time_based_df['Avg_Entry_Level_Pct'], time_based_df['Mean_Return'],
          s=250, alpha=0.7, c='blue', marker='o', edgecolors='darkblue',
          linewidths=2, label='Time-Based Entry', zorder=3)

# Percentage-based: entry level is fixed, return is conditional
ax.scatter(pct_based_df['Entry_Pct'], pct_based_df['Conditional_Mean_Return'],
          s=250, alpha=0.7, c='red', marker='s', edgecolors='darkred',
          linewidths=2, label='Percentage-Based Entry (Conditional)', zorder=3)

# Annotate points
for idx, row in time_based_df.iterrows():
    ax.annotate(row['Entry_Time'],
               (row['Avg_Entry_Level_Pct'], row['Mean_Return']),
               fontsize=9, ha='right', va='bottom',
               xytext=(-5, 5), textcoords='offset points')

for idx, row in pct_based_df.iterrows():
    ax.annotate(f"{int(row['Entry_Pct'])}%",
               (row['Entry_Pct'], row['Conditional_Mean_Return']),
               fontsize=9, ha='left', va='bottom',
               xytext=(5, 5), textcoords='offset points')

ax.set_xlabel('Entry Level (% above 09:30 Open)', fontsize=12, fontweight='bold')
ax.set_ylabel('Mean Return (%)', fontsize=12, fontweight='bold')
ax.set_title('Entry Level vs Mean Return Relationship\n(Shows optimal entry zones)',
            fontsize=14, fontweight='bold', pad=20)
ax.legend(fontsize=11, loc='upper right')
ax.grid(True, alpha=0.3)

# Add trend lines
from scipy.stats import linregress

# Time-based trend
slope_time, intercept_time, r_time, p_time, se_time = linregress(
    time_based_df['Avg_Entry_Level_Pct'], time_based_df['Mean_Return']
)
x_time = np.linspace(time_based_df['Avg_Entry_Level_Pct'].min(),
                     time_based_df['Avg_Entry_Level_Pct'].max(), 100)
y_time = slope_time * x_time + intercept_time
ax.plot(x_time, y_time, 'b--', alpha=0.5, linewidth=2,
       label=f'Time-Based Trend (R²={r_time**2:.3f})')

# Percentage-based trend
slope_pct, intercept_pct, r_pct, p_pct, se_pct = linregress(
    pct_based_df['Entry_Pct'], pct_based_df['Conditional_Mean_Return']
)
x_pct = np.linspace(pct_based_df['Entry_Pct'].min(),
                    pct_based_df['Entry_Pct'].max(), 100)
y_pct = slope_pct * x_pct + intercept_pct
ax.plot(x_pct, y_pct, 'r--', alpha=0.5, linewidth=2,
       label=f'Percentage-Based Trend (R²={r_pct**2:.3f})')

ax.legend(fontsize=10, loc='upper right')

plt.tight_layout()
plt.savefig('plots_12pct/6_entry_level_vs_return.png', dpi=150, bbox_inches='tight')
print("  Saved: plots_12pct/6_entry_level_vs_return.png")

print("\n" + "=" * 80)
print("All visualizations generated successfully!")
print("Saved in: /home/tom/Documents/ibkr_scripts/GUS3/data/plots_12pct/")
print("=" * 80)
