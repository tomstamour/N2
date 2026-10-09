#!/usr/bin/env python3
"""How much of FromPRhigh(%) >= TP is explained by BroadClass / FineClass alone?

Companion to analyze_fromprhigh_sentiment.py, which treats the two class columns
as ordinary features. This one asks the narrower question directly:

  * per-level hit rate, lift, Wilson CI, Fisher-exact vs the rest (BH-corrected)
  * how much of the TP mass each level carries (recall / coverage)
  * class-ONLY discrimination: out-of-fold AUC of a one-hot logistic, Cramer's V,
    chi-square over the full contingency table
  * marginal value: does class add anything on top of the sentiment channels?

Class labels are rule/regex-assigned upstream and are not fit on the outcome, so
unlike kw15 they need no honest-window carve-out - every row is scored.

  /home/tom/venv/bin/python analyze_class_predictive_power.py \
      --input outputs/07_PRstats_..._BodyEnc.tsv --tp-threshold 50
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

TARGET_COL = "fromPR_High(%)"
DATE_COL = "ArrivalDate"
CLASS_COLS = ["BroadClass", "FineClass"]
SCORE_RE = re.compile(r"_(neg|neu|ind|pos|ss|unc|lit|smod|wmod|cons)_(hd|bd)$")


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - h) / d, (c + h) / d)


def bh(pvals: np.ndarray) -> np.ndarray:
    """Benjamini-Hochberg adjusted p-values."""
    n = len(pvals)
    order = np.argsort(pvals)
    adj = np.empty(n)
    prev = 1.0
    for rank, i in enumerate(reversed(order), start=1):
        prev = min(prev, pvals[i] * n / (n - rank + 1))
        adj[i] = prev
    return adj


def load(path: Path, tp: float) -> pd.DataFrame:
    df = pd.read_csv(path, sep="\t", dtype=str, low_memory=False)
    df[TARGET_COL] = pd.to_numeric(df[TARGET_COL], errors="coerce")
    df = df[df[TARGET_COL].notna()].copy()
    df["y"] = (df[TARGET_COL] >= tp).astype(int)
    return df.reset_index(drop=True)


def level_table(df: pd.DataFrame, col: str) -> pd.DataFrame:
    base = df["y"].mean()
    tot_tp = int(df["y"].sum())
    rows = []
    for lvl, g in df.groupby(col, dropna=False):
        n, k = len(g), int(g["y"].sum())
        # Fisher exact: this level vs every other level of the same column
        rest_n, rest_k = len(df) - n, tot_tp - k
        _, p = stats.fisher_exact([[k, n - k], [rest_k, rest_n - rest_k]])
        lo, hi = wilson(k, n)
        rows.append({
            "column": col, "level": str(lvl), "n": n, "tp": k,
            "precision": k / n, "lift": (k / n) / base if base else np.nan,
            "ci_lo": lo, "ci_hi": hi,
            "share_of_rows": n / len(df), "share_of_tp": k / tot_tp if tot_tp else np.nan,
            "median_high": g[TARGET_COL].median(),
            "p90_high": g[TARGET_COL].quantile(0.90),
            "uniq_symbols": g["Symbol"].nunique() if "Symbol" in g else np.nan,
            "p_fisher": p,
        })
    out = pd.DataFrame(rows).sort_values("precision", ascending=False, kind="stable")
    out["p_bh"] = bh(out["p_fisher"].to_numpy())
    return out.reset_index(drop=True)


def cramers_v(df: pd.DataFrame, col: str) -> tuple[float, float, int]:
    tab = pd.crosstab(df[col], df["y"])
    chi2, p, dof, _ = stats.chi2_contingency(tab)
    n = tab.to_numpy().sum()
    v = math.sqrt(chi2 / (n * (min(tab.shape) - 1)))
    return v, p, dof


def oof_auc(X: np.ndarray, y: np.ndarray, seeds: int = 5) -> tuple[float, float]:
    """Out-of-fold AUC of an L2 logistic on X. Repeated stratified 5-fold."""
    aucs = []
    for seed in range(seeds):
        oof = np.zeros(len(y))
        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=seed)
        for tr, te in skf.split(X, y):
            m = LogisticRegression(max_iter=2000, C=1.0)
            m.fit(X[tr], y[tr])
            oof[te] = m.predict_proba(X[te])[:, 1]
        aucs.append(roc_auc_score(y, oof))
    return float(np.mean(aucs)), float(np.std(aucs))


def sentiment_matrix(df: pd.DataFrame) -> tuple[np.ndarray, list[str]]:
    cols = [c for c in df.columns if SCORE_RE.search(c)]
    X = df[cols].apply(pd.to_numeric, errors="coerce")
    keep = [c for c in cols if X[c].notna().mean() > 0.9 and X[c].nunique() > 1]
    X = X[keep].fillna(X[keep].median())
    return ((X - X.mean()) / X.std(ddof=0)).fillna(0.0).to_numpy(), keep


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--min-n", type=int, default=20,
                    help="levels below this are reported but flagged thin")
    ap.add_argument("--cv-repeats", type=int, default=5)
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default="07_class_power")
    a = ap.parse_args()

    df = load(Path(a.input), a.tp_threshold)
    base = df["y"].mean()
    outdir = Path(a.outdir)
    L = []
    W = L.append

    W(f"# BroadClass / FineClass predictive power - FromPRhigh(%) >= {a.tp_threshold:g}%\n")
    W(f"Corpus `{Path(a.input).name}` - {len(df):,} rows with a FromPRhigh value, "
      f"{int(df['y'].sum())} TPs, base rate **{base:.2%}**.\n")
    W("Classes are regex/rule-assigned upstream and never fit on the outcome, so "
      "every row is in scope (no kw15-style honest window).\n")

    tables = {}
    W("\n## 1. Overall discrimination - class alone\n")
    W("| column | levels | Cramer's V | chi2 p | one-hot OOF AUC |")
    W("|---|---:|---:|---:|---:|")
    for col in CLASS_COLS:
        v, p, _ = cramers_v(df, col)
        D = pd.get_dummies(df[col], drop_first=True).to_numpy(float)
        auc, sd = oof_auc(D, df["y"].to_numpy(), a.cv_repeats)
        W(f"| `{col}` | {df[col].nunique()} | {v:.3f} | {p:.3g} | {auc:.4f} +/- {sd:.4f} |")
    W("\nAUC 0.50 = no information. Cramer's V is the effect size of the whole "
      "contingency table (0 = independent, 1 = deterministic).\n")

    W("\n## 2. Marginal value on top of sentiment\n")
    S, scols = sentiment_matrix(df)
    y = df["y"].to_numpy()
    base_auc, base_sd = oof_auc(S, y, a.cv_repeats)
    W(f"| feature set | cols | OOF AUC |")
    W("|---|---:|---:|")
    W(f"| sentiment only | {len(scols)} | {base_auc:.4f} +/- {base_sd:.4f} |")
    for col in CLASS_COLS:
        D = pd.get_dummies(df[col], drop_first=True).to_numpy(float)
        auc, sd = oof_auc(np.hstack([S, D]), y, a.cv_repeats)
        W(f"| sentiment + `{col}` | {len(scols) + D.shape[1]} | {auc:.4f} +/- {sd:.4f} "
          f"({auc - base_auc:+.4f}) |")
    D = np.hstack([pd.get_dummies(df[c], drop_first=True).to_numpy(float)
                   for c in CLASS_COLS])
    auc, sd = oof_auc(np.hstack([S, D]), y, a.cv_repeats)
    W(f"| sentiment + both | {len(scols) + D.shape[1]} | {auc:.4f} +/- {sd:.4f} "
      f"({auc - base_auc:+.4f}) |")

    for n_sec, col in enumerate(CLASS_COLS, start=3):
        t = level_table(df, col)
        tables[col] = t
        W(f"\n## {n_sec}. `{col}` - every level\n")
        W("| level | n | TP | hit rate | lift | 95% CI | % of all rows | % of all TPs "
          "| median | p90 | symbols | BH p |")
        W("|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|")
        for _, r in t.iterrows():
            thin = " *" if r.n < a.min_n else ""
            sig = "**" if r.p_bh < 0.05 else ""
            W(f"| `{r.level}`{thin} | {r.n} | {r.tp} | {sig}{r.precision:.2%}{sig} | "
              f"{r.lift:.2f}x | {r.ci_lo:.1%}-{r.ci_hi:.1%} | {r.share_of_rows:.1%} | "
              f"{r.share_of_tp:.1%} | {r.median_high:.2f} | {r.p90_high:.1f} | "
              f"{int(r.uniq_symbols)} | {r.p_bh:.3g} |")
        W(f"\n`*` = fewer than {a.min_n} rows, CI too wide to act on. "
          "**bold** = Fisher-exact vs all other levels survives BH at 5%.\n")

    W(f"\n## {3 + len(CLASS_COLS)}. Concentration - how far does the top of the "
      "ranking take you\n")
    for col in CLASS_COLS:
        t = tables[col][tables[col].n >= a.min_n].sort_values(
            "precision", ascending=False, kind="stable")
        cum_n = t.n.cumsum()
        cum_tp = t.tp.cumsum()
        W(f"\n**{col}** (levels with n >= {a.min_n}, best hit rate first)\n")
        W("| through level | cum rows | cum TPs | cum hit rate | cum lift | recall |")
        W("|---|---:|---:|---:|---:|---:|")
        for i, (_, r) in enumerate(t.iterrows()):
            prec = cum_tp.iloc[i] / cum_n.iloc[i]
            W(f"| `{r.level}` | {cum_n.iloc[i]} | {cum_tp.iloc[i]} | {prec:.2%} | "
              f"{prec / base:.2f}x | {cum_tp.iloc[i] / df['y'].sum():.1%} |")

    all_t = pd.concat(tables.values(), ignore_index=True)
    tsv = outdir / f"{a.prefix}.tsv"
    all_t.to_csv(tsv, sep="\t", index=False)
    md = outdir / f"{a.prefix}.md"
    md.write_text("\n".join(L) + "\n")
    print(f"wrote {md}\nwrote {tsv}")


if __name__ == "__main__":
    main()
