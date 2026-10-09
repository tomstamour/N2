#!/usr/bin/env python3
"""What does the tape do in the W seconds AFTER a trade-mole signal? (2026-10-08)

The operator labels a trigger bad mostly because "little happens after" it or
"the price rises, then falls". Until trade-mole-2.5 a recording stopped at the
trigger, so nothing after the signal could be measured. Since 2026-10-05 a live
trigger is followed by up to 60 s of raw rows (``post_trigger`` = True), and any
signal that fires EARLIER than a recording's live trigger has the rest of that
recording behind it. This tool measures a short confirmation window on both.

Everything is read from the raw rows (TRADE, TICK_PRICE bid/ask, TICK_STRING 48
day volume). It is an exploration aid: the decision rules that survive are put
into trade-mole-2.6 and scored through the REAL code by ``eval_rules.py``.

Signal time T = the row that carries the first ``surge_detected`` = True (a TRADE
row on the trade path, a PRETRADE_SURGE row on the pre-trade path) — NOT the first
``post_trigger`` row, which starts a few ms to tens of ms later.

Our own order does not pollute the tape: x-wing buys 1-55 shares and the mole drops
every print under 100 shares (RTVolume size is in lots, truncated to 0).

Usage:
    python confirm_after_signal.py --tails            # live fires that carry a tail
    python confirm_after_signal.py --tails --window 2
"""
import argparse
import bisect
import csv
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import session_context as SC  # noqa: E402

OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))
DEDUP_EPSILON_SEC = 0.005       # trade-mole-2.5.py DEDUP_EPSILON_SEC
LABELS = {"pos": "good", "late": "good", "neg": "bad", "neu": "grey", "neu-late": "grey",
          "positive": "good", "negative": "bad", "neutral": "grey", "medium": "grey"}


def f(v):
    try:
        return float(v) if v not in (None, "", "None") else None
    except ValueError:
        return None


