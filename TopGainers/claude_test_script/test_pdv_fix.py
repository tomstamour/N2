#!/usr/bin/env python3
"""
Test PDV calculator date range fix.
Verifies that fetching 20 calendar days provides sufficient trading days.
"""

import datetime
import yfinance as yf

def test_pdv_date_range():
    """Test that 20 calendar days gives us at least 11 trading days."""
    
    # Use the problematic date from the log
    date_a = datetime.date(2025, 12, 1)  # Sunday - non-trading day
    
    # New calculation (20 calendar days)
    start_date = date_a - datetime.timedelta(days=20)  # 2025-11-11
    end_date = date_a + datetime.timedelta(days=1)     # 2025-12-02
    
    print(f"Test Date-A: {date_a} (Sunday - non-trading)")
    print(f"Fetching from {start_date} to {end_date} ({(end_date - start_date).days} calendar days)")
    print()
    
    # Test with a real stock (AAPL - always has data)
    print("Fetching historical data from yfinance for AAPL...")
    ticker = yf.Ticker("AAPL")
    history = ticker.history(
        start=start_date.strftime('%Y-%m-%d'),
        end=end_date.strftime('%Y-%m-%d'),
        interval='1d',
        actions=False
    )
    
    trading_days = len(history)
    print(f"Trading days returned: {trading_days}")
    print(f"Date range in data: {history.index[0].date()} to {history.index[-1].date()}")
    print()
    
    # Validate we have enough data
    if trading_days >= 11:
        print("✓ SUCCESS: Got sufficient trading days ({} >= 11)".format(trading_days))
        
        # Show the calculation would work
        date_a_volume = int(history.iloc[-1]['Volume'])
        prior_volumes = history.iloc[-11:-1]['Volume'].tolist()
        avg_volume = sum(prior_volumes) / 10.0
        xvol = date_a_volume / avg_volume
        
        print(f"\nExample calculation:")
        print(f"  Date-A volume: {date_a_volume:,}")
        print(f"  10-day avg: {avg_volume:,.0f}")
        print(f"  xVol ratio: {xvol:.2f}")
        return True
    else:
        print("✗ FAILED: Insufficient trading days ({} < 11)".format(trading_days))
        return False

if __name__ == "__main__":
    success = test_pdv_date_range()
    exit(0 if success else 1)
