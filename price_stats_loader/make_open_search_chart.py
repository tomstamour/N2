#!/usr/bin/env python3
"""Chart for 09_open_search.md: held-out TP:FP vs volume, coloured by deploy tier.

Palette validated with the dataviz skill (3 categorical hues, light surface, ALL
CHECKS PASS). #1baf7a carries a contrast WARN so tier-1 marks are directly labelled.
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
TIER_COLOR = {1: C_AQUA, 2: C_BLUE, 3: C_ORANGE}
TIER_LABEL = {1: "tier 1 — already live (free)",
              2: "tier 2 — +1 headline encoder",
              3: "tier 3 — needs body encoding"}
OUT = Path("outputs/charts_09_open_search")

# Reference points measured in 09_open_search.md on the same held-out block.
GATE = (3.3, 0.23, "live O5.2 gate")
CLASS_ONLY = [(3.3, 0.19, "MA alone, no score cut"),
              (6.0, 0.22, "MA+Partnership alone")]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    res = pd.read_csv("outputs/09_open_search.tsv", sep="\t")
    d = res[(res.te_fires >= 10) & np.isfinite(res.te_tp_fp)].copy()

    f, ax = plt.subplots(figsize=(10, 6.4), facecolor=SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title("Open search — held-out TP:FP vs volume, by cost to deploy\n"
                 "every point selected on train, scored once on test",
                 color=INK, fontsize=12, fontweight="600", loc="left", pad=12)
    ax.set_xlabel("trades per day (calibrated to the live 3.3/day)", color=INK2, fontsize=9)
    ax.set_ylabel("TP:FP on the held-out block", color=INK2, fontsize=9)
    ax.tick_params(colors=INK2, labelsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(GRID)
    ax.grid(True, color=GRID, lw=0.8, alpha=0.9)
    ax.set_axisbelow(True)

    # The two lines a candidate has to clear to mean anything.
    ax.axhline(GATE[1], color=INK, lw=1.3, ls=":")
    # Parked out at x=4.2: the candidate cloud fills everything below ~2 trades/day.
    ax.text(4.2, GATE[1] + 0.014, f"live gate TP:FP {GATE[1]:.2f} — the bar to clear",
            color=INK, fontsize=8.5, fontweight="600")
    for x, yv, lab in CLASS_ONLY:
        ax.scatter([x], [yv], marker="s", s=80, color=NEUTRAL, zorder=4,
                   edgecolor=SURFACE, linewidth=2)
        ax.annotate(lab, (x, yv), textcoords="offset points", xytext=(-8, -16),
                    fontsize=8, color=INK2, ha="right")

    for t in (3, 2, 1):  # fixed order, cheapest drawn last so it sits on top
        g = d[d.tier == t]
        if g.empty:
            continue
        ax.scatter(g.te_trades_day, g.te_tp_fp, s=np.clip(g.te_fires * 3, 30, 220),
                   color=TIER_COLOR[t], alpha=0.85, edgecolor=SURFACE, linewidth=1.8,
                   zorder=3, label=TIER_LABEL[t])
    # Direct-label the best of each tier - the aqua contrast WARN requires it.
    for t in (1, 2, 3):
        g = d[d.tier == t]
        if g.empty:
            continue
        r = g.sort_values("te_tp_fp", ascending=False).iloc[0]
        ax.annotate(f"best tier {t}: {r.te_tp_fp:.2f}", (r.te_trades_day, r.te_tp_fp),
                    textcoords="offset points", xytext=(10, 4), fontsize=8.5,
                    color=INK, fontweight="600")

    ax.set_xlim(0, max(7.0, d.te_trades_day.max() * 1.15))
    ax.set_ylim(0, d.te_tp_fp.max() * 1.18)
    lg = ax.legend(frameon=False, fontsize=8.5, loc="upper right", labelcolor=INK2)
    lg.set_title("deployment cost", prop={"size": 8.5})
    f.text(0.125, 0.015, "Marker area = held-out fire count. Points below the dotted "
           "line lose to the gate that already runs.", fontsize=8.2, color=INK2)
    f.tight_layout(rect=(0, 0.035, 1, 1))
    f.savefig(OUT / "tpfp_vs_volume.png", dpi=160, facecolor=SURFACE)
    plt.close(f)
    print(f"wrote {OUT}/tpfp_vs_volume.png")


if __name__ == "__main__":
    main()
