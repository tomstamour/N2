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
import math
from datetime import datetime, timedelta
from itertools import groupby
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
    "request_events": {},            # {reqId: threading.Event()} for efficient completion tracking
    "yfinance_cache": {},            # {symbol: {float, inst_pct, timestamp}}
    "next_req_id": 1,                # Auto-increment request ID
    "next_gue_id": 1,                # Auto-increment GUE ID
    "next_req_id_lock": threading.Lock(),  # Lock for thread-safe request ID generation
    "cache_dir": "./cache/hguf_historical",  # Disk cache directory
    "use_cache": True,               # Enable/disable caching (controlled by --no-cache flag)
    "cache_stats": {"hits": 0, "misses": 0},  # Cache performance metrics
    "batch_stats": {                 # Batch processing statistics
        "current_batch_timeouts": 0,
        "global_timeouts": 0
    },
}

# Timezone
EASTERN_TZ = pytz.timezone('US/Eastern')

# Console for Rich output
console = Console()

# Logger
logger = None


# ============================================================================
# Market Days Calendar and Helper Functions
# ============================================================================

def observe_holiday(holiday_date):
    """
    Adjust holiday to observed date (Mon if Sat, Fri if Sun).

    Args:
        holiday_date: date object

    Returns:
        date - observed holiday date
    """
    from datetime import date
    if holiday_date.weekday() == 5:  # Saturday
        return holiday_date - timedelta(days=1)  # Observe on Friday
    elif holiday_date.weekday() == 6:  # Sunday
        return holiday_date + timedelta(days=1)  # Observe on Monday
    return holiday_date


def nth_weekday(year, month, weekday, n):
    """
    Find nth occurrence of weekday in month (e.g., 3rd Monday of January).

    Args:
        year: int
        month: int (1-12)
        weekday: int (0=Monday, 6=Sunday)
        n: int - nth occurrence

    Returns:
        date - nth occurrence of weekday in month
    """
    from datetime import date
    first_day = date(year, month, 1)
    first_weekday = first_day.weekday()
    offset = (weekday - first_weekday) % 7
    first_occurrence = first_day + timedelta(days=offset)
    return first_occurrence + timedelta(weeks=n-1)


def last_weekday(year, month, weekday):
    """
    Find last occurrence of weekday in month (e.g., last Monday of May).

    Args:
        year: int
        month: int (1-12)
        weekday: int (0=Monday, 6=Sunday)

    Returns:
        date - last occurrence of weekday in month
    """
    from datetime import date
    # Find first day of next month, go back to last day of target month
    if month == 12:
        last_day = date(year, 12, 31)
    else:
        last_day = date(year, month + 1, 1) - timedelta(days=1)

    # Find last occurrence of target weekday
    offset = (last_day.weekday() - weekday) % 7
    return last_day - timedelta(days=offset)


def calculate_good_friday(year):
    """
    Calculate Good Friday (Friday before Easter Sunday).
    Uses Meeus's Julian algorithm for Easter calculation.

    Args:
        year: int

    Returns:
        date - Good Friday date
    """
    from datetime import date
    # Easter calculation using Meeus's Julian algorithm
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    easter_sunday = date(year, month, day)

    # Good Friday is 2 days before Easter Sunday
    return easter_sunday - timedelta(days=2)


def get_us_market_holidays(year):
    """
    Return set of US market holiday dates for given year.

    Includes: New Year's Day, MLK Day, Presidents Day, Good Friday,
    Memorial Day, Independence Day, Labor Day, Thanksgiving, Christmas.

    Args:
        year: int

    Returns:
        set of date objects representing market holidays
    """
    from datetime import date
    holidays = set()

    # New Year's Day (Jan 1, or observed Mon if falls on weekend)
    new_years = date(year, 1, 1)
    holidays.add(observe_holiday(new_years))

    # MLK Day (3rd Monday of January)
    holidays.add(nth_weekday(year, 1, 0, 3))  # 0 = Monday, 3rd occurrence

    # Presidents Day (3rd Monday of February)
    holidays.add(nth_weekday(year, 2, 0, 3))

    # Good Friday (Friday before Easter - requires calculation)
    holidays.add(calculate_good_friday(year))

    # Memorial Day (last Monday of May)
    holidays.add(last_weekday(year, 5, 0))

    # Independence Day (July 4, or observed Mon/Fri if weekend)
    july4 = date(year, 7, 4)
    holidays.add(observe_holiday(july4))

    # Labor Day (1st Monday of September)
    holidays.add(nth_weekday(year, 9, 0, 1))

    # Thanksgiving (4th Thursday of November)
    holidays.add(nth_weekday(year, 11, 3, 4))  # 3 = Thursday, 4th occurrence

    # Christmas (Dec 25, or observed Mon/Fri if weekend)
    christmas = date(year, 12, 25)
    holidays.add(observe_holiday(christmas))

    return holidays


def is_market_day(check_date):
    """
    Check if given date is a market day (not weekend or holiday).

    Args:
        check_date: date object

    Returns:
        bool - True if market day, False if weekend/holiday
    """
    # Check weekend
    if check_date.weekday() >= 5:  # Saturday (5) or Sunday (6)
        return False

    # Check holidays
    year = check_date.year
    holidays = get_us_market_holidays(year)
    if check_date in holidays:
        return False

    return True


def get_next_market_day(from_date):
    """
    Get next market day after from_date (not including from_date itself).

    Args:
        from_date: date object

    Returns:
        date - next trading day
    """
    next_date = from_date + timedelta(days=1)
    while not is_market_day(next_date):
        next_date += timedelta(days=1)
    return next_date


def get_previous_market_day(from_date):
    """
    Get previous market day before from_date (not including from_date itself).

    Args:
        from_date: date object

    Returns:
        date - previous trading day
    """
    prev_date = from_date - timedelta(days=1)
    while not is_market_day(prev_date):
        prev_date -= timedelta(days=1)
    return prev_date


def count_market_days_between(from_date, to_date):
    """
    Count market days between two dates.

    Returns the signed number of market days to get from from_date to to_date.
    Skips weekends and US market holidays.

    Args:
        from_date: date object (reference date)
        to_date: date object (target date)

    Returns:
        int: Signed market day offset
        - 0 if same date
        - Positive if to_date is after from_date
        - Negative if to_date is before from_date

    Example:
        Dec 12 (Fri) → Dec 12 (Fri): 0 (same day)
        Dec 12 (Fri) → Dec 15 (Mon): 1 (next market day, skip weekend)
        Dec 12 (Fri) → Dec 16 (Tue): 2 (two market days forward)
        Dec 15 (Mon) → Dec 12 (Fri): -1 (previous market day)
    """
    if from_date == to_date:
        return 0

    if to_date > from_date:
        # Count forward
        count = 0
        current = from_date
        while current < to_date:
            current = get_next_market_day(current)
            count += 1
            if current == to_date:
                break
        return count
    else:
        # Count backward
        count = 0
        current = from_date
        while current > to_date:
            current = get_previous_market_day(current)
            count += 1
            if current == to_date:
                break
        return -count


