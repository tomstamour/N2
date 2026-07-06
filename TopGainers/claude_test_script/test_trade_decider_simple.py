#!/usr/bin/env python3
"""
Simple unit test for Trade Decider logic (without IBKR dependencies).
"""

# Copy the evaluate_trade_decision function here for testing
def evaluate_trade_decision(stock_data, args):
    """
    Evaluate if stock meets ALL trade decision criteria.

    Trade Decision Logic:
    - GapL(%) must be LARGER than args.gap_larger_then
    - Instit(%) must be SMALLER than args.institution_smaller_then
    - Float(M$) must be SMALLER than args.float_smaller_then
    - CP-B($) must be SMALLER than args.price_smaller_then
    - BF_BEST must be SMALLER than args.borrow_fee_smaller_then

    All 5 criteria must be met for "YES" result.
    Missing/N/A data results in "NO" (conservative approach).

    Args:
        stock_data: Dictionary with stock information
        args: Parsed command-line arguments

    Returns:
        str: "YES" if ALL criteria are met, "NO" otherwise
    """
    # If trade decision feature not enabled (no TD args), return NO
    if all([
        args.gap_larger_then is None,
        args.institution_smaller_then is None,
        args.float_smaller_then is None,
        args.price_smaller_then is None,
        args.borrow_fee_smaller_then is None
    ]):
        return "NO"

    # Check criterion 1: GapL(%) - must be LARGER than threshold
    if args.gap_larger_then is not None:
        live_pct = stock_data.get('live_percentage')
        if live_pct is None or live_pct <= args.gap_larger_then:
            return "NO"

    # Check criterion 2: Instit(%) - must be SMALLER than threshold
    if args.institution_smaller_then is not None:
        inst_pct = stock_data.get('institutional_pct')
        if inst_pct is None or inst_pct >= args.institution_smaller_then:
            return "NO"

    # Check criterion 3: Float(M$) - must be SMALLER than threshold
    if args.float_smaller_then is not None:
        float_shares = stock_data.get('float_shares')
        if float_shares is None:
            return "NO"
        # Convert to millions for comparison
        float_millions = float_shares / 1_000_000
        if float_millions >= args.float_smaller_then:
            return "NO"

    # Check criterion 4: CP-B($) - must be SMALLER than threshold
    if args.price_smaller_then is not None:
        cp_b_price = stock_data.get('checkpoint_b_price')
        if cp_b_price is None or cp_b_price >= args.price_smaller_then:
            return "NO"

    # Check criterion 5: BF_BEST - must be SMALLER than threshold
    if args.borrow_fee_smaller_then is not None:
        bf_best = stock_data.get('bf_best')
        if bf_best is None or bf_best >= args.borrow_fee_smaller_then:
            return "NO"

    # All criteria met!
    return "YES"


# Test class to mock args
class MockArgs:
    def __init__(self):
        self.gap_larger_then = 15
        self.institution_smaller_then = 40
        self.float_smaller_then = 50
        self.price_smaller_then = 10
        self.borrow_fee_smaller_then = 5


print("=" * 60)
print("Trade Decider Unit Tests")
print("=" * 60)

mock_args = MockArgs()
test_passed = 0
test_total = 0

# Test 1: All criteria met
test_total += 1
stock_yes = {
    'live_percentage': 20,  # > 15 ✓
    'institutional_pct': 30,  # < 40 ✓
    'float_shares': 40_000_000,  # 40M < 50M ✓
    'checkpoint_b_price': 8,  # < 10 ✓
    'bf_best': 3  # < 5 ✓
}
result = evaluate_trade_decision(stock_yes, mock_args)
print(f"Test 1 - All criteria met: {result} ", end="")
if result == "YES":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected YES")

