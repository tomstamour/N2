# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`X-wing-1.0.py` is a standalone IBKR (`ibapi`) trading script that takes a **long** position on a
single ticker and protects it with a **yield-laddered trailing stop**. The stop's tightness is driven
by an external lookup table that maps the position's yield to `Trigger(%)`/`Limit(%)` offsets.

- **One process trades ONE symbol.** To trade ≥10 symbols, run ≥10 processes (see the fleet launcher).
- **Never uses `reqTickByTickData`** (IBKR caps tick-by-tick at ~3 concurrent requests, which would
  break a 10-symbol fleet). Uses streaming Level-1 `reqMktData` instead, sampled once per second.
- **Never uses `reqGlobalCancel`** — it cancels orders for the *entire account*, which would wipe out
  the other instances in the fleet. Only this instance's own orderIds are ever touched.
- **Orders are MODIFIED (replaced), not cancelled** — the same `orderId` is reused via `placeOrder` to
  minimise latency between trailing-stop adjustments.

The directory also ships a fleet launcher/stopper and example config/tables.

## ⚠️ Critical facts (easy to get wrong)

- **Interpreter:** use **`/home/tom/venv/bin/python`**. The system `python3` does **not** have `ibapi`
  installed. Installed version is **ibapi 9.81.1** — the code depends on its specific signatures:
  - `error(self, reqId, errorCode, errorString)` is **3-arg** (override keeps a defaulted 4th param).
  - `Order.eTradeOnly` / `Order.firmQuoteOnly` **default to `True`** in 9.81 and IBKR will **reject**
    such orders → the code forces both to `False` in `_base_order()`. Do not remove this.
  - `reqMktData(reqId, contract, genericTickList, snapshot, regulatorySnapshot, mktDataOptions)` —
    streaming call is `reqMktData(1, contract, "", False, False, [])`.
  - Unset price sentinel is `1.7976931348623157e+308` (`UNSET_DOUBLE`); ticks at/below 0 are ignored.
- **`--port` defaults to `4002` (paper IB Gateway).** `4001`/`7496` are LIVE — the script logs a
  real-money warning, and the fleet launcher requires typing `LIVE` to proceed.
- **Synthetic stop = the resting broker order IS the crash net.** If the script dies, the SELL STP LMT
  it left at the broker still protects the position. Don't "optimise" it into a purely in-memory stop.

## Architecture

Two combined `EClient`+`EWrapper` clients per instance, each running its message loop on its own
daemon thread (`client.run()`), both feeding one shared `XWingController` (guarded by an `RLock`):

| Client | clientId | Responsibilities |
|--------|----------|------------------|
| `OrderClient` | `--client-id-base` | `reqContractDetails` (resolve contract + `minTick`), `nextValidId`, `placeOrder`/replace, `orderStatus`, `execDetails` |
| `DataClient`  | base + 1 | one streaming `reqMktData`; `tickPrice` maintains latest BID(1)/ASK(2)/LAST(4)/CLOSE(9) |

The **main thread** runs `control_loop()` at a 1 Hz cadence (configurable via `--loop-interval`). A
`threading.Event` named `wake` lets fill callbacks interrupt the loop's sleep so the protective order
is (re)armed near-instantly after a fill instead of waiting up to a full second.

Key code map (all in `X-wing-1.0.py`):
- `XWingController` — all trading state + the order/quote logic.
  - `place_entry()` / `place_reentry()` — BUY LMT orders.
  - `compute_protective_levels(mid)` — **single source of truth** for the stop levels: tracks
    `high_water_mid` and **ratchets** `aux`/`lmt` (only move up; hold when price falls). Used by both
    the loop's `mid ≤ aux` trigger check and the trail arming, so they can't diverge.
  - `arm_or_replace_protective(mid, aux=, lmt=, force_exit=, exit_lmt=)` — places/replaces the
    **single** resting SELL order (same `orderId` = modify, never cancel). Trail path takes the
    precomputed ratcheted `aux`/`lmt`. `force_exit=True` converts it to a SELL LMT: at `exit_lmt` (the
    held table limit, for a synthetic stop trigger) or, when `exit_lmt` is None, the marketable
    bid-cross price (`_exit_cross_price()`) used to flatten at termination.
  - `on_order_status()` — tracks fills, position, running/frozen avg fill, queues re-entry.
  - `on_order_error()` — the type-change-modify-rejected fallback (cancel + new), see Gotchas.
  - `current_mid()` — BID/ASK midpoint, falling back to LAST then CLOSE.
  - `_exit_cross_price()` — marketable SELL price = `bid × (1 − --exit-cross-percent/100)`; falls back
    bid → mid → last → close.
  - `shutdown()` — flatten @ bid (if long), `cancelMktData`, disconnect both clients. **No
    `reqGlobalCancel`.**
