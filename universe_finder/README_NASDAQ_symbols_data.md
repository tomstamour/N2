# NASDAQ_symbols_data.py

Fetches all major-exchange stock symbols and enriches each with its **float value (in millions of shares)**. Results are cached to a TSV file and reloaded automatically within a 24-hour window.

---

## Purpose

Builds a master reference DataFrame of exchange-listed tickers for use by downstream short-selling screens (e.g. `universe_finder.py`). Covers all major US exchanges: NASDAQ, NYSE, NYSE MKT, NYSE ARCA, BATS, and IEXG.

---

## Output

Saved to `./data/nasdaq_symbols_data.tsv` (tab-separated, created automatically).

| Column | Type | Description |
|--------|------|-------------|
| `Symbol` | str | Ticker symbol |
| `Exchange` | str | Human-readable exchange name |
| `Float_M` | float | Float in millions of shares (NaN if unavailable) |
| `Float_Source` | str | Which source provided the float value |

`Float_Source` values: `finviz` / `nasdaq` / `yfinance` / `none`

---

## Float data chain

Each ticker is tried in priority order until a value is found:

1. **Primary — finvizfinance** (`finvizfinance(sym).ticker_fundament()["Shs Float"]`)
   Most current and reliable. Requires `pip install finvizfinance`.

2. **Fallback 1 — Nasdaq.com API** (`/api/quote/{sym}/summary?assetclass=stocks`)
   JSON endpoint; no API key required. Used when Finviz fails.

3. **Fallback 2 — yfinance** (`yf.Ticker(sym).info["floatShares"]`)
   Coarse fallback only. Used when both primary sources fail.

---

## Usage

### As a library

```python
from NASDAQ_symbols_data import build_dataframe

df = build_dataframe()                      # loads from cache if < 24h old
df = build_dataframe(force_refresh=True)    # always fetches fresh data
```

### From the command line

```bash
python NASDAQ_symbols_data.py               # uses cache if fresh
python NASDAQ_symbols_data.py --refresh     # force a fresh fetch
```

CLI prints a summary: total symbol count, float coverage %, breakdown by exchange and float source, and the first 10 rows.

---

## Caching

- Cache file: `./data/nasdaq_symbols_data.tsv`
- TTL: **24 hours** (based on file modification time)
- Expired or missing cache triggers a full re-fetch and overwrites the file
- `force_refresh=True` bypasses the TTL check entirely

---

## Architecture

| Concern | Detail |
|---------|--------|
| Symbol source | Nasdaq FTP: `nasdaqlisted.txt` + `otherlisted.txt` |
| Concurrency | `ThreadPoolExecutor` with **5 workers** |
| Submission pacing | 50 ms stagger between task submissions |
| Progress logging | Every 100 symbols |
| Logging output | `./runs/DD-Mon-YYYY/NASDAQ_symbols_data.log` (DEBUG level) |
| No IBKR required | All data from public web sources |

---

## Dependencies

| Package | Install | Role |
|---------|---------|------|
| `pandas` | `pip install pandas` | DataFrame, TSV I/O |
| `requests` | `pip install requests` | Nasdaq FTP + API fetches |
| `finvizfinance` | `pip install finvizfinance` | Primary float source |
| `yfinance` | `pip install yfinance` | Last-resort float fallback |

`finvizfinance` and `yfinance` are imported lazily inside their respective functions — the script will still run (with degraded float coverage) if either is missing.

---

## Logging

Follows the same pattern as `universe_finder.py`:

```
./runs/04-Apr-2026/NASDAQ_symbols_data.log
```

Uses a named logger (`"NASDAQ_symbols_data"`) — will not interfere with the calling script's logging configuration.
