#!/usr/bin/env python3
"""Re-run the 2026-07-30 precision sweep against a CORRECTED corpus.

`analyze_surge_predictors_v3.py` holds the calibration protocol the operator
locked on 2026-07-30 (pos counts as caught if the candidate crosses anywhere in
its recording; neu is a tiebreaker; report the best neg-kill at 0/1/2/3 pos
lost). Nothing about that protocol changes here — only the FEATURE VALUES do,
because the corpus was regenerated through `replay_mole.py` with the twin
double-count and/or the partial-window rate suppression corrected.

Run `replay_mole.py --out-dir ../mole-outputs/recomputed/<variant>` first.

Usage:
    /home/tom/venv/bin/python recalibrate_corrected.py both
    /home/tom/venv/bin/python recalibrate_corrected.py baseline twin rate both
"""

import datetime as dt
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import analyze_surge_predictors_v3 as v3  # noqa: E402

RECOMPUTED = os.path.normpath(os.path.join(HERE, "..", "mole-outputs", "recomputed"))


def run(variant: str) -> None:
    corpus = os.path.join(RECOMPUTED, variant)
    if not os.path.isdir(corpus):
        raise SystemExit(f"no such corpus: {corpus} — run replay_mole.py first")

    today = dt.date.today().isoformat()
    report = os.path.normpath(os.path.join(
        HERE, "..", "mole-outputs", f"surge-predictor-analysis-{today}-{variant}.md"))

    # v3 globs its own OUT_DIR and writes its own REPORT; both are module-level,
    # and the rest of the module reuses base's pure helpers.
    v3.OUT_DIR, v3.REPORT = corpus, report
    print("=" * 78)
    print(f"VARIANT {variant}  ({corpus})")
    print("=" * 78)
    v3.main()
    print(f"-> {report}\n")


if __name__ == "__main__":
    variants = sys.argv[1:] or ["both"]
    for v in variants:
        run(v)