- `LimitsTable` — loads the TSV, banded yield lookup.
- `PriceActionWriter` — appends the per-second price-action TSV (`csv.DictWriter`, locked).
- Contract is `STK / SMART / USD`; orders are `tif="GTC"`, `outsideRth=True`, `transmit=True`.

## Strategy / control-loop phases

Phase constants: `PHASE_INIT`, `PHASE_ENTRY_PENDING`, `PHASE_ENTRY_PARTIAL`, `PHASE_LONG_MONITOR`,
`PHASE_EXITING`, `PHASE_RE_ENTRY_PENDING`, `PHASE_FLATTEN`, `PHASE_SHUTDOWN`.

1. **Entry:** place a BUY LMT at `--Entry-limit-price` for `floor(--capital / entry)` shares.
2. **Arm protective:** on each (partial) fill, arm a SELL STP LMT sized to the filled qty. The first
   entry's average fill is **frozen** as `original_avg_fill` once the buy is fully filled.
3. **Trail (every second, RATCHETING):** `compute_protective_levels(mid)` tracks a **high-water mid**
   and computes `aux`/`lmt` off it, then **ratchets** — the stop only ever moves **UP** and **HOLDS**
   when price falls (it never floats down with the price). The resting STP LMT is **replaced** (same
   orderId) only when the ratcheted level rises; while held, the redundant-replace skip means no churn.
   This makes it behave like a true native stop-limit (it actually triggers when price drops to `aux`).
4. **Synthetic trigger:** when `mid ≤ aux` (now reachable because `aux` is held, not recomputed below
   the live mid), replace that same order into a **SELL LMT at the held table `lmt`** (`exit_lmt`) —
   textbook stop-limit semantics. Since `lmt < aux ≤ bid`, that limit is marketable in normal
   conditions; if price gaps below it the order is **left resting** (by design — spec step 5). This
   software trip backs up native stop triggering, which is unreliable in ETH. One order throughout =
   no oversell. (Termination/flatten still uses the aggressive **bid-cross** price, not the table `lmt`.)
5. **Re-entry:** when the protective sell fully fills (position → 0) and not terminating, place a new
   BUY LMT at the **sell fill price** for the same share count, then re-arm the stop using the **same
   frozen `original_avg_fill`** (the yield reference never changes across cycles).
6. **Termination:** on `--lifetime` / `--session-end` / SIGINT / SIGTERM, flatten any open long with a
   marketable SELL LMT @ bid, then disconnect.

## Limits table (`--input-limits-table`)

Tab-separated. Required columns: **`Yield (%)`**, **`Trigger(%)`**, **`Limit(%)`**. Any
`auxPrice ($)`/`lmtPrice ($)` columns are **illustrative only and ignored** — the script recomputes
prices from the live midpoint. See `example-yield_vs-stopLimits.tsv`.

- **Row selection is banded:** pick the row with the highest `Yield (%)` ≤ the high-water yield. Yields
  below the first row use the first (floor) row; the `0`-yield row is the break-even / loss floor.
- **Price formula** (off the **high-water mid**, then ratcheted — see `compute_protective_levels`):
  - `auxPrice = high_water_mid × (1 − Trigger(%)/100)` — stop trigger
  - `lmtPrice = high_water_mid × (1 − Limit(%)/100)` — stop limit
  - Both are floored at their highest previous value (the stop never moves down) and `lmt` is clamped
    to `≤ aux`. Rounded to the contract `minTick` (from `contractDetails`; default 0.01).
- **Yield** = `(high_water_mid − original_avg_fill) / original_avg_fill × 100`.

