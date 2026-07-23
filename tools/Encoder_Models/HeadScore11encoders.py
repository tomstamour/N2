#!/usr/bin/env python
"""
HeadScore11encoders.py — batch-score a PR-stats TSV with all 11 headliners.
(Formerly Score11encoders.py; the Body-column twin is BodyScore11encoders.py.)

Reads a TSV with a `Headline` column, scores every headline with each of the
11 encoder scripts in this directory (one model loaded at a time), and writes
the same table with the model outputs appended as new columns. Output file is
the input filename with `.tsv` replaced by `_11EncScored.tsv`.

Usage:
    /home/tom/venv/bin/python HeadScore11encoders.py \
        --input ../../price_stats_loader/outputs/01_PR-stats-...tsv
    # quick test on a subset:
    /home/tom/venv/bin/python HeadScore11encoders.py --input FILE \
        --limit 5 --models FinMarBa,LouMcD-SDict

Appended columns, in model order below (70 total for all 11 models):
  transformers: {M}_negative {M}_neutral {M}_positive {M}_sentiment_score
                {M}_label {M}_elapsed_ms   (FinMarBa's middle class is
                `indecisive`, kept under its real name)
  LouMcD-SDict: the 7 category counts + sentiment_score + label + elapsed_ms
"""

import argparse
import csv
import gc
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Same registry as verify_headliners.py: script filename + scoring-fn name.
# `keys` = result-dict keys written out, in column order, prefixed "{name}_".
_STD_KEYS = ["negative", "neutral", "positive", "sentiment_score", "label", "elapsed_ms"]
MODELS = {
    "FinMarBa":      {"script": "FinMarBa_headliner.py",      "fn": "score_headline",
                      "keys": ["negative", "indecisive", "positive",
                               "sentiment_score", "label", "elapsed_ms"]},
    "FLANG-BERT":    {"script": "FLANG-BERT_headliner.py",    "fn": "analyze_headline", "keys": _STD_KEYS},
    "FLANG-ELECTRA": {"script": "FLANG-ELECTRA_headliner.py", "fn": "analyze_headline", "keys": _STD_KEYS},
    "RoBERTa":       {"script": "RoBERTa_headliner.py",       "fn": "score_headline",   "keys": _STD_KEYS},
    "ModernBERT":    {"script": "ModernBERT_headliner.py",    "fn": "analyze_headline", "keys": _STD_KEYS},
    "DeBERTa":       {"script": "DeBERTa_headliner.py",       "fn": "analyze_headline", "keys": _STD_KEYS},
    "finbert-tone":  {"script": "finbert-tone_headliner.py",  "fn": "score_headline",   "keys": _STD_KEYS},
    "FinancialBERT": {"script": "FinancialBERT_headliner.py", "fn": "score_headline",   "keys": _STD_KEYS},
    "distilroberta": {"script": "distilroberta_headliner.py", "fn": "score_headline",   "keys": _STD_KEYS},
    "Trading-Hero":  {"script": "Trading-Hero_headliner.py",  "fn": "score_headline",   "keys": _STD_KEYS},
    "LouMcD-SDict":  {"script": "LouMcD-SDict_headliner.py",  "fn": "score_headline",
                      "keys": ["negative", "positive", "uncertainty", "litigious",
                               "strong_modal", "weak_modal", "constraining",
                               "sentiment_score", "label", "elapsed_ms"]},
}

PROGRESS_EVERY = 250


def _load_module(script_name: str):
    """Import a headliner by file path (several filenames contain hyphens)."""
    mod_name = "headliner_" + script_name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(mod_name, HERE / script_name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _fmt(value) -> str:
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") if value % 1 else f"{value:g}"
    return str(value)


def score_table(rows, headline_idx, model_names):
    """Append one column block per model to every row, one model at a time."""
    n = len(rows)
    for name in model_names:
        cfg = MODELS[name]
        keys = cfg["keys"]
        print(f"[{name}] loading …", flush=True)
        module = _load_module(cfg["script"])
        module.load_model()
        score_fn = getattr(module, cfg["fn"])
        score_fn("warm up")  # cold-start pass so row timings are warm
        t0 = time.perf_counter()
        for i, row in enumerate(rows, 1):
            headline = row[headline_idx].strip() if headline_idx < len(row) else ""
            if headline:
                result = score_fn(headline)
                # zero-shot/FLANG scripts name their timing key inference_ms
                result.setdefault("elapsed_ms", result.get("inference_ms"))
                row.extend(_fmt(result[k]) for k in keys)
            else:
                row.extend([""] * len(keys))
            if i % PROGRESS_EVERY == 0 or i == n:
                rate = (time.perf_counter() - t0) / i * 1000
                print(f"[{name}] {i}/{n}  ({rate:.1f} ms/row)", flush=True)
        # Release the ONNX session before loading the next model.
        mod_name = "headliner_" + cfg["script"].replace("-", "_").removesuffix(".py")
        sys.modules.pop(mod_name, None)
        del module, score_fn
        gc.collect()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Score every Headline of a TSV with the 11 encoder headliners.")
    parser.add_argument("--input", required=True, help="input .tsv path")
    parser.add_argument("--models", metavar="NAME[,NAME...]",
                        help=f"subset of models (default: all). Choices: {', '.join(MODELS)}")
    parser.add_argument("--limit", type=int, metavar="N",
                        help="score only the first N rows (quick test)")
    parser.add_argument("--headline-col", default="Headline",
                        help="header name of the headline column (default: Headline)")
    args = parser.parse_args()

    in_path = Path(args.input).expanduser().resolve()
    if not in_path.is_file():
        parser.error(f"input not found: {in_path}")
    if not in_path.name.endswith(".tsv"):
        parser.error("input must be a .tsv file")
    out_path = in_path.with_name(in_path.name[:-len(".tsv")] + "_11EncScored.tsv")
    if out_path == in_path:
        parser.error("output would overwrite the input")

    if args.models:
        model_names = [m.strip() for m in args.models.split(",") if m.strip()]
        unknown = [m for m in model_names if m not in MODELS]
        if unknown:
            parser.error(f"unknown model(s): {', '.join(unknown)}. Choices: {', '.join(MODELS)}")
    else:
        model_names = list(MODELS)

    with open(in_path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        try:
            header = next(reader)
        except StopIteration:
            parser.error("input file is empty")
        rows = list(reader)
    if args.headline_col not in header:
        parser.error(f"column '{args.headline_col}' not found in {in_path.name}")
    headline_idx = header.index(args.headline_col)
    if args.limit is not None:
        rows = rows[:args.limit]

    print(f"Input : {in_path.name}  ({len(rows)} rows, {len(header)} cols)")
    print(f"Models: {', '.join(model_names)}")

    for name in model_names:
        header.extend(f"{name}_{k}" for k in MODELS[name]["keys"])

    t0 = time.perf_counter()
    score_table(rows, headline_idx, model_names)
    print(f"Scoring done in {time.perf_counter() - t0:.1f} s")

    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"Wrote {out_path}  ({len(rows)} rows, {len(header)} cols)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
