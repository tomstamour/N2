#!/usr/bin/env python3
"""
Stock Sector Finder (SSF)
GUS-ATS - Gap Up Short Algorithmic Trading System

This script reads the CSV output from Gainers_Scanner_Script.py and adds sector/industry
information for each stock using the Yahoo Finance API (yfinance library).

Input: gainers_scan_{timestamp}.csv
Output: gainers_scan_{timestamp}_sectors.csv (with added "Sector" column)
"""

import sys
import time
import datetime
import logging
import pandas as pd
from pathlib import Path
from typing import Optional, Dict

# yfinance import
try:
    import yfinance as yf
except ImportError:
    print("Error: yfinance module not found. Please install it with: pip install yfinance")
    exit(1)

# =====================
# Configuration Loading
# =====================
def load_config(config_file='GUS_Config_File.txt'):
    """Load configuration from text file."""
    config = {}
    config_path = Path(__file__).parent / config_file

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            # Skip comments and empty lines
            if not line or line.startswith('#'):
                continue

            if '=' in line:
                key, value = line.split('=', 1)
                key = key.strip()
                value = value.strip()

                # Type conversion
                if value.replace('.', '', 1).isdigit():
                    config[key] = float(value) if '.' in value else int(value)
                else:
                    config[key] = value

    return config

# Load configuration
try:
    CONFIG = load_config()
except Exception as e:
    print(f"Error loading configuration: {e}")
    exit(1)

# =====================
# Setup Logging
# =====================
LOG_DIR = Path(__file__).parent / CONFIG.get('LOG_DIR', 'logs')
LOG_DIR.mkdir(exist_ok=True)

log_filename = LOG_DIR / f"sector_finder_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_filename),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# =====================
# Main Sector Finder Class
# =====================
class SectorFinder:
    def __init__(self, config, csv_path: Optional[str] = None):
        self.config = config
        self.csv_path = csv_path
        self.csv_file = None
        self.request_delay = config.get('SECTOR_REQUEST_DELAY', 0.1)

    def find_latest_csv(self) -> Optional[Path]:
        """Find the most recent gainers_scan CSV file."""
        output_dir = Path(__file__).parent / self.config.get('OUTPUT_DIR', 'output')

        if not output_dir.exists():
            logger.error(f"Output directory not found: {output_dir}")
            return None

        # Find all gainers_scan CSV files
        csv_files = list(output_dir.glob("gainers_scan_*.csv"))

        # Filter out files that already have sectors or trends
        csv_files = [f for f in csv_files if '_sectors.csv' not in f.name and '_trends.csv' not in f.name]

        if not csv_files:
            logger.error(f"No gainers_scan CSV files found in {output_dir}")
            return None

        # Sort by modification time and return the most recent
        latest = max(csv_files, key=lambda f: f.stat().st_mtime)
        logger.info(f"Found latest CSV: {latest}")
        return latest

    def load_csv(self) -> Optional[pd.DataFrame]:
        """Load the gainers CSV file."""
        if self.csv_path:
            csv_file = Path(self.csv_path)
        else:
            csv_file = self.find_latest_csv()

        if not csv_file or not csv_file.exists():
            logger.error(f"CSV file not found: {csv_file}")
            return None

        try:
            df = pd.read_csv(csv_file)
            logger.info(f"Loaded {len(df)} stocks from {csv_file}")

            # Validate required columns
            required_cols = ['Symbol']
            missing_cols = [col for col in required_cols if col not in df.columns]

            if missing_cols:
                logger.error(f"Missing required columns: {missing_cols}")
                return None

            self.csv_file = csv_file  # Store for output naming
            return df

        except Exception as e:
            logger.error(f"Error loading CSV: {e}")
            return None

    def get_sector_info(self, symbol: str) -> str:
        """
        Get sector and industry information for a stock using Yahoo Finance.
        Returns a comma-separated string like "Sector,Industry" or "N/A" if not available.
        """
        try:
            logger.info(f"Fetching sector/industry for {symbol}...")

            # Create ticker object
            ticker = yf.Ticker(symbol)

            # Get info
            info = ticker.info

            # Extract sector and industry
            sector = info.get('sector', '')
            industry = info.get('industry', '')

            # Build result string
            if sector and industry:
                result = f"{sector},{industry}"
            elif sector:
                result = sector
            elif industry:
                result = industry
            else:
                result = "N/A"

            logger.info(f"{symbol}: Sector/Industry = {result}")

            # Rate limiting
            time.sleep(self.request_delay)

            return result

        except Exception as e:
            logger.error(f"Error fetching sector info for {symbol}: {e}")
            return "N/A"

    def process_stocks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Process all stocks and add Sector column."""
        logger.info(f"Processing {len(df)} stocks for sector/industry information...")

        # Add Sector column
        df['Sector'] = df['Symbol'].apply(lambda symbol: self.get_sector_info(symbol))

        logger.info(f"Sector information added for {len(df)} stocks.")

        # Log summary
        na_count = (df['Sector'] == 'N/A').sum()
        success_count = len(df) - na_count
        logger.info(f"Successfully retrieved sector info for {success_count}/{len(df)} stocks ({na_count} N/A)")

        return df

    def export_enhanced_csv(self, df: pd.DataFrame) -> Optional[Path]:
        """Export enhanced CSV with Sector column."""
        if df.empty:
            logger.warning("No data to export.")
            return None

        # Generate output filename based on input filename
        input_name = self.csv_file.stem  # e.g., "gainers_scan_20231005_120000"
        output_name = f"{input_name}_sectors.csv"
        output_path = self.csv_file.parent / output_name

        # Export to CSV
        df.to_csv(output_path, index=False)

        logger.info(f"Enhanced results exported to: {output_path}")
        return output_path

    def run(self):
        """Main execution flow."""
        try:
            # Load CSV
            df = self.load_csv()
            if df is None or df.empty:
                logger.error("No data to process. Exiting.")
                return None

            # Process stocks and add Sector column
            enhanced_df = self.process_stocks(df)

            # Export enhanced CSV
            output_file = self.export_enhanced_csv(enhanced_df)

            return output_file

        except Exception as e:
            logger.error(f"Error during sector finding: {e}", exc_info=True)
            return None

# =====================
# Main Entry Point
# =====================
def main():
    """Main function."""
    logger.info("=" * 60)
    logger.info("Stock Sector Finder (SSF) - GUS-ATS")
    logger.info("=" * 60)

    # Check for command-line CSV path
    csv_path = sys.argv[1] if len(sys.argv) > 1 else None

    if csv_path:
        logger.info(f"Using specified CSV file: {csv_path}")
    else:
        logger.info("Searching for latest gainers_scan CSV...")

    logger.info("")

    finder = SectorFinder(CONFIG, csv_path=csv_path)
    result_file = finder.run()

    if result_file:
        logger.info("")
        logger.info("=" * 60)
        logger.info(f"✓ Sector finding complete! Results saved to: {result_file}")
        logger.info("=" * 60)
    else:
        logger.error("Sector finding failed or no results found.")

    logger.info("Script terminated.")

if __name__ == "__main__":
    main()
