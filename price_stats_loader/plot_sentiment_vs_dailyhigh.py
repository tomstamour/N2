#!/usr/bin/env python3
import sys
import argparse
import pathlib
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

try:
    from adjustText import adjust_text
    HAS_ADJUST_TEXT = True
except ImportError:
    HAS_ADJUST_TEXT = False

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
    "positive": "#2ecc71",
    "neutral":  "#95a5a6",
    "negative": "#e74c3c",
}


def load_data(tsv_path: pathlib.Path) -> pd.DataFrame:
    df = pd.read_csv(tsv_path, sep="\t", dtype=str)
    df["DailyHigh(%)"] = pd.to_numeric(df["DailyHigh(%)"], errors="coerce")
    for col in SENTIMENT_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["date"] = pd.to_datetime(df["ArrivalTime"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["point_label"] = df["Symbol"] + "\n" + df["date"].fillna("")
    df["color"] = df["label"].map(COLOR_MAP).fillna("#bdc3c7")
    return df


def make_plot(df: pd.DataFrame, sentiment_col: str, tsv_path: pathlib.Path, output_dir: pathlib.Path, show_labels: bool = True) -> None:
    subset = df.dropna(subset=["DailyHigh(%)", sentiment_col])
    if subset.empty:
        print(f"  Skipping {sentiment_col} — no rows with both DailyHigh(%) and {sentiment_col}")
        return

    fig, ax = plt.subplots(figsize=(14, 8))

    for lbl, color in COLOR_MAP.items():
        grp = subset[subset["label"] == lbl]
        ax.scatter(
            grp["DailyHigh(%)"],
            grp[sentiment_col],
            facecolors="none",
            edgecolors=color,
            label=lbl,
            linewidths=1.5,
            s=80,
            zorder=3,
        )

    ax.axhline(0, color="black", linewidth=0.6, linestyle="--", alpha=0.5)
    ax.axvline(0, color="black", linewidth=0.6, linestyle="--", alpha=0.5)

    texts = []
    if show_labels:
        for _, row in subset.iterrows():
            t = ax.annotate(
                row["point_label"],
                xy=(row["DailyHigh(%)"], row[sentiment_col]),
                fontsize=6,
                color="#2c3e50",
                xytext=(4, 4),
                textcoords="offset points",
            )
            texts.append(t)

    if HAS_ADJUST_TEXT and texts:
        adjust_text(texts, ax=ax, arrowprops=dict(arrowstyle="-", color="gray", lw=0.4))

    legend_patches = [
        mpatches.Patch(color=c, label=lbl) for lbl, c in COLOR_MAP.items()
    ]
    ax.legend(handles=legend_patches, title="Sentiment label", loc="upper left")

    ax.set_xlabel("DailyHigh (%)", fontsize=11)
    ax.set_ylabel(sentiment_col, fontsize=11)
    ax.set_title(f"{sentiment_col} vs Daily High (%) — {tsv_path.name}", fontsize=13)
    ax.grid(True, linestyle="--", alpha=0.3)

    fig.tight_layout()

    out_path = output_dir / f"{tsv_path.stem}_{sentiment_col}.png"
    fig.savefig(out_path, dpi=150)
    print(f"  Saved → {out_path}")
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Plot sentiment scores vs daily high percentage")
    parser.add_argument("--input", type=str, default=str(DEFAULT_INPUT),
                        help="Path to input TSV file (default: outputs/concatenated_enriched_FinBERT.tsv)")
    parser.add_argument("--output", type=str, help="Output directory for PNG files (default: same as input directory)")
    parser.add_argument("--labels", choices=["true", "false"], default="true",
                        help="Display ticker-date labels on plots (default: true)")
    args = parser.parse_args()

    show_labels = args.labels.lower() == "true"
    tsv_path = pathlib.Path(args.input)
    if not tsv_path.exists():
        sys.exit(f"File not found: {tsv_path}")

    output_dir = pathlib.Path(args.output) if args.output else tsv_path.parent
    output_dir.mkdir(parents=True, exist_ok=True)

    df = load_data(tsv_path)
    print(f"Loaded {len(df)} rows from {tsv_path}")
    if not HAS_ADJUST_TEXT:
        print("Tip: pip install adjustText for non-overlapping labels")

    for col in SENTIMENT_COLUMNS:
        if col not in df.columns:
            print(f"  Skipping {col} — column not found in file")
            continue
        print(f"Plotting {col}...")
        make_plot(df, col, tsv_path, output_dir, show_labels)

    print("Done.")


if __name__ == "__main__":
    main()
