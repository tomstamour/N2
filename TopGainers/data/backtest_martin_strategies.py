#!/usr/bin/env python3
"""
Backtest Martin's Gap-Up Short Strategies

Backtests 13 defined strategies on historical gap-up short data
Calculates: Win Rate, Max Drawdown, Win Ratio, Sharpe Ratio
"""

import pandas as pd
import numpy as np
from datetime import datetime

print("=" * 80)
print("MARTIN'S STRATEGIES BACKTESTING")
print("=" * 80)

# Load historical data
print("\nLoading dataset...")
df = pd.read_csv('gus_stats_ibkr.txt')
print(f"Dataset: {len(df)} historical events")

# Define strategies
strategies = {
    'S1': {'entry': 10, 'sl': 40, 'tp': -25, 'position_size': 36.67, 'exit_time': 'EOD'},
    'S2': {'entry': 10, 'sl': 40, 'tp': -25, 'position_size': 36.67, 'exit_time': 'EOD'},  # Duplicate of S1
    'S3': {'entry': 10, 'sl': 50, 'tp': -25, 'position_size': 27.50, 'exit_time': 'EOD'},
    'S4': {'entry': 10, 'sl': 50, 'tp': -25, 'position_size': 27.50, 'exit_time': '13:00'},
    'S5': {'entry': 'TIME:09:45', 'sl': 30, 'tp': -30, 'position_size': 53.26, 'exit_time': '13:00'},
    'S6': {'entry': 0, 'sl': 40, 'tp': -30, 'position_size': 25.00, 'exit_time': '13:00'},
    'S7': {'entry': 0, 'sl': 60, 'tp': -15, 'position_size': 16.67, 'exit_time': 'EOD'},
    'S8': {'entry': 0, 'sl': 60, 'tp': -55, 'position_size': 16.67, 'exit_time': '12:45'},
    'S9': {'entry': 2, 'sl': 45, 'tp': -15, 'position_size': 23.72, 'exit_time': '15:55'},
    'S10': {'entry': 0, 'sl': 60, 'tp': -45, 'position_size': 16.67, 'exit_time': '12:45'},
    'S11': {'entry': 0, 'sl': 60, 'tp': -40, 'position_size': 16.67, 'exit_time': '12:45'},
    'S12': {'entry': 0, 'sl': 60, 'tp': -35, 'position_size': 16.67, 'exit_time': '12:45'},
    'S13': {'entry': 0, 'sl': 50, 'tp': -55, 'position_size': 20.00, 'exit_time': '12:45'},
}

def time_to_minutes(time_str):
    """Convert HH:MM to minutes from 09:30"""
    try:
        h, m = map(int, time_str.split(':'))
        return (h - 9) * 60 + (m - 30)
    except:
        return np.nan

def get_exit_price(row, exit_time):
    """Get exit price based on exit time"""
    if exit_time == 'EOD':
        return row['15:55_C']
    else:
        col_name = f'{exit_time}_O'
        if col_name in row.index:
            return row[col_name]
        else:
            # If exact time not available, use close
            return row['15:55_C']

