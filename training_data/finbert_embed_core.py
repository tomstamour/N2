#!/usr/bin/env python
"""
finbert_embed_core.py -- shared FinBERT (ProsusAI/finbert) embedding backend.

Produces the 768-dim contextual embedding of a text as the attention-mask-weighted
mean of the model's last hidden layer (mean pooling). Backs both:

  * make_finbert_embeddings.py -- batch: whole training TSV  -> wide sidecar TSV
  * finbert_embed_infer.py     -- single PR (CLI flags / importable embed_pr)

Model backend: a dedicated ONNX FP32 feature-extraction export of ProsusAI/finbert.
This is NOT the INT8 classification model in ../FinBERT/finbert_onnx/ -- that one
only emits logits, and INT8 rounding noise would land straight in the embedding
values (worse for a downstream PCA/regression than for an argmax label).

One-time export (writes ../FinBERT/finbert_feat_onnx/, ProsusAI/finbert is already
in the HuggingFace cache on this machine):

    /home/tom/venv/bin/python finbert_embed_core.py --export

Text prep (matches the body encoders, see tools/Encoder_Models/body_prep.py):
  headline -> raw text, tokenizer truncates at 512 tokens
  body     -> body_prep.prep_body (strip wire boilerplate + first 200 words),
              tokenizer then truncates at 512 tokens

No PCA here -- that transform is fit later, on the training fold only.
"""

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
SCRIPTS_DIR = HERE.parent                         # training_data -> scripts
FINBERT_DIR = SCRIPTS_DIR / "FinBERT"
MODEL_DIR = FINBERT_DIR / "finbert_feat_onnx"     # gitignored model output
BODY_PREP_PATH = SCRIPTS_DIR / "tools" / "Encoder_Models" / "body_prep.py"

HF_MODEL = "ProsusAI/finbert"
MAX_TOKENS = 512
BODY_CAP_WORDS = 200
EMBED_DIM = 768

_tokenizer = None
_model = None
_prep_body_fn = None


# ---------------------------------------------------------------------------
# body_prep reuse (lazy: it drags in pandas/sklearn via keyword_finder)
# ---------------------------------------------------------------------------
def _load_body_prep():
    global _prep_body_fn
    if _prep_body_fn is None:
        spec = importlib.util.spec_from_file_location(
            "body_prep_for_embed", BODY_PREP_PATH
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["body_prep_for_embed"] = mod
        spec.loader.exec_module(mod)
        _prep_body_fn = mod.prep_body
    return _prep_body_fn


# ---------------------------------------------------------------------------
# model export / load
# ---------------------------------------------------------------------------
def export_model(force: bool = False) -> None:
    """One-time: export ProsusAI/finbert to an ONNX FP32 feature-extraction model."""
    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    if (MODEL_DIR / "model.onnx").is_file() and not force:
        print(f"[export] {MODEL_DIR / 'model.onnx'} already exists "
              f"-- skipping (pass --force to re-export)")
        return

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[export] exporting {HF_MODEL} -> ONNX FP32 at {MODEL_DIR} ...", flush=True)
    model = ORTModelForFeatureExtraction.from_pretrained(HF_MODEL, export=True)
    model.save_pretrained(str(MODEL_DIR))
    AutoTokenizer.from_pretrained(HF_MODEL).save_pretrained(str(MODEL_DIR))
    print(f"[export] done -> {MODEL_DIR}", flush=True)


def load_model() -> None:
    """Load the exported ONNX model + tokenizer into module singletons (idempotent)."""
    global _tokenizer, _model
    if _model is not None:
        return

    from optimum.onnxruntime import ORTModelForFeatureExtraction
    from transformers import AutoTokenizer

    if not (MODEL_DIR / "model.onnx").is_file():
        raise FileNotFoundError(
            f"feature-extraction model not found at {MODEL_DIR}\n"
            f"run the one-time export first:\n"
            f"  /home/tom/venv/bin/python {Path(__file__).name} --export"
        )
    _tokenizer = AutoTokenizer.from_pretrained(str(MODEL_DIR))
    _model = ORTModelForFeatureExtraction.from_pretrained(str(MODEL_DIR))


# ---------------------------------------------------------------------------
# embedding
# ---------------------------------------------------------------------------
def _embed_batch(texts):
    """Mean-pooled 768-dim embedding for a list of non-empty strings -> (B, 768) float32."""
    enc = _tokenizer(
        list(texts),
        padding=True,
        truncation=True,
        max_length=MAX_TOKENS,
        return_tensors="np",
    )
    out = _model(**enc)
    last_hidden = np.asarray(out.last_hidden_state, dtype=np.float32)      # (B, T, 768)
    mask = np.asarray(enc["attention_mask"], dtype=np.float32)[:, :, None]  # (B, T, 1)
    summed = (last_hidden * mask).sum(axis=1)                              # (B, 768)
    counts = np.clip(mask.sum(axis=1), 1e-9, None)                        # (B, 1)
    return (summed / counts).astype(np.float32)


def prep_headline(headline: str) -> str:
    return (headline or "").strip()


def prep_body(body: str, headline: str = "") -> str:
    return _load_body_prep()(body or "", headline or "", BODY_CAP_WORDS)


def embed_texts(texts, batch_size: int = 32, progress_every: int = 0):
    """Embed a list of already-prepped strings -> (N, 768) float32.

    Blank / whitespace-only entries get an all-zero vector (no model call).
    """
    load_model()
    n = len(texts)
    out = np.zeros((n, EMBED_DIM), dtype=np.float32)
    idx = [i for i, t in enumerate(texts) if t and t.strip()]
    for b in range(0, len(idx), batch_size):
        chunk = idx[b:b + batch_size]
        vecs = _embed_batch([texts[i] for i in chunk])
        for j, i in enumerate(chunk):
            out[i] = vecs[j]
        done = min(b + batch_size, len(idx))
        if progress_every and (done % progress_every < batch_size or done == len(idx)):
            print(f"    {done}/{len(idx)} non-blank embedded "
                  f"({n - len(idx)} blank -> zeros)", flush=True)
    return out


def embed_pr(headline: str, body: str = "") -> dict:
    """Embed one press release. Returns {'hd': ndarray(768,), 'bd': ndarray(768,)} float32.

    Empty headline or body -> all-zero vector for that stream.
    """
    load_model()
    h = prep_headline(headline)
    b = prep_body(body, headline)
    hv = _embed_batch([h])[0] if h else np.zeros(EMBED_DIM, dtype=np.float32)
    bv = _embed_batch([b])[0] if b else np.zeros(EMBED_DIM, dtype=np.float32)
    return {"hd": hv, "bd": bv}


# ---------------------------------------------------------------------------
# CLI (export only -- this module is imported by the two embedding scripts)
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--export", action="store_true",
                    help="one-time: export the ONNX FP32 feature-extraction model")
    ap.add_argument("--force", action="store_true",
                    help="re-export even if the model dir already exists")
    args = ap.parse_args()

    if args.export:
        export_model(force=args.force)
        return 0
    ap.error("nothing to do -- pass --export "
             "(this module is imported by make_finbert_embeddings.py / finbert_embed_infer.py)")


if __name__ == "__main__":
    sys.exit(main())
