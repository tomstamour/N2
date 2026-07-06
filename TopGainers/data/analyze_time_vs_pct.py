
import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings('ignore')

def time_to_minutes(time_str):
    """Convert HH:MM time string to minutes from 09:30"""
    try:
        h, m = map(int, time_str.split(':'))
        return (h - 9) * 60 + (m - 30)
    except:
        return np.nan

def load_and_process_data(filepath):
    df = pd.read_csv(filepath)
    
    # Generate list of 5-minute bar time periods
    time_periods = []
    for hour in range(9, 16):
        for minute in range(0, 60, 5):
            if hour == 9 and minute < 30:
                continue
            if hour == 15 and minute > 55:
                continue
            time_periods.append(f"{hour:02d}:{minute:02d}")
            
    return df, time_periods

def simulate_percentage_based_entry(df, entry_pct):
    """
    Simulate percentage-based entry strategy with limit orders
    """
    results = df.copy()

    # Entry price = 09:30 open * (1 + entry_pct/100)
    results['Entry_Price'] = df['09:30_O'] * (1 + entry_pct / 100)
    
    # Entry filled = True if High% >= entry_pct
    results['Entry_Filled'] = df['High%'] >= entry_pct

    # Calculate returns from entry to intraday low (for filled orders only)
    # Note: We use the Low% of the day. 
    # Conservative assumption: If we fill, we catch the move to the day's low?
    # Ideally we check if Low occurred after High, but High% and Low% columns don't have timestamps attached in a way that guarantees sequence for *every* bar.
    # However, 'Time Low' and 'Time High' are available.
    # If Time Low < Time Entry, then we missed the low? 
    # But for Limit Order at 9:30, we are "active" from start. 
    # If Fill happens at 10:00, and Low was at 9:45, we missed it.
    
    # Let's check Time High vs Time Low for better accuracy if possible, 
    # but the prompt implies simple "Price drop after".
    # Existing script logic:
    intraday_low_price = df['09:30_O'] * (1 + df['Low%'] / 100)
    
    # Refined Logic: Check if Low occurred AFTER the fill.
    # We estimate fill time by finding first bar > limit. 
    
    results['Best_Return_Pct'] = np.nan
    results['Worst_Excursion_Pct'] = np.nan
    
    # We need to iterate to be accurate about timing
    # Vectorizing "first bar > X" is hard, using loop for simplicity/correctness
    
    returns = []
    waes = []
    
    # Pre-calculate column indices for speed
    cols = results.columns
    
    # We need the time columns to find fill time
    # This is computationally expensive to do full scan, let's use the simplified logic 
    # from the original script but add a check for Time Low.
    
    # Simplified approximation matching previous script for consistency, 
    # but let's try to be slightly smarter about Time Low.
    
    # Convert 'Time Low' to comparable integer
    results['Time_Low_Minutes'] = results['Time Low'].apply(time_to_minutes)
    
    for idx, row in results.iterrows():
        if not row['Entry_Filled']:
            returns.append(np.nan)
            waes.append(np.nan)
            continue
            
        entry_price = row['Entry_Price']
        
        # Find fill time
        fill_time_idx = -1
        # columns like 09:30_H, 09:35_H ...
        # Check 5 min bars
        bars = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(0, 60, 5) if not (h==9 and m<30) and not (h==15 and m>55)]
        
        found_fill = False
        fill_period = ""
        
        for i, period in enumerate(bars):
            h_col = f"{period}_H"
            if h_col in row and row[h_col] >= entry_price:
                fill_time_idx = i
                fill_period = period
                found_fill = True
                break
        
        if not found_fill:
            # Should not happen if Entry_Filled is True based on Day High, 
            # unless Day High happened in a bar we aren't checking (e.g. pre/post? dataset seems 9:30-15:55)
            # Fallback to day stats
            returns.append(((entry_price - intraday_low_price[idx]) / entry_price) * 100)
            waes.append((( (row['09:30_O'] * (1 + row['High%']/100)) - entry_price) / entry_price) * 100)
            continue
            
        # Now find lowest Low AFTER fill_time
        # Check all bars after fill_time_idx
        min_price_after_fill = entry_price # Start at entry
        max_price_after_fill = entry_price
        
        # We need to look at Lows and Highs of subsequent bars
        # Optimization: Use the columns directly
        # constructing list of values for this row is slow but safe
        
        # Slicing is faster?
        # Let's just use the Day Low if Time Low > Fill Time
        # The dataset has 'Time Low'.
        
        time_low_mins = row['Time_Low_Minutes']
        fill_time_mins = time_to_minutes(fill_period)
        
        if pd.notna(time_low_mins) and pd.notna(fill_time_mins) and time_low_mins >= fill_time_mins:
            # The day's low happened after entry
            best_low = row['09:30_O'] * (1 + row['Low%']/100)
        else:
            # The day's low happened before entry. We need to find the low *after* entry.
            # Scan subsequent bars
            current_lows = []
            current_highs = []
            for p in bars[fill_time_idx:]:
                if f"{p}_L" in row:
                    current_lows.append(row[f"{p}_L"])
                if f"{p}_H" in row:
                    current_highs.append(row[f"{p}_H"])
            
            if current_lows:
                best_low = min(current_lows)
            else:
                best_low = entry_price # No subsequent bars? (End of day)

        # WAE: Max High after fill
        # Similar logic
        if pd.notna(fill_time_mins):
             # Scan subsequent bars for High
            current_highs = []
            for p in bars[fill_time_idx:]:
                 if f"{p}_H" in row:
                    current_highs.append(row[f"{p}_H"])
            if current_highs:
                max_high = max(current_highs)
            else:
                max_high = entry_price
        else:
             max_high = row['09:30_O'] * (1 + row['High%']/100)
             
        ret = ((entry_price - best_low) / entry_price) * 100
        wae = ((max_high - entry_price) / entry_price) * 100
        returns.append(ret)
        waes.append(wae)

    results['Best_Return_Pct'] = returns
    results['Worst_Excursion_Pct'] = waes
    
    return results

