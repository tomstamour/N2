#!/usr/bin/env python3
"""Turn a RetroUniverse-3 output into the corrected-baseline table that
``eval_rules.py --baselines`` / ``replay_mole.load_baseline_overrides`` read.

Why (2026-10-08). The nightly stock-list job stalled on 2026-09-30, 10-01 and
10-02, so the orchestrator kept serving ``stocks_universe_2026-09-29.tsv`` on
2026-10-01 and 10-02 (and 10-03). The two numbers the trade-mole takes from the
list — the normal time between trades (ITI) and the normal trade size — then
described the wrong session. RetroUniverse-3 rebuilds what the missing file
should have held; this script picks, for each labelled recording, the value the
orchestrator would have looked up.

Rules copied from the live code, so the replay sees what live would have seen:
  * RTH column when the arm time is 09:30 <= t < 16:00 ET, else ETH
    (Orchestrator5.5 ``_is_rth_now``). The arm time is the stamp in the file name.
  * ITI is the RAW value rounded to 1 decimal (the nightly's canonical column is
    round(raw, 1) on 838/838 rows of the 2026-09-29 file); trade size is the raw
    value (canonical == raw on 762/762 rows).
  * A raw value of 44444 (fetch failed) is kept as the sentinel; the live pipeline
    would have imputed it, which cannot be reproduced for a single day.

Usage:
    python make_baseline_overrides.py RETRO_OUTPUT.tsv OUT.csv [mole-outputs dir]
"""
import csv
import glob
import os
import re
import sys
from datetime import date, timedelta

import pandas as pd

SENTINEL = 44444.0
NAME_RE = re.compile(
    r"^(?:_(?P<lab>[a-z-]+)_)?trade-mole-table_(?P<sym>[A-Z0-9.\-]+)_(?P<day>\d{4}-\d{2}-\d{2})_"
    r"(?P<hh>\d{2})-(?P<mm>\d{2})-(?P<ss>\d{2})\.csv$")


def is_rth(hh: int, mm: int) -> bool:
    """Orchestrator5.5._is_rth_now(): (h == 9 and m >= 30) or (10 <= h < 16)."""
    return (hh == 9 and mm >= 30) or (10 <= hh < 16)


def main(retro_path, out_path, mole_dir):
    r = pd.read_csv(retro_path, sep="\t")
    r = r[r["Group"] == "target"]
    by_key = {(x["Symbol"], x["ArrivalDate"]): x for _, x in r.iterrows()}
    rows = []
    for f in sorted(glob.glob(os.path.join(mole_dir, "*trade-mole-table_*.csv"))):
        m = NAME_RE.match(os.path.basename(f))
        if not m:
            continue
        x = by_key.get((m["sym"], m["day"]))
        if x is None:
            continue
        side = "RTH" if is_rth(int(m["hh"]), int(m["mm"])) else "ETH"
        iti_raw = float(x[f"{side}_avgITI_sec_raw"])
        size_raw = float(x[f"{side}_TradeSize_raw" if side == "ETH" else "RTH_tradeSize_raw"])
        rows.append({
            "file": os.path.basename(f),
            "iti": iti_raw if iti_raw >= SENTINEL else round(iti_raw, 1),
            "size": size_raw,
            "side": side,
            "served_date": str(date.fromisoformat(m["day"]) - timedelta(days=1)),
            "RTH_avgITI_sec": round(float(x["RTH_avgITI_sec_raw"]), 1),
            "ETH_avgITI_sec": round(float(x["ETH_avgITI_sec_raw"]), 1),
            "RTH_tradeSize": float(x["RTH_tradeSize_raw"]),
            "ETH_TradeSize": float(x["ETH_TradeSize_raw"]),
            "source": "RetroUniverse-3 (ArrivalDate-1 session), 2026-10-08",
        })
    with open(out_path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len(rows)} recordings -> {out_path}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    main(sys.argv[1], sys.argv[2],
         sys.argv[3] if len(sys.argv) > 3 else os.path.normpath(os.path.join(here, "..", "mole-outputs")))
