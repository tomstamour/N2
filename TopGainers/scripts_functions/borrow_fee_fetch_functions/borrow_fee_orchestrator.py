#!/usr/bin/env python3
"""
Multi-Source Borrow Fee Orchestrator for GUS3
==============================================

Coordinates multiple borrow fee data sources to enrich GusTradeTable files with comprehensive coverage.
Uses a priority waterfall approach: IBKR FTP (BF1) → iBorrowDesk (BF2) → ShortableStocks (BF3).

Features:
- Runs multiple fetchers in sequence
- Adds BF_BEST column with best available value
- Generates enrichment coverage report
- Updates BorrowFee column for backwards compatibility

Data Sources:
- BF1: IBKR FTP (daily bulk download, 17,900+ symbols)
- BF2: iBorrowDesk API (15-min updates, real-time fallback)
- BF3: ShortableStocks.com API (optional paid, 15-min updates)

Usage:
    # Run all available sources
    python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt

    # Specify sources
    python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt --sources ftp,iborrowdesk

    # Generate detailed report
    python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt --report ./runs/22-Nov-2025/borrow_fee_report.txt

    # Use cached FTP data
    python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt --ftp-cache ./data/borrow_fees.csv

Author: Claude Code (Anthropic)
Date: 2025-11-22
"""

import argparse
import logging
import os
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

# Add scripts_functions to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from borrow_fee_ftp_fetcher import IBKRBorrowFeeFetcher
    from borrow_fee_iborrowdesk_fetcher import IBorrowDeskFetcher
