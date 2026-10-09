#!/usr/bin/env python3
"""Replay the O5.2 trade gate over a labelled PRstats corpus.

Calls the REAL gate, not a copy of it: Orchestrator5.2._load_trigger_rules() parses
the rules file and _evaluate_trigger() decides each row. _evaluate_trigger's docstring
states it is injectable so an offline replay can score a candidate rule set - this is
that replay. Reimplementing the DSL here would silently diverge from live the first
time an operator used a feature this file did not know about.

Importable (`replay(df, rules_path)`) and runnable:

  /home/tom/venv/bin/python replay_gate.py \
      --input outputs/07_..._gateReplay.tsv \
      --rules ../orchestrator5/orchestrator_trigger_rules.txt \
      --verify-against ../orchestrator5/tables

!! The corpus must carry Prosus_*_hd (score_prosus_headlines.py) and EarningScore
   (tools/Earning_Scorer/earning_scorer.py) or CORE/FinBERTSolo/EARNINGS can never
   fire and every result is quietly wrong.

!!!! THE EARNINGS RULE CANNOT BE REPLAYED FAITHFULLY, and it is not this script's
   fault. The PRstats corpus `Body` column is HARD-CAPPED AT 400 WORDS (4084 of 4376
   rows sit at exactly 400; each is a strict prefix of the body live received). The
   headline models and kw15 are unaffected - kw15's body pipeline caps at 400 words
   too, so it agrees to <=0.011 - but the earnings scorer scans the WHOLE body for
   figures, so it sees a different document offline than live. Measured against
   tables/ for 2026-08-12..14: 42 of 70 earnings rows score differently and 9 (12.9%)
   cross the `>= 4` gate cut. Verified fidelity, same window:

       non-Earnings_Results   89/89   100.0%
       Earnings_Results       64/70    91.4%

   Treat any EARNINGS-branch number from this corpus as an estimate, and never
   calibrate that rule here without an untruncated body.

!! `fired_<Rule>` MEANS "THIS RULE FIRED THE GATE", NOT "ITS CONDITIONS HELD". Since
   the class-whitelist precondition (2026-08-17) those differ: a REQUIRE line can block
   a row whose CORE conditions all passed, and that row has fired_CORE False while its
   `detail` cell shows a `CORE:YES` group. Reading `detail` and reading `fired_*` will
   therefore give you different per-rule counts, and `fired_*` is the one that matches
   what traded. REQUIRE rules get no fired_ column at all.
"""
from __future__ import annotations

import argparse
import glob
import importlib.util as ilu
import os
import sys
from pathlib import Path

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
O52_PATH = SCRIPTS_DIR / "orchestrator5" / "Orchestrator5.2.py"
LIVE_RULES = SCRIPTS_DIR / "orchestrator5" / "orchestrator_trigger_rules.txt"

_spec = ilu.spec_from_file_location("O52_replay", str(O52_PATH))
O52 = ilu.module_from_spec(_spec)
_spec.loader.exec_module(O52)

# source -> {gate key: corpus column}. Mirrors _TRIGGER_FIELDS' (source, key) pairs;
# since 2026-08-09 the gate field names ARE the TSV column names, so most of this is
# an identity map and the interesting part is which of the six dicts a key lives in.
SRC_COLS = {
    "finbert": {"pos": "Prosus_pos_hd", "neu": "Prosus_neu_hd", "neg": "Prosus_neg_hd",
                "ss": "Prosus_ss_hd", "lab": "Prosus_lab_hd"},
    "fmb":     {"pos": "FinMarBa_pos_hd", "ind": "FinMarBa_ind_hd",
                "neg": "FinMarBa_neg_hd", "ss": "FinMarBa_ss_hd",
                "lab": "FinMarBa_lab_hd"},
    "flang":   {"pos": "FLANG_pos_hd", "neu": "FLANG_neu_hd", "neg": "FLANG_neg_hd",
                "ss": "FLANG_ss_hd", "lab": "FLANG_lab_hd"},
    "kw":      {"kw15_proba_bd": "kw15_proba_bd", "kw15_proba_hd": "kw15_proba_hd"},
    "earn":    {"FineClass": "FineClass", "EarningScore": "EarningScore"},
    "row":     {"Float": "Float", "MktCap": "MktCap",
                "LastDailyClose": "LastDailyClose", "itiBaseline": "itiBaseline",
                "tradeSizeBaseline": "tradeSizeBaseline", "Exchange": "Exchange",
                "Symbol": "Symbol", "TickerCount": "TickerCount"},
}
# The gate's _KIND_STR fields. Everything else is numeric (EarningScore included -
# it is INTEGER POINTS, and _KIND_INT accepts the float the gate coerces).
_STR_FIELDS = {("finbert", "lab"), ("fmb", "lab"), ("flang", "lab"),
               ("earn", "FineClass"), ("row", "Exchange"), ("row", "Symbol")}


def _is_str_field(src: str, key: str) -> bool:
    return (src, key) in _STR_FIELDS


