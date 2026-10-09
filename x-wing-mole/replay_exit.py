#!/usr/bin/env python
"""
replay_exit.py — replay a recorded xwing_tables/*.tsv through the REAL exit path.

No ibapi, no Gateway, no orders. Each row's bid/ask/last is pushed into a real
XWingController via update_quote(), then compute_protective_levels() and
exit_decision() are called exactly as control_loop() calls them, so this
exercises the shipped code rather than a re-implementation of it.

Why it exists: the 2026-10-01 early ejections (MEDS, VEEA, HXHX) could not be
reproduced offline, because the only exit harness (verify_xwing3_tracking.py)
covers the ENTRY tracker. This replays the exit.

What it reports per file:
    the first row where the exit fires, which mechanism fired it
    (bid+last / hard_floor / bid_only_stale_last), the yield at that point, and
    how many passes were held by the trade-confirmation defence.

Run:
    /home/tom/venv/bin/python analyze_trade_mole/replay_exit.py            # the 3 labelled files
    /home/tom/venv/bin/python analyze_trade_mole/replay_exit.py --all      # every file this week
    /home/tom/venv/bin/python analyze_trade_mole/replay_exit.py --self-check
"""

import argparse
import csv
import importlib.util
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
# This harness was written to live in analyze_trade_mole/ (which is how CLAUDE.md
# documents it) and resolved x-wing-3.0.py as HERE.parent. It now sits at the
# x-wing-mole root, where that pointed one directory too high and every run died
# on FileNotFoundError at import. Locate the module instead of assuming a depth,
# so the file works from either place.
XWING_DIR = HERE if (HERE / "x-wing-3.0.py").is_file() else HERE.parent
TABLES = XWING_DIR / "xwing_tables"


def _load(mod_name, filename):
    spec = importlib.util.spec_from_file_location(mod_name, XWING_DIR / filename)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


xw = _load("xwing3_under_test", "x-wing-3.0.py")


class FakeClock:
    """Row-driven clock.

    The controller stamps every spread sample and quote arrival with
    time.time(), and current_spread_pct()/last_age() measure against it. Replayed
    rows arrive within microseconds of each other in wall-clock terms, so without
    this the 10 s spread window never trims: samples from the whole recording pile
    up and the median spread comes out far wider than the quote actually was,
    which silently masks which defence is doing the work. Same reason
    replay_mole.py advances a fake clock to each row.
    """

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def set_iso(self, ts):
        # The tables stamp timespec="seconds", so sub-second ordering within a
        # second is lost; rows sharing a second share a clock value, which is
        # the best the recording supports.
        self.t = datetime.fromisoformat(ts).timestamp()


class FakeClient:
    def placeOrder(self, *a, **k):
        pass

    def cancelOrder(self, *a, **k):
        pass


class NullWriter:
    def write(self, row):
        pass

    def close(self):
        pass


def _f(x):
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _snap(ref_snapshot):
    """The recorded `last` lives inside ref_snapshot on pre-3.2 files."""
    out = {}
    for kv in (ref_snapshot or "").split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def build_ctl(symbol, table_path, log=None):
    cfg = xw.build_config(symbol, None, limits_table=str(table_path))
    ctl = xw.XWingController(cfg, xw.LimitsTable(str(table_path)), NullWriter(),
                             log=log or xw.logger, driven=True)
    ctl.order_client = FakeClient()
    ctl.contract = object()
    ctl.min_tick = 0.0001
    return ctl


def replay(path, limits_table, verbose=False):
    rows = list(csv.DictReader(open(path), delimiter="\t"))
    symbol = rows[0]["symbol"] if rows else "?"
    ctl = build_ctl(symbol, limits_table)

    entry = None
    fired = None
    held = 0
    t0 = None
    clock = FakeClock()
    real_time = xw.time.time
    xw.time.time = clock
    try:
        return _replay_rows(ctl, rows, path, symbol, clock, verbose)
    finally:
        xw.time.time = real_time


