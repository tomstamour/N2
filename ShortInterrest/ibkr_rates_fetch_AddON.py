#!/usr/bin/env python3
"""
ibkr_rates_fetch_AddON.py — build the borrow-rate cache the SI backfill reads.

WHY THIS EXISTS
---------------
ShortInterrest_backfill_AddON.py sourced its borrow block from iBorrowDesk, and
that endpoint went 403 on 2026-07-22. The cache it left behind stops at fetch
date 2026-07-21 and only ever covered the symbols that appeared in a nightly
universe file, so a PR-stats corpus backfilled today comes out mostly blank:

  * every row anchored later than ~2026-07-24 loses the borrow block outright
    (fetch_iborrowdesk_asof drops a snapshot more than IBD_MAX_STALENESS_DAYS
    stale rather than backdating it), and
  * a symbol that never made the nightly universe has no cache file at all.

CTB_RebatePct is worse: its only source was ever the IBKR shortstock FTP, whose
ports have never been reachable from this network, so it has been blank in every
file this project has produced.

IBKR's own API serves both series as ordinary historical bars, a full year deep:

  reqHistoricalData(whatToShow='FEE_RATE')     daily borrow-fee bars
  reqHistoricalData(whatToShow='REBATE_RATE')  daily rebate-rate bars

Measured on the paper Gateway: 251 daily bars back to 2025-09-02, and a 30-symbol
sample drawn only from symbols the old cache never held returned 30/30 with a
full year of history. That covers any backfill window this project cares about.

WHY A SEPARATE SCRIPT
---------------------
ShortInterrest_backfill_AddON.py is an OFFLINE, re-runnable, point-in-time tool;
every other source it reads is a file on disk. Keeping the Gateway connection
here preserves that: this job runs once (and is resumable — an already-cached
symbol is skipped unless --overwrite), after which the backfill stays offline.

WHY WHOLE-HISTORY-THEN-SLICE
----------------------------
ibkr_borrow_addOn.fetch_borrow_async takes an `end_datetime` so a caller can
anchor one request on a past session, but a corpus spanning N dates would then
cost 2*N requests per symbol. One year of daily bars is a single request per
series, and the backfill slices it per row date locally — 2 requests per symbol
regardless of how many dates the corpus spans.

UNITS: IBKR returns these rates as ANNUALIZED DECIMAL FRACTIONS. Every close is
scaled by 100 HERE, so the cache on disk is already in the percent units the
CTB_* columns use and the conversion lives in exactly one place.
See universe_finder/verify_ibkr_borrow.py for the standing regression on it.

Output: cache/ibkr_rates/{SYMBOL}.json

    {"symbol": "NRXP", "fetched": "2026-09-01", "duration": "1 Y",
     "daily": [{"date": "2026-05-04", "fee": 4.76, "rebate": -1.37}, ...]}

A failed or empty fetch is NEVER written, matching the rule every other cache in
this directory follows: a cached miss would be indistinguishable from a real one.

Run with the venv interpreter, against the IDLE PAPER GATEWAY (4002) — the live
gateway drops bulk historical requests under contention:

  /home/tom/venv/bin/python ibkr_rates_fetch_AddON.py \
      -i ../price_stats_loader/outputs/PRstats_*_mktstate.tsv --port 4002
"""

import argparse
import asyncio
import glob as globmod
import json
import logging
import os
import sys
from datetime import date, datetime

