# x-wing-mole

IBKR (ibapi) trading trio. A surge detector (`trade-mole`) watches a ticker and,
on trigger, hands a buy-signal to an order manager (`x-wing`); a `clerk` runs both
together in one process on a single shared ibapi connection.

## Active scripts
- **`clerk-1.1.py`** — warm pool of pre-connected ibapi clients (ids 1000–1029).
  Listens on a TCP/JSON socket for orch-triggers `{ticker, lastDailyClose,
  itiBaseline}`, then runs a trade-mole + x-wing **duo** in-process on one shared
  client. Recycles the client (cancel mkt data + reset) without dropping the socket.
  Reads `clerk-trigger-conditions.txt` once at startup: per-ET-window `lifeTime`
  (mole watch, resolved at collection start) and `finbert_gate` on/off (sentiment
  gate, resolved at the surge instant; off = surge alone places the order and
  sentiment messages are ignored). Windows are end-exclusive; missing/malformed
  file → built-ins (02:30, gate on).
- **`trade-mole-2.1.py`** — trade-frequency surge detector. Standalone, or **driven**
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
