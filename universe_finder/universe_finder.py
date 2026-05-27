#!/usr/bin/env python3
"""
universe_finder.py
------------------
Maintains a filtered list of small-cap stock symbols in memory for rapid use
by downstream trading strategies (e.g. short-selling scanners).

Filtering criteria (ALL must pass):
  - Institutional ownership  < max_institution_pct  (%)
  - Float                    < max_float_m           (millions of shares)
  - Last close price         < max_price             ($)

Data source: yfinance only (no IBKR required).
  - Fundamentals (float, institution %): fetched ONCE at startup via ThreadPoolExecutor.
  - Price (Close): re-fetched on every refresh cycle via yf.download() batch call.

Missing data is treated permissively: a symbol with no float/institution data
still passes those filters (it is not excluded for lack of data).
Invalid / delisted symbols are skipped with a warning log entry.

Usage:
    import universe_finder

    universe_finder.start(
        watchlist_path='./watchlist.txt',
        max_institution_pct=20,
        max_float_m=20,
        max_price=10,
        refresh_minutes=5,
    )

    symbols = universe_finder.get_universe()
    # ['HOOD', 'CLOV', ...]
"""

import threading
import logging
import datetime
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    import yfinance as yf
except ImportError:
    raise ImportError("yfinance is required. Install with: pip install yfinance")


# ─── Rate-limit / crumb resilience constants ──────────────────────────────────

_FUNDAMENTALS_WORKERS = 3
_FUNDAMENTALS_RETRY_DELAYS = (0.3, 1.3, 2.3)   # seconds, indexed by attempt
_PRICE_CHUNK_SIZE = 200
_PRICE_CHUNK_SLEEP = 3.0                         # seconds between chunks
_PRICE_RETRY_DELAYS = (5, 10)                    # pauses between 3 attempts


# ─── Module-level state ────────────────────────────────────────────────────────

_universe: list = []
_universe_lock = threading.Lock()
_first_fetch_done = threading.Event()
_shutdown = threading.Event()

_config: dict = {}
# {symbol: {'float_m': float|None, 'institution_pct': float|None}}
_fundamentals_cache: dict = {}

logger = logging.getLogger("universe_finder")


# ─── Public API ───────────────────────────────────────────────────────────────

def start(
    watchlist_path,
    max_institution_pct: float = 20.0,
    max_float_m: float = 20.0,
    max_price: float = 10.0,
    refresh_minutes: int = 5,
) -> None:
    """
    Start universe_finder. Reads the watchlist, fetches fundamentals, applies
    all filters, and launches a background refresh thread.

    Blocks until the first successful fetch completes before returning.

    Args:
        watchlist_path:      Path to a plain-text file with one ticker symbol per line,
                             or a Python list of symbol strings.
                             Lines starting with '#' are treated as comments.
        max_institution_pct: Maximum institutional ownership allowed (%), exclusive.
                             e.g. 20 → symbols with >= 20% institution are excluded.
        max_float_m:         Maximum float allowed (millions of shares), exclusive.
                             e.g. 20 → symbols with float >= 20M shares are excluded.
        max_price:           Maximum last close price allowed ($), exclusive.
                             e.g. 10 → symbols trading at >= $10 are excluded.
        refresh_minutes:     How often the price-based filter is re-applied (minutes).
                             Fundamentals are NOT re-fetched on subsequent cycles.
    """
    global _config

    _config = {
        "watchlist_path": watchlist_path,
        "max_institution_pct": max_institution_pct,
        "max_float_m": max_float_m,
        "max_price": max_price,
        "refresh_seconds": refresh_minutes * 60,
    }

    _setup_logging()

    logger.info("=" * 60)
    logger.info("universe_finder starting")
    logger.info(
        f"Filters: institution < {max_institution_pct}% | "
        f"float < {max_float_m}M shares | price < ${max_price}"
    )
    logger.info(f"Refresh interval: {refresh_minutes} min")

    symbols = _read_watchlist(watchlist_path)
    if not symbols:
        logger.warning("Watchlist is empty or unreadable — universe will be empty.")
        _first_fetch_done.set()
        return

    logger.info(f"Watchlist loaded: {len(symbols)} symbols: {symbols}")

    # Step 1: fetch fundamentals once at startup (concurrent)
    _fetch_fundamentals(symbols)

    # Step 2: initial price fetch + filter pass
    _refresh_universe(symbols)

    # Unblock any callers waiting on get_universe()
    _first_fetch_done.set()

    # Step 3: launch background refresh thread (daemon — auto-killed with process)
    t = threading.Thread(
        target=_refresh_loop,
        args=(symbols,),
        daemon=True,
        name="universe_finder_refresh",
    )
    t.start()
    logger.info("Background refresh thread started.")


