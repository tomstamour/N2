# x-wing-mole

IBKR (ibapi) trading trio. A surge detector (`trade-mole`) watches a ticker and,
on trigger, hands a buy-signal to an order manager (`x-wing`); a `clerk` runs both
together in one process on a single shared ibapi connection.

## Active scripts
- **`clerk-1.2.py`** — TRIO clerk (2026-07-26, **paper-GW verify pending**): runs
  trade-mole + T47 + a SEQUENCE of x-wing runs per trigger on one shared client.
  Copy of 1.1; the delta: loads `T47-airspeeder-2.0.py` (was 1.0 until
  2026-08-02 — the loader line is the only wiring); `DuoSession`→`TrioSession`
  (owns `.t47`; `session.xwing` = the CURRENT run's controller, swapped by T47 per
  relaunch, which atomically re-points order/tick routing); `SharedIBClient` fans
  ticks to mole+xwing+T47 and echoes `orderStatus` to T47 (EP/SP capture); the surge
  launch hands off via `t47.launch_run1`; `_on_xwing_done` → `t47.on_run_done` (T47
  decides: resume watch or finish); `finish` → `t47.shutdown` — the client is held
  for the WHOLE trio and recycled only at trio end. The resolved ContractDetails is
  cached session-side (`_cd`) and applied to each relaunched x-wing (reqId 98 never
  re-requested); all runs share `client.alloc_order_id`. New guard: a post-fire
  `Sentiment BAD` is ignored (`sentiment_ignored_late`) — 1.1 had no `_buy_fired`
  check and a late BAD would tear a live session down. `trade-mole-2.1`/`x-wing-2.1`
  needed zero edits.
- **`T47-airspeeder-2.0.py`** — x-wing RE-ENTRY controller (**loaded by clerk-1.2
  since 2026-08-02**; driven-only: no ibapi, no standalone mode). Same clerk-facing
  API as 1.0 (`set_anchor`/`launch_run1`/`on_tick_price`/`observe_order_status`/
  `on_run_done`/`shutdown`) and the same safety layer, but the 4-run DROP/PUSH
  ladder collapses to **ONE re-entry at SP1**: at the run-1 exit the exit avg fill
  is FROZEN as `level` (no HP ratchet, never re-anchors), `LP` tracks the lowest
  bid since, `DIP=(level−LP)/level·100` ≥`EP2_MIN_DIP_PCT`=10 latches Valid-DIP,
  and the first `bid ≥ level` after that relaunches one fresh x-wing
  (`on_buy_signal(ask, ask)`, Rule-F shape; **swap `session.xwing` BEFORE placing**).
  `XWING_REENTRY_NUMBER`=1 ⇒ 2 runs max. An aborted entry (Inactive/timeout/reject)
  consumes its run but still opens the watch, aiming at `ctl.trigger_print` →
  `ctl.ask` → bid since no SP1 exists. Watch capped by `T47_WATCH_LIFETIME` (2h
  from collection start) + 20:00 ET, both flat-only (T47 never touches `stop_evt`).
  Safety ends unchanged: `t47:residual_position`, `t47:late_entry_fill`,
  `t47:budget_exhausted:*`. Output `T47-tables/SYMBOL-yyyy-mm-dd-hh-**t47v2**.tsv`
  (13 cols: …SP/HP/LP/DIP(%)/GAP(%)/Valid-DIP…; `SP` = the frozen level while the
  watch is open, `GAP(%)`=0 is the fire line, `HP` informational only), lazy-opened
  so no-surge sessions leave no file. Verified by an offline stub harness
  (9 cases); **paper-GW verify pending**.
