#!/usr/bin/env python3
"""Score a trade-mole rule set against the operator's labelled corpus.

Why this exists alongside sweep_thresholds.py (2026-08-14 recalibration):

  * sweep_thresholds.py globs only ``_pos_/_neg_/_neu_`` and so silently drops
    the ``_neutral_`` and ``_medium_`` files the operator also labelled.
  * More importantly it inherited replay_mole's "full trade-path recording"
    requirement, which excluded EVERY pre-trade-only recording — 26 of the 90
    labelled files >= 2026-07-09, including 8 positives and 10 negatives. The
    pre-trade path is roughly half of all false fires, so every precision number
    published before this file describes the trade path only.
  * It scores pos/neu/neg as three independent tallies. The operator's stated
    objective is a single weighted trade-off (neu is a *soft* negative), so the
    comparison between candidates needs one number.

2026-10-02 (trade-mole-2.5 pass) additions:

  * ``total`` — the operator's full trade-off: a missed good trigger (``_pos_``
    or ``_late_``) costs POS_MISS_WEIGHT = 3 bad ones, i.e.
    ``total = neg + 0.4 x neu + 3 x (pos missed + late missed)``. The old
    ``weighted-cost`` (neg + 0.4 x neu) is still printed beside it.
  * WHEN and AT WHAT PRICE each file fires (``--timing``), and for ``_late_`` /
    ``_pos_`` files how long after the surge start (``session_context.onset``)
    and how far above the surge-start price. ``_late_`` means "fire earlier",
    so a rule set is judged on these, not on fired/not-fired alone.
  * ``--against-module`` / ``--against-set`` / ``--against-disable`` — replay a
    reference configuration too and list every file whose decision, rule, time
    or price changed (the 2.4 -> 2.5 equivalence check and every candidate diff).
  * ``--unlabelled`` — the control set: every unlabelled recording since
    MIN_DATE. A candidate must not add fires there without a reason (2.4's
    branch NF scored perfectly on labels and was caught only here).
  * ``--min-date`` / ``--max-date`` — the date split (tune on older files,
    confirm on newer ones).

2026-10-08 (trade-mole-2.6 pass) additions:

  * ``_neu-late_`` (a grey-zone file that fired late) is scored as grey and its fire
    time is also listed with the late files (``tag`` in every result row).
  * ``--baselines CSV`` replays the files named in the CSV with corrected baselines
    (columns file, iti, size) instead of the ones recorded in them: the orchestrator
    served the 2026-09-29 stock list on 10-01/02 because the nightly job had
    stalled. ``--against-recorded-baselines`` also replays with the recorded values
    and lists every file the correction changes. Table made by
    ``make_baseline_overrides.py`` from a RetroUniverse-3 run.
  * trade-mole-2.6 ``CONFIRM_*``: a signal still HELD when its recording ends cannot
    be resolved (older recordings stop at the live trigger). Such files are counted
    as "no fire" in the main line and as fired in the "upper bound" line; ``--against``
    diffs show them as UNRESOLVED, not REMOVED.

Rules are switched off by neutralising their thresholds rather than by editing
should_fire, so the real OR is always what runs. Rule A is the exception: its
threshold is a def-time default argument, unreachable through a module-constant
override, so it is patched onto the instance after construction.

⚠ CENSORING. A recording stops at the live trigger that produced it. A rule set
can be seen firing EARLIER than that, never LATER: "does not fire" on such a
file means "not inside the recorded window". ``--timing`` marks those files.
Recordings made by trade-mole-2.5+ carry a post-trigger tail (up to 60 s, rows
flagged post_trigger) that is replayed like any other rows, so on those files a
stricter rule CAN be seen firing later — within the tail, which also contains
our own x-wing prints.

Usage:
    python eval_rules.py                       # score current 2.2 constants
    python eval_rules.py --module trade-mole-2.3.py
    python eval_rules.py --only D              # D as the sole rule
    python eval_rules.py --disable B,D
    python eval_rules.py --set NAME=VALUE ...
    python eval_rules.py --leave-one-out       # per-rule economics table
    python eval_rules.py --module trade-mole-2.4.py --timing
    python eval_rules.py --module trade-mole-2.5.py --against-module trade-mole-2.4.py
    python eval_rules.py --module trade-mole-2.5.py --unlabelled --against-module trade-mole-2.4.py
"""
import argparse, glob, logging, os, re, sys, collections, statistics

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import replay_mole as R  # noqa: E402
import session_context as SC  # noqa: E402

