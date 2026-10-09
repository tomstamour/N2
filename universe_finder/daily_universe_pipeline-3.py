#!/usr/bin/env python3
"""
daily_universe_pipeline-3.py
----------------------------
XGBoost-feeding successor to daily_universe_pipeline-2.py.

v2 produced the 38-column `stocks_universe_YYYY-MM-DD.tsv` that Orchestrator5.2
reads for Float / MktCap / LastDailyClose / ITI+trade-size baselines. That file
supplies only 4 of the 29 direct structured features the shipped XGBoost models
consume (`training_data/xgb_fromPRhigh_features_conservative.txt`). v3 adds the
two blocks that close the symbol-level half of that gap:

  * the 84-column market-state block from
    `tools/market_state/market_state_calculator.py` (67 price-action features +
    status/split/basis provenance) — 10 of the 49 model features live here;
  * the 20-column VIX / CNN Fear & Greed block from
    `price_stats_loader/vix_addOn.py` — `FNG_Index` is the 49th-list member.

and drops the IBKR borrow fetch (v2's Step 3.6), which cost ~72 min a night,
contributes none of the 49 features and has no consumer anywhere in the live
chain. Its seven columns remain in the header, blank, so the schema stays
continuous with every `stocks_universe_*.tsv` ever written.

THE ANCHOR CONTRACT — why this reproduces the training set
----------------------------------------------------------
Every session-resolving step of the training-set chain
(`price_stats_loader/training-set-construction-steps.md`) ran at
`--days-prior 1` against each PR's ArrivalDate, i.e. "the last session that
closed before the PR arrived". v3 runs on the evening of day D and anchors at
`--days-prior 0`, which `tools/market_state/CLAUDE.md` states as an identity:

    training:  PR on ArrivalDate = D+1, --days-prior 1 -> resolve(D+1, 1) = D
    v3:        run on day D,            --days-prior 0 -> resolve(D,   0) = D
    "Train with 1, infer with 0."

`Sessions.resolve` (market_state_calculator.py) is the single authority for that
resolution; `vix_addOn.resolve_anchor` reimplements it line-for-line and was
verified at 0/360 mismatches. The weekend case needs no special-casing: a
Saturday anchor at `--days-prior 0` resolves to Friday.

ONE PINNED SESSION DATE. `SESSION_DATE` is resolved once at process start and
every step anchors off it; nothing calls `datetime.now()` again for a date.
v2 called it at *write* time, so a run crossing midnight wrote session-D data
into `stocks_universe_D+1.tsv` (28 of 82 snapshots are shifted by one session —
see `cache/universe_session_map.tsv`). The anchor is also carried into the file
itself as the `Date` column, and market_state_calculator's basis referee looks
up `stocks_universe_<anchor>.tsv` BY FILENAME — `msc_basis_ratio` is a model
feature, so the name and the anchor have to agree.

End-to-end pipeline:
  1. Symbol list + Float_M + MarketCap_M via NASDAQ_symbols_data.build_dataframe()
     (one yfinance call serves both; this is the same source the training set's
     `Float`/`MktCap` were taken from — no backfill, no recomputation).
  2. LastDailyClosePrice per symbol via IBKR historical data.
  3. RTH/ETH_avgITI_sec + RTH/ETH_tradeSize from one TRADES request per symbol.
  3.5 Paced second-pass retry of failed-but-fetchable rows.
  4. ITI_imputer.py in place -> 10 columns.
  6. Append RTH_tradeSize / ETH_TradeSize -> 12 columns.
  7. trade_size_imputer.py in place -> 14 columns.
  8. ShortInterrest_AddON.enrich_dataframe -> 38 columns. Called with
     `ibkr_borrow={}` (NOT None): `use_ibkr = ibkr_borrow is not None`, so None
     would re-enable the two DEAD web borrow sources (a 15 s FTP timeout plus
     ~830 iBorrowDesk calls at 0.8 s each). An empty dict means "IBKR is the
     source and it has nothing" -> blank borrow columns, zero network cost.
  8.5 Append the `Date` anchor column and the four raw ITI/trade-size sidecars
     (see below) -> 43 columns. MUST come after step 7: both imputers validate
     against a fixed 8/10-column schema and reject extra columns.
  9. market_state_calculator.py --anchor-date <D> --days-prior 0 --session both
     -> 127 columns. `--session both` is not optional: the training msc_ block
     was built with it and the three session modes are NOT comparable.
 10. vix_addOn.py --date-column Date --days-prior 0 --fng-carry-forward 1
     -> 147 columns. Market-wide; two bulk HTTP requests for the whole run.
 11. Stock-Split-finder.py (OPT-IN, `--splits`) -> +4 columns. Off by default.
     Uses `--never-split-fill blank` because the default `maxima` sentinel is
     derived from the file's OWN resolved rows, so a nightly file would emit
     `1986-11-04 / 1.0 / 168588.0` where training froze `1985-09-12 / 1.0 /
     172054.1`.

Steps 9-11 are subprocess replays of each addon's own CLI, with the flags the
training set used and only `--days-prior 1` -> 0. Each writes a
`<stem>_<suffix>.tsv` sibling which is verified column-for-column against its
input and then `os.replace`d over the canonical file. Each is skipped if its
marker column is already present, so a re-run cannot append a second copy of
every column (the duplicate-`.1` mess that cost steps 5-9 of the training chain).
Any failure logs and leaves the pre-step file intact.

RAW ITI/TRADE-SIZE SIDECARS. The training corpus carried RAW 44444 sentinels,
which the models map to frozen constants (`xgb_cons49_se_prep.json`:
RTH_avgITI_sec -> 46800.0, ETH_avgITI_sec -> 68400.0, RTH_tradeSize -> 0.0).
Steps 4 and 7 overwrite those sentinels with HGB predictions, and they must —
Orchestrator5.2 reads the canonical columns as itiBaseline / tradeSizeBaseline
for the mole trigger. `*_raw` preserves what the training pipeline saw so a
retrain can reproduce it. None of the three survived SHAP pruning into the
current 49-feature allowlist, so this matters only for retrains.

Output columns (147 by default, 151 with --splits):
  Symbol | Exchange | Float_M | MarketCap_M | Float_Source
         | LastDailyClosePrice | RTH_avgITI_sec | ETH_avgITI_sec
         | ITI_impute_flag | ITI_impute_method
         | RTH_tradeSize | ETH_TradeSize
         | TradeSize_impute_flag | TradeSize_impute_method
         | SI_* x8 | SVR_* x4 | CTB_* x3 | Borrow_* x4 | RegSHO_* x2
         | SqueezeFlags | SqueezeScore | SI_DataCoverage
         | Date | RTH_avgITI_sec_raw | ETH_avgITI_sec_raw
         | RTH_tradeSize_raw | ETH_TradeSize_raw
         | <67 market-state features> | msc_* x17
         | VIX_* x7 | FNG_* x13
         [ | LastSplit_Date | LastSplit_Factor | LastSplit_MktHours
           | LastSplit_Status ]
  Step 1 fields are NaN when no source had the value; steps 2/3 fields carry the
  44444 sentinel on a failed fetch. Market-state and VIX cells are EMPTY when
  uncomputable — deliberately not 44444, which XGBoost would split on.

KNOWN DIVERGENCES from the training-set construction (accepted, documented):
  * RegSHO_Flag / RegSHO_ConsecDays. The live SI addon walks back from
    `eastern_today()` and gets its 1-day publication lag only because Nasdaq has
    not posted yet (~23:00 ET); the backfill applies it deliberately. Neither
    column is a model feature; only SqueezeScore (10 of 100 points) is touched.
  * msc_basis_ratio sits at ~1.0 live (bars and reference close are both captured
    today), where the training column has real tails. Inherent to point-in-time
    inference.

WHAT THIS SCRIPT DOES NOT PRODUCE. The other 14 direct features plus the 20
hd_pca_*/bd_pca_* are PR-level and belong to the Orchestrator5.2 successor:
FineClass, BroadClass, TimeToOpen, DeBERTa_ss_hd.1, Prosus_ss_hd.1,
FLANG_ss_hd.1, TradeHero_ss_hd.1, FinMarBa_ind_hd.1, RoBERTa_neg_hd.1,
TradeHero_pos_bd.1, Prosus_ss_bd, Prosus_neg_bd, FinancialB_ss_bd.1,
TradeHero_ss_bd.1, and the FinBERT embedding -> PCA projection of Headline/Body.
Note O5.2 renames Float_M -> Float and MarketCap_M -> MktCap on the way through.

Usage:
    python daily_universe_pipeline-3.py [--host HOST] [--port PORT] [--clientID ID]
                             [--refresh] [--past-days-lookback N]
                             [--price-concurrency N] [--freq-concurrency N]
                             [--batch-size N]
                             [--price-timeout S] [--freq-timeout S]
                             [--freq-bar-size SIZE]
                             [--retries N] [--freq-retries N]
                             [--max-float M] [--limit N]
                             [--anchor-date YYYY-MM-DD] [--output-stem STEM]
                             [--no-market-state] [--msc-session {rth,eth,both}]
                             [--msc-concurrency N] [--msc-timeout S]
                             [--msc-client-id ID] [--msc-split-max-age N]
                             [--no-vix] [--splits]

  Rows skipped by `--max-float` keep their Step 1 fields (Float_M /
  MarketCap_M / Float_Source) but have NaN in LastDailyClosePrice and
  RTH_avgITI_sec / ETH_avgITI_sec — distinct from the 44444 sentinel used
  for symbols whose IBKR fetch was attempted but failed.

Steps 2 & 3 fetch concurrently (bounded by their respective semaphores). Step 3
reuses the qualified contracts produced by Step 2, so each freq fetch is one
round-trip instead of two. The runner reconnects automatically if the IBKR
connection drops mid-run, and a mid-batch socket disconnect (the IB Gateway
03:59 ET nightly restart) no longer aborts the pipeline — the unfetched
symbols get the 44444 sentinel and an output file is always written.
"""


import argparse
import asyncio
import fcntl
import logging
import os
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd  # used by overwrite-protection: read existing TSV to compare

_SCRIPT_DIR = Path(__file__).parent
_DATA_DIR   = _SCRIPT_DIR / "data"
_RUNS_DIR   = _SCRIPT_DIR / "runs"

# Canonical output stem. v3 replaces v2 in cron and writes the SAME filename —
# Orchestrator5.2, daily-board and the marketcap/lastclose backfill tools all
# read these files by column name, so appending columns is transparent to them.
# `--output-stem` overrides it for side-by-side validation runs.
_OUTPUT_STEM = "stocks_universe"

# Hardcoded output filters — rows failing any criterion are dropped entirely.
# Float_M / MarketCap_M applied BEFORE IBKR fetches; price applied after.
# Strict: NaN in any criterion = drop.
_FILTER_MAX_FLOAT_M = 50.0    # Float_M must be < 50 M shares
_FILTER_MAX_MCAP_M  = 50.0    # MarketCap_M must be < 50 M
_FILTER_MAX_PRICE   = 6.00    # LastDailyClosePrice must be < $6.00

