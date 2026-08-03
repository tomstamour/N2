#!/usr/bin/env python3
"""Surge-predictor re-analysis on the 2026-07-09..07-30 labeled window — the
"precision" edition.

The 2026-07-07 refit solved recall; on the new window the problem is inverted:
under the CURRENT rules all 21 pos fire, all 8 neu fire, and 23 of 30 neg ALSO
fire. So this pass ranks variables/thresholds by how many neg fires they KILL
while keeping pos firing, per the user's locked decisions (2026-07-30):

  1. A pos counts as CAUGHT if the candidate crosses anywhere in its recording
     (not only <=1s); median time-to-fire is a ranking tiebreaker only.
  2. neu files (fired, wish they hadn't) are a SECONDARY objective: thresholds
     are calibrated pos-vs-neg; the neu kill-rate is reported as a tiebreaker.
  3. Full recall/FP frontier: for each candidate, the best neg-kill threshold at
     0, 1, 2, 3 pos lost — the user picks the operating point from the report.
  4. Pretrade (Rule F) fires are analyzed on in-file quote-snapshot features
     only (ratio, gap, baseline ITI, bid, ask, spread%) — no universe join, no
     time-of-day, in this pass.

Structure of the labeled window (discovered 2026-07-30):
  * 227-col full recordings = trade-path sessions (16 pos / 6 neu / 20 neg,
    incl. FFAI which is 227-col but Rule-F-fired after 3 trades printed).
  * slim 15-col files = Rule-F pretrade fires, quote-snapshot rows only
    (5 pos / 2 neu / 6 neg); slim 16-col = quote-only no-fire sessions (4 neg).

CENSORING CAVEAT (also stated in the report): recording stops at the fire, so
for a fired file we cannot know whether a STRICTER candidate would have fired
later (pos losses are pessimistic) nor whether a surviving neg would have fired
after the recorded span (neg kills are optimistic).

Read-only on the data; writes one markdown report:
    mole-outputs/surge-predictor-analysis-2026-07-30.md

Usage:
    /home/tom/venv/bin/python analyze_surge_predictors_v3.py
"""

import csv
import glob
import os
import re
import statistics

import numpy as np

import analyze_surge_predictors as base  # proven sentinel/denylist/parse helpers

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))
REPORT = os.path.join(OUT_DIR, "surge-predictor-analysis-2026-07-30.md")

MIN_DATE = "2026-07-09"
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

POS_BUDGETS = (0, 1, 2, 3)   # pos-lost operating points swept per candidate
COVERAGE_GATE = 0.60         # column must be present in >=60% of pos AND neg files
TOP_SINGLES = 20             # leaderboard depth
PAIR_POOL = 15               # top singles fed into the AND-pair sweep
MAX_GRID = 40                # per-column threshold-grid cap for 2D sweeps

_f = base._f
is_sentinel = base.is_sentinel

# Flags that parse as floats but are meaningless as thresholds (new-schema cols
# on top of base.DENYLIST).
EXTRA_DENY = {"halted", "rt_single_trade_flag"}

# v1's set predates the 06-14 column; it is baseline-derived too.
ITI_DERIVED = base.ITI_DERIVED | {"bslnITI_on_last3trades_avrgITI"}

# The live rule family, re-swept on the new window (B/C are single-column).
RULES_1D = [
    ("Rule B", "bsln_over_prev_trd_gap", False),
    ("Rule C", "bslnITI_on_last3trades_avrgITI", False),
]
RULES_2D = [
    ("Rule A", "quote_updates_10s", False, "dollar_rate_5s", False),
    ("Rule D", "measured_ITIsec", True, "trades_in_5s", False),
    ("Rule E", "dollar_rate_5s", False, "price_change_since_start_pct", False),
]


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #

def discover():
    out = []
    for pat in ("_pos_*.csv", "_neg_*.csv", "_neu_*.csv"):
        for p in glob.glob(os.path.join(OUT_DIR, pat)):
            m = DATE_RE.search(os.path.basename(p))
            if m and m.group(1) >= MIN_DATE:
                out.append(p)
    return sorted(set(out))


def short_name(path):
    b = os.path.basename(path)
    m = re.match(r"_(pos|neg|neu)_trade-mole-table_([A-Z]+)_(20\d\d-\d\d-\d\d)_(\d\d-\d\d)", b)
    if not m:
        return b
    return f"{m.group(2)}_{m.group(3)[5:]}_{m.group(4)}"


def rule_of(reason):
    if reason.startswith("bslnITI_over_pretrade_gap"):
        return "F"
    if reason.startswith("bsln_over_prev_trd_gap"):
        return "B"
    if reason.startswith("bslnITI_on_last3trades"):
        return "C"
    if reason.startswith("quote_updates"):
        return "A"
    if reason.startswith("measured_ITI"):
        return "D"
    if reason.startswith("$rate"):
        return "E"
    return "?"


def _truthy(s):
    return (s or "").strip().lower() in ("true", "1")


