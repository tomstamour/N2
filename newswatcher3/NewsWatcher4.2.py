#!/usr/bin/env python3
"""
NewsWatcher4.2.py
-----------------
Real-time press-release / news monitor using the RTPR.io *alerts* WebSocket
plus a per-article HTTP curl of the signed permalink.

NW4.2 changes vs NW4.1 (surgical, narrow):
  • _fetch_article issues exactly ONE curl attempt — no retries, no backoff.
    A failed curl previously held a fetch-semaphore slot for up to ~40s
    (20s timeout × 2 attempts + 0.5s backoff), starving co-arriving alerts
    during a top-of-hour PR burst.
  • On curl failure, NW4.2 fires the accepted callback with a synthetic
    payload (Headline='FAILED curl') instead of firing alert_release. The
    orchestrator releases the warm client via its excluded-strings path
    (Headline must match an entry in its excluded_strings file) and writes
    an audit row to its news_output_*.tsv so failures are visible alongside
    successes.
  • Constants removed: FETCH_MAX_RETRIES, FETCH_BACKOFF_SEC.
  • Everything else (filter pipeline, normalize, persist, flush, blacklist,
    WS protocol, output formats, public API) is unchanged from NW4.1.

This is the post-licensing-change replacement for NewsWatcher3 (firehose).
RTPR can no longer redistribute article bodies through a raw WebSocket pipe,
so the new flow is:

  1. Connect to wss://ws.rtpr.io/ws-alerts?apiKey=...
     The connection is governed by server-side filter rules created by the
     user on https://rtpr.io/wire.  This file expects a single catch-all
     rule:  `tickers_length gte 1`.
  2. For each {"type":"alert", ...} message the server pushes, GET the
     signed permalink (article_url) with the header X-API-Key.  The HTTP
     response carries the full article JSON.
  3. Normalize the response onto the same dict shape NewsWatcher3 used
     internally (id, ticker, tickers, exchange, title, author, created,
     article_body) and hand it to `_handle_article` — which is byte-identical
     to NW3 and owns dedup, the filter pipeline, blacklist write, and the
     user callback.

Public API is byte-identical to NW3 so callers swap `import NewsWatcher3 as
nw` for `import NewsWatcher4 as nw` and keep working:

    import NewsWatcher4 as nw

    nw.start(
        universe_tsv='./nasdaq_symbols_data.tsv',
        black_list='./black_list.csv',
        blacklist_expiry_hours=168,
        api_keys='./RTPR_API-Key.txt',
    )

    df  = nw.get_news_df()                       # accepted DataFrame
    obj = nw.get_news_object('id-51772090')      # accepted article
    obj = nw.get_blocked_object('id-51772090')   # article blocked by filters
    nw.update_universe(new_list)
    nw.stop()
"""

import asyncio
import html as _htmllib
import json
import logging
import os
import queue
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from urllib.parse import urlparse

try:
    import pandas as pd
except ImportError:
    raise ImportError("pandas is required. Install with: pip install pandas")

try:
    import websockets
except ImportError:
    raise ImportError("websockets is required. Install with: pip install websockets")

try:
    import aiohttp
except ImportError:
    raise ImportError("aiohttp is required. Install with: pip install aiohttp")


# ─── Module-level private state ───────────────────────────────────────────────

_news_df: pd.DataFrame = pd.DataFrame(columns=["ID", "ArrivalTime", "Symbol", "Headline"])
_news_objects: dict = {}             # accepted articles, keyed by 'id-<id>'
_blocked_objects: dict = {}          # articles that did not pass filters, keyed by 'id-<id>'

_blacklist_set: set = set()          # in-memory O(1) lookup
_blacklist_records: list = []        # [{'Symbol': ..., 'Date': 'DD-MM-YYYY', 'ID': ...}, ...]
_universe_set: set = set()           # O(1) ticker membership

_seen_ids: set = set()               # post-normalize dedup, used by _handle_article
_fetched_ids: set = set()            # pre-fetch dedup, key = id extracted from article_url
_rejected_count: int = 0
_rejected_lock = threading.Lock()

_excluded_strings_lower: list = []   # pre-lowercased substrings

_priced_data: dict = {}              # {symbol: {'Float_M': float|None, 'LastDailyClosePrice': float|None}}

_df_lock = threading.Lock()
_objects_lock = threading.Lock()           # protects _news_objects + _seen_ids
_blocked_lock = threading.Lock()           # protects _blocked_objects
_blacklist_lock = threading.Lock()
_universe_lock = threading.Lock()
_priced_lock = threading.Lock()
_fetched_lock = threading.Lock()           # protects _fetched_ids
_shutdown_event = threading.Event()

_background_thread: threading.Thread | None = None
_config: dict = {}

# Dedicated pool for immediate per-article JSON persistence (see
# _persist_article_now). Decouples disk I/O from NW4's single asyncio loop
# thread so writing an accepted article never blocks the next alert's arm. Two
# workers is ample — writes are tiny and the loop only ever submits, never waits.
_persist_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='nw4-persist')

# Dedicated pool for the CPU-bound permalink scrape (_normalize_article). Running
# the regex scrape inline on NW4's single asyncio loop blocked the loop for the
# scrape's full duration per article; during a top-of-hour burst that starved the
# WS reader and inflated ArrivalTime (published-at→recv_ts) to ~2s. Offloading it
# here lets the GIL preempt (~5ms) so the loop keeps stamping recv_ts / firing arms.
# This module-level pool is a placeholder — start() recreates it sized by
# CPU_POOL_WORKERS, and _handle_alert only runs after start(), so the placeholder is
# never used for work (mirrors _persist_executor).
_cpu_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='nw4-cpu')

# Dedicated single-worker pool for downstream callbacks (alert arm, alert release,
# accepted). The orchestrator's arm handler talks to IBKR and can block; if that
# ran on the asyncio loop it stalled in-flight curls and the WS reader. Single
# worker preserves arm→release / arm→accepted ordering for the same art_id (the
# orchestrator's release-only-if-armed invariant depends on it). DO NOT raise
# max_workers > 1. start() recreates a fresh pool; this module-level one is never
# used for real work (mirrors _persist_executor / _cpu_executor).
_callback_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='nw4-callback')

# Dedicated single-worker pool for the periodic _flush. _flush holds several locks
# and does N file writes; running it inline on the asyncio loop blocked the loop
# for the flush's full duration (multi-second during a real session). Single worker
# is enough — flushes run every flush_interval_seconds and the periodic coroutine
# awaits each one, so there's never more than one queued. start() recreates it.
_flush_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='nw4-flush')

_news_callback = None
# Alert-flow callbacks (NW4): fired on the lightweight WS alert, BEFORE the
# permalink curl, so the orchestrator can arm reqMktData without waiting on the
# fetch. _alert_callback fires when the alert's primary ticker passes the cheap
# pre-filter; _alert_release_callback fires when such a pre-armed article is
# later dropped (curl/normalize failure or full-filter block) and was never
# accepted, so the orchestrator can release the warm client it consumed.
_alert_callback = None
_alert_release_callback = None
_callback_lock = threading.Lock()

logger = logging.getLogger("NewsWatcherV4")

# QueueHandler/QueueListener pair: the logger only owns a QueueHandler so every
# logger.xxx() call on the asyncio loop is a non-blocking queue.put_nowait. The
# listener thread (started in _setup_logging) dequeues records and runs the real
# _DailyFileHandler + StreamHandler. Cleared on stop() so a fresh start() can
# rebuild the pipeline.
_log_queue: queue.Queue | None = None
_log_listener: QueueListener | None = None

# ─── NW4 tuning knobs ─────────────────────────────────────────────────────────

MAX_CONCURRENT_FETCHES = 64      # cap concurrent permalink curls (was 8 → 32 → 64; still
                                 # saw a self-draining queue ramp during the 2026-06-15
                                 # 08:00 burst where ~60 curls fired within ~4s. The
                                 # alert-ticker pre-filter in _handle_alert keeps the
                                 # actual fetch volume small; see [Timing] logs below.)
FETCH_TIMEOUT_SEC      = 20.0    # HTTP timeout for the single curl attempt. NW4.2 has no
                                 # retries (see below), so this is also the hard ceiling on
                                 # how long an alert can occupy a fetch-semaphore slot. 20s
                                 # rides out the RTPR top-of-hour TTFB peak (4–8s, occ. >10s
                                 # on fast fresh connections) in one shot; arm already fired
                                 # pre-curl, so only the sentiment confirm waits (clerk holds
                                 # the client ~60s).
# NW4.2: single curl attempt per alert. No retries, no backoff. Rationale: under a top-of-
# hour burst, a single slow RTPR endpoint multiplied by 2 retries + 0.5s backoff occupied a
# semaphore slot for up to ~40s, starving co-arriving alerts. A failed curl now fires the
# accepted callback with Headline='FAILED curl' so the orchestrator releases the warm client
# (via its excluded-strings path) and logs an audit row in news_output_*.tsv.
SLOW_FETCH_LOG_SEC     = 1.0     # alert→body-ready total at/above this logs [Timing] at
                                 # INFO (else DEBUG) — splits semwait / curl / normalize
                                 # so a burst backlog (our queue) is distinguishable from
                                 # RTPR server slowness (curl). See _handle_alert.
HTTP_POOL_LIMIT        = 64      # aiohttp TCPConnector pool size (>= MAX_CONCURRENT_FETCHES)
KEEPALIVE_TIMEOUT_SEC  = 15      # drop idle keepalive conns after 15s (was 300). A NAT/LB
                                 # idle timeout silently kills conns we still think are alive,
                                 # so a burst after a gap reuses a DEAD conn → hangs to the
                                 # fetch timeout (the PRZO 2026-06-17 08:12 case: reused=True
                                 # reached=start). 15s < any NAT idle window, so a reused conn
                                 # is always fresh; within a burst conns are <15s apart anyway.
