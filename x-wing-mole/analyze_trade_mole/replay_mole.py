#!/usr/bin/env python3
"""Offline replay harness for trade-mole recordings.

Reconstructs a recorded session by feeding its rows back through a real
`IBKRSurgeApp` — the mole's OWN tick handlers, `_process_trade_event` and
`_compute_windows` — so recomputed features can never drift from live. No ibapi
connection is made; the app is constructed and driven directly.

Why this exists
---------------
Two recorded-feature defects were found while investigating the AZI 2026-08-07
fire (see the plan / the accompanying analysis report):

  1. the RTVolume/RTTradeVolume twin of a fill is deduped for the ITI series but
     still counted into `_cum_*` and `_trade_log`, inflating every count and
     rate column by a per-symbol-variable factor;
  2. `dollar_rate_{w}s` and friends divide by the NOMINAL window `w` rather than
     elapsed session age, suppressing every flow gate during the first `w`
     seconds of a session — exactly the window a PR surge lives in.

Both live in the recorded columns that the 2026-07-30 calibration was fit on, so
the fixes cannot be evaluated by eye: the labelled corpus has to be regenerated
and the thresholds re-derived. This harness is what regenerates it.

Determinism
-----------
The mole stamps rows with `time.time()` / `time.perf_counter()` at callback
time. Replay installs a fake clock over the module's `time` and `datetime`
handles and advances it to each row's recorded `local_arrival_time`, so a replay
of an untouched recording reproduces that recording (see `--self-check`).

`local_mono_time` is only recorded on TRADE rows; the wall→mono offset is
constant within a session (verified: 1.2e-06 s spread on AZI, i.e. float noise),
so it is recovered from the first TRADE row and applied to every event.

Usage
-----
    # fidelity check: replay unchanged, diff against the original
    python replay_mole.py --self-check ../mole-outputs/trade-mole-table_AZI_*.csv

    # regenerate a corpus with the fixes on
    python replay_mole.py --out-dir ../mole-outputs/recomputed \\
        --set DEDUP_EXCLUDE_TWIN_FROM_COUNTS=True \\
        --set RATE_NORMALIZE_BY_SESSION_AGE=True \\
        '../mole-outputs/_pos_*.csv' '../mole-outputs/_neg_*.csv'

Read-only on its inputs. Run with the venv python (needs pandas + ibapi).
"""

import argparse
import csv
import datetime as _datetime_mod
import glob
import importlib.util
import logging
import os
import re
import sys
import time as _time_mod
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
MOLE_DIR = os.path.normpath(os.path.join(HERE, ".."))
DEFAULT_MODULE = "trade-mole-2.2.py"

# Columns that must be present for a recording to be replayable. The slim
# 15/16-col Rule-F pretrade files have no trade path and are skipped.
# Columns without which a recording cannot be driven back through the handlers.
# `rt_source` is deliberately NOT here: it is a TRADE-row column, and requiring
# it unconditionally is what excluded every pre-trade-only recording from the
# corpus (see session_params). It is enforced below, but only for files that
# actually contain a TRADE row.
REQUIRED_COLS = ("event_type", "local_arrival_time", "tick_type")
TRADE_REQUIRED_COLS = ("rt_source",)

# Corrected baselines (2026-10-08). The orchestrator served the 2026-09-29 stock
# list on 2026-10-01 and 10-02 because the nightly job stalled, so the baselines
# echoed into those recordings (normal time between trades, normal trade size)
# describe the wrong session. {recording basename: row of the override table}; a
# row has at least `iti` (seconds) and `size` (shares). Empty = replay exactly what
# was recorded. Filled by load_baseline_overrides(); see eval_rules.py --baselines.
BASELINE_OVERRIDES = {}


def load_baseline_overrides(path: str) -> int:
    """Fill BASELINE_OVERRIDES from a CSV with the columns `file`, `iti`, `size`
    (any other column is kept and passed on to session_context). Returns the
    number of recordings overridden."""
    BASELINE_OVERRIDES.clear()
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            iti, size = _f(r, "iti"), _f(r, "size")
            if r.get("file") and iti is not None and size is not None:
                BASELINE_OVERRIDES[r["file"]] = dict(r, iti=iti, size=size)
    return len(BASELINE_OVERRIDES)


