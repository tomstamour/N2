#!/usr/bin/env python3
"""
evaluate_earning_scorer.py

Calibrate EarnScoreNorm and honestly evaluate the Earning_Scorer against
fromPR_High(%) >= 30 on the Earnings_Results slice of the PR-stats master.

Population (2026-08-14 rebuild): fromPR_status == "ok" AND
FineClass == "Earnings_Results".  That is exactly the population the live
EARNINGS gate leg sees, and the TSV's FineClass reproduces at 99.98% from
tools/categorizer/PrClassifier.classify_row, so calibrating on the column is
the same as calibrating on what the orchestrator would compute.

Protocol (repo-wide honest-block convention):
    train = ArrivalDate <  2026-06-20    (everything is fitted here)
    test  = ArrivalDate >= 2026-06-20    (scored ONCE, at the end)

Two things this script does NOT do, on purpose:

  * It never writes earn_score_params.json.  EarningScore is the field the live
    trade gate reads (`EarningScore >= 4` in orchestrator_trigger_rules.txt);
    re-calibrating it here would silently move a live cut.  The frozen path is
    asserted against embedded golden cases at startup instead -- see
    assert_frozen_path().  Only earn_norm_params.json is written.
  * It has no encoder-comparison section.  The 2026-08-13 master carries only
    headline encoder columns (*_ss_hd) -- no *_Body_* columns and no
    FinBERT/Prosus column at all -- so the 2026-08-01 report's
    "rules + distilroberta_Body" finding is not reproducible from this corpus
    and is not silently re-derived from a different feature.

Overfit control.  The 2026-08-01 calibration picked thresholds by maximising
train-block AUC over a 972-combo grid with 26 positives, and the report blamed
part of the 0.848 -> 0.597 train/test drop on that.  Here thresholds AND the
regularisation strength are picked by 5-fold CV *inside* the train block, so
the selection criterion is itself out-of-sample.  The test block is untouched
until every parameter is frozen.

Run:  /home/tom/venv/bin/python evaluate_earning_scorer.py --from-master
      /home/tom/venv/bin/python evaluate_earning_scorer.py          # slice exists
"""

import argparse
import itertools
import json
import math
import os
import random

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

import earning_scorer as es

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_MASTER = os.path.join(
    SCRIPT_DIR, "..", "..", "price_stats_loader", "outputs",
    "07_PRstats_2026-05-04-2026-08-13_CoffeeFilter_fromPRhigh_11EncScored_kw15Scored.tsv")
SLICE = es.DEFAULT_INPUT
SPLIT_DATE = "2026-06-20"
TARGET = 30.0
TARGET2 = 15.0
NORM_OUT = es.DEFAULT_NORM_FILE
QA_OUT = os.path.join(SCRIPT_DIR, "earn_qa_dump.tsv")
SCORED_OUT = SLICE.replace(".tsv", "_earnScored.tsv")

EARN_CLASS = "Earnings_Results"

# Threshold grid.  Same values as the 2026-08-01 run so the two calibrations are
# comparable; what changed is that the winner is chosen by inner CV, not by
# train-block fit.
GRID = {
    "rev_lo": [10.0, 20.0, 30.0],
    "rev_hi": [40.0, 50.0, 75.0, 100.0],
    "prof_thr": [20.0, 30.0, 50.0],
    "margin_bps_thr": [100.0, 200.0, 300.0],
    "rev_fcap_thr": [0.10, 0.25, 0.50],
    "prof_fcap_thr": [0.05, 0.10, 0.20],
}
C_GRID = [0.05, 0.1, 0.25, 0.5, 1.0]
WEIGHT_STEP = 0.05          # weights are rounded to this, so the table is readable
N_FOLDS = 5

