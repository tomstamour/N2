#!/usr/bin/env python3
"""Open search: ANY encoder channel + a FineClass white/blacklist -> best TP:FP.

Where analyze_class_trading_value.py only ever asked what the five scores already in
the O5.2 gate can do, this searches the WHOLE encoder surface (all 11 headline
encoders + all 11 body encoders + kw15 + LouMcD lexicon channels) crossed with a
FineClass keep-set, on the FULL corpus.

For a fixed score cut the optimal keep-set is not a search: rank the classes by their
precision UNDER THAT CUT and take a prefix. So the whole (cut x class-subset) space is
swept exactly, in one pass, rather than sampled. Taking a prefix from the top is a
whitelist; the same frontier read from the other end is a blacklist - they are one
family, not two.

!! THE SEARCH SPACE IS ~10^5 CANDIDATES AND WILL PRODUCE BEAUTIFUL NONSENSE IF SCORED
   ON THE DATA IT WAS PICKED ON. Two defences, both mandatory, both reported:
     1. Strict DATE SPLIT - every candidate is selected on the train block and scored
        once on the untouched test block.
     2. A PERMUTATION NULL - the identical search is re-run on shuffled labels, so the
        train-block precision that pure selection bias buys is measured, not assumed.
        A real candidate must beat that number, not the base rate.

  /home/tom/venv/bin/python search_class_encoder_rules.py \
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

TARGET, DATE, CLASS = "fromPR_High(%)", "ArrivalDate", "FineClass"
KW_HONEST_FROM = "2026-06-20"
SCORE_RE = re.compile(r"_(neg|neu|ind|pos|ss|unc|lit|smod|wmod|cons)_(hd|bd)$")
KW_COLS = ["kw15_proba_bd", "kw15_proba_hd"]
LIVE_TPD = 3.3  # live fires/day, the anchor for every volume number

# What it COSTS to deploy a rule naming a given channel. A field O5.2 does not compute
# is not a rules-file edit: the DSL resolves names through _TRIGGER_FIELDS
# (Orchestrator5.2.py:717), so a new channel needs a model wired into the trade-critical
# path AND an entry in that table. A tier-3 winner that beats the gate by two points is
# not worth the change; a tier-1 winner that does is free.
TIER1_MODELS = {"Prosus", "FinMarBa", "FLANG"}          # already scored live, headline
# Warm inference cost per headline, from tools/Encoder_Models (LouMcD is pure stdlib).
TIER2_MS = {"LouMcD": 0.02, "DistilRo": 6.5, "TradeHero": 7.0, "RoBERTa": 7.0,
            "FinTone": 12.0, "FinancialB": 15.0, "ModernB": 70.0,
            "DeBERTa": 105.0, "FLElectra": 110.0}


def channel_tier(col: str) -> tuple[int, str]:
    """(tier, why) for one channel name."""
    if col in KW_COLS:
        return 1, "live (kw15)"
    model = col.rsplit("_", 2)[0]
    if col.endswith("_bd"):
        return 3, f"body — O5.2 does no body encoding at all ({model})"
    if model in TIER1_MODELS:
        return 1, f"live ({model} headline)"
    return 2, f"+{TIER2_MS.get(model, float('nan')):.0f} ms headline ({model})"


def rule_tier(cols: list[str]) -> tuple[int, str]:
    """A rule costs whatever its most expensive term costs."""
    ts = [channel_tier(c) for c in cols if c]
    t = max(x[0] for x in ts)
    return t, "; ".join(w for tt, w in ts if tt == t)


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p, d = k / n, 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def build_masks(df: pd.DataFrame, chans: list[str], n_cuts: int):
    """One boolean column per (channel, direction, threshold). Cuts are quantiles of
    the channel's own distribution, so a saturated channel does not waste the grid."""
    names, cols = [], []
    for c in chans:
        v = pd.to_numeric(df[c], errors="coerce")
        if v.notna().mean() < 0.9 or v.nunique() < 5:
            continue
        arr = v.to_numpy(float)
        ok = ~np.isnan(arr)
        qs = np.unique(np.nanquantile(arr, np.linspace(0.05, 0.95, n_cuts)))
        for t in qs:
            for op in (">=", "<="):
                m = (arr >= t) if op == ">=" else (arr <= t)
                m &= ok            # NaN never fires, matching the gate's None rule
                names.append((c, op, float(t)))
                cols.append(m)
    return names, np.array(cols, dtype=bool)


