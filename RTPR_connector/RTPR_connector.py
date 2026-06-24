#!/usr/bin/env python3
"""
RTPR_connector.py — low-latency connector to the RTPR.io alerts WebSocket firehose.

Architecture (single process, single asyncio event loop, uvloop-accelerated):

    [WS-listener task] --jobQueue--> [N fetch-workers] --resultQueue--> [DataFrame-writer]
          |                               |                                    |
      ping/pong,                    shared aiohttp                       single writer
      reconnect+backoff,            ClientSession (keep-alive),          (list append)
      ArrivalTime stamp,            lxml HTML parse,                          |
      universe filter,              CurlTime stamp                     [Flush task @ 20:35]
      dedup-by-ID

  * 1 stateful WS connection, many stateless consumers, 1 shared pooled HTTP session,
    1 writer that owns the in-memory PR_dataframe.
  * The WS-listener does *zero* HTTP / disk / heavy parsing so the TCP receive buffer never
    backs up during bursts (10-15 PRs within ~1s). It only: stamps arrival time, json-decodes
    the tiny frame, checks the ticker against the ~700-symbol universe (O(1) frozenset), dedups
    by article ID, and hands a job to the queue.
  * Workers are asyncio coroutines (NOT OS threads): a worker mid-fetch is a coroutine suspended
    on a socket await, so concurrency is bounded by the worker count, independent of CPU cores.
  * Nothing is written to disk except the rotating log and the once-daily table flush.

Columns produced (exact order):
    Symbol | Tickers | ID | ArrivalDate | Created | ArrivalTime | CurlTime | Headline | Body | Exchange | Source

Field sources (established via rtpr_probe.py, 2026-06-22):
  - WS frame:  Symbol(ticker), ID(article_url slug), ArrivalDate/Created(article_published_at), ArrivalTime(recv)
  - HTML page: Headline(h1.title), Tickers(span.ticker), Exchange(span.exchange),
               Source(span.meta-source), Body(div.article-body), CurlTime(fetch complete)

Usage:
    python3 RTPR_connector.py                 # auto-loads the newest universe file; re-scans daily @ 03:58 ET
    python3 RTPR_connector.py --self-test     # offline parser check against the saved probe dump
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import logging.handlers
import os
import queue as _queue
import re
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import lxml.html
import pandas as pd
import websockets

# --------------------------------------------------------------------------------------
# Configuration constants
# --------------------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent

WS_URL = "wss://ws.rtpr.io/ws-alerts?apiKey={key}"
PONG = json.dumps({"type": "pong"})

# Timezone for ALL output time fields and the daily flush trigger.
# America/New_York is DST-aware (UTC-4 in summer / UTC-5 in winter) and matches RTPR's own
# rendering + the US market clock. For a literal frozen UTC-4 offset, use ZoneInfo("Etc/GMT+4").
DISPLAY_TZ = ZoneInfo("America/New_York")
UTC = timezone.utc

COLUMNS = [
    "Symbol", "Tickers", "ID", "ArrivalDate", "Created", "ArrivalTime",
    "CurlTime", "Headline", "Body", "Exchange", "Source",
]

# Defaults (all overridable via CLI)
DEFAULT_UNIVERSE_DIR = SCRIPT_DIR.parent / "universe_finder" / "data"
UNIVERSE_FILE_RE = re.compile(r"^stocks_universe_(\d{4}-\d{2}-\d{2})\.tsv$")
DEFAULT_KEY_FILE = SCRIPT_DIR / "RTPR_API-Key.txt"
DEFAULT_OUT_DIR = SCRIPT_DIR / "tables"
DEFAULT_LOG_DIR = SCRIPT_DIR / "logs"
SELF_TEST_DUMP = "/tmp/rtpr_raw.primary.html"

log = logging.getLogger("rtpr")


# --------------------------------------------------------------------------------------
# Optional consumer hooks (latency-neutral) — set by an embedding process such as
# Orchestrator5.0. Both default to None so a standalone run is byte-for-byte unaffected:
# the only added cost is a single `is not None` check. When set, each is fired with a
# NON-BLOCKING submit (the consumer offloads its own heavy work to its own threads), so
# neither touches the WS-ingest / curl / parse hot-path latency.
#   _alert_hook(symbol: str, art_id: str, arrival_dt: datetime)  — fired pre-curl, the
#       instant a universe+dedup-passing alert is enqueued (the pre-fetch "arm" moment).
#   _row_hook(job: dict, row: dict)  — fired the instant a row is built by a worker
#       (post-curl), before it reaches the batched store.
# --------------------------------------------------------------------------------------

_alert_hook = None
_row_hook = None


def set_alert_hook(fn) -> None:
    """Register a callable fired pre-curl on each enqueued alert. See module note."""
    global _alert_hook
    _alert_hook = fn


def set_row_hook(fn) -> None:
    """Register a callable fired the instant a worker builds a row. See module note."""
    global _row_hook
    _row_hook = fn


# --------------------------------------------------------------------------------------
# Time helpers — everything is computed in UTC then rendered in DISPLAY_TZ
# --------------------------------------------------------------------------------------

def now_utc() -> datetime:
    return datetime.now(UTC)


def fmt_time(dt_utc: datetime) -> str:
    """tz-aware UTC datetime -> 'HH:MM:SS.mmm' in DISPLAY_TZ (millisecond precision)."""
    lt = dt_utc.astimezone(DISPLAY_TZ)
    return lt.strftime("%H:%M:%S.") + f"{lt.microsecond // 1000:03d}"


def fmt_date(dt_utc: datetime) -> str:
    """tz-aware UTC datetime -> 'YYYY-MM-DD' in DISPLAY_TZ."""
    return dt_utc.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d")


def parse_published_at(s: str) -> datetime:
    """Parse RTPR 'article_published_at' (ISO-8601, e.g. '2026-06-22T22:57:42.252Z') -> aware UTC."""
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# --------------------------------------------------------------------------------------
# Startup loaders (run at boot; the universe is additionally hot-reloaded once a day)
# --------------------------------------------------------------------------------------

def find_latest_universe(data_dir: Path) -> Path:
    """Return the newest ``stocks_universe_YYYY-MM-DD.tsv`` in ``data_dir``.

    "Newest" is the greatest date embedded in the filename (ISO dates sort lexically), not
    mtime — robust to files being re-generated out of order. The ``_nonFiltered`` sibling
    variants never match ``UNIVERSE_FILE_RE``, so they are excluded by construction.
    """
    best: tuple[str, Path] | None = None
    for p in Path(data_dir).iterdir():
        m = UNIVERSE_FILE_RE.match(p.name)
        if m and (best is None or m.group(1) > best[0]):
            best = (m.group(1), p)
    if best is None:
        raise FileNotFoundError(
            f"No universe file matching 'stocks_universe_YYYY-MM-DD.tsv' found in {data_dir}"
        )
    return best[1]


def load_universe(path: Path) -> frozenset[str]:
    """Load only the 'Symbol' column of the TSV universe into an uppercased frozenset (O(1) lookups)."""
    if not Path(path).exists():
        raise FileNotFoundError(f"Universe file not found: {path}")
    df = pd.read_csv(path, sep="\t", usecols=["Symbol"])
    syms = frozenset(
        str(s).strip().upper() for s in df["Symbol"] if str(s).strip() and str(s).strip().lower() != "nan"
    )
    if not syms:
        raise ValueError(f"Universe file {path} produced 0 symbols")
    return syms


def load_api_key(path: Path) -> str:
    """Read the RTPR API key (the line starting with 'rtpr_') from the key file."""
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith("rtpr_"):
                return line
    raise RuntimeError(f"No RTPR API key (line starting with 'rtpr_') found in {path}")


# --------------------------------------------------------------------------------------
# Article parsing — fixed RTPR permalink HTML template (see rtpr_probe.py / doc dump)
# --------------------------------------------------------------------------------------

def extract_id(url: str) -> str:
    """Article ID = the permalink path slug: https://rtpr.io/a/<ID>?exp=...&sig=... -> <ID>."""
    try:
        return url.split("/a/", 1)[1].split("?", 1)[0]
    except IndexError:
        return ""


