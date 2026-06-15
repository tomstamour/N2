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

  Output columns (12 after ITI_imputer's 2 sidecars; 14 after trade_size_imputer's 2):
    Symbol | Exchange | Float_M | MarketCap_M | Float_Source
           | LastDailyClosePrice | RTH_avgITI_sec | ETH_avgITI_sec
           [ | ITI_impute_flag | ITI_impute_method ]
           | RTH_tradeSize | ETH_TradeSize
           [ | TradeSize_impute_flag | TradeSize_impute_method ]
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

# The trade-size addon module has a literal '.' in its filename
# (trade_frequency_and_size.addOn.py), so it can't be imported by name — load
# it by path. Done lazily inside main() to keep import errors local to a run.
_TRADE_SIZE_ADDON = _SCRIPT_DIR / "trade_frequency_and_size.addOn.py"

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

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch NASDAQ symbol list with floats, IBKR close prices, and trade frequency."
    )
    parser.add_argument("--host",     default="127.0.0.1", metavar="HOST")
    parser.add_argument("--port",     type=int, default=4001, metavar="PORT")
    parser.add_argument("--clientID", type=int, default=10,   metavar="ID")
    parser.add_argument("--refresh",  action="store_true",
                        help="Force fresh symbol/float fetch (ignore 24h cache)")
    parser.add_argument("--past-days-lookback", type=int, default=3, metavar="N",
                        help="Trading days to average for RTH/ETH avg ITI (Step 3)")
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

    # Suffix manual `--limit` runs so a 500-symbol test doesn't silently
    # overwrite that same date's cron output (the cron passes no --limit, so
    # its filename is unchanged). Bare `--limit 0` / unset stays bare.
    date_tag    = datetime.now().strftime("%Y-%m-%d")
    limit_tag   = f"_limit{args.limit}" if args.limit and args.limit > 0 else ""
    output_path = _DATA_DIR / f"{_OUTPUT_STEM}_{date_tag}{limit_tag}.tsv"

    valid_rth = sum(1 for v in rth_results if v == v)
    valid_eth = sum(1 for v in eth_results if v == v)

    # Overwrite protection — only for the canonical (non --limit) filename.
    # A cron starting at 20:30 ET finishes after midnight and writes
    # `*_dayN+1.tsv`; an identically-named manual re-run later on dayN+1 used
    # to silently clobber it. We now read the existing file's populated-row
    # counts and only replace if the new run is at least as good in BOTH
    # ITI columns. A worse run goes to a timestamped sidecar instead.
    # `--limit` test runs already get their own filename and skip this path.
    superseded_path = None
    if not limit_tag and output_path.exists():
        try:
            existing = pd.read_csv(output_path, sep="\t")
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

        if valid_rth >= existing_rth and valid_eth >= existing_eth:
            # New run is at least as good in both — promote it. Stash the
            # outgoing file for one safety cycle so a bad replacement is
            # recoverable until tomorrow.
            ts = datetime.now().strftime("%H%M")
            superseded_path = _DATA_DIR / (
                f"{_OUTPUT_STEM}_{date_tag}_{ts}_superseded.tsv")
            try:
                os.replace(output_path, superseded_path)
                log.info(f"Replacing existing {output_path.name} "
                         f"(old RTH={existing_rth}, ETH={existing_eth}; new "
                         f"RTH={valid_rth}, ETH={valid_eth}). Old file "
                         f"moved to {superseded_path.name}.")
            except OSError as exc:
                log.warning(f"Could not stash existing file ({exc!r}); "
                            f"overwriting in place.")
                superseded_path = None
        else:
            # Existing file is better in at least one column and equal-or-
            # better in the other → keep it, write the new run to a sidecar.
            ts = datetime.now().strftime("%H%M")
            sidecar = _DATA_DIR / (
                f"{_OUTPUT_STEM}_{date_tag}_{ts}.tsv")
            log.warning(
                f"PROTECTING existing {output_path.name} "
                f"(RTH={existing_rth}, ETH={existing_eth}) — new run is "
                f"weaker (RTH={valid_rth}, ETH={valid_eth}). Writing "
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


if __name__ == "__main__":
    main()


# ─── NOT scheduled in cron ────────────────────────────────────────────────────
#
# This trade-size-enabled version is intentionally NOT in crontab — the nightly
# cron still runs pipeline_daily.py (writing the canonical
# `nasdaq_symbols_data_priced_YYYY-MM-DD.tsv`). Run this one manually to produce
# the parallel `nasdaq_symbols_data_priced_sized_YYYY-MM-DD.tsv`, e.g.:
#
#   /home/tom/venv/bin/python3 daily_universe_pipeline-2.py
#
# Once validated, swap the cron line to point here (and decide whether the
# `_sized` output should become the canonical one). `runs/cron.log` rotation and
# the per-day `runs/{DD-MMM-YYYY}/pipeline_daily.log` behave exactly as in
# pipeline_daily.py (this script reuses the same `log` name "pipeline_daily").
