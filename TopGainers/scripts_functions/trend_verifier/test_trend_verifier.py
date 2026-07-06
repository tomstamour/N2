#!/usr/bin/env python3
"""
Test suite for Trend Verifier
Tests CLI argument parsing, cache functionality, and GTT column insertion
"""

import sys
import os
import csv
import tempfile
import datetime
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

def test_cli_help():
    """Test that --help argument works"""
    import subprocess

    script_path = Path(__file__).parent / "Trend_Verifier.py"
    result = subprocess.run(
        ["python3", str(script_path), "--help"],
        capture_output=True,
        text=True
    )

    assert result.returncode == 0, "Help command should exit with code 0"
    assert "Trend Verifier" in result.stdout, "Help text should contain 'Trend Verifier'"
    assert "--input" in result.stdout, "Help should show --input argument"
    assert "--client-id" in result.stdout, "Help should show --client-id argument"
    print("✓ CLI help test passed")


def test_cache_loading():
    """Test cache loading and TTL validation"""
    from Trend_Verifier import TrendCache

    # Create temporary cache file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        cache_path = f.name
        writer = csv.DictWriter(f, fieldnames=['symbol', 'trend_1y', 'trend_6m', 'trend_3m', 'cached_at', 'bars_count'])
        writer.writeheader()
        writer.writerow({
            'symbol': 'AAPL',
            'trend_1y': '2.23',
            'trend_6m': '1.45',
            'trend_3m': '0.98',
            'cached_at': datetime.datetime.now().isoformat(),
            'bars_count': '252'
        })

    try:
        # Test loading
        cache = TrendCache(cache_path, ttl_seconds=3600)
        assert len(cache.cache) == 1, "Cache should contain 1 entry"
        assert 'AAPL' in cache.cache, "Cache should contain AAPL"

        # Test get (should be valid)
        data = cache.get('AAPL')
        assert data is not None, "AAPL data should be valid (not stale)"
        assert data['trend_1y'] == 2.23, "Trend_1y should be 2.23"

        # Test stale data
        cache_stale = TrendCache(cache_path, ttl_seconds=0)  # 0 second TTL = always stale
        data_stale = cache_stale.get('AAPL')
        assert data_stale is None, "Stale data should return None"

        print("✓ Cache loading test passed")

    finally:
        os.unlink(cache_path)


def test_cache_set_and_save():
    """Test cache set and save functionality"""
    from Trend_Verifier import TrendCache

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as f:
        cache_path = f.name

    try:
        # Create cache and add data
        cache = TrendCache(cache_path, ttl_seconds=3600)
        cache.set('TSLA', 1.23, 0.45, -0.67, 252)

        assert 'TSLA' in cache.cache, "TSLA should be in cache"
        assert cache.cache['TSLA']['trend_1y'] == 1.23, "Trend_1y should be 1.23"

        # Save cache
        cache.save()

        # Load cache in new instance
        cache2 = TrendCache(cache_path, ttl_seconds=3600)
        assert 'TSLA' in cache2.cache, "TSLA should persist after reload"
        assert cache2.cache['TSLA']['trend_6m'] == 0.45, "Trend_6m should be 0.45"

        print("✓ Cache set and save test passed")

    finally:
        if os.path.exists(cache_path):
            os.unlink(cache_path)


def test_gtt_column_insertion():
    """Test that trend columns are inserted at the correct position"""
    # Create mock GTT file
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt', newline='') as f:
        gtt_path = f.name
        writer = csv.writer(f, delimiter='\t')

        # Header without trend columns
        header = ['Symbol', 'CP-A(t)', 'CP-A($)', 'BorrowFee', 'BF1', 'BF2', 'BF_BEST', 'LastUpdate']
        writer.writerow(header)

        # Sample row
        writer.writerow(['AAPL', '2025-11-24/09:30', '150.25', '0.25', '0.25', '0.30', '0.25', '2025-11-24/10:30'])

    try:
        # Read and check column insertion logic
        with open(gtt_path, 'r') as f:
            reader = csv.reader(f, delimiter='\t')
            header = next(reader)

        # Simulate column insertion (as done in Trend_Verifier.py)
        if 'LastUpdate' in header:
            last_update_idx = header.index('LastUpdate')
        else:
            last_update_idx = len(header)

        # Insert trend columns before LastUpdate
        header.insert(last_update_idx, '3moT')
        header.insert(last_update_idx, '6moT')
        header.insert(last_update_idx, '1yrT')

        # Verify positions
        assert header.index('BF_BEST') < header.index('1yrT'), "BF_BEST should come before 1yrT"
        assert header.index('1yrT') < header.index('LastUpdate'), "1yrT should come before LastUpdate"
        assert header.index('6moT') < header.index('LastUpdate'), "6moT should come before LastUpdate"
        assert header.index('3moT') < header.index('LastUpdate'), "3moT should come before LastUpdate"

        # Verify order of trend columns
        assert header.index('1yrT') < header.index('6moT'), "1yrT should come before 6moT"
        assert header.index('6moT') < header.index('3moT'), "6moT should come before 3moT"

        print("✓ GTT column insertion test passed")

    finally:
        os.unlink(gtt_path)


def test_trend_calculation():
    """Test trend slope calculation logic"""
    import numpy as np

    # Mock daily bars (simulating upward trend)
    bars = [
        {'date': datetime.datetime(2024, 1, i), 'close': 100 + i * 0.5}
        for i in range(1, 253)  # 252 trading days
    ]

    # Extract closes
    close_prices = np.array([bar['close'] for bar in bars])

    # Calculate 1-year slope
    x = np.arange(len(close_prices))
    slope, _ = np.polyfit(x, close_prices, 1)
    slope_rounded = round(slope, 2)

    # Verify slope is positive (upward trend)
    assert slope_rounded > 0, "Upward trend should have positive slope"
    assert slope_rounded == 0.5, f"Expected slope 0.50, got {slope_rounded}"

    print("✓ Trend calculation test passed")


def test_cli_argument_parsing():
    """Test that CLI argument validation works"""
    import subprocess

    script_path = Path(__file__).parent / "Trend_Verifier.py"

    # Test missing required arguments
    result = subprocess.run(
        ["python3", str(script_path)],
        capture_output=True,
        text=True
    )

    assert result.returncode != 0, "Script should fail without required arguments"
    assert "required" in result.stderr.lower() or "error" in result.stderr.lower(), \
        "Error message should mention required arguments"

    print("✓ CLI argument validation test passed")


def run_all_tests():
    """Run all tests"""
    print("="*60)
    print("Running Trend Verifier Test Suite")
    print("="*60)

    tests = [
        test_cli_help,
        test_cache_loading,
        test_cache_set_and_save,
        test_gtt_column_insertion,
        test_trend_calculation,
        test_cli_argument_parsing
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except AssertionError as e:
            print(f"✗ {test.__name__} failed: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ {test.__name__} error: {e}")
            failed += 1

    print("="*60)
    print(f"Tests passed: {passed}/{len(tests)}")
    print(f"Tests failed: {failed}/{len(tests)}")
    print("="*60)

    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)