def calculate_market_days_offset(gap_time, hours, direction='forward'):
    """
    Calculate datetime offset by market days from gap_time.

    This function converts hours to market days (hours / 24) and counts
    that many market days forward or backward from gap_time, skipping
    weekends and holidays.

    Args:
        gap_time: datetime - the gap time (Checkpoint-B at 09:29)
        hours: float - hours to offset (interpreted as hours/24 = market days)
        direction: str - 'forward' for post-gap, 'backward' for pre-gap

    Returns:
        datetime - offset time after/before adding market days

    Example:
        gap_time = 2025-12-12 09:29 (Thursday)
        hours = 72.0
        market_days = 72 / 24 = 3.0

        Market days: Dec 12 (Thu), Dec 13 (Fri), Dec 16 (Mon) - skip weekend

        For 3.0 market days forward:
        - Start: Dec 12 09:29
        - Count 3 market days: Dec 12, Dec 13, Dec 16
        - End: Dec 16 20:00 (end of 3rd market day)
    """
    # Convert hours to market days (calendar-based: hours / 24)
    market_days_total = hours / 24.0
    whole_market_days = int(market_days_total)
    fractional_market_day = market_days_total - whole_market_days

    logger.debug(f"Converting {hours}h to {market_days_total:.2f} market days ({whole_market_days} whole + {fractional_market_day:.2f} fractional)")

    # Start from gap_time's date
    current_date = gap_time.date()

    if direction == 'forward':
        # POST-GAP: Count forward market days

        # Special case: if hours = 0, return gap_time
        if hours == 0:
            return gap_time

        # Count forward whole market days from gap_time's date
        # The first market day is gap_time's date (if it's a market day)
        market_days_counted = 0

        # Check if gap_time's date is a market day
        if is_market_day(current_date):
            market_days_counted = 1  # Count current day as first market day

        # Count additional market days
        while market_days_counted < whole_market_days:
            current_date = get_next_market_day(current_date)
            market_days_counted += 1

        # Handle fractional day
        if fractional_market_day > 0:
            # Move to next market day for the fractional portion
            current_date = get_next_market_day(current_date)

            # Calculate time within fractional market day
            # Trading day: 04:00-20:00 (16 hours)
            # fraction × 16 hours from 04:00
            hours_into_trading_day = fractional_market_day * 16
            result_time = datetime.combine(current_date, datetime.min.time()).replace(hour=4, minute=0, second=0, tzinfo=EASTERN_TZ) + timedelta(hours=hours_into_trading_day)

            logger.debug(f"Forward offset: {whole_market_days} whole days + {fractional_market_day:.2f} fractional → end at {current_date} {result_time.strftime('%H:%M')}")
        else:
            # Whole number of market days: end at 20:00 of final market day
            result_time = datetime.combine(current_date, datetime.min.time()).replace(hour=20, minute=0, second=0, tzinfo=EASTERN_TZ)

            logger.debug(f"Forward offset: {whole_market_days} whole days → end at {current_date} 20:00")

        return result_time

    elif direction == 'backward':
        # PRE-GAP: Count backward market days

        # Special case: if hours = 0, return gap_time
        if hours == 0:
            return gap_time

        # Count backward whole market days from gap_time's date
        market_days_counted = 0

        # Count backward market days (not including gap_time's date)
        while market_days_counted < whole_market_days:
            current_date = get_previous_market_day(current_date)
            market_days_counted += 1

        # Handle fractional day
        if fractional_market_day > 0:
            # Fractional pre-gap: go back fractional portion into previous market day
            current_date = get_previous_market_day(current_date)

            # Calculate time within fractional market day
            # Trading day: 04:00-20:00 (16 hours)
            # For pre-gap, we count from END of trading day backward
            # fraction × 16 hours before 20:00
            hours_before_eod = fractional_market_day * 16
            result_time = datetime.combine(current_date, datetime.min.time()).replace(hour=20, minute=0, second=0, tzinfo=EASTERN_TZ) - timedelta(hours=hours_before_eod)

            logger.debug(f"Backward offset: {whole_market_days} whole days + {fractional_market_day:.2f} fractional → start at {current_date} {result_time.strftime('%H:%M')}")
        else:
            # Whole number of market days: start at 04:00 of final market day
            result_time = datetime.combine(current_date, datetime.min.time()).replace(hour=4, minute=0, second=0, tzinfo=EASTERN_TZ)

            logger.debug(f"Backward offset: {whole_market_days} whole days → start at {current_date} 04:00")

        return result_time

    else:
        raise ValueError(f"Invalid direction: {direction}")


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

    def nextValidId(self, orderId: int):
        """Connection ready signal"""
        super().nextValidId(orderId)
        self.next_order_id = orderId
        logger.info(f"Connection established. Next valid order ID: {orderId}")
        app_state["connection_ready"].set()

    def error(self, reqId: int, errorCode: int, errorString: str):
        """Handle connection/request errors"""
        # Informational messages (not errors)
        informational_codes = {2104, 2106, 2158, 2107, 2119, 2176}  # Removed 162 - needs dedicated handler

        if errorCode in informational_codes:
            if errorCode == 162 and "pacing violation" in errorString.lower():
                logger.warning(f"IBKR pacing violation detected (reqId {reqId}): {errorString}")
            else:
                logger.info(f"Info {errorCode}: {errorString}")
            return

        # Fatal connection errors
        fatal_codes = {502, 504, 1100, 1101, 1102, 1300}
        if errorCode in fatal_codes:
            if errorCode == 502:
                logger.error(f"FATAL ERROR 502: Cannot connect to IB Gateway")
                client_id = app_state.get('client_id', 'unknown')
                logger.error(f"Most common cause: Client ID {client_id} is already in use")
                logger.error("")
                logger.error("Troubleshooting steps:")
                logger.error("  1. Check if another HGUF instance is running (kill it if found)")
                logger.error("  2. Check IB Gateway active connections: IB Gateway → View → Messages API")
                logger.error("  3. Try a different client ID (e.g., --client-id 701)")
                logger.error("  4. Ensure IB Gateway/TWS is running")
                logger.error("  5. Verify 'Enable ActiveX and Socket EClients' is enabled in API settings")
                logger.error("  6. Check port configuration (4001 for live, 4002 for paper)")
            else:
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

                # Signal event for efficient waiting even on error
                if reqId in app_state["request_events"]:
                    app_state["request_events"][reqId].set()
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
        # Get symbol and bar count for informative logging
        symbol_info = app_state["request_map"].get(reqId, ("unknown", "unknown", None))
        symbol = symbol_info[0]
        bar_count = len(app_state["historical_data"].get(reqId, []))
        logger.info(f"{symbol}: Historical data request complete - received {bar_count} bars (reqId={reqId}, start={start}, end={end})")

        with self.pending_requests_lock:
            if reqId in app_state["pending_requests"]:
                app_state["pending_requests"].remove(reqId)

            # Signal event-based completion for efficient waiting
            # Use try-except to handle race condition where event was already deleted
            if reqId in app_state["request_events"]:
                try:
                    app_state["request_events"][reqId].set()
                except Exception as e:
                    logger.debug(f"Event signal failed for reqId {reqId}: {e}")


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
        120: '2 hours',
        240: '4 hours',
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
    Fetch Float, Institution%, and Net Income from yfinance with 24-hour caching.
    Returns: dict with 'float_millions', 'institution_pct', 'exchange', 'net_income_millions' keys
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
        net_income = info.get('netIncome', None)  # TTM Net Income

        # Convert to appropriate units
        float_millions = float_shares / 1e6 if float_shares else None
        institution_pct = inst_pct * 100 if inst_pct is not None else None
        net_income_millions = (net_income / 1e6) if (net_income is not None and isinstance(net_income, (int, float))) else None

        result = {
            'float_millions': float_millions,
            'institution_pct': institution_pct,
            'exchange': exchange,
            'net_income_millions': net_income_millions
        }

        # Cache result
        app_state["yfinance_cache"][symbol] = {
            'data': result,
            'timestamp': current_time
        }

        # Enhanced logging to include NetIncome status
        if float_millions and institution_pct:
            if net_income_millions:
                logger.debug(f"{symbol}: Fetched yfinance data - Float: {float_millions:.2f}M, Inst: {institution_pct:.2f}%, NetIncome: {net_income_millions:.2f}M")
            else:
                logger.debug(f"{symbol}: Fetched yfinance data - Float: {float_millions:.2f}M, Inst: {institution_pct:.2f}%, NetIncome: NA (not available)")
        else:
            logger.debug(f"{symbol}: yfinance data incomplete - Float: {float_millions}, Inst: {institution_pct}, NetIncome: {net_income_millions}")

        return result

    except Exception as e:
        logger.warning(f"{symbol}: Failed to fetch yfinance data: {e}")
        return {'float_millions': None, 'institution_pct': None, 'exchange': None, 'net_income_millions': None}


def calculate_volume_ratio(symbol, date_11):
    """
    Calculate volume ratio (xVol) for date-11 relative to 10-day average.

    Formula:
        xVol = (date-11 daily volume) / (average of 10 prior trading days)

    Args:
        symbol (str): Stock ticker symbol (e.g., "AAPL")
        date_11 (datetime): Day before gap event (timezone-aware datetime)

    Returns:
        float or None: Volume ratio (2 decimals, e.g., 2.35) or None if error
    """
    cache_ttl = 86400  # 24 hours
    current_time = time.time()

    # Check cache
    if symbol in app_state["yfinance_cache"]:
        cache_entry = app_state["yfinance_cache"][symbol]
        cached_data = cache_entry.get('data', {})
        if 'xvol' in cached_data and current_time - cache_entry['timestamp'] < cache_ttl:
            logger.debug(f"{symbol}: Using cached xVol data")
            return cached_data['xvol']

    logger.info(f"{symbol}: Calculating xVol (volume ratio)...")

    try:
        # Extract date from date_11 datetime
        date_11_date = date_11.date()

        # Fetch 20 calendar days to ensure ≥11 trading days
        start_date = date_11_date - timedelta(days=20)
        end_date = date_11_date + timedelta(days=1)  # yfinance end is exclusive

        logger.debug(f"{symbol}: Fetching volume data from {start_date} to {date_11_date}")

        # Fetch historical volume from yfinance
        ticker = yf.Ticker(symbol)
        history = ticker.history(
            start=start_date.strftime('%Y-%m-%d'),
            end=end_date.strftime('%Y-%m-%d'),
            interval='1d',
            actions=False
        )

        # Rate limiting
        time.sleep(0.1)

        # Validate sufficient data
        if len(history) < 11:
            logger.warning(f"{symbol}: Insufficient volume data (got {len(history)} days, need 11)")
            return None

        # Extract date-11 volume (last row)
        date_11_volume = int(history.iloc[-1]['Volume'])
        logger.debug(f"{symbol}: date-11 volume = {date_11_volume:,}")

        # Extract prior 10 days volumes
        prior_volumes = history.iloc[-11:-1]['Volume'].tolist()

        # Calculate average
        avg_volume = sum(prior_volumes) / 10.0
        logger.debug(f"{symbol}: Average 10-day volume = {avg_volume:,.2f}")

        # Handle zero average edge case
        if avg_volume == 0:
            logger.warning(f"{symbol}: Zero average volume")
            return None

        # Calculate xVol
        xvol = date_11_volume / avg_volume
        xvol = round(xvol, 2)

        logger.info(f"{symbol}: xVol={xvol:.2f} (date-11 vol={date_11_volume:,}, 10-day avg={avg_volume:,.0f})")

        # Update cache with xVol
        if symbol not in app_state["yfinance_cache"]:
            app_state["yfinance_cache"][symbol] = {'data': {}, 'timestamp': current_time}

        app_state["yfinance_cache"][symbol]['data']['xvol'] = xvol
        app_state["yfinance_cache"][symbol]['timestamp'] = current_time
        logger.debug(f"{symbol}: Cached xVol data")

        return xvol

    except Exception as e:
        logger.warning(f"{symbol}: Error calculating xVol: {e}")
        return None


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


