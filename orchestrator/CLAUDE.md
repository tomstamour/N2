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

- `NewsWatcher2` — `/home/tom/Documents/ibkr_scripts/N1/scripts/newswatcher2/`
- `yfinance_stock_universe` — `/home/tom/Documents/ibkr_scripts/N1/scripts/universe_finder/`
- `pre_trade_frequency_baseline` — `/home/tom/Documents/ibkr_scripts/N1/scripts/volume/trade_frequency_baseline/` (requires IBKR Gateway/TWS)
- `trade_mole.py` — `/home/tom/Documents/ibkr_scripts/N1/scripts/volume/trade_surge_mole/` (CLI; spawned as detached subprocess on qualifying news)

## Integration Context

Orchestrator sits at the top of the pipeline:
`universe_finder` → `newswatcher2` → **Orchestrator** → `FinBERT` / `NerSecDictionary` / `pronounCer`
