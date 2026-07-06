import pandas as pd
import numpy as np
import re

# Load Data
def load_data(filepath):
    # TBS seems to be tab or comma separated. The preview showed tabs/spaces?
    # Let's check the delimiter from the read_file output. 
    # The output looked like: "Date\tTicker\tGap%..."
    try:
        df = pd.read_csv(filepath, sep='\t')
        if len(df.columns) < 5: # Fallback if not tab
            df = pd.read_csv(filepath, sep=',')
    except:
        df = pd.read_csv(filepath, sep=None, engine='python')
    
    return df

def parse_strategies(filepath):
    strategies = []
    with open(filepath, 'r') as f:
        lines = f.readlines()
    
    # Skip header
    start_idx = 0
    for i, line in enumerate(lines):
        if line.startswith('---'):
            start_idx = i + 1
            break
            
    for line in lines[start_idx:]:
        line = line.strip()
        if not line or line.startswith('*'):
            continue
            
        # Try tab split first
        parts = [p.strip() for p in line.split('\t') if p.strip()]
        
        # If not enough parts, try splitting by 2 or more spaces
        if len(parts) < 4:
             parts = [p.strip() for p in re.split(r'\s{2,}', line) if p.strip()]
             
        if len(parts) < 4:
            print(f"DEBUG: Skipping line (not enough parts): {line}")
            continue
            
        name = parts[0]
        desc = parts[1]
        pos_size_str = parts[2].replace('%', '')
        exit_rule = parts[3]
        
        # Parse Description for Entry, SL, TP
        # Regex to prioritize Time (ending in h) or Percentage (ending in %)
        entry_match = re.search(r'Entry\s+([\d:]+h|[\+\-]?\d*\.?\d+%)', desc)
        sl_match = re.search(r'SL\s+([\+\-]?\d*\.?\d+%)', desc)
        tp_match = re.search(r'TP\s+([\+\-]?\d*\.?\d+%)', desc)
        
        entry_val = entry_match.group(1) if entry_match else "0%"
        sl_val = float(sl_match.group(1).replace('%', ''))/100 if sl_match else 0.0
        tp_val = float(tp_match.group(1).replace('%', ''))/100 if tp_match else 0.0
        
        pos_size = float(pos_size_str)/100
        
        strategies.append({
            'name': name,
            'entry_desc': entry_val,
            'sl': sl_val,
            'tp': abs(tp_val), # Treat TP as absolute percentage drop for Short
            'pos_size': pos_size,
            'exit_time': exit_rule
        })
    return strategies

def get_price(row, time_str, type='O'):
    # time_str format "HH:MM". type in ['O', 'H', 'L', 'C', 'V']
    col_name = f"{time_str}_{type}"
    if col_name in row:
        return row[col_name]
    return None

def backtest_strategy(df, strategy):
    trades = []
    
    # Time columns mapping
    time_cols = [c for c in df.columns if re.match(r'\d{2}:\d{2}_[OHLC]', c)]
    times = sorted(list(set([c.split('_')[0] for c in time_cols])))
    
    for idx, row in df.iterrows():
        open_0930 = row['09:30_O']
        
        # Determine Entry
        entry_price = 0.0
        entry_time_idx = -1
        
        if 'h' in strategy['entry_desc']: # Time based entry
            entry_time_str = strategy['entry_desc'].replace('h', '')
            if len(entry_time_str) == 4: # 9:45 -> 09:45
                entry_time_str = '0' + entry_time_str
            
            p = get_price(row, entry_time_str, 'O')
            if p is not None:
                entry_price = p
                if entry_time_str in times:
                    entry_time_idx = times.index(entry_time_str)
        else: # Limit entry
            pct = float(strategy['entry_desc'].replace('%', '')) / 100
            limit_price = open_0930 * (1 + pct)
            
            # Check if triggered
            # Iterate through all times to find first High >= limit_price
            for i, t in enumerate(times):
                h = get_price(row, t, 'H')
                if h is not None and h >= limit_price:
                    entry_price = limit_price # Limit filled at limit price
                    entry_time_idx = i
                    break
        
        if entry_price == 0.0 or entry_time_idx == -1:
            continue # No trade
            
        # Trade Management
        sl_price = entry_price * (1 + strategy['sl'])
        tp_price = entry_price * (1 - strategy['tp'])
        
        exit_price = 0.0
        result = 'OPEN'
        
        # Determine Exit Time Index
        exit_time_str = strategy['exit_time']
        if exit_time_str == 'EOD':
            exit_time_idx = len(times) - 1 # Last candle
        else:
            ts = exit_time_str.replace('h', '')
            if len(ts) == 4: ts = '0' + ts
            if ts in times:
                exit_time_idx = times.index(ts)
            else:
                exit_time_idx = len(times) - 1 # Default to EOD if time not found
        
        # Determine Loop Range and Exit Price Type based on Strategy
        loop_end_idx = exit_time_idx
        exit_price_type = 'O' # Default for time-based
        
        if strategy['exit_time'] == 'EOD':
            loop_end_idx = exit_time_idx + 1 # Include last bar for SL/TP
            exit_price_type = 'C' # Exit at Close
        else:
            loop_end_idx = exit_time_idx # Stop BEFORE exit bar
            exit_price_type = 'O' # Exit at Open of exit bar

        # Simulation loop
        for i in range(entry_time_idx, loop_end_idx):
            t = times[i]
            # Get candle High/Low
            # For the entry candle, be careful? 
            # If Limit Entry, we assume entry happened. 
            # Should we check High/Low for SL/TP in the same candle? 
            # Conservative: Yes.
            
            curr_h = get_price(row, t, 'H')
            curr_l = get_price(row, t, 'L')
            curr_o = get_price(row, t, 'O') # Use open for gap checks
            
            if curr_h is None or curr_l is None:
                continue

            # Check SL (Short: Stop if Price goes UP)
            if curr_h >= sl_price:
                # Slippage/Gap check: If Open > SL, we exit at Open
                # If Open < SL but High > SL, we exit at SL
                if curr_o > sl_price:
                     exit_price = curr_o
                else:
                     exit_price = sl_price
                result = 'SL'
                break
            
            # Check TP (Short: Profit if Price goes DOWN)
            if curr_l <= tp_price:
                # Gap check
                if curr_o < tp_price:
                    exit_price = curr_o
                else:
                    exit_price = tp_price
                result = 'TP'
                break
                
        if result == 'OPEN':
            # Time Exit
            final_t = times[exit_time_idx]
            price = get_price(row, final_t, exit_price_type)
            if price is not None:
                exit_price = price
                result = 'TIME'
            else:
                # Data missing, force close at last available?
                exit_price = entry_price # Breakeven / Invalid
        
        # Calculate PnL (Short)
        # (Entry - Exit) / Entry
        pnl = (entry_price - exit_price) / entry_price
        trades.append({
            'Date': row['Date'],
            'Strategy': strategy['name'],
            'Entry': entry_price,
            'Exit': exit_price,
            'PnL': pnl,
            'PosSize': strategy['pos_size'],
            'Result': result
        })
        
    return pd.DataFrame(trades)

