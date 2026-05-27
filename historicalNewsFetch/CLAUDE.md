# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

HistNewsFetcher.py is a standalone Python script that fetches historical news articles from the Alpaca REST API for a given stock symbol and date range. Each article is saved as an individual JSON file with atomic write operations.

## Dependencies

Install required package:
```bash
pip install alpaca-py
```

## Running the Script

### Basic Usage
```bash
./HistNewsFetcher.py --symbol NVDA --start-time "2022-01-01" --end-time "2022-12-31"
```

### With Custom Output Directory
```bash
./HistNewsFetcher.py --symbol TSLA --start-time "2025-12-01" --end-time "2025-12-31" --output-dir ./custom_output
```

### Make Script Executable
```bash
chmod +x HistNewsFetcher.py
```

## API Credentials

The script requires Alpaca API credentials in `./alpaca_API-Keys.txt` (symlinked to `../newsWatcher/alpaca_API-Keys.txt`):

```
Endpoint:
https://...
Key:
YOUR_API_KEY
Secret:
YOUR_SECRET_KEY
```

## Architecture

### Core Classes

**ConfigurationManager**
- Parses API credentials from file using "Key:" and "Secret:" markers
- Used pattern: Read lines sequentially, look for markers, grab next line as value

**ArgumentParser**
- Fail-fast validation: validates all inputs before making API calls
- Checks: symbol format (alphanumeric + hyphens), date formats (YYYY-MM-DD), date range validity, file existence
- Creates output/log directories if needed

**NewsFileHandler**
- File naming: `{SYMBOL}_{DD}-{MMM}-{YYYY}.json` (lowercase month, e.g., `NVDA_27-jan-2026.json`)
- Duplicate handling: Appends counter suffix (`_1.json`, `_2.json`, etc.) for multiple articles on same date
- Atomic writes: Writes to `.tmp` file first, then `os.replace()` for atomic rename
- Article deduplication: Scans all JSON files in output directory to check if article ID exists (enables resume capability)

**AlpacaHistoricalNewsFetcher**
- Uses `alpaca.data.historical.news.NewsClient` (requires both `api_key` and `secret_key` parameters)
- Creates `NewsRequest` with: symbols, start/end dates, limit=50, include_content=True, page_token
- Pagination: Response is `NewsSet` object with `response.data['news']` list and `response.next_page_token`
- Rate limiting: Exponential backoff (2s, 4s, 8s, 16s, 32s, 64s max), max 5 retries

**ProgressTracker**
- Real-time updates: "Fetched N articles (M pages) for SYMBOL"
- Final summary with statistics (articles fetched/saved, pages, time elapsed)

### Signal Handling

Uses global `shutdown_requested` flag with SIGINT/SIGTERM handlers for graceful shutdown. Main fetch loop checks this flag.

### Logging

Dual-handler setup:
- File: DEBUG level → `./logs/HistNewsFetcher_{YYYY-MM-DD}.log`
- Console: INFO level
- Format: `'%(asctime)s %(levelname)s %(name)s: %(message)s'`

## Output Format

### JSON Structure
Each file contains one article with fields:
- `id`, `headline`, `summary`, `author`
- `created_at`, `updated_at` (format: "YYYY-MM-DD HH:MM:SS+00:00")
- `url`, `content`, `symbols` (list), `source`
- 2-space indentation

### Directory Structure
```
historicalNewsFetch/
├── HistNewsFetcher.py          # Main script
├── alpaca_API-Keys.txt         # Symlink to ../newsWatcher/alpaca_API-Keys.txt
├── outputs/                    # Default output directory
│   ├── NVDA_27-jan-2026.json
│   ├── NVDA_27-jan-2026_1.json
│   └── ...
└── logs/                       # Daily log files
    └── HistNewsFetcher_2026-01-28.log
```

## Key Implementation Patterns

### Timestamp Conversion
API returns datetime objects that need conversion to strings matching format: "YYYY-MM-DD HH:MM:SS+00:00"
```python
created_at = str(article.created_at).replace('TzInfo(0)', '+00:00')
```

### Filename Generation from Timestamp
Parse `created_at` field (format: "2026-01-27 16:37:47+00:00"), extract date part, convert to "dd-mmm-yyyy" lowercase.

### API Response Structure
- Response is `NewsSet` object (not dict)
- Access articles via `response.data.get('news', [])`
- Pagination token via `response.next_page_token`

### Atomic File Operations
Always write to temporary file with `.tmp` extension, then use `os.replace(temp_path, final_path)` for atomic rename. This prevents corrupted files if script is interrupted.

## Related Scripts

This script shares the ConfigurationManager and logging patterns with `../newsWatcher/NewsWatcher.py`. Both scripts:
- Use same API credentials file format
- Follow same dual-handler logging pattern (DEBUG to file, INFO to console)
- Use signal handlers for graceful shutdown
- Save articles in same JSON format

## Troubleshooting

### API Authentication Errors
Ensure NewsClient constructor receives both `api_key` and `secret_key` parameters explicitly.

### Response Parsing Errors
Remember: API returns `NewsSet` object, not a simple dict. Access news list via `response.data.get('news', [])`.

### Rate Limiting
Script automatically retries with exponential backoff. If persistent rate limits occur, the API may be throttling requests.
