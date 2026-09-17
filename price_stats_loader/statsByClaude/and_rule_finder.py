#!/usr/bin/env python3
"""Best multi-leg AND threshold rules over every sentiment-score column, for
selecting big movers (`fromPR_High(%) >= threshold`, default 40).

Sibling of `threshold_finder_finbert_finmarba.py`, which is frozen at pairs +
triples over 5 hand-picked columns. This one is built for the wide case: ~25
auto-detected score columns and rules of up to 4 legs, i.e. ~1.7M candidate
rules, which the old naive `itertools.product` engine cannot reach.

Two things make that tractable and readable:

  * **Monotone DFS pruning.** AND is monotone decreasing in both fires and TP,
    so a prefix that already fires on < --min-fires rows (or has too few TP to
    beat base) prunes its whole subtree. ~1.7M rules in ~15s.
  * **Rule minimization.** With collinear and *saturated* columns (several body
    encoders sit at 0.998+ on 40-60% of rows) the raw top-N is one rule wearing
    interchangeable inert legs. Every rule is reduced to its minimal equivalent
    form (drop any leg that does not change the fire set), then de-duplicated on
    fire signature, then near-clones (Jaccard >= --jaccard) are suppressed at
    display time.

IMPORTANT — thresholds are chosen on the SAME rows they are scored on. With
~1.7M candidate rules against a few dozen movers, the top rule's precision is an
**UPPER BOUND, not an estimate**; several rules will look excellent by chance
alone. The encoder/keyword scores themselves are honest out-of-sample (all
models were trained elsewhere) — it is the threshold search that is fitted here.
To turn the headline number into an estimate, re-run this search on shuffled
labels and subtract the best rule's score on noise, or search a larger corpus
and validate on this file as a holdout.

Run (defaults point at the 05 corpus, threshold 40):
    /home/tom/venv/bin/python and_rule_finder.py
"""
import argparse
import heapq
import itertools
import math
import pathlib
import sys
from datetime import date

import numpy as np
import pandas as pd

DEFAULT_TSV = (
    "../outputs/"
    "05_PRstats_2026-07-24-2026-07-30_kwScored_FromPRhigh_11EncBodyHeadScored_CoffeeF.tsv"
)
TARGET = "fromPR_High(%)"
DATE_COL = "ArrivalDate"

# Quantile grid each column contributes as candidate cutoffs, loose -> strict.
QUANTILES_GE = [0.50, 0.65, 0.78, 0.88, 0.94]
QUANTILES_LE = [0.50, 0.35, 0.22, 0.12, 0.06]
# --min-fires floors reported in the sensitivity section.
SENS_FLOORS = [10, 15, 20, 25]
# Finalists retained per leaderboard during the DFS. Metrics are scalars, so
# this is cheap; masks are recomputed for finalists only (storing 1.7M masks
# would be ~0.5 GB).
TOP_KEEP = 20000


# --------------------------------------------------------------------------- #
# CLI spec parsing  (copied from threshold_finder_finbert_finmarba.py)
# --------------------------------------------------------------------------- #
def parse_columns(specs):
    """['Prosus_pos_hd>=', 'FinMarBa_ind_hd<='] -> [('Prosus_pos_hd','>='), ...]"""
    out = []
    for s in specs:
        for op in (">=", "<="):
            if s.endswith(op):
                name = s[:-len(op)].strip()
                if not name:
                    sys.exit(f"--columns: empty column name in {s!r}")
                out.append((name, op))
                break
        else:
            sys.exit(f"--columns: {s!r} must end with '>=' or '<='")
    return out


def parse_anchors(specs):
    """['Prosus_pos_hd=0.5,0.6'] -> {'Prosus_pos_hd': [0.5, 0.6]}"""
    out = {}
    for s in specs:
        if "=" not in s:
            sys.exit(f"--anchors: {s!r} must look like NAME=v1,v2")
        name, vals = s.split("=", 1)
        try:
            out[name.strip()] = [float(v) for v in vals.split(",") if v.strip()]
        except ValueError:
            sys.exit(f"--anchors: non-numeric value in {s!r}")
    return out