def get_universe() -> list:
    """
    Return the current filtered list of symbols.

    Blocks only on the very first call (until the initial fetch completes).
    All subsequent calls return immediately from the in-memory cache.

    Returns:
        A copy of the filtered symbol list, e.g. ['HOOD', 'CLOV', 'AMC'].
        Returns an empty list if the watchlist was empty or unreadable.
    """
    _first_fetch_done.wait()
    with _universe_lock:
        return list(_universe)


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """
    Configure the 'universe_finder' logger to write to:
      ./runs/DD-Mon-YYYY/universe_finder.log
    The date is fixed at the time start() is called (no daily rollover).
    Duplicate handlers are avoided if start() is called more than once.
    """
    date_str = datetime.datetime.now().strftime("%d-%b-%Y")  # e.g. 16-Mar-2026
    log_dir = Path("runs") / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "universe_finder.log"

    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
        )
        logger.addHandler(fh)

    logger.info(f"Log file: {log_path.resolve()}")


# ─── Watchlist I/O ────────────────────────────────────────────────────────────

def _read_watchlist(path) -> list:
    """
    Read one symbol per line from a plain-text file, or normalise a pre-built list.
    Lines starting with '#' are skipped (comments).
    Returns a de-duplicated list of uppercase symbols.
    """
    if isinstance(path, list):
        seen = set()
        symbols = []
        for item in path:
            sym = str(item).strip().upper()
            if sym and not sym.startswith("#") and sym not in seen:
                symbols.append(sym)
                seen.add(sym)
        logger.debug(f"Received {len(symbols)} unique symbols from list input")
        return symbols

    p = Path(path)
    if not p.exists():
        logger.error(f"Watchlist file not found: {p.resolve()}")
        return []

    seen = set()
    symbols = []
    with p.open() as f:
        for line in f:
            sym = line.strip().upper()
            if sym and not sym.startswith("#") and sym not in seen:
                symbols.append(sym)
                seen.add(sym)

    logger.debug(f"Read {len(symbols)} unique symbols from {p.resolve()}")
    return symbols


# ─── Fundamentals fetch (startup only) ───────────────────────────────────────

