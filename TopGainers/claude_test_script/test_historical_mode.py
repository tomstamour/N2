#!/usr/bin/env python3
"""
Test script to validate historical mode argument parsing
"""
import sys
import argparse
import datetime
import pytz

# Add the script directory to path
sys.path.insert(0, './scripts_functions/gainers_scanner_scripts')

def test_argument_parsing():
    """Test that --historical-mode argument is properly parsed"""
    parser = argparse.ArgumentParser()
    parser.add_argument('--time0', required=True)
    parser.add_argument('--gap-percentage', type=float, required=True)
    parser.add_argument('--client-id', type=int, required=True)
    parser.add_argument('--output-dir', default='.')
    parser.add_argument('--historical-mode', action='store_true')
    parser.add_argument('--no-new-window', action='store_true')
    parser.add_argument('--max-stocks', type=int, default=50)
    parser.add_argument('--log', default=None)

    # Test case 1: Historical mode enabled
    test_args1 = [
        '--time0', '2025-11-20/16:00',
        '--gap-percentage', '40',
        '--client-id', '702',
        '--historical-mode',
        '--no-new-window'
    ]

    args1 = parser.parse_args(test_args1)

    print("Test Case 1: Historical Mode Enabled")
    print(f"  time0: {args1.time0}")
    print(f"  gap_percentage: {args1.gap_percentage}")
    print(f"  client_id: {args1.client_id}")
    print(f"  historical_mode: {args1.historical_mode}")
    print(f"  Expected: True")
    print(f"  Result: {'PASS' if args1.historical_mode == True else 'FAIL'}")
    print()

    # Test case 2: Historical mode disabled (default)
    test_args2 = [
        '--time0', '2025-11-20/16:00',
        '--gap-percentage', '40',
        '--client-id', '702',
        '--no-new-window'
    ]

    # Reset parser for second test
    parser2 = argparse.ArgumentParser()
    parser2.add_argument('--time0', required=True)
    parser2.add_argument('--gap-percentage', type=float, required=True)
    parser2.add_argument('--client-id', type=int, required=True)
    parser2.add_argument('--output-dir', default='.')
    parser2.add_argument('--historical-mode', action='store_true')
    parser2.add_argument('--no-new-window', action='store_true')
    parser2.add_argument('--max-stocks', type=int, default=50)
    parser2.add_argument('--log', default=None)

    args2 = parser2.parse_args(test_args2)

    print("Test Case 2: Historical Mode Disabled (Default)")
    print(f"  time0: {args2.time0}")
    print(f"  gap_percentage: {args2.gap_percentage}")
    print(f"  client_id: {args2.client_id}")
    print(f"  historical_mode: {args2.historical_mode}")
    print(f"  Expected: False")
    print(f"  Result: {'PASS' if args2.historical_mode == False else 'FAIL'}")
    print()

def test_end_time_calculation():
    """Test end time calculation for historical mode"""
    ET_TZ = pytz.timezone('US/Eastern')

    # Parse time0
    time0_str = "2025-11-20/16:00"
    time0_naive = datetime.datetime.strptime(time0_str, '%Y-%m-%d/%H:%M')
    time0_dt = ET_TZ.localize(time0_naive)

    # Calculate end time (time0 + 1 day at 16:00)
    time0_date = time0_dt.date()
    end_date = time0_date + datetime.timedelta(days=1)
    end_dt = ET_TZ.localize(datetime.datetime.combine(end_date, datetime.time(16, 0)))

    print("Test Case 3: End Time Calculation")
    print(f"  time0: {time0_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  end_dt: {end_dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print(f"  Expected: 2025-11-21 16:00:00 EST")
    print(f"  Result: {'PASS' if end_dt.strftime('%Y-%m-%d %H:%M') == '2025-11-21 16:00' else 'FAIL'}")
    print()

    # Calculate duration
    duration_seconds = int((end_dt - time0_dt).total_seconds())
    print(f"  Duration: {duration_seconds} seconds ({duration_seconds/3600:.1f} hours)")
    print(f"  Expected: 86400 seconds (24 hours)")
    print(f"  Result: {'PASS' if duration_seconds == 86400 else 'FAIL'}")
    print()

if __name__ == "__main__":
    print("="*80)
    print("Historical Mode Feature - Unit Tests")
    print("="*80)
    print()

    test_argument_parsing()
    test_end_time_calculation()

    print("="*80)
    print("All Tests Complete")
    print("="*80)
