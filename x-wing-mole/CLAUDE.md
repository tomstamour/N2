# x-wing-mole

IBKR (ibapi) trading trio. A surge detector (`trade-mole`) watches a ticker and,
on trigger, hands a buy-signal to an order manager (`x-wing`); a `clerk` runs both
together in one process on a single shared ibapi connection.

## Active scripts
- **`clerk-1.0.py`** — warm pool of pre-connected ibapi clients (ids 1000–1029).
  Listens on a TCP/JSON socket for orch-triggers `{ticker, lastDailyClose,
  itiBaseline}`, then runs a trade-mole + x-wing **duo** in-process on one shared
  client. Recycles the client (cancel mkt data + reset) without dropping the socket.
- **`trade-mole-2.0.py`** — trade-frequency surge detector. Standalone, or **driven**
  by the clerk (ticks injected). Preserves all ITI_baseline calcs/columns; the
  temporary trigger rule is isolated in `IBKRSurgeApp.should_fire()`. Stops
  recording at the buy-signal and calls `buy_signal_callback(last_ask)`.
- **`x-wing-2.0.py`** — yield-laddered trailing-stop trader. Standalone, or **driven**.
  Fires on `on_buy_signal(last_ask)`; places a BUY LMT then a ratcheted protective
  SELL STP LMT; no re-entry. Per-trade config is the hard-coded block at the top.

See `xmole-commands.txt` for the principal run + trigger commands.

## Legacy (to be DELETED soon — refactor is done, do not edit or extend)
- **`X-wing-1.0.py`** → superseded by `x-wing-2.0.py`
- **`trade_mole_4.1.py`** → superseded by `trade-mole-2.0.py`

## Notes
- Run with the venv python that has ibapi: `/home/tom/venv/bin/python`
  (system `python3` lacks ibapi; it only works for `py_compile` syntax checks).
- Lifetimes parse as `mm:ss` (e.g. `00:15` = 15s, `01:00` = 60s).
- Port 4001 is the LIVE Gateway; 4002 paper GW / 7497 paper TWS.