# --------------------------------------------------------------------------
# frozen-path guard
#
# These are synthetic, self-contained, and exercise every family that feeds
# EarningScore.  They do NOT depend on the corpus, so they keep working as the
# master grows.  If one of these scores changes, families A-I or
# earn_score_params.json moved -- which means the live gate's `EarningScore >= 4`
# now selects a different set of press releases.  That is never a silent change.
# --------------------------------------------------------------------------
FROZEN_GOLDENS = [
    ("plain results", "Acme Corp Reports Second Quarter 2026 Financial Results",
     "Acme Corp today reported financial results for the quarter. "
     "Revenue was $10.0 million.", None, 0),
    ("headline growth", "Acme Corp Revenue Up 60% in Second Quarter 2026",
     "Revenue increased to $16.0 million compared to $10.0 million a year ago.",
     None, 4),
    ("rev decline", "Acme Corp Reports Q2 2026 Results",
     "Revenue decreased 22% to $7.8 million compared to $10.0 million in the "
     "prior year.", None, -1),
    ("guidance raise", "Acme Corp Reports Q2 2026 Results",
     "The Company raised its full year 2026 revenue guidance. Revenue grew 12% "
     "year-over-year.", None, 4),
    ("guidance cut", "Acme Corp Reports Q2 2026 Results",
     "The Company lowered its full year outlook for 2026.", None, -2),
    ("profit flip", "Acme Corp Reports Q2 2026 Results",
     "Net income of $2.1 million compared to a net loss of $0.8 million in the "
     "prior year period.", None, 2),
    ("record + margin", "Acme Corp Reports Record Quarterly Revenue",
     "Record quarterly revenue of $25.0 million. Gross margin expanded 670 "
     "basis points to 23.5%.", None, 3),
    ("neg flags", "Acme Corp Reports Q2 2026 Results",
     "The report includes a going concern qualification and a material weakness "
     "in internal controls.", None, -2),
    ("size relative", "Acme Corp Reports Q2 2026 Results",
     "Revenue was $30.0 million for the quarter. Net income of $9.9 million.",
     5_000_000.0, 2),
    ("scheduling", "Acme Corp to Report Second Quarter 2026 Results on August 12, 2026",
     "Acme Corp will report results on August 12, 2026 and host a conference "
     "call.", None, 0),
]


def assert_frozen_path():
    """EarningScore must be bit-identical to the released live behaviour."""
    params = es.load_params()
    bad = []
    for name, head, body, mc, want in FROZEN_GOLDENS:
        got, _ = es.score_metrics(es.extract_metrics(head, body, mc), params)
        if got != want:
            bad.append(f"    {name}: expected {want}, got {got}")
    if bad:
        raise SystemExit(
            "FROZEN PATH CHANGED -- EarningScore no longer matches its golden "
            "cases:\n" + "\n".join(bad) +
            "\n\nThe live gate reads this field (`EarningScore >= 4` in "
            "orchestrator5/orchestrator_trigger_rules.txt). Either revert the "
            "change to families A-I / earn_score_params.json, or deliberately "
            "retune the live gate and update these goldens in the same commit.")
    print(f"frozen-path guard: OK ({len(FROZEN_GOLDENS)} golden cases, "
          f"EarningScore unchanged)")


# --------------------------------------------------------------------------
# data
# --------------------------------------------------------------------------
def build_slice(master, out):
    df = pd.read_csv(master, sep="\t", low_memory=False)
    n0 = len(df)
    df = df[(df["fromPR_status"] == "ok") & (df["FineClass"] == EARN_CLASS)]
    df.to_csv(out, sep="\t", index=False)
    print(f"slice: {n0} master rows -> {len(df)} rows "
          f"(fromPR_status==ok AND FineClass=={EARN_CLASS})\n  wrote {out}")
    return out


def extract_all(df):
    return [es.extract_metrics(str(r.get("Headline", "")), str(r.get("Body", "")),
                               es._row_size_dollars(r))
            for _, r in df.iterrows()]


NAMES = list(es.NORM_FEATURES)


def indicator_matrix(feats, thresholds):
    """Reference implementation: calls the shared definitions row by row."""
    X = np.zeros((len(feats), len(NAMES)), dtype=float)
    for i, f in enumerate(feats):
        ind = es.norm_indicators(f, thresholds)
        for j, n in enumerate(NAMES):
            X[i, j] = 1.0 if ind[n] else 0.0
    return X, NAMES


