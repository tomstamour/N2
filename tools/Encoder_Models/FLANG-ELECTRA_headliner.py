#!/usr/bin/env python3
"""
FLANG-ELECTRA_headliner.py — Headline sentiment via FLANG-ELECTRA (ONNX FP32)

Runs avinasht/FLANG-ELECTRA_Synonym-wordnet — the best public sentiment
fine-tune of SALT-NLP/FLANG-ELECTRA (financial-domain ELECTRA-large,
FLANG paper arXiv 2211.00083; fine-tune eval accuracy 93.8%, 3-class,
Financial PhraseBank-style labels) — on a single PR headline string.

Why FP32 and not INT8 (benchmarked 2026-07-18 on the i7-1185G7):
  - INT8 dynamic quantization corrupts this ELECTRA-large's judgment,
    same failure mode as ModernBERT-large: e.g. "Nasdaq delisting notice"
    flips negative(0.80 FP32) → neutral(1.00 INT8). INT8 was faster
    (~43 ms median) but untrustworthy.
  - FP32 labels are correct on all 8 verification headlines and warm
    latency is ~110 ms median / ~133 ms p95 (threads=2) — under the
    150 ms target, so quantization isn't needed.
  - intra_op threads: 2 is optimal; 4 keeps the same median but p95
    explodes to ~290 ms (hyperthread thrashing).
Known model quirk (present in FP32 too, PhraseBank training style):
dry factual filings can score neutral — e.g. "files for Chapter 11
bankruptcy protection" → neutral 0.999.

Two-phase usage (CLI):

    # Phase 1 — one-time export (downloads model ~1.3 GB, converts to ONNX FP32)
    /home/tom/venv/bin/python FLANG-ELECTRA_headliner.py --export

    # Phase 2 — inference on a plain-text file (first non-empty line = headline)
    /home/tom/venv/bin/python FLANG-ELECTRA_headliner.py --input ./headline-example.txt

    # Inference on a raw headline string
    /home/tom/venv/bin/python FLANG-ELECTRA_headliner.py --headline "Apple reports record quarterly earnings"

    # Warm-latency benchmark
    /home/tom/venv/bin/python FLANG-ELECTRA_headliner.py --headline "..." --benchmark 20

Library usage (filename contains a hyphen, so use importlib):

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "flang_electra_headliner",
        "/home/tom/Documents/ibkr_scripts/N2/scripts/tools/Encoder_Models/FLANG-ELECTRA_headliner.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    mod.load_model()                      # optional pre-warm
    result = mod.analyze_headline("Apple reports record quarterly earnings")
    # {"headline": "...", "pos": 0.91, "neg": 0.02, "neu": 0.07,
    #  "ss": 0.89, "lab": "positive", "ms": 110.3}
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
MODEL_DIR         = SCRIPT_DIR / "flang_electra_onnx"
HUGGINGFACE_MODEL = "avinasht/FLANG-ELECTRA_Synonym-wordnet"
# The fine-tune repo ships only weights+config — tokenizer files (vocab.txt
# etc.) must come from the base model repo:
TOKENIZER_MODEL   = "SALT-NLP/FLANG-ELECTRA"

# The checkpoint's config.json id2label is junk (LABEL_0/1/2).
# Real class meaning per logit index, verified empirically 2026-07-18 on
# unambiguous headlines: idx0 dominant on "workforce reduction of 40%"
# (0.998) and "Nasdaq delisting notice" (0.80); idx1 on conference/
# earnings-call-date announcements (1.00); idx2 on "record quarterly
# earnings" and "FDA approves" (0.998). Standard Financial PhraseBank order:
LABEL_ORDER = ["negative", "neutral", "positive"]

MAX_LENGTH       = 256
INTRA_OP_THREADS = 2   # fastest on the 4-core i7-1185G7; >4 thrashes hyperthreads

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
# (INT8 deliberately NOT applied — dynamic quantization corrupts labels on
#  this ELECTRA-large; see header. FP32 already meets the latency target.)
# ===========================================================================
class ONNXExporter:
    """Downloads the FLANG-ELECTRA fine-tune and exports it to ONNX FP32."""

    def __init__(self, output_dir: Path):
        self.output_dir = output_dir

    def export(self) -> bool:
        """Full export pipeline.  Returns True on success."""
        temp_dir = tempfile.mkdtemp()
        try:
            logger.info("Step 1/3 — Downloading and exporting base ONNX model…")
            base_model_path = self._download_and_export_base(Path(temp_dir))

            logger.info("Step 2/3 — Copying FP32 model (INT8 skipped: corrupts labels)…")
            self.output_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(base_model_path), str(self.output_dir / "model.onnx"))

            logger.info("Step 3/3 — Copying tokenizer and config files…")
            self._copy_tokenizer_files(Path(temp_dir), self.output_dir)

            logger.info(f"Export complete.  ONNX FP32 model saved to: {self.output_dir}")
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
        tokenizer = AutoTokenizer.from_pretrained(TOKENIZER_MODEL)
        tokenizer.save_pretrained(str(temp_dir))

        return temp_dir / "model.onnx"

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
# FlangElectraInferencer — load ONNX model, run inference, decode logits
# ===========================================================================
class FlangElectraInferencer:
    """Loads the pre-exported ONNX FP32 model and produces sentiment scores."""

    def __init__(self, model_dir: Path):
        self.model_dir = model_dir
        self.model:     Optional[ORTModelForSequenceClassification] = None
        self.tokenizer: Optional[AutoTokenizer] = None

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
        logger.info("Model and tokenizer loaded.")

    def predict(self, text: str) -> Dict[str, float]:
        """
        Run inference on one headline string.

        Logit index order per LABEL_ORDER. Returns class probabilities
        rounded to 4 dp.
        """
        inputs = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=MAX_LENGTH
        )
        outputs = self.model(**inputs)

        raw = outputs.logits[0]
        logits = np.array(raw.detach() if hasattr(raw, "detach") else raw).flatten().tolist()

        probs = self._softmax(logits)
        return {label: round(p, 4) for label, p in zip(LABEL_ORDER, probs)}

    @staticmethod
    def _softmax(logits: List[float]) -> List[float]:
        x = np.array(logits)
        e = np.exp(x - np.max(x))
        return (e / e.sum()).tolist()


# ===========================================================================
# Module-level singleton — shared across library calls
# ===========================================================================
_inferencer: Optional[FlangElectraInferencer] = None
_model_dir:  Optional[Path] = None


def load_model(model_dir: Path = MODEL_DIR) -> None:
    """
    Load the FLANG-ELECTRA ONNX model into the module singleton.

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
            f"  python FLANG-ELECTRA_headliner.py --export"
        )

    _inferencer = FlangElectraInferencer(model_dir)
    _inferencer.load()
    _model_dir = model_dir


