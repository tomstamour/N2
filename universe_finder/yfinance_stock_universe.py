#!/usr/bin/env python3
"""
yfinance_stock_universe.py
--------------------------
Discovers US-listed stocks filtered by market capitalisation (and optionally
float, institutional ownership, and net income) and returns a sorted,
deduplicated list of equity ticker strings.

Designed as a drop-in data source for universe_finder.py — same output
format as stocks_list_fetch.fetch().

Strategy:
  1. Server-side screen via yf.screen() + EquityQuery:
       - Region = US
       - Exchange whitelist (NYSE, NASDAQ, AMEX, Arca, BATS — no OTC/pink)
       - Market cap < max_market_cap (primary filter)
       - Optional: institutional ownership, net income TTM
  2. Paginate up to ~10,000 results (250 per page).
  3. Optional client-side float filter: fetch floatShares ticker-by-ticker
     for the narrowed set.
  4. Aggressive rate-limit handling (429 back-off, configurable delays).

Usage:
    import yfinance_stock_universe

    symbols = yfinance_stock_universe.fetch(max_market_cap=300)
    # Returns sorted, deduplicated list: ['CLOV', 'GRAB', 'HOOD', ...]

    # With additional filters:
    symbols = yfinance_stock_universe.fetch(
        max_market_cap=300,    # millions
        max_float_m=20,        # millions of shares
        max_inst_pct=0.20,     # fraction 0-1
        max_net_income=0,      # USD; 0 = money-losers only
    )
"""

import time
import logging
import datetime
import argparse
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    from yfinance import EquityQuery
except ImportError:
    raise ImportError("yfinance is required. Install with: pip install yfinance")


# ─── Constants ────────────────────────────────────────────────────────────────

# Major US exchanges — excludes OTC (PNK, OEM, OQB, OQX)
_US_EXCHANGES = ["NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS"]

_PAGE_SIZE    = 250    # Yahoo maximum per request
_MAX_PAGES    = 40     # 250 * 40 = 10,000 ceiling
_SCREEN_DELAY = 1.0    # seconds between pagination requests

_TICKER_DELAY = 0.35   # seconds between individual ticker .info calls
_BATCH_SIZE   = 80     # pause longer every N tickers
_BATCH_PAUSE  = 35     # seconds to pause after each batch
_RETRY_WAIT   = 35     # seconds to wait on a 429 error
_MAX_RETRIES  = 3      # retries per ticker before skipping

logger = logging.getLogger("yfinance_stock_universe")


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """
    Configure the 'yfinance_stock_universe' logger to write to:
      ./runs/DD-Mon-YYYY/yfinance_stock_universe.log
    The date is fixed at the time fetch() is called.
    Duplicate handlers are avoided if fetch() is called more than once.
    """
    date_str = datetime.datetime.now().strftime("%d-%b-%Y")
    log_dir  = Path("runs") / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "yfinance_stock_universe.log"

    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(fh)

    logger.info(f"Log file: {log_path.resolve()}")


# ─── Query builder ────────────────────────────────────────────────────────────

def _build_query(max_market_cap_usd: float, cap_field: str = "intradaymarketcap") -> EquityQuery:
    """
    Build the EquityQuery for the server-side screen.

    Only market cap is used as a server-side filter. Yahoo Finance's screener
    silently excludes tickers that lack data for any filtered field — fields
    like pctheldinst and netincomeis have poor coverage for micro-caps, which
    would cause valid tickers to be silently dropped.

    The same silent-drop issue affects market cap: some tickers have
    intradayMarketCap=None but a valid marketCap. We therefore run two passes
    (one per field) and merge the results.

    Args:
        max_market_cap_usd: Market cap upper bound in USD (not millions).
        cap_field:          Yahoo screener field name to filter on; either
                            "intradaymarketcap" (default) or "lastclosemarketcap.lasttwelvemonths".
    """
    return EquityQuery("and", [
        EquityQuery("eq",    ["region", "us"]),
        EquityQuery("is-in", ["exchange"] + _US_EXCHANGES),
        EquityQuery("lt",    [cap_field, max_market_cap_usd]),
    ])


# ─── Server-side screener ─────────────────────────────────────────────────────

