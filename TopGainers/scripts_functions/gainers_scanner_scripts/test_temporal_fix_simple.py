#!/usr/bin/env python3
"""
Simple standalone test to verify temporal filtering logic works correctly.

Tests the fix for the bug where Checkpoint-B could be detected BEFORE time0.
Bug reference: INHD false positive (CP-B at 12:24 PM before time0 at 4:00 PM)
"""

import datetime


def test_bars_with_pre_time0_spike():
    """Test Case 1: Bars with pre-time0 spike (INHD scenario)."""
    print("\n=== Test 1: Bars with pre-time0 spike ===")

    time0 = datetime.datetime(2025, 11, 24, 16, 0, 0)  # Nov 24, 4:00 PM

    # Create mock bars: spike at 12:24 PM ($3.45), lower prices after 4:00 PM
    bars = [
        {'date': datetime.datetime(2025, 11, 24, 12, 24, 0), 'high': 3.45},  # BEFORE time0
        {'date': datetime.datetime(2025, 11, 24, 16, 1, 0), 'high': 0.57},   # AFTER time0
        {'date': datetime.datetime(2025, 11, 24, 16, 5, 0), 'high': 0.58},
        {'date': datetime.datetime(2025, 11, 24, 16, 10, 0), 'high': 0.60},
    ]

    print(f"Total bars: {len(bars)}")
    print(f"Pre-time0 spike: ${bars[0]['high']} at {bars[0]['date']}")

    # Apply the FIX: Filter bars to only include those AFTER time0
    valid_bars = [b for b in bars if b['date'] > time0]

    print(f"Valid bars after filtering: {len(valid_bars)}/{len(bars)}")

    # Find peak
    if valid_bars:
        peak_bar = max(valid_bars, key=lambda b: b['high'])
        print(f"Peak after time0: ${peak_bar['high']:.2f} at {peak_bar['date']}")

        # Verify
        assert peak_bar['high'] == 0.60, "Should select $0.60 peak after time0"
        assert peak_bar['high'] != 3.45, "Should NOT select pre-time0 spike"
        print("✓ PASS: Correctly filtered out pre-time0 spike")
    else:
        print("✗ FAIL: No valid bars found")
        return False

    return True


def test_no_bars_after_time0():
    """Test Case 2: All bars before time0."""
    print("\n=== Test 2: All bars before time0 ===")

    time0 = datetime.datetime(2025, 11, 24, 16, 0, 0)

    # All bars before time0
    bars = [
        {'date': datetime.datetime(2025, 11, 24, 12, 0, 0), 'high': 3.45},
        {'date': datetime.datetime(2025, 11, 24, 13, 0, 0), 'high': 3.50},
        {'date': datetime.datetime(2025, 11, 24, 14, 0, 0), 'high': 3.40},
    ]

    print(f"Total bars: {len(bars)}")
    print(f"All bars are before time0 ({time0})")

    # Apply the FIX
    valid_bars = [b for b in bars if b['date'] > time0]

    print(f"Valid bars after filtering: {len(valid_bars)}/{len(bars)}")

    # Verify
    assert len(valid_bars) == 0, "Should have 0 bars after time0"
    print("✓ PASS: Correctly identified no valid bars")

    return True


def test_bar_exactly_at_time0():
    """Test Case 3: Bar at exactly time0."""
    print("\n=== Test 3: Bar exactly at time0 ===")

    time0 = datetime.datetime(2025, 11, 24, 16, 0, 0)

    bars = [
        {'date': datetime.datetime(2025, 11, 24, 15, 59, 0), 'high': 0.55},  # Before
        {'date': datetime.datetime(2025, 11, 24, 16, 0, 0), 'high': 0.57},   # EXACTLY at time0
        {'date': datetime.datetime(2025, 11, 24, 16, 1, 0), 'high': 0.58},   # After
    ]

    print(f"Total bars: {len(bars)}")
    print(f"Bar at exact time0: ${bars[1]['high']} at {bars[1]['date']}")

    # Apply the FIX (using > not >=)
    valid_bars = [b for b in bars if b['date'] > time0]

    print(f"Valid bars after filtering: {len(valid_bars)}/{len(bars)}")

    # Verify
    assert len(valid_bars) == 1, "Should have 1 bar after time0"
    assert valid_bars[0]['high'] == 0.58, "Should only include bar after time0"
    assert bars[1] not in valid_bars, "Bar at exact time0 should be excluded"
    print("✓ PASS: Correctly excluded bar at exact time0")

    return True


