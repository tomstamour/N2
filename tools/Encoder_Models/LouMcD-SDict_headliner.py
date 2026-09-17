#!/usr/bin/env python3
"""
LouMcD-SDict_headliner.py — Headline sentiment via the Loughran-McDonald
Financial Sentiment Dictionary (word-list lexicon, no neural model).

Scores a single PR headline against the official LM Master Dictionary
(1993-2025 edition, sraf.nd.edu). Pure stdlib — no numpy/transformers.
Warm scoring runs in well under 1 ms.

Two-phase usage (CLI):

    # Phase 1 — one-time export (downloads the Master Dictionary CSV,
    # keeps only sentiment-tagged words, caches a compact local JSON)
    python LouMcD-SDict_headliner.py --export

    # Phase 2 — inference on a headline string
    python LouMcD-SDict_headliner.py --headline "Apple reports record quarterly earnings"

    # Inference on a text file (first non-empty line is the headline)
    python LouMcD-SDict_headliner.py --file ./headline-example.txt

Library usage (the hyphenated filename requires importlib):

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "loumcd_headliner", "LouMcD-SDict_headliner.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    result = mod.score_headline("Apple reports record quarterly earnings")

Output notes:
  - "positive"/"negative" (and the 5 other category keys) are matched-WORD
    COUNTS, not probabilities like the neural headliner scripts.
  - "sentiment_score" is LM polarity: (pos - neg) / (pos + neg), 0.0 when
    no sentiment word matches.
  - Category membership in the Master CSV is the year the word was added;
    0 = not in category, negative year = word later removed (not a member).
"""

import argparse
import csv
import json
import logging
import re
import sys
import tempfile
import time
import urllib.request
from datetime import date
from pathlib import Path
from typing import Dict, FrozenSet, Optional, Union

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
DICT_DIR   = SCRIPT_DIR / "loumcd_sdict"
DICT_FILE  = DICT_DIR / "lm_dictionary.json"

# Google Drive file id from https://sraf.nd.edu/loughranmcdonald-master-dictionary/
# ("Loughran-McDonald_MasterDictionary_1993-2025.csv", page updated March 2026)
DRIVE_FILE_ID = "1iq2RUf8qGFEAk1g8wQntP3habOnR3fXF"
CSV_URL = (
    "https://drive.usercontent.google.com/download"
    f"?id={DRIVE_FILE_ID}&export=download&confirm=t"
)
DICT_VERSION = "1993-2025"

# CSV column name → internal category name. These full words stay: they key the
# `counts` / `matched` / `proportions` dicts and read far better in `matched_words`.
CATEGORIES = {
    "Negative":     "negative",
    "Positive":     "positive",
    "Uncertainty":  "uncertainty",
    "Litigious":    "litigious",
    "Strong_Modal": "strong_modal",
    "Weak_Modal":   "weak_modal",
    "Constraining": "constraining",
}

# Internal category name → unified output key (2026-08-09). Applied only to the
# seven flat count keys of the public result; see score_headline.
CATEGORY_KEYS = {
    "negative":     "neg",
    "positive":     "pos",
    "uncertainty":  "unc",
    "litigious":    "lit",
    "strong_modal": "smod",
    "weak_modal":   "wmod",
    "constraining": "cons",
}

TOKEN_RE = re.compile(r"[A-Z]+")

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


# ===========================================================================
# Export — one-time download + compaction of the Master Dictionary
# ===========================================================================
def export_dictionary(dict_file: Path = DICT_FILE) -> bool:
    """Download the LM Master CSV, keep sentiment-tagged words, cache as JSON."""
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            csv_path = Path(temp_dir) / "master_dictionary.csv"
            logger.info(f"Step 1/2 — Downloading Master Dictionary CSV from {CSV_URL}")
            urllib.request.urlretrieve(CSV_URL, csv_path)
            size_mb = csv_path.stat().st_size / 1e6
            logger.info(f"  Downloaded {size_mb:.1f} MB")

            logger.info("Step 2/2 — Extracting sentiment-tagged words…")
            words: Dict[str, list] = {}
            with open(csv_path, newline="") as f:
                reader = csv.DictReader(f)
                missing = [c for c in ("Word", *CATEGORIES) if c not in reader.fieldnames]
                if missing:
                    raise ValueError(f"CSV missing expected columns: {missing}")
                for row in reader:
                    cats = [
                        out_key
                        for col, out_key in CATEGORIES.items()
                        if float(row[col] or 0) > 0
                    ]
                    if cats:
                        words[row["Word"].upper()] = cats

        for out_key in CATEGORIES.values():
            n = sum(1 for cats in words.values() if out_key in cats)
            logger.info(f"  {out_key:>13}: {n} words")
        logger.info(f"  Total sentiment-tagged words: {len(words)}")

        dict_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "meta": {
                "source":   CSV_URL,
                "version":  DICT_VERSION,
                "exported": date.today().isoformat(),
                "words":    len(words),
            },
            "words": words,
        }
        with open(dict_file, "w") as f:
            json.dump(payload, f)
        logger.info(f"Export complete.  Dictionary saved to: {dict_file}")
        return True
    except Exception as e:
        logger.error(f"Export failed: {e}")
        return False


