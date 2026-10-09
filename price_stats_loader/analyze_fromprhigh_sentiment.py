#!/usr/bin/env python
"""Find the sentiment-score values most associated with fromPR_High(%).

Full statistical sweep over an enriched PR-stats TSV:

  1. univariate    - ROC-AUC / Spearman / Mann-Whitney (+ BH-FDR) per numeric
                     feature, plus a threshold sweep picking the best cut by
                     precision subject to a minimum fire count
  2. categorical   - hit-rate + Wilson CI per level of every label column
  3. AND-rules     - exhaustive 2-way then beam-searched 3-way conjunctions
                     over the best univariate features, with a date-split
                     stability check and a bootstrap CI
  4. multivariate  - L2 logistic regression + HistGradientBoosting, 5-fold
                     stratified CV, as an upper bound on total signal

Honest-window rule: the kw15 columns come from a model trained on
2026-05-03..2026-06-19, so any feature/rule/model that touches them is scored
only on rows after that window (--kw-honest-from). Everything else uses the
full sample. Every row of every output carries the window it was scored on.

Run with the venv python (needs sklearn/scipy/matplotlib):
  /home/tom/venv/bin/python analyze_fromprhigh_sentiment.py \
      --input outputs/07_PRstats_..._kw15Scored.tsv --tp-threshold 50
"""

import argparse
import itertools
import os
import re
import sys
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from scipy import stats

warnings.filterwarnings("ignore")

# ---------------------------------------------------------------- palette ---
# dataviz reference palette, light mode (categorical slots 1-3 validate
# all-pairs, which is what the scatter charts need).
C_BLUE, C_ORANGE, C_AQUA = "#2a78d6", "#eb6834", "#1baf7a"
C_SURFACE, C_INK, C_INK2 = "#fcfcfb", "#0b0b0b", "#52514e"
C_GRID, C_NEUTRAL = "#e6e5e1", "#8a8985"

LABEL_COL = "fromPR_High(%)"
STATUS_COL = "fromPR_status"
DATE_COL = "ArrivalDate"

# _ms_hd columns are inference-time milliseconds, not sentiment - excluded.
SCORE_RE = re.compile(r"_(neg|neu|ind|pos|ss|unc|lit|smod|wmod|cons)_(hd|bd)$")
LABEL_RE = re.compile(r"_lab_(hd|bd)$")

CONTEXT_NUMERIC = ["Float", "MktCap", "Trades/sec", "itiBaseline",
                   "tradeSizeBaseline", "hour_of_day", "headline_len"]
CONTEXT_CATEGORICAL = ["BroadClass", "FineClass", "Exchange", "Author",
                       "boilerplate_category", "day_of_week"]


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ------------------------------------------------------------------ loading --
def load(path, tp_threshold):
    df = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False,
                     engine="python")
    n_raw = len(df)
    df = df[df[STATUS_COL] == "ok"].copy()
    df["_high"] = pd.to_numeric(df[LABEL_COL], errors="coerce")
    df = df[df["_high"].notna()].copy()
    df["_y"] = (df["_high"] >= tp_threshold).astype(int)

    # ArrivalTime is "HH:MM:SS" before 2026-06 and "HH:MM:SS.mmm" after, so a
    # single inferred format silently NaTs two thirds of the corpus - parse the
    # clock field positionally instead.
    hm = df["ArrivalTime"].str.extract(r"^\s*(\d{1,2}):(\d{2})").astype(float)
    df["hour_of_day"] = hm[0] + hm[1] / 60.0
    df["day_of_week"] = pd.to_datetime(df[DATE_COL], errors="coerce").dt.day_name()
    df["headline_len"] = df["Headline"].str.len()
    df = df.sort_values(DATE_COL).reset_index(drop=True)
    log(f"loaded {n_raw} rows -> {len(df)} scorable, "
        f"{int(df['_y'].sum())} TP ({100 * df['_y'].mean():.2f}%)")
    return df


def discover_features(df, kw_cols, min_coverage):
    """Return (numeric_features, categorical_features) as name -> is_kw flag."""
    numeric, categorical = {}, {}
    for c in df.columns:
        if c in ("_high", "_y"):
            continue
        if SCORE_RE.search(c) or c in kw_cols or c in CONTEXT_NUMERIC:
            v = pd.to_numeric(df[c], errors="coerce")
            if v.notna().sum() < min_coverage or v.nunique() < 2:
                continue
            df[c + "__num"] = v
            numeric[c] = c in kw_cols
        elif LABEL_RE.search(c) or c in CONTEXT_CATEGORICAL:
            categorical[c] = False
    return numeric, categorical


# -------------------------------------------------------------- univariate --
def auc_and_tests(x, y):
    """ROC-AUC (of x as-is), Mann-Whitney p, Spearman vs continuous high%."""
    pos, neg = x[y == 1], x[y == 0]
    if len(pos) < 3 or len(neg) < 3:
        return np.nan, np.nan
    u, p = stats.mannwhitneyu(pos, neg, alternative="two-sided")
    return u / (len(pos) * len(neg)), p


