#!/usr/bin/env python3
"""Mine every IBKR price-cap event out of the clerk logs and calibrate the wall.

Why this exists (2026-08-27, after YAAS and CJMB both died to a 202 in one day):

IBKR refuses a limit that is "too far through the market" against a REFERENCE
PRICE OF ITS OWN, which we cannot read directly and which lags the tape by
seconds. Two shapes:

  * 2161 -> the order is LIVE, worked at a capped price (soft).
  * 202  -> the order is DEAD, refused outright (hard).

x-wing-3.1 bounds every entry price by an estimate of that wall. The bound has
three sources, strongest first: an observed 202 wall (exact), an observed 2161
cap times ENTRY_WALL_OVER_CAP_MULT, and — dark until this script says otherwise
— an estimate off our OWN reference. That last one is the problem this script
exists to solve: our reference is NOT IBKR's. Measured on the three known walls,
IBKR's sits at or BELOW our last by 0-10% (YAAS 2.50/2.50, CJMB 2.01/2.16,
EHGO 1.75/1.95), so a band fitted to three points either fails to bind or
throttles every normal gapper.

⚠ Two traps this miner deliberately avoids, both already paid for elsewhere in
this directory:
  * it globs mole CSVs INCLUDING the operator's ``_pos_``/``_neg_``/``_neu_``/
    ``_neutral_``/``_medium_`` prefixes (the sweep_thresholds.py mistake), and
  * it does NOT require a TRADE row (the replay_mole mistake) — EHGO is a
    pre-trade-only recording and has none.

Reports:
  A  band      every 202: wall, IBKR's ref, wall/ref, side, session
  B  capmult   every 2161: what wall/cap multiplier is consistent with it
  C  cost      per candidate estimator: 202s prevented vs normal entries made
               less aggressive, and — the veto column — entries priced BELOW
               THE BID, which must be zero for anything shippable
  D  fixtures  --emit-fixtures dumps the replay tuples for the verifier

Usage:
    python calibrate_price_wall.py                 # all reports
    python calibrate_price_wall.py --report band
    python calibrate_price_wall.py --self-check    # regression on known counts
    python calibrate_price_wall.py --emit-fixtures
"""
import argparse
import collections
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
XWING_DIR = os.path.dirname(HERE)
CLERK_LOGS = os.path.join(XWING_DIR, "clerk_logs")
MOLE_OUT = os.path.join(XWING_DIR, "mole-outputs")
XWING_TABLES = os.path.join(XWING_DIR, "xwing_tables")

# --------------------------------------------------------------------------- #
# Log grammar. The session key is the LOGGER NAME (xwing.SYM.CLIENTID) plus the
# orderId: reqId alone collides across concurrent sessions on one clerk.
# --------------------------------------------------------------------------- #
RE_TS = re.compile(r"^(\d\d:\d\d:\d\d\.\d+)")
RE_XWING = re.compile(r"xwing\.([A-Z.]+)\.(\d+):")
RE_ARMED = re.compile(r"\[([A-Z.]+)\] trio armed on client (\d+).*?lastClose=([\d.]+)")
RE_ENTRY_PRICE = re.compile(
    r"Entry price: (\w+)=([\d.]+) \* \(1\+([\d.]+)%\) -> ([\d.]+)"
    r"(?: \[STALE[^\]]*\])? \[ceiling ([\d.]+)")
RE_ENTRY_PLACE = re.compile(r"ENTRY: BUY LMT (\d+) ([A-Z.]+) @ ([\d.]+) \(orderId=(\d+)\)")
# ⚠ BOTH carry the clientId and BOTH need it. Matching a cap/wall to a session
# by orderId alone silently cross-wires runs — orderIds restart per client, so
# EHGO's 2.093 wall on client 1003 order 37 was attributed to AIXC, which held
# order 37 on a different client the same day.
RE_CAP = re.compile(
    r"client (\d+) reqId=(\d+) code=2161: (BUY|SELL) (\d+) ([A-Z.]+).*?"
    r"cap \(or limit\) the price of your Limit Order to ([\d.]+)")
RE_WALL = re.compile(
    r"client (\d+) reqId=(\d+) code=202:.*?more aggressive than ([\d.]+)\..*?"
    r"market price of ([\d.]+)\.")
RE_CHASE = re.compile(r"(CHASE_\w+|CAP_RESCUE) id=(\d+) ([A-Z.]+) ([\d.]+) -> ([\d.]+) \(ask ([\d.]+)")
RE_FILLED = re.compile(r"Entry FILLED: (\d+) sh, original avg fill ([\d.]+)")
RE_ABORT = re.compile(r"Entry order (\d+) (\w+) with no fill")

