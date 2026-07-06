#!/usr/bin/env python3
"""
ShortInterrest_AddON.py — enrich a stock-universe TSV with short-interest /
short-squeeze metrics assembled from free web sources (no TWS API connection).

Sources (all free):
  1. FINRA Equity Short Interest (bi-monthly)   api.finra.org (anonymous POST;
     per-symbol yfinance fallback when the API refuses or misses a symbol)
  2. FINRA Reg SHO daily short-sale volume      cdn.finra.org CNMSshvolYYYYMMDD.txt
  3. IBKR stock-loan borrow list                ftp3.interactivebrokers.com usa.txt
     (user 'shortstock', no password) -> current fee rate / rebate / available
  4. iBorrowDesk history (unofficial JSON)      iborrowdesk.com/api/ticker/<SYM>
     -> 5-day fee/availability deltas and a utilization proxy
  5. Nasdaq Reg SHO threshold list              nasdaqtrader.com nasdaqthYYYYMMDD.txt

Input : TSV with a 'Symbol' column; 'Float_M' (millions of shares) and
        'LastDailyClosePrice' are used when present for %-of-float and $-ADV.
Output: <input_stem>_SI.tsv next to the input (or --output-dir) with appended
        SI_* / SVR_* / CTB_* / Borrow_* / RegSHO_* columns, a SqueezeFlags
        summary and a heuristic SqueezeScore (0-100).

Raw downloads are cached under --cache-dir (default ./cache next to this file)
with dated filenames so re-runs on the same day do not refetch, and the FTP
snapshots accumulate history for future backtests.

Notes:
  - FINRA short interest is structurally 9-24 days stale; treat SI_* as a slow
    prior, not a timing signal.
  - Daily short volume is off-exchange flow, NOT short interest.
  - The threshold-list check covers Nasdaq-listed symbols only (v1).
  - Low dollar-ADV is reported as a flag (LOWLIQ), not a veto, because this
    universe is intentionally micro-cap.

Run with the venv interpreter:
  /home/tom/venv/bin/python ShortInterrest_AddON.py -i stocks_universe_2026-07-02_head20.tsv

Importable use (daily_universe_pipeline-2.py Step 8): `enrich_dataframe(df)`
appends the same columns to an in-memory DataFrame and returns it; all log
lines go to the named logger "short_interest" so the caller can route them.
"""

import argparse
import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from ftplib import FTP
from zoneinfo import ZoneInfo

import pandas as pd
import requests

# ---------------------------------------------------------------- endpoints
FINRA_SI_URL = "https://api.finra.org/data/group/otcMarket/name/{dataset}"
# (dataset, symbol filter field): consolidated covers exchange-listed issues
# (NYSE/NASDAQ), the standardized set covers OTC. Queried in order; a symbol
# found in an earlier dataset is not re-queried in later ones.
FINRA_SI_DATASETS = [
    ('consolidatedShortInterest', 'symbolCode'),
    ('equityShortInterestStandardized', 'securitiesInformationProcessorSymbolIdentifier'),
]
FINRA_SHVOL_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{ymd}.txt"
IBKR_FTP_HOST = "ftp3.interactivebrokers.com"
IBKR_FTP_USER = "shortstock"
IBKR_FTP_FILE = "usa.txt"
IBORROWDESK_URL = "https://iborrowdesk.com/api/ticker/{symbol}"
NASDAQ_REGSHO_URL = "https://www.nasdaqtrader.com/dynamic/symdir/regsho/nasdaqth{ymd}.txt"

HTTP_TIMEOUT = 30
HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) short-metrics-research/1.0",
    "Accept": "*/*",
}
IBORROW_SLEEP_SECONDS = 0.8   # politeness delay between per-symbol calls
YF_SLEEP_SECONDS = 0.7

SHVOL_DAYS = 6                # trading days of short-volume history (1 today + 5 trend)
REGSHO_DAYS = 10              # trading days of threshold lists for consecutive count
BORROW_HISTORY_DAYS = 20      # iBorrowDesk lookback for the utilization proxy
BORROW_DELTA_DAYS = 5