OUT_DIR = os.path.normpath(os.path.join(HERE, "..", "mole-outputs"))
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
MIN_DATE = "2026-07-09"
# The operator used several spellings for the same three classes.
LABEL_ALIASES = {"pos": "pos", "positive": "pos",
                 "neg": "neg", "negative": "neg",
                 "neu": "neu", "neutral": "neu", "medium": "neu",
                 # _neu-late_ (first used 2026-10-06): a grey-zone file that fired
                 # late. Scored as grey (cost NEU_WEIGHT if it fires); its fire time
                 # is also printed beside the late files (timing_summary, tag).
                 "neu-late": "neu",
                 # _late_ = "I wanted this one, but sooner". It is a POSITIVE
                 # for recall purposes and carries a latency objective, so it is
                 # tracked separately and left OUT of the weighted cost; its
                 # time-to-fire is what matters. (2026-09-18)
                 "late": "late"}
# A _neu_ costs less than a _neg_: the operator would rather not fire on them but
# accepts they look like real surges and may be unavoidable.
NEU_WEIGHT = 0.4
# Operator, 2026-10-02: "losing 1 good trigger is OK only if it removes at least
# 3 bad ones". A missed _pos_ or _late_ file costs this many _neg_ fires.
POS_MISS_WEIGHT = 3.0

# Threshold values that make each rule unsatisfiable. Everything except A is a
# module-level constant read inside should_fire at call time, so an override
# reaches it; A is applied to the instance (see _patch_rule_a).
RULE_OFF = {
    "PRE": {"PRETRADE_STALENESS_FIRE": False},
    "B":   {"BSLN_OVER_PREV_TRD_GAP_THRESHOLD": 1e18},
    "C":   {"BSLN_ITI_LAST3_THRESHOLD": 1e18},
    "D":   {"SURGE_ITI_COLLAPSE_MAX_SEC": -1.0},
    "E":   {"SURGE_FLOW_MIN_PRICE_RISE_PCT": 1e18},
    "A":   {"__rule_a_off__": True},
    # v2.4 branches. Both revert by being set to None, which is also their
    # documented kill switch in the mole.
    "N":   {"SURGE_N_MIN_PRICE_RISE_PCT": None},
    "NF":  {"SURGE_NF_MIN_FLOW_X_BASELINE": None},
}
RULES = ("PRE", "A", "B", "C", "D", "E", "N", "NF")


def classify(reason: str) -> str:
    if reason.startswith("bslnITI_over_pretrade"): return "PRE"
    if reason.startswith("bsln_over_prev"):        return "B"
    if reason.startswith("bslnITI_on_last3"):      return "C"
    if reason.startswith("quote_updates"):         return "A"
    if reason.startswith("measured_ITI"):          return "D"
    if reason.startswith("$rate"):                 return "E"
    if reason.startswith("bigmove_price_chg"):     return "N"    # v2.4
    if reason.startswith("flowburst_x_base"):      return "NF"   # v2.4
    return "?"


def applicable_rules(mole):
    """RULES the loaded module actually implements. Lets --leave-one-out run
    against 2.2/2.3 (no N/NF constants) and 2.4 alike."""
    out = []
    for r in RULES:
        keys = [k for k in RULE_OFF[r] if not k.startswith("__")]
        if all(hasattr(mole, k) for k in keys):
            out.append(r)
    return out


def _in_window(name, min_date, max_date):
    m = DATE_RE.search(name)
    if not m or m.group(1) < min_date:
        return False
    return max_date is None or m.group(1) <= max_date


def labelled_files(min_date=MIN_DATE, max_date=None):
    out = []
    for p in sorted(glob.glob(os.path.join(OUT_DIR, "_*.csv"))):
        b = os.path.basename(p)
        if not _in_window(b, min_date, max_date):
            continue
        lab = LABEL_ALIASES.get(b.split("_")[1])
        if lab:
            out.append((lab, p))
    return out


def unlabelled_files(min_date=MIN_DATE, max_date=None):
    """The control set: live recordings the operator never judged."""
    return [("unl", p) for p in sorted(glob.glob(os.path.join(OUT_DIR, "trade-mole-table_*.csv")))
            if _in_window(os.path.basename(p), min_date, max_date)]