# ===========================================================================
# Module-level singleton — shared across library calls
# ===========================================================================
_dictionary: Optional[Dict[str, FrozenSet[str]]] = None
_dict_file:  Optional[Path] = None


def load_model(dict_file: Path = DICT_FILE) -> None:
    """
    Load the compact LM dictionary into the module singleton.

    Idempotent — subsequent calls with the same dict_file are no-ops.
    Call this explicitly to pre-warm before the first inference call,
    or let score_headline() load it lazily on demand.
    """
    global _dictionary, _dict_file

    if _dictionary is not None and _dict_file == dict_file:
        return  # already loaded

    if not dict_file.exists():
        raise FileNotFoundError(
            f"LM dictionary file not found: {dict_file}\n"
            f"Run the one-time export first:\n"
            f"  python LouMcD-SDict_headliner.py --export"
        )

    logger.info(f"Loading LM dictionary from {dict_file}…")
    with open(dict_file) as f:
        payload = json.load(f)
    _dictionary = {w: frozenset(cats) for w, cats in payload["words"].items()}
    _dict_file = dict_file
    logger.info(f"Dictionary loaded ({payload['meta']['words']} words, "
                f"version {payload['meta']['version']}).")


# ===========================================================================
# Public library API
# ===========================================================================

def score_headline(headline: str, dict_file: Path = DICT_FILE) -> Dict:
    """
    Score one headline against the Loughran-McDonald dictionary.

    Returns:
        {
            "headline":        str,
            "neg":             int,    # matched-word counts per LM category
            "pos":             int,
            "unc":             int,
            "lit":           int,
            "smod":          int,
            "wmod":          int,
            "cons":          int,
            "proportions":   dict,   # count / total_tokens per category
            "matched_words": dict,   # category -> matched words (non-empty only)
            "total_tokens":  int,
            "ss":            float,  # (pos - neg) / (pos + neg), 0.0 if no hits
            "lab":           str,    # positive / negative / neutral
            "ms":            float,  # tokenize + lookup wall time
        }

    The two nested dicts (`proportions`, `matched_words`) keep the FULL category
    names as their inner keys: they are never flattened into columns, and
    `matched_words` reads far better as {"litigious": [...]} than {"lit": [...]}.
    """
    load_model(dict_file)

    t0 = time.perf_counter()
    tokens = TOKEN_RE.findall(headline.upper())

    counts  = {key: 0 for key in CATEGORIES.values()}
    matched = {key: [] for key in CATEGORIES.values()}
    for token in tokens:
        for cat in _dictionary.get(token, ()):
            counts[cat] += 1
            matched[cat].append(token)

    pos, neg = counts["positive"], counts["negative"]
    sentiment_score = (pos - neg) / (pos + neg) if (pos + neg) > 0 else 0.0
    if pos > neg:
        label = "positive"
    elif neg > pos:
        label = "negative"
    else:
        label = "neutral"
    elapsed_ms = (time.perf_counter() - t0) * 1000.0

    total = len(tokens)
    # Unified 2026-08-09 key names (see tools/Encoder_Models/CLAUDE.md). Only the
    # seven flat COUNT keys are shortened, via CATEGORY_KEYS; the nested dicts below
    # stay keyed by full category name because they never become columns.
    result: Dict = {"headline": headline}
    result.update({CATEGORY_KEYS[k]: c for k, c in counts.items()})
    result["proportions"]   = {k: round(c / total, 4) if total else 0.0
                               for k, c in counts.items()}
    result["matched_words"] = {k: v for k, v in matched.items() if v}
    result["total_tokens"]  = total
    result["ss"]            = round(sentiment_score, 4)
    result["lab"]           = label
    result["ms"]            = round(elapsed_ms, 3)
    return result


def score_headline_file(path: Union[str, Path], dict_file: Path = DICT_FILE) -> Dict:
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

    result = score_headline(headline, dict_file)
    result["source_file"] = str(path)
    return result


# ===========================================================================
# CLI
# ===========================================================================
def _cli_run(args) -> bool:
    """Execute CLI logic.  Returns True on success."""
    if args.export:
        return export_dictionary()

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
        description="Headline sentiment via the Loughran-McDonald dictionary",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # One-time dictionary export
  python LouMcD-SDict_headliner.py --export

  # Score a headline string directly
  python LouMcD-SDict_headliner.py --headline "Apple reports record quarterly earnings"

  # Score the first non-empty line of a text file
  python LouMcD-SDict_headliner.py --file ./headline-example.txt
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--export",
        action="store_true",
        help="One-time export: download the LM Master Dictionary CSV → compact local JSON",
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
