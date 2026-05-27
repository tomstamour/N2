#!/usr/bin/env python3
"""
HistNews2.py - Fetch filtered historical news data from Alpaca REST API

Fetches historical news articles for a list of stock symbols and a date range.
Saves only articles matching all of the following criteria:
  - source == "benzinga"
  - author == "Benzinga Newsdesk"
  - content == "" (empty string)
  - summary == "" (empty string)
  - symbols list contains exactly one symbol

Implements pagination, rate limiting with exponential backoff, and graceful
shutdown handling. Reuses patterns from HistNewsFetcher.py.
"""

import argparse
import logging
import json
import signal
import sys
import os
import time
from datetime import datetime
from pathlib import Path

from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest


# Global variables for graceful shutdown
shutdown_requested = False
logger = None


class ConfigurationManager:
    """Load and manage configuration for the script."""

    @staticmethod
    def load_alpaca_credentials(file_path):
        """
        Parse API credentials from file.

        Expected format:
        Endpoint:
        https://...
        Key:
        <API_KEY>
        Secret:
        <SECRET_KEY>
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"API keys file not found: {file_path}")

        api_key = None
        secret_key = None

        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if line.startswith('Key:'):
                        if i + 1 < len(lines):
                            api_key = lines[i + 1].strip()
                    elif line.startswith('Secret'):  # Match "Secret:" or "Secret key:"
                        if i + 1 < len(lines):
                            secret_key = lines[i + 1].strip()
                    i += 1
        except Exception as e:
            raise ValueError(f"Error parsing API credentials file: {e}")

        if not api_key:
            raise ValueError("Missing 'Key:' field in API credentials file")
        if not secret_key:
            raise ValueError("Missing 'Secret:' field in API credentials file")

        return api_key, secret_key


class SymbolListLoader:
    """Load and validate a list of stock symbols from a text file."""

    @staticmethod
    def load(file_path):
        """
        Read symbols from a text file (one symbol per line).

        Skips blank lines and lines starting with '#'.
        Uppercases all symbols and validates format.

        Args:
            file_path: Path to the symbols file

        Returns:
            List of validated, uppercased symbol strings
        """
        path = Path(file_path)
        if not path.exists():
            print(f"✗ Symbols file not found: {file_path}", file=sys.stderr)
            sys.exit(1)

        symbols = []
        try:
            with open(path, 'r') as f:
                for lineno, line in enumerate(f, 1):
                    symbol = line.strip()
                    if not symbol or symbol.startswith('#'):
                        continue
                    symbol = symbol.upper()
                    if not symbol.replace('-', '').isalnum():
                        print(
                            f"✗ Invalid symbol on line {lineno}: {symbol}",
                            file=sys.stderr
                        )
                        sys.exit(1)
                    symbols.append(symbol)
        except Exception as e:
            print(f"✗ Error reading symbols file: {e}", file=sys.stderr)
            sys.exit(1)

        if not symbols:
            print(f"✗ No symbols found in file: {file_path}", file=sys.stderr)
            sys.exit(1)

        return symbols


class ExcludedStringsLoader:
    """Load a list of headline-exclusion strings from a text file."""

    @staticmethod
    def load(file_path):
        """
        Read exclusion strings from a text file (one string per line).

        Skips blank lines and lines starting with '#'.

        Args:
            file_path: Path to the exclusion strings file

        Returns:
            List of strings (original case preserved; comparison is done case-insensitively)
        """
        path = Path(file_path)
        if not path.exists():
            print(f"✗ Excluded strings file not found: {file_path}", file=sys.stderr)
            sys.exit(1)

        strings = []
        try:
            with open(path, 'r') as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith('#'):
                        continue
                    strings.append(s)
        except Exception as e:
            print(f"✗ Error reading excluded strings file: {e}", file=sys.stderr)
            sys.exit(1)

        return strings


class ArticleFilter:
    """Filter news articles by the required criteria."""

    @staticmethod
    def matches_criteria(article_data, excluded_strings=None):
        """
        Check if an article dict matches all required filter criteria.

        Args:
            article_data: dict with article fields (same structure as saved JSON)
            excluded_strings: optional list of strings; if any is found as a
                case-insensitive substring in the headline, the article is rejected

        Returns:
            True if all criteria are met, False otherwise
        """
        if not (
            article_data.get('content') == "" and
            article_data.get('summary') == "" and
            article_data.get('source', '').lower() == 'benzinga' and
            article_data.get('author') == 'Benzinga Newsdesk' and
            len(article_data.get('symbols', [])) == 1
        ):
            return False

        if excluded_strings:
            headline = (article_data.get('headline') or '').lower()
            for s in excluded_strings:
                if s.lower() in headline:
                    return False

        return True


class NewsFileHandler:
    """Handle saving news articles to individual JSON files."""

    @staticmethod
    def get_news_filename(symbol, created_at, output_dir):
        """
        Generate filename from article creation date with counter logic.

        Args:
            symbol: Stock ticker symbol
            created_at: Article creation timestamp string
            output_dir: Directory to save files

        Returns:
            Path object for the output file
        """
        try:
            date_part = created_at.split(' ')[0]  # Get YYYY-MM-DD part
            date_obj = datetime.strptime(date_part, "%Y-%m-%d")
            date_str = date_obj.strftime("%d-%b-%Y").lower()  # dd-mmm-yyyy
        except Exception as e:
            logger.warning(f"Error parsing timestamp {created_at}, using current date: {e}")
            date_str = datetime.now().strftime("%d-%b-%Y").lower()

        base_filename = f"{symbol}_{date_str}.json"
        filepath = Path(output_dir) / base_filename

        # If file exists, add counter
        if filepath.exists():
            counter = 1
            while True:
                new_filename = f"{symbol}_{date_str}_{counter}.json"
                filepath = Path(output_dir) / new_filename
                if not filepath.exists():
                    break
                counter += 1

        return filepath

    @staticmethod
    def save_news_article(article_data, symbol, output_dir):
        """
        Save a pre-built article dict to a JSON file with atomic write.

        Args:
            article_data: dict with article fields
            symbol: Stock ticker symbol
            output_dir: Directory to save files

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            filepath = NewsFileHandler.get_news_filename(
                symbol, str(article_data.get('created_at', '')), output_dir
            )

            # Write to temp file first for atomic operation
            temp_path = f"{filepath}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(article_data, f, indent=2, default=str)

            # Atomic rename
            os.replace(temp_path, filepath)
            logger.debug(f"Saved article {article_data.get('id')} to {filepath.name}")
            return True

        except Exception as e:
            logger.error(f"Error saving article {article_data.get('id', 'unknown')}: {e}")
            return False

    @staticmethod
    def article_exists(article_id, output_dir, symbol):
        """
        Check if article already exists by searching symbol-prefixed JSON files.

        Args:
            article_id: ID of the article to check
            output_dir: Directory to search
            symbol: Stock symbol to scope the search

        Returns:
            True if article exists, False otherwise
        """
        output_path = Path(output_dir)
        if not output_path.exists():
            return False

        try:
            for json_file in output_path.glob(f"{symbol}_*.json"):
                try:
                    with open(json_file, 'r') as f:
                        data = json.load(f)
                        if data.get('id') == article_id:
                            return True
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"Error checking for existing article: {e}")

        return False


