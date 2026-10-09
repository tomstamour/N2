#!/usr/bin/env python3
"""Dual-subscribe probe: reqMktData (RTVolume) vs reqTickByTickData (AllLast).

WHY THIS EXISTS
---------------
The 2026-08-08 feed-lag report measured a ~0.5 s exchange-stamp -> local-arrival
lag and called it "not ours to fix". Two things about that need testing:

  1. 15327 of 19812 recorded TRADE rows (77%) carry rt_single_trade_flag=False,
     i.e. they are IBKR AGGREGATES of several fills. The mole's reconciliation
     path for that (generic tick 54) is dead: tws_trade_count is populated on
     0 of 19812 rows. So `trades_in_5s` counts MESSAGES, not fills -- and Rule D
     gates on it.
  2. An aggregate carries ONE stamp, so whatever delay the FIRST fill inside it
     suffered is invisible to a stamp->arrival metric. A PR surge is exactly the
     bursty regime where fills get aggregated.

Neither is answerable from the 600 existing recordings -- they are all
reqMktData. This probe subscribes BOTH feeds to the same contract on ONE client
so every comparison is against a shared clock, and answers:

  Q1  per fill, which feed arrives first, and by how much?
  Q2  how many real fills does one RTVolume message hide?
  Q3  does an aggregate's stamp point at its FIRST or its LAST constituent fill?
      (this is the one that decides whether conflation costs LATENCY at all, as
      opposed to merely costing count accuracy)
  Q4  all of the above restricted to the first seconds after subscribe, since
      that is the entire life of a PR surge and the only regime the rules see.

DECISION CRITERION, fixed before running: migrating the mole to tick-by-tick is
justified only if Q1/Q3 show the first fill of a burst arriving materially
earlier than the aggregate containing it. If aggregates are stamped with their
first fill and arrive promptly, there is no latency to recover and the finding
collapses to a COUNTING problem -- fixable in _compute_windows without touching
the subscription.

Costs of migrating, to weigh against whatever this shows:
  * tickByTickAllLast's `time` is epoch SECONDS (int) -- a resolution regression
    from RTVolume's milliseconds. Breaks the first-ITI seed and this very lag
    metric. Does NOT affect later ITIs (local monotonic clock) or Rules B/F
    (tick 45, already whole-second).
  * this codebase records a 3-concurrent-request cap on reqTickByTickData
    (x-wing-2.0.py:33, x-wing-2.1.py:41). --cap-test probes it.
  * unconflated counts rescale every count/rate column, so a fresh labelled
    corpus would have to be recorded from scratch.

READ-ONLY with respect to trading: market-data subscriptions only, never places
an order. Run it on the PAPER GW (4002) per the clerk-saturation rule in the
root CLAUDE.md -- the live GW drops bulk data jobs under contention.

Usage:
    /home/tom/venv/bin/python probe_tickbytick.py --symbol AAPL --duration 600
    /home/tom/venv/bin/python probe_tickbytick.py --symbol AAPL --cap-test
    /home/tom/venv/bin/python probe_tickbytick.py --analyze probe-tbt_AAPL_*.csv
"""

import argparse
import csv
import glob
import logging
import os
import statistics
import sys
import threading
import time
from datetime import datetime

from ibapi.client import EClient
from ibapi.contract import Contract
from ibapi.wrapper import EWrapper

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))

# Same generic ticks the mole subscribes with (trade-mole-2.2.py GENERIC_TICKS),
# so the RTVolume side of this comparison is byte-for-byte what the mole sees.
GENERIC_TICKS = "233,236,293,294,295,318,375,165,221"

# reqId layout: market data at 100+i, tick-by-tick at 200+i, one slot per symbol.
REQ_MKTDATA_BASE = 100
REQ_TBT_BASE = 200

FIELDS = [
    "symbol",
    "feed",             # RTVolume | RTTradeVolume | TickByTick
    "recv_mono",        # shared perf_counter, taken at the top of the callback
    "recv_wall",
    "recv_iso",
    "session_age",      # seconds since the subscribe call
    "stamp_epoch",      # the message's own timestamp (ms for RTV, sec for TBT)
    "price",
    "size",
    "rt_total_volume",  # RTVolume only: cumulative session volume
    "single_trade_flag",
    "exchange",
    "special_conditions",
    "raw",
]


