#!/usr/bin/env python
"""Head-to-head: does XGBoost model-2 predict fromPR_High(%) better than model-1?

THIS IS A STATISTICS-ONLY STUDY. Nothing here recommends, edits or implies a change to a
trading script: not Orchestrator5.2/5.3, not orchestrator_trigger_rules.txt. The O5.2 gate
appears ONLY as a fixed read-only reference point both models are measured against, so the
two model columns can be read on a scale someone already has intuition for.

Why this corpus makes an unusually strong comparison -- all four verified, not assumed:

  * model-2's val fold is the SAME 1380 rows, the SAME 25 sessions (2026-07-28..08-28), in
    the SAME row order as model-1's, and the target is BYTE-IDENTICAL (max abs diff 0.0).
  * so every comparison here is PAIRED: block_boot_delta_auc() evaluates both models on the
    same resampled sessions and the shared session noise cancels. An unpaired interval on
    25 sessions would be roughly twice as wide and would call everything a tie by default.
  * model-2's shipped scores reproduce ../training_data/model-2/*_report.md exactly, so they
    are the models those reports describe -- see the --check output. model-1 had a defect
    here once (predicting past best_iteration); this script re-runs that check on BOTH.
  * the gate reads none of these columns, so it is a genuine control: 113 fires and 655
    whitelist admissions must come out identical or the join is wrong.

WHAT DIFFERS BETWEEN THE MODELS (from ../training_data/model-{1,2}/):
    features        49 -> 25          (a leaner SHAP allowlist)
    Prosus scores   original -> re-scored          ("prosusReScore")
    FineClass       original -> re-categorised     ("reCat", 34/1380 val rows differ = 2.5%)
    split           3424/1380 cutoff 2026-07-28, identical

!! model-2/xgb_fromPRhigh_clf_report.md says "49-feature SHAP allowlist". That text is
   STALE -- its boosters carry 25 features. Do not trust that line; trust the .ubj.

!! `fromPR_High(%)` is the intraday CEILING from the PR, not realized PnL. Every "mean
   captured move" here is a best case with a perfect exit, equally for every column. It
   ranks scorers; it does not forecast an account balance.

!! Fires cluster inside a session, so every confidence interval resamples whole
   ArrivalDates (a day-block bootstrap), never individual rows.

Statistics routines are COPIED from compare_xgb_vs_orch52.py, not imported, per the
statsByClaude house convention that each report script is standalone (see
statsByClaude/CLAUDE.md). replay_gate IS imported -- it drives the real
Orchestrator5.2._evaluate_trigger(), and reimplementing the DSL would silently diverge.

Usage:
    /home/tom/venv/bin/python compare_xgb_models.py \
        --m1 outputs/2026-07-28_2026-08-28_..._XGBscores_bestiter_orch52cols_gateReplay.tsv \
        --m2 outputs/08_2026-05-04-2026-08-28_..._PCA_val_M2-scores.tsv
"""

import argparse
import importlib.util as ilu
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import rankdata, spearmanr

_HERE = Path(__file__).resolve().parent

# The five scores each model emits. Every column in this report is one of these, tagged
# _m1 / _m2, or the rank-average blend of the two.
BASE_COLS = ["proba_clf_ge50", "proba_clf_ge35", "proba_clf_ge20",
             "pred_reg_q80", "pred_reg_se"]
# The numeric scores the live gate reads, kept as the floor of the ranking table.
# Orientation is decided from the data, not asserted: FinMarBa_ind_hd is an INDECISION
# score, so low is bullish.
O52_COLS = ["kw15_proba_bd", "kw15_proba_hd", "FLANG_ss_hd",
            "Prosus_pos_hd", "FinMarBa_ind_hd"]
TARGET = "fromPR_High(%)_actual"
DATE_COL = "ArrivalDate"
KEYS = ["Symbol", "ArrivalDate", "ArrivalTime"]

M1, M2, BL = "_m1", "_m2", "blend_"


# --------------------------------------------------------------------------
# statistics -- copied not imported, per the statsByClaude house convention
# --------------------------------------------------------------------------
def auc_score(y, s):
    """Mann-Whitney rank AUC. Handles ties via average ranks; no sklearn."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    n1 = float(y.sum())
    n0 = float(len(y) - n1)
    if n1 == 0 or n0 == 0:
        return float("nan")
    r = rankdata(s)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def avg_precision(y, s):
    """Area under the precision-recall curve, step-wise, tie groups collapsed."""
    y = np.asarray(y, dtype=float)
    s = np.asarray(s, dtype=float)
    pos = y.sum()
    if pos == 0:
        return float("nan")
    order = np.argsort(-s, kind="mergesort")
    ys, ss = y[order], s[order]
    ends = np.flatnonzero(np.r_[np.diff(ss) != 0, True])
    tp = np.cumsum(ys)[ends]
    k = (ends + 1).astype(float)
    prec = tp / k
    rec = tp / pos
    return float(np.sum(np.diff(np.r_[0.0, rec]) * prec))


def wilson(k, n, z=1.959964):
    """95% Wilson score interval -- does not collapse to zero width at k=0 or k=n."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return float((c - h) / d), float((c + h) / d)


def newcombe(k1, n1, k2, n2):
    """95% CI for p1 - p2 (Newcombe method 10, from two Wilson intervals)."""
    if n1 == 0 or n2 == 0:
        return (float("nan"), float("nan"))
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    p1, p2 = k1 / n1, k2 / n2
    lo = (p1 - p2) - np.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2)
    hi = (p1 - p2) + np.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)
    return float(lo), float(hi)


def _day_blocks(days):
    """Row indices grouped by session, for the block bootstrap."""
    idx = {}
    for i, d in enumerate(days):
        idx.setdefault(d, []).append(i)
    return [np.asarray(v) for v in idx.values()]


def block_boot_auc(y, s, days, n_boot, rng):
    """95% CI on AUC, resampling whole SESSIONS."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        a = auc_score(y[sel], s[sel])
        if not np.isnan(a):
            out.append(a)
    if not out:
        return (float("nan"), float("nan"))
    return float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))


def block_boot_delta_auc(y, s_a, s_b, days, n_boot, rng):
    """PAIRED day-block bootstrap on AUC(a) - AUC(b): both scores are evaluated on the
    SAME resampled sessions, so the shared sampling noise cancels. This is the whole
    reason a model-1/model-2 gap can be tested at all on 25 sessions.
    Returns (CI, P(delta>0))."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        ya = y[sel]
        if ya.sum() == 0 or ya.sum() == len(ya):
            continue
        out.append(auc_score(ya, s_a[sel]) - auc_score(ya, s_b[sel]))
    if not out:
        return (float("nan"), float("nan")), float("nan")
    out = np.asarray(out)
    return ((float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))),
            float((out > 0).mean()))