EASTERN = ZoneInfo("America/New_York")

# Named logger so an importing pipeline can attach its own handlers; the CLI
# path (setup_logging) configures the root logger and messages propagate up.
log = logging.getLogger("short_interest")

# ------------------------------------------------------- scoring constants
# Consensus thresholds from the three deep-search docs (see doc/ and the plan
# file). Weights: structure 40 / borrow friction 30 / flow 15 / float+reg 15.
TH_SIF_HIGH_PCT = 20.0        # short % of float
TH_SIF_EXTREME_PCT = 30.0
TH_DTC_HIGH = 3.0             # days to cover
TH_DTC_EXTREME = 5.0
TH_CTB_HIGH_PCT = 30.0        # annualized borrow fee
TH_CTB_EXTREME_PCT = 80.0
TH_AVAIL_DROP_PCT = -50.0     # 5-day change in shares available to borrow
TH_UTIL_HIGH = 0.90           # utilization proxy
TH_SVR_HIGH_PCT = 50.0        # daily short volume ratio
TH_SVR_RISING_PTS = 5.0       # SVR today vs 5-day average, percentage points
TH_FLOAT_LOW_SHARES = 50_000_000
TH_DOLLAR_ADV_FLOOR = 1_000_000  # $ADV below this only raises the LOWLIQ flag

SCORE_SIF_HIGH = 15
SCORE_SIF_EXTREME = 10        # additional
SCORE_DTC_HIGH = 8
SCORE_DTC_EXTREME = 7         # additional
SCORE_CTB_HIGH = 12
SCORE_CTB_EXTREME = 6         # additional
SCORE_AVAIL_DROP = 6
SCORE_UTIL_HIGH = 6
SCORE_SVR_HIGH = 10
SCORE_SVR_RISING = 5
SCORE_FLOAT_LOW = 5
SCORE_REGSHO = 10

OUTPUT_COLUMNS = [
    'SI_Source', 'SI_SettlementDate', 'SI_SharesShort', 'SI_PrevSharesShort',
    'SI_ChangePct', 'SI_PctFloat', 'SI_DTC', 'SI_DollarADV',
    'SVR_Date', 'SVR_Pct', 'SVR_5dAvgPct', 'SVR_TrendPts',
    'CTB_FeePct', 'CTB_RebatePct', 'CTB_FeeDelta5d',
    'Borrow_Available', 'Borrow_AvailDelta5dPct', 'Borrow_UtilProxy', 'Borrow_Source',
    'RegSHO_Flag', 'RegSHO_ConsecDays',
    'SqueezeFlags', 'SqueezeScore', 'SI_DataCoverage',
]