# --------------------------------------------------------------------------- #
# Module loading (mirrors clerk-1.2.py:_load_module — the filenames have hyphens)
# --------------------------------------------------------------------------- #
def load_mole(filename: str):
    path = os.path.join(MOLE_DIR, filename)
    mod_name = "trademole_replay"
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# Fake clock
# --------------------------------------------------------------------------- #
class ReplayClock:
    """Wall/mono clock driven by the recording. Advances only when the replay
    loop sets it, so every timestamp the mole stamps is the recorded one.

    `mono` is stored outright rather than as `wall + offset`: perf_counter
    values are ~2.4e6 while wall is ~1.8e9, and reconstructing one from the
    other quantises to the larger magnitude's ULP (~2.4e-7 s). That error is
    meaningless against any threshold but it makes the fidelity check unable to
    tell float noise from a real divergence, which is the whole point of it."""

    def __init__(self, mono_offset: float = 0.0):
        self.wall = 0.0
        self.mono = 0.0
        self.mono_offset = mono_offset

    def set(self, wall: float, mono=None) -> None:
        self.wall = wall
        self.mono = mono if mono is not None else wall + self.mono_offset

    def time(self) -> float:
        return self.wall

    def perf_counter(self) -> float:
        return self.mono


class _TimeShim:
    """Stands in for the `time` module inside the mole: the two clock functions
    are replayed, everything else delegates to the real module."""

    def __init__(self, clock: ReplayClock):
        self._clock = clock

    def time(self):
        return self._clock.time()

    def perf_counter(self):
        return self._clock.perf_counter()

    def __getattr__(self, name):
        return getattr(_time_mod, name)