def make_contract(symbol: str) -> Contract:
    """Identical to trade-mole-2.2.py:410 make_contract."""
    c = Contract()
    c.symbol = symbol
    c.secType = "STK"
    c.exchange = "SMART"
    c.primaryExchange = "NASDAQ"
    c.currency = "USD"
    return c


class ProbeApp(EWrapper, EClient):
    """Logs both feeds to one CSV. All callbacks fire on the EClient.run()
    thread, so recv_mono is a single totally-ordered clock across both feeds --
    that is the whole point of subscribing them on one client."""

    def __init__(self, writer, fh):
        EClient.__init__(self, wrapper=self)
        self._w = writer
        self._fh = fh
        self._lock = threading.Lock()
        self.connected_event = threading.Event()
        self.disconnected_flag = False
        self.t0 = None                  # perf_counter at subscribe
        self.counts = {"RTVolume": 0, "RTTradeVolume": 0, "TickByTick": 0}
        self.errors = []
        self.sym_by_req = {}            # reqId -> symbol, both feeds
        self._log = logging.getLogger("probe")

    # -- plumbing ----------------------------------------------------------
    def error(self, reqId, errorCode, errorString, advancedOrderRejectJson=""):
        if errorCode in (2104, 2106, 2107, 2158, 2119, 2100, 2108):
            self._log.info(f"[IBKR {errorCode}] {errorString}")
            return
        self.errors.append((reqId, errorCode, errorString))
        self._log.warning(f"[IBKR err {errorCode}] reqId={reqId}: {errorString}")

    def nextValidId(self, orderId):
        self._log.info(f"Connected. nextValidId={orderId}")
        self.connected_event.set()

    def connectionClosed(self):
        self.disconnected_flag = True
        self._log.info("IBKR connection closed.")

    def _emit(self, **kw):
        row = {k: kw.get(k, "") for k in FIELDS}
        with self._lock:
            self._w.writerow(row)
            self._fh.flush()

    # -- reqMktData side ---------------------------------------------------
    def tickString(self, reqId, tickType, value):
        now_mono, now_wall = time.perf_counter(), time.time()
        if tickType not in (48, 77) or not value:
            return
        # Same parse as trade-mole-2.2.py:1554 tickString.
        try:
            parts = value.split(";")
            if len(parts) < 6 or not parts[0] or not parts[1]:
                return
            price = float(parts[0])
            size = int(float(parts[1]))
            stamp = (int(parts[2]) / 1000.0) if parts[2] else None
            total_vol = int(float(parts[3])) if parts[3] else None
            single = (parts[5].lower() == "true")
        except (ValueError, IndexError) as e:
            self._log.debug(f"RTVolume parse error on {value!r}: {e}")
            return
        if size <= 0:
            return
        feed = "RTVolume" if tickType == 48 else "RTTradeVolume"
        self.counts[feed] += 1
        self._emit(
            symbol=self.sym_by_req.get(reqId, ""),
            feed=feed, recv_mono=f"{now_mono:.6f}", recv_wall=f"{now_wall:.6f}",
            recv_iso=datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
            session_age=f"{now_mono - self.t0:.6f}" if self.t0 else "",
            stamp_epoch=f"{stamp:.3f}" if stamp else "",
            price=price, size=size, rt_total_volume=total_vol,
            single_trade_flag=single, raw=value,
        )

    # -- reqTickByTickData side -------------------------------------------
    def tickByTickAllLast(self, reqId, tickType, time_, price, size,
                          tickAttribLast, exchange, specialConditions):
        now_mono, now_wall = time.perf_counter(), time.time()
        self.counts["TickByTick"] += 1
        self._emit(
            symbol=self.sym_by_req.get(reqId, ""),
            feed="TickByTick", recv_mono=f"{now_mono:.6f}", recv_wall=f"{now_wall:.6f}",
            recv_iso=datetime.fromtimestamp(now_wall).isoformat(timespec="milliseconds"),
            session_age=f"{now_mono - self.t0:.6f}" if self.t0 else "",
            stamp_epoch=f"{float(time_):.3f}",   # epoch SECONDS -- see module docstring
            price=price, size=size, exchange=exchange,
            special_conditions=specialConditions,
        )


# ==========================================================================
# Capture
# ==========================================================================

