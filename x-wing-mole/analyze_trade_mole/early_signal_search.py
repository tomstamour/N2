#!/usr/bin/env python3
"""Earlier signal + a short check afterwards — offline search (2026-10-08).

Idea. The operator wants `_late_` files triggered earlier, and wants fewer bad
triggers ("little happens after", "the price rises, then falls"). A more
SENSITIVE rule fires earlier but also fires on more bad files. If a bad signal
shows itself within a second (no more trades, a falling bid), a check of up to
W seconds after the early signal could let the early signal order only when the
tape agrees. The 2.5 rules stay untouched and immediate; the early path is added.

How it is scored.
  * The early signal times come from the REAL mole (`eval_rules.run_corpus`) with
    one or more constants relaxed.
  * The check is evaluated on the RECORDED rows after that time. A recording ends
    at the live trigger (older files) or 60 s after it (2.5-era tails), so an early
    signal has data behind it as long as it fires at least W seconds before the
    recording ends. Otherwise it "has no data": under the strict policy (default)
    it is NOT confirmed, i.e. the file keeps its 2.5 result.
  * Final fire time = min(2.5 fire, confirmation time of the early signal).
  * Cost = neg + 0.4 x neu + 3 x (pos missed + late missed)  (operator, 2026-10-02).

This is an exploration tool. Whatever survives is implemented in trade-mole-2.6
and re-scored through the real code (eval_rules.py).

Usage:
    python early_signal_search.py --baselines ../mole-outputs/baseline-overrides-2026-10-08.csv
    python early_signal_search.py --baselines ... --detail "E5,K2"
"""
import argparse
import logging
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import eval_rules as E  # noqa: E402
import replay_mole as R  # noqa: E402
import confirm_after_signal as C  # noqa: E402
import session_context as SC  # noqa: E402

# --------------------------------------------------------------------------- #
# Sensitive configurations: each relaxes the 2.5 rules in one direction.
# --------------------------------------------------------------------------- #
SENSITIVE = {
    "base": {},
    "E5": {"SURGE_E_MIN_FLOW_X_BASELINE": 5.0},
    "E3": {"SURGE_E_MIN_FLOW_X_BASELINE": 3.0},
    "Ep10": {"SURGE_FLOW_MIN_PRICE_RISE_PCT": 0.010},
    "Ep08": {"SURGE_FLOW_MIN_PRICE_RISE_PCT": 0.008},
    "E5p10": {"SURGE_E_MIN_FLOW_X_BASELINE": 5.0, "SURGE_FLOW_MIN_PRICE_RISE_PCT": 0.010},
    "N3": {"SURGE_N_MIN_PRICE_RISE_PCT": 0.03},
    "N2": {"SURGE_N_MIN_PRICE_RISE_PCT": 0.02},
    "PRE15": {"PRETRADE_MAX_GAP_SEC": 1.5},
    "PRE20": {"PRETRADE_MAX_GAP_SEC": 2.0, "PRETRADE_MAX_RATIO": 50000.0},
    "Dage5": {"SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC": 5.0},
    "Diti40": {"SURGE_ITI_COLLAPSE_MAX_SEC": 0.40},
    "Dvol10": {"SURGE_D_MIN_VOLUME_5S": 10.0},
    "C800": {"BSLN_ITI_LAST3_THRESHOLD": 800.0},
    "NF100": {"SURGE_NF_MIN_FLOW_X_BASELINE": 100.0},
    "NF30": {"SURGE_NF_MIN_FLOW_X_BASELINE": 30.0},
    "vwap5": {"SURGE_MIN_PX_VS_VWAP_PCT": -5.0},
    "ALL": {"SURGE_E_MIN_FLOW_X_BASELINE": 5.0, "SURGE_FLOW_MIN_PRICE_RISE_PCT": 0.010,
            "SURGE_N_MIN_PRICE_RISE_PCT": 0.03, "PRETRADE_MAX_GAP_SEC": 1.5,
            "SURGE_ITI_COLLAPSE_MAX_SESSION_AGE_SEC": 5.0, "SURGE_ITI_COLLAPSE_MAX_SEC": 0.40,
            "BSLN_ITI_LAST3_THRESHOLD": 800.0, "SURGE_NF_MIN_FLOW_X_BASELINE": 100.0},
}


class Ctx:
    """Per-file cache: rows, tape, first-row time, surge onset."""

    def __init__(self):
        self.tape, self.t0, self.onset = {}, {}, {}

    def get(self, path):
        if path not in self.tape:
            rows = C.load(path)
            self.tape[path] = C.Tape(rows)
            self.t0[path] = E._first_row_time(path)
            c = E.context(path)
            self.onset[path] = c["onset_t"]
        return self.tape[path], self.t0[path], self.onset[path]


def combine(base, sens, ctx, W, check, policy):
    """Final result for one file given the 2.5 result, the sensitive-config result
    and the check. Returns (fired, t_final, kind) with kind in
    same / earlier / added / unconfirmed / nodata."""
    b_fired, b_t = base["fired"], base["t_fire"]
    if not sens["fired"]:
        return b_fired, b_t, "same"
    if b_fired and sens["t_fire"] >= b_t - 1e-6:
        return b_fired, b_t, "same"                     # no earlier than 2.5
    tape, t0, _ = ctx.get(sens["path"])
    T = t0 + sens["t_fire"]
    state, tc = C.first_confirm_time(tape, T, W, check)
    if state == "ok":
        return True, tc - t0, "added" if not b_fired else "earlier"
    if state == "nodata" and policy == "optimistic":
        return True, sens["t_fire"] + W, "added" if not b_fired else "earlier"
    return b_fired, b_t, state if state == "nodata" else "unconfirmed"