# The trade-size addon module has a literal '.' in its filename
# (trade_frequency_and_size.addOn.py), so it can't be imported by name — load
# it by path. Done lazily inside main() to keep import errors local to a run.
_TRADE_SIZE_ADDON = _SCRIPT_DIR / "trade_frequency_and_size.addOn.py"

# Short-interest enrichment (Step 8) lives in a sibling directory outside this
# package, so it too is loaded by path. Its `enrich_dataframe(df, ...)` appends
# the SI_*/SVR_*/CTB_*/Borrow_*/RegSHO_*/Squeeze columns (24) to the final file.
_SHORT_INTEREST_ADDON = (
    _SCRIPT_DIR.parent / "ShortInterrest" / "ShortInterrest_AddON.py")

# Steps 9-11 replay these three addons' own CLIs as subprocesses rather than
# importing them: each stays the single authority for its own columns, a crash
# in one cannot kill the pipeline, and Stock-Split-finder's logic is trapped
# inside a main() that takes no argv anyway. Each writes a `<stem>_<suffix>.tsv`
# sibling; `_run_addon` verifies and renames it over the canonical file.
_MSC_SCRIPT    = _SCRIPT_DIR.parent / "tools" / "market_state" / "market_state_calculator.py"
_VIX_SCRIPT    = _SCRIPT_DIR.parent / "price_stats_loader" / "vix_addOn.py"
_SPLITS_SCRIPT = _SCRIPT_DIR / "Stock-Split-finder.py"

# The column whose presence proves a step already ran on this file. Without the
# guard a re-run appends a SECOND copy of every column and pandas silently
# renames it `*.1` on the next read — the duplicate-column mess that cost steps
# 5-9 of the training-set chain. Stock-Split-finder self-guards; the other two
# do not.
_MSC_MARKER    = "msc_anchor_date"
_VIX_MARKER    = "VIX_AnchorDate"
_SPLITS_MARKER = "LastSplit_Status"

# Written at Step 8.5. The canonical ITI/trade-size columns carry the imputers'
# predictions (Orchestrator5.2 needs those for itiBaseline / tradeSizeBaseline);
# these preserve the RAW pre-imputation values, including the 44444 sentinel the
# XGBoost models were trained to map to frozen constants.
# The 9 columns this file supplies that are in the LIVE model-2 / model-2FT
# 25-feature allowlist (training_data/model-2/xgb_fromPRhigh_features_*.txt —
# all 10 boosters share one list). O5.2 renames Float_M->Float and
# MarketCap_M->MktCap; the other 7 pass through under their own names. The
# superseded model-1 used 15 from here; the 6 it dropped (SVR_5dAvgPct,
# FNG_Index, realized_vol_5d, gap_abs_mean_20d, days_since_52w_low,
# illiq_trend) are still emitted, for retrains.
_M2_UNIVERSE_FEATURES = ("Float_M", "MarketCap_M", "SI_ChangePct", "atr_pct_14",
                         "float_turnover_max_20d", "msc_basis_ratio",
                         "dist_swing_high_20d_pct", "range_pct_1d",
                         "amihud_63d")

_RAW_SIDECARS = ("RTH_avgITI_sec_raw", "ETH_avgITI_sec_raw",
                 "RTH_tradeSize_raw", "ETH_TradeSize_raw")

# The anchor session, carried into the output so the file states its own
# provenance. v2 had no such column and derived the filename from
# `datetime.now()` at write time, which shifts by a day on any run that crosses
# midnight. market_state_calculator's basis referee resolves
# `stocks_universe_<anchor>.tsv` by filename, so the two must agree.
_DATE_COLUMN = "Date"

# Aggregate stdout/stderr log written by the cron line ( `>> cron.log 2>&1` ).
# Cron's shell is the sole writer, so we cannot use Python's
# RotatingFileHandler (that would race on rename). Instead, before any
# logging starts, we manually trim the file if it crosses _CRON_LOG_MAX
# bytes — moving the current contents to cron.log.1 (overwriting an older
# backup). Bounded, single-writer, race-free.
_CRON_LOG     = _RUNS_DIR / "cron.log"
_CRON_LOG_MAX = 5_000_000

log = logging.getLogger("pipeline_daily3")

# One run at a time. Without this a run stuck in Step 1 (finviz 403s,
# 2026-09-30) let the next three nightly crons start on top of it, each
# rotating cron.log under the others. The lock dies with the process.
_LOCK_FILE = _RUNS_DIR / "pipeline_daily-3.lock"
_lock_fh = None

# Phone alerts (ntfy_alert.py, the IB Gateway monitor's ntfy topic). Anything
# that leaves the output file incomplete or suspect is appended to _problems
# where it is detected; main() sends ONE "flawed" alert listing them at the
# end. Crashes and a blocked run alert on their own. A run that hangs or is
# killed outright cannot alert itself: universe_watchdog.py (06:00 ET) does.
_problems: list[str] = []
_MIN_OUTPUT_ROWS = 500   # normal nights write ~820-840 rows
_log_path: Path | None = None   # this run's per-day log, set by _setup_logging


def _alert(title: str, message: str, priority: str = "high") -> None:
    try:
        from ntfy_alert import send_alert
        send_alert(title, message, priority=priority)
    except Exception:
        pass  # an alert must never break the run


def _acquire_run_lock() -> None:
    """Take an exclusive non-blocking flock, or exit 1 if another run holds it."""
    global _lock_fh
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)
    _lock_fh = open(_LOCK_FILE, "a+")
    try:
        fcntl.flock(_lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _lock_fh.seek(0)
        holder = _lock_fh.read().strip() or "unknown"
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} pipeline_daily-3: another run "
              f"is still in progress ({holder}) — exiting without doing anything. "
              f"Check it with `ps aux | grep daily_universe_pipeline-3`.",
              file=sys.stderr, flush=True)
        _alert("Universe: nightly run blocked",
               f"daily_universe_pipeline-3 did not start: the previous run is "
               f"still going ({holder}). Check it with "
               f"`ps aux | grep daily_universe_pipeline-3`.")
        sys.exit(1)
    _lock_fh.seek(0)
    _lock_fh.truncate()
    _lock_fh.write(f"pid {os.getpid()} started {datetime.now():%Y-%m-%d %H:%M:%S}\n")
    _lock_fh.flush()


def _rotate_cron_log() -> None:
    """One-shot bounded rotation of runs/cron.log on startup.

    Cron appends stdout/stderr to runs/cron.log via shell redirection. If
    that file has grown past _CRON_LOG_MAX, move it to cron.log.1 (which
    replaces any existing backup) so the active log starts fresh. A second
    backup tier (cron.log.2) keeps the *previous* rotation around in case
    the user wants to inspect the night just before the crash burst.
    """
    try:
        if not _CRON_LOG.exists() or _CRON_LOG.stat().st_size < _CRON_LOG_MAX:
            return
        backup1 = _CRON_LOG.with_suffix(".log.1")
        backup2 = _CRON_LOG.with_suffix(".log.2")
        if backup1.exists():
            os.replace(backup1, backup2)
        os.replace(_CRON_LOG, backup1)
    except OSError:
        # Never let log housekeeping abort the pipeline itself.
        pass


def _setup_logging() -> None:
    _rotate_cron_log()

    date_str = datetime.now().strftime("%d-%b-%Y")
    log_dir  = _RUNS_DIR / date_str
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "pipeline_daily-3.log"
    global _log_path
    _log_path = log_path

    log.setLevel(logging.DEBUG)
    if not log.handlers:
        fh = logging.FileHandler(log_path)
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
        log.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.INFO)
        ch.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        log.addHandler(ch)

        # ib_insync writes its own WARNING/ERROR (pacing violations, data farm
        # transitions, timeouts) to logger "ib_insync". Pipe those into the
        # per-day file so they're co-located with our own log lines, instead of
        # leaking only to stderr → cron.log. We intentionally do NOT attach the
        # stream handler — keeps the console quiet.
        ib_log = logging.getLogger("ib_insync")
        ib_log.setLevel(logging.WARNING)
        ib_log.addHandler(fh)

        # ShortInterrest_AddON (Step 8) logs to the named logger
        # "short_interest". Route it into both the per-day file and the
        # console so the nightly log has the full SI fetch trace.
        si_log = logging.getLogger("short_interest")
        si_log.setLevel(logging.INFO)
        si_log.addHandler(fh)
        si_log.addHandler(ch)


def _batches(seq, n):
    """Yield (start_index, slice) chunks of `seq` of length `n`."""
    for i in range(0, len(seq), n):
        yield i, seq[i:i + n]


