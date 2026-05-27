#!/usr/bin/env python3
"""
HistNewsFetcher2.py - Fetch recent news data from RTPR.io REST API

Fetches news articles for a given stock symbol from the last 24 hours,
saving each article as an individual JSON file. Implements rate limiting
with exponential backoff and graceful shutdown handling.

Note: RTPR.io retains articles for the last 24 hours only.
"""

import argparse
import hashlib
import logging
import json
import signal
import sys
import os
import time
from datetime import datetime
from pathlib import Path

import requests


# Global variables for graceful shutdown
shutdown_requested = False
logger = None

RTPR_BASE_URL = "https://api.rtpr.io"


class ConfigurationManager:
    """Load and manage configuration for the script."""

    @staticmethod
    def load_rtpr_credentials(file_path):
        """
        Parse API key from file.

        Expected format:
        Key:
        <API_KEY>
        """
        if not Path(file_path).exists():
            raise FileNotFoundError(f"API keys file not found: {file_path}")

        api_key = None

        try:
            with open(file_path, 'r') as f:
                lines = f.readlines()
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if line.startswith('Key:'):
                        if i + 1 < len(lines):
                            api_key = lines[i + 1].strip()
                    i += 1
        except Exception as e:
            raise ValueError(f"Error parsing API credentials file: {e}")

        if not api_key:
            raise ValueError("Missing 'Key:' field in API credentials file")

        return api_key


