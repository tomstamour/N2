#!/usr/bin/env python3
"""
RetroUniverse-3.py
------------------
Point-in-time replay of daily_universe_pipeline-3.py for PAST PRs.

The nightly cron (20:30 ET on calendar day D, --days-prior 0) writes
data/stocks_universe_<D>.tsv, and the orchestrator serves a PR that arrives on
calendar day A with the file named A-1. Given any TSV of PRs (`Symbol` +
`ArrivalDate`, yyyy-mm-dd), this script appends to EVERY row the 150 universe
columns -- pipeline-3's 147 minus the `Symbol` join key, plus Step 11's four
LastSplit_* -- exactly as stocks_universe_<A-1>.tsv would have held them. It
replaces the ~8-script manual chain of
price_stats_loader/training-set-construction-steps.md with one resumable run,
for building training sets, backtesting the live chain, and auditing the
nightly files (--compare-live).

THE ANCHOR CONTRACT -- universe orientation
-------------------------------------------
Every session-resolving step of the training chain ran at --days-prior 1
against ArrivalDate; pipeline-3 runs on day D at --days-prior 0. The two name
the same session on EVERY calendar day, weekends and holidays included:

    Sessions.resolve(A, 1) == Sessions.resolve(A - 1 calendar day, 0)

(market_state_calculator.Sessions.resolve). So the addons are not run
"PR-style" at --days-prior 1. A work table W gets one row per unique
(Symbol, ArrivalDate) plus `Date` = A - 1 -- literally the concatenation of the
nightly runs that served those PRs -- and every addon is replayed against
`Date` at --days-prior 0, the way pipeline-3 runs it on one file. That is the
orientation the trade-frequency backfill's split probe and the split finder's
20:00 clock assume. --days-prior 1 is therefore fixed, not a flag.

  ⚠ Every addon auto-detects its date column with ArrivalDate FIRST. W carries
  both, so `Date` is passed explicitly everywhere; auto-detection would anchor
  every row one session late. The one exception is marketcap_backfill_AddON,
  whose universe join is defined on ArrivalDate - 1 and runs at its own
  --days-prior 1.

LIVE-FILE SEMANTICS (operator decision 2026-09-28)
--------------------------------------------------
Where the live file and the old 08_... training corpus disagree, the live file
wins -- train/serve parity:
  * the 7 borrow columns are blank (pipeline-3 dropped borrow; the SI backfill
    runs --skip-borrow-history), so SqueezeScore carries no CTB points;
  * LastSplit_* are measured to Date 20:00 ET with --never-split-fill blank
    (pipeline-3 Step 11), not to the PR instant with the maxima sentinel;
  * LastDailyClosePrice is the RTH close of the anchor session, as traded;
  * msc_basis_ratio is refereed like live: the nightly snapshot of the anchor
    session (renamed to its TRUE session, see _build_price_refs), else this
    run's own close -> ratio 1.0, which is what live always reads.

Steps (each is persisted in W, so re-running the same command resumes):
  S1 Float_M / Float_Source / Exchange from the snapshot that served the PR
     (newest canonical stocks_universe_* / nasdaq_symbols_data_priced_* on or
     before Date); blank when the symbol is not in it -- the training set's
     own Float, and operator decision: no rebuild from today's yfinance.
  S2 LastDailyClosePrice: IBKR RTH daily close of the anchor session.  [IBKR]
  S3 MarketCap_M: marketcap_backfill_AddON.py UNCHANGED (training step 16).
  S4 the 24 short-interest columns: ShortInterrest_backfill_AddON.py.
  S5 the 20 VIX / Fear & Greed columns: vix_addOn.py.
  S6 the 4 LastSplit_* columns: Stock-Split-finder.py.
  S7 the 84 market-state columns: market_state_calculator.py, in chunks of
     symbols.                                                          [IBKR]
  S8 raw RTH/ETH ITI + trade size: trade_frequency_and_size_backfill_AddON.py,
     in chunks of units, then a paced retry like pipeline-3 Step 3.5. [IBKR]
  S9 ITI / trade-size imputation POOLED over all rows (operator decision).
  then the in-universe flags, the join back onto the input, --compare-live.

S3-S8 are subprocess replays of each addon's own CLI through pipeline-3's
_run_addon: each writes a <stem>_<suffix>.tsv sibling that is verified
column-for-column against its input before it is os.replace'd in, and a marker
column skips a step (or chunk) that already ran.

OUTPUT
------
<input_dir>/<input_stem>_RetroU3.tsv: every input row, in input order, then the
150 universe columns in pipeline-3's order, then RU3_InUniverse /
RU3_FilterReason / RU3_InLiveFile / RU3_SnapshotDate / RU3_Close_Session /
RU3_Close_SplitAdj and marketcap_backfill's MarketCap_Source / _AsOf /
_LagDays. An input column whose name collides with an appended one is kept,
renamed <name>_input (operator decision). Rows without a usable Symbol /
ArrivalDate, or whose nightly has not run yet, get blank universe columns and
the reason in RU3_FilterReason. The work table and per-addon logs live in
runs/retro/<input_stem>/.

SAFETY
------
  * Nothing is ever written into universe_finder/data/: RTPR_connector3 loads
    the newest-CREATED stocks_universe_YYYY-MM-DD.tsv there as the next day's
    live subscription list, and marketcap_backfill / audit_lastdailyclose glob
    stocks_universe_*.tsv in it.
  * IBKR work stays on the paper Gateway (4002) and yields to the nightly: no
    IBKR step or chunk starts while daily_universe_pipeline-3.py is running or
    between 20:05 and 20:40 ET -- contention there costs the next day's LIVE
    universe (2026-07-06: 691/819 fetches lost). Chunks are ~15 min, so a run
    never overlaps the cron's IBKR phases. The nightly's run lock
    (runs/pipeline_daily-3.lock) is deliberately never touched: even a
    momentary flock could make a cron starting in that instant exit.
  * Splits. A past anchor needs every split AFTER it, so S2 first refreshes the
    shared cache tools/market_state/tables/splits/ ADDITIVELY: re-query
    yfinance without letting it write, then merge by ex-date -- add, never
    delete. A plain refresh would let yfinance's empty answer for a DELISTED
    symbol overwrite real history with "never split" (NTZ, OMH on 2026-09-07;
    gitignored, no restore). Every reader after that runs at max-age 36500.
    Symbols with a split after their anchor get fresh MSC bars (a private
    --cache-dir, never MSC's --no-cache, which would also bypass this split
    cache): the shared bar cache can hold a series pulled on the ex-date that
    MSC's arithmetic would misread (CDT/TRUG 2026-09-29).

KNOWN, ACCEPTED DIVERGENCES from the real nightly file
------------------------------------------------------
  * Fetch time: late or corrected prints move raw ITI/size a little, IBKR can
    correct a bar, and msc_asof / msc_fetch_secs / msc_bars_fetched_at
    describe this run.
  * FNG_Index: the nightly reads CNN's provisional same-day score, the history
    replayed here holds the settled one -- it differs on essentially every row
    (09-17: 28.69 live vs 28.29 settled). Worse, CNN revises its history: the
    whole FNG_ block for 07-15..08-27 differs between the training corpus's
    09-05 fetch and a 10-03 one (08-03: 50.74 then, 46.14 now), so F&G is not
    reproducible point-in-time. VIX is (CBOE history is stable). Neither FNG
    column is an input of model-2 or model-3.
  * A symbol IBKR no longer serves (delisted, renamed) has no bars today: its
    market-state and ITI/size columns stay blank / 44444 (msc_status
    no_contract); its close falls back to the nightly's own snapshot when one
    holds the anchor session (RU3_Close_SplitAdj=snapshot).
  * Where a nightly file is itself a session behind (09-23: CBOE had not
    published, VIX_AnchorDate 09-22), this script has the correct anchor --
    --compare-live shows those rows as VIX/FNG mismatches.
  * The imputed canonical ITI/size come from one model pooled over the input,
    not from that night's cross-section; the *_raw columns are exact.
  * marketcap_backfill's formula pass (symbol absent from its D-1 file) uses
    June-median shares and the split window (ArrivalDate, 06-09], which is
    empty after 06-09. That window is anchored on ArrivalDate -- tuned to
    lastDailyClose_AddOn's close -- so a split going ex between the anchor and
    ArrivalDate is not divided out of this script's anchor-basis close.
  * Float_M is blank whenever the symbol is not in the served-by snapshot
    (always before 2026-05-14), so those rows fail RU3_InUniverse.
  * The SI backfill queries only FINRA's consolidated dataset; live also used
    the OTC one plus a yfinance fallback.
  * A PR between 00:00 and 03:00 ET was really served the A-2 file (the
    orchestrator reloads at 03:00); A-1 is used, as in the training corpus.

Usage:
    /home/tom/venv/bin/python RetroUniverse-3.py -i PRS.tsv [-o OUT.tsv]
        [--compare-live] [--limit N] [--fresh] [--port 4002]
        [--no-market-state] [--no-freq] [--no-impute] [--no-mcap]
        [--no-short-interest] [--no-vix] [--no-splits]
"""

import argparse
import asyncio
import importlib.util
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import time
from bisect import bisect_right
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from ib_insync import Stock

_SCRIPT_DIR = Path(__file__).resolve().parent
ET = ZoneInfo("America/New_York")

# The sibling modules (ITI_imputer, trade_size_imputer, closing_price_fetch_addOn
# -- the last also imported lazily by pipeline-3's _run_concurrent) must resolve
# however this file is started, not only as `python RetroUniverse-3.py`.
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))


