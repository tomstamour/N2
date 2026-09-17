#!/usr/bin/env python3
"""Plot fromPR_High(%) for every PR and colour the subset matching an AND gate.

Y axis = fromPR_High(%); X axis = PR # in TSV row order (evenly spaced, so points
never pile up in one column). Points satisfying ALL --and-operand conditions are
blue, everything else black.

Example:
    plot_sentiment_AND-logics.py \\
        --input outputs/05_PRstats_..._FromPRhigh.tsv \\
        --and-operand FLANG_pos_hd '>=' 0.7 \\
        --and-operand kw15_proba_hd 0.6
"""
import argparse
import difflib
import math
import operator
import pathlib
import sys

import pandas as pd

try:
    import plotext as plt
except ImportError:
    sys.exit("Install plotext: pip install plotext")

DEFAULT_Y_COLUMN = "fromPR_High(%)"

OPS = {
    ">":  operator.gt,
    ">=": operator.ge,
    "<":  operator.lt,
    "<=": operator.le,
    "==": operator.eq,
    "!=": operator.ne,
}

# plotext's default theme paints the canvas with 256-colour index 15 and draws in
# index 0. Over SSH those indices get remapped by the remote palette (Solarized
# maps 15 to a cream/yellow), so the plot looks washed out. The 'clear' theme
# paints no background at all and inherits the terminal's own fg/bg.
# --marker-column: one plot character per category, handed out most-frequent-first.
MARKER_LETTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
OVERFLOW_MARKER = "*"   # categories past the end of MARKER_LETTERS
BLANK_MARKER = "."      # empty / missing category
BLANK_CATEGORY = "(blank)"

DEFAULT_THEME = "clear"
MATCH_COLOR = "red"
OTHER_COLOR = "default"  # terminal's own foreground: readable on light AND dark


TRUE_WORDS = {"true", "t", "yes", "y", "1", "on"}
FALSE_WORDS = {"false", "f", "no", "n", "0", "off"}


def str_to_bool(value):
    """<true|false> argument value, case-insensitive."""
    text = str(value).strip().lower()
    if text in TRUE_WORDS:
        return True
    if text in FALSE_WORDS:
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {value!r}")


def parse_operands(raw_operands, parser):
    """Turn the raw --and-operand token lists into [(col, op_str, value)]."""
    operands = []
    for tokens in raw_operands or []:
        if len(tokens) == 2:
            col, op_str, value = tokens[0], ">", tokens[1]
        elif len(tokens) == 3:
            col, op_str, value = tokens
        else:
            parser.error(
                f"--and-operand takes 2 or 3 values (COL [OP] VALUE), got {len(tokens)}: {tokens}"
            )
        if op_str not in OPS:
            parser.error(
                f"--and-operand: unknown operator {op_str!r} for column {col!r}; "
                f"expected one of {' '.join(OPS)}"
            )
        try:
            value = float(value)
        except ValueError:
            parser.error(f"--and-operand: threshold {value!r} for column {col!r} is not a number")
        operands.append((col, op_str, value))
    return operands


def check_columns(df, operands, y_column, extra_columns=()):
    """Fail loudly (with close matches) when a requested column is absent."""
    wanted = [y_column] + [col for col, _, _ in operands] + [c for c in extra_columns if c]
    missing = [col for col in wanted if col not in df.columns]
    if not missing:
        return
    lines = []
    for col in missing:
        close = difflib.get_close_matches(col, list(df.columns), n=8, cutoff=0.4)
        if not close:
            close = [c for c in df.columns if col.lower() in c.lower()][:8]
        hint = ", ".join(close) if close else "(no similar column names found)"
        lines.append(f"  {col!r} not in file. Closest: {hint}")
    sys.exit("Column(s) not found:\n" + "\n".join(lines))


def build_mask(df, operands):
    """AND across all operands. NaN in any operand column => non-matching."""
    mask = pd.Series(True, index=df.index)
    for col, op_str, value in operands:
        col_vals = df[col]
        mask &= OPS[op_str](col_vals, value) & col_vals.notna()
    return mask


def category_values(df, column):
    """The marker column as clean strings, with empties folded into BLANK_CATEGORY."""
    values = df[column].astype(str).str.strip()
    return values.mask(values.isin(["", "nan", "NaN", "None", "<NA>"]), BLANK_CATEGORY)


