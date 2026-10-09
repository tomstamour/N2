#!/usr/bin/env python3
"""Low-latency RTPR firehose WebSocket connector.

Unlike the alerts socket (RTPR_connector2), the firehose socket
(wss://ws.rtpr.io?apiKey=...) pushes the full article inline:

    {"type":"article","data":{id, ticker, tickers, title, article_body,
                              author, created, ...}}

so there is no HTTP fetch. The hot path is intentionally tiny:

    firehose WS -> websocket_listener -> result_queue -> dataframe_writer

The listener stamps ArrivalTime the instant a frame arrives, parses only what
is needed, filters against the universe set, builds the row, and enqueues it.
The single dataframe_writer task is the only mutator of PR_dataframe and owns
the per-day dedup set and the daily disk flush.
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
import socket
import sys
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any

try:
    import pandas as pd
    import websockets
except ImportError as exc:  # pragma: no cover - depends on runtime env
    missing = exc.name or str(exc)
    raise SystemExit(
        f"Missing dependency: {missing}. Install with: pip install websockets pandas"
    ) from exc


FIXED_TZ = timezone(timedelta(hours=-4), name="UTC-4")
PR_COLUMNS = [
    "Symbol",
    "Tickers",
    "ID",
    "ArrivalDate",
    "Created",
    "ArrivalTime",
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

# --- Upstream WebSocket liveness --------------------------------------------
# The RTPR server sends an application-level {"type":"ping"} every 30s, which we
# answer with PONG_FRAME. ANSWERING IS NOT CHECKING. On 2026-09-23 the upstream
# went silent at 06:02:32 and this process sat idle for 2h48m still holding an
# ESTABLISHED socket that never delivered another byte -- 50 pre-market PRs lost,
# no WARNING, no ERROR, nothing in `ps` to see. A half-open TCP connection never
# delivers a FIN, so `async for raw in ws` simply never yields again, no
# ConnectionClosed is ever raised, and the reconnect path in websocket_listener
# is unreachable. Silence itself has to be the alarm.
#
# 90s is 3 missed server pings. This mirrors FEED_SILENCE_TIMEOUT_SEC in
# Orchestrator5.3.py, which guards the NEXT hop down -- and which is exactly why
# that hop rode out the same incident and reconnected on its own.
WS_IDLE_TIMEOUT_SECONDS = 90.0
WS_IDLE_POLL_SECONDS = 5.0

# Library-level keepalive (RFC 6455 protocol Ping frames, a separate mechanism
# from the server's application-level ping above). Set to 0 to disable and
# restore pre-2026-09-23 behaviour if RTPR's edge ever stops answering Pings.
WS_PING_INTERVAL_SECONDS = 30.0
WS_PING_TIMEOUT_SECONDS = 30.0

# OS-level half-open detection; belt and braces with the idle watchdog.
WS_TCP_KEEPIDLE = 60
WS_TCP_KEEPINTVL = 10
WS_TCP_KEEPCNT = 3

# In-process row hook (default None -> no extra work on the hot path).
# websocket_listener calls _row_hook(data, row, arrival_dt) on the WS loop
# thread right after the row is enqueued — BEFORE the writer's dedup, so WS
# reconnect replays flow through and consumers must dedup by article ID.
# run_connector registers FeedServer.broadcast here when --feed-port is
# enabled (the standard topology: this standalone connector feeding
# Orchestrator5.0 over local TCP); tests may register their own callable.
# The callable MUST be non-blocking and MUST NOT mutate `data` or `row`.
_row_hook = None


def set_row_hook(fn) -> None:
    global _row_hook
    _row_hook = fn


@dataclass(frozen=True)
class Config:
    api_key_file: Path
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
    subscribe_all: bool
    dedup: bool
    table_suffix: str
    table_delimiter: str
    ws_url_template: str
    ws_idle_timeout: float
    ws_ping_interval: float
    ws_ping_timeout: float
    feed_port: int
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


class ConnectionState:
    """Shared handle to the live websocket so the reloader can re-subscribe."""

    def __init__(self) -> None:
        self.ws: Any = None


class FixedTZFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, FIXED_TZ)
        return format_log_datetime(dt)


class DailyFixedTZFileHandler(logging.Handler):
    """Write log records to RTPR_connector3_Firehose_YYYY_MM_DD.log in fixed UTC-4."""

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
        return self.log_dir / f"RTPR_connector3_Firehose_{date_key}.log"

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
        description="Connect to the RTPR firehose WebSocket and cache universe PRs."
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
        "--subscribe-all",
        action="store_true",
        help='Subscribe with tickers:[""] (every ticker) instead of the universe list.',
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
        help="Log every not_in_universe skip (forced on, even with --subscribe-all).",
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
        default="wss://ws.rtpr.io?apiKey={api_key}",
    )
    parser.add_argument(
        "--ws-idle-timeout",
        type=float,
        default=WS_IDLE_TIMEOUT_SECONDS,
        help=(
            "Reconnect if NOTHING (server ping or article) arrives on the upstream "
            "WebSocket for this many seconds. The server pings every 30s, so the "
            "default is 3 missed pings. 0 disables the watchdog (not advised: it is "
            "the only thing that catches a silently half-open upstream)."
        ),
    )
    parser.add_argument(
        "--ws-ping-interval",
        type=float,
        default=WS_PING_INTERVAL_SECONDS,
        help=(
            "Seconds between client-sent protocol Ping frames (0 disables, which was "
            "the behaviour before 2026-09-23)."
        ),
    )
    parser.add_argument(
        "--ws-ping-timeout",
        type=float,
        default=WS_PING_TIMEOUT_SECONDS,
        help="Seconds to wait for a Pong before treating the connection as dead.",
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
        "--self-test",
        action="store_true",
        help="Run the offline row-builder self-test and exit.",
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

    suffix, delimiter = OUTPUT_FORMATS[args.output_format]

    if args.no_log_skips:
        log_skips = False
    elif args.log_skips:
        log_skips = True
    else:
        # not_in_universe is expected/high-volume when subscribed to everything.
        log_skips = not args.subscribe_all

    return Config(
        api_key_file=args.api_key_file,
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
        log_skips=log_skips,
        console_log=not args.no_console_log,
        subscribe_all=args.subscribe_all,
        dedup=not args.no_dedup,
        table_suffix=suffix,
        table_delimiter=delimiter,
        ws_url_template=args.ws_url_template,
        ws_idle_timeout=args.ws_idle_timeout,
        ws_ping_interval=args.ws_ping_interval,
        ws_ping_timeout=args.ws_ping_timeout,
        feed_port=args.feed_port,
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

    logger = logging.getLogger("RTPR_connector3_Firehose")
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
    """Firehose field name is uncertain; try the documented/likely keys in order."""
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
    """Firehose article_body may be HTML or plain text; return clean plain text."""
    text = str(value or "")
    if "<" in text and ">" in text:
        text = TAG_RE.sub(" ", text)
        text = unescape(text)
    return clean_plain_text(text)


def build_row_from_data(
    data: dict[str, Any],
    tickers: list[str],
    symbol: str,
    arrival_dt: datetime,
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
        "Headline": strip_rtpr_suffix(clean_inline_text(str(title or ""))),
        "Body": to_plain_body(body),
    }


def build_subscribe_payload(symbols: set[str], subscribe_all: bool) -> str:
    tickers = [""] if subscribe_all else sorted(symbols)
    return json.dumps({"action": "subscribe", "tickers": tickers}, separators=(",", ":"))


async def send_subscribe(
    ws: Any,
    symbols: set[str],
    config: Config,
    logger: logging.Logger,
    reason: str,
) -> None:
    payload = build_subscribe_payload(symbols, config.subscribe_all)
    await ws.send(payload)
    count = "ALL" if config.subscribe_all else str(len(symbols))
    logger.info("WS_SUBSCRIBE_SENT reason=%s count=%s", reason, count)


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
    conn_state: ConnectionState,
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
            if not config.subscribe_all and conn_state.ws is not None:
                try:
                    await send_subscribe(conn_state.ws, symbols, config, logger, "reload")
                except Exception as exc:
                    logger.warning(
                        "UNIVERSE_RESUBSCRIBE_FAIL error_type=%s", type(exc).__name__
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
        path = config.table_dir / f"PR_dataframe_{format_millis_date(stamp)}{config.table_suffix}"
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


def _enable_tcp_keepalive(ws: Any, logger: logging.Logger) -> None:
    """OS-level half-open detection, belt and braces with the idle watchdog.

    Best effort by design: this reaches into the transport for the raw socket, which
    not every websockets backend exposes. A failure here is harmless -- the idle
    watchdog is the authoritative check -- so it is logged and never raised."""
    try:
        transport = getattr(ws, "transport", None)
        sock = transport.get_extra_info("socket") if transport is not None else None
        if sock is None:
            return
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (
            ("TCP_KEEPIDLE", WS_TCP_KEEPIDLE),
            ("TCP_KEEPINTVL", WS_TCP_KEEPINTVL),
            ("TCP_KEEPCNT", WS_TCP_KEEPCNT),
        ):
            if hasattr(socket, name):
                sock.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
    except Exception as exc:  # pragma: no cover - platform/transport dependent
        logger.info("WS_KEEPALIVE_SKIP error_type=%s", type(exc).__name__)


async def websocket_listener(
    result_queue: asyncio.Queue[dict[str, str] | None],
    api_key: str,
    universe_cache: UniverseCache,
    conn_state: ConnectionState,
    config: Config,
    logger: logging.Logger,
) -> None:
    ws_url = config.ws_url_template.format(api_key=api_key)
    backoff = 2.0
    consecutive_failures = 0
    first_article_logged = False

    while True:
        idle_fired = False
        watchdog = None
        try:
            logger.info("WS_CONNECT")
            async with websockets.connect(
                ws_url,
                ping_interval=(config.ws_ping_interval or None),
                ping_timeout=(config.ws_ping_timeout or None),
                max_queue=None,
            ) as ws:
                conn_state.ws = ws
                backoff = 2.0
                consecutive_failures = 0
                _enable_tcp_keepalive(ws, logger)
                logger.info("WS_CONNECTED")
                await send_subscribe(ws, universe_cache.symbols, config, logger, "connect")

                # Silence watchdog -- see WS_IDLE_TIMEOUT_SECONDS above for why this
                # exists. It runs as its own task and CLOSES the socket rather than
                # cancelling recv(), so a frame can never be lost mid-read. The close
                # ends `async for` cleanly, and execution then falls through to the
                # reconnect path at the bottom of this loop, reusing the backoff that
                # is already there.
                last_msg_at = now_fixed()

                async def _idle_watchdog() -> None:
                    nonlocal idle_fired
                    while True:
                        await asyncio.sleep(WS_IDLE_POLL_SECONDS)
                        idle_s = (now_fixed() - last_msg_at).total_seconds()
                        if idle_s < config.ws_idle_timeout:
                            continue
                        idle_fired = True
                        logger.warning(
                            "WS_IDLE_TIMEOUT idle=%.1fs limit=%.1fs — upstream silent, "
                            "closing to force a reconnect",
                            idle_s,
                            config.ws_idle_timeout,
                        )
                        await ws.close()
                        return

                if config.ws_idle_timeout > 0:
                    watchdog = asyncio.create_task(_idle_watchdog())

                async for raw in ws:
                    # ANY frame -- article, ping or control -- proves the upstream is
                    # alive. This stamp is what the idle watchdog reads.
                    last_msg_at = arrival_dt = now_fixed()
                    try:
                        msg = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        logger.warning(
                            "WS_BAD_JSON bytes=%d", len(raw) if raw is not None else 0
                        )
                        continue

                    msg_type = msg.get("type")

                    if msg_type == "article":
                        data = msg.get("data")
                        if not isinstance(data, dict):
                            data = msg
                        symbol = normalize_symbol(data.get("ticker"))
                        tickers = normalize_tickers(data.get("tickers"), symbol)
                        if not tickers:
                            logger.info(
                                "SKIP reason=missing_ticker id=%s",
                                str(data.get("id") or "-"),
                            )
                            continue
                        if not universe_cache.intersects(tickers):
                            if config.log_skips:
                                logger.info(
                                    "SKIP %-6s reason=not_in_universe tickers=%s",
                                    symbol or "-",
                                    tickers_json(tickers),
                                )
                            continue

                        row = build_row_from_data(data, tickers, symbol, arrival_dt)
                        result_queue.put_nowait(row)

                        if _row_hook is not None:
                            try:
                                _row_hook(data, row, arrival_dt)
                            except Exception:
                                logger.exception(
                                    "ROW_HOOK_ERROR id=%s", row["ID"] or "-"
                                )

                        if not first_article_logged:
                            first_article_logged = True
                            logger.info(
                                "FIRST_ARTICLE_KEYS keys=%s",
                                json.dumps(sorted(data.keys())),
                            )
                        logger.info(
                            "PR %-6s %-10s lat=%dms created=%s arrival=%s tickers=%s",
                            row["Symbol"] or "-",
                            row["ID"] or "-",
                            elapsed_ms(arrival_dt, now_fixed()),
                            row["Created"],
                            row["ArrivalTime"],
                            row["Tickers"],
                        )
                        continue

                    if msg_type == "ping":
                        await ws.send(PONG_FRAME)
                        if config.log_heartbeats:
                            logger.info("HEARTBEAT pong")
                        continue

                    if msg_type == "connected":
                        logger.info("WS_HELLO connected")
                        await send_subscribe(
                            ws, universe_cache.symbols, config, logger, "connected"
                        )
                        continue

                    if msg_type == "subscribed":
                        tk = msg.get("tickers")
                        count = len(tk) if isinstance(tk, list) else 0
                        logger.info("WS_SUBSCRIBED count=%d", count)
                        continue

                    logger.info("WS_SKIP type=%s", msg_type)

            # A watchdog close exits the loop above CLEANLY, so it never reaches the
            # except handlers. Count it, or a genuinely dead upstream would be retried
            # at the floor delay forever instead of backing off.
            if idle_fired:
                consecutive_failures += 1

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
        finally:
            conn_state.ws = None
            if watchdog is not None:
                watchdog.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await watchdog
                watchdog = None

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

        {"type":"hello","service":"RTPR_connector3_Firehose-feed","proto":1,...}   on connect
        {"type":"article","arrival_ms":...,"data":{...},"row":{...}}     per article
        {"type":"heartbeat","server_ms":...}                             every 5s

    `data` is the raw firehose payload, `row` the connector's built row;
    arrival_ms is the epoch-ms arrival stamp. Articles are broadcast pre-dedup
    (WS reconnects can replay) — consumers dedup by data/row ID themselves.
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
                    "service": "RTPR_connector3_Firehose-feed",
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

        result_queue: asyncio.Queue[dict[str, str] | None] = asyncio.Queue(maxsize=0)
        conn_state = ConnectionState()
        shutdown_event = asyncio.Event()

        def request_shutdown() -> None:
            logger.info("SHUTDOWN_REQUESTED")
            shutdown_event.set()

        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            with contextlib.suppress(NotImplementedError):
                loop.add_signal_handler(sig, request_shutdown)

        logger.info(
            "STARTUP universe_file=%s universe_symbols=%d subscribe_all=%d dedup=%d "
            "flush=%02d:%02d reload=%02d:%02d:%02d output=%s feed_port=%d",
            universe_cache.path,
            len(universe_cache.symbols),
            int(config.subscribe_all),
            int(config.dedup),
            config.flush_hour,
            config.flush_minute,
            config.universe_reload_hour,
            config.universe_reload_minute,
            config.universe_reload_second,
            config.table_suffix,
            config.feed_port,
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
        reloader_task = asyncio.create_task(
            universe_reloader(universe_cache, conn_state, config, logger),
            name="universe_reloader",
        )
        ws_task = asyncio.create_task(
            websocket_listener(
                result_queue, api_key, universe_cache, conn_state, config, logger
            ),
            name="websocket_listener",
        )

        await shutdown_event.wait()

        reloader_task.cancel()
        ws_task.cancel()
        await asyncio.gather(reloader_task, ws_task, return_exceptions=True)

        if feed_server is not None:
            set_row_hook(None)
            await feed_server.stop()

        await result_queue.put(None)
        await writer_task
    finally:
        logger.info("STOPPED")
        log_listener.stop()


def self_test() -> int:
    data = {
        "id": "nBw481tnja",
        "ticker": "MOGU",
        "tickers": ["MOGU"],
        "title": "MOGU Announces Unaudited Financial Results - RTPR",
        "article_body": "<p>MOGU announced <b>results</b> today.</p>",
        "author": "GlobeNewswire",
        "article_published_at": "2026-06-22T10:34:00.256Z",
    }
    arrival_dt = to_fixed_tz(datetime(2026, 6, 22, 10, 34, 1, 11000, tzinfo=timezone.utc))
    symbol = normalize_symbol(data.get("ticker"))
    tickers = normalize_tickers(data.get("tickers"), symbol)
    row = build_row_from_data(data, tickers, symbol, arrival_dt)

    required = {
        "Symbol": "MOGU",
        "Tickers": '["MOGU"]',
        "ID": "nBw481tnja",
        "ArrivalDate": "2026-06-22",
        "Created": "06:34:00.256",
        "ArrivalTime": "06:34:01.011",
        "Headline": "MOGU Announces Unaudited Financial Results",
    }
    for key, expected in required.items():
        actual = row[key]
        if actual != expected:
            print(f"SELF_TEST_FAIL {key}: expected={expected!r} actual={actual!r}")
            return 1
    if "<" in row["Body"] or not row["Body"]:
        print(f"SELF_TEST_FAIL body not clean: {row['Body']!r}")
        return 1

    # Multi-ticker: still one row, primary ticker as Symbol, full array preserved.
    multi = normalize_tickers(["MOGU", "BABA"], "MOGU")
    if multi != ["MOGU", "BABA"] or tickers_json(multi) != '["MOGU","BABA"]':
        print(f"SELF_TEST_FAIL multi_tickers: actual={multi!r}")
        return 1

    if list(PR_dataframe.columns) != PR_COLUMNS:
        print(f"SELF_TEST_FAIL columns: actual={list(PR_dataframe.columns)!r}")
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
