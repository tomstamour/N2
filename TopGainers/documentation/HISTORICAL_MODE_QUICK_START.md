# Historical Mode - Quick Start Guide

## What is Historical Mode?

Historical Mode lets you run the Gainers Scanner Script on **weekends or after market hours** to analyze stocks from **previous trading days** without needing live market data.

## When to Use It

✅ **Use Historical Mode when:**
- Running on weekends (markets closed)
- Working after market hours
- Analyzing past trading days
- Developing/debugging without time pressure

❌ **Use Live Mode when:**
- Markets are open and you want real-time data
- You need borrow status and fee rates
- Running during trading hours for actual trading

## Basic Usage

Just add `--historical-mode` to your command:

```bash
python3 ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-20/16:00" \
  --gap-percentage 40 \
  --client-id 702 \
  --output-dir ./runs/20-Nov-2025/ \
  --historical-mode \
  --no-new-window
```

## How It Works

1. **Sets time window**: time0 → (time0 + 1 day) at 16:00 ET
   - Example: `2025-11-20/16:00 → 2025-11-21/16:00`

2. **Fetches historical data**: Gets price bars from IBKR for the entire window

3. **Finds peak prices**: Identifies highest price each stock reached

4. **Checks threshold**: Stocks that crossed your `--gap-percentage` are added to output

5. **Uses last bar**: Last historical bar's close price becomes the "current" price

## What You Get

✅ **Available in Historical Mode:**
- GusTradeTable with stocks that crossed threshold
- Checkpoint-A (time0) price
- Checkpoint-B (peak) price and time
- Peak gain percentage
- Live percentage (from last historical bar)
- Fundamental data (Float, Institutional %, Sector, Industry from yfinance)

❌ **Not Available (set to "N/A"):**
- Borrow Status
- Borrow Fee Rate
- Shortable Shares

## Quick Examples

### Weekend Analysis - Analyze Friday's Gap-Ups
```bash
# Run this on Saturday or Sunday
python3 ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-22/09:30" \
  --gap-percentage 30 \
  --client-id 702 \
  --output-dir ./runs/22-Nov-2025/ \
  --historical-mode \
  --no-new-window
```

### Small Test - Just 5 Stocks
```bash
# Faster execution for testing
python3 ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-21/10:00" \
  --gap-percentage 20 \
  --client-id 702 \
  --output-dir ./test/ \
  --historical-mode \
  --no-new-window \
  --max-stocks 5
```

### Your Original Failing Command - NOW WORKS!
```bash
python3 ./scripts_functions/gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-20/16:00" \
  --gap-percentage 40 \
  --client-id 702 \
  --log ./runs/22-Nov-2025/GGS_historical.log \
  --output-dir ./runs/22-Nov-2025/ \
  --historical-mode \
  --no-new-window
```

## Troubleshooting

### "No data available at time0"
- Some stocks don't have historical data at that time
- Try a different time0 or higher `--max-stocks`

### Empty table output
- Lower the `--gap-percentage` threshold
- Increase `--max-stocks` to scan more stocks
- Verify time0 is during a trading day (not weekend/holiday)

### IB Gateway connection errors
- Historical mode still needs IB Gateway running
- It uses historical data API (not live market data)
- Check that IB Gateway is running and client ID is unique

## Key Points

1. **IB Gateway Required**: You still need IB Gateway running (for historical data API)
2. **24-Hour Window**: Always analyzes time0 to time0+1 day at 16:00 ET
3. **No Live Data**: Doesn't need stocks to be actively trading "now"
4. **Perfect for Weekends**: Ideal for development and debugging on your schedule

## More Information

- Full documentation: `GAINERS_SCANNER_README.md` (Historical Mode section)
- Implementation details: `HISTORICAL_MODE_IMPLEMENTATION_SUMMARY.md`
- Main project guide: `CLAUDE.md`

## Questions?

Check the log file for detailed execution information:
```bash
tail -f ./runs/22-Nov-2025/GGS_historical.log
```

---
**Now you can work on your trading scripts during weekends and after hours!**