def sweep_thresholds(x, y, min_fires, grid_q):
    """Best cut in each direction by precision subject to fires >= min_fires.

    Returns list of dicts, one per direction, plus the full curve for plotting.
    """
    order = np.argsort(x, kind="mergesort")
    sv, sl = x[order], y[order]
    cum = np.cumsum(sl)
    total_tp, n = cum[-1], len(sv)
    cuts = np.unique(np.quantile(sv, grid_q))

    idx_l = np.searchsorted(sv, cuts, side="left")
    fires_ge = n - idx_l
    tp_ge = total_tp - np.where(idx_l > 0, cum[np.maximum(idx_l - 1, 0)], 0)

    idx_r = np.searchsorted(sv, cuts, side="right")
    fires_le = idx_r
    tp_le = np.where(idx_r > 0, cum[np.maximum(idx_r - 1, 0)], 0)

    out, curves = [], {}
    for direction, fires, tps in ((">=", fires_ge, tp_ge),
                                  ("<=", fires_le, tp_le)):
        with np.errstate(invalid="ignore", divide="ignore"):
            prec = np.where(fires > 0, tps / np.maximum(fires, 1), np.nan)
        curves[direction] = (cuts, prec, fires)
        ok = fires >= min_fires
        if not ok.any():
            continue
        best = np.argmax(np.where(ok, prec, -1))
        out.append(dict(direction=direction, threshold=float(cuts[best]),
                        fires=int(fires[best]), tp=int(tps[best]),
                        precision=float(prec[best]),
                        recall=float(tps[best] / total_tp) if total_tp else np.nan))
    return out, curves


def univariate(df, numeric, kw_from, min_fires, grid_q):
    rows, curves = [], {}
    for col, is_kw in numeric.items():
        sub = df if not is_kw else df[df[DATE_COL] >= kw_from]
        window = "HONEST" if is_kw else "FULL"
        v = sub[col + "__num"]
        m = v.notna()
        x, y = v[m].to_numpy(float), sub.loc[m, "_y"].to_numpy(int)
        high = sub.loc[m, "_high"].to_numpy(float)
        if len(x) < min_fires * 3 or y.sum() < 5:
            continue
        auc, p = auc_and_tests(x, y)
        rho, rho_p = stats.spearmanr(x, high)
        base = y.mean()
        best, cur = sweep_thresholds(x, y, min_fires, grid_q)
        curves[col] = (cur, base, window)
        for b in best:
            fired = (x >= b["threshold"]) if b["direction"] == ">=" \
                else (x <= b["threshold"])
            syms = sub.loc[m, "Symbol"].to_numpy()[fired]
            rows.append(dict(
                feature=col, window=window, n=len(x), n_tp=int(y.sum()),
                base_rate=base, auc=auc, mw_p=p, spearman_rho=rho,
                spearman_p=rho_p, coverage=float(fired.mean()),
                uniq_symbols=int(pd.unique(syms).size),
                max_symbol_share=float(pd.Series(syms).value_counts(normalize=True).max())
                if len(syms) else np.nan,
                median_high_fired=float(np.median(high[fired])) if fired.any() else np.nan,
                mean_high_fired=float(np.mean(high[fired])) if fired.any() else np.nan,
                lift=b["precision"] / base if base else np.nan, **b))
    res = pd.DataFrame(rows)
    if not res.empty:
        res["mw_q_bh"] = bh_fdr(res.groupby("feature")["mw_p"].transform("first"))
        res["dir_matches_auc"] = ((res.auc >= 0.5) & (res.direction == ">=")) | \
                                 ((res.auc < 0.5) & (res.direction == "<="))
        res = res.sort_values("precision", ascending=False,
                              kind="stable").reset_index(drop=True)
    return res, curves


def ranked_features(uni):
    """One row per feature: the direction its AUC implies, ordered by |AUC-0.5|.

    Direction is taken from the AUC sign, never from row order - a `>=` cut on a
    feature whose AUC is below 0.5 is fitting noise in the wrong tail.
    """
    if uni.empty:
        return uni
    keep = uni[((uni.auc >= 0.5) & (uni.direction == ">=")) |
               ((uni.auc < 0.5) & (uni.direction == "<="))].copy()
    keep["_disc"] = (keep.auc - 0.5).abs()
    return (keep.sort_values("_disc", ascending=False, kind="stable")
                .drop_duplicates("feature").reset_index(drop=True))


def bh_fdr(p):
    p = np.asarray(p, float)
    ok = ~np.isnan(p)
    q = np.full_like(p, np.nan)
    pv = p[ok]
    order = np.argsort(pv)
    n = len(pv)
    adj = pv[order] * n / (np.arange(n) + 1)
    adj = np.minimum.accumulate(adj[::-1])[::-1]
    out = np.empty(n)
    out[order] = np.clip(adj, 0, 1)
    q[ok] = out
    return q