def calculate_gusday_label(bar_datetime, gusday_date):
    """
    Calculate GUSday label based on market day difference.

    GUSday labeling follows market days (trading days only, skipping weekends and holidays):
    - Bars on same market date as gap day → 'GUSday'
    - Bars on previous market dates → 'GUSday-1', 'GUSday-2', etc.
    - Bars on future market dates → 'GUSday+1', 'GUSday+2', etc.

    Args:
        bar_datetime: Bar timestamp (timezone-aware datetime)
        gusday_date: GUSday reference date (day-12, timezone-aware datetime)

    Returns:
        str: GUSday label (e.g., 'GUSday', 'GUSday-1', 'GUSday+2')

    Example:
        Gap on Dec 12 (Fri):
        - Dec 12 bars → 'GUSday'
        - Dec 11 bars → 'GUSday-1'
        - Dec 15 bars (Mon) → 'GUSday+1' (skips weekend)
        - Dec 16 bars (Tue) → 'GUSday+2'
    """
    # Extract calendar dates (00:00-23:59 boundaries)
    bar_date = bar_datetime.date()
    gus_date = gusday_date.date()

    # Calculate market day offset (skips weekends and holidays)
    market_day_diff = count_market_days_between(gus_date, bar_date)

    if market_day_diff == 0:
        return 'GUSday'
    elif market_day_diff < 0:
        return f'GUSday-{abs(market_day_diff)}'  # Explicit: 'GUSday-1', 'GUSday-2'
    else:
        return f'GUSday+{market_day_diff}'  # Explicit: 'GUSday+1', 'GUSday+2'


def deduplicate_bars(bars):
    """
    Remove duplicate bars based on timestamp.
    Keeps first occurrence of each unique timestamp.

    This is a safety measure to prevent duplicate rows in output.
    Duplicates should not occur in normal operation - if this function
    removes duplicates, it indicates a bug in the fetch logic that should
    be investigated.

    Args:
        bars: List of bar dicts with 'date' field (datetime)

    Returns:
        Deduplicated list of bars, maintaining original order
    """
    seen_timestamps = set()
    deduplicated = []
    duplicates_found = 0

    for bar in bars:
        timestamp = bar['date']
        if timestamp not in seen_timestamps:
            seen_timestamps.add(timestamp)
            deduplicated.append(bar)
        else:
            duplicates_found += 1
            logger.warning(f"Duplicate bar detected at {timestamp}, skipping (OHLCV: {bar['open']}/{bar['high']}/{bar['low']}/{bar['close']}, Vol: {bar['volume']})")

    if duplicates_found > 0:
        logger.warning(f"DEDUPLICATION: Removed {duplicates_found} duplicate bars out of {len(bars)} total")

    return deduplicated


# ============================================================================
# Cache Management Functions
# ============================================================================

import pickle
import hashlib

def get_cache_key(symbol, date, bar_type, **kwargs):
    """
    Generate a unique cache key for historical data.

    Args:
        symbol: Stock symbol
        date: Date for the data
        bar_type: Type of bars ('daily', 'premarket', 'intraday')
        **kwargs: Additional parameters (e.g., candle_size for intraday)

    Returns:
        str: Cache key in format {symbol}_{date}_{bar_type}_{hash}.pkl
    """
    date_str = date.strftime('%Y%m%d') if isinstance(date, datetime) else str(date)

    # Include kwargs in hash for uniqueness
    kwargs_str = '_'.join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    hash_input = f"{symbol}_{date_str}_{bar_type}_{kwargs_str}"
    hash_suffix = hashlib.md5(hash_input.encode()).hexdigest()[:8]

    return f"{symbol}_{date_str}_{bar_type}_{hash_suffix}.pkl"


def load_from_cache(cache_dir, cache_key, ttl_seconds=86400):
    """
    Load data from cache if it exists and hasn't expired.

    Args:
        cache_dir: Cache directory path
        cache_key: Cache key (filename)
        ttl_seconds: Time-to-live in seconds (default: 24 hours)
                     Use 0 for permanent cache (historical data >1 month old)

    Returns:
        Cached data or None if not found/expired
    """
    cache_file = os.path.join(cache_dir, cache_key)

    if not os.path.exists(cache_file):
        return None

    try:
        # Check cache age
        file_age = time.time() - os.path.getmtime(cache_file)

        # TTL = 0 means permanent cache (for historical data)
        if ttl_seconds > 0 and file_age > ttl_seconds:
            logger.debug(f"Cache expired: {cache_key} (age: {file_age:.0f}s > TTL: {ttl_seconds}s)")
            return None

        # Load cached data
        with open(cache_file, 'rb') as f:
            cached_data = pickle.load(f)

        logger.debug(f"Cache hit: {cache_key} (age: {file_age:.0f}s)")
        return cached_data

    except Exception as e:
        logger.warning(f"Failed to load cache {cache_key}: {e}")
        return None


def save_to_cache(cache_dir, cache_key, data):
    """
    Save data to cache.

    Args:
        cache_dir: Cache directory path
        cache_key: Cache key (filename)
        data: Data to cache (must be picklable)
    """
    try:
        os.makedirs(cache_dir, exist_ok=True)
        cache_file = os.path.join(cache_dir, cache_key)

        with open(cache_file, 'wb') as f:
            pickle.dump(data, f)

        logger.debug(f"Cache saved: {cache_key}")

    except Exception as e:
        logger.warning(f"Failed to save cache {cache_key}: {e}")


def cleanup_request_state(req_id):
    """
    Remove all traces of a request from app_state.
    Thread-safe cleanup for timed-out or completed requests.

    Args:
        req_id: Request ID to clean up
    """
    # Use lock if available, otherwise proceed without
    lock = app_state.get("pending_requests_lock")

    if lock:
        with lock:
            _cleanup_request_state_unsafe(req_id)
    else:
        _cleanup_request_state_unsafe(req_id)


def _cleanup_request_state_unsafe(req_id):
    """Internal cleanup without lock (must be called with lock held)"""
    app_state["request_events"].pop(req_id, None)
    app_state["pending_requests"].discard(req_id)
    app_state["request_map"].pop(req_id, None)
    app_state["historical_data"].pop(req_id, None)


def wait_with_connection_check(request_event, timeout_seconds, symbol, request_type):
    """
    Wait for request event with connection-loss awareness.

    Args:
        request_event: Threading event to wait on
        timeout_seconds: Maximum wait time in seconds
        symbol: Stock symbol (for logging)
        request_type: Type of request (for logging)

    Returns:
        bool: True if event was set, False if timeout or connection lost
    """
    timeout_start = time.time()

    while time.time() - timeout_start < timeout_seconds:
        # Check for connection loss first
        if app_state["connection_lost"].is_set():
            logger.error(f"{symbol}: Connection lost during {request_type} request")
            return False

        # Check for shutdown signal
        if app_state["shutdown"].is_set():
            logger.warning(f"{symbol}: Shutdown requested during {request_type} request")
            return False

        # Wait for event with 1-second intervals
        if request_event.wait(timeout=1):
            return True  # Success

    # Timeout
    return False


# ============================================================================
# Gap Detection Functions
# ============================================================================

def fetch_daily_bars(app, symbol, start_date, end_date, retry=True):
    """
    Fetch daily bars for a symbol covering the time window.
    Supports disk-based caching for performance.
    Returns list of daily bars or None if failed.

    Args:
        app: IBKR API application instance
        symbol: Stock symbol
        start_date: Start date (datetime)
        end_date: End date (datetime)
        retry: Whether to retry on timeout (default: True)
    """
    # Check cache first
    if app_state["use_cache"]:
        cache_key = get_cache_key(symbol, end_date, 'daily', start=start_date.strftime('%Y%m%d'))

        # Determine TTL: permanent cache for data >1 month old
        data_age_days = (datetime.now(EASTERN_TZ) - end_date).days
        ttl = 0 if data_age_days > 30 else 86400  # 0 = permanent, 24h otherwise

        cached_data = load_from_cache(app_state["cache_dir"], cache_key, ttl_seconds=ttl)
        if cached_data is not None:
            app_state["cache_stats"]["hits"] += 1
            logger.debug(f"{symbol}: Using cached daily bars ({len(cached_data)} bars)")
            return cached_data

        app_state["cache_stats"]["misses"] += 1

    contract = create_contract(symbol)

    # Calculate duration (days from start to end + 1 day before)
    days = (end_date - start_date).days + 2  # +1 for day-before, +1 for inclusive end
    duration_str = f"{days} D"

    # Generate request ID (thread-safe)
    with app_state["next_req_id_lock"]:
        req_id = app_state["next_req_id"]
        app_state["next_req_id"] += 1

    # Create event for this request
    request_event = threading.Event()
    app_state["request_events"][req_id] = request_event

    # Register request
    app_state["request_map"][req_id] = (symbol, 'daily', None)
    app_state["pending_requests"].add(req_id)
    app_state["historical_data"][req_id] = []

    # Request daily bars
    end_datetime_str = format_datetime_for_ibkr(end_date.replace(hour=16, minute=0, second=0))

    logger.debug(f"{symbol}: Requesting daily bars from IBKR (endDateTime={end_datetime_str}, duration={duration_str})")

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

    # Wait for completion with connection-loss awareness
    if not wait_with_connection_check(request_event, 30, symbol, "daily bars"):
        logger.warning(f"{symbol}: Daily bars request timed out after 30 seconds")
        cleanup_request_state(req_id)

        # Track timeout
        if "batch_stats" in app_state:
            app_state["batch_stats"]["current_batch_timeouts"] += 1
            app_state["batch_stats"]["global_timeouts"] += 1

        # Retry once for critical daily bars request
        if retry:
            logger.info(f"{symbol}: Retrying daily bars request (1/1)...")
            time.sleep(1)  # Brief pause before retry
            return fetch_daily_bars(app, symbol, start_date, end_date, retry=False)
        else:
            return None

    # Retrieve bars BEFORE cleanup (cleanup deletes the data!)
    bars = app_state["historical_data"].get(req_id, [])
    logger.debug(f"{symbol}: Received {len(bars)} daily bars from IBKR")

    # Clean up after retrieving data
    cleanup_request_state(req_id)

    # CRITICAL FIX: Sort bars by date in ascending order (IBKR may return in reverse order)
    if bars:
        bars.sort(key=lambda b: b['date'])
        logger.info(f"{symbol}: Fetched {len(bars)} daily bars (range: {bars[0]['date'].strftime('%Y-%m-%d')} to {bars[-1]['date'].strftime('%Y-%m-%d')})")
    else:
        logger.warning(f"{symbol}: No daily bars returned by IBKR for date range")

    # Save to cache
    if app_state["use_cache"] and bars:
        save_to_cache(app_state["cache_dir"], cache_key, bars)

    return bars


