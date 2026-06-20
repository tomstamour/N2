#!/usr/bin/env python3
"""
Surge-detection threshold analysis for trade-mole recordings (June 8-11 re-fit).

Finds which column + threshold best separates labeled "_positive" (real surge
that should have fired x-wing) from "_negative" (no surge) recordings in
mole-outputs/, and diagnoses why the *current* price-momentum should_fire() rule
missed many positives.

Pure stdlib (csv/statistics) -- no ibapi needed; run with system python3:
    /usr/bin/python3 analyze_iti_threshold.py

Decisions baked in (confirmed with user, 2026-06-11):
  * Analysis window: only labeled files dated 2026-06-08 .. 2026-06-11.
  * Include BOTH the older `.txt` recordings and the new clerk `.csv`
    (trade-mole-table) recordings.
  * EXCLUDE any file whose baseline carries the 44444 "fetch failed" sentinel
    (hist_baseline_avg_iti == 44444 or hist_baseline_trade_size == 44444) -- those
    poison the baseline-relative columns.
  * Also evaluate the baseline-derived columns the user asked about: the
    TradeSize-baseline family (size_ratio_*, signed_size_ratio_*, ...) and the
    ITI-baseline family (accel_*_vs_hist_baseline, accel_*_vs_10s). A coverage
    gate prevents poorly-populated columns from winning on a tiny sample.
  * Optimize the recommendation for MAX RECALL: rank by TPR, then earliest
    median first-cross latency, then TNR. Report (not forbid) false positives.
  * Fire rule: single-row crossing. Timed positives ("_positive-HH:MM:SS.mmm_..")
    use rows at/after that local time; everything else uses the whole file.
"""

import csv
import os
import re
from datetime import datetime, time as dtime
from statistics import median

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mole-outputs")

# Only labeled files from this date window are analysed.
DATE_RE = re.compile(r"2026-06-(08|09|10|11)")

# Time/identity columns excluded from feature analysis (per request).
EXCLUDE_TIME = {"local_arrival_time", "local_arrival_iso", "Time"}

# The pipeline writes 44444 when a historical baseline fetch was ATTEMPTED but
# FAILED. Such files are dropped entirely (their accel_* columns are garbage).
SENTINEL = 44444.0
SENTINEL_COLS = ("hist_baseline_avg_iti", "hist_baseline_trade_size")

# A column must be non-null on at least this fraction of files in BOTH classes
# to be eligible for ranking (keeps sparse baseline columns from winning on a
# handful of files).
COVERAGE_GATE = 0.60

WINDOWS = ["1s", "2s", "3s", "4s", "5s", "10s"]

# Higher value => more surge-like.
COLS_HIGHER = (
    # --- trade-frequency family (cross-version: in old .txt and new .csv) ---
    [f"trade_rate_{w}" for w in WINDOWS]
    + [f"trades_in_{w}" for w in WINDOWS]
    + [f"quote_updates_{w}" for w in WINDOWS]
    + [f"dollar_rate_{w}" for w in WINDOWS]
    + ["max_cluster_200ms_in_5s"]
    # --- ITI-baseline-relative (user asked to consider) ---
    + ["accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s"]
    + ["accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline",
       "accel_5s_vs_hist_baseline"]
    # --- TradeSize-baseline family (user asked to consider; new schema only) ---
    + [f"size_ratio_{w}" for w in WINDOWS]
    + [f"signed_size_ratio_{w}" for w in WINDOWS]
    + [f"buy_size_ratio_{w}" for w in WINDOWS]
    + [f"large_trade_count_{w}" for w in WINDOWS]
    + [f"large_trade_volume_frac_{w}" for w in WINDOWS]
    + ["size_accel_1s_vs_10s", "size_accel_2s_vs_10s", "size_accel_5s_vs_10s"]
)
# Lower value => more surge-like (inter-trade interval collapses).
COLS_LOWER = [f"avg_iti_{w}" for w in WINDOWS]

ALL_COLS = COLS_HIGHER + COLS_LOWER

RELATIVE_COLS = {c for c in ALL_COLS if c.startswith("accel_") or c.startswith("size_")}