def _fetch_fundamentals(symbols: list) -> None:
    """
    Fetch float (shares) and institutional ownership % from yfinance for every
    symbol, concurrently via ThreadPoolExecutor (max_workers=_FUNDAMENTALS_WORKERS).
    Results are stored in the module-level _fundamentals_cache.
    Symbols with no usable yfinance data are cached as {None, None}
    and logged as warnings (they are NOT excluded — permissive policy).

    Rate-limit resilience: each request is preceded by a short sleep and retried
    up to 3 times (with increasing backoff) on 401/crumb or 429 errors.
    """
    logger.info(
        f"Fetching fundamentals for {len(symbols)} symbols "
        f"(workers={_FUNDAMENTALS_WORKERS}, retries=3)..."
    )

    def _is_rate_limit_or_crumb(e: Exception) -> bool:
        msg = str(e)
        return any(kw in msg for kw in ("401", "Unauthorized", "Too Many Requests", "Rate"))

    def _fetch_one(sym: str):
        for attempt in range(3):
            time.sleep(_FUNDAMENTALS_RETRY_DELAYS[attempt])
            try:
                info = yf.Ticker(sym).info

                # Heuristic for unknown/delisted symbols: yfinance returns a very
                # sparse dict (often just {'trailingPegRatio': None} or similar).
                if not info or info.get("quoteType") is None:
                    logger.warning(f"{sym}: No yfinance data found — skipping (included permissively)")
                    return sym, None, None

                # Float in raw share count → convert to millions
                float_shares = info.get("floatShares")
                float_m = (float_shares / 1_000_000) if float_shares is not None else None

                # heldPercentInstitutions is a 0.0–1.0 fraction → convert to %
                institution_raw = info.get("heldPercentInstitutions")
                institution_pct = (institution_raw * 100) if institution_raw is not None else None

                if float_m is not None and institution_pct is not None:
                    logger.debug(
                        f"{sym}: fundamentals fetched — "
                        f"float={float_m:.2f}M shares, institution={institution_pct:.1f}%"
                    )
                else:
                    logger.debug(
                        f"{sym}: partial fundamentals — "
                        f"float={float_m}, institution={institution_pct}"
                    )

                return sym, float_m, institution_pct

            except Exception as e:
                if _is_rate_limit_or_crumb(e) and attempt < 2:
                    logger.warning(
                        f"{sym}: rate-limit/crumb error (attempt {attempt+1}/3), retrying — {e}"
                    )
                    continue
                logger.warning(f"{sym}: Exception fetching fundamentals — {e}")
                return sym, None, None

    with ThreadPoolExecutor(max_workers=_FUNDAMENTALS_WORKERS) as executor:
        futures = {executor.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, float_m, institution_pct = future.result()
            _fundamentals_cache[sym] = {
                "float_m": float_m,
                "institution_pct": institution_pct,
            }

    cached_count = sum(
        1 for v in _fundamentals_cache.values()
        if v["float_m"] is not None or v["institution_pct"] is not None
    )
    logger.info(
        f"Fundamentals fetch complete: {cached_count}/{len(symbols)} symbols "
        f"had usable data."
    )


# ─── Price fetch (every refresh cycle) ───────────────────────────────────────

def _fetch_prices(symbols: list) -> dict:
    """
    Batch-fetch the latest close prices for all symbols via yf.download().
    Uses period='1d', interval='1d' — returns the most recent full-day close.

    Large symbol lists are split into chunks of _PRICE_CHUNK_SIZE to avoid
    Yahoo Finance rate limits.  Each chunk is retried up to 3 times on
    rate-limit errors, with exponential backoff.

    Returns:
        Dict mapping symbol -> close price (float) or None if unavailable.
    """
    prices = {sym: None for sym in symbols}

    if not symbols:
        return prices

    # Split into chunks
    chunks = [
        symbols[i : i + _PRICE_CHUNK_SIZE]
        for i in range(0, len(symbols), _PRICE_CHUNK_SIZE)
    ]
    total_chunks = len(chunks)
    logger.info(
        f"Fetching prices: {len(symbols)} symbols in {total_chunks} chunk(s) "
        f"of up to {_PRICE_CHUNK_SIZE}"
    )

    def _is_rate_limit(e: Exception) -> bool:
        msg = str(e)
        return any(kw in msg for kw in ("429", "Too Many Requests", "Rate", "401", "Unauthorized"))

    for chunk_idx, chunk in enumerate(chunks):
        for attempt in range(3):
            try:
                df = yf.download(
                    tickers=chunk,
                    period="1d",
                    interval="1d",
                    auto_adjust=True,
                    progress=False,
                    threads=True,
                )

                if df is None or df.empty:
                    logger.warning(
                        f"Chunk {chunk_idx+1}/{total_chunks}: yf.download() returned empty DataFrame"
                    )
                    break

                # Access Close column (handles both single-ticker Series and multi-ticker DataFrame)
                try:
                    close = df["Close"]
                except KeyError:
                    logger.warning(
                        f"Chunk {chunk_idx+1}/{total_chunks}: 'Close' column not found"
                    )
                    break

                if hasattr(close, "columns"):
                    # Multi-ticker: close is a DataFrame with symbol columns
                    for sym in chunk:
                        if sym in close.columns:
                            series = close[sym].dropna()
                            if not series.empty:
                                prices[sym] = float(series.iloc[-1])
                                logger.debug(f"{sym}: close price = ${prices[sym]:.2f}")
                            else:
                                logger.debug(f"{sym}: close column present but all NaN")
                        else:
                            logger.debug(f"{sym}: not returned by yf.download()")
                else:
                    # Single-ticker: close is a Series
                    series = close.dropna()
                    if chunk and not series.empty:
                        prices[chunk[0]] = float(series.iloc[-1])
                        logger.debug(f"{chunk[0]}: close price = ${prices[chunk[0]]:.2f}")

                logger.debug(
                    f"Chunk {chunk_idx+1}/{total_chunks} complete ({len(chunk)} symbols)"
                )
                break  # success — move on to next chunk

            except Exception as e:
                if _is_rate_limit(e) and attempt < 2:
                    delay = _PRICE_RETRY_DELAYS[attempt]
                    logger.warning(
                        f"Chunk {chunk_idx+1}/{total_chunks}: rate-limit error "
                        f"(attempt {attempt+1}/3), retrying in {delay}s — {e}"
                    )
                    time.sleep(delay)
                    continue
                logger.error(
                    f"Chunk {chunk_idx+1}/{total_chunks}: yf.download() failed — {e}"
                )
                break

        # Sleep between chunks (skip after the last one)
        if chunk_idx < total_chunks - 1:
            time.sleep(_PRICE_CHUNK_SLEEP)

    return prices


# ─── Filter logic ─────────────────────────────────────────────────────────────

def _apply_filters(symbols: list, prices: dict) -> list:
    """
    Apply all three filters with AND logic.
    Missing metrics (None) are treated as passing (permissive).

    Returns:
        Ordered list of symbols that passed all active filters.
    """
    passed = []
    failed = []
    max_inst = _config["max_institution_pct"]
    max_float = _config["max_float_m"]
    max_px = _config["max_price"]

    for sym in symbols:
        fund = _fundamentals_cache.get(sym, {})
        float_m = fund.get("float_m")
        institution_pct = fund.get("institution_pct")
        price = prices.get(sym)

        # None = data missing = treated as passing
        passes_institution = institution_pct is None or institution_pct < max_inst
        passes_float = float_m is None or float_m < max_float
        passes_price = price is None or price < max_px

        if passes_institution and passes_float and passes_price:
            passed.append(sym)
            _log_decision(sym, "PASS", float_m, institution_pct, price)
        else:
            reasons = []
            if not passes_institution:
                reasons.append(f"institution={institution_pct:.1f}% >= {max_inst}%")
            if not passes_float:
                reasons.append(f"float={float_m:.2f}M >= {max_float}M")
            if not passes_price:
                reasons.append(f"price=${price:.2f} >= ${max_px}")
            failed.append(sym)
            logger.debug(f"{sym}: FAIL — {'; '.join(reasons)}")

    logger.debug(
        f"Filter pass: {len(passed)} passed, {len(failed)} failed. "
        f"Failed: {failed if failed else '—'}"
    )
    return passed


def _log_decision(sym, verdict, float_m, institution_pct, price):
    parts = []
    parts.append(f"institution={'N/A' if institution_pct is None else f'{institution_pct:.1f}%'}")
    parts.append(f"float={'N/A' if float_m is None else f'{float_m:.2f}M'}")
    parts.append(f"price={'N/A' if price is None else f'${price:.2f}'}")
    logger.debug(f"{sym}: {verdict} — {' | '.join(parts)}")


# ─── Refresh logic ────────────────────────────────────────────────────────────

def _refresh_universe(symbols: list) -> None:
    """
    Fetch latest prices, apply all filters, and atomically update the
    in-memory universe list. On yfinance failure, the previous list is kept.
    """
    logger.debug(f"Refreshing universe ({len(symbols)} candidates)...")
    try:
        prices = _fetch_prices(symbols)
        new_universe = _apply_filters(symbols, prices)

        with _universe_lock:
            _universe.clear()
            _universe.extend(new_universe)

        logger.info(
            f"Universe updated: {len(new_universe)}/{len(symbols)} symbols passed — "
            f"{new_universe}"
        )

    except Exception as e:
        logger.error(
            f"Universe refresh failed — keeping previous list intact. Error: {e}"
        )


def _refresh_loop(symbols: list) -> None:
    """
    Background daemon thread: sleeps for refresh_seconds, then re-runs the
    price fetch + filter pass. Exits cleanly when _shutdown is set.
    Fundamentals are NOT re-fetched here (fetched once at startup only).
    """
    interval = _config["refresh_seconds"]
    logger.debug(f"Refresh loop running (interval={interval}s).")

    while True:
        # wait() returns True if _shutdown was set, False on timeout
        triggered = _shutdown.wait(timeout=interval)
        if triggered:
            logger.info("Shutdown requested — refresh loop exiting.")
            break
        _refresh_universe(symbols)