def load(path):
    """One record per labeled recording: label, fire info, TRADE-row series
    (sentinel/abstain-guarded like v1), and pretrade snapshot features."""
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        fields = list(reader.fieldnames or [])
        rows = list(reader)

    label = os.path.basename(path).split("_")[1]  # pos | neg | neu
    t0 = None
    for r in rows:
        t0 = _f(r, "local_arrival_time")
        if t0 is not None:
            break

    fired, fire_reason, fire_t, fire_rule = False, "", None, ""
    for r in rows:
        if _truthy(r.get("surge_detected")):
            fired = True
            fire_reason = (r.get("surge_reason") or "").strip()
            ta = _f(r, "local_arrival_time")
            fire_t = (ta - t0) if (ta is not None and t0 is not None) else None
            fire_rule = rule_of(fire_reason)
            break

    iti_base = base.first_good_baseline(rows, "hist_baseline_avg_iti")
    size_base = base.first_good_baseline(rows, "hist_baseline_trade_size")
    abstain = set()
    if iti_base is None:
        abstain |= ITI_DERIVED
    if size_base is None:
        abstain |= {c for c in fields if base.is_size_derived(c)}

    deny = base.DENYLIST | EXTRA_DENY
    cand = [c for c in fields if c not in deny]

    # Aligned TRADE rows (for same-row AND rules) + per-column series.
    trows = []
    series = {}
    for r in rows:
        if r.get("event_type") != "TRADE":
            continue
        ta = _f(r, "local_arrival_time")
        if ta is None or t0 is None:
            continue
        t_rel = ta - t0
        vals = {}
        for c in cand:
            if c in abstain:
                continue
            v = _f(r, c)
            if v is None or is_sentinel(v):
                continue
            vals[c] = v
            series.setdefault(c, []).append((t_rel, v))
        trows.append((t_rel, vals))

    # Pretrade snapshot features. Fired files: the PRETRADE_SURGE row; no-fire
    # quote-only files: the closest-to-firing tick row (max running ratio).
    snap = {}
    srow, best_ratio = None, None
    for r in rows:
        if r.get("event_type") == "PRETRADE_SURGE":
            srow = r
            break
        v = _f(r, "bsln_over_prev_trd_gap")
        if v is not None and not is_sentinel(v) and (best_ratio is None or v > best_ratio):
            best_ratio, srow = v, r
    if srow is not None:
        bid, ask = _f(srow, "bid"), _f(srow, "ask")
        if bid is None or ask is None:  # tick rows carry one side each
            for r in rows:
                bid = bid if bid is not None else _f(r, "bid")
                ask = ask if ask is not None else _f(r, "ask")
        spread_pct = None
        if bid and ask and bid > 0 and ask >= bid:
            spread_pct = (ask - bid) / ((ask + bid) / 2.0) * 100.0
        snap = {
            "ratio": _f(srow, "bsln_over_prev_trd_gap"),
            "gap": _f(srow, "prev_trd_time_gap"),
            "baseline": iti_base,
            "bid": bid,
            "ask": ask,
            "spread_pct": spread_pct,
        }

    return {
        "path": path,
        "name": short_name(path),
        "label": label,
        "ncols": len(fields),
        "nrows": len(rows),
        "fired": fired,
        "fire_reason": fire_reason,
        "fire_rule": fire_rule,
        "fire_t": fire_t,
        "n_trades": len(trows),
        "trows": trows,
        "series": series,
        "snap": snap,
        "iti_base": iti_base,
    }


# --------------------------------------------------------------------------- #
# Frontier machinery
# --------------------------------------------------------------------------- #

def crosses(v, T, lower):
    return (v <= T) if lower else (v >= T)


def file_extreme(rec, col, lower):
    s = rec["series"].get(col)
    if not s:
        return None
    vs = [v for _t, v in s]
    return min(vs) if lower else max(vs)


def first_cross_t(rec, col, T, lower):
    for t_rel, v in rec["series"].get(col, []):
        if crosses(v, T, lower):
            return t_rel
    return None


def col_frontier(col, lower, positives, negatives, neutrals, npos_total, budgets=POS_BUDGETS):
    """Per-budget best threshold: keep >= npos_total-k pos crossing, minimize neg
    crossings. Pos files where the column is absent/abstained count as LOST."""
    pos_ext = [(file_extreme(f, col, lower), f) for f in positives]
    pos_ext = [(e, f) for e, f in pos_ext if e is not None]
    pos_ext.sort(key=lambda x: x[0], reverse=not lower)
    out = {}
    for k in budgets:
        need = npos_total - k
        if need <= 0 or len(pos_ext) < need:
            out[k] = None
            continue
        T = pos_ext[need - 1][0]
        caught, ttfs, lost = [], [], []
        for f in positives:
            t = first_cross_t(f, col, T, lower)
            if t is None:
                lost.append(f["name"])
            else:
                caught.append(f["name"])
                ttfs.append(t)
        neg_hit = [f["name"] for f in negatives
                   if (e := file_extreme(f, col, lower)) is not None and crosses(e, T, lower)]
        neu_hit = [f["name"] for f in neutrals
                   if (e := file_extreme(f, col, lower)) is not None and crosses(e, T, lower)]
        out[k] = {
            "T": T, "caught": len(caught), "lost": lost,
            "neg": neg_hit, "neu": neu_hit,
            "med_ttf": statistics.median(ttfs) if ttfs else None,
        }
    return out


def frontier_key(fr, budgets=POS_BUDGETS):
    """Sort key: fewer negs at k=0, then k=1, ... then faster median ttf."""
    key = []
    for k in budgets:
        e = fr.get(k)
        key.append((len(e["neg"]), len(e["neu"])) if e else (99, 99))
    e0 = next((fr[k] for k in budgets if fr.get(k)), None)
    key.append(e0["med_ttf"] if e0 and e0["med_ttf"] is not None else 9e9)
    return tuple(key)


def best_direction(col, positives, negatives, neutrals, npos_total):
    dirs = [True] if col in base.LOWER_IS_SURGE_HINT else [False, True]
    best = None
    for lower in dirs:
        fr = col_frontier(col, lower, positives, negatives, neutrals, npos_total)
        if all(v is None for v in fr.values()):
            continue
        cand = (frontier_key(fr), lower, fr)
        if best is None or cand[0] < best[0]:
            best = cand
    return best  # (key, lower, frontier) or None


def grid_thresholds(col, positives, cap=MAX_GRID):
    vals = sorted({v for f in positives for _t, v in f["series"].get(col, [])})
    if not vals:
        return []
    if len(vals) > cap:
        idx = np.linspace(0, len(vals) - 1, cap).round().astype(int)
        vals = sorted({vals[i] for i in idx})
    return vals


