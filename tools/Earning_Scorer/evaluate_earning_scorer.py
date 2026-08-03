#!/usr/bin/env python3
"""
evaluate_earning_scorer.py

Calibrate + honestly evaluate earning_scorer.py against fromPR_High(%) >= 30
on the Earnings_Results PR-stats file.

Protocol (matches the repo's established honest-block convention):
    train = ArrivalDate <  2026-06-20   (regex thresholds calibrated here ONLY)
    test  = ArrivalDate >= 2026-06-20   (never touched during calibration)

Steps:
    1. extract earn_* features once (param-independent) via earning_scorer
    2. small grid over rule thresholds, maximizing ROC-AUC of the additive
       score on the TRAIN block; freeze winner -> earn_score_params.json
    3. report: score-bucket tables, precision/recall/lift at cutoffs, AUC,
       precision@topN -- on train, test and full sample, with per-block base
       rates (the >=30% base rate shifts May -> June/July); secondary >=15
       target as a stability check
    4. per-family univariate stats (fire rate / precision / lift) = the
       "which text structures predict the move" table
    5. encoder comparison: single-feature AUC of every *_sentiment_score
       column (headline and body), then a tiny logistic combo of
       EarningScore + best train-block encoder
    6. QA dump of all positives + 30 random rows -> earn_qa_dump.tsv

Run:  /home/tom/venv/bin/python evaluate_earning_scorer.py
"""

import itertools
import json
import math
import os
import random

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

import earning_scorer as es

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
INPUT = es.DEFAULT_INPUT
SPLIT_DATE = "2026-06-20"
TARGET = 30.0
TARGET2 = 15.0
PARAMS_OUT = es.DEFAULT_PARAMS_FILE
QA_OUT = os.path.join(SCRIPT_DIR, "earn_qa_dump.tsv")
SCORED_OUT = INPUT.replace(".tsv", "_earnScored.tsv")

GRID = {
    "rev_lo": [10.0, 20.0, 30.0],
    "rev_hi": [40.0, 50.0, 75.0, 100.0],
    "prof_thr": [20.0, 30.0, 50.0],
    "margin_bps_thr": [100.0, 200.0, 300.0],
    "rev_fcap_thr": [0.10, 0.25, 0.50],
    "prof_fcap_thr": [0.05, 0.10, 0.20],
}


def extract_all(df):
    feats = []
    for _, r in df.iterrows():
        f = es.extract_metrics(str(r.get("Headline", "")), str(r.get("Body", "")),
                               es._row_size_dollars(r))
        feats.append(f)
    return feats


def scores_for(feats, params):
    return np.array([es.score_metrics(f, params)[0] for f in feats], dtype=float)


def block_table(name, score, y, t):
    base = y.mean()
    print(f"\n--- {name}: n={len(y)}  positives={y.sum()}  base rate={base:.3f} ---")
    print(f"{'cutoff':>7} {'n>=cut':>7} {'hits':>5} {'prec':>6} {'recall':>7} {'lift':>6}  medianMove")
    for cut in [1, 2, 3, 4, 5, 6, 7]:
        sel = score >= cut
        if sel.sum() == 0:
            continue
        prec = y[sel].mean()
        rec = y[sel].sum() / max(y.sum(), 1)
        print(f"{cut:>7} {sel.sum():>7} {y[sel].sum():>5} {prec:>6.3f} {rec:>7.3f} "
              f"{prec / base if base else float('nan'):>6.2f}  {np.median(t[sel]):.2f}%")
    if y.sum() and y.sum() < len(y):
        print(f"  AUC={roc_auc_score(y, score):.3f}")


def precision_at_top(score, y, n):
    idx = np.argsort(-score, kind="stable")[:n]
    return y[idx].mean(), y[idx].sum()


