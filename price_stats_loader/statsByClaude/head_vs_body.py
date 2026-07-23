#!/usr/bin/env python3
"""Headline vs body: does scoring the PR body with the 11 encoders beat scoring
the headline, and does it add anything on top?

Two questions, one script, because both need the same paired machinery:

  SECTION A -- who wins each channel?
      For every `<enc>_<chan>` / `<enc>_Body_<chan>` pair, the oriented AUC of
      each and the difference, with a PAIRED BOOTSTRAP CI on that difference.
      The pairing is the whole point: both scores are computed on the same rows
      against the same labels, so the two AUCs are strongly correlated. Two
      independent Hanley-McNeil intervals would be far too wide and would call
      every real difference a tie. Resampling rows (the same resampled rows for
      both members) cancels the shared noise.

  SECTION B -- does body ADD to headline?
      Per encoder, pooled out-of-fold logistic AUC of [head, body] against each
      alone. Synergy = pair - better single. A column can lose Section A badly
      and still be worth computing if it is carrying independent information;
      Spearman(head, body) is reported alongside to show how much room there is.

ORIENTATION IS A PRIORI, NOT FITTED. Each channel gets a fixed sign from its
semantics (`positive`/`sentiment_score` -> +1; `negative`/`neutral`/
`indecisive`/the LouMcD hedging counts -> -1), applied identically to the
headline and body member of a pair. Orienting each member by its own observed
AUC (`max(a, 1-a)`) would be a winner's curse: it can only ever push an AUC up
toward 0.5+, so a pure-noise column scores ~0.51 instead of ~0.50 and the
head-body difference is biased. The a priori rule reproduces the orientations
used in report-Headline-11encoders.md (FinMarBa_indecisive and
FinancialBERT_negative both invert) without looking at the target.

MULTIPLE COMPARISONS: ~44 pairs x 2 thresholds. At a nominal 95% level, 2-4
"significant" differences are expected from noise alone, so a Bonferroni-
adjusted interval is reported next to the nominal one and only the Bonferroni
column should be read as evidence.

Pure numpy / pandas / scipy.

Run:
    /home/tom/venv/bin/python head_vs_body.py \
        ../outputs/PRstats_..._Classed_kwScored_BodyEnc.tsv
"""
import argparse
import pathlib
import sys

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.stats import norm, rankdata, spearmanr

TARGET_DEFAULT = "fromPR_High(%)"
TSV_DEFAULT = ("../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_"
               "11EncScored_Classed_kwScored_BodyEnc.tsv")
BODY_MARKER = "_Body_"

# A priori sign per channel: +1 = higher value should mean a bigger spike.
CHANNEL_SIGN = {
    "positive": +1,
    "sentiment_score": +1,
    "negative": -1,
    "neutral": -1,
    "indecisive": -1,
    "uncertainty": -1,
    "litigious": -1,
    "strong_modal": -1,
    "weak_modal": -1,
    "constraining": -1,
}
SKIP_CHANNELS = {"label", "elapsed_ms"}