def parse_article(html_bytes: bytes) -> tuple[str, list[str], list[str], str, str]:
    """Extract (headline, tickers[], exchanges[], source, body) from the RTPR HTML page."""
    t = lxml.html.fromstring(html_bytes)

    h1 = t.xpath('//h1[@class="title"]')
    if h1:
        headline = h1[0].text_content().strip()
    else:  # fallback: <title>...headline... — RTPR</title>
        title = t.xpath("//title/text()")
        headline = title[0].rsplit(" — RTPR", 1)[0].strip() if title else ""

    tickers = [x.strip() for x in t.xpath('//span[@class="ticker"]/text()') if x.strip()]
    exchanges = [x.strip() for x in t.xpath('//span[@class="exchange"]/text()') if x.strip()]

    src = t.xpath('//span[@class="meta-source"]/text()')
    source = src[0].strip() if src else ""

    body_el = t.xpath('//div[contains(@class,"article-body")]')
    body = body_el[0].text_content().strip() if body_el else ""

    return headline, tickers, exchanges, source, body


def _jarr(items: list[str]) -> str:
    """Compact JSON array string, e.g. ['WTM'] -> '[\"WTM\"]' (matches the spec's stringified form)."""
    return json.dumps(items, separators=(",", ":"))


def build_row(job: dict, headline: str, tickers: list[str], exchanges: list[str],
              source: str, body: str, curl_dt: datetime) -> dict:
    """Assemble a PR_dataframe row (keys inserted in COLUMNS order)."""
    return {
        "Symbol": job["symbol"],
        "Tickers": _jarr(tickers),
        "ID": job["id"],
        "ArrivalDate": fmt_date(job["published_at"]),
        "Created": fmt_time(job["published_at"]),
        "ArrivalTime": fmt_time(job["arrival_dt"]),
        "CurlTime": fmt_time(curl_dt),
        "Headline": headline,
        "Body": body,
        "Exchange": _jarr(exchanges),
        "Source": source,
    }


