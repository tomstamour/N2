# x-wing-mole

IBKR (ibapi) trading trio. A surge detector (`trade-mole`) watches a ticker and,
on trigger, hands a buy-signal to an order manager (`x-wing`); a `clerk` runs both
together in one process on a single shared ibapi connection.

## Active scripts
- **`x-wing-3.0.py`** — x-wing-2.1 + an entry limit that **TRACKS THE ASK**
  (2026-08-12, **loaded by clerk-1.2 since 2026-08-12; paper-GW verify pending**).
  ⚠⚠ **2026-08-27 — now also bounded by IBKR's REGULATORY PRICE WALL, and it
  re-places once when the wall kills the entry.** Two entries died to the cap the
  same day: **YAAS** priced its OPENING shot at 3.13 off a **21.5 s stale 1-lot
  ask** (3.05 vs a 2.45 bid, 24.5% spread) against a **2.99** wall → 202, dead in
  269 ms, `track_n=0`, and the stock then ran to a **4.33 bid (+60.4%)**;
  **CJMB** was 2161-capped to 2.25 and **live**, then OUR OWN `CHASE_CEIL` to the
  2.44 ceiling crossed the **2.40396** wall and killed it (EHGO 08-19 identical).
  The band is exact: **BUY wall = IBKR_ref × 1.196, SELL = × 0.902** (+19.6% /
  −9.8%; the old "202 wall at +9.8%" note was the SELL side). **IBKR's reference
  is its own and LAGS the tape by seconds.**
  ⚠⚠ **Structural, and the reason CJMB/EHGO are not bad luck: whenever a 2161 has
  fired, the +6% ceiling sits AT OR ABOVE the wall** (CJMB +1.5%, EHGO +3.0%,
  CAST +0.5%, FEED +0.04%, XOS −0.09%), so `CHASE_CEIL` and `CAP_RESCUE` were
  **202 generators on every capped run** — CAST's shipped 08-18 rescue targeted
  1.0300 against a ~1.0252 wall.
  `_entry_upper()` = `min(entry_ceiling, _wall_bound())` now replaces every bare
  `entry_ceiling` on the entry path; `_wall_bound()` prefers an **observed 202
  wall** (exact), else an **observed 2161 cap × `ENTRY_WALL_OVER_CAP_MULT`=1.06**,
  else a **dark estimate** (`ENTRY_WALL_ESTIMATE_ENABLED=False`). New event
  `CHASE_WALL` separates "hit OUR ceiling" from "hit IBKR's wall".
  **1.06 is corpus-bounded, not theory**: CJMB's wall forces `< 1.06843`, REBN
  (capped 1.63, **filled 1.72**) forces `>= 1.05521` ⇒ window **[1.05521,
  1.06843)**. ⚠ The window over ALL capped runs is **EMPTY** (`[1.09358,
  1.06843)`) and that is real — a cap and a later chase are priced off DIFFERENT
  references. Only the **fill-reaching** bound binds (a limit is not a fill: REBN
  chased 1.75 and filled 1.72; CAPS/SGLY filled AT their caps), and only 3–4 of 10
  capped entries ever filled. **Err LOW** — too low leaves the order ALIVE at a
  worse limit, too high gets it CANCELLED.
  `retry_entry_after_wall()` re-places ONCE (budget `ENTRY_WALL_RETRY_MAX`=2) on a
  **NEW orderId** (a 202 is a confirmed cancel; reusing the id is error 103) at
  `min(wall, ceiling)`, qty = REMAINING. ⚠ **`entry_ts` is NOT reset** — the 10 s
  timeout stays anchored on the FIRST placement, so a retry cannot undo the
  30→10 s cut. UP stays frozen (IBKR just said we are too aggressive).
  ⚠⚠ **It fires even when the bound is BELOW the live ask, and that is the
  point** — IBKR's reference lags and catches up. **IVF 2026-08-17** is the proof
  on the sell side: the identical 2.39 was refused 11 times in 1.66 s against a
  stale 2.6899 reference and **accepted 5 s later**.
  ⚠⚠ **THE ORDERING FIX, without which the retry can never fire on the YAAS
  shape**: the **202 arrives AFTER the `Cancelled` it explains**, so the abort is
  held open (reusing the 0.75 s `ENTRY_TRACK_STATUS_GRACE_SEC`) long enough for
  the error to classify it. CJMB/EHGO only survived because a modify happened to
  be in flight — luck, not design. A Part-B `Inactive` still aborts immediately.
  Also fixed in the same pass: **the protective cancel+replace had no counter, no
  throttle and no gap and re-sent the IDENTICAL price** — IVF burned orderIds
  34→44, **11 orders in 1.66 s**, position **unprotected ~6.4 s**. Now budget 3 +
  0.5 s gap + token bucket, and on a 202 the exit is lifted **above** the sell
  wall via `_wall_step_up()`. `T47-airspeeder-2.0.py` gained 3 getattr-guarded
  lines arming the late-fill guard on `{active_buy_id} | _retired_entry_ids`.
  Table gains `wall_obs` + `ref_snapshot` (append-only, 26 cols) + events
  `CHASE_WALL/ENTRY_WALL_202/ENTRY_RETRY_n/ENTRY_WALL_RETRY_REFUSED`. Kill
  switches `--no-entry-wall` (full 3.0) and `--no-entry-wall-retry`. Verified by
  **185 offline checks** (was 125) incl. YAAS/CJMB/EHGO/IVF replays with pre-fix
  assertions; calibrated by `analyze_trade_mole/calibrate_price_wall.py`
  (`--self-check` reproduces both bands from raw logs). **Paper-GW verify pending.**
  2.1 priced the entry once and left it resting GTC. 3.0 re-prices the SAME
  orderId in place so the limit holds `MAX_LIMIT_ENTRY_PERCENT_PRICE`=**2.5%**
  above the LIVE ask, **in both directions**, bounded by a ceiling frozen at
  placement (`ENTRY_TRACK_MAX_PCT`=6.0 ⇒ `trigger_ref × 1.06`).
  **The DOWN ratchet is the half the evidence supports** and is NOT time-boxed:
  all 20 filled entries that took >10 s paid **100% of the pad**, because a
  static limit is already an uncontrolled chase run by IBKR up into itself
  (BAOS −13.40%, NIVF −11.54%, GWH −9.35%). The **UP** follow is time-boxed to
  `ENTRY_TRACK_UP_WINDOW_SEC`=3.0 (84.6% of fills land ≤3 s; every deep-tail
  loser is ≥7 s), then `_track_up_frozen` — down keeps running to the 10 s
  `ENTRY_FILL_TIMEOUT_SEC`.
  ⚠ The window check is **LAZY** — it lives inside `if target > working +
  delta_floor`, so it does not fire at t=3 s, it fires on the **first up-move the
  tracker actually WANTS** after 3 s. On a flat-then-jump tape that is the very
  move that would have filled: the window expires *onto* that move, not quietly.
  (The 2161 rescue below no longer depends on this — since 2026-08-18 it has its
  own branch and fires on a flat ask.)
  ⚠ **`ENTRY_CAP_RESCUE_ENABLED`=True (2026-08-13, **REPAIRED 2026-08-18 — it had
  never once fired**) — ONE UP move when IBKR has us parked under the offer.
  Armed if `entry_capped_to` is set AND **the cap itself** is below the live ask,
  the shot is unspent, no 202, the 3 s window has elapsed (or UP is frozen), and
  the ceiling is more than a dead band above the working limit; blocked outright
  by a 202; spent only when the modify reaches the wire (set beside `_track_n +=
  1`, never where `rescue_armed` is computed, or the throttle burns it). Event
  `CAP_RESCUE`, priced at the **CEILING**.
  ⚠⚠ **As shipped on 08-13 it had THREE independent blockers, each fatal alone,
  and fired 0 times across 10 capped entries in 6 days** (BBLG×2, CAPS, DAIO,
  FEED, MGRX, REBN, RCT, XOS, CAST). (1) **Unreachable** — the whole rescue was
  nested inside `if target > working + delta_floor`, and a capped order under a
  FLAT ask can never open that gate because our own limit already sits 2.5% above
  the ask, so `target` EQUALS `working`. (2) **Never armed** — `working_px`
  preferred `entry_lmt_working` over `entry_capped_to`, but **`openOrder` echoes
  the limit WE asked for, never IBKR's cap** (CAST `openOrder` 0.9943 vs cap
  0.9600; MGRX 0.5915 vs 0.5764; REBN 1.70 vs 1.63; BBLG 1.01 vs 0.9646; CAPS
  0.3383 vs 0.3254; FEED 0.449 vs 0.4346; RCT 0.244 vs 0.2377; XOS 2.31 vs 2.24),
  so `capped_inert = working_px < ask` was **permanently False in production**.
  (3) **A no-op even if armed** — it targeted `ask + offset`, which on a flat ask
  IS the limit we already hold. **CAST 2026-08-18** is the case: capped 0.9600
  against an ask **frozen at 0.9700 for the full 10 s**, `0.9943 > 0.9943+0.0061`
  false, DOWN false (`0.9943 > 1.0185` false), entry died with **n=0**.
  Fix = the rescue gets its OWN `elif` between UP and DOWN (so a flat ask reaches
  it), `capped_inert` reads `entry_capped_to` alone, and the target is
  `entry_ceiling`. The **3 s gate is deliberately RETAINED** so every currently
  working path is untouched — REBN, the only successful capped chase on record,
  still takes the plain in-window `CHASE_CEIL`. The rescue sits BEFORE DOWN
  (capped-and-inert ⇒ raise, never lower) and is self-limiting: once the ask falls
  to/below the cap the order is no longer inert and DOWN takes over. The
  room check (`ceiling > working + delta_floor`) lives in the ARMING, not after the
  branch chain — arming without room would swallow the `elif` and strand the limit
  with no DOWN ratchet for the rest of the run.
  ⚠ **A limit is not a fill**: REBN's went to 1.75 and it filled 1.72 (the
  then-ask); CAPS filled at its 0.3254 cap, under its 0.3383 limit. So ceiling
  pricing does not mean paying the ceiling — the bounded worst case is unchanged
  at +6% over `trigger_ref`, which `CHASE_CEIL` already reaches routinely.
  ⚠ **Whether the rescue actually FILLS is still unknown and the logs cannot say.**
  Over 10 caps `cap/ask` is 0.9873–0.9990 and `cap/limit` 0.9550–0.9745, but
  `limit/ask` is near-constant (1.025–1.037, cv 0.004) ⇒ the two anchors are
  COLLINEAR. Cap-follows-market ⇒ the rescue never fills; cap-is-a-haircut-on-our-
  limit ⇒ CAST's cap goes 0.9600→~0.9945 vs a 0.9700 ask and fills. **The rescue is
  the experiment** — watch several `CAP_RESCUE` rows before concluding anything.
  **Only 3 of the 10 capped entries ever filled** — a 2161 is close to a death
  sentence for the entry, and that is the base rate any rescue is measured against.
  Originally motivated by the 08-13 pair: **MGRX** capped 0.5764 vs a 0.5770 ask,
  ask flat 4.5 s (n=0), first leg up at **4.539 s** → frozen permanently → aborted
  → stock ran **+27.3%**; **REBN** the identical cap (1.70→1.63 vs 1.64) legged up
  at **1.775 s**, inside the window → `CHASE_CEIL` 1.70→1.75 → **filled 1.72 in
  139 ms**. REBN filling *above* its own 1.70 limit is the proof the lever is real:
  IBKR works a capped order up "to a more aggressive price **still within your
  specified limit price**", so our limit is the envelope. A capped order is *not* a
  chasing order — it is inert. **Post-fix MGRX rescues on the FLAT ask at the 3 s
  mark** (0.5915→0.6117) instead of waiting for a leg up it may never get; REBN
  replays unchanged. Does NOT reopen "extend the timeout" (still rejected) and
  would NOT have saved PFSA (cap/ask 0.859, still ~14% under).
  ⚠ **Out of scope, and probably the bigger problem: the +6% CEILING.** RCT and XOS
  (both 08-17) were capped AND chased AND still missed, because the ask ran past
  the ceiling — RCT 0.2380→0.2800 (+17.6%) vs a 0.2523 ceiling, XOS 2.2500→2.9500
  vs 2.3900. Of the 7 capped entries that never filled, the ceiling (not the cap)
  is what bound RCT, XOS and FEED. Hysteresis `ENTRY_TRACK_DOWN_HYST_MULT`=2.0 (lower
  only once the limit is >5% over the ask), dead band `max(1 tick, 25% of
  offset)`, throttle 0.25 s, budget 20 modifies, process-global 30 msg/s token
  bucket + an **error-100 handler** (nothing in the repo handled it before).
  **`MAX_CAP_ENTRY_PERCENT` is None** — the `last_close × 1.30` clamp priced 7 of
  317 entries BELOW the market (GLE −24.8% … ICCM −70.4%) and made T47 re-entries
  structurally unfillable; the trigger-ref ceiling replaces it.
  **`ENTRY_USE_PRICE_MGMT_ALGO=False`** — it prevents neither 2161 (predates 2.1)
  nor Part B (NCRA died with it on); it just meant IBKR was re-pricing the order
  underneath us. New: 2161 is parsed for its capped price → `ENTRY_CAPPED` row;
  `on_open_order` (fed by both clerks, getattr-guarded) exposes the price IBKR is
  really working; 202 freezes UP only; `{104,105,161,10147,10148}` stop tracking
  without aborting; a `Cancelled` within 0.75 s of a modify is deferred (a
  Part-B `Inactive` is **not** — that series must stay comparable).
  ⚠⚠ **`ENTRY_MODIFY_RETRY_CODES = {103}` (2026-08-14) — a REJECTED MODIFY used to
  blind the tracker for the rest of the run.** IBKR 103 "Duplicate order id" is a
  **registration race**, not a bad order: a re-price that reaches the Gateway
  before it has registered the original `placeOrder` is read as a NEW order
  re-using a live id. The order survives at its previous limit — but 3.0 had **no
  handler for 103 on the entry path at all** (it sits in `MODIFY_REJECT_CODES`,
  which is consulted only for the *protective* order), so `entry_lmt` kept the
  price that never existed. `track_entry_limit` reads `entry_lmt` as `working`,
  and `target > working + delta_floor` compares a target **against itself** ⇒ can
  never be true ⇒ **the tracker concludes it already did the job and goes silent,
  permanently**. Self-sealing: no later quote can wake it. **SCNX 2026-08-14** is
  the case — entry 0.4481, ask legged to 0.4504, `CHASE_UP`→0.4617 rejected 2 ms
  later, order then rested **0.5% under a motionless 0.4504 offer for 3.8 s**,
  timed out, stock ran to 0.6277 (**+43.6%**). Fix = `_track_prev_lmt` +
  `_rollback_entry_lmt`: restore `entry_lmt` to the price IBKR is really working,
  then **keep tracking for 103 only** — the next quote re-issues the identical
  modify, held ≥0.25 s by the throttle, by which time the order is registered
  (orderStatus ack latency median **149 ms**, p90 577, max 1305 over 36 entries).
  Bounded by the 20-modify budget, the 3 s window and the token bucket, so it is
  not the blind retry the stop codes forbid; `{104,105,161,10147,10148}` roll back
  **and** still stop. Content errors (106, 110) stay out of the retry set — they
  recur identically. ⚠ Non-deterministic by gap: SCNX lost it at **+4 ms** and CAPS
  08-12 at +52 ms, while CYCU (**+0 ms**) and NEXR (+46 ms) were accepted — so
  **gating the first modify on the ack was evaluated and REJECTED**: it costs the
  full ack latency on every chasing run, at the start, where the tape moves
  fastest, to avoid a race the rollback clears in one 0.25 s throttle interval.
  2 of the 9 modifies since 3.0 went live drew a 103; **zero before 3.0** (checked
  07-15 → 08-11) — the in-place re-price is what exposes it.
  ⚠ **`on_open_order`'s "price-capped by IBKR" warning was a MISNOMER and is now
  the second trigger.** `openOrder` echoes the limit **we asked for**, never
  IBKR's 2161 cap: all 7 genuine caps since 3.0 (REBN, MGRX, CAPS, BBLG×2, FEED,
  DAIO) reported our own limit back (MGRX: `openOrder` 0.5915 while the cap was
  0.5764 — and note **MGRX DID receive an `openOrder`**; the older claim that it
  did not is false, the `entry_capped_to` fallback is load-bearing for this
  echo reason instead). All 4 firings of that warning were rejected modifies. It
  now rolls back too, guarded by `px == _track_prev_lmt` (±½ tick) so it fires
  only when IBKR is working **exactly** the price we tried to replace — which is
  what lets it catch a modify dropped with **no error code at all**, and what
  stops a stale echo or a genuinely-reported cap from triggering a false rollback.
  Table gains 5 appended cols (`entry_order_id, entry_lmt, entry_status, track_n,
  capped_to`) + events `CHASE_UP/CHASE_DOWN/CHASE_CEIL/CAP_RESCUE/ENTRY_CAPPED`
  + `MODIFY_REJECT_<code>` (`_103`, a stop code, or `_openOrder`), concatenated
  with `+` when a reject and its retry land in the same control-loop pass.
  **A `MODIFY_REJECT` row means the tracker was briefly lying about the order** —
  `entry_lmt` in the rows before it is the price we asked for, not the resting one.
  A 202 that is the **ack of our own cancel** (`_entry_abort_pending`) is logged as
  such instead of as the aggressive-limit wall — MGRX mislabelled one 96 ms after
  its own timeout-cancel, which is cosmetic until 202 starts blocking the rescue.
  ⚠ Offset must stay a **PERCENT**: spread in cents varies 27× across the price
  buckets, in percent only 1.9×, and 45% of entries are sub-$1 where minTick is
  $0.0001 (2 ticks = 0.03%, inert). 2.5% is a measured knee (first-shot coverage
  82.9% @1.0% → 91.0% @2.0% → **96.0% @2.5%** → 97.0% @2.9%), set by feed
  staleness (ask is 0.3–0.9 s old; p90 ask move in 1 s is +3.74%).
  ⚠ `--no-entry-track` reverts to 2.1 single-shot behaviour; `ENTRY_CAP_RESCUE_ENABLED
  = False` reverts just the rescue; emptying `ENTRY_MODIFY_RETRY_CODES` reverts
  the 103 retry while keeping the rollback. Verified offline by
  `analyze_trade_mole/verify_xwing3_tracking.py` (**125 checks**, incl. a GLE
  replay, the MGRX/CAST/REBN cap-rescue set, and the SCNX 103 replay — which also
  asserts the pre-fix behaviour, i.e. that without the rollback the tracker is
  provably silent on the very quote that would have filled).
