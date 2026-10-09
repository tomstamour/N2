#!/usr/bin/env python3
"""Per-session context for trade-mole recordings (2026-10-02, trade-mole-2.5 pass).

A mole recording (``[_label_]trade-mole-table_<SYM>_<YYYY-MM-DD>_<HH-MM-SS>.csv``)
holds the tape only. The trigger revision also needs what was known about the
session when the clerk armed it, and an independent "when did the surge really
start" reference for the operator's ``_late_`` labels. This module derives both,
read-only on every input:

  * news row      — ``orchestrator5/tables/news_output_<date>.tsv``: the PR that
                    armed the session (publish/arrival time, class, scores, gate
                    result, float, market cap).
  * stock facts   — ``universe_finder/data/stocks_universe_<D>.tsv`` with the
                    latest D strictly BEFORE the arm date, i.e. a file that existed
                    when the session was armed (point-in-time; the orchestrator
                    loads the newest file on disk).
  * end reason    — fired / news BAD (clerk released the session early) /
                    expired (watch window ran out) / short.
  * surge start   — "onset": the first moment the TRUE volume clearly rises above
                    the name's normal level. See ``onset()``.

Why TRUE volume. RTVolume sizes are in LOTS and the mole drops every print under
100 shares (``trade-mole-2.4.py`` tickString note), so the prints it records are a
subset of the tape. IBKR's running day volume — the ``totalVolume`` field carried
by every RTVolume (tick 48) message, equal to tick 8 — counts everything,
including the dropped prints and the volume aggregated into one message. On LRHC
2026-10-01 the first recorded print is 14 lots while the day volume moved 94 lots
since the snapshot, and IBKR's trade count (tick 54) went 1 -> 40 in 5 s against 2
recorded prints. That hidden activity is what the operator sees on a 1-second
TradingView chart.

Units. ``hist_baseline_trade_size`` is in SHARES (universe_finder/
tradeSizeExplained.txt: lot_multiplier = 1, verified against AAPL), while every
live size and volume is in LOTS of 100. ``normal_lots_per_sec()`` converts.
"""
import csv
import datetime as _dt
import glob
import os
import re
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, "..", ".."))
NEWS_DIR = os.path.join(SCRIPTS, "orchestrator5", "tables")
UNIVERSE_DIR = os.path.join(SCRIPTS, "universe_finder", "data")

csv.field_size_limit(10 ** 9)   # news Body cells are long

NAME_RE = re.compile(
    r"trade-mole-table_([A-Z0-9.\-]+)_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})")
SENTINEL = 44444.0

# Corrected baselines (2026-10-08), {recording basename: override row}. A row has
# `iti` (seconds) and `size` (shares) and may carry the four universe columns
# RTH_avgITI_sec / ETH_avgITI_sec / RTH_tradeSize / ETH_TradeSize. Filled by
# eval_rules.py --baselines (it aliases this to replay_mole.BASELINE_OVERRIDES).
# Empty = use what the recording and the newest file on disk say.
BASELINE_OVERRIDES = {}

# Statuses that mean the clerk never ran a session for the row.
_NOT_ARMED = {"excluded_string", "arm_rejected", "not_armed", "no_reply"}


def parse_name(path):
    """(symbol, 'YYYY-MM-DD', seconds-of-day of the arm) or None."""
    m = NAME_RE.search(os.path.basename(path))
    if not m:
        return None
    sym, d, hh, mm, ss = m.groups()
    return sym, d, int(hh) * 3600 + int(mm) * 60 + int(ss)


def _f(v):
    if v is None:
        return None
    v = str(v).strip()
    if v == "" or v.lower() in ("nan", "none"):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def _secs(hms):
    """'08:30:00.517' -> seconds of day (float), or None."""
    if not hms:
        return None
    try:
        h, m, s = hms.strip().split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None


# --------------------------------------------------------------------------- #
# News rows
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=None)
def _news_index():
    """{(symbol, date): [row, ...]} over every news_output_*.tsv (incl. _rotated)."""
    idx = {}
    for p in sorted(glob.glob(os.path.join(NEWS_DIR, "news_output_*.tsv"))):
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh, delimiter="\t"):
                sym = (r.get("Symbol") or "").strip().upper()
                d = (r.get("ArrivalDate") or "").strip()
                if sym and d:
                    r["_arrival_s"] = _secs(r.get("ArrivalTime"))
                    r["_source"] = os.path.basename(p)
                    idx.setdefault((sym, d), []).append(r)
    return idx


