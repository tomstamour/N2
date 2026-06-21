# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running

```bash
python3 Orchestrator.py
```

Blocks until Ctrl+C. Graceful shutdown calls `nw.stop()` automatically.

## Architecture

`Orchestrator.py` is the top-level entry point for the news pipeline. It:
1. Fetches the stock universe via `yfinance_stock_universe.fetch()`
2. Registers an event callback with NewsWatcher2 **before** `nw.start()`
3. Starts NewsWatcher2 (returns immediately — non-blocking)
4. Blocks the main thread on a `threading.Event` until Ctrl+C / SIGTERM
5. Shuts down the executor and NewsWatcher2 gracefully on exit

**Threading model:**
- Main thread: blocked on `_stop_event.wait()` — wakes only on signal
- NW2 background thread: runs the Alpaca WebSocket stream (managed by NewsWatcher2)
- Echo worker threads: `ThreadPoolExecutor(max_workers=2)` — one slot per parallel task

**Event flow (per accepted news item):**
```
NW2 background thread
  → on_news_accepted(news_dict) [called synchronously in _handle_news coroutine]
      → executor.submit(echo1, news_dict)   [non-blocking]
      → executor.submit(echo2, news_dict)   [non-blocking]
      → future_e1.result(timeout=30)        [blocks NW2 thread — OK for fast tasks]
      → future_e2.result(timeout=30)
      → build completed news_dict {Symbol, ID, ArrivalTime, Headline, Echo1, Echo2}
      → print formatted output
```

**Signal handler ordering (critical):**
Signal handlers are registered **after** `nw.start()` so Orchestrator's handlers override NW2's own handlers. `nw.stop()` is idempotent — safe to call even if already stopped.

## Replacing Echo Functions with Real Tasks

`echo1()` and `echo2()` are placeholder stubs. Replace their bodies with real analysis (FinBERT, NER, HTTP calls, etc.).

When real tasks are slow (> a few ms), avoid blocking the NW2 asyncio loop by switching `on_news_accepted` to fire-and-forget:

```python
def _collect_and_log(news_dict, f1, f2):
    echo1_val = f1.result(timeout=60)
    echo2_val = f2.result(timeout=60)
    # ... build and log completed_dict ...

def on_news_accepted(news_dict):
    f1 = _executor.submit(echo1, news_dict)
    f2 = _executor.submit(echo2, news_dict)
    _executor.submit(_collect_and_log, news_dict, f1, f2)  # returns immediately
```

Also increase `max_workers` in `ThreadPoolExecutor` if adding more parallel tasks.

## Trade-frequency worker

`analyze_trade_frequency()` runs in parallel with FinBERT and `echo2` for every accepted news item. It imports `pre_trade_frequency_baseline` as a module (not subprocess), opens its own IBKR connection per call (clientID auto-increments from `TF_BASE_CLIENT_ID` to avoid collisions), and returns `trades_per_sec` which lands in `completed_dict['Trades/sec']` and the daily TSV. All knobs (`TF_PORT`, `TF_TICKS_QUANTITY`, `TF_ON_TRIGGER`, `TF_CROSS_SESSION`, `TF_OUTPUT_DIR`, `TF_LOG_DIR`, etc.) are constants near the top of `Orchestrator.py`. Per-call CSV goes to `TF_OUTPUT_DIR`, per-call log to `TF_LOG_DIR`. Failures (timeout, no IBKR) yield `None` rather than raising, so the news pipeline keeps running.

## Trade-mole trigger

`maybe_launch_trade_mole()` runs in `_collect_and_log` after the row is written to the daily TSV. When `sentiment_score > TM_SENTIMENT_SCORE_MIN`, `Float < TM_FLOAT_MAX_M`, and `Trades/sec` is a valid positive number, it spawns `trade_mole.py` as a detached background subprocess (`start_new_session=True`) so Ctrl+C on Orchestrator does not kill it. Each launch gets the next clientID from `TM_BASE_CLIENT_ID` (400, 401, ...). Per-instance stdout/stderr goes to `TM_LOG_DIR/SYMBOL_TIMESTAMP_CLIENTID.log`; CSV goes to `TM_OUTPUT_DIR`. All knobs live in the "Trade-mole trigger" config section near the top of `Orchestrator.py`. Failures to spawn are logged and never raise — the news pipeline keeps running.