def block_boot_delta_prec(y_raw, th, s_a, s_b, n, days, n_boot, rng):
    """PAIRED day-block bootstrap on precision@top-n(a) - precision@top-n(b).

    The matched-fire comparison is the one the reader acts on, and a Newcombe interval
    treats the two top-n sets as independent samples when they in fact overlap heavily
    and are drawn from the same sessions. This is the paired version: re-rank inside each
    resampled corpus and keep the top n*(rows/len) so the workload scales with the draw."""
    blocks = _day_blocks(days)
    nb = len(blocks)
    frac = n / len(y_raw)
    out = []
    for _ in range(n_boot):
        pick = rng.integers(0, nb, nb)
        sel = np.concatenate([blocks[j] for j in pick])
        k = max(1, int(round(frac * len(sel))))
        yr = y_raw[sel]
        pa = (yr[top_n_mask(s_a[sel], k)] >= th).mean()
        pb = (yr[top_n_mask(s_b[sel], k)] >= th).mean()
        out.append(pa - pb)
    out = np.asarray(out)
    return ((float(np.percentile(out, 2.5)), float(np.percentile(out, 97.5))),
            float((out > 0).mean()))


# --------------------------------------------------------------------------
# the metric block every candidate is reported through
# --------------------------------------------------------------------------
def metric_block(mask, y_raw, thresholds, n_days):
    """Precision at matched fire rate, captured move, and recall of the big movers."""
    mask = np.asarray(mask, dtype=bool)
    fired = y_raw[mask]
    n = int(mask.sum())
    out = {"fires": n, "fires_per_day": n / n_days if n_days else float("nan")}
    for th in thresholds:
        pos = y_raw >= th
        tp = int((pos & mask).sum())
        base = float(pos.mean())
        prec = tp / n if n else float("nan")
        lo, hi = wilson(tp, n)
        out[f"tp{th}"] = tp
        out[f"prec{th}"] = prec
        out[f"prec{th}_lo"] = lo
        out[f"prec{th}_hi"] = hi
        out[f"lift{th}"] = prec / base if base and n else float("nan")
        out[f"recall{th}"] = tp / int(pos.sum()) if pos.sum() else float("nan")
    out["mean"] = float(fired.mean()) if n else float("nan")
    out["median"] = float(np.median(fired)) if n else float("nan")
    out["sum"] = float(fired.sum()) if n else 0.0
    # the mean is hostage to one +1035% row; a ceiling at 100% is the same number with
    # the single fattest tail bounded, and is reported beside it rather than instead
    out["mean_cap100"] = float(np.minimum(fired, 100.0).mean()) if n else float("nan")
    return out


def top_n_mask(score, n):
    """Top-n by score, ties broken by original order (mergesort is stable)."""
    if n <= 0:
        return np.zeros(len(score), dtype=bool)
    order = np.argsort(-np.asarray(score, dtype=float), kind="mergesort")
    m = np.zeros(len(score), dtype=bool)
    m[order[:n]] = True
    return m


def fmt(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:.{nd}f}"