class ArgumentParser:
    """Parse and validate command-line arguments."""

    @staticmethod
    def parse_args():
        """Parse and validate CLI arguments with fail-fast validation."""
        parser = argparse.ArgumentParser(
            description="Fetch recent news from RTPR.io API (last 24h) and save to individual JSON files"
        )

        parser.add_argument(
            "--symbol",
            required=True,
            help="Stock ticker symbol (e.g., NVDA, TSLA)"
        )

        parser.add_argument(
            "--output-dir",
            default="./outputs/",
            help="Output directory for JSON files (default: ./outputs/)"
        )

        parser.add_argument(
            "--api-keys",
            default="./RTPR_API-Key.txt",
            help="API credentials file (default: ./RTPR_API-Key.txt)"
        )

        parser.add_argument(
            "--log-dir",
            default="./logs",
            help="Log directory (default: ./logs)"
        )

        args = parser.parse_args()

        ArgumentParser._validate_arguments(args)

        return args

    @staticmethod
    def _validate_arguments(args):
        """Validate all arguments with fail-fast approach."""
        if not args.symbol or not args.symbol.replace('-', '').isalnum():
            print(f"✗ Invalid symbol format: {args.symbol}", file=sys.stderr)
            print("  Symbol must be alphanumeric (e.g., NVDA, BRK-B)", file=sys.stderr)
            sys.exit(1)

        args.symbol = args.symbol.upper()

        if not Path(args.api_keys).exists():
            print(f"✗ API keys file not found: {args.api_keys}", file=sys.stderr)
            sys.exit(1)

        try:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ Cannot create output directory: {args.output_dir}", file=sys.stderr)
            print(f"  Error: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            Path(args.log_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            print(f"✗ Cannot create log directory: {args.log_dir}", file=sys.stderr)
            print(f"  Error: {e}", file=sys.stderr)
            sys.exit(1)


class NewsFileHandler:
    """Handle saving news articles to individual JSON files."""

    @staticmethod
    def get_news_filename(symbol, created, output_dir):
        """
        Generate filename from article creation date with counter logic.

        Args:
            symbol: Stock ticker symbol
            created: Article creation timestamp (ISO 8601, e.g. "2025-07-28T16:30:00.000Z")
            output_dir: Directory to save files

        Returns:
            Path object for the output file
        """
        try:
            date_part = created.split('T')[0]  # "2025-07-28"
            date_obj = datetime.strptime(date_part, "%Y-%m-%d")
            date_str = date_obj.strftime("%d-%b-%Y").lower()  # "28-jul-2025"
        except Exception as e:
            logger.warning(f"Error parsing timestamp {created}, using current date: {e}")
            date_str = datetime.now().strftime("%d-%b-%Y").lower()

        base_filename = f"{symbol}_{date_str}.json"
        filepath = Path(output_dir) / base_filename

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
        Save a single news article dict to a JSON file with atomic write.

        Args:
            article: News article dict from RTPR API
            symbol: Stock ticker symbol
            output_dir: Directory to save files

        Returns:
            True if saved successfully, False otherwise
        """
        try:
            output_path = Path(output_dir)
            output_path.mkdir(parents=True, exist_ok=True)

            created = article.get('created', '')
            filepath = NewsFileHandler.get_news_filename(symbol, created, output_dir)

            news_data = {
                'id': article.get('id'),
                'ticker': article.get('ticker'),
                'tickers': article.get('tickers', []),
                'exchange': article.get('exchange'),
                'title': article.get('title'),
                'author': article.get('author'),
                'created': created,
                'article_body': article.get('article_body'),
            }

            temp_path = f"{filepath}.tmp"
            with open(temp_path, 'w') as f:
                json.dump(news_data, f, indent=2, default=str)

            os.replace(temp_path, filepath)
            logger.debug(f"Saved article {article.get('id')} to {filepath.name}")
            return True

        except Exception as e:
            logger.error(f"Error saving article {article.get('id', 'unknown')}: {e}")
            return False

    @staticmethod
    def article_exists(article_id, output_dir, symbol):
        """
        Check if article already exists by scanning symbol-prefixed JSON files.

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


class RTPRNewsFetcher:
    """Fetch recent news from RTPR.io REST API with rate limiting."""

    def __init__(self, api_key):
        """Initialize with API key."""
        self.api_key = api_key
        self.max_retries = 5
        self.base_retry_delay = 2  # seconds

    def fetch_all_news(self, symbol):
        """
        Fetch up to 100 recent articles for a symbol (last 24 hours).

        Args:
            symbol: Stock ticker symbol

        Returns:
            List of article dicts
        """
        url = f"{RTPR_BASE_URL}/articles/{symbol}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        params = {"limit": 100}

        response = self._fetch_with_retry(url, headers, params)

        if response is None:
            return []

        try:
            data = response.json()
            articles = data.get("articles", [])
            for article in articles:
                if not article.get('id'):
                    # REST API doesn't return an id field; derive one from content
                    raw = f"{article.get('ticker','')}{article.get('created','')}{article.get('title','')}"
                    article['id'] = hashlib.sha1(raw.encode()).hexdigest()[:16]
            logger.info(f"Fetched {len(articles)} articles for {symbol}")
            return articles
        except Exception as e:
            logger.error(f"Error parsing response JSON: {e}")
            return []

    def _fetch_with_retry(self, url, headers, params, retry_count=0):
        """
        Fetch with exponential backoff retry on rate limiting.

        Returns:
            requests.Response or None if failed
        """
        try:
            response = requests.get(url, headers=headers, params=params, timeout=30)

            if response.status_code == 200:
                return response

            if response.status_code == 429:
                if retry_count < self.max_retries:
                    delay = min(self.base_retry_delay * (2 ** retry_count), 64)
                    logger.warning(
                        f"Rate limited, retrying in {delay}s "
                        f"(attempt {retry_count + 1}/{self.max_retries})"
                    )
                    time.sleep(delay)
                    return self._fetch_with_retry(url, headers, params, retry_count + 1)
                else:
                    logger.error("Max retries exceeded for rate limiting")
                    return None

            if response.status_code == 401:
                logger.error("Authentication failed: invalid API key")
                return None

            if response.status_code == 403:
                logger.error("Access forbidden: free trial may have expired")
                return None

            logger.error(f"Unexpected HTTP status {response.status_code}: {response.text}")
            return None

        except requests.exceptions.Timeout:
            logger.error("Request timed out")
            return None
        except Exception as e:
            logger.error(f"Error fetching news: {e}")
            return None


class ProgressTracker:
    """Track and display progress of news fetching."""

    def __init__(self, symbol):
        self.symbol = symbol
        self.start_time = datetime.now()
        self.articles_fetched = 0
        self.articles_saved = 0
        self.articles_skipped = 0

    def display_summary(self):
        """Display final summary statistics."""
        elapsed = datetime.now() - self.start_time

        logger.info("=" * 60)
        logger.info("Fetch Summary:")
        logger.info(f"  Symbol: {self.symbol}")
        logger.info(f"  Articles fetched: {self.articles_fetched}")
        logger.info(f"  Articles saved: {self.articles_saved}")
        logger.info(f"  Articles skipped (existing): {self.articles_skipped}")
        logger.info(f"  Time elapsed: {elapsed}")
        logger.info("=" * 60)


def setup_logging(log_dir):
    """Configure logging with console and file handlers."""
    global logger
    logger = logging.getLogger("HistNewsFetcher2")
    logger.setLevel(logging.DEBUG)

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_path / f"HistNewsFetcher2_{today}.log"
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

    logger.info("HistNewsFetcher2 logging initialized")


def signal_handler(sig, frame):
    """Handle SIGINT and SIGTERM for graceful shutdown."""
    global shutdown_requested
    logger.info("Received shutdown signal (Ctrl+C)")
    shutdown_requested = True


def main():
    """Main entry point."""
    global shutdown_requested

    try:
        args = ArgumentParser.parse_args()

        setup_logging(args.log_dir)

        logger.info("=" * 60)
        logger.info("HistNewsFetcher2 starting (RTPR.io — last 24 hours only)")
        logger.info("=" * 60)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            api_key = ConfigurationManager.load_rtpr_credentials(args.api_keys)
            logger.info(f"Loaded API credentials from {args.api_keys}")
        except (FileNotFoundError, ValueError) as e:
            logger.error(f"Error loading API credentials: {e}")
            print(f"✗ Error loading API credentials: {e}", file=sys.stderr)
            sys.exit(1)

        try:
            Path(args.output_dir).mkdir(parents=True, exist_ok=True)
            logger.info(f"Output directory ready: {args.output_dir}")
        except Exception as e:
            logger.error(f"Error creating output directory: {e}")
            print(f"✗ Error creating output directory: {e}", file=sys.stderr)
            sys.exit(1)

        tracker = ProgressTracker(args.symbol)
        logger.info(f"Fetching recent news for {args.symbol}")

        fetcher = RTPRNewsFetcher(api_key)
        articles = fetcher.fetch_all_news(args.symbol)
        tracker.articles_fetched = len(articles)

        for article in articles:
            if shutdown_requested:
                logger.info("Shutdown requested, stopping save")
                break

            article_id = article.get('id')
            if not article_id:
                logger.warning("Article missing ID, skipping")
                continue

            if NewsFileHandler.article_exists(article_id, args.output_dir, args.symbol):
                tracker.articles_skipped += 1
                logger.debug(f"Article {article_id} already exists, skipping")
            else:
                if NewsFileHandler.save_news_article(article, args.symbol, args.output_dir):
                    tracker.articles_saved += 1

        logger.info(
            f"Saved {tracker.articles_saved} new articles, "
            f"skipped {tracker.articles_skipped} existing"
        )
        tracker.display_summary()

        logger.info("=" * 60)
        logger.info("HistNewsFetcher2 completed successfully")
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