def _load_by_path(name: str, path: Path):
    """Import a module whose filename is not importable (it has a hyphen)."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# pipeline-3 is the authority on how an addon is replayed and verified
# (_run_addon / _verify_passthrough), on the IBKR batch runner and its
# reconnect handling (_run_concurrent) and on the universe filters, so they are
# imported, not copied: the two scripts can never verify differently. Importing
# runs none of its pipeline -- the cron-log rotation and the run lock live in
# its main().
_p3 = _load_by_path("daily_universe_pipeline_3",
                    _SCRIPT_DIR / "daily_universe_pipeline-3.py")

_MSC_DIR = _SCRIPT_DIR.parent / "tools" / "market_state"
if str(_MSC_DIR) not in sys.path:
    sys.path.insert(0, str(_MSC_DIR))
import market_state_calculator as msc  # noqa: E402  (Sessions, split lookup)

_DATA_DIR    = _p3._DATA_DIR
_RUNS_DIR    = _p3._RUNS_DIR
_RETRO_DIR   = _RUNS_DIR / "retro"
_MSC_TABLES  = _MSC_DIR / "tables"          # split_cache_path appends /splits
_MCAP_SCRIPT = _SCRIPT_DIR / "marketcap_backfill_AddON.py"
_FREQ_SCRIPT = _SCRIPT_DIR / "trade_frequency_and_size_backfill_AddON.py"
_SI_SCRIPT   = _SCRIPT_DIR.parent / "ShortInterrest" / "ShortInterrest_backfill_AddON.py"
_SESSION_MAP = (_SCRIPT_DIR.parent / "price_stats_loader" / "cache"
                / "universe_session_map.tsv")
_STEP1_CACHE = _DATA_DIR / "nasdaq_symbols_data.tsv"

_OUT_SUFFIX    = "_RetroU3"
_DATE_COL      = _p3._DATE_COLUMN           # "Date" = ArrivalDate - 1
_SENTINEL      = "44444"
# Fetch-if-absent, never re-query: see the SAFETY note and pipeline-3's
# --msc-split-max-age.
_SPLIT_MAX_AGE = 36500
# S2's referee: a close this far (either way) from the nightly snapshot of the
# same session is a split basis error, not a revised bar. Above IBKR's late bar
# revisions (AMZE 09-16: 2.5%), below the smallest real split (5-for-4 = 1.25).
_REFEREE_MIN_RATIO = 1.2

# ── the universe columns, in the order pipeline-3 writes them ───────────────
_BASE_COLS = ("Exchange", "Float_M", "MarketCap_M", "Float_Source",
              "LastDailyClosePrice", "RTH_avgITI_sec", "ETH_avgITI_sec",
              "ITI_impute_flag", "ITI_impute_method",
              "RTH_tradeSize", "ETH_TradeSize",
              "TradeSize_impute_flag", "TradeSize_impute_method")
_SI_COLS = ("SI_Source", "SI_SettlementDate", "SI_SharesShort",
            "SI_PrevSharesShort", "SI_ChangePct", "SI_PctFloat", "SI_DTC",
            "SI_DollarADV", "SVR_Date", "SVR_Pct", "SVR_5dAvgPct",
            "SVR_TrendPts", "CTB_FeePct", "CTB_RebatePct", "CTB_FeeDelta5d",
            "Borrow_Available", "Borrow_AvailDelta5dPct", "Borrow_UtilProxy",
            "Borrow_Source", "RegSHO_Flag", "RegSHO_ConsecDays",
            "SqueezeFlags", "SqueezeScore", "SI_DataCoverage")
_RAW_COLS = tuple(_p3._RAW_SIDECARS)
_MSC_COLS = (tuple(msc.FEATURE_COLUMNS) + tuple(msc.STATUS_COLUMNS)
             + tuple(msc.SPLIT_COLUMNS) + tuple(msc.BASIS_COLUMNS))
_VIX_COLS = ("VIX_AnchorDate", "VIX_PrevClose", "VIX_Open", "VIX_High",
             "VIX_Low", "VIX_Close", "VIX_Chg1D_pct", "FNG_Index",
             "FNG_Rating", "FNG_PrevIndex", "FNG_Chg1D", "FNG_Momentum_SP500",
             "FNG_Momentum_SP125", "FNG_StockPriceStrength",
             "FNG_StockPriceBreadth", "FNG_PutCallRatio", "FNG_VIX50",
             "FNG_JunkBondSpread", "FNG_SafeHavenDemand", "FNG_AsOf")
_SPLIT_COLS = ("LastSplit_Date", "LastSplit_Factor", "LastSplit_MktHours",
               "LastSplit_Status")
_UNIVERSE_COLS = (_BASE_COLS + _SI_COLS + (_DATE_COL,) + _RAW_COLS
                  + _MSC_COLS + _VIX_COLS + _SPLIT_COLS)       # 150

# Provenance this script adds, plus marketcap_backfill's own.
_EXTRA_COLS = ("RU3_InUniverse", "RU3_FilterReason", "RU3_InLiveFile",
               "RU3_SnapshotDate", "RU3_Close_Session", "RU3_Close_SplitAdj",
               "MarketCap_Source", "MarketCap_AsOf", "MarketCap_LagDays")

# What trade_frequency_and_size_backfill_AddON writes, and where W keeps it:
# its values are the RAW fetch (44444 = attempted and failed), i.e. pipeline-3's
# *_raw sidecars. S9 then writes the imputed canonical columns.
_FREQ_TO_RAW = {"RTH_avgITI_sec": "RTH_avgITI_sec_raw",
                "ETH_avgITI_sec": "ETH_avgITI_sec_raw",
                "RTH_tradeSize":  "RTH_tradeSize_raw",
                "ETH_TradeSize":  "ETH_TradeSize_raw"}
_IMPUTED_COLS = ("RTH_avgITI_sec", "ETH_avgITI_sec", "ITI_impute_flag",
                 "ITI_impute_method", "RTH_tradeSize", "ETH_TradeSize",
                 "TradeSize_impute_flag", "TradeSize_impute_method")

# Markers: a column whose presence proves the step already ran on that file.
_JOIN_MARKER   = "RU3_SnapshotDate"
_CLOSE_MARKER  = "RU3_Close_SplitAdj"
_MCAP_MARKER   = "MarketCap_Source"
_SI_MARKER     = "SI_DataCoverage"
_VIX_MARKER    = _p3._VIX_MARKER
_SPLITS_MARKER = _p3._SPLITS_MARKER
_MSC_MARKER    = _p3._MSC_MARKER
_FREQ_MARKER   = "ETH_TradeSize"            # on a chunk file
_RAW_MARKER    = "RTH_avgITI_sec_raw"       # on W, once S8 merged
_IMPUTE_MARKER = "ITI_impute_flag"

# Universe-sourced inputs of the LIVE model-3 (O5.4/O5.5,
# training_data/model-3/configs/int8_c200_s04/features.txt); Float_M and
# MarketCap_M reach it renamed Float / MktCap. model-2's are pipeline-3's.
_M3_UNIVERSE_FEATURES = ("Float_M", "MarketCap_M", "atr_pct_14",
                         "realized_vol_21d", "range_pct_1d", "gap_abs_mean_20d",
                         "dist_52w_high_pct", "dist_swing_high_20d_pct",
                         "dist_swing_high_60d_pct", "amihud_63d",
                         "msc_split_sessions_since", "msc_basis_ratio",
                         "SI_SharesShort", "SVR_5dAvgPct")
_M2_UNIVERSE_FEATURES = tuple(_p3._M2_UNIVERSE_FEATURES)

_LIVE_PORTS = {4001: "the LIVE Gateway", 7496: "LIVE TWS"}
_NIGHTLY_SCRIPT = "daily_universe_pipeline-3.py"
_NIGHTLY_WINDOW = ((20, 5), (20, 40))       # ET [start, end): no IBKR work starts
# Describe the fetch, not the market: --compare-live skips them. (A series
# pulled on another day reaches a different start, so msc_bars_available moves
# while every feature computed from those bars stays put.)
_FETCH_TIME_COLS = {"msc_asof", "msc_fetch_secs", "msc_bars_fetched_at",
                    "msc_bars_available", "msc_split_factor_applied"}

_SNAPSHOT_RE = re.compile(
    r"^(stocks_universe|nasdaq_symbols_data_priced)_(\d{4}-\d{2}-\d{2})\.tsv$")
_DAY_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})(?:[ T].*)?$")

log = logging.getLogger("retro_universe3")


# ── logging ──────────────────────────────────────────────────────────────────

def _setup_logging() -> Path:
    """Per-day runs/{DD-MMM-YYYY}/RetroUniverse-3.log, next to pipeline-3's.

    The file handler sits on the ROOT logger, so every library warning
    (ib_insync's per-request errors, the in-process split lookup, the imputers)
    lands in the file too -- and closing_price_fetch_addOn's import-time
    basicConfig() becomes a no-op instead of a second console stream. The
    console shows only this script and the pipeline-3 helpers it borrows,
    which log as 'pipeline_daily3'.
    """
    log_dir = _RUNS_DIR / datetime.now().strftime("%d-%b-%Y")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "RetroUniverse-3.log"

    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(fh)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
    for name in ("retro_universe3", "pipeline_daily3"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.DEBUG)
        lg.addHandler(ch)

    logging.getLogger("msc").setLevel(logging.INFO)
    logging.getLogger("ib_insync").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)
    return log_path


# ── small helpers ────────────────────────────────────────────────────────────

def _read_tsv(path: Path) -> pd.DataFrame:
    """Byte-preserving read: every cell stays the text on disk, and the ticker
    NA (Nano Labs) is not eaten as a missing value."""
    return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False)


def _write_tsv(df: pd.DataFrame, path: Path) -> None:
    """Atomic write, so a crash can never leave a half-written W or output."""
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, sep="\t", index=False)
    os.replace(tmp, path)


def _parse_day(text) -> date | None:
    """yyyy-mm-dd out of a cell (a trailing time is tolerated), else None."""
    m = _DAY_RE.match(str(text))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(1))
    except ValueError:
        return None


def _num(text) -> float | None:
    """A cell as a number; blank, nan and the 44444 sentinel are None."""
    s = str(text).strip()
    if not s or s.lower() in ("nan", "none") or s == _SENTINEL:
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return None if v != v or v == 44444.0 else v


def _norm(sym) -> str:
    return str(sym).strip().upper()


def _sessions_for(days) -> "msc.Sessions":
    """An XNYS index wide enough to step back a session from any of `days`."""
    return msc.Sessions(min(days) - timedelta(days=40), max(days) + timedelta(days=5))


def _newest_on_or_before(days_sorted: list, d: date) -> date | None:
    i = bisect_right(days_sorted, d) - 1
    return days_sorted[i] if i >= 0 else None


def _replay(label: str, script: Path, argv: list[str], path: Path,
            suffix: str, marker: str) -> bool:
    """pipeline-3's _run_addon, with a guard for a missing script."""
    if not script.exists():
        log.error(f"{label}: {script} not found — skipping.")
        return False
    return _p3._run_addon(label, script, argv, path, suffix, marker)


# ── production safety ────────────────────────────────────────────────────────

def _nightly_running() -> bool:
    """True while a python process is running daily_universe_pipeline-3.py.

    Reads /proc only. It deliberately does NOT probe runs/pipeline_daily-3.lock:
    taking that flock even for an instant could make a cron starting in the same
    instant exit without writing tomorrow's universe. Matching on a python
    argv[0] keeps an editor or the cron's own `sh -c` line from counting.
    """
    me = os.getpid()
    target = _NIGHTLY_SCRIPT.encode()
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == me:
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as fh:
                argv = fh.read().split(b"\0")
        except OSError:
            continue
        if len(argv) < 2 or b"python" not in os.path.basename(argv[0]):
            continue
        if any(a.endswith(target) for a in argv[1:]):
            return True
    return False


def _yield_to_nightly(args, what: str) -> None:
    """Hold an IBKR step until the paper Gateway is free of the nightly.

    The nightly owns 4002 from 20:30 until it exits (Steps 2/3, then the ~85-min
    Step 9). Every IBKR step and chunk calls this before it starts; chunks are
    sized to ~15 min, so nothing started before 20:05 is still running at 20:30.
    """
    if args.no_yield:
        return
    said = None
    while True:
        running = _nightly_running()
        now = datetime.now(ET)
        (h0, m0), (h1, m1) = _NIGHTLY_WINDOW
        in_window = (h0, m0) <= (now.hour, now.minute) < (h1, m1)
        if not (running or in_window):
            if said is not None:
                log.info(f"  ▶ {what}: the paper Gateway is free again.")
            return
        if said is None or time.monotonic() - said >= 300:
            why = (f"{_NIGHTLY_SCRIPT} is running" if running
                   else "the nightly starts at 20:30 ET")
            log.info(f"  ⏸ {what} waits: {why} and it shares the paper Gateway "
                     f"(re-checked every 30 s; --no-yield overrides).")
            said = time.monotonic()
        time.sleep(30)


def _resolve_output(args, in_path: Path, tag: str) -> Path:
    out = (Path(args.output) if args.output
           else in_path.with_name(f"{in_path.stem}{_OUT_SUFFIX}{tag}.tsv")).resolve()
    if out.parent == _DATA_DIR.resolve() or out.name.startswith("stocks_universe_"):
        raise SystemExit(
            f"Refusing to write {out}: nothing may land in {_DATA_DIR} or be "
            f"named stocks_universe_* — RTPR_connector3 would load it as the "
            f"live universe. Pass -o somewhere else.")
    if out == in_path:
        raise SystemExit(f"The output would overwrite the input {in_path}; pass -o.")
    return out


def _check_universe_header() -> None:
    """Self-check: _UNIVERSE_COLS against the newest 147-column live snapshot.

    The column order is spelled out here (MSC's block comes from MSC itself);
    if pipeline-3's schema ever moves, say so instead of writing a silently
    different layout.
    """
    files = sorted(p for p in _DATA_DIR.glob("stocks_universe_*.tsv")
                   if _SNAPSHOT_RE.match(p.name))
    for p in reversed(files):
        hdr = _p3._read_header(p)
        if len(hdr) != 147:
            continue
        expect = ["Symbol", *_UNIVERSE_COLS[:-len(_SPLIT_COLS)]]
        if hdr == expect:
            log.debug(f"  column order verified against {p.name}")
        else:
            diff = [(i, a, b) for i, (a, b) in enumerate(zip(hdr, expect)) if a != b]
            log.warning(f"  ⚠ column order differs from the live {p.name} "
                        f"(first differences {diff[:4]}) — pipeline-3's schema "
                        f"moved; update _UNIVERSE_COLS.")
        return
    log.debug("  no 147-column snapshot to verify the column order against")


# ── S0: the input and the work table ────────────────────────────────────────

def _load_input(path: Path, sym_col: str, day_col: str) -> pd.DataFrame:
    df = _read_tsv(path)
    for col in (sym_col, day_col):
        if col not in df.columns:
            raise SystemExit(f"{path.name} has no '{col}' column "
                             f"(see --symbol-col / --date-col).")
    dupes = sorted({c[:-2] for c in df.columns
                    if c.endswith(".1") and c[:-2] in df.columns})
    if dupes:
        log.warning(f"  input header repeats {len(dupes)} name(s) (read back as "
                    f"'.1'): {', '.join(dupes[:6])} — values are kept as read.")
    return df


def _row_keys(df: pd.DataFrame, sym_col: str, day_col: str, today: date):
    """Per input row: the (Symbol, ArrivalDate) unit key, or None + a reason."""
    keys, status = [], []
    for sym, day in zip(df[sym_col], df[day_col]):
        s, d = str(sym).strip(), _parse_day(day)
        if not s or d is None:
            keys.append(None)
            status.append("bad_input")
        elif d - timedelta(days=1) >= today:
            # The file that serves it is written tonight or later.
            keys.append(None)
            status.append("no_nightly_yet")
        else:
            keys.append((s, d.isoformat()))
            status.append("")
    return keys, status


def _work_table(keys: list, limit: int) -> pd.DataFrame:
    """W: one row per unique (Symbol, ArrivalDate), first-appearance order."""
    units = list(dict.fromkeys(k for k in keys if k is not None))
    if limit:
        units = units[:limit]
    return pd.DataFrame({
        "Symbol": [s for s, _ in units],
        "ArrivalDate": [a for _, a in units],
        _DATE_COL: [(date.fromisoformat(a) - timedelta(days=1)).isoformat()
                    for _, a in units],
    })


def _open_work_dir(tag: str, W: pd.DataFrame, fresh: bool) -> Path:
    """runs/retro/<tag>/ with W.tsv; resumes when it holds the same units."""
    work = _RETRO_DIR / tag
    if fresh and work.exists():
        shutil.rmtree(work)
        log.info(f"  --fresh: removed {work}")
    for sub in ("msc", "freq", "imputer", "price_refs", "logs"):
        (work / sub).mkdir(parents=True, exist_ok=True)
    w_path = work / "W.tsv"
    if w_path.exists():
        old = _read_tsv(w_path)
        same = (len(old) == len(W)
                and old["Symbol"].tolist() == W["Symbol"].tolist()
                and old["ArrivalDate"].tolist() == W["ArrivalDate"].tolist())
        if not same:
            raise SystemExit(f"{work} holds the work table of a different input "
                             f"(or --limit). Re-run with --fresh to rebuild it.")
        done = [m for m in (_JOIN_MARKER, _CLOSE_MARKER, _MCAP_MARKER, _SI_MARKER,
                            _VIX_MARKER, _SPLITS_MARKER, _RAW_MARKER,
                            _IMPUTE_MARKER) if m in old.columns]
        log.info(f"  resuming {work.name}: already in W — "
                 f"{', '.join(done) if done else 'nothing yet'}")
    else:
        _write_tsv(W, w_path)
    return work


# ── S1: the served-by snapshot (Float_M, Float_Source, Exchange) ─────────────

def _snapshot_index() -> dict:
    """{date: path} of the canonical nightly snapshots in data/.

    Exact names only: the suffixed siblings (_superseded, _SI, _nonFiltered,
    _splits, _mcapBack ...) are not what the orchestrator loaded, and the
    2026-07-07 _1124_superseded file differs from the canonical one on 643
    closes.
    """
    idx = {}
    for p in _DATA_DIR.iterdir():
        m = _SNAPSHOT_RE.match(p.name)
        if not m:
            continue
        d = date.fromisoformat(m.group(2))
        if d not in idx or m.group(1) == "stocks_universe":
            idx[d] = p
    return idx


def _load_snapshot(path: Path) -> dict:
    t = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False,
                    usecols=lambda c: c in ("Symbol", "Exchange", "Float_M",
                                            "Float_Source"))
    out = {}
    cols = [t[c] if c in t.columns else [""] * len(t)
            for c in ("Exchange", "Float_M", "Float_Source")]
    for sym, exch, flt, src in zip(t["Symbol"], *cols):
        s = _norm(sym)
        if s and s not in out:
            out[s] = (exch, flt, src)
    return out


