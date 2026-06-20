#!/usr/bin/env python3
"""
Interactive Plotly viewer for trade_mole.py output
==================================================

Turns one CSV produced by trade_mole.py into a self-contained interactive
HTML chart.

Layout (top -> bottom, all sharing the same time X axis):
  Row 1 : price + bid/ask band + microprice (left Y), volume bars (right Y)
  Rows 2-6 : five swappable variable-group panels (per-panel dropdown menu)
  Row 7 : surge-rule timeline (Rule A / B / C markers)

Usage:
    python plot_trade_mole.py --input outputs/AGPU_2026-04-22_09-19.txt
    python plot_trade_mole.py --input <file> --output some/dir/
    python plot_trade_mole.py --input <file> --output explicit.html

Tolerates both the current schema (hist_baseline_*) and the older
"_30s_baseline" schema present in pre-existing capture files.

Requires: pandas, plotly.
"""

import argparse
import os
import sys
from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


# ---------------------------------------------------------------------------
# Variable-group catalog for the 5 swappable panels (rows 2-6)
# ---------------------------------------------------------------------------
# Each group is a list of CANDIDATE column names. At load time we filter
# each group to columns that actually exist in the input CSV, so this works
# across schema versions.

GROUPS: dict[str, list[str]] = {
    "Trade rate (windows) vs baseline": [
        "trade_rate_1s", "trade_rate_2s", "trade_rate_5s", "trade_rate_10s",
        "hist_baseline_trade_rate", "trade_rate_30s_baseline",
    ],
    "Trades in window": [
        "trades_in_1s", "trades_in_2s", "trades_in_5s", "trades_in_10s",
    ],
    "Volume in window": [
        "volume_in_1s", "volume_in_2s", "volume_in_5s", "volume_in_10s",
    ],
    "Dollar vol in window": [
        "dollar_vol_in_1s", "dollar_vol_in_5s", "dollar_vol_in_10s",
    ],
    "Accel vs baseline": [
        "accel_1s_vs_hist_baseline", "accel_2s_vs_hist_baseline",
        "accel_5s_vs_hist_baseline",
        "accel_1s_vs_30s", "accel_2s_vs_30s", "accel_5s_vs_30s",
    ],
    "Accel vs 10s": [
        "accel_1s_vs_10s", "accel_2s_vs_10s", "accel_5s_vs_10s",
    ],
    "Inter-trade time": [
        "inter_trade_time_sec", "avg_iti_5s", "avg_iti_10s",
        "hist_baseline_avg_iti", "avg_iti_30s_baseline",
    ],
    "Spread": ["spread", "spread_pct"],
    "Cumulative": ["cum_volume", "cum_trade_count", "cum_dollar_volume"],
    "VWAP": ["vwap"],
    "TWS smoothed": [
        "tws_trade_rate_per_min", "tws_volume_rate_per_min", "tws_trade_count",
    ],
    "Status flags": ["halted", "shortable", "shortable_shares"],
}

DEFAULT_SLOT_GROUPS = [
    "Trade rate (windows) vs baseline",
    "Accel vs baseline",
    "Inter-trade time",
    "Volume in window",
    "Spread",
]
N_SLOTS = 5  # rows 2..6

# Row layout — 7 rows total (1 price/vol + 5 swappable + 1 surge timeline)
ROW_HEIGHTS = [0.30, 0.12, 0.12, 0.12, 0.12, 0.12, 0.10]
VERTICAL_SPACING = 0.02

SURGE_RULES = [
    ("Rule A: 1s rate jump",     "rate_1s/hist_baseline", "#ef4444"),
    ("Rule B: 5s sustained",     "rate_5s/hist_baseline", "#f59e0b"),
    ("Rule C: ITI collapse",     "iti_collapse",          "#a855f7"),
]


# ---------------------------------------------------------------------------
# CLI / paths
# ---------------------------------------------------------------------------