def simulate_time_based_entry(df, entry_time):
    """
    Simulate entering at Market Open of a specific 5-min bar
    """
    results = df.copy()
    col_open = f"{entry_time}_O"
    
    if col_open not in df.columns:
        return None
        
    results['Entry_Price'] = df[col_open]
    results['Entry_Filled'] = True # Market order always fills (theoretically)
    
    # Calculate returns
    # We need Low AFTER entry time
    returns = []
    waes = []
    
    bars = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(0, 60, 5) if not (h==9 and m<30) and not (h==15 and m>55)]
    
    try:
        start_idx = bars.index(entry_time)
    except ValueError:
        return None
        
    for idx, row in results.iterrows():
        entry_price = row['Entry_Price']
        
        # Scan bars starting from this time (inclusive of this bar's low? Yes, if we enter at Open)
        current_lows = []
        current_highs = []
        
        for p in bars[start_idx:]:
            if f"{p}_L" in row:
                current_lows.append(row[f"{p}_L"])
            if f"{p}_H" in row:
                current_highs.append(row[f"{p}_H"])
        
        if current_lows:
            best_low = min(current_lows)
        else:
            best_low = entry_price
            
        if current_highs:
            max_high = max(current_highs)
        else:
            max_high = entry_price
            
        ret = ((entry_price - best_low) / entry_price) * 100
        wae = ((max_high - entry_price) / entry_price) * 100
        
        returns.append(ret)
        waes.append(wae)
        
    results['Best_Return_Pct'] = returns
    results['Worst_Excursion_Pct'] = waes
    
    return results

def main():
    data_file = '/home/tom/Documents/ibkr_scripts/GUS3/data/gus_stats_ibkr.txt'
    df, time_periods = load_and_process_data(data_file)
    
    stats_summary = []
    
    # 1. Evaluate Percentage Based Strategies
    pct_levels = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    
    print("Evaluating Percentage Based Entries...")
    for pct in pct_levels:
        res = simulate_percentage_based_entry(df, pct)
        filled = res[res['Entry_Filled']]
        
        fill_rate = len(filled) / len(res) * 100
        
        if len(filled) > 0:
            avg_ret = filled['Best_Return_Pct'].mean()
            median_ret = filled['Best_Return_Pct'].median()
            std_ret = filled['Best_Return_Pct'].std()
            sharpe = avg_ret / std_ret if std_ret > 0 else 0
            
            # Expected Return (account for non-fills as 0)
            exp_ret = res['Best_Return_Pct'].fillna(0).mean()
            
            stats_summary.append({
                'Type': 'Percentage',
                'Parameter': f"+{pct}%",
                'Fill_Rate': fill_rate,
                'Exp_Return': exp_ret,
                'Cond_Return': avg_ret,
                'Cond_Sharpe': sharpe,
                'Risk_WAE': filled['Worst_Excursion_Pct'].mean()
            })
            
    # 2. Evaluate Time Based Strategies
    # Check every 5 mins from 09:30 to 10:30 (most relevant for morning)
    time_check_periods = [f"{h:02d}:{m:02d}" for h in range(9, 11) for m in range(0, 60, 5) if not (h==9 and m<30) and not (h==10 and m>30)] 
    # Up to 10:30
    
    print("Evaluating Time Based Entries...")
    for time_str in time_check_periods:
        res = simulate_time_based_entry(df, time_str)
        if res is not None:
             # Fill rate is 100%
            avg_ret = res['Best_Return_Pct'].mean()
            std_ret = res['Best_Return_Pct'].std()
            sharpe = avg_ret / std_ret if std_ret > 0 else 0
            
            stats_summary.append({
                'Type': 'Time',
                'Parameter': time_str,
                'Fill_Rate': 100.0,
                'Exp_Return': avg_ret, # Same as Cond for 100% fill
                'Cond_Return': avg_ret,
                'Cond_Sharpe': sharpe,
                'Risk_WAE': res['Worst_Excursion_Pct'].mean()
            })

    # Output Results
    summary_df = pd.DataFrame(stats_summary)
    
    # Sort by Exp_Return
    print("\n--- TOP STRATEGIES BY EXPECTED RETURN ---")
    print(summary_df.sort_values('Exp_Return', ascending=False).head(10).to_string(index=False))
    
    print("\n--- TOP STRATEGIES BY SHARPE RATIO ---")
    print(summary_df.sort_values('Cond_Sharpe', ascending=False).head(10).to_string(index=False))
    
    summary_df.to_csv('comparison_results.csv', index=False)

if __name__ == "__main__":
    main()