WS_RECV_TIMEOUT_SEC    = 90      # matches RTPR's 90s pong deadline
CPU_POOL_WORKERS       = 4       # threads for the off-loop HTML scrape (_normalize_article).
                                 # The scrape is GIL-bound (re holds the GIL), so this restores
                                 # event-loop responsiveness via ~5ms GIL preemption rather than
                                 # adding CPU throughput — see _cpu_executor + _handle_alert.
RECV_LAG_WARN_SEC      = 1.0     # [RecvLag] logs at INFO (else DEBUG) when an alert's
                                 # published-at→recv_ts gap reaches this. A burst ramp here that
                                 # tracks len(inflight) means OUR loop is saturated, not RTPR.

# Backoff schedule (seconds) for RTPR auth-class WS close codes (4004 trial-expired,
# 4005 connection-revoked or auth-service-unreachable). Indexed by consecutive
# auth-failure streak; capped at the last value. Counter resets on a successful
# RTPR `alerts connected` handshake.
AUTH_FAILURE_BACKOFF_SEC = [60, 300, 1800, 3600]   # 1 min → 5 min → 30 min → 1 h

RTPR_WS_URL_TEMPLATE = "wss://ws.rtpr.io/ws-alerts?apiKey={key}"

# ─── Centralized per-user config (scripts/config/n2_config_file.txt) ──────────
# NW4 reads its RTPR API key from the shared config via the n2_config parser;
# repo-relative defaults below let a fresh clone run without source edits. The
# orchestrator normally passes explicit paths, overriding these.
import importlib.util as _ilu
_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
_cfg_spec = _ilu.spec_from_file_location("n2_config", _SCRIPTS_DIR / "config" / "n2_config.py")
n2_config = _ilu.module_from_spec(_cfg_spec)
_cfg_spec.loader.exec_module(n2_config)
_DEFAULT_CONFIG_FILE = _SCRIPTS_DIR / "config" / "n2_config_file.txt"


# ─── Public API ───────────────────────────────────────────────────────────────

def start(
    universe_tsv: str = str(_SCRIPTS_DIR / 'universe_finder' / 'data' / 'nasdaq_symbols_data_priced.tsv'),
    black_list: str = str(_SCRIPTS_DIR / 'orchestrator3' / 'black_list.csv'),
    blacklist_expiry_hours: int = 24,
    api_keys: str = str(_DEFAULT_CONFIG_FILE),
    log_dir: str = './logs',
    output_dir: str = './outputs',
    news_df_dir: str = './outputs',
    blocked_dir: str = './blocked_PRs',
    accepted_dir: str = './accepted_PRs',
    excluded_strings_file: str = './excluded_strings.txt',
    excluded_strings: list = None,
    priced_tsv: str | None = None,
    reject_float_greater_then: float = 50,
    reject_price_greater_then: float = 2.00,
    flush_interval_seconds: int = 300,
) -> None:
    """
    Start NewsWatcherV4.

    Loads credentials, universe, blacklist, and excluded-strings, sets up
    logging, and launches a background daemon thread that connects to the
    RTPR.io *alerts* WebSocket and curls per-article permalinks on demand.
    Returns immediately.

    PREREQUISITE: a filter rule must already exist on https://rtpr.io/wire
    (recommended: `tickers_length gte 1` as a catch-all). Without a rule
    the alerts WS connects but emits no `alert` messages.

    Args:
        universe_tsv:           Path to TSV containing the ticker universe.  The
                                first column (header 'Symbol') is read.
        black_list:             CSV path persisting the blacklist (Symbol,Date,ID).
        blacklist_expiry_hours: Entries older than this many hours are purged on
                                load.
        api_keys:               Path to the RTPR API key file (single 'Key:' line).
        log_dir:                Directory for log files.
        output_dir:             Directory where per-symbol (NW2-parity) JSON files
                                are written.
        news_df_dir:            Directory where the daily NewsDF TSV is written.
        blocked_dir:            Directory where articles that did not pass the
                                filter pipeline are written as JSON files on flush.
        accepted_dir:           Directory where articles that passed the filter
                                pipeline are written as JSON files on flush.
        excluded_strings_file:     File containing one excluded substring per line.
                                   Used unless `excluded_strings` is provided.
        excluded_strings:          Explicit list overriding the file.
        priced_tsv:                Path to TSV with Symbol, Float_M, LastDailyClosePrice
                                   columns. When provided, enables the float/price filter.
                                   None disables the filter entirely.
        reject_float_greater_then: Block articles whose ticker has Float_M > this value (M).
        reject_price_greater_then: Block articles whose ticker has LastDailyClosePrice > this.
        flush_interval_seconds:    How often in-memory state is flushed to disk.
    """
    global _background_thread, _config, _persist_executor, _cpu_executor
    global _callback_executor, _flush_executor
    global _news_df, _news_objects, _blocked_objects
    global _blacklist_set, _blacklist_records, _universe_set
    global _seen_ids, _fetched_ids, _shutdown_event, _rejected_count
    global _excluded_strings_lower, _priced_data

    if _background_thread is not None and _background_thread.is_alive():
        raise RuntimeError(
            "NewsWatcherV4 is already running. Call stop() first."
        )

    # Fresh persist pool for this session. stop() shuts the previous one down,
    # and start() can't run while the loop thread is alive, so the old pool is
    # always already drained here. The initial module-level pool has no threads
    # until first submit, so replacing it on first start() is free.
    _persist_executor = ThreadPoolExecutor(
        max_workers=2, thread_name_prefix='nw4-persist'
    )
    # Fresh CPU-scrape pool for this session (same lifecycle as the persist pool).
    _cpu_executor = ThreadPoolExecutor(
        max_workers=CPU_POOL_WORKERS, thread_name_prefix='nw4-cpu'
    )
    # Fresh single-worker callback pool for this session. Single worker preserves
    # arm→release / arm→accepted ordering for the same art_id; do not raise.
    _callback_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix='nw4-callback'
    )
    # Fresh single-worker flush pool for this session.
    _flush_executor = ThreadPoolExecutor(
        max_workers=1, thread_name_prefix='nw4-flush'
    )

    # Reset state (safe — background thread not yet alive)
    _shutdown_event = threading.Event()
    _news_df = pd.DataFrame(columns=["ID", "ArrivalTime", "Symbol", "Headline"])
    _news_objects = {}
    _blocked_objects = {}
    _blacklist_set = set()
    _blacklist_records = []
    _universe_set = set()
    _seen_ids = set()
    _fetched_ids = set()
    _rejected_count = 0
    _excluded_strings_lower = []
    _priced_data = {}

    # Restore today's DataFrame from disk if a prior session already wrote it
    today_str = datetime.now().strftime('%Y-%m-%d')
    existing_tsv = Path(news_df_dir) / f"NewsDF-{today_str}.tsv"
    if existing_tsv.exists():
        try:
            loaded_df = pd.read_csv(existing_tsv, sep='\t')
            if not loaded_df.empty:
                _news_df = loaded_df
                _seen_ids.update(str(x) for x in loaded_df['ID'].tolist())
                logger.info(f"Restored {len(loaded_df)} rows from existing TSV: {existing_tsv}")
        except Exception as e:
            logger.warning(f"Could not load existing TSV {existing_tsv}: {e}")

    _config = {
        'universe_tsv':           universe_tsv,
        'black_list':             black_list,
        'blacklist_expiry_hours': blacklist_expiry_hours,
        'api_keys':               api_keys,
        'log_dir':                log_dir,
        'output_dir':             output_dir,
        'news_df_dir':            news_df_dir,
        'blocked_dir':            blocked_dir,
        'accepted_dir':           accepted_dir,
        'excluded_strings_file':    excluded_strings_file,
        'priced_tsv':               priced_tsv,
        'reject_float_greater_then': reject_float_greater_then,
        'reject_price_greater_then': reject_price_greater_then,
        'flush_interval_seconds':   flush_interval_seconds,
    }

    _setup_logging(log_dir)

    logger.info("=" * 60)
    logger.info("NewsWatcherV4 starting (RTPR alerts WS + permalink fetch)")
    logger.info(f"Universe TSV     : {universe_tsv}")
    logger.info(f"Blacklist expiry : {blacklist_expiry_hours} hours")
    logger.info(f"Flush interval   : {flush_interval_seconds}s")
    logger.info(f"Blocked dir      : {blocked_dir}")
    logger.info(f"Accepted dir     : {accepted_dir}")
    logger.info(f"Fetch concurrency: {MAX_CONCURRENT_FETCHES}  "
                f"(timeout={FETCH_TIMEOUT_SEC}s, single attempt — no retries)")
    if priced_tsv is not None:
        logger.info(f"Priced TSV       : {priced_tsv}")
        logger.info(f"Reject float >   : {reject_float_greater_then}M")
        logger.info(f"Reject price >   : ${reject_price_greater_then}")
    else:
        logger.warning("Priced TSV       : NOT SET — price/float filter DISABLED")
    logger.info("=" * 60)

    _load_universe_tsv(universe_tsv)
    _load_blacklist(black_list, blacklist_expiry_hours)
    if priced_tsv is not None:
        _load_priced_tsv(priced_tsv)

    if excluded_strings is not None:
        _excluded_strings_lower = [s.lower() for s in excluded_strings if s]
        logger.info(f"Excluded strings (override): {len(_excluded_strings_lower)} entries")
    else:
        _load_excluded_strings(excluded_strings_file)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(news_df_dir).mkdir(parents=True, exist_ok=True)
    Path(blocked_dir).mkdir(parents=True, exist_ok=True)
    Path(accepted_dir).mkdir(parents=True, exist_ok=True)

    # Signal handlers — only register from main thread
    def _signal_handler(signum, frame):
        logger.info(f"Signal {signum} received — shutting down...")
        stop()
        raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT,  _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    _background_thread = threading.Thread(
        target=_thread_main,
        daemon=True,
        name="NewsWatcherV4_bg",
    )
    _background_thread.start()
    logger.info("Background thread started — connecting to RTPR.io ws-alerts...")

    import atexit as _atexit
    _atexit.register(stop)


