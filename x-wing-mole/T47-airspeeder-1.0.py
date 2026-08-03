#!/usr/bin/env python3
"""
T47-airspeeder-1.0.py — x-wing RELAUNCH controller, the third member of the
clerk-1.2 TRIO (trade-mole + T47 + a sequence of x-wing runs) that shares ONE
ibapi client.

Role
----
Run 1 is today's trigger unchanged: trade-mole surge (+ sentiment gate) →
x-wing entry; T47 merely books it. From then on T47 owns the session: after
each x-wing run ends it keeps watching the BID and relaunches a FRESH x-wing
(up to XWING_RELAUNCH_NUMBER more times) on a "drop then bounce" pattern:

    exit fill (SP)  →  HP := SP, LP := SP        (in-position peaks IGNORED)
    while flat      →  bid > HP:  HP := bid, LP := HP  (Valid-DROP cleared)
                       bid < LP:  LP := bid            (low re-anchors)
    DROP(%) = (HP − LP) / HP × 100   > DROP_THRESHOLD_PCT → Valid-DROP latches
    PUSH(%) = (bid − LP) / LP × 100  > PUSH_THRESHOLD_PCT → RELAUNCH
                       (fresh x-wing, on_buy_signal(ask, ask) — Rule-F shape)

An aborted run (entry never filled: entry_inactive / entry_timeout / reject)
consumes a run and re-anchors HP = LP = current bid — run 1 included (a failed
first entry starts the watch instead of ending the session).

The watch is capped by T47_WATCH_LIFETIME (anchored at collection start) and
hard-stops at T47_SESSION_END_ET. Both act only while FLAT: a live x-wing run
always finishes on its own terms first (its protective stop stays in charge).

Driven-only: this module never imports ibapi. clerk-1.2 loads it via
importlib, fans bid/ask ticks and order-status echoes into it, and provides a
session façade (build_new_xwing / start_run_thread / finish / client_dead and
the swappable `session.xwing` callback-routing slot).

Output: one TSV per session in T47-tables/ (SYMBOL-yyyy-mm-dd-hh-t47.tsv),
one row per bid tick plus one per event. The file (name AND handle) is
resolved lazily on the first write, so a session that never launches run 1
leaves no file and a deferred (04:00 ET) session stamps its real launch hour.
"""

import csv
import logging
import threading
import time
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
logger = logging.getLogger("t47")

# --------------------------------------------------------------------------- #
# Tunables — edit here
# --------------------------------------------------------------------------- #
XWING_RELAUNCH_NUMBER = 3        # x-wing relaunches AFTER run 1 (so 4 runs max)
DROP_THRESHOLD_PCT    = 20.0     # DROP > this -> Valid-DROP latches
PUSH_THRESHOLD_PCT    = 7.0     # PUSH > this (while Valid-DROP) -> relaunch
LP_DWELL_SEC          = 0        # 0 = off; else the LP must be at least this
                                 # many seconds old (no newer low) before a
                                 # push may fire (dead-cat-bounce filter)
T47_WATCH_LIFETIME    = "02:00:00"  # hh:mm:ss (or mm:ss) from collection
                                    # start; only acts while flat
T47_SESSION_END_ET    = "20:00"  # ET hard stop (extended-hours close); flat only
HEARTBEAT_SEC         = 30.0     # deadline/dead-client fallback poll — a quiet
                                 # tape produces no bid ticks to check on
T47_TABLE_DIR         = str(Path(__file__).resolve().parent / "T47-tables")

# ibapi tickPrice type codes (local copies; this module stays ibapi-free)
TICK_BID, TICK_ASK       = 1, 2
DELAYED_BID, DELAYED_ASK = 66, 67
_UNSET_SENTINEL = 1e300          # ibapi UNSET_DOUBLE guard (1.797e308)


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def parse_lifetime(s) -> int:
    """'hh:mm:ss' or 'mm:ss' -> seconds."""
    parts = [int(p) for p in str(s).strip().split(":")]
    if len(parts) == 2:
        m, sec = parts
        return m * 60 + sec
    if len(parts) == 3:
        h, m, sec = parts
        return h * 3600 + m * 60 + sec
    raise ValueError(f"bad lifetime {s!r} (want hh:mm:ss or mm:ss)")


def parse_hhmm(s) -> dtime:
    """'20:00' -> datetime.time(20, 0)."""
    h, m = str(s).strip().split(":")
    return dtime(int(h), int(m))


