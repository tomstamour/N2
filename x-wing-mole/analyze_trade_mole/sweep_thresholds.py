#!/usr/bin/env python3
"""Sweep trade-mole trigger constants end-to-end through the REAL should_fire().

Every threshold in trade-mole-2.2.py is a module-level constant, and
`replay_mole.replay_file` can override module constants before constructing the
app — so a candidate rule set can be evaluated by replaying the labelled corpus
through the actual detector rather than a re-implementation of it. That matters
here: the 2026-07-30 sweep and `analyze_trigger_rules.py` both re-implement the
OR, and their copies have drifted from the live rules (they still cut Rule C at
100 where 2.2 cuts at 1550).

Intended use is the recalibration that has to accompany the two recorded-feature
fixes (DEDUP_EXCLUDE_TWIN_FROM_COUNTS / RATE_NORMALIZE_BY_SESSION_AGE): those
change the scale of every count and rate column, so A's $60, D's 0.28/3 and E's
10/1.2% no longer mean what they meant when they were fit.

Protocol follows the operator's 2026-07-30 decisions: a positive counts as
caught if it fires anywhere in its recording; time-to-fire is a tiebreaker only;
neu is reported but does not drive the choice.

CENSORING (unchanged, and it bounds everything below): recordings stop at the
live fire, so a candidate STRICTER than the live rules cannot be observed firing
later than that point — pos losses read pessimistic, neg kills optimistic.

Usage:
    # one constant, holding the rest at their current values
    python sweep_thresholds.py --fixes both --sweep SURGE_A_MIN_DOLLAR_RATE_5S \\
        --grid 10,20,30,40,60,80,120

    # two at once (cartesian)
    python sweep_thresholds.py --fixes both \\
        --sweep SURGE_ITI_COLLAPSE_MAX_SEC --grid 0.28,0.4,0.6,0.8 \\
        --sweep2 SURGE_ITI_COLLAPSE_MIN_TRADES_5S --grid2 2,3,4

    # just score the current constants
    python sweep_thresholds.py --fixes both
"""

import argparse
import glob
import logging
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import replay_mole as R  # noqa: E402

OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
MIN_DATE = "2026-07-09"

FIX_SETS = {
    "none": {},
    "twin": {"DEDUP_EXCLUDE_TWIN_FROM_COUNTS": True},
    "rate": {"RATE_NORMALIZE_BY_SESSION_AGE": True},
    "both": {"DEDUP_EXCLUDE_TWIN_FROM_COUNTS": True,
             "RATE_NORMALIZE_BY_SESSION_AGE": True},
}


def labelled_files(min_date=MIN_DATE):
    out = []
    for tag in ("_pos_", "_neg_", "_neu_"):
        for p in sorted(glob.glob(os.path.join(OUT_DIR, f"{tag}*.csv"))):
            m = DATE_RE.search(os.path.basename(p))
            if m and m.group(1) >= min_date:
                out.append((tag.strip("_"), p))
    return out


def score(files, mole, overrides, log):
    """Replay every file under `overrides`; return {label: (n, fired, ttfs)}."""
    agg = {}
    for label, path in files:
        app, fire = R.replay_file(path, mole, overrides, stop_at_trigger=True, log=log)
        if app is None:
            continue
        n, fired, ttfs = agg.get(label, (0, 0, []))
        n += 1
        if fire:
            fired += 1
            start = app._session_start_mono
            rec = next((x for x in app.records if x.get("surge_detected")), None)
            if rec is not None and rec.get("session_age_sec") is not None:
                ttfs.append(rec["session_age_sec"])
            elif start is not None:
                ttfs.append(0.0)
        agg[label] = (n, fired, ttfs)
    return agg


def fmt(agg):
    cells = []
    for lab in ("pos", "neu", "neg"):
        n, fired, _ = agg.get(lab, (0, 0, []))
        cells.append(f"{fired}/{n}" if n else "-")
    ttfs = agg.get("pos", (0, 0, []))[2]
    med = f"{statistics.median(ttfs):.2f}" if ttfs else "-"
    return cells, med


def parse_grid(s):
    vals = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if part in ("True", "False"):
            vals.append(part == "True")
        else:
            vals.append(float(part) if ("." in part or "e" in part) else int(part))
    return vals


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fixes", choices=sorted(FIX_SETS), default="both",
                    help="which recorded-feature corrections to apply (default both)")
    ap.add_argument("--module", default=R.DEFAULT_MODULE)
    ap.add_argument("--sweep", help="constant to sweep")
    ap.add_argument("--grid", help="comma-separated values for --sweep")
    ap.add_argument("--sweep2", help="second constant (cartesian with --sweep)")
    ap.add_argument("--grid2", help="comma-separated values for --sweep2")
    ap.add_argument("--min-date", default=MIN_DATE)
    ap.add_argument("--set", action="append", dest="sets", metavar="NAME=VALUE",
                    help="hold an extra constant at VALUE for the whole sweep")
    args = ap.parse_args()

    logging.basicConfig(level=logging.CRITICAL)
    log = logging.getLogger("sweep")
    log.setLevel(logging.CRITICAL)

    mole = R.load_mole(args.module)
    files = labelled_files(args.min_date)
    if not files:
        raise SystemExit("no labelled recordings found")

    base = dict(FIX_SETS[args.fixes])
    base.update(R.parse_set(args.sets))

    held = ", ".join(f"{k}={v}" for k, v in sorted(base.items())) or "(none)"
    print(f"corpus: {len(files)} labelled recordings >= {args.min_date}")
    print(f"fixes/holds: {held}\n")

    if not args.sweep:
        agg = score(files, mole, base, log)
        cells, med = fmt(agg)
        print(f"{'pos':>8} {'neu':>8} {'neg':>8} {'medTTF':>8}")
        print(f"{cells[0]:>8} {cells[1]:>8} {cells[2]:>8} {med:>8}")
        return

    grid = parse_grid(args.grid or "")
    grid2 = parse_grid(args.grid2 or "") if args.sweep2 else [None]
    if not grid:
        raise SystemExit("--sweep needs --grid")

    hdr = f"{args.sweep:>34}"
    if args.sweep2:
        hdr += f" {args.sweep2:>34}"
    print(f"{hdr} {'pos':>8} {'neu':>8} {'neg':>8} {'medTTF':>8}")

    for v in grid:
        for v2 in grid2:
            ov = dict(base)
            ov[args.sweep] = v
            if args.sweep2:
                ov[args.sweep2] = v2
            agg = score(files, mole, ov, log)
            cells, med = fmt(agg)
            line = f"{v!s:>34}"
            if args.sweep2:
                line += f" {v2!s:>34}"
            print(f"{line} {cells[0]:>8} {cells[1]:>8} {cells[2]:>8} {med:>8}")


if __name__ == "__main__":
    main()