def load(path):
    with open(path, newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["_t"] = f(r.get("local_arrival_time"))
    return [r for r in rows if r["_t"] is not None]


def signal_row(rows):
    """Index of the first row that carries the live fire, or None."""
    for i, r in enumerate(rows):
        if (r.get("surge_detected") or "").strip().lower() == "true" and \
                r.get("event_type") in ("TRADE", "PRETRADE_SURGE"):
            return i
    return None


class Tape:
    """Fast 'value at or before time t' lookups over one recording."""

    def __init__(self, rows):
        self.rows = rows
        self.t_end = rows[-1]["_t"]
        # One fill is reported on two channels (RTVolume and RTTradeVolume). The mole
        # treats the second report as a twin when it comes from the other channel
        # within DEDUP_EPSILON_SEC of the last accepted trade (trade-mole-2.5.py
        # `is_duplicate`); count it once here too.
        trades, last_t, last_src = [], None, None
        for r in rows:
            if r.get("event_type") != "TRADE" or not f(r.get("price")):
                continue
            src = r.get("rt_source") or ""
            if last_t is not None and src != last_src and (r["_t"] - last_t) < DEDUP_EPSILON_SEC:
                continue                               # twin of the previous fill
            last_t, last_src = r["_t"], src
            trades.append((r["_t"], f(r.get("price")), f(r.get("size")) or 0.0,
                           (r.get("aggr") or "").upper()))
        self.trades = trades
        self.tt = [x[0] for x in trades]
        bids = [(r["_t"], f(r.get("price"))) for r in rows
                if r.get("event_type") == "TICK_PRICE" and f(r.get("tick_type")) == 1 and f(r.get("price"))]
        asks = [(r["_t"], f(r.get("price"))) for r in rows
                if r.get("event_type") == "TICK_PRICE" and f(r.get("tick_type")) == 2 and f(r.get("price"))]
        # quotes echoed on TRADE rows keep the series dense before the tail starts
        for r in rows:
            if r.get("event_type") == "TRADE":
                if f(r.get("bid")):
                    bids.append((r["_t"], f(r["bid"])))
                if f(r.get("ask")):
                    asks.append((r["_t"], f(r["ask"])))
        bids.sort()
        asks.sort()
        self.bids, self.bt = bids, [x[0] for x in bids]
        self.asks, self.at = asks, [x[0] for x in asks]
        # IBKR running day volume (lots), same series the surge-start reference uses
        self.vol = [(r["_t"], v) for r, v in _vol_points(rows)]
        self.vt = [x[0] for x in self.vol]

    def _at(self, ts, series, t):
        i = bisect.bisect_right(ts, t) - 1
        return series[i][1] if i >= 0 else None

    def bid(self, t):
        return self._at(self.bt, self.bids, t)

    def ask(self, t):
        return self._at(self.at, self.asks, t)

    def last(self, t):
        i = bisect.bisect_right(self.tt, t) - 1
        return self.trades[i][1] if i >= 0 else None

    def day_volume(self, t):
        i = bisect.bisect_right(self.vt, t) - 1
        return self.vol[i][1] if i >= 0 else None


def _vol_points(rows):
    last = None
    for r in rows:
        et, tt = r.get("event_type"), f(r.get("tick_type"))
        v = None
        if et == "TICK_SIZE" and tt == 8:
            v = f(r.get("size"))
        elif et == "TRADE" and (r.get("rt_source") or "") == "RTVolume":
            v = f(r.get("rt_total_volume"))
        elif et == "TICK_STRING" and tt == 48:
            parts = (r.get("value_str") or "").split(";")
            if len(parts) >= 4:
                v = f(parts[3])
        if v is None or (last is not None and v < last):
            continue
        last = v
        yield r, v


def window_features(tape, T, W):
    """Measures over (T, T+W]. None when the recording ends before T+W."""
    if tape.t_end < T + W - 1e-9:
        return None
    tr = [x for x in tape.trades if T < x[0] <= T + W]
    px0 = tape.last(T)
    px1 = tape.last(T + W)
    bid0, bid1 = tape.bid(T), tape.bid(T + W)
    ask0, ask1 = tape.ask(T), tape.ask(T + W)
    v0, v1 = tape.day_volume(T), tape.day_volume(T + W)
    d = {
        "n_trades": len(tr),
        "vol_lots": sum(x[2] for x in tr),
        "n_buy": sum(1 for x in tr if x[3] == "BUY"),
        "n_sell": sum(1 for x in tr if x[3] == "SELL"),
        "px0": px0, "px1": px1,
        "px_chg_pct": ((px1 / px0 - 1) * 100) if (px0 and px1) else None,
        "px_max_pct": ((max(x[1] for x in tr) / px0 - 1) * 100) if (tr and px0) else None,
        "px_min_pct": ((min(x[1] for x in tr) / px0 - 1) * 100) if (tr and px0) else None,
        "bid0": bid0, "bid1": bid1,
        "bid_chg_pct": ((bid1 / bid0 - 1) * 100) if (bid0 and bid1) else None,
        "ask0": ask0, "ask1": ask1,
        "ask_chg_pct": ((ask1 / ask0 - 1) * 100) if (ask0 and ask1) else None,
        "spread1_pct": ((ask1 - bid1) / bid1 * 100) if (bid1 and ask1 and bid1 > 0) else None,
        "dayvol_delta": (v1 - v0) if (v0 is not None and v1 is not None) else None,
    }
    return d


# --------------------------------------------------------------------------- #
# Checks on the W-second window after a signal. Each takes the window
# features (confirm_after_signal.window_features) and returns True = confirmed.
# --------------------------------------------------------------------------- #
def _ge(v, x):
    return v is not None and v >= x


CHECKS = {
    "K0": ("no check", lambda d: True),
    "K1": ("at least 1 trade", lambda d: d["n_trades"] >= 1),
    "K2": ("at least 2 trades", lambda d: d["n_trades"] >= 2),
    "K3": ("2 trades, price not lower", lambda d: d["n_trades"] >= 2 and _ge(d["px_chg_pct"], 0.0)),
    "K4": ("1 trade, bid not falling", lambda d: d["n_trades"] >= 1 and _ge(d["bid_chg_pct"], 0.0)),
    "K5": ("at least 1 buyer-side trade", lambda d: d["n_buy"] >= 1),
    "K6": ("2 trades, bid not falling", lambda d: d["n_trades"] >= 2 and _ge(d["bid_chg_pct"], 0.0)),
    "K7": ("1 trade + day volume rising", lambda d: d["n_trades"] >= 1 and _ge(d["dayvol_delta"], 1)),
    "K8": ("bid not falling", lambda d: _ge(d["bid_chg_pct"], 0.0)),
}


def first_confirm_time(tape, T, W, check):
    """Earliest time in (T, T+W] at which `check` holds on the window so far, or
    None. Evaluated at every trade in the window and at T+W, which is what a
    tick-driven implementation would see. Returns (state, t) where state is
    'ok', 'fail' or 'nodata'. The no-check case (K0) confirms at T itself: it
    needs no data after the signal and adds no wait."""
    if check is CHECKS["K0"][1]:
        return "ok", T
    if tape.t_end < T + W - 1e-9:
        return "nodata", None
    times = [x[0] for x in tape.trades if T < x[0] <= T + W] + [T + W]
    for t in times:
        d = window_features(tape, T, t - T)
        if d is not None and check(d):
            return "ok", t
    return "fail", None


def tail_files():
    out = []
    for p in sorted(glob.glob(os.path.join(OUT_DIR, "_*_trade-mole-table_*.csv"))):
        b = os.path.basename(p)
        m = re.match(r"^_([a-z-]+)_trade-mole-table_([A-Z0-9.\-]+)_(\d{4}-\d{2}-\d{2})_", b)
        if not m or m.group(3) < "2026-10-03" or m.group(1) not in LABELS:
            continue
        out.append((m.group(1), m.group(2), p))
    return out


def checks_summary(W):
    """Every check, on every live fire that carries a tail: how many bad / grey / good
    signals it keeps, how long the order waits, what the wait costs in price."""
    sig = []                                    # (cls, lab, sym, path, T, tape, rows)
    for lab, sym, p in tail_files():
        rows = load(p)
        i = signal_row(rows)
        if i is None:
            continue
        tape = Tape(rows)
        if tape.t_end < rows[i]["_t"] + W:
            continue                            # tail too short (session ended)
        sig.append((LABELS[lab], lab, sym, "pre" if rows[i].get("event_type") == "PRETRADE_SURGE" else "trd",
                    rows[i]["_t"], tape))
    for subset, keep in (("trade path only", lambda s: s[3] == "trd"), ("all signals", lambda s: True)):
        sub = [s for s in sig if keep(s)]
        n = {c: sum(1 for s in sub if s[0] == c) for c in ("good", "grey", "bad")}
        print(f"\n== {subset}: good {n['good']}  grey {n['grey']}  bad {n['bad']}   (window {W:g}s)")
        print(f"{'check':5}{'what':34}{'good kept':>10}{'grey kept':>10}{'bad kept':>9}"
              f"{'net(1:3)':>9}   wait s (median/max)  ask cost % (median/max)")
        for k, (desc, fn) in CHECKS.items():
            kept = {c: 0 for c in n}
            waits, costs = [], []
            for cls, lab, sym, path, T, tape in sub:
                st, tc = first_confirm_time(tape, T, W, fn)
                if st == "ok":
                    kept[cls] += 1
                    if cls == "good":
                        waits.append(tc - T)
                        a0, a1 = tape.ask(T), tape.ask(tc)
                        if a0 and a1:
                            costs.append((a1 / a0 - 1) * 100)
            # operator's balance: a removed bad = +1, a removed grey = +0.4, a lost good = -3
            net = (n["bad"] - kept["bad"]) + 0.4 * (n["grey"] - kept["grey"]) - 3.0 * (n["good"] - kept["good"])
            med = lambda v: "-" if not v else f"{sorted(v)[len(v) // 2]:.2f}"
            mx = lambda v: "-" if not v else f"{max(v):.2f}"
            print(f"{k:5}{desc[:33]:34}{kept['good']:>7}/{n['good']:<2}{kept['grey']:>7}/{n['grey']:<2}"
                  f"{kept['bad']:>6}/{n['bad']:<2}{net:>+9.1f}   {med(waits):>6}/{mx(waits):<6}   "
                  f"{med(costs):>6}/{mx(costs):<6}")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tails", action="store_true",
                    help="measure the live fires of recordings that carry a tail")
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--checks", action="store_true",
                    help="score every candidate check on the tail files (wait and price cost)")
    args = ap.parse_args()
    W = args.window
    if not args.tails:
        ap.error("nothing to do; pass --tails")
    if args.checks:
        return checks_summary(W)

    print(f"window after the signal: {W:g} s\n")
    hdr = ("cls   label     sym   path  Tsec   n_tr vol  buy sell | px0     px_chg%  max%   min%  | "
           "bid0    bid%   ask0    ask%  spr%  | dayvolΔ")
    print(hdr)
    for lab, sym, p in tail_files():
        rows = load(p)
        i = signal_row(rows)
        if i is None:
            print(f"{LABELS[lab]:5} {lab:9} {sym:5} (never fired)")
            continue
        T = rows[i]["_t"]
        path = "pre" if rows[i].get("event_type") == "PRETRADE_SURGE" else "trd"
        d = window_features(Tape(rows), T, W)
        if d is None:
            print(f"{LABELS[lab]:5} {lab:9} {sym:5} {path}  not enough data after the signal")
            continue
        g = lambda v, n=2: "   -  " if v is None else f"{v:6.{n}f}"
        print(f"{LABELS[lab]:5} {lab:9} {sym:5} {path}  {T - rows[0]['_t']:6.1f} {d['n_trades']:4} "
              f"{d['vol_lots']:4.0f} {d['n_buy']:3} {d['n_sell']:4} | {g(d['px0'], 4)} {g(d['px_chg_pct'])} "
              f"{g(d['px_max_pct'])} {g(d['px_min_pct'])} | {g(d['bid0'], 4)} {g(d['bid_chg_pct'])} "
              f"{g(d['ask0'], 4)} {g(d['ask_chg_pct'])} {g(d['spread1_pct'])} | "
              f"{'-' if d['dayvol_delta'] is None else f'{d['dayvol_delta']:.0f}'}")


if __name__ == "__main__":
    main()
