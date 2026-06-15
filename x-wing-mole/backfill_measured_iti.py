#!/usr/bin/env python3
"""Backfill the 9 measured-ITI columns onto already-recorded mole-output files.

`trade-mole-2.1.py` emits these columns live (right after `vwap`):

    prev_trd_time_gap, bsln_over_prev_trd_gap, measured_ITIsec,
    ratio_baseline_over_measured_ITI,
    measured_ITIsec_collapse_ratio, measured_ITIsec_collapse_diff,
    measured_ITIsec_collapse_velocity, ratio_baseline_collapse_ratio,
    ratio_baseline_collapse_diff, ratio_baseline_collapse_velocity

This script reconstructs them *faithfully* from data already present in the
recorded CSVs — it is not an approximation. Every input the live
`IBKRSurgeApp._process_trade_event` uses is recorded per-row:

    local_mono_time      -> live `now_mono`  (same time.monotonic() clock)
    exchange_time_epoch  -> live `exch`      (= rt_time_ms / 1000)
    rt_source            -> RTVolume/RTTradeVolume duplicate detection
    value_str (tick 45)  -> last_timestamp_at_subscribe (first tick-45 row)
    hist_baseline_avg_iti

The per-trade math below mirrors `trade-mole-2.1.py:460-505` and the tick-45
capture at `:1040-1042` verbatim.

Output: non-destructive copies named `<stem>_augmented.csv` beside each original.
Originals are never modified.

Usage:
    /home/tom/venv/bin/python backfill_measured_iti.py            # all in-scope files
    /home/tom/venv/bin/python backfill_measured_iti.py FILE...    # specific files
"""

import csv
import glob
import os
import re
import sys

# Mirror of trade-mole-2.1.py:150 — a trade arriving < 5ms after the prior
# accepted trade from the OTHER RT channel is the RTVolume/RTTradeVolume twin.
DEDUP_EPSILON_SEC = 0.005

# Scope: _positive_/_negative_ labeled files dated on/after this day.
MIN_DATE = "2026-06-08"
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(HERE, "mole-outputs")

# The 9 new columns, in table order, inserted immediately after `vwap`.
NEW_COLS = [
    "prev_trd_time_gap",
    "bsln_over_prev_trd_gap",
    "measured_ITIsec",
    "ratio_baseline_over_measured_ITI",
    "measured_ITIsec_collapse_ratio",
    "measured_ITIsec_collapse_diff",
    "measured_ITIsec_collapse_velocity",
    "ratio_baseline_collapse_ratio",
    "ratio_baseline_collapse_diff",
    "ratio_baseline_collapse_velocity",
]
# The first two are single constants per file (back-filled on every row); the rest
# are written per-row in the trade loop.
PER_ROW_COLS = NEW_COLS[2:]


def _f(row, key):
    """Parse a CSV cell as float, or None if blank/unparseable."""
    v = row.get(key, "")
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _tick_type(row):
    v = row.get("tick_type", "")
    if not v:
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _fmt(x):
    """Render a computed float for output; blank for None."""
    if x is None:
        return ""
    return repr(x)