- **`clerk-1.2.py`** — TRIO clerk (2026-07-26, **paper-GW verify pending**): runs
  trade-mole + T47 + a SEQUENCE of x-wing runs per trigger on one shared client.
  **Loads `x-wing-3.0.py` since 2026-08-12** (loader line `clerk-1.2.py:135`;
  clerk-1.1 stays on 2.1 as the live duo fallback). Both clerks now also forward
  `openOrder` → `ctl.on_open_order` behind a `getattr` guard, which is what keeps
  clerk-1.1 loading 2.1 unchanged.
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
  ⚠ **ONE LIVE SESSION PER SYMBOL since 2026-08-12** (this fix landed in 1.2 only —
  1.1 still double-arms). `handle_trigger` rejects an arm for an already-armed ticker
  with `{"status":"rejected","reason":"already_armed"}`, logged at WARNING with the
  incumbent's clientId. It exists because O5.2 arms per ARTICLE and the wire
  re-publishes the same PR under a new ID: on 2026-08-12 four PRs arrived twice and
  three armed twice, so NEXR ran two sessions (clients 1014+1015) that **each entered
  and each stopped out** — 2× size, 2× commission, two ladders competing on the exit.
  Nothing downstream could catch it: every session has its own clientId, and x-wing
  tracks position from its **own fills only** (no `reqPositions`), so session B starts
  at `position = 0`. O5.2's per-ticker dedup is the first line of defence; this is the
  backstop, so **an `already_armed` line in `clerk.log` means O5.2's dedup missed a
  shape** — read it.
  The check is a check-and-**claim** under `_sessions_lock` via an `_arming` set, not a
  bare `in self._sessions` read: `handle_trigger` runs on a thread per connection and
  real duplicates land ~90 ms apart, well inside the acquire+construct window.
  Two companions landed with it: the `session_start_failed` path now **unregisters**
  (it called `register_session` and returned without it — harmless before, a permanent
  symbol block once the guard exists), and `unregister_session(session)` is
  **identity-checked**, popping only when the stored session IS the one finishing
  (popping by symbol let the first of two same-symbol sessions delete the second's
  routing entry, stranding a live session on `sentiment_ignored`).
  ⚠ Known-latent, deliberately unfixed because the guard prevents the collision:
  `PendingStore.upsert/remove` are keyed by symbol alone, and per-session file paths
  are symbol + second-resolution timestamp — on 08-12 both NEXR moles wrote the same
  `trade-mole-table_NEXR_2026-08-12_07-58-00.csv` and shared one T47 TSV.
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
  ⚠ **Since 2026-08-19 NO window disables the gate** — the file holds only
  `DEFAULT lifeTime=02:30 finbert_gate=on`, so O5.2's `BAD` is honoured at every
  hour and there is no surge-only trade path. The `finbert_gate` mechanism is
  still in the code, only ever taking its gate-on branches. The removed
  `WINDOW 04:00-06:59 lifeTime=00:10 finbert_gate=off` line is kept commented out
  in that file's header with its rationale; see `version-control.txt` 2026-08-19.
  Because the built-in fallback equals the DEFAULT line exactly, **no parse
  outcome — missing file, typo, truncation — can re-enable the bypass.**
  ⚠ That window reached further than its span: `conditions_instant()` resolves a
  closed-market arm against its **deferred collection start** (04:00), not its
  arrival, so overnight/weekend PRs were governed by it too (88 historical rows,
  only 75 of them 04:00–06:59 arrivals). Removing it therefore also reversed the
  2026-07-28 entry's intended behaviour change: an overnight `BAD` PR no longer
  survives to 04:00, it is released at sentiment arrival.
