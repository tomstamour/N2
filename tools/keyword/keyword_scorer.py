#!/usr/bin/env python3
"""
keyword_scorer.py

Score a financial PR text on a 0-1 scale from a keyword table produced by
keyword_finder.py (see keyword_analysis_report_04-07-2026.md).

Scoring methods (each becomes one output column, so they can be compared):

    kw_add_first  ADDITIVE log-odds, first occurrence only (presence).
                  score = sigmoid( sum coef_i over distinct matched terms ).
                  NOTE: adding logit coefs IS multiplying odds ratios --
                  exp(b1+b2) = OR1*OR2 -- so this is also "the" principled
                  multiplicative method, just computed on the log scale.
    kw_add_log    ADDITIVE log-odds, occurrences log-damped: weight 1+ln(count)
                  (mirrors sublinear_tf of the winning TF-IDF model).
    kw_add_lin    ADDITIVE log-odds, occurrences counted linearly (weight=count).
    kw_mult_nb    MULTIPLICATIVE naive-Bayes on MARGINAL per-term odds:
                  posterior_odds = prior_odds * prod_i [marginal odds shift of
                  term i alone].  Unlike the additive methods (whose coefs are
                  conditional on all other words), this double-counts
                  correlated evidence -- included so add-vs-multiply can be
                  settled empirically.  Per-term P(big|term) is recovered from
                  the table's univariate_r / document_frequency_pct.
    model_proba   Gold standard: predict_proba of the fitted sklearn pipeline
                  (tfidf_fitted_pipeline.joblib) -- the 0.78-AUC model itself.

All scores are in (0,1).  For kw_add_* 0.5 = neutral (no net keyword
evidence); model_proba/kw_mult_nb are anchored near the training prevalence.
Scores are RANKING scores, not calibrated probabilities (the model was
trained with class_weight='balanced').  If a reference file exists
(built by --evaluate), each method also gets a <method>_pctl column =
fraction of the historical corpus scoring below it.

Text preprocessing mirrors training: lowercase, wire-boilerplate stripping
(URLs / newswire vendor tokens), token pattern \\b[a-zA-Z]{2,}\\b, unigrams +
bigrams.  Raw Orchestrator5 Body cells (HTML tags/entities from the RTPR
payload) are normalized first: tags stripped + entities decoded when the text
looks like HTML.  A boilerplate_category column (lawfirm/compliance/earnings/
conference, or empty) flags PR types the model never saw; gate on it
downstream.

--words-detail true additionally writes <input>_words_detail.tsv: per PR row
the top 10 positive + top 10 negative keyword contributors, with each term's
occurrence count, its power (coefficient from the input keyword table) and
its contribution = power * (1+ln count) -- the exact kw_add_log summand.

CLI:
    # score a TSV (appends columns, writes <input>_scored.tsv)
    /home/tom/venv/bin/python keyword_scorer.py --input file.tsv --text-col Body

    # back-test every method on the historical file (exact test split of the
    # report) and (re)build the percentile reference
    /home/tom/venv/bin/python keyword_scorer.py --evaluate

Module:
    from keyword_scorer import get_default_scorer
    scorer = get_default_scorer()
    scores = scorer.score_text(body_text, headline=headline)   # dict
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import math
import re
import sys
import warnings
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from keyword_finder import (boilerplate_category, load_and_prepare,
                            strip_wire_boilerplate)

# same token rule as keyword_finder's vectorizers
TOKEN_RE = re.compile(r"(?u)\b[a-zA-Z][a-zA-Z]+\b")
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Orchestrator5's Body cell is the RAW RTPR payload one-lined, which may
    carry HTML tags/entities; training bodies were clean text.  Mirror
    RTPR_connector3_Firehose.to_plain_body(): only touch text that looks like HTML."""
    if "<" in text and ">" in text:
        text = html.unescape(_TAG_RE.sub(" ", text))
    return text

CANON_DIR = HERE / "outputs" / "classify_ge20_nowire"
DEFAULT_KEYWORDS = [str(CANON_DIR / "binary_keyword_impact_all_terms.csv")]
DEFAULT_PIPELINE = str(CANON_DIR / "tfidf_fitted_pipeline.joblib")
DEFAULT_REFERENCE = str(CANON_DIR / "scorer_reference.json")
DEFAULT_INPUT = str(
    HERE / "PR-stats-2026-05-03to06-19-filtered-50float-12high_RBscore_body_clean.tsv")
DEFAULT_PREVALENCE = 0.221          # ge20 train positive rate (report §2.3)

