#!/usr/bin/env python3
"""Rank FineClass / BroadClass by BIG-MOVER HIT-RATE on fromPR_High(%).

Unlike class_conditional_predictors.py (per-class AUC of encoder columns),
this script treats the CLASS ITSELF as the predictor and asks the precision
question directly:

    inside class C, what fraction of PRs reached fromPR_High(%) >= threshold,
    and is that reliably above the corpus base rate?

Three sections:

  1. Class ranking — for BroadClass and FineClass: n, movers, hit-rate,
     lift vs base rate, 95% Wilson CI on the hit-rate, one-vs-rest Fisher
     exact p and its Benjamini-Hochberg FDR q. Classes below --min-n are
     kept but flagged `n<MIN` and excluded from the FDR family (their CI is
     too wide to rank on). Sorted by the LOWER Wilson bound, not the point
     estimate, so a 3/4 = 75% class cannot outrank a 60/200 = 30% one.
     Both the above-base-rate and the below-base-rate (veto candidate)
     tails are reported.

  2. FineClass nested inside BroadClass — where a broad class hides a
     strong or dead sub-class.

  3. Class x encoder interaction — within each class that clears --min-n
     and --min-pos, every auto-detected encoder score channel is scored by
     within-class AUC, and the best actionable cut (a within-class quantile
     from --cut-quantiles, either direction) is reported with its hit-rate,
     lift over the CLASS base rate, Wilson CI and BH-FDR q over the whole
     (class x column x direction x cut) family. This answers "which gate
     condition works in which class", which a global AUC ranking hides.

Columns are auto-oriented: a channel whose full-corpus AUC is < 0.5 is
negated everywhere and labeled `col(inv)`. A within-class AUC < 0.5 after
that means the signal REVERSES inside that class.

Pure numpy / pandas / scipy — no sklearn, no matplotlib.

Run:
    /home/tom/venv/bin/python class_hitrate_ranking.py \
        ../outputs/04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_\
11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv --threshold 30
"""
import argparse
import math
import pathlib
import sys

import numpy as np
import pandas as pd
from scipy.stats import rankdata, fisher_exact

TARGET_DEFAULT = "fromPR_High(%)"
TSV_DEFAULT = ("../outputs/04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_"
               "11EncScored_Classed_BodyEnc_clean_FromPRhigh.tsv")

# Encoder score channels tested in the interaction section. Since the 2026-08-09
# column unification the channel is an INFIX, not a suffix — `_hd`/`_bd` is
# terminal — so these match `_<channel>_` anywhere and pick up head and body twins
# alike, without editing a list when a new encoder block is added.
CHANNEL_INFIXES = ("_pos_", "_neg_", "_neu_", "_ind_", "_ss_")
# ProsusAI FinBERT no longer needs listing here: its columns used to be the bare
# `positive` / `sentiment_score` / … and are now ordinary `Prosus_*_hd` members of
# the infix match above.
EXTRA_COLUMNS = ["nocoref_neutral_filter", "nocoref_confidence_weighted",
                 "nocoref_net_score", "nocoref_top_k", "nocoref_positional"]
# Never predictors (outcome / leakage / operational).
BLOCKLIST = {"fromPR_High(%)", "fromPR_HighTime(m:s)", "fromPR_status",
             "DailyHigh(%)", "DailyHigh($)", "Trades/sec", "Trigger",
             "Sentiment", "ClerkArm", "ClerkSentiment", "body_sentiment"}


