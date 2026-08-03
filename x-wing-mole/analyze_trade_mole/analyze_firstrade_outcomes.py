#!/usr/bin/env python3
"""
First-trade / downward-tape surge fires vs. x-wing trade outcomes.

Motivation (2026-07-23, ZCMD): the trade-mole price-drop veto
(trade-mole-2.1.py:1224-1231) compares the last trade price to _start_price,
which is the FIRST trade of the session (:565). When a surge fires ON that first
trade, start==last, price_change_since_start==0, and the veto is structurally
blind. ~45% of post-veto fires are first-trade fires. This script measures
whether the first-trade fires that land on a DOWNWARD tape actually lose money —
so a directional veto (or a stop-limit entry) can be decided from evidence, not
intuition.

What it does (read-only): for every fired mole recording it
  * pulls the surge (fire) row and its directional signals,
  * joins to the matching x-wing table by the shared {SYM}_{DATE}_{TIME} stamp,
  * extracts the trade outcome (entry fill, worst/last yield, stop hit),
  * cross-tabs outcome by downward-tape flag within cohorts (first-trade / all /
    by rule family), and writes a dated markdown report.

Stdlib only. Run with the venv python for consistency:
    /home/tom/venv/bin/python analyze_firstrade_outcomes.py --since 2026-07-07
"""

import argparse
import csv
import glob
import os
import re
from statistics import median

HERE = os.path.dirname(os.path.abspath(__file__))
MOLE_DIR = os.path.join(HERE, "..", "mole-outputs")
XWING_DIR = os.path.join(HERE, "..", "xwing_tables")

STAMP_RE = re.compile(r"_([A-Z0-9.]+_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})")
DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})_")

# x-wing terminal signals that mean no position was ever taken.
NO_FILL_EVENTS = {"entry_timeout", "entry_inactive"}
NO_FILL_PHASES = {"ENTRY_FAILED"}


def _f(row, key):
    """Parse a CSV/TSV cell as float, tolerating blanks."""
    v = (row.get(key) or "").strip()
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def rule_family(reason):
    """Map a surge_reason string to its rule letter (see should_fire docstring)."""
    if not reason:
        return "?"
    if reason.startswith("bsln_over_prev_trd_gap"):
        return "B"
    if reason.startswith("bslnITI_on_last3trades"):
        return "C"
    if reason.startswith("quote_updates"):
        return "A"
    if reason.startswith("measured_ITIsec"):
        return "D"
    if reason.startswith("dollar_rate") or "price_change" in reason:
        return "E"
    return "?"


def stamp_of(path):
    m = STAMP_RE.search(os.path.basename(path))
    return m.group(1) if m else None


def date_of(path):
    m = DATE_RE.search(os.path.basename(path))
    return m.group(1) if m else "0000-00-00"


def fire_row(mole_path):
    """Return the first surge_detected==True row, or None if the file never fired."""
    try:
        with open(mole_path, newline="") as fh:
            for row in csv.DictReader(fh):
                if (row.get("surge_detected") or "").strip().lower() == "true":
                    return row
    except (OSError, csv.Error):
        return None
    return None


def extract_fire(row):
    """Directional + identity features from a mole surge row."""
    reason = (row.get("surge_reason") or "").strip()
    aggr = (row.get("aggr") or "").strip()
    bid_drift = _f(row, "bid_drift_5s_bp")
    signed_dollar = _f(row, "signed_dollar_5s")
    lift = _f(row, "lift_offer_ratio_5s")
    ctc = _f(row, "cum_trade_count")
    pc = _f(row, "price_change_since_start_pct")
    downward = (aggr == "SELL") or (bid_drift is not None and bid_drift < 0) \
        or (signed_dollar is not None and signed_dollar < 0)
    return {
        "rule": rule_family(reason),
        "reason": reason,
        "aggr": aggr,
        "bid_drift_5s_bp": bid_drift,
        "signed_dollar_5s": signed_dollar,
        "lift_offer_ratio_5s": lift,
        "price_change_since_start_pct": pc,
        "first_trade": ctc is not None and ctc <= 1,
        "downward_tape": downward,
        "sell_print": aggr == "SELL",
        "neg_bid_drift": bid_drift is not None and bid_drift < 0,
    }


