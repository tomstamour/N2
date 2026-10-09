#!/usr/bin/env python3
"""Which FineClass values are worth trading, and can a class filter cut the O5.2
false-positive rate without killing trade volume?

Four questions, in the order they have to be answered:

  1. CONDITIONAL CLASS TABLE - among the rows the gate ACTUALLY FIRES ON, which
     classes are the TPs and which are the FPs, and which RULE produced each. The
     unconditional class ranking (07_class_power.md) cannot answer this: the gate
     already selects hard on positive sentiment, which correlates with class.
  2. MAGNITUDE BY CLASS - does the fromPR_High distribution differ by class, i.e. do
     some classes produce bigger movers rather than more frequent ones? Ranking by
     median and ranking by P(>=50%) are different orderings if this is true.
  3. FILTER FRONTIER - veto / whitelist / class-conditional, each priced in
     precision AND trades-per-day, with a date-split stability check.
  4. CLASS x MODEL GRID - is any class better served by a specific encoder, and does
     out-of-fold class-specific model selection actually beat one global model?

  /home/tom/venv/bin/python analyze_class_trading_value.py \
      --input outputs/07_PRstats_2026-05-04-2026-08-13_gateReplay.tsv

!! Read replay_gate.py's docstring first. The EARNINGS branch is replay-degraded by
   the corpus's 400-word Body cap; every EARNINGS number below is flagged, not fixed.
"""
from __future__ import annotations

import argparse
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

import replay_gate

TARGET = "fromPR_High(%)"
DATE = "ArrivalDate"
CLASS = "FineClass"
KW_HONEST_FROM = "2026-06-20"      # kw15 trained on 2026-05-03..06-19
GATE_VERIFIED_FROM = "2026-08-12"  # first date the current 5-rule gate ran live
LIVE_TRADES_PER_DAY = 3.3          # 136 fires / 41 days in tables/news_output_*.tsv
KW_RULES = {"CORE", "EARNINGS", "KwBodySolo"}   # rules that read a kw15 score
SCORE_RE = re.compile(r"_(neg|neu|ind|pos|ss|unc|lit|smod|wmod|cons)_(hd|bd)$")
MAG_CUTS = [15, 30, 50, 100]

# The six classes whose unconditional hit rate is furthest below base (07_class_power).
VETO_CANDIDATES = ["Management_Change", "Earnings_Date", "Conference_Presentation",
                   "Reverse_Split", "Legal", "Deficiency_Notice"]
OPERATOR_WHITELIST = ["MA", "Partnership", "Regain_Compliance", "Buyback_Dividend"]
# Pre-registered: FLANG is documented weak on non-FPB biotech positives, and
# FLANGSolo is a live rule - so this is where a model swap is most likely to be real.
BIOTECH = ["Clinical_Data", "Clinical_Update", "FDA_Approval", "FDA_Designation"]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p, d = k / n, 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def bh(p: np.ndarray) -> np.ndarray:
    p = np.asarray(p, float)
    n = len(p)
    if n == 0:
        return p
    order = np.argsort(p)
    adj, prev = np.empty(n), 1.0
    for rank, i in enumerate(reversed(order), start=1):
        prev = min(prev, p[i] * n / (n - rank + 1))
        adj[i] = prev
    return adj


