#!/usr/bin/env python3
"""Rank surge-detection predictors on the labeled June 8-12 mole-output set.

Reads the `_augmented.csv` files in `mole-outputs/` (positives that SHOULD have
fired sub-second, negatives that should NEVER fire) and, for every numeric column,
finds the best *clean separator*: a threshold that catches as many positives as
early as possible while crossing on **zero** negative files (whole-file rule).

Two user-specified lead signals are reported explicitly at the top:
  1. snapshot     : prev_trd_time_gap     (small  => the tape traded just before
                    we hit record => frequency already high; known at t=0)
  2. progressive  : ratio_baseline_over_measured_ITI (large => measured trade
                    frequency rising above the historical baseline)
plus their OR, then a full leaderboard of every other column for comparison, and
a diagnostic of the live incumbent rule `quote_updates_10s >= 11`.

Read-only: writes one markdown report, modifies nothing else.

Usage:
    path/to/venv/bin/python analyze_surge_predictors.py
"""

import csv
import glob
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "mole-outputs")
REPORT = os.path.join(OUT_DIR, "surge-predictor-analysis-2026-06-12.md")

MIN_DATE = "2026-06-08"
MAX_DATE = "2026-06-12"
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

FAST_SEC = 1.0  # a positive "fired fast" if it crosses within this many seconds

# The strict whole-file "never cross" rule is too harsh for ITI/baseline-derived
# metrics (one hard negative like SMCI can sink an otherwise good signal). For
# those, also sweep how many positives we catch if we TOLERATE up to MAX_TOL
# negative false-fires. The leaderboard stays at zero-tolerance; this is extra.
MAX_TOL = 3
# ITI- and baseline-derived metrics to show the tolerance sweep for, with dir.
TOL_METRICS = [
    ("prev_trd_time_gap", True),
    ("bsln_over_prev_trd_gap", False),
    ("ratio_baseline_over_measured_ITI", False),
    ("measured_ITIsec", True),
    ("accel_2s_vs_hist_baseline", False),
]

# Incumbent live rule (trade-mole-2.1.py:891-911 + defaults at :136-137).
INCUMBENT_COL = "quote_updates_10s"
INCUMBENT_T = 11.0

# The lead signals.
SNAPSHOT_COL = "prev_trd_time_gap"                     # lower is surge
NORM_SNAPSHOT_COL = "bsln_over_prev_trd_gap"           # native column, higher is surge
PROGRESSIVE_COL = "ratio_baseline_over_measured_ITI"    # higher is surge

# --- Sentinel / legacy-value handling ---------------------------------------
# 44444 is the universe-pipeline "baseline fetch FAILED" sentinel
# (trade-mole-2.1.py:139, TRADE_SIZE_BASELINE_SENTINEL). It pollutes every
# baseline-normalized column. Discard it everywhere, and on any file whose ITI
# or trade-size baseline is sentinel/<=0, make the dependent columns ABSTAIN.
SENTINELS = (44444.0,)


def is_sentinel(v):
    return v is not None and any(abs(v - s) < 1e-6 for s in SENTINELS)


# Columns derived from the historical ITI baseline (abstain if ITI baseline bad).
ITI_DERIVED = {
    "ratio_baseline_over_measured_ITI",
    "bsln_over_prev_trd_gap",
    "ratio_baseline_collapse_ratio", "ratio_baseline_collapse_diff",
    "ratio_baseline_collapse_velocity",
    "hist_baseline_trade_rate", "hist_baseline_avg_iti",
    "accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline",
    "accel_5s_vs_hist_baseline",
}
# Columns derived from the historical trade-SIZE baseline (abstain if it is bad).
SIZE_DERIVED_PREFIXES = (
    "size_ratio_", "buy_size_ratio_", "signed_size_ratio_",
    "large_trade_count_", "large_trade_volume_frac_",
)
SIZE_DERIVED_EXACT = {"hist_baseline_trade_size"}


def is_size_derived(col):
    return col in SIZE_DERIVED_EXACT or col.startswith(SIZE_DERIVED_PREFIXES)

# Columns that parse as floats but are useless / misleading as thresholds:
# raw clocks, monotonically-increasing cumulatives, identifiers, flags.
DENYLIST = {
    "local_arrival_time", "local_mono_time", "exchange_time_epoch", "tick_type",
    "Time", "rt_time_ms", "value", "surge_detected",
    "cum_volume", "cum_trade_count", "cum_dollar_volume", "rt_total_volume",
    "tws_trade_count", "session_age_sec",
    # Raw baseline LEVELS are static per-ticker constants (liquidity-class
    # proxies), not surge dynamics — using them as a trigger is leakage. The
    # baseline-normalized ratios keep the useful part.
    "hist_baseline_avg_iti", "hist_baseline_trade_rate", "hist_baseline_trade_size",
}