Worked example (example file): fill `10.00`, mid `10.00` (0% yield) → `aux 9.90 / lmt 9.85`; mid rises
to `12.00` (+20%, row `20%` Trigger 9 / Limit 10) → `aux 10.92 / lmt 10.80`. If the mid then eases back
to `11.00`, the stop **holds** at `10.92 / 10.80` (does not re-peg down); it only triggers if the mid
falls to `10.92`.

## CLI arguments

Defined in `build_arg_parser()`. Note `--Entry-limit-price` has a **capital E**. (An earlier
`--limit-entry-prc` was dropped as a duplicate of `--Entry-limit-price`.)

| Flag | Req? | Default | Meaning |
|------|------|---------|---------|
| `--symbol` | yes | — | Ticker, e.g. `AAPL` |
| `--Entry-limit-price` | no* | — | Fixed Buy LMT price. Optional fallback; `--max-limit-entry-percent-price` takes precedence |
| `--max-limit-entry-percent-price` | no* | — | Compute Buy LMT off the live ask: `ask × (1 + pct/100)` (fallback ask→last→close) |
| `--last-close-price` | no | — | Previous session close; with `--max-cap-entry-percent` caps the entry limit. Both required together |
| `--max-cap-entry-percent` | no | — | Entry-limit cap = `last-close-price × (1 + pct/100)`; computed entry is lowered to this cap |
| `--capital` | yes | — | Dollar budget; shares = `floor(capital / entry-limit-price)` |
| `--prewarm` | no | off | Two-phase startup: connect/resolve/stream, then wait for `SIGUSR1` (fire) / `SIGTERM` (abort) |
| `--prewarm-timeout` | no | `120` | Seconds to wait in prewarm before auto-aborting; `≤0` = wait indefinitely |
| `--account` | no | default acct | IBKR account ID set on every order (`order.account`) |
| `--input-limits-table` | yes | — | TSV with `Yield (%)`, `Trigger(%)`, `Limit(%)` |
| `--log-dir` | yes | — | Log directory |
| `--price-action-table` | no | `--log-dir` | TSV file path, or a directory (auto-names the file) |
| `--lifetime` | no | off | `mm:ss` run duration; on expiry flatten @ bid and exit. **Counts from FIRE, not prewarm launch** |
| `--session-end` | no | off | `HH:MM` ET; flatten @ bid and exit at/after this time |
| `--client-id-base` | no | `10000` | Order client uses this id; data client uses `+1` |
| `--host` | no | `127.0.0.1` | TWS/Gateway host |
| `--port` | no | `4002` | See port table |
| `--market-data-type` | no | `1` | 1 real-time, 2 frozen, 3 delayed, 4 delayed-frozen |
| `--loop-interval` | no | `1.0` | Control-loop / price-sample interval (seconds) |
| `--exit-cross-percent` | no | `0.5` | Forced-exit SELL LMT price = `bid × (1 − pct/100)` — crosses the spread so the exit fills |
| `--loglevel` | no | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR` |

\* **At least one entry-price source is required**: either `--max-limit-entry-percent-price`
(computed off the ask at entry time) or `--Entry-limit-price` (fixed). `main()` exits with an error
if both are absent, or if only one of `--last-close-price` / `--max-cap-entry-percent` is given.

### Entry-price computation (`compute_entry_limit_price()`)

Evaluated at entry time (the FIRE instant when prewarmed):
```
ref         = ask  (else last, else close)                     # no-ask fallback
entry       = ref × (1 + max_limit_entry_percent_price / 100)
cap         = last_close_price × (1 + max_cap_entry_percent / 100)   # only if both given
final_limit = round_to_tick( min(entry, cap) )                       # cap applied only when set
shares      = floor(capital / final_limit)
```
Worked checks: ask `10.00`, pct `10` → `11.00` (no cap). `--last-close-price 5 --max-cap-entry-percent
15` → cap `5.75`; ask `5.50`, pct `10` → `6.05` → **capped to `5.75`**. Re-entry is unchanged (still
buys at the protective-sell fill price); this rule governs only the *initial* entry.

### Prewarm (two-phase startup)

`--prewarm` splits startup so the slow part (connect → `reqContractDetails` → `reqMktData` → first
tick, ~2 s) happens *before* the trade decision. The instance connects, resolves the contract, and
streams quotes, then sets phase `PREWARM` and blocks on a 0.2 s poll loop until:
- **`SIGUSR1`** → `fire_requested=True` → `place_entry()` fires immediately off the warm ask.
- **`SIGTERM`/`SIGINT`** → abort: clean disconnect, no trade (position is 0, so no flatten).
- **`--prewarm-timeout` elapses** → auto-abort (safety net for a crashed/forgotten parent).

The `SIGUSR1` handler is registered **before** the connect chain so an early fire isn't lost.
*Future:* `Orchestrator3.3.py` launches X-wing `--prewarm` at PR arrival (parallel to FinBERT),
retains the child PID, and sends `SIGUSR1`/`SIGTERM` after the trigger evaluation. Test the flow
locally with `prewarm_test.sh SYMBOL fire|abort|timeout [delay_s]`.

## Port configuration

| Mode | Port |
|------|------|
| IB Gateway — Paper | `4002` (default) |
| IB Gateway — Live | `4001` |
| TWS — Paper | `7497` |
| TWS — Live | `7496` |

## Running

### Single instance (paper)
```bash
/home/tom/venv/bin/python X-wing-1.0.py \
  --symbol AAPL --Entry-limit-price 150 --capital 3000 \
  --input-limits-table example-yield_vs-stopLimits.tsv \
  --log-dir ./logs --price-action-table ./xwing_tables \
  --lifetime 03:00 --port 4002 --client-id-base 10000
