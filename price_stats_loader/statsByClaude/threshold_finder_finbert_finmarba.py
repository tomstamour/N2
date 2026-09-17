#!/usr/bin/env python3
"""Best AND/OR threshold rules over a set of score columns, for selecting big
movers (`fromPR_High(%) >= threshold`, default 30).

Columns and their fire direction are given with `--columns NAME>=` / `NAME<=`.
The default is the original scope — FinBERT `positive` plus the four FinMarBa
headline columns, all externally trained so every row is an honest test:

    positive                    fire HIGH  (>=)   FinBERT headline P(positive)
    FinMarBa_positive           fire HIGH  (>=)
    FinMarBa_sentiment_score    fire HIGH  (>=)   +/-1 scale (pos - neg)
    FinMarBa_negative           fire LOW   (<=)
    FinMarBa_ind_hd         fire LOW   (<=)

IMPORTANT — the columns are scored on the SAME rows that select the winning
rule. With a few hundred candidate rules surviving the "Wilson-low > base"
screen, several will clear it by chance, so a top rule's precision is an UPPER
BOUND, not an estimate. The block-consistency guard is the only defence here and
it is weak (it asks for lift >= 1x in both halves, not replication). To turn the
number into an estimate, re-run the search on shuffled labels and subtract what
the best rule scores on noise.

What it does:
  1. Single-column frontier  - sweep a per-column quantile cutoff grid; report
     fires, TP, precision%, recall%, lift-vs-base, Wilson 95% CI at each cutoff.
     Recommend a knee (max F0.5 among cutoffs firing on >=20 rows).
  2. AND combinations        - cross-product of a few candidate cutoffs per
     column, all pairs + triples; precision-ranked.
  3. OR  combinations        - same legs, unioned; recall-ranked.
  4. Block-consistency guard - every recommended rule is re-checked on the early
     (< split-date) and recent (>= split-date) blocks; a rule whose lift drops
     below 1x in either block is flagged FAIL (window artifact) and not
     recommended.

Full corpus is used for statistical power; the block split (default 2026-06-20)
only gates the recommendation. Routines are self-contained (numpy/pandas/scipy);
nothing is imported from the sibling scripts, matching this directory's
copy-not-import convention.

Run (defaults point at the canonical TSV, threshold 30):
    /home/tom/venv/bin/python threshold_finder_finbert_finmarba.py
"""
import argparse
import itertools
import pathlib
import sys
from datetime import date

import numpy as np
import pandas as pd

DEFAULT_TSV = (
    "../outputs/"
    "03_PRstats_2026-05-04-2026-07-16_CoffeeFilter_11EncScored_Classed_kwScored_BodyEnc.tsv"
)
TARGET = "fromPR_High(%)"
DATE_COL = "ArrivalDate"
SPLIT_DATE = "2026-06-20"

