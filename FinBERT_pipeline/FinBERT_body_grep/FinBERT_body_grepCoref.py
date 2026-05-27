#!/usr/bin/env python3
"""
FinBERT_body_grepCoref.py - FinBERT sentiment pipeline for the `article_body`
field of news JSON files. Coreference resolution is handled by a lightweight
grep-based pass: a configurable list of pronoun/reference strings is replaced
with the primary entity name before sentence splitting so NER and FinBERT see
entity-linked text throughout.

Pipeline (in order):
    1. jsonCleaner       - HTML/entity/whitespace/URL/ticker scrub
    2. grepCoref         - pronoun-string → entity-name substitution (grep list)
    3. SentenceSplitter  - sentence boundary detection (spaCy)
    4. NerSecDicCreator  - SEC-EDGAR ticker resolution NER
    5. FinBERT-analysis  - entity-targeted FinBERT (ONNX INT8) sentiment

All sibling-script references use local (./) paths only. A local FinBERT/
directory (or symlink) must exist next to this script.

Use FinBERT_body_noCoref.py when no coreference resolution at all is required.
"""

import argparse
import importlib.util
import json
import logging
import logging.handlers
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

# --- Locate sibling scripts on disk (local paths only) ----------------------
_THIS_FILE       = Path(__file__).resolve()
_THIS_DIR        = _THIS_FILE.parent
_JSONCLEANER_DIR = _THIS_DIR / "jsonCleaner"
_SPLITTER_DIR    = _THIS_DIR / "SentenceSplitter"
_NER_DIR         = _THIS_DIR / "NerSecDictionary"
_FINBERT_DIR     = _THIS_DIR / "FinBERT"

