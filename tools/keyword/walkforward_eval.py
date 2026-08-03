#!/usr/bin/env python3
"""Walk-forward evaluation of trained keyword models on a held-out VAL corpus.

The training guide is emphatic that CV and in-sample numbers are not the
arbiter: model_proba measured 0.907 in-sample and 0.676 walk-forward. This
scores frozen models on a corpus they never saw, which is the honest read.

It loads `tfidf_fitted_pipeline.joblib` directly rather than going through
`keyword_scorer-2.py --evaluate`, deliberately: `--evaluate` reconstructs a
train/test split and a split mismatch silently inflates AUC (the fake 0.92 of
guide §5.3). A separate VAL file has no split to reconstruct, so that whole
class of error cannot occur.

Text prep mirrors training exactly: keyword_finder applies
`strip_wire_boilerplate` to the text column BEFORE fitting and does NOT bake it
into the pipeline, so the same transform is applied here. Body truncation must
already match (the 400-word cap).

Each model is scored against SEVERAL label thresholds, not just its own. That is
the question worth answering: does a ge40 model actually rank ≥40% movers better
than a ge15 model does?

Precision is reported at several SELECTION VOLUMES (top 5/10/20/30%) with Wilson
intervals, not at one volume bare. A single decile on a 777-row VAL is ~77 rows,
where a 3-point precision gap is two PRs; without the interval the table invites
a conclusion the data cannot carry. Headline-vs-body pairs are additionally
compared at MATCHED volume with a Newcombe method-10 CI on the difference, which
is the only form of that comparison that means anything.

The absolute-cutoff table exists because top-k% cannot be deployed: a live gate
needs a constant, and a model whose proba never reaches 0.6 on VAL cannot be
wired to `>= 0.6` no matter how well it ranks.

Run from tools/keyword/:
    walkforward_eval.py --val inputs/..._VAL_..._cap400.tsv \
        --models model_trained_on_<stamp>/classify_fromPRhigh_ge15 ...
"""
import argparse
import csv
import os
import sys

import joblib
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keyword_finder import strip_wire_boilerplate  # noqa: E402

csv.field_size_limit(10**9)

LEVELS = [0.5, 0.6, 0.7, 0.8, 0.9]


