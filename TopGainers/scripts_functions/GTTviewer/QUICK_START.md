# GTTviewer - Quick Start Guide

## Instant Usage

```bash
# Basic usage - view any GTT file
python3 GTTviewer.py --input-GTT /path/to/GusTradeTable.txt

# View today's GTT file
python3 GTTviewer.py --input-GTT ./runs/$(date +%d-%b-%Y)/GusTradeTable_$(date +%dmm%Y).txt
```

## Most Common Commands

### 1. Live Monitoring (Fast Updates)
```bash
python3 GTTviewer.py --input-GTT <file> --refresh-interval 2
```

### 2. Top Gainers (Top 20)
```bash
python3 GTTviewer.py --input-GTT <file> --max-rows 20 --sort-by live_percentage
```

### 3. Easy-to-Borrow Stocks Only
```bash
python3 GTTviewer.py --input-GTT <file> --filter-borrow Easy
```

### 4. High Gainers (>20%)
```bash
python3 GTTviewer.py --input-GTT <file> --filter-min-gain 20
```

### 5. Compact View (More Rows)
```bash
python3 GTTviewer.py --input-GTT <file> --display-mode compact
```

## Display Modes

| Mode | When to Use |
|------|-------------|
| `compact` | Quick overview, small terminal, many stocks |
| `normal` | Default, balanced view - **auto-includes FDF columns if present** |
| `detailed` | All columns including financial metrics (Net_Income_TTM, Revenue_TTM, etc.) |

## Color Guide

### Gain Percentages
- **White**: 0-5% (low)
- **Green**: 5-15% (moderate)
- **Bright Green**: 15-30% (good)
- **Yellow**: 30-50% (high)
- **Orange**: 75-100% (extreme)
- **Red**: >100% (extraordinary)

### Borrow Status
- **Green**: Easy
- **Yellow**: Available
- **Red**: Hard
- **Gray**: N/A

### Timestamp
- **Green**: <1 min (fresh)
- **White**: 1-5 min
- **Yellow**: 5-15 min
- **Red**: >30 min (stale)

## Quick Filter Combos

### Conservative Shorts
```bash
python3 GTTviewer.py --input-GTT <file> \
  --filter-borrow Easy \
  --filter-min-gain 15 \
  --filter-max-gain 50
```

### Extreme Gainers Watch
```bash
python3 GTTviewer.py --input-GTT <file> \
  --filter-min-gain 50 \
  --sort-by live_percentage \
  --max-rows 10
```

### Fundamental Analysis (FDF enriched)
```bash
python3 GTTviewer.py --input-GTT <file> \
  --display-mode detailed \
  --show-columns "Symbol,live_percentage,Float,Institutional_Pct,Short_Interest"
```

## Keyboard Shortcuts

- **Ctrl+C**: Exit gracefully

## Tips

1. **Multiple Windows**: Open multiple viewers with different filters in separate terminals
2. **File Path**: Use tab-completion or drag-and-drop for file paths
3. **Refresh Rate**: Lower = faster updates, higher = less CPU usage
4. **Max Rows**: Use `--max-rows 10` for focused view
5. **Logging**: Add `--log viewer.log` to troubleshoot issues

## Integration with Pipeline

```bash
# 1. Run Gainers Scanner
python3 scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "$(date +%Y-%m-%d)/09:30" \
  --gap-percentage 5 \
  --client-id 300 \
  --output-dir ./runs/$(date +%d-%b-%Y)

# 2. Open GTTviewer in another terminal (while scanner runs)
python3 scripts_functions/GTTviewer/GTTviewer.py \
  --input-GTT ./runs/$(date +%d-%b-%Y)/GusTradeTable_$(date +%dmm%Y).txt \
  --refresh-interval 3

# 3. After FDF enrichment, view detailed data
python3 scripts_functions/GTTviewer/GTTviewer.py \
  --input-GTT ./runs/$(date +%d-%b-%Y)/GusTradeTable_$(date +%dmm%Y).txt \
  --display-mode detailed
```

## Examples Script

Run the interactive examples:
```bash
cd scripts_functions/GTTviewer
./example_usage.sh
```

## Help

```bash
python3 GTTviewer.py --help
```

For full documentation, see **GTTVIEWER_README.md**
