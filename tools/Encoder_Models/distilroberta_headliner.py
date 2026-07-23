#!/usr/bin/env python3
"""
distilroberta_headliner.py — Headline sentiment via DistilRoBERTa-financial (ONNX INT8)

Runs mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis
(distilroberta-base fine-tuned on Financial PhraseBank, 3 classes)
on a single PR headline string.

Two-phase usage (CLI):

    # Phase 1 — one-time export (downloads model, converts to ONNX, quantizes to INT8)
    python distilroberta_headliner.py --export

    # Phase 2 — inference on a headline string
    python distilroberta_headliner.py --headline "Apple reports record quarterly earnings"

    # Inference on a text file (first non-empty line is the headline)
    python distilroberta_headliner.py --file ./headline-example.txt

Library usage:

    from distilroberta_headliner import score_headline

    result = score_headline("Apple reports record quarterly earnings")
    # {"headline": "...", "positive": 0.93, "neutral": 0.05,
    #  "negative": 0.02, "sentiment_score": 0.91, "label": "positive",
    #  "elapsed_ms": 8.1}
"""

import argparse
import json
import logging
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional, Union

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
MODEL_DIR         = SCRIPT_DIR / "distilroberta_financial_onnx"
HUGGINGFACE_MODEL = "mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis"

