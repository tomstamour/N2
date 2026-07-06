#!/usr/bin/env python3
"""
Borrow Fee iBorrowDesk Fetcher for GUS3
========================================

Fetches borrow fee data from iBorrowDesk's free JSON API.
This provides real-time borrow fee rates and serves as a fallback when IBKR FTP data is missing.

API Details:
- Endpoint: https://iborrowdesk.com/api/ticker/{SYMBOL}
- Format: JSON
- Updates: Every 15 minutes (market hours), hourly (off-hours)
- Cost: FREE (basic usage)
- Rate Limit: Self-imposed 1 request/second (respectful)

Usage:
    # Fetch specific symbols and save to cache
    python3 borrow_fee_iborrowdesk_fetcher.py --symbols AAPL TSLA NVDA --output ./data/iborrowdesk_cache.csv

    # Enrich GTT file with iBorrowDesk data (fallback for BF1=N/A)
    python3 borrow_fee_iborrowdesk_fetcher.py --enrich-gtt ./runs/22-Nov-2025/GusTradeTable.txt --fallback-only

    # Fetch all symbols from GTT
    python3 borrow_fee_iborrowdesk_fetcher.py --enrich-gtt ./runs/22-Nov-2025/GusTradeTable.txt

Author: Claude Code (Anthropic)
Date: 2025-11-22
"""

import argparse
import csv
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Set
from urllib.parse import quote

try:
    import requests
