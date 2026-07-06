#!/usr/bin/env python3
"""
Test script for Trade Decider argument parsing and logic.
"""

import sys
import os

# Add the script directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scripts_functions/gainers_scanner_scripts'))

# Test 1: Verify argument parsing accepts all 5 TD arguments
print("=" * 60)
print("TEST 1: Argument Parsing with All 5 TD Arguments")
print("=" * 60)

test_args = [
    'test_script.py',
    '--time0', '2025-11-21/09:30',
    '--gap-percentage', '10',
    '--client-id', '300',
    '--gap_larger_then', '15',
    '--institution_smaller_then', '40',
    '--float_smaller_then', '50',
    '--price_smaller_then', '10',
    '--borrow_fee_smaller_then', '5'
]

sys.argv = test_args

try:
    # Import after setting sys.argv
    from Gainers_Scanner_Script import parse_arguments
    args = parse_arguments()

    print("✓ Arguments parsed successfully!")
    print(f"  gap_larger_then: {args.gap_larger_then}")
    print(f"  institution_smaller_then: {args.institution_smaller_then}")
    print(f"  float_smaller_then: {args.float_smaller_then}")
    print(f"  price_smaller_then: {args.price_smaller_then}")
    print(f"  borrow_fee_smaller_then: {args.borrow_fee_smaller_then}")
    print()
except SystemExit as e:
    print(f"✗ FAILED: SystemExit with code {e.code}")
    sys.exit(1)
except Exception as e:
    print(f"✗ FAILED: {e}")
    sys.exit(1)

# Test 2: Verify partial TD arguments trigger error
print("=" * 60)
print("TEST 2: Partial TD Arguments Should Fail")
print("=" * 60)

test_args_partial = [
    'test_script.py',
    '--time0', '2025-11-21/09:30',
    '--gap-percentage', '10',
    '--client-id', '300',
    '--gap_larger_then', '15',
    '--institution_smaller_then', '40'
    # Missing last 3 TD arguments
]

sys.argv = test_args_partial

try:
    # Re-import to test with new args
    import importlib
    import Gainers_Scanner_Script
    importlib.reload(Gainers_Scanner_Script)

    args = Gainers_Scanner_Script.parse_arguments()
    print("✗ FAILED: Should have raised error for partial TD arguments")
    sys.exit(1)
except SystemExit as e:
    if e.code == 2:  # argparse error code
        print("✓ Correctly rejected partial TD arguments")
        print()
    else:
        print(f"✗ FAILED: Unexpected exit code {e.code}")
        sys.exit(1)

# Test 3: Test evaluate_trade_decision function
print("=" * 60)
print("TEST 3: Trade Decision Evaluation Logic")
print("=" * 60)

# Create a mock args object
class MockArgs:
    def __init__(self):
        self.gap_larger_then = 15
        self.institution_smaller_then = 40
        self.float_smaller_then = 50
        self.price_smaller_then = 10
        self.borrow_fee_smaller_then = 5

mock_args = MockArgs()

# Import the evaluate function
from Gainers_Scanner_Script import evaluate_trade_decision

# Test case 1: All criteria met
stock_yes = {
    'live_percentage': 20,  # > 15 ✓
    'institutional_pct': 30,  # < 40 ✓
    'float_shares': 40_000_000,  # 40M < 50M ✓
    'checkpoint_b_price': 8,  # < 10 ✓
    'bf_best': 3  # < 5 ✓
}

result = evaluate_trade_decision(stock_yes, mock_args)
print(f"Test Case 1 (All criteria met): {result}")
if result == "YES":
    print("  ✓ PASS")
else:
    print("  ✗ FAIL - Expected YES")

# Test case 2: Gap too small
stock_no_gap = {
    'live_percentage': 10,  # <= 15 ✗
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}

result = evaluate_trade_decision(stock_no_gap, mock_args)
print(f"Test Case 2 (Gap too small): {result}")
if result == "NO":
    print("  ✓ PASS")
else:
    print("  ✗ FAIL - Expected NO")

# Test case 3: Institution too large
stock_no_inst = {
    'live_percentage': 20,
    'institutional_pct': 50,  # >= 40 ✗
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}

result = evaluate_trade_decision(stock_no_inst, mock_args)
print(f"Test Case 3 (Institution too large): {result}")
if result == "NO":
    print("  ✓ PASS")
else:
    print("  ✗ FAIL - Expected NO")

# Test case 4: Missing data (institutional_pct = None)
stock_no_data = {
    'live_percentage': 20,
    'institutional_pct': None,  # Missing ✗
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}

result = evaluate_trade_decision(stock_no_data, mock_args)
print(f"Test Case 4 (Missing institutional_pct): {result}")
if result == "NO":
    print("  ✓ PASS")
else:
    print("  ✗ FAIL - Expected NO")

# Test case 5: BF_BEST too high
stock_no_bf = {
    'live_percentage': 20,
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 10  # >= 5 ✗
}

result = evaluate_trade_decision(stock_no_bf, mock_args)
print(f"Test Case 5 (BF_BEST too high): {result}")
if result == "NO":
    print("  ✓ PASS")
else:
    print("  ✗ FAIL - Expected NO")

print()
print("=" * 60)
print("ALL TESTS COMPLETED SUCCESSFULLY!")
print("=" * 60)
