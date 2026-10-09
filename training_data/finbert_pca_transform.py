#!/usr/bin/env python
"""
finbert_pca_transform.py -- apply the fitted FinBERT PCA transform at inference.

Loads finbert_pca.pkl (written by make_finbert_pca.py) and projects a single
press release's raw 768-dim headline/body embeddings onto the training-fold PCA
basis -- the exact same transform used to build the training matrix.

    from finbert_embed_infer import embed_pr
    from finbert_pca_transform import transform_pr

    pcs = transform_pr(embed_pr(headline, body))
    # {"hd_pca": [..k1 floats..], "bd_pca": [..k2 floats..]}

CLI:
    /home/tom/venv/bin/python finbert_pca_transform.py \\
        --headline "Acme Corp receives FDA approval" --body-file body.txt --pretty
"""

import argparse
import json
import sys
from pathlib import Path

import joblib
import numpy as np

HERE = Path(__file__).resolve().parent
MODEL_PATH = HERE / "model-1" / "finbert_pca.pkl"

_M = None


def load(path: Path = MODEL_PATH) -> dict:
    global _M
    if _M is None:
        if not Path(path).is_file():
            raise FileNotFoundError(
                f"{path} not found -- build it first:\n"
                f"  /home/tom/venv/bin/python make_finbert_pca.py"
            )
        _M = joblib.load(path)
    return _M


def _project(vec, part) -> np.ndarray:
    v = np.asarray(vec, dtype=np.float64).ravel()
    if v.shape[0] != part["mean_"].shape[0]:
        raise ValueError(f"expected {part['mean_'].shape[0]} dims, got {v.shape[0]}")
    return (v - part["mean_"]) @ part["components_"].T


def transform_pr(emb: dict, path: Path = MODEL_PATH) -> dict:
    """emb: {'hd': ndarray(768,), 'bd': ndarray(768,)} -> {'hd_pca': [...], 'bd_pca': [...]}."""
    m = load(path)
    return {
        "hd_pca": _project(emb["hd"], m["hd"]).tolist(),
        "bd_pca": _project(emb["bd"], m["bd"]).tolist(),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--headline", default="")
    body = ap.add_mutually_exclusive_group()
    body.add_argument("--body", default="")
    body.add_argument("--body-file")
    body.add_argument("--body-stdin", action="store_true")
    ap.add_argument("--model", default=str(MODEL_PATH))
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    from finbert_embed_infer import embed_pr

    body_text = args.body
    if args.body_file:
        body_text = Path(args.body_file).expanduser().read_text(encoding="utf-8")
    elif args.body_stdin:
        body_text = sys.stdin.read()
    if not args.headline.strip() and not body_text.strip():
        ap.error("need at least one of --headline / --body / --body-file / --body-stdin")

    pcs = transform_pr(embed_pr(args.headline, body_text), Path(args.model))
    payload = {"hd_pca": [round(x, 6) for x in pcs["hd_pca"]],
               "bd_pca": [round(x, 6) for x in pcs["bd_pca"]]}
    text = json.dumps(payload, indent=2 if args.pretty else None)
    if args.out:
        Path(args.out).expanduser().write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}  (hd_pca:{len(payload['hd_pca'])}, "
              f"bd_pca:{len(payload['bd_pca'])})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