def _cell(v):
    """A blank/NaN cell is None - the gate's 'a None ALWAYS fails' path, which is
    exactly right for a score a model never produced."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    s = str(v).strip()
    return None if s == "" or s.lower() == "nan" else s


def _num(v):
    c = _cell(v)
    if c is None:
        return None
    try:
        return float(c)
    except ValueError:
        return None


def replay(df: pd.DataFrame, rules_path: str | Path = LIVE_RULES) -> pd.DataFrame:
    """Score every row of `df`. Returns a frame indexed like `df` with columns
    is_ok / label / detail / missing_blocked / fired_<RuleName> per FIRING rule.

    A REQUIRE precondition gets NO fired_ column: it permits, it never fires. A row
    it blocked has is_ok False and every fired_* False even where `detail` shows a
    `:YES` group - see the module docstring."""
    O52._load_trigger_rules(str(rules_path))
    # REQUIRE rules never fire, so a fired_<Name> for one would be False on every row
    # and would land in the rule list analyze_class_trading_value.py derives from these
    # columns, where it reads as a dead rule. getattr keeps this file loadable against
    # a pre-2026-08-17 Orchestrator5.2.py that has no _Rule.kind.
    _FIRE = getattr(O52, "_RULE_FIRE", "fire")
    rule_names = [r.name for r in O52._trigger_rules
                  if getattr(r, "kind", _FIRE) == _FIRE]

    # Only build the dicts the loaded gate actually reads.
    needed = {name for name, _ in O52._trigger_fields}
    packs = {}
    for src, mapping in SRC_COLS.items():
        keep = {k: c for k, c in mapping.items()
                if c in df.columns and (k in needed or c in needed)}
        packs[src] = {k: [(_cell if _is_str_field(src, k) else _num)(v)
                          for v in df[c]]
                      for k, c in keep.items()}

    out = []
    n = len(df)
    for i in range(n):
        vals = {src: {k: col[i] for k, col in pack.items()}
                for src, pack in packs.items()}
        is_ok, label, detail, blocked = O52._evaluate_trigger(
            vals["finbert"], vals["fmb"], vals["flang"],
            vals["kw"], vals["earn"], vals["row"])
        rec = {"is_ok": is_ok, "label": label, "detail": detail,
               "missing_blocked": blocked}
        got = set(label.split("_")) if is_ok else set()
        for rn in rule_names:
            rec[f"fired_{rn}"] = rn in got
        out.append(rec)
    return pd.DataFrame(out, index=df.index)


def verify_against_live(df: pd.DataFrame, res: pd.DataFrame, tables_dir: str,
                        since: str) -> bool:
    """Compare replayed YES/NO with what O5.2 actually wrote, over the window where
    the CURRENT gate was live. Earlier rows ran superseded gates and are excluded -
    a mismatch there would mean nothing.

    This is the check that licenses every downstream number. If it fails, the replay
    is wrong and the analysis is void."""
    files = sorted(glob.glob(os.path.join(tables_dir, "news_output_*.tsv")))
    if not files:
        print(f"!! no news_output_*.tsv under {tables_dir} - CANNOT VERIFY")
        return False
    live = pd.concat([pd.read_csv(f, sep="\t", dtype=str, low_memory=False)
                      for f in files], ignore_index=True)
    live = live[live["ArrivalDate"] >= since]
    live["live_yes"] = live["Trigger"].astype(str).str.startswith("YES")
    live = live.drop_duplicates("ID")

    m = (df[["ID", "ArrivalDate"]].assign(replay_yes=res["is_ok"].values)
         .merge(live[["ID", "live_yes", "Symbol", "Trigger"]], on="ID", how="inner"))
    if m.empty:
        print(f"!! no overlapping IDs at/after {since} - CANNOT VERIFY")
        return False
    bad = m[m.replay_yes != m.live_yes]
    print(f"[verify] {len(m)} rows overlap live tables since {since}: "
          f"{len(m) - len(bad)} agree, {len(bad)} DISAGREE")
    # Split by earnings: the 400-word Body cap corrupts EarningScore and nothing else
    # (see the module docstring), so a mismatch there is a known corpus defect while a
    # mismatch anywhere else is a real replay bug.
    if "FineClass" in df.columns:
        fc = df.set_index("ID")["FineClass"]
        m["earn"] = m["ID"].map(fc).eq("Earnings_Results")
        for lbl, s in (("non-Earnings_Results", m[~m.earn]),
                       ("Earnings_Results", m[m.earn])):
            if len(s):
                d = int((s.replay_yes != s.live_yes).sum())
                print(f"  {lbl:<22} {len(s) - d}/{len(s)}  {1 - d / len(s):.1%}")
        bad = m[(m.replay_yes != m.live_yes) & (~m.earn)]
        print("  (earnings mismatches are the known 400-word Body cap, not a bug)")
    for _, r in bad.head(10).iterrows():
        print(f"  {r.Symbol} {r.ID}: replay={r.replay_yes} live={r.live_yes}")
        print(f"    live Trigger: {str(r.Trigger)[:240]}")
    return bad.empty


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--rules", default=str(LIVE_RULES))
    ap.add_argument("--verify-against", default=None,
                    help="orchestrator5/tables dir - compare with live Trigger")
    ap.add_argument("--verify-since", default="2026-08-18",
                    help="first date the CURRENT gate was live. Bump this on EVERY "
                         "gate change: rows written under the previous gate are not "
                         "wrong, they just ran a different one, and comparing across "
                         "the boundary reports them all as disagreements.")
    ap.add_argument("--out", default=None, help="write per-row verdicts to this TSV")
    a = ap.parse_args()

    df = pd.read_csv(a.input, sep="\t", dtype=str, low_memory=False)
    res = replay(df, a.rules)
    print(f"{len(df)} rows | fired {int(res.is_ok.sum())} "
          f"({res.is_ok.mean():.2%}) | missing-blocked {int(res.missing_blocked.sum())}")
    for c in [c for c in res.columns if c.startswith("fired_")]:
        print(f"  {c[6:]:<12} {int(res[c].sum())}")

    ok = True
    if a.verify_against:
        ok = verify_against_live(df, res, a.verify_against, a.verify_since)
    if a.out:
        pd.concat([df[["ID", "Symbol", "ArrivalDate"]], res], axis=1).to_csv(
            a.out, sep="\t", index=False)
        print(f"wrote {a.out}")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