def _f(v, nd=4):
    """Blank-for-None float formatting (same convention as x-wing's _f)."""
    if v is None:
        return ""
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return str(v)


# --------------------------------------------------------------------------- #
# TSV writer
# --------------------------------------------------------------------------- #
T47_COLUMNS = ["Time", "CP-Bid", "Ask", "EP", "SP", "LP", "HP", "DROP(%)",
               "PUSH(%)", "Valid-DROP", "Valid-PUSH", "Run", "State", "Event"]


class T47Writer:
    """Per-session TSV writer (mirrors x-wing's PriceActionWriter, but BOTH the
    filename and the open are deferred to the first write — see module doc)."""

    def __init__(self, symbol, table_dir=None, log=None):
        self.symbol = symbol
        self.table_dir = Path(table_dir or T47_TABLE_DIR)
        self._log = log or logger
        self._lock = threading.Lock()
        self._fh = None
        self._w = None
        self.path = None

    @property
    def opened(self) -> bool:
        return self._fh is not None

    def _ensure_open(self):
        """Resolve the path and open on first use. Caller holds _lock."""
        if self._fh is not None:
            return
        now = datetime.now()
        self.path = self.table_dir / f"{self.symbol}-{now:%Y-%m-%d-%H}-t47.tsv"
        self.table_dir.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=T47_COLUMNS,
                                 delimiter="\t", extrasaction="ignore")
        if new:
            self._w.writeheader()
            self._fh.flush()
        self._log.info("[T47] table: %s", self.path.resolve())

    def write(self, row: dict):
        with self._lock:
            self._ensure_open()
            self._w.writerow(row)
            self._fh.flush()

    def close(self):
        with self._lock:
            if self._fh is None:
                return
            try:
                self._fh.close()
            except Exception:
                pass


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
# session states
ST_IDLE        = "IDLE"          # mole watch phase; caching quotes only
ST_RUN_PENDING = "RUN_PENDING"   # an x-wing run is launched, entry not filled
ST_IN_POSITION = "IN_POSITION"   # entry filled; the run manages the position
ST_WATCH       = "WATCH"         # flat; DROP/PUSH re-entry tracking active
ST_DONE        = "DONE"


