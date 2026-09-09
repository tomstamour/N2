#!/usr/bin/env python
"""
finbert_embed_infer.py -- FinBERT embeddings for a SINGLE press release (production / inference).

Same model, mean pooling and text prep as make_finbert_embeddings.py, one PR at a time.
Produces the 768-dim headline embedding and the 768-dim body embedding.

CLI:
    /home/tom/venv/bin/python finbert_embed_infer.py \\
        --headline "Acme Corp receives FDA approval for lead drug" \\
        --body-file /path/to/body.txt

    cat body.txt | /home/tom/venv/bin/python finbert_embed_infer.py \\
        --headline "Acme Corp receives FDA approval" --body-stdin --pretty

Output: JSON {"hd": [..768 floats..], "bd": [..768 floats..]} on stdout
        (or to --out FILE). Empty headline/body -> a 768-length all-zero list.

Library use (for the live chain):
    from finbert_embed_infer import embed_pr
    v = embed_pr(headline, body)     # {"hd": np.ndarray(768,), "bd": np.ndarray(768,)}  float32

One-time model export (if not done yet):
    /home/tom/venv/bin/python finbert_embed_core.py --export
"""

import argparse
import json
import sys
from pathlib import Path

from finbert_embed_core import EMBED_DIM, embed_pr, load_model

__all__ = ["embed_pr", "load_model", "EMBED_DIM"]


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--headline", default="", help="headline text")
    body = ap.add_mutually_exclusive_group()
    body.add_argument("--body", default="", help="body text (inline)")
    body.add_argument("--body-file", help="read body text from this file")
    body.add_argument("--body-stdin", action="store_true", help="read body text from stdin")
    ap.add_argument("--pretty", action="store_true", help="indent the JSON output")
    ap.add_argument("--out", help="write JSON here instead of stdout")
    args = ap.parse_args()

    body_text = args.body
    if args.body_file:
        body_text = Path(args.body_file).expanduser().read_text(encoding="utf-8")
    elif args.body_stdin:
        body_text = sys.stdin.read()

    if not args.headline.strip() and not body_text.strip():
        ap.error("need at least one of --headline / --body / --body-file / --body-stdin")

    vec = embed_pr(args.headline, body_text)
    payload = {
        "hd": [round(float(x), 6) for x in vec["hd"]],
        "bd": [round(float(x), 6) for x in vec["bd"]],
    }
    text = json.dumps(payload, indent=2 if args.pretty else None)

    if args.out:
        Path(args.out).expanduser().write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}  (hd:{len(payload['hd'])}, bd:{len(payload['bd'])})")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