def pair_hit_grid(rec, c1, low1, c2, low2, t1s, t2s):
    """Boolean (len(t1s), len(t2s)) — some TRADE row crosses BOTH thresholds."""
    a, b = [], []
    for _t, vals in rec["trows"]:
        if c1 in vals and c2 in vals:
            a.append(vals[c1])
            b.append(vals[c2])
    if not a:
        return np.zeros((len(t1s), len(t2s)), dtype=bool)
    a = np.asarray(a)[:, None]
    b = np.asarray(b)[:, None]
    t1 = np.asarray(t1s)[None, :]
    t2 = np.asarray(t2s)[None, :]
    m1 = (a <= t1) if low1 else (a >= t1)
    m2 = (b <= t2) if low2 else (b >= t2)
    return (m1.astype(np.int32).T @ m2.astype(np.int32)) > 0


def pair_first_t(rec, c1, low1, c2, low2, T1, T2):
    for t_rel, vals in rec["trows"]:
        if c1 in vals and c2 in vals and crosses(vals[c1], T1, low1) and crosses(vals[c2], T2, low2):
            return t_rel
    return None


def pair_frontier(c1, low1, c2, low2, positives, negatives, neutrals, npos_total,
                  budgets=POS_BUDGETS):
    t1s = grid_thresholds(c1, positives)
    t2s = grid_thresholds(c2, positives)
    if not t1s or not t2s:
        return None
    shape = (len(t1s), len(t2s))
    pos_n = np.zeros(shape, dtype=np.int32)
    neg_n = np.zeros(shape, dtype=np.int32)
    neu_n = np.zeros(shape, dtype=np.int32)
    for f in positives:
        pos_n += pair_hit_grid(f, c1, low1, c2, low2, t1s, t2s)
    for f in negatives:
        neg_n += pair_hit_grid(f, c1, low1, c2, low2, t1s, t2s)
    for f in neutrals:
        neu_n += pair_hit_grid(f, c1, low1, c2, low2, t1s, t2s)
    out = {}
    for k in budgets:
        need = npos_total - k
        ok = pos_n >= need
        if not ok.any():
            out[k] = None
            continue
        score = np.where(ok, neg_n * 100 + neu_n, 10**9)
        i, j = np.unravel_index(int(score.argmin()), shape)
        T1, T2 = t1s[i], t2s[j]
        ttfs, lost = [], []
        for f in positives:
            t = pair_first_t(f, c1, low1, c2, low2, T1, T2)
            (ttfs.append(t) if t is not None else lost.append(f["name"]))
        neg_hit = [f["name"] for f in negatives
                   if pair_first_t(f, c1, low1, c2, low2, T1, T2) is not None]
        neu_hit = [f["name"] for f in neutrals
                   if pair_first_t(f, c1, low1, c2, low2, T1, T2) is not None]
        out[k] = {
            "T": (T1, T2), "caught": int(pos_n[i, j]), "lost": lost,
            "neg": neg_hit, "neu": neu_hit,
            "med_ttf": statistics.median(ttfs) if ttfs else None,
        }
    return out


# --------------------------------------------------------------------------- #
# OR-level ruleset simulation. The live trigger is an OR of legs; sweeping a
# leg as a SOLE predictor is meaningless for wiring (it would have to carry
# every pos alone). Here each leg is tightened while the others hold at the
# live thresholds, and the outcome is measured at the RULESET level.
# --------------------------------------------------------------------------- #

# Live thresholds (trade-mole-2.1.py constants, verified 2026-07-30).
CUR = dict(veto=0.005, B=100.0, C=100.0, A_qu=11.0, A_rate=2.0,
           D_iti=0.3, D_tr=3.0, E_rate=10.0, E_pc=0.01)


def or_first_fire(rec, P):
    """Replay the trade-path should_fire OR per TRADE row on recorded columns.
    Same per-row semantics as analyze_trigger_rules.first_fire, but on the new
    schema only (all columns recorded; no rebuild needed)."""
    vb = rec["iti_base"] is not None
    for t_rel, v in rec["trows"]:
        pc = v.get("price_change_since_start_pct")
        if pc is not None and pc < -P["veto"]:
            continue
        if vb:
            x = v.get("bsln_over_prev_trd_gap")
            if x is not None and x > P["B"]:
                return ("B", t_rel)
            x = v.get("bslnITI_on_last3trades_avrgITI")
            if x is not None and x > P["C"]:
                return ("C", t_rel)
        qu = v.get("quote_updates_10s")
        dr = v.get("dollar_rate_5s")
        if qu is not None and dr is not None and qu >= P["A_qu"] and dr >= P["A_rate"]:
            return ("A", t_rel)
        mi = v.get("measured_ITIsec")
        t5 = v.get("trades_in_5s")
        if mi is not None and t5 is not None and mi <= P["D_iti"] and t5 >= P["D_tr"]:
            return ("D", t_rel)
        if dr is not None and pc is not None and dr >= P["E_rate"] and pc >= P["E_pc"]:
            return ("E", t_rel)
    return None


def or_confusion(P, p1_pos, p1_neg, p1_neu):
    fires = {r["name"]: or_first_fire(r, P) for r in p1_pos + p1_neg + p1_neu}
    n = {}
    for grp, rows in (("pos", p1_pos), ("neg", p1_neg), ("neu", p1_neu)):
        n[grp] = sum(1 for r in rows if fires[r["name"]])
    ttfs = [fires[r["name"]][1] for r in p1_pos if fires[r["name"]]]
    n["med_ttf"] = statistics.median(ttfs) if ttfs else None
    return n, fires


def _next_up(v):
    return float(np.nextafter(v, np.inf))


def _next_dn(v):
    return float(np.nextafter(v, -np.inf))


def _col_vals(part1, col, cond):
    return sorted({v for f in part1 for _t, v in f["series"].get(col, []) if cond(v)})


def _cap(vals, n=30):
    if len(vals) <= n:
        return vals
    idx = np.linspace(0, len(vals) - 1, n).round().astype(int)
    return sorted({vals[i] for i in idx})