def setup_logging(log_path: str | None = None) -> None:
    """Configure logging to stderr and, optionally, to a file."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_path:
        log_dir = os.path.dirname(os.path.abspath(log_path))
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(logging.FileHandler(log_path))

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=handlers,
        force=True,
    )


# ------------------------------------------------------------ small helpers
def eastern_today() -> date:
    return datetime.now(EASTERN).date()


def weekdays_back(start: date, count: int, max_scan: int = 30) -> list[date]:
    """Most recent `count` weekdays ending at `start` (newest first).

    Holidays are not excluded here; fetchers tolerate 404s on holiday dates.
    """
    days: list[date] = []
    d = start
    scanned = 0
    while len(days) < count and scanned < max_scan:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
        scanned += 1
    return days


def to_float(value) -> float | None:
    """Parse a number that may carry '>', '%', ',' or be empty."""
    if value is None:
        return None
    s = str(value).strip().lstrip('>').replace(',', '').rstrip('%').strip()
    if not s or s.upper() in {'NA', 'N/A', 'NONE', 'NULL', 'NAN'}:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def first_present(record: dict, candidates: list[str]):
    """Return the first non-empty value among candidate field names."""
    for name in candidates:
        if name in record and record[name] not in (None, ''):
            return record[name]
    return None


def cached_download(path: str, downloader) -> str | None:
    """Return text content from cache, or run downloader() and cache it.

    downloader() returns the text or None on failure. Failures are not cached.
    """
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, 'r', errors='replace') as fh:
            return fh.read()
    text = downloader()
    if text:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fh:
            fh.write(text)
    return text


def http_get_text(url: str) -> str | None:
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=HTTP_TIMEOUT)
        if resp.status_code == 200 and resp.text.strip():
            # nasdaqtrader.com serves an HTML "Page Not Available" page with
            # HTTP 200 for missing dates (holidays); don't treat it as data.
            if resp.text.lstrip().startswith('<'):
                log.info(f"GET {url} -> HTML page instead of data; skipping.")
                return None
            return resp.text
        log.info(f"GET {url} -> HTTP {resp.status_code}")
    except requests.RequestException as e:
        log.warning(f"GET {url} failed: {e}")
    return None


# ------------------------------------------------- 1. FINRA short interest
def _parse_finra_records(records: list[dict], source: str) -> dict[str, dict]:
    """Keep the latest-settlement record per symbol, normalizing field names."""
    out: dict[str, dict] = {}
    for rec in records:
        sym = first_present(rec, ['symbolCode', 'issueSymbolIdentifier',
                                  'securitiesInformationProcessorSymbolIdentifier'])
        settle = first_present(rec, ['settlementDate'])
        if not sym or not settle:
            continue
        sym = str(sym).strip().upper()
        prev = out.get(sym)
        if prev is None or str(settle) > str(prev.get('settlementDate', '')):
            out[sym] = {
                'settlementDate': str(settle)[:10],
                'sharesShort': to_float(first_present(rec, [
                    'currentShortPositionQuantity', 'currentShortShareNumber'])),
                'prevSharesShort': to_float(first_present(rec, [
                    'previousShortPositionQuantity', 'previousShortShareNumber'])),
                'changePct': to_float(first_present(rec, [
                    'changePercent', 'percentageChangefromPreviousShort'])),
                'adv': to_float(first_present(rec, [
                    'averageDailyVolumeQuantity', 'averageShortShareNumber'])),
                'dtc': to_float(first_present(rec, [
                    'daysToCoverQuantity', 'daysToCoverNumber', 'daysToCover'])),
                'source': source,
            }
    return out


def fetch_finra_short_interest(symbols: list[str], cache_dir: str) -> dict[str, dict]:
    """Latest bi-monthly FINRA short-interest record per symbol.

    Anonymous POSTs to the FINRA Query API, consolidated (exchange-listed)
    dataset first, then the OTC standardized one for symbols still missing.
    Raw responses are cached per day and per symbol-set. On 401/403 a free
    key from developer.finra.org would be needed; the caller then falls back
    to yfinance for anything missing.
    """
    import hashlib
    remaining = sorted(set(symbols))
    cutoff = (eastern_today() - timedelta(days=45)).isoformat()
    out: dict[str, dict] = {}

    for dataset, symbol_field in FINRA_SI_DATASETS:
        if not remaining:
            break
        sym_hash = hashlib.sha1('|'.join(remaining).encode()).hexdigest()[:10]
        cache_path = os.path.join(cache_dir, 'finra_si',
                                  f"{dataset}_{eastern_today()}_{sym_hash}.json")
        if os.path.exists(cache_path):
            with open(cache_path) as fh:
                records = json.load(fh)
        else:
            body = {
                'limit': 5000,
                'compareFilters': [{'compareType': 'GTE',
                                    'fieldName': 'settlementDate',
                                    'fieldValue': cutoff}],
                'domainFilters': [{'fieldName': symbol_field, 'values': remaining}],
            }
            try:
                resp = requests.post(
                    FINRA_SI_URL.format(dataset=dataset), json=body,
                    timeout=HTTP_TIMEOUT,
                    headers={**HTTP_HEADERS, 'Accept': 'application/json',
                             'Content-Type': 'application/json'})
            except requests.RequestException as e:
                log.warning(f"FINRA SI ({dataset}) request failed: {e}")
                continue
            if resp.status_code == 204:
                records = []
            elif resp.status_code == 200:
                try:
                    records = resp.json()
                except ValueError:
                    log.warning(f"FINRA SI ({dataset}): response was not JSON.")
                    continue
            elif resp.status_code in (401, 403):
                log.warning(
                    f"FINRA SI ({dataset}): HTTP {resp.status_code} — anonymous "
                    "access refused. Register a free API key at "
                    "developer.finra.org, or rely on the yfinance fallback.")
                continue
            else:
                log.info(f"FINRA SI ({dataset}): HTTP {resp.status_code} "
                             f"— {resp.text[:200]}")
                continue
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w') as fh:
                json.dump(records, fh)

        parsed = _parse_finra_records(records, 'finra')
        log.info(f"FINRA SI ({dataset}): {len(records)} records, "
                     f"{len(parsed)} symbols matched.")
        out.update(parsed)
        remaining = [s for s in remaining if s not in out]

    return out


def fetch_yfinance_short_interest(symbols: list[str]) -> dict[str, dict]:
    """Per-symbol yfinance fallback for the FINRA bi-monthly numbers."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — cannot fall back for short interest.")
        return {}

    out: dict[str, dict] = {}
    for sym in symbols:
        try:
            info = yf.Ticker(sym).info or {}
        except Exception as e:
            log.info(f"{sym}: yfinance info failed — {e}")
            continue
        shares_short = to_float(info.get('sharesShort'))
        if shares_short is None:
            continue
        settle_ts = info.get('dateShortInterest')
        settle = (datetime.fromtimestamp(settle_ts, tz=EASTERN).date().isoformat()
                  if settle_ts else None)
        out[sym] = {
            'settlementDate': settle,
            'sharesShort': shares_short,
            'prevSharesShort': to_float(info.get('sharesShortPriorMonth')),
            'changePct': None,
            'adv': to_float(info.get('averageVolume')),
            'dtc': to_float(info.get('shortRatio')),
            'source': 'yfinance',
        }
        time.sleep(YF_SLEEP_SECONDS)
    return out