def calculate_stats(trades_df):
    if trades_df.empty:
        return {
            'Win Rate': 0,
            'Max Drawdown': 0,
            'Win Ratio': 0,
            'Sharpe Ratio': 0
        }
        
    # Win Rate
    wins = trades_df[trades_df['PnL'] > 0]
    win_rate = len(wins) / len(trades_df) * 100
    
    # Win Ratio (Avg Win / Avg Loss)
    losses = trades_df[trades_df['PnL'] <= 0]
    avg_win = wins['PnL'].mean() if not wins.empty else 0
    avg_loss = abs(losses['PnL'].mean()) if not losses.empty else 0
    win_ratio = avg_win / avg_loss if avg_loss != 0 else 0
    
    # Max Drawdown & Sharpe
    # Using Position Size scaled returns
    trades_df['TradeReturn'] = trades_df['PnL'] * trades_df['PosSize']
    
    # Sort by date
    trades_df = trades_df.sort_values('Date')
    
    # Daily Returns (Sum of Trade Returns for the day)
    daily_returns = trades_df.groupby('Date')['TradeReturn'].sum()
    
    # Equity Curve (Starting 1.0)
    # Using simple returns: Equity_t = Equity_{t-1} * (1 + r_t)
    equity = [1.0]
    for r in daily_returns:
        equity.append(equity[-1] * (1 + r))
    
    equity = np.array(equity)
    peaks = np.maximum.accumulate(equity)
    drawdowns = (equity - peaks) / peaks
    max_dd = drawdowns.min() * 100 # Percentage
    
    # Sharpe Ratio
    if daily_returns.std() != 0:
        sharpe = (daily_returns.mean() / daily_returns.std()) * np.sqrt(252)
    else:
        sharpe = 0
        
    return {
        'Win Rate': win_rate,
        'Max Drawdown': max_dd,
        'Win Ratio': win_ratio,
        'Sharpe Ratio': sharpe
    }

def main():
    data_file = 'gus_stats_ibkr.tbs'
    strat_file = 'martin_strategies_gus.txt'
    
    df = load_data(data_file)
    strategies = parse_strategies(strat_file)
    print(f"DEBUG: Parsed {len(strategies)} strategies.")
    if len(strategies) > 0:
        print(f"DEBUG: First strategy: {strategies[0]}")
    else:
        print("DEBUG: No strategies parsed! Check file format.")
    
    results = []
    
    for strat in strategies:
        print(f"Backtesting {strat['name']}...")
        trades = backtest_strategy(df, strat)
        stats = calculate_stats(trades)
        stats['Strategy'] = strat['name']
        stats['Description'] = f"E:{strat['entry_desc']} SL:{strat['sl']:.0%} TP:{strat['tp']:.0%} {strat['exit_time']}"
        results.append(stats)
        
    results_df = pd.DataFrame(results)
    print(f"DEBUG: Results DataFrame columns: {results_df.columns}")
    
    # Format for Markdown
    results_df = results_df[['Strategy', 'Description', 'Win Rate', 'Max Drawdown', 'Win Ratio', 'Sharpe Ratio']]
    
    markdown = results_df.to_markdown(index=False, floatfmt=".2f")
    
    with open('strategies_martin_report_Genmini.md', 'w') as f:
        f.write("# Martin Strategies Backtest Report\n\n")
        f.write(f"Dataset: {data_file}\n")
        f.write(f"Number of records: {len(df)}\n\n")
        f.write("## Strategy Performance\n\n")
        f.write(markdown)
        f.write("\n\n")
        f.write("### Definitions\n")
        f.write("- **Win Rate**: Percentage of trades with PnL > 0.\n")
        f.write("- **Max Drawdown**: Maximum peak-to-valley percentage decline in the equity curve (compounded daily returns).\n")
        f.write("- **Win Ratio**: Average Win % / Average Loss %.\n")
        f.write("- **Sharpe Ratio**: Annualized Sharpe Ratio (assuming 252 trading days) based on daily returns.\n")

    print("Report generated: strategies_martin_report_Genmini.md")

if __name__ == "__main__":
    main()