def _step_snapshot_join(w_path: Path) -> None:
    """S1 — Float_M / Float_Source / Exchange from the file that served the PR.

    Same rule as marketcap_backfill's join: the newest snapshot dated on or
    before calendar ArrivalDate - 1 (= Date) -- the file the orchestrator held.
    The training corpus's Float IS this value on 100% of its Jul/Aug rows.
    A symbol absent from that file gets a blank Float_M (no walk-back, no
    today's-yfinance rebuild: operator decision) and Float_Source 'none', the
    live vocabulary, so the imputers' one-hot stays in range.
    """
    W = _read_tsv(w_path)
    if _JOIN_MARKER in W.columns:
        log.info("S1 snapshot join: already in W — skipping.")
        return
    idx = _snapshot_index()
    days = sorted(idx)
    su_days = sorted(d for d, p in idx.items() if p.name.startswith("stocks_universe_"))
    step1 = {}
    if _STEP1_CACHE.exists():
        t = pd.read_csv(_STEP1_CACHE, sep="\t", dtype=str, keep_default_na=False,
                        usecols=lambda c: c in ("Symbol", "Exchange"))
        step1 = {_norm(s): e for s, e in zip(t["Symbol"], t["Exchange"])}

    loaded: dict = {}

    def snap(d):
        if d not in loaded:
            loaded[d] = _load_snapshot(idx[d])
        return loaded[d]

    exch, flt, fsrc, src_col, live_col = [], [], [], [], []
    hits = 0
    for sym, day in zip(W["Symbol"], W[_DATE_COL]):
        s, d = _norm(sym), date.fromisoformat(day)
        src = _newest_on_or_before(days, d)
        hit = snap(src).get(s) if src else None
        if hit:
            hits += 1
            e, f, fs = hit
            flt.append("" if str(f).strip().lower() in ("nan", "none") else f)
            fsrc.append(fs or "none")
            exch.append(e or step1.get(s, ""))
        else:
            flt.append("")
            fsrc.append("none")
            exch.append(step1.get(s, ""))
        src_col.append(src.isoformat() if src else "")
        # Membership of the stocks_universe file the orchestrator loaded --
        # the ground truth of whether this PR was even in scope that night.
        live = _newest_on_or_before(su_days, d)
        live_col.append("" if live is None else ("1" if s in snap(live) else "0"))

    W["Exchange"], W["Float_M"], W["Float_Source"] = exch, flt, fsrc
    W["RU3_SnapshotDate"], W["RU3_InLiveFile"] = src_col, live_col
    _write_tsv(W, w_path)
    log.info(f"S1 snapshot join: symbol found in its served-by snapshot on "
             f"{hits}/{len(W)} rows ({len(loaded)} snapshot files read); "
             f"in the live stocks_universe file: "
             f"{live_col.count('1')} yes / {live_col.count('0')} no / "
             f"{live_col.count('')} before 2026-06-09.")


# ── S2: LastDailyClosePrice ──────────────────────────────────────────────────

def _bar_day(bar) -> date:
    d = bar.date
    return d.date() if isinstance(d, datetime) else d


async def _daily_closes(ib, contract, first: date, last: date, sem,
                        timeout: int, retries: int):
    """[(session, RTH close)] for one symbol over [first, last], ONE request.

    Live Step 2's request shape (closing_price_fetch_addOn.
    fetch_last_rth_close_async): TRADES / 1 day / useRTH=True. The timeout goes
    INTO reqHistoricalDataAsync -- never asyncio.wait_for, whose cancellation
    leaks reqIds until the Gateway stops answering. The request ends the day
    after `last` at 00:00 UTC (msc.end_datetime_str), so `last`'s own bar is in.
    """
    end_str = msc.end_datetime_str(last)
    span = (last - first).days + 10
    duration = f"{span} D" if span <= 365 else f"{math.ceil(span / 365)} Y"
    async with sem:
        for attempt in range(retries + 1):
            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract, endDateTime=end_str, durationStr=duration,
                    barSizeSetting="1 day", whatToShow="TRADES", useRTH=True,
                    formatDate=1, keepUpToDate=False, timeout=timeout)
            except asyncio.CancelledError:
                raise
            except Exception:
                bars = None
            if bars:
                return [(_bar_day(b), float(b.close)) for b in bars]
            if attempt < retries:
                await asyncio.sleep(1.0)
    return None


