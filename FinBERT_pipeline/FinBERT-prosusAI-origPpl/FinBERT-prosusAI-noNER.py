#!/usr/bin/env python3
"""
FinBERT-prosusAI-noNER.py — Article-level FinBERT sentiment, no NER.

Runs ProsusAI/finbert (ONNX INT8) on every sentence of the article_body
without entity recognition or ticker-targeted substitution.

Steps:
    1. Load input JSON
    2. Clean article_body (HTML/entity/URL scrub via jsonCleaner)
    3. Split into sentences (spaCy en_core_web_sm)
    4. Score all sentences in a single batch (FinBERT ONNX INT8)
    5. Compute article-level aggregate
    6. Write output JSON

Usage:
    python FinBERT-prosusAI-noNER.py --input BWEN-2026-05-12.json
    python FinBERT-prosusAI-noNER.py --input BWEN-2026-05-12.json --output /tmp/result.json
    python FinBERT-prosusAI-noNER.py --input BWEN-2026-05-12.json --finbert-model-dir /path/to/finbert_onnx
"""

import argparse
import importlib.util
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Sibling-script path wiring
# ---------------------------------------------------------------------------
_THIS_DIR        = Path(__file__).resolve().parent
_SCRIPTS_ROOT    = _THIS_DIR.parent.parent          # N2/scripts/
_JSONCLEANER_DIR = _SCRIPTS_ROOT / "jsonCleaner"
_FINBERT_DIR     = _SCRIPTS_ROOT / "FinBERT"

for _p in (_JSONCLEANER_DIR, _FINBERT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from jsonCleaner import TextCleaner  # noqa: E402

# ---------------------------------------------------------------------------
# Deferred FinBERT import (heavy ML deps; hyphen in filename needs importlib)
# ---------------------------------------------------------------------------
_finbert_mod      = None
FinBERTInferencer = None
SENTIMENT_THRESHOLD = 0.05  # fallback until module loaded


def _load_finbert_module():
    global _finbert_mod, FinBERTInferencer, SENTIMENT_THRESHOLD
    if _finbert_mod is not None:
        return
    spec = importlib.util.spec_from_file_location(
        "finbert_analysis", _FINBERT_DIR / "FinBERT-analysis.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _finbert_mod        = mod
    FinBERTInferencer   = mod.FinBERTInferencer
    SENTIMENT_THRESHOLD = mod.SENTIMENT_THRESHOLD


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("FinBERT-prosusAI-noNER")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sentiment_label(score: float) -> str:
    if score > SENTIMENT_THRESHOLD:
        return "positive"
    if score < -SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"


def _aggregate(scores):
    """Mean of sentiment_score values; returns aggregate dict."""
    n = len(scores)
    avg_pos  = round(sum(s["positive"]  for s in scores) / n, 4)
    avg_neg  = round(sum(s["negative"]  for s in scores) / n, 4)
    avg_neu  = round(sum(s["neutral"]   for s in scores) / n, 4)
    avg_score = round(sum(s["sentiment_score"] for s in scores) / n, 4)
    return {
        "positive":        avg_pos,
        "negative":        avg_neg,
        "neutral":         avg_neu,
        "sentiment_score": avg_score,
        "sentiment_label": _sentiment_label(avg_score),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="FinBERT sentiment — all sentences, no NER")
    parser.add_argument("--input",  required=True,  help="Path to input news JSON")
    parser.add_argument("--output", default=None,   help="Path for output JSON (default: ./finBERT_outputs/<stem>_FinBERT_noNER.json)")
    parser.add_argument("--finbert-model-dir", default=None, help="Override ONNX model directory")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error(f"Input file not found: {input_path}")
        sys.exit(1)

    model_dir = Path(args.finbert_model_dir) if args.finbert_model_dir else _FINBERT_DIR / "finbert_onnx"

    # Resolve output path
    default_filename = f"{input_path.stem}_FinBERT_noNER.json"
    if args.output:
        p = Path(args.output)
        if p.is_dir() or args.output.endswith("/"):
            p.mkdir(parents=True, exist_ok=True)
            output_path = p / default_filename
        else:
            output_path = p
    else:
        out_dir = _THIS_DIR / "finBERT_outputs"
        out_dir.mkdir(exist_ok=True)
        output_path = out_dir / default_filename

    # ------------------------------------------------------------------
    # Step 1 — Load JSON
    # ------------------------------------------------------------------
    logger.info(f"Loading {input_path.name}")
    with open(input_path, encoding="utf-8") as f:
        article = json.load(f)

    raw_body = article.get("article_body", "")
    if not raw_body:
        logger.error("article_body is empty — nothing to score")
        sys.exit(1)

    ticker  = article.get("ticker", "")
    title   = article.get("title", "")
    created = article.get("created", "")

    # ------------------------------------------------------------------
    # Step 2 — Clean text
    # ------------------------------------------------------------------
    logger.info("Cleaning article_body…")
    cleaned = TextCleaner.clean(raw_body)

    # ------------------------------------------------------------------
    # Step 3 — Sentence splitting
    # ------------------------------------------------------------------
    logger.info("Splitting into sentences (spaCy en_core_web_sm)…")
    import spacy
    nlp = spacy.load("en_core_web_sm")
    doc = nlp(cleaned)
    sentences = [
        {"id": i, "text": sent.text.strip()}
        for i, sent in enumerate(doc.sents)
        if sent.text.strip()
    ]
    logger.info(f"  {len(sentences)} sentences extracted")

    # ------------------------------------------------------------------
    # Step 4 — Load FinBERT + batch inference
    # ------------------------------------------------------------------
    _load_finbert_module()
    logger.info(f"Loading FinBERT ONNX model from {model_dir}…")
    inferencer = FinBERTInferencer(model_dir)
    inferencer.load()

    logger.info("Running batch inference…")
    texts  = [s["text"] for s in sentences]
    scores = inferencer.predict_batch(texts)

    # ------------------------------------------------------------------
    # Step 5 — Attach label, build scored sentence list
    # ------------------------------------------------------------------
    scored_sentences = []
    for sent, score in zip(sentences, scores):
        scored_sentences.append({
            "id":              sent["id"],
            "text":            sent["text"],
            "positive":        score["positive"],
            "negative":        score["negative"],
            "neutral":         score["neutral"],
            "sentiment_score": score["sentiment_score"],
            "sentiment_label": _sentiment_label(score["sentiment_score"]),
        })

    # ------------------------------------------------------------------
    # Step 6 — Article-level aggregate
    # ------------------------------------------------------------------
    agg = _aggregate(scores)

    # ------------------------------------------------------------------
    # Step 7 — Write output
    # ------------------------------------------------------------------
    output = {
        "metadata": {
            "input_file":       input_path.name,
            "ticker":           ticker,
            "title":            title,
            "created":          created,
            "model":            "ProsusAI/finbert",
            "inference_engine": "ONNX Runtime (INT8 quantized)",
            "total_sentences":  len(scored_sentences),
        },
        "aggregate": agg,
        "sentences": scored_sentences,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"Output written to {output_path}")
    logger.info(
        f"Aggregate: {agg['sentiment_label']} "
        f"(score={agg['sentiment_score']}, "
        f"pos={agg['positive']}, neg={agg['negative']}, neu={agg['neutral']})"
    )


if __name__ == "__main__":
    main()
