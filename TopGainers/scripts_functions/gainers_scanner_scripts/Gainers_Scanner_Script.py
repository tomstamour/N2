#!/usr/bin/env python3
"""
Gainers Scanner Script (GSS)
GUS-ATS - Gap Up Short Algorithmic Trading System

This script performs continuous scans to find stocks with X% price increases
from the previous day's close to the current price.

Outputs: CSV file with symbols, prices, gain percentages, and borrow info.
Prints per-cycle status lines and rewrites the GusTradeTable on disk each scan
cycle. For a live view, open the table in a separate process (e.g. VisiData with
`--reload-every=N`) rather than rendering it in-process.

Note: pandas and yfinance are imported lazily inside the functions that use them
to keep startup memory low.
"""

import time
import datetime
import logging
import threading
import os
import sys
import argparse
import subprocess
from pathlib import Path
import pytz
from concurrent.futures import ThreadPoolExecutor, as_completed
import xml.etree.ElementTree as ET
import json
import urllib.request
import urllib.parse
import urllib.error

# Rich library for terminal display (only Console is used)
try:
    from rich.console import Console
except ImportError:
    print("Error: rich module not found. Please install it with: pip install rich")
    exit(1)

# IBAPI imports
try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.tag_value import TagValue
except ImportError:
    print("Error: ibapi module not found. Please install it with: pip install ibapi")
    exit(1)

# NOTE: pandas (`import pandas as pd`) and yfinance (`import yfinance as yf`) are
# imported lazily inside the functions that use them (fetch_pr_base_price,
# get_yfinance_data, previous_day_volume_calculator) to keep startup memory low.
# They pull in heavy transitive deps (numpy/requests/lxml/curl_cffi) that we do
# not want to load on every run.

# =====================
# Command-Line Argument Parsing
# =====================
def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='''Gainers Scanner Script (GSS) - Track stocks crossing gain thresholds from a specific time checkpoint

This script continuously monitors stocks using Interactive Brokers API, tracking those that
cross a specified gain percentage threshold from a user-defined time checkpoint (time0).
Results are saved to a tab-delimited GusTradeTable file with accumulative tracking.
        ''',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
Examples:
  # Time0 mode - intraday checkpoint
  python3 Gainers_Scanner_Script.py --time0 "2025-11-14/09:45" --gap-percentage 50 --client-id 300

  # Date-A mode - daily close price
  python3 Gainers_Scanner_Script.py --date-A "2025-11-26" --gap-percentage 50 --client-id 300

  # Date-A mode with historical analysis
  python3 Gainers_Scanner_Script.py --date-A "2025-11-26" --gap-percentage 30 --client-id 300 --historical-mode

  # With custom log file
  python3 Gainers_Scanner_Script.py --time0 "2025-11-14/09:30" --gap-percentage 30 --client-id 300 --log ./logs/my_scan.log

  # Paper trading with custom output directory
  python3 Gainers_Scanner_Script.py --time0 "2025-11-14/10:00" --gap-percentage 50 --client-id 300 --port 4002 --output-dir ./data

For full documentation, see: GSS_TIME0_README.md
For quick start guide, see: GSS_QUICK_START.md