class T47Controller:
    """Re-entry state machine. All mutable state lives under one RLock; the
    ibapi callback thread must never block in here (tick work = compute + one
    flushed TSV row, matching what x-wing already does on that thread).

    LOCK RULE: decide and mark ST_DONE under the lock, but call
    session.finish() only AFTER releasing it — finish() re-enters this object
    via shutdown()."""

    def __init__(self, symbol, session, log=None, now=time.time):
        self.symbol = symbol.upper()
        self.session = session          # TrioSession façade (duck-typed)
        self.log = log or logger
        self._now = now                 # injectable wall clock (offline tests)

        self._lock = threading.RLock()
        self._state = ST_IDLE
        self._run_no = 0
        self._runs_max = 1 + XWING_RELAUNCH_NUMBER
        self._ctl = None                # current run's XWingController

        # quotes (cached from the fan-out even while IDLE)
        self._bid = None
        self._ask = None

        # drop/push tracking (only ratchets while ST_WATCH)
        self.hp = None
        self.lp = None
        self._lp_ts = None              # wall time of the last LP (re)anchor
        self._valid_drop = False

        # latest-known fill prices (displayed on every row)
        self.ep = None                  # Entry Price (latest entry avg fill)
        self.sp = None                  # Stop Price (latest exit avg fill)
        self._entry_fill_seen = False   # per-run ENTRY_FILL row guard
        self._exit_fill_seen = False    # per-run EXIT_FILL row guard (dup statuses)

        self._deadline_ts = None        # anchor + T47_WATCH_LIFETIME
        self._session_end = parse_hhmm(T47_SESSION_END_ET)
        self._pending_finish = None     # finish reason deferred to run end
        # entry orderIds of ABORTED runs: entry_timeout's cancel can go
        # unconfirmed, so the resting BUY may fill later -> unmanaged position
        # the trio must never relaunch on top of (late-fill guard).
        self._prior_entry_ids = {}      # orderId -> run_no

        self._hb_stop = threading.Event()
        self._hb_thread = None
        self._shutdown_done = False
        self.writer = T47Writer(self.symbol, log=self.log)

    # ------------------------------------------------------------------ #
    # clerk-facing API
    # ------------------------------------------------------------------ #
    def set_anchor(self):
        """Collection start (clerk _begin_collection): the watch-lifetime clock
        starts NOW — for deferred overnight sessions that is 04:00 ET, not the
        trigger's arrival."""
        with self._lock:
            self._deadline_ts = self._now() + parse_lifetime(T47_WATCH_LIFETIME)

    def launch_run1(self, last_ask, trigger_print):
        """Sentinel-thread handoff of the mole+sentiment trigger. Run 1 reuses
        the session's pre-built controller and today's pricing args untouched;
        T47 only books it and starts its heartbeat."""
        with self._lock:
            if self._state != ST_IDLE:
                return
            self._run_no = 1
            self._ctl = self.session.xwing
            self._entry_fill_seen = False
            self._exit_fill_seen = False
            self._state = ST_RUN_PENDING
            self.writer.write(self._row("RUN_LAUNCH"))
        self._hb_thread = threading.Thread(target=self._heartbeat_loop,
                                           daemon=True,
                                           name=f"t47-hb-{self.symbol}")
        self._hb_thread.start()
        self.log.info("[T47] run 1/%d launching (mole trigger, last_ask=%s "
                      "print=%s)", self._runs_max, last_ask, trigger_print)
        self._ctl.on_buy_signal(last_ask, trigger_print)
        self.session.start_run_thread(self._ctl, 1)

    def on_tick_price(self, tickType, price):
        """SharedIBClient fan-out (ibapi thread). Ask ticks only refresh the
        cache; every bid tick becomes a TSV row once run 1 has launched, and in
        ST_WATCH drives the DROP/PUSH ratchet + deadline checks."""
        if price is None or price <= 0 or price >= _UNSET_SENTINEL:
            return
        if tickType in (TICK_ASK, DELAYED_ASK):
            with self._lock:
                self._ask = price
            return
        if tickType not in (TICK_BID, DELAYED_BID):
            return

        finish_reason = None
        do_relaunch = False
        with self._lock:
            self._bid = price
            st = self._state
            if st in (ST_IDLE, ST_DONE):
                return                       # cache only; no rows yet/anymore
            if st in (ST_RUN_PENDING, ST_IN_POSITION):
                self.writer.write(self._row())   # frozen HP/LP, no ratchet
                return

            # ---- ST_WATCH ----
            now = self._now()
            finish_reason, event = self._deadline_event_locked(now)
            if finish_reason:
                self._state = ST_DONE
                self.writer.write(self._row(event))
            else:
                event, over = "", {}
                if self.hp is None:
                    # abort re-anchor happened before any bid was seen: seed
                    self.hp = self.lp = price
                    self._lp_ts = now
                elif price > self.hp:
                    # new high while flat: drop tracking restarts from it
                    if self._valid_drop:
                        event = "HP_RESET"
                    self.hp = self.lp = price
                    self._lp_ts = now
                    self._valid_drop = False
                elif price < self.lp:
                    self.lp = price          # deepening low re-anchors LP
                    self._lp_ts = now

                drop = (self.hp - self.lp) / self.hp * 100.0
                if not self._valid_drop and drop > DROP_THRESHOLD_PCT:
                    self._valid_drop = True
                    event = "VALID_DROP"

                if self._valid_drop:
                    push = (price - self.lp) / self.lp * 100.0
                    if (push > PUSH_THRESHOLD_PCT
                            and self._pending_finish is None
                            and self._run_no < self._runs_max
                            and (LP_DWELL_SEC <= 0
                                 or (now - self._lp_ts) >= LP_DWELL_SEC)):
                        # latch BEFORE anything else so the next bid tick
                        # cannot fire a second relaunch
                        self._state = ST_RUN_PENDING
                        event = "RELAUNCH"
                        over = {"Valid-PUSH": "YES", "PUSH(%)": _f(push, 2)}
                        do_relaunch = True
                self.writer.write(self._row(event, **over))

        if finish_reason:
            self.session.finish(finish_reason)
        elif do_relaunch:
            threading.Thread(target=self._do_relaunch, daemon=True,
                             name=f"t47-launch-{self.symbol}").start()

    def observe_order_status(self, orderId, status, filled, avg, last_fill):
        """SharedIBClient echo (ibapi thread), invoked AFTER the current x-wing
        processed the same callback — same thread as the mutator, so reading
        ctl.orders here is race-free. Stamps ENTRY_FILL / EXIT_FILL rows,
        captures EP / SP, and enforces the late-fill guard."""
        finish_reason = None
        with self._lock:
            if self._state == ST_DONE:
                return
            if orderId in self._prior_entry_ids and filled and filled > 0:
                # a supposedly-dead entry from an aborted run just filled: an
                # unmanaged live position — never stack a relaunch on it.
                self.log.warning("[T47] LATE FILL on run-%d entry %d (%.0f sh)"
                                 " — unmanaged position",
                                 self._prior_entry_ids[orderId], orderId, filled)
                if self._state == ST_WATCH:
                    self._state = ST_DONE
                    self.writer.write(self._row("LATE_FILL"))
                    finish_reason = "t47:late_entry_fill"
                elif self._pending_finish is None:
                    self._pending_finish = "t47:late_entry_fill"
                    self.writer.write(self._row("LATE_FILL"))
            else:
                ctl = self._ctl
                rec = ctl.orders.get(orderId) if ctl is not None else None
                if rec is not None:
                    role = rec.get("role")
                    if role == "entry" and filled and filled > 0:
                        self.ep = (ctl.original_avg_fill
                                   or ctl.running_entry_avg or avg or self.ep)
                        if not self._entry_fill_seen:
                            self._entry_fill_seen = True
                            if self._state == ST_RUN_PENDING:
                                self._state = ST_IN_POSITION
                            self.writer.write(self._row("ENTRY_FILL"))
                    elif (role == "protective" and status == "Filled"
                          and not self._exit_fill_seen):
                        self._exit_fill_seen = True
                        self.sp = avg or last_fill or self._bid
                        self.writer.write(self._row("EXIT_FILL"))
        if finish_reason:
            self.session.finish(finish_reason)

    def on_run_done(self, reason):
        """run_trade completion (its own thread; the run has flattened and
        finalized its own table). Decide: end the trio or resume the watch."""
        finish_reason = None
        with self._lock:
            if self._state == ST_DONE:
                return
            ctl = self._ctl
            pos = int(round(ctl.position)) if ctl is not None else 0
            entry_filled = ctl is not None and (
                ctl.original_avg_fill is not None
                or ctl.running_entry_avg is not None)
            if ctl is not None and not entry_filled:
                oid = getattr(ctl, "active_buy_id", None)
                if oid is not None:      # aborted entry: arm the late-fill guard
                    self._prior_entry_ids[oid] = self._run_no

            if pos > 0:
                # flatten incomplete: shares (and possibly a resting SELL) are
                # still live at the broker — never relaunch on top of that.
                self.log.warning("[T47] run %d ended (%s) with %d sh residual "
                                 "— ending trio", self._run_no, reason, pos)
                self._state = ST_DONE
                finish_reason = "t47:residual_position"
            elif self._pending_finish is not None:
                self._state = ST_DONE
                finish_reason = self._pending_finish
            elif self._run_no >= self._runs_max:
                self._state = ST_DONE
                finish_reason = f"t47:budget_exhausted:{reason}"
            else:
                # resume the watch
                if entry_filled:
                    anchor = self.sp
                    if anchor is None and ctl is not None:
                        # exit fill echo not seen (shouldn't happen): fall back
                        # to the protective order's recorded avg, then the bid
                        prot = ctl.orders.get(ctl.protective_id) or {}
                        anchor = prot.get("avg") or self._bid
                    event = ""
                else:
                    anchor = self._bid   # abort: re-anchor at the market
                    event = "ABORT"
                self.hp = self.lp = anchor   # None-tolerant: first WATCH bid seeds
                self._lp_ts = self._now()
                self._valid_drop = False
                self._state = ST_WATCH
                self.log.info("[T47] run %d done (%s, filled=%s) -> WATCH, "
                              "anchor HP=LP=%s (%d run(s) left)", self._run_no,
                              reason, entry_filled, _f(anchor),
                              self._runs_max - self._run_no)
                self.writer.write(self._row(event))
        if finish_reason:
            self.log.info("[T47] trio ending: %s", finish_reason)
            self.session.finish(finish_reason)

    def shutdown(self, reason):
        """TrioSession.finish() teardown hook. Idempotent; never creates a
        file for a session that never launched run 1."""
        with self._lock:
            if self._shutdown_done:
                return
            self._shutdown_done = True
            self._hb_stop.set()
            self._state = ST_DONE
            if self.writer.opened:
                self.writer.write(self._row(f"DONE:{reason}"))
        self.writer.close()

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _deadline_event_locked(self, now):
        """(finish_reason, event) if a flat-state deadline has passed, else
        (None, None). Caller holds the lock."""
        if self._deadline_ts is not None and now >= self._deadline_ts:
            return "t47:lifetime", "LIFETIME_END"
        if datetime.now(tz=ET).time() >= self._session_end:
            return "t47:session_end", "SESSION_END"
        return None, None

    def _do_relaunch(self):
        """Launch thread for runs 2..N (mirrors the clerk's sentinel-thread
        pattern; build_driven re-reads the limits TSV, so never on the ibapi
        thread). ORDER MATTERS: the session's routing slot (session.xwing)
        must point at the NEW controller BEFORE its entry order exists, else
        the first orderStatus (ms after placeOrder) would route to the dead
        one and the fill would be dropped."""
        try:
            ctl = self.session.build_new_xwing()
        except Exception as e:
            self.log.error("[T47] build_new_xwing failed: %s", e, exc_info=True)
            with self._lock:
                self._state = ST_DONE
            self.session.finish("t47:launch_failed")
            return
        with self._lock:
            self._run_no += 1
            run_no = self._run_no
            self._ctl = ctl
            self._entry_fill_seen = False
            self._exit_fill_seen = False
            self.session.xwing = ctl         # swap the routing slot FIRST
            bid, ask = self._bid, self._ask
            self.writer.write(self._row("RUN_LAUNCH"))
        ref = ask if (ask is not None and ask > 0) else bid
        ctl.update_quote(bid=bid, ask=ref)   # seed so the run's first loop row is live
        self.log.info("[T47] run %d/%d relaunch: on_buy_signal(ask=%s, ask) "
                      "[bid=%s]", run_no, self._runs_max, _f(ref), _f(bid))
        ctl.on_buy_signal(ref, ref)          # Rule-F shape: live ask, both args
        self.session.start_run_thread(ctl, run_no)

    def _heartbeat_loop(self):
        """Fallback poll: a quiet tape produces no bid ticks, so deadlines and
        a dead client cannot be detected tick-driven alone. While a run is
        live it only marks _pending_finish — the run always finishes on its
        own terms (never touch ctl.stop_evt: that would force-flatten)."""
        while not self._hb_stop.wait(HEARTBEAT_SEC):
            try:
                dead = self.session.client_dead()
            except Exception:
                dead = False
            finish_reason = None
            with self._lock:
                st = self._state
                if st == ST_DONE:
                    return
                if st == ST_WATCH:
                    finish_reason, event = self._deadline_event_locked(self._now())
                    if finish_reason is None and dead:
                        finish_reason, event = "t47:client_dead", "CLIENT_DEAD"
                    if finish_reason:
                        self._state = ST_DONE
                        self.writer.write(self._row(event))
                elif st in (ST_RUN_PENDING, ST_IN_POSITION):
                    if self._pending_finish is None:
                        reason, _ = self._deadline_event_locked(self._now())
                        if reason is None and dead:
                            reason = "t47:client_dead"
                        if reason is not None:
                            self._pending_finish = reason
                            self.log.info("[T47] %s while run %d live -> trio "
                                          "ends after this run", reason,
                                          self._run_no)
            if finish_reason:
                self.session.finish(finish_reason)

    def _row(self, event="", **over):
        """Build one TSV row from current state. Caller holds the lock.
        Extra kwargs override columns (e.g. the RELAUNCH row's PUSH value,
        computed before the state latch flipped WATCH -> RUN_PENDING)."""
        drop = push = None
        if self.hp and self.lp:
            drop = (self.hp - self.lp) / self.hp * 100.0
        if (self._valid_drop and self.lp and self._bid
                and self._state == ST_WATCH):
            push = (self._bid - self.lp) / self.lp * 100.0
        row = {
            "Time": datetime.now().isoformat(timespec="milliseconds"),
            "CP-Bid": _f(self._bid),
            "Ask": _f(self._ask),
            "EP": _f(self.ep),
            "SP": _f(self.sp),
            "LP": _f(self.lp),
            "HP": _f(self.hp),
            "DROP(%)": _f(drop, 2),
            "PUSH(%)": _f(push, 2),
            "Valid-DROP": "YES" if self._valid_drop else "NO",
            "Valid-PUSH": "NO",
            "Run": self._run_no if self._run_no > 0 else "",
            "State": self._state,
            "Event": event,
        }
        row.update(over)
        return row


if __name__ == "__main__":
    print(__doc__)
    print("T47 is DRIVEN-ONLY: it has no ibapi connection of its own. "
          "Run it via clerk-1.2.py.")
