#!/usr/bin/env python3
"""Low-latency RTPR alerts WebSocket connector with parallel article fetch.

Unlike the firehose socket (RTPR_connector3_Firehose), the alerts socket
(wss://ws.rtpr.io/ws-alerts?apiKey=...) pushes a minimal frame per matched
article:

    {"type":"alert","ticker":"AAPL","rules":[{"rule_name":...}],
     "article_published_at":"...Z","article_url":"https://rtpr.io/a/{id}?exp&sig"}

so the article itself must be fetched over HTTPS. The fetch architecture
follows the rtpr_official recipe (doc/rtpr_WebSocket_rtpr-official-curl-advices.txt):

  1. The WS read path does zero blocking work: it stamps ArrivalTime the
     instant a frame is read, replies to ping frames inline, and hands the
     alert to a thread pool. It never fetches or parses on the read path.
  2. Fetches run in a dedicated ThreadPoolExecutor (12 workers by default),
     so a 10-15 alert burst fetches concurrently instead of serially.
  3. All fetches go through ONE long-lived requests.Session (HTTP keep-alive,
     X-API-Key header) with a connection pool sized >= the worker count, so
     burst concurrency never re-pays TLS handshakes. No curl subprocesses.
  4. article_url is requested exactly as sent (signed rtpr.io/a/{id}?exp&sig
     permalink); the fetch timer wraps only the GET.

Hot path:

    ws-alerts WS -> websocket_listener -> handle_alert task
        -> fetch pool thread (GET + parse + build row)
        -> result_queue -> dataframe_writer
        -> row hook -> FeedServer (local TCP feed for Orchestrator5.0)

Outputs mirror RTPR_connector3_Firehose.py (same feed protocol on
127.0.0.1:8900) with one additive difference: the row carries a CurlTime
column (fetch-completion timestamp, HH:MM:SS.mmm UTC-4) right after
ArrivalTime. The daily table is PR_dataframe_curl_YYYY-MM-DD.tsv and logs
are RTPR_connector3_curl_*.log. The API key comes from the shared N2 config
file (../config/n2_config_file.txt).
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
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    import requests
    import websockets
except ImportError as exc:  # pragma: no cover - depends on runtime env
    missing = exc.name or str(exc)
    raise SystemExit(
        f"Missing dependency: {missing}. Install with: pip install websockets pandas requests"
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

PONG_FRAME = json.dumps({"type": "pong"})

OUTPUT_FORMATS = {
    "tsv": (".tsv", "\t"),
    "csv-tab": (".csv", "\t"),
    "csv": (".csv", ","),
}

# Local TCP feed (JSON lines on loopback): every accepted article is fanned out
# to connected consumers (Orchestrator5.0, `nc 127.0.0.1 8900` debug watchers).
# Bounded per-client queues keep a slow consumer from ever blocking the WS loop.
FEED_HOST = "127.0.0.1"
FEED_PROTO = 1
FEED_HEARTBEAT_SECONDS = 5.0
FEED_CLIENT_QUEUE_MAX = 2000
FEED_SERVICE_NAME = "RTPR_connector3_curl-feed"

# Fetch behavior (rtpr_official recipe: pool >= workers so bursts never
# overflow the connection pool and re-pay TLS handshakes).
FETCH_CONNECT_TIMEOUT_SECONDS = 3.05
FETCH_RETRY_PAUSE_SECONDS = 0.25
SHUTDOWN_FETCH_GRACE_SECONDS = 15.0

# Pre-fetch duplicate suppression window (permalink IDs). The writer's per-day
# ID dedup stays authoritative; this only avoids re-fetching replayed alerts.
RECENT_IDS_MAX = 20000

# In-process row hook (default None -> no extra work on the hot path).
# handle_alert calls _row_hook(data, row, arrival_dt) on the WS loop thread
# right after the row is enqueued — BEFORE the writer's dedup, so WS
# reconnect replays that slip past the pre-fetch dedup flow through and
# consumers must dedup by article ID. run_connector registers
# FeedServer.broadcast here when --feed-port is enabled (the standard
# topology: this standalone connector feeding Orchestrator5.0 over local
# TCP); tests may register their own callable. The callable MUST be
# non-blocking and MUST NOT mutate `data` or `row`.
_row_hook = None


def set_row_hook(fn) -> None:
    global _row_hook
    _row_hook = fn


@dataclass(frozen=True)
class Config:
    config_file: Path
    universe_dir: Path
    log_dir: Path
    table_dir: Path
    writer_batch_size: int
    writer_idle_flush_seconds: float
    flush_hour: int
    flush_minute: int
    universe_reload_hour: int
    universe_reload_minute: int
    universe_reload_second: int
    flush_on_shutdown: bool
    log_heartbeats: bool
    log_skips: bool
    console_log: bool
    universe_filter: bool
    dedup: bool
    table_suffix: str
    table_delimiter: str
    ws_url_template: str
    feed_port: int
    fetch_workers: int
    fetch_timeout: float
    fetch_retries: int
    self_test: bool


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


class RecentIds:
    """Bounded seen-set of permalink IDs for pre-fetch duplicate suppression."""

    def __init__(self, maxlen: int = RECENT_IDS_MAX) -> None:
        self.maxlen = maxlen
        self._order: deque[str] = deque()
        self._seen: set[str] = set()

    def __contains__(self, item: str) -> bool:
        return item in self._seen

    def add(self, item: str) -> None:
        if item in self._seen:
            return
        self._seen.add(item)
        self._order.append(item)
        if len(self._order) > self.maxlen:
            self._seen.discard(self._order.popleft())


class FixedTZFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, FIXED_TZ)
        return format_log_datetime(dt)


class DailyFixedTZFileHandler(logging.Handler):
    """Write log records to RTPR_connector3_curl_YYYY_MM_DD.log in fixed UTC-4."""

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
        return self.log_dir / f"RTPR_connector3_curl_{date_key}.log"

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
        description=(
            "Connect to the RTPR alerts WebSocket, fetch each signed article "
            "permalink through a threaded HTTP pool, and cache universe PRs."
        )
    )
    parser.add_argument(
        "--config-file",
        type=Path,
        default=Path("../config/n2_config_file.txt"),
        help="N2 config file providing the RTPR_API_KEY value.",
    )
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
    parser.add_argument("--writer-batch-size", type=int, default=200)
    parser.add_argument("--writer-idle-flush-seconds", type=float, default=0.5)
    parser.add_argument("--flush-hour", type=int, default=20)
    parser.add_argument("--flush-minute", type=int, default=35)
    parser.add_argument("--universe-reload-hour", type=int, default=3)
    parser.add_argument("--universe-reload-minute", type=int, default=50)
    parser.add_argument("--universe-reload-second", type=int, default=0)
    parser.add_argument(
        "--output-format",
        choices=sorted(OUTPUT_FORMATS),
        default="tsv",
        help="Daily flush format: tsv (tab/.tsv), csv-tab (tab/.csv), csv (comma/.csv).",
    )
    parser.add_argument(
        "--no-universe-filter",
        action="store_true",
        help=(
            "Ingest every alert without checking the ticker against the "
            "stocks_universe file (no universe file is loaded at all)."
        ),
    )
    parser.add_argument(
        "--no-dedup",
        action="store_true",
        help="Keep every article row, including duplicate article IDs.",
    )
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
    skip_group = parser.add_mutually_exclusive_group()
    skip_group.add_argument(
        "--log-skips",
        action="store_true",
        help="Log every not_in_universe skip (already the default).",
    )
    skip_group.add_argument(
        "--no-log-skips",
        action="store_true",
        help="Never log not_in_universe skips.",
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
        "--feed-port",
        type=int,
        default=8900,
        help=(
            "Publish each accepted article as a JSON line on the local TCP feed "
            f"{FEED_HOST}:<port> for orchestrator consumers (0 disables the feed)."
        ),
    )
    parser.add_argument(
        "--fetch-workers",
        type=int,
        default=12,
        help="Concurrent article fetch threads (rtpr_official recipe: 12).",
    )
    parser.add_argument(
        "--fetch-timeout",
        type=float,
        default=10.0,
        help=(
            "Per-attempt HTTP read timeout in seconds "
            f"(connect timeout is fixed at {FETCH_CONNECT_TIMEOUT_SECONDS}s)."
        ),
    )
    parser.add_argument(
        "--fetch-retries",
        type=int,
        default=1,
        help="Extra attempts after a failed article fetch before giving up.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run the offline parser/row-builder self-test and exit.",
    )
    args = parser.parse_args(argv)

    if args.writer_batch_size < 1:
        parser.error("--writer-batch-size must be >= 1")
    if args.writer_idle_flush_seconds <= 0:
        parser.error("--writer-idle-flush-seconds must be > 0")
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
    if args.feed_port != 0 and not 1024 <= args.feed_port <= 65535:
        parser.error("--feed-port must be 0 (disabled) or between 1024 and 65535")
    if args.fetch_workers < 1:
        parser.error("--fetch-workers must be >= 1")
    if args.fetch_timeout <= 0:
        parser.error("--fetch-timeout must be > 0")
    if args.fetch_retries < 0:
        parser.error("--fetch-retries must be >= 0")

    suffix, delimiter = OUTPUT_FORMATS[args.output_format]

    return Config(
        config_file=args.config_file,
        universe_dir=args.universe_dir,
        log_dir=args.log_dir,
        table_dir=args.table_dir,
        writer_batch_size=args.writer_batch_size,
        writer_idle_flush_seconds=args.writer_idle_flush_seconds,
        flush_hour=args.flush_hour,
        flush_minute=args.flush_minute,
        universe_reload_hour=args.universe_reload_hour,
        universe_reload_minute=args.universe_reload_minute,
        universe_reload_second=args.universe_reload_second,
        flush_on_shutdown=args.flush_on_shutdown,
        log_heartbeats=not args.no_log_heartbeats,
        log_skips=not args.no_log_skips,
        console_log=not args.no_console_log,
        universe_filter=not args.no_universe_filter,
        dedup=not args.no_dedup,
        table_suffix=suffix,
        table_delimiter=delimiter,
        ws_url_template=args.ws_url_template,
        feed_port=args.feed_port,
        fetch_workers=args.fetch_workers,
        fetch_timeout=args.fetch_timeout,
        fetch_retries=args.fetch_retries,
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

    logger = logging.getLogger("RTPR_connector3_curl")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False
    logger.addHandler(logging.handlers.QueueHandler(log_queue))
    return logger, listener


def normalize_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


API_KEY_RE = re.compile(r"\brtpr_[A-Za-z0-9][A-Za-z0-9_-]*\b")


def parse_n2_config_text(text: str) -> dict[str, str]:
    """Parse the N2 config format: a LABEL line ending in ':' followed by its
    value on the next line. Blank lines and '#' lines are ignored; a trailing
    ' #...' inline comment on a value line is stripped."""
    values: dict[str, str] = {}
    label: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(":") and " " not in line[:-1]:
            label = line[:-1].strip()
            continue
        if label is None:
            continue
        value = line.split(" #", 1)[0].strip()
        if value:
            values[label] = value
            label = None
    return values


def load_api_key(config_file: Path) -> str:
    values = parse_n2_config_text(config_file.read_text(encoding="utf-8"))
    key = values.get("RTPR_API_KEY", "")
    if not key:
        raise ValueError(f"No RTPR_API_KEY entry found in config file: {config_file}")
    if not API_KEY_RE.fullmatch(key):
        raise ValueError(
            f"RTPR_API_KEY in {config_file} does not look like an RTPR key (rtpr_...)"
        )
    return key


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
    return max(
        candidates,
        key=lambda path: (universe_file_created_timestamp(path), path.name),
    )


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


PUBLISHED_KEYS = ("article_published_at", "created", "published_at", "created_at")


def parse_published_first(data: dict[str, Any], fallback: datetime) -> datetime:
    for key in PUBLISHED_KEYS:
        value = data.get(key)
        if value:
            return parse_published_at(value, fallback)
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


def tickers_json(tickers: list[str]) -> str:
    return json.dumps(tickers, ensure_ascii=True, separators=(",", ":"))


def normalize_tickers(value: Any, primary: str = "") -> list[str]:
    """Coerce the payload tickers (list, JSON string, or delimited string) to a
    de-duplicated list of upper-case symbols, ensuring `primary` is present."""
    result: list[str] = []
    seen: set[str] = set()

    def add(sym: Any) -> None:
        s = normalize_symbol(sym)
        if s and s not in seen:
            seen.add(s)
            result.append(s)

    if isinstance(value, (list, tuple)):
        for item in value:
            add(item)
    elif isinstance(value, str):
        text = value.strip()
        if text:
            parsed: Any = None
            if text[0] in "[{":
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
            if isinstance(parsed, list):
                for item in parsed:
                    add(item)
            else:
                for part in re.split(r"[,\s]+", text):
                    add(part)

    if primary:
        add(primary)
    return result


TAG_RE = re.compile(r"(?s)<[^>]+>")


def clean_plain_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_inline_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def strip_rtpr_suffix(title: str) -> str:
    return re.sub(r"\s+(?:—|-)\s+RTPR\s*$", "", title).strip()


def to_plain_body(value: Any) -> str:
    """Permalink fragments are HTML page content, so entities are always
    escaped even when no tags survive — unescape unconditionally (unlike the
    firehose variant, whose payload bodies may already be plain text)."""
    text = str(value or "")
    if "<" in text and ">" in text:
        text = TAG_RE.sub(" ", text)
    text = unescape(text)
    return clean_plain_text(text)


# --- signed-permalink HTML parsing (anchored to the rtpr.io/a/{id} template,
# see RTPR_url_fetch_raw_output.txt for a captured sample) ---

ARTICLE_ID_URL_RE = re.compile(r"/a/([^/?#]+)")
H1_TITLE_RE = re.compile(r'(?s)<h1 class="title">(.*?)</h1>')
TITLE_TAG_RE = re.compile(r"(?s)<title>(.*?)</title>")
TIME_DATETIME_RE = re.compile(r'<time datetime="([^"]+)"')
META_SOURCE_RE = re.compile(r'(?s)<span class="meta-source">(.*?)</span>')
TICKER_SPAN_RE = re.compile(r'<span class="ticker">([^<]*)</span>')
FOOTER_ID_RE = re.compile(r"(?s)Article ID:\s*<code>([^<]*)</code>")
BODY_DIV_OPEN_RE = re.compile(r'<div class="article-body[^"]*">')
DIV_TOKEN_RE = re.compile(r"(?i)</?div\b")
FOOTER_DIV_MARK = '<div class="footer-meta">'


def article_id_from_url(url: str) -> str:
    match = ARTICLE_ID_URL_RE.search(str(url or ""))
    return match.group(1) if match else ""


def extract_article_body_html(html: str) -> str:
    """Return the inner HTML of the article-body div via a balanced div walk
    (LSEG `is-html` bodies can nest divs, so the first </div> is not enough)."""
    open_match = BODY_DIV_OPEN_RE.search(html)
    if not open_match:
        return ""
    start = open_match.end()
    depth = 1
    for token in DIV_TOKEN_RE.finditer(html, start):
        if token.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start : token.start()]
        else:
            depth += 1
    footer = html.find(FOOTER_DIV_MARK, start)
    return html[start:footer] if footer != -1 else html[start:]


def parse_article_html(html: str) -> dict[str, Any]:
    """Extract the fields the permalink page carries. Returned title/source are
    unescaped text; body_html is the raw inner fragment (entities intact)."""
    title_match = H1_TITLE_RE.search(html)
    if title_match:
        title = unescape(title_match.group(1))
    else:
        tag_match = TITLE_TAG_RE.search(html)
        title = strip_rtpr_suffix(unescape(tag_match.group(1))) if tag_match else ""

    time_match = TIME_DATETIME_RE.search(html)
    source_match = META_SOURCE_RE.search(html)
    footer_match = FOOTER_ID_RE.search(html)
    tickers = [unescape(sym).strip() for sym in TICKER_SPAN_RE.findall(html)]

    return {
        "title": clean_inline_text(title),
        "published": time_match.group(1).strip() if time_match else "",
        "source": clean_inline_text(unescape(source_match.group(1))) if source_match else "",
        "tickers": [sym for sym in tickers if sym],
        "body_html": extract_article_body_html(html),
        "footer_id": footer_match.group(1).strip() if footer_match else "",
    }


def build_row_from_data(
    data: dict[str, Any],
    tickers: list[str],
    symbol: str,
    arrival_dt: datetime,
    curl_dt: datetime,
) -> dict[str, str]:
    published_dt = parse_published_first(data, arrival_dt)
    title = data.get("title")
    if title is None:
        title = data.get("headline")
    body = data.get("article_body")
    if body is None:
        body = data.get("body")
    return {
        "Symbol": symbol,
        "Tickers": tickers_json(tickers),
        "ID": str(data.get("id") or "").strip(),
        "ArrivalDate": format_millis_date(published_dt),
        "Created": format_millis_time(published_dt),
        "ArrivalTime": format_millis_time(arrival_dt),
        "CurlTime": format_millis_time(curl_dt),
        "Headline": strip_rtpr_suffix(clean_inline_text(str(title or ""))),
        "Body": to_plain_body(body),
    }


def build_alert_data_and_row(
    alert: dict[str, Any],
    article_url: str,
    article_id: str,
    html: str,
    arrival_dt: datetime,
    curl_dt: datetime,
    fetch_ms: int,
    attempts: int,
    http_status: int,
    logger: logging.Logger | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Synthesize a firehose-shaped `data` payload from alert + fetched page,
    then build the standard row from it. The first seven keys mirror the
    firehose article payload so feed consumers see the same shape."""
    parsed = parse_article_html(html)

    if (
        logger is not None
        and article_id
        and parsed["footer_id"]
        and parsed["footer_id"] != article_id
    ):
        logger.warning(
            "ARTICLE_ID_MISMATCH url_id=%s page_id=%s", article_id, parsed["footer_id"]
        )

    symbol = normalize_symbol(alert.get("ticker"))
    tickers = normalize_tickers(parsed["tickers"], symbol)
    rules = [
        str(rule.get("rule_name") or "")
        for rule in (alert.get("rules") or [])
        if isinstance(rule, dict)
    ]
    published = str(alert.get("article_published_at") or "").strip() or parsed["published"]

    data: dict[str, Any] = {
        "id": article_id or parsed["footer_id"],
        "ticker": symbol,
        "tickers": tickers,
        "title": parsed["title"],
        "article_body": parsed["body_html"],
        "author": parsed["source"],
        "article_published_at": published,
        "article_url": article_url,
        "rules": rules,
        "fetch_ms": fetch_ms,
        "fetch_attempts": attempts,
        "http_status": http_status,
    }
    row = build_row_from_data(data, tickers, symbol, arrival_dt, curl_dt)
    return data, row