def test_mixed_bars_realistic():
    """Test Case 4: Realistic 928 bars scenario."""
    print("\n=== Test 4: Realistic 928 bars (INHD-like scenario) ===")

    time0 = datetime.datetime(2025, 11, 24, 16, 0, 0)

    bars = []

    # Add 500 bars before time0
    start_time = datetime.datetime(2025, 11, 24, 6, 0, 0)
    for i in range(500):
        bar_time = start_time + datetime.timedelta(minutes=i)
        bars.append({'date': bar_time, 'high': 0.50 + (i * 0.01)})

    # Add spike at 12:24 PM (before time0)
    bars.append({'date': datetime.datetime(2025, 11, 24, 12, 24, 0), 'high': 10.00})

    # Add 428 bars after time0
    start_time_after = datetime.datetime(2025, 11, 24, 16, 1, 0)
    for i in range(428):
        bar_time = start_time_after + datetime.timedelta(minutes=i)
        bars.append({'date': bar_time, 'high': 0.55 + (i * 0.001)})

    print(f"Total bars: {len(bars)}")
    print(f"Pre-time0 spike: $10.00 at 12:24 PM")

    # Apply the FIX
    valid_bars = [b for b in bars if b['date'] > time0]

    print(f"Valid bars after filtering: {len(valid_bars)}/{len(bars)}")

    # Find peak
    if valid_bars:
        peak_bar = max(valid_bars, key=lambda b: b['high'])
        print(f"Peak after time0: ${peak_bar['high']:.2f}")

        # Verify
        assert len(valid_bars) == 428, "Should have 428 bars after time0"
        assert peak_bar['high'] < 1.0, "Peak after time0 should be < 1.0"
        assert peak_bar['high'] != 10.00, "Should NOT select pre-time0 spike of 10.00"
        print("✓ PASS: Correctly handled 928 bars with pre-time0 spike")
    else:
        print("✗ FAIL: No valid bars found")
        return False

    return True


def test_checkpoint_ordering_validation():
    """Test Case 5: Checkpoint ordering validation."""
    print("\n=== Test 5: Checkpoint ordering validation ===")

    # Valid ordering
    cp_a_time = "2025-11-24/16:00"
    cp_b_time = "2025-11-25/04:56"

    cp_a_dt = datetime.datetime.strptime(cp_a_time, '%Y-%m-%d/%H:%M')
    cp_b_dt = datetime.datetime.strptime(cp_b_time, '%Y-%m-%d/%H:%M')

    print(f"Testing valid ordering: CP-A={cp_a_time}, CP-B={cp_b_time}")
    assert cp_b_dt > cp_a_dt, "Valid ordering should pass"
    print("✓ PASS: Valid checkpoint ordering accepted")

    # Invalid ordering (INHD case)
    cp_a_time_invalid = "2025-11-24/16:00"
    cp_b_time_invalid = "2025-11-24/12:24"

    cp_a_dt_invalid = datetime.datetime.strptime(cp_a_time_invalid, '%Y-%m-%d/%H:%M')
    cp_b_dt_invalid = datetime.datetime.strptime(cp_b_time_invalid, '%Y-%m-%d/%H:%M')

    print(f"Testing invalid ordering: CP-A={cp_a_time_invalid}, CP-B={cp_b_time_invalid}")
    is_valid = cp_b_dt_invalid > cp_a_dt_invalid
    assert not is_valid, "Invalid ordering should be detected"
    print("✓ PASS: Invalid checkpoint ordering rejected")

    return True


def main():
    """Run all tests."""
    print("="*60)
    print("CHECKPOINT TEMPORAL VALIDATION FIX - TEST SUITE")
    print("="*60)

    tests = [
        test_bars_with_pre_time0_spike,
        test_no_bars_after_time0,
        test_bar_exactly_at_time0,
        test_mixed_bars_realistic,
        test_checkpoint_ordering_validation,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except AssertionError as e:
            print(f"✗ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"✗ ERROR: {e}")
            failed += 1

    print("\n" + "="*60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("="*60)

    return 0 if failed == 0 else 1


if __name__ == '__main__':
    import sys
    sys.exit(main())
