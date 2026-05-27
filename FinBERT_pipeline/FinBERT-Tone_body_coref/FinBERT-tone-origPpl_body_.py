#!/usr/bin/env python3
"""
FinBERT-tone-origPpl_body_.py - Financial sentiment pipeline using the original
HuggingFace pipeline API for yiyanghkust/finbert-tone.

Pipeline:
    1. Load article JSON (article_body + ticker fields)
    2. Split article_body into sentences via spaCy en_core_web_sm
    3. Score each sentence with transformers pipeline("text-classification")
    4. Aggregate all sentences under the primary ticker
    5. Write output in the same per-ticker schema as FinBERT-tone_body_coref.py

No coreference resolution, no NER, no ONNX — uses the model exactly as shown
on the yiyanghkust/finbert-tone HuggingFace model card.
"""

import argparse
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Union

import spacy
from transformers import BertForSequenceClassification, BertTokenizer, pipeline

HUGGINGFACE_MODEL = "yiyanghkust/finbert-tone"
SENTIMENT_THRESHOLD = 0.05
FIELD_NAME = "article_body"

_THIS_FILE = Path(__file__).resolve()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("FinBERT_tone_origPpl")


def _label_from_score(score: float) -> str:
    if score > SENTIMENT_THRESHOLD:
        return "positive"
    if score < -SENTIMENT_THRESHOLD:
        return "negative"
    return "neutral"


