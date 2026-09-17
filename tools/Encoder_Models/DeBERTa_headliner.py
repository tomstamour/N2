#!/usr/bin/env python3
"""
DeBERTa_headliner.py — Zero-shot headline classification (ONNX FP32)

Runs MoritzLaurer/deberta-v3-base-zeroshot-v2.0 (NLI) on a single PR headline
and scores it against candidate labels (default: positive / negative / neutral)
via the standard zero-shot entailment recipe. All candidate hypotheses are
scored in ONE batched forward pass. Same recipe and output shape as
ModernBERT_headliner.py, so the two models are directly comparable.

Why FP32 and not INT8 (benchmarked 2026-07-18 on the i7-1185G7):
  - INT8 dynamic quantization CORRUPTS DeBERTa-v3's judgment: probabilities
    collapse to near-uniform (~0.28–0.38) and labels flip vs FP32 on 2 of 4
    test headlines (same failure mode seen with ModernBERT-large INT8)
  - FP32 (intra_op=3): sharp, correct labels (FDA approval 0.999 pos,
    delisting 0.9996 neg) at ~105 ms median / ~116 ms p95 warm — under the
    150 ms target

The checkpoint is 2-class (entailment / not_entailment); the entailment logit
index is auto-detected from the model config.

Two-phase usage (CLI):

    # Phase 1 — one-time export (downloads model, converts to ONNX FP32)
    /home/tom/venv/bin/python DeBERTa_headliner.py --export

    # Phase 2 — inference on a plain-text file (first non-empty line = headline)
    /home/tom/venv/bin/python DeBERTa_headliner.py --input ./headline-example.txt

    # Inference on a raw headline string
    /home/tom/venv/bin/python DeBERTa_headliner.py --headline "Apple reports record quarterly earnings"

    # Custom candidate labels / latency benchmark
    /home/tom/venv/bin/python DeBERTa_headliner.py --headline "..." --labels bullish,bearish
    /home/tom/venv/bin/python DeBERTa_headliner.py --headline "..." --benchmark 20

Library usage:

    from DeBERTa_headliner import analyze_headline, load_model

    load_model()                      # optional pre-warm
    result = analyze_headline("Apple reports record quarterly earnings")
    # {"headline": "...", "pos": 0.82, "neg": 0.03, "neu": 0.15,
    #  "ss": 0.79, "lab": "positive", "ms": 92.4}
"""

import argparse
import json
import logging
import sys
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
MODEL_DIR         = SCRIPT_DIR / "deberta_zeroshot_onnx"
HUGGINGFACE_MODEL = "MoritzLaurer/deberta-v3-base-zeroshot-v2.0"

DEFAULT_LABELS      = ["positive", "negative", "neutral"]
HYPOTHESIS_TEMPLATE = "This example is {}."
MAX_LENGTH          = 256
INTRA_OP_THREADS    = 3   # FP32 sweet spot on the 4-core i7-1185G7 (2→142 ms, 3→105 ms, 4→143 ms, 8→243 ms median)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# ONNXExporter — one-time download + ONNX FP32 export
# ===========================================================================
class ONNXExporter:
    """Downloads the zero-shot model and exports it to ONNX FP32.

    INT8 is deliberately NOT applied: dynamic quantization corrupts
    DeBERTa-v3 (near-uniform probabilities, label flips vs FP32), and FP32
    already meets the latency target on this CPU — see module docstring.
    """

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def export(self) -> bool:
        """Full export pipeline.  Returns True on success."""
        try:
            self.output_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Step 1/2 — Downloading and exporting ONNX FP32 model…")
            logger.info(f"  Downloading {HUGGINGFACE_MODEL} from HuggingFace…")
            model = ORTModelForSequenceClassification.from_pretrained(
                HUGGINGFACE_MODEL, export=True
            )
            model.save_pretrained(str(self.output_dir))

            logger.info("Step 2/2 — Saving tokenizer…")
            try:
                tokenizer = AutoTokenizer.from_pretrained(HUGGINGFACE_MODEL)
            except Exception as e:
                # DeBERTa-v3 ships a SentencePiece tokenizer; slow→fast conversion
                # needs the sentencepiece + protobuf packages in the venv.
                raise RuntimeError(
                    f"Tokenizer load failed ({e}).\n"
                    "  DeBERTa-v3 needs SentencePiece support:\n"
                    "  pip install --break-system-packages sentencepiece protobuf"
                ) from e
            tokenizer.save_pretrained(str(self.output_dir))

            logger.info(f"Export complete.  ONNX FP32 model saved to: {self.output_dir}")
            return True
        except Exception as e:
            logger.error(f"Export failed: {e}")
            return False