def news_row(path, tolerance_s=5.0):
    """The PR row that armed this recording, or None.

    The recording's filename stamp is the arm instant truncated to the second
    (clerk-1.2 builds the path when the session is created), and the clerk arms
    on arrival, so the matching row's ArrivalTime sits within about a second of
    it. A re-published PR can produce two rows a few hundred ms apart; prefer the
    one the clerk acted on (not an excluded/rejected status), then the closest."""
    p = parse_name(path)
    if p is None:
        return None
    sym, d, t = p
    best, best_key = None, None
    for r in _news_index().get((sym, d), []):
        a = r.get("_arrival_s")
        if a is None:
            continue
        dt = abs(a - t)
        if dt > tolerance_s:
            continue
        status = (r.get("ClerkSentiment") or "").split(":")[-1]
        key = (status in _NOT_ARMED, dt)
        if best_key is None or key < best_key:
            best, best_key = r, key
    return best


def gate_status(row):
    """('XgbSolo', 'sentiment_ack') from 'XgbSolo:sentiment_ack'; ('', status)
    when there is no leg; (None, None) without a row."""
    if row is None:
        return None, None
    cs = (row.get("ClerkSentiment") or "").strip()
    if ":" in cs:
        leg, status = cs.rsplit(":", 1)
        return leg, status
    return "", cs


NEWS_NUMERIC = (
    # FinBERT family. July tables carry it as positive/negative/neutral, later
    # ones as Prosus_*_hd (same ProsusAI checkpoint); news_features() merges them.
    "positive", "negative", "neutral", "sentiment_score",
    "Prosus_pos_hd", "Prosus_neg_hd", "Prosus_neu_hd", "Prosus_ss_hd",
    "FinMarBa_pos_hd", "FinMarBa_neg_hd", "FLANG_pos_hd", "FLANG_neg_hd",
    "kw15_proba_hd", "kw15_proba_bd", "EarningScore",
    "ge50_m3", "ge35_m3", "ge20_m3", "se_m3",
    "FM3_hd_ge50", "FM3_bd_ge50",
    "Float", "MktCap", "LastDailyClose", "itiBaseline", "tradeSizeBaseline",
)


def news_features(row):
    """Flat dict of point-in-time news fields (numbers as float, else None)."""
    if row is None:
        return {}
    out = {k: _f(row.get(k)) for k in NEWS_NUMERIC}
    out["finbert_pos"] = out["Prosus_pos_hd"] if out["Prosus_pos_hd"] is not None else out["positive"]
    out["finbert_neg"] = out["Prosus_neg_hd"] if out["Prosus_neg_hd"] is not None else out["negative"]
    created, arrival = _secs(row.get("Created")), row.get("_arrival_s")
    out["publish_to_arrival_s"] = (arrival - created) if (created is not None and arrival is not None
                                                          and 0 <= arrival - created < 3600) else None
    out["arrival_s_of_day"] = arrival
    out["fine_class"] = (row.get("FineClass") or "").strip() or None
    out["broad_class"] = (row.get("BroadClass") or "").strip() or None
    out["headline"] = (row.get("Headline") or "").strip()
    leg, status = gate_status(row)
    out["gate_leg"], out["gate_status"] = leg, status
    return out


# --------------------------------------------------------------------------- #
# Stock facts (daily universe)
# --------------------------------------------------------------------------- #
UNIV_RE = re.compile(r"^stocks_universe_(\d{4}-\d{2}-\d{2})\.tsv$")


@lru_cache(maxsize=None)
def _universe_dates():
    out = []
    for p in os.listdir(UNIVERSE_DIR):
        m = UNIV_RE.match(p)
        if m:
            out.append(m.group(1))
    return sorted(out)


@lru_cache(maxsize=None)
def _universe(date):
    p = os.path.join(UNIVERSE_DIR, f"stocks_universe_{date}.tsv")
    with open(p, newline="") as fh:
        return {(r.get("Symbol") or "").strip().upper(): r
                for r in csv.DictReader(fh, delimiter="\t")}


def universe_date_for(arm_date):
    """Latest universe file dated strictly before the arm date — one that was on
    disk when the session was armed, whatever the orchestrator had loaded."""
    prior = [d for d in _universe_dates() if d < arm_date]
    return prior[-1] if prior else None