def _pick_close(bars, anchor: date | None, prev: date | None):
    """The bar live Step 2 would have read for this anchor, or None.

    Live asks for '2 D' at 20:30 and takes bars[-1]: the anchor's own close, or
    the previous session's when the symbol did not trade on the anchor.
    """
    if not bars or anchor is None:
        return None
    best = None
    for d, close in bars:
        if d <= anchor and (prev is None or d >= prev) and close > 0:
            if best is None or d > best[0]:
                best = (d, close)
    return best


def _refresh_split_cache(symbols: list[str], workers: int) -> None:
    """Bring the shared split cache up to date by ADDING splits, never removing.

    A past anchor needs every split AFTER it, and a list cached weeks ago does
    not have them: CDT's and TRUG's 2026-09-29 reverse splits were missing from
    their 09-07 files, which put their 09-17 closes 25x / 10x off, read
    msc_basis_ratio 25 / 10 and divided their trade sizes by the same. A plain
    refresh (max-age 1, what the training chain ran) is no answer either: for a
    DELISTED symbol yfinance returns empty splits with live metadata, and
    fetch_splits then writes "never split" over real history (NTZ, OMH,
    2026-09-07). So each symbol is re-queried WITHOUT touching the cache
    (fetch_splits(use_cache=False)) and merged by ex-date: new ex-dates are
    added, a changed factor on the same ex-date takes yfinance's, and an ex-date
    yfinance no longer lists is KEPT (logged). A failed lookup changes nothing.
    Every reader after this -- this script, MSC, the TFB backfill, the split
    finder, marketcap_backfill -- then reads the cache at max-age 36500.
    """
    def look(sym):
        path = msc.split_cache_path(_MSC_TABLES, sym)
        cached = None
        if path.exists():
            try:
                t = pd.read_csv(path, sep="\t", float_precision="round_trip")
                cached = [(pd.to_datetime(r.ex_date).date(), float(r.factor))
                          for r in t.itertuples()]
            except Exception:
                cached = None
        return sym, path, cached, msc.fetch_splits(sym, _MSC_TABLES, use_cache=False)

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(look, symbols))
    n_files = n_dates = n_new = n_kept = n_failed = 0
    for sym, path, cached, fresh in results:
        if fresh is None:
            n_failed += 1
            continue
        if cached is None:
            merged = sorted(fresh)
            n_new += 1
        else:
            # A factor yfinance re-states within float noise is the SAME split:
            # keep the cached value so an unchanged file is never rewritten.
            by_day = dict(cached)
            for d, f in fresh:
                if d not in by_day or not math.isclose(by_day[d], f, rel_tol=1e-9):
                    by_day[d] = f
            merged = sorted(by_day.items())
            gone = sorted({d for d, _ in cached} - {d for d, _ in fresh})
            if gone:
                n_kept += 1
                log.debug(f"  splits {sym}: yfinance no longer lists "
                          f"{[d.isoformat() for d in gone]} — kept")
            if merged == sorted(cached):
                continue
            n_files += 1
            n_dates += len({d for d, _ in merged} - {d for d, _ in cached})
            log.info(f"  splits {sym}: {[(d.isoformat(), f) for d, f in merged]} "
                     f"(was {[(d.isoformat(), f) for d, f in sorted(cached)]})")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        pd.DataFrame(merged, columns=["ex_date", "factor"]).to_csv(
            tmp, sep="\t", index=False)
        os.replace(tmp, path)
    log.info(f"S2 split cache: {len(symbols)} symbols re-queried — {n_files} "
             f"updated ({n_dates} new ex-date(s)), {n_new} newly cached, "
             f"{n_kept} kept history yfinance no longer lists, {n_failed} "
             f"lookups failed (left as is).")


def _step_close(w_path: Path, args) -> bool:
    """S2 — the anchor session's RTH close, as traded.

    IBKR back-adjusts every bar to the share basis in force at REQUEST time, so
    the close is multiplied by forward_split_factor(splits, bar session, today)
    -- the as-traded value the nightly recorded that evening. The split list is
    the shared yfinance cache, freshly merged by _refresh_split_cache.
    RU3_Close_SplitAdj says which path a row took: adjusted / none_needed /
    unknown (lookup failed — the request-basis close is kept, as
    lastDailyClose_AddOn does) / snapshot (IBKR has no bar any more, e.g. a
    symbol delisted since; the nightly's own close, from a snapshot holding
    exactly the anchor session) / refereed (see below).

    yfinance can date a split on another session than IBKR applied it: RUBI's
    3-for-2 is listed ex 2026-10-06, but IBKR's 10-05 bar is already post-split
    (the 10-05 nightly recorded 0.70 and today's bar is 0.70), so multiplying
    by 1.5 wrote 1.05. Where a snapshot holds exactly the anchor session, its
    close is as-traded by construction and referees the result: off by
    _REFEREE_MIN_RATIO or more, the snapshot's close is used (refereed), as
    trade_frequency_and_size_backfill's price probe trusts IBKR over yfinance.
    Without such a snapshot (before 2026-05-14, or a session no nightly
    recorded) yfinance's factor stands unaudited.
    """
    W = _read_tsv(w_path)
    if _CLOSE_MARKER in W.columns:
        log.info("S2 close: already in W — skipping.")
        return True
    days = [date.fromisoformat(x) for x in W[_DATE_COL]]
    sessions = _sessions_for(days)
    anchors = [sessions.resolve(d, 0) for d in days]
    syms = [_norm(s) for s in W["Symbol"]]
    span: dict = {}
    for s, a in zip(syms, anchors):
        if a is not None:
            lo, hi = span.get(s, (a, a))
            span[s] = (min(lo, a), max(hi, a))
    symbols = sorted(span)
    # Also yields the shared yfinance quota: the nightly's Step 1 is ~7000
    # .info calls and already meets rate limits on its own.
    _yield_to_nightly(args, "S2 split refresh + close fetch")
    if args.no_split_refresh:
        log.info("S2 split cache: --no-split-refresh — reading it as it is.")
    else:
        _refresh_split_cache(symbols, args.split_workers)
    splits = msc.fetch_all_splits(symbols, _MSC_TABLES, True,
                                  args.split_workers, _SPLIT_MAX_AGE)

    log.info(f"S2 close: {len(symbols)} symbols / {len(W)} rows — one RTH daily "
             f"request per symbol on {args.host}:{args.port} "
             f"(clientId {args.clientID}, concurrency {args.price_concurrency}) …")
    from closing_price_fetch_addOn import connect  # noqa: E402
    try:
        ib = connect(args.host, args.port, args.clientID)
    except SystemExit:
        log.error(f"S2 close: no IBKR connection on {args.host}:{args.port} — is "
                  f"the paper Gateway up? Every later step needs the closes; "
                  f"re-run the same command to resume.")
        return False

    fetched_on = datetime.now(ET).date()
    bars_by: dict = {}
    by_sym: dict = {}
    try:
        sem = asyncio.Semaphore(args.price_concurrency)

        async def _qualify(symbol):
            async with sem:
                try:
                    q = await ib.qualifyContractsAsync(Stock(symbol, "SMART", "USD"))
                except asyncio.CancelledError:
                    raise
                except Exception:
                    return None
                return q[0] if q else None

        contracts = _p3._run_concurrent(
            ib, symbols, coro_factory=_qualify, is_empty=lambda c: c is None,
            nan_value=None, label="S2 qualify", args=args)
        by_sym = dict(zip(symbols, contracts))

        async def _closes(symbol):
            contract = by_sym.get(symbol)
            if contract is None:
                return None
            first, last = span[symbol]
            return await _daily_closes(ib, contract,
                                       sessions.shift_back(first, 1) or first,
                                       last, sem, args.price_timeout, args.retries)

        out = _p3._run_concurrent(
            ib, symbols, coro_factory=_closes, is_empty=lambda r: not r,
            nan_value=None, label="S2 close fetch", args=args)
        bars_by = dict(zip(symbols, out))
    except Exception as exc:
        log.exception(f"S2 close: unexpected error ({exc!r}).")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    n_no_bars = sum(1 for s in symbols if not bars_by.get(s))
    qualified = [s for s in symbols if by_sym.get(s) is not None]
    starved = sum(1 for s in qualified if not bars_by.get(s))
    if qualified and starved > 0.5 * len(qualified):
        # The pipeline-3 contention-canary signature: not "these symbols are
        # gone" (they fail to qualify, and a corpus of old PRs has many) but
        # "the Gateway answered nothing for symbols it knows". Leave S2 undone.
        log.error(f"S2 close: {starved}/{len(qualified)} qualified symbols "
                  f"returned no bars — the Gateway is starved or down. Nothing "
                  f"written; re-run to resume.")
        return False

    # A symbol IBKR no longer serves (delisted since) falls back to the close
    # the nightly itself recorded -- but only from a snapshot whose TRUE
    # session is this row's anchor; that value is as-traded by construction.
    snap_map, _ = _snapshot_sessions()
    snap_px: dict = {}

    def snapshot_close(served: str, sym: str, anchor):
        d = _parse_day(served)
        hit = snap_map.get(d) if d else None
        if anchor is None or not hit or hit[0] != anchor:
            return None
        if d not in snap_px:
            t = pd.read_csv(hit[1], sep="\t", dtype=str, keep_default_na=False,
                            usecols=lambda c: c in ("Symbol", "LastDailyClosePrice"))
            snap_px[d] = {_norm(x): px for x, px in
                          zip(t["Symbol"], t.get("LastDailyClosePrice", [""] * len(t)))}
        v = _num(snap_px[d].get(sym, ""))
        return v if v is not None and v > 0 else None

    closes, bar_days, adj, refereed = [], [], [], []
    counts = {"adjusted": 0, "none_needed": 0, "unknown": 0, "snapshot": 0,
              "refereed": 0, "": 0}
    for s, a, served in zip(syms, anchors, W[_JOIN_MARKER]):
        pick = _pick_close(bars_by.get(s), a,
                           sessions.shift_back(a, 1) if a else None)
        if pick is None:
            v = snapshot_close(served, s, a)
            closes.append("" if v is None else f"{v:.4f}")
            bar_days.append("" if v is None else a.isoformat())
            adj.append("" if v is None else "snapshot")
            counts["" if v is None else "snapshot"] += 1
            continue
        bday, close = pick
        f = msc.forward_split_factor(splits.get(s), bday, fetched_on)
        if f is None:
            tag, value = "unknown", close
        elif f == 1.0:
            tag, value = "none_needed", close
        else:
            tag, value = "adjusted", close * f
        ref = snapshot_close(served, s, a)
        if ref is not None and max(value, ref) / min(value, ref) >= _REFEREE_MIN_RATIO:
            refereed.append(f"{s} {a}: {value:.4f} ({tag}, F={f}) -> snapshot {ref:.4f}")
            tag, value = "refereed", ref
        closes.append(f"{value:.4f}")
        bar_days.append(bday.isoformat())
        adj.append(tag)
        counts[tag] += 1

    W["LastDailyClosePrice"] = closes
    W["RU3_Close_Session"] = bar_days
    W["RU3_Close_SplitAdj"] = adj
    _write_tsv(W, w_path)
    log.info(f"S2 close: {len(W) - counts['']}/{len(W)} rows priced "
             f"(split-adjusted {counts['adjusted']}, none needed "
             f"{counts['none_needed']}, split list unknown {counts['unknown']}, "
             f"from the nightly's own snapshot {counts['snapshot']}, refereed "
             f"by it {counts['refereed']}); {counts['']} without a close; "
             f"{n_no_bars}/{len(symbols)} symbols returned nothing from IBKR.")
    if refereed:
        log.warning(f"S2 close: {len(refereed)} row(s) off the nightly snapshot of "
                    f"their session by a split-sized ratio — the split list and "
                    f"IBKR disagree on a split's date; the snapshot's close is used:")
        for line in refereed[:15]:
            log.warning(f"    {line}")
        if len(refereed) > 15:
            log.warning(f"    … and {len(refereed) - 15} more")
    return True


