#!/usr/bin/env python3
"""
Test script for OTC stock detection feature in Gainers Scanner Script.

This script tests:
1. OTC detection logic (PINK, OTCBB, OTCMKTS exchanges)
2. Major exchange detection (NASDAQ, NYSE, etc.)
3. Trade decision filtering with --discard-otc flag
4. GTT file backward compatibility (25-column old vs 26-column new format)
5. Missing exchange data handling

Usage:
    python3 test_otc_detection.py
"""

import sys
import os

# Add parent directory to path to import the scanner script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts_functions', 'gainers_scanner_scripts'))

# Mock Contract class for testing
class MockContract:
    """Mock IBKR Contract for testing"""
    def __init__(self, symbol, primary_exchange, exchange='SMART'):
        self.symbol = symbol
        self.primaryExchange = primary_exchange
        self.exchange = exchange
        self.secType = "STK"
        self.currency = "USD"


def test_otc_exchange_detection():
    """Test OTC exchange detection for various exchange types"""
    print("\n=== Test 1: OTC Exchange Detection ===")

    # Import the scanner class
    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        # Create minimal args object
        args = argparse.Namespace(
            time0='2025-12-10/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test cases: (symbol, exchange, expected_is_otc, expected_is_otc_str)
        test_cases = [
            # OTC exchanges
            ("GEGRF", "PINK", True, "OTC"),
            ("ABCDF", "OTCBB", True, "OTC"),
            ("XYZPQ", "OTCMKTS", True, "OTC"),
            ("TEST1", "OTC", True, "OTC"),
            ("TEST2", "pink", True, "OTC"),  # Test case insensitivity

            # Major exchanges
            ("AAPL", "NASDAQ", False, "-"),
            ("TSLA", "NYSE", False, "-"),
            ("SPY", "ARCX", False, "-"),
            ("QQQ", "AMEX", False, "-"),

            # Edge cases
            ("NONE1", None, False, "-"),  # None exchange
            ("EMPTY", "", False, "-"),     # Empty string
        ]

        passed = 0
        failed = 0

        for symbol, exchange, expected_otc, expected_str in test_cases:
            contract = MockContract(symbol, exchange)
            result = scanner.detect_otc_status(symbol, contract)

            if result['is_otc'] == expected_otc and result['is_otc_str'] == expected_str:
                print(f"✓ PASS: {symbol:10s} ({exchange or 'None':10s}) → OTC={result['is_otc']}, str='{result['is_otc_str']}'")
                passed += 1
            else:
                print(f"✗ FAIL: {symbol:10s} ({exchange or 'None':10s}) → Expected OTC={expected_otc}, got {result['is_otc']}")
                failed += 1

        print(f"\nResults: {passed} passed, {failed} failed")
        return failed == 0

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_trade_decision_with_otc_filter():
    """Test trade decision logic with OTC filtering"""
    print("\n=== Test 2: Trade Decision with OTC Filter ===")

    try:
        from Gainers_Scanner_Script import evaluate_trade_decision
        import argparse

        # Test case 1: OTC stock with --discard-otc YES
        print("\nTest Case 1: OTC stock with --discard-otc YES")
        args = argparse.Namespace(
            discard_otc='YES',
            gap_larger_then=10.0,
            institution_smaller_then=50.0,
            float_smaller_then=100.0,
            price_smaller_then=50.0,
            borrow_fee_smaller_then=5.0,
            xVolume_smaller_then=3.0
        )

        stock_data = {
            'symbol': 'OTCSTOCK',
            'is_otc': True,
            'is_otc_str': 'OTC',
            'live_percentage': 15.0,  # Meets criterion
            'institutional_pct': 30.0,  # Meets criterion
            'float_shares': 50_000_000,  # Meets criterion (50M)
            'checkpoint_b_price': 25.0,  # Meets criterion
            'bf_best': 2.0,  # Meets criterion
            'xvol': 1.5  # Meets criterion
        }

        result = evaluate_trade_decision(stock_data, args)
        if result == "NO":
            print(f"✓ PASS: OTC stock correctly rejected with --discard-otc YES")
        else:
            print(f"✗ FAIL: Expected 'NO', got '{result}'")
            return False

        # Test case 2: OTC stock with --discard-otc NO (default)
        print("\nTest Case 2: OTC stock with --discard-otc NO")
        args.discard_otc = 'NO'

        result = evaluate_trade_decision(stock_data, args)
        if result == "YES":
            print(f"✓ PASS: OTC stock correctly allowed with --discard-otc NO")
        else:
            print(f"✗ FAIL: Expected 'YES' (stock meets all criteria), got '{result}'")
            return False

        # Test case 3: Non-OTC stock with --discard-otc YES
        print("\nTest Case 3: Non-OTC stock with --discard-otc YES")
        args.discard_otc = 'YES'
        stock_data['is_otc'] = False
        stock_data['is_otc_str'] = '-'
        stock_data['symbol'] = 'NASDAQ_STOCK'

        result = evaluate_trade_decision(stock_data, args)
        if result == "YES":
            print(f"✓ PASS: Non-OTC stock correctly allowed with --discard-otc YES")
        else:
            print(f"✗ FAIL: Expected 'YES', got '{result}'")
            return False

        print("\n✓ All trade decision tests passed!")
        return True

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_gtt_format_parsing():
    """Test backward compatibility with old 25-column GTT vs new 26-column GTT"""
    print("\n=== Test 3: GTT Format Parsing (Backward Compatibility) ===")

    try:
        import tempfile
        from pathlib import Path
        from Gainers_Scanner_Script import load_existing_gtt

        # Test case 1: Old 25-column GTT (without OTC column)
        print("\nTest Case 1: Old 25-column GTT format")
        old_gtt_content = """Symbol\tCP-A(t)\tCP-A($)\tCP-B(t)\tCP-B($)\tGap(%)\tGapL(%)\tPriceL($)\tSect\tIndus\tShortStatus\tShortDiff\tShortableShares\tFloat(M$)\tNetIncome(M$)\tInstit(%)\tBorrowFee\tBF1\tBF2\tBF_BEST\txVol\tTrade\tKellyPosition\tTradeCash($)\tLastUpdate
AAPL\t2025-12-10/09:30\t150.00\t2025-12-10/10:00\t180.00\t20.00\t18.50\t177.75\tTechnology\tConsumer Electronics\tHIGH_AVAIL\t2.80\t5000\t2650.00\t99050.00\t58.95\t0.25\tN/A\tN/A\tN/A\t1.23\tYES\t10\t1777.50\t2025-12-10 10:30:00"""

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(old_gtt_content)
            old_gtt_path = Path(f.name)

        try:
            tracked = load_existing_gtt(old_gtt_path)

            if len(tracked) == 1 and 'AAPL' in tracked:
                stock = tracked['AAPL']
                # Check that OTC fields default to non-OTC
                if stock.get('is_otc') == False and stock.get('is_otc_str') == '-':
                    print(f"✓ PASS: Old GTT format correctly parsed, OTC defaulted to non-OTC")
                else:
                    print(f"✗ FAIL: OTC fields not correctly defaulted (is_otc={stock.get('is_otc')}, is_otc_str='{stock.get('is_otc_str')}')")
                    return False
            else:
                print(f"✗ FAIL: Failed to parse old GTT format")
                return False
        finally:
            old_gtt_path.unlink()

        # Test case 2: New 26-column GTT (with OTC column)
        print("\nTest Case 2: New 26-column GTT format")
        new_gtt_content = """Symbol\tCP-A(t)\tCP-A($)\tCP-B(t)\tCP-B($)\tGap(%)\tGapL(%)\tPriceL($)\tSect\tIndus\tOTC\tShortStatus\tShortDiff\tShortableShares\tFloat(M$)\tNetIncome(M$)\tInstit(%)\tBorrowFee\tBF1\tBF2\tBF_BEST\txVol\tTrade\tKellyPosition\tTradeCash($)\tLastUpdate
AAPL\t2025-12-10/09:30\t150.00\t2025-12-10/10:00\t180.00\t20.00\t18.50\t177.75\tTechnology\tConsumer Electronics\t-\tHIGH_AVAIL\t2.80\t5000\t2650.00\t99050.00\t58.95\t0.25\tN/A\tN/A\tN/A\t1.23\tYES\t10\t1777.50\t2025-12-10 10:30:00
GEGRF\t2025-12-10/09:30\t2.50\t2025-12-10/10:20\t3.75\t50.00\t48.50\t3.71\tHealthcare\tBiotechnology\tOTC\tN/A\tN/A\tN/A\t50.00\tN/A\t5.50\tN/A\tN/A\t6.56\t6.56\tN/A\tNO\tN/A\tN/A\t2025-12-10 10:30:00"""

        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write(new_gtt_content)
            new_gtt_path = Path(f.name)

        try:
            tracked = load_existing_gtt(new_gtt_path)

            if len(tracked) == 2 and 'AAPL' in tracked and 'GEGRF' in tracked:
                aapl = tracked['AAPL']
                gegrf = tracked['GEGRF']

                # Check AAPL (non-OTC)
                if aapl.get('is_otc') == False and aapl.get('is_otc_str') == '-':
                    print(f"✓ PASS: AAPL correctly parsed as non-OTC")
                else:
                    print(f"✗ FAIL: AAPL OTC fields incorrect")
                    return False

                # Check GEGRF (OTC)
                if gegrf.get('is_otc') == True and gegrf.get('is_otc_str') == 'OTC':
                    print(f"✓ PASS: GEGRF correctly parsed as OTC")
                else:
                    print(f"✗ FAIL: GEGRF OTC fields incorrect (is_otc={gegrf.get('is_otc')}, is_otc_str='{gegrf.get('is_otc_str')}')")
                    return False
            else:
                print(f"✗ FAIL: Failed to parse new GTT format")
                return False
        finally:
            new_gtt_path.unlink()

        print("\n✓ All GTT format tests passed!")
        return True

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all OTC detection tests"""
    print("=" * 70)
    print("OTC Stock Detection Feature - Test Suite")
    print("=" * 70)

    results = []

    # Run all tests
    results.append(("OTC Exchange Detection", test_otc_exchange_detection()))
    results.append(("Trade Decision with OTC Filter", test_trade_decision_with_otc_filter()))
    results.append(("GTT Format Backward Compatibility", test_gtt_format_parsing()))

    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)

    total = len(results)
    passed = sum(1 for _, result in results if result)
    failed = total - passed

    for test_name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if failed == 0:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n❌ {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