UNIVERSE_NUMERIC = (
    "Float_M", "MarketCap_M", "LastDailyClosePrice",
    "RTH_avgITI_sec", "ETH_avgITI_sec", "RTH_tradeSize", "ETH_TradeSize",
    "SI_PctFloat", "SI_DTC", "SVR_Pct", "SqueezeScore",
    "ret_1d_pct", "ret_5d_pct", "ret_21d_pct", "rvol_5d", "rvol_20d", "vol_z_20d",
    "dvol_log10", "float_turnover_1d", "float_turnover_5d_avg", "atr_pct_14",
    "realized_vol_5d", "range_pct_1d", "gap_pct_1d", "gap_up_freq_20d", "max_gap_20d",
    "rsi_14", "sma20_dist_pct", "sma50_dist_pct", "dist_52w_high_pct",
    "dist_52w_low_pct", "drawdown_from_peak_pct", "amihud_21d",
    "zero_vol_day_ratio_20d", "price_log10",
)


def stock_facts(path):
    """Point-in-time stock facts for the recording's symbol, or {} if absent."""
    p = parse_name(path)
    if p is None:
        return {}
    sym, d, _ = p
    ud = universe_date_for(d)
    if ud is None:
        return {}
    r = _universe(ud).get(sym)
    if r is None:
        return {"universe_date": ud}
    out = {k: _f(r.get(k)) for k in UNIVERSE_NUMERIC}
    for k in ("RTH_avgITI_sec", "ETH_avgITI_sec", "RTH_tradeSize", "ETH_TradeSize"):
        if out.get(k) == SENTINEL:
            out[k] = None
    out["RegSHO_Flag"] = (r.get("RegSHO_Flag") or "").strip() or None
    out["universe_date"] = ud
    ov = BASELINE_OVERRIDES.get(os.path.basename(path))
    if ov:
        for k in ("RTH_avgITI_sec", "ETH_avgITI_sec", "RTH_tradeSize", "ETH_TradeSize"):
            v = _f(ov.get(k))
            if v is not None:
                out[k] = None if v == SENTINEL else v
        out["universe_date"] = ov.get("served_date") or ud
    return out


# --------------------------------------------------------------------------- #
# Recording-derived context: end reason, true-volume series, onset
# --------------------------------------------------------------------------- #
def read_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def end_reason(rows, news=None):
    """Why the recording stopped.

    fired     — it carries the live trigger row (recordings stop there);
    news_bad  — the clerk released it on a BAD sentiment before any trigger;
    expired   — the watch window ran out (no trigger, no BAD);
    empty     — nothing recorded."""
    if not rows:
        return "empty"
    if any((r.get("surge_detected") or "").strip().lower() == "true" for r in rows):
        return "fired"
    _, status = gate_status(news)
    if status == "sentiment_bad_released":
        return "news_bad"
    return "expired"


def true_volume_series(rows):
    """[(t_rel, day_volume_lots)] from IBKR's running day volume: the snapshot /
    update tick 8 and the totalVolume of every tick-48 RTVolume message,
    including the zero-size volume-only messages the mole records as raw
    TICK_STRING rows. Tick 77 (RTTradeVolume) carries a different total
    (unreportable trades excluded) and is skipped. Monotone by construction —
    an out-of-order smaller value is ignored."""
    if not rows:
        return []
    t0 = _f(rows[0].get("local_arrival_time"))
    out, last = [], None
    for r in rows:
        t = _f(r.get("local_arrival_time"))
        if t is None:
            continue
        et, tt = r.get("event_type"), _f(r.get("tick_type"))
        v = None
        if et == "TICK_SIZE" and tt == 8:
            v = _f(r.get("size"))
        elif et == "TRADE" and (r.get("rt_source") or "") == "RTVolume":
            v = _f(r.get("rt_total_volume"))
        elif et == "TICK_STRING" and tt == 48:
            parts = (r.get("value_str") or "").split(";")
            if len(parts) >= 4:
                v = _f(parts[3])
        if v is None or (last is not None and v < last):
            continue
        out.append((t - t0, v))
        last = v
    return out


def normal_lots_per_sec(iti, size_shares):
    """The name's normal volume rate in LOTS per second (baseline size is shares)."""
    if not iti or iti <= 0 or iti == SENTINEL or not size_shares or size_shares <= 0 \
            or size_shares == SENTINEL:
        return None
    return size_shares / iti / 100.0


