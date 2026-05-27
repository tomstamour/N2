#!/usr/bin/env python3
"""
NASDAQ_symbols_data.py
----------------------
Fetches all major-exchange stock symbols (NASDAQ, NYSE, NYSE MKT, NYSE ARCA,
BATS, IEXG) from the Nasdaq FTP symbol directory and enriches each with its
float value (in millions of shares) and market capitalization (in millions of
USD).

Fundamentals data chain (per ticker):
  Primary : finvizfinance  → ticker_fundament()["Shs Float"] + ["Market Cap"]
  Fallback: yfinance       → info["floatShares"] + info["marketCap"]

Float_M and MarketCap_M are sourced independently: each metric falls through to
the next source on its own, so a finviz row that lists a Market Cap but no float
(`Shs Float = '-'`) still gets its float from the yfinance fallback. The two
values can therefore come from different sources. Float_Source records where the
**float** came from (falling back to the market-cap source, then "none"); there
is no separate MarketCap_Source.

Results are cached to ./data/nasdaq_symbols_data.tsv.  The cache is reused
for up to 24 hours; run with force_refresh=True to bypass it. Caches written
by older versions (missing the MarketCap_M column) are silently invalidated.

Public API:
    from NASDAQ_symbols_data import build_dataframe

    df = build_dataframe()
    # df columns: Symbol | Exchange | Float_M | MarketCap_M | Float_Source

    df = build_dataframe(force_refresh=True)   # force a fresh fetch

CLI:
    python NASDAQ_symbols_data.py
    python NASDAQ_symbols_data.py --refresh     # ignore cache
"""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    raise ImportError("pandas is required. Install with: pip install pandas")

try:
    import requests
except ImportError:
    raise ImportError("requests is required. Install with: pip install requests")


# ─── Paths & constants ────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).parent
_DATA_DIR   = _SCRIPT_DIR / "data"
_CACHE_FILE = _DATA_DIR / "nasdaq_symbols_data.tsv"
_CACHE_MAX_AGE = timedelta(hours=24)

_NASDAQ_SCREENER_URL = (
    "https://api.nasdaq.com/api/screener/stocks"
    "?tableonly=true&limit=10000&exchange={exchange}&download=true"
)
_NASDAQ_SUMMARY_URL = "https://api.nasdaq.com/api/quote/{sym}/summary?assetclass=stocks"

# Exchanges to pull from the Nasdaq screener
_SCREENER_EXCHANGES = ("nasdaq", "nyse", "amex")

_SCREENER_EXCHANGE_LABEL = {
    "nasdaq": "NASDAQ",
    "nyse":   "NYSE",
    "amex":   "NYSE MKT",
}

_MAX_WORKERS = 3  # yfinance rate-limits badly above ~3 concurrent requests
_PROGRESS_EVERY = 100   # log a progress line every N symbols

_API_HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept":     "application/json",
}

logger = logging.getLogger("NASDAQ_symbols_data")