def stop() -> None:
    """
    Graceful shutdown.

    Signals the background thread, waits up to 15 seconds for the final
    flush, then clears in-memory state.
    """
    global _background_thread

    if _background_thread is None or not _background_thread.is_alive():
        logger.warning("stop() called but NewsWatcherV4 is not running.")
        return

    logger.info("Shutdown requested — signalling background thread...")
    _shutdown_event.set()
    _background_thread.join(timeout=15)

    if _background_thread.is_alive():
        logger.warning("Background thread did not exit within 15 s.")
    else:
        logger.info("Background thread exited cleanly.")

    _background_thread = None

    with _df_lock:
        global _news_df
        _news_df = pd.DataFrame(columns=["ID", "ArrivalTime", "Symbol", "Headline"])
    with _objects_lock:
        _news_objects.clear()
        _seen_ids.clear()
    with _fetched_lock:
        _fetched_ids.clear()
    with _blocked_lock:
        _blocked_objects.clear()
    with _blacklist_lock:
        _blacklist_records.clear()

    # Drain any in-flight immediate-persist writes before declaring stopped.
    _persist_executor.shutdown(wait=True)
    # Drain any in-flight off-loop scrapes too. _async_main already gathered the
    # inflight tasks (which own these futures) before the loop thread joined, so
    # these are complete or near-done.
    _cpu_executor.shutdown(wait=True)
    # Drain any queued downstream callbacks. Must come before the log listener
    # so any callback exception log lands in the queue while the listener is
    # still alive to drain it.
    _callback_executor.shutdown(wait=True)
    # Drain any queued periodic flush. In practice there's at most one queued
    # submission and it was either awaited already (loop exited cleanly) or
    # cancelled (loop torn down). Belt-and-suspenders.
    _flush_executor.shutdown(wait=True)

    # Drain logs LAST so every prior step's log records hit disk. QueueListener.stop()
    # enqueues a sentinel, joins the consumer thread, then both handlers it owns
    # (fh + ch) are closed internally — they were never attached to `logger`, so
    # we don't have to detach them here.
    global _log_listener, _log_queue
    if _log_listener is not None:
        _log_listener.stop()
        _log_listener = None
    _log_queue = None
    # The QueueHandler is still attached to `logger` but its target queue is now
    # discarded, so emit() would silently no-op. Re-attach a synchronous
    # StreamHandler so the final "stopped" line still surfaces on stdout for the
    # operator (cheap; one line on shutdown only).
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, QueueHandler)
               for h in logger.handlers):
        _tail = logging.StreamHandler()
        _tail.setLevel(logging.INFO)
        _tail.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        ))
        logger.addHandler(_tail)

    logger.info("NewsWatcherV4 stopped.")


def get_news_df() -> pd.DataFrame:
    """Return a copy of the current accepted news DataFrame (thread-safe)."""
    with _df_lock:
        return _news_df.copy()


def get_news_object(id_str: str) -> dict | None:
    """Return the accepted article dict for an id-key, or None if pruned/absent."""
    with _objects_lock:
        obj = _news_objects.get(id_str)
    if obj is None:
        logger.warning(
            f"get_news_object: '{id_str}' not found in memory — "
            "it may have been flushed and pruned already."
        )
    return obj


def get_blocked_object(id_str: str) -> dict | None:
    """Return the blocked article dict for an id-key, or None if pruned/absent."""
    with _blocked_lock:
        obj = _blocked_objects.get(id_str)
    if obj is None:
        logger.warning(
            f"get_blocked_object: '{id_str}' not found in memory — "
            "it may have been flushed and pruned already."
        )
    return obj


def update_universe(new_list: list) -> None:
    """Replace the in-memory universe set used by the filter pipeline."""
    global _universe_set
    with _universe_lock:
        _universe_set = set(new_list)
    _config['stock_universe'] = list(new_list)
    logger.info(f"Universe updated: {len(new_list)} symbols")


def update_priced_tsv(path: str) -> None:
    """Reload Float/price/exchange data from a new priced TSV. Thread-safe."""
    _load_priced_tsv(path)


def register_callback(fn) -> None:
    """
    Register a callable invoked each time an article passes all filters.

    Called from the background thread with one dict argument:
      {'Symbol': comma-joined tickers, 'ID': ..., 'ArrivalTime': ...,
       'Headline': ..., 'article_body': ..., 'exchange': ...}

    Pass None to deregister.  Exceptions in the callback are caught and
    logged.
    """
    global _news_callback
    with _callback_lock:
        _news_callback = fn
    logger.info(f"Callback registered: {fn}")


def register_alert_callback(fn) -> None:
    """
    Register a callable invoked the instant a WS alert's primary ticker passes
    the cheap pre-filter (in-universe + not-blacklisted + price/float), BEFORE
    the article body is curled. Lets a consumer start time-critical work (e.g.
    the clerk arm / reqMktData) without waiting on the fetch queue.

    Called from the background thread with: fn(ticker: str, art_id: str,
    recv_ts: datetime). Must return quickly (it runs on the asyncio loop thread).
    Pass None to deregister. Exceptions are caught and logged.
    """
    global _alert_callback
    with _callback_lock:
        _alert_callback = fn
    logger.info(f"Alert callback registered: {fn}")


def register_alert_release_callback(fn) -> None:
    """
    Register a callable invoked when an article that already fired the alert
    callback (i.e. was pre-armed) is subsequently dropped before acceptance —
    curl failure, normalize failure, post-curl dedup, or full-filter block — so
    the consumer can release whatever it provisioned on the alert.

    Called with: fn(ticker: str, art_id: str). Pass None to deregister.
    """
    global _alert_release_callback
    with _callback_lock:
        _alert_release_callback = fn
    logger.info(f"Alert-release callback registered: {fn}")


def _log_cb_exception(fut, kind: str, ticker: str, art_id: str) -> None:
    """done_callback for _callback_executor submissions: surfaces callback
    exceptions the same way the old try/except did, without blocking the loop.
    Runs on whatever thread completed the future (the callback worker)."""
    exc = fut.exception()
    if exc is not None:
        logger.error(
            f"Exception in {kind} callback for {ticker} id={art_id}: {exc}",
            exc_info=exc,
        )


def _emit_alert_arm(ticker: str, art_id: str, recv_ts) -> None:
    """Schedule the registered alert callback on _callback_executor. Never blocks.

    The submit returns immediately so the asyncio loop stays free for the curl
    that's about to fire. Exceptions are surfaced via _log_cb_exception. Ordering
    is preserved relative to a later _emit_alert_release / accepted callback for
    the same article because _callback_executor has max_workers=1.
    """
    with _callback_lock:
        cb = _alert_callback
    if cb is None:
        return
    fut = _callback_executor.submit(cb, ticker, art_id, recv_ts)
    fut.add_done_callback(
        lambda f, _t=ticker, _i=art_id: _log_cb_exception(f, "alert", _t, _i)
    )


def _emit_alert_release(ticker: str, art_id: str) -> None:
    """Schedule the registered alert-release callback on _callback_executor.
    Never blocks. See _emit_alert_arm for the ordering guarantee."""
    with _callback_lock:
        cb = _alert_release_callback
    if cb is None:
        return
    fut = _callback_executor.submit(cb, ticker, art_id)
    fut.add_done_callback(
        lambda f, _t=ticker, _i=art_id: _log_cb_exception(f, "alert-release", _t, _i)
    )


# ─── Logging setup ────────────────────────────────────────────────────────────

class _DailyFileHandler(logging.FileHandler):
    """FileHandler that writes to ``NewsWatcher4_<today>.log`` and rolls to a new
    dated file when the local date changes.

    Fixes the long-standing bug where the date was computed once at process start
    and frozen into the filename: a process started yesterday kept appending to
    yesterday's file across midnight, so today's top-of-hour burst landed in
    ``NewsWatcher4_<yesterday>.log`` and was invisible to a ``$(date +%F)`` grep.
    """

    def __init__(self, log_dir: str, prefix: str = "NewsWatcher4", encoding=None):
        self._log_dir = Path(log_dir)
        self._prefix = prefix
        self._cur_date = datetime.now().strftime("%Y-%m-%d")
        super().__init__(self._path_for(self._cur_date), encoding=encoding)

    def _path_for(self, date_str: str) -> str:
        return str(self._log_dir / f"{self._prefix}_{date_str}.log")

    def emit(self, record):
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._cur_date:
            self._cur_date = today
            self.close()
            self.baseFilename = os.path.abspath(self._path_for(today))
            self.stream = self._open()
        super().emit(record)


def _setup_logging(log_dir: str) -> None:
    """Wire NW4.1 logging so the asyncio loop never blocks on a file write.

    The logger itself only holds a QueueHandler — every logger.info/debug call
    on the hot path becomes a queue.put_nowait (microseconds, no I/O). A
    dedicated QueueListener thread dequeues records and runs the real
    _DailyFileHandler + StreamHandler off the loop. respect_handler_level=True
    preserves the per-handler filters we configured (file=DEBUG, console=INFO).
    """
    global _log_queue, _log_listener
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return

    Path(log_dir).mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    fh = _DailyFileHandler(log_dir)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    # Producer side: only the QueueHandler is attached to `logger`. logger.info()
    # becomes a non-blocking enqueue on the asyncio loop thread.
    _log_queue = queue.Queue(-1)
    qh = QueueHandler(_log_queue)
    qh.setLevel(logging.DEBUG)
    logger.addHandler(qh)
    logger.propagate = False    # don't double-log through root

    # Consumer side: dedicated daemon thread runs fh+ch, honoring their levels.
    _log_listener = QueueListener(_log_queue, fh, ch, respect_handler_level=True)
    _log_listener.start()


# ─── Credentials ──────────────────────────────────────────────────────────────