def _load_trade_size_addon():
    """Load trade_frequency_and_size.addOn.py by path (its '.' breaks import).

    Returns the module; callers use `.fetch_freq_async` / `.past_trading_days`.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "trade_frequency_and_size_addOn", _TRADE_SIZE_ADDON)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load trade-size addon at {_TRADE_SIZE_ADDON}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_short_interest_addon():
    """Load ../ShortInterrest/ShortInterrest_AddON.py by path.

    It sits outside this directory (not importable by name); callers use
    `.enrich_dataframe`. Loaded lazily inside Step 8 so a missing file or a
    missing dependency (requests) only costs that step, not the whole run.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "ShortInterrest_AddON", _SHORT_INTEREST_ADDON)
    if spec is None or spec.loader is None:
        raise ImportError(
            f"Cannot load short-interest addon at {_SHORT_INTEREST_ADDON}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_concurrent(ib, symbols, coro_factory, is_empty, nan_value, label, args):
    """Fetch results for `symbols` concurrently, batch by batch.

    Each batch is run via asyncio.gather (bounded by a semaphore inside the
    coroutines). Between batches we verify the connection and reconnect if it
    dropped; a batch processed while disconnected is re-run once after
    reconnecting.

    A mid-batch socket disconnect — the IB Gateway 03:59 ET restart signature
    — used to raise `ConnectionError` out of `ib.run(...)` and abort the
    whole pipeline. It's now caught here: the failed batch's slots are
    filled with `nan_value`, we try `ensure_connected` once, and either
    continue with the next batch or, if reconnect fails, fill every
    remaining slot with `nan_value` and return so the caller can still
    write its output file.

    `coro_factory(symbol)` -> awaitable returning that symbol's result.
    `is_empty(result)` -> bool, used only for progress accounting/logging.
    `nan_value` is what fills slots when a batch is lost to a disconnect.
    Returns a list of results aligned with `symbols`.
    """
    from closing_price_fetch_addOn import ensure_connected  # noqa: E402

    total = len(symbols)
    results: list = [nan_value] * total

    async def _gather(batch):
        return await asyncio.gather(*[coro_factory(s) for s in batch])

    for start, batch in _batches(symbols, args.batch_size):
        ensure_connected(ib, args.host, args.port, args.clientID)

        try:
            out = ib.run(_gather(batch))
        except (ConnectionError, OSError) as exc:
            log.warning(f"  {label}: connection lost mid-batch at {start} "
                        f"({exc!r}); attempting reconnect …")
            # The batch's slots are already nan_value from the initial fill;
            # leave them be. Try one reconnect; if it fails, mark the rest
            # of the universe as nan_value and return.
            if not ensure_connected(ib, args.host, args.port,
                                    args.clientID):
                log.error(f"  {label}: could not reconnect; aborting Step "
                          f"and recording {total - start - len(batch)} "
                          f"remaining symbols as missing.")
                _problems.append(f"{label}: Gateway lost, could not reconnect — "
                                 f"{total - start - len(batch)} symbols left blank")
                return results
            # Reconnect succeeded — don't retry this batch (its symbols are
            # the NaN sentinel; retrying near the 03:59 cutoff risks an
            # infinite loop). Move on to the next batch.
            log.warning(f"  {label}: reconnected; skipping retry of batch "
                        f"at {start} and continuing.")
            done = start + len(batch)
            log.debug(f"  {label}: {done}/{total}  (batch dropped: "
                      f"{len(batch)}/{len(batch)} lost to disconnect)")
            continue

        # Mass-failure guard: a mid-batch disconnect is the 44444-collapse
        # signature — reconnect and retry the whole batch once.
        if not ib.isConnected():
            log.warning(f"  {label}: connection lost during batch at "
                        f"{start}; reconnecting and retrying batch …")
            if ensure_connected(ib, args.host, args.port, args.clientID):
                try:
                    out = ib.run(_gather(batch))
                except (ConnectionError, OSError) as exc:
                    log.warning(f"  {label}: retry also failed ({exc!r}); "
                                f"keeping NaN for batch at {start}.")
                    out = [nan_value] * len(batch)

        results[start:start + len(batch)] = out
        done = start + len(batch)
        empties = sum(1 for r in out if is_empty(r))
        log.debug(f"  {label}: {done}/{total}  (batch empties: {empties}/{len(batch)})")

        # Contention canary (2026-07-06 incident): >50% of the FIRST batch
        # empty means the Gateway's historical path is starved from minute
        # one (mass 30s timeouts + Error 162, zero pacing violations, socket
        # up) — not the slot-exhaustion cascade, which builds up over time.
        if start == 0 and len(batch) >= 50 and empties > len(batch) / 2:
            _problems.append(f"{label}: {empties}/{len(batch)} of the first "
                             f"batch came back empty (Gateway starved)")
            log.error(
                f"  {label}: CONTENTION CANARY — {empties}/{len(batch)} of the "
                f"first batch came back empty. Historical queries are being "
                f"starved at the Gateway on {args.host}:{args.port} (the "
                f"2026-07-06 signature: mass timeouts from the first request, "
                f"Error 162, no pacing violations). Check what else is "
                f"connected to this Gateway (clerk/moles/daily-board on the "
                f"live GW) — the rest of this step will likely fail the same "
                f"way.")

    return results


def _session_date(override: str | None) -> date:
    """The ONE anchor session date for this run, resolved once at process start.

    Everything downstream — the output filename, the `Date` column,
    market_state_calculator's `--anchor-date`, vix_addOn's anchor, the splits
    `--as-of` — reads this and nothing re-derives it from the clock. v2 called
    `datetime.now()` at *write* time, so a run that crossed midnight labelled
    session-D data as D+1; 28 of 82 historical snapshots are shifted that way
    (`cache/universe_session_map.tsv`).

    This is a CALENDAR date, not a session: `--days-prior 0` inside each addon
    does the session resolution (`Sessions.resolve`), so a Saturday run
    correctly anchors on Friday without any special-casing here.

    The cron line exports TZ=America/New_York, so `datetime.now()` is ET.
    """
    if override:
        return datetime.strptime(override, "%Y-%m-%d").date()
    return datetime.now().date()


def _read_header(path: Path) -> list[str]:
    """Column names of a TSV, without parsing the body."""
    with open(path, encoding="utf-8") as fh:
        first = fh.readline().rstrip("\n").rstrip("\r")
    return first.split("\t") if first else []


def _verify_passthrough(before: Path, after: Path) -> str | None:
    """Confirm an addon preserved every inherited column byte-for-byte.

    Three different reader/writer pairs touch this file across steps 9-11
    (pandas in market_state_calculator, the csv module in vix_addOn, pandas
    again in Stock-Split-finder). The universe TSV has no embedded tabs or
    newlines so a clean round-trip is expected, but this is the same check the
    training-set chain applied at every hop ("312 inherited columns
    byte-identical") and it is what makes the rename at the end safe.

    Returns None when the output is good, or a reason string to log and abort
    the step on. The pre-step file is never modified, so a failure is always
    recoverable by re-running.
    """
    old = pd.read_csv(before, sep="\t", dtype=str, keep_default_na=False)
    new = pd.read_csv(after,  sep="\t", dtype=str, keep_default_na=False)

    if len(new) != len(old):
        return f"row count changed {len(old)} -> {len(new)}"

    missing = [c for c in old.columns if c not in new.columns]
    if missing:
        return f"{len(missing)} inherited columns vanished: {missing[:6]}"

    # A `.1` name that wasn't there before means pandas de-duplicated a header
    # collision on read — i.e. the addon appended over an existing block.
    dupes = [c for c in new.columns
             if c.endswith(".1") and c not in set(old.columns)]
    if dupes:
        return f"duplicate-column collision introduced: {dupes[:6]}"

    diff = [c for c in old.columns if not new[c].equals(old[c])]
    if diff:
        return f"{len(diff)} inherited columns changed value: {diff[:6]}"

    return None


def _run_addon(label: str, script: Path, argv: list[str], path: Path,
               suffix: str, marker: str) -> bool:
    """Replay one addon's CLI as a subprocess and adopt its output in place.

    `path` is the canonical file; the addon writes `<stem>_<suffix>.tsv` beside
    it. On success that sibling is verified against `path` and then
    `os.replace`d over it, so the canonical name always holds either the
    pre-step file or a fully verified post-step file — never a half-written one.

    Best-effort, exactly like steps 4/6/7/8: any failure logs and returns False,
    leaving the file on disk valid and the step re-runnable standalone.

    Skips (and reports success) when `marker` is already in the header. Neither
    market_state_calculator nor vix_addOn guards against being run twice, and a
    second pass would append a duplicate copy of every column.
    """
    if marker in _read_header(path):
        log.info(f"{label}: '{marker}' already present in {path.name} — "
                 f"already enriched, skipping.")
        return True

    sibling = path.with_name(f"{path.stem}_{suffix}.tsv")
    if sibling.exists():
        # Debris from an earlier failed attempt; adopting it blind would be
        # adopting an unverified, possibly truncated file.
        try:
            sibling.unlink()
            log.warning(f"{label}: removed stale {sibling.name} from a "
                        f"previous run.")
        except OSError as exc:
            log.error(f"{label}: cannot remove stale {sibling.name} "
                      f"({exc!r}); skipping step.")
            return False

    cmd = [sys.executable, str(script), *argv]
    log.info(f"{label}: {' '.join(cmd[1:])}")
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(script.parent), text=True,
                              capture_output=True)
    except Exception as exc:
        log.exception(f"{label}: could not start {script.name}: {exc!r}")
        return False
    elapsed = time.time() - t0

    # The addons log their own detail to their own files (msc --log, etc.);
    # mirror the tail here so the nightly log is self-contained on a failure.
    for stream, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
        for line in (stream or "").splitlines()[-40:]:
            log.debug(f"  [{label}:{name}] {line}")

    if proc.returncode != 0:
        log.error(f"{label}: {script.name} exited rc={proc.returncode} after "
                  f"{elapsed:.0f}s — {path.name} is unchanged. stderr tail:")
        for line in (proc.stderr or "").splitlines()[-15:]:
            log.error(f"    {line}")
        return False

    if not sibling.exists():
        log.error(f"{label}: {script.name} returned 0 but wrote no "
                  f"{sibling.name} — {path.name} is unchanged.")
        return False

    problem = _verify_passthrough(path, sibling)
    if problem is not None:
        log.error(f"{label}: output failed the passthrough check ({problem}). "
                  f"Keeping {path.name} and leaving {sibling.name} on disk "
                  f"for inspection.")
        return False

    n_before = len(_read_header(path))
    n_after  = len(_read_header(sibling))
    try:
        os.replace(sibling, path)
    except OSError as exc:
        log.error(f"{label}: could not move {sibling.name} into place "
                  f"({exc!r}); {path.name} is unchanged.")
        return False

    log.info(f"{label}: ok — appended {n_after - n_before} columns "
             f"({n_before} -> {n_after}) in {elapsed:.0f}s.")
    return True