# ─── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    """
    Configure the 'NASDAQ_symbols_data' logger to write to:
      ./runs/DD-Mon-YYYY/NASDAQ_symbols_data.log
    Duplicate handlers are avoided if build_dataframe() is called more than once.
    """
    date_str = datetime.now().strftime("%d-%b-%Y")
    log_dir  = _SCRIPT_DIR / "runs" / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "NASDAQ_symbols_data.log"

    logger.setLevel(logging.DEBUG)

    if not logger.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(ch)

    # Silence yfinance's internal logger (it otherwise prints 401/crumb warnings)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)


# ─── Symbol list ──────────────────────────────────────────────────────────────

def _fetch_symbol_list() -> pd.DataFrame:
    """
    Fetch all major-exchange symbols via the Nasdaq.com screener API.
    Pulls NASDAQ, NYSE, and AMEX (NYSE MKT) separately, then deduplicates.
    Returns a DataFrame[Symbol, Exchange].
    """
    rows: list[dict] = []

    for exchange in _SCREENER_EXCHANGES:
        url   = _NASDAQ_SCREENER_URL.format(exchange=exchange)
        label = _SCREENER_EXCHANGE_LABEL[exchange]
        logger.debug(f"Fetching screener: {exchange} …")
        resp  = requests.get(url, headers=_API_HEADERS, timeout=30)
        resp.raise_for_status()
        payload = resp.json()
        ticker_rows = payload.get("data", {}).get("rows", [])
        for item in ticker_rows:
            sym = str(item.get("symbol", "")).strip()
            if sym:
                rows.append({"Symbol": sym, "Exchange": label})
        logger.debug(f"  {label}: {len(ticker_rows)} symbols")

    df = pd.DataFrame(rows)
    df.drop_duplicates(subset="Symbol", keep="first", inplace=True)
    df.reset_index(drop=True, inplace=True)
    logger.info(f"Symbol list: {len(df)} unique symbols across all exchanges")
    return df


# ─── Float parsers ────────────────────────────────────────────────────────────

def _parse_float_str(val: str | None) -> float | None:
    """
    Convert a Finviz/Nasdaq formatted number string to a float in millions.

    Examples:
        "12.34M"  →  12.34
        "1.2B"    →  1200.0
        "450K"    →  0.45
        "-"       →  None
        ""        →  None
    """
    if not val or not isinstance(val, str):
        return None
    val = val.strip().replace(",", "")
    if val in ("-", "N/A", ""):
        return None
    try:
        suffix = val[-1].upper()
        number = float(val[:-1])
        if suffix == "B":
            return round(number * 1_000, 4)
        if suffix == "M":
            return round(number, 4)
        if suffix == "K":
            return round(number / 1_000, 4)
        # no suffix — assume raw share count
        return round(float(val) / 1_000_000, 4)
    except (ValueError, IndexError):
        return None


def _finviz_fundamentals(sym: str) -> tuple[float | None, float | None]:
    """Primary fundamentals source: finvizfinance → ticker_fundament().

    Returns (float_m, market_cap_m); either may be None if the symbol's row in
    Finviz lacks the field. Both values are extracted from the same dict, so
    this stays a single HTTP call per symbol.

    Rate-limited to ~1 req/s; retries up to 3 times on 403/429 with 30s backoff.
    """
    try:
        from finvizfinance.quote import finvizfinance  # type: ignore
    except ImportError:
        return None, None  # package not installed — skip silently

    for attempt in range(3):
        try:
            time.sleep(1.0)  # ~1 req/s to avoid finviz.com blocking
            data = finvizfinance(sym).ticker_fundament()
            # Finviz formats both fields the same way ("12.34M", "1.20B", "-"),
            # so _parse_float_str handles both. The unit interpretation is
            # caller-specific: shares-millions for float, USD-millions for cap.
            return (
                _parse_float_str(data.get("Shs Float")),
                _parse_float_str(data.get("Market Cap")),
            )
        except Exception as exc:
            exc_str = str(exc)
            if "403" in exc_str or "429" in exc_str or "Too Many" in exc_str:
                wait = 30 * (attempt + 1)
                logger.warning(f"{sym} finviz rate limited (attempt {attempt + 1}/3), waiting {wait}s")
                time.sleep(wait)
                continue
            logger.debug(f"{sym} finviz error: {exc}")
            return None, None
    logger.warning(f"{sym} finviz: gave up after 3 rate-limit retries")
    return None, None


def _float_from_nasdaq(sym: str) -> float | None:
    """
    Fallback 1: Nasdaq.com summary API.
    Endpoint: https://api.nasdaq.com/api/quote/{sym}/summary?assetclass=stocks
    JSON path: data.summaryData.ShareFloat.value
    NOTE: As of 2026-04 the ShareFloat field has been removed from this endpoint.
          This source currently returns None for all symbols.
    """
    try:
        url = _NASDAQ_SUMMARY_URL.format(sym=sym.lower())
        resp = requests.get(url, headers=_API_HEADERS, timeout=10)
        resp.raise_for_status()
        payload = resp.json()
        raw = payload["data"]["summaryData"]["ShareFloat"]["value"]
        return _parse_float_str(raw)
    except KeyError:
        return None  # ShareFloat field not in response (API changed)
    except Exception as exc:
        logger.debug(f"{sym} nasdaq summary error: {exc}")
        return None


def _yfinance_fundamentals(sym: str) -> tuple[float | None, float | None]:
    """
    Fallback fundamentals source: yfinance `.info` dict.

    Returns (float_m, market_cap_m). Float falls back to floatShares →
    sharesOutstanding; market cap reads `marketCap` (raw USD, converted to
    millions). Both come from the same single `.info` call.

    Retries up to 3 times on rate-limiting (429) with 60s/120s/180s backoff.
    """
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return None, None  # package not installed — skip silently

    for attempt in range(3):
        try:
            time.sleep(0.5)  # ~2 req/s per worker
            info = yf.Ticker(sym).info
            shares = info.get("floatShares") or info.get("sharesOutstanding")
            float_m = round(shares / 1_000_000, 4) if shares and shares > 0 else None
            mcap_usd = info.get("marketCap")
            mcap_m = round(mcap_usd / 1_000_000, 4) if mcap_usd and mcap_usd > 0 else None
            return float_m, mcap_m
        except Exception as exc:
            exc_str = str(exc)
            if "Too Many Requests" in exc_str or "Rate limit" in exc_str:
                wait = 60 * (attempt + 1)
                logger.warning(f"{sym} yfinance rate limited (attempt {attempt + 1}/3), waiting {wait}s")
                time.sleep(wait)
                continue
            logger.debug(f"{sym} yfinance error: {exc}")
            return None, None
    logger.warning(f"{sym} yfinance: gave up after 3 rate-limit retries")
    return None, None


# ─── Orchestration ────────────────────────────────────────────────────────────

def _fetch_fundamentals(sym: str) -> tuple[float | None, float | None, str]:
    """
    Try fundamentals sources in priority order, sourcing each metric
    independently.
    Returns (float_millions, market_cap_millions, source_name).
    source_name is one of: "finviz", "yfinance", "none".

    Each metric (float, market cap) falls through to the next source on its own:
    we keep querying sources until BOTH values are filled (or sources run out),
    short-circuiting as soon as both are present so a fully-satisfying finviz row
    incurs no extra yfinance call. This fixes the case where finviz lists a
    Market Cap but `Shs Float = '-'` (e.g. TURB): the float now comes from the
    yfinance fallback instead of being lost because finviz already "won" on the
    market cap.

    `source_name` reports where the **float** came from (the column is
    Float_Source), falling back to the market-cap source and then "none". So
    float and market cap may come from different sources; the returned name
    tracks the float's origin.
    """
    float_m = mcap_m = None
    float_src = mcap_src = None
    for fn, name in [
        (_finviz_fundamentals,   "finviz"),
        (_yfinance_fundamentals, "yfinance"),
    ]:
        if float_m is not None and mcap_m is not None:
            break  # both filled — don't make further calls
        f, m = fn(sym)
        if float_m is None and f is not None:
            float_m, float_src = f, name
        if mcap_m is None and m is not None:
            mcap_m, mcap_src = m, name
    return float_m, mcap_m, (float_src or mcap_src or "none")


def _fetch_all_fundamentals(
    symbols: list[str],
) -> dict[str, tuple[float | None, float | None, str]]:
    """
    Fetch fundamentals for all symbols concurrently (_MAX_WORKERS workers).
    Returns {sym: (float_m, market_cap_m, source)}.
    """
    results: dict[str, tuple[float | None, float | None, str]] = {}
    total = len(symbols)

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as pool:
        # Stagger submissions slightly to avoid a burst at t=0
        futures: dict = {}
        for sym in symbols:
            futures[pool.submit(_fetch_fundamentals, sym)] = sym

        completed = 0
        for future in as_completed(futures):
            sym = futures[future]
            try:
                float_m, mcap_m, source = future.result()
            except Exception as exc:
                logger.warning(f"{sym}: unexpected error — {exc}")
                float_m, mcap_m, source = None, None, "none"
            results[sym] = (float_m, mcap_m, source)
            completed += 1
            if completed % _PROGRESS_EVERY == 0 or completed == total:
                logger.debug(f"Fundamentals fetch progress: {completed}/{total}")

    return results


# ─── Public API ───────────────────────────────────────────────────────────────

def build_dataframe(force_refresh: bool = False) -> pd.DataFrame:
    """
    Build (or reload from cache) the full exchange symbol DataFrame.

    Columns:
        Symbol       — ticker string
        Exchange     — human-readable exchange name
        Float_M      — float in millions of shares (NaN if unavailable)
        MarketCap_M  — market cap in millions of USD (NaN if unavailable);
                       may come from a different source than Float_M
        Float_Source — which source provided the **float** ("finviz" /
                       "yfinance" / "none"), falling back to the market-cap
                       source; usually also identifies the MarketCap_M source,
                       but the two can diverge when finviz supplies only the
                       market cap

    The result is cached to ./data/nasdaq_symbols_data.tsv and reloaded on
    subsequent calls within 24 hours.  Pass force_refresh=True to bypass.

    A cache file written by an older version (missing the MarketCap_M column)
    is treated as stale and a fresh fetch is performed automatically — no
    manual --refresh needed for the schema bump.

    Returns:
        pd.DataFrame with the columns above.
    """
    _setup_logging()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # ── Cache check ───────────────────────────────────────────────────────────
    if not force_refresh and _CACHE_FILE.exists():
        age = datetime.now() - datetime.fromtimestamp(_CACHE_FILE.stat().st_mtime)
        if age < _CACHE_MAX_AGE:
            cached = pd.read_csv(_CACHE_FILE, sep="\t")
            if "MarketCap_M" not in cached.columns:
                logger.info(
                    f"Cache at {_CACHE_FILE} predates MarketCap_M column — "
                    "invalidating and re-fetching"
                )
                # fall through to fresh fetch
            else:
                logger.info(
                    f"Loading from cache ({_CACHE_FILE}, age {int(age.total_seconds() / 60)} min)"
                )
                return cached
        else:
            logger.info(f"Cache expired ({int(age.total_seconds() / 3600):.1f}h old) — re-fetching")

    # ── Fresh fetch ───────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("NASDAQ_symbols_data: starting fresh fetch")

    sym_df  = _fetch_symbol_list()
    symbols = sym_df["Symbol"].tolist()

    logger.info(f"Fetching fundamentals for {len(symbols)} symbols with {_MAX_WORKERS} workers …")
    results = _fetch_all_fundamentals(symbols)

    # `results.get` default keeps the .map total — any symbol missing from
    # results (shouldn't happen, but belt-and-braces) flows through as
    # (None, None, "none").
    sym_df["Float_M"]      = sym_df["Symbol"].map(lambda s: results.get(s, (None, None, "none"))[0])
    sym_df["MarketCap_M"]  = sym_df["Symbol"].map(lambda s: results.get(s, (None, None, "none"))[1])
    sym_df["Float_Source"] = sym_df["Symbol"].map(lambda s: results.get(s, (None, None, "none"))[2])

    # ── Save to TSV ───────────────────────────────────────────────────────────
    sym_df.to_csv(_CACHE_FILE, sep="\t", index=False)
    logger.info(f"Saved to {_CACHE_FILE}")

    n = len(sym_df)
    float_cov = sym_df["Float_M"].notna().sum()
    mcap_cov  = sym_df["MarketCap_M"].notna().sum()
    logger.info(
        f"Done. {n} symbols | float coverage: {float_cov} ({float_cov/n*100:.1f}%) "
        f"| market cap coverage: {mcap_cov} ({mcap_cov/n*100:.1f}%)"
    )

    return sym_df


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch NASDAQ+NYSE symbol list with float data")
    ap.add_argument(
        "--refresh", action="store_true",
        help="Ignore cache and force a fresh fetch",
    )
    args = ap.parse_args()

    df = build_dataframe(force_refresh=args.refresh)

    total      = len(df)
    float_cov  = df["Float_M"].notna().sum()
    mcap_cov   = df["MarketCap_M"].notna().sum()
    float_pct  = f"{float_cov/total*100:.1f}%" if total else "n/a"
    mcap_pct   = f"{mcap_cov/total*100:.1f}%"  if total else "n/a"
    print(f"\nTotal symbols      : {total}")
    print(f"Float coverage     : {float_cov} / {total} ({float_pct})")
    print(f"Market cap coverage: {mcap_cov} / {total} ({mcap_pct})")
    print(f"\nBy exchange:")
    print(df["Exchange"].value_counts().to_string())
    print(f"\nBy source:")
    print(df["Float_Source"].value_counts().to_string())
    print(f"\nSample (first 10 rows):")
    print(df.head(10).to_string(index=False))
