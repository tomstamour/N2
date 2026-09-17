#!/usr/bin/env python3
"""
FLANG-BERT_headliner.py — Headline sentiment via FLANG-BERT (ONNX INT8)

Runs avinasht/FLANG-BERT_flang-bert (a 3-class sequence-classification
fine-tune of SALT-NLP/FLANG-BERT — the official FLANG-BERT itself is a
fill-mask base model with no sentiment head) on a single PR headline.

The checkpoint's config.json labels are undocumented (LABEL_0/1/2), so the
one-time export step probes the quantized model with unambiguous financial
sentences, derives which logit index means negative/neutral/positive, and
writes the verified mapping to label_map.json. Inference refuses to run
without that file.

Two-phase usage (CLI):

    # Phase 1 — one-time export (downloads model, converts to ONNX, quantizes
    # to INT8, verifies + saves the label mapping)
    /home/tom/venv/bin/python FLANG-BERT_headliner.py --export

    # Phase 2 — inference on a plain-text file (first non-empty line = headline)
    /home/tom/venv/bin/python FLANG-BERT_headliner.py --input ./headline-example.txt

    # Inference on a raw headline string
    /home/tom/venv/bin/python FLANG-BERT_headliner.py --headline "Apple reports record quarterly earnings"

    # Warm-latency benchmark
    /home/tom/venv/bin/python FLANG-BERT_headliner.py --headline "..." --benchmark 20

Library usage (filename has a hyphen — load via importlib):

    import importlib.util
    spec = importlib.util.spec_from_file_location("flangbert_headliner", "FLANG-BERT_headliner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mod.load_model()                  # optional pre-warm
    result = mod.analyze_headline("Apple reports record quarterly earnings")
    # {"headline": "...", "pos": 0.82, "neg": 0.03, "neu": 0.15,
    #  "ss": 0.79, "lab": "positive", "ms": 14.2}
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
MODEL_DIR         = SCRIPT_DIR / "flangbert_sentiment_onnx"
HUGGINGFACE_MODEL = "avinasht/FLANG-BERT_flang-bert"
# The fine-tune repo ships weights only (no tokenizer files); the tokenizer
# comes from the base model it was fine-tuned from.
TOKENIZER_MODEL   = "SALT-NLP/FLANG-BERT"
LABEL_MAP_FILE    = "label_map.json"

SENTIMENTS = ["negative", "neutral", "positive"]
MAX_LENGTH = 128

# Unambiguous probes used at export time to discover which logit index means
# what (the checkpoint ships generic LABEL_0/1/2). Both probes of a class must
# agree, and the three classes must land on three distinct indices.
PROBE_SENTENCES = {
    "negative": [
        "Company files for Chapter 11 bankruptcy as shares plunge 80%",
        "Firm reports huge quarterly loss, cuts guidance and announces mass layoffs",
    ],
    "neutral": [
        "Company schedules its annual shareholder meeting for June 15",
        "Company to present at an industry investor conference next week",
    ],
    # Positive probes stay in Financial-PhraseBank-style earnings language: the
    # model mislabels out-of-domain positives (a "Phase 3 endpoint met" probe
    # scored negative), so exotic phrasing can't be used to verify label order.
    "positive": [
        "The company's quarterly net profit rose 30 percent, beating analyst expectations",
        "Operating profit increased significantly and the company raised its dividend",
    ],
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# ONNXExporter — one-time download + export + INT8 quantization + label probe
# ===========================================================================
class ONNXExporter:
    """Downloads the FLANG-BERT fine-tune, exports to ONNX FP32, quantizes to
    INT8, then probes the quantized model to verify the label mapping."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def export(self) -> bool:
        """Full export pipeline.  Returns True on success."""
        temp_dir = tempfile.mkdtemp()
        try:
            logger.info("Step 1/4 — Downloading and exporting base ONNX model…")
            base_model_path = self._download_and_export_base(Path(temp_dir))

            logger.info("Step 2/4 — Quantizing to INT8…")
            self._quantize_int8(base_model_path, self.output_dir)

            logger.info("Step 3/4 — Copying tokenizer and config files…")
            self._copy_tokenizer_files(Path(temp_dir), self.output_dir)

            logger.info("Step 4/4 — Probing quantized model to verify label mapping…")
            self._verify_and_save_label_map(self.output_dir)

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

        logger.info(f"  Saving tokenizer (from base model {TOKENIZER_MODEL})…")
        tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL)
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

    def _verify_and_save_label_map(self, model_dir: Path) -> None:
        """
        Run the probe sentences through the freshly quantized model and derive
        which logit index corresponds to each sentiment. Fails the export if
        the probes disagree within a class or two classes collide on an index —
        better no model than one with silently wrong labels.
        """
        model     = ORTModelForSequenceClassification.from_pretrained(str(model_dir))
        tokenizer = AutoTokenizer.from_pretrained(str(model_dir))

        sentiment_to_index: Dict[str, int] = {}
        for sentiment, sentences in PROBE_SENTENCES.items():
            indices = []
            for sent in sentences:
                inputs  = tokenizer(sent, return_tensors="pt",
                                    truncation=True, max_length=MAX_LENGTH)
                logits  = np.array(model(**inputs).logits).flatten()
                idx     = int(np.argmax(logits))
                indices.append(idx)
                logger.info(f"  probe [{sentiment}] → logit index {idx}  "
                            f"(logits {np.round(logits, 3).tolist()})  \"{sent[:60]}…\"")
            if len(set(indices)) != 1:
                raise RuntimeError(
                    f"Label probe inconsistent for '{sentiment}': probes hit "
                    f"indices {indices}. Refusing to guess the label mapping."
                )
            sentiment_to_index[sentiment] = indices[0]

        if len(set(sentiment_to_index.values())) != len(SENTIMENTS):
            raise RuntimeError(
                f"Label probe collision — two sentiments map to the same logit "
                f"index: {sentiment_to_index}. Refusing to guess the label mapping."
            )

        index_to_sentiment = {str(idx): s for s, idx in sentiment_to_index.items()}
        map_path = model_dir / LABEL_MAP_FILE
        with open(map_path, "w") as f:
            json.dump(index_to_sentiment, f, indent=2)
        logger.info(f"  Verified label mapping {index_to_sentiment} → {map_path}")