## Dependencies

- `NewsWatcher2` — `path/to/N1/scripts/newswatcher2/`
- `yfinance_stock_universe` — `path/to/N1/scripts/universe_finder/`
- `pre_trade_frequency_baseline` — `path/to/N1/scripts/volume/trade_frequency_baseline/` (requires IBKR Gateway/TWS)
- `trade_mole.py` — `path/to/N1/scripts/volume/trade_surge_mole/` (CLI; spawned as detached subprocess on qualifying news)

## Integration Context

Orchestrator sits at the top of the pipeline:
`universe_finder` → `newswatcher2` → **Orchestrator** → `FinBERT` / `NerSecDictionary` / `pronounCer`

---

## Orchestrator3 (RTPR / NewsWatcher3)

`Orchestrator3.py` is the RTPR-driven counterpart to `Orchestrator.py`. The shape of the pipeline (FinBERT → trade-frequency baseline → trade_mole) is identical; only the news source changed. Run with:

```bash
python3 Orchestrator3.py
```

### News source

`NewsWatcher3` (RTPR.io WebSocket firehose) replaces `NewsWatcher2` (Alpaca). NW3 owns the universe, blacklist, excluded-strings, and priced-data filters internally — Orchestrator3 just passes the relevant paths and thresholds via `nw.start()`. Orchestrator3 also re-reads the priced TSV (`NW3_PRICED_TSV`) into `_priced_df` so it can record `Float_M` in its daily TSV.

### Multi-ticker fan-out (the central difference vs Orchestrator.py)

NW3 hands the callback a `Symbol` field that is a comma-joined string of up to two tickers (`"ABC,DEF"`). `on_news_accepted()` splits it and **fans out per ticker**:

- FinBERT and `echo2` run **once per article** — their futures are shared across all tickers.
- `analyze_trade_frequency(news_dict, symbol)` and `maybe_launch_trade_mole(completed_dict)` run **once per ticker** (independent IBKR connection / independent threshold check / independent subprocess).
- The daily TSV gets **one row per ticker**: same `ID`, `ArrivalTime`, `Headline`, `Author`, FinBERT scores; per-ticker `Symbol`, `Float`, `Trades/sec`. The raw `tickers` list is preserved in a JSON-encoded `Tickers` column so the comma in `Symbol` stays unambiguous.

### TSV columns

```
['Symbol', 'Tickers', 'ID', 'ArrivalTime', 'Headline', 'Author', 'Float',
 'positive', 'negative', 'neutral', 'sentiment_score', 'label', 'Trades/sec']
```

### Author lookup

NW3's callback payload only contains `Symbol / ID / ArrivalTime / Headline`. Orchestrator3 retrieves the article's `author` via `nw.get_news_object(f"id-{news_id}")` from inside `_collect_and_log`. NW3 prunes accepted objects on its periodic flush (default 60 minutes), which is well after our worker has run; if it ever returns `None`, the TSV cell is empty and nothing else breaks.

### Threading model and worker pool

`ThreadPoolExecutor(max_workers=8)` — sized to absorb the fan-out (up to 2 tickers × TF + 1 FinBERT + 1 echo2 + collect tasks per article). Otherwise identical to `Orchestrator.py`.

### NW3 inputs

All NW3-related paths and thresholds live in the `NW3_*` config block near the top of `Orchestrator3.py` (`NW3_UNIVERSE_TSV`, `NW3_PRICED_TSV`, `NW3_BLACK_LIST`, `NW3_API_KEYS`, `NW3_LOG_DIR`, `NW3_OUTPUT_DIR`, `NW3_NEWS_DF_DIR`, `NW3_BLOCKED_DIR`, `NW3_ACCEPTED_DIR`, `NW3_EXCLUDED_STRINGS_FILE`, `NW3_BLACKLIST_EXPIRY_DAYS`, `NW3_REJECT_FLOAT_GT`, `NW3_REJECT_PRICE_GT`, `NW3_FLUSH_INTERVAL_SEC`). NW3's `start()` signature is unchanged.

