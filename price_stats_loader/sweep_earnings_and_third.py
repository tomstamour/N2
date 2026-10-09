#!/usr/bin/env python3
"""Two questions, one script.

  A. Is `EarningScore >= 4 AND kw15_proba_bd >= 0.50` at the right thresholds?
  B. Does adding a THIRD sentiment channel improve the top-6 whitelist rule?
     The operator currently has Prosus_pos_hd + FinMarBa_ind_hd (two sentiment
     channels) plus the two kw15 keyword channels. The only third sentiment channel
     that costs nothing is FLANG - O5.2 already scores it live on every PR.

!!!! PART A IS MEASURED ON A CORRUPTED INPUT AND ITS NUMBERS ARE SOFT.
   The corpus `Body` is hard-capped at 400 words (4084/4376 rows sit at exactly 400,
   each a strict prefix of what O5.2 received live, whose bodies run 4.8k-28.5k
   chars). The earnings scorer reads the WHOLE body for figures, so it sees a
   different document here than in production. Measured against orchestrator5/tables
   for 2026-08-12..14: 42 of 70 earnings rows score differently, and 9 (12.9%) cross
   the `>= 4` cut. A retune derived here would be fitted to truncated documents.
   Part B is unaffected - headline channels and kw15 (which caps at 400 words itself)
   both agree with live.

WINDOW: kw15 trained on 2026-05-03..06-19, so everything is on 2026-06-20+.
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
BASE4 = [("FinMarBa_ind_hd", "<="), ("Prosus_pos_hd", ">="),
         ("kw15_proba_bd", ">="), ("kw15_proba_hd", ">=")]
# Candidate third sentiment channels. FLANG is the only one already live (tier 1);
# the rest are shown so the operator can see what a paid channel would buy.
THIRD = [("FLANG_ss_hd", ">=", 1), ("FLANG_pos_hd", ">=", 1), ("FLANG_neg_hd", "<=", 1),
         ("FLANG_neu_hd", "<=", 1), ("FLElectra_ss_hd", ">=", 2),
         ("DistilRo_ss_hd", ">=", 2), ("TradeHero_ss_hd", ">=", 2),
         ("FinTone_ss_hd", ">=", 2), ("RoBERTa_ss_hd", ">=", 2)]


def num(df, c):
    return pd.to_numeric(df[c], errors="coerce").to_numpy(float)


def masks(v, op, n_cuts, off=True):
    ok = ~np.isnan(v)
    qs = np.unique(np.nanquantile(v, np.linspace(0.02, 0.98, n_cuts)))
    out, labs = ([ok.copy()], [None]) if off else ([], [])
    for t in qs:
        out.append(((v >= t) if op == ">=" else (v <= t)) & ok)
        labs.append(float(t))
    return np.array(out, bool), labs


def five(x):
    return ((np.nan,) * 5 if not len(x) else
            (np.nanmin(x), np.nanpercentile(x, 25), np.nanmedian(x),
             np.nanpercentile(x, 75), np.nanmax(x)))


def stat(m, y, hi):
    n, k = int(m.sum()), int(y[m].sum())
    lo, up = wilson(k, n)
    return {"fires": n, "tp": k, "fp": n - k,
            "prec": k / n if n else np.nan,
            "tpfp": k / (n - k) if n > k else (np.inf if k else 0.0),
            "ci_lo": lo, "ci_hi": up, "q": five(hi[m])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--n-cuts", type=int, default=16)
    ap.add_argument("--null-repeats", type=int, default=30)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="14_earnings_third")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    df["gate_ok"] = replay_gate.replay(df).is_ok.values
    df = df[df[DATE] >= KW_HONEST_FROM].reset_index(drop=True)
    df["y"] = (df[TARGET] >= a.tp_threshold).astype(int)
    per_fire = LIVE_TPD / int(df.gate_ok.sum())
    O = []
    W = O.append

    W("# A. Retuning `EarningScore >= 4 AND kw15_proba_bd >= 0.50`  ·  "
      "B. Is a third sentiment score worth it?\n")
    W(f"Honest window (`>= {KW_HONEST_FROM}`): {len(df):,} rows / "
      f"{df[DATE].nunique()} days, base {df.y.mean():.2%}. "
      f"1 trade/day = {1/per_fire:.0f} fires.\n")

    # ── A. earnings ──────────────────────────────────────────────────────────
    e = df[df[CLASS] == "Earnings_Results"].reset_index(drop=True)
    ey = e["y"].to_numpy()
    ehi = e[TARGET].to_numpy()
    es = num(e, "EarningScore")
    kb = num(e, "kw15_proba_bd")

    W("\n## A. The earnings rule\n")
    W("> ⚠️ **These numbers rest on a corrupted input.** The corpus `Body` is capped "
      "at 400 words; the earnings scorer reads the whole body. Against live tables for "
      "2026-08-12..14, **42 of 70 earnings rows score differently offline and 9 "
      "(12.9%) cross the `>= 4` cut**. Treat everything in this section as indicative, "
      "and do not retune the live threshold from it without an untruncated re-fetch.\n")
    W(f"\n`Earnings_Results` in this window: **{len(e)} rows, {int(ey.sum())} TP "
      f"({ey.mean():.1%})** — against a {df.y.mean():.1%} corpus base, so the class is "
      "already below average before any scoring.\n")

    cur = (es >= 4) & (kb >= 0.50)
    s = stat(cur, ey, ehi)
    W(f"\n**Current live thresholds (4, 0.50):** {s['fires']} fires, {s['tp']} TP, "
      f"{s['fp']} FP — precision **{s['prec']:.1%}**, **TP:FP {s['tpfp']:.2f}**, "
      f"{s['fires']*per_fire:.1f} trades/day, CI {s['ci_lo']:.0%}-{s['ci_hi']:.0%}.\n")

    W("\n### EarningScore threshold sweep (kw15_proba_bd held at 0.50)\n")
    W("| EarningScore >= | fires | TP | FP | TP:FP | precision | 95% CI | trades/day |")
    W("|---:|---:|---:|---:|---:|---:|---|---:|")
    for t in range(0, 9):
        m = (es >= t) & (kb >= 0.50)
        if m.sum() < 5:
            continue
        st = stat(m, ey, ehi)
        mark = " **(live)**" if t == 4 else ""
        W(f"| **{t}**{mark} | {st['fires']} | {st['tp']} | {st['fp']} | "
          f"{st['tpfp']:.2f} | {st['prec']:.1%} | {st['ci_lo']:.0%}-{st['ci_hi']:.0%} | "
          f"{st['fires']*per_fire:.1f} |")

    W("\n### Joint sweep — both thresholds free\n")
    W("| EarningScore >= | kw15_proba_bd >= | fires | TP | FP | TP:FP | precision | trades/day |")
    W("|---:|---:|---:|---:|---:|---:|---:|---:|")
    grid = []
    for t in range(0, 9):
        for b in np.round(np.arange(0.30, 0.86, 0.05), 2):
            m = (es >= t) & (kb >= b)
            if m.sum() < 8:
                continue
            st = stat(m, ey, ehi)
            grid.append((st["tpfp"], st["fires"], t, b, st))
    grid.sort(key=lambda r: (-r[0], -r[1]))
    for tpfp, n, t, b, st in grid[:8]:
        W(f"| {t} | {b:.2f} | {st['fires']} | {st['tp']} | {st['fp']} | "
          f"**{tpfp:.2f}** | {st['prec']:.1%} | {st['fires']*per_fire:.1f} |")
    W(f"\nBest found: EarningScore >= {grid[0][2]}, kw15_proba_bd >= {grid[0][3]:.2f} "
      f"(TP:FP {grid[0][0]:.2f} vs the live {s['tpfp']:.2f}).\n")

    # ── B. third sentiment channel on the whitelist ──────────────────────────
    sub = df[df[CLASS].isin(WL6)].reset_index(drop=True)
    y = sub["y"].to_numpy().astype(np.float32)
    hi = sub[TARGET].to_numpy()
    W("\n## B. Does a third sentiment channel help the top-6 whitelist rule?\n")
    W(f"Whitelist: {len(sub)} rows, {int(y.sum())} TP ({y.mean():.1%}). The base rule "
      "already carries `FinMarBa_ind_hd` + `Prosus_pos_hd` (sentiment) and both kw15 "
      "channels (keyword). A third sentiment channel is added as a 5th AND term and "
      "ALL FIVE thresholds are re-swept jointly, so the comparison is fair — the base "
      "rule is free to move too.\n")

    sets, labs = zip(*(masks(num(sub, c), o, a.n_cuts) for c, o in BASE4))
    B, BL = sets[0], [(x,) for x in labs[0]]
    for s2, lb in zip(sets[1:], labs[1:]):
        B = (B[:, None, :] & s2[None, :, :]).reshape(-1, len(sub))
        BL = [t + (x,) for t in BL for x in lb]
    Bf = B.astype(np.float32)

    rng = np.random.default_rng(0)
    perms = [rng.permutation(y) for _ in range(a.null_repeats)]

    def best_at(Mf, Mb, tpd, band=0.4):
        lo, up = (tpd - band) / per_fire, (tpd + band) / per_fire
        f = Mf.sum(axis=1)
        sel = (f >= lo) & (f <= up)
        if not sel.any():
            return None, np.nan
        t = Mf @ y
        p = np.where(sel, t / np.maximum(f, 1), -1.0)
        i = int(np.argmax(p))
        nf = np.mean([np.max(np.where(sel, (Mf @ pp) / np.maximum(f, 1), -1.0))
                      for pp in perms])
        return (i, Mb[i]), nf

    rows = []
    for tpd in (2.0, 4.0, 6.0):
        r0, nf0 = best_at(Bf, B, tpd)
        if r0 is None:
            continue
        i0, m0 = r0
        st0 = stat(m0, y.astype(int), hi)
        rows.append({"tpd": tpd, "third": "— none (base 4-term)", "tier": 1,
                     "floor": nf0, **st0, "labels": BL[i0], "thr": None})
        for c, o, tier in THIRD:
            if c not in sub.columns:
                continue
            tm, tl = masks(num(sub, c), o, a.n_cuts)
            M2 = (B[:, None, :] & tm[None, :, :]).reshape(-1, len(sub))
            L2 = [t + (x,) for t in BL for x in tl]
            r, nf = best_at(M2.astype(np.float32), M2, tpd)
            if r is None:
                continue
            i, m = r
            st = stat(m, y.astype(int), hi)
            rows.append({"tpd": tpd, "third": c, "tier": tier, "floor": nf,
                         **st, "labels": L2[i][:4], "thr": L2[i][4]})
    res = pd.DataFrame(rows)

    for tpd in sorted(res.tpd.unique()):
        g = res[res.tpd == tpd].sort_values("tpfp", ascending=False)
        b0 = g[g.third.str.startswith("—")].iloc[0]
        W(f"\n### At ~{tpd:.0f} trades/day\n")
        W("| third channel | tier | its threshold | fires | TP | FP | TP:FP | "
          "precision | vs base | floor | vs floor |")
        W("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        ops = {c: o for c, o, _ in THIRD}
        for _, r in g.iterrows():
            # A None/NaN threshold means the sweep chose to switch the term OFF - the
            # channel earned nothing, which is a result, not a missing value.
            thr = ("—" if r.third.startswith("—") else
                   "*(switched off — no threshold helped)*" if pd.isna(r.thr) else
                   f"`{r.third} {ops.get(r.third, '>=')} {r.thr:.4f}`")
            d = r.tpfp - b0.tpfp
            dm = "" if r.third.startswith("—") else f"{d:+.3f}"
            mark = "**" if (not r.third.startswith("—")) and d > 0.02 else ""
            W(f"| {r.third} | {int(r.tier)} | {thr} | {r.fires} | {r.tp} | {r.fp} | "
              f"{mark}{r.tpfp:.2f}{mark} | {r.prec:.1%} | {dm} | {r.floor:.1%} | "
              f"{r.prec - r.floor:+.1%} |")

    outdir = Path(a.outdir)
    res.drop(columns=["labels", "q"]).to_csv(outdir / f"{a.prefix}.tsv",
                                             sep="\t", index=False)
    (outdir / f"{a.prefix}.md").write_text("\n".join(O) + "\n")
    print(f"wrote {outdir / (a.prefix + '.md')}")


if __name__ == "__main__":
    main()
