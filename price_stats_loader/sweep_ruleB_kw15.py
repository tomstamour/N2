#!/usr/bin/env python3
"""Rule B + kw15, threshold sweep inside a fixed class whitelist, targeting ~6/day.

Rule B came out of the 10_menu_search open search as the best 3-term rule whose
channels are affordable at runtime (measured 2026-08-17: TradeHero body 104 ms,
FinMarBa body 76 ms, FLANG headline 8 ms - vs DeBERTa body's 805 ms, which is why the
higher-scoring DeBERTa variant is not here):

    TradeHero_ss_bd >= t1  AND  FinMarBa_neg_bd >= t2  AND  FLANG_neg_hd <= t3

This sweeps those three thresholds jointly with kw15_proba_bd/hd added as 4th and 5th
terms, and reports what each variant does at a TARGET TRIGGER RATE rather than at
whatever volume the optimum happens to land on.

WINDOW: kw15 was trained on 2026-05-03..06-19, so EVERY number here is computed on
2026-06-20 onward. That is the operator's explicit choice and it costs 61% of the
corpus - a kw15 threshold picked on the training dates would be fitted to rows the
model already saw.

The threshold grid is still a search, so the permutation null is still reported. It is
much smaller than the open search (5 fixed channels, directions fixed by Rule B, only
thresholds free) so the floor should be correspondingly lower.

  /home/tom/venv/bin/python sweep_ruleB_kw15.py \
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

# Operator's choice: the top-8-by-hit-rate list minus Business_Update AND
# Buyback_Dividend. That leaves SIX classes, not seven - noted in the report.
WL6 = ["MA", "Partnership", "Regain_Compliance", "Clinical_Data",
       "Contract_Award", "Patent_IP"]
MENU = {"top-6 whitelist": WL6}
# (column, direction) - directions are FIXED by Rule B, only thresholds are swept.
# FinMarBa_ind_hd added 2026-08-17 at the operator's request. It is the indecision
# veto CORE/KwBodySolo/FLANGSolo already run, it is a HEADLINE channel (tier 1, no new
# latency) unlike FinMarBa_neg_bd, and it only ever appears here beside direction terms
# - the shape the rules file's !!!! warning requires, since a low `ind` means confident
# in EITHER direction and cannot pin the sign alone.
TERMS = [("TradeHero_ss_bd", ">="), ("FinMarBa_neg_bd", ">="), ("FLANG_neg_hd", "<="),
         ("FinMarBa_ind_hd", "<=")]
KW_TERMS = [("kw15_proba_bd", ">="), ("kw15_proba_hd", ">=")]


def cuts_for(v: np.ndarray, n: int) -> np.ndarray:
    """Quantile cuts plus a pass-everything sentinel, so 'this term off' is inside
    the grid rather than a separate code path."""
    q = np.unique(np.nanquantile(v, np.linspace(0.02, 0.98, n)))
    return q


def masks_for(v: np.ndarray, op: str, qs: np.ndarray, allow_off: bool) -> tuple:
    ok = ~np.isnan(v)
    out, labs = [], []
    if allow_off:
        out.append(ok.copy())
        labs.append(None)                       # term absent
    for t in qs:
        m = (v >= t) if op == ">=" else (v <= t)
        out.append(m & ok)
        labs.append(float(t))
    return np.array(out, bool), labs


def five_num(x: np.ndarray) -> tuple:
    if not len(x):
        return (np.nan,) * 5
    return (np.nanmin(x), np.nanpercentile(x, 25), np.nanmedian(x),
            np.nanpercentile(x, 75), np.nanmax(x))


def sweep(base_masks, base_labs, kw_masks, kw_labs, y, hi, target_lo, target_hi):
    """All (base x kw) combinations at once via two matmuls. Returns a frame of
    every combination whose fire count lands inside the target band."""
    B = base_masks.astype(np.float32)
    K = kw_masks.astype(np.float32)
    fires = B @ K.T
    tps = (B * y) @ K.T
    bi, ki = np.where((fires >= target_lo) & (fires <= target_hi))
    rows = []
    for b, k in zip(bi, ki):
        n, t = int(fires[b, k]), int(tps[b, k])
        fp = n - t
        rows.append({"b": b, "k": k, "fires": n, "tp": t, "fp": fp,
                     "prec": t / n, "tpfp": t / fp if fp else np.inf})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--target-per-day", type=float, default=6.0)
    ap.add_argument("--band", type=float, default=0.5, help="+/- trades/day tolerance")
    ap.add_argument("--n-cuts", type=int, default=14)
    ap.add_argument("--null-repeats", type=int, default=20)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="11_ruleB_kw15")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    gate = replay_gate.replay(df)
    df["gate_ok"] = gate.is_ok.values
    df = df[df[DATE] >= KW_HONEST_FROM].reset_index(drop=True)   # honest window
    df["y"] = (df[TARGET] >= a.tp_threshold).astype(int)

    y = df["y"].to_numpy().astype(np.float32)
    hi = df[TARGET].to_numpy()
    days = df[DATE].nunique()
    gate_n = int(df.gate_ok.sum())
    gate_k = int(df.loc[df.gate_ok, "y"].sum())
    # Volume anchor: the replayed gate on these rows IS the live 3.3 trades/day.
    per_fire = LIVE_TPD / gate_n
    tgt = a.target_per_day / per_fire
    lo_n, hi_n = (a.target_per_day - a.band) / per_fire, (a.target_per_day + a.band) / per_fire

    L, W = [], None
    W = L.append
    W("# Rule B + kw15 — threshold sweep at a ~%.0f trigger/day target\n" % a.target_per_day)
    W(f"TP = `fromPR_High(%)` >= {a.tp_threshold:g}%. **Honest window only** "
      f"(`{DATE} >= {KW_HONEST_FROM}`, since kw15 trained on 2026-05-03..06-19): "
      f"**{len(df):,} rows over {days} days**, base rate **{df.y.mean():.2%}**.\n")
    W(f"Live O5.2 gate on these rows: {gate_n} fires, {gate_k} TP, {gate_n-gate_k} FP — "
      f"precision {gate_k/gate_n:.1%}, **TP:FP {gate_k/(gate_n-gate_k):.2f}**, "
      f"{LIVE_TPD} trades/day. So 1 trade/day = {1/per_fire:.0f} fires here, and your "
      f"**{a.target_per_day:.0f}/day target = ~{tgt:.0f} fires** "
      f"(band {lo_n:.0f}–{hi_n:.0f}).\n")

    W("\n## The volume budget, before any threshold\n")
    W("A rule can only fire on rows that exist, so the whitelist itself caps the "
      "achievable trigger rate.\n")
    W("| class subset | rows | max trades/day if EVERY row fired | TPs | base rate |")
    W("|---|---:|---:|---:|---:|")
    for label, keep in MENU.items():
        sel = np.ones(len(df), bool) if keep is None else df[CLASS].isin(keep).to_numpy()
        n = int(sel.sum())
        flag = "" if n * per_fire >= a.target_per_day else " 🚫 CANNOT REACH TARGET"
        W(f"| {label}{flag} | {n} | **{n * per_fire:.1f}** | {int(df.y[sel].sum())} | "
          f"{df.y[sel].mean():.1%} |")
    hit = [l for l, k in MENU.items()
           if (np.ones(len(df), bool) if k is None
               else df[CLASS].isin(k).to_numpy()).sum() * per_fire >= a.target_per_day]
    if hit:
        sel0 = df[CLASS].isin(MENU[hit[0]]).to_numpy()
        W(f"\nThe target is reachable, but only just: hitting {a.target_per_day:.0f}/day "
          f"means firing on ~{tgt / int(sel0.sum()):.0%} of every row in the whitelist, "
          f"so the score cuts can do very little filtering. Precision is therefore "
          f"pinned close to the whitelist's own {df.y[sel0].mean():.1%} base rate — that "
          "is the real cost of the volume target, and no threshold can undo it.\n")

    # ── sweep, per menu entry ────────────────────────────────────────────────
    rng = np.random.default_rng(0)
    out_rows = []
    for label, keep in MENU.items():
        sel = np.ones(len(df), bool) if keep is None else df[CLASS].isin(keep).to_numpy()
        if sel.sum() * per_fire < a.target_per_day - a.band:
            continue                       # cannot reach the target band at all
        idx = np.where(sel)[0]
        ysub, hisub = y[idx], hi[idx]

        base_sets, base_labs = [], []
        for col, op in TERMS:
            v = pd.to_numeric(df[col], errors="coerce").to_numpy(float)[idx]
            m, lb = masks_for(v, op, cuts_for(v, a.n_cuts), allow_off=True)
            base_sets.append(m)
            base_labs.append(lb)
        # cross the three base terms
        B, BL = base_sets[0], [(l,) for l in base_labs[0]]
        for s, lb in zip(base_sets[1:], base_labs[1:]):
            B = (B[:, None, :] & s[None, :, :]).reshape(-1, len(idx))
            BL = [t + (l,) for t in BL for l in lb]

        for variant, kwcols in (("no kw15", []),
                                ("+ kw15_proba_bd", [KW_TERMS[0]]),
                                ("+ kw15_proba_hd", [KW_TERMS[1]]),
                                ("+ both kw15", KW_TERMS)):
            if kwcols:
                K, KL = None, [()]
                for col, op in kwcols:
                    v = pd.to_numeric(df[col], errors="coerce").to_numpy(float)[idx]
                    m, lb = masks_for(v, op, cuts_for(v, a.n_cuts), allow_off=True)
                    K = m if K is None else (K[:, None, :] & m[None, :, :]).reshape(-1, len(idx))
                    KL = [t + (l,) for t in KL for l in lb]
            else:
                K, KL = np.ones((1, len(idx)), bool), [()]

            res = sweep(B, BL, K, KL, ysub, hisub, lo_n, hi_n)
            if res.empty:
                continue
            best = res.sort_values(["tpfp", "fires"], ascending=False).iloc[0]
            m = B[int(best.b)] & K[int(best.k)]
            fired_hi = hisub[m]
            lo_ci, hi_ci = wilson(int(best.tp), int(best.fires))
            terms = [f"`{c} {o} {t:.4f}`" for (c, o), t in zip(TERMS, BL[int(best.b)])
                     if t is not None]
            terms += [f"`{c} {o} {t:.4f}`" for (c, o), t in zip(kwcols, KL[int(best.k)])
                      if t is not None]
            q = five_num(fired_hi)
            # permutation null at THIS grid size, inside THIS subset
            nulls = []
            for _ in range(a.null_repeats):
                yp = rng.permutation(ysub)
                r = sweep(B, BL, K, KL, yp, hisub, lo_n, hi_n)
                if not r.empty:
                    nulls.append(r.prec.max())
            floor = float(np.mean(nulls)) if nulls else np.nan
            out_rows.append({
                "menu": label, "variant": variant,
                "rule": " AND ".join(terms) if terms else "(all terms off)",
                "n_terms": len(terms),
                "fires": int(best.fires), "tp": int(best.tp), "fp": int(best.fp),
                "prec": best.prec, "tpfp": best.tpfp,
                "trades_day": best.fires * per_fire,
                "ci_lo": lo_ci, "ci_hi": hi_ci,
                "floor": floor, "above_floor": best.prec - floor,
                "hi_min": q[0], "hi_q1": q[1], "hi_med": q[2], "hi_q3": q[3], "hi_max": q[4],
                "n_combos": len(res),
            })
    res_df = pd.DataFrame(out_rows)

    W(f"\n## Best rule at ~{a.target_per_day:.0f} trades/day\n")
    W("Thresholds swept jointly; directions fixed by Rule B. A term may also be OFF "
      "(that is inside the grid), so a 2-term winner means the third term did not earn "
      "its place at this volume.\n")
    W("| class subset | kw15 | rule | fires | TP | FP | TP:FP | precision | floor | "
      "vs floor | 95% CI | trades/day |")
    W("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|---:|")
    for _, r in res_df.sort_values("tpfp", ascending=False).iterrows():
        mark = "**" if r.above_floor > 0.03 else ""
        W(f"| {r.menu} | {r.variant} | {r.rule} | {r.fires} | {r.tp} | {r.fp} | "
          f"**{r.tpfp:.2f}** | {r.prec:.1%} | {r.floor:.1%} | {mark}{r.above_floor:+.1%}{mark} | "
          f"{r.ci_lo:.0%}-{r.ci_hi:.0%} | {r.trades_day:.1f} |")

    W("\n## Distribution of `fromPR_High(%)` on the rows each rule fires\n")
    W("Five-number summary over ALL fired rows (TP and FP together), as requested.\n")
    W("| class subset | kw15 | TP:FP | min | Q1 | median | Q3 | max |")
    W("|---|---|---:|---:|---:|---:|---:|---:|")
    for _, r in res_df.sort_values("tpfp", ascending=False).iterrows():
        W(f"| {r.menu} | {r.variant} | {r.tpfp:.2f} | {r.hi_min:.1f} | {r.hi_q1:.1f} | "
          f"**{r.hi_med:.1f}** | {r.hi_q3:.1f} | {r.hi_max:.0f} |")
    b = res_df.sort_values("tpfp", ascending=False).iloc[0]
    W(f"\nRead these against the 50% TP line. For the best rule the median fired row "
      f"reaches **{b.hi_med:.1f}%** and Q3 is **{b.hi_q3:.1f}%** — so under half the "
      f"triggers clear 50%, but the typical one is still a solid double-digit move, and "
      f"the minimum ({b.hi_min:.1f}%) shows even the worst trigger barely goes negative "
      "on its high-water mark. A pooled TP/FP split understates that: at this volume "
      "'FP' mostly means 'moved 10-40% instead of 50%', not 'went the wrong way'.\n")

    outdir = Path(a.outdir)
    res_df.to_csv(outdir / f"{a.prefix}.tsv", sep="\t", index=False)
    (outdir / f"{a.prefix}.md").write_text("\n".join(L) + "\n")
    print(f"{len(df)} honest rows | target {tgt:.0f} fires | {len(res_df)} variants")
    print(f"wrote {outdir / (a.prefix + '.md')}")


if __name__ == "__main__":
    main()