def _screen_one_pass(max_market_cap_usd: float, cap_field: str) -> list:
    """
    Run a single paginated screen for one cap_field and return raw quote dicts.

    Args:
        max_market_cap_usd: Market cap upper bound in USD.
        cap_field:          "intradaymarketcap" or "lastclosemarketcap.lasttwelvemonths".

    Returns:
        List of quote dicts (may be empty on failure).
    """
    query      = _build_query(max_market_cap_usd, cap_field)
    all_quotes: list = []
    offset     = 0

    for page in range(1, _MAX_PAGES + 1):
        logger.debug(
            f"[{cap_field}] page {page} (offset={offset}, size={_PAGE_SIZE}) ..."
        )

        try:
            resp = yf.screen(
                query,
                offset=offset,
                size=_PAGE_SIZE,
                sortField="ticker",
                sortAsc=True,
            )
        except Exception as exc:
            logger.warning(f"[{cap_field}] Screener request failed: {exc}")
            break

        try:
            quotes = resp.get("quotes", [])
            total  = resp.get("total", 0)
        except (AttributeError, TypeError):
            logger.warning(f"[{cap_field}] Unexpected screener response — stopping.")
            break

        if not quotes:
            logger.info(f"[{cap_field}] No more results — done.")
            break

        all_quotes.extend(quotes)
        logger.info(
            f"[{cap_field}] Page {page}: {len(quotes)} quotes "
            f"(cumulative {len(all_quotes)} / {total})"
        )

        if len(all_quotes) >= total:
            break

        offset += _PAGE_SIZE
        time.sleep(_SCREEN_DELAY)

    return all_quotes


def _screen_server_side(max_market_cap_usd: float) -> list:
    """
    Run two paginated screens (intradaymarketcap + marketcap) and merge results.

    Yahoo Finance silently drops tickers where the filtered field is None.
    Some tickers (e.g. COCP) have intradayMarketCap=None but a valid marketCap.
    Running both passes and deduplicating by symbol ensures those tickers are
    captured.

    Returns:
        Deduplicated list of quote dicts sorted by symbol.
        Returns [] if both passes fail or return nothing.
    """
    logger.info("Pass 1/2: screening by intradaymarketcap ...")
    pass1 = _screen_one_pass(max_market_cap_usd, "intradaymarketcap")
    logger.info(f"Pass 1 complete: {len(pass1)} tickers.")

    time.sleep(_SCREEN_DELAY)

    logger.info("Pass 2/2: screening by lastclosemarketcap.lasttwelvemonths (catches intradayMarketCap=None tickers) ...")
    pass2 = _screen_one_pass(max_market_cap_usd, "lastclosemarketcap.lasttwelvemonths")
    logger.info(f"Pass 2 complete: {len(pass2)} tickers.")

    # Merge: prefer pass1 dict for any symbol present in both (it has more data)
    merged: dict[str, dict] = {}
    for q in pass2:
        sym = q.get("symbol")
        if sym:
            merged[sym] = q
    for q in pass1:
        sym = q.get("symbol")
        if sym:
            merged[sym] = q   # pass1 wins on collision

    new_from_pass2 = len(merged) - len({q["symbol"] for q in pass1 if q.get("symbol")})
    logger.info(
        f"Merge complete: {len(merged)} unique tickers "
        f"({new_from_pass2} added by marketcap pass)."
    )
    return list(merged.values())


# ─── Client-side float filter ─────────────────────────────────────────────────

def _fetch_float(symbol: str) -> Optional[int]:
    """
    Return floatShares for symbol, or None on failure / missing data.
    Retries up to _MAX_RETRIES times on HTTP 429 rate-limit errors.
    """
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            info = yf.Ticker(symbol).info
            return info.get("floatShares")
        except Exception as exc:
            err_str = str(exc).lower()
            if "429" in err_str or "too many" in err_str:
                logger.warning(
                    f"429 on {symbol} (attempt {attempt}/{_MAX_RETRIES}) "
                    f"— cooling down {_RETRY_WAIT}s ..."
                )
                time.sleep(_RETRY_WAIT)
            else:
                logger.debug(f"Error fetching float for {symbol}: {exc}")
                return None
    logger.warning(f"Skipping {symbol} after {_MAX_RETRIES} retries.")
    return None


def _filter_by_float(quotes: list, max_float_shares: int) -> list:
    """
    Fetch floatShares for each quote and keep only those below max_float_shares.

    Args:
        quotes:           Raw quote dicts from _screen_server_side().
        max_float_shares: Float shares upper bound (absolute count, not millions).

    Returns:
        Filtered list of quote dicts (original dict + 'floatShares' key added).
    """
    total  = len(quotes)
    passed = []

    logger.info(
        f"Fetching float for {total} tickers "
        f"(delay={_TICKER_DELAY}s, batch={_BATCH_SIZE}/{_BATCH_PAUSE}s) ..."
    )

    for idx, q in enumerate(quotes, start=1):
        symbol       = q.get("symbol", "???")
        float_shares = _fetch_float(symbol)

        if float_shares is not None and float_shares < max_float_shares:
            q["floatShares"] = float_shares
            passed.append(q)
            logger.debug(
                f"[{idx}/{total}] {symbol:<8}  float={float_shares:,}  PASS"
            )
        elif float_shares is not None:
            logger.debug(
                f"[{idx}/{total}] {symbol:<8}  float={float_shares:,}  too high"
            )
        else:
            logger.debug(f"[{idx}/{total}] {symbol:<8}  float=N/A  skipped")

        if idx % _BATCH_SIZE == 0 and idx < total:
            logger.info(
                f"Batch pause ({idx}/{total} done) — sleeping {_BATCH_PAUSE}s ..."
            )
            time.sleep(_BATCH_PAUSE)
        else:
            time.sleep(_TICKER_DELAY)

    logger.info(f"Float filter complete: {len(passed)} / {total} tickers passed.")
    return passed