# ----------------------------- metrics --------------------------------------
def auc_score(scores, labels):
    """ROC AUC via the Mann-Whitney rank formula (handles ties)."""
    labels = labels.astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    return (ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def hanley_mcneil_se(auc, n_pos, n_neg):
    """Standard error of a single AUC (used only for the standalone columns)."""
    q1 = auc / (2 - auc)
    q2 = 2 * auc ** 2 / (1 + auc)
    var = (auc * (1 - auc) + (n_pos - 1) * (q1 - auc ** 2)
           + (n_neg - 1) * (q2 - auc ** 2)) / (n_pos * n_neg)
    return float(np.sqrt(max(var, 0.0)))


def paired_bootstrap_diff(s_head, s_body, labels, n_boot, seed=42):
    """Percentile CI + SE for AUC(body) - AUC(head), resampling rows.

    Both members are evaluated on the SAME resampled rows each iteration, so the
    shared row-level noise cancels and the interval reflects only the disagreement
    between the two scores.
    """
    rng = np.random.default_rng(seed)
    n = len(labels)
    diffs = np.empty(n_boot)
    ok = 0
    for b in range(n_boot):
        idx = rng.integers(0, n, n)
        y = labels[idx]
        if y.all() or not y.any():
            continue
        diffs[ok] = auc_score(s_body[idx], y) - auc_score(s_head[idx], y)
        ok += 1
    diffs = diffs[:ok]
    if ok < 100:
        return float("nan"), float("nan"), float("nan")
    lo, hi = np.percentile(diffs, [2.5, 97.5])
    return float(lo), float(hi), float(diffs.std(ddof=1))


# ----------------------------- logistic --------------------------------------
def fit_logistic(X, y, l2=1e-3):
    """Penalized logistic regression; w[0] = intercept, X pre-standardized."""
    n, d = X.shape

    def nll(w):
        z = X @ w[1:] + w[0]
        loss = np.mean(np.logaddexp(0, z) - y * z) + l2 * np.sum(w[1:] ** 2)
        p = 1.0 / (1.0 + np.exp(-z))
        grad = np.empty_like(w)
        grad[0] = np.mean(p - y)
        grad[1:] = X.T @ (p - y) / n + 2 * l2 * w[1:]
        return loss, grad

    res = minimize(nll, np.zeros(d + 1), jac=True, method="L-BFGS-B")
    return res.x


def pct_rank(v):
    """Percentile-rank transform, NaNs preserved.

    Unsupervised (the target is never consulted), so computing it on the full
    column is not leakage -- it is the same transform the `_pctl` columns in the
    corpus already use. It exists because the logistic below is sensitive to
    heavy tails that the rank-based AUC is immune to: `LouMcD-SDict_Body_negative`
    is a raw word count with skew 3.3 and a +6.4 sigma maximum, and a couple of
    long PRs would otherwise dominate the fit.
    """
    out = np.full(len(v), np.nan)
    m = ~np.isnan(v)
    if m.sum():
        out[m] = rankdata(v[m]) / m.sum()
    return out


def cv_auc(X, y, folds=5, seed=42):
    """Pooled out-of-fold AUC; features standardized per fold on the train fold
    only, so no test-fold information leaks into the scaling.

    NOTE: pooling means the AUC is taken over OOF predictions concatenated
    across folds, which carries a real cost -- each fold fits its own intercept
    and scale, so probabilities from different folds are not strictly on a
    common scale. For a single feature this can drag the pooled AUC well below
    the fit-free rank AUC (measured: finbert-tone 0.590 -> 0.533) even though a
    one-feature logistic is monotone and should preserve AUC exactly. The
    consequence for Section B is that a pooled-OOF pair may ONLY be compared
    against pooled-OOF singles computed the same way -- never against a
    full-data rank AUC, which pays no such penalty.
    """
    n = len(y)
    folds = min(folds, n)
    rng = np.random.default_rng(seed)
    idx = rng.permutation(n)
    oof = np.empty(n)
    for k in range(folds):
        test_idx = idx[k::folds]
        train_idx = np.setdiff1d(idx, test_idx, assume_unique=False)
        mu = X[train_idx].mean(axis=0)
        sd = X[train_idx].std(axis=0)
        sd[sd == 0] = 1.0
        w = fit_logistic((X[train_idx] - mu) / sd, y[train_idx])
        Xte = (X[test_idx] - mu) / sd
        oof[test_idx] = 1.0 / (1.0 + np.exp(-(Xte @ w[1:] + w[0])))
    return auc_score(oof, y)


# ----------------------------- pairing --------------------------------------
def split_body_col(col):
    """`FinMarBa_Body_indecisive` -> ('FinMarBa', 'indecisive')."""
    enc, chan = col.split(BODY_MARKER, 1)
    return enc, chan


def find_pairs(columns):
    pairs = []
    for col in columns:
        if BODY_MARKER not in col:
            continue
        enc, chan = split_body_col(col)
        if chan in SKIP_CHANNELS or chan not in CHANNEL_SIGN:
            continue
        head = f"{enc}_{chan}"
        if head in columns:
            pairs.append((enc, chan, head, col))
    return pairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="?", default=TSV_DEFAULT)
    ap.add_argument("--target", default=TARGET_DEFAULT)
    ap.add_argument("--thresholds", nargs="+", type=float, default=[30.0, 15.0],
                    help="first value is the primary threshold")
    ap.add_argument("--folds", type=int, default=5)
    ap.add_argument("--n-boot", type=int, default=2000)
    ap.add_argument("--max-outlier", type=float, default=500.0)
    ap.add_argument("--min-n", type=int, default=200)
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--split-date", default="2026-06-20")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report-suffix", default="")
    args = ap.parse_args()

    path = pathlib.Path(args.tsv)
    if not path.exists():
        sys.exit(f"File not found: {path}")
    out_dir = path.parent if str(path.parent) else pathlib.Path(".")
    out_path = out_dir / f"head_vs_body_encoders_report{args.report_suffix}.md"

    df = pd.read_csv(path, sep="\t", dtype=str)
    t = pd.to_numeric(df[args.target], errors="coerce")
    keep = t.notna() & (t <= args.max_outlier)
    df, t = df[keep].reset_index(drop=True), t[keep].reset_index(drop=True)

    pairs = find_pairs(set(df.columns))
    if not pairs:
        sys.exit("no head/body column pairs found")
    pairs.sort()

    # Materialize oriented score vectors once.
    scores = {}
    for _, chan, head, body in pairs:
        sign = CHANNEL_SIGN[chan]
        for c in (head, body):
            if c not in scores:
                scores[c] = sign * pd.to_numeric(df[c], errors="coerce").values

    recent_mask = None
    if args.date_col in df.columns:
        d = df[args.date_col].fillna("").astype(str)
        recent_mask = (d.ne("") & d.ge(args.split_date)).values

    lines = []
    emit = lines.append
    emit(f"# Headline vs body — the 11 encoders on `{args.target}`\n")
    emit(f"**Corpus:** `{path.name}` — {len(df)} rows with a target "
         f"(outliers >{args.max_outlier:g}% dropped).")
    emit(f"**Pairs compared:** {len(pairs)} head/body channel pairs across "
         f"{len({p[0] for p in pairs})} encoders.")
    emit(f"**Bootstrap:** {args.n_boot} paired row resamples, seed {args.seed}.\n")
    emit("Orientation is **a priori** from each channel's semantics, applied "
         "identically to both members of a pair — never fitted to the target. "
         "`(inv)` marks a channel used negated. AUC below 0.50 after that fixed "
         "orientation means the column runs backwards.\n")

    primary = args.thresholds[0]
    best_head, best_body = {}, {}

    for thr in args.thresholds:
        y = (t.values >= thr)
        n_pos, n_neg = int(y.sum()), int((~y).sum())
        emit(f"\n## Section A — paired AUC, movers = `{args.target} >= {thr:g}`\n")
        emit(f"{n_pos} movers / {len(y)} rows = base rate {y.mean():.1%}. "
             f"Single-AUC Hanley-McNeil SE ≈ ±{hanley_mcneil_se(0.6, n_pos, n_neg):.3f}.\n")

        m_bonf = len(pairs)
        z_bonf = norm.ppf(1 - 0.05 / (2 * m_bonf))
        emit(f"Bonferroni column uses z = {z_bonf:.2f} "
             f"(alpha 0.05 split over {m_bonf} pairs).\n")
        emit("| encoder | channel | HEAD | BODY | Δ (body−head) | 95% CI (paired) "
             "| Bonferroni CI | rho(h,b) | verdict |")
        emit("|---|---|---|---|---|---|---|---|---|")

        rows = []
        for enc, chan, head, body in pairs:
            sh, sb = scores[head], scores[body]
            m = ~np.isnan(sh) & ~np.isnan(sb)
            if m.sum() < args.min_n:
                continue
            yy = y[m]
            if yy.sum() < 10 or (~yy).sum() < 10:
                continue
            a_h, a_b = auc_score(sh[m], yy), auc_score(sb[m], yy)
            lo, hi, se = paired_bootstrap_diff(sh[m], sb[m], yy,
                                               args.n_boot, args.seed)
            rho = spearmanr(sh[m], sb[m]).statistic
            d = a_b - a_h
            blo, bhi = d - z_bonf * se, d + z_bonf * se
            if blo > 0:
                verdict = "**body wins**"
            elif bhi < 0:
                verdict = "head wins"
            elif lo > 0:
                verdict = "body (nominal only)"
            elif hi < 0:
                verdict = "head (nominal only)"
            else:
                verdict = "tie"
            tag = "(inv)" if CHANNEL_SIGN[chan] < 0 else ""
            rows.append((enc, chan, tag, a_h, a_b, d, lo, hi, blo, bhi, rho,
                         verdict))
            if thr == primary:
                if enc not in best_head or a_h > best_head[enc][1]:
                    best_head[enc] = (head, a_h, chan)
                if enc not in best_body or a_b > best_body[enc][1]:
                    best_body[enc] = (body, a_b, chan)

        for (enc, chan, tag, a_h, a_b, d, lo, hi, blo, bhi, rho,
             verdict) in sorted(rows, key=lambda r: -r[5]):
            emit(f"| {enc} | `{chan}` {tag} | {a_h:.3f} | {a_b:.3f} | "
                 f"{d:+.3f} | [{lo:+.3f}, {hi:+.3f}] | [{blo:+.3f}, {bhi:+.3f}] "
                 f"| {rho:.2f} | {verdict} |")

        n_body = sum(1 for r in rows if r[8] > 0)
        n_head = sum(1 for r in rows if r[9] < 0)
        emit(f"\n**Tally at Bonferroni:** body wins {n_body}, head wins "
             f"{n_head}, tie {len(rows) - n_body - n_head}, of {len(rows)} pairs.")

    # ---------------- Section B: does body add on top of headline? -----------
    y = (t.values >= primary)
    emit(f"\n## Section B — does body ADD on top of headline? "
         f"(movers >= {primary:g})\n")
    emit("Pooled **out-of-fold** logistic AUC, "
         f"{args.folds} folds, features percentile-ranked then standardized per "
         "fold on the train fold only. Every number in this table is computed "
         "the same way — **the singles are pooled-OOF too**, not the fit-free "
         "rank AUCs of Section A, so the pair is not charged a cross-validation "
         "penalty its baselines avoided. Synergy = pair − better OOF single; "
         "`rank A` columns repeat Section A's fit-free AUCs for reference.\n")
    emit("| encoder | head col | body col | rank A head | rank A body | OOF head "
         "| OOF body | OOF head+body | synergy | rho |")
    emit("|---|---|---|---|---|---|---|---|---|---|")

    def oof(cols, mask):
        X = np.column_stack([pct_rank(scores[c])[mask] for c in cols])
        return cv_auc(X, y[mask].astype(float), args.folds, args.seed)

    synergy_rows = []
    for enc in sorted(best_head):
        if enc not in best_body:
            continue
        hcol, a_h, _ = best_head[enc]
        bcol, a_b, _ = best_body[enc]
        sh, sb = scores[hcol], scores[bcol]
        m = ~np.isnan(sh) & ~np.isnan(sb)
        if m.sum() < args.min_n:
            continue
        o_h, o_b = oof([hcol], m), oof([bcol], m)
        both = oof([hcol, bcol], m)
        rho = spearmanr(sh[m], sb[m]).statistic
        syn = both - max(o_h, o_b)
        synergy_rows.append((enc, hcol, bcol, a_h, a_b, o_h, o_b, both, syn, rho))

    for (enc, hcol, bcol, a_h, a_b, o_h, o_b, both, syn,
         rho) in sorted(synergy_rows, key=lambda r: -r[7]):
        emit(f"| {enc} | `{hcol}` | `{bcol}` | {a_h:.3f} | {a_b:.3f} | "
             f"{o_h:.3f} | {o_b:.3f} | **{both:.3f}** | {syn:+.3f} | {rho:.2f} |")

    n_pos_syn = sum(1 for r in synergy_rows if r[8] > 0.01)
    emit(f"\n**Encoders where body adds more than +0.01 AUC over the better "
         f"single: {n_pos_syn} of {len(synergy_rows)}.**")

    # Cross-encoder: overall best head + overall best body.
    if best_head and best_body:
        top_h = max(best_head.values(), key=lambda v: v[1])
        top_b = max(best_body.values(), key=lambda v: v[1])
        sh, sb = scores[top_h[0]], scores[top_b[0]]
        m = ~np.isnan(sh) & ~np.isnan(sb)
        o_h, o_b = oof([top_h[0]], m), oof([top_b[0]], m)
        both = oof([top_h[0], top_b[0]], m)
        rho = spearmanr(sh[m], sb[m]).statistic
        emit(f"\n**Cross-encoder best pair:** `{top_h[0]}` (OOF {o_h:.3f}) + "
             f"`{top_b[0]}` (OOF {o_b:.3f}) → OOF AUC **{both:.3f}** "
             f"(synergy {both - max(o_h, o_b):+.3f}, rho {rho:.2f}).")

    # ---------------- stability: recent block ----------------
    if recent_mask is not None and recent_mask.sum() >= args.min_n:
        emit(f"\n## Stability check — rows with {args.date_col} >= "
             f"{args.split_date}\n")
        yr = y[recent_mask]
        emit(f"{int(yr.sum())} movers / {int(recent_mask.sum())} rows = "
             f"{yr.mean():.1%} base. All 11 encoders are externally trained, so "
             "the FULL corpus above is their valid test set — this block is a "
             "small-sample stability check only, never the primary number.\n")
        emit("| encoder | best head | AUC | best body | AUC |")
        emit("|---|---|---|---|---|")
        for enc in sorted(best_head):
            if enc not in best_body:
                continue
            hcol, bcol = best_head[enc][0], best_body[enc][0]
            sh, sb = scores[hcol][recent_mask], scores[bcol][recent_mask]
            mh, mb = ~np.isnan(sh), ~np.isnan(sb)
            ah = auc_score(sh[mh], yr[mh]) if mh.sum() > 50 else float("nan")
            ab = auc_score(sb[mb], yr[mb]) if mb.sum() > 50 else float("nan")
            emit(f"| {enc} | `{hcol}` | {ah:.3f} | `{bcol}` | {ab:.3f} |")

    out_path.write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n[wrote {out_path}]", file=sys.stderr)


if __name__ == "__main__":
    main()