def assign_markers(values):
    """Map each category to one plot character, most frequent category first.

    Returns (marker_by_category, overflow) where overflow is the list of categories
    that ran past the alphabet and all share OVERFLOW_MARKER.
    """
    counts = values.value_counts()
    ordered = [c for c in counts.index if c != BLANK_CATEGORY]
    marker_by_category, overflow = {}, []
    for i, category in enumerate(ordered):
        if i < len(MARKER_LETTERS):
            marker_by_category[category] = MARKER_LETTERS[i]
        else:
            marker_by_category[category] = OVERFLOW_MARKER
            overflow.append(category)
    if BLANK_CATEGORY in counts.index:
        marker_by_category[BLANK_CATEGORY] = BLANK_MARKER
    return marker_by_category, overflow


def marker_legend(column, values, marker_by_category, mask, overflow):
    """Text legend printed under the plot: letter, category, totals."""
    counts = values.value_counts()
    lines = [f"Point letters ({column}):"]
    for category, marker in sorted(marker_by_category.items(), key=lambda kv: (kv[1], kv[0])):
        matched = int((mask & (values == category)).sum())
        lines.append(f"  {marker}  {category:<28} n={counts[category]:<5} matched={matched}")
    if overflow:
        lines.append(f"  {OVERFLOW_MARKER}  shared by {len(overflow)} further categories "
                     f"(more categories than plot letters): {', '.join(overflow)}")
    return "\n".join(lines)


def gate_label(operands):
    if not operands:
        return "no operands"
    return " AND ".join(f"{col}{op}{value:g}" for col, op, value in operands)