# ===========================================================================
# Public library API
# ===========================================================================

def analyze_headline(headline: str, model_dir: Path = MODEL_DIR) -> Dict:
    """
    Run FLANG-ELECTRA sentiment analysis on a single headline string.

    Args:
        headline:  The headline text to analyse.
        model_dir: Path to the exported ONNX FP32 model directory.
                   Defaults to ./flang_electra_onnx (same dir as this script).

    Returns:
        {
            "headline":        str,
            "pos":             float,
            "neg":             float,
            "neu":             float,
            "ss":              float,   # positive - negative
            "lab":             str,     # argmax of the three classes
            "ms":              float,   # tokenize + forward + decode wall time
        }
    """
    load_model(model_dir)

    t0 = time.perf_counter()
    scores = _inferencer.predict(headline)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    return {
        "headline":        headline,
        # Unified 2026-08-09 key names; internals keep full words (see FinMarBa).
        # `ms` also unifies what this file used to call `inference_ms` with the
        # `elapsed_ms` the other headliners returned.
        "pos": scores["positive"],
        "neg": scores["negative"],
        "neu": scores["neutral"],
        "ss":  round(scores["positive"] - scores["negative"], 4),
        "lab": max(LABEL_ORDER, key=lambda k: scores[k]),
        "ms":  round(elapsed_ms, 1),
    }


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

    if args.benchmark:
        try:
            _run_benchmark(headline, args.benchmark)
        except FileNotFoundError as e:
            logger.error(str(e))
            return False
        return True

    try:
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
        description="Headline sentiment via FLANG-ELECTRA (ONNX FP32)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time model export
  python FLANG-ELECTRA_headliner.py --export

  # Analyse the headline in a plain-text file (first non-empty line)
  python FLANG-ELECTRA_headliner.py --input ./headline-example.txt

  # Analyse a headline string directly
  python FLANG-ELECTRA_headliner.py --headline "Apple reports record quarterly earnings"

  # Warm-latency benchmark
  python FLANG-ELECTRA_headliner.py --headline "..." --benchmark 20
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
