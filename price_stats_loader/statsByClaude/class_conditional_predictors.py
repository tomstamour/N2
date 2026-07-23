#!/usr/bin/env python3
"""Per-class predictability: inside each PR class (--class-col, default
BroadClass), how well does each of the 14 single-column sentiment predictors
(11 encoder best channels + FinBERT positive / sentiment_score /
nocoref_neutral_filter) separate big movers (target >= threshold)?

Answers two questions:
  * Which classes are HARD — even the best column's within-class AUC stays
    near 0.5?
  * Is one encoder better than the others on particular classes?

Columns are auto-oriented: any column whose full-corpus AUC at the primary
threshold is < 0.5 is negated everywhere and labeled `col(inv)` (this catches
FinMarBa_indecisive and FinancialBERT_negative without a hardcoded list).
After orientation a within-class AUC < 0.5 means the signal REVERSES inside
that class. Class movers below --min-pos flag the cell `~` (unreliable).

A TP/FP section breaks four fixed triggers (live gate positive>=0.8,
FLANG-BERT_positive>=0.95, FinMarBa_indecisive<=0.1,
nocoref_neutral_filter>=0.5) down per class — same firing semantics as
false_positive_count.py.

Pure numpy / pandas / scipy.

Run:
    /home/tom/venv/bin/python class_conditional_predictors.py \
        ../outputs/PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored_Classed.tsv
"""
import argparse
import pathlib
import re
import sys

import numpy as np
import pandas as pd
from scipy.stats import rankdata

TARGET_DEFAULT = "fromPR_High(%)"

COLUMNS_DEFAULT = [
    "FinMarBa_indecisive",
    "positive",
    "FLANG-BERT_positive",
    "FLANG-ELECTRA_sentiment_score",
    "Trading-Hero_sentiment_score",
    "DeBERTa_positive",
    "sentiment_score",
    "ModernBERT_positive",
    "nocoref_neutral_filter",
    "distilroberta_sentiment_score",
    "finbert-tone_sentiment_score",
    "RoBERTa_sentiment_score",
    "LouMcD-SDict_sentiment_score",
    "FinancialBERT_negative",
]

# (column, cutoff, fire_low) — fire_low=True fires on value <= cutoff.
TRIGGERS_DEFAULT = [
    ("positive", 0.8, False),
    ("FLANG-BERT_positive", 0.95, False),
    ("FinMarBa_indecisive", 0.1, True),
    ("nocoref_neutral_filter", 0.5, False),
]

# Same spec grammar as conditional_value.py, so a trigger can be copied between
# the two scripts unchanged.
COND_RE = re.compile(r"^\s*(.+?)\s*(>=|<=)\s*([-+0-9.eE]+)\s*$")


def parse_trigger(spec, columns):
    """'FinMarBa_indecisive<=0.1' -> ('FinMarBa_indecisive', 0.1, True)."""
    m = COND_RE.match(spec)
    if not m:
        sys.exit(f"bad trigger {spec!r}; expected 'COLUMN>=VALUE' or "
                 f"'COLUMN<=VALUE'")
    col, op, val = m.group(1), m.group(2), float(m.group(3))
    if col not in columns:
        sys.exit(f"trigger column {col!r} not in file")
    return col, val, op == "<="