### Dependencies (Orchestrator3 only)

- `NewsWatcher3` — `path/to/N2/scripts/newswatcher3/`
- `pre_trade_frequency_baseline` — `path/to/N1/scripts/volume/trade_frequency_baseline/` (requires IBKR Gateway/TWS)
- `trade_mole.py` — `path/to/N1/scripts/volume/trade_surge_mole/`
- Priced universe TSV — `path/to/N1/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv`

The original `Orchestrator.py` (NW2 / Alpaca path) is the legacy implementation and remains in this directory for reference.

---

## Orchestrator3.2 (FinBERT body-pipeline addition)

`Orchestrator3.2.py` extends `Orchestrator3.1.py` by running the heavyweight `FinBERT_body_pipeline` (clean → coreference resolution → sentence split → SEC-EDGAR NER → entity-targeted FinBERT) on every accepted article's `article_body` in parallel with the existing FinBERT-headliner. Run with:

```bash
python3 Orchestrator3.2.py
```

### Body source

This version requires NewsWatcher3 to expose the article body to its callback. NW3's callback payload now includes a fifth key, `article_body`, sourced from the raw RTPR `data` dict (already stored intact in `_news_objects`). Orchestrator3.2 hands the dict directly to `FinBERTBodyPipeline.process()`, whose `FIELD_NAME` constant is `article_body`, so no key renaming is needed.

### Body sentiment definition

`body_sentiment` in the daily TSV is `result['finbert']['ticker_sentiments'][symbol]['overall_sentiment_score']` — the mean of every sentence's `sentiment_score` (`positive − negative`) for that ticker, computed by `SentimentAggregator.build_output()` in `FinBERT-analysis.py`. One value per TSV row; if the body NER never resolved this ticker (or the body was empty), the cell is empty.

### Concurrency and model preloading

`FinBERTBodyPipeline` is **not thread-safe** (shared spaCy nlp, fastcoref model, FinBERT ONNX session, `TickerResolver` cache). Each body worker thread owns its own pipeline instance, loaded **once at startup** — never per article.

**How preloading works:**
- `_body_executor = ThreadPoolExecutor(max_workers=BODY_FINBERT_WORKERS, initializer=_init_body_worker)` — `initializer` is a Python guarantee: it runs exactly once per worker thread before the thread picks up any task.
- `_init_body_worker()` constructs a `FinBERTBodyPipeline` and calls `load_models()` (spaCy, fastcoref, ONNX FinBERT, SEC-EDGAR ticker map), then stores the result in `threading.local()`.
- `main()` submits `BODY_FINBERT_WORKERS` no-op tasks and waits for all of them with `_futures_wait()` **before** `nw.start()`. This forces all N worker threads to start and run their initializers so the firehose only opens once every worker is warm.
- `_get_body_pipeline()` is a one-liner (`return _body_local.pipeline`) — no lazy checks needed.

Each loaded pipeline costs ~1.5–2 GB RSS, so `BODY_FINBERT_WORKERS=2` adds ~3–4 GB at startup. Startup will take ~30–60 s × N workers, then every article hits a warm pipeline with no cold-start penalty. The body executor is separate from the FinBERT-headliner pool so neither starves the other.

### Trigger gate

`evaluate_trigger` and `maybe_launch_trade_mole` are **unchanged** — body sentiment is informational. The trade_mole launch still hinges on headline `sentiment_score > TM_SENTIMENT_SCORE_MIN` and `Float < TM_FLOAT_MAX_M`. `_collect_and_log` resolves the headline future before the body future, so a slow body never delays the trigger decision.

### TSV columns

```
['Symbol', 'Tickers', 'ID', 'ArrivalTime', 'Headline', 'Author',
 'Float', 'FinBERTCompletedAt',
 'positive', 'negative', 'neutral', 'sentiment_score', 'label',
 'body_sentiment', 'BodyCompletedAt',
 'Trigger']
```

`csv.DictWriter(extrasaction='ignore')` is in place, so existing daily TSVs from 3.1 keep working — only files written by 3.2 carry the two new columns.

### Body-pipeline outputs

