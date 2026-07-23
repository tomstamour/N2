#!/usr/bin/env python
"""
body_prep.py — clean a PR Body cell and cap it for the encoder headliners.

The raw `Body` strings in the PR-stats TSVs carry newswire plumbing that wastes
the models' token windows (FLANG-BERT sees only 128 tokens): a CSV quote-escaping
artifact, a verbatim repeat of the Headline, the "CITY, Date (GLOBE NEWSWIRE) --"
dateline (sometimes doubled), and globenewswire/prnewswire tracker URLs that
tokenize into 50+ wordpieces each.

`clean_body(body, headline)` strips all of that (URL/vendor scrubbing reuses
strip_wire_boilerplate from tools/keyword/keyword_finder.py); `cap_words(text)`
then keeps the first 200 words. Each headliner's own tokenizer enforces the
final 512/256/128-token maximum via truncation=True.

Self-test (prints before/after for known-messy rows of the CoffeeFilter TSV):
    /home/tom/venv/bin/python body_prep.py [--input FILE.tsv] [--rows 0,100,3000]
"""

import importlib.util
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_KEYWORD_FINDER = HERE.parent / "keyword" / "keyword_finder.py"

DEFAULT_CAP_WORDS = 200
DATELINE_WINDOW = 300  # chars of body scanned for dateline markers

_WS_RE = re.compile(r"\s+")
_EMPTY_PAREN_RE = re.compile(r"[(\[]\s*[)\]]")
# "(https://…)" parentheticals (globenewswire Tracker links, logo links) — removed
# whole so no dangling "(" survives; bare URLs are handled by strip_wire_boilerplate
_URL_PAREN_RE = re.compile(r"\(\s*(?:https?://|www\.)[^()]*\)")
_PAREN_GAP_RE = re.compile(r"\(\s+")
_GAP_PAREN_RE = re.compile(r"\s+\)")
# Wire-distributor dateline markers, e.g. "TORONTO, May 5, 2026 (GLOBE NEWSWIRE) -- "
# or "/PRNewswire/ -- "; also a bare "Month D, YYYY -- " dateline with no vendor tag.
_DATELINE_RE = re.compile(
    r"(?:"
    r"\((?:GLOBE\s?NEWSWIRE|ACCESS\s?Newswire|BUSINESS\s?WIRE|"
    r"Newsfile\s?Corp\.?|PRNewswire|MARKETWIRED?)\)"
    r"|/PRNewswire[^/\n]{0,60}/"
    r"|\b(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+\d{1,2},\s+\d{4}\s*(?=(?:--+|—|–))"
    r")\s*(?:--+|—|–)?\s*",
    re.IGNORECASE,
)


def _load_wire_stripper():
    """Reuse strip_wire_boilerplate from tools/keyword/keyword_finder.py."""
    spec = importlib.util.spec_from_file_location("kf_for_body_prep", _KEYWORD_FINDER)
    module = importlib.util.module_from_spec(spec)
    sys.modules["kf_for_body_prep"] = module
    spec.loader.exec_module(module)
    return module.strip_wire_boilerplate


_strip_wire = None  # loaded lazily: keyword_finder drags in pandas/sklearn


def clean_body(body: str, headline: str = "") -> str:
    """Strip CSV-escape artifacts, the repeated headline, the wire dateline and
    tracker URLs/vendor tokens from a raw Body cell."""
    global _strip_wire
    if _strip_wire is None:
        _strip_wire = _load_wire_stripper()

    s = body.strip()
    # CSV quote-escaping artifact: field wrapped in '"' with internal '"' doubled
    if s.startswith('"'):
        s = s[1:]
        if s.endswith('"'):
            s = s[:-1]
    s = _WS_RE.sub(" ", s.replace('""', '"')).strip()

    # leading verbatim repeat of the headline
    h = _WS_RE.sub(" ", (headline or "")).strip()
    if h and s[: len(h)].casefold() == h.casefold():
        s = s[len(h):].lstrip(" –—-:.,")

    # dateline(s): cut through the LAST wire marker in the opening window
    # (handles the doubled "CITY, Date ... CITY, Date /PRNewswire/ --" case)
    last = None
    for m in _DATELINE_RE.finditer(s[:DATELINE_WINDOW]):
        last = m
    if last:
        s = s[last.end():].lstrip(" –—-")

    s = _URL_PAREN_RE.sub(" ", s)
    s = _strip_wire(s)
    s = _PAREN_GAP_RE.sub("(", _GAP_PAREN_RE.sub(")", s))
    s = _EMPTY_PAREN_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip()


def cap_words(text: str, n: int = DEFAULT_CAP_WORDS) -> str:
    """Keep the first n whitespace-delimited words."""
    words = text.split()
    return " ".join(words[:n]) if len(words) > n else text


def prep_body(body: str, headline: str = "", n_words: int = DEFAULT_CAP_WORDS) -> str:
    """clean_body + cap_words in one call — the input the headliners score."""
    return cap_words(clean_body(body, headline), n_words)


def _self_test(argv):
    import argparse
    import csv

    default_tsv = (HERE.parent.parent / "price_stats_loader" / "outputs" /
                   "PRstats_2026-06-20-2026-07-16_CoffeeFilter_11EncScored.tsv")
    ap = argparse.ArgumentParser(description="print before/after for sample rows")
    ap.add_argument("--input", default=str(default_tsv))
    ap.add_argument("--rows", default="0,100,1500,3000",
                    help="comma-separated 0-based data-row indexes")
    args = ap.parse_args(argv)

    with open(args.input, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader)
        rows = list(reader)
    bi, hi = header.index("Body"), header.index("Headline")
    for idx in (int(x) for x in args.rows.split(",")):
        body, headline = rows[idx][bi], rows[idx][hi]
        out = prep_body(body, headline)
        print(f"=== row {idx}  ({len(body.split())} raw words -> {len(out.split())})")
        print(f"RAW : {body[:300]!r}")
        print(f"PREP: {out[:300]!r}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(_self_test(sys.argv[1:]))
