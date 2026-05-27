# NerSecDicCreator - Financial NER Tool

Fast Named Entity Recognition (NER) for financial news, powered by SEC EDGAR company data and custom alias mappings.

## Quick Start

```bash
# Basic usage (auto-builds cache on first run)
python3 NerSecDicCreator.py --input your_file.json

# Output file: your_file_NER.json
```

## Features

- **Fast**: <100ms processing per 12 sentences (after cache build)
- **Offline**: Works offline after first run (7-day cache TTL)
- **Accurate**: Multi-tier entity resolution (ticker → alias → fuzzy name)
- **Reliable**: Graceful fallback when SEC data unavailable
- **Simple**: Rule-based approach, no ML models required

## Installation

No external dependencies - uses Python standard library only:

```bash
# Just run it (requires Python 3.7+)
python3 NerSecDicCreator.py --input my_file.json
```

## Usage

### Process a File
```bash
python3 NerSecDicCreator.py --input FEED_28-jan-2026_sentences.json
# Generates: FEED_28-jan-2026_sentences_NER.json
```

### Shorthand
```bash
python3 NerSecDicCreator.py -i my_file.json
```

### Cache Management
```bash
# Refresh SEC data (if internet available)
python3 NerSecDicCreator.py --update-cache

# Force rebuild cache
python3 NerSecDicCreator.py --rebuild-cache
```

## Input Format

JSON file with sentences structure:

```json
{
  "metadata": {
    "input_basename": "FEED_28-jan-2026",
    "total_sentences": 12,
    "source_counts": {...}
  },
  "sentences": [
    {
      "id": 0,
      "text": "EXCLUSIVE: ENvue Medical Pushes Reusable OTC Syringes...",
      "source": "headline",
      "char_start": 0,
      "char_end": 91
    },
    ...
  ]
}
```

## Output Format

JSON file with detected entities:

```json
{
  "metadata": {
    "input_file": "...",
    "total_sentences": 12,
    "total_entities": 9,
    "unique_tickers": ["NVUE"],
    "processing_time_seconds": 0.1
  },
  "sentences": [
    {
      "id": 0,
      "text": "...",
      "source": "headline",
      "entities": [
        {
          "text": "ENvue Medical Inc",
          "ticker": "NVUE",
          "official_name": "ENvue Medical Inc.",
          "cik": "0001823395",
          "char_start": 0,
          "char_end": 17,
          "match_type": "alias"
        }
      ]
    }
  ]
}
```

## Entity Resolution

The tool resolves company names to tickers using multi-tier matching:

1. **Ticker Match**: Direct 2-5 letter ticker symbol (e.g., "NVUE")
2. **Company Name Match**: Company name with suffix (e.g., "ENvue Medical Inc." → NVUE)
3. **Alias Match**: Partial company name matches

Unresolved entities are included with `ticker: null` and `match_type: "unresolved"`.

## Performance

**First Run** (builds cache):
- ~0.1 seconds for 12 sentences
- Cache building: ~3-5 seconds

**Subsequent Runs** (cached):
- ~0.01 seconds for 12 sentences

**Scaling**:
- Linear with sentence count
- Parallel processing (4 workers default)
- Sub-second for typical articles (50-100 sentences)

## Cache

Cache location: `~/.cache/NerSecDictionary/`

**Cache Contents**:
- `sec_tickers.json` - Company data (~2MB)
- `sec_aliases.json` - Company name variations (~3MB)
- `cache_metadata.json` - Metadata and expiration info
- `yfinance/` - Optional yfinance cache

**TTL**:
- SEC data: 7 days
- yfinance data: 24 hours (optional)

## Advanced Usage

### Check Cache Status
```bash
ls -la ~/.cache/NerSecDictionary/
cat ~/.cache/NerSecDictionary/cache_metadata.json | jq .
```

### Verify Output
```bash
# Check metadata
jq '.metadata' output.json

# View all entities
jq '.sentences[].entities' output.json

# Filter by ticker
jq '.sentences[].entities | select(.ticker != null)' output.json
```

### Debug Entity Extraction
```bash
# Check which sentences have entities
jq '.sentences[] | select(.entities | length > 0) | {id, text, entity_count: (.entities | length)}'
```

## What Gets Detected

The tool extracts:
- Company names with suffixes: "ENvue Medical Inc.", "Apple Corp.", etc.
- Ticker symbols: NVUE, AAPL, TSLA, etc.
- Multi-word capitalized phrases (when resolvable to known companies)

The tool filters out:
- Common abbreviations: CEO, CFO, FDA, SEC, NYSE, etc.
- Single letters in most contexts
- Generic business terms

## Troubleshooting

### Script runs but finds no entities
- Check that company names have proper capitalization
- Verify ticker symbols are 2-5 uppercase letters
- Review false positive filter list if needed

### Cache issues
```bash
# Remove all cached data and rebuild
rm -rf ~/.cache/NerSecDictionary
python3 NerSecDicCreator.py -i your_file.json
```

### SEC data download fails (403 error)
- Script automatically falls back to internal database
- Fallback includes 9 common tech/finance companies
- Use `--rebuild-cache` to retry after network issues resolved

## Customization

To add more companies to fallback database, edit `_get_fallback_sec_data()` method in the script:

```python
def _get_fallback_sec_data(self) -> Dict:
    fallback_data = {
        'cik': {'ticker': 'SYMBOL', 'title': 'Company Name', 'cik_str': cik},
        ...
    }
```

## Files

- `NerSecDicCreator.py` - Main script (561 lines)
- `IMPLEMENTATION.md` - Detailed implementation documentation
- `README.md` - This file
- `FEED_28-jan-2026_sentences.json` - Example input file
- `FEED_28-jan-2026_sentences_NER.json` - Example output file

## Architecture

See `IMPLEMENTATION.md` for:
- Detailed architecture overview
- Core classes and methods
- Design decisions and trade-offs
- Testing results
- Known limitations

## License

This tool is part of the IBKR Scripts project.

## Support

For issues or questions:
1. Check `IMPLEMENTATION.md` for detailed technical info
2. Review the troubleshooting section above
3. Check cache status with `ls -la ~/.cache/NerSecDictionary/`
4. Run with verbose logging (logs appear in console)