# Columns we know read "small = surge"; everything else defaults to testing both
# directions and keeping whichever separates better.
LOWER_IS_SURGE_HINT = {
    "prev_trd_time_gap", "measured_ITIsec", "inter_trade_time_sec",
}


def _f(row, key):
    v = row.get(key, "")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def discover():
    found = []
    for pat in ("_positive_*_augmented.csv", "_negative_*_augmented.csv"):
        found.extend(glob.glob(os.path.join(OUT_DIR, pat)))
    out = []
    for p in sorted(set(found)):
        m = DATE_RE.search(os.path.basename(p))
        if not m or not (MIN_DATE <= m.group(1) <= MAX_DATE):
            continue
        out.append(p)
    return out


def short_name(path):
    """Compact ticker_date label for tables."""
    b = os.path.basename(path)
    b = re.sub(r"^_(positive|negative)_", "", b)
    b = b.replace("trade-mole-table_", "").replace("_augmented.csv", "")
    return b


def first_good_baseline(rows, key):
    """First positive, non-sentinel value of a baseline column (else None)."""
    for r in rows:
        v = _f(r, key)
        if v is not None and v > 0 and not is_sentinel(v):
            return v
    return None


def load(path):
    """Return a per-file record with a value series per candidate column.

    Sentinel / legacy values are discarded: any raw 44444 cell is dropped, and
    every column derived from a bad (sentinel/<=0/missing) ITI or trade-size
    baseline is forced to ABSTAIN for the whole file.
    """
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    t0 = None
    for r in rows:
        t0 = _f(r, "local_arrival_time")
        if t0 is not None:
            break

    iti_base = first_good_baseline(rows, "hist_baseline_avg_iti")
    size_base = first_good_baseline(rows, "hist_baseline_trade_size")
    iti_bad = iti_base is None
    size_bad = size_base is None

    cand = [c for c in fields if c not in DENYLIST]
    # Columns to abstain on for this file (bad baseline -> derived value is junk).
    abstain = set()
    if iti_bad:
        abstain |= ITI_DERIVED
    if size_bad:
        abstain |= {c for c in cand if is_size_derived(c)}

    pttg = None
    for r in rows:
        v = _f(r, SNAPSHOT_COL)
        if v is not None:
            pttg = v
            break

    series = {c: [] for c in cand}

    n_trades = 0
    for r in rows:
        if r.get("event_type") != "TRADE":
            continue
        n_trades += 1
        ta = _f(r, "local_arrival_time")
        t_rel = (ta - t0) if (ta is not None and t0 is not None) else None
        if t_rel is None:
            continue
        for c in cand:
            if c in abstain:
                continue
            v = _f(r, c)
            if v is None or is_sentinel(v):
                continue
            series[c].append((t_rel, n_trades, v))

    return {
        "path": path,
        "name": short_name(path),
        "label": "positive" if os.path.basename(path).startswith("_positive_") else "negative",
        "fields": fields,
        "n_trades": n_trades,
        "series": series,
        "prev_trd_time_gap": pttg,
        "iti_base": iti_base,
        "size_base": size_base,
        "iti_bad": iti_bad,
        "size_bad": size_bad,
    }


def crosses(val, T, lower):
    return (val <= T) if lower else (val >= T)


def first_cross(serie, T, lower):
    """Earliest (t_rel, trade_idx) crossing in a value series, or None."""
    for t_rel, idx, v in serie:
        if crosses(v, T, lower):
            return (t_rel, idx)
    return None


def any_cross(serie, T, lower):
    return any(crosses(v, T, lower) for _t, _i, v in serie)


def evaluate(col, T, lower, positives, negatives):
    """Score a (column, threshold, direction) operating point."""
    fired, fast, missed, t_fires = 0, 0, [], []
    for f in positives:
        hit = first_cross(f["series"].get(col, []), T, lower)
        if hit is None:
            missed.append(f["name"])
        else:
            fired += 1
            t_fires.append(hit[0])
            if hit[0] <= FAST_SEC:
                fast += 1
    neg_cross = [f["name"] for f in negatives if any_cross(f["series"].get(col, []), T, lower)]
    return {
        "col": col, "T": T, "lower": lower,
        "fired": fired, "fast": fast, "missed": missed,
        "t_fires": t_fires, "neg_cross": neg_cross,
        "clean": len(neg_cross) == 0,
        "median_t": statistics.median(t_fires) if t_fires else None,
    }


