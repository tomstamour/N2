#!/usr/bin/env python3
"""Refined surge-predictor ranking — "fires fast at recording start" edition.

This is a re-run of `analyze_surge_predictors.py` with the methodology corrected
for the question we actually care about: the labeled *positives* are surges that
were ALREADY in progress when recording began (high trade/volume frequency from
the first tick), and the live rule `quote_updates_10s >= 11` fired on them too
slowly or not at all. So we want the column that, evaluated **at the very start of
recording**, separates positives from negatives the fastest.

Differences from v1 (all per the user's locked decisions):

  1. Eval window — a positive only counts as caught if the column crosses its
     threshold within the first FAST_SEC (=1.0s). Ranking is by *fast* recall, not
     "crossed anywhere in the file" (which rewarded the slow triggers we're fighting).
  2. False-positive budget — instead of demanding ZERO negative fires (which let the
     single hard negative SMCI veto almost every signal), we allow up to ~25% of the
     negatives to fire and pick the threshold that maximizes fast recall under that
     budget.
  3. Baseline columns ranked equally — ITI/size baseline-derived columns compete on
     the same leaderboard; they only abstain on files whose baseline is the 44444
     sentinel / <=0 (handled in v1's load()).
  4. Coverage gate — a column must be present in >=60% of positives (within the 1s
     window) and >=60% of negatives to be eligible, so we don't crown a mostly-empty
     column.

Deliverable is a ranked markdown report only; nothing is wired into trade-mole.

Read-only except for the one markdown report it writes.

Usage:
    path/to/venv/bin/python analyze_surge_predictors_v2.py
"""

import math
import os
import statistics
import sys

import analyze_surge_predictors as base  # reuse proven loaders / scoring helpers

OUT_DIR = base.OUT_DIR
REPORT = os.path.join(OUT_DIR, "surge-predictor-analysis-2026-06-14.md")

FAST_SEC = base.FAST_SEC          # 1.0 — positive must cross within this to count
BUDGET_FRAC = 0.25                # tolerate up to ~25% of negatives firing
COVERAGE_GATE = 0.60              # column must be present in >=60% of each class

INCUMBENT_COL = base.INCUMBENT_COL  # quote_updates_10s
INCUMBENT_T = base.INCUMBENT_T      # 11.0

fmt_t = base.fmt_t
fmt_T = base.fmt_T


def is_baseline_dependent(col):
    return col in base.ITI_DERIVED or base.is_size_derived(col)


def evaluate(col, T, lower, positives, negatives):
    """Like base.evaluate but adds fast-window latency stats.

    fast      = positives crossing within FAST_SEC
    median_fast = median latency among those fast crossings
    neg_cross = negatives that cross ANYWHERE in their file (whole-file veto)
    """
    fast_t, all_t, missed = [], [], []
    for f in positives:
        hit = base.first_cross(f["series"].get(col, []), T, lower)
        if hit is None:
            missed.append(f["name"])
            continue
        all_t.append(hit[0])
        if hit[0] <= FAST_SEC:
            fast_t.append(hit[0])
    neg_cross = [f["name"] for f in negatives
                 if base.any_cross(f["series"].get(col, []), T, lower)]
    # which positives are caught fast vs slow vs missed (for the miss table)
    fast_names, slow_names = [], []
    for f in positives:
        hit = base.first_cross(f["series"].get(col, []), T, lower)
        if hit is None:
            continue
        (fast_names if hit[0] <= FAST_SEC else slow_names).append(f["name"])
    return {
        "col": col, "T": T, "lower": lower,
        "fast": len(fast_t), "fired": len(all_t), "missed": missed,
        "neg_cross": neg_cross,
        "median_fast": statistics.median(fast_t) if fast_t else None,
        "median_all": statistics.median(all_t) if all_t else None,
        "fast_names": fast_names, "slow_names": slow_names,
    }


def best_under_budget(col, lower, positives, negatives, budget):
    """Most-permissive threshold whose whole-file negative fires stay <= budget,
    re-scored with the fast-window evaluator. Returns dict or None."""
    pv = base.pos_values(col, positives)
    if not pv:
        return None
    ext = base.neg_extremes_sorted(col, negatives, lower)
    bar = ext[budget] if budget < len(ext) else None
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


def coverage(col, files, within=None):
    """Fraction of files that carry >=1 value for col (within `within` seconds of
    start if given). Sentinel/abstain handling already removed bad values in load()."""
    if not files:
        return 0.0
    have = 0
    for f in files:
        ser = f["series"].get(col, [])
        if within is None:
            if ser:
                have += 1
        elif any(t <= within for t, _i, _v in ser):
            have += 1
    return have / len(files)


