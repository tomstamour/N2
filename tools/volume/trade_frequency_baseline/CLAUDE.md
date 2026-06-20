# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Four single-file IBKR baseline scripts that measure trade frequency and emit a "plug these into `ibkr_trade_surge.py`" (or "post-trigger calibration reference") footer. The output is consumed by the sibling `../trade_surge_mole/trade_mole.py` surge detector (the `pastWindows_…`/`pre_…` footer sets `SURGE_PRIOR_ITI_MIN` and a `trades/min` surge threshold = `baseline_rate × 5`; the `futureWindows_…` footer is a calibration reference for *post-trigger* expectations rather than a direct drop-in).

The scripts are **complementary, not alternatives** — they differ on two axes: (1) time direction from the anchor — PAST vs FUTURE window, and (2) resolution — bar-approximated vs true per-tick ITI:

| Script | API | Time direction | What it samples | ITI resolution |
|---|---|---|---|---|
| `pastWindows_trade_frequency_baseline.py` | `reqHistoricalData` (TRADES bars, `.barCount`) — 1 call | PAST | Same intraday window **ending** at anchor, across N past trading days | Approximated: `bar_sec / barCount` |
| `pre_trade_frequency_baseline.py` | `reqHistoricalTicks` (TRADES) — 1 call (opt-in 2 via `--cross-session`) | PAST | Last N ticks ending at `--startTime` today or at "now" | True per-tick diffs, but **integer-second** timestamps |
| `futureWindows_trade_frequency_baseline.py` | `reqHistoricalData` (TRADES bars, `.barCount`) — 1 call | FUTURE | Intraday window **starting** at anchor, across N past trading days | Approximated: `bar_sec / barCount` |
| `futureWindows_tick_trade_frequency_baseline.py` | `reqHistoricalTicks` (TRADES) — **N parallel calls**, one per past day, each on its own clientID | FUTURE | Up to `--ticks-quantity` trades per past day ending at (anchor + windowMin) | True per-tick diffs, but **integer-second** timestamps |

Three of the four are single-request, sub-1s wall clock (~300–700 ms). `futureWindows_tick_…` is the exception by design: it fans out N connections (default 10) to keep wall time close to one round-trip (~500 ms – 2 s) despite being N requests. Do not add pagination or multi-request loops to the single-request scripts without strong reason — it breaks their latency contract. The only other sanctioned exception is `pre_trade_frequency_baseline.py`'s opt-in `--cross-session true` (max 2 total calls, ~1s worst case) when the first fetch under-fills near session open.

## Run

```bash
# Multi-day baseline: RTH window across last 10 trading days
python pastWindows_trade_frequency_baseline.py --symbol MARA --clientID 10

# Multi-day baseline: trigger mode (last 30 min before "now", per past day, ETH included)
python pastWindows_trade_frequency_baseline.py --symbol RAYA --clientID 711 \
    --on-trigger true --port 4001 --log ./logs/RAYA.log

# Pre-trigger tick baseline: last 1000 ticks ending "now"
python3 pre_trade_frequency_baseline.py --symbol MARA --clientID 21 --on-trigger true

# Pre-trigger tick baseline: explicit upper bound, auto-named CSV in ./outputs/
python3 pre_trade_frequency_baseline.py --symbol MARA --clientID 22 \
    --startTime 09:45:00 --ticks-quantity 500 --output ./outputs --port 4001

# Pre-trigger tick baseline at pre-market open: fall back to previous session if under-filled
python3 pre_trade_frequency_baseline.py --symbol MARA --clientID 22 \
    --startTime 04:00:05 --ticks-quantity 500 --cross-session true --port 4001

# Future-window bar baseline: 30-min window STARTING at 09:30 across last 10 past days
python futureWindows_trade_frequency_baseline.py --symbol MARA --clientID 30

# Future-window bar baseline, trigger mode: 30 min AFTER "now" per past day
python futureWindows_trade_frequency_baseline.py --symbol MARA --clientID 32 \
    --on-trigger true --port 4001

# Future-window TICK baseline (parallel): 30-min window starting 09:30, clientIDs 40..49
python futureWindows_tick_trade_frequency_baseline.py --symbol MARA --clientID 40

# Future-window TICK baseline, trigger mode: 30 min AFTER "now" per past day, ETH
python futureWindows_tick_trade_frequency_baseline.py --symbol MARA --clientID 60 \
    --on-trigger true --log ./logs/MARA_future_tick.log
```

Ports: `7497` paper TWS, `7496` live TWS, `4002` paper GW, `4001` live GW. Requires `ibapi` and `pandas` plus a running TWS/Gateway with API enabled. See `commands.txt` for more invocations.

## Key conventions shared by all scripts

**`--on-trigger true`** is the "run this right before arming the surge detector" mode. It overrides `--startTime`/`--endTime`, anchors the window to "now" ET, and forces `useRth=0` (ETH included) — because news-driven ignitions typically occur outside RTH.

**`--output` path resolution.** If the arg is a directory (exists, or ends with `/`), the script auto-generates a file inside it; otherwise the arg is used as a literal file path. Example: `--output ./outputs` → `./outputs/MARA_pre_2026-04-23_21-09.csv` (for `pre_…`). A literal `./foo.csv` is written as-is.

**`--log <path>` tees stdout+stderr** (via `_Tee`) to a file in addition to the console — use this for capturing the "plug into ibkr_trade_surge.py" footer for later paste. The raw ibapi protocol chatter also lands in this log.

**`clientID` must be unique** per TWS/Gateway connection. A collision with another running script can fail silently (the API accepts the connect but never fires `nextValidId`; `CONNECT_TIMEOUT=10` will trip).

