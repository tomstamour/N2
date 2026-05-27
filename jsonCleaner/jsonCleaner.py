#!/usr/bin/env python3
"""
jsonCleaner.py - Extract and clean news article text from Alpaca Benzinga JSON files

Extracts headline, summary, and content fields from Alpaca News API JSON files,
applies multi-step text cleaning (HTML stripping, entity decoding, whitespace
normalization, URL removal), and writes cleaned fields to a single JSON output file.
Processes fields in parallel using ProcessPoolExecutor for efficient CPU-bound
text processing. Ready for downstream FinBERT sentiment analysis.
"""

import argparse
import logging
import json
import signal
import sys
import os
import re
import html
import tempfile
from datetime import datetime
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

try:
    from lxml import html as lxml_html
    LXML_AVAILABLE = True
except ImportError:
    LXML_AVAILABLE = False


# Global variables for graceful shutdown
shutdown_requested = False
logger = None

# Precompiled regexes for TextCleaner hot path. Compiling once at import time
# avoids re-compiling on every clean() call (each article = 6 cleaning passes).
_RE_HTML_TAG       = re.compile(r'<[^>]+>')
_RE_WHITESPACE     = re.compile(r'\s+')
_RE_HTTP_URL       = re.compile(r'https?://[^\s\)]+')
_RE_WWW_URL        = re.compile(r'www\.[^\s\)]+')
_RE_TICKER_PARENS  = re.compile(r'\([A-Z]+:[A-Z0-9]+\)')


def setup_logging(log_dir):
    """
    Setup dual-handler logging: console (INFO) and file (DEBUG).

    Args:
        log_dir: Path to directory for log files (created if missing)

    Returns:
        logging.Logger: Configured logger instance
    """
    global logger

    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)

    # Create logger
    logger = logging.getLogger('jsonCleaner')
    logger.setLevel(logging.DEBUG)

    # Prevent duplicate handlers
    logger.handlers.clear()

    # File handler (DEBUG level, detailed format)
    log_file = log_dir / f"jsonCleaner_{datetime.now().strftime('%Y-%m-%d')}.log"
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    file_handler.setFormatter(file_formatter)

    # Console handler (INFO level, simple format)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter('%(levelname)s: %(message)s')
    console_handler.setFormatter(console_formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"Logging initialized. Log file: {log_file}")
    return logger


def signal_handler(sig, frame):
    """Handle SIGINT/SIGTERM for graceful shutdown."""
    global shutdown_requested
    shutdown_requested = True
    logger.warning(f"Signal {sig} received. Initiating graceful shutdown...")
    sys.exit(0)


