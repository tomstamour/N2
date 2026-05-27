# HistNewsFetcher.py - Historical News Fetcher

A Python script to fetch historical news data from the Alpaca REST API and save each article as a separate JSON file.

## Installation

The script requires the `alpaca-py` package:

```bash
pip install alpaca-py
```

## Usage

### Basic Command

```bash
./HistNewsFetcher.py --symbol NVDA --start-time "2022-01-01" --end-time "2022-12-31"
```

### Required Arguments

- `--symbol <TICKER>`: Stock ticker symbol (e.g., NVDA, TSLA, AAPL)
- `--start-time <"YYYY-MM-DD">`: Start date for news search
- `--end-time <"YYYY-MM-DD">`: End date for news search

### Optional Arguments

- `--output-dir <path>`: Output directory (default: `./outputs/`)
- `--api-keys <path>`: API credentials file (default: `./alpaca_API-Keys.txt`)
- `--log-dir <path>`: Log directory (default: `./logs`)

## Examples

### Fetch news for a single day
```bash
./HistNewsFetcher.py --symbol NVDA --start-time "2026-01-27" --end-time "2026-01-27"
```

### Fetch news for a full month
```bash
./HistNewsFetcher.py --symbol TSLA --start-time "2025-12-01" --end-time "2025-12-31"
```

### Custom output directory
```bash
./HistNewsFetcher.py --symbol AAPL --start-time "2025-01-01" --end-time "2025-01-31" --output-dir ./my_news_output
```

### Run the same command twice (demonstrates resume capability)
```bash
./HistNewsFetcher.py --symbol NVDA --start-time "2026-01-27" --end-time "2026-01-27"
# First run: Saves 15 new articles
./HistNewsFetcher.py --symbol NVDA --start-time "2026-01-27" --end-time "2026-01-27"
# Second run: Skipped 15 existing articles
```

## Output Format

### File Naming
Files are named with the article creation date: `{SYMBOL}_{DD}-{MMM}-{YYYY}.json`
- Example: `NVDA_27-jan-2026.json`

### Duplicate Handling
If multiple articles exist for the same date, they get numbered:
- `NVDA_27-jan-2026.json`
- `NVDA_27-jan-2026_1.json`
- `NVDA_27-jan-2026_2.json`

### JSON Structure
Each file contains a single news article with this structure:
```json
{
  "id": 50163912,
  "headline": "Article headline",
  "summary": "Article summary",
  "author": "Author name",
  "created_at": "2026-01-27 16:37:47+00:00",
  "updated_at": "2026-01-27 16:37:48+00:00",
  "url": "https://...",
  "content": "Full article HTML content",
  "symbols": ["NVDA", "AAPL", "..."],
  "source": "benzinga"
}
```

## Logging

The script creates daily log files in the `logs/` directory:
- `logs/HistNewsFetcher_2026-01-28.log`

Log files contain DEBUG-level information. Console output shows INFO-level messages.

## Error Handling

### Validation Errors
The script validates inputs at startup:
- Invalid symbol format (must be alphanumeric, e.g., NVDA, BRK-B)
- Invalid date format (must be YYYY-MM-DD)
- Invalid date range (end date must be after start date)
- Missing API credentials file
- Cannot create output directory

### API Errors
- Rate limiting is handled with exponential backoff (2s, 4s, 8s, 16s, 32s, 64s)
- Max 5 retries for transient failures
- Failed articles are logged but don't stop the process

## Features

✓ **Pagination**: Automatically handles multiple pages of results
✓ **Resume Capability**: Running the same command twice skips existing articles
✓ **Atomic Writes**: Files are written safely with .tmp file + atomic rename
✓ **Progress Tracking**: Real-time updates during fetch
✓ **Graceful Shutdown**: Handles Ctrl+C properly
✓ **Rate Limiting**: Automatic exponential backoff on rate limits
✓ **Comprehensive Logging**: DEBUG to file, INFO to console
✓ **Input Validation**: Fail-fast validation at startup

## API Credentials

The script reads credentials from `./alpaca_API-Keys.txt` (or custom path with `--api-keys`).

File format:
```
Endpoint:
https://...
Key:
YOUR_API_KEY
Secret:
YOUR_SECRET_KEY
```

The API keys file is already symlinked to `../newsWatcher/alpaca_API-Keys.txt`.

## Troubleshooting

### No articles found for date range
The API returns 0 articles for the specified date/symbol combination. Try a different date range.

### Rate limit errors
The script will automatically retry with exponential backoff. If you get persistent rate limit errors, wait and try again.

### Permission denied
Ensure the script is executable:
```bash
chmod +x HistNewsFetcher.py
```

### API key errors
Verify the `alpaca_API-Keys.txt` file exists and contains valid credentials in the expected format.

## Performance

- Fetches ~50 articles per API request
- Handles pagination automatically
- Typical run time: <1 second for single day with 15-20 articles
- Atomic writes prevent corrupted files