def build_failed_row(job: dict, curl_dt: datetime) -> dict:
    """Metadata-only row used when the fetch/parse ultimately fails — preserves the event."""
    return {
        "Symbol": job["symbol"],
        "Tickers": "[]",
        "ID": job["id"],
        "ArrivalDate": fmt_date(job["published_at"]),
        "Created": fmt_time(job["published_at"]),
        "ArrivalTime": fmt_time(job["arrival_dt"]),
        "CurlTime": fmt_time(curl_dt),
        "Headline": "",
        "Body": "",
        "Exchange": "[]",
        "Source": "",
    }


# --------------------------------------------------------------------------------------
# Fetch instrumentation — per-curl connection-stage breakdown + in-flight gauge
# (ported from N2copy/scripts/newswatcher3/rtpr_curl_probe.py so a single live burst
#  self-diagnoses where the latency goes: dns / queued / connect+TLS / ttfb / body).
# --------------------------------------------------------------------------------------

class Inflight:
    """Count of fetches currently in flight. Read/written only on the event-loop
    thread (ws-listener + workers), so no lock is needed."""
    __slots__ = ("n",)

    def __init__(self) -> None:
        self.n = 0


def _make_trace_config() -> aiohttp.TraceConfig:
    """aiohttp TraceConfig that stamps each connection lifecycle event onto the
    request's trace_request_ctx dict (monotonic clock)."""
    def _stamp(key):
        async def _cb(session, ctx, params):
            d = getattr(ctx, "trace_request_ctx", None)
            if isinstance(d, dict):
                d.setdefault(key, time.monotonic())
        return _cb

    async def _mark_reused(session, ctx, params):
        d = getattr(ctx, "trace_request_ctx", None)
        if isinstance(d, dict):
            d["reused"] = True

    tc = aiohttp.TraceConfig()
    tc.on_request_start.append(_stamp("t_start"))
    tc.on_dns_resolvehost_start.append(_stamp("t_dns_start"))
    tc.on_dns_resolvehost_end.append(_stamp("t_dns_end"))
    tc.on_connection_queued_start.append(_stamp("t_queued_start"))
    tc.on_connection_queued_end.append(_stamp("t_queued_end"))
    tc.on_connection_create_start.append(_stamp("t_create_start"))
    tc.on_connection_create_end.append(_stamp("t_create_end"))
    tc.on_connection_reuseconn.append(_mark_reused)
    tc.on_response_chunk_received.append(_stamp("t_ttfb"))
    tc.on_request_end.append(_stamp("t_end"))
    return tc


def _dur(ctx: dict, a: str, b: str):
    ta, tb = ctx.get(a), ctx.get(b)
    return (tb - ta) if (ta is not None and tb is not None) else None


def _stage_durations(ctx: dict) -> dict:
    """Disjoint-ish per-stage seconds from the trace stamps (None if not reached)."""
    start, ttfb = ctx.get("t_start"), ctx.get("t_ttfb")
    return {
        "dns":     _dur(ctx, "t_dns_start", "t_dns_end"),
        "queued":  _dur(ctx, "t_queued_start", "t_queued_end"),
        "connect": _dur(ctx, "t_create_start", "t_create_end"),
        "ttfb":    (ttfb - start) if (start is not None and ttfb is not None) else None,
    }


def _fmt_s(x) -> str:
    return f"{x:.3f}" if isinstance(x, (int, float)) else "NA"


# --------------------------------------------------------------------------------------
# In-memory store — the ONLY component that mutates PR_dataframe state (single writer)
# --------------------------------------------------------------------------------------

