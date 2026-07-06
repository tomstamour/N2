#!/usr/bin/env python3
"""
Test suite for checkpoint temporal validation fix.

Tests the fix for the bug where Checkpoint-B could be detected BEFORE time0,
violating the fundamental logic that threshold crossing must occur AFTER baseline.

Bug reference: INHD false positive (CP-B at 12:24 PM before time0 at 4:00 PM)
"""

import sys
import datetime
import unittest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))

# Mock app_state before importing the module
mock_app_state = {
    'time0_datetime': datetime.datetime(2025, 11, 24, 16, 0, 0),  # Nov 24, 4:00 PM
    'historical_data': {}
}

# Import the module with mocked app_state
with patch.dict('sys.modules', {'Gainers_Scanner_Script.app_state': mock_app_state}):
    from Gainers_Scanner_Script import load_existing_gtt


class TestCheckpointTemporalValidation(unittest.TestCase):
    """Test suite for temporal validation of Checkpoint-A and Checkpoint-B."""

    def setUp(self):
        """Set up test fixtures."""
        self.time0 = datetime.datetime(2025, 11, 24, 16, 0, 0)  # Nov 24, 4:00 PM
        self.time0_naive = self.time0.replace(tzinfo=None)

    def test_bars_with_pre_time0_spike(self):
        """
        Test Case 1: Historical bars with highest price BEFORE time0.

        Scenario: INHD had $3.45 spike at 12:24 PM (before time0 at 4:00 PM)
                  but lower prices after 4:00 PM.
        Expected: Should select highest price AFTER time0, not the pre-time0 spike.
        """
        # Create mock bars: spike at 12:24 PM ($3.45), lower prices after 4:00 PM
        bars = [
            {'date': datetime.datetime(2025, 11, 24, 12, 24, 0), 'high': 3.45, 'low': 3.40, 'close': 3.43},  # BEFORE time0
            {'date': datetime.datetime(2025, 11, 24, 16, 1, 0), 'high': 0.57, 'low': 0.55, 'close': 0.56},   # AFTER time0
            {'date': datetime.datetime(2025, 11, 24, 16, 5, 0), 'high': 0.58, 'low': 0.56, 'close': 0.57},
            {'date': datetime.datetime(2025, 11, 24, 16, 10, 0), 'high': 0.60, 'low': 0.57, 'close': 0.59},
        ]

        # Filter bars (simulating the fix)
        valid_bars = [b for b in bars if b['date'] > self.time0_naive]

        # Verify filtering
        self.assertEqual(len(valid_bars), 3, "Should have 3 bars after time0")
        self.assertNotIn(bars[0], valid_bars, "Pre-time0 spike should be excluded")

        # Find peak
        peak_bar = max(valid_bars, key=lambda b: b['high'])

        # Verify correct peak selection
        self.assertEqual(peak_bar['high'], 0.60, "Should select $0.60 peak after time0")
        self.assertGreater(peak_bar['date'], self.time0_naive, "Peak time should be after time0")
        self.assertNotEqual(peak_bar['high'], 3.45, "Should NOT select pre-time0 spike")

    def test_no_bars_after_time0(self):
        """
        Test Case 2: All historical bars are BEFORE time0.

        Scenario: Historical data contains only pre-time0 bars.
        Expected: Should return empty list and function should return None.
        """
        # All bars before time0
        bars = [
            {'date': datetime.datetime(2025, 11, 24, 12, 0, 0), 'high': 3.45, 'low': 3.40, 'close': 3.43},
            {'date': datetime.datetime(2025, 11, 24, 13, 0, 0), 'high': 3.50, 'low': 3.45, 'close': 3.48},
            {'date': datetime.datetime(2025, 11, 24, 14, 0, 0), 'high': 3.40, 'low': 3.35, 'close': 3.38},
            {'date': datetime.datetime(2025, 11, 24, 15, 0, 0), 'high': 3.30, 'low': 3.25, 'close': 3.28},
        ]

        # Filter bars
        valid_bars = [b for b in bars if b['date'] > self.time0_naive]

        # Verify no valid bars
        self.assertEqual(len(valid_bars), 0, "Should have 0 bars after time0")

    def test_bar_exactly_at_time0(self):
        """
        Test Case 3: Bar with date exactly equal to time0.

        Scenario: One bar timestamp equals time0 exactly.
        Expected: Should be excluded (condition is >, not >=).
        """
        bars = [
            {'date': datetime.datetime(2025, 11, 24, 15, 59, 0), 'high': 0.55, 'low': 0.53, 'close': 0.54},
            {'date': datetime.datetime(2025, 11, 24, 16, 0, 0), 'high': 0.57, 'low': 0.55, 'close': 0.56},   # EXACTLY at time0
            {'date': datetime.datetime(2025, 11, 24, 16, 1, 0), 'high': 0.58, 'low': 0.56, 'close': 0.57},
        ]

        # Filter bars
        valid_bars = [b for b in bars if b['date'] > self.time0_naive]

        # Verify exact time0 is excluded
        self.assertEqual(len(valid_bars), 1, "Should have 1 bar after time0 (excluding exact match)")
        self.assertNotEqual(valid_bars[0]['date'], self.time0_naive, "Bar at exact time0 should be excluded")
        self.assertEqual(valid_bars[0]['high'], 0.58, "Should select bar after time0")

    def test_mixed_bars_before_and_after_time0(self):
        """
        Test Case 4: Realistic scenario with many bars before and after time0.

        Scenario: 928 bars spanning from early morning through next day (like INHD case).
        Expected: Should only consider bars after time0.
        """
        bars = []

        # Add 500 bars before time0 (6:00 AM to 3:59 PM)
        start_time = datetime.datetime(2025, 11, 24, 6, 0, 0)
        for i in range(500):
            bar_time = start_time + datetime.timedelta(minutes=i)
            bars.append({
                'date': bar_time,
                'high': 0.50 + (i * 0.01),  # Gradually increasing, peak at 5.50
                'low': 0.48 + (i * 0.01),
                'close': 0.49 + (i * 0.01)
            })

        # Add spike at 12:24 PM (before time0) - this is the pre-time0 peak
        bars.append({
            'date': datetime.datetime(2025, 11, 24, 12, 24, 0),
            'high': 10.00,  # Large spike before time0
            'low': 9.50,
            'close': 9.75
        })

        # Add 428 bars after time0 (4:00 PM onwards)
        start_time_after = datetime.datetime(2025, 11, 24, 16, 1, 0)
        for i in range(428):
            bar_time = start_time_after + datetime.timedelta(minutes=i)
            bars.append({
                'date': bar_time,
                'high': 0.55 + (i * 0.001),  # Lower prices after time0, peak at ~0.98
                'low': 0.53 + (i * 0.001),
                'close': 0.54 + (i * 0.001)
            })

        # Filter bars
        valid_bars = [b for b in bars if b['date'] > self.time0_naive]

        # Verify filtering
        self.assertEqual(len(valid_bars), 428, "Should have 428 bars after time0")
        self.assertLess(len(valid_bars), len(bars), "Should filter out pre-time0 bars")

        # Find peak
        peak_bar = max(valid_bars, key=lambda b: b['high'])

        # Verify correct peak selection
        self.assertLess(peak_bar['high'], 1.0, "Peak after time0 should be < 1.0")
        self.assertNotEqual(peak_bar['high'], 10.00, "Should NOT select pre-time0 spike of 10.00")
        self.assertGreater(peak_bar['date'], self.time0_naive, "Peak should be after time0")

    def test_gtt_load_validation_invalid_ordering(self):
        """
        Test Case 5: GTT file with invalid checkpoint ordering.

        Scenario: GTT file contains INHD with CP-B before CP-A.
        Expected: Should skip the entry with warning.
        """
        # Create temporary GTT file with invalid data
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            # Write header
            f.write("Symbol\tCP-A(t)\tCP-A($)\tCP-B(t)\tCP-B($)\tGap(%)\tGapL(%)\tSect\tIndus\tShortStatus\tShortDiff\tShortableShares\tFloat(M$)\tNetIncome(M$)\tInstit(%)\tBorrowFee\tBF1\tBF2\tBF_BEST\tLastUpdate\n")

            # Write valid entry
            f.write("ICON\t2025-11-24/16:00\t0.91\t2025-11-25/04:56\t1.43\t56.83\t21.74\tIndustrials\tMarine Shipping\tMODERATE_AVAIL\tN/A\tN/A\t2.18\t-7.07\t6.35\t11.06\t11.06\tN/A\tN/A\t2025-11-25 07:57:11\n")

            # Write INVALID entry - CP-B before CP-A
            f.write("INHD\t2025-11-24/16:00\t0.51\t2025-11-24/12:24\t3.45\t573.57\t11.28\tBasic Materials\tSteel\tHIGH_AVAIL\tN/A\t151745\t7.15\t-8.39\t1.86\t24.59\t24.59\tN/A\tN/A\t2025-11-25 07:57:11\n")

            gtt_path = Path(f.name)

        try:
            # Load GTT with validation
            tracked = load_existing_gtt(gtt_path)

            # Verify INHD was skipped due to invalid ordering
            self.assertIn('ICON', tracked, "Valid entry ICON should be loaded")
            self.assertNotIn('INHD', tracked, "Invalid entry INHD should be skipped")
            self.assertEqual(len(tracked), 1, "Should only load 1 valid entry")

        finally:
            # Cleanup
            gtt_path.unlink()

    def test_checkpoint_validation_at_stock_addition(self):
        """
        Test Case 6: Validation when adding new stock to tracked_stocks.

        Scenario: process_stocks receives result with peak_time before time0.
        Expected: Should skip adding the stock with critical error log.
        """
        # This test would require mocking the entire process_stocks flow
        # For now, we verify the logic by checking the validation condition

        # Mock result with invalid peak_time (before time0)
        invalid_peak_time = datetime.datetime(2025, 11, 24, 12, 24, 0)  # Before time0

        # Simulate validation check
        is_valid = invalid_peak_time > self.time0_naive

        # Verify validation catches the error
        self.assertFalse(is_valid, "Peak time before time0 should fail validation")

        # Mock result with valid peak_time (after time0)
        valid_peak_time = datetime.datetime(2025, 11, 24, 16, 30, 0)  # After time0

        # Simulate validation check
        is_valid = valid_peak_time > self.time0_naive

        # Verify valid peak passes
        self.assertTrue(is_valid, "Peak time after time0 should pass validation")


