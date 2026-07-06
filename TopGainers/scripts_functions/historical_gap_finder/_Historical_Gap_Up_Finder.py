#!/usr/bin/env python3
"""
Historical Gap Up Finder (HGUF)
Scans IBKR historical database for gap-up events within a defined time period.

Author: GUS3 Trading System
Description: Identifies stocks that gapped up >= minimum percentage from previous day RTH close
             to next day pre-market end (09:29), then fetches detailed OHLC data for analysis.
"""

import argparse
import sys
import os
import logging
import threading
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
import pytz
from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
import yfinance as yf
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.table import Table
from rich.live import Live

# Global app state for thread-safe data management
app_state = {
    "connection_ready": threading.Event(),
    "connection_lost": threading.Event(),
    "shutdown": threading.Event(),
    "historical_data": {},           # {reqId: [bars]}
    "daily_data": {},                # {symbol: [daily_bars]}
    "gap_events": [],                # [{gue_id, symbol, date_11, date_12, gap_pct, ...}]
    "intraday_data": {},             # {gue_id: [bars with calculated metrics]}
    "pending_requests": set(),       # Active request IDs
    "request_map": {},               # {reqId: (symbol, request_type, metadata)}
    "yfinance_cache": {},            # {symbol: {float, inst_pct, timestamp}}
    "next_req_id": 1,                # Auto-increment request ID
    "next_gue_id": 1,                # Auto-increment GUE ID
}

# Timezone
EASTERN_TZ = pytz.timezone('US/Eastern')

# Console for Rich output
console = Console()

# Logger
logger = None


# ============================================================================
# IBKR API Wrapper and Client Classes
# ============================================================================