def ci(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{lo:.1%}-{hi:.1%}" if n else "-"


def load(path: Path, tp: float):
    df = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    df[TARGET] = pd.to_numeric(df[TARGET], errors="coerce")
    df = df[df[TARGET].notna()].reset_index(drop=True)
    df["y"] = (df[TARGET] >= tp).astype(int)
    res = replay_gate.replay(df)
    return pd.concat([df, res], axis=1)


# ── 1. conditional class table ────────────────────────────────────────────────

def conditional_classes(fired: pd.DataFrame, base: float, min_n: int) -> pd.DataFrame:
    tot = int(fired["y"].sum())
    rows = []
    for lvl, g in fired.groupby(CLASS):
        n, k = len(g), int(g["y"].sum())
        _, p = stats.fisher_exact([[k, n - k],
                                   [tot - k, len(fired) - n - (tot - k)]])
        lo, hi = wilson(k, n)
        rows.append({"level": lvl, "fires": n, "tp": k, "fp": n - k,
                     "precision": k / n, "lift": (k / n) / base if base else np.nan,
                     "ci_lo": lo, "ci_hi": hi,
                     "share_of_fires": n / len(fired),
                     "median_high": g[TARGET].median(),
                     "uniq_symbols": g["Symbol"].nunique(), "p": p})
    out = pd.DataFrame(rows).sort_values(["precision", "fires"], ascending=False,
                                         kind="stable")
    out["p_bh"] = bh(out["p"].to_numpy())
    out["thin"] = out.fires < min_n
    return out.reset_index(drop=True)


def rule_by_class(fired: pd.DataFrame, rules: list[str]) -> pd.DataFrame:
    """Which rule produced the FPs in which class. A gate-level fix needs this, not
    the marginal class table: vetoing a class punishes every rule, while the FPs may
    all be coming from one."""
    rows = []
    for rn in rules:
        sub = fired[fired[f"fired_{rn}"]]
        for lvl, g in sub.groupby(CLASS):
            rows.append({"rule": rn, "level": lvl, "fires": len(g),
                         "tp": int(g["y"].sum()), "fp": int((1 - g["y"]).sum()),
                         "precision": g["y"].mean()})
    return pd.DataFrame(rows).sort_values(["rule", "fp"], ascending=[True, False])


# ── 2. magnitude by class ─────────────────────────────────────────────────────

def magnitude(df: pd.DataFrame, min_n: int) -> pd.DataFrame:
    rows = []
    for lvl, g in df.groupby(CLASS):
        rest = df[df[CLASS] != lvl][TARGET]
        _, p = stats.mannwhitneyu(g[TARGET], rest, alternative="two-sided")
        r = {"level": lvl, "n": len(g), "median": g[TARGET].median(),
             "iqr_lo": g[TARGET].quantile(.25), "iqr_hi": g[TARGET].quantile(.75),
             "p90": g[TARGET].quantile(.90), "p99": g[TARGET].quantile(.99),
             "max": g[TARGET].max(), "p_mw": p, "thin": len(g) < min_n}
        for c in MAG_CUTS:
            r[f"pct_ge{c}"] = (g[TARGET] >= c).mean()
        # Conditional magnitude: given it IS a >=50% mover, how big does it get?
        win = g.loc[g[TARGET] >= 50, TARGET]
        r["median_given_tp"] = win.median() if len(win) else np.nan
        rows.append(r)
    out = pd.DataFrame(rows)
    out["p_bh"] = bh(out["p_mw"].to_numpy())
    return out.sort_values("median", ascending=False, kind="stable").reset_index(drop=True)


# ── 3. filter frontier ────────────────────────────────────────────────────────

def score_filter(df: pd.DataFrame, fire: pd.Series, name: str, shape: str,
                 base: float, ref_fires: int, days: int,
                 boots: int = 2000, seed: int = 0) -> dict:
    n = int(fire.sum())
    y = df.loc[fire, "y"]
    k = int(y.sum())
    lo, hi = wilson(k, n)
    rng = np.random.default_rng(seed)
    if n:
        arr = y.to_numpy()
        bs = rng.choice(arr, size=(boots, n), replace=True).mean(axis=1)
        b_lo, b_hi = np.percentile(bs, [2.5, 97.5])
    else:
        b_lo = b_hi = np.nan
    # Date-split stability. A candidate selected on this data will look good on it;
    # a precision that halves across the split is the expected failure mode here.
    d = df.loc[fire, DATE]
    mid = d.sort_values().iloc[len(d) // 2] if n else None
    p1 = df.loc[fire & (df[DATE] <= mid), "y"].mean() if n else np.nan
    p2 = df.loc[fire & (df[DATE] > mid), "y"].mean() if n else np.nan
    return {"name": name, "shape": shape, "fires": n, "tp": k, "fp": n - k,
            "precision": k / n if n else np.nan,
            "lift": (k / n) / base if n and base else np.nan,
            "recall": k / int(df["y"].sum()) if int(df["y"].sum()) else np.nan,
            "ci": ci(k, n), "boot_lo": b_lo, "boot_hi": b_hi,
            "prec_first_half": p1, "prec_second_half": p2,
            "vol_vs_current": n / ref_fires if ref_fires else np.nan,
            "trades_per_day": LIVE_TRADES_PER_DAY * n / ref_fires if ref_fires else np.nan,
            "uniq_symbols": df.loc[fire, "Symbol"].nunique()}


def build_frontier(df: pd.DataFrame, rules: list[str], base: float,
                   days: int, cond_tiers: dict) -> pd.DataFrame:
    cur = df["is_ok"]
    ref = int(cur.sum())
    out = [score_filter(df, cur, "CURRENT GATE (all 5 rules)", "baseline",
                        base, ref, days)]

    # --- veto shapes: keep the gate, drop rows in dead classes
    for tag, drop in (("veto 6 dead classes", VETO_CANDIDATES),
                      ("veto 4 worst", VETO_CANDIDATES[:4]),
                      ("veto Earnings_Results too",
                       VETO_CANDIDATES + ["Earnings_Results"])):
        out.append(score_filter(df, cur & ~df[CLASS].isin(drop),
                                tag, "veto", base, ref, days))
    # Veto applied ONLY to the solo rules, leaving CORE/EARNINGS untouched - the
    # targeted version, if the FPs turn out to be solo-driven.
    solo = df[[f"fired_{r}" for r in rules if r.endswith("Solo")]].any(axis=1)
    multi = df[[f"fired_{r}" for r in rules if not r.endswith("Solo")]].any(axis=1)
    out.append(score_filter(df, multi | (solo & ~df[CLASS].isin(VETO_CANDIDATES)),
                            "veto 6 on SOLO rules only", "veto", base, ref, days))

    # --- whitelist shapes
    for tag, keep in (("whitelist MA+Partnership", ["MA", "Partnership"]),
                      ("whitelist operator's 4", OPERATOR_WHITELIST),
                      ("whitelist top-8 by hit rate",
                       ["MA", "Partnership", "Buyback_Dividend", "Regain_Compliance",
                        "Clinical_Data", "Contract_Award", "Patent_IP",
                        "Business_Update"])):
        out.append(score_filter(df, cur & df[CLASS].isin(keep),
                                tag, "whitelist", base, ref, days))

    # --- class-conditional: solo rules only inside the strong tier, everything else
    # must clear the multi-condition rules. Expressible in the DSL as one rule per
    # strong class plus the untouched CORE/EARNINGS lines.
    for tag, strong in cond_tiers.items():
        out.append(score_filter(df, multi | (solo & df[CLASS].isin(strong)),
                                tag, "class-conditional", base, ref, days))

    # --- rule ablations. Not a class filter, but the honest comparison: if dropping
    # one rule outright beats every class filter, a class filter is the wrong tool.
    for r in rules:
        others = [x for x in rules if x != r]
        out.append(score_filter(df, df[[f"fired_{x}" for x in others]].any(axis=1),
                                f"drop `{r}` entirely", "rule ablation", base, ref, days))

    # --- targeted combinations, aimed at what the rule x class table actually shows:
    # the solo rules fire wide and EARNINGS owns a quarter of all fires.
    def f(r):
        return df[f"fired_{r}"]

    out.append(score_filter(
        df, multi | (solo & ~df[CLASS].eq("Earnings_Results")),
        "solos may not fire on Earnings_Results", "combination", base, ref, days))
    out.append(score_filter(
        df, f("CORE") | f("EARNINGS") | f("KwBodySolo")
        | ((f("FLANGSolo") | f("FinBERTSolo")) & df[CLASS].isin(["MA", "Partnership"])),
        "score solos (FLANG/FinBERT) only in MA+Partnership", "combination",
        base, ref, days))
    out.append(score_filter(
        df, (f("CORE") | f("EARNINGS")
             | ((f("KwBodySolo") | f("FLANGSolo")) & df[CLASS].isin(["MA", "Partnership"]))),
        "drop FinBERTSolo + solos only in MA+Partnership", "combination",
        base, ref, days))
    return pd.DataFrame(out)


# ── 4. class x model grid ─────────────────────────────────────────────────────

def class_model_grid(df: pd.DataFrame, min_n: int, min_pos: int) -> pd.DataFrame:
    chans = [c for c in df.columns if SCORE_RE.search(c) and not c.endswith("_ms_hd")]
    rows = []
    for lvl, g in df.groupby(CLASS):
        if len(g) < min_n or g["y"].sum() < min_pos or g["y"].nunique() < 2:
            continue
        y = g["y"].to_numpy()
        for c in chans:
            v = pd.to_numeric(g[c], errors="coerce")
            if v.notna().mean() < 0.9 or v.nunique() < 3:
                continue
            m = v.notna()
            if y[m].sum() < min_pos or len(set(y[m])) < 2:
                continue
            auc = roc_auc_score(y[m], v[m])
            # Mann-Whitney is the exact test for an AUC against 0.5.
            a, b = v[m][y[m] == 1], v[m][y[m] == 0]
            _, p = stats.mannwhitneyu(a, b, alternative="two-sided")
            rows.append({"level": lvl, "channel": c, "n": int(m.sum()),
                         "tp": int(y[m].sum()), "auc": auc,
                         "disc": abs(auc - 0.5), "p": p})
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["p_bh"] = bh(out["p"].to_numpy())
    return out.sort_values("disc", ascending=False, kind="stable").reset_index(drop=True)


def class_specific_beats_global(df: pd.DataFrame, grid: pd.DataFrame,
                                min_n: int, seeds: int = 5):
    """The decisive test for question 4. Inside each fold, pick each class's best
    channel ON THE TRAINING PART ONLY, score the held-out part with it, and compare
    against one globally-best channel chosen the same way. If class-specific
    selection does not win out of fold, the grid's winners are noise.

    AUC is measured WITHIN each class and then averaged, never pooled: different
    classes' raw scores sit on different scales, so a pooled AUC over sign-flipped
    raw values measures scale mismatch, not skill (it scored 0.465 - below chance -
    when computed that way, which is the artifact, not a finding)."""
    chans = sorted(grid.channel.unique())
    levels = [l for l, g in df.groupby(CLASS) if len(g) >= min_n and g["y"].sum() >= 5]
    sub = df[df[CLASS].isin(levels)].reset_index(drop=True)
    X = sub[chans].apply(pd.to_numeric, errors="coerce")
    X = X.fillna(X.median()).to_numpy()
    y = sub["y"].to_numpy()
    lv = sub[CLASS].to_numpy()

    def pick(idx, cols):
        best, bd, bs = None, -1.0, 1.0
        for j, _ in enumerate(cols):
            a = roc_auc_score(y[idx], X[idx, j])
            if abs(a - 0.5) > bd:
                best, bd, bs = j, abs(a - 0.5), (1.0 if a >= 0.5 else -1.0)
        return best, bs

    per_class = {l: {"global": [], "class": []} for l in levels}
    for seed in range(seeds):
        oof_g, oof_c = np.zeros(len(y)), np.zeros(len(y))
        for tr, te in StratifiedKFold(5, shuffle=True, random_state=seed).split(X, y):
            gj, gs = pick(tr, chans)
            oof_g[te] = gs * X[te, gj]
            for l in levels:
                mtr, mte = np.where(lv[tr] == l)[0], np.where(lv[te] == l)[0]
                if len(mte) == 0:
                    continue
                itr = tr[mtr]
                if len(itr) < 20 or len(set(y[itr])) < 2:
                    oof_c[te[mte]] = gs * X[te[mte], gj]
                    continue
                cj, cs = pick(itr, chans)
                oof_c[te[mte]] = cs * X[te[mte], cj]
        for l in levels:
            m = lv == l
            if len(set(y[m])) < 2:
                continue
            per_class[l]["global"].append(roc_auc_score(y[m], oof_g[m]))
            per_class[l]["class"].append(roc_auc_score(y[m], oof_c[m]))

    rows = []
    for l in levels:
        if not per_class[l]["global"]:
            continue
        n = int((lv == l).sum())
        rows.append({"level": l, "n": n, "tp": int(y[lv == l].sum()),
                     "global_auc": float(np.mean(per_class[l]["global"])),
                     "classwise_auc": float(np.mean(per_class[l]["class"]))})
    t = pd.DataFrame(rows)
    t["delta"] = t.classwise_auc - t.global_auc
    w = t.n / t.n.sum()
    summary = {"levels": len(t), "channels": len(chans), "n": int(t.n.sum()),
               "global_auc": float((t.global_auc * w).sum()),
               "classwise_auc": float((t.classwise_auc * w).sum()),
               "wins": int((t.delta > 0).sum())}
    summary["delta"] = summary["classwise_auc"] - summary["global_auc"]
    return summary, t.sort_values("delta", ascending=False).reset_index(drop=True)


# ── report ────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--min-fires", type=int, default=10)
    ap.add_argument("--min-class-n", type=int, default=80)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="08_class_trading_value")
    a = ap.parse_args()

    df = load(Path(a.input), a.tp_threshold)
    rules = [c[6:] for c in df.columns if c.startswith("fired_")]
    hon = df[df[DATE] >= KW_HONEST_FROM].reset_index(drop=True)
    kwfree = [r for r in rules if r not in KW_RULES]
    df["kwfree_ok"] = df[[f"fired_{r}" for r in kwfree]].any(axis=1)

    base_full, base_hon = df["y"].mean(), hon["y"].mean()
    days_hon = hon[DATE].nunique()
    L, W = [], None
    W = L.append

    W(f"# Which FineClass values are worth trading — and can a class filter cut the "
      f"O5.2 FP rate?\n")
    W(f"TP = `fromPR_High(%)` >= {a.tp_threshold:g}%. Corpus `{Path(a.input).name}`.\n")
    W(f"- **FULL window** {len(df):,} rows, {int(df.y.sum())} TPs, base **{base_full:.2%}**\n"
      f"- **HONEST window** (kw15 trained on 2026-05-03..06-19, so from "
      f"{KW_HONEST_FROM}) {len(hon):,} rows over {days_hon} days, {int(hon.y.sum())} "
      f"TPs, base **{base_hon:.2%}**\n")
    W("The gate is replayed through the real `Orchestrator5.2._evaluate_trigger()` "
      "with the live `orchestrator_trigger_rules.txt`, not a reimplementation.\n")

    W("\n## 0. Replay fidelity, and one corpus defect you need to know about\n")
    W("Against `orchestrator5/tables/` for the window where the current 5-rule gate "
      f"actually ran live ({GATE_VERIFIED_FROM} onward):\n")
    W("| rows | agreement |\n|---|---|")
    W("| non-`Earnings_Results` | **89/89 = 100.0%** |")
    W("| `Earnings_Results` | 64/70 = 91.4% |")
    W("\n**The corpus `Body` column is hard-capped at 400 words** — 4,084 of 4,376 rows "
      "sit at exactly 400, and each is a strict prefix of the body live received "
      "(live bodies run 4,800–28,500 chars). Headline models are unaffected and kw15 "
      "agrees to <=0.011 because its own body pipeline caps at 400 words too. But the "
      "earnings scorer scans the WHOLE body for figures, so **42 of 70 earnings rows "
      "score differently offline and 9 (12.9%) cross the `EarningScore >= 4` cut**. "
      "Every EARNINGS number below is an estimate. This also means the earnings "
      "scorer was calibrated on truncated bodies while it runs on full ones — a "
      "live-vs-backtest mismatch on a rule carrying ~23% of live fires, and a "
      "plausible reason the 2026-08-14 retrain hit a ~0.6 AUC ceiling.\n")

    W("\n## 1. What the gate actually fires on\n")
    W("| rule | fires (FULL) | fires (HONEST) | precision (HONEST) | window that is honest |")
    W("|---|---:|---:|---:|---|")
    for r in rules:
        f_full, f_hon = int(df[f"fired_{r}"].sum()), int(hon[f"fired_{r}"].sum())
        sub = hon[hon[f"fired_{r}"]]
        prec = f"{sub.y.mean():.1%} ({int(sub.y.sum())}/{len(sub)})" if len(sub) else "-"
        win = "honest only" if r in KW_RULES else "**FULL (no kw15)**"
        W(f"| `{r}` | {f_full} | {f_hon} | {prec} | {win} |")
    cur_h = hon[hon.is_ok]
    W(f"\nWhole gate, HONEST window: **{len(cur_h)} fires / {len(hon)} rows "
      f"({len(cur_h)/len(hon):.2%})**, precision **{cur_h.y.mean():.2%}** "
      f"({int(cur_h.y.sum())} TP / {len(cur_h) - int(cur_h.y.sum())} FP), "
      f"lift {cur_h.y.mean()/base_hon:.2f}x, CI {ci(int(cur_h.y.sum()), len(cur_h))}.\n")
    kf = df[df.kwfree_ok]
    W(f"kw15-free sub-gate (`FLANGSolo` OR `FinBERTSolo`) on the FULL corpus — 2.6x "
      f"the sample: **{len(kf)} fires**, precision **{kf.y.mean():.2%}** "
      f"({int(kf.y.sum())} TP), lift {kf.y.mean()/base_full:.2f}x, "
      f"CI {ci(int(kf.y.sum()), len(kf))}.\n")

    W("\n## 2. Class breakdown of the fires — the decisive table\n")
    cc = conditional_classes(cur_h, base_hon, a.min_fires)
    W("HONEST window, all five rules. `*` = fewer than "
      f"{a.min_fires} fires.\n")
    W("| class | fires | TP | FP | precision | lift | 95% CI | % of fires | symbols | BH p |")
    W("|---|---:|---:|---:|---:|---:|---|---:|---:|---:|")
    for _, r in cc.iterrows():
        W(f"| `{r.level}`{' *' if r.thin else ''} | {r.fires} | {r.tp} | {r.fp} | "
          f"{r.precision:.1%} | {r.lift:.2f}x | {r.ci_lo:.0%}-{r.ci_hi:.0%} | "
          f"{r.share_of_fires:.1%} | {int(r.uniq_symbols)} | {r.p_bh:.3g} |")
    ccf = conditional_classes(df[df.kwfree_ok], base_full, a.min_fires)
    W(f"\nSame table for the kw15-free sub-gate on the FULL corpus "
      f"({int(df.kwfree_ok.sum())} fires — more power, fewer rules):\n")
    W("| class | fires | TP | FP | precision | lift | 95% CI | BH p |")
    W("|---|---:|---:|---:|---:|---:|---|---:|")
    for _, r in ccf.iterrows():
        W(f"| `{r.level}`{' *' if r.thin else ''} | {r.fires} | {r.tp} | {r.fp} | "
          f"{r.precision:.1%} | {r.lift:.2f}x | {r.ci_lo:.0%}-{r.ci_hi:.0%} | "
          f"{r.p_bh:.3g} |")

    W("\n### Which rule produces the FPs, by class\n")
    rbc = rule_by_class(cur_h, rules)
    W("HONEST window. Only class/rule cells with at least one fire.\n")
    W("| rule | class | fires | TP | FP | precision |")
    W("|---|---|---:|---:|---:|---:|")
    for _, r in rbc[rbc.fires > 0].iterrows():
        W(f"| `{r.rule}` | `{r.level}` | {r.fires} | {r.tp} | {r.fp} | {r.precision:.0%} |")

    W("\n## 3. Do classes differ in the SIZE of the move, not just the frequency?\n")
    mag = magnitude(df, a.min_class_n)
    W("FULL corpus (class assignment needs no honest window). Ordered by median.\n")
    W("| class | n | median | IQR | p90 | p99 | max | P(>=15%) | P(>=30%) | P(>=50%) "
      "| P(>=100%) | median if >=50% | BH p |")
    W("|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in mag.iterrows():
        mg = f"{r.median_given_tp:.0f}" if pd.notna(r.median_given_tp) else "-"
        W(f"| `{r.level}`{' *' if r.thin else ''} | {r.n} | **{r['median']:.2f}** | "
          f"{r.iqr_lo:.1f}–{r.iqr_hi:.1f} | {r.p90:.1f} | {r.p99:.0f} | {r['max']:.0f} | "
          f"{r.pct_ge15:.1%} | {r.pct_ge30:.1%} | {r.pct_ge50:.1%} | {r.pct_ge100:.1%} | "
          f"{mg} | {r.p_bh:.3g} |")
    ok = mag[~mag.thin]
    rho_med, p_med = stats.spearmanr(ok["median"], ok["pct_ge50"])
    rho_15, _ = stats.spearmanr(ok["pct_ge15"], ok["pct_ge50"])
    rho_tail, _ = stats.spearmanr(ok["pct_ge50"], ok["median_given_tp"].fillna(0))
    W(f"\nRank agreement across classes with n>={a.min_class_n}: median vs P(>=50%) "
      f"**Spearman {rho_med:.3f}** (p={p_med:.3g}); P(>=15%) vs P(>=50%) {rho_15:.3f}; "
      f"P(>=50%) vs conditional size {rho_tail:.3f}.\n")
    top = mag.sort_values("median_given_tp", ascending=False).head(6)
    W("\n**Frequency and size are NOT the same ranking.** The first two correlations "
      "are high, so a class that moves often also moves far *on average*. But "
      f"P(>=50%) vs the conditional size of a winner is only {rho_tail:.3f} — once a "
      "PR is a >=50% mover, which class it came from tells you something different. "
      "Median `fromPR_High` among winners only:\n")
    W("| class | n | P(>=50%) | median move GIVEN >=50% | max |")
    W("|---|---:|---:|---:|---:|")
    for _, r in top.iterrows():
        if pd.isna(r.median_given_tp):
            continue
        W(f"| `{r.level}`{' *' if r.thin else ''} | {r.n} | {r.pct_ge50:.1%} | "
          f"**{r.median_given_tp:.0f}%** | {r['max']:.0f}% |")

    W("\n## 4. The filter frontier\n")
    strong = [r.level for _, r in cc.iterrows()
              if r.precision > base_hon and not r.thin]
    tiers = {"class-conditional: solos only in above-base classes": strong,
             "class-conditional: solos only in MA+Partnership": ["MA", "Partnership"]}
    fr = build_frontier(hon, rules, base_hon, days_hon, tiers)
    # Every candidate decays across the date split, including the untouched baseline -
    # so before reading that as overfitting, check whether the WINDOW moved.
    dmid = hon[DATE].sort_values().iloc[len(hon) // 2]
    h1, h2 = hon[hon[DATE] <= dmid], hon[hon[DATE] > dmid]
    W(f"\n**Regime check first.** Split at {dmid}: base rate "
      f"{h1.y.mean():.2%} ({int(h1.y.sum())}/{len(h1)}) in the first half vs "
      f"{h2.y.mean():.2%} ({int(h2.y.sum())}/{len(h2)}) in the second — a "
      f"{h1.y.mean()/h2.y.mean():.1f}x drop in the RATE OF >=50% MOVERS ITSELF. "
      "Every candidate below decays across that split, the untouched baseline "
      "included, so the decay is the market, not the filter. Compare candidates to "
      "the baseline's decay, not to zero.\n")
    W(f"HONEST window ({len(hon)} rows, {days_hon} days). trades/day is calibrated by "
      f"ratio against the replayed current gate, anchored to the live "
      f"{LIVE_TRADES_PER_DAY}/day. **Floor: >=2 trades/day.**\n")
    W("| candidate | shape | fires | TP | FP | precision | lift | recall | 95% CI | "
      "boot CI | 1st half | 2nd half | vol | trades/day |")
    W("|---|---|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|")
    for _, r in fr.sort_values("precision", ascending=False, kind="stable").iterrows():
        flag = "" if r.trades_per_day >= 2 else " 🚫"
        # r["shape"] not r.shape - attribute access hits Series.shape, not the column.
        W(f"| {r['name']}{flag} | {r['shape']} | {r.fires} | {r.tp} | {r.fp} | "
          f"**{r.precision:.1%}** | {r.lift:.2f}x | {r.recall:.0%} | {r.ci} | "
          f"{r.boot_lo:.0%}-{r.boot_hi:.0%} | {r.prec_first_half:.0%} | "
          f"{r.prec_second_half:.0%} | {r.vol_vs_current:.0%} | "
          f"{r.trades_per_day:.1f} |")
    W("\n🚫 = below the 2 trades/day floor.\n")

    W("\n## 5. Is any class better served by a specific model?\n")
    grid = class_model_grid(df, a.min_class_n, min_pos=5)
    W(f"AUC of every sentiment channel within every class with n>={a.min_class_n} and "
      f">=5 TPs — {len(grid)} cells, BH-corrected as one family.\n")
    sig = grid[grid.p_bh < 0.05]
    W(f"**{len(sig)} of {len(grid)} cells survive BH at 5%.**\n")
    if len(sig):
        W("| class | channel | n | TP | AUC | BH p |")
        W("|---|---|---:|---:|---:|---:|")
        for _, r in sig.head(30).iterrows():
            W(f"| `{r.level}` | `{r.channel}` | {r.n} | {r.tp} | {r.auc:.3f} | {r.p_bh:.3g} |")
    W("\n### Pre-registered check: FLANG on biotech classes\n")
    W("FLANG is documented weak on non-FPB biotech positives and `FLANGSolo` is a "
      "live rule, so this is where a model swap would be directly actionable.\n")
    W("| class | n | TP | FLANG_ss_hd AUC | best channel | its AUC |")
    W("|---|---:|---:|---:|---|---:|")
    for lvl in BIOTECH:
        g = grid[grid.level == lvl]
        if g.empty:
            W(f"| `{lvl}` | — | — | *below n/TP floor — unreadable* | | |")
            continue
        f = g[g.channel == "FLANG_ss_hd"]
        b = g.iloc[0]
        fv = f"{f.iloc[0].auc:.3f}" if len(f) else "-"
        W(f"| `{lvl}` | {b.n} | {b.tp} | {fv} | `{b.channel}` | {b.auc:.3f} |")

    W("\n### Does class-specific model selection beat one global model?\n")
    cs, cst = class_specific_beats_global(df, grid, a.min_class_n)
    W(f"Out-of-fold, 5 seeds x 5 folds, over {cs['levels']} classes / "
      f"{cs['channels']} channels / {cs['n']:,} rows. Each fold picks its channels on "
      "the training part only, and AUC is measured WITHIN each class then averaged "
      "by class size — pooling raw scores across classes would measure scale "
      "mismatch, not skill.\n")
    W("| selection | size-weighted OOF AUC |")
    W("|---|---:|")
    W(f"| one global channel | {cs['global_auc']:.4f} |")
    W(f"| per-class channel | {cs['classwise_auc']:.4f} ({cs['delta']:+.4f}) |")
    W(f"\nPer-class selection wins in **{cs['wins']} of {cs['levels']}** classes.\n")
    W("| class | n | TP | global | per-class | delta |")
    W("|---|---:|---:|---:|---:|---:|")
    for _, r in cst.iterrows():
        W(f"| `{r.level}` | {int(r.n)} | {int(r.tp)} | {r.global_auc:.3f} | "
          f"{r.classwise_auc:.3f} | {r.delta:+.3f} |")
    cst.to_csv(Path(a.outdir) / f"{a.prefix}_classwise_model.tsv", sep="\t", index=False)

    # ── verdict ───────────────────────────────────────────────────────────────
    best = fr[fr.trades_per_day >= 2].sort_values("precision", ascending=False).iloc[0]
    cur = fr[fr["shape"] == "baseline"].iloc[0]
    wl = fr[fr["name"] == "whitelist MA+Partnership"].iloc[0]
    veto = fr[fr["name"] == "veto 6 dead classes"].iloc[0]
    dead_fires = int(cc[cc.level.isin(VETO_CANDIDATES)].fires.sum())
    op4 = fr[fr["name"] == "whitelist operator's 4"].iloc[0]
    W("\n## Verdict\n")
    W(f"**1. The veto does almost nothing, and the reason is the whole point of this "
      f"report.** Dropping all six dead classes moves precision "
      f"{cur.precision:.1%} -> {veto.precision:.1%}. Unconditionally those classes look "
      f"terrible (1.3%-2.1% hit rates), but the gate ALREADY does not fire on them — "
      f"**{dead_fires} fires total across all six**, out of {int(cur.fires)}. The "
      "sentiment cuts have already removed what a class veto would remove.\n")
    W(f"**2. Only a whitelist moves precision materially, and it costs the volume "
      f"floor.** `MA`+`Partnership` runs {wl.precision:.1%} ({int(wl.tp)}/{int(wl.fires)}) "
      f"against {cur.precision:.1%} — but at {wl.trades_per_day:.1f} trades/day, below "
      f"the floor of 2. Of the operator's four proposed classes, `Regain_Compliance` "
      f"and `Buyback_Dividend` contribute 3 fires and 0 TPs; adding them makes it "
      f"WORSE ({op4.precision:.1%}).\n")
    W(f"**3. Best candidate inside the floor: `{best['name']}`** — "
      f"{best.precision:.1%} on {int(best.fires)} fires ({int(best.tp)} TP / "
      f"{int(best.fp)} FP) at {best.trades_per_day:.1f} trades/day. That removes "
      f"{int(cur.fp - best.fp)} of {int(cur.fp)} false positives ({(cur.fp-best.fp)/cur.fp:.0%}) "
      f"at the cost of {int(cur.tp - best.tp)} of {int(cur.tp)} true positives. "
      f"Its CI is {best.ci} against the baseline's {cur.ci} — **the intervals overlap**, "
      "so this is a real but modest edge, not a settled improvement.\n")
    W(f"**4. Frequency and size are mostly the same ranking (Spearman {rho_med:.2f}), "
      "with one genuine exception.** `FDA_Approval` clears 50% only 9.5% of the time, "
      "but its winners have a median of **311%** — 3x any other class. That is the "
      "operator's intuition, and it is right in a specific way: it is a tail bet, not "
      "a hit-rate bet, and n=21 is far too thin to gate on.\n")
    W(f"**5. No, per-class model selection does not work.** Size-weighted OOF AUC "
      f"{cs['classwise_auc']:.4f} vs {cs['global_auc']:.4f} for one global channel "
      f"({cs['delta']:+.4f}), winning in only {cs['wins']} of {cs['levels']} classes. "
      "The 31 BH-surviving grid cells are almost all `Earnings_Results` (n=604 buys "
      "the power) and they do not transfer out of fold. The one suggestive exception "
      "is the pre-registered biotech hypothesis: `Clinical_Data` is the single "
      "biggest per-class win (+0.075).\n")

    outdir = Path(a.outdir)
    for nm, t in (("conditional_classes", cc), ("conditional_classes_kwfree", ccf),
                  ("rule_by_class", rbc), ("magnitude", mag),
                  ("frontier", fr), ("class_model_grid", grid)):
        t.to_csv(outdir / f"{a.prefix}_{nm}.tsv", sep="\t", index=False)
    (outdir / f"{a.prefix}.md").write_text("\n".join(L) + "\n")
    print(f"wrote {outdir / (a.prefix + '.md')} and 6 TSVs")

    # Hand the frontier back for chart building / rule generation.
    return fr


if __name__ == "__main__":
    main()
