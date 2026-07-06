
import pandas as pd
import numpy as np

def time_to_minutes(time_str):
    """Convert HH:MM time string to minutes from 09:30"""
    try:
        h, m = map(int, time_str.split(':'))
        return (h - 9) * 60 + (m - 30)
    except:
        return np.nan

def analyze_take_profit():
    # Load data
    df = pd.read_csv('/home/tom/Documents/ibkr_scripts/GUS3/data/gus_stats_ibkr.txt')
    # Clean data
    df = df.dropna(subset=['High%', 'Low%', 'Close vs Open%'])
    
    # Constants based on previous findings
    ENTRY_PCT = 4.0      # Limit Sell at +4%
    STOP_LOSS_PCT = 60.0 # Market Buy at +60%
    
    print(f"Base Strategy: Entry +{ENTRY_PCT}%, Stop Loss +{STOP_LOSS_PCT}%")
    
    # ---------------------------------------------------------
    # PART 1: PRICE-BASED TAKE PROFIT (Limit Buy orders)
    # ---------------------------------------------------------
    tp_levels = range(5, 65, 5) # Test -5%, -10% ... -60%
    
    price_results = []
    
    for tp_drop in tp_levels:
        tp_target = -tp_drop # e.g. -5, -10
        
        # Counters
        total_pnl = 0.0
        trades_count = 0
        wins = 0
        losses = 0
        
        for idx, row in df.iterrows():
            high_pct = row['High%']
            low_pct = row['Low%']
            close_pct = row['Close vs Open%']
            
            # 1. Entry Check
            if high_pct < ENTRY_PCT:
                continue # No trade
                
            trades_count += 1
            
            # 2. Outcome Determination
            # We need to know what happened first: Stop Loss or Take Profit?
            # Approximation logic:
            # - If High >= SL, did it happen BEFORE Low <= TP?
            #   - We use 'Time High' and 'Time Low' to estimate.
            
            hit_sl = high_pct >= STOP_LOSS_PCT
            hit_tp = low_pct <= tp_target
            
            pnl = 0.0
            
            if hit_sl and not hit_tp:
                # Only hit SL
                pnl = ENTRY_PCT - STOP_LOSS_PCT
                losses += 1
            elif hit_tp and not hit_sl:
                # Only hit TP
                pnl = ENTRY_PCT - tp_target
                wins += 1
            elif hit_sl and hit_tp:
                # Hit BOTH. Conflict. Check times.
                # Assuming Time format HH:MM
                time_high = row['Time High']
                time_low = row['Time Low']
                
                t_high_mins = time_to_minutes(time_high)
                t_low_mins = time_to_minutes(time_low)
                
                if pd.notna(t_high_mins) and pd.notna(t_low_mins):
                    if t_high_mins < t_low_mins:
                        # Stopped out first
                        pnl = ENTRY_PCT - STOP_LOSS_PCT
                        losses += 1
                    else:
                        # Target hit first
                        pnl = ENTRY_PCT - tp_target
                        wins += 1
                else:
                    # Fallback (Conservative): Assume Stop Loss first
                    pnl = ENTRY_PCT - STOP_LOSS_PCT
                    losses += 1
            else:
                # Hit NEITHER. Exit at Close.
                pnl = ENTRY_PCT - close_pct
                if pnl > 0: wins += 1
                else: losses += 1
            
            total_pnl += pnl
            
        avg_ret = total_pnl / trades_count if trades_count > 0 else 0
        win_rate = (wins / trades_count) * 100 if trades_count > 0 else 0
        
        price_results.append({
            'Type': 'Price-Based',
            'Target': f"{tp_target}%",
            'Avg_Return': avg_ret,
            'Win_Rate': win_rate
        })

    # ---------------------------------------------------------
    # PART 2: TIME-BASED EXIT (Market Buy at Time)
    # ---------------------------------------------------------
    # We'll check 5-minute intervals from 09:35 to Close
    # Columns format: HH:MM_C (Close of the bar)
    
    time_results = []
    times_to_test = [f"{h:02d}:{m:02d}" for h in range(9, 16) for m in range(0, 60, 15) if not (h==9 and m<30) and not (h==15 and m>55)]
    times_to_test.append("15:55") # End of day
    
    for exit_time in times_to_test:
        col_close = f"{exit_time}_C"
        col_high = f"{exit_time}_H" # To check for intraday stop loss during the hold period?
        
        # Simplified simulation:
        # We hold until exit_time.
        # IF Stop Loss (+60%) is hit at ANY point before exit_time, we are out.
        # ELSE we exit at Close of exit_time bar.
        
        total_pnl = 0.0
        trades_count = 0
        wins = 0
        
        for idx, row in df.iterrows():
            high_pct = row['High%']
            
            # 1. Entry Check
            if high_pct < ENTRY_PCT:
                continue
            trades_count += 1
            
            # 2. Check Global Stop Loss First
            # Optimization: If Day High < SL, we definitely survive to exit time?
            # No, Day High could be AFTER exit time.
            # We need to check if High occurred BEFORE exit_time and was > SL.
            # OR just check Day High. If Day High >= SL, we assume we got stopped.
            # (Conservative approach).
            
            if high_pct >= STOP_LOSS_PCT:
                # We hit SL.
                # Did we hit it before exit_time?
                time_high = row['Time High']
                t_high_mins = time_to_minutes(time_high)
                t_exit_mins = time_to_minutes(exit_time)
                
                if pd.notna(t_high_mins) and t_high_mins <= t_exit_mins:
                    pnl = ENTRY_PCT - STOP_LOSS_PCT
                    total_pnl += pnl
                    continue # Next trade
            
            # 3. Exit at Time
            # We need the price at exit_time relative to Open 9:30
            # col_close is absolute price.
            # We calculate pct change manually.
            
            if col_close not in df.columns:
                # Missing data for this specific time bar on this day?
                # Fallback to Close? Or Skip?
                # Let's use 15:55_C or Close vs Open%
                exit_pct = row['Close vs Open%'] 
            else:
                price_at_exit = row[col_close]
                open_price = row['09:30_O']
                exit_pct = ((price_at_exit / open_price) - 1) * 100
                
            pnl = ENTRY_PCT - exit_pct
            total_pnl += pnl
            if pnl > 0: wins += 1
            
        avg_ret = total_pnl / trades_count if trades_count > 0 else 0
        win_rate = (wins / trades_count) * 100 if trades_count > 0 else 0
        
        time_results.append({
            'Type': 'Time-Based',
            'Target': exit_time,
            'Avg_Return': avg_ret,
            'Win_Rate': win_rate
        })

    # ---------------------------------------------------------
    # OUTPUT
    # ---------------------------------------------------------
    
    all_results = price_results + time_results
    res_df = pd.DataFrame(all_results)
    
    print("\n--- TOP 10 TAKE PROFIT STRATEGIES (By Return) ---")
    print(res_df.sort_values('Avg_Return', ascending=False).head(10).to_string(index=False))
    
    print("\n--- TOP 5 PRICE-BASED TARGETS ---")
    print(pd.DataFrame(price_results).sort_values('Avg_Return', ascending=False).head(5).to_string(index=False))
    
    print("\n--- TOP 5 TIME-BASED EXITS ---")
    print(pd.DataFrame(time_results).sort_values('Avg_Return', ascending=False).head(5).to_string(index=False))

    # Specific check for -15%
    print("\n--- REFERENCE: -15% TARGET ---")
    print(res_df[res_df['Target'] == '-15%'].to_string(index=False))

if __name__ == "__main__":
    analyze_take_profit()
