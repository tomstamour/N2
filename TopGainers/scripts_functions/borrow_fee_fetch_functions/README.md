# Borrow Fee Fetch Functions

Multi-source borrow fee data enrichment system for GUS3.

## Overview

This package provides comprehensive borrow fee data enrichment by aggregating data from multiple sources:

1. **IBKR FTP Server** (Official)
   - 17,980+ US stocks
   - Updated multiple times daily
   - Most reliable source

2. **iBorrowDesk API** (Free, Unofficial)
   - JSON API for borrow fee data
   - Rate-limited requests
   - Good coverage for popular stocks

## Components

### borrow_fee_ftp_fetcher.py
Fetches borrow fee data from IBKR's official FTP server (`ftp2.interactivebrokers.com`).

**Usage:**
```bash
# Download FTP data and save to cache
python3 borrow_fee_ftp_fetcher.py --output ./cache/borrow_fees.csv

# Enrich GTT file with FTP data
python3 borrow_fee_ftp_fetcher.py \
  --load-csv ./cache/borrow_fees.csv \
  --enrich-gtt ../../runs/23-Nov-2025/GusTradeTable.txt
```

### borrow_fee_iborrowdesk_fetcher.py
Fetches borrow fee data from iBorrowDesk's free JSON API.

**Usage:**
```bash
# Fetch and cache iBorrowDesk data
python3 borrow_fee_iborrowdesk_fetcher.py \
  --symbols TSLA,NVDA,AAPL \
  --cache ./cache/iborrowdesk_cache.csv
```

### borrow_fee_orchestrator.py
**Main entry point** - Coordinates multiple fetchers and enriches GTT files with comprehensive borrow fee data.

**Usage:**
```bash
# Enrich GTT with multi-source data (FTP + iBorrowDesk)
python3 borrow_fee_orchestrator.py \
  --input ../../runs/23-Nov-2025/GusTradeTable.txt \
  --sources ftp,iborrowdesk \
  --ftp-cache ./cache/borrow_fees.csv \
  --ibd-cache ./cache/iborrowdesk_cache.csv
```

**Columns Added to GTT:**
- `BF1`: Borrow fee from source 1 (FTP)
- `BF2`: Borrow fee from source 2 (iBorrowDesk)
- `BF_BEST`: Best (most conservative/highest) fee from all sources
- `BF_Source`: Source of the best fee (FTP, iBorrowDesk, or MULTI)

## Integration with Gainers Scanner

The orchestrator is **automatically called** by the Gainers Scanner Script after each scan completes.

No manual intervention required - just run GSS normally:

```bash
python3 ../gainers_scanner_scripts/Gainers_Scanner_Script.py \
  --time0 "2025-11-23/09:30" \
  --gap-percentage 10 \
  --client-id 702
```

## Cache Files

Located in `./cache/`:

- `borrow_fees.csv`: Cached FTP data (1.6 MB, 17,980 symbols)
- `iborrowdesk_cache.csv`: Cached iBorrowDesk API responses

**Cache Behavior:**
- Reused to avoid repeated downloads
- 60-second timeout on FTP requests
- Automatically created on first run

## Directory Structure

```
borrow_fee_fetch_functions/
├── __init__.py                          # Python package init
├── README.md                            # This file
├── borrow_fee_ftp_fetcher.py           # IBKR FTP fetcher
├── borrow_fee_iborrowdesk_fetcher.py   # iBorrowDesk API fetcher
├── borrow_fee_orchestrator.py          # Multi-source orchestrator
├── stock_borrow_fees_data_sources.txt  # Research documentation
└── cache/                               # Data cache directory
    ├── borrow_fees.csv                  # FTP cache
    └── iborrowdesk_cache.csv           # iBorrowDesk cache
```

## Error Handling

All scripts use graceful degradation:
- If FTP fails, system continues without FTP data
- If iBorrowDesk fails, system continues without iBorrowDesk data
- If orchestrator fails completely, GTT file remains unchanged
- All errors logged to console

## Documentation

See also:
- `../../BORROW_FEE_IMPROVEMENT_SUMMARY.md` - Implementation details
- `stock_borrow_fees_data_sources.txt` - Data source research
- `../gainers_scanner_scripts/GSS_TIME0_README.md` - GSS integration details