def leg_candidates(leg, part1):
    """Tightening-only override grids per leg, sorted loose -> tight. For >=
    style params both v (keep boundary) and just-above-v (kill boundary) are
    candidates; for <= style, v and just-below-v."""
    if leg == "B":
        vals = _cap(_col_vals(part1, "bsln_over_prev_trd_gap", lambda v: v > CUR["B"]))
        return [{"B": T} for T in [CUR["B"]] + vals]
    if leg == "C":
        vals = _cap(_col_vals(part1, "bslnITI_on_last3trades_avrgITI", lambda v: v > CUR["C"]))
        return [{"C": T} for T in [CUR["C"]] + vals]
    if leg == "A":
        qus = sorted({CUR["A_qu"]} | {v + 1 for v in _col_vals(
            part1, "quote_updates_10s", lambda v: v >= CUR["A_qu"])})
        rates = sorted({CUR["A_rate"]} | {x for v in _cap(_col_vals(
            part1, "dollar_rate_5s", lambda v: v >= CUR["A_rate"]))
            for x in (v, _next_up(v))})
        return [{"A_qu": q, "A_rate": r} for q in qus for r in rates]
    if leg == "D":
        itis = sorted({CUR["D_iti"]} | {x for v in _cap(_col_vals(
            part1, "measured_ITIsec", lambda v: v <= CUR["D_iti"]))
            for x in (v, _next_dn(v))})
        trs = sorted({CUR["D_tr"]} | {v + 1 for v in _col_vals(
            part1, "trades_in_5s", lambda v: v >= CUR["D_tr"])})
        return [{"D_iti": i, "D_tr": t} for i in itis for t in trs]
    if leg == "E":
        rates = sorted({CUR["E_rate"]} | {x for v in _cap(_col_vals(
            part1, "dollar_rate_5s", lambda v: v >= CUR["E_rate"]))
            for x in (v, _next_up(v))})
        pcs = sorted({CUR["E_pc"]} | {x for v in _cap(_col_vals(
            part1, "price_change_since_start_pct", lambda v: v >= CUR["E_pc"]))
            for x in (v, _next_up(v))})
        return [{"E_rate": r, "E_pc": p} for r in rates for p in pcs]
    raise ValueError(leg)


def leg_frontier(leg, baseP, p1_pos, p1_neg, p1_neu, budgets=(0, 1, 2)):
    """Best tightening of one leg with the others held at baseP. Ties resolve
    to the LOOSEST setting (safer under censoring)."""
    npos = len(p1_pos)
    out = {}
    for k in budgets:
        best = None
        for ov in leg_candidates(leg, p1_pos + p1_neg + p1_neu):
            P = dict(baseP, **ov)
            n, fires = or_confusion(P, p1_pos, p1_neg, p1_neu)
            if n["pos"] < npos - k:
                continue
            sc = (n["neg"], n["neu"], -n["pos"])
            if best is None or sc < best[0]:
                best = (sc, ov, n, fires)
        out[k] = best and {"ov": best[1], "n": best[2], "fires": best[3]}
    return out


# --------------------------------------------------------------------------- #
# Proposed Rule-F veto (Part 2). Three keep-conditions on a pretrade fire:
#   1. no trade has printed yet (F is a PRE-trade rule; FFAI fired after 3
#      prints on a falling tape — F has no price-drop veto, this closes it),
#   2. pretrade gap <= MAX_GAP (last known trade within ~a second of the
#      snapshot; the killed negs sat at 1.41-1.72s),
#   3. ratio <= MAX_RATIO (a quasi-never-trading name + one dust print fakes
#      infinite acceleration: LITS ratio 34200 vs pos max 3420).
# --------------------------------------------------------------------------- #

PROPOSED_F = dict(max_gap=1.25, max_ratio=5000.0)


def f_keep(rec):
    s = rec["snap"]
    if rec["n_trades"] > 0:
        return False, "trade already printed"
    g, r = s.get("gap"), s.get("ratio")
    if g is None or g > PROPOSED_F["max_gap"]:
        return False, f"gap {fmt_T(g)} > {PROPOSED_F['max_gap']}"
    if r is None or r > PROPOSED_F["max_ratio"]:
        return False, f"ratio {fmt_T(r)} > {PROPOSED_F['max_ratio']:g}"
    return True, "keep"


# --------------------------------------------------------------------------- #
# Pretrade (Rule F) veto sweep — per-file scalars, tiny n
# --------------------------------------------------------------------------- #

SNAP_FEATURES = ["ratio", "gap", "baseline", "bid", "ask", "spread_pct"]


def snap_frontier(feats, dirs, positives, negatives, neutrals, npos_total,
                  budgets=(0, 1, 2)):
    """KEEP-condition sweep (fire only if every feat crosses). Thresholds from
    pos values; pos with a missing feature counts as lost under the veto."""
    grids = []
    for feat, lower in zip(feats, dirs):
        vals = sorted({f["snap"].get(feat) for f in positives
                       if f["snap"].get(feat) is not None})
        if not vals:
            return None
        grids.append(vals)

    def keeps(rec, Ts):
        for feat, lower, T in zip(feats, dirs, Ts):
            v = rec["snap"].get(feat)
            if v is None or not crosses(v, T, lower):
                return False
        return True

    combos = [()]
    for g in grids:
        combos = [c + (t,) for c in combos for t in g]
    out = {}
    for k in budgets:
        need = npos_total - k
        best = None
        for Ts in combos:
            caught = [f["name"] for f in positives if keeps(f, Ts)]
            if len(caught) < need:
                continue
            neg = [f["name"] for f in negatives if keeps(f, Ts)]
            neu = [f["name"] for f in neutrals if keeps(f, Ts)]
            sc = (len(neg), len(neu))
            if best is None or sc < best[0]:
                best = (sc, {"T": Ts, "caught": len(caught),
                             "lost": [f["name"] for f in positives if f["name"] not in caught],
                             "neg": neg, "neu": neu, "med_ttf": None})
        out[k] = best[1] if best else None
    return out


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def fmt_T(T):
    if isinstance(T, tuple):
        return " & ".join(fmt_T(t) for t in T)
    if T is None:
        return "-"
    return f"{T:g}" if abs(T) >= 0.01 else f"{T:.4g}"