def run_probe(args):
    symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    tag = symbols[0] if len(symbols) == 1 else f"multi{len(symbols)}"
    path = os.path.join(OUT_DIR, f"probe-tbt_{tag}_{stamp}.csv")
    fh = open(path, "w", newline="")
    w = csv.DictWriter(fh, fieldnames=FIELDS)
    w.writeheader()

    app = ProbeApp(w, fh)
    logging.info(f"Connecting to {args.host}:{args.port} (clientId={args.clientID}) ...")
    app.connect(args.host, args.port, clientId=args.clientID)
    threading.Thread(target=app.run, daemon=True, name="ibapi-run").start()
    if not app.connected_event.wait(timeout=10):
        logging.error("No connection within 10s. Is the Gateway up on that port?")
        app.disconnect()
        fh.close()
        return 1

    app.t0 = time.perf_counter()
    for i, sym in enumerate(symbols):
        mkt_id, tbt_id = REQ_MKTDATA_BASE + i, REQ_TBT_BASE + i
        app.sym_by_req[mkt_id] = sym
        app.sym_by_req[tbt_id] = sym
        c = make_contract(sym)
        # Order matters for fairness: whichever is requested first gets a head
        # start on the subscribe round-trip. --tbt-first flips it so the bias is
        # measurable rather than assumed away.
        if args.tbt_first:
            app.reqTickByTickData(tbt_id, c, "AllLast", 0, False)
            app.reqMktData(mkt_id, c, GENERIC_TICKS, False, False, [])
        else:
            app.reqMktData(mkt_id, c, GENERIC_TICKS, False, False, [])
            app.reqTickByTickData(tbt_id, c, "AllLast", 0, False)
    logging.info(f"Subscribed both feeds to {len(symbols)} symbol(s): "
                 f"{','.join(symbols)}. Writing {path}")
    if len(symbols) > 3:
        logging.info(f"{len(symbols)} concurrent tick-by-tick requests -- this "
                     f"run also tests the documented 3-request cap; watch for errors.")

    try:
        end = time.time() + args.duration
        while time.time() < end and not app.disconnected_flag:
            time.sleep(1)
            remaining = int(end - time.time())
            if remaining > 0 and remaining % 30 == 0:
                logging.info(f"[{remaining:>4}s left] {app.counts}")
    except KeyboardInterrupt:
        logging.info("Interrupted; stopping capture.")

    for i in range(len(symbols)):
        app.cancelMktData(REQ_MKTDATA_BASE + i)
        app.cancelTickByTickData(REQ_TBT_BASE + i)
    time.sleep(0.5)
    app.disconnect()
    fh.close()

    logging.info(f"Done. {app.counts}")
    if app.errors:
        logging.info("Errors seen (relevant to the 3-request cap):")
        for r, c, s in app.errors:
            logging.info(f"  reqId={r} code={c}: {s}")
    logging.info(f"Now: probe_tickbytick.py --analyze {path}")
    return 0


# ==========================================================================
# Analysis
# ==========================================================================

def load(path, symbol=None):
    rtv, tbt = [], []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if symbol is not None and (r.get("symbol") or "") != symbol:
                continue
            try:
                r["recv_mono"] = float(r["recv_mono"])
                r["size"] = int(float(r["size"]))
                r["price"] = float(r["price"])
                r["session_age"] = float(r["session_age"]) if r["session_age"] else None
                r["stamp_epoch"] = float(r["stamp_epoch"]) if r["stamp_epoch"] else None
                r["rt_total_volume"] = (int(float(r["rt_total_volume"]))
                                        if r["rt_total_volume"] else None)
            except (ValueError, KeyError):
                continue
            # RTTradeVolume is the twin of the same fill on the other channel;
            # counting it here would double every RTVolume-side number, which is
            # exactly the defect the 08-08 report found in the mole itself.
            if r["feed"] == "RTVolume":
                rtv.append(r)
            elif r["feed"] == "TickByTick":
                tbt.append(r)
    rtv.sort(key=lambda x: x["recv_mono"])
    tbt.sort(key=lambda x: x["recv_mono"])
    return rtv, tbt