```

### Fleet (≥10 symbols)
One process per symbol, with `--client-id-base` auto-incremented by **2** (each instance owns
`base` + `base+1`): 10000, 10002, 10004, …

```bash
cp fleet-config.example.tsv fleet-config.tsv   # edit per-symbol entry price / capital
./launch_x-wing_fleet.sh                        # paper (4002) by default
./stop_x-wing_fleet.sh                           # SIGTERM -> graceful flatten, then SIGKILL leftovers
./stop_x-wing_fleet.sh --force                   # immediate SIGKILL (skips flatten)
```

- `fleet-config.tsv` columns: `symbol  entry_limit_price  capital  [limits_table]` (tab-separated;
  `#` comments allowed). Shared settings are env-var overridable, e.g.
  `LOG_DIR=/data/x PORT=4002 LIFETIME=06:30:00 ./launch_x-wing_fleet.sh fleet-config.tsv`.
- PIDs are recorded in `x-wing-fleet.pids`. The launcher **refuses to start** if that file exists and
  is non-empty (prevents clientId collisions) — stop the fleet or remove the file first.
- `stop_x-wing_fleet.sh` sends **SIGTERM first** so each instance runs its flatten-then-disconnect
  path, waits `GRACE` (default 20s), then SIGKILLs any straggler.

## Outputs

- **Log:** `<log-dir>/x-wing-{symbol}-{YYYY-MM-DD}.log` (file = DEBUG, console = `--loglevel`), format
  `%(asctime)s - %(levelname)s - %(message)s`. Human-scannable status line per second, e.g.:
  `RTH AAPL | mid=10.53 yld=+5.30% | row(5%) 4.0/4.5 | STOP aux=10.11 lmt=10.06 | pos=100 @avg10.0000`
- **Price-action TSV:** auto-named `x-wing-table-{symbol}-{MM}-{HH}-{DD}-{YYYY}.tsv` when
  `--price-action-table` is a directory (month-hour-day-year, per the original spec). Columns
  (`PA_COLUMNS`): `timestamp, symbol, phase, bid, ask, mid, orig_avg_fill, filled_qty, position,
  yield_pct, row_yield_threshold, trigger_pct, limit_pct, aux_price, lmt_price, protective_order_id,
  protective_status, event`.
- Working output dirs in this folder: `logs/`, `xwing_tables/`.

## Key behaviors & gotchas

- **Replace, never cancel:** trailing updates reuse the protective order's `orderId` via `placeOrder`
  (IBKR treats it as a modification). The only `cancelOrder` is the documented fallback below.