# -------------------------------------------- 2. FINRA daily short volume
def fetch_short_volume_history(cache_dir: str) -> dict[str, list[tuple[str, float]]]:
    """Per-symbol [(date, SVR%), ...] oldest->newest over recent trading days."""
    per_symbol: dict[str, list[tuple[str, float]]] = {}
    found = 0
    for d in weekdays_back(eastern_today(), SHVOL_DAYS + 4):
        if found >= SHVOL_DAYS:
            break
        ymd = d.strftime('%Y%m%d')
        path = os.path.join(cache_dir, 'shvol', f"CNMSshvol{ymd}.txt")
        text = cached_download(path, lambda u=FINRA_SHVOL_URL.format(ymd=ymd): http_get_text(u))
        if not text:
            continue  # holiday, or today's file not posted yet (~6 PM ET)
        found += 1
        for line in text.splitlines():
            parts = line.strip().split('|')
            if len(parts) < 5 or parts[0] == 'Date':
                continue
            short_v, total_v = to_float(parts[2]), to_float(parts[4])
            if short_v is None or not total_v:
                continue
            sym = parts[1].strip().upper()
            per_symbol.setdefault(sym, []).append(
                (d.isoformat(), round(short_v / total_v * 100.0, 2)))
    for series in per_symbol.values():
        series.sort()
    log.info(f"Short-volume files loaded: {found} day(s), "
                 f"{len(per_symbol)} symbols.")
    return per_symbol