def match_by_volume(rtv, tbt):
    """Assign tick-by-tick fills to the RTVolume message that reported them.

    RTVolume carries a cumulative session volume, so the fills covered by
    message n are those whose sizes sum to (total_n - total_{n-1}). Greedy in
    arrival order; a message whose delta cannot be filled exactly is reported as
    unmatched rather than forced, so a feed inconsistency shows up instead of
    silently biasing the result.
    """
    matched, unmatched = [], 0
    i = 0
    prev_total = None
    for m in rtv:
        tot = m["rt_total_volume"]
        if tot is None:
            continue
        if prev_total is None:
            prev_total = tot
            # Skip the first message: its delta reaches back before we
            # subscribed, so there are no tick-by-tick fills to cover it.
            continue
        delta = tot - prev_total
        prev_total = tot
        if delta <= 0:
            continue
        acc, covered = 0, []
        j = i
        while j < len(tbt) and acc < delta:
            acc += tbt[j]["size"]
            covered.append(tbt[j])
            j += 1
        if acc == delta and covered:
            matched.append((m, covered))
            i = j
        else:
            unmatched += 1
    return matched, unmatched


def q(vals, p):
    v = sorted(vals)
    return v[int(p * (len(v) - 1))] if v else float("nan")


def lag_block(rtv, tbt, say):
    """Per-feed stamp -> arrival lag. This is the measurement that answers
    "would tick-by-tick be faster", and unlike the fill matching it needs no
    cross-feed join, so a feed disagreement cannot corrupt it.

    RTVolume stamps are full milliseconds, so its lag is read directly.
    tickByTickAllLast stamps are epoch SECONDS, so an observed lag is
    L + frac(true_trade_time). Assuming trades fall uniformly within a second
    that is uniform on [L, L+1): the low quantile estimates L and the spread
    should come out near 1.0. If it does not, the model is wrong and the
    estimate is flagged rather than reported.
    """
    rl = [float(r["recv_wall"]) - r["stamp_epoch"] for r in rtv if r["stamp_epoch"]]
    tl = [float(r["recv_wall"]) - r["stamp_epoch"] for r in tbt if r["stamp_epoch"]]
    # The first tick of each feed is the pre-subscribe snapshot (a stale last
    # trade), not a live delivery; it would drag every statistic.
    tl_live = tl[1:] if len(tl) > 2 else tl

    say("### Stamp -> arrival lag, per feed")
    say()
    if rl:
        say(f"RTVolume     n={len(rl):<4} min {min(rl):+.3f}  median {q(rl,.5):+.3f}  "
            f"max {max(rl):+.3f}  (spread {max(rl)-min(rl):.3f} s)")
    if tl_live:
        lo, hi = q(tl_live, .05), q(tl_live, .95)
        say(f"TickByTick   n={len(tl_live):<4} min {min(tl_live):+.3f}  "
            f"median {q(tl_live,.5):+.3f}  max {max(tl_live):+.3f}")
        say()
        width = hi - lo
        if 0.6 <= width <= 1.4:
            say(f"  p05 {lo:.3f} / p95 {hi:.3f}, spread {width:.3f} ~ 1.0 as the "
                f"whole-second truncation model predicts, so the true "
                f"tick-by-tick lag is **~{lo:.3f} s**.")
            if rl:
                d = q(rl, .5) - lo
                say(f"  vs RTVolume's {q(rl,.5):.3f} s -> tick-by-tick is "
                    f"**{d:+.3f} s** {'earlier' if d > 0 else 'later'}.")
        else:
            say(f"  p05 {lo:.3f} / p95 {hi:.3f}, spread {width:.3f} is NOT ~1.0, so "
                f"the truncation model does not hold here and no point estimate "
                f"of the tick-by-tick lag is defensible from this sample.")
    say()


def analyze(paths, burst_sec):
    out = []

    def say(s=""):
        print(s)
        out.append(s)

    for path in paths:
        with open(path, newline="") as fh:
            syms = sorted({(r.get("symbol") or "") for r in csv.DictReader(fh)})
        for sym in syms:
            _analyze_one(path, sym or None, burst_sec, say)

    return "\n".join(out)