- **Type-change-on-modify caveat:** converting a live `STP LMT` into a `LMT` (the exit path) may be
  rejected by IBKR. `on_order_error()` watches `MODIFY_REJECT_CODES` and, only during an exit, falls
  back to a single `cancelOrder` + fresh SELL LMT. Routine trailing never hits this path.
- **No `reqGlobalCancel`, ever** — shared account; it would hit sibling instances.
- **Two exit flavors:** a **synthetic stop trigger** (`mid ≤ aux`) places the SELL LMT at the held
  table `lmt` (stop-limit semantics; rests if price gaps below it — spec step 5). A **flatten** at
  termination uses the aggressive **bid-cross** price instead — priced **below the bid**, not at the
  ask, because a sell limit at the ask is *not* marketable (it joins the offer queue and waits for a
  buyer). The `--exit-cross-percent` haircut below the bid guarantees a marketable flatten, especially
  in thin names. (This was the MTVA flatten-incomplete bug: `SELL LMT @ ask 2.63` never lifted.)
- **ETH liquidity:** even a bid-crossing SELL LMT can rest unfilled in thin pre/post-market (no resting
  bid to hit). By design the script leaves it resting and keeps running rather than chasing further down.
- **Market-data subscription:** if no real-time quote arrives within ~10s and `--market-data-type 1`,
  the script retries with delayed data (type 3). With no quote at all it proceeds (entry is a limit
  order) but the trailing stop can't compute until quotes arrive.
- **Position/avg-fill** are derived from `orderStatus`; `original_avg_fill` is frozen on first full
  entry fill and reused across all re-entries.

## Dependencies

- `ibapi` **9.81.1** (installed in `/home/tom/venv`), `pandas`.
- Standard library: `threading`, `signal`, `logging`, `csv`, `math`, `zoneinfo` (ET session logic).
- External: IB Gateway or TWS running with the API enabled.

## File locations

- **Main script:** `/home/tom/Documents/ibkr_scripts/N2/scripts/x-wing/X-wing-1.0.py`
- **Prewarm test harness:** `prewarm_test.sh` (launch `--prewarm`, then fire/abort/timeout)
- **Fleet launcher:** `launch_x-wing_fleet.sh` — **stopper:** `stop_x-wing_fleet.sh`
- **Fleet config example:** `fleet-config.example.tsv` (copy to `fleet-config.tsv`)
- **Limits table example:** `example-yield_vs-stopLimits.tsv`
- **Original spec:** `x-wing-prompt.txt`
- **Reference IBKR connectors in this repo:**
  - `/home/tom/Documents/ibkr_scripts/et_bot/IBConnector.py` (EWrapper/EClient, fills, disconnect — but
    it calls `reqGlobalCancel`, which X-wing deliberately omits).
  - `/home/tom/Documents/ibkr_scripts/02_sanic_server_TV/` (ib_insync-based webhook trader).

## Testing / verification

```bash
PY=/home/tom/venv/bin/python
$PY -m py_compile X-wing-1.0.py          # compiles clean
$PY X-wing-1.0.py --help                 # lists all args
```

Table-formula unit check (no Gateway needed) — load `LimitsTable` via `importlib` and assert
fill `10.00` → `9.90/9.85` and mid `12.00` (+20%) → `10.92/10.80`, plus banded lookups
(7%→row 5, 25%→row 20, −3%→row 0).

Ratchet unit check (no Gateway needed) — build an `XWingController` with `original_avg_fill=10.0` and
call `compute_protective_levels(mid)` over a sequence: `10.00` → `aux 9.90/lmt 9.85`; `11.00` →
ratchets up; `10.50` → **HOLDS** (does not re-peg down); `mid ≤ aux` becomes True when price falls to
the held `aux`; after a protective fill the ratchet state (`high_water_mid`, `_aux_floor`, `_lmt_floor`)
resets so the next cycle starts fresh.

The live paths still require a **paper Gateway on 4002** to confirm end to end: buy fills → protective
STP LMT armed at the ratcheted level (orderId stable; replaces only when the stop rises, holds when
price falls) → synthetic trigger places SELL LMT @ held table `lmt` / lifetime flatten @ bid-cross →
re-entry at the sell price → and `kill -9` leaving the broker-side STP LMT resting at the held level.