for _p in (_JSONCLEANER_DIR, _SPLITTER_DIR, _NER_DIR, _FINBERT_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# --- Imports from sibling scripts -------------------------------------------
from jsonCleaner import TextCleaner
from NerSecDicCreator import TickerResolver, NERProcessor

import spacy

_finbert_mod = None
FinBERTInferencer = None
EntityDeduplicator = None
TextSubstitutor = None
SentimentAggregator = None
DEFAULT_FINBERT_MODEL_DIR = _FINBERT_DIR / "finbert_onnx"
HUGGINGFACE_MODEL = "ProsusAI/finbert"


def _import_finbert_module():
    global _finbert_mod, FinBERTInferencer, EntityDeduplicator
    global TextSubstitutor, SentimentAggregator
    global DEFAULT_FINBERT_MODEL_DIR, HUGGINGFACE_MODEL
    if _finbert_mod is not None:
        return
    spec = importlib.util.spec_from_file_location(
        "finbert_analysis", _FINBERT_DIR / "FinBERT-analysis.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _finbert_mod = mod
    FinBERTInferencer    = mod.FinBERTInferencer
    EntityDeduplicator   = mod.EntityDeduplicator
    TextSubstitutor      = mod.TextSubstitutor
    SentimentAggregator  = mod.SentimentAggregator
    DEFAULT_FINBERT_MODEL_DIR = mod.MODEL_DIR
    HUGGINGFACE_MODEL    = mod.HUGGINGFACE_MODEL


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("FinBERT_body_grepCoref")


FIELD_NAME = "article_body"


class FinBERTBodyPipeline:
    """End-to-end pipeline (grep coref) for sentiment analysis of an article_body field.

    Typical usage in a long-running WebSocket service:

        pipeline = FinBERTBodyPipeline()
        pipeline.load_models()                # heavy, do once at startup
        result = pipeline.process(article_dict)   # fast, per article
    """

    _MERGE_ABBREVS = frozenset({
        "Inc.", "Corp.", "Ltd.", "LLC.", "Co.", "L.P.", "N.A.", "P.C.", "P.A.",
        "Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "St.", "Jr.", "Sr.", "vs.",
    })

    def __init__(
        self,
        output_dir: Union[str, Path] = "./finBERT_outputs",
        write_outputs: bool = True,
        log_source: bool = True,
        finbert_model_dir: Optional[Union[str, Path]] = None,
        log_file: Optional[Union[str, Path]] = None,
        sentences_to_analyse: Optional[int] = None,
        grep_strings_file: Optional[Union[str, Path]] = "coreference_grep_strings.txt",
    ):
        self.output_dir = Path(output_dir)
        self.write_outputs = write_outputs
        self.log_source = log_source
        self.finbert_model_dir = Path(finbert_model_dir) if finbert_model_dir else DEFAULT_FINBERT_MODEL_DIR
        self.sentences_to_analyse = sentences_to_analyse

        self.grep_strings_file: Optional[Path] = (
            Path(grep_strings_file) if grep_strings_file else None
        )
        self.grep_strings: List[str] = []

        self.spacy_nlp: Optional[spacy.Language] = None
        self.ticker_resolver: Optional[TickerResolver] = None
        self.finbert = None
        self._loaded = False

        self._last_timings: List[Dict] = []

        self._log_file_dir: Optional[Path] = None
        self._log_mem_handler: Optional[logging.handlers.MemoryHandler] = None
        if log_file is not None:
            p = Path(log_file)
            if p.is_dir():
                self._log_file_dir = p
                self._log_mem_handler = logging.handlers.MemoryHandler(
                    capacity=10_000,
                    flushLevel=logging.CRITICAL + 1,
                )
                logger.addHandler(self._log_mem_handler)
            else:
                self._attach_file_handler(p)

    @staticmethod
    def _attach_file_handler(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler) and Path(h.baseFilename) == path.resolve():
                return
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setLevel(logging.INFO)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        logger.addHandler(fh)
        logger.info(f"Pipeline logs also writing to {path}")

    @contextmanager
    def _step_timer(self, step_name: str, **size_metrics):
        t0 = time.perf_counter()
        try:
            yield
        finally:
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            entry = {"step": step_name, "elapsed_ms": elapsed_ms, **size_metrics}
            self._last_timings.append(entry)
            metrics_str = " ".join(f"{k}={v}" for k, v in size_metrics.items())
            logger.info(
                f"step={step_name} elapsed_ms={elapsed_ms}"
                + (f" {metrics_str}" if metrics_str else "")
            )

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def load_models(self) -> None:
        """One-time load of every heavy model used by the pipeline."""
        t0 = time.time()
        logger.info("Loading models (grep-coref variant)...")

        logger.info("  Loading spaCy en_core_web_sm...")
        self.spacy_nlp = spacy.load("en_core_web_sm")

        logger.info("  Loading SEC EDGAR TickerResolver (cache or rebuild)...")
        self.ticker_resolver = TickerResolver()

        if self.grep_strings_file is not None:
            gf = self.grep_strings_file
            if not gf.is_absolute():
                gf = _THIS_DIR / gf
            self.grep_strings = self._load_grep_strings(gf)
            logger.info(f"  Loaded {len(self.grep_strings)} grep-coref patterns from {gf}")

        logger.info(f"  Loading FinBERT ONNX model from {self.finbert_model_dir}...")
        _import_finbert_module()
        if not self.finbert_model_dir.exists():
            raise FileNotFoundError(
                f"FinBERT ONNX model directory not found: {self.finbert_model_dir}\n"
                f"Run the one-time export first:\n"
                f"  python {_FINBERT_DIR / 'FinBERT-analysis.py'} --export"
            )
        self.finbert = FinBERTInferencer(self.finbert_model_dir)
        self.finbert.load()

        self._loaded = True
        logger.info(f"Models loaded in {time.time() - t0:.2f}s.")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        if getattr(self, "_dump_thread", None) and self._dump_thread.is_alive():
            self._dump_thread.join()

        if self.ticker_resolver is not None:
            try:
                self.ticker_resolver.save_cache()
            except Exception as e:
                logger.error(f"shutdown: failed to save ticker resolver cache: {e}")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def process(
        self,
        article: Union[Dict, str, Path],
        write_outputs: Optional[bool] = None,
        sentences_to_analyse: Optional[int] = None,
    ) -> Dict:
        """Run the pipeline (grep coref) on one article.

        Args:
            article: Either a dict (e.g. WebSocket payload) or a path to a JSON file.
                     The dict must contain an "article_body" key.
            write_outputs: Per-call override of self.write_outputs.
            sentences_to_analyse: Per-call override of self.sentences_to_analyse.

        Returns:
            dict with the in-memory output of every step:
                {
                    "cleaned":   {"article_body": str},
                    "grepcoref": {"article_body": str, "metadata": {...}},
                    "sentences": {"metadata": {...}, "sentences": [...]},
                    "ner":       {"metadata": {...}, "sentences": [...]},
                    "finbert":   {"metadata": {...}, "ticker_sentiments": {...}},
                    "stem":      str,
                    "timings":   [...],
                }
        """
        if not self._loaded:
            raise RuntimeError("Pipeline models not loaded. Call load_models() first.")

        article_dict, source_path = self._resolve_input(article)
        if FIELD_NAME not in article_dict or not article_dict[FIELD_NAME]:
            raise ValueError(f"Input is missing or empty '{FIELD_NAME}' field")

        stem = self._derive_stem(article_dict, source_path)
        if self._log_file_dir is not None and self._log_mem_handler is not None:
            log_path = (self._log_file_dir / f"{stem}_pipeline.log").resolve()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            fh = logging.FileHandler(log_path, encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
            self._log_mem_handler.setTarget(fh)
            self._log_mem_handler.flush()
            logger.removeHandler(self._log_mem_handler)
            self._log_mem_handler = None
            logger.addHandler(fh)
            logger.info(f"Pipeline logs also writing to {log_path}")
        do_write = self.write_outputs if write_outputs is None else write_outputs
        effective_sentences = sentences_to_analyse if sentences_to_analyse is not None else self.sentences_to_analyse

        self._last_timings = []
        t_pipeline = time.perf_counter()
        logger.info(f"Processing article (stem={stem})...")

        allowed_tickers = set(article_dict.get("tickers") or [])

        raw_body = article_dict[FIELD_NAME]
        with self._step_timer("clean", chars_in=len(raw_body)):
            cleaned = self._step_clean(raw_body)
        self._last_timings[-1]["chars_out"] = len(cleaned[FIELD_NAME])

        with self._step_timer("grepcoref"):
            entity_name = self._resolve_entity_name(article_dict)
            grepcoref = self._step_grep_coref(cleaned[FIELD_NAME], entity_name)
        self._last_timings[-1]["replacements_made"] = grepcoref["metadata"]["replacements_made"]

        split_input = (
            self._truncate_to_n_sentences(grepcoref[FIELD_NAME], effective_sentences)
            if effective_sentences is not None
            else grepcoref[FIELD_NAME]
        )

        with self._step_timer("split"):
            sentences = self._step_split(split_input, source_file=stem,
                                         sentences_to_analyse=effective_sentences)
        self._last_timings[-1]["n_sentences"] = len(sentences.get("sentences", []))

        with self._step_timer("ner"):
            ner = self._step_ner(sentences, source_file=stem)
        ner_meta = ner.get("metadata", {})
        self._last_timings[-1].update({
            "n_sentences":       ner_meta.get("total_sentences", 0),
            "n_entities":        ner_meta.get("total_entities", 0),
            "n_unique_tickers":  len(ner_meta.get("unique_tickers", [])),
        })

        with self._step_timer("finbert"):
            finbert = self._step_finbert(ner, source_file=stem, allowed_tickers=allowed_tickers)
        fb_meta = finbert.get("metadata", {})
        self._last_timings[-1].update({
            "n_sentences_with_entities": fb_meta.get("total_sentences_processed", 0),
            "n_inferences":              fb_meta.get("total_ticker_sentiments", 0),
        })

        result = {
            "cleaned":   cleaned,
            "grepcoref": grepcoref,
            "sentences": sentences,
            "ner":       ner,
            "finbert":   finbert,
            "stem":      stem,
            "timings":   self._last_timings,
        }

        if do_write:
            self._dump_thread = threading.Thread(target=self._dump, args=(stem, result), daemon=False)
            self._dump_thread.start()

        total_ms = round((time.perf_counter() - t_pipeline) * 1000.0, 2)
        breakdown = ",".join(f"{t['step']}:{t['elapsed_ms']}" for t in self._last_timings)
        logger.info(f"Pipeline done total_ms={total_ms} breakdown={breakdown}")
        return result

    # ------------------------------------------------------------------
    # Input resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_input(article: Union[Dict, str, Path]):
        if isinstance(article, dict):
            return article, None
        path = Path(article)
        if not path.exists():
            raise FileNotFoundError(f"Input file not found: {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), path

    @staticmethod
    def _derive_stem(article_dict: Dict, source_path: Optional[Path]) -> str:
        if source_path is not None:
            return source_path.stem

        ticker = article_dict.get("ticker")
        created = article_dict.get("created")
        if ticker and created:
            date_part = created.split("T", 1)[0]
            return f"{ticker}-{date_part}"

        return f"article-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # ------------------------------------------------------------------
    # Step 1: jsonCleaner
    # ------------------------------------------------------------------
    @staticmethod
    def _step_clean(text: str) -> Dict[str, str]:
        return {FIELD_NAME: TextCleaner.clean(text)}

    # ------------------------------------------------------------------
    # Step 2: grep-based coreference
    # ------------------------------------------------------------------
    @staticmethod
    def _load_grep_strings(path: Path) -> List[str]:
        """Load pronoun/string patterns from file. One per line; surrounding quotes stripped."""
        patterns = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if (line.startswith('"') and line.endswith('"')) or \
                   (line.startswith("'") and line.endswith("'")):
                    line = line[1:-1]
                patterns.append(line)
        return patterns

    def _resolve_entity_name(self, article_dict: Dict) -> Optional[str]:
        """Return the primary entity's official SEC name (falls back to ticker symbol)."""
        ticker = article_dict.get("ticker")
        if not ticker:
            tickers = article_dict.get("tickers") or []
            ticker = tickers[0] if tickers else None
        if not ticker:
            return None
        info = self.ticker_resolver.ticker_to_info.get(ticker.upper())
        if info and info.get("official_name"):
            return info["official_name"].title()
        return ticker

    def _step_grep_coref(self, text: str, entity_name: Optional[str]) -> Dict:
        """Replace loaded pronoun/reference patterns with entity_name."""
        if not self.grep_strings or not entity_name:
            return {
                FIELD_NAME: text,
                "metadata": {
                    "entity_name":      entity_name,
                    "patterns_file":    str(self.grep_strings_file),
                    "patterns_loaded":  len(self.grep_strings),
                    "replacements_made": 0,
                    "applied_patterns": [],
                },
            }

        result = text
        applied: List[Dict] = []
        for pattern in self.grep_strings:
            count = result.count(pattern)
            if count:
                if pattern.startswith(" ") and pattern.endswith(" "):
                    replacement = f" {entity_name} "
                elif pattern.startswith(" "):
                    replacement = f" {entity_name}"
                elif pattern.endswith(" "):
                    replacement = f"{entity_name} "
                else:
                    replacement = entity_name
                result = result.replace(pattern, replacement)
                applied.append({"pattern": pattern, "count": count})

        return {
            FIELD_NAME: result,
            "metadata": {
                "entity_name":       entity_name,
                "patterns_file":     str(self.grep_strings_file),
                "patterns_loaded":   len(self.grep_strings),
                "replacements_made": sum(a["count"] for a in applied),
                "applied_patterns":  applied,
            },
        }

    # ------------------------------------------------------------------
    # Helper: pre-truncate to N sentences before split
    # ------------------------------------------------------------------
    def _truncate_to_n_sentences(self, text: str, n: int) -> str:
        """Return text sliced to the end of the nth spaCy sentence."""
        doc = self.spacy_nlp(text)
        sents = list(doc.sents)
        if len(sents) <= n:
            return text
        return text[:sents[n - 1].end_char]

    # ------------------------------------------------------------------
    # Step 3: sentence segmentation
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_abbreviated_fragments(sents) -> list:
        """Merge spans incorrectly split after abbreviations like 'Inc.', 'Corp.'."""
        sents = list(sents)
        merged = []
        i = 0
        while i < len(sents):
            span = sents[i]
            while i + 1 < len(sents):
                last_word = span.text.rstrip().rsplit(None, 1)[-1] if span.text.strip() else ""
                if last_word not in FinBERTBodyPipeline._MERGE_ABBREVS:
                    break
                span = span.doc[span.start : sents[i + 1].end]
                i += 1
            merged.append(span)
            i += 1
        return merged

    def _step_split(self, text: str, source_file: str,
                    sentences_to_analyse: Optional[int] = None) -> Dict:
        sentences: List[Dict] = []
        if text.strip():
            doc = self.spacy_nlp(text)
            for idx, sent in enumerate(self._merge_abbreviated_fragments(doc.sents)):
                sentences.append({
                    "id":         idx,
                    "text":       sent.text,
                    "source":     FIELD_NAME,
                    "char_start": sent.start_char,
                    "char_end":   sent.end_char,
                })

        original_count = len(sentences)
        if sentences_to_analyse is not None:
            sentences = sentences[:sentences_to_analyse]

        metadata = {
            "input_file":       f"{source_file}_grepcoref.json",
            "total_sentences":  len(sentences),
            "source_counts":    {FIELD_NAME: len(sentences)},
            "processed_fields": [FIELD_NAME] if sentences else [],
            "missing_fields":   [] if sentences else [FIELD_NAME],
        }
        if sentences_to_analyse is not None and original_count > len(sentences):
            metadata["sentences_truncated_to"] = sentences_to_analyse
        return {"metadata": metadata, "sentences": sentences}

    # ------------------------------------------------------------------
    # Step 4: NER + ticker resolution
    # ------------------------------------------------------------------
    def _step_ner(self, sentences_block: Dict, source_file: str) -> Dict:
        sentences = sentences_block.get("sentences", [])
        processor = NERProcessor(self.ticker_resolver)
        processed = processor.process_all_sentences(sentences)

        all_entities = [e for s in processed for e in s.get("entities", [])]
        unique_tickers = sorted({e["ticker"] for e in all_entities if e.get("ticker")})

        metadata = {
            "input_file":       f"{source_file}_sentences.json",
            "total_sentences":  len(sentences),
            "total_entities":   len(all_entities),
            "unique_tickers":   unique_tickers,
        }
        return {"metadata": metadata, "sentences": processed}

    # ------------------------------------------------------------------
    # Step 5: FinBERT inference
    # ------------------------------------------------------------------
    def _step_finbert(self, ner_block: Dict, source_file: str,
                      allowed_tickers: Optional[set] = None) -> Dict:
        sentences = ner_block.get("sentences", [])
        aggregator = SentimentAggregator()
        skipped: List[int] = []
        processed = 0

        for sentence in sentences:
            sent_id = sentence["id"]
            text    = sentence.get("text", "")

            if not text.strip():
                skipped.append(sent_id)
                continue

            deduped = EntityDeduplicator.deduplicate(sentence.get("entities", []))
            if not deduped:
                skipped.append(sent_id)
                continue

            processed += 1
            scored_deduped = (
                {t: ents for t, ents in deduped.items() if t in allowed_tickers}
                if allowed_tickers else deduped
            )
            if scored_deduped:
                tickers_to_score = scored_deduped
                use_substitution = True
            else:
                tickers_to_score = allowed_tickers
                use_substitution = False
            for target_ticker in tickers_to_score:
                targeted_text = (
                    TextSubstitutor.substitute(text, scored_deduped, target_ticker)
                    if use_substitution else text
                )
                scores = self.finbert.predict(targeted_text)
                aggregator.add_sentence_result(target_ticker, {
                    "sentence_id":   sent_id,
                    "source":        sentence.get("source", FIELD_NAME),
                    "original_text": text,
                    "targeted_text": targeted_text,
                    **scores,
                })

        ticker_sentiments = aggregator.build_output()
        total_inferences  = sum(b["sentence_count"] for b in ticker_sentiments.values())

        metadata = {
            "input_file":                f"{source_file}_NER.json",
            "model":                     HUGGINGFACE_MODEL,
            "inference_engine":          "ONNX Runtime (INT8 quantized)",
            "total_sentences_processed": processed,
            "skipped_sentences":         skipped,
            "total_ticker_sentiments":   total_inferences,
            "unique_tickers":            sorted(ticker_sentiments.keys()),
        }
        return {"metadata": metadata, "ticker_sentiments": ticker_sentiments}

    # ------------------------------------------------------------------
    # Disk dump
    # ------------------------------------------------------------------
    def _dump(self, stem: str, result: Dict) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            f"{stem}_cleaned.json":    result["cleaned"],
            f"{stem}_grepcoref.json":  result["grepcoref"],
            f"{stem}_sentences.json":  result["sentences"],
            f"{stem}_NER.json":        result["ner"],
            f"{stem}_FinBERT.json":    result["finbert"],
        }
        for name, payload in files.items():
            path = self.output_dir / name
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, ensure_ascii=False)
            logger.info(f"  wrote {path}")

        if self.log_source:
            src_path = self.output_dir / f"{stem}_pipeline_source.py"
            src_path.write_text(_THIS_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            logger.info(f"  wrote {src_path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="FinBERT body pipeline (grep coref): clean -> grepCoref -> split -> NER -> FinBERT"
    )
    parser.add_argument("--input", required=True, help="Path to a JSON file with an 'article_body' field")
    parser.add_argument("--output-dir", default="./finBERT_outputs", help="Directory for intermediate + final outputs")
    parser.add_argument("--no-write", action="store_true", help="Skip writing intermediate files to disk")
    parser.add_argument("--no-log-source", action="store_true", help="Skip writing a verbatim copy of this script to output_dir")
    parser.add_argument("--finbert-model-dir", default=None, help="Override FinBERT ONNX model directory")
    parser.add_argument("--log-file", default=None, help="Optional file or directory to tee per-step timing logs to")
    parser.add_argument(
        "--sentences-to-analyse", type=int, default=None, metavar="N",
        help="Process only the first N sentences after spaCy splitting (default: all).",
    )
    parser.add_argument(
        "--strings-to-grep",
        default="coreference_grep_strings.txt",
        metavar="FILE",
        help="File with one pronoun/string pattern per line to replace with the primary "
             "entity name before sentence splitting (default: coreference_grep_strings.txt "
             "next to this script). Pass empty string to disable grep coref.",
    )
    args = parser.parse_args()

    pipeline = FinBERTBodyPipeline(
        output_dir=args.output_dir,
        write_outputs=not args.no_write,
        log_source=not args.no_log_source,
        finbert_model_dir=args.finbert_model_dir,
        log_file=args.log_file,
        sentences_to_analyse=args.sentences_to_analyse,
        grep_strings_file=args.strings_to_grep or None,
    )
    pipeline.load_models()
    result = pipeline.process(args.input)

    summary = result["finbert"]["metadata"]
    gc_meta = result["grepcoref"]["metadata"]
    print(json.dumps({
        "stem":                     result["stem"],
        "grep_coref_entity":        gc_meta.get("entity_name"),
        "grep_coref_replacements":  gc_meta.get("replacements_made", 0),
        "unique_tickers":           summary["unique_tickers"],
        "total_ticker_sentiments":  summary["total_ticker_sentiments"],
        "skipped_sentences":        summary["skipped_sentences"],
    }, indent=2))

    pipeline.shutdown()


if __name__ == "__main__":
    main()
