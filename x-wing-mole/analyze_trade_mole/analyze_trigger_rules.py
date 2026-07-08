#!/usr/bin/env python3
"""
Trigger-rule simulator for labeled trade-mole recordings (2026-07-07 refit).

Replays every labeled recording in mole-outputs/ row-by-row through the
should_fire() rule variants and reports, per file, which rule fires first and
when — so a rule change can be validated against the FULL labeled history
(positives must still fire, negatives must not) before touching
trade-mole-2.1.py.

Labels understood (both conventions):
  * new  (2026-06-16+):  _pos_*.csv / _neg_*.csv
  * june (2026-06-02..12): _positive*/_negative* .txt/.csv; the `_augmented`
    twin is preferred when present (it carries the bsln/prev_gap columns).
    Timed positives ("_positive-HH:MM:SS.mmm_...") only count as caught when
    the fire lands at/after the labeled onset; earlier fires print as PRE-.

Column reconstruction for old schemas:
  * bslnITI_on_last3trades_avrgITI (column added 2026-06-14) is rebuilt from
    measured_ITIsec + hist_baseline_avg_iti, replaying the live dedup rule
    (twin print on the other RT channel < 5 ms later forward-fills). The
    rebuild is verified exact against the recorded column on new files.
  * price_change_since_start_pct falls back to (price - first_price)/first_price.

Run with the venv python (pandas):
    /home/tom/venv/bin/python analyze_trigger_rules.py

Calibration notes for the 2026-07-07 thresholds live in
mole-outputs/trigger-analysis-2026-07-07.md. Supersedes analyze_iti_threshold.py
(date-locked to the 06-08..11 window, doesn't know the _pos_/_neg_ labels).
"""

import glob
import os
import re
from datetime import datetime, time as dtime
from statistics import median

import numpy as np
import pandas as pd

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "mole-outputs")

SENTINEL = 44444.0
DEDUP_EPSILON_SEC = 0.005

# ---------------------------------------------------------------------------
# Rule variants. Keys:
#   guardC : Rule C skipped when the ITI baseline is missing/<=0/44444
#   veto   : price-drop veto fraction (None = off)
#   A      : quote_updates_10s threshold
#   Agate  : Rule A minimum dollar_rate_5s (None = ungated)
#   Riti   : Rule D measured_ITI cutoff (None = off); trades_in_5s >= 3 required
#   Rrate/Rpc : Rule E dollar_rate_5s and price-rise thresholds (None = off)
# The LIVE-pre-0707 variant reproduces the buggy pre-refit behaviour.
# ---------------------------------------------------------------------------
VARIANTS = {
    "LIVE-pre-0707 (B|C-unguarded|A11)": dict(guardC=False, veto=None, A=11,
                                              Agate=None, Riti=None, Rrate=None, Rpc=None),
    "CURRENT-2026-07-07 (veto,B,Cg,A11&$2,D,E)": dict(guardC=True, veto=0.005, A=11,
                                                      Agate=2.0, Riti=0.3, Rrate=10.0, Rpc=0.01),
}


def onset_of(base):
    m = re.search(r"(\d{2}:\d{2}:\d{2}\.\d+)", base)
    if not m:
        return None
    hh, mm, rest = m.group(1).split(":")
    sec = float(rest)
    return dtime(int(hh), int(mm), int(sec), int(round((sec % 1) * 1e6)))


def discover():
    """Return {picked_path: (label, onset, generation)} — one file per recording,
    preferring the _augmented twin."""
    groups = {}
    pats = ["_pos_*.csv", "_neg_*.csv", "_positive*", "_negative*"]
    for pat in pats:
        for f in glob.glob(os.path.join(OUTDIR, pat)):
            if not (f.endswith(".csv") or f.endswith(".txt")):
                continue
            base = re.sub(r"_augmented", "", os.path.basename(f))
            base = re.sub(r"\.(csv|txt)$", "", base)
            groups.setdefault(base, []).append(f)
    picked = {}
    for base, fs in sorted(groups.items()):
        f = sorted(fs, key=lambda x: ("augmented" not in x, x))[0]
        name = os.path.basename(f)
        lab = "pos" if ("_pos_" in name or "positive" in name) else "neg"
        gen = "new" if name.startswith(("_pos_", "_neg_")) else "june"
        picked[f] = (lab, onset_of(base), gen)
    return picked


def num(s):
    return pd.to_numeric(s, errors="coerce")