def extract_outcome(xwing_path, stop_pct):
    """Trade outcome proxies from an x-wing table.

    The TSV holds running (unrealized, mid-based) yield_pct and stops mid-EXITING;
    there is no clean realized-exit row, so we use robust proxies: entry fill,
    worst/last yield, and whether the protective stop triggered.
    """
    entry_fill = None
    filled_qty_max = 0.0
    min_yield = None
    final_yield = None
    hit_stop = False
    phases = set()
    events = set()
    try:
        with open(xwing_path, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                phases.add((row.get("phase") or "").strip())
                ev = (row.get("event") or "").strip()
                if ev:
                    events.add(ev)
                    if ev == "stop_triggered":
                        hit_stop = True
                if entry_fill is None:
                    of = _f(row, "orig_avg_fill")
                    if of is not None and of > 0:
                        entry_fill = of
                fq = _f(row, "filled_qty")
                if fq is not None:
                    filled_qty_max = max(filled_qty_max, fq)
                y = _f(row, "yield_pct")
                if y is not None:
                    final_yield = y
                    min_yield = y if min_yield is None else min(min_yield, y)
    except (OSError, csv.Error):
        return None

    no_fill = (bool(events & NO_FILL_EVENTS) or bool(phases & NO_FILL_PHASES)) \
        and filled_qty_max <= 0
    filled = filled_qty_max > 0 and not no_fill

    outcome = None
    if filled:
        if hit_stop or (min_yield is not None and min_yield <= -stop_pct):
            outcome = "loss"
        elif final_yield is not None and final_yield > 0:
            outcome = "win"
        else:
            outcome = "flat/neg"
    return {
        "filled": filled,
        "entry_fill": entry_fill,
        "min_yield": min_yield,
        "final_yield": final_yield,
        "hit_stop": hit_stop,
        "outcome": outcome,
        "no_fill_reason": ",".join(sorted(events & NO_FILL_EVENTS)) or (
            "ENTRY_FAILED" if phases & NO_FILL_PHASES else ""),
    }


def build_records(since, stop_pct):
    records = []
    unmatched = []
    xwing_by_stamp = {stamp_of(p): p for p in glob.glob(os.path.join(XWING_DIR, "*.tsv"))}
    for mole_path in sorted(glob.glob(os.path.join(MOLE_DIR, "trade-mole-table_*.csv"))):
        if date_of(mole_path) < since:
            continue
        row = fire_row(mole_path)
        if row is None:
            continue  # never fired
        stamp = stamp_of(mole_path)
        rec = {"stamp": stamp, "symbol": (row.get("symbol") or stamp.split("_")[0])}
        rec.update(extract_fire(row))
        xp = xwing_by_stamp.get(stamp)
        if xp is None:
            unmatched.append(stamp)
            rec["outcome_data"] = None
        else:
            rec["outcome_data"] = extract_outcome(xp, stop_pct)
        records.append(rec)
    return records, unmatched


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------
def _crosstab(records, stop_pct):
    """Return markdown table lines: outcome split by downward_tape, filled only."""
    lines = []
    for label, flag in (("downward tape", True), ("upward/neutral tape", False)):
        subset = [r for r in records
                  if r["downward_tape"] is flag
                  and r["outcome_data"] and r["outcome_data"]["filled"]]
        n = len(subset)
        if n == 0:
            lines.append(f"| {label} | 0 | – | – | – | – |")
            continue
        losses = sum(1 for r in subset if r["outcome_data"]["outcome"] == "loss")
        wins = sum(1 for r in subset if r["outcome_data"]["outcome"] == "win")
        mins = [r["outcome_data"]["min_yield"] for r in subset
                if r["outcome_data"]["min_yield"] is not None]
        finals = [r["outcome_data"]["final_yield"] for r in subset
                  if r["outcome_data"]["final_yield"] is not None]
        lines.append(
            f"| {label} | {n} | {losses} ({losses / n:.0%}) | {wins} ({wins / n:.0%}) | "
            f"{median(mins):+.1f}% | {min(mins):+.1f}% |"
            if mins else f"| {label} | {n} | {losses} | {wins} | – | – |")
    return lines


def _cohort_block(title, records, stop_pct):
    lines = [f"### {title}", ""]
    filled = [r for r in records if r["outcome_data"] and r["outcome_data"]["filled"]]
    nofill = [r for r in records if r["outcome_data"] and not r["outcome_data"]["filled"]]
    nojoin = [r for r in records if not r["outcome_data"]]
    lines.append(f"N fired = {len(records)}  |  filled = {len(filled)}  |  "
                 f"never-filled = {len(nofill)}  |  no x-wing table = {len(nojoin)}")
    lines.append("")
    lines.append("| tape at fire | filled N | losses | wins | median worst-yield | worst |")
    lines.append("|---|---|---|---|---|---|")
    lines.extend(_crosstab(records, stop_pct))
    lines.append("")
    return lines


def write_report(records, unmatched, since, stop_pct, out_path):
    first = [r for r in records if r["first_trade"]]
    ruleA_first = [r for r in first if r["rule"] == "A"]
    ruleB_first = [r for r in first if r["rule"] == "B"]

    L = []
    L.append("# First-trade / downward-tape surge fires vs. outcomes")
    L.append("")
    L.append(f"Generated by `analyze_firstrade_outcomes.py`  |  fires since **{since}** "
             f"(post-veto)  |  loss = worst yield ≤ −{stop_pct:.0f}% or stop_triggered")
    L.append("")
    L.append(f"Total fired recordings: **{len(records)}**  |  "
             f"first-trade fires: **{len(first)}**  |  "
             f"fires with no matching x-wing table: **{len(unmatched)}**")
    L.append("")
    L.extend(_cohort_block("Cohort 1 — FIRST-TRADE fires (the veto blind spot)", first, stop_pct))
    L.extend(_cohort_block("Cohort 2 — ALL fires (baseline)", records, stop_pct))
    L.extend(_cohort_block("Cohort 3a — first-trade, Rule A only", ruleA_first, stop_pct))
    L.extend(_cohort_block("Cohort 3b — first-trade, Rule B only", ruleB_first, stop_pct))

    # Per-signal discrimination on first-trade filled fires.
    L.append("### Signal discrimination (first-trade, filled) — loss-rate by each component")
    L.append("")
    L.append("| signal true | N | loss-rate |   | signal false | N | loss-rate |")
    L.append("|---|---|---|---|---|---|---|")
    ff = [r for r in first if r["outcome_data"] and r["outcome_data"]["filled"]]
    for key in ("sell_print", "neg_bid_drift", "downward_tape"):
        t = [r for r in ff if r[key]]
        f = [r for r in ff if not r[key]]

        def lr(g):
            if not g:
                return "–"
            return f"{sum(1 for r in g if r['outcome_data']['outcome'] == 'loss') / len(g):.0%}"
        L.append(f"| {key} | {len(t)} | {lr(t)} |   | not {key} | {len(f)} | {lr(f)} |")
    L.append("")

    # Full first-trade detail table.
    L.append("### First-trade fire detail")
    L.append("")
    L.append("| symbol | rule | aggr | bid_drift_5s | down? | filled | entry | worst y% | "
             "final y% | stop | outcome |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(first, key=lambda x: x["stamp"]):
        od = r["outcome_data"]
        if od is None:
            L.append(f"| {r['symbol']} | {r['rule']} | {r['aggr']} | "
                     f"{_fmt(r['bid_drift_5s_bp'])} | {'Y' if r['downward_tape'] else 'n'} | "
                     f"NO-TABLE | | | | | |")
            continue
        L.append(
            f"| {r['symbol']} | {r['rule']} | {r['aggr']} | {_fmt(r['bid_drift_5s_bp'])} | "
            f"{'Y' if r['downward_tape'] else 'n'} | {'Y' if od['filled'] else 'n'} | "
            f"{_fmt(od['entry_fill'])} | {_fmt(od['min_yield'], pct=True)} | "
            f"{_fmt(od['final_yield'], pct=True)} | {'Y' if od['hit_stop'] else ''} | "
            f"{od['outcome'] or od['no_fill_reason']} |")
    L.append("")
    if unmatched:
        L.append(f"_Unmatched stamps (no x-wing table): {', '.join(unmatched)}_")
        L.append("")

    with open(out_path, "w") as fh:
        fh.write("\n".join(L))
    return "\n".join(L)


def _fmt(v, pct=False):
    if v is None:
        return ""
    return f"{v:+.1f}%" if pct else f"{v:g}"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="2026-07-07",
                    help="Only fires on/after this YYYY-MM-DD (default 2026-07-07, post-veto).")
    ap.add_argument("--stop-pct", type=float, default=5.0,
                    help="Loss if worst yield <= -stop_pct or stop_triggered (default 5).")
    ap.add_argument("--out", default=None,
                    help="Markdown report path (default mole-outputs/"
                         "firstrade-outcome-analysis-<today>.md).")
    args = ap.parse_args()

    out = args.out or os.path.join(
        MOLE_DIR, f"firstrade-outcome-analysis-{_today()}.md")

    records, unmatched = build_records(args.since, args.stop_pct)
    if not records:
        print(f"No fired recordings on/after {args.since} in {MOLE_DIR}")
        return
    report = write_report(records, unmatched, args.since, args.stop_pct, out)
    print(report)
    print(f"\n[wrote] {os.path.normpath(out)}")


def _today():
    from datetime import date
    return date.today().isoformat()


if __name__ == "__main__":
    main()