class PRStore:
    """Accumulates rows in memory; materializes the `PR_dataframe` on demand / at flush.

    Rows are appended as plain dicts (O(1)) and the DataFrame is built only when needed
    (snapshot / daily flush), which avoids per-batch concat reallocation. A short-held lock
    guards the swap so the flush executor never races the writer.
    """

    def __init__(self) -> None:
        self._rows: list[dict] = []
        self._lock = threading.Lock()

    def extend(self, rows: list[dict]) -> None:
        if rows:
            with self._lock:
                self._rows.extend(rows)

    def swap_out(self) -> pd.DataFrame:
        """Atomically take all rows and reset to empty; returns them as a DataFrame."""
        with self._lock:
            old, self._rows = self._rows, []
        return pd.DataFrame(old, columns=COLUMNS)

    def dataframe(self) -> pd.DataFrame:
        with self._lock:
            return pd.DataFrame(list(self._rows), columns=COLUMNS)

    def __len__(self) -> int:
        with self._lock:
            return len(self._rows)


# --------------------------------------------------------------------------------------
# Coroutines
# --------------------------------------------------------------------------------------

async def ws_listener(cfg, jobq: asyncio.Queue, seen_ids: set, inflight: Inflight) -> None:
    """Owns the single WS connection: connect, heartbeat, reconnect, filter, enqueue."""
    url = WS_URL.format(key=cfg.api_key)
    backoff = 2
    consecutive_failures = 0

    while True:
        try:
            async with websockets.connect(url, ping_interval=None, max_size=2 ** 20) as ws:
                log.info("WS connected to ws.rtpr.io/ws-alerts")
                backoff = 2
                consecutive_failures = 0

                async for raw in ws:
                    arrival_dt = now_utc()  # stamp the instant of receipt, before any work
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue

                    mtype = msg.get("type")
                    if mtype == "ping":
                        await ws.send(PONG)
                        continue
                    if mtype != "alert":
                        if mtype in ("connected", "subscribed"):
                            log.info("WS %s: %s", mtype, msg.get("message") or msg.get("plan") or "")
                        continue

                    ticker = msg.get("ticker")
                    if not ticker or ticker.upper() not in cfg.universe:
                        continue  # filter BEFORE fetch — the core latency win
                    article_url = msg.get("article_url")
                    if not article_url:
                        continue
                    art_id = extract_id(article_url)
                    if art_id in seen_ids:
                        continue  # dedup (e.g. reconnect replays)
                    seen_ids.add(art_id)

                    try:
                        pub_dt = parse_published_at(msg["article_published_at"])
                    except Exception:
                        pub_dt = arrival_dt

                    job = {
                        "symbol": ticker,
                        "id": art_id,
                        "url": article_url,
                        "published_at": pub_dt,
                        "arrival_dt": arrival_dt,
                    }
                    try:
                        jobq.put_nowait(job)
                    except asyncio.QueueFull:
                        log.warning("job queue full — dropping %s %s", ticker, art_id)
                        continue

                    # Pre-curl consumer hook (the "arm" moment): fired non-blocking so it
                    # never delays draining the WS receive buffer. No-op if unset.
                    if _alert_hook is not None:
                        try:
                            _alert_hook(ticker, art_id, arrival_dt)
                        except Exception:
                            log.exception("alert hook failed for %s %s", ticker, art_id)

                    # [RecvLag]: gap from the article's own published_at -> our receipt,
                    # paired with the live in-flight fetch count. If recv_lag ramps with
                    # inflight during a burst, OUR loop is saturated; if it stays flat,
                    # the delay is upstream (RTPR's WS push), not us.
                    recv_lag = (arrival_dt - pub_dt).total_seconds()
                    log.info("[RecvLag] %-6s recv_lag=%.3f inflight=%d",
                             ticker, recv_lag, inflight.n)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            consecutive_failures += 1
            # After a run of failures, slow to a 5-min poll to stay off the rate limiter.
            if consecutive_failures >= 5:
                delay = 300
            else:
                delay = backoff
                backoff = min(backoff * 2, 60)
            log.warning("WS disconnected (%s: %s) — reconnect in %ss",
                        type(e).__name__, e, delay)
            await asyncio.sleep(delay)