# --------------------------------------------- 3. IBKR shortstock FTP file
def fetch_ibkr_borrow_file(cache_dir: str) -> dict[str, dict]:
    """Current IBKR borrow snapshot: {SYM: {fee, rebate, available}}."""
    path = os.path.join(cache_dir, 'shortstock', f"usa_{eastern_today()}.txt")

    def download() -> str | None:
        try:
            chunks: list[bytes] = []
            with FTP(IBKR_FTP_HOST, timeout=15) as ftp:
                ftp.login(IBKR_FTP_USER, '')
                ftp.retrbinary(f"RETR {IBKR_FTP_FILE}", chunks.append)
            return b''.join(chunks).decode('utf-8', errors='replace')
        except Exception as e:
            log.warning(
                f"IBKR shortstock FTP failed ({e}) — outbound FTP/port 21 may be "
                "blocked on this network; current fee/available will come from "
                "iBorrowDesk instead.")
            return None

    text = cached_download(path, download)
    if not text:
        return {}

    out: dict[str, dict] = {}
    col_idx: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.rstrip('\n').split('|')
        if line.startswith('#SYM'):
            col_idx = {name.lstrip('#').strip().upper(): i
                       for i, name in enumerate(parts)}
            continue
        if line.startswith('#') or len(parts) < 3 or not col_idx:
            continue

        def col(name: str):
            i = col_idx.get(name)
            return parts[i] if i is not None and i < len(parts) else None

        sym = (col('SYM') or '').strip().upper()
        if not sym:
            continue
        out[sym] = {
            'fee': to_float(col('FEERATE')),
            'rebate': to_float(col('REBATERATE')),
            'available': to_float(col('AVAILABLE')),
        }
    log.info(f"IBKR borrow file: {len(out)} symbols.")
    return out


# ------------------------------------------------- 4. iBorrowDesk history
def fetch_iborrowdesk(symbol: str, cache_dir: str) -> dict | None:
    """Daily borrow history for one symbol (unofficial endpoint), or None."""
    path = os.path.join(cache_dir, 'iborrowdesk', f"{symbol}_{eastern_today()}.json")
    if os.path.exists(path):
        with open(path) as fh:
            return json.load(fh)
    text = http_get_text(IBORROWDESK_URL.format(symbol=symbol))
    time.sleep(IBORROW_SLEEP_SECONDS)
    if not text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        log.info(f"{symbol}: iBorrowDesk returned non-JSON.")
        return None
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as fh:
        json.dump(data, fh)
    return data


def borrow_history_metrics(data: dict | None) -> dict:
    """5-day fee delta, 5-day availability change and utilization proxy."""
    empty = {'fee_now': None, 'available_now': None, 'fee_delta_5d': None,
             'avail_delta_5d_pct': None, 'util_proxy': None}
    if not data:
        return empty
    daily = data.get('daily') or []
    rows = []
    for entry in daily:
        fee = to_float(entry.get('fee'))
        avail = to_float(entry.get('available'))
        stamp = str(entry.get('date') or entry.get('time') or '')
        if fee is None and avail is None:
            continue
        rows.append((stamp, fee, avail))
    if not rows:
        return empty
    rows.sort()
    rows = rows[-BORROW_HISTORY_DAYS:]

    _, fee_now, avail_now = rows[-1]
    fee_delta = avail_delta_pct = None
    if len(rows) > BORROW_DELTA_DAYS:
        _, fee_then, avail_then = rows[-1 - BORROW_DELTA_DAYS]
        if fee_now is not None and fee_then is not None:
            fee_delta = round(fee_now - fee_then, 2)
        if avail_now is not None and avail_then:
            avail_delta_pct = round((avail_now - avail_then) / avail_then * 100.0, 1)
    avail_values = [a for _, _, a in rows if a is not None]
    util_proxy = None
    if avail_values and max(avail_values) > 0 and avail_now is not None:
        util_proxy = round(1.0 - avail_now / max(avail_values), 3)
    return {'fee_now': fee_now, 'available_now': avail_now,
            'fee_delta_5d': fee_delta, 'avail_delta_5d_pct': avail_delta_pct,
            'util_proxy': util_proxy}


