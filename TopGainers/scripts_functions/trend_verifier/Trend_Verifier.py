#!/usr/bin/env python3
"""
Trend Verifier (TVR)
GUS3 - Gap Up Short Algorithmic Trading System

CLI-driven script that enriches GusTradeTable files with trend slope analysis
for 1-year, 6-month, and 3-month periods using linear regression on daily closing prices.

Input: GusTradeTable (tab-delimited format)
Output: Enriched GusTradeTable with 1yrT, 6moT, 3moT columns (in-place modification)

Usage:
    python3 Trend_Verifier.py --input <GTT_file> --client-id <id> [options]

Example:
    python3 Trend_Verifier.py --input ./runs/21-Nov-2025/GusTradeTable.txt --client-id 600
"""

import os
import sys
import time
import argparse
import datetime
import logging
import csv
import numpy as np
import threading
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

# IBAPI imports
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
except ImportError:
    print("Error: ibapi module not found. Please install it with: pip install ibapi")
    sys.exit(1)

# =====================
# Global State
# =====================
app_state = {
    "historical_data": {},
    "connection_ready": threading.Event(),
    "shutdown": threading.Event(),
    "cache": {},  # In-memory cache for trend slopes
}

# =====================
# Cache Management
# =====================
class TrendCache:
    """Manages persistent caching of trend slope data."""

    def __init__(self, cache_path: str, ttl_seconds: int = 86400):
        """
        Initialize cache manager.

        Args:
            cache_path: Path to CSV cache file
            ttl_seconds: Time-to-live in seconds (default: 24 hours)
        """
        self.cache_path = Path(cache_path)
        self.ttl_seconds = ttl_seconds
        self.cache = {}  # symbol -> {trend_1y, trend_6m, trend_3m, cached_at, bars_count}

        # Ensure cache directory exists
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing cache
        self.load()

    def load(self):
        """Load cache from CSV file."""
        if not self.cache_path.exists():
            return

        try:
            with open(self.cache_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = row['symbol']
                    self.cache[symbol] = {
                        'trend_1y': float(row['trend_1y']) if row['trend_1y'] != 'None' else None,
                        'trend_6m': float(row['trend_6m']) if row['trend_6m'] != 'None' else None,
                        'trend_3m': float(row['trend_3m']) if row['trend_3m'] != 'None' else None,
                        'cached_at': datetime.datetime.fromisoformat(row['cached_at']),
                        'bars_count': int(row['bars_count'])
                    }
        except Exception as e:
            logging.warning(f"Error loading cache: {e}")

    def save(self):
        """Save cache to CSV file."""
        try:
            with open(self.cache_path, 'w', newline='') as f:
                fieldnames = ['symbol', 'trend_1y', 'trend_6m', 'trend_3m', 'cached_at', 'bars_count']
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()

                for symbol, data in self.cache.items():
                    writer.writerow({
                        'symbol': symbol,
                        'trend_1y': data['trend_1y'],
                        'trend_6m': data['trend_6m'],
                        'trend_3m': data['trend_3m'],
                        'cached_at': data['cached_at'].isoformat(),
                        'bars_count': data['bars_count']
                    })
        except Exception as e:
            logging.warning(f"Error saving cache: {e}")

    def get(self, symbol: str) -> Optional[Dict]:
        """
        Get cached trend data for symbol if still valid.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Dict with trend data if valid, None if stale or missing
        """
        if symbol not in self.cache:
            return None

        data = self.cache[symbol]
        age = (datetime.datetime.now() - data['cached_at']).total_seconds()

        if age > self.ttl_seconds:
            return None  # Stale data

        return data

    def set(self, symbol: str, trend_1y: float, trend_6m: float, trend_3m: float, bars_count: int):
        """
        Store trend data in cache.

        Args:
            symbol: Stock ticker symbol
            trend_1y: 1-year trend slope
            trend_6m: 6-month trend slope
            trend_3m: 3-month trend slope
            bars_count: Number of bars used for calculation
        """
        self.cache[symbol] = {
            'trend_1y': trend_1y,
            'trend_6m': trend_6m,
            'trend_3m': trend_3m,
            'cached_at': datetime.datetime.now(),
            'bars_count': bars_count
        }

# =====================
# IBAPI Wrapper
# =====================
class TrendWrapper(EWrapper):
    def __init__(self):
        EWrapper.__init__(self)
        self.next_order_id = None
        self.pending_historical_requests = {}  # reqId -> symbol mapping
        self.historical_data_lock = threading.Lock()

    def error(self, reqId, errorCode, errorString):
        """Handle error messages from IB."""
        # Ignore informational messages
        if errorCode in [2104, 2106, 2158, 2107, 2119, 2176]:
            logging.debug(f"Info (Code {errorCode}): {errorString}")
            return

        logging.error(f"Error - ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}")

        # Handle fatal connection errors
        if errorCode in [504, 1100, 1101, 1102, 1300]:
            logging.critical("Fatal connection error. Shutting down.")
            app_state["shutdown"].set()

    def nextValidId(self, orderId: int):
        """Callback when connection is established."""
        super().nextValidId(orderId)
        self.next_order_id = orderId
        logging.info(f"Connected to IB. Next valid order ID: {orderId}")
        app_state["connection_ready"].set()

    def historicalData(self, reqId: int, bar):
        """Callback for historical data bars."""
        super().historicalData(reqId, bar)

        # Extract symbol from reqId mapping
        symbol = self.pending_historical_requests.get(reqId)

        if symbol and bar.date:
            try:
                # Parse bar date (format: YYYYMMDD)
                dt_obj = datetime.datetime.strptime(bar.date, "%Y%m%d")

                # Store the daily bar
                with self.historical_data_lock:
                    if symbol not in app_state["historical_data"]:
                        app_state["historical_data"][symbol] = []

                    app_state["historical_data"][symbol].append({
                        'date': dt_obj,
                        'close': bar.close
                    })

            except Exception as e:
                logging.error(f"Error parsing historical data for {symbol}: {e}")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback when historical data request is complete."""
        super().historicalDataEnd(reqId, start, end)

        symbol = self.pending_historical_requests.get(reqId)
        if symbol:
            bar_count = len(app_state["historical_data"].get(symbol, []))
            logging.debug(f"{symbol}: Received {bar_count} daily bars")

            # Remove from pending
            with self.historical_data_lock:
                if reqId in self.pending_historical_requests:
                    del self.pending_historical_requests[reqId]

# =====================
# IBAPI Client
# =====================
class TrendClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)

# =====================
# Trend Analysis Functions
# =====================
def calculate_trend_slopes(bars: List[Dict]) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """
    Calculate trend slopes for 1Y, 6M, and 3M periods using linear regression.

    Args:
        bars: List of daily bar dictionaries with 'date' and 'close' keys

    Returns:
        Tuple of (1Y_slope, 6M_slope, 3M_slope), each rounded to 2 decimals or None
    """
    if not bars or len(bars) < 63:  # Need at least 63 days for 3-month trend
        logging.debug(f"Insufficient data for trend calculation: {len(bars)} bars")
        return (None, None, None)

    # Sort by date and extract closing prices
    bars_sorted = sorted(bars, key=lambda b: b['date'])
    close_prices = np.array([bar['close'] for bar in bars_sorted])

    # Calculate slopes for different periods
    slope_1y = None
    slope_6m = None
    slope_3m = None

    # 1-Year: All available data
    if len(bars_sorted) >= 63:
        x_1y = np.arange(len(close_prices))
        slope_1y_raw, _ = np.polyfit(x_1y, close_prices, 1)
        slope_1y = round(slope_1y_raw, 2)  # 2 decimal places

    # 6-Month: Last ~126 trading days (or available)
    days_6m = min(126, len(bars_sorted))
    if days_6m >= 63:
        close_6m = close_prices[-days_6m:]
        x_6m = np.arange(len(close_6m))
        slope_6m_raw, _ = np.polyfit(x_6m, close_6m, 1)
        slope_6m = round(slope_6m_raw, 2)  # 2 decimal places

    # 3-Month: Last ~63 trading days
    days_3m = min(63, len(bars_sorted))
    if days_3m >= 63:
        close_3m = close_prices[-days_3m:]
        x_3m = np.arange(len(close_3m))
        slope_3m_raw, _ = np.polyfit(x_3m, close_3m, 1)
        slope_3m = round(slope_3m_raw, 2)  # 2 decimal places

    return (slope_1y, slope_6m, slope_3m)


def fetch_and_calculate_trend(symbol: str, client: TrendClient, wrapper: TrendWrapper,
                              cache: TrendCache, next_req_id: int) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    """
    Fetch historical data and calculate trend slopes for a single symbol.

    Args:
        symbol: Stock ticker
        client: IB client instance
        wrapper: IB wrapper instance
        cache: Cache manager
        next_req_id: Request ID to use

    Returns:
        Tuple of (symbol, 1Y_slope, 6M_slope, 3M_slope)
    """
    try:
        # Check cache first
        cached_data = cache.get(symbol)
        if cached_data:
            logging.info(f"{symbol}: Using cached trend data (age: {(datetime.datetime.now() - cached_data['cached_at']).seconds}s)")
            return (symbol, cached_data['trend_1y'], cached_data['trend_6m'], cached_data['trend_3m'])

        # Create contract
        contract = Contract()
        contract.symbol = symbol
        contract.secType = "STK"
        contract.exchange = "SMART"
        contract.currency = "USD"

        # Clear any existing data for this symbol
        if symbol in app_state["historical_data"]:
            del app_state["historical_data"][symbol]

        # Register request
        wrapper.pending_historical_requests[next_req_id] = symbol

        # Request 1 year of daily bars
        client.reqHistoricalData(
            reqId=next_req_id,
            contract=contract,
            endDateTime='',  # Current time
            durationStr='1 Y',
            barSizeSetting='1 day',
            whatToShow='TRADES',
            useRTH=1,  # Regular trading hours only
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )

        # Wait for completion (with timeout)
        timeout = 0
        while next_req_id in wrapper.pending_historical_requests and timeout < 100:
            time.sleep(0.1)
            timeout += 1

        # Get bars
        bars = app_state["historical_data"].get(symbol, [])

        if not bars:
            logging.warning(f"{symbol}: No historical data received")
            return (symbol, None, None, None)

        # Calculate slopes
        slope_1y, slope_6m, slope_3m = calculate_trend_slopes(bars)

        # Update cache
        cache.set(symbol, slope_1y, slope_6m, slope_3m, len(bars))

        logging.info(f"{symbol}: Trends calculated - 1Y: {slope_1y}, 6M: {slope_6m}, 3M: {slope_3m}")

        return (symbol, slope_1y, slope_6m, slope_3m)

    except Exception as e:
        logging.error(f"{symbol}: Error fetching/calculating trends: {e}")
        return (symbol, None, None, None)


# =====================
# GTT File Processing
# =====================
def enrich_gtt_file(gtt_path: str, client: TrendClient, wrapper: TrendWrapper,
                   cache: TrendCache, logger: logging.Logger) -> bool:
    """
    Enrich GTT file with trend slope columns in-place.

    Args:
        gtt_path: Path to GusTradeTable file
        client: IB client instance
        wrapper: IB wrapper instance
        cache: Cache manager
        logger: Logger instance

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Read GTT file
        with open(gtt_path, 'r') as f:
            reader = csv.reader(f, delimiter='\t')
            header = next(reader)
            rows = list(reader)

        logger.info(f"Read {len(rows)} stocks from {gtt_path}")

        # Check if trend columns already exist
        if '1yrT' in header and '6moT' in header and '3moT' in header:
            logger.info("Trend columns already exist, updating values...")
            idx_1y = header.index('1yrT')
            idx_6m = header.index('6moT')
            idx_3m = header.index('3moT')
        else:
            # Insert trend columns before LastUpdate
            if 'LastUpdate' in header:
                last_update_idx = header.index('LastUpdate')
            else:
                last_update_idx = len(header)

            # Insert in reverse order to maintain correct positioning
            header.insert(last_update_idx, '3moT')
            header.insert(last_update_idx, '6moT')
            header.insert(last_update_idx, '1yrT')

            idx_1y = last_update_idx
            idx_6m = last_update_idx + 1
            idx_3m = last_update_idx + 2

            # Pad existing rows with N/A
            for row in rows:
                row.insert(last_update_idx, 'N/A')
                row.insert(last_update_idx + 1, 'N/A')
                row.insert(last_update_idx + 2, 'N/A')

            logger.info(f"Added trend columns at positions {idx_1y}, {idx_6m}, {idx_3m}")

        # Extract symbols
        symbol_idx = header.index('Symbol')
        symbols = [row[symbol_idx] for row in rows]

        logger.info(f"Processing trends for {len(symbols)} stocks...")

        # Process stocks in parallel
        trend_results = {}
        next_req_id = 6000

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = {}
            for symbol in symbols:
                future = executor.submit(
                    fetch_and_calculate_trend,
                    symbol, client, wrapper, cache, next_req_id
                )
                futures[future] = symbol
                next_req_id += 1
                time.sleep(0.05)  # Small delay between requests

            # Collect results
            for future in as_completed(futures):
                symbol, slope_1y, slope_6m, slope_3m = future.result()
                trend_results[symbol] = (slope_1y, slope_6m, slope_3m)

        # Update rows with trend data
        for row in rows:
            symbol = row[symbol_idx]
            if symbol in trend_results:
                slope_1y, slope_6m, slope_3m = trend_results[symbol]
                row[idx_1y] = str(slope_1y) if slope_1y is not None else 'N/A'
                row[idx_6m] = str(slope_6m) if slope_6m is not None else 'N/A'
                row[idx_3m] = str(slope_3m) if slope_3m is not None else 'N/A'

        # Write updated GTT file
        with open(gtt_path, 'w', newline='') as f:
            writer = csv.writer(f, delimiter='\t')
            writer.writerow(header)
            writer.writerows(rows)

        # Save cache
        cache.save()

        logger.info(f"Successfully enriched {gtt_path} with trend data")
        return True

    except Exception as e:
        logger.error(f"Error enriching GTT file: {e}", exc_info=True)
        return False


# =====================
# Main Entry Point
# =====================
def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description='Trend Verifier - Enrich GusTradeTable with trend slope analysis',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python3 Trend_Verifier.py --input ./runs/21-Nov-2025/GusTradeTable.txt --client-id 600

  # With custom cache location
  python3 Trend_Verifier.py --input ./runs/21-Nov-2025/GusTradeTable.txt --client-id 600 \\
    --cache ./custom_cache/trends.csv

  # Paper trading account
  python3 Trend_Verifier.py --input ./runs/21-Nov-2025/GusTradeTable.txt --client-id 600 \\
    --port 4002
        """
    )

    # Required arguments
    parser.add_argument('--input', required=True, help='Path to GusTradeTable file')
    parser.add_argument('--client-id', type=int, required=True, help='IB Gateway client ID')

    # Optional arguments
    parser.add_argument('--cache', default='./scripts_functions/trend_verifier/cache/trend_slopes_cache.csv',
                       help='Path to trend cache file (default: ./scripts_functions/trend_verifier/cache/trend_slopes_cache.csv)')
    parser.add_argument('--cache-ttl', type=int, default=86400,
                       help='Cache TTL in seconds (default: 86400 = 24 hours)')
    parser.add_argument('--host', default='127.0.0.1', help='IB Gateway host (default: 127.0.0.1)')
    parser.add_argument('--port', type=int, default=4001, help='IB Gateway port (default: 4001)')
    parser.add_argument('--log', help='Log file path (default: auto-generated)')
    parser.add_argument('--verbose', action='store_true', help='Enable verbose logging')

    args = parser.parse_args()

    # Setup logging
    log_level = logging.DEBUG if args.verbose else logging.INFO

    if args.log:
        log_file = args.log
    else:
        log_dir = Path(__file__).parent / 'logs'
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / f"trend_verifier_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler()
        ]
    )

    logger = logging.getLogger(__name__)

    logger.info("=" * 60)
    logger.info("Trend Verifier (TVR) - GUS3")
    logger.info("=" * 60)
    logger.info(f"Input GTT: {args.input}")
    logger.info(f"Cache: {args.cache} (TTL: {args.cache_ttl}s)")
    logger.info(f"IB Gateway: {args.host}:{args.port} (Client ID: {args.client_id})")
    logger.info("")

    # Validate input file
    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        sys.exit(1)

    # Initialize cache
    cache = TrendCache(args.cache, args.cache_ttl)
    logger.info(f"Loaded {len(cache.cache)} cached symbols")

    # Initialize IB API
    wrapper = TrendWrapper()
    client = TrendClient(wrapper)

    try:
        # Connect to IB Gateway
        logger.info("Connecting to IB Gateway...")
        client.connect(args.host, args.port, args.client_id)

        # Start API thread
        api_thread = threading.Thread(target=client.run, daemon=True)
        api_thread.start()

        # Wait for connection
        if not app_state["connection_ready"].wait(timeout=15):
            logger.error("Connection timeout. Ensure IB Gateway is running.")
            sys.exit(1)

        logger.info("Connected successfully")
        logger.info("")

        # Enrich GTT file
        success = enrich_gtt_file(args.input, client, wrapper, cache, logger)

        if success:
            logger.info("")
            logger.info("=" * 60)
            logger.info("✓ Trend enrichment completed successfully!")
            logger.info("=" * 60)
        else:
            logger.error("Trend enrichment failed")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

    finally:
        # Disconnect
        if client.isConnected():
            logger.info("Disconnecting from IB Gateway...")
            client.disconnect()

        if api_thread and api_thread.is_alive():
            api_thread.join(timeout=5)

    logger.info("Script terminated.")


if __name__ == "__main__":
    main()