async def fetch_and_build(job: dict, cfg, session: aiohttp.ClientSession,
                          cpu_executor: ThreadPoolExecutor, inflight: Inflight) -> dict:
    """Fetch the article over the shared session, parse it, return a PR_dataframe row."""
    headers = {"X-API-Key": cfg.api_key, "Accept": "text/html"}
    body_bytes = None
    last_err = None
    t0 = time.perf_counter()

    inflight.n += 1
    inflight_at_start = inflight.n
    try:
        for attempt in range(cfg.max_retries + 1):
            ctx: dict = {}
            a0 = time.perf_counter()
            status = None
            try:
                async with session.get(
                    job["url"], headers=headers, trace_request_ctx=ctx,
                    timeout=aiohttp.ClientTimeout(total=cfg.fetch_timeout),
                ) as resp:
                    status = resp.status
                    if resp.status == 200:
                        body_bytes = await resp.read()
                    else:
                        last_err = f"HTTP {resp.status}"
            except asyncio.CancelledError:
                raise
            except Exception as e:
                status = type(e).__name__
                last_err = f"{type(e).__name__}: {e}"

            # [FetchTrace]: per-attempt connection-stage breakdown + the in-flight count
            # at this fetch's start. ttfb high while dns/queued/connect are ~0 means the
            # round-trip is RTPR server-side; if ttfb rises with inflight it's a per-IP
            # throttle on us (then a small concurrency cap helps — see plan §D).
            stages = _stage_durations(ctx)
            log.info(
                "[FetchTrace] %-6s %s attempt=%d status=%s reused=%s inflight=%d "
                "dns=%s queued=%s connect=%s ttfb=%s body=%s total=%.3f",
                job["symbol"], job["id"], attempt, status, ctx.get("reused", False),
                inflight_at_start, _fmt_s(stages["dns"]), _fmt_s(stages["queued"]),
                _fmt_s(stages["connect"]), _fmt_s(stages["ttfb"]),
                _fmt_s(_dur(ctx, "t_ttfb", "t_end")), time.perf_counter() - a0,
            )

            if body_bytes is not None:
                break
            if attempt < cfg.max_retries:
                await asyncio.sleep(0.15 * (attempt + 1))
    finally:
        inflight.n -= 1

    curl_dt = now_utc()
    if body_bytes is None:
        log.warning("PR %-6s %s FETCH-FAILED (%s)", job["symbol"], job["id"], last_err)
        return build_failed_row(job, curl_dt)

    latency_ms = (time.perf_counter() - t0) * 1000.0
    # Parse off the event loop: lxml releases the GIL during parsing, so this keeps the
    # loop free to stamp ArrivalTime / read WS frames while a burst's bodies are parsed.
    loop = asyncio.get_running_loop()
    try:
        headline, tickers, exchanges, source, body = await loop.run_in_executor(
            cpu_executor, parse_article, body_bytes)
    except Exception as e:
        log.warning("PR %-6s %s PARSE-FAILED: %s", job["symbol"], job["id"], e)
        return build_failed_row(job, curl_dt)

    row = build_row(job, headline, tickers, exchanges, source, body, curl_dt)
    log.info("PR %-6s %s %.0fms created=%s arrival=%s curl=%s tickers=%s",
             job["symbol"], job["id"], latency_ms,
             row["Created"], row["ArrivalTime"], row["CurlTime"], row["Tickers"])
    return row


async def worker(wid: int, cfg, jobq: asyncio.Queue, resultq: asyncio.Queue,
                 session: aiohttp.ClientSession, cpu_executor: ThreadPoolExecutor,
                 inflight: Inflight) -> None:
    """Stateless consumer: pull job -> fetch+parse -> push row. Concurrency == #workers."""
    while True:
        job = await jobq.get()
        try:
            row = await fetch_and_build(job, cfg, session, cpu_executor, inflight)
            # Row-built consumer hook: fired the instant a complete row exists, before
            # the batched store. Non-blocking (the consumer offloads heavy work). No-op
            # if unset; a hook exception never disrupts the writer path.
            if _row_hook is not None:
                try:
                    _row_hook(job, row)
                except Exception:
                    log.exception("row hook failed for %s", job.get("id"))
            await resultq.put(row)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("worker %d unexpected error on %s", wid, job.get("id"))
        finally:
            jobq.task_done()


async def df_writer(cfg, resultq: asyncio.Queue, store: PRStore) -> None:
    """Single writer: drain the result queue and batch rows into the in-memory store."""
    buf: list[dict] = []
    while True:
        try:
            row = await asyncio.wait_for(resultq.get(), timeout=cfg.writer_max_delay)
            buf.append(row)
            resultq.task_done()
            while len(buf) < cfg.writer_batch_size:  # opportunistic drain, no waiting
                try:
                    buf.append(resultq.get_nowait())
                    resultq.task_done()
                except asyncio.QueueEmpty:
                    break
            if len(buf) >= cfg.writer_batch_size:
                store.extend(buf)
                buf = []
        except asyncio.TimeoutError:
            if buf:  # idle flush so low-volume rows still land promptly
                store.extend(buf)
                buf = []
        except asyncio.CancelledError:
            if buf:
                store.extend(buf)
            raise