COEF_COLUMNS = ("logit_coef", "logit_coef_binary", "elasticnet_coef",
                "coef", "power", "weight")
KW_METHODS = ("kw_add_first", "kw_add_log", "kw_add_lin",
              "kw_mult_nb", "kw_mult_nb_log")
METHOD_LABELS = {
    "kw_add_first":   "ADD log-odds, first occurrence only",
    "kw_add_log":     "ADD log-odds, 1+ln(count) damping",
    "kw_add_lin":     "ADD log-odds, linear count",
    "kw_mult_nb":     "MULTIPLY marginal odds (naive Bayes)",
    "kw_mult_nb_log": "MULTIPLY marginal odds, 1+ln(count) damping",
    "model_proba":    "fitted tfidf pipeline (gold standard)",
}
_P_EPS = 1e-3


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, z))))


def _logit(p: float) -> float:
    p = min(max(p, _P_EPS), 1.0 - _P_EPS)
    return math.log(p / (1.0 - p))


def _detect_coef_col(df: pd.DataFrame, override: str | None) -> str:
    if override:
        if override not in df.columns:
            sys.exit(f"ERROR: --coef-col '{override}' not in keyword table "
                     f"columns {list(df.columns)}")
        return override
    for c in COEF_COLUMNS:
        if c in df.columns:
            return c
    sys.exit(f"ERROR: no coefficient column found (looked for {COEF_COLUMNS}) "
             f"in keyword table columns {list(df.columns)}")


def _read_prevalence(keywords_path: Path) -> float:
    """train_pos_rate from the model_performance_summary.csv next to the table."""
    summary = keywords_path.parent / "model_performance_summary.csv"
    try:
        s = pd.read_csv(summary)
        val = float(s.loc[s["task"] == "classification", "train_pos_rate"].iloc[0])
        if 0.0 < val < 1.0:
            return val
    except Exception:
        pass
    return DEFAULT_PREVALENCE