def main():
    df = pd.read_csv(INPUT, sep="\t")
    df["t"] = pd.to_numeric(df["fromPR_High(%)"], errors="coerce")
    df = df[df["fromPR_status"] == "ok"].reset_index(drop=True)
    y = (df["t"] >= TARGET).astype(int).values
    y15 = (df["t"] >= TARGET2).astype(int).values
    t = df["t"].values
    is_train = (df["ArrivalDate"] < SPLIT_DATE).values

    print(f"rows={len(df)}  train={is_train.sum()} ({y[is_train].sum()} pos)  "
          f"test={(~is_train).sum()} ({y[~is_train].sum()} pos)  target >= {TARGET}%")
    print("monthly base rates (>=30%):")
    m = df.assign(pos=y).groupby(df["ArrivalDate"].str[:7])["pos"].agg(["count", "sum", "mean"])
    print(m.round(3).to_string())

    print("\nextracting features ...")
    feats = extract_all(df)

    # ---- calibration on train only -------------------------------------
    print(f"\ncalibrating thresholds on train (grid size "
          f"{np.prod([len(v) for v in GRID.values()])}) ...")
    base_params = dict(es.BUILTIN_PARAMS)
    best_auc, best_combo = -1.0, None
    keys = list(GRID)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        p = dict(base_params)
        p.update(dict(zip(keys, combo)))
        if p["rev_hi"] <= p["rev_lo"]:
            continue
        sc = scores_for([feats[i] for i in np.where(is_train)[0]], p)
        auc = roc_auc_score(y[is_train], sc)
        if auc > best_auc:
            best_auc, best_combo = auc, dict(zip(keys, combo))
    params = dict(base_params)
    params.update(best_combo)
    print(f"best train AUC={best_auc:.3f} with {best_combo}")
    with open(PARAMS_OUT, "w") as fh:
        json.dump(params, fh, indent=2)
    print(f"wrote {PARAMS_OUT}")

    score = scores_for(feats, params)

    # ---- performance tables --------------------------------------------
    print(f"\n================ EarningScore vs >= {TARGET}% ================")
    block_table("TRAIN (<%s)" % SPLIT_DATE, score[is_train], y[is_train], t[is_train])
    block_table("TEST (>=%s)" % SPLIT_DATE, score[~is_train], y[~is_train], t[~is_train])
    block_table("FULL", score, y, t)
    for n in (10, 20):
        p, h = precision_at_top(score[~is_train], y[~is_train], n)
        print(f"  test precision@top{n}: {p:.3f} ({h} hits)")

    print(f"\n================ stability check vs >= {TARGET2}% ================")
    block_table("TEST (>=%s)" % SPLIT_DATE, score[~is_train], y15[~is_train], t[~is_train])

    # ---- per-family univariate stats (the text-structures table) --------
    print("\n================ text structures ranked (full sample) ================")
    fcols = {
        "body rev growth >= rev_lo": lambda f: (f.get("earn_rev_growth_pct") or float("nan")) >= params["rev_lo"],
        "body rev growth >= rev_hi": lambda f: (f.get("earn_rev_growth_pct") or float("nan")) >= params["rev_hi"],
        "headline rev growth >= rev_lo": lambda f: (f.get("earnH_rev_growth_pct") or float("nan")) >= params["rev_lo"],
        "profit growth >= prof_thr": lambda f: max((f.get("earn_profit_growth_pct") or float("nan")),
                                                   (f.get("earnH_profit_growth_pct") or float("nan"))) >= params["prof_thr"],
        "record claim": lambda f: (f.get("earn_record_count", 0) + f.get("earnH_record_count", 0)) > 0,
        "guidance raise": lambda f: max(f.get("earn_guidance", 0), f.get("earnH_guidance", 0)) > 0,
        "guidance cut": lambda f: min(f.get("earn_guidance", 0), f.get("earnH_guidance", 0)) < 0,
        "expects record": lambda f: (f.get("earn_expects_record", 0) or f.get("earnH_expects_record", 0)),
        "profit flip": lambda f: f.get("earn_profit_flip", 0) > 0,
        "margin +>= thr bps": lambda f: (f.get("earn_margin_delta_bps") or float("nan")) >= params["margin_bps_thr"],
        "rev/floatcap >= thr": lambda f: (f.get("earn_rev_to_floatcap") or float("nan")) >= params["rev_fcap_thr"],
        "profit/floatcap >= thr": lambda f: (f.get("earn_profit_to_floatcap") or float("nan")) >= params["prof_fcap_thr"],
        "any negative flag": lambda f: f.get("earn_neg_flags", 0) > 0,
        "rev growth negative": lambda f: (f.get("earn_rev_growth_pct") or float("nan")) < 0,
        "scheduling-only PR": lambda f: f.get("earn_scheduling_only", 0) == 1,
        "headline has %": lambda f: f.get("earnH_has_pct", 0) == 1,
    }
    base = y.mean()
    rows = []
    for name, fn in fcols.items():
        fired = np.array([bool(fn(f)) if not (isinstance(fn(f), float) and math.isnan(fn(f))) else False
                          for f in feats])
        if fired.sum() == 0:
            continue
        prec = y[fired].mean()
        rows.append((name, fired.sum(), prec, prec / base, np.median(t[fired])))
    rows.sort(key=lambda r: -r[3])
    print(f"{'structure':<30} {'n':>4} {'prec>=30':>9} {'lift':>6} {'medMove':>8}")
    for name, n, prec, lift, med in rows:
        print(f"{name:<30} {n:>4} {prec:>9.3f} {lift:>6.2f} {med:>7.2f}%")

    # ---- encoder comparison ---------------------------------------------
    print("\n================ encoder sentiment columns (AUC vs >=30%) ================")
    enc_cols = [c for c in df.columns if c.endswith("_sentiment_score")]
    res = []
    for c in enc_cols:
        v = pd.to_numeric(df[c], errors="coerce")
        mask = v.notna().values
        if y[mask].sum() < 3 or y[mask].sum() == mask.sum():
            continue
        a_tr = roc_auc_score(y[mask & is_train], v[mask & is_train])
        a_te = roc_auc_score(y[mask & ~is_train], v[mask & ~is_train])
        res.append((c, a_tr, a_te))
    res.sort(key=lambda r: -r[1])
    print(f"{'column':<42} {'trainAUC':>9} {'testAUC':>8}")
    for c, a_tr, a_te in res:
        print(f"{c:<42} {a_tr:>9.3f} {a_te:>8.3f}")
    print(f"{'EarningScore (rules)':<42} "
          f"{roc_auc_score(y[is_train], score[is_train]):>9.3f} "
          f"{roc_auc_score(y[~is_train], score[~is_train]):>8.3f}")

    # tiny combo: EarningScore + best train-block encoder
    best_enc = res[0][0]
    v = pd.to_numeric(df[best_enc], errors="coerce").fillna(0.0).values
    X = np.column_stack([score, v])
    mu, sd = X[is_train].mean(0), X[is_train].std(0) + 1e-9
    Xs = (X - mu) / sd
    clf = LogisticRegression(class_weight="balanced", max_iter=1000)
    clf.fit(Xs[is_train], y[is_train])
    proba = clf.predict_proba(Xs)[:, 1]
    print(f"\ncombo = logistic(EarningScore + {best_enc}) fit on train:")
    print(f"  train AUC={roc_auc_score(y[is_train], proba[is_train]):.3f}  "
          f"test AUC={roc_auc_score(y[~is_train], proba[~is_train]):.3f}")
    for n in (10, 20):
        p, h = precision_at_top(proba[~is_train], y[~is_train], n)
        ps, hs = precision_at_top(score[~is_train], y[~is_train], n)
        print(f"  test precision@top{n}: combo {p:.3f} ({h})  vs rules alone {ps:.3f} ({hs})")

    # ---- scored TSV + QA dump -------------------------------------------
    out = df.drop(columns=["t"]).copy()
    fdf = pd.DataFrame([{c: f.get(c) for c in es.FEATURE_COLS} for f in feats])
    out = pd.concat([out, fdf], axis=1)
    out["EarningScore"] = score
    out["EarnBreakdown"] = [es.score_metrics(f, params)[1] for f in feats]
    out.to_csv(SCORED_OUT, sep="\t", index=False)
    print(f"\nwrote {SCORED_OUT}")

    random.seed(42)
    pos_idx = list(np.where(y == 1)[0])
    rnd_idx = random.sample([i for i in range(len(df)) if i not in pos_idx], 30)
    qa_rows = []
    for i in pos_idx + rnd_idx:
        f = feats[i]
        sents = "; ".join(dict.fromkeys(
            f"[{k}:{m}={v:.1f}] {s[:110]}" for k, m, v, s in
            (f["_body_hits"] + f["_head_hits"])[:6]))
        qa_rows.append({
            "set": "POS" if y[i] else "RND",
            "Symbol": df["Symbol"].iloc[i], "ArrivalDate": df["ArrivalDate"].iloc[i],
            "fromPR_High(%)": t[i], "EarningScore": score[i],
            "EarnBreakdown": es.score_metrics(f, params)[1],
            "Headline": str(df["Headline"].iloc[i])[:120], "hits": sents,
        })
    pd.DataFrame(qa_rows).to_csv(QA_OUT, sep="\t", index=False)
    print(f"wrote {QA_OUT}  ({len(qa_rows)} rows: {len(pos_idx)} positives + 30 random)")


if __name__ == "__main__":
    main()