class HGUFWrapper(EWrapper):
    """Handles IBKR API callbacks"""

    def __init__(self):
        EWrapper.__init__(self)
        self.next_order_id = None
        self.pending_requests_lock = threading.Lock()
        self.historical_data_lock = threading.Lock()

    def cleanup_timeout_request(self, req_id, symbol, request_type):
        """
        Clean up a timed-out historical data request.
        Thread-safe cleanup using pending_requests_lock.

        Args:
            req_id: Request ID that timed out
            symbol: Stock symbol (for logging)
            request_type: Type of request ('daily', 'premarket', 'intraday')
        """
        logger.debug(f"{symbol}: Cleaning up timed-out {request_type} request {req_id}")

        with self.pending_requests_lock:
            app_state["pending_requests"].discard(req_id)
            logger.debug(f"  Removed req_id {req_id} from pending_requests (size: {len(app_state['pending_requests'])})")

        with self.historical_data_lock:
            if req_id in app_state["historical_data"]:
                del app_state["historical_data"][req_id]
                logger.debug(f"  Removed req_id {req_id} from historical_data")

        if req_id in app_state["request_map"]:
            del app_state["request_map"][req_id]
            logger.debug(f"  Removed req_id {req_id} from request_map")

    def nextValidId(self, orderId: int):
        """Connection ready signal"""
        super().nextValidId(orderId)
        self.next_order_id = orderId
        logger.info(f"Connection established. Next valid order ID: {orderId}")
        app_state["connection_ready"].set()

    def error(self, reqId: int, errorCode: int, errorString: str):
        """Handle connection/request errors"""
        # Informational messages (not errors)
        informational_codes = {2104, 2106, 2158, 2107, 2119, 2176, 162}

        if errorCode in informational_codes:
            if errorCode == 162 and "pacing violation" in errorString.lower():
                logger.warning(f"IBKR pacing violation detected (reqId {reqId}): {errorString}")
            else:
                logger.info(f"Info {errorCode}: {errorString}")
            return

        # Fatal connection errors
        fatal_codes = {504, 1100, 1101, 1102, 1300}
        if errorCode in fatal_codes:
            logger.error(f"FATAL ERROR {errorCode}: {errorString}")
            app_state["connection_lost"].set()
            app_state["shutdown"].set()
            return

        # Historical data errors (non-fatal, just log)
        if errorCode == 162:  # No data available
            logger.warning(f"ReqId {reqId}: No historical data available - {errorString}")
            with self.pending_requests_lock:
                if reqId in app_state["pending_requests"]:
                    app_state["pending_requests"].remove(reqId)
            return

        # General errors
        logger.error(f"Error {errorCode} (ReqId {reqId}): {errorString}")

    def connectionClosed(self):
        """Detects unexpected disconnections"""
        super().connectionClosed()
        logger.warning("IB connection closed unexpectedly")
        app_state["connection_lost"].set()

    def historicalData(self, reqId: int, bar):
        """Callback for historical data bars"""
        if not bar or not bar.date:
            return

        # Parse bar date (handle both formats: "20251125 09:30:00" or "20251125")
        try:
            if len(bar.date) == 8:  # Daily bar format: "20251125"
                dt_obj = datetime.strptime(bar.date, '%Y%m%d')
            else:  # Intraday bar format: "20251125 09:30:00" or "20251125  09:30:00"
                dt_obj = datetime.strptime(bar.date.strip(), '%Y%m%d %H:%M:%S')

            # Make timezone-aware (US/Eastern)
            dt_obj = EASTERN_TZ.localize(dt_obj)
        except ValueError as e:
            logger.warning(f"ReqId {reqId}: Failed to parse bar date '{bar.date}': {e}")
            return

        bar_data = {
            'date': dt_obj,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        }

        with self.historical_data_lock:
            if reqId not in app_state["historical_data"]:
                app_state["historical_data"][reqId] = []
            app_state["historical_data"][reqId].append(bar_data)

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback when historical data request is complete"""
        logger.debug(f"ReqId {reqId}: Historical data request complete (start={start}, end={end})")
        with self.pending_requests_lock:
            if reqId in app_state["pending_requests"]:
                app_state["pending_requests"].remove(reqId)


class HGUFClient(EClient):
    """IBKR API client"""

    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)


class HGUFApp(HGUFWrapper, HGUFClient):
    """Combined IBKR API application"""

    def __init__(self):
        HGUFWrapper.__init__(self)
        HGUFClient.__init__(self, wrapper=self)


# ============================================================================
# Utility Functions
# ============================================================================

def setup_logging(log_file=None, verbose=False):
    """Set up logging infrastructure"""
    global logger

    # Create logger
    logger = logging.getLogger('HGUF')
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.handlers = []  # Clear existing handlers

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # File handler
    if log_file is None:
        log_dir = os.path.join(os.path.dirname(__file__), '../../logs')
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"hguf_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    logger.info(f"Logging to: {log_file}")

    # Log the command line for reproducibility
    command_line = ' '.join(sys.argv)
    logger.info(f"Command: {command_line}")

    return log_file


def load_stock_universe(universe_file):
    """Load stock symbols from universe file"""
    if not os.path.exists(universe_file):
        logger.error(f"Universe file not found: {universe_file}")
        sys.exit(1)

    symbols = []
    with open(universe_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                symbols.append(line.upper())

    logger.info(f"Loaded {len(symbols)} symbols from universe file: {universe_file}")
    return symbols


def map_candle_size_to_ibkr_bar_size(candle_minutes):
    """Map candle size in minutes to IBKR barSizeSetting"""
    mapping = {
        1: '1 min',
        2: '2 mins',
        3: '3 mins',
        5: '5 mins',
        10: '10 mins',
        15: '15 mins',
        20: '20 mins',
        30: '30 mins',
        60: '1 hour',
    }

    if candle_minutes in mapping:
        return mapping[candle_minutes]
    else:
        logger.error(f"Invalid candle size: {candle_minutes} minutes")
        logger.error(f"Valid sizes: {list(mapping.keys())}")
        sys.exit(1)


def create_contract(symbol):
    """Create IBKR contract for US stock"""
    contract = Contract()
    contract.symbol = symbol
    contract.secType = 'STK'
    contract.exchange = 'SMART'
    contract.currency = 'USD'
    contract.primaryExchange = ''  # Let IBKR determine
    return contract


def detect_otc(contract, yfinance_data=None):
    """
    Detect OTC stocks using multi-layer detection.
    Reuses logic from Gainers_Scanner_Script.py
    """
    standard_otc_exchanges = {'PINK', 'OTCBB', 'OTCMKTS', 'OTC', 'OTCQX', 'OTCQB'}
    ibkr_otc_exchanges = {'EXPERT', 'SCM', 'VENTURE', 'GREY'}
    all_otc_exchanges = standard_otc_exchanges | ibkr_otc_exchanges

    # Layer 1: IBKR exchange codes
    if contract.exchange and contract.exchange.upper() in all_otc_exchanges:
        return True

    if contract.primaryExchange and contract.primaryExchange.upper() in all_otc_exchanges:
        return True

    if hasattr(contract, 'tradingClass') and contract.tradingClass and contract.tradingClass.upper() in all_otc_exchanges:
        return True

    # Layer 2: yfinance exchange data
    if yfinance_data and 'exchange' in yfinance_data:
        yfinance_otc_markers = {'OTCMKTS', 'PINK', 'PNK', 'OTC', 'OTCBB', 'OTCQB', 'OTCQX', 'OQX', 'OQB'}
        yf_exchange = yfinance_data['exchange'].upper()
        if yf_exchange in yfinance_otc_markers:
            return True

    return False


def get_yfinance_fundamentals(symbol):
    """
    Fetch Float and Institution% from yfinance with 24-hour caching.
    Returns: dict with 'float_millions' and 'institution_pct' keys
    """
    cache_ttl = 86400  # 24 hours
    current_time = time.time()

    # Check cache
    if symbol in app_state["yfinance_cache"]:
        cache_entry = app_state["yfinance_cache"][symbol]
        if current_time - cache_entry['timestamp'] < cache_ttl:
            logger.debug(f"{symbol}: Using cached yfinance data")
            return cache_entry['data']

    # Fetch from yfinance
    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info

        # Extract data
        float_shares = info.get('floatShares', None)
        inst_pct = info.get('heldPercentInstitutions', None)
        exchange = info.get('exchange', None)

        # Convert float to millions
        float_millions = float_shares / 1e6 if float_shares else None
        institution_pct = inst_pct * 100 if inst_pct is not None else None

        result = {
            'float_millions': float_millions,
            'institution_pct': institution_pct,
            'exchange': exchange
        }

        # Cache result
        app_state["yfinance_cache"][symbol] = {
            'data': result,
            'timestamp': current_time
        }

        logger.debug(f"{symbol}: Fetched yfinance data - Float: {float_millions:.2f}M, Inst: {institution_pct:.2f}%"
                    if float_millions and institution_pct else f"{symbol}: yfinance data incomplete")

        return result

    except Exception as e:
        logger.warning(f"{symbol}: Failed to fetch yfinance data: {e}")
        return {'float_millions': None, 'institution_pct': None, 'exchange': None}


def parse_date_dd_mm_yyyy(date_str):
    """Parse date in DD-MM-YYYY format to datetime object (US/Eastern timezone)"""
    try:
        dt = datetime.strptime(date_str, '%d-%m-%Y')
        return EASTERN_TZ.localize(dt)
    except ValueError as e:
        logger.error(f"Failed to parse date '{date_str}': {e}")
        logger.error("Expected format: DD-MM-YYYY (e.g., 25-11-2025)")
        sys.exit(1)


def format_datetime_for_ibkr(dt):
    """Format datetime for IBKR API (YYYYMMDD HH:MM:SS US/Eastern)"""
    return dt.strftime('%Y%m%d %H:%M:%S') + ' US/Eastern'


def format_datetime_for_output(dt):
    """Format datetime for HGUF-table output (YYYY-MM-DD HH:MM:SS)"""
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# ============================================================================
# Gap Detection Functions
# ============================================================================

def fetch_daily_bars(app, symbol, start_date, end_date):
    """
    Fetch daily bars for a symbol covering the time window.
    Returns list of daily bars or None if failed.
    """
    contract = create_contract(symbol)

    # Calculate duration (days from start to end + 1 day before)
    days = (end_date - start_date).days + 2  # +1 for day-before, +1 for inclusive end
    duration_str = f"{days} D"

    # Generate request ID
    req_id = app_state["next_req_id"]
    app_state["next_req_id"] += 1

    # Register request
    app_state["request_map"][req_id] = (symbol, 'daily', None)
    app_state["pending_requests"].add(req_id)
    app_state["historical_data"][req_id] = []

    # Request daily bars
    end_datetime_str = format_datetime_for_ibkr(end_date.replace(hour=16, minute=0, second=0))

    logger.debug(f"{symbol}: Requesting daily bars (endDateTime={end_datetime_str}, duration={duration_str})")

    app.reqHistoricalData(
        reqId=req_id,
        contract=contract,
        endDateTime=end_datetime_str,
        durationStr=duration_str,
        barSizeSetting='1 day',
        whatToShow='TRADES',
        useRTH=1,  # RTH only for daily close prices
        formatDate=1,
        keepUpToDate=False,
        chartOptions=[]
    )

    # Wait for completion
    timeout = 0
    while req_id in app_state["pending_requests"] and timeout < 100:
        time.sleep(0.1)
        timeout += 1

    if timeout >= 100:
        logger.warning(f"{symbol}: Daily bars request timed out (req_id={req_id}, waited 10s)")
        app.cleanup_timeout_request(req_id, symbol, 'daily')
        return None

    bars = app_state["historical_data"].get(req_id, [])
    logger.debug(f"{symbol}: Received {len(bars)} daily bars")

    return bars


def fetch_pre_market_bars(app, symbol, date):
    """
    Fetch pre-market bars for a specific date (04:00-09:29).
    Returns list of pre-market bars or None if failed.
    """
    contract = create_contract(symbol)

    # Pre-market end time: 09:29:00
    pre_market_end = date.replace(hour=9, minute=29, second=0)

    # Request 6 hours of 1-minute bars (covers 04:00-09:29)
    duration_str = '21600 S'  # 6 hours in seconds

    # Generate request ID
    req_id = app_state["next_req_id"]
    app_state["next_req_id"] += 1

    # Register request
    app_state["request_map"][req_id] = (symbol, 'premarket', date)
    app_state["pending_requests"].add(req_id)
    app_state["historical_data"][req_id] = []

    # Request pre-market bars
    end_datetime_str = format_datetime_for_ibkr(pre_market_end)

    logger.debug(f"{symbol}: Requesting pre-market bars for {date.strftime('%Y-%m-%d')} (endDateTime={end_datetime_str})")

    app.reqHistoricalData(
        reqId=req_id,
        contract=contract,
        endDateTime=end_datetime_str,
        durationStr=duration_str,
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=0,  # Include extended hours
        formatDate=1,
        keepUpToDate=False,
        chartOptions=[]
    )

    # Wait for completion
    timeout = 0
    while req_id in app_state["pending_requests"] and timeout < 100:
        time.sleep(0.1)
        timeout += 1

    if timeout >= 100:
        logger.warning(f"{symbol}: Pre-market bars request timed out (req_id={req_id}, waited 10s)")
        app.cleanup_timeout_request(req_id, symbol, 'premarket')
        return None

    bars = app_state["historical_data"].get(req_id, [])
    logger.debug(f"{symbol}: Received {len(bars)} pre-market bars")

    return bars


def _fetch_single_intraday_request(app, symbol, start_datetime, end_datetime, candle_size_minutes):
    """
    Fetch intraday bars for a single request (duration must be <= 24 hours).
    This is the core request logic extracted from fetch_intraday_bars.
    Returns list of intraday bars or None if failed.
    """
    contract = create_contract(symbol)

    # Calculate duration in seconds
    duration_seconds = int((end_datetime - start_datetime).total_seconds())

    # Map candle size to IBKR bar size
    bar_size = map_candle_size_to_ibkr_bar_size(candle_size_minutes)

    # Generate request ID
    req_id = app_state["next_req_id"]
    app_state["next_req_id"] += 1

    # Register request
    app_state["request_map"][req_id] = (symbol, 'intraday', (start_datetime, end_datetime))
    app_state["pending_requests"].add(req_id)
    app_state["historical_data"][req_id] = []

    # Request intraday bars
    end_datetime_str = format_datetime_for_ibkr(end_datetime)
    duration_str = f"{duration_seconds} S"

    logger.debug(f"{symbol}: Requesting intraday bars (endDateTime={end_datetime_str}, duration={duration_str}, barSize={bar_size})")

    app.reqHistoricalData(
        reqId=req_id,
        contract=contract,
        endDateTime=end_datetime_str,
        durationStr=duration_str,
        barSizeSetting=bar_size,
        whatToShow='TRADES',
        useRTH=0,  # Include extended hours
        formatDate=1,
        keepUpToDate=False,
        chartOptions=[]
    )

    # Wait for completion
    timeout = 0
    while req_id in app_state["pending_requests"] and timeout < 200:  # Longer timeout for intraday
        time.sleep(0.1)
        timeout += 1

    if timeout >= 200:
        logger.warning(f"{symbol}: Intraday bars request timed out (req_id={req_id}, waited 20s)")
        app.cleanup_timeout_request(req_id, symbol, 'intraday')
        return None

    bars = app_state["historical_data"].get(req_id, [])
    logger.debug(f"{symbol}: Received {len(bars)} intraday bars")

    return bars


def _fetch_intraday_bars_split(app, symbol, start_datetime, end_datetime, candle_size_minutes):
    """
    Split large time range into multiple requests to avoid IBKR's 24-hour limit.
    For requests spanning day-11 09:30 to day-12 16:00:
      - Request 1: day-11 09:30 to day-11 23:59:59 (RTH + ETH)
      - Request 2: day-12 00:00:00 to day-12 16:00 (ETH + RTH)
    Returns combined list of intraday bars or None if failed.
    """
    all_bars = []

    # Request 1: Start to end of first day
    day_11_end = start_datetime.replace(hour=23, minute=59, second=59)
    logger.debug(f"{symbol}: Splitting request - Part 1: {start_datetime.strftime('%Y-%m-%d %H:%M')} to {day_11_end.strftime('%Y-%m-%d %H:%M')}")

    bars_1 = _fetch_single_intraday_request(app, symbol, start_datetime, day_11_end, candle_size_minutes)
    if bars_1:
        all_bars.extend(bars_1)
        logger.debug(f"{symbol}: Part 1 returned {len(bars_1)} bars")

    # Request 2: Start of next day to end
    day_12_start = end_datetime.replace(hour=0, minute=0, second=0)
    logger.debug(f"{symbol}: Splitting request - Part 2: {day_12_start.strftime('%Y-%m-%d %H:%M')} to {end_datetime.strftime('%Y-%m-%d %H:%M')}")

    bars_2 = _fetch_single_intraday_request(app, symbol, day_12_start, end_datetime, candle_size_minutes)
    if bars_2:
        all_bars.extend(bars_2)
        logger.debug(f"{symbol}: Part 2 returned {len(bars_2)} bars")

    logger.debug(f"{symbol}: Split request complete - Total {len(all_bars)} bars combined")

    return all_bars if all_bars else None


def fetch_intraday_bars(app, symbol, start_datetime, end_datetime, candle_size_minutes):
    """
    Fetch intraday bars from start_datetime to end_datetime with specified candle size.
    Automatically splits requests exceeding 24 hours to comply with IBKR API limits.
    Returns list of intraday bars or None if failed.
    """
    duration_seconds = int((end_datetime - start_datetime).total_seconds())

    # IBKR limit: 86400 seconds (24 hours) per request
    if duration_seconds <= 86400:
        # Single request (original logic)
        return _fetch_single_intraday_request(app, symbol, start_datetime, end_datetime, candle_size_minutes)
    else:
        # Split into multiple requests
        logger.debug(f"{symbol}: Duration {duration_seconds}s exceeds 24h limit, splitting request")
        return _fetch_intraday_bars_split(app, symbol, start_datetime, end_datetime, candle_size_minutes)


def detect_gap_event_for_symbol(app, symbol, daily_bars, start_date, end_date, min_gap_pct, max_open_price):
    """
    Detect gap events for a single symbol across the time window.
    Returns list of gap event dicts or empty list.
    """
    gap_events = []

    # Need at least 2 consecutive days to detect gaps
    if len(daily_bars) < 2:
        return gap_events

    # Iterate through consecutive day pairs within time window
    for i in range(len(daily_bars) - 1):
        day_11_bar = daily_bars[i]
        day_12_bar = daily_bars[i + 1]

        day_11_date = day_11_bar['date']
        day_12_date = day_12_bar['date']

        # Check if day_12 is within time window
        if day_12_date < start_date or day_12_date > end_date:
            continue

        day_11_close = day_11_bar['close']

        # Fetch pre-market bars for day-12
        pre_market_bars = fetch_pre_market_bars(app, symbol, day_12_date)

        if not pre_market_bars or len(pre_market_bars) == 0:
            logger.debug(f"{symbol}: No pre-market data for {day_12_date.strftime('%Y-%m-%d')}")
            continue

        # Find highest price at/before 09:29
        pre_market_high = max([bar['high'] for bar in pre_market_bars])

        # Calculate gap percentage
        gap_pct = ((pre_market_high - day_11_close) / day_11_close) * 100

        if gap_pct >= min_gap_pct:
            logger.info(f"{symbol}: Gap detected on {day_12_date.strftime('%Y-%m-%d')} - {gap_pct:.2f}%")

            # Fetch day-12 RTH open bar to check max-open-price filter
            # RTH open is at 09:30
            day_12_open_time = day_12_date.replace(hour=9, minute=30, second=0)

            # Request 1-minute bar at 09:30
            rth_open_bars = fetch_intraday_bars(app, symbol, day_12_open_time, day_12_open_time + timedelta(minutes=1), 1)

            if not rth_open_bars or len(rth_open_bars) == 0:
                logger.warning(f"{symbol}: No RTH open bar for {day_12_date.strftime('%Y-%m-%d')}, skipping")
                continue

            day_12_open_price = rth_open_bars[0]['open']

            # Apply max-open-price filter
            if max_open_price is not None and day_12_open_price > max_open_price:
                logger.debug(f"{symbol}: Filtered out by max-open-price (open=${day_12_open_price:.2f} > ${max_open_price:.2f})")
                continue

            # Create GUE record
            gue_id = app_state["next_gue_id"]
            app_state["next_gue_id"] += 1

            gap_event = {
                'gue_id': gue_id,
                'symbol': symbol,
                'date_11': day_11_date,
                'date_12': day_12_date,
                'day_11_close': day_11_close,
                'day_12_open': day_12_open_price,
                'pre_market_high': pre_market_high,
                'gap_pct': gap_pct
            }

            gap_events.append(gap_event)

        # Add throttling to avoid pacing violations
        time.sleep(0.05)  # 20 requests/sec conservative

    return gap_events


def calculate_metrics_for_gue(gue, intraday_bars):
    """
    Calculate GAP%, Push%, MAX for each bar in a GUE.
    Returns list of enriched bar dicts with calculated metrics.
    """
    day_11_close = gue['day_11_close']
    day_12_open = gue['day_12_open']
    day_12_date = gue['date_12']

    # Define time ranges
    premarket_start = day_12_date.replace(hour=4, minute=0, second=0)
    premarket_end = day_12_date.replace(hour=9, minute=29, second=59)
    rth_start = day_12_date.replace(hour=9, minute=30, second=0)
    rth_end = day_12_date.replace(hour=16, minute=0, second=0)

    enriched_bars = []
    push_percentages = []  # Track all Push% values to find max

    for bar in intraday_bars:
        bar_time = bar['date']
        bar_high = bar['high']
        bar_close = bar['close']

        enriched_bar = bar.copy()

        # Calculate GAP% (only during day-12 pre-market)
        if premarket_start <= bar_time <= premarket_end:
            gap_pct = ((bar_high - day_11_close) / day_11_close) * 100
            enriched_bar['gap_pct'] = gap_pct
        else:
            enriched_bar['gap_pct'] = None

        # Calculate Push% (only during day-12 RTH)
        if rth_start <= bar_time <= rth_end:
            if day_12_open > 0:
                push_pct = ((bar_high - day_12_open) / day_12_open) * 100
                enriched_bar['push_pct'] = push_pct
                push_percentages.append((len(enriched_bars), push_pct))
            else:
                enriched_bar['push_pct'] = None
        else:
            enriched_bar['push_pct'] = None

        enriched_bar['max_marker'] = None  # Will be set later

        enriched_bars.append(enriched_bar)

    # Find index with highest Push%
    if push_percentages:
        max_index, max_push = max(push_percentages, key=lambda x: x[1])
        enriched_bars[max_index]['max_marker'] = 'max'

    return enriched_bars


def write_hguf_table(gap_events_with_data, output_file):
    """
    Write HGUF-table to tab-delimited file.
    gap_events_with_data: list of dicts with keys: gue, enriched_bars, fundamentals
    """
    header = ['Symbol', 'GUE', 'Time', 'O', 'H', 'L', 'C', 'Volume', 'GAP%', 'Push%', 'MAX', 'Float', 'Institution%']

    with open(output_file, 'w') as f:
        # Write header
        f.write('\t'.join(header) + '\n')

        # Write data rows (sorted by GUE ID)
        sorted_events = sorted(gap_events_with_data, key=lambda x: x['gue']['gue_id'])

        for event in sorted_events:
            gue = event['gue']
            enriched_bars = event['enriched_bars']
            fundamentals = event['fundamentals']

            symbol = gue['symbol']
            gue_id = gue['gue_id']
            float_millions = fundamentals.get('float_millions')
            institution_pct = fundamentals.get('institution_pct')

            for bar in enriched_bars:
                row = [
                    symbol,
                    str(gue_id),
                    format_datetime_for_output(bar['date']),
                    f"{bar['open']:.4f}",
                    f"{bar['high']:.4f}",
                    f"{bar['low']:.4f}",
                    f"{bar['close']:.4f}",
                    str(bar['volume']),
                    f"{bar['gap_pct']:.2f}" if bar['gap_pct'] is not None else 'NA',
                    f"{bar['push_pct']:.2f}" if bar['push_pct'] is not None else 'NA',
                    bar['max_marker'] if bar['max_marker'] else 'NA',
                    f"{float_millions:.2f}" if float_millions else 'NA',
                    f"{institution_pct:.2f}" if institution_pct else 'NA'
                ]
                f.write('\t'.join(row) + '\n')

    logger.info(f"Wrote HGUF-table to: {output_file}")


# ============================================================================
# Main HGUF Logic
# ============================================================================

def main():
    """Main entry point"""
    global logger

    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Historical Gap Up Finder (HGUF) - Scan IBKR historical database for gap-up events',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python3 Historical_Gap_Up_Finder.py --time-window "25-11-2025" "10-12-2025" --minimum-gap-percentage 30 --candles-size 10 --client-id 700

  # With filters
  python3 Historical_Gap_Up_Finder.py --time-window "01-11-2025" "30-11-2025" --minimum-gap-percentage 50 --max-open-price 20.0 --max-float 100.0 --max-institution 40.0 --candles-size 5 --client-id 700
        """
    )

    # Required arguments
    parser.add_argument('--time-window', nargs=2, required=True, metavar=('START', 'END'),
                       help='Time window in DD-MM-YYYY format (e.g., "25-11-2025" "10-12-2025")')
    parser.add_argument('--minimum-gap-percentage', type=float, required=True,
                       help='Minimum gap percentage threshold (e.g., 30 for 30%%)')
    parser.add_argument('--candles-size', type=int, required=True,
                       help='Candle size in minutes (valid: 1, 2, 3, 5, 10, 15, 20, 30, 60)')
    parser.add_argument('--client-id', type=int, required=True,
                       help='IBKR client ID (use unique ID, e.g., 700)')

    # Optional price/fundamental filters
    parser.add_argument('--max-open-price', type=float,
                       help='Maximum opening price filter (day-12 RTH open)')
    parser.add_argument('--max-float', type=float,
                       help='Maximum float in millions (e.g., 100 = 100M shares)')
    parser.add_argument('--min-float', type=float,
                       help='Minimum float in millions')
    parser.add_argument('--max-institution', type=float,
                       help='Maximum institutional ownership %% (0-100)')
    parser.add_argument('--min-institution', type=float,
                       help='Minimum institutional ownership %% (0-100)')

    # Other optional arguments
    parser.add_argument('--output-dir', default='.',
                       help='Output directory (default: current directory)')
    parser.add_argument('--universe-file',
                       default=os.path.join(os.path.dirname(__file__), 'stock_universe.txt'),
                       help='Stock universe file path (default: stock_universe.txt)')
    parser.add_argument('--log',
                       help='Log file path (default: auto-generated in ../../logs/)')
    parser.add_argument('--port', type=int, default=4001,
                       help='IB Gateway port (4001=live, 4002=paper, default: 4001)')
    parser.add_argument('--host', default='127.0.0.1',
                       help='IB Gateway host (default: 127.0.0.1)')
    parser.add_argument('--verbose', action='store_true',
                       help='Enable verbose logging')

    args = parser.parse_args()

    # Validate arguments
    if args.minimum_gap_percentage <= 0:
        parser.error("--minimum-gap-percentage must be > 0")

    if args.candles_size not in [1, 2, 3, 5, 10, 15, 20, 30, 60]:
        parser.error(f"--candles-size must be one of: 1, 2, 3, 5, 10, 15, 20, 30, 60 (got {args.candles_size})")

    if args.max_open_price is not None and args.max_open_price <= 0:
        parser.error("--max-open-price must be > 0")

    if args.min_float is not None and args.max_float is not None:
        if args.min_float >= args.max_float:
            parser.error("--min-float must be < --max-float")

    if args.min_institution is not None and args.max_institution is not None:
        if args.min_institution >= args.max_institution:
            parser.error("--min-institution must be < --max-institution")

    if args.min_institution is not None and (args.min_institution < 0 or args.min_institution > 100):
        parser.error("--min-institution must be 0-100")

    if args.max_institution is not None and (args.max_institution < 0 or args.max_institution > 100):
        parser.error("--max-institution must be 0-100")

    # Parse time window
    start_date = parse_date_dd_mm_yyyy(args.time_window[0])
    end_date = parse_date_dd_mm_yyyy(args.time_window[1])

    if start_date >= end_date:
        parser.error("Start date must be before end date")

    if end_date > datetime.now(EASTERN_TZ):
        parser.error("End date must be in the past")

    # Set up logging
    log_file = setup_logging(args.log, args.verbose)

    # Display configuration
    console.print("\n[bold cyan]═══════════════════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]   Historical Gap Up Finder (HGUF)[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════════════════[/bold cyan]\n")

    console.print(f"[bold]Configuration:[/bold]")
    console.print(f"  Time Window: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    console.print(f"  Minimum Gap: {args.minimum_gap_percentage}%")
    console.print(f"  Candle Size: {args.candles_size} minutes")
    console.print(f"  Client ID: {args.client_id}")
    console.print(f"  IB Gateway: {args.host}:{args.port}")
    console.print(f"  Universe File: {args.universe_file}")
    console.print(f"  Output Dir: {args.output_dir}")
    console.print(f"  Log File: {log_file}")

    if args.max_open_price:
        console.print(f"  Max Open Price: ${args.max_open_price:.2f}")
    if args.min_float or args.max_float:
        console.print(f"  Float Range: {args.min_float or 'None'}-{args.max_float or 'None'} M")
    if args.min_institution or args.max_institution:
        console.print(f"  Institution Range: {args.min_institution or 'None'}%-{args.max_institution or 'None'}%")

    console.print()

    # Load stock universe
    universe_symbols = load_stock_universe(args.universe_file)

    if len(universe_symbols) == 0:
        logger.error("No symbols found in universe file")
        sys.exit(1)

    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)

    # Connect to IBKR
    app = HGUFApp()
    app.connect(args.host, args.port, clientId=args.client_id)

    # Start API client thread
    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    # Wait for connection
    console.print("[yellow]Connecting to IB Gateway...[/yellow]")
    if not app_state["connection_ready"].wait(timeout=15):
        logger.error("Failed to connect to IB Gateway within 15 seconds")
        sys.exit(1)

    console.print("[green]✓ Connected to IB Gateway[/green]\n")

    # ========================================================================
    # PHASE 1: Fetch Daily Bars and Detect Gap Events
    # ========================================================================

    console.print("[bold cyan]Phase 1: Fetching daily bars and detecting gap events...[/bold cyan]\n")

    all_gap_events = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:

        task = progress.add_task(f"[cyan]Scanning {len(universe_symbols)} symbols...", total=len(universe_symbols))

        for symbol in universe_symbols:
            progress.update(task, description=f"[cyan]Scanning {symbol}...")

            # Fetch daily bars
            daily_bars = fetch_daily_bars(app, symbol, start_date, end_date)

            if not daily_bars:
                progress.advance(task)
                continue

            # Check for OTC (filter out)
            contract = create_contract(symbol)
            yf_data = get_yfinance_fundamentals(symbol)
            if detect_otc(contract, yf_data):
                logger.debug(f"{symbol}: OTC stock, skipping")
                progress.advance(task)
                continue

            # Detect gap events for this symbol
            gap_events = detect_gap_event_for_symbol(
                app, symbol, daily_bars, start_date, end_date,
                args.minimum_gap_percentage, args.max_open_price
            )

            if gap_events:
                all_gap_events.extend(gap_events)
                progress.update(task, description=f"[green]{symbol} - Found {len(gap_events)} gap(s)!")

            progress.advance(task)

            # Throttle to avoid pacing violations
            time.sleep(0.05)

    console.print(f"\n[bold green]✓ Phase 1 Complete: Found {len(all_gap_events)} gap events across {len(set([g['symbol'] for g in all_gap_events]))} symbols[/bold green]\n")

    if len(all_gap_events) == 0:
        console.print("[yellow]No gap events found matching criteria. Exiting.[/yellow]")
        app.disconnect()
        return

    # ========================================================================
    # PHASE 2: Enrich with Fundamentals and Apply Filters
    # ========================================================================

    console.print("[bold cyan]Phase 2: Enriching with fundamentals and applying filters...[/bold cyan]\n")

    filtered_gap_events = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        console=console
    ) as progress:

        task = progress.add_task("[cyan]Fetching fundamentals...", total=len(all_gap_events))

        for gue in all_gap_events:
            symbol = gue['symbol']
            progress.update(task, description=f"[cyan]Enriching {symbol}...")

            # Fetch fundamentals
            fundamentals = get_yfinance_fundamentals(symbol)

            float_millions = fundamentals.get('float_millions')
            institution_pct = fundamentals.get('institution_pct')

            # Apply float filters
            if args.min_float is not None and (float_millions is None or float_millions < args.min_float):
                logger.debug(f"{symbol} (GUE {gue['gue_id']}): Filtered by min-float ({float_millions} < {args.min_float})")
                progress.advance(task)
                continue

            if args.max_float is not None and (float_millions is None or float_millions > args.max_float):
                logger.debug(f"{symbol} (GUE {gue['gue_id']}): Filtered by max-float ({float_millions} > {args.max_float})")
                progress.advance(task)
                continue

            # Apply institution filters
            if args.min_institution is not None and (institution_pct is None or institution_pct < args.min_institution):
                logger.debug(f"{symbol} (GUE {gue['gue_id']}): Filtered by min-institution ({institution_pct} < {args.min_institution})")
                progress.advance(task)
                continue

            if args.max_institution is not None and (institution_pct is None or institution_pct > args.max_institution):
                logger.debug(f"{symbol} (GUE {gue['gue_id']}): Filtered by max-institution ({institution_pct} > {args.max_institution})")
                progress.advance(task)
                continue

            # Passed all filters
            gue['fundamentals'] = fundamentals
            filtered_gap_events.append(gue)

            progress.advance(task)

    console.print(f"\n[bold green]✓ Phase 2 Complete: {len(filtered_gap_events)} gap events passed fundamental filters[/bold green]\n")

    if len(filtered_gap_events) == 0:
        console.print("[yellow]No gap events passed fundamental filters. Exiting.[/yellow]")
        app.disconnect()
        return

    # ========================================================================
    # PHASE 3: Fetch Detailed Intraday OHLC Data
    # ========================================================================

    console.print("[bold cyan]Phase 3: Fetching detailed intraday OHLC data...[/bold cyan]\n")

    gap_events_with_data = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:

        task = progress.add_task("[cyan]Fetching intraday bars...", total=len(filtered_gap_events))

        for gue in filtered_gap_events:
            symbol = gue['symbol']
            date_11 = gue['date_11']
            date_12 = gue['date_12']

            progress.update(task, description=f"[cyan]Fetching {symbol} GUE {gue['gue_id']}...")

            # Define time range: day-11 09:30 to day-12 16:00
            start_time = date_11.replace(hour=9, minute=30, second=0)
            end_time = date_12.replace(hour=16, minute=0, second=0)

            # Fetch intraday bars
            intraday_bars = fetch_intraday_bars(app, symbol, start_time, end_time, args.candles_size)

            if not intraday_bars or len(intraday_bars) == 0:
                logger.warning(f"{symbol} (GUE {gue['gue_id']}): No intraday bars, skipping")
                progress.advance(task)
                continue

            # Calculate metrics (GAP%, Push%, MAX)
            enriched_bars = calculate_metrics_for_gue(gue, intraday_bars)

            gap_events_with_data.append({
                'gue': gue,
                'enriched_bars': enriched_bars,
                'fundamentals': gue['fundamentals']
            })

            progress.advance(task)

            # Throttle
            time.sleep(0.05)

    console.print(f"\n[bold green]✓ Phase 3 Complete: Fetched intraday data for {len(gap_events_with_data)} gap events[/bold green]\n")

    if len(gap_events_with_data) == 0:
        console.print("[yellow]No gap events have intraday data. Exiting.[/yellow]")
        app.disconnect()
        return

    # ========================================================================
    # PHASE 4: Write HGUF-table
    # ========================================================================

    console.print("[bold cyan]Phase 4: Writing HGUF-table...[/bold cyan]\n")

    # Generate output filename
    start_date_str = start_date.strftime('%d%b%Y')
    end_date_str = end_date.strftime('%d%b%Y')
    output_filename = f"HGUF-table_{start_date_str}_to_{end_date_str}.txt"
    output_path = os.path.join(args.output_dir, output_filename)

    # Write table
    write_hguf_table(gap_events_with_data, output_path)

    # Display summary
    total_rows = sum([len(event['enriched_bars']) for event in gap_events_with_data])
    unique_symbols = len(set([event['gue']['symbol'] for event in gap_events_with_data]))

    console.print(f"\n[bold green]✓ Phase 4 Complete[/bold green]\n")

    # Summary table
    summary_table = Table(title="HGUF Summary", show_header=True, header_style="bold cyan")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Value", style="green")

    summary_table.add_row("Total Gap Events", str(len(gap_events_with_data)))
    summary_table.add_row("Unique Symbols", str(unique_symbols))
    summary_table.add_row("Total OHLC Rows", str(total_rows))
    summary_table.add_row("Output File", output_path)

    console.print(summary_table)
    console.print()

    # Cleanup
    app.disconnect()
    console.print("[green]✓ Disconnected from IB Gateway[/green]")
    console.print("[bold green]✓ HGUF scan complete![/bold green]\n")


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted by user[/yellow]")
        sys.exit(0)
    except Exception as e:
        if logger:
            logger.exception(f"Unexpected error: {e}")
        else:
            print(f"FATAL ERROR: {e}")
        sys.exit(1)