#!/usr/bin/env python3
"""Charts for 08_class_trading_value.md. Reads the TSVs that analysis wrote.

Palette validated with the dataviz skill's validate_palette.js (3 categorical hues,
light surface: ALL CHECKS PASS). #1baf7a carries a contrast WARN against the
surface, so every mark in that hue is directly labelled - that is the required
relief, not an optional nicety.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

C_BLUE, C_ORANGE, C_AQUA = "#2a78d6", "#eb6834", "#1baf7a"
SURFACE, INK, INK2, GRID, NEUTRAL = "#fcfcfb", "#0b0b0b", "#52514e", "#e6e5e1", "#8a8985"
SHAPE_COLOR = {"baseline": INK, "veto": C_BLUE, "whitelist": C_ORANGE,
               "class-conditional": C_AQUA, "rule ablation": NEUTRAL}
# "combination" candidates ARE class-conditional shapes - one hue, one legend entry.
# Two legend rows sharing a colour would break identity-by-colour.
SHAPE_LABEL = {"combination": "class-conditional"}
OUT = Path("outputs/charts_08_class_trading_value")


def style(ax, title, xl, yl):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, color=INK, fontsize=12, fontweight="600", loc="left", pad=12)
    ax.set_xlabel(xl, color=INK2, fontsize=9)
    ax.set_ylabel(yl, color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(True, color=GRID, lw=0.8, alpha=0.9)
    ax.set_axisbelow(True)


def fig(w, h):
    f, ax = plt.subplots(figsize=(w, h), facecolor=SURFACE)
    return f, ax


def chart_frontier(fr: pd.DataFrame) -> None:
    f, ax = fig(10, 6.4)
    style(ax, "Filter frontier — precision bought per trade/day given up",
          "trades per day (calibrated to the live 3.3/day)",
          "precision on fired rows  (TP = fromPR_High ≥ 50%)")
    base = fr[fr["shape"] == "baseline"].iloc[0]
    ax.axvline(2.0, color=C_ORANGE, lw=1.5, ls="--", alpha=0.7)
    ax.text(2.03, 0.435, "2 trades/day floor", color=C_ORANGE, fontsize=8, rotation=90,
            va="top")
    ax.axhline(base.precision, color=NEUTRAL, lw=1.2, ls=":")
    ax.text(0.35, base.precision + 0.004, "current gate 22.0%", color=NEUTRAL, fontsize=8)
    fr = fr.assign(disp=fr["shape"].map(lambda s: SHAPE_LABEL.get(s, s)))
    for shape, g in fr.groupby("disp"):
        ax.scatter(g.trades_per_day, g.precision, s=90, zorder=3,
                   color=SHAPE_COLOR[g["shape"].iloc[0]], edgecolor=SURFACE,
                   linewidth=2, label=shape)
    # Direct-label the few points a reader has to act on (never every point).
    # Offsets are per-label: the two right-hand points sit ~0.006 apart in precision
    # and collide with a shared offset.
    LABELS = {
        "CURRENT GATE (all 5 rules)": ("current gate", (-12, -18), "right"),
        "whitelist MA+Partnership": ("whitelist MA+Partnership", (9, 6), "left"),
        "drop FinBERTSolo + solos only in MA+Partnership": ("CANDIDATE A", (9, 8), "left"),
        "veto 6 dead classes": ("veto 6 dead classes\n(does ~nothing)", (-18, 34), "right"),
    }
    for _, r in fr.iterrows():
        if r["name"] not in LABELS:
            continue
        lab, off, ha = LABELS[r["name"]]
        # A leader line where the label cannot sit adjacent to its own mark: three
        # candidates cluster within 0.006 precision at the right edge.
        arrow = (dict(arrowstyle="-", color=NEUTRAL, lw=0.9,
                      shrinkA=2, shrinkB=4) if abs(off[1]) > 20 else None)
        ax.annotate(lab, (r.trades_per_day, r.precision), textcoords="offset points",
                    xytext=off, fontsize=8.5, color=INK, fontweight="600", ha=ha,
                    arrowprops=arrow)
    ax.set_ylim(0.16, 0.45)
    ax.set_xlim(0.3, 3.7)
    ax.yaxis.set_major_formatter(lambda v, p: f"{v:.0%}")
    lg = ax.legend(frameon=False, fontsize=8.5, loc="upper right", labelcolor=INK2)
    lg.set_title("filter shape", prop={"size": 8.5})
    f.tight_layout()
    f.savefig(OUT / "frontier.png", dpi=160, facecolor=SURFACE)
    plt.close(f)


def chart_conditional(cc: pd.DataFrame, base: float) -> None:
    """The decisive table as a picture: hit rate among FIRED rows, with the CI that
    makes most of the ranking unreadable."""
    d = cc.sort_values("precision").reset_index(drop=True)
    f, ax = fig(9.5, 7.2)
    style(ax, "Class hit rate among the rows the gate actually fires on\n"
              "(honest window, 173 fires — the intervals are the point)",
          "precision  (TP = fromPR_High ≥ 50%)", "")
    y = np.arange(len(d))
    for i, r in d.iterrows():
        col = C_AQUA if r.precision > base else C_BLUE
        ax.plot([r.ci_lo, r.ci_hi], [i, i], color=col, lw=2.4, alpha=0.42,
                solid_capstyle="round")
        ax.scatter(r.precision, i, s=68, color=col, zorder=3,
                   edgecolor=SURFACE, linewidth=1.8)
        ax.text(r.ci_hi + 0.012, i, f"{r.tp}/{r.fires}", va="center",
                fontsize=8, color=INK2)
    ax.axvline(base, color=C_ORANGE, lw=1.5, ls="--")
    ax.text(base + 0.008, len(d) - 0.4, f"base rate {base:.1%}", color=C_ORANGE,
            fontsize=8.5)
    ax.set_yticks(y)
    ax.set_yticklabels(d.level, fontsize=9, color=INK)
    ax.set_xlim(-0.03, 1.0)
    ax.xaxis.set_major_formatter(lambda v, p: f"{v:.0%}")
    f.tight_layout()
    f.savefig(OUT / "conditional_class_precision.png", dpi=160, facecolor=SURFACE)
    plt.close(f)


def chart_magnitude(mag: pd.DataFrame) -> None:
    """Frequency vs size: the two questions the operator conflated."""
    d = mag[mag.median_given_tp.notna()].copy()
    f, ax = fig(9.5, 6.4)
    style(ax, "How OFTEN a class produces a ≥50% mover vs how BIG that mover is",
          "P(fromPR_High ≥ 50%)", "median fromPR_High among winners only")
    thin = d.thin
    ax.scatter(d.loc[~thin, "pct_ge50"], d.loc[~thin, "median_given_tp"],
               s=np.clip(d.loc[~thin, "n"] / 3.5, 28, 300), color=C_BLUE, alpha=0.82,
               edgecolor=SURFACE, linewidth=2, zorder=3, label="n ≥ 80")
    ax.scatter(d.loc[thin, "pct_ge50"], d.loc[thin, "median_given_tp"],
               s=np.clip(d.loc[thin, "n"] / 3.5, 28, 300), color=NEUTRAL, alpha=0.75,
               edgecolor=SURFACE, linewidth=2, zorder=3, label="n < 80 — unreadable")
    for _, r in d.iterrows():
        if r.median_given_tp > 100 or r.pct_ge50 > 0.10 or r.level in ("MA", "Partnership"):
            ax.annotate(r.level, (r.pct_ge50, r.median_given_tp),
                        textcoords="offset points", xytext=(8, 5), fontsize=8.3,
                        color=INK if not r.thin else INK2)
    ax.xaxis.set_major_formatter(lambda v, p: f"{v:.0%}")
    ax.yaxis.set_major_formatter(lambda v, p: f"{v:.0f}%")
    ax.set_xlim(0.005, d.pct_ge50.max() * 1.16)  # headroom so the MA label fits
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK2, loc="upper right")
    f.text(0.135, 0.017, "FDA_Approval is the operator's intuition, isolated: it "
           "rarely clears 50% (9.5%) but when it does the median is 311%.",
           fontsize=8.2, color=INK2)
    f.tight_layout(rect=(0, 0.035, 1, 1))
    f.savefig(OUT / "frequency_vs_size.png", dpi=160, facecolor=SURFACE)
    plt.close(f)


def chart_rule_fp(rbc: pd.DataFrame) -> None:
    """Where the false positives actually come from, by rule."""
    p = (rbc.groupby("rule")[["tp", "fp"]].sum()
         .assign(prec=lambda t: t.tp / (t.tp + t.fp))
         .sort_values("fp"))
    f, ax = fig(9, 4.6)
    style(ax, "Where the false positives come from — fires by rule (honest window)",
          "fires", "")
    y = np.arange(len(p))
    # 2px surface gap between adjacent fills is the spec; height+linewidth give it.
    ax.barh(y, p.tp, color=C_AQUA, height=0.62, edgecolor=SURFACE, linewidth=2,
            label="true positive (≥50%)", zorder=3)
    ax.barh(y, p.fp, left=p.tp, color=C_BLUE, height=0.62, edgecolor=SURFACE,
            linewidth=2, label="false positive", zorder=3)
    for i, (_, r) in enumerate(p.iterrows()):
        ax.text(r.tp + r.fp + 1.2, i, f"{r.prec:.0%}  ({int(r.tp)}/{int(r.tp+r.fp)})",
                va="center", fontsize=8.5, color=INK2)
    ax.set_yticks(y)
    ax.set_yticklabels(p.index, fontsize=9.5, color=INK)
    ax.set_xlim(0, p.sum(axis=1).max() * 1.32)
    ax.legend(frameon=False, fontsize=8.5, labelcolor=INK2, loc="lower right")
    f.tight_layout()
    f.savefig(OUT / "fp_by_rule.png", dpi=160, facecolor=SURFACE)
    plt.close(f)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    p = "outputs/08_class_trading_value_"
    fr = pd.read_csv(p + "frontier.tsv", sep="\t")
    cc = pd.read_csv(p + "conditional_classes.tsv", sep="\t")
    mag = pd.read_csv(p + "magnitude.tsv", sep="\t")
    rbc = pd.read_csv(p + "rule_by_class.tsv", sep="\t")
    base = fr[fr["shape"] == "baseline"].iloc[0].precision / \
        fr[fr["shape"] == "baseline"].iloc[0].lift
    chart_frontier(fr)
    chart_conditional(cc, base)
    chart_magnitude(mag)
    chart_rule_fp(rbc)
    print(f"wrote 4 charts to {OUT}/")


if __name__ == "__main__":
    main()
