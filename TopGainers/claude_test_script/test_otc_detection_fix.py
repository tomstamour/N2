#!/usr/bin/env python3
"""
Test script for OTC detection bug fix in Gainers Scanner Script.

This script tests the multi-layer OTC detection with:
1. IBKR exchange codes (EXPERT, SCM, VENTURE, GREY, PINK, OTCBB, etc.)
2. yfinance exchange fallback (OTCMKTS, PINK, OTC, OTCQB, OTCQX)
3. Symbol pattern heuristics (logging only)

Known OTC stocks for testing:
- ORGS: OTC Markets (EXPERT exchange via IBKR)
- GEGRF: OTC Markets (5-letter F ending pattern)
- LUNA: OTC Markets (EXPERT exchange confirmed)

Usage:
    python3 test_otc_detection_fix.py
"""

import sys
import os

# Add parent directory to path to import the scanner script
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts_functions', 'gainers_scanner_scripts'))

# Mock Contract class for testing
class MockContract:
    """Mock IBKR Contract for testing"""
    def __init__(self, symbol, exchange='', primary_exchange=''):
        self.symbol = symbol
        self.exchange = exchange
        self.primaryExchange = primary_exchange
        self.secType = "STK"
        self.currency = "USD"


def test_layer1_ibkr_expert_exchange():
    """Test Layer 1: IBKR EXPERT exchange detection (primary fix for ORGS)"""
    print("\n=== Test 1: Layer 1 - IBKR EXPERT Exchange (ORGS case) ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        # Create minimal args object
        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test ORGS with EXPERT exchange (from scanner logs)
        contract = MockContract("ORGS", exchange="EXPERT", primary_exchange="")
        result = scanner.detect_otc_status("ORGS", contract)

        if result['is_otc'] == True and result['is_otc_str'] == "OTC":
            print(f"✓ PASS: ORGS detected as OTC via exchange='EXPERT'")
            print(f"  Result: is_otc={result['is_otc']}, is_otc_str='{result['is_otc_str']}', method='{result['detection_method']}'")
            return True
        else:
            print(f"✗ FAIL: ORGS should be OTC but got is_otc={result['is_otc']}")
            return False

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_layer1_ibkr_other_otc_codes():
    """Test Layer 1: Other IBKR-specific OTC codes (SCM, VENTURE, GREY)"""
    print("\n=== Test 2: Layer 1 - Other IBKR OTC Codes ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test cases for IBKR-specific OTC codes
        test_cases = [
            ("TEST_SCM", "SCM", ""),      # Small Cap Market
            ("TEST_VENT", "VENTURE", ""), # Venture exchange
            ("TEST_GREY", "GREY", ""),    # Grey market
            ("TEST_PINK", "PINK", ""),    # Pink sheets
            ("TEST_OTCBB", "OTCBB", ""),  # OTC Bulletin Board
        ]

        passed = 0
        failed = 0

        for symbol, exchange, primary_exchange in test_cases:
            contract = MockContract(symbol, exchange=exchange, primary_exchange=primary_exchange)
            result = scanner.detect_otc_status(symbol, contract)

            if result['is_otc'] == True and result['is_otc_str'] == "OTC":
                print(f"✓ PASS: {symbol:15s} (exchange='{exchange:10s}') → OTC")
                passed += 1
            else:
                print(f"✗ FAIL: {symbol:15s} (exchange='{exchange:10s}') → Expected OTC, got is_otc={result['is_otc']}")
                failed += 1

        print(f"\nResults: {passed} passed, {failed} failed")
        return failed == 0

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_layer1_major_exchanges():
    """Test Layer 1: Major exchanges should NOT be detected as OTC"""
    print("\n=== Test 3: Layer 1 - Major Exchanges (Not OTC) ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test cases for major exchanges
        test_cases = [
            ("NVDA", "SMART", "NASDAQ"),  # NASDAQ with SMART routing
            ("TSLA", "SMART", "NYSE"),    # NYSE with SMART routing
            ("AAPL", "NASDAQ", ""),       # Direct NASDAQ
            ("SPY", "ARCX", ""),          # ARCX (NYSE Arca)
        ]

        passed = 0
        failed = 0

        for symbol, exchange, primary_exchange in test_cases:
            contract = MockContract(symbol, exchange=exchange, primary_exchange=primary_exchange)
            result = scanner.detect_otc_status(symbol, contract)

            if result['is_otc'] == False and result['is_otc_str'] == "-":
                print(f"✓ PASS: {symbol:15s} (exchange='{exchange:10s}', primary='{primary_exchange:10s}') → NOT OTC")
                passed += 1
            else:
                print(f"✗ FAIL: {symbol:15s} should NOT be OTC, got is_otc={result['is_otc']}")
                failed += 1

        print(f"\nResults: {passed} passed, {failed} failed")
        return failed == 0

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_layer2_yfinance_fallback():
    """Test Layer 2: yfinance exchange fallback when IBKR is empty/SMART"""
    print("\n=== Test 4: Layer 2 - yfinance Fallback ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test case: IBKR returns SMART (ambiguous), but yfinance says OTCMKTS
        contract = MockContract("GEGRF", exchange="SMART", primary_exchange="")
        yfinance_data = {'exchange': 'OTCMKTS'}  # Mock yfinance data

        result = scanner.detect_otc_status("GEGRF", contract, yfinance_data=yfinance_data)

        if result['is_otc'] == True and result['is_otc_str'] == "OTC":
            print(f"✓ PASS: GEGRF detected as OTC via yfinance fallback (exchange='OTCMKTS')")
            print(f"  Result: is_otc={result['is_otc']}, is_otc_str='{result['is_otc_str']}', method='{result['detection_method']}'")
            return True
        else:
            print(f"✗ FAIL: GEGRF should be OTC via yfinance fallback, got is_otc={result['is_otc']}")
            return False

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_layer2_yfinance_otc_codes():
    """Test Layer 2: Various yfinance OTC exchange codes"""
    print("\n=== Test 5: Layer 2 - yfinance OTC Codes ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test various yfinance OTC codes
        yf_otc_codes = ['OTCMKTS', 'PINK', 'OTC', 'OTCBB', 'OTCQB', 'OTCQX']

        passed = 0
        failed = 0

        for yf_code in yf_otc_codes:
            contract = MockContract(f"TEST_{yf_code}", exchange="SMART", primary_exchange="")
            yfinance_data = {'exchange': yf_code}
            result = scanner.detect_otc_status(f"TEST_{yf_code}", contract, yfinance_data=yfinance_data)

            if result['is_otc'] == True and result['is_otc_str'] == "OTC":
                print(f"✓ PASS: yfinance exchange='{yf_code:10s}' → OTC")
                passed += 1
            else:
                print(f"✗ FAIL: yfinance exchange='{yf_code:10s}' → Expected OTC, got is_otc={result['is_otc']}")
                failed += 1

        print(f"\nResults: {passed} passed, {failed} failed")
        return failed == 0

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_edge_case_empty_exchange():
    """Test edge case: Empty exchange fields should default to non-OTC"""
    print("\n=== Test 6: Edge Case - Empty Exchange ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test with completely empty exchange fields
        contract = MockContract("TEST_EMPTY", exchange="", primary_exchange="")
        result = scanner.detect_otc_status("TEST_EMPTY", contract)

        if result['is_otc'] == False and result['is_otc_str'] == "-":
            print(f"✓ PASS: Empty exchange fields → NOT OTC (fail-safe default)")
            print(f"  Result: is_otc={result['is_otc']}, is_otc_str='{result['is_otc_str']}'")
            return True
        else:
            print(f"✗ FAIL: Empty exchange should default to non-OTC, got is_otc={result['is_otc']}")
            return False

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def test_layer3_symbol_pattern_logging():
    """Test Layer 3: Symbol pattern should only log, not detect as OTC"""
    print("\n=== Test 7: Layer 3 - Symbol Pattern (Logging Only) ===")

    try:
        from Gainers_Scanner_Script import GainersScanner
        import argparse

        args = argparse.Namespace(
            time0='2025-12-11/09:30',
            gap_percentage=10,
            client_id=300,
            host='127.0.0.1',
            port=4001,
            historical_mode=False,
            discard_otc='NO'
        )

        scanner = GainersScanner(args)

        # Test 5-letter symbol ending in F (pattern match) but NASDAQ exchange (NOT OTC)
        contract = MockContract("STAFFF", exchange="NASDAQ", primary_exchange="")
        result = scanner.detect_otc_status("STAFFF", contract)

        if result['is_otc'] == False and result['is_otc_str'] == "-":
            print(f"✓ PASS: 5-letter F-ending symbol on NASDAQ → NOT OTC (pattern ignored)")
            print(f"  Note: Symbol pattern should only log warning, not affect detection")
            return True
        else:
            print(f"✗ FAIL: Symbol pattern should NOT cause OTC detection, got is_otc={result['is_otc']}")
            return False

    except Exception as e:
        print(f"✗ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all OTC detection fix tests"""
    print("=" * 70)
    print("OTC Detection Bug Fix - Test Suite")
    print("=" * 70)

    results = []

    # Run all tests
    results.append(("Layer 1: IBKR EXPERT Exchange (ORGS)", test_layer1_ibkr_expert_exchange()))
    results.append(("Layer 1: Other IBKR OTC Codes", test_layer1_ibkr_other_otc_codes()))
    results.append(("Layer 1: Major Exchanges", test_layer1_major_exchanges()))
    results.append(("Layer 2: yfinance Fallback", test_layer2_yfinance_fallback()))
    results.append(("Layer 2: yfinance OTC Codes", test_layer2_yfinance_otc_codes()))
    results.append(("Edge Case: Empty Exchange", test_edge_case_empty_exchange()))
    results.append(("Layer 3: Symbol Pattern (Logging Only)", test_layer3_symbol_pattern_logging()))

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
        print("\nThe OTC detection fix is working correctly:")
        print("  - IBKR EXPERT exchange detected as OTC (fixes ORGS bug)")
        print("  - yfinance fallback working when IBKR is ambiguous")
        print("  - Major exchanges correctly identified as non-OTC")
        print("  - Edge cases handled gracefully")
        return 0
    else:
        print(f"\n❌ {failed} test(s) failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