class KeywordScorer:
    """Table-driven PR scorer; optionally wraps the fitted sklearn pipeline."""

    def __init__(self, coefs: dict[str, float], marginal_p1: dict[str, float],
                 prevalence: float, pipeline=None, reference: dict | None = None):
        self.coefs = coefs                    # term -> conditional logit coef
        self.p1 = marginal_p1                 # term -> marginal P(big | term)
        self.prevalence = prevalence
        self._logit_pi = _logit(prevalence)
        self.pipeline = pipeline
        self.reference = reference or {}

    # ------------------------------------------------------------------ load
    @classmethod
    def load(cls, keywords=None, coef_col: str | None = None,
             pipeline_path: str | None = DEFAULT_PIPELINE,
             reference_path: str | None = DEFAULT_REFERENCE,
             prevalence: float | None = None,
             min_doc_freq: int = 0) -> "KeywordScorer":
        paths = [Path(p) for p in (keywords or DEFAULT_KEYWORDS)]
        paths = [p if p.exists() else HERE / p for p in paths]
        frames = []
        for p in paths:
            if not p.exists():
                sys.exit(f"ERROR: keyword table not found: {p}")
            kt = pd.read_csv(p)
            kt["_coef"] = pd.to_numeric(kt[_detect_coef_col(kt, coef_col)],
                                        errors="coerce")
            frames.append(kt)
        kt = pd.concat(frames, ignore_index=True)
        kt["term"] = kt["term"].astype(str)
        kt = kt.drop_duplicates(subset="term", keep="first")
        if min_doc_freq and "document_frequency" in kt.columns:
            kt = kt[pd.to_numeric(kt["document_frequency"],
                                  errors="coerce").fillna(0) >= min_doc_freq]
        sel = kt[kt["_coef"].fillna(0.0) != 0.0]
        coefs = dict(zip(sel["term"], sel["_coef"].astype(float)))

        pi = prevalence if prevalence is not None else _read_prevalence(paths[0])

        # marginal P(big | term present) recovered from point-biserial r:
        #   r = (p1 - pi) * sqrt(f/(1-f)) / sqrt(pi*(1-pi))   (binary feature,
        #   rate f) => p1 = pi + r * sqrt(pi*(1-pi)) * sqrt((1-f)/f)
        p1: dict[str, float] = {}
        if {"univariate_r", "document_frequency_pct"} <= set(sel.columns):
            r = pd.to_numeric(sel["univariate_r"], errors="coerce")
            f = pd.to_numeric(sel["document_frequency_pct"], errors="coerce") / 100.0
            ok = r.notna() & f.notna() & (f > 0) & (f < 1)
            vals = pi + r[ok] * math.sqrt(pi * (1 - pi)) * np.sqrt((1 - f[ok]) / f[ok])
            p1 = {t: float(min(max(v, _P_EPS), 1 - _P_EPS))
                  for t, v in zip(sel.loc[ok, "term"], vals)}
        else:
            print("  note: keyword table lacks univariate_r/document_frequency_pct"
                  " -> kw_mult_nb method disabled")

        pipeline = None
        if pipeline_path:
            pp = Path(pipeline_path)
            if not pp.exists():
                pp = HERE / pipeline_path
            try:
                import joblib
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    pipeline = joblib.load(pp)
            except Exception as exc:                      # missing sklearn, bad file...
                print(f"  note: pipeline not loaded ({exc}) -> model_proba disabled")

        reference = None
        if reference_path:
            rp = Path(reference_path)
            if rp.exists():
                reference = json.loads(rp.read_text())

        print(f"KeywordScorer: {len(coefs)} weighted terms from "
              f"{', '.join(p.name for p in paths)} | prevalence={pi:.3f} | "
              f"pipeline={'yes' if pipeline is not None else 'no'} | "
              f"percentile-ref={'yes' if reference else 'no'}")
        return cls(coefs, p1, pi, pipeline, reference)

    # --------------------------------------------------------------- scoring
    @staticmethod
    def _ngram_counts(stripped_lower_text: str) -> Counter:
        toks = TOKEN_RE.findall(stripped_lower_text)
        counts = Counter(toks)
        counts.update(" ".join(bg) for bg in zip(toks, toks[1:]))
        return counts

    def _score_counts(self, counts: Counter, explain: bool = False,
                      detail: bool = False) -> dict:
        z_first = z_log = z_lin = 0.0
        z_nb = z_nb_log = self._logit_pi
        hits = 0
        contribs: list[tuple[str, float]] = []
        details: list[tuple[str, int, float, float]] = []
        for term, cnt in counts.items():
            b = self.coefs.get(term)
            if b is not None:
                hits += 1
                w_log = 1.0 + math.log(cnt)
                z_first += b
                z_log += b * w_log
                z_lin += b * cnt
                if explain:
                    contribs.append((term, b * w_log))
                if detail:
                    details.append((term, cnt, b, b * w_log))
                p1 = self.p1.get(term)
                if p1 is not None:
                    shift = _logit(p1) - self._logit_pi
                    z_nb += shift
                    z_nb_log += shift * w_log
        out = {
            "kw_add_first": _sigmoid(z_first),
            "kw_add_log": _sigmoid(z_log),
            "kw_add_lin": _sigmoid(z_lin),
            "kw_mult_nb": _sigmoid(z_nb),
            "kw_mult_nb_log": _sigmoid(z_nb_log),
            "n_kw_hits": hits,
        }
        if explain:
            contribs.sort(key=lambda tc: abs(tc[1]), reverse=True)
            out["kw_top_terms"] = " | ".join(
                f"{t}:{c:+.2f}" for t, c in contribs[:5])
        if detail:
            out["_detail"] = details
        return out

    def score_texts(self, texts, explain: bool = False,
                    already_stripped: bool = False, detail: bool = False):
        """Returns a scores DataFrame; with detail=True returns
        (scores, per-row list of (term, count, power, contribution))."""
        stripped = [t if isinstance(t, str) else "" for t in texts]
        if not already_stripped:
            stripped = [strip_wire_boilerplate(_strip_html(t)) for t in stripped]
        rows = [self._score_counts(self._ngram_counts(t.lower()), explain, detail)
                for t in stripped]
        df = pd.DataFrame(rows)
        detail_lists = df.pop("_detail").tolist() if "_detail" in df.columns else None
        if self.pipeline is not None:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                df["model_proba"] = self.pipeline.predict_proba(stripped)[:, 1]
        for m in [c for c in df.columns if c in METHOD_LABELS]:
            ref = self.reference.get("methods", {}).get(m)
            if ref:
                arr = np.asarray(ref["sorted_scores"], dtype=float)
                df[m + "_pctl"] = (np.searchsorted(arr, df[m].to_numpy(),
                                                   side="right") / len(arr))
        return (df, detail_lists) if detail else df

    def score_text(self, text: str, headline: str = "",
                   explain: bool = False) -> dict:
        out = self.score_texts([text], explain=explain).iloc[0].to_dict()
        out["boilerplate_category"] = boilerplate_category(
            f"{headline} \n {text}" if headline else text)
        return out

    # ------------------------------------------------------------- reference
    def build_reference(self, scores: pd.DataFrame, threshold: float,
                        source: str, path: str) -> None:
        methods = {m: {"sorted_scores": np.sort(scores[m].to_numpy())
                       .round(6).tolist()}
                   for m in scores.columns if m in METHOD_LABELS}
        ref = {"created": dt.datetime.now().isoformat(timespec="seconds"),
               "source": source, "n_docs": int(len(scores)),
               "threshold": threshold, "prevalence": self.prevalence,
               "methods": methods}
        Path(path).write_text(json.dumps(ref))
        self.reference = ref
        print(f"  percentile reference written -> {path} "
              f"({len(methods)} methods, n={len(scores)})")