except ImportError:
    print("ERROR: requests library not found. Install with: pip install requests")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class IBorrowDeskFetcher:
    """Fetches borrow fee data from iBorrowDesk API"""

    BASE_URL = "https://iborrowdesk.com/api/ticker/{}"
    USER_AGENT = "GUS3-BorrowFee-Fetcher/1.0"
    RATE_LIMIT = 1.0  # seconds between requests (respectful)
    TIMEOUT = 10  # seconds for HTTP requests
    MAX_WORKERS = 5  # Parallel requests (with rate limiting)

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': self.USER_AGENT,
            'Accept': 'application/json'
        })
        self.borrow_data = {}
        self.last_request_time = 0

    def _rate_limit(self):
        """Enforce rate limiting between requests"""
        elapsed = time.time() - self.last_request_time
        if elapsed < self.RATE_LIMIT:
            sleep_time = self.RATE_LIMIT - elapsed
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def fetch_symbol(self, symbol: str) -> Optional[Dict]:
        """
        Fetch borrow fee for a single symbol from iBorrowDesk API

        Args:
            symbol: Stock symbol to fetch

        Returns:
            Dict with fee_rate, available_shares, rebate_rate, timestamp or None on error
        """
        self._rate_limit()

        url = self.BASE_URL.format(quote(symbol))

        try:
            logger.debug(f"Fetching {symbol} from iBorrowDesk...")
            response = self.session.get(url, timeout=self.TIMEOUT)

            if response.status_code == 404:
                logger.debug(f"{symbol}: Not found in iBorrowDesk")
                return None

            if response.status_code != 200:
                logger.warning(f"{symbol}: HTTP {response.status_code}")
                return None

            data = response.json()

            # Extract latest borrow fee data
            latest_fee = data.get('latest_fee')
            latest_available = data.get('latest_available')
            updated = data.get('updated')

            # Try to get rebate rate from daily data
            rebate_rate = None
            if data.get('daily') and len(data['daily']) > 0:
                rebate_rate = data['daily'][0].get('rebate')

            result = {
                'symbol': symbol,
                'fee_rate': float(latest_fee) if latest_fee is not None else None,
                'available_shares': int(latest_available) if latest_available is not None else None,
                'rebate_rate': float(rebate_rate) if rebate_rate is not None else None,
                'timestamp': updated or datetime.now().isoformat(),
                'source': 'iBorrowDesk'
            }

            if result['fee_rate'] is not None:
                logger.debug(f"{symbol}: Fee = {result['fee_rate']:.2f}%")
            else:
                logger.debug(f"{symbol}: No fee data available")

            return result

        except requests.exceptions.Timeout:
            logger.warning(f"{symbol}: Request timeout")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"{symbol}: Connection error")
            return None
        except json.JSONDecodeError:
            logger.error(f"{symbol}: Invalid JSON response")
            return None
        except Exception as e:
            logger.error(f"{symbol}: Unexpected error: {e}")
            return None

    def fetch_batch(self, symbols: List[str], max_workers: int = None) -> Dict[str, Dict]:
        """
        Fetch borrow fees for multiple symbols in parallel with rate limiting

        Args:
            symbols: List of stock symbols
            max_workers: Max parallel workers (default: self.MAX_WORKERS)

        Returns:
            Dict mapping symbol -> borrow fee data
        """
        max_workers = max_workers or self.MAX_WORKERS
        results = {}

        logger.info(f"Fetching {len(symbols)} symbols from iBorrowDesk (rate limit: {self.RATE_LIMIT}s/req)...")

        # Use ThreadPoolExecutor for parallel requests
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_symbol = {
                executor.submit(self.fetch_symbol, symbol): symbol
                for symbol in symbols
            }

            completed = 0
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                completed += 1

                try:
                    result = future.result()
                    if result and result['fee_rate'] is not None:
                        results[symbol] = result
                        self.borrow_data[symbol] = result
                        logger.info(f"[{completed}/{len(symbols)}] {symbol}: {result['fee_rate']:.2f}%")
                    else:
                        logger.info(f"[{completed}/{len(symbols)}] {symbol}: N/A")
                except Exception as e:
                    logger.error(f"{symbol}: Exception in fetch: {e}")

        logger.info(f"Successfully fetched {len(results)}/{len(symbols)} symbols")
        return results

    def save_to_csv(self, output_path: str) -> bool:
        """Save borrow fee data to CSV cache file"""
        if not self.borrow_data:
            logger.warning("No data to save")
            return False

        try:
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            with open(output_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'symbol', 'fee_rate', 'available_shares', 'rebate_rate', 'timestamp', 'source'
                ])
                writer.writeheader()
                for symbol in sorted(self.borrow_data.keys()):
                    writer.writerow(self.borrow_data[symbol])

            logger.info(f"Saved {len(self.borrow_data)} records to {output_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save CSV: {e}")
            return False

    def load_from_csv(self, csv_path: str) -> bool:
        """Load cached borrow fee data from CSV file"""
        try:
            self.borrow_data = {}
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = row['symbol']
                    row['fee_rate'] = float(row['fee_rate']) if row['fee_rate'] else None
                    row['available_shares'] = int(row['available_shares']) if row['available_shares'] else None
                    row['rebate_rate'] = float(row['rebate_rate']) if row['rebate_rate'] else None
                    self.borrow_data[symbol] = row

            logger.info(f"Loaded {len(self.borrow_data)} records from {csv_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load CSV: {e}")
            return False

    def get_borrow_fee(self, symbol: str) -> Optional[float]:
        """Get borrow fee rate for a symbol from cached data"""
        return self.borrow_data.get(symbol, {}).get('fee_rate')

    def enrich_gtt_file(self, gtt_path: str, output_path: Optional[str] = None,
                       fallback_only: bool = True) -> bool:
        """
        Enrich GusTradeTable file with iBorrowDesk data

        Args:
            gtt_path: Path to GTT file
            output_path: Output path (default: overwrite input)
            fallback_only: Only fetch symbols where BF1 is N/A (default: True)

        Returns:
            True if successful
        """
        if not os.path.exists(gtt_path):
            logger.error(f"GTT file not found: {gtt_path}")
            return False

        output_path = output_path or gtt_path

        try:
            # Read GTT file
            with open(gtt_path, 'r') as f:
                lines = f.readlines()

            if not lines:
                logger.error("Empty GTT file")
                return False

            # Parse header
            header = lines[0].strip().split('\t')
            logger.info(f"GTT columns: {header}")

            # Find column indices
            try:
                symbol_idx = header.index('Symbol')
            except ValueError:
                logger.error("Symbol column not found in GTT")
                return False

            # Check if BF1 exists (for fallback mode)
            bf1_idx = None
            if 'BF1' in header:
                bf1_idx = header.index('BF1')
                logger.info("BF1 column found in GTT")

            # Check if BF2 column exists, add if not
            bf2_idx = None
            if 'BF2' in header:
                bf2_idx = header.index('BF2')
                logger.info("BF2 column found in GTT")
            else:
                # Add BF2 column after BF1 (or after Symbol if BF1 doesn't exist)
                if bf1_idx is not None:
                    bf2_idx = bf1_idx + 1
                else:
                    bf2_idx = symbol_idx + 1
                header.insert(bf2_idx, 'BF2')
                logger.info("Adding BF2 column to GTT")

            # Collect symbols to fetch
            symbols_to_fetch = []
            for line in lines[1:]:
                if not line.strip():
                    continue

                cols = line.strip().split('\t')
                if len(cols) <= symbol_idx:
                    continue

                symbol = cols[symbol_idx]

                # Fallback mode: only fetch if BF1 is N/A or empty
                if fallback_only and bf1_idx is not None:
                    if len(cols) > bf1_idx:
                        bf1_value = cols[bf1_idx]
                        if bf1_value and bf1_value != 'N/A':
                            continue  # Skip symbols that already have BF1 data

                symbols_to_fetch.append(symbol)

            if not symbols_to_fetch:
                logger.info("No symbols to fetch (all have BF1 data or empty GTT)")
            else:
                logger.info(f"Fetching {len(symbols_to_fetch)} symbols from iBorrowDesk...")
                self.fetch_batch(symbols_to_fetch)

            # Update header line
            enriched_lines = ['\t'.join(header) + '\n']

            # Process data rows
            updated_count = 0

            for line in lines[1:]:
                if not line.strip():
                    continue

                cols = line.strip().split('\t')
                if len(cols) <= symbol_idx:
                    logger.warning(f"Malformed GTT line: {line}")
                    enriched_lines.append(line)
                    continue

                # Ensure cols has enough elements
                while len(cols) < len(header):
                    cols.append('N/A')

                symbol = cols[symbol_idx]

                # Get iBorrowDesk fee rate
                ibd_fee = self.get_borrow_fee(symbol)

                # Update BF2 column
                if ibd_fee is not None:
                    cols[bf2_idx] = f"{ibd_fee:.2f}"
                    updated_count += 1
                    logger.debug(f"{symbol}: BF2 = {ibd_fee:.2f}%")
                else:
                    cols[bf2_idx] = 'N/A'

                enriched_lines.append('\t'.join(cols) + '\n')

            # Write output
            with open(output_path, 'w') as f:
                f.writelines(enriched_lines)

            logger.info(f"Enriched GTT: Updated BF2 for {updated_count} symbols")
            logger.info(f"Output written to: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to enrich GTT: {e}")
            return False


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Fetch borrow fee data from iBorrowDesk API and enrich GusTradeTable files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fetch specific symbols
  python3 borrow_fee_iborrowdesk_fetcher.py --symbols AAPL TSLA NVDA --output ./data/iborrowdesk_cache.csv

  # Enrich GTT (only fetch symbols with BF1=N/A)
  python3 borrow_fee_iborrowdesk_fetcher.py --enrich-gtt ./runs/22-Nov-2025/GusTradeTable.txt --fallback-only

  # Enrich GTT (fetch all symbols)
  python3 borrow_fee_iborrowdesk_fetcher.py --enrich-gtt ./runs/22-Nov-2025/GusTradeTable.txt

  # Use cached data
  python3 borrow_fee_iborrowdesk_fetcher.py --load-csv ./data/iborrowdesk_cache.csv --enrich-gtt ./runs/22-Nov-2025/GusTradeTable.txt
        """
    )

    parser.add_argument('--symbols', nargs='+',
                        help='Stock symbols to fetch')
    parser.add_argument('--output', '-o',
                        help='Save fetched data to CSV file')
    parser.add_argument('--load-csv',
                        help='Load borrow fee data from existing CSV instead of API')
    parser.add_argument('--enrich-gtt',
                        help='Enrich GusTradeTable file with borrow fee data')
    parser.add_argument('--gtt-output',
                        help='Output path for enriched GTT (default: overwrite input)')
    parser.add_argument('--fallback-only', action='store_true',
                        help='Only fetch symbols where BF1 is N/A (smart fallback mode)')
    parser.add_argument('--rate-limit', type=float, default=1.0,
                        help='Seconds between API requests (default: 1.0)')
    parser.add_argument('--max-workers', type=int, default=5,
                        help='Max parallel workers (default: 5)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose debug logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Validate arguments
    if not args.symbols and not args.enrich_gtt and not args.load_csv:
        parser.error("Must specify --symbols, --enrich-gtt, or --load-csv")

    fetcher = IBorrowDeskFetcher()
    fetcher.RATE_LIMIT = args.rate_limit
    fetcher.MAX_WORKERS = args.max_workers

    # Load data from cache if specified
    if args.load_csv:
        logger.info(f"Loading borrow fee data from CSV: {args.load_csv}")
        if not fetcher.load_from_csv(args.load_csv):
            logger.error("Failed to load CSV")
            return 1

    # Fetch symbols if specified
    if args.symbols:
        logger.info(f"Fetching {len(args.symbols)} symbols from iBorrowDesk...")
        fetcher.fetch_batch(args.symbols)

    # Save to CSV if requested
    if args.output:
        if not fetcher.save_to_csv(args.output):
            logger.error("Failed to save CSV")
            return 1

    # Enrich GTT if requested
    if args.enrich_gtt:
        if not fetcher.enrich_gtt_file(args.enrich_gtt, args.gtt_output, args.fallback_only):
            logger.error("Failed to enrich GTT")
            return 1

    logger.info("Completed successfully")
    return 0


if __name__ == '__main__':
    sys.exit(main())
