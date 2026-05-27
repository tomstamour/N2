"""
SandP500_symbols_loader.py

Fetches the current list of S&P 500 constituent symbols from Wikipedia.
Source: https://en.wikipedia.org/wiki/List_of_S%26P_500_companies

Usage as a library:
    from SandP500_symbols_loader import load_sp500_symbols
    symbols = load_sp500_symbols()  # list[str], ~503 tickers

Symbols use yfinance conventions (e.g. BRK-B not BRK.B).
"""

import io

import pandas as pd
import requests

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}


def load_sp500_symbols() -> list[str]:
    """Fetch S&P 500 symbols from Wikipedia. Returns a sorted list of ticker strings."""
    try:
        resp = requests.get(_WIKI_URL, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        tables = pd.read_html(io.StringIO(resp.text))
    except Exception as e:
        raise RuntimeError(f"Failed to fetch S&P 500 table from Wikipedia: {e}") from e

    df = tables[0]  # first table is the constituents list
    symbols = (
        df["Symbol"]
        .str.strip()
        .str.replace(".", "-", regex=False)  # BRK.B → BRK-B (yfinance convention)
        .tolist()
    )
    return sorted(symbols)


if __name__ == "__main__":
    symbols = load_sp500_symbols()
    print(symbols)
    print(f"\nLoaded {len(symbols)} symbols.")