def resolve_output_path(input_path: str, output_arg: Optional[str]) -> str:
    """
    If --output is omitted: write `<basename>.html` next to the input.
    If --output is a directory: write `<basename>.html` inside it.
    Otherwise: treat as literal file path.
    """
    base = os.path.splitext(os.path.basename(input_path))[0] + ".html"

    if not output_arg:
        return os.path.join(os.path.dirname(os.path.abspath(input_path)), base)

    is_dir = (
        output_arg.endswith(os.sep)
        or output_arg.endswith("/")
        or os.path.isdir(output_arg)
    )
    if is_dir:
        os.makedirs(output_arg, exist_ok=True)
        return os.path.join(output_arg, base)

    parent = os.path.dirname(output_arg)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return output_arg


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load(input_path: str) -> pd.DataFrame:
    df = pd.read_csv(input_path)
    if "local_arrival_iso" not in df.columns:
        raise SystemExit("Input file is missing 'local_arrival_iso' — not a trade_mole output?")
    df["t"] = pd.to_datetime(df["local_arrival_iso"], errors="coerce")
    df = df.dropna(subset=["t"]).sort_values("t").reset_index(drop=True)
    return df


def filter_groups_to_available(df: pd.DataFrame) -> dict[str, list[str]]:
    """Drop columns that don't exist in this file; drop now-empty groups."""
    out: dict[str, list[str]] = {}
    for name, cols in GROUPS.items():
        present = [c for c in cols if c in df.columns]
        if present:
            out[name] = present
    return out


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------

def add_price_volume_row(fig: go.Figure, df: pd.DataFrame) -> None:
    """Row 1: price line + bid/ask filled band + microprice; volume bars on right Y."""
    trades = df[df["event_type"] == "TRADE"]

    # Bid/ask: forward-filled across the full event stream so band is continuous.
    qa = df[["t", "bid", "ask"]].copy()
    qa["bid"] = qa["bid"].ffill()
    qa["ask"] = qa["ask"].ffill()
    qa = qa.dropna(subset=["bid", "ask"])

    # Ask trace (drawn first, no fill) — bid trace fills DOWN to ask using "tonexty"
    # Plotly fills toward the previous trace, so order is: ask first, bid second
    # with fill="tonexty" filling the band between them.
    fig.add_trace(
        go.Scatter(
            x=qa["t"], y=qa["ask"], name="ask",
            mode="lines", line=dict(width=1, color="#94a3b8", shape="hv"),
            hovertemplate="ask %{y:.4f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=qa["t"], y=qa["bid"], name="bid",
            mode="lines", line=dict(width=1, color="#94a3b8", shape="hv"),
            fill="tonexty", fillcolor="rgba(148,163,184,0.18)",
            hovertemplate="bid %{y:.4f}<extra></extra>",
        ),
        row=1, col=1, secondary_y=False,
    )

    # Microprice overlay (only on TRADE rows where it's typically populated)
    if "microprice" in df.columns:
        mp = df[["t", "microprice"]].dropna()
        if not mp.empty:
            fig.add_trace(
                go.Scatter(
                    x=mp["t"], y=mp["microprice"], name="microprice",
                    mode="lines", line=dict(width=1, color="#22d3ee", dash="dot"),
                    hovertemplate="microprice %{y:.4f}<extra></extra>",
                ),
                row=1, col=1, secondary_y=False,
            )

    # Trade prints
    if not trades.empty:
        fig.add_trace(
            go.Scatter(
                x=trades["t"], y=trades["price"], name="trade",
                mode="lines+markers",
                line=dict(width=1.2, color="#f8fafc"),
                marker=dict(size=4, color="#f8fafc"),
                hovertemplate=(
                    "price %{y:.4f}<br>size %{customdata[0]}<br>"
                    "exch %{customdata[1]}<extra></extra>"
                ),
                customdata=trades[["size", "exchange"]].values,
            ),
            row=1, col=1, secondary_y=False,
        )

        # Volume bars on right Y (per-trade size)
        fig.add_trace(
            go.Bar(
                x=trades["t"], y=trades["size"], name="size",
                marker=dict(color="#3b82f6", opacity=0.45),
                hovertemplate="size %{y}<extra></extra>",
            ),
            row=1, col=1, secondary_y=True,
        )

    fig.update_yaxes(title_text="price", row=1, col=1, secondary_y=False)
    fig.update_yaxes(title_text="size",  row=1, col=1, secondary_y=True,
                     showgrid=False)