def _patch_rule_a(mole):
    """Force Rule A unsatisfiable. Its threshold is a def-time default argument,
    so a module-constant override cannot reach it; set it on the instance."""
    orig = mole.IBKRSurgeApp.__init__

    def init(self, *a, **kw):
        orig(self, *a, **kw)
        self._surge_min_quote_updates = 10 ** 9
    mole.IBKRSurgeApp.__init__ = init
    return orig


def _first_row_time(path):
    with open(path, newline="") as fh:
        rd = R.csv.DictReader(fh)
        for r in rd:
            t = R._f(r, "local_arrival_time")
            if t is not None:
                return t
    return None


_CTX = {}


def context(path):
    """session_context.context(), cached: it depends on the file and on whether a
    corrected baseline is active for it, not on the rules."""
    key = (path, os.path.basename(path) in R.BASELINE_OVERRIDES)
    if key not in _CTX:
        _CTX[key] = SC.context(path)
    return _CTX[key]


def label_tag(name):
    """The operator's own label spelling (pos, neg, neu-late, medium, ...) or
    'unl' for an unlabelled recording."""
    return name.split("_")[1] if name.startswith("_") and name.count("_") > 1 else "unl"


def replay_one(mole, overrides, lab, path, log):
    """Replay one file through the real handlers; one flat result dict."""
    name = os.path.basename(path)
    app, fire = R.replay_file(path, mole, dict(overrides), stop_at_trigger=True, log=log)
    res = {"lab": lab, "tag": label_tag(name), "file": name, "path": path,
           "replayable": app is not None,
           "fired": False, "rule": "-", "reason": "", "t_fire": None,
           "px_fire": None, "ask_fire": None}
    if app is None:
        return res
    # trade-mole-2.6 CONFIRM_*: a signal still HELD when the recording ends could
    # not be confirmed or dropped — the recording stops at the live trigger, so
    # there is no tape behind it. Reported apart from a real "no fire".
    held = getattr(app, "_pending_signal", None)
    if held and not fire:
        res["pending_at_end"] = True
        res["pending_rule"] = classify(held["reason"])
    if fire:
        rec = next((x for x in app.records if x.get("surge_detected")), None) or {}
        t0 = _first_row_time(path)
        res.update(fired=True, reason=rec.get("surge_reason", ""),
                   rule=classify(rec.get("surge_reason", "")),
                   t_fire=(fire["wall"] - t0) if t0 is not None else None,
                   px_fire=rec.get("price") if rec.get("price") is not None else rec.get("ask"),
                   ask_fire=rec.get("ask") if rec.get("event_type") == "TRADE" else fire.get("ask"))
    return res


def run_corpus(mole, overrides, files, log):
    """Replay every file under one configuration. Returns a list of dicts."""
    overrides = dict(overrides)
    rule_a_off = overrides.pop("__rule_a_off__", False)
    saved_init = _patch_rule_a(mole) if rule_a_off else None
    try:
        return [replay_one(mole, overrides, lab, p, log) for lab, p in files]
    finally:
        if saved_init is not None:
            mole.IBKRSurgeApp.__init__ = saved_init


def optimistic(results):
    """Same results with every signal that was still held at the end of its
    recording counted as a fire (the upper bound; `results` itself is the lower)."""
    out = []
    for d in results:
        if d.get("pending_at_end"):
            d = dict(d, fired=True, rule=d["pending_rule"])
        out.append(d)
    return out


def aggregate(results):
    agg = collections.Counter()
    per_rule = collections.defaultdict(collections.Counter)
    for d in results:
        lab = d["lab"]
        agg[f"{lab}_n"] += 1
        if not d["replayable"]:
            agg[f"{lab}_unreplayable"] += 1
            continue
        if d["fired"]:
            agg[f"{lab}_fire"] += 1
            per_rule[d["rule"]][lab] += 1
    return agg, per_rule


def score(mole, overrides, files, log):
    """Backward-compatible tally: (agg, per_rule, detail)."""
    results = run_corpus(mole, overrides, files, log)
    agg, per_rule = aggregate(results)
    detail = [(d["lab"], d["file"], d["rule"], d["reason"]) for d in results]
    return agg, per_rule, detail


def summarize(agg):
    pos, neu, neg = agg["pos_fire"], agg["neu_fire"], agg["neg_fire"]
    npos, nneu, nneg = agg["pos_n"], agg["neu_n"], agg["neg_n"]
    cost = neg + NEU_WEIGHT * neu
    return pos, npos, neu, nneu, neg, nneg, cost


