# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## File map

This directory contains three generations of the news-ingestion module. **The latest implementation is `NewsWatcher4.1.py`**; older versions are kept for diff/rollback.

| File | Status | Notes |
|------|--------|-------|
| `NewsWatcher4.1.py` | **Current** | NW4 + executor-based offloading for logging, downstream callbacks, and the periodic flush. Keeps the asyncio loop free during PR bursts so aiohttp curls complete as fast as RTPR's TTFB allows. |
| `NewsWatcher4.py` | Previous | RTPR alerts-WS + permalink-fetch model. Single asyncio loop + `_persist_executor` + `_cpu_executor` only. |
| `NewsWatcher3.py` | Legacy | Pre-licensing-change firehose. Not functional under the current RTPR plan; kept for reference. |

The public API is byte-identical from NW3 → NW4 → NW4.1 (NW4 added two new optional callbacks; NW4.1 changed only the thread they run on, not their signatures).

## Running and Testing

```bash
# Integration test (requires a valid RTPR.io API key)
python3 test_main.py
```

**Importing from another script:**

`NewsWatcher4.1.py` contains a dot in its filename so it cannot be imported with a bare `import` statement. Either rename to `NewsWatcher4_1.py` for direct import, or use `importlib`:

```python
import importlib
nw = importlib.import_module("NewsWatcher4.1")
```

The original `NewsWatcher4.py` still imports normally if you need a known-good fallback while diffing.

**Minimum start call** (defaults assume CWD == this directory):

```python
nw.start()
```

**Full usage cycle:**

```python
nw.start(
    universe_tsv='./nasdaq_symbols_data_priced.tsv',
    black_list='./black_list.csv',
    blacklist_expiry_hours=168,
    api_keys='./RTPR_API-Key.txt',
    blocked_dir='./blocked_PRs',
    accepted_dir='./accepted_PRs',
    excluded_strings_file='./excluded_strings.txt',
    priced_tsv='./nasdaq_symbols_data_priced.tsv',
    reject_float_greater_then=50,
    reject_price_greater_then=2.00,
    flush_interval_seconds=300,
)

df  = nw.get_news_df()                      # accepted DataFrame: ID, ArrivalTime, Symbol, Headline
obj = nw.get_news_object("id-51772090")     # full accepted article dict, or None if pruned
raw = nw.get_blocked_object("id-51772090")  # blocked article dict, or None if pruned/absent

nw.update_universe(["AAPL", "MSFT"])        # live swap, no restart needed

nw.stop()                                   # graceful shutdown, flushes to disk
```

**Three callbacks (all execute on `_callback_executor`, NOT on the asyncio loop):**

1. **Accepted-article callback** — fires when an article passes the full filter pipeline.
   ```python
   def on_accepted(news_dict: dict) -> None:
       # keys: Symbol, ID, ArrivalTime, Headline, article_body,
       #       exchange, prearmed, art_id
       ...
   nw.register_callback(on_accepted)
   ```
2. **Alert-arm callback** — fires the instant a WS alert's primary ticker passes the cheap pre-filter, BEFORE the permalink curl. Lets the orchestrator arm `reqMktData` while NW4.1 is still curling.
   ```python
   def on_alert(ticker: str, art_id: str, recv_ts) -> None: ...
   nw.register_alert_callback(on_alert)
   ```
3. **Alert-release callback** — fires when an article that was pre-armed via alert-arm is later dropped (curl/normalize failure, post-curl dedup, full-filter block). Lets the orchestrator release the client it provisioned.
   ```python
   def on_release(ticker: str, art_id: str) -> None: ...
   nw.register_alert_release_callback(on_release)
   ```

- Register BEFORE `start()` to avoid a race window.
- Callbacks run on `_callback_executor`, a **single-worker** thread pool. This preserves FIFO ordering for the same `art_id`: `arm → (release OR accepted)`. The orchestrator's release-only-if-armed invariant depends on it — **do not raise `max_workers` above 1**.
- Exceptions are caught by a `done_callback` and logged at ERROR with `exc_info`; they cannot crash NW4.1.
- Pass `None` to deregister.