def _replay_rows(ctl, rows, path, symbol, clock, verbose):
    entry = None
    fired = None
    held = 0
    t0 = None
    for r in rows:
        clock.set_iso(r["timestamp"])
        snap = _snap(r.get("ref_snapshot"))
        bid = _f(r.get("bid"))
        ask = _f(r.get("ask"))
        # 3.2 writes `last` as a column; older files only carry it in the snapshot
        last = _f(r.get("last")) or _f(snap.get("last"))
        if bid or ask or last:
            ctl.update_quote(bid=bid, ask=ask, last=last)

        fill = _f(r.get("orig_avg_fill"))
        if fill and entry is None:
            entry = fill
            with ctl.lock:
                ctl.original_avg_fill = fill
                ctl.position = 1.0
            t0 = r["timestamp"]
        if entry is None:
            continue

        exit_ref = ctl.current_exit_ref()
        if exit_ref is None:
            continue
        _, _, _, aux, lmt = ctl.compute_protective_levels(exit_ref)
        fire, src = ctl.exit_decision(exit_ref, aux)
        y = (exit_ref - entry) / entry * 100.0
        if verbose:
            print(f"    {r['timestamp']} bid={exit_ref:.4f} last={last} "
                  f"aux={aux:.4f} spread={ctl._spread_pct_used} "
                  f"eff_trig={ctl._eff_trig} -> {src}")
        if not fire and src == "deferred":
            held += 1
        if fire and fired is None:
            dt = ""
            if t0:
                dt = f"{(datetime.fromisoformat(r['timestamp']) - datetime.fromisoformat(t0)).total_seconds():.0f}s"
            fired = dict(ts=r["timestamp"], src=src, y=y, aux=aux, lmt=lmt,
                         bid=exit_ref, last=last, held_before=held, dt=dt)
    return dict(symbol=symbol, path=path, entry=entry, fired=fired, held=held,
                rows=len(rows))


# --------------------------------------------------------------------------- #
# Labelled corpus (2026-10-02)
#
# Same convention trade-mole's corpus already uses: a label is the leading
# underscore token of the file name, read as basename.split("_")[1]. See
# eval_rules.py:93-103 -- the operator's existing `_early_xwing-table_HXHX_...`
# naming already matches it with no change.
# --------------------------------------------------------------------------- #
LABEL_ALIASES = {
    # the exit came too soon: the price recovered after we were out
    "early": "early", "fast": "early",
    # the exit did its job
    "good": "good", "ok": "good",
    # the loss was larger than the operator is willing to carry
    "hloss": "hloss", "loss": "hloss", "bigloss": "hloss",
    # held too long and gave the profit back -- v7's failure, the OPPOSITE
    # error, and the corpus needs it or calibration overfits to "never eject"
    "late": "late", "giveback": "late",
    # the exit limit never filled (the LESL shape)
    "nofill": "nofill",
}


def labelled_tables():
    """(label, path) for every labelled table, newest last."""
    out = []
    for p in sorted(TABLES.glob("_*xwing-table_*.tsv")):
        parts = p.name.split("_")
        if len(parts) < 2:
            continue
        lab = LABEL_ALIASES.get(parts[1].lower())
        if lab:
            out.append((lab, p))
    return out


def post_exit_rows(path):
    """The post-exit tail for a table, or [] if it was recorded before the tail
    existed. The label prefix is stripped when looking: the operator renames the
    main table to label it, the tail file keeps the original stem."""
    stem = path.stem
    bare = stem
    parts = stem.split("_")
    if parts and parts[0] == "":
        bare = "_".join(parts[2:])           # "_early_xwing-table_X_date" -> "xwing-table_X_date"
    for cand in (stem, bare):
        f = TABLES / "postexit" / f"{cand}-post.tsv"
        if f.exists():
            return list(csv.DictReader(open(f), delimiter="\t"))
    return []