def backfill_file(path):
    """Read one mole-output file, compute the 9 columns, write <stem>_augmented.csv.

    Returns a one-line summary string.
    """
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    if "vwap" not in fieldnames:
        return f"SKIP  {os.path.basename(path)}  (no 'vwap' column)"

    # Live-app state (see trade-mole-2.1.py:359-374).
    last_timestamp_at_subscribe = None
    first_iti = None              # exchange-clock pre-first-trade gap (seeds measured ITI)
    prev_trd_time_gap = None      # recording-start − LAST_TIMESTAMP (the emitted column)
    last_accepted_trade_mono = None
    last_accepted_rt_source = None
    prev_measured_iti = None
    prev_ratio_baseline = None

    # Recording-start wall-clock = local_arrival_time of the first row (subscribe moment).
    subscribe_wall = _f(rows[0], "local_arrival_time") if rows else None
    # Historical baseline ITI (constant per file) for bsln_over_prev_trd_gap.
    baseline_iti = next(
        (v for v in (_f(r, "hist_baseline_avg_iti") for r in rows) if v is not None),
        None,
    )

    n_trades = 0
    missing_anchor = False

    for row in rows:
        # --- tick-45 LAST_TIMESTAMP capture (trade-mole-2.1.py:1040-1042) ---
        if _tick_type(row) == 45 and last_timestamp_at_subscribe is None:
            vs = row.get("value_str", "")
            if vs:
                try:
                    last_timestamp_at_subscribe = float(int(vs))
                except (TypeError, ValueError):
                    pass

        if row.get("event_type") != "TRADE":
            continue
        n_trades += 1

        now_mono = _f(row, "local_mono_time")
        rt_source = row.get("rt_source", "")
        hist_baseline_avg_iti = _f(row, "hist_baseline_avg_iti")

        # --- measured-ITI block (trade-mole-2.1.py:460-505), verbatim logic ---
        measured_iti = None
        ratio_base_over_iti = None
        m_collapse_ratio = m_collapse_diff = m_collapse_vel = None
        r_collapse_ratio = r_collapse_diff = r_collapse_vel = None

        is_duplicate = (
            last_accepted_trade_mono is not None
            and now_mono is not None
            and rt_source != last_accepted_rt_source
            and (now_mono - last_accepted_trade_mono) < DEDUP_EPSILON_SEC
        )

        if is_duplicate:
            # Same fill on the other RT channel: forward-fill, do not advance state.
            measured_iti = prev_measured_iti
            ratio_base_over_iti = prev_ratio_baseline
        else:
            if last_accepted_trade_mono is None:
                # First accepted trade: seed measured ITI from the exchange-epoch
                # pre-first gap (exchange-clock, the cleaner "true first interval").
                exch = _f(row, "exchange_time_epoch")  # live exch = rt_time_ms/1000
                if exch is not None and last_timestamp_at_subscribe is not None:
                    first_iti = exch - last_timestamp_at_subscribe
                else:
                    missing_anchor = True
                measured_iti = first_iti
                # prev_trd_time_gap: staleness at recording-start (subscribe moment).
                if (
                    prev_trd_time_gap is None
                    and subscribe_wall is not None
                    and last_timestamp_at_subscribe is not None
                ):
                    prev_trd_time_gap = subscribe_wall - last_timestamp_at_subscribe
            elif now_mono is not None:
                measured_iti = now_mono - last_accepted_trade_mono

            if (
                measured_iti is not None
                and measured_iti > 0
                and hist_baseline_avg_iti is not None
            ):
                ratio_base_over_iti = hist_baseline_avg_iti / measured_iti

            pm = prev_measured_iti
            if measured_iti is not None and pm is not None:
                m_collapse_diff = measured_iti - pm
                if pm > 0:
                    m_collapse_ratio = measured_iti / pm
                if measured_iti > 0:
                    m_collapse_vel = (measured_iti - pm) / measured_iti
            pr = prev_ratio_baseline
            if ratio_base_over_iti is not None and pr is not None:
                r_collapse_diff = ratio_base_over_iti - pr
                if pr != 0:
                    r_collapse_ratio = ratio_base_over_iti / pr
                if measured_iti is not None and measured_iti > 0:
                    r_collapse_vel = (ratio_base_over_iti - pr) / measured_iti

            last_accepted_trade_mono = now_mono
            last_accepted_rt_source = rt_source
            prev_measured_iti = measured_iti
            prev_ratio_baseline = ratio_base_over_iti

        row["measured_ITIsec"] = _fmt(measured_iti)
        row["ratio_baseline_over_measured_ITI"] = _fmt(ratio_base_over_iti)
        row["measured_ITIsec_collapse_ratio"] = _fmt(m_collapse_ratio)
        row["measured_ITIsec_collapse_diff"] = _fmt(m_collapse_diff)
        row["measured_ITIsec_collapse_velocity"] = _fmt(m_collapse_vel)
        row["ratio_baseline_collapse_ratio"] = _fmt(r_collapse_ratio)
        row["ratio_baseline_collapse_diff"] = _fmt(r_collapse_diff)
        row["ratio_baseline_collapse_velocity"] = _fmt(r_collapse_vel)

    # Back-fill the two constants on every row (matches write_records_output:1375-1384).
    pttg = _fmt(prev_trd_time_gap)
    bopg = None
    if prev_trd_time_gap and prev_trd_time_gap > 0 and baseline_iti is not None:
        bopg = baseline_iti / prev_trd_time_gap
    bopg = _fmt(bopg)
    for row in rows:
        row["prev_trd_time_gap"] = pttg
        row["bsln_over_prev_trd_gap"] = bopg
        for c in PER_ROW_COLS:
            row.setdefault(c, "")

    # Build output header: insert the 9 names right after `vwap` (name-based, so
    # it works for both the wide June-12 and narrow June-8 schema variants).
    out_fields = []
    for name in fieldnames:
        out_fields.append(name)
        if name == "vwap":
            out_fields.extend(NEW_COLS)

    stem, _ext = os.path.splitext(path)
    out_path = stem + "_augmented.csv"
    with open(out_path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    note = ""
    if missing_anchor:
        note = "  [no tick-45/exchange anchor -> first measured ITI blank]"
    return (
        f"OK    {os.path.basename(out_path)}  "
        f"rows={len(rows)} trades={n_trades} prev_trd_time_gap={pttg or 'None'}{note}"
    )


def discover_files():
    """All _positive_/_negative_ labeled files in mole-outputs/ dated >= MIN_DATE."""
    found = []
    for pat in ("_positive_*", "_negative_*", "_positive-*", "_negative-*"):
        found.extend(glob.glob(os.path.join(OUT_DIR, pat)))
    out = []
    for p in sorted(set(found)):
        base = os.path.basename(p)
        if base.endswith("_augmented.csv"):
            continue
        m = DATE_RE.search(base)
        if not m or m.group(1) < MIN_DATE:
            continue
        out.append(p)
    return out


def main(argv):
    targets = argv[1:] if len(argv) > 1 else discover_files()
    if not targets:
        print("No in-scope files found.")
        return 0
    print(f"Processing {len(targets)} file(s):")
    for path in targets:
        try:
            print("  " + backfill_file(path))
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"  ERROR {os.path.basename(path)}: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