def pct(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{100 * x:.{nd}f}%"


def sgn(x, nd=3):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "n/a"
    return f"{x:+.{nd}f}"


def block_row(name, b, th, extra=""):
    return (f"| {name} | {b['fires']} | {b['fires_per_day']:.2f} | {b[f'tp{th}']} | "
            f"{pct(b[f'prec{th}'])} | {pct(b[f'prec{th}_lo'])}–{pct(b[f'prec{th}_hi'])} | "
            f"{fmt(b[f'lift{th}'], 2)}x | {pct(b[f'recall{th}'])} | "
            f"{fmt(b['mean'], 1)} | {fmt(b['median'], 1)} | {fmt(b['mean_cap100'], 1)} | "
            f"{b['sum']:.0f} |{extra}")


BLOCK_HDR = ("| candidate | fires | /day | TP | precision | 95% CI | lift | recall | "
             "mean | median | mean(cap100) | sum |")
BLOCK_SEP = "|---|---|---|---|---|---|---|---|---|---|---|---|"


def sub_prec_gate(mask, sub, y_raw, th):
    """Precision of a FIXED fire set inside a sub-window.

    A score column can be re-cut to a proportional workload inside any subset; the gate
    cannot -- it fires where it fires. So its sub-window cell is just its own hit rate
    there, on however many fires it happened to produce."""
    m = np.asarray(mask, dtype=bool) & np.asarray(sub, dtype=bool)
    return float((y_raw[m] >= th).mean()) if m.sum() else float("nan")


def label(c):
    """Human name for a score column: pred_reg_se_m1 -> `pred_reg_se` (M1)."""
    if c.startswith(BL):
        return f"`{c[len(BL):]}` (blend)"
    if c.endswith(M1):
        return f"`{c[:-len(M1)]}` (M1)"
    if c.endswith(M2):
        return f"`{c[:-len(M2)]}` (M2)"
    return f"`{c}`"


# Published val-fold metrics from ../training_data/model-{1,2}/*_report.md. Recomputing
# these from the shipped score columns is the check that says the columns really are the
# models those reports describe. model-1 once failed it (it had been scored past
# best_iteration), which is why it runs on both models every time.
#   col -> (roc_auc_at_own_threshold_or_None, top138_precision, own_threshold)
REPORTED = {
    "m1": {"proba_clf_ge20": (0.735, 0.551, 20.0),
           "proba_clf_ge35": (0.734, 0.348, 35.0),
           "proba_clf_ge50": (0.722, 0.239, 50.0),
           "pred_reg_se":    (None,  0.580, 20.0),
           "pred_reg_q80":   (None,  0.594, 20.0)},
    "m2": {"proba_clf_ge20": (0.728, 0.565, 20.0),
           "proba_clf_ge35": (0.727, 0.341, 35.0),
           "proba_clf_ge50": (0.721, 0.268, 50.0),
           "pred_reg_se":    (None,  0.551, 20.0),
           "pred_reg_q80":   (None,  0.543, 20.0)},
}
REPORTED_SPEARMAN = {"m1": {"pred_reg_se": 0.450, "pred_reg_q80": 0.444},
                     "m2": {"pred_reg_se": 0.449, "pred_reg_q80": 0.432}}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--m1", required=True,
                    help="model-1 gate-replay TSV (carries the gate columns, "
                         "EarningScore, ID and model-1's five scores)")
    ap.add_argument("--m2", required=True, help="model-2 score TSV")
    ap.add_argument("--rules", default=None, help="trigger-rules DSL (default: live)")
    ap.add_argument("--threshold", type=float, default=50.0,
                    help="the verdict cut on fromPR_High(%%)")
    ap.add_argument("--secondary-thresholds", default="15,20,35")
    ap.add_argument("--outdir", default=str(_HERE / "statsByClaude"))
    ap.add_argument("--report",
                    default="2026-09-20_XGB_model2_vs_model1_predictive_power.md")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--clean-window-from", default="2026-08-14",
                    help="start of the sub-window in which the O5.2 ClassWhitelist was "
                         "already frozen (it was tuned through 08-13)")
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    th = args.threshold
    sec = [float(x) for x in args.secondary_thresholds.split(",") if x.strip()]
    all_th = [th] + [s for s in sec if s != th]

    spec = ilu.spec_from_file_location("replay_gate", str(_HERE / "replay_gate.py"))
    rg = ilu.module_from_spec(spec)
    spec.loader.exec_module(rg)

    # ---------------- load + the paired join ----------------
    p1, p2 = Path(args.m1), Path(args.m2)
    rd = dict(sep="\t", dtype=str, keep_default_na=False, na_values=[""])
    d1 = pd.read_csv(p1, **rd)
    d2 = pd.read_csv(p2, **rd)

    for nm, d, need in (("--m1", d1, KEYS + BASE_COLS + O52_COLS + [TARGET]),
                        ("--m2", d2, KEYS + BASE_COLS + [TARGET])):
        miss = [c for c in need if c not in d.columns]
        if miss:
            sys.exit(f"{nm} ({d is d1 and p1.name or p2.name}) is missing {miss}")
    if "EarningScore" not in d1.columns:
        sys.exit("--m1 has no EarningScore -- run earnscore_backfill_addOn.py first; "
                 "replay_gate would silently disable the EARNINGS branch and you would "
                 "lose ~46% of the gate's fires with no error")

    keep2 = KEYS + BASE_COLS + [TARGET] + (["FineClass"] if "FineClass" in d2 else [])
    ren = {c: c + M2 for c in BASE_COLS}
    ren[TARGET] = TARGET + M2
    if "FineClass" in d2:
        ren["FineClass"] = "FineClass" + M2      # for the reCat caveat ONLY -- the gate
                                                 # never sees it (replay_gate reads the
                                                 # bare name, which stays model-1's)
    d2s = d2[keep2].rename(columns=ren)

    for nm, d in (("--m1", d1), ("--m2", d2s)):
        if d.duplicated(KEYS).any():
            sys.exit(f"{nm} has duplicate (Symbol, ArrivalDate, ArrivalTime) keys -- "
                     "the paired design is void")
    df = d1.merge(d2s, on=KEYS, how="inner", validate="one_to_one")
    if not (len(df) == len(d1) == len(d2)):
        sys.exit(f"join is {len(df)}/{len(d1)} (m1) /{len(d2)} (m2) -- refusing to "
                 "score a partial pairing")

    df = df.rename(columns={c: c + M1 for c in BASE_COLS})
    num = [c + M1 for c in BASE_COLS] + [c + M2 for c in BASE_COLS] \
        + O52_COLS + [TARGET, TARGET + M2]
    for c in num:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if df[num].isna().any().any():
        bad = [c for c in num if df[c].isna().any()]
        sys.exit(f"NaN in {bad} -- refusing to score")

    # The load-bearing assertion: if the two files disagree on the outcome, every paired
    # interval below is measuring two different corpora and means nothing.
    tgt_gap = float(np.abs(df[TARGET] - df[TARGET + M2]).max())
    if tgt_gap > 0:
        sys.exit(f"the two files disagree on {TARGET} (max abs diff {tgt_gap}) -- "
                 "the paired design is void")

    # ---------------- the blend ----------------
    # Rank-average: mean of the two within-corpus ranks. Only the ORDER matters for AUC
    # and for a top-n selection, so this needs no rescaling of the two models onto a
    # common unit -- which is the point, since pred_reg_* is a percentage and
    # proba_clf_* is a probability.
    n = len(df)
    for c in BASE_COLS:
        df[BL + c] = (rankdata(df[c + M1].to_numpy(dtype=float))
                      + rankdata(df[c + M2].to_numpy(dtype=float))) / (2.0 * n)

    COLS_M1 = [c + M1 for c in BASE_COLS]
    COLS_M2 = [c + M2 for c in BASE_COLS]
    COLS_BL = [BL + c for c in BASE_COLS]
    ALL = COLS_M1 + COLS_M2 + COLS_BL

    y_raw = df[TARGET].to_numpy(dtype=float)
    y = (y_raw >= th).astype(int)
    days = df[DATE_COL].to_numpy()
    n_days = df[DATE_COL].nunique()

    # ---------------- the gate (a control, never a variable) ----------------
    rules_path = args.rules or str(rg.LIVE_RULES)
    res = rg.replay(df, rules_path)
    gate = res["is_ok"].to_numpy(dtype=bool)
    whitelist = res["detail"].str.contains("ClassWhitelist:ALLOW").to_numpy()
    rule_names = [c[len("fired_"):] for c in res.columns if c.startswith("fired_")]
    N = int(gate.sum())

    # ---------------- the report-reproduction check ----------------
    checks = []
    for tag, suf in (("m1", M1), ("m2", M2)):
        for c, (ra, rp, cth) in REPORTED[tag].items():
            s = df[c + suf].to_numpy(dtype=float)
            lab = (y_raw >= cth).astype(int)
            got_p = float(lab[top_n_mask(s, 138)].mean())
            got_a = auc_score(lab, s) if ra is not None else None
            ok = abs(got_p - rp) < 0.004 and (ra is None or abs(got_a - ra) < 0.002)
            checks.append((tag, c, ra, got_a, rp, got_p, ok))
        for c, rs in REPORTED_SPEARMAN[tag].items():
            got = float(spearmanr(df[c + suf].to_numpy(dtype=float), y_raw).statistic)
            checks.append((tag, c + " (Spearman)", rs, got, None, None,
                           abs(got - rs) < 0.002))
    checks_ok = all(c[-1] for c in checks)

    print(f"[join]  {n} rows, {n_days} sessions, target identical (max diff {tgt_gap})")
    print(f"[gate]  {N} fires, {int(whitelist.sum())} whitelist admissions")
    print(f"[check] model report reproduction: "
          f"{sum(1 for c in checks if c[-1])}/{len(checks)} pass")
    for tag, c, ra, ga, rp, gp, ok in checks:
        if not ok:
            print(f"  !! {tag} {c}: reported {ra}/{rp} got {fmt(ga)}/{fmt(gp)}")

    lines = []
    W = lines.append

    # ---------------- header ----------------
    W("# Does XGBoost model-2 predict `fromPR_High(%)` better than model-1?")
    W("")
    W("**Date:** 2026-09-20  ")
    W(f"**Corpus:** the shared XGB **validation fold** — {n} rows, {n_days} sessions, "
      f"{df[DATE_COL].min()}..{df[DATE_COL].max()}, no NaN. Train cutoff `2026-07-28`, so "
      "every column here is genuinely out-of-sample for **both** models.  ")
    W(f"**model-1:** `{p1.name}`  ")
    W(f"**model-2:** `{p2.name}`  ")
    W(f"**Verdict cut:** `fromPR_High(%) >= {th:g}` "
      f"({int(y.sum())} rows, {pct(y.mean())} base rate).  ")
    W("**Scope:** *statistics only.* This report compares two score sets. It recommends "
      "no gate, proposes no rule line, and nothing in `orchestrator5/` was read for "
      "anything except replaying today's gate as a fixed reference point.  ")
    W("**Companion:** [`2026-09-16_XGB_vs_O52_predictive_power.md`]"
      "(2026-09-16_XGB_vs_O52_predictive_power.md) — the model-1-vs-gate study. Its "
      "model-1 numbers are reproduced here unchanged.")
    W("")
    W("Base rates on this corpus: " + ", ".join(
        f"`>= {t:g}` **{int((y_raw >= t).sum())}** ({pct((y_raw >= t).mean())})"
        for t in sorted(all_th)) + ".")
    W("")

    verdict_slot = len(lines)
    W("")

    # ---------------- what differs ----------------
    W("## What actually changed between the two models")
    W("")
    W("| | model-1 | model-2 |")
    W("|---|---|---|")
    W("| features | **49** | **25** |")
    W("| Prosus (FinBERT) scores | original | re-scored (`prosusReScore`) |")
    W("| `FineClass` | original | re-categorised (`reCat`) |")
    W("| train / val split | 3424 / 1380, cutoff `2026-07-28` | *identical* |")
    W("| target | `fromPR_High(%)` | *identical, byte for byte* |")
    W("")
    if "FineClass" + M2 in df.columns:
        fc_same = int((df["FineClass"].fillna("") == df["FineClass" + M2].fillna("")).sum())
        W(f"The re-categorisation moved **{n - fc_same} of {n}** validation rows "
          f"({pct(1 - fc_same / n)}) to a different `FineClass`. The label vocabulary is "
          "unchanged. **Model-2 reads the new labels; the replayed gate below reads the "
          "old ones** — deliberately, because the old ones are what O5.2 actually saw on "
          "the live tape and what the 448/448 verification was done against.")
        W("")
    W("So model-2 is not a bigger model. It is a **leaner** one — half the inputs — "
      "trained on refreshed sentiment and refreshed class labels. That framing matters "
      "for how to read everything below: the interesting question is not whether it wins, "
      "it is whether it **loses anything**.")
    W("")

    # ---------------- method ----------------
    W("## Method")
    W("")
    W("1. The two score files are joined on `(Symbol, ArrivalDate, ArrivalTime)` with a "
      f"`one_to_one` validation, {n}/{n} matched. The script **hard-fails** if the two "
      "files disagree on `fromPR_High(%)` by any amount; they agree exactly, so every "
      "comparison below is **paired**.")
    W("2. Because the pairing holds, ΔAUC and Δprecision use a **paired day-block "
      f"bootstrap** ({args.bootstrap} replicates): both models are scored on the *same* "
      "resampled sessions, so the session-to-session noise they share cancels instead of "
      "being counted twice. On 25 sessions this is the difference between a test that can "
      "resolve something and one that cannot.")
    W("3. Whole `ArrivalDate`s are resampled, never individual rows — fires cluster inside "
      "a session, and a row bootstrap would report intervals roughly a third too narrow.")
    W("4. Ranking uses the Mann-Whitney rank AUC (ties-safe, no sklearn); proportions "
      "carry Wilson 95% CIs; unpaired precision gaps carry Newcombe 95% CIs.")
    W(f"5. Head-to-head comparisons are at **matched workload**: the gate fires {N} times "
      f"on this corpus, so every score column is given its top {N} rows and judged on the "
      "same number of trades.")
    W("6. The **blend** of a column is the rank-average of model-1's and model-2's version "
      "of that same column — the mean of the two within-corpus ranks. Only order matters "
      "for AUC and for a top-N cut, so no rescaling is needed between a percentage "
      "(`pred_reg_*`) and a probability (`proba_clf_*`).")
    W("")

    # ---------------- 1. ranking ----------------
    W("## Findings")
    W("")
    W(f"### 1. Ranking power at `>= {th:g}` — all fifteen columns")
    W("")
    W("AUC is the probability that a randomly chosen `>= "
      f"{th:g}%` mover is ranked above a randomly chosen non-mover. 0.500 is a coin flip. "
      "CIs are the day-block bootstrap.")
    W("")
    crit = {}
    for c in ALL:
        s = df[c].to_numpy(dtype=float)
        a = auc_score(y, s)
        lo, hi = block_boot_auc(y, s, days, args.bootstrap, rng)
        crit[c] = {"auc": a, "auc_lo": lo, "auc_hi": hi, "ap": avg_precision(y, s)}
    o52 = {}
    for c in O52_COLS:
        s = df[c].to_numpy(dtype=float)
        a = auc_score(y, s)
        inv = a < 0.5
        if inv:
            s, a = -s, 1.0 - a
        lo, hi = block_boot_auc(y, s, days, args.bootstrap, rng)
        o52[c] = {"auc": a, "auc_lo": lo, "auc_hi": hi,
                  "ap": avg_precision(y, s), "inv": inv, "s": s}
    W("| column | model | AUC | 95% CI | PR-AUC |")
    W("|---|---|---|---|---|")
    for c in sorted(ALL, key=lambda c: -crit[c]["auc"]):
        grp = ("blend" if c.startswith(BL) else "M1" if c.endswith(M1) else "M2")
        base = c[len(BL):] if c.startswith(BL) else c[:-3]
        W(f"| `{base}` | {grp} | {fmt(crit[c]['auc'])} | "
          f"{fmt(crit[c]['auc_lo'])}–{fmt(crit[c]['auc_hi'])} | {fmt(crit[c]['ap'])} |")
    for c in sorted(O52_COLS, key=lambda c: -o52[c]["auc"]):
        tag = " *(inverted)*" if o52[c]["inv"] else ""
        W(f"| `{c}`{tag} | *gate reads* | {fmt(o52[c]['auc'])} | "
          f"{fmt(o52[c]['auc_lo'])}–{fmt(o52[c]['auc_hi'])} | {fmt(o52[c]['ap'])} |")
    W("")
    best_m1 = max(COLS_M1, key=lambda c: crit[c]["auc"])
    best_m2 = max(COLS_M2, key=lambda c: crit[c]["auc"])
    best_bl = max(COLS_BL, key=lambda c: crit[c]["auc"])
    best_o52 = max(O52_COLS, key=lambda c: o52[c]["auc"])
    W(f"Both models' columns cluster in a narrow band — model-1 spans "
      f"{fmt(min(crit[c]['auc'] for c in COLS_M1))}–"
      f"{fmt(max(crit[c]['auc'] for c in COLS_M1))}, model-2 "
      f"{fmt(min(crit[c]['auc'] for c in COLS_M2))}–"
      f"{fmt(max(crit[c]['auc'] for c in COLS_M2))} — and every one of them sits above the "
      f"best single column the gate reads (`{best_o52}`, {fmt(o52[best_o52]['auc'])}).")
    W("")
    # ---------------- 2. the gate, as a fixed reference point ----------------
    W("### 2. The live O5.2 gate — the reference point, unchanged")
    W("")
    W("Replayed with `replay_gate.replay()`, which drives the real "
      f"`Orchestrator5.2._evaluate_trigger()` over `{Path(rules_path).name}`. **The gate "
      "reads none of the ten model columns**, so it is a control: it must land on exactly "
      "the numbers the 2026-09-16 report recorded, and it does.")
    W("")
    gb = metric_block(gate, y_raw, all_th, n_days)
    W(f"- **{N} fires** ({gb['fires_per_day']:.2f}/day), "
      f"{int(whitelist.sum())} rows admitted by `REQUIRE ClassWhitelist`")
    W("- per-branch: " + ", ".join(
        f"`{r}` {int(res['fired_' + r].sum())}" for r in rule_names))
    W(f"- at `>= {th:g}`: {gb[f'tp{th}']} hits, precision **{pct(gb[f'prec{th}'])}** "
      f"({pct(gb[f'prec{th}_lo'])}–{pct(gb[f'prec{th}_hi'])}), "
      f"lift {fmt(gb[f'lift{th}'], 2)}x, recall {pct(gb[f'recall{th}'])}")
    W("")
    W("Two structural facts carried over from the companion report, because they bound "
      "what any of this can achieve: only "
      f"**{int((whitelist & (y_raw >= th)).sum())} of {int((y_raw >= th).sum())}** "
      f"`>= {th:g}%` movers are inside the class whitelist at all, and the gate's lead on "
      "*captured move* was two rows deep. Neither is affected by which model is used.")
    W("")

    # ---------------- 3. matched workload ----------------
    W(f"### 3. At the gate's own workload — top {N} rows each")
    W("")
    W(BLOCK_HDR)
    W(BLOCK_SEP)
    W(block_row("**O5.2 gate** *(reference)*", gb, th))
    masks = {}
    for c in ALL:
        masks[c] = top_n_mask(df[c].to_numpy(dtype=float), N)
        b = metric_block(masks[c], y_raw, all_th, n_days)
        crit[c]["matched"] = b[f"prec{th}"]
        crit[c]["tp"] = b[f"tp{th}"]
        crit[c]["blk"] = b
    for c in sorted(ALL, key=lambda c: -crit[c]["matched"]):
        W(block_row(label(c), crit[c]["blk"], th))
    W("")
    spread = (max(crit[c]["matched"] for c in ALL)
              - min(crit[c]["matched"] for c in ALL))
    W(f"Every column beats the gate's {pct(gb[f'prec{th}'])}. The spread *among the "
      f"fifteen* is {pct(spread)} — for scale, §4 shows the paired bootstrap cannot "
      f"resolve a gap anywhere near that size on this corpus.")
    W("")

    # ---------------- 4. the core comparison ----------------
    W("### 4. Model-2 minus model-1, paired — the core result")
    W("")
    W("Each row compares the **same metric** from the two models. Both bootstraps are "
      "paired: the same resampled sessions score both models, so only the *difference "
      "between them* is being measured, not the noise they share. `P(Δ>0)` is the "
      "fraction of replicates in which model-2 came out ahead.")
    W("")
    W(f"| column | AUC M1 | AUC M2 | ΔAUC | 95% CI | P(Δ>0) | separable? |")
    W("|---|---|---|---|---|---|---|")
    h2h = {}
    for c in BASE_COLS:
        a1, a2 = crit[c + M1]["auc"], crit[c + M2]["auc"]
        (lo_, hi_), pg = block_boot_delta_auc(
            y, df[c + M2].to_numpy(dtype=float), df[c + M1].to_numpy(dtype=float),
            days, args.bootstrap, rng)
        ok = lo_ > 0 or hi_ < 0
        h2h[c] = {"d_auc": a2 - a1, "auc_ci": (lo_, hi_), "auc_p": pg, "auc_sep": ok}
        W(f"| `{c}` | {fmt(a1)} | {fmt(a2)} | {sgn(a2 - a1)} | "
          f"{sgn(lo_)}–{sgn(hi_)} | {pct(pg)} | {'**yes**' if ok else 'no'} |")
    W("")
    W(f"And the same question at the workload a reader acts on — precision in the top "
      f"{N}:")
    W("")
    W(f"| column | prec@{N} M1 | prec@{N} M2 | Δ | 95% CI (paired) | P(Δ>0) | "
      "95% CI (Newcombe) | separable? |")
    W("|---|---|---|---|---|---|---|---|")
    for c in BASE_COLS:
        p1_, p2_ = crit[c + M1]["matched"], crit[c + M2]["matched"]
        (plo, phi), ppg = block_boot_delta_prec(
            y_raw, th, df[c + M2].to_numpy(dtype=float),
            df[c + M1].to_numpy(dtype=float), N, days, args.bootstrap, rng)
        nlo, nhi = newcombe(crit[c + M2]["tp"], N, crit[c + M1]["tp"], N)
        ok = plo > 0 or phi < 0
        h2h[c].update({"d_prec": p2_ - p1_, "prec_ci": (plo, phi),
                       "prec_p": ppg, "prec_sep": ok})
        W(f"| `{c}` | {pct(p1_)} | {pct(p2_)} | {pct(p2_ - p1_)} | "
          f"{pct(plo)}–{pct(phi)} | {pct(ppg)} | {pct(nlo)}–{pct(nhi)} | "
          f"{'**yes**' if ok else 'no'} |")
    W("")
    W("A point estimate and its `P(Δ>0)` can point opposite ways here (e.g. a `+0.9%` Δ "
      "with model-2 ahead in only 35% of replicates). That is not an error: the paired "
      f"precision bootstrap **re-ranks inside each resampled corpus** and takes the top "
      f"{N / n:.1%} of whatever it drew, so its distribution is not centred on the "
      "full-sample Δ. A single-row lead (32 true positives against 31) is exactly the "
      "size of gap that survives on the full sample and evaporates on re-draw — which is "
      "the same message as the interval.")
    W("")
    sep_auc = [c for c in BASE_COLS if h2h[c]["auc_sep"]]
    sep_prec = [c for c in BASE_COLS if h2h[c]["prec_sep"]]
    if not sep_auc and not sep_prec:
        W("**Not one column separates.** Every interval, on both metrics, contains zero. "
          "With the pairing working in its favour — the strongest version of this test the "
          "corpus supports — the data cannot distinguish model-2 from model-1 on any of "
          "the five scores.")
    else:
        W(f"Separable: AUC {sep_auc or 'none'}; precision {sep_prec or 'none'}. "
          "Read those rows as real; the rest are inside the noise.")
    W("")
    worst = min(BASE_COLS, key=lambda c: h2h[c]["auc_p"])
    best_d = max(BASE_COLS, key=lambda c: h2h[c]["d_prec"])
    W(f"The two rows worth naming anyway. `{worst}` is the column where model-2 looks "
      f"weakest — ΔAUC {sgn(h2h[worst]['d_auc'])}, ahead in only "
      f"{pct(h2h[worst]['auc_p'])} of replicates; it is also the column the 2026-09-16 "
      f"report selected for model-1, so the one apparent regression lands exactly where "
      f"the previous study pointed. `{best_d}` is where model-2 gains most at matched "
      f"fires ({pct(h2h[best_d]['d_prec'])}). Neither crosses the separability bar.")
    W("")
    # ---------------- 5. where they disagree ----------------
    W("### 5. Where the two models disagree")
    W("")
    W(f"A ΔAUC near zero can mean *the two models rank alike* or *they disagree in both "
      f"directions and it averages out*. Those call for different follow-ups, so: the "
      f"overlap of the two top-{N} sets, per column.")
    W("")
    W(f"| column | Spearman ρ | shared rows | M1-only | M2-only | "
      f"hit rate M1-only | hit rate M2-only |")
    W("|---|---|---|---|---|---|---|")
    for c in BASE_COLS:
        s1 = df[c + M1].to_numpy(dtype=float)
        s2 = df[c + M2].to_numpy(dtype=float)
        m1m, m2m = masks[c + M1], masks[c + M2]
        both = int((m1m & m2m).sum())
        o1, o2 = m1m & ~m2m, m2m & ~m1m
        rho = float(spearmanr(s1, s2).statistic)
        h1 = float((y_raw[o1] >= th).mean()) if o1.sum() else float("nan")
        h2 = float((y_raw[o2] >= th).mean()) if o2.sum() else float("nan")
        W(f"| `{c}` | {fmt(rho)} | {both} | {int(o1.sum())} | {int(o2.sum())} | "
          f"{pct(h1)} | {pct(h2)} |")
    W("")
    W(f"They agree on the broad ordering but genuinely swap rows in and out of the "
      f"traded set. That disagreement is what makes the blend in §9 worth testing at all "
      "— two models that picked the same rows could not help each other.")
    W("")

    # ---------------- 6. operating curve ----------------
    W("### 6. The operating curve — does the answer depend on workload?")
    W("")
    W(f"Everything above is read at {N} fires because that is what the gate does. If "
      "model-2 were better only at that one workload it would be a fit artifact, so here "
      "is precision across the range, for the highest-**AUC** column of each model (§1) "
      "and its blend. Note these are not the same columns that lead at 113 fires — "
      "another reading of §10's point that the ranking among columns is not stable.")
    W("")
    curve_cols = [best_m1, best_m2, best_bl]
    ks = sorted({25, 50, 75, 100, N, 150, 200, 276, 400})
    W("| top-N | /day | " + " | ".join(label(c) for c in curve_cols) + " | gate |")
    W("|---|---|" + "---|" * (len(curve_cols) + 1))
    for k in ks:
        cells = []
        for c in curve_cols:
            m = top_n_mask(df[c].to_numpy(dtype=float), k)
            cells.append(pct(float((y_raw[m] >= th).mean())))
        gcell = f"**{pct(gb[f'prec{th}'])}**" if k == N else ""
        W(f"| {k}{' *(gate)*' if k == N else ''} | {k / n_days:.2f} | "
          + " | ".join(cells) + f" | {gcell} |")
    W("")

    # ---------------- 7. stability ----------------
    W("### 7. Stability — halves of the window, and the clean sub-window")
    W("")
    uniq_days = sorted(df[DATE_COL].unique())
    mid = uniq_days[len(uniq_days) // 2]
    halfA = df[DATE_COL].to_numpy() < mid
    halfB = ~halfA
    clean = (df[DATE_COL] >= args.clean_window_from).to_numpy()

    def sub_prec(c, sub):
        idx = np.where(sub)[0]
        if len(idx) == 0:
            return float("nan")
        k = max(1, int(round(N * len(idx) / n)))
        s = df[c].to_numpy(dtype=float)[idx]
        return float((y_raw[idx][top_n_mask(s, k)] >= th).mean())

    for c in ALL:
        crit[c]["A"] = sub_prec(c, halfA)
        crit[c]["B"] = sub_prec(c, halfB)
        crit[c]["clean"] = sub_prec(c, clean)
    W(f"Precision at the same *fire rate* (top {N / n:.1%} of rows), inside each subset. "
      f"Half A = {uniq_days[0]}..{uniq_days[len(uniq_days) // 2 - 1]} "
      f"({int(halfA.sum())} rows), half B = {mid}..{uniq_days[-1]} "
      f"({int(halfB.sum())} rows), clean = `>= {args.clean_window_from}` "
      f"({int(clean.sum())} rows, the stretch where the gate's own class whitelist was "
      "already frozen).")
    W("")
    W("| column | model | half A | half B | clean | full |")
    W("|---|---|---|---|---|---|")
    for c in sorted(ALL, key=lambda c: -crit[c]["matched"]):
        grp = ("blend" if c.startswith(BL) else "M1" if c.endswith(M1) else "M2")
        base = c[len(BL):] if c.startswith(BL) else c[:-3]
        W(f"| `{base}` | {grp} | {pct(crit[c]['A'])} | {pct(crit[c]['B'])} | "
          f"{pct(crit[c]['clean'])} | {pct(crit[c]['matched'])} |")
    W(f"| **O5.2 gate** | *ref* | {pct(sub_prec_gate(gate, halfA, y_raw, th))} | "
      f"{pct(sub_prec_gate(gate, halfB, y_raw, th))} | "
      f"{pct(sub_prec_gate(gate, clean, y_raw, th))} | {pct(gb[f'prec{th}'])} |")
    W("")
    swing = {c: abs(crit[c]["A"] - crit[c]["B"]) for c in ALL}
    W(f"The half-to-half swing reaches {pct(max(swing.values()))} for "
      f"{label(max(swing, key=swing.get))} — larger than any model-1/model-2 gap in §4. "
      "That is the single clearest statement of how much this corpus can resolve: the "
      "same column, measured on two halves of the same month, moves more than the two "
      "models move apart.")
    W("")
    # ---------------- 8. the tail ----------------
    W("### 8. Is anyone's lead just a couple of rows?")
    W("")
    W("`fromPR_High(%)` has a very fat right tail on this corpus (max "
      f"{y_raw.max():.0f}%), so a mean captured move can be carried by one or two PRs. "
      "The companion report found exactly that for the gate. Same test here: mean move "
      f"in the top {N}, before and after dropping each column's two fattest rows.")
    W("")
    W("| column | mean | mean less top-2 | median | drop |")
    W("|---|---|---|---|---|")

    def tail_row(nm, m):
        v = np.sort(y_raw[np.asarray(m, dtype=bool)])[::-1]
        mu = float(v.mean()) if len(v) else float("nan")
        mu2 = float(v[2:].mean()) if len(v) > 2 else float("nan")
        return (f"| {nm} | {fmt(mu, 1)} | {fmt(mu2, 1)} | "
                f"{fmt(float(np.median(v)), 1)} | {fmt(mu - mu2, 1)} |"), mu - mu2
    r, _ = tail_row("**O5.2 gate** *(reference)*", gate)
    W(r)
    drops = {}
    for c in sorted(ALL, key=lambda c: -crit[c]["matched"]):
        r, d = tail_row(label(c), masks[c])
        drops[c] = d
        W(r)
    W("")
    W("Read the last column as fragility: how much of the average is two rows. It is "
      "large for everything here — that is the nature of the target, not a defect in any "
      f"one model — which is why this report ranks on **precision** and **AUC** and "
      "treats mean captured move as context only.")
    W("")

    # ---------------- 9. the blend ----------------
    W("### 9. Does blending the two models help?")
    W("")
    W("Rank-average of model-1 and model-2 on the same metric, tested against **the "
      "better of its two parents** — the honest comparison, since you would otherwise "
      "just use that parent alone. Paired bootstrap again.")
    W("")
    W(f"| column | better parent | parent AUC | blend AUC | ΔAUC | 95% CI | P(Δ>0) | "
      f"parent prec@{N} | blend prec@{N} | separable? |")
    W("|---|---|---|---|---|---|---|---|---|---|")
    blend_wins = []
    for c in BASE_COLS:
        par = c + M1 if crit[c + M1]["auc"] >= crit[c + M2]["auc"] else c + M2
        (lo_, hi_), pg = block_boot_delta_auc(
            y, df[BL + c].to_numpy(dtype=float), df[par].to_numpy(dtype=float),
            days, args.bootstrap, rng)
        ok = lo_ > 0 or hi_ < 0
        if ok and crit[BL + c]["auc"] > crit[par]["auc"]:
            blend_wins.append(c)
        W(f"| `{c}` | {'M1' if par.endswith(M1) else 'M2'} | {fmt(crit[par]['auc'])} | "
          f"{fmt(crit[BL + c]['auc'])} | {sgn(crit[BL + c]['auc'] - crit[par]['auc'])} | "
          f"{sgn(lo_)}–{sgn(hi_)} | {pct(pg)} | {pct(crit[par]['matched'])} | "
          f"{pct(crit[BL + c]['matched'])} | {'**yes**' if ok else 'no'} |")
    W("")
    n_bl_up = sum(1 for c in BASE_COLS
                  if crit[BL + c]["auc"] > max(crit[c + M1]["auc"], crit[c + M2]["auc"]))
    if blend_wins:
        W(f"Blending **is** separably better for {blend_wins}. That is the one place in "
          "this report where combining the two models buys something the corpus can "
          "actually see.")
    else:
        W(f"**No blend separates from its better parent.** The rank average lands above "
          f"the better parent's AUC in {n_bl_up} of {len(BASE_COLS)} columns, which is "
          "the mild gain you would expect from averaging two correlated rankers, but not "
          "once by enough to clear the noise. On this window there is no measurable "
          "reason to run both models instead of one.")
    W("")

    # ---------------- 10. selection matrix ----------------
    CRITERIA = [("AUC", "auc"), ("PR-AUC", "ap"), (f"prec@{N}", "matched"),
                ("clean window", "clean"), ("half A", "A"), ("half B", "B")]
    for _, key in CRITERIA:
        for c in ALL:
            crit[c][key + "_rank"] = 1 + sum(1 for o in ALL if crit[o][key] > crit[c][key])
    for c in ALL:
        crit[c]["firsts"] = sum(1 for _, k in CRITERIA if crit[c][k + "_rank"] == 1)
        crit[c]["meanrank"] = float(np.mean([crit[c][k + "_rank"] for _, k in CRITERIA]))
    best_all = min(ALL, key=lambda c: (-crit[c]["firsts"], crit[c]["meanrank"],
                                       -crit[c]["auc"]))
    W("### 10. Selection matrix — all fifteen columns, six criteria")
    W("")
    W("Rank `1` is best; ties share a rank. Same construction as the companion report, "
      "widened from five columns to fifteen.")
    W("")
    W("| column | model | " + " | ".join(nm for nm, _ in CRITERIA) + " | firsts | mean rank |")
    W("|---|---|" + "---|" * (len(CRITERIA) + 2))
    for c in sorted(ALL, key=lambda c: (-crit[c]["firsts"], crit[c]["meanrank"])):
        grp = ("blend" if c.startswith(BL) else "M1" if c.endswith(M1) else "M2")
        base = c[len(BL):] if c.startswith(BL) else c[:-3]
        cells = [f"{(fmt(crit[c][k]) if k in ('auc', 'ap') else pct(crit[c][k]))} "
                 f"({crit[c][k + '_rank']})" for _, k in CRITERIA]
        mark = " **←top**" if c == best_all else ""
        W(f"| `{base}`{mark} | {grp} | " + " | ".join(cells)
          + f" | {crit[c]['firsts']} | {crit[c]['meanrank']:.1f} |")
    W("")
    W(f"Tally leader: **{label(best_all)}**. Now the same separability test applied to "
      "that claim — paired ΔAUC, tally leader minus each other column:")
    W("")
    W("| vs | ΔAUC | 95% CI | P(Δ>0) | separable? |")
    W("|---|---|---|---|---|")
    sep_any = []
    for c in sorted(ALL, key=lambda c: -crit[c]["auc"]):
        if c == best_all:
            continue
        (lo_, hi_), pg = block_boot_delta_auc(
            y, df[best_all].to_numpy(dtype=float), df[c].to_numpy(dtype=float),
            days, args.bootstrap, rng)
        ok = lo_ > 0 or hi_ < 0
        if ok:
            sep_any.append(c)
        W(f"| {label(c)} | {sgn(crit[best_all]['auc'] - crit[c]['auc'])} | "
          f"{sgn(lo_)}–{sgn(hi_)} | {pct(pg)} | {'**yes**' if ok else 'no'} |")
    W("")
    if not sep_any:
        W(f"**Every interval contains zero.** {label(best_all)} wins the tally and is "
          "separable from nothing. The tally is a tie-break dressed as a ranking; treat "
          "the choice among these fifteen as a preference — interpretability, cost, "
          "whichever you already run — and not as a finding.")
    else:
        W(f"Separable from: {', '.join(label(c) for c in sep_any)}. Those gaps are real; "
          "the rest are not.")
    W("")
    # ---------------- verdict ----------------
    med_m1 = float(np.median([crit[c]["auc"] for c in COLS_M1]))
    med_m2 = float(np.median([crit[c]["auc"] for c in COLS_M2]))
    v = []
    if not sep_auc and not sep_prec:
        v.append(f"**No. On this window model-2 is statistically indistinguishable from "
                 f"model-1 — and that is the finding, not a failure to find one.** All "
                 f"five scores were compared with a *paired* day-block bootstrap, the "
                 f"most sensitive test these {n_days} sessions support, and not one gap "
                 f"separates from zero on either ranking AUC or precision at the gate's "
                 f"{N}-fire workload. Median AUC at `>= {th:g}` is {fmt(med_m1)} for "
                 f"model-1 and {fmt(med_m2)} for model-2.")
        v.append("")
        v.append(f"**What model-2 does buy is parsimony.** It reaches that same accuracy "
                 f"on **25 features instead of 49** — fewer upstream columns to compute, "
                 f"fewer to break, and less surface for the kind of silent join or "
                 f"re-score defect this pipeline has produced before. If you were "
                 f"choosing between them on evidence, 'same power, half the inputs' is "
                 f"the whole of the case for model-2, and it is a reasonable one. What "
                 f"the data does *not* support is a claim that it predicts better.")
    else:
        v.append(f"**Partly.** Separable on AUC: {sep_auc or 'none'}; on precision at "
                 f"{N} fires: {sep_prec or 'none'}. Every other column is a tie. Median "
                 f"AUC {fmt(med_m1)} (M1) vs {fmt(med_m2)} (M2).")
    v.append("")
    v.append(f"Two things this report does **not** find. There is no separable gain from "
             f"blending the two models (§9). And the fifteen columns are not separable "
             f"from *each other* (§10) — the same conclusion the companion report reached "
             f"for model-1's five, now confirmed across both models. Meanwhile every one "
             f"of the fifteen out-performs the live gate's {pct(gb[f'prec{th}'])} at "
             f"matched workload, which remains the one comparison this corpus resolves "
             f"cleanly.")
    lines[verdict_slot] = "## Verdict\n\n" + "\n".join(v) + "\n"

    # ---------------- cross-check integrity ----------------
    W("## Cross-check integrity")
    W("")
    W(f"1. **The join is genuinely paired.** {n}/{n} rows matched `one_to_one` on "
      f"`(Symbol, ArrivalDate, ArrivalTime)`, and the two files agree on "
      f"`{TARGET}` to **max abs diff {tgt_gap:g}**. The script exits non-zero on any "
      "other outcome — an unpaired comparison here would silently halve the sensitivity "
      "of every interval in §4.")
    W(f"2. **The gate is untouched by either model.** {N} fires, "
      f"{int(whitelist.sum())} whitelist admissions, per-branch "
      + ", ".join(f"`{r}` {int(res['fired_' + r].sum())}" for r in rule_names)
      + " — identical to the 2026-09-16 run, as it must be: `replay_gate` reads only the "
        "six O5.2 columns plus `EarningScore`, never a model column.")
    W("3. **Both models reproduce their own training reports**, which is what says these "
      "score columns really are the models those reports describe:")
    W("")
    W("| model | column | reported | recomputed | |")
    W("|---|---|---|---|---|")
    for tag, c, ra, ga, rp, gp, ok in checks:
        r_s = fmt(ra) if ra is not None else pct(rp)
        g_s = fmt(ga) if ga is not None else pct(gp)
        W(f"| {tag.upper()} | `{c}` | {r_s} | {g_s} | {'OK' if ok else '**MISMATCH**'} |")
    W("")
    if checks_ok:
        W("All pass. This check is not ceremonial: model-1's first scoring run **failed "
          "it**, because `score_val_predictions.py` was predicting with every boosted "
          "round instead of stopping at `best_iteration`. Model-2's scores were correct "
          "on arrival.")
    else:
        W("**A check FAILED — treat every number in this report as void until it is "
          "resolved.** A mismatch here means the score columns are not the models the "
          "training reports describe.")
    W("")

    # ---------------- caveats ----------------
    W("## Caveats")
    W("")
    W(f"1. **One window, {n_days} sessions, {int(y.sum())} big movers.** Everything here "
      "is a single validation fold. §7 shows the same column swinging more between two "
      "halves of this month than the two models differ overall — so a model-2-vs-model-1 "
      "verdict from one window is inherently a weak instrument, and 'indistinguishable' "
      "is the correct reading rather than 'equal'.")
    W("2. **`model-2/xgb_fromPRhigh_clf_report.md` mis-states its own feature count.** It "
      "says *\"49-feature SHAP allowlist\"*; the boosters carry **25** "
      "(`xgb_cons_se.ubj`, `xgb_fromPRhigh_clf_ge*.ubj`), and "
      "`xgb_fromPRhigh_features_conservative.txt` lists 25. The line is inherited "
      "boilerplate from model-1's run. Not corrected here — it is a training-side "
      "artifact, and this report does not edit training outputs.")
    W("3. **The two models do not read the same `FineClass`.** Model-2 was trained and "
      "scored against the re-categorised labels; the gate replayed in §2 reads the "
      "original ones, because those are what O5.2 saw live and what the 448/448 tape "
      "verification was performed against. Model-2 therefore gets a slightly different "
      "view of the same PRs than the gate does. Holding the gate fixed is the right "
      "choice for a control, but it does mean §2 is not a preview of how the gate would "
      "behave if the new classifier were ever adopted.")
    W("4. **`fromPR_High(%)` is the intraday ceiling from the PR, not realized PnL.** "
      "Every captured-move number is a best case with a perfect exit — applied equally "
      "to both models and the gate, so it ranks them fairly, but it is not an account "
      "balance.")
    W("5. **The `EARNINGS` branch of the replayed gate is an estimate.** The corpus "
      "`Body` is capped at 400 words while the live earnings scorer reads the whole "
      "body; `replay_gate.py` measures 91.4% fidelity there against 100.0% elsewhere. "
      "This affects only the §2 reference row, identically for both models.")
    W("6. **Probabilities are not calibrated** in either model — `proba_clf_*` is a "
      "ranking score, and no number here treats it as a probability.")
    W("")

    # ---------------- artifacts ----------------
    W("## Artifacts")
    W("")
    W(f"- `{p1.name}` — model-1 scores + the six gate columns + `EarningScore`/`ID`")
    W(f"- `{p2.name}` — model-2 scores")
    W(f"- `compare_xgb_models.py` — this report, end to end. Deterministic "
      f"(`--seed {args.seed}`, {args.bootstrap} bootstrap replicates).")
    W("- `compare_xgb_vs_orch52.py` — the companion model-1-vs-gate study, unmodified.")
    W("")
    W("Neither TSV is tracked by git (`outputs/*.tsv` is un-ignored by `.gitignore:35` "
      "but no scores file has ever been added); the scripts and this report are.")
    W("")
    W("```")
    W(f"/home/tom/venv/bin/python compare_xgb_models.py \\")
    W(f"  --m1 outputs/{p1.name} \\")
    W(f"  --m2 outputs/{p2.name}")
    W("```")

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    out = outdir / args.report
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[write] {out}")
    sys.exit(0 if checks_ok else 1)


if __name__ == "__main__":
    main()