# Test 2: Gap too small (edge case: exactly equal)
test_total += 1
stock_no_gap = {
    'live_percentage': 15,  # <= 15 ✗ (equal to threshold)
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}
result = evaluate_trade_decision(stock_no_gap, mock_args)
print(f"Test 2 - Gap equal to threshold: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 3: Gap too small
test_total += 1
stock_no_gap2 = {
    'live_percentage': 10,  # < 15 ✗
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}
result = evaluate_trade_decision(stock_no_gap2, mock_args)
print(f"Test 3 - Gap too small: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 4: Institution too large
test_total += 1
stock_no_inst = {
    'live_percentage': 20,
    'institutional_pct': 50,  # >= 40 ✗
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}
result = evaluate_trade_decision(stock_no_inst, mock_args)
print(f"Test 4 - Institution too large: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 5: Institution equal to threshold (edge case)
test_total += 1
stock_inst_equal = {
    'live_percentage': 20,
    'institutional_pct': 40,  # >= 40 ✗ (equal to threshold)
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}
result = evaluate_trade_decision(stock_inst_equal, mock_args)
print(f"Test 5 - Institution equal to threshold: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 6: Float too large
test_total += 1
stock_no_float = {
    'live_percentage': 20,
    'institutional_pct': 30,
    'float_shares': 60_000_000,  # 60M >= 50M ✗
    'checkpoint_b_price': 8,
    'bf_best': 3
}
result = evaluate_trade_decision(stock_no_float, mock_args)
print(f"Test 6 - Float too large: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 7: Price too high
test_total += 1
stock_no_price = {
    'live_percentage': 20,
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 12,  # >= 10 ✗
    'bf_best': 3
}
result = evaluate_trade_decision(stock_no_price, mock_args)
print(f"Test 7 - Price too high: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 8: BF_BEST too high
test_total += 1
stock_no_bf = {
    'live_percentage': 20,
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 10  # >= 5 ✗
}
result = evaluate_trade_decision(stock_no_bf, mock_args)
print(f"Test 8 - BF_BEST too high: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 9: Missing data (institutional_pct = None)
test_total += 1
stock_no_data = {
    'live_percentage': 20,
    'institutional_pct': None,  # Missing ✗
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': 3
}
result = evaluate_trade_decision(stock_no_data, mock_args)
print(f"Test 9 - Missing institutional_pct: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 10: Missing BF_BEST
test_total += 1
stock_no_bf_data = {
    'live_percentage': 20,
    'institutional_pct': 30,
    'float_shares': 40_000_000,
    'checkpoint_b_price': 8,
    'bf_best': None  # Missing ✗
}
result = evaluate_trade_decision(stock_no_bf_data, mock_args)
print(f"Test 10 - Missing BF_BEST: {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

# Test 11: Edge case - exact boundaries that should pass
test_total += 1
stock_edge_pass = {
    'live_percentage': 15.01,  # > 15 ✓
    'institutional_pct': 39.99,  # < 40 ✓
    'float_shares': 49_999_999,  # 49.99M < 50M ✓
    'checkpoint_b_price': 9.99,  # < 10 ✓
    'bf_best': 4.99  # < 5 ✓
}
result = evaluate_trade_decision(stock_edge_pass, mock_args)
print(f"Test 11 - Edge case boundaries (should pass): {result} ", end="")
if result == "YES":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected YES")

# Test 12: No TD arguments (feature disabled)
test_total += 1
class MockArgsDisabled:
    gap_larger_then = None
    institution_smaller_then = None
    float_smaller_then = None
    price_smaller_then = None
    borrow_fee_smaller_then = None

mock_args_disabled = MockArgsDisabled()
stock_any = {
    'live_percentage': 100,
    'institutional_pct': 5,
    'float_shares': 10_000_000,
    'checkpoint_b_price': 1,
    'bf_best': 0.5
}
result = evaluate_trade_decision(stock_any, mock_args_disabled)
print(f"Test 12 - Feature disabled (no TD args): {result} ", end="")
if result == "NO":
    print("✓ PASS")
    test_passed += 1
else:
    print("✗ FAIL - Expected NO")

print()
print("=" * 60)
print(f"Results: {test_passed}/{test_total} tests passed")
if test_passed == test_total:
    print("✓ ALL TESTS PASSED!")
    exit(0)
else:
    print(f"✗ {test_total - test_passed} tests failed")
    exit(1)
print("=" * 60)
