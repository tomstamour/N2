# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A single-file IBKR Level-1 trade-frequency surge detector (`trade_mole.py`). It subscribes to real-time tick-by-tick data for one symbol, maintains rolling windows (1/2/3/4/5/10s) in a bounded deque, compares them against an externally supplied historical baseline trade rate (`--baseline-trade-per-second`), runs three independent surge-detection rules on each trade, and writes a wide CSV (~50+ columns) on shutdown.

Note: `USERGUIDE_trade_surge_mole.txt` refers to the script as `ibkr_trade_surge.py` — the actual filename is `trade_mole.py`. Treat the userguide as the authoritative spec for behavior (windows, surge rules, column set).

## Run

```bash
# Paper TWS, auto-named file in ./outputs/
python trade_mole.py --symbol AAPL --clientID 7 --lifeTime 15:00 \
    --output ./outputs/ --baseline-trade-per-second 5.0

# Live IB Gateway, explicit file path, illiquid microcap baseline
python trade_mole.py --symbol SOFI --clientID 3 --lifeTime 05:00 \
    --output /tmp/SOFI_premarket.txt --port 4001 --baseline-trade-per-second 0.05
```

`--baseline-trade-per-second` is required and must be > 0. It is a historical (externally pre-computed) baseline trade rate used as the denominator for surge-detection acceleration ratios; the implied baseline avg ITI is `1/baseline`.

Ports: `7497` paper TWS, `7496` live TWS, `4002` paper Gateway, `4001` live Gateway. `--lifeTime` is `mm:ss`. `--output` resolves to a directory (auto-name `SYMBOL_YYYY-MM-DD_HH-MM.txt`) or a literal file path. Content is CSV regardless of `.txt` extension.

Requires `ibapi` and `pandas`; a running TWS or IB Gateway with API enabled.

## Architecture notes that aren't obvious from the code

**Threading model.** All `ibapi` callbacks fire on the `EClient.run()` thread. State mutation is confined to that thread; the main thread only reads `self.records` *after* `disconnect()`. No locks are used — preserve this invariant if you touch the callbacks.

**Hot-path discipline in `tickByTickAllLast`.** The callback is intentionally lean: `perf_counter` timestamp → append+trim the 10s deque → single O(n) bucketing scan into all windows (`_compute_windows`) → surge check → dict build. No pandas, no I/O, no locks. DataFrame construction happens once at shutdown. Don't add per-trade logging, pandas ops, or blocking calls here.

**Three surge rules (`_detect_surge`), live from trade #1 (no warmup):**
- **A. Rate jump** — `trades_in_1s ≥ 2` AND `rate_1s / hist_baseline ≥ 5×` (instant ignition).
- **B. Sustained burst** — `trades_in_5s ≥ 5` AND `rate_5s / hist_baseline ≥ 3×` (slower builds).
- **C. ITI collapse** — current inter-trade time < 2s while `hist_baseline_avg_iti > 10s` (dead-tape-to-live pattern; critical for illiquid pre-market microcaps).

Any rule tripping fires a surge; `surge_reason` concatenates all tripped rules.

**Baseline is external.** `WINDOWS = [1,2,3,4,5,10]` is the user-facing set; the baseline rate used as the denominator for surge ratios and rule thresholds is supplied via `--baseline-trade-per-second` (constant for the run).

**Three parallel subscriptions, by design:**
- `reqTickByTickData("AllLast")` — every trade print (incl. odd lots); primary signal.
- `reqTickByTickData("BidAsk")` — every NBBO change; supplies bid/ask/spread/midprice/microprice snapshot at each trade.
- `reqMktData(..., "233,236,293,294,295,318,375,165,221")` — generic ticks for halt/shortable/mark/last-RTH/TWS-smoothed rates + RTVolume string parse.

Quote state is maintained as running fields (`_last_bid`, `_last_ask`, etc.) so each trade record embeds a quote snapshot without joining.

**Microprice** uses the Stoikov weighting: `(bid·ask_sz + ask·bid_sz)/(bid_sz+ask_sz)` — opposite-side sizing, so heavy ask pressure pulls fair value toward the ask.

## Operational gotchas

- **`clientID` must be unique per TWS/Gateway connection.** A collision with another running script can fail silently.
- **Pre-/after-hours microcaps** have 30–120s baseline ITIs; supply a small `--baseline-trade-per-second` (e.g. `0.01`–`0.05`) so Rule C's ITI-collapse threshold reflects the true historical regime.
- **Tick 236 (Shortable) updates infrequently**; mid-session SSR restrictions are not surfaced here (SSR is a separate SIP indicator).