def _load_rtpr_credentials(file_path: str) -> str:
    """
    Parse the RTPR API key from `file_path` using the shared n2_config parser.

    Accepts the central config's ``RTPR_API_KEY:`` label, falling back to the
    legacy ``Key:`` label used by the standalone ``RTPR_API-Key.txt`` — so
    either file works:

        RTPR_API_KEY:                 |   Key:
        rtpr_XXXXXXXXXXXXXXX          |   rtpr_XXXXXXXXXXXXXXX
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"API keys file not found: {file_path}")

    cfg = n2_config.load_config(file_path)
    api_key = cfg.get('RTPR_API_KEY') or cfg.get('Key')

    if not api_key:
        raise ValueError(
            f"Missing 'RTPR_API_KEY:' (or legacy 'Key:') field in {file_path}")
    return api_key


# ─── Universe loader ──────────────────────────────────────────────────────────

def _load_universe_tsv(path: str) -> None:
    """Load Symbol column from the universe TSV into _universe_set."""
    global _universe_set

    p = Path(path)
    if not p.exists():
        logger.error(f"Universe TSV not found: {path} — universe_set will be empty")
        return

    try:
        df = pd.read_csv(p, sep='\t')
    except Exception as e:
        logger.error(f"Failed to read universe TSV {path}: {e}")
        return

    if 'Symbol' in df.columns:
        symbols = df['Symbol'].astype(str).str.strip().tolist()
    else:
        symbols = df.iloc[:, 0].astype(str).str.strip().tolist()

    symbols = [s for s in symbols if s and s.lower() != 'nan']
    with _universe_lock:
        _universe_set = set(symbols)
    logger.info(f"Universe loaded: {len(_universe_set)} symbols from {path}")


# ─── Excluded-strings loader ──────────────────────────────────────────────────

def _load_excluded_strings(path: str) -> None:
    global _excluded_strings_lower

    p = Path(path)
    if not p.exists():
        logger.warning(f"Excluded-strings file not found: {path} — using empty list")
        _excluded_strings_lower = []
        return

    try:
        with open(p, 'r') as f:
            entries = [ln.strip().lower() for ln in f if ln.strip()]
    except Exception as e:
        logger.error(f"Failed to read excluded-strings file {path}: {e}")
        _excluded_strings_lower = []
        return

    _excluded_strings_lower = entries
    logger.info(f"Excluded strings loaded: {len(entries)} entries from {path}")


# ─── Priced-data loader ───────────────────────────────────────────────────────

def _load_priced_tsv(path: str) -> None:
    """Load Symbol, Exchange, Float_M, LastDailyClosePrice from the priced TSV into _priced_data."""
    global _priced_data

    p = Path(path)
    if not p.exists():
        logger.error(f"Priced TSV not found: {path} — priced filter will block all articles")
        return

    try:
        df = pd.read_csv(p, sep='\t', usecols=['Symbol', 'Exchange', 'Float_M', 'LastDailyClosePrice'])
    except ValueError:
        # Exchange column absent in older TSV files — load without it
        try:
            df = pd.read_csv(p, sep='\t', usecols=['Symbol', 'Float_M', 'LastDailyClosePrice'])
        except Exception as e:
            logger.error(f"Failed to read priced TSV {path}: {e}")
            return
    except Exception as e:
        logger.error(f"Failed to read priced TSV {path}: {e}")
        return

    result = {}
    for _, row in df.iterrows():
        sym     = str(row['Symbol']).strip()
        float_m = row['Float_M']             if pd.notna(row['Float_M'])             else None
        price   = row['LastDailyClosePrice'] if pd.notna(row['LastDailyClosePrice']) else None
        exch    = str(row['Exchange']).strip() if 'Exchange' in df.columns and pd.notna(row.get('Exchange')) else None
        result[sym] = {'Float_M': float_m, 'LastDailyClosePrice': price, 'Exchange': exch}

    with _priced_lock:
        _priced_data = result
    logger.info(f"Priced TSV loaded: {len(result)} symbols from {path}")


# ─── Blacklist I/O ────────────────────────────────────────────────────────────

def _load_blacklist(path: str, expiry_hours: int) -> None:
    """
    Load CSV (Symbol,Date,ID), purge entries older than expiry_hours hours,
    store in memory.  Creates an empty file with header if absent.
    Date field may be 'DD-MM-YYYY HH:MM' (preferred) or legacy 'DD-MM-YYYY'
    (treated as midnight of that day).
    """
    p = Path(path)

    if not p.exists():
        logger.info(f"Blacklist file not found — creating empty: {path}")
        _write_blacklist_atomic(path, [])
        return

    now = datetime.now()
    surviving = []
    purged = 0

    DATETIME_FORMATS = ['%d-%m-%Y %H:%M']
    DATE_FORMATS     = ['%d-%m-%Y', '%Y-%m-%d', '%m-%d-%Y', '%d/%m/%Y']

    try:
        with open(p, 'r') as f:
            lines = f.readlines()

        if not lines:
            logger.info("Blacklist file is empty.")
            return

        data_lines = [l.strip() for l in lines[1:] if l.strip()]

        for line in data_lines:
            parts = line.split(',')
            if len(parts) < 2:
                continue
            symbol, date_str = parts[0].strip(), parts[1].strip()
            id_val = parts[2].strip() if len(parts) >= 3 else 'NA'

            entry_dt = None
            for fmt in DATETIME_FORMATS:
                try:
                    entry_dt = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if entry_dt is None:
                for fmt in DATE_FORMATS:
                    try:
                        entry_dt = datetime.strptime(date_str, fmt).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        break
                    except ValueError:
                        continue

            if entry_dt is None:
                logger.warning(f"Unparseable blacklist date '{date_str}' for {symbol} — skipping")
                purged += 1
                continue

            age_hours = (now - entry_dt).total_seconds() / 3600
            if age_hours >= expiry_hours:
                purged += 1
                logger.debug(f"Purging blacklist entry: {symbol} ({date_str}, {age_hours:.1f}h old)")
            else:
                surviving.append({'Symbol': symbol, 'Date': date_str, 'ID': id_val})

        if data_lines and not surviving and purged == 0:
            logger.warning(
                f"Blacklist file has {len(data_lines)} line(s) but none could be parsed."
            )

    except Exception as e:
        logger.error(f"Error reading blacklist file: {e}")
        return

    with _blacklist_lock:
        _blacklist_records.clear()
        _blacklist_records.extend(surviving)
        _blacklist_set.clear()
        _blacklist_set.update(r['Symbol'] for r in surviving)

    logger.info(
        f"Blacklist loaded: {len(surviving)} active entries, "
        f"{purged} purged (expiry={expiry_hours}h)"
    )
    if surviving:
        logger.debug(f"Active blacklist: {[r['Symbol'] for r in surviving]}")


def _write_blacklist_atomic(path: str, records: list) -> None:
    """Atomic write (temp + os.replace) of the blacklist CSV."""
    temp_path = path + ".tmp"
    try:
        with open(temp_path, 'w') as f:
            f.write("Symbol,Date,ID\n")
            for r in records:
                f.write(f"{r['Symbol']},{r['Date']},{r.get('ID', 'NA')}\n")
        os.replace(temp_path, path)
        logger.debug(f"Blacklist written: {len(records)} entries → {path}")
    except Exception as e:
        logger.error(f"Error writing blacklist: {e}")


# ─── Per-article JSON writer ──────────────────────────────────────────────────

def _safe_filename_part(s: str) -> str:
    """Strip filesystem-hostile characters from a filename fragment."""
    if not s:
        return 'UNK'
    return ''.join(c if c.isalnum() or c in (',', '-', '_') else '_' for c in s)


def _write_article_json(directory: str, obj: dict, today_str: str) -> str | None:
    """
    Write one article dict to `{directory}/{id}-{ticker}-YYYY-MM-DD.json`
    atomically.  Returns the path written, or None on error.
    """
    article_id = obj.get('id', 'NOID')
    ticker = obj.get('ticker') or 'UNK'
    fname = f"{_safe_filename_part(str(article_id))}-{_safe_filename_part(str(ticker))}-{today_str}.json"
    final_path = Path(directory) / fname
    temp_path = final_path.with_suffix('.json.tmp')
    try:
        with open(temp_path, 'w') as f:
            json.dump(obj, f, indent=2, default=str)
        os.replace(temp_path, final_path)
        return str(final_path)
    except Exception as e:
        logger.error(f"Error writing article JSON {final_path}: {e}")
        return None


def _persist_article_now(obj: dict) -> None:
    """Write one accepted article's per-article JSON (accepted_dir) and per-symbol
    JSON (output_dir) to disk IMMEDIATELY, off NW4's asyncio loop thread.

    Without this, an accepted article only reaches disk at the next periodic
    _flush() (default hourly), so a lookup in between finds nothing and a crash
    before the flush loses up to a full interval of accepted articles. This makes
    each article durable within milliseconds of acceptance.

    Filenames mirror _flush() steps 4 & 5 exactly (same `obj`, same date basis),
    so the later flush simply overwrites these files instead of duplicating them —
    the flush remains the idempotent catch-all/backstop. Atomic temp+replace
    avoids leaving a partial file if the process dies mid-write. Never raises:
    runs on the persist pool, so a failure here can't touch the news pipeline."""
    today_str = datetime.now().strftime('%Y-%m-%d')
    try:
        # 1. Per-article JSON → accepted_dir (atomic, via shared helper)
        _write_article_json(_config['accepted_dir'], obj, today_str)

        # 2. Per-symbol JSON → output_dir (NW2 parity; comma-joined tickers)
        tickers = obj.get('tickers') or []
        symbol_str = ','.join(tickers) if tickers else (obj.get('ticker') or 'UNK')
        symbol_str = _safe_filename_part(symbol_str)
        final_path = Path(_config['output_dir']) / f"{symbol_str}-{today_str}.json"
        temp_path = final_path.with_suffix('.json.tmp')
        with open(temp_path, 'w') as f:
            json.dump(obj, f, indent=2, default=str)
        os.replace(temp_path, final_path)
    except Exception as e:
        logger.error(
            f"Immediate persist failed for id={obj.get('id')}: {e}", exc_info=True
        )