def simulate_trade(row, strategy):
    """
    Simulate a single trade based on strategy parameters

    Returns dict with:
    - entry_filled: bool
    - exit_reason: 'TP', 'SL', 'TIME', 'NO_ENTRY'
    - return_pct: % return (positive = profit)
    - entry_price: actual entry price
    - exit_price: actual exit price
    """

    open_price = row['09:30_O']

    # Determine entry
    if strategy['entry'] == 'TIME:09:45':
        # Time-based entry at open of 09:45 bar
        entry_col = '09:45_O'
        if entry_col not in row.index or pd.isna(row[entry_col]):
            return {'entry_filled': False, 'exit_reason': 'NO_ENTRY', 'return_pct': 0,
                    'entry_price': np.nan, 'exit_price': np.nan}
        entry_price = row[entry_col]
        entry_pct = ((entry_price / open_price) - 1) * 100
        entry_filled = True
    else:
        # Percentage-based entry
        entry_pct = strategy['entry']
        entry_price = open_price * (1 + entry_pct / 100)

        # Check if entry was reached (High% >= entry_pct)
        if row['High%'] < entry_pct:
            return {'entry_filled': False, 'exit_reason': 'NO_ENTRY', 'return_pct': 0,
                    'entry_price': entry_price, 'exit_price': np.nan}
        entry_filled = True

    # Entry was reached, now check for TP, SL, or time exit

    # Calculate TP and SL prices
    # TP is negative (below entry), so: entry_price * (1 + tp/100)
    # SL is positive (above entry), so: entry_price * (1 + sl/100)
    tp_price = entry_price * (1 + strategy['tp'] / 100)
    sl_price = entry_price * (1 + strategy['sl'] / 100)

    # Check if TP was hit (Low reached tp_price)
    # TP is below entry, so we need Low to go low enough
    intraday_low_price = open_price * (1 + row['Low%'] / 100)
    tp_hit = intraday_low_price <= tp_price

    # Check if SL was hit (High reached sl_price after entry)
    # SL is above entry
    intraday_high_price = open_price * (1 + row['High%'] / 100)
    sl_hit = intraday_high_price >= sl_price

    # Determine exit (priority: SL > TP > TIME)
    # In reality, whichever hit first would trigger, but we don't have intraday sequencing
    # Conservative assumption: if both hit, SL triggered first (worst case)

    if sl_hit:
        # Stop loss hit
        exit_price = sl_price
        exit_reason = 'SL'
        return_pct = ((entry_price - exit_price) / entry_price) * 100  # For short: profit when price drops
        return_pct = -return_pct  # SL means loss
    elif tp_hit:
        # Take profit hit
        exit_price = tp_price
        exit_reason = 'TP'
        return_pct = ((entry_price - exit_price) / entry_price) * 100  # For short
    else:
        # Neither hit, exit at time
        exit_price = get_exit_price(row, strategy['exit_time'])
        if pd.isna(exit_price):
            exit_price = open_price * (1 + row['Close vs Open%'] / 100)
        exit_reason = 'TIME'
        return_pct = ((entry_price - exit_price) / entry_price) * 100

    return {
        'entry_filled': True,
        'exit_reason': exit_reason,
        'return_pct': return_pct,
        'entry_price': entry_price,
        'exit_price': exit_price
    }

def calculate_max_drawdown(equity_curve):
    """Calculate maximum drawdown from equity curve"""
    cummax = equity_curve.cummax()
    drawdown = (equity_curve - cummax) / cummax * 100
    return drawdown.min()

def calculate_sharpe_ratio(returns, risk_free_rate=0):
    """Calculate Sharpe ratio"""
    if len(returns) == 0 or returns.std() == 0:
        return 0
    return (returns.mean() - risk_free_rate) / returns.std()