class AlpacaHistoricalNewsFetcher:
    """Fetch historical news from Alpaca API with pagination and rate limiting."""

    def __init__(self, api_key, secret_key):
        """Initialize the news client."""
        self.client = NewsClient(api_key=api_key, secret_key=secret_key)
        self.max_retries = 5
        self.base_retry_delay = 2  # seconds
        self.pages_fetched = 0

    def fetch_all_news(self, symbol, start_date, end_date):
        """
        Fetch all news articles for a symbol in date range with pagination.

        Args:
            symbol: Stock ticker symbol
            start_date: Start date (datetime object)
            end_date: End date (datetime object)

        Returns:
            List of news article objects
        """
        all_articles = []
        page_token = None
        page_count = 0

        while True:
            if shutdown_requested:
                logger.info("Shutdown requested, stopping fetch")
                break

            try:
                request = NewsRequest(
                    symbols=symbol,
                    start=start_date,
                    end=end_date,
                    limit=50,
                    include_content=True,
                    page_token=page_token
                )

                response = self._fetch_with_retry(request)

                if not response or not response.data or not response.data.get('news'):
                    logger.debug("No more articles in response")
                    break

                articles = response.data.get('news', [])
                all_articles.extend(articles)
                page_count += 1
                self.pages_fetched = page_count
                logger.info(
                    f"  Fetched {len(all_articles)} articles "
                    f"({page_count} pages) for {symbol}"
                )

                page_token = response.next_page_token
                if not page_token:
                    logger.debug("No more pages available")
                    break

            except Exception as e:
                logger.error(f"Error fetching news page: {e}")
                break

        return all_articles

    def _fetch_with_retry(self, request, retry_count=0):
        """
        Fetch news with exponential backoff retry logic.

        Args:
            request: NewsRequest object
            retry_count: Current retry count

        Returns:
            Response object or None if failed
        """
        try:
            return self.client.get_news(request)
        except Exception as e:
            error_str = str(e).lower()

            if "429" in error_str or "rate limit" in error_str:
                if retry_count < self.max_retries:
                    delay = min(self.base_retry_delay * (2 ** retry_count), 64)
                    logger.warning(
                        f"Rate limited, retrying in {delay}s "
                        f"(attempt {retry_count + 1}/{self.max_retries})"
                    )
                    time.sleep(delay)
                    return self._fetch_with_retry(request, retry_count + 1)
                else:
                    logger.error("Max retries exceeded for rate limiting")
                    return None
            else:
                logger.error(f"Error fetching news: {e}")
                return None


