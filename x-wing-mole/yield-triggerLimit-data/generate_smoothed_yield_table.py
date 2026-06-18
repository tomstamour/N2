#!/usr/bin/env python
"""
generate_smoothed_yield_table.py - densify the X-wing yield/stop-limits table.

Reads a coarse, hand-entered TSV (Yield (%), Trigger(%), Limit(%)) and rewrites it
with Yield (%) on a fine, evenly-spaced grid (default 1% steps: 0,1,2,...,100). The
Trigger(%) and Limit(%) columns are filled by PCHIP interpolation
(scipy.interpolate.PchipInterpolator) - a monotonic, shape-preserving cubic. PCHIP
follows the existing up-then-plateau progression without overshooting, so the flat
top of the source curve (Trigger 22 / Limit 24) stays flat.

Output uses the canonical headers `Yield (%)\tTrigger(%)\tLimit(%)`, so it is a drop-in
`--input-limits-table` for X-wing-1.0.py (LimitsTable loads it unchanged).

Usage:
  /home/tom/venv/bin/python generate_smoothed_yield_table.py
  /home/tom/venv/bin/python generate_smoothed_yield_table.py \
      --input example-yield_vs-stopLimits.tsv \
      --output smoothed-yield_vs-stopLimits.tsv --step 1.0 --decimals 2
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
from scipy.interpolate import PchipInterpolator

HERE = os.path.dirname(os.path.abspath(__file__))

# Canonical output headers (kept identical to what X-wing's LimitsTable expects).
YIELD_COL = "Yield (%)"
TRIGGER_COL = "Trigger(%)"
LIMIT_COL = "Limit(%)"


def _find(df, candidates):
    """Return the first candidate header present in df (tolerant matching).

    Mirrors LimitsTable._find() in X-wing-1.0.py so both read the TSV identically.
    """
    for c in candidates:
        if c in df.columns:
            return c
    raise ValueError(
        f"table missing a column among {candidates}; found {list(df.columns)}"
    )


def load_source(path):
    """Load the source TSV into sorted, float (yield, trigger, limit) arrays."""
    df = pd.read_csv(path, sep="\t", comment="#")
    df.columns = [c.strip() for c in df.columns]
    ycol = _find(df, ["Yield (%)", "Yield(%)", "Yield"])
    tcol = _find(df, ["Trigger(%)", "Trigger (%)", "Trigger"])
    lcol = _find(df, ["Limit(%)", "Limit (%)", "Limit"])
    rows = df[[ycol, tcol, lcol]].dropna().astype(float)
    rows = rows.drop_duplicates(subset=ycol).sort_values(ycol).reset_index(drop=True)
    if len(rows) < 2:
        raise ValueError(f"need >=2 usable rows to interpolate; got {len(rows)} from {path!r}")
    return rows[ycol].to_numpy(), rows[tcol].to_numpy(), rows[lcol].to_numpy()


def build_table(yield_src, trigger_src, limit_src, step, decimals):
    """Return a DataFrame with Yield on a `step`-spaced grid and PCHIP'd Trigger/Limit."""
    y0, y1 = float(yield_src[0]), float(yield_src[-1])
    # +step/2 so the inclusive endpoint (e.g. 100) is not dropped by float rounding.
    grid = np.arange(y0, y1 + step / 2.0, step)

    trigger = PchipInterpolator(yield_src, trigger_src)(grid)
    limit = PchipInterpolator(yield_src, limit_src)(grid)

    # Safety guards (expected to be no-ops with PCHIP on this monotone data):
    #   - keep percentages non-negative
    #   - keep Limit >= Trigger so lmtPrice <= auxPrice (X-wing's lmt<=aux invariant)
    trigger = np.clip(trigger, 0.0, None)
    limit = np.clip(limit, 0.0, None)
    limit = np.maximum(limit, trigger)

    return pd.DataFrame(
        {
            YIELD_COL: np.round(grid, decimals),
            TRIGGER_COL: np.round(trigger, decimals),
            LIMIT_COL: np.round(limit, decimals),
        }
    )


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--input", default=os.path.join(HERE, "example-yield_vs-stopLimits.tsv"),
                   help="source TSV with Yield (%%)/Trigger(%%)/Limit(%%) columns")
    p.add_argument("--output", default=os.path.join(HERE, "smoothed-yield_vs-stopLimits.tsv"),
                   help="destination TSV (101 rows at the default 1%% step)")
    p.add_argument("--step", type=float, default=1.0,
                   help="yield increment in percent for the output grid")
    p.add_argument("--decimals", type=int, default=2,
                   help="decimal places for Trigger/Limit (and the yield grid)")
    args = p.parse_args(argv)

    if args.step <= 0:
        p.error("--step must be > 0")

    yield_src, trigger_src, limit_src = load_source(args.input)
    table = build_table(yield_src, trigger_src, limit_src, args.step, args.decimals)
    table.to_csv(args.output, sep="\t", index=False)

    print(f"Wrote {len(table)} rows -> {args.output}")
    print(f"  Yield   : {table[YIELD_COL].iloc[0]:g} .. {table[YIELD_COL].iloc[-1]:g} "
          f"(step {args.step:g})")
    print(f"  Trigger : {table[TRIGGER_COL].min():g} .. {table[TRIGGER_COL].max():g}")
    print(f"  Limit   : {table[LIMIT_COL].min():g} .. {table[LIMIT_COL].max():g}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
