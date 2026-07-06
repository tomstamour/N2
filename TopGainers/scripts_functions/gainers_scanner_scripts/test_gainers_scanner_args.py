#!/usr/bin/env python3
"""
Test script to verify Gainers Scanner argument parsing
"""

import sys
from pathlib import Path

# Add parent directory to path to import the scanner
sys.path.insert(0, str(Path(__file__).parent))

def test_argument_parsing():
    """Test that argument parsing works correctly."""
    print("Testing Gainers Scanner Script argument parsing...\n")

    # Test 1: Import the module
    try:
        import Gainers_Scanner_Script as gss
        print("✓ Successfully imported Gainers_Scanner_Script")
    except Exception as e:
        print(f"✗ Failed to import: {e}")
        return False

    # Test 2: Test argument parser with sample args
    try:
        # Simulate command-line arguments
        test_args = [
            'Gainers_Scanner_Script.py',
            '--output', 'test_output.csv',
            '--gap-percentage', '50',
            '--client-id', '300',
            '--host', '127.0.0.1',
            '--port', '4001',
            '--log-dir', './test_logs',
            '--max-stocks', '25',
            '--min-price', '10.0',
            '--refresh-interval', '5',
            '--no-new-window'
        ]

        # Temporarily replace sys.argv
        original_argv = sys.argv
        sys.argv = test_args

        args = gss.parse_arguments()

        # Restore original argv
        sys.argv = original_argv

        print("✓ Arguments parsed successfully")
        print(f"  - Output: {args.output}")
        print(f"  - Gap percentage: {args.gap_percentage}")
        print(f"  - Client ID: {args.client_id}")
        print(f"  - Host: {args.host}")
        print(f"  - Port: {args.port}")
        print(f"  - Log dir: {args.log_dir}")
        print(f"  - Max stocks: {args.max_stocks}")
        print(f"  - Min price: {args.min_price}")
        print(f"  - Refresh interval: {args.refresh_interval}")
        print(f"  - No new window: {args.no_new_window}")

        # Verify values
        assert args.output == 'test_output.csv', "Output path mismatch"
        assert args.gap_percentage == 50, "Gap percentage mismatch"
        assert args.client_id == 300, "Client ID mismatch"
        assert args.host == '127.0.0.1', "Host mismatch"
        assert args.port == 4001, "Port mismatch"
        assert args.log_dir == './test_logs', "Log dir mismatch"
        assert args.max_stocks == 25, "Max stocks mismatch"
        assert args.min_price == 10.0, "Min price mismatch"
        assert args.refresh_interval == 5, "Refresh interval mismatch"
        assert args.no_new_window == True, "No new window flag mismatch"

        print("✓ All argument values verified correctly")

    except Exception as e:
        print(f"✗ Argument parsing failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Test 3: Test with minimal required args
    try:
        test_args_minimal = [
            'Gainers_Scanner_Script.py',
            '--output', 'minimal.csv',
            '--gap-percentage', '25',
            '--client-id', '301'
        ]

        original_argv = sys.argv
        sys.argv = test_args_minimal

        args = gss.parse_arguments()

        sys.argv = original_argv

        print("✓ Minimal arguments parsed successfully")
        print(f"  - Using defaults: host={args.host}, port={args.port}, refresh={args.refresh_interval}")

        assert args.host == '127.0.0.1', "Default host incorrect"
        assert args.port == 4001, "Default port incorrect"
        assert args.refresh_interval == 3, "Default refresh interval incorrect"

    except Exception as e:
        print(f"✗ Minimal argument parsing failed: {e}")
        return False

    print("\n" + "="*60)
    print("All tests passed! ✓")
    print("="*60)
    print("\nThe refactored Gainers Scanner Script is ready to use.")
    print("\nExample command:")
    print("  python Gainers_Scanner_Script.py --output gainers.csv --gap-percentage 50 --client-id 300")
    print("\nFor full documentation, see: GAINERS_SCANNER_README.md")

    return True

if __name__ == "__main__":
    success = test_argument_parsing()
    sys.exit(0 if success else 1)