def load(path):
    df = pd.read_csv(path, low_memory=False)
    tr = df[df.event_type == "TRADE"].copy()
    if not len(tr):
        return None
    for c in ["price", "session_age_sec", "local_arrival_time", "measured_ITIsec",
              "hist_baseline_avg_iti", "bsln_over_prev_trd_gap", "quote_updates_10s",
              "dollar_rate_5s", "trades_in_5s", "price_change_since_start_pct"]:
        tr[c] = num(tr[c]) if c in tr.columns else np.nan
    tr["rt_source"] = tr["rt_source"].astype(str) if "rt_source" in tr.columns else ""
    if tr["price_change_since_start_pct"].isna().all():
        p0 = tr["price"].iloc[0]
        tr["price_change_since_start_pct"] = (tr["price"] - p0) / p0 if p0 else np.nan
    tod = []
    for s in tr.get("local_arrival_iso", pd.Series([""] * len(tr))).astype(str):
        try:
            tod.append(datetime.fromisoformat(s).time())
        except ValueError:
            tod.append(None)
    tr["tod"] = tod

    hist = tr["hist_baseline_avg_iti"].iloc[0]
    valid_bsln = pd.notna(hist) and hist > 0 and abs(hist - SENTINEL) > 1e-6

    # Rebuild the accepted-trade series -> bslnITI_on_last3 + measured ITI,
    # mirroring _process_trade_event (dedup twins forward-fill, don't advance).
    t = tr["local_arrival_time"].values
    src = tr["rt_source"].values
    mrec = tr["measured_ITIsec"].values
    l3 = np.full(len(tr), np.nan)
    mm = np.full(len(tr), np.nan)
    last_t = last_s = None
    pm = None
    pl3 = np.nan
    first = True
    for i in range(len(tr)):
        ti = t[i] if pd.notna(t[i]) else None
        dup = (last_t is not None and src[i] != last_s and ti is not None
               and (ti - last_t) < DEDUP_EPSILON_SEC)
        if dup:
            l3[i] = pl3
            mm[i] = mm[i - 1] if i > 0 else np.nan
            continue
        if pd.notna(mrec[i]):
            m = mrec[i]
        elif ti is not None and last_t is not None and not first:
            m = ti - last_t
        else:
            m = np.nan
        v = np.nan
        if pd.notna(m) and pm is not None and pd.notna(pm) and pd.notna(hist):
            avg2 = (m + pm) / 2.0
            if avg2 > 0:
                v = hist / avg2
        l3[i] = v
        mm[i] = m
        pl3 = v
        pm = m
        first = False
        if ti is not None:
            last_t = ti
            last_s = src[i]
    tr["l3"] = l3
    tr["m_iti"] = mm

    # Sanity: where the file already records the column, the rebuild must agree.
    if "bslnITI_on_last3trades_avrgITI" in df.columns:
        rec = num(tr["bslnITI_on_last3trades_avrgITI"])
        both = pd.notna(rec) & pd.notna(tr["l3"])
        if both.any() and not np.allclose(rec[both], tr.loc[both, "l3"], rtol=0.05):
            print(f"  !! l3 rebuild mismatch in {os.path.basename(path)}")
    return dict(tr=tr, valid_bsln=valid_bsln)


def first_fire(d, onset, V):
    """Replay should_fire per TRADE row. Returns (rule, session_age) or (None, None).
    'PRE-<rule>' marks a fire before a timed positive's labeled onset."""
    tr = d["tr"]
    vb = d["valid_bsln"]
    for _, r in tr.iterrows():
        pc = r["price_change_since_start_pct"]
        if V["veto"] is not None and pd.notna(pc) and pc < -V["veto"]:
            continue
        why = None
        if vb and pd.notna(r["bsln_over_prev_trd_gap"]) and r["bsln_over_prev_trd_gap"] > 100:
            why = "B"
        elif (vb or not V["guardC"]) and pd.notna(r["l3"]) and r["l3"] > 100:
            why = "C"
        elif (pd.notna(r["quote_updates_10s"]) and r["quote_updates_10s"] >= V["A"]
              and (V["Agate"] is None
                   or (pd.notna(r["dollar_rate_5s"]) and r["dollar_rate_5s"] >= V["Agate"]))):
            why = "A"
        elif (V["Riti"] is not None and pd.notna(r["m_iti"]) and r["m_iti"] <= V["Riti"]
              and pd.notna(r["trades_in_5s"]) and r["trades_in_5s"] >= 3):
            why = "D"
        elif (V["Rrate"] is not None and pd.notna(r["dollar_rate_5s"])
              and r["dollar_rate_5s"] >= V["Rrate"]
              and pd.notna(pc) and pc >= V["Rpc"]):
            why = "E"
        if why:
            if onset is not None and (r["tod"] is None or r["tod"] < onset):
                return "PRE-" + why, r["session_age_sec"]
            return why, r["session_age_sec"]
    return None, None


def main():
    files = discover()
    data = []
    skipped = []
    for f, (lab, onset, gen) in sorted(files.items()):
        d = load(f)
        m = re.search(r"(?:table_)?([A-Z]{2,6})_20", os.path.basename(f))
        name = (m.group(1) if m else os.path.basename(f)[:8]) + ("" if gen == "new" else "*")
        if d is None:
            skipped.append(name)
            continue
        d.update(label=lab, onset=onset, gen=gen, name=name)
        data.append(d)
    npos = sum(1 for d in data if d["label"] == "pos")
    print(f"Loaded {len(data)} unique labeled recordings "
          f"({npos} pos / {len(data) - npos} neg); skipped (no TRADE rows): {skipped}")

    for vn, V in VARIANTS.items():
        print("\n" + "=" * 90)
        print(f"VARIANT: {vn}")
        print("=" * 90)
        for setname, gen in (("NEW (_pos_/_neg_)", "new"), ("JUNE (regression)", "june")):
            print(f"--- {setname} ---")
            for lab in ("pos", "neg"):
                rows = [d for d in data if d["label"] == lab and d["gen"] == gen]
                fired, missed, lats = [], [], []
                for d in rows:
                    why, age = first_fire(d, d["onset"], V)
                    if why and not why.startswith("PRE"):
                        fired.append(f"{d['name']}:{why}@{age:.2f}")
                        lats.append(0.0 if d["onset"] is not None else age)
                    else:
                        missed.append(d["name"] + (f"({why}@{age:.1f})" if why else ""))
                med = f"  medLat={median(lats):.2f}s" if (lab == "pos" and lats) else ""
                print(f"  {lab}: {len(fired)}/{len(rows)} fire{med}")
                if fired:
                    print("     " + "  ".join(fired))
                if missed:
                    print(f"     no fire: {', '.join(missed)}")


if __name__ == "__main__":
    main()
