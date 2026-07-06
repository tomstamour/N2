#!/usr/bin/env python3
"""
Fundamentals Data Fetcher (FDF) - Enhanced IBKR Fundamental Data Fetcher for GTT Table
Enriches GusTradeTable files with fundamental metrics from IBKR API

Fetches: Float, Institutional %, Net Income, Short Interest, Market Data, and more
Integrates with Gainers Scanner Script (GSS) pipeline
"""

from ibapi.client import EClient
from ibapi.wrapper import EWrapper
from ibapi.contract import Contract
from ibapi.common import TickerId, TickAttrib
import threading
import time
import xml.etree.ElementTree as ET
import pandas as pd
from typing import Dict, Optional, List
from decimal import Decimal
import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeRemainingColumn
from rich.logging import RichHandler
import os

console = Console()


class IBKREnhancedData(EWrapper, EClient):
    """IBKR API client for fetching fundamental and market data"""

    def __init__(self, logger=None):
        EClient.__init__(self, self)
        self.logger = logger or logging.getLogger(__name__)
        self.fundamental_data = {}
        self.market_data = {}
        self.short_data = {}
        self.data_received = {}
        self.market_data_received = {}
        self.next_req_id = 1000
        self.lock = threading.Lock()

    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        """Error handling"""
        # Ignore connection status messages
        if errorCode in [2104, 2106, 2158, 2107, 2119, 2176]:
            self.logger.debug(f"Info {reqId} - Code: {errorCode}, Msg: {errorString}")
            return

        # Log errors
        self.logger.warning(f"Error {reqId} - Code: {errorCode}, Msg: {errorString}")

        if reqId in self.data_received:
            with self.lock:
                self.data_received[reqId] = True
        if reqId in self.market_data_received:
            with self.lock:
                self.market_data_received[reqId] = True

    def fundamentalData(self, reqId, data):
        """Callback when fundamental data is received"""
        self.logger.debug(f"Received fundamental data for request {reqId}")
        with self.lock:
            self.fundamental_data[reqId] = data
            self.data_received[reqId] = True

    def tickPrice(self, reqId, tickType, price, attrib):
        """Handle price ticks"""
        with self.lock:
            if reqId not in self.market_data:
                self.market_data[reqId] = {}

            # TickType 1 = Bid, 2 = Ask, 4 = Last
            if tickType == 4:  # Last price
                self.market_data[reqId]['last_price'] = price
            elif tickType == 1:  # Bid
                self.market_data[reqId]['bid'] = price
            elif tickType == 2:  # Ask
                self.market_data[reqId]['ask'] = price

    def tickSize(self, reqId, tickType, size):
        """Handle size ticks"""
        with self.lock:
            if reqId not in self.market_data:
                self.market_data[reqId] = {}

            # TickType 8 = Volume
            if tickType == 8:
                self.market_data[reqId]['volume'] = size

    def tickString(self, reqId, tickType, value):
        """Handle string ticks"""
        with self.lock:
            if reqId not in self.market_data:
                self.market_data[reqId] = {}

            # TickType 48 = RTVolume
            if tickType == 48:
                self.market_data[reqId]['rt_volume'] = value

    def tickGeneric(self, reqId, tickType, value):
        """Handle generic ticks - includes shortable shares and fee rate"""
        with self.lock:
            if reqId not in self.short_data:
                self.short_data[reqId] = {}

            # TickType 46 = Shortable shares (requested via generic tick 236)
            if tickType == 46:
                self.short_data[reqId]['shortable'] = value
            # TickType 499 = Borrow fee rate (if available)
            elif tickType == 499:
                self.short_data[reqId]['fee_rate'] = value

    def tickReqParams(self, tickerId, minTick, bboExchange, snapshotPermissions):
        """Confirms market data request parameters"""
        pass

    def parse_fundamental_data(self, xml_data: str, report_type: str = "ReportSnapshot") -> Dict:
        """
        Enhanced XML parsing with multiple fallback paths

        Parameters:
        -----------
        xml_data : str
            XML response from IBKR fundamental data request
        report_type : str
            Type of IBKR report (ReportSnapshot, ReportRatios, ReportsOwnership, ReportsFinSummary)
            Used for report-specific parsing logic if needed

        Returns:
        --------
        Dict with parsed metrics and _report_type key for tracking source
        """
        metrics = {
            'float': None,
            'float_shares': None,
            'institutional_pct': None,
            'net_income': None,
            'net_income_ttm': None,
            'revenue': None,
            'revenue_ttm': None,
            'eps': None,
            'pe_ratio': None,
            'market_cap': None,
            'shares_outstanding': None,
            'short_interest': None,
            'short_interest_ratio': None,
            'beta': None,
            'profit_margin': None,
            'roe': None,
            'debt_to_equity': None
        }

        try:
            root = ET.fromstring(xml_data)

            # Multiple paths for Float
            for path in ['.//Float', './/FloatShares', './/SharesFloat',
                        './/CommonSharesOutstanding', './/SharesOut']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['float_shares'] = float(elem.text)
                        metrics['float'] = elem.text
                        break
                    except ValueError:
                        pass

            # Shares Outstanding
            for path in ['.//SharesOutstanding', './/SharesOut', './/CommonSharesOutstanding']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['shares_outstanding'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Institutional ownership percentage (VERY IMPORTANT)
            for path in ['.//PctInstitutions', './/InstitutionalHoldingsPercent',
                        './/PercentInsiders', './/PctHeld']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['institutional_pct'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Net Income - try multiple paths
            for path in ['.//NetIncome', './/IncomeNet', './/NIAvailtoCommon']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    metrics['net_income'] = elem.text
                    break

            # TTM Net Income
            for path in ['.//TTMNetIncome', './/NetIncomeTTM', './/TwelveMonthNetIncome']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    metrics['net_income_ttm'] = elem.text
                    break

            # Revenue
            for path in ['.//Revenue', './/TotalRevenue', './/Sales']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    metrics['revenue'] = elem.text
                    break

            # TTM Revenue
            for path in ['.//TTMRevenue', './/RevenueTTM', './/TwelveMonthRevenue']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    metrics['revenue_ttm'] = elem.text
                    break

            # EPS
            for path in ['.//EPS', './/EPSBasic', './/EPSDiluted']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['eps'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # P/E Ratio
            for path in ['.//PERatio', './/PE', './/PriceToEarnings']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['pe_ratio'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Market Cap
            for path in ['.//MarketCap', './/MktCap', './/MarketCapitalization']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['market_cap'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Short Interest
            for path in ['.//ShortInterest', './/SharesShort', './/ShortInt']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['short_interest'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Short Interest Ratio (Days to Cover)
            for path in ['.//ShortIntRatio', './/DaysToCover', './/ShortRatio']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['short_interest_ratio'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Beta
            for path in ['.//Beta', './/BetaValue']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['beta'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Profit Margin
            for path in ['.//ProfitMargin', './/NetProfitMargin', './/NPM']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['profit_margin'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # ROE (Return on Equity)
            for path in ['.//ROE', './/ReturnOnEquity']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['roe'] = float(elem.text)
                        break
                    except ValueError:
                        pass

            # Debt to Equity
            for path in ['.//DebtToEquity', './/DE', './/DebtEquityRatio']:
                elem = root.find(path)
                if elem is not None and elem.text:
                    try:
                        metrics['debt_to_equity'] = float(elem.text)
                        break
                    except ValueError:
                        pass

        except ET.ParseError as e:
            self.logger.error(f"Error parsing XML: {e}")

        # Add report type to track data source
        metrics['_report_type'] = report_type

        return metrics


def create_contract(symbol: str, exchange: str = "SMART") -> Contract:
    """Create a contract object for a stock"""
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = exchange
    contract.currency = "USD"
    return contract


def merge_metrics(metrics_list: List[Dict], priority_order: List[str], logger=None) -> Dict:
    """
    Merge metrics from multiple report types with priority ordering

    When the same metric is available from multiple report types, use the value
    from the highest-priority source.

    Parameters:
    -----------
    metrics_list : List[Dict]
        List of metric dictionaries from different report types
        Each dict should have a '_report_type' key indicating its source
    priority_order : List[str]
        Report types in priority order (first = highest priority)
        Example: ["ReportRatios", "ReportsFinSummary", "ReportsOwnership", "ReportSnapshot"]
    logger : logging.Logger
        Logger instance for tracking data sources

    Returns:
    --------
    Dict
        Merged dictionary with best available data for each metric
        Includes '_source' suffix keys to track where each metric came from
    """

    if not metrics_list:
        return {}

    logger = logger or logging.getLogger(__name__)
    merged = {}

    # Get all possible metric keys from all dictionaries
    all_keys = set()
    for metrics_dict in metrics_list:
        all_keys.update(k for k in metrics_dict.keys() if k != '_report_type')

    # For each metric, find the best value according to priority order
    for metric_key in all_keys:
        found = False

        # Check each report type in priority order
        for report_type in priority_order:
            # Find the metrics dict for this report type
            for metrics_dict in metrics_list:
                if metrics_dict.get('_report_type') == report_type:
                    # Check if this dict has a non-None value for this metric
                    value = metrics_dict.get(metric_key)
                    if value is not None:
                        merged[metric_key] = value
                        merged[f'{metric_key}_source'] = report_type
                        logger.debug(f"Metric '{metric_key}' sourced from {report_type}")
                        found = True
                        break

            if found:
                break

        # If not found in any report type, set to None
        if not found:
            merged[metric_key] = None
            merged[f'{metric_key}_source'] = 'N/A'

    return merged


def fetch_complete_data(symbols: list, host: str = "127.0.0.1",
                       port: int = 4001, client_id: int = 1,
                       get_market_data: bool = True, logger=None) -> pd.DataFrame:
    """
    Fetch comprehensive fundamental and market data

    Parameters:
    -----------
    symbols : list
        List of stock symbols
    host : str
        IB Gateway host
    port : int
        IB Gateway port (4001 for live, 4002 for paper)
    client_id : int
        Unique client ID
    get_market_data : bool
        Whether to fetch real-time market data
    logger : logging.Logger
        Logger instance

    Returns:
    --------
    pd.DataFrame with comprehensive metrics
    """

    logger = logger or logging.getLogger(__name__)
    app = IBKREnhancedData(logger=logger)

    logger.info(f"Connecting to IB Gateway at {host}:{port} with client_id={client_id}")
    app.connect(host, port, client_id)

    api_thread = threading.Thread(target=app.run, daemon=True)
    api_thread.start()

    time.sleep(2)

    if not app.isConnected():
        logger.error("Failed to connect to IB Gateway")
        console.print("[red]❌ Failed to connect to IB Gateway[/red]")
        return pd.DataFrame()

    logger.info("Connected to IB Gateway")
    console.print("[green]✓[/green] Connected to IB Gateway\n")

    results = []
    req_id_to_symbol = {}
    market_req_to_symbol = {}

    # Step 1: Request fundamental data from multiple report types
    console.print("[cyan]📊 Requesting fundamental data (multiple report types)...[/cyan]")

    # Define report types to request (in priority order)
    report_types = ["ReportRatios", "ReportsOwnership", "ReportsFinSummary", "ReportSnapshot"]
    logger.info(f"Requesting {len(report_types)} report types for {len(symbols)} symbols")
    logger.info(f"Report types: {', '.join(report_types)}")

    total_requests = len(symbols) * len(report_types)

    for symbol in symbols:
        contract = create_contract(symbol)

        for report_type in report_types:
            req_id = app.next_req_id
            req_id_to_symbol[req_id] = (symbol, report_type)  # Track symbol AND report type

            with app.lock:
                app.data_received[req_id] = False

            app.reqFundamentalData(req_id, contract, report_type, [])
            logger.debug(f"Requested {report_type} for {symbol} (req_id: {req_id})")

            app.next_req_id += 1
            time.sleep(0.3)  # Rate limiting

    # Wait for fundamental data
    timeout = 60  # Increased timeout for multiple reports
    start_time = time.time()

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TimeRemainingColumn(),
        console=console
    ) as progress:
        task = progress.add_task("Fetching fundamental data...", total=total_requests)

        while time.time() - start_time < timeout:
            with app.lock:
                received = sum(1 for v in app.data_received.values() if v)
                progress.update(task, completed=received)

                if all(app.data_received.values()):
                    break
            time.sleep(0.5)

    # Step 2: Request market data with shortable info
    if get_market_data:
        console.print("\n[cyan]💹 Requesting market data (price, volume, borrow rate)...[/cyan]")
        logger.info(f"Requesting market data for {len(symbols)} symbols")

        for symbol in symbols:
            contract = create_contract(symbol)
            req_id = app.next_req_id
            market_req_to_symbol[req_id] = symbol

            with app.lock:
                app.market_data_received[req_id] = False

            # Generic tick 236 = Shortable shares (returns tickType 46)
            # NOTE: Tick 499 is INVALID for stocks (Error 321)
            app.reqMktData(req_id, contract, "236", False, False, [])
            logger.debug(f"Requested market data for {symbol} (req_id: {req_id})")

            app.next_req_id += 1
            time.sleep(0.2)

        # Wait for market data
        time.sleep(5)

        # Cancel market data subscriptions
        for req_id in market_req_to_symbol.keys():
            app.cancelMktData(req_id)

    # Step 3: Process all data
    console.print("\n[cyan]📈 Processing results and merging multi-report data...[/cyan]\n")
    logger.info("Processing fetched data from multiple report types")

    for symbol in symbols:
        result = {'symbol': symbol}

        # Get fundamental data from ALL report types for this symbol
        fund_req_ids = [k for k, (sym, rtype) in req_id_to_symbol.items() if sym == symbol]

        if fund_req_ids:
            metrics_list = []

            # Parse XML from each report type
            for req_id in fund_req_ids:
                with app.lock:
                    if req_id in app.fundamental_data:
                        xml_data = app.fundamental_data[req_id]
                        symbol_from_req, report_type = req_id_to_symbol[req_id]

                        # Parse with report type awareness
                        metrics = app.parse_fundamental_data(xml_data, report_type)
                        metrics_list.append(metrics)

                        logger.debug(f"Parsed {report_type} for {symbol}: {sum(1 for v in metrics.values() if v is not None and v != report_type)} metrics found")

            # Merge metrics from all report types with priority
            if metrics_list:
                merged_metrics = merge_metrics(metrics_list, report_types, logger)

                # Remove source tracking keys and _report_type before adding to result
                final_metrics = {k: v for k, v in merged_metrics.items()
                                if not k.endswith('_source') and k != '_report_type'}
                result.update(final_metrics)

                # Log which reports provided data
                sources = {k: v for k, v in merged_metrics.items() if k.endswith('_source') and v != 'N/A'}
                if sources:
                    logger.info(f"{symbol}: Data sourced from {len(set(sources.values()))} report types")
            else:
                logger.warning(f"No fundamental data received for {symbol} from any report type")

        # Get market data
        if get_market_data:
            mkt_req_id = [k for k, v in market_req_to_symbol.items() if v == symbol]
            if mkt_req_id:
                req_id = mkt_req_id[0]
                with app.lock:
                    if req_id in app.market_data:
                        mkt = app.market_data[req_id]
                        result['current_price'] = mkt.get('last_price')
                        result['bid'] = mkt.get('bid')
                        result['ask'] = mkt.get('ask')
                        result['volume'] = mkt.get('volume')

                    if req_id in app.short_data:
                        short = app.short_data[req_id]
                        result['shortable_shares'] = short.get('shortable')
                        result['borrow_fee_rate'] = short.get('fee_rate')

        results.append(result)

    app.disconnect()
    logger.info("Disconnected from IB Gateway")

    df = pd.DataFrame(results)
    console.print("[green]✓[/green] Data fetch complete!\n")
    return df


def update_gtt_table(gtt_df: pd.DataFrame, metrics_df: pd.DataFrame) -> pd.DataFrame:
    """
    Update GTT table with new fundamental metrics

    Parameters:
    -----------
    gtt_df : pd.DataFrame
        Original GusTradeTable DataFrame
    metrics_df : pd.DataFrame
        DataFrame with fetched metrics

    Returns:
    --------
    Updated GTT DataFrame with new columns
    """

    # Define column mapping from internal names to GTT output names
    column_mapping = {
        'float_shares': 'Float',
        'institutional_pct': 'Institutional_Pct',
        'short_interest': 'Short_Interest',
        'short_interest_ratio': 'Short_Interest_Ratio',
        'shortable_shares': 'Shortable_Shares',
        'borrow_fee_rate': 'Borrow_Fee_Rate',
        'net_income_ttm': 'Net_Income_TTM',
        'revenue_ttm': 'Revenue_TTM',
        'profit_margin': 'Profit_Margin',
        'roe': 'ROE',
        'debt_to_equity': 'Debt_To_Equity',
        'market_cap': 'Market_Cap',
        'shares_outstanding': 'Shares_Outstanding',
        'eps': 'EPS',
        'pe_ratio': 'PE_Ratio',
        'beta': 'Beta',
        'current_price': 'FDF_Current_Price',
        'volume': 'FDF_Volume',
        'bid': 'FDF_Bid',
        'ask': 'FDF_Ask'
    }

    # Rename columns in metrics_df
    metrics_df_renamed = metrics_df.rename(columns=column_mapping)

    # Keep only symbol and renamed columns
    cols_to_keep = ['symbol'] + [v for v in column_mapping.values() if v in metrics_df_renamed.columns]
    metrics_df_clean = metrics_df_renamed[cols_to_keep]

    # Drop columns from gtt_df if they already exist (to avoid duplicates)
    cols_to_drop = [col for col in metrics_df_clean.columns
                   if col in gtt_df.columns and col != 'Symbol']
    if cols_to_drop:
        gtt_df = gtt_df.drop(columns=cols_to_drop)

    # Merge on Symbol column
    updated_gtt = gtt_df.merge(metrics_df_clean, left_on='Symbol', right_on='symbol', how='left')

    # Drop the duplicate 'symbol' column if it exists
    if 'symbol' in updated_gtt.columns:
        updated_gtt = updated_gtt.drop(columns=['symbol'])

    # Replace NaN with 'N/A' for display
    for col in updated_gtt.columns:
        if col not in gtt_df.columns:  # Only for new columns
            updated_gtt[col] = updated_gtt[col].fillna('N/A')

    return updated_gtt


def setup_logging(log_file: Optional[str], verbose: bool = False) -> logging.Logger:
    """
    Setup logging infrastructure

    Parameters:
    -----------
    log_file : str or None
        Path to log file (auto-generated if None)
    verbose : bool
        Enable debug logging

    Returns:
    --------
    Logger instance
    """

    # Create logs directory if needed
    if log_file is None:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_file = log_dir / f"fdf_{timestamp}.log"
    else:
        log_file = Path(log_file)
        log_file.parent.mkdir(parents=True, exist_ok=True)

    # Setup logger
    logger = logging.getLogger("FDF")
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)

    # File handler (detailed)
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(file_formatter)
    logger.addHandler(file_handler)

    # Console handler (less verbose)
    console_handler = RichHandler(console=console, show_time=False, show_path=False)
    console_handler.setLevel(logging.WARNING)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized - Log file: {log_file}")
    return logger


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments"""

    parser = argparse.ArgumentParser(
        description="Fundamentals Data Fetcher (FDF) - Enrich GusTradeTable with fundamental metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic usage
  python fundamentals_data_fetcher.py --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt --client-id 400

  # Custom output and logging
  python fundamentals_data_fetcher.py \\
    --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \\
    --output ./runs/19-Nov-2025/GusTradeTable_enriched.txt \\
    --log ./runs/19-Nov-2025/fdf.log \\
    --client-id 400

  # Paper trading account (port 4002)
  python fundamentals_data_fetcher.py \\
    --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \\
    --client-id 400 \\
    --port 4002

  # Skip market data (faster)
  python fundamentals_data_fetcher.py \\
    --input ./runs/19-Nov-2025/GusTradeTable_19nov2025.txt \\
    --client-id 400 \\
    --no-market-data
        """
    )

    # Required arguments
    parser.add_argument(
        '--input',
        type=str,
        required=True,
        help='Path to GusTradeTable file (tab-delimited .txt)'
    )

    parser.add_argument(
        '--client-id',
        type=int,
        required=True,
        help='IB Gateway client ID (must be unique)'
    )

    # Optional arguments
    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output file path (default: overwrites input file)'
    )

    parser.add_argument(
        '--log',
        type=str,
        default=None,
        help='Log file path (default: auto-generated in ./logs/)'
    )

    parser.add_argument(
        '--host',
        type=str,
        default='127.0.0.1',
        help='IB Gateway host (default: 127.0.0.1)'
    )

    parser.add_argument(
        '--port',
        type=int,
        default=4001,
        help='IB Gateway port (4001 for live, 4002 for paper, default: 4001)'
    )

    parser.add_argument(
        '--no-market-data',
        action='store_true',
        help='Skip real-time market data fetching (faster)'
    )

    parser.add_argument(
        '--batch-delay',
        type=float,
        default=0.5,
        help='Delay between batches in seconds (default: 0.5)'
    )

    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose logging'
    )

    return parser.parse_args()


def display_summary(original_df: pd.DataFrame, enriched_df: pd.DataFrame, logger: logging.Logger):
    """Display summary table of enrichment results"""

    # Count how many stocks have data for key metrics
    key_metrics = ['Float', 'Institutional_Pct', 'Short_Interest', 'Net_Income_TTM']

    table = Table(title="Enrichment Summary", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="white")
    table.add_column("Stocks with Data", justify="right", style="green")
    table.add_column("Missing Data", justify="right", style="yellow")
    table.add_column("Coverage %", justify="right", style="cyan")

    total_stocks = len(enriched_df)

    for metric in key_metrics:
        if metric in enriched_df.columns:
            # Count non-N/A values
            with_data = sum(enriched_df[metric] != 'N/A')
            missing = total_stocks - with_data
            coverage = (with_data / total_stocks * 100) if total_stocks > 0 else 0

            table.add_row(
                metric,
                str(with_data),
                str(missing),
                f"{coverage:.1f}%"
            )

    console.print("\n")
    console.print(table)
    console.print("\n")

    # Log summary
    logger.info(f"Enrichment complete - {total_stocks} stocks processed")
    for metric in key_metrics:
        if metric in enriched_df.columns:
            with_data = sum(enriched_df[metric] != 'N/A')
            coverage = (with_data / total_stocks * 100) if total_stocks > 0 else 0
            logger.info(f"{metric}: {with_data}/{total_stocks} stocks ({coverage:.1f}%)")


def main():
    """Main workflow orchestration"""

    # Parse arguments
    args = parse_arguments()

    # Setup logging
    logger = setup_logging(args.log, args.verbose)

    try:
        # Display banner
        console.print("\n[bold cyan]═══════════════════════════════════════════════════════[/bold cyan]")
        console.print("[bold cyan]   Fundamentals Data Fetcher (FDF) for GusTradeTable   [/bold cyan]")
        console.print("[bold cyan]═══════════════════════════════════════════════════════[/bold cyan]\n")

        # Validate input file
        input_file = Path(args.input)
        if not input_file.exists():
            logger.error(f"Input file not found: {input_file}")
            console.print(f"[red]❌ Input file not found: {input_file}[/red]")
            sys.exit(1)

        logger.info(f"Loading GusTradeTable from {input_file}")
        console.print(f"[cyan]📂 Loading GusTradeTable from:[/cyan] {input_file}")

        # Load GusTradeTable (tab-delimited)
        try:
            gtt_df = pd.read_csv(input_file, sep='\t')
            logger.info(f"Loaded {len(gtt_df)} rows")
            console.print(f"[green]✓[/green] Loaded [bold]{len(gtt_df)}[/bold] stocks\n")
        except Exception as e:
            logger.error(f"Failed to load input file: {e}")
            console.print(f"[red]❌ Failed to load input file: {e}[/red]")
            sys.exit(1)

        # Validate Symbol column exists
        if 'Symbol' not in gtt_df.columns:
            logger.error("Input file missing 'Symbol' column")
            console.print("[red]❌ Input file missing 'Symbol' column[/red]")
            sys.exit(1)

        # Get symbols
        symbols = gtt_df['Symbol'].tolist()
        logger.info(f"Found {len(symbols)} symbols to enrich")
        console.print(f"[cyan]🎯 Symbols to enrich:[/cyan] {', '.join(symbols[:10])}")
        if len(symbols) > 10:
            console.print(f"   [dim]... and {len(symbols)-10} more[/dim]\n")
        else:
            console.print()

        # Display configuration
        console.print(f"[cyan]⚙️  Configuration:[/cyan]")
        console.print(f"   Host: {args.host}")
        console.print(f"   Port: {args.port}")
        console.print(f"   Client ID: {args.client_id}")
        console.print(f"   Market Data: {'No' if args.no_market_data else 'Yes'}")
        console.print()

        # Fetch data
        metrics_df = fetch_complete_data(
            symbols=symbols,
            host=args.host,
            port=args.port,
            client_id=args.client_id,
            get_market_data=not args.no_market_data,
            logger=logger
        )

        if metrics_df.empty:
            logger.error("Failed to fetch any data")
            console.print("[red]❌ Failed to fetch any data[/red]")
            sys.exit(1)

        # Update GTT table
        logger.info("Merging fundamental data with GusTradeTable")
        console.print("[cyan]🔄 Merging fundamental data with GusTradeTable...[/cyan]")
        enriched_gtt = update_gtt_table(gtt_df, metrics_df)

        # Add timestamp column
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        enriched_gtt['FDF_Timestamp'] = timestamp

        # Determine output file
        output_file = Path(args.output) if args.output else input_file
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Save updated table (tab-delimited)
        logger.info(f"Saving enriched table to {output_file}")
        console.print(f"[cyan]💾 Saving enriched table to:[/cyan] {output_file}")
        enriched_gtt.to_csv(output_file, sep='\t', index=False)
        console.print("[green]✓[/green] Saved successfully!\n")

        # Display summary
        display_summary(gtt_df, enriched_gtt, logger)

        # Display preview
        console.print("[cyan]📋 Preview of new columns:[/cyan]")
        new_cols = [col for col in enriched_gtt.columns if col not in gtt_df.columns]
        if new_cols:
            preview_df = enriched_gtt[['Symbol'] + new_cols[:5]].head(5)
            preview_table = Table(show_header=True, header_style="bold cyan")

            for col in preview_df.columns:
                preview_table.add_column(col, style="white")

            for _, row in preview_df.iterrows():
                preview_table.add_row(*[str(v) for v in row])

            console.print(preview_table)

        console.print("\n[green]✓ Enrichment complete![/green]\n")
        logger.info("Enrichment workflow completed successfully")

    except KeyboardInterrupt:
        logger.warning("Process interrupted by user")
        console.print("\n[yellow]⚠️  Process interrupted by user[/yellow]")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}", exc_info=True)
        console.print(f"\n[red]❌ Unexpected error: {e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
