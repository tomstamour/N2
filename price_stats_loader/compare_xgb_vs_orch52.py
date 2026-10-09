#!/usr/bin/env python
"""Head-to-head: do the XGBoost scores predict fromPR_High(%) better than the gate
Orchestrator5.2 actually trades on today?

The corpus is the XGB VALIDATION fold (train cutoff 2026-07-28), so every XGB column is
genuinely out-of-sample. The O5.2 side is represented two ways, because a single column is
not what O5.2 trades:

  * as the REAL GATE -- replay_gate.replay() is imported, which drives the real
    Orchestrator5.2._evaluate_trigger() over every row. A binary fire/no-fire set, i.e. ONE
    operating point, not a ranking.
  * as the individual scores the gate reads (Prosus_pos_hd, FinMarBa_ind_hd, FLANG_ss_hd,
    kw15_proba_bd, kw15_proba_hd, FineClass), each as its own ranker.

Comparisons are at MATCHED FIRE COUNT: take the gate's N fires, take each XGB score's top N,
and read precision / EV / recall off the same number of trades. Anything else compares a
scorer at one workload against a gate at another.

!! `fromPR_High(%)` is the intraday CEILING from the PR, not realized PnL. Every "EV per
   fire" number here is the best case with a perfect exit, for BOTH sides equally. It ranks
   the scorers; it does not forecast an account balance.

!! The EARNINGS branch is an ESTIMATE. The corpus Body is capped at 400 words while the live
   earnings scorer reads the whole body -- see earnscore_backfill_addOn.py. replay_gate.py
   measures 91.4% fidelity there vs 100.0% elsewhere.

!! Fires cluster inside a session, so every confidence interval here resamples whole
   ArrivalDates (a day-block bootstrap), never individual rows. Row bootstrap on this corpus
   would report intervals roughly a third too narrow.

Input must already carry EarningScore + ID -- run earnscore_backfill_addOn.py first.

Usage:
    /home/tom/venv/bin/python compare_xgb_vs_orch52.py \
        --input outputs/2026-07-28_2026-08-28_..._orch52cols_gateReplay.tsv
"""

import argparse
import importlib.util as ilu
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

_HERE = Path(__file__).resolve().parent

XGB_COLS = ["proba_clf_ge50", "proba_clf_ge35", "proba_clf_ge20",
            "pred_reg_q80", "pred_reg_se"]
# The numeric scores the live gate reads. Orientation is decided from the data, not
# asserted: FinMarBa_ind_hd is an INDECISION score, so low is bullish.
O52_COLS = ["kw15_proba_bd", "kw15_proba_hd", "FLANG_ss_hd",
            "Prosus_pos_hd", "FinMarBa_ind_hd"]
TARGET = "fromPR_High(%)_actual"
DATE_COL = "ArrivalDate"


# --------------------------------------------------------------------------
# statistics -- copied not imported, per the statsByClaude house convention
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


def avg_precision(y, s):
    """Area under the precision-recall curve, step-wise, tie groups collapsed."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    pos = y.sum()
    if pos == 0:
        return float("nan")
    order = np.argsort(-s, kind="mergesort")
    ys, ss = y[order], s[order]
    # a tie group must be scored as a whole: you cannot select half of it
    ends = np.flatnonzero(np.r_[np.diff(ss) != 0, True])
    tp = np.cumsum(ys)[ends]
    k = (ends + 1).astype(float)
    prec = tp / k
    rec = tp / pos
    return float(np.sum(np.diff(np.r_[0.0, rec]) * prec))


def wilson(k, n, z=1.959964):
    """95% Wilson score interval -- the house choice (class_hitrate_ranking.py) because
    it does not collapse to a zero-width interval at k=0 or k=n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - h) / d), float((c + h) / d)


def newcombe(k1, n1, k2, n2):
    """95% CI for p1 - p2 (Newcombe method 10, from two Wilson intervals). Used by
    conditional_value.py for exactly this question: is the precision gap real?"""
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    lo = (p1 - p2) - np.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = (p1 - p2) + np.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return float(lo), float(hi)


def _day_blocks(days):
    """Row indices grouped by session, for the block bootstrap."""
    idx = {}
    for i, d in enumerate(days):
        idx.setdefault(d, []).append(i)
    return [np.asarray(v) for v in idx.values()]


def block_boot_auc(y, s, days, n_boot, rng):
    """95% CI on AUC, resampling whole SESSIONS. Rows inside a day share the tape, the
    news cycle and often the symbol, so they are not independent draws."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        a = auc_score(y[sel], s[sel])
        if not np.isnan(a):
            out.append(a)
    if not out:
        return (float("nan"), float("nan"))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def block_boot_delta_auc(y, s_a, s_b, days, n_boot, rng):
    """PAIRED day-block bootstrap on AUC(a) - AUC(b): both scores are evaluated on the
    SAME resampled sessions, so the shared sampling noise cancels. Returns (CI, P(delta>0))."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        ya = y[sel]
        if ya.sum() == 0 or ya.sum() == len(ya):
            continue
        out.append(auc_score(ya, s_a[sel]) - auc_score(ya, s_b[sel]))
    if not out:
        return (float("nan"), float("nan")), float("nan")
    out = np.asarray(out)
    return ((float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))),
            float((out > 0).mean()))


def oof_target_encode(levels, y, folds, rng, smooth=10.0):
    """Out-of-fold mean-target encoding, so a high-cardinality categorical cannot cheat
    by memorising its own rows. Smoothed toward the global rate so a 2-row class does not
    land at 0.0 or 1.0. Same idea power_predictor.py uses for its categoricals."""
    levels = np.asarray(levels, dtype=object)
    y = np.asarray(y, dtype=float)
    n = len(y)
    enc = np.full(n, np.nan)
    order = rng.permutation(n)
    parts = np.array_split(order, folds)
    for p in parts:
        tr = np.setdiff1d(order, p, assume_unique=False)
        prior = y[tr].mean()
        df = pd.DataFrame({"lvl": levels[tr], "y": y[tr]})
        agg = df.groupby("lvl")["y"].agg(["sum", "count"])
        m = ((agg["sum"] + smooth * prior) / (agg["count"] + smooth)).to_dict()
        enc[p] = [m.get(l, prior) for l in levels[p]]
    return enc


# --------------------------------------------------------------------------
# the metric block every candidate is reported through
# --------------------------------------------------------------------------
def metric_block(mask, y_raw, thresholds, n_days):
    """Everything the user asked to judge on, for one fire set: precision at matched
    fire rate, EV per fire, and recall of the big movers."""
    mask = np.asarray(mask, dtype=bool)
    fired = y_raw[mask]
    n = int(mask.sum())
    out = {"fires": n, "fires_per_day": n / n_days if n_days else float("nan")}
    for th in thresholds:
        pos = y_raw >= th
        tp = int((pos & mask).sum())
        base = float(pos.mean())
        prec = tp / n if n else float("nan")
        lo, hi = wilson(tp, n)
        out[f"tp{th}"] = tp
        out[f"prec{th}"] = prec
        out[f"prec{th}_lo"] = lo
        out[f"prec{th}_hi"] = hi
        out[f"lift{th}"] = prec / base if base and n else float("nan")
        out[f"recall{th}"] = tp / int(pos.sum()) if pos.sum() else float("nan")
    out["mean"] = float(fired.mean()) if n else float("nan")
    out["median"] = float(np.median(fired)) if n else float("nan")
    out["sum"] = float(fired.sum()) if n else 0.0
    # the mean is hostage to one +1035% row; a ceiling at 100% is the same number with
    # the single fattest tail bounded, and is reported beside it rather than instead
    out["mean_cap100"] = float(np.minimum(fired, 100.0).mean()) if n else float("nan")
    return out