import pandas as pd

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_UNIVERSE_DIR = os.path.abspath(os.path.join(_SCRIPT_DIR, os.pardir, 'universe_finder'))
for _p in (_SCRIPT_DIR, _UNIVERSE_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from ib_insync import IB, Stock  # noqa: E402

# _rate_bars is reused rather than reimplemented: its timeout MUST go inside
# reqHistoricalDataAsync (ib_insync's own timeout branch calls
# cancelHistoricalData and releases the server-side slot; an outer wait_for
# propagates CancelledError first and leaks reqIds until the Gateway hits its
# 50-request cap and goes silent). See ibkr_borrow_addOn.py:161-166.
from ibkr_borrow_addOn import _rate_bars  # noqa: E402

log = logging.getLogger("short_interest")

DEFAULT_DURATION = "1 Y"
DEFAULT_BAR_SIZE = "1 day"
DEFAULT_PORT = 4002        # idle paper GW; never the live one for bulk history
DEFAULT_CLIENT_ID = 63     # outside clerk 1000-1029 / dailyStats 77 / board 20000
DEFAULT_CONCURRENCY = 4    # the rung that tamed the Error 162/366 slot cascade

CACHE_SUBDIR = "ibkr_rates"


def setup_logging(log_path: str | None = None) -> None:
    """Configure the named 'short_interest' logger.

    Deliberately not basicConfig(force=True) — see the same note in
    ShortInterrest_backfill_AddON.setup_logging.
    """
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return
    fmt = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    log.addHandler(stream)
    if log_path:
        os.makedirs(os.path.dirname(os.path.abspath(log_path)), exist_ok=True)
        handler = logging.FileHandler(log_path)
        handler.setFormatter(fmt)
        log.addHandler(handler)


def _bar_date(bar) -> str | None:
    """A bar's date as YYYY-MM-DD; daily bars carry a date, not a datetime."""
    value = getattr(bar, 'date', None)
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.strftime('%Y-%m-%d')
    return str(value)[:10] or None


def collect_symbols(paths: list[str]) -> list[str]:
    """Unique upper-cased Symbol values across every input file."""
    out: set[str] = set()
    for path in paths:
        try:
            df = pd.read_csv(path, sep='\t', dtype=str, keep_default_na=False,
                             usecols=['Symbol'])
        except (OSError, ValueError) as exc:
            log.error(f"{path}: could not read a Symbol column ({exc!r}) — skipping.")
            continue
        for value in df['Symbol']:
            sym = str(value).strip().upper()
            if sym and sym.lower() != 'nan':
                out.add(sym)
        log.info(f"{os.path.basename(path)}: {len(df)} row(s)")
    return sorted(out)


def merge_series(fee_bars, rebate_bars) -> list[dict]:
    """[{date, fee, rebate}] sorted by date, both rates already in PERCENT.

    The two series are fetched independently and can differ by a bar, so they
    are joined on date rather than zipped positionally; a date present in only
    one series keeps that value and leaves the other absent.
    """
    merged: dict[str, dict] = {}
    for bars, key in ((fee_bars, 'fee'), (rebate_bars, 'rebate')):
        for bar in bars or []:
            stamp = _bar_date(bar)
            if stamp is None:
                continue
            merged.setdefault(stamp, {'date': stamp})[key] = round(
                bar.close * 100.0, 4)
    return [merged[k] for k in sorted(merged)]


async def fetch_one(ib: IB, symbol: str, sem: asyncio.Semaphore,
                    duration: str, bar_size: str, timeout: float,
                    retries: int) -> list[dict] | None:
    """The merged daily rate series for one symbol, or None."""
    async with sem:
        contract = Stock(symbol, 'SMART', 'USD')
        try:
            qualified = await ib.qualifyContractsAsync(contract)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"{symbol}: qualify failed ({type(exc).__name__}: {exc})")
            return None
        if not qualified:
            log.info(f"{symbol}: no SMART/USD contract")
            return None
        contract = qualified[0]

        fee_bars = await _rate_bars(ib, contract, 'FEE_RATE', timeout, retries,
                                    '', symbol, duration, bar_size)
        if not fee_bars:
            # No fee series means no borrow data worth having; skip the second
            # request rather than pay for it (same call the nightly addon makes).
            log.info(f"{symbol}: no FEE_RATE bars")
            return None
        rebate_bars = await _rate_bars(ib, contract, 'REBATE_RATE', timeout,
                                       retries, '', symbol, duration, bar_size)
        return merge_series(fee_bars, rebate_bars) or None


def write_cache(cache_dir: str, symbol: str, series: list[dict],
                duration: str) -> None:
    payload = {
        'symbol': symbol,
        'fetched': date.today().isoformat(),
        'duration': duration,
        'daily': series,
    }
    path = os.path.join(cache_dir, f"{symbol}.json")
    tmp = f"{path}.tmp"
    with open(tmp, 'w') as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)      # atomic: a crash can't leave a truncated cache