def add_swappable_panels(
    fig: go.Figure,
    df: pd.DataFrame,
    groups: dict[str, list[str]],
) -> tuple[list[list[int]], list[list[str]], list[int]]:
    """
    For each of the 5 swappable rows (rows 2..6), add ALL group traces but
    only mark the default group visible. Returns trace-index bookkeeping
    needed to build the per-row dropdown menus.

    Returns
    -------
    slot_trace_indices : list of length N_SLOTS, each a list of fig-trace indices
                         belonging to that slot (across all groups).
    slot_trace_groups  : parallel structure storing which group each trace
                         belongs to, so we can build the visibility mask.
    pre_slot_trace_count : number of traces added BEFORE this function ran
                           (i.e. row 1 traces). Useful for offsetting indices.
    """
    pre_count = len(fig.data)
    slot_trace_indices: list[list[int]] = [[] for _ in range(N_SLOTS)]
    slot_trace_groups:  list[list[str]] = [[] for _ in range(N_SLOTS)]

    group_names = list(groups.keys())

    # Pick a working default for each slot — fall back if user-preferred default isn't available.
    slot_defaults: list[str] = []
    for i in range(N_SLOTS):
        preferred = DEFAULT_SLOT_GROUPS[i] if i < len(DEFAULT_SLOT_GROUPS) else None
        if preferred and preferred in groups:
            slot_defaults.append(preferred)
        else:
            # fall back to i-th available group, wrapping
            slot_defaults.append(group_names[i % len(group_names)])

    palette = ["#60a5fa", "#f472b6", "#34d399", "#fbbf24", "#c084fc",
               "#fb7185", "#22d3ee", "#a3e635"]

    for slot_idx in range(N_SLOTS):
        row = slot_idx + 2  # rows 2..6
        default_group = slot_defaults[slot_idx]

        for gname in group_names:
            cols = groups[gname]
            visible = (gname == default_group)
            for j, col in enumerate(cols):
                series = df[["t", col]].dropna()
                if series.empty:
                    # Add an empty trace anyway so trace indexing stays stable across slots
                    series = pd.DataFrame({"t": [], col: []})
                trace_idx = len(fig.data)
                fig.add_trace(
                    go.Scatter(
                        x=series["t"], y=series[col],
                        name=col,
                        mode="lines",
                        line=dict(width=1.2, color=palette[j % len(palette)]),
                        legendgroup=f"slot{slot_idx}-{gname}",
                        showlegend=False,
                        visible=visible,
                        hovertemplate=f"{col} %{{y}}<extra></extra>",
                    ),
                    row=row, col=1,
                )
                slot_trace_indices[slot_idx].append(trace_idx)
                slot_trace_groups[slot_idx].append(gname)

    return slot_trace_indices, slot_trace_groups, pre_count


def add_surge_timeline(fig: go.Figure, df: pd.DataFrame) -> None:
    """Row 7: three marker traces, one per surge rule."""
    if "surge_reason" not in df.columns or "surge_detected" not in df.columns:
        return
    surge_flag = df["surge_detected"].astype("string").fillna("").str.lower()
    fired = df[surge_flag.isin(("true", "1"))].copy()
    if fired.empty:
        # Add one invisible empty trace so the row still renders with a y axis
        fig.add_trace(
            go.Scatter(x=[], y=[], mode="markers", showlegend=False),
            row=7, col=1,
        )
        return

    reasons = fired["surge_reason"].fillna("").astype(str)
    for rule_label, needle, color in SURGE_RULES:
        mask = reasons.str.contains(needle, regex=False)
        sub = fired[mask]
        fig.add_trace(
            go.Scatter(
                x=sub["t"],
                y=[rule_label] * len(sub),
                mode="markers",
                name=rule_label,
                marker=dict(size=10, color=color, symbol="diamond"),
                showlegend=True,
                customdata=sub[["surge_reason", "price"]].values if not sub.empty else [],
                hovertemplate=(
                    "%{x}<br>" + rule_label + "<br>"
                    "price %{customdata[1]:.4f}<br>"
                    "reason %{customdata[0]}<extra></extra>"
                ),
            ),
            row=7, col=1,
        )


