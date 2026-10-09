#!/usr/bin/env python3
"""
ibkr_borrow_addOn.py
====================
Borrow-market data (cost-to-borrow, rebate rate, shortable shares) fetched from
the IBKR API, for Step 3.6 of daily_universe_pipeline-2.py.

WHY THIS EXISTS
---------------
The seven borrow columns of the nightly universe file
(CTB_FeePct, CTB_RebatePct, CTB_FeeDelta5d, Borrow_Available,
Borrow_AvailDelta5dPct, Borrow_UtilProxy, Borrow_Source) had two web sources in
ShortInterrest_AddON.py, and by 2026-08 both were dead:

  * iBorrowDesk (https://iborrowdesk.com/api/ticker/{sym}) — the source of six
    of the seven — stopped answering on 2026-07-22. Every request since fails
    with RemoteDisconnected; the site root returns HTTP 403 and a browser-UA
    request is reset outright, so it is a block, not a rate limit. Every
    stocks_universe_*.tsv from 2026-07-22 onward is 0/N on all seven columns.
  * The IBKR shortstock FTP (ftp3.interactivebrokers.com:21) — the ONLY source
    of CTB_RebatePct — has never once succeeded from this network; ports
    21/80/443/990 on that host are all unreachable. CTB_RebatePct was therefore
    blank in every file the project has ever written.

IBKR's own API serves the same data over the connection the pipeline already
holds, so no new vendor is involved:

  reqHistoricalData(whatToShow='FEE_RATE')     daily borrow-fee bars, 1y history
  reqHistoricalData(whatToShow='REBATE_RATE')  daily rebate-rate bars
  reqMktData(genericTickList='236')            ticker.shortableShares

UNITS: IBKR returns these rates as ANNUALIZED DECIMAL FRACTIONS, so every close
is multiplied by 100 to land in the percent units the existing columns use.
Verified against stocks_universe_2026-07-21.tsv (the last file with live
iBorrowDesk data), anchoring the request at 20:15 ET on that session: 11 of 12
symbols reproduce the old values to 4 significant figures (AAME 4.0000 vs
4.0021, ABTS 140.20 vs 140.1962, ACCL 125.72 vs 125.7188). Cross-check on the
units: fee + rebate is ~0.0338 for both AAPL and AAME, i.e. one benchmark rate
behind both series, which only holds if these are fractions.

CTB_FeeDelta5d from here is also more correct than what it replaces:
ShortInterrest_AddON.borrow_history_metrics indexes rows[-6] of a sparse
iBorrowDesk array, i.e. "6 records back", which for a thinly-quoted symbol can
be months. Daily bars give a true 5-session delta.

WHAT IBKR CANNOT GIVE US
------------------------
shortableShares is a live snapshot with no historical whatToShow, so
Borrow_AvailDelta5dPct and Borrow_UtilProxy cannot be computed on night one.
They are derived instead from a rolling store this module maintains
(data/borrow_avail_history.tsv): blank for ~6 sessions, then the delta comes
alive, and the util proxy after ~20. Self-healing, with nothing fabricated in
the meantime.

Do NOT seed that store from the pre-2026-07-22 Borrow_Available column. Those
are iBorrowDesk's rounded values (AAME 95,000) against IBKR's precise ones
(105,554); mixing the two would corrupt every delta spanning the seam.

CONSUMED BY
-----------
build_borrow_map() returns {SYMBOL: {fee, rebate, available, fee_delta_5d,
avail_delta_5d_pct, util_proxy}}, which is passed to
ShortInterrest_AddON.enrich_dataframe(..., ibkr_borrow=...) at Step 8. That is
the whole interface; this module knows nothing about the output schema.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock

try:
    import pandas_market_calendars as _mcal
except ImportError:
    _mcal = None

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

# Enough daily bars to reach 5 sessions back with margin for holidays. The
# probe returned 9-10 bars for "10 D", so 15 calendar days comfortably clears
# the 6 bars fee_delta_5d needs.
_BORROW_DURATION = "15 D"
_BORROW_BAR_SIZE = "1 day"

# Sessions back for the fee/availability deltas, and the window the utilisation
# proxy normalises against. Both mirror ShortInterrest_AddON's
# BORROW_DELTA_DAYS / BORROW_HISTORY_DAYS so the columns keep their meaning.
BORROW_DELTA_SESSIONS = 5
BORROW_HISTORY_SESSIONS = 20

# Sessions of availability snapshots to keep on disk. Only the newest
# BORROW_HISTORY_SESSIONS are ever read; the rest is slack for gap tolerance.
AVAIL_HISTORY_KEEP_SESSIONS = 40

AVAIL_HISTORY_COLUMNS = ("Date", "Symbol", "ShortableShares")

# Market-data lines are a limited resource on the account (~100 simultaneous),
# so shortableShares is harvested in batches that are cancelled before the next
# one starts.
_MKTDATA_BATCH = 50
_MKTDATA_SETTLE_SEC = 8.0

_BORROW_ERR_BUDGET = 25
_borrow_err_count = 0


def _log_borrow_error(symbol: str, exc: BaseException) -> None:
    """Log a borrow-fetch failure, capped at _BORROW_ERR_BUDGET lines."""
    global _borrow_err_count
    if _borrow_err_count >= _BORROW_ERR_BUDGET:
        return
    _borrow_err_count += 1
    log.warning("borrow fetch %s failed (%s): %s", symbol, type(exc).__name__, exc)
    if _borrow_err_count == _BORROW_ERR_BUDGET:
        log.warning("borrow fetch: error log budget exhausted; "
                    "suppressing further per-symbol warnings.")


def session_date() -> str:
    """The session an ET-evening snapshot describes, as YYYY-MM-DD.

    Same convention as trade_frequency_and_size.addOn.past_trading_days: today
    counts once the after-hours session has closed (>= 20:00 ET), else the walk
    starts at yesterday. Holiday-aware via the NYSE calendar so a snapshot taken
    on a closed day is filed against the last real session rather than creating
    a phantom one that would skew the 5-session delta.
    """
    now = datetime.now(tz=ET)
    d = now.date()
    if now.hour < 20:
        d -= timedelta(days=1)

    if _mcal is not None:
        try:
            valid = _mcal.get_calendar("NYSE").valid_days(
                start_date=d - timedelta(days=15), end_date=d)
            if len(valid):
                return valid[-1].strftime("%Y-%m-%d")
        except Exception as exc:                       # pragma: no cover
            log.warning("session_date: NYSE calendar failed (%s); "
                        "falling back to a weekday walk.", exc)

    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ── Per-symbol fee/rebate fetch ──────────────────────────────────────────────

async def _rate_bars(ib: IB, contract, what_to_show: str, timeout: float,
                     retries: int, end_datetime: str, symbol: str,
                     duration: str = None, bar_size: str = None):
    """Daily bars for one of the two borrow-rate series, or None.

    `duration` / `bar_size` default to the nightly run's _BORROW_DURATION /
    _BORROW_BAR_SIZE ("15 D" of daily bars — just past the 6 the 5-session fee
    delta needs). A backfill asks for a far deeper window through these keywords
    rather than through a second copy of this request, which exists so the
    reqId-leak note below stays authoritative in one place.
    See ShortInterrest/ibkr_rates_fetch_AddON.py."""
    for attempt in range(retries + 1):
        try:
            # `timeout` goes INTO reqHistoricalDataAsync rather than an outer
            # asyncio.wait_for: ib_insync's own timeout branch calls
            # cancelHistoricalData(reqId), which releases the server-side slot.
            # An outer wait_for propagates CancelledError before that branch can
            # run and leaks reqIds until the Gateway hits its 50-simultaneous
            # cap and goes silent. See trade_frequency_and_size.addOn.py:413-421.
            bars = await ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end_datetime,
                durationStr=duration or _BORROW_DURATION,
                barSizeSetting=bar_size or _BORROW_BAR_SIZE,
                whatToShow=what_to_show,
                useRTH=True,
                formatDate=1,
                keepUpToDate=False,
                timeout=timeout,
            )
            if bars:
                return bars
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log_borrow_error(f"{symbol}/{what_to_show}", exc)
        if attempt < retries:
            await asyncio.sleep(1.0)
    return None


async def fetch_borrow_async(ib: IB, symbol: str, sem: asyncio.Semaphore,
                             timeout: float = 60.0, retries: int = 0,
                             contract: Optional[Stock] = None,
                             end_datetime: str = "") -> tuple:
    """(fee_pct, rebate_pct, fee_delta_5d) for one symbol; NaN on failure.

    Two historical requests per symbol (FEE_RATE then REBATE_RATE). A
    pre-qualified `contract` — Step 2 of the pipeline already has one for every
    symbol — skips the qualify round-trip.

    All three values are in PERCENT: IBKR returns annualized decimal fractions,
    so every close is scaled by 100. `fee_delta_5d` is the change over
    BORROW_DELTA_SESSIONS sessions, NaN when the series is too short.

    `end_datetime` is passed straight through, so "" means now (what the nightly
    run wants) and an IBKR-format timestamp ("YYYYMMDD HH:MM:SS US/Eastern")
    anchors the request on a past session. That keyword is the only thing a
    point-in-time backfill would need, which is why it exists here rather than
    in a second copy of this function.
    """
    nan = float("nan")
    async with sem:
        if contract is None:
            contract = Stock(symbol, "SMART", "USD")
            try:
                qualified = await ib.qualifyContractsAsync(contract)
                if not qualified:
                    return nan, nan, nan
                contract = qualified[0]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log_borrow_error(symbol, exc)
                return nan, nan, nan

        fee_bars = await _rate_bars(ib, contract, "FEE_RATE", timeout, retries,
                                    end_datetime, symbol)
        if not fee_bars:
            # No fee series means no borrow data worth having; skip the second
            # request rather than pay for it.
            return nan, nan, nan

        fee = round(fee_bars[-1].close * 100.0, 4)
        delta = nan
        if len(fee_bars) > BORROW_DELTA_SESSIONS:
            then = fee_bars[-1 - BORROW_DELTA_SESSIONS].close
            delta = round((fee_bars[-1].close - then) * 100.0, 2)

        rebate_bars = await _rate_bars(ib, contract, "REBATE_RATE", timeout,
                                       retries, end_datetime, symbol)
        # A negative rebate is normal and correct on hard-to-borrow names
        # (the short pays rather than earns) — do not clamp it.
        rebate = round(rebate_bars[-1].close * 100.0, 4) if rebate_bars else nan

        return fee, rebate, delta


# ── Shortable shares (live snapshot, generic tick 236) ───────────────────────

def fetch_shortable_shares(ib: IB, contracts: list,
                           batch: int = _MKTDATA_BATCH,
                           settle: float = _MKTDATA_SETTLE_SEC) -> dict:
    """{SYMBOL: shortable shares} for the qualified contracts given.

    Market data is a subscription, not a request/response, so this cannot go
    through _run_concurrent: it subscribes to a batch, waits for the ticks to
    land, harvests, then cancels every line in the batch before moving on.
    Batching is what keeps us inside the account's simultaneous market-data
    line limit (~100); measured 58/60 symbols in 8 s.
    """
    out: dict = {}
    live = [c for c in contracts if c is not None]
    if not live:
        return out

    for start in range(0, len(live), batch):
        chunk = live[start:start + batch]
        tickers = []
        try:
            for contract in chunk:
                tickers.append(ib.reqMktData(contract, "236", False, False))
            ib.sleep(settle)
            for ticker in tickers:
                shares = ticker.shortableShares
                # NaN is ib_insync's "no tick arrived"; != itself detects it.
                if shares == shares and shares is not None:
                    out[ticker.contract.symbol.strip().upper()] = float(shares)
        except Exception as exc:
            log.warning("shortableShares batch at %d failed (%r); continuing.",
                        start, exc)
        finally:
            # Always release the lines, even on error — a leaked subscription
            # eats a slot for the rest of the session.
            for contract in chunk:
                try:
                    ib.cancelMktData(contract)
                except Exception:
                    pass
        log.debug("  shortableShares: %d/%d symbols", len(out), len(live))

    return out


# ── Rolling availability history ─────────────────────────────────────────────

def load_avail_history(path: str) -> dict:
    """{SYMBOL: [(date, shares), ...] oldest first} from the rolling store."""
    history: dict = {}
    if not os.path.exists(path):
        return history
    try:
        with open(path) as fh:
            header = fh.readline().rstrip("\n").split("\t")
            if tuple(header) != AVAIL_HISTORY_COLUMNS:
                log.warning("%s: unexpected header %s — ignoring the store.",
                            path, header)
                return {}
            for line in fh:
                parts = line.rstrip("\n").split("\t")
                if len(parts) != 3:
                    continue
                try:
                    history.setdefault(parts[1], []).append(
                        (parts[0], float(parts[2])))
                except ValueError:
                    continue
    except OSError as exc:
        log.warning("Could not read %s (%r); treating it as empty.", path, exc)
        return {}
    for series in history.values():
        series.sort()
    return history


def append_avail_snapshot(path: str, date: str, avail: dict) -> dict:
    """Record today's shortableShares and return the UPDATED history.

    Returns rather than requiring a second load_avail_history() call because
    the deltas are off by one session if they are computed against a history
    that excludes tonight: avail_metrics indexes recent[-1-5], which is
    "5 sessions before now" only when recent[-1] IS now. Handing the caller
    the post-append history makes that ordering impossible to get wrong.

    Existing rows for `date` are replaced, so re-running the same night is
    idempotent instead of stacking duplicates that would corrupt the delta.
    The whole file is rewritten; it holds at most a few tens of thousands of
    rows.
    """
    history = load_avail_history(path)
    if not avail:
        log.info("Borrow availability store: nothing to record for %s.", date)
        return history

    rows: list = []
    for symbol, series in history.items():
        for stamp, shares in series:
            if stamp != date:
                rows.append((stamp, symbol, shares))
    rows.extend((date, symbol, shares) for symbol, shares in avail.items())

    # Bound the file: keep only the newest AVAIL_HISTORY_KEEP_SESSIONS dates.
    keep = set(sorted({r[0] for r in rows})[-AVAIL_HISTORY_KEEP_SESSIONS:])
    rows = sorted(r for r in rows if r[0] in keep)

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        fh.write("\t".join(AVAIL_HISTORY_COLUMNS) + "\n")
        for stamp, symbol, shares in rows:
            fh.write(f"{stamp}\t{symbol}\t{shares}\n")
    os.replace(tmp, path)          # atomic: a crash can't truncate the store
    log.info("Borrow availability store: %d symbols recorded for %s "
             "(%d rows over %d sessions in %s).",
             len(avail), date, len(rows), len(keep), os.path.basename(path))

    updated: dict = {}
    for stamp, symbol, shares in rows:
        updated.setdefault(symbol, []).append((stamp, shares))
    return updated


def avail_metrics(series: list, avail_now: Optional[float]) -> tuple:
    """(avail_delta_5d_pct, util_proxy) from one symbol's snapshot series.

    Same two formulas ShortInterrest_AddON.borrow_history_metrics uses, applied
    to our own accumulated snapshots instead of iBorrowDesk's `daily` array.
    Both are None until the store is deep enough, which is the point: an early
    value computed off two sessions would be noise wearing a real column's name.
    The delta needs BORROW_DELTA_SESSIONS + 1 sessions, the util proxy the full
    BORROW_HISTORY_SESSIONS, so they come alive on different nights.

    `series` must include tonight's snapshot — see append_avail_snapshot.
    """
    if avail_now is None or not series:
        return None, None

    recent = series[-BORROW_HISTORY_SESSIONS:]
    delta_pct = None
    if len(recent) > BORROW_DELTA_SESSIONS:
        then = recent[-1 - BORROW_DELTA_SESSIONS][1]
        if then:
            delta_pct = round((avail_now - then) / then * 100.0, 1)

    # The util proxy needs the FULL window, not merely a non-empty one. With a
    # shallow store the running max is close to avail_now, so a fresh store
    # yields util ~= 0.0 for every symbol — a real-looking "nothing is borrowed"
    # reading manufactured out of one data point. It is also the window the
    # UTIL90 threshold (>= 0.90) was calibrated against on iBorrowDesk's
    # ~20-entry arrays, so emitting it early would mis-scale the flag as well.
    util = None
    if len(recent) >= BORROW_HISTORY_SESSIONS:
        peak = max(shares for _, shares in recent)
        if peak > 0:
            util = round(1.0 - avail_now / peak, 3)

    return delta_pct, util


# ── Assembly ─────────────────────────────────────────────────────────────────

def build_borrow_map(symbols: list, rates: list, avail: dict,
                     history: dict) -> dict:
    """{SYMBOL: {fee, rebate, available, fee_delta_5d, avail_delta_5d_pct,
    util_proxy}} — the dict ShortInterrest_AddON.enrich_dataframe consumes.

    `rates` is positionally aligned with `symbols` and holds the 3-tuples
    fetch_borrow_async returned; `avail` comes from fetch_shortable_shares and
    `history` MUST be the post-append store append_avail_snapshot returned (see
    that function for why an unappended history shifts the delta). A symbol with
    no usable
    value at all is omitted entirely, so the SI addon sees a missing key rather
    than a dict of Nones and leaves Borrow_Source blank.
    """
    def clean(value):
        # NaN (fetch failure) becomes None, which is what compute_symbol_metrics
        # tests for. `!= itself` is the NaN check.
        if value is None or value != value:
            return None
        return value

    out: dict = {}
    for symbol, rate in zip(symbols, rates):
        key = str(symbol).strip().upper()
        fee, rebate, fee_delta = (clean(v) for v in rate)
        available = clean(avail.get(key))
        delta_pct, util = avail_metrics(history.get(key, []), available)

        entry = {
            "fee": fee,
            "rebate": rebate,
            "available": available,
            "fee_delta_5d": fee_delta,
            "avail_delta_5d_pct": delta_pct,
            "util_proxy": util,
        }
        if any(v is not None for v in entry.values()):
            out[key] = entry
    return out
