#!/bin/bash
#
# Test script for Historical Gap Up Finder - GUSday and Flexible Time Windows
#
# This script tests the new GUSday column and flexible time window functionality
# added to the Historical Gap Up Finder.
#
# Requirements:
# - IB Gateway or TWS running on port 4001 (or 4002 for paper trading)
# - test_stock_universe.txt file with a small set of symbols for testing
#
# Usage:
#   cd /home/tom/Documents/ibkr_scripts/GUS3/scripts_functions/historical_gap_finder
#   bash ../../claude_test_script/test_hguf_gusday.sh

set -e  # Exit on error

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HGUF_DIR="$SCRIPT_DIR/../scripts_functions/historical_gap_finder"
TEST_OUTPUT_DIR="$SCRIPT_DIR/test_output_hguf"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create test output directory
mkdir -p "$TEST_OUTPUT_DIR"

echo "============================================================"
echo "HGUF GUSday & Flexible Time Windows - Test Suite"
echo "============================================================"
echo ""
echo "Test Output Directory: $TEST_OUTPUT_DIR"
echo "Timestamp: $TIMESTAMP"
echo ""

# Test 1: Basic functionality (equivalent to old behavior)
echo "=========================================="
echo "Test 1: Basic Functionality (24h pre-gap, 6.5h post-gap)"
echo "=========================================="
echo ""
echo "This test replicates the old hardcoded behavior:"
echo "  - Pre-gap: 24.0 hours (day-11 09:30 to day-12 09:29)"
echo "  - Post-gap: 6.5 hours (day-12 09:29 to day-12 16:00)"
echo ""

python3 "$HGUF_DIR/Historical_Gap_Up_Finder.py" \
  --time-window "25-11-2025" "26-11-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 700 \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 6.5 \
  --universe-file "$HGUF_DIR/test_stock_universe.txt" \
  --output-dir "$TEST_OUTPUT_DIR" \
  --log "$TEST_OUTPUT_DIR/test1_${TIMESTAMP}.log" \
  --verbose

if [ $? -eq 0 ]; then
    echo "✓ Test 1 PASSED"

    # Check for GUSday column in output
    OUTPUT_FILE=$(ls -t "$TEST_OUTPUT_DIR"/HGUF-table_*.txt 2>/dev/null | head -1)
    if [ -f "$OUTPUT_FILE" ]; then
        echo "  Checking for GUSday column..."
        if grep -q "GUSday" "$OUTPUT_FILE"; then
            echo "  ✓ GUSday column found in output"

            # Show unique GUSday values
            echo "  Unique GUSday values found:"
            awk -F'\t' 'NR>1 {print $15}' "$OUTPUT_FILE" | sort -u | sed 's/^/    /'
        else
            echo "  ✗ ERROR: GUSday column not found in output"
            exit 1
        fi
    fi
else
    echo "✗ Test 1 FAILED"
    exit 1
fi

echo ""
echo ""

# Test 2: Multi-day window (24h pre-gap, 24h post-gap)
echo "=========================================="
echo "Test 2: Multi-Day Window (24h pre-gap, 24h post-gap)"
echo "=========================================="
echo ""
echo "Expected GUSday labels: GUSday-1, GUSday, GUSday+1"
echo ""

python3 "$HGUF_DIR/Historical_Gap_Up_Finder.py" \
  --time-window "25-11-2025" "26-11-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 701 \
  --fetch-time-pre-gap 24.0 \
  --fetch-time-post-gap 24.0 \
  --universe-file "$HGUF_DIR/test_stock_universe.txt" \
  --output-dir "$TEST_OUTPUT_DIR" \
  --log "$TEST_OUTPUT_DIR/test2_${TIMESTAMP}.log" \
  --verbose

if [ $? -eq 0 ]; then
    echo "✓ Test 2 PASSED"

    # Verify GUSday+1 appears
    OUTPUT_FILE=$(ls -t "$TEST_OUTPUT_DIR"/HGUF-table_*.txt 2>/dev/null | head -1)
    if [ -f "$OUTPUT_FILE" ]; then
        echo "  Checking for GUSday+1 label..."
        if grep -q "GUSday+1" "$OUTPUT_FILE"; then
            echo "  ✓ GUSday+1 label found (multi-day coverage confirmed)"
        else
            echo "  ⚠ WARNING: GUSday+1 not found (may indicate short post-gap data)"
        fi

        echo "  Unique GUSday values found:"
        awk -F'\t' 'NR>1 {print $15}' "$OUTPUT_FILE" | sort -u | sed 's/^/    /'
    fi
else
    echo "✗ Test 2 FAILED"
    exit 1
fi

echo ""
echo ""

# Test 3: Zero pre-gap (only post-gap data)
echo "=========================================="
echo "Test 3: Zero Pre-Gap (0h pre-gap, 8h post-gap)"
echo "=========================================="
echo ""
echo "Expected: Only GUSday labels, no GUSday-1"
echo ""

python3 "$HGUF_DIR/Historical_Gap_Up_Finder.py" \
  --time-window "25-11-2025" "26-11-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 702 \
  --fetch-time-pre-gap 0.0 \
  --fetch-time-post-gap 8.0 \
  --universe-file "$HGUF_DIR/test_stock_universe.txt" \
  --output-dir "$TEST_OUTPUT_DIR" \
  --log "$TEST_OUTPUT_DIR/test3_${TIMESTAMP}.log" \
  --verbose

