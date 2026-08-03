#!/usr/bin/env python3
"""TP/FP tables for the best FinBERT / encoder / nocoref columns.

Two views, because one is not enough:

  ABSOLUTE cutoffs  — the live-trigger view. What you get if you wire
                      `col >= 0.8` into a gate. Directly comparable to
                      false_positive_count.py. Meaningless across columns whose
                      distributions differ wildly (FinMarBa_indecisive has
                      median 0.186; FLANG-ELECTRA_positive is saturated near 1).

  TOP-k% selection  — the ranking view. Take the k% of rows a column scores
                      highest. Scale-free, so every column is compared on equal
                      selectivity. This is the honest cross-column comparison.

Columns marked (inv) fire LOW; they are negated before ranking and their
absolute cutoffs are mirrored to 1-level.
"""
import csv
import sys

import numpy as np

csv.field_size_limit(10**9)
F = ("outputs/04_PRstats_2026-05-04-2026-07-23_CoffeeFilter_11EncScored_"
     "Classed_BodyEnc_clean_FromPRhigh.tsv")
BLANK = ("", "nan", "NaN")

# (column, invert) — best channel per model, from head_vs_body Section B.
FINBERT = [("positive", 0), ("sentiment_score", 0), ("neutral", 1), ("negative", 1)]
NOCOREF = [("nocoref_net_score", 0), ("nocoref_confidence_weighted", 0),
           ("nocoref_neutral_filter", 0)]
HEAD = [("FLANG-BERT_sentiment_score", 0), ("FLANG-ELECTRA_sentiment_score", 0),
        ("DeBERTa_sentiment_score", 0), ("Trading-Hero_positive", 0),
        ("FinMarBa_indecisive", 1), ("distilroberta_sentiment_score", 0),
        ("ModernBERT_positive", 0), ("finbert-tone_sentiment_score", 0),
        ("RoBERTa_positive", 0), ("FinancialBERT_negative", 1),
        ("LouMcD-SDict_sentiment_score", 0)]
BODY = [("Trading-Hero_Body_sentiment_score", 0),
        ("FinancialBERT_Body_sentiment_score", 0),
        ("FLANG-ELECTRA_Body_positive", 0), ("FLANG-BERT_Body_sentiment_score", 0),
        ("distilroberta_Body_sentiment_score", 0), ("DeBERTa_Body_positive", 0),
        ("FinMarBa_Body_indecisive", 1), ("ModernBERT_Body_positive", 0),
        ("RoBERTa_Body_positive", 0), ("finbert-tone_Body_sentiment_score", 0),
        ("LouMcD-SDict_Body_strong_modal", 0)]

TOPK = [0.05, 0.10, 0.20, 0.30]
LEVELS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


def load():
    with open(F, newline="", encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh, delimiter="\t")
                if r["fromPR_High(%)"] not in BLANK]
    y = np.array([float(r["fromPR_High(%)"]) for r in rows])
    keep = y <= 500.0                      # same outlier rule as the sibling tools
    return [r for r, k in zip(rows, keep) if k], y[keep]


def col(rows, name):
    v, m = [], []
    for r in rows:
        s = r.get(name, "")
        try:
            v.append(float(s)); m.append(s not in BLANK)
        except ValueError:
            v.append(np.nan); m.append(False)
    return np.array(v), np.array(m)


def topk_table(rows, y, spec, thr, out):
    base = (y >= thr).mean()
    out.append(f"\n**Target `fromPR_High(%) >= {thr:g}%` — base rate "
               f"{base*100:.1f}% ({int((y>=thr).sum())} of {len(y)} rows).** "
               f"Cells are TP/FP, then precision (lift).\n")
    out.append("| column | n | " + " | ".join(f"top {int(k*100)}%" for k in TOPK) + " |")
    out.append("|---|---|" + "---|" * len(TOPK))
    for name, inv in spec:
        x, m = col(rows, name)
        if m.sum() < 200:
            continue
        s = -x[m] if inv else x[m]
        yy = (y[m] >= thr).astype(int)
        order = np.argsort(-s, kind="mergesort")
        cells = []
        for k in TOPK:
            n = max(1, int(k * m.sum()))
            sel = order[:n]
            tp = int(yy[sel].sum()); fp = n - tp
            prec = tp / n
            b = yy.mean()
            cells.append(f"{tp}/{fp} = {prec*100:.0f}% ({prec/b:.2f}x)")
        label = name + ("(inv)" if inv else "")
        out.append(f"| `{label}` | {int(m.sum())} | " + " | ".join(cells) + " |")


def abs_table(rows, y, spec, thr, out):
    base = (y >= thr).mean()
    out.append(f"\n**Absolute cutoffs, target `>= {thr:g}%` "
               f"(base {base*100:.1f}%).** Inverted columns fire on "
               f"`value <= 1-level`. Cells are TP/FP = precision.\n")
    out.append("| column | " + " | ".join(f"`>={l}`" for l in LEVELS) + " |")
    out.append("|---|" + "---|" * len(LEVELS))
    for name, inv in spec:
        x, m = col(rows, name)
        if m.sum() < 200:
            continue
        yy = (y[m] >= thr).astype(int); xx = x[m]
        cells = []
        for l in LEVELS:
            fired = (xx <= (1 - l)) if inv else (xx >= l)
            n = int(fired.sum())
            if n == 0:
                cells.append("—"); continue
            tp = int(yy[fired].sum())
            cells.append(f"{tp}/{n-tp} = {tp/n*100:.0f}%")
        label = name + ("(inv)" if inv else "")
        out.append(f"| `{label}` | " + " | ".join(cells) + " |")


def main():
    rows, y = load()
    out = []
    groups = [("FinBERT (live) + nocoref", FINBERT + NOCOREF),
              ("Encoders — best HEADLINE channel", HEAD),
              ("Encoders — best BODY channel", BODY)]
    for title, spec in groups:
        out.append(f"\n### {title}\n")
        for thr in (15, 30):
            topk_table(rows, y, spec, thr, out)
    out.append("\n### Absolute-cutoff view (live-trigger semantics)\n")
    for thr in (15, 30):
        abs_table(rows, y, FINBERT + NOCOREF + HEAD[:5], thr, out)
    txt = "\n".join(out)
    print(txt)
    with open(sys.argv[1], "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")


if __name__ == "__main__":
    main()