# --------------------------------------------------------------------------- #
# module-level convenience for the orchestrator
# --------------------------------------------------------------------------- #
_DEFAULT_SCORER: KeywordScorer | None = None


def get_default_scorer() -> KeywordScorer:
    global _DEFAULT_SCORER
    if _DEFAULT_SCORER is None:
        _DEFAULT_SCORER = KeywordScorer.load()
    return _DEFAULT_SCORER


def score_text(text: str, headline: str = "") -> dict:
    return get_default_scorer().score_text(text, headline)


# --------------------------------------------------------------------------- #
# CLI modes
# --------------------------------------------------------------------------- #
def _resolve_col(df: pd.DataFrame, name: str, what: str,
                 required: bool = True) -> str | None:
    if name in df.columns:
        return name
    for c in df.columns:
        if c.lower() == name.lower():
            return c
    if required:
        sys.exit(f"ERROR: {what} column '{name}' not found. "
                 f"Available: {list(df.columns)}")
    return None


def run_score(scorer: KeywordScorer, args) -> None:
    inp = Path(args.input)
    df = pd.read_csv(inp, sep="\t", dtype=str)
    text_col = _resolve_col(df, args.text_col, "text")
    head_col = _resolve_col(df, args.headline_col, "headline", required=False)
    print(f"scoring {len(df)} rows of {inp.name} (text column '{text_col}')")

    bodies = df[text_col].fillna("")
    detect = (df[head_col].fillna("") + " \n " + bodies) if head_col else bodies
    want_detail = args.words_detail == "true"
    res = scorer.score_texts(bodies.tolist(), explain=args.explain,
                             detail=want_detail)
    scores, detail_lists = res if want_detail else (res, None)
    scores.insert(0, "boilerplate_category", detect.map(boilerplate_category).values)

    for c in scores.columns:
        if scores[c].dtype.kind == "f":
            scores[c] = scores[c].round(6)
        df[c] = scores[c].values

    out = Path(args.out) if args.out else inp.with_name(inp.stem + "_scored.tsv")
    df.to_csv(out, sep="\t", index=False)
    num = scores.select_dtypes("number")
    print(f"added columns: {', '.join(scores.columns)}")
    print(num.describe().loc[["mean", "50%", "min", "max"]].round(3).to_string())
    print(f"wrote {out}")

    if detail_lists is not None:
        _write_words_detail(df, detail_lists, out, args)


def _write_words_detail(df: pd.DataFrame, detail_lists: list, out: Path,
                        args) -> None:
    """Top 10 positive + top 10 negative keyword contributors per PR row.
    power = the term's coefficient from the input keyword table;
    contribution = power * (1+ln count), the kw_add_log summand."""
    id_col = _resolve_col(df, args.id_col, "id", required=False)
    sym_col = _resolve_col(df, "Symbol", "symbol", required=False)
    recs = []
    for i, terms in enumerate(detail_lists):
        pos = sorted((t for t in terms if t[2] > 0),
                     key=lambda t: t[3], reverse=True)[:10]
        neg = sorted((t for t in terms if t[2] < 0), key=lambda t: t[3])[:10]
        for side, group in (("pos", pos), ("neg", neg)):
            for rank, (term, cnt, power, contrib) in enumerate(group, 1):
                recs.append({
                    "row": i,
                    "ID": df[id_col].iloc[i] if id_col else "",
                    "Symbol": df[sym_col].iloc[i] if sym_col else "",
                    "side": side, "rank": rank, "term": term, "count": cnt,
                    "power": round(power, 4),
                    "contribution": round(contrib, 4),
                })
    stem = out.stem[:-len("_scored")] if out.stem.endswith("_scored") else out.stem
    dpath = out.with_name(stem + "_words_detail.tsv")
    cols = ["row", "ID", "Symbol", "side", "rank", "term", "count",
            "power", "contribution"]
    pd.DataFrame(recs, columns=cols).to_csv(dpath, sep="\t", index=False)
    print(f"words-detail: {len(recs)} term lines for {len(detail_lists)} rows "
          f"-> {dpath}")