# ===========================================================================
# FlangBertInferencer — load ONNX model + label map, run inference
# ===========================================================================
class FlangBertInferencer:
    """Loads the pre-exported ONNX INT8 model and produces sentiment scores."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model:       Optional[ORTModelForSequenceClassification] = None
        self.tokenizer:   Optional[AutoTokenizer] = None
        self.label_order: List[str] = []   # sentiment name per logit index

    def load(self) -> None:
        """Load model, tokenizer and the export-time verified label map."""
        map_path = self.model_dir / LABEL_MAP_FILE
        if not map_path.exists():
            raise FileNotFoundError(
                f"Label map not found: {map_path}\n"
                f"The checkpoint's labels are undocumented — inference requires the "
                f"probe-verified mapping written by the export step. Re-run:\n"
                f"  python FLANG-BERT_headliner.py --export"
            )
        with open(map_path, "r") as f:
            index_to_sentiment = json.load(f)
        self.label_order = [index_to_sentiment[str(i)] for i in range(len(SENTIMENTS))]

        logger.info(f"Loading ONNX model from {self.model_dir}…")
        self.model     = ORTModelForSequenceClassification.from_pretrained(str(self.model_dir))
        self.tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir))
        logger.info(f"Model and tokenizer loaded (logit order: {self.label_order}).")

    def predict(self, text: str) -> Dict[str, float]:
        """One headline → softmax over the 3 class logits, remapped through the
        verified label map."""
        inputs  = self.tokenizer(text, return_tensors="pt",
                                 truncation=True, max_length=MAX_LENGTH)
        outputs = self.model(**inputs)

        raw = outputs.logits[0]
        logits = np.array(raw.detach() if hasattr(raw, "detach") else raw).flatten().tolist()

        probs = self._softmax(logits)
        return {label: round(p, 4) for label, p in zip(self.label_order, probs)}

    @staticmethod
    def _softmax(logits: List[float]) -> List[float]:
        x = np.array(logits)
        e = np.exp(x - np.max(x))
        return (e / e.sum()).tolist()


# ===========================================================================
# Module-level singleton — shared across library calls
# ===========================================================================
_inferencer: Optional[FlangBertInferencer] = None
_model_dir:  Optional[Path] = None


def load_model(model_dir: Path = MODEL_DIR) -> None:
    """
    Load the FLANG-BERT sentiment ONNX model into the module singleton.

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
            f"  python FLANG-BERT_headliner.py --export"
        )

    _inferencer = FlangBertInferencer(model_dir)
    _inferencer.load()
    _model_dir = model_dir