def seconds_until_next(hour: int, minute: int) -> float:
    now = datetime.now(DISPLAY_TZ)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _write_csv(df: pd.DataFrame, path: str) -> None:
    # pipe-delimited; pandas auto-quotes any field containing '|' or newlines (QUOTE_MINIMAL),
    # reproducing the spec's "[""WTM""]" quoting for the JSON-array columns.
    df.to_csv(path, sep="|", index=False)


async def do_flush(cfg, store: PRStore, seen_ids: set, reason: str) -> None:
    df = store.swap_out()        # atomic swap -> empty
    seen_ids.clear()             # fresh dedup window
    n = len(df)
    if n == 0:
        log.info("flush (%s): nothing to write", reason)
        return
    date_str = datetime.now(DISPLAY_TZ).strftime("%Y-%m-%d")
    path = str(Path(cfg.out_dir) / f"PR_dataframe_{date_str}.csv")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _write_csv, df, path)   # disk write off the event loop
    log.info("flush (%s): wrote %d rows -> %s", reason, n, path)


async def flush_scheduler(cfg, store: PRStore, seen_ids: set) -> None:
    """Sleep until the next HH:MM in DISPLAY_TZ, flush PR_dataframe to ./tables, repeat."""
    while True:
        delay = seconds_until_next(cfg.flush_hour, cfg.flush_minute)
        log.info("next flush in %.0fs (%02d:%02d %s)",
                 delay, cfg.flush_hour, cfg.flush_minute, DISPLAY_TZ.key)
        await asyncio.sleep(delay)
        try:
            await do_flush(cfg, store, seen_ids, reason="scheduled")
        except Exception:
            log.exception("scheduled flush failed")
        await asyncio.sleep(1)  # ensure we move past the trigger minute before recomputing


async def universe_reload_scheduler(cfg) -> None:
    """Daily at cfg.universe_reload_{hour,minute} in DISPLAY_TZ: re-scan the universe
    directory and hot-swap cfg.universe to the newest dated file.

    The directory scan + pandas read run in the default executor so a reload coinciding
    with an alert never blocks the event loop (the same offload do_flush uses for the CSV
    write). Reassigning cfg.universe is atomic w.r.t. ws_listener: both run on the single
    event-loop thread, and a frozenset is an immutable snapshot, so no lock is needed. Any
    failure (file not published yet, unreadable, 0 symbols) keeps the current universe —
    a long-lived daemon must not die on a transient directory state.
    """
    while True:
        delay = seconds_until_next(cfg.universe_reload_hour, cfg.universe_reload_minute)
        log.info("next universe reload in %.0fs (%02d:%02d %s)",
                 delay, cfg.universe_reload_hour, cfg.universe_reload_minute, DISPLAY_TZ.key)
        await asyncio.sleep(delay)
        try:
            loop = asyncio.get_running_loop()
            path = await loop.run_in_executor(None, find_latest_universe, cfg.universe_dir)
            syms = await loop.run_in_executor(None, load_universe, path)
            cfg.universe = syms  # atomic hot-swap (single event-loop thread)
            log.info("universe reloaded: %d symbols from %s", len(syms), path.name)
        except Exception:
            log.exception("universe reload failed — keeping current %d symbols",
                          len(cfg.universe))
        await asyncio.sleep(1)  # move past the trigger minute before recomputing


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------