# ===========================================================================
# ZeroShotInferencer — load ONNX model, run batched NLI, decode entailment
# ===========================================================================
class ZeroShotInferencer:
    """Loads the pre-exported ONNX FP32 NLI model and scores candidate labels."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model:      Optional[ORTModelForSequenceClassification] = None
        self.tokenizer:  Optional[AutoTokenizer] = None
        self.entail_idx: int = 0

    def load(self) -> None:
        """Load model and tokenizer from the exported directory."""
        import onnxruntime as ort

        logger.info(f"Loading ONNX model from {self.model_dir}…")
        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = INTRA_OP_THREADS
        self.model = ORTModelForSequenceClassification.from_pretrained(
            str(self.model_dir), session_options=session_options
        )
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        self.entail_idx = self._find_entailment_index()
        logger.info(f"Model and tokenizer loaded (entailment logit index: {self.entail_idx}).")

    def _find_entailment_index(self) -> int:
        """Locate the 'entailment' logit from the model config's id2label."""
        id2label = getattr(self.model.config, "id2label", None) or {}
        for idx, label in id2label.items():
            name = str(label).lower()
            if "entail" in name and "not" not in name:
                return int(idx)
        logger.warning("No 'entailment' label found in config — defaulting to index 0")
        return 0

    def predict(self, text: str, labels: List[str]) -> Dict[str, float]:
        """
        Zero-shot classify one text against candidate labels.

        All (premise, hypothesis) pairs run in a single batched forward pass;
        per-label probability = softmax over entailment logits across labels.
        """
        hypotheses = [HYPOTHESIS_TEMPLATE.format(lbl) for lbl in labels]
        inputs = self.tokenizer(
            [text] * len(labels),
            hypotheses,
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=MAX_LENGTH,
        )
        outputs = self.model(**inputs)

        raw = outputs.logits
        logits = np.array(raw.detach() if hasattr(raw, "detach") else raw)

        entail_logits = logits[:, self.entail_idx]
        probs = self._softmax(entail_logits.tolist())
        return {lbl: round(p, 4) for lbl, p in zip(labels, probs)}

    @staticmethod
    def _softmax(logits: List[float]) -> List[float]:
        x = np.array(logits)
        e = np.exp(x - np.max(x))
        return (e / e.sum()).tolist()


# ===========================================================================
# Module-level singleton — shared across library calls
# ===========================================================================
_inferencer: Optional[ZeroShotInferencer] = None
_model_dir:  Optional[Path] = None


def load_model(model_dir: Path = MODEL_DIR) -> None:
    """
    Load the DeBERTa zero-shot ONNX model into the module singleton.

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
            f"  python DeBERTa_headliner.py --export"
        )

    _inferencer = ZeroShotInferencer(model_dir)
    _inferencer.load()
    _model_dir = model_dir


# ===========================================================================
# Public library API
# ===========================================================================

def analyze_headline(
    headline: str,
    labels: Optional[List[str]] = None,
    model_dir: Path = MODEL_DIR,
) -> Dict:
    """
    Zero-shot classify a single headline string.

    Args:
        headline:  The headline text to analyse.
        labels:    Candidate labels. Defaults to ["positive", "negative", "neutral"].
        model_dir: Path to the exported ONNX FP32 model directory.
                   Defaults to ./deberta_zeroshot_onnx (same dir as this script).

    Returns (default labels):
        {
            "headline": str,
            "pos":      float,
            "neg":      float,
            "neu":      float,
            "ss":       float,   # positive - negative
            "lab":      str,     # argmax label
            "ms":       float,   # tokenize + forward + decode wall time
        }

    With custom labels, the three fixed keys and `ss` are replaced by a "scores"
    dict keyed by label (CLI-only; the batch scorers never take that branch).
    """
    load_model(model_dir)

    labels = labels or DEFAULT_LABELS
    t0 = time.perf_counter()
    scores = _inferencer.predict(headline, labels)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Unified 2026-08-09 key names (see tools/Encoder_Models/CLAUDE.md). `scores` is
    # keyed by the caller's label strings and is mapped explicitly rather than
    # update()d, so `lab` keeps yielding a full word. `ms` unifies what this file
    # called `inference_ms` with the other headliners' `elapsed_ms`.
    result: Dict = {"headline": headline}
    if labels == DEFAULT_LABELS:
        result["pos"] = scores["positive"]
        result["neg"] = scores["negative"]
        result["neu"] = scores["neutral"]
        result["ss"]  = round(scores["positive"] - scores["negative"], 4)
    else:
        result["scores"] = scores
    result["lab"] = max(scores, key=scores.get)
    result["ms"]  = round(elapsed_ms, 1)
    return result


def analyze_headline_file(
    path: Union[str, Path],
    labels: Optional[List[str]] = None,
    model_dir: Path = MODEL_DIR,
) -> Dict:
    """
    Read a plain-text file and analyse its first non-empty line as the headline.

    Args:
        path:      Path to a text file (e.g. headline-example.txt).
        labels:    Candidate labels (see analyze_headline).
        model_dir: Path to the exported ONNX FP32 model directory.

    Returns:
        Same dict as analyze_headline(), plus the source file path.

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

    result = analyze_headline(headline, labels, model_dir)
    result["source_file"] = str(path)
    return result


