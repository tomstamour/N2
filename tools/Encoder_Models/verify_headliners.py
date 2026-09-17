#!/usr/bin/env python
"""
verify_headliners.py — post-install sanity check for every headliner in this dir.

Each headliner was probe-verified by hand once (label order, INT8 faithfulness),
but a fresh `--export` on another machine / onnxruntime can silently reproduce
the failures this directory has already hit: junk id2label, INT8 label flips,
uniform probabilities. This script re-scores a fixed set of golden headlines
through every model's library API and compares against `verify_goldens.txt`,
the results captured on the validated reference install.

Goldens record VALIDATED behavior, not ideal sentiment — known quirks (e.g.
Trading-Hero scoring a delisting notice neutral) are stored as-is and must
replicate. A FAIL means "this export differs from the one that was validated",
never "the golden is wrong".

Usage:
    /home/tom/venv/bin/python verify_headliners.py               # verify all
    /home/tom/venv/bin/python verify_headliners.py --model DeBERTa
    /home/tom/venv/bin/python verify_headliners.py --tol 0.02
    /home/tom/venv/bin/python verify_headliners.py --capture --force  # re-baseline

Exit code 0 = all present models PASS (missing model dirs are SKIPped),
1 = any FAIL or missing goldens file.
"""

import argparse
import importlib.util
import json
import math
import socket
import sys
import time
from datetime import date
from pathlib import Path
from typing import Dict, List, Tuple

HERE = Path(__file__).resolve().parent
GOLDENS_FILE = HERE / "verify_goldens.txt"   # .txt so git tracks it (.json is ignored)
LATENCY_BUDGET_MS = 150.0                    # warm per-headline budget (warn only)
DEFAULT_TOL = 0.05

# display name -> script file, scoring function, model dir (existence check only).
# "exact": deterministic scorer, compared with zero tolerance.
# Model names are the unified 2026-08-09 column tokens, shared with
# HeadScore11encoders.MODELS — keep the two in step; the names are the contract.
MODELS = {
    "FinMarBa":   {"script": "FinMarBa_headliner.py",      "fn": "score_headline",   "model_dir": "finmarba_onnx"},
    "FLANG":      {"script": "FLANG-BERT_headliner.py",    "fn": "analyze_headline", "model_dir": "flangbert_sentiment_onnx"},
    "FLElectra":  {"script": "FLANG-ELECTRA_headliner.py", "fn": "analyze_headline", "model_dir": "flang_electra_onnx"},
    "RoBERTa":    {"script": "RoBERTa_headliner.py",       "fn": "score_headline",   "model_dir": "roberta_sentiment_onnx"},
    "ModernB":    {"script": "ModernBERT_headliner.py",    "fn": "analyze_headline", "model_dir": "modernbert_zeroshot_onnx"},
    "DeBERTa":    {"script": "DeBERTa_headliner.py",       "fn": "analyze_headline", "model_dir": "deberta_zeroshot_onnx"},
    "FinTone":    {"script": "finbert-tone_headliner.py",  "fn": "score_headline",   "model_dir": "finbert_tone_onnx"},
    "FinancialB": {"script": "FinancialBERT_headliner.py", "fn": "score_headline",   "model_dir": "financialbert_onnx"},
    "DistilRo":   {"script": "distilroberta_headliner.py", "fn": "score_headline",   "model_dir": "distilroberta_financial_onnx"},
    "TradeHero":  {"script": "Trading-Hero_headliner.py",  "fn": "score_headline",   "model_dir": "trading_hero_onnx"},
    "LouMcD":     {"script": "LouMcD-SDict_headliner.py",  "fn": "score_headline",   "model_dir": "loumcd_sdict", "exact": True},
}

# Fixed probe set: FLANG-BERT's export probes plus the known quirk-triggers from
# CLAUDE.md (delisting = the classic INT8-flip case, FDA = out-of-domain positive).
GOLDEN_HEADLINES = {
    "bankruptcy":    "Company files for Chapter 11 bankruptcy as shares plunge 80%",
    "delisting":     "Company receives Nasdaq delisting notice after failing to meet listing requirements",
    "offering":      "Company announces $10 million registered direct offering priced at a discount",
    "record_profit": "The company's quarterly net profit rose 30 percent, beating analyst expectations",
    "fda_approval":  "FDA grants approval for the company's lead drug candidate",
    "contract_win":  "Company wins multi-year supply contract with a major automaker",
    "conference":    "Company to present at an industry investor conference next week",
    "veea_example":  "Veea Announces the VeeaONE Distributed Intelligence Platform, Enabling "
                     "Cybersecure Sovereign Data Fabrics and Enterprise AI Grids for Physical AI",
}