# --------------------------------------- 5. Nasdaq Reg SHO threshold lists
def fetch_regsho_membership(cache_dir: str) -> list[tuple[str, set[str]]]:
    """[(date_iso, {symbols on the Nasdaq threshold list})], newest first."""
    lists: list[tuple[str, set[str]]] = []
    for d in weekdays_back(eastern_today(), REGSHO_DAYS + 4):
        if len(lists) >= REGSHO_DAYS:
            break
        ymd = d.strftime('%Y%m%d')
        path = os.path.join(cache_dir, 'regsho', f"nasdaqth{ymd}.txt")
        text = cached_download(path, lambda u=NASDAQ_REGSHO_URL.format(ymd=ymd): http_get_text(u))
        if text is None:
            continue
        if not text.lstrip().startswith('Symbol|'):
            log.info(f"Reg SHO {ymd}: unexpected file format — skipping day.")
            continue
        symbols: set[str] = set()
        for line in text.splitlines():
            parts = line.strip().split('|')
            if len(parts) < 3 or parts[0] in ('Symbol', '') or parts[0].startswith('#'):
                continue
            symbols.add(parts[0].strip().upper())
        lists.append((d.isoformat(), symbols))
    log.info(f"Reg SHO threshold lists loaded: {len(lists)} day(s).")
    return lists


def regsho_flag_and_streak(symbol: str,
                           lists: list[tuple[str, set[str]]]) -> tuple[bool | None, int | None]:
    if not lists:
        return None, None
    streak = 0
    for _, members in lists:  # newest first
        if symbol in members:
            streak += 1
        else:
            break
    return streak > 0, streak


# ----------------------------------------------------- metrics + scoring
def compute_symbol_metrics(symbol: str, float_shares: float | None,
                           last_close: float | None,
                           si: dict | None,
                           svr_series: list[tuple[str, float]] | None,
                           ftp_borrow: dict | None,
                           borrow_hist: dict,
                           regsho: tuple[bool | None, int | None]) -> dict:
    m: dict = {c: None for c in OUTPUT_COLUMNS}
    coverage = []

    # --- structure: bi-monthly short interest
    if si:
        coverage.append(si['source'])
        m['SI_Source'] = si['source']
        m['SI_SettlementDate'] = si['settlementDate']
        m['SI_SharesShort'] = si['sharesShort']
        m['SI_PrevSharesShort'] = si['prevSharesShort']
        change = si['changePct']
        if change is None and si['sharesShort'] is not None and si['prevSharesShort']:
            change = round((si['sharesShort'] - si['prevSharesShort'])
                           / si['prevSharesShort'] * 100.0, 1)
        m['SI_ChangePct'] = change
        if si['sharesShort'] is not None and float_shares:
            m['SI_PctFloat'] = round(si['sharesShort'] / float_shares * 100.0, 2)
        dtc = si['dtc']
        if dtc is None and si['sharesShort'] is not None and si['adv']:
            dtc = round(si['sharesShort'] / si['adv'], 2)
        m['SI_DTC'] = dtc
        if si['adv'] is not None and last_close:
            m['SI_DollarADV'] = round(si['adv'] * last_close)

    # --- flow: daily short volume ratio
    if svr_series:
        coverage.append('svr')
        m['SVR_Date'], m['SVR_Pct'] = svr_series[-1]
        prior = [v for _, v in svr_series[:-1]][-5:]
        if prior:
            avg = round(sum(prior) / len(prior), 2)
            m['SVR_5dAvgPct'] = avg
            m['SVR_TrendPts'] = round(m['SVR_Pct'] - avg, 2)

    # --- friction: borrow market (FTP snapshot is the live source of truth,
    # iBorrowDesk fills history-derived deltas and can back-fill current values)
    fee = rebate = available = None
    borrow_src = []
    if ftp_borrow:
        fee, rebate, available = (ftp_borrow['fee'], ftp_borrow['rebate'],
                                  ftp_borrow['available'])
        borrow_src.append('ibkr_ftp')
    if fee is None and borrow_hist['fee_now'] is not None:
        fee = borrow_hist['fee_now']
    if available is None and borrow_hist['available_now'] is not None:
        available = borrow_hist['available_now']
    if any(v is not None for v in borrow_hist.values()):
        borrow_src.append('iborrowdesk')
    if borrow_src:
        coverage.append('borrow')
    m['CTB_FeePct'] = fee
    m['CTB_RebatePct'] = rebate
    m['CTB_FeeDelta5d'] = borrow_hist['fee_delta_5d']
    m['Borrow_Available'] = available
    m['Borrow_AvailDelta5dPct'] = borrow_hist['avail_delta_5d_pct']
    m['Borrow_UtilProxy'] = borrow_hist['util_proxy']
    m['Borrow_Source'] = '+'.join(borrow_src) if borrow_src else None

    # --- regulatory
    flag, streak = regsho
    if flag is not None:
        coverage.append('regsho')
        m['RegSHO_Flag'] = int(flag)
        m['RegSHO_ConsecDays'] = streak

    # --- flags and score
    score = 0
    flags = []

    def hit(name: str, points: int):
        nonlocal score
        score += points
        flags.append(name)

    sif = m['SI_PctFloat']
    if sif is not None and sif >= TH_SIF_HIGH_PCT:
        hit('SIF20', SCORE_SIF_HIGH)
        if sif >= TH_SIF_EXTREME_PCT:
            hit('SIF30', SCORE_SIF_EXTREME)
    if m['SI_DTC'] is not None and m['SI_DTC'] >= TH_DTC_HIGH:
        hit('DTC3', SCORE_DTC_HIGH)
        if m['SI_DTC'] >= TH_DTC_EXTREME:
            hit('DTC5', SCORE_DTC_EXTREME)
    if fee is not None and fee >= TH_CTB_HIGH_PCT:
        hit('CTB30', SCORE_CTB_HIGH)
        if fee >= TH_CTB_EXTREME_PCT:
            hit('CTB80', SCORE_CTB_EXTREME)
    if (m['Borrow_AvailDelta5dPct'] is not None
            and m['Borrow_AvailDelta5dPct'] <= TH_AVAIL_DROP_PCT):
        hit('AVAILDROP', SCORE_AVAIL_DROP)
    if m['Borrow_UtilProxy'] is not None and m['Borrow_UtilProxy'] >= TH_UTIL_HIGH:
        hit('UTIL90', SCORE_UTIL_HIGH)
    if m['SVR_Pct'] is not None and m['SVR_Pct'] >= TH_SVR_HIGH_PCT:
        hit('SVR50', SCORE_SVR_HIGH)
    if m['SVR_TrendPts'] is not None and m['SVR_TrendPts'] >= TH_SVR_RISING_PTS:
        hit('SVRUP', SCORE_SVR_RISING)
    if float_shares is not None and float_shares < TH_FLOAT_LOW_SHARES:
        hit('LOWFLOAT', SCORE_FLOAT_LOW)
    if m['RegSHO_Flag']:
        hit('REGSHO', SCORE_REGSHO)

    # informational flags (no score contribution)
    if m['SI_DollarADV'] is not None and m['SI_DollarADV'] < TH_DOLLAR_ADV_FLOOR:
        flags.append('LOWLIQ')
    if sif is not None and sif > 100.0:
        flags.append('FLOAT_SUSPECT')

    m['SqueezeFlags'] = ';'.join(flags)
    m['SqueezeScore'] = min(score, 100)
    m['SI_DataCoverage'] = '+'.join(coverage) if coverage else 'none'
    return m


