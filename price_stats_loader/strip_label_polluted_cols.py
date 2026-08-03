#!/usr/bin/env python3
"""Drop the columns invalidated by the fromPR_High(%) baseline defect.

Until 2026-07-26 ``fromPR_HighOfDay_addOn.py`` used the close of the 1-min bar
*containing* the PR as the baseline instead of the bar before it, so the
denominator already priced the news in (see ``version_control.txt``). Measured
on 726 rows of the 06-20->07-23 corpus: 72.9% of values wrong, and 13.2% of
rows flip the >=15% cut the keyword models are trained on.

Two families of columns are therefore invalid:

  1. ``fromPR_High(%)`` itself — the biased label.
  2. every keyword-model column — those models were TRAINED on that label, so
     their scores carry the bias no matter which corpus they were applied to.
     That includes ``n_kw_hits*``, which counts hits against the
     model-SELECTED keyword list and so is a model output, not a raw feature.

Explicitly NOT dropped, having been audited as label-independent:

  * ``fromPR_HighTime(m:s)`` — the fix changed only the denominator; the bar
    carrying the max high is picked in an untouched branch, and the values were
    verified identical pre- and post-fix.
  * ``DailyHigh(%)`` / ``DailyHigh($)`` — measured against the previous daily
    close, so there is no PR-minute baseline to get wrong.
  * ``Trigger``, ``nocoref_*``, FinBERT ``positive/negative/neutral/
    sentiment_score/label``, ``body_sentiment``, ``boilerplate_category``.

``Headline-RBscore`` is rule-based (a hand-written word list in
``tools/small-cap-news-scorer/fast_signal.py``) and is NOT polluted, but it is
dropped by default here at the user's request; pass ``--keep-rbscore`` to keep
it.

Example:
    python3 strip_label_polluted_cols.py \\
        -i outputs/01_PR-stats-...-_KeyWordScored_.tsv \\
        -o outputs/01_PR-stats-2026-05-03to06-19-filtered-50float-12high_body_created_excStr2.tsv
"""

import argparse
import os
import sys

import pandas as pd

# The biased label.
LABEL_COLS = ['fromPR_High(%)']

# Keyword-model outputs: body pass, its percentile twins, the headline (_hl)
# pass, its percentile twins, and the frozen live combo.
_KW_BASE = ['kw_add_first', 'kw_add_log', 'kw_add_lin', 'kw_mult_nb',
            'kw_mult_nb_log', 'n_kw_hits', 'model_proba']
_KW_PCTL = ['kw_add_first_pctl', 'kw_add_log_pctl', 'kw_add_lin_pctl',
            'kw_mult_nb_pctl', 'kw_mult_nb_log_pctl', 'model_proba_pctl']

KEYWORD_COLS = (
    _KW_BASE
    + _KW_PCTL
    + [c + '_hl' for c in _KW_BASE]
    + [c + '_hl' for c in _KW_PCTL]
    + ['model_proba_PLUS_kw_mult_nb']
)

# Rule-based, not polluted — dropped by default by request, kept with
# --keep-rbscore.
RBSCORE_COLS = ['Headline-RBscore']

DEFAULT_DROP_COLS = LABEL_COLS + RBSCORE_COLS + KEYWORD_COLS


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--input', '-i', required=True, dest='input_file',
                        metavar='FILE', help='Path to the input .tsv file.')
    parser.add_argument('--output', '-o', required=True, dest='output_file',
                        metavar='FILE', help='Path to write the cleaned .tsv.')
    parser.add_argument('--drop-cols', dest='drop_cols', default=None,
                        metavar='A,B,C',
                        help='Comma-separated column list overriding the '
                             'built-in default.')
    parser.add_argument('--keep-rbscore', action='store_true',
                        help='Keep Headline-RBscore (rule-based, not polluted).')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite the output file if it already exists.')
    args = parser.parse_args()

    if args.drop_cols is not None:
        drop = [c.strip() for c in args.drop_cols.split(',') if c.strip()]
    else:
        drop = list(DEFAULT_DROP_COLS)
        if args.keep_rbscore:
            drop = [c for c in drop if c not in RBSCORE_COLS]

    if os.path.exists(args.output_file) and not args.force:
        print(f"[fatal] output already exists: {args.output_file} "
              f"(pass --force to overwrite)", file=sys.stderr)
        return 1

    # na_filter=False keeps blank cells as empty strings rather than turning
    # them into NaN, which would be written back out as the literal 'nan'.
    df = pd.read_csv(args.input_file, sep='\t', dtype=str,
                     keep_default_na=False, na_filter=False)

    missing = [c for c in drop if c not in df.columns]
    if missing:
        print(f"[fatal] {len(missing)} column(s) to drop not found in "
              f"{args.input_file}: {', '.join(missing)}", file=sys.stderr)
        print("[fatal] refusing to write a partially-cleaned file.",
              file=sys.stderr)
        return 1

    before = list(df.columns)
    out = df.drop(columns=drop)

    os.makedirs(os.path.dirname(os.path.abspath(args.output_file)),
                exist_ok=True)
    out.to_csv(args.output_file, sep='\t', index=False)

    print(f"input : {args.input_file}")
    print(f"        {len(df)} rows x {len(before)} cols")
    print(f"dropped {len(drop)} cols: {', '.join(drop)}")
    print(f"output: {args.output_file}")
    print(f"        {len(out)} rows x {len(out.columns)} cols")
    return 0


if __name__ == '__main__':
    sys.exit(main())
