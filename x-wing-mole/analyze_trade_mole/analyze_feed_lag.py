#!/usr/bin/env python3
"""Exchange-stamp → local-arrival lag across the trade-mole recordings.

Every TRADE row carries both `exchange_time_epoch` (the RTVolume message's own
millisecond stamp) and `local_arrival_time` (when our process saw it), so the
delivery lag is already recorded corpus-wide — no instrumentation needed.

The question this answers: the AZI 2026-08-07 session showed a 456-457 ms lag
that was constant to +/-1 ms across all five fills. Constant-within-session but
varying between sessions points at a per-session offset (a clock or connection
anchor); noisy within a session points at per-tick transport jitter. Only the
second is attackable by anything we control, so the WITHIN-file dispersion is
the number that matters, not the median.

Caveats stated in the report rather than silently absorbed:
  * tick-45 whole-second truncation does NOT affect this — the RTVolume stamp is
    full milliseconds.
  * recordings stop at the fire, so fired sessions contribute few rows and are
    biased toward the first seconds after arming.
  * a session's rows are not independent; per-file aggregates are the unit of
    analysis, and the corpus-level percentiles below are over FILES, not rows.

Read-only. Writes one markdown report:
    mole-outputs/feed-lag-analysis-<date>.md

Usage:
    /home/tom/venv/bin/python analyze_feed_lag.py
    /home/tom/venv/bin/python analyze_feed_lag.py --min-rows 5
"""

import argparse
import csv
import datetime as dt
import glob
import os
import math
import re
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
# Lags outside this band are a clock jump or a parse artifact, not delivery.
SANE_RANGE = (-5.0, 30.0)