def wilson(k, n, z=1.96):
    """Wilson score interval for a single proportion."""
    if n == 0:
        return (float('nan'), float('nan'))
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def newcombe_diff(k1, n1, k2, n2, z=1.96):
    """Newcombe's method 10 CI for p2 - p1, built from the two Wilson
    intervals.  Robust at the small counts and extreme proportions where the
    normal-approximation CI misbehaves."""
    if n1 == 0 or n2 == 0:
        return (float('nan'), float('nan'))
    l1, u1 = wilson(k1, n1, z)
    l2, u2 = wilson(k2, n2, z)
    p1, p2 = k1 / n1, k2 / n2
    lo = (p2 - p1) - z * np.sqrt(l2 * (1 - l2) / n2 + u1 * (1 - u1) / n1)
    hi = (p2 - p1) + z * np.sqrt(u2 * (1 - u2) / n2 + l1 * (1 - l1) / n1)
    return (lo, hi)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--val', required=True, help='held-out VAL tsv')
    ap.add_argument('--models', nargs='+', required=True,
                    help='model directories holding tfidf_fitted_pipeline.joblib')
    ap.add_argument('--target-col', default='fromPR_High(%)')
    ap.add_argument('--thresholds', default='15,30,40')
    ap.add_argument('--top-fracs', default='0.05,0.10,0.20,0.30',
                    help='selection volumes for the precision tables')
    ap.add_argument('--top-frac', type=float, default=None,
                    help='deprecated alias for a single --top-fracs value')
    args = ap.parse_args()

    thresholds = [float(t) for t in args.thresholds.split(',')]
    top_fracs = ([args.top_frac] if args.top_frac is not None
                 else [float(f) for f in args.top_fracs.split(',')])
    with open(args.val, newline='', encoding='utf-8') as fh:
        rows = [r for r in csv.DictReader(fh, delimiter='\t')
                if r[args.target_col] not in ('', 'nan')]
    y_raw = np.array([float(r[args.target_col]) for r in rows])
    print(f"VAL: {len(rows)} labelled rows from {os.path.basename(args.val)}")
    print("base rates: " + "  ".join(
        f"≥{t:g}% {(y_raw>=t).mean()*100:.1f}%" for t in thresholds))
    print()

    hdr = f"{'model':<44}{'text':<9}" + "".join(f"{'AUC≥'+f'{t:g}':>10}" for t in thresholds)
    print(hdr); print('-' * len(hdr))

    results = {}
    for mdir in args.models:
        pipe_path = os.path.join(mdir, 'tfidf_fitted_pipeline.joblib')
        if not os.path.exists(pipe_path):
            print(f"{os.path.basename(mdir):<44}  [no pipeline yet]"); continue
        pipe = joblib.load(pipe_path)
        col = 'Headline' if mdir.rstrip('/').endswith('_headline') else 'Body'
        texts = [strip_wire_boilerplate(r[col] or '') for r in rows]
        p = pipe.predict_proba(texts)[:, 1]
        aucs = []
        for t in thresholds:
            yb = (y_raw >= t).astype(int)
            aucs.append(roc_auc_score(yb, p) if 0 < yb.sum() < len(yb) else float('nan'))
        results[mdir] = (p, aucs)
        name = os.path.basename(mdir.rstrip('/'))
        print(f"{name:<44}{col:<9}" + "".join(f"{a:>10.4f}" for a in aucs))

    # ---- precision at deployable volume, with Wilson intervals -------------
    for t in thresholds:
        yb = (y_raw >= t).astype(int)
        base = yb.mean()
        print(f"\nprecision @ top-k% of VAL — target ≥{t:g}% "
              f"(base {base*100:.1f}%, {int(yb.sum())} positives)")
        hdr2 = f"{'model':<44}" + "".join(
            f"{'top ' + f'{f*100:.0f}%':>30}" for f in top_fracs)
        print(hdr2); print('-' * len(hdr2))
        for mdir, (p, _) in results.items():
            cells = ''
            for f in top_fracs:
                k = max(1, int(f * len(rows)))
                top = np.argsort(-p)[:k]
                tp = int(yb[top].sum())
                prec = tp / k
                lo, hi = wilson(tp, k)
                cells += (f"{prec*100:>8.1f}% {prec/base:>4.2f}x "
                          f"[{lo*100:>4.1f},{hi*100:>5.1f}]")
            print(f"{os.path.basename(mdir.rstrip('/')):<44}{cells}")

    # ---- headline vs body at MATCHED volume --------------------------------
    # Same k for both channels, so the precision difference is a difference of
    # two proportions on equal denominators and Newcombe applies directly.
    pairs = [(b, b + '_headline') for b in results
             if b + '_headline' in results]
    if pairs:
        print("\nheadline vs body at matched volume — Δ = headline − body, "
              "Newcombe 95% CI")
        for body, head in pairs:
            name = os.path.basename(body.rstrip('/'))
            print(f"\n  {name}   (n = {len(rows)} VAL rows)")
            hdr3 = (f"  {'target':<8}{'volume':<14}{'body':>20}"
                    f"{'headline':>20}{'Δ':>11}      95% CI")
            print(hdr3); print('  ' + '-' * (len(hdr3) - 2))
            for t in thresholds:
                yb = (y_raw >= t).astype(int)
                for f in top_fracs:
                    k = max(1, int(f * len(rows)))
                    tb = int(yb[np.argsort(-results[body][0])[:k]].sum())
                    th = int(yb[np.argsort(-results[head][0])[:k]].sum())
                    lo, hi = newcombe_diff(tb, k, th, k)
                    sig = '  *' if not (lo <= 0 <= hi) else ''
                    print(f"  ≥{t:<7g}{f'top {f*100:.0f}% (n={k})':<14}"
                          f"{f'{tb}/{k} = {tb/k*100:.1f}%':>20}"
                          f"{f'{th}/{k} = {th/k*100:.1f}%':>20}"
                          f"{(th-tb)/k*100:>+9.1f}pp"
                          f"   [{lo*100:>+6.1f},{hi*100:>+6.1f}]{sig}")

    # ---- absolute cutoffs — what a live gate could actually use -------------
    for t in thresholds:
        yb = (y_raw >= t).astype(int)
        print(f"\nabsolute cutoffs — target ≥{t:g}% "
              f"(base {yb.mean()*100:.1f}%).  cells are TP/fires = precision")
        hdr4 = f"{'model':<44}" + "".join(f"{'>='+f'{l:g}':>20}" for l in LEVELS)
        print(hdr4); print('-' * len(hdr4))
        for mdir, (p, _) in results.items():
            cells = ''
            for l in LEVELS:
                fired = p >= l
                n = int(fired.sum())
                if n == 0:
                    cells += f"{'—':>20}"; continue
                tp = int(yb[fired].sum())
                cells += f"{f'{tp}/{n} = {tp/n*100:.0f}%':>20}"
            print(f"{os.path.basename(mdir.rstrip('/')):<44}{cells}")

    if len(results) > 1:
        print("\nrank correlation between model scores (Spearman):")
        from scipy.stats import spearmanr
        names = [os.path.basename(m.rstrip('/')) for m in results]
        ps = [v[0] for v in results.values()]
        for i in range(len(ps)):
            for j in range(i + 1, len(ps)):
                print(f"  {names[i]:<42} vs {names[j]:<42} {spearmanr(ps[i], ps[j]).statistic:.3f}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