def _analyze_one(path, symbol, burst_sec, say):
        rtv, tbt = load(path, symbol)
        say(f"## {os.path.basename(path)}" + (f" -- {symbol}" if symbol else ""))
        say()
        if not tbt and not rtv:
            say("Silent: neither feed delivered a print. Nothing to compare.")
            say()
            return
        if not tbt:
            say(f"No tick-by-tick rows ({len(rtv)} RTVolume). Subscription rejected "
                "or capped -- check the run's error log before reading anything "
                "into this.")
            say()
            return

        say(f"RTVolume messages: {len(rtv)}   TickByTick fills: {len(tbt)}")
        single = [r for r in rtv if r["single_trade_flag"] == "True"]
        say(f"RTVolume single-trade: {len(single)} / {len(rtv)} "
            f"({100*len(single)/max(len(rtv),1):.1f}%)")
        if rtv:
            say(f"fills per RTVolume message: {len(tbt)/len(rtv):.2f}x")
        say()

        lag_block(rtv, tbt, say)

        # Do the feeds even agree on volume?  If not, the fill matching below is
        # meaningless and every count comparison needs that caveat attached.
        tbt_vol = sum(r["size"] for r in tbt)
        rtv_vol = None
        cums = [r["rt_total_volume"] for r in rtv if r["rt_total_volume"] is not None]
        if len(cums) >= 2:
            rtv_vol = cums[-1] - cums[0]
        say("### Do the feeds agree on volume?")
        say()
        say(f"tick-by-tick fill sizes sum to {tbt_vol}")
        if rtv_vol is not None:
            say(f"RTVolume cumulative volume moved {rtv_vol} "
                f"({cums[0]} -> {cums[-1]})")
            if rtv_vol == 0 or tbt_vol / max(rtv_vol, 1) > 3:
                say()
                say("**The two feeds are not reporting the same trade population.** "
                    "That is a bigger finding than any latency number here: "
                    "reqMktData is not merely aggregating the same fills, it is "
                    "omitting volume that tick-by-tick reports. Fill matching "
                    "below is skipped as meaningless.")
                say()
                return
        say()

        matched, unmatched = match_by_volume(rtv, tbt)
        if not matched:
            say(f"Volume matching failed on every message ({unmatched} unmatched); "
                "the two feeds disagree on cumulative volume. Nothing below is "
                "trustworthy for this file.")
            say()
            return
        say(f"Matched {len(matched)} RTVolume messages to fills "
            f"({unmatched} unmatched, reported not forced)")
        say()

        # -- Q2: how many fills does one RTVolume message hide? -------------
        fills = [len(c) for _, c in matched]
        say(f"### Q2  fills per RTVolume message")
        say()
        say(f"mean {statistics.mean(fills):.2f}   median {q(fills,.5)}   "
            f"p90 {q(fills,.9)}   max {max(fills)}")
        say(f"messages hiding >1 fill: {sum(1 for f in fills if f>1)}/{len(fills)} "
            f"({100*sum(1 for f in fills if f>1)/len(fills):.1f}%)")
        say(f"total fills {sum(fills)} vs {len(matched)} messages -> "
            f"trades_in_5s undercounts by {sum(fills)/max(len(matched),1):.2f}x")
        say()

        # -- Q1/Q3: latency the first fill of an aggregate suffers -----------
        # The headline. For each RTVolume message, how long after its FIRST
        # constituent fill arrived on tick-by-tick did the message itself land?
        first_deltas = [(m["recv_mono"] - c[0]["recv_mono"]) for m, c in matched]
        last_deltas = [(m["recv_mono"] - c[-1]["recv_mono"]) for m, c in matched]
        say(f"### Q1  RTVolume arrival minus tick-by-tick arrival (s)")
        say()
        say("| against | p10 | median | p90 |")
        say("|---|---|---|---|")
        say(f"| its FIRST fill | {q(first_deltas,.1):+.4f} | "
            f"{q(first_deltas,.5):+.4f} | {q(first_deltas,.9):+.4f} |")
        say(f"| its LAST fill | {q(last_deltas,.1):+.4f} | "
            f"{q(last_deltas,.5):+.4f} | {q(last_deltas,.9):+.4f} |")
        say()
        say("Positive = tick-by-tick arrived first. The FIRST-fill row is the "
            "latency conflation costs us; the LAST-fill row is pure feed "
            "overhead with conflation removed.")
        say()

        multi = [(m, c) for m, c in matched if len(c) > 1]
        if multi:
            md = [(m["recv_mono"] - c[0]["recv_mono"]) for m, c in multi]
            say(f"Restricted to the {len(multi)} genuinely aggregated messages: "
                f"first-fill delta median {q(md,.5):+.4f} s, p90 {q(md,.9):+.4f} s")
            say()

            # -- Q3: does the aggregate's stamp point at first or last? -----
            # RTVolume stamps are ms, tick-by-tick stamps are whole seconds, so
            # this is only decidable when the covered fills straddle a second
            # boundary. Report the decidable subset rather than guessing.
            first_hit = last_hit = undecidable = 0
            for m, c in multi:
                if m["stamp_epoch"] is None:
                    continue
                secs = {int(f["stamp_epoch"]) for f in c if f["stamp_epoch"]}
                if len(secs) < 2:
                    undecidable += 1
                    continue
                ms = int(m["stamp_epoch"])
                fs, ls = int(c[0]["stamp_epoch"]), int(c[-1]["stamp_epoch"])
                if ms == ls and ms != fs:
                    last_hit += 1
                elif ms == fs and ms != ls:
                    first_hit += 1
                else:
                    undecidable += 1
            say("### Q3  which constituent does an aggregate's stamp match?")
            say()
            say(f"matches its LAST fill:  {last_hit}")
            say(f"matches its FIRST fill: {first_hit}")
            say(f"undecidable (all fills in one second): {undecidable}")
            say()
            if last_hit > first_hit:
                say("Stamped with the LAST fill -> the delay suffered by the "
                    "earlier fills in each burst is invisible to any "
                    "stamp-minus-arrival metric, including the 08-08 report's.")
            elif first_hit > last_hit:
                say("Stamped with the FIRST fill -> stamp-minus-arrival already "
                    "captures the full conflation delay, and the 08-08 lag "
                    "figure needs no correction on this account.")
            say()

        # -- Q4: the burst regime, which is the only one the rules see -------
        if tbt and tbt[0]["session_age"] is not None:
            bm = [(m, c) for m, c in matched
                  if m["session_age"] is not None and m["session_age"] <= burst_sec]
            say(f"### Q4  first {burst_sec:g}s after subscribe (the PR-surge regime)")
            say()
            if bm:
                bf = [len(c) for _, c in bm]
                bd = [(m["recv_mono"] - c[0]["recv_mono"]) for m, c in bm]
                say(f"{len(bm)} messages / {sum(bf)} fills   "
                    f"undercount {sum(bf)/len(bm):.2f}x   "
                    f"first-fill delta median {q(bd,.5):+.4f} s")
            else:
                say("No matched messages inside the window -- the symbol was "
                    "silent at subscribe. Re-run against an active tape.")
            say()


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", help="one symbol, or a comma-separated list; "
                                     "more than 3 also exercises the request cap")
    ap.add_argument("--duration", type=float, default=600.0, help="seconds (default 600)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=4002,
                    help="4002 = PAPER GW (default). 4001 is the LIVE GW -- "
                         "run bulk data jobs on the idle paper GW.")
    ap.add_argument("--clientID", type=int, default=811,
                    help="stay clear of clerk 1000-1029, dailyStats 77, board 20000")
    ap.add_argument("--tbt-first", action="store_true",
                    help="request tick-by-tick BEFORE reqMktData, to measure "
                         "subscribe-order bias instead of assuming it away")
    ap.add_argument("--cap-test", action="store_true",
                    help="fire extra tick-by-tick requests to probe the 3-request cap")
    ap.add_argument("--cap-test-extra", type=int, default=4)
    ap.add_argument("--analyze", nargs="+", metavar="CSV",
                    help="analyze captured probe CSVs instead of capturing")
    ap.add_argument("--burst-sec", type=float, default=5.0)
    ap.add_argument("--report", action="store_true",
                    help="with --analyze, also write mole-outputs/tickbytick-probe-<date>.md")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # ibapi logs every wire message at INFO, which buries the probe's own output.
    logging.getLogger("ibapi").setLevel(logging.WARNING)

    if args.analyze:
        paths = []
        for p in args.analyze:
            paths.extend(sorted(glob.glob(p)) or [p])
        text = analyze(paths, args.burst_sec)
        if args.report:
            rp = os.path.join(OUT_DIR,
                              f"tickbytick-probe-{datetime.now().strftime('%Y-%m-%d')}.md")
            with open(rp, "w") as fh:
                fh.write(f"# reqMktData vs reqTickByTickData probe "
                         f"({datetime.now().strftime('%Y-%m-%d')})\n\n{text}\n")
            print(f"\nwrote {rp}")
        return 0

    if not args.symbol:
        ap.error("--symbol is required unless --analyze is given")
    if args.port == 4001:
        logging.warning("port 4001 is the LIVE Gateway. The paper GW (4002) is "
                        "the right place for a data probe.")
    return run_probe(args)


if __name__ == "__main__":
    sys.exit(main())