# (column, operator) - operator is the fire direction. Overridable with
# --columns; these remain the default so existing invocations are unchanged.
COLUMNS = [
    ("Prosus_pos_hd", ">="),
    ("FinMarBa_pos_hd", ">="),
    ("FinMarBa_ss_hd", ">="),
    ("FinMarBa_neg_hd", "<="),
    ("FinMarBa_ind_hd", "<="),
]
# Quantiles used to build each column's frontier grid. For ">=" a higher
# quantile is a stricter cutoff (fewer fires); for "<=" a lower quantile is
# stricter. We list them loose -> strict in each case.
GRID_GE = [0.30, 0.40, 0.50, 0.60, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
GRID_LE = [0.70, 0.60, 0.50, 0.40, 0.30, 0.25, 0.20, 0.15, 0.10, 0.05]
# Candidate cutoffs each column contributes to the AND/OR combo search, as
# quantiles. Spread down into the tails so the search can reach the
# high-precision corners (e.g. FinMarBa_ind_hd's far-left tail, which is the
# live-gate operating point).
CAND_GE = [0.50, 0.65, 0.78, 0.88, 0.94]
CAND_LE = [0.50, 0.30, 0.18, 0.10, 0.05]
# Interpretable round cutoffs added to the combo candidates alongside the
# quantiles, so the search is anchored to human operating points (e.g. the live
# gate's Prosus_pos_hd>=0.6 / indecisive<=0.1) and not just data percentiles.
ANCHORS = {
    "Prosus_pos_hd": [0.5, 0.6, 0.7, 0.8],
    "FinMarBa_pos_hd": [0.6, 0.7, 0.8],
    "FinMarBa_ss_hd": [0.3, 0.5, 0.65],
    "FinMarBa_neg_hd": [0.10, 0.15, 0.20],
    "FinMarBa_ind_hd": [0.08, 0.10, 0.12, 0.15],
}

MIN_FIRES = 20          # a cutoff must fire on this many rows to be "recommendable"
MIN_FIRES_COMBO = 12    # combos are sparser; slightly lower floor

# The live O5.2 trade gate, as actually deployed (Orchestrator5.2.py:132-134):
#   SENTIMENT_POSITIVE_MIN = 0.6, FMB_INDECISIVE_MAX = 0.13, COMBO_TRIGGER_MIN = 0.67
# Earlier versions of this script hardcoded indecisive <= 0.1 and combo >= 0.6,
# neither of which was ever the deployed value.
LIVE_GATE_DEFAULT = "Prosus_pos_hd>=0.6,FinMarBa_ind_hd<=0.13"
COMBO_THRESHOLD_DEFAULT = 0.67


# --------------------------------------------------------------------------- #
# CLI spec parsing
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


def parse_rule(spec):
    """'Prosus_pos_hd>=0.6,FinMarBa_ind_hd<=0.13' -> [(col, op, cut), ...]"""
    legs = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        for op in (">=", "<="):
            if op in part:
                name, cut = part.split(op, 1)
                try:
                    legs.append((name.strip(), op, float(cut)))
                except ValueError:
                    sys.exit(f"--live-gate: non-numeric cutoff in {part!r}")
                break
        else:
            sys.exit(f"--live-gate: {part!r} needs '>=' or '<='")
    return legs


# --------------------------------------------------------------------------- #
# stats helpers
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


def newcombe_diff(k1, n1, k2, n2, z=1.96):
    """Newcombe method-10 CI for p2 - p1 (precision(gate AND cond) - precision
    (gate)). Robust at small counts / extreme proportions. Contains 0 -> the
    upgrade is not demonstrated."""
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    p1, p2 = k1 / n1, k2 / n2
    lo = (p2 - p1) - z * np.sqrt(l2 * (1 - l2) / n2 + u1 * (1 - u1) / n1)
    hi = (p2 - p1) + z * np.sqrt(u2 * (1 - u2) / n2 + l1 * (1 - l1) / n1)
    return (lo, hi)


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


def block_split(fired, df, movers, blocks):
    """Precision/lift of a fire mask within each (label, mask, base) block."""
    out = []
    for label, m, base in blocks:
        fm = fired & m
        n = int(fm.sum())
        tp = int((fm & movers).sum())
        prec = tp / n if n else float("nan")
        lift = prec / base if (n and base) else float("nan")
        out.append((label, n, tp, prec, lift))
    return out


def consistency_verdict(fired, df, movers, date_blocks):
    """PASS if lift >= 1.0 in BOTH date blocks that have any fires; else FAIL."""
    lifts = []
    for _, m, base in date_blocks:
        fm = fired & m
        n = int(fm.sum())
        if n == 0:
            lifts.append(None)
            continue
        prec = int((fm & movers).sum()) / n
        lifts.append(prec / base if base else float("nan"))
    present = [l for l in lifts if l is not None and not np.isnan(l)]
    if len(present) < 2:
        return "N/A", lifts
    return ("PASS" if all(l >= 1.0 for l in present) else "FAIL"), lifts


# --------------------------------------------------------------------------- #
# grids
# --------------------------------------------------------------------------- #
def quantile_cutoffs(values, quantiles, decimals=3):
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


def rule_str(legs):
    return " AND ".join(f"{c} {op} {cut:g}" for c, op, cut in legs)


def rule_str_or(legs):
    return " OR ".join(f"{c} {op} {cut:g}" for c, op, cut in legs)


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tsv", nargs="?", default=DEFAULT_TSV)
    ap.add_argument("--threshold", type=float, default=30.0)
    ap.add_argument("--split-date", default=SPLIT_DATE)
    ap.add_argument("--out", default=None, help="report path (default dated .md)")
    ap.add_argument("--top", type=int, default=15, help="combos shown per section")
    ap.add_argument("--combo-col", default="model_proba_PLUS_kw_mult_nb",
                    help="live combo/keyword score column for section 7")
    ap.add_argument("--combo-threshold", type=float,
                    default=COMBO_THRESHOLD_DEFAULT,
                    help=f"live combo gate threshold (default "
                         f"{COMBO_THRESHOLD_DEFAULT}, the deployed "
                         f"COMBO_TRIGGER_MIN)")
    ap.add_argument("--columns", nargs="+", default=None, metavar="NAME>=|NAME<=",
                    help="score columns and their fire direction, e.g. "
                         "'Prosus_pos_hd>=' 'FinMarBa_ind_hd<='. Default: the "
                         "five FinBERT/FinMarBa headline columns.")
    ap.add_argument("--anchors", nargs="+", default=None, metavar="NAME=v1,v2",
                    help="interpretable cutoffs added to each column's combo "
                         "candidates alongside the quantile grid.")
    ap.add_argument("--sweep-pair", default=None, metavar="COL_A,COL_B",
                    help="pair for the focused 2D sweep (section 4). Default: "
                         "the first '>=' column x the first '<=' column.")
    ap.add_argument("--live-gate", default=LIVE_GATE_DEFAULT,
                    help=f"the deployed AND rule to benchmark against "
                         f"(default {LIVE_GATE_DEFAULT!r}). Pass '' to skip.")
    args = ap.parse_args()

    columns = parse_columns(args.columns) if args.columns else list(COLUMNS)
    anchors = parse_anchors(args.anchors) if args.anchors else dict(ANCHORS)
    live_legs = parse_rule(args.live_gate) if args.live_gate else []

    path = pathlib.Path(args.tsv)
    if not path.exists():
        sys.exit(f"input not found: {path}")
    df = pd.read_csv(path, sep="\t", dtype=str)

    missing = [c for c, _ in columns if c not in df.columns] + \
              ([TARGET] if TARGET not in df.columns else [])
    if missing:
        sys.exit(f"missing columns: {', '.join(missing)}")
    # A live-gate leg on a column absent from the file would silently misreport
    # the benchmark row, so drop the benchmark rather than fake it.
    if live_legs and any(c not in df.columns for c, _, _ in live_legs):
        absent = sorted({c for c, _, _ in live_legs if c not in df.columns})
        print(f"[warn] live-gate columns absent ({', '.join(absent)}); "
              f"benchmark row skipped", file=sys.stderr)
        live_legs = []

    # Sweep pair: explicit, else first ">=" x first "<=".
    if args.sweep_pair:
        sweep = [c.strip() for c in args.sweep_pair.split(",")]
        if len(sweep) != 2:
            sys.exit("--sweep-pair needs exactly two comma-separated columns")
        opmap = dict(columns)
        for c in sweep:
            if c not in opmap:
                sys.exit(f"--sweep-pair column {c!r} is not in --columns")
        sweep_a, sweep_b = sweep[0], sweep[1]
    else:
        # The sweep exists to examine the DEPLOYED pair, so prefer the live
        # gate's own two columns when both are in scope; else first >= x first <=.
        live_cols = [c for c, _, _ in live_legs]
        ge = [c for c, op in columns if op == ">="]
        le = [c for c, op in columns if op == "<="]
        live_ge = [c for c in live_cols if c in ge]
        live_le = [c for c in live_cols if c in le]
        sweep_a = live_ge[0] if live_ge else (ge[0] if ge else columns[0][0])
        sweep_b = live_le[0] if live_le else (
            le[0] if le else (columns[1][0] if len(columns) > 1 else None))
    op_a = dict(columns)[sweep_a]
    op_b = dict(columns)[sweep_b] if sweep_b else None

    t = pd.to_numeric(df[TARGET], errors="coerce")
    keep = t.notna()
    df, t = df[keep].reset_index(drop=True), t[keep].reset_index(drop=True)
    movers = (t.values >= args.threshold)
    total_movers = int(movers.sum())
    n_rows = len(df)
    base = movers.mean()

    d = df[DATE_COL].fillna("").astype(str) if DATE_COL in df.columns \
        else pd.Series([""] * n_rows)
    recent = (d.ne("") & d.ge(args.split_date)).values
    early = ~recent
    base_early = movers[early].mean() if early.any() else float("nan")
    base_recent = movers[recent].mean() if recent.any() else float("nan")
    date_blocks = [
        (f"early (<{args.split_date})", early, base_early),
        (f"recent (>={args.split_date})", recent, base_recent),
    ]

    lines = []
    P = lines.append
    col_title = ", ".join(f"`{c}`" for c, _ in columns)
    P(f"# Best thresholds: {col_title} vs `{TARGET} >= {args.threshold:g}`")
    P(f"**Generated:** {date.today().isoformat()}  |  **Input:** `{path.name}`")
    P("")
    P("## 1. Data & method")
    P("")
    P(f"- Rows with a `{TARGET}` value: **{n_rows}**  |  movers "
      f"(`{TARGET} >= {args.threshold:g}`): **{total_movers}**  |  base rate "
      f"**{base:.1%}**.")
    P(f"- Blocks (split at `{args.split_date}`): early n="
      f"{int(early.sum())} base {base_early:.1%}  |  recent n="
      f"{int(recent.sum())} base {base_recent:.1%}.")
    P("- Both FinBERT and FinMarBa are externally trained (never saw this "
      "target), so all rows are an honest test. The block split only gates the "
      "recommendation: a rule must keep lift >= 1x in BOTH blocks (else it is a "
      "window artifact).")
    P("- Metrics per cutoff: **fires** (rows firing), **TP** (fires that are "
      "movers), **precision** = TP/fires, **recall** = TP/all movers, **lift** = "
      "precision / base rate, with Wilson 95% CI on precision.")
    hi_cols = [c for c, op in columns if op == ">="]
    lo_cols = [c for c, op in columns if op == "<="]
    dirs = []
    if hi_cols:
        dirs.append(", ".join(f"`{c}`" for c in hi_cols) + " fire HIGH (`>=`)")
    if lo_cols:
        dirs.append(", ".join(f"`{c}`" for c in lo_cols) + " fire LOW (`<=`)")
    P("- Fire direction: " + "; ".join(dirs) + ".")
    P("")

    # ---- single-column AUC orientation ----
    P("## 2. Single-column AUC (orientation)")
    P("")
    P("AUC that each column, in its fire direction, ranks a mover above a "
      "non-mover. 0.50 = no signal.")
    P("")
    P("| column | dir | AUC |")
    P("|---|---|---|")
    for col, op in columns:
        s = pd.to_numeric(df[col], errors="coerce")
        a = auc_mannwhitney(s, movers)
        if op == "<=":
            a = 1 - a  # low-fires-high: invert so >0.5 means the low tail is enriched
        P(f"| `{col}` | {op} | {a:.3f} |")
    P("")

    # ---- single-column frontier ----
    P("## 3. Single-column frontier")
    P("")
    P("Two operating points are picked per column: the **knee** (max F0.5, a "
      "precision-leaning balance that keeps recall) and the **high-precision** "
      "cutoff (strictest cutoff whose Wilson-low still beats base, firing on "
      f">= {MIN_FIRES} rows).")
    P("")
    single_reco = {}  # col -> {"knee": (...), "hp": (...)}
    for col, op in columns:
        grid = GRID_GE if op == ">=" else GRID_LE
        cuts = quantile_cutoffs(df[col], grid)
        P(f"### `{col}`  (fire {op})")
        P("")
        P("| cutoff | fires | TP | precision | recall | lift | Wilson95 |")
        P("|---|---|---|---|---|---|---|")
        rows = []
        for q, c in cuts:
            fired = fire_mask(df[col], op, c)
            m = cell(fired, movers, base, total_movers)
            rows.append((c, m, fired))
            P(f"| {op} {c:g} | {m['n']} | {m['tp']} | {fmt_pct(m['prec'])} | "
              f"{fmt_pct(m['recall'])} | {fmt_lift(m['lift'])} | "
              f"[{fmt_pct(m['lo'])}, {fmt_pct(m['hi'])}] |")
        elig = [(c, m, f) for c, m, f in rows if m["n"] >= MIN_FIRES]
        picks = {}
        if elig:
            # knee: max F0.5
            c, m, fired = max(elig, key=lambda r: r[1]["f05"])
            v, _ = consistency_verdict(fired, df, movers, date_blocks)
            picks["knee"] = (op, c, m, v, block_split(fired, df, movers, date_blocks))
            # high-precision: strictest cutoff with wilson_lo > base; else max prec
            sig = [(c, m, f) for c, m, f in elig if m["lo"] > base]
            pool = sig if sig else elig
            c2, m2, f2 = max(pool, key=lambda r: (r[1]["prec"], r[1]["recall"]))
            v2, _ = consistency_verdict(f2, df, movers, date_blocks)
            picks["hp"] = (op, c2, m2, v2, block_split(f2, df, movers, date_blocks))
            single_reco[col] = picks
            for tag, (op_, cc, mm, vv, bl) in (("Knee", picks["knee"]),
                                               ("High-prec", picks["hp"])):
                P("")
                P(f"**{tag}:** `{col} {op_} {cc:g}` -> precision "
                  f"{fmt_pct(mm['prec'])}, recall {fmt_pct(mm['recall'])}, lift "
                  f"{fmt_lift(mm['lift'])} ({mm['tp']}/{mm['n']}). Block check: " +
                  "; ".join(f"{lab} {fmt_lift(lf)} ({tp}/{n})"
                            for (lab, n, tp, pr, lf) in bl) + f"  ->  **{vv}**")
        P("")

    # ---- combo candidate legs ----
    cand = {}
    for col, op in columns:
        qs = CAND_GE if op == ">=" else CAND_LE
        cuts = {round(c, 3) for _, c in quantile_cutoffs(df[col], qs)}
        cuts.update(anchors.get(col, []))
        cand[col] = [(op, c) for c in sorted(cuts)]

    def eval_rule(legs, mode):
        masks = [fire_mask(df[c], op, cut) for c, op, cut in legs]
        fired = masks[0].copy()
        for mk in masks[1:]:
            fired = (fired & mk) if mode == "AND" else (fired | mk)
        m = cell(fired, movers, base, total_movers)
        verdict, lifts = consistency_verdict(fired, df, movers, date_blocks)
        return fired, m, verdict, lifts

    def search(mode, keep_fn):
        """Enumerate pair+triple combos (r=2 before r=3 so simpler rules win the
        de-clone), de-clone by identical fire signature, return kept results."""
        seen = set()
        out = []
        for r in (2, 3):
            for combo in itertools.combinations(cols, r):
                grids = [[(c, op, cut) for (op, cut) in cand[c]] for c in combo]
                for legs in itertools.product(*grids):
                    fired, m, verdict, lifts = eval_rule(list(legs), mode)
                    if not keep_fn(m):
                        continue
                    sig = fired.tobytes()
                    if sig in seen:  # a simpler rule already covers this exact set
                        continue
                    seen.add(sig)
                    out.append((list(legs), m, verdict))
        return out

    # ---- AND combos ----
    P("## 4. AND combinations (precision-ranked)")
    P("")
    P("All column pairs & triples, over candidate cutoffs (per-column quantiles "
      "plus round anchor levels such as `positive` 0.5/0.6/0.7/0.8 and "
      "`indecisive` 0.08/0.10/0.12/0.15). De-cloned: a longer rule whose fire "
      "set is identical to a simpler one is dropped. Kept: fires >= "
      f"{MIN_FIRES_COMBO} and Wilson-low > base ({base:.1%}). "
      "`consist` = both-block lift>=1x guard.")
    P("")
    cols = [c for c, _ in columns]
    and_results = search("AND",
                         lambda m: m["n"] >= MIN_FIRES_COMBO and m["lo"] > base)
    and_results.sort(key=lambda x: (x[1]["prec"], x[1]["n"]), reverse=True)
    P("| rule | fires | TP | precision | recall | lift | Wilson95 | consist |")
    P("|---|---|---|---|---|---|---|---|")
    for legs, m, verdict in and_results[:args.top]:
        P(f"| {rule_str(legs)} | {m['n']} | {m['tp']} | {fmt_pct(m['prec'])} | "
          f"{fmt_pct(m['recall'])} | {fmt_lift(m['lift'])} | "
          f"[{fmt_pct(m['lo'])}, {fmt_pct(m['hi'])}] | {verdict} |")
    if not and_results:
        P("| _(no AND combo cleared the fires/precision floor)_ | | | | | | | |")
    P("")

    # focused 2D sweep over the chosen pair (default: the live-gate pair)
    if sweep_b:
        P(f"### Focused sweep: `{sweep_a}` ({op_a}) x `{sweep_b}` ({op_b})")
        P("")
        P(f"Cell = precision% / recall% (fires) for "
          f"`({sweep_a} {op_a} p AND {sweep_b} {op_b} q)`.")
        P("")

        def sweep_cuts(colname, op):
            """Anchors if given, else the column's own candidate quantiles."""
            if anchors.get(colname):
                return sorted(anchors[colname], reverse=(op == "<="))
            qs = CAND_GE if op == ">=" else CAND_LE
            return [c for _, c in quantile_cutoffs(df[colname], qs)]

        p_cuts = sweep_cuts(sweep_a, op_a)
        q_cuts = sweep_cuts(sweep_b, op_b)
        P(f"| {sweep_a} \\ {sweep_b} | " +
          " | ".join(f"{op_b} {q:g}" for q in q_cuts) + " |")
        P("|" + "---|" * (len(q_cuts) + 1))
        grid = {}
        for p in p_cuts:
            cells = []
            for q in q_cuts:
                fired, m, verdict, lifts = eval_rule(
                    [(sweep_a, op_a, p), (sweep_b, op_b, q)], "AND")
                grid[(p, q)] = m
                cells.append(f"{fmt_pct(m['prec'])} / {fmt_pct(m['recall'])} ({m['n']})")
            P(f"| {op_a} {p:g} | " + " | ".join(cells) + " |")
        P("")
        # Describe the surface from the numbers rather than asserting a shape.
        usable = {k: m for k, m in grid.items() if m["n"] >= MIN_FIRES_COMBO}
        if usable:
            bp = max(usable, key=lambda k: usable[k]["prec"])
            br = max(usable, key=lambda k: usable[k]["recall"])
            P(f"Highest precision at >= {MIN_FIRES_COMBO} fires: "
              f"`{sweep_a} {op_a} {bp[0]:g} AND {sweep_b} {op_b} {bp[1]:g}` -> "
              f"{fmt_pct(usable[bp]['prec'])} precision, "
              f"{fmt_pct(usable[bp]['recall'])} recall, "
              f"{usable[bp]['n']} fires ({fmt_lift(usable[bp]['lift'])}). "
              f"Widest coverage: `{sweep_a} {op_a} {br[0]:g} AND "
              f"{sweep_b} {op_b} {br[1]:g}` -> {fmt_pct(usable[br]['prec'])} / "
              f"{fmt_pct(usable[br]['recall'])} ({usable[br]['n']} fires).")
        else:
            P(f"_No cell in this sweep fires on >= {MIN_FIRES_COMBO} rows._")
        P("")

    # ---- OR combos ----
    P("## 5. OR combinations (recall-ranked)")
    P("")
    P("Same candidate legs, unioned. OR trades precision for coverage; kept: "
      f"precision >= base ({base:.1%}). Ranked by recall.")
    P("")
    or_results = search("OR",
                        lambda m: not np.isnan(m["prec"]) and m["prec"] >= base)
    or_results.sort(key=lambda x: (x[1]["recall"], x[1]["prec"]), reverse=True)
    P("| rule | fires | TP | precision | recall | lift | consist |")
    P("|---|---|---|---|---|---|---|")
    for legs, m, verdict in or_results[:args.top]:
        P(f"| {rule_str_or(legs)} | {m['n']} | {m['tp']} | {fmt_pct(m['prec'])} | "
          f"{fmt_pct(m['recall'])} | {fmt_lift(m['lift'])} | {verdict} |")
    if not or_results:
        P("| _(no OR combo held precision at/above base)_ | | | | | | |")
    P("")

    # ---- recommendations ----
    P("## 6. Recommended rules")
    P("")
    P("Only rules passing the both-block consistency guard are listed. "
      "Precision/recall are full-corpus.")
    P("")
    P("### Best single-column cutoffs")
    P("")
    P("Knee = balanced (max F0.5); High-prec = strictest cutoff still beating "
      "base. Both must pass the both-block guard.")
    P("")
    P("| column | knee rule | knee P / R | high-prec rule | high-prec P / R |")
    P("|---|---|---|---|---|")
    for col, op in columns:
        if col in single_reco:
            _, ck, mk, vk, _ = single_reco[col]["knee"]
            _, ch, mh, vh, _ = single_reco[col]["hp"]
            P(f"| `{col}` | {op} {ck:g} ({vk}) | {fmt_pct(mk['prec'])} / "
              f"{fmt_pct(mk['recall'])} | {op} {ch:g} ({vh}) | "
              f"{fmt_pct(mh['prec'])} / {fmt_pct(mh['recall'])} |")
    P("")
    and_pass = [r for r in and_results if r[2] == "PASS"]
    best_and = and_pass[0] if and_pass else None  # highest precision (list is sorted)
    bal_and = max(and_pass, key=lambda r: r[1]["f05"], default=None)
    best_or = next((r for r in or_results if r[2] == "PASS"
                    and r[1]["lift"] >= 1.3), None)
    P("### Best AND rules")
    P("")
    P("_Purity_ = highest precision that passes the guard (few fires, size-up "
      "conviction signal). _Balanced_ = best F0.5 (more fires, still precise).")
    P("")
    if best_and:
        legs, m, _ = best_and
        P(f"- **Purity:** `{rule_str(legs)}` -> precision **{fmt_pct(m['prec'])}**, "
          f"recall **{fmt_pct(m['recall'])}**, lift **{fmt_lift(m['lift'])}** "
          f"({m['tp']}/{m['n']}).")
    if bal_and and (not best_and or bal_and[0] != best_and[0]):
        legs, m, _ = bal_and
        P(f"- **Balanced:** `{rule_str(legs)}` -> precision "
          f"**{fmt_pct(m['prec'])}**, recall **{fmt_pct(m['recall'])}**, lift "
          f"**{fmt_lift(m['lift'])}** ({m['tp']}/{m['n']}).")
    if not best_and:
        P("- _none cleared the guard._")
    P("")
    P("### Best OR rule (coverage)")
    if best_or:
        legs, m, _ = best_or
        P(f"- `{rule_str_or(legs)}` -> precision **{fmt_pct(m['prec'])}**, recall "
          f"**{fmt_pct(m['recall'])}**, lift **{fmt_lift(m['lift'])}** "
          f"({m['tp']}/{m['n']}), consistency PASS.")
    else:
        P("- _none held both precision and a stable >=1.3x lift._")
    P("")
    P("_Caveat: `fromPR_High(%)` is peak excursion, not capturable P&L; "
      "precision here is 'fraction of fires that touched +30%', an upper bound "
      "on tradeable hit-rate._")
    P("")

    # ---------------------------------------------------------------- #
    # Section 7 - fold in the live combo>=0.6 leg (HONEST BLOCK ONLY).
    # The combo/keyword score trained on the early block, so only the recent
    # block is an honest test for it (FinBERT/FinMarBa stay honest everywhere,
    # but the comparison must share one eval set -> use the honest block).
    # ---------------------------------------------------------------- #
    ccol, cthr = args.combo_col, args.combo_threshold
    P(f"## 7. Folding in the live `{ccol} >= {cthr:g}` leg (honest block only)")
    P("")
    if ccol not in df.columns:
        P(f"_Column `{ccol}` not in input; section skipped._")
        P("")
    else:
        hb = recent
        mv_hb = movers[hb]
        n_hb, tot_mv_hb = int(hb.sum()), int(mv_hb.sum())
        base_hb = base_recent
        cvals = pd.to_numeric(df[ccol], errors="coerce")
        combo_fire = ((cvals >= cthr) & cvals.notna()).values
        P(f"The `{ccol}` keyword model trained on the early (<{args.split_date}) "
          f"block, so only the recent block is an honest test for it. All of "
          f"section 7 is on that block: **n={n_hb}, movers={tot_mv_hb}, base "
          f"{base_hb:.1%}**.")
        P("")

        def hb_cell(mask):
            fm = mask & hb
            n = int(fm.sum())
            tp = int((fm & movers).sum())
            prec = tp / n if n else float("nan")
            rec = tp / tot_mv_hb if tot_mv_hb else float("nan")
            lift = prec / base_hb if (n and base_hb) else float("nan")
            return n, tp, prec, rec, lift

        cn, ctp, cprec, crec, clift = hb_cell(combo_fire)
        P(f"**Baseline — `{ccol} >= {cthr:g}` alone:** fires **{cn}**, TP "
          f"**{ctp}**, precision **{fmt_pct(cprec)}**, recall {fmt_pct(crec)}, "
          f"lift **{fmt_lift(clift)}**.")
        P("")

        # Curated sentiment rules, drawn from the sections above.
        test_rules = []
        if live_legs:
            test_rules.append(
                ("live sentiment leg: " + " AND ".join(
                    f"{c}{op}{cut:g}" for c, op, cut in live_legs), live_legs))
        for col in [c for c, _ in columns]:
            if col in single_reco:
                op_, cc, _, _, _ = single_reco[col]["knee"]
                test_rules.append((f"{col} {op_} {cc:g} (knee)",
                                   [(col, op_, cc)]))
                op2, cc2, _, _, _ = single_reco[col]["hp"]
                if cc2 != cc:
                    test_rules.append((f"{col} {op2} {cc2:g} (high-prec)",
                                       [(col, op2, cc2)]))
        if best_and:
            test_rules.append((f"AND-purity: {rule_str(best_and[0])}",
                               best_and[0]))
        if bal_and and (not best_and or bal_and[0] != best_and[0]):
            test_rules.append((f"AND-balanced: {rule_str(bal_and[0])}",
                               bal_and[0]))

        P("For each sentiment rule S, three deployment views:")
        P("")
        P("- **OR union** `combo OR S` — the combined trigger.")
        P("- **marginal** = fires where **combo misses but S fires** — the movers "
          "S would *add*, and the precision of those added fires. This is the "
          "real 'does S earn an OR slot' test: marginal lift <= 1x means S only "
          "adds noise on top of combo.")
        P("- **AND upgrade** `combo AND S` — S as a conviction filter on combo's "
          "own fires; delta = precision(combo AND S) − precision(combo), with a "
          "Newcombe 95% CI (contains 0 -> not demonstrated).")
        P("")
        P("| sentiment rule S | S alone (P/R) | combo OR S (P/R) | marginal adds (TP/fires, P, lift) | combo AND S (P/R) | AND delta [95% CI] |")
        P("|---|---|---|---|---|---|")
        s7 = []
        for label, legs in test_rules:
            masks = [fire_mask(df[c], op, cut) for c, op, cut in legs]
            rule_fire = masks[0].copy()
            for mk in masks[1:]:
                rule_fire = rule_fire & mk
            sn, stp, sprec, srec, slift = hb_cell(rule_fire)
            on, otp, oprec, orec, olift = hb_cell(combo_fire | rule_fire)
            mn, mtp, mprec, mrec, mlift = hb_cell((~combo_fire) & rule_fire)
            an, atp, aprec, arec, alift = hb_cell(combo_fire & rule_fire)
            if an > 0 and cn > 0:
                lo, hi = newcombe_diff(ctp, cn, atp, an)
                verdict = "helps" if lo > 0 else ("hurts" if hi < 0 else "n.s.")
                delta = f"{aprec - cprec:+.0%} [{lo*100:+.0f}, {hi*100:+.0f}] {verdict}"
            else:
                verdict, delta = "no fires", "no fires"
            s7.append({"Prosus_lab_hd": label, "sprec": sprec, "mlift": mlift, "mn": mn,
                       "aprec": aprec, "an": an, "verdict": verdict})
            P(f"| {label} | {fmt_pct(sprec)}/{fmt_pct(srec)} | "
              f"{fmt_pct(oprec)}/{fmt_pct(orec)} | {mtp}/{mn}, {fmt_pct(mprec)}, "
              f"{fmt_lift(mlift)} | {fmt_pct(aprec)}/{fmt_pct(arec)} | {delta} |")
        P("")
        # Verdict is DERIVED from the table above, never asserted: with a
        # user-supplied column set the old hardcoded conclusions could be
        # flatly false for the data in front of us.
        P("### Deployment verdict")
        P("")
        beats = [r for r in s7 if not np.isnan(r["sprec"]) and r["sprec"] > cprec]
        P(f"- `{ccol} >= {cthr:g}` alone: {fmt_pct(cprec)} precision, "
          f"{fmt_lift(clift)}, {cn} fires. "
          + (f"**{len(beats)} of {len(s7)}** sentiment rules beat it standalone ("
             + ", ".join(f"`{r['Prosus_lab_hd']}` {fmt_pct(r['sprec'])}" for r in beats[:3])
             + ")." if beats else
             f"None of the {len(s7)} sentiment rules beat it standalone."))
        earners = [r for r in s7 if r["mn"] >= MIN_FIRES_COMBO
                   and not np.isnan(r["mlift"]) and r["mlift"] > 1.0]
        P("- **As an extra OR leg:** " + (
            f"{len(earners)} rule(s) add combo-missed fires at lift > 1x — "
            + ", ".join(f"`{r['Prosus_lab_hd']}` ({r['mn']} adds, {fmt_lift(r['mlift'])})"
                        for r in earners[:3]) + "."
            if earners else
            f"none of the {len(s7)} rules add combo-missed fires above base rate "
            f"at >= {MIN_FIRES_COMBO} fires, i.e. they re-select combo's own "
            f"winners or add fires with no edge."))
        helps = [r for r in s7 if r["verdict"] == "helps"]
        hurts = [r for r in s7 if r["verdict"] == "hurts"]
        P("- **As an AND conviction filter:** " + (
            f"{len(helps)} rule(s) raise combo's precision with a Newcombe CI "
            f"excluding 0 — " + ", ".join(f"`{r['Prosus_lab_hd']}` -> {fmt_pct(r['aprec'])} "
                                          f"on {r['an']} fires" for r in helps[:3]) + "."
            if helps else
            f"no rule's precision gain over combo alone clears its Newcombe CI; "
            f"{sum(1 for r in s7 if r['verdict'] == 'n.s.')} are n.s."
            + (f", {len(hurts)} actively hurt" if hurts else "")
            + ". Treat any apparent gain as a size-up signal, not a proven jump."))
        P(f"- Sample size caveat: this block is n={n_hb} with {tot_mv_hb} movers, "
          f"so CIs are wide and absence of significance is not evidence of "
          f"absence.")
        P("")

    report = "\n".join(lines) + "\n"
    out = pathlib.Path(args.out) if args.out else pathlib.Path(
        f"{date.today().isoformat()}_"
        f"{'_'.join(c for c, _ in columns)[:60]}_thresholds.md")
    out.write_text(report)

    # concise stdout
    print(f"rows={n_rows} movers={total_movers} base={base:.1%} "
          f"(early {base_early:.1%} / recent {base_recent:.1%})")
    print("single-column (knee | high-prec):")
    for col, op in columns:
        if col in single_reco:
            _, ck, mk, vk, _ = single_reco[col]["knee"]
            _, ch, mh, vh, _ = single_reco[col]["hp"]
            print(f"  {col:26s} knee {op} {ck:<6g} P{fmt_pct(mk['prec']):>4s} "
                  f"R{fmt_pct(mk['recall']):>4s} [{vk}] | hp {op} {ch:<6g} "
                  f"P{fmt_pct(mh['prec']):>4s} R{fmt_pct(mh['recall']):>4s} [{vh}]")
    if best_and:
        legs, m, _ = best_and
        print(f"best AND: {rule_str(legs)}  prec {fmt_pct(m['prec'])} "
              f"recall {fmt_pct(m['recall'])} lift {fmt_lift(m['lift'])}")
    if best_or:
        legs, m, _ = best_or
        print(f"best OR : {rule_str_or(legs)}  prec {fmt_pct(m['prec'])} "
              f"recall {fmt_pct(m['recall'])} lift {fmt_lift(m['lift'])}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
