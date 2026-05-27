#!/usr/bin/env python3
"""
FinBERT-tone-analysis.py — Entity-targeted financial sentiment analysis (ONNX INT8)

Runs yiyanghkust/finbert-tone on NER-annotated JSON input produced by NerSecDicCreator.
For each sentence, generates one sentiment score per resolved ticker by rewriting
entity mentions as [TARGET] / [OTHER] before inference.

Two-phase usage:

    # Phase 1 — one-time export (downloads model, converts to ONNX, quantizes to INT8)
    python FinBERT-tone-analysis.py --export

    # Phase 2 — fast inference (loads pre-exported model, processes input file)
    python FinBERT-tone-analysis.py --input ./FEED_28-jan-2026_sentences_NER.json
    python FinBERT-tone-analysis.py --input ./FEED_28-jan-2026_sentences_NER.json --output ./results.json
"""

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

try:
    from transformers import AutoTokenizer
except ImportError:
    print("Error: 'transformers' is not installed.")
    print("  pip install --break-system-packages numpy transformers optimum[onnxruntime]")
    sys.exit(1)

try:
    from optimum.onnxruntime import ORTModelForSequenceClassification
except ImportError:
    print("Error: 'optimum[onnxruntime]' is not installed.")
    print("  pip install --break-system-packages numpy transformers optimum[onnxruntime]")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR        = Path(__file__).parent
MODEL_DIR         = SCRIPT_DIR / "finbert_tone_onnx"
HUGGINGFACE_MODEL = "yiyanghkust/finbert-tone"
SENTIMENT_THRESHOLD = 0.05   # |score| below this → "neutral"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# ONNXExporter — one-time download + export + INT8 quantization
# ===========================================================================
class ONNXExporter:
    """Downloads yiyanghkust/finbert-tone, exports to ONNX FP32, quantizes to INT8."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def export(self) -> bool:
        """Full export pipeline.  Returns True on success."""
        temp_dir = tempfile.mkdtemp()
        try:
            logger.info("Step 1/3 — Downloading and exporting base ONNX model…")
            base_model_path = self._download_and_export_base(Path(temp_dir))

            logger.info("Step 2/3 — Quantizing to INT8…")
            self._quantize_int8(base_model_path, self.output_dir)

            logger.info("Step 3/3 — Copying tokenizer and config files…")
            self._copy_tokenizer_files(Path(temp_dir), self.output_dir)

            logger.info(f"Export complete.  ONNX INT8 model saved to: {self.output_dir}")
            return True
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return False
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    # ------------------------------------------------------------------
    def _download_and_export_base(self, temp_dir: Path) -> Path:
        """Download from HuggingFace and export to ONNX FP32.  Returns path to model.onnx."""
        logger.info(f"  Downloading {HUGGINGFACE_MODEL} from HuggingFace…")
        model = ORTModelForSequenceClassification.from_pretrained(
            HUGGINGFACE_MODEL, export=True
        )
        model.save_pretrained(str(temp_dir))

        logger.info("  Saving tokenizer…")
        tokenizer = AutoTokenizer.from_pretrained(HUGGINGFACE_MODEL)
        tokenizer.save_pretrained(str(temp_dir))

        return temp_dir / "model.onnx"

    def _quantize_int8(self, base_model_path: Path, output_dir: Path) -> None:
        """INT8 dynamic quantisation.  Import is deferred — not needed at inference time."""
        from onnxruntime.quantization import QuantType, quantize_dynamic   # noqa: deferred

        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Quantizing {base_model_path.name} → {output_dir / 'model.onnx'}")
        quantize_dynamic(
            model_input=str(base_model_path),
            model_output=str(output_dir / "model.onnx"),
            weight_type=QuantType.QUInt8,
        )

    def _copy_tokenizer_files(self, source_dir: Path, dest_dir: Path) -> None:
        """Copy tokenizer + config so the output dir is self-contained."""
        needed = [
            "config.json",
            "tokenizer_config.json",
            "tokenizer.json",
            "vocab.txt",
            "special_tokens_map.json",
        ]
        for fname in needed:
            src = source_dir / fname
            if src.exists():
                shutil.copy2(str(src), str(dest_dir / fname))
                logger.info(f"  Copied {fname}")
            else:
                logger.warning(f"  {fname} not found in source — skipped")


# ===========================================================================
# EntityDeduplicator — filter nulls, group by ticker, greedy longest-span dedup
# ===========================================================================
class EntityDeduplicator:
    """
    Removes unresolved (ticker=null) entities and eliminates overlapping spans
    within each ticker group, keeping the longest span at each position.

    This handles a common pattern in the NER output where both "ENvue Medical Inc"
    and "ENvue Medical" are tagged at the same char_start for the same ticker.
    """

    @staticmethod
    def deduplicate(entities: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Returns {ticker: [non-overlapping entity dicts]}.
        Empty dict if no resolved entities exist.
        """
        # 1. Filter out unresolved
        resolved = [e for e in entities if e.get("ticker") is not None]

        # 2. Group by ticker
        grouped: Dict[str, List[Dict]] = defaultdict(list)
        for e in resolved:
            grouped[e["ticker"]].append(e)

        # 3. Greedy dedup per ticker
        return {ticker: EntityDeduplicator._greedy_select(spans) for ticker, spans in grouped.items()}

    @staticmethod
    def _greedy_select(spans: List[Dict]) -> List[Dict]:
        """
        Sort by span length descending, then greedily accept non-overlapping spans.
        Overlap condition: a_start < b_end AND b_start < a_end.
        """
        sorted_spans = sorted(
            spans,
            key=lambda e: (-(e["char_end"] - e["char_start"]), e["char_start"]),
        )

        accepted: List[Dict] = []
        for candidate in sorted_spans:
            c_start, c_end = candidate["char_start"], candidate["char_end"]
            if not any(c_start < a["char_end"] and a["char_start"] < c_end for a in accepted):
                accepted.append(candidate)

        return accepted