# ─── Periodic flush ───────────────────────────────────────────────────────────

def _purge_blacklist_in_memory() -> int:
    """Remove expired entries from the in-memory blacklist without re-reading disk.
    Returns the number of entries purged."""
    expiry_hours = _config.get('blacklist_expiry_hours', 24)
    now = datetime.now()

    DATETIME_FORMATS = ['%d-%m-%Y %H:%M']
    DATE_FORMATS     = ['%d-%m-%Y', '%Y-%m-%d', '%m-%d-%Y', '%d/%m/%Y']

    surviving = []
    purged = 0

    with _blacklist_lock:
        for r in _blacklist_records:
            date_str = r['Date']
            entry_dt = None

            for fmt in DATETIME_FORMATS:
                try:
                    entry_dt = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if entry_dt is None:
                for fmt in DATE_FORMATS:
                    try:
                        entry_dt = datetime.strptime(date_str, fmt).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        break
                    except ValueError:
                        continue

            if entry_dt is None:
                surviving.append(r)
                continue

            age_hours = (now - entry_dt).total_seconds() / 3600
            if age_hours >= expiry_hours:
                purged += 1
                logger.debug(
                    f"Mid-session purge: {r['Symbol']} ({date_str}, {age_hours:.1f}h old)"
                )
            else:
                surviving.append(r)

        _blacklist_records.clear()
        _blacklist_records.extend(surviving)
        _blacklist_set.clear()
        _blacklist_set.update(r['Symbol'] for r in surviving)

    if purged:
        logger.info(
            f"Mid-session blacklist purge: {purged} expired entries removed "
            f"(expiry={expiry_hours}h, {len(surviving)} remaining)"
        )
    return purged


def _flush(final: bool = False) -> None:
    """
    Flush in-memory state to disk:
      1. Expire stale blacklist entries (in-memory purge)
      2. Blacklist CSV
      3. Per-article JSON → blocked_PRs/
      4. Per-article JSON → accepted_PRs/
      5. Per-symbol JSON → output_dir/ (NW2 parity)
      6. NewsDF TSV → news_df_dir/
      7. Prune _blocked_objects + _news_objects, clear _seen_ids + _fetched_ids
    """
    label = "Final flush" if final else "Periodic flush"
    logger.info(f"{label} starting...")

    today_str = datetime.now().strftime('%Y-%m-%d')

    # 1. Mid-session blacklist expiry purge
    _purge_blacklist_in_memory()

    # 2. Blacklist CSV
    with _blacklist_lock:
        records_snapshot = list(_blacklist_records)
    _write_blacklist_atomic(_config['black_list'], records_snapshot)

    # 3. Blocked per-article JSON
    with _blocked_lock:
        blocked_snapshot = dict(_blocked_objects)
    blocked_written = 0
    for key, obj in blocked_snapshot.items():
        if _write_article_json(_config['blocked_dir'], obj, today_str):
            blocked_written += 1

    # 4. Accepted per-article JSON
    with _objects_lock:
        accepted_snapshot = dict(_news_objects)
    accepted_written = 0
    for key, obj in accepted_snapshot.items():
        if _write_article_json(_config['accepted_dir'], obj, today_str):
            accepted_written += 1

    # 5. Per-symbol JSON (NW2 parity) — comma-joined tickers in filename
    output_dir = _config['output_dir']
    per_symbol_written = 0
    for key, obj in accepted_snapshot.items():
        tickers = obj.get('tickers') or []
        if not tickers:
            primary = obj.get('ticker') or 'UNK'
            symbol_str = primary
        else:
            symbol_str = ','.join(tickers)
        symbol_str = _safe_filename_part(symbol_str)
        filepath = Path(output_dir) / f"{symbol_str}-{today_str}.json"
        try:
            with open(filepath, 'w') as f:
                json.dump(obj, f, indent=2, default=str)
            per_symbol_written += 1
        except Exception as e:
            logger.error(f"Error writing per-symbol JSON for {key}: {e}")

    # 6. News DataFrame TSV
    news_df_dir = _config['news_df_dir']
    df_path = Path(news_df_dir) / f"NewsDF-{today_str}.tsv"
    with _df_lock:
        df_snapshot = _news_df.copy()
    try:
        df_snapshot.to_csv(df_path, sep='\t', index=False)
        logger.debug(f"News DataFrame written: {len(df_snapshot)} rows → {df_path}")
    except Exception as e:
        logger.error(f"Error writing news DataFrame TSV: {e}")

    # 7. Prune memory
    with _blocked_lock:
        for key in blocked_snapshot:
            _blocked_objects.pop(key, None)
    with _objects_lock:
        for key in accepted_snapshot:
            _news_objects.pop(key, None)
        _seen_ids.clear()
    with _fetched_lock:
        _fetched_ids.clear()

    logger.info(
        f"{label} complete: {blocked_written} blocked, "
        f"{accepted_written} accepted, {per_symbol_written} per-symbol JSONs, "
        f"{len(df_snapshot)} DataFrame rows."
    )


# ─── Filter pipeline ──────────────────────────────────────────────────────────

def _passes_filters(tickers: list, title: str, article_exchange: str = '') -> tuple[bool, str]:
    """
    Filter pipeline. Returns (passed, reason_if_failed).

      1. len(tickers) <= 2
      2. >= 1 ticker in universe
      3. no ticker in blacklist
      4. no excluded substring in title (case-insensitive)
      5. in-exchange tickers pass Float_M <= reject_float_greater_then and
         LastDailyClosePrice <= reject_price_greater_then (skipped if priced_tsv=None).
         Tickers absent from priced data or listed on a different exchange than
         article_exchange are skipped rather than failed.
    """
    n = len(tickers)
    if n == 0:
        return False, "tickers list is empty"
    if n > 2:
        return False, f"tickers count={n} > 2"

    with _universe_lock:
        in_universe = any(t in _universe_set for t in tickers)
    if not in_universe:
        return False, f"no ticker in universe (tickers={tickers})"

    with _blacklist_lock:
        for t in tickers:
            if t in _blacklist_set:
                return False, f"ticker '{t}' is blacklisted"

    title_lower = title.lower()
    for excl in _excluded_strings_lower:
        if excl in title_lower:
            return False, f"headline contains excluded string '{excl}'"

    with _priced_lock:
        priced_snapshot = _priced_data
    if priced_snapshot:
        reject_float = _config.get('reject_float_greater_then')
        reject_price = _config.get('reject_price_greater_then')
        for t in tickers:
            entry = priced_snapshot.get(t)
            if entry is None:
                continue  # not in our DB — likely a foreign cross-listing; skip
            priced_exch = entry.get('Exchange')
            if article_exchange and priced_exch and priced_exch != article_exchange:
                continue  # different exchange than the article's primary exchange; skip
            float_m = entry['Float_M']
            price   = entry['LastDailyClosePrice']
            # Check each threshold against its own field independently. A blank
            # field (warrant `KIDZW` with no last close, or a finviz-float row
            # whose price enrichment was skipped → `skipped_nan`) skips only that
            # one check. It must NOT let a valid, disqualifying Float_M through:
            # NEE (Float_M=2080, blank LastDailyClosePrice) was previously armed
            # because a None price short-circuited the float check below.
            if reject_float is not None and float_m is not None and float_m > reject_float:
                return False, f"ticker '{t}' float={float_m}M > {reject_float}M"
            if reject_price is not None and price is not None and price > reject_price:
                return False, f"ticker '{t}' price={price} > {reject_price}"

    return True, ''


# ─── Alert-flow helpers (new in NW4) ──────────────────────────────────────────

def _extract_article_id_from_url(url: str) -> str | None:
    """
    Extract the stable RTPR article id from a permalink like
        https://rtpr.io/a/lseg_n123?exp=...&sig=...
    Returns 'lseg_n123' or None if the URL cannot be parsed.

    Used as a pre-fetch dedup key so multiple rule matches against the same
    article never trigger duplicate HTTP curls.
    """
    if not url:
        return None
    try:
        path = urlparse(url).path  # '/a/lseg_n123'
        parts = [p for p in path.split('/') if p]
        if len(parts) >= 2 and parts[0] == 'a':
            return parts[1]
        return parts[-1] if parts else None
    except Exception:
        return None


# ─── Fetch tracing (per-attempt connection-lifecycle decomposition) ───────────
# An aiohttp TraceConfig stamps timestamps into the per-request dict passed via
# `trace_request_ctx`, so a [FetchTrace] line — and crucially a timeout — can say
# exactly where the time went: DNS, pool-wait (queued), TCP+TLS connect, or TTFB.
# This is the signal the opaque `curl=` number (and the empty-message
# asyncio.TimeoutError) could never provide.

def _make_trace_config() -> aiohttp.TraceConfig:
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


# Ordered (timestamp-key, label) pairs describing the request lifecycle; used to
# render durations and to name the last stage a (timed-out) attempt reached.
_TRACE_STAGES = [
    ("t_start", "start"),
    ("t_dns_start", "dns_start"),
    ("t_dns_end", "dns_end"),
    ("t_queued_start", "queued_start"),
    ("t_queued_end", "queued_end"),
    ("t_create_start", "connect_start"),
    ("t_create_end", "connect_end"),
    ("t_ttfb", "ttfb"),
    ("t_end", "end"),
]


def _last_stage(ctx: dict) -> str:
    """Name of the last lifecycle stage whose timestamp was stamped — on a timeout
    this is *where* the attempt stalled (e.g. 'connect_start' = died mid TCP/TLS)."""
    last = "none"
    for key, label in _TRACE_STAGES:
        if ctx.get(key) is not None:
            last = label
    return last