When `BODY_FINBERT_WRITE_OUTPUTS=True`, the pipeline writes per-stage JSON (`<stem>_cleaned.json`, `_pronouns.json`, `_sentences.json`, `_NER.json`, `_FinBERT.json`) per article to `BODY_FINBERT_OUTPUT_DIR` (default `body_finbert_outputs/` next to `Orchestrator3.2.py`). Each pipeline instance creates the directory on construction. Disable by flipping `BODY_FINBERT_WRITE_OUTPUTS=False` if disk pressure becomes an issue.

### Body config knobs

All in the `BODY_FINBERT_*` block at the top of `Orchestrator3.2.py`:

- `BODY_FINBERT_OUTPUT_DIR`
- `BODY_FINBERT_WORKERS` (default 2)
- `BODY_FINBERT_COREF_MODE` (`'full'` = fastcoref, `'simple'` = pronoun-only fallback)
- `BODY_FINBERT_WRITE_OUTPUTS`
- `BODY_FINBERT_TIMEOUT_SEC` (per-article ceiling when `_collect_and_log` resolves the body future)

### Dependencies (additional vs 3.1)

- `FinBERT_body_pipeline` — `path/to/N2/scripts/FinBERT_pipeline/`
- Sibling scripts pulled in by the body pipeline: `jsonCleaner`, `pronounCer`, `SentenceSplitter`, `NerSecDictionary`, `FinBERT/FinBERT-analysis.py` (uses ONNX INT8 model — run `python3 FinBERT-analysis.py --export` once if `finbert_onnx/` is not yet exported).
- Python deps: `spacy` + `en_core_web_sm`, `fastcoref` (optional but required for `coref_mode='full'`), `transformers`, `optimum[onnxruntime]`.

---

## Orchestrator4.0 (clerk hand-off)

`Orchestrator4.0.py` replaces the legacy trade-mole subprocess launcher with a TCP/JSON hand-off to `clerk-1.1.py` (the warm ibapi client pool in `path/to/N2/scripts/x-wing-mole/`). The FinBERT-headliner + noCoref body pipeline is identical to `Orchestrator3.5.py`. Beyond the trade trigger, this version moves the clerk **arm onto the raw WS alert** (pre-fetch) and adds a ticker pre-filter in NewsWatcher4 so `reqMktData` starts without waiting on the article-body curl (see *Arm-on-alert & NW4 pre-filter* below). Run with:

```bash
python3 Orchestrator4.0.py        # the clerk must already be running (see below)
```

### Two-step handshake (replaces `maybe_launch_trade_mole`)

The clerk needs both a mole surge **and** a sentiment confirmation before it places an order. The orchestrator drives this with two independent TCP messages to `CLERK_HOST:CLERK_PORT` (default `127.0.0.1:8765`), one JSON object per line, one short-lived connection each (`_send_clerk()`):

- **STEP 1a — ARM (`_arm_clerk`)**: now fires **on the raw WS alert, before the article body is curled** — NewsWatcher4 invokes the registered `register_alert_callback(_on_alert)` the instant an alert's primary ticker passes its cheap pre-filter. `_on_alert` submits `_arm_clerk` on the dedicated `_clerk_executor` (off the NW4 loop thread) so the clerk starts `reqMktData` ASAP, and **stashes the arm future in `_arm_results[(art_id, ticker)]`**. Payload `{"ticker", "lastDailyClose", "itiBaseline", "tradeSizeBaseline"}` — one message **per pre-filter-passing ticker**. When the article is later accepted, `on_news_accepted` **reuses the stashed future** for pre-armed tickers and arms only *new* in-universe partner tickers found in the full (scraped) ticker list. A pre-armed ticker that ends up excluded (ORCH `excluded_strings-2.txt`), dropped before acceptance (curl/normalize failure, dedup, full-filter block), or absent from the final ticker list is **released** (`_on_alert_release`/`_release_armed` → Sentiment `BAD`) so its warm client returns to the pool.
- **STEP 1b — SENTIMENT (`_send_sentiment`)**: fires from `_collect_and_log` after FinBERT, `{"ticker", "Sentiment": "OK"|"BAD"}`. `_collect_and_log` **waits on the arm future first** (created back on the alert, so it has almost always resolved by now) — the clerk must have registered the session for both the OK gate and the BAD early-release. A `rejected` arm (`no_free_client`) skips STEP 1b; a pre-armed-but-excluded ticker is released here with `BAD` instead of confirming.