def fetch_price_at_time(app, symbol, target_datetime):
    """
    Fetch the price at a specific time point (e.g., 09:29).
    Returns the close price of a 1-minute bar ending at target_datetime.
    Returns None if failed or no data available.
    """
    # Create contract
    contract = Contract()
    contract.symbol = symbol
    contract.secType = 'STK'
    contract.exchange = 'SMART'
    contract.currency = 'USD'

    # Generate request ID (thread-safe)
    with app_state["next_req_id_lock"]:
        req_id = app_state["next_req_id"]
        app_state["next_req_id"] += 1

    # Create event for this request
    request_event = threading.Event()
    app_state["request_events"][req_id] = request_event

    # Register request
    app_state["request_map"][req_id] = (symbol, 'checkpoint', target_datetime)
    app_state["pending_requests"].add(req_id)
    app_state["historical_data"][req_id] = []

    # Format datetime for IBKR API
    end_datetime_str = format_datetime_for_ibkr(target_datetime)

    logger.debug(f"{symbol}: Requesting price at {target_datetime.strftime('%Y-%m-%d %H:%M')} from IBKR")

    # Request single 1-minute bar ending at target_datetime
    app.reqHistoricalData(
        reqId=req_id,
        contract=contract,
        endDateTime=end_datetime_str,
        durationStr='60 S',  # 60 seconds = 1 minute
        barSizeSetting='1 min',
        whatToShow='TRADES',
        useRTH=0,  # Include extended hours (for pre-market)
        formatDate=1,
        keepUpToDate=False,
        chartOptions=[]
    )

    # Wait for completion with connection-loss awareness
    if not wait_with_connection_check(request_event, 15, symbol, "checkpoint price"):
        logger.warning(f"{symbol}: Checkpoint price request timed out after 15 seconds")
        cleanup_request_state(req_id)

        # Track timeout
        if "batch_stats" in app_state:
            app_state["batch_stats"]["current_batch_timeouts"] += 1
            app_state["batch_stats"]["global_timeouts"] += 1

        return None  # No retry for checkpoint prices (individual checkpoints can fail)

    # Clean up after successful completion
    cleanup_request_state(req_id)

    bars = app_state["historical_data"].get(req_id, [])

    # Return close price of the bar if available
    if bars and len(bars) > 0:
        price = bars[0]['close']
        logger.debug(f"{symbol}: Checkpoint price at {target_datetime.strftime('%Y-%m-%d %H:%M')} = ${price:.2f}")
        return price
    else:
        logger.warning(f"{symbol}: No price data available at {target_datetime.strftime('%Y-%m-%d %H:%M')}")
        return None