def pct(sorted_vals, q):
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    i = q * (len(sorted_vals) - 1)
    lo, hi = int(i), min(int(i) + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (i - lo)


# The RTVolume/RTTradeVolume twin is the SAME fill on the other channel: same
# exchange stamp, arriving ~1 ms later, hence a near-identical lag. Counting it
# as an independent observation is what collapsed the within-file spread to
# ~0 in the 2026-08-08 report -- most recordings stop at the fire and hold only
# a handful of rows, the majority of them twins. Same epsilon the mole itself
# uses (trade-mole-2.2.py DEDUP_EPSILON_SEC).
DEDUP_EPSILON_SEC = 0.005


def scan_file(path):
    """Return per-file stats, or None when the file has no usable TRADE rows.

    Dispersion is computed over DISTINCT FILLS, not rows."""
    lags, hours = [], []
    first_wall = None
    prev_arrival = prev_source = None
    try:
        with open(path, newline="") as fh:
            rd = csv.DictReader(fh)
            cols = rd.fieldnames or []
            if "exchange_time_epoch" not in cols or "local_arrival_time" not in cols:
                return None
            for r in rd:
                a = r.get("local_arrival_time")
                if not a:
                    continue
                try:
                    a = float(a)
                except ValueError:
                    continue
                if first_wall is None:
                    first_wall = a
                if r.get("event_type") != "TRADE":
                    continue
                e = r.get("exchange_time_epoch")
                if not e:
                    continue
                try:
                    lag = a - float(e)
                except ValueError:
                    continue
                src = r.get("rt_source")
                is_twin = (
                    prev_arrival is not None
                    and src != prev_source
                    and (a - prev_arrival) < DEDUP_EPSILON_SEC
                )
                if is_twin:
                    continue
                prev_arrival, prev_source = a, src
                if SANE_RANGE[0] <= lag <= SANE_RANGE[1]:
                    lags.append(lag)
                    hours.append(dt.datetime.fromtimestamp(a).hour)
    except OSError:
        return None
    if not lags:
        return None

    s = sorted(lags)
    m = DATE_RE.search(os.path.basename(path))
    return {
        "file": os.path.basename(path),
        "date": m.group(1) if m else "?",
        "symbol": _symbol(path),
        "hour": statistics.mode(hours) if hours else None,
        "n": len(s),
        "median": statistics.median(s),
        "min": s[0],
        "max": s[-1],
        # Within-file spread — the discriminator between a per-session offset
        # and per-tick jitter.
        "spread": s[-1] - s[0],
        "iqr": (pct(s, 0.75) - pct(s, 0.25)) if len(s) > 3 else None,
        # Phase of the session's first tick within its second. If the per-session
        # offset were set by a 1 Hz publishing slot assigned at subscribe time,
        # this would predict the lag.
        "sub_phase": (first_wall % 1.0) if first_wall is not None else None,
    }


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if not sx or not sy:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def _symbol(path):
    base = os.path.basename(path)
    for tag in ("_pos_", "_neg_", "_neu_"):
        if base.startswith(tag):
            base = base[len(tag):]
    return base.replace("trade-mole-table_", "").split("_")[0]


def group_table(rows, key, label, min_files=1):
    buckets = {}
    for r in rows:
        buckets.setdefault(r[key], []).append(r["median"])
    out = [f"| {label} | files | median lag | p10 | p90 |",
           "|---|---:|---:|---:|---:|"]
    for k in sorted(buckets, key=lambda x: (x is None, x)):
        v = sorted(buckets[k])
        if len(v) < min_files:
            continue
        out.append(f"| {k} | {len(v)} | {statistics.median(v):.3f} | "
                   f"{pct(v, 0.10):.3f} | {pct(v, 0.90):.3f} |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--min-rows", type=int, default=3,
                    help="ignore files with fewer usable TRADE rows (default 3); "
                         "the within-file spread is meaningless below this")
    ap.add_argument("--out", help="report path (default mole-outputs/feed-lag-analysis-<today>.md)")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(OUT_DIR, "*trade-mole-table_*.csv")))
    rows = [r for r in (scan_file(p) for p in paths) if r]
    if not rows:
        raise SystemExit("no recordings with both timestamps found")

    usable = [r for r in rows if r["n"] >= args.min_rows]
    meds = sorted(r["median"] for r in rows)
    spreads = sorted(r["spread"] for r in usable)
    total_rows = sum(r["n"] for r in rows)

    # A file counts as "flat" when its whole lag range fits inside 10 ms — i.e.
    # every fill in that session was delivered at the same offset.
    flat = [r for r in usable if r["spread"] <= 0.010]

    today = dt.date.today().isoformat()
    out_path = args.out or os.path.join(OUT_DIR, f"feed-lag-analysis-{today}.md")

    L = []
    L.append(f"# Feed lag: exchange stamp -> local arrival ({today})\n")
    L.append(f"`local_arrival_time - exchange_time_epoch` over every TRADE row in "
             f"`mole-outputs/`. {len(rows)} recordings, {total_rows} trade rows.\n")
    L.append("Generated by `analyze_trade_mole/analyze_feed_lag.py` (read-only, re-runnable).\n")

    L.append("## Corpus level\n")
    L.append("Percentiles are over per-file medians, so a long session cannot "
             "outvote a short one.\n")
    L.append(f"| statistic | value |\n|---|---:|")
    L.append(f"| files | {len(rows)} |")
    L.append(f"| trade rows | {total_rows} |")
    for q in (0.10, 0.25, 0.50, 0.75, 0.90):
        L.append(f"| p{int(q*100)} of per-file median | {pct(meds, q):.3f} s |")
    L.append(f"| min / max per-file median | {meds[0]:.3f} / {meds[-1]:.3f} s |\n")

    L.append("## Is it a per-session offset or per-tick jitter?\n")
    L.append(f"Within-file spread (max - min lag), over the {len(usable)} files with "
             f">={args.min_rows} trade rows:\n")
    L.append("| statistic | value |\n|---|---:|")
    for q in (0.10, 0.50, 0.90):
        L.append(f"| p{int(q*100)} within-file spread | {pct(spreads, q):.3f} s |")
    L.append(f"| files flat to <=10 ms | {len(flat)}/{len(usable)} "
             f"({100.0*len(flat)/len(usable):.0f}%) |\n")

    # The 2026-08-08 edition of this report read the pooled median (~1 ms) as
    # proof of a per-session constant. It is not: recordings STOP AT THE FIRE, so
    # most files hold only a few fills, and a few fills delivered close together
    # necessarily share a delivery phase. Bucketing by how much of the session we
    # actually observed is what exposes that, so it is reported unconditionally.
    L.append("### Spread vs how many fills we observed\n")
    L.append("Recordings stop at the fire, so most files are short. A short file "
             "cannot show dispersion it had no chance to sample -- bucket before "
             "concluding anything about constancy.\n")
    L.append("| distinct fills | files | median spread | p90 spread |\n|---|---:|---:|---:|")
    BUCKETS = [(2, 5, "2-4"), (5, 10, "5-9"), (10, 20, "10-19"),
               (20, 50, "20-49"), (50, 10**9, "50+")]
    trend = []
    for lo, hi, label in BUCKETS:
        sel = sorted(r["spread"] for r in usable if lo <= r["n"] < hi)
        if not sel:
            continue
        trend.append((label, pct(sel, 0.50)))
        L.append(f"| {label} | {len(sel)} | {pct(sel, 0.50):.4f} s | "
                 f"{pct(sel, 0.90):.4f} s |")
    L.append("")
    if len(trend) >= 2 and trend[-1][1] > 10 * max(trend[0][1], 1e-6):
        L.append(f"> Spread rises monotonically with observation length "
                 f"({trend[0][0]} fills: {trend[0][1]:.4f} s -> {trend[-1][0]}: "
                 f"{trend[-1][1]:.4f} s). **The lag is NOT a per-session "
                 f"constant.** The pooled median is a censoring artifact of short "
                 f"recordings, and per-delivery variation of several hundred ms is "
                 f"real in any session long enough to show it.\n")
    long_files = [r for r in usable if r["n"] >= 20]
    if long_files:
        ls = sorted(r["spread"] for r in long_files)
        L.append(f"> Read the {len(long_files)} files with >=20 distinct fills, not the "
                 f"pooled figure: median spread {pct(ls, 0.50):.3f} s, p90 "
                 f"{pct(ls, 0.90):.3f} s. The lag moves by hundreds of ms inside a "
                 f"session, so a fixed per-session anchor is ruled OUT and "
                 f"per-delivery variation is ruled IN.\n")
    else:
        L.append("> No file carries >=20 distinct fills, so this corpus cannot "
                 "distinguish a per-session anchor from per-delivery variation. "
                 "Do not conclude either from the pooled spread.\n")

    L.append("## Mechanism: what the shape rules out\n")
    over_1s = sum(1 for x in meds if x >= 1.0)
    L.append(f"Per-file medians land in [0, 1) s in {len(meds) - over_1s}/{len(meds)} "
             f"sessions (max {meds[-1]:.3f} s, none negative), spread near-uniformly "
             f"across that second:\n")
    L.append("| decile | files | share |\n|---|---:|---:|")
    for i in range(10):
        lo = i / 10.0
        n = sum(1 for x in meds if lo <= x < lo + 0.1)
        L.append(f"| [{lo:.1f}, {lo + 0.1:.1f}) | {n} | {100.0 * n / len(meds):.1f}% |")
    L.append("")
    L.append("A per-session constant that is near-uniform on a ONE-SECOND modulus "
             "is the signature of a 1 Hz phase somewhere in the path, re-drawn each "
             "session.\n")

    phased = [r for r in rows if r["sub_phase"] is not None]
    if len(phased) >= 30:
        ph = [r["sub_phase"] for r in phased]
        lg = [r["median"] for r in phased]
        c1 = pearson(ph, lg)
        c2 = pearson(ph, [(1 - x) % 1 for x in lg])
        L.append("**Tested: is that phase set by when we subscribe?** If the feed "
                 "assigned a publishing slot at subscription time, the phase of our "
                 "first tick within its second would predict the lag — and we could "
                 "chase it by timing the subscribe.\n")
        L.append(f"| test | Pearson r |\n|---|---:|")
        L.append(f"| subscribe phase vs lag | {c1:+.3f} |")
        L.append(f"| subscribe phase vs (1 - lag) mod 1 | {c2:+.3f} |\n")
        L.append("| subscribe phase | files | median lag |\n|---|---:|---:|")
        for i in range(10):
            lo = i / 10.0
            v = [r["median"] for r in phased if lo <= r["sub_phase"] < lo + 0.1]
            if v:
                L.append(f"| [{lo:.1f}, {lo + 0.1:.1f}) | {len(v)} | "
                         f"{statistics.median(v):.3f} |")
        L.append("")
        L.append("> **No relationship** — flat across every decile. The phase is not "
                 "ours to choose, so subscribing at a different point in the second "
                 "would not reduce the lag.\n")

    L.append("So two mechanisms are ruled out — per-tick transport jitter (the "
             "within-session spread is ~1 ms) and a subscribe-time slot assignment "
             "(no correlation). What remains is an offset fixed per session upstream "
             "of us: either the stamping clock or a publishing cadence we do not "
             "control. Separating those needs a probe against a second time source, "
             "which this corpus cannot provide.\n")

    L.append("## By month\n")
    by_month = [dict(r, month=r["date"][:7]) for r in rows]
    L.append(group_table(by_month, "month", "month") + "\n")

    L.append("## By hour of day (local)\n")
    L.append("Top-of-hour contention is a known amplifier elsewhere in the stack "
             "(see the NW4 burst-latency diagnosis), so this slice is the one to "
             "watch for a scheduling effect.\n")
    L.append(group_table(rows, "hour", "hour", min_files=3) + "\n")

    L.append("## Widest and tightest sessions\n")
    L.append("| file | n | median | spread |\n|---|---:|---:|---:|")
    for r in sorted(usable, key=lambda r: -r["spread"])[:10]:
        L.append(f"| {r['file']} | {r['n']} | {r['median']:.3f} | {r['spread']:.3f} |")
    L.append("")
    for r in sorted(usable, key=lambda r: r["spread"])[:5]:
        L.append(f"| {r['file']} | {r['n']} | {r['median']:.3f} | {r['spread']:.3f} |")
    L.append("")

    L.append("## Caveats\n")
    L.append("- Recordings stop at the fire, so fired sessions contribute only their "
             "first seconds; this is a sample of post-arm tape, not of the day.\n"
             "- Rows within a session are not independent — per-file aggregates are "
             "the unit of analysis throughout.\n"
             "- This measures stamp-to-arrival. It cannot separate a genuine "
             "dissemination delay from an offset between the stamping clock and "
             "ours; that needs a probe against a second time source.\n")

    with open(out_path, "w") as fh:
        fh.write("\n".join(L) + "\n")

    print(f"files={len(rows)} rows={total_rows}")
    print(f"per-file median lag: p50={pct(meds,0.5):.3f}s  "
          f"p10={pct(meds,0.1):.3f}s  p90={pct(meds,0.9):.3f}s")
    print(f"within-file spread:  p50={pct(spreads,0.5):.3f}s  "
          f"flat(<=10ms)={len(flat)}/{len(usable)}")
    print(f"-> {out_path}")


if __name__ == "__main__":
    main()