def score_labels(limits_table, verbose=False):
    """Score the exit rules against the labelled corpus.

    Reported as ONE weighted cost in the shape eval_rules.py already uses: a
    hard failure counts 1.0, a soft one 0.4.

    ⚠ An `_early_` or `_late_` file with NO post-exit tail is UNSCORABLE and is
    reported as such, never silently scored. Both labels are claims about what
    the price did AFTER the exit, and a recording that stops at the exit cannot
    support or refute them. This matters: replay_mole.py once required a TRADE
    row and silently skipped 26 of 90 files, which invalidated every precision
    number before 2026-08-14. Do not repeat it."""
    cases = labelled_tables()
    if not cases:
        print("no labelled tables found in", TABLES)
        return 1
    hard = soft = unscorable = 0
    print(f"{'label':8} {'file':40} {'verdict':54}")
    for lab, p in sorted(cases):
        res = replay(p, limits_table, verbose=verbose)
        f = res["fired"]
        post = post_exit_rows(p)
        entry = res["entry"]
        verdict = ""
        weight = 0.0

        if entry is None:
            # No entry fill ever happened (NTRP 2026-07-15 ends in
            # entry_timeout), so there was no position and no exit to judge.
            # An exit rule cannot be scored on a trade that never opened.
            unscorable += 1
            verdict = "UNSCORABLE: no entry fill in this recording"
        elif lab in ("early", "late") and not post:
            unscorable += 1
            verdict = "UNSCORABLE: no post-exit tail in this recording"
        elif lab == "early":
            # the claim: the price recovered after we were ejected
            best = max((_f(r.get("bid")) or 0.0) for r in post)
            rec = ((best - entry) / entry * 100.0) if entry else None
            if f is not None and f["src"] in ("bid_only", "bid_only_stale_last"):
                hard += 1
                weight = 1.0
                verdict = f"FAIL still ejected on the bid alone ({f['y']:+.2f}%)"
            elif f is None:
                verdict = f"ok   held; bid later reached {rec:+.2f}%"
            else:
                verdict = f"ok   fires as {f['src']} ({f['y']:+.2f}%); bid later {rec:+.2f}%"
        elif lab == "hloss":
            # the claim: this loss was bigger than tolerable. Two ways to fail:
            # not exiting at all, and exiting materially LOWER than the shipped
            # run already managed. The second is what gives the hard floor
            # something to be measured against in --leave-one-out.
            was = recorded_exit(p)
            if f is None:
                hard += 1
                weight = 1.0
                verdict = "FAIL never exits -- an intolerable loss must be capped"
            elif was and was["y"] is not None and f["y"] < was["y"] - 2.0:
                soft += 1
                weight = 0.4
                verdict = (f"soft exits {f['y'] - was['y']:+.2f} pts lower than "
                           f"the shipped run ({was['y']:+.2f}% -> {f['y']:+.2f}%)")
            else:
                verdict = f"ok   exits as {f['src']} at {f['y']:+.2f}%"
        elif lab == "good":
            was = recorded_exit(p)
            if f is None:
                soft += 1
                weight = 0.4
                verdict = "soft a good exit no longer fires at all"
            elif was and was["y"] is not None and f["y"] < was["y"] - 2.0:
                soft += 1
                weight = 0.4
                verdict = (f"soft a good exit moved {f['y'] - was['y']:+.2f} pts "
                           f"worse ({was['y']:+.2f}% -> {f['y']:+.2f}%)")
            else:
                verdict = f"ok   {f['src']} at {f['y']:+.2f}%"
        elif lab == "late":
            was = recorded_exit(p)
            if f is None:
                soft += 1
                weight = 0.4
                verdict = "soft still never exits (the give-back is unaddressed)"
            elif was and was["y"] is not None and f["y"] <= was["y"]:
                soft += 1
                weight = 0.4
                verdict = f"soft exits no earlier ({was['y']:+.2f}% -> {f['y']:+.2f}%)"
            else:
                verdict = f"ok   exits earlier at {f['y']:+.2f}%"
        elif lab == "nofill":
            verdict = ("ok   chase is armed" if xw.EXIT_LMT_CHASE_ENABLED
                       else "FAIL the limit chase is disabled")
            if not xw.EXIT_LMT_CHASE_ENABLED:
                hard += 1
                weight = 1.0
        print(f"{lab:8} {p.name[:40]:40} {verdict:54}"
              + (f" [{weight:.1f}]" if weight else ""))

    cost = hard + 0.4 * soft
    print(f"\n{len(cases)} labelled  hard={hard}  soft={soft}  "
          f"unscorable={unscorable}  COST={cost:.1f}")
    if unscorable:
        print(f"⚠ {unscorable} file(s) could not be scored -- either the entry "
              "never filled, or the label is a claim about what happened AFTER "
              "the exit and the recording ends at the exit. The second kind "
              "needs POST_EXIT_WATCH_SEC on when the trade is taken.")
    return cost