def _check_vix_anchor(path: Path, date_tag: str) -> bool:
    """Confirm Step 10's VIX/FNG block landed on the same session as Step 9.

    `--refresh` removes the stale-cache cause of a slid anchor, but it cannot
    remove the other one: if CBOE has simply not published the anchor session's
    row yet, no amount of refetching produces it, and `resolve_anchor` will
    quietly fall back to the newest session it does have (`problem=None`).

    ⚠ The reference is `msc_anchor_date`, NOT the `Date` column. `Date` is the
    CALENDAR day the file was produced; `msc_anchor_date` is the SESSION that
    `Sessions.resolve(Date, 0)` landed on. On a weekday the two coincide, but a
    Saturday or Sunday run resolves back to Friday -- and so does vix_addOn, via
    its own (correct) weekend fallback. Comparing `VIX_AnchorDate` against
    `Date` therefore cried wolf on every weekend run: 2026-09-19 and 2026-09-20
    both logged a MISMATCH while the file was in fact perfectly aligned
    (`msc_anchor_date == VIX_AnchorDate == 2026-09-18` on every row). The
    equality the training corpus actually validated is
    `VIX_AnchorDate == msc_anchor_date` (4804/4804 rows), so that is what is
    checked here. With Step 9 skipped (--no-market-state) no resolved session
    exists on disk and `Date` is the only reference available.

    Warn rather than fail. `VIX_AnchorDate` records the truth per row, so a
    consumer can filter on it; blanking 20 columns over a late CBOE publish
    would destroy more than it protects. Returns True when the anchors agree.
    """
    try:
        import pandas as pd  # noqa: E402
        want = ["VIX_AnchorDate"]
        if _MSC_MARKER in _read_header(path):
            want.append(_MSC_MARKER)
        got = pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False,
                          usecols=want)
    except Exception as exc:
        log.warning(f"Step 10 — could not read VIX_AnchorDate back to verify "
                    f"the anchor ({exc!r}).")
        return False

    vix = sorted(set(got["VIX_AnchorDate"]) - {""})
    if _MSC_MARKER in got.columns:
        ref, ref_name = sorted(set(got[_MSC_MARKER]) - {""}), _MSC_MARKER
    else:
        ref, ref_name = [date_tag], f"Date (no {_MSC_MARKER} on disk)"

    if len(ref) == 1 and vix == ref:
        note = "" if ref[0] == date_tag else (
            f" — run date {date_tag} is not a session, and both steps resolved "
            f"back to the same preceding one, which is correct")
        log.info(f"  VIX anchor check: VIX_AnchorDate == {ref_name} == "
                 f"{ref[0]} on all {len(got)} rows{note}.")
        return True

    log.warning(
        f"  ⚠ VIX ANCHOR MISMATCH: {ref_name} = {ref or 'blank'} but "
        f"VIX_AnchorDate = {vix or 'blank'} (run date {date_tag}). CBOE had no "
        f"row for the resolved session even after --refresh, so vix_addOn fell "
        f"back to the newest session it had. The 20 VIX_/FNG_ columns are one "
        f"session behind the market-state block in this file; FNG_Index is a "
        f"feature of the 49-column model-1 allowlist (model-2/2FT do not use "
        f"it). VIX_AnchorDate/FNG_AsOf record the truth per row, so filter on "
        f"them; re-run Step 10 alone once CBOE publishes to repair it.")
    return False


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch NASDAQ symbol list with floats, IBKR close prices, and trade frequency."
    )
    parser.add_argument("--host",     default="127.0.0.1", metavar="HOST")
    parser.add_argument("--port",     type=int, default=4001, metavar="PORT")
    parser.add_argument("--clientID", type=int, default=10,   metavar="ID")
    parser.add_argument("--refresh",  action="store_true",
                        help="Force fresh symbol/float fetch (ignore 24h cache)")
    parser.add_argument("--past-days-lookback", type=int, default=1, metavar="N",
                        help="Complete trading sessions to average for Step 3 "
                             "ITI/trade-size (default 1 = last complete session "
                             "only; 'today' counts once past 20:00 ET)")
    parser.add_argument("--price-concurrency", type=int, default=12, metavar="N",
                        help="Max in-flight IBKR requests for Step 2 (close prices)")
    parser.add_argument("--freq-concurrency", type=int, default=4, metavar="N",
                        help="Max in-flight IBKR requests for Step 3 (trade frequency). "
                             "Sits below IBKR's 50-simultaneous-historical cap and "
                             "ib_insync's 45 req/s client throttle. History: 48 → 24 "
                             "→ 8 → 4. 48 collapsed instantly (Step 3 went 0/7000); 24 "
                             "bled slowly (2026-06-01 run: 9%% success, 10k+ Error-162 "
                             "+ 1k+ Error-366); 8 looked clean on the 2026-06-02 "
                             "500-symbol test (~74%%, 7 Error-366) but the cascade "
                             "crept back at full scale — 2026-06-02 cron logged 880 "
                             "Error-162 + 58 Error-366, RTH 43%% / ETH 40%%. 4 is "
                             "the next step down per the CLAUDE.md diagnostic ladder "
                             "(>~50 Error-366 ⇒ lower concurrency). Bump higher only "
                             "after a sustained full run with 0 Error-366.")
    # Back-compat: --concurrency still works and seeds both knobs when used.
    parser.add_argument("--concurrency", type=int, default=None, metavar="N",
                        help=argparse.SUPPRESS)
    parser.add_argument("--batch-size", type=int, default=200, metavar="N",
                        help="Symbols per batch; reconnect checkpoint granularity")
    parser.add_argument("--price-timeout", type=int, default=15, metavar="S",
                        help="Per-request timeout for close-price fetch (Step 2)")
    parser.add_argument("--freq-timeout", type=int, default=30, metavar="S",
                        help="Per-request timeout for frequency fetch (Step 3). "
                             "History: 30 → 12 → 30. The 12s trim assumed 'real "
                             "fetches complete in 5–7s under load' — true only "
                             "while --freq-concurrency was high (and queue buildup "
                             "made anything slower a lost cause anyway). With "
                             "concurrency=8 the queue is no longer the bottleneck; "
                             "per-symbol latency on thin micro-caps dominates and "
                             "legitimately spans 15–25s, so 12s mass-timed-out "
                             "real symbols (2026-06-02 test: 28%% success at 12s "
                             "vs 74%% at 30s, same concurrency).")
    parser.add_argument("--freq-bar-size", default="30 mins", metavar="SIZE",
                        help="Bar size for the frequency fetch (Step 3)")
    parser.add_argument("--retries", type=int, default=2, metavar="N",
                        help="Per-request retries on empty/timeout before NaN (Step 2)")
    parser.add_argument("--freq-retries", type=int, default=0, metavar="N",
                        help="Per-request retries for Step 3; default 0 because "
                             "NaN there almost always means 'no TRADES history' "
                             "and retrying just wastes in-flight slots")
    parser.add_argument("--max-float", type=float, default=200.0, metavar="M",
                        help="Skip IBKR fetch (Steps 2 & 3) for symbols with "
                             "Float_M >= M (default 200). Skipped rows keep "
                             "Float_M / MarketCap_M / Float_Source but get NaN "
                             "in LastDailyClosePrice and RTH/ETH_avgITI_sec — "
                             "distinct from the 44444 sentinel used for actual "
                             "fetch failures. Symbols with unknown Float_M "
                             "(NaN from Step 1) pass the filter and are "
                             "fetched. To effectively disable, pass a value "
                             "larger than any plausible float (e.g. 1e9).")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="Process only the first N symbols (0 = all; for "
                             "testing). When N > 0, output is written to a "
                             "separate ..._limitN.tsv file so the test run "
                             "doesn't overwrite the nightly cron output for "
                             "the same date.")
    parser.add_argument("--no-impute", action="store_true",
                        help="Skip the ITI imputation step. By default, once "
                             "the canonical TSV is on disk, ITI_imputer.py "
                             "runs to produce a sibling ..._imputed.tsv with "
                             "model-predicted values for the 44444 sentinel "
                             "rows. Useful for fetch-only debugging or when "
                             "scikit-learn is unavailable.")
    parser.add_argument("--no-size-impute", action="store_true",
                        help="Skip the trade-size imputation step (Step 7). By "
                             "default, after the trade-size columns are appended, "
                             "trade_size_imputer.py runs in place to predict the "
                             "44444-sentinel RTH_tradeSize/ETH_TradeSize rows and "
                             "append 2 provenance sidecars. Independent of "
                             "--no-impute.")
    parser.add_argument("--no-short-interest", action="store_true",
                        help="Skip the short-interest enrichment step (Step 8). "
                             "By default ShortInterrest_AddON.enrich_dataframe "
                             "appends the 24 SI_*/SVR_*/CTB_*/Borrow_*/RegSHO_*/"
                             "Squeeze columns in place from FINRA + Nasdaq bulk "
                             "downloads (no IBKR connection, ~8 s). The seven "
                             "borrow columns are emitted BLANK: v3 dropped v2's "
                             "IBKR borrow fetch and both legacy web sources are "
                             "dead. SI_ChangePct and SVR_5dAvgPct — the two SI "
                             "columns in the 49-feature allowlist — are "
                             "unaffected. Best-effort: a failure leaves the "
                             "14-column file intact.")
    # ── v3: anchor, output identity, and the three new addon steps ──────────
    parser.add_argument("--anchor-date", default=None, metavar="YYYY-MM-DD",
                        help="Override the pinned session date (default: "
                             "today in ET). Everything anchors off this single "
                             "value — output filename, the Date column, "
                             "market-state --anchor-date, vix_addOn's anchor, "
                             "the splits --as-of. Each addon then applies "
                             "--days-prior 0, i.e. 'the last XNYS session on or "
                             "before this date', so a weekend value correctly "
                             "resolves back to Friday. Use it to replay a "
                             "previous night; note the IBKR bars and the "
                             "FINRA/CBOE/CNN feeds are still fetched as of NOW, "
                             "so a replay is not a true point-in-time rebuild.")
    parser.add_argument("--output-stem", default=_OUTPUT_STEM, metavar="STEM",
                        help=f"Output filename stem (default {_OUTPUT_STEM}). "
                             f"Change it to run v3 beside v2 without either "
                             f"clobbering the other; leave it alone once v3 "
                             f"owns the cron slot, because Orchestrator5.2 "
                             f"globs 'stocks_universe_????-??-??.tsv'.")
    parser.add_argument("--no-market-state", action="store_true",
                        help="Skip Step 9. The 84-column market-state block "
                             "carries 10 of the 49 XGBoost features "
                             "(atr_pct_14, float_turnover_max_20d, "
                             "msc_basis_ratio, dist_swing_high_20d_pct, "
                             "range_pct_1d, amihud_63d, realized_vol_5d, "
                             "gap_abs_mean_20d, days_since_52w_low, "
                             "illiq_trend), so skipping it leaves the model "
                             "unservable — for fetch-only debugging.")
    parser.add_argument("--msc-session", default="both",
                        choices=("rth", "eth", "both"), metavar="MODE",
                        help="Step 9 --session (default both). NOT a free "
                             "choice: the training msc_ block was built with "
                             "'both' and the three modes are not comparable "
                             "(RTH discards premarket volume and range, which "
                             "is exactly when a PR lands). 'both' costs 2 IBKR "
                             "requests per symbol instead of 1.")
    parser.add_argument("--msc-concurrency", type=int, default=12, metavar="N",
                        help="Step 9 --concurrency (default 12). Throughput "
                             "plateaus at 12-16 — the Gateway is the "
                             "bottleneck, not the client.")
    parser.add_argument("--msc-timeout", type=int, default=90, metavar="S",
                        help="Step 9 --timeout (default 90). Bounds QUEUE "
                             "WAIT, not service time: raising --msc-concurrency "
                             "without raising this converts speed into "
                             "msc_status=failure (25 s at concurrency 8 failed "
                             "24-37%%; 90 s at 12 failed 0/95). Keep it at or "
                             "above concurrency x 5.")
    parser.add_argument("--msc-port", type=int, default=None, metavar="PORT",
                        help="Gateway port for Step 9 (default: same as "
                             "--port). Bulk historical belongs on the idle "
                             "paper GW 4002; the live GW starves it.")
    parser.add_argument("--msc-client-id", type=int, default=31, metavar="ID",
                        help="Step 9 --client-id (default 31, market_state's "
                             "own reserved slot). Clear of clerk 1000-1029, "
                             "dailyStats 77, daily-board 20000 and this "
                             "pipeline's own --clientID.")
    parser.add_argument("--msc-split-max-age", type=int, default=36500,
                        metavar="N",
                        help="Step 9 --split-cache-max-age-days (default "
                             "36500 = fetch-if-absent, never re-query). The "
                             "addon's own default of 1 would re-query ~830 "
                             "symbols from yfinance EVERY night, and for a "
                             "DELISTED symbol yfinance returns empty splits "
                             "while still returning metadata, so fetch_splits "
                             "writes a header-only 'never split' file over real "
                             "history (NTZ and OMH were destroyed this way on "
                             "2026-09-07; the dir is gitignored, there is no "
                             "restore). The 1-day TTL exists for the historical "
                             "backfill, where a wrong split factor corrupts old "
                             "anchors. Here forward_split_factor is a no-op — "
                             "no split can post-date today — so the TTL buys "
                             "nothing and only risks the cache.")
    parser.add_argument("--no-vix", action="store_true",
                        help="Skip Step 10. FNG_Index is one of the 49 XGBoost "
                             "features. The block is market-wide and costs two "
                             "bulk HTTP requests for the entire run, so there "
                             "is rarely a reason to skip it.")
    parser.add_argument("--splits", action="store_true",
                        help="Run Step 11, Stock-Split-finder (off by "
                             "default). Adds LastSplit_Date/Factor/MktHours/"
                             "Status, ~30 s of yfinance. None are in the 49; "
                             "only LastSplit_Factor survived the training-file "
                             "correlation prune. Runs with --never-split-fill "
                             "blank because the default 'maxima' sentinel is "
                             "derived from the file's OWN resolved rows, so a "
                             "nightly file would emit a different sentinel than "
                             "the one frozen into the training corpus. "
                             "LastSplit_MktHours here counts to 20:00 ET on the "
                             "anchor date; the O5.2 successor should recompute "
                             "it from LastSplit_Date and the real PR instant.")
    args = parser.parse_args()

    # Legacy --concurrency seeds whichever per-step knob the user didn't set.
    if args.concurrency is not None:
        args.price_concurrency = args.concurrency
        args.freq_concurrency  = args.concurrency

    _acquire_run_lock()   # before _setup_logging: it rotates cron.log
    _setup_logging()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    # THE anchor. Resolved once, here, and never re-derived from the clock —
    # see _session_date() for why. Steps 8.5/9/10/11 and the output filename
    # all read this.
    session_date = _session_date(args.anchor_date)
    date_tag = session_date.strftime("%Y-%m-%d")

    log.info("=" * 60)
    log.info("pipeline_daily-3: starting")
    log.info(f"  anchor session date: {date_tag}"
             f"{' (--anchor-date override)' if args.anchor_date else ''} — "
             f"every step runs at --days-prior 0 against it")
    log.info(f"  market-state: {'OFF' if args.no_market_state else args.msc_session}"
             f"   vix/fng: {'OFF' if args.no_vix else 'on'}"
             f"   splits: {'on' if args.splits else 'off'}"
             f"   borrow: dropped in v3")

    # ── Step 1/3: symbol list + float data ───────────────────────────────────
    log.info("Step 1/3 — fetching symbol list and float data …")
    from NASDAQ_symbols_data import build_dataframe          # noqa: E402
    df = build_dataframe(force_refresh=args.refresh)
    log.info(f"  {len(df)} symbols loaded")

    # Drop warrants: 5-character symbols ending in 'W' (e.g. ATIIW, ASPSW).
    warrant_mask = df["Symbol"].str.len().eq(5) & df["Symbol"].str.endswith("W")
    n_warrants = warrant_mask.sum()
    if n_warrants:
        df = df[~warrant_mask].copy()
        log.info(f"  Warrant filter: dropped {n_warrants} symbols (5-char ending in W)")

    if args.limit and args.limit > 0:
        df = df.head(args.limit).copy()
        log.info(f"  --limit {args.limit}: processing first {len(df)} symbols only")

    # ── Early filter: Float_M and MarketCap_M ─────────────────────────────────
    # Drop rows that fail the fundamentals thresholds (or have missing values —
    # strict policy). Applied before IBKR fetches to avoid wasting request slots.
    n_pre_fund = len(df)
    df = df[
        df["Float_M"].notna()     & (df["Float_M"]     < _FILTER_MAX_FLOAT_M) &
        df["MarketCap_M"].notna() & (df["MarketCap_M"] < _FILTER_MAX_MCAP_M)
    ].copy()
    log.info(
        f"  Fundamentals filter: dropped {n_pre_fund - len(df)} rows "
        f"(Float_M >= {_FILTER_MAX_FLOAT_M} or MarketCap_M >= {_FILTER_MAX_MCAP_M} "
        f"or either missing), {len(df)} remain"
    )

    symbols = df["Symbol"].tolist()
    total   = len(symbols)

    # Float filter: symbols whose Float_M is known and >= --max-float skip
    # Steps 2 & 3 entirely (they'll get NaN in all three IBKR columns).
    # NaN Float_M is permissive — matches universe_finder.py's policy of
    # "missing data passes the filter".
    floats = df["Float_M"].tolist()
    skipped_by_float: set[str] = {
        s for s, f in zip(symbols, floats)
        if f == f and f >= args.max_float          # `f == f` drops NaN
    }
    log.info(
        f"  Float filter: max_float={args.max_float} → "
        f"skipping {len(skipped_by_float)}/{total} symbols, "
        f"fetching {total - len(skipped_by_float)}"
    )

    # Pre-initialise every result slot to its NaN sentinel so that *any* exit
    # path — clean finish, mid-run disconnect, even an unexpected exception
    # leaking out of _run_concurrent — still produces a complete output file.
    nan = float("nan")
    prices: list           = [nan] * total
    qualified: list        = [None] * total
    rth_results: list      = [nan] * total
    eth_results: list      = [nan] * total
    rth_size_results: list = [nan] * total
    eth_size_results: list = [nan] * total

    # ── Steps 2+3: single IBKR connection for both fetches ───────────────────
    log.info(f"Step 2/3 — fetching IBKR close prices from {args.host}:{args.port} "
             f"(concurrency={args.price_concurrency}, batch={args.batch_size}) …")
    from closing_price_fetch_addOn import connect, fetch_last_rth_close_async  # noqa: E402
    _tfs = _load_trade_size_addon()
    fetch_freq_async  = _tfs.fetch_freq_async
    past_trading_days = _tfs.past_trading_days

    ib = connect(args.host, args.port, args.clientID)
    try:
        # ── Step 2/3: close prices ────────────────────────────────────────────
        price_sem = asyncio.Semaphore(args.price_concurrency)

        async def _price_one(symbol):
            # Symbols filtered out by --max-float short-circuit to
            # (NaN, None). Returning None for the contract means Step 3's
            # existing `if contract is None` guard also short-circuits them
            # to (NaN, NaN) — no separate skip check needed below.
            if symbol in skipped_by_float:
                return (nan, None)
            return await fetch_last_rth_close_async(
                ib, str(symbol), price_sem, args.price_timeout, args.retries)

        price_pairs = _run_concurrent(
            ib, symbols,
            coro_factory=_price_one,
            is_empty=lambda r: r[0] != r[0],  # price is NaN
            nan_value=(nan, None),
            label="price fetch",
            args=args,
        )
        prices    = [p for p, _ in price_pairs]
        qualified = [c for _, c in price_pairs]

        # ── Step 3/3: trade frequency (RTH/ETH avg ITI) ───────────────────────
        log.info(f"Step 3/3 — fetching trade frequency (RTH/ETH avg ITI, "
                 f"bar='{args.freq_bar_size}', concurrency={args.freq_concurrency}) …")
        days = past_trading_days(args.past_days_lookback)
        log.info(f"  Lookback: {args.past_days_lookback} days  ({days[-1]} → {days[0]})")
        freq_sem = asyncio.Semaphore(args.freq_concurrency)

        async def _freq_one(symbol, contract):
            # Symbols that failed to qualify in Step 2 won't qualify here
            # either — skip the round-trip and short-circuit to NaN.
            if contract is None:
                return (nan, nan, nan, nan)
            return await fetch_freq_async(
                ib, str(symbol), days, freq_sem,
                args.freq_timeout, args.freq_retries,
                args.freq_bar_size,
                contract=contract,
            )

        # _run_concurrent iterates `symbols`; we pair each symbol with its
        # qualified contract via a parallel index lookup.
        sym_to_contract = dict(zip(symbols, qualified))
        # fetch_freq_async returns a 4-tuple (rth_iti, eth_iti, rth_size, eth_size).
        freq = _run_concurrent(
            ib, symbols,
            coro_factory=lambda s: _freq_one(s, sym_to_contract.get(s)),
            is_empty=lambda r: r[0] != r[0] and r[1] != r[1],  # both ITIs NaN
            nan_value=(nan, nan, nan, nan),
            label="freq fetch",
            args=args,
        )
        rth_results      = [r[0] for r in freq]
        eth_results      = [r[1] for r in freq]
        rth_size_results = [r[2] for r in freq]
        eth_size_results = [r[3] for r in freq]

        # ── Step 3.5: paced second-pass retry of failed-but-fetchable rows ────
        # The Error 162/366 cascade is Gateway-side slot accounting drift, not
        # a pacing or socket issue (the connection stays up; _run_concurrent's
        # reconnect path never fires here). Inline retries via --freq-retries
        # fail for the same reason the first attempt did. A *separated*,
        # time-paced retry at lower concurrency is qualitatively different:
        # the 90-s pause lets slot bookkeeping drain, and halving concurrency
        # (cap 2) keeps the retry pass from re-creating the same condition.
        # Excludes symbols whose contract didn't qualify in Step 2 (no point
        # retrying — they short-circuited to NaN structurally, not from slot
        # exhaustion). Bails if we're inside the 03:59 ET Gateway-restart
        # window so we don't start a retry pass that will get cut.
        retry_idx = [i for i, c in enumerate(qualified)
                     if c is not None and rth_results[i] != rth_results[i]]
        attempted = sum(1 for c in qualified if c is not None)
        cutoff_ok = True
        now_et = datetime.now()
        if now_et.hour == 3 and now_et.minute >= 39:
            cutoff_ok = False
        if 0 < len(retry_idx) and attempted > 0 \
                and len(retry_idx) <= attempted * 0.5 and cutoff_ok:
            retry_conc = max(1, min(2, args.freq_concurrency // 2))
            log.info(f"Step 3.5/3 — retry pass: {len(retry_idx)} of "
                     f"{attempted} attempted symbols failed; sleeping 90 s "
                     f"to let Gateway slot accounting drain, then retrying "
                     f"with concurrency={retry_conc} (cap 2).")
            time.sleep(90)
            retry_syms = [symbols[i] for i in retry_idx]
            retry_sem  = asyncio.Semaphore(retry_conc)

            async def _freq_retry(symbol):
                contract = sym_to_contract.get(symbol)
                if contract is None:
                    return (nan, nan, nan, nan)
                return await fetch_freq_async(
                    ib, str(symbol), days, retry_sem,
                    args.freq_timeout, args.freq_retries,
                    args.freq_bar_size,
                    contract=contract,
                )

            # Reuse _run_concurrent — same reconnect/disconnect semantics and
            # progress logging, on a smaller cohort.
            retry_out = _run_concurrent(
                ib, retry_syms,
                coro_factory=_freq_retry,
                is_empty=lambda r: r[0] != r[0] and r[1] != r[1],
                nan_value=(nan, nan, nan, nan),
                label="freq retry",
                args=args,
            )
            recovered_rth = recovered_eth = 0
            for j, idx in enumerate(retry_idx):
                r, e, rs, es = retry_out[j]
                if r == r:  # not NaN
                    rth_results[idx] = r
                    recovered_rth += 1
                if e == e:
                    eth_results[idx] = e
                    recovered_eth += 1
                # Trade size rides on the same fetch — recover it whenever the
                # retry produced a value, independent of the ITI recovery.
                if rs == rs:
                    rth_size_results[idx] = rs
                if es == es:
                    eth_size_results[idx] = es
            log.info(f"  freq retry: recovered RTH {recovered_rth}/"
                     f"{len(retry_idx)}  ETH {recovered_eth}/{len(retry_idx)}")
        elif len(retry_idx) > attempted * 0.5:
            log.warning(f"Step 3.5/3 — skipping retry: {len(retry_idx)}/"
                        f"{attempted} attempted symbols failed (>50%%); "
                        f"a paced retry won't help that magnitude — check "
                        f"Gateway state instead.")
        elif not cutoff_ok:
            log.warning(f"Step 3.5/3 — skipping retry: too close to 03:59 ET "
                        f"Gateway restart cutoff (now {now_et:%H:%M}).")

    except Exception as exc:
        # Belt-and-braces: anything that leaks out of Step 2/3 should still
        # let us write a (mostly-NaN) output file rather than lose
        # everything fetched so far.
        log.exception(f"Unexpected error during IBKR fetch steps: {exc!r}")
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass
        log.info("Disconnected from IBKR.")

    # ── Assemble + save output (always runs, even after a crash above) ───────
    df["LastDailyClosePrice"] = prices
    df["RTH_avgITI_sec"] = rth_results
    df["ETH_avgITI_sec"] = eth_results

    # Fetch failures get the 44444 sentinel; rows skipped by --max-float
    # keep NaN so downstream can tell "didn't try" from "tried and failed".
    # LastDailyClosePrice has never had a fillna step, so skipped rows
    # naturally stay NaN there too.
    attempted_mask = ~df["Symbol"].isin(skipped_by_float)
    df.loc[attempted_mask & df["RTH_avgITI_sec"].isna(), "RTH_avgITI_sec"] = 44444
    df.loc[attempted_mask & df["ETH_avgITI_sec"].isna(), "ETH_avgITI_sec"] = 44444

    # Trade-size columns are NOT added to `df` here: the file written below must
    # keep the exact 8-column schema ITI_imputer validates against. Instead we
    # stash the per-symbol values (same sentinel policy as the ITI columns —
    # attempted-but-failed → 44444, --max-float-skipped → NaN) and append the
    # two columns to the file in Step 6, after the imputer has run.
    attempted_set = set(df.loc[attempted_mask, "Symbol"].astype(str))
    size_by_symbol: dict = {}
    for sym, rs, es in zip(symbols, rth_size_results, eth_size_results):
        if str(sym) in attempted_set:
            rs = 44444 if rs != rs else rs   # `!=` self is True only for NaN
            es = 44444 if es != es else es
        size_by_symbol[str(sym)] = (rs, es)

    # Same idea for the ITI pair, and for the same reason the trade-size values
    # are stashed: Step 4's imputer overwrites the canonical columns in place,
    # and Orchestrator5.2 needs those imputed values (itiBaseline drives the
    # mole trigger). The XGBoost models, however, were trained on the RAW 44444
    # sentinel and map it to a frozen constant. Keyed by symbol so the price
    # filter below can drop rows without disturbing the mapping.
    raw_iti_by_symbol: dict = {
        str(s): (r, e) for s, r, e in
        zip(df["Symbol"], df["RTH_avgITI_sec"], df["ETH_avgITI_sec"])
    }

    # ── Price filter: drop rows failing LastDailyClosePrice threshold ─────────
    # NaN price (failed IBKR fetch or --max-float-skipped row) also fails the
    # strict filter, so those rows are removed too.
    n_pre_price = len(df)
    df = df[
        df["LastDailyClosePrice"].notna() &
        (df["LastDailyClosePrice"] < _FILTER_MAX_PRICE)
    ].copy()
    log.info(
        f"  Price filter: dropped {n_pre_price - len(df)} rows "
        f"(LastDailyClosePrice >= {_FILTER_MAX_PRICE} or missing), {len(df)} remain"
    )

    # Suffix manual `--limit` runs so a 500-symbol test doesn't silently
    # overwrite that same date's cron output (the cron passes no --limit, so
    # its filename is unchanged). Bare `--limit 0` / unset stays bare.
    #
    # `date_tag` comes from the pinned session date, NOT from datetime.now().
    # v2 re-read the clock here, so a run that started at 20:30 and finished
    # after midnight wrote session-D data into `..._D+1.tsv` — and
    # market_state_calculator's basis referee resolves
    # `stocks_universe_<anchor>.tsv` by filename, so a shifted name silently
    # costs the `msc_basis_ratio` model feature its reference price.
    stem        = args.output_stem
    limit_tag   = f"_limit{args.limit}" if args.limit and args.limit > 0 else ""
    output_path = _DATA_DIR / f"{stem}_{date_tag}{limit_tag}.tsv"

    # Post-filter counts used by overwrite-protection (compares what's actually
    # written, not the raw fetch universe).
    valid_rth = int(
        (df["RTH_avgITI_sec"].notna() & (df["RTH_avgITI_sec"] != 44444)).sum()
    )
    valid_eth = int(
        (df["ETH_avgITI_sec"].notna() & (df["ETH_avgITI_sec"] != 44444)).sum()
    )

    # Overwrite protection — only for the canonical (non --limit) filename.
    # A cron starting at 20:30 ET finishes after midnight and writes
    # `*_dayN+1.tsv`; an identically-named manual re-run later on dayN+1 used
    # to silently clobber it. We compare SUCCESS FRACTIONS (valid / total rows)
    # rather than absolute counts so that a filtered run (small universe, high
    # success rate) correctly supersedes an unfiltered run (large universe, low
    # success rate). A worse run goes to a timestamped sidecar instead.
    # `--limit` test runs already get their own filename and skip this path.
    new_total = max(1, len(df))
    superseded_path = None
    if not limit_tag and output_path.exists():
        try:
            existing = pd.read_csv(output_path, sep="\t")
            existing_total = max(1, len(existing))
            if "ITI_impute_flag" in existing.columns:
                # The existing file is post-imputation: its 44444 sentinels
                # were already filled with predictions, so a raw value/44444
                # count scores it as a perfect N/N and unfairly demotes every
                # nightly cron whose new (pre-impute) df still carries un-fetched
                # ETH sentinels. Recover the ACTUAL fetched counts from the
                # cohort flag so we compare like-for-like (fetched-vs-fetched).
                #   ok  -> both fetched; rth -> RTH imputed; eth -> ETH imputed;
                #   both -> neither fetched; skipped_nan -> never attempted.
                flag = existing["ITI_impute_flag"].astype(str)
                existing_rth = int(flag.isin(["ok", "eth"]).sum())
                existing_eth = int(flag.isin(["ok", "rth"]).sum())
            else:
                existing_rth = int(
                    existing["RTH_avgITI_sec"].notna().sum()
                    - (existing["RTH_avgITI_sec"] == 44444).sum())
                existing_eth = int(
                    existing["ETH_avgITI_sec"].notna().sum()
                    - (existing["ETH_avgITI_sec"] == 44444).sum())
        except Exception as exc:
            log.warning(f"Could not read existing {output_path.name} to "
                        f"compare ({exc!r}); proceeding to overwrite.")
            existing_rth = existing_eth = -1
            existing_total = 1

        if (valid_rth / new_total >= existing_rth / existing_total and
                valid_eth / new_total >= existing_eth / existing_total):
            # New run is at least as good (by fraction) in both — promote it.
            # Stash the outgoing file for one safety cycle so a bad replacement
            # is recoverable until tomorrow.
            ts = datetime.now().strftime("%H%M")
            superseded_path = _DATA_DIR / (
                f"{stem}_{date_tag}_{ts}_superseded.tsv")
            try:
                os.replace(output_path, superseded_path)
                log.info(f"Replacing existing {output_path.name} "
                         f"(old RTH={existing_rth}/{existing_total}, "
                         f"ETH={existing_eth}/{existing_total}; new "
                         f"RTH={valid_rth}/{new_total}, "
                         f"ETH={valid_eth}/{new_total}). Old file "
                         f"moved to {superseded_path.name}.")
            except OSError as exc:
                log.warning(f"Could not stash existing file ({exc!r}); "
                            f"overwriting in place.")
                superseded_path = None
        else:
            # Existing file has a better success fraction in at least one
            # column → keep it, write the new run to a sidecar.
            ts = datetime.now().strftime("%H%M")
            sidecar = _DATA_DIR / (
                f"{stem}_{date_tag}_{ts}.tsv")
            log.warning(
                f"PROTECTING existing {output_path.name} "
                f"(RTH={existing_rth}/{existing_total}, "
                f"ETH={existing_eth}/{existing_total}) — new run is "
                f"weaker (RTH={valid_rth}/{new_total}, "
                f"ETH={valid_eth}/{new_total}). Writing "
                f"this run to {sidecar.name} instead.")
            output_path = sidecar

    df.to_csv(output_path, sep="\t", index=False, float_format="%.4f")

    succeeded = sum(1 for p in prices if p == p)  # NaN check
    failed    = len(prices) - succeeded
    valid_rth_size = sum(1 for v in rth_size_results if v == v)
    valid_eth_size = sum(1 for v in eth_size_results if v == v)
    # valid_rth / valid_eth were computed above for overwrite protection
    log.info(
        f"Done. prices={succeeded}/{total}  "
        f"RTH_ITI={valid_rth}/{total}  ETH_ITI={valid_eth}/{total}  "
        f"RTH_size={valid_rth_size}/{total}  ETH_size={valid_eth_size}/{total}  "
        f"failed_prices={failed}  "
        f"skipped_by_float={len(skipped_by_float)}"
    )
    log.info(f"Output: {output_path}")

    # ── Step 4 — in-place imputation of missing-ITI rows ───────────────────
    # Once the 8-column canonical file is on disk, run ITI_imputer.py to
    # overwrite it in-place with the 10-column imputed version (2 sidecar
    # columns added; canonical RTH/ETH cols receive the predictions). The
    # imputer validates the input against its fixed 8-column ORIG_COLS schema,
    # so the trade-size columns MUST NOT be present yet — they are appended in
    # Step 6 below. ITI_imputer's write is atomic (temp + os.replace), so a
    # crash mid-write cannot corrupt the file. Imports are deferred (sklearn is
    # heavy) so `--no-impute` and pre-impute failures stay zero-cost.
    #
    # Runs on whatever `output_path` was actually written, including:
    #   - canonical `..._sized_YYYY-MM-DD.tsv`
    #   - overwrite-protected `..._sized_YYYY-MM-DD_HHMM.tsv` sidecar
    #   - manual `..._sized_YYYY-MM-DD_limitN.tsv` test runs (ITI_imputer's
    #     min-train-rows gate handles too-small N gracefully)
    if args.no_impute:
        log.info("--no-impute: skipping ITI_imputer step.")
    else:
        log.info(f"Step 4 — imputing missing-ITI rows in place via "
                 f"ITI_imputer.py ({output_path.name}) …")
        try:
            from ITI_imputer import main as _iti_main  # noqa: E402
            rc = _iti_main(["--input", str(output_path)])
            if rc == 0:
                log.info("  ITI_imputer: ok (10-col file written in place).")
            else:
                log.warning(f"  ITI_imputer: returned rc={rc} (likely too few "
                            f"complete rows for training — check "
                            f"ITI_imputer.log for the cohort breakdown).")
        except Exception as exc:
            log.exception(f"ITI_imputer failed: {exc!r} — the file on disk is "
                          f"unaffected; imputation can be re-run manually.")

    # ── Step 6 — append the two trade-size columns to the file on disk ──────
    # Done last so the imputer (Step 4) sees the exact 8-column schema it
    # validates. We read the file back as strings (keep_default_na=False) so
    # the imputer's own formatting for the existing columns is preserved
    # byte-for-byte, then append RTH_tradeSize / ETH_TradeSize (merged by
    # Symbol to be robust against any row reordering) and rewrite. Values use
    # the sentinel policy stashed in `size_by_symbol`: 44444 for attempted-but-
    # failed fetches, empty for --max-float-skipped rows. The 44444 sentinels
    # are then imputed in Step 7 below (unless --no-size-impute); the empty
    # --max-float-skipped rows stay empty.
    try:
        disk = pd.read_csv(output_path, sep="\t", dtype=str, keep_default_na=False)

        def _fmt_size(v) -> str:
            if v != v:            # NaN → skipped row, leave blank
                return ""
            if float(v) == 44444:  # fetch-failure sentinel, keep as-is
                return "44444"
            return f"{float(v):.4f}"

        _nan_pair = (float("nan"), float("nan"))
        disk["RTH_tradeSize"] = disk["Symbol"].map(
            lambda s: _fmt_size(size_by_symbol.get(str(s), _nan_pair)[0]))
        disk["ETH_TradeSize"] = disk["Symbol"].map(
            lambda s: _fmt_size(size_by_symbol.get(str(s), _nan_pair)[1]))
        disk.to_csv(output_path, sep="\t", index=False)
        log.info(f"Step 6 — appended RTH_tradeSize / ETH_TradeSize "
                 f"(RTH_size={valid_rth_size}/{total}, "
                 f"ETH_size={valid_eth_size}/{total}) → {output_path.name}")
    except Exception as exc:
        log.exception(f"Step 6 (trade-size append) failed: {exc!r} — the "
                      f"ITI/price columns on disk are unaffected.")

    # ── Step 7 — in-place imputation of the trade-size columns ──────────────
    # The trade-size analogue of Step 4: trade_size_imputer.py converts the
    # 44444 RTH_tradeSize/ETH_TradeSize sentinels to model predictions and
    # appends 2 provenance sidecars (TradeSize_impute_flag /
    # TradeSize_impute_method) → the final 14-column file. --max-float-skipped
    # (blank) rows stay blank, matching the ITI imputer's nan_skipped policy.
    # ITI_imputer.py is untouched; trade_size_imputer imports its HGB helpers.
    # The write is atomic (temp + os.replace); any failure here leaves the
    # 12-column Step 6 file (raw 44444 sizes) intact and re-runnable. Imports
    # are deferred (sklearn is heavy) so --no-size-impute stays zero-cost.
    if args.no_size_impute:
        log.info("--no-size-impute: skipping trade_size_imputer step.")
    else:
        log.info(f"Step 7 — imputing trade-size columns in place via "
                 f"trade_size_imputer.py ({output_path.name}) …")
        try:
            from trade_size_imputer import main as _size_main  # noqa: E402
            rc = _size_main(["--input", str(output_path)])
            if rc == 0:
                log.info("  trade_size_imputer: ok (14-col file written in place).")
            else:
                log.warning(f"  trade_size_imputer: returned rc={rc} (likely too "
                            f"few complete rows for training — check "
                            f"trade_size_imputer.log for the cohort breakdown).")
        except Exception as exc:
            log.exception(f"trade_size_imputer failed: {exc!r} — the file on "
                          f"disk (12-col, raw 44444 sizes) is unaffected; "
                          f"imputation can be re-run manually.")

    # ── Step 8 — append short-interest / squeeze metrics in place ───────────
    # ../ShortInterrest/ShortInterrest_AddON.enrich_dataframe appends the 24
    # SI_*/SVR_*/CTB_*/Borrow_*/RegSHO_*/SqueezeFlags/SqueezeScore/
    # SI_DataCoverage columns → the 38-column file. Every source is a BULK
    # whole-market download (one FINRA short-interest POST, ~6 CNMSshvol files,
    # ~10 nasdaqth files), cached per-day under ../ShortInterrest/cache, so the
    # cost is ~8 s regardless of symbol count.
    #
    # The seven borrow columns come out BLANK in v3 — see the ibkr_borrow={}
    # note at the call site. `SI_ChangePct` and `SVR_5dAvgPct`, the only two SI
    # columns in the XGBoost 49-feature allowlist, are unaffected: both are
    # FINRA-sourced and reproduce the training-set backfill exactly.
    #
    # ⚠ KNOWN DIVERGENCE, accepted. `RegSHO_Flag` / `RegSHO_ConsecDays` are
    # built by walking back from `eastern_today()`, so their 1-session
    # publication lag is accidental — Nasdaq posts nasdaqth<D>.txt around 23:00
    # ET and the walk simply fails to find it. A run that reaches this step
    # after 23:00 picks up day D's list where the training backfill
    # (`regsho_lag_sessions=1`) used D-1's, giving ConsecDays one higher.
    # Neither column is a model feature; only SqueezeScore (10 of its 100
    # points) is touched. Keeping Step 8 HERE, before the ~60-minute Step 9,
    # is what keeps the run on the right side of 23:00 on most nights.
    #
    # Same read-back-as-strings pattern as Step 6 so the imputers' formatting of
    # the existing columns is preserved byte-for-byte. Best-effort like Steps
    # 4/6/7: any failure logs and leaves the 14-column file valid on disk. The
    # step is also re-runnable standalone:
    #   /home/tom/venv/bin/python ../ShortInterrest/ShortInterrest_AddON.py \
    #       -i data/stocks_universe_YYYY-MM-DD.tsv   (writes a _SI.tsv sibling)
    if args.no_short_interest:
        log.info("--no-short-interest: skipping short-interest step.")
    else:
        log.info(f"Step 8 — appending short-interest / squeeze metrics via "
                 f"ShortInterrest_AddON ({output_path.name}) …")
        try:
            _si = _load_short_interest_addon()
            disk = pd.read_csv(output_path, sep="\t", dtype=str,
                               keep_default_na=False)
            n_cols_before = len(disk.columns)
            # ibkr_borrow={} — NOT None. `use_ibkr = ibkr_borrow is not None`
            # inside the addon, so None re-enables the two DEAD legacy borrow
            # sources: a 15 s shortstock-FTP timeout (unreachable on this
            # network, always has been) plus one iBorrowDesk call per symbol at
            # 0.8 s (403 since 2026-07-22) — ~15 min to produce nothing. An
            # empty dict means "IBKR is the source and it supplied nothing",
            # which is the blank-and-free path v3 wants.
            disk = _si.enrich_dataframe(
                disk, skip_borrow_history=True, ibkr_borrow={})
            disk.to_csv(output_path, sep="\t", index=False)
            covered = int((disk["SI_DataCoverage"] != "none").sum())
            # Report the two SI columns that are actually XGBoost features
            # rather than the old borrow count: SI_DataCoverage is satisfied by
            # FINRA/SVR/RegSHO alone, so it reads 100% whatever borrow does, and
            # v3 does not fetch borrow at all. (`Borrow_Source` is None rather
            # than "" in the returned frame — it only becomes blank on write —
            # so a `!= ""` count there would report a fully-populated column.)
            n_si_chg = int(disk["SI_ChangePct"].astype(str).str.len().gt(0).sum())
            n_svr    = int(disk["SVR_5dAvgPct"].astype(str).str.len().gt(0).sum())
            log.info(f"Step 8 — appended {len(disk.columns) - n_cols_before} "
                     f"SI columns (coverage: {covered}/{len(disk)} rows with "
                     f"any data; model features SI_ChangePct {n_si_chg}/"
                     f"{len(disk)}, SVR_5dAvgPct {n_svr}/{len(disk)}) "
                     f"→ {output_path.name}")
        except Exception as exc:
            log.exception(f"Step 8 (short-interest) failed: {exc!r} — the "
                          f"14-column file on disk is unaffected; re-run "
                          f"ShortInterrest_AddON.py manually against it.")


    # ── Step 8.5 — the anchor Date column + the raw ITI/trade-size sidecars ──
    # Has to come AFTER Step 7: ITI_imputer and trade_size_imputer both validate
    # the file against a fixed 8/10-column schema and would reject the extras.
    #
    # `Date` is the pinned session date. It is what Steps 10/11 anchor off (only
    # market_state_calculator has a real --anchor-date flag; vix_addOn hard-
    # requires a date COLUMN and exits without one), and it makes the file state
    # its own provenance instead of leaving that to a filename that has
    # historically drifted by a session.
    #
    # The four `*_raw` columns carry the pre-imputation ITI / trade-size values,
    # 44444 sentinel intact. The canonical columns must keep the imputers'
    # predictions because Orchestrator5.2 reads them as itiBaseline /
    # tradeSizeBaseline for the mole trigger; the XGBoost models were trained on
    # the raw sentinel and map it to a frozen constant
    # (xgb_cons49_se_prep.json: RTH_avgITI_sec → 46800.0, ETH_avgITI_sec →
    # 68400.0, RTH_tradeSize → 0.0). Neither view can serve both consumers, so
    # the file carries both. None of the three survived SHAP pruning into the
    # current 49-feature allowlist — this is for retrains.
    try:
        disk = pd.read_csv(output_path, sep="\t", dtype=str,
                           keep_default_na=False)

        def _fmt_raw(v) -> str:
            """Blank = never attempted; 44444 = attempted and failed."""
            if v is None or v != v:          # NaN: --max-float-skipped row
                return ""
            if float(v) == 44444:
                return "44444"
            return f"{float(v):.4f}"

        _nan_pair = (float("nan"), float("nan"))
        disk[_DATE_COLUMN] = date_tag
        disk["RTH_avgITI_sec_raw"] = disk["Symbol"].map(
            lambda s: _fmt_raw(raw_iti_by_symbol.get(str(s), _nan_pair)[0]))
        disk["ETH_avgITI_sec_raw"] = disk["Symbol"].map(
            lambda s: _fmt_raw(raw_iti_by_symbol.get(str(s), _nan_pair)[1]))
        disk["RTH_tradeSize_raw"] = disk["Symbol"].map(
            lambda s: _fmt_raw(size_by_symbol.get(str(s), _nan_pair)[0]))
        disk["ETH_TradeSize_raw"] = disk["Symbol"].map(
            lambda s: _fmt_raw(size_by_symbol.get(str(s), _nan_pair)[1]))
        disk.to_csv(output_path, sep="\t", index=False)
        n_sentinel = sum(int((disk[c] == "44444").sum()) for c in _RAW_SIDECARS)
        log.info(f"Step 8.5 — appended {_DATE_COLUMN}={date_tag} and "
                 f"{len(_RAW_SIDECARS)} raw sidecars "
                 f"({n_sentinel} sentinel cells preserved) → "
                 f"{output_path.name}")
    except Exception as exc:
        log.exception(f"Step 8.5 failed: {exc!r} — the 38-column file on disk "
                      f"is unaffected, but Steps 10/11 need the {_DATE_COLUMN} "
                      f"column and will be skipped.")

    have_date_col = _DATE_COLUMN in _read_header(output_path)

    # ── Step 9 — market state: 84 columns, 10 of them XGBoost features ───────
    # The one expensive step in v3 (~60 min for ~830 symbols): 2 IBKR requests
    # per symbol, each a ~2-year daily-bar series, because the longest window
    # any feature needs is 252 sessions (days_since_52w_low). MSC's bar cache
    # does NOT help night-over-night — its hit test is
    # `cached[-1].date >= anchor`, so a new anchor misses for every symbol.
    #
    # --days-prior 0 against the pinned anchor is the whole point: it produces
    # the same vector a training backfill produced for a PR on D+1 at
    # --days-prior 1 ("train with 1, infer with 0"). --session both is not a
    # free choice either — the training msc_ block was built with it and the
    # three session modes are not comparable.
    if args.no_market_state:
        log.info("--no-market-state: skipping Step 9. The model cannot be "
                 "scored without it (10 of the 49 features).")
    elif not _MSC_SCRIPT.exists():
        log.error(f"Step 9 — {_MSC_SCRIPT} not found; skipping.")
        _problems.append(f"Step 9: {_MSC_SCRIPT.name} not found, step skipped")
    else:
        log.info(f"Step 9 — market state (anchor {date_tag}, "
                 f"session={args.msc_session}) — expect ~60 min at this "
                 f"universe size …")
        if not _run_addon(
            "Step 9 market-state", _MSC_SCRIPT,
            ["-i", str(output_path),
             "--anchor-date", date_tag,
             "--days-prior", "0",
             "--session", args.msc_session,
             "--host", args.host,
             "--port", str(args.msc_port if args.msc_port else args.port),
             "--client-id", str(args.msc_client_id),
             "--concurrency", str(args.msc_concurrency),
             "--timeout", str(args.msc_timeout),
             # The universe file's own close, as the basis-check referee's
             # fallback. MSC prefers a point-in-time stocks_universe_<anchor>.tsv
             # from --price-ref-dir (which defaults to this data/ directory and
             # will find the file we just wrote); this names the in-file column
             # explicitly rather than leaving it to auto-detection.
             "--price-col", "LastDailyClosePrice",
             "--split-cache-max-age-days", str(args.msc_split_max_age),
             # --log takes nargs='?', so it must stay LAST or argparse eats the
             # next token as its value.
             "--log"],
            output_path, "mktstate", _MSC_MARKER):
            _problems.append("Step 9 market-state failed — 84 msc_ columns missing")

    # ── Step 10 — VIX + CNN Fear & Greed: 20 columns, FNG_Index is a feature ──
    # Market-wide: every value derives from the anchor date alone, so the whole
    # run costs two bulk HTTP requests (CBOE VIX_History.csv + one CNN graphdata
    # call) and the same 20-value record is broadcast to every row.
    # --fng-carry-forward 1 matches the training set: CNN occasionally skips a
    # real session, and the flag walks the WHOLE block back one day so
    # FNG_PrevIndex/FNG_Chg1D stay self-consistent, with FNG_AsOf recording it.
    #
    # ⚠ --refresh is NOT optional. Alone among the steps, vix_addOn resolves its
    # anchor from DATA AVAILABILITY rather than from the calendar: the session
    # index is built from whatever rows the CBOE history happens to contain
    # (`build_session_index`), and `resolve_anchor` then silently returns the
    # newest session at or before the target with `problem=None`. So a VIX cache
    # that predates the anchor slides the whole block back a session with no
    # warning, no flag and no blank — it even logs the miss as "non-session date
    # (weekend/holiday)", which for a Thursday is simply wrong. `--refresh`
    # bypasses the addon's 12 h TTL for the VIX *and* the F&G cache
    # (`load_history:315`, `load_fng_history:454`), which costs nothing the step
    # was not already paying on a cold cache. Without it, ANY v3 run between
    # ~08:30 and 20:30 — a manual re-run, a retry, a backfill — leaves a cache
    # too fresh to refetch and poisons that evening's cron.
    #
    # Note FNG rides on the VIX index: `fill_fng` reads `days[j]`, so a stale
    # CBOE file drags Fear & Greed back a session too, even when CNN's own cache
    # already holds the right day.
    if args.no_vix:
        log.info("--no-vix: skipping Step 10 (FNG_Index is one of the 49 "
                 "features).")
    elif not have_date_col:
        log.error(f"Step 10 — no {_DATE_COLUMN} column on disk (Step 8.5 "
                  f"failed); vix_addOn has no --anchor-date flag and cannot "
                  f"run. Skipping.")
        _problems.append("Step 10: no Date column (Step 8.5 failed), VIX/FNG skipped")
    elif not _VIX_SCRIPT.exists():
        log.error(f"Step 10 — {_VIX_SCRIPT} not found; skipping.")
        _problems.append(f"Step 10: {_VIX_SCRIPT.name} not found, step skipped")
    else:
        log.info(f"Step 10 — VIX + Fear & Greed (anchor {date_tag}) …")
        if _run_addon(
                "Step 10 vix/fng", _VIX_SCRIPT,
                ["-i", str(output_path),
                 "--date-column", _DATE_COLUMN,
                 "--days-prior", "0",
                 "--fng-carry-forward", "1",
                 "--refresh"],
                output_path, "vixfng", _VIX_MARKER):
            if not _check_vix_anchor(output_path, date_tag):
                _problems.append("Step 10: VIX/FNG anchor does not match the "
                                 "market-state anchor (CBOE late?)")
        else:
            _problems.append("Step 10 vix/fng failed — 20 VIX/FNG columns missing")

    # ── Step 11 — last-split recency (opt-in) ────────────────────────────────
    # Off by default: none of its four columns are in the 49-feature allowlist
    # and only LastSplit_Factor survived the training-file correlation prune.
    #
    # --never-split-fill blank, NOT the addon's default `maxima`: that sentinel
    # is computed from THIS file's own resolved rows, so a nightly universe
    # would emit e.g. 1986-11-04 / 1.0 / 168588.0 where the training corpus
    # froze 1985-09-12 / 1.0 / 172054.1. Blank keeps the raw observation and
    # leaves the frozen constants to whoever replays the training transform;
    # LastSplit_Status still separates never_split from no_split_yet/no_data.
    #
    # Whole-file mode (--date-column '') with --as-of <D> 20:00 rather than
    # per-row mode, because a universe snapshot is one instant: the 20:00 ET
    # capture time. LastSplit_MktHours is therefore measured to that instant —
    # the O5.2 successor should recompute it from LastSplit_Date and the real
    # PR instant, which is what the training column measured.
    if not args.splits:
        log.debug("Step 11 — --splits not set; skipping last-split columns.")
    elif not _SPLITS_SCRIPT.exists():
        log.error(f"Step 11 — {_SPLITS_SCRIPT} not found; skipping.")
        _problems.append(f"Step 11: {_SPLITS_SCRIPT.name} not found, step skipped")
    else:
        log.info(f"Step 11 — last-split recency (as-of {date_tag} 20:00) …")
        if not _run_addon(
            "Step 11 splits", _SPLITS_SCRIPT,
            ["-i", str(output_path),
             "--date-column", "",
             "--as-of", f"{date_tag} 20:00",
             "--days-prior", "0",
             "--never-split-fill", "blank",
             # Same delisted-symbol cache hazard as Step 9; see
             # --msc-split-max-age. Both share tools/market_state/tables/splits.
             "--split-max-age-days", str(args.msc_split_max_age)],
            output_path, "splits", _SPLITS_MARKER):
            _problems.append("Step 11 splits failed — LastSplit_* columns missing")

    # ── Done ────────────────────────────────────────────────────────────────
    final_cols = _read_header(output_path)
    log.info(f"pipeline_daily-3: finished. {output_path.name} — "
             f"{len(final_cols)} columns, anchor {date_tag}.")
    missing = [c for c in ("Float_M", "MarketCap_M", "SI_ChangePct",
                           "SVR_5dAvgPct", "FNG_Index", "atr_pct_14",
                           "float_turnover_max_20d", "msc_basis_ratio",
                           "dist_swing_high_20d_pct", "range_pct_1d",
                           "amihud_63d", "realized_vol_5d", "gap_abs_mean_20d",
                           "days_since_52w_low", "illiq_trend")
               if c not in final_cols]
    if missing:
        live = [c for c in missing if c in _M2_UNIVERSE_FEATURES]
        log.warning(f"  ⚠ {len(missing)}/15 XGBoost feature columns are ABSENT "
                    f"from the output: {missing}. Of those, {len(live)} are in "
                    f"the LIVE model-2/2FT 25-feature allowlist ({live or 'none'}) "
                    f"— O5.3 cannot score from this file until the failed step "
                    f"is re-run. The rest belong to the superseded 49-column "
                    f"model-1 and are kept for retrains.")
        _problems.append(f"{len(missing)}/15 XGBoost feature columns absent "
                         f"({len(live)} used by the live model): {missing}")
    else:
        log.info(f"  all 15 symbol-level XGBoost feature columns present "
                 f"({len(_M2_UNIVERSE_FEATURES)} of them in the live "
                 f"model-2/2FT allowlist).")
    try:
        with open(output_path) as fh:
            n_rows = sum(1 for _ in fh) - 1
    except OSError:
        n_rows = -1
    if n_rows < _MIN_OUTPUT_ROWS:
        _problems.append(f"only {n_rows} rows in the file (normal ~820-840)")

    if _problems:
        log.warning(f"  ⚠ {len(_problems)} problem(s) — sending a phone alert:")
        for prob in _problems:
            log.warning(f"    - {prob}")
        _alert(f"Universe: {output_path.name} is flawed",
               "\n".join(f"- {prob}" for prob in _problems)
               + f"\nLog: {_log_path}",
               priority="default")



if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SystemExit (lock check, argparse) and Ctrl-C are not crashes and are
        # not caught. Re-raise so cron.log keeps the traceback.
        _alert("Universe: nightly run crashed",
               f"daily_universe_pipeline-3 stopped with {type(exc).__name__}: "
               f"{str(exc)[:300]}\nNo stocks_universe file for tonight. Log: "
               f"{_log_path}; traceback in runs/cron.log.")
        raise


# ─── Cron ─────────────────────────────────────────────────────────────────────
#
# v3 takes over v2's nightly slot. The crontab block already exports
# TZ=America/New_York, which is what makes `datetime.now()` in _session_date()
# an ET date:
#
#   TZ=America/New_York
#   30 20 * * * /home/tom/venv/bin/python3 \
#       /home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/daily_universe_pipeline-3.py \
#       --port 4002 --freq-concurrency 2 --freq-timeout 60 \
#       >> /home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/runs/cron.log 2>&1
#   0 6 * * * /home/tom/venv/bin/python3 \
#       /home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/universe_watchdog.py \
#       >> /home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/runs/watchdog.log 2>&1
#
# The 06:00 watchdog phones an alert if last night's run did not finish (the
# one failure this script cannot report itself: a hang or a kill).
#
# Port 4002 is the idle paper Gateway. Both IBKR steps — Step 2/3's price and
# frequency fetches and Step 9's market-state bars — are bulk historical work,
# which the LIVE gateway starves under contention (2026-07-06: 691/819 lost).
#
# `runs/cron.log` rotation is unchanged from v2. The per-day file is
# `runs/{DD-MMM-YYYY}/pipeline_daily-3.log` (v2 wrote `pipeline_daily.log`), so
# the two can run side by side during validation without interleaving.