MOLE_TICKS = {"1": "bid", "2": "ask", "4": "last", "9": "close", "37": "mark"}


def _fmt(v, nd=4):
    return "" if v is None else f"{v:.{nd}f}"


# --------------------------------------------------------------------------- #
# Mining
# --------------------------------------------------------------------------- #
class Session:
    """One armed x-wing entry: its pricing, its caps, its walls, its outcome."""

    __slots__ = ("day", "symbol", "client", "last_close", "src", "ref", "pad",
                 "limit", "ceiling", "order_id", "shares", "caps", "walls",
                 "chases", "filled_avg", "abort", "t_place", "ticks")

    def __init__(self, day, symbol, client):
        self.day, self.symbol, self.client = day, symbol, client
        self.last_close = self.src = self.ref = self.pad = None
        self.limit = self.ceiling = self.order_id = self.shares = None
        self.caps, self.walls, self.chases = [], [], []
        self.filled_avg = self.abort = self.t_place = None
        self.ticks = {}

    @property
    def key(self):
        return (self.day, self.symbol, self.client, self.order_id)

    @property
    def capped(self):
        return bool(self.caps)

    @property
    def walled(self):
        return bool(self.walls)


def _day_of(path):
    base = os.path.basename(path)
    return base.rsplit(".", 1)[-1] if base != "clerk.log" else "current"


def mine_clerk_logs(paths=None):
    """(sessions, wall_events) out of every clerk log."""
    paths = paths or sorted(glob.glob(os.path.join(CLERK_LOGS, "clerk.log*")))
    sessions, wall_events = {}, []
    for path in paths:
        day = _day_of(path)
        # symbol/client -> the session currently arming on that client
        by_client, live = {}, {}
        with open(path, errors="ignore") as fh:
            for line in fh:
                ts = RE_TS.match(line)
                ts = ts.group(1) if ts else ""

                m = RE_ARMED.search(line)
                if m:
                    sym, cid, close = m.group(1), m.group(2), float(m.group(3))
                    s = Session(day, sym, cid)
                    s.last_close = close
                    by_client[cid] = s
                    continue

                mx = RE_XWING.search(line)
                cid = mx.group(2) if mx else None
                s = by_client.get(cid)

                m = RE_ENTRY_PRICE.search(line)
                if m and s is not None:
                    s.src, s.ref = m.group(1), float(m.group(2))
                    s.pad, s.limit = float(m.group(3)), float(m.group(4))
                    s.ceiling = float(m.group(5))
                    continue

                m = RE_ENTRY_PLACE.search(line)
                if m and s is not None:
                    s.shares = int(m.group(1))
                    s.limit = float(m.group(3))
                    s.order_id = m.group(4)
                    s.t_place = ts
                    sessions[s.key] = s
                    live[(cid, s.order_id)] = s
                    continue

                m = RE_CHASE.search(line)
                if m:
                    t = live.get((cid, m.group(2)))
                    if t is not None:
                        t.chases.append((ts, m.group(1), float(m.group(4)),
                                         float(m.group(5)), float(m.group(6))))
                    continue

                m = RE_FILLED.search(line)
                if m and s is not None:
                    s.filled_avg = float(m.group(2))
                    continue

                m = RE_ABORT.search(line)
                if m:
                    t = live.get((cid, m.group(1)))
                    if t is not None:
                        t.abort = m.group(2)
                    continue

                m = RE_CAP.search(line)
                if m:
                    ccid, oid, side = m.group(1), m.group(2), m.group(3)
                    cap = float(m.group(6))
                    t = live.get((ccid, oid))
                    if t is not None:
                        t.caps.append((ts, cap, side))
                    continue

                m = RE_WALL.search(line)
                if m:
                    ccid, oid = m.group(1), m.group(2)
                    wall, ref = float(m.group(3)), float(m.group(4))
                    tgt = live.get((ccid, oid))
                    side = "BUY" if wall > ref else "SELL"
                    if tgt is not None:
                        tgt.walls.append((ts, wall, ref, side))
                    wall_events.append(dict(
                        day=day, ts=ts, order_id=oid, wall=wall, ref=ref,
                        side=side, ratio=wall / ref if ref else None,
                        symbol=tgt.symbol if tgt else "?",
                        entry_limit=tgt.limit if tgt else None,
                        ceiling=tgt.ceiling if tgt else None,
                        capped_to=tgt.caps[-1][1] if (tgt and tgt.caps) else None,
                    ))
    return sessions, wall_events


