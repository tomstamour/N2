#!/usr/bin/env python3
import sys
import pathlib
import pandas as pd

try:
    import plotext as plt
except ImportError:
    sys.exit("Install plotext: pip install plotext")

DEFAULT_INPUT = pathlib.Path("outputs/concatenated_enriched_FinBERT.tsv")

SENTIMENT_COLUMNS = [
    "sentiment_score",
    "neutral_filter",
    "confidence_weighted",
    "net_score",
    "top_k",
    "positional",
]

COLOR_MAP = {
    "positive": "red",
    "neutral":  "red",
    "negative": "red",
}


def load_data(tsv_path: pathlib.Path) -> pd.DataFrame:
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    df["DailyHigh(%)"] = pd.to_numeric(df["DailyHigh(%)"], errors="coerce")
    for col in SENTIMENT_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["ArrivalTime"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["color"] = df["label"].map(COLOR_MAP).fillna("red")
    return df


def make_plot(df: pd.DataFrame, sentiment_col: str, tsv_path: pathlib.Path) -> None:
    subset = df.dropna(subset=["DailyHigh(%)", sentiment_col])
    if subset.empty:
        print(f"  Skipping {sentiment_col} — no rows with both DailyHigh(%) and {sentiment_col}")
        return

    plt.clf()
    plt.title(f"{sentiment_col} vs DailyHigh(%) — {tsv_path.name}")
    plt.xlabel("DailyHigh (%)")
    plt.ylabel(sentiment_col)
    plt.plotsize(110, 30)
    plt.ticks_color("red")

    x_vals = subset["DailyHigh(%)"].tolist()
    x_min = int(min(x_vals) // 50) * 50
    x_max = int(max(x_vals) // 50) * 50 + 50
    plt.xticks(list(range(x_min, x_max + 1, 50)))

    for lbl, color in COLOR_MAP.items():
        grp = subset[subset["label"] == lbl]
        if grp.empty:
            continue
        plt.scatter(
            grp["DailyHigh(%)"].tolist(),
            grp[sentiment_col].tolist(),
            color=color,
            marker="dot",
        )

    plot_str = plt.build()
    plt.show()
    print()

    out_path = tsv_path.parent / f"{tsv_path.stem}_{sentiment_col}.plotext"
    out_path.write_text(plot_str, encoding="utf-8")
    print(f"  Saved → {out_path}")


def main() -> None:
    tsv_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")

    df = load_data(tsv_path)
    print(f"Loaded {len(df)} rows from {tsv_path}\n")

    for col in SENTIMENT_COLUMNS:
        if col not in df.columns:
            print(f"  Skipping {col} — column not found in file")
            continue
        print(f"=== {col} ===")
        make_plot(df, col, tsv_path)

    print("Done.")


if __name__ == "__main__":
    main()
