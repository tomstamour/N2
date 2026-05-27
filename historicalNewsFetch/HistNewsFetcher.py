#!/usr/bin/env python3
"""
HistNewsFetcher.py - Fetch historical news data from Alpaca REST API

Fetches historical news articles for a given stock symbol and date range,
saving each article as an individual JSON file. Implements pagination,
rate limiting with exponential backoff, and graceful shutdown handling.
"""

import argparse
import logging
import json
import signal
import sys
import os
import time
from datetime import datetime, timedelta
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


class ArgumentParser:
    """Parse and validate command-line arguments."""

    @staticmethod
    def parse_args():
        """Parse and validate CLI arguments with fail-fast validation."""
        parser = argparse.ArgumentParser(
            description="Fetch historical news from Alpaca API and save to individual JSON files"
        )

        parser.add_argument(
            "--symbol",
            required=True,
            help="Stock ticker symbol (e.g., NVDA, TSLA)"
        )

        parser.add_argument(
            "--start-time",
            required=True,
            help='Start date for news search (YYYY-MM-DD format, e.g., "2022-01-01")'
        )

        parser.add_argument(
            "--end-time",
            required=True,
            help='End date for news search (YYYY-MM-DD format, e.g., "2022-12-31")'
        )

        parser.add_argument(
            "--output-dir",
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

        args = parser.parse_args()

        # Validate arguments
        ArgumentParser._validate_arguments(args)

        return args

    @staticmethod
    def _validate_arguments(args):
        """Validate all arguments with fail-fast approach."""
        # Validate symbol format
        if not args.symbol or not args.symbol.replace('-', '').isalnum():
            print(f"✗ Invalid symbol format: {args.symbol}", file=sys.stderr)
            print("  Symbol must be alphanumeric (e.g., NVDA, BRK-B)", file=sys.stderr)
            sys.exit(1)

        args.symbol = args.symbol.upper()

        # Validate date formats
        try:
            start_date = datetime.strptime(args.start_time, "%Y-%m-%d")
        except ValueError:
            print(f"✗ Invalid start-time format: {args.start_time}", file=sys.stderr)
            print('  Use YYYY-MM-DD format (e.g., "2022-01-01")', file=sys.stderr)
            sys.exit(1)

        try:
            end_date = datetime.strptime(args.end_time, "%Y-%m-%d")
        except ValueError:
            print(f"✗ Invalid end-time format: {args.end_time}", file=sys.stderr)
            print('  Use YYYY-MM-DD format (e.g., "2022-12-31")', file=sys.stderr)
            sys.exit(1)

        # Validate date range (allow same day)
        if start_date > end_date:
            print(f"✗ Invalid date range: start_date must be before or equal to end_date", file=sys.stderr)
            print(f"  Start: {args.start_time}, End: {args.end_time}", file=sys.stderr)
            sys.exit(1)

        # Validate API keys file exists
        if not Path(args.api_keys).exists():
            print(f"✗ API keys file not found: {args.api_keys}", file=sys.stderr)
            sys.exit(1)

        # Validate output directory can be created
        try:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ Cannot create output directory: {args.output_dir}", file=sys.stderr)
            print(f"  Error: {e}", file=sys.stderr)
            sys.exit(1)

        # Validate log directory can be created
        try:
            Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ Cannot create log directory: {args.log_dir}", file=sys.stderr)
            print(f"  Error: {e}", file=sys.stderr)
            sys.exit(1)


class NewsFileHandler:
    """Handle saving news articles to individual JSON files."""

    @staticmethod
    def get_news_filename(symbol, created_at, output_dir):
        """
        Generate filename from article creation date with counter logic.

        Args:
            symbol: Stock ticker symbol
            created_at: Article creation timestamp (format: "YYYY-MM-DD HH:MM:SS+TZ")
            output_dir: Directory to save files

        Returns:
            Path object for the output file
        """
        try:
            # Parse created_at timestamp
            # Format: "2026-01-27 16:37:47+00:00"
            timestamp_str = created_at.split('+')[0].split('-')[0:3]
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
    def save_news_article(article, symbol, output_dir):
        """
        Save a single news article to a JSON file with atomic write.

        Args:
            article: News article object from API
            symbol: Stock ticker symbol
            output_dir: Directory to save files

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            filepath = NewsFileHandler.get_news_filename(
                symbol, str(article.created_at), output_dir
            )

            # Extract article data - format created_at and updated_at as strings
            created_at = getattr(article, 'created_at', None)
            updated_at = getattr(article, 'updated_at', None)

            # Convert datetime to string format (match existing format)
            if created_at:
                created_at = str(created_at).replace('TzInfo(0)', '+00:00')
            if updated_at:
                updated_at = str(updated_at).replace('TzInfo(0)', '+00:00')

            news_data = {
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

            # Write to temp file first for atomic operation
            temp_path = f"{filepath}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(news_data, f, indent=2, default=str)

            # Atomic rename
            os.replace(temp_path, filepath)
            logger.debug(f"Saved article {article.id} to {filepath.name}")
            return True

        except Exception as e:
            logger.error(f"Error saving article {getattr(article, 'id', 'unknown')}: {e}")
            return False

    @staticmethod
    def article_exists(article_id, output_dir, symbol):
        """
        Check if article already exists by searching symbol-prefixed JSON files for article ID.

        Args:
            article_id: ID of the article to check
            output_dir: Directory to search
            symbol: Stock symbol to scope the search (only checks {symbol}_*.json files)

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
                # Create request
                request = NewsRequest(
                    symbols=symbol,
                    start=start_date,
                    end=end_date,
                    limit=50,
                    include_content=True,
                    page_token=page_token
                )

                # Fetch page with retries
                response = self._fetch_with_retry(request)

                if not response or not response.data or not response.data.get('news'):
                    logger.debug(f"No more articles in response")
                    break

                articles = response.data.get('news', [])
                all_articles.extend(articles)
                page_count += 1
                self.pages_fetched = page_count
                logger.info(
                    f"Progress: Fetched {len(all_articles)} articles "
                    f"({page_count} pages) for {symbol}"
                )

                # Check for next page
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

            # Check for rate limiting
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
                    logger.error(f"Max retries exceeded for rate limiting")
                    return None
            else:
                logger.error(f"Error fetching news: {e}")
                return None


class ProgressTracker:
    """Track and display progress of news fetching."""

    def __init__(self, symbol, start_date, end_date):
        self.symbol = symbol
        self.start_date = start_date
        self.end_date = end_date
        self.start_time = datetime.now()
        self.articles_fetched = 0
        self.articles_saved = 0
        self.articles_skipped = 0
        self.pages_fetched = 0

    def display_summary(self):
        """Display final summary statistics."""
        elapsed = datetime.now() - self.start_time

        logger.info("=" * 60)
        logger.info("Fetch Summary:")
        logger.info(f"  Symbol: {self.symbol}")
        logger.info(f"  Date range: {self.start_date.date()} to {self.end_date.date()}")
        logger.info(f"  Articles fetched: {self.articles_fetched}")
        logger.info(f"  Articles saved: {self.articles_saved}")
        logger.info(f"  Articles skipped (existing): {self.articles_skipped}")
        logger.info(f"  Pages fetched: {self.pages_fetched}")
        logger.info(f"  Time elapsed: {elapsed}")
        logger.info("=" * 60)


def setup_logging(log_dir):
    """Configure logging with console and file handlers."""
    global logger
    logger = logging.getLogger("HistNewsFetcher")
    logger.setLevel(logging.DEBUG)

    # Create log directory
    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    # File handler (DEBUG level)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_path / f"HistNewsFetcher_{today}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)

    # Console handler (INFO level)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # Format
    formatter = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    # Add handlers
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info("HistNewsFetcher logging initialized")


def signal_handler(sig, frame):
    """Handle SIGINT and SIGTERM for graceful shutdown."""
    global shutdown_requested
    logger.info("Received shutdown signal (Ctrl+C)")
    shutdown_requested = True


def main():
    """Main entry point."""
    global shutdown_requested

    try:
        # Parse and validate arguments
        args = ArgumentParser.parse_args()

        # Setup logging
        setup_logging(args.log_dir)

        logger.info("=" * 60)
        logger.info("HistNewsFetcher starting")
        logger.info("=" * 60)

        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Load API credentials
        try:
            api_key, secret_key = ConfigurationManager.load_alpaca_credentials(args.api_keys)
            logger.info(f"Loading API credentials from {args.api_keys}")
            # Set environment variables for NewsClient
            os.environ['APCA_API_KEY_ID'] = api_key
            os.environ['APCA_API_SECRET_KEY'] = secret_key
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Error loading API credentials: {e}")
            print(f"✗ Error loading API credentials: {e}", file=sys.stderr)
            sys.exit(1)

        # Parse dates
        start_date = datetime.strptime(args.start_time, "%Y-%m-%d")
        end_date = datetime.strptime(args.end_time, "%Y-%m-%d")

        # Create output directory
        try:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
            logger.info(f"Output directory ready: {args.output_dir}")
        except Exception as e:
            logger.error(f"Error creating output directory: {e}")
            print(f"✗ Error creating output directory: {e}", file=sys.stderr)
            sys.exit(1)

        # Initialize tracker and fetcher
        tracker = ProgressTracker(args.symbol, start_date, end_date)
        logger.info(
            f"Fetching news for {args.symbol} "
            f"from {args.start_time} to {args.end_time}"
        )

        # Create fetcher after env vars are set
        try:
            fetcher = AlpacaHistoricalNewsFetcher(api_key, secret_key)
        except Exception as e:
            logger.error(f"Error initializing news fetcher: {e}")
            print(f"✗ Error initializing news fetcher: {e}", file=sys.stderr)
            sys.exit(1)

        # Fetch all articles
        articles = fetcher.fetch_all_news(args.symbol, start_date, end_date)
        tracker.articles_fetched = len(articles)
        tracker.pages_fetched = fetcher.pages_fetched
        logger.info(f"Fetched {len(articles)} articles total")

        # Save articles, skipping existing ones
        for article in articles:
            if shutdown_requested:
                logger.info("Shutdown requested, stopping save")
                break

            article_id = getattr(article, 'id', None)
            if not article_id:
                logger.warning("Article missing ID, skipping")
                continue

            # Check if already exists
            if NewsFileHandler.article_exists(article_id, args.output_dir, args.symbol):
                tracker.articles_skipped += 1
                logger.debug(f"Article {article_id} already exists, skipping")
            else:
                if NewsFileHandler.save_news_article(
                    article, args.symbol, args.output_dir
                ):
                    tracker.articles_saved += 1

        # Display summary
        logger.info(
            f"Saved {tracker.articles_saved} new articles, "
            f"skipped {tracker.articles_skipped} existing"
        )
        tracker.display_summary()

        logger.info("=" * 60)
        logger.info("HistNewsFetcher completed successfully")
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
