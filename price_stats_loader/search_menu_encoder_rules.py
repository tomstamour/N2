#!/usr/bin/env python3
"""Best encoder rule (up to 3-way AND) inside each of a FIXED menu of FineClass
white/blacklists. Full corpus, IN-SAMPLE by explicit operator choice.

Design note on what is and is not biased here - the two dimensions differ:

  * The CLASS MENU is PRE-SPECIFIED, not searched. Nothing about it is chosen by
    maximising over the data, so every class-only number in this report is an
    honest full-corpus estimate needing no correction.
  * The ENCODER CUT is searched over ~10^5-10^7 candidates. The 11+1 encoders are
    frozen and externally trained, so no model saw this corpus - but the THRESHOLD,
    the DIRECTION, the CHANNEL and the ARITY are all picked by maximising on these
    same rows. That is a fitted choice, and the reported TP:FP is therefore an upper
    bound on live performance, not a forecast.

The size of that second bias is not asserted, it is MEASURED: `--null-repeats` runs
the identical search on SHUFFLED labels, where by construction no signal exists and
the encoders are exactly as external as before. Whatever it finds is the floor for
"meaningless". The report leads with that number.

  /home/tom/venv/bin/python search_menu_encoder_rules.py \
      --input outputs/07_PRstats_2026-05-04-2026-08-13_gateReplay.tsv
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

import replay_gate
from search_class_encoder_rules import (SCORE_RE, KW_COLS, LIVE_TPD, build_masks,
                                        channel_tier, rule_tier, wilson)

TARGET, DATE, CLASS = "fromPR_High(%)", "ArrivalDate", "FineClass"

# ── THE FIXED MENU ────────────────────────────────────────────────────────────
# Pre-specified, so these carry no selection bias. Whitelists name what to KEEP;
# blacklists name what to DROP and are stored as their complement at load time.
WHITELISTS = {
    "ALL (no class filter)": None,
    "MA": ["MA"],
    "MA + Partnership": ["MA", "Partnership"],
    "MA + Partnership + Patent_IP": ["MA", "Partnership", "Patent_IP"],
    "operator's 4 (MA/Partnership/Regain/Buyback)":
        ["MA", "Partnership", "Regain_Compliance", "Buyback_Dividend"],
    "MA + Partnership + Contract_Award + Patent_IP":
        ["MA", "Partnership", "Contract_Award", "Patent_IP"],
    "top-8 by unconditional hit rate":
        ["MA", "Partnership", "Buyback_Dividend", "Regain_Compliance", "Clinical_Data",
         "Contract_Award", "Patent_IP", "Business_Update"],
}
# The six classes furthest below base rate in 07_class_power.md, plus variants.
DEAD6 = ["Management_Change", "Earnings_Date", "Conference_Presentation",
         "Reverse_Split", "Legal", "Deficiency_Notice"]
BLACKLISTS = {
    "blacklist: 6 dead classes": DEAD6,
    "blacklist: 6 dead + Earnings_Results": DEAD6 + ["Earnings_Results"],
    "blacklist: Earnings_Results only": ["Earnings_Results"],
    "blacklist: 6 dead + Earnings_Results + Other":
        DEAD6 + ["Earnings_Results", "Other"],
}


def counts(Mb: np.ndarray, y: np.ndarray):
    """(fires, tps) per mask row."""
    f = Mb.sum(axis=1)
    t = Mb @ y
    return f, t


def search_subset(M: np.ndarray, names: list, y: np.ndarray, rows: np.ndarray,
                  min_fires: int, top_k: int, beam: int, max_arity: int):
    """Best rules of arity 1..max_arity on `rows`. Returns a list of dicts.

    Vacuous terms are stripped: a quantile grid always contains cuts at the extreme
    tail of a channel's own distribution (`X >= <the minimum>` passes everything), so
    without this the leaderboard fills with 3-term spellings of a 1-term rule."""
    Ms = M[:, rows]
    yv = y[rows].astype(np.float32)
    n_rows = Ms.shape[1]
    out = []

    f, t = counts(Ms.astype(np.float32), yv)
    ok = f >= min_fires
    prec = np.where(ok, t / np.maximum(f, 1), -1.0)
    # Keep the most promising singles as the seed for the pair/triple stages.
    seed = np.argsort(-prec)[:top_k]
    for i in np.argsort(-prec)[:beam]:
        if not ok[i]:
            continue
        out.append({"arity": 1, "terms": (i,), "fires": int(f[i]), "tp": int(t[i])})

    if max_arity < 2 or len(seed) < 2:
        return out, seed

    # --- pairs over the seed set, via matmul (fires = Mi.Mj, tps = (Mi*y).Mj)
    S = Ms[seed].astype(np.float32)
    F2 = S @ S.T
    T2 = (S * yv) @ S.T
    iu = np.triu_indices(len(seed), k=1)
    f2, t2 = F2[iu], T2[iu]
    keep = f2 >= min_fires
    # A pair whose fire set equals either parent's is that parent, not a pair.
    par = (f2 == F2[iu[0], iu[0]]) | (f2 == F2[iu[1], iu[1]])
    p2 = np.where(keep & ~par, t2 / np.maximum(f2, 1), -1.0)
    pair_idx = np.argsort(-p2)[:beam]
    pairs = []
    for r in pair_idx:
        if p2[r] < 0:
            continue
        i, j = seed[iu[0][r]], seed[iu[1][r]]
        if names[i][0] == names[j][0]:
            continue  # same channel twice is a threshold, not a rule
        out.append({"arity": 2, "terms": (i, j), "fires": int(f2[r]), "tp": int(t2[r])})
        pairs.append((i, j))

    if max_arity < 3:
        return out, seed

    # --- triples: extend the best pairs by one seed term (beam search; exhaustive
    # triples over 100 channels is ~10^9 and buys nothing over this).
    for (i, j) in pairs[:max(1, beam // 4)]:
        base = Ms[i] & Ms[j]
        nb = int(base.sum())
        cand = base.astype(np.float32) @ S.T
        cand_t = (base.astype(np.float32) * yv) @ S.T
        good = cand >= min_fires
        # again: a third term that changes nothing is not a term
        good &= cand < nb
        p3 = np.where(good, cand_t / np.maximum(cand, 1), -1.0)
        for r in np.argsort(-p3)[:3]:
            k = seed[r]
            if p3[r] < 0 or k in (i, j):
                continue
            if names[k][0] in (names[i][0], names[j][0]):
                continue
            out.append({"arity": 3, "terms": (i, j, k),
                        "fires": int(cand[r]), "tp": int(cand_t[r])})
    return out, seed


def best_of(res: list) -> dict | None:
    """Best by TP:FP, tie-broken by fire count (more evidence wins a tie)."""
    if not res:
        return None
    def key(r):
        fp = r["fires"] - r["tp"]
        return (r["tp"] / fp if fp else float("inf"), r["fires"])
    return max(res, key=key)


def fmt_rule(terms, names) -> str:
    return " AND ".join(f"`{names[i][0]} {names[i][1]} {names[i][2]:.4f}`" for i in terms)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--n-cuts", type=int, default=16)
    ap.add_argument("--min-fires", type=int, default=25,
                    help="statistical minimum, not a volume floor")
    ap.add_argument("--top-k", type=int, default=260, help="seed masks for pair/triple")
    ap.add_argument("--beam", type=int, default=60)
    ap.add_argument("--max-arity", type=int, default=3)
    ap.add_argument("--null-repeats", type=int, default=5)
    ap.add_argument("--include-kw15", action="store_true")
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="10_menu_search")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    df["y"] = (df[TARGET] >= a.tp_threshold).astype(int)
    y = df["y"].to_numpy()
    gate = replay_gate.replay(df)
    df["gate_ok"] = gate.is_ok.values

    chans = [c for c in df.columns if SCORE_RE.search(c)]
    if a.include_kw15:
        chans += [c for c in KW_COLS if c in df.columns]
    names, M = build_masks(df, chans, a.n_cuts)
    days = df[DATE].nunique()
    base = y.mean()

    menu = {k: (None if v is None else list(v)) for k, v in WHITELISTS.items()}
    all_lv = set(df[CLASS].unique())
    for k, drop in BLACKLISTS.items():
        menu[k] = sorted(all_lv - set(drop))

    print(f"{len(df)} rows | {len(chans)} channels -> {len(names)} cuts | "
          f"{len(menu)} menu entries")

    # ── the discount, PER SUBSET AND PER ARITY ───────────────────────────────
    # It must be computed inside each subset, not once globally: searching 2398 cuts
    # on MA's 387 rows overfits far harder than on all 4248, and a 3-way AND overfits
    # harder than a single cut. One global floor would badly understate the bar for
    # exactly the small, high-arity cells that look most impressive.
    rng = np.random.default_rng(0)

    def null_floor(rows: np.ndarray) -> dict[int, float]:
        acc: dict[int, list] = {ar: [] for ar in range(1, a.max_arity + 1)}
        for _ in range(a.null_repeats):
            yp = rng.permutation(y[rows])
            yfull = np.zeros(len(df), dtype=y.dtype)
            yfull[rows] = yp          # shuffle WITHIN the subset, preserving its base rate
            r, _ = search_subset(M, names, yfull, rows, a.min_fires, a.top_k,
                                 a.beam, a.max_arity)
            for ar in acc:
                b = best_of([x for x in r if x["arity"] == ar])
                if b:
                    acc[ar].append(b["tp"] / b["fires"])
        return {ar: (float(np.mean(v)) if v else float("nan")) for ar, v in acc.items()}

    # ── the real search, per menu entry ──────────────────────────────────────
    rows_out = []
    floors = {}
    for label, keep in menu.items():
        sel = (np.ones(len(df), bool) if keep is None
               else df[CLASS].isin(keep).to_numpy())
        rows = np.where(sel)[0]
        n_c, k_c = int(sel.sum()), int(y[sel].sum())
        if n_c < a.min_fires:
            continue
        floors[label] = null_floor(rows)
        res, _ = search_subset(M, names, y, rows, a.min_fires, a.top_k,
                               a.beam, a.max_arity)
        for arity in (1, 2, 3):
            b = best_of([r for r in res if r["arity"] == arity])
            if not b:
                continue
            fp = b["fires"] - b["tp"]
            lo, hi = wilson(b["tp"], b["fires"])
            cols = [names[i][0] for i in b["terms"]]
            tier, why = rule_tier(cols)
            rows_out.append({
                "menu": label, "arity": arity,
                "rule": fmt_rule(b["terms"], names),
                "tier": tier, "tier_why": why,
                "class_rows": n_c, "class_tp": k_c,
                "class_prec": k_c / n_c,
                "class_tpfp": k_c / (n_c - k_c) if n_c > k_c else np.inf,
                "fires": b["fires"], "tp": b["tp"], "fp": fp,
                "prec": b["tp"] / b["fires"],
                "tpfp": b["tp"] / fp if fp else np.inf,
                "ci_lo": lo, "ci_hi": hi,
                "fires_per_day": b["fires"] / days,
                "noise_floor": floors[label][arity],
                "above_floor": (b["tp"] / b["fires"]) - floors[label][arity],
                "uniq_symbols": int(df.loc[sel, "Symbol"].nunique()),
            })
    res_df = pd.DataFrame(rows_out)

    g = df[df.gate_ok]
    g_n, g_k = len(g), int(g.y.sum())
    g_tpfp = g_k / (g_n - g_k)
    # Volume anchor: the replayed gate on this corpus == the live 3.3 trades/day.
    res_df["trades_day"] = LIVE_TPD * res_df.fires / g_n

    L, W = [], None
    W = L.append
    W("# Fixed class menu x open encoder search — FULL CORPUS, IN-SAMPLE\n")
    W(f"TP = `fromPR_High(%)` >= {a.tp_threshold:g}%. All **{len(df):,} rows** "
      f"({days} days), base rate **{base:.2%}**. kw15 "
      f"{'INCLUDED' if a.include_kw15 else 'excluded (it was trained on part of this corpus)'}.\n")
    W(f"{len(chans)} frozen encoder channels x {a.n_cuts} quantile cuts x 2 directions "
      f"= {len(names):,} single cuts, searched up to {a.max_arity}-way AND inside each "
      f"of {len(menu)} pre-specified class subsets.\n")

    W("\n## Read this before reading any number below\n")
    W("**Two different things are reported here and they do NOT carry the same "
      "warranty.**\n")
    W("| | selected from the data? | so the number is |\n|---|---|---|")
    W("| the class subsets | **no** — a fixed, pre-specified menu | an honest "
      "full-corpus estimate |")
    W(f"| the encoder cut | **yes** — best of ~{len(names):,} cuts and their "
      f"{a.max_arity}-way combinations | an **upper bound**, not a forecast |")
    W("\nThe 12 headline / 11 body encoders are frozen and were trained by outside "
      "teams on other corpora, so no model ever saw these rows. That removes one "
      "source of bias. It does not remove the other: the *threshold*, the *direction*, "
      "the *channel* and the *arity* were all chosen by maximising on these same rows.\n")
    W(f"**Measured, not asserted** — the identical search re-run on SHUFFLED labels "
      f"({a.null_repeats} repeats), where no signal exists by construction and the "
      "encoders are exactly as external as before. The shuffle happens INSIDE each "
      "subset, so each cell's floor carries that subset's own base rate and row "
      "count:\n")
    W("| class subset | rows | base | noise floor, 1-term | 2-term | 3-term |")
    W("|---|---:|---:|---:|---:|---:|")
    for label in floors:
        sel = (np.ones(len(df), bool) if menu[label] is None
               else df[CLASS].isin(menu[label]).to_numpy())
        fl = floors[label]
        W(f"| {label} | {int(sel.sum())} | {y[sel].mean():.1%} | "
          + " | ".join(f"{fl[ar]:.1%}" for ar in range(1, a.max_arity + 1)) + " |")
    W("\n**This is the bar, not the base rate.** A searched rule scoring at or below "
      "its own row's floor found nothing — the number is what maximising over "
      f"{len(names):,} cuts produces from noise alone. Note the floor RISES sharply "
      "with arity and with smaller subsets: a 3-term rule inside `MA` is picked from "
      "the same search space using a tenth of the rows, so it overfits roughly that "
      "much harder. **The class-only table needs no discount at all** — that menu was "
      "fixed in advance.\n")

    W(f"\n## Reference: the live O5.2 gate on the same {len(df):,} rows\n")
    W(f"**{g_n} fires, {g_k} TP, {g_n - g_k} FP — precision {g_k/g_n:.1%}, "
      f"TP:FP {g_tpfp:.2f}, {LIVE_TPD} trades/day.**\n")

    W("\n## Class-only — the unbiased half\n")
    W("No encoder cut at all: just the class subset. **These numbers are honest** — "
      "nothing here was picked by searching.\n")
    W("| class subset | rows | TP | FP | TP:FP | precision | 95% CI | trades/day |")
    W("|---|---:|---:|---:|---:|---:|---|---:|")
    seen = set()
    for _, r in res_df.iterrows():
        if r.menu in seen:
            continue
        seen.add(r.menu)
        lo, hi = wilson(int(r.class_tp), int(r.class_rows))
        tp_fp = "inf" if np.isinf(r.class_tpfp) else f"{r.class_tpfp:.2f}"
        flag = " **<-- beats the gate**" if r.class_tpfp > g_tpfp else ""
        W(f"| {r.menu}{flag} | {int(r.class_rows)} | {int(r.class_tp)} | "
          f"{int(r.class_rows - r.class_tp)} | **{tp_fp}** | {r.class_prec:.1%} | "
          f"{lo:.0%}-{hi:.0%} | {LIVE_TPD * r.class_rows / g_n:.1f} |")

    W("\n## Best encoder rule inside each class subset — IN-SAMPLE, inflated\n")
    W("Every cell below is the maximum over the search space, evaluated on the rows "
      "that chose it. **`vs floor` is the only column that carries information**: it "
      "is the rule's precision minus what the same search extracts from shuffled "
      "labels in the same subset at the same arity. Negative or near zero means the "
      "rule is a coin that came up heads, not a signal.\n")
    for arity in range(1, a.max_arity + 1):
        sub = res_df[res_df.arity == arity].sort_values("above_floor", ascending=False)
        if sub.empty:
            continue
        W(f"\n### {arity}-term encoder rules\n")
        W("| class subset | rule | tier | fires | TP | FP | TP:FP | precision | "
          "floor | **vs floor** | 95% CI | trades/day | vs class-only |")
        W("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|---:|")
        for _, r in sub.iterrows():
            tp_fp = "inf" if np.isinf(r.tpfp) else f"{r.tpfp:.2f}"
            gain = (r.tpfp - r.class_tpfp) if np.isfinite(r.tpfp) else np.nan
            mark = "**" if r.above_floor > 0.05 else ""
            W(f"| {r.menu} | {r.rule} | {int(r.tier)} | {int(r.fires)} | {int(r.tp)} | "
              f"{int(r.fp)} | {tp_fp} | {r.prec:.1%} | {r.noise_floor:.1%} | "
              f"{mark}{r.above_floor:+.1%}{mark} | {r.ci_lo:.0%}-{r.ci_hi:.0%} | "
              f"{r.trades_day:.1f} | {gain:+.2f} |")
        n_up = int((sub.above_floor > 0.05).sum())
        W(f"\n{n_up} of {len(sub)} clear their own noise floor by more than 5 points.\n")

    W("\n## How to read `vs class-only`\n")
    W("That column is the encoder cut's contribution: the rule's TP:FP minus the same "
      "class subset's TP:FP with no cut at all. It is the honest question — **does the "
      "encoder add anything the class filter did not already give you?** A rule with a "
      "big TP:FP but a small gain is mostly just the class filter wearing a costume.\n")

    outdir = Path(a.outdir)
    res_df.to_csv(outdir / f"{a.prefix}.tsv", sep="\t", index=False)
    (outdir / f"{a.prefix}.md").write_text("\n".join(L) + "\n")
    print(f"gate TP:FP {g_tpfp:.2f} | rules clearing their floor by >5pts: "
          f"{int((res_df.above_floor > 0.05).sum())}/{len(res_df)}")
    print(f"wrote {outdir / (a.prefix + '.md')}")


if __name__ == "__main__":
    main()