def align_time_range_to_eth_epoch(start_datetime, end_datetime, candle_size_minutes, is_gusday=False):
    """
    Align time range to ETH window epoch (04:00) for consistent candle boundaries.

    For non-GUSday candles, adjusts start/end times to align with candle boundaries
    measured from 04:00 each day. GUSday candles remain unchanged.

    Args:
        start_datetime: Requested start time (timezone-aware)
        end_datetime: Requested end time (timezone-aware)
        candle_size_minutes: Candle size in minutes
        is_gusday: If True, skip alignment (return original times)

    Returns:
        Tuple: (aligned_start, aligned_end, alignment_applied: bool)

    Examples:
        # 240-min candles, non-GUSday
        align_time_range_to_eth_epoch(
            datetime(2025, 11, 24, 10, 37),  # 10:37
            datetime(2025, 11, 24, 18, 22),  # 18:22
            240, is_gusday=False
        )
        → (2025-11-24 08:00, 2025-11-24 20:00, True)

        # GUSday candles - no alignment
        align_time_range_to_eth_epoch(
            datetime(2025, 11, 24, 10, 37),
            datetime(2025, 11, 24, 18, 22),
            10, is_gusday=True
        )
        → (2025-11-24 10:37, 2025-11-24 18:22, False)
    """
    # Skip alignment for GUSday candles
    if is_gusday:
        return (start_datetime, end_datetime, False)

    # Find ETH epoch (04:00) for the start datetime's date
    # If start time is before 04:00, use previous day's 04:00 as epoch
    if start_datetime.hour < 4:
        eth_epoch = (start_datetime - timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
    else:
        eth_epoch = start_datetime.replace(hour=4, minute=0, second=0, microsecond=0)

    # Calculate minutes from ETH epoch to start and end times
    minutes_start_since_epoch = (start_datetime - eth_epoch).total_seconds() / 60
    minutes_end_since_epoch = (end_datetime - eth_epoch).total_seconds() / 60

    # Handle multi-day ranges: if end is more than 24 hours from epoch, find appropriate epoch for end
    if minutes_end_since_epoch > 1440:  # More than 24 hours (1 day)
        # Find ETH epoch for the end datetime's date
        if end_datetime.hour < 4:
            eth_epoch_end = (end_datetime - timedelta(days=1)).replace(hour=4, minute=0, second=0, microsecond=0)
        else:
            eth_epoch_end = end_datetime.replace(hour=4, minute=0, second=0, microsecond=0)

        # Recalculate end minutes from its appropriate epoch
        minutes_end_from_its_epoch = (end_datetime - eth_epoch_end).total_seconds() / 60

        # Align end to its epoch
        aligned_end_minutes = math.ceil(minutes_end_from_its_epoch / candle_size_minutes) * candle_size_minutes
        aligned_end = eth_epoch_end + timedelta(minutes=aligned_end_minutes)
    else:
        # Single-day range: align end to the same epoch as start
        aligned_end_minutes = math.ceil(minutes_end_since_epoch / candle_size_minutes) * candle_size_minutes
        aligned_end = eth_epoch + timedelta(minutes=aligned_end_minutes)

    # Align start (floor to previous boundary)
    aligned_start_minutes = math.floor(minutes_start_since_epoch / candle_size_minutes) * candle_size_minutes
    aligned_start = eth_epoch + timedelta(minutes=aligned_start_minutes)

    # Check if alignment changed the times
    alignment_applied = (aligned_start != start_datetime) or (aligned_end != end_datetime)

    if alignment_applied:
        logger.debug(f"Aligned non-GUSday time range ({candle_size_minutes}-min candles): "
                    f"Start: {start_datetime.strftime('%Y-%m-%d %H:%M')} → {aligned_start.strftime('%Y-%m-%d %H:%M')}, "
                    f"End: {end_datetime.strftime('%Y-%m-%d %H:%M')} → {aligned_end.strftime('%Y-%m-%d %H:%M')}")

    return (aligned_start, aligned_end, alignment_applied)


def _fetch_single_intraday_request(app, symbol, start_datetime, end_datetime, candle_size_minutes, is_gusday=False, retry=True):
    """
    Fetch intraday bars for a single request (duration must be <= 24 hours).
    This is the core request logic extracted from fetch_intraday_bars.
    Supports disk-based caching for performance.
    Returns list of intraday bars or None if failed.

    Args:
        app: IBKR API app instance
        symbol: Stock symbol
        start_datetime: Start of time range (timezone-aware)
        end_datetime: End of time range (timezone-aware)
        candle_size_minutes: Candle size in minutes
        is_gusday: If True, skip alignment (for GUSday period 04:00-20:00)
        retry: Whether to retry on timeout (default: True)
    """
    # Align time range for non-GUSday candles
    aligned_start, aligned_end, alignment_applied = align_time_range_to_eth_epoch(
        start_datetime, end_datetime, candle_size_minutes, is_gusday
    )

    if alignment_applied:
        logger.debug(f"{symbol}: Aligned non-GUSday time range: "
                    f"{start_datetime.strftime('%H:%M')} → {aligned_start.strftime('%H:%M')}, "
                    f"{end_datetime.strftime('%H:%M')} → {aligned_end.strftime('%H:%M')}")
        # Update request parameters with aligned times
        start_datetime = aligned_start
        end_datetime = aligned_end

    # Check cache first
    if app_state["use_cache"]:
        cache_key = get_cache_key(
            symbol, end_datetime, 'intraday',
            start=start_datetime.strftime('%Y%m%d%H%M'),
            candle=candle_size_minutes,
            aligned='Y' if not is_gusday else 'N'
        )

        # Debug logging for cache key
        logger.debug(f"{symbol}: Cache key: {cache_key} for range {start_datetime.strftime('%Y-%m-%d %H:%M')} to {end_datetime.strftime('%Y-%m-%d %H:%M')}")

        # Determine TTL: permanent cache for data >1 month old
        data_age_days = (datetime.now(EASTERN_TZ) - end_datetime).days
        ttl = 0 if data_age_days > 30 else 86400

        cached_data = load_from_cache(app_state["cache_dir"], cache_key, ttl_seconds=ttl)
        if cached_data is not None:
            app_state["cache_stats"]["hits"] += 1
            logger.debug(f"{symbol}: Using cached intraday bars ({len(cached_data)} bars)")
            return cached_data

        app_state["cache_stats"]["misses"] += 1

    contract = create_contract(symbol)

    # Calculate duration in seconds
    duration_seconds = int((end_datetime - start_datetime).total_seconds())

    # Map candle size to IBKR bar size
    bar_size = map_candle_size_to_ibkr_bar_size(candle_size_minutes)

    # Generate request ID (thread-safe)
    with app_state["next_req_id_lock"]:
        req_id = app_state["next_req_id"]
        app_state["next_req_id"] += 1

    # Create event for this request
    request_event = threading.Event()
    app_state["request_events"][req_id] = request_event

    # Register request
    app_state["request_map"][req_id] = (symbol, 'intraday', (start_datetime, end_datetime))
    app_state["pending_requests"].add(req_id)
    app_state["historical_data"][req_id] = []

    # Request intraday bars
    end_datetime_str = format_datetime_for_ibkr(end_datetime)
    duration_str = f"{duration_seconds} S"

    logger.debug(f"{symbol}: Requesting intraday bars from IBKR (endDateTime={end_datetime_str}, duration={duration_str}, barSize={bar_size})")

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

    # Wait for completion with connection-loss awareness
    if not wait_with_connection_check(request_event, 40, symbol, "intraday bars"):
        logger.warning(f"{symbol}: Intraday bars request timed out after 40 seconds")
        cleanup_request_state(req_id)

        # Track timeout
        if "batch_stats" in app_state:
            app_state["batch_stats"]["current_batch_timeouts"] += 1
            app_state["batch_stats"]["global_timeouts"] += 1

        # Retry once for critical intraday bars request
        if retry:
            logger.info(f"{symbol}: Retrying intraday bars request (1/1)...")
            time.sleep(1)  # Brief pause before retry
            return _fetch_single_intraday_request(app, symbol, start_datetime, end_datetime,
                                                  candle_size_minutes, is_gusday, retry=False)
        else:
            return None

    # Retrieve bars BEFORE cleanup (cleanup deletes the data!)
    bars = app_state["historical_data"].get(req_id, [])
    logger.debug(f"{symbol}: Received {len(bars)} intraday bars from IBKR")

    # Clean up after retrieving data
    cleanup_request_state(req_id)

    # Save to cache
    if app_state["use_cache"] and bars:
        save_to_cache(app_state["cache_dir"], cache_key, bars)

    return bars


def _fetch_intraday_bars_split(app, symbol, start_datetime, end_datetime, candle_size_minutes, is_gusday=False):
    """
    Split large time range into multiple requests at ETH window boundaries (04:00).
    Handles requests spanning 2+ days by fetching each segment separately.
    This ensures all intermediate days are included (fixes bug where only first and last day were fetched).

    For non-GUSday candles, splits at 04:00 boundaries to align with ETH window start.
    For GUSday candles, uses original midnight-based splitting.

    For example, a non-GUSday request from 2025-12-11 09:29 to 2025-12-15 09:29 will fetch:
      - Segment 1: 2025-12-11 09:29 to 2025-12-12 04:00:00
      - Segment 2: 2025-12-12 04:00:00 to 2025-12-13 04:00:00
      - Segment 3: 2025-12-13 04:00:00 to 2025-12-14 04:00:00
      - Segment 4: 2025-12-14 04:00:00 to 2025-12-15 04:00:00
      - Segment 5: 2025-12-15 04:00:00 to 2025-12-15 09:29:00

    Args:
        app: IBKR API app instance
        symbol: Stock symbol
        start_datetime: Start of time range (timezone-aware)
        end_datetime: End of time range (timezone-aware)
        candle_size_minutes: Candle size in minutes
        is_gusday: If True, use midnight split (for GUSday period 04:00-20:00)

    Returns combined list of intraday bars or None if failed.
    """
    all_bars = []

    # Determine split boundary (04:00 for non-GUSday, 00:00 for GUSday)
    split_hour = 0 if is_gusday else 4
    split_type = "midnight" if is_gusday else "04:00 ETH"

    # Calculate number of segments by finding 04:00/00:00 boundaries
    current = start_datetime
    segment_num = 0

    logger.debug(f"{symbol}: Splitting multi-day request at {split_type} boundaries")

    while current < end_datetime:
        segment_num += 1

        # Find next split boundary
        if current.hour < split_hour:
            # Next boundary is on the same day
            next_boundary = current.replace(hour=split_hour, minute=0, second=0, microsecond=0)
        else:
            # Next boundary is tomorrow
            next_boundary = (current + timedelta(days=1)).replace(hour=split_hour, minute=0, second=0, microsecond=0)

        # Segment end is the minimum of next boundary and final end_datetime
        segment_end = min(next_boundary, end_datetime)

        # Skip if segment is on a non-market day
        segment_date = current.date()
        if not is_market_day(segment_date):
            logger.debug(f"{symbol}: Skipping non-market segment starting {current.strftime('%Y-%m-%d %H:%M')}")
            current = segment_end
            continue

        # Fetch bars for this segment
        logger.debug(f"{symbol}: Fetching segment {segment_num}: {current.strftime('%Y-%m-%d %H:%M')} to {segment_end.strftime('%Y-%m-%d %H:%M')}")

        # No retry for segments - already retrying at higher level
        bars = _fetch_single_intraday_request(app, symbol, current, segment_end, candle_size_minutes, is_gusday, retry=False)
        if bars:
            all_bars.extend(bars)
            logger.debug(f"{symbol}: Segment {segment_num} returned {len(bars)} bars, cumulative total: {len(all_bars)} bars")

        # Move to next segment
        current = segment_end

        # Throttle between requests
        time.sleep(0.05)

    logger.debug(f"{symbol}: Multi-day split complete - Total {len(all_bars)} bars across {segment_num} segments")

    return all_bars if all_bars else None


def segment_time_range_by_gusday(start_datetime, end_datetime, gusday_date):
    """
    Segment a time range into GUSday and non-GUSday periods.

    GUSday period: 04:00-20:00 on the gap day (market hours)
    Non-GUSday periods: All other times

    Args:
        start_datetime: Start of time range (timezone-aware datetime)
        end_datetime: End of time range (timezone-aware datetime)
        gusday_date: Gap day reference (timezone-aware datetime with date component)

    Returns:
        List of segment dicts: [
            {'start': datetime, 'end': datetime, 'is_gusday': bool},
            ...
        ]
        Sorted chronologically, non-overlapping, covering entire range.

    Examples:
        # Case 1: Range entirely before GUSday
        start: 2025-11-24 10:00, end: 2025-11-24 16:00, gusday: 2025-11-25
        → [{'start': 2025-11-24 10:00, 'end': 2025-11-24 16:00, 'is_gusday': False}]

        # Case 2: Range spans GUSday boundaries
        start: 2025-11-25 02:00, end: 2025-11-25 22:00, gusday: 2025-11-25
        → [
            {'start': 2025-11-25 02:00, 'end': 2025-11-25 04:00, 'is_gusday': False},
            {'start': 2025-11-25 04:00, 'end': 2025-11-25 20:00, 'is_gusday': True},
            {'start': 2025-11-25 20:00, 'end': 2025-11-25 22:00, 'is_gusday': False}
          ]

        # Case 3: Range entirely within GUSday
        start: 2025-11-25 09:30, end: 2025-11-25 16:00, gusday: 2025-11-25
        → [{'start': 2025-11-25 09:30, 'end': 2025-11-25 16:00, 'is_gusday': True}]
    """
    segments = []

    # Define GUSday boundaries (04:00-20:00 on gap day)
    gusday_start = gusday_date.replace(hour=4, minute=0, second=0, microsecond=0)
    gusday_end = gusday_date.replace(hour=20, minute=0, second=0, microsecond=0)

    # Case 1: Entire range before GUSday start
    if end_datetime <= gusday_start:
        return [{'start': start_datetime, 'end': end_datetime, 'is_gusday': False}]

    # Case 2: Entire range after GUSday end
    if start_datetime >= gusday_end:
        return [{'start': start_datetime, 'end': end_datetime, 'is_gusday': False}]

    # Case 3: Range spans GUSday boundaries - need segmentation

    # Segment 1: Before GUSday start (if applicable)
    if start_datetime < gusday_start:
        segments.append({
            'start': start_datetime,
            'end': min(end_datetime, gusday_start),
            'is_gusday': False
        })

    # Segment 2: GUSday period (if applicable)
    if start_datetime < gusday_end and end_datetime > gusday_start:
        segments.append({
            'start': max(start_datetime, gusday_start),
            'end': min(end_datetime, gusday_end),
            'is_gusday': True
        })

    # Segment 3: After GUSday end (if applicable)
    if end_datetime > gusday_end:
        segments.append({
            'start': max(start_datetime, gusday_end),
            'end': end_datetime,
            'is_gusday': False
        })

    # Validation: Ensure no gaps or overlaps
    if len(segments) > 1:
        for i in range(len(segments) - 1):
            assert segments[i]['end'] == segments[i+1]['start'], \
                f"Segment gap detected: {segments[i]['end']} != {segments[i+1]['start']}"

    # Validation: Ensure coverage of entire range
    assert segments[0]['start'] == start_datetime, \
        f"First segment doesn't start at range start: {segments[0]['start']} != {start_datetime}"
    assert segments[-1]['end'] == end_datetime, \
        f"Last segment doesn't end at range end: {segments[-1]['end']} != {end_datetime}"

    logger.debug(f"Segmented time range into {len(segments)} segments:")
    for i, seg in enumerate(segments):
        seg_type = "GUSday" if seg['is_gusday'] else "non-GUSday"
        logger.debug(f"  Segment {i+1}: {seg['start'].strftime('%Y-%m-%d %H:%M')} to {seg['end'].strftime('%Y-%m-%d %H:%M')} ({seg_type})")

    return segments


def fetch_intraday_bars_segmented(app, symbol, start_datetime, end_datetime,
                                   gusday_date, gusday_candle_size, nongusday_candle_size):
    """
    Fetch intraday bars with different candle sizes for GUSday and non-GUSday periods.

    GUSday period: 04:00-20:00 on gap day (uses gusday_candle_size)
    Non-GUSday periods: All other times (uses nongusday_candle_size)

    Args:
        app: IBKR API app instance
        symbol: Stock symbol
        start_datetime: Start of time range (timezone-aware)
        end_datetime: End of time range (timezone-aware)
        gusday_date: Gap day reference date (timezone-aware)
        gusday_candle_size: Candle size in minutes for GUSday period
        nongusday_candle_size: Candle size in minutes for non-GUSday periods

    Returns:
        List of intraday bars (merged from all segments, sorted by time)
        or None if all segments failed

    Implementation:
        1. Segment time range using segment_time_range_by_gusday()
        2. For each segment, fetch bars using appropriate candle size
        3. Merge all bars and sort chronologically
        4. Deduplicate at boundaries
        5. Return unified bar list
    """
    logger.debug(f"{symbol}: Segmented fetch - GUSday candles: {gusday_candle_size}min, non-GUSday: {nongusday_candle_size}min")

    # Segment the time range
    segments = segment_time_range_by_gusday(start_datetime, end_datetime, gusday_date)

    all_bars = []
    failed_segments = 0

    for i, segment in enumerate(segments):
        seg_start = segment['start']
        seg_end = segment['end']
        is_gusday = segment['is_gusday']

        # Determine candle size for this segment
        candle_size = gusday_candle_size if is_gusday else nongusday_candle_size
        seg_type = "GUSday" if is_gusday else "non-GUSday"

        logger.debug(f"{symbol}: Fetching segment {i+1}/{len(segments)} ({seg_type}): "
                    f"{seg_start.strftime('%Y-%m-%d %H:%M')} to {seg_end.strftime('%Y-%m-%d %H:%M')} "
                    f"with {candle_size}-min candles")

        # Use existing fetch_intraday_bars() which handles >24h splits
        # Pass is_gusday flag to enable/disable alignment
        segment_bars = fetch_intraday_bars(app, symbol, seg_start, seg_end, candle_size, is_gusday=is_gusday)

        if segment_bars and len(segment_bars) > 0:
            logger.debug(f"{symbol}: Segment {i+1} returned {len(segment_bars)} bars")
            all_bars.extend(segment_bars)
        else:
            logger.warning(f"{symbol}: Segment {i+1} ({seg_type}) returned no bars")
            failed_segments += 1

        # Throttle between segments (IBKR API rate limiting)
        if i < len(segments) - 1:  # Don't throttle after last segment
            time.sleep(0.05)

    # Check if all segments failed
    if failed_segments == len(segments):
        logger.error(f"{symbol}: All {len(segments)} segments failed to fetch data")
        return None

    # Sort bars chronologically (important when merging different candle sizes)
    if all_bars:
        all_bars.sort(key=lambda x: x['date'])

        # Deduplicate by timestamp (in case of boundary overlaps)
        seen_timestamps = set()
        deduped_bars = []
        for bar in all_bars:
            bar_time = bar['date']
            if bar_time not in seen_timestamps:
                deduped_bars.append(bar)
                seen_timestamps.add(bar_time)
            else:
                logger.debug(f"{symbol}: Skipping duplicate bar at {bar_time.strftime('%Y-%m-%d %H:%M')}")

        all_bars = deduped_bars
        logger.debug(f"{symbol}: Merged {len(all_bars)} total bars from {len(segments)} segments")

    return all_bars if all_bars else None


def fetch_intraday_bars(app, symbol, start_datetime, end_datetime, candle_size_minutes, is_gusday=False):
    """
    Fetch intraday bars from start_datetime to end_datetime with specified candle size.
    Automatically splits requests exceeding 24 hours to comply with IBKR API limits.
    Returns list of intraday bars or None if failed.

    Args:
        app: IBKR API app instance
        symbol: Stock symbol
        start_datetime: Start of time range (timezone-aware)
        end_datetime: End of time range (timezone-aware)
        candle_size_minutes: Candle size in minutes
        is_gusday: If True, skip alignment (for GUSday period 04:00-20:00)
    """
    duration_seconds = int((end_datetime - start_datetime).total_seconds())

    # IBKR limit: 86400 seconds (24 hours) per request
    if duration_seconds <= 86400:
        # Single request (original logic)
        return _fetch_single_intraday_request(app, symbol, start_datetime, end_datetime, candle_size_minutes, is_gusday)
    else:
        # Split into multiple requests
        logger.debug(f"{symbol}: Duration {duration_seconds}s exceeds 24h limit, splitting request")
        return _fetch_intraday_bars_split(app, symbol, start_datetime, end_datetime, candle_size_minutes, is_gusday)


def detect_gap_event_for_symbol(app, symbol, daily_bars, start_date, end_date, min_gap_pct, max_gap_pct, max_open_price):
    """
    Detect gap events for a single symbol across the time window.
    Returns list of gap event dicts or empty list.
    """
    gap_events = []

    # Need at least 2 consecutive days to detect gaps
    if len(daily_bars) < 2:
        logger.warning(f"{symbol}: Insufficient bars ({len(daily_bars)}) for gap detection (need >= 2)")
        return gap_events

    # Log gap detection entry at INFO level (critical diagnostic message)
    logger.info(f"{symbol}: Starting gap detection with {len(daily_bars)} bars (window: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')})")

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
        day_12_open = day_12_bar['open']  # Get open from daily bar (09:30 RTH open)

        # Calculate gap percentage using daily bars (standard gap definition)
        # Gap = (today_open - yesterday_close) / yesterday_close * 100
        gap_pct = ((day_12_open - day_11_close) / day_11_close) * 100

        # Debug: Log every gap calculation (helps identify why gaps aren't detected)
        logger.debug(f"{symbol}: {day_11_date.strftime('%Y-%m-%d')} close=${day_11_close:.2f} -> {day_12_date.strftime('%Y-%m-%d')} open=${day_12_open:.2f}, gap={gap_pct:.2f}% (threshold={min_gap_pct}%)")

        if gap_pct >= min_gap_pct:
            logger.info(f"{symbol}: Gap detected on {day_12_date.strftime('%Y-%m-%d')} - {gap_pct:.2f}%")

            # Apply maximum-gap-percentage filter (if provided)
            if max_gap_pct is not None and gap_pct > max_gap_pct:
                logger.debug(f"{symbol}: Filtered out by maximum-gap-percentage (gap={gap_pct:.2f}% > {max_gap_pct:.2f}%)")
                continue

            # Apply max-open-price filter
            if max_open_price is not None and day_12_open > max_open_price:
                logger.debug(f"{symbol}: Filtered out by max-open-price (open=${day_12_open:.2f} > ${max_open_price:.2f})")
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
                'day_12_open': day_12_open,
                'gap_pct': gap_pct
            }

            gap_events.append(gap_event)

        # Add throttling to avoid pacing violations
        time.sleep(0.05)  # 20 requests/sec conservative

    return gap_events


