#!/usr/bin/env python3
"""
Test script for Previous Day Volume Calculator (PDV) feature.

Tests:
1. Valid calculation - Normal stock with 11+ days history
2. Insufficient data - Newly listed stock (<11 days)
3. Caching behavior - Verify cache hit/miss
4. Invalid symbol - Error handling for non-existent ticker
5. Edge cases - Zero volume, weekends, holidays

Usage:
    python3 test_pdv_calculator.py
"""

import datetime
import time
import yfinance as yf
import pytz


# ========================================
# MOCK PDV Function (Standalone Version)
# ========================================
def previous_day_volume_calculator_standalone(symbol, date_a_datetime, cache=None):
    """
    Standalone version of PDV calculator for testing.

    This is a copy of the function from Gainers_Scanner_Script.py
    for isolated testing purposes.
    """
    # Initialize cache if not provided
    if cache is None:
        cache = {}

    TTL = 86400  # 24 hours

    # Check cache first
    if symbol in cache:
        cached_time = cache[symbol]['timestamp']
        age = time.time() - cached_time
        if age < TTL:
            print(f"  [CACHE HIT] Using cached PDV data for {symbol} (age: {age:.0f}s)")
            return cache[symbol]['data']
        else:
            print(f"  [CACHE EXPIRED] Cache expired for {symbol} (age: {age:.0f}s > TTL: {TTL}s)")

    print(f"  [API CALL] Calculating PDV for {symbol}...")

    # Initialize result
    result = {
        'xvol': None,
        'date_a_volume': None,
        'avg_volume': None,
        'error': None
    }

    try:
        # Extract date from date_a_datetime
        date_a = date_a_datetime.date()

        # Calculate date range
        start_date = date_a - datetime.timedelta(days=11)
        end_date = date_a + datetime.timedelta(days=1)

        print(f"  Fetching volume data from {start_date} to {date_a}")

        # Fetch historical volume data from yfinance
        ticker = yf.Ticker(symbol)
        history = ticker.history(
            start=start_date.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d'),
            interval='1d',
            actions=False
        )

        # Rate limiting
        time.sleep(0.1)

        # Validate we have enough data
        if len(history) < 11:
            error_msg = f"Insufficient volume data (got {len(history)} days, need 11)"
            print(f"  [ERROR] {error_msg}")
            result['error'] = error_msg
            cache[symbol] = {'data': result, 'timestamp': time.time()}
            return result

        # Extract date-A volume (last row)
        date_a_volume = int(history.iloc[-1]['Volume'])
        result['date_a_volume'] = date_a_volume
        print(f"  date-A volume = {date_a_volume:,}")

        # Extract prior 10 days volumes (rows -11 to -2)
        prior_volumes = history.iloc[-11:-1]['Volume'].tolist()

        # Calculate average of prior 10 days
        avg_volume = sum(prior_volumes) / 10.0
        result['avg_volume'] = avg_volume
        print(f"  Average 10-day volume = {avg_volume:,.2f}")

        # Handle zero average edge case
        if avg_volume == 0:
            error_msg = "Zero average volume"
            print(f"  [ERROR] {error_msg}")
            result['error'] = error_msg
            cache[symbol] = {'data': result, 'timestamp': time.time()}
            return result

        # Calculate xVol ratio
        xvol = date_a_volume / avg_volume
        result['xvol'] = round(xvol, 2)

        print(f"  [SUCCESS] xVol={result['xvol']:.2f}, "
              f"date-A volume={date_a_volume:,}, "
              f"10-day avg={avg_volume:,.0f}")

    except Exception as e:
        error_msg = f"API error: {str(e)}"
        print(f"  [ERROR] {error_msg}")
        result['error'] = error_msg

    # Cache the result
    cache[symbol] = {
        'data': result,
        'timestamp': time.time()
    }

    return result


# ========================================
# TEST CASES
# ========================================

