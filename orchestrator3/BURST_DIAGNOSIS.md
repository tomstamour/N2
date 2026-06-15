# Diagnosing top-of-hour fetch-burst latency (NewsWatcher4)

When the daily TSV (`tables/news_output_YYYY-MM-DD.tsv`) shows a high `CurlTime` for a
cluster of PRs around the top of the hour (e.g. 08:00:00), this is the runbook.

## TL;DR

A burst of high `CurlTime` is almost always a **transient, self-draining fetch-queue
backlog**, not a broken thread pool. NW4's pools are healthy by design — every blocking
step is offloaded off the asyncio loop thread:

- disk persist → `_persist_executor`
- clerk arm → `_clerk_executor` (`_on_alert` only submits, never blocks)
- accept callback `on_news_accepted` → fire-and-forget (no blocking `.result()`)

So before chasing a "stuck queue", read the timing logs below — they tell you whether the
delay is **our** fetch pool or **RTPR's** server.

## Metric gotcha: what `CurlTime` actually measures

`CurlTime` is **not** local processing time and **not** the curl duration alone. It is the
**full alert→body-ready latency**:

- `ArrivalTime` is stamped when the WS alert frame arrives — `_ws_loop` in
  `../newswatcher3/NewsWatcher4.py`.
- `CurlTime` is stamped when the accepted article reaches the orchestrator callback —
  `on_news_accepted` in `Orchestrator4.0.py` (`news_dict['CurlTime'] = datetime.now()`).

It therefore includes the network round-trip to rtpr.io **plus** any time spent waiting for
a free fetch slot. A remote HTTPS GET is sub-second at best — it can never be
"milliseconds". The single `CurlTime` number cannot, on its own, separate queue-wait from
server-latency. The `[Timing]` log (below) can.

## How to diagnose a burst

1. Open the NW4 log for the day: `logs/NewsWatcher4_YYYY-MM-DD.log`.
2. Grep for the per-stage timing lines emitted by `_handle_alert`:

   ```bash
   grep '\[Timing\]' logs/NewsWatcher4_YYYY-MM-DD.log
   ```

   Each line splits the latency into three stages:

   ```
   [Timing] id=<art_id> ticker=<T> semwait=0.012s curl=2.430s normalize=0.004s total=2.446s
   ```

   - **`semwait`** — time spent waiting for a free fetch slot. This is **our local queue**
     (`asyncio.Semaphore(MAX_CONCURRENT_FETCHES)` + aiohttp `limit_per_host`, all to the
     single host rtpr.io).
   - **`curl`** — the RTPR permalink round-trip, including any retries / backoff.
   - **`normalize`** — local HTML scrape (`_normalize_article`).

   Lines log at **INFO** when `total >= SLOW_FETCH_LOG_SEC` (default `1.0s`), else DEBUG —
   so during a slow burst the INFO log shows exactly the items that hurt.

3. Also scan for fetch problems that would otherwise be invisible:

   ```bash
   grep -E 'HTTP (429|5[0-9]{2})|transient error|exhausted retries|queue overflow' \
     logs/NewsWatcher4_YYYY-MM-DD.log
   ```

## Decision rule

| Observation | Bottleneck | Action |
|---|---|---|
| `semwait` dominates | Our fetch pool is too narrow for the burst | Raise `MAX_CONCURRENT_FETCHES` **and** `HTTP_POOL_LIMIT` in `../newswatcher3/NewsWatcher4.py` (keep them equal; `limit_per_host` tracks `HTTP_POOL_LIMIT`). |
| `curl` dominates | RTPR's permalink endpoint is slow under top-of-hour load | Widening the pool **won't help** and risks HTTP 429 rate-limiting. Leave the pool; watch for `429` warnings in `_fetch_article`. |
| `normalize` dominates | Local CPU/parse (unexpected) | Profile `_normalize_article`; consider offloading to an executor. |

## Relevant knobs (`../newswatcher3/NewsWatcher4.py`, "NW4 tuning knobs" block)

- `MAX_CONCURRENT_FETCHES` — concurrent permalink curls (semaphore width).
- `HTTP_POOL_LIMIT` — aiohttp `TCPConnector` pool size; keep `>= MAX_CONCURRENT_FETCHES`.
- `FETCH_TIMEOUT_SEC`, `FETCH_MAX_RETRIES`, `FETCH_BACKOFF_SEC` — per-attempt fetch behavior.
- `SLOW_FETCH_LOG_SEC` — `[Timing]` INFO/DEBUG threshold.

## Baseline reference (2026-06-15 08:00 burst)

- ~60 curls fired within ~4 s; the semaphore was 32-wide at the time.
- `CurlTime` latency ramped 0.24 s → 6.8 s through the burst, then drained — the late
  arrival (MEHA, +13 s) saw only 2.8 s. Classic build-and-drain queue signature.
- Logs that day were clean: no timeouts, retries, `aiohttp`/`ClientError`, tracebacks, or
  WS `4008` (queue-overflow).
- **Response:** `MAX_CONCURRENT_FETCHES` / `HTTP_POOL_LIMIT` widened 32 → 64, and the
  `[Timing]` instrumentation was added the same change to confirm `semwait`-vs-`curl` on
  the next burst.