def pos_values(col, positives):
    vals = set()
    for f in positives:
        for _t, _i, v in f["series"].get(col, []):
            vals.add(v)
    return vals


def neg_extreme(col, negatives, lower):
    """For 'never cross whole file': the negative value a clean T must clear.

    higher-is-surge: clean iff T > max(neg vals) -> return that max.
    lower-is-surge : clean iff T < min(neg vals) -> return that min.
    None if no negative ever has a value (signal abstains on all negatives).
    """
    extreme = None
    for f in negatives:
        for _t, _i, v in f["series"].get(col, []):
            if extreme is None:
                extreme = v
            else:
                extreme = min(extreme, v) if lower else max(extreme, v)
    return extreme


def neg_extremes_sorted(col, negatives, lower):
    """Per-negative extreme value (max if higher-is-surge, min if lower), sorted
    so that index k is the bar a threshold must clear to tolerate k false-fires.
    higher: descending (allow the k highest to cross). lower: ascending.
    """
    ext = []
    for f in negatives:
        vals = [v for _t, _i, v in f["series"].get(col, [])]
        if vals:
            ext.append(min(vals) if lower else max(vals))
    ext.sort(reverse=not lower)
    return ext


def best_with_tol(col, lower, positives, negatives, k):
    """Best operating point for one column+direction allowing up to k negative
    false-fires (k=0 == best_clean). Returns an evaluate() dict, or None."""
    pv = pos_values(col, positives)
    if not pv:
        return None
    ext = neg_extremes_sorted(col, negatives, lower)
    # The bar to clear: tolerate the k most-extreme negatives, clear the rest.
    bar = ext[k] if k < len(ext) else None  # None => no negative left to clear
    if bar is None:
        T = max(pv) if lower else min(pv)
    elif lower:
        cands = [v for v in pv if v < bar]
        if not cands:
            return None
        T = max(cands)
    else:
        cands = [v for v in pv if v > bar]
        if not cands:
            return None
        T = min(cands)
    return evaluate(col, T, lower, positives, negatives)


def best_clean(col, lower, positives, negatives):
    """The optimal clean (zero-false-fire) operating point, or None."""
    return best_with_tol(col, lower, positives, negatives, 0)


def best_for_column(col, positives, negatives):
    """Best clean operating point across both directions (or None)."""
    options = []
    dirs = (True,) if col in LOWER_IS_SURGE_HINT else (False, True)
    for lower in dirs:
        r = best_clean(col, lower, positives, negatives)
        if r:
            options.append(r)
    if not options:
        return None
    # Prefer more fast catches, then more total, then earlier median.
    options.sort(key=lambda r: (r["fast"], r["fired"], -(r["median_t"] or 1e9)), reverse=True)
    return options[0]


def fmt_t(x):
    return f"{x:.3f}s" if x is not None else "-"


def fmt_T(x):
    return f"{x:.4g}"