def session_baselines(rows):
    """(hist ITI, hist trade size in shares) echoed into the recording."""
    for r in rows:
        if r.get("event_type") in ("TRADE", "PRETRADE_SURGE", "PRETRADE_VETO"):
            iti = _f(r.get("hist_baseline_avg_iti"))
            if iti is not None:
                return iti, _f(r.get("hist_baseline_trade_size"))
    return None, None


# Onset defaults. Offline reference only — never a live rule. A window of 2 s
# matches the resolution the operator reads (1-second bars) with one bar of slack.
ONSET_WINDOW_S = 2.0
ONSET_X_NORMAL = 20.0     # true volume >= 20x the name's normal rate over the window
ONSET_MIN_LOTS = 10.0     # ... and at least 1,000 shares, so one odd lot never counts


def onset(rows, window_s=ONSET_WINDOW_S, x_normal=ONSET_X_NORMAL, min_lots=ONSET_MIN_LOTS,
          baselines=None):
    """Surge start: the first moment the true volume over the last `window_s`
    seconds reaches max(min_lots, x_normal x normal volume for that window).

    Returns dict(t, lots, need, px) with t relative to the recording's first
    row, px = the last recorded print at or before t (or the first print after
    it when the onset precedes every print), or None when the threshold is never
    reached inside the recording. For a name with no usable baseline only the
    absolute floor applies. `baselines` = (iti, size_shares) replaces the values
    echoed into the recording (the 2026-10-01/02 stale-list correction)."""
    series = true_volume_series(rows)
    if not series:
        return None
    iti, size = baselines if baselines else session_baselines(rows)
    rate = normal_lots_per_sec(iti, size)
    need = max(min_lots, x_normal * rate * window_s) if rate else min_lots
    j = 0
    hit = None
    for i, (t, v) in enumerate(series):
        while j < i and series[j + 1][0] <= t - window_s:
            j += 1
        # volume at (t - window): last value at or before that instant; before the
        # first observation the first observation itself (the subscribe snapshot).
        base = series[j][1] if series[j][0] <= t - window_s else series[0][1]
        if v - base >= need:
            hit = (t, v - base)
            break
    if hit is None:
        return None
    t_on, lots = hit
    t0 = _f(rows[0].get("local_arrival_time"))
    px_before, px_after = None, None
    for r in rows:
        if r.get("event_type") != "TRADE":
            continue
        t = _f(r.get("local_arrival_time")) - t0
        p = _f(r.get("price"))
        if t <= t_on + 1e-9:
            px_before = p
        elif px_after is None:
            px_after = p
            break
    return {"t": t_on, "lots": lots, "need": need,
            "px": px_before if px_before is not None else px_after}


def context(path):
    """Everything above for one recording, as one flat dict."""
    rows = read_rows(path)
    nr = news_row(path)
    out = {"file": os.path.basename(path)}
    out["end_reason"] = end_reason(rows, nr)
    t0 = _f(rows[0].get("local_arrival_time")) if rows else None
    t1 = _f(rows[-1].get("local_arrival_time")) if rows else None
    out["duration_s"] = (t1 - t0) if (t0 is not None and t1 is not None) else None
    out["news_matched"] = nr is not None
    out.update({f"news_{k}": v for k, v in news_features(nr).items()})
    sf = stock_facts(path)
    out["stock_matched"] = bool(sf) and "Float_M" in sf
    out.update({f"stock_{k}": v for k, v in sf.items()})
    ov = BASELINE_OVERRIDES.get(os.path.basename(path))
    out["baseline_overridden"] = bool(ov)
    on = onset(rows, baselines=(ov["iti"], ov["size"]) if ov else None)
    out["onset_t"] = on["t"] if on else None
    out["onset_px"] = on["px"] if on else None
    out["onset_lots"] = on["lots"] if on else None
    out["onset_need_lots"] = on["need"] if on else None
    return out


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        c = context(p)
        print(c["file"], {k: c[k] for k in ("end_reason", "duration_s", "news_matched",
                                            "news_gate_leg", "news_gate_status",
                                            "news_fine_class", "stock_matched",
                                            "stock_universe_date", "onset_t", "onset_px",
                                            "onset_lots", "onset_need_lots")})