## Architecture notes that aren't obvious from the code

**Threading model.** All `ibapi` callbacks fire on the `EClient.run()` thread started as `daemon=True, name="ibapi-run"` (or `ibapi-run-{clientID}` in `futureWindows_tick_…`, which spawns one per connection). State (`self._bars` / `self._ticks`, `_error`, events) is mutated only on that thread. The main thread reads those lists *after* `app.disconnect()` and `api_thread.join()`. No locks are used — each app instance owns its own state, so parallel apps don't contend. Preserve this invariant if you touch the callbacks.

**Blocking via `threading.Event`.** `connected_event` is set in `nextValidId`; `_done_event` is set in `historicalDataEnd` / `historicalTicksLast(done=True)` / inside `error()` for a matching `reqId`. `fetch_*` just does the request then `_done_event.wait(FETCH_TIMEOUT=5)`. Timeout is logged but not raised — the script proceeds with whatever arrived.

**Duration padding in `pastWindows_…`.** `durationStr = f"{args.days + 4} D"` pads +4 calendar days over requested trading days so weekends/holidays don't truncate the sample. IBKR then walks backward from `endDateTime` and we window-filter by `tod_sec` on the client side (`_parse_bar_date`).

**`pre_trade_frequency_baseline.py` integer-second caveat.** `HistoricalTickLast.time` is in **integer unix seconds**. On fast tapes (>1 trade/sec), many consecutive ticks share a timestamp and their diffs collapse to 0 — so `iti_min_sec` and `iti_median_sec` can legitimately be 0 while `iti_mean_sec` (= `span_sec / (n_ticks - 1)`) stays correct. That's why the footer uses `iti_mean_sec` for `SURGE_PRIOR_ITI_MIN` rather than median. If you need sub-second ITI resolution, switch to live `reqTickByTickData` (as in `../trade_surge_mole/trade_mole.py`).

**`numberOfTicks` is hard-capped at 1000 per request** by IBKR for `reqHistoricalTicks`. `pre_trade_frequency_baseline.py` clamps and warns rather than paginating above that cap — pagination-to-fill would blow the <1s latency target. The separate `--cross-session` opt-in is NOT pagination around this cap; it's a single extra request to step over the overnight session gap, and each call is still clamped to 1000 ticks.

**ITI in bar mode is approximated**, not measured. In `pastWindows_…` the per-bar ITI is `bar_sec / barCount`, computed only over bars with `barCount > 0`. `iti_mean_sec` is `window_sec / total_trades` (true average over whole window), while `iti_{median,min,max}` come from the per-bar rates (intra-window variability). These two families of stats have different denominators by design — do not "fix" them to be consistent.

**`futureWindows_tick_…` parallel fan-out.** Unique in this directory: opens `--days` concurrent IBKR connections with consecutive clientIDs `[base, base+days-1]` (where `base = --clientID`). Each connection is a separate `FutureTickApp` instance (its own `EClient`, own `threading.Event`s, own `_ticks` list). Main thread creates and connects them serially, then fires all `reqHistoricalTicks` calls back-to-back without waiting, then waits for each app's `_done_event` in sequence. Total wall time is bounded by the slowest single round-trip rather than summing them. Per-day request state is fully isolated per app — no cross-reqId dispatch logic needed. `partial_coverage=True` on a row means the 1000-tick cap bit *and* the earliest received tick is after the window start (likely missed earlier in-window trades).

**Informational error codes are filtered:** `2104, 2106, 2107, 2158, 2100, 2108, 2119, 2176`. A real error on the script's `reqId` records `self._error` and sets `_done_event` so the fetch unblocks instead of hanging to timeout. (`2176` is a fractional-share-rules warning emitted by IBKR when the installed `ibapi` client is older than v163; it trims a metadata value but does not affect the trade bar/tick stream — without filtering it, the handler aborts the fetch before any bars are appended, which surfaces as `0 bars in ~280 ms` for symbols like CAST.)

## Operational gotchas

- **Running this before RTH with default `--startTime=09:30:00`** will yield `n_ticks=0` or `n_trades=0` (no trades before 09:30 ET on an empty session). Use `--on-trigger true` or an explicit pre-market `--startTime`.
- **`reqHistoricalTicks` with empty `startDateTime` is session-bounded.** It walks backward from `endDateTime` but does NOT cross overnight gaps. Asking `pre_trade_frequency_baseline.py` for 50 ticks at `--startTime 04:00:05` (5s into today's pre-market open) returns only the few ticks printed since session open. Pass `--cross-session true` to opt into ONE additional `reqHistoricalTicks` call whose `endDateTime` is 1s before the oldest tick (landing in the previous session). Hard-capped at 2 requests total — deeper walk-back is not supported by design.
- **The footer's surge threshold is `rate × 5` trades/min**, matching Rule A's 5× ratio in `trade_mole.py`. If you retune that ratio upstream, update the footer formula here too.
- **Output file extension is not forced** — `--output ./foo.txt` writes CSV content into a `.txt` file. The auto-name path uses `.csv`.
- **`futureWindows_tick_…` clientID range.** `--clientID` is the *base* of a contiguous range `[base, base+days-1]`. If any ID in that range is held by another running script (another baseline run, `trade_mole.py`, a TWS chart, etc.), that connection will silently fail to fire `nextValidId` within `CONNECT_TIMEOUT`, and that day's row will show `n_trades=0`. Pick a base that leaves enough headroom. Other days still produce data — one bad connection doesn't abort the run.
