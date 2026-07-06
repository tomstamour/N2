#!/usr/bin/env python3
"""Low-latency RTPR WebSocket connector.

The hot path is intentionally small:
WebSocket alert -> alert queue -> async HTTP worker -> result queue ->
single dataframe writer.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import json
import logging
import logging.handlers
import os
import queue
import re
import signal
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlsplit, urlunsplit

try:
    import aiohttp
    import pandas as pd
    import websockets
except ImportError as exc:  # pragma: no cover - depends on runtime env
    missing = exc.name or str(exc)
    raise SystemExit(
        "Missing dependency: "
        f"{missing}. Install with: pip install aiohttp websockets pandas"
    ) from exc


FIXED_TZ = timezone(timedelta(hours=-4), name="UTC-4")
PR_COLUMNS = [
    "Symbol",
    "Tickers",
    "ID",
    "ArrivalDate",
    "Created",
    "ArrivalTime",
    "CurlTime",
    "Headline",
    "Body",
]

# The requested in-memory dataframe. Only dataframe_writer mutates it.
PR_dataframe = pd.DataFrame(columns=PR_COLUMNS)


@dataclass(frozen=True)
class Config:
    api_key_file: Path
    universe_dir: Path
    log_dir: Path
    table_dir: Path
    workers: int
    max_concurrent_fetches: int
    fetch_timeout: float
    writer_batch_size: int
    writer_idle_flush_seconds: float
    flush_hour: int
    flush_minute: int
    universe_reload_hour: int
    universe_reload_minute: int
    universe_reload_second: int
    flush_on_shutdown: bool
    log_heartbeats: bool
    console_log: bool
    ws_url_template: str
    self_test: bool


@dataclass(frozen=True)
class AlertEnvelope:
    payload: dict[str, Any]
    arrival_dt: datetime


@dataclass(frozen=True)
class ArticleData:
    article_id: str
    headline: str
    body: str
    tickers: list[str]


@dataclass
class HTTPTraceTimings:
    request_start_perf: float | None = None
    queued_start_perf: float | None = None
    dns_start_perf: float | None = None
    connect_start_perf: float | None = None
    request_headers_sent_perf: float | None = None
    response_headers_perf: float | None = None
    queued_ms: int = 0
    dns_ms: int = 0
    connect_ms: int = 0
    reused_connection: bool = False
    dns_cache: str = "-"


@dataclass
class UniverseCache:
    symbols: set[str]
    path: Path
    loaded_at: datetime

    def contains(self, symbol: str) -> bool:
        return symbol in self.symbols

    def intersects(self, tickers: list[str]) -> bool:
        symbols = self.symbols
        return any(ticker in symbols for ticker in tickers)

    def replace(self, path: Path, symbols: set[str], loaded_at: datetime) -> None:
        self.path = path
        self.symbols = symbols
        self.loaded_at = loaded_at


class FixedTZFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, FIXED_TZ)
        return format_log_datetime(dt)


class DailyFixedTZFileHandler(logging.Handler):
    """Write log records to RTPR_connector2_YYYY_MM_DD.log in fixed UTC-4."""

    def __init__(self, log_dir: Path, encoding: str = "utf-8") -> None:
        super().__init__()
        self.log_dir = log_dir
        self.encoding = encoding
        self.current_date_key: str | None = None
        self.stream: Any = None

    @staticmethod
    def date_key(record: logging.LogRecord) -> str:
        dt = datetime.fromtimestamp(record.created, FIXED_TZ)
        return f"{dt:%Y_%m_%d}"

    def path_for_date(self, date_key: str) -> Path:
        return self.log_dir / f"RTPR_connector2_{date_key}.log"

    def open_for_date(self, date_key: str) -> None:
        if date_key == self.current_date_key and self.stream is not None:
            return
        if self.stream is not None:
            self.stream.flush()
            self.stream.close()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.stream = self.path_for_date(date_key).open("a", encoding=self.encoding)
        self.current_date_key = date_key

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.open_for_date(self.date_key(record))
            self.stream.write(self.format(record) + "\n")
            self.flush()
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        if self.stream is not None:
            self.stream.flush()

    def close(self) -> None:
        try:
            if self.stream is not None:
                self.stream.flush()
                self.stream.close()
                self.stream = None
        finally:
            super().close()


def parse_args(argv: list[str]) -> Config:
    parser = argparse.ArgumentParser(
        description="Connect to RTPR WebSocket alerts and fetch matching PR articles."
    )
    parser.add_argument("--api-key-file", type=Path, default=Path("RTPR_API-Key.txt"))
    parser.add_argument(
        "--universe-dir",
        type=Path,
        default=Path("../universe_finder/data"),
        help=(
            "Directory containing stocks_universe_YYYY-MM-DD.tsv files. "
            "The newest matching file is loaded at startup and reloaded daily."
        ),
    )
    parser.add_argument("--log-dir", type=Path, default=Path("logs"))
    parser.add_argument("--table-dir", type=Path, default=Path("tables"))
    parser.add_argument("--workers", type=int, default=50)
    parser.add_argument("--max-concurrent-fetches", type=int, default=100)
    parser.add_argument("--fetch-timeout", type=float, default=8.0)
    parser.add_argument("--writer-batch-size", type=int, default=100)
    parser.add_argument("--writer-idle-flush-seconds", type=float, default=1.0)
    parser.add_argument("--flush-hour", type=int, default=20)
    parser.add_argument("--flush-minute", type=int, default=35)
    parser.add_argument("--universe-reload-hour", type=int, default=3)
    parser.add_argument("--universe-reload-minute", type=int, default=30)
    parser.add_argument("--universe-reload-second", type=int, default=0)
    parser.add_argument(
        "--flush-on-shutdown",
        action="store_true",
        help="Write the current dataframe when the process receives SIGINT/SIGTERM.",
    )
    parser.add_argument(
        "--no-log-heartbeats",
        action="store_true",
        help="Do not write RTPR ping/pong heartbeat events to the log.",
    )
    parser.add_argument(
        "--no-console-log",
        action="store_true",
        help="Do not mirror log lines to stdout.",
    )
    parser.add_argument(
        "--ws-url-template",
        default="wss://ws.rtpr.io/ws-alerts?apiKey={api_key}",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Parse the local RTPR_url_fetch_raw_output.txt sample and exit.",
    )
    args = parser.parse_args(argv)

    if args.workers < 1:
        parser.error("--workers must be >= 1")
    if args.max_concurrent_fetches < 1:
        parser.error("--max-concurrent-fetches must be >= 1")
    if args.fetch_timeout <= 0:
        parser.error("--fetch-timeout must be > 0")
    if not 0 <= args.flush_hour <= 23:
        parser.error("--flush-hour must be between 0 and 23")
    if not 0 <= args.flush_minute <= 59:
        parser.error("--flush-minute must be between 0 and 59")
    if not 0 <= args.universe_reload_hour <= 23:
        parser.error("--universe-reload-hour must be between 0 and 23")
    if not 0 <= args.universe_reload_minute <= 59:
        parser.error("--universe-reload-minute must be between 0 and 59")
    if not 0 <= args.universe_reload_second <= 59:
        parser.error("--universe-reload-second must be between 0 and 59")

    return Config(
        api_key_file=args.api_key_file,
        universe_dir=args.universe_dir,
        log_dir=args.log_dir,
        table_dir=args.table_dir,
        workers=args.workers,
        max_concurrent_fetches=args.max_concurrent_fetches,
        fetch_timeout=args.fetch_timeout,
        writer_batch_size=args.writer_batch_size,
        writer_idle_flush_seconds=args.writer_idle_flush_seconds,
        flush_hour=args.flush_hour,
        flush_minute=args.flush_minute,
        universe_reload_hour=args.universe_reload_hour,
        universe_reload_minute=args.universe_reload_minute,
        universe_reload_second=args.universe_reload_second,
        flush_on_shutdown=args.flush_on_shutdown,
        log_heartbeats=not args.no_log_heartbeats,
        console_log=not args.no_console_log,
        ws_url_template=args.ws_url_template,
        self_test=args.self_test,
    )


def setup_logging(
    log_dir: Path, console_log: bool = True
) -> tuple[logging.Logger, logging.handlers.QueueListener]:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_queue: queue.SimpleQueue[logging.LogRecord] = queue.SimpleQueue()
    formatter = FixedTZFormatter("%(asctime)s %(levelname)-7s %(message)s")

    file_handler = DailyFixedTZFileHandler(log_dir, encoding="utf-8")
    file_handler.setFormatter(formatter)

    handlers: list[logging.Handler] = [file_handler]
    if console_log:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        handlers.append(console_handler)

    listener = logging.handlers.QueueListener(
        log_queue, *handlers, respect_handler_level=True
    )
    listener.start()

    logger = logging.getLogger("RTPR_connector2")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(logging.handlers.QueueHandler(log_queue))
    return logger, listener


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


API_KEY_RE = re.compile(r"\brtpr_[A-Za-z0-9][A-Za-z0-9_-]*\b")


def load_api_key(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    stripped = content.strip()
    if not stripped:
        raise ValueError(f"API key file is empty: {path}")

    match = API_KEY_RE.search(content)
    if match:
        return match.group(0)

    if "\n" not in stripped and "#" not in stripped and " " not in stripped:
        return stripped

    raise ValueError(f"Could not find an RTPR API key token in: {path}")


def load_universe_symbols(path: Path) -> set[str]:
    symbols: set[str] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        if not reader.fieldnames or "Symbol" not in reader.fieldnames:
            raise ValueError(f"Universe file must contain a Symbol column: {path}")
        for row in reader:
            symbol = normalize_symbol(row.get("Symbol"))
            if symbol:
                symbols.add(symbol)
    if not symbols:
        raise ValueError(f"No symbols loaded from universe file: {path}")
    return symbols


UNIVERSE_FILE_RE = re.compile(r"^stocks_universe_\d{4}-\d{2}-\d{2}\.tsv$")


def universe_file_created_timestamp(path: Path) -> float:
    stat_result = path.stat()
    if hasattr(stat_result, "st_birthtime"):
        return float(stat_result.st_birthtime)
    return float(stat_result.st_mtime)


def find_latest_universe_file(directory: Path) -> Path:
    if not directory.is_dir():
        raise FileNotFoundError(f"Universe directory does not exist: {directory}")

    candidates = [
        path
        for path in directory.iterdir()
        if path.is_file() and UNIVERSE_FILE_RE.fullmatch(path.name)
    ]
    if not candidates:
        raise FileNotFoundError(
            "No files matching stocks_universe_YYYY-MM-DD.tsv found in "
            f"{directory}"
        )
    return max(candidates, key=lambda path: (universe_file_created_timestamp(path), path.name))


def load_latest_universe_symbols(directory: Path) -> tuple[Path, set[str]]:
    latest_file = find_latest_universe_file(directory)
    return latest_file, load_universe_symbols(latest_file)


def now_fixed() -> datetime:
    return datetime.now(FIXED_TZ)


def to_fixed_tz(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(FIXED_TZ)


def parse_published_at(value: Any, fallback: datetime) -> datetime:
    if not value:
        return fallback
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return to_fixed_tz(datetime.fromisoformat(text))
    except ValueError:
        return fallback


def format_millis_time(dt: datetime) -> str:
    fixed = to_fixed_tz(dt)
    return f"{fixed:%H:%M:%S}.{fixed.microsecond // 1000:03d}"


def format_millis_date(dt: datetime) -> str:
    return f"{to_fixed_tz(dt):%Y-%m-%d}"


def format_log_datetime(dt: datetime) -> str:
    fixed = to_fixed_tz(dt)
    return f"{fixed:%Y-%m-%d %H:%M:%S}.{fixed.microsecond // 1000:03d}"


def elapsed_ms(start: datetime, end: datetime) -> int:
    return int(max(0.0, (end - start).total_seconds()) * 1000)


def perf_elapsed_ms(start: float | None, end: float | None = None) -> int:
    if start is None:
        return 0
    if end is None:
        end = time.perf_counter()
    return int(max(0.0, end - start) * 1000)


def tickers_json(tickers: list[str]) -> str:
    return json.dumps(tickers, ensure_ascii=True, separators=(",", ":"))


def clean_plain_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_rtpr_suffix(title: str) -> str:
    return re.sub(r"\s+(?:\u2014|-)\s+RTPR\s*$", "", title).strip()


BLOCK_TAGS = {
    "address",
    "article",
    "blockquote",
    "div",
    "footer",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "p",
    "section",
    "table",
    "tr",
}


class RTPRHTMLExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_title = False
        self.article_depth = 0
        self.ticker_depth = 0
        self.title_parts: list[str] = []
        self.body_parts: list[str] = []
        self.current_ticker_parts: list[str] = []
        self.tickers: list[str] = []
        self._seen_tickers: set[str] = set()

    @staticmethod
    def _classes(attrs: list[tuple[str, str | None]]) -> set[str]:
        for key, value in attrs:
            if key.lower() == "class" and value:
                return set(value.split())
        return set()

    def _add_body_break(self) -> None:
        if self.body_parts and self.body_parts[-1] != "\n":
            self.body_parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        classes = self._classes(attrs)

        if tag == "title":
            self.in_title = True

        starts_article_body = "article-body" in classes and self.article_depth == 0
        if self.article_depth > 0 and tag in BLOCK_TAGS:
            self._add_body_break()
        if starts_article_body:
            self.article_depth = 1
        elif self.article_depth > 0:
            self.article_depth += 1
        if self.article_depth > 0 and tag == "br":
            self._add_body_break()

        starts_ticker = "ticker" in classes and self.ticker_depth == 0
        if starts_ticker:
            self.ticker_depth = 1
            self.current_ticker_parts = []
        elif self.ticker_depth > 0:
            self.ticker_depth += 1

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self.in_title = False

        if self.article_depth > 0:
            if tag in BLOCK_TAGS:
                self._add_body_break()
            self.article_depth -= 1

        if self.ticker_depth > 0:
            self.ticker_depth -= 1
            if self.ticker_depth == 0:
                symbol = normalize_symbol(clean_inline_text("".join(self.current_ticker_parts)))
                if (
                    symbol
                    and re.fullmatch(r"[A-Z0-9./-]{1,20}", symbol)
                    and symbol not in self._seen_tickers
                ):
                    self._seen_tickers.add(symbol)
                    self.tickers.append(symbol)
                self.current_ticker_parts = []

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
        if self.article_depth > 0:
            self.body_parts.append(data)
        if self.ticker_depth > 0:
            self.current_ticker_parts.append(data)


def html_to_plain_text(html: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return clean_plain_text(unescape(text))


def extract_article_id(html: str, article_url: str) -> str:
    match = re.search(
        r"Article ID:\s*(?:<[^>]+>\s*)*([A-Za-z0-9_-]+)", html, re.IGNORECASE
    )
    if match:
        return match.group(1)

    plain = html_to_plain_text(html)
    match = re.search(r"Article ID:\s*([A-Za-z0-9_-]+)", plain, re.IGNORECASE)
    if match:
        return match.group(1)

    path_id = urlsplit(article_url).path.rstrip("/").split("/")[-1]
    return path_id or ""


def parse_article_html(html: str, article_url: str) -> ArticleData:
    extractor = RTPRHTMLExtractor()
    extractor.feed(html)
    extractor.close()

    headline = strip_rtpr_suffix(clean_inline_text("".join(extractor.title_parts)))
    body = clean_plain_text("".join(extractor.body_parts))
    if not body:
        body = html_to_plain_text(html)

    return ArticleData(
        article_id=extract_article_id(html, article_url),
        headline=headline,
        body=body,
        tickers=extractor.tickers,
    )


def redact_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.query:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "...", parts.fragment))
    return url


def redact_api_key_text(value: str, api_key: str = "") -> str:
    text = str(value)
    if api_key:
        text = text.replace(api_key, "[REDACTED_API_KEY]")
    text = API_KEY_RE.sub("[REDACTED_API_KEY]", text)
    text = re.sub(r"(apiKey=)\S+", r"\1...", text)
    return " ".join(text.split())


def get_http_trace(trace_config_ctx: Any) -> HTTPTraceTimings | None:
    trace = getattr(trace_config_ctx, "trace_request_ctx", None)
    if isinstance(trace, HTTPTraceTimings):
        return trace
    return None


async def on_http_request_start(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.request_start_perf = time.perf_counter()


async def on_http_connection_queued_start(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.queued_start_perf = time.perf_counter()


async def on_http_connection_queued_end(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.queued_ms += perf_elapsed_ms(trace.queued_start_perf)
        trace.queued_start_perf = None


async def on_http_dns_cache_hit(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.dns_cache = "hit"


async def on_http_dns_cache_miss(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.dns_cache = "miss"


async def on_http_dns_resolve_start(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.dns_start_perf = time.perf_counter()


async def on_http_dns_resolve_end(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.dns_ms += perf_elapsed_ms(trace.dns_start_perf)
        trace.dns_start_perf = None


async def on_http_connection_create_start(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.connect_start_perf = time.perf_counter()


async def on_http_connection_create_end(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.connect_ms += perf_elapsed_ms(trace.connect_start_perf)
        trace.connect_start_perf = None


async def on_http_connection_reuse(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.reused_connection = True


async def on_http_request_headers_sent(
    session: aiohttp.ClientSession, trace_config_ctx: Any, params: Any
) -> None:
    trace = get_http_trace(trace_config_ctx)
    if trace is not None:
        trace.request_headers_sent_perf = time.perf_counter()


def build_http_trace_config() -> aiohttp.TraceConfig:
    trace_config = aiohttp.TraceConfig()
    trace_config.on_request_start.append(on_http_request_start)
    trace_config.on_connection_queued_start.append(on_http_connection_queued_start)
    trace_config.on_connection_queued_end.append(on_http_connection_queued_end)
    trace_config.on_dns_cache_hit.append(on_http_dns_cache_hit)
    trace_config.on_dns_cache_miss.append(on_http_dns_cache_miss)
    trace_config.on_dns_resolvehost_start.append(on_http_dns_resolve_start)
    trace_config.on_dns_resolvehost_end.append(on_http_dns_resolve_end)
    trace_config.on_connection_create_start.append(on_http_connection_create_start)
    trace_config.on_connection_create_end.append(on_http_connection_create_end)
    trace_config.on_connection_reuseconn.append(on_http_connection_reuse)
    trace_config.on_request_headers_sent.append(on_http_request_headers_sent)
    return trace_config


def trace_request_sent_ms(trace: HTTPTraceTimings) -> int:
    return perf_elapsed_ms(trace.request_start_perf, trace.request_headers_sent_perf)


def trace_ttfb_ms(trace: HTTPTraceTimings, fallback_headers_ms: int) -> int:
    if (
        trace.request_headers_sent_perf is not None
        and trace.response_headers_perf is not None
    ):
        return perf_elapsed_ms(
            trace.request_headers_sent_perf, trace.response_headers_perf
        )
    return max(0, fallback_headers_ms - trace.queued_ms - trace.connect_ms)


def build_row(
    envelope: AlertEnvelope,
    article: ArticleData,
    curl_dt: datetime,
) -> dict[str, str]:
    payload = envelope.payload
    published_dt = parse_published_at(payload.get("article_published_at"), envelope.arrival_dt)
    symbol = normalize_symbol(payload.get("ticker"))

    return {
        "Symbol": symbol,
        "Tickers": tickers_json(article.tickers),
        "ID": article.article_id,
        "ArrivalDate": format_millis_date(published_dt),
        "Created": format_millis_time(published_dt),
        "ArrivalTime": format_millis_time(envelope.arrival_dt),
        "CurlTime": format_millis_time(curl_dt),
        "Headline": article.headline,
        "Body": article.body,
    }


def next_daily_time(now: datetime, hour: int, minute: int, second: int = 0) -> datetime:
    fixed_now = to_fixed_tz(now)
    target = datetime.combine(
        fixed_now.date(),
        dt_time(hour=hour, minute=minute, second=second),
        tzinfo=FIXED_TZ,
    )
    if fixed_now >= target:
        target += timedelta(days=1)
    return target


def next_flush_time(now: datetime, hour: int, minute: int) -> datetime:
    return next_daily_time(now, hour, minute, 0)


def write_dataframe_sync(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    df.to_csv(tmp_path, sep="\t", index=False)
    os.replace(tmp_path, path)


async def write_dataframe_snapshot(
    df: pd.DataFrame,
    path: Path,
    logger: logging.Logger,
    reason: str,
) -> None:
    rows = len(df)
    if rows == 0:
        logger.info("DF_FLUSH_SKIP reason=%s rows=0 path=%s", reason, path)
        return
    started = time.perf_counter()
    try:
        await asyncio.to_thread(write_dataframe_sync, df, path)
    except Exception:
        logger.exception("DF_FLUSH_FAIL reason=%s rows=%d path=%s", reason, rows, path)
        return
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "DF_FLUSH_OK reason=%s rows=%d elapsed=%dms path=%s",
        reason,
        rows,
        elapsed_ms,
        path,
    )


async def universe_reloader(
    universe_cache: UniverseCache,
    config: Config,
    logger: logging.Logger,
) -> None:
    next_reload = next_daily_time(
        now_fixed(),
        config.universe_reload_hour,
        config.universe_reload_minute,
        config.universe_reload_second,
    )
    logger.info("UNIVERSE_RELOAD_SCHEDULE next_reload=%s", format_log_datetime(next_reload))

    while True:
        try:
            await asyncio.sleep(max(0.0, (next_reload - now_fixed()).total_seconds()))
            previous_path = universe_cache.path
            previous_count = len(universe_cache.symbols)
            latest_file, symbols = await asyncio.to_thread(
                load_latest_universe_symbols, config.universe_dir
            )
            universe_cache.replace(latest_file, symbols, now_fixed())
            logger.info(
                "UNIVERSE_RELOAD_OK file=%s symbols=%d previous_file=%s previous_symbols=%d",
                latest_file,
                len(symbols),
                previous_path,
                previous_count,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("UNIVERSE_RELOAD_FAIL")
        finally:
            next_reload = next_daily_time(
                now_fixed() + timedelta(seconds=1),
                config.universe_reload_hour,
                config.universe_reload_minute,
                config.universe_reload_second,
            )
            logger.info(
                "UNIVERSE_RELOAD_SCHEDULE next_reload=%s",
                format_log_datetime(next_reload),
            )


async def dataframe_writer(
    result_queue: asyncio.Queue[dict[str, str] | None],
    config: Config,
    logger: logging.Logger,
) -> None:
    global PR_dataframe

    buffer: list[dict[str, str]] = []
    pending_writes: set[asyncio.Task[None]] = set()
    scheduled_flush = next_flush_time(now_fixed(), config.flush_hour, config.flush_minute)
    logger.info("DF_WRITER_START next_flush=%s", format_log_datetime(scheduled_flush))

    def append_buffer() -> None:
        nonlocal buffer
        global PR_dataframe
        if not buffer:
            return
        batch_df = pd.DataFrame.from_records(buffer, columns=PR_COLUMNS)
        PR_dataframe = pd.concat([PR_dataframe, batch_df], ignore_index=True)
        buffer = []

    def schedule_write(snapshot: pd.DataFrame, reason: str, stamp: datetime) -> None:
        path = config.table_dir / f"PR_dataframe_{format_millis_date(stamp)}.csv"
        task = asyncio.create_task(write_dataframe_snapshot(snapshot, path, logger, reason))
        pending_writes.add(task)
        task.add_done_callback(pending_writes.discard)

    try:
        while True:
            timeout = min(
                config.writer_idle_flush_seconds,
                max(0.0, (scheduled_flush - now_fixed()).total_seconds()),
            )
            try:
                item = await asyncio.wait_for(result_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                item = None
                timed_out = True
            else:
                timed_out = False

            if not timed_out:
                if item is None:
                    result_queue.task_done()
                    break
                buffer.append(item)
                result_queue.task_done()
                if len(buffer) >= config.writer_batch_size:
                    append_buffer()

            if timed_out:
                append_buffer()

            if now_fixed() >= scheduled_flush:
                append_buffer()
                snapshot = PR_dataframe
                PR_dataframe = pd.DataFrame(columns=PR_COLUMNS)
                schedule_write(snapshot, "scheduled", scheduled_flush)
                scheduled_flush = next_flush_time(
                    scheduled_flush + timedelta(seconds=1),
                    config.flush_hour,
                    config.flush_minute,
                )
                logger.info("DF_WRITER_NEXT_FLUSH %s", format_log_datetime(scheduled_flush))
    finally:
        append_buffer()
        if config.flush_on_shutdown and len(PR_dataframe) > 0:
            snapshot = PR_dataframe
            PR_dataframe = pd.DataFrame(columns=PR_COLUMNS)
            schedule_write(snapshot, "shutdown", now_fixed())
        if pending_writes:
            await asyncio.gather(*pending_writes, return_exceptions=True)
        logger.info("DF_WRITER_STOP rows_in_memory=%d", len(PR_dataframe))


async def websocket_listener(
    alert_queue: asyncio.Queue[AlertEnvelope | None],
    api_key: str,
    universe_cache: UniverseCache,
    config: Config,
    logger: logging.Logger,
) -> None:
    ws_url = config.ws_url_template.format(api_key=api_key)
    backoff = 2.0
    consecutive_failures = 0

    while True:
        try:
            logger.info("WS_CONNECT")
            async with websockets.connect(ws_url, ping_interval=None, max_queue=None) as ws:
                backoff = 2.0
                consecutive_failures = 0
                logger.info("WS_CONNECTED")

                async for raw in ws:
                    arrival_dt = now_fixed()
                    try:
                        msg = json.loads(raw)
                    except json.JSONDecodeError:
                        logger.warning("WS_BAD_JSON bytes=%d", len(raw))
                        continue

                    msg_type = msg.get("type")
                    if msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong"}))
                        if config.log_heartbeats:
                            logger.info("HEARTBEAT pong")
                        continue

                    if msg_type != "alert":
                        logger.info("WS_SKIP type=%s", msg_type)
                        continue

                    symbol = normalize_symbol(msg.get("ticker"))
                    if not symbol:
                        logger.info("SKIP - reason=missing_alert_ticker")
                        continue
                    if not universe_cache.contains(symbol):
                        logger.info(
                            "SKIP %s reason=alert_ticker_not_in_universe", symbol
                        )
                        continue

                    alert_queue.put_nowait(AlertEnvelope(payload=msg, arrival_dt=arrival_dt))

        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            consecutive_failures += 1
            logger.warning(
                "WS_CLOSED code=%s reason=%s failures=%d",
                getattr(exc, "code", None),
                redact_api_key_text(getattr(exc, "reason", ""), api_key),
                consecutive_failures,
            )
        except Exception as exc:
            consecutive_failures += 1
            logger.error(
                "WS_ERROR failures=%d error_type=%s error=%s",
                consecutive_failures,
                type(exc).__name__,
                redact_api_key_text(str(exc), api_key),
            )

        if consecutive_failures >= 8:
            delay = 300.0
        else:
            delay = min(backoff, 60.0)
            backoff = min(backoff * 2.0, 60.0)
        logger.warning(
            "WS_RECONNECT delay=%.1fs failures=%d", delay, consecutive_failures
        )
        await asyncio.sleep(delay)


async def article_worker(
    worker_id: int,
    alert_queue: asyncio.Queue[AlertEnvelope | None],
    result_queue: asyncio.Queue[dict[str, str] | None],
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    universe_cache: UniverseCache,
    logger: logging.Logger,
) -> None:
    while True:
        envelope = await alert_queue.get()
        try:
            if envelope is None:
                return

            payload = envelope.payload
            symbol = normalize_symbol(payload.get("ticker"))
            article_url = str(payload.get("article_url") or "").strip()
            dequeued_dt = now_fixed()
            if not article_url:
                logger.warning("FETCH_FAIL %s reason=missing_article_url", symbol or "-")
                continue

            fetch_start_dt: datetime | None = None
            headers_dt: datetime | None = None
            curl_dt: datetime | None = None
            http_trace = HTTPTraceTimings()
            try:
                async with semaphore:
                    fetch_start_dt = now_fixed()
                    logger.info(
                        "FETCH_START %-6s worker=%d queue=%dms dequeue=%dms url=%s",
                        symbol or "-",
                        worker_id,
                        elapsed_ms(envelope.arrival_dt, fetch_start_dt),
                        elapsed_ms(envelope.arrival_dt, dequeued_dt),
                        redact_url(article_url),
                    )
                    async with session.get(
                        article_url, trace_request_ctx=http_trace
                    ) as response:
                        headers_perf = time.perf_counter()
                        headers_dt = now_fixed()
                        http_trace.response_headers_perf = headers_perf
                        html = await response.text(errors="replace")
                        curl_dt = now_fixed()
                        status = response.status
                        content_type = response.headers.get("Content-Type", "")
            except Exception as exc:
                fail_dt = now_fixed()
                headers_wait_ms = elapsed_ms(
                    fetch_start_dt or fail_dt, headers_dt or fail_dt
                )
                logger.warning(
                    "FETCH_FAIL %s error=%s queue=%dms http=%dms conn_queue=%dms dns=%dms conn_setup=%dms reused=%d dns_cache=%s req_sent=%dms ttfb=%dms url=%s",
                    symbol or "-",
                    repr(exc),
                    elapsed_ms(envelope.arrival_dt, fetch_start_dt or fail_dt),
                    elapsed_ms(fetch_start_dt or fail_dt, fail_dt),
                    http_trace.queued_ms,
                    http_trace.dns_ms,
                    http_trace.connect_ms,
                    int(http_trace.reused_connection),
                    http_trace.dns_cache,
                    trace_request_sent_ms(http_trace),
                    trace_ttfb_ms(http_trace, headers_wait_ms),
                    redact_url(article_url),
                )
                continue

            headers_wait_ms = elapsed_ms(fetch_start_dt, headers_dt)
            if status != 200:
                logger.warning(
                    "FETCH_FAIL %s status=%d content_type=%s queue=%dms http=%dms conn_queue=%dms dns=%dms conn_setup=%dms reused=%d dns_cache=%s req_sent=%dms ttfb=%dms url=%s",
                    symbol or "-",
                    status,
                    content_type,
                    elapsed_ms(envelope.arrival_dt, fetch_start_dt),
                    elapsed_ms(fetch_start_dt, curl_dt),
                    http_trace.queued_ms,
                    http_trace.dns_ms,
                    http_trace.connect_ms,
                    int(http_trace.reused_connection),
                    http_trace.dns_cache,
                    trace_request_sent_ms(http_trace),
                    trace_ttfb_ms(http_trace, headers_wait_ms),
                    redact_url(article_url),
                )
                continue

            article = parse_article_html(html, article_url)
            if not article.tickers:
                logger.info(
                    "SKIP %s reason=no_html_tickers article_id=%s",
                    symbol or "-",
                    article.article_id or "-",
                )
                continue
            if not universe_cache.intersects(article.tickers):
                logger.info(
                    "SKIP %s reason=html_tickers_not_in_universe article_id=%s tickers=%s",
                    symbol or "-",
                    article.article_id or "-",
                    tickers_json(article.tickers),
                )
                continue

            row = build_row(envelope, article, curl_dt)
            await result_queue.put(row)

            published_dt = parse_published_at(
                payload.get("article_published_at"), envelope.arrival_dt
            )
            logger.info(
                "PR %-6s %-10s %dms created=%s arrival=%s curl=%s tickers=%s queue=%dms http=%dms headers=%dms body=%dms worker=%d conn_queue=%dms dns=%dms conn_setup=%dms reused=%d dns_cache=%s req_sent=%dms ttfb=%dms",
                row["Symbol"] or "-",
                row["ID"] or "-",
                elapsed_ms(envelope.arrival_dt, curl_dt),
                format_millis_time(published_dt),
                row["ArrivalTime"],
                row["CurlTime"],
                row["Tickers"],
                elapsed_ms(envelope.arrival_dt, fetch_start_dt),
                elapsed_ms(fetch_start_dt, curl_dt),
                headers_wait_ms,
                elapsed_ms(headers_dt, curl_dt),
                worker_id,
                http_trace.queued_ms,
                http_trace.dns_ms,
                http_trace.connect_ms,
                int(http_trace.reused_connection),
                http_trace.dns_cache,
                trace_request_sent_ms(http_trace),
                trace_ttfb_ms(http_trace, headers_wait_ms),
            )
        except Exception:
            logger.exception("WORKER_ERROR id=%d", worker_id)
        finally:
            alert_queue.task_done()


async def run_connector(config: Config) -> None:
    api_key = load_api_key(config.api_key_file)
    logger, log_listener = setup_logging(config.log_dir, config.console_log)

    try:
        universe_file, universe_symbols = await asyncio.to_thread(
            load_latest_universe_symbols, config.universe_dir
        )
        universe_cache = UniverseCache(
            symbols=universe_symbols,
            path=universe_file,
            loaded_at=now_fixed(),
        )

        alert_queue: asyncio.Queue[AlertEnvelope | None] = asyncio.Queue(maxsize=0)
        result_queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue(maxsize=0)
        shutdown_event = asyncio.Event()

        def request_shutdown() -> None:
            logger.info("SHUTDOWN_REQUESTED")
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, request_shutdown)

        timeout = aiohttp.ClientTimeout(total=config.fetch_timeout)
        connector = aiohttp.TCPConnector(
            limit=config.max_concurrent_fetches,
            limit_per_host=config.max_concurrent_fetches,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        headers = {
            "X-API-Key": api_key,
            "User-Agent": "RTPR_connector2/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        semaphore = asyncio.Semaphore(config.max_concurrent_fetches)

        effective_fetch_slots = min(config.workers, config.max_concurrent_fetches)
        logger.info(
            "STARTUP universe_file=%s universe_symbols=%d universe_reload=%02d:%02d:%02d workers=%d max_concurrent_fetches=%d effective_fetch_slots=%d",
            universe_cache.path,
            len(universe_cache.symbols),
            config.universe_reload_hour,
            config.universe_reload_minute,
            config.universe_reload_second,
            config.workers,
            config.max_concurrent_fetches,
            effective_fetch_slots,
        )

        trace_config = build_http_trace_config()
        async with aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers=headers,
            raise_for_status=False,
            trace_configs=[trace_config],
        ) as session:
            writer_task = asyncio.create_task(
                dataframe_writer(result_queue, config, logger), name="dataframe_writer"
            )
            reloader_task = asyncio.create_task(
                universe_reloader(universe_cache, config, logger),
                name="universe_reloader",
            )
            ws_task = asyncio.create_task(
                websocket_listener(alert_queue, api_key, universe_cache, config, logger),
                name="websocket_listener",
            )
            worker_tasks = [
                asyncio.create_task(
                    article_worker(
                        worker_id,
                        alert_queue,
                        result_queue,
                        session,
                        semaphore,
                        universe_cache,
                        logger,
                    ),
                    name=f"article_worker_{worker_id}",
                )
                for worker_id in range(config.workers)
            ]

            await shutdown_event.wait()

            reloader_task.cancel()
            ws_task.cancel()
            await asyncio.gather(reloader_task, ws_task, return_exceptions=True)

            for _ in worker_tasks:
                alert_queue.put_nowait(None)
            await asyncio.gather(*worker_tasks, return_exceptions=True)

            await result_queue.put(None)
            await writer_task
    finally:
        logger.info("STOPPED")
        log_listener.stop()

def self_test() -> int:
    sample_path = Path("RTPR_url_fetch_raw_output.txt")
    html = sample_path.read_text(encoding="utf-8")
    article = parse_article_html(
        html,
        "https://rtpr.io/a/nACS0Gsm4a?exp=1782832920&sig=sample",
    )
    sample_payload = {
        "type": "alert",
        "ticker": "HYLN",
        "article_published_at": "2026-06-30T15:20:00.730Z",
        "article_url": "https://rtpr.io/a/nACS0Gsm4a?exp=1782832920&sig=sample",
    }
    arrival_dt = datetime(2026, 6, 30, 15, 20, 1, 108000, tzinfo=timezone.utc)
    curl_dt = datetime(2026, 6, 30, 15, 20, 1, 260000, tzinfo=timezone.utc)
    row = build_row(AlertEnvelope(sample_payload, to_fixed_tz(arrival_dt)), article, curl_dt)

    required = {
        "Symbol": "HYLN",
        "ID": "nACS0Gsm4a",
        "ArrivalDate": "2026-06-30",
        "Created": "11:20:00.730",
        "ArrivalTime": "11:20:01.108",
        "CurlTime": "11:20:01.260",
    }
    for key, expected in required.items():
        actual = row[key]
        if actual != expected:
            print(f"SELF_TEST_FAIL {key}: expected={expected!r} actual={actual!r}")
            return 1
    if row["Tickers"] != '["HYLN"]':
        print(f"SELF_TEST_FAIL Tickers: actual={row['Tickers']!r}")
        return 1
    if not row["Headline"] or not row["Body"]:
        print("SELF_TEST_FAIL missing headline/body")
        return 1

    print("SELF_TEST_OK")
    print(json.dumps({k: row[k] for k in PR_COLUMNS if k != "Body"}, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    config = parse_args(sys.argv[1:] if argv is None else argv)
    if config.self_test:
        return self_test()
    asyncio.run(run_connector(config))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