- **`T47-airspeeder-1.0.py`** — x-wing RELAUNCH controller, **prior version, kept on
  disk** (superseded by 2.0 on 2026-08-02; its `-t47.tsv` tables from 2026-07-28
  use a different column set). Role: run 1 is the normal mole+sentiment
  trigger untouched (T47 only books it); after each x-wing exit T47 watches the
  **bid** and relaunches a FRESH x-wing (≤`XWING_RELAUNCH_NUMBER`=3 more, 4 runs
  max) on a drop-then-bounce: at each exit `HP=LP=SP` (exit avg fill; in-position
  peaks ignored), bid>HP re-anchors both (Valid-DROP cleared), `DROP=(HP−LP)/HP·100`
  >15 latches Valid-DROP, then `PUSH=(bid−LP)/LP·100` >10 relaunches with
  `on_buy_signal(ask, ask)` (Rule-F shape; **swap `session.xwing` BEFORE placing** —
  else the first orderStatus routes to the dead controller). Aborted entries
  (Inactive/timeout/reject) consume a run and re-anchor HP=LP=current bid — run 1
  included. Watch capped by `T47_WATCH_LIFETIME` (2h from collection start) +
  20:00 ET, both flat-only: a live run always finishes on its own terms (T47 never
  touches `stop_evt`). Safety ends: residual position → `t47:residual_position`;
  fill on an aborted run's entry id → `t47:late_entry_fill`. All thresholds are
  top-of-file constants. Output `T47-tables/SYMBOL-yyyy-mm-dd-hh-t47.tsv`: one row
  per bid tick + event rows (RUN_LAUNCH/ENTRY_FILL/EXIT_FILL/ABORT/VALID_DROP/
  HP_RESET/RELAUNCH/…), lazy-opened so no-surge sessions leave no file.
- **`clerk-1.1.py`** (**LIVE fallback** — duo, one x-wing run per trigger) — warm pool
  of pre-connected ibapi clients (ids 1000–1029).
  Listens on a TCP/JSON socket for orch-triggers `{ticker, lastDailyClose,
  itiBaseline}`, then runs a trade-mole + x-wing **duo** in-process on one shared
  client. Recycles the client (cancel mkt data + reset) without dropping the socket.
  Reads `clerk-trigger-conditions.txt` once at startup: per-ET-window `lifeTime`
  (mole watch, resolved at collection start) and `finbert_gate` on/off (sentiment
  gate, resolved at the surge instant; off = surge alone places the order and
  sentiment messages are ignored). Windows are end-exclusive; missing/malformed
  file → built-ins (02:30, gate on).
- **`trade-mole-2.2.py`** — 2.1 + the 2026-07-30 precision re-calibration
  (**loaded by BOTH clerks since 2026-07-30** — takes effect on next clerk
  restart; a running clerk keeps its imported 2.1). Zero structural change: Rule C
  100→1550, Rule A dollar gate 2→60, Rule D max ITI 0.3→0.28, Rule E price rise
  1%→1.2%, plus a **Rule F veto** (pretrade fire suppressed unless: no trade
  printed yet AND pretrade gap ≤1.25s AND ratio ≤5000; writes a `PRETRADE_VETO`
  row, disables the pretrade path for the session, trade path keeps watching;
  kill-switch `--no-pretrade-veto`). On the 07-09..07-30 labeled window: pos
  21/21 kept, neg fires 23/30→13, median TTF unchanged — but the F-veto rests on
  n=5 pretrade pos and all numbers are censored (recordings stop at the fire).
  Calibration report: `mole-outputs/surge-predictor-analysis-2026-07-30.md`
  (re-runnable via `analyze_trade_mole/analyze_surge_predictors_v3.py`).