**Dependencies:** `pandas`, `websockets`, `aiohttp` (no Alpaca SDK).

**Credentials:** `RTPR_API-Key.txt` must contain a single line after `Key:`:
```
Key:
rtpr_XXXXXXXXXXXXXXXXXXXXXXXX
```

## Architecture

`NewsWatcher4.1.py` is a self-contained press-release / news ingestion module. It bridges an async RTPR.io WebSocket stream into a synchronous, thread-safe API.

**Threading model:**
- Caller's main thread uses the synchronous public API.
- One background daemon thread (`NewsWatcherV4_bg`) runs a single asyncio event loop. The WS reader, per-alert tasks, and all aiohttp curls live on this loop.
- **Four thread pools** offload every form of blocking work from the loop:

| Pool | Workers | Owns | Why |
|------|---------|------|-----|
| `_persist_executor` (`nw4-persist`) | 2 | Per-article JSON writes (`_persist_article_now`) | Disk I/O kept off the loop. Submitted after the accepted callback. |
| `_cpu_executor` (`nw4-cpu`) | 4 (`CPU_POOL_WORKERS`) | HTML scrape (`_normalize_article`) | GIL-bound regex; off-loop scrape lets the loop keep stamping `recv_ts` and firing arms during bursts. |
| `_callback_executor` (`nw4-callback`) | **1** | All three downstream callbacks (arm, release, accepted) | A blocking IBKR call in the orchestrator can't stall the loop. Single worker preserves arm→release/accepted ordering. |
| `_flush_executor` (`nw4-flush`) | 1 | Periodic `_flush` | Multi-second flush can't block WS reads or in-flight curls. |

- **Logging is non-blocking on the loop.** The logger only owns a `QueueHandler`; a `QueueListener` thread (started by `_setup_logging`) dequeues records and runs the real `_DailyFileHandler` + `StreamHandler`. Every `logger.info/debug` on the hot path is a `queue.put_nowait` (microseconds, no I/O).
- Shutdown order in `stop()`: persist → cpu → callback → flush → log listener. A synchronous `StreamHandler` is re-attached on shutdown so the final `"NewsWatcherV4 stopped."` line surfaces on stdout after the listener is dead.
- Nine `threading.Lock` objects: `_df_lock`, `_objects_lock` (accepted objects + `_seen_ids`), `_blocked_lock`, `_blacklist_lock`, `_universe_lock`, `_priced_lock`, `_fetched_lock` (pre-fetch dedup), `_callback_lock`, `_rejected_lock`.