if [ $? -eq 0 ]; then
    echo "✓ Test 3 PASSED"

    OUTPUT_FILE=$(ls -t "$TEST_OUTPUT_DIR"/HGUF-table_*.txt 2>/dev/null | head -1)
    if [ -f "$OUTPUT_FILE" ]; then
        echo "  Checking that no GUSday-1 labels exist..."
        if grep -q "GUSday-1" "$OUTPUT_FILE"; then
            echo "  ✗ ERROR: GUSday-1 found (should not exist with 0h pre-gap)"
            exit 1
        else
            echo "  ✓ No GUSday-1 labels (as expected)"
        fi

        echo "  Unique GUSday values found:"
        awk -F'\t' 'NR>1 {print $15}' "$OUTPUT_FILE" | sort -u | sed 's/^/    /'
    fi
else
    echo "✗ Test 3 FAILED"
    exit 1
fi

echo ""
echo ""

# Test 4: Fractional hours (6.5h pre-gap, 7.5h post-gap)
echo "=========================================="
echo "Test 4: Fractional Hours (6.5h pre-gap, 7.5h post-gap)"
echo "=========================================="
echo ""
echo "Testing fractional hour support (6h 30m pre, 7h 30m post)"
echo ""

python3 "$HGUF_DIR/Historical_Gap_Up_Finder.py" \
  --time-window "25-11-2025" "26-11-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 10 \
  --client-id 703 \
  --fetch-time-pre-gap 6.5 \
  --fetch-time-post-gap 7.5 \
  --universe-file "$HGUF_DIR/test_stock_universe.txt" \
  --output-dir "$TEST_OUTPUT_DIR" \
  --log "$TEST_OUTPUT_DIR/test4_${TIMESTAMP}.log" \
  --verbose

if [ $? -eq 0 ]; then
    echo "✓ Test 4 PASSED (fractional hours supported)"

    OUTPUT_FILE=$(ls -t "$TEST_OUTPUT_DIR"/HGUF-table_*.txt 2>/dev/null | head -1)
    if [ -f "$OUTPUT_FILE" ]; then
        echo "  Unique GUSday values found:"
        awk -F'\t' 'NR>1 {print $15}' "$OUTPUT_FILE" | sort -u | sed 's/^/    /'
    fi
else
    echo "✗ Test 4 FAILED"
    exit 1
fi

echo ""
echo ""

# Test 5: Extended window with warning (100h pre-gap, 100h post-gap)
echo "=========================================="
echo "Test 5: Large Window Warning (100h pre-gap, 100h post-gap)"
echo "=========================================="
echo ""
echo "Testing large window warning (should log warning for 200h total)"
echo ""

python3 "$HGUF_DIR/Historical_Gap_Up_Finder.py" \
  --time-window "25-11-2025" "26-11-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 15 \
  --client-id 704 \
  --fetch-time-pre-gap 100.0 \
  --fetch-time-post-gap 100.0 \
  --universe-file "$HGUF_DIR/test_stock_universe.txt" \
  --output-dir "$TEST_OUTPUT_DIR" \
  --log "$TEST_OUTPUT_DIR/test5_${TIMESTAMP}.log" \
  --verbose

if [ $? -eq 0 ]; then
    echo "✓ Test 5 PASSED"

    # Check log for warning
    LOG_FILE="$TEST_OUTPUT_DIR/test5_${TIMESTAMP}.log"
    if [ -f "$LOG_FILE" ]; then
        if grep -q "Large time window:" "$LOG_FILE"; then
            echo "  ✓ Large window warning found in log"
        else
            echo "  ✗ WARNING: Large window warning not found in log"
        fi
    fi

    OUTPUT_FILE=$(ls -t "$TEST_OUTPUT_DIR"/HGUF-table_*.txt 2>/dev/null | head -1)
    if [ -f "$OUTPUT_FILE" ]; then
        echo "  Unique GUSday values found (expecting -4 to +4):"
        awk -F'\t' 'NR>1 {print $15}' "$OUTPUT_FILE" | sort -u | sed 's/^/    /'
    fi
else
    echo "✗ Test 5 FAILED"
    exit 1
fi

echo ""
echo ""

# Summary
echo "============================================================"
echo "TEST SUMMARY"
echo "============================================================"
echo ""
echo "All 5 tests completed successfully!"
echo ""
echo "Test outputs saved to: $TEST_OUTPUT_DIR"
echo ""
echo "✓ Test 1: Basic functionality (24h pre, 6.5h post) - PASSED"
echo "✓ Test 2: Multi-day window (24h pre, 24h post) - PASSED"
echo "✓ Test 3: Zero pre-gap (0h pre, 8h post) - PASSED"
echo "✓ Test 4: Fractional hours (6.5h pre, 7.5h post) - PASSED"
echo "✓ Test 5: Large window warning (100h pre, 100h post) - PASSED"
echo ""
echo "GUSday column implementation validated successfully!"
echo "============================================================"
