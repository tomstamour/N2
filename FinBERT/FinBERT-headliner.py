#!/usr/bin/env python3
"""
FinBERT-headliner.py — Headline-only financial sentiment analysis (ONNX INT8)

Runs ProsusAI/finbert on the "headline" field of raw news JSON files.
No NER annotation required — direct headline text → sentiment scores.

Two-phase usage (CLI):

    # Phase 1 — one-time export (downloads model, converts to ONNX, quantizes to INT8)
    python FinBERT-headliner.py --export

    # Phase 2 — inference on a raw news JSON file
    python FinBERT-headliner.py --input ./FEED_28-jan-2026_1.json
    python FinBERT-headliner.py --input ./FEED_28-jan-2026_1.json --output ./result.json

    # Inference on a raw headline string
    python FinBERT-headliner.py --headline "Apple reports record quarterly earnings"

Library usage:

    from FinBERT_headliner import analyze_headline, analyze_news_file

    scores = analyze_headline("Apple reports record quarterly earnings")
    # {"headline": "...", "positive": 0.82, "negative": 0.03,
    #  "neutral": 0.15, "sentiment_score": 0.79, "label": "positive"}

    result = analyze_news_file("./FEED_28-jan-2026_1.json")
"""

import argparse
import json
import logging
import shutil
import sys
import tempfile
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
MODEL_DIR         = SCRIPT_DIR / "finbert_onnx"
HUGGINGFACE_MODEL = "ProsusAI/finbert"
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
    """Downloads ProsusAI/finbert, exports to ONNX FP32, quantizes to INT8."""

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
        self.model     = ORTModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        logger.info("Model and tokenizer loaded.")

    def predict(self, text: str) -> Dict[str, float]:
        """
        Run inference on one text string.

        ProsusAI/finbert logit order: [positive, negative, neutral]  (indices 0, 1, 2)
        Returns positive/negative/neutral probabilities and sentiment_score (pos − neg).
        """
        inputs  = self.tokenizer(text, return_tensors="pt", truncation=True, max_length=512)
        outputs = self.model(**inputs)

        raw = outputs.logits[0]
        logits = np.array(raw.detach() if hasattr(raw, "detach") else raw).flatten().tolist()

        pos, neg, neu = self._softmax(logits)
        return {
            "positive":        round(pos, 4),
            "negative":        round(neg, 4),
            "neutral":         round(neu, 4),
            "sentiment_score": round(pos - neg, 4),
        }

    @staticmethod
    def _softmax(logits: List[float]) -> List[float]:
        x = np.array(logits)
        e = np.exp(x - np.max(x))
        return (e / e.sum()).tolist()


# ===========================================================================
# Module-level singleton — shared across library calls
# ===========================================================================
_inferencer: Optional[FinBERTInferencer] = None
_model_dir:  Optional[Path] = None


def load_model(model_dir: Path = MODEL_DIR) -> None:
    """
    Load the FinBERT ONNX model into the module singleton.

    Idempotent — subsequent calls with the same model_dir are no-ops.
    Call this explicitly to pre-warm the model before the first inference call,
    or let analyze_headline() load it lazily on demand.
    """
    global _inferencer, _model_dir

    if _inferencer is not None and _model_dir == model_dir:
        return  # already loaded

    if not model_dir.exists():
        raise FileNotFoundError(
            f"ONNX model directory not found: {model_dir}\n"
            f"Run the one-time export first:\n"
            f"  python FinBERT-headliner.py --export"
        )

    _inferencer = FinBERTInferencer(model_dir)
    _inferencer.load()
    _model_dir = model_dir


# ===========================================================================
# Public library API
# ===========================================================================

def _label_from_score(score: float) -> str:
    if score >  SENTIMENT_THRESHOLD:
        return "positive"
    if score < -SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"


def analyze_headline(headline: str, model_dir: Path = MODEL_DIR) -> Dict:
    """
    Run FinBERT sentiment analysis on a single headline string.

    Args:
        headline:  The headline text to analyse.
        model_dir: Path to the exported ONNX INT8 model directory.
                   Defaults to ./finbert_onnx (same dir as this script).

    Returns:
        {
            "headline":        str,
            "positive":        float,
            "negative":        float,
            "neutral":         float,
            "sentiment_score": float,   # positive - negative
            "label":           str,     # "positive" | "negative" | "neutral"
        }
    """
    load_model(model_dir)

    scores = _inferencer.predict(headline)
    return {
        "headline": headline,
        **scores,
        "label": _label_from_score(scores["sentiment_score"]),
    }


def analyze_news_file(path: Union[str, Path], model_dir: Path = MODEL_DIR) -> Dict:
    """
    Load a raw news JSON file and analyse its "headline" field.

    Args:
        path:      Path to a raw news JSON file (must contain a "headline" key).
        model_dir: Path to the exported ONNX INT8 model directory.

    Returns:
        Same dict as analyze_headline(), plus the source file path.

    Raises:
        FileNotFoundError: if the file does not exist.
        KeyError:          if the JSON has no "headline" field.
        json.JSONDecodeError: if the file is not valid JSON.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"News file not found: {path}")

    with open(path, "r") as f:
        data = json.load(f)

    if "headline" not in data:
        raise KeyError(f"'headline' field not found in {path.name}")

    headline = data["headline"]
    if not headline or not headline.strip():
        raise ValueError(f"'headline' field is empty in {path.name}")

    result = analyze_headline(headline, model_dir)
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

    if args.headline:
        try:
            result = analyze_headline(args.headline)
        except FileNotFoundError as e:
            logger.error(str(e))
            return False
        _print_and_maybe_save(result, args.output)
        return True

    # --input path
    try:
        result = analyze_news_file(args.input)
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError) as e:
        logger.error(str(e))
        return False

    output_path = (
        Path(args.output) if args.output
        else Path(args.input).parent / (Path(args.input).stem + "_headline_sentiment.json")
    )
    _print_and_maybe_save(result, str(output_path))
    return True


def _print_and_maybe_save(result: Dict, output_path: Optional[str]) -> None:
    print(json.dumps(result, indent=2))
    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        logger.info(f"Result written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Headline-only FinBERT sentiment analysis (ONNX INT8)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time model export (shared with FinBERT-analysis.py)
  python FinBERT-headliner.py --export

  # Analyse the headline from a raw news JSON file
  python FinBERT-headliner.py --input ./FEED_28-jan-2026_1.json
  python FinBERT-headliner.py --input ./FEED_28-jan-2026_1.json --output ./result.json

  # Analyse a headline string directly
  python FinBERT-headliner.py --headline "Apple reports record quarterly earnings"
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--export",
        action="store_true",
        help="One-time export: download ProsusAI/finbert → ONNX → INT8 quantize",
    )
    mode.add_argument(
        "--input",
        type=str,
        help="Path to raw news JSON file (must contain a 'headline' field)",
    )
    mode.add_argument(
        "--headline",
        type=str,
        help="Headline text to analyse directly (no file required)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON path (default: <input_stem>_headline_sentiment.json)",
    )

    args = parser.parse_args()
    sys.exit(0 if _cli_run(args) else 1)


if __name__ == "__main__":
    main()