def _make_datetime_shim(clock: ReplayClock):
    """`datetime.now()` must follow the replay clock; the rest of the class
    (fromtimestamp, isoformat, ...) has to keep working, so subclass it."""

    class ReplayDatetime(_datetime_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return _datetime_mod.datetime.fromtimestamp(clock.time(), tz)

    return ReplayDatetime


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #
def _f(row, col):
    v = row.get(col, "")
    if v is None or v == "" or v == "None":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(row, col):
    v = _f(row, col)
    return int(v) if v is not None else None


def _b(row, col):
    v = (row.get(col) or "").strip().lower()
    if v in ("true", "1"):
        return True
    if v in ("false", "0"):
        return False
    return None


def read_rows(path: str):
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        return list(rd), (rd.fieldnames or [])


def session_params(rows):
    """Recover the constructor inputs the live session was given. The baselines
    are echoed into every TRADE row, so the first one carries them.

    PRETRADE-ONLY recordings (2026-08-14): a session that fired on the pre-trade
    staleness path stops recording before any trade prints, so it has no TRADE
    row at all — its narrow 15/16-column table ends at the PRETRADE_SURGE row,
    which is the only row carrying `hist_baseline_avg_iti`. Those recordings were
    silently skipped by the whole sweep/replay stack, which is how the pre-trade
    rule came to be the single largest source of false fires in the labelled
    corpus while every published precision number described the trade path only
    (26 of 90 labelled files >= 2026-07-09, including 8 positives and 10
    negatives). Fall back to the PRETRADE_SURGE/PRETRADE_VETO row so they score.

    `local_mono_time` is absent from those tables, so mono_offset stays 0.0: the
    fake clock then reports perf_counter() == wall, which is self-consistent
    because the pre-trade rule reads only wall-clock stamps (`_first_event_wall`
    minus LAST_TIMESTAMP). Nothing on the pre-trade path divides monotonic
    deltas, so the ULP concern that forces TRADE rows to use their recorded
    `local_mono_time` verbatim does not arise here."""
    baseline_iti = None
    baseline_size = None
    mono_offset = 0.0
    for r in rows:
        if r.get("event_type") != "TRADE":
            continue
        if baseline_iti is None:
            baseline_iti = _f(r, "hist_baseline_avg_iti")
            baseline_size = _f(r, "hist_baseline_trade_size")
            mono = _f(r, "local_mono_time")
            wall = _f(r, "local_arrival_time")
            if mono is not None and wall is not None:
                mono_offset = mono - wall
            break
    if baseline_iti is None:
        for r in rows:
            if r.get("event_type") not in ("PRETRADE_SURGE", "PRETRADE_VETO"):
                continue
            baseline_iti = _f(r, "hist_baseline_avg_iti")
            baseline_size = _f(r, "hist_baseline_trade_size")
            if baseline_iti is not None:
                break
    return baseline_iti, baseline_size, mono_offset


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #
def _is_post_trigger(row) -> bool:
    return (row.get("post_trigger") or "").strip().lower() in ("true", "1")


def replay_file(path: str, mole, overrides: dict, stop_at_trigger: bool,
                log: logging.Logger, detect: bool = True,
                skip_post_trigger: bool = False):
    """Replay one recording. Returns (app, fire_info) or (None, None) if the
    file is not replayable.

    Post-trigger tail (trade-mole-2.5+, 2026-10-03): a recording may continue
    for up to POST_TRIGGER_RECORD_SEC after the live trigger, as RAW rows flagged
    post_trigger=True. They are ordinary TRADE / TICK_* rows, so by default they
    are dispatched like any other: a candidate rule that does not fire where the
    live one did can fire inside the tail, which is the point of recording it.
    skip_post_trigger=True drops them (the feature self-check, which compares
    against rows the live mole wrote WITHOUT features)."""
    rows, cols = read_rows(path)
    if skip_post_trigger:
        rows = [r for r in rows if not _is_post_trigger(r)]
    if not rows:
        log.warning("%s: empty", os.path.basename(path))
        return None, None
    missing = [c for c in REQUIRED_COLS if c not in cols]
    if any(r.get("event_type") == "TRADE" for r in rows):
        missing += [c for c in TRADE_REQUIRED_COLS if c not in cols]
    if missing:
        log.info("%s: skipped (not a full trade-path recording; missing %s)",
                 os.path.basename(path), ",".join(missing))
        return None, None

    baseline_iti, baseline_size, mono_offset = session_params(rows)
    ov = BASELINE_OVERRIDES.get(os.path.basename(path))
    if ov is not None:
        baseline_iti, baseline_size = ov["iti"], ov["size"]
    if baseline_iti is None:
        log.info("%s: skipped (no TRADE row, so no baseline to recover)",
                 os.path.basename(path))
        return None, None

    symbol = _symbol_from_name(path)
    clock = ReplayClock(mono_offset)

    # Install the replay clock over the module's handles, and any constant
    # overrides for this run. Everything is restored before returning.
    saved = {"time": mole.time, "datetime": mole.datetime}
    for k in overrides:
        if not hasattr(mole, k):
            raise SystemExit(f"--set {k}: no such constant in the mole module")
        saved[k] = getattr(mole, k)
    mole.time = _TimeShim(clock)
    mole.datetime = _make_datetime_shim(clock)
    for k, v in overrides.items():
        setattr(mole, k, v)

    try:
        app = mole.IBKRSurgeApp(
            symbol,
            baseline_avg_iti=baseline_iti,
            surge_dollar_rate=mole.DEFAULT_SURGE_DOLLAR_RATE,
            surge_max_spread_pct=mole.DEFAULT_SURGE_MAX_SPREAD_PCT,
            surge_min_lift_ratio=mole.DEFAULT_SURGE_MIN_LIFT_RATIO,
            surge_min_bid_drift_bp=mole.DEFAULT_SURGE_MIN_BID_DRIFT_BP,
            surge_min_trades_5s=mole.DEFAULT_SURGE_MIN_TRADES_5S,
            surge_max_price_drop_pct=mole.DEFAULT_SURGE_MAX_PRICE_DROP_PCT,
            baseline_avg_trade_size=baseline_size,
            large_trade_mult=mole.DEFAULT_LARGE_TRADE_MULT,
            pretrade_staleness_fire=mole.PRETRADE_STALENESS_FIRE,
            pretrade_staleness_threshold=mole.PRETRADE_STALENESS_THRESHOLD,
            logger=log,
        )
        app.stop_at_trigger = stop_at_trigger
        if not detect:
            # Feature-only replay: neutralise both fire paths so the session runs
            # to the end of the recording and emits exactly the recorded rows.
            # Needed to compare FEATURES against a file written by a different
            # rule generation — see self_check().
            app.should_fire = lambda w, iti: (False, "")
            app._pretrade_fire = False

        fire = {}

        def on_buy_signal(last_ask, *a, **kw):
            if not fire:
                fire["wall"] = clock.time()
                fire["ask"] = last_ask
                fire["n_events"] = len(app.records)

        app.buy_signal_callback = on_buy_signal

        for r in rows:
            wall = _f(r, "local_arrival_time")
            if wall is None:
                continue
            # TRADE rows carry their own perf_counter stamp; use it verbatim.
            # Live samples the wall and mono clocks a microsecond apart, so a
            # derived offset leaves ~1e-6 s of error — harmless in itself, but
            # the ITI divisions amplify it into the ratio columns.
            clock.set(wall, _f(r, "local_mono_time"))
            _dispatch(app, r, clock)

        if fire:
            fire["reason"] = _last_surge_reason(app.records)
        return app, (fire or None)
    finally:
        mole.time = saved.pop("time")
        mole.datetime = saved.pop("datetime")
        for k, v in saved.items():
            setattr(mole, k, v)


def _dispatch(app, r, clock):
    """Route one recorded row back through the handler that produced it."""
    et = r.get("event_type")
    tt = _i(r, "tick_type")

    if et == "TRADE":
        # tickString() sets _vwap from the RTVolume message before handing off,
        # and the recorded vwap column is stamped after that update.
        v = _f(r, "vwap")
        if v:
            app._vwap = v
        exch = _f(r, "exchange_time_epoch")
        single = _b(r, "rt_single_trade_flag")
        app._process_trade_event(
            now_mono=clock.perf_counter(),
            now_wall=_f(r, "local_arrival_time"),
            price=_f(r, "price"),
            size=int(_f(r, "size") or 0),
            rt_time_ms=int(round(exch * 1000)) if exch else None,
            single_trade_flag=bool(single),
            rt_total_volume=_i(r, "rt_total_volume"),
            rt_source=r.get("rt_source") or "RTVolume",
        )
    elif et == "TICK_PRICE":
        app.tickPrice(0, tt, _f(r, "price"), None)
    elif et == "TICK_SIZE":
        app.tickSize(0, tt, _f(r, "size") or 0)
    elif et == "TICK_GENERIC":
        app.tickGeneric(0, tt, _f(r, "value"))
    elif et == "TICK_STRING":
        app.tickString(0, tt, r.get("value_str") or "")
    # PRETRADE_VETO / PRETRADE_SURGE rows are emitted BY the mole, not fed to it.


def _last_surge_reason(records):
    for rec in reversed(records):
        if rec.get("surge_detected"):
            return rec.get("surge_reason") or ""
    return ""


def _symbol_from_name(path: str) -> str:
    # Any operator label prefix (_pos_, _neg_, _neu_, _late_, _neutral_,
    # _medium_, ...). Matching only the first three left the symbol EMPTY for
    # the other labels (2026-10-02). Cosmetic: the symbol is used for logging.
    base = re.sub(r"^_[^_]+_", "", os.path.basename(path))
    parts = base.replace("trade-mole-table_", "").split("_")
    return parts[0] if parts else "UNKNOWN"


# --------------------------------------------------------------------------- #
# Self-check
# --------------------------------------------------------------------------- #
# Decision columns. A recording made by an older mole generation carries that
# generation's fire decisions, so replaying it through a NEWER module legitimately
# disagrees here (2.2 moved Rule C 100->1550, Rule A's dollar gate 2->60, D
# 0.3->0.28, E 1%->1.2% and added the Rule-F veto). The fidelity question this
# check answers is whether the FEATURES are reproduced; decisions are what the
# recalibration is meant to change.
DECISION_COLS = {"surge_detected", "surge_reason"}
NON_REPRODUCIBLE = {"Time"}  # derived from local_arrival_iso; compared via it
ISO_TOLERANT_COLS = {"local_arrival_iso"}  # 1 ms sampling artifact; see self_check
# Normally set on the first accepted trade (trade-mole-2.2.py:670), but the Rule-F
# pretrade path ALSO writes them (:1218) with its floored gap. In a session whose
# only source was the pretrade path — no tick-45, so the trade path had nothing to
# work from — detect=False legitimately leaves them empty.
FIRE_PATH_COLS = {"prev_trd_time_gap", "bsln_over_prev_trd_gap"}


def self_check(path: str, mole, log: logging.Logger, detect: bool = False) -> bool:
    """Replay and compare every feature cell against the original.

    Runs with detection off by default so the replay traverses the whole
    recording regardless of which rule generation wrote it. Pass detect=True to
    compare decisions too — only meaningful against a file the given module
    version actually produced."""
    import pandas as pd

    # Post-trigger tail (2.5+): with detection OFF the replay would compute full
    # features for tail rows the live mole recorded RAW, so leave the tail out on
    # both sides. With detection ON the replayed mole fires at the same instant
    # and records the tail raw itself, so only the POST_TRIGGER_END marker — it is
    # stamped by the clerk, not produced from a tick — has to go.
    app, _ = replay_file(path, mole, {}, stop_at_trigger=True, log=log, detect=detect,
                         skip_post_trigger=not detect)
    if app is None:
        return True  # skipped, not a failure

    tmp = os.path.join(
        os.environ.get("TMPDIR", "/tmp"),
        f"replay-selfcheck-{os.path.basename(path)}",
    )
    mole.write_records_output(app, tmp, log=log)
    orig = pd.read_csv(path)
    new = pd.read_csv(tmp)
    os.unlink(tmp)

    name = os.path.basename(path)
    if "event_type" in orig.columns:
        orig = orig[orig["event_type"].astype(str) != "POST_TRIGGER_END"]
        orig = orig.reset_index(drop=True)
    if not detect:
        # PRETRADE_SURGE / PRETRADE_VETO rows are emitted BY the fire path, which
        # detect=False switches off. Drop them from the original so the remaining
        # rows line up.
        orig = orig[~orig["event_type"].astype(str).str.startswith("PRETRADE_")]
        orig = orig.reset_index(drop=True)
        if "post_trigger" in orig.columns:
            orig = orig[~orig["post_trigger"].astype(str).str.lower().isin(("true", "1"))]
            orig = orig.reset_index(drop=True)
            if "post_trigger" not in new.columns:
                orig = orig.drop(columns=["post_trigger"])
    if list(orig.columns) != list(new.columns):
        only_o = [c for c in orig.columns if c not in new.columns]
        only_n = [c for c in new.columns if c not in orig.columns]
        log.error("%s: COLUMN MISMATCH  missing=%s extra=%s", name, only_o, only_n)
        return False
    if len(orig) != len(new):
        log.error("%s: ROW COUNT %d -> %d", name, len(orig), len(new))
        return False

    skip = NON_REPRODUCIBLE if detect else (NON_REPRODUCIBLE | DECISION_COLS)
    bad = []
    for c in orig.columns:
        if c in skip:
            continue
        a, b = orig[c], new[c]
        if (not detect and c in FIRE_PATH_COLS
                and b.isna().all() and a.notna().any()):
            log.info("%s: %s only sourced from the pretrade path — not comparable "
                     "with detection off", name, c)
            continue
        if c in ISO_TOLERANT_COLS:
            # Live stamps local_arrival_time from time.time() and the iso string
            # from a SEPARATE datetime.now() read microseconds later, so the two
            # can straddle a millisecond boundary. Replay derives both from the
            # one recorded epoch, and the second sample is not in the file to
            # recover, so allow a millisecond here.
            ta = pd.to_datetime(a, errors="coerce")
            tb = pd.to_datetime(b, errors="coerce")
            n = int(((ta - tb).abs() > pd.Timedelta("1.5ms")).sum())
            if n:
                bad.append((c, n))
            continue
        if a.dtype.kind in "fi" and b.dtype.kind in "fi":
            diff = (a.fillna(-9e18) - b.fillna(-9e18)).abs()
            n = int((diff > 1e-6).sum())
        else:
            n = int((a.fillna("") .astype(str) != b.fillna("").astype(str)).sum())
        if n:
            bad.append((c, n))
    if bad:
        log.error("%s: %d columns differ: %s", name, len(bad),
                  ", ".join(f"{c}({n})" for c, n in bad[:12]))
        return False
    log.info("%s: OK (%d rows x %d cols reproduced)", name, len(new), len(new.columns))
    return True


# --------------------------------------------------------------------------- #
def parse_set(pairs):
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"--set expects NAME=VALUE, got {p!r}")
        k, v = p.split("=", 1)
        v = v.strip()
        if v in ("True", "False"):
            out[k.strip()] = (v == "True")
        else:
            try:
                out[k.strip()] = float(v) if "." in v else int(v)
            except ValueError:
                out[k.strip()] = v
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", help="recording CSVs (globs allowed)")
    ap.add_argument("--module", default=DEFAULT_MODULE,
                    help=f"mole module to replay through (default {DEFAULT_MODULE})")
    ap.add_argument("--out-dir", help="write recomputed CSVs here")
    ap.add_argument("--set", action="append", dest="sets", metavar="NAME=VALUE",
                    help="override a module-level constant for this run")
    ap.add_argument("--self-check", action="store_true",
                    help="replay unchanged and diff features against the original")
    ap.add_argument("--check-decisions", action="store_true",
                    help="self-check also compares surge_detected/surge_reason; only "
                         "meaningful against files the given --module actually wrote")
    ap.add_argument("--no-detect", action="store_true",
                    help="neutralise both fire paths so the whole recording is "
                         "traversed (features only)")
    ap.add_argument("--no-stop-at-trigger", action="store_true",
                    help="keep recording past the fire (recordings end there anyway)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )
    log = logging.getLogger("replay")
    if not args.verbose:
        log.setLevel(logging.INFO)

    paths = []
    for pat in args.inputs:
        hits = sorted(glob.glob(pat))
        paths.extend(hits or ([pat] if os.path.exists(pat) else []))
    if not paths:
        raise SystemExit("no input files matched")

    mole = load_mole(args.module)
    overrides = parse_set(args.sets)

    if args.self_check:
        ok = sum(1 for p in paths if self_check(p, mole, log, detect=args.check_decisions))
        scope = "features+decisions" if args.check_decisions else "features"
        print(f"\nself-check ({scope}): {ok}/{len(paths)} reproduced")
        return 0 if ok == len(paths) else 1

    if not args.out_dir:
        raise SystemExit("--out-dir is required unless --self-check")
    os.makedirs(args.out_dir, exist_ok=True)

    written = skipped = 0
    for p in paths:
        app, fire = replay_file(p, mole, overrides,
                                stop_at_trigger=not args.no_stop_at_trigger, log=log,
                                detect=not args.no_detect)
        if app is None:
            skipped += 1
            continue
        out = os.path.join(args.out_dir, os.path.basename(p))
        mole.write_records_output(app, out, log=log)
        written += 1
        if fire:
            when = datetime.fromtimestamp(fire["wall"]).isoformat(timespec="milliseconds")
            log.info("  fire @ %s after %d events — %s",
                     when, fire["n_events"], fire["reason"])
        else:
            log.info("  no fire")
    print(f"\nreplayed {written} file(s), skipped {skipped} -> {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
