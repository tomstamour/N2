#!/usr/bin/env python3
"""
stocks_list_fetch.py
--------------------
Downloads ETF constituent holdings from BlackRock iShares and returns a
sorted, deduplicated list of equity ticker symbols.

Designed as the upstream data source for universe_finder.py — replaces a
static watchlist.txt with a live-fetched list of ETF constituents.

Usage:
    import stocks_list_fetch

    symbols = stocks_list_fetch.fetch(['SMMD', 'IWC'])
    # Returns sorted, deduplicated list: ['AAOI', 'ABNB', 'APLD', ...]
"""

import logging
import datetime
import io
from pathlib import Path

try:
    import requests
except ImportError:
    raise ImportError("requests is required. Install with: pip install requests")

try:
    import pandas as pd
except ImportError:
    raise ImportError("pandas is required. Install with: pip install pandas")


# ─── ETF Registry ─────────────────────────────────────────────────────────────

_ETF_REGISTRY: dict = {
    "SMMD": (
        "https://www.ishares.com/us/products/288024/ishares-russell-2500-etf"
        "/1467271812596.ajax?fileType=csv&fileName=SMMD_holdings&dataType=fund"
    ),
    "IWC": (
        "https://www.ishares.com/us/products/239716/ishares-microcap-etf"
        "/1467271812596.ajax?fileType=csv&fileName=IWC_holdings&dataType=fund"
    ),
}

_HTTP_HEADERS: dict = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv",
    "X-Requested-With": "XMLHttpRequest",
}

logger = logging.getLogger("stocks_list_fetch")


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch(etf_symbols: list) -> list:
    """
    Download holdings CSVs for the given ETF symbols and return a sorted,
    deduplicated list of equity ticker strings.

    Args:
        etf_symbols: List of ETF ticker strings, e.g. ['SMMD', 'IWC'].
                     Unknown symbols are skipped with a warning.

    Returns:
        Sorted, deduplicated list of equity tickers found across all ETFs.
        Returns [] if all ETFs fail or none are recognised.
    """
    _setup_logging()

    logger.info("=" * 60)
    logger.info(f"stocks_list_fetch.fetch() called with: {etf_symbols}")

    all_tickers: set = set()

    for symbol in etf_symbols:
        sym = symbol.upper().strip()
        url = _ETF_REGISTRY.get(sym)
        if url is None:
            logger.warning(f"{sym}: not in ETF registry — skipping. "
                           f"Known ETFs: {list(_ETF_REGISTRY.keys())}")
            continue

        csv_text = _download_csv(sym, url)
        if csv_text is None:
            continue

        tickers = _parse_holdings(csv_text, sym)
        logger.info(f"{sym}: parsed {len(tickers)} equity tickers")
        all_tickers.update(tickers)

    result = sorted(all_tickers)
    logger.info(f"fetch() complete: {len(result)} unique tickers across {etf_symbols}")
    return result


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """
    Configure the 'stocks_list_fetch' logger to write to:
      ./runs/DD-Mon-YYYY/stocks_list_fetch.log
    The date is fixed at the time fetch() is called.
    Duplicate handlers are avoided if fetch() is called more than once.
    """
    date_str = datetime.datetime.now().strftime("%d-%b-%Y")  # e.g. 17-Mar-2026
    log_dir = Path("runs") / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "stocks_list_fetch.log"

    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(fh)

    logger.info(f"Log file: {log_path.resolve()}")


# ─── HTTP download ────────────────────────────────────────────────────────────

def _download_csv(symbol: str, url: str) -> "str | None":
    """
    Download the iShares holdings CSV for the given ETF.

    Adds a Referer header derived from the URL (everything before the
    '/1467271812596.ajax' AJAX suffix), which iShares requires.

    Returns:
        Raw CSV text string on success, or None on any failure.
    """
    referer = url.split("/1467271812596.ajax")[0]
    headers = {**_HTTP_HEADERS, "Referer": referer}

    logger.debug(f"{symbol}: downloading from {url}")

    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning(f"{symbol}: request timed out")
        return None
    except requests.exceptions.HTTPError as e:
        logger.warning(f"{symbol}: HTTP error — {e}")
        return None
    except requests.exceptions.RequestException as e:
        logger.warning(f"{symbol}: request failed — {e}")
        return None

    content_type = response.headers.get("Content-Type", "")
    if "text/csv" not in content_type and "text/plain" not in content_type:
        logger.warning(
            f"{symbol}: unexpected content-type '{content_type}' — "
            f"likely blocked by Akamai (expected text/csv). "
            f"Response snippet: {response.text[:200]!r}"
        )
        return None

    logger.debug(f"{symbol}: downloaded {len(response.text)} chars (content-type: {content_type})")
    return response.text


# ─── CSV parsing ──────────────────────────────────────────────────────────────

def _find_header_row(lines: list) -> "int | None":
    """
    Scan lines for the first one containing both 'Ticker' and 'Asset Class'.
    This is robust against the BOM + metadata preamble in iShares CSVs.

    Returns:
        Index of the header line, or None if not found.
    """
    for i, line in enumerate(lines):
        if "Ticker" in line and "Asset Class" in line:
            logger.debug(f"Header row found at line index {i}: {line[:80]!r}")
            return i
    return None


def _parse_holdings(csv_text: str, symbol: str) -> list:
    """
    Parse iShares holdings CSV text and return a list of equity ticker strings.

    Filtering applied:
      - Asset Class == 'Equity'
      - Ticker != '-'  (unlisted/OTC holdings with no symbol)
      - Non-empty, non-NaN tickers

    Args:
        csv_text: Raw CSV text from _download_csv().
        symbol:   ETF symbol (used for logging only).

    Returns:
        List of equity ticker strings, or [] on any parse failure.
    """
    lines = csv_text.splitlines()

    header_idx = _find_header_row(lines)
    if header_idx is None:
        logger.warning(
            f"{symbol}: could not find header row ('Ticker' + 'Asset Class') — "
            f"skipping. First 15 lines: {lines[:15]}"
        )
        return []

    csv_body = "\n".join(lines[header_idx:])

    try:
        df = pd.read_csv(io.StringIO(csv_body))
    except Exception as e:
        logger.warning(f"{symbol}: pandas CSV parse error — {e}")
        return []

    required_cols = {"Ticker", "Asset Class"}
    missing = required_cols - set(df.columns)
    if missing:
        logger.warning(
            f"{symbol}: required columns missing after parse: {missing}. "
            f"Found columns: {list(df.columns)}"
        )
        return []

    logger.debug(f"{symbol}: raw rows={len(df)}, columns={list(df.columns)}")

    # Filter to equities only
    equities = df[df["Asset Class"] == "Equity"].copy()
    logger.debug(f"{symbol}: {len(equities)} equity rows (of {len(df)} total)")

    # Extract and clean tickers
    tickers = (
        equities["Ticker"]
        .dropna()
        .astype(str)
        .str.strip()
        .str.upper()
    )

    # Drop '-' placeholder rows
    tickers = tickers[tickers != "-"]

    result = tickers.tolist()
    logger.debug(f"{symbol}: {len(result)} valid equity tickers after filtering")
    return result