# ------------------------------------------------------------- enrichment
def enrich_dataframe(df: pd.DataFrame, cache_dir: str | None = None,
                     skip_borrow_history: bool = False) -> pd.DataFrame:
    """Append the SI_*/SVR_*/CTB_*/Borrow_*/RegSHO_*/Squeeze columns to `df`.

    `df` must have a 'Symbol' column; 'Float_M' (millions of shares) and
    'LastDailyClosePrice' are used when present — values may be strings
    (everything goes through to_float), so a dtype=str frame works. Rows with
    a missing/blank Symbol are dropped. Returns the enriched copy.

    This is the library entry point used by daily_universe_pipeline-2.py;
    the CLI in main() is a thin wrapper around it.
    """
    if 'Symbol' not in df.columns:
        raise ValueError("DataFrame has no 'Symbol' column.")
    cache_dir = cache_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), 'cache')
    os.makedirs(cache_dir, exist_ok=True)

    df = df[df['Symbol'].notna()].copy()
    df = df[df['Symbol'].astype(str).str.strip() != ''].copy()
    symbols = sorted({str(s).strip().upper() for s in df['Symbol']})
    log.info(f"{len(symbols)} unique symbols to enrich")

    # market-wide fetches (one download each, cached per day)
    si_map = fetch_finra_short_interest(symbols, cache_dir)
    missing_si = [s for s in symbols if s not in si_map]
    if missing_si:
        log.info(f"FINRA SI missing for {len(missing_si)} symbols — trying yfinance.")
        si_map.update(fetch_yfinance_short_interest(missing_si))
    svr_map = fetch_short_volume_history(cache_dir)
    ftp_map = fetch_ibkr_borrow_file(cache_dir)
    regsho_lists = fetch_regsho_membership(cache_dir)

    # per-symbol assembly (iBorrowDesk is the only per-symbol network call)
    metrics: dict[str, dict] = {}
    for n, sym in enumerate(symbols, 1):
        row = df[df['Symbol'].astype(str).str.strip().str.upper() == sym].iloc[0]
        float_m = to_float(row.get('Float_M'))
        float_shares = float_m * 1e6 if float_m else None
        last_close = to_float(row.get('LastDailyClosePrice'))

        if skip_borrow_history:
            hist = borrow_history_metrics(None)
        else:
            log.info(f"[{n}/{len(symbols)}] {sym}: fetching borrow history...")
            hist = borrow_history_metrics(fetch_iborrowdesk(sym, cache_dir))

        metrics[sym] = compute_symbol_metrics(
            symbol=sym,
            float_shares=float_shares,
            last_close=last_close,
            si=si_map.get(sym),
            svr_series=svr_map.get(sym),
            ftp_borrow=ftp_map.get(sym),
            borrow_hist=hist,
            regsho=regsho_flag_and_streak(sym, regsho_lists),
        )

    for col in OUTPUT_COLUMNS:
        df[col] = [metrics[str(s).strip().upper()][col] for s in df['Symbol']]

    covered = sum(1 for m in metrics.values() if m['SI_DataCoverage'] != 'none')
    scored = sum(1 for m in metrics.values() if (m['SqueezeScore'] or 0) > 0)
    log.info(f"Symbols: {len(symbols)} | with any data: {covered} | score > 0: {scored}")
    return df


