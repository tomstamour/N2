# CLAUDE.md — RTPR_connector

## Purpose

`RTPR_connector.py` is a low-latency connector to the **RTPR.io alerts WebSocket firehose**. For a
configurable universe of (~700) small-cap stock symbols, it ingests RTPR alert frames, fetches the
full press-release/article the instant an alert fires, and records each into an **in-memory** pandas
dataframe (`PR_dataframe`). It feeds an IBKR-based algorithmic trading workflow.

Design priority: **stay in the millisecond range even during bursts** (10–15 PRs within ~1s). WS
ingest is non-blocking; article fetches run concurrently over a shared keep-alive HTTP session.

## Run

```bash
python3 RTPR_connector.py                 # auto-loads the newest universe file; re-scans daily @ 03:58 ET
python3 RTPR_connector.py --self-test     # offline parser check, no network
```

Runs on the system `python3` (3.12); all deps already installed (`requirements.txt`). Long-lived
foreground process; Ctrl-C / SIGTERM triggers a final flush then clean exit. Key flags (all have
defaults): `--universe-dir` (the `universe_finder/data` dir), `--universe-reload-at` (03:58),
`--api-keys`, `--workers` (32), `--flush-at` (20:35), `--out-dir` (./tables), `--log-dir` (./logs),
`--fetch-timeout`, `--max-retries`, `--log-level`, `--self-test`.

## Architecture (asyncio + uvloop + aiohttp, single event loop)

```
[WS-listener] --jobQueue--> [32 fetch-workers] --resultQueue--> [DataFrame-writer] --(20:35)--> ./tables/PR_dataframe_<date>.csv
```

- **WS-listener** (1, stateful): owns the single WS connection — connect, ping/pong heartbeat,
  reconnect (2s→60s backoff, then 5-min slow-poll after repeated failures). On each frame: stamps
  `ArrivalTime`, json-decodes, filters `ticker` against the universe frozenset **before fetching**,
  dedups by article ID, enqueues. Zero HTTP/disk/heavy work here so the TCP buffer never backs up.
- **Fetch-workers** (N=32, stateless coroutines — **not OS threads**): pull job → GET permalink with
  `X-API-Key` over one shared `aiohttp.ClientSession` → stamp `CurlTime` → **lxml-parse off the loop**
  (`run_in_executor` on a 4-thread `rtpr-cpu` pool; lxml drops the GIL, so the loop stays free to read
  WS frames during a burst) → emit row. Single attempt by default (`--max-retries 0`); on hard failure
  a metadata-only row is emitted so the event is never lost. `N` is a concurrency ceiling on in-flight
  fetches (sized above the burst), independent of CPU core count.
- **DataFrame-writer** (1): the only component that mutates `PR_dataframe`; batches rows in.
- **Flush** (1): at 20:35 `DISPLAY_TZ`, atomically swaps the dataframe to empty and writes the old
  one to `./tables` off the event loop, then clears the dedup set.
- **Logging:** `QueueHandler`/`QueueListener` on a background thread → `./logs/RTPR_connector.log`
  (never blocks the hot path).

Design rationale is in `doc/` (threading, connection management, dataframe ingestion, WS protocol).

## PR_dataframe schema

```
Symbol | Tickers | ID | ArrivalDate | Created | ArrivalTime | CurlTime | Headline | Body | Exchange | Source
```

- **From the WS frame:** `Symbol` (matched ticker), `ID` (`article_url` path slug), `ArrivalDate` /
  `Created` (from `article_published_at`), `ArrivalTime` (receipt time).
- **From the fetched HTML:** `Headline`, `Tickers`, `Exchange`, `Source`, `Body`; plus `CurlTime`
  (fetch-complete time).
- `Tickers` / `Exchange` are compact JSON-array strings (e.g. `["WTM"]`); **one row per article**
  even when multiple tickers are present.
- All time fields render in `DISPLAY_TZ` at millisecond precision (`HH:MM:SS.mmm`); date `YYYY-MM-DD`.

## Critical conventions / gotchas

- **Timezone:** the `DISPLAY_TZ` constant (default `America/New_York`, DST-aware) governs **all** time
  columns AND the flush trigger. One-line switch to `ZoneInfo("Etc/GMT+4")` for literal fixed UTC-4.
- **No disk writes except** the rotating log and the once-daily flush — everything (including article
  bodies) stays in RAM.
- **The HTML parser is coupled to RTPR's fixed permalink template** (`h1.title`, `span.ticker`,
  `span.exchange`, `span.meta-source`, `div.article-body`, footer `<code>` for the ID). The permalink
  returns **server-rendered HTML, not JSON** (verified via `rtpr_probe.py` on 2026-06-22; the WS
  frame has only a single `ticker`, so the multi-ticker list + headline + body all come from the
  HTML). If RTPR changes the template, update `parse_article()` and re-run `--self-test`.
- **Single WS slot:** RTPR allows one ws-alerts connection per account. This connector occupies it —
  NW4 / `rtpr_probe.py` / any other RTPR consumer cannot run simultaneously.