# ===========================================================================
# TextSubstitutor — produce [TARGET] / [OTHER] rewritten text
# ===========================================================================
class TextSubstitutor:
    """Rewrites entity mentions for entity-targeted FinBERT inference."""

    @staticmethod
    def substitute(text: str, deduped: Dict[str, List[Dict]], target_ticker: str) -> str:
        """
        Replace target-ticker spans with [TARGET], all other ticker spans with [OTHER].
        Replacements are applied end-to-start to keep earlier offsets valid.
        """
        replacements: List[tuple] = []
        for ticker, spans in deduped.items():
            label = "[TARGET]" if ticker == target_ticker else "[OTHER]"
            for span in spans:
                replacements.append((span["char_start"], span["char_end"], label))

        # Sort descending by start position — replace from end to start
        replacements.sort(key=lambda x: x[0], reverse=True)

        result = text
        for start, end, label in replacements:
            result = result[:start] + label + result[end:]

        return result


# ===========================================================================
# FinBERTInferencer — load ONNX model, run inference, decode logits
# ===========================================================================
class FinBERTInferencer:
    """Loads the pre-exported ONNX INT8 model and produces sentiment scores."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model:     Optional[ORTModelForSequenceClassification] = None
        self.tokenizer: Optional[AutoTokenizer] = None

    def load(self) -> None:
        """Load model and tokenizer from the exported directory."""
        logger.info(f"Loading ONNX model from {self.model_dir}…")
        # Bound thread fan-out: keeps a WebSocket process from oversubscribing
        # CPU when several articles arrive in parallel.
        try:
            from onnxruntime import SessionOptions
            sess_opts = SessionOptions()
            sess_opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)
            sess_opts.inter_op_num_threads = 1
            self.model = ORTModelForSequenceClassification.from_pretrained(
                str(self.model_dir), session_options=sess_opts
            )
        except Exception:
            self.model = ORTModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        logger.info("Model and tokenizer loaded.")

    def predict(self, text: str) -> Dict[str, float]:
        """Single-text inference. Thin wrapper around predict_batch()."""
        return self.predict_batch([text])[0]

    def predict_batch(
        self,
        texts: List[str],
        batch_size: int = 32,
    ) -> List[Dict[str, float]]:
        """
        Run inference on a list of texts in batches.

        yiyanghkust/finbert-tone logit order: [neutral, positive, negative] (indices 0, 1, 2).
        Returns one dict per input text with positive/negative/neutral probabilities
        and sentiment_score (pos − neg).
        """
        if not texts:
            return []

        results: List[Dict[str, float]] = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            inputs = self.tokenizer(
                chunk,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            )
            outputs = self.model(**inputs)

            raw = outputs.logits
            logits = np.array(raw.detach() if hasattr(raw, "detach") else raw)
            # logits shape: (batch, 3)
            probs = self._softmax_batch(logits)

            for row in probs:
                neu, pos, neg = float(row[0]), float(row[1]), float(row[2])
                results.append({
                    "positive":        round(pos, 4),
                    "negative":        round(neg, 4),
                    "neutral":         round(neu, 4),
                    "sentiment_score": round(pos - neg, 4),
                })

        return results

    @staticmethod
    def _softmax(logits: List[float]) -> List[float]:
        """Numerically stable softmax for a single logit vector."""
        x = np.array(logits)
        e = np.exp(x - np.max(x))
        return (e / e.sum()).tolist()

    @staticmethod
    def _softmax_batch(logits: np.ndarray) -> np.ndarray:
        """Vectorized row-wise softmax over a (batch, classes) array."""
        x = logits - np.max(logits, axis=1, keepdims=True)
        e = np.exp(x)
        return e / np.sum(e, axis=1, keepdims=True)


# ===========================================================================
# SentimentAggregator — collect per-sentence results, compute per-ticker stats
# ===========================================================================
class SentimentAggregator:
    """Accumulates sentence-level results and builds the per-ticker output block."""

    def __init__(self):
        self._data: Dict[str, List[Dict]] = defaultdict(list)

    def add_sentence_result(self, ticker: str, record: Dict) -> None:
        self._data[ticker].append(record)

    def build_output(self) -> Dict[str, Dict]:
        """
        Compute overall_sentiment_score (mean) and overall_label per ticker.
        Returns the full ticker_sentiments structure.
        """
        output: Dict[str, Dict] = {}
        for ticker in sorted(self._data):
            sentences = self._data[ticker]
            scores    = [s["sentiment_score"] for s in sentences]
            overall   = round(sum(scores) / len(scores), 4)
            output[ticker] = {
                "overall_sentiment_score": overall,
                "overall_label":           self._label_from_score(overall),
                "sentence_count":          len(sentences),
                "sentences":               sentences,
            }
        return output

    @staticmethod
    def _label_from_score(score: float) -> str:
        if score >  SENTIMENT_THRESHOLD:
            return "positive"
        if score < -SENTIMENT_THRESHOLD:
            return "negative"
        return "neutral"


# ===========================================================================
# FinBERTProcessor — top-level orchestrator for one input file
# ===========================================================================
class FinBERTProcessor:
    """Load input → deduplicate → substitute → infer → aggregate → save output."""

    def __init__(self, input_path: Path, output_path: Optional[Path], model_dir: Path):
        self.input_path  = input_path
        self.output_path = output_path or self._derive_output_path(input_path)
        self.model_dir   = model_dir

    # ------------------------------------------------------------------
    def run(self) -> bool:
        """Entry point.  Returns True on success."""
        if not self.model_dir.exists():
            logger.error(
                f"ONNX model directory not found: {self.model_dir}\n"
                f"Run the one-time export first:\n"
                f"  python FinBERT-tone-analysis.py --export"
            )
            return False

        start_time = time.time()

        inferencer = FinBERTInferencer(self.model_dir)
        inferencer.load()

        input_data = self._load_input()
        if input_data is None:
            return False

        sentences  = input_data.get("sentences", [])
        aggregator = SentimentAggregator()
        skipped:   List[int] = []
        processed  = 0

        logger.info(f"Processing {len(sentences)} sentences…")
        for sentence in sentences:
            sent_id = sentence["id"]
            text    = sentence.get("text", "")

            if not text.strip():
                logger.warning(f"Sentence {sent_id}: empty text — skipped")
                skipped.append(sent_id)
                continue

            deduped = EntityDeduplicator.deduplicate(sentence.get("entities", []))
            if not deduped:
                logger.info(f"Sentence {sent_id}: no resolved entities — skipped")
                skipped.append(sent_id)
                continue

            processed += 1
            for target_ticker in deduped:
                targeted_text = TextSubstitutor.substitute(text, deduped, target_ticker)
                logger.debug(f"  Sent {sent_id} / {target_ticker}: {targeted_text[:90]}…")

                scores = inferencer.predict(targeted_text)
                aggregator.add_sentence_result(target_ticker, {
                    "sentence_id":   sent_id,
                    "source":        sentence.get("source", ""),
                    "original_text": text,
                    "targeted_text": targeted_text,
                    **scores,
                })

        elapsed            = round(time.time() - start_time, 2)
        ticker_sentiments  = aggregator.build_output()
        total_inferences   = sum(b["sentence_count"] for b in ticker_sentiments.values())

        output_data = {
            "metadata": {
                "input_file":                self.input_path.name,
                "model":                     HUGGINGFACE_MODEL,
                "inference_engine":          "ONNX Runtime (INT8 quantized)",
                "total_sentences_processed": processed,
                "skipped_sentences":         skipped,
                "total_ticker_sentiments":   total_inferences,
                "unique_tickers":            sorted(ticker_sentiments.keys()),
                "processing_time_seconds":   elapsed,
            },
            "ticker_sentiments": ticker_sentiments,
        }

        self._save_output(output_data)
        logger.info(f"Done. Output written to: {self.output_path}")
        return True

    # ------------------------------------------------------------------
    def _load_input(self) -> Optional[Dict]:
        if not self.input_path.exists():
            logger.error(f"Input file not found: {self.input_path}")
            return None
        try:
            with open(self.input_path, "r") as f:
                data = json.load(f)
            if "sentences" not in data:
                logger.error("Input JSON is missing the 'sentences' key")
                return None
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse input JSON: {e}")
            return None

    def _save_output(self, output_data: Dict) -> None:
        with open(self.output_path, "w") as f:
            json.dump(output_data, f, indent=2)

    @staticmethod
    def _derive_output_path(input_path: Path) -> Path:
        """Default: {stem}_FinBERT-tone.json in the same directory as the input file."""
        return input_path.parent / (input_path.stem + "_FinBERT-tone.json")


# ===========================================================================
# main
# ===========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Entity-targeted FinBERT-tone sentiment analysis (ONNX INT8)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time model export (download → ONNX → INT8 quantization)
  python FinBERT-tone-analysis.py --export

  # Run sentiment analysis on an NER-annotated JSON file
  python FinBERT-tone-analysis.py --input ./FEED_28-jan-2026_sentences_NER.json

  # Specify a custom output path
  python FinBERT-tone-analysis.py --input ./data.json --output ./results.json
        """,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--export",
        action="store_true",
        help="One-time export: download yiyanghkust/finbert-tone → ONNX → INT8 quantize",
    )
    group.add_argument(
        "--input",
        type=str,
        help="Path to NER-annotated input JSON (output of NerSecDicCreator)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON path (default: <input_stem>_FinBERT-tone.json)",
    )

    args = parser.parse_args()

    if args.export:
        exporter = ONNXExporter(MODEL_DIR)
        sys.exit(0 if exporter.export() else 1)
    else:
        input_path  = Path(args.input)
        output_path = Path(args.output) if args.output else None
        processor   = FinBERTProcessor(input_path, output_path, MODEL_DIR)
        sys.exit(0 if processor.run() else 1)


if __name__ == "__main__":
    main()