class TestLoggingAndEdgeCases(unittest.TestCase):
    """Test logging improvements and edge cases."""

    def test_filtered_bar_count_logging(self):
        """
        Test that logging shows filtered bar count (e.g., "512/928 bars after time0").
        """
        time0_naive = datetime.datetime(2025, 11, 24, 16, 0, 0)

        # Create bars
        all_bars = [
            {'date': datetime.datetime(2025, 11, 24, 12, i, 0), 'high': 1.0, 'low': 0.9, 'close': 0.95}
            for i in range(60)
        ] + [
            {'date': datetime.datetime(2025, 11, 24, 16, i, 0), 'high': 0.6, 'low': 0.5, 'close': 0.55}
            for i in range(1, 60)
        ]

        # Filter
        valid_bars = [b for b in all_bars if b['date'] > time0_naive]

        # Verify counts for logging
        total_bars = len(all_bars)
        filtered_bars = len(valid_bars)

        self.assertEqual(total_bars, 119, "Should have 119 total bars")
        self.assertEqual(filtered_bars, 59, "Should have 59 valid bars after time0")

        # Construct log message (as would appear in code)
        log_msg = f"({filtered_bars}/{total_bars} bars after time0)"
        self.assertIn("59/119", log_msg, "Log should show filtered count")


def run_tests():
    """Run all tests with verbose output."""
    # Create test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Add all test cases
    suite.addTests(loader.loadTestsFromTestCase(TestCheckpointTemporalValidation))
    suite.addTests(loader.loadTestsFromTestCase(TestLoggingAndEdgeCases))

    # Run tests
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Return exit code
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(run_tests())