def build_http_session(api_key: str, pool_size: int) -> requests.Session:
    """One long-lived keep-alive session for ALL article fetches (rtpr_official
    recipe): X-API-Key header, connection pool sized >= the fetch workers."""
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(
        pool_connections=pool_size, pool_maxsize=pool_size
    )
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers["X-API-Key"] = api_key
    return session


def fetch_article(
    session: requests.Session,
    article_url: str,
    article_id: str,
    symbol: str,
    config: Config,
    logger: logging.Logger,
) -> tuple[str | None, int, int, int, datetime | None]:
    """GET the signed permalink exactly as sent (runs in a fetch pool thread).

    Returns (html_or_None, fetch_ms_of_last_attempt, attempts, http_status,
    curl_dt) where curl_dt is stamped the instant the successful GET returned
    (download complete). The fetch timer wraps only the GET. On final failure
    logs FETCH_GIVEUP and returns html=None; the alert is dropped (no row)."""
    attempts_allowed = config.fetch_retries + 1
    timeout = (FETCH_CONNECT_TIMEOUT_SECONDS, config.fetch_timeout)
    fetch_ms = 0
    status = 0
    for attempt in range(1, attempts_allowed + 1):
        error: str | None = None
        status = 0
        started = time.perf_counter()
        try:
            response = session.get(article_url, timeout=timeout)
            curl_dt = now_fixed()
            fetch_ms = int((time.perf_counter() - started) * 1000)
            status = response.status_code
            if status == 200 and response.text.strip():
                return response.text, fetch_ms, attempt, status, curl_dt
            error = "empty_body" if status == 200 else f"http_{status}"
        except requests.RequestException as exc:
            fetch_ms = int((time.perf_counter() - started) * 1000)
            error = type(exc).__name__
        if attempt < attempts_allowed:
            logger.warning(
                "FETCH_RETRY %-6s id=%s attempt=%d/%d error=%s fetch=%dms",
                symbol or "-",
                article_id or "-",
                attempt,
                attempts_allowed,
                error,
                fetch_ms,
            )
            time.sleep(FETCH_RETRY_PAUSE_SECONDS)
    logger.error(
        "FETCH_GIVEUP %-6s id=%s attempts=%d error=%s fetch=%dms",
        symbol or "-",
        article_id or "-",
        attempts_allowed,
        error,
        fetch_ms,
    )
    return None, fetch_ms, attempts_allowed, status, None