def summarize(name, series):
    if series.empty:
        return f"  {name:<13} n=0"
    return (
        f"  {name:<13} n={len(series):<5} "
        f"mean={series.mean():8.2f}  median={series.median():8.2f}  max={series.max():8.2f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scatter fromPR_High(%) and highlight rows matching an AND gate.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--input", required=True, type=pathlib.Path, help="input TSV path")
    parser.add_argument(
        "--and-operand",
        dest="and_operand",
        action="append",
        nargs="+",
        metavar="COL [OP] VALUE",
        help="repeatable condition, e.g. --and-operand FLANG_pos_hd '>=' 0.7 "
             "(operator defaults to '>' when omitted)",
    )
    parser.add_argument("--y-column", default=DEFAULT_Y_COLUMN,
                        # argparse %-expands help strings, so '%' must be escaped
                        help=f"column plotted on Y (default: {DEFAULT_Y_COLUMN.replace('%', '%%')})")
    parser.add_argument("--status-ok-only", action="store_true",
                        help="drop rows whose fromPR_status is not 'ok'")
    parser.add_argument("--plot-size", nargs=2, type=int, default=[110, 30],
                        metavar=("W", "H"), help="terminal plot size (default: 110 30)")
    parser.add_argument("--marker-column", "--shape-column", dest="marker_column",
                        default=None, metavar="COL",
                        help="draw each point as a letter according to this text column "
                             "(e.g. FineClass); letters are handed out most-frequent-first "
                             "and listed under the plot. Colour still means match/no-match")
    parser.add_argument("--log-y-axis", "-log-y-axis", dest="log_y_axis",
                        nargs="?", const=True, default=False, type=str_to_bool,
                        metavar="true|false",
                        help="plot Y on a base-10 logarithmic scale (default: false). "
                             "Rows with a non-positive Y have no logarithm and are dropped")
    parser.add_argument("--top-y-value", type=float, default=None, metavar="Y",
                        help="cap the Y axis at this value (points above it are off-canvas; "
                             "no row is dropped and the stats below still use every row)")
    parser.add_argument("--theme", default=DEFAULT_THEME,
                        help=f"plotext theme (default: {DEFAULT_THEME}, which paints no "
                             "background and inherits the terminal colours)")
    parser.add_argument("--match-color", default=MATCH_COLOR,
                        help=f"colour of matching points (default: {MATCH_COLOR})")
    parser.add_argument("--other-color", default=OTHER_COLOR,
                        help=f"colour of non-matching points (default: {OTHER_COLOR} = "
                             "terminal foreground)")
    parser.add_argument("--no-save", action="store_true",
                        help="do not write the .plotext file next to the input")
    args = parser.parse_args()

    tsv_path = args.input
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")

    operands = parse_operands(args.and_operand, parser)

    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    print(f"Loaded {len(df)} rows from {tsv_path}")

    check_columns(df, operands, args.y_column, extra_columns=[args.marker_column])

    if args.status_ok_only:
        if "fromPR_status" not in df.columns:
            sys.exit("--status-ok-only requested but there is no fromPR_status column")
        before = len(df)
        df = df[df["fromPR_status"].astype(str).str.strip().str.lower() == "ok"]
        print(f"  fromPR_status == ok: kept {len(df)} of {before} rows")

    df[args.y_column] = pd.to_numeric(df[args.y_column], errors="coerce")
    for col, _, _ in operands:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    before = len(df)
    df = df.dropna(subset=[args.y_column]).reset_index(drop=True)
    dropped = before - len(df)
    if dropped:
        print(f"  Dropped {dropped} rows with non-numeric {args.y_column}")
    if df.empty:
        sys.exit(f"No rows left with a numeric {args.y_column}")

    if args.log_y_axis:
        before = len(df)
        df = df[df[args.y_column] > 0].reset_index(drop=True)
        dropped = before - len(df)
        if dropped:
            print(f"  log Y axis: dropped {dropped} rows with {args.y_column} <= 0 "
                  "(no logarithm)")
        if df.empty:
            sys.exit(f"No rows left with a positive {args.y_column} for a log axis")

    mask = build_mask(df, operands) if operands else pd.Series(False, index=df.index)
    x = list(range(1, len(df) + 1))
    y = df[args.y_column].tolist()

    match_x = [xi for xi, m in zip(x, mask) if m]
    match_y = [yi for yi, m in zip(y, mask) if m]
    other_x = [xi for xi, m in zip(x, mask) if not m]
    other_y = [yi for yi, m in zip(y, mask) if not m]

    match_marker = other_marker = "dot"
    legend_text = None
    if args.marker_column:
        values = category_values(df, args.marker_column)
        marker_by_category, overflow = assign_markers(values)
        markers = [marker_by_category[v] for v in values]
        match_marker = [mk for mk, m in zip(markers, mask) if m]
        other_marker = [mk for mk, m in zip(markers, mask) if not m]
        legend_text = marker_legend(args.marker_column, values, marker_by_category,
                                    mask, overflow)

    plt.clf()
    plt.theme(args.theme)
    plt.plotsize(*args.plot_size)
    plt.title(f"{args.y_column} — {gate_label(operands)} (n={len(match_x)}/{len(df)}) — {tsv_path.name}")
    plt.xlabel("PR # (row order)")
    plt.ylabel(args.y_column + (" (log10)" if args.log_y_axis else ""))
    if args.log_y_axis:
        plt.yscale("log")  # plotext's log scale is base 10

    # with per-point letters the in-plot legend cannot show a single marker, so the
    # match/no-match key moves to the text legend printed under the plot
    labels = (None, None) if args.marker_column else ("no match", "match")
    if other_x:
        plt.scatter(other_x, other_y, color=args.other_color,
                    marker=other_marker, label=labels[0])
    if match_x:
        plt.scatter(match_x, match_y, color=args.match_color,
                    marker=match_marker, label=labels[1])

    if args.top_y_value is not None:
        y_min = min(y)
        if args.top_y_value <= y_min:
            sys.exit(f"--top-y-value {args.top_y_value:g} is at or below the smallest "
                     f"{args.y_column} ({y_min:g}); nothing would be visible")
        # on a log axis plotext wants the limits already in log10 units
        if args.log_y_axis:
            plt.ylim(math.log10(y_min), math.log10(args.top_y_value))
        else:
            plt.ylim(y_min, args.top_y_value)
        above = sum(1 for yi in y if yi > args.top_y_value)
        if above:
            print(f"  Y axis capped at {args.top_y_value:g}: "
                  f"{above} point(s) plot above the top and are not drawn")

    # build() once and print it: plt.show() would rebuild, and plotext rescales the
    # data in place, so a second build re-applies log10 to already-logged values.
    plot_str = plt.build()
    print(plot_str)
    print()

    if legend_text:
        print(f"Colour: {args.match_color} = match, {args.other_color} = no match")
        print(legend_text)
        print()

    print(f"Gate: {gate_label(operands)}")
    print(summarize("matched", df.loc[mask, args.y_column]))
    print(summarize("not matched", df.loc[~mask, args.y_column]))

    if not args.no_save:
        out_path = tsv_path.parent / f"{tsv_path.stem}_ANDlogics.plotext"
        out_path.write_text(plot_str, encoding="utf-8")
        print(f"\nSaved → {out_path}")


if __name__ == "__main__":
    main()
