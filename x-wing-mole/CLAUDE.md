# x-wing-mole

IBKR (ibapi) trading trio. A surge detector (`trade-mole`) watches a ticker and
hands a buy-signal to an order manager (`x-wing`) on trigger; `clerk` runs both
in one process on one shared ibapi connection.

## Active scripts
- **`x-wing-3.0.py`** — 2.1 + entry limit TRACKS THE ASK (08-12) + **3.2
  EXIT-noise defences + 3.3 market-data capture (10-02) + 3.4 dead-exit re-arm
  (10-06)**; loaded by clerk-1.2 since 08-12; paper-GW verify pending.
  ⚠⚠ **10-06 (3.4): a REFUSED exit order had no owner.** JUNS 10-06 held 1 sh
  **unprotected for 4 hours**. hard_floor fired, the STP LMT→LMT type change by
  modify on the live id drew **103** (`Duplicate order id`), the cancel+new
  fallback then priced the replacement at `_lmt_floor`=1.98 — the LADDER's limit,
  not the bid the hard floor asked for — and IBKR refused it with **202**
  (`more aggressive than 2.3001 ... market price 2.55`) **111 ms** later, inside
  `PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC`, so the min-gap guard returned having
  done nothing. All three guards in that fallback (budget / min-gap / token
  bucket) return after only `wake.set()`; they are right not to storm, but
  nothing owned the state they leave. `chase_exit_limit` cannot — it bails on
  `_prot_reject_n` by design. Three fixes: `_exit_repair_price()` (ONE price,
  and it honours the trigger — `hard_floor` means the BID, because the hard
  floor IS the loss cap and a limit above the market caps nothing);
  `rearm_dead_exit()` in the control loop's exiting branch, re-placing on a
  **FRESH orderId** when the exit reads `ORDER_DEAD_STATUSES`
  (`arm_or_replace_protective` reuses `protective_id` by design, so a dead id
  draws 103 again — clear it in the SUPERVISOR, never inside it), bounded
  `EXIT_REARM_MAX_N`=5 / `_MIN_GAP_SEC`=2.0 / `_AFTER_SEC`=2.0 grace + the global
  throttle, an UNKNOWN status never treated as dead; and `exit_wall_obs`, the
  SELL-side mirror of `entry_wall_obs` (3.2 parsed the 202's exact floor and
  threw it away — recorded FIRST, before the early-returning guards, highest
  floor kept, lifted over with `_wall_step_up`). The shutdown FLATTEN inherited
  BOTH defects and repeated the 103→202 pair at 15:15 (`FLATTEN incomplete:
  1 sh still open`) — it now clears a dead id and prices through
  `_exit_repair_price(force_bid=True)`. **The exit DECISION is untouched and
  proven so**: `replay_exit.py --all` is byte-identical over the whole corpus.
  Kill: `--no-exit-rearm`. Verified: `verify_xwing3_tracking.py` **267/267**
  (new `case_dead_exit_rearm` replays the JUNS state at 11:15:38.216).
  ⚠ Paper GW still has to settle whether the re-armed SELL LMT is ACCEPTED.
  ⚠ OPEN, same day: **VCIG** 15:45 is the ENTRY half — 202 at 1.0500 (wall
  1.0472, IBKR ref a stale 0.9537 against a real 1.02/1.02), the retry pinned
  itself under the wall at 1.0400, the ask ran to 1.10 and the 10 s timeout
  cancelled it. The clamp worked as designed; a wall-pinned entry limit being
  left frozen while the market leaves it behind is unaddressed.
  ⚠⚠ **10-02 (3.3): the data was already arriving and was being thrown away.**
  clerk-1.2.py:666 subscribes with trade-mole's `GENERIC_TICKS` and its
  `SharedIBClient` implements all four tick callbacks (`clerk-1.2.py:433-459`) —
  but `tickSize`/`tickString`/`tickGeneric` fanned to the **MOLE ONLY**, and
  `route_tick_price` kept 5 tick ids with no else branch. Bid/ask/last SIZE
  (0/3/5), day volume (8) and day high/low/open (6/7/14) come with the DEFAULT
  tick set and vanished. Reading them costs **no new request, no extra
  market-data line, no latency** — ibapi already decoded them. Now
  `route_tick_size`/`_generic`/`_string` + clerk fan-out (mole stays FIRST,
  x-wing call getattr-guarded + try/except). **RECORD-ONLY**: `replay_exit.py
  --all` is byte-identical to 3.2, so the exit path is provably untouched.
  ⚠ A price of 0 is dropped; a **SIZE of 0 is NOT** — it is the book being
  pulled, the event behind every 10-01 ejection. `is not None`, never
  truthiness. Same for a trade rate of 0 (the dead tape MEDS entered on, with
  `last_age=126 s`).
  ⚠ `microprice()` = `(bid·ask_sz + ask·bid_sz)/(bid_sz+ask_sz)` — weights
  CROSSED on purpose (deep bid + thin ask ⇒ next trade likelier at the ask ⇒
  fair value HIGH). The honest answer to "the yield is on the bid so it is
  already negative at the fill". **Nothing anchors on it yet** — that wants the
  labelled corpus. Also `touch_imbalance`, `quote_rate` (frozen feed vs quiet
  market are indistinguishable in the tables today), `bid_vol_pct`,
  `px_vs_vwap_pct`, `note_trade_through` (complement of `deferred_n`).
  Table **+22 append-only cols** at `PA_COLUMNS[35:]`, built in ONE place
  (`market_snapshot_row()`); `static_snapshot` holds only the once-per-session
  values. `trade-mole-2.4.py GENERIC_TICKS += ",595"` (3/5/10-min volume) —
  behaviour-neutral, proven by `replay_mole.py --self-check --check-decisions`
  at 77/253 before AND after.
  ⚠⚠ `POST_EXIT_WATCH_SEC` **0 → 300** (4 Hz for 30 s, then 1 Hz, stale bail
  30 s). The 3.2 claim that the clerk cancels market data on recycle was
  **WRONG**: `T47.on_run_done` (`T47-airspeeder-2.0.py:367-407`) opens its watch
  and the clerk holds the client for the whole trio, `cancelMktData` only in
  `TrioSession.finish()` (`clerk-1.2.py:846`) — quotes keep arriving for up to
  2 h. No clerk change needed; do NOT defer `cancelMktData` (holds a client out
  of the pool, delays T47).
  Verified offline: `verify_xwing3_tracking.py` **239/239**. ⚠ Paper GW must
  still settle what the Gateway SENDS: does `halted` (49) arrive without its own
  generic tick, do `vol_3min/5min/10min` arrive for a thin small cap, is
  `day_volume` in lots. ⚠ The labelled corpus is 7 files, 4 unscorable, so
  `--leave-one-out` shows 0.0 for every defence — the corpus is thin, not the
  defences inert.
  ⚠⚠ **10-02: the BID alone no longer fires the exit.** The whole exit path runs
  off the bid (`current_exit_ref`, `yield_pct`, peak-bid `high_water`,
  `aux=hw×(1−Trigger%/100)`), and TWO stops exist: the real IBKR `STP LMT`
  resting at the Gateway (`triggerMethod` unset ⇒ IBKR triggers on the **last
  traded price**; it was correct and never fired) and x-wing's OWN 1 Hz
  `if exit_ref <= aux:` on the bid, which replaces the stop-limit with a plain
  SELL LMT. The second one ejected MEDS/VEEA/HXHX 1–9 s after entry at prices
  with no print on the chart — a withdrawn bid moves no shares. 6 of 26
  triggered exits fired while the last trade AND the ask were still at/above the
  entry fill (KIDZ: ejected on bid 2.45 while last=ask=2.90, −13% on a stock
  that was UP). Now `exit_decision()` returns `bid+last` (a trade must agree),
  `deferred` (bid crossed, nothing traded ⇒ HOLD), or
  `hard_floor`/`hard_floor_bid`. ⚠ Re-anchoring the yield on the bid-at-fill
  fixes NOTHING: `LimitsTable.lookup()` floors at the first row, so −5.22% and
  0.00% pick the same `Yield 0` row, and `aux` comes from `high_water` anyway.
  ⚠ **Trigger% is now a FLOOR**: `eff_trigger% = max(table, 1.5×MEDIAN spread%
  over 10s)`, capped 20%. The median, not the instantaneous spread — HXHX quoted
  2.3% for one tick against a 4.9% median. ⚠ The **ratchet had to be split**:
  `_aux_floor/_lmt_floor` stay the effective held levels (two existing readers),
  new `_aux_table_floor/_lmt_table_floor` carry the profit ratchet, and
  `aux=min(ratcheted_table, hw×(1−eff_trig/100))` — otherwise the ratchet pushes
  the stop UP into the noise band, which *is* the MEDS failure.
  ⚠ `HARD_FLOOR_PCT`=5 on the **last print** caps the TRADED decline at 5%, NOT
  the realised loss (the sale is at the bid, a spread lower: fires at −8.57% on
  NUR, −12.51% on QCLS). A plain 5% bid floor would fire on VEEA's 5.6% bid
  noise, so the bid clause is spread-widened: `bid ≤ entry×(1−(5%+spread%)/100)`.
  A 12%-spread name (LRHC) cannot be exited for a 5% loss.
  `chase_exit_limit()` re-prices an unfilled exit LMT down to the bid (LESL
  rested 860+s through a gap-down) — bounded exactly as the IVF storm requires:
  max 5, throttled, down-only, and abandoned once `_prot_reject_n>0`.
  Table **+9 append-only cols** (`last`, `spread_pct`, `eff_trigger_pct`,
  `eff_limit_pct`, `trigger_source`, `deferred_n`, `exit_fill_px`,
  `exit_fill_ts`, `realized_yield_pct`) + events `TRIGGER_DEFERRED`,
  `EXIT_CHASE_<n>`. The exit fill was previously recorded NOWHERE (the loop
  checks `stop_evt` before building a row); `write_terminal_row()` fixes it.
  `POST_EXIT_WATCH_SEC` **ships 0/OFF** — in driven mode the clerk cancels the
  market-data subscription on recycle, so post-exit quotes freeze.
  Kill: `--no-exit-trade-confirm`, `--no-exit-spread-trigger`,
  `--hard-floor-pct 0`, `--hard-floor-on-bid`, `--no-exit-lmt-chase`.
  Verified offline only: `verify_xwing3_tracking.py` 209/209 and
  `replay_exit.py --self-check`. ⚠ ~10 sessions replay as
  "NEVER FIRES" — not proof of improvement: every recording ENDS at the old
  exit, which is what the post-exit watch exists to fix. Re-prices the SAME orderId
  so the limit stays 2.5% above the live ask both directions
  (`MAX_LIMIT_ENTRY_PERCENT_PRICE`), ceiling frozen at placement =
  `trigger_ref×1.06` (`ENTRY_TRACK_MAX_PCT`). DOWN is NOT time-boxed — a static
  limit is already an uncontrolled chase (20 fills >10s paid 100% of the pad).
  UP is time-boxed 3s (84.6% of fills land ≤3s), then frozen; the window check
  is lazy — fires on the first wanted up-move after 3s. Offset must be a
  PERCENT not cents (27× spread variance in cents, 1.9× in %); 2.5% is the
  measured knee (96% first-shot coverage).
  ⚠⚠ **08-27: bounded by IBKR's regulatory price wall** (`BUY=ref×1.196`,
  `SELL=ref×0.902`). IBKR's reference lags seconds; once a 2161 cap fires the
  +6% ceiling sits at/above the wall. `_entry_upper()=min(ceiling,_wall_bound())`
  gates the entry path; `_wall_bound()` prefers observed 202 wall, else observed
  cap×1.06 (corpus-bounded [1.05521,1.06843)), else off.
  `retry_entry_after_wall()` re-places ONCE on a NEW orderId (reusing draws
  error 103) — `entry_ts` NOT reset, 10s timeout anchors on first placement.
  Fires even below the live ask (IBKR's stale ref catches up — IVF proof).
  Needed: 202-arrives-AFTER-its-Cancelled ordering fixed (abort held open 0.75s
  for error classification); protective cancel+replace fixed (no counter/throttle,
  was re-sending identical price — IVF: 11 orders in 1.66s). Kill:
  `--no-entry-wall`, `--no-entry-wall-retry`.
  ⚠ **`CAP_RESCUE`** (08-13, REPAIRED 08-18 with 3 compounding bugs, fired 0/10):
  one UP move when capped-and-inert below ask, priced at CEILING. Only 3/10
  capped entries ever filled; the base rate any rescue is measured against.
  Armed only off `entry_capped_to` (NOT `openOrder`, which echoes OUR limit).
  `MAX_CAP_ENTRY_PERCENT`=None — old `last_close×1.30` clamp priced 7/317
  entries BELOW market. `ENTRY_USE_PRICE_MGMT_ALGO`=False — doesn't fix 2161 or
  Part B, just lets IBKR re-price under us.
  ⚠⚠ **`ENTRY_MODIFY_RETRY_CODES={103}`** (08-14): rejected MODIFY blinded
  tracker PERMANENTLY (self-sealing). 103 is a Gateway registration race. Fix:
  `_rollback_entry_lmt` restores the real working price (SCNX 08-14 missed
  +43.6% to this). Gating the first modify on the ack was rejected (costs
  ~149ms latency to dodge a race the rollback already clears).
  Table +5 cols (`entry_order_id`, `entry_lmt`, `entry_status`, `track_n`,
  `capped_to`) + events `CHASE_UP/DOWN/CEIL`, `CAP_RESCUE`, `ENTRY_CAPPED`,
  `MODIFY_REJECT_<code>`. Kill: `--no-entry-track` (full revert to 2.1).
  Verified: `verify_xwing3_tracking.py`, 125 checks + 185 wall checks.
- **`clerk-1.2.py`** — TRIO clerk (07-26, paper-GW verify pending): trade-mole +
  T47 + sequence of x-wing runs per trigger, one shared client. Loads
  `x-wing-3.0.py` since 08-12 and `trade-mole-2.5.py` since the 10-04 20:12
  restart (2.4 from 09-18; clerk-1.1 stays 2.1/2.2). ⚠ Running clerk keeps imported module — loader flip needs
  restart.
  Delta from 1.1: `DuoSession`→`TrioSession` (owns `.t47`); shared client fans
  ticks to mole+xwing+T47; `ContractDetails` cached (reqId 98 never re-requested);
  post-fire `Sentiment BAD` ignored.
  ⚠ **10-02: `tickSize`/`tickString`/`tickGeneric` now fan to x-wing too**
  (`:446-459`) — they went to the mole ONLY, so every size/volume/VWAP/rate tick
  on the subscription was discarded by x-wing. Mole stays FIRST in each (the
  ordering comment in `tickPrice` is load-bearing); `_route_xwing()` is
  getattr-guarded so a loader flip to x-wing-2.1/3.0 is a no-op, and
  try/except'd so new code cannot kill the mole's signal path.
  ⚠ `cancelMktData` (`:846`, `TrioSession.finish()`) is the ONLY cancel and
  `finish()` runs at TRIO end, not run end — so after an x-wing exit T47 opens
  its watch and the market-data line stays live for up to 2 h. x-wing-3.0's
  post-exit tail relies on this; do NOT defer the cancel (it would hold a client
  out of the pool and delay T47).
  ⚠ **ONE LIVE SESSION PER SYMBOL since 08-12** (1.2 only). `handle_trigger`
  rejects second arm (`already_armed`). O5.2 arms per ARTICLE and the wire
  re-publishes PRs: 08-12 NEXR ran two sessions that each entered/stopped (2×
  size, 2× commission). Check-and-**claim** under lock. An `already_armed` log
  line means O5.2's dedup missed a shape.
  **10-03: mole post-trigger tail support** — with a mole that has
  `POST_TRIGGER_RECORD_SEC` (2.5+), `on_buy_signal` arms `_tail_timer`
  (tail + 1 s) → `_end_mole_tail("complete")`; `finish()` cancels it and ends a
  still-open tail with `session_end:<reason>`. Every CSV write goes through
  `_write_mole(final)` under `_mole_write_lock` (at-trigger write once, post-tail
  rewrite once, a late first write never overwrites the final). With 2.4 loaded
  nothing is scheduled — behaviour identical. Tested with stand-in objects
  (complete / sentiment-timeout / 2.4 cases), not yet on a Gateway.
- **`T47-airspeeder-2.0.py`** — x-wing RE-ENTRY controller (loaded by clerk-1.2
  since 08-02; driven-only). Collapses 4-run DROP/PUSH ladder to **ONE re-entry
  at frozen SP1**: exit avg fill freezes as `level`, `LP` tracks lowest bid
  since, `DIP=(level−LP)/level·100` ≥10 (`EP2_MIN_DIP_PCT`) latches, first
  `bid≥level` after relaunches x-wing. `XWING_REENTRY_NUMBER`=1 ⇒ 2 runs max.
  Watch capped 2h or 20:00 ET, flat-only. Output `T47-tables/*-t47v2.tsv`
  (13 cols); 9-case offline harness; paper-GW verify pending.
- **`T47-airspeeder-1.0.py`** — prior RELAUNCH controller, kept on disk
  (superseded 08-02). Up to 3 relaunches on drop-then-bounce:
  `DROP>15%` from HP latches, then `PUSH>10%` off LP relaunches.
  Output `T47-tables/*-t47.tsv`.
- **`clerk-1.1.py`** (**LIVE fallback** — duo, one x-wing run per trigger).
  Warm pool of pre-connected ibapi clients (ids 1000–1029). TCP/JSON trigger
  listener → trade-mole + x-wing duo, one shared client, recycled per trade.
  Reads `clerk-trigger-conditions.txt` once at startup for per-window
  `lifeTime` + `finbert_gate`; missing/malformed → default (02:30, gate on).
  ⚠ **Since 08-19 NO window disables the gate** — file holds only DEFAULT line,
  so sentiment `BAD` honoured at every hour. Removed window also governed
  overnight/weekend PRs (resolved against deferred 04:00 start) — overnight
  `BAD` no longer survives to 04:00.
- **`trade-mole-2.6.py`** — 2.5 + 10-08 pass (**NOT loaded**: clerk-1.2:144 names
  2.5; the switch is that one line + a clerk restart, operator's call). Report
  `mole-outputs/surge-predictor-analysis-2026-10-08.md`. 219 labels, same balance
  (`total = neg + 0.4×neu + 3×(pos+late missed)`), 2.5 corrected = pos 58/66 neu
  23/34 neg 31/101 late 16/18 **total 70.2**. **Decisions identical to 2.5** (0
  changes on 219 labelled + 473 unlabelled; `replay_mole --self-check` 812/1681
  both). **No rule changed**: every rule pays for itself (leave-one-out PRE +45.8,
  D +41.8, N +26.2, E +24.2, C +6.0) and 17 constants swept one at a time move 1–2
  files or add unlabelled fires. Option F (D needs 2 trades, not 3) moves FNGR/VCIG
  (late) and CJMB/LABT (pos) 1.3–6.9 s earlier, 4–9% cheaper, adds AZI 08-07 + FUSE
  08-04 (unlabelled) — label those to decide. **NEW, OFF:** `CONFIRM_WINDOW_SEC`
  (None) / `CONFIRM_MIN_TRADES` / `CONFIRM_MIN_DAYVOL_LOTS` / `CONFIRM_RULES` —
  `_confirm_gate()` HOLDS a trade-path signal until N new trades print (a twin
  report of one fill counts once) or drops it at the window end; PRE never held.
  Tails (19 trade-path signals): "1 trade in 1 s" keeps 8/8 good, 3/5 grey, 2/6
  bad (Fisher p 0.015 before ~9 checks); whole-mole on the 27 files from 10-05:
  total 9.8→7.4, good files fire 0.01–0.75 s later. ⚠ Costs: first print comes at
  a median 0.25 s (RTVolume = 250 ms batches), ask +2.8% median / +11% on the
  fastest good files; "≥2 trades" LOSES good ones (an early count double-counted
  twin prints). Turn on only after ≥40 trade-path tails keep ≥90% of good
  (`confirm_after_signal.py --tails --checks`). ⚠⚠ **Stale stock list 10-01/02/03:**
  the nightly job stalled (finviz 403), the orchestrator served `stocks_universe_
  2026-09-29` for 3 days; ONLY those days since 07-09 (checked all 92) — 15
  labelled files (6 of 18 late). Rebuilt with RetroUniverse-3
  (`mole-outputs/baseline-overrides-2026-10-08.csv`; 34/34 validation rows within
  1.6% of the real file): values off 1.2×–212× but only CUPR (late, no longer
  fires) and TNMG (45.7→38.7 s) change. Labels follow the price (x-wing, n=164:
  best bid in 60 s median +4.5% good / +1.3% grey / −1.6% bad). Hidden-volume
  "surge start" occurs on 50% of BAD files — not a trigger.
- **`trade-mole-2.5.py`** — 2.4 + 10-02 pass (**loaded by clerk-1.2:144 since the
  10-04 20:12 restart**; first recordings with a tail are 10-05). Report
  `mole-outputs/surge-predictor-analysis-2026-10-02.md`. 192
  labels; operator's trade-off `total = neg + 0.4×neu + 3×(pos+late missed)`,
  and late fixes may add NO neg. **One decision change:**
  `SURGE_VWAP_GATE_RULES`=(A,B,C,D,NF) — the −1% VWAP gate now holds only rules
  with no price-rise leg; `None` = 2.4. ⚠ That VWAP is IBKR's WHOLE-DAY VWAP
  (RTVolume field), not the session's (2.4's docstring is wrong): names that
  traded higher earlier in the day were held until the move regained the old
  average. LESL 09-30 +26.2s→+3.9s (27.5% cheaper), LGHL +57.9s→+2.5s; nothing
  else on the labels; unlabelled 74→76 (IMCC 09-23 good, SLE 08-14 −4.6%).
  Keeping the gate on D is what keeps PHGE 07-24 (neg) out. Counts unchanged
  (pos 54/62 neu 16/27 neg 24/90 late 12/13, total 57.4): ⚠ **no veto at the
  trigger instant separates good from bad** — held-out AUC 0.43–0.53 over every
  tape/hidden-volume/news/stock feature; every active rule pays for itself
  (leave-one-out +9 to +46). Confirmation wait unmeasurable on THAT corpus (102/106
  fired files had <1s recorded after the fire) — measured 10-08 on the tails, see 2.6. **Record-only** (no rule reads them):
  `true_day_volume`/`true_vol_5s`/`true_vol_x_normal_5s` (IBKR day volume incl.
  the sub-100-share prints the print path drops) and `tws_trade_count` — ⚠ tick
  54 arrives via **tickGeneric**, only tickSize handled it, so that column was
  empty forever and `_reconcile_trade_count` has NEVER run (still not called).
  ⚠ `hist_baseline_trade_size` is SHARES, live sizes LOTS: `flow_x_baseline_5s`
  is 100× understated (cosmetic). Verified: setting off ⇒ 2.4 decisions on all
  192+472 files and every 2.4 column on 445 replayable files.
  **10-03 POST-TRIGGER TAIL** (`POST_TRIGGER_RECORD_SEC`=60, 0 ⇒ 2.4): recording
  no longer stops at the buy-signal. For 60 s, or until the clerk tears the
  session down, every tick is kept RAW (`post_trigger=True`; trades skip ITIs,
  windows and `should_fire` — 3.6 µs/trade msg, 2.4 µs/quote, measured) and
  clerk-1.2 rewrites the CSV at tail end with one `POST_TRIGGER_END` row
  (`complete after Ns` / `session_end:<why> after Ns`; none ⇒ crash). Tick
  handlers are split public→`_tick_*` body: pre-trigger path unchanged, tail
  path try/except'd (the clerk feeds the mole BEFORE x-wing, unguarded).
  `replay_mole` replays tail rows by default, so a stricter rule CAN fire
  later: verified LESL — 2.4's rules on a 2.5 tail file find their live +26.2 s
  fire @ 0.244. ⚠ The tail holds our OWN x-wing prints. ⚠ A session ended by
  the sentiment gate keeps a short tail: teardown is never delayed for it.
  Writes are atomic (`.tmp` + `os.replace`).
- **`trade-mole-2.4.py`** — 2.3 + 09-18 precision pass (**loaded by clerk-1.2
  since 09-18**; clerk-1.1 stays 2.2). **2.3→2.4: pos 35/39→33/39, neu
  14/23→12/23, neg 25/70→11/70** (cost `neg+0.4×neu` 30.6→15.8, −48%).
  ⚠ Unlabelled control (206 recordings): fires 78→55, **zero added**.
  **Organising finding: 2.3 mixed RELATIVE (B/C/pretrade) with ABSOLUTE (D/E)**;
  ITI spans 0.4s–34,200s. New: `SURGE_E_MIN_FLOW_X_BASELINE`=10.0 (flow vs
  normal, [5,25]); `SURGE_MIN_PX_VS_VWAP_PCT`=−1.0 (direction gate, strictly
  negative); `SURGE_D_MIN_VOLUME_5S`=20 lots ([18,21] — recheck first);
  `SURGE_C_MIN_CUM_DOLLAR`=80 lot-$; branch N `SURGE_N_MIN_PRICE_RISE_PCT`=0.04
  (liquid-name surges E can't reach).
  ⚠ **10-02: `GENERIC_TICKS` += `,595`** (ticks 63/64/65 = 3/5/10-min volume),
  requested for **x-wing's** exit calibration — the mole does not branch on
  them. Behaviour-neutral, proven with `replay_mole.py --self-check
  --check-decisions` over `mole-outputs/_*.csv`: 77/253 reproduced before AND
  after the edit.
  ⚠⚠ **Branch NF shipped OFF** — perfect on labels but ONLY source of added
  fires on unlabelled set (four dead-name 1–2-print fires). Set
  `SURGE_NF_MIN_FLOW_X_BASELINE`=700.0 to enable.
- **`trade-mole-2.3.py`** — 2.2 + 08-14 precision pass (prior version; superseded
  by 2.4 on 09-18). On 90 labelled files: **pos 28/28, neu 15/18, neg 23/44
  → pos 24/28, neu 9/18, neg 11/44** (negatives halved).
  ⚠ First fixed measurement: `replay_mole` required TRADE row, silently skipped
  26/90 pre-trade-only files — every number before 08-14 covers trade path only.
  Changes: `RULE_B_ENABLED=False` (overlap, not fixable);
  `PRETRADE_MIN_GAP_VS_CLOSE_PCT`=5.0 (PRE-TRADE ONLY, inverts on trade path);
  `SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC`=1.5; `RULE_A_ENABLED=False` (zero
  unique pos). Rule C stays — 2 unique pos, zero cost.
- **`trade-mole-2.2.py`** — 2.1 + 07-30 precision re-calibration (loaded by BOTH
  clerks since 07-30 — restart to pick up). Rule C 100→1550, Rule A dollar
  2→60, Rule D ITI 0.3→0.28, Rule E price 1%→1.2%, + Rule F pretrade veto
  (kill: `--no-pretrade-veto`). Labelled: pos 21/21, neg 23→13.
  **08-11: Rule E dollar 10→3**, driven by PLAG; inert on labelled but adds 3
  fires on unlabelled (PLAG/OXBR/DXST). Coupled to `DEDUP_EXCLUDE_TWIN_FROM_COUNTS`.
- **`trade-mole-2.1.py`** — trade-frequency surge detector (prior live; clerks
  load 2.2+ since 07-30). **Rule F** (07-23): pre-trade twin of Rule B off
  quotes alone — fires on stale snapshot before first trade prints (~2s earlier).
  Default on (`--no-pretrade-staleness-fire` to disable).
- **`x-wing-2.1.py`** — yield-laddered trailing-stop trader (loaded by clerk-1.1
  only since 08-12; clerk-1.2 loads 3.0). **2.1 = 2.0 + IBKR Price Mgmt Algo
  on BUY entry** (caps-and-works over-aggressive limit instead of rejecting).

See `xmole-commands.txt` for run + trigger commands.

## Legacy (to be DELETED — do not edit or extend)
- **`X-wing-1.0.py`** → superseded by `x-wing-2.0.py`
- **`trade_mole_4.1.py`** → superseded by `trade-mole-2.1.py`

## Offline replay + calibration
- **`replay_exit.py`** (at the x-wing-mole ROOT, not `analyze_trade_mole/` —
  CLAUDE.md said otherwise until 10-06 and the script's own `__file__.parent.parent`
  module lookup carried the same error, so every run died at import)
  `--score-labels` / `--leave-one-out`
  (10-02) — scores the exit rules against labelled tables, reusing
  `eval_rules.py`'s convention verbatim (`glob("_*.tsv")`,
  `basename.split("_")[1]`). Labels `early`/`good`/`hloss` + **`late`** (held
  too long, gave the profit back — v7's failure and the OPPOSITE error, without
  which calibration overfits to "never eject") and **`nofill`** (the LESL
  shape). ⚠ It REFUSES to score a file whose recording ends at the exit or whose
  entry never filled, and says so — `replay_mole.py` once silently skipped 26 of
  90 files and invalidated every precision number before 08-14.
- **`replay_exit.py`** (10-02) — replays a recorded
  `xwing_tables/*.tsv`'s bid/ask/last through the REAL
  `compute_protective_levels()` + `exit_decision()`. ⚠ Uses a **row-driven fake
  clock**: without it the 10 s spread window never trims and the median spread
  comes out far too wide, which silently masks which defence is doing the work.
  `--self-check` asserts the 3 labelled ejections are no longer bid-only exits;
  `--all` replays the week. HCTI −6.06%→+8.38%, ZBAO −3.43%→+10.41%, LESL
  −15.31%→−2.01%.
- **`analyze_trade_mole/replay_mole.py`** — replays mole CSVs through real
  `IBKRSurgeApp` so features can't drift; fake clock advances to each row.
  `--self-check`: 74/74 labelled + 46/46 2.2-era reproduce (older disagree on
  decisions — rule gens changed; `--check-decisions` includes them).
  ⚠ Until 08-14 **skipped pre-trade-only recordings** (required TRADE row) — 26/90
  files excluded; now falls back to `PRETRADE_SURGE`/`PRETRADE_VETO` row.
- **`analyze_trade_mole/eval_rules.py`** (08-14) — **corpus-wide scorer; use this,
  not `sweep_thresholds.py`**, for anything spanning whole label set. Scores
  `_neutral_`/`_medium_` files, includes pre-trade, reports one weighted cost
  (`neg+0.4×neu`). `--leave-one-out` prints each rule's marginal pos/cost.
  10-02: `total` (a missed pos/late = 3 neg), `--timing` (fire time/price vs the
  surge start), `--against-module/--against-set` (per-file diff vs a
  reference — the equivalence check), `--unlabelled` (control set),
  `--min-date/--max-date`. 10-08: `_neu-late_` (scored grey, timed like late),
  `--baselines CSV` / `--against-recorded-baselines` (replay with corrected
  baselines, see 2.6), held-signal lower/upper bounds for `CONFIRM_*`.
- **`analyze_trade_mole/confirm_after_signal.py`** (10-08) — the first seconds after
  a signal from the raw rows: `--tails` (per-file table), `--checks` (every
  candidate check on the tail files: kept good/grey/bad, wait, ask cost). Counts
  one fill reported on both RT channels once. `early_signal_search.py` — a more
  sensitive rule set + a check; `sweep_constants.py` — one constant at a time
  through the real mole, labelled + `--unlabelled` + `--files`;
  `make_baseline_overrides.py` — RetroUniverse-3 output → the corrected-baseline
  table; `verify_confirm_gate.py` — unit + end-to-end checks of the 2.6 gate.
- **`analyze_trade_mole/session_context.py`** (10-02) — joins a recording to its
  news row (`orchestrator5/tables/news_output_<date>.tsv`, ±5 s on arrival) and
  stock facts (latest `stocks_universe_<D>.tsv` with D < arm date), gives the end
  reason (fired / news_bad / expired) and the offline **surge start**: first
  moment IBKR's day-volume counter rises ≥20× normal and ≥10 lots over 2 s.
  Reference only — never a live rule.
- **`analyze_trade_mole/sweep_thresholds.py`** — sweeps constants through REAL
  `should_fire()`. ⚠ Blind to pre-trade-only recordings.
- **`analyze_trade_mole/analyze_feed_lag.py`** — exchange→local-arrival lag,
  bucketed by observation length. Spread monotonic 0.0008s→**0.325s median**.

## reqTickByTickData probe (2026-08-11) — asked, measured, REJECTED
- **`analyze_trade_mole/probe_tickbytick.py`** — compares `reqMktData` vs
  `reqTickByTickData` on one client. Read-only, paper GW.
- **Do not migrate.** Measured 0.16–0.43s SLOWER on small caps; epoch-second
  stamps (vs ms); cap is 5/connection not 3.
- ⚠ **Real finding: RTVolume volume is in LOTS** (confirmed exact vs tick 8).
  `_cum_volume`/`dollar_rate_*` ~100x understated; fills under 100 shares
  truncate to 0 and drop (~64% of prints, ~8% of volume).
- ⚠⚠ **Fix neither.** The 100x is cosmetic. The odd-lot drop is **load-bearing**:
  unfiltered tape makes non-surging small caps look surging (24–62% of intervals
  under Rule D's bar). AST-verified behaviour-neutral.
- Two feature defects behind flags **default OFF** (`DEDUP_EXCLUDE_TWIN_FROM_COUNTS`,
  `RATE_NORMALIZE_BY_SESSION_AGE`) — flipping either without re-deriving A/D/E
  drops recall 16/16→14/16.

## Notes
- Run with venv python that has ibapi (system `python3` only for `py_compile`).
- Lifetimes parse `mm:ss` (`00:15`=15s). Port 4001 = LIVE Gateway; 4002 paper
  GW / 7497 paper TWS.
- Rare marketable BUY LMT entries come back IBKR-"Inactive" with no error reason
  (x-wing aborts). Price Mgmt Algo does **not** fix (NCRA 07-29 died with it);
  x-wing-3.0 sets it False since its only effect is letting IBKR re-price under
  a tracking limit. Part B rate climbing: 0.00%→0.49%→1.37% Jun→Jul→Aug.
- **ARM→live-Ask latency, fixed 07-21:** clerk fires `reqMktData` on bare
  `STK/SMART/USD` (IBKR resolves server-side) instead of blocking on
  `reqContractDetails` (~560ms saved). conId/minTick resolve in background
  thread; entry waits on `_contract_ready`. ⚠ Per-client slot (reqId 98) is
  shared — don't re-arm until resolver has consumed it.
