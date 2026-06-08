#!/usr/bin/env python3
"""
trade_frequency_and_size.addOn.py
=================================
Superset of trade_frequency_addOn.py. Enriches a symbol TSV with the same
historical average inter-trade interval (ITI) columns AND, derived from the
*same* fetched TRADES bars (no extra IBKR request), the average trade size
(shares exchanged per transaction) for the RTH and ETH sessions. The trade-size
baseline is intended as a reference for detecting live surges in trading
momentum.

Four columns are appended:
  RTH_avgITI_sec   — avg ITI (seconds) over past N trading days, RTH session
  ETH_avgITI_sec   — avg ITI (seconds) over past N trading days, ETH sessions
  RTH_tradeSize    — avg shares per trade, RTH session, over the lookback window
  ETH_TradeSize    — avg shares per trade, ETH sessions, over the lookback window

Session definitions (US/Eastern):
  RTH  09:30 – 16:00   23,400 s
  ETH  04:00 – 09:30   (pre-market,  19,800 s)
       16:00 – 20:00   (after-hours, 14,400 s)
       combined        34,200 s

Lower ITI = more active stock (shorter gap between trades).
IQR outlier removal (Tukey 1.5×IQR fences) is applied across the per-day
samples before averaging the ITIs (unchanged from trade_frequency_addOn.py).

Trade size is computed as an *aggregate ratio* over the whole lookback
window per session: (sum of bar volume × lot multiplier) / (sum of bar trade
counts). This matches the definition of mean_size_{w}s in the downstream
real-time surge detector (trade_surge_mole/trade_mole_4.1.py), which compares
live shares-per-trade against this historical baseline. No IQR is applied to
the trade-size metric — the aggregate ratio is the intended statistic.

Usage
-----
    python trade_frequency_and_size.addOn.py \\
        --input-table data/nasdaq_symbols_data_priced_2026-05-19.tsv \\
        --past-days-lookback 10 \\
        --clientID 12

Requires: ib_insync, pandas. IB Gateway (live) on port 4001 by default.
"""

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

import pandas as pd
from ib_insync import IB, Stock

log = logging.getLogger(__name__)

# ── Session constants (seconds-since-midnight ET) ────────────────────────────

ET = ZoneInfo("America/New_York")

_RTH_START     = 9 * 3600 + 30 * 60   # 09:30 = 34,200
_RTH_END       = 16 * 3600             # 16:00 = 57,600
RTH_SEC        = _RTH_END - _RTH_START  # 23,400

_ETH_MOR_START = 4 * 3600              # 04:00 = 14,400
_ETH_MOR_END   = _RTH_START            # 09:30
_ETH_EVE_START = _RTH_END              # 16:00
_ETH_EVE_END   = 20 * 3600             # 20:00 = 72,000
ETH_SEC = (_ETH_MOR_END - _ETH_MOR_START) + (_ETH_EVE_END - _ETH_EVE_START)  # 34,200

# IBKR caps 1-min bar requests at 30 calendar days per request.
# 30 cal days ~ 26 weekdays after removing weekends; the +4 day buffer for
# holidays/weekends leaves ~22 reliable trading days.
_MAX_LOOKBACK_WARN = 22

# Bar size for the frequency fetch. The session boundaries (04:00 / 09:30 /
# 16:00 / 20:00) all fall on 30-minute marks, so a "30 mins" bar never straddles
# a boundary: per-session barCount sums (and therefore the ITI values) are
# identical to "1 min" bars, while each request carries ~30x less data — which
# avoids tripping IBKR pacing / data-farm-inactive limits.  Cannot go coarser:
# 09:30 is not aligned to a 1-hour boundary.  The same property holds for the
# per-session volume sums used by RTH_tradeSize / ETH_TradeSize.
_FREQ_BAR_SIZE = "30 mins"

# IBKR reqHistoricalData TRADES volume on THIS feed is already in actual shares,
# not round lots — so the multiplier is 1. Verified empirically 2026-06-07:
# AAPL daily TRADES bars (useRTH=True) returned raw volume ~25–38 M shares/day,
# matching the real-share order of magnitude (yfinance composite tape ~44–70 M;
# IBKR's RTH-only SMART TRADES running ~0.5–0.6× the consolidated tape is the
# expected coverage difference, not a unit error). ×100 gave ~2.5 *billion*
# shares/day for AAPL — physically impossible — and inflated RTH_tradeSize to an
# implausible ~24k-share median across the 100-symbol test. If a future feed/API
# change reverts to round-lot reporting, set this back to 100.
_IBKR_VOLUME_LOT_MULTIPLIER = 1