def total_cost(agg):
    """The operator's full trade-off (2026-10-02)."""
    *_, cost = summarize(agg)
    missed = (agg["pos_n"] - agg["pos_fire"]) + (agg["late_n"] - agg["late_fire"])
    return cost + POS_MISS_WEIGHT * missed


def fmt(agg):
    pos, npos, neu, nneu, neg, nneg, cost = summarize(agg)
    late = f"  late {agg['late_fire']}/{agg['late_n']}" if agg["late_n"] else ""
    return (f"pos {pos:2}/{npos:<2}  neu {neu:2}/{nneu:<2}  neg {neg:2}/{nneg:<2}{late}"
            f"  | recall {pos/npos*100 if npos else 0:5.1f}%  "
            f"weighted-cost {cost:5.1f}  total {total_cost(agg):5.1f}")


# --------------------------------------------------------------------------- #
# Timing (2026-10-02)
# --------------------------------------------------------------------------- #
def timing(d):
    """Fire time/price vs the surge start, plus whether the file is censored
    (it carries a live trigger, so a later fire cannot be observed)."""
    c = context(d["path"])
    out = {"onset_t": c["onset_t"], "onset_px": c["onset_px"],
           "end_reason": c["end_reason"], "after_onset_s": None, "above_onset_pct": None}
    if d["fired"] and c["onset_t"] is not None and d["t_fire"] is not None:
        out["after_onset_s"] = d["t_fire"] - c["onset_t"]
    if d["fired"] and c["onset_px"] and d["px_fire"]:
        out["above_onset_pct"] = (d["px_fire"] - c["onset_px"]) / c["onset_px"] * 100.0
    return out


def _med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def timing_summary(results, labs=("late", "neu-late", "pos")):
    lines = []
    for lab in labs:
        # `lab` matches the scoring class (late, pos) or the operator's own
        # spelling (neu-late is scored as grey but timed like a late file).
        rs = [d for d in results
              if (d["lab"] == lab or d.get("tag") == lab) and d["fired"]]
        if not rs:
            continue
        tt = [timing(d) for d in rs]
        ttf = _med([d["t_fire"] for d in rs])
        aft = _med([t["after_onset_s"] for t in tt])
        abv = _med([t["above_onset_pct"] for t in tt])
        slow = sum(1 for d in rs if d["t_fire"] is not None and d["t_fire"] > 3.0)
        f = lambda v, u: "n/a" if v is None else f"{v:.2f}{u}"
        lines.append(f"  {lab:5} fired {len(rs):3}  median time-to-fire {f(ttf, 's')}  "
                     f"fires >3s {slow:3}  median after surge start {f(aft, 's')}  "
                     f"median above surge-start price {f(abv, '%')}")
    return "\n".join(lines)


def print_timing(results):
    print(f"\n{'lab':5}{'file':48}{'rule':5}{'t_fire':>8}{'px':>9}"
          f"{'onset':>8}{'after':>8}{'above%':>8}  end")
    for d in sorted(results, key=lambda d: (d["lab"], d["file"])):
        t = timing(d)
        f = lambda v, n=2: "-" if v is None else f"{v:.{n}f}"
        lab = "neuL" if d.get("tag") == "neu-late" else d["lab"]
        print(f"{lab:5}{d['file'][-48:]:48}{d['rule']:5}{f(d['t_fire']):>8}"
              f"{f(d['px_fire'], 4):>9}{f(t['onset_t']):>8}{f(t['after_onset_s']):>8}"
              f"{f(t['above_onset_pct'], 1):>8}  {t['end_reason']}")