def _format_fetch_trace(ctx: dict) -> str:
    """Render per-stage durations from a trace_request_ctx dict. Missing stages
    print '—' (e.g. a reused keepalive conn has no dns/connect)."""
    def _dur(a, b):
        ta, tb = ctx.get(a), ctx.get(b)
        return f"{tb - ta:.3f}" if (ta is not None and tb is not None) else "—"

    start = ctx.get("t_start")
    ttfb = ctx.get("t_ttfb")
    ttfb_s = f"{ttfb - start:.3f}" if (start is not None and ttfb is not None) else "—"
    return (
        f"dns={_dur('t_dns_start', 't_dns_end')} "
        f"queued={_dur('t_queued_start', 't_queued_end')} "
        f"connect={_dur('t_create_start', 't_create_end')} "
        f"ttfb={ttfb_s} "
        f"reused={ctx.get('reused', False)} "
        f"reached={_last_stage(ctx)}"
    )


def _log_fetch_trace(art_id, ticker, status, ctx, elapsed, *,
                     force_info=False):
    """Emit one [FetchTrace] line for the single fetch attempt. INFO when slow
    or non-200 (so bursts/timeouts surface on console + file); DEBUG for fast
    successes (still captured in the DEBUG-level file). NW4.2 has no retries,
    so there is no `attempt=N/M` field."""
    msg = (
        f"[FetchTrace] id={art_id} ticker={ticker} status={status} "
        f"elapsed={elapsed:.3f}s {_format_fetch_trace(ctx)}"
    )
    (logger.info if (force_info or elapsed >= SLOW_FETCH_LOG_SEC)
     else logger.debug)(msg)


async def _fetch_article(session: aiohttp.ClientSession, url: str,
                         api_key: str, *, art_id: str | None = None,
                         ticker: str | None = None) -> str | None:
    """
    GET the signed article permalink with X-API-Key.  Returns the raw
    response body as text (HTML) on success, None on failure.

    Per probe on 2026-05-25, RTPR's permalink endpoint always returns
    `text/html; charset=utf-8` regardless of Accept header / format query
    / X-Requested-With, so the response is the article HTML page that
    `_normalize_article` will scrape.  The Accept header is left at
    `application/json` for forward-compatibility in case RTPR ever
    exposes a JSON variant.

    NW4.2: one attempt only — no retries, no backoff. Any non-200, transient
    network error, or timeout returns None immediately. _handle_alert turns
    that None into a synthetic accepted callback (Headline='FAILED curl') so
    the orchestrator releases its pre-armed warm client and writes a TSV
    audit row.
    """
    headers = {'X-API-Key': api_key, 'Accept': 'application/json'}
    ctx: dict = {}
    attempt_t0 = time.monotonic()
    try:
        timeout = aiohttp.ClientTimeout(total=FETCH_TIMEOUT_SEC)
        async with session.get(url, headers=headers, timeout=timeout,
                               trace_request_ctx=ctx) as resp:
            if resp.status == 200:
                text = await resp.text()
                _log_fetch_trace(art_id, ticker, "200", ctx,
                                 time.monotonic() - attempt_t0)
                return text
            if resp.status in (401, 403, 404):
                body = (await resp.text())[:200]
                logger.error(
                    f"_fetch_article: HTTP {resp.status} "
                    f"url={url[:120]} body={body}"
                )
            else:
                logger.warning(
                    f"_fetch_article: HTTP {resp.status} url={url[:120]}"
                )
            _log_fetch_trace(art_id, ticker, str(resp.status), ctx,
                             time.monotonic() - attempt_t0, force_info=True)
            return None
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.warning(
            f"_fetch_article: transient error url={url[:120]}: {exc}"
        )
        _log_fetch_trace(art_id, ticker, type(exc).__name__, ctx,
                         time.monotonic() - attempt_t0, force_info=True)
        return None


# ─── HTML scrape regexes for _normalize_article ───────────────────────────────
# Probe on 2026-05-25 confirmed the RTPR permalink returns a small static HTML
# page (no __NEXT_DATA__, no JSON-LD, no RSC, no OpenGraph) with named CSS
# classes for every NW3 dict field.  These regexes anchor on those class names
# with word boundaries so class-list shuffling can't cause cross-matches.
_RX_H1_TITLE  = re.compile(
    r'<h1[^>]*class="[^"]*\btitle\b[^"]*"[^>]*>(.*?)</h1>',
    re.I | re.S,
)
_RX_TITLE_TAG = re.compile(r'<title[^>]*>([^<]*)</title>', re.I)
_RX_AUTHOR    = re.compile(
    r'<span[^>]*class="[^"]*\bmeta-source\b[^"]*"[^>]*>([^<]*)</span>',
    re.I,
)
_RX_TIME      = re.compile(r'<time[^>]*datetime="([^"]+)"', re.I)
_RX_TICKER    = re.compile(
    r'<span[^>]*class="[^"]*\bticker\b[^"]*"[^>]*>([^<]*)</span>',
    re.I,
)
_RX_EXCHANGE  = re.compile(
    r'<span[^>]*class="[^"]*\bexchange\b[^"]*"[^>]*>([^<]*)</span>',
    re.I,
)
_RX_BODY      = re.compile(
    r'<div[^>]*class="[^"]*\barticle-body\b[^"]*"[^>]*>(.*?)</div>',
    re.I | re.S,
)
_RX_ART_ID    = re.compile(r'Article ID:\s*<code[^>]*>([^<]+)</code>', re.I)
_TITLE_SUFFIX = ' — RTPR'  # em-dash + " RTPR" appended to <title>


def _normalize_article(html: str, alert: dict, fallback_id: str | None) -> dict | None:
    """
    Scrape RTPR's article HTML permalink response into the NW3-style article
    dict that `_handle_article` expects:

      {id, ticker, tickers, exchange, title, author, created, article_body}

    The RTPR permalink endpoint (https://rtpr.io/a/<id>?exp=…&sig=…) returns
    a static HTML page with one named CSS class per field (probed 2026-05-25):

      title         ← <h1 class="title">…</h1>  (fallback: <title>… — RTPR</title>)
      id            ← <code> inside "Article ID: <code>…</code>"  (fallback: fallback_id from URL slug)
      author        ← <span class="meta-source">…</span>
      created       ← <time datetime="…">       (fallback: alert['article_published_at'])
      tickers       ← every <span class="ticker">…</span>          (fallback: [alert['ticker']])
      exchange      ← first <span class="exchange">…</span>
      article_body  ← <div class="article-body">…</div> (html-entity-decoded)

    Returns None when the page is missing both id and title — those pages are
    treated as un-parseable and dropped at the call site.

    `_raw_keys` is a fixed string identifying the scrape strategy ("html-css-
    class-scrape") so log greps can detect drift if a fallback strategy is ever
    added.
    """
    if not isinstance(html, str) or not html:
        return None

    # title — prefer <h1 class="title">, fall back to <title>… — RTPR</title>
    m = _RX_H1_TITLE.search(html)
    title = _htmllib.unescape(m.group(1).strip()) if m else ''
    if not title:
        m = _RX_TITLE_TAG.search(html)
        if m:
            t = _htmllib.unescape(m.group(1).strip())
            title = t[: -len(_TITLE_SUFFIX)] if t.endswith(_TITLE_SUFFIX) else t

    # id — prefer the footer "Article ID: <code>…</code>", fall back to URL slug
    m = _RX_ART_ID.search(html)
    art_id = m.group(1).strip() if m else (fallback_id or '')

    # author / created / article_body
    m = _RX_AUTHOR.search(html)
    author = _htmllib.unescape(m.group(1).strip()) if m else ''

    m = _RX_TIME.search(html)
    created = m.group(1).strip() if m else (alert.get('article_published_at') or '')

    m = _RX_BODY.search(html)
    article_body = _htmllib.unescape(m.group(1)).strip() if m else ''

    # tickers list (post-curl) + primary ticker (from the alert envelope)
    tickers = [
        _htmllib.unescape(t.strip())
        for t in _RX_TICKER.findall(html)
        if t.strip()
    ]
    if not tickers and alert.get('ticker'):
        tickers = [alert['ticker']]
    primary_ticker = alert.get('ticker') or (tickers[0] if tickers else None)

    # exchange — keep only the first match, matching NW3's single-string semantics
    exchanges = [
        _htmllib.unescape(e.strip())
        for e in _RX_EXCHANGE.findall(html)
        if e.strip()
    ]
    exchange = exchanges[0] if exchanges else ''

    # An RTPR page that produced neither id nor title is unrecoverable.
    if not art_id or not title:
        logger.warning(
            f"_normalize_article: scrape failed (id={art_id!r} title={title!r}) "
            f"for alert ticker={alert.get('ticker')!r}; first 200 chars: {html[:200]!r}"
        )
        return None

    return {
        'id':           str(art_id),
        'ticker':       primary_ticker,
        'tickers':      tickers,
        'exchange':     exchange,
        'title':        title,
        'author':       author,
        'created':      created,
        'article_body': article_body,
        # Debug carry-throughs:
        '_alert':       alert,
        '_raw_keys':    'html-css-class-scrape',
    }


