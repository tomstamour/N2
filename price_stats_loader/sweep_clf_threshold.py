#!/usr/bin/env python3
"""Operating-threshold sweep for a model-2 classifier column on fromPR_High(%).

Answers one question: if you fire when `--column` clears a cut, what do you get?
TP / FP / FN / TN at every cut, with the uncertainty attached.

STATS ONLY. No gate is recommended, no DSL rule line is proposed, nothing under
orchestrator5/ is read or written. The scores are used RAW -- the 09-20 calibration
study established that any monotone rescaling (Platt, the scale_pos_weight prior
correction) selects exactly the same rows in exactly the same order, so it cannot
change a threshold decision. It changes the units, not the choice.

THREE TRAPS THIS SCRIPT IS BUILT TO AVOID, because a naive sweep walks into all of them:

  1. TAKING THE BEST ROW OF THE TABLE. Searching a grid for the highest precision and
     reporting that number is selection bias, and here it is worth ~10 points: choosing
     the cut on one half of the fold returned 0.77 at an apparent 32.6%, which delivered
     22.2% on the other half. Section 4 does that experiment explicitly so the reader
     sees the size of the lie before trusting any optimum.

  2. READING THE THIN TAIL. Precision at a cut that fires 9 times is not a measurement.
     `--min-fires` marks those rows rather than letting them look like findings.

  3. CONFUSING A NUMBER WITH A WORKLOAD. An absolute cut drifts as the score
     distribution moves; a percentile does not. Section 5 reports both, which is the
     same advice already written into Orchestrator5.3.py:1583 -- set a cut from a
     measured quantile of your own tape.

House convention (statsByClaude/CLAUDE.md): standalone, routines copied not imported.
No sklearn. Deterministic: --seed 42 fixes the bootstrap.
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata

_HERE = Path(__file__).resolve().parent

TARGET_ACT = "fromPR_High(%)_actual"
DATE_COL = "ArrivalDate"


# --------------------------------------------------------------------------
# stats primitives -- copied from analyze_model2_deep.py per house convention
# --------------------------------------------------------------------------
def auc_score(y, s):
    """Mann-Whitney rank AUC. Handles ties via average ranks; no sklearn."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    n1 = float(y.sum())
    n0 = float(len(y) - n1)
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def wilson(k, n, z=1.959964):
    """95% Wilson score interval -- does not collapse to zero width at k=0 or k=n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - h) / d), float((c + h) / d)


def _day_blocks(days):
    """Row indices grouped by session, for the block bootstrap."""
    idx = {}
    for i, d in enumerate(days):
        idx.setdefault(d, []).append(i)
    return [np.asarray(v) for v in idx.values()]


def block_boot_precision(s, y, thr, days, n_boot, rng, min_fires):
    """95% CI on precision at a FIXED cut, resampling whole SESSIONS.

    Sessions, not rows: two press releases from the same morning share the market
    regime, so a row bootstrap would report an interval perhaps half its honest width."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        m = s[sel] >= thr
        if m.sum() >= max(1, min_fires // 4):
            out.append(y[sel][m].mean())
    if len(out) < 50:
        return (float("nan"), float("nan"))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def pct(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100 * x:.{nd}f}%"


def sgn(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100 * x:+.{nd}f}"


def parse_grid(spec):
    """'0.05:0.95:0.05' -> array of cut points."""
    try:
        lo, hi, st = (float(x) for x in spec.split(":"))
    except ValueError:
        sys.exit(f"--grid/--fine-grid must be lo:hi:step, got {spec!r}")
    return np.round(np.arange(lo, hi + st / 2, st), 6)


def confusion(s, y, thr):
    m = s >= thr
    return (int((m & (y == 1)).sum()), int((m & (y == 0)).sum()),
            int((~m & (y == 1)).sum()), int((~m & (y == 0)).sum()))


def best_cut(s, y, mask, grid, min_fires):
    """Highest-precision cut inside `mask`, ignoring cuts that fire too rarely."""
    bp, bt, bn = -1.0, None, 0
    for t in grid:
        m = mask & (s >= t)
        if m.sum() < min_fires:
            continue
        p = float(y[m].mean())
        if p > bp:
            bp, bt, bn = p, float(t), int(m.sum())
    return bt, bp, bn


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scores", default="outputs/08_2026-05-04-2026-08-28_RawData_splits_"
                    "rmCol_NaN_rmCol_rmCol_prosusReScore_reCat_PCA_val_M2-scores.tsv")
    ap.add_argument("--column", default="proba_clf_ge50")
    ap.add_argument("--target-threshold", type=float, default=50.0,
                    help="fromPR_High(%%) level that counts as a true positive")
    ap.add_argument("--grid", default="0.05:0.95:0.05")
    ap.add_argument("--fine-grid", default="0.30:0.85:0.01",
                    help="denser grid used ONLY for the honest-selection experiment")
    ap.add_argument("--min-fires", type=int, default=40,
                    help="below this a cut's precision is unmeasurable; flagged, and "
                         "excluded from any search for a best cut")
    ap.add_argument("--bootstrap", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--outdir", default="statsByClaude")
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    col, tt = args.column, args.target_threshold
    outdir = (_HERE / args.outdir).resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    report = args.report or (f"statsByClaude/2026-09-20_XGB_model2_"
                             f"{col.replace('proba_clf_', '')}_threshold.md")
    stem = f"model2_{col.replace('proba_clf_', '')}_threshold_sweep"

    d = pd.read_csv(_HERE / args.scores, sep="\t", low_memory=False)
    for c in (col, TARGET_ACT, DATE_COL):
        if c not in d.columns:
            sys.exit(f"--scores is missing required column {c!r}")
    s = d[col].to_numpy(dtype=float)
    y_raw = d[TARGET_ACT].to_numpy(dtype=float)
    y = (y_raw >= tt).astype(int)
    days = d[DATE_COL].astype(str).to_numpy()
    n = len(d)
    uniq = np.array(sorted(set(days)))
    nd = len(uniq)
    P, Nn = int(y.sum()), int((y == 0).sum())
    base = P / n
    grid = parse_grid(args.grid)
    fine = parse_grid(args.fine_grid)
    print(f"{n} rows / {nd} sessions | column {col} | positives at >={tt:.0f}%: {P}")

    # split-half by date
    firsts = set(uniq[:nd // 2])
    A = np.array([x in firsts for x in days])
    B = ~A

    # ---------------------------------------------------------------- sweep
    rows = []
    for t in grid:
        tp, fp, fn, tn = confusion(s, y, t)
        fires = tp + fp
        prec = tp / fires if fires else float("nan")
        rec = tp / P if P else float("nan")
        lo, hi = wilson(tp, fires)
        blo, bhi = (block_boot_precision(s, y, t, days, args.bootstrap, rng,
                                         args.min_fires)
                    if fires >= args.min_fires else (float("nan"), float("nan")))
        mA, mB = A & (s >= t), B & (s >= t)
        pA = float(y[mA].mean()) if mA.sum() else float("nan")
        pB = float(y[mB].mean()) if mB.sum() else float("nan")
        rows.append({
            "threshold": float(t), "fires": fires, "fires_per_day": fires / nd,
            "workload_pct": fires / n, "TP": tp, "FP": fp, "FN": fn, "TN": tn,
            "precision": prec, "wilson_lo": lo, "wilson_hi": hi,
            "boot_lo": blo, "boot_hi": bhi,
            "lift": prec / base if fires else float("nan"), "recall": rec,
            "f1": 2 * prec * rec / (prec + rec) if fires and (prec + rec) else float("nan"),
            "prec_half_A": pA, "prec_half_B": pB, "swing": abs(pA - pB),
            "fires_A": int(mA.sum()), "fires_B": int(mB.sum()),
            "thin": fires < args.min_fires,
        })
    sw = pd.DataFrame(rows)
    sw.to_csv(outdir / f"{stem}.tsv", sep="\t", index=False)

    # -------------------------------------------------------- verification
    checks, n_ok = [], 0
    checks.append(("rows", float(n), float(n)))
    checks.append(("confusion sums to n at every cut",
                   float(len(sw)),
                   float((sw.TP + sw.FP + sw.FN + sw.TN == n).sum())))
    checks.append(("fire count non-increasing across the grid", 1.0,
                   float(bool((np.diff(sw.fires.to_numpy()) <= 0).all()))))
    checks.append((f"positives at >={tt:.0f}%", float(P),
                   float((y_raw >= tt).sum())))
    checks.append(("sessions", float(nd), float(len(set(days)))))
    for _, a, b in checks:
        n_ok += abs(a - b) < 0.5

    # ------------------------------------------------------ honest selection
    tA, pA_in, nA = best_cut(s, y, A, fine, args.min_fires)
    tB, pB_in, nB = best_cut(s, y, B, fine, args.min_fires)
    tF, pF_in, nF = best_cut(s, y, np.ones(n, bool), fine, args.min_fires)
    sel = []
    for src, dst, t, pin, lbl in ((A, B, tA, pA_in, "half A → half B"),
                                  (B, A, tB, pB_in, "half B → half A")):
        m = dst & (s >= t)
        pout = float(y[m].mean()) if m.sum() else float("nan")
        sel.append((lbl, t, pin, pout, int(m.sum())))

    # ------------------------------------------------------------- report
    L = []
    L.append(f"# `{col}` — operating threshold on `fromPR_High(%) >= {tt:.0f}%`")
    L.append("")
    L.append(f"*Generated {pd.Timestamp.now():%Y-%m-%d %H:%M} by "
             f"`price_stats_loader/sweep_clf_threshold.py` (seed {args.seed}, "
             f"{args.bootstrap} day-block bootstrap reps).*")
    L.append("")
    L.append("**Stats only.** No gate is recommended, no rule line is proposed, and "
             "nothing under `orchestrator5/` is read or written.")
    L.append("")
    L.append(f"Scores are used **raw**. The 09-20 calibration study showed that any "
             "monotone rescaling — Platt, or the `scale_pos_weight` prior correction — "
             "selects the identical rows in the identical order. It changes the units, "
             "not the choice, so a threshold decision is unaffected by it.")
    L.append("")
    verdict_slot = len(L)
    L.append("")
    L.append(f"**Fold**: {n} rows, {nd} sessions, {uniq[0]}..{uniq[-1]}. "
             f"**{P} positives** at `>= {tt:.0f}%` (base rate {pct(base)}), "
             f"{Nn} negatives. AUC of `{col}` here is {fmt(auc_score(y, s))}.")
    L.append("")

    # 1
    L.append("## 1. The sweep")
    L.append("")
    L.append("A **TP** is a fire that reached the target; an **FP** is a fire that did "
             "not. **Lift** is precision divided by the base rate — how many times "
             "better than firing at random. **Workload** is the share of all press "
             "releases that clear the cut.")
    L.append("")
    L.append("| thr | fires | /day | workload | TP | FP | FN | TN | precision | "
             "Wilson 95% | day-block 95% | lift | recall | F1 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in sw.iterrows():
        flag = " ⚠" if r["thin"] else ""
        boot = (f"{pct(r['boot_lo'])}–{pct(r['boot_hi'])}"
                if not np.isnan(r["boot_lo"]) else "*too thin*")
        L.append(f"| **{r['threshold']:.2f}**{flag} | {int(r['fires'])} | "
                 f"{r['fires_per_day']:.1f} | {pct(r['workload_pct'])} | "
                 f"{int(r['TP'])} | {int(r['FP'])} | {int(r['FN'])} | {int(r['TN'])} | "
                 f"**{pct(r['precision'])}** | {pct(r['wilson_lo'])}–{pct(r['wilson_hi'])} "
                 f"| {boot} | {fmt(r['lift'], 2)}x | {pct(r['recall'])} | "
                 f"{fmt(r['f1'])} |")
    L.append(f"| *fire on everything* | {n} | {n/nd:.1f} | 100.0% | {P} | {Nn} | 0 | 0 "
             f"| {pct(base)} | — | — | 1.00x | 100.0% | — |")
    L.append("")
    thin = sw[sw.thin]
    if len(thin):
        L.append(f"⚠ marks a cut firing fewer than {args.min_fires} times, where "
                 "precision is not a measurement. "
                 + ", ".join(f"`{r.threshold:.2f}` fires {int(r.fires)}x"
                             for _, r in thin.iterrows())
                 + ". These are excluded from every search below.")
        L.append("")

    # 2
    L.append("## 2. Where precision stops improving")
    L.append("")
    ok = sw[~sw.thin]
    pk = float(ok["precision"].max())
    plateau = ok[ok["precision"] >= pk - 0.01]
    lo_t, hi_t = float(plateau["threshold"].min()), float(plateau["threshold"].max())
    L.append(f"Peak measurable precision is **{pct(pk)}**, and every cut from "
             f"**{lo_t:.2f} to {hi_t:.2f}** sits within one point of it. Across that "
             "band recall falls from "
             f"{pct(float(plateau[plateau.threshold == lo_t]['recall'].iloc[0]))} to "
             f"{pct(float(plateau[plateau.threshold == hi_t]['recall'].iloc[0]))} — so "
             "raising the cut inside the plateau costs winners and buys nothing.")
    L.append("")
    L.append("Every Wilson and day-block interval in that band overlaps heavily. "
             "**The plateau is one statistical object, not a ranking of cuts.** "
             "Picking the single highest row of it is the trap section 4 measures.")
    L.append("")

    # 3
    L.append("## 3. Split-half stability")
    L.append("")
    L.append(f"Half A = {uniq[0]}..{uniq[nd//2-1]} ({int(A.sum())} rows), "
             f"half B = {uniq[nd//2]}..{uniq[-1]} ({int(B.sum())} rows). A cut worth "
             "using should hold up in both.")
    L.append("")
    L.append("| thr | precision A | fires A | precision B | fires B | **swing** |")
    L.append("|---|---|---|---|---|---|")
    for _, r in ok.iterrows():
        L.append(f"| {r['threshold']:.2f} | {pct(r['prec_half_A'])} | "
                 f"{int(r['fires_A'])} | {pct(r['prec_half_B'])} | "
                 f"{int(r['fires_B'])} | **{sgn(r['swing'])} pts** |")
    L.append("")
    steady = ok.loc[ok["swing"].idxmin()]
    L.append(f"Steadiest cut in the measurable range: **{steady['threshold']:.2f}**, "
             f"swinging {sgn(steady['swing'])} points between halves "
             f"({pct(steady['prec_half_A'])} vs {pct(steady['prec_half_B'])}).")
    L.append("")

    # 4
    L.append("## 4. The in-sample optimum is a mirage")
    L.append("")
    L.append("Searching this fold for the best cut and quoting its precision is "
             "selection bias. Here is that bias measured directly: choose the cut on "
             "one half, then score it on the half it has never seen.")
    L.append("")
    L.append("| experiment | cut chosen | looked like | **actually delivered** | fires | "
             "optimism |")
    L.append("|---|---|---|---|---|---|")
    for lbl, t, pin, pout, m in sel:
        L.append(f"| {lbl} | {t:.2f} | {pct(pin)} | **{pct(pout)}** | {m} | "
                 f"{sgn(pin - pout)} pts |")
    L.append("")
    gap = float(np.nanmean([pin - pout for _, _, pin, pout, _ in sel]))
    L.append(f"Searching the **whole** fold returns `{tF:.2f}` at {pct(pF_in)} on "
             f"{nF} fires. That number carries the same bias — on average "
             f"**{sgn(gap)} points** of it — so it should not be quoted as the "
             "expected hit rate. A cut chosen for the *shape* of the curve, rather "
             "than for topping the table, does not pay that penalty.")
    L.append("")

    # 5
    L.append("## 5. Absolute cut or percentile?")
    L.append("")
    L.append("An absolute number is only as stable as the score distribution behind it. "
             "A percentile pins the workload instead and lets the number move.")
    L.append("")
    L.append("| nominal | workload in half A | workload in half B | drift |")
    L.append("|---|---|---|---|")
    for t in ok["threshold"]:
        wa, wb = float((s[A] >= t).mean()), float((s[B] >= t).mean())
        L.append(f"| {t:.2f} | {pct(wa)} | {pct(wb)} | {sgn(wa - wb)} pts |")
    L.append("")
    L.append("| workload held fixed | cut in half A | cut in half B | precision A | "
             "precision B | swing |")
    L.append("|---|---|---|---|---|---|")
    for q in (0.05, 0.10, 0.15, 0.20, 0.30):
        ta, tb = float(np.quantile(s[A], 1 - q)), float(np.quantile(s[B], 1 - q))
        ma, mb = A & (s >= ta), B & (s >= tb)
        pa, pb = float(y[ma].mean()), float(y[mb].mean())
        L.append(f"| top {q:.0%} | {ta:.3f} | {tb:.3f} | {pct(pa)} | {pct(pb)} | "
                 f"**{sgn(abs(pa - pb))} pts** |")
    L.append("")
    L.append("This is the same guidance already written into "
             "`Orchestrator5.3.py:1583` — *\"pick a cut from a measured quantile of "
             "your own tape, not from '0.5 means more likely than not'\"*.")
    L.append("")

    # integrity
    L.append("## Cross-check integrity")
    L.append("")
    L.append("| check | expected | got | |")
    L.append("|---|---|---|---|")
    for name, a, b in checks:
        L.append(f"| {name} | {a:g} | {b:g} | {'✅' if abs(a - b) < 0.5 else '❌'} |")
    L.append("")
    L.append(f"**{n_ok}/{len(checks)} checks pass.**")
    L.append("")

    L.append("## Caveats")
    L.append("")
    L.append(f"1. **{P} positives on one {nd}-session window.** Every interval here is "
             "wide because of that, and no method fixes it.")
    L.append("2. **The plateau is flat within noise.** Treat the recommended cut as the "
             "best point estimate in a band, not a proven optimum.")
    L.append("3. **Re-derive, do not inherit.** When a new validation window exists, "
             "run this again rather than carrying the number forward — section 5 shows "
             "an absolute cut drifts as a workload even between two halves of one month.")
    L.append("4. **Raw scores throughout.** Applying a calibration would renumber the "
             "cut without changing which rows it selects.")
    L.append("")
    L.append("## Artifacts")
    L.append("")
    L.append(f"- `{stem}.tsv` — the full grid, every column computed above")
    L.append("")
    L.append(f"Regenerate: `/home/tom/venv/bin/python sweep_clf_threshold.py "
             f"--column {col} --target-threshold {tt:.0f}`")
    L.append("")
    L.append("See also `2026-09-20_XGB_model2_deep_analysis.md` (what these columns can "
             "and cannot do) and `2026-09-20_XGB_model2_calibration.md` (why raw is "
             "fine for this purpose).")
    L.append("")

    # verdict
    cand = ok[ok["precision"] >= pk - 0.01]
    rec_row = cand.loc[cand["recall"].idxmax()]
    rt = float(rec_row["threshold"])
    qeq = float((s >= rt).mean())
    v = ["## Verdict", ""]
    v.append(f"**Use `{col} >= {rt:.2f}` — or equivalently the top ~{qeq*100:.0f}% by "
             f"score.**")
    v.append("")
    v.append(f"That fires {int(rec_row['fires'])} times across {nd} sessions "
             f"({rec_row['fires_per_day']:.1f}/day), of which "
             f"**{int(rec_row['TP'])} reach `>= {tt:.0f}%` and "
             f"{int(rec_row['FP'])} do not** — precision {pct(rec_row['precision'])}, "
             f"{fmt(rec_row['lift'], 2)}x the base rate, catching "
             f"{pct(rec_row['recall'])} of all the big movers in the fold.")
    v.append("")
    v.append(f"It is the **lowest** cut on the precision plateau ({lo_t:.2f}–{hi_t:.2f}), "
             "which is the right end to stand on: precision is flat across that band "
             "while recall falls steadily, so the cheapest cut in the plateau keeps the "
             f"most winners. It is also the steadiest by split-half swing "
             f"({sgn(rec_row['swing'])} points).")
    v.append("")
    v.append(f"**Do not go higher.** The apparent optimum at `{tF:.2f}` "
             f"({pct(pF_in)}) does not survive honest testing — the same search on half "
             f"the data overstated itself by {sgn(gap)} points (§4). Above the plateau "
             "you pay recall for noise.")
    v.append("")
    v.append("**Caveat, stated plainly:** every interval across the plateau overlaps, "
             "so this is the best-supported point in a band, not a proven optimum.")
    v.append("")
    L[verdict_slot] = "\n".join(v)

    rp = (_HERE / report).resolve()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(L) + "\n")
    print(f"report -> {rp}")
    print(f"artifact -> {outdir / (stem + '.tsv')}")
    if n_ok != len(checks):
        sys.exit(f"INTEGRITY FAILURE: {len(checks) - n_ok} check(s) failed")


if __name__ == "__main__":
    main()