# ===========================================================================
# CLI
# ===========================================================================
def _run_benchmark(headline: str, labels: Optional[List[str]], repeats: int) -> None:
    """Warm the model, then time `repeats` inference calls and print stats."""
    analyze_headline(headline, labels)  # load + warm-up call (excluded from stats)

    timings = []
    for _ in range(repeats):
        timings.append(analyze_headline(headline, labels)["ms"])

    t = np.array(timings)
    print(json.dumps({
        "headline":  headline,
        "repeats":   repeats,
        "min_ms":    round(float(t.min()), 1),
        "median_ms": round(float(np.median(t)), 1),
        "p95_ms":    round(float(np.percentile(t, 95)), 1),
        "max_ms":    round(float(t.max()), 1),
    }, indent=2))


def _cli_run(args) -> bool:
    """Execute CLI logic.  Returns True on success."""
    if args.export:
        exporter = ONNXExporter(MODEL_DIR)
        return exporter.export()

    labels = [l.strip() for l in args.labels.split(",")] if args.labels else None

    if args.headline:
        headline = args.headline
        source_file = None
    else:
        try:
            with open(args.input, "r") as f:
                headline = next((ln.strip() for ln in f if ln.strip()), "")
        except FileNotFoundError:
            logger.error(f"Headline file not found: {args.input}")
            return False
        if not headline:
            logger.error(f"No non-empty line found in {args.input}")
            return False
        source_file = args.input

    if args.benchmark:
        try:
            _run_benchmark(headline, labels, args.benchmark)
        except FileNotFoundError as e:
            logger.error(str(e))
            return False
        return True

    try:
        result = analyze_headline(headline, labels)
    except FileNotFoundError as e:
        logger.error(str(e))
        return False

    if source_file:
        result["source_file"] = source_file
    _print_and_maybe_save(result, args.output)
    return True


def _print_and_maybe_save(result: Dict, output_path: Optional[str]) -> None:
    print(json.dumps(result, indent=2))
    if output_path:
        with open(output_path, "w") as f:
            json.dump(result, f, indent=2)
        logger.info(f"Result written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Zero-shot DeBERTa-v3 headline classification (ONNX FP32)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time model export
  python DeBERTa_headliner.py --export

  # Analyse the headline in a plain-text file (first non-empty line)
  python DeBERTa_headliner.py --input ./headline-example.txt

  # Analyse a headline string directly
  python DeBERTa_headliner.py --headline "Apple reports record quarterly earnings"

  # Custom labels / warm-latency benchmark
  python DeBERTa_headliner.py --headline "..." --labels bullish,bearish
  python DeBERTa_headliner.py --headline "..." --benchmark 20
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--export",
        action="store_true",
        help=f"One-time export: download {HUGGINGFACE_MODEL} → ONNX FP32",
    )
    mode.add_argument(
        "--input",
        type=str,
        help="Path to a plain-text file; its first non-empty line is the headline",
    )
    mode.add_argument(
        "--headline",
        type=str,
        help="Headline text to analyse directly (no file required)",
    )

    parser.add_argument(
        "--labels",
        type=str,
        default=None,
        help="Comma-separated candidate labels (default: positive,negative,neutral)",
    )
    parser.add_argument(
        "--benchmark",
        type=int,
        default=None,
        metavar="N",
        help="Run N warm inference repeats and print latency stats instead of scores",
    )
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON path (optional)",
    )

    args = parser.parse_args()
    sys.exit(0 if _cli_run(args) else 1)


if __name__ == "__main__":
    main()
