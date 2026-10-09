#!/usr/bin/env python3
"""All-tier-1 rule sweep on the top-6 whitelist: every channel already runs live.

Four channels, all of them already scored by O5.2 on every PR, so ANY rule this finds
is a rules-file edit with ZERO new latency and no model to wire in:

    FinMarBa_ind_hd <= t1   (the indecision veto CORE/KwBodySolo/FLANGSolo already use)
    Prosus_pos_hd   >= t2   (ProsusAI FinBERT, the FinBERTSolo channel)
    kw15_proba_bd   >= t3
    kw15_proba_hd   >= t4

This is where the analysis should have started. The route here was long: the open
100-channel search's best rules needed DeBERTa body scores (measured 805 ms/PR), the
fallback "Rule B" needed TradeHero+FinMarBa body (104+76 ms), and both body terms then
turned out to contribute ~nothing once FinMarBa_ind_hd was in the rule.

!! FinMarBa_ind_hd is a LOW-`indecisive` cut, which means "confident in EITHER
   direction" - it cannot pin the sign alone (see the !!!! warning in
   orchestrator_trigger_rules.txt, and the WETO reverse-split false positive that
   killed the standalone version). Every rule here carries Prosus_pos_hd and/or the
   kw15 channels beside it, which is the required shape.

WINDOW: kw15 trained on 2026-05-03..06-19, so everything is computed on 2026-06-20+.

  /home/tom/venv/bin/python sweep_tier1_rule.py \
      --input outputs/07_PRstats_2026-05-04-2026-08-13_gateReplay.tsv
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

import replay_gate
from search_class_encoder_rules import wilson

TARGET, DATE, CLASS = "fromPR_High(%)", "ArrivalDate", "FineClass"
KW_HONEST_FROM = "2026-06-20"
LIVE_TPD = 3.3
WL6 = ["MA", "Partnership", "Regain_Compliance", "Clinical_Data",
       "Contract_Award", "Patent_IP"]
TERMS = [("FinMarBa_ind_hd", "<="), ("Prosus_pos_hd", ">="),
         ("kw15_proba_bd", ">="), ("kw15_proba_hd", ">=")]


def term_masks(v: np.ndarray, op: str, n_cuts: int):
    """Masks for every threshold, plus an OFF sentinel so 'this term is absent' is
    inside the grid rather than a separate pass."""
    ok = ~np.isnan(v)
    qs = np.unique(np.nanquantile(v, np.linspace(0.02, 0.98, n_cuts)))
    out, labs = [ok.copy()], [None]
    for t in qs:
        out.append(((v >= t) if op == ">=" else (v <= t)) & ok)
        labs.append(float(t))
    return np.array(out, bool), labs


def cross(sets, labs):
    M, L = sets[0], [(x,) for x in labs[0]]
    for s, lb in zip(sets[1:], labs[1:]):
        M = (M[:, None, :] & s[None, :, :]).reshape(-1, M.shape[1])
        L = [t + (x,) for t in L for x in lb]
    return M, L


def five_num(x):
    if not len(x):
        return (np.nan,) * 5
    return (np.nanmin(x), np.nanpercentile(x, 25), np.nanmedian(x),
            np.nanpercentile(x, 75), np.nanmax(x))


def fmt(labels):
    return " AND ".join(f"`{c} {o} {t:.4f}`" for (c, o), t in zip(TERMS, labels)
                        if t is not None) or "(all terms off)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--n-cuts", type=int, default=18)
    ap.add_argument("--band", type=float, default=0.4)
    ap.add_argument("--null-repeats", type=int, default=25)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="13_tier1_rule")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    df["gate_ok"] = replay_gate.replay(df).is_ok.values
    df = df[df[DATE] >= KW_HONEST_FROM].reset_index(drop=True)
    df["y"] = (df[TARGET] >= a.tp_threshold).astype(int)

    gate_n = int(df.gate_ok.sum())
    gate_k = int(df.loc[df.gate_ok, "y"].sum())
    per_fire = LIVE_TPD / gate_n
    days = df[DATE].nunique()

    sel = df[CLASS].isin(WL6).to_numpy()
    sub = df[sel].reset_index(drop=True)
    y = sub["y"].to_numpy().astype(np.float32)
    hi = sub[TARGET].to_numpy()

    sets, labs = zip(*(term_masks(pd.to_numeric(sub[c], errors="coerce").to_numpy(float),
                                  o, a.n_cuts) for c, o in TERMS))
    M, L = cross(list(sets), list(labs))
    Mf = M.astype(np.float32)
    fires = Mf.sum(axis=1)
    tps = Mf @ y
    print(f"{len(sub)} whitelist rows | {len(L):,} threshold combinations")

    rng = np.random.default_rng(0)
    perms = [rng.permutation(y) for _ in range(a.null_repeats)]
    null_tps = np.stack([Mf @ p for p in perms])          # (repeats, combos)

    rows = []
    for tpd in (2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0):
        lo, hiN = (tpd - a.band) / per_fire, (tpd + a.band) / per_fire
        band = (fires >= lo) & (fires <= hiN)
        if not band.any():
            continue
        prec = np.where(band, tps / np.maximum(fires, 1), -1.0)
        b = int(np.argmax(prec))
        n, k = int(fires[b]), int(tps[b])
        # Noise floor: the best precision this SAME grid reaches in the SAME volume
        # band on shuffled labels. Selection bias, measured rather than assumed.
        nf = np.mean([np.max(np.where(band, nt / np.maximum(fires, 1), -1.0))
                      for nt in null_tps])
        lo_ci, hi_ci = wilson(k, n)
        q = five_num(hi[M[b]])
        rows.append({"target_tpd": tpd, "rule": fmt(L[b]), "labels": L[b],
                     "idx": b, "fires": n, "tp": k, "fp": n - k,
                     "prec": k / n, "tpfp": k / (n - k) if n > k else np.inf,
                     "trades_day": n * per_fire, "floor": nf,
                     "above_floor": k / n - nf, "ci_lo": lo_ci, "ci_hi": hi_ci,
                     "hi_min": q[0], "hi_q1": q[1], "hi_med": q[2],
                     "hi_q3": q[3], "hi_max": q[4],
                     "n_terms": sum(x is not None for x in L[b])})
    res = pd.DataFrame(rows)

    W, Lo = [], None
    W = W.append if False else Lo
    out = []
    A = out.append
    A("# All-tier-1 rule on the top-6 whitelist — nothing new to compute\n")
    A(f"TP = `fromPR_High(%)` >= {a.tp_threshold:g}%. Honest window "
      f"(`>= {KW_HONEST_FROM}`, kw15's training cutoff): **{len(df):,} rows / {days} "
      f"days**, base **{df.y.mean():.2%}**. Whitelist = "
      + ", ".join(f"`{c}`" for c in WL6) + f" → **{len(sub)} rows**, base "
      f"**{sub.y.mean():.1%}**, ceiling **{len(sub) * per_fire:.1f} trades/day**.\n")
    A(f"All four channels — `FinMarBa_ind_hd`, `Prosus_pos_hd`, `kw15_proba_bd`, "
      f"`kw15_proba_hd` — are **already scored live by O5.2 on every PR**. Any rule "
      f"below is a rules-file edit: no model to wire in, no added latency.\n")
    A(f"Live gate on the same rows: {gate_n} fires, {gate_k} TP, {gate_n-gate_k} FP — "
      f"precision {gate_k/gate_n:.1%}, **TP:FP {gate_k/(gate_n-gate_k):.2f}** at "
      f"{LIVE_TPD} trades/day.\n")
    A(f"\n{len(L):,} threshold combinations swept (each term may also be OFF).\n")

    A("\n## Best rule at each trigger rate\n")
    A("`vs floor` compares against the best precision this same grid reaches on "
      "SHUFFLED labels in the same volume band — the selection bias, measured.\n")
    A("| trades/day | rule | terms | fires | TP | FP | TP:FP | precision | floor | "
      "vs floor | 95% CI |")
    A("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    for _, r in res.iterrows():
        mark = "**" if r.above_floor > 0.03 else ""
        A(f"| **{r.trades_day:.1f}** | {r.rule} | {int(r.n_terms)} | {r.fires} | "
          f"{r.tp} | {r.fp} | **{r.tpfp:.2f}** | {r.prec:.1%} | {r.floor:.1%} | "
          f"{mark}{r.above_floor:+.1%}{mark} | {r.ci_lo:.0%}-{r.ci_hi:.0%} |")

    A("\n## `fromPR_High(%)` distribution on fired rows\n")
    A("| trades/day | TP:FP | min | Q1 | median | Q3 | max |")
    A("|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in res.iterrows():
        A(f"| {r.trades_day:.1f} | {r.tpfp:.2f} | {r.hi_min:.1f} | {r.hi_q1:.1f} | "
          f"**{r.hi_med:.1f}** | {r.hi_q3:.1f} | {r.hi_max:.0f} |")

    # ── drop-one on the 6/day rule: which terms actually earn their place ────
    tgt = res.iloc[(res.target_tpd - 6.0).abs().argmin()]
    A(f"\n## Drop-one on the {tgt.trades_day:.1f} trades/day rule\n")
    A("Removing one term at a time. A term whose removal barely moves TP:FP is "
      "decoration, whatever its threshold looks like.\n")
    A("| dropped | passes (of whitelist) | fires | TP | precision | TP:FP | delta |")
    A("|---|---:|---:|---:|---:|---:|---:|")
    base_lab = tgt.labels
    keep = [i for i, t in enumerate(base_lab) if t is not None]
    masks = {}
    for i in keep:
        c, o = TERMS[i]
        v = pd.to_numeric(sub[c], errors="coerce").to_numpy(float)
        masks[i] = ((v >= base_lab[i]) if o == ">=" else (v <= base_lab[i])) & ~np.isnan(v)
    full = np.logical_and.reduce([masks[i] for i in keep])
    fn, fk = int(full.sum()), int(y[full].sum())
    base_r = fk / (fn - fk)
    for i in keep:
        m = np.logical_and.reduce([masks[j] for j in keep if j != i])
        n2, k2 = int(m.sum()), int(y[m].sum())
        r2 = k2 / (n2 - k2) if n2 > k2 else np.inf
        A(f"| `{TERMS[i][0]}` | {masks[i].mean():.0%} | {n2} | {k2} | {k2/n2:.1%} | "
          f"{r2:.3f} | {r2 - base_r:+.3f} |")
    A(f"\nFull rule: {fn} fires, {fk} TP, precision {fk/fn:.1%}, TP:FP {base_r:.3f}.\n")

    outdir = Path(a.outdir)
    res.drop(columns=["labels", "idx"]).to_csv(outdir / f"{a.prefix}.tsv",
                                               sep="\t", index=False)
    (outdir / f"{a.prefix}.md").write_text("\n".join(out) + "\n")
    print(f"wrote {outdir / (a.prefix + '.md')}")


if __name__ == "__main__":
    main()
