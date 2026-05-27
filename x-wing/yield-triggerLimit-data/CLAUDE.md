# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

Tooling to produce and inspect the `--input-limits-table` TSV consumed by `X-wing-1.0.py`. The typical workflow is:

1. Hand-edit a coarse table (like `example-yield_vs-stopLimits.tsv`) with key yield breakpoints.
2. Run `generate_smoothed_yield_table.py` to densify it via PCHIP interpolation → `smoothed-yield_vs-stopLimits.tsv`.
3. Optionally run `plot_yield_table.py` to visually verify the curve shape.
4. Pass the smoothed TSV to X-wing as `--input-limits-table smoothed-yield_vs-stopLimits.tsv`.

## Commands

```bash
PY=/home/tom/venv/bin/python

# Regenerate smoothed table from example source (default args)
$PY generate_smoothed_yield_table.py

# Custom step or decimal precision
$PY generate_smoothed_yield_table.py \
    --input example-yield_vs-stopLimits.tsv \
    --output smoothed-yield_vs-stopLimits.tsv \
    --step 0.5 --decimals 3

# Save plot PNG (headless)
$PY plot_yield_table.py

# Save PNG and open interactive window
$PY plot_yield_table.py --input smoothed-yield_vs-stopLimits.tsv --show
```

## Table format

Required columns (tab-separated, `#` comments supported): `Yield (%)`, `Trigger(%)`, `Limit(%)`.
Column-name matching is tolerant (strips whitespace; accepts `Yield`, `Trigger`, `Limit` variants).
Any extra columns (e.g. illustrative `auxPrice ($)`) are ignored by both scripts and by X-wing's `LimitsTable`.

## Interpolation invariants

`generate_smoothed_yield_table.py` enforces two hard constraints after PCHIP interpolation:
- `Trigger(%)` and `Limit(%)` are clipped to `≥ 0`.
- `Limit(%) ≥ Trigger(%)` always — preserving X-wing's `lmtPrice ≤ auxPrice` invariant.

PCHIP (monotone cubic) is chosen because it follows the up-then-plateau shape of the source data without overshooting, so the flat top (e.g. Trigger 22 / Limit 24) stays exactly flat.

## Dependencies

`numpy`, `pandas`, `scipy` (generator) and `matplotlib` (plotter) — all installed in `/home/tom/venv`. Use that interpreter; the system `python3` lacks these packages.

## Relationship to X-wing

See the parent directory's `CLAUDE.md` for how X-wing consumes the table (banded row selection, price formula, ratcheting). The `_find()` helper in `generate_smoothed_yield_table.py` deliberately mirrors `LimitsTable._find()` in `X-wing-1.0.py` so both read TSVs identically.