def calculate_metrics_for_gue(gue, intraday_bars):
    """
    Calculate GAP%, Push%, Low%, MAX, and MIN for each bar in a GUE.
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
    low_percentages = []   # Track all Low% values to find min

    for bar in intraday_bars:
        bar_time = bar['date']
        bar_high = bar['high']
        bar_low = bar['low']
        bar_close = bar['close']

        enriched_bar = bar.copy()

        # Calculate GUSday label FIRST (before metrics calculation)
        gusday_label = calculate_gusday_label(bar_time, day_12_date)

        # Calculate GAP% with two cases:
        # Case 1: Pre-market bars (04:00-09:29) - use bar_high
        # Case 2: 09:30 GUSday bar - use bar_open
        is_premarket = (premarket_start <= bar_time <= premarket_end)
        is_0930_bar = (bar_time.hour == 9 and bar_time.minute == 30)
        is_gusday = (gusday_label == 'GUSday')

        if is_premarket:
            # Existing behavior: pre-market uses bar_high
            gap_pct = ((bar_high - day_11_close) / day_11_close) * 100
            enriched_bar['gap_pct'] = gap_pct
        elif is_0930_bar and is_gusday:
            # New behavior: 09:30 GUSday bar uses bar_open
            bar_open = bar['open']
            gap_pct = ((bar_open - day_11_close) / day_11_close) * 100
            enriched_bar['gap_pct'] = gap_pct
        else:
            enriched_bar['gap_pct'] = None

        # Calculate Push% and Low% based on GUSday label
        # Case 1: GUSday during RTH (existing behavior)
        # Case 2: GUSday+N for all hours (new behavior)

        # Determine if GUSday label is positive (GUSday or GUSday+N)
        is_gusday = (gusday_label == 'GUSday')
        is_positive_future_gusday = gusday_label.startswith('GUSday+')

        # Check if current bar is during RTH hours
        is_rth_hours = (rth_start <= bar_time <= rth_end)

        # Calculate Push% and Low% if:
        # - GUSday AND RTH hours (existing behavior)
        # - GUSday+N for any hour (new behavior)
        should_calculate = (is_gusday and is_rth_hours) or is_positive_future_gusday

        if should_calculate:
            if day_12_open > 0:
                push_pct = ((bar_high - day_12_open) / day_12_open) * 100
                enriched_bar['push_pct'] = push_pct
                push_percentages.append((len(enriched_bars), push_pct))

                low_pct = ((bar_low - day_12_open) / day_12_open) * 100
                enriched_bar['low_pct'] = low_pct
                low_percentages.append((len(enriched_bars), low_pct))
            else:
                enriched_bar['push_pct'] = None
                enriched_bar['low_pct'] = None
        else:
            enriched_bar['push_pct'] = None
            enriched_bar['low_pct'] = None

        enriched_bar['max_marker'] = None  # Will be set later
        enriched_bar['min_marker'] = None  # Will be set later

        # Store GUSday label
        enriched_bar['gusday'] = gusday_label

        enriched_bars.append(enriched_bar)

    # Find index with highest Push%
    if push_percentages:
        max_index, max_push = max(push_percentages, key=lambda x: x[1])
        enriched_bars[max_index]['max_marker'] = 'max'

    # Find index with lowest Low%
    if low_percentages:
        min_index, min_low = min(low_percentages, key=lambda x: x[1])
        enriched_bars[min_index]['min_marker'] = 'min'

    # Deduplicate bars to prevent duplicate rows in output
    # (This is a safety measure - duplicates indicate a bug in fetch logic)
    original_count = len(enriched_bars)
    enriched_bars = deduplicate_bars(enriched_bars)
    if len(enriched_bars) < original_count:
        symbol = gue['symbol']
        logger.warning(f"{symbol}: Deduplication removed {original_count - len(enriched_bars)} duplicate bars")

    return enriched_bars


def write_hguf_table(gap_events_with_data, output_file):
    """
    Write HGUF-table to tab-delimited file.
    gap_events_with_data: list of dicts with keys: gue, enriched_bars, fundamentals
    """
    header = ['Symbol', 'GUE', 'SGUE', 'Time', 'O', 'H', 'L', 'C', 'Volume', 'xVol', 'NetIncomeM$', 'GAP%', 'Push%', 'Low%', 'MAX', 'MIN', 'GUSday', 'Float', 'Institution%']

    # Assign SGUE IDs
    sorted_by_symbol = sorted(gap_events_with_data, key=lambda x: x['gue']['symbol'])
    for symbol, group in groupby(sorted_by_symbol, key=lambda x: x['gue']['symbol']):
        events_for_symbol = sorted(list(group), key=lambda x: x['gue']['gue_id'])
        for i, event in enumerate(events_for_symbol):
            event['gue']['sgue_id'] = i + 1

    with open(output_file, 'w') as f:
        # Write header
        f.write('\t'.join(header) + '\n')

        # Write data rows (sorted by Symbol, then GUE ID)
        sorted_events = sorted(gap_events_with_data, key=lambda x: (x['gue']['symbol'], x['gue']['gue_id']))

        for event in sorted_events:
            gue = event['gue']
            enriched_bars = event['enriched_bars']
            fundamentals = event['fundamentals']

            symbol = gue['symbol']
            gue_id = gue['gue_id']
            sgue_id = gue['sgue_id']
            float_millions = fundamentals.get('float_millions')
            institution_pct = fundamentals.get('institution_pct')

            # Track first GUSday-1 row per GUE
            first_gusday_minus_1_shown = False

            for bar in enriched_bars:
                # Determine xVol and NetIncome display values
                gusday_label = bar['gusday']
                xvol_value = gue.get('xvol')  # Get xVol from GUE record
                net_income_value = gue.get('net_income')  # Get Net Income from GUE record

                # Show xVol and NetIncome ONLY in first row where GUSday == "GUSday-1"
                if gusday_label == 'GUSday-1' and not first_gusday_minus_1_shown:
                    xvol_display = f"{xvol_value:.2f}" if xvol_value is not None else 'NA'
                    net_income_display = f"{net_income_value:.2f}" if net_income_value is not None else 'NA'
                    first_gusday_minus_1_shown = True
                else:
                    xvol_display = 'NA'
                    net_income_display = 'NA'

                row = [
                    symbol,
                    str(gue_id),
                    str(sgue_id),
                    format_datetime_for_output(bar['date']),
                    f"{bar['open']:.4f}",
                    f"{bar['high']:.4f}",
                    f"{bar['low']:.4f}",
                    f"{bar['close']:.4f}",
                    str(bar['volume']),
                    xvol_display,  # xVol column (after Volume, before NetIncome)
                    net_income_display,  # NetIncomeM$ column (after xVol, before GAP%)
                    f"{bar['gap_pct']:.2f}" if bar['gap_pct'] is not None else 'NA',
                    f"{bar['push_pct']:.2f}" if bar['push_pct'] is not None else 'NA',
                    f"{bar['low_pct']:.2f}" if 'low_pct' in bar and bar['low_pct'] is not None else 'NA',
                    bar['max_marker'] if bar['max_marker'] else 'NA',
                    bar.get('min_marker') if bar.get('min_marker') else 'NA',
                    bar['gusday'],
                    f"{float_millions:.2f}" if float_millions else 'NA',
                    f"{institution_pct:.2f}" if institution_pct else 'NA'
                ]
                f.write('\t'.join(row) + '\n')

    logger.info(f"Wrote HGUF-table to: {output_file}")


# ============================================================================
# Phase 1 Parallel Processing Helper
# ============================================================================

def process_single_symbol_phase1(app, symbol, start_date, end_date, min_gap_pct, max_gap_pct, max_open_price):
    """
    Process a single symbol in Phase 1 (fetch daily bars and detect gaps).
    Designed for parallel execution with ThreadPoolExecutor.

    Returns:
        list: Gap events for this symbol, or empty list if none found
    """
    try:
        # Fetch daily bars
        daily_bars = fetch_daily_bars(app, symbol, start_date, end_date)

        if not daily_bars:
            logger.warning(f"{symbol}: No daily bars fetched, skipping gap detection")
            return []

        # Check for OTC (filter out)
        contract = create_contract(symbol)
        yf_data = get_yfinance_fundamentals(symbol)
        if detect_otc(contract, yf_data):
            logger.info(f"{symbol}: OTC stock detected, skipping gap detection")
            return []

        # Detect gap events for this symbol
        gap_events = detect_gap_event_for_symbol(
            app, symbol, daily_bars, start_date, end_date,
            min_gap_pct, max_gap_pct, max_open_price
        )

        return gap_events if gap_events else []

    except Exception as e:
        logger.error(f"{symbol}: Error in Phase 1 processing - {e}")
        return []


# ============================================================================
# Main HGUF Logic
# ============================================================================

def main():
    """Main entry point"""
    global logger
    script_start_time = time.time()  # Record start time

    # Parse arguments
    parser = argparse.ArgumentParser(
        description='Historical Gap Up Finder (HGUF) - Scan IBKR historical database for gap-up events',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage with 10-min GUSday candles, 4-hour non-GUSday candles
  python3 Historical_Gap_Up_Finder.py --time-window "25-11-2025" "10-12-2025" --minimum-gap-percentage 30 --GUSday-candles-size 10 --nonGUSday-candles-size 240 --client-id 700 --fetch-time-pre-gap 24.0 --fetch-time-post-gap 6.5

  # With gap range filter (30-100%) - excludes extreme gaps
  python3 Historical_Gap_Up_Finder.py --time-window "01-11-2025" "30-11-2025" --minimum-gap-percentage 30 --maximum-gap-percentage 100 --GUSday-candles-size 10 --nonGUSday-candles-size 240 --client-id 700 --fetch-time-pre-gap 24.0 --fetch-time-post-gap 6.5

  # With filters and custom candle sizes
  python3 Historical_Gap_Up_Finder.py --time-window "01-11-2025" "30-11-2025" --minimum-gap-percentage 50 --max-open-price 20.0 --max-float 100.0 --max-institution 40.0 --GUSday-candles-size 5 --nonGUSday-candles-size 60 --client-id 700 --fetch-time-pre-gap 48.0 --fetch-time-post-gap 24.0

  # Multi-day window (48h pre-gap, 72h post-gap) with 15-min GUSday, 2-hour non-GUSday
  python3 Historical_Gap_Up_Finder.py --time-window "15-11-2025" "20-11-2025" --minimum-gap-percentage 25 --GUSday-candles-size 15 --nonGUSday-candles-size 120 --client-id 700 --fetch-time-pre-gap 48.0 --fetch-time-post-gap 72.0
        """
    )

    # Required arguments
    parser.add_argument('--time-window', nargs=2, required=True, metavar=('START', 'END'),
                       help='Time window in DD-MM-YYYY format (e.g., "25-11-2025" "10-12-2025")')
    parser.add_argument('--minimum-gap-percentage', type=float, required=True,
                       help='Minimum gap percentage threshold (e.g., 30 for 30%%)')
    parser.add_argument('--maximum-gap-percentage', type=float, required=False, default=None,
                       help='Maximum gap percentage threshold (optional, e.g., 100 for 100%%). Gaps exceeding this will be excluded.')
    parser.add_argument('--GUSday-candles-size', type=int, required=True,
                       help='Candle size in minutes for GUSday market hours (04:00-20:00). Valid: 1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 240')
    parser.add_argument('--nonGUSday-candles-size', type=int, required=True,
                       help='Candle size in minutes for non-GUSday periods (all other times). Valid: 1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 240')
    parser.add_argument('--client-id', type=int, required=True,
                       help='IBKR client ID (use unique ID, e.g., 700)')
    parser.add_argument('--fetch-time-pre-gap', type=float, required=True,
                       help='Hours to fetch before gap time (09:29). Supports decimals (e.g., 6.5 = 6h 30m)')
    parser.add_argument('--fetch-time-post-gap', type=float, required=True,
                       help='Hours to fetch after gap time (09:29). Supports decimals (e.g., 7.5 = 7h 30m)')

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
    parser.add_argument('--no-cache', action='store_true',
                       help='Disable disk-based caching (forces fresh IBKR API requests)')

    args = parser.parse_args()

    # Set up logging IMMEDIATELY (before any validation using logger)
    log_file = setup_logging(args.log, args.verbose)

    # Validate arguments
    if args.minimum_gap_percentage <= 0:
        parser.error("--minimum-gap-percentage must be > 0")

    if args.maximum_gap_percentage is not None:
        if args.maximum_gap_percentage <= 0:
            parser.error("--maximum-gap-percentage must be > 0")
        if args.maximum_gap_percentage <= args.minimum_gap_percentage:
            parser.error("--maximum-gap-percentage must be greater than --minimum-gap-percentage")

    VALID_CANDLE_SIZES = [1, 2, 3, 5, 10, 15, 20, 30, 60, 120, 240]

    if args.GUSday_candles_size not in VALID_CANDLE_SIZES:
        parser.error(f"--GUSday-candles-size must be one of: {VALID_CANDLE_SIZES} (got {args.GUSday_candles_size})")

    if args.nonGUSday_candles_size not in VALID_CANDLE_SIZES:
        parser.error(f"--nonGUSday-candles-size must be one of: {VALID_CANDLE_SIZES} (got {args.nonGUSday_candles_size})")

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

    # Validate time window arguments
    if args.fetch_time_pre_gap < 0:
        parser.error("--fetch-time-pre-gap must be >= 0")

    if args.fetch_time_post_gap < 0:
        parser.error("--fetch-time-post-gap must be >= 0")

    if args.fetch_time_pre_gap == 0 and args.fetch_time_post_gap == 0:
        parser.error("At least one of --fetch-time-pre-gap or --fetch-time-post-gap must be > 0")

    # Parse time window
    start_date = parse_date_dd_mm_yyyy(args.time_window[0])
    end_date = parse_date_dd_mm_yyyy(args.time_window[1])

    if start_date >= end_date:
        parser.error("Start date must be before end date")

    if end_date > datetime.now(EASTERN_TZ):
        parser.error("End date must be in the past")

    # Warn about large time windows
    total_window_hours = args.fetch_time_pre_gap + args.fetch_time_post_gap
    total_market_days = total_window_hours / 24.0
    if total_window_hours > 168:  # 7 market days
        logger.warning(f"Large time window: {total_market_days:.1f} market days total ({args.fetch_time_pre_gap/24:.1f} pre-gap + {args.fetch_time_post_gap/24:.1f} post-gap)")
        logger.warning("This may result in long execution time and many IBKR API requests")

    # Configure caching
    if args.no_cache:
        app_state["use_cache"] = False
        logger.info("Disk caching DISABLED (--no-cache flag)")
    else:
        app_state["use_cache"] = True
        logger.info(f"Disk caching ENABLED (cache_dir: {app_state['cache_dir']})")

    # Display configuration
    console.print("\n[bold cyan]═══════════════════════════════════════════════════[/bold cyan]")
    console.print("[bold cyan]   Historical Gap Up Finder (HGUF)[/bold cyan]")
    console.print("[bold cyan]═══════════════════════════════════════════════════[/bold cyan]\n")

    console.print(f"[bold]Configuration:[/bold]")
    console.print(f"  Time Window: {start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}")
    console.print(f"  Minimum Gap: {args.minimum_gap_percentage}%")
    if args.maximum_gap_percentage is not None:
        console.print(f"  Maximum Gap: {args.maximum_gap_percentage}%")
    console.print(f"  GUSday Candle Size: {args.GUSday_candles_size} minutes ({map_candle_size_to_ibkr_bar_size(args.GUSday_candles_size)})")
    console.print(f"  Non-GUSday Candle Size: {args.nonGUSday_candles_size} minutes ({map_candle_size_to_ibkr_bar_size(args.nonGUSday_candles_size)})")
    console.print(f"  Client ID: {args.client_id}")
    console.print(f"  IB Gateway: {args.host}:{args.port}")
    console.print(f"  Universe File: {args.universe_file}")
    console.print(f"  Output Dir: {args.output_dir}")
    console.print(f"  Log File: {log_file}")
    console.print(f"  Cache: {'DISABLED' if args.no_cache else 'ENABLED'}")

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

    # Store client_id in app_state for error messages
    app_state["client_id"] = args.client_id

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

    # Parallel processing configuration
    BATCH_SIZE = 20  # Process 20 symbols concurrently per batch
    MAX_WORKERS = 20  # Up to 20 parallel workers
    INTER_BATCH_SLEEP = 0.5  # Sleep between batches to avoid pacing violations

    # Split universe into batches
    symbol_batches = [universe_symbols[i:i + BATCH_SIZE] for i in range(0, len(universe_symbols), BATCH_SIZE)]
    total_batches = len(symbol_batches)

    # Track timeout statistics across all batches
    global_stats = {
        "total_symbols": len(universe_symbols),
        "completed": 0,
        "timeouts": 0,
        "errors": 0,
        "otc_filtered": 0
    }

    logger.info(f"Phase 1: Processing {len(universe_symbols)} symbols in {total_batches} batch(es) of {BATCH_SIZE} with {MAX_WORKERS} workers")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:

        task = progress.add_task(f"[cyan]Scanning {len(universe_symbols)} symbols...", total=len(universe_symbols))

        for batch_num, batch in enumerate(symbol_batches, 1):
            logger.info(f"Processing batch {batch_num}/{total_batches} ({len(batch)} symbols)...")

            # Reset batch timeout counter
            app_state["batch_stats"]["current_batch_timeouts"] = 0

            # Process batch in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                # Submit all symbols in batch for parallel processing
                future_to_symbol = {}
                for symbol in batch:
                    future = executor.submit(
                        process_single_symbol_phase1,
                        app, symbol, start_date, end_date,
                        args.minimum_gap_percentage, args.maximum_gap_percentage, args.max_open_price
                    )
                    future_to_symbol[future] = symbol

                # Collect results as they complete
                for future in as_completed(future_to_symbol):
                    symbol = future_to_symbol[future]
                    progress.update(task, description=f"[cyan]Scanning {symbol}...")

                    try:
                        gap_events = future.result()

                        global_stats["completed"] += 1

                        if gap_events:
                            all_gap_events.extend(gap_events)
                            progress.update(task, description=f"[green]{symbol} - Found {len(gap_events)} gap(s)!")
                            logger.info(f"{symbol}: Found {len(gap_events)} gap event(s)")

                    except Exception as e:
                        logger.error(f"{symbol}: Exception in Phase 1 - {e}")
                        global_stats["errors"] += 1

                    progress.advance(task)

            # Log batch summary with timeout statistics
            batch_timeouts = app_state["batch_stats"]["current_batch_timeouts"]
            timeout_rate = batch_timeouts / len(batch) if len(batch) > 0 else 0

            logger.info(f"Batch {batch_num} complete: {len(batch)} symbols processed, "
                       f"{batch_timeouts} timeouts, {global_stats['errors']} total errors")

            # Warn if high timeout rate
            if timeout_rate > 0.3:
                logger.warning(f"Batch {batch_num}: High timeout rate ({timeout_rate:.1%}). "
                              f"Check IBKR connection or reduce batch size.")

            if timeout_rate > 0.8:
                logger.critical(f"Batch {batch_num}: Critical timeout rate ({timeout_rate:.1%}). "
                               f"Most requests are failing. Consider stopping and diagnosing IBKR connectivity.")

            # Inter-batch sleep to avoid overwhelming IBKR API
            if batch_num < total_batches:
                time.sleep(INTER_BATCH_SLEEP)

    # Global statistics summary
    global_timeouts = app_state["batch_stats"]["global_timeouts"]
    logger.info(f"Phase 1 Complete: {global_stats['completed']}/{global_stats['total_symbols']} symbols processed")
    logger.info(f"Total timeouts: {global_timeouts}, errors: {global_stats['errors']}")

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

            # Calculate xVol (volume ratio)
            date_11 = gue['date_11']
            xvol = calculate_volume_ratio(symbol, date_11)

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
            gue['xvol'] = xvol  # Store xVol in GUE record
            gue['net_income'] = fundamentals.get('net_income_millions')  # Store Net Income
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

            # Define time range based on flexible window arguments
            gap_time = date_12.replace(hour=9, minute=29, second=0)  # Checkpoint-B

            # Calculate start_time and end_time using market days logic
            start_time = calculate_market_days_offset(gap_time, args.fetch_time_pre_gap, direction='backward')
            end_time = calculate_market_days_offset(gap_time, args.fetch_time_post_gap, direction='forward')

            # Validate end_time doesn't exceed current time
            current_time = datetime.now(EASTERN_TZ)
            if end_time > current_time:
                logger.warning(f"{symbol} (GUE {gue['gue_id']}): end_time exceeds current time, capping to current time")
                end_time = current_time

            logger.debug(f"{symbol}: Fetching {args.fetch_time_pre_gap}h pre-gap, {args.fetch_time_post_gap}h post-gap")

            # Fetch intraday bars with segmented candle sizes
            intraday_bars = fetch_intraday_bars_segmented(
                app, symbol, start_time, end_time,
                date_12,  # GUSday reference date
                args.GUSday_candles_size,
                args.nonGUSday_candles_size
            )

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

    end_time = time.time()  # Record end time
    elapsed_time = end_time - script_start_time
    minutes = int(elapsed_time // 60)
    seconds = int(elapsed_time % 60)
    run_time_str = f"{minutes}m{seconds}s"
    summary_table.add_row("Run Time", run_time_str)

    # Add cache statistics
    if app_state["use_cache"]:
        total_requests = app_state["cache_stats"]["hits"] + app_state["cache_stats"]["misses"]
        hit_rate = (app_state["cache_stats"]["hits"] / total_requests * 100) if total_requests > 0 else 0
        cache_stats_str = f"{app_state['cache_stats']['hits']}/{total_requests} ({hit_rate:.1f}%)"
        summary_table.add_row("Cache Hit Rate", cache_stats_str)

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