class ProgressTracker:
    """Track and display progress across all symbols."""

    def __init__(self, symbols, start_date, end_date):
        self.symbols = symbols
        self.start_date = start_date
        self.end_date = end_date
        self.start_time = datetime.now()

        # Grand totals
        self.total_fetched = 0
        self.total_matched = 0
        self.total_saved = 0
        self.total_skipped = 0
        self.total_pages = 0

    def log_symbol_summary(self, symbol, fetched, matched, saved, skipped, pages):
        """Log per-symbol statistics."""
        self.total_fetched += fetched
        self.total_matched += matched
        self.total_saved += saved
        self.total_skipped += skipped
        self.total_pages += pages

        logger.info(
            f"  [{symbol}] fetched={fetched} matched={matched} "
            f"saved={saved} skipped(dup)={skipped} pages={pages}"
        )

    def display_summary(self):
        """Display final grand total summary."""
        elapsed = datetime.now() - self.start_time

        logger.info("=" * 60)
        logger.info("HistNews2 Summary:")
        logger.info(f"  Symbols processed: {len(self.symbols)}")
        logger.info(f"  Date range: {self.start_date.date()} to {self.end_date.date()}")
        logger.info(f"  Total articles fetched: {self.total_fetched}")
        logger.info(f"  Total articles matched filters: {self.total_matched}")
        logger.info(f"  Total articles saved: {self.total_saved}")
        logger.info(f"  Total articles skipped (existing): {self.total_skipped}")
        logger.info(f"  Total pages fetched: {self.total_pages}")
        logger.info(f"  Time elapsed: {elapsed}")
        logger.info("=" * 60)


def setup_logging(log_dir):
    """Configure logging with console and file handlers."""
    global logger
    logger = logging.getLogger("HistNews2")
    logger.setLevel(logging.DEBUG)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_path / f"HistNews2_{today}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("HistNews2 logging initialized")


def signal_handler(sig, frame):
    """Handle SIGINT and SIGTERM for graceful shutdown."""
    global shutdown_requested
    logger.info("Received shutdown signal (Ctrl+C)")
    shutdown_requested = True


def build_article_data(article):
    """
    Extract fields from an API article object into a dict.

    Returns the same structure saved by NewsFileHandler.
    """
    created_at = getattr(article, 'created_at', None)
    updated_at = getattr(article, 'updated_at', None)

    if created_at:
        created_at = str(created_at).replace('TzInfo(0)', '+00:00')
    if updated_at:
        updated_at = str(updated_at).replace('TzInfo(0)', '+00:00')

    return {
        'id': getattr(article, 'id', None),
        'headline': getattr(article, 'headline', None),
        'summary': getattr(article, 'summary', None),
        'author': getattr(article, 'author', None),
        'created_at': created_at,
        'updated_at': updated_at,
        'url': getattr(article, 'url', None),
        'content': getattr(article, 'content', None),
        'symbols': getattr(article, 'symbols', []),
        'source': getattr(article, 'source', None),
    }


