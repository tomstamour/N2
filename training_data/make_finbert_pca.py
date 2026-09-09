#!/usr/bin/env python
"""
make_finbert_pca.py -- PCA reduction of the FinBERT embedding sidecar.

Reads the wide embedding sidecar produced by make_finbert_embeddings.py
(row_idx, Symbol, ArrivalTime, hd_emb_000..767, bd_emb_000..767) and the base
training TSV it was built from, fits a SEPARATE PCA per stream (headline / body)
on the TRAINING FOLD ONLY (rows with ArrivalDate < --cutoff), then applies that
frozen transform to every row.

Outputs:
  <base_stem>_PCA.tsv     base 210 cols + hd_pca_* + bd_pca_* (same row order)
  finbert_pca.pkl         fitted transform (per-stream mean_ + components_) -- inference
  finbert_pca_meta.json   per-PC explained variance + train-fold target association
  finbert_pca_report.md   scree summary + which components to prioritise

PCA is mean-centred only (no per-dim StandardScaler): the design notes expect
~95% variance within a few dozen components, which only holds for covariance PCA.

    /home/tom/venv/bin/python make_finbert_pca.py
    /home/tom/venv/bin/python make_finbert_pca.py --cutoff 2026-07-28
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
from scipy import stats
from sklearn.decomposition import PCA

HERE = Path(__file__).resolve().parent
csv.field_size_limit(16 * 1024 * 1024)

EMB_GLOB = "08_*_FinBERTemb.tsv"
BASE_GLOB = "08_*_rmCol_NaN_rmCol.tsv"
DEFAULT_CUTOFF = "2026-07-28"
THRESHOLDS = (15, 20, 30)


def _newest(glob, exclude=()):
    cands = [
        p for p in HERE.glob(glob)
        if not any(p.name.endswith(x) for x in exclude)
    ]
    return max(cands, key=lambda p: p.stat().st_mtime) if cands else None


def _read_tsv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader)
        rows = list(reader)
    return header, rows


def _fit_stream(name, X, train_mask, var_target, max_components):
    n_train = int(train_mask.sum())
    nc = min(X.shape[1], n_train - 1)
    pca = PCA(n_components=nc, svd_solver="full", random_state=0)
    pca.fit(X[train_mask])
    cum = np.cumsum(pca.explained_variance_ratio_)
    k_var = min(int(np.searchsorted(cum, var_target) + 1), nc)
    k = k_var
    capped = bool(max_components and max_components < k_var)
    if max_components:
        k = min(k, max_components)
    scores = (X - pca.mean_) @ pca.components_[:k].T
    tag = f"k(cap)={k}/{nc}" if capped else f"k(>={var_target:.0%})={k}/{nc}"
    print(f"  [{name}] train={n_train}  {tag}  cum_var@k={cum[k - 1]:.4f}  "
          f"(k@90%={int(np.searchsorted(cum, 0.90) + 1)}, "
          f"k@{var_target:.0%}={k_var}, "
          f"k@99%={int(np.searchsorted(cum, 0.99) + 1)})")
    return pca, k, cum, scores.astype(np.float64), capped


def _assoc(scores_train, y, y_bins):
    """Per-PC train-fold univariate association tables."""
    out = []
    for j in range(scores_train.shape[1]):
        col = scores_train[:, j]
        rec = {
            "pc": j,
            "abs_pearson": abs(float(stats.pearsonr(col, y)[0])),
            "abs_spearman": abs(float(stats.spearmanr(col, y)[0])),
        }
        for t, b in y_bins.items():
            rec[f"abs_pbis_ge{t}"] = abs(float(stats.pointbiserialr(b, col)[0]))
        out.append(rec)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--emb", help=f"embedding sidecar (default: newest {EMB_GLOB})")
    ap.add_argument("--base", help=f"base training TSV (default: newest {BASE_GLOB})")
    ap.add_argument("--cutoff", default=DEFAULT_CUTOFF,
                    help=f"train = rows with ArrivalDate < this (default {DEFAULT_CUTOFF})")
    ap.add_argument("--date-col", default="ArrivalDate")
    ap.add_argument("--label-col", default="fromPR_High(%)")
    ap.add_argument("--var-target", type=float, default=0.95)
    ap.add_argument("--max-components", type=int, default=0,
                    help="optional per-stream cap on kept components (0 = no cap)")
    ap.add_argument("--output")
    ap.add_argument("--model-out")
    ap.add_argument("--meta-out")
    ap.add_argument("--report-out")
    args = ap.parse_args()

    emb_path = (Path(args.emb).expanduser().resolve() if args.emb
                else _newest(EMB_GLOB))
    base_path = (Path(args.base).expanduser().resolve() if args.base
                 else _newest(BASE_GLOB, exclude=("_FinBERTemb.tsv", "_PCA.tsv")))
    if not emb_path or not emb_path.is_file():
        ap.error(f"embedding sidecar not found: {emb_path}")
    if not base_path or not base_path.is_file():
        ap.error(f"base training TSV not found: {base_path}")

    out_path = (Path(args.output).expanduser().resolve() if args.output
                else base_path.with_name(base_path.stem + "_PCA.tsv"))
    model_out = Path(args.model_out) if args.model_out else HERE / "finbert_pca.pkl"
    meta_out = Path(args.meta_out) if args.meta_out else HERE / "finbert_pca_meta.json"
    report_out = Path(args.report_out) if args.report_out else HERE / "finbert_pca_report.md"

    print(f"emb  : {emb_path.name}")
    print(f"base : {base_path.name}")
    print(f"out  : {out_path.name}")

    emb_h, emb_rows = _read_tsv(emb_path)
    base_h, base_rows = _read_tsv(base_path)
    if len(emb_rows) != len(base_rows):
        ap.error(f"row count mismatch: emb {len(emb_rows)} vs base {len(base_rows)}")

    for c in (args.date_col, args.label_col, "Symbol", "ArrivalTime"):
        if c not in base_h:
            ap.error(f"column {c!r} not in {base_path.name}")
    b_sym, b_at = base_h.index("Symbol"), base_h.index("ArrivalTime")
    b_date, b_lbl = base_h.index(args.date_col), base_h.index(args.label_col)

    hd_cols = [i for i, c in enumerate(emb_h) if c.startswith("hd_emb_")]
    bd_cols = [i for i, c in enumerate(emb_h) if c.startswith("bd_emb_")]
    e_ridx, e_sym, e_at = (emb_h.index("row_idx"), emb_h.index("Symbol"),
                           emb_h.index("ArrivalTime"))
    if len(hd_cols) != 768 or len(bd_cols) != 768:
        ap.error(f"expected 768 hd + 768 bd cols, got {len(hd_cols)} + {len(bd_cols)}")

    # ---- alignment guard: sidecar join is by row order ----
    for i, (er, br) in enumerate(zip(emb_rows, base_rows)):
        if (er[e_ridx] != str(i) or er[e_sym] != br[b_sym]
                or er[e_at] != br[b_at]):
            ap.error(f"row {i} misaligned: emb "
                     f"({er[e_ridx]},{er[e_sym]},{er[e_at]}) vs base "
                     f"({i},{br[b_sym]},{br[b_at]})")

    HD = np.array([[float(r[c]) for c in hd_cols] for r in emb_rows], dtype=np.float64)
    BD = np.array([[float(r[c]) for c in bd_cols] for r in emb_rows], dtype=np.float64)

    dates = np.array([r[b_date] for r in base_rows])
    train_mask = dates < args.cutoff
    n_tr, n_va = int(train_mask.sum()), int((~train_mask).sum())
    if n_tr < 50 or n_va < 1:
        ap.error(f"degenerate split at cutoff {args.cutoff}: train {n_tr} / val {n_va}")

    def _lbl(v):
        try:
            return float(v)
        except ValueError:
            return np.nan
    y_all = np.array([_lbl(r[b_lbl]) for r in base_rows])
    print(f"cutoff {args.cutoff}: train {n_tr} ({100 * n_tr / len(base_rows):.1f}%)  "
          f"val {n_va}")
    for t in THRESHOLDS:
        pr_tr = np.nanmean((y_all[train_mask] >= t).astype(float))
        pr_va = np.nanmean((y_all[~train_mask] >= t).astype(float))
        print(f"  prevalence >= {t:>2}%: train {pr_tr:.3f}  val {pr_va:.3f}")

    t0 = time.perf_counter()
    print("[pca] fitting (train fold only) ...", flush=True)
    hd_pca, hd_k, hd_cum, hd_sc, hd_capped = _fit_stream(
        "hd", HD, train_mask, args.var_target, args.max_components)
    bd_pca, bd_k, bd_cum, bd_sc, bd_capped = _fit_stream(
        "bd", BD, train_mask, args.var_target, args.max_components)
    print(f"[pca] done in {time.perf_counter() - t0:.1f}s", flush=True)

    # ---- write augmented TSV ----
    hd_names = [f"hd_pca_{i:03d}" for i in range(hd_k)]
    bd_names = [f"bd_pca_{i:03d}" for i in range(bd_k)]
    out_header = list(base_h) + hd_names + bd_names
    width = len(base_h)
    print(f"writing {out_path.name}  ({len(base_rows)} rows, {len(out_header)} cols) ...",
          flush=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        w.writerow(out_header)
        for i, br in enumerate(base_rows):
            row = br + [""] * (width - len(br)) if len(br) < width else br
            w.writerow(row
                       + [f"{v:.6f}" for v in hd_sc[i]]
                       + [f"{v:.6f}" for v in bd_sc[i]])

    # ---- association tables (train fold only) ----
    y_tr = y_all[train_mask]
    fin = ~np.isnan(y_tr)
    y_bins = {t: (y_tr[fin] >= t).astype(float) for t in THRESHOLDS}
    hd_assoc = _assoc(hd_sc[train_mask][fin], y_tr[fin], y_bins)
    bd_assoc = _assoc(bd_sc[train_mask][fin], y_tr[fin], y_bins)

    # ---- pickle (inference transform) ----
    joblib.dump({
        "cutoff": args.cutoff,
        "var_target": args.var_target,
        "hd": {"mean_": hd_pca.mean_,
               "components_": hd_pca.components_[:hd_k],
               "explained_variance_ratio_": hd_pca.explained_variance_ratio_[:hd_k]},
        "bd": {"mean_": bd_pca.mean_,
               "components_": bd_pca.components_[:bd_k],
               "explained_variance_ratio_": bd_pca.explained_variance_ratio_[:bd_k]},
        "hd_emb_order": [emb_h[c] for c in hd_cols],
        "bd_emb_order": [emb_h[c] for c in bd_cols],
        "pca_col_names": hd_names + bd_names,
    }, model_out)

    meta = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
        "emb_file": emb_path.name,
        "base_file": base_path.name,
        "output_file": out_path.name,
        "cutoff": args.cutoff,
        "var_target": args.var_target,
        "max_components": args.max_components or None,
        "rows": {"total": len(base_rows), "train": n_tr, "val": n_va},
        "prevalence": {
            f"ge{t}": {
                "train": float(np.nanmean((y_all[train_mask] >= t))),
                "val": float(np.nanmean((y_all[~train_mask] >= t))),
            } for t in THRESHOLDS
        },
        "hd": {
            "k": hd_k,
            "capped": hd_capped,
            "cum_var_at_k": float(hd_cum[hd_k - 1]),
            "explained_variance_ratio": hd_pca.explained_variance_ratio_[:hd_k].tolist(),
            "cumulative_variance": hd_cum[:hd_k].tolist(),
            "target_assoc_train": hd_assoc,
        },
        "bd": {
            "k": bd_k,
            "capped": bd_capped,
            "cum_var_at_k": float(bd_cum[bd_k - 1]),
            "explained_variance_ratio": bd_pca.explained_variance_ratio_[:bd_k].tolist(),
            "cumulative_variance": bd_cum[:bd_k].tolist(),
            "target_assoc_train": bd_assoc,
        },
    }
    meta_out.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    _write_report(report_out, meta, hd_cum, bd_cum, hd_assoc, bd_assoc,
                  hd_pca, bd_pca, args)
    print(f"wrote {out_path}")
    print(f"wrote {model_out.name}, {meta_out.name}, {report_out.name}")
    return 0


def _k_at(cum, frac):
    return int(np.searchsorted(cum, frac) + 1)


def _write_report(path, meta, hd_cum, bd_cum, hd_assoc, bd_assoc,
                  hd_pca, bd_pca, args):
    def top(assoc, key, n=15):
        return sorted(assoc, key=lambda r: r[key], reverse=True)[:n]

    lines = []
    lines.append("# FinBERT embedding PCA -- component recommendation\n")
    lines.append(f"- base file: `{meta['base_file']}`")
    lines.append(f"- embeddings: `{meta['emb_file']}`")
    lines.append(f"- augmented output: `{meta['output_file']}`")
    lines.append(f"- train/val cutoff: **{meta['cutoff']}** "
                 f"(ArrivalDate < cutoff) -> train {meta['rows']['train']} / "
                 f"val {meta['rows']['val']} of {meta['rows']['total']}")
    lines.append(f"- PCA fit on the **train fold only**, mean-centred, no "
                 f"per-dim scaling, `whiten=False`\n")

    lines.append("## Scree / how many components\n")
    lines.append("| stream | k @90% | k @95% | k @99% | kept | cum var @ kept |")
    lines.append("|---|---|---|---|---|---|")
    for s, cum, pca, k in (("headline (hd)", hd_cum, hd_pca, meta["hd"]["k"]),
                           ("body (bd)", bd_cum, bd_pca, meta["bd"]["k"])):
        lines.append(f"| {s} | {_k_at(cum, .90)} | {_k_at(cum, .95)} | "
                     f"{_k_at(cum, .99)} | {k} | {cum[k - 1]:.4f} |")
    lines.append("")

    hk, bk = meta["hd"]["k"], meta["bd"]["k"]
    hv, bv = meta["hd"]["cum_var_at_k"], meta["bd"]["cum_var_at_k"]
    lines.append("## Recommendation\n")
    if meta.get("max_components"):
        lines.append(f"Add the **{hk} `hd_pca_*`** and **{bk} `bd_pca_*`** columns "
                     f"from `{meta['output_file']}` ({hk + bk} new features), "
                     f"capped at {meta['max_components']}/stream. At this cap they "
                     f"cover **{hv:.1%}** of headline / **{bv:.1%}** of body "
                     f"embedding variance (see the scree table above for what >=95% "
                     f"would have cost). Let XGBoost + SHAP / permutation importance "
                     f"prune from there.\n")
    else:
        lines.append(f"Add **all {hk} `hd_pca_*`** and **all {bk} `bd_pca_*`** "
                     f"columns from `{meta['output_file']}` to the training matrix "
                     f"({hk + bk} new features). They cover >=95% of the embedding "
                     f"variance per stream; let XGBoost + SHAP / permutation "
                     f"importance prune from there.\n")
    lines.append("The tables below rank those kept components by **train-fold** "
                 "univariate association with `fromPR_High(%)` -- the components "
                 "most likely to carry signal and survive pruning. Low-rank PCs "
                 "here are still worth passing to the tree (non-linear / "
                 "interaction effects), just lower prior.\n")

    for s, assoc in (("Headline PCs", hd_assoc), ("Body PCs", bd_assoc)):
        lines.append(f"### {s} -- top 15 by |Pearson r| vs target (train)\n")
        lines.append("| PC | |Pearson| | |Spearman| | |pbis>=15| | |pbis>=20| | |pbis>=30| |")
        lines.append("|---|---|---|---|---|---|")
        for r in top(assoc, "abs_pearson"):
            lines.append(f"| {r['pc']:03d} | {r['abs_pearson']:.3f} | "
                         f"{r['abs_spearman']:.3f} | {r['abs_pbis_ge15']:.3f} | "
                         f"{r['abs_pbis_ge20']:.3f} | {r['abs_pbis_ge30']:.3f} |")
        lines.append("")

    lines.append("## Notes\n")
    lines.append("- Re-run with a different `--cutoff` to match your final "
                 "train/val split; the PCA basis shifts slightly but the >=95% "
                 "cut and the ranking are stable.")
    lines.append("- Inference: `finbert_pca_transform.transform_pr(embed_pr(...))` "
                 "applies the exact `finbert_pca.pkl` transform (per-stream "
                 "`(v - mean_) @ components_.T`).")
    lines.append("- Alternatives not used: L2-normalise each embedding row before "
                 "centring; or correlation-PCA (`StandardScaler`) -- both push the "
                 "95% cut to ~150-250 components.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