- **`trade-mole-2.1.py`** — trade-frequency surge detector (prior live version,
  kept on disk; clerks load 2.2 since 2026-07-30). Standalone, or **driven**
  by the clerk (ticks injected). Preserves all ITI_baseline calcs/columns; the
  temporary trigger rule is isolated in `IBKRSurgeApp.should_fire()`. Stops
  recording at the buy-signal and calls `buy_signal_callback(last_ask)`.
  **Rule F — pre-trade start-staleness (2026-07-23):** a snapshot-only twin of
  Rule B evaluated off quotes (`_maybe_fire_pretrade`), so a stale illiquid name
  fires the instant the reqMktData snapshot lands — before the first trade prints
  (~2 s earlier on NIVF). Fires when `bslnITI_over_pretrade_gap = baselineITI /
  max(first_event_wall − LAST_TIMESTAMP, PRETRADE_MIN_GAP_SEC)` exceeds its OWN
  constant `PRETRADE_STALENESS_THRESHOLD` (independent of Rule B). Needs a valid
  ITI baseline and a snapshot ask (prices the entry off it, `trigger_print=ask`).
  Enabled by default (`PRETRADE_STALENESS_FIRE`); disable per-run with
  `--no-pretrade-staleness-fire`. Both fire paths share `_emit_buy_signal`.
- **`x-wing-2.1.py`** — yield-laddered trailing-stop trader (**LIVE driven script**; the
  clerk loads this one). Standalone, or **driven**. Fires on `on_buy_signal(last_ask,
  trigger_print)`; places a BUY LMT then a ratcheted protective SELL STP LMT; no re-entry.
  Per-trade config is the hard-coded block at the top. **2.1 = 2.0 + IBKR Price Management
  Algo (`usePriceMgmtAlgo=True`, constant `ENTRY_USE_PRICE_MGMT_ALGO`) on the BUY entry
  only** — an over-aggressive pre-market entry limit is CAPPED and worked instead of
  rejected (silent "Inactive"/202); see `rejected-orders-findings.txt`. `x-wing-2.0.py` is
  the prior version, kept on disk.

See `xmole-commands.txt` for the principal run + trigger commands.

## Legacy (to be DELETED soon — refactor is done, do not edit or extend)
- **`X-wing-1.0.py`** → superseded by `x-wing-2.0.py`
- **`trade_mole_4.1.py`** → superseded by `trade-mole-2.1.py`

## Notes
- Run with the venv python that has ibapi: `path/to/venv/bin/python`
  (system `python3` lacks ibapi; it only works for `py_compile` syntax checks).
- Lifetimes parse as `mm:ss` (e.g. `00:15` = 15s, `01:00` = 60s).
- Port 4001 is the LIVE Gateway; 4002 paper GW / 7497 paper TWS.
- Rare marketable BUY LMT entries can come back IBKR-"Inactive" with no error reason
  (x-wing then aborts the run); see `rejected-orders-findings.txt` for occurrences, the
  shared fingerprint, and candidate causes. **x-wing-2.1 attaches the Price Management Algo
  to the entry (`usePriceMgmtAlgo=True`) so IBKR caps-and-works such an over-aggressive limit
  instead of rejecting it; effectiveness still to be confirmed live on the paper GW.**
- **Market-data latency (ARM → live Ask), fixed 2026-07-21:** on ARM the clerk fires
  `reqMktData` on a **bare `STK/SMART/USD`** contract (IBKR resolves it server-side), so the
  Ask that prices the x-wing entry streams within ~ms of the PR instead of blocking on the
  `reqContractDetails` round-trip first (that round-trip was ~560 ms at top-of-hour — JUNS's
  `reqMktData` landed 577 ms after its PR before this change; verified ~7 ms after ARM after).
  The resolved `conId`/`minTick` — needed only by the *entry order*, not the quote — is
  fetched in a background thread (`DuoSession._resolve_contract_async` → x-wing
  `set_contract_details`), and the entry launch waits on `_contract_ready` so it never
  prices/places against an unresolved contract. ⚠ The per-client contract slot
  (`client._contract_details`, fixed reqId 98) is **shared**, so a client must not be
  re-armed until its background resolver has consumed it — release it back to the pool only
  after the resolver finishes (else a re-arm can cross-wire the wrong contract onto a live
  session).