# Keys excluded from golden storage/comparison (machine-dependent or redundant).
# `ms` is the unified 2026-08-09 timing key; before that the headliners disagreed
# between `elapsed_ms` and `inference_ms`, so both are still listed — a golden file
# captured on either vocabulary stays readable.
VOLATILE_KEYS = {"headline", "ms", "elapsed_ms", "inference_ms"}


def _load_module(script_name: str):
    """Import a headliner by file path (several filenames contain hyphens)."""
    mod_name = "headliner_" + script_name.replace("-", "_").removesuffix(".py")
    spec = importlib.util.spec_from_file_location(mod_name, HERE / script_name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _clean(result: Dict) -> Dict:
    return {k: v for k, v in result.items() if k not in VOLATILE_KEYS}


def _timing_ms(result: Dict) -> float:
    # Every headliner returns `ms` since 2026-08-09; the two legacy spellings are
    # kept as fallbacks so this still reports a number against an older checkout.
    return float(result.get("ms",
                 result.get("elapsed_ms", result.get("inference_ms", math.nan))))


def _flatten(obj, prefix="") -> Dict[str, object]:
    """Dict tree -> {dot.path: leaf} so golden/current compare key-by-key."""
    if isinstance(obj, dict):
        flat = {}
        for k, v in obj.items():
            flat.update(_flatten(v, f"{prefix}{k}."))
        return flat
    return {prefix.rstrip("."): obj}


def _compare(golden: Dict, current: Dict, tol: float) -> Tuple[List[str], float]:
    """Return (failure messages, max numeric drift) for one headline result."""
    g, c = _flatten(golden), _flatten(current)
    failures, max_drift = [], 0.0
    for path in sorted(set(g) | set(c)):
        if path not in g:
            failures.append(f"unexpected key '{path}' = {c[path]!r}")
        elif path not in c:
            failures.append(f"missing key '{path}' (golden {g[path]!r})")
        elif isinstance(g[path], (int, float)) and not isinstance(g[path], bool) \
                and isinstance(c[path], (int, float)):
            drift = abs(float(c[path]) - float(g[path]))
            max_drift = max(max_drift, drift)
            if drift > tol:
                failures.append(f"'{path}' drift {drift:.4f} > tol {tol} "
                                f"(golden {g[path]}, got {c[path]})")
        elif g[path] != c[path]:
            failures.append(f"'{path}' mismatch: golden {g[path]!r}, got {c[path]!r}")
    return failures, max_drift


def _score_all(names: List[str]) -> Dict[str, Dict]:
    """Run every requested model over the golden headlines.

    Returns {model: {"results": {hkey: cleaned dict}, "warm_ms": float}} for
    models whose model dir exists, {"skip": reason} otherwise.
    """
    out = {}
    for name in names:
        cfg = MODELS[name]
        if not (HERE / cfg["model_dir"]).is_dir():
            out[name] = {"skip": f"model dir {cfg['model_dir']}/ missing — "
                                 f"run: {cfg['script']} --export"}
            continue
        print(f"[{name}] loading {cfg['script']} ...", flush=True)
        module = _load_module(cfg["script"])
        module.load_model()
        score_fn = getattr(module, cfg["fn"])
        results, timings = {}, []
        for hkey, text in GOLDEN_HEADLINES.items():
            res = score_fn(text)
            timings.append(_timing_ms(res))
            results[hkey] = _clean(res)
        # min over probes = warm latency, immune to a semi-cold first call
        out[name] = {"results": results, "warm_ms": min(timings)}
        print(f"[{name}] scored {len(results)} probes, warm {min(timings):.1f} ms", flush=True)
    return out


def capture(names: List[str], force: bool) -> int:
    if GOLDENS_FILE.exists() and not force:
        print(f"ERROR: {GOLDENS_FILE.name} exists — re-baselining overwrites the "
              f"validated reference. Re-run with --force if that is intended.")
        return 1
    try:
        import onnxruntime
        ort_ver = onnxruntime.__version__
    except ImportError:
        ort_ver = "unavailable"
    scored = _score_all(names)
    # partial capture (--model) merges into the existing file instead of clobbering it
    prior = {}
    if GOLDENS_FILE.exists() and len(names) < len(MODELS):
        prior = json.loads(GOLDENS_FILE.read_text()).get("models", {})
    prior.update({n: d["results"] for n, d in scored.items() if "results" in d})
    goldens = {
        "_meta": {
            "captured_on": date.today().isoformat(),
            "host": socket.gethostname(),
            "python": sys.version.split()[0],
            "onnxruntime": ort_ver,
        },
        "models": prior,
    }
    skipped = [n for n, d in scored.items() if "skip" in d]
    GOLDENS_FILE.write_text(json.dumps(goldens, indent=2, sort_keys=True) + "\n")
    print(f"\nWrote goldens for {len(goldens['models'])} models to {GOLDENS_FILE.name}")
    if skipped:
        print(f"WARNING: skipped (no model dir): {', '.join(skipped)}")
    return 0


def verify(names: List[str], tol: float) -> int:
    if not GOLDENS_FILE.exists():
        print(f"ERROR: {GOLDENS_FILE.name} not found — capture it on a validated "
              f"install first: verify_headliners.py --capture")
        return 1
    goldens = json.loads(GOLDENS_FILE.read_text())["models"]
    scored = _score_all(names)

    rows, any_fail = [], False
    for name in names:
        data = scored[name]
        if "skip" in data:
            print(f"[{name}] SKIP: {data['skip']}")
            rows.append((name, "-", "-", "-", "SKIP"))
            continue
        if name not in goldens:
            print(f"[{name}] FAIL: no golden entry — re-run --capture on the reference install")
            rows.append((name, "-", "-", "-", "FAIL"))
            any_fail = True
            continue
        model_tol = 0.0 if MODELS[name].get("exact") else tol
        n_fail, max_drift = 0, 0.0
        for hkey in GOLDEN_HEADLINES:
            if hkey not in goldens[name]:
                print(f"[{name}] {hkey}: FAIL: probe absent from goldens — re-capture needed")
                n_fail += 1
                continue
            failures, drift = _compare(goldens[name][hkey], data["results"][hkey], model_tol)
            max_drift = max(max_drift, drift)
            for msg in failures:
                print(f"[{name}] {hkey}: FAIL: {msg}")
            n_fail += bool(failures)
        warm = data["warm_ms"]
        if warm > LATENCY_BUDGET_MS:
            print(f"[{name}] WARN: warm latency {warm:.1f} ms exceeds {LATENCY_BUDGET_MS:.0f} ms budget")
        status = "FAIL" if n_fail else "PASS"
        any_fail |= bool(n_fail)
        rows.append((name, f"{len(GOLDEN_HEADLINES) - n_fail}/{len(GOLDEN_HEADLINES)}",
                     f"{max_drift:.4f}", f"{warm:.1f}", status))

    print("\nmodel           probes  max_drift  warm_ms  status")
    print("-" * 52)
    for name, probes, drift, warm, status in rows:
        print(f"{name:<15} {probes:>6}  {drift:>9}  {warm:>7}  {status}")
    print("\n" + ("FAIL — fresh export differs from validated reference" if any_fail
                  else "All present models match the validated reference."))
    return 1 if any_fail else 0


def main():
    parser = argparse.ArgumentParser(
        description="Verify freshly exported headliner models against validated golden scores.")
    parser.add_argument("--capture", action="store_true",
                        help="record current outputs as the golden reference (validated install only)")
    parser.add_argument("--force", action="store_true",
                        help="allow --capture to overwrite an existing goldens file")
    parser.add_argument("--model", choices=sorted(MODELS), metavar="NAME",
                        help="verify a single model (choices: %(choices)s)")
    parser.add_argument("--tol", type=float, default=DEFAULT_TOL,
                        help="max allowed probability/score drift (default %(default)s; "
                             "LouMcD-SDict is always exact)")
    args = parser.parse_args()

    names = [args.model] if args.model else list(MODELS)
    sys.exit(capture(names, args.force) if args.capture else verify(names, args.tol))


if __name__ == "__main__":
    main()