Note: --time0 format is YYYY-MM-DD/HH:MM in US/Eastern timezone
      --date-A format is YYYY-MM-DD (uses daily close price at 16:00 ET)
      Output file will be auto-generated as GusTradeTable_DDmmmYYYY.txt
      Log file will be auto-generated as ./logs/scanner_YYYYMMDD_HHMMSS.log unless --log is specified
        '''
    )

    # Checkpoint arguments (mutually exclusive: --time0 OR --date-A)
    parser.add_argument('--time0',
                        required=False,
                        default=None,
                        help='Reference time checkpoint in US/Eastern (format: YYYY-MM-DD/HH:MM, e.g., "2025-11-14/09:45"). Mutually exclusive with --date-A.')
    parser.add_argument('--date-A',
                        required=False,
                        default=None,
                        help='Reference date checkpoint (format: YYYY-MM-DD, e.g., "2025-11-26"). Uses daily close price. Mutually exclusive with --time0.')
    parser.add_argument('--gap-percentage',
                        type=float,
                        required=True,
                        help='Minimum gain percentage threshold from time0 (e.g., 50 for 50%%)')
    parser.add_argument('--client-id',
                        type=int,
                        required=True,
                        help='IB Gateway client ID - must be unique per connection (e.g., 300)')

    # Optional arguments
    parser.add_argument('--output-dir',
                        default='.',
                        help='Directory for GusTradeTable output file (default: current directory)')
    parser.add_argument('--host',
                        default='127.0.0.1',
                        help='IB Gateway host (default: 127.0.0.1)')
    parser.add_argument('--port',
                        type=int,
                        default=4001,
                        help='IB Gateway port (default: 4001)')
    parser.add_argument('--log',
                        default=None,
                        help='Log file path - includes command line and all output (default: auto-generated as ./logs/scanner_YYYYMMDD_HHMMSS.log)')
    parser.add_argument('--config',
                        default=str(Path(__file__).resolve().parents[3] / 'config' / 'n2_config_file.txt'),
                        help='N2 config file holding the RTPR.io API key (default: <repo>/scripts/config/n2_config_file.txt). Used for the LastPR/Headline columns.')
    parser.add_argument('--no-rtpr',
                        action='store_true',
                        help='Disable RTPR.io latest-PR enrichment (LastPR/Headline columns will show "-").')
    parser.add_argument('--max-stocks',
                        type=int,
                        default=50,
                        help='Maximum stocks to scan (default: 50)')
    parser.add_argument('--min-price',
                        type=float,
                        default=0.50,
                        help='Minimum stock price filter (default: 0.50)')
    parser.add_argument('--refresh-interval',
                        type=int,
                        default=30,
                        help='Seconds between scans and disk rewrites of the '
                             'GusTradeTable (default: 30). Pair with VisiData '
                             '`--reload-every` for a live external view.')
    parser.add_argument('--no-new-window',
                        action='store_true',
                        help='Display in current terminal instead of new window')
    parser.add_argument('--historical-mode',
                        action='store_true',
                        help='Historical mode: use only historical data (no live prices required) - ideal for weekend/after-hours analysis')

    # Trade Decision (TD) Arguments - All 5 required together
    parser.add_argument('--gap_larger_then',
                        type=float,
                        default=None,
                        help='Trade decision: GapL(%%) must be LARGER than this value (e.g., 15 for 15%%). Required with other TD args.')
    parser.add_argument('--institution_smaller_then',
                        type=float,
                        default=None,
                        help='Trade decision: Instit(%%) must be SMALLER than this value (e.g., 40 for 40%%). Required with other TD args.')
    parser.add_argument('--float_smaller_then',
                        type=float,
                        default=None,
                        help='Trade decision: Float(M$) must be SMALLER than this value (e.g., 50 for 50M). Required with other TD args.')
    parser.add_argument('--price_smaller_then',
                        type=float,
                        default=None,
                        help='Trade decision: CP-B($) must be SMALLER than this value (e.g., 10 for $10). Required with other TD args.')
    parser.add_argument('--borrow_fee_smaller_then',
                        type=float,
                        default=None,
                        help='Trade decision: BF_BEST must be SMALLER than this value (e.g., 5 for 5%%). Required with other TD args.')
    parser.add_argument('--xVolume-smaller-then',
                        type=float,
                        default=None,
                        help='Trade decision: xVol must be SMALLER than this value (e.g., 3.0 for 3x average volume). Required with other TD args.')

    # OTC Filtering argument
    parser.add_argument('--discard-otc',
                        type=str,
                        choices=['YES', 'NO'],
                        default='NO',
                        help='Discard OTC stocks from trade decisions (YES/NO). Default: NO')

    # Kelly Criterion Position Calculator arguments
    parser.add_argument('--cash-to-play',
                        type=float,
                        default=None,
                        help='Total cash available for Kelly position calculations (optional). '
                             'If not provided, Kelly columns will show N/A.')
    parser.add_argument('--max-tradeCash-proportion-per-stock',
                        type=float,
                        default=0.33,
                        help='Maximum proportion of total cash per stock (0.0 to 1.0). '
                             'Default: 0.33 (33%% concentration limit).')
    parser.add_argument('--account',
                        type=str,
                        help='IBKR account ID for WhatIf checks (optional - auto-detected if not specified)')

    args = parser.parse_args()

    # Validate Trade Decision arguments - all 6 must be specified together
    td_args = [
        args.gap_larger_then,
        args.institution_smaller_then,
        args.float_smaller_then,
        args.price_smaller_then,
        args.borrow_fee_smaller_then,
        args.xVolume_smaller_then
    ]
    td_args_specified = [arg is not None for arg in td_args]

    if any(td_args_specified) and not all(td_args_specified):
        parser.error("Trade Decision feature requires ALL 6 arguments:\n"
                    "  --gap_larger_then\n"
                    "  --institution_smaller_then\n"
                    "  --float_smaller_then\n"
                    "  --price_smaller_then\n"
                    "  --borrow_fee_smaller_then\n"
                    "  --xVolume-smaller-then\n"
                    "Either specify all 6 or none.")

    # Validate Kelly Calculator arguments
    if args.max_tradeCash_proportion_per_stock is not None:
        if not (0.0 <= args.max_tradeCash_proportion_per_stock <= 1.0):
            parser.error("--max-tradeCash-proportion-per-stock must be between 0.0 and 1.0")

    if args.cash_to_play is not None:
        if args.cash_to_play < 0:
            parser.error("--cash-to-play must be a positive number")

    # Validate --time0 and --date-A mutual exclusion
    if args.time0 is None and args.date_A is None:
        parser.error("Either --time0 or --date-A must be specified (not both, not neither)")

    if args.time0 is not None and args.date_A is not None:
        parser.error("--time0 and --date-A are mutually exclusive. Specify only one.")

    # Store which mode we're using for later reference
    args.checkpoint_mode = 'time0' if args.time0 is not None else 'date-A'

    return args

# =====================
# Utility Functions for Time0 and GTT
# =====================
def parse_time0(time0_str):
    """
    Parse and validate the time0 argument.

    Args:
        time0_str: String in format 'YYYY-MM-DD/HH:MM'

    Returns:
        datetime object in US/Eastern timezone, or None if invalid
    """
    try:
        # Parse the string
        dt_naive = datetime.datetime.strptime(time0_str, '%Y-%m-%d/%H:%M')

        # Localize to US/Eastern timezone
        dt_et = ET_TZ.localize(dt_naive)

        # Check if it's in the future
        now_et = datetime.datetime.now(ET_TZ)
        if dt_et > now_et:
            logger.error(f"time0 cannot be in the future: {time0_str}")
            return None

        logger.info(f"time0 parsed: {dt_et.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        return dt_et

    except ValueError as e:
        logger.error(f"Invalid time0 format '{time0_str}'. Expected: YYYY-MM-DD/HH:MM (e.g., '2025-11-14/09:45')")
        logger.error(f"Parse error: {e}")
        return None

def parse_date_a(date_str):
    """
    Parse and validate the date-A argument.

    Args:
        date_str: String in format 'YYYY-MM-DD'

    Returns:
        datetime object at 16:00 US/Eastern (market close), or None if invalid
    """
    try:
        # Parse the date string (YYYY-MM-DD)
        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()

        # Create datetime at market close (16:00 ET)
        dt_naive = datetime.datetime.combine(date_obj, datetime.time(16, 0))

        # Localize to US/Eastern timezone
        dt_et = ET_TZ.localize(dt_naive)

        # Check if it's in the future
        now_et = datetime.datetime.now(ET_TZ)
        if dt_et > now_et:
            logger.error(f"date-A cannot be in the future: {date_str}")
            return None

        logger.info(f"date-A parsed: {dt_et.strftime('%Y-%m-%d')} (using market close 16:00 ET)")
        return dt_et

    except ValueError as e:
        logger.error(f"Invalid date-A format '{date_str}'. Expected: YYYY-MM-DD (e.g., '2025-11-26')")
        logger.error(f"Parse error: {e}")
        return None

def generate_gtt_filename(output_dir):
    """
    Generate the GusTradeTable filename with current date.

    Args:
        output_dir: Directory where GTT file should be saved

    Returns:
        Full path to GTT file (e.g., './data/GusTradeTable_18nov2025.txt')
    """
    # Get current date
    current_date = datetime.datetime.now()

    # Format as DDmmmYYYY (e.g., 18nov2025)
    date_str = current_date.strftime('%d%b%Y').lower()

    # Create filename
    #filename = f"GusTradeTable_{date_str}.txt" # Conmmented out since we wanted a generic name without date in the file name.
    filename = "GusTradeTable.txt"


    # Create full path
    output_path = Path(output_dir) / filename

    return output_path

def format_time_for_gtt(dt, date_only=False):
    """
    Format datetime for GTT output.

    Args:
        dt: datetime object
        date_only: If True, return 'YYYY-MM-DD'. If False, return 'YYYY-MM-DD/HH:MM'

    Returns:
        String in format 'YYYY-MM-DD' or 'YYYY-MM-DD/HH:MM'
    """
    if dt.tzinfo:
        # Convert to ET if not already
        dt_et = dt.astimezone(ET_TZ)
    else:
        dt_et = dt

    if date_only:
        return dt_et.strftime('%Y-%m-%d')
    else:
        return dt_et.strftime('%Y-%m-%d/%H:%M')

def parse_fundamental_metrics(xml_data: str) -> dict:
    """
    Parse IBKR fundamental XML data for specific metrics needed for shorting analysis.

    NOTE: Float, Net Income TTM, and Institutional % are now fetched from yfinance.
    This function only extracts:
    1. Borrow Fee Rate (borrow_fee_rate) - Cost to borrow shares for shorting

    Args:
        xml_data: XML response string from IBKR fundamental data request

    Returns:
        Dict with parsed metrics (None if not found)
    """
    metrics = {
        'borrow_fee_rate': None
    }

    try:
        root = ET.fromstring(xml_data)

        # NOTE: Float, Net Income TTM, and Institutional % are now fetched from yfinance
        # Only extracting Borrow Fee Rate from IBKR

        # Borrow Fee Rate - may not be in fundamental data (sometimes in market data)
        for path in ['.//BorrowFeeRate', './/ShortBorrowFee', './/BorrowRate']:
            elem = root.find(path)
            if elem is not None and elem.text:
                try:
                    metrics['borrow_fee_rate'] = float(elem.text)
                    logger.debug(f"Found Borrow Fee Rate via {path}: {metrics['borrow_fee_rate']}")
                    break
                except ValueError:
                    pass

    except ET.ParseError as e:
        logger.error(f"Error parsing fundamental XML: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in parse_fundamental_metrics: {e}")

    return metrics

# =====================
# Setup Logging
# =====================
def setup_logging(log_file_path=None):
    """
    Setup logging configuration.

    Args:
        log_file_path: Full path to log file. If None, auto-generates in ./logs/

    Returns:
        Tuple of (logger, log_file_path)
    """
    if not log_file_path:
        # Auto-generate default log file
        log_dir = Path('logs')
        log_dir.mkdir(exist_ok=True, parents=True)
        log_file_path = log_dir / f"scanner_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    else:
        # Use user-specified path
        log_path = Path(log_file_path)
        # Create parent directories if they don't exist
        log_path.parent.mkdir(exist_ok=True, parents=True)
        log_file_path = log_path

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(log_file_path)
        ]
    )

    return logging.getLogger(__name__), log_file_path

# Logger will be initialized after parsing args
logger = None

# =====================
# Global State
# =====================
app_state = {
    "scanner_results": [],
    "scanner_complete": threading.Event(),
    "historical_data": {},
    "current_prices": {},
    "borrow_info": {},
    "contracts": {},
    "connection_ready": threading.Event(),
    "connection_lost": threading.Event(),  # NEW: Set when connection is lost (recoverable)
    "shutdown": threading.Event(),
    "reconnection_in_progress": False,  # NEW: Flag to prevent concurrent reconnections
    "results_table": [],  # Current results for display
    "last_update": None,
    "tracked_stocks": {},  # Dict of stocks that crossed threshold: {symbol: {checkpoint data}}
    "time0_datetime": None,  # The reference time checkpoint (Checkpoint-A)
    "time0_prices": {},  # Cache of prices at time0: {symbol: price}
    "gtt_file_path": None,  # Path to GusTradeTable file
    "fundamental_cache": {},  # Cache fundamental data: {symbol: {metrics, timestamp}}
    "fundamental_cache_ttl": 3600,  # Cache TTL in seconds (1 hour)
    "peak_prices": {},  # Cache of peak prices in window: {symbol: {peak_price, peak_time}}
    "yfinance_cache": {},  # Cache yfinance data: {symbol: {data, timestamp}}
    "yfinance_cache_ttl": 3600,  # Cache TTL in seconds (1 hour)
    "pdv_cache": {},  # Cache PDV (Previous Day Volume) data: {symbol: {data, timestamp}}
    "pdv_cache_ttl": 86400,  # Cache TTL in seconds (24 hours - daily volume data)
    "market_data_status": None,  # Latest market data farm connection status
    "scanner_timeout_count": 0,  # Track consecutive scanner timeouts
    "current_refresh_interval": None,  # Dynamic refresh interval with backoff
    "use_date_a_mode": False,  # True if using --date-A, False if using --time0
    # WhatIf margin check tracking
    "margin_checks": {},  # Track active WhatIf margin eligibility checks: {order_id: {symbol, event, result, margin_data}}
    "margin_check_lock": threading.Lock(),  # Thread-safe access to margin_checks
    "next_whatif_order_id": 90000,  # Counter for WhatIf order IDs (will be updated by nextValidId callback)
    # Account management
    "managed_accounts": [],  # List of account IDs from managedAccounts callback
    "selected_account": None,  # Account ID to use for orders (from --account arg or auto-selected)
    "account_ready": threading.Event(),  # Set when managedAccounts callback received
}

# Timezone setup
ET_TZ = pytz.timezone('US/Eastern')
UTC_TZ = pytz.UTC

# =====================
# IBAPI Wrapper
# =====================
class ScannerWrapper(EWrapper):
    def __init__(self):
        EWrapper.__init__(self)
        self.next_order_id = None
        self.pending_historical_requests = set()
        self.pending_market_data_requests = set()
        self.pending_fundamental_requests = set()
        self.historical_data_lock = threading.Lock()
        self.market_data_lock = threading.Lock()
        self.fundamental_data_lock = threading.Lock()
        self.fundamental_data = {}

    def error(self, reqId, errorCode, errorString):
        """Handle error messages from IB."""
        # Ignore informational messages
        # 2104, 2106, 2158: Market data farm connection status
        # 2107, 2119, 2176: HMDS (Historical Market Data Service) status
        # 162: Scanner subscription cancelled (expected after cancelScannerSubscription)
        if errorCode in [2104, 2106, 2158, 2107, 2119, 2176, 162]:
            # Log market data connection status for diagnostics
            if errorCode in [2104, 2106, 2158]:
                app_state["market_data_status"] = errorString
                logger.info(f"Market Data Status (Code {errorCode}): {errorString}")
            else:
                logger.debug(f"Info (Code {errorCode}): {errorString}")
            return

        logger.error(f"Error - ReqId: {reqId}, Code: {errorCode}, Msg: {errorString}")

        # Handle recoverable connection errors (set connection_lost instead of shutdown)
        if errorCode in [1100, 1101, 1102]:
            logger.warning(f"Recoverable connection error (Code {errorCode}). Will attempt reconnection.")
            app_state["connection_lost"].set()
            return

        # Handle WhatIf order errors (margin eligibility checks)
        with app_state["margin_check_lock"]:
            if reqId in app_state["margin_checks"]:
                margin_check = app_state["margin_checks"][reqId]
                symbol = margin_check['symbol']

                # Specific error codes indicating not shortable:
                # 10189: Failed to request live updates (not shortable)
                # 201: Order rejected (various reasons, assume not shortable)
                # 354: Short sale not allowed
                # 10147: OrderId that needs to be cancelled is not found
                # 435: Must specify account (missing account in order)
                if errorCode == 435:
                    logger.error(f"{symbol}: WhatIf check failed - Account not specified (Error 435)")
                    logger.error(f"Available accounts: {app_state.get('managed_accounts', 'None')}")
                    logger.error(f"Selected account: {app_state.get('selected_account', 'None')}")
                    logger.error(f"Solution: Use --account argument to specify account ID")
                    margin_check['result'] = 'N/A'
                elif errorCode in [10189, 201, 354, 10147]:
                    logger.warning(f"{symbol}: WhatIf check indicates NOT SHORTABLE (Error {errorCode}: {errorString})")
                    margin_check['result'] = 'NO'
                else:
                    logger.error(f"{symbol}: WhatIf check error (Code {errorCode}: {errorString}) - marking N/A")
                    margin_check['result'] = 'N/A'

                margin_check['margin_data'] = {
                    'initMarginAfter': None,
                    'maintMarginAfter': None,
                    'equityWithLoanAfter': None,
                    'error_code': errorCode,
                    'error_msg': errorString
                }
                margin_check['event'].set()
                return  # WhatIf error handled, don't process as fatal error

        # Handle fatal connection errors (invalid config, etc.)
        if errorCode in [504, 1300]:
            logger.critical("Fatal connection error (invalid configuration). Shutting down.")
            app_state["shutdown"].set()

    def nextValidId(self, orderId: int):
        """Callback when connection is established."""
        super().nextValidId(orderId)
        self.next_order_id = orderId
        # Use nextValidId as starting point for WhatIf order IDs to avoid duplicates
        app_state["next_whatif_order_id"] = orderId + 50000  # Offset by 50000 for separation
        logger.info(f"Connected to IB. Next valid order ID: {orderId}")
        logger.info(f"WhatIf order IDs will start at: {orderId + 50000}")
        app_state["connection_ready"].set()
        # Clear connection_lost flag on successful connection
        app_state["connection_lost"].clear()

    def managedAccounts(self, accountsList: str):
        """
        Callback when managed accounts list is received from IBKR.
        Called automatically during connection handshake.

        Args:
            accountsList (str): Comma-separated list of account IDs (e.g., "DU1234567,DU7654321")
        """
        accounts = accountsList.split(',')
        app_state["managed_accounts"] = accounts

        # Use first account as default if not explicitly specified
        if not app_state.get("selected_account") and accounts:
            app_state["selected_account"] = accounts[0]
            logger.info(f"Managed accounts: {accountsList}")
            logger.info(f"Using default account: {accounts[0]}")

        # Set account_ready event for synchronization
        app_state["account_ready"].set()

    def connectionClosed(self):
        """Callback when connection is closed."""
        super().connectionClosed()
        logger.info("ANSWER connectionClosed {}")
        # Only set connection_lost if shutdown wasn't explicitly requested
        if not app_state["shutdown"].is_set():
            logger.warning("Connection closed unexpectedly. Marking for reconnection.")
            app_state["connection_lost"].set()

    def scannerData(self, reqId: int, rank: int, contractDetails, distance: str,
                    benchmark: str, projection: str, legsStr: str):
        """Callback for scanner results."""
        super().scannerData(reqId, rank, contractDetails, distance, benchmark, projection, legsStr)

        contract = contractDetails.contract
        logger.info(f"Scanner result #{rank}: {contract.symbol} ({contract.primaryExchange})")

        app_state["scanner_results"].append({
            'rank': rank,
            'symbol': contract.symbol,
            'contract': contract,
            'exchange': contract.primaryExchange or contract.exchange
        })
        app_state["contracts"][contract.symbol] = contract

    def scannerDataEnd(self, reqId: int):
        """Callback when scanner data is complete."""
        super().scannerDataEnd(reqId)
        logger.info(f"Scanner data complete. Found {len(app_state['scanner_results'])} stocks.")
        app_state["scanner_complete"].set()

    def historicalData(self, reqId: int, bar):
        """Callback for historical data bars."""
        super().historicalData(reqId, bar)

        # Extract symbol from reqId (we'll encode it as: base_id + symbol_hash)
        symbol = None
        for sym, rid in getattr(self, 'hist_request_map', {}).items():
            if rid == reqId:
                symbol = sym
                break

        if symbol and bar.date:
            try:
                # Parse bar date
                if "  " in bar.date:
                    dt_obj = datetime.datetime.strptime(bar.date, "%Y%m%d  %H:%M:%S")
                else:
                    dt_obj = datetime.datetime.fromtimestamp(int(bar.date))

                # Store the 4:00 AM bar
                with self.historical_data_lock:
                    if symbol not in app_state["historical_data"]:
                        app_state["historical_data"][symbol] = []

                    app_state["historical_data"][symbol].append({
                        'date': dt_obj,
                        'open': bar.open,
                        'high': bar.high,
                        'low': bar.low,
                        'close': bar.close,
                        'volume': bar.volume
                    })
                    logger.debug(f"{symbol}: Historical bar at {dt_obj} - O:{bar.open} H:{bar.high} L:{bar.low} C:{bar.close}")

            except Exception as e:
                logger.error(f"Error parsing historical data for {symbol}: {e}")

    def historicalDataEnd(self, reqId: int, start: str, end: str):
        """Callback when historical data request is complete."""
        super().historicalDataEnd(reqId, start, end)

        with self.historical_data_lock:
            if reqId in self.pending_historical_requests:
                self.pending_historical_requests.remove(reqId)

        symbol = None
        for sym, rid in getattr(self, 'hist_request_map', {}).items():
            if rid == reqId:
                symbol = sym
                break

        if symbol:
            bar_count = len(app_state["historical_data"].get(symbol, []))
            logger.debug(f"Historical data complete for {symbol}: {bar_count} bars")

    def tickPrice(self, reqId: int, tickType: int, price: float, attrib):
        """Callback for market data price ticks."""
        super().tickPrice(reqId, tickType, price, attrib)

        # TickType 4 = LAST price, 1 = BID, 2 = ASK
        if tickType in [4, 1, 2]:
            symbol = None
            for sym, rid in getattr(self, 'mkt_request_map', {}).items():
                if rid == reqId:
                    symbol = sym
                    break

            if symbol and price > 0:
                with self.market_data_lock:
                    if symbol not in app_state["current_prices"]:
                        app_state["current_prices"][symbol] = {}

                    if tickType == 4:
                        app_state["current_prices"][symbol]['last'] = price
                        logger.debug(f"{symbol}: Current price = ${price:.2f}")
                    elif tickType == 1:
                        app_state["current_prices"][symbol]['bid'] = price
                    elif tickType == 2:
                        app_state["current_prices"][symbol]['ask'] = price

    def tickGeneric(self, reqId: int, tickType: int, value: float):
        """Callback for generic tick data (including shortable shares and borrow fee rate)."""
        super().tickGeneric(reqId, tickType, value)

        # Get symbol from request ID
        symbol = None
        for sym, rid in getattr(self, 'mkt_request_map', {}).items():
            if rid == reqId:
                symbol = sym
                break

        if symbol:
            with self.market_data_lock:
                if symbol not in app_state["borrow_info"]:
                    app_state["borrow_info"][symbol] = {}

                # TickType 46 = Shortable shares (requested via generic tick 236)
                if tickType == 46:
                    app_state["borrow_info"][symbol]['shortable'] = value
                    # Enhanced logging for Bug #3 investigation
                    logger.info(f"{symbol}: Tick 46 (shortable difficulty) = {value}")

                # TickType 499 = Borrow fee rate (if available)
                elif tickType == 499:
                    app_state["borrow_info"][symbol]['borrow_fee_rate'] = value
                    logger.info(f"{symbol}: Borrow fee rate from tick 499 = {value}%")

    def tickSize(self, reqId: int, tickType: int, size: int):
        """Callback for size tick data (including exact shortable shares count)."""
        super().tickSize(reqId, tickType, size)

        # Get symbol from request ID
        symbol = None
        for sym, rid in getattr(self, 'mkt_request_map', {}).items():
            if rid == reqId:
                symbol = sym
                break

        if symbol:
            with self.market_data_lock:
                if symbol not in app_state["borrow_info"]:
                    app_state["borrow_info"][symbol] = {}

                # TickType 89 = Shortable shares count (exact number of shares available)
                if tickType == 89:
                    app_state["borrow_info"][symbol]['shares_count'] = size
                    logger.debug(f"{symbol}: Shortable shares count (tick 89) = {size:,}")

    def tickString(self, reqId: int, tickType: int, value: str):
        """Callback for string tick data."""
        super().tickString(reqId, tickType, value)

        # TickType 48 = RTVolume (can contain last trade info)
        symbol = None
        for sym, rid in getattr(self, 'mkt_request_map', {}).items():
            if rid == reqId:
                symbol = sym
                break

        if symbol and tickType == 48:
            logger.debug(f"{symbol}: RTVolume = {value}")

    def tickSnapshotEnd(self, reqId: int):
        """Callback when snapshot is complete."""
        super().tickSnapshotEnd(reqId)

        with self.market_data_lock:
            if reqId in self.pending_market_data_requests:
                self.pending_market_data_requests.remove(reqId)

        symbol = None
        for sym, rid in getattr(self, 'mkt_request_map', {}).items():
            if rid == reqId:
                symbol = sym
                break

        if symbol:
            logger.debug(f"Snapshot complete for {symbol}")

    def openOrder(self, orderId: int, contract, order, orderState):
        """
        Handle WhatIf order preview responses.

        This callback receives OrderState data containing margin requirements
        for WhatIf order previews. If valid margin values are returned,
        the stock is shortable for the account. If invalid/negative margins,
        the stock is not shortable.

        Args:
            orderId: Order ID (90000+ for WhatIf orders)
            contract: IBKR Contract object
            order: IBKR Order object
            orderState: OrderState with margin data (initMarginAfter, maintMarginAfter, etc.)
        """
        super().openOrder(orderId, contract, order, orderState)

        # Check if this is a WhatIf order we're tracking
        with app_state["margin_check_lock"]:
            if orderId not in app_state["margin_checks"]:
                return  # Not a WhatIf order we're tracking

            margin_check = app_state["margin_checks"][orderId]
            symbol = margin_check['symbol']

        # Extract margin data from OrderState
        try:
            init_margin = float(orderState.initMarginAfter) if orderState.initMarginAfter else None
            maint_margin = float(orderState.maintMarginAfter) if orderState.maintMarginAfter else None
            equity_with_loan = float(orderState.equityWithLoanAfter) if orderState.equityWithLoanAfter else None

            # Determine if shortable based on valid margin values
            # Valid margin = positive numeric values returned
            if init_margin and init_margin > 0 and maint_margin and maint_margin > 0:
                result = 'YES'
                logger.info(f"{symbol}: WhatIf check PASSED - InitMargin: ${init_margin:,.2f}, MaintMargin: ${maint_margin:,.2f}")
            else:
                result = 'NO'
                logger.warning(f"{symbol}: WhatIf check FAILED - Invalid margin values (InitMargin: {init_margin}, MaintMargin: {maint_margin})")

            # Store result
            with app_state["margin_check_lock"]:
                margin_check['result'] = result
                margin_check['margin_data'] = {
                    'initMarginAfter': init_margin,
                    'maintMarginAfter': maint_margin,
                    'equityWithLoanAfter': equity_with_loan,
                    'error_code': None,
                    'error_msg': None
                }
                margin_check['event'].set()  # Signal completion

        except Exception as e:
            logger.error(f"{symbol}: Error processing WhatIf response: {e}")
            with app_state["margin_check_lock"]:
                margin_check['result'] = 'N/A'
                margin_check['margin_data'] = {'error_code': None, 'error_msg': str(e)}
                margin_check['event'].set()

    def orderStatus(self, orderId: int, status: str, filled: float, remaining: float,
                    avgFillPrice: float, permId: int, parentId: int, lastFillPrice: float,
                    clientId: int, whyHeld: str, mktCapPrice: float):
        """
        Handle order status updates.

        WhatIf orders typically don't trigger this callback, but we log it
        for debugging if they do.

        Args:
            orderId: Order ID
            status: Order status (PendingSubmit, Submitted, Filled, Cancelled, etc.)
            filled: Filled quantity
            remaining: Remaining quantity
            avgFillPrice: Average fill price
            permId: Permanent order ID
            parentId: Parent order ID
            lastFillPrice: Last fill price
            clientId: Client ID
            whyHeld: Why order is held
            mktCapPrice: Market cap price
        """
        super().orderStatus(orderId, status, filled, remaining, avgFillPrice, permId,
                           parentId, lastFillPrice, clientId, whyHeld, mktCapPrice)

        # WhatIf orders don't typically trigger orderStatus, but log if they do
        with app_state["margin_check_lock"]:
            if orderId in app_state["margin_checks"]:
                symbol = app_state["margin_checks"][orderId]['symbol']
                logger.debug(f"{symbol}: WhatIf order {orderId} status: {status}")

    def fundamentalData(self, reqId: int, data: str):
        """Callback when fundamental data is received from IBKR."""
        super().fundamentalData(reqId, data)
        logger.debug(f"Received fundamental data for request {reqId} ({len(data)} bytes)")

        with self.fundamental_data_lock:
            self.fundamental_data[reqId] = data
            if reqId in self.pending_fundamental_requests:
                self.pending_fundamental_requests.remove(reqId)

# =====================
# IBAPI Client
# =====================
class ScannerClient(EClient):
    def __init__(self, wrapper):
        EClient.__init__(self, wrapper)

# =====================
# GTT File Operations
# =====================
def create_gtt_header(gtt_path):
    """Create the header row for the GusTradeTable file (legacy function - not actively used)."""
    header = [
        'Symbol',
        'CheckPoint-A TIME',
        'CheckPoint-A PRICE',
        'CheckPoint-B TIME',
        'CheckPoint-B PRICE',
        'Gain_Percent',
        'live_percentage',
        'Shortable_Status',  # IBKR-style label
        'Shortable_Difficulty',  # tick 46 raw value
        'Shortable_Shares',  # tick 89 exact count
        'Last_Update'
    ]

    with open(gtt_path, 'w') as f:
        f.write('\t'.join(header) + '\n')

    logger.info(f"Created GTT file with header: {gtt_path}")

# =====================
# RTPR.io REST API — "latest PR" enrichment (LastPR + Headline columns)
# =====================
# The scanner fetches each symbol's most recent press release from RTPR's REST
# API (GET https://api.rtpr.io/articles/{ticker}). Two columns are derived and
# placed right after Symbol:
#   * LastPR  — time since the latest PR, formatted "hh:mm" (or "Nd hh:mm" past
#               24h). Shown only if the PR is within RTPR_PR_MAX_AGE_DAYS; else "-".
#   * Headline — the PR title (tabs/newlines stripped so the TSV stays intact).
# Fetch policy: ONCE per symbol, the first time it is seen (cached on the stock
# dict via 'pr_fetched'); the LastPR clock keeps ticking every write from the
# cached publish time without another API call.
RTPR_API_URL = "https://api.rtpr.io/articles/{ticker}?limit=1"
RTPR_PR_MAX_AGE_DAYS = 7          # only surface PRs newer than this; else "-"
RTPR_FETCH_TIMEOUT = 8.0         # per-request HTTP timeout (seconds)
RTPR_FETCH_WORKERS = 10          # concurrent fetches for newly-seen symbols
_rtpr_api_key = None             # set in main() from --config; None disables the feature

# Concurrency for the per-batch stock-processing pools. Kept modest to limit
# thread stacks and glibc malloc arenas (lower virtual-memory footprint).
SCAN_MAX_WORKERS = 8


def load_rtpr_api_key(config_path):
    """Read the RTPR API key from the N2 config file.

    The config uses a LABEL line ('RTPR_API_KEY:') followed by its value on the
    next line; the value line starts with 'rtpr_'. We simply return the first
    'rtpr_...' token found (matching how the RTPR connectors load it).
    """
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith('#'):
                    continue
                if s.startswith('rtpr_'):
                    return s.split()[0]  # drop any trailing inline comment
    except Exception as e:
        logger.warning(f"Could not read RTPR API key from {config_path}: {e}")
    return None


def _parse_iso_utc(s):
    """Parse RTPR's 'created' timestamp (ISO-8601, e.g. '2026-07-05T01:10:00.260Z')
    into a timezone-aware UTC datetime. Returns None if unparseable."""
    if not s:
        return None
    try:
        s = s.strip()
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'  # fromisoformat() (pre-3.11) doesn't accept 'Z'
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def fetch_latest_pr(ticker, timeout=RTPR_FETCH_TIMEOUT):
    """Fetch the single most-recent PR for `ticker` from RTPR's REST API.

    Returns {'published_at': <aware UTC datetime>, 'headline': <str>} on success,
    or None if there is no PR, the key is missing, or the request fails.
    """
    if not _rtpr_api_key:
        return None
    url = RTPR_API_URL.format(ticker=urllib.parse.quote(str(ticker).strip()))
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {_rtpr_api_key}",   # REST uses Bearer (not X-API-Key)
        "Accept": "application/json",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except Exception as e:
        logger.debug(f"RTPR fetch failed for {ticker}: {type(e).__name__}: {e}")
        return None

    articles = data.get('articles') or []
    if not articles:
        return None
    art = articles[0]
    published_at = _parse_iso_utc(art.get('created'))
    if published_at is None:
        return None
    return {'published_at': published_at, 'headline': art.get('title') or ''}


def fetch_pr_base_price(ticker, published_at):
    """Return the stock's price at (or just before) the PR's Created timestamp.

    Uses yfinance 1-minute intraday data (extended hours included) — enough to
    cover the RTPR_PR_MAX_AGE_DAYS window. Returns the Open of the 1-minute bar
    containing `published_at`, or None if the data is unavailable. This is the
    denominator of the PRyield return.
    """
    import yfinance as yf
    import pandas as pd
    try:
        hist = yf.Ticker(str(ticker).strip()).history(
            period='7d', interval='1m', prepost=True
        )
        if hist is None or hist.empty:
            return None

        ts = pd.Timestamp(published_at)  # tz-aware UTC
        if hist.index.tz is not None:
            ts = ts.tz_convert(hist.index.tz)
        else:
            ts = ts.tz_convert('UTC').tz_localize(None)

        # asof() returns the last index label <= ts (the minute the PR dropped)
        label = hist.index.asof(ts)
        if label is None or pd.isna(label):
            return None

        price = float(hist.loc[label, 'Open'])
        if price != price or price <= 0:  # NaN / nonpositive guard
            return None
        return price
    except Exception as e:
        logger.debug(f"PR base-price fetch failed for {ticker}: {type(e).__name__}: {e}")
        return None


def enrich_last_pr(tracked_stocks):
    """Populate 'pr_published_at' / 'pr_headline' / 'pr_base_price' for any symbol not yet fetched.

    Fetches each symbol's latest PR exactly once (guarded by the 'pr_fetched'
    flag), concurrently for newly-seen symbols. If the feature is disabled (no
    API key) every pending symbol is marked fetched with empty PR data so the
    LastPR/Headline columns simply render "-".
    """
    if not _rtpr_api_key:
        for data in tracked_stocks.values():
            if not data.get('pr_fetched'):
                data['pr_published_at'] = None
                data['pr_headline'] = None
                data['pr_base_price'] = None
                data['pr_fetched'] = True
        return

    pending = [sym for sym, data in tracked_stocks.items() if not data.get('pr_fetched')]
    if not pending:
        return

    logger.info(f"RTPR: fetching latest PR for {len(pending)} newly-seen symbol(s)...")

    now = datetime.datetime.now(datetime.timezone.utc)

    def _worker(sym):
        pr = fetch_latest_pr(sym)
        base_price = None
        # Only fetch the PR-time price when the PR is inside the display window
        # (otherwise PRyield would render "-" anyway) — saves a yfinance call.
        if pr is not None:
            age_seconds = (now - pr['published_at']).total_seconds()
            if age_seconds <= RTPR_PR_MAX_AGE_DAYS * 86400:
                base_price = fetch_pr_base_price(sym, pr['published_at'])
        return sym, pr, base_price

    with ThreadPoolExecutor(max_workers=min(RTPR_FETCH_WORKERS, len(pending))) as executor:
        futures = [executor.submit(_worker, sym) for sym in pending]
        for future in as_completed(futures):
            try:
                sym, result, base_price = future.result()
            except Exception as e:
                logger.debug(f"RTPR worker error: {e}")
                continue
            data = tracked_stocks.get(sym)
            if data is None:
                continue
            if result is None:
                data['pr_published_at'] = None
                data['pr_headline'] = None
                data['pr_base_price'] = None
            else:
                data['pr_published_at'] = result['published_at']
                data['pr_headline'] = result['headline']
                data['pr_base_price'] = base_price
            data['pr_fetched'] = True


def format_last_pr(published_at):
    """Format 'time since last PR' from a UTC datetime.

    Returns "-" if there is no PR or it is older than RTPR_PR_MAX_AGE_DAYS;
    otherwise "hh:mm" within a day, or "Nd hh:mm" once past 24 hours.
    """
    if published_at is None:
        return '-'
    now = datetime.datetime.now(datetime.timezone.utc)
    total_seconds = (now - published_at).total_seconds()
    if total_seconds < 0:
        total_seconds = 0  # clock skew / future-dated PR
    if total_seconds > RTPR_PR_MAX_AGE_DAYS * 86400:
        return '-'
    total_minutes = int(total_seconds // 60)
    days, rem_minutes = divmod(total_minutes, 1440)
    hours, minutes = divmod(rem_minutes, 60)
    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}"
    return f"{hours:02d}:{minutes:02d}"


def format_pr_yield(stock, last_pr_str):
    """Signed % return from the PR-time price to the current live price.

    Returns "-" when LastPR is "-" (no recent PR), or when either the PR-time
    base price or the current price is unavailable. Recomputed every write from
    the cached PR-time price and the live price, so it moves in real time.
    """
    if last_pr_str == '-':
        return '-'
    base = stock.get('pr_base_price')
    current = stock.get('current_price')
    if not base or base <= 0 or current is None:
        return '-'
    pct = (current - base) / base * 100.0
    return f"{pct:+.2f}"


def format_headline(stock, last_pr_str):
    """Return the sanitized PR headline for the file, or "-" when LastPR is "-"
    (no qualifying recent PR). Tabs/newlines are stripped to keep the TSV valid."""
    if last_pr_str == '-':
        return '-'
    raw = stock.get('pr_headline') or ''
    cleaned = raw.replace('\t', ' ').replace('\r', ' ').replace('\n', ' ').strip()
    return cleaned or '-'


def write_complete_gtt(tracked_stocks, gtt_path):
    """
    Write complete GTT file with all tracked stocks.
    This overwrites the file completely with current state.

    IMPORTANT: Writes ALL stocks in tracked_stocks dict, including those
    that may have dropped below threshold. Once tracked, always tracked.

    NOW INCLUDES FUNDAMENTAL METRICS:
    - Float: Total shares available for trading
    - Net_Income_TTM: Trailing twelve month net income
    - Institutional_Pct: Percentage held by institutions
    - Borrow_Fee_Rate: Cost to borrow shares for shorting (from multi-source enrichment)

    SHORTABLE STATUS (IBKR-style):
    - Shortable_Status: HIGH_AVAIL/MODERATE_AVAIL/LOW_AVAIL/NOT_SHORTABLE/N/A (interpreted from tick 46)
    - Shortable_Difficulty: Raw tick 46 value (0.0-3.0+)
    - Shortable_Shares: Raw tick 89 count (exact shares available for shorting)

    Args:
        tracked_stocks: Dictionary of tracked stocks {symbol: stock_data}
        gtt_path: Path to GTT file
    """
    # Fetch each newly-seen symbol's latest PR once (LastPR/Headline columns).
    enrich_last_pr(tracked_stocks)

    # Define header with fundamental metrics and sector/industry columns
    header = [
        'Symbol',
        'LastPR',               # Time since latest PR (hh:mm / Nd hh:mm) from RTPR.io
        'PRyield',              # Signed % return from PR Created-time price to live price
        'Headline',             # Latest PR headline from RTPR.io
        'CP-A(t)',              # Checkpoint-A TIME
        'CP-A($)',              # Checkpoint-A PRICE
        'CP-B(t)',              # Checkpoint-B TIME
        'CP-B($)',              # Checkpoint-B PRICE
        'Gap(%)',               # Gain_Percent
        'GapL(%)',              # live_percentage
        'PriceL($)',            # Live price used for GapL(%) calculation
        'Sect',                 # Sector (from yfinance)
        'Indus',                # Industry (from yfinance)
        'OTC',                  # OTC status ("OTC" or "-")
        'ShortStatus',          # Shortable_Status (IBKR-style label)
        'ShortDiff',            # Shortable_Difficulty (Raw tick 46 value)
        'ShortableShares',      # Shortable_Shares (Raw tick 89 count)
        'Shortable',            # Margin Eligibility via WhatIf (YES/NO/N/A)
        'Float(M$)',            # Float in millions (from yfinance)
        'NetIncome(M$)',        # Net_Income_TTM in millions (from yfinance)
        'Instit(%)',            # Institutional_Pct (from yfinance)
        'BorrowFee',            # Borrow_Fee_Rate (from IBKR)
        'BF1',                  # Borrow Fee source 1 (added by enrichment)
        'BF2',                  # Borrow Fee source 2 (added by enrichment)
        'BF_BEST',              # Best borrow fee (added by enrichment)
        '1yrT',                 # 1-year trend slope
        '6moT',                 # 6-month trend slope
        '3moT',                 # 3-month trend slope
        'xVol',                 # Volume ratio (date-A volume / 10-day average)
        'Trade',                # Trade decision: YES/NO (from trade_decider)
        'KellyPosition',        # Kelly position size in shares (from Kelly calculator)
        'TradeCash($)',         # Allocated cash for position (from Kelly calculator)
        'LastUpdate'            # Last_Update
    ]

    # Convert dict to sorted list (by live_percentage descending)
    stock_list = []
    for symbol, data in tracked_stocks.items():
        stock_list.append({
            'symbol': symbol,
            **data
        })

    # Sort by live_percentage (highest first)
    sorted_stocks = sorted(stock_list, key=lambda x: x.get('live_percentage', 0), reverse=True)

    # Write complete file atomically: write to a temp file in the same
    # directory, then os.replace() onto the real path. os.replace is atomic on
    # the same filesystem, so an external reader (e.g. VisiData with
    # --reload-every) never observes a half-written/truncated table.
    tmp_path = f"{gtt_path}.tmp"
    try:
        with open(tmp_path, 'w') as f:
            # Write header
            f.write('\t'.join(header) + '\n')

            # Write all tracked stocks
            for stock in sorted_stocks:
                # Format fundamental fields with N/A fallback
                sector_val = stock.get('sector')
                industry_val = stock.get('industry')
                float_val = stock.get('float_shares')
                net_income_val = stock.get('net_income_ttm')
                inst_pct_val = stock.get('institutional_pct')
                borrow_fee_val = stock.get('borrow_fee_rate')

                # Convert Float and NetIncome to millions
                float_in_millions = float_val / 1_000_000 if float_val is not None else None
                # NetIncome is already a string from yfinance, need to convert if numeric
                if net_income_val is not None:
                    try:
                        net_income_numeric = float(net_income_val)
                        net_income_in_millions = net_income_numeric / 1_000_000
                    except (ValueError, TypeError):
                        net_income_in_millions = None
                else:
                    net_income_in_millions = None

                # Get shortable status and raw tick values
                shortable_status = stock.get('shortable_status', 'N/A')
                shortable_difficulty = stock.get('shortable_difficulty')  # tick 46 raw value
                shortable_shares = stock.get('shortable_shares')  # tick 89 exact count

                # Get borrow fee enrichment columns (will be N/A if not enriched yet)
                bf1_val = stock.get('bf1')
                bf2_val = stock.get('bf2')
                bf_best_val = stock.get('bf_best')

                # Get trend analysis columns (will be N/A if not enriched yet)
                trend_1y_val = stock.get('trend_1y')
                trend_6m_val = stock.get('trend_6m')
                trend_3m_val = stock.get('trend_3m')

                # Get xVol (volume ratio from PDV calculator)
                xvol_val = stock.get('xvol')

                # Get trade decision (will be "NO" if not evaluated or criteria not met)
                trade_decision = stock.get('trade_decision', 'NO')

                # Get Kelly calculator values
                kelly_position = stock.get('kelly_position')
                trade_cash = stock.get('trade_cash')

                # Format Kelly columns
                if kelly_position is None:
                    kelly_position_str = "N/A"
                else:
                    kelly_position_str = str(kelly_position)

                if trade_cash is None:
                    trade_cash_str = "N/A"
                else:
                    trade_cash_str = f"{trade_cash:.2f}"

                # Extract current_price for PriceL($) column
                current_price = stock.get('current_price')

                # Extract OTC status for OTC column
                is_otc_str = stock.get('is_otc_str', '-')

                # LastPR / PRyield / Headline (from RTPR.io, placed right after Symbol)
                last_pr_str = format_last_pr(stock.get('pr_published_at'))
                pr_yield_str = format_pr_yield(stock, last_pr_str)
                headline_str = format_headline(stock, last_pr_str)

                row = [
                    stock['symbol'],
                    last_pr_str,
                    pr_yield_str,
                    headline_str,
                    stock['checkpoint_a_time'],
                    f"{stock['checkpoint_a_price']:.2f}",
                    stock['checkpoint_b_time'],
                    f"{stock['checkpoint_b_price']:.2f}",
                    f"{stock['gain_percent']:.2f}",
                    f"{stock['live_percentage']:.2f}",
                    # LIVE PRICE:
                    f"{current_price:.2f}" if current_price is not None else 'N/A',
                    # SECTOR/INDUSTRY FIELDS:
                    str(sector_val) if sector_val is not None else 'N/A',
                    str(industry_val) if industry_val is not None else 'N/A',
                    # OTC STATUS:
                    is_otc_str,
                    # SHORTABLE DATA (IBKR-style):
                    shortable_status,
                    f"{shortable_difficulty:.2f}" if shortable_difficulty is not None else 'N/A',
                    str(shortable_shares) if shortable_shares is not None else 'N/A',
                    # MARGIN ELIGIBILITY (WhatIf check):
                    stock.get('shortable_via_margin', 'N/A'),
                    # FUNDAMENTAL FIELDS (converted to millions):
                    f"{float_in_millions:.2f}" if float_in_millions is not None else 'N/A',
                    f"{net_income_in_millions:.2f}" if net_income_in_millions is not None else 'N/A',
                    f"{inst_pct_val:.2f}" if inst_pct_val is not None else 'N/A',
                    f"{borrow_fee_val:.2f}" if borrow_fee_val is not None else 'N/A',
                    # BORROW FEE ENRICHMENT COLUMNS (added by orchestrator):
                    f"{bf1_val:.2f}" if bf1_val is not None else 'N/A',
                    f"{bf2_val:.2f}" if bf2_val is not None else 'N/A',
                    f"{bf_best_val:.2f}" if bf_best_val is not None else 'N/A',
                    # TREND COLUMNS (added by trend_verifier):
                    f"{trend_1y_val:.2f}" if trend_1y_val is not None else 'N/A',
                    f"{trend_6m_val:.2f}" if trend_6m_val is not None else 'N/A',
                    f"{trend_3m_val:.2f}" if trend_3m_val is not None else 'N/A',
                    # XVOL COLUMN (volume ratio from PDV calculator):
                    f"{xvol_val:.2f}" if xvol_val is not None else 'N/A',
                    # TRADE DECISION (added by trade_decider):
                    trade_decision,
                    # KELLY CALCULATOR (added by kelly calculator):
                    kelly_position_str,
                    trade_cash_str,
                    stock['last_update']
                ]
                f.write('\t'.join(row) + '\n')

        os.replace(tmp_path, gtt_path)
        logger.info(f"Wrote complete GTT with {len(sorted_stocks)} stocks to {gtt_path}")

    except Exception as e:
        logger.error(f"Error writing complete GTT file: {e}")
        # Clean up the partial temp file so it can't be mistaken for output.
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def evaluate_trade_decision(stock_data, args):
    """
    Evaluate if stock meets ALL trade decision criteria.

    Trade Decision Logic (evaluated in order):
    - Criterion 0 (PRIORITY): OTC Filter - if --discard-otc YES, reject OTC stocks
    - Criterion 0.5 (HARD REQUIREMENT): Shortable=YES required (margin eligibility via WhatIf)
    - Criterion 1: GapL(%) must be LARGER than args.gap_larger_then
    - Criterion 2: Instit(%) must be SMALLER than args.institution_smaller_then
    - Criterion 3: Float(M$) must be SMALLER than args.float_smaller_then
    - Criterion 4: CP-B($) must be SMALLER than args.price_smaller_then
    - Criterion 5: BF_BEST must be SMALLER than args.borrow_fee_smaller_then
    - Criterion 6: xVol must be SMALLER than args.xVolume_smaller_then

    All criteria must be met for "YES" result.
    Missing/N/A data results in "NO" (conservative approach).

    Note: Criterion 0.5 (Shortable) is a hard requirement - stocks with Shortable=NO or N/A
    are automatically rejected regardless of other criteria.

    Args:
        stock_data: Dictionary with stock information
        args: Parsed command-line arguments

    Returns:
        str: "YES" if ALL criteria are met, "NO" otherwise
    """
    # If trade decision feature not enabled (no TD args), return NO
    if all([
        args.gap_larger_then is None,
        args.institution_smaller_then is None,
        args.float_smaller_then is None,
        args.price_smaller_then is None,
        args.borrow_fee_smaller_then is None,
        args.xVolume_smaller_then is None
    ]):
        return "NO"

    # Check criterion 0 (PRIORITY): OTC Filter
    # This check happens FIRST to short-circuit expensive checks
    if hasattr(args, 'discard_otc') and args.discard_otc == 'YES':
        is_otc = stock_data.get('is_otc', False)
        if is_otc:
            logger.debug(f"{stock_data.get('symbol', 'UNKNOWN')}: Discarded - OTC stock")
            return "NO"

    # Check criterion 0.5 (HARD REQUIREMENT): Margin Eligibility
    # This check happens IMMEDIATELY after OTC filter to avoid wasting checks on non-shortable stocks
    # Both 'NO' and 'N/A' disqualify (conservative approach)
    shortable_via_margin = stock_data.get('shortable_via_margin')
    if shortable_via_margin != 'YES':
        # Either 'NO' or 'N/A' - both disqualify for trading
        logger.debug(f"{stock_data.get('symbol', 'UNKNOWN')}: Discarded - Not shortable via margin (Shortable={shortable_via_margin})")
        return "NO"

    # Check criterion 1: GapL(%) - must be LARGER than threshold
    if args.gap_larger_then is not None:
        live_pct = stock_data.get('live_percentage')
        if live_pct is None or live_pct <= args.gap_larger_then:
            return "NO"

    # Check criterion 2: Instit(%) - must be SMALLER than threshold
    if args.institution_smaller_then is not None:
        inst_pct = stock_data.get('institutional_pct')
        if inst_pct is None or inst_pct >= args.institution_smaller_then:
            return "NO"

    # Check criterion 3: Float(M$) - must be SMALLER than threshold
    if args.float_smaller_then is not None:
        float_shares = stock_data.get('float_shares')
        if float_shares is None:
            return "NO"
        # Convert to millions for comparison
        float_millions = float_shares / 1_000_000
        if float_millions >= args.float_smaller_then:
            return "NO"

    # Check criterion 4: CP-B($) - must be SMALLER than threshold
    if args.price_smaller_then is not None:
        cp_b_price = stock_data.get('checkpoint_b_price')
        if cp_b_price is None or cp_b_price >= args.price_smaller_then:
            return "NO"

    # Check criterion 5: BF_BEST - must be SMALLER than threshold
    if args.borrow_fee_smaller_then is not None:
        bf_best = stock_data.get('bf_best')
        if bf_best is None or bf_best >= args.borrow_fee_smaller_then:
            return "NO"

    # Check criterion 6: xVol - must be SMALLER than threshold
    if args.xVolume_smaller_then is not None:
        xvol = stock_data.get('xvol')
        if xvol is None or xvol >= args.xVolume_smaller_then:
            return "NO"

    # All 6 criteria met!
    return "YES"


def calculate_kelly_positions(tracked_stocks, args):
    """
    Calculate Kelly Criterion position sizes for stocks marked Trade='YES'.

    Distributes --cash-to-play across qualifying stocks with concentration limits.
    Updates tracked_stocks dict in-place with 'kelly_position' and 'trade_cash' fields.

    Args:
        tracked_stocks (dict): Dictionary of stock data {symbol: stock_data_dict}
        args: Parsed command-line arguments containing cash_to_play and max_proportion

    Returns:
        None (modifies tracked_stocks in-place)

    Algorithm:
        1. Count stocks with trade_decision='YES'
        2. Calculate base allocation: cash_to_play / yes_count
        3. Apply cap: min(base_allocation, cash_to_play * max_proportion)
        4. Calculate position: floor(capped_cash / stock_price)
        5. Store trade_cash as the capped allocation (not position value)

    Edge Cases:
        - If cash_to_play is None: Set all to None (displays as N/A)
        - If trade_decision != 'YES': Set to None
        - If price invalid (None/≤0): Set to None
        - If yes_count == 0: Set all to None
        - Valid zero position: Store as 0 (displays as "0")
    """

    # Initialize all stocks with None (N/A by default)
    for symbol, stock_data in tracked_stocks.items():
        stock_data['kelly_position'] = None
        stock_data['trade_cash'] = None

    # Feature disabled if --cash-to-play not provided
    if args.cash_to_play is None:
        return

    # Count stocks marked YES
    yes_stocks = [
        (symbol, data) for symbol, data in tracked_stocks.items()
        if data.get('trade_decision') == 'YES'
    ]

    yes_count = len(yes_stocks)

    # No qualifying stocks - all remain N/A
    if yes_count == 0:
        return

    # Calculate base allocation per stock
    base_cash_per_stock = args.cash_to_play / yes_count

    # Calculate concentration cap
    max_cash_per_stock = args.cash_to_play * args.max_tradeCash_proportion_per_stock

    # Apply cap to each stock
    cash_available = min(base_cash_per_stock, max_cash_per_stock)

    # Calculate positions for YES stocks
    for symbol, stock_data in yes_stocks:
        # Get current price (use checkpoint_b_price as most recent peak price)
        stock_price = stock_data.get('checkpoint_b_price')

        # Validate price
        if stock_price is None or stock_price <= 0:
            # Invalid price - leave as N/A
            continue

        # Calculate position size (floor to get whole shares)
        kelly_position = int(cash_available / stock_price)

        # Store results
        stock_data['kelly_position'] = kelly_position
        stock_data['trade_cash'] = cash_available  # Store capped allocation, not position value


def enrich_gtt_with_ftp_data(gtt_path):
    """
    Enrich GTT file with borrow fee data from multiple sources.
    Uses multi-source orchestrator to fetch from IBKR FTP and iBorrowDesk API.

    Args:
        gtt_path: Path to GTT file to enrich

    Returns:
        bool: True if enrichment succeeded, False otherwise
    """
    try:
        # Import the orchestrator module
        script_dir = os.path.dirname(os.path.abspath(__file__))
        orchestrator_path = os.path.join(os.path.dirname(script_dir), 'borrow_fee_fetch_functions', 'borrow_fee_orchestrator.py')

        if not os.path.exists(orchestrator_path):
            logger.warning(f"Borrow fee orchestrator not found at {orchestrator_path}, skipping enrichment")
            return False

        # Use subprocess to run the multi-source enrichment
        # This keeps dependencies isolated and allows for easier error handling
        cmd = [
            'python3',
            orchestrator_path,
            '--input', gtt_path,
            '--sources', 'ftp,iborrowdesk',  # Use FTP + iBorrowDesk
            '--ftp-cache', './scripts_functions/borrow_fee_fetch_functions/cache/borrow_fees.csv',  # Cache FTP data
            '--ibd-cache', './scripts_functions/borrow_fee_fetch_functions/cache/iborrowdesk_cache.csv'  # Cache iBorrowDesk data
        ]

        logger.info("Enriching GTT with multi-source borrow fee data (FTP + iBorrowDesk)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode == 0:
            logger.info("GTT enrichment with multi-source data completed successfully")
            # Log summary from subprocess output
            for line in result.stdout.split('\n'):
                if 'Updated' in line or 'Enriched' in line or 'BF1' in line or 'BF2' in line:
                    logger.info(f"Enrichment: {line.strip()}")
            return True
        else:
            logger.warning(f"Multi-source enrichment failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Multi-source enrichment timed out (120s), continuing without enrichment")
        return False
    except Exception as e:
        logger.warning(f"Multi-source enrichment error: {e}, continuing without enrichment")
        return False


def enrich_gtt_with_trend_data(gtt_path):
    """
    Enrich GTT file with trend slope analysis for 1-year, 6-month, and 3-month periods.
    Adds columns: 1yrT, 6moT, 3moT (positioned after BF_BEST, before LastUpdate).

    Args:
        gtt_path: Path to GTT file to enrich

    Returns:
        bool: True if enrichment succeeded, False otherwise
    """
    try:
        # Locate trend verifier script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        trend_script = os.path.join(os.path.dirname(script_dir), 'trend_verifier', 'Trend_Verifier.py')

        if not os.path.exists(trend_script):
            logger.warning(f"Trend verifier not found at {trend_script}, skipping trend enrichment")
            return False

        # Build subprocess command
        cmd = [
            'python3',
            trend_script,
            '--input', gtt_path,
            '--client-id', '600',  # Use client ID 600 for trend verifier
            '--cache', './scripts_functions/trend_verifier/cache/trend_slopes_cache.csv',
            '--cache-ttl', '86400'  # 24-hour cache TTL
        ]

        logger.info("Enriching GTT with trend slope analysis (1Y, 6M, 3M)...")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0:
            logger.info("GTT trend enrichment completed successfully")
            # Log summary from subprocess output
            for line in result.stdout.split('\n'):
                if 'enriched' in line.lower() or 'trends calculated' in line.lower() or 'successfully' in line.lower():
                    logger.info(f"Trend enrichment: {line.strip()}")
            return True
        else:
            logger.warning(f"Trend enrichment failed: {result.stderr}")
            return False

    except subprocess.TimeoutExpired:
        logger.warning("Trend enrichment timed out (300s), continuing without trend data")
        return False
    except Exception as e:
        logger.warning(f"Trend enrichment error: {e}, continuing without trend data")
        return False


def reload_enriched_columns(tracked_stocks, gtt_path):
    """
    Reload enriched columns (BF1, BF2, BF_BEST, 1yrT, 6moT, 3moT) from GTT file back into memory.

    This ensures that in-memory tracked_stocks dict has the same enriched data as the GTT file,
    allowing trade decisions to be evaluated with BF_BEST and trend data.

    Args:
        tracked_stocks (dict): In-memory tracked stocks dictionary
        gtt_path (Path): Path to GTT file

    Returns:
        None (modifies tracked_stocks in-place)
    """
    if not gtt_path.exists():
        logger.warning(f"reload_enriched_columns: GTT file not found: {gtt_path}")
        return

    try:
        with open(gtt_path, 'r') as f:
            lines = f.readlines()

        if len(lines) < 2:
            logger.warning("reload_enriched_columns: GTT file has no data rows")
            return

        # Parse header
        header = lines[0].strip().split('\t')

        # Find column indices for enriched columns
        try:
            bf1_idx = header.index('BF1')
            bf2_idx = header.index('BF2')
            bf_best_idx = header.index('BF_BEST')
        except ValueError as e:
            logger.warning(f"reload_enriched_columns: Missing borrow fee columns in GTT header: {e}")
            bf1_idx = bf2_idx = bf_best_idx = None

        # Find trend column indices (may not exist in older GTT files)
        try:
            trend_1y_idx = header.index('1yrT')
            trend_6m_idx = header.index('6moT')
            trend_3m_idx = header.index('3moT')
        except ValueError:
            trend_1y_idx = trend_6m_idx = trend_3m_idx = None

        # Find symbol column
        symbol_idx = header.index('Symbol')

        # Parse data rows and update tracked_stocks
        enriched_count = 0
        for line in lines[1:]:
            cols = line.strip().split('\t')
            if len(cols) <= symbol_idx:
                continue

            symbol = cols[symbol_idx]

            if symbol not in tracked_stocks:
                continue  # Stock not in tracked_stocks (shouldn't happen)

            # Helper function to safely parse numeric values
            def parse_value(val_str):
                if val_str == 'N/A' or not val_str:
                    return None
                try:
                    return float(val_str)
                except ValueError:
                    return None

            # Reload borrow fee columns
            if bf1_idx is not None and len(cols) > bf1_idx:
                tracked_stocks[symbol]['bf1'] = parse_value(cols[bf1_idx])
            if bf2_idx is not None and len(cols) > bf2_idx:
                tracked_stocks[symbol]['bf2'] = parse_value(cols[bf2_idx])
            if bf_best_idx is not None and len(cols) > bf_best_idx:
                tracked_stocks[symbol]['bf_best'] = parse_value(cols[bf_best_idx])

            # Reload trend columns (if they exist)
            if trend_1y_idx is not None and len(cols) > trend_1y_idx:
                tracked_stocks[symbol]['trend_1y'] = parse_value(cols[trend_1y_idx])
            if trend_6m_idx is not None and len(cols) > trend_6m_idx:
                tracked_stocks[symbol]['trend_6m'] = parse_value(cols[trend_6m_idx])
            if trend_3m_idx is not None and len(cols) > trend_3m_idx:
                tracked_stocks[symbol]['trend_3m'] = parse_value(cols[trend_3m_idx])

            enriched_count += 1

        logger.info(f"Reloaded enriched columns for {enriched_count} stocks from GTT file")

    except Exception as e:
        logger.error(f"Error reloading enriched columns: {e}", exc_info=True)


def load_existing_gtt(gtt_path):
    """
    Load tracked stocks from existing GTT file (for resume support).

    Only supports NEW format with renamed columns.
    Expected columns: Symbol, CP-A(t), CP-A($), CP-B(t), CP-B($), Gap(%), GapL(%), PriceL($), Sect, Indus,
                     ShortStatus, ShortDiff, ShortableShares, Float(M$), NetIncome(M$), Instit(%),
                     BorrowFee, BF1, BF2, BF_BEST, Trade, KellyPosition, TradeCash($), LastUpdate

    Note: Float and NetIncome values in file are in millions and will be converted back to original scale.
    """
    tracked = {}

    if not gtt_path.exists():
        return tracked

    try:
        with open(gtt_path, 'r') as f:
            lines = f.readlines()

        # Helper function to parse value or return None
        def parse_value(val):
            if val == 'N/A' or not val:
                return None
            try:
                return float(val)
            except ValueError:
                return val  # Return as string if not a number

        # Build a header-name → index map so parsing survives added or reordered
        # columns. This is what lets the LastPR/Headline columns (inserted after
        # Symbol) and the enrichment columns coexist without breaking resume.
        header = lines[0].strip().split('\t')
        col_idx = {name: i for i, name in enumerate(header)}
        logger.info(f"Detected GTT header with {len(header)} columns")

        def get_col(cols, name, default=None):
            i = col_idx.get(name)
            if i is None or i >= len(cols):
                return default
            return cols[i]

        def to_float(val, default=0.0):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default

        for line in lines[1:]:
            cols = line.strip().split('\t')

            symbol = get_col(cols, 'Symbol')
            if not symbol:
                continue

            checkpoint_a_time = get_col(cols, 'CP-A(t)')
            checkpoint_b_time = get_col(cols, 'CP-B(t)')
            if checkpoint_a_time is None or checkpoint_b_time is None:
                continue

            # CHECKPOINT VALIDATION: Ensure CP-B is after CP-A
            try:
                cp_a_dt = datetime.datetime.strptime(checkpoint_a_time, '%Y-%m-%d/%H:%M')
                cp_b_dt = datetime.datetime.strptime(checkpoint_b_time, '%Y-%m-%d/%H:%M')

                if cp_b_dt <= cp_a_dt:
                    logger.warning(f"Skipping {symbol} from GTT - Invalid checkpoint ordering: CP-B ({checkpoint_b_time}) not after CP-A ({checkpoint_a_time})")
                    continue  # Skip this row
            except Exception as e:
                logger.warning(f"Skipping {symbol} from GTT - Failed to parse checkpoint times: {e}")
                continue

            # Parse Float and NetIncome back from millions to original scale
            float_millions = parse_value(get_col(cols, 'Float(M$)'))
            float_shares = float_millions * 1_000_000 if isinstance(float_millions, (int, float)) else None

            net_income_millions = parse_value(get_col(cols, 'NetIncome(M$)'))
            net_income_ttm = str(net_income_millions * 1_000_000) if isinstance(net_income_millions, (int, float)) else None

            otc_val = get_col(cols, 'OTC')

            tracked[symbol] = {
                'checkpoint_a_time': checkpoint_a_time,
                'checkpoint_a_price': to_float(get_col(cols, 'CP-A($)')),
                'checkpoint_b_time': checkpoint_b_time,
                'checkpoint_b_price': to_float(get_col(cols, 'CP-B($)')),
                'gain_percent': to_float(get_col(cols, 'Gap(%)')),
                'live_percentage': to_float(get_col(cols, 'GapL(%)')),
                'current_price': parse_value(get_col(cols, 'PriceL($)')),
                'sector': parse_value(get_col(cols, 'Sect')),
                'industry': parse_value(get_col(cols, 'Indus')),
                # OTC STATUS (default to non-OTC when column absent):
                'is_otc': otc_val == 'OTC',
                'is_otc_str': otc_val if otc_val is not None else '-',
                'shortable_status': get_col(cols, 'ShortStatus', 'N/A'),
                'shortable_difficulty': parse_value(get_col(cols, 'ShortDiff')),
                'shortable_shares': parse_value(get_col(cols, 'ShortableShares')),
                'shortable_via_margin': get_col(cols, 'Shortable', 'N/A'),
                'float_shares': float_shares,
                'net_income_ttm': net_income_ttm,
                'institutional_pct': parse_value(get_col(cols, 'Instit(%)')),
                'borrow_fee_rate': parse_value(get_col(cols, 'BorrowFee')),
                # Enrichment columns (None when absent):
                'bf1': parse_value(get_col(cols, 'BF1')),
                'bf2': parse_value(get_col(cols, 'BF2')),
                'bf_best': parse_value(get_col(cols, 'BF_BEST')),
                'trend_1y': parse_value(get_col(cols, '1yrT')),
                'trend_6m': parse_value(get_col(cols, '6moT')),
                'trend_3m': parse_value(get_col(cols, '3moT')),
                'xvol': parse_value(get_col(cols, 'xVol')),
                'trade_decision': get_col(cols, 'Trade', 'NO'),
                'last_update': get_col(cols, 'LastUpdate', 'N/A'),
            }

            # Kelly columns (optional)
            kelly_pos_str = get_col(cols, 'KellyPosition', 'N/A')
            try:
                tracked[symbol]['kelly_position'] = int(kelly_pos_str) if kelly_pos_str not in (None, 'N/A', '') else None
            except ValueError:
                tracked[symbol]['kelly_position'] = None
            tracked[symbol]['trade_cash'] = parse_value(get_col(cols, 'TradeCash($)'))

            # Note: LastPR/Headline are intentionally NOT restored here. Leaving
            # 'pr_fetched' unset makes enrich_last_pr() re-fetch the latest PR
            # once on resume, keeping the age/headline fresh.

        logger.info(f"Loaded {len(tracked)} tracked stocks from existing GTT")
        return tracked

    except Exception as e:
        logger.error(f"Error loading GTT file: {e}")
        return {}

# =====================
# Main Scanner Application
# =====================
class GainersScanner:
    def __init__(self, args):
        self.args = args
        # Store account from command line if provided
        if hasattr(args, 'account') and args.account:
            app_state["selected_account"] = args.account
            logger.info(f"Using account from --account argument: {args.account}")
        self.wrapper = ScannerWrapper()
        self.client = ScannerClient(self.wrapper)
        self.ib_thread = None
        self.next_req_id = 5000  # Start request IDs from 5000
        self.console = Console()

    def connect(self):
        """Connect to IB Gateway."""
        host = self.args.host
        port = self.args.port
        client_id = self.args.client_id

        logger.info(f"Connecting to IB Gateway at {host}:{port} with client ID {client_id}...")

        self.client.connect(host, port, client_id)
        self.ib_thread = threading.Thread(target=self.client.run, daemon=True)
        self.ib_thread.start()

        # Wait for connection
        if not app_state["connection_ready"].wait(timeout=15):
            logger.error("Connection timeout. Ensure IB Gateway is running.")
            return False

        # Wait for account information (with 5 second timeout)
        if not app_state["account_ready"].wait(timeout=5):
            logger.warning("Account information not received. WhatIf checks may fail if multiple accounts exist.")
        else:
            logger.info(f"Account ready: {app_state['selected_account']}")

        logger.info("Successfully connected to IB Gateway.")
        return True

    def disconnect(self):
        """Disconnect from IB Gateway."""
        if self.client.isConnected():
            logger.info("Disconnecting from IB Gateway...")
            self.client.disconnect()

        if self.ib_thread and self.ib_thread.is_alive():
            self.ib_thread.join(timeout=5)

        logger.info("Disconnected.")

    def reconnect_with_retry(self, max_retries=5):
        """
        Attempt to reconnect to IB Gateway with exponential backoff.

        Args:
            max_retries: Maximum number of reconnection attempts (default: 5)

        Returns:
            bool: True if reconnection succeeded, False if all attempts failed
        """
        if app_state["reconnection_in_progress"]:
            logger.info("Reconnection already in progress, skipping duplicate attempt")
            return False

        app_state["reconnection_in_progress"] = True

        try:
            # Disconnect cleanly first
            logger.info("Attempting clean disconnection before reconnection...")
            if self.client.isConnected():
                self.client.disconnect()
            if self.ib_thread and self.ib_thread.is_alive():
                self.ib_thread.join(timeout=5)

            # Wait a moment for cleanup
            time.sleep(2)

            # Clear old connection state
            app_state["connection_ready"].clear()

            # Exponential backoff: 5s, 10s, 20s, 40s, 80s
            backoff_delays = [5, 10, 20, 40, 80]

            for attempt in range(1, max_retries + 1):
                logger.info(f"Reconnection attempt {attempt}/{max_retries}...")
                self.console.print(f"\n[yellow]⟳ Reconnection attempt {attempt}/{max_retries}...[/yellow]")

                # Attempt connection
                success = self.connect()

                if success:
                    logger.info(f"✓ Reconnection successful on attempt {attempt}")
                    self.console.print(f"[bold green]✓ Reconnection successful![/bold green]")
                    app_state["connection_lost"].clear()
                    return True
                else:
                    logger.warning(f"✗ Reconnection attempt {attempt} failed")
                    self.console.print(f"[red]✗ Reconnection attempt {attempt} failed[/red]")

                    # If not last attempt, wait with exponential backoff
                    if attempt < max_retries:
                        delay = backoff_delays[min(attempt - 1, len(backoff_delays) - 1)]
                        logger.info(f"Waiting {delay}s before next reconnection attempt...")
                        self.console.print(f"[dim]Waiting {delay}s before next attempt...[/dim]")
                        time.sleep(delay)

            # All attempts failed
            logger.critical(f"✗ All {max_retries} reconnection attempts failed. Shutting down.")
            self.console.print(f"[bold red]✗ All {max_retries} reconnection attempts failed. Shutting down.[/bold red]")
            app_state["shutdown"].set()
            return False

        finally:
            app_state["reconnection_in_progress"] = False

    def detect_otc_status(self, symbol, contract, yfinance_data=None):
        """
        Detect if a stock is OTC (Over-The-Counter) using multi-layer approach.

        Detection Layers:
        1. IBKR contract exchange codes (primary) - checks contract.exchange first, then primaryExchange
        2. yfinance exchange data (fallback) - uses yfinance API data when IBKR is ambiguous
        3. Symbol pattern heuristics (logging only) - flags potential OTC stocks for review

        OTC exchanges include:
        - Standard: PINK, OTCBB, OTCMKTS, OTC
        - IBKR-specific: EXPERT (OTC Markets), SCM, VENTURE, GREY
        - yfinance: OTCMKTS, PINK, OTC, OTCBB, OTCQB, OTCQX

        Args:
            symbol: Stock ticker symbol
            contract: IBKR Contract object with exchange and primaryExchange fields
            yfinance_data: Optional dict from get_yfinance_data() with 'exchange' key

        Returns:
            dict: {
                'is_otc': bool,              # True if OTC, False if major exchange
                'is_otc_str': str,           # "OTC" if OTC, "-" if not
                'detection_method': str      # Method used for detection (for debugging)
            }
        """
        # Layer 1: IBKR exchange codes
        # Standard OTC exchanges
        standard_otc_exchanges = {
            'PINK',      # Pink Sheets
            'OTCBB',     # OTC Bulletin Board
            'OTCMKTS',   # OTC Markets
            'OTC',       # Generic OTC
            'OTCQX',     # OTC Markets - OTCQX tier (Best Market)
            'OTCQB'      # OTC Markets - OTCQB tier (Venture Market)
        }

        # IBKR-specific OTC exchange codes (discovered through testing)
        # NOTE: SCM (Nasdaq Capital Market) is NOT OTC - it's a NASDAQ tier exchange
        ibkr_otc_exchanges = {
            'EXPERT',   # OTC Markets exchange (ORGS, LUNA confirmed)
            'VENTURE',  # OTC Venture exchange
            'GREY'      # Grey market OTC
        }

        # Combined OTC exchange set
        all_otc_exchanges = standard_otc_exchanges | ibkr_otc_exchanges

        # Check contract.exchange FIRST (more reliable than primaryExchange for scanner results)
        if contract.exchange and contract.exchange.upper() in all_otc_exchanges:
            logger.info(f"{symbol}: OTC detected via contract.exchange='{contract.exchange}' [IBKR Layer 1]")
            return {
                'is_otc': True,
                'is_otc_str': 'OTC',
                'detection_method': f'IBKR-exchange:{contract.exchange}'
            }

        # Check primaryExchange (if populated)
        if contract.primaryExchange and contract.primaryExchange.upper() in all_otc_exchanges:
            logger.info(f"{symbol}: OTC detected via contract.primaryExchange='{contract.primaryExchange}' [IBKR Layer 1]")
            return {
                'is_otc': True,
                'is_otc_str': 'OTC',
                'detection_method': f'IBKR-primaryExchange:{contract.primaryExchange}'
            }

        # Check tradingClass (some OTC stocks have EXPERT in tradingClass, not exchange/primaryExchange)
        if hasattr(contract, 'tradingClass') and contract.tradingClass and contract.tradingClass.upper() in all_otc_exchanges:
            logger.info(f"{symbol}: OTC detected via contract.tradingClass='{contract.tradingClass}' [IBKR Layer 1]")
            return {
                'is_otc': True,
                'is_otc_str': 'OTC',
                'detection_method': f'IBKR-tradingClass:{contract.tradingClass}'
            }

        # Layer 2: yfinance exchange data (fallback when IBKR is empty/SMART)
        if yfinance_data and 'exchange' in yfinance_data and yfinance_data['exchange']:
            yf_exchange = yfinance_data['exchange'].upper()

            # yfinance uses different exchange codes
            yfinance_otc_markers = {
                'OTCMKTS',    # OTC Markets
                'PINK',       # Pink Sheets
                'PNK',        # Pink Sheets (abbreviated form from yfinance)
                'OTC',        # Generic OTC
                'OTCBB',      # OTC Bulletin Board
                'OTCQB',      # OTCQB Venture Market
                'OTCQX',      # OTCQX Best Market
                'OQX',        # OTC Markets OTCQX (abbreviated form from yfinance)
                'OQB'         # OTC Markets OTCQB (abbreviated form from yfinance)
            }

            if yf_exchange in yfinance_otc_markers:
                logger.info(f"{symbol}: OTC detected via yfinance exchange='{yf_exchange}' [yfinance Layer 2]")
                return {
                    'is_otc': True,
                    'is_otc_str': 'OTC',
                    'detection_method': f'yfinance-exchange:{yf_exchange}'
                }

        # Layer 3: Symbol pattern heuristics (last resort - logging only, NOT used for detection)
        # OTC stocks often (not always) have 5-letter symbols ending in F
        # This is NOT reliable alone but useful as a weak signal for manual review
        if len(symbol) == 5 and symbol.endswith('F') and symbol[:-1].isalpha():
            # Weak signal - log as warning, NOT definitive
            logger.warning(f"{symbol}: Symbol matches OTC pattern (5-letter ending in F) but no exchange confirmation. "
                          f"Treating as non-OTC but flagging for manual review. [Heuristic Layer 3]")
            # Do NOT mark as OTC based on pattern alone - too many false positives

        # Default: Not OTC (major exchange)
        exchange_info = contract.primaryExchange or contract.exchange or 'N/A'
        logger.debug(f"{symbol}: Not OTC - exchange='{exchange_info}' [Default]")

        return {
            'is_otc': False,
            'is_otc_str': '-',
            'detection_method': f'non-OTC:{exchange_info}'
        }

    def run_scanner(self):
        """Execute IB scanner to get stock universe."""
        logger.info("Requesting top percentage gainers from IB Scanner...")

        # Reset scanner results for new scan
        app_state["scanner_results"] = []
        app_state["scanner_complete"].clear()

        # Scanner subscription parameters
        # Use TOP_PERC_GAIN to get top percentage gainers
        self.client.reqScannerSubscription(
            reqId=1,
            subscription=self.create_scanner_subscription(),
            scannerSubscriptionOptions=[],
            scannerSubscriptionFilterOptions=[]
        )

        # Wait for scanner to complete
        scanner_completed = app_state["scanner_complete"].wait(timeout=30)

        if not scanner_completed:
            # Timeout occurred - IBKR never sent scannerDataEnd
            app_state["scanner_timeout_count"] += 1
            logger.warning(f"Scanner timeout (no response from IBKR). Timeout count: {app_state['scanner_timeout_count']}")

            # Check for persistent service unavailability
            if app_state["scanner_timeout_count"] >= 3:
                current_time = datetime.datetime.now(ET_TZ)
                is_premarket = current_time.hour < 9 or (current_time.hour == 9 and current_time.minute < 30)

                if is_premarket:
                    logger.warning("IBKR scanner service may not be available during early pre-market hours (before 7:30 AM EST).")
                    self.console.print("[yellow]⚠ IBKR Scanner Service Unavailable[/yellow]")
                    self.console.print("[yellow]  The scanner has timed out 3+ times. This is common before 7:30 AM EST.[/yellow]")
                    self.console.print("[yellow]  Consider waiting until 7:30 AM or using --historical-mode.[/yellow]")
                else:
                    logger.warning("IBKR scanner service appears to be unavailable (3+ consecutive timeouts).")
                    self.console.print("[yellow]⚠ IBKR Scanner Service Issue[/yellow]")
                    self.console.print("[yellow]  The scanner has timed out 3+ times. IBKR may be experiencing issues.[/yellow]")
        else:
            # Scanner completed successfully - reset timeout counter
            if app_state["scanner_timeout_count"] > 0:
                logger.info(f"Scanner service recovered after {app_state['scanner_timeout_count']} timeouts.")
            app_state["scanner_timeout_count"] = 0

        # Cancel scanner subscription
        self.client.cancelScannerSubscription(1)

        # Limit to max stocks
        stocks = app_state["scanner_results"][:self.args.max_stocks]

        # Distinguish between "0 results from IBKR" vs "service timeout"
        if len(stocks) == 0:
            if scanner_completed:
                logger.info("Scanner returned 0 stocks (IBKR sent scannerDataEnd with no results).")
            else:
                logger.info("Scanner returned 0 stocks (timeout - IBKR service not responding).")
        else:
            logger.info(f"Scanner returned {len(stocks)} stocks for analysis.")

        return stocks

    def create_scanner_subscription(self):
        """Create scanner subscription object."""
        from ibapi.scanner import ScannerSubscription

        scan_sub = ScannerSubscription()
        scan_sub.instrument = "STK"
        scan_sub.locationCode = "STK.US"
        scan_sub.scanCode = "TOP_PERC_GAIN"  # Top percentage gainers

        # Optional filters
        if self.args.min_price > 0:
            scan_sub.abovePrice = self.args.min_price

        return scan_sub

    def get_price_at_time0(self, symbol, contract):
        """
        Get the stock price at the time0 checkpoint (Checkpoint-A).
        Uses cached value if already fetched for this symbol.

        Args:
            symbol: Stock symbol
            contract: IB contract object

        Returns:
            Price at time0, or None if unavailable
        """
        # Check cache first
        if symbol in app_state["time0_prices"]:
            return app_state["time0_prices"][symbol]

        logger.info(f"Fetching price at time0 for {symbol}...")

        # Clear previous historical data for this symbol
        if symbol in app_state["historical_data"]:
            app_state["historical_data"][symbol] = []

        # Request historical data around time0
        req_id = self.next_req_id
        self.next_req_id += 1

        # Store mapping
        if not hasattr(self.wrapper, 'hist_request_map'):
            self.wrapper.hist_request_map = {}
        self.wrapper.hist_request_map[symbol] = req_id
        self.wrapper.pending_historical_requests.add(req_id)

        # Get time0 from global state
        time0_dt = app_state["time0_datetime"]

        # Format end time for IB API (request data up to time0)
        # Add a buffer of 30 minutes after time0 to ensure we capture it
        end_time = time0_dt + datetime.timedelta(minutes=30)
        end_time_str = end_time.strftime('%Y%m%d %H:%M:%S') + ' US/Eastern'

        # Request 1-hour of 1-minute bars for precision
        self.client.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_time_str,
            durationStr='3600 S',  # 1 hour = 3600 seconds
            barSizeSetting='1 min',
            whatToShow='TRADES',
            useRTH=0,  # Include extended hours
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )

        # Wait for response
        time.sleep(0.2)

        # Wait for historical data to complete
        timeout = 0
        while req_id in self.wrapper.pending_historical_requests and timeout < 50:
            time.sleep(0.1)
            timeout += 1

        # Find the bar closest to time0
        bars = app_state["historical_data"].get(symbol, [])
        if bars:
            # Convert time0 to naive datetime for comparison
            time0_naive = time0_dt.replace(tzinfo=None)

            # Find bar with time closest to time0
            closest_bar = min(bars, key=lambda b: abs((b['date'] - time0_naive).total_seconds()))

            # Use the close price of the closest bar
            time0_price = closest_bar['close']

            # Calculate time difference
            time_diff = abs((closest_bar['date'] - time0_naive).total_seconds())

            logger.info(f"{symbol}: Price at time0 = ${time0_price:.2f} (bar at {closest_bar['date']}, {time_diff:.0f}s from target)")

            # Cache the result
            app_state["time0_prices"][symbol] = time0_price

            return time0_price
        else:
            logger.warning(f"{symbol}: No data available at time0")
            return None

    def get_daily_close_price(self, symbol, contract, date_a_dt):
        """
        Get the daily close price for a specific date (date-A mode).
        Uses cached value if already fetched for this symbol+date.

        Args:
            symbol: Stock symbol
            contract: IB contract object
            date_a_dt: datetime object representing the date-A (at 16:00 ET)

        Returns:
            Daily close price, or None if unavailable
        """
        # Create cache key with date to support multiple dates
        date_str = date_a_dt.strftime('%Y-%m-%d')
        cache_key = f"{symbol}_{date_str}"

        # Check cache first (use time0_prices dict with extended key)
        if cache_key in app_state["time0_prices"]:
            logger.debug(f"{symbol}: Using cached daily close price for {date_str}")
            return app_state["time0_prices"][cache_key]

        logger.info(f"Fetching daily close price for {symbol} on {date_str}...")

        # Clear previous historical data for this symbol
        if symbol in app_state["historical_data"]:
            app_state["historical_data"][symbol] = []

        # Request historical data
        req_id = self.next_req_id
        self.next_req_id += 1

        # Store mapping
        if not hasattr(self.wrapper, 'hist_request_map'):
            self.wrapper.hist_request_map = {}
        self.wrapper.hist_request_map[symbol] = req_id
        self.wrapper.pending_historical_requests.add(req_id)

        # Format end time for IB API (end of market day)
        # Request data ending at 16:00 ET on the target date
        end_time_str = date_a_dt.strftime('%Y%m%d %H:%M:%S') + ' US/Eastern'

        # Request 1 day of daily bars
        self.client.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_time_str,
            durationStr='1 D',  # 1 day duration
            barSizeSetting='1 day',  # Daily bar
            whatToShow='TRADES',
            useRTH=1,  # Regular trading hours only (use RTH close price)
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )

        # Wait for response
        time.sleep(0.2)

        # Wait for historical data to complete
        timeout = 0
        while req_id in self.wrapper.pending_historical_requests and timeout < 50:
            time.sleep(0.1)
            timeout += 1

        # Get the daily bar
        bars = app_state["historical_data"].get(symbol, [])
        if bars:
            # Should only have 1 bar (the daily bar for the target date)
            # Use the close price of that bar
            daily_bar = bars[0]
            daily_close_price = daily_bar['close']

            logger.info(f"{symbol}: Daily close price on {date_str} = ${daily_close_price:.2f}")

            # Cache the result with date-specific key
            app_state["time0_prices"][cache_key] = daily_close_price

            # ALSO cache with symbol-only key for compatibility with existing code
            # that expects app_state["time0_prices"][symbol]
            app_state["time0_prices"][symbol] = daily_close_price

            return daily_close_price
        else:
            logger.warning(f"{symbol}: No daily bar data available for {date_str}")
            return None

    def get_highest_price_in_window(self, symbol, contract):
        """
        Get the highest price in the time window from time0 to current time.
        This is used for the new peak-based threshold checking logic.

        Uses hybrid approach:
        - On first call: Fetches historical data from IBKR for the entire window
        - Returns: Dict with {peak_price, peak_time, bars}

        In historical mode:
        - End time is set to market close (16:00 ET) on time0 date + 1 day
        - Example: time0 = 2025-11-20/16:00 → end time = 2025-11-21/16:00

        Args:
            symbol: Stock symbol
            contract: IB contract object

        Returns:
            Dict with peak_price (float), peak_time (datetime), and bars (list)
            or None if unavailable
        """
        # Get time0 from global state
        time0_dt = app_state["time0_datetime"]

        # Determine end time based on mode
        if self.args.historical_mode:
            # Historical mode: end time = market close (16:00 ET) on time0 date + 1 day
            time0_date = time0_dt.date()
            end_date = time0_date + datetime.timedelta(days=1)
            end_dt = ET_TZ.localize(datetime.datetime.combine(end_date, datetime.time(16, 0)))
            logger.info(f"[Historical Mode] Fetching highest price in window (time0 -> {end_dt.strftime('%Y-%m-%d %H:%M')} ET) for {symbol}...")
        else:
            # Live mode: end time = now
            end_dt = datetime.datetime.now(ET_TZ)
            logger.info(f"Fetching highest price in window (time0 -> now) for {symbol}...")

        # Clear previous historical data for this symbol
        if symbol in app_state["historical_data"]:
            app_state["historical_data"][symbol] = []

        # Request historical data from time0 to end_dt
        req_id = self.next_req_id
        self.next_req_id += 1

        # Store mapping
        if not hasattr(self.wrapper, 'hist_request_map'):
            self.wrapper.hist_request_map = {}
        self.wrapper.hist_request_map[symbol] = req_id
        self.wrapper.pending_historical_requests.add(req_id)

        # Calculate duration from time0 to end_dt
        duration_seconds = int((end_dt - time0_dt).total_seconds())

        # Format end time for IB API
        end_time_str = end_dt.strftime('%Y%m%d %H:%M:%S') + ' US/Eastern'

        # Determine appropriate bar size and duration string based on window size
        # IB API has limits on duration for each bar size
        if duration_seconds <= 1800:  # <= 30 minutes
            bar_size = '1 min'
            duration_str = f'{duration_seconds} S'
        elif duration_seconds <= 7200:  # <= 2 hours
            bar_size = '1 min'
            duration_str = f'{int(duration_seconds/60)} M'  # Use minutes
        elif duration_seconds <= 86400:  # <= 1 day
            bar_size = '1 min'
            # IB API max for 1-min bars is ~2000 bars = ~33 hours
            duration_str = f'{min(duration_seconds, 86400)} S'
        else:
            # For longer windows, use larger bar sizes
            bar_size = '5 mins'
            days = min(int(duration_seconds / 86400) + 1, 30)  # Max 30 days for 5-min bars
            duration_str = f'{days} D'

        logger.debug(f"{symbol}: Requesting historical data - duration: {duration_str}, bar size: {bar_size}")

        # Request historical data for the entire window
        self.client.reqHistoricalData(
            reqId=req_id,
            contract=contract,
            endDateTime=end_time_str,
            durationStr=duration_str,
            barSizeSetting=bar_size,
            whatToShow='TRADES',
            useRTH=0,  # Include extended hours
            formatDate=1,
            keepUpToDate=False,
            chartOptions=[]
        )

        # Wait for response
        time.sleep(0.2)

        # Wait for historical data to complete
        timeout = 0
        while req_id in self.wrapper.pending_historical_requests and timeout < 50:
            time.sleep(0.1)
            timeout += 1

        # Find the bar with the highest price in the window
        bars = app_state["historical_data"].get(symbol, [])
        if bars:
            # CRITICAL FIX: Filter bars to only include those AFTER time0
            time0_dt = app_state["time0_datetime"]
            time0_naive = time0_dt.replace(tzinfo=None)  # Bars are stored as naive datetime

            valid_bars = [b for b in bars if b['date'] > time0_naive]

            if not valid_bars:
                logger.warning(f"{symbol}: No bars found after time0 ({time0_naive}) - all {len(bars)} bars precede baseline")
                return None

            # Find bar with highest 'high' price ONLY among valid bars
            peak_bar = max(valid_bars, key=lambda b: b['high'])
            peak_price = peak_bar['high']
            peak_time = peak_bar['date']

            logger.info(f"{symbol}: Highest price in window = ${peak_price:.2f} at {peak_time} ({len(valid_bars)}/{len(bars)} bars after time0)")

            return {
                'peak_price': peak_price,
                'peak_time': peak_time,
                'bars': valid_bars  # Return only valid bars
            }
        else:
            logger.warning(f"{symbol}: No data available in time window")
            return None

    def get_current_price(self, symbol, contract):
        """Get current market price and borrow info using streaming subscription."""
        logger.info(f"Fetching current price and borrow info for {symbol}...")

        req_id = self.next_req_id
        self.next_req_id += 1

        # Store mapping
        if not hasattr(self.wrapper, 'mkt_request_map'):
            self.wrapper.mkt_request_map = {}
        self.wrapper.mkt_request_map[symbol] = req_id
        self.wrapper.pending_market_data_requests.add(req_id)

        # Request streaming market data with shortable shares data
        # Generic tick 236 = Shortable shares (returns as tickType 46)
        # NOTE: Tick 499 is INVALID for stocks (Error 321) - borrow fees obtained via FTP instead
        # NOTE: Cannot use snapshot=True with generic ticks (IB API limitation - Error 321)
        self.client.reqMktData(
            reqId=req_id,
            contract=contract,
            genericTickList='236',  # Request shortable shares only
            snapshot=False,  # Streaming mode required for generic ticks
            regulatorySnapshot=False,
            mktDataOptions=[]
        )

        # Wait for initial data
        time.sleep(0.5)  # Give more time for streaming data to arrive

        # Wait for data to arrive
        timeout = 0
        while req_id in self.wrapper.pending_market_data_requests and timeout < 50:
            time.sleep(0.1)
            timeout += 1

        # Cancel the streaming subscription now that we have the data
        self.client.cancelMktData(req_id)

        # Get price
        price_data = app_state["current_prices"].get(symbol, {})
        current_price = price_data.get('last') or price_data.get('bid') or price_data.get('ask')

        if current_price:
            logger.info(f"{symbol}: Current price = ${current_price:.2f}")
        else:
            logger.warning(f"{symbol}: No current price available")

        return current_price

    def get_fundamental_data(self, symbol, contract):
        """
        Fetch fundamental metrics from IBKR API with caching.

        NOTE: Float, Net Income TTM, and Institutional % are now fetched from yfinance.
        This method only fetches:
        - borrow_fee_rate: Borrow fee rate (if available)

        Implements 1-hour cache to avoid excessive API requests since
        fundamental data doesn't change frequently.

        Returns dict with:
        - borrow_fee_rate: Borrow fee rate (if available)
        """
        # Check cache first
        cache = app_state["fundamental_cache"]
        ttl = app_state["fundamental_cache_ttl"]

        if symbol in cache:
            cached_time = cache[symbol]['timestamp']
            age = time.time() - cached_time
            if age < ttl:
                logger.debug(f"{symbol}: Using cached fundamental data (age: {age:.0f}s)")
                return cache[symbol]['metrics']
            else:
                logger.debug(f"{symbol}: Cache expired (age: {age:.0f}s > TTL: {ttl}s)")

        logger.info(f"{symbol}: Fetching fresh fundamental data from IBKR...")

        # Report types to request - reduced to only what's needed for borrow fee
        # ReportsFinSummary: Financial summary (may contain borrow fee rate)
        report_types = ["ReportsFinSummary"]

        results = []

        for report_type in report_types:
            req_id = self.next_req_id
            self.next_req_id += 1

            # Add to pending requests
            with self.wrapper.fundamental_data_lock:
                self.wrapper.pending_fundamental_requests.add(req_id)

            # Request fundamental data from IBKR
            self.client.reqFundamentalData(req_id, contract, report_type, [])
            logger.debug(f"{symbol}: Requested {report_type} (req_id: {req_id})")

            # Rate limiting to avoid overwhelming IBKR API
            time.sleep(0.3)

            # Wait for response with timeout
            timeout = 0
            max_timeout = 30  # 3 seconds max wait per report
            while req_id in self.wrapper.pending_fundamental_requests and timeout < max_timeout:
                time.sleep(0.1)
                timeout += 1

            # Parse if received
            with self.wrapper.fundamental_data_lock:
                if req_id in self.wrapper.fundamental_data:
                    xml_data = self.wrapper.fundamental_data[req_id]
                    metrics = parse_fundamental_metrics(xml_data)
                    results.append(metrics)
                    logger.debug(f"{symbol}: Parsed {report_type} - "
                               f"Borrow Fee: {metrics.get('borrow_fee_rate')}")
                else:
                    logger.debug(f"{symbol}: No data received for {report_type} (timeout)")

        # Merge results from all report types (first non-None value wins)
        merged = {
            'borrow_fee_rate': None
        }

        for key in merged.keys():
            for result in results:
                if result.get(key) is not None:
                    merged[key] = result[key]
                    break

        # Log summary
        logger.info(f"{symbol}: Fundamental data complete (IBKR) - "
                   f"Borrow Fee: {merged['borrow_fee_rate']}")

        # Cache the result
        cache[symbol] = {
            'metrics': merged,
            'timestamp': time.time()
        }
        logger.debug(f"{symbol}: Cached fundamental data (TTL: {ttl}s)")

        return merged

    def get_yfinance_data(self, symbol):
        """
        Fetch fundamental metrics, sector/industry, and exchange from yfinance API with caching.

        Implements 1-hour cache to avoid excessive API requests since
        fundamental data doesn't change frequently.

        Returns dict with:
        - float_shares: Total float (floatShares)
        - net_income_ttm: Net income TTM (netIncomeToCommon)
        - institutional_pct: Percentage held by institutions (heldPercentInstitutions)
        - sector: Sector classification
        - industry: Industry classification
        - exchange: Exchange code (for OTC detection fallback)
        """
        # Check cache first
        cache = app_state["yfinance_cache"]
        ttl = app_state["yfinance_cache_ttl"]

        if symbol in cache:
            cached_time = cache[symbol]['timestamp']
            age = time.time() - cached_time
            if age < ttl:
                logger.debug(f"{symbol}: Using cached yfinance data (age: {age:.0f}s)")
                return cache[symbol]['data']
            else:
                logger.debug(f"{symbol}: yfinance cache expired (age: {age:.0f}s > TTL: {ttl}s)")

        logger.info(f"{symbol}: Fetching fresh data from yfinance...")

        import yfinance as yf

        # Initialize results
        results = {
            'float_shares': None,
            'net_income_ttm': None,
            'institutional_pct': None,
            'sector': None,
            'industry': None,
            'exchange': None  # For OTC detection fallback
        }

        try:
            # Create ticker object
            ticker = yf.Ticker(symbol)

            # Get info dict
            info = ticker.info

            # Extract Float
            if 'floatShares' in info and info['floatShares']:
                results['float_shares'] = float(info['floatShares'])
                logger.debug(f"{symbol}: Float from yfinance: {results['float_shares']:,.0f}")

            # Extract Net Income TTM
            if 'netIncomeToCommon' in info and info['netIncomeToCommon']:
                results['net_income_ttm'] = str(info['netIncomeToCommon'])
                logger.debug(f"{symbol}: Net Income TTM from yfinance: {results['net_income_ttm']}")

            # Extract Institutional %
            if 'heldPercentInstitutions' in info and info['heldPercentInstitutions'] is not None:
                # yfinance returns as decimal (e.g., 0.75 = 75%)
                results['institutional_pct'] = float(info['heldPercentInstitutions']) * 100
                logger.debug(f"{symbol}: Institutional % from yfinance: {results['institutional_pct']:.2f}%")

            # Extract Sector
            if 'sector' in info and info['sector']:
                results['sector'] = info['sector']
                logger.debug(f"{symbol}: Sector from yfinance: {results['sector']}")

            # Extract Industry
            if 'industry' in info and info['industry']:
                results['industry'] = info['industry']
                logger.debug(f"{symbol}: Industry from yfinance: {results['industry']}")

            # Extract Exchange (for OTC detection fallback)
            if 'exchange' in info and info['exchange']:
                results['exchange'] = info['exchange']
                logger.debug(f"{symbol}: Exchange from yfinance: {results['exchange']}")

            # Rate limiting - sleep 0.1s between requests
            time.sleep(0.1)

            # Log summary
            found_count = sum(1 for v in results.values() if v is not None)
            logger.info(f"{symbol}: yfinance data complete - {found_count}/6 metrics found - "
                       f"Float: {results['float_shares']}, "
                       f"Net Income TTM: {results['net_income_ttm']}, "
                       f"Institutional %: {results['institutional_pct']}, "
                       f"Sector: {results['sector']}, "
                       f"Industry: {results['industry']}, "
                       f"Exchange: {results['exchange']}")

        except Exception as e:
            logger.warning(f"{symbol}: Error fetching yfinance data: {e}")
            # Return None values on error

        # Cache the result (even if some/all fields are None)
        cache[symbol] = {
            'data': results,
            'timestamp': time.time()
        }
        logger.debug(f"{symbol}: Cached yfinance data (TTL: {ttl}s)")

        return results

    def previous_day_volume_calculator(self, symbol, date_a_datetime):
        """
        Calculate volume ratio (xVol) for a stock on date-A relative to 10-day average.

        Formula:
            Average 10 Days Volume = AVG(daily volumes from days -11 to -1 relative to date-A)
            xVol = (date-A volume) / (Average 10 Days Volume)

        Args:
            symbol (str): Stock ticker symbol (e.g., "AAPL")
            date_a_datetime (datetime): The date-A checkpoint (datetime object in US/Eastern)

        Returns:
            dict: {
                'xvol': float or None,          # Volume ratio (e.g., 2.35 = 235% of average)
                'date_a_volume': int or None,   # Actual volume on date-A
                'avg_volume': float or None,    # Average volume of prior 10 days
                'error': str or None            # Error message if failed
            }
        """
        # Check cache first
        cache = app_state["pdv_cache"]
        ttl = app_state["pdv_cache_ttl"]

        if symbol in cache:
            cached_time = cache[symbol]['timestamp']
            age = time.time() - cached_time
            if age < ttl:
                logger.debug(f"{symbol}: Using cached PDV data (age: {age:.0f}s)")
                return cache[symbol]['data']
            else:
                logger.debug(f"{symbol}: PDV cache expired (age: {age:.0f}s > TTL: {ttl}s)")

        logger.info(f"{symbol}: Calculating PDV (Previous Day Volume) ratio...")

        import yfinance as yf

        # Initialize result
        result = {
            'xvol': None,
            'date_a_volume': None,
            'avg_volume': None,
            'error': None
        }

        try:
            # Extract date from date_a_datetime
            date_a = date_a_datetime.date()

            # Calculate date range:
            # - Need 11 trading days: day 0 (date-A) + days -1 to -10 (prior 10 days)
            # - yfinance end date is exclusive, so we use date-A + 1 day
            # - Fetch 20 calendar days to account for weekends/holidays (ensures ≥11 trading days)
            start_date = date_a - datetime.timedelta(days=20)
            end_date = date_a + datetime.timedelta(days=1)

            logger.debug(f"{symbol}: Fetching volume data from {start_date} to {date_a} (20 calendar days)")

            # Fetch historical volume data from yfinance
            ticker = yf.Ticker(symbol)
            history = ticker.history(
                start=start_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d'),
                interval='1d',
                actions=False
            )

            # Rate limiting - match yfinance pattern
            time.sleep(0.1)

            # Validate we have enough data
            if len(history) < 11:
                error_msg = f"Insufficient volume data (got {len(history)} days, need 11)"
                logger.warning(f"{symbol}: {error_msg}")
                result['error'] = error_msg
                cache[symbol] = {'data': result, 'timestamp': time.time()}
                return result

            # Extract date-A volume (last row in the dataset)
            date_a_volume = int(history.iloc[-1]['Volume'])
            result['date_a_volume'] = date_a_volume
            logger.debug(f"{symbol}: date-A volume = {date_a_volume:,}")

            # Extract prior 10 days volumes (rows -11 to -2, excluding the last row which is date-A)
            prior_volumes = history.iloc[-11:-1]['Volume'].tolist()

            # Calculate average of prior 10 days
            avg_volume = sum(prior_volumes) / 10.0
            result['avg_volume'] = avg_volume
            logger.debug(f"{symbol}: Average 10-day volume = {avg_volume:,.2f}")

            # Handle zero average edge case
            if avg_volume == 0:
                error_msg = "Zero average volume"
                logger.warning(f"{symbol}: {error_msg}")
                result['error'] = error_msg
                cache[symbol] = {'data': result, 'timestamp': time.time()}
                return result

            # Calculate xVol ratio
            xvol = date_a_volume / avg_volume
            result['xvol'] = round(xvol, 2)

            logger.info(f"{symbol}: PDV calculation complete - "
                       f"xVol={result['xvol']:.2f}, "
                       f"date-A volume={date_a_volume:,}, "
                       f"10-day avg={avg_volume:,.0f}")

        except Exception as e:
            error_msg = f"API error: {str(e)}"
            logger.warning(f"{symbol}: Error calculating PDV: {e}")
            result['error'] = error_msg

        # Cache the result (even if calculation failed)
        cache[symbol] = {
            'data': result,
            'timestamp': time.time()
        }
        logger.debug(f"{symbol}: Cached PDV data (TTL: {ttl}s)")

        return result

    def check_margin_eligibility(self, symbol, contract, price):
        """
        Check if stock is shortable via WhatIf order preview (margin eligibility test).

        Uses IBKR's WhatIf order preview to test margin eligibility for short selling.
        This provides definitive shortability status by testing actual margin requirements
        for your account, unlike Tick 46/89 which only show SLB (stock loan/borrow) availability.

        Args:
            symbol (str): Stock ticker symbol
            contract (Contract): IBKR Contract object
            price (float): Current stock price for order preview

        Returns:
            str: 'YES' if shortable, 'NO' if not shortable, 'N/A' if check failed/timeout

        Implementation notes:
            - WhatIf orders don't execute - they only calculate margin requirements
            - Order IDs start at 90000 (separate namespace from real orders)
            - Timeout: 3 seconds per stock
            - Historical mode: Skips check entirely (returns 'N/A')
            - Thread-safe via margin_check_lock
        """
        from ibapi.order import Order

        # Historical mode: Skip WhatIf check entirely (no live connection)
        if self.args.historical_mode:
            logger.debug(f"{symbol}: [Historical Mode] Skipping WhatIf margin check")
            return 'N/A'

        # Validate price input
        if not price or price <= 0:
            logger.warning(f"{symbol}: Invalid price ${price}, cannot perform WhatIf check")
            return 'N/A'

        logger.info(f"{symbol}: Initiating WhatIf margin eligibility check (price: ${price:.2f})...")
        start_time = time.time()

        # Generate unique order ID for WhatIf (separate from real orders)
        with app_state["margin_check_lock"]:
            order_id = app_state["next_whatif_order_id"]
            app_state["next_whatif_order_id"] += 1

            # Create tracking entry with threading.Event for synchronization
            check_event = threading.Event()
            app_state["margin_checks"][order_id] = {
                'symbol': symbol,
                'contract': contract,
                'event': check_event,
                'result': None,
                'margin_data': {},
                'timestamp': time.time()
            }

        # Create WhatIf short sell order
        order = Order()
        order.action = "SELL"  # Short sell
        order.orderType = "LMT"  # Limit order
        order.totalQuantity = 100  # Standard test quantity
        order.lmtPrice = price  # Use current price as limit
        order.whatIf = True  # CRITICAL: Makes this a preview-only request (won't execute)

        # CRITICAL FIX for Error 435: Set account for WhatIf order
        # Required when user has multiple accounts or IBKR requires explicit account
        if app_state.get("selected_account"):
            order.account = app_state["selected_account"]
            logger.debug(f"{symbol}: WhatIf order using account: {order.account}")
        else:
            logger.warning(f"{symbol}: No account specified for WhatIf check - may fail if multiple accounts exist")

        # CRITICAL FIX: Override IBKR Order class defaults that IB Gateway rejects
        # The Order() constructor sets eTradeOnly=True and firmQuoteOnly=True by default (ibapi/order.py lines 81-82)
        # IB Gateway rejects these attributes with Error 10268, so we must explicitly override them
        order.eTradeOnly = False
        order.firmQuoteOnly = False

        # Submit WhatIf order
        try:
            self.client.placeOrder(order_id, contract, order)
            logger.debug(f"{symbol}: WhatIf order {order_id} submitted")
        except Exception as e:
            logger.error(f"{symbol}: Failed to submit WhatIf order: {e}")
            with app_state["margin_check_lock"]:
                if order_id in app_state["margin_checks"]:
                    del app_state["margin_checks"][order_id]
            return 'N/A'

        # Wait for response with timeout
        timeout_seconds = 3.0
        response_received = check_event.wait(timeout=timeout_seconds)

        # Retrieve result
        with app_state["margin_check_lock"]:
            if order_id not in app_state["margin_checks"]:
                logger.error(f"{symbol}: Margin check data lost during processing")
                return 'N/A'

            margin_check = app_state["margin_checks"][order_id]
            result = margin_check['result']

            if not response_received:
                logger.warning(f"{symbol}: WhatIf check timeout after {timeout_seconds}s - marking N/A")
                result = 'N/A'

            # Clean up tracking entry to prevent memory leak
            del app_state["margin_checks"][order_id]

        # Log timing
        duration = time.time() - start_time
        logger.debug(f"{symbol}: WhatIf check completed in {duration:.3f}s (result: {result})")

        return result if result else 'N/A'

    def process_single_stock(self, stock_info, percent_threshold, is_first_scan=False):
        """
        Process a single stock - designed for parallel execution.
        NOW USES PEAK-BASED THRESHOLD CHECKING with hybrid approach.

        Args:
            stock_info: Dict with symbol and contract
            percent_threshold: Gain percentage threshold
            is_first_scan: If True, fetch historical peak; if False, use cached peak and update with current price

        Returns dict with stock data if successful, None if failed.
        """
        symbol = stock_info['symbol']
        contract = stock_info['contract']

        try:
            # Step 1: Get price at Checkpoint-A (either time0 or date-A) - cached if already fetched
            if app_state["use_date_a_mode"]:
                # Date-A mode: fetch daily close price
                checkpoint_a_price = self.get_daily_close_price(symbol, contract, app_state["time0_datetime"])
            else:
                # Time0 mode: fetch intraday price
                checkpoint_a_price = self.get_price_at_time0(symbol, contract)

            if not checkpoint_a_price:
                logger.debug(f"{symbol}: No data at Checkpoint-A, skipping")
                return None

            # Step 2: Get current price (and borrow info)
            # In historical mode, skip current price and use last historical bar instead
            if self.args.historical_mode:
                # Historical mode: will use last bar's close price as "current" price
                current_price = None
                logger.debug(f"{symbol}: [Historical Mode] Skipping current price fetch")
            else:
                # Live mode: require current price
                current_price = self.get_current_price(symbol, contract)
                if not current_price:
                    logger.debug(f"{symbol}: No current price, skipping")
                    return None

            # Step 3: Determine peak price using hybrid approach
            peak_cache = app_state.get("peak_prices", {})

            if symbol in peak_cache and not is_first_scan and not self.args.historical_mode:
                # HYBRID APPROACH (Live mode only): Use cached peak and compare with current price
                cached_peak = peak_cache[symbol]
                peak_price = cached_peak['peak_price']
                peak_time = cached_peak['peak_time']

                # Update peak if current price is higher
                if current_price > peak_price:
                    peak_price = current_price
                    peak_time = datetime.datetime.now()
                    peak_cache[symbol] = {
                        'peak_price': peak_price,
                        'peak_time': peak_time
                    }
                    logger.debug(f"{symbol}: NEW PEAK detected - ${peak_price:.2f} at {peak_time}")
            else:
                # FIRST SCAN or symbol not in cache OR historical mode: Fetch historical peak from IBKR
                peak_data = self.get_highest_price_in_window(symbol, contract)
                if not peak_data:
                    logger.debug(f"{symbol}: No peak data available, skipping")
                    return None

                peak_price = peak_data['peak_price']
                peak_time = peak_data['peak_time']
                bars = peak_data['bars']

                # In historical mode, use last bar's close price as "current" price
                if self.args.historical_mode:
                    if bars:
                        last_bar = max(bars, key=lambda b: b['date'])
                        current_price = last_bar['close']
                        logger.debug(f"{symbol}: [Historical Mode] Using last bar close price as current: ${current_price:.2f} at {last_bar['date']}")
                    else:
                        logger.warning(f"{symbol}: [Historical Mode] No bars available for current price")
                        current_price = peak_price  # Fallback to peak price
                else:
                    # Live mode: Compare with current price (might be higher than historical peak)
                    if current_price > peak_price:
                        peak_price = current_price
                        peak_time = datetime.datetime.now()
                        logger.debug(f"{symbol}: Current price exceeds historical peak - using ${peak_price:.2f}")

                # Cache the peak (only in live mode for subsequent scans)
                if not self.args.historical_mode:
                    peak_cache[symbol] = {
                        'peak_price': peak_price,
                        'peak_time': peak_time
                    }

            # Step 4: Calculate percentages
            # live_percentage: Current price vs time0 (for tracking current status)
            live_pct = ((current_price - checkpoint_a_price) / checkpoint_a_price) * 100

            # peak_percentage: Peak price vs time0 (for threshold checking)
            peak_pct = ((peak_price - checkpoint_a_price) / checkpoint_a_price) * 100

            # Step 5: Get borrow info (shortable shares from market data)
            # In historical mode, borrow info is not available
            if self.args.historical_mode:
                shortable_status = "N/A"
                shortable_difficulty = None  # tick 46 raw value
                shortable_shares_count = None  # tick 89 exact count
                logger.debug(f"{symbol}: [Historical Mode] Borrow status set to N/A")
            else:
                borrow_info = app_state["borrow_info"].get(symbol, {})
                shortable_difficulty = borrow_info.get('shortable', None)  # tick 46 value (0.0-3.0+)
                shortable_shares_count = borrow_info.get('shares_count', None)  # tick 89 count

                # Interpret shortable difficulty value for IBKR-style status label
                if shortable_difficulty is not None:
                    if shortable_difficulty > 2.5:
                        shortable_status = "HIGH_AVAIL"  # At least 1,000 shares available
                    elif shortable_difficulty > 1.5:
                        shortable_status = "MODERATE_AVAIL"  # Locatable but fewer shares
                    elif shortable_difficulty > 0:
                        shortable_status = "LOW_AVAIL"  # Hard to borrow
                    else:
                        shortable_status = "NOT_SHORTABLE"  # Not available for shorting
                    # Enhanced logging for Bug #3 investigation
                    logger.info(f"{symbol}: ShortStatus determination - tick46={shortable_difficulty} -> {shortable_status}")
                else:
                    shortable_status = "N/A"

            # Step 5.5: WhatIf margin eligibility check (definitive shortable status)
            # Uses current price for margin calculation
            # Sequential execution after tick 46/89 completes - provides definitive answer
            # on whether the stock can actually be shorted (not just SLB availability)
            if current_price and current_price > 0:
                shortable_via_margin = self.check_margin_eligibility(symbol, contract, current_price)
            else:
                # No valid current price - cannot perform WhatIf check
                logger.warning(f"{symbol}: No valid current price for WhatIf check, using N/A")
                shortable_via_margin = 'N/A'

            logger.info(f"{symbol}: Margin eligibility check result: {shortable_via_margin}")

            # Step 6: Get fundamental data from IBKR (WITH CACHING) - only borrow fee rate
            # Skip in historical mode as it may require current market connection
            if self.args.historical_mode:
                fundamental_data = {'borrow_fee_rate': None}
                logger.debug(f"{symbol}: [Historical Mode] Skipping IBKR fundamental data fetch")
            else:
                fundamental_data = self.get_fundamental_data(symbol, contract)

            # Step 7: Get yfinance data (WITH CACHING) - Float, Net Income TTM, Institutional %, Sector, Industry
            # This works in both live and historical mode (uses internet, not IB connection)
            yfinance_data = self.get_yfinance_data(symbol)

            # Step 8: Get borrow fee rate - prioritize tick 499 (market data) over fundamental data
            # In historical mode, this will be None
            if self.args.historical_mode:
                borrow_fee_rate = None
            else:
                borrow_fee_rate = borrow_info.get('borrow_fee_rate') or fundamental_data.get('borrow_fee_rate')

            # Step 9: Detect OTC status with multi-layer approach (IBKR + yfinance fallback)
            otc_status = self.detect_otc_status(symbol, contract, yfinance_data=yfinance_data)

            logger.debug(f"{symbol}: time0: ${checkpoint_a_price:.2f} | peak: ${peak_price:.2f} ({peak_pct:.2f}%) | "
                        f"current: ${current_price:.2f} ({live_pct:.2f}%) | "
                        f"OTC: {otc_status['is_otc_str']} ({otc_status['detection_method']})")

            # Return complete result with peak-based metrics
            return {
                'symbol': symbol,
                'checkpoint_a_price': checkpoint_a_price,
                'current_price': current_price,
                'peak_price': peak_price,
                'peak_time': peak_time,
                'gain_percent': live_pct,  # Current gain (for live_percentage column)
                'peak_percent': peak_pct,  # Peak gain (for threshold checking)
                'crossed_threshold': peak_pct >= percent_threshold,  # NOW BASED ON PEAK
                # OTC STATUS (from contract exchange):
                'is_otc': otc_status['is_otc'],
                'is_otc_str': otc_status['is_otc_str'],
                # FUNDAMENTAL FIELDS (from yfinance):
                'float_shares': yfinance_data.get('float_shares'),
                'net_income_ttm': yfinance_data.get('net_income_ttm'),
                'institutional_pct': yfinance_data.get('institutional_pct'),
                # SECTOR/INDUSTRY (from yfinance):
                'sector': yfinance_data.get('sector'),
                'industry': yfinance_data.get('industry'),
                # SHORTABLE INFO (from IBKR live market data):
                'shortable_status': shortable_status,  # IBKR-style label (HIGH_AVAIL/MODERATE_AVAIL/LOW_AVAIL/NOT_SHORTABLE/N/A)
                'shortable_difficulty': shortable_difficulty,  # Raw tick 46 value (0.0-3.0+)
                'shortable_shares': shortable_shares_count,  # Raw tick 89 count (exact shares available)
                # MARGIN ELIGIBILITY (from WhatIf order preview):
                'shortable_via_margin': shortable_via_margin,  # Definitive margin check (YES/NO/N/A)
                # BORROW FEE (from IBKR): tick 499 (market data) or fundamental data fallback
                'borrow_fee_rate': borrow_fee_rate
            }

        except Exception as e:
            logger.error(f"Error processing {symbol}: {e}")
            return None

    def update_tracked_stocks_prices(self, tracked_stocks: dict) -> int:
        """
        Update prices for all tracked stocks using current market data.
        Used when scanner returns 0 stocks but we still want to refresh tracked stocks.

        Args:
            tracked_stocks: Dictionary of tracked stocks from app_state["tracked_stocks"]

        Returns:
            Count of successfully updated stocks
        """
        updated_count = 0

        if not tracked_stocks:
            return 0

        logger.info(f"Updating prices for {len(tracked_stocks)} tracked stocks (scanner returned 0)...")

        for symbol, stock_data in tracked_stocks.items():
            try:
                # Get current price from market data
                # Use the same contract creation logic as process_single_stock
                contract = Contract()
                contract.symbol = symbol
                contract.secType = "STK"
                contract.exchange = stock_data.get('exchange', 'SMART')
                contract.currency = "USD"

                # Request current market price
                req_id = self.next_req_id()
                self.reqMktData(req_id, contract, "", False, False, [])

                # Wait for price update (timeout: 2 seconds)
                timeout = 2.0
                start_time = time.time()
                current_price = None

                while time.time() - start_time < timeout:
                    if req_id in app_state.get("market_data_prices", {}):
                        current_price = app_state["market_data_prices"][req_id]
                        break
                    time.sleep(0.05)

                # Cancel market data request
                self.cancelMktData(req_id)

                if current_price and current_price > 0:
                    # Update GapL(%) based on current price vs CP-A($)
                    checkpoint_a_price = stock_data.get('CP-A($)')
                    if checkpoint_a_price and checkpoint_a_price > 0:
                        live_percentage = ((current_price - checkpoint_a_price) / checkpoint_a_price) * 100

                        stock_data['GapL(%)'] = round(live_percentage, 2)
                        stock_data['PriceL($)'] = current_price  # Store current price for PriceL($) column
                        stock_data['LastUpdate'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                        updated_count += 1
                        logger.debug(f"Updated {symbol}: GapL(%)={live_percentage:.2f}%")
                else:
                    logger.warning(f"Failed to get current price for {symbol}")

            except Exception as e:
                logger.warning(f"Failed to update price for {symbol}: {e}")
                continue

        logger.info(f"Successfully updated {updated_count}/{len(tracked_stocks)} tracked stocks")
        return updated_count

    def process_stocks(self, stocks, scan_iteration=1):
        """
        Process stocks from scanner with parallel execution (batched).
        This is the core logic for accumulative tracking with PEAK-BASED selection.

        Updates ALL tracked stocks (not just those in scanner results).
        Once a stock is tracked, it stays tracked forever (add-only behavior).

        Args:
            stocks: List of stock dictionaries from scanner
            scan_iteration: Current scan iteration number (1 = first scan)
        """
        percent_threshold = self.args.gap_percentage
        tracked_stocks = app_state["tracked_stocks"]
        time0_dt = app_state["time0_datetime"]
        is_first_scan = (scan_iteration == 1)

        logger.info(f"Processing {len(stocks)} stocks from scanner (threshold: {percent_threshold}%, currently tracked: {len(tracked_stocks)}, scan #{scan_iteration})...")

        new_stocks_count = 0
        updated_from_scanner_count = 0
        scanner_symbols = set()

        # STEP 1: Process scanner results in batches
        batch_size = 20
        for batch_start in range(0, len(stocks), batch_size):
            batch = stocks[batch_start:batch_start + batch_size]
            batch_num = (batch_start // batch_size) + 1
            total_batches = (len(stocks) + batch_size - 1) // batch_size

            logger.info(f"Processing scanner batch {batch_num}/{total_batches} ({len(batch)} stocks)...")

            # Process batch in parallel using ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=SCAN_MAX_WORKERS) as executor:
                # Submit all stocks in batch for parallel processing
                # Determine is_first_scan per stock: True if not in peak_cache
                future_to_stock = {}
                for stock_info in batch:
                    symbol = stock_info['symbol']
                    # First scan for this stock if not in peak cache
                    stock_first_scan = symbol not in app_state.get("peak_prices", {})
                    future = executor.submit(self.process_single_stock, stock_info, percent_threshold, stock_first_scan)
                    future_to_stock[future] = stock_info

                # Collect results as they complete
                for future in as_completed(future_to_stock):
                    result = future.result()
                    if result is None:
                        continue

                    symbol = result['symbol']
                    scanner_symbols.add(symbol)

                    # Check if stock is already being tracked
                    if symbol not in tracked_stocks:
                        # NEW STOCK: Check if it crosses threshold (based on PEAK)
                        if result['crossed_threshold']:
                            # Record Checkpoint-B as the PEAK moment
                            checkpoint_b_time = format_time_for_gtt(result['peak_time'], date_only=app_state["use_date_a_mode"])
                            checkpoint_a_time = format_time_for_gtt(time0_dt, date_only=app_state["use_date_a_mode"])

                            # DEFENSIVE CHECK: Ensure Checkpoint-B is AFTER Checkpoint-A
                            if result['peak_time'] <= time0_dt.replace(tzinfo=None):
                                logger.critical(f"{symbol}: INVALID - Checkpoint-B ({checkpoint_b_time}) is not after Checkpoint-A ({checkpoint_a_time}). Skipping stock.")
                                continue  # Skip this stock

                            stock_data = {
                                'symbol': symbol,
                                'checkpoint_a_time': checkpoint_a_time,
                                'checkpoint_a_price': result['checkpoint_a_price'],
                                'checkpoint_b_time': checkpoint_b_time,
                                'checkpoint_b_price': result['peak_price'],  # PEAK price, not current
                                'gain_percent': result['peak_percent'],  # PEAK percentage
                                'live_percentage': result['gain_percent'],  # Current percentage
                                'current_price': result.get('current_price'),  # Live price for PriceL($) column
                                'shortable_status': result['shortable_status'],
                                # SECTOR/INDUSTRY FIELDS:
                                'sector': result.get('sector'),
                                'industry': result.get('industry'),
                                # OTC STATUS:
                                'is_otc': result.get('is_otc', False),
                                'is_otc_str': result.get('is_otc_str', '-'),
                                # FUNDAMENTAL FIELDS:
                                'float_shares': result.get('float_shares'),
                                'net_income_ttm': result.get('net_income_ttm'),
                                'institutional_pct': result.get('institutional_pct'),
                                'borrow_fee_rate': result.get('borrow_fee_rate'),
                                'shortable_shares': result.get('shortable_shares'),
                                'shortable_difficulty': result.get('shortable_difficulty'),
                                'shortable_via_margin': result.get('shortable_via_margin'),
                                'last_update': datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                            }

                            # Calculate xVol for date-A mode
                            if app_state["use_date_a_mode"]:
                                pdv_result = self.previous_day_volume_calculator(symbol, app_state["time0_datetime"])
                                stock_data['xvol'] = pdv_result['xvol']
                            else:
                                stock_data['xvol'] = None

                            # Trade decision will be evaluated AFTER enrichment
                            stock_data['trade_decision'] = "NO"  # Placeholder, will be re-evaluated

                            # Add to tracked stocks (in-memory only)
                            tracked_stocks[symbol] = stock_data

                            # Log with visual highlighting for trade decision
                            trade_marker = " 🟢 TRADE: YES" if stock_data['trade_decision'] == "YES" else ""
                            logger.info(f"✓ NEW: {symbol} crossed threshold - PEAK: {result['peak_percent']:.2f}% (${result['peak_price']:.2f} at {checkpoint_b_time}), LIVE: {result['gain_percent']:.2f}%{trade_marker}")
                            new_stocks_count += 1

                    else:
                        # EXISTING STOCK: Update live_percentage and potentially update peak
                        live_pct = result['gain_percent']
                        tracked_stocks[symbol]['live_percentage'] = live_pct
                        tracked_stocks[symbol]['current_price'] = result.get('current_price')
                        tracked_stocks[symbol]['shortable_status'] = result['shortable_status']

                        # Check if peak has increased (update Checkpoint-B)
                        if result['peak_price'] > tracked_stocks[symbol]['checkpoint_b_price']:
                            # DEFENSIVE CHECK: Ensure new peak is AFTER time0
                            peak_time_naive = result['peak_time']
                            if peak_time_naive <= time0_dt.replace(tzinfo=None):
                                logger.critical(f"{symbol}: INVALID PEAK UPDATE - Peak time ({format_time_for_gtt(peak_time_naive)}) is not after time0. Ignoring update.")
                            else:
                                tracked_stocks[symbol]['checkpoint_b_price'] = result['peak_price']
                                tracked_stocks[symbol]['checkpoint_b_time'] = format_time_for_gtt(result['peak_time'])
                                tracked_stocks[symbol]['gain_percent'] = result['peak_percent']
                                logger.info(f"↑ PEAK UPDATE: {symbol} new peak at {result['peak_percent']:.2f}% (${result['peak_price']:.2f})")

                        # Update sector/industry data (may have changed or been enriched)
                        tracked_stocks[symbol]['sector'] = result.get('sector')
                        tracked_stocks[symbol]['industry'] = result.get('industry')
                        # Update OTC status (may have changed or been enriched)
                        tracked_stocks[symbol]['is_otc'] = result.get('is_otc', False)
                        tracked_stocks[symbol]['is_otc_str'] = result.get('is_otc_str', '-')
                        # Update fundamental data (may have changed or been enriched)
                        tracked_stocks[symbol]['float_shares'] = result.get('float_shares')
                        tracked_stocks[symbol]['net_income_ttm'] = result.get('net_income_ttm')
                        tracked_stocks[symbol]['institutional_pct'] = result.get('institutional_pct')
                        tracked_stocks[symbol]['borrow_fee_rate'] = result.get('borrow_fee_rate')
                        tracked_stocks[symbol]['shortable_shares'] = result.get('shortable_shares')
                        tracked_stocks[symbol]['shortable_difficulty'] = result.get('shortable_difficulty')
                        tracked_stocks[symbol]['shortable_via_margin'] = result.get('shortable_via_margin')
                        tracked_stocks[symbol]['last_update'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                        # Update xVol for date-A mode
                        if app_state["use_date_a_mode"]:
                            pdv_result = self.previous_day_volume_calculator(symbol, app_state["time0_datetime"])
                            tracked_stocks[symbol]['xvol'] = pdv_result['xvol']

                        # Trade decision will be re-evaluated AFTER enrichment (removed from here)

                        logger.debug(f"↻ UPDATE from scanner: {symbol} live%: {live_pct:.2f}%")
                        updated_from_scanner_count += 1

            logger.info(f"Scanner batch {batch_num}/{total_batches} complete")

        # STEP 2: Update tracked stocks NOT in current scanner results
        # These are stocks that crossed threshold before but may have dropped below it
        tracked_not_in_scanner = [sym for sym in tracked_stocks.keys() if sym not in scanner_symbols]

        if tracked_not_in_scanner:
            logger.info(f"Updating {len(tracked_not_in_scanner)} previously tracked stocks not in current scanner results...")
            updated_from_tracked_count = 0

            # Process in batches
            for batch_start in range(0, len(tracked_not_in_scanner), batch_size):
                batch_symbols = tracked_not_in_scanner[batch_start:batch_start + batch_size]
                batch_num = (batch_start // batch_size) + 1
                total_batches = (len(tracked_not_in_scanner) + batch_size - 1) // batch_size

                logger.info(f"Processing tracked-only batch {batch_num}/{total_batches} ({len(batch_symbols)} stocks)...")

                # Process in parallel
                with ThreadPoolExecutor(max_workers=SCAN_MAX_WORKERS) as executor:
                    futures = {}
                    for symbol in batch_symbols:
                        # Get contract from cached contracts or create new one
                        if symbol in app_state["contracts"]:
                            contract = app_state["contracts"][symbol]
                        else:
                            # Create basic contract
                            contract = Contract()
                            contract.symbol = symbol
                            contract.secType = "STK"
                            contract.exchange = "SMART"
                            contract.currency = "USD"
                            app_state["contracts"][symbol] = contract

                        # Submit for processing (without threshold check, use cached peak)
                        stock_info = {'symbol': symbol, 'contract': contract}
                        # Pass False for is_first_scan since these are already tracked
                        futures[executor.submit(self.process_single_stock, stock_info, 0, False)] = symbol

                    # Collect results
                    for future in as_completed(futures):
                        symbol = futures[future]
                        result = future.result()

                        if result and symbol in tracked_stocks:
                            # Update live percentage and fundamental data (regardless of threshold)
                            live_pct = result['gain_percent']
                            tracked_stocks[symbol]['live_percentage'] = live_pct
                            tracked_stocks[symbol]['shortable_status'] = result['shortable_status']

                            # Check if peak has increased (update Checkpoint-B)
                            if result['peak_price'] > tracked_stocks[symbol]['checkpoint_b_price']:
                                tracked_stocks[symbol]['checkpoint_b_price'] = result['peak_price']
                                tracked_stocks[symbol]['checkpoint_b_time'] = format_time_for_gtt(result['peak_time'])
                                tracked_stocks[symbol]['gain_percent'] = result['peak_percent']
                                logger.info(f"↑ PEAK UPDATE: {symbol} new peak at {result['peak_percent']:.2f}% (${result['peak_price']:.2f})")

                            # Update sector/industry data
                            tracked_stocks[symbol]['sector'] = result.get('sector')
                            tracked_stocks[symbol]['industry'] = result.get('industry')
                            # Update fundamental data
                            tracked_stocks[symbol]['float_shares'] = result.get('float_shares')
                            tracked_stocks[symbol]['net_income_ttm'] = result.get('net_income_ttm')
                            tracked_stocks[symbol]['institutional_pct'] = result.get('institutional_pct')
                            tracked_stocks[symbol]['borrow_fee_rate'] = result.get('borrow_fee_rate')
                            tracked_stocks[symbol]['shortable_shares'] = result.get('shortable_shares')
                            tracked_stocks[symbol]['shortable_difficulty'] = result.get('shortable_difficulty')
                            tracked_stocks[symbol]['shortable_via_margin'] = result.get('shortable_via_margin')
                            tracked_stocks[symbol]['last_update'] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

                            # Update xVol for date-A mode
                            if app_state["use_date_a_mode"]:
                                pdv_result = self.previous_day_volume_calculator(symbol, app_state["time0_datetime"])
                                tracked_stocks[symbol]['xvol'] = pdv_result['xvol']

                            # Trade decision will be re-evaluated AFTER enrichment (removed from here)

                            logger.debug(f"↻ UPDATE from tracked: {symbol} live%: {live_pct:.2f}%")
                            updated_from_tracked_count += 1

                logger.info(f"Tracked-only batch {batch_num}/{total_batches} complete")

            logger.info(f"Updated {updated_from_tracked_count} previously tracked stocks not in scanner")
        else:
            logger.info("All tracked stocks were in scanner results")
            updated_from_tracked_count = 0

        total_updated = updated_from_scanner_count + updated_from_tracked_count

        logger.info(f"Scan complete: {new_stocks_count} new, {updated_from_scanner_count} updated (scanner), {updated_from_tracked_count} updated (tracked-only), Total tracked: {len(tracked_stocks)}")

        # ========================================
        # ENRICHMENT PHASE: Perform enrichment BEFORE trade evaluation
        # ========================================
        if tracked_stocks:
            logger.info("Starting enrichment phase (borrow fees + trends)...")

            # Write temporary GTT file for enrichment
            gtt_path = app_state["gtt_file_path"]
            write_complete_gtt(tracked_stocks, gtt_path)

            # Enrich file with borrow fees
            enrich_gtt_with_ftp_data(gtt_path)

            # Enrich file with trend data
            enrich_gtt_with_trend_data(gtt_path)

            # Reload enriched columns (BF1, BF2, BF_BEST, 1yrT, 6moT, 3moT) back into memory
            reload_enriched_columns(tracked_stocks, gtt_path)

            logger.info("Enrichment phase complete")

        # ========================================
        # TRADE EVALUATION PHASE: Evaluate ALL stocks with enriched data
        # ========================================
        trade_yes_count = 0
        if any([
            self.args.gap_larger_then is not None,
            self.args.institution_smaller_then is not None,
            self.args.float_smaller_then is not None,
            self.args.price_smaller_then is not None,
            self.args.borrow_fee_smaller_then is not None
        ]):
            logger.info("Evaluating trade decisions with enriched data...")

            for symbol, stock_data in tracked_stocks.items():
                stock_data['trade_decision'] = evaluate_trade_decision(stock_data, self.args)
                if stock_data['trade_decision'] == 'YES':
                    trade_yes_count += 1
                    logger.info(f"🟢 {symbol}: Trade decision = YES")

            logger.info(f"🟢 TRADE DECISION: {trade_yes_count}/{len(tracked_stocks)} stocks meet all criteria (YES)")
        else:
            # Trade decision feature not enabled, set all to NO
            for symbol, stock_data in tracked_stocks.items():
                stock_data['trade_decision'] = "NO"

        # Return statistics along with tracked_stocks
        return {
            'tracked_stocks': tracked_stocks,
            'stats': {
                'new': new_stocks_count,
                'updated_scanner': updated_from_scanner_count,
                'updated_tracked': updated_from_tracked_count,
                'total_tracked': len(tracked_stocks)
            }
        }


    def run_continuous_scan(self):
        """Run continuous scanning with accumulative tracking and periodic GTT file overwrites."""
        try:
            # Connect to IB
            if not self.connect():
                return None

            # Setup GTT file
            gtt_path = app_state["gtt_file_path"]
            output_dir = Path(self.args.output_dir)
            output_dir.mkdir(exist_ok=True, parents=True)

            # Load existing GTT if present (resume support)
            app_state["tracked_stocks"] = load_existing_gtt(gtt_path)

            time0_str = format_time_for_gtt(app_state["time0_datetime"])

            # Print startup banner
            self.console.print("\n" + "="*80)
            if self.args.historical_mode:
                self.console.print(f"[bold cyan]Gainers Scanner (GSS) - HISTORICAL MODE[/bold cyan]")
            else:
                self.console.print(f"[bold cyan]Gainers Scanner (GSS) - Continuous Mode[/bold cyan]")
            self.console.print("="*80)
            self.console.print(f"Time0 (Checkpoint-A): {time0_str}")
            self.console.print(f"Gap Percentage Threshold: {self.args.gap_percentage}%")
            if self.args.historical_mode:
                time0_dt = app_state["time0_datetime"]
                end_date = time0_dt.date() + datetime.timedelta(days=1)
                end_time_str = f"{end_date.strftime('%Y-%m-%d')}/16:00"
                self.console.print(f"[yellow]Historical Mode: Analyzing time0 → {end_time_str} (using historical data only)[/yellow]")
            self.console.print(f"Refresh Interval: {self.args.refresh_interval}s")
            self.console.print(f"Output File: {gtt_path}")
            self.console.print(f"Tracked stocks at startup: {len(app_state['tracked_stocks'])}")
            self.console.print("="*80 + "\n")

            scan_iteration = 0

            # Continuous scanning loop
            try:
                while not app_state["shutdown"].is_set():
                    # Check for connection loss and attempt reconnection
                    if app_state["connection_lost"].is_set():
                        logger.warning("Connection lost detected. Initiating reconnection...")
                        self.console.print("\n[bold red]⚠ Connection lost detected![/bold red]")

                        # Attempt reconnection
                        reconnect_success = self.reconnect_with_retry(max_retries=5)

                        if not reconnect_success:
                            # reconnect_with_retry already set shutdown flag
                            logger.error("Failed to reconnect. Exiting scan loop.")
                            break

                        # Continue with next scan iteration after successful reconnection
                        self.console.print("[green]Resuming scanning...[/green]")

                    scan_iteration += 1
                    self.console.print(f"\n[bold yellow]>>> Scan iteration {scan_iteration} starting...[/bold yellow]")

                    # Run scanner to get stock universe
                    stocks = self.run_scanner()

                    if stocks:
                        # Process stocks (track new crossings, update ALL tracked stocks)
                        result = self.process_stocks(stocks, scan_iteration)
                        tracked_stocks = result['tracked_stocks']
                        stats = result['stats']

                        # Update global state
                        app_state["results_table"] = list(tracked_stocks.values())
                        app_state["last_update"] = datetime.datetime.now()
                        app_state["tracked_stocks"] = tracked_stocks  # Ensure persistence

                        # Calculate Kelly positions (after all trade_decision evaluations)
                        calculate_kelly_positions(tracked_stocks, self.args)

                        # Print status summary
                        self.console.print(f"\n[bold green]Scan iteration {scan_iteration} complete:[/bold green]")
                        self.console.print(f"  • New stocks added: {stats['new']}")
                        self.console.print(f"  • Updated from scanner: {stats['updated_scanner']}")
                        self.console.print(f"  • Updated from tracked: {stats['updated_tracked']}")
                        self.console.print(f"  • [bold]Total tracked stocks: {stats['total_tracked']}[/bold]")

                    else:
                        # Scanner returned 0 stocks - refresh existing tracked stocks only
                        tracked_stocks = app_state["tracked_stocks"]

                        if tracked_stocks:
                            # Update prices for all tracked stocks using market data
                            self.console.print(f"\n[yellow]⚠️  Scanner returned 0 stocks. Refreshing {len(tracked_stocks)} tracked stocks...[/yellow]")

                            # Request market data updates for all tracked symbols
                            updated_count = self.update_tracked_stocks_prices(tracked_stocks)

                            # Update global state
                            app_state["results_table"] = list(tracked_stocks.values())
                            app_state["last_update"] = datetime.datetime.now()

                            # Perform enrichment and re-evaluate trade decisions (same as process_stocks does)
                            logger.info("Starting enrichment phase (borrow fees + trends)...")
                            write_complete_gtt(tracked_stocks, gtt_path)
                            enrich_gtt_with_ftp_data(gtt_path)
                            enrich_gtt_with_trend_data(gtt_path)
                            reload_enriched_columns(tracked_stocks, gtt_path)
                            logger.info("Enrichment phase complete")

                            # Re-evaluate trade decisions with enriched data
                            if any([
                                self.args.gap_larger_then is not None,
                                self.args.institution_smaller_then is not None,
                                self.args.float_smaller_then is not None,
                                self.args.price_smaller_then is not None,
                                self.args.borrow_fee_smaller_then is not None
                            ]):
                                for symbol, stock_data in tracked_stocks.items():
                                    stock_data['trade_decision'] = evaluate_trade_decision(stock_data, self.args)

                            # Calculate Kelly positions (after price updates and trade decisions)
                            calculate_kelly_positions(tracked_stocks, self.args)

                            self.console.print(f"  • Tracked stocks refreshed: {updated_count}/{len(tracked_stocks)}")
                        else:
                            self.console.print(f"\n[dim]Scanner returned 0 stocks. No tracked stocks to refresh.[/dim]")

                    # ALWAYS write GTT after each scan (even if 0 new stocks from scanner)
                    if tracked_stocks:
                        # Write complete GTT file (overwrite)
                        # NOTE: Enrichment already happened in process_stocks(), no need to re-enrich
                        write_complete_gtt(tracked_stocks, gtt_path)

                        self.console.print(f"  • GTT file updated: {gtt_path}")

                    # Calculate dynamic refresh interval with exponential backoff for timeouts
                    if app_state["scanner_timeout_count"] >= 3:
                        # Apply exponential backoff: 3s -> 10s -> 30s -> 60s (max)
                        backoff_multiplier = min(2 ** (app_state["scanner_timeout_count"] - 2), 20)  # 1, 2, 4, 8, 16, 20(max)
                        current_interval = min(self.args.refresh_interval * backoff_multiplier, 60)
                        self.console.print(f"\n[yellow]Scanner service unavailable. Using backoff interval: {current_interval}s[/yellow]")
                        self.console.print(f"[dim]Next scan in {current_interval} seconds... (Press Ctrl+C to stop)[/dim]")
                    else:
                        current_interval = self.args.refresh_interval
                        self.console.print(f"\n[dim]Next scan in {current_interval} seconds... (Press Ctrl+C to stop)[/dim]")

                    # Wait before next scan
                    time.sleep(current_interval)

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Scan interrupted by user (Ctrl+C)[/yellow]")

        except Exception as e:
            logger.error(f"Error during scan execution: {e}", exc_info=True)
            return None

        finally:
            self.disconnect()
            total_tracked = len(app_state["tracked_stocks"])
            self.console.print("\n" + "="*80)
            self.console.print(f"[green]Scanner stopped. Total stocks tracked: {total_tracked}[/green]")
            self.console.print(f"[green]Results saved to: {app_state['gtt_file_path']}[/green]")
            self.console.print("="*80 + "\n")

    def run_single_scan(self):
        """Single scan mode (not continuous) - mainly for testing."""
        try:
            # Connect to IB
            if not self.connect():
                return None

            # Setup GTT file
            gtt_path = app_state["gtt_file_path"]
            output_dir = Path(self.args.output_dir)
            output_dir.mkdir(exist_ok=True, parents=True)

            # Load existing GTT if present
            app_state["tracked_stocks"] = load_existing_gtt(gtt_path)

            time0_str = format_time_for_gtt(app_state["time0_datetime"])

            # Print startup banner
            self.console.print("\n" + "="*80)
            if self.args.historical_mode:
                self.console.print(f"[bold cyan]Gainers Scanner (GSS) - HISTORICAL MODE (Single Scan)[/bold cyan]")
            else:
                self.console.print(f"[bold cyan]Gainers Scanner (GSS) - Single Scan Mode[/bold cyan]")
            self.console.print("="*80)
            self.console.print(f"Time0 (Checkpoint-A): {time0_str}")
            self.console.print(f"Gap Percentage Threshold: {self.args.gap_percentage}%")
            if self.args.historical_mode:
                time0_dt = app_state["time0_datetime"]
                end_date = time0_dt.date() + datetime.timedelta(days=1)
                end_time_str = f"{end_date.strftime('%Y-%m-%d')}/16:00"
                self.console.print(f"[yellow]Historical Mode: Analyzing time0 → {end_time_str} (using historical data only)[/yellow]")
            self.console.print(f"Output File: {gtt_path}")
            self.console.print(f"Tracked stocks at startup: {len(app_state['tracked_stocks'])}")
            self.console.print("="*80 + "\n")

            # Run scanner to get stock universe
            stocks = self.run_scanner()

            if not stocks:
                logger.error("No stocks found from scanner. Exiting.")
                return None

            # Process stocks (scan_iteration = 1 for single scan)
            result = self.process_stocks(stocks, scan_iteration=1)
            tracked_stocks = result['tracked_stocks']
            stats = result['stats']

            # Write complete GTT file
            write_complete_gtt(tracked_stocks, gtt_path)

            # Enrich GTT with FTP borrow fee data (optional, non-blocking)
            enrich_gtt_with_ftp_data(gtt_path)

            # Enrich GTT with trend slope analysis (optional, non-blocking)
            enrich_gtt_with_trend_data(gtt_path)

            # Display results summary
            self.console.print("\n" + "="*80)
            self.console.print(f"[bold green]Single scan complete:[/bold green]")
            self.console.print(f"  • New stocks added: {stats['new']}")
            self.console.print(f"  • Updated from scanner: {stats['updated_scanner']}")
            self.console.print(f"  • Updated from tracked: {stats['updated_tracked']}")
            self.console.print(f"  • [bold]Total tracked stocks: {stats['total_tracked']}[/bold]")
            self.console.print(f"  • Results saved to: {gtt_path}")
            self.console.print("="*80 + "\n")

            logger.info(f"Single scan complete. Tracked: {len(tracked_stocks)} stocks")
            logger.info(f"Results saved to: {gtt_path}")

            return gtt_path

        except Exception as e:
            logger.error(f"Error during scan execution: {e}", exc_info=True)
            return None

        finally:
            self.disconnect()

# =====================
# Terminal Window Launcher
# =====================
def launch_in_new_terminal(args):
    """Launch the script in a new terminal window."""
    script_path = Path(__file__).absolute()
    python_exec = sys.executable

    # Build command with all arguments
    cmd_args = [
        '--gap-percentage', str(args.gap_percentage),
        '--client-id', str(args.client_id),
        '--output-dir', args.output_dir,
        '--host', args.host,
        '--port', str(args.port),
        '--max-stocks', str(args.max_stocks),
        '--min-price', str(args.min_price),
        '--refresh-interval', str(args.refresh_interval),
        '--no-new-window'  # Important: prevent infinite recursion
    ]

    # Add checkpoint argument (either --time0 or --date-A)
    if args.checkpoint_mode == 'time0':
        cmd_args.extend(['--time0', args.time0])
    elif args.checkpoint_mode == 'date-A':
        cmd_args.extend(['--date-A', args.date_A])

    # Add log argument only if specified
    if args.log:
        cmd_args.extend(['--log', args.log])

    # Add historical-mode flag if enabled
    if args.historical_mode:
        cmd_args.append('--historical-mode')

    # Add trade decision arguments if specified
    if args.gap_larger_then is not None:
        cmd_args.extend(['--gap_larger_then', str(args.gap_larger_then)])
    if args.institution_smaller_then is not None:
        cmd_args.extend(['--institution_smaller_then', str(args.institution_smaller_then)])
    if args.float_smaller_then is not None:
        cmd_args.extend(['--float_smaller_then', str(args.float_smaller_then)])
    if args.price_smaller_then is not None:
        cmd_args.extend(['--price_smaller_then', str(args.price_smaller_then)])
    if args.borrow_fee_smaller_then is not None:
        cmd_args.extend(['--borrow_fee_smaller_then', str(args.borrow_fee_smaller_then)])

    # Forward RTPR (LastPR/Headline) options to the relaunched window
    if getattr(args, 'config', None):
        cmd_args.extend(['--config', args.config])
    if getattr(args, 'no_rtpr', False):
        cmd_args.append('--no-rtpr')

    # Detect available terminal emulator
    terminals = [
        ['gnome-terminal', '--', python_exec, str(script_path)] + cmd_args,
        ['xterm', '-e', python_exec, str(script_path)] + cmd_args,
        ['konsole', '-e', python_exec, str(script_path)] + cmd_args,
        ['xfce4-terminal', '-e', f"{python_exec} {script_path} {' '.join(cmd_args)}"],
    ]

    for terminal_cmd in terminals:
        try:
            subprocess.Popen(terminal_cmd)
            print(f"Launched scanner in new terminal window")
            print(f"Output directory: {args.output_dir}")
            return True
        except FileNotFoundError:
            continue

    print("Error: No compatible terminal emulator found.")
    print("Available terminals: gnome-terminal, xterm, konsole, xfce4-terminal")
    print("Running in current terminal instead...")
    return False

# =====================
# Main Entry Point
# =====================
def main():
    """Main function."""
    # --- Low virtual-memory guard -------------------------------------------
    # glibc reads MALLOC_ARENA_MAX from the environment at process startup, so
    # setting os.environ here would be too late for the current process. A
    # one-time re-exec (before any worker threads are created) makes it stick,
    # capping the number of 64 MB malloc arenas that a multi-threaded Python
    # process would otherwise reserve — the main driver of high VSZ here.
    # With pandas/yfinance now imported lazily, this throwaway first pass is
    # cheap. The guard is skipped once the var is present (second pass, or when
    # a launcher already exported it).
    if os.environ.get("MALLOC_ARENA_MAX") is None:
        os.environ["MALLOC_ARENA_MAX"] = "2"
        os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "131072")
        os.execv(sys.executable, [sys.executable] + sys.argv)
    # ------------------------------------------------------------------------

    global logger, _rtpr_api_key

    # Parse arguments
    args = parse_arguments()

    # Setup logging
    logger, log_file_path = setup_logging(args.log)

    # Load the RTPR.io API key for the LastPR/Headline columns (unless disabled)
    if args.no_rtpr:
        _rtpr_api_key = None
        logger.info("RTPR LastPR/Headline enrichment DISABLED (--no-rtpr)")
    else:
        _rtpr_api_key = load_rtpr_api_key(args.config)
        if _rtpr_api_key:
            logger.info(f"RTPR API key loaded (…{_rtpr_api_key[-4:]}) from {args.config}; LastPR/Headline enrichment ENABLED")
        else:
            logger.warning(f"No RTPR API key found in {args.config}; LastPR/Headline columns will show '-'")

    # Log the command line that launched the script
    command_line = ' '.join(sys.argv)
    logger.info("=" * 60)
    logger.info(f"Command: {command_line}")
    logger.info("=" * 60)
    logger.info("Gainers Scanner Script (GSS) - GUS-ATS")
    logger.info("=" * 60)

    # Parse and validate checkpoint (either time0 or date-A)
    if args.checkpoint_mode == 'time0':
        checkpoint_dt = parse_time0(args.time0)
        if not checkpoint_dt:
            logger.error("Invalid time0. Exiting.")
            return
        app_state["use_date_a_mode"] = False
        logger.info(f"Using time0 mode: Intraday checkpoint at {args.time0}")
    elif args.checkpoint_mode == 'date-A':
        checkpoint_dt = parse_date_a(args.date_A)
        if not checkpoint_dt:
            logger.error("Invalid date-A. Exiting.")
            return
        app_state["use_date_a_mode"] = True
        logger.info(f"Using date-A mode: Daily close price for {args.date_A}")

    # Set checkpoint in global state (stored as "time0_datetime" for compatibility)
    app_state["time0_datetime"] = checkpoint_dt

    # Generate GTT filename
    gtt_path = generate_gtt_filename(args.output_dir)
    app_state["gtt_file_path"] = gtt_path

    checkpoint_str = format_time_for_gtt(checkpoint_dt, date_only=app_state["use_date_a_mode"])

    if args.historical_mode:
        logger.info("=" * 60)
        logger.info("HISTORICAL MODE ENABLED")
        end_date = checkpoint_dt.date() + datetime.timedelta(days=1)
        end_time_str = f"{end_date.strftime('%Y-%m-%d')}/16:00"

        if app_state["use_date_a_mode"]:
            checkpoint_display = format_time_for_gtt(checkpoint_dt, date_only=True)
        else:
            checkpoint_display = format_time_for_gtt(checkpoint_dt, date_only=False)

        logger.info(f"Analysis Window: {checkpoint_display} → {end_time_str}")
        logger.info("Using historical data only (no live prices required)")
        logger.info("=" * 60)

    logger.info(f"Checkpoint-A: {checkpoint_str}")
    logger.info(f"Gap Percentage Threshold: {args.gap_percentage}%")
    logger.info(f"IB Gateway: {args.host}:{args.port}")
    logger.info(f"Client ID: {args.client_id}")
    logger.info(f"Output Directory: {args.output_dir}")
    logger.info(f"GTT File: {gtt_path}")
    logger.info(f"Log File: {log_file_path}")
    logger.info(f"Refresh Interval: {args.refresh_interval}s")
    logger.info("")

    # Launch in new terminal if requested
    if not args.no_new_window:
        if launch_in_new_terminal(args):
            # Parent process exits, child continues in new window
            return
        # If launch failed, continue in current terminal

    # Create scanner instance
    scanner = GainersScanner(args)

    # Run continuous scan
    scanner.run_continuous_scan()

    logger.info("Script terminated.")

if __name__ == "__main__":
    main()
