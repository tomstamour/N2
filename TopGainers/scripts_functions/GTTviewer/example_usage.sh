#!/bin/bash
# GTTviewer - Example Usage Script
# Demonstrates various ways to use GTTviewer

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${GREEN}GTTviewer Example Usage${NC}"
echo "========================================"
echo ""

# Find the most recent GTT file
GTT_FILE=$(find ../../runs -name "GusTradeTable*.txt" -type f -printf '%T@ %p\n' | sort -rn | head -1 | cut -d' ' -f2-)

if [ -z "$GTT_FILE" ]; then
    echo -e "${YELLOW}No GTT file found in runs directory${NC}"
    echo "Please run the Gainers Scanner Script first to generate a GTT file"
    exit 1
fi

echo -e "Using GTT file: ${GREEN}$GTT_FILE${NC}"
echo ""

# Function to run example
run_example() {
    local description="$1"
    local command="$2"

    echo -e "${YELLOW}Example: $description${NC}"
    echo "Command: $command"
    echo ""
    echo "Press Enter to run this example (Ctrl+C to skip)..."
    read -r

    eval "$command"

    echo ""
    echo "---"
    echo ""
}

# Example 1: Basic usage
run_example "Basic Usage - Default view with 5-second refresh" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\""

# Example 2: Compact mode
run_example "Compact Mode - Minimal display, top 10 stocks" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --display-mode compact --max-rows 10"

# Example 3: Fast refresh with filtering
run_example "Fast Refresh - 2-second updates, stocks above 10% gain" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --refresh-interval 2 --filter-min-gain 10"

# Example 4: Easy-to-borrow stocks only
run_example "Easy Shorts - Only stocks with 'Easy' borrow status" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --filter-borrow Easy"

# Example 5: Custom columns
run_example "Custom Columns - Show only Symbol, Gain%, and Borrow Status" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --show-columns Symbol,live_percentage,Gain_Percent,Borrow_Status"

# Example 6: Detailed mode (if FDF enriched)
run_example "Detailed Mode - All columns including FDF enrichment" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --display-mode detailed --max-rows 10"

# Example 7: Sort by different column
run_example "Sort by Gain at Threshold - Show stocks sorted by Gain_Percent" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --sort-by Gain_Percent --max-rows 15"

# Example 8: Multiple filters
run_example "Multi-Filter - Gains between 10-50%, Easy borrow only" \
    "python3 GTTviewer.py --input-GTT \"$GTT_FILE\" --filter-min-gain 10 --filter-max-gain 50 --filter-borrow Easy"

echo -e "${GREEN}All examples completed!${NC}"
echo ""
echo "Tips:"
echo "  - Press Ctrl+C to exit the viewer at any time"
echo "  - Use --help to see all available options"
echo "  - Combine options to create your perfect view"
echo ""