def head_vs_body(uni):
    """Pair every `X_chan_hd` with its `X_chan_bd` twin and compare AUC.

    The channels only exist in both flavours when the corpus has been scored by
    both HeadScore11encoders and BodyScore11encoders; when it hasn't, this comes
    back empty and the report says so rather than implying headline supremacy.
    """
    if uni.empty:
        return pd.DataFrame()
    best = (uni.assign(_disc=(uni.auc - 0.5).abs())
               .sort_values("_disc", ascending=False, kind="stable")
               .drop_duplicates("feature")
               .set_index("feature"))
    rows = []
    for feat in best.index:
        if not feat.endswith("_hd"):
            continue
        twin = feat[:-3] + "_bd"
        if twin not in best.index:
            continue
        h, b = best.loc[feat], best.loc[twin]
        dh, db = abs(h.auc - 0.5), abs(b.auc - 0.5)
        rows.append(dict(channel=feat[:-3], auc_head=h.auc, auc_body=b.auc,
                         disc_head=dh, disc_body=db, disc_delta=db - dh,
                         winner="body" if db > dh else "headline",
                         prec_head=h.precision, prec_body=b.precision,
                         rho_head=h.spearman_rho, rho_body=b.spearman_rho))
    out = pd.DataFrame(rows)
    return out.sort_values("disc_delta", ascending=False).reset_index(drop=True) \
        if not out.empty else out


def wilson(k, n, z=1.96):
    if n == 0:
        return (np.nan, np.nan)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def categorical_table(df, categorical, min_n):
    rows = []
    base = df["_y"].mean()
    for col in categorical:
        vc = df[col].replace("", "(blank)")
        for level, n in vc.value_counts().items():
            if n < min_n:
                continue
            m = vc == level
            k = int(df.loc[m, "_y"].sum())
            lo, hi = wilson(k, int(n))
            rows.append(dict(column=col, level=level, n=int(n), tp=k,
                             precision=k / n, lift=(k / n) / base,
                             ci_lo=lo, ci_hi=hi,
                             median_high=float(df.loc[m, "_high"].median()),
                             uniq_symbols=int(df.loc[m, "Symbol"].nunique())))
    out = pd.DataFrame(rows)
    return out.sort_values("precision", ascending=False).reset_index(drop=True) \
        if not out.empty else out


# ------------------------------------------------------------------- rules --
def build_masks(sub, feats, uni, min_fires, n_cuts):
    """Fire-masks for a grid of cuts per feature, in its AUC direction.

    Also returns a per-cut `valid` mask (feature non-null). A null never fires,
    but it does make the row un-evaluable, which is what the evaluable base rate
    is computed against - several context columns here are only populated from
    July onward, so a rule containing one is really a July+ rule.
    """
    names, masks, valids = [], [], []
    n = len(sub)
    for col in feats:
        row = uni[uni.feature == col]
        if row.empty:
            continue
        direction = row.iloc[0]["direction"]
        raw = sub[col + "__num"].to_numpy(float)
        valid = ~np.isnan(raw)
        v = np.where(valid, raw, -np.inf if direction == ">=" else np.inf)
        finite = raw[valid]
        if len(finite) < min_fires * 3:
            continue
        qs = np.linspace(0.55, 0.985, n_cuts) if direction == ">=" \
            else np.linspace(0.015, 0.45, n_cuts)
        for t in np.unique(np.quantile(finite, qs)):
            m = ((v >= t) if direction == ">=" else (v <= t)) & valid
            f = int(m.sum())
            if f < min_fires or f > 0.6 * n:
                continue
            names.append((col, direction, float(t)))
            masks.append(m)
            valids.append(valid)
    empty = np.zeros((0, n), bool)
    return (names,
            np.array(masks, dtype=bool) if masks else empty,
            np.array(valids, dtype=bool) if valids else empty)


def rule_stats(mask, sub, min_fires, eligible=None):
    f = int(mask.sum())
    if f < min_fires:
        return None
    y = sub["_y"].to_numpy(int)[mask]
    high = sub["_high"].to_numpy(float)[mask]
    syms = sub["Symbol"].to_numpy()[mask]
    dates = sub[DATE_COL].to_numpy()[mask]
    tp = int(y.sum())
    # split the rule's own window at its median date -> stability check
    mid = np.median(pd.to_datetime(sub[DATE_COL]).astype("int64").to_numpy())
    dnum = pd.to_datetime(dates).astype("int64").to_numpy()
    h1, h2 = dnum <= mid, dnum > mid
    boot = np.nan
    if f:
        rng = np.random.default_rng(7)
        samp = rng.integers(0, f, size=(400, f))
        boot = np.percentile(y[samp].mean(axis=1), [5, 95])
    yall = sub["_y"].to_numpy(int)
    n_elig = int(eligible.sum()) if eligible is not None else len(yall)
    base_elig = float(yall[eligible].mean()) if eligible is not None and n_elig \
        else float(yall.mean())
    return dict(fires=f, tp=tp, precision=tp / f,
                n_evaluable=n_elig, base_rate_evaluable=base_elig,
                lift_evaluable=(tp / f) / base_elig if base_elig else np.nan,
                uniq_symbols=int(pd.unique(syms).size),
                max_symbol_share=float(pd.Series(syms).value_counts(normalize=True).max()),
                median_high=float(np.median(high)), mean_high=float(np.mean(high)),
                prec_first_half=float(y[h1].mean()) if h1.sum() else np.nan,
                n_first_half=int(h1.sum()),
                prec_second_half=float(y[h2].mean()) if h2.sum() else np.nan,
                n_second_half=int(h2.sum()),
                boot_lo=float(boot[0]), boot_hi=float(boot[1]))


def fmt_term(t):
    return f"{t[0]} {t[1]} {t[2]:.4g}"


