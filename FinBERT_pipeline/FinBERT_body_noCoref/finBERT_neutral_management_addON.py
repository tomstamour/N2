"""Post-process a `_FinBERT.json` file by re-aggregating per-sentence sentiment
with one of six neutral-sentence-management strategies described in
`neutral-sentences-management-concepts.txt`.

The script is a pure post-processor: it does not load any ML model, only re-aggregates
the per-sentence `positive`/`negative`/`neutral` scores already present in the input.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from copy import deepcopy
from pathlib import Path

SENTIMENT_THRESHOLD = 0.05  # matches FinBERT-analysis.py
ROUND_DECIMALS = 4

METHOD_SLUGS = {
    1: "neutral_filter",
    2: "confidence_weighted",
    3: "net_score",
    4: "top_k",
    5: "positional",
    6: "recommended",
}


def label_from_score(score: float) -> str:
    if score > SENTIMENT_THRESHOLD:
        return "positive"
    if score < -SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"


def _empty_result() -> tuple[float, int]:
    return 0.0, 0


def method_1_neutral_filter(sentences, neutral_threshold: float) -> tuple[float, int]:
    kept = [s for s in sentences if s["neutral"] < neutral_threshold]
    if not kept:
        return _empty_result()
    score = sum(s["positive"] - s["negative"] for s in kept) / len(kept)
    return score, len(kept)


def method_2_confidence_weighted(sentences) -> tuple[float, int]:
    if not sentences:
        return _empty_result()
    weights = [1.0 - s["neutral"] for s in sentences]
    total_w = sum(weights)
    if total_w == 0:
        return _empty_result()
    score = sum((s["positive"] - s["negative"]) * w for s, w in zip(sentences, weights)) / total_w
    return score, len(sentences)


def method_3_net_score(sentences) -> tuple[float, int]:
    if not sentences:
        return _empty_result()
    score = sum(s["positive"] - s["negative"] for s in sentences) / len(sentences)
    return score, len(sentences)


def method_4_top_k(sentences, k: int) -> tuple[float, int]:
    if not sentences:
        return _empty_result()
    # Literal interpretation of the concept-file snippet: top-k by `positive` score.
    top = sorted(sentences, key=lambda s: s["positive"], reverse=True)[:k]
    score = sum(s["positive"] - s["negative"] for s in top) / len(top)
    return score, len(top)


def method_5_positional(sentences, decay: float) -> tuple[float, int]:
    if not sentences:
        return _empty_result()
    weights = [math.exp(-decay * s["sentence_id"]) for s in sentences]
    total_w = sum(weights)
    if total_w == 0:
        return _empty_result()
    score = sum((s["positive"] - s["negative"]) * w for s, w in zip(sentences, weights)) / total_w
    return score, len(sentences)


def method_6_recommended(sentences, neutral_threshold: float) -> tuple[float, int]:
    signal = [s for s in sentences if s["neutral"] < neutral_threshold]
    if not signal:
        return _empty_result()
    weights = [1.0 - s["neutral"] for s in signal]
    total_w = sum(weights)
    if total_w == 0:
        return _empty_result()
    score = sum((s["positive"] - s["negative"]) * w for s, w in zip(signal, weights)) / total_w
    return score, len(signal)


def aggregate(method: int, sentences, *, neutral_threshold: float, top_k: int, positional_decay: float) -> tuple[float, int]:
    if method == 1:
        return method_1_neutral_filter(sentences, neutral_threshold)
    if method == 2:
        return method_2_confidence_weighted(sentences)
    if method == 3:
        return method_3_net_score(sentences)
    if method == 4:
        return method_4_top_k(sentences, top_k)
    if method == 5:
        return method_5_positional(sentences, positional_decay)
    if method == 6:
        return method_6_recommended(sentences, neutral_threshold)
    raise ValueError(f"Unknown method: {method}")


def params_for_method(method: int, neutral_threshold: float, top_k: int, positional_decay: float) -> dict:
    if method == 1:
        return {"neutral_threshold": neutral_threshold}
    if method == 2:
        return {}
    if method == 3:
        return {}
    if method == 4:
        return {"top_k": top_k}
    if method == 5:
        return {"positional_decay": positional_decay}
    if method == 6:
        return {"neutral_threshold": neutral_threshold}
    raise ValueError(f"Unknown method: {method}")


def derive_output_stem(input_path: Path) -> str:
    stem = input_path.stem
    if stem.endswith("_FinBERT"):
        stem = stem[: -len("_FinBERT")]
    return stem


def process(input_path: Path, output_dir: Path, method: int, *, neutral_threshold: float, top_k: int, positional_decay: float) -> Path:
    with input_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "ticker_sentiments" not in data:
        sys.exit(f"Error: input file {input_path} has no 'ticker_sentiments' key — not a FinBERT output JSON.")

    slug = METHOD_SLUGS[method]
    out = deepcopy(data)

    out.setdefault("metadata", {})
    out["metadata"]["neutral_management"] = {
        "method": method,
        "name": slug,
        "params": params_for_method(method, neutral_threshold, top_k, positional_decay),
    }

    for ticker, block in out["ticker_sentiments"].items():
        sentences = block.get("sentences", [])
        score, used = aggregate(
            method,
            sentences,
            neutral_threshold=neutral_threshold,
            top_k=top_k,
            positional_decay=positional_decay,
        )
        block["adjusted_sentiment_score"] = round(score, ROUND_DECIMALS)
        block["adjusted_label"] = label_from_score(score)
        block["sentence_count_used"] = used

    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{derive_output_stem(input_path)}_FinBERT_{slug}.json"
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    return output_path


_EPILOG = """\
Neutral-management methods
--------------------------
  1  neutral_filter        Drop sentences where neutral score >= --neutral-threshold,
                           then average (positive - negative) over the rest.

  2  confidence_weighted   Weight each sentence by (1 - neutral); no sentences dropped.

  3  net_score             Simple mean of (positive - negative) across all sentences.

  4  top_k                 Keep only the top-K sentences by positive score, then average
                           (positive - negative) over those K sentences (--top-k).

  5  positional            Exponential decay by sentence index; earlier sentences count
                           more (--positional-decay controls the rate).

  6  recommended           Combine methods 1 and 2: filter low-signal sentences first,
                           then apply confidence weighting to the remainder.