async def main_async(cfg) -> None:
    jobq: asyncio.Queue = asyncio.Queue(maxsize=cfg.queue_max)
    resultq: asyncio.Queue = asyncio.Queue()
    store = PRStore()
    seen_ids: set = set()
    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    # keepalive_timeout=15 (was 75): drop idle conns before a NAT/LB silently kills them,
    # so the first curl of a between-bursts gap never stalls reusing a dead connection.
    pool = max(100, cfg.workers * 2)
    connector = aiohttp.TCPConnector(
        limit=pool, limit_per_host=pool, ttl_dns_cache=300, keepalive_timeout=15,
    )
    inflight = Inflight()
    cpu_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="rtpr-cpu")
    async with aiohttp.ClientSession(
        connector=connector, trace_configs=[_make_trace_config()],
    ) as session:
        ws_task = asyncio.create_task(ws_listener(cfg, jobq, seen_ids, inflight), name="ws")
        worker_tasks = [
            asyncio.create_task(
                worker(i, cfg, jobq, resultq, session, cpu_executor, inflight), name=f"w{i}")
            for i in range(cfg.workers)
        ]
        writer_task = asyncio.create_task(df_writer(cfg, resultq, store), name="writer")
        flush_task = asyncio.create_task(flush_scheduler(cfg, store, seen_ids), name="flush")
        reload_task = asyncio.create_task(universe_reload_scheduler(cfg), name="ureload")

        log.info("RTPR_connector running: %d symbols, %d workers, flush @ %02d:%02d %s",
                 len(cfg.universe), cfg.workers, cfg.flush_hour, cfg.flush_minute, DISPLAY_TZ.key)

        await stop.wait()
        log.info("shutdown signal — draining in-flight fetches and flushing")

        # 1) stop ingesting new alerts
        ws_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await ws_task
        # 2) let workers finish queued jobs (bounded grace)
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(jobq.join(), timeout=cfg.fetch_timeout + 3)
        # 3) tear down workers, then writer, then flush task — in that order
        for t in worker_tasks:
            t.cancel()
        await asyncio.gather(*worker_tasks, return_exceptions=True)
        cpu_executor.shutdown(wait=True)   # let any in-progress off-loop parse finish
        writer_task.cancel()
        await asyncio.gather(writer_task, return_exceptions=True)
        flush_task.cancel()
        reload_task.cancel()
        await asyncio.gather(flush_task, reload_task, return_exceptions=True)
        # 4) drain any rows still sitting in the result queue, then final flush
        leftover = []
        while True:
            try:
                leftover.append(resultq.get_nowait())
            except asyncio.QueueEmpty:
                break
        store.extend(leftover)
        await do_flush(cfg, store, seen_ids, reason="shutdown")

    log.info("stopped")


# --------------------------------------------------------------------------------------
# Logging — QueueHandler/QueueListener so log I/O never blocks the event loop hot path
# --------------------------------------------------------------------------------------

def setup_logging(log_dir: Path, level: int) -> logging.handlers.QueueListener:
    os.makedirs(log_dir, exist_ok=True)
    logq: _queue.Queue = _queue.Queue(-1)

    log.setLevel(level)
    log.handlers.clear()
    log.addHandler(logging.handlers.QueueHandler(logq))
    log.propagate = False

    fmt = logging.Formatter("%(asctime)s.%(msecs)03d %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.handlers.TimedRotatingFileHandler(
        str(Path(log_dir) / "RTPR_connector.log"), when="midnight", backupCount=14, encoding="utf-8",
    )
    fh.setFormatter(fmt)
    ch = logging.StreamHandler()
    ch.setFormatter(fmt)

    listener = logging.handlers.QueueListener(logq, fh, ch, respect_handler_level=True)
    listener.start()
    return listener


# --------------------------------------------------------------------------------------
# Offline self-test — validates the parser/row builder against the saved probe dump
# --------------------------------------------------------------------------------------

def run_self_test() -> int:
    print(f"[self-test] reading {SELF_TEST_DUMP}")
    try:
        with open(SELF_TEST_DUMP, "rb") as f:
            html = f.read()
    except FileNotFoundError:
        print(f"[self-test] dump not found — run rtpr_probe.py --out /tmp/rtpr_raw first")
        return 2

    frame = {
        "type": "alert",
        "ticker": "WTM",
        "article_published_at": "2026-06-22T22:57:42.252Z",
        "article_url": "https://rtpr.io/a/nPnbmRd0Da?exp=1782169182&sig=deadbeef",
    }
    pub = parse_published_at(frame["article_published_at"])
    job = {
        "symbol": frame["ticker"],
        "id": extract_id(frame["article_url"]),
        "url": frame["article_url"],
        "published_at": pub,
        "arrival_dt": pub,   # deterministic for the test
    }
    headline, tickers, exchanges, source, body = parse_article(html)
    row = build_row(job, headline, tickers, exchanges, source, body, curl_dt=pub)

    expected = {
        "Symbol": "WTM",
        "Tickers": '["WTM"]',
        "ID": "nPnbmRd0Da",
        "ArrivalDate": "2026-06-22",
        "Created": "18:57:42.252",
        "Exchange": '["NYSE"]',
        "Source": "PR Newswire",
    }
    ok = True
    for k, v in expected.items():
        got = row[k]
        good = got == v
        ok &= good
        print(f"  [{'OK ' if good else 'FAIL'}] {k:<11} = {got!r}" + ("" if good else f"  (expected {v!r})"))

    hl_ok = row["Headline"].startswith("Australia")
    body_ok = row["Body"].startswith("Australia")
    cols_ok = list(row.keys()) == COLUMNS
    ok &= hl_ok and body_ok and cols_ok
    print(f"  [{'OK ' if hl_ok else 'FAIL'}] Headline    = {row['Headline'][:64]!r}")
    print(f"  [{'OK ' if body_ok else 'FAIL'}] Body[:40]   = {row['Body'][:40]!r}")
    print(f"  [{'OK ' if cols_ok else 'FAIL'}] column order = {list(row.keys())}")

    # Show the materialized one-row PR_dataframe as it would be written to CSV.
    df = pd.DataFrame([row], columns=COLUMNS)
    print("\n[self-test] PR_dataframe row (pipe-delimited):")
    print(df.to_csv(sep="|", index=False).rstrip())

    print("\n[self-test]", "PASS" if ok else "FAIL")
    return 0 if ok else 1