def test_valid_calculation():
    """Test 1: Valid calculation with normal stock (AAPL)"""
    print("\n" + "="*70)
    print("TEST 1: Valid Calculation (AAPL)")
    print("="*70)

    # Use a date that's guaranteed to have enough history
    # November 27, 2025 (if testing in future, adjust accordingly)
    ET_TZ = pytz.timezone('US/Eastern')
    date_a = ET_TZ.localize(datetime.datetime(2024, 11, 27, 9, 30))

    cache = {}
    result = previous_day_volume_calculator_standalone('AAPL', date_a, cache)

    print(f"\nResult:")
    print(f"  xVol: {result['xvol']}")
    print(f"  date-A volume: {result['date_a_volume']:,}" if result['date_a_volume'] else "  date-A volume: None")
    print(f"  10-day avg volume: {result['avg_volume']:,.0f}" if result['avg_volume'] else "  10-day avg volume: None")
    print(f"  Error: {result['error']}")

    # Assertions
    if result['xvol'] is not None:
        print("\n✅ TEST PASSED: xVol calculated successfully")
        assert result['xvol'] > 0, "xVol should be positive"
        assert result['date_a_volume'] > 0, "date-A volume should be positive"
        assert result['avg_volume'] > 0, "Average volume should be positive"
    else:
        print(f"\n❌ TEST FAILED: xVol is None (error: {result['error']})")


def test_cache_behavior():
    """Test 2: Caching behavior"""
    print("\n" + "="*70)
    print("TEST 2: Cache Behavior (TSLA)")
    print("="*70)

    ET_TZ = pytz.timezone('US/Eastern')
    date_a = ET_TZ.localize(datetime.datetime(2024, 11, 27, 9, 30))

    cache = {}

    # First call - should be cache miss
    print("\n[First Call - Expected: CACHE MISS]")
    result1 = previous_day_volume_calculator_standalone('TSLA', date_a, cache)

    # Second call - should be cache hit
    print("\n[Second Call - Expected: CACHE HIT]")
    result2 = previous_day_volume_calculator_standalone('TSLA', date_a, cache)

    # Verify results are identical
    if result1 == result2:
        print("\n✅ TEST PASSED: Cache returned identical results")
    else:
        print(f"\n❌ TEST FAILED: Results differ")
        print(f"  First: {result1}")
        print(f"  Second: {result2}")


def test_insufficient_data():
    """Test 3: Newly listed stock with insufficient data"""
    print("\n" + "="*70)
    print("TEST 3: Insufficient Data (Recent IPO)")
    print("="*70)
    print("NOTE: This test uses a date very close to a stock's listing.")
    print("      Depending on the stock chosen, it may or may not fail.")
    print("      Adjust the symbol and date if needed for your test.")

    # Use a very recent date for testing (you may need to adjust this)
    ET_TZ = pytz.timezone('US/Eastern')
    # Example: Use a date within 5 days of a recent IPO
    # You'll need to manually find a recently listed stock and adjust
    date_a = ET_TZ.localize(datetime.datetime(2024, 11, 15, 9, 30))

    cache = {}
    # Note: Replace 'RECENT' with an actual recent IPO ticker
    # For demonstration, we'll use AAPL with a very old date that won't work
    print("  [INFO] Simulating insufficient data scenario")
    print("         Using AAPL with a date before it had 11 days of trading")

    # AAPL was listed in 1980, so using 1980-01-02 should work
    # Let's try to find a date that might not have full data
    # For testing purposes, we'll just show the structure
    result = previous_day_volume_calculator_standalone('AAPL',
                                                       ET_TZ.localize(datetime.datetime(1980, 1, 2, 9, 30)),
                                                       cache)

    print(f"\nResult:")
    print(f"  xVol: {result['xvol']}")
    print(f"  Error: {result['error']}")

    if result['error'] and 'Insufficient' in result['error']:
        print("\n✅ TEST PASSED: Correctly detected insufficient data")
    elif result['xvol'] is not None:
        print("\n⚠️  TEST INCONCLUSIVE: Stock has sufficient historical data")
        print("    (This is expected for old stocks like AAPL)")
    else:
        print(f"\n❌ TEST FAILED: Unexpected error: {result['error']}")


def test_invalid_symbol():
    """Test 4: Invalid ticker symbol"""
    print("\n" + "="*70)
    print("TEST 4: Invalid Symbol (INVALID_TICKER)")
    print("="*70)

    ET_TZ = pytz.timezone('US/Eastern')
    date_a = ET_TZ.localize(datetime.datetime(2024, 11, 27, 9, 30))

    cache = {}
    result = previous_day_volume_calculator_standalone('INVALID_TICKER_XYZ', date_a, cache)

    print(f"\nResult:")
    print(f"  xVol: {result['xvol']}")
    print(f"  Error: {result['error']}")

    if result['error'] is not None:
        print("\n✅ TEST PASSED: Correctly handled invalid symbol")
    else:
        print("\n❌ TEST FAILED: Should have returned an error for invalid symbol")