# ----------------------------- stats helpers --------------------------------
def auc_score(scores, labels):
    """ROC AUC via the Mann-Whitney rank formula (handles ties)."""
    labels = np.asarray(labels).astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    return (ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def wilson(k, n, z=1.959963985):
    """95% Wilson score interval for a binomial proportion."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def bh_fdr(pvals):
    """Benjamini-Hochberg q-values, same order as the input."""
    p = np.asarray(pvals, dtype=float)
    n = len(p)
    if n == 0:
        return np.array([])
    order = np.argsort(p)
    ranked = p[order] * n / (np.arange(n) + 1)
    ranked = np.minimum.accumulate(ranked[::-1])[::-1]
    q = np.empty(n)
    q[order] = np.clip(ranked, 0, 1)
    return q


def fisher_p(k_in, n_in, k_out, n_out):
    """Two-sided Fisher exact on class-vs-rest movers."""
    table = [[k_in, n_in - k_in], [k_out, n_out - k_out]]
    try:
        return float(fisher_exact(table)[1])
    except Exception:
        return float("nan")


# ----------------------------- data -----------------------------------------
def load_and_clean(tsv_path, target, max_outlier, date_from):
    df = pd.read_csv(tsv_path, sep="\t", dtype=str, low_memory=False)
    if target not in df.columns:
        sys.exit(f"target column {target!r} not in {tsv_path}")
    n_total = len(df)
    df[target] = pd.to_numeric(df[target], errors="coerce")
    df = df.dropna(subset=[target])
    n_null = n_total - len(df)

    n_out = 0
    if max_outlier is not None:
        n_out = int((df[target] > max_outlier).sum())
        df = df[df[target] <= max_outlier]

    n_date = 0
    if date_from and "ArrivalDate" in df.columns:
        before = len(df)
        df = df[df["ArrivalDate"] >= date_from]
        n_date = before - len(df)

    dedup_keys = [c for c in ("Symbol", "Headline", "ArrivalDate") if c in df.columns]
    n_dup = 0
    if dedup_keys:
        before = len(df)
        df = df.drop_duplicates(subset=dedup_keys, keep="first")
        n_dup = before - len(df)

    stats = dict(n_total=n_total, n_null=n_null, n_out=n_out,
                 n_date=n_date, n_dup=n_dup, n_kept=len(df))
    return df.reset_index(drop=True), stats


def detect_channels(df, min_valid):
    """Auto-detect numeric encoder score columns."""
    cols = []
    for c in df.columns:
        if c in BLOCKLIST or c in cols:
            continue
        if any(inf in c for inf in CHANNEL_INFIXES) or c in EXTRA_COLUMNS:
            v = pd.to_numeric(df[c], errors="coerce")
            if v.notna().sum() >= min_valid and v.nunique(dropna=True) > 2:
                cols.append(c)
    return cols


# ----------------------------- sections -------------------------------------
def class_table(df, cls_col, y, min_n):
    """One row per level of cls_col."""
    base_k, base_n = int(y.sum()), len(y)
    rows = []
    levels = df[cls_col].fillna("<blank>")
    for lvl, idx in levels.groupby(levels).groups.items():
        m = np.zeros(len(df), dtype=bool)
        m[df.index.get_indexer(pd.Index(idx))] = True
        n_in = int(m.sum())
        k_in = int(y[m].sum())
        n_out, k_out = base_n - n_in, base_k - k_in
        lo, hi = wilson(k_in, n_in)
        rate = k_in / n_in if n_in else float("nan")
        base_rate = base_k / base_n
        rows.append(dict(
            level=str(lvl), n=n_in, movers=k_in, rate=rate,
            lift=rate / base_rate if base_rate else float("nan"),
            lo=lo, hi=hi,
            median=float(np.median(df.loc[m, "__t"])) if n_in else float("nan"),
            p=fisher_p(k_in, n_in, k_out, n_out) if n_in and n_out else float("nan"),
            small=n_in < min_n,
        ))
    tbl = pd.DataFrame(rows)
    fam = tbl.index[~tbl["small"]]
    tbl["q"] = float("nan")
    if len(fam):
        tbl.loc[fam, "q"] = bh_fdr(tbl.loc[fam, "p"].values)
    return tbl.sort_values(["small", "lo"], ascending=[True, False]).reset_index(drop=True)


def fmt_class_table(tbl, base_rate, thr):
    out = [f"| class | n | movers | hit-rate | lift | 95% Wilson CI | "
           f"median {thr:g}%-target | Fisher p | BH q | flag |",
           "|---|---:|---:|---:|---:|---|---:|---:|---:|---|"]
    for _, r in tbl.iterrows():
        flag = "n<MIN" if r["small"] else (
            "**sig+**" if (r["q"] == r["q"] and r["q"] < 0.05 and r["rate"] > base_rate)
            else ("**sig-**" if (r["q"] == r["q"] and r["q"] < 0.05) else ""))
        q_txt = "—" if r["q"] != r["q"] else f"{r['q']:.4f}"
        out.append(
            f"| {r['level']} | {r['n']} | {r['movers']} | {r['rate']*100:.1f}% | "
            f"{r['lift']:.2f}x | [{r['lo']*100:.1f}%, {r['hi']*100:.1f}%] | "
            f"{r['median']:.1f} | {r['p']:.4f} | {q_txt} | {flag} |")
    return "\n".join(out)


def interaction_section(df, y, channels, cls_cols, min_n, min_pos, quantiles,
                        min_fire, top_k, orient_margin):
    """Within-class encoder cuts. Returns (markdown, n_tests)."""
    base_rate = y.mean()
    # Orient channels on the full corpus.
    oriented = {}
    for c in channels:
        v = pd.to_numeric(df[c], errors="coerce")
        ok = v.notna().values
        if ok.sum() < min_n:
            continue
        a = auc_score(v.values[ok], y[ok])
        if a != a:
            continue
        # |corpus AUC - 0.5| < orient_margin => the SIGN of this channel is not
        # actually determined by the data; flag it rather than trust it.
        oriented[c] = (-1.0 if a < 0.5 else 1.0, a,
                       abs(a - 0.5) < orient_margin)

    cells = []
    for cls_col in cls_cols:
        levels = df[cls_col].fillna("<blank>")
        for lvl in sorted(levels.unique()):
            m = (levels == lvl).values
            n_in, k_in = int(m.sum()), int(y[m].sum())
            if n_in < min_n or k_in < min_pos:
                continue
            cls_rate = k_in / n_in
            for c, (sign, corpus_auc, weak) in oriented.items():
                v = pd.to_numeric(df[c], errors="coerce").values
                sub_ok = m & ~np.isnan(v)
                if sub_ok.sum() < min_n or int(y[sub_ok].sum()) < min_pos:
                    continue
                s = sign * v[sub_ok]
                yy = y[sub_ok]
                a_in = auc_score(s, yy)
                for q in quantiles:
                    cut = float(np.quantile(s, 1.0 - q))
                    fire = s >= cut
                    n_f, k_f = int(fire.sum()), int(yy[fire].sum())
                    if n_f < min_fire:
                        continue
                    lo, hi = wilson(k_f, n_f)
                    rate = k_f / n_f
                    cells.append(dict(
                        cls_col=cls_col, level=str(lvl), col=c,
                        inv=sign < 0, weak_orient=weak,
                        auc_corpus=corpus_auc, auc_in=a_in, q_frac=q,
                        cut=sign * cut, n_fire=n_f, movers=k_f, rate=rate,
                        cls_n=n_in, cls_rate=cls_rate,
                        lift_cls=rate / cls_rate if cls_rate else float("nan"),
                        lift_base=rate / base_rate if base_rate else float("nan"),
                        lo=lo, hi=hi,
                        p=fisher_p(k_f, n_f, k_in - k_f, n_in - n_f),
                    ))
    if not cells:
        return "_No class x encoder cell cleared the sample-size floors._", 0, None
    cel = pd.DataFrame(cells)
    cel["fdr_q"] = bh_fdr(cel["p"].values)

    parts = []
    for cls_col in cls_cols:
        sub = cel[cel["cls_col"] == cls_col]
        if sub.empty:
            continue
        parts.append(f"\n### {cls_col}\n")
        for lvl in sub.sort_values("cls_n", ascending=False)["level"].unique():
            s = sub[sub["level"] == lvl]
            best = (s.sort_values("lo", ascending=False)
                     .drop_duplicates(subset=["col"])
                     .head(top_k))
            cls_n = int(s["cls_n"].iloc[0])
            cls_rate = float(s["cls_rate"].iloc[0])
            parts.append(f"\n**{lvl}** — n={cls_n}, class hit-rate "
                         f"{cls_rate*100:.1f}% (corpus {base_rate*100:.1f}%)\n")
            parts.append("| encoder cut | corpus AUC | within-class AUC | "
                         "fires | movers | hit-rate | 95% CI | lift vs class | "
                         "lift vs corpus | Fisher p | BH q |")
            parts.append("|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|")
            for _, r in best.iterrows():
                op = "<=" if r["inv"] else ">="
                ac = (f"{r['auc_corpus']:.3f}?" if r["weak_orient"]
                      else f"{r['auc_corpus']:.3f}")
                parts.append(
                    f"| `{r['col']} {op} {abs(r['cut']):.4g}` (top {r['q_frac']*100:.0f}%) | "
                    f"{ac} | "
                    f"{r['auc_in']:.3f} | {r['n_fire']} | {r['movers']} | "
                    f"{r['rate']*100:.1f}% | [{r['lo']*100:.1f}%, {r['hi']*100:.1f}%] | "
                    f"{r['lift_cls']:.2f}x | {r['lift_base']:.2f}x | "
                    f"{r['p']:.4f} | {r['fdr_q']:.4f} |")
    return "\n".join(parts), len(cel), cel


# ----------------------------- main -----------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="?", default=TSV_DEFAULT)
    ap.add_argument("--target", default=TARGET_DEFAULT)
    ap.add_argument("--threshold", type=float, default=30.0,
                    help="big-mover cut on the target (default 30)")
    ap.add_argument("--class-cols", nargs="+", default=["BroadClass", "FineClass"])
    ap.add_argument("--min-n", type=int, default=30,
                    help="classes below this are flagged n<MIN and left out "
                         "of the FDR family")
    ap.add_argument("--min-pos", type=int, default=8,
                    help="min movers in a class for the interaction section")
    ap.add_argument("--min-fire", type=int, default=10,
                    help="min rows a within-class cut must fire on")
    ap.add_argument("--cut-quantiles", nargs="+", type=float,
                    default=[0.5, 0.33, 0.25, 0.1],
                    help="top-fraction cuts tested inside each class")
    ap.add_argument("--orient-margin", type=float, default=0.04,
                    help="a channel whose corpus AUC is within this of 0.50 "
                         "has an UNDETERMINED sign; its corpus AUC is shown "
                         "with a trailing '?' and it is skipped in the "
                         "conclusion")
    ap.add_argument("--top-k", type=int, default=5,
                    help="encoder cuts shown per class")
    ap.add_argument("--max-outlier", type=float, default=None,
                    help="drop rows with target above this (default: keep all "
                         "— a binary hit-rate is outlier-insensitive)")
    ap.add_argument("--subset-date-from", default=None,
                    help="keep only ArrivalDate >= this (YYYY-MM-DD)")
    ap.add_argument("--report-suffix", default="")
    args = ap.parse_args()

    tsv_path = pathlib.Path(args.tsv)
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")
    out_dir = tsv_path.parent if str(tsv_path.parent) else pathlib.Path(".")
    target, thr = args.target, args.threshold

    df, st = load_and_clean(tsv_path, target, args.max_outlier,
                            args.subset_date_from)
    df["__t"] = df[target]
    y = (df[target] >= thr).values
    base_k, base_n = int(y.sum()), len(y)
    base_rate = base_k / base_n

    channels = detect_channels(df, args.min_n)

    L = [f"# Class predictiveness of `{target}` >= {thr:g}%", "",
         f"Input: `{tsv_path.name}`  ",
         f"Rows: {st['n_total']} read -> {st['n_kept']} kept "
         f"(dropped {st['n_null']} null target, {st['n_out']} outlier, "
         f"{st['n_date']} out-of-date-range, {st['n_dup']} duplicate "
         "(Symbol, Headline, ArrivalDate))  ",
         f"Base rate: **{base_k}/{base_n} = {base_rate*100:.2f}%** of PRs reach "
         f"{target} >= {thr:g}%  ",
         f"Sample-size floor: n >= {args.min_n} to be ranked; smaller classes "
         "are shown but flagged `n<MIN`.  ",
         "Ranked by the LOWER 95% Wilson bound (not the point estimate), so a "
         "tiny class cannot top the table on 3/4 = 75%.  ",
         f"`sig+` / `sig-` = one-vs-rest Fisher exact survives BH-FDR at "
         "q<0.05 within the class column's family.", ""]

    for cls_col in args.class_cols:
        if cls_col not in df.columns:
            L += [f"## {cls_col}", "", "_column not in file_", ""]
            continue
        tbl = class_table(df, cls_col, y, args.min_n)
        L += [f"## {cls_col} — hit-rate ranking", "",
              fmt_class_table(tbl, base_rate, thr), ""]

    # Nested FineClass within BroadClass.
    if "BroadClass" in df.columns and "FineClass" in df.columns:
        L += ["## FineClass nested inside BroadClass", "",
              "Where a broad class hides a strong or dead sub-class.", "",
              "| BroadClass | FineClass | n | movers | hit-rate | 95% CI | "
              "lift vs broad | lift vs corpus |",
              "|---|---|---:|---:|---:|---|---:|---:|"]
        bc = df["BroadClass"].fillna("<blank>")
        fc = df["FineClass"].fillna("<blank>")
        for b in sorted(bc.unique()):
            mb = (bc == b).values
            nb, kb = int(mb.sum()), int(y[mb].sum())
            if nb < args.min_n:
                continue
            rb = kb / nb
            subs = sorted(fc[mb].unique())
            if len(subs) < 2:
                continue
            for f in subs:
                mf = mb & (fc == f).values
                nf, kf = int(mf.sum()), int(y[mf].sum())
                if nf == 0:
                    continue
                lo, hi = wilson(kf, nf)
                rf = kf / nf
                small = " `n<MIN`" if nf < args.min_n else ""
                L.append(f"| {b} | {f}{small} | {nf} | {kf} | {rf*100:.1f}% | "
                         f"[{lo*100:.1f}%, {hi*100:.1f}%] | "
                         f"{rf/rb if rb else float('nan'):.2f}x | "
                         f"{rf/base_rate:.2f}x |")
        L.append("")

    # Class x encoder interaction.
    L += ["## Class x encoder interaction", "",
          f"{len(channels)} encoder score channels auto-detected. Inside each "
          f"class with n >= {args.min_n} and >= {args.min_pos} movers, each "
          "channel is oriented on the full corpus (a channel with corpus "
          "AUC < 0.5 is negated and shown with a `<=` operator), scored by "
          "within-class AUC, and cut at within-class quantiles "
          f"{', '.join(f'top {q*100:.0f}%' for q in args.cut_quantiles)}. "
          f"Cuts firing on < {args.min_fire} rows are dropped. BH-FDR `q` is "
          "over the whole (class x channel x cut) family — treat any cell "
          "with q >= 0.05 as noise.", "",
          "**Two caveats on every number in this section.** (1) The cut shown "
          "is the best of "
          f"{len(args.cut_quantiles)} quantiles x ~{len(channels)} channels "
          "*inside that class*, so the hit-rate is the maximum of a large "
          "in-sample search and is optimistically biased even where q < 0.05 — "
          "the FDR controls the claim 'this channel separates', not the "
          "claim 'this hit-rate will replicate'. (2) The cut VALUES are "
          "within-class quantiles, so they are class-specific and are not "
          "portable global constants; a live gate leg needs the cut re-fit "
          "on a held-out period.", "",
          "A corpus AUC printed with a trailing `?` means |AUC - 0.50| < "
          f"{args.orient_margin}: the channel's SIGN is not determined by the "
          "data, so the direction of its cut is close to arbitrary. Such rows "
          "are kept in the tables but excluded from the conclusion.", ""]
    inter_md, n_tests, cel = interaction_section(
        df, y, channels, [c for c in args.class_cols if c in df.columns],
        args.min_n, args.min_pos, args.cut_quantiles, args.min_fire,
        args.top_k, args.orient_margin)
    L += [f"_{n_tests} cells tested._", inter_md, ""]

    # ------------------------- conclusion ------------------------------------
    L += ["## Conclusion", ""]
    for cls_col in args.class_cols:
        if cls_col not in df.columns:
            continue
        tbl = class_table(df, cls_col, y, args.min_n)
        sig = tbl[(tbl["q"] < 0.05) & (~tbl["small"])]
        up = sig[sig["rate"] > base_rate].sort_values("lo", ascending=False)
        dn = sig[sig["rate"] < base_rate].sort_values("rate")
        L.append(f"**{cls_col}** — {len(up)} class(es) significantly ABOVE the "
                 f"{base_rate*100:.1f}% base rate after FDR: " +
                 (", ".join(f"`{r['level']}` {r['rate']*100:.1f}% "
                            f"({r['lift']:.2f}x, n={r['n']})"
                            for _, r in up.iterrows()) or "none") + ".")
        L.append("")
        L.append(f"&nbsp;&nbsp;{len(dn)} significantly BELOW (veto "
                 "candidates): " +
                 (", ".join(f"`{r['level']}` {r['rate']*100:.1f}% "
                            f"({r['lift']:.2f}x, n={r['n']})"
                            for _, r in dn.iterrows()) or "none") + ".")
        L.append("")
    if cel is not None:
        surv = (cel[(cel["fdr_q"] < 0.05) & (~cel["weak_orient"])]
                .sort_values("lo", ascending=False))
        L += ["", f"**Class x encoder** — {len(surv)} of {len(cel)} cells "
              "survive BH-FDR at q<0.05. Strongest by lower Wilson bound "
              "(one row per class, best cut):", "",
              "| class col | class | cut | fires | hit-rate | 95% CI | "
              "lift vs class | q |", "|---|---|---|---:|---:|---|---:|---:|"]
        for _, r in surv.drop_duplicates(subset=["cls_col", "level"]).head(12).iterrows():
            op = "<=" if r["inv"] else ">="
            L.append(f"| {r['cls_col']} | {r['level']} | "
                     f"`{r['col']} {op} {abs(r['cut']):.4g}` | {r['n_fire']} | "
                     f"{r['rate']*100:.1f}% | [{r['lo']*100:.1f}%, "
                     f"{r['hi']*100:.1f}%] | {r['lift_cls']:.2f}x | "
                     f"{r['fdr_q']:.4f} |")
        L.append("")

    out = out_dir / f"fromprhigh_class_hitrate_ranking{args.report_suffix}.md"
    out.write_text("\n".join(L))
    print("\n".join(L))
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
