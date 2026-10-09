#!/usr/bin/env python
"""
s4_reference_gen.py -- writes the missing reference_scores.json for the two S4 models
(kw30_tfidf_S4_FT_hd, kw50_tfidf_S4_FT_bd) in
S4_model_trained_on_2026-05-04-2026-09-24_val7030/.

Neither model's card.json has a reference_scores.json (its own note: "nothing loads this
model live, so there is no consumer to byte-verify against"). Orchestrator5.5's o55_s4kw.py
now IS a consumer, so it needs one -- same shape and same idea as kw15_ft_train.py's own
reference_rows(): raw Headline / Body cells scored through the LIVE prep
(fm3_common.prep_headline / prep_body400_kw, == o54_engine._kw15_prep) into the fitted
joblib pipelines. Orchestrator5.5 must reproduce every value exactly at startup.

Unlike kw15_ft_train.py's reference (one label, two channels of the SAME threshold), the
two S4 models have different labels (>=30 hd, >=50 bd) and different channels, so the sample
here is an unstratified seeded draw across the whole corpus, not a pos/neg split -- this is a
load/serve byte-parity check, not a calibration check.

  /home/tom/venv/bin/python s4_reference_gen.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent                    # tools/keyword/
sys.path.insert(0, str(HERE / "model-FM3"))
import fm3_common as C  # noqa: E402  (FIRST: caps BLAS threads before numpy loads)

import json

import joblib
import numpy as np
import pandas as pd

S4_DIR = HERE / "S4_model_trained_on_2026-05-04-2026-09-24_val7030"
S4_HD_PIPELINE = S4_DIR / "kw30_tfidf_S4_FT_hd" / "tfidf_fitted_pipeline.joblib"
S4_BD_PIPELINE = S4_DIR / "kw50_tfidf_S4_FT_bd" / "tfidf_fitted_pipeline.joblib"
S4_REFERENCE = S4_DIR / "reference_scores.json"
OUTPUTS = ("kw30_tfidf_S4_FT_hd", "kw50_tfidf_S4_FT_bd")
N_REF = 40

# Same edge cases kw15_ft_train.py probes: HTML + no body, empty everything, and a body
# full of the newswire plumbing strip_wire_boilerplate removes.
REF_PROBES = [
    {"ID": "probe-html-no-body",
     "Headline": "<b>Acme Corp</b> Announces FDA Approval of its Lead Drug Candidate",
     "Body": ""},
    {"ID": "probe-empty", "Headline": "", "Body": ""},
    {"ID": "probe-wire",
     "Headline": "Acme Corp Announces $10 Million Registered Direct Offering",
     "Body": ("NEW YORK, Sept. 28, 2026 (GLOBE NEWSWIRE) -- Acme Corp (NASDAQ: ACME) today "
              "announced a registered direct offering of 2,000,000 shares at $5.00 per share. "
              "https://www.globenewswire.com/Tracker?data=abc123 H.C. Wainwright & Co. is "
              "acting as the exclusive placement agent. Contact: ir@acme.com www.acme.com")},
]


def reference_rows(df: pd.DataFrame, hd_pipe, bd_pipe) -> list[dict]:
    rng = np.random.RandomState(7)
    pick = sorted(rng.choice(len(df), min(N_REF, len(df)), replace=False).tolist())
    rows = [{"ID": str(df["ID"].iloc[i]),
             "Headline": "" if pd.isna(df["Headline"].iloc[i]) else str(df["Headline"].iloc[i]),
             "Body": "" if pd.isna(df["Body"].iloc[i]) else str(df["Body"].iloc[i])}
            for i in pick] + [dict(p) for p in REF_PROBES]
    for r in rows:
        hd = C.prep_headline(r["Headline"])
        bd = C.prep_body400_kw(r["Body"])
        r["kw30_tfidf_S4_FT_hd"] = round(float(hd_pipe.predict_proba([hd])[0][1]), 6)
        r["kw50_tfidf_S4_FT_bd"] = round(float(bd_pipe.predict_proba([bd])[0][1]), 6)
    return rows


def main() -> int:
    df = C.load_corpus()
    snap = json.loads(C.SNAPSHOT_META.read_text())
    if df.attrs.get("sha256") != snap["sha256"]:
        sys.exit("refusing: the corpus cache does not match the FM3 snapshot sha256")
    print(f"=== corpus: {len(df)} rows, sessions {df['session'].min()} -> {df['session'].max()}, "
          f"snapshot sha256 {snap['sha256'][:12]}")
    hd_pipe = joblib.load(S4_HD_PIPELINE)
    bd_pipe = joblib.load(S4_BD_PIPELINE)
    rows = reference_rows(df, hd_pipe, bd_pipe)
    ref = {"note": "raw Headline / Body cells -> the S4 outputs through the live kw15 prep "
                   "(fm3_common.prep_headline / prep_body400_kw, == o54_engine._kw15_prep); "
                   "Orchestrator5.5's o55_s4kw.py must reproduce every value exactly at "
                   "startup. Unstratified seeded sample (RandomState(7)) + 3 edge probes -- "
                   "a load/serve parity check, not a calibration check.",
           "outputs": list(OUTPUTS),
           "rows": rows}
    S4_REFERENCE.write_text(json.dumps(ref, indent=1))
    print(f"wrote {S4_REFERENCE} ({len(rows)} rows)")
    for r in rows[:3]:
        print(f"  {r['ID']}: hd={r['kw30_tfidf_S4_FT_hd']} bd={r['kw50_tfidf_S4_FT_bd']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
