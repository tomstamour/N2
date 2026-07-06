# Historical Gap Up Finder (HGUF)

## Project Overview

The Historical Gap Up Finder (HGUF) is a Python script that scans the Interactive Brokers (IBKR) historical database to identify "gap-up" events for a given list of stocks over a specified time period. A gap-up event is when a stock's price at exactly 09:29 (Checkpoint-B) is significantly higher than the previous day's RTH closing price at 16:00 (Checkpoint-A).

The script is highly customizable, allowing users to define the time window, minimum gap percentage, and various filters based on price, float, and institutional ownership. It enriches the data with fundamentals from Yahoo Finance and provides detailed intraday OHLC (Open, High, Low, Close) data for each identified gap event.

The final output is a tab-delimited file (`.txt`) that can be easily opened in spreadsheet software for further analysis. The script uses the `rich` library to provide a user-friendly command-line interface with progress bars and summary tables.

### Key Technologies

*   **Python 3:** The core programming language.
*   **Interactive Brokers API (ibapi):** For fetching historical market data.
*   **yfinance:** For fetching fundamental stock data (e.g., float, institutional ownership).
*   **rich:** For creating a rich terminal interface with progress bars, tables, and formatted text.
*   **pandas:** (as inferred from the context of financial data analysis, although not explicitly used in the provided script).
*   **pytz:** For timezone handling.

### Architecture

The system is a single, command-line Python script (`Historical_Gap_Up_Finder.py`) that performs the following steps:

1.  **Initialization:** Parses command-line arguments, sets up logging, and connects to the IBKR Gateway.
2.  **Phase 1: Gap Detection:**
    *   Fetches daily historical data for each stock in the `stock_universe.txt` file.
    *   Identifies potential gap-up events by comparing the previous day's close (16:00) to the next day's price at 09:29.
3.  **Phase 2: Enrichment and Filtering:**
    *   Fetches fundamental data (float, institutional ownership) from Yahoo Finance for the stocks with gap-up events.
    *   Applies user-defined filters (e.g., max float, min institutional ownership).
4.  **Phase 3: Detailed Data Fetching:**
    *   For the filtered gap events, it fetches detailed intraday OHLC data from the day before the gap to the day of the gap.
5.  **Phase 4: Output Generation:**
    *   Calculates additional metrics like `GAP%` and `Push%`.
    *   Writes the final, enriched data to a tab-delimited file in the `hguf-tables/` directory.

## Building and Running

The project does not have a formal build process. The script is run directly from the command line.

### Dependencies

Install the required Python libraries using pip:

```bash
pip install rich pandas pytz ibapi yfinance
```

### Running the Script

To run the Historical Gap Up Finder, you need to have the Interactive Brokers Gateway or Trader Workstation (TWS) running and the API enabled.

**Basic Example:**

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "01-12-2025" "20-12-2025" \
  --minimum-gap-percentage 30 \
  --candles-size 5 \
  --client-id 700
```

**Example with Filters:**

```bash
python3 Historical_Gap_Up_Finder.py \
  --time-window "01-11-2025" "30-11-2025" \
  --minimum-gap-percentage 50 \
  --max-open-price 20.0 \
  --max-float 100.0 \
  --max-institution 40.0 \
  --candles-size 5 \
  --client-id 700 \
  --output-dir ./hguf-tables
```

## Development Conventions

### Coding Style

The Python code follows the PEP 8 style guide. The script is well-structured, with clear function definitions and comments explaining the different parts of the code.

### Logging

The script uses the `logging` module to provide detailed information about its execution. Log files are saved in the `logs/` directory. Verbose logging can be enabled with the `--verbose` flag.

### Error Handling

The script includes error handling for common issues such as connection failures to the IBKR Gateway, historical data request errors, and API rate limiting.