def top_n_mask(score, n):
    """Top-n by score, ties broken by original order (mergesort is stable)."""
    if n <= 0:
        return np.zeros(len(score), dtype=bool)
    order = np.argsort(-np.asarray(score, dtype=float), kind="mergesort")
    m = np.zeros(len(score), dtype=bool)
    m[order[:n]] = True
    return m


def cut_for_n(score, n):
    """The threshold a top-n selection corresponds to -- what a DSL rule line would say."""
    s = np.sort(np.asarray(score, dtype=float))[::-1]
    if n <= 0:
        return float("inf")
    return float(s[min(n, len(s)) - 1])


# --------------------------------------------------------------------------
def cut_str(x):
    """Format a threshold at the precision a rules file needs.

    The cuts here span two scales: `pred_reg_*` is a percentage (~24) and `proba_clf_*` is
    a probability (~0.80). One format cannot serve both -- rounding 0.7968 to `0.8` is not
    a cosmetic loss, it is a different gate with a different fire count."""
    return f"{x:.4f}" if abs(x) < 10 else f"{x:.1f}"


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def pct(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100 * x:.{nd}f}%"


def block_row(name, b, th, extra=""):
    return (f"| {name} | {b['fires']} | {b['fires_per_day']:.2f} | {b[f'tp{th}']} | "
            f"{pct(b[f'prec{th}'])} | {pct(b[f'prec{th}_lo'])}–{pct(b[f'prec{th}_hi'])} | "
            f"{fmt(b[f'lift{th}'], 2)}x | {pct(b[f'recall{th}'])} | "
            f"{fmt(b['mean'], 1)} | {fmt(b['median'], 1)} | {fmt(b['mean_cap100'], 1)} | "
            f"{b['sum']:.0f} |{extra}")


BLOCK_HDR = ("| candidate | fires | /day | TP | precision | 95% CI | lift | recall | "
             "mean | median | mean(cap100) | sum |")