# ── S5 check ─────────────────────────────────────────────────────────────────

def _check_vix_rows(w_path: Path) -> None:
    """VIX_AnchorDate against each row's anchor session (pipeline-3's check,
    per row). vix_addOn anchors on the sessions CBOE has printed, so a CBOE gap
    would slide a row back silently; VIX_AnchorDate records the truth."""
    W = _read_tsv(w_path)
    if _VIX_MARKER not in W.columns:
        return
    days = [date.fromisoformat(x) for x in W[_DATE_COL]]
    sessions = _sessions_for(days)
    bad = sum(1 for d, v in zip(days, W[_VIX_MARKER])
              if (sessions.resolve(d, 0) or date.min).isoformat() != v)
    if bad:
        log.warning(f"S5 vix: VIX_AnchorDate is not the anchor session on "
                    f"{bad}/{len(W)} rows (CBOE had no row for it); "
                    f"VIX_AnchorDate / FNG_AsOf record the truth per row.")
    else:
        log.info(f"S5 vix: VIX_AnchorDate == anchor session on all {len(W)} rows.")


# ── chunked IBKR steps ───────────────────────────────────────────────────────

def _chunk_files(chunk_dir: Path, prefix: str, W: pd.DataFrame, groups: list,
                 cols: tuple) -> list[Path]:
    """The chunk plan: written once, then reused verbatim on every resume.

    The plan lives in the chunk files themselves, so changing a --*-chunk-*
    size mid-run cannot reshuffle rows between finished and unfinished chunks.
    """
    existing = sorted(chunk_dir.glob(f"{prefix}_[0-9][0-9][0-9].tsv"))
    want = list(zip(W["Symbol"], W["ArrivalDate"]))
    if existing:
        have = []
        for p in existing:
            t = pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False,
                            usecols=["Symbol", "ArrivalDate"])
            have.extend(zip(t["Symbol"], t["ArrivalDate"]))
        if len(have) == len(want) and set(have) == set(want):
            return existing
        log.warning(f"  {chunk_dir.name}/{prefix}_* does not cover its rows — "
                    f"rebuilding that chunk plan.")
        for p in chunk_dir.glob(f"{prefix}_*"):
            p.unlink()
    paths = []
    for k, rows in enumerate(groups, 1):
        p = chunk_dir / f"{prefix}_{k:03d}.tsv"
        _write_tsv(W.loc[rows, list(cols)], p)
        paths.append(p)
    return paths


def _snapshot_sessions() -> tuple[dict, int]:
    """({file_date: (session, path)}, n_late) for the canonical stocks_universe
    files: the trading session whose close each one actually holds.

    The filename is not always that session:
      * the June 06-09..06-15 files were hand-run before dawn and hold the
        previous session's close;
      * weekend files hold Friday's.
    price_stats_loader/cache/universe_session_map.tsv records it up to 09-03,
    and resolve(file_date, 0) is right for the evening crons after that. A file
    written more than a day after its own date came from a stalled or late
    re-run whose Step 2 fetched a later close; it is left out unless the map
    vouches for it.
    """
    smap = {}
    if _SESSION_MAP.exists():
        t = pd.read_csv(_SESSION_MAP, sep="\t", dtype=str, keep_default_na=False)
        for f, s in zip(t["file_date"], t["session"]):
            fd, sd = _parse_day(f), _parse_day(s)
            if fd and sd:
                smap[fd] = sd
    files = {d: p for d, p in _snapshot_index().items()
             if p.name.startswith("stocks_universe_")}
    if not files:
        return {}, 0
    sessions = _sessions_for(list(files))
    out, late = {}, 0
    for d, p in files.items():
        if d not in smap:
            written = datetime.fromtimestamp(p.stat().st_mtime, ET).date()
            if (written - d).days > 1:
                late += 1
                log.debug(f"  snapshot {p.name} written {written} — session unknown, skipped")
                continue
        s = smap.get(d) or sessions.resolve(d, 0)
        if s is not None:
            out[d] = (s, p)
    return out, late


def _build_price_refs(ref_dir: Path) -> tuple[int, int]:
    """stocks_universe_<SESSION>.tsv symlinks: MSC's basis referee, live-like.

    market_state_calculator.load_universe_price_refs looks the reference up BY
    FILENAME as the anchor session. Live, that is the file written the same
    night, so the referee always describes the anchor session. Here every
    snapshot is linked under the session it really holds (_snapshot_sessions),
    preferring the file written ON that session. Rebuilt every run, so a new
    snapshot is picked up. Returns (links made, files skipped as late).
    """
    for p in ref_dir.glob("stocks_universe_*.tsv"):
        p.unlink()
    mapping, late = _snapshot_sessions()
    best: dict = {}
    for d, (s, p) in mapping.items():
        rank = (d != s, d)
        if s not in best or rank < best[s][0]:
            best[s] = (rank, p)
    for s, (_, p) in best.items():
        (ref_dir / f"stocks_universe_{s.isoformat()}.tsv").symlink_to(p)
    return len(best), late


_MSC_CHUNK_COLS = ("Symbol", "ArrivalDate", _DATE_COL, "Float_M",
                   "LastDailyClosePrice")
_MSC_FRESH_PREFIX = "mscnc"     # chunks run on fresh bars (see below)


def _fresh_bar_cache(work: Path) -> Path:
    """A private MSC --cache-dir for the fresh-bar chunks: an EMPTY bars/ (every
    series is pulled today; the shared bar cache is neither read nor written)
    beside a link to the shared, additively merged splits/.

    MSC's own --no-cache cannot do this: it also skips the split cache and asks
    yfinance directly, whose answer can have lost history. On 2026-10-09
    yfinance listed only NIVF's split of that day, dropping seven older ones,
    so msc_split_* read "no split" where the cache (and the live file) had
    09-01 -- msc_split_sessions_since is a model-3 input.
    """
    root = work / "msc_cache"
    shutil.rmtree(root / "bars", ignore_errors=True)
    (root / "bars").mkdir(parents=True)
    link = root / "splits"
    if not link.is_symlink():
        link.symlink_to(_MSC_TABLES / "splits", target_is_directory=True)
    return root


def _step_market_state(work: Path, w_path: Path, args) -> bool:
    """S7 — the 84 market-state columns, chunked by SYMBOL.

    MSC fetches one series per symbol covering all of its anchors and computes
    each row from that symbol's own bars, so a symbol chunk gives exactly what
    one big run would. --session both is not optional (the msc_ block is not
    comparable across session modes); --price-ref-dir is the session-calibrated
    symlink dir; this run's own close is the fallback referee, which -- like
    live -- reads 1.0. --log must stay LAST (it takes an optional value).

    Symbols with a split after their earliest anchor run in their own chunks
    on fresh bars (_fresh_bar_cache). MSC's shared bar cache may hold a series
    pulled ON the ex-date, and its split arithmetic assumes such bars are still
    pre-split (forward_split_factor and split_prefix_products both stop at
    ex-date >= fetched_at -- right for ARTL 08-31, wrong for CDT/TRUG 09-29,
    which the 09-29 nightly pulled already adjusted: basis_mismatch, ratio
    25 / 10, no referee can fix it). Bars fetched today put every past split
    strictly before the fetch. Everyone else keeps the shared cache, which a
    nightly has usually filled already; the fresh chunks neither read nor
    write it.
    """
    W = _read_tsv(w_path)
    syms = W["Symbol"].map(_norm)
    days = [date.fromisoformat(x) for x in W[_DATE_COL]]
    sessions = _sessions_for(days)
    first_anchor: dict = {}
    for s, d in zip(syms, days):
        a = sessions.resolve(d, 0)
        if a is not None and (s not in first_anchor or a < first_anchor[s]):
            first_anchor[s] = a
    splits = msc.fetch_all_splits(sorted(first_anchor), _MSC_TABLES, True,
                                  args.split_workers, _SPLIT_MAX_AGE)
    straddle = {s for s, a in first_anchor.items()
                if any(d > a for d, _ in (splits.get(s) or []))}

    n = max(1, args.msc_chunk_symbols)
    plans = []
    for prefix, members in (("msc", lambda s: s not in straddle),
                            (_MSC_FRESH_PREFIX, lambda s: s in straddle)):
        mask = syms.map(members)
        if not mask.any():
            continue
        sub = W[mask]
        order = list(dict.fromkeys(syms[mask]))
        groups = [sub.index[syms[mask].isin(set(order[i:i + n]))].tolist()
                  for i in range(0, len(order), n)]
        plans += [(prefix, p) for p in
                  _chunk_files(work / "msc", prefix, sub, groups, _MSC_CHUNK_COLS)]

    pending = [(prefix, p) for prefix, p in plans
               if _MSC_MARKER not in _p3._read_header(p)]
    if not pending:
        log.info(f"S7 market state: all {len(plans)} chunk(s) already done.")
        return True
    refs = work / "price_refs"
    n_refs, n_late = _build_price_refs(refs)
    log.info(f"S7 market state: {len(pending)}/{len(plans)} chunk(s) to run "
             f"(~{n} symbols each, --session both; {len(straddle)} symbol(s) "
             f"with a split after their anchor refetched fresh); {n_refs} "
             f"session-calibrated universe referees"
             f"{f', {n_late} late-written snapshot(s) left out' if n_late else ''}.")
    for prefix, p in pending:
        k = plans.index((prefix, p)) + 1
        _yield_to_nightly(args, f"S7 market-state chunk {k}/{len(plans)}")
        _p3._run_addon(
            f"S7 market-state {k}/{len(plans)} ({p.stem})", _p3._MSC_SCRIPT,
            ["-i", str(p),
             "--date-column-name", _DATE_COL, "--days-prior", "0",
             "--session", "both",
             "--float-col", "Float_M", "--float-unit", "millions",
             "--price-col", "LastDailyClosePrice",
             "--price-ref-dir", str(refs),
             "--split-cache-max-age-days", str(_SPLIT_MAX_AGE),
             *(["--cache-dir", str(_fresh_bar_cache(work))]
               if prefix == _MSC_FRESH_PREFIX else []),
             "--host", args.host, "--port", str(args.port),
             "--client-id", str(args.msc_client_id),
             "--concurrency", str(args.msc_concurrency),
             "--timeout", str(args.msc_timeout),
             "--log", str(work / "logs" / f"{p.stem}.log")],
            p, "mktstate", _MSC_MARKER)
    left = [p.name for _, p in plans if _MSC_MARKER not in _p3._read_header(p)]
    if left:
        log.warning(f"S7 market state: {len(left)} chunk(s) not done "
                    f"({', '.join(left[:5])}) — their rows stay blank; re-run "
                    f"to retry them.")
        return False
    return True