# Columns the CURRENT live should_fire() rule reads (for the diagnostic).
CURRENT_RULE_COLS = [
    "bid_drift_3s_bp",
    "mid_velocity_1s_bp_per_s", "mid_velocity_2s_bp_per_s", "mid_velocity_3s_bp_per_s",
    "lift_offer_ratio_3s", "lift_offer_ratio_4s",
]


def parse_filename(fname):
    """Return (label, surge_onset_time or None). label in {'pos','neg',None}."""
    base = os.path.basename(fname)
    onset = None
    m = re.search(r"(\d{2}:\d{2}:\d{2}\.\d+)", base)
    if m:
        hh, mm, rest = m.group(1).split(":")
        sec = float(rest)
        onset = dtime(int(hh), int(mm), int(sec), int(round((sec % 1) * 1_000_000)))
    if "positive" in base:
        return "pos", onset
    if "negative" in base:
        return "neg", onset
    return None, None


def ticker(fname):
    base = os.path.basename(fname)
    m = re.search(r"(?:table_)?([A-Z]{2,6})_2026", base)
    return m.group(1) if m else base[:10]


def iso_to_time(s):
    try:
        return datetime.fromisoformat(s).time()
    except Exception:
        return None


def fval(row, col):
    v = row.get(col, "")
    if v in ("", None):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load_file(path):
    """Return (label, onset, rows, dropped_reason). rows = TRADE rows in the surge
    region. dropped_reason is set (and rows empty) when the file is a sentinel."""
    label, onset = parse_filename(path)
    all_trades = []
    region = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            if row.get("event_type") != "TRADE":
                continue
            all_trades.append(row)
            if onset is not None:
                t = iso_to_time(row.get("local_arrival_iso", ""))
                if t is None or t < onset:
                    continue
            region.append(row)
    # Sentinel detection on the first TRADE row.
    if all_trades:
        first = all_trades[0]
        for c in SENTINEL_COLS:
            v = fval(first, c)
            if v is not None and abs(v - SENTINEL) < 1e-6:
                return label, onset, [], f"{c}=={int(SENTINEL)}"
    return label, onset, region, None


def surge_stat(rows, col):
    """Per-file surge statistic: max (higher=surge) or min (avg_iti)."""
    vals = [v for v in (fval(r, col) for r in rows) if v is not None]
    if not vals:
        return None
    return min(vals) if col in COLS_LOWER else max(vals)


def session_age(row):
    return fval(row, "session_age_sec")


def first_cross_latency(rows, col, thr):
    """Seconds from session start (session_age_sec) to the first crossing."""
    lower = col in COLS_LOWER
    for r in rows:
        v = fval(r, col)
        if v is None:
            continue
        if (v <= thr) if lower else (v >= thr):
            age = session_age(r)
            return max(0.0, age) if age is not None else 0.0
    return None


# ---- current live rule (mirror of trade-mole-2.1.py should_fire) ----
def current_rule_fires_row(r):
    bd = fval(r, "bid_drift_3s_bp")
    if bd is None or bd < 400:
        return False
    mv = None
    for k in ("mid_velocity_1s_bp_per_s", "mid_velocity_2s_bp_per_s",
              "mid_velocity_3s_bp_per_s"):
        v = fval(r, k)
        if v is not None:
            mv = v
            break
    if mv is None or mv < 150:
        return False
    lift = fval(r, "lift_offer_ratio_3s")
    if lift is None:
        lift = fval(r, "lift_offer_ratio_4s")
    if lift is not None and lift < 0.55:
        return False
    return True


def current_rule_first_fire(rows):
    for r in rows:
        if current_rule_fires_row(r):
            return session_age(r)
    return None


# ---- candidate combined max-recall rule ----
COMBINED_QU = "quote_updates_5s"
COMBINED_QU_THR = 9.0
COMBINED_TR = "trade_rate_4s"
COMBINED_TR_THR = 3.0


def combined_rule_first_fire(rows):
    for r in rows:
        qu = fval(r, COMBINED_QU)
        tr = fval(r, COMBINED_TR)
        freq = (qu is not None and qu >= COMBINED_QU_THR) or \
               (tr is not None and tr >= COMBINED_TR_THR)
        if freq or current_rule_fires_row(r):
            return session_age(r)
    return None