# ===========================================================================
# Public library API
# ===========================================================================

def analyze_headline(headline: str, model_dir: Path = MODEL_DIR) -> Dict:
    """
    Run FLANG-BERT sentiment analysis on a single headline string.

    Args:
        headline:  The headline text to analyse.
        model_dir: Path to the exported ONNX INT8 model directory.
                   Defaults to ./flangbert_sentiment_onnx (same dir as this script).

    Returns:
        {
            "headline": str,
            "pos":      float,
            "neg":      float,
            "neu":      float,
            "ss":       float,   # positive - negative
            "lab":      str,     # argmax of the three classes
            "ms":       float,   # tokenize + forward + decode wall time
        }
    """
    load_model(model_dir)

    t0 = time.perf_counter()
    scores = _inferencer.predict(headline)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    # Unified 2026-08-09 key names (see tools/Encoder_Models/CLAUDE.md). `scores` is
    # keyed by self.label_order — full words straight off the model config — and is
    # mapped explicitly rather than update()d, so `lab` keeps yielding "positive" /
    # "neutral" / "negative" instead of the short key. `ms` unifies what this file
    # called `inference_ms` with the other headliners' `elapsed_ms`.
    result: Dict = {"headline": headline}
    result["pos"] = scores["positive"]
    result["neg"] = scores["negative"]
    result["neu"] = scores["neutral"]
    result["ss"]  = round(scores["positive"] - scores["negative"], 4)
    result["lab"] = max(scores, key=scores.get)
    result["ms"]  = round(elapsed_ms, 1)
    return result


def analyze_headline_file(path: Union[str, Path], model_dir: Path = MODEL_DIR) -> Dict:
    """
    Read a plain-text file and analyse its first non-empty line as the headline.

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

    result = analyze_headline(headline, model_dir)
    result["source_file"] = str(path)
    return result


# ===========================================================================
# CLI
# ===========================================================================
def _run_benchmark(headline: str, repeats: int) -> None:
    """Warm the model, then time `repeats` inference calls and print stats."""
    analyze_headline(headline)  # load + warm-up call (excluded from stats)

    timings = []
    for _ in range(repeats):
        timings.append(analyze_headline(headline)["ms"])

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

    try:
        if args.benchmark:
            _run_benchmark(headline, args.benchmark)
            return True
        result = analyze_headline(headline)
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
        description="Headline sentiment via FLANG-BERT fine-tune (ONNX INT8)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time model export (also verifies + saves the label mapping)
  python FLANG-BERT_headliner.py --export

  # Analyse the headline in a plain-text file (first non-empty line)
  python FLANG-BERT_headliner.py --input ./headline-example.txt

  # Analyse a headline string directly
  python FLANG-BERT_headliner.py --headline "Apple reports record quarterly earnings"

  # Warm-latency benchmark
  python FLANG-BERT_headliner.py --headline "..." --benchmark 20
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--export",
        action="store_true",
        help=f"One-time export: download {HUGGINGFACE_MODEL} → ONNX → INT8 quantize → verify label map",
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