def run_evaluate(scorer: KeywordScorer, args) -> None:
    """Back-test every method on the historical file, on the SAME held-out
    test split as keyword_finder (seed/stratify reproduce it), then rebuild
    the percentile reference."""
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import train_test_split

    df = load_and_prepare(Path(args.input), args.text_col, args.target_col,
                          args.id_col, drop_duplicates=True,
                          strip_boilerplate=True, strip_wire=True)
    y = (df["_target"].to_numpy(dtype=float) >= args.threshold).astype(int)
    idx_tr, idx_te = train_test_split(np.arange(len(y)), test_size=args.test_size,
                                      random_state=args.seed, stratify=y)
    print(f"\n=== Evaluate: {len(y)} docs, positives={y.sum()} "
          f"({100 * y.mean():.1f}%), test split n={len(idx_te)} "
          f"(seed={args.seed}, mirrors keyword_finder) ===")

    scores = scorer.score_texts(df["_text"].tolist(), already_stripped=True)
    methods = [m for m in METHOD_LABELS if m in scores.columns]

    rows = []
    for m in methods:
        s = scores[m].to_numpy(dtype=float)
        rows.append({
            "method": m, "what": METHOD_LABELS[m],
            "test_auc": roc_auc_score(y[idx_te], s[idx_te]),
            "test_ap": average_precision_score(y[idx_te], s[idx_te]),
            "train_auc": roc_auc_score(y[idx_tr], s[idx_tr]),
            "full_auc": roc_auc_score(y, s),
        })
    lg = (pd.DataFrame(rows).sort_values("test_auc", ascending=False)
          .reset_index(drop=True))
    print("\nLeague table (test split = honest comparison; train/full are "
          "in-sample, optimistic):")
    print(lg.round(4).to_string(index=False))
    print(f"baseline: AUC=0.5, AP={y[idx_te].mean():.3f} (test prevalence)")
    if "model_proba" in methods:
        print("sanity: model_proba test_auc should reproduce the report's "
              "0.7828 if the split matched.")

    scorer.build_reference(scores, args.threshold, Path(args.input).name,
                           args.reference)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evaluate", action="store_true",
                    help="back-test all methods on the historical file instead "
                         "of scoring; also (re)builds the percentile reference")
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--out", default=None, help="scored TSV path "
                    "(default: <input>_scored.tsv)")
    ap.add_argument("--text-col", default="body",
                    help="matched case-insensitively (so O5's 'Body' works)")
    ap.add_argument("--headline-col", default="Headline")
    ap.add_argument("--target-col", default="DailyHigh(%)")
    ap.add_argument("--id-col", default="ID")
    ap.add_argument("--threshold", type=float, default=20.0)
    ap.add_argument("--keywords", nargs="+", default=DEFAULT_KEYWORDS,
                    help="one or more keyword CSVs (keyword_finder tables, "
                         "consensus sheets, or any CSV with term+power columns)")
    ap.add_argument("--coef-col", default=None,
                    help=f"power column (default: first of {COEF_COLUMNS})")
    ap.add_argument("--pipeline", default=DEFAULT_PIPELINE)
    ap.add_argument("--no-pipeline", action="store_true",
                    help="skip the joblib model (pure keyword-table scoring)")
    ap.add_argument("--reference", default=DEFAULT_REFERENCE)
    ap.add_argument("--no-percentile", action="store_true")
    ap.add_argument("--prevalence", type=float, default=None,
                    help="P(positive) anchor for kw_mult_nb "
                         "(default: from model_performance_summary.csv)")
    ap.add_argument("--min-doc-freq", type=int, default=0,
                    help="drop keyword-table terms below this document_frequency")
    ap.add_argument("--explain", action="store_true",
                    help="add kw_top_terms column with each row's top 5 "
                         "signed keyword contributions")
    ap.add_argument("--words-detail", choices=["true", "false"], default="false",
                    help="score mode: also write <input>_words_detail.tsv with "
                         "each row's top 10 positive + top 10 negative keyword "
                         "contributors (term, count, power, contribution)")
    ap.add_argument("--test-size", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    scorer = KeywordScorer.load(
        keywords=args.keywords, coef_col=args.coef_col,
        pipeline_path=None if args.no_pipeline else args.pipeline,
        reference_path=None if args.no_percentile else args.reference,
        prevalence=args.prevalence, min_doc_freq=args.min_doc_freq)

    if args.evaluate:
        run_evaluate(scorer, args)
    else:
        run_score(scorer, args)


if __name__ == "__main__":
    main()