def search_rules(sub, feats, uni, window, min_fires, n_cuts, top_pairs,
                 require_cols=None):
    names, M, V = build_masks(sub, feats, uni, min_fires, n_cuts)
    if M.shape[0] == 0:
        return pd.DataFrame()
    y = sub["_y"].to_numpy(np.float64)
    base = y.mean()
    Mf = M.astype(np.float64)
    fires2 = Mf @ Mf.T
    tp2 = (Mf * y) @ Mf.T
    solo_prec = np.divide(tp2.diagonal(), np.maximum(fires2.diagonal(), 1))

    cand = []
    k = len(names)
    for i in range(k):
        for j in range(i + 1, k):
            if names[i][0] == names[j][0]:
                continue
            f = fires2[i, j]
            if f < min_fires:
                continue
            cand.append((tp2[i, j] / f, i, j))
    cand.sort(reverse=True)

    rows = []

    def emit(idx, arity):
        terms = [names[t] for t in idx]
        if require_cols and not any(t[0] in require_cols for t in terms):
            return
        mask = M[idx[0]].copy()
        elig = V[idx[0]].copy()
        for t in idx[1:]:
            mask &= M[t]
            elig &= V[t]
        st = rule_stats(mask, sub, min_fires, elig)
        if not st:
            return
        # marginal value of the last term: best precision over subsets of size-1 less
        if arity == 2:
            best_sub = max(solo_prec[idx[0]], solo_prec[idx[1]])
        else:
            best_sub = max(tp2[a, b] / fires2[a, b] if fires2[a, b] else 0
                           for a, b in itertools.combinations(idx, 2))
        rows.append(dict(rule=" AND ".join(fmt_term(t) for t in terms),
                         arity=arity, window=window, base_rate=base,
                         lift=st["precision"] / base,
                         best_subset_precision=float(best_sub),
                         gain_over_subset=st["precision"] - float(best_sub),
                         _fp=np.packbits(mask).tobytes(), **st))

    for prec, i, j in cand:
        emit((i, j), 2)
        if len(rows) >= 4000:
            break

    # 3-way: beam from the strongest surviving pairs
    seen = set()
    for prec, i, j in cand[:max(top_pairs, 1)]:
        for z in range(len(names)):
            if names[z][0] in (names[i][0], names[j][0]):
                continue
            key = tuple(sorted((i, j, z)))
            if key in seen:
                continue
            seen.add(key)
            emit(key, 3)
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # collapse rules that fire on exactly the same rows: keep the simplest, then
    # the one whose extra terms earn their place
    out = (out.sort_values(["arity", "gain_over_subset"], ascending=[True, False],
                           kind="stable")
              .drop_duplicates("_fp")
              .drop_duplicates("rule"))
    return (out.sort_values(["precision", "fires"], ascending=[False, False],
                            kind="stable")
               .drop(columns=["_fp"]).reset_index(drop=True))