async def run(ib: IB, symbols: list[str], cache_dir: str, args) -> tuple:
    sem = asyncio.Semaphore(args.concurrency)
    done = failed = 0
    tasks = [asyncio.ensure_future(
        fetch_one(ib, sym, sem, args.duration, args.bar_size,
                  args.timeout, args.retries)) for sym in symbols]
    for i, (sym, task) in enumerate(zip(symbols, tasks), 1):
        try:
            series = await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning(f"{sym}: {type(exc).__name__}: {exc}")
            series = None
        if series:
            write_cache(cache_dir, sym, series, args.duration)
            done += 1
        else:
            failed += 1
        if i % 50 == 0 or i == len(symbols):
            log.info(f"  {i}/{len(symbols)} symbols — cached {done}, no data {failed}")
    return done, failed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=('Fetch a year of IBKR FEE_RATE / REBATE_RATE daily bars for '
                     'every symbol in the input TSV(s) into cache/ibkr_rates/, '
                     'for ShortInterrest_backfill_AddON.py --ibkr-rates.'),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', '-i', required=True, nargs='+', metavar='FILE',
                        dest='input_files',
                        help='Input .tsv file(s) with a Symbol column; glob '
                             'patterns are accepted.')
    parser.add_argument('--cache-dir', metavar='DIR', default=None,
                        help='Raw-download cache (default: ./cache next to this '
                             f'script); rates land in its {CACHE_SUBDIR}/ subdir.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=DEFAULT_PORT,
                        help=f'Gateway port (default {DEFAULT_PORT} = PAPER). '
                             'Use the idle paper GW: the live one drops bulk '
                             'historical requests under contention.')
    parser.add_argument('--clientID', type=int, default=DEFAULT_CLIENT_ID,
                        dest='client_id',
                        help=f'API clientId (default {DEFAULT_CLIENT_ID}).')
    parser.add_argument('--concurrency', type=int, default=DEFAULT_CONCURRENCY,
                        help=f'Simultaneous symbols (default {DEFAULT_CONCURRENCY}).')
    parser.add_argument('--timeout', type=float, default=60.0,
                        help='Per-request timeout in seconds (default 60).')
    parser.add_argument('--retries', type=int, default=1,
                        help='Retries per rate series (default 1).')
    parser.add_argument('--duration', default=DEFAULT_DURATION,
                        help=f'IBKR durationStr (default {DEFAULT_DURATION!r}).')
    parser.add_argument('--bar-size', default=DEFAULT_BAR_SIZE,
                        help=f'IBKR barSizeSetting (default {DEFAULT_BAR_SIZE!r}).')
    parser.add_argument('--overwrite', action='store_true',
                        help='Re-fetch symbols that are already cached.')
    parser.add_argument('--limit', type=int, default=0, metavar='N',
                        help='Only fetch the first N uncached symbols (smoke test).')
    parser.add_argument('--log', '-l', metavar='FILE', dest='log_file', default=None,
                        help='Optional log file (appended).')
    args = parser.parse_args()

    setup_logging(args.log_file)

    paths: list[str] = []
    for pattern in args.input_files:
        hits = sorted(globmod.glob(pattern)) if any(c in pattern for c in '*?[') else [pattern]
        if not hits:
            log.warning(f"No files match {pattern!r}")
        paths.extend(h for h in hits if os.path.exists(h))
    if not paths:
        log.error("Nothing to do.")
        return 2

    cache_root = args.cache_dir or os.path.join(_SCRIPT_DIR, 'cache')
    cache_dir = os.path.join(cache_root, CACHE_SUBDIR)
    os.makedirs(cache_dir, exist_ok=True)

    symbols = collect_symbols(paths)
    if not args.overwrite:
        before = len(symbols)
        symbols = [s for s in symbols
                   if not os.path.exists(os.path.join(cache_dir, f"{s}.json"))]
        log.info(f"{before} unique symbol(s); {before - len(symbols)} already "
                 f"cached, {len(symbols)} to fetch.")
    else:
        log.info(f"{len(symbols)} unique symbol(s); --overwrite, fetching all.")
    if args.limit:
        symbols = symbols[:args.limit]
        log.info(f"--limit {args.limit}: fetching {len(symbols)}.")
    if not symbols:
        log.info("Every symbol is already cached — nothing to fetch.")
        return 0

    ib = IB()
    try:
        ib.connect(args.host, args.port, clientId=args.client_id, timeout=30)
    except Exception as exc:
        log.error(f"Could not connect to {args.host}:{args.port} "
                  f"clientId={args.client_id} ({exc!r}).")
        return 2
    log.info(f"Connected to {args.host}:{args.port} clientId={args.client_id}; "
             f"fetching {args.duration} of {args.bar_size} FEE_RATE/REBATE_RATE "
             f"bars for {len(symbols)} symbol(s), concurrency={args.concurrency}.")

    try:
        done, failed = ib.run(run(ib, symbols, cache_dir, args))
    except KeyboardInterrupt:
        log.warning("Interrupted — the cache keeps every symbol written so far; "
                    "re-run to resume.")
        return 1
    finally:
        ib.disconnect()

    log.info(f"Done: cached {done}/{len(symbols)}, no data for {failed}. "
             f"Cache: {cache_dir}")
    return 0 if done else 1


if __name__ == '__main__':
    sys.exit(main())