class ArgumentParser:
    """Parse and validate command-line arguments."""

    @staticmethod
    def parse_args():
        """Parse and validate CLI arguments with fail-fast validation."""
        parser = argparse.ArgumentParser(
            description="Extract and clean news article text from Alpaca Benzinga JSON files. "
                        "Outputs cleaned fields to a single JSON file."
        )

        parser.add_argument(
            "--input",
            required=True,
            help="Path to input JSON file from Alpaca News API"
        )

        parser.add_argument(
            "--log-dir",
            default="./logs",
            help="Directory for log files (default: ./logs)"
        )

        parser.add_argument(
            "--parts-to-process",
            type=int,
            choices=[1, 2, 3],
            default=3,
            help="Parts to process and include in output: "
                 "1=headline only, 2=headline+summary, 3=headline+summary+content (default: 3)"
        )

        parser.add_argument(
            "--output",
            default=None,
            help="Path to output JSON file (default: auto-generated as <input>_cleaned.json)"
        )

        args = parser.parse_args()

        # Validate input file
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"Input file not found: {args.input}")

        if not input_path.is_file():
            raise ValueError(f"Input path is not a file: {args.input}")

        # Validate JSON structure
        try:
            with open(input_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON file: {e}")
        except Exception as e:
            raise ValueError(f"Error reading JSON file: {e}")

        # Check required fields
        required_fields = {'headline', 'summary', 'content'}
        if not isinstance(data, dict):
            raise ValueError("JSON root must be an object")

        missing_fields = required_fields - set(data.keys())
        if missing_fields:
            raise ValueError(f"Missing required fields: {missing_fields}")

        logger.info(f"Arguments validated. Input: {args.input}, Log dir: {args.log_dir}")
        return args


class TextCleaner:
    """Text cleaning pipeline with multi-step HTML/entity/whitespace/URL processing."""

    @staticmethod
    def strip_html_tags(text):
        """
        Strip HTML tags using lxml with fallback to regex.

        Args:
            text: Text potentially containing HTML tags

        Returns:
            str: Text with HTML tags removed
        """
        if not text:
            return ""

        if LXML_AVAILABLE:
            try:
                doc = lxml_html.fromstring(f"<div>{text}</div>")
                result = doc.text_content()
                return result if result else ""
            except Exception as e:
                logger.debug(f"lxml parsing failed ({type(e).__name__}), falling back to regex")

        # Regex fallback: remove all HTML tags
        result = _RE_HTML_TAG.sub('', text)
        return result if result else ""

    @staticmethod
    def decode_html_entities(text):
        """
        Decode HTML entities (&quot; → ", &amp; → &, etc.).

        Args:
            text: Text potentially containing HTML entities

        Returns:
            str: Text with HTML entities decoded
        """
        if not text:
            return ""

        return html.unescape(text)

    @staticmethod
    def normalize_whitespace(text):
        """
        Normalize whitespace: collapse multiple spaces/tabs/newlines to single space.

        Args:
            text: Text with potential whitespace issues

        Returns:
            str: Text with normalized whitespace
        """
        if not text:
            return ""

        # Collapse all whitespace sequences to single space
        text = _RE_WHITESPACE.sub(' ', text)
        # Strip leading/trailing whitespace
        return text.strip()

    @staticmethod
    def remove_urls(text):
        """
        Remove HTTP/HTTPS/WWW URLs from text.

        Args:
            text: Text potentially containing URLs

        Returns:
            str: Text with URLs removed
        """
        if not text:
            return ""

        # Remove http/https URLs
        text = _RE_HTTP_URL.sub('', text)
        # Remove www URLs (without protocol)
        text = _RE_WWW_URL.sub('', text)

        return text

    @staticmethod
    def remove_ticker_references(text):
        """
        Remove stock ticker references in format (EXCHANGE:SYMBOL).

        Matches patterns like:
        - (NASDAQ:FEED)
        - (NYSE:AAPL)
        - (AMEX:XYZ)
        - (LSE:ABC)

        Args:
            text: Text potentially containing ticker references

        Returns:
            str: Text with ticker references removed
        """
        if not text:
            return ""

        # Remove ticker references: (EXCHANGE:SYMBOL)
        # Pattern: opening paren, uppercase letters, colon, uppercase letters/numbers, closing paren
        text = _RE_TICKER_PARENS.sub('', text)

        return text

    @staticmethod
    def clean(text):
        """
        Apply full cleaning pipeline: HTML strip → entity decode → whitespace norm → URL removal → ticker removal → final whitespace norm.

        Args:
            text: Raw text to clean (can be None)

        Returns:
            str: Cleaned text
        """
        if text is None:
            text = ""

        # Step 1: Strip HTML tags
        text = TextCleaner.strip_html_tags(text)

        # Step 2: Decode HTML entities
        text = TextCleaner.decode_html_entities(text)

        # Step 3: Normalize whitespace
        text = TextCleaner.normalize_whitespace(text)

        # Step 4: Remove URLs
        text = TextCleaner.remove_urls(text)

        # Step 5: Remove ticker references
        text = TextCleaner.remove_ticker_references(text)

        # Step 6: Final whitespace normalization (cleanup after removals)
        text = TextCleaner.normalize_whitespace(text)

        return text


class FileWriter:
    """Atomic file writing with fsync."""

    @staticmethod
    def atomic_write(filepath, content):
        """
        Write content to file atomically using temp file + os.replace().

        Args:
            filepath: Target file path
            content: Content to write (UTF-8 encoded)

        Raises:
            IOError: If write or atomic rename fails
        """
        filepath = Path(filepath)

        try:
            temp_path = filepath.parent / f"{filepath.name}.tmp"

            # Write to temp file
            with open(temp_path, 'w', encoding='utf-8') as f:
                f.write(content)
                f.flush()
                # Force write to disk
                os.fsync(f.fileno())

            # Atomic rename
            os.replace(temp_path, filepath)

        except Exception as e:
            logger.error(f"Error writing file {filepath}: {e}")
            # Clean up temp file if it exists
            try:
                temp_path.unlink()
            except:
                pass
            raise


def process_field(field_name, text, output_path):
    """
    Worker function for ProcessPoolExecutor: clean text and write to file.

    Args:
        field_name: Name of the field (for logging)
        text: Raw text to clean
        output_path: Path to write cleaned output

    Returns:
        tuple: (field_name, original_length, cleaned_length)
    """
    try:
        original_length = len(text or "")
        cleaned_text = TextCleaner.clean(text or "")
        cleaned_length = len(cleaned_text)

        FileWriter.atomic_write(output_path, cleaned_text)

        return field_name, original_length, cleaned_length

    except Exception as e:
        logger.error(f"Error processing {field_name}: {e}")
        raise


class ParallelProcessor:
    """Orchestrate parallel processing of text fields."""

    def __init__(self, max_workers=3, timeout=300):
        """
        Initialize parallel processor.

        Args:
            max_workers: Number of worker threads (default: 3)
            timeout: Timeout per worker in seconds (default: 300)
        """
        self.max_workers = max_workers
        self.timeout = timeout

    def process_fields(self, fields_dict):
        """
        Process multiple fields in parallel.

        Args:
            fields_dict: Dict of {field_name: (text, output_path)}

        Returns:
            dict: Results {field_name: (orig_len, cleaned_len)} for successful fields
        """
        results = {}

        try:
            with ProcessPoolExecutor(max_workers=self.max_workers) as executor:
                futures = {
                    executor.submit(process_field, name, text, path): name
                    for name, (text, path) in fields_dict.items()
                }

                # Process results as they complete
                for future in as_completed(futures, timeout=self.timeout):
                    field_name = futures[future]
                    try:
                        name, orig_len, clean_len = future.result()
                        results[name] = (orig_len, clean_len)
                        logger.info(
                            f"Completed {name}: {orig_len} → {clean_len} chars "
                            f"({100 * (1 - clean_len/max(orig_len, 1)):.1f}% reduction)"
                        )
                    except Exception as e:
                        logger.error(f"Worker error for {field_name}: {e}")
                        # Continue processing other fields

        except TimeoutError:
            logger.error(f"Processing timeout exceeded ({self.timeout}s)")

        return results


class JSONCleaner:
    """Main orchestrator for JSON cleaning workflow."""

    def __init__(self, input_path, log_dir="./logs", parts_to_process=3, output_path=None):
        """
        Initialize JSON cleaner.

        Args:
            input_path: Path to input JSON file
            log_dir: Directory for log files
            parts_to_process: Which parts to process (1=headline, 2=headline+summary, 3=all)
            output_path: Optional custom path to output JSON file
        """
        global logger

        self.input_path = Path(input_path)
        self.parts_to_process = parts_to_process
        self.output_path = Path(output_path) if output_path else None

        # Setup logging with the specified log_dir if not already initialized
        if logger is None or not logger.handlers:
            logger = setup_logging(log_dir)

        # Setup signal handlers
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

    def load_json(self):
        """
        Load and extract fields from JSON file.

        Returns:
            tuple: (headline, summary, content) - None values become empty strings
        """
        try:
            with open(self.input_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            headline = data.get('headline') or ""
            summary = data.get('summary') or ""
            content = data.get('content') or ""

            logger.debug(
                f"Loaded JSON. Headline: {len(headline)} chars, "
                f"Summary: {len(summary)} chars, Content: {len(content)} chars"
            )

            return headline, summary, content

        except Exception as e:
            logger.error(f"Error loading JSON: {e}")
            raise

    def generate_output_path(self):
        """
        Generate output file path based on custom path or auto-generated name.

        Returns:
            Path: Output JSON file path

        Example:
            Custom: --output /path/to/custom.json → /path/to/custom.json
            Auto: FEED_28-jan-2026.json → FEED_28-jan-2026_cleaned.json
        """
        if self.output_path:
            # Use custom output path
            logger.debug(f"Using custom output path: {self.output_path}")

            # Validate output path is not a directory
            if self.output_path.exists() and self.output_path.is_dir():
                raise ValueError(f"Output path is a directory, not a file: {self.output_path}")

            # Validate parent directory exists
            if not self.output_path.parent.exists():
                raise ValueError(f"Output directory does not exist: {self.output_path.parent}")

            return self.output_path
        else:
            # Auto-generate output path
            base_name = self.input_path.stem  # Remove .json extension
            output_dir = self.input_path.parent
            output_path = output_dir / f"{base_name}_cleaned.json"
            logger.debug(f"Auto-generated output path: {output_path.name}")
            return output_path

    def run(self):
        """
        Execute the full cleaning workflow.

        Steps:
        1. Load JSON and extract fields
        2. Determine which fields to process based on parts_to_process
        3. Clean requested fields
        4. Write cleaned fields to single JSON output file
        5. Log statistics and summary
        """
        logger.info(f"Starting JSON cleaning workflow: {self.input_path.name}")
        logger.info(f"Parts to process: {self.parts_to_process} (1=headline, 2=headline+summary, 3=all)")

        try:
            # Load JSON
            headline, summary, content = self.load_json()

            # Build cleaned JSON object based on parts_to_process
            cleaned_json = {}
            total_orig = 0
            total_clean = 0

            if self.parts_to_process >= 1:
                orig_len = len(headline or "")
                cleaned_text = TextCleaner.clean(headline)
                cleaned_json['headline'] = cleaned_text
                clean_len = len(cleaned_text)
                total_orig += orig_len
                total_clean += clean_len
                logger.info(
                    f"Completed headline: {orig_len} → {clean_len} chars "
                    f"({100 * (1 - clean_len/max(orig_len, 1)):.1f}% reduction)"
                )

            if self.parts_to_process >= 2:
                orig_len = len(summary or "")
                cleaned_text = TextCleaner.clean(summary)
                cleaned_json['summary'] = cleaned_text
                clean_len = len(cleaned_text)
                total_orig += orig_len
                total_clean += clean_len
                logger.info(
                    f"Completed summary: {orig_len} → {clean_len} chars "
                    f"({100 * (1 - clean_len/max(orig_len, 1)):.1f}% reduction)"
                )

            if self.parts_to_process >= 3:
                orig_len = len(content or "")
                cleaned_text = TextCleaner.clean(content)
                cleaned_json['content'] = cleaned_text
                clean_len = len(cleaned_text)
                total_orig += orig_len
                total_clean += clean_len
                logger.info(
                    f"Completed content: {orig_len} → {clean_len} chars "
                    f"({100 * (1 - clean_len/max(orig_len, 1)):.1f}% reduction)"
                )

            # Write cleaned JSON to output file
            output_path = self.generate_output_path()
            json_output = json.dumps(cleaned_json, ensure_ascii=False, indent=2)
            FileWriter.atomic_write(str(output_path), json_output)

            # Summary statistics
            if cleaned_json:
                reduction = 100 * (1 - total_clean / max(total_orig, 1))
                logger.info(f"Processing complete. Total: {total_orig} → {total_clean} chars ({reduction:.1f}% reduction)")
                logger.info(f"Output file created: {output_path.name}")
            else:
                logger.warning("No fields were processed")

        except KeyboardInterrupt:
            logger.warning("Interrupted by user")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            sys.exit(1)


def main():
    """Main entry point."""
    global logger

    try:
        # Setup temporary logging for argument validation
        logger = setup_logging("./logs")

        # Parse arguments (uses logger.info for validation messages)
        args = ArgumentParser.parse_args()

        # Reconfigure logging with user-specified log directory if different
        if args.log_dir != "./logs":
            logger = setup_logging(args.log_dir)

        # Create cleaner and run
        cleaner = JSONCleaner(args.input, args.log_dir, args.parts_to_process, args.output)
        cleaner.run()

        logger.info("JSON cleaning completed successfully")

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


def clean_to_dict(input_path: str, parts_to_process: int = 3) -> dict:
    """
    Library entry point. Loads and cleans a JSON news file, returning
    a plain Python dict — no file is written.

    Args:
        input_path:        Path to the Alpaca/Benzinga JSON file.
        parts_to_process:  1 = headline only
                           2 = headline + summary
                           3 = headline + summary + content (default)

    Returns:
        dict with cleaned text fields, e.g.:
        {"headline": "...", "summary": "...", "content": "..."}

    Raises:
        FileNotFoundError: if input_path does not exist.
        ValueError:        if JSON is invalid or missing required fields.
    """
    import json
    from pathlib import Path

    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)       # raises json.JSONDecodeError on bad JSON

    required = {"headline", "summary", "content"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"Missing required fields: {missing}")

    result = {}

    if parts_to_process >= 1:
        result["headline"] = TextCleaner.clean(data.get("headline"))

    if parts_to_process >= 2:
        result["summary"] = TextCleaner.clean(data.get("summary"))

    if parts_to_process >= 3:
        result["content"] = TextCleaner.clean(data.get("content"))

    return result


if __name__ == '__main__':
    main()
