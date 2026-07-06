
import pandas as pd
import numpy as np

def analyze_stop_losses():
    # Load data
    df = pd.read_csv('/home/tom/Documents/ibkr_scripts/GUS3/data/gus_stats_ibkr.txt')
    
    # Drop NaNs in critical columns to avoid calculation errors
    df = df.dropna(subset=['High%', 'Low%', 'Close vs Open%'])
    
    # Constants for Simulation
    ENTRY_PCT = 4.0   # +4% above Open
    TP_PCT = -15.0    # -15% below Open (Target)
    
    # Stop Loss range to test: 15% to 100% in 5% increments
    sl_levels = range(15, 105, 5)
    
    results = []
    
    print(f"Simulating Strategy: Entry +{ENTRY_PCT}%, Target {TP_PCT}%")
    print(f"{ 'SL Level':<10} | {'Net Return':<12} | {'Win Rate':<10} | {'Stop Rate':<10} | {'False Stops':<12} | {'Saved Crashes':<12}")
    print("-" * 80)
    
    for sl in sl_levels:
        # Counters
        total_pnl = 0.0
        trades_count = 0
        wins = 0
        stops = 0
        false_stops = 0 # Stopped out, but would have hit TP
        saved_crashes = 0 # Stopped out, and stock closed HIGHER than SL (saved big loss)
        
        trade_returns = []
        
        for idx, row in df.iterrows():
            high_pct = row['High%']
            low_pct = row['Low%']
            close_pct = row['Close vs Open%']
            
            # 1. Check Entry
            if high_pct < ENTRY_PCT:
                continue # No trade
            
            trades_count += 1
            
            # 2. Check Stop Loss (Conservative: If High touches SL, we are out)
            if high_pct >= sl:
                # STOPPED OUT
                pnl = ENTRY_PCT - sl # Loss (e.g. 4 - 30 = -26%)
                stops += 1
                
                # Analyze "What if?"
                if low_pct <= TP_PCT:
                    false_stops += 1 # We stopped out, but it eventually went to target
                elif close_pct > sl:
                    saved_crashes += 1 # We stopped out, and price ended even higher (Good stop!)
                
            # 3. Check Take Profit
            elif low_pct <= TP_PCT:
                # TARGET HIT
                pnl = ENTRY_PCT - TP_PCT # Win (e.g. 4 - (-15) = +19%)
                wins += 1
                
            # 4. End of Day Exit
            else:
                pnl = ENTRY_PCT - close_pct
                if pnl > 0:
                    wins += 1 # Technical win, though maybe small
            
            total_pnl += pnl
            trade_returns.append(pnl)
            
        # Metrics
        avg_return = total_pnl / trades_count if trades_count > 0 else 0
        win_rate = (wins / trades_count) * 100 if trades_count > 0 else 0
        stop_rate = (stops / trades_count) * 100 if trades_count > 0 else 0
        false_stop_pct = (false_stops / stops) * 100 if stops > 0 else 0
        
        results.append({
            'SL_Pct': sl,
            'Avg_Return': avg_return,
            'Win_Rate': win_rate,
            'Stop_Rate': stop_rate,
            'False_Stop_Ratio': false_stop_pct,
            'Total_PnL_Points': total_pnl # Sum of % returns
        })
        
        print(f"+{sl:<9}% | {avg_return:<11.2f}% | {win_rate:<9.1f}% | {stop_rate:<9.1f}% | {false_stop_pct:<11.1f}% | {saved_crashes}")

    # Find Optimal
    results_df = pd.DataFrame(results)
    best_sl = results_df.loc[results_df['Avg_Return'].idxmax()]
    
    print("\n" + "="*80)
    print(f"OPTIMAL STOP LOSS: +{best_sl['SL_Pct']:.0f}% above Open")
    print(f"Expected Return per Trade: {best_sl['Avg_Return']:.2f}%")
    print(f"Stop Out Rate: {best_sl['Stop_Rate']:.1f}%")
    print("="*80)
    
    # Additional Context: Max Drawdown Analysis for the "Best" vs "Tight" vs "Loose"
    # (Simplified to worst single trade loss)
    
if __name__ == "__main__":
    analyze_stop_losses()