# --------------------------------------------------------------------------------------
# CLI / entrypoint
# --------------------------------------------------------------------------------------

class Cfg:
    pass


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="RTPR.io alerts WebSocket -> in-memory PR_dataframe connector.")
    ap.add_argument("--universe-dir", default=str(DEFAULT_UNIVERSE_DIR),
                    help="Directory scanned for the newest stocks_universe_YYYY-MM-DD.tsv; "
                         "only the 'Symbol' column is loaded (default: %(default)s)")
    ap.add_argument("--universe-reload-at", default="03:58",
                    help="Daily universe re-scan time HH:MM in DISPLAY_TZ (default: %(default)s)")
    ap.add_argument("--api-keys", default=str(DEFAULT_KEY_FILE),
                    help="File containing the RTPR API key line (default: %(default)s)")
    ap.add_argument("--workers", type=int, default=32,
                    help="Concurrent-fetch ceiling = # async worker coroutines (default: %(default)s)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Daily flush directory (default: %(default)s)")
    ap.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR), help="Log directory (default: %(default)s)")
    ap.add_argument("--flush-at", default="20:35", help="Daily flush time HH:MM in DISPLAY_TZ (default: %(default)s)")
    ap.add_argument("--queue-max", type=int, default=10000, help="Max pending jobs before drop (default: %(default)s)")
    ap.add_argument("--fetch-timeout", type=float, default=20.0,
                    help="Per-fetch timeout seconds. 20s (not 10s) so a slow RTPR TTFB "
                         "isn't killed mid-flight, forcing a retry storm (default: %(default)s)")
    ap.add_argument("--max-retries", type=int, default=0,
                    help="Fetch retries on failure. 0 (single attempt) so retries don't pile "
                         "onto an already-slammed endpoint during a burst (default: %(default)s)")
    ap.add_argument("--log-level", default="INFO", help="Logging level (default: %(default)s)")
    ap.add_argument("--self-test", action="store_true", help="Run the offline parser self-test and exit")
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    if args.self_test:
        return run_self_test()

    cfg = Cfg()
    cfg.universe_dir = Path(args.universe_dir)
    uni_path = find_latest_universe(cfg.universe_dir)   # startup: newest dated file right now
    cfg.universe = load_universe(uni_path)
    cfg.api_key = load_api_key(Path(args.api_keys))
    cfg.workers = max(1, args.workers)
    cfg.out_dir = Path(args.out_dir)
    cfg.log_dir = Path(args.log_dir)
    cfg.queue_max = args.queue_max
    cfg.fetch_timeout = args.fetch_timeout
    cfg.max_retries = max(0, args.max_retries)
    cfg.writer_batch_size = 50
    cfg.writer_max_delay = 0.5
    hh, mm = args.flush_at.split(":")
    cfg.flush_hour, cfg.flush_minute = int(hh), int(mm)
    uhh, umm = args.universe_reload_at.split(":")
    cfg.universe_reload_hour, cfg.universe_reload_minute = int(uhh), int(umm)

    os.makedirs(cfg.out_dir, exist_ok=True)
    listener = setup_logging(cfg.log_dir, getattr(logging, args.log_level.upper(), logging.INFO))

    # uvloop: drop-in faster event loop on Linux; degrade gracefully if unavailable.
    try:
        import uvloop
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        log.info("uvloop enabled")
    except Exception as e:
        log.info("uvloop unavailable (%s) — using default asyncio loop", e)

    log.info("loaded %d universe symbols from %s", len(cfg.universe), uni_path)

    rc = 0
    try:
        asyncio.run(main_async(cfg))
    except KeyboardInterrupt:
        log.info("interrupted")
    except Exception:
        log.exception("fatal error")
        rc = 1
    finally:
        listener.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
