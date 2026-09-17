#!/usr/bin/env python3
"""
T47-airspeeder-2.0.py — x-wing RE-ENTRY controller, the third member of the
clerk-1.2 TRIO (trade-mole + T47 + up to TWO x-wing runs) that shares ONE
ibapi client.

Role
----
Run 1 is today's trigger unchanged: trade-mole surge (+ sentiment gate) →
x-wing entry; T47 merely books it. When that run exits, T47 keeps watching the
BID and grants exactly ONE re-entry (EP2), at the price run 1 exited at (SP1):

    exit fill (SP1)  →  level := SP1   (FROZEN — never re-anchors)
    while flat       →  LP := min(bid seen since the freeze)
    DIP(%) = (level − LP) / level × 100  >= EP2_MIN_DIP_PCT → Valid-DIP latches
    bid >= level (once Valid-DIP)        → RELAUNCH one fresh x-wing
                       (on_buy_signal(ask, ask) — Rule-F shape)

That is the whole rule: dip away from SP1, then come back to it. There is no
high-water ratchet and no PUSH threshold (see T47-airspeeder-1.0.py for the
4-run DROP/PUSH ladder this replaces).

An aborted run (entry never filled: entry_inactive / entry_timeout / reject)
still gets its one re-entry — run 1 included — but there is no SP1 to aim at,
so the level falls back to the trigger price (the surge print, then the ask).

The watch is capped by T47_WATCH_LIFETIME (anchored at collection start) and
hard-stops at T47_SESSION_END_ET. Both act only while FLAT: a live x-wing run
always finishes on its own terms (its protective stop stays in charge).

Driven-only: this module never imports ibapi. clerk-1.2 loads it via
importlib, fans bid/ask ticks and order-status echoes into it, and provides a
session façade (build_new_xwing / start_run_thread / finish / client_dead and
the swappable `session.xwing` callback-routing slot).

Output: one TSV per session in T47-tables/ (SYMBOL-yyyy-mm-dd-hh-t47v2.tsv),
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
XWING_REENTRY_NUMBER  = 1        # re-entries AFTER run 1 (so 2 runs max)
EP2_MIN_DIP_PCT       = 10.0     # the bid must dip at least this far below the
                                 # level before a return to it may re-enter
                                 # (0 = no dip required: any re-cross fires)
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
T47_COLUMNS = ["Time", "CP-Bid", "Ask", "EP", "SP", "HP", "LP", "DIP(%)",
               "GAP(%)", "Valid-DIP", "Run", "State", "Event"]


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
        self._ever_opened = False
        self._closed = False
        self.path = None

    @property
    def opened(self) -> bool:
        """True once a file has been created (stays True after close, so
        shutdown can tell 'never wrote anything' from 'wrote and closed')."""
        return self._ever_opened

    def _ensure_open(self):
        """Resolve the path and open on first use. Caller holds _lock."""
        if self._fh is not None:
            return
        now = datetime.now()
        self.path = self.table_dir / f"{self.symbol}-{now:%Y-%m-%d-%H}-t47v2.tsv"
        self.table_dir.mkdir(parents=True, exist_ok=True)
        new = not self.path.exists()
        self._fh = open(self.path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=T47_COLUMNS,
                                 delimiter="\t", extrasaction="ignore")
        self._ever_opened = True
        if new:
            self._w.writeheader()
            self._fh.flush()
        self._log.info("[T47] table: %s", self.path.resolve())

    def write(self, row: dict):
        with self._lock:
            if self._closed:
                return               # post-shutdown straggler: drop it
            self._ensure_open()
            self._w.writerow(row)
            self._fh.flush()

    def close(self):
        with self._lock:
            self._closed = True
            if self._fh is None:
                return
            try:
                self._fh.close()
            except Exception:
                pass
            self._fh = None
            self._w = None


# --------------------------------------------------------------------------- #
# Controller
# --------------------------------------------------------------------------- #
# session states
ST_IDLE        = "IDLE"          # mole watch phase; caching quotes only
ST_RUN_PENDING = "RUN_PENDING"   # an x-wing run is launched, entry not filled
ST_IN_POSITION = "IN_POSITION"   # entry filled; the run manages the position
ST_WATCH       = "WATCH"         # flat; DIP/re-cross re-entry tracking active
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
        self._runs_max = 1 + XWING_REENTRY_NUMBER
        self._ctl = None                # current run's XWingController

        # quotes (cached from the fan-out even while IDLE)
        self._bid = None
        self._ask = None

        # re-entry tracking (only live while ST_WATCH)
        self.level = None               # FROZEN re-entry price (SP1)
        self.lp = None                  # lowest bid seen since the freeze
        self._valid_dip = False

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
        # the trio must never re-enter on top of (late-fill guard).
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
        ST_WATCH drives the DIP/re-cross test + the deadline checks."""
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
                self.writer.write(self._row())   # frozen level, no tracking
                return

            # ---- ST_WATCH ----
            now = self._now()
            finish_reason, event = self._deadline_event_locked(now)
            if finish_reason:
                self._state = ST_DONE
                self.writer.write(self._row(event))
            else:
                event, over = "", {}
                if self.level is None:
                    # aborted run with no known price at all: the first bid of
                    # the watch becomes the level
                    self.level = price
                    event = "LEVEL_SET"
                if self.lp is None or price < self.lp:
                    self.lp = price          # deepening low re-anchors LP

                dip = (self.level - self.lp) / self.level * 100.0
                if not self._valid_dip and dip >= EP2_MIN_DIP_PCT:
                    self._valid_dip = True
                    event = "VALID_DIP"

                if (self._valid_dip and price >= self.level
                        and self._pending_finish is None
                        and self._run_no < self._runs_max):
                    # latch BEFORE anything else so the next bid tick cannot
                    # fire a second re-entry
                    self._state = ST_RUN_PENDING
                    event = "RELAUNCH"
                    over = {"Valid-DIP": "YES"}
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
                # unmanaged live position — never stack a re-entry on it.
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
        finalized its own table). Decide: end the trio or open the watch."""
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
                # Arm the late-fill guard on EVERY entry orderId this run used,
                # not just the last one. x-wing-3.1 re-places a wall-rejected
                # (IBKR 202) entry on a FRESH orderId and retires the dead one
                # into ctl._retired_entry_ids, so keying only on active_buy_id
                # would let a late fill on a retired id slip past the guard
                # entirely. getattr-guarded: x-wing-2.1/3.0 have no such set and
                # this collapses to the original single-id behaviour there.
                ids = {getattr(ctl, "active_buy_id", None)}
                ids |= set(getattr(ctl, "_retired_entry_ids", ()) or ())
                for oid in ids:
                    if oid is not None:  # aborted entry: arm the late-fill guard
                        self._prior_entry_ids[oid] = self._run_no

            if pos > 0:
                # flatten incomplete: shares (and possibly a resting SELL) are
                # still live at the broker — never re-enter on top of that.
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
                # open the watch: freeze the re-entry level
                if entry_filled:
                    level = self.sp
                    if level is None and ctl is not None:
                        # exit fill echo not seen (shouldn't happen): fall back
                        # to the protective order's recorded avg, then the bid
                        prot = ctl.orders.get(ctl.protective_id) or {}
                        level = prot.get("avg") or self._bid
                    event = ""
                else:
                    # no SP1 exists: aim at the price that triggered the run
                    level = (getattr(ctl, "trigger_print", None)
                             or getattr(ctl, "ask", None)
                             or self._bid) if ctl is not None else self._bid
                    event = "ABORT"
                self.level = level       # None-tolerant: first WATCH bid seeds it
                self.lp = None
                self._valid_dip = False
                self._state = ST_WATCH
                self.log.info("[T47] run %d done (%s, filled=%s) -> WATCH, "
                              "EP2 level=%s (dip %.1f%% then re-cross)",
                              self._run_no, reason, entry_filled, _f(level),
                              EP2_MIN_DIP_PCT)
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
        """Launch thread for the EP2 run (mirrors the clerk's sentinel-thread
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
            # the watch is over: retire the level so the rows of this run show
            # its own fills, not the level it was launched off
            self.level = None
            self.lp = None
            self._valid_dip = False
            bid, ask = self._bid, self._ask
            self.writer.write(self._row("RUN_LAUNCH"))
        ref = ask if (ask is not None and ask > 0) else bid
        ctl.update_quote(bid=bid, ask=ref)   # seed so the run's first loop row is live
        self.log.info("[T47] run %d/%d re-entry: on_buy_signal(ask=%s, ask) "
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
        Extra kwargs override columns (e.g. the RELAUNCH row's Valid-DIP, set
        before the state latch flipped WATCH -> RUN_PENDING)."""
        # SP shows the FROZEN re-entry level while the watch is open, and the
        # latest exit fill otherwise (so the EXIT_FILL row already carries SP1).
        sp = self.level if self.level is not None else self.sp
        dip = gap = None
        if sp:
            if self.lp:
                dip = (sp - self.lp) / sp * 100.0
            if self._bid:
                gap = (self._bid - sp) / sp * 100.0
        hp = None
        if self._ctl is not None:
            try:                          # x-wing's in-position high-water bid
                hp = self._ctl.high_water
            except Exception:
                hp = None
        row = {
            "Time": datetime.now().isoformat(timespec="milliseconds"),
            "CP-Bid": _f(self._bid),
            "Ask": _f(self._ask),
            "EP": _f(self.ep),
            "SP": _f(sp),
            "HP": _f(hp),
            "LP": _f(self.lp),
            "DIP(%)": _f(dip, 2),
            "GAP(%)": _f(gap, 2),
            "Valid-DIP": "YES" if self._valid_dip else "NO",
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