def fetch_and_build(
    session: requests.Session,
    alert: dict[str, Any],
    article_url: str,
    article_id: str,
    arrival_dt: datetime,
    config: Config,
    logger: logging.Logger,
) -> tuple[dict[str, Any], dict[str, str]] | None:
    """Fetch pool thread entry point: GET + parse + build (data, row)."""
    symbol = normalize_symbol(alert.get("ticker"))
    html, fetch_ms, attempts, status, curl_dt = fetch_article(
        session, article_url, article_id, symbol, config, logger
    )
    if html is None or curl_dt is None:
        return None
    return build_alert_data_and_row(
        alert,
        article_url,
        article_id,
        html,
        arrival_dt,
        curl_dt,
        fetch_ms,
        attempts,
        status,
        logger,
    )


def redact_api_key_text(value: str, api_key: str = "") -> str:
    text = str(value)
    if api_key:
        text = text.replace(api_key, "[REDACTED_API_KEY]")
    text = API_KEY_RE.sub("[REDACTED_API_KEY]", text)
    text = re.sub(r"(apiKey=)\S+", r"\1...", text)
    return " ".join(text.split())


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


def write_dataframe_sync(df: pd.DataFrame, path: Path, delimiter: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    df.to_csv(tmp_path, sep=delimiter, index=False)
    os.replace(tmp_path, path)


async def write_dataframe_snapshot(
    df: pd.DataFrame,
    path: Path,
    delimiter: str,
    logger: logging.Logger,
    reason: str,
) -> None:
    rows = len(df)
    if rows == 0:
        logger.info("DF_FLUSH_SKIP reason=%s rows=0 path=%s", reason, path)
        return
    started = time.perf_counter()
    try:
        await asyncio.to_thread(write_dataframe_sync, df, path, delimiter)
    except Exception:
        logger.exception("DF_FLUSH_FAIL reason=%s rows=%d path=%s", reason, rows, path)
        return
    elapsed = int((time.perf_counter() - started) * 1000)
    logger.info(
        "DF_FLUSH_OK reason=%s rows=%d elapsed=%dms path=%s",
        reason,
        rows,
        elapsed,
        path,
    )


async def universe_reloader(
    universe_cache: UniverseCache,
    config: Config,
    logger: logging.Logger,
) -> None:
    """Daily universe reload (03:50 by default). Unlike the firehose connector
    there is no re-subscribe step: the alerts socket has no subscription (rules
    live server-side), so swapping the in-memory set is the whole job."""
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
    seen_ids: set[str] = set()
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
        path = (
            config.table_dir
            / f"PR_dataframe_curl_{format_millis_date(stamp)}{config.table_suffix}"
        )
        task = asyncio.create_task(
            write_dataframe_snapshot(
                snapshot, path, config.table_delimiter, logger, reason
            )
        )
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
                article_id = item.get("ID") or ""
                if config.dedup and article_id and article_id in seen_ids:
                    logger.info(
                        "SKIP %-6s reason=duplicate_id id=%s",
                        item.get("Symbol") or "-",
                        article_id,
                    )
                else:
                    if article_id:
                        seen_ids.add(article_id)
                    buffer.append(item)
                    if len(buffer) >= config.writer_batch_size:
                        append_buffer()
                result_queue.task_done()

            if timed_out:
                append_buffer()

            if now_fixed() >= scheduled_flush:
                append_buffer()
                snapshot = PR_dataframe
                PR_dataframe = pd.DataFrame(columns=PR_COLUMNS)
                schedule_write(snapshot, "scheduled", scheduled_flush)
                seen_ids.clear()
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
    result_queue: asyncio.Queue[dict[str, str] | None],
    api_key: str,
    universe_cache: UniverseCache,
    session: requests.Session,
    fetch_pool: ThreadPoolExecutor,
    inflight: set[asyncio.Task],
    config: Config,
    logger: logging.Logger,
) -> None:
    ws_url = config.ws_url_template.format(api_key=api_key)
    backoff = 2.0
    consecutive_failures = 0
    first_alert_logged = False
    recent_ids = RecentIds()

    async def handle_alert(
        alert: dict[str, Any],
        article_url: str,
        article_id: str,
        symbol: str,
        arrival_dt: datetime,
    ) -> None:
        """Awaits the fetch pool, then enqueues + broadcasts on the loop thread.
        The listener itself never blocks on this coroutine."""
        loop = asyncio.get_running_loop()
        try:
            outcome = await loop.run_in_executor(
                fetch_pool,
                fetch_and_build,
                session,
                alert,
                article_url,
                article_id,
                arrival_dt,
                config,
                logger,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("FETCH_TASK_ERROR id=%s", article_id or "-")
            return
        if outcome is None:
            return  # fetch gave up; FETCH_GIVEUP already logged
        data, row = outcome

        if not data["tickers"]:
            logger.info("SKIP reason=missing_ticker id=%s", row["ID"] or "-")
            return
        # Alerts should always carry a ticker; if one didn't, fall back to
        # filtering on the page's full ticker list after the fetch.
        if (
            config.universe_filter
            and not symbol
            and not universe_cache.intersects(data["tickers"])
        ):
            if config.log_skips:
                logger.info(
                    "SKIP %-6s reason=not_in_universe id=%s tickers=%s",
                    "-",
                    row["ID"] or "-",
                    row["Tickers"],
                )
            return

        result_queue.put_nowait(row)
        if _row_hook is not None:
            try:
                _row_hook(data, row, arrival_dt)
            except Exception:
                logger.exception("ROW_HOOK_ERROR id=%s", row["ID"] or "-")
        logger.info(
            "PR %-6s %-10s fetch=%dms attempts=%d lat=%dms created=%s arrival=%s tickers=%s",
            row["Symbol"] or "-",
            row["ID"] or "-",
            data["fetch_ms"],
            data["fetch_attempts"],
            elapsed_ms(arrival_dt, now_fixed()),
            row["Created"],
            row["ArrivalTime"],
            row["Tickers"],
        )

    while True:
        try:
            logger.info("WS_CONNECT")
            async with websockets.connect(
                ws_url, ping_interval=None, max_queue=None
            ) as ws:
                backoff = 2.0
                consecutive_failures = 0
                logger.info("WS_CONNECTED")

                async for raw in ws:
                    arrival_dt = now_fixed()
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "WS_BAD_JSON bytes=%d", len(raw) if raw is not None else 0
                        )
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "alert":
                        symbol = normalize_symbol(msg.get("ticker"))
                        article_url = str(msg.get("article_url") or "").strip()
                        article_id = article_id_from_url(article_url)
                        rules = [
                            str(rule.get("rule_name") or "")
                            for rule in (msg.get("rules") or [])
                            if isinstance(rule, dict)
                        ]
                        if not first_alert_logged:
                            first_alert_logged = True
                            logger.info(
                                "FIRST_ALERT_KEYS keys=%s", json.dumps(sorted(msg.keys()))
                            )
                        logger.info(
                            "ALERT %-6s %-10s rules=%s",
                            symbol or "-",
                            article_id or "-",
                            json.dumps(rules, ensure_ascii=True),
                        )
                        if not article_url:
                            logger.info(
                                "SKIP %-6s reason=missing_url", symbol or "-"
                            )
                            continue
                        if (
                            config.universe_filter
                            and symbol
                            and not universe_cache.contains(symbol)
                        ):
                            if config.log_skips:
                                logger.info(
                                    "SKIP %-6s reason=not_in_universe id=%s",
                                    symbol,
                                    article_id or "-",
                                )
                            continue
                        if config.dedup and article_id:
                            if article_id in recent_ids:
                                logger.info(
                                    "SKIP %-6s reason=duplicate_id id=%s",
                                    symbol or "-",
                                    article_id,
                                )
                                continue
                            recent_ids.add(article_id)

                        task = asyncio.create_task(
                            handle_alert(msg, article_url, article_id, symbol, arrival_dt),
                            name=f"fetch_{article_id or 'unknown'}",
                        )
                        inflight.add(task)
                        task.add_done_callback(inflight.discard)
                        continue

                    if msg_type == "ping":
                        await ws.send(PONG_FRAME)
                        if config.log_heartbeats:
                            logger.info("HEARTBEAT pong")
                        continue

                    if msg_type == "connected":
                        logger.info("WS_HELLO connected")
                        continue

                    logger.info("WS_SKIP type=%s", msg_type)

        except asyncio.CancelledError:
            raise
        except websockets.ConnectionClosed as exc:
            consecutive_failures += 1
            logger.warning(
                "WS_CLOSED code=%s reason=%s failures=%d",
                getattr(exc, "code", None),
                redact_api_key_text(getattr(exc, "reason", "") or "", api_key),
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
        logger.warning("WS_RECONNECT delay=%.1fs failures=%d", delay, consecutive_failures)
        await asyncio.sleep(delay)


class FeedServer:
    """Loopback TCP fan-out of accepted articles as JSON lines.

    run_connector registers broadcast() as the row hook, so it runs on the WS
    loop thread right after each row is enqueued and must never block: each
    client gets a bounded queue drained by its own writer coroutine, and a
    client whose queue overflows is dropped instead of ever stalling the hot
    path. Consumers (Orchestrator5.0, `nc` watchers) receive per line:

        {"type":"hello","service":"RTPR_connector3_curl-feed","proto":1,...}   on connect
        {"type":"article","arrival_ms":...,"data":{...},"row":{...}}     per article
        {"type":"heartbeat","server_ms":...}                             every 5s

    `data` is the synthesized alert+fetch payload (firehose-compatible keys
    id/ticker/tickers/title/article_body/author/article_published_at plus
    article_url/rules/fetch_ms extras), `row` the connector's built row —
    including the CurlTime column (fetch-completion stamp), an additive key
    the firehose feed does not have; arrival_ms is the epoch-ms arrival stamp. Articles are broadcast before
    the writer's dedup (WS reconnects can replay past the pre-fetch dedup
    window) — consumers dedup by data/row ID themselves.
    """

    def __init__(self, port: int, logger: logging.Logger) -> None:
        self.port = port
        self.logger = logger
        self.server: Any = None
        self.heartbeat_task: asyncio.Task | None = None
        # queue -> (peer label, handler task); handler tasks unregister themselves.
        self._clients: dict[asyncio.Queue, tuple[str, asyncio.Task]] = {}

    async def start(self) -> None:
        self.server = await asyncio.start_server(
            self._handle_client, FEED_HOST, self.port
        )
        self.heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="feed_heartbeat"
        )
        self.logger.info("FEED_LISTENING host=%s port=%d", FEED_HOST, self.port)

    async def stop(self) -> None:
        if self.heartbeat_task is not None:
            self.heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.heartbeat_task
        if self.server is not None:
            self.server.close()  # stop accepting; existing handlers cancelled below
        tasks = [task for _, task in list(self._clients.values())]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.server is not None:
            # Only AFTER the handlers are done: on Python 3.12+ wait_closed()
            # also waits for all client handlers, so awaiting it first would
            # deadlock against the cancellations above.
            with contextlib.suppress(Exception):
                await self.server.wait_closed()
        self.logger.info("FEED_STOPPED")

    def broadcast(self, data: dict, row: dict, arrival_dt: datetime) -> None:
        """Row hook (WS loop thread): serialize once, enqueue to every client."""
        if not self._clients:
            return
        line = (
            json.dumps(
                {
                    "type": "article",
                    "arrival_ms": round(arrival_dt.timestamp() * 1000),
                    "data": data,
                    "row": row,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        self._send_to_all(line, kind="article")

    def _send_to_all(self, payload: bytes, kind: str) -> None:
        for q, (peer, task) in list(self._clients.items()):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Reader stopped consuming; drop it rather than buffer unboundedly.
                self._clients.pop(q, None)
                task.cancel()
                self.logger.warning(
                    "FEED_CLIENT_OVERFLOW peer=%s dropped queue_max=%d kind=%s",
                    peer,
                    FEED_CLIENT_QUEUE_MAX,
                    kind,
                )

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(FEED_HEARTBEAT_SECONDS)
            if not self._clients:
                continue
            line = (
                json.dumps(
                    {
                        "type": "heartbeat",
                        "server_ms": round(now_fixed().timestamp() * 1000),
                    },
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            self._send_to_all(line, kind="heartbeat")

    async def _handle_client(self, reader: Any, writer: Any) -> None:
        peername = writer.get_extra_info("peername")
        peer = f"{peername[0]}:{peername[1]}" if isinstance(peername, tuple) else str(peername)
        q: asyncio.Queue = asyncio.Queue(maxsize=FEED_CLIENT_QUEUE_MAX)
        self._clients[q] = (peer, asyncio.current_task())
        self.logger.info(
            "FEED_CLIENT_CONNECTED peer=%s clients=%d", peer, len(self._clients)
        )
        hello = (
            json.dumps(
                {
                    "type": "hello",
                    "service": FEED_SERVICE_NAME,
                    "proto": FEED_PROTO,
                    "server_time": format_log_datetime(now_fixed()),
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        try:
            writer.write(hello)
            await writer.drain()
            while True:
                payload = await q.get()
                writer.write(payload)
                await writer.drain()
        except asyncio.CancelledError:
            pass  # dropped on overflow, or server shutdown
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass  # client went away; detected on the next write
        finally:
            self._clients.pop(q, None)
            # abort(), not close(): close() flushes transport-buffered data first,
            # which never completes against a stalled reader and would hang stop().
            with contextlib.suppress(Exception):
                writer.transport.abort()
            self.logger.info(
                "FEED_CLIENT_DISCONNECTED peer=%s clients=%d", peer, len(self._clients)
            )


async def run_connector(config: Config) -> None:
    api_key = load_api_key(config.config_file)
    logger, log_listener = setup_logging(config.log_dir, config.console_log)

    session: requests.Session | None = None
    fetch_pool: ThreadPoolExecutor | None = None
    try:
        if config.universe_filter:
            universe_file, universe_symbols = await asyncio.to_thread(
                load_latest_universe_symbols, config.universe_dir
            )
        else:
            universe_file, universe_symbols = Path("-"), set()
        universe_cache = UniverseCache(
            symbols=universe_symbols,
            path=universe_file,
            loaded_at=now_fixed(),
        )

        pool_size = max(20, config.fetch_workers + 8)
        session = build_http_session(api_key, pool_size)
        fetch_pool = ThreadPoolExecutor(
            max_workers=config.fetch_workers, thread_name_prefix="rtpr-fetch"
        )

        result_queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue(maxsize=0)
        inflight: set[asyncio.Task] = set()
        shutdown_event = asyncio.Event()

        def request_shutdown() -> None:
            logger.info("SHUTDOWN_REQUESTED")
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, request_shutdown)

        logger.info(
            "STARTUP config_file=%s universe_filter=%d universe_file=%s "
            "universe_symbols=%d dedup=%d flush=%02d:%02d reload=%02d:%02d:%02d "
            "output=%s feed_port=%d fetch_workers=%d http_pool=%d "
            "fetch_timeout=%.1fs fetch_retries=%d",
            config.config_file,
            int(config.universe_filter),
            universe_cache.path,
            len(universe_cache.symbols),
            int(config.dedup),
            config.flush_hour,
            config.flush_minute,
            config.universe_reload_hour,
            config.universe_reload_minute,
            config.universe_reload_second,
            config.table_suffix,
            config.feed_port,
            config.fetch_workers,
            pool_size,
            config.fetch_timeout,
            config.fetch_retries,
        )

        # Local TCP feed for orchestrator consumers — started before the WS task
        # so no accepted article can ever precede the hook registration.
        feed_server: FeedServer | None = None
        if config.feed_port > 0:
            feed_server = FeedServer(config.feed_port, logger)
            await feed_server.start()
            set_row_hook(feed_server.broadcast)

        writer_task = asyncio.create_task(
            dataframe_writer(result_queue, config, logger), name="dataframe_writer"
        )
        reloader_task: asyncio.Task | None = None
        if config.universe_filter:
            reloader_task = asyncio.create_task(
                universe_reloader(universe_cache, config, logger),
                name="universe_reloader",
            )
        ws_task = asyncio.create_task(
            websocket_listener(
                result_queue,
                api_key,
                universe_cache,
                session,
                fetch_pool,
                inflight,
                config,
                logger,
            ),
            name="websocket_listener",
        )

        await shutdown_event.wait()

        if reloader_task is not None:
            reloader_task.cancel()
        ws_task.cancel()
        background = [task for task in (reloader_task, ws_task) if task is not None]
        await asyncio.gather(*background, return_exceptions=True)

        # Drain in-flight fetches BEFORE stopping the feed so their rows still
        # reach both the writer and the TCP consumers.
        if inflight:
            logger.info(
                "SHUTDOWN_DRAIN inflight=%d grace=%.0fs",
                len(inflight),
                SHUTDOWN_FETCH_GRACE_SECONDS,
            )
            done, pending = await asyncio.wait(
                set(inflight), timeout=SHUTDOWN_FETCH_GRACE_SECONDS
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

        if feed_server is not None:
            set_row_hook(None)
            await feed_server.stop()

        await result_queue.put(None)
        await writer_task
    finally:
        if fetch_pool is not None:
            fetch_pool.shutdown(wait=False, cancel_futures=True)
        if session is not None:
            with contextlib.suppress(Exception):
                session.close()
        logger.info("STOPPED")
        log_listener.stop()


SELF_TEST_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>HYLN Investor Alert: Levi &amp; Korsinsky Investigates Hyliion Holdings Corp. (HYLN) for Potential Securities Fraud — RTPR</title>
</head>
<body>
<nav class="nav">
  <div class="nav-inner">
    <a class="brand" href="https://rtpr.io/">RTPR</a>
  </div>
</nav>
<main>
  <article class="card">
    <header class="card-header">
      <div class="meta">
        <span class="meta-source">ACCESSWIRE</span> <span aria-hidden="true">·</span> <time datetime="2026-06-30T15:20:00.730Z">Jun 30, 2026, 11:20 AM EDT</time>
      </div>
      <h1 class="title">HYLN Investor Alert: Levi &amp; Korsinsky Investigates Hyliion Holdings Corp. (HYLN) for Potential Securities Fraud</h1>
      <div class="tickers"><span class="ticker">HYLN</span><span class="exchange">NYSE American</span></div>
    </header>
    <div class="card-body">
      <div class="article-body">NEW YORK CITY, NY / ACCESS <a href="https://www.accessnewswire.com/" target="_blank" rel="nofollow noopener">Newswire</a> / June 30, 2026 / Levi &amp; Korsinsky notifies investors that it has commenced an investigation into Hyliion Holdings Corp. (&quot;Hyliion Holdings Corp.&quot;) (NYSE:HYLN) concerning potential violations of the federal securities laws.

Copyright 2026 ACCESS Newswire. All Rights Reserved.</div>
      <div class="footer-meta">
        <span>Article ID: <code>nACS0Gsm4a</code></span>
        <span>Available for 24 hours after publication</span>
      </div>
    </div>
  </article>
</main>
</body>
</html>
"""

SELF_TEST_NESTED_HTML = (
    '<html><body><div class="card-body">'
    '<div class="article-body is-html"><p>Intro</p>'
    '<div class="lseg"><table><tr><td>1</td></tr></table></div>'
    "<p>Tail &amp; end</p></div>"
    '<div class="footer-meta"><span>Article ID: <code>xyz123</code></span></div>'
    "</div></body></html>"
)

SELF_TEST_CONFIG_TEXT = """# comment line
# another comment

RTPR_API_KEY:
rtpr_TESTKEY123  # inline note

IBKR_ACCOUNT:
U1234567
"""

FIREHOSE_COMPAT_KEYS = (
    "id",
    "ticker",
    "tickers",
    "title",
    "article_body",
    "author",
    "article_published_at",
)


def self_test() -> int:
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}: {detail}")
            print(f"SELF_TEST_FAIL {name} {detail}")

    url = "https://rtpr.io/a/nACS0Gsm4a?exp=1782832920&sig=dd57700c4505"
    check("url_id", article_id_from_url(url) == "nACS0Gsm4a", article_id_from_url(url))

    parsed = parse_article_html(SELF_TEST_HTML)
    expected_headline = (
        "HYLN Investor Alert: Levi & Korsinsky Investigates Hyliion Holdings "
        "Corp. (HYLN) for Potential Securities Fraud"
    )
    check("parse_title", parsed["title"] == expected_headline, repr(parsed["title"]))
    check(
        "parse_published",
        parsed["published"] == "2026-06-30T15:20:00.730Z",
        repr(parsed["published"]),
    )
    check("parse_source", parsed["source"] == "ACCESSWIRE", repr(parsed["source"]))
    check("parse_tickers", parsed["tickers"] == ["HYLN"], repr(parsed["tickers"]))
    check("parse_footer_id", parsed["footer_id"] == "nACS0Gsm4a", repr(parsed["footer_id"]))
    check(
        "parse_body_html",
        parsed["body_html"].startswith("NEW YORK CITY, NY / ACCESS <a href="),
        repr(parsed["body_html"][:60]),
    )

    nested = parse_article_html(SELF_TEST_NESTED_HTML)
    expected_nested = (
        '<p>Intro</p><div class="lseg"><table><tr><td>1</td></tr></table></div>'
        "<p>Tail &amp; end</p>"
    )
    check("nested_body", nested["body_html"] == expected_nested, repr(nested["body_html"]))

    alert = {
        "type": "alert",
        "ticker": "HYLN",
        "rules": [{"rule_name": "Universe PRs"}],
        "article_published_at": "2026-06-30T15:20:00.730Z",
        "article_url": url,
    }
    arrival_dt = to_fixed_tz(datetime(2026, 6, 30, 15, 20, 1, 11000, tzinfo=timezone.utc))
    curl_dt = to_fixed_tz(datetime(2026, 6, 30, 15, 20, 1, 98000, tzinfo=timezone.utc))
    data, row = build_alert_data_and_row(
        alert, url, "nACS0Gsm4a", SELF_TEST_HTML, arrival_dt, curl_dt, 87, 1, 200
    )

    required = {
        "Symbol": "HYLN",
        "Tickers": '["HYLN"]',
        "ID": "nACS0Gsm4a",
        "ArrivalDate": "2026-06-30",
        "Created": "11:20:00.730",
        "ArrivalTime": "11:20:01.011",
        "CurlTime": "11:20:01.098",
        "Headline": expected_headline,
    }
    for key, expected in required.items():
        check(f"row_{key}", row[key] == expected, f"expected={expected!r} actual={row[key]!r}")

    check(
        "columns_order",
        PR_COLUMNS.index("ArrivalTime") == PR_COLUMNS.index("Created") + 1
        and PR_COLUMNS.index("CurlTime") == PR_COLUMNS.index("ArrivalTime") + 1,
        repr(PR_COLUMNS),
    )
    millis_re = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")
    check(
        "millis_3_digits",
        all(millis_re.fullmatch(row[key]) for key in ("Created", "ArrivalTime", "CurlTime")),
        repr({key: row[key] for key in ("Created", "ArrivalTime", "CurlTime")}),
    )
    check(
        "row_body_clean",
        bool(row["Body"])
        and "<" not in row["Body"]
        and "&amp;" not in row["Body"]
        and row["Body"].startswith(
            "NEW YORK CITY, NY / ACCESS Newswire / June 30, 2026 / Levi & Korsinsky"
        ),
        repr(row["Body"][:80]),
    )

    missing_keys = [key for key in FIREHOSE_COMPAT_KEYS if key not in data]
    check("data_compat_keys", not missing_keys, repr(missing_keys))
    check("data_rules", data["rules"] == ["Universe PRs"], repr(data["rules"]))
    check("data_fetch_ms", data["fetch_ms"] == 87, repr(data["fetch_ms"]))
    check(
        "data_json_serializable",
        bool(json.dumps({"data": data, "row": row}, ensure_ascii=False)),
    )

    values = parse_n2_config_text(SELF_TEST_CONFIG_TEXT)
    check(
        "config_api_key",
        values.get("RTPR_API_KEY") == "rtpr_TESTKEY123",
        repr(values.get("RTPR_API_KEY")),
    )
    check(
        "config_second_label",
        values.get("IBKR_ACCOUNT") == "U1234567",
        repr(values.get("IBKR_ACCOUNT")),
    )

    # Multi-ticker: still one row, primary ticker as Symbol, full array preserved.
    multi = normalize_tickers(["MOGU", "BABA"], "MOGU")
    check(
        "multi_tickers",
        multi == ["MOGU", "BABA"] and tickers_json(multi) == '["MOGU","BABA"]',
        repr(multi),
    )

    check("df_columns", list(PR_dataframe.columns) == PR_COLUMNS, repr(list(PR_dataframe.columns)))

    if failures:
        print(f"SELF_TEST_FAILURES={len(failures)}")
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