def precompute_signals(feats):
    """Continuous quantities + threshold-free booleans, once.

    The grid re-thresholds these ~15k times; calling the per-row lambdas each
    time made calibration take longer than the rest of the script combined.
    NaN compares False under numpy just as _ge() returns False, so the
    vectorised form is equivalent -- and assert_fast_path_matches() proves it
    against the shared definitions rather than trusting that argument.
    """
    def col(key, prefix="earn_"):
        return np.array([f.get(prefix + key, np.nan) if
                         isinstance(f.get(prefix + key, np.nan), (int, float))
                         else np.nan for f in feats], dtype=float)

    hl_rev = col("rev_growth_pct", "earnH_")
    best_rev = np.fmax(col("rev_growth_pct"), hl_rev)
    best_prof = np.fmax(col("profit_growth_pct"),
                        col("profit_growth_pct", "earnH_"))
    b = lambda fn: np.array([bool(fn(f)) for f in feats])
    return {
        "hl_rev": hl_rev, "best_rev": best_rev, "best_prof": best_prof,
        "margin": col("margin_delta_bps"),
        "rev_fcap": col("rev_to_floatcap"),
        "prof_fcap": col("profit_to_floatcap"),
        "hl_has_pct": b(lambda f: f.get("earnH_has_pct", 0)),
        "hl_has_metric": b(lambda f: f.get("earnH_has_metric", 0)),
        "record": b(lambda f: (f.get("earn_record_count", 0)
                               + f.get("earnH_record_count", 0)) > 0),
        "guid_raise": b(lambda f: max(f.get("earn_guidance", 0),
                                      f.get("earnH_guidance", 0)) > 0),
        "guid_cut": b(lambda f: min(f.get("earn_guidance", 0),
                                    f.get("earnH_guidance", 0)) < 0),
        "expects_record": b(lambda f: f.get("earn_expects_record", 0)
                            or f.get("earnH_expects_record", 0)),
        "profit_flip": b(lambda f: f.get("earn_profit_flip", 0)),
        "neg_flag": b(lambda f: f.get("earn_neg_flags", 0) > 0),
        "no_structure": b(lambda f: f.get("earn_n_hits", 0) == 0),
        "sched_only": b(lambda f: f.get("earn_scheduling_only", 0)),
    }


def fast_matrix(sig, thr):
    with np.errstate(invalid="ignore"):
        cols = {
            "hl_rev_growth": sig["hl_rev"] >= thr["rev_lo"],
            "hl_has_pct": sig["hl_has_pct"],
            "hl_has_metric": sig["hl_has_metric"],
            "rev_growth_lo": sig["best_rev"] >= thr["rev_lo"],
            "rev_growth_hi": sig["best_rev"] >= thr["rev_hi"],
            "rev_decline": sig["best_rev"] < 0,
            "prof_growth": sig["best_prof"] >= thr["prof_thr"],
            "record": sig["record"],
            "guid_raise": sig["guid_raise"],
            "guid_cut": sig["guid_cut"],
            "expects_record": sig["expects_record"],
            "profit_flip": sig["profit_flip"],
            "margin_up": sig["margin"] >= thr["margin_bps_thr"],
            "neg_flag": sig["neg_flag"],
            "rev_fcap": sig["rev_fcap"] >= thr["rev_fcap_thr"],
            "prof_fcap": sig["prof_fcap"] >= thr["prof_fcap_thr"],
            "no_structure": sig["no_structure"],
            "sched_only": sig["sched_only"],
        }
    return np.column_stack([cols[n].astype(float) for n in NAMES])


def assert_fast_path_matches(feats, sig):
    """The vectorised grid must agree with earning_scorer's own definitions."""
    for thr in ({"rev_lo": 10.0, "rev_hi": 50.0, "prof_thr": 50.0,
                 "margin_bps_thr": 300.0, "rev_fcap_thr": 0.5,
                 "prof_fcap_thr": 0.2},
                {"rev_lo": 30.0, "rev_hi": 100.0, "prof_thr": 20.0,
                 "margin_bps_thr": 100.0, "rev_fcap_thr": 0.1,
                 "prof_fcap_thr": 0.05}):
        ref, _ = indicator_matrix(feats, thr)
        if not np.array_equal(ref, fast_matrix(sig, thr)):
            raise SystemExit("fast indicator path disagrees with "
                             "earning_scorer.norm_indicators -- fix fast_matrix")
    print("fast-path guard: OK (vectorised indicators == shared definitions)")