# ── Helpers ──────────────────────────────────────────────────────────────────

def past_trading_days(n: int) -> list:
    """Return the last n weekdays (Mon–Fri) as YYYYMMDD strings, newest first."""
    days, d = [], datetime.now(tz=ET).date() - timedelta(days=1)
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.strftime("%Y%m%d"))
        d -= timedelta(days=1)
    return days


def _bar_et_components(bar):
    """
    Extract (seconds_since_midnight_ET, 'YYYYMMDD') from bar.date.
    bar.date may be a naive datetime (treated as ET) or a string.
    Returns (None, None) on parse failure.
    """
    d = bar.date
    if isinstance(d, str):
        parts = d.strip().split()
        if len(parts) < 2 or ":" not in parts[1]:
            return None, None
        try:
            dt = datetime.strptime(f"{parts[0]} {parts[1]}", "%Y%m%d %H:%M:%S")
            dt = dt.replace(tzinfo=ET)
        except ValueError:
            return None, None
    elif isinstance(d, datetime):
        dt = d.replace(tzinfo=ET) if d.tzinfo is None else d.astimezone(ET)
    else:
        return None, None

    tod = dt.hour * 3600 + dt.minute * 60 + dt.second
    return tod, dt.strftime("%Y%m%d")


def iqr_filtered_mean(values: list) -> Optional[float]:
    """
    Mean after removing Tukey IQR outliers (1.5×IQR fence).
    Falls back to plain mean when fewer than 4 values (not enough for fences).
    Returns None for empty input.
    """
    if not values:
        return None
    if len(values) < 4:
        return sum(values) / len(values)
    s = sorted(values)
    n = len(s)
    q1 = s[n // 4]
    q3 = s[(3 * n) // 4]
    iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    kept = [v for v in s if lo <= v <= hi]
    return sum(kept) / len(kept) if kept else sum(values) / len(values)


def compute_session_itis(bars, days: list) -> tuple:
    """
    Scan 1-minute bars (useRTH=False) and compute per-day trade counts for RTH
    and ETH sessions. Returns (rth_itis, eth_itis) as lists of per-day ITI values.
    Days with zero trades in a session are excluded from that session's list.
    """
    rth_by_date: dict = {}
    eth_by_date: dict = {}

    for bar in bars:
        tod, date_str = _bar_et_components(bar)
        if tod is None:
            continue
        bc = int(getattr(bar, "barCount", 0) or 0)
        if bc <= 0:
            continue

        if _RTH_START <= tod < _RTH_END:
            rth_by_date[date_str] = rth_by_date.get(date_str, 0) + bc

        if (_ETH_MOR_START <= tod < _ETH_MOR_END) or (_ETH_EVE_START <= tod < _ETH_EVE_END):
            eth_by_date[date_str] = eth_by_date.get(date_str, 0) + bc

    rth_itis, eth_itis = [], []
    for day in days:
        rth_trades = rth_by_date.get(day, 0)
        if rth_trades > 0:
            rth_itis.append(RTH_SEC / rth_trades)
        eth_trades = eth_by_date.get(day, 0)
        if eth_trades > 0:
            eth_itis.append(ETH_SEC / eth_trades)

    return rth_itis, eth_itis


def compute_session_volumes(bars) -> tuple:
    """
    Aggregate average volume-per-trade for RTH and ETH sessions over the full
    set of bars (the whole lookback window). For each session:

        volPerTrade = (Σ bar.volume × _IBKR_VOLUME_LOT_MULTIPLIER) / Σ bar.barCount

    i.e. total shares traded / total number of trades — the historical analog of
    the live detector's mean_size_{w}s. No per-day split or IQR filtering: the
    aggregate ratio is the intended statistic.

    Returns (rth_volPerTrade, eth_volPerTrade); either is None when that session
    had zero trades across the window.
    """
    rth_vol_sum = 0.0
    eth_vol_sum = 0.0
    rth_trades = 0
    eth_trades = 0

    for bar in bars:
        tod, _date_str = _bar_et_components(bar)
        if tod is None:
            continue
        bc = int(getattr(bar, "barCount", 0) or 0)
        if bc <= 0:
            continue
        # bar.volume can be -1/None on empty bars — clamp to >= 0.
        vol = float(getattr(bar, "volume", 0) or 0)
        if vol < 0:
            vol = 0.0

        if _RTH_START <= tod < _RTH_END:
            rth_vol_sum += vol
            rth_trades  += bc

        if (_ETH_MOR_START <= tod < _ETH_MOR_END) or (_ETH_EVE_START <= tod < _ETH_EVE_END):
            eth_vol_sum += vol
            eth_trades  += bc

    rth_vpt = (rth_vol_sum * _IBKR_VOLUME_LOT_MULTIPLIER / rth_trades) if rth_trades > 0 else None
    eth_vpt = (eth_vol_sum * _IBKR_VOLUME_LOT_MULTIPLIER / eth_trades) if eth_trades > 0 else None
    return rth_vpt, eth_vpt


def build_output_path(input_path: str) -> str:
    root, ext = os.path.splitext(input_path)
    return f"{root}_freqSizeEnriched{ext}"


# Cap-limited per-symbol WARNING for freq-fetch errors. We previously swallowed
# every exception silently, which masked the reqId-slot leak that broke Step 3
# (cancelling `reqHistoricalDataAsync` via an outer wait_for never called
# ib_insync's `cancelHistoricalData`, so the Gateway hit its 50-simultaneous
# cap and stopped responding). Logging is capped so a bad night doesn't dump
# thousands of WARNING lines.
_FREQ_ERR_BUDGET = 25
_freq_err_count = 0


def _log_freq_error(symbol: str, exc: BaseException) -> None:
    """Log a freq-fetch failure, capped at _FREQ_ERR_BUDGET lines per process."""
    global _freq_err_count
    if _freq_err_count >= _FREQ_ERR_BUDGET:
        return
    _freq_err_count += 1
    log.warning("freq fetch %s failed (%s): %s", symbol, type(exc).__name__, exc)
    if _freq_err_count == _FREQ_ERR_BUDGET:
        log.warning("freq fetch: error log budget exhausted; "
                    "suppressing further per-symbol warnings.")


# ── Per-symbol IBKR fetch ────────────────────────────────────────────────────

def fetch_freq(ib: IB, symbol: str, days: list, delay_ms: int) -> tuple:
    """
    Fetch 1-min TRADES bars for symbol and compute IQR-filtered avg ITI for
    RTH and ETH sessions across the supplied trading days, plus aggregate avg
    volume-per-trade for each session.

    Returns (rth_iti_avg, eth_iti_avg, rth_volPerTrade, eth_volPerTrade);
    any element may be float('nan') on error.
    """
    nan = float("nan")

    contract = Stock(symbol, "SMART", "USD")
    try:
        qualified = ib.qualifyContracts(contract)
        if not qualified:
            log.warning(f"{symbol}: qualifyContracts returned nothing — skipping")
            return nan, nan, nan, nan
        contract = qualified[0]
    except Exception as exc:
        log.warning(f"{symbol}: qualifyContracts error ({exc}) — skipping")
        return nan, nan, nan, nan

    n_cal = min(len(days) + 4, 30)
    try:
        bars = ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr=f"{n_cal} D",
            barSizeSetting="1 min",
            whatToShow="TRADES",
            useRTH=False,
            formatDate=1,
            keepUpToDate=False,
            timeout=60,
        )
    except Exception as exc:
        log.warning(f"{symbol}: reqHistoricalData error ({exc}) — skipping")
        ib.sleep(delay_ms / 1000.0)
        return nan, nan, nan, nan

    ib.sleep(delay_ms / 1000.0)

    if not bars:
        log.warning(f"{symbol}: no historical bars returned")
        return nan, nan, nan, nan

    rth_itis, eth_itis = compute_session_itis(bars, days)
    rth_vpt, eth_vpt   = compute_session_volumes(bars)

    rth_avg = iqr_filtered_mean(rth_itis)
    eth_avg = iqr_filtered_mean(eth_itis)
    return (
        rth_avg if rth_avg is not None else nan,
        eth_avg if eth_avg is not None else nan,
        rth_vpt if rth_vpt is not None else nan,
        eth_vpt if eth_vpt is not None else nan,
    )


async def fetch_freq_async(ib: IB, symbol: str, days: list, sem: asyncio.Semaphore,
                           timeout: int = 30, retries: int = 2,
                           bar_size: str = _FREQ_BAR_SIZE,
                           contract: Optional[Stock] = None) -> tuple:
    """Async sibling of fetch_freq for concurrent batch fetching.

    `sem` bounds the number of in-flight requests. A failed qualifyContracts is
    treated as a permanent bad-symbol error (no retry); an empty/timed-out
    historical request is retried up to `retries` times before recording NaN.
    Uses `bar_size` (default "30 mins") — see _FREQ_BAR_SIZE for why this yields
    the same ITI (and per-session volume sums) as 1-min bars. Reuses
    compute_session_itis / compute_session_volumes / iqr_filtered_mean.

    If a pre-qualified `contract` is supplied (e.g. from an earlier
    `fetch_last_rth_close_async` call in the same run), the qualify round-trip
    is skipped — this roughly halves the per-symbol IBKR cost.

    Returns (rth_iti_avg, eth_iti_avg, rth_volPerTrade, eth_volPerTrade);
    any element may be float('nan') on error.
    """
    nan = float("nan")
    async with sem:
        if contract is None:
            contract = Stock(symbol, "SMART", "USD")
            try:
                qualified = await ib.qualifyContractsAsync(contract)
                if not qualified:
                    return nan, nan, nan, nan
                contract = qualified[0]
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log_freq_error(symbol, exc)
                return nan, nan, nan, nan

        # Pass `timeout` into reqHistoricalDataAsync directly instead of
        # wrapping it in an outer asyncio.wait_for: ib_insync's own internal
        # timeout handler calls `cancelHistoricalData(reqId)` on expiry, which
        # releases the server-side reqId slot. The previous outer-wait_for
        # pattern propagated CancelledError into ib_insync before its
        # TimeoutError branch could run, leaking reqIds until the Gateway hit
        # its 50-simultaneous-historical-requests cap and went silent. On
        # timeout, ib_insync returns an empty BarDataList (not an exception),
        # so `if bars: break` is the right check.
        n_cal = min(len(days) + 4, 30)
        bars = None
        for attempt in range(retries + 1):
            try:
                bars = await ib.reqHistoricalDataAsync(
                    contract,
                    endDateTime="",
                    durationStr=f"{n_cal} D",
                    barSizeSetting=bar_size,
                    whatToShow="TRADES",
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                    timeout=timeout,
                )
                if bars:
                    break
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                bars = None
                _log_freq_error(symbol, exc)
            if attempt < retries:
                await asyncio.sleep(1.0)

        if not bars:
            return nan, nan, nan, nan

        rth_itis, eth_itis = compute_session_itis(bars, days)
        rth_vpt, eth_vpt   = compute_session_volumes(bars)
        rth_avg = iqr_filtered_mean(rth_itis)
        eth_avg = iqr_filtered_mean(eth_itis)
        return (
            rth_avg if rth_avg is not None else nan,
            eth_avg if eth_avg is not None else nan,
            rth_vpt if rth_vpt is not None else nan,
            eth_vpt if eth_vpt is not None else nan,
        )


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Enrich a symbol TSV with avg inter-trade interval and avg "
                    "trade size (shares per trade) for RTH and ETH sessions.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input-table", required=True, metavar="PATH",
                        help="Input TSV (must have a Symbol column)")
    parser.add_argument("--past-days-lookback", type=int, default=10, metavar="N",
                        help="Number of past trading days to average over")
    parser.add_argument("--clientID", type=int, default=12, metavar="ID",
                        help="IBKR API client ID")
    parser.add_argument("--port", type=int, default=4001, metavar="PORT",
                        help="IB Gateway/TWS port (4001=live GW, 4002=paper GW, 7496=live TWS)")
    parser.add_argument("--host", default="127.0.0.1", metavar="HOST")
    parser.add_argument("--output", default=None, metavar="PATH",
                        help="Output TSV path (default: <input>_freqSizeEnriched.tsv)")
    parser.add_argument("--delay-ms", type=int, default=500, metavar="MS",
                        help="Sleep between per-symbol requests; ib_insync auto-handles pacing errors")
    parser.add_argument("--loglevel", default="INFO",
                        choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log", default=None, metavar="PATH",
                        help="Also write log output to this file (parent dirs created if needed)")
    args = parser.parse_args()

    handlers: list = [logging.StreamHandler()]
    if args.log:
        log_dir = os.path.dirname(args.log)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(args.log))

    logging.basicConfig(
        level=getattr(logging, args.loglevel),
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )

    if args.past_days_lookback > _MAX_LOOKBACK_WARN:
        log.warning(
            f"--past-days-lookback {args.past_days_lookback} exceeds the recommended limit of "
            f"{_MAX_LOOKBACK_WARN} trading days. IBKR caps 1-min bar requests at 30 calendar days; "
            f"data for older days may be incomplete."
        )

    if not os.path.isfile(args.input_table):
        log.error(f"Input file not found: {args.input_table}")
        sys.exit(1)

    df = pd.read_csv(args.input_table, sep="\t")
    if "Symbol" not in df.columns:
        log.error("Input TSV must have a 'Symbol' column.")
        sys.exit(1)

    symbols = df["Symbol"].tolist()
    total = len(symbols)
    log.info(f"Loaded {total} symbols from {args.input_table}")

    days = past_trading_days(args.past_days_lookback)
    log.info(f"Lookback: {args.past_days_lookback} trading days  ({days[-1]} → {days[0]})")

    ib = IB()
    log.info(f"Connecting to {args.host}:{args.port} clientId={args.clientID} ...")
    try:
        ib.connect(args.host, args.port, clientId=args.clientID, timeout=20)
    except ConnectionRefusedError:
        log.error(f"Connection refused — is IB Gateway/TWS running on port {args.port}?")
        sys.exit(1)
    except Exception as exc:
        log.error(f"Connection failed: {exc}")
        sys.exit(1)
    if not ib.isConnected():
        log.error("ib.connect() returned but isConnected() is False.")
        sys.exit(1)
    log.info("Connected.")

    rth_results: list = []
    eth_results: list = []
    rth_vol_results: list = []
    eth_vol_results: list = []
    t_start = time.perf_counter()

    try:
        for i, symbol in enumerate(symbols, start=1):
            rth, eth, rth_vol, eth_vol = fetch_freq(ib, str(symbol), days, args.delay_ms)
            rth_results.append(rth)
            eth_results.append(eth)
            rth_vol_results.append(rth_vol)
            eth_vol_results.append(eth_vol)

            if i % 100 == 0 or i == total:
                elapsed = time.perf_counter() - t_start
                rate = i / elapsed if elapsed > 0 else 0
                log.info(
                    f"[{i}/{total}] {elapsed:.0f}s elapsed  "
                    f"({rate:.1f} symbols/s)  last={symbol}"
                )
    finally:
        ib.disconnect()
        log.info("Disconnected.")

    df["RTH_avgITI_sec"] = rth_results
    df["ETH_avgITI_sec"] = eth_results
    df["RTH_tradeSize"]  = rth_vol_results
    df["ETH_TradeSize"]  = eth_vol_results
    df["RTH_avgITI_sec"] = df["RTH_avgITI_sec"].fillna(44444)
    df["ETH_avgITI_sec"] = df["ETH_avgITI_sec"].fillna(44444)
    df["RTH_tradeSize"]  = df["RTH_tradeSize"].fillna(44444)
    df["ETH_TradeSize"]  = df["ETH_TradeSize"].fillna(44444)

    valid_rth      = sum(1 for v in rth_results if v == v)
    valid_eth      = sum(1 for v in eth_results if v == v)
    valid_rth_size = sum(1 for v in rth_vol_results if v == v)
    valid_eth_size = sum(1 for v in eth_vol_results if v == v)
    log.info(
        f"RTH_avgITI_sec: {valid_rth}/{total} populated  "
        f"ETH_avgITI_sec: {valid_eth}/{total} populated  "
        f"RTH_tradeSize: {valid_rth_size}/{total} populated  "
        f"ETH_TradeSize: {valid_eth_size}/{total} populated"
    )

    output_path = args.output or build_output_path(args.input_table)
    df.to_csv(output_path, sep="\t", index=False, float_format="%.4f")
    elapsed_total = time.perf_counter() - t_start
    log.info(f"Wrote {len(df)} rows → {output_path}  ({elapsed_total:.0f}s total)")


if __name__ == "__main__":
    main()