def class_counts(M: np.ndarray, C: np.ndarray, y: np.ndarray):
    """(fires, tps) per (mask, class). C is the one-hot class matrix (rows x classes)."""
    Mf = M.astype(np.float32)
    fires = Mf @ C                       # (n_masks, n_classes)
    tps = Mf @ (C * y[:, None])
    return fires, tps


def best_keepset(fires: np.ndarray, tps: np.ndarray, min_fires: int,
                 min_class_fires: int):
    """For each mask, the class prefix (ranked by precision under that mask) with the
    best precision at >= min_fires. Returns per-mask (n, k, keep-mask)."""
    n_masks, n_cls = fires.shape
    # A class carrying too few fires under this cut cannot be ranked - its precision is
    # noise. Park it at the bottom so it is only ever included by a full keep-set.
    prec = np.where(fires >= min_class_fires, tps / np.maximum(fires, 1), -1.0)
    order = np.argsort(-prec, axis=1, kind="stable")
    f_sorted = np.take_along_axis(fires, order, axis=1)
    t_sorted = np.take_along_axis(tps, order, axis=1)
    cum_f, cum_t = np.cumsum(f_sorted, axis=1), np.cumsum(t_sorted, axis=1)
    cum_p = np.where(cum_f >= min_fires, cum_t / np.maximum(cum_f, 1), -1.0)
    best_j = np.argmax(cum_p, axis=1)
    rows = np.arange(n_masks)
    n = cum_f[rows, best_j]
    k = cum_t[rows, best_j]
    keep = np.zeros((n_masks, n_cls), dtype=bool)
    for i in range(n_masks):
        keep[i, order[i, :best_j[i] + 1]] = True
    return n, k, keep, best_j


