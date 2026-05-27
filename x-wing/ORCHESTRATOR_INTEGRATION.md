# Integrating X-wing into Orchestrator3.3 — key points

> **Status:** design spec (not yet implemented). This document describes how `X-wing-1.0.py` will be
> wired into `Orchestrator3.3.py` as the automated long-side **trade executor**, driven by the
> existing news → FinBERT → trigger pipeline. No code has been changed yet; items in
> [§10](#10-open-decisions-fill-in-before-implementation) must be decided first.

Paths referenced:
- Orchestrator: `/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/Orchestrator3.3.py`
- X-wing: `/home/tom/Documents/ibkr_scripts/N2/scripts/x-wing/X-wing-1.0.py`
- X-wing prewarm signal harness: `/home/tom/Documents/ibkr_scripts/N2/scripts/x-wing/prewarm_test.sh`

---

## 1. Goal & model

`Orchestrator3.3.py` already converts a news firehose into trade signals:

```
NewsWatcher3 (accepted PR) → FinBERT sentiment → evaluate_trigger (sentiment + float) → launch
```

Today the launched process is `trade_mole_4.py`, which is a **passive surge detector/logger** — it
never takes a position. `X-wing-1.0.py` is the **actual long-side trade executor** (yield-laddered
trailing stop, one OS process per symbol).

**Decision:** X-wing is *added* as the executor. `trade_mole` stays exactly as it is (passive
detector); `maybe_launch_trade_mole` is **not** removed. Both launch off the same trigger.

**Why prewarm:** X-wing ships a `--prewarm` mode that splits its ~2 s startup
(connect → `reqContractDetails` → `reqMktData` → first tick) away from the trade decision. Launching
X-wing `--prewarm` **at news arrival, in parallel with FinBERT inference**, hides that connect
latency behind the model so the entry fires near-instantly when the trigger resolves YES.

---

## 2. Decision flow (the core)

There are two existing hook points, and the per-article / per-ticker split matters:

- **FinBERT runs once per article** (one shared future), see `on_news_accepted`
  (`Orchestrator3.3.py:361`).
- **The trigger is per-ticker** because `Float` is per-symbol, evaluated in `_collect_and_log`
  (`Orchestrator3.3.py:304`). An article carries up to 2 tickers.

So prewarm and the subsequent fire/abort are **per ticker**.

### 2a. At news arrival — `on_news_accepted` (per ticker)

For each ticker, before/alongside submitting the FinBERT future:

1. **Float pre-gate** — `Float` is already known at arrival via `_lookup_float(symbol)`
   (`Orchestrator3.3.py:231`). If it fails `TM_FLOAT_MAX_M`, **do not prewarm** — there is no point
   opening IBKR connections for news that will fail the trigger anyway.
2. **Off-hours gate** — reuse the ET-hour check used by `maybe_launch_trade_mole`
   (`Orchestrator3.3.py:147-153`); skip the 20:00–04:00 ET dead window so X-wing is not prewarmed
   into a multi-hour idle connection.
3. If both pass → launch `X-wing --prewarm` (see [§3](#3-cli-arguments-orchestrator-must-build)) and
   store the `subprocess.Popen` handle in a registry keyed by `(news_id, symbol)`
   (see [§5](#5-pidhandle-registry)).

> Only `sentiment_score` is unknown at arrival; `Float` is known. Gating prewarm on the
> already-known `Float` is what keeps the connection budget bounded.

### 2b. After FinBERT — `_collect_and_log` (per ticker)

`evaluate_trigger(completed_dict)` (`Orchestrator3.3.py:119`) already yields `YES` / `NO:...`.
After computing it, look up the prewarmed handle for `(news_id, symbol)` and signal X-wing:

| Trigger result | Action | X-wing reaction |
|---|---|---|
| `YES` | `os.kill(pid, signal.SIGUSR1)` | **FIRE** — places the BUY LMT off the warm ask |
| `NO:...` | `os.kill(pid, signal.SIGTERM)` | **ABORT** — clean disconnect, no trade |
| no handle | nothing | (Float pre-gate or off-hours skipped the prewarm) |

**Crucial:** the FinBERT-error path (`except` in `_collect_and_log`, `Orchestrator3.3.py:310-316`)
must also send **SIGTERM** to any prewarmed handle — otherwise a model failure leaks a connected,
idle X-wing process that lives until its timeout.

### 2c. Safety net

X-wing `--prewarm-timeout` auto-aborts the process if the orchestrator never signals it (parent
crash / forgotten child). This is a backstop, not the primary path.

```
news arrives ──► Float gate ──► (parallel) ┌─ FinBERT inference ──► trigger YES/NO
                                            └─ X-wing --prewarm (connect/resolve/stream)
                                                         │
                              trigger YES ──► SIGUSR1 ──► FIRE entry
                              trigger NO  ──► SIGTERM ──► abort
                              (no signal) ──► --prewarm-timeout auto-abort
```

---

## 3. CLI arguments Orchestrator must build

Mirror the argv-build + detached-`Popen` pattern in `maybe_launch_trade_mole`
(`Orchestrator3.3.py:135-189`). X-wing args (see its `build_arg_parser`, `X-wing-1.0.py:105`):

| Arg | Source |
|---|---|
| `--symbol` | the ticker |
| `--capital` | `XW_CAPITAL` (config) |
| `--input-limits-table` | `XW_LIMITS_TABLE` (config) |
| `--max-limit-entry-percent-price` | `XW_MAX_LIMIT_ENTRY_PCT` (config) — entry priced off the live ask at FIRE |
| `--last-close-price` | **lookup from the priced TSV** — `_priced_df` column `LastDailyClosePrice` via a new `_lookup_last_close(symbol)` mirroring `_lookup_float` |
| `--max-cap-entry-percent` | `XW_MAX_CAP_ENTRY_PCT` (config) — *both* cap args must be passed together or *neither* |
| `--prewarm` / `--prewarm-timeout N` | always set; `N` > worst-case FinBERT latency + margin |
| `--client-id-base` | `_next_xw_clientid()` — **increments by 2** (X-wing owns base **and** base+1) |
| `--log-dir` / `--price-action-table` | `XW_LOG_DIR` / `XW_OUTPUT_DIR` |
| `--host` / `--port` | `XW_HOST` / `XW_PORT` |
| `--exit-cross-percent` | `XW_EXIT_CROSS_PCT` (config) |
| `--lifetime` and/or `--session-end` | `XW_LIFETIME` / `XW_SESSION_END` (config) |
| `--account` | `XW_ACCOUNT` (optional) |
| `--market-data-type` / `--loglevel` | `XW_MARKET_DATA_TYPE` / config |

> X-wing's entry-price validation requires **at least one** of `--max-limit-entry-percent-price` or
> `--Entry-limit-price`, and the cap requires **both** `--last-close-price` and
> `--max-cap-entry-percent` together (`X-wing-1.0.py:934-942`). If `LastDailyClosePrice` is missing
> for a symbol, omit *both* cap args (entry then uses ask × (1 + pct) uncapped).

---

## 4. New `XW_*` config block

Add a block parallel to the `TM_*` block (`Orchestrator3.3.py:54-68`):

```python
XW_SCRIPT          = '/home/tom/Documents/ibkr_scripts/N2/scripts/x-wing/X-wing-1.0.py'
XW_PYTHON          = '/home/tom/venv/bin/python'   # MUST be the venv (ibapi 9.81.1) — see note
XW_CAPITAL         = ...        # $ per trade            (TODO §10)
XW_LIMITS_TABLE    = '/home/tom/Documents/ibkr_scripts/N2/scripts/x-wing/example-yield_vs-stopLimits.tsv'
XW_MAX_LIMIT_ENTRY_PCT = ...    # entry = ask*(1+pct/100) (TODO §10)
XW_MAX_CAP_ENTRY_PCT   = ...    # cap   = last_close*(1+pct/100) (TODO §10)
XW_EXIT_CROSS_PCT  = 0.5
XW_LIFETIME        = ...        # mm:ss, optional        (TODO §10)
XW_SESSION_END     = None       # HH:MM ET, optional
XW_LOG_DIR         = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/x-wing_logs'
XW_OUTPUT_DIR      = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/x-wing_tables'
XW_HOST            = '127.0.0.1'
XW_PORT            = 4002        # 4002 paper / 4001 LIVE  (TODO §10 — real money)
XW_MARKET_DATA_TYPE = 1
XW_ACCOUNT         = None        # optional, set on every order
XW_BASE_CLIENT_ID  = 1000        # dedicated range, step 2 — must not collide with TM (400) or NW3
```

**`XW_PYTHON` matters:** `trade_mole` is launched with `sys.executable` (`Orchestrator3.3.py:65`),
which is fine for it. **X-wing requires the `/home/tom/venv` interpreter** (ibapi 9.81.1 and its
exact signatures). Either run the orchestrator under that venv, or always launch X-wing with the
explicit `XW_PYTHON` — do not rely on `sys.executable`.

---

## 5. PID / handle registry

```python
_xw_prewarmed = {}                  # {(news_id, symbol): subprocess.Popen}
_xw_prewarmed_lock = threading.Lock()
```

- **Populate** at prewarm launch in `on_news_accepted` (per ticker).
- **Consume** (read, signal, then `pop`) at fire/abort in `_collect_and_log`.
- Guarantee removal on **every** path — `YES`, `NO`, and the FinBERT-`except` path — so handles never
  leak. Use `.pop((news_id, symbol), None)` so a double-signal is a no-op.

---

## 6. Detachment & lifecycle

- Launch detached with `start_new_session=True` (exactly as `maybe_launch_trade_mole` does,
  `Orchestrator3.3.py:176-183`). This means:
  - Ctrl+C / SIGINT on the orchestrator does **not** propagate to X-wing children.
  - A **fired** X-wing keeps managing its position (and its broker-side resting stop) even across an
    orchestrator restart.
  - The parent still holds the PID, so `os.kill(pid, SIGUSR1/SIGTERM)` for fire/abort still works on
    a detached child.
- On orchestrator shutdown (`main()` `finally`, `Orchestrator3.3.py:447-453`): iterate the registry
  and **SIGTERM any still-pending prewarms** so their IBKR connections are released promptly instead
  of waiting for `--prewarm-timeout`. (Already-fired X-wings are no longer in the registry and are
  intentionally left running.)

---

## 7. Connection-budget & safety

- Each **prewarmed** X-wing opens **two** IBKR client connections (order + data, clientIds `base` and
  `base+1`) plus **one** streaming market-data line. IBKR's market-data line default is ~100/account.
- The **Float pre-gate** (§2a) and **off-hours gate** are what keep concurrent prewarms bounded — do
  not drop them.
- **Live money:** X-wing logs a real-money warning on ports `4001`/`7496` (`X-wing-1.0.py:927-929`).
  `XW_PORT` must be chosen deliberately. Note `trade_mole` already runs `TM_PORT = 4001` (live).
- X-wing never uses `reqGlobalCancel` and only ever touches its own orderIds, so concurrent instances
  on the shared account are safe.

---

## 8. Timing / race notes

- **Early fire is safe.** X-wing registers its `SIGUSR1` handler *before* connecting and latches
  `fire_requested` (`X-wing-1.0.py:962-967`). If FinBERT finishes before X-wing has reached the
  prewarm wait, the fire is honored as soon as the wait loop is entered — it is not lost.
- **Fire before the ask is warm.** If fired very early, the entry falls back ask→last→close (or the
  fixed `--Entry-limit-price` if supplied). Set `--prewarm-timeout` comfortably above the worst-case
  FinBERT latency so this is rare.
- **`--lifetime` counts from FIRE, not from prewarm launch** — the trade clock starts when the
  position actually opens, so prewarm idle time does not eat into the run.

---

## 9. Reuse map (existing code to lean on)

| What | Where |
|---|---|
| Detached `Popen` + argv build + per-symbol log file | `maybe_launch_trade_mole` — `Orchestrator3.3.py:135-189` |
| Float lookup | `_lookup_float` — `Orchestrator3.3.py:231` |
| Last-close lookup (to add) | mirror `_lookup_float` against `LastDailyClosePrice` in `_priced_df` (`Orchestrator3.3.py:401`) |
| Off-hours ET gate | `Orchestrator3.3.py:147-153` |
| Auto-increment clientId counter pattern | `_next_tm_clientid` — `Orchestrator3.3.py:94` (new one steps by **2**) |
| Trigger decision (unchanged) | `evaluate_trigger` — `Orchestrator3.3.py:119` |
| X-wing prewarm signal contract | `prewarm_test.sh` — `SIGUSR1`=fire, `SIGTERM`=abort, no-signal=timeout |
| X-wing prewarm wait loop | `X-wing-1.0.py:996-1018` |

---

## 10. Open decisions (fill in before implementation)

- **`XW_CAPITAL`** — dollars per trade.
- **`XW_MAX_LIMIT_ENTRY_PCT`** and **`XW_MAX_CAP_ENTRY_PCT`** — entry-pricing percentages, and whether
  to apply the cap at all (cap needs `LastDailyClosePrice`).
- **Limits table** — keep `example-yield_vs-stopLimits.tsv` or point to a production table.
- **`XW_PORT`** — paper (`4002`) vs live (`4001`), and whether to pin **`--account`**.
- **`XW_LIFETIME` / `XW_SESSION_END`** — how long a position is allowed to run before forced flatten.
- **`--prewarm-timeout`** value — set above the measured worst-case FinBERT latency.

---

## 11. How to verify once built

1. **Signal flow in isolation first:** `./prewarm_test.sh SYMBOL fire|abort|timeout` against paper
   Gateway (4002) — confirms the SIGUSR1/SIGTERM/timeout contract end to end.
2. **Dry run on paper (`XW_PORT=4002`):** run the orchestrator, feed/simulate an accepted news item
   that passes the Float gate. Confirm in the logs:
   - an `X-wing --prewarm` launch line with a `pid=`,
   - a `SIGUSR1` (fire) on a `YES` trigger, or `SIGTERM` (abort) on a `NO` trigger,
   - the registry entry is removed afterward.
3. **Client-id collisions:** grep logs for `clientId` and confirm the X-wing range
   (`XW_BASE_CLIENT_ID`, step 2) never overlaps `TM_BASE_CLIENT_ID` (400) or NW3.
4. **Compile check:** `/home/tom/venv/bin/python -m py_compile Orchestrator3.3.py`.