- **Burst latency is mostly RTPR's TTFB, not us.** The permalink curl is slow (often 2–8s) during
  top-of-hour PR bursts because RTPR is slow to respond — confirmed on the sibling NW4 feed (see
  `../N2copy/scripts/newswatcher3/CLAUDE.md` + `rtpr_curl_probe.py`). Config is set to NW4.2's
  validated values to avoid *self-inflicted* overhead: `--fetch-timeout 20` (don't kill a near-done
  fetch → retry storm), `--max-retries 0` (don't pile onto a slammed endpoint), `keepalive_timeout=15`
  (don't reuse a NAT-dropped dead conn), off-loop parse (don't block the loop). Two diagnostic log
  lines make every burst self-diagnosing: **`[RecvLag] <tkr> recv_lag=.. inflight=..`** (WS receipt
  gap vs in-flight count — ramps with `inflight` ⇒ our loop is saturated; flat ⇒ RTPR-side) and
  **`[FetchTrace] <tkr> <id> attempt=.. status=.. dns=.. queued=.. connect=.. ttfb=.. body=..
  inflight=..`** (per-curl stage breakdown — high `ttfb` at low `inflight` ⇒ RTPR-global slowness;
  `ttfb` rising with `inflight` ⇒ a per-IP throttle, fix by capping concurrency with a small
  semaphore). Run on a **colocated low-latency host**, so a slow egress baseline is ruled out.
- **Universe** is auto-sourced — no `--universe` flag. At startup and again daily at **03:58
  `DISPLAY_TZ`** (≈2 min before the 04:00 ET pre-market open) the connector scans `--universe-dir`
  (`../universe_finder/data/`) and hot-swaps in the newest `stocks_universe_YYYY-MM-DD.tsv` — chosen
  by the **date in the filename** (the `_nonFiltered` variants are excluded; only the `Symbol` column
  is read). The daily re-scan (`universe_reload_scheduler`, mirrors `flush_scheduler`) runs off the
  event loop and, on any error (file not published yet, unreadable, 0 symbols), **keeps the current
  set** rather than crashing — so the daemon runs across days without a restart.
- **API key** is read from `RTPR_API-Key.txt` (the line starting `rtpr_`) at startup — never hard-code
  or commit the key.

## Verifying changes

1. `python3 -m py_compile RTPR_connector.py`
2. `python3 RTPR_connector.py --self-test` — validates `parse_article` / `build_row` against the saved
   probe dump `/tmp/rtpr_raw.primary.html` (regenerate with `rtpr_probe.py --out /tmp/rtpr_raw` if
   missing). Asserts the exact 11-column row for the WTM sample.
3. Live smoke test (needs the WS slot + a universe-ticker alert; best pre-market 04:00–09:30 ET):
   run it and confirm a `PR …` log line appears with `Created < ArrivalTime < CurlTime`.

## Files

- `RTPR_connector.py` — the connector (single-file program).
- `rtpr_probe.py` — one-shot WS + permalink probe used to reverse-engineer the HTML. Run the N2copy
  original (`/home/tom/Documents/ibkr_scripts/N2copy/scripts/newswatcher3/rtpr_probe.py`); the copy
  here needs `NewsWatcher3.py` alongside it to import its key parser.
- `doc/` — architecture rationale + RTPR WS protocol notes.
- `requirements.txt` — runtime deps (websockets, aiohttp, pandas, lxml, uvloop).
- `RTPR_API-Key.txt` — RTPR API key (secret; do not commit).
- `tables/` — daily flush output. `logs/` — rotating logs.
- `prompt.txt` — original task spec.

## Status (as of 2026-06-23)

Fully validated end-to-end. Offline: compile, universe load (691 symbols), parser self-test (exact
11-column row), flush CSV format. Live (evening smoke test): WS connect + auth, heartbeat (stable
through multiple ping cycles), the scheduled 20:35 flush firing + rescheduling for the next day, and
a real universe-ticker event — **RAY** (article `nGNXbGWt3G`): `alert → fetch → row` in **241 ms**
(`created 20:35:15.115 < arrival 20:35:15.266 < curl 20:35:15.507`), then a graceful SIGTERM that
drained and flushed the row to `tables/PR_dataframe_2026-06-22.csv`. That CSV is the first real output
sample — safe to delete before a clean production start.

**2026-06-23 — first real burst + latency fix.** A 6-PR burst at 07:30:00 (DFLI/PSTV/QUCY/TRIB/HSCS/
BOLD) was slow: total `created→row` 3–8s, with both stages (WS `recv_lag` 0.5→2.3s, curl 2.6→5.4s)
ramping through the burst. Diagnosis matched the sibling NW4 feed's prior finding: top-of-hour curls
are RTPR-server-side TTFB-bound, not ours. Applied NW4.2's validated config + ported its per-stage
instrumentation (see "Burst latency" under Critical conventions): `--fetch-timeout 20`, `--max-retries
0`, `keepalive_timeout=15`, off-loop lxml parse, and the `[RecvLag]` / `[FetchTrace]` log lines.
**Next:** capture the next live top-of-hour burst and read `[FetchTrace]` — if `ttfb` is flat-high
across `inflight`, it's RTPR's floor (accept); if it rises with `inflight`, add a ~2–3 fetch semaphore
to stagger. Sub-1s during heavy RTPR bursts may not be reachable while their TTFB is the bottleneck.

**2026-06-23 — universe auto-load.** Dropped the `--universe` flag. The connector now discovers the
newest `stocks_universe_YYYY-MM-DD.tsv` in `--universe-dir` at startup and hot-swaps it daily at
03:58 ET via a `universe_reload_scheduler` coroutine (mirrors `flush_scheduler`), so a long-running
process always filters against the current day's universe without a restart.