def score(files_res):
    """files_res: list of (lab, fired, t_final, onset). -> counts + cost."""
    n = {k: 0 for k in ("pos", "neu", "neg", "late")}
    f = {k: 0 for k in n}
    for lab, fired, _, _ in files_res:
        n[lab] += 1
        if fired:
            f[lab] += 1
    cost = f["neg"] + E.NEU_WEIGHT * f["neu"]
    missed = (n["pos"] - f["pos"]) + (n["late"] - f["late"])
    after = [t - on for lab, fired, t, on in files_res
             if lab == "late" and fired and t is not None and on is not None]
    tt = [t for lab, fired, t, _ in files_res if lab == "late" and fired and t is not None]
    return n, f, cost + E.POS_MISS_WEIGHT * missed, (statistics.median(after) if after else None), \
        (statistics.median(tt) if tt else None)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", default="trade-mole-2.5.py")
    ap.add_argument("--baselines", default=None)
    ap.add_argument("--window", type=float, default=1.0)
    ap.add_argument("--policy", choices=("strict", "optimistic"), default="strict")
    ap.add_argument("--detail", default=None, metavar="CONFIG,CHECK",
                    help="list every file that changes for one pair, e.g. E5,K2")
    ap.add_argument("--confirm-base", action="store_true",
                    help="ALSO require the check on 2.5's own fire when data exists")
    args = ap.parse_args()

    logging.disable(logging.CRITICAL)
    log = logging.getLogger("early")
    mole = R.load_mole(args.module)
    if args.baselines:
        R.load_baseline_overrides(args.baselines)
        SC.BASELINE_OVERRIDES = R.BASELINE_OVERRIDES
    files = E.labelled_files()
    ctx = Ctx()
    W = args.window

    res = {name: {d["file"]: d for d in E.run_corpus(mole, dict(ov), files, log)}
           for name, ov in SENSITIVE.items()}
    base = res["base"]
    lab_of = {n: d["lab"] for n, d in base.items()}

    def onset_of(name):
        return ctx.get(base[name]["path"])[2]

    # reference line
    ref = [(lab_of[n], base[n]["fired"], base[n]["t_fire"], onset_of(n)) for n in base]
    n0, f0, c0, a0, t0_ = score(ref)
    print(f"window {W:g}s  policy {args.policy}   files {len(base)}")
    print(f"2.5 as is: pos {f0['pos']}/{n0['pos']} neu {f0['neu']}/{n0['neu']} neg {f0['neg']}/{n0['neg']} "
          f"late {f0['late']}/{n0['late']}  total {c0:.1f}  late: median after surge start "
          f"{'-' if a0 is None else f'{a0:.2f}'}s  median time {'-' if t0_ is None else f'{t0_:.2f}'}s\n")

    rows = []
    for cname in SENSITIVE:
        if cname == "base":
            continue
        for kname, (kdesc, kfn) in C.CHECKS.items():
            out, kinds = [], {}
            for name in base:
                fired, t, kind = combine(base[name], res[cname][name], ctx, W, kfn, args.policy)
                out.append((lab_of[name], fired, t, onset_of(name)))
                kinds.setdefault(kind, []).append((lab_of[name], name))
            n, f, cost, aft, tt = score(out)
            added_bad = sum(1 for lab, _ in kinds.get("added", []) if lab == "neg")
            added_grey = sum(1 for lab, _ in kinds.get("added", []) if lab == "neu")
            added_good = sum(1 for lab, _ in kinds.get("added", []) if lab in ("pos", "late"))
            earlier_late = sum(1 for lab, _ in kinds.get("earlier", []) if lab == "late")
            rows.append((cost, cname, kname, f, aft, tt, added_bad, added_grey, added_good, earlier_late,
                         len(kinds.get("nodata", [])), len(kinds.get("unconfirmed", [])), kinds))
    rows.sort(key=lambda r: (r[0], r[1], r[2]))
    print(f"{'config':8}{'check':5}{'pos':>5}{'neu':>5}{'neg':>5}{'late':>5}{'total':>8}"
          f"{'d_tot':>7}{'addBad':>7}{'addGry':>7}{'addGood':>8}{'lateEarlier':>12}{'noData':>7}{'blocked':>8}"
          f"  late after-start / time")
    for cost, cname, kname, f, aft, tt, ab, ag, agd, el, nd, ub, _ in rows[:60]:
        print(f"{cname:8}{kname:5}{f['pos']:5}{f['neu']:5}{f['neg']:5}{f['late']:5}{cost:8.1f}{cost - c0:+7.1f}"
              f"{ab:7}{ag:7}{agd:8}{el:12}{nd:7}{ub:8}  "
              f"{'-' if aft is None else f'{aft:.2f}'} / {'-' if tt is None else f'{tt:.2f}'}")

    if args.detail:
        cname, kname = args.detail.split(",")
        for r in rows:
            if r[1] == cname and r[2] == kname:
                kinds = r[-1]
                print(f"\nfiles that change for {cname} + {kname}:")
                for kind in ("added", "earlier", "unconfirmed", "nodata"):
                    for lab, name in sorted(kinds.get(kind, [])):
                        print(f"  {kind:12}{lab:5}{name}")


if __name__ == "__main__":
    main()
