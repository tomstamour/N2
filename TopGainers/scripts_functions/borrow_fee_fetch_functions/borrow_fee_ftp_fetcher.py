#!/usr/bin/env python3
"""
Borrow Fee FTP Fetcher for GUS3
================================

Fetches borrow fee and shortable shares data from Interactive Brokers' official FTP site.
This data supplements the real-time API data which often lacks borrow fee rates.

FTP Details:
- Server: ftp2.interactivebrokers.com
- Username: shortstock
- Password: (empty)
- Protocol: FTP
- Data: Stock rebate/fee rates and available shares

Usage:
    # Download and parse FTP data
    python3 borrow_fee_ftp_fetcher.py --output ./data/borrow_fees.csv

    # Update GTT file with FTP data
    python3 borrow_fee_ftp_fetcher.py --enrich-gtt ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt

    # Both download and enrich
    python3 borrow_fee_ftp_fetcher.py --output ./data/borrow_fees.csv --enrich-gtt ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt

Author: Claude Code (Anthropic)
Date: 2025-11-21
"""

import argparse
import csv
import ftplib
import io
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class IBKRBorrowFeeFetcher:
    """Fetches borrow fee data from IBKR FTP site"""

    FTP_HOST = "ftp2.interactivebrokers.com"
    FTP_USER = "shortstock"
    FTP_PASS = ""
    FTP_FILE = "usa.txt"  # Main US stocks file

    def __init__(self):
        self.ftp = None
        self.borrow_data = {}

    def connect(self) -> bool:
        """Connect to IBKR FTP server"""
        try:
            logger.info(f"Connecting to {self.FTP_HOST}...")
            self.ftp = ftplib.FTP(self.FTP_HOST)
            self.ftp.login(self.FTP_USER, self.FTP_PASS)
            logger.info(f"Connected successfully")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to FTP: {e}")
            return False

    def disconnect(self):
        """Disconnect from FTP server"""
        if self.ftp:
            try:
                self.ftp.quit()
                logger.info("Disconnected from FTP")
            except:
                pass

    def download_file(self) -> Optional[str]:
        """Download borrow fee data file from FTP"""
        if not self.ftp:
            logger.error("Not connected to FTP")
            return None

        try:
            logger.info(f"Downloading {self.FTP_FILE}...")
            data = io.BytesIO()
            self.ftp.retrbinary(f'RETR {self.FTP_FILE}', data.write)
            content = data.getvalue().decode('utf-8')
            logger.info(f"Downloaded {len(content)} bytes")
            return content
        except Exception as e:
            logger.error(f"Failed to download file: {e}")
            return None

    def parse_ftp_data(self, content: str) -> Dict[str, Dict]:
        """
        Parse FTP file content into structured data

        FTP File Format (pipe-delimited):
        #SYM|CUR|NAME|CON_ID|ISIN|REBATERATE|FEERATE|AVAILABLE

        Example:
        #SYM|CUR|NAME|CON_ID|ISIN|REBATERATE|FEERATE|AVAILABLE
        A|USD|AGILENT TECHNOLOGIES INC|12345|US00123456|0.25|0.30|1000000
        AAPL|USD|APPLE INC|67890|US00987654|0.00|0.50|50000000

        Returns:
            Dict mapping symbol -> {fee_rate, rebate_rate, available_shares, ...}
        """
        data = {}
        lines = content.strip().split('\n')

        if not lines:
            logger.warning("Empty FTP file")
            return data

        # Skip header line (starts with #SYM)
        header_found = False
        for line in lines:
            line = line.strip()

            if not line:
                continue

            # Check for header
            if line.startswith('#SYM'):
                header_found = True
                logger.debug(f"Found header: {line}")
                continue

            if not header_found:
                continue

            # Parse data line
            parts = line.split('|')
            if len(parts) < 8:
                logger.debug(f"Skipping malformed line: {line}")
                continue

            symbol = parts[0].strip()
            currency = parts[1].strip()
            name = parts[2].strip()
            con_id = parts[3].strip()
            isin = parts[4].strip()

            try:
                rebate_rate = float(parts[5].strip()) if parts[5].strip() else None
            except ValueError:
                rebate_rate = None

            try:
                fee_rate = float(parts[6].strip()) if parts[6].strip() else None
            except ValueError:
                fee_rate = None

            try:
                available = int(parts[7].strip()) if parts[7].strip() else None
            except ValueError:
                available = None

            # Store data (only USD stocks for now)
            if currency == 'USD':
                data[symbol] = {
                    'symbol': symbol,
                    'currency': currency,
                    'name': name,
                    'con_id': con_id,
                    'isin': isin,
                    'rebate_rate': rebate_rate,
                    'fee_rate': fee_rate,
                    'available_shares': available,
                    'ftp_timestamp': datetime.now().isoformat()
                }

        logger.info(f"Parsed {len(data)} symbols from FTP data")
        return data

    def fetch_data(self) -> Dict[str, Dict]:
        """Main method to fetch and parse borrow fee data"""
        if not self.connect():
            return {}

        content = self.download_file()
        self.disconnect()

        if not content:
            return {}

        self.borrow_data = self.parse_ftp_data(content)
        return self.borrow_data

    def save_to_csv(self, output_path: str) -> bool:
        """Save borrow fee data to CSV file"""
        if not self.borrow_data:
            logger.warning("No data to save")
            return False

        try:
            # Create directory if needed
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            with open(output_path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=[
                    'symbol', 'currency', 'name', 'con_id', 'isin',
                    'rebate_rate', 'fee_rate', 'available_shares', 'ftp_timestamp'
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
        """Load borrow fee data from existing CSV file"""
        try:
            self.borrow_data = {}
            with open(csv_path, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    symbol = row['symbol']
                    # Convert numeric fields
                    row['rebate_rate'] = float(row['rebate_rate']) if row['rebate_rate'] else None
                    row['fee_rate'] = float(row['fee_rate']) if row['fee_rate'] else None
                    row['available_shares'] = int(row['available_shares']) if row['available_shares'] else None
                    self.borrow_data[symbol] = row

            logger.info(f"Loaded {len(self.borrow_data)} records from {csv_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to load CSV: {e}")
            return False

    def get_borrow_fee(self, symbol: str) -> Optional[float]:
        """Get borrow fee rate for a symbol"""
        return self.borrow_data.get(symbol, {}).get('fee_rate')

    def get_available_shares(self, symbol: str) -> Optional[int]:
        """Get available shortable shares for a symbol"""
        return self.borrow_data.get(symbol, {}).get('available_shares')

    def enrich_gtt_file(self, gtt_path: str, output_path: Optional[str] = None) -> bool:
        """
        Enrich GusTradeTable file with FTP borrow fee data

        Updates the BorrowFee column with data from FTP when available.
        Also adds/updates BF1 column for multi-source tracking.
        If output_path is None, overwrites the input file.
        """
        if not os.path.exists(gtt_path):
            logger.error(f"GTT file not found: {gtt_path}")
            return False

        if not self.borrow_data:
            logger.error("No borrow fee data loaded. Run fetch_data() first.")
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

            # Find column indices (using new column names)
            try:
                symbol_idx = header.index('Symbol')
                borrow_fee_idx = header.index('BorrowFee')
            except ValueError as e:
                logger.error(f"Required column not found in GTT: {e}")
                return False

            # Check if BF1 column exists, add if not
            bf1_idx = None
            if 'BF1' in header:
                bf1_idx = header.index('BF1')
                logger.info("BF1 column found in GTT")
            else:
                # Add BF1 column after BorrowFee
                bf1_idx = borrow_fee_idx + 1
                header.insert(bf1_idx, 'BF1')
                logger.info("Adding BF1 column to GTT")

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
                current_fee = cols[borrow_fee_idx]

                # Get FTP fee rate
                ftp_fee = self.get_borrow_fee(symbol)

                # Update BF1 column with FTP data
                if ftp_fee is not None:
                    cols[bf1_idx] = f"{ftp_fee:.2f}"
                    updated_count += 1
                    logger.debug(f"{symbol}: BF1 = {ftp_fee:.2f}%")

                    # Also update BorrowFee if it's N/A (backwards compatibility)
                    if current_fee == 'N/A' or not current_fee:
                        cols[borrow_fee_idx] = f"{ftp_fee:.2f}"
                        logger.debug(f"{symbol}: Updated BorrowFee to {ftp_fee:.2f}%")
                else:
                    cols[bf1_idx] = 'N/A'

                enriched_lines.append('\t'.join(cols) + '\n')

            # Write output
            with open(output_path, 'w') as f:
                f.writelines(enriched_lines)

            logger.info(f"Enriched GTT: Updated BF1 for {updated_count} symbols")
            logger.info(f"Output written to: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to enrich GTT: {e}")
            return False


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Fetch borrow fee data from IBKR FTP and enrich GusTradeTable files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download FTP data and save to CSV
  python3 borrow_fee_ftp_fetcher.py --output ./data/borrow_fees.csv

  # Enrich GTT file with FTP data (using existing CSV)
  python3 borrow_fee_ftp_fetcher.py --load-csv ./data/borrow_fees.csv --enrich-gtt ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt

  # Download FTP and immediately enrich GTT
  python3 borrow_fee_ftp_fetcher.py --enrich-gtt ./runs/21-Nov-2025/GusTradeTable_21nov2025.txt
        """
    )

    parser.add_argument('--output', '-o',
                        help='Save FTP data to CSV file')
    parser.add_argument('--load-csv',
                        help='Load borrow fee data from existing CSV instead of FTP')
    parser.add_argument('--enrich-gtt',
                        help='Enrich GusTradeTable file with borrow fee data')
    parser.add_argument('--gtt-output',
                        help='Output path for enriched GTT (default: overwrite input)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose debug logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Validate arguments
    if not args.output and not args.enrich_gtt:
        parser.error("Must specify --output and/or --enrich-gtt")

    fetcher = IBKRBorrowFeeFetcher()

    # Load data (from FTP or CSV)
    if args.load_csv:
        logger.info(f"Loading borrow fee data from CSV: {args.load_csv}")
        if not fetcher.load_from_csv(args.load_csv):
            logger.error("Failed to load CSV")
            return 1
    else:
        logger.info("Fetching borrow fee data from IBKR FTP...")
        data = fetcher.fetch_data()
        if not data:
            logger.error("Failed to fetch FTP data")
            return 1

    # Save to CSV if requested
    if args.output:
        if not fetcher.save_to_csv(args.output):
            logger.error("Failed to save CSV")
            return 1

    # Enrich GTT if requested
    if args.enrich_gtt:
        if not fetcher.enrich_gtt_file(args.enrich_gtt, args.gtt_output):
            logger.error("Failed to enrich GTT")
            return 1

    logger.info("Completed successfully")
    return 0


if __name__ == '__main__':
    sys.exit(main())
