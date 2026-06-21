#!/usr/bin/env python
"""
plot_yield_table_terminal.py - terminal-readable X-wing yield/stop-limits plot.

Reads a TSV with Yield (%), Trigger(%), Limit(%) columns and renders all three as
curves into the terminal using plotext (ANSI-colored text). The output is also
saved to a text file so it can be opened/cat'd remotely over SSH.

Curves use distinct markers (solid / dotted / dashed) and dark colors only
(no yellow), so each is identifiable by line style alone in case colors are
stripped.

Usage:
  path/to/venv/bin/python plot_yield_table_terminal.py
  path/to/venv/bin/python plot_yield_table_terminal.py \\
      --input smoothed-yield_vs-stopLimits.tsv \\
      --output yield_table_plot.txt --no-show
"""

import argparse
import os
import sys

import pandas as pd
import plotext as plt

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
    p.add_argument("--output", default=os.path.join(HERE, "yield_table_plot.txt"),
                   help="text output path (ANSI-colored; cat to view)")
    p.add_argument("--no-show", action="store_true",
                   help="do not print the plot to the terminal (still saves the file)")
    p.add_argument("--width", type=int, default=120, help="plot width in characters")
    p.add_argument("--height", type=int, default=30, help="plot height in characters")
    args = p.parse_args(argv)

    df, (ycol, tcol, lcol) = load_table(args.input)
    x = list(range(len(df)))

    plt.clear_figure()
    plt.plotsize(args.width, args.height)
    plt.theme("clear")
    plt.title(f"Yield vs Trigger / Limit  -  {os.path.basename(args.input)}")
    plt.xlabel("Row index (equidistant)")
    plt.ylabel("Percent (%)")
    plt.grid(True, True)

    # Solid / dotted / dashed markers, all dark colors (no yellow).
    plt.plot(x, df[ycol].tolist(), label=YIELD_COL,   color="blue",    marker="hd")
    plt.plot(x, df[tcol].tolist(), label=TRIGGER_COL, color="red",     marker="dot")
    plt.plot(x, df[lcol].tolist(), label=LIMIT_COL,   color="magenta", marker="braille")

    plt.build()  # populate canvas so save_fig works even without show()
    if not args.no_show:
        plt.show()

    plt.save_fig(args.output, keep_colors=True)
    print(f"Saved plot ({len(df)} rows) -> {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