# ─── Public API ───────────────────────────────────────────────────────────────

def fetch(
    max_market_cap: float = 300,
    max_float_m: Optional[float] = None,
    max_inst_pct: Optional[float] = None,
    max_net_income: Optional[float] = None,
) -> list:
    """
    Screen US-listed stocks and return a sorted, deduplicated list of ticker
    strings — same output format as stocks_list_fetch.fetch().

    Args:
        max_market_cap:  Market cap upper bound in millions (default 300).
        max_float_m:     Optional float shares upper bound in millions.
                         When set, triggers a slower per-ticker fetch pass.
        max_inst_pct:    Optional max institutional ownership fraction 0-1
                         (e.g. 0.20 = 20%).
        max_net_income:  Optional max TTM net income in USD. Use 0 or a negative
                         value to target money-losing companies only.

    Returns:
        Sorted, deduplicated list of equity ticker strings.
        Returns [] if the screener fails or nothing passes the filters.
    """
    _setup_logging()

    logger.info("=" * 60)
    logger.info(f"yfinance_stock_universe.fetch() called")
    logger.info(f"  max_market_cap : {max_market_cap}M")
    logger.info(f"  max_float_m    : {max_float_m}M" if max_float_m is not None else "  max_float_m    : None")
    logger.info(f"  max_inst_pct   : {max_inst_pct}" if max_inst_pct is not None else "  max_inst_pct   : None")
    logger.info(f"  max_net_income : {max_net_income}" if max_net_income is not None else "  max_net_income : None")

    if max_inst_pct is not None:
        logger.warning(
            "max_inst_pct is not applied as a server-side filter — Yahoo's screener "
            "silently drops tickers lacking pctheldinst data (common for micro-caps)."
        )
    if max_net_income is not None:
        logger.warning(
            "max_net_income is not applied as a server-side filter — Yahoo's screener "
            "silently drops tickers lacking netincomeis data (common for micro-caps)."
        )

    quotes = _screen_server_side(max_market_cap * 1_000_000)

    if not quotes:
        logger.warning("No tickers returned by server-side screen.")
        return []

    if max_float_m is not None:
        quotes = _filter_by_float(quotes, int(max_float_m * 1_000_000))

    result = sorted({q["symbol"] for q in quotes if q.get("symbol")})
    logger.info(f"fetch() complete: {len(result)} unique tickers")
    return result


# ─── CLI ──────────────────────────────────────────────────────────────────────

def _main():
    parser = argparse.ArgumentParser(
        description="Screen US-listed stocks by market cap (and optionally float, "
                    "institutional ownership, and net income).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  # All stocks under $300M market cap
  python yfinance_stock_universe.py --max-market-cap 300

  # Small cap + low float + low institutions + money-losers only
  python yfinance_stock_universe.py --max-market-cap 300 --max-float 20 --max-inst 0.20 --max-income 0

  # Write results to CSV
  python yfinance_stock_universe.py --max-market-cap 500 --output results.csv
""",
    )
    parser.add_argument(
        "--max-market-cap", type=float, default=300,
        help="Max market cap in millions (default: 300)",
    )
    parser.add_argument(
        "--max-float", type=float, default=None,
        help="Max float shares in millions (optional)",
    )
    parser.add_argument(
        "--max-inst", type=float, default=None,
        help="Max institutional ownership fraction 0-1 (optional, e.g. 0.20 = 20%%)",
    )
    parser.add_argument(
        "--max-income", type=float, default=None,
        help="Max TTM net income in USD (optional; use 0 for money-losers only)",
    )
    parser.add_argument(
        "--output", "-o", type=str, default=None,
        help="Write symbol list to a text file, one per line",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable DEBUG logging to console as well",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )

    symbols = fetch(
        max_market_cap=args.max_market_cap,
        max_float_m=args.max_float,
        max_inst_pct=args.max_inst,
        max_net_income=args.max_income,
    )

    print(f"\n{len(symbols)} symbols found:")
    for sym in symbols:
        print(sym)

    if args.output and symbols:
        out_path = Path(args.output)
        out_path.write_text("\n".join(symbols) + "\n")
        print(f"\nResults written to {out_path.resolve()}")


if __name__ == "__main__":
    _main()