# --------------------------------------------------------------------------- #
# stats helpers  (copied from threshold_finder_finbert_finmarba.py)
# --------------------------------------------------------------------------- #
def wilson(k, n, z=1.96):
    """Wilson score 95% interval for a single proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def auc_mannwhitney(scores, labels):
    """AUC via the Mann-Whitney rank formula (ties handled). Higher score is
    expected to predict label==True; returns nan if a class is missing."""
    s = pd.to_numeric(scores, errors="coerce").values
    y = np.asarray(labels, dtype=bool)
    ok = ~np.isnan(s)
    s, y = s[ok], y[ok]
    pos, neg = y.sum(), (~y).sum()
    if pos == 0 or neg == 0:
        return float("nan")
    order = np.argsort(s, kind="mergesort")
    ranks = np.empty(len(s), float)
    sv = s[order]
    i = 0
    while i < len(sv):
        j = i
        while j + 1 < len(sv) and sv[j + 1] == sv[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2.0 + 1.0  # average rank, 1-based
        i = j + 1
    sum_pos = ranks[y].sum()
    return (sum_pos - pos * (pos + 1) / 2.0) / (pos * neg)


def fire_mask(values, op, cutoff):
    v = pd.to_numeric(values, errors="coerce")
    fired = (v >= cutoff) if op == ">=" else (v <= cutoff)
    return (fired & v.notna()).values


def cell(fired, movers, base, total_movers):
    """Return a metrics dict for a boolean fire mask."""
    n = int(fired.sum())
    tp = int((fired & movers).sum())
    prec = tp / n if n else float("nan")
    recall = tp / total_movers if total_movers else float("nan")
    lift = prec / base if (n and base) else float("nan")
    lo, hi = wilson(tp, n)
    f1 = (2 * prec * recall / (prec + recall)) if (prec + recall) else 0.0
    b = 0.5
    denom = (b * b * prec + recall)
    f05 = ((1 + b * b) * prec * recall / denom) if denom else 0.0
    return dict(n=n, tp=tp, prec=prec, recall=recall, lift=lift,
                lo=lo, hi=hi, f1=f1, f05=f05)


def quantile_cutoffs(values, quantiles, decimals=3):
    """Quantile grid, de-duplicated on the rounded cutoff value. A saturated
    column (e.g. 60% of rows at 0.998) collapses to a single usable cutoff."""
    v = pd.to_numeric(values, errors="coerce").dropna().values
    cuts = np.quantile(v, quantiles)
    seen, out = set(), []
    for q, c in zip(quantiles, cuts):
        r = round(float(c), decimals)
        if r not in seen:
            seen.add(r)
            out.append((q, r))
    return out


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #
def fmt_pct(x):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x*100:.0f}%"


def fmt_lift(x):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.2f}x"


def fmt_num(x, nd=1):
    return "-" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def rule_str(legs):
    return " AND ".join(f"`{c}` {op} {cut:g}" for c, op, cut in legs)


def rule_str_plain(legs):
    return " AND ".join(f"{c}{op}{cut:g}" for c, op, cut in legs)


# --------------------------------------------------------------------------- #
# column detection
# --------------------------------------------------------------------------- #
def detect_columns(df):
    """Every `*_ss_hd` / `*_ss_bd` plus the kw15 probabilities, in file order."""
    out = []
    for c in df.columns:
        if c.endswith(("_ss_hd", "_ss_bd")) or c in ("kw15_proba_bd",
                                                     "kw15_proba_hd"):
            out.append((c, ">="))
    return out


def saturation_stats(v):
    """(#unique rounded values, q50, q94, max, fraction within 1e-3 of max)."""
    v = v[~np.isnan(v)]
    if v.size == 0:
        return (0, float("nan"), float("nan"), float("nan"), float("nan"))
    mx = float(np.max(v))
    return (int(np.unique(np.round(v, 4)).size),
            float(np.quantile(v, 0.50)), float(np.quantile(v, 0.94)), mx,
            float((v >= mx - 1e-3).mean()))


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #
class TopK:
    """Bounded max-by-key collection; keeps the K best payloads seen."""

    def __init__(self, k):
        self.k = k
        self.h = []
        self._c = itertools.count()

    def push(self, key, payload):
        if len(self.h) < self.k:
            heapq.heappush(self.h, (key, next(self._c), payload))
        elif key > self.h[0][0]:
            heapq.heapreplace(self.h, (key, next(self._c), payload))

    def items(self):
        return [p for _, _, p in sorted(self.h, key=lambda t: t[0], reverse=True)]


def run_search(M, Y, bycol, max_legs, min_fires, min_tp, floors):
    """Exhaustive DFS over one-leg-per-column AND rules, pruning any prefix that
    can no longer reach min_fires / min_tp (both monotone decreasing in AND).

    Returns (prec_topk_by_floor, f05_topk, best_by_depth, n_evaluated)."""
    ncol = len(bycol)
    prec_top = {f: TopK(TOP_KEEP) for f in floors}
    f05_top = TopK(TOP_KEEP)
    best_depth = {}
    n_eval = [0]
    total_movers = int(Y.sum())

    def record(mask, chosen):
        n = int(mask.sum())
        tp = int((mask & Y).sum())
        prec = tp / n
        rec = tp / total_movers if total_movers else 0.0
        denom = 0.25 * prec + rec
        f05 = (1.25 * prec * rec / denom) if denom else 0.0
        d = len(chosen)
        n_eval[0] += 1
        payload = (tuple(chosen), n, tp, prec)
        for f in floors:
            if n >= f:
                prec_top[f].push((prec, n), payload)
        f05_top.push((f05, n), payload)
        cur = best_depth.get(d)
        if cur is None or (prec, n) > (cur[3], cur[1]):
            best_depth[d] = payload

    def dfs(ci, mask, chosen):
        if len(chosen) >= 1:
            record(mask, chosen)
        if len(chosen) == max_legs:
            return
        for c2 in range(ci + 1, ncol):
            for i in bycol[c2]:
                m2 = mask & M[i]
                if int(m2.sum()) < min_fires:
                    continue
                if int((m2 & Y).sum()) < min_tp:
                    continue
                dfs(c2, m2, chosen + [i])

    for c in range(ncol):
        for i in bycol[c]:
            m = M[i]
            if int(m.sum()) < min_fires:
                continue
            if int((m & Y).sum()) < min_tp:
                continue
            dfs(c, m, [i])

    return prec_top, f05_top, best_depth, n_eval[0]


def and_mask(M, idx):
    m = M[idx[0]].copy()
    for i in idx[1:]:
        m &= M[i]
    return m


def minimize(M, idx):
    """Drop any leg whose removal leaves the fire set unchanged. Greedy, repeated
    to fixpoint; always keeps at least one leg."""
    idx = list(idx)
    target = and_mask(M, idx)
    changed = True
    while changed and len(idx) > 1:
        changed = False
        for i in list(idx):
            rest = [j for j in idx if j != i]
            if not rest:
                continue
            if np.array_equal(and_mask(M, rest), target):
                idx = rest
                changed = True
                break
    return tuple(idx), target


def jaccard(a, b):
    inter = int((a & b).sum())
    union = int((a | b).sum())
    return inter / union if union else 0.0


def finalize(payloads, M, movers, base, total_movers, jac, top, want_floor=None):
    """Minimize -> exact-dedupe on fire signature -> near-clone suppression.
    Returns [(legs_idx, mask, metrics, n_clones_suppressed)] of length <= top."""
    seen = {}
    order = []
    for idx, n, tp, prec in payloads:
        midx, mask = minimize(M, idx)
        if want_floor is not None and int(mask.sum()) < want_floor:
            continue
        sig = mask.tobytes()
        if sig in seen:
            # keep the shortest expression of an identical fire set
            if len(midx) < len(seen[sig][0]):
                seen[sig] = (midx, mask)
            continue
        seen[sig] = (midx, mask)
        order.append(sig)

    kept = []
    for sig in order:
        midx, mask = seen[sig]
        clone_of = next((k for k in kept if jaccard(mask, k[1]) >= jac), None)
        if clone_of is not None:
            clone_of[3][0] += 1        # fold into the rule it restates
            continue
        if len(kept) >= top:
            continue                   # full: keep scanning only to count clones
        kept.append([midx, mask, cell(mask, movers, base, total_movers), [0]])
    return kept


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv", nargs="?", default=DEFAULT_TSV)
    ap.add_argument("--threshold", type=float, default=40.0,
                    help="fromPR_High(%%) cut defining a mover (default 40)")
    ap.add_argument("--max-legs", type=int, default=4)
    ap.add_argument("--min-fires", type=int, default=12,
                    help="a rule must fire on this many rows to be reported")
    ap.add_argument("--jaccard", type=float, default=0.90,
                    help="fire-set overlap above which a rule is a near-clone")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--columns", nargs="+", default=None, metavar="NAME>=|NAME<=",
                    help="override auto-detected leg columns")
    ap.add_argument("--anchors", nargs="+", default=None, metavar="NAME=v1,v2",
                    help="extra round cutoffs for a column")
    ap.add_argument("--quantiles", nargs="+", type=float, default=None,
                    help="override the >= quantile grid")
    ap.add_argument("--out", default=None, help="report path (default dated .md)")
    args = ap.parse_args()

    path = pathlib.Path(args.tsv)
    if not path.exists():
        sys.exit(f"input not found: {path}")
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)
    if TARGET not in df.columns:
        sys.exit(f"input has no {TARGET!r} column")

    t = pd.to_numeric(df[TARGET], errors="coerce")
    keep = t.notna().values
    df = df.loc[keep].reset_index(drop=True)
    t = t[keep].reset_index(drop=True)

    columns = parse_columns(args.columns) if args.columns else detect_columns(df)
    columns = [(c, op) for c, op in columns if c in df.columns]
    if not columns:
        sys.exit("no usable leg columns found")
    anchors = parse_anchors(args.anchors) if args.anchors else {}
    qs_ge = args.quantiles if args.quantiles else QUANTILES_GE

    movers = (t.values >= args.threshold)
    total_movers = int(movers.sum())
    base = movers.mean()
    if total_movers == 0:
        sys.exit(f"no rows reach {TARGET} >= {args.threshold:g}")

    # ---- legs -------------------------------------------------------------
    legs = []           # (col, op, cutoff)
    bycol = []          # per column: list of leg indices
    sat = {}            # per column: saturation stats + grid size
    for c, op in columns:
        v = pd.to_numeric(df[c], errors="coerce").values
        grid = quantile_cutoffs(df[c], qs_ge if op == ">=" else QUANTILES_LE)
        cuts = [cut for _, cut in grid]
        for a in anchors.get(c, []):
            if round(float(a), 3) not in cuts:
                cuts.append(round(float(a), 3))
        idxs = []
        for cut in cuts:
            idxs.append(len(legs))
            legs.append((c, op, cut))
        bycol.append(idxs)
        sat[c] = saturation_stats(v) + (len(cuts),)

    M = np.array([fire_mask(df[c], op, cut) for c, op, cut in legs])
    Y = movers
    min_tp = int(math.ceil(args.min_fires * base))
    floors = sorted(set(SENS_FLOORS + [args.min_fires]))
    dfs_floor = min(floors)
    dfs_min_tp = int(math.ceil(dfs_floor * base))

    import time
    t0 = time.time()
    prec_top, f05_top, best_depth, n_eval = run_search(
        M, Y, bycol, args.max_legs, dfs_floor, dfs_min_tp, floors)
    elapsed = time.time() - t0

    # ---- finalists --------------------------------------------------------
    main_top = finalize(prec_top[args.min_fires].items(), M, movers, base,
                        total_movers, args.jaccard, args.top,
                        want_floor=args.min_fires)
    f05_pay = [p for p in f05_top.items() if p[1] >= args.min_fires]
    recall_top = finalize(f05_pay, M, movers, base, total_movers,
                          args.jaccard, args.top, want_floor=args.min_fires)
    recall_top.sort(key=lambda k: (k[2]["f05"], k[2]["n"]), reverse=True)

    dates = df[DATE_COL].values if DATE_COL in df.columns else None
    day_list = sorted(set(dates)) if dates is not None else []

    # ---- report -----------------------------------------------------------
    lines = []
    P = lines.append
    thr = f"{args.threshold:g}"
    P(f"# Best AND thresholds: all sentiment scores vs `{TARGET} >= {thr}`")
    P(f"**Generated:** {date.today()}  |  **Input:** `{path.name}`")
    P("")

    P("## 1. Data & method")
    P("")
    P(f"- Rows with a `{TARGET}` value: **{len(df)}**  |  movers "
      f"(`{TARGET} >= {thr}`): **{total_movers}**  |  base rate **{base:.1%}**.")
    if day_list:
        P(f"- Window: `{day_list[0]}` -> `{day_list[-1]}` ({len(day_list)} "
          f"trading days).")
    P(f"- Leg columns: **{len(columns)}** auto-detected score columns "
      f"-> **{len(legs)}** distinct (column, cutoff) legs after collapsing "
      f"duplicate quantile cutoffs.")
    P(f"- Rules searched: **{n_eval:,}** (1..{args.max_legs} legs, one leg per "
      f"column) in {elapsed:.1f}s, pruning any prefix below {dfs_floor} fires "
      f"or {dfs_min_tp} TP.")
    P(f"- Metrics: **fires** (rows firing), **TP** (fires that are movers), "
      f"**precision** = TP/fires, **recall** = TP/{total_movers}, **lift** = "
      f"precision / {base:.1%}, Wilson 95% CI on precision, plus the median and "
      f"mean `{TARGET}` among fires.")
    P("")
    P("> **Read every precision below as an upper bound, not an estimate.** The "
      f"cutoffs were chosen on these same {len(df)} rows: {n_eval:,} candidate "
      f"rules were scored against {total_movers} movers, so the best few clear "
      "any bar partly by chance. The model *scores* are honest out-of-sample "
      "(all encoders are externally trained, and the kw15 model trained on "
      "pre-2026-06-20 data) — it is the threshold search that is fitted here. "
      "No null-model correction, holdout, or consistency gate is applied, per "
      "the requested configuration.")
    P("")

    # ---- 2. degenerate columns
    P("## 2. Saturated / degenerate columns")
    P("")
    P("Columns whose values pile up at one end carry little threshold "
      "information: their quantile grid collapses to a few distinct cutoffs, and "
      "the resulting legs fire on a large fraction of rows, so they ride along "
      "in rules without selecting anything. Rule minimization (below) strips "
      "them, but read any that survive with this table in mind.")
    P("")
    P("| column | uniq | q50 | q94 | max | % at max | cutoffs in grid |")
    P("|---|---|---|---|---|---|---|")
    for c, _ in columns:
        u, q50, q94, mx, fsat, ng = sat[c]
        flag = " **<-**" if (fsat > 0.35 or u < 20 or ng <= 2) else ""
        P(f"| `{c}`{flag} | {u} | {fmt_num(q50,3)} | {fmt_num(q94,3)} | "
          f"{fmt_num(mx,3)} | {fsat*100:.0f}% | {ng} |")
    P("")

    # ---- 3. single-column AUC
    P("## 3. Single-column AUC (orientation)")
    P("")
    P("AUC that each column, in its fire direction, ranks a mover above a "
      "non-mover. 0.50 = no signal. This is the bar any AND rule has to beat.")
    P("")
    P("| column | dir | AUC |")
    P("|---|---|---|")
    aucs = []
    for c, op in columns:
        s = pd.to_numeric(df[c], errors="coerce")
        a = auc_mannwhitney(s if op == ">=" else -s, movers)
        aucs.append((a, c, op))
    for a, c, op in sorted(aucs, reverse=True):
        P(f"| `{c}` | {op} | {fmt_num(a,3)} |")
    P("")

    # ---- 4. best rule per depth
    P("## 4. Best rule at each depth")
    P("")
    P(f"Precision-ranked best rule using exactly 1, 2, ... {args.max_legs} legs "
      f"(before minimization, floor {dfs_floor} fires). This is the direct "
      "answer to whether extra legs buy real precision or just shrink the fire "
      "count into noise.")
    P("")
    P("| legs | rule | fires | TP | precision | recall | lift | Wilson95 |")
    P("|---|---|---|---|---|---|---|---|")
    for d in range(1, args.max_legs + 1):
        p = best_depth.get(d)
        if not p:
            P(f"| {d} | _(none cleared the floor)_ | | | | | | |")
            continue
        idx, n, tp, prec = p
        m = cell(and_mask(M, idx), movers, base, total_movers)
        P(f"| {d} | {rule_str([legs[i] for i in idx])} | {m['n']} | {m['tp']} | "
          f"{fmt_pct(m['prec'])} | {fmt_pct(m['recall'])} | {fmt_lift(m['lift'])} "
          f"| [{fmt_pct(m['lo'])}, {fmt_pct(m['hi'])}] |")
    P("")

    # ---- 5. top rules
    def rule_table(kept, header):
        P(header)
        P("")
        P("| # | rule | fires | TP | precision | recall | lift | Wilson95 | "
          f"median {TARGET} | mean | clones |")
        P("|---|---|---|---|---|---|---|---|---|---|---|")
        for i, (idx, mask, m, nclone) in enumerate(kept, 1):
            vals = t.values[mask]
            P(f"| {i} | {rule_str([legs[j] for j in idx])} | {m['n']} | {m['tp']} "
              f"| {fmt_pct(m['prec'])} | {fmt_pct(m['recall'])} | "
              f"{fmt_lift(m['lift'])} | [{fmt_pct(m['lo'])}, {fmt_pct(m['hi'])}] "
              f"| {fmt_num(np.median(vals),1)} | {fmt_num(np.mean(vals),1)} | "
              f"{nclone[0]} |")
        if not kept:
            P("| _(nothing cleared the floor)_ | | | | | | | | | | |")
        P("")

    rule_table(main_top, "## 5. Top AND rules (precision-ranked, minimized, de-cloned)")
    P("Legs whose removal does not change the fire set have been dropped, so "
      "each rule shown is the shortest expression of its own fire set. `clones` "
      "counts further searched rules whose fire set overlaps this one at "
      f"Jaccard >= {args.jaccard:g} — a high count means many apparently "
      "different rules are restatements of this one.")
    P("")

    rule_table(recall_top, "## 6. Top AND rules (F0.5-ranked — keeps coverage)")
    P("Same pool ranked by F0.5 (precision-leaning but recall-aware), for an "
      "operating point that still fires often enough to matter.")
    P("")

    # ---- 7. min-fires sensitivity
    P("## 7. `--min-fires` sensitivity")
    P("")
    P("Best rule once a rule is required to fire on at least N rows. Headline "
      "precision that collapses as N rises was resting on a handful of rows.")
    P("")
    P("| min-fires | rule | fires | TP | precision | lift | Wilson95 |")
    P("|---|---|---|---|---|---|---|")
    for f in floors:
        k = finalize(prec_top[f].items(), M, movers, base, total_movers,
                     args.jaccard, 1, want_floor=f)
        if not k:
            P(f"| {f} | _(none)_ | | | | | |")
            continue
        idx, mask, m, _ = k[0]
        P(f"| {f} | {rule_str([legs[j] for j in idx])} | {m['n']} | {m['tp']} | "
          f"{fmt_pct(m['prec'])} | {fmt_lift(m['lift'])} | "
          f"[{fmt_pct(m['lo'])}, {fmt_pct(m['hi'])}] |")
    P("")

    # ---- 8. per-day breakdown
    if day_list:
        P("## 8. Per-day fire breakdown (informational)")
        P("")
        P("Fires and TP per trading day for the top rules. **Not a filter and "
          "not part of the ranking** — shown so a rule that fires on a single "
          "day is visible as such.")
        P("")
        P("| # | rule | " + " | ".join(day_list) + " |")
        P("|---|---|" + "---|" * len(day_list))
        for i, (idx, mask, m, _) in enumerate(main_top[:10], 1):
            cells = []
            for d in day_list:
                dm = mask & (dates == d)
                cells.append(f"{int((dm & movers).sum())}/{int(dm.sum())}")
            P(f"| {i} | {rule_str_plain([legs[j] for j in idx])} | "
              + " | ".join(cells) + " |")
        P("")
        P("_Cells are TP/fires._")
        P("")

    # ---- 9. passthrough-leg audit
    P("## 9. Passthrough legs in the top rules")
    P("")
    P("Every distinct leg used by the top-{} rules, with what it does **on its "
      "own**. A leg that fires on most rows at ~1.0x lift carries no signal by "
      "itself: it survived minimization only because it clips a handful of rows, "
      "and the search kept whichever clipping happened to help. Legs flagged "
      "**<-** (standalone lift < 1.25x) are the ones to distrust first."
      .format(len(main_top)))
    P("")
    leg_use = {}
    for idx, _, _, _ in main_top:
        for j in idx:
            leg_use[j] = leg_use.get(j, 0) + 1
    P("| leg | used in | fires alone | % of rows | precision alone | lift alone |")
    P("|---|---|---|---|---|---|")
    for j, cnt in sorted(leg_use.items(), key=lambda kv: -kv[1]):
        m = cell(M[j], movers, base, total_movers)
        flag = " **<-**" if (not np.isnan(m["lift"]) and m["lift"] < 1.25) else ""
        P(f"| {rule_str([legs[j]])}{flag} | {cnt} | {m['n']} | "
          f"{m['n']/len(df)*100:.0f}% | {fmt_pct(m['prec'])} | "
          f"{fmt_lift(m['lift'])} |")
    P("")

    out = pathlib.Path(args.out) if args.out else (
        pathlib.Path(__file__).parent /
        f"{date.today()}_ANDrules_fromPRhigh{int(args.threshold)}_allSentiment.md")
    out.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[written] {out}")

    # ---- fired-row detail TSV --------------------------------------------
    detail_rows = []
    idcols = [c for c in ("Symbol", "ArrivalDate", "ArrivalTime", "Headline")
              if c in df.columns]
    for i, (idx, mask, m, _) in enumerate(main_top[:10], 1):
        sub = df.loc[mask, idcols].copy()
        sub.insert(0, "rule_rank", i)
        sub.insert(1, "rule", rule_str_plain([legs[j] for j in idx]))
        sub[TARGET] = t.values[mask]
        sub["is_mover"] = movers[mask]
        for j in idx:
            c = legs[j][0]
            sub[c] = pd.to_numeric(df[c], errors="coerce").values[mask]
        detail_rows.append(sub)
    if detail_rows:
        det = pathlib.Path(str(out).replace(".md", "_firedrows.tsv"))
        pd.concat(detail_rows, ignore_index=True).to_csv(det, sep="\t", index=False)
        print(f"[written] {det}")


if __name__ == "__main__":
    main()