Examples
--------
  # Method 6 (recommended) with default params:
  python finBERT_neutral_management_addON.py \\
      --input path/to/AEHL-2026-05-08_FinBERT.json \\
      --output-dir outputs/ \\
      --management-method 6

  # Method 4, keep top 5 sentences:
  python finBERT_neutral_management_addON.py \\
      --input path/to/AEHL-2026-05-08_FinBERT.json \\
      --output-dir outputs/ \\
      --management-method 4 --top-k 5
"""


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Re-aggregate FinBERT per-sentence sentiment with a neutral-management strategy.\n"
            "Reads a *_FinBERT.json produced by FinBERT-analysis.py and writes a new JSON\n"
            "with adjusted_sentiment_score / adjusted_label fields for each ticker."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, type=Path, help="Path to a *_FinBERT.json file.")
    parser.add_argument("--output-dir", required=True, type=Path, help="Destination directory for the re-aggregated JSON.")
    parser.add_argument(
        "--management-method",
        required=True,
        type=int,
        choices=sorted(METHOD_SLUGS.keys()),
        metavar="{1,2,3,4,5,6}",
        help=(
            "Aggregation method: "
            "1=neutral_filter, 2=confidence_weighted, 3=net_score, "
            "4=top_k, 5=positional, 6=recommended. "
            "See the epilog for full descriptions."
        ),
    )
    parser.add_argument(
        "--neutral-threshold",
        type=float,
        default=0.85,
        help="Neutral-score cutoff for methods 1 and 6 (default: 0.85).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=3,
        help="Number of top sentences to keep for method 4 (default: 3).",
    )
    parser.add_argument(
        "--positional-decay",
        type=float,
        default=0.1,
        help="Exponential decay rate for method 5 (default: 0.1).",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        sys.exit(f"Error: input file does not exist: {args.input}")

    out_path = process(
        args.input,
        args.output_dir,
        args.management_method,
        neutral_threshold=args.neutral_threshold,
        top_k=args.top_k,
        positional_decay=args.positional_decay,
    )
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