except ImportError as e:
    print(f"ERROR: Failed to import fetcher modules: {e}")
    print("Ensure borrow_fee_ftp_fetcher.py and borrow_fee_iborrowdesk_fetcher.py exist in the same directory")
    sys.exit(1)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class BorrowFeeOrchestrator:
    """Orchestrates multiple borrow fee data sources for comprehensive GTT enrichment"""

    def __init__(self):
        self.ftp_fetcher = None
        self.ibd_fetcher = None
        self.stats = {
            'total_symbols': 0,
            'bf1_count': 0,
            'bf2_count': 0,
            'bf3_count': 0,
            'missing_count': 0,
            'bf1_only': [],
            'bf2_only': [],
            'bf3_only': [],
            'missing': [],
            'discrepancies': []
        }

    def run_ftp_enrichment(self, gtt_path: str, ftp_cache: Optional[str] = None) -> bool:
        """Run IBKR FTP fetcher to add BF1 column"""
        logger.info("=" * 60)
        logger.info("SOURCE 1: IBKR FTP (BF1)")
        logger.info("=" * 60)

        self.ftp_fetcher = IBKRBorrowFeeFetcher()

        # Load data from cache or fetch fresh
        if ftp_cache and os.path.exists(ftp_cache):
            logger.info(f"Loading FTP data from cache: {ftp_cache}")
            if not self.ftp_fetcher.load_from_csv(ftp_cache):
                logger.error("Failed to load FTP cache")
                return False
        else:
            logger.info("Fetching fresh data from IBKR FTP...")
            data = self.ftp_fetcher.fetch_data()
            if not data:
                logger.error("Failed to fetch FTP data")
                return False

            # Save to cache if path provided
            if ftp_cache:
                self.ftp_fetcher.save_to_csv(ftp_cache)

        # Enrich GTT with BF1 column
        if not self.ftp_fetcher.enrich_gtt_file(gtt_path):
            logger.error("Failed to enrich GTT with FTP data")
            return False

        logger.info("✓ FTP enrichment completed")
        return True

    def run_iborrowdesk_enrichment(self, gtt_path: str, ibd_cache: Optional[str] = None,
                                   fallback_only: bool = True) -> bool:
        """Run iBorrowDesk fetcher to add BF2 column"""
        logger.info("=" * 60)
        logger.info("SOURCE 2: iBorrowDesk API (BF2)")
        logger.info("=" * 60)

        self.ibd_fetcher = IBorrowDeskFetcher()

        # Load data from cache if available
        if ibd_cache and os.path.exists(ibd_cache):
            logger.info(f"Loading iBorrowDesk data from cache: {ibd_cache}")
            if not self.ibd_fetcher.load_from_csv(ibd_cache):
                logger.warning("Failed to load iBorrowDesk cache, will fetch fresh data")

        # Enrich GTT with BF2 column (fetches symbols as needed)
        if not self.ibd_fetcher.enrich_gtt_file(gtt_path, fallback_only=fallback_only):
            logger.error("Failed to enrich GTT with iBorrowDesk data")
            return False

        # Save to cache if path provided
        if ibd_cache and self.ibd_fetcher.borrow_data:
            self.ibd_fetcher.save_to_csv(ibd_cache)

        logger.info("✓ iBorrowDesk enrichment completed")
        return True

    def add_best_value_columns(self, gtt_path: str) -> bool:
        """
        Add BF_BEST column using waterfall logic
        Also updates BorrowFee for backwards compatibility
        """
        logger.info("=" * 60)
        logger.info("ADDING BF_BEST COLUMN")
        logger.info("=" * 60)

        try:
            # Read GTT file
            with open(gtt_path, 'r') as f:
                lines = f.readlines()

            if not lines:
                logger.error("Empty GTT file")
                return False

            # Parse header
            header = lines[0].strip().split('\t')

            # Find column indices (using new column names)
            try:
                symbol_idx = header.index('Symbol')
                borrow_fee_idx = header.index('BorrowFee')
            except ValueError as e:
                logger.error(f"Required column not found: {e}")
                return False

            # Find BF columns
            bf1_idx = header.index('BF1') if 'BF1' in header else None
            bf2_idx = header.index('BF2') if 'BF2' in header else None
            bf3_idx = header.index('BF3') if 'BF3' in header else None

            # Add BF_BEST column if it doesn't exist (BF_Source is no longer added)
            if 'BF_BEST' not in header:
                # Find the position just before LastUpdate
                last_update_idx = header.index('LastUpdate') if 'LastUpdate' in header else len(header)
                header.insert(last_update_idx, 'BF_BEST')

            best_idx = header.index('BF_BEST')

            # Update header
            enriched_lines = ['\t'.join(header) + '\n']

            # Process data rows
            for line in lines[1:]:
                if not line.strip():
                    continue

                cols = line.strip().split('\t')
                if len(cols) <= symbol_idx:
                    enriched_lines.append(line)
                    continue

                # Ensure cols has enough elements
                while len(cols) < len(header):
                    cols.append('N/A')

                symbol = cols[symbol_idx]

                # Get values from each source
                bf1 = cols[bf1_idx] if bf1_idx and len(cols) > bf1_idx else 'N/A'
                bf2 = cols[bf2_idx] if bf2_idx and len(cols) > bf2_idx else 'N/A'
                bf3 = cols[bf3_idx] if bf3_idx and len(cols) > bf3_idx else 'N/A'

                # Waterfall logic: BF1 → BF2 → BF3 → N/A
                best_value = 'N/A'

                if bf1 != 'N/A' and bf1:
                    best_value = bf1
                    self.stats['bf1_count'] += 1
                    if bf2 == 'N/A' and bf3 == 'N/A':
                        self.stats['bf1_only'].append(symbol)
                elif bf2 != 'N/A' and bf2:
                    best_value = bf2
                    self.stats['bf2_count'] += 1
                    self.stats['bf2_only'].append(symbol)
                elif bf3 != 'N/A' and bf3:
                    best_value = bf3
                    self.stats['bf3_count'] += 1
                    self.stats['bf3_only'].append(symbol)
                else:
                    self.stats['missing_count'] += 1
                    self.stats['missing'].append(symbol)

                # Update BF_BEST column
                cols[best_idx] = best_value

                # Update BorrowFee column for backwards compatibility
                if best_value != 'N/A':
                    cols[borrow_fee_idx] = best_value

                # Check for discrepancies between sources
                if bf1 != 'N/A' and bf2 != 'N/A':
                    try:
                        bf1_val = float(bf1)
                        bf2_val = float(bf2)
                        if abs(bf1_val - bf2_val) > 1.0:  # > 1% difference
                            self.stats['discrepancies'].append({
                                'symbol': symbol,
                                'bf1': bf1_val,
                                'bf2': bf2_val,
                                'diff': abs(bf1_val - bf2_val)
                            })
                    except ValueError:
                        pass

                enriched_lines.append('\t'.join(cols) + '\n')
                self.stats['total_symbols'] += 1

            # Write output
            with open(gtt_path, 'w') as f:
                f.writelines(enriched_lines)

            logger.info(f"✓ Added BF_BEST column")
            logger.info(f"  Total symbols: {self.stats['total_symbols']}")
            logger.info(f"  BF1 (FTP): {self.stats['bf1_count']}")
            logger.info(f"  BF2 (IBD): {self.stats['bf2_count']}")
            logger.info(f"  BF3 (SS): {self.stats['bf3_count']}")
            logger.info(f"  Missing: {self.stats['missing_count']}")

            return True

        except Exception as e:
            logger.error(f"Failed to add best value columns: {e}")
            return False

    def generate_report(self, report_path: str) -> bool:
        """Generate enrichment coverage report"""
        try:
            with open(report_path, 'w') as f:
                f.write("=" * 80 + "\n")
                f.write("MULTI-SOURCE BORROW FEE ENRICHMENT REPORT\n")
                f.write("=" * 80 + "\n")
                f.write(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")

                # Summary statistics
                f.write("SUMMARY\n")
                f.write("-" * 80 + "\n")
                f.write(f"Total symbols processed: {self.stats['total_symbols']}\n")
                f.write(f"Symbols with borrow fee data: {self.stats['total_symbols'] - self.stats['missing_count']}\n")
                f.write(f"Symbols missing all data: {self.stats['missing_count']}\n\n")

                coverage_pct = ((self.stats['total_symbols'] - self.stats['missing_count']) /
                               self.stats['total_symbols'] * 100) if self.stats['total_symbols'] > 0 else 0
                f.write(f"Overall coverage: {coverage_pct:.1f}%\n\n")

                # Source breakdown
                f.write("SOURCE BREAKDOWN\n")
                f.write("-" * 80 + "\n")
                f.write(f"BF1 (IBKR FTP):       {self.stats['bf1_count']} symbols\n")
                f.write(f"BF2 (iBorrowDesk):    {self.stats['bf2_count']} symbols\n")
                f.write(f"BF3 (ShortableStocks): {self.stats['bf3_count']} symbols\n\n")

                # Exclusive sources
                f.write("EXCLUSIVE SOURCE COVERAGE\n")
                f.write("-" * 80 + "\n")
                f.write(f"Only in FTP ({len(self.stats['bf1_only'])} symbols):\n")
                if self.stats['bf1_only']:
                    f.write("  " + ", ".join(self.stats['bf1_only'][:20]))
                    if len(self.stats['bf1_only']) > 20:
                        f.write(f" ... and {len(self.stats['bf1_only']) - 20} more")
                f.write("\n\n")

                f.write(f"Only in iBorrowDesk ({len(self.stats['bf2_only'])} symbols):\n")
                if self.stats['bf2_only']:
                    f.write("  " + ", ".join(self.stats['bf2_only']))
                f.write("\n\n")

                f.write(f"Only in ShortableStocks ({len(self.stats['bf3_only'])} symbols):\n")
                if self.stats['bf3_only']:
                    f.write("  " + ", ".join(self.stats['bf3_only']))
                f.write("\n\n")

                # Missing symbols
                f.write(f"MISSING FROM ALL SOURCES ({len(self.stats['missing'])} symbols)\n")
                f.write("-" * 80 + "\n")
                if self.stats['missing']:
                    f.write("  " + ", ".join(self.stats['missing']))
                    f.write("\n\n")
                    f.write("Possible reasons:\n")
                    f.write("  - OTC/Pink Sheet stocks (not shortable through major brokers)\n")
                    f.write("  - Foreign ADRs with limited US borrow availability\n")
                    f.write("  - New IPOs not yet added to shortable lists\n")
                    f.write("  - Micro-cap stocks with no borrow inventory\n\n")

                # Discrepancies
                if self.stats['discrepancies']:
                    f.write(f"DATA DISCREPANCIES (>{1.0}% difference)\n")
                    f.write("-" * 80 + "\n")
                    for disc in self.stats['discrepancies']:
                        f.write(f"  {disc['symbol']:6s}: FTP={disc['bf1']:6.2f}% | IBD={disc['bf2']:6.2f}% | Diff={disc['diff']:5.2f}%\n")
                    f.write("\n")

                f.write("=" * 80 + "\n")
                f.write("END OF REPORT\n")
                f.write("=" * 80 + "\n")

            logger.info(f"Report written to: {report_path}")
            return True

        except Exception as e:
            logger.error(f"Failed to generate report: {e}")
            return False


def main():
    """Main CLI entry point"""
    parser = argparse.ArgumentParser(
        description='Multi-source borrow fee enrichment orchestrator for GusTradeTable files',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run all available sources
  python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt

  # Use only FTP and iBorrowDesk
  python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt --sources ftp,iborrowdesk

  # Generate detailed report
  python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt --report ./runs/22-Nov-2025/borrow_fee_report.txt

  # Use cached FTP data
  python3 borrow_fee_orchestrator.py --input ./runs/22-Nov-2025/GusTradeTable.txt --ftp-cache ./data/borrow_fees.csv
        """
    )

    parser.add_argument('--input', '-i', required=True,
                        help='Path to GusTradeTable file to enrich')
    parser.add_argument('--output', '-o',
                        help='Output path for enriched GTT (default: overwrite input)')
    parser.add_argument('--sources', default='ftp,iborrowdesk',
                        help='Comma-separated list of sources to use (default: ftp,iborrowdesk)')
    parser.add_argument('--ftp-cache',
                        help='Path to FTP CSV cache (default: ./data/borrow_fees.csv)')
    parser.add_argument('--ibd-cache',
                        help='Path to iBorrowDesk CSV cache (default: ./data/iborrowdesk_cache.csv)')
    parser.add_argument('--report',
                        help='Generate enrichment report at this path')
    parser.add_argument('--no-fallback', action='store_true',
                        help='Fetch all symbols from iBorrowDesk (not just BF1=N/A)')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Enable verbose debug logging')

    args = parser.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)

    # Validate input file
    if not os.path.exists(args.input):
        logger.error(f"Input file not found: {args.input}")
        return 1

    # Set default cache paths
    if not args.ftp_cache:
        args.ftp_cache = './data/borrow_fees.csv'
    if not args.ibd_cache:
        args.ibd_cache = './data/iborrowdesk_cache.csv'

    # Parse sources
    sources = [s.strip().lower() for s in args.sources.split(',')]
    logger.info(f"Using sources: {', '.join(sources)}")

    # Create orchestrator
    orchestrator = BorrowFeeOrchestrator()

    # Determine output path
    output_path = args.output or args.input

    # Run enrichment pipeline
    logger.info("=" * 60)
    logger.info("MULTI-SOURCE BORROW FEE ENRICHMENT PIPELINE")
    logger.info("=" * 60)
    logger.info(f"Input:  {args.input}")
    logger.info(f"Output: {output_path}")
    logger.info("")

    # Source 1: IBKR FTP
    if 'ftp' in sources:
        if not orchestrator.run_ftp_enrichment(output_path, args.ftp_cache):
            logger.error("FTP enrichment failed")
            return 1
        logger.info("")

    # Source 2: iBorrowDesk
    if 'iborrowdesk' in sources or 'ibd' in sources:
        fallback_only = not args.no_fallback
        if not orchestrator.run_iborrowdesk_enrichment(output_path, args.ibd_cache, fallback_only):
            logger.error("iBorrowDesk enrichment failed")
            return 1
        logger.info("")

    # Source 3: ShortableStocks (future)
    if 'shortablestocks' in sources or 'ss' in sources:
        logger.warning("ShortableStocks source not yet implemented")

    # Add BF_BEST column
    if not orchestrator.add_best_value_columns(output_path):
        logger.error("Failed to add best value column")
        return 1

    logger.info("")
    logger.info("=" * 60)
    logger.info("ENRICHMENT COMPLETED SUCCESSFULLY")
    logger.info("=" * 60)

    # Generate report if requested
    if args.report:
        logger.info("")
        if not orchestrator.generate_report(args.report):
            logger.warning("Failed to generate report (non-fatal)")

    return 0


if __name__ == '__main__':
    sys.exit(main())