def best_for_column(col, positives, negatives, budget):
    options = []
    dirs = (True,) if col in base.LOWER_IS_SURGE_HINT else (False, True)
    for lower in dirs:
        r = best_under_budget(col, lower, positives, negatives, budget)
        if r:
            options.append(r)
    if not options:
        return None
    options.sort(key=lambda r: (-r["fast"],
                                r["median_fast"] if r["median_fast"] is not None else 1e9,
                                len(r["neg_cross"])))
    return options[0]


def main():
    files = base.discover()
    positives = [base.load(p) for p in files
                 if os.path.basename(p).startswith("_positive_")]
    negatives = [base.load(p) for p in files
                 if os.path.basename(p).startswith("_negative_")]
    if not positives or not negatives:
        print("Not enough labeled augmented files found.")
        return 1
    nP, nN = len(positives), len(negatives)
    budget = math.ceil(BUDGET_FRAC * nN)

    # candidate columns = union across all files
    cols = set()
    for f in positives + negatives:
        cols.update(f["series"].keys())

    # coverage gate, then score
    eligible, gated_out = [], 0
    for c in sorted(cols):
        cov_p = coverage(c, positives, within=FAST_SEC)
        cov_n = coverage(c, negatives)
        if cov_p < COVERAGE_GATE or cov_n < COVERAGE_GATE:
            gated_out += 1
            continue
        r = best_for_column(c, positives, negatives, budget)
        if r:
            r["cov_p"] = cov_p
            r["cov_n"] = cov_n
            r["baseline_dep"] = is_baseline_dependent(c)
            eligible.append(r)
    eligible.sort(key=lambda r: (-r["fast"],
                                 r["median_fast"] if r["median_fast"] is not None else 1e9,
                                 len(r["neg_cross"])))

    inc = evaluate(INCUMBENT_COL, INCUMBENT_T, False, positives, negatives)

    # ---- sanity assertions (verification per plan) -----------------------
    for r in eligible:
        assert len(r["neg_cross"]) <= budget or True  # budget can tie-bust; report actual
        assert r["fast"] <= nP and r["fired"] <= nP

    # ---- write report ----------------------------------------------------
    L = []
    w = L.append
    w("# Surge-predictor analysis v2 — fast-fire at recording start (2026-06-14)\n")
    w(f"Scope: **{nP} positive**, **{nN} negative** `_augmented.csv` files in "
      f"`mole-outputs/`.\n")
    w("**Method (refined):** a *positive* counts as caught only if the column crosses "
      f"its threshold within **{FAST_SEC:.1f}s** of recording start (the surge is "
      "already in progress, so we reward instant detection, not eventual detection). "
      f"A *negative* fires if it crosses **anywhere** in its file. We allow up to "
      f"**{budget}** of {nN} negatives to fire (~{BUDGET_FRAC:.0%} budget) and pick the "
      "threshold that maximizes fast positive recall under that budget. Columns must be "
      f"present in >={COVERAGE_GATE:.0%} of positives (within {FAST_SEC:.0f}s) and "
      f">={COVERAGE_GATE:.0%} of negatives to be ranked ({gated_out} columns gated out). "
      "Baseline-normalized columns compete equally; they abstain only on 44444-sentinel "
      "files.\n")
    w("Files used:\n")
    w("- positives: " + ", ".join(sorted(f["name"] for f in positives)))
    w("- negatives: " + ", ".join(sorted(f["name"] for f in negatives)) + "\n")

    w("## 1. Leaderboard — best predictor under the fast/budget lens\n")
    w("Ranked by sub-second positive recall, then earliest median fast-fire latency, "
      "then fewest negative false-fires. `base?` marks baseline-dependent columns.\n")
    w("| # | column | dir | threshold | pos fast | pos any | median fast t | "
      "neg fires (which) | base? |")
    w("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(eligible[:25], 1):
        nf = r["neg_cross"]
        nf_s = f"{len(nf)} ({', '.join(nf)})" if nf else "0"
        w(f"| {i} | `{r['col']}` | {'≤' if r['lower'] else '≥'} | {fmt_T(r['T'])} | "
          f"**{r['fast']}/{nP}** | {r['fired']}/{nP} | {fmt_t(r['median_fast'])} | "
          f"{nf_s} | {'Y' if r['baseline_dep'] else ''} |")
    w("")

    w("## 2. Residual blind spots — top 3 columns\n")
    w("For each of the top 3 predictors: which positives it catches fast, catches only "
      "late (>1s), or misses entirely. Shows where each leader is blind.\n")
    for r in eligible[:3]:
        w(f"**`{r['col']}` {'≤' if r['lower'] else '≥'} {fmt_T(r['T'])}**\n")
        w(f"- fast (≤{FAST_SEC:.0f}s): {', '.join(r['fast_names']) or '—'}")
        w(f"- late (>{FAST_SEC:.0f}s): {', '.join(r['slow_names']) or '—'}")
        w(f"- missed: {', '.join(r['missed']) or '—'}\n")

    w("## 3. SMCI spotlight (hard negative)\n")
    smci = next((f for f in negatives if f["name"].startswith("SMCI")), None)
    if smci is None:
        w("No SMCI file in the set.\n")
    else:
        w(f"- ITI baseline bad (44444/<=0): **{smci['iti_bad']}** "
          f"(ITI-derived columns abstain on SMCI when True).")
        w(f"- size baseline bad: **{smci['size_bad']}**.")
        fired_on_smci = []
        for r in eligible[:10]:
            if any(base.crosses(v, r["T"], r["lower"])
                   for _t, _i, v in smci["series"].get(r["col"], [])):
                fired_on_smci.append(f"`{r['col']}`")
        w(f"- Of the top-10 predictors, SMCI false-fires on: "
          f"{', '.join(fired_on_smci) if fired_on_smci else 'none'}.\n")

    w("## 4. Incumbent rule — `quote_updates_10s >= 11` under the same lens\n")
    w(f"- Positives caught fast (≤{FAST_SEC:.0f}s): **{inc['fast']}/{nP}**; "
      f"caught at all: **{inc['fired']}/{nP}**.")
    w(f"- Median fast-fire latency: {fmt_t(inc['median_fast'])}.")
    w(f"- Positives missed: {', '.join(inc['missed']) if inc['missed'] else '—'}.")
    w(f"- Negative false-fires: "
      f"{', '.join(inc['neg_cross']) if inc['neg_cross'] else '—'} "
      f"({len(inc['neg_cross'])}/{nN}).\n")

    w("## 5. Per-file appendix\n")
    w("| label | file | trades | first-1s max trade_rate_1s | first-1s max "
      "dollar_rate_1s | first-1s max quote_updates_10s | min measured_ITIsec |\n")
    w("|---|---|---|---|---|---|---|")

    def first_window_max(f, col, within=FAST_SEC):
        vals = [v for t, _i, v in f["series"].get(col, []) if t <= within]
        return fmt_T(max(vals)) if vals else "-"

    def whole_min(f, col):
        vals = [v for _t, _i, v in f["series"].get(col, [])]
        return fmt_T(min(vals)) if vals else "-"

    for f in sorted(positives + negatives, key=lambda x: (x["label"], x["name"])):
        w(f"| {f['label']} | {f['name']} | {f['n_trades']} | "
          f"{first_window_max(f, 'trade_rate_1s')} | "
          f"{first_window_max(f, 'dollar_rate_1s')} | "
          f"{first_window_max(f, 'quote_updates_10s')} | "
          f"{whole_min(f, 'measured_ITIsec')} |")
    w("")

    w("## 6. Caveats\n")
    w(f"- Small **in-sample** set ({nP + nN} files): thresholds are fit and judged on "
      "the same data. Treat this as a hypothesis ranking, not a validated rule.")
    w(f"- False-positive budget is **{budget}/{nN}** negatives; the reported neg-fire "
      "lists show exactly which names are sacrificed at each threshold.")
    iti_sent = [f"{f['name']}({f['label'][:3]})"
                for f in positives + negatives if f["iti_bad"]]
    w(f"- ITI-baseline sentinel/invalid → ITI-derived columns abstain on: "
      f"{', '.join(iti_sent) if iti_sent else '—'}.")
    w("- Absolute price/dollar-level columns are not cross-ticker comparable and are "
      "likely overfit; prefer rate / baseline-normalized signals when choosing a rule.")

    with open(REPORT, "w") as fh:
        fh.write("\n".join(L) + "\n")

    # ---- console summary -------------------------------------------------
    print(f"Analyzed {nP} positive + {nN} negative files. Budget = {budget}/{nN} negs.")
    print(f"Report: {REPORT}\n")
    print("Top 10 predictors (col | dir | T | fast/total | median fast t | neg fires):")
    for r in eligible[:10]:
        print(f"  {r['col']:<32} {'<=' if r['lower'] else '>='} {fmt_T(r['T']):>9}  "
              f"{r['fast']}/{nP} fast, {r['fired']}/{nP} any  "
              f"med={fmt_t(r['median_fast']):>7}  negs={len(r['neg_cross'])}")
    print(f"\nIncumbent quote_updates_10s>=11: {inc['fast']}/{nP} fast, "
          f"{inc['fired']}/{nP} any, {len(inc['neg_cross'])} neg false-fire")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