# Class meaning per logit index, from the checkpoint's config.json id2label
# (0=negative, 1=neutral, 2=positive), probe-verified 2026-07-18 ("shares
# plunge … bankruptcy" → negative 0.997, "record profit, raises guidance" →
# positive 1.000). INT8 verified faithful to FP32 on 9 probes (identical
# argmax, max prob drift 0.012). Warm latency ~6.5 ms INT8 (~17 ms FP32).
# Caveat: neutral-heavy on dryly worded bad news (delisting notice without
# market-move wording → neutral), same as FinancialBERT.
LABEL_ORDER = ["negative", "neutral", "positive"]

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
    """Downloads the DistilRoBERTa financial checkpoint, exports to ONNX FP32, quantizes to INT8."""

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

    def _download_and_export_base(self, temp_dir: Path) -> Path:
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
        from onnxruntime.quantization import QuantType, quantize_dynamic   # noqa: deferred

        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"  Quantizing {base_model_path.name} → {output_dir / 'model.onnx'}")
        quantize_dynamic(
            model_input=str(base_model_path),
            model_output=str(output_dir / "model.onnx"),
            weight_type=QuantType.QUInt8,
        )

    def _copy_tokenizer_files(self, source_dir: Path, dest_dir: Path) -> None:
        needed = [
            "config.json",
            "tokenizer_config.json",
            "tokenizer.json",
            "vocab.json",
            "merges.txt",
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
# DistilRoBERTaInferencer — load ONNX model, run inference, decode logits
# ===========================================================================
class DistilRoBERTaInferencer:
    """Loads the pre-exported ONNX INT8 model and produces sentiment scores."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model:     Optional[ORTModelForSequenceClassification] = None
        self.tokenizer: Optional[AutoTokenizer] = None

    def load(self) -> None:
        """Load model and tokenizer from the exported directory."""
        logger.info(f"Loading ONNX model from {self.model_dir}…")
        self.model     = ORTModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        logger.info("Model and tokenizer loaded.")

    def predict(self, text: str) -> Dict[str, float]:
        """
        Run inference on one headline string.

        Logit index order per LABEL_ORDER: [negative, neutral, positive].
        Returns class probabilities, sentiment_score (pos − neg) and the
        tokenize+forward wall time in ms.
        """
        t0 = time.perf_counter()
        inputs  = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        outputs = self.model(**inputs)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        raw = outputs.logits[0]
        logits = np.array(raw.detach() if hasattr(raw, "detach") else raw).flatten().tolist()

        probs = self._softmax(logits)
        scores = {label: round(p, 4) for label, p in zip(LABEL_ORDER, probs)}
        scores["sentiment_score"] = round(scores["positive"] - scores["negative"], 4)
        scores["elapsed_ms"] = round(elapsed_ms, 1)
        return scores

    @staticmethod
    def _softmax(logits: List[float]) -> List[float]:
        x = np.array(logits)
        e = np.exp(x - np.max(x))
        return (e / e.sum()).tolist()


# ===========================================================================
# Module-level singleton — shared across library calls
# ===========================================================================
_inferencer: Optional[DistilRoBERTaInferencer] = None
_model_dir:  Optional[Path] = None


def load_model(model_dir: Path = MODEL_DIR) -> None:
    """
    Load the DistilRoBERTa ONNX model into the module singleton.

    Idempotent — subsequent calls with the same model_dir are no-ops.
    Call this explicitly to pre-warm the model before the first inference call,
    or let score_headline() load it lazily on demand.
    """
    global _inferencer, _model_dir

    if _inferencer is not None and _model_dir == model_dir:
        return  # already loaded

    if not model_dir.exists():
        raise FileNotFoundError(
            f"ONNX model directory not found: {model_dir}\n"
            f"Run the one-time export first:\n"
            f"  python distilroberta_headliner.py --export"
        )

    _inferencer = DistilRoBERTaInferencer(model_dir)
    _inferencer.load()
    _model_dir = model_dir


# ===========================================================================
# Public library API
# ===========================================================================

def score_headline(headline: str, model_dir: Path = MODEL_DIR) -> Dict:
    """
    Run DistilRoBERTa-financial sentiment analysis on a single headline string.

    Args:
        headline:  The headline text to analyse.
        model_dir: Path to the exported ONNX INT8 model directory.
                   Defaults to ./distilroberta_financial_onnx (same dir as this script).

    Returns:
        {
            "headline":        str,
            "positive":        float,
            "neutral":         float,
            "negative":        float,
            "sentiment_score": float,   # positive - negative
            "label":           str,     # argmax of the three classes
            "elapsed_ms":      float,   # tokenize + forward wall time
        }
    """
    load_model(model_dir)

    scores = _inferencer.predict(headline)
    label = max(LABEL_ORDER, key=lambda k: scores[k])
    return {
        "headline":        headline,
        "positive":        scores["positive"],
        "neutral":         scores["neutral"],
        "negative":        scores["negative"],
        "sentiment_score": scores["sentiment_score"],
        "label":           label,
        "elapsed_ms":      scores["elapsed_ms"],
    }


def score_headline_file(path: Union[str, Path], model_dir: Path = MODEL_DIR) -> Dict:
    """
    Read a text file and score its first non-empty line as the headline.

    Raises:
        FileNotFoundError: if the file does not exist.
        ValueError:        if the file has no non-empty line.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Headline file not found: {path}")

    headline = ""
    with open(path, "r") as f:
        for line in f:
            if line.strip():
                headline = line.strip()
                break
    if not headline:
        raise ValueError(f"No non-empty line found in {path.name}")

    result = score_headline(headline, model_dir)
    result["source_file"] = str(path)
    return result


# ===========================================================================
# CLI
# ===========================================================================
def _cli_run(args) -> bool:
    """Execute CLI logic.  Returns True on success."""
    if args.export:
        exporter = ONNXExporter(MODEL_DIR)
        return exporter.export()

    try:
        if args.headline:
            result = score_headline(args.headline)
        else:
            result = score_headline_file(args.file)
    except (FileNotFoundError, ValueError) as e:
        logger.error(str(e))
        return False

    print(json.dumps(result, indent=2))
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Headline sentiment via DistilRoBERTa-financial (ONNX INT8)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time model export
  python distilroberta_headliner.py --export

  # Score a headline string directly
  python distilroberta_headliner.py --headline "Apple reports record quarterly earnings"

  # Score the first non-empty line of a text file
  python distilroberta_headliner.py --file ./headline-example.txt
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--export",
        action="store_true",
        help="One-time export: download mrm8488/distilroberta-finetuned-financial-news-sentiment-analysis → ONNX → INT8 quantize",
    )
    mode.add_argument(
        "--headline",
        type=str,
        help="Headline text to score directly",
    )
    mode.add_argument(
        "--file",
        type=str,
        help="Path to a text file; its first non-empty line is scored as the headline",
    )

    args = parser.parse_args()
    sys.exit(0 if _cli_run(args) else 1)


if __name__ == "__main__":
    main()