def _freq_argv(path: Path, args, concurrency: int, log_path: Path) -> list[str]:
    """trade_frequency_and_size_backfill_AddON, replayed as pipeline-3 Step 3.

    `--split-source both`: the price probe divides the chunk's
    LastDailyClosePrice by IBKR's bar for the same session, i.e. it measures
    the split factor S2 really applied, and the trade size follows it wherever
    the backfill's own yfinance factor disagrees. Two cases on 2026-10-09:
      * RUBI 10-05, which S2 refereed (yfinance dates the split a session
        late): sizes would otherwise be divided by 1.5;
      * NIVF / NXGL / ZBAO, whose splits went ex THAT day: yfinance lists them,
        IBKR had not applied them yet. S2's forward_split_factor stops at the
        fetch day, the backfill's split_factor_since does not, so their sizes
        came out 4x / 20x / 50x too large.
    Elsewhere the probe agrees with yfinance and changes nothing; it costs one
    daily bar per unit. `--split-max-age-days 36500`: see the SAFETY note.
    """
    return ["-i", str(path),
            "--date-col", _DATE_COL, "--days-prior", "0",
            "--split-source", "both",
            "--split-max-age-days", str(_SPLIT_MAX_AGE),
            "--host", args.host, "--port", str(args.port),
            "--clientID", str(args.freq_client_id),
            "--freq-concurrency", str(concurrency),
            "--freq-timeout", str(args.freq_timeout),
            "--log", str(log_path)]


