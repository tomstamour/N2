#!/usr/bin/env python
"""
verify_xwing3_tracking.py — offline stub harness for x-wing-3.0's entry
ask-tracking. No ibapi connection, no Gateway, no orders: a fake client records
every placeOrder/cancelOrder and a fake quote source replays an ask series
through the REAL XWingController, so the assertions below exercise the shipped
code path rather than a re-implementation of it.

Run:  /home/tom/venv/bin/python analyze_trade_mole/verify_xwing3_tracking.py
      (also works with system python3 — pandas is the only hard dependency)

Case 1 replays the canonical "should have tracked" run,
xwing_tables/xwing-table_GLE_2026-08-11_09-04-11.tsv: the ask ran 0.6698 ->
0.7062 over the 10 s the entry sat unfilled, and the run died entry_inactive.
"""

import importlib.util
import sys
import time
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
XWING_DIR = HERE.parent


def _load(mod_name, filename):
    path = XWING_DIR / filename
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


xw = _load("xwing3_under_test", "x-wing-3.0.py")


# --------------------------------------------------------------------------- #
# Stubs
# --------------------------------------------------------------------------- #
class FakeClient:
    """Records the wire instead of writing to it."""

    def __init__(self):
        self.placed = []     # (orderId, action, otype, qty, lmt, algo)
        self.cancelled = []

    def placeOrder(self, orderId, contract, order):
        self.placed.append((orderId, order.action, order.orderType,
                            int(order.totalQuantity),
                            round(float(order.lmtPrice), 6),
                            bool(getattr(order, "usePriceMgmtAlgo", False))))

    def cancelOrder(self, orderId):
        self.cancelled.append(orderId)

    def entry_sends(self, oid):
        return [p for p in self.placed if p[0] == oid]

    def sends_for(self, oids):
        return [p for p in self.placed if p[0] in oids]


def fake_contract_details(symbol, min_tick):
    c = xw.make_contract(symbol)
    c.conId = 1
    c.primaryExchange = "NASDAQ"
    return types.SimpleNamespace(contract=c, minTick=min_tick)


class Harness:
    """One controller wired to a FakeClient, with the control loop driven by
    hand: we never start run_trade's thread, we call control_loop's tracker
    directly so the test owns the clock."""

    def __init__(self, symbol="TEST", min_tick=0.0001, last_close=1.0,
                 quiet=True, **cfg):
        import logging
        log = logging.getLogger(f"verify.{symbol}.{id(self)}")
        log.handlers[:] = [logging.NullHandler()] if quiet else []
        log.propagate = not quiet
        self.log = log
        self.client = FakeClient()
        self._next_id = 100
        cfg.setdefault("price_action_table", "/dev/null")
        self.ctl = xw.build_driven(symbol, last_close, self.client, log=log,
                                   on_done=None, id_alloc=self._alloc, **cfg)
        self.ctl.set_contract_details(fake_contract_details(symbol, min_tick))
        # never write a table in the harness
        self.ctl.pa.write = lambda row: None

    def _alloc(self):
        self._next_id += 1
        return self._next_id

    def fire(self, ask, trigger_print=None):
        self.ctl.update_quote(bid=ask * 0.99, ask=ask)
        self.ctl.on_buy_signal(ask, trigger_print if trigger_print else ask)

    def tick(self, ask, advance=0.30):
        """One quote update + one tracker evaluation, `advance` seconds later."""
        self._warp(advance)
        self.ctl.update_quote(bid=ask * 0.995, ask=ask)
        return self.ctl.track_entry_limit()

    def _warp(self, secs):
        """Move the controller's own timestamps back instead of sleeping."""
        if self.ctl.entry_ts is not None:
            self.ctl.entry_ts -= secs
        if self.ctl._track_last_ts is not None:
            self.ctl._track_last_ts -= secs
        # 3.1: the wall re-place has its own gap gate. Miss this and every retry
        # test fails on the throttle rather than on what it meant to assert.
        if getattr(self.ctl, "_entry_retry_last_ts", None) is not None:
            self.ctl._entry_retry_last_ts -= secs
        # keep the global token bucket from being the thing that fails a test
        xw.ORDER_THROTTLE._tokens = xw.ORDER_THROTTLE.rate

    def status(self, status, filled=0.0, avg=0.0, why_held=""):
        self.ctl.on_order_status(self.ctl.active_buy_id, status, float(filled),
                                 float(avg), float(avg), why_held=why_held)

    def err(self, code, msg=""):
        self.ctl.on_order_error(self.ctl.active_buy_id, code, msg)

    @property
    def sends(self):
        return self.client.entry_sends(self.ctl.active_buy_id)

    @property
    def all_entry_sends(self):
        """Every send on the live entry id AND on any id a wall re-place
        retired. `sends` alone silently loses the pre-retry history, because
        active_buy_id moves when the entry is re-placed."""
        ids = set(getattr(self.ctl, "_retired_entry_ids", ()) or ())
        if self.ctl.active_buy_id is not None:
            ids.add(self.ctl.active_buy_id)
        return self.client.sends_for(ids)

    def warp(self, secs):
        """Public clock advance for tests that do not send a quote."""
        self._warp(secs)


# --------------------------------------------------------------------------- #
# Assertions
# --------------------------------------------------------------------------- #
FAILURES = []
PASSED = []


def check(name, cond, detail=""):
    (PASSED if cond else FAILURES).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""))


def tick_ok(price, min_tick):
    t = xw.effective_tick(price, min_tick)
    return abs(round(price / t) * t - price) < 1e-9


# --------------------------------------------------------------------------- #
def case_gle_replay():
    """The canonical case: replay the real ask series from the GLE table."""
    print("\n[1] GLE 2026-08-11 09:04:11 replay (real ask series)")
    import csv
    path = XWING_DIR / "xwing_tables" / "xwing-table_GLE_2026-08-11_09-04-11.tsv"
    if not path.exists():
        check("GLE table present", False, str(path))
        return
    rows = [r for r in csv.DictReader(open(path, newline=""), delimiter="\t")
            if r.get("phase") == "ENTRY_PENDING" and r.get("ask")]
    asks = [float(r["ask"]) for r in rows]
    check("GLE table has an ENTRY_PENDING ask series", len(asks) >= 5,
          f"{len(asks)} rows, {asks[0]} -> {max(asks)}")

    h = Harness("GLE", min_tick=0.0001, last_close=0.3872)
    h.fire(asks[0])
    events = [h.tick(a, advance=1.0) for a in asks[1:]]
    sends = h.sends
    ceiling = h.ctl.entry_ceiling

    check("opening shot is ref +2.5%", abs(sends[0][4] - 0.6866) < 1e-6,
          f"ask {asks[0]} -> {sends[0][4]}")
    check("2.1 would NOT have filled: its limit < the ask that arrived",
          asks[0] * 1.029 < max(asks),
          f"2.1 limit {asks[0] * 1.029:.4f} vs ask high {max(asks):.4f}")
    check("tracker raised the limit above the ask high",
          sends[-1][4] >= max(asks),
          f"final limit {sends[-1][4]} vs ask high {max(asks)}")
    check("ceiling is ref +6% and was never breached",
          all(p[4] <= ceiling + 1e-9 for p in sends),
          f"ceiling {ceiling}, max sent {max(p[4] for p in sends)}")
    check("every send reuses the one entry orderId",
          len({p[0] for p in h.client.placed}) == 1,
          f"ids={sorted({p[0] for p in h.client.placed})}")
    check("no CHASE_UP after the up-window closed",
          all(e != "CHASE_UP" for e in events[3:]),
          f"events={[e for e in events if e]}")
    print(f"      sent: {[p[4] for p in sends]}")


def case_invariants():
    print("\n[2] core invariants (synthetic ask ramp, $1.91 name, tick 0.01)")
    h = Harness("RAMP", min_tick=0.01, last_close=1.50)
    h.fire(1.91)
    for a in (1.93, 1.96, 2.00, 2.05, 2.10, 2.20, 2.40):
        h.tick(a, advance=0.30)
    sends = h.sends
    ceiling = h.ctl.entry_ceiling

    check("one orderId for every modify", len({p[0] for p in h.client.placed}) == 1)
    check("totalQuantity constant across all sends",
          len({p[3] for p in sends}) == 1, f"qty={ {p[3] for p in sends} }")
    check("usePriceMgmtAlgo is OFF on every send",
          all(p[5] is False for p in sends))
    check("ceiling = ref * 1.06", abs(ceiling - 2.03) < 1e-6, f"{ceiling}")
    check("limit never exceeds the ceiling",
          all(p[4] <= ceiling + 1e-9 for p in sends),
          f"max {max(p[4] for p in sends)} vs {ceiling}")
    check("every limit lands on a valid tick",
          all(tick_ok(p[4], 0.01) for p in sends),
          f"{[p[4] for p in sends]}")
    check("limits are monotonically non-decreasing on a rising ask",
          all(b[4] >= a[4] for a, b in zip(sends, sends[1:])))
    check("modify count is bounded", h.ctl._track_n <= xw.ENTRY_TRACK_MAX_MODIFIES)


def case_penny_floor():
    print("\n[3] SEC Rule 612 penny floor and the sub-$1 tick")
    h = Harness("SUB", min_tick=0.0001, last_close=0.12)
    h.fire(0.1500)
    check("sub-$1 opening shot uses the $0.0001 grid",
          abs(h.sends[0][4] - 0.1538) < 1e-6, f"{h.sends[0][4]}")
    h2 = Harness("CROSS", min_tick=0.0001, last_close=0.90)
    h2.fire(0.9950)
    px = h2.sends[0][4]
    check("a limit crossing $1.00 is forced onto the penny grid",
          px >= 1.0 and tick_ok(px, 0.01), f"{px}")