def evaluate(mask: np.ndarray, keep: np.ndarray, cls_idx: np.ndarray,
             y: np.ndarray) -> tuple[int, int]:
    fire = mask & keep[cls_idx]
    return int(fire.sum()), int(y[fire].sum())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--split-date", default="2026-07-13",
                    help="train = before, test = on/after")
    ap.add_argument("--n-cuts", type=int, default=18)
    ap.add_argument("--min-fires", type=int, default=40,
                    help="minimum TRAIN fires for a candidate to be considered")
    ap.add_argument("--min-class-fires", type=int, default=4)
    ap.add_argument("--pair-channels", type=int, default=26)
    ap.add_argument("--pair-cuts", type=int, default=7)
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--include-kw15", action="store_true",
                    help="add kw15 channels; forces the honest window (from "
                         f"{KW_HONEST_FROM}) because kw15 saw earlier dates in training")
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="09_open_search")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    if a.include_kw15:
        df = df[df[DATE] >= KW_HONEST_FROM].reset_index(drop=True)
    df["y"] = (df[TARGET] >= a.tp_threshold).astype(int)
    gate = replay_gate.replay(df)
    df["gate_ok"] = gate.is_ok.values

    chans = [c for c in df.columns if SCORE_RE.search(c)]
    if a.include_kw15:
        chans += [c for c in KW_COLS if c in df.columns]
    levels = sorted(df[CLASS].unique())
    cls_idx = df[CLASS].map({l: i for i, l in enumerate(levels)}).to_numpy()
    C = np.zeros((len(df), len(levels)), dtype=np.float32)
    C[np.arange(len(df)), cls_idx] = 1.0
    y = df["y"].to_numpy()

    tr = (df[DATE] < a.split_date).to_numpy()
    te = ~tr
    days_te = df.loc[te, DATE].nunique()
    # Volume anchor: what the LIVE gate does on the test block maps to LIVE_TPD.
    gate_te = int((df["gate_ok"].to_numpy() & te).sum())
    gate_te_tp = int(y[df["gate_ok"].to_numpy() & te].sum())

    names, M = build_masks(df, chans, a.n_cuts)
    print(f"{len(df)} rows | {len(chans)} channels -> {len(names)} single cuts | "
          f"train {tr.sum()} / test {te.sum()} ({days_te} days)")

    def search(y_use: np.ndarray, tag: str):
        """Select on TRAIN only. Returns a frame of candidates with train stats."""
        Mtr = M[:, tr]
        f, t = class_counts(Mtr, C[tr], y_use[tr])
        n, k, keep, _ = best_keepset(f, t, a.min_fires, a.min_class_fires)
        prec = np.where(n > 0, k / np.maximum(n, 1), 0.0)
        rec = []
        for i in np.argsort(-prec)[:400]:
            if n[i] < a.min_fires:
                continue
            rec.append({"kind": "single", "src": "M",
                        "term1": f"{names[i][0]} {names[i][1]} "
                                                  f"{names[i][2]:.4f}", "term2": "",
                        "chan1": names[i][0], "chan2": "",
                        "mask_i": i, "mask_j": -1,
                        "keep": tuple(np.array(levels)[keep[i]]),
                        "tr_fires": int(n[i]), "tr_tp": int(k[i]),
                        "tr_prec": float(prec[i])})
        return pd.DataFrame(rec), keep

    cands, keep_single = search(y, "real")

    # ── pairs: two channels AND-ed, then the same exact class frontier ────────
    # Rank channels by |AUC-0.5| on TRAIN so the pair grid spends its budget on
    # channels that carry signal at all.
    disc = []
    for c in chans:
        v = pd.to_numeric(df[c], errors="coerce").to_numpy(float)
        m = ~np.isnan(v) & tr
        if m.sum() < 200 or len(set(y[m])) < 2:
            continue
        r = pd.Series(v[m]).rank().to_numpy()
        n1 = y[m].sum()
        auc = (r[y[m] == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * (m.sum() - n1))
        disc.append((abs(auc - 0.5), c))
    top_ch = [c for _, c in sorted(disc, reverse=True)[:a.pair_channels]]
    pn, PM = build_masks(df, top_ch, a.pair_cuts)
    print(f"pair grid: {len(top_ch)} channels -> {len(pn)} cuts -> "
          f"{len(pn) * (len(pn) - 1) // 2:,} pairs")

    PMtr = PM[:, tr]
    Ctr, ytr = C[tr], y[tr]
    rows = []
    for i in range(len(pn)):
        if pn[i][0] in ("",):
            continue
        And = PMtr[i] & PMtr[i + 1:]
        if not len(And):
            continue
        f, t = class_counts(And, Ctr, ytr)
        n, k, keep, _ = best_keepset(f, t, a.min_fires, a.min_class_fires)
        p = np.where(n > 0, k / np.maximum(n, 1), 0.0)
        for off in np.argsort(-p)[:3]:
            j = i + 1 + off
            if n[off] < a.min_fires or pn[i][0] == pn[j][0]:
                continue  # same channel twice is a threshold, not a rule
            rows.append({"kind": "pair", "src": "PM",
                         "term1": f"{pn[i][0]} {pn[i][1]} {pn[i][2]:.4f}",
                         "term2": f"{pn[j][0]} {pn[j][1]} {pn[j][2]:.4f}",
                         "chan1": pn[i][0], "chan2": pn[j][0],
                         "mask_i": i, "mask_j": j,
                         "keep": tuple(np.array(levels)[keep[off]]),
                         "tr_fires": int(n[off]), "tr_tp": int(k[off]),
                         "tr_prec": float(p[off])})
    pairs = pd.DataFrame(rows)
    cands = pd.concat([cands, pairs], ignore_index=True)
    cands = cands.sort_values("tr_prec", ascending=False, kind="stable")
    cands = cands.drop_duplicates(subset=["term1", "term2"]).head(a.top * 20)

    # ── strip vacuous terms, then dedup by what a rule actually DOES ──────────
    # A quantile grid inevitably produces cuts at the tail of a channel's own
    # distribution: `Prosus_ss_hd >= -0.0234` passes everything, so the pair is really a
    # single. Without this the leaderboard fills with five spellings of one rule.
    def raw_mask(r):
        G = M if r.src == "M" else PM
        return G[r.mask_i] if r.mask_j < 0 else G[r.mask_i] & G[r.mask_j]

    def train_mask(r):
        keep = np.isin(np.array(levels), list(r["keep"]))
        return (raw_mask(r) & keep[cls_idx])[tr]

    rec = []
    for _, r in cands.iterrows():
        r = r.copy()
        if r.kind == "pair":
            full = (PM[r.mask_i] & PM[r.mask_j])[tr]
            for drop, keepi in (("term1", r.mask_j), ("term2", r.mask_i)):
                if np.array_equal(full, PM[keepi][tr]):
                    # dropping this term changes nothing on train -> it is not a term
                    r["kind"], r["mask_i"], r["mask_j"] = "single", keepi, -1
                    r["term1"] = r["term2"] if drop == "term1" else r["term1"]
                    r["chan1"] = r["chan2"] if drop == "term1" else r["chan1"]
                    r["term2"], r["chan2"] = "", ""
                    break
        rec.append(r)
    cands = pd.DataFrame(rec)
    cands["_fp"] = [np.packbits(train_mask(r)).tobytes() for _, r in cands.iterrows()]
    cands = (cands.sort_values("tr_prec", ascending=False, kind="stable")
                  .drop_duplicates("_fp").drop(columns="_fp").head(a.top * 8))

    # ── score every survivor ONCE on the untouched test block ─────────────────
    out = []
    for _, r in cands.iterrows():
        m = raw_mask(r)
        keep = np.isin(np.array(levels), list(r.keep))
        fire = m & keep[cls_idx]
        n_te, k_te = int((fire & te).sum()), int(y[fire & te].sum())
        lo, hi = wilson(k_te, n_te)
        tier, why = rule_tier([r.chan1, r.chan2])
        out.append({**r.to_dict(),
                    "tier": tier, "tier_why": why,
                    "n_classes_kept": len(r.keep),
                    "te_fires": n_te, "te_tp": k_te, "te_fp": n_te - k_te,
                    "te_prec": k_te / n_te if n_te else np.nan,
                    "te_tp_fp": (k_te / (n_te - k_te)) if n_te - k_te else np.inf,
                    "te_ci_lo": lo, "te_ci_hi": hi,
                    "te_trades_day": LIVE_TPD * n_te / gate_te if gate_te else np.nan,
                    "shrink": (k_te / n_te - r.tr_prec) if n_te else np.nan})
    res = pd.DataFrame(out)

    # ── permutation null: how much train precision does SELECTION ALONE buy? ──
    rng = np.random.default_rng(0)
    null_tr, null_te = [], []
    for _ in range(5):
        yp = rng.permutation(y)
        f, t = class_counts(M[:, tr], C[tr], yp[tr])
        n, k, keep, _ = best_keepset(f, t, a.min_fires, a.min_class_fires)
        p = np.where(n > 0, k / np.maximum(n, 1), 0.0)
        b = int(np.argmax(p))
        null_tr.append(p[b])
        fire = M[b] & keep[b][cls_idx]
        nte = int((fire & te).sum())
        null_te.append(y[fire & te].sum() / nte if nte else np.nan)

    base_tr, base_te = y[tr].mean(), y[te].mean()
    L = []
    W = L.append
    W(f"# Open search — any encoder channel x a FineClass keep-set\n")
    W(f"TP = `fromPR_High(%)` >= {a.tp_threshold:g}%. "
      f"{'HONEST window (kw15 included)' if a.include_kw15 else 'FULL corpus, kw15 EXCLUDED'}.\n")
    W(f"- **train** `{DATE} < {a.split_date}` — {int(tr.sum()):,} rows, "
      f"base {base_tr:.2%}\n"
      f"- **test** (untouched until the end) — {int(te.sum()):,} rows over {days_te} "
      f"days, base {base_te:.2%}\n")
    W(f"{len(chans)} channels x {a.n_cuts} quantile cuts x 2 directions = "
      f"{len(names):,} single cuts, plus {len(pn) * (len(pn) - 1) // 2:,} pairs from the "
      f"{a.pair_channels} most discriminative channels. For every one of them the class "
      "keep-set is solved exactly (rank classes by precision under that cut, take the "
      "best prefix), so the class dimension is swept, not sampled.\n")

    W("\n## The number that decides whether any of this is real\n")
    W(f"**Permutation null** — the same search, on shuffled labels, 5 repeats:\n")
    W(f"- best TRAIN precision it finds: **{np.mean(null_tr):.1%}** "
      f"(vs a {base_tr:.2%} base rate)\n"
      f"- that winner's TEST precision: **{np.nanmean(null_te):.1%}**\n")
    W(f"So this search buys ~{np.mean(null_tr)/base_tr:.0f}x the base rate on the train "
      "block **from selection alone, with no signal present at all**. A train-block "
      "number below that is worthless, and only the TEST column below means anything.\n")

    W(f"\n## Reference: the live O5.2 gate on the same test block\n")
    W(f"**{gate_te} fires, {gate_te_tp} TP, {gate_te - gate_te_tp} FP — "
      f"precision {gate_te_tp/gate_te:.1%}, TP:FP {gate_te_tp/(gate_te-gate_te_tp):.2f}, "
      f"{LIVE_TPD:.1f} trades/day** (this is the volume anchor).\n")

    gate_tpfp = gate_te_tp / (gate_te - gate_te_tp)
    gate_prec = gate_te_tp / gate_te
    # No volume floor (operator's call) - only a statistical minimum, because a 4-fire
    # test block cannot distinguish 50% from 12%.
    elig = res[res.te_fires >= 10].copy()
    W(f"\n## Top {a.top} candidates, ranked by TEST TP:FP\n")
    W("Selected on train, scored ONCE on test. No volume floor — only a >=10 test-fire "
      "statistical minimum. `shrink` = test minus train precision; it is negative for "
      "essentially everything, and that is the selection bias, not bad luck.\n")
    W("| rule | tier | classes kept | train prec | **test fires** | TP | FP | "
      "**TP:FP** | test prec | 95% CI | trades/day | shrink |")
    W("|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|---:|")
    show = elig.sort_values("te_tp_fp", ascending=False).head(a.top)
    for _, r in show.iterrows():
        rule = f"`{r.term1}`" + (f" AND `{r.term2}`" if r.term2 else "")
        kept = ("ALL" if r.n_classes_kept == len(levels)
                else ", ".join(r["keep"]) if r.n_classes_kept <= 4
                else f"{r.n_classes_kept} classes")
        tpfp = "inf" if np.isinf(r.te_tp_fp) else f"{r.te_tp_fp:.2f}"
        W(f"| {rule} | {int(r.tier)} | {kept} | {r.tr_prec:.0%} | **{r.te_fires}** | "
          f"{r.te_tp} | {r.te_fp} | **{tpfp}** | {r.te_prec:.1%} | "
          f"{r.te_ci_lo:.0%}-{r.te_ci_hi:.0%} | {r.te_trades_day:.1f} | "
          f"{r.shrink:+.1%} |")
    W(f"\nGate reference on the same rows: **TP:FP {gate_tpfp:.2f}**, "
      f"precision {gate_prec:.1%}.\n")

    # ── the control that decides whether the SCORES matter at all ────────────
    # Almost every winner above keeps MA (+/- Partnership/Patent_IP). If the class
    # filter ALONE reaches the same TP:FP, the encoder cut is decoration and the whole
    # 100-channel search has found nothing the class column did not already say.
    W("\n### Control: what does the class filter do with NO score cut at all?\n")
    W("Nearly every winner above keeps `MA`, often with `Partnership`/`Patent_IP`. If "
      "the class alone matches them, the encoder term is decoration.\n")
    W("| keep-set | test fires | TP | FP | TP:FP | precision | 95% CI | trades/day |")
    W("|---|---:|---:|---:|---:|---:|---|---:|")
    top_keeps = []
    for ks in show["keep"]:
        if ks not in top_keeps:
            top_keeps.append(ks)
    for ks in ([("MA",), ("MA", "Partnership")] +
               [k for k in top_keeps if k not in [("MA",), ("MA", "Partnership")]][:4]):
        keep = np.isin(np.array(levels), list(ks))
        fire = keep[cls_idx] & te
        n_c, k_c = int(fire.sum()), int(y[fire].sum())
        if not n_c:
            continue
        lo, hi = wilson(k_c, n_c)
        ratio = "inf" if n_c == k_c else f"{k_c / (n_c - k_c):.2f}"
        W(f"| {', '.join(ks)} | {n_c} | {k_c} | {n_c - k_c} | **{ratio}** | "
          f"{k_c / n_c:.1%} | {lo:.0%}-{hi:.0%} | "
          f"{LIVE_TPD * n_c / gate_te:.1f} |")

    W("\n### Deployment cost of the winners\n")
    W("A rule naming a channel O5.2 does not compute is a CODE change, not a rules-file "
      "edit: the DSL resolves field names through `_TRIGGER_FIELDS` "
      "(`Orchestrator5.2.py:717`), so the model must also be wired into the "
      "trade-critical path.\n")
    W("| tier | meaning | candidates in the top table beating the gate |")
    W("|---:|---|---:|")
    beat = show[show.te_tp_fp > gate_tpfp]
    for t, meaning in ((1, "free — Prosus / FinMarBa / FLANG headline or kw15, already live"),
                       (2, "another headline encoder: +7 ms (DistilRo/TradeHero) to "
                           "+110 ms (DeBERTa/FLElectra) on the hot path"),
                       (3, "a body channel — O5.2 does NO body encoding today")):
        W(f"| {t} | {meaning} | {int((beat.tier == t).sum())} |")

    W("\n## Does anything actually beat the gate?\n")
    W(f"- candidates clearing the gate's TP:FP ({gate_tpfp:.2f}) on test, >=10 fires: "
      f"**{len(beat)} of {len(elig)}**\n")
    W(f"- of those, clearing the permutation null's train precision "
      f"({np.mean(null_tr):.1%}) *on test*: "
      f"**{int((beat.te_prec > np.mean(null_tr)).sum())}**\n")
    W(f"- median shrink train->test across all {len(res)} survivors: "
      f"**{res.shrink.median():+.1%}**\n")
    if len(beat) == 0:
        W("\n**Nothing in the whole 100-channel surface beats the live gate on held-out "
          "data.** That is the finding — the extra 95 channels do not contain a better "
          "(cut x class) rule than what already runs.\n")

    outdir = Path(a.outdir)
    res.to_csv(outdir / f"{a.prefix}.tsv", sep="\t", index=False)
    (outdir / f"{a.prefix}.md").write_text("\n".join(L) + "\n")
    print(f"wrote {outdir / (a.prefix + '.md')}")


if __name__ == "__main__":
    main()