def _freq_retry(work: Path, W: pd.DataFrame, raw: dict, args) -> dict:
    """pipeline-3 Step 3.5: one paced, lower-concurrency second pass.

    The Error 162/366 cascade is Gateway-side slot accounting, so an inline
    retry fails for the reason the first try did; a separate pass after a
    90-s pause does not. Rows are those whose RTH value is still the sentinel,
    and only when they are at most half of all rows (beyond that the Gateway is
    the problem). Run directly rather than through _run_addon because it
    deliberately rewrites cells in place.
    """
    failed = [i for i, v in enumerate(raw["RTH_avgITI_sec"])
              if str(v).strip() in ("", _SENTINEL)]
    if not failed:
        return raw
    if len(failed) > 0.5 * len(W):
        log.warning(f"S8 retry: skipped — {len(failed)}/{len(W)} rows failed "
                    f"(>50%); a paced retry won't fix that, check the Gateway.")
        return raw
    conc = max(1, min(2, args.freq_concurrency // 2))
    path = work / "freq" / "retry.tsv"
    sub = W.iloc[failed][["Symbol", "ArrivalDate", _DATE_COL,
                          "LastDailyClosePrice"]].copy()
    for col in _FREQ_TO_RAW:
        sub[col] = [raw[col][i] for i in failed]
    _write_tsv(sub, path)
    sibling = path.with_name(f"{path.stem}_freqSizeBack.tsv")
    if sibling.exists():
        sibling.unlink()
    log.info(f"S8 retry: {len(failed)}/{len(W)} rows failed; sleeping 90 s to "
             f"let Gateway slot accounting drain, then concurrency={conc}.")
    time.sleep(90)
    _yield_to_nightly(args, "S8 retry pass")
    cmd = [sys.executable, str(_FREQ_SCRIPT),
           *_freq_argv(path, args, conc, work / "logs" / "freq_retry.log")]
    proc = subprocess.run(cmd, cwd=str(_FREQ_SCRIPT.parent), text=True,
                          capture_output=True)
    if proc.returncode != 0 or not sibling.exists():
        log.warning(f"S8 retry: exited rc={proc.returncode} — keeping the "
                    f"first-pass values.")
        for line in (proc.stderr or "").splitlines()[-10:]:
            log.debug(f"  [S8 retry:stderr] {line}")
        return raw
    out = _read_tsv(sibling)
    if (out["Symbol"].tolist() != sub["Symbol"].tolist()
            or out["ArrivalDate"].tolist() != sub["ArrivalDate"].tolist()):
        log.warning("S8 retry: output rows do not line up with the retry file "
                    "— ignoring it.")
        return raw
    recovered = dict.fromkeys(_FREQ_TO_RAW, 0)
    for j, i in enumerate(failed):
        for col in _FREQ_TO_RAW:
            v = str(out[col].iat[j]).strip()
            if v and v != _SENTINEL and str(raw[col][i]).strip() in ("", _SENTINEL):
                raw[col][i] = v
                recovered[col] += 1
    log.info("S8 retry: recovered " + ", ".join(
        f"{c} {recovered[c]}/{len(failed)}" for c in _FREQ_TO_RAW))
    return raw


def _step_freq(work: Path, w_path: Path, args) -> bool:
    """S8 — raw RTH/ETH ITI + trade size, chunked by UNIT.

    Rows are sorted by (symbol, Date) first, so the units the backfill dedups
    (symbol, anchor session) never straddle two chunks. Its output is the RAW
    fetch with the 44444 sentinel -- pipeline-3's *_raw sidecars -- and only
    lands in W once every chunk is done and the retry pass has run.
    """
    W = _read_tsv(w_path)
    if _RAW_MARKER in W.columns:
        log.info("S8 trade frequency/size: already in W — skipping.")
        return True
    order = (W.assign(_s=W["Symbol"].map(_norm))
              .sort_values(["_s", _DATE_COL], kind="stable").index.tolist())
    n = max(1, args.freq_chunk_units)
    groups = [order[i:i + n] for i in range(0, len(order), n)]
    paths = _chunk_files(work / "freq", "freq", W, groups,
                         ("Symbol", "ArrivalDate", _DATE_COL, "LastDailyClosePrice"))
    pending = [p for p in paths if _FREQ_MARKER not in _p3._read_header(p)]
    if pending:
        log.info(f"S8 trade frequency/size: {len(pending)}/{len(paths)} chunk(s) "
                 f"to run (~{n} units each, concurrency {args.freq_concurrency}, "
                 f"~2.7 s/unit) …")
    for p in pending:
        k = paths.index(p) + 1
        _yield_to_nightly(args, f"S8 trade-frequency chunk {k}/{len(paths)}")
        _replay(f"S8 trade freq/size {k}/{len(paths)}", _FREQ_SCRIPT,
                _freq_argv(p, args, args.freq_concurrency,
                           work / "logs" / f"{p.stem}.log"),
                p, "freqSizeBack", _FREQ_MARKER)
    left = [p.name for p in paths if _FREQ_MARKER not in _p3._read_header(p)]
    if left:
        log.warning(f"S8 trade frequency/size: {len(left)} chunk(s) not done "
                    f"({', '.join(left[:5])}) — re-run to retry them; "
                    f"imputation waits for all of them.")
        return False

    got = pd.concat([pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False,
                                 usecols=["Symbol", "ArrivalDate", *_FREQ_TO_RAW])
                     for p in paths], ignore_index=True)
    merged = W[["Symbol", "ArrivalDate"]].merge(
        got, on=["Symbol", "ArrivalDate"], how="left", validate="one_to_one")
    raw = {c: merged[c].fillna(_SENTINEL).tolist() for c in _FREQ_TO_RAW}
    raw = _freq_retry(work, W, raw, args)
    for src, dst in _FREQ_TO_RAW.items():
        W[dst] = raw[src]
    _write_tsv(W, w_path)
    ok = {c: sum(1 for v in raw[c] if str(v).strip() not in ("", _SENTINEL))
          for c in _FREQ_TO_RAW}
    log.info("S8 trade frequency/size: " + ", ".join(
        f"{c} {ok[c]}/{len(W)}" for c in _FREQ_TO_RAW) + " fetched.")
    return True


# ── S9: pooled imputation ────────────────────────────────────────────────────

def _step_impute(work: Path, w_path: Path, args) -> bool:
    """S9 — the canonical ITI / trade-size columns, imputed over ALL rows.

    Operator decision: ONE pooled model over the input (the nightly fits one per
    night's cross-section; a PR file has no such cross-section). The imputers
    are called exactly as pipeline-3 Steps 4/7 call them -- in place on a file
    in their fixed schema -- on a temp copy. Symbol is replaced by a row key:
    neither imputer uses it, and a real ticker 'NA' would not survive their
    read; row order is the join. Below the imputers' 200-row gate they write the
    44444 sentinels back with their own flags and method ''.
    """
    W = _read_tsv(w_path)
    if _IMPUTE_MARKER in W.columns:
        log.info("S9 imputation: already in W — skipping.")
        return True
    if _RAW_MARKER not in W.columns:
        log.warning("S9 imputation: no raw ITI/size in W yet (S8 incomplete).")
        return False
    if args.no_impute:
        for src, dst in _FREQ_TO_RAW.items():
            W[src] = W[dst]
        for c in ("ITI_impute_flag", "ITI_impute_method",
                  "TradeSize_impute_flag", "TradeSize_impute_method"):
            W[c] = ""
        _write_tsv(W, w_path)
        log.info("S9 imputation: --no-impute — canonical columns = raw values.")
        return True

    keys = [f"RU{i:06d}" for i in range(len(W))]
    path = work / "imputer" / "pooled.tsv"
    t = pd.DataFrame({
        "Symbol": keys,
        "Exchange": W["Exchange"],
        "Float_M": W["Float_M"],
        "MarketCap_M": W["MarketCap_M"] if "MarketCap_M" in W.columns else "",
        "Float_Source": W["Float_Source"],
        "LastDailyClosePrice": W["LastDailyClosePrice"],
        "RTH_avgITI_sec": W["RTH_avgITI_sec_raw"],
        "ETH_avgITI_sec": W["ETH_avgITI_sec_raw"],
    })
    _write_tsv(t, path)
    log.info(f"S9 imputation: pooled over {len(W)} rows (ITI_imputer, then "
             f"trade_size_imputer) …")
    try:
        from ITI_imputer import main as iti_main  # noqa: E402
        rc_iti = iti_main(["--input", str(path), "--quiet"])
        t = _read_tsv(path)
        t["RTH_tradeSize"] = W["RTH_tradeSize_raw"].to_numpy()
        t["ETH_TradeSize"] = W["ETH_TradeSize_raw"].to_numpy()
        _write_tsv(t, path)
        from trade_size_imputer import main as size_main  # noqa: E402
        rc_size = size_main(["--input", str(path), "--quiet"])
        t = _read_tsv(path)
    except Exception as exc:
        log.exception(f"S9 imputation failed ({exc!r}) — W keeps the raw "
                      f"values; re-run to retry.")
        return False
    if len(t) != len(W) or t["Symbol"].tolist() != keys:
        log.error("S9 imputation: the imputers' output does not line up with W "
                  "— not merged.")
        return False
    for c in _IMPUTED_COLS:
        W[c] = t[c].to_numpy()
    _write_tsv(W, w_path)
    log.info(f"S9 imputation: ITI rc={rc_iti} flags "
             f"{dict(W['ITI_impute_flag'].value_counts())}; trade size "
             f"rc={rc_size} flags {dict(W['TradeSize_impute_flag'].value_counts())}"
             f"{' (rc=1: under the 200-row gate, sentinels kept)' if rc_iti or rc_size else ''}.")
    return True


# ── S10/S11: flags, assembly, the join back onto the input ───────────────────

def _universe_flags(U: pd.DataFrame, args):
    """RU3_InUniverse / RU3_FilterReason under pipeline-3's strict filters
    (NaN fails, 5-character symbols ending in W are warrants)."""
    inu, why = [], []
    for sym, f, m, p in zip(U["Symbol"], U["Float_M"], U["MarketCap_M"],
                            U["LastDailyClosePrice"]):
        s = _norm(sym)
        reasons = ["warrant"] if len(s) == 5 and s.endswith("W") else []
        for name, val, cap in (("float", f, args.max_float_m),
                               ("mcap", m, args.max_mcap_m),
                               ("price", p, args.max_price)):
            v = _num(val)
            if v is None:
                reasons.append(f"{name}_missing")
            elif v >= cap:
                reasons.append(f"{name}>={cap:g}")
        inu.append("0" if reasons else "1")
        why.append(";".join(reasons))
    return inu, why


def _universe_frame(work: Path, w_path: Path, args) -> pd.DataFrame:
    """One row per unit: the 150 universe columns + extras, in output order.

    MSC results are read straight from the finished chunks, so an incomplete
    S7 still yields every finished symbol; anything a skipped or failed step
    should have produced is blank.
    """
    W = _read_tsv(w_path)
    # Both plans: msc_NNN (shared bar cache) and mscnc_NNN (fresh bars).
    done = [p for p in sorted((work / "msc").glob("msc*_[0-9][0-9][0-9].tsv"))
            if _MSC_MARKER in _p3._read_header(p)]
    if done:
        got = pd.concat([pd.read_csv(p, sep="\t", dtype=str, keep_default_na=False,
                                     usecols=["Symbol", "ArrivalDate", *_MSC_COLS])
                         for p in done], ignore_index=True)
        W = W.merge(got, on=["Symbol", "ArrivalDate"], how="left",
                    validate="one_to_one")
    for c in (*_UNIVERSE_COLS, *_EXTRA_COLS):
        if c not in W.columns:
            W[c] = ""
    W = W.fillna("")
    W["RU3_InUniverse"], W["RU3_FilterReason"] = _universe_flags(W, args)
    return W[["Symbol", "ArrivalDate", *_UNIVERSE_COLS, *_EXTRA_COLS]]


def _write_output(df_in: pd.DataFrame, keys: list, status: list,
                  U: pd.DataFrame, out_path: Path, limit: int):
    """Input rows (colliding names renamed <name>_input) + the universe block.

    Returns (keep mask, rename map, appended block) for verification/coverage.
    With --limit only the rows of the first N units are written.
    """
    appended = [*_UNIVERSE_COLS, *_EXTRA_COLS]
    taken = set(df_in.columns) | set(appended)
    rename = {}
    for c in appended:
        if c in df_in.columns:
            new, n = f"{c}_input", 2
            while new in taken:
                new, n = f"{c}_input{n}", n + 1
            rename[c] = new
            taken.add(new)

    pos_of = {k: i for i, k in enumerate(zip(U["Symbol"], U["ArrivalDate"]))}
    pos = np.array([pos_of.get(k, -1) if k is not None else -1 for k in keys],
                   dtype=int)
    keep = (pos >= 0) if limit else np.ones(len(df_in), dtype=bool)
    left = df_in.loc[keep].rename(columns=rename).reset_index(drop=True)
    p = pos[keep]
    block = pd.DataFrame("", index=range(len(p)), columns=appended)
    hit = p >= 0
    if hit.any():
        block.loc[hit, appended] = U[appended].iloc[p[hit]].to_numpy()
    if (~hit).any():
        block.loc[~hit, "RU3_FilterReason"] = np.asarray(status, dtype=object)[keep][~hit]
    _write_tsv(pd.concat([left, block], axis=1), out_path)
    return keep, rename, block


def _verify_output(df_in: pd.DataFrame, out_path: Path, keep, rename) -> str | None:
    """Every input column must come back cell-for-cell identical."""
    back = _read_tsv(out_path)
    expect = df_in.loc[keep].reset_index(drop=True)
    if len(back) != len(expect):
        return f"row count {len(expect)} -> {len(back)}"
    for c in expect.columns:
        oc = rename.get(c, c)
        if oc not in back.columns:
            return f"input column {c!r} is missing"
        if not back[oc].equals(expect[c]):
            return f"input column {c!r} changed value"
    return None


def _log_coverage(U: pd.DataFrame) -> None:
    """Fill rates of the live models' universe inputs, plus the status mix."""
    n = len(U)
    if not n:
        return

    def filled(c):
        return int((U[c].astype(str).str.strip() != "").sum())

    m3 = ", ".join(f"{c} {filled(c)}" for c in _M3_UNIVERSE_FEATURES)
    log.info(f"  model-3 universe inputs filled (of {n} units): {m3}")
    extra = [c for c in _M2_UNIVERSE_FEATURES if c not in _M3_UNIVERSE_FEATURES]
    if extra:
        log.info("  model-2 extras: " + ", ".join(f"{c} {filled(c)}" for c in extra))
    st = U["msc_status"].replace("", "(blank)").value_counts()
    log.info(f"  msc_status: {dict(st)}")
    both = (U["VIX_AnchorDate"] != "") & (U["msc_anchor_date"] != "")
    agree = int((U.loc[both, "VIX_AnchorDate"] == U.loc[both, "msc_anchor_date"]).sum())
    log.info(f"  VIX_AnchorDate == msc_anchor_date on {agree}/{int(both.sum())} "
             f"rows carrying both")
    log.info(f"  RU3_InUniverse: {int((U['RU3_InUniverse'] == '1').sum())} in / "
             f"{int((U['RU3_InUniverse'] == '0').sum())} out; RU3_InLiveFile: "
             f"{dict(U['RU3_InLiveFile'].replace('', '(no file)').value_counts())}")


# ── S12: --compare-live ──────────────────────────────────────────────────────

def _same(a, b) -> bool:
    a, b = str(a).strip(), str(b).strip()
    a = "" if a.lower() in ("nan", "none") else a
    b = "" if b.lower() in ("nan", "none") else b
    if a == b:
        return True
    try:
        x, y = float(a), float(b)
    except ValueError:
        return False
    return abs(x - y) <= max(1e-9, 1e-3 * max(abs(x), abs(y)))


def _compare_live(U: pd.DataFrame, out_path: Path) -> Path | None:
    """Per-column agreement with the real stocks_universe_<Date>.tsv.

    Only rows whose own Date has a canonical snapshot that lists the symbol are
    compared -- the acceptance test of this script, and an audit of the nightly.
    Numbers agree within 1e-3 relative; text must match. The fetch-time msc_
    columns describe the run, not the market, and are skipped.
    """
    idx = {d: p for d, p in _snapshot_index().items()
           if p.name.startswith("stocks_universe_")}
    stats: dict = {}
    n_rows = 0
    for day, grp in U.groupby(_DATE_COL, sort=True):
        p = idx.get(date.fromisoformat(day))
        if p is None:
            continue
        live = _read_tsv(p)
        live.index = live["Symbol"].map(_norm)
        live = live[~live.index.duplicated()]
        cols = [c for c in _UNIVERSE_COLS
                if c in live.columns and c not in _FETCH_TIME_COLS]
        for _, row in grp.iterrows():
            s = _norm(row["Symbol"])
            if s not in live.index:
                continue
            n_rows += 1
            lrow = live.loc[s]
            for c in cols:
                r, l_ = str(row[c]).strip(), str(lrow[c]).strip()
                st = stats.setdefault(c, {"n": 0, "equal": 0, "both_blank": 0,
                                          "retro_blank": 0, "live_blank": 0,
                                          "max_rel": 0.0, "example": ""})
                st["n"] += 1
                if _same(r, l_):
                    st["equal"] += 1
                    st["both_blank"] += (r == "" and l_ == "")
                    continue
                if r == "":
                    st["retro_blank"] += 1
                elif l_ == "":
                    st["live_blank"] += 1
                else:
                    try:
                        x, y = float(r), float(l_)
                        st["max_rel"] = max(st["max_rel"],
                                            abs(x - y) / max(abs(x), abs(y), 1e-12))
                    except ValueError:
                        pass
                if not st["example"]:
                    st["example"] = f"{s} {day}: retro={r!r} live={l_!r}"
    if not n_rows:
        log.info("S12 --compare-live: no row has a snapshot for its own Date "
                 "that lists its symbol — nothing to compare.")
        return None
    rep = pd.DataFrame([
        {"column": c, "n_compared": st["n"], "n_equal": st["equal"],
         "pct_equal": round(100.0 * st["equal"] / st["n"], 2),
         "both_blank": st["both_blank"], "retro_only_blank": st["retro_blank"],
         "live_only_blank": st["live_blank"],
         "max_rel_diff": round(st["max_rel"], 6), "first_mismatch": st["example"]}
        for c, st in stats.items()])
    rep_path = out_path.with_name(f"{out_path.stem}_vs_live.tsv")
    _write_tsv(rep, rep_path)
    worst = rep[rep["pct_equal"] < 99.0].sort_values("pct_equal")
    log.info(f"S12 --compare-live: {n_rows} rows vs their real snapshot, "
             f"{len(rep)} columns; {len(rep) - len(worst)} agree on ≥99% → "
             f"{rep_path.name}")
    for r in worst.head(25).itertuples(index=False):
        log.info(f"    {r.column:<26} {r.pct_equal:6.2f}%  "
                 f"(retro-blank {r.retro_only_blank}, live-blank "
                 f"{r.live_only_blank}, max rel {r.max_rel_diff:g})  "
                 f"e.g. {r.first_mismatch}")
    return rep_path


# ── main ─────────────────────────────────────────────────────────────────────

def _parse_args(argv):
    p = argparse.ArgumentParser(
        description="Append to every PR row the daily_universe_pipeline-3 "
                    "columns exactly as stocks_universe_<ArrivalDate-1>.tsv "
                    "would have held them (point-in-time, resumable).")
    p.add_argument("-i", "--input", required=True, metavar="TSV",
                   help="PR table with Symbol + ArrivalDate (yyyy-mm-dd)")
    p.add_argument("-o", "--output", default=None, metavar="TSV",
                   help=f"default <input_stem>{_OUT_SUFFIX}.tsv beside the input; "
                        f"never inside universe_finder/data/")
    p.add_argument("--symbol-col", default="Symbol", metavar="COL")
    p.add_argument("--date-col", default="ArrivalDate", metavar="COL",
                   help="PR arrival date; the served-by nightly is this - 1 day")
    p.add_argument("--limit", type=int, default=0, metavar="N",
                   help="only the first N (Symbol, ArrivalDate) units, written "
                        "to ..._limitN.tsv with its own work dir (smoke tests)")
    p.add_argument("--fresh", action="store_true",
                   help="discard this input's work dir and start over")
    p.add_argument("--compare-live", action="store_true",
                   help="also write <output_stem>_vs_live.tsv: per-column "
                        "agreement with the real snapshot of each row's Date")

    g = p.add_argument_group("IBKR (paper Gateway)")
    g.add_argument("--host", default="127.0.0.1")
    g.add_argument("--port", type=int, default=4002,
                   help="4002 = paper GW, where bulk historical belongs")
    g.add_argument("--clientID", type=int, default=81, metavar="ID",
                   help="S2 close fetch (clear of the nightly's 10/31, clerk "
                        "1000-1029, dailyStats 77, daily-board 20000)")
    g.add_argument("--batch-size", type=int, default=200, metavar="N",
                   help="S2 symbols per batch; reconnect checkpoint")
    g.add_argument("--price-concurrency", type=int, default=12, metavar="N")
    g.add_argument("--price-timeout", type=int, default=30, metavar="S")
    g.add_argument("--retries", type=int, default=2, metavar="N",
                   help="S2 retries per symbol on an empty/timed-out request")
    g.add_argument("--split-workers", type=int, default=10, metavar="N",
                   help="yfinance threads for the S2 split-cache refresh")
    g.add_argument("--no-split-refresh", action="store_true",
                   help="skip S2's additive split-cache refresh (offline "
                        "re-runs); a split newer than a cached list then "
                        "goes uncorrected")
    g.add_argument("--msc-client-id", type=int, default=83, metavar="ID")
    g.add_argument("--msc-concurrency", type=int, default=12, metavar="N")
    g.add_argument("--msc-timeout", type=int, default=90, metavar="S",
                   help="bounds queue wait; keep >= concurrency x 5")
    g.add_argument("--msc-chunk-symbols", type=int, default=150, metavar="N",
                   help="symbols per S7 chunk (~15 min uncached)")
    g.add_argument("--freq-client-id", type=int, default=82, metavar="ID")
    g.add_argument("--freq-concurrency", type=int, default=2, metavar="N",
                   help="the nightly cron's value; >~50 Error 366 means lower it")
    g.add_argument("--freq-timeout", type=int, default=60, metavar="S")
    g.add_argument("--freq-chunk-units", type=int, default=300, metavar="N",
                   help="units per S8 chunk (~2.7 s each at concurrency 2)")

    g = p.add_argument_group("universe filters (RU3_InUniverse)")
    g.add_argument("--max-float-m", type=float, default=_p3._FILTER_MAX_FLOAT_M)
    g.add_argument("--max-mcap-m", type=float, default=_p3._FILTER_MAX_MCAP_M)
    g.add_argument("--max-price", type=float, default=_p3._FILTER_MAX_PRICE)

    g = p.add_argument_group("steps")
    g.add_argument("--no-mcap", action="store_true", help="skip S3 MarketCap_M")
    g.add_argument("--no-short-interest", action="store_true", help="skip S4")
    g.add_argument("--no-vix", action="store_true", help="skip S5")
    g.add_argument("--no-splits", action="store_true", help="skip S6")
    g.add_argument("--no-market-state", action="store_true",
                   help="skip S7 (holds 10 of model-3's 14 universe inputs)")
    g.add_argument("--no-freq", action="store_true",
                   help="skip S8/S9 (ITI and trade size, ~2.7 s per unit)")
    g.add_argument("--no-impute", action="store_true",
                   help="S9 copies the raw values into the canonical columns")

    g = p.add_argument_group("safety")
    g.add_argument("--no-yield", action="store_true",
                   help="do not wait for the nightly pipeline before IBKR work")
    g.add_argument("--allow-live-gateway", action="store_true",
                   help=f"permit ports {sorted(_LIVE_PORTS)} (real-money stack)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    log_path = _setup_logging()
    log.info("=" * 60)
    log.info("RetroUniverse-3: starting")

    if args.port in _LIVE_PORTS and not args.allow_live_gateway:
        log.error(f"Port {args.port} is {_LIVE_PORTS[args.port]}. Bulk historical "
                  f"work belongs on the paper Gateway (4002); pass "
                  f"--allow-live-gateway to override.")
        return 2
    in_path = Path(args.input).resolve()
    if not in_path.is_file():
        log.error(f"Input not found: {in_path}")
        return 2
    tag = f"_limit{args.limit}" if args.limit else ""
    out_path = _resolve_output(args, in_path, tag)

    df_in = _load_input(in_path, args.symbol_col, args.date_col)
    keys, status = _row_keys(df_in, args.symbol_col, args.date_col,
                             datetime.now(ET).date())
    W = _work_table(keys, args.limit)
    log.info(f"  input : {in_path.name} — {len(df_in)} rows, {len(df_in.columns)} "
             f"columns → {len(W)} (Symbol, ArrivalDate) units"
             f"{f' (--limit {args.limit})' if args.limit else ''}, "
             f"{W['Symbol'].map(_norm).nunique()} symbols"
             f"{f', Date {W[_DATE_COL].min()}..{W[_DATE_COL].max()}' if len(W) else ''}")
    if status.count("bad_input") or status.count("no_nightly_yet"):
        log.info(f"          {status.count('bad_input')} rows without a usable "
                 f"{args.symbol_col}/{args.date_col}, "
                 f"{status.count('no_nightly_yet')} whose nightly has not run yet")
    log.info(f"  output: {out_path}")
    log.info(f"  log   : {log_path}")
    _check_universe_header()

    problems: list[str] = []
    if len(W):
        work = _open_work_dir(in_path.stem + tag, W, args.fresh)
        w_path = work / "W.tsv"
        log.info(f"  work  : {work}")

        _step_snapshot_join(w_path)
        if not _step_close(w_path, args):
            log.error("RetroUniverse-3: stopped at S2 — nothing downstream can "
                      "run without the closes. Re-run the same command to resume.")
            return 1

        if not args.no_mcap and not _replay(
                "S3 market cap", _MCAP_SCRIPT,
                ["-i", str(w_path), "--days-prior", "1"],
                w_path, "mcapBack", _MCAP_MARKER):
            problems.append("S3 market cap")
        if not args.no_short_interest and not _replay(
                "S4 short interest", _SI_SCRIPT,
                ["-i", str(w_path), "--date-col", _DATE_COL, "--days-prior", "0",
                 "--skip-borrow-history",
                 "--log", str(work / "logs" / "si_backfill.log")],
                w_path, "SIback", _SI_MARKER):
            problems.append("S4 short interest")
        if not args.no_vix:
            if _replay("S5 vix/fng", _p3._VIX_SCRIPT,
                       ["-i", str(w_path), "--date-column", _DATE_COL,
                        "--days-prior", "0", "--fng-carry-forward", "1",
                        "--refresh"],
                       w_path, "vixfng", _VIX_MARKER):
                _check_vix_rows(w_path)
            else:
                problems.append("S5 vix/fng")
        if not args.no_splits and not _replay(
                "S6 splits", _p3._SPLITS_SCRIPT,
                ["-i", str(w_path), "--date-column", _DATE_COL,
                 "--time-column", "", "--days-prior", "0", "--eod-fallback",
                 "--never-split-fill", "blank",
                 "--split-max-age-days", str(_SPLIT_MAX_AGE), "--no-compare-msc"],
                w_path, "splits", _SPLITS_MARKER):
            problems.append("S6 splits")
        if not args.no_market_state and not _step_market_state(work, w_path, args):
            problems.append("S7 market state (incomplete)")
        if not args.no_freq:
            if not _step_freq(work, w_path, args):
                problems.append("S8 trade frequency/size (incomplete)")
            elif not _step_impute(work, w_path, args):
                problems.append("S9 imputation")
        U = _universe_frame(work, w_path, args)
    else:
        log.warning("  no usable (Symbol, ArrivalDate) unit in the input — the "
                    "output gets blank universe columns only.")
        U = pd.DataFrame(columns=["Symbol", "ArrivalDate", *_UNIVERSE_COLS,
                                  *_EXTRA_COLS])

    keep, rename, _ = _write_output(df_in, keys, status, U, out_path, args.limit)
    problem = _verify_output(df_in, out_path, keep, rename)
    if problem:
        log.error(f"Output verification FAILED: {problem}")
        problems.append("output verification")
    if rename:
        log.info(f"  {len(rename)} input column(s) collided with appended names "
                 f"and were renamed *_input (e.g. "
                 f"{', '.join(list(rename.values())[:4])})")
    log.info(f"RetroUniverse-3: wrote {out_path.name} — {int(keep.sum())} rows, "
             f"{len(df_in.columns)} input + {len(_UNIVERSE_COLS) + len(_EXTRA_COLS)} "
             f"appended columns.")
    _log_coverage(U)
    if args.compare_live and len(U):
        _compare_live(U, out_path)

    if problems:
        log.warning(f"RetroUniverse-3: finished with problems: "
                    f"{'; '.join(problems)}. Re-run the same command to resume.")
        return 1
    log.info("RetroUniverse-3: finished.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