- **`trade-mole-2.3.py`** — 2.2 + the 2026-08-14 precision pass on the operator's
  labels (**NOT loaded by any clerk — both still load 2.2; flip the `_load_module`
  line to adopt it**). On all 90 labelled files ≥2026-07-09:
  **2.2 = pos 28/28, neu 15/18, neg 23/44 → 2.3 = pos 24/28, neu 9/18, neg 11/44**
  (negatives more than HALVED; budget was ≤5 positives, spent 4).
  ⚠ **First it fixed the measurement, not the rules**: `replay_mole` required
  `rt_source` and a TRADE row for the baseline, so every **pre-trade-only
  recording was silently skipped by the whole sweep stack** — 26 of 90 labelled
  files incl. 8 pos / 10 neg — and the pretrade path is ~HALF of all false fires.
  **Every trigger precision number published before 08-14 describes the TRADE
  PATH ONLY.** Fixed in `replay_mole.py` (453 self-check files reproduce before
  AND after; 51 files moved `skipped`→replayable, nothing else changed).
  Four rule changes, all behind constants:
  (1) **`RULE_B_ENABLED=False`** — B bought 1 pos (SLND) for 5 neg and is not
  threshold-fixable because CVKD 20777 sits ABOVE SLND 13657 (2.2's own header
  said so; 2.3 acts on it). (2) **`PRETRADE_MIN_GAP_VS_CLOSE_PCT=5.0`** — the
  pretrade rule fired on staleness alone, and **staleness is not direction**; the
  direction was already in ticks **4 (last)** and **9 (prev close)**, which the
  mole received and threw away. Gap `(last−close)/close` at the fire instant:
  pos median **+7.84%**, neg median **+0.91%**. Keeps 8/8 pretrade-ONLY positives,
  drops 5 of 7 firing negs. **Both ticks REQUIRED** (no tick 4 ⇒ no fire; that
  alone stops BLNE) — substituting the ask was measured and REJECTED (destroys the
  separation). It **DEFERS, never disables** (GWH is held, then fires when tick 4
  lands at +6.43%). ⚠ **PRE-TRADE ONLY — on the trade path this feature INVERTS**
  (Rule B's negs gap up MORE than its pos, +8.2% vs −0.3%). (3)
  **`SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC=1.5`** — D's classes are identical on
  EVERY feature (same median ITI 0.25, same trades_in_5s 3) and differ only in
  WHEN: all 6 pos fire ≤0.501s, while IDAI 2.8s / BNRG 3.0s / NUWE 47.3s / OMH
  2.5s / VTAK 15.7s are late. [0.6,2.0] all score identically; 1.5 is mid-plateau
  (VSEE the slowest pos at 0.501s, OMH the earliest late FP at 2.502s).
  (4) **`RULE_A_ENABLED=False`** — contributes ZERO unique positives (IPW is caught
  the same instant by another leg). ⚠ **Rule C is untouchable**: 2 unique positives
  and removing it saves EXACTLY ZERO cost. Adds `snap_last`/`snap_close` columns
  (+`snap_gap_vs_close_pct` on the PRETRADE_SURGE row); otherwise
  **feature-identical to 2.2 — 698 recordings match on every shared column, 0 differ**.
  ⚠ `RULE_B_ENABLED=True` is a real alternative operating point, **strictly better
  than 2.2 on every axis** (pos 28/28, neu 10, neg 16) — one flag away if the
  recall loss is unwelcome. Median pos time-to-fire 0.050s→0.250s.
  Report: `mole-outputs/surge-predictor-analysis-2026-08-14.md`; scored by
  `analyze_trade_mole/eval_rules.py`.
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
  **2026-08-11: Rule E dollar gate 10→3** (`SURGE_FLOW_MIN_DOLLAR_RATE_5S`), the one
  constant not from the 07-30 pass. Driven by PLAG 2026-08-11 07:30 — a FLANGSolo PR
  whose 149s watch held TWO fills; Rule E's price leg passed at +3.06% while
  `dollar_rate_5s` reached 4.46, and no other rule was within 35x (A blocked outright
  at `quote_updates_10s`=3 vs 11; B/C/F are relative to the name's own cadence and
  PLAG's `itiBaseline` is 876.9s, so its 11-min pre-PR gap scores a *normal* 1.31).
  Costs nothing on the labeled corpus — `sweep_thresholds.py --fixes none` is
  identical 10→3 (pos 16/16, neg 11/20) — **because the dollar leg is INERT there**,
  so the corpus neither endorses nor forbids it; the `+1.2%` price leg is now the
  whole of Rule E's selectivity. **Don't quote that as "free"**: over ALL 2.2-era
  recordings (≥07-30, 54 of 114 scored — the unlabelled live sessions the sweep never
  sees) fires go **27→30**, the new ones being PLAG, OXBR 08-04 and DXST 07-30, plus
  REBN 08-11 firing 2.25s earlier via E instead of C. OXBR/DXST are unlabelled with no
  prints after the fire point ⇒ **unknown** whether they're wins; the cut rests on
  PLAG (n=1) + no measurable negative. ⚠ `sweep_thresholds.py` only ever scores
  `_pos_/_neg_/_neu_` files — for a rule this loose, re-check the unlabelled set too.
  ⚠ **Coupled to `DEDUP_EXCLUDE_TWIN_FROM_COUNTS`**:
  PLAG's 4.46 is twin-inflated (2.232 de-twinned), so flipping that flag must move
  this constant to 2.0 in the same edit. A **Rule-E-on-the-1s-window** variant was
  measured and rejected the same day (+0 pos, +1 neg — SPRC 07-29).
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
- **`x-wing-2.1.py`** — yield-laddered trailing-stop trader (**loaded by clerk-1.1
  only** since 2026-08-12; clerk-1.2 loads 3.0). Standalone, or **driven**. Fires on `on_buy_signal(last_ask,
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

## Offline replay + calibration (added 2026-08-08)
- **`analyze_trade_mole/replay_mole.py`** — replays a recorded mole CSV back through
  a real `IBKRSurgeApp` (its own tick handlers, `_process_trade_event`,
  `_compute_windows`), so recomputed features cannot drift from live. A fake clock
  over the module's `time`/`datetime` handles is advanced to each row's recorded
  stamp; TRADE rows carry `local_mono_time` and it is used verbatim (deriving it as
  `wall + offset` quantises to the ~1.8e9 magnitude's ULP and the ITI divisions
  amplify that). `--self-check` replays unchanged and diffs: **74/74 labelled
  recordings reproduce on features, 46/46 2.2-era recordings reproduce including
  fire decisions**. Older recordings legitimately disagree on `surge_detected`/
  `surge_reason` — they were written by earlier rule generations — so the check
  runs with detection off by default (`--check-decisions` to include them).
  `--set NAME=VALUE` overrides any module constant, which is what makes the
  threshold sweeps end-to-end.
  ⚠ **2026-08-14 — it used to skip every PRE-TRADE-ONLY recording.** `rt_source`
  was in `REQUIRED_COLS` and the ITI baseline was recovered only from a TRADE row;
  a session that fires on the pre-trade path stops recording BEFORE any trade
  prints, so its narrow 15/16-col table has neither. That silently excluded 26 of
  the 90 labelled files (8 pos / 10 neg) and is why every pre-08-14 precision
  number covers the trade path only. Now `rt_source` is enforced (as
  `TRADE_REQUIRED_COLS`) only when a TRADE row is present, and `session_params`
  falls back to the `PRETRADE_SURGE`/`PRETRADE_VETO` row. `mono_offset` stays 0.0
  there, which is sound: the pre-trade path reads only wall-clock stamps and never
  divides monotonic deltas, so the ULP problem that forces TRADE rows to use their
  recorded `local_mono_time` verbatim does not arise.
- **`analyze_trade_mole/eval_rules.py`** (2026-08-14) — **the corpus-wide scorer;
  use this, not `sweep_thresholds.py`, for anything that spans the whole label
  set.** Three reasons it exists: `sweep_thresholds.py` globs only
  `_pos_/_neg_/_neu_` (dropping the operator's `_neutral_`/`_medium_` files), it
  inherited replay_mole's TRADE-row requirement so it **never scored a single
  pre-trade recording** (26 of 90 files — see trade-mole-2.3 above), and it prints
  three independent tallies where the operator's objective is one weighted
  trade-off (`neg + 0.4 × neu`; `_neu_` is a SOFT negative). Rules are switched
  off by neutralising their thresholds so the REAL OR always runs — except Rule A,
  whose threshold is a def-time default argument that a module-constant override
  cannot reach, so it is patched onto the instance. `--leave-one-out` prints each
  rule's marginal pos/cost, which is what exposed that Rule A carries zero unique
  positives and Rule C is free.
- **`analyze_trade_mole/sweep_thresholds.py`** — sweeps trigger constants through
  the REAL `should_fire()`. Prefer it over `analyze_trigger_rules.py` and the v3
  internal simulator: both re-implement the OR and have drifted (they still cut
  Rule C at 100 where 2.2 cuts 1550). ⚠ **Blind to `_neutral_`/`_medium_` and to
  every pre-trade-only recording** — use `eval_rules.py` instead.
- **`analyze_trade_mole/analyze_feed_lag.py`** — exchange-stamp → local-arrival lag.
  Now buckets the within-file spread by observation length, because the
  2026-08-08 edition's "per-session constant, 1 ms spread" was a **censoring
  artifact** (recordings stop at the fire ⇒ most files hold 2–4 fills and cannot
  sample dispersion). Bucketed, the spread is monotonic 0.0008 s → **0.325 s
  median over the 24 files with ≥20 distinct fills**; per-delivery variation of
  several hundred ms is real, a fixed per-session anchor is out. Not the twin —
  deduping twins changes nothing.

## reqTickByTickData probe (2026-08-11) — asked, measured, REJECTED
- **`analyze_trade_mole/probe_tickbytick.py`** — subscribes `reqMktData` AND
  `reqTickByTickData("AllLast")` to the same contract on ONE client (one clock),
  `--analyze` reports per-feed lag / fill counts / aggregate anatomy. Read-only,
  paper GW. Report: `mole-outputs/tickbytick-probe-2026-08-11.md`.
- **Do not migrate to tick-by-tick.** Measured pre-market on the mole's own
  universe it is **0.16–0.43 s SLOWER** (AUUD/JWEL/SCKT/YMT) and only 0.08–0.18 s
  faster on AAPL. Its stamp is epoch **seconds** (vs RTVolume's ms), and the
  concurrency cap is **5 per connection, not 3** (error 10190 — the "3-request
  cap" notes in `x-wing-2.0.py:33` / `x-wing-2.1.py:41` are stale).
- ⚠ **The probe's real finding: RTVolume volume is denominated in LOTS.**
  `rt_total_volume` == reqMktData tick 8 exactly (28143 vs 28139, ratio 1.000),
  and message `size` shares that unit (sum == cumulative delta, ratio 1.00 on 5/5
  symbols). Corpus confirms: of 4017 TRADE rows **93.2% of sizes are <100 and
  only 0.7% are multiples of 100**, modes 1/2/5/10/20/50. So `_cum_volume` and
  `dollar_rate_*` are **~100x understated** (Rule A's `$60` is really ~$6000/s,
  Rule E's `$10` ~$1000/s), and a fill **under 100 shares truncates to 0 lots**
  and is dropped by the `if rt_size > 0` guard (~64% of prints, but only ~8% of
  volume — which is why A/E were never exposed and B/C/D carried all of it).
- ⚠⚠ **FIX NEITHER.** The 100x is cosmetic (every threshold was fit on these same
  columns; rescaling forces A and E to be re-derived and buys nothing). **The
  odd-lot drop is LOAD-BEARING**: on 4 non-surging small caps with no news, the
  unfiltered tick-by-tick tape runs **24–62% of intervals under Rule D's 0.28 s
  bar** and hits a **5 s count of 34 against a threshold of 3** (SCKT: median
  arrival-ITI 0.000 s). The truncation is a noise filter stripping small,
  numerous, uninformative prints, and Rule D was calibrated against that filtered
  tape — the full tape does not surface surges earlier, it makes background
  trading look like one. Compounded because `measured_ITI` uses the LOCAL ARRIVAL
  clock and the unfiltered feed delivers in bursts. A 36-line comment at
  `trade-mole-2.2.py:1554` records this because the drop *looks* like a bug.
  Also strengthens holding the pending `SURGE_ITI_COLLAPSE_MIN_TRADES_5S` 3→2.
  Live code: comments only, AST-verified behaviour-neutral.
- **Two recorded-feature defects found, fixed behind flags that DEFAULT OFF**
  (`trade-mole-2.2.py`): `DEDUP_EXCLUDE_TWIN_FROM_COUNTS` (the RTVolume/
  RTTradeVolume twin was deduped for the ITI series only, so every count/volume/
  rate column double-counted each fill — AZI read `cum_volume` 208 vs a true 141)
  and `RATE_NORMALIZE_BY_SESSION_AGE` (window rates divided by the nominal `w`, so
  every flow gate was diluted during the first `w` seconds — the whole life of a PR
  surge). **Flipping either without re-deriving A/D/E moves the operating point**;
  with the fixes on and thresholds unchanged, pos recall drops 16/16 → 14/16.
  Recommended point (16/16 pos, 12/20 neg) is both flags on plus
  `SURGE_ITI_COLLAPSE_MIN_TRADES_5S` 3→2 — the count threshold has to halve because
  with the twin counted, "3" already meant 2 real prints.
  Report: `mole-outputs/surge-predictor-analysis-2026-08-08.md`.
- A **"Rule G" for moderate relative acceleration was tested and REJECTED** the same
  day — with a directional AND it is a near-subset of Rule E (first-to-fire in 1
  file of 44, changes nothing); without one, every threshold 3x–40x fires 20/20 neg.
  An ADDED OR branch can only increase negative fires, so at 16/16 recall it cannot
  help by construction. See the constants block in `trade-mole-2.2.py`.

## Notes
- Run with the venv python that has ibapi: `path/to/venv/bin/python`
  (system `python3` lacks ibapi; it only works for `py_compile` syntax checks).
- Lifetimes parse as `mm:ss` (e.g. `00:15` = 15s, `01:00` = 60s).
- Port 4001 is the LIVE Gateway; 4002 paper GW / 7497 paper TWS.
- Rare marketable BUY LMT entries can come back IBKR-"Inactive" with no error reason
  (x-wing then aborts the run); see `rejected-orders-findings.txt` for occurrences, the
  shared fingerprint, and candidate causes. x-wing-2.1 attaches the Price Management Algo
  to the entry (`usePriceMgmtAlgo=True`) so IBKR caps-and-works such an over-aggressive limit
  instead of rejecting it. ⚠ **It does NOT work for this**: NCRA 2026-07-29 was deactivated
  with the algo attached, and 2161 caps predate 2.1 (07-20, 07-22) — the algo addresses
  neither Part A nor Part B. **x-wing-3.0 sets it False**, because its only real effect is
  that IBKR re-prices the order underneath us, which a tracking limit cannot tolerate.
  Nothing yet prevents Part B; its rate is climbing (0.00% Jun → 0.49% Jul → 1.37% Aug).
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