def main():
    files = discover()
    positives = [load(p) for p in files if os.path.basename(p).startswith("_positive_")]
    negatives = [load(p) for p in files if os.path.basename(p).startswith("_negative_")]
    if not positives or not negatives:
        print("Not enough labeled files found.")
        return 1
    nP, nN = len(positives), len(negatives)

    # Candidate column universe = union of all files' candidate columns.
    cols = set()
    for f in positives + negatives:
        cols.update(f["series"].keys())

    results = []
    for c in sorted(cols):
        r = best_for_column(c, positives, negatives)
        if r:
            results.append(r)
    results.sort(key=lambda r: (r["fast"], r["fired"], -(r["median_t"] or 1e9)), reverse=True)

    # --- Lead signals + OR -------------------------------------------------
    # Three lead signals: raw snapshot, baseline-normalized snapshot, progressive.
    leads = [
        ("snapshot (raw)", SNAPSHOT_COL, True),
        ("snapshot vs baseline_ITI", NORM_SNAPSHOT_COL, False),
        ("progressive", PROGRESSIVE_COL, False),
    ]
    lead_res = {}
    for tag, col, lower in leads:
        lead_res[tag] = best_clean(col, lower, positives, negatives)

    # OR over the two baseline-normalized lead signals (both sentinel-guarded).
    or_components = [(col, lower, lead_res[tag]["T"])
                     for tag, col, lower in leads
                     if tag != "snapshot (raw)" and lead_res[tag]]
    or_rows = []
    or_fast = or_fired = 0
    if or_components:
        for f in positives:
            best_t, best_via = None, "-"
            for col, lower, T in or_components:
                hit = first_cross(f["series"].get(col, []), T, lower)
                if hit and (best_t is None or hit[0] < best_t):
                    best_t, best_via = hit[0], col
            if best_t is not None:
                or_fired += 1
                if best_t <= FAST_SEC:
                    or_fast += 1
            or_rows.append((f["name"], fmt_t(best_t), best_via))

    # --- Incumbent diagnostic ---------------------------------------------
    inc = evaluate(INCUMBENT_COL, INCUMBENT_T, False, positives, negatives)

    # --- Write report ------------------------------------------------------
    L = []
    w = L.append
    w("# Surge-predictor analysis — labeled set 2026-06-08..2026-06-12\n")
    w(f"Scope: **{nP} positive**, **{nN} negative** `_augmented.csv` files in "
      f"`mole-outputs/`. A *positive* should fire within **{FAST_SEC:.0f}s** of "
      "recording start; a *negative* must **never** cross (whole-file rule). "
      "Threshold = earliest TRADE row crossing; time = seconds since first recorded row.\n")

    w("## 1. Lead signals (user-specified) + OR\n")
    w("All baseline-normalized signals **discard the 44444 sentinel** and abstain on "
      "files with a missing/<=0 baseline.\n")
    w("| signal | column | dir | threshold | pos fast | pos total | neg false-fire | median t |")
    w("|---|---|---|---|---|---|---|---|")
    for tag, col, lower in leads:
        r = lead_res[tag]
        if r:
            w(f"| {tag} | `{r['col']}` | {'≤' if r['lower'] else '≥'} | "
              f"{fmt_T(r['T'])} | {r['fast']}/{nP} | {r['fired']}/{nP} | "
              f"{len(r['neg_cross'])} | {fmt_t(r['median_t'])} |")
        else:
            w(f"| {tag} | `{col}` | {'≤' if lower else '≥'} | "
              "no clean separator | | | | |")
    if or_components:
        names = " or ".join(f"`{c}`{'≤' if lo else '≥'}{fmt_T(T)}" for c, lo, T in or_components)
        w(f"| **OR** | {names} | | | **{or_fast}/{nP}** | **{or_fired}/{nP}** | 0 | |")
    w("")
    if or_rows:
        w("Per-positive OR outcome (which signal fired first):\n")
        w("| positive | t_fire | via |")
        w("|---|---|---|")
        for name, t, via in or_rows:
            w(f"| {name} | {t} | {via} |")
        w("")

    w("## 2. Leaderboard — best clean separator per column\n")
    w("Ranked by positives caught sub-second, then total caught, then earliest "
      "median fire. Only columns with at least one clean threshold appear.\n")
    w("| # | column | dir | threshold | pos fast | pos total | median t | positives missed |")
    w("|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(results[:20], 1):
        miss = ", ".join(r["missed"]) if r["missed"] else "—"
        w(f"| {i} | `{r['col']}` | {'≤' if r['lower'] else '≥'} | {fmt_T(r['T'])} | "
          f"{r['fast']}/{nP} | {r['fired']}/{nP} | {fmt_t(r['median_t'])} | {miss} |")
    w("")

    w("## 3. ITI / baseline metrics — false-positive tolerance sweep\n")
    w("For ITI- and baseline-derived metrics the strict zero-false-fire bar is "
      "often set by a single hard negative. Each row below shows the best threshold "
      f"if you **tolerate k negative false-fires** (k = 0..{MAX_TOL}): how many "
      "positives it then catches (sub-second / total) and which negatives fire. "
      "Sentinel-baseline files still abstain throughout.\n")
    for col, lower in TOL_METRICS:
        if not any(f["series"].get(col) for f in positives + negatives):
            continue
        w(f"**`{col}`** ({'≤ lower = surge' if lower else '≥ higher = surge'})\n")
        w("| tolerate k | threshold | pos fast | pos total | neg false-fires |")
        w("|---|---|---|---|---|")
        prev = None
        for k in range(MAX_TOL + 1):
            r = best_with_tol(col, lower, positives, negatives, k)
            if r is None:
                w(f"| {k} | no threshold | | | |")
                continue
            nf = r["neg_cross"]
            sig = (r["T"], r["fired"], tuple(sorted(nf)))
            if sig == prev:  # no change from a higher tolerance budget
                w(f"| {k} | (same as k={k-1}) | | | |")
                continue
            prev = sig
            w(f"| {k} | {fmt_T(r['T'])} | {r['fast']}/{nP} | {r['fired']}/{nP} | "
              f"{(', '.join(nf) + f' ({len(nf)})') if nf else '0'} |")
        w("")

    w("## 4. Incumbent rule diagnostic — `quote_updates_10s >= 11`\n")
    w(f"- Positives fired sub-second: **{inc['fast']}/{nP}**; "
      f"fired at all: **{inc['fired']}/{nP}**.")
    w(f"- Positives missed: {', '.join(inc['missed']) if inc['missed'] else '—'}")
    w(f"- Negative false-fires: {', '.join(inc['neg_cross']) if inc['neg_cross'] else '—'} "
      f"({len(inc['neg_cross'])}).")
    w(f"- Median fire time on caught positives: {fmt_t(inc['median_t'])}.\n")

    w("## 5. Per-file appendix\n")
    w("| label | file | trades | prev_trd_time_gap | min measured_ITIsec | "
      "max ratio_base/ITI | max quote_updates_10s | incumbent fired |")
    w("|---|---|---|---|---|---|---|---|")
    inc_fire = {}
    for f in positives + negatives:
        hit = first_cross(f["series"].get(INCUMBENT_COL, []), INCUMBENT_T, False)
        inc_fire[f["path"]] = fmt_t(hit[0]) if hit else "no"
    for f in sorted(positives + negatives, key=lambda x: (x["label"], x["name"])):
        ser = f["series"]
        mi = [v for _t, _i, v in ser.get("measured_ITIsec", [])]
        rr = [v for _t, _i, v in ser.get(PROGRESSIVE_COL, [])]
        qu = [v for _t, _i, v in ser.get(INCUMBENT_COL, [])]
        w(f"| {f['label']} | {f['name']} | {f['n_trades']} | "
          f"{fmt_T(f['prev_trd_time_gap']) if f['prev_trd_time_gap'] is not None else '-'} | "
          f"{fmt_T(min(mi)) if mi else '-'} | {fmt_T(max(rr)) if rr else '-'} | "
          f"{fmt_T(max(qu)) if qu else '-'} | {inc_fire[f['path']]} |")
    w("")

    w("## 6. Caveats\n")
    w(f"- Small **in-sample** set ({nP+nN} files): thresholds are fit and judged on the "
      "same data, so a 'clean' separator may not generalize. Treat the leaderboard as a "
      "hypothesis ranking, not a validated rule.")
    iti_sent = [f"{f['name']} ({f['label'][:3]})" for f in positives + negatives if f["iti_bad"]]
    size_sent = [f"{f['name']} ({f['label'][:3]})" for f in positives + negatives if f["size_bad"]]
    w(f"- **ITI-baseline sentinel/invalid (44444 or <=0)** → all ITI-derived columns "
      f"(incl. progressive & normalized snapshot) abstain on: "
      f"{', '.join(iti_sent) if iti_sent else '—'}.")
    w(f"- **Trade-size-baseline sentinel/invalid** → all size-ratio / large-trade columns "
      f"abstain on: {', '.join(size_sent) if size_sent else '—'}.")
    abst_snap = [f["name"] for f in positives + negatives if f["prev_trd_time_gap"] is None]
    w(f"- Raw snapshot abstains (blank prev_trd_time_gap, missing tick-45 anchor) on: "
      f"{', '.join(abst_snap) if abst_snap else '—'}.")
    w("- Absolute-price / dollar columns appearing high in the leaderboard are not "
      "cross-ticker comparable and are likely overfit; prefer baseline-normalized signals.")

    with open(REPORT, "w") as fh:
        fh.write("\n".join(L) + "\n")

    # --- Console summary ---------------------------------------------------
    print(f"Analyzed {nP} positive + {nN} negative files.")
    print(f"Report: {REPORT}\n")
    print("Top 8 clean separators (col | dir | T | fast/total | median t):")
    for r in results[:8]:
        print(f"  {r['col']:<34} {'<=' if r['lower'] else '>='} {fmt_T(r['T']):>8}  "
              f"{r['fast']}/{nP} fast, {r['fired']}/{nP} total  med={fmt_t(r['median_t'])}")
    print(f"\nIncumbent quote_updates_10s>=11: {inc['fast']}/{nP} fast, "
          f"{inc['fired']}/{nP} total, {len(inc['neg_cross'])} neg false-fire")
    print("\nLead signals (sentinel-guarded):")
    for tag, col, lower in leads:
        r = lead_res[tag]
        if r:
            print(f"  {tag:<26} {'<=' if r['lower'] else '>='} {fmt_T(r['T']):>10}  "
                  f"{r['fast']}/{nP} fast, {r['fired']}/{nP} total")
        else:
            print(f"  {tag:<26} no clean separator")
    if or_components:
        print(f"  {'OR(normalized+progressive)':<26} {'':>13}  {or_fast}/{nP} fast, "
              f"{or_fired}/{nP} total, 0 neg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