def build_per_panel_menus(
    n_total_traces: int,
    pre_count: int,
    slot_trace_indices: list[list[int]],
    slot_trace_groups: list[list[str]],
    groups: dict[str, list[str]],
) -> list[dict]:
    """
    Build one Plotly updatemenu dropdown per swappable slot. Each dropdown's
    buttons set the `visible` array such that only the chosen group's traces
    in THAT slot are visible; other slots and the row-1/row-7 traces are
    untouched (we read+rewrite the full-length visible mask).

    Pinned to the figure's left margin at the vertical center of each slot row.
    """
    group_names = list(groups.keys())
    menus = []

    # Compute paper-space y for each row's vertical center.
    # Subplots stack from row 1 (top) to row 7 (bottom).
    plot_area = 1.0 - VERTICAL_SPACING * (len(ROW_HEIGHTS) - 1)
    scaled = [h * plot_area for h in ROW_HEIGHTS]
    row_tops: list[float] = []
    cursor = 1.0
    for h in scaled:
        row_tops.append(cursor)
        cursor -= h + VERTICAL_SPACING
    # Center y for row r (1-indexed) in paper coords:
    def row_center_y(r: int) -> float:
        return row_tops[r - 1] - scaled[r - 1] / 2.0

    # Precompute the union set of swappable trace indices so we can quickly
    # build a "default visible mask" by reading the figure (we do this in
    # build_figure where we have access to fig.data — here we just emit the
    # visibility-update args using restyle on the slot's trace indices.

    for slot_idx in range(N_SLOTS):
        row = slot_idx + 2
        slot_traces = slot_trace_indices[slot_idx]
        slot_groups_per_trace = slot_trace_groups[slot_idx]

        buttons = []
        # Determine default by checking which group has any visible trace
        for gname in group_names:
            # visibility array for THIS slot's traces only
            this_slot_visible = [g == gname for g in slot_groups_per_trace]
            buttons.append(dict(
                label=gname,
                method="restyle",
                args=[{"visible": this_slot_visible}, slot_traces],
            ))

        menus.append(dict(
            type="dropdown",
            direction="down",
            buttons=buttons,
            x=0.005, xanchor="left",
            y=row_center_y(row), yanchor="middle",
            pad=dict(l=2, r=2, t=2, b=2),
            bgcolor="rgba(30,41,59,0.85)",
            bordercolor="#475569",
            font=dict(size=10, color="#e2e8f0"),
            showactive=True,
        ))

    return menus


# ---------------------------------------------------------------------------
# Main figure assembly
# ---------------------------------------------------------------------------

def build_figure(df: pd.DataFrame, title: str) -> go.Figure:
    fig = make_subplots(
        rows=7, cols=1,
        shared_xaxes=True,
        vertical_spacing=VERTICAL_SPACING,
        row_heights=ROW_HEIGHTS,
        specs=[
            [{"secondary_y": True}],
            [{}], [{}], [{}], [{}], [{}],
            [{}],
        ],
    )

    add_price_volume_row(fig, df)

    groups = filter_groups_to_available(df)
    if not groups:
        raise SystemExit("No plottable columns found in input file.")

    slot_idx, slot_grp, pre_count = add_swappable_panels(fig, df, groups)

    add_surge_timeline(fig, df)

    menus = build_per_panel_menus(
        n_total_traces=len(fig.data),
        pre_count=pre_count,
        slot_trace_indices=slot_idx,
        slot_trace_groups=slot_grp,
        groups=groups,
    )

    fig.update_layout(
        title=title,
        template="plotly_dark",
        height=1400,
        hovermode="x unified",
        margin=dict(l=160, r=40, t=70, b=30),
        legend=dict(orientation="h", y=1.02, x=0),
        updatemenus=menus,
        bargap=0.0,
    )
    fig.update_xaxes(rangeslider=dict(visible=False))

    # Make row 7's category axis show all three rules even if some are empty
    fig.update_yaxes(
        row=7, col=1,
        categoryorder="array",
        categoryarray=[r[0] for r in SURGE_RULES],
    )

    return fig


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(
        description="Render a trade_mole.py output CSV as an interactive Plotly HTML chart.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--input", required=True, help="Path to a trade_mole.py output .txt/.csv file")
    p.add_argument("--output", default=None,
                   help="Output .html path or directory. Default: sibling of --input.")
    p.add_argument("--title", default=None, help="Override chart title.")
    args = p.parse_args()

    if not os.path.isfile(args.input):
        sys.exit(f"Input file not found: {args.input}")

    df = load(args.input)

    # Title
    base = os.path.splitext(os.path.basename(args.input))[0]
    baseline_val = None
    for col in ("hist_baseline_trade_rate", "trade_rate_30s_baseline"):
        if col in df.columns:
            vals = df[col].dropna()
            if not vals.empty:
                baseline_val = float(vals.iloc[0])
                break
    title = args.title or (
        f"{base}" + (f"  —  baseline ≈ {baseline_val:.3f}/s" if baseline_val else "")
    )

    fig = build_figure(df, title=title)

    output_path = resolve_output_path(args.input, args.output)
    fig.write_html(output_path, include_plotlyjs="cdn", full_html=True)
    print(f"Wrote {output_path}  ({len(df)} rows, {len(fig.data)} traces)")


if __name__ == "__main__":
    main()