# ------------------------------------------------------------ multivariate --
def multivariate(df, numeric, kw_cols, kw_from, seed=7, repeats=3):
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.inspection import permutation_importance
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import average_precision_score, roc_auc_score
    from sklearn.model_selection import StratifiedKFold, cross_val_predict
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    head_only = [c for c in numeric if c not in kw_cols and not c.endswith("_bd")]
    head_body = [c for c in numeric if c not in kw_cols]
    results = []
    # the first two differ ONLY by the 70 body columns, so the gap between them
    # is the incremental value of scoring bodies at all
    for tag, cols, sub, chart in (
            ("FULL / headline only", head_only, df, False),
            ("FULL / headline+body", head_body, df, True),
            ("HONEST / +kw15", list(numeric), df[df[DATE_COL] >= kw_from], True)):
        X = sub[[c + "__num" for c in cols]].astype(float)
        X.columns = cols
        cat = sub[["BroadClass"]].replace("", "(blank)")
        y = sub["_y"].to_numpy(int)
        if y.sum() < 20:
            continue
        pre = ColumnTransformer([
            ("num", Pipeline([("imp", SimpleImputer(strategy="median")),
                              ("sc", StandardScaler())]), cols),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=25),
             ["BroadClass"])])
        Xall = pd.concat([X.reset_index(drop=True),
                          cat.reset_index(drop=True)], axis=1)
        cv = StratifiedKFold(5, shuffle=True, random_state=seed)
        for name, clf in (
                ("logistic-L2", LogisticRegression(max_iter=3000, C=0.3,
                                                   class_weight="balanced")),
                ("hist-gbdt", HistGradientBoostingClassifier(
                    max_iter=300, learning_rate=0.06, max_leaf_nodes=15,
                    min_samples_leaf=40, l2_regularization=1.0,
                    random_state=seed))):
            # repeat the whole CV under different fold seeds: the spread across
            # seeds is the yardstick for judging whether a between-model gap is
            # real, and with only a few hundred positives it is not small
            aucs, prs, tds, p = [], [], [], None
            for r in range(max(repeats, 1)):
                cvr = StratifiedKFold(5, shuffle=True, random_state=seed + r)
                pipe = Pipeline([("pre", pre), ("clf", clf)])
                pr_ = cross_val_predict(pipe, Xall, y, cv=cvr,
                                        method="predict_proba")[:, 1]
                aucs.append(roc_auc_score(y, pr_))
                prs.append(average_precision_score(y, pr_))
                tds.append(float(y[np.argsort(-pr_)[:max(len(y) // 10, 1)]].mean()))
                if p is None:
                    p = pr_
            results.append(dict(dataset=tag, model=name, chart=chart,
                                n=len(y), n_tp=int(y.sum()),
                                base_rate=float(y.mean()),
                                roc_auc=float(np.mean(aucs)),
                                roc_auc_sd=float(np.std(aucs)),
                                pr_auc=float(np.mean(prs)),
                                top_decile_precision=float(np.mean(tds)),
                                cv_repeats=int(max(repeats, 1)),
                                oof=p))
        # importances from a single full fit of the GBDT
        pipe = Pipeline([("pre", pre),
                         ("clf", HistGradientBoostingClassifier(
                             max_iter=300, learning_rate=0.06, max_leaf_nodes=15,
                             min_samples_leaf=40, l2_regularization=1.0,
                             random_state=seed))]).fit(Xall, y)
        imp = permutation_importance(pipe, Xall, y, n_repeats=8,
                                     random_state=seed, scoring="roc_auc")
        results[-1]["importance"] = pd.DataFrame(
            dict(feature=Xall.columns, importance=imp.importances_mean,
                 std=imp.importances_std)).sort_values("importance",
                                                       ascending=False)
    return results


# ------------------------------------------------------------------ charts --
def _style(ax):
    ax.set_facecolor(C_SURFACE)
    ax.grid(True, color=C_GRID, lw=0.8, zorder=0)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(C_GRID)
    ax.tick_params(colors=C_INK2, labelsize=8, length=0)


def make_charts(outdir, uni, ranked, curves, rules, df, mv, tp_threshold):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(outdir, exist_ok=True)
    plt.rcParams.update({"figure.facecolor": C_SURFACE,
                         "savefig.facecolor": C_SURFACE,
                         "text.color": C_INK, "axes.labelcolor": C_INK2,
                         "font.size": 9})

    top = ranked.head(6)

    # 1. precision vs threshold (coverage encoded as marker size, not a 2nd axis)
    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    for ax, (_, r) in zip(axes.ravel(), top.iterrows()):
        (cur, base, window) = curves[r.feature]
        cuts, prec, fires = cur[r.direction]
        ok = fires >= 20
        ax.plot(cuts[ok], prec[ok], color=C_BLUE, lw=2, zorder=3)
        ax.scatter(cuts[ok], prec[ok], s=np.clip(fires[ok] / 8, 6, 90),
                   color=C_BLUE, alpha=0.35, edgecolor="none", zorder=2)
        ax.axhline(base, color=C_NEUTRAL, lw=1.2, ls="--", zorder=1)
        ax.axvline(r.threshold, color=C_ORANGE, lw=1.5, zorder=4)
        ax.set_title(f"{r.feature}  ({r.direction}, {window})", fontsize=9,
                     color=C_INK)
        ax.set_xlabel("threshold")
        ax.set_ylabel(f"P(high ≥ {tp_threshold:g}%)")
        _style(ax)
    fig.suptitle("Precision vs threshold — dot size = number of fires; "
                 "dashed = base rate; orange = selected cut",
                 fontsize=10, color=C_INK2)
    fig.tight_layout()
    fig.savefig(f"{outdir}/precision_vs_threshold_top.png", dpi=140)
    plt.close(fig)

    # 2. score distributions, TP vs FP
    fig, axes = plt.subplots(2, 3, figsize=(13, 7))
    for ax, (_, r) in zip(axes.ravel(), top.iterrows()):
        sub = df if r.window == "FULL" else df[df["_kw_ok"]]
        v = sub[r.feature + "__num"].astype(float)
        m = v.notna()
        pos = v[m & (sub["_y"] == 1)]
        neg = v[m & (sub["_y"] == 0)]
        bins = np.histogram_bin_edges(v[m], bins=30)
        ax.hist(neg, bins=bins, density=True, color=C_BLUE, alpha=0.55,
                label=f"miss (n={len(neg)})", zorder=2)
        ax.hist(pos, bins=bins, density=True, color=C_ORANGE, alpha=0.6,
                label=f"≥{tp_threshold:g}% (n={len(pos)})", zorder=3)
        ax.set_title(f"{r.feature}  AUC {r.auc:.3f}", fontsize=9, color=C_INK)
        ax.set_ylabel("density")
        ax.legend(frameon=False, fontsize=7)
        _style(ax)
    fig.suptitle("Score distribution — hits vs misses", fontsize=10, color=C_INK2)
    fig.tight_layout()
    fig.savefig(f"{outdir}/score_distributions_top.png", dpi=140)
    plt.close(fig)

    # 3. ROC of top features + CV models
    from sklearn.metrics import roc_curve
    fig, ax = plt.subplots(figsize=(7.5, 6.5))
    pal = [C_BLUE, C_ORANGE, C_AQUA, "#eda100", "#e87ba4", "#4a3aa7"]
    for (_, r), col in zip(top.iterrows(), pal):
        sub = df if r.window == "FULL" else df[df["_kw_ok"]]
        v = sub[r.feature + "__num"].astype(float)
        m = v.notna()
        x = v[m].to_numpy()
        sign = 1 if r.auc >= 0.5 else -1
        fpr, tpr, _ = roc_curve(sub.loc[m, "_y"], sign * x)
        ax.plot(fpr, tpr, color=col, lw=2,
                label=f"{r.feature} ({max(r.auc, 1 - r.auc):.3f})")
    for m_ in mv:
        if m_["model"] == "hist-gbdt" and m_.get("chart"):
            sub = df if not m_["dataset"].startswith("HONEST") else df[df["_kw_ok"]]
            fpr, tpr, _ = roc_curve(sub["_y"], m_["oof"])
            ax.plot(fpr, tpr, color=C_INK, lw=2, ls="--",
                    label=f"GBDT CV, {m_['dataset']} ({m_['roc_auc']:.3f})")
    ax.plot([0, 1], [0, 1], color=C_NEUTRAL, lw=1, ls=":")
    ax.set_xlabel("false-positive rate")
    ax.set_ylabel("true-positive rate")
    ax.set_title(f"ROC — single scores vs full models (target ≥ {tp_threshold:g}%)",
                 fontsize=10, color=C_INK)
    ax.legend(frameon=False, fontsize=7.5, loc="lower right")
    _style(ax)
    fig.tight_layout()
    fig.savefig(f"{outdir}/roc_top_features.png", dpi=140)
    plt.close(fig)

    # 4. rule frontier
    if not rules.empty:
        fig, ax = plt.subplots(figsize=(8.5, 6))
        for arity, col, lab in ((1, C_AQUA, "single cut"), (2, C_BLUE, "2-way AND"),
                                (3, C_ORANGE, "3-way AND")):
            s = rules[rules.arity == arity]
            if s.empty:
                continue
            ax.scatter(s.fires, s.precision, s=26, color=col, alpha=0.55,
                       edgecolor=C_SURFACE, linewidth=0.5, label=lab, zorder=3)
        ax.axhline(rules.base_rate.iloc[0], color=C_NEUTRAL, lw=1.2, ls="--",
                   zorder=1)
        ax.set_xscale("log")
        ax.set_xlabel("fires (coverage, log scale)")
        ax.set_ylabel(f"precision — P(high ≥ {tp_threshold:g}%)")
        ax.set_title("Rule frontier — precision vs coverage (dashed = base rate)",
                     fontsize=10, color=C_INK)
        ax.legend(frameon=False, fontsize=8)
        _style(ax)
        fig.tight_layout()
        fig.savefig(f"{outdir}/rule_frontier.png", dpi=140)
        plt.close(fig)
    log(f"charts -> {outdir}/")


# ------------------------------------------------------------------ report --
def md_table(df, cols, floatfmt=4, limit=None):
    d = df[cols].head(limit) if limit else df[cols]
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    lines = [head, sep]
    for _, r in d.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                cells.append("" if pd.isna(v) else f"{v:.{floatfmt}g}")
            else:
                cells.append(str(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def write_report(path, args, df, uni, ranked, catt, rules, mv, kw_cols):
    base = df["_y"].mean()
    hon = df[df["_kw_ok"]]
    L = []
    A = L.append
    A(f"# fromPR_High(%) ≥ {args.tp_threshold:g}% — sentiment association report\n")
    A(f"Input: `{os.path.basename(args.input)}`  ")
    A(f"Generated: {datetime.now():%Y-%m-%d %H:%M}  ")
    A(f"TP definition: `fromPR_High(%) >= {args.tp_threshold:g}`\n")
    A("## Populations\n")
    A(f"| window | span | rows | TP | base rate |")
    A("|---|---|---|---|---|")
    A(f"| FULL | {df[DATE_COL].min()} → {df[DATE_COL].max()} | {len(df)} | "
      f"{int(df['_y'].sum())} | {100*base:.2f}% |")
    A(f"| HONEST (kw15) | {args.kw_honest_from} → {df[DATE_COL].max()} | {len(hon)} | "
      f"{int(hon['_y'].sum())} | {100*hon['_y'].mean():.2f}% |\n")
    A(f"The kw15 columns (`{', '.join(kw_cols)}`) come from a model trained on "
      f"2026-05-03..2026-06-19, so they — and every rule or model containing them — "
      f"are scored on the HONEST window only. All other features use FULL. "
      f"**Never compare a HONEST precision to a FULL precision directly**: the "
      f"HONEST base rate is {100*hon['_y'].mean():.2f}% vs {100*base:.2f}%, so use "
      f"the `lift` column instead.\n")
    A("### Data availability\n")
    sparse = []
    for c in ranked.feature:
        v = df[c + "__num"]
        if v.notna().mean() >= 0.99:
            continue
        by_month = df.assign(_ok=v.notna()).groupby(df[DATE_COL].str[:7])["_ok"].mean()
        sparse.append(dict(feature=c, non_null=int(v.notna().sum()),
                           pct=100 * v.notna().mean(),
                           tp_rate_present=100 * df.loc[v.notna(), "_y"].mean(),
                           tp_rate_absent=100 * df.loc[v.isna(), "_y"].mean(),
                           **{m: round(100 * x) for m, x in by_month.items()}))
    if sparse:
        sp = pd.DataFrame(sparse).sort_values("pct")
        A("Columns that are not populated on every row. **A rule containing one of "
          "these is implicitly restricted to the rows where it exists** — if that "
          "coverage is concentrated in later months, the rule is a statement about "
          "that period, not about the whole corpus.\n")
        A(md_table(sp, [c for c in sp.columns]))
        A("")
    A(f"`*_ms_hd` columns are model inference milliseconds, not sentiment, and are "
      f"excluded. Rows are not independent: {df['Symbol'].nunique()} unique symbols "
      f"across {len(df)} rows — `uniq_symbols` and `max_symbol_share` on every "
      f"result show whether a finding is one symbol repeating.\n")

    A("## 1. Univariate — ranked by discrimination (|AUC − 0.5|)\n")
    A(md_table(ranked.head(25), ["feature", "window", "n", "base_rate", "auc",
                                 "spearman_rho", "mw_q_bh", "direction", "threshold",
                                 "fires", "tp", "precision", "lift", "uniq_symbols"]))
    A("\n`auc` is of the raw score, so < 0.5 means *lower is better* — the "
      "`direction` shown is always the one the AUC implies, and the threshold is "
      "the best cut in that direction. `mw_q_bh` is "
      "the BH-FDR-adjusted Mann-Whitney p across all features tested — treat "
      "anything above 0.10 as noise no matter how good its precision looks.\n")

    A(f"### Best single cuts by precision (≥ {args.min_fires} fires, "
      f"AUC-consistent direction only)\n")
    A(md_table(ranked.sort_values("precision", ascending=False,
                                  kind="stable").head(25),
               ["feature", "window", "n", "base_rate", "direction", "threshold",
                "fires", "tp", "precision", "lift", "recall", "coverage",
                "median_high_fired", "uniq_symbols", "max_symbol_share"]))

    A("\n### Headline vs body, same model and channel\n")
    hb = head_vs_body(uni)
    if hb.empty:
        A("_no `_bd` twins in this corpus — every encoder column is headline-only, "
          "so nothing here says whether the body carries more signal. Score the "
          "bodies with `tools/Encoder_Models/BodyScore11encoders.py` to populate "
          "this._\n")
    else:
        nb = int((hb.winner == "body").sum())
        A(f"Body wins on **{nb} of {len(hb)}** channels (by |AUC − 0.5|). "
          f"`disc_delta` > 0 means the body version discriminates better.\n")
        A(md_table(hb, ["channel", "auc_head", "auc_body", "disc_delta", "winner",
                        "prec_head", "prec_body", "rho_head", "rho_body"]))
        A("")
    A("\n## 2. Categorical columns — hit rate by level\n")
    if not catt.empty:
        A(md_table(catt.head(30), ["column", "level", "n", "tp", "precision",
                                   "lift", "ci_lo", "ci_hi", "median_high",
                                   "uniq_symbols"]))
    A("\n## 3. AND-rules\n")
    if rules.empty:
        A("_no rule cleared the minimum fire count_\n")
    else:
        for w in rules.window.unique():
            s = rules[rules.window == w]
            A(f"### {w} window\n")
            A(md_table(s.head(25), ["rule", "arity", "fires", "tp", "precision",
                                    "lift_evaluable", "gain_over_subset",
                                    "n_evaluable", "prec_first_half",
                                    "prec_second_half", "boot_lo", "median_high",
                                    "uniq_symbols", "max_symbol_share"]))
            A("")
        A("`lift_evaluable` divides precision by the base rate among rows where every "
          "term of the rule is non-null — the only fair denominator when a term is a "
          "sparse column (see Data availability). `n_evaluable` is how many rows that "
          "is; when it is far below the window size, the rule is really a statement "
          "about that sub-population.\n")
        A("`gain_over_subset` is the rule's precision minus the best precision of any "
          "of its own sub-rules with one term dropped. **At or below zero means the "
          "extra term buys nothing** and the shorter rule is the real finding. Rules "
          "firing on an identical set of rows have been collapsed to the simplest "
          "form.\n")
        A("`prec_first_half` / `prec_second_half` split the rule's own window at its "
          "median date. A rule whose precision collapses in the second half is fitted "
          "to the past, not predictive. `boot_lo`/`boot_hi` are a 90% bootstrap "
          "interval on precision — when `boot_lo` sits at or below the base rate the "
          "rule is not distinguishable from chance.\n")
        A("**These rules are selected on the same data they are scored on.** With "
          f"thousands of candidate conjunctions the top precision is inflated; treat "
          "them as hypotheses ranked by plausibility, and prefer rules that are "
          "stable across halves, have a `boot_lo` well above base rate, and spread "
          "over many symbols.\n")

    A("## 4. Multivariate — how much total signal exists\n")
    mvt = pd.DataFrame([{k: v for k, v in m.items()
                         if k not in ("oof", "importance", "chart")} for m in mv])
    A(md_table(mvt,
               ["dataset", "model", "n", "n_tp", "base_rate", "roc_auc",
                "roc_auc_sd", "pr_auc", "top_decile_precision", "cv_repeats"]))
    A("\n5-fold stratified CV, out-of-fold predictions, averaged over `cv_repeats` "
      "fold seeds; `roc_auc_sd` is the spread across those seeds. **Any gap between "
      "two rows smaller than their sd is noise, not a finding** — in particular the "
      "headline-only vs headline+body pair, which differ only by the 70 body "
      "columns. `top_decile_precision` is the "
      "hit rate among the highest-scoring 10% of rows — the practical ceiling any "
      "hand-built rule is competing against.\n")
    for m in mv:
        if "importance" in m:
            A(f"### Permutation importance (GBDT, {m['dataset']})\n")
            A(md_table(m["importance"].head(20), ["feature", "importance", "std"]))
            A("")
    A("## Charts\n")
    for f in ("precision_vs_threshold_top.png", "score_distributions_top.png",
              "roc_top_features.png", "rule_frontier.png"):
        A(f"- `{os.path.basename(args.chartdir)}/{f}`")
    with open(path, "w") as fh:
        fh.write("\n".join(L) + "\n")
    log(f"report -> {path}")


# -------------------------------------------------------------------- main --
def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True)
    ap.add_argument("--tp-threshold", type=float, default=50.0)
    ap.add_argument("--kw-honest-from", default="2026-06-20",
                    help="first ArrivalDate NOT in the kw model's training window")
    ap.add_argument("--kw-cols", default="kw15_proba_bd,kw15_proba_hd")
    ap.add_argument("--min-fires", type=int, default=20)
    ap.add_argument("--min-coverage", type=int, default=300,
                    help="skip features with fewer non-null values")
    ap.add_argument("--top-features", type=int, default=20,
                    help="features carried into the AND-rule search")
    ap.add_argument("--rule-cuts", type=int, default=12)
    ap.add_argument("--beam", type=int, default=60,
                    help="best 2-way rules expanded to 3-way")
    ap.add_argument("--outdir", default="outputs")
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--cv-repeats", type=int, default=3,
                    help="CV fold seeds to average the multivariate scores over")
    ap.add_argument("--no-charts", action="store_true")
    args = ap.parse_args()

    kw_cols = [c.strip() for c in args.kw_cols.split(",") if c.strip()]
    stem = args.prefix or (os.path.basename(args.input).split("_")[0] +
                           f"_fromPRhigh{args.tp_threshold:g}")
    args.chartdir = os.path.join(args.outdir, f"charts_{stem}")

    df = load(args.input, args.tp_threshold)
    df["_kw_ok"] = df[DATE_COL] >= args.kw_honest_from
    numeric, categorical = discover_features(df, kw_cols, args.min_coverage)
    log(f"features: {len(numeric)} numeric, {len(categorical)} categorical")

    grid_q = np.arange(0.01, 1.0, 0.01)
    uni, curves = univariate(df, numeric, args.kw_honest_from, args.min_fires,
                             grid_q)
    log(f"univariate: {uni.feature.nunique()} features scored")

    catt = categorical_table(df, categorical, args.min_fires)

    ranked = ranked_features(uni)
    top_full = [f for f in ranked.feature if f not in kw_cols][:args.top_features]
    top_hon = list(ranked.feature[:args.top_features])
    for c in kw_cols:
        if c in numeric and c not in top_hon:
            top_hon.append(c)

    log("rule search: FULL window")
    r1 = search_rules(df, top_full, ranked, "FULL", args.min_fires,
                      args.rule_cuts, args.beam)
    log("rule search: HONEST window (kw15-containing rules)")
    r2 = search_rules(df[df["_kw_ok"]].reset_index(drop=True), top_hon, ranked,
                      "HONEST", args.min_fires, args.rule_cuts, args.beam,
                      require_cols=set(kw_cols))
    rules = pd.concat([x for x in (r1, r2) if not x.empty], ignore_index=True)

    # single cuts, in the same frame, so the frontier chart can compare arities
    if not ranked.empty:
        singles = ranked.assign(arity=1, rule=ranked.apply(
            lambda r: f"{r.feature} {r.direction} {r.threshold:.4g}", axis=1))
        singles = singles.rename(columns={"median_high_fired": "median_high",
                                          "mean_high_fired": "mean_high",
                                          "n": "n_evaluable",
                                          "base_rate": "base_rate_evaluable",
                                          "lift": "lift_evaluable"})
        singles["base_rate"] = singles["base_rate_evaluable"]
        keep = ["rule", "arity", "window", "base_rate", "fires", "tp", "precision",
                "lift_evaluable", "n_evaluable", "base_rate_evaluable",
                "median_high", "mean_high", "uniq_symbols", "max_symbol_share"]
        rules = pd.concat([rules, singles[keep]], ignore_index=True)
    rules = rules.sort_values(["precision", "fires"], ascending=[False, False]) \
                 .reset_index(drop=True)

    log("multivariate")
    mv = multivariate(df, numeric, kw_cols, args.kw_honest_from,
                      repeats=args.cv_repeats)

    os.makedirs(args.outdir, exist_ok=True)
    up = f"{args.outdir}/{stem}_univariate.tsv"
    cp = f"{args.outdir}/{stem}_categorical.tsv"
    rp = f"{args.outdir}/{stem}_rules.tsv"
    uni.to_csv(up, sep="\t", index=False, float_format="%.6g")
    catt.to_csv(cp, sep="\t", index=False, float_format="%.6g")
    rules.to_csv(rp, sep="\t", index=False, float_format="%.6g")
    log(f"tables -> {up}, {cp}, {rp}")

    if not args.no_charts:
        make_charts(args.chartdir, uni, ranked, curves, rules, df, mv,
                    args.tp_threshold)

    write_report(f"{args.outdir}/{stem}_analysis.md", args, df, uni, ranked, catt,
                 rules, mv, kw_cols)


if __name__ == "__main__":
    sys.exit(main())