def fmt_entry(e, nneg, nneu):
    if e is None:
        return "unattainable"
    med = f"{e['med_ttf']:.2f}s" if e["med_ttf"] is not None else "-"
    return (f"T={fmt_T(e['T'])} -> neg {len(e['neg'])}/{nneg}, "
            f"neu {len(e['neu'])}/{nneu}, medTTF {med}")


def frontier_rows(name, fr, nneg, nneu, budgets=POS_BUDGETS):
    lines = []
    for k in budgets:
        e = fr.get(k) if fr else None
        detail = ""
        if e and e["neg"]:
            detail = f" — surviving neg: {', '.join(sorted(e['neg']))}"
        if e and e["lost"]:
            detail += f" — pos lost: {', '.join(sorted(e['lost']))}"
        lines.append(f"| {name if k == budgets[0] else ''} | {k} | "
                     f"{fmt_entry(e, nneg, nneu)}{detail} |")
    return lines


def main():
    files = discover()
    recs = [load(p) for p in files]
    by = lambda lab: [r for r in recs if r["label"] == lab]
    pos, neg, neu = by("pos"), by("neg"), by("neu")
    print(f"Loaded {len(recs)} labeled recordings >= {MIN_DATE}: "
          f"{len(pos)} pos / {len(neg)} neg / {len(neu)} neu")

    # ---- Baseline scoreboard under CURRENT rules (from recorded fires) ----
    def fire_mix(rows):
        mix = {}
        for r in rows:
            if r["fired"]:
                mix[r["fire_rule"]] = mix.get(r["fire_rule"], 0) + 1
        return mix

    sb = {}
    for lab, rows in (("pos", pos), ("neg", neg), ("neu", neu)):
        fired = [r for r in rows if r["fired"]]
        sb[lab] = (len(fired), len(rows), fire_mix(rows))
        print(f"  {lab}: {len(fired)}/{len(rows)} fired  rules={sb[lab][2]}")

    # ---- Part 1 population: trade-path recordings ----
    p1_pos = [r for r in pos if r["n_trades"] > 0]
    p1_neg = [r for r in neg if r["n_trades"] > 0]
    p1_neu = [r for r in neu if r["n_trades"] > 0]
    npos1 = len(p1_pos)
    print(f"Part 1 (trade rows): {npos1} pos / {len(p1_neg)} neg / {len(p1_neu)} neu")

    # Candidate columns with coverage in both classes.
    cols = sorted({c for r in p1_pos + p1_neg for c in r["series"]})
    eligible = []
    for c in cols:
        cp = sum(1 for r in p1_pos if r["series"].get(c)) / max(npos1, 1)
        cn = sum(1 for r in p1_neg if r["series"].get(c)) / max(len(p1_neg), 1)
        if cp >= COVERAGE_GATE and cn >= COVERAGE_GATE:
            eligible.append(c)
    print(f"{len(eligible)} columns pass the {COVERAGE_GATE:.0%} coverage gate")

    ranked = []
    for c in eligible:
        b = best_direction(c, p1_pos, p1_neg, p1_neu, npos1)
        if b:
            ranked.append((b[0], c, b[1], b[2]))
    ranked.sort(key=lambda x: x[0])

    # ---- Live-rule re-sweeps ----
    rule_fronts = []
    for rname, c, lower in RULES_1D:
        fr = col_frontier(c, lower, p1_pos, p1_neg, p1_neu, npos1)
        rule_fronts.append((f"{rname}: {c} {'<=' if lower else '>='} T", fr))
    for rname, c1, l1, c2, l2 in RULES_2D:
        fr = pair_frontier(c1, l1, c2, l2, p1_pos, p1_neg, p1_neu, npos1)
        rule_fronts.append((f"{rname}: {c1}{'<=' if l1 else '>='}T1 AND "
                            f"{c2}{'<=' if l2 else '>='}T2", fr))

    # ---- AND-pair sweep over the top singles ----
    pool = [(c, lower) for _k, c, lower, _fr in ranked[:PAIR_POOL]]
    best_single_neg0 = min((len(fr[0]["neg"]) for _k, _c, _l, fr in ranked if fr.get(0)),
                           default=99)
    pair_results = []
    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            c1, l1 = pool[i]
            c2, l2 = pool[j]
            fr = pair_frontier(c1, l1, c2, l2, p1_pos, p1_neg, p1_neu, npos1)
            if fr and fr.get(0) and len(fr[0]["neg"]) < best_single_neg0:
                key = frontier_key(fr)
                pair_results.append((key, f"{c1}{'<=' if l1 else '>='} & "
                                          f"{c2}{'<=' if l2 else '>='}", fr))
    pair_results.sort(key=lambda x: x[0])

    # ---- OR-level simulation: sanity vs recorded fires, then leg tightening --
    cur_n, cur_fires = or_confusion(CUR, p1_pos, p1_neg, p1_neu)
    mismatch = []
    for r in p1_pos + p1_neg + p1_neu:
        sim = cur_fires[r["name"]]
        rec_rule = r["fire_rule"] if r["fired"] else None
        sim_rule = sim[0] if sim else None
        if sim_rule != rec_rule:
            mismatch.append(f"{r['name']}({r['label']}): live={rec_rule or 'no-fire'} "
                            f"sim={sim_rule or 'no-fire'}")
    print(f"OR sim @ current thresholds: pos {cur_n['pos']}/{npos1}, "
          f"neg {cur_n['neg']}/{len(p1_neg)}, neu {cur_n['neu']}/{len(p1_neu)}; "
          f"{len(mismatch)} file(s) disagree with the recorded fire: {mismatch}")

    leg_fronts = {}
    for leg in ("A", "B", "C", "D", "E"):
        leg_fronts[leg] = leg_frontier(leg, CUR, p1_pos, p1_neg, p1_neu)

    # Adaptive greedy combine: apply legs in order of individual neg kills,
    # re-sweeping each against the working ruleset (0 pos lost at OR level).
    order = sorted(leg_fronts,
                   key=lambda lg: (leg_fronts[lg][0]["n"]["neg"]
                                   if leg_fronts[lg][0] else 99))
    comboP = dict(CUR)
    for leg in order:
        fr = leg_frontier(leg, comboP, p1_pos, p1_neg, p1_neu, budgets=(0,))
        if fr[0]:
            comboP.update(fr[0]["ov"])
    combo_n, combo_fires = or_confusion(comboP, p1_pos, p1_neg, p1_neu)

    # ---- Proposed Rule-F veto ----
    f_all = [r for r in recs if r["fire_rule"] == "F"]
    f_out = [(r, *f_keep(r)) for r in f_all]

    # ---- Part 2: pretrade fires ----
    f_pos = [r for r in pos if r["fire_rule"] == "F"]
    f_neg = [r for r in neg if r["fire_rule"] == "F"]
    f_neu = [r for r in neu if r["fire_rule"] == "F"]
    f_ctl = [r for r in neg if not r["fired"] and r["n_trades"] == 0]
    print(f"Part 2 (Rule-F fires): {len(f_pos)} pos / {len(f_neg)} neg / "
          f"{len(f_neu)} neu; {len(f_ctl)} quote-only no-fire controls")

    snap_singles = []
    for feat in SNAP_FEATURES:
        for lower in (False, True):
            fr = snap_frontier([feat], [lower], f_pos, f_neg, f_neu, len(f_pos))
            if fr and any(fr.values()):
                key = frontier_key(fr, budgets=(0, 1, 2))
                snap_singles.append((key, f"{feat} {'<=' if lower else '>='} T", fr))
    snap_singles.sort(key=lambda x: x[0])

    snap_pairs = []
    for i in range(len(SNAP_FEATURES)):
        for j in range(i + 1, len(SNAP_FEATURES)):
            for l1 in (False, True):
                for l2 in (False, True):
                    fe = [SNAP_FEATURES[i], SNAP_FEATURES[j]]
                    fr = snap_frontier(fe, [l1, l2], f_pos, f_neg, f_neu, len(f_pos))
                    if fr and fr.get(0):
                        key = frontier_key(fr, budgets=(0, 1, 2))
                        snap_pairs.append(
                            (key, f"{fe[0]} {'<=' if l1 else '>='} T1 AND "
                                  f"{fe[1]} {'<=' if l2 else '>='} T2", fr))
    snap_pairs.sort(key=lambda x: x[0])

    # ------------------------------------------------------------------ #
    # Write the report
    # ------------------------------------------------------------------ #
    L = []
    A = L.append
    nneg1, nneu1 = len(p1_neg), len(p1_neu)
    A("# Surge-predictor analysis — labeled window 2026-07-09 .. 2026-07-30")
    A("")
    A(f"Generated by `analyze_trade_mole/analyze_surge_predictors_v3.py`. "
      f"{len(recs)} recordings: **{len(pos)} pos / {len(neg)} neg / {len(neu)} neu** "
      f"(labels `_pos_`/`_neg_`/`_neu_`, dates >= {MIN_DATE}; June files excluded).")
    A("")
    A("**Locked methodology (user decisions 2026-07-30):** pos = caught if the "
      "candidate crosses ANYWHERE in the recording (median TTF is a tiebreaker); "
      "neu is a secondary objective (calibrated pos-vs-neg, neu kill-rate "
      "reported); the full 0/1/2/3-pos-lost frontier is reported per candidate; "
      "pretrade fires use in-file quote-snapshot features only.")
    A("")
    A("## ⚠ Censoring caveat")
    A("")
    A("Recording STOPS at the fire. For every fired file we only see pre-fire "
      "rows, so: a pos counted LOST under a stricter threshold might have fired "
      "later live (pessimistic), and a neg counted KILLED might have fired after "
      "the recorded span (optimistic). Frontier numbers are bounds, not exact "
      "live outcomes. De-censoring requires recording past the fire (see "
      "Refinements).")
    A("")
    A("## Baseline — CURRENT rules on this window")
    A("")
    A("| label | fired | fire-rule mix |")
    A("|---|---|---|")
    for lab in ("pos", "neg", "neu"):
        n, tot, mix = sb[lab]
        mixs = ", ".join(f"{k}:{v}" for k, v in sorted(mix.items()))
        A(f"| {lab} | {n}/{tot} | {mixs} |")
    A("")
    A("Recall is already 100%; **the problem is precision** (23/30 neg fire). "
      "Everything below is about killing neg/neu fires while keeping pos.")
    A("")

    # ---- Headline recommendation ----
    def _fires_str(fires, rows):
        out = []
        for r in rows:
            f = fires.get(r["name"])
            if f:
                out.append(f"{r['name']}:{f[0]}@{f[1]:.1f}s")
        return ", ".join(sorted(out))

    A("## Recommended ruleset (headline)")
    A("")
    A("**Trade path** — tighten the legs jointly (each verified at the OR level, "
      "0 pos lost; details in the leg-tightening section):")
    A("")
    A("| leg | current | proposed |")
    A("|---|---|---|")
    prop_desc = {
        "B": ("bsln_over_prev_trd_gap >", "B"),
        "C": ("bslnITI_on_last3trades_avrgITI >", "C"),
        "A_qu": ("quote_updates_10s >=", "A_qu"),
        "A_rate": ("A: dollar_rate_5s >=", "A_rate"),
        "D_iti": ("measured_ITIsec <=", "D_iti"),
        "D_tr": ("D: trades_in_5s >=", "D_tr"),
        "E_rate": ("dollar_rate_5s >=", "E_rate"),
        "E_pc": ("E: price_change_since_start >=", "E_pc"),
    }
    for key, (desc, pk) in prop_desc.items():
        cur_v, new_v = CUR[pk], comboP[pk]
        mark = "" if cur_v == new_v else " **<-**"
        A(f"| {desc} | {fmt_T(cur_v)} | {fmt_T(new_v)}{mark} |")
    A("")
    A(f"Combined trade-path outcome: pos **{combo_n['pos']}/{npos1}**, neg "
      f"**{combo_n['neg']}/{len(p1_neg)}** (was {cur_n['neg']}), neu "
      f"{combo_n['neu']}/{len(p1_neu)} (was {cur_n['neu']}); pos median TTF "
      f"{combo_n['med_ttf']:.2f}s (was {cur_n['med_ttf']:.2f}s).")
    surv = _fires_str(combo_fires, p1_neg)
    if surv:
        A(f"Surviving neg fires: {surv}.")
    slowed = []
    for r in p1_pos:
        a, b = cur_fires[r["name"]], combo_fires[r["name"]]
        if a and b and b[1] - a[1] > 0.5:
            slowed.append(f"{r['name']} {a[0]}@{a[1]:.1f}s -> {b[0]}@{b[1]:.1f}s")
    if slowed:
        A(f"Latency cost (pos fires delayed >0.5s): {'; '.join(slowed)}.")
    A("")
    A("Caveats on the exact values: they sit ON observed extremes (e.g. "
      "`A_rate` lands on a neu file's rate because beyond ~$55/s further "
      "raises only chase neu kills — every neg A-fire is dead by ~$55; "
      "similarly `D_iti`=0.276 is KIDZ's exact print). For wiring, prefer "
      "round numbers just past the boundary that matters and treat the neu "
      "tail as optional: e.g. C>1500, A_rate>=60 (or >=150 to also kill the "
      "ZCMD neu), D_iti<=0.28, E_pc>=0.012.")
    A("")
    n_keep = sum(1 for _r, k, _w in f_out if k)
    kept_by_lab = {}
    for r, k, _w in f_out:
        kept_by_lab.setdefault(r["label"], [0, 0])
        kept_by_lab[r["label"]][0] += int(k)
        kept_by_lab[r["label"]][1] += 1
    ks = ", ".join(f"{lab} {v[0]}/{v[1]}" for lab, v in sorted(kept_by_lab.items()))
    A(f"**Rule F (pretrade)** — add three keep-conditions (fire only if all "
      f"hold): no trade printed yet, pretrade gap <= {PROPOSED_F['max_gap']}, "
      f"ratio <= {PROPOSED_F['max_ratio']:g}. Result on the {len(f_out)} "
      f"recorded F-fires: keeps {ks} (per-file table in Part 2). "
      f"**Low confidence — n=5 pos.**")
    A("")
    A(f"## Part 1 — trade-path variables ({npos1} pos / {nneg1} neg / {nneu1} neu "
      "recordings with TRADE rows)")
    A("")
    A(f"Pos-lost budgets are out of {npos1} trade-path pos (the 5 pretrade-fired "
      "pos are Part 2's population). `neg x/y` = neg recordings still crossing "
      "at that threshold. Columns must be present in >= "
      f"{COVERAGE_GATE:.0%} of pos and neg files.")
    A("")
    A(f"### Single-column leaderboard (top {TOP_SINGLES} of {len(ranked)})")
    A("")
    A("**Read with care:** no single column separates well (best 0-loss kill is "
      "only a few negs), and the top of the board is dominated by LIQUIDITY / "
      "PRICE-LEVEL context (`tws_volume_rate_per_min`, `bid`/`ask`/`price` <= "
      "~$4) — i.e. 'this window's pos were quieter, cheaper names', which may "
      "not generalize. `tws_volume_rate_per_min <=` is at least consistent "
      "with the SMCI hard-negative finding (already-busy tape = bad fire); the "
      "price-level columns are more likely small-sample artifacts.")
    A("")
    A("| column (direction) | pos lost | best threshold -> false fires |")
    A("|---|---|---|")
    for _k, c, lower, fr in ranked[:TOP_SINGLES]:
        L.extend(frontier_rows(f"`{c}` {'<=' if lower else '>='}", fr, nneg1, nneu1))
    A("")
    A("### Rule columns as SOLE predictors (context only)")
    A("")
    A("Each live-rule column forced to carry every pos alone. NOT wiring advice "
      "— the live trigger is an OR where each leg only needs its own pos (see "
      "the leg-tightening section) — but it shows single-rule separability: "
      "e.g. Rule C alone cannot reach 16/16 at any threshold.")
    A("")
    A("| rule | pos lost | best threshold -> false fires |")
    A("|---|---|---|")
    for rname, fr in rule_fronts:
        L.extend(frontier_rows(rname, fr, nneg1, nneu1))
    A("")
    A("### OR-level leg tightening (the actionable table)")
    A("")
    A(f"One leg tightened at a time, the others held at live thresholds; "
      f"pos/neg/neu counted at the RULESET level (a pos dropped by the "
      f"tightened leg still counts if another leg catches it in the recorded "
      f"rows). Sim at current thresholds: pos {cur_n['pos']}/{npos1}, neg "
      f"{cur_n['neg']}/{nneg1}, neu {cur_n['neu']}/{nneu1}"
      + (f" — {len(mismatch)} file(s) differ from the recorded live fire "
         f"({'; '.join(mismatch)})" if mismatch else "") + ".")
    A("")
    A("| leg | pos lost | proposed setting -> ruleset outcome |")
    A("|---|---|---|")
    for leg in ("A", "B", "C", "D", "E"):
        for k in (0, 1, 2):
            e = leg_fronts[leg].get(k)
            if e is None:
                A(f"| {leg if k == 0 else ''} | {k} | unattainable |")
                continue
            ov = ", ".join(f"{kk}={fmt_T(vv)}" for kk, vv in e["ov"].items())
            lost = [r["name"] for r in p1_pos if not e["fires"].get(r["name"])]
            surv = _fires_str(e["fires"], p1_neg)
            med = (f", medTTF {e['n']['med_ttf']:.2f}s"
                   if e["n"]["med_ttf"] is not None else "")
            A(f"| {leg if k == 0 else ''} | {k} | {ov} -> pos {e['n']['pos']}"
              f"/{npos1}, neg {e['n']['neg']}/{nneg1}, neu {e['n']['neu']}"
              f"/{nneu1}{med}"
              + (f" — pos lost: {', '.join(lost)}" if lost else "")
              + (f" — surviving: {surv}" if surv else "") + " |")
    A("")
    changed = ", ".join(f"{kk}={fmt_T(vv)}" for kk, vv in comboP.items()
                        if CUR[kk] != vv) or "(no change)"
    A(f"**Combined (greedy, 0 pos lost at every step):** {changed} -> "
      f"pos {combo_n['pos']}/{npos1}, neg {combo_n['neg']}/{nneg1}, "
      f"neu {combo_n['neu']}/{nneu1}, pos medTTF {combo_n['med_ttf']:.2f}s.")
    A("")
    A("### AND-pairs that beat the best single column at 0 pos lost")
    A("")
    if pair_results:
        A("| pair | pos lost | best thresholds -> false fires |")
        A("|---|---|---|")
        for _k, name, fr in pair_results[:10]:
            L.extend(frontier_rows(name, fr, nneg1, nneu1))
    else:
        A(f"None — no same-row AND pair beats the best single column "
          f"({best_single_neg0} neg) at 0 pos lost.")
    A("")
    A(f"## Part 2 — pretrade Rule-F fires ({len(f_pos)} pos / {len(f_neg)} neg / "
      f"{len(f_neu)} neu; {len(f_ctl)} no-fire controls)")
    A("")
    A(f"**LOW CONFIDENCE: n = {len(f_pos)} pos.** Candidate KEEP-conditions on "
      "the fire (fire only if condition holds), swept on snapshot features.")
    A("")
    A("| file | label | ratio | gap s | bslnITI | bid | ask | spread% |")
    A("|---|---|---|---|---|---|---|---|")
    for r in sorted(f_pos + f_neu + f_neg + f_ctl, key=lambda x: (x["label"], x["name"])):
        s = r["snap"]
        cells = [fmt_T(s.get(k)) for k in SNAP_FEATURES]
        tag = r["label"] + ("" if r["fired"] else " (no-fire ctl)")
        A(f"| {r['name']} | {tag} | " + " | ".join(cells) + " |")
    A("")
    A("### Single-feature keep-conditions")
    A("")
    A("| condition | pos lost | best threshold -> false fires |")
    A("|---|---|---|")
    for _k, name, fr in snap_singles[:6]:
        L.extend(frontier_rows(name, fr, len(f_neg), len(f_neu), budgets=(0, 1, 2)))
    A("")
    A("### Two-feature keep-conditions (top 5)")
    A("")
    A("| condition | pos lost | best thresholds -> false fires |")
    A("|---|---|---|")
    for _k, name, fr in snap_pairs[:5]:
        L.extend(frontier_rows(name, fr, len(f_neg), len(f_neu), budgets=(0, 1, 2)))
    A("")
    A("### Proposed Rule-F veto — per-file outcome")
    A("")
    A("Keep a pretrade fire only if: **no trade printed yet** AND **gap <= "
      f"{PROPOSED_F['max_gap']}** AND **ratio <= {PROPOSED_F['max_ratio']:g}**. "
      "Rationale: F is a PRE-trade rule, yet FFAI fired after 3 prints on a "
      "tape already -0.6% below start (F bypasses the price-drop veto); the "
      "gap cap says the last known trade was within ~1s of the snapshot (all 5 "
      "pos cluster at 1.0-1.18s, four killed negs at 1.41-1.72s); the ratio "
      "cap rejects the quasi-never-trading + one-dust-print signature (LITS "
      "34200 vs pos max 3420).")
    A("")
    A("| file | label | verdict | why |")
    A("|---|---|---|---|")
    for r, keep, why in sorted(f_out, key=lambda x: (x[0]["label"], x[0]["name"])):
        A(f"| {r['name']} | {r['label']} | {'KEEP' if keep else 'VETO'} | {why} |")
    A("")
    A("## Refinements / next steps")
    A("")
    A("1. **De-censor the recordings** — keep recording N (~30) seconds PAST "
      "the buy-signal (shadow mode, no orders). Every number in this report "
      "is a bound because files stop at the fire; post-fire tape would show "
      "whether killed negs re-fire later and whether lost pos recover.")
    A("2. **Grow the neg/neu label set before trusting the F-veto** — n=5 "
      "pretrade pos; the gap cap rides on a 1.18-vs-1.41s margin. A week of "
      "new pretrade fires (kept + vetoed, logged either way) would confirm or "
      "kill it cheaply. Wire the veto as OBSERVABILITY FIRST (log "
      "`would_veto` without suppressing) if in doubt.")
    A("3. **Universe-context join** (declined this pass): float/mcap/avg-vol "
      "for the feature-starved Rule-F fires; `tws_volume_rate_per_min <=` "
      "ranking #1 suggests liquidity context genuinely matters.")
    A("4. **Time-of-day windows** (declined this pass): FFAI fired at 04:00 "
      "arm-time on a 00:52 trigger; clerk-trigger-conditions windows already "
      "exist as the natural place for a fire-time gate.")
    A("5. **Extend the price-drop veto to Rule F** — implied by the FFAI "
      "no-prints keep-condition, but worth doing in trade-mole regardless of "
      "the rest of the veto.")
    A("")
    A("## Per-file appendix")
    A("")
    A("| file | label | cols | rows | trades | fired | rule | TTF s | reason |")
    A("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(recs, key=lambda x: (x["label"], x["name"])):
        ttf = f"{r['fire_t']:.3f}" if r["fire_t"] is not None else "-"
        A(f"| {r['name']} | {r['label']} | {r['ncols']} | {r['nrows']} | "
          f"{r['n_trades']} | {'Y' if r['fired'] else 'N'} | {r['fire_rule'] or '-'} "
          f"| {ttf} | {r['fire_reason'][:60]} |")
    A("")

    with open(REPORT, "w") as fh:
        fh.write("\n".join(L))
    print(f"\nReport written: {REPORT}")


if __name__ == "__main__":
    main()