# --------------------------------------------------------------------------
# reporting helpers
# --------------------------------------------------------------------------
def block_table(name, score, y, t, cuts):
    base = y.mean()
    print(f"\n--- {name}: n={len(y)}  positives={y.sum()}  base rate={base:.3f} ---")
    print(f"{'cutoff':>8} {'n>=cut':>7} {'hits':>5} {'prec':>6} {'recall':>7} "
          f"{'lift':>6}  medianMove")
    for cut in cuts:
        sel = score >= cut
        if sel.sum() == 0:
            continue
        prec = y[sel].mean()
        rec = y[sel].sum() / max(y.sum(), 1)
        cs = f"{cut:.2f}" if isinstance(cut, float) else str(cut)
        print(f"{cs:>8} {sel.sum():>7} {y[sel].sum():>5} {prec:>6.3f} {rec:>7.3f} "
              f"{prec / base if base else float('nan'):>6.2f}  {np.median(t[sel]):.2f}%")
    if 0 < y.sum() < len(y):
        print(f"  AUC={roc_auc_score(y, score):.3f}")


def precision_at_top(score, y, n):
    idx = np.argsort(-score, kind="stable")[:n]
    return y[idx].mean(), y[idx].sum()


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--from-master", nargs="?", const=DEFAULT_MASTER, default=None,
                    metavar="PATH",
                    help="rebuild the Earnings_Results slice from the PR-stats "
                         "master before evaluating (default master: %(default)s)")
    ap.add_argument("--input", default=SLICE, help="slice TSV to evaluate")
    args = ap.parse_args()

    assert_frozen_path()

    src = args.input
    if args.from_master:
        src = build_slice(os.path.abspath(args.from_master), SLICE)

    df = pd.read_csv(src, sep="\t", low_memory=False)
    df["t"] = pd.to_numeric(df["fromPR_High(%)"], errors="coerce")
    df = df[(df["fromPR_status"] == "ok") & (df["FineClass"] == EARN_CLASS)]
    df = df.reset_index(drop=True)
    y = (df["t"] >= TARGET).astype(int).values
    y15 = (df["t"] >= TARGET2).astype(int).values
    t = df["t"].values
    is_train = (df["ArrivalDate"] < SPLIT_DATE).values

    print(f"\nrows={len(df)}  train={is_train.sum()} ({y[is_train].sum()} pos)  "
          f"test={(~is_train).sum()} ({y[~is_train].sum()} pos)  target >= {TARGET}%")
    print("monthly base rates (>=30%):")
    m = (df.assign(pos=y).groupby(df["ArrivalDate"].str[:7])["pos"]
         .agg(["count", "sum", "mean"]))
    print(m.round(3).to_string())

    print("\nextracting features ...")
    feats = extract_all(df)
    sig = precompute_signals(feats)
    assert_fast_path_matches(feats, sig)
    sig_tr = {k: v[is_train] for k, v in sig.items()}

    # ---- calibration: inner CV on the train block ONLY --------------------
    ntc = int(np.prod([len(v) for v in GRID.values()]))
    print(f"\ncalibrating EarnScoreNorm ({ntc} threshold combos x {len(C_GRID)} C "
          f"x {N_FOLDS}-fold inner CV, train block only) ...")
    tr_idx = np.where(is_train)[0]
    ftr = [feats[i] for i in tr_idx]
    ytr = y[tr_idx]
    skf = StratifiedKFold(n_splits=N_FOLDS, shuffle=True, random_state=42)
    folds = list(skf.split(np.zeros(len(ytr)), ytr))

    keys = list(GRID)
    best = (-1.0, None, None)          # (cv auc, thresholds, C)
    for combo in itertools.product(*(GRID[k] for k in keys)):
        thr = dict(zip(keys, combo))
        if thr["rev_hi"] <= thr["rev_lo"]:
            continue
        Xtr = fast_matrix(sig_tr, thr)
        for C in C_GRID:
            aucs = []
            for a, b in folds:
                if ytr[a].sum() == 0 or ytr[b].sum() == 0:
                    continue
                # liblinear here purely for speed: ~2.4 ms/fit vs ~26 ms for
                # lbfgs, which is the difference between a 1-minute and a
                # 10-minute grid. Selection only needs the AUC *ranking*; the
                # weights that actually ship are refitted below with lbfgs, so
                # liblinear's regularised intercept never reaches the output.
                clf = LogisticRegression(C=C, class_weight="balanced",
                                         max_iter=2000, solver="liblinear")
                clf.fit(Xtr[a], ytr[a])
                aucs.append(roc_auc_score(ytr[b], clf.decision_function(Xtr[b])))
            if aucs and np.mean(aucs) > best[0]:
                best = (float(np.mean(aucs)), thr, C)
    cv_auc, thresholds, C = best
    print(f"best inner-CV AUC={cv_auc:.3f}  thresholds={thresholds}  C={C}")

    # refit on the full train block, then round the weights
    Xtr, names = fast_matrix(sig_tr, thresholds), NAMES
    clf = LogisticRegression(C=C, class_weight="balanced", max_iter=2000)
    clf.fit(Xtr, ytr)
    raw_w = dict(zip(names, clf.coef_[0]))
    # round(., 4) strips the binary-float tail so the JSON reads 0.35, not
    # 0.35000000000000003 -- these weights are meant to be read by a human.
    snap = lambda v: round(round(v / WEIGHT_STEP) * WEIGHT_STEP, 4)
    weights = {k: snap(v) for k, v in raw_w.items()}
    bias = snap(float(clf.intercept_[0]))
    norm = {"thresholds": thresholds, "weights": weights, "bias": bias}

    X_all = fast_matrix(sig, thresholds)
    unrounded = X_all @ np.array([raw_w[n] for n in names]) + clf.intercept_[0]
    nscore = np.array([es.score_norm(f, norm)[0] for f in feats])
    print(f"weight rounding to {WEIGHT_STEP}: train AUC "
          f"{roc_auc_score(ytr, unrounded[tr_idx]):.4f} (unrounded) -> "
          f"{roc_auc_score(ytr, nscore[tr_idx]):.4f} (rounded)")

    with open(NORM_OUT, "w") as fh:
        json.dump(norm, fh, indent=2)
    print(f"wrote {NORM_OUT}   (earn_score_params.json deliberately NOT touched)")

    print(f"\n{'indicator':<16}{'weight':>8}{'fires':>7}{'rate':>7}"
          f"{'prec30':>8}{'lift':>7}")
    base_all = y.mean()
    for j, n in enumerate(names):
        fired = X_all[:, j] > 0
        if fired.sum() == 0:
            print(f"{n:<16}{weights[n]:>8.2f}{0:>7}")
            continue
        print(f"{n:<16}{weights[n]:>8.2f}{fired.sum():>7}{fired.mean():>7.3f}"
              f"{y[fired].mean():>8.3f}{y[fired].mean() / base_all:>7.2f}")
    print(f"{'(bias)':<16}{bias:>8.2f}")

    # ---- the frozen score, for comparison --------------------------------
    params = es.load_params()
    score = np.array([es.score_metrics(f, params)[0] for f in feats], dtype=float)

    # ---- performance ------------------------------------------------------
    ncuts = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
    icuts = [1, 2, 3, 4, 5, 6, 7]
    print(f"\n================ EarnScoreNorm vs >= {TARGET}% ================")
    block_table("TRAIN (<%s)" % SPLIT_DATE, nscore[is_train], y[is_train],
                t[is_train], ncuts)
    block_table("TEST (>=%s)" % SPLIT_DATE, nscore[~is_train], y[~is_train],
                t[~is_train], ncuts)
    block_table("FULL", nscore, y, t, ncuts)

    print(f"\n================ EarningScore (FROZEN) vs >= {TARGET}% ================")
    block_table("TRAIN (<%s)" % SPLIT_DATE, score[is_train], y[is_train],
                t[is_train], icuts)
    block_table("TEST (>=%s)" % SPLIT_DATE, score[~is_train], y[~is_train],
                t[~is_train], icuts)

    print("\n================ head-to-head on the TEST block ================")
    print(f"{'score':<24}{'AUC>=30':>9}{'AUC>=15':>9}{'@top10':>9}{'@top20':>9}"
          f"{'@top40':>9}")
    for label, s in (("EarningScore (frozen)", score), ("EarnScoreNorm (new)", nscore)):
        row = f"{label:<24}{roc_auc_score(y[~is_train], s[~is_train]):>9.3f}" \
              f"{roc_auc_score(y15[~is_train], s[~is_train]):>9.3f}"
        for n in (10, 20, 40):
            p, h = precision_at_top(s[~is_train], y[~is_train], n)
            row += f"{p:>7.3f}({h})"
        print(row)

    print(f"\n================ stability check vs >= {TARGET2}% ================")
    block_table("TEST EarnScoreNorm", nscore[~is_train], y15[~is_train],
                t[~is_train], ncuts)

    # ---- per-structure univariate table -----------------------------------
    print("\n================ text structures ranked (full sample) ================")
    print(f"{'structure':<18} {'n':>4} {'rate':>6} {'prec>=30':>9} {'lift':>6} "
          f"{'medMove':>8}")
    rows = []
    for j, n in enumerate(names):
        fired = X_all[:, j] > 0
        if fired.sum() == 0:
            continue
        prec = y[fired].mean()
        rows.append((n, fired.sum(), fired.mean(), prec, prec / base_all,
                     np.median(t[fired])))
    rows.sort(key=lambda r: -r[4])
    for n, cnt, rate, prec, lift, med in rows:
        print(f"{n:<18} {cnt:>4} {rate:>6.3f} {prec:>9.3f} {lift:>6.2f} {med:>7.2f}%")

    # ---- report-only learned challenger -----------------------------------
    # Continuous earn_* features, not the indicators. Fitted on train, reported
    # on test, and deliberately NOT persisted: nothing sklearn-shaped may end up
    # on the live import path.
    print("\n================ learned challenger (report only, not shipped) ========")
    cont = [c for c in es.FEATURE_COLS]
    Xc = np.array([[float(f.get(c, np.nan) if f.get(c) is not None else np.nan)
                    for c in cont] for f in feats])
    Xc_tr = Xc[tr_idx]
    med = np.nanmedian(np.where(np.isinf(Xc_tr), np.nan, Xc_tr), axis=0)
    med = np.where(np.isnan(med), 0.0, med)
    Xc = np.where(np.isnan(Xc) | np.isinf(Xc), med, Xc)
    mu, sd = Xc[tr_idx].mean(0), Xc[tr_idx].std(0) + 1e-9
    Xc = (Xc - mu) / sd
    lg = LogisticRegression(C=0.1, class_weight="balanced", max_iter=5000)
    lg.fit(Xc[tr_idx], ytr)
    pc = lg.decision_function(Xc)
    print(f"logistic on {len(cont)} continuous earn_* features (C=0.1):")
    print(f"  train AUC={roc_auc_score(ytr, pc[tr_idx]):.3f}  "
          f"test AUC={roc_auc_score(y[~is_train], pc[~is_train]):.3f}")
    for n in (10, 20):
        p, h = precision_at_top(pc[~is_train], y[~is_train], n)
        pn, hn = precision_at_top(nscore[~is_train], y[~is_train], n)
        print(f"  test precision@top{n}: challenger {p:.3f} ({h})  vs "
              f"EarnScoreNorm {pn:.3f} ({hn})")

    # ---- outputs ----------------------------------------------------------
    out = df.drop(columns=["t"]).copy()
    fdf = pd.DataFrame([{c: f.get(c) for c in es.FEATURE_COLS} for f in feats])
    out = pd.concat([out, fdf], axis=1)
    out["EarningScore"] = score.astype(int)
    out["EarnBreakdown"] = [es.score_metrics(f, params)[1] for f in feats]
    out["EarnScoreNorm"] = nscore
    out["EarnNormBreakdown"] = [es.score_norm(f, norm)[1] for f in feats]
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
            "block": "train" if is_train[i] else "test",
            "Symbol": df["Symbol"].iloc[i], "ArrivalDate": df["ArrivalDate"].iloc[i],
            "fromPR_High(%)": t[i],
            "EarningScore": int(score[i]), "EarnScoreNorm": round(nscore[i], 4),
            "EarnBreakdown": es.score_metrics(f, params)[1],
            "EarnNormBreakdown": es.score_norm(f, norm)[1],
            "Headline": str(df["Headline"].iloc[i])[:120], "hits": sents,
        })
    pd.DataFrame(qa_rows).to_csv(QA_OUT, sep="\t", index=False)
    print(f"wrote {QA_OUT}  ({len(qa_rows)} rows: {len(pos_idx)} positives + 30 random)")


if __name__ == "__main__":
    main()
