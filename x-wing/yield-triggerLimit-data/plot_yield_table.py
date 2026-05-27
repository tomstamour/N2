#!/usr/bin/env python
"""
plot_yield_table.py - plot an X-wing yield/stop-limits table.

Reads a TSV with Yield (%), Trigger(%), Limit(%) columns and plots all three as
curves against an evenly-spaced X axis (the row index: 0,1,2,...). Saves a PNG;
pass --show to also open an interactive window.

Usage:
  /home/tom/venv/bin/python plot_yield_table.py
  /home/tom/venv/bin/python plot_yield_table.py \
      --input smoothed-yield_vs-stopLimits.tsv --output yield_table_plot.png --show
"""

import argparse
import os
import sys

import matplotlib
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))

YIELD_COL = "Yield (%)"
TRIGGER_COL = "Trigger(%)"
LIMIT_COL = "Limit(%)"


def _find(df, candidates):
    """Return the first candidate header present in df (tolerant matching)."""
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(
        f"table missing a column among {candidates}; found {list(df.columns)}"
    )


def load_table(path):
    df = pd.read_csv(path, sep="\t", comment="#")
    df.columns = [c.strip() for c in df.columns]
    ycol = _find(df, ["Yield (%)", "Yield(%)", "Yield"])
    tcol = _find(df, ["Trigger(%)", "Trigger (%)", "Trigger"])
    lcol = _find(df, ["Limit(%)", "Limit (%)", "Limit"])
    return df[[ycol, tcol, lcol]].dropna().astype(float).reset_index(drop=True), (ycol, tcol, lcol)


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", default=os.path.join(HERE, "smoothed-yield_vs-stopLimits.tsv"),
                   help="TSV with Yield (%%)/Trigger(%%)/Limit(%%) columns")
    p.add_argument("--output", default=os.path.join(HERE, "yield_table_plot.png"),
                   help="PNG output path")
    p.add_argument("--show", action="store_true",
                   help="open an interactive window in addition to saving the PNG")
    args = p.parse_args(argv)

    if not args.show:
        matplotlib.use("Agg")  # headless: save without a display
    import matplotlib.pyplot as plt  # imported after backend selection

    df, (ycol, tcol, lcol) = load_table(args.input)
    x = range(len(df))  # equidistant points on the X axis (row index)

    fig, ax = plt.subplots(figsize=(11, 6))
    ax.plot(x, df[ycol], label=YIELD_COL, color="tab:blue", linewidth=2)
    ax.plot(x, df[tcol], label=TRIGGER_COL, color="tab:orange", linewidth=2)
    ax.plot(x, df[lcol], label=LIMIT_COL, color="tab:green", linewidth=2)

    ax.set_xlabel("Row index (equidistant)")
    ax.set_ylabel("Percent (%)")
    ax.set_title(f"Yield vs Trigger / Limit  -  {os.path.basename(args.input)}")
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend()
    fig.tight_layout()

    fig.savefig(args.output, dpi=150)
    print(f"Saved plot ({len(df)} rows) -> {args.output}")
    if args.show:
        plt.show()
    return 0


if __name__ == "__main__":
    sys.exit(main())
