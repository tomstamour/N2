#!/usr/bin/env python3
"""
Unit tests for Kelly Criterion position calculator.

Tests the calculate_kelly_positions() function with various scenarios.

Usage:
    python3 test_kelly_calculator.py
"""

import sys
import os
from argparse import Namespace

# Import function from main script
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from Gainers_Scanner_Script import calculate_kelly_positions


def test_scenario_a():
    """Scenario A: Base < Cap (4 YES stocks)"""
    print("\n" + "="*80)
    print("Test: Scenario A - Base < Cap (4 YES stocks)")
    print("="*80)

    args = Namespace(
        cash_to_play=1000.0,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'YES', 'checkpoint_b_price': 150.0},
        'B': {'trade_decision': 'YES', 'checkpoint_b_price': 100.0},
        'C': {'trade_decision': 'YES', 'checkpoint_b_price': 200.0},
        'D': {'trade_decision': 'YES', 'checkpoint_b_price': 250.0},
    }

    # Expected: Base=250, Cap=330, Use=250
    # A: 250/150=1, B: 250/100=2, C: 250/200=1, D: 250/250=1

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: ${args.cash_to_play}")
    print(f"Max proportion: {args.max_tradeCash_proportion_per_stock}")
    print(f"YES stocks: 4")
    print(f"Expected Base: $250, Cap: $330, Use: $250\n")

    errors = []

    # Test Stock A
    if tracked_stocks['A']['kelly_position'] != 1:
        errors.append(f"Stock A: Expected position=1, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] != 250.0:
        errors.append(f"Stock A: Expected cash=250.0, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A (Price $150): KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash=${tracked_stocks['A']['trade_cash']:.2f}")

    # Test Stock B
    if tracked_stocks['B']['kelly_position'] != 2:
        errors.append(f"Stock B: Expected position=2, got {tracked_stocks['B']['kelly_position']}")
    if tracked_stocks['B']['trade_cash'] != 250.0:
        errors.append(f"Stock B: Expected cash=250.0, got {tracked_stocks['B']['trade_cash']}")
    print(f"Stock B (Price $100): KellyPosition={tracked_stocks['B']['kelly_position']}, TradeCash=${tracked_stocks['B']['trade_cash']:.2f}")

    # Test Stock C
    if tracked_stocks['C']['kelly_position'] != 1:
        errors.append(f"Stock C: Expected position=1, got {tracked_stocks['C']['kelly_position']}")
    if tracked_stocks['C']['trade_cash'] != 250.0:
        errors.append(f"Stock C: Expected cash=250.0, got {tracked_stocks['C']['trade_cash']}")
    print(f"Stock C (Price $200): KellyPosition={tracked_stocks['C']['kelly_position']}, TradeCash=${tracked_stocks['C']['trade_cash']:.2f}")

    # Test Stock D
    if tracked_stocks['D']['kelly_position'] != 1:
        errors.append(f"Stock D: Expected position=1, got {tracked_stocks['D']['kelly_position']}")
    if tracked_stocks['D']['trade_cash'] != 250.0:
        errors.append(f"Stock D: Expected cash=250.0, got {tracked_stocks['D']['trade_cash']}")
    print(f"Stock D (Price $250): KellyPosition={tracked_stocks['D']['kelly_position']}, TradeCash=${tracked_stocks['D']['trade_cash']:.2f}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


def test_scenario_b():
    """Scenario B: Base > Cap (1 YES stock)"""
    print("\n" + "="*80)
    print("Test: Scenario B - Base > Cap (1 YES stock)")
    print("="*80)

    args = Namespace(
        cash_to_play=1000.0,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'YES', 'checkpoint_b_price': 150.0},
    }

    # Expected: Base=1000, Cap=330, Use=330
    # A: 330/150=2

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: ${args.cash_to_play}")
    print(f"Max proportion: {args.max_tradeCash_proportion_per_stock}")
    print(f"YES stocks: 1")
    print(f"Expected Base: $1000, Cap: $330, Use: $330\n")

    errors = []

    if tracked_stocks['A']['kelly_position'] != 2:
        errors.append(f"Stock A: Expected position=2, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] != 330.0:
        errors.append(f"Stock A: Expected cash=330.0, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A (Price $150): KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash=${tracked_stocks['A']['trade_cash']:.2f}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


def test_scenario_c():
    """Scenario C: Base > Cap (2 YES stocks)"""
    print("\n" + "="*80)
    print("Test: Scenario C - Base > Cap (2 YES stocks)")
    print("="*80)

    args = Namespace(
        cash_to_play=1000.0,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'YES', 'checkpoint_b_price': 100.0},
        'B': {'trade_decision': 'YES', 'checkpoint_b_price': 150.0},
    }

    # Expected: Base=500, Cap=330, Use=330
    # A: 330/100=3, B: 330/150=2

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: ${args.cash_to_play}")
    print(f"Max proportion: {args.max_tradeCash_proportion_per_stock}")
    print(f"YES stocks: 2")
    print(f"Expected Base: $500, Cap: $330, Use: $330\n")

    errors = []

    if tracked_stocks['A']['kelly_position'] != 3:
        errors.append(f"Stock A: Expected position=3, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] != 330.0:
        errors.append(f"Stock A: Expected cash=330.0, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A (Price $100): KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash=${tracked_stocks['A']['trade_cash']:.2f}")

    if tracked_stocks['B']['kelly_position'] != 2:
        errors.append(f"Stock B: Expected position=2, got {tracked_stocks['B']['kelly_position']}")
    if tracked_stocks['B']['trade_cash'] != 330.0:
        errors.append(f"Stock B: Expected cash=330.0, got {tracked_stocks['B']['trade_cash']}")
    print(f"Stock B (Price $150): KellyPosition={tracked_stocks['B']['kelly_position']}, TradeCash=${tracked_stocks['B']['trade_cash']:.2f}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


def test_no_yes_stocks():
    """Edge case: Trade='NO' for all stocks"""
    print("\n" + "="*80)
    print("Test: Edge Case - All stocks Trade='NO'")
    print("="*80)

    args = Namespace(
        cash_to_play=1000.0,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'NO', 'checkpoint_b_price': 100.0},
        'B': {'trade_decision': 'NO', 'checkpoint_b_price': 150.0},
    }

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: ${args.cash_to_play}")
    print(f"YES stocks: 0")
    print(f"Expected: All kelly_position=None, trade_cash=None\n")

    errors = []

    if tracked_stocks['A']['kelly_position'] is not None:
        errors.append(f"Stock A: Expected position=None, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] is not None:
        errors.append(f"Stock A: Expected cash=None, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A: KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash={tracked_stocks['A']['trade_cash']}")

    if tracked_stocks['B']['kelly_position'] is not None:
        errors.append(f"Stock B: Expected position=None, got {tracked_stocks['B']['kelly_position']}")
    if tracked_stocks['B']['trade_cash'] is not None:
        errors.append(f"Stock B: Expected cash=None, got {tracked_stocks['B']['trade_cash']}")
    print(f"Stock B: KellyPosition={tracked_stocks['B']['kelly_position']}, TradeCash={tracked_stocks['B']['trade_cash']}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


def test_feature_disabled():
    """Edge case: --cash-to-play not provided"""
    print("\n" + "="*80)
    print("Test: Edge Case - Feature Disabled (cash-to-play=None)")
    print("="*80)

    args = Namespace(
        cash_to_play=None,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'YES', 'checkpoint_b_price': 100.0},
    }

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: None")
    print(f"Expected: kelly_position=None, trade_cash=None\n")

    errors = []

    if tracked_stocks['A']['kelly_position'] is not None:
        errors.append(f"Stock A: Expected position=None, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] is not None:
        errors.append(f"Stock A: Expected cash=None, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A: KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash={tracked_stocks['A']['trade_cash']}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


def test_invalid_price():
    """Edge case: Invalid price data"""
    print("\n" + "="*80)
    print("Test: Edge Case - Invalid Price Data")
    print("="*80)

    args = Namespace(
        cash_to_play=1000.0,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'YES', 'checkpoint_b_price': None},
        'B': {'trade_decision': 'YES', 'checkpoint_b_price': 0},
        'C': {'trade_decision': 'YES', 'checkpoint_b_price': -10.0},
    }

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: ${args.cash_to_play}")
    print(f"YES stocks: 3 (all with invalid prices)")
    print(f"Expected: All kelly_position=None, trade_cash=None\n")

    errors = []

    if tracked_stocks['A']['kelly_position'] is not None:
        errors.append(f"Stock A: Expected position=None, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] is not None:
        errors.append(f"Stock A: Expected cash=None, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A (Price None): KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash={tracked_stocks['A']['trade_cash']}")

    if tracked_stocks['B']['kelly_position'] is not None:
        errors.append(f"Stock B: Expected position=None, got {tracked_stocks['B']['kelly_position']}")
    if tracked_stocks['B']['trade_cash'] is not None:
        errors.append(f"Stock B: Expected cash=None, got {tracked_stocks['B']['trade_cash']}")
    print(f"Stock B (Price 0): KellyPosition={tracked_stocks['B']['kelly_position']}, TradeCash={tracked_stocks['B']['trade_cash']}")

    if tracked_stocks['C']['kelly_position'] is not None:
        errors.append(f"Stock C: Expected position=None, got {tracked_stocks['C']['kelly_position']}")
    if tracked_stocks['C']['trade_cash'] is not None:
        errors.append(f"Stock C: Expected cash=None, got {tracked_stocks['C']['trade_cash']}")
    print(f"Stock C (Price -10): KellyPosition={tracked_stocks['C']['kelly_position']}, TradeCash={tracked_stocks['C']['trade_cash']}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


def test_valid_zero_position():
    """Edge case: Valid zero position (price > cash available)"""
    print("\n" + "="*80)
    print("Test: Edge Case - Valid Zero Position (high price)")
    print("="*80)

    args = Namespace(
        cash_to_play=100.0,
        max_tradeCash_proportion_per_stock=0.33
    )

    tracked_stocks = {
        'A': {'trade_decision': 'YES', 'checkpoint_b_price': 500.0},  # 33 < 500, position = 0
    }

    calculate_kelly_positions(tracked_stocks, args)

    print(f"Cash-to-play: ${args.cash_to_play}")
    print(f"Max proportion: {args.max_tradeCash_proportion_per_stock}")
    print(f"YES stocks: 1")
    print(f"Expected Base: $100, Cap: $33, Use: $33")
    print(f"Stock price: $500 > $33 cash available")
    print(f"Expected: kelly_position=0, trade_cash=33.0\n")

    errors = []

    if tracked_stocks['A']['kelly_position'] != 0:
        errors.append(f"Stock A: Expected position=0, got {tracked_stocks['A']['kelly_position']}")
    if tracked_stocks['A']['trade_cash'] != 33.0:
        errors.append(f"Stock A: Expected cash=33.0, got {tracked_stocks['A']['trade_cash']}")
    print(f"Stock A (Price $500): KellyPosition={tracked_stocks['A']['kelly_position']}, TradeCash=${tracked_stocks['A']['trade_cash']:.2f}")

    if errors:
        print("\n❌ FAILED:")
        for error in errors:
            print(f"  - {error}")
        return False
    else:
        print("\n✓ PASSED")
        return True


if __name__ == '__main__':
    print("\n" + "#"*80)
    print("# Kelly Criterion Position Calculator - Unit Tests")
    print("#"*80)

    results = []

    # Run all tests
    results.append(("Scenario A: Base < Cap (4 stocks)", test_scenario_a()))
    results.append(("Scenario B: Base > Cap (1 stock)", test_scenario_b()))
    results.append(("Scenario C: Base > Cap (2 stocks)", test_scenario_c()))
    results.append(("Edge: All Trade='NO'", test_no_yes_stocks()))
    results.append(("Edge: Feature Disabled", test_feature_disabled()))
    results.append(("Edge: Invalid Price", test_invalid_price()))
    results.append(("Edge: Valid Zero Position", test_valid_zero_position()))

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for name, result in results:
        status = "✓ PASSED" if result else "❌ FAILED"
        print(f"{status}: {name}")

    print("\n" + "="*80)
    print(f"Results: {passed}/{total} tests passed")
    print("="*80 + "\n")

    # Exit code
    sys.exit(0 if passed == total else 1)