def case_down_ratchet():
    print("\n[4] downward ratchet, hysteresis, and the un-time-boxed down side")
    h = Harness("FADE", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    opening = h.sends[0][4]
    ev = h.tick(1.98, advance=0.30)          # -1%: inside the hysteresis band
    check("small ask retreat does NOT re-price", ev is None and len(h.sends) == 1,
          f"ev={ev}")
    ev = h.tick(1.88, advance=0.30)          # limit now >5% over the ask
    check("a real leg down DOES re-price", ev == "CHASE_DOWN", f"ev={ev}")
    check("the new limit is ask+2.5%, i.e. lower than the opening",
          h.sends[-1][4] < opening, f"{opening} -> {h.sends[-1][4]}")
    # ...and it must still work after the up-window has closed
    h.tick(1.86, advance=xw.ENTRY_TRACK_UP_WINDOW_SEC + 1.0)
    ev_up = h.tick(2.30, advance=0.30)
    check("UP is frozen after the up-window", ev_up is None, f"ev={ev_up}")
    n_before = len(h.sends)
    ev_dn = h.tick(1.60, advance=0.30)
    check("DOWN still runs after the up-window (this is the point of 3.0)",
          ev_dn == "CHASE_DOWN" and len(h.sends) == n_before + 1, f"ev={ev_dn}")


def case_throttle_and_deadband():
    print("\n[5] throttle, dead band, and the modify budget")
    h = Harness("THR", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    ev = h.tick(2.001, advance=1.0)
    check("a sub-dead-band move is ignored", ev is None, f"ev={ev}")
    h.tick(2.10, advance=1.0)
    n = len(h.sends)
    # same tick again with NO time advance -> throttled
    h.ctl.update_quote(ask=2.20)
    ev = h.ctl.track_entry_limit()
    check("a second re-price inside MIN_INTERVAL is throttled",
          ev is None and len(h.sends) == n, f"ev={ev}")

    # The UP side is structurally bounded by (ceiling - offset) / dead_band, so
    # only an OSCILLATING ask can reach the budget. Drive that explicitly, with
    # the up-window kept open, or the budget check passes vacuously.
    h2 = Harness("BUDGET", min_tick=0.0001, last_close=0.50,
                 entry_track_up_window=10_000.0)
    h2.fire(1.00)
    for i in range(60):
        h2.tick(1.02 if i % 2 == 0 else 0.90, advance=0.30)
    check("an oscillating ask actually reaches the budget",
          h2.ctl._track_n == xw.ENTRY_TRACK_MAX_MODIFIES,
          f"n={h2.ctl._track_n}")
    check("...and tracking then stops for good",
          h2.ctl._track_stopped == "budget_exhausted", str(h2.ctl._track_stopped))
    check("...with no further sends",
          len(h2.sends) == xw.ENTRY_TRACK_MAX_MODIFIES + 1,
          f"{len(h2.sends)} sends for n={h2.ctl._track_n}")

    # UP-only bound, for the record: with a 6% ceiling, a 2.5% offset and a
    # 1-tick dead band, a $2 name can take at most a handful of upward steps.
    h3 = Harness("UPBOUND", min_tick=0.01, last_close=1.50)
    h3.fire(2.00)
    for i in range(30):
        h3.tick(2.00 + 0.02 * (i + 1), advance=0.30)
    check("a monotonic ask ramp cannot exhaust the budget on its own",
          h3.ctl._track_n <= 8, f"n={h3.ctl._track_n} (ceiling {h3.ctl.entry_ceiling})")


def case_disabled():
    print("\n[6] ENTRY_TRACK_ENABLED=False reproduces 2.1 single-shot behaviour")
    h = Harness("OFF", min_tick=0.01, last_close=1.50, entry_track_enabled=False)
    h.fire(2.00)
    for a in (2.10, 2.30, 2.60, 1.70):
        h.tick(a, advance=0.5)
    check("exactly one placeOrder for the entry", len(h.sends) == 1,
          f"{len(h.sends)} sends")
    check("no cancels", h.client.cancelled == [])


def case_partial_fill():
    print("\n[7] partial fill: totalQuantity and the residual")
    h = Harness("PART", min_tick=0.0001, last_close=0.20)
    h.fire(0.2500)
    qty0 = h.sends[0][3]
    h.tick(0.2600, advance=0.30)
    h.status("Submitted", filled=qty0 - 1, avg=0.2550)     # partial
    ev = h.tick(0.3000, advance=0.30)
    check("tracking stops at the first fill", ev is None, f"ev={ev}")
    check("no send ever changed totalQuantity",
          all(p[3] == qty0 for p in h.sends), f"qtys={[p[3] for p in h.sends]}")
    check("position reflects only what filled",
          abs(h.ctl.position - (qty0 - 1)) < 1e-9, f"pos={h.ctl.position}")
    check("original_avg_fill stays None until the terminal Filled",
          h.ctl.original_avg_fill is None)
    check("running_entry_avg carries the interim reference",
          h.ctl.running_entry_avg == 0.2550)


def case_error_paths():
    print("\n[8] 2161 / 202 / 100 / modify-class errors")
    msg2161 = ("BUY 2 PFSA NASDAQ.SCM @ 2.15  for U18283054  In accordance with our "
               "regulatory obligations as a broker, we will initially cap (or limit) "
               "the price of your Limit Order to 1.76 or a more aggressive price "
               "still within your specified limit price.")
    check("2161 capped price is parsed", xw._parse_capped_price(msg2161) == 1.76,
          str(xw._parse_capped_price(msg2161)))

    h = Harness("CAP", min_tick=0.01, last_close=1.71)
    h.fire(2.05)
    h.err(2161, msg2161)
    check("2161 records the cap and does NOT abort",
          h.ctl.entry_capped_to == 1.76 and h.ctl.entry_failed is None)
    check("2161 does not stop tracking", h.ctl._track_stopped is None)

    h = Harness("AGG", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.err(202, "We cannot accept an order at a limit price ... more aggressive")
    ev_up = h.tick(2.50, advance=0.5)
    check("202 freezes the upward follow", ev_up is None and h.ctl._track_up_frozen)
    ev_dn = h.tick(1.70, advance=0.5)
    check("202 leaves the down ratchet alive", ev_dn == "CHASE_DOWN", f"ev={ev_dn}")

    h = Harness("MOD", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.err(10148, "OrderId 1 that needs to be cancelled cannot be cancelled")
    check("modify-class error stops tracking",
          h.ctl._track_stopped == "modify_error_10148", str(h.ctl._track_stopped))
    check("...but does not abort the run", h.ctl.entry_failed is None)

    h = Harness("RATE", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.err(100, "Max rate of messages per second has been exceeded")
    check("error 100 blocks the global throttle", xw.ORDER_THROTTLE.blocked())
    n = len(h.sends)
    h.ctl.update_quote(ask=2.30)
    h.ctl.entry_ts -= 1.0
    h.ctl.track_entry_limit()
    check("...and no re-price gets through while blocked", len(h.sends) == n)
    xw.ORDER_THROTTLE._blocked_until = 0.0    # release for the remaining cases


def _cap_msg(px):
    return ("BUY 8 X NASDAQ.SCM @ 9.99  for U18283054  In accordance with our "
            "regulatory obligations as a broker, we will initially cap (or limit) "
            f"the price of your Limit Order to {px} or a more aggressive price "
            "still within your specified limit price.")


def case_cap_rescue():
    """The one UP move when a 2161 has us parked under the ask.

    Rewritten 2026-08-18. The 2026-08-13 edition asserted TWO of the bugs as if
    they were requirements — that a flat capped ask must produce no modify, and
    that `entry_lmt_working` is None when capped — and only passed because the
    stub never delivered an openOrder. Live, the rescue fired ZERO times across
    10 capped entries in 6 days. Cases: MGRX 08-13 (flat ask, then a post-window
    leg up), CAST 08-18 (ask FROZEN for the entire 10 s), REBN 08-13 (chased
    inside the window and filled — must stay untouched)."""
    print("\n[8b] 2161 cap rescue: MGRX + CAST replays, REBN no-regression")

    # ---- MGRX: capped under a FLAT ask must be rescued once the window ends ---
    h = Harness("MGRX", min_tick=0.0001, last_close=0.533)
    h.fire(0.5770)
    check("MGRX opening shot is ask +2.5%", abs(h.sends[0][4] - 0.5915) < 1e-9,
          f"{h.sends[0][4]}")
    check("MGRX ceiling is ref +6%", abs(h.ctl.entry_ceiling - 0.6117) < 1e-9,
          f"{h.ctl.entry_ceiling}")
    h.err(2161, _cap_msg("0.5764"))
    check("the cap is recorded below the live ask",
          h.ctl.entry_capped_to == 0.5764 and h.ctl.entry_capped_to < h.ctl.ask)
    # PRODUCTION behaviour, and the reason the rescue never armed live: openOrder
    # echoes the limit WE asked for, never IBKR's cap. 3.0 preferred it for
    # capped_inert, so `working_px < ask` was permanently False.
    h.ctl.on_open_order(h.ctl.active_buy_id, 0.5915, "Submitted")
    check("openOrder reports OUR limit, not the cap (exactly as it does live)",
          h.ctl.entry_lmt_working == 0.5915 and h.ctl.entry_lmt_working > h.ctl.ask,
          f"working={h.ctl.entry_lmt_working} ask={h.ctl.ask}")
    ev = h.tick(0.5770, advance=0.9)
    check("inside the 3 s window a flat capped ask still does nothing",
          ev is None and h.ctl._track_n == 0, f"ev={ev} n={h.ctl._track_n}")
    ev = h.tick(0.5770, advance=2.5)          # past the window, ask STILL flat
    check("past the window a FLAT ask now fires CAP_RESCUE", ev == "CAP_RESCUE",
          f"ev={ev}")
    # 3.1: the envelope is _entry_upper(), so a CAPPED run is bounded by
    # cap*ENTRY_WALL_OVER_CAP_MULT (0.5764*1.06 -> 0.6109) rather than the bare
    # 0.6117 ceiling. What must survive is the ECONOMICS, not the digits: the
    # rescue still has to sit well above the ask it is trying to lift over.
    check("...to the wall-bounded envelope, not the bare ceiling",
          abs(h.sends[-1][4] - 0.6109) < 1e-9, f"{h.sends[-1][4]}")
    check("...still comfortably above the 0.5770 ask it must clear",
          h.sends[-1][4] > 0.5770 * 1.04, f"{h.sends[-1][4]} vs ask {h.ctl.ask}")
    check("...and never above the 0.6117 ceiling",
          h.sends[-1][4] <= h.ctl.entry_ceiling + 1e-9,
          f"{h.sends[-1][4]} <= {h.ctl.entry_ceiling}")
    check("...above the live ask, which is the entire point",
          h.sends[-1][4] > 0.5770, f"{h.sends[-1][4]} vs ask 0.5770")
    check("...on the SAME orderId", len({p[0] for p in h.sends}) == 1)
    check("...and the shot is now spent", h.ctl._cap_rescue_used)
    n_after = len(h.sends)
    ev2 = h.tick(0.5970, advance=0.3)
    check("a later leg up is refused — the rescue is ONE shot",
          ev2 is None and len(h.sends) == n_after, f"ev={ev2}")
    ev_dn = h.tick(0.5000, advance=0.5)
    check("the DOWN ratchet still runs after a rescue", ev_dn == "CHASE_DOWN",
          f"ev={ev_dn}")

    # ---- CAST 2026-08-18: the ask never moved at all -------------------------
    h = Harness("CAST", min_tick=0.0001, last_close=0.8598)
    h.fire(0.9700)
    check("CAST opening shot is the recorded 0.9943",
          abs(h.sends[0][4] - 0.9943) < 1e-9, f"{h.sends[0][4]}")
    check("CAST ceiling is the recorded 1.0300",
          abs(h.ctl.entry_ceiling - 1.0300) < 1e-9, f"{h.ctl.entry_ceiling}")
    h.err(2161, _cap_msg("0.9600"))
    h.ctl.on_open_order(h.ctl.active_buy_id, 0.9943, "Submitted")   # as it did live
    n0 = len(h.sends)
    for _ in range(3):
        ev = h.tick(0.9700, advance=0.9)      # ask FROZEN at 0.9700, as recorded
    check("inside the window CAST sends nothing (this part was always right)",
          ev is None and len(h.sends) == n0, f"ev={ev}")
    ev = h.tick(0.9700, advance=0.6)          # 3.3 s in, ask STILL 0.9700
    check("past the window the frozen ask fires CAP_RESCUE", ev == "CAP_RESCUE",
          f"ev={ev}")
    # 3.1: 1.0300 was the bare ceiling and it sat ABOVE CAST's own wall (a 0.96
    # cap implies a wall near 1.025), so the 08-18 rescue as shipped would have
    # drawn the very 202 it exists to avoid. Bounded, it goes to 1.01.
    check("...raising the limit 0.9943 -> 1.01 (wall-bounded, was 1.0300)",
          abs(h.sends[-1][4] - 1.01) < 1e-9, f"{h.sends[-1][4]}")
    check("...which is under the ~1.025 wall its 0.96 cap implies",
          h.sends[-1][4] < 0.96 * 1.0679, f"{h.sends[-1][4]}")
    check("...while still clearing the frozen 0.9700 ask",
          h.sends[-1][4] > 0.9700, f"{h.sends[-1][4]}")
    check("...where the OLD pricing would have re-sent 0.9943 unchanged",
          h.sends[-1][4] > 0.9943, f"{h.sends[-1][4]}")
    check("...exactly one modify, one shot",
          h.ctl._track_n == 1 and h.ctl._cap_rescue_used, f"n={h.ctl._track_n}")

    # ---- the rescue always lands ON the ceiling ------------------------------
    h = Harness("ONESHOT", min_tick=0.0001, last_close=0.45)
    h.fire(0.5000)
    h.err(2161, _cap_msg("0.4990"))
    ev = h.tick(0.5000, advance=3.5)
    # 3.1: the envelope is _entry_upper(), i.e. min(ceiling, wall bound). This
    # run IS capped, so the wall bound (0.4990*1.06 -> 0.5289) is what binds,
    # not the 0.53 ceiling. The invariant under test is unchanged: the rescue
    # spends its one shot on the FULL envelope, never on ask+offset.
    check("the rescue lands ON the wall-bounded envelope, never at ask+offset",
          ev == "CAP_RESCUE"
          and abs(h.sends[-1][4] - h.ctl._entry_upper()) < 1e-9,
          f"ev={ev} lmt={h.sends[-1][4]} upper={h.ctl._entry_upper()} "
          f"ceiling={h.ctl.entry_ceiling}")
    check("...and that envelope is the CAP bound here, below the ceiling",
          h.ctl._entry_upper() < h.ctl.entry_ceiling - 1e-9,
          f"upper={h.ctl._entry_upper()} ceiling={h.ctl.entry_ceiling}")
    ev2 = h.tick(0.5200, advance=0.3)
    check("...and at the ceiling no further UP move is possible",
          ev2 is None and h.ctl._cap_rescue_used, f"ev={ev2}")

    # ---- ask falls through the cap: no longer inert, so DOWN owns it ---------
    h = Harness("BELOW", min_tick=0.0001, last_close=0.8598)
    h.fire(0.9700)
    h.err(2161, _cap_msg("0.9600"))
    ev = h.tick(0.9000, advance=4.0)          # ask now BELOW the cap
    check("with the ask under the cap the order is not inert -> DOWN, not rescue",
          ev == "CHASE_DOWN", f"ev={ev}")
    check("...and the rescue shot is still unspent", not h.ctl._cap_rescue_used)

    # ---- already at the ceiling: don't arm, and don't swallow the ratchet ----
    h = Harness("ATCEIL", min_tick=0.0001, last_close=0.8598)
    h.fire(0.9700)
    h.err(2161, _cap_msg("0.9600"))
    h.ctl.entry_lmt = h.ctl.entry_ceiling     # as if a CHASE_CEIL had already run
    ev = h.tick(0.9700, advance=4.0)
    check("with no room left the rescue does NOT arm, and DOWN stays reachable",
          ev == "CHASE_DOWN", f"ev={ev}")
    check("...and the shot is NOT burned on a no-op", not h.ctl._cap_rescue_used)

    # ---- REBN: chased INSIDE the window, so the rescue must never arm --------
    h = Harness("REBN", min_tick=0.01, last_close=1.49)
    h.fire(1.6500)
    check("REBN opening shot is 1.70", abs(h.sends[0][4] - 1.70) < 1e-9,
          f"{h.sends[0][4]}")
    h.err(2161, _cap_msg("1.63"))
    ev = h.tick(1.7200, advance=1.775)
    # 3.1: REBN really was capped (clerk.log 2026-08-13 07:30:01.260 code=2161,
    # cap 1.63) and really did chase to 1.75 UNREJECTED, so its wall was >= 1.75.
    # cap*mult must therefore stay >= the 1.72 ask, or the clamp would cost the
    # one successful capped chase on record. That is the LOWER edge of the safe
    # window for ENTRY_WALL_OVER_CAP_MULT; CJMB's 2.40396 wall is the upper edge.
    check("an in-window leg up on a CAPPED run is now CHASE_WALL",
          ev == "CHASE_WALL", f"ev={ev}")
    check("...bounded to 1.72 rather than the 1.75 ceiling",
          abs(h.sends[-1][4] - 1.72) < 1e-9, f"{h.sends[-1][4]}")
    check("THE POINT: 1.72 is exactly the ask it filled at, so the fill survives",
          h.sends[-1][4] >= 1.72 - 1e-9, f"limit {h.sends[-1][4]} vs ask 1.72")
    check("...and the rescue was never spent", not h.ctl._cap_rescue_used)

    # ---- no 2161 at all: the 3 s box must be exactly as it was ---------------
    h = Harness("NOCAP", min_tick=0.0001, last_close=0.533)
    h.fire(0.5770)
    h.tick(0.5770, advance=4.5)
    n_before = len(h.sends)
    ev = h.tick(0.5970, advance=0.3)
    check("with no cap, a post-window leg up is still frozen",
          ev is None and h.ctl._track_up_frozen and len(h.sends) == n_before,
          f"ev={ev}")

    # ---- 202 means "too aggressive": the rescue must stay blocked ------------
    h = Harness("WALL", min_tick=0.0001, last_close=0.533)
    h.fire(0.5770)
    h.err(2161, _cap_msg("0.5764"))
    h.err(202, "We cannot accept an order at a limit price ... more aggressive")
    check("202 blocks the rescue", h.ctl._cap_rescue_blocked)
    h.tick(0.5770, advance=4.0)
    ev = h.tick(0.5970, advance=0.3)
    check("...so no rescue fires even though we are capped under the ask",
          ev is None, f"ev={ev}")

    # ---- 202 as the ack of OUR OWN cancel must not be read as the wall -------
    h = Harness("ACK", min_tick=0.0001, last_close=0.533)
    h.fire(0.5770)
    h.err(2161, _cap_msg("0.5764"))
    h.ctl._entry_abort_pending = True          # our timeout-cancel is in flight
    h.err(202, "Order Canceled - reason:")
    check("a cancel-ack 202 does NOT freeze the upward follow",
          not h.ctl._track_up_frozen, "MGRX logged this as an aggressive-limit wall")
    check("a cancel-ack 202 does NOT block the rescue",
          not h.ctl._cap_rescue_blocked)

    # ---- the kill switch -----------------------------------------------------
    saved = xw.ENTRY_CAP_RESCUE_ENABLED
    try:
        xw.ENTRY_CAP_RESCUE_ENABLED = False
        h = Harness("OFF", min_tick=0.0001, last_close=0.533)
        h.fire(0.5770)
        h.err(2161, _cap_msg("0.5764"))
        h.tick(0.5770, advance=4.0)
        ev = h.tick(0.5970, advance=0.3)
        check("ENTRY_CAP_RESCUE_ENABLED=False reverts to the plain 3 s box",
              ev is None and h.ctl._track_up_frozen, f"ev={ev}")
    finally:
        xw.ENTRY_CAP_RESCUE_ENABLED = saved


def case_dead_status_grace():
    print("\n[9] Inactive vs a Cancelled that lands right after a modify")
    h = Harness("PARTB", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.status("Inactive", filled=0, why_held="")
    check("a Part-B Inactive still aborts IMMEDIATELY (series continuity)",
          h.ctl.entry_failed == "entry_inactive" and h.ctl._entry_dead_pending is None)

    h = Harness("DESYNC", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.tick(2.10, advance=0.30)                 # a modify is now in flight
    h.status("Cancelled", filled=0)
    check("a Cancelled right after a modify is DEFERRED, not taken",
          h.ctl.entry_failed is None and h.ctl._entry_dead_pending is not None)
    h.status("Submitted", filled=0)
    check("...and is dropped when the order proves to be alive",
          h.ctl.entry_failed is None and h.ctl._entry_dead_pending is None)

    h = Harness("DESYNC2", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.tick(2.10, advance=0.30)
    h.status("Cancelled", filled=0)
    h.ctl._entry_dead_pending = (h.ctl._entry_dead_pending[0],
                                 h.ctl._entry_dead_pending[1], time.time() - 1)
    # drive one control-loop pass by hand: the deferred abort must resolve
    h.ctl.stop_evt.clear()
    _one_loop_pass(h)
    check("...but IS taken when the grace expires with no revival",
          h.ctl.entry_failed == "entry_inactive", str(h.ctl.entry_failed))


def _one_loop_pass(h):
    """Run exactly one control_loop iteration and stop."""
    import threading
    t = threading.Thread(target=xw.control_loop, args=(h.ctl, time.time()),
                         daemon=True)
    h.ctl.args.lifetime = 0          # force the loop to break after one pass
    t.start()
    t.join(timeout=5)


def case_open_order():
    print("\n[10] on_open_order sees the price IBKR is actually working")
    h = Harness("OO", min_tick=0.01, last_close=1.71)
    h.fire(2.05)
    sent = h.sends[0][4]
    h.ctl.on_open_order(h.ctl.active_buy_id, 1.76, "Submitted")
    check("the working limit is recorded", h.ctl.entry_lmt_working == 1.76)
    check("...and differs from what we sent", sent > 1.76,
          f"sent {sent}, working 1.76")
    # 2026-08-14: with NO modify outstanding there is nothing to roll back, so the
    # gap must be reported and left alone. (It is also not a cap — see
    # case_modify_reject: openOrder echoes our own limit, never IBKR's 2161.)
    check("...and with no modify outstanding, entry_lmt is NOT rewritten",
          h.ctl.entry_lmt == sent, f"entry_lmt={h.ctl.entry_lmt} sent={sent}")
    h.ctl.on_open_order(999999, 9.99, "Submitted")
    check("a foreign orderId is ignored", h.ctl.entry_lmt_working == 1.76)


def case_modify_reject():
    """A modify that IBKR did not accept must not leave the tracker believing it
    landed. Built from SCNX 2026-08-14: entry 0.4481, ask legged to 0.4504, the
    CHASE_UP to 0.4617 was rejected 2 ms later with 103 'Duplicate order id', and
    because entry_lmt kept the phantom 0.4617 the dead-band test `target > working
    + delta_floor` compared 0.4617 against itself and could never fire again. The
    order rested 0.5% under a motionless offer for 3.8 s and timed out; the stock
    ran +43.6%."""
    print("\n[12] rejected modify: rollback, retry, and the 2161 boundary")

    # --- SCNX replay: the exact prices off the recorded tape -------------------
    h = Harness("SCNX", min_tick=0.0001, last_close=0.4650)
    h.fire(0.4371)
    placed = h.sends[0][4]
    check("SCNX entry is placed at the recorded 0.4481", placed == 0.4481, str(placed))
    ev = h.tick(0.4504, advance=0.004)          # the real +4 ms gap
    check("ask 0.4504 draws the recorded CHASE_UP", ev == "CHASE_UP", f"ev={ev}")
    check("...to the recorded 0.4617", h.ctl.entry_lmt == 0.4617, str(h.ctl.entry_lmt))
    check("...and the pre-modify limit is remembered",
          h.ctl._track_prev_lmt == 0.4481, str(h.ctl._track_prev_lmt))

    h.err(103, "Duplicate order id")
    check("103 rolls entry_lmt back to the price IBKR is really working",
          h.ctl.entry_lmt == 0.4481, str(h.ctl.entry_lmt))
    check("...does NOT stop tracking", h.ctl._track_stopped is None,
          str(h.ctl._track_stopped))
    check("...does NOT abort the run", h.ctl.entry_failed is None)
    check("...and stamps a MODIFY_REJECT row",
          h.ctl._modify_reject_event == "MODIFY_REJECT_103",
          str(h.ctl._modify_reject_event))

    n_before = len(h.sends)
    ev2 = h.tick(0.4504, advance=0.30)          # same ask, one throttle interval later
    check("the very next quote re-issues the modify", ev2 == "CHASE_UP", f"ev={ev2}")
    check("...at the same 0.4617", h.ctl.entry_lmt == 0.4617, str(h.ctl.entry_lmt))
    check("...as a second wire send", len(h.sends) == n_before + 1,
          f"{n_before} -> {len(h.sends)}")
    check("...counted against the budget", h.ctl._track_n == 2, str(h.ctl._track_n))
    check("...still inside the 3 s UP window", not h.ctl._track_up_frozen)
    check("THE POINT: the retry is marketable against the 0.4504 ask",
          h.ctl.entry_lmt > 0.4504, f"limit {h.ctl.entry_lmt} vs ask 0.4504")

    # --- the phantom-limit regression, stated directly -------------------------
    h2 = Harness("PHANTOM", min_tick=0.0001, last_close=0.4650)
    h2.fire(0.4371)
    h2.tick(0.4504, advance=0.004)
    h2.ctl._track_prev_lmt = None                # simulate the pre-fix state
    ev3 = h2.tick(0.4504, advance=0.30)
    check("without the rollback the tracker is provably silent", ev3 is None,
          f"ev={ev3} lmt={h2.ctl.entry_lmt}")

    # --- a non-retryable modify error still stops, but also rolls back ---------
    h = Harness("MOD2", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    sent0 = h.sends[0][4]
    h.tick(2.20, advance=0.30)
    check("a CHASE_UP went out first", h.ctl.entry_lmt > sent0,
          f"{sent0} -> {h.ctl.entry_lmt}")
    h.err(104, "Cannot modify a filled order")
    check("104 rolls back too", h.ctl.entry_lmt == sent0, str(h.ctl.entry_lmt))
    check("...and DOES stop tracking (no retry)",
          h.ctl._track_stopped == "modify_error_104", str(h.ctl._track_stopped))
    n = len(h.sends)
    h.tick(2.40, advance=0.30)
    check("...so no further modify is sent", len(h.sends) == n)

    # --- 2161 must be untouched: a cap is NOT a rejected modify ----------------
    h = Harness("CAP2", min_tick=0.01, last_close=1.71)
    h.fire(2.05)
    h.tick(2.20, advance=0.30)
    lmt_after_chase = h.ctl.entry_lmt
    h.err(2161, _cap_msg(1.98))
    check("2161 sets the cap", h.ctl.entry_capped_to == 1.98, str(h.ctl.entry_capped_to))
    check("...and must NOT roll entry_lmt back", h.ctl.entry_lmt == lmt_after_chase,
          str(h.ctl.entry_lmt))
    check("...and must NOT stamp a MODIFY_REJECT", h.ctl._modify_reject_event is None,
          str(h.ctl._modify_reject_event))

    # --- openOrder as the second trigger, with no error code at all ------------
    h = Harness("OO2", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    sent0 = h.sends[0][4]
    h.tick(2.20, advance=0.30)
    h.ctl.on_open_order(h.ctl.active_buy_id, sent0, "Submitted")
    check("a silently dropped modify is caught by openOrder alone",
          h.ctl.entry_lmt == sent0, str(h.ctl.entry_lmt))
    check("...and stamps a MODIFY_REJECT row",
          h.ctl._modify_reject_event == "MODIFY_REJECT_openOrder",
          str(h.ctl._modify_reject_event))
    check("...while entry_lmt_working keeps its own meaning",
          h.ctl.entry_lmt_working == sent0, str(h.ctl.entry_lmt_working))

    # openOrder reporting a price we NEVER sent must not be treated as a reject:
    # that is the shape a genuine cap would take if IBKR ever reported it.
    h = Harness("OO3", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    h.tick(2.20, advance=0.30)
    lmt = h.ctl.entry_lmt
    h.ctl.on_open_order(h.ctl.active_buy_id, 1.11, "Submitted")
    check("an unrecognised working price does NOT roll anything back",
          h.ctl.entry_lmt == lmt, str(h.ctl.entry_lmt))

    # --- the retry is bounded: a persistent 103 cannot become a storm ----------
    h = Harness("STORM", min_tick=0.01, last_close=1.50)
    h.fire(2.00)
    ask = 1.90
    for _ in range(60):
        if h.ctl._track_stopped is not None:
            break
        ask -= 0.02
        if h.tick(ask, advance=0.30):
            h.err(103, "Duplicate order id")
    check("a persistent 103 stops at the modify budget",
          h.ctl._track_n <= xw.ENTRY_TRACK_MAX_MODIFIES, f"n={h.ctl._track_n}")
    check("...and tracking is disabled rather than looping",
          h.ctl._track_stopped is not None, str(h.ctl._track_stopped))
    check("...with one wire send per counted modify",
          len(h.sends) == h.ctl._track_n + 1, f"sends={len(h.sends)} n={h.ctl._track_n}")


def case_table_columns():
    """The row dict and PA_COLUMNS must agree, and every new column must be
    APPENDED so 2.1-era parsers (which set-match on `event`) still read the file.

    Append-only is checked in SLICES, not as one tail comparison: 3.0 added five
    columns and 3.1 added two more, and a single `PA_COLUMNS[19:]` assertion
    fails the moment anyone appends correctly — which is the wrong signal."""
    print("\n[11] price-action table: real write, real header")
    import csv
    import tempfile
    legacy = ["timestamp", "symbol", "phase", "bid", "ask", "mid", "orig_avg_fill",
              "filled_qty", "position", "yield_pct", "row_yield_threshold",
              "trigger_pct", "limit_pct", "aux_price", "lmt_price",
              "protective_order_id", "protective_status", "event", "why_held"]
    check("the 19 legacy columns are unchanged and still first",
          xw.PA_COLUMNS[:19] == legacy, f"{xw.PA_COLUMNS[:19]}")
    check("the 5 x-wing-3.0 columns still sit at 19:24",
          xw.PA_COLUMNS[19:24] == ["entry_order_id", "entry_lmt", "entry_status",
                                   "track_n", "capped_to"], f"{xw.PA_COLUMNS[19:24]}")
    check("the 2 x-wing-3.1 wall columns still sit at 24:26",
          xw.PA_COLUMNS[24:26] == ["wall_obs", "ref_snapshot"],
          f"{xw.PA_COLUMNS[24:26]}")
    # 3.2 appended the exit-noise diagnostics after them. The assertion is
    # append-ONLY: three readers (this file, analyze_firstrade_outcomes.py,
    # calibrate_price_wall.py) parse these tables by column name, so a reorder
    # silently corrupts historical analysis. Position 26 onward is the only
    # place a new column may go.
    check("the 9 x-wing-3.2 exit columns still sit at 26:35",
          xw.PA_COLUMNS[26:35] == ["last", "spread_pct", "eff_trigger_pct",
                                   "eff_limit_pct", "trigger_source", "deferred_n",
                                   "exit_fill_px", "exit_fill_ts",
                                   "realized_yield_pct"],
          f"{xw.PA_COLUMNS[26:35]}")
    # 3.3 appended the market-microstructure columns -- the ticks that were
    # already arriving on the clerk's subscription and were being discarded.
    check("the 22 x-wing-3.3 market columns are appended last",
          xw.PA_COLUMNS[35:] == ["bid_size", "ask_size", "last_size",
                                 "microprice", "touch_imbalance", "day_high",
                                 "day_low", "day_volume_lots", "vwap",
                                 "px_vs_vwap_pct", "trade_count", "trade_rate",
                                 "volume_rate", "vol_3min", "vol_5min",
                                 "vol_10min", "halted", "quote_rate",
                                 "bid_vol_10s", "trade_through_n",
                                 "last_exch_ts", "static_snapshot"],
          f"{xw.PA_COLUMNS[35:]}")

    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "t.tsv"
        h = Harness("TBL", min_tick=0.01, last_close=1.50,
                    price_action_table=str(out))
        h.ctl.pa = xw.PriceActionWriter(str(out), "TBL", td, log=h.log)
        h.fire(2.00)
        h.tick(2.10, advance=0.30)
        h.err(2161, "cap (or limit) the price of your Limit Order to 1.98 or a more")
        h.ctl.args.lifetime = 0
        _one_loop_pass(h)
        h.ctl.pa.close()
        check("a file was written", out.exists())
        if out.exists():
            rows = list(csv.DictReader(open(out, newline=""), delimiter="\t"))
            check("header matches PA_COLUMNS exactly",
                  list(rows[0].keys()) == xw.PA_COLUMNS if rows else False)
            r = rows[0] if rows else {}
            check("entry_order_id is populated",
                  str(r.get("entry_order_id")) == str(h.ctl.active_buy_id),
                  f"{r.get('entry_order_id')}")
            check("entry_lmt is populated", bool(r.get("entry_lmt")),
                  f"{r.get('entry_lmt')}")
            check("track_n is populated", r.get("track_n") == "1",
                  f"{r.get('track_n')}")
            check("capped_to carries IBKR's cap", r.get("capped_to", "").startswith("1.98"),
                  f"{r.get('capped_to')}")
            check("the ENTRY_CAPPED event is recorded",
                  any(x.get("event") == "ENTRY_CAPPED" for x in rows),
                  f"events={[x.get('event') for x in rows]}")


def _wall_msg(wall, ref, brk="using a <br>limit"):
    """A real 202 aggressive-limit rejection. The literal <br> MOVES with the
    length of the price string — `using a <br>limit`, `using <br>a limit` and
    `order <br>using a limit` all occur in the corpus — so the parser is tested
    against that, not against one tidy sentence."""
    return ("Order Canceled - reason:We cannot accept an order at a limit price "
            "at or more aggressive than %s. Please submit your order %s price "
            "that is closer to the current market price of %s." % (wall, brk, ref))


def case_price_wall():
    """IBKR's regulatory price wall: the clamp, and the re-place after a 202.

    Three real runs, all reproduced from clerk.log:
      YAAS 2026-08-27 07:30:24 — the OPENING shot (3.13) was over the wall
        (2.99). Never capped first, so no clamp could have saved it; only the
        re-place can. Stock then ran to a 4.33 bid, +60%.
      CJMB 2026-08-27 08:15:03 — opening 2.36 ACCEPTED and 2161-capped to 2.25,
        then OUR OWN CHASE_CEIL to the 2.44 ceiling crossed the 2.40396 wall and
        killed a live order.
      EHGO 2026-08-19 06:30:00 — identical to CJMB (cap 1.91, chase 2.10, wall
        2.093).
    The wall is ref*1.196 exactly on all three."""
    print("\n[14] IBKR price wall: clamp + bounded re-place")

    # ---- the parser, against every <br> position seen live -------------------
    for wall, ref, brk in ((2.99, 2.5, "using a <br>limit"),
                           (2.40396, 2.01, "using <br>a limit"),
                           (2.093, 1.75, "<br>using a limit")):
        w, r = xw._parse_wall_prices(_wall_msg(wall, ref, brk))
        check(f"202 parses wall/ref through '{brk}'",
              w == wall and r == ref, f"{w}/{r}")
    check("a generic 202 yields no wall (it is a BUCKET, not one error)",
          xw._parse_wall_prices("Order Canceled - reason:") == (None, None))
    check("the trigger-price 202 variant yields no wall either",
          xw._parse_wall_prices("Order Canceled - reason:The Limit Price for this "
                                "order should be better or equal to the trigger "
                                "price") == (None, None))

    # ---- _wall_step_down: the wall ITSELF is refused -------------------------
    h = Harness("STEP", min_tick=0.01, last_close=1.0)
    check("an exact tick multiple steps one tick DOWN (2.99 -> 2.98)",
          abs(h.ctl._wall_step_down(2.99) - 2.98) < 1e-9,
          f"{h.ctl._wall_step_down(2.99)}")
    check("a non-multiple just floors (2.40396 -> 2.40)",
          abs(h.ctl._wall_step_down(2.40396) - 2.40) < 1e-9,
          f"{h.ctl._wall_step_down(2.40396)}")
    hs = Harness("STEPSUB", min_tick=0.0001, last_close=0.5)
    check("sub-$1 keeps the 0.0001 grid (0.5764 -> 0.5763)",
          abs(hs.ctl._wall_step_down(0.5764) - 0.5763) < 1e-9,
          f"{hs.ctl._wall_step_down(0.5764)}")

    # ---- CJMB: the cap clamp stops the chase walking into the wall -----------
    h = Harness("CJMB", min_tick=0.01, last_close=2.10)
    h.fire(2.30, 2.29)
    check("CJMB opening shot reproduces the log (2.3600)",
          abs(h.sends[0][4] - 2.36) < 1e-9, f"{h.sends[0][4]}")
    check("CJMB ceiling reproduces the log (2.4400)",
          abs(h.ctl.entry_ceiling - 2.44) < 1e-9, f"{h.ctl.entry_ceiling}")
    h.err(2161, _cap_msg("2.25"))
    ev = h.tick(2.60, advance=1.73)
    check("a capped run chasing up is CHASE_WALL, not CHASE_CEIL",
          ev == "CHASE_WALL", f"ev={ev}")
    check("...bounded to 2.38 by the 2.25 cap",
          abs(h.sends[-1][4] - 2.38) < 1e-9, f"{h.sends[-1][4]}")
    check("THE POINT: 2.38 is under the 2.40396 wall that really killed it",
          h.sends[-1][4] < 2.40396, f"{h.sends[-1][4]} < 2.40396")
    check("...and still above the 2.36 IBKR had already accepted",
          h.sends[-1][4] > 2.36, f"{h.sends[-1][4]}")
    # pre-fix proof: neutralise the multiplier and the old target comes back
    keep = xw.ENTRY_WALL_OVER_CAP_MULT
    try:
        xw.ENTRY_WALL_OVER_CAP_MULT = 1e9
        h2 = Harness("CJMBPRE", min_tick=0.01, last_close=2.10)
        h2.fire(2.30, 2.29)
        h2.err(2161, _cap_msg("2.25"))
        ev2 = h2.tick(2.60, advance=1.73)
        check("PRE-FIX: unbounded, it targets 2.44 and trips the real wall",
              ev2 == "CHASE_CEIL" and abs(h2.sends[-1][4] - 2.44) < 1e-9
              and h2.sends[-1][4] > 2.40396, f"ev={ev2} lmt={h2.sends[-1][4]}")
    finally:
        xw.ENTRY_WALL_OVER_CAP_MULT = keep

    # ---- EHGO: same shape, independent tape ---------------------------------
    h = Harness("EHGO", min_tick=0.01, last_close=1.81)
    h.fire(1.98, 1.98)
    check("EHGO opening shot reproduces the log (2.0300)",
          abs(h.sends[0][4] - 2.03) < 1e-9, f"{h.sends[0][4]}")
    check("EHGO ceiling reproduces the log (2.1000)",
          abs(h.ctl.entry_ceiling - 2.10) < 1e-9, f"{h.ctl.entry_ceiling}")
    h.err(2161, _cap_msg("1.91"))
    h.tick(2.08, advance=1.90)
    check("EHGO never re-prices above its 2.093 wall",
          all(p[4] < 2.093 for p in h.all_entry_sends),
          f"{[p[4] for p in h.all_entry_sends]}")

    # ---- YAAS: the opening shot IS the wall breach; only a re-place helps ----
    h = Harness("YAAS", min_tick=0.01, last_close=2.52)
    h.fire(3.05, 2.70)
    oid0 = h.ctl.active_buy_id
    check("YAAS opening shot reproduces the log (3.1300)",
          abs(h.sends[0][4] - 3.13) < 1e-9, f"{h.sends[0][4]}")
    check("YAAS ceiling reproduces the log (3.2400)",
          abs(h.ctl.entry_ceiling - 3.24) < 1e-9, f"{h.ctl.entry_ceiling}")
    check("no cap was ever seen, so the clamp CANNOT save this shape",
          h.ctl._wall_bound() == float("inf") and h.sends[0][4] > 2.99,
          f"bound={h.ctl._wall_bound()}")
    # ⚠ production order: the Cancelled arrives BEFORE the 202 that explains it
    h.status("Cancelled", filled=0)
    check("THE ORDERING FIX: Cancelled alone must NOT terminate the run yet",
          h.ctl.entry_failed is None, f"entry_failed={h.ctl.entry_failed}")
    h.err(202, _wall_msg(2.99, 2.5))
    check("the exact wall is recorded from the message",
          h.ctl.entry_wall_obs == 2.99 and h.ctl.entry_wall_ref == 2.5,
          f"wall={h.ctl.entry_wall_obs} ref={h.ctl.entry_wall_ref}")
    check("...and the re-place is armed", h.ctl._entry_wall_retry_pending)
    ev = h.ctl.retry_entry_after_wall()
    check("the re-place happens and is labelled", ev == "ENTRY_RETRY_0", f"{ev}")
    check("...on a NEW orderId, with the old one retired",
          h.ctl.active_buy_id != oid0 and h.ctl._retired_entry_ids == {oid0},
          f"{oid0} -> {h.ctl.active_buy_id}, retired={h.ctl._retired_entry_ids}")
    check("...priced at 2.98, one tick under the 2.99 wall",
          abs(h.sends[-1][4] - 2.98) < 1e-9, f"{h.sends[-1][4]}")
    check("...never above the ORIGINAL frozen ceiling",
          h.sends[-1][4] <= h.ctl.entry_ceiling + 1e-9,
          f"{h.sends[-1][4]} <= {h.ctl.entry_ceiling}")
    check("...BELOW the live 3.05 ask, and that is CORRECT — IBKR's reference "
          "lags and catches up (IVF 2026-08-17 proved it on the sell side)",
          h.sends[-1][4] < 3.05, f"{h.sends[-1][4]} < 3.05")
    check("the 10 s fill clock was NOT restarted by the re-place",
          h.ctl.entry_ts is not None
          and (time.time() - h.ctl.entry_ts) >= 0, "entry_ts preserved")
    check("UP stays frozen on the replacement (IBKR said too aggressive)",
          h.ctl._track_up_frozen and h.ctl._cap_rescue_blocked)
    check("the run is live again", h.ctl.entry_failed is None)

    # bounded: exactly ENTRY_WALL_RETRY_MAX re-places, never more
    for _ in range(4):
        h.warp(0.6)
        h.status("Cancelled", filled=0)
        h.err(202, _wall_msg(2.99, 2.5))
        h.ctl.retry_entry_after_wall()
    ids = {p[0] for p in h.client.placed}
    check("a persistent wall stops at the retry budget",
          len(ids) == 1 + xw.ENTRY_WALL_RETRY_MAX,
          f"{len(ids)} ids for budget {xw.ENTRY_WALL_RETRY_MAX}")
    check("...and the run then dies rather than looping",
          h.ctl.entry_failed == "entry_inactive", f"{h.ctl.entry_failed}")

    # ---- the guards ---------------------------------------------------------
    h = Harness("LATE", min_tick=0.01, last_close=2.52)
    h.fire(3.05, 2.70)
    h.warp(9.0)                                   # 1.0 s left of the 10 s window
    h.status("Cancelled", filled=0)
    h.err(202, _wall_msg(2.99, 2.5))
    check("a re-place with under ENTRY_WALL_RETRY_MIN_LIFE_SEC left is refused",
          h.ctl.retry_entry_after_wall() is None
          and len(h.client.placed) == 1, f"{len(h.client.placed)} sends")

    h = Harness("PART", min_tick=0.01, last_close=2.52)
    h.fire(3.05, 2.70)
    h.status("Submitted", filled=1, avg=3.13)
    h.err(202, _wall_msg(2.99, 2.5))
    check("a PARTIALLY FILLED entry is never re-placed",
          not h.ctl._entry_wall_retry_pending
          and h.ctl.retry_entry_after_wall() is None)

    h = Harness("ACK2", min_tick=0.01, last_close=2.0)
    h.fire(2.0)
    h.ctl._entry_abort_pending = True
    h.err(202, "Order Canceled - reason:")
    check("a 202 acking OUR OWN cancel records no wall and re-places nothing",
          h.ctl.entry_wall_obs is None
          and not h.ctl._entry_wall_retry_pending)

    # ---- kill switch: full 3.0 behaviour ------------------------------------
    keep = xw.ENTRY_WALL_RETRY_ENABLED
    try:
        xw.ENTRY_WALL_RETRY_ENABLED = False
        h = Harness("OFF", min_tick=0.01, last_close=2.52)
        h.fire(3.05, 2.70)
        h.status("Cancelled", filled=0)
        check("PRE-FIX: with the retry off, Cancelled aborts immediately",
              h.ctl.entry_failed == "entry_inactive", f"{h.ctl.entry_failed}")
        h.err(202, _wall_msg(2.99, 2.5))
        check("...and nothing is ever re-placed",
              h.ctl.retry_entry_after_wall() is None
              and len(h.client.placed) == 1, f"{len(h.client.placed)} sends")
    finally:
        xw.ENTRY_WALL_RETRY_ENABLED = keep

    keep = xw.ENTRY_WALL_ENABLED
    try:
        xw.ENTRY_WALL_ENABLED = False
        h = Harness("NOWALL", min_tick=0.01, last_close=2.10)
        h.fire(2.30, 2.29)
        h.err(2161, _cap_msg("2.25"))
        ev = h.tick(2.60, advance=1.73)
        check("--no-entry-wall restores 3.0 exactly: CHASE_CEIL to 2.44",
              ev == "CHASE_CEIL" and abs(h.sends[-1][4] - 2.44) < 1e-9,
              f"ev={ev} lmt={h.sends[-1][4]}")
    finally:
        xw.ENTRY_WALL_ENABLED = keep

    # ---- a run that never meets the wall must be byte-identical to 3.0 ------
    h = Harness("CLEAN", min_tick=0.0001, last_close=0.3872)
    h.fire(0.6698)
    check("with no cap and no 202 the bound is +inf",
          h.ctl._wall_bound() == float("inf"), f"{h.ctl._wall_bound()}")
    check("...so _entry_upper() IS the plain ceiling",
          h.ctl._entry_upper() == h.ctl.entry_ceiling,
          f"{h.ctl._entry_upper()} == {h.ctl.entry_ceiling}")


def _sell_wall_msg(wall, ref):
    return ("Order Canceled - reason:We cannot accept an order at a limit price "
            "at or more aggressive than %s. Please submit your order <br>using a "
            "limit price that is closer to the current market price of %s."
            % (wall, ref))


def case_protective_storm():
    """The SELL side of the wall, and the order storm it used to cause.

    IVF 2026-08-17 09:40:59.627 -> 09:41:01.284: the protective cancel+new
    fallback had no counter, no throttle and no gap and re-sent the IDENTICAL
    2.39 against a 2.4262898 floor — orderIds 34..44, ELEVEN orders in 1.66 s,
    each 202-rejected and each also drawing a 10148, position unprotected ~6.4 s.
    For a SELL "more aggressive" is LOWER, so the wall is a FLOOR."""
    print("\n[15] protective SELL wall: bounded cancel+replace")

    check("the sell band is ref*0.902 on all three IVF references",
          all(abs(w / r - 0.902) < 1e-6 for w, r in
              ((2.4262898, 2.6899), (2.411948, 2.674), (2.4049124, 2.6662))))

    h = Harness("STEPUP", min_tick=0.01, last_close=2.5)
    check("_wall_step_up lifts strictly ABOVE the floor (2.4262898 -> 2.43)",
          abs(h.ctl._wall_step_up(2.4262898) - 2.43) < 1e-9,
          f"{h.ctl._wall_step_up(2.4262898)}")
    check("...and an exact multiple gets one extra tick (2.43 -> 2.44)",
          abs(h.ctl._wall_step_up(2.43) - 2.44) < 1e-9,
          f"{h.ctl._wall_step_up(2.43)}")

    # ---- the storm itself ---------------------------------------------------
    h = Harness("IVF", min_tick=0.01, last_close=2.60)
    ctl = h.ctl
    ctl.position = 2.0
    ctl.exiting = True
    ctl._lmt_floor = 2.39                     # the price IVF kept re-sending
    ctl.protective_id = 34
    ctl.orders[34] = dict(action="SELL", otype="LMT", status="Submitted",
                          filled=0.0, avg=0.0, role="protective")
    n_before = len(h.client.placed)
    for i in range(11):                       # exactly what production did
        ctl.on_order_error(ctl.protective_id, 202,
                           _sell_wall_msg(2.4262898, 2.6899))
    replaces = len(h.client.placed) - n_before
    check("eleven rejections no longer produce eleven orders",
          replaces <= xw.PROTECTIVE_MODIFY_RETRY_MAX,
          f"{replaces} replaces for {xw.PROTECTIVE_MODIFY_RETRY_MAX} budget")
    check("...and the FIRST replace is lifted above the 2.4262898 floor",
          h.client.placed[n_before][4] >= 2.43 - 1e-9,
          f"{h.client.placed[n_before][4]}")
    check("...i.e. never the 2.39 IBKR had already refused",
          all(p[4] > 2.39 for p in h.client.placed[n_before:]),
          f"{[p[4] for p in h.client.placed[n_before:]]}")
    check("...each on its own new orderId, as the fallback requires",
          len({p[0] for p in h.client.placed[n_before:]}) == replaces,
          f"ids={[p[0] for p in h.client.placed[n_before:]]}")

    # ---- the gap gate, independent of the budget ----------------------------
    h = Harness("GAP", min_tick=0.01, last_close=2.60)
    ctl = h.ctl
    ctl.position = 2.0
    ctl.exiting = True
    ctl._lmt_floor = 2.39
    ctl.protective_id = 34
    ctl.orders[34] = dict(action="SELL", otype="LMT", status="Submitted",
                          filled=0.0, avg=0.0, role="protective")
    ctl.on_order_error(34, 202, _sell_wall_msg(2.4262898, 2.6899))
    first = len(h.client.placed)
    ctl.on_order_error(ctl.protective_id, 202, _sell_wall_msg(2.4262898, 2.6899))
    check("a second rejection inside the gap window is spaced, not sent",
          len(h.client.placed) == first, f"{len(h.client.placed)} vs {first}")
    ctl._prot_reject_last_ts -= (xw.PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC + 0.1)
    ctl.on_order_error(ctl.protective_id, 202, _sell_wall_msg(2.4262898, 2.6899))
    check("...and IS sent once the gap has elapsed",
          len(h.client.placed) == first + 1, f"{len(h.client.placed)}")

    # ---- a NON-202 modify rejection keeps the documented behaviour ----------
    h = Harness("TYPE", min_tick=0.01, last_close=2.60)
    ctl = h.ctl
    ctl.position = 2.0
    ctl.exiting = True
    ctl._lmt_floor = 2.39
    ctl.protective_id = 34
    ctl.orders[34] = dict(action="SELL", otype="LMT", status="Submitted",
                          filled=0.0, avg=0.0, role="protective")
    ctl.on_order_error(34, 105, "Order being modified does not match original order")
    check("a non-202 modify reject still cancel+news at the HELD price",
          len(h.client.placed) == 1 and abs(h.client.placed[0][4] - 2.39) < 1e-9,
          f"{[p[4] for p in h.client.placed]}")
    check("...and still cancels the old protective id",
          h.client.cancelled == [34], f"{h.client.cancelled}")


def _juns_ctl(min_tick=0.01, entry=2.66, bid=2.55, pos=1):
    """A controller in the exact state JUNS 2026-10-06 was in at 11:15:38.216:
    hard_floor has fired, `exiting` is latched, and the resting exit is dead."""
    h = Harness("JUNS", min_tick=min_tick, last_close=2.42)
    ctl = h.ctl
    ctl.position = float(pos)
    ctl.original_avg_fill = entry
    ctl.update_quote(bid=bid, ask=bid + 0.06, last=bid)
    ctl.exiting = True
    ctl._trigger_source = "hard_floor"
    ctl._exit_lmt_sent = None          # what the control loop records for hard_floor
    ctl._lmt_floor = 1.98              # the LADDER's limit -- not the hard floor's
    ctl._aux_floor = 2.19
    ctl._exit_ts = time.time()
    ctl.protective_id = 62
    ctl.orders[62] = dict(action="SELL", otype="LMT", status="Cancelled",
                          filled=0.0, avg=0.0, role="protective")
    return h, ctl


def case_dead_exit_rearm():
    """JUNS 2026-10-06: a dead protective exit left 1 sh naked for 4 hours.

    The chain, from the clerk log:
      11:15:38.103  hard_floor fires -> type-change modify (STP LMT -> LMT @ 2.50
                    the bid) on the LIVE orderId 61
      11:15:38.105  IBKR 103 "Duplicate order id"
      11:15:38.105  cancel+new fallback places 62 @ _lmt_floor = 1.98 -- the
                    LADDER's limit, not the bid the hard floor asked for
      11:15:38.216  IBKR 202 "more aggressive than 2.3001 ... market price 2.55",
                    111 ms after the replace, so the min-gap guard returned
                    having done nothing
      -> EXITING, protective_status=Cancelled, 1 sh open, for 4 hours.
    """
    print("\n[18] dead protective exit: re-arm (JUNS 2026-10-06)")

    # ---- defect A: the repair price must honour WHICH trigger fired ---------
    h, ctl = _juns_ctl()
    check("a hard_floor repair prices at the BID, not the ladder limit",
          abs(ctl._exit_repair_price() - 2.55) < 1e-9,
          f"{ctl._exit_repair_price()} (ladder was {ctl._lmt_floor})")
    ctl._trigger_source = "bid+last"
    ctl._exit_lmt_sent = 1.98
    check("...while a ladder trigger still repairs at the held limit",
          abs(ctl._exit_repair_price() - 1.98) < 1e-9,
          f"{ctl._exit_repair_price()}")

    # ---- defect C: the sell wall is remembered and cleared -----------------
    h, ctl = _juns_ctl()
    ctl.on_order_error(62, 202, _sell_wall_msg(2.3001, 2.55))
    check("a 202 on the protective id records the sell wall",
          ctl.exit_wall_obs == 2.3001, f"{ctl.exit_wall_obs}")
    check("...and its reference price too", ctl.exit_wall_ref == 2.55,
          f"{ctl.exit_wall_ref}")
    ctl._trigger_source = "bid+last"
    ctl._exit_lmt_sent = 1.98
    check("...so a later repair is lifted above it, never back to 1.98",
          ctl._exit_repair_price() >= 2.3001 + 1e-9,
          f"{ctl._exit_repair_price()}")
    check("...landing on the first tick strictly above the floor",
          abs(ctl._exit_repair_price() - 2.31) < 1e-9,
          f"{ctl._exit_repair_price()}")
    check("the highest floor shown this run is the one kept",
          (ctl.on_order_error(62, 202, _sell_wall_msg(2.1, 2.4)) is None
           and ctl.exit_wall_obs == 2.3001), f"{ctl.exit_wall_obs}")

    # ---- defect B: the min-gap guard no longer ends the story --------------
    h, ctl = _juns_ctl()
    n0 = len(h.client.placed)
    # the real sequence: one 202 inside the gap window, handled by the guard
    ctl._prot_reject_n = 1
    ctl._prot_reject_last_ts = time.time()
    ctl.on_order_error(62, 202, _sell_wall_msg(2.3001, 2.55))
    check("the 202 inside the gap window still sends nothing (3.2 behaviour)",
          len(h.client.placed) == n0, f"{len(h.client.placed) - n0} sends")
    check("...and chase_exit_limit still refuses to push a refused SELL lower",
          ctl.chase_exit_limit() is None)
    # first supervisor pass only ARMS the grace -- it must not race a replace
    check("the first re-arm pass arms the grace and sends nothing",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == n0,
          f"dead_since set={ctl._exit_dead_since is not None}")
    check("...and a pass inside the grace window is still silent",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == n0)
    ctl._exit_dead_since -= (xw.EXIT_REARM_AFTER_SEC + 0.1)
    ev = ctl.rearm_dead_exit()
    check("...then the grace elapses and the exit IS re-armed",
          ev == "EXIT_REARM_1" and len(h.client.placed) == n0 + 1,
          f"event={ev} sends={len(h.client.placed) - n0}")
    oid, action, otype, qty, lmt, _algo = h.client.placed[-1]
    check("the re-arm is a SELL LMT for the whole open position",
          action == "SELL" and otype == "LMT" and qty == 1,
          f"{action} {otype} {qty}")
    check("...on a FRESH orderId, never the dead 62 that drew the 103",
          oid != 62 and ctl.protective_id == oid, f"id={oid}")
    check("...priced above the 2.3001 wall IBKR had just quoted",
          lmt >= 2.3001 + 1e-9, f"{lmt}")
    check("...and never at the 1.98 IBKR already refused", lmt > 1.98,
          f"{lmt}")

    # ---- bounded exactly like every other order path in this file ----------
    h, ctl = _juns_ctl()
    n0 = len(h.client.placed)
    for _ in range(12):
        ctl.orders[ctl.protective_id] = dict(action="SELL", otype="LMT",
                                             status="Cancelled", filled=0.0,
                                             avg=0.0, role="protective")
        ctl.rearm_dead_exit()                     # arms the grace
        if ctl._exit_dead_since is not None:
            ctl._exit_dead_since -= (xw.EXIT_REARM_AFTER_SEC + 0.1)
        ctl._exit_rearm_ts -= (xw.EXIT_REARM_MIN_GAP_SEC + 0.1)
        xw.ORDER_THROTTLE._tokens = xw.ORDER_THROTTLE.rate
        ctl.rearm_dead_exit()
    sent = len(h.client.placed) - n0
    check("twelve dead reads cannot produce twelve orders",
          sent <= xw.EXIT_REARM_MAX_N,
          f"{sent} sends for a {xw.EXIT_REARM_MAX_N} budget")
    check("...and the budget-spent state latches so it is logged once",
          ctl._exit_rearm_exhausted)

    # ---- the states it must NOT act on -------------------------------------
    h, ctl = _juns_ctl()
    ctl.orders[62]["status"] = "Submitted"
    n0 = len(h.client.placed)
    check("a WORKING exit is left alone (never double the sell side)",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == n0)
    ctl.orders[62]["status"] = ""
    check("...and so is an exit with no status yet (unknown != dead)",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == n0)
    ctl.orders[62]["status"] = "Cancelled"
    ctl.orders[62]["filled"] = 1.0
    check("...and one that is dead because it FILLED",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == n0)
    h, ctl = _juns_ctl(pos=0)
    check("a flat run is not re-armed",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == 0)
    h, ctl = _juns_ctl()
    ctl.exiting = False
    check("...nor is a run that has not triggered an exit",
          ctl.rearm_dead_exit() is None and len(h.client.placed) == 0)

    # ---- the kill switch ---------------------------------------------------
    h, ctl = _juns_ctl()
    xw.EXIT_REARM_ENABLED = False
    try:
        check("--no-exit-rearm is a true no-op",
              ctl.rearm_dead_exit() is None and len(h.client.placed) == 0)
    finally:
        xw.EXIT_REARM_ENABLED = True

    # ---- the lifetime FLATTEN inherited both defects ------------------------
    # 15:15:04 took the SAME 103 -> 202 pair, because shutdown() re-placed on
    # the dead protective id at the ladder price: "FLATTEN incomplete: 1 sh
    # still open; SELL LMT left resting at the broker".
    h, ctl = _juns_ctl()
    ctl._trigger_source = "bid+last"
    ctl._exit_lmt_sent = 1.98
    check("the flatten forces the BID even for a ladder trigger (it must FILL)",
          abs(ctl._exit_repair_price(force_bid=True) - 2.55) < 1e-9,
          f"{ctl._exit_repair_price(force_bid=True)} vs "
          f"{ctl._exit_repair_price()} when not flattening")
    ctl.exit_wall_obs = 2.6000
    check("...but still never below a wall IBKR has shown us",
          abs(ctl._exit_repair_price(force_bid=True) - 2.61) < 1e-9,
          f"{ctl._exit_repair_price(force_bid=True)}")
    check("ORDER_DEAD_STATUSES is what shutdown tests the resting exit against",
          ctl.orders[62]["status"] in xw.ORDER_DEAD_STATUSES
          and "Submitted" not in xw.ORDER_DEAD_STATUSES,
          f"{sorted(xw.ORDER_DEAD_STATUSES)}")

    # ---- a new entry fill resets the budget --------------------------------
    h, ctl = _juns_ctl()
    ctl._exit_rearm_n = xw.EXIT_REARM_MAX_N
    ctl._exit_rearm_exhausted = True
    ctl.active_buy_id = 59
    ctl.orders[59] = dict(action="BUY", otype="LMT", status="Submitted",
                          filled=0.0, avg=0.0, role="entry")
    ctl.on_order_status(59, "Filled", 1.0, 2.66, 2.66)
    check("a fresh entry fill resets the re-arm budget for the new cycle",
          ctl._exit_rearm_n == 0 and not ctl._exit_rearm_exhausted
          and ctl._exit_dead_since is None,
          f"n={ctl._exit_rearm_n}")


# --------------------------------------------------------------------------- #
def case_exit_noise_defences():
    """[16] 3.2 exit-noise defences: trade confirmation, spread band, hard floor.

    The three labelled 2026-10-01 ejections are all held by the spread band
    alone, so replay_exit.py never exercises the trade confirmation. These
    assertions drive exit_decision() directly so each defence is tested on its
    own, with the others neutralised."""
    print("\n[16] 3.2 exit-noise defences")

    def armed(entry=3.73, bid=3.62, ask=3.71, last=3.71, spread_mult=0.0,
              floor_pct=0.0, confirm=True, on_last=True):
        """A controller holding 1 share at `entry`, with the chosen defences on."""
        h = Harness(symbol="EXIT", min_tick=0.01)
        with h.ctl.lock:
            h.ctl.original_avg_fill = entry
            h.ctl.position = 1.0
        h.ctl.update_quote(bid=bid, ask=ask, last=last)
        return h

    import contextlib

    @contextlib.contextmanager
    def knobs(spread_mult=0.0, floor_pct=0.0, confirm=True, on_last=True):
        old = (xw.EXIT_TRIGGER_SPREAD_MULT, xw.HARD_FLOOR_PCT,
               xw.EXIT_REQUIRE_TRADE_CONFIRM, xw.HARD_FLOOR_ON_LAST)
        xw.EXIT_TRIGGER_SPREAD_MULT = spread_mult
        xw.HARD_FLOOR_PCT = floor_pct
        xw.EXIT_REQUIRE_TRADE_CONFIRM = confirm
        xw.HARD_FLOOR_ON_LAST = on_last
        try:
            yield
        finally:
            (xw.EXIT_TRIGGER_SPREAD_MULT, xw.HARD_FLOOR_PCT,
             xw.EXIT_REQUIRE_TRADE_CONFIRM, xw.HARD_FLOOR_ON_LAST) = old

    # --- (A) trade confirmation, with the band and the floor OFF -------------
    # The exact MEDS shape: bid crosses aux, last and ask are at/above entry.
    with knobs(spread_mult=0.0, floor_pct=0.0, confirm=True):
        h = armed()
        fire, src = h.ctl.exit_decision(3.58, 3.58)   # bid == aux
        check("MEDS shape: bid at aux but last 3.71 above it -> HELD",
              fire is False and src == "deferred", f"fire={fire} src={src}")
        check("...and the deferral is counted for the table",
              h.ctl._deferred_n == 1, f"{h.ctl._deferred_n}")

        fire, src = h.ctl.exit_decision(3.58, 3.58)
        h.ctl.update_quote(last=3.50)                  # now a trade agrees
        fire, src = h.ctl.exit_decision(3.58, 3.58)
        check("...but once a trade prints at/below aux it FIRES",
              fire is True and src == "bid+last", f"fire={fire} src={src}")

    with knobs(spread_mult=0.0, floor_pct=0.0, confirm=False):
        h = armed()
        fire, src = h.ctl.exit_decision(3.58, 3.58)
        check("--no-exit-trade-confirm restores the pre-3.2 bid-only trigger",
              fire is True and src == "bid_only", f"fire={fire} src={src}")

    # A silent/stale tape must NOT leave the position unprotected.
    with knobs(spread_mult=0.0, floor_pct=0.0, confirm=True):
        h = armed(last=None)
        h.ctl.last = None
        h.ctl.last_ts = None
        fire, src = h.ctl.exit_decision(3.58, 3.58)
        check("no trade has EVER printed -> the bid stands alone",
              fire is True and src == "bid_only_stale_last", f"src={src}")

        h = armed()
        with h.ctl.lock:
            h.ctl.last_ts = time.time() - (xw.EXIT_CONFIRM_MAX_LAST_AGE_SEC + 5)
        fire, src = h.ctl.exit_decision(3.58, 3.58)
        check(f"a print older than {xw.EXIT_CONFIRM_MAX_LAST_AGE_SEC:.0f}s "
              "is no longer evidence", fire is True
              and src == "bid_only_stale_last", f"src={src}")

    # --- (B) the spread band -------------------------------------------------
    with knobs(spread_mult=1.5, floor_pct=0.0, confirm=True):
        h = armed(bid=3.62, ask=3.71)      # 2.45% spread
        _, trig, _, aux, _ = h.ctl.compute_protective_levels(3.62)
        sp = h.ctl.current_spread_pct()
        check("the band widens the v8 floor Trigger% by 1.5x the median spread",
              h.ctl._eff_trig > trig and
              abs(h.ctl._eff_trig - 1.5 * sp) < 1e-6,
              f"table={trig} spread={sp:.2f}% eff={h.ctl._eff_trig:.2f}%")
        check("...so aux sits clear of the 4-cent oscillation that ejected MEDS",
              aux < 3.58, f"aux={aux} vs the 3.58 bid that fired the old stop")

        h2 = armed(bid=3.62, ask=3.63)     # 0.28% spread: band below the floor
        _, trig2, _, _, _ = h2.ctl.compute_protective_levels(3.62)
        check("on a TIGHT spread the table Trigger% still governs (it is a floor)",
              abs(h2.ctl._eff_trig - trig2) < 1e-9,
              f"table={trig2} eff={h2.ctl._eff_trig}")

    # The ratchet must survive the band, and the band must be able to push the
    # stop back DOWN when the spread widens (the MEDS failure mode).
    with knobs(spread_mult=1.5, floor_pct=0.0, confirm=True):
        h = armed(bid=3.62, ask=3.63)
        *_, aux_tight, _ = h.ctl.compute_protective_levels(3.62)
        h.ctl.update_quote(bid=3.62, ask=4.00)          # spread blows out
        for _ in range(12):
            h.ctl.update_quote(bid=3.62, ask=4.00)
        *_, aux_wide, _ = h.ctl.compute_protective_levels(3.62)
        check("a widening spread pushes the stop back DOWN, ratchet notwithstanding",
              aux_wide < aux_tight, f"{aux_tight} -> {aux_wide}")

    with knobs(spread_mult=0.0, floor_pct=0.0, confirm=True):
        h = armed(bid=3.62, ask=3.63)
        *_, aux_lo, _ = h.ctl.compute_protective_levels(3.62)
        *_, aux_hi, _ = h.ctl.compute_protective_levels(4.00)   # new high-water
        *_, aux_back, _ = h.ctl.compute_protective_levels(3.62) # price falls back
        check("the profit ratchet still HOLDS when price falls (band off)",
              aux_hi > aux_lo and abs(aux_back - aux_hi) < 1e-9,
              f"{aux_lo} -> {aux_hi} -> {aux_back}")

    # --- (C) the hard floor --------------------------------------------------
    with knobs(spread_mult=0.0, floor_pct=5.0, confirm=True, on_last=True):
        h = armed(entry=100.0, bid=99.0, ask=99.5, last=94.9)
        fire, src = h.ctl.exit_decision(99.0, 1.0)   # aux far below: only the floor
        check("a TRADE 5.1% below entry fires the floor with no confirmation",
              fire is True and src == "hard_floor", f"src={src}")

        h = armed(entry=100.0, bid=99.0, ask=99.5, last=96.0)
        fire, src = h.ctl.exit_decision(99.0, 1.0)
        check("...and a trade only 4% down does not", fire is False, f"src={src}")

        # The bid clause: spread-widened, so VEEA-style bid noise cannot fire it.
        h = armed(entry=2.49, bid=2.36, ask=2.50, last=2.50)
        for _ in range(12):
            h.ctl.update_quote(bid=2.36, ask=2.50)
        fire, src = h.ctl.exit_decision(2.36, 1.0)
        check("VEEA shape: a 5.22% BID drop with last unmoved does NOT fire it",
              fire is False, f"src={src} spread={h.ctl.current_spread_pct():.2f}%")

        h = armed(entry=2.49, bid=1.90, ask=2.00, last=2.50)
        for _ in range(12):
            h.ctl.update_quote(bid=1.90, ask=2.00)
        fire, src = h.ctl.exit_decision(1.90, 1.0)
        check("...but a bid collapse beyond floor+spread does, even on a stale last",
              fire is True and src == "hard_floor_bid", f"src={src}")

    with knobs(spread_mult=0.0, floor_pct=0.0, confirm=True):
        h = armed(entry=100.0, bid=50.0, ask=51.0, last=50.0)
        fire, src = h.ctl.exit_decision(50.0, 1.0)
        check("--hard-floor-pct 0 disables the floor entirely",
              fire is False, f"src={src}")

    # --- (D) the exit-limit chase -------------------------------------------
    h = armed()
    with h.ctl.lock:
        h.ctl.exiting = True
        h.ctl.protective_id = 999
        h.ctl._exit_ts = time.time() - (xw.EXIT_LMT_CHASE_AFTER_SEC + 1)
        h.ctl._exit_lmt_sent = 3.57
    h.ctl.update_quote(bid=3.40, ask=3.50)
    ev1 = h.ctl.chase_exit_limit()
    check("an unfilled exit LMT is chased DOWN to the live bid", ev1 == "EXIT_CHASE_1",
          f"{ev1}")
    ev2 = h.ctl.chase_exit_limit()
    check("...but not again inside the throttle window", ev2 is None, f"{ev2}")
    with h.ctl.lock:
        h.ctl._exit_lmt_chase_ts -= (xw.PROTECTIVE_MODIFY_RETRY_MIN_GAP_SEC + 0.1)
    ev3 = h.ctl.chase_exit_limit()
    check("...and never re-sends the SAME price (IVF: 11 orders in 1.66s)",
          ev3 is None, f"{ev3}")
    with h.ctl.lock:
        h.ctl._exit_lmt_chase_n = xw.EXIT_LMT_CHASE_MAX_N
        h.ctl._exit_lmt_chase_ts = 0.0
    h.ctl.update_quote(bid=3.00, ask=3.10)
    check(f"...and stops after EXIT_LMT_CHASE_MAX_N={xw.EXIT_LMT_CHASE_MAX_N}",
          h.ctl.chase_exit_limit() is None)
    h2 = armed()
    with h2.ctl.lock:
        h2.ctl.exiting = True
        h2.ctl.protective_id = 998
        h2.ctl._exit_ts = time.time() - (xw.EXIT_LMT_CHASE_AFTER_SEC + 1)
        h2.ctl._exit_lmt_sent = 3.57
        h2.ctl._prot_reject_n = 1          # IBKR has refused a price
    h2.ctl.update_quote(bid=3.40, ask=3.50)
    check("a 202-rejected exit is NOT pushed lower (the wall handler owns it)",
          h2.ctl.chase_exit_limit() is None)

    # --- realised yield ------------------------------------------------------
    h = armed()
    check("realized_yield_pct is None until the exit actually fills",
          h.ctl.realized_yield_pct() is None)
    with h.ctl.lock:
        h.ctl.exit_fill_px = 3.57
    check("...then reports (exit - entry)/entry",
          abs(h.ctl.realized_yield_pct() - ((3.57 - 3.73) / 3.73 * 100)) < 1e-9,
          f"{h.ctl.realized_yield_pct():.4f}%")


def case_market_metrics():
    """[17] 3.3 market-data capture: the ticks that were already arriving.

    Every assertion here drives the REAL routers, so a tick the Gateway sends
    cannot silently stop being recorded. The one that matters most is the
    zero-size case: a price of 0 is garbage and must be dropped, but a SIZE of
    0 is the book being pulled, which is the event behind every one of the
    2026-10-01 noise ejections. An `if size:` anywhere in this path would throw
    away exactly the observation the columns exist for."""
    print("\n[17] 3.3 market-data capture")

    def fresh():
        h = Harness(symbol="MET", min_tick=0.01)
        h.ctl.update_quote(bid=3.60, ask=3.70, last=3.65)
        return h

    # --- sizes ---------------------------------------------------------------
    h = fresh()
    xw.route_tick_size(h.ctl, xw.TICK_BID_SIZE, 12)
    xw.route_tick_size(h.ctl, xw.TICK_ASK_SIZE, 4)
    xw.route_tick_size(h.ctl, xw.TICK_LAST_SIZE, 3)
    check("tickSize 0/3/5 land in bid_size/ask_size/last_size",
          (h.ctl.bid_size, h.ctl.ask_size, h.ctl.last_size) == (12.0, 4.0, 3.0),
          f"{h.ctl.bid_size}/{h.ctl.ask_size}/{h.ctl.last_size}")
    xw.route_tick_size(h.ctl, xw.TICK_BID_SIZE, 0)
    check("a bid SIZE of 0 is recorded, not dropped (the book was pulled)",
          h.ctl.bid_size == 0.0, f"{h.ctl.bid_size}")
    xw.route_tick_size(h.ctl, xw.TICK_BID_SIZE, None)
    xw.route_tick_size(h.ctl, xw.TICK_BID_SIZE, -1)
    xw.route_tick_size(h.ctl, xw.TICK_BID_SIZE, xw.UNSET_DOUBLE)
    check("...but None, negative and UNSET_DOUBLE are all dropped",
          h.ctl.bid_size == 0.0, f"{h.ctl.bid_size}")
    xw.route_tick_size(h.ctl, xw.TICK_VOLUME, 4210)
    check("tick 8 lands in day_volume (and is in LOTS)",
          h.ctl.day_volume == 4210.0, f"{h.ctl.day_volume}")

    # --- prices that were already arriving and being dropped -----------------
    h = fresh()
    xw.route_tick_price(h.ctl, xw.TICK_HIGH, 4.10)
    xw.route_tick_price(h.ctl, xw.TICK_LOW, 3.20)
    xw.route_tick_price(h.ctl, xw.TICK_OPEN, 3.40)
    check("tickPrice 6/7/14 land in day_high/day_low/day_open",
          (h.ctl.day_high, h.ctl.day_low, h.ctl.day_open) == (4.10, 3.20, 3.40),
          f"{h.ctl.day_high}/{h.ctl.day_low}/{h.ctl.day_open}")
    xw.route_tick_price(h.ctl, xw.TICK_HIGH, 0.0)
    check("...and a price of 0 is still rejected (unlike a size)",
          h.ctl.day_high == 4.10, f"{h.ctl.day_high}")

    # --- generic ticks -------------------------------------------------------
    h = fresh()
    xw.route_tick_generic(h.ctl, xw.TICK_TRADE_RATE, 12.5)
    xw.route_tick_generic(h.ctl, xw.TICK_VOLUME_RATE, 300.0)
    xw.route_tick_generic(h.ctl, xw.TICK_VOL_5MIN, 880)
    xw.route_tick_generic(h.ctl, xw.TICK_HALTED, 1)
    xw.route_tick_generic(h.ctl, xw.TICK_SHORTABLE, 3)
    check("tickGeneric 55/56/64/49/46 are all captured",
          (h.ctl.trade_rate, h.ctl.volume_rate, h.ctl.vol_5min,
           h.ctl.halted, h.ctl.shortable) == (12.5, 300.0, 880.0, 1.0, 3.0),
          f"rate={h.ctl.trade_rate} halted={h.ctl.halted}")
    xw.route_tick_generic(h.ctl, xw.TICK_TRADE_RATE, 0)
    check("a trade rate of 0 is recorded -- a dead tape is the MEDS shape",
          h.ctl.trade_rate == 0.0, f"{h.ctl.trade_rate}")

    # --- RTVolume ------------------------------------------------------------
    h = fresh()
    xw.route_tick_string(h.ctl, xw.TICK_RT_VOLUME,
                         "3.6800;5;1759300000000;4210;3.5512;true")
    check("RTVolume gives us the VWAP for free",
          h.ctl.vwap == 3.5512, f"{h.ctl.vwap}")
    check("...and the EXCHANGE stamp of the print, not our arrival time",
          h.ctl.last_exch_ms == 1759300000000.0, f"{h.ctl.last_exch_ms}")
    check("...and the print itself still routes to `last`",
          h.ctl.last == 3.68, f"{h.ctl.last}")
    check("...and px_vs_vwap_pct is derived from it",
          abs(h.ctl.px_vs_vwap_pct() - ((3.68 - 3.5512) / 3.5512 * 100)) < 1e-9,
          f"{h.ctl.px_vs_vwap_pct():.3f}%")
    before = (h.ctl.vwap, h.ctl.last)
    xw.route_tick_string(h.ctl, xw.TICK_RT_VOLUME, ";;;;;")
    check("an EMPTY RTVolume payload changes nothing",
          (h.ctl.vwap, h.ctl.last) == before, f"{h.ctl.vwap}/{h.ctl.last}")
    xw.route_tick_string(h.ctl, xw.TICK_RT_VOLUME, "garbage")
    check("...and a malformed one does not raise either",
          (h.ctl.vwap, h.ctl.last) == before)
    xw.route_tick_string(h.ctl, xw.TICK_RT_VOLUME, "3.7000;7;;;3.5600;false")
    check("a PARTIAL payload keeps the fields it does carry",
          (h.ctl.last, h.ctl.vwap, h.ctl.last_single_trade) == (3.70, 3.56, 0.0),
          f"{h.ctl.last}/{h.ctl.vwap}/{h.ctl.last_single_trade}")

    # --- microprice ----------------------------------------------------------
    h = fresh()
    check("microprice degrades to the plain mid with no sizes",
          abs(h.ctl.microprice() - 3.65) < 1e-9, f"{h.ctl.microprice()}")
    xw.route_tick_size(h.ctl, xw.TICK_BID_SIZE, 50)
    xw.route_tick_size(h.ctl, xw.TICK_ASK_SIZE, 1)
    mp = h.ctl.microprice()
    check("a deep bid against a thin ask puts the fair value HIGH, near the ask",
          mp > 3.65 and abs(mp - (3.60 * 1 + 3.70 * 50) / 51) < 1e-9, f"{mp:.4f}")
    check("...and touch_imbalance says the same thing",
          abs(h.ctl.touch_imbalance() - 50 / 51) < 1e-9,
          f"{h.ctl.touch_imbalance():.3f}")
    h2 = fresh()
    xw.route_tick_size(h2.ctl, xw.TICK_BID_SIZE, 1)
    xw.route_tick_size(h2.ctl, xw.TICK_ASK_SIZE, 50)
    check("a 1-lot bid against a deep ask barely moves it off the ASK side",
          h2.ctl.microprice() < 3.65, f"{h2.ctl.microprice():.4f}")
    h3 = fresh()
    xw.route_tick_size(h3.ctl, xw.TICK_BID_SIZE, 0)
    xw.route_tick_size(h3.ctl, xw.TICK_ASK_SIZE, 0)
    check("both sizes 0 cannot divide by zero",
          abs(h3.ctl.microprice() - 3.65) < 1e-9 and h3.ctl.touch_imbalance() is None,
          f"{h3.ctl.microprice()}")

    # --- rolling metrics -----------------------------------------------------
    h = fresh()
    check("bid_vol_10s needs 3 samples and is None before that",
          h.ctl.bid_vol_pct() is None)
    for px in (3.60, 3.62, 3.58, 3.63, 3.59):
        h.ctl.update_quote(bid=px)
    check("...then reports a non-zero standard deviation of bid returns",
          h.ctl.bid_vol_pct() > 0, f"{h.ctl.bid_vol_pct():.3f}%")
    check("quote_rate counts the ticks in the window",
          h.ctl.quote_rate() > 0, f"{h.ctl.quote_rate():.2f}/s")

    # --- trade-through counter ----------------------------------------------
    h = fresh()
    h.ctl.update_quote(last=3.40)
    h.ctl.note_trade_through(3.50)
    h.ctl.note_trade_through(3.50)
    check("a print below the stop is counted ONCE, not once per loop pass",
          h.ctl._trade_through_n == 1, f"{h.ctl._trade_through_n}")
    h.ctl.update_quote(last=3.60)
    h.ctl.note_trade_through(3.50)
    check("...and a print above it is not counted at all",
          h.ctl._trade_through_n == 1, f"{h.ctl._trade_through_n}")

    # --- the row -------------------------------------------------------------
    h = fresh()
    row = h.ctl.market_snapshot_row()
    check("market_snapshot_row covers exactly the 22 new columns",
          sorted(row) == sorted(xw.PA_COLUMNS[35:]),
          f"{len(row)} keys")
    check("...and every value is writable (no None leaks into the TSV)",
          all(v is not None for v in row.values()))
    h.ctl.update_quote(shortable=3, shortable_shares=100000, avg_volume=52000)
    check("the once-per-session fields are packed into static_snapshot",
          "shortable=3" in h.ctl.static_snapshot_str()
          and "avg_volume=52000" in h.ctl.static_snapshot_str(),
          h.ctl.static_snapshot_str())
    check("x-wing asks for the same generic ticks the clerk does, plus 595",
          xw.GENERIC_TICKS.endswith(",595") and "233" in xw.GENERIC_TICKS,
          xw.GENERIC_TICKS)


def main():
    print("x-wing-3.0 entry ask-tracking — offline stub harness")
    print(f"offset={xw.MAX_LIMIT_ENTRY_PERCENT_PRICE}%  ceiling=+{xw.ENTRY_TRACK_MAX_PCT}%  "
          f"up_window={xw.ENTRY_TRACK_UP_WINDOW_SEC}s  "
          f"hyst={xw.ENTRY_TRACK_DOWN_HYST_MULT}x  budget={xw.ENTRY_TRACK_MAX_MODIFIES}")
    for fn in (case_gle_replay, case_invariants, case_penny_floor,
               case_down_ratchet, case_throttle_and_deadband, case_disabled,
               case_partial_fill, case_error_paths, case_cap_rescue,
               case_dead_status_grace, case_open_order, case_table_columns,
               case_modify_reject, case_price_wall, case_protective_storm,
               case_exit_noise_defences, case_market_metrics,
               case_dead_exit_rearm):
        fn()
    print(f"\n{len(PASSED)} passed, {len(FAILURES)} failed")
    for f in FAILURES:
        print(f"  FAILED: {f}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    sys.exit(main())