**WebSocket + HTTP protocol (RTPR.io alerts mode):**
- WS endpoint: `wss://ws.rtpr.io/ws-alerts?apiKey=<KEY>`
- Server-side filter rules govern the stream (created at https://rtpr.io/wire). Expected: a single catch-all `tickers_length gte 1`.
- Heartbeat: server `{"type": "ping"}` every ~30s; client must reply `{"type": "pong"}` within 90s.
- Alert payload: `{"type": "alert", "ticker": "XXX", "article_url": "https://rtpr.io/a/...?sig=...", "article_published_at": "..."}` — body is NOT included.
- For each alert, NW4.1 issues `GET article_url` with header `X-API-Key: <KEY>` to retrieve the article body. **This is the "Curl" process.** Bounded by `MAX_CONCURRENT_FETCHES=64` (asyncio.Semaphore).
- Reconnect: 10s on most disconnects. Auth-class codes (4004/4005) use exponential backoff: 1m → 5m → 30m → 1h, reset on a successful handshake.

**Ingestion pipeline (per alert):**
```
WS alert arrives ('ticker', 'article_url', 'article_published_at')
  ├─ stamp recv_ts (used as ArrivalTime so it reflects reception, not curl time)
  └─ asyncio.create_task(_handle_alert(...))
        ├─ extract article_id from article_url
        ├─ pre-fetch dedup (skip if art_id in _fetched_ids)
        ├─ cheap pre-filter on primary ticker (universe + blacklist + price/float)
        ├─ on PASS: fire alert-arm callback → submitted to _callback_executor
        ├─ await GET article_url under fetch_sem (max 64 concurrent)
        ├─ await run_in_executor(_cpu_executor, _normalize_article, …)
        └─ await _handle_article(data)
              ├─ silent dedup on data['id'] (_seen_ids — not a filter)
              ├─ full filter pipeline _passes_filters(tickers, title, exchange)
              ├─ on BLOCK: store in _blocked_objects; alert-release if prearmed
              └─ on ACCEPT:
                    ├─ append row to _news_df, store in _news_objects
                    ├─ auto-blacklist every ticker in tickers[]
                    ├─ fire accepted callback → submitted to _callback_executor
                    └─ submit _persist_article_now to _persist_executor

Periodic flush (every flush_interval_seconds, default 300)
  └─ await loop.run_in_executor(_flush_executor, _flush, False)
        • blacklist CSV (atomic)
        • blocked + accepted per-article JSONs
        • per-symbol JSON → outputs/<comma-joined-symbols>-YYYY-MM-DD.json
        • daily NewsDF TSV → outputs/NewsDF-YYYY-MM-DD.tsv
        • prune _blocked_objects, _news_objects; clear _seen_ids, _fetched_ids

stop() → final synchronous _flush(final=True) before the loop exits
```

**Two-tier in-memory storage:** accepted articles in `_news_objects`, rejected in `_blocked_objects`. Both flushed periodically and pruned. Pre-fetch dedup uses `_fetched_ids` (URL-slug id) and is cleared at flush. Post-normalize dedup uses `_seen_ids` (article payload id).

**Filter pipeline (`_passes_filters`):**
1. `1 <= len(tickers) <= 2`
2. At least one ticker in the universe (`_universe_set`)
3. No ticker in the blacklist (`_blacklist_set`)
4. No `excluded_strings` substring in `title` (case-insensitive)
5. If `priced_tsv` is set: for each ticker matched in `_priced_data` on the article's exchange, `Float_M <= reject_float_greater_then` AND `LastDailyClosePrice <= reject_price_greater_then`. Tickers absent from priced data or listed on a different exchange are skipped (not failed); blank fields skip only their own check.

The same function runs both pre-fetch (single primary ticker, empty title/exchange — only steps 2/3/5 are evaluable) and post-fetch (full `tickers[]` + title + exchange).

**Auto-blacklist on accept:** every ticker in `tickers[]` is appended to the blacklist with the accepted article's id and arrival timestamp. Expires after `blacklist_expiry_hours`.

**Symbol value:** the DataFrame `Symbol` column and per-symbol JSON filename use a comma-joined string of all `tickers[]` entries (e.g. `AAPL,MSFT`). Downstream consumers must parse if they need individual tickers.

**Atomic file writes:** all disk writes use temp file + `os.replace()`.

**Burst-latency tuning knobs** (top of `NewsWatcher4.1.py`):
- `MAX_CONCURRENT_FETCHES = 64` — fetch semaphore
- `FETCH_TIMEOUT_SEC = 20.0`, `FETCH_MAX_RETRIES = 2`, `FETCH_BACKOFF_SEC = 0.5`
- `HTTP_POOL_LIMIT = 64`, `KEEPALIVE_TIMEOUT_SEC = 15` — aiohttp connector
- `WS_RECV_TIMEOUT_SEC = 90` — matches RTPR's pong deadline
- `CPU_POOL_WORKERS = 4` — `_cpu_executor` size
- `SLOW_FETCH_LOG_SEC = 1.0`, `RECV_LAG_WARN_SEC = 1.0` — thresholds for INFO vs DEBUG on `[Timing]` / `[RecvLag]`

## Changes from NW4 → NW4.1

NW4.1 changes are surgical and confined to `NewsWatcher4.1.py`. The public API, WebSocket protocol, filter pipeline, fetch logic, and all output file formats are unchanged. The only behavior change is **which thread runs which work** — the asyncio loop is freed from logging, callback invocation, and the periodic flush, so aiohttp curls aren't starved during PR bursts.

| Area | NW4 (`NewsWatcher4.py`) | NW4.1 (`NewsWatcher4.1.py`) | NW4.1 location |
|------|-------------------------|-----------------------------|----------------|
| **Logging** | `_DailyFileHandler` + `StreamHandler` attached directly to `logger`. Every `logger.info/debug` on the hot path does a synchronous file write on the asyncio loop. | `logger` holds only a `QueueHandler`; a `QueueListener` thread owns the real handlers. Hot-path logs are `queue.put_nowait`. | `_setup_logging`, line 643 |
| **Alert-arm / alert-release callbacks** | `_emit_alert_arm` / `_emit_alert_release` invoke `cb(...)` inline on the loop. | `_callback_executor.submit(cb, ...)` + `done_callback` for exception logging. | lines 581, 599 |
| **Accepted-article callback** | `try: cb(payload) except: log` inline on the loop inside `_handle_article`. | `_callback_executor.submit(cb, payload)` + `done_callback`. | line 1709 |
| **Exception logging from callbacks** | Try/except in the emitter. | New helper `_log_cb_exception(fut, kind, ticker, art_id)` attached as `done_callback`. | line 569 |
| **Periodic flush** | `_periodic_flush` calls `_flush(final=False)` directly on the loop. | `await loop.run_in_executor(_flush_executor, _flush, False)`. Final shutdown `_flush(final=True)` stays synchronous. | line 1742 |
| **Imports** | stdlib + `asyncio` etc. | + `queue`, `from logging.handlers import QueueHandler, QueueListener`. | lines 49, 55 |
| **Module-level state** | `_persist_executor`, `_cpu_executor`. | + `_callback_executor` (workers=**1**, `nw4-callback`), `_flush_executor` (workers=1, `nw4-flush`), `_log_queue`, `_log_listener`. | lines 121-157 |
| **`start()`** | Recreates `_persist_executor` + `_cpu_executor`. | + recreates `_callback_executor` + `_flush_executor`; adds both to the `global` declaration. | line 264 (globals), 287-294 (instantiation) |
| **`stop()`** | Shutdown order: persist → cpu. | Shutdown order: persist → cpu → callback → flush → log listener. A synchronous `StreamHandler` is re-attached AFTER `_log_listener.stop()` so the final `"NewsWatcherV4 stopped."` line still surfaces on stdout. | line 436+ |

**Single-worker constraint (`_callback_executor`):** `max_workers=1` is **load-bearing**. It guarantees FIFO ordering of `arm → release` and `arm → accepted` for the same `art_id`, which the orchestrator's release-only-if-armed invariant depends on. Raising it would surface as orchestrator clients being released before they were armed.

**What did NOT change in NW4.1:**
- WS protocol, reconnect cadence, auth-failure backoff schedule (4004/4005)
- `_passes_filters` and the full 5-step filter pipeline
- Pre-fetch dedup (`_fetched_ids`) and the cheap pre-filter on the alert's primary ticker
- `_fetch_article` retry logic, semaphore, aiohttp connector tuning
- `_normalize_article` already off-loop via `_cpu_executor` (NW4)
- `_persist_article_now` already off-loop via `_persist_executor` (NW4)
- All output file formats, paths, and the `NewsWatcher4_<date>.log` filename
- Public function signatures (`register_callback`, `register_alert_callback`, `register_alert_release_callback`, `get_news_df`, `get_news_object`, `get_blocked_object`, `update_universe`, `start`, `stop`)
- Final shutdown flush at end of `_async_main` — stays synchronous on the loop thread (the loop is exiting; nothing needs to stay responsive)

**Known follow-up (NOT in NW4.1):**
- `_news_df = pd.concat([_news_df, new_row], ignore_index=True)` per accepted article (line 1676) is O(n) and slowly degrades over a long session. Clean fix is a `_pending_rows: list[dict]` drained at `get_news_df()` / `_flush()` time. Skipped — it's amortized and small relative to a stalled loop; the executor-offload fixes were the burst-latency culprits.

**Stale docstrings in NW4.1.py to clean up:** `register_callback` (line 520), `register_alert_callback` (line 537), `register_alert_release_callback` (line 554) still claim callbacks run "from the background thread" / "on the asyncio loop thread." After the NW4.1 change they run on `_callback_executor`. Docstrings need a quick refresh; behavior is correct.

## Burst-latency instrumentation

NW4.1 emits three structured log lines for diagnosing top-of-hour burst behavior:

- `[Timing] id=… ticker=… semwait=… curl=… normalize=… total=…` — every alert. Splits the alert→body-ready window into semaphore wait (our local queue), curl (RTPR round-trip), normalize (HTML scrape). Always INFO.
- `[RecvLag] ticker=… recv_lag=… inflight=…` — gap from `article_published_at` → `recv_ts` paired with the in-flight task count. A ramp here that tracks `inflight` means OUR loop is saturated, not RTPR.
- `[FetchTrace] id=… ticker=… attempt=… reused=… dns=… queued=… connect=… ttfb=… body=…` — per-curl-attempt breakdown from `_make_trace_config()`. Used together with `rtpr_curl_probe.py` to disentangle host/network/server contributions.

Expected post-fix: `semwait < 0.1s` during bursts, `recv_lag` decoupled from `inflight`, `curl − ttfb` near zero.

## Output Files

| File | Location | Format | Contents |
|------|----------|--------|----------|
| News DataFrame | `news_df_dir/NewsDF-YYYY-MM-DD.tsv` | TSV: ID, ArrivalTime, Symbol, Headline | Accepted articles only |
| Per-symbol news | `output_dir/<comma-joined-symbols>-YYYY-MM-DD.json` | JSON | Accepted articles, one file per symbol-string per day |
| Blocked articles | `blocked_PRs/<id>-<ticker>-YYYY-MM-DD.json` | JSON | Articles rejected by the full filter |
| Accepted articles | `accepted_PRs/<id>-<ticker>-YYYY-MM-DD.json` | JSON | Articles passing the full filter |
| Blacklist | `black_list.csv` | CSV: Symbol, Date (DD-MM-YYYY HH:MM), ID | Auto-extended on each accept |
| Log | `log_dir/NewsWatcher4_YYYY-MM-DD.log` | Python logging | DEBUG to file, INFO to stdout, rotated daily |

## Integration Context

NW4.1 is the news ingestion layer for the **N2 pipeline**. The post-RTPR-licensing-change migration (firehose → alerts + per-article permalink curl) introduced multi-second permalink curls during PR bursts. The NW4.1 executor-offload changes (logging, callbacks, periodic flush — all moved off the asyncio loop) ensure the loop is never blocked by anything other than awaited I/O, so aiohttp curls complete as fast as RTPR's TTFB allows.

The `blocked_PRs/` directory exists so downstream tooling can replay/audit all rejected traffic, while the existing N1-style outputs (`outputs/`, `NewsDF-YYYY-MM-DD.tsv`) remain compatible with consumers built against NW2.

The universe is loaded from `nasdaq_symbols_data_priced.tsv` (Symbol + Float_M + LastDailyClosePrice + Exchange columns) at start. `update_universe(new_list)` performs an O(1) hot-swap without restarting the WebSocket.

`rtpr_curl_probe.py` (same directory) is a standalone diagnostic that replays the permalink curl under controlled L=1 / L=N concurrency — useful for separating host/network/server contributions when `[Timing]` shows pathological `curl` values.