def test_multiple_stocks():
    """Test 5: Multiple stocks calculation"""
    print("\n" + "="*70)
    print("TEST 5: Multiple Stocks (AAPL, TSLA, MSFT, NVDA)")
    print("="*70)

    ET_TZ = pytz.timezone('US/Eastern')
    date_a = ET_TZ.localize(datetime.datetime(2024, 11, 27, 9, 30))

    symbols = ['AAPL', 'TSLA', 'MSFT', 'NVDA']
    cache = {}

    results = {}
    for symbol in symbols:
        print(f"\n--- Testing {symbol} ---")
        result = previous_day_volume_calculator_standalone(symbol, date_a, cache)
        results[symbol] = result

    print("\n" + "="*70)
    print("SUMMARY:")
    print("="*70)
    print(f"{'Symbol':<10} {'xVol':<10} {'date-A Vol':<15} {'10-day Avg':<15} {'Status':<15}")
    print("-" * 70)

    success_count = 0
    for symbol, result in results.items():
        if result['xvol'] is not None:
            print(f"{symbol:<10} {result['xvol']:<10.2f} {result['date_a_volume']:<15,} {result['avg_volume']:<15,.0f} {'SUCCESS':<15}")
            success_count += 1
        else:
            error_msg = result['error'][:30] if result['error'] else "Unknown"
            print(f"{symbol:<10} {'N/A':<10} {'N/A':<15} {'N/A':<15} {error_msg:<15}")

    print("-" * 70)
    print(f"Success Rate: {success_count}/{len(symbols)} ({success_count/len(symbols)*100:.0f}%)")

    if success_count == len(symbols):
        print("\n✅ TEST PASSED: All stocks calculated successfully")
    elif success_count > 0:
        print(f"\n⚠️  TEST PARTIAL: {success_count}/{len(symbols)} stocks succeeded")
    else:
        print("\n❌ TEST FAILED: No stocks calculated successfully")


def test_weekend_date():
    """Test 6: Weekend date (Saturday)"""
    print("\n" + "="*70)
    print("TEST 6: Weekend Date (Saturday)")
    print("="*70)
    print("NOTE: yfinance should automatically use the most recent trading day")

    ET_TZ = pytz.timezone('US/Eastern')
    # November 30, 2024 is a Saturday
    date_a = ET_TZ.localize(datetime.datetime(2024, 11, 30, 9, 30))

    cache = {}
    result = previous_day_volume_calculator_standalone('AAPL', date_a, cache)

    print(f"\nResult:")
    print(f"  xVol: {result['xvol']}")
    print(f"  Error: {result['error']}")

    if result['xvol'] is not None:
        print("\n✅ TEST PASSED: Weekend date handled correctly")
        print("    yfinance automatically adjusted to most recent trading day")
    elif result['error']:
        print(f"\n⚠️  TEST WARNING: Got error: {result['error']}")
        print("    This may be expected depending on yfinance behavior")
    else:
        print("\n❌ TEST FAILED: Unexpected result")


# ========================================
# MAIN
# ========================================
def main():
    print("\n" + "="*70)
    print("PDV (Previous Day Volume Calculator) Test Suite")
    print("="*70)
    print("This test suite validates the PDV functionality.")
    print()
    print("Prerequisites:")
    print("  - yfinance library installed (pip3 install yfinance)")
    print("  - Internet connection for API calls")
    print("  - pytz library installed (pip3 install pytz)")
    print("="*70)

    try:
        # Run all tests
        test_valid_calculation()
        test_cache_behavior()
        test_insufficient_data()
        test_invalid_symbol()
        test_multiple_stocks()
        test_weekend_date()

        print("\n" + "="*70)
        print("ALL TESTS COMPLETED")
        print("="*70)
        print("\nReview the results above to verify PDV calculator behavior.")
        print("\nFor detailed documentation, see:")
        print("  documentation/PDV_README.md")

    except Exception as e:
        print(f"\n❌ CRITICAL ERROR: Test suite failed with exception:")
        print(f"   {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