async def _handle_alert(
    alert: dict,
    session: aiohttp.ClientSession,
    api_key: str,
    sem: asyncio.Semaphore,
    recv_ts=None,
) -> None:
    """
    End-to-end pipeline for one alert message:

      1. Extract article id from article_url (pre-fetch dedup key).
      2. Pre-fetch dedup — return if the same article was already curled in
         this flush window (e.g. matched multiple rules).
      3. Acquire bounded semaphore and curl the signed permalink.
      4. Normalize to the NW3 dict shape.
      5. Hand off to _handle_article — universe check + full filter pipeline
         + callback dispatch happens there on the complete ticker list.

    NOTE: NW4 deliberately does NOT pre-filter on alert.ticker.  The alert
    payload only carries a single primary ticker, but the full article may
    list a partner ticker that IS in our universe (e.g. an LSE-listed PR
    that also tags a NASDAQ ADR).  NW3 / Orchestrator3.3 observed this
    behavior — articles from LSE/TSX/NYSE AMERICAN/etc. land in the TSV
    when a partner ticker is in our universe TSV.  We mirror that here by
    always fetching and letting `_passes_filters` decide on the full
    tickers list.
    """
    article_url = alert.get('article_url')
    if not article_url:
        logger.debug(f"Alert without article_url: {str(alert)[:200]}")
        return

    art_id = _extract_article_id_from_url(article_url)
    if art_id is None:
        logger.warning(f"Could not extract article id from url={article_url}")
        # Still proceed — _handle_article will dedup on the payload's id.

    if art_id is not None:
        with _fetched_lock:
            if art_id in _fetched_ids:
                logger.debug(f"Pre-fetch dedup hit: {art_id}")
                return
            _fetched_ids.add(art_id)

    # Cheap pre-filter on the alert's primary ticker (the alert carries a single
    # `ticker`; the full list is only known post-curl). Reuses _passes_filters
    # with an empty title/exchange so it evaluates exactly universe + blacklist +
    # price/float. Only gate-passers are curled AND armed — this both slashes the
    # fetch volume during top-of-hour bursts and bounds how many warm clients we
    # consume. Trade-off: an article whose ONLY in-universe ticker is a body-only
    # partner not named in the alert is dropped here (see _normalize_article).
    primary = alert.get('ticker')
    gate_ok, gate_reason = (
        _passes_filters([primary], '', '') if primary
        else (False, 'alert has no ticker')
    )
    if not gate_ok:
        logger.debug(f"Pre-filter drop id={art_id} ticker={primary}: {gate_reason}")
        return

    # Passed the gate → arm now (pre-fetch), then curl the body. Only pre-arm
    # when we have a stable art_id to key the consumer's stash on (it is the same
    # id surfaced on the accepted payload); art_id is virtually always present,
    # but if it's missing we skip the pre-arm and let the post-curl path arm.
    prearmed = None
    if art_id is not None:
        prearmed = primary
        _emit_alert_arm(prearmed, art_id, recv_ts)

    # Per-stage timing so a top-of-hour burst is diagnosable: semwait = time spent
    # waiting for a free fetch slot (OUR local queue), curl = the RTPR round-trip
    # (single attempt; NW4.2 has no retries), normalize = local HTML scrape. The
    # single TSV "CurlTime" number can't separate these; the [Timing] line below can.
    t0 = time.monotonic()
    async with sem:
        t1 = time.monotonic()                      # semaphore acquired
        raw = await _fetch_article(session, article_url, api_key,
                                   art_id=art_id, ticker=primary)
    t2 = time.monotonic()                          # curl returned
    if raw is None:
        logger.warning(
            f"Fetch failed for article id={art_id} url={article_url[:120]}"
        )
        logger.debug(
            f"[Timing] id={art_id} ticker={primary} semwait={t1 - t0:.3f}s "
            f"curl={t2 - t1:.3f}s (fetch-failed)"
        )
        # NW4.2: synthetic accepted callback with Headline='FAILED curl'. The
        # orchestrator's excluded-strings path (Headline must be in its
        # excluded_strings file) releases the warm client via BAD sentiment and
        # writes an audit row to news_output_*.tsv. This replaces the bare
        # _emit_alert_release path NW4.1 used here — release still happens, but
        # downstream now also gets a TSV trace. Routed directly to the callback
        # (not through _handle_article) so we don't auto-blacklist `primary`,
        # store a fake article in _news_objects, or persist a fake JSON.
        with _callback_lock:
            cb = _news_callback
        if cb is not None:
            synthetic_id = f"id-{art_id}" if art_id else f"id-{int(time.time() * 1000)}"
            synthetic = {
                'Symbol':       primary,
                'ID':           synthetic_id,
                'ArrivalTime':  recv_ts or datetime.now(),
                'Headline':     'FAILED curl',
                'article_body': '',
                'exchange':     '',
                'prearmed':     [primary] if prearmed else [],
                'art_id':       art_id,
            }
            fut = _callback_executor.submit(cb, synthetic)
            fut.add_done_callback(
                lambda f, _i=synthetic_id: _log_cb_exception(f, "accepted", "-", _i)
            )
        return

    # Offload the synchronous regex scrape to the CPU pool so it never blocks the
    # asyncio loop — that inline block was what starved the WS reader and inflated
    # ArrivalTime during bursts. run_in_executor takes positional args only, so
    # art_id maps to _normalize_article's 3rd param (fallback_id). t3 now also
    # captures any time this scrape spent queued behind the pool's other workers.
    loop = asyncio.get_running_loop()
    data = await loop.run_in_executor(_cpu_executor, _normalize_article, raw, alert, art_id)
    t3 = time.monotonic()                          # normalize done
    semwait, curl, normalize, total = t1 - t0, t2 - t1, t3 - t2, t3 - t0
    timing_msg = (
        f"[Timing] id={art_id} ticker={primary} semwait={semwait:.3f}s "
        f"curl={curl:.3f}s normalize={normalize:.3f}s total={total:.3f}s"
    )
    # Always INFO: during a burst we want every row's alert→body-ready breakdown,
    # paired with the per-attempt [FetchTrace] lines, captured on console + file.
    logger.info(timing_msg)
    if data is None or not data.get('id'):
        # _normalize_article already logged the scrape failure; just drop.
        _emit_alert_release(prearmed, art_id)
        return

    await _handle_article(data, recv_ts=recv_ts, prearmed=prearmed, art_id=art_id)


# ─── Article handler (unchanged from NW3) ─────────────────────────────────────

async def _handle_article(data: dict, recv_ts=None, prearmed=None, art_id=None) -> None:
    """Process one normalized article dict (post-curl + post-normalize).

    recv_ts  — when the WS alert was received (stamped in _ws_loop). Used as
               ArrivalTime so latency reflects reception, not fetch completion.
    prearmed — the ticker armed pre-fetch in _handle_alert (or None). If this
               article is dropped (dedup/block) it is released via the
               alert-release callback so the consumer can free its client.
    art_id   — URL-slug id, the key the consumer armed under (passed back on
               both the accepted payload and any release)."""
    global _news_df, _rejected_count

    news_id = data.get('id')
    if news_id is None:
        logger.debug("Article without id — skipping")
        return
    news_id = str(news_id)

    tickers_raw = data.get('tickers') or []
    tickers = [str(t).strip() for t in tickers_raw if t]
    title   = data.get('title') or ''
    author  = data.get('author') or ''

    # Silent dedup (not a filter — just skip re-storing)
    with _objects_lock:
        if news_id in _seen_ids:
            if prearmed:
                _emit_alert_release(prearmed, art_id)
            return
        _seen_ids.add(news_id)

    # ArrivalTime = when the WS alert was received, not when the curl finished,
    # so a saturated fetch pool can't inflate it. Falls back to now() if unset.
    arrival = recv_ts or datetime.now()
    id_key = f"id-{news_id}"

    # Apply reduced filter pipeline
    article_exchange = str(data.get('exchange') or '').strip().upper()
    passed, reason = _passes_filters(tickers, title, article_exchange)
    if not passed:
        logger.debug(f"Blocked id={news_id}: {reason}")
        with _blocked_lock:
            _blocked_objects[id_key] = data
        with _rejected_lock:
            _rejected_count += 1
        if prearmed:
            # Pre-armed on the alert but the full (post-curl) filter blocked it —
            # release the warm client the consumer provisioned.
            _emit_alert_release(prearmed, art_id)
        return

    # Accepted branch: DataFrame + accepted objects + auto-blacklist + callback
    symbol_str = ','.join(tickers)

    new_row = pd.DataFrame([{
        'ID':          news_id,
        'ArrivalTime': arrival,
        'Symbol':      symbol_str,
        'Headline':    title,
    }])
    with _df_lock:
        _news_df = pd.concat([_news_df, new_row], ignore_index=True)

    with _objects_lock:
        _news_objects[id_key] = data

    date_str = arrival.strftime('%d-%m-%Y %H:%M')
    with _blacklist_lock:
        for tk in tickers:
            _blacklist_set.add(tk)
            _blacklist_records.append({'Symbol': tk, 'Date': date_str, 'ID': news_id})

    logger.info(
        f"Accepted: {symbol_str} — id={news_id} | author={author} | "
        f"headline={title[:80]}"
    )

    with _callback_lock:
        cb = _news_callback
    if cb is not None:
        payload = {
            'Symbol':       symbol_str,
            'ID':           news_id,
            'ArrivalTime':  arrival,
            'Headline':     title,
            'article_body': data.get('article_body', ''),
            'exchange':     article_exchange,
            'prearmed':     [prearmed] if prearmed else [],
            'art_id':       art_id,
        }
        # Off-loop submit: the orchestrator's accepted callback may talk to IBKR
        # and block. Submitting to _callback_executor (single worker) keeps the
        # asyncio loop free for the next alert's curl while preserving order vs
        # the matching _emit_alert_arm / _emit_alert_release for the same art_id.
        fut = _callback_executor.submit(cb, payload)
        fut.add_done_callback(
            lambda f, _i=news_id: _log_cb_exception(f, "accepted", "-", _i)
        )

    # Persist this accepted article to disk NOW, instead of waiting for the next
    # hourly _flush(). Scheduled AFTER the consumer callback so it never delays
    # the orchestrator's clerk arm / sentiment path, and offloaded to
    # _persist_executor so the disk I/O never blocks NW4's asyncio loop (a
    # blocked loop would delay the NEXT alert's arm). The periodic flush still
    # runs as the idempotent backstop and is what prunes _news_objects.
    _persist_executor.submit(_persist_article_now, data)


# ─── Async main (runs inside background thread) ───────────────────────────────