### Arm-on-alert & NW4 pre-filter (latency fix)

NewsWatcher4 is alert+curl: a lightweight WS alert arrives, then NW4 HTTP-curls the article body before filtering. Previously the arm fired only **after** that curl, so a saturated fetch pool during top-of-hour PR bursts delayed `reqMktData` by ~10 s (root cause of the NVNI `nGNX50PnTT` case). Three NW4 changes (in `NewsWatcher4.py`) fix this:

- **Pre-filter before the curl** — `_handle_alert` runs `_passes_filters([alert_ticker], '', '')` (universe + blacklist + price/float on the alert's single ticker; empty title/exchange) and **only curls + arms gate-passers**. This slashes burst fetch volume (~78 → ~5–10 curls) and bounds how many warm clients arming consumes. Trade-off: the alert carries only one ticker (the full list is scraped post-curl), so an article whose *only* in-universe ticker is a body-only partner is dropped. On a sampled day this dropped 13/225 accepted PRs — all out-of-strategy foreign/dual-listings (float >50M or price >$10) that previously slipped through NW4's `_passes_filters` **exchange-skip** branch; the empty-exchange gate enforces float/price and tightens that loophole. Zero in-strategy PRs were lost.
- **Arm/release callbacks** — `register_alert_callback(fn)` fires `fn(ticker, art_id, recv_ts)` on a gate-pass (pre-fetch); `register_alert_release_callback(fn)` fires `fn(ticker, art_id)` when a pre-armed article is dropped before acceptance. The accepted-article payload now also carries `prearmed` (list) + `art_id` so the consumer can dedup arms and key its stash.
- **ArrivalTime at receipt** — stamped when the WS frame arrives (`_ws_loop`) and threaded through `_handle_alert`→`_handle_article`, so a slow curl no longer inflates it. Fetch pool widened: `MAX_CONCURRENT_FETCHES` 8→32, `HTTP_POOL_LIMIT` 16→32 (later 32→64; see *Burst receive-lag fix* below).

### Burst receive-lag fix (off-loop HTML scrape)

A top-of-hour burst stacks **two independent latencies**, told apart by TSV columns:
- **Receive lag** `Created → ArrivalTime` — the article's publish time → the WS-receipt stamp (`recv_ts` in `_ws_loop`).
- **Fetch lag** `ArrivalTime → CurlTime` — the fetch backlog (semaphore + curl + normalize); `CurlTime` is stamped in the orchestrator when the accepted article arrives, so this span is the full alert→body-ready fetch, never "milliseconds."

The 2026-06-15 08:00 burst (~60 PRs in ~4s) showed receive lag ramping to ~2.2 s. Root cause: NW4 ran its **entire** hot path on the single asyncio loop thread (no `run_in_executor`). The per-alert regex scrape **`_normalize_article`** ran inline, blocking the loop for its full duration, so `_ws_loop` could not drain the `websockets` receive buffer and `recv_ts` stamped late. The lag is **load-dependent** (ramps through the burst, drops to ~0.3 s for a late isolated PR) ⇒ our consumer, not RTPR.

**Fix (in `NewsWatcher4.py`):** `_normalize_article` is offloaded to a dedicated `_cpu_executor` (`ThreadPoolExecutor`, `CPU_POOL_WORKERS=4`, created in `start()`, drained in `stop()`) via `await loop.run_in_executor(...)`. Because the regex holds the GIL, threads do **not** add CPU throughput — but the GIL preempts every ~5 ms, so the loop reclaims control and keeps stamping `recv_ts` / firing arms instead of blocking for the whole scrape. The cheap pre-filter + `_emit_alert_arm` stay **on the loop** (the arm still fires pre-fetch, ASAP); the lean `create_task` reader and the `inflight`-set shutdown drain are unchanged; the `(art_id,ticker)` arm contract is untouched (no orchestrator change). `_normalize_article` takes no locks, so moving it off-loop is safe. True multi-core (`ProcessPoolExecutor`, `NORMALIZE_PROC_WORKERS`) is a documented, **measurement-gated** next step — warranted only if `[Timing]` `normalize` shows the scrape still saturating a core (it alone justifies the pickling cost; the receive and curl paths are I/O-bound and gain nothing from their own CPU/process).

**Instrumentation:** `[RecvLag]` lines in `_ws_loop` pair `recv_lag` (alert `article_published_at` → `recv_ts`) with `len(inflight)` — a ramp tracking `inflight` proves loop saturation vs flat = RTPR upstream. Logs INFO above `RECV_LAG_WARN_SEC` (1.0 s), else DEBUG; never raises (missing/malformed timestamp → `recv_lag=NA`). The existing `[Timing]` line's `normalize` field now also includes the executor round-trip, so it surfaces time a scrape spent queued behind the pool. Fetch pool is now `MAX_CONCURRENT_FETCHES`/`HTTP_POOL_LIMIT` = 64. New NW4 knobs (top of `NewsWatcher4.py`): `CPU_POOL_WORKERS`, `RECV_LAG_WARN_SEC`.

### Sentiment gate

`OK` iff headline FinBERT `positive >= SENTIMENT_POSITIVE_MIN` (0.2) — the sole criterion. A missing/failed FinBERT positive → `BAD`. `evaluate_sentiment()`.

### clerk-1.1.py change

`clerk-1.1.py` was patched so a `Sentiment:"BAD"` message immediately `finish()`es the duo and returns the warm client to the pool (reply `sentiment_bad_released`) instead of holding it for the full ~60 s watch window. `"OK"` → `sentiment_ack`; no active session / other → `sentiment_ignored`.

### Universe lookups

The daily `stocks_universe_YYYY-MM-DD.tsv` adds `LastDailyClosePrice`, `RTH_tradeSize`, `ETH_TradeSize` (note: **inconsistent casing** — lowercase `t` in RTH, uppercase `T` in ETH). New helpers `_lookup_last_close`, `_lookup_baseline_trade_size` mirror `_lookup_baseline_iti` (RTH 09:30–16:00 ET via `_is_rth_now()`, else ETH). All lookups route through `_coerce_float`, which maps blanks / NaN / the literal `skipped_nan` string (~13% of rows) to `None`/sentinel without raising. Missing trade-size/ITI → `44444` sentinel (trade-mole treats it as "no baseline"); missing close → `null` (x-wing runs without its optional entry-price cap).

### TSV columns

Drops `Trigger`; adds `LastDailyClose`, `itiBaseline`, `tradeSizeBaseline` (what was sent), `Sentiment` (OK/BAD), `ClerkArm` (`accepted:<clientId>` / `rejected:<reason>` / `skipped:excluded_string` / `skipped:not_armed`), and `ClerkSentiment` (`sentiment_ack` / `sentiment_bad_released` / `sentiment_ignored` / `skipped:*`). With arm-on-alert, `sentiment_bad_released` now also appears when a pre-armed ticker is released because its headline was excluded. `_append_to_tsv` auto-rotates on header mismatch.

### Config knobs (top of `Orchestrator4.0.py`)

`CLERK_HOST`, `CLERK_PORT`, `CLERK_TIMEOUT_SEC`, `CLERK_ARM_WAIT_SEC`, `SENTIMENT_POSITIVE_MIN`, `TRADE_SIZE_SENTINEL`, `DEFAULT_BASELINE_ITI`, `UNIVERSE_DATA_DIR`, `ORCH_EXCLUDED_STRINGS_FILE`. The `TM_*` subprocess/surge knobs are gone — surge thresholds now live in trade-mole-2.1 inside the clerk.

### Dependencies (vs 3.5)

- `clerk-1.1.py` — `path/to/N2/scripts/x-wing-mole/` (start it first; it loads `x-wing-2.0.py` + `trade-mole-2.1.py` and connects its own ibapi pool). Start example:
  ```bash
  path/to/venv/bin/python clerk-1.1.py --client-qty 5 --port 4002 --listen-port 8765
  ```
  `--port 4001` is the **LIVE** Gateway (real money); use `4002` (paper GW) for testing.
- The orchestrator no longer spawns `trade_mole_*.py` or opens any ibapi connection of its own.
