#!/usr/bin/env python3
"""
daily_universe_pipeline-2.py
----------------------------
Trade-size-enabled successor to pipeline_daily.py. Identical to it except
Step 3 also fetches a per-trade Trade Size baseline (shares exchanged per
transaction) for the RTH and ETH sessions — derived from the *same* TRADES
bars already fetched for the ITI values, so no extra IBKR request — and two
new columns (RTH_tradeSize / ETH_TradeSize) are appended to the output.

To avoid any collision with the production cron output, this version writes
to a DISTINCT filename stem:  nasdaq_symbols_data_priced_sized_YYYY-MM-DD.tsv
(the original pipeline_daily.py output is left completely untouched).

End-to-end pipeline:
  1. Fetch symbol list + float data + market cap via
     NASDAQ_symbols_data.build_dataframe()
     (Float_M and MarketCap_M ride on the same finviz / yfinance call —
     no extra network traffic vs. the float-only era.)
  2. Append LastDailyClosePrice for each symbol via IBKR historical data
  3. Append RTH_avgITI_sec / ETH_avgITI_sec AND RTH_tradeSize / ETH_TradeSize
     via trade_frequency_and_size.addOn (4-tuple per symbol; both metrics come
     from one reqHistoricalData TRADES request).
  4. Save the 8-column canonical-schema file to
     ./data/nasdaq_symbols_data_priced_sized_YYYY-MM-DD.tsv
     (manual `--limit N` runs are written to
     ./data/nasdaq_symbols_data_priced_sized_YYYY-MM-DD_limitN.tsv so a small
     test never silently overwrites the same-day nightly output.)
  5. Run ITI_imputer.py on the file just written, OVERWRITING it
     in-place with the 10-column imputed version (2 sidecar columns
     added: status flag, method tag; the canonical RTH/ETH columns receive
     the model predictions). The imputer is given the unchanged 8-column
     schema it expects — the trade-size columns are NOT present yet. Skipped
     by `--no-impute`. Imputer failure does NOT clobber the file — the write
     at step 4 is preserved on any imputer exception (atomic temp+rename
     inside ITI_imputer guarantees this).
  6. Append RTH_tradeSize / ETH_TradeSize to whatever file step 5 produced,
     reading it back as strings so the imputer's exact formatting for the
     first 8/10 columns is preserved. At this point trade size carries the
     44444 sentinel for attempted-but-failed fetches and stays NaN (empty)
     for --max-float-skipped rows — the addon's behavior. (12-column file.)
  7. Run trade_size_imputer.py on the file in place (unless --no-size-impute):
     the 44444 RTH_tradeSize/ETH_TradeSize sentinels are replaced with model
     predictions and 2 provenance sidecars are appended → the final 14-column
     file. --max-float-skipped (blank) rows stay blank. ITI_imputer.py is left
     untouched; trade_size_imputer imports its HGB helpers. Atomic write; a
     failure here leaves the 12-column Step 6 file intact and re-runnable.
  8. Run ../ShortInterrest/ShortInterrest_AddON.enrich_dataframe on the file
     in place (unless --no-short-interest): appends 24 short-interest /
     short-squeeze columns (SI_*, SVR_*, CTB_*, Borrow_*, RegSHO_*,
     SqueezeFlags, SqueezeScore, SI_DataCoverage) from free web sources —
     FINRA short interest + daily short volume, IBKR shortstock FTP,
     iBorrowDesk, Nasdaq Reg SHO threshold list. No IBKR API connection.
     → final 38-column file. Best-effort: a failure leaves the 14-column
     file intact; re-run the addon standalone to recover.
     --skip-borrow-history drops the per-symbol iBorrowDesk calls.

  Output columns (12 after ITI_imputer's 2 sidecars; 14 after trade_size_imputer's 2;
  38 after ShortInterrest_AddON's 24):
    Symbol | Exchange | Float_M | MarketCap_M | Float_Source
           | LastDailyClosePrice | RTH_avgITI_sec | ETH_avgITI_sec
           [ | ITI_impute_flag | ITI_impute_method ]
           | RTH_tradeSize | ETH_TradeSize
           [ | TradeSize_impute_flag | TradeSize_impute_method ]
           [ | SI_* ×8 | SVR_* ×4 | CTB_* ×3 | Borrow_* ×4 | RegSHO_* ×2
             | SqueezeFlags | SqueezeScore | SI_DataCoverage ]
  Step 1 fields (Float_M, MarketCap_M) are NaN when no source had the value.
  Step 2/3 fields are filled with the sentinel 44444 on miss.

NOTE: not wired into cron — run manually / schedule separately once validated.

Usage:
    python daily_universe_pipeline-2.py [--host HOST] [--port PORT] [--clientID ID]
                             [--refresh] [--past-days-lookback N]
                             [--price-concurrency N] [--freq-concurrency N]
                             [--batch-size N]
                             [--price-timeout S] [--freq-timeout S]
                             [--freq-bar-size SIZE]
                             [--retries N] [--freq-retries N]
                             [--max-float M]
                             [--limit N]

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
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd  # used by overwrite-protection: read existing TSV to compare

_SCRIPT_DIR = Path(__file__).parent
_DATA_DIR   = _SCRIPT_DIR / "data"
_RUNS_DIR   = _SCRIPT_DIR / "runs"

# Distinct output stem so this trade-size-enabled version never collides with
# pipeline_daily.py's canonical `nasdaq_symbols_data_priced_YYYY-MM-DD.tsv`.
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

# Aggregate stdout/stderr log written by the cron line ( `>> cron.log 2>&1` ).
# Cron's shell is the sole writer, so we cannot use Python's
# RotatingFileHandler (that would race on rename). Instead, before any
# logging starts, we manually trim the file if it crosses _CRON_LOG_MAX
# bytes — moving the current contents to cron.log.1 (overwriting an older
# backup). Bounded, single-writer, race-free.
_CRON_LOG     = _RUNS_DIR / "cron.log"
_CRON_LOG_MAX = 5_000_000

log = logging.getLogger("pipeline_daily")


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
    log_path = log_dir / "pipeline_daily.log"

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


def _fetch_borrow(ib, symbols, qualified, args) -> dict:
    """Step 3.6 — the borrow map {SYMBOL: {fee, rebate, available, ...}}.

    Two halves, because IBKR serves them through different mechanisms:

      * fee + rebate come from FEE_RATE / REBATE_RATE daily bars, i.e. ordinary
        historical requests — so they go through _run_concurrent and inherit its
        reconnect/disconnect semantics and NaN fill. Concurrency is its own knob
        (default 4, the same rung of the ladder that tamed the Error 162/366
        slot-exhaustion cascade) because this adds ~2 requests per symbol.
      * shortable shares come from a market-data subscription (generic tick
        236), which is not request/response and cannot go through
        _run_concurrent; ibkr_borrow_addOn batches and cancels those itself.

    Contracts are reused from Step 2, exactly as Step 3 does: a symbol that
    failed to qualify there short-circuits rather than paying for a re-qualify.
    See ibkr_borrow_addOn.py for why this replaced the two dead web sources.
    """
    import ibkr_borrow_addOn as borrow  # noqa: E402

    nan = float("nan")
    sym_to_contract = dict(zip(symbols, qualified))
    n_qualified = sum(1 for c in qualified if c is not None)
    log.info(f"Step 3.6/3 — fetching borrow fee/rebate for {n_qualified} "
             f"qualified symbols (concurrency={args.borrow_concurrency}) …")

    borrow_sem = asyncio.Semaphore(args.borrow_concurrency)

    async def _borrow_one(symbol):
        contract = sym_to_contract.get(symbol)
        if contract is None:
            return (nan, nan, nan)
        return await borrow.fetch_borrow_async(
            ib, str(symbol), borrow_sem,
            args.borrow_timeout, args.borrow_retries,
            contract=contract,
        )

    rates = _run_concurrent(
        ib, symbols,
        coro_factory=_borrow_one,
        is_empty=lambda r: r[0] != r[0],      # fee is NaN
        nan_value=(nan, nan, nan),
        label="borrow fetch",
        args=args,
    )
    n_fee = sum(1 for r in rates if r[0] == r[0])
    log.info(f"  borrow: fee for {n_fee}/{n_qualified}, "
             f"rebate for {sum(1 for r in rates if r[1] == r[1])}")

    contracts = [c for c in qualified if c is not None]
    avail = borrow.fetch_shortable_shares(ib, contracts)
    log.info(f"  borrow: shortable shares for {len(avail)}/{len(contracts)}")

    # Record tonight's snapshot BEFORE computing the deltas — avail_metrics
    # indexes 5 sessions back from the newest entry, which is only correct when
    # the newest entry is tonight. append_avail_snapshot returns the updated
    # history for exactly that reason.
    history_path = str(_DATA_DIR / "borrow_avail_history.tsv")
    history = borrow.append_avail_snapshot(
        history_path, borrow.session_date(), avail)

    borrow_map = borrow.build_borrow_map(symbols, rates, avail, history)
    n_delta = sum(1 for e in borrow_map.values()
                  if e['avail_delta_5d_pct'] is not None)
    log.info(f"  borrow: {len(borrow_map)} symbols with any borrow data; "
             f"{n_delta} with an availability delta (needs "
             f"{borrow.BORROW_DELTA_SESSIONS}+ sessions in the store).")
    return borrow_map


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
                             "By default, after the imputers finish, "
                             "ShortInterrest_AddON.enrich_dataframe appends the "
                             "24 SI_*/SVR_*/CTB_*/Borrow_*/RegSHO_*/Squeeze "
                             "columns in place (free web sources; no IBKR "
                             "connection). Best-effort: a failure leaves the "
                             "14-column file intact.")
    parser.add_argument("--skip-borrow-history", action="store_true",
                        help="Step 8: skip the per-symbol iBorrowDesk calls "
                             "(~0.8 s each). Faster, but CTB_FeeDelta5d / "
                             "Borrow_AvailDelta5dPct / Borrow_UtilProxy stay "
                             "blank and SqueezeScore loses those components. "
                             "Has no effect unless --no-ibkr-borrow is also "
                             "set: the IBKR-API borrow fetch (Step 3.6) "
                             "bypasses iBorrowDesk entirely.")
    parser.add_argument("--no-ibkr-borrow", action="store_true",
                        help="Skip Step 3.6, the IBKR borrow fetch (FEE_RATE / "
                             "REBATE_RATE daily bars + shortable shares via "
                             "generic tick 236). Step 8 then falls back to the "
                             "legacy web sources — both of which are dead "
                             "(iBorrowDesk 403s since 2026-07-22; the "
                             "shortstock FTP has never been reachable), so "
                             "the seven borrow columns will be blank.")
    parser.add_argument("--borrow-concurrency", type=int, default=4,
                        metavar="N",
                        help="Max in-flight IBKR requests for Step 3.6. "
                             "Default 4, matching the --freq-concurrency rung "
                             "that tamed the Error 162/366 slot-exhaustion "
                             "cascade; this step adds ~2 historical requests "
                             "per symbol, so raising it risks reopening that.")
    parser.add_argument("--borrow-timeout", type=int, default=60,
                        metavar="SEC",
                        help="Per-request timeout for the Step 3.6 rate bars. "
                             "Default 60 for the same reason the cron passes "
                             "--freq-timeout 60: measured tail latency on these "
                             "rate bars reaches 24 s against a median of ~6 s, "
                             "and at 30 s that tail times out (REBATE_RATE lost "
                             "2 of 20 symbols at concurrency 4). Retries are 0, "
                             "so a generous timeout is the cheap side of the "
                             "trade.")
    parser.add_argument("--borrow-retries", type=int, default=0, metavar="N",
                        help="Per-request retries for Step 3.6; default 0 for "
                             "the same reason as --freq-retries — a symbol "
                             "with no borrow-rate history won't grow one on a "
                             "retry.")
    args = parser.parse_args()

    # Legacy --concurrency seeds whichever per-step knob the user didn't set.
    if args.concurrency is not None:
        args.price_concurrency = args.concurrency
        args.freq_concurrency  = args.concurrency

    _setup_logging()
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    log.info("=" * 60)
    log.info("pipeline_daily: starting")

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
    # Step 3.6's output. Empty means "no borrow data" all the way through to
    # Step 8, which is the same shape a failed/skipped fetch produces — so any
    # exit path still yields a complete file.
    borrow_by_symbol: dict = {}

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

        # ── Step 3.6: borrow market (fee / rebate / shortable shares) ─────────
        # Must run HERE, inside the IBKR block: the connection is closed in the
        # `finally` below, long before Step 8 consumes this. Placed after the
        # Step 3.5 retry so a borrow failure can never cost the ITI data, and
        # wrapped in its own try/except so it degrades to blank columns rather
        # than aborting the run (crash-safety invariants 1 and 2 unchanged).
        if args.no_ibkr_borrow:
            log.info("Step 3.6/3 — --no-ibkr-borrow: skipping the borrow fetch.")
        elif not cutoff_ok:
            log.warning("Step 3.6/3 — skipping borrow fetch: too close to the "
                        f"03:59 ET Gateway restart (now {now_et:%H:%M}).")
        else:
            try:
                borrow_by_symbol = _fetch_borrow(ib, symbols, qualified, args)
            except Exception as exc:
                log.exception(f"Step 3.6 (borrow) failed: {exc!r} — the seven "
                              f"borrow columns will be blank; everything else "
                              f"is unaffected.")
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
    date_tag    = datetime.now().strftime("%Y-%m-%d")
    limit_tag   = f"_limit{args.limit}" if args.limit and args.limit > 0 else ""
    output_path = _DATA_DIR / f"{_OUTPUT_STEM}_{date_tag}{limit_tag}.tsv"

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
                f"{_OUTPUT_STEM}_{date_tag}_{ts}_superseded.tsv")
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
                f"{_OUTPUT_STEM}_{date_tag}_{ts}.tsv")
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
    # SI_DataCoverage columns (FINRA + Nasdaq Reg SHO over the web) → the final
    # 38-column file. The seven borrow columns are NOT web-sourced any more:
    # both of the addon's borrow sources died (iBorrowDesk 403s since
    # 2026-07-22; the shortstock FTP never worked here), so Step 3.6 fetched
    # them from the IBKR connection and `borrow_by_symbol` carries them in.
    # Same read-back-as-strings pattern as Step 6 so the
    # imputers' formatting of the existing columns is preserved byte-for-byte.
    # Best-effort like Steps 4/6/7: the sources are flaky external services,
    # so any failure logs and leaves the 14-column file valid on disk. The
    # step is also re-runnable standalone:
    #   /home/tom/venv/bin/python ../ShortInterrest/ShortInterrest_AddON.py \
    #       -i data/stocks_universe_YYYY-MM-DD.tsv   (writes a _SI.tsv sibling)
    # Raw downloads are cached per-day under ../ShortInterrest/cache, so a
    # same-day re-run only repeats the per-symbol iBorrowDesk misses.
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
            disk = _si.enrich_dataframe(
                disk, skip_borrow_history=args.skip_borrow_history,
                ibkr_borrow=None if args.no_ibkr_borrow else borrow_by_symbol)
            disk.to_csv(output_path, sep="\t", index=False)
            covered = int((disk["SI_DataCoverage"] != "none").sum())
            # Report borrow coverage separately: SI_DataCoverage is satisfied by
            # FINRA/SVR/RegSHO alone, so it stayed at 100% for the five weeks
            # the borrow leg was silently contributing nothing.
            with_borrow = int((disk["Borrow_Source"] != "").sum())
            log.info(f"Step 8 — appended {len(disk.columns) - n_cols_before} "
                     f"SI columns (coverage: {covered}/{len(disk)} rows with "
                     f"any data; borrow: {with_borrow}/{len(disk)}) "
                     f"→ {output_path.name}")
        except Exception as exc:
            log.exception(f"Step 8 (short-interest) failed: {exc!r} — the "
                          f"14-column file on disk is unaffected; re-run "
                          f"ShortInterrest_AddON.py manually against it.")


if __name__ == "__main__":
    main()


# ─── NOT scheduled in cron ────────────────────────────────────────────────────
#
# This trade-size-enabled version is intentionally NOT in crontab — the nightly
# cron still runs pipeline_daily.py (writing the canonical
# `nasdaq_symbols_data_priced_YYYY-MM-DD.tsv`). Run this one manually to produce
# the parallel `nasdaq_symbols_data_priced_sized_YYYY-MM-DD.tsv`, e.g.:
#
#   path/to/venv/bin/python3 daily_universe_pipeline-2.py
#
# Once validated, swap the cron line to point here (and decide whether the
# `_sized` output should become the canonical one). `runs/cron.log` rotation and
# the per-day `runs/{DD-MMM-YYYY}/pipeline_daily.log` behave exactly as in
# pipeline_daily.py (this script reuses the same `log` name "pipeline_daily").