def parse_args():
    """Parse and validate CLI arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch historical news from Alpaca API for a list of symbols, "
            "saving only Benzinga Newsdesk articles with empty content and summary."
        )
    )

    parser.add_argument(
        "--symbols",
        required=True,
        help="Path to text file with stock symbols (one per line)"
    )

    parser.add_argument(
        "--start-date",
        required=True,
        help='Start date in DD-MM-YYYY format (e.g. "01-01-2022")'
    )

    parser.add_argument(
        "--end-date",
        required=True,
        help='End date in DD-MM-YYYY format (e.g. "31-12-2022")'
    )

    parser.add_argument(
        "--output",
        default="./outputs/",
        help="Output directory for JSON files (default: ./outputs/)"
    )

    parser.add_argument(
        "--api-keys",
        default="./alpaca_API-Keys.txt",
        help="API credentials file (default: ./alpaca_API-Keys.txt)"
    )

    parser.add_argument(
        "--log-dir",
        default="./logs",
        help="Log directory (default: ./logs)"
    )

    parser.add_argument(
        "--excluded-strings",
        default=None,
        help="Path to text file with strings to exclude from headlines (one per line, optional)"
    )

    args = parser.parse_args()

    # Validate date formats
    try:
        args.start_date_obj = datetime.strptime(args.start_date, "%d-%m-%Y")
    except ValueError:
        print(f"✗ Invalid --start-date format: {args.start_date}", file=sys.stderr)
        print('  Use DD-MM-YYYY format (e.g. "01-01-2022")', file=sys.stderr)
        sys.exit(1)

    try:
        args.end_date_obj = datetime.strptime(args.end_date, "%d-%m-%Y")
    except ValueError:
        print(f"✗ Invalid --end-date format: {args.end_date}", file=sys.stderr)
        print('  Use DD-MM-YYYY format (e.g. "31-12-2022")', file=sys.stderr)
        sys.exit(1)

    if args.start_date_obj > args.end_date_obj:
        print("✗ --start-date must be before or equal to --end-date", file=sys.stderr)
        print(f"  Start: {args.start_date}, End: {args.end_date}", file=sys.stderr)
        sys.exit(1)

    # Validate excluded strings file if provided
    if args.excluded_strings and not Path(args.excluded_strings).exists():
        print(f"✗ Excluded strings file not found: {args.excluded_strings}", file=sys.stderr)
        sys.exit(1)

    # Validate API keys file
    if not Path(args.api_keys).exists():
        print(f"✗ API keys file not found: {args.api_keys}", file=sys.stderr)
        sys.exit(1)

    # Create output and log directories
    for dir_path, label in [(args.output, "output"), (args.log_dir, "log")]:
        try:
            Path(dir_path).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ Cannot create {label} directory: {dir_path}", file=sys.stderr)
            print(f"  Error: {e}", file=sys.stderr)
            sys.exit(1)

    return args


def main():
    """Main entry point."""
    global shutdown_requested

    try:
        args = parse_args()
        setup_logging(args.log_dir)

        logger.info("=" * 60)
        logger.info("HistNews2 starting")
        logger.info("=" * 60)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Load API credentials
        try:
            api_key, secret_key = ConfigurationManager.load_alpaca_credentials(args.api_keys)
            logger.info(f"Loaded API credentials from {args.api_keys}")
            os.environ['APCA_API_KEY_ID'] = api_key
            os.environ['APCA_API_SECRET_KEY'] = secret_key
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Error loading API credentials: {e}")
            print(f"✗ Error loading API credentials: {e}", file=sys.stderr)
            sys.exit(1)

        # Load symbols
        symbols = SymbolListLoader.load(args.symbols)
        logger.info(f"Loaded {len(symbols)} symbols from {args.symbols}")
        logger.info(f"Date range: {args.start_date} to {args.end_date}")
        logger.info(f"Output directory: {args.output}")

        # Load excluded strings (optional)
        excluded_strings = []
        if args.excluded_strings:
            excluded_strings = ExcludedStringsLoader.load(args.excluded_strings)
            logger.info(
                f"Loaded {len(excluded_strings)} excluded headline strings "
                f"from {args.excluded_strings}"
            )

        # Initialize fetcher and tracker
        try:
            fetcher = AlpacaHistoricalNewsFetcher(api_key, secret_key)
        except Exception as e:
            logger.error(f"Error initializing news fetcher: {e}")
            print(f"✗ Error initializing news fetcher: {e}", file=sys.stderr)
            sys.exit(1)

        tracker = ProgressTracker(symbols, args.start_date_obj, args.end_date_obj)

        # Process each symbol
        for symbol in symbols:
            if shutdown_requested:
                logger.info("Shutdown requested, stopping symbol loop")
                break

            logger.info(f"Processing {symbol} ...")
            fetcher.pages_fetched = 0

            articles = fetcher.fetch_all_news(
                symbol, args.start_date_obj, args.end_date_obj
            )

            matched = 0
            saved = 0
            skipped = 0

            for article in articles:
                if shutdown_requested:
                    logger.info("Shutdown requested, stopping article loop")
                    break

                article_id = getattr(article, 'id', None)
                if not article_id:
                    logger.warning("Article missing ID, skipping")
                    continue

                article_data = build_article_data(article)

                if not ArticleFilter.matches_criteria(article_data, excluded_strings):
                    logger.debug(f"Article {article_id} did not match filters, discarding")
                    continue

                matched += 1

                if NewsFileHandler.article_exists(article_id, args.output, symbol):
                    skipped += 1
                    logger.debug(f"Article {article_id} already exists, skipping")
                else:
                    if NewsFileHandler.save_news_article(article_data, symbol, args.output):
                        saved += 1

            tracker.log_symbol_summary(
                symbol,
                fetched=len(articles),
                matched=matched,
                saved=saved,
                skipped=skipped,
                pages=fetcher.pages_fetched,
            )

        tracker.display_summary()

        logger.info("=" * 60)
        logger.info("HistNews2 completed successfully")
        logger.info("=" * 60)

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        print(f"✗ Unexpected error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