def main():
    files = sorted(
        os.path.join(OUTDIR, f)
        for f in os.listdir(OUTDIR)
        if (f.startswith("_positive") or f.startswith("_negative"))
        and (f.endswith(".txt") or f.endswith(".csv"))
        and DATE_RE.search(f)
    )

    data = []     # (name, label, onset, rows)
    dropped = []  # (name, reason)
    no_data = []
    for p in files:
        label, onset, rows, reason = load_file(p)
        if label is None:
            continue
        if reason:
            dropped.append((ticker(p), reason))
            continue
        if not rows:
            no_data.append(ticker(p))
            continue
        data.append((ticker(p), label, onset, rows))

    pos = [d for d in data if d[1] == "pos"]
    neg = [d for d in data if d[1] == "neg"]

    print("=" * 100)
    print("SURGE-DETECTION THRESHOLD ANALYSIS  (labeled files 2026-06-08..11)")
    print("=" * 100)
    if dropped:
        print("Dropped (44444 baseline sentinel): "
              + ", ".join(f"{t} [{r}]" for t, r in dropped))
    if no_data:
        print("Dropped (no TRADE rows in region): " + ", ".join(no_data))
    print(f"Loaded {len(pos)} positives, {len(neg)} negatives.\n")
    print("  positives: " + ", ".join(sorted(d[0] for d in pos)))
    print("  negatives: " + ", ".join(sorted(d[0] for d in neg)))
    print()

    # ---- per-file surge statistics ----
    stats = {c: {d[0]: surge_stat(d[3], c) for d in data} for c in ALL_COLS}

    # ---- coverage ----
    def covered(col, group):
        return sum(1 for d in group if stats[col][d[0]] is not None)

    # ---- threshold sweep per column (max-recall objective) ----
    results = []
    for col in ALL_COLS:
        lower = col in COLS_LOWER
        pv = [stats[col][d[0]] for d in pos if stats[col][d[0]] is not None]
        nv = [stats[col][d[0]] for d in neg if stats[col][d[0]] is not None]
        cp, cn = len(pv), len(nv)
        if cp == 0 or cn == 0:
            continue
        if cp < COVERAGE_GATE * len(pos) or cn < COVERAGE_GATE * len(neg):
            eligible = False
        else:
            eligible = True
        cand = sorted(set(pv + nv))
        thrs = []
        for i in range(len(cand)):
            thrs.append(cand[i])
            if i + 1 < len(cand):
                thrs.append((cand[i] + cand[i + 1]) / 2)
        best = None
        for thr in thrs:
            if lower:
                tp = sum(1 for v in pv if v <= thr)
                fp = sum(1 for v in nv if v <= thr)
            else:
                tp = sum(1 for v in pv if v >= thr)
                fp = sum(1 for v in nv if v >= thr)
            tpr = tp / cp
            tnr = (cn - fp) / cn
            # median first-cross latency over positives that cross at this thr
            lats = [lat for lat in
                    (first_cross_latency(d[3], col, thr) for d in pos)
                    if lat is not None]
            med_lat = median(lats) if lats else float("inf")
            # max recall: maximise TPR, then minimise false positives (max TNR),
            # then earliest latency. Picks the HIGHEST threshold that still keeps
            # full recall, instead of the trivial floor threshold.
            key = (round(tpr, 6), round(tnr, 6), -med_lat)
            if best is None or key > best[0]:
                best = (key, thr, tpr, tnr, tp, fp, med_lat)
        _, thr, tpr, tnr, tp, fp, med_lat = best
        results.append(dict(
            col=col, thr=thr, tpr=tpr, tnr=tnr, tp=tp, fp=fp,
            cp=cp, cn=cn, med_lat=med_lat, eligible=eligible,
            relative=col in RELATIVE_COLS,
        ))

    def sortkey(r):
        return (r["eligible"], r["tpr"], r["tnr"],
                -(r["med_lat"] if r["med_lat"] != float("inf") else 1e9))

    results.sort(key=sortkey, reverse=True)

    print("=" * 100)
    print("RANKED COLUMNS  (max-recall: TPR, then earliest median latency, then TNR)")
    print("  thr = best single-row threshold;  >= for higher-surge cols, <= for avg_iti")
    print("=" * 100)
    hdr = (f"{'column':<28}{'thr':>10}{'TPR':>6}{'TNR':>6}"
           f"{'tp/np':>8}{'fp/nn':>8}{'medLat':>8}{'cov p/n':>9}{'flag':>6}")
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        lat = "inf" if r["med_lat"] == float("inf") else f"{r['med_lat']:.2f}"
        flag = ("rel" if r["relative"] else "abs") + ("" if r["eligible"] else "*")
        print(f"{r['col']:<28}{r['thr']:>10.3f}{r['tpr']:>6.2f}{r['tnr']:>6.2f}"
              f"{str(r['tp'])+'/'+str(r['cp']):>8}{str(r['fp'])+'/'+str(r['cn']):>8}"
              f"{lat:>8}{str(r['cp'])+'/'+str(r['cn']):>9}{flag:>6}")
    print("\n  flag '*' = below coverage gate "
          f"({COVERAGE_GATE:.0%} of files non-null in both classes) -> not eligible "
          "as a primary trigger.")

    # ---- diagnostic: why did the CURRENT price-momentum rule miss? ----
    print("\n" + "=" * 100)
    print("CURRENT RULE DIAGNOSTIC  (should_fire: bid_drift_3s>=400 AND mid_vel>=150 "
          "AND lift>=0.55)")
    print("=" * 100)
    print(f"{'lbl':>4} {'sym':<6}{'bid_drift_3s':>13}{'mid_vel_max':>12}"
          f"{'lift_3s':>8}{'fires?':>8}{'@age_s':>8}")
    n_pos_fire = 0
    for d in sorted(data, key=lambda x: (x[1], x[0])):
        sym, label, _, rows = d
        bd = surge_stat(rows, "bid_drift_3s_bp")
        mv = max([x for x in (surge_stat(rows, "mid_velocity_1s_bp_per_s"),
                              surge_stat(rows, "mid_velocity_2s_bp_per_s"),
                              surge_stat(rows, "mid_velocity_3s_bp_per_s"))
                  if x is not None], default=None)
        lf = surge_stat(rows, "lift_offer_ratio_3s")
        age = current_rule_first_fire(rows)
        fires = age is not None
        if label == "pos" and fires:
            n_pos_fire += 1
        bds = f"{bd:.0f}" if bd is not None else "None"
        mvs = f"{mv:.0f}" if mv is not None else "None"
        lfs = f"{lf:.2f}" if lf is not None else "None"
        ages = f"{age:.2f}" if age is not None else "--"
        print(f"{label:>4} {sym:<6}{bds:>13}{mvs:>12}{lfs:>8}{str(fires):>8}{ages:>8}")
    print(f"\nCurrent rule recall on positives: {n_pos_fire}/{len(pos)}")

    # ---- candidate combined max-recall rule ----
    print("\n" + "=" * 100)
    print(f"CANDIDATE COMBINED RULE (max recall):")
    print(f"  ({COMBINED_QU} >= {COMBINED_QU_THR:g}  OR  {COMBINED_TR} >= "
          f"{COMBINED_TR_THR:g})  OR  (current momentum clause)")
    print("=" * 100)
    print(f"{'lbl':>4} {'sym':<6}{'fires?':>8}{'@age_s':>8}")
    ctp = cfp = 0
    pos_lats = []
    for d in sorted(data, key=lambda x: (x[1], x[0])):
        sym, label, _, rows = d
        age = combined_rule_first_fire(rows)
        fires = age is not None
        if label == "pos" and fires:
            ctp += 1
            if age is not None:
                pos_lats.append(age)
        if label == "neg" and fires:
            cfp += 1
        ages = f"{age:.2f}" if age is not None else "--"
        print(f"{label:>4} {sym:<6}{str(fires):>8}{ages:>8}")
    sub1 = sum(1 for a in pos_lats if a <= 1.0)
    sub2 = sum(1 for a in pos_lats if a <= 2.0)
    print(f"\nCombined rule: TP={ctp}/{len(pos)}  FP={cfp}/{len(neg)}  "
          f"| positives firing <=1s: {sub1}/{ctp}  <=2s: {sub2}/{ctp}  "
          f"| median pos latency: {median(pos_lats):.2f}s" if pos_lats else
          f"\nCombined rule: TP={ctp}/{len(pos)}  FP={cfp}/{len(neg)}")


if __name__ == "__main__":
    main()