# -------------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(
        description=('Enrich a universe TSV with short-interest / short-squeeze '
                     'metrics from free sources (FINRA, IBKR shortstock FTP, '
                     'iBorrowDesk, Nasdaq Reg SHO threshold list). Output is '
                     '<input_stem>_SI.tsv.'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--input', '-i', required=True, metavar='FILE',
                        dest='input_file',
                        help='Input .tsv with a Symbol column (Float_M and '
                             'LastDailyClosePrice are used when present).')
    parser.add_argument('--output-dir', '-o', metavar='DIR', default=None,
                        help="Output directory (default: the input file's directory).")
    parser.add_argument('--cache-dir', metavar='DIR', default=None,
                        help='Raw-download cache (default: ./cache next to this script).')
    parser.add_argument('--log', '-l', metavar='FILE', dest='log_file', default=None,
                        help='Optional log file (appended).')
    parser.add_argument('--skip-borrow-history', action='store_true',
                        help='Skip the per-symbol iBorrowDesk calls (faster; no '
                             'CTB/availability deltas or utilization proxy).')
    args = parser.parse_args()

    setup_logging(args.log_file)

    df = pd.read_csv(args.input_file, sep='\t')
    if 'Symbol' not in df.columns:
        log.error("Input file has no 'Symbol' column.")
        return
    log.info(f"Input: {args.input_file}")
    df = enrich_dataframe(df, cache_dir=args.cache_dir,
                          skip_borrow_history=args.skip_borrow_history)

    input_stem = os.path.splitext(os.path.basename(args.input_file))[0]
    out_dir = args.output_dir or os.path.dirname(os.path.abspath(args.input_file))
    out_path = os.path.join(out_dir, f"{input_stem}_SI.tsv")
    df.to_csv(out_path, sep='\t', index=False)
    log.info(f"Saved {out_path}")


if __name__ == '__main__':
    main()