async def _async_main(api_key: str) -> None:
    RECONNECT_DELAY = 10
    FLUSH_INTERVAL  = _config.get('flush_interval_seconds', 300)
    STATUS_INTERVAL = 60

    async_shutdown = asyncio.Event()
    abort_reconnect = False
    auth_failure_streak = 0    # incremented on 4004/4005; reset on successful handshake
    inflight: set = set()
    fetch_sem = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)

    async def _watch_shutdown():
        while not async_shutdown.is_set():
            await asyncio.sleep(0.5)
            if _shutdown_event.is_set():
                async_shutdown.set()

    async def _periodic_flush():
        # Run _flush on _flush_executor so the asyncio loop stays free for WS
        # reads + curls during the (multi-second) flush. Awaiting the future
        # preserves the existing "one flush at a time" semantics — the next
        # iteration won't sleep+submit until the previous flush returns.
        loop = asyncio.get_running_loop()
        try:
            while not async_shutdown.is_set():
                await asyncio.sleep(FLUSH_INTERVAL)
                if async_shutdown.is_set():
                    break
                await loop.run_in_executor(_flush_executor, _flush, False)
        except asyncio.CancelledError:
            pass

    async def _status_logger():
        try:
            while not async_shutdown.is_set():
                await asyncio.sleep(STATUS_INTERVAL)
                if async_shutdown.is_set():
                    break
                with _df_lock:
                    accepted_count = len(_news_df)
                with _blocked_lock:
                    blocked_count = len(_blocked_objects)
                with _blacklist_lock:
                    bl_size = len(_blacklist_set)
                with _rejected_lock:
                    rejected_count = _rejected_count
                with _fetched_lock:
                    fetched_count = len(_fetched_ids)
                logger.info(
                    f"Connection active — accepted {accepted_count}, "
                    f"blocked (in mem) {blocked_count}, "
                    f"rejected {rejected_count}, "
                    f"blacklist {bl_size}, "
                    f"fetched-ids {fetched_count}, "
                    f"inflight {len(inflight)}"
                )
        except asyncio.CancelledError:
            pass

    connector = aiohttp.TCPConnector(
        limit=HTTP_POOL_LIMIT,
        limit_per_host=HTTP_POOL_LIMIT,
        keepalive_timeout=KEEPALIVE_TIMEOUT_SEC,  # was 300 — drop idle conns before they go stale
        enable_cleanup_closed=True,               # reap half-closed TLS conns the peer dropped
    )

    async def _ws_loop(http: aiohttp.ClientSession):
        """Connect to ws-alerts; dispatch each alert to a background task."""
        nonlocal abort_reconnect, auth_failure_streak
        url = RTPR_WS_URL_TEMPLATE.format(key=api_key)
        async with websockets.connect(url, ping_interval=None) as ws:
            logger.info("WebSocket connected to RTPR.io ws-alerts.")
            while not async_shutdown.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=WS_RECV_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"No data received for {WS_RECV_TIMEOUT_SEC}s "
                        "(server should ping every 30s) — triggering reconnect"
                    )
                    return
                except websockets.ConnectionClosed as exc:
                    code = getattr(exc, 'code', None)
                    if code == 4002:
                        logger.warning("WS closed 4002 (ping timeout) — reconnecting.")
                    elif code == 4008:
                        logger.warning("WS closed 4008 (queue overflow) — reconnecting immediately.")
                    elif code == 4004:
                        auth_failure_streak += 1
                        logger.critical(
                            f"WS closed 4004 (TRIAL EXPIRED) — will retry with "
                            f"auth-failure backoff (streak={auth_failure_streak})."
                        )
                    elif code == 4005:
                        auth_failure_streak += 1
                        logger.critical(
                            f"WS closed 4005 (CONNECTION REVOKED — key rotated / plan changed, "
                            f"or RTPR auth service temporarily unreachable) — will retry with "
                            f"auth-failure backoff (streak={auth_failure_streak})."
                        )
                    else:
                        logger.warning(f"WS closed code={code} — reconnecting.")
                    return

                try:
                    msg = json.loads(raw)
                except Exception as e:
                    logger.error(f"Failed to parse RTPR alert: {e}; raw={str(raw)[:200]}")
                    continue

                mtype = msg.get('type')

                if mtype == 'ping':
                    await ws.send(json.dumps({'type': 'pong'}))
                elif mtype == 'connected':
                    logger.info(f"RTPR alerts connected: plan={msg.get('plan')}")
                    if auth_failure_streak > 0:
                        logger.info(
                            f"Auth-failure streak cleared (was {auth_failure_streak}) — "
                            f"RTPR reachable again."
                        )
                    auth_failure_streak = 0
                elif mtype == 'alert':
                    recv_ts = datetime.now()   # stamp at receipt, before any fetch
                    task = asyncio.create_task(
                        _handle_alert(msg, http, api_key, fetch_sem, recv_ts)
                    )
                    inflight.add(task)
                    task.add_done_callback(inflight.discard)
                    # [RecvLag] receive-side probe: the gap from the article's own
                    # published-at to when THIS loop stamped recv_ts, logged with the
                    # live in-flight count. If recv_lag ramps with inflight during a
                    # top-of-hour burst, the single asyncio loop is CPU-saturated (our
                    # consumer) rather than RTPR being slow. Never fatal — a missing or
                    # malformed timestamp logs recv_lag=NA.
                    try:
                        _pub = msg.get('article_published_at')
                        if _pub:
                            _pub_dt = datetime.fromisoformat(_pub.replace('Z', '+00:00'))
                            if _pub_dt.tzinfo is None:
                                _pub_dt = _pub_dt.replace(tzinfo=timezone.utc)
                            _recv_lag = (datetime.now(timezone.utc) - _pub_dt).total_seconds()
                            _lag_str = f"{_recv_lag:.3f}s"
                        else:
                            _recv_lag, _lag_str = None, "NA"
                    except Exception:
                        _recv_lag, _lag_str = None, "NA"
                    (logger.info
                     if _recv_lag is not None and _recv_lag >= RECV_LAG_WARN_SEC
                     else logger.debug)(
                        f"[RecvLag] ticker={msg.get('ticker')} "
                        f"recv_lag={_lag_str} inflight={len(inflight)}"
                    )
                elif mtype == 'subscribed':
                    # Not expected on ws-alerts (rules are server-side), but
                    # log if it ever appears.
                    logger.info(f"RTPR subscribed: {msg.get('message', '')}")
                elif mtype == 'error':
                    logger.error(f"RTPR error message: {msg}")
                else:
                    logger.debug(f"Unknown alert message type '{mtype}': {str(msg)[:200]}")

    shutdown_watcher_task = asyncio.create_task(_watch_shutdown())

    async with aiohttp.ClientSession(
        connector=connector, trace_configs=[_make_trace_config()]
    ) as http:
        while not async_shutdown.is_set() and not abort_reconnect:
            try:
                logger.info("Establishing WebSocket connection to RTPR ws-alerts...")
                flush_task  = asyncio.create_task(_periodic_flush())
                status_task = asyncio.create_task(_status_logger())
                stream_task = asyncio.create_task(_ws_loop(http))

                done, pending = await asyncio.wait(
                    [stream_task, flush_task, status_task, shutdown_watcher_task],
                    return_when=asyncio.FIRST_COMPLETED,
                )

                for task in pending:
                    if task is not shutdown_watcher_task:
                        task.cancel()
                        try:
                            await task
                        except asyncio.CancelledError:
                            pass

                for task in done:
                    if task is stream_task:
                        try:
                            exc = task.exception()
                            if exc:
                                logger.error(f"Stream error: {exc}")
                        except asyncio.CancelledError:
                            pass

                if async_shutdown.is_set() or abort_reconnect:
                    logger.info(
                        "Shutdown requested — stopping connection loop."
                        if async_shutdown.is_set() else
                        "Abort flag set — stopping connection loop."
                    )
                    break

            except asyncio.CancelledError:
                logger.info("Connection loop cancelled.")
                break
            except Exception as e:
                logger.error(f"Unexpected error in connection loop: {e}", exc_info=True)

            if not async_shutdown.is_set() and not abort_reconnect:
                if auth_failure_streak > 0:
                    idx = min(auth_failure_streak - 1, len(AUTH_FAILURE_BACKOFF_SEC) - 1)
                    delay = AUTH_FAILURE_BACKOFF_SEC[idx]
                    logger.info(
                        f"Reconnecting in {delay}s (auth-failure backoff, "
                        f"streak={auth_failure_streak})..."
                    )
                else:
                    delay = RECONNECT_DELAY
                    logger.info(f"Reconnecting in {delay}s...")
                # Cancellable sleep: returns immediately if nw.stop() was called.
                try:
                    await asyncio.wait_for(async_shutdown.wait(), timeout=delay)
                    # async_shutdown was set — outer while loop will see it and break.
                except asyncio.TimeoutError:
                    pass    # delay elapsed normally; loop around and try to reconnect

        # Drain in-flight fetches before the final flush so their results land
        if inflight:
            logger.info(f"Draining {len(inflight)} in-flight fetches (max 15s)...")
            try:
                await asyncio.wait_for(
                    asyncio.gather(*inflight, return_exceptions=True),
                    timeout=15,
                )
            except asyncio.TimeoutError:
                logger.warning("Some fetches did not complete within drain window.")

    shutdown_watcher_task.cancel()
    try:
        await shutdown_watcher_task
    except asyncio.CancelledError:
        pass

    logger.info("Running final flush before exit...")
    _flush(final=True)

    if abort_reconnect:
        logger.critical(
            "NewsWatcherV4 aborted reconnect loop due to an unrecoverable "
            "WS close code (4004/4005). The background thread is exiting; "
            "the orchestrator's main thread will remain blocked on its "
            "stop event until you Ctrl+C it."
        )

    with _df_lock:
        final_count = len(_news_df)
    with _blacklist_lock:
        bl_size = len(_blacklist_set)

    logger.info("=" * 60)
    logger.info("NewsWatcherV4 shutdown complete")
    logger.info(f"  Total accepted items : {final_count}")
    logger.info(f"  Blacklist size       : {bl_size}")
    logger.info("=" * 60)


# ─── Background thread entry point ────────────────────────────────────────────

def _thread_main() -> None:
    try:
        api_key = _load_rtpr_credentials(_config['api_keys'])
        logger.info("RTPR credentials loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load RTPR credentials: {e}")
        return

    try:
        asyncio.run(_async_main(api_key))
    except Exception as e:
        logger.error(f"Unhandled exception in async main: {e}")