def print_diff(new, ref, title="changed vs reference"):
    """Every file whose decision, rule, fire time or fire price moved."""
    byf = {d["file"]: d for d in ref}
    rows = []
    for d in new:
        r = byf.get(d["file"])
        if r is None:
            continue
        same = (d["fired"] == r["fired"] and d["rule"] == r["rule"]
                and (d["t_fire"] is None) == (r["t_fire"] is None)
                and (d["t_fire"] is None or abs(d["t_fire"] - r["t_fire"]) < 1e-6)
                and d["px_fire"] == r["px_fire"])
        if not same:
            rows.append((d, r))
    print(f"\n{title}: {len(rows)} file(s)")
    f = lambda v, n=2: "-" if v is None else f"{v:.{n}f}"
    for d, r in sorted(rows, key=lambda x: (x[0]["lab"], x[0]["file"])):
        if d["fired"] and not r["fired"]:
            kind = "ADDED"
        elif r["fired"] and not d["fired"]:
            kind = "UNRESOLVED" if d.get("pending_at_end") else "REMOVED"
        elif d["t_fire"] is not None and r["t_fire"] is not None and d["t_fire"] < r["t_fire"] - 1e-6:
            kind = "EARLIER"
        elif d["t_fire"] is not None and r["t_fire"] is not None and d["t_fire"] > r["t_fire"] + 1e-6:
            kind = "LATER"
        else:
            kind = "SAME-TIME"
        print(f"  {d['lab']:5}{kind:10}{d['file'][-48:]:48} "
              f"ref {r['rule']:4}{f(r['t_fire']):>8}s {f(r['px_fire'], 4):>9}  ->  "
              f"new {d['rule']:4}{f(d['t_fire']):>8}s {f(d['px_fire'], 4):>9}")
    return rows


def parse_sets(pairs):
    base = {}
    for s in pairs:
        k, _, v = s.partition("=")
        if v in ("True", "False"):
            base[k] = (v == "True")
        elif v == "None":
            # The v2.4 kill switches are all "set the constant to None", so the
            # CLI has to be able to express it.
            base[k] = None
        else:
            try:
                base[k] = float(v) if ("." in v or "e" in v.lower()) else int(v)
            except ValueError:
                base[k] = v
    return base