def backtest_strategy(df, strategy_name, strategy):
    """
    Backtest a single strategy

    Returns dict with performance metrics
    """

    print(f"\nBacktesting {strategy_name}...")
    print(f"  Entry: {strategy['entry']}%, SL: +{strategy['sl']}%, TP: {strategy['tp']}%")
    print(f"  Position Size: {strategy['position_size']}%, Exit: {strategy['exit_time']}")

    trades = []

    for idx, row in df.iterrows():
        trade = simulate_trade(row, strategy)
        trades.append(trade)

    trades_df = pd.DataFrame(trades)

    # Filter to filled trades only
    filled_trades = trades_df[trades_df['entry_filled']].copy()

    if len(filled_trades) == 0:
        print(f"  WARNING: No trades filled!")
        return {
            'strategy': strategy_name,
            'total_trades': 0,
            'filled_trades': 0,
            'win_rate': 0,
            'win_ratio': 0,
            'avg_return': 0,
            'total_return': 0,
            'sharpe_ratio': 0,
            'max_drawdown': 0,
            'tp_exits': 0,
            'sl_exits': 0,
            'time_exits': 0
        }

    # Calculate metrics
    winning_trades = filled_trades[filled_trades['return_pct'] > 0]
    losing_trades = filled_trades[filled_trades['return_pct'] <= 0]

    win_rate = len(winning_trades) / len(filled_trades) * 100

    avg_win = winning_trades['return_pct'].mean() if len(winning_trades) > 0 else 0
    avg_loss = abs(losing_trades['return_pct'].mean()) if len(losing_trades) > 0 else 0
    win_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    avg_return = filled_trades['return_pct'].mean()

    # Account for position sizing in returns
    # Each trade uses position_size% of account
    # Return on account = trade_return * position_size
    filled_trades['account_return'] = filled_trades['return_pct'] * (strategy['position_size'] / 100)

    # Calculate equity curve (assuming starting with $10,000)
    starting_capital = 10000
    filled_trades['equity'] = starting_capital * (1 + filled_trades['account_return'] / 100).cumprod()

    total_return_pct = (filled_trades['equity'].iloc[-1] - starting_capital) / starting_capital * 100

    max_drawdown = calculate_max_drawdown(filled_trades['equity'])
    sharpe_ratio = calculate_sharpe_ratio(filled_trades['account_return'])

    # Exit reason breakdown
    tp_exits = (filled_trades['exit_reason'] == 'TP').sum()
    sl_exits = (filled_trades['exit_reason'] == 'SL').sum()
    time_exits = (filled_trades['exit_reason'] == 'TIME').sum()

    print(f"  Filled Trades: {len(filled_trades)} ({len(filled_trades)/len(df)*100:.1f}%)")
    print(f"  Win Rate: {win_rate:.1f}%")
    print(f"  Avg Return: {avg_return:.2f}%")
    print(f"  Total Return: {total_return_pct:.2f}%")
    print(f"  Sharpe Ratio: {sharpe_ratio:.3f}")
    print(f"  Max Drawdown: {max_drawdown:.2f}%")
    print(f"  Exits: TP={tp_exits}, SL={sl_exits}, TIME={time_exits}")

    return {
        'strategy': strategy_name,
        'entry': strategy['entry'],
        'sl': strategy['sl'],
        'tp': strategy['tp'],
        'position_size': strategy['position_size'],
        'exit_time': strategy['exit_time'],
        'total_trades': len(df),
        'filled_trades': len(filled_trades),
        'fill_rate': len(filled_trades) / len(df) * 100,
        'win_rate': win_rate,
        'win_ratio': win_ratio,
        'avg_return_per_trade': avg_return,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_return': total_return_pct,
        'sharpe_ratio': sharpe_ratio,
        'max_drawdown': max_drawdown,
        'tp_exits': tp_exits,
        'sl_exits': sl_exits,
        'time_exits': time_exits
    }

# Backtest all strategies
print("\n" + "=" * 80)
print("BACKTESTING ALL STRATEGIES")
print("=" * 80)

results = []

for strategy_name, strategy_params in strategies.items():
    result = backtest_strategy(df, strategy_name, strategy_params)
    results.append(result)

# Create results DataFrame
results_df = pd.DataFrame(results)

# Sort by Sharpe Ratio (descending)
results_df = results_df.sort_values('sharpe_ratio', ascending=False)

print("\n" + "=" * 80)
print("RESULTS SUMMARY")
print("=" * 80)

print("\nTop 5 Strategies by Sharpe Ratio:")
print(results_df.head()[['strategy', 'sharpe_ratio', 'total_return', 'win_rate', 'max_drawdown']].to_string(index=False))

print("\nTop 5 Strategies by Total Return:")
top_return = results_df.sort_values('total_return', ascending=False).head()
print(top_return[['strategy', 'total_return', 'sharpe_ratio', 'win_rate', 'max_drawdown']].to_string(index=False))

print("\nTop 5 Strategies by Win Rate:")
top_winrate = results_df.sort_values('win_rate', ascending=False).head()
print(top_winrate[['strategy', 'win_rate', 'total_return', 'sharpe_ratio', 'max_drawdown']].to_string(index=False))

# Save results
results_df.to_csv('martin_strategies_backtest_results.csv', index=False)
print("\n" + "=" * 80)
print("Results saved to: martin_strategies_backtest_results.csv")
print("=" * 80)