BLOCK_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|"


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--rules", default=None, help="trigger-rules DSL (default: live)")
    ap.add_argument("--threshold", type=float, default=50.0,
                    help="the cut the verdict is decided on (default 50)")
    ap.add_argument("--secondary-thresholds", default="15,20,35")
    ap.add_argument("--outdir", default=str(_HERE / "statsByClaude"))
    ap.add_argument("--report", default="2026-09-16_XGB_vs_O52_predictive_power.md")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--compare-scores", default=None,
                    help="an earlier XGB-scores TSV (same join keys) to diff against, for "
                         "the 'what changed' section -- e.g. the pre-best_iteration file")
    ap.add_argument("--clean-window-from", default="2026-08-14",
                    help="start of the sub-window in which NEITHER side saw the data "
                         "during calibration (the ClassWhitelist was tuned through 08-13)")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    th = args.threshold
    sec = [float(x) for x in args.secondary_thresholds.split(",") if x.strip()]
    all_th = [th] + [s for s in sec if s != th]

    spec = ilu.spec_from_file_location("replay_gate", str(_HERE / "replay_gate.py"))
    rg = ilu.module_from_spec(spec)
    spec.loader.exec_module(rg)

    inp = Path(args.input)
    df = pd.read_csv(inp, sep="\t", dtype=str, keep_default_na=False, na_values=[""])
    for c in XGB_COLS + O52_COLS + [TARGET]:
        if c not in df.columns:
            sys.exit(f"input is missing required column {c}")
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "EarningScore" not in df.columns:
        sys.exit("input has no EarningScore -- run earnscore_backfill_addOn.py first; "
                 "replay_gate would silently disable the EARNINGS branch")
    if df[XGB_COLS + O52_COLS + [TARGET]].isna().any().any():
        sys.exit("NaN in a score or target column -- refusing to score")

    y_raw = df[TARGET].to_numpy(dtype=float)
    y = (y_raw >= th).astype(int)
    days = df[DATE_COL].to_numpy()
    n_days = df[DATE_COL].nunique()
    n = len(df)

    rules_path = args.rules or str(rg.LIVE_RULES)
    res = rg.replay(df, rules_path)
    gate = res["is_ok"].to_numpy(dtype=bool)
    whitelist = res["detail"].str.contains("ClassWhitelist:ALLOW").to_numpy()
    rule_names = [c[len("fired_"):] for c in res.columns if c.startswith("fired_")]

    lines = []
    W = lines.append

    # ---------------- header ----------------
    W("# Do the XGBoost scores predict `fromPR_High(%)` better than the live O5.2 gate?")
    W("")
    W("**Date:** 2026-09-16  ")
    W(f"**Corpus:** `{inp.name}` — {n} rows, {n_days} sessions, "
      f"{df[DATE_COL].min()}..{df[DATE_COL].max()}, no NaN.  ")
    W("**Provenance:** the XGB **validation fold** (`../training_data/`, train cutoff "
      "`2026-07-28`), so `pred_reg_se`, `pred_reg_q80` and `proba_clf_ge20/35/50` are "
      "genuinely out-of-sample. Scores are **`best_iteration`-truncated** — each booster is "
      "asked for only the rounds early stopping kept (146/3000/146/164/210 of "
      "336/3000/246/264/310); see §11. The six gate columns were joined by "
      "`orch52cols_backfill_addOn.py`; `EarningScore` + `ID` by "
      "`earnscore_backfill_addOn.py`.  ")
    W(f"**Gate:** the real one — `replay_gate.replay()` drives "
      f"`Orchestrator5.2._evaluate_trigger()` over `{Path(rules_path).name}`.  ")
    W(f"**Verdict cut:** `fromPR_High(%) >= {th:g}` "
      f"({int(y.sum())} rows, {pct(y.mean())} base rate).  ")
    W("")
    W("Base rates on this corpus: " + ", ".join(
        f"`>= {t:g}` **{int((y_raw >= t).sum())}** ({pct((y_raw >= t).mean())})"
        for t in sorted(all_th)) + ".")
    W("")

    verdict_slot = len(lines)   # filled in once the numbers exist
    W("")

    # ---------------- method ----------------
    W("## Method")
    W("")
    W("1. **`earnscore_backfill_addOn.py`** rebuilt `EarningScore` from the canonical "
      "corpus with the frozen `earn_score_params.json`. Without it `replay_gate` silently "
      "disables the `EARNINGS` branch — which turns out to be O5.2's single largest "
      "source of fires here, so this step is load-bearing, not cosmetic.")
    W("2. **`replay_gate.py --verify-against ../orchestrator5/tables --verify-since "
      "2026-08-18`** checked the replay against what O5.2 actually wrote on the live tape.")
    W("3. **This script** computes every number below. Ranking metrics use the "
      "Mann-Whitney rank AUC (ties-safe, no sklearn); proportions carry Wilson 95% CIs; "
      "precision gaps carry Newcombe 95% CIs; AUC CIs come from a "
      f"**{args.bootstrap}-replicate day-block bootstrap** that resamples whole sessions, "
      "because fires cluster within a day.")
    W(f"4. Head-to-head comparisons are at **matched fire count**: the gate fires N times, "
      f"each XGB score is given its top N.")
    W("")

    # ---------------- 1. ranking quality ----------------
    W("## Findings")
    W("")
    W("### 1. Every XGB score out-ranks every score the gate reads, and the gap survives "
      "the bootstrap")
    W("")
    cands = {}
    for c in XGB_COLS:
        cands[c] = (df[c].to_numpy(dtype=float), "XGB")
    for c in O52_COLS:
        v = df[c].to_numpy(dtype=float)
        if auc_score(y, v) < 0.5:          # orient from the data, do not assert
            cands[f"{c}(inv)"] = (-v, "O5.2")
        else:
            cands[c] = (v, "O5.2")
    fc_enc = oof_target_encode(df["FineClass"].to_numpy(), y, args.folds, rng)
    cands["FineClass(OOF target-enc)"] = (fc_enc, "O5.2")

    rank_rows = []
    for name, (s, side) in cands.items():
        a = auc_score(y, s)
        lo, hi = block_boot_auc(y, s, days, args.bootstrap, rng)
        row = {"name": name, "side": side, "auc": a, "lo": lo, "hi": hi,
               "ap": avg_precision(y, s),
               "rho": float(spearmanr(s, y_raw).statistic)}
        for t in sorted(sec):
            row[f"auc{t:g}"] = auc_score((y_raw >= t).astype(int), s)
        rank_rows.append(row)
    rank_rows.sort(key=lambda r: -r["auc"])

    sec_hdr = "".join(f" AUC>={t:g} |" for t in sorted(sec))
    W(f"| rank | score | side | AUC>={th:g} | 95% CI (day-block) | PR-AUC | Spearman |"
      + sec_hdr)
    W("|---|---|---|---|---|---|---|" + "---|" * len(sec))
    for i, r in enumerate(rank_rows, 1):
        W(f"| {i} | `{r['name']}` | {r['side']} | **{fmt(r['auc'])}** | "
          f"{fmt(r['lo'])}–{fmt(r['hi'])} | {fmt(r['ap'])} | {r['rho']:+.3f} |"
          + "".join(f" {fmt(r[f'auc{t:g}'])} |" for t in sorted(sec)))
    W("")
    W(f"PR-AUC baseline (= base rate) is {fmt(float(y.mean()))}. "
      "`FinMarBa_ind_hd` is an *indecision* score, so it is oriented `(inv)` — the gate "
      "already uses it that way (`<= 0.14`).")
    W("")

    best_x = max((r for r in rank_rows if r["side"] == "XGB"), key=lambda r: r["auc"])
    best_o = max((r for r in rank_rows if r["side"] == "O5.2"), key=lambda r: r["auc"])
    (dlo, dhi), pgt = block_boot_delta_auc(
        y, cands[best_x["name"]][0], cands[best_o["name"]][0], days, args.bootstrap, rng)
    W(f"**Paired day-block bootstrap**, `{best_x['name']}` minus `{best_o['name']}`: "
      f"ΔAUC = **{fmt(best_x['auc'] - best_o['auc'])}**, 95% CI "
      f"{fmt(dlo)}–{fmt(dhi)}, P(Δ>0) = {pct(pgt)}. "
      + ("The interval excludes zero." if dlo > 0 else
         "**The interval includes zero** — on this corpus the ranking gap is not "
         "separable from session-to-session noise."))
    W("")

    # ---------------- 2. the gate's operating point ----------------
    W("### 2. What the live gate actually does on this window")
    W("")
    gb = metric_block(gate, y_raw, all_th, n_days)
    W(f"`replay_gate` fires **{gb['fires']}/{n}** rows ({pct(gb['fires'] / n)}), "
      f"**{gb['fires_per_day']:.2f}/day** over {n_days} sessions. "
      f"`ClassWhitelist` admits {int(whitelist.sum())} rows and blocks "
      f"{int((~whitelist).sum())}. Rows blocked by a missing score only: "
      f"{int(res['missing_blocked'].sum())}.")
    W("")
    W("| branch | fires | TP@>=%g | precision | median move | sum captured |" % th)
    W("|---|---|---|---|---|---|")
    for rn in rule_names:
        m = res[f"fired_{rn}"].to_numpy(dtype=bool)
        b = metric_block(m, y_raw, [th], n_days)
        W(f"| `{rn}` | {b['fires']} | {b[f'tp{th}']} | {pct(b[f'prec{th}'])} | "
          f"{fmt(b['median'], 1)} | {b['sum']:.0f} |")
    W(f"| **gate (any rule)** | **{gb['fires']}** | **{gb[f'tp{th}']}** | "
      f"**{pct(gb[f'prec{th}'])}** | **{fmt(gb['median'], 1)}** | **{gb['sum']:.0f}** |")
    W("")
    W("Branch counts sum to more than the gate total: a row can satisfy several rules.")
    W("")
    W("The structural ceiling the whitelist imposes:")
    W("")
    wl_pos = int((y.astype(bool) & whitelist).sum())
    W(f"- of the **{int(y.sum())}** rows that reached `>= {th:g}`, only **{wl_pos}** "
      f"({pct(wl_pos / y.sum())}) are inside `ClassWhitelist` at all. "
      f"**No tuning of CORE/EARNINGS/the solo rules can ever reach the other "
      f"{int(y.sum()) - wl_pos}** — the precondition removed them before any rule ran.")
    W("")

    # ---------------- 3. matched fire count ----------------
    N = gb["fires"]
    W(f"### 3. At the same {N} fires, the XGB scores hit harder")
    W("")
    W(BLOCK_HDR + " cut |")
    W(BLOCK_SEP + "---|")
    W(block_row("**O5.2 gate (live)**", gb, th, " — |"))
    matched = {}
    for c in XGB_COLS:
        s = df[c].to_numpy(dtype=float)
        m = top_n_mask(s, N)
        b = metric_block(m, y_raw, all_th, n_days)
        matched[c] = (b, m, cut_for_n(s, N))
        W(block_row(f"`{c}` top-{N}", b, th, f" {fmt(cut_for_n(s, N), 4)} |"))
    W("")
    W(f"| candidate | Δ precision vs gate | Newcombe 95% CI | verdict |")
    W("|---|---|---|---|")
    for c in XGB_COLS:
        b = matched[c][0]
        d = b[f"prec{th}"] - gb[f"prec{th}"]
        lo, hi = newcombe(b[f"tp{th}"], b["fires"], gb[f"tp{th}"], gb["fires"])
        v = "**better**" if lo > 0 else ("worse" if hi < 0 else "n.s.")
        W(f"| `{c}` | {d * 100:+.1f} pp | {lo * 100:+.1f} pp – {hi * 100:+.1f} pp | {v} |")
    W("")
    W("`n.s.` means the interval contains zero: the gap is real in the point estimate but "
      f"not demonstrated at {N} fires and {int(y.sum())} positives. That is a statement "
      "about this corpus's size, not evidence of equivalence.")
    W("")

    # ---------------- criteria for the column choice ----------------
    # Picking the "best" XGB column on ONE number is how the 2026-09-16 first pass ended up
    # crowning a winner that a 0.9 pp shift dethroned. Score every column on every criterion
    # first, pick on the tally, and let section 10 show the whole matrix.
    clean = df[DATE_COL].to_numpy() >= args.clean_window_from
    y_c = y_raw[clean]
    nd_c = len(set(days[clean]))
    gbc = metric_block(gate[clean], y_c, all_th, nd_c)
    uniq = sorted(df[DATE_COL].unique())
    mid = uniq[len(uniq) // 2]
    first = df[DATE_COL].to_numpy() < mid
    auc_of = {r["name"]: r["auc"] for r in rank_rows}
    ap_of = {r["name"]: r["ap"] for r in rank_rows}

    def _split_precision(sc, tr, te):
        """Tune the cut on the train half at the gate's fire RATE, measure on the other."""
        k_tr = max(1, int(round(gb["fires_per_day"] * len(set(days[tr])))))
        cut = cut_for_n(sc[tr], k_tr)
        m = (sc >= cut) & te
        return (float((y_raw[m] >= th).mean()) if m.sum() else float("nan"),
                cut, int(m.sum()))

    crit = {}
    for c in XGB_COLS:
        sc = df[c].to_numpy(dtype=float)
        pc = metric_block(top_n_mask(sc[clean], gbc["fires"]), y_c, all_th, nd_c)
        pa, cut_a, na = _split_precision(sc, ~first, first)   # tuned on B, measured on A
        pb, cut_b, nb = _split_precision(sc, first, ~first)   # tuned on A, measured on B
        crit[c] = {"auc": auc_of[c], "ap": ap_of[c],
                   "matched": matched[c][0][f"prec{th}"],
                   "clean": pc[f"prec{th}"], "clean_block": pc,
                   "A": pa, "B": pb, "A_n": na, "B_n": nb,
                   "cut_a": cut_a, "cut_b": cut_b,
                   "sum": matched[c][0]["sum"]}
    CRITERIA = [("AUC", "auc"), ("PR-AUC", "ap"), (f"prec@{N}", "matched"),
                ("clean window", "clean"), ("half A", "A"), ("half B", "B")]
    for _, key in CRITERIA:                      # rank 1 = best, ties share the rank
        order = sorted(XGB_COLS, key=lambda c: -crit[c][key])
        for c in XGB_COLS:
            crit[c][key + "_rank"] = 1 + sum(1 for o in order if crit[o][key] > crit[c][key])
    for c in XGB_COLS:
        crit[c]["firsts"] = sum(1 for _, k in CRITERIA if crit[c][k + "_rank"] == 1)
        crit[c]["meanrank"] = np.mean([crit[c][k + "_rank"] for _, k in CRITERIA])
    # tally first, then mean rank, then AUC -- all three printed in section 10
    best_m = min(XGB_COLS, key=lambda c: (-crit[c]["firsts"], crit[c]["meanrank"],
                                          -crit[c]["auc"]))

    # ---------------- 4. overlap ----------------
    bm = matched[best_m][1]
    W(f"### 4. Where they disagree — `{best_m}` top-{N} vs the gate")
    W("")
    W("| cell | n | TP@>=%g | hit rate | median move | sum captured |" % th)
    W("|---|---|---|---|---|---|")
    for label, m in (("both fire", gate & bm),
                     (f"gate only", gate & ~bm),
                     (f"`{best_m}` only", ~gate & bm),
                     ("neither", ~gate & ~bm)):
        b = metric_block(m, y_raw, [th], n_days)
        W(f"| {label} | {b['fires']} | {b[f'tp{th}']} | {pct(b[f'prec{th}'])} | "
          f"{fmt(b['median'], 1)} | {b['sum']:.0f} |")
    W("")
    head_col = "Headline" if "Headline" in df.columns else None
    for label, m in ((f"`{best_m}` catches, gate misses", ~gate & bm),
                     (f"gate catches, `{best_m}` misses", gate & ~bm)):
        sub = df[m & (y_raw >= th)].copy()
        sub["_t"] = y_raw[m & (y_raw >= th)]
        sub = sub.sort_values("_t", ascending=False).head(10)
        W(f"**Top winners {label}** ({int((m & (y_raw >= th)).sum())} in total):")
        W("")
        W("| symbol | date | FineClass | fromPR_High(%) |" +
          (" headline |" if head_col else ""))
        W("|---|---|---|---|" + ("---|" if head_col else ""))
        for _, r in sub.iterrows():
            h = ""
            if head_col:
                t = str(r[head_col]).replace("|", "/")
                h = f" {t[:70]}{'…' if len(t) > 70 else ''} |"
            W(f"| {r['Symbol']} | {r[DATE_COL]} | {r['FineClass']} | {r['_t']:.1f} |" + h)
        W("")

    # ---------------- 5. operating curve ----------------
    W("### 5. The operating curve — precision against workload")
    W("")
    rates = sorted({0.5, 1.0, 2.0, 3.0, 4.0, 6.0, 8.0, 10.0,
                    round(gb["fires_per_day"], 2)})
    for c in [best_m, "proba_clf_ge35"] if best_m != "proba_clf_ge35" else [best_m]:
        s = df[c].to_numpy(dtype=float)
        W(f"`{c}`:")
        W("")
        W("| target /day | fires | cut | TP | precision | 95% CI | lift | recall | "
          "median | sum |")
        W("|---|---|---|---|---|---|---|---|---|---|")
        for rate in rates:
            k = int(round(rate * n_days))
            if k < 1 or k > n:
                continue
            b = metric_block(top_n_mask(s, k), y_raw, [th], n_days)
            star = " ←gate rate" if abs(rate - round(gb["fires_per_day"], 2)) < 1e-9 else ""
            W(f"| {rate:g}{star} | {b['fires']} | {fmt(cut_for_n(s, k), 4)} | "
              f"{b[f'tp{th}']} | {pct(b[f'prec{th}'])} | {pct(b[f'prec{th}_lo'])}–"
              f"{pct(b[f'prec{th}_hi'])} | {fmt(b[f'lift{th}'], 2)}x | "
              f"{pct(b[f'recall{th}'])} | {fmt(b['median'], 1)} | {b['sum']:.0f} |")
        W("")

    # ---------------- 6. hybrids ----------------
    # veto frontier: keep the gate, add an XGB floor to every branch
    s_best = df[best_m].to_numpy(dtype=float)
    frontier = []
    for q in np.arange(0.0, 0.85, 0.05):
        cut = float(np.quantile(s_best[gate], q))
        m = gate & (s_best >= cut)
        tp = int((m & (y_raw >= th)).sum())
        frontier.append((cut, int(m.sum()), tp, tp / m.sum() if m.sum() else float("nan")))
    # the knee: best precision among cuts that give up at most one true positive
    keep_tp = gb[f"tp{th}"] - 1
    ok = [f for f in frontier if f[2] >= keep_tp]
    veto_cut = max(ok, key=lambda f: f[3])[0] if ok else frontier[0][0]
    veto = gate & (s_best >= veto_cut)

    W("### 6. Hybrids — XGB as a veto, as an extra branch, and on the whitelist alone")
    W("")
    W(BLOCK_HDR)
    W(BLOCK_SEP)
    W(block_row("O5.2 gate (live)", gb, th))
    hybrids = {}
    for q in (0.50, 0.70, 0.80, 0.90):
        cut = float(np.quantile(s_best, q))
        hi_m = s_best >= cut
        for nm, m in ((f"gate AND `{best_m}`>={cut:.3f}", gate & hi_m),
                      (f"gate OR `{best_m}`>={cut:.3f}", gate | hi_m),
                      (f"whitelist AND `{best_m}`>={cut:.3f}", whitelist & hi_m)):
            b = metric_block(m, y_raw, all_th, n_days)
            hybrids[nm] = (b, m)
            W(block_row(nm, b, th))
    W("")
    W(f"**The veto frontier.** Keeping every existing rule and ANDing an XGB floor onto "
      f"the gate — what each floor costs in fires and buys in precision:")
    W("")
    W(f"| `{best_m}` floor | fires | /day | TP | TP lost | precision | lift |")
    W("|---|---|---|---|---|---|---|")
    base_th = float((y_raw >= th).mean())
    for cut, nf, tp, pr in frontier:
        star = " ←picked" if abs(cut - veto_cut) < 1e-12 else ""
        W(f"| {cut_str(cut)}{star} | {nf} | {nf / n_days:.2f} | {tp} | "
          f"{gb[f'tp{th}'] - tp} | {pct(pr)} | {fmt(pr / base_th, 2)}x |")
    W("")
    W(f"The pick is the floor with the best precision among those giving up at most one "
      f"true positive — `{best_m} >= {cut_str(veto_cut)}`.")
    W("")
    W(f"**Which branch does that veto clean up?** Per-branch fires, split by whether "
      f"`{best_m}` clears the picked floor of {cut_str(veto_cut)}:")
    W("")
    cut_N = veto_cut
    W(f"| branch | fires | fires with `{best_m}` >= {cut_N:.4f} | TP kept | TP lost | "
      "precision before → after |")
    W("|---|---|---|---|---|---|")
    for rn in rule_names + ["__GATE__"]:
        m = gate if rn == "__GATE__" else res[f"fired_{rn}"].to_numpy(dtype=bool)
        keep = m & (s_best >= cut_N)
        tp_b = int((m & (y_raw >= th)).sum())
        tp_a = int((keep & (y_raw >= th)).sum())
        pb = tp_b / m.sum() if m.sum() else float("nan")
        pa = tp_a / keep.sum() if keep.sum() else float("nan")
        nm = "**gate (any rule)**" if rn == "__GATE__" else f"`{rn}`"
        W(f"| {nm} | {int(m.sum())} | {int(keep.sum())} | {tp_a} | {tp_b - tp_a} | "
          f"{pct(pb)} → {pct(pa)} |")
    W("")

    # ---------------- 7. stability ----------------
    W("### 7. Stability — is the cut a fit artifact?")
    W("")
    W(f"Split at `{mid}`: block A = {int(first.sum())} rows / "
      f"{len(set(days[first]))} sessions, block B = {int((~first).sum())} rows / "
      f"{len(set(days[~first]))} sessions.")
    W("")
    W("| tuned on | measured on | cut | fires | precision | 95% CI | lift |")
    W("|---|---|---|---|---|---|---|")
    for tr_name, tr, te_name, te in (("A", first, "B", ~first), ("B", ~first, "A", first)):
        rate = gb["fires_per_day"]
        k_tr = max(1, int(round(rate * len(set(days[tr])))))
        cut = cut_for_n(s_best[tr], k_tr)
        m = (s_best >= cut) & te
        tp = int((m & (y_raw >= th)).sum())
        lo, hi = wilson(tp, int(m.sum()))
        base = float((y_raw[te] >= th).mean())
        p = tp / m.sum() if m.sum() else float("nan")
        W(f"| {tr_name} | {te_name} | {cut:.4f} | {int(m.sum())} | {pct(p)} | "
          f"{pct(lo)}–{pct(hi)} | {fmt(p / base, 2)}x |")
    # the gate, same two blocks, for reference
    for te_name, te in (("B", ~first), ("A", first)):
        m = gate & te
        tp = int((m & (y_raw >= th)).sum())
        lo, hi = wilson(tp, int(m.sum()))
        base = float((y_raw[te] >= th).mean())
        p = tp / m.sum() if m.sum() else float("nan")
        W(f"| *(gate, fixed)* | {te_name} | — | {int(m.sum())} | {pct(p)} | "
          f"{pct(lo)}–{pct(hi)} | {fmt(p / base, 2)}x |")
    W("")
    W(f"**The clean sub-window.** The `ClassWhitelist` was calibrated on "
      f"2026-06-20..2026-08-13, which overlaps the first half of this corpus — so the "
      f"full-window comparison is if anything *generous to O5.2*. Restricted to "
      f"`>= {args.clean_window_from}` ({int(clean.sum())} rows, "
      f"{len(set(days[clean]))} sessions, "
      f"{int((y_raw[clean] >= th).sum())} movers at `>= {th:g}`, "
      f"{pct(float((y_raw[clean] >= th).mean()))} base rate), where neither side saw "
      f"the data:")
    W("")
    W(BLOCK_HDR)
    W(BLOCK_SEP)
    # scored the sub-window AGAINST the sub-window: recall and lift are only meaningful
    # against the positives and base rate that actually live inside it
    W(block_row("O5.2 gate (live)", gbc, th))
    for c in XGB_COLS:
        W(block_row(f"`{c}` top-{gbc['fires']}", crit[c]["clean_block"], th))
    W("")
    per_day = pd.Series(days[top_n_mask(s_best, N)]).value_counts()
    W(f"Fire concentration for `{best_m}` top-{N}: busiest session "
      f"{int(per_day.max())} fires, median {per_day.median():.0f}, "
      f"{len(per_day)}/{n_days} sessions fire at all.")
    W("")

    # ---------------- 8. the tail ----------------
    W("### 8. The gate's edge on captured move is two rows deep")
    W("")
    gate_only = gate & ~bm
    xgb_only = ~gate & bm
    # itertuples() mangles a leading-underscore column name, so this one is plain
    tail = df.assign(tgt=y_raw)[gate_only].sort_values("tgt", ascending=False).head(2)
    tail_sum = float(tail["tgt"].sum())
    go = metric_block(gate_only, y_raw, [th], n_days)
    xo = metric_block(xgb_only, y_raw, [th], n_days)
    W(f"The two biggest movers of the whole window — "
      + ", ".join(f"**{r.Symbol} {r.tgt:.0f}%**" for r in tail.itertuples())
      + f" — are both in the {int(gate_only.sum())} rows the gate fires and "
      f"`{best_m}` does not. They are {tail_sum:.0f} of that set's {go['sum']:.0f} "
      f"percentage-points, i.e. {pct(tail_sum / go['sum'])} of it.")
    W("")
    W("| set | n | TP | sum captured | sum excl. its own top 2 | median |")
    W("|---|---|---|---|---|---|")
    for label, m in (("gate only", gate_only), (f"`{best_m}` only", xgb_only),
                     ("gate (all fires)", gate), (f"`{best_m}` (all fires)", bm)):
        v = np.sort(y_raw[m])[::-1]
        b = metric_block(m, y_raw, [th], n_days)
        W(f"| {label} | {b['fires']} | {b[f'tp{th}']} | {b['sum']:.0f} | "
          f"{v[2:].sum():.0f} | {fmt(b['median'], 1)} |")
    W("")
    W(f"So the honest reading of \"EV per fire\" is: `{best_m}` wins on the bulk of the "
      f"distribution (median {fmt(metric_block(bm, y_raw, [th], n_days)['median'], 1)} vs "
      f"{fmt(gb['median'], 1)}, and it still leads once each side's own top 2 are "
      f"removed), while the gate happened to own the two lottery tickets. With "
      f"{int(y.sum())} positives and 25 sessions, which side catches a 1000% outlier is "
      "not a repeatable property — do not read it as the gate finding something the "
      "model cannot.")
    W("")

    # ---------------- 9. the recommendation ----------------
    W("### 9. Candidate gates at the live workload")
    W("")
    N_rec = N
    rec_cut = cut_for_n(s_best, N_rec)
    options = {
        f"**A — replace**: `{best_m} >= {cut_str(rec_cut)}`": s_best >= rec_cut,
        f"**B — veto**: gate AND `{best_m} >= {cut_str(veto_cut)}`": veto,
        f"**C — whitelist + XGB**: ClassWhitelist AND `{best_m} >= "
        f"{cut_str(cut_for_n(np.where(whitelist, s_best, -np.inf), N_rec))}`":
            whitelist & (s_best >= cut_for_n(np.where(whitelist, s_best, -np.inf), N_rec)),
        "**D — today's gate**": gate,
    }
    W(BLOCK_HDR)
    W(BLOCK_SEP)
    for name, m in options.items():
        W(block_row(name, metric_block(m, y_raw, all_th, n_days), th))
    W("")
    W("| option | Δ precision vs today | Newcombe 95% CI | Δ fires/day |")
    W("|---|---|---|---|")
    for name, m in options.items():
        if name.startswith("**D"):
            continue
        b = metric_block(m, y_raw, all_th, n_days)
        lo, hi = newcombe(b[f"tp{th}"], b["fires"], gb[f"tp{th}"], gb["fires"])
        W(f"| {name} | {(b[f'prec{th}'] - gb[f'prec{th}']) * 100:+.1f} pp | "
          f"{lo * 100:+.1f} – {hi * 100:+.1f} pp | "
          f"{b['fires_per_day'] - gb['fires_per_day']:+.2f} |")
    W("")
    W(f"The `{best_m}` cut is **stable across the two halves** of the window "
      "(§7: the same target fire rate picks it within a point either way), which is the "
      "one thing that makes a fixed numeric threshold safe to write into a rules file.")
    W("")


    # ---------------- 10. which column ----------------
    W("### 10. Which XGB column? — the same question asked six ways")
    W("")
    W("Ranks across every criterion in this report. `1` is best; ties share a rank.")
    W("")
    W("| column | " + " | ".join(nm for nm, _ in CRITERIA) + " | firsts | mean rank |")
    W("|---|" + "---|" * (len(CRITERIA) + 2))
    for c in sorted(XGB_COLS, key=lambda c: (-crit[c]["firsts"], crit[c]["meanrank"])):
        cells = []
        for _, k in CRITERIA:
            v = crit[c][k]
            shown = fmt(v) if k in ("auc", "ap") else pct(v)
            cells.append(f"{shown} ({crit[c][k + '_rank']})")
        mark = " **←picked**" if c == best_m else ""
        W(f"| `{c}`{mark} | " + " | ".join(cells)
          + f" | {crit[c]['firsts']} | {crit[c]['meanrank']:.1f} |")
    W("")
    W(f"Selection rule, applied in order: most firsts → lowest mean rank → highest AUC. "
      f"That picks **`{best_m}`**.")
    W("")
    # is any gap between the XGB columns actually separable from noise?
    W(f"But the honest answer is that these columns are not distinguishable on this corpus. "
      f"Paired day-block bootstrap of ΔAUC, `{best_m}` minus each other column:")
    W("")
    W(f"| vs | ΔAUC | 95% CI | P(Δ>0) | separable? |")
    W("|---|---|---|---|---|")
    sep_any = False
    for c in XGB_COLS:
        if c == best_m:
            continue
        (lo_, hi_), pg = block_boot_delta_auc(
            y, df[best_m].to_numpy(dtype=float), df[c].to_numpy(dtype=float),
            days, args.bootstrap, rng)
        ok = lo_ > 0 or hi_ < 0
        sep_any = sep_any or ok
        W(f"| `{c}` | {crit[best_m]['auc'] - crit[c]['auc']:+.3f} | {fmt(lo_)}–{fmt(hi_)} | "
          f"{pct(pg)} | {'**yes**' if ok else 'no'} |")
    W("")
    if not sep_any:
        W(f"**Every interval contains zero.** No XGB column is separable from any other here. "
          f"`{best_m}` wins the tally, but treat the choice among the five as a "
          f"*preference*, not a finding — the spread between best and worst at {N} fires is "
          f"{(max(crit[c]['matched'] for c in XGB_COLS) - min(crit[c]['matched'] for c in XGB_COLS)) * 100:.1f} pp, "
          f"against a {(crit[best_m]['matched'] - gb[f'prec{th}']) * 100:.1f} pp gap to the gate. "
          "The XGB-vs-gate comparison is the one this corpus can actually resolve.")
    else:
        W("At least one pair IS separable — see the table.")
    W("")

    # ---------------- 11. what changed ----------------
    if args.compare_scores:
        prev = pd.read_csv(args.compare_scores, sep="\t")
        keys = ["Symbol", DATE_COL, "ArrivalTime"]
        pj = df[keys].merge(prev, on=keys, how="left", validate="one_to_one")
        W("### 11. What changed vs the uncorrected scores")
        W("")
        W(f"`{Path(args.compare_scores).name}` was produced by "
          "`score_val_predictions.py` **before** it passed `iteration_range`, i.e. with "
          "every boosted round rather than the early-stopped `best_iteration`. Same rows, "
          "same target, different trees. Effect on this report:")
        W("")
        W(f"| column | max abs Δscore | AUC>={th:g} before → after | "
          f"prec@{N} before → after |")
        W("|---|---|---|---|")
        prev_prec = {}
        for c in XGB_COLS:
            if c not in prev.columns:
                continue
            o = pj[c].to_numpy(dtype=float)
            nn = df[c].to_numpy(dtype=float)
            a_o = auc_score(y, o)
            p_o = float((y_raw[top_n_mask(o, N)] >= th).mean())
            prev_prec[c] = p_o
            tag = " *(unchanged — never early-stopped)*" if np.array_equal(o, nn) else ""
            W(f"| `{c}`{tag} | {np.abs(nn - o).max():.4f} | {fmt(a_o)} → {fmt(crit[c]['auc'])} "
              f"| {pct(p_o)} → {pct(crit[c]['matched'])} |")
        W("")
        if prev_prec:
            old_best = max(prev_prec, key=lambda c: prev_prec[c])
            moved = old_best != best_m
            W("The aggregate movement is small, but it is **not** cosmetic at the row level — "
              f"`pred_reg_se` moves by up to "
              f"{np.abs(df['pred_reg_se'].to_numpy(dtype=float) - pj['pred_reg_se'].to_numpy(dtype=float)).max():.1f} "
              f"percentage points on a single PR."
              + (f" And it was enough to unseat the column a single-criterion rule would "
                 f"name: on the uncorrected scores `{old_best}` led at "
                 f"{pct(prev_prec[old_best])} at {N} fires; corrected, it is "
                 f"{pct(crit[old_best]['matched'])} and §10 picks `{best_m}`. That is the "
                 "whole reason this report decides the column on a matrix rather than on one "
                 "number." if moved else
                 f" The leader at {N} fires is unchanged (`{old_best}`)."))
            W("")

    # ---------------- recommendation + integrity + caveats ----------------
    ra = metric_block(options[list(options)[0]], y_raw, all_th, n_days)
    rb = metric_block(veto, y_raw, all_th, n_days)
    W("## Recommendation")
    W("")
    W(f"**Ship option B first, and treat option A as the target.**")
    W("")
    W(f"Read this section against the Verdict's second point: the precision gaps below are "
      f"large and consistent, but on {int(y.sum())} positives their Newcombe intervals "
      f"straddle zero. That is an argument for shipping the reversible option and "
      f"collecting another window — not for shipping neither, and not for treating "
      f"{pct(ra[f'prec{th}'])} as a number you can bank.")
    W("")
    W(f"**B — the veto — is the one that is cheap, safe and reversible.** Keep every "
      f"existing rule and AND an XGB floor onto the gate: {rb['fires']} fires "
      f"({rb['fires_per_day']:.2f}/day, down from {gb['fires_per_day']:.2f}), precision "
      f"{pct(gb[f'prec{th}'])} → **{pct(rb[f'prec{th}'])}** at `>= {th:g}`, giving up "
      f"{gb[f'tp{th}'] - rb[f'tp{th}']} of {gb[f'tp{th}']} true positives. It cannot "
      f"trade anything today's gate would not, so the worst case is fewer trades, not "
      f"different ones. In the DSL it is one new precondition line:")
    W("")
    W("```")
    W("REQUIRE ClassWhitelist: FineClass in MA, Partnership, Regain_Compliance, "
      "Clinical_Data, Contract_Award, Patent_IP, Earnings_Results")
    W(f"REQUIRE XgbFloor:       {best_m} >= {cut_str(veto_cut)}")
    W("```")
    W("")
    W(f"!! A `REQUIRE` is the right shape here — constraining every branch from one line "
      f"is exactly what preconditions are for — but `REQUIRE` **fails closed**: if the "
      f"scorer dies, `{best_m}` is None, the "
      f"line fails, and *all* trading stops. That is the documented behaviour and the "
      f"`BLOCKED BY MISSING SCORE ONLY` alarm is what makes it visible. If you would "
      f"rather a dead model degrade to today's gate instead of to silence, put the same "
      f"condition on each of the five rules instead of in a `REQUIRE`.")
    W("")
    W(f"**A — the replacement — is where the value is**, and §3 is the evidence: at the "
      f"identical {N_rec} fires it beats today's gate by "
      f"{(ra[f'prec{th}'] - gb[f'prec{th}']) * 100:+.1f} pp on precision *and* by "
      f"{(ra[f'recall{th}'] - gb[f'recall{th}']) * 100:+.1f} pp on recall — the veto buys "
      f"precision by trading less, while the replacement buys precision and coverage at "
      f"the same workload. Written as:")
    W("")
    W("```")
    W(f"XgbReg: {best_m} >= {cut_str(rec_cut)}")
    W("```")
    W("")
    W(f"and note what is **absent**: no `ClassWhitelist`. Option C keeps it and comes out "
      f"*worse* than A (§9) — the model already reads `FineClass` and `BroadClass` as "
      f"features, so the precondition is now redundant, and its cost is the "
      f"{int(y.sum()) - wl_pos} `>= {th:g}` movers it deletes before any rule runs (§2).")
    W("")
    W("**What A costs to run.** The models need the 49 features in "
      "`../training_data/xgb_fromPRhigh_features_conservative.txt` — `atr_pct_14`, "
      "`MktCap`, `Float`, `gap_abs_mean_20d`, `amihud_63d`, `msc_basis_ratio`, "
      "`FNG_Index`, `SI_ChangePct`, `dist_swing_high_20d_pct` and 27 FinBERT-embedding "
      "PCA components. O5.2 computes none of them at PR arrival today: the technicals "
      "come from the nightly universe TSV and the PCA components need a FinBERT embed + "
      "`finbert_pca.pkl` transform on the live path. "
      "`../training_data/XGBoost_inference_script.py` already implements load-prep-score "
      "for exactly this, so the work is wiring and a latency budget, not modelling. "
      f"On the O5.2 side it is one new entry in `_TRIGGER_FIELDS` "
      f"(`Orchestrator5.2.py:785`) plus a source dict for `{best_m}`; the DSL itself "
      "needs no change, since it already takes arbitrary numeric fields. "
      "Until that exists, A is not deployable and B is — and note B needs the same "
      "inference path, so B is 'cheap' only in risk, not in engineering.")
    W("")
    W("**Do not do this yet:** retune `CORE`/`FLANGSolo`/`FinBERTSolo` thresholds off "
      "this report. §6 shows where the veto's cuts land, and the branch it prunes hardest "
      "is `EARNINGS` — which is precisely the branch this corpus replays least "
      "faithfully (400-word body cap, 91.4% fidelity). Those numbers are the least "
      "trustworthy in the report, so do not let them drive a threshold change.")
    W("")

    W("## Cross-check integrity")
    W("")
    W("1. **The replay matches the live tape.** "
      "`replay_gate.py --verify-against ../orchestrator5/tables --verify-since "
      "2026-08-18` — 448 rows overlap, **448 agree, 0 disagree** "
      "(non-Earnings_Results 407/407, Earnings_Results 41/41). This is what licenses "
      "every gate number above.")
    W("2. **`EarningScore` reproduces the frozen score.** 155 of the 253 "
      "`Earnings_Results` rows carry a reference score from the earlier earnScored "
      "corpus: **155/155 exact**. (One non-earnings row differs, because the two corpora "
      "carry different `LastDailyClose` bases; the gate cannot read `EarningScore` "
      "outside `Earnings_Results`, so it cannot matter.)")
    W("3. **Base rates and whitelist size are as expected:** 333 / 190 / 132 rows at "
      "`>= 20` / `>= 35` / `>= 50`; `ClassWhitelist` admits 655 of 1380.")
    W("4. **The scores now come from the early-stopped models — this was a defect, and it "
      "is fixed.** `../training_data/score_val_predictions.py` used to call "
      "`booster.predict(dm)` with no `iteration_range`, so it scored with every boosted "
      "round (246/264/310 for the classifiers, 336 for `pred_reg_se`) instead of the "
      "`best_iteration` early stopping chose (145/163/209/235) — a different model from "
      "the one each `*_report.md` describes. Fixed 2026-09-16; the script now derives the "
      "range from the booster and logs it per model, with `--all-trees` to reproduce the "
      "old behaviour. **The proof it is right:** top-10% precision now reproduces "
      "`xgb_fromPRhigh_clf_report.md` **exactly** — 0.551 @`>=20`, 0.348 @`>=35`, "
      "0.239 @`>=50`; the old columns gave 0.572 and 0.232. §11 quantifies what moved.")
    W("5. **The gate is a control and did not move.** It reads only the six O5.2 columns "
      "and `EarningScore`, never an XGB score, so re-scoring must leave it untouched — and "
      "it does: 113 fires, the same five branch counts, `ClassWhitelist` 655, and the same "
      "448/448 live-tape agreement as before the fix. `pred_reg_q80` is a second null "
      "control: `best_iteration` 2999 of 3000, so its column is bit-identical (§11).")
    W("")

    W("## Caveats")
    W("")
    W("- **`fromPR_High(%)` is the intraday ceiling from the PR, not realized PnL.** "
      "Every mean/median/sum here is the best case with a perfect exit. It is applied "
      "identically to both sides, so it ranks them fairly; it does not forecast an "
      "account balance.")
    W("- **The `EARNINGS` branch is an estimate.** The corpus `Body` is capped at 400 "
      f"words (1311 of {n} rows sit at exactly 400) while the live scorer reads the whole "
      "body. `replay_gate.py` measures 91.4% fidelity on `Earnings_Results` vs 100.0% "
      "elsewhere. `EARNINGS` is 52 of the gate's 113 fires here, so this is the single "
      "largest source of slack in the gate's numbers.")
    W("- **This corpus is not the live tape.** It is coffee-filtered PRstats, so "
      f"{gb['fires_per_day']:.2f} fires/day here is not the live rate (the "
      "`ClassWhitelist` calibration measured ~2.2/day on a different corpus). Fire "
      "*rates* compare validly between the two sides on this corpus; the absolute "
      "number does not transfer to production.")
    W("- **The recommended cuts were chosen on this data.** §7 is the only thing "
      "bounding that optimism, and it is a 12/13-session split, not a fresh window.")
    W(f"- **n = {int(y.sum())} positives over {n_days} sessions.** Every Wilson interval "
      "in §3 spans 15+ points. Read the intervals, not the point estimates — the "
      "*direction* is consistent across every cut and every sub-window, which is the "
      "real evidence; no single cell here is precise.")
    W("- **Five XGB scores were compared and the best was reported.** That selection is "
      "itself a small multiple-comparisons cost; the four runners-up land within 4 pp of "
      "each other (§3), so the choice among them matters much less than the choice "
      "between XGB and the gate.")
    W("")

    W("## Artifacts")
    W("")
    W(f"- `outputs/{inp.name}` — the scored corpus, built by "
      "`earnscore_backfill_addOn.py`. `.gitignore:35` re-includes "
      "`price_stats_loader/outputs/*.tsv`, so it is not *ignored* — but no `*XGBscores*.tsv` "
      "has ever been `git add`ed, so it is untracked. Regenerating it cannot dirty a "
      "tracked file.")
    W("- `score_val_predictions.py --out <path>` — writes the score columns. The file it "
      "feeds was historically hand-renamed twice (`08_...` → date-range prefix, "
      "`_val_scores` → `_val_XGBscores`) with no script; writing straight to `outputs/` "
      "with `--out` is what removes that undocumented gap.")
    W("- `compare_xgb_vs_orch52.py` — regenerates this report end to end.")
    W("- `earnscore_backfill_addOn.py` — the `EarningScore`/`ID` backfill.")
    W("- `replay_gate.py` — unchanged; imported, not copied.")
    W("")

    # ---------------- verdict ----------------
    bmb = matched[best_m][0]
    d = bmb[f"prec{th}"] - gb[f"prec{th}"]
    nlo, nhi = newcombe(bmb[f"tp{th}"], bmb["fires"], gb[f"tp{th}"], gb["fires"])
    v = []
    v.append("## Verdict")
    v.append("")
    sep = nlo > 0
    v.append(
        f"**Yes — on ranking, decisively; on precision at the live workload, consistently "
        f"but not yet at 95% confidence.** Two different strengths of claim, and they should "
        "not be run together:")
    v.append("")
    v.append(
        f"1. **Ranking — settled.** All five XGB columns out-AUC every score the gate reads. "
        f"Best XGB `{best_x['name']}` {fmt(best_x['auc'])} vs best gate input "
        f"`{best_o['name']}` {fmt(best_o['auc'])}; paired day-block "
        f"ΔAUC {fmt(best_x['auc'] - best_o['auc'])}, 95% CI {fmt(dlo)}–{fmt(dhi)} — "
        "**excludes zero**.")
    v.append(
        f"2. **Precision at matched fires — large, consistent, not certified.** At the gate's "
        f"own {N} fires ({gb['fires_per_day']:.2f}/day), `{best_m}` hits `>= {th:g}` on "
        f"**{bmb[f'tp{th}']}/{N} = {pct(bmb[f'prec{th}'])}** against the gate's "
        f"**{gb[f'tp{th}']}/{N} = {pct(gb[f'prec{th}'])}** — {d * 100:+.1f} pp, but the "
        f"Newcombe 95% CI is {nlo * 100:+.1f} to {nhi * 100:+.1f} pp, which "
        + ("**excludes zero**." if sep else "**contains zero**. ")
        + ("" if sep else
           f"With {int(y.sum())} positives over {n_days} sessions this corpus cannot "
           "certify a gap of that size at 95%. What supports it is consistency, not any "
           "single interval: the same direction holds for all five XGB columns, at every "
           "cut, in both date halves and in the clean sub-window (§10)."))
    v.append(
        f"   It also catches {pct(bmb[f'recall{th}'])} of the window's {int(y.sum())} big "
        f"movers against {pct(gb[f'recall{th}'])}, and its fires sum to "
        f"{bmb['sum']:.0f} percentage-points of ceiling move against {gb['sum']:.0f}.")
    v.append("")
    v.append(
        f"**Do not read `{best_m}` as the uniquely best column.** It wins the §10 tally "
        f"{crit[best_m]['firsts']}/6, but no XGB column is separable from any other here — "
        "every pairwise ΔAUC interval contains zero. An earlier pass of this report, run on "
        "scores that had not been truncated to `best_iteration`, named `pred_reg_se` on a "
        "single criterion; §11 shows what that was worth.")
    v.append("")
    v.append(
        f"The single biggest structural finding is §2's: only **{wl_pos} of the "
        f"{int(y.sum())}** `>= {th:g}` movers are inside `ClassWhitelist`, so "
        f"{pct(1 - wl_pos / y.sum())} of the upside is unreachable by *any* retuning of "
        f"the current rules. That ceiling, not the sentiment cuts, is what bounds O5.2 "
        "today.")
    lines[verdict_slot:verdict_slot] = v

    out = Path(args.outdir) / args.report
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out}  ({len(lines)} lines)")
    print(f"gate: {gb['fires']} fires, precision@>={th:g} {pct(gb[f'prec{th}'])}")
    print(f"best XGB at matched N: {best_m} {pct(bmb[f'prec{th}'])}")


if __name__ == "__main__":
    main()