class FinBERTOrigPplPipeline:
    """Sentiment pipeline using the stock HuggingFace text-classification pipeline.

    Simpler than FinBERTBodyPipeline: no coref, no NER, no ONNX.
    Attribution is to the single primary 'ticker' in the input JSON.

    Typical usage:
        pipeline = FinBERTOrigPplPipeline()
        pipeline.load_models()
        result = pipeline.process(article_dict)
    """

    def __init__(
        self,
        output_dir: Union[str, Path] = "./finBERT_outputs",
        write_outputs: bool = True,
        log_source: bool = True,
        device: Optional[str] = None,
        sentences_to_analyse: Optional[int] = None,
    ):
        self.output_dir = Path(output_dir)
        self.write_outputs = write_outputs
        self.log_source = log_source
        self.sentences_to_analyse = sentences_to_analyse

        # Resolve device: "cuda" -> 0, "cpu" -> -1, None -> -1
        if device == "cuda":
            self._device = 0
        else:
            self._device = -1

        self.spacy_nlp: Optional[spacy.Language] = None
        self._hf_pipeline = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Model loading
    # ------------------------------------------------------------------
    def load_models(self) -> None:
        t0 = time.time()
        logger.info("Loading models (original HF pipeline — yiyanghkust/finbert-tone)...")

        logger.info("  Loading spaCy en_core_web_sm...")
        self.spacy_nlp = spacy.load("en_core_web_sm")

        logger.info(f"  Loading yiyanghkust/finbert-tone from HuggingFace (device={self._device})...")
        tokenizer = BertTokenizer.from_pretrained(HUGGINGFACE_MODEL)
        model = BertForSequenceClassification.from_pretrained(HUGGINGFACE_MODEL, num_labels=3)
        self._hf_pipeline = pipeline(
            "text-classification",
            model=model,
            tokenizer=tokenizer,
            top_k=None,
            device=self._device,
        )

        self._loaded = True
        logger.info(f"Models loaded in {time.time() - t0:.2f}s.")

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------
    def process(
        self,
        article: Union[Dict, str, Path],
        write_outputs: Optional[bool] = None,
        sentences_to_analyse: Optional[int] = None,
    ) -> Dict:
        """Run the pipeline on one article.

        Args:
            article: Dict with 'article_body' (and 'ticker') fields, or path to JSON.
            write_outputs: Per-call override of self.write_outputs.
            sentences_to_analyse: Per-call override of self.sentences_to_analyse.

        Returns:
            dict with keys: "sentences", "finbert", "stem"
        """
        if not self._loaded:
            raise RuntimeError("Call load_models() first.")

        article_dict, source_path = self._resolve_input(article)
        if FIELD_NAME not in article_dict or not article_dict[FIELD_NAME]:
            raise ValueError(f"Input is missing or empty '{FIELD_NAME}' field")

        ticker = article_dict.get("ticker", "UNKNOWN")
        stem = self._derive_stem(article_dict, source_path)
        do_write = self.write_outputs if write_outputs is None else write_outputs
        effective_n = sentences_to_analyse if sentences_to_analyse is not None else self.sentences_to_analyse

        t_pipeline = time.perf_counter()
        logger.info(f"Processing article (stem={stem}, ticker={ticker})...")

        sentences = self._split_sentences(article_dict[FIELD_NAME], effective_n)
        logger.info(f"  split: {len(sentences)} sentences")

        sentence_results = self._score_sentences(sentences)
        logger.info(f"  scored {len(sentence_results)} sentences")

        finbert_output = self._aggregate(ticker, sentence_results, stem)

        result = {
            "sentences": [
                {"id": i, "text": s, "source": FIELD_NAME}
                for i, s in enumerate(sentences)
            ],
            "finbert": finbert_output,
            "stem": stem,
        }

        if do_write:
            self._dump_thread = threading.Thread(
                target=self._dump, args=(stem, result), daemon=False
            )
            self._dump_thread.start()

        total_ms = round((time.perf_counter() - t_pipeline) * 1000.0, 2)
        logger.info(f"Pipeline done total_ms={total_ms}")
        return result

    def shutdown(self) -> None:
        if getattr(self, "_dump_thread", None) and self._dump_thread.is_alive():
            self._dump_thread.join()

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
            return f"{ticker}-{created.split('T', 1)[0]}"
        return f"article-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # ------------------------------------------------------------------
    # Step 1: sentence splitting
    # ------------------------------------------------------------------
    def _split_sentences(self, text: str, n: Optional[int] = None) -> List[str]:
        doc = self.spacy_nlp(text)
        sents = [sent.text.strip() for sent in doc.sents if sent.text.strip()]
        if n is not None:
            sents = sents[:n]
        return sents

    # ------------------------------------------------------------------
    # Step 2: HuggingFace pipeline inference
    # ------------------------------------------------------------------
    def _score_sentences(self, sentences: List[str]) -> List[Dict]:
        results = []
        for i, sent in enumerate(sentences):
            raw = self._hf_pipeline(sent)[0]  # list of {label, score}
            score_map = {item["label"].lower(): item["score"] for item in raw}
            pos = score_map.get("positive", 0.0)
            neg = score_map.get("negative", 0.0)
            neu = score_map.get("neutral", 0.0)
            sentiment_score = round(pos - neg, 6)
            results.append({
                "sentence_id":    i,
                "source":         FIELD_NAME,
                "original_text":  sent,
                "positive":       round(pos, 6),
                "negative":       round(neg, 6),
                "neutral":        round(neu, 6),
                "sentiment_score": sentiment_score,
                "label":          _label_from_score(sentiment_score),
            })
        return results

    # ------------------------------------------------------------------
    # Step 3: aggregation
    # ------------------------------------------------------------------
    @staticmethod
    def _aggregate(ticker: str, sentence_results: List[Dict], stem: str) -> Dict:
        if sentence_results:
            mean_score = round(
                sum(r["sentiment_score"] for r in sentence_results) / len(sentence_results),
                6,
            )
            ticker_sentiments = {
                ticker: {
                    "overall_sentiment_score": mean_score,
                    "overall_label":           _label_from_score(mean_score),
                    "sentence_count":          len(sentence_results),
                    "sentences":               sentence_results,
                }
            }
            unique_tickers = [ticker]
        else:
            ticker_sentiments = {}
            unique_tickers = []

        metadata = {
            "input_file":                stem,
            "model":                     HUGGINGFACE_MODEL,
            "inference_engine":          "HuggingFace transformers pipeline",
            "total_sentences_processed": len(sentence_results),
            "unique_tickers":            unique_tickers,
        }
        return {"metadata": metadata, "ticker_sentiments": ticker_sentiments}

    # ------------------------------------------------------------------
    # Disk dump
    # ------------------------------------------------------------------
    def _dump(self, stem: str, result: Dict) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        files = {
            f"{stem}_sentences.json":             {"sentences": result["sentences"]},
            f"{stem}_FinBERT-tone-origPpl.json":  result["finbert"],
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
        description=(
            "FinBERT-tone original HF pipeline: split -> score -> aggregate. "
            "Uses transformers pipeline('text-classification') directly, no ONNX, no NER."
        )
    )
    parser.add_argument("--input", required=True, help="Path to a JSON file with an 'article_body' field")
    parser.add_argument("--output-dir", default="./finBERT_outputs", help="Directory for output files")
    parser.add_argument("--no-write", action="store_true", help="Skip writing output files to disk")
    parser.add_argument("--no-log-source", action="store_true", help="Skip copying this script to output_dir")
    parser.add_argument(
        "--device", choices=["cpu", "cuda"], default="cpu",
        help="Device for HuggingFace pipeline. Default: cpu.",
    )
    parser.add_argument(
        "--sentences-to-analyse", type=int, default=None, metavar="N",
        help="Process only the first N sentences.",
    )
    args = parser.parse_args()

    pipe = FinBERTOrigPplPipeline(
        output_dir=args.output_dir,
        write_outputs=not args.no_write,
        log_source=not args.no_log_source,
        device=args.device,
        sentences_to_analyse=args.sentences_to_analyse,
    )
    pipe.load_models()
    result = pipe.process(args.input)

    summary = result["finbert"]["metadata"]
    print(json.dumps({
        "stem":                     result["stem"],
        "unique_tickers":           summary["unique_tickers"],
        "total_sentences_processed": summary["total_sentences_processed"],
        "ticker_sentiments": {
            t: {
                "overall_label":          v["overall_label"],
                "overall_sentiment_score": v["overall_sentiment_score"],
                "sentence_count":          v["sentence_count"],
            }
            for t, v in result["finbert"]["ticker_sentiments"].items()
        },
    }, indent=2))

    pipe.shutdown()


if __name__ == "__main__":
    main()