def config(sets, disable, only):
    base = parse_sets(sets)
    off = [r for r in disable.split(",") if r]
    if only:
        keep = [r for r in only.split(",") if r]
        off = [r for r in RULES if r not in keep]
    for r in off:
        base.update(RULE_OFF[r])
    return base


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--module", default="trade-mole-2.2.py")
    ap.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--disable", default="", help="comma list of PRE,A,B,C,D,E,N,NF")
    ap.add_argument("--only", default="", help="comma list; all other rules off")
    ap.add_argument("--leave-one-out", action="store_true")
    ap.add_argument("--detail", action="store_true")
    ap.add_argument("--timing", action="store_true",
                    help="per-file fire time/price vs the surge start")
    ap.add_argument("--against-module", default=None,
                    help="also replay this module and list every changed file")
    ap.add_argument("--against-set", action="append", default=[], metavar="NAME=VALUE")
    ap.add_argument("--against-disable", default="")
    ap.add_argument("--unlabelled", action="store_true",
                    help="score the unlabelled control set instead of the labels")
    ap.add_argument("--min-date", default=MIN_DATE)
    ap.add_argument("--max-date", default=None)
    ap.add_argument("--baselines", default=None, metavar="CSV",
                    help="corrected baselines (columns file, iti, size) replayed in "
                         "place of the recorded ones; see replay_mole.BASELINE_OVERRIDES")
    ap.add_argument("--against-recorded-baselines", action="store_true",
                    help="with --baselines: also replay with the baselines AS RECORDED "
                         "and list every file the correction changes")
    ap.add_argument("-v", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING if args.v else logging.CRITICAL)
    log = logging.getLogger("eval")
    log.addHandler(logging.NullHandler())
    log.propagate = bool(args.v)
    log.setLevel(logging.WARNING if args.v else logging.CRITICAL)

    mole = R.load_mole(args.module)
    files = (unlabelled_files(args.min_date, args.max_date) if args.unlabelled
             else labelled_files(args.min_date, args.max_date))
    base = config(args.set, args.disable, args.only)
    against = bool(args.against_module or args.against_set or args.against_disable)
    if args.baselines:
        n_ov = R.load_baseline_overrides(args.baselines)
        SC.BASELINE_OVERRIDES = R.BASELINE_OVERRIDES      # one shared table
        print(f"corrected baselines: {n_ov} recording(s) from {args.baselines}")

    window = f">= {args.min_date}" + (f" <= {args.max_date}" if args.max_date else "")
    print(f"module: {args.module}   corpus: {len(files)} "
          f"{'UNLABELLED' if args.unlabelled else 'labelled'} files {window}"
          f"   neu weight {NEU_WEIGHT}   missed-good weight {POS_MISS_WEIGHT}")

    rules_here = applicable_rules(mole)
    if args.leave_one_out:
        agg, per_rule, _ = score(mole, dict(base), files, log)
        print(f"\nALL RULES ON     {fmt(agg)}")
        print(f"\n{'first-to-fire':16}{'pos':>5}{'neu':>5}{'neg':>5}{'late':>5}")
        for r in rules_here:
            c = per_rule.get(r, {})
            print(f"  {r:14}{c.get('pos',0):5}{c.get('neu',0):5}{c.get('neg',0):5}{c.get('late',0):5}")
        print(f"\n{'candidate':16}{'result':84}{'d_pos':>7}{'d_cost':>8}{'d_total':>9}")
        b_pos, _, _, _, _, _, b_cost = summarize(agg)
        b_tot = total_cost(agg)
        for r in rules_here:
            o = dict(base); o.update(RULE_OFF[r])
            a2, _, _ = score(mole, o, files, log)
            p2, _, _, _, _, _, c2 = summarize(a2)
            print(f"  without {r:8}{fmt(a2):84}{p2-b_pos:+7}{c2-b_cost:+8.1f}"
                  f"{total_cost(a2)-b_tot:+9.1f}")
        for r in rules_here:
            o = dict(base)
            for x in rules_here:
                if x != r:
                    o.update(RULE_OFF[x])
            a2, _, _ = score(mole, o, files, log)
            print(f"  only    {r:8}{fmt(a2)}")
        return

    results = run_corpus(mole, dict(base), files, log)
    agg, per_rule = aggregate(results)
    if args.unlabelled:
        n_rep = sum(1 for d in results if d["replayable"])
        print(f"\nfires {agg['unl_fire']} of {n_rep} replayable "
              f"({agg['unl_n'] - n_rep} not replayable)")
        for r in list(rules_here) + ["?"]:
            c = per_rule.get(r, {})
            if c.get("unl"):
                print(f"  {r:14}{c['unl']:5}")
    else:
        print(f"\n{fmt(agg)}")
        n_held = sum(1 for d in results if d.get("pending_at_end"))
        if n_held:
            oagg, _ = aggregate(optimistic(results))
            print(f"{fmt(oagg)}   <- upper bound: {n_held} signal(s) still held at the end "
                  f"of their recording counted as fired")
        if agg["pos_unreplayable"] or agg["neg_unreplayable"] or agg["neu_unreplayable"]:
            print(f"  unreplayable: pos {agg['pos_unreplayable']} "
                  f"neu {agg['neu_unreplayable']} neg {agg['neg_unreplayable']}")
        print(f"\n{'first-to-fire':16}{'pos':>5}{'neu':>5}{'neg':>5}{'late':>5}")
        for r in list(rules_here) + ["?"]:
            c = per_rule.get(r, {})
            if sum(c.values()):
                print(f"  {r:14}{c.get('pos',0):5}{c.get('neu',0):5}{c.get('neg',0):5}"
                      f"{c.get('late',0):5}")
        print("\n" + timing_summary(results))
    if args.detail:
        print()
        for d in sorted(results, key=lambda d: (d["lab"], d["file"])):
            print(f"  {d['lab']:4}{d['rule']:5}{d['file'][:52]:54}{d['reason'][:46]}")
    if args.timing:
        print_timing(results)
    if args.baselines and args.against_recorded_baselines:
        kept = dict(R.BASELINE_OVERRIDES)
        R.BASELINE_OVERRIDES.clear()          # replay what the live mole was given
        try:
            rec = run_corpus(mole, dict(base), files, log)
        finally:
            R.BASELINE_OVERRIDES.update(kept)
        ragg, _ = aggregate(rec)
        print(f"\nreference (baselines AS RECORDED):\n{fmt(ragg)}")
        touched = {os.path.basename(p) for p in kept}
        print_diff([d for d in results if d["file"] in touched],
                   [d for d in rec if d["file"] in touched],
                   title="files changed by the corrected baselines")
    if against:
        ref_mole = R.load_mole(args.against_module) if args.against_module else mole
        ref_cfg = config(args.against_set, args.against_disable, "")
        ref = run_corpus(ref_mole, ref_cfg, files, log)
        ragg, _ = aggregate(ref)
        label = (args.against_module or args.module) + (
            " " + " ".join(args.against_set) if args.against_set else "")
        if args.unlabelled:
            print(f"\nreference ({label}): fires {ragg['unl_fire']}")
        else:
            print(f"\nreference ({label}):\n{fmt(ragg)}\n" + timing_summary(ref))
        print_diff(results, ref)


if __name__ == "__main__":
    main()