def mine_mole_ticks(session):
    """Last bid/ask/last/close/mark seen BEFORE the fire, from the mole CSV.

    ⚠ Globs the label prefixes and does NOT require a TRADE row."""
    pat = os.path.join(MOLE_OUT, f"*trade-mole-table_{session.symbol}_*.csv")
    best = None
    for path in sorted(glob.glob(pat)):
        if session.day != "current" and session.day not in os.path.basename(path):
            continue
        best = path
    if not best:
        return {}
    out = {}
    try:
        with open(best, newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("event_type") != "TICK_PRICE":
                    continue
                tt = (row.get("tick_type") or "").split(".")[0]
                name = MOLE_TICKS.get(tt)
                if not name:
                    continue
                try:
                    out[name] = float(row.get("price") or 0) or out.get(name)
                except ValueError:
                    pass
    except OSError:
        return {}
    return out


# --------------------------------------------------------------------------- #
# Reports
# --------------------------------------------------------------------------- #
def report_band(wall_events, min_n):
    print("\n=== (A) BAND: wall / IBKR reference, per 202 ===")
    if not wall_events:
        print("  no 202 wall events found")
        return
    print(f"  {'day':12}{'time':14}{'sym':7}{'side':6}{'wall':>12}{'ref':>10}{'ratio':>10}")
    for e in sorted(wall_events, key=lambda x: (x["day"], x["ts"])):
        print(f"  {e['day']:12}{e['ts']:14}{e['symbol']:7}{e['side']:6}"
              f"{e['wall']:>12.7g}{e['ref']:>10.5g}{e['ratio']:>10.6f}")
    by_side = collections.defaultdict(list)
    for e in wall_events:
        by_side[e["side"]].append(e["ratio"])
    print("\n  side   n   distinct ratios")
    for side, ratios in sorted(by_side.items()):
        uniq = sorted({round(r, 6) for r in ratios})
        n_ev = len(ratios)
        print(f"  {side:6}{n_ev:<4}{uniq}")
        if n_ev < min_n:
            print(f"         ⚠ n={n_ev} < --min-n {min_n}: DO NOT fit a constant "
                  f"to this yet")


def report_capmult(sessions, wall_events):
    print("\n=== (B) CAP MULTIPLIER: what wall/cap is each 2161 consistent with? ===")
    direct = []
    for s in sessions.values():
        if s.capped and s.walled:
            cap = s.caps[-1][1]
            wall = s.walls[-1][1]
            direct.append((s.day, s.symbol, cap, wall, wall / cap))
    print("  DIRECT (a cap AND a wall in the same run):")
    if direct:
        print(f"    {'day':12}{'sym':7}{'cap':>9}{'wall':>12}{'wall/cap':>10}")
        for d, sym, cap, wall, r in sorted(direct):
            print(f"    {d:12}{sym:7}{cap:>9.4f}{wall:>12.7g}{r:>10.5f}")
        hi = min(r for *_, r in direct)
        print(f"    => UPPER bound on the multiplier: < {hi:.5f}")
    else:
        print("    none")

    # Lower bound: a capped run whose chase was ACCEPTED proves wall >= that price.
    print("\n  LOWER bounds (a capped run whose chase was NOT rejected):")
    lows = []
    for s in sessions.values():
        if not s.capped or s.walled or not s.chases:
            continue
        cap = s.caps[-1][1]
        top = max(c[3] for c in s.chases)
        lows.append((s.day, s.symbol, cap, top, top / cap,
                     s.filled_avg))
    if lows:
        print(f"    {'day':12}{'sym':7}{'cap':>9}{'max chase':>11}{'>= mult':>10}{'fill':>9}")
        for d, sym, cap, top, r, fill in sorted(lows):
            print(f"    {d:12}{sym:7}{cap:>9.4f}{top:>11.4f}{r:>10.5f}"
                  f"{('' if fill is None else f'{fill:.4f}'):>9}")
        lo_all = max(r for *_, r, _ in lows)
        filled = [x for x in lows if x[5] is not None]
        lo_fill = max((r for *_, r, f in lows if f is not None), default=None)
        print(f"    => LOWER bound over ALL capped runs:     >= {lo_all:.5f}")
        if lo_fill is not None:
            print(f"    => LOWER bound over runs that FILLED:    >= {lo_fill:.5f}"
                  f"   ({len(filled)} of {len(lows)} filled)")
            print("       ⚠ Only the FILLED bound is economically binding. A capped")
            print("       run that never filled proves nothing was lost by pricing")
            print("       it lower — and only 3 of 10 capped entries ever filled.")
        # ⚠ The bound that ACTUALLY matters. The two above ask "could we still
        # have SENT that limit?", but a limit is not a fill: REBN chased to 1.75
        # and filled at 1.72, CAPS filled AT its 0.3254 cap under a 0.3383 limit.
        # The real constraint is only that the bound still REACHES the price the
        # trade executed at, which is a much weaker — and satisfiable — demand.
        fills = [(d, sym, cap, f, f / cap)
                 for d, sym, cap, top, r, f in lows if f is not None]
        if fills:
            print("\n    FILL-REACHING bound (bound must still reach the executed "
                  "price):")
            print(f"      {'day':12}{'sym':7}{'cap':>9}{'filled at':>11}{'>= mult':>10}")
            for d, sym, cap, f, r in sorted(fills):
                print(f"      {d:12}{sym:7}{cap:>9.4f}{f:>11.4f}{r:>10.5f}")
            print(f"      => LOWER bound that preserves every FILL: "
                  f">= {max(r for *_, r in fills):.5f}")
    else:
        print("    none")

    if direct and lows:
        lo_all = max(r for *_, r, _ in lows)
        lo_fill = max((r for *_, r, f in lows if f is not None), default=None)
        hi = min(r for *_, r in direct)
        print(f"\n  WINDOW over ALL capped runs:  [{lo_all:.5f}, {hi:.5f})  "
              f"{'CONSISTENT' if lo_all < hi else '⚠ EMPTY'}")
        if lo_fill is not None:
            ok = lo_fill < hi
            print(f"  WINDOW over FILLED runs only: [{lo_fill:.5f}, {hi:.5f})  "
                  f"{'CONSISTENT' if ok else '⚠ EMPTY'}")
        fills = [f / cap for *_, cap, top, r, f in
                 [(d, s2, c, t, r2, f2) for d, s2, c, t, r2, f2 in lows]
                 if f is not None]
        if fills:
            lo_x = max(fills)
            ok = lo_x < hi
            print(f"  WINDOW preserving every FILL: [{lo_x:.5f}, {hi:.5f})  "
                  f"{'CONSISTENT' if ok else '⚠ EMPTY'}   <-- THE ONE THAT MATTERS")
            cur = 1.06
            print(f"  shipping ENTRY_WALL_OVER_CAP_MULT={cur}: "
                  f"{'INSIDE' if lo_x <= cur < hi else 'OUTSIDE'}")
        if lo_all >= hi:
            print("\n  ⚠ The all-runs window is EMPTY, and that is a REAL property of")
            print("    the data, not a mining bug: a cap and a later chase are priced")
            print("    off DIFFERENT references because IBKR's moves between them. So")
            print("    no single multiplier is universally correct. Err LOW — too low")
            print("    leaves the order ALIVE at a worse limit, too high gets it")
            print("    CANCELLED outright.")


def report_cost(sessions, estimators, mults):
    print("\n=== (C) COST: what would an ESTIMATE-based clamp have done? ===")
    print("  'below bid' MUST be 0 for a shippable candidate — that column is the")
    print("  MAX_CAP_ENTRY_PERCENT failure 3.0 deleted (7 of 317 entries under")
    print("  the market, GLE -24.8% .. ICCM -70.4%).\n")
    rows = []
    for est_name in estimators:
        for mult in mults:
            prevented = missed = softened = below_bid = 0
            for s in sessions.values():
                t = s.ticks
                if est_name == "last":
                    ref = t.get("last")
                elif est_name == "mark":
                    ref = t.get("mark")
                elif est_name == "min_last_mark":
                    c = [x for x in (t.get("last"), t.get("mark")) if x]
                    ref = min(c) if c else None
                elif est_name == "min_last_mark_close":
                    c = [x for x in (t.get("last"), t.get("mark"),
                                     t.get("close") or s.last_close) if x]
                    ref = min(c) if c else None
                else:
                    ref = None
                if not ref or not s.limit:
                    continue
                bound = ref * mult
                if s.walled:
                    (prevented if bound < s.walls[-1][1] else missed)
                    if bound < s.walls[-1][1]:
                        prevented += 1
                    else:
                        missed += 1
                elif bound < s.limit:
                    softened += 1
                    bid = t.get("bid")
                    if bid and bound < bid:
                        below_bid += 1
            rows.append((est_name, mult, prevented, missed, softened, below_bid))
    print(f"  {'estimator':22}{'mult':>7}{'prevented':>11}{'missed':>8}"
          f"{'softened':>10}{'below bid':>11}")
    for r in rows:
        flag = "  <-- VETO" if r[5] else ""
        print(f"  {r[0]:22}{r[1]:>7.3f}{r[2]:>11}{r[3]:>8}{r[4]:>10}{r[5]:>11}{flag}")


def emit_fixtures(sessions):
    print("\n=== (D) FIXTURES for verify_xwing3_tracking.py ===")
    for s in sorted(sessions.values(), key=lambda x: (x.day, x.symbol)):
        if not (s.capped or s.walled):
            continue
        print(f"  # {s.symbol} {s.day} {s.t_place}  last_close={s.last_close}")
        print(f"  #   {s.src}={s.ref} pad={s.pad}% -> limit={s.limit} "
              f"ceiling={s.ceiling}")
        for ts, cap, side in s.caps:
            print(f"  #   2161 @{ts} cap={cap}")
        for ts, wall, ref, side in s.walls:
            print(f"  #   202  @{ts} wall={wall} ref={ref} ratio={wall/ref:.6f}")
        for c in s.chases:
            print(f"  #   {c[1]} @{c[0]} {c[2]} -> {c[3]} (ask {c[4]})")
        print(f"  #   outcome: "
              f"{'FILLED @%.4f' % s.filled_avg if s.filled_avg else (s.abort or '?')}")
        print(f"  ({s.symbol!r}, {s.last_close}, {s.ref}, {s.limit}, {s.ceiling}, "
              f"{[c[1] for c in s.caps]}, {[(w[1], w[2]) for w in s.walls]}),")


def self_check(sessions, wall_events):
    """Regression on the counts this file was written against. If these move,
    the regexes have drifted, not the world."""
    print("\n=== SELF-CHECK ===")
    fails = 0

    def ck(name, got, want):
        nonlocal fails
        ok = got == want
        fails += not ok
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {got} (want {want})")

    buy = [e for e in wall_events if e["side"] == "BUY"]
    sell = [e for e in wall_events if e["side"] == "SELL"]
    ck("BUY 202 walls", len(buy), 3)
    ck("BUY band is exactly 1.196", sorted({round(e["ratio"], 6) for e in buy}),
       [1.196])
    ck("SELL band is exactly 0.902", sorted({round(e["ratio"], 6) for e in sell}),
       [0.902])
    caps = sum(len(s.caps) for s in sessions.values())
    ck("2161 caps mined (>0)", caps > 0, True)
    ck("entries mined (>0)", len(sessions) > 0, True)
    known = {("YAAS", 2.99, 2.5), ("CJMB", 2.40396, 2.01), ("EHGO", 2.093, 1.75)}
    got = {(e["symbol"], e["wall"], e["ref"]) for e in buy}
    ck("the three known BUY walls are all present", got, known)
    print(f"\n  {'OK' if not fails else str(fails) + ' FAILED'}")
    return fails


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", default="all",
                    choices=["all", "band", "capmult", "cost"])
    ap.add_argument("--min-n", type=int, default=8,
                    help="refuse to recommend a band below this many observations")
    ap.add_argument("--emit-fixtures", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--estimators", default="last,mark,min_last_mark,min_last_mark_close")
    ap.add_argument("--mults", default="1.06,1.10,1.15,1.196")
    args = ap.parse_args()

    sessions, wall_events = mine_clerk_logs()
    for s in sessions.values():
        s.ticks = mine_mole_ticks(s)
    print(f"mined {len(sessions)} entries, "
          f"{sum(len(s.caps) for s in sessions.values())} 2161 caps, "
          f"{len(wall_events)} 202 walls "
          f"from {CLERK_LOGS}")

    if args.self_check:
        return self_check(sessions, wall_events)
    if args.report in ("all", "band"):
        report_band(wall_events, args.min_n)
    if args.report in ("all", "capmult"):
        report_capmult(sessions, wall_events)
    if args.report in ("all", "cost"):
        report_cost(sessions,
                    [e.strip() for e in args.estimators.split(",") if e.strip()],
                    [float(m) for m in args.mults.split(",") if m.strip()])
    if args.emit_fixtures:
        emit_fixtures(sessions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