def auc_score(scores, labels):
    """ROC AUC via the Mann-Whitney rank formula (handles ties)."""
    labels = labels.astype(bool)
    n_pos = int(labels.sum())
    n_neg = int((~labels).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    ranks = rankdata(scores)
    return (ranks[labels].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)


def load_and_clean(tsv_path, target, max_outlier=500.0):
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    if target not in df.columns:
        sys.exit(f"target column {target!r} not in {tsv_path}")
    df[target] = pd.to_numeric(df[target], errors="coerce")
    df = df.dropna(subset=[target])
    df = df[df[target] <= max_outlier]
    dedup_keys = [c for c in ("Symbol", "Headline", "ArrivalDate") if c in df.columns]
    if dedup_keys:
        df = df.drop_duplicates(subset=dedup_keys, keep="first")
    return df.reset_index(drop=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("tsv", nargs="?",
                    default="../outputs/PRstats_2026-06-20-2026-07-16_"
                            "CoffeeFilter_11EncScored_Classed.tsv")
    ap.add_argument("--target", default=TARGET_DEFAULT)
    ap.add_argument("--class-col", default="BroadClass")
    ap.add_argument("--thresholds", nargs="+", type=float, default=[30.0, 15.0],
                    help="first value is the primary threshold")
    ap.add_argument("--min-class-n", type=int, default=40,
                    help="classes with fewer rows are dropped")
    ap.add_argument("--min-pos", type=int, default=8,
                    help="class movers below this flag the AUC cell '~'")
    ap.add_argument("--columns", nargs="+", default=COLUMNS_DEFAULT)
    ap.add_argument("--triggers", nargs="+", default=None, metavar="SPEC",
                    help="trigger specs for the per-class TP/FP table, e.g. "
                         "'positive>=0.8' 'FinMarBa_indecisive<=0.1'. "
                         "Default: the four headline triggers.")
    ap.add_argument("--report-suffix", default="",
                    help="appended to the report filename; use it when running "
                         "a non-default column set so the default report is "
                         "not overwritten")
    args = ap.parse_args()

    tsv_path = pathlib.Path(args.tsv)
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")
    out_dir = tsv_path.parent if str(tsv_path.parent) else pathlib.Path(".")
    target, cls_col = args.target, args.class_col
    thr_main = args.thresholds[0]

    df = load_and_clean(tsv_path, target)
    if cls_col not in df.columns:
        sys.exit(f"class column {cls_col!r} not in file")
    triggers = ([parse_trigger(s, df.columns) for s in args.triggers]
                if args.triggers else list(TRIGGERS_DEFAULT))
    missing = [c for c in args.columns if c not in df.columns]
    if missing:
        sys.exit(f"column(s) not found: {', '.join(missing)}")
    for col in args.columns:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    t = df[target].values

    # --- auto-orientation on the full corpus at the primary threshold
    orient, disp = {}, {}
    y_main = (t >= thr_main).astype(float)
    for col in args.columns:
        m = df[col].notna().values
        a = auc_score(df[col].values[m], y_main[m])
        inv = (not np.isnan(a)) and a < 0.5
        orient[col] = -1.0 if inv else 1.0
        disp[col] = col + "(inv)" if inv else col

    # --- classes kept
    cls = df[cls_col].fillna("<missing>").astype(str)
    counts = cls.value_counts()
    levels = [lv for lv in counts.index if counts[lv] >= args.min_class_n]
    levels.sort(key=lambda lv: -counts[lv])

    L = []

    def emit(line=""):
        print(line)
        L.append(line)

    emit(f"# Per-class predictability — {cls_col} × {len(args.columns)} single "
         f"columns — {tsv_path.name}")
    emit()
    emit(f"- Rows kept: **{len(df)}**; classes with >= {args.min_class_n} rows: "
         f"{len(levels)} of {counts.size}")
    emit(f"- Thresholds: primary **>= {thr_main:g}%**, companion "
         f"{', '.join(f'>= {x:g}%' for x in args.thresholds[1:])}")
    emit(f"- Columns auto-oriented on the full corpus; `(inv)` = used negated. "
         f"Within-class AUC < 0.5 then means the signal reverses in that class.")
    emit(f"- `~` marks cells with < {args.min_pos} movers in the class (unreliable).")
    emit()

    per_thr_stats = {}
    for thr in args.thresholds:
        y_all = (t >= thr).astype(float)
        emit(f"## Within-class AUC matrix — movers = {target} >= {thr:g}%")
        emit()
        short = {c: (disp[c][:14] if len(disp[c]) > 14 else disp[c])
                 for c in args.columns}
        emit("| class | n | movers | base | " +
             " | ".join(short[c] for c in args.columns) + " |")
        emit("|---|---|---|---|" + "---|" * len(args.columns))
        stats = {}
        for lv in levels:
            m_lv = (cls == lv).values
            n_pos = int(y_all[m_lv].sum())
            cells, aucs = [], {}
            for col in args.columns:
                m = m_lv & df[col].notna().values
                a = auc_score(orient[col] * df[col].values[m], y_all[m])
                aucs[col] = a
                flag = "~" if n_pos < args.min_pos else ""
                cells.append("—" if np.isnan(a) else f"{a:.2f}{flag}")
            stats[lv] = {"n": int(m_lv.sum()), "n_pos": n_pos, "aucs": aucs}
            emit(f"| {lv} | {m_lv.sum()} | {n_pos} | "
                 f"{y_all[m_lv].mean():.1%} | " + " | ".join(cells) + " |")
        emit()
        per_thr_stats[thr] = stats

    # --- class hardness ranking (primary threshold)
    stats = per_thr_stats[thr_main]
    emit(f"## Class hardness — best achievable single-column AUC (>= {thr_main:g}%)")
    emit()
    emit("| class | n | movers | best column | best AUC | median AUC (14 cols) |")
    emit("|---|---|---|---|---|---|")
    hardness = []
    for lv in levels:
        s = stats[lv]
        valid = {c: a for c, a in s["aucs"].items() if not np.isnan(a)}
        if not valid:
            hardness.append((lv, float("nan"), None, float("nan")))
            continue
        best_col = max(valid, key=valid.get)
        med = float(np.median(list(valid.values())))
        hardness.append((lv, valid[best_col], best_col, med))
    hardness.sort(key=lambda r: (r[1] if not np.isnan(r[1]) else 99))
    for lv, best_auc, best_col, med in hardness:
        s = stats[lv]
        flag = " ~" if s["n_pos"] < args.min_pos else ""
        if best_col is None:
            emit(f"| {lv} | {s['n']} | {s['n_pos']} | — | — | — |")
        else:
            emit(f"| {lv} | {s['n']} | {s['n_pos']} | {disp[best_col]} "
                 f"| {best_auc:.2f}{flag} | {med:.2f} |")
    emit()

    # --- winners: which column wins how many classes (reliable classes only)
    emit(f"## Winners — within-class best column counts (classes with >= "
         f"{args.min_pos} movers, >= {thr_main:g}%)")
    emit()
    wins = {c: [] for c in args.columns}
    for lv in levels:
        s = stats[lv]
        if s["n_pos"] < args.min_pos:
            continue
        valid = {c: a for c, a in s["aucs"].items() if not np.isnan(a)}
        if not valid:
            continue
        ranked = sorted(valid.items(), key=lambda kv: -kv[1])
        wins[ranked[0][0]].append((lv, ranked[0][1]))
        emit(f"- **{lv}** top-3: " + ", ".join(
            f"{disp[c]} {a:.2f}" for c, a in ranked[:3]))
    emit()
    emit("| column | classes won | where |")
    emit("|---|---|---|")
    for col in sorted(args.columns, key=lambda c: -len(wins[c])):
        if not wins[col]:
            continue
        emit(f"| {disp[col]} | {len(wins[col])} | " + ", ".join(
            f"{lv} ({a:.2f})" for lv, a in wins[col]) + " |")
    emit()

    # --- TP/FP per class for the fixed triggers (primary threshold)
    y_all = (t >= thr_main).astype(float)
    emit(f"## TP/FP per class — fixed triggers, movers >= {thr_main:g}% "
         f"(cells: TP/FP = precision)")
    emit()
    trig_labels = [f"{c}{'<=' if lo else '>='}{cut:g}" for c, cut, lo in triggers]
    emit("| class | movers | " + " | ".join(trig_labels) + " |")
    emit("|---|---|" + "---|" * len(triggers))
    for lv in levels + ["<ALL>"]:
        m_lv = np.ones(len(df), bool) if lv == "<ALL>" else (cls == lv).values
        row = []
        for col, cut, fire_low in triggers:
            v = df[col].values
            fired = (v <= cut) if fire_low else (v >= cut)
            fired = fired & m_lv & df[col].notna().values
            tp = int((fired & (y_all == 1)).sum())
            fp = int((fired & (y_all == 0)).sum())
            prec = f" = {tp / (tp + fp):.0%}" if tp + fp else ""
            row.append(f"{tp}/{fp}{prec}")
        emit(f"| {lv} | {int(y_all[m_lv].sum())} | " + " | ".join(row) + " |")
    emit()

    target_slug = re.sub(r"[^A-Za-z0-9]+", "", target).lower()
    report_path = (out_dir /
                   f"{target_slug}_class_conditional_report"
                   f"{args.report_suffix}.md")
    report_path.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nReport written -> {report_path}")


if __name__ == "__main__":
    main()