def leave_one_out(limits_table):
    """Each defence's marginal contribution: turn it off, re-score, show the
    delta. Mirrors eval_rules.py --leave-one-out."""
    base = score_labels(limits_table)
    knobs = (("trade confirmation", "EXIT_REQUIRE_TRADE_CONFIRM", False),
             ("spread band", "EXIT_TRIGGER_SPREAD_MULT", 0.0),
             ("hard floor", "HARD_FLOOR_PCT", 0.0),
             ("exit-limit chase", "EXIT_LMT_CHASE_ENABLED", False))
    print("\n=== leave-one-out ===")
    for name, attr, off in knobs:
        keep = getattr(xw, attr)
        setattr(xw, attr, off)
        try:
            print(f"\n--- without the {name} ({attr}={off!r}) ---")
            c = score_labels(limits_table)
        finally:
            setattr(xw, attr, keep)
        print(f"    {name}: cost {base:.1f} -> {c:.1f}  (delta {c - base:+.1f})")
    return 0


def recorded_exit(path):
    """What the SHIPPED v8 run actually did, straight from the file."""
    rows = list(csv.DictReader(open(path), delimiter="\t"))
    trg = [r for r in rows if r.get("event") == "stop_triggered"]
    fills = [r for r in rows if _f(r.get("orig_avg_fill"))]
    if not (trg and fills):
        return None
    entry = _f(fills[0]["orig_avg_fill"])
    b = _f(trg[0]["bid"])
    return dict(ts=trg[0]["timestamp"], y=((b - entry) / entry * 100.0) if b else None,
                aux=_f(trg[0].get("aux_price")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="*")
    ap.add_argument("--all", action="store_true", help="every table from 2026-09-28 on")
    ap.add_argument("--limits-table", default=str(XWING_DIR / "yield-triggerLimit-data"
                                                  / "v8-input.tsv"))
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--self-check", action="store_true",
                    help="assert the 3 labelled ejections are now held")
    ap.add_argument("--score-labels", action="store_true",
                    help="score the exit rules against the labelled corpus "
                         "(_early_/_good_/_hloss_/_late_/_nofill_ prefixes)")
    ap.add_argument("--leave-one-out", action="store_true",
                    help="--score-labels once per defence, each turned off")
    a = ap.parse_args()

    if a.self_check:
        return self_check(a.limits_table)
    if a.leave_one_out:
        return leave_one_out(a.limits_table)
    if a.score_labels:
        score_labels(a.limits_table, verbose=a.verbose)
        return 0

    if a.files:
        files = [Path(f) for f in a.files]
    elif a.all:
        files = sorted(p for p in TABLES.glob("*xwing-table_*.tsv")
                       if p.name.split("_")[-2] >= "2026-09-28")
    else:
        files = sorted(TABLES.glob("_early_xwing-table_*.tsv"))

    print(f"{'file':46} {'entry':>8} {'was':>22} {'now':>34} {'held':>5}")
    for p in files:
        try:
            res = replay(p, a.limits_table, verbose=a.verbose)
        except Exception as e:
            print(f"{p.name[:46]:46}  ERROR {e}")
            continue
        was = recorded_exit(p)
        wtxt = f"stop @ {was['y']:+.2f}%" if was and was["y"] is not None else "-"
        f = res["fired"]
        ntxt = (f"{f['src']:<22} {f['y']:+7.2f}% {f['dt']:>5}" if f else "NEVER FIRES")
        print(f"{p.name[:46]:46} {(res['entry'] or 0):>8.4f} {wtxt:>22} "
              f"{ntxt:>34} {res['held']:>5}")


def self_check(limits_table):
    """The acceptance test for the 2026-10-02 exit-noise defences."""
    cases = sorted(TABLES.glob("_early_xwing-table_*.tsv"))
    assert cases, "no _early_ files found"
    ok = fail = 0
    for p in cases:
        res = replay(p, limits_table)
        f = res["fired"]
        sym = res["symbol"]
        # The recorded run ejected on the bid alone while the last trade was
        # still at/above the entry. The defence must either hold the trade
        # outright, or fire for a REASON other than the bare bid.
        bad = f is not None and f["src"] == "bid_only"
        if bad:
            print(f"  FAIL {sym}: still ejected on the bid alone at {f['y']:+.2f}%")
            fail += 1
        else:
            what = "held to the end of the recording" if f is None else \
                   f"fires as {f['src']} at {f['y']:+.2f}% after {f['dt']}"
            print(f"  PASS {sym}: {what} ({res['held']} passes held)")
            ok += 1
    print(f"\n{ok} passed, {fail} failed")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main() or 0)
