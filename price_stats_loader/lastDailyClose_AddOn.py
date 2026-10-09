"""Add LastDailyClosePrice column by matching PR IDs against orchestrator tables.

This script enriches a PR-stats TSV with the prior trading-day close price for each
PR by looking it up in the orchestrator5 and orchestrator3 daily output tables
(news_output_<YYYY-MM-DD>.tsv files under tables/). For any PR not found in those
tables (older dates, gaps), it falls back to a live IBKR fetch using the same
machinery as prevclose_backfill_addOn.py.

THE TARGET BASIS IS AS-TRADED. The orchestrator tables recorded their close live on
the PR day, so they are on the share basis that was in force back then, and every
other price on the row is too. Live IBKR fetches are NOT: IBKR back-adjusts history
to the share basis in force at REQUEST time, so a symbol that reverse-split between
the PR and the backfill comes back on today's much higher basis (VMAR's 2026-06-09
close read 38.00 in a 2026-09-04 request against the 0.4181 it actually traded at).

The correction is one multiplier, F = forward_split_factor(splits, anchor) -- the
product of every split between the anchor session and the request -- applied as

    as_traded = ibkr_bar * F

For VMAR that is 38.00 * 0.1 * 0.1 = 0.38. F < 1 for a reverse split, so it scales
today's basis DOWN onto the as-traded one; it does not "undo a missing adjustment".
Getting this backwards inverts the correction by F^2, so trace a concrete symbol
before touching it.

Splits come from yfinance, which market_state_calculator.py demonstrates is wrong in
BOTH directions on microcaps, so F is a hypothesis and basis_check() refereess it
against a point-in-time close whenever one exists.

Output is written to <input_stem>_DailyClose.tsv with three status columns:
    LastDailyClosePrice_SplitAdj    "adjusted" | "none_needed" | "unknown" | ""
    LastDailyClosePrice_BasisFlag   basis_check()'s verdict on F (blank if unchecked)
    LastDailyClosePrice_Source      "orch5" | "orch3" | "ibkr" | "yfinance"
                                    | "unrecoverable_no_history"

Run with the ibapi venv, on the PAPER gateway (the live one drops bulk historical
requests under contention):
    /home/tom/venv/bin/python lastDailyClose_AddOn.py \\
        -i outputs/PR-stats-...tsv --port 4002 -l /tmp/lastclose.log

To fill only the rows a previous run left empty, without re-fetching anything it
already got right:
    /home/tom/venv/bin/python lastDailyClose_AddOn.py \\
        -i outputs/..._DailyClose.tsv --fill-missing-only --port 4002
"""

import argparse
import logging
import math
import os
import time
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
import pandas as pd
import pandas_market_calendars as mcal
from pathlib import Path

# Reuse the fetch/calendar/connection machinery.
from highOfDay_addON import (
    IB_HOST, IB_PORT, PACING_SLEEP_SECONDS, CONSECUTIVE_FAILURE_ABORT,
    setup_logging, is_us_exchange, get_target_date, fetch_hod_and_prev_close,
)
from ib_insync import IB

# clientId: 8 is clear of 6 (highOfDay), 7 (fromPR_HighOfDay), 77 (dailyStats), 20000 (board)
CLIENT_ID = 8

SCRIPT_DIR = Path(__file__).parent
ORCH5_TABLES_DIR = SCRIPT_DIR.parent / 'orchestrator5' / 'tables'
ORCH3_TABLES_DIR = SCRIPT_DIR.parent / 'orchestrator3' / 'tables'
MSC_CACHE_DIR = SCRIPT_DIR.parent / 'tools' / 'market_state' / 'tables'
UNIVERSE_DIR = SCRIPT_DIR.parent / 'universe_finder' / 'data'

# Mirrored from tools/market_state/market_state_calculator.py
# Updated 2026-09-04 alongside MSC @ line 609
SPLIT_CACHE_MAX_AGE_DAYS = 1
ET = timezone(timedelta(hours=-5))

_XNYS = mcal.get_calendar('XNYS')


def prev_trading_day(d: date) -> date:
    """The last US equity session strictly before d."""
    sched = _XNYS.valid_days(start_date=d - timedelta(days=12), end_date=d - timedelta(days=1))
    if len(sched) == 0:
        raise ValueError(f"no trading session in the 12 days before {d}")
    return sched[-1].date()


def get_table_path(base_dir: Path, arrival_date: str) -> Path:
    """Return path to the news_output_<date>.tsv file (no variant suffix)."""
    return base_dir / f'news_output_{arrival_date}.tsv'


def get_table_path_variants(base_dir: Path, arrival_date: str) -> list:
    """Return candidate file paths for a given date, including variant suffixes.

    Matches the naming quirks in orchestrator tables:
    - news_output_<date>.tsv (primary)
    - news_output_<date>_rotated.tsv
    - news_output_<date>_2.tsv
    - news_output_<date>_MIXED_SCHEMA.tsv
    """
    base = get_table_path(base_dir, arrival_date)
    variants = [
        base,
        base.parent / f'{base.stem}_rotated.tsv',
        base.parent / f'{base.stem}_2.tsv',
        base.parent / f'{base.stem}_MIXED_SCHEMA.tsv',
    ]
    return [p for p in variants if p.exists()]


# Parsed orchestrator tables, keyed by path. Module level on purpose: the lookup runs
# once per row, and a cache rebuilt inside the function (as this once was) is no cache
# at all -- it re-read every table from disk 4,804 times on the 2026-09-04 run and was
# most of that run's wall time.
_ORCH_TABLE_CACHE: dict = {}


def load_orch_table(path: Path) -> pd.DataFrame:
    """Load orchestrator table, caching by path to avoid re-reading."""
    if path not in _ORCH_TABLE_CACHE:
        _ORCH_TABLE_CACHE[path] = pd.read_csv(path, sep='\t', low_memory=False)
    return _ORCH_TABLE_CACHE[path]


def lookup_in_orchestrator_tables(pr_id: str, arrival_date: str) -> tuple:
    """
    Look up a PR ID in orchestrator5 and orchestrator3 tables for a given date.

    Returns (price, source) where source is 'orch5', 'orch3', or None if not found.

    NOTE ON SEMANTICS: the value these tables hold is the last close available when
    the PR arrived. For a PR that arrives after the 16:00 close that is the session
    BEFORE the one the live-fetch path would return, so the two fill paths disagree by
    one session on after-hours PRs (20.3% of the 2026-09-04 corpus; 383 confirmed
    instances). Nothing here corrects that -- it is recorded in
    LastDailyClosePrice_Source so downstream code can tell the two apart.
    """
    # Try orchestrator5 first (current live system).
    for path in get_table_path_variants(ORCH5_TABLES_DIR, arrival_date):
        try:
            df = load_orch_table(path)
            if 'ID' in df.columns and 'LastDailyClose' in df.columns:
                match = df[df['ID'] == pr_id]
                if not match.empty:
                    price = match.iloc[0]['LastDailyClose']
                    if pd.notna(pd.to_numeric(price, errors='coerce')):
                        return (price, 'orch5')
        except Exception as e:
            logging.warning(f"Error reading {path}: {e}")

    # Fall back to orchestrator3.
    for path in get_table_path_variants(ORCH3_TABLES_DIR, arrival_date):
        try:
            df = load_orch_table(path)
            if 'ID' in df.columns and 'LastDailyClose' in df.columns:
                match = df[df['ID'] == pr_id]
                if not match.empty:
                    price = match.iloc[0]['LastDailyClose']
                    if pd.notna(pd.to_numeric(price, errors='coerce')):
                        return (price, 'orch3')
        except Exception as e:
            logging.warning(f"Error reading {path}: {e}")

    return (None, None)


def build_output_path(input_path: str, output_dir: str = None) -> str:
    """Build the output path with _DailyClose suffix."""
    if output_dir is None:
        output_dir = os.path.dirname(os.path.abspath(input_path))
    else:
        output_dir = os.path.abspath(output_dir)

    input_stem = os.path.splitext(os.path.basename(input_path))[0]
    return os.path.join(output_dir, f'{input_stem}_DailyClose.tsv')


# =====================================================================
# Split adjustment machinery (mirrored from market_state_calculator.py)
# Kept byte-identical per vix_addOn.py precedent; any updates to MSC
# must be replicated here. Mirrored: fetch_splits, forward_split_factor,
# split_prefix_products, plausible_split_ratio, basis_check and their
# constants. NOT mirrored: MSC's bar fetching and feature machinery.
#
# forward_split_factor() alone was mirrored until 2026-09-04. That left
# yfinance as the sole authority on F, which is exactly what MSC added
# basis_check() to stop -- so a wrong split list produced a confidently
# wrong price with no signal. basis_check() is now mirrored too.
# =====================================================================

BASIS_MIN_FACTOR = 2.5
BASIS_RATIO_TOL = 0.02          # how close to a clean rational the factor must sit
BASIS_AGREE_LO = 0.75           # ratios inside this band are "the same price"
BASIS_AGREE_HI = 1.34

def split_cache_path(cache_dir: Path, symbol: str) -> Path:
    """Return the cache path for a symbol's splits TSV."""
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in symbol.upper())
    return cache_dir / "splits" / f"{safe}.tsv"


def fetch_splits(symbol: str, cache_dir: Path, use_cache: bool,
                 attempts: int = 2, max_age_days: int = SPLIT_CACHE_MAX_AGE_DAYS,
                 force_refresh: bool = False) -> list[tuple[date, float]] | None:
    """All splits on record for symbol as [(ex_date, factor), ...].

    factor is yfinance's new/old share ratio: 0.05 = 1-for-20 reverse,
    20 = 20-for-1 forward. Returns [] when the symbol never split and None when
    the lookup failed (so 'no split' and 'no data' stay distinguishable).

    A cache file older than max_age_days is treated as a miss (see
    SPLIT_CACHE_MAX_AGE_DAYS); force_refresh treats every file as a miss.
    """
    path = split_cache_path(cache_dir, symbol)
    fresh = False
    if path.exists() and not force_refresh:
        try:
            age = time.time() - path.stat().st_mtime
            fresh = age <= max_age_days * 86400
        except OSError:
            fresh = False
    if use_cache and fresh:
        try:
            df = pd.read_csv(path, sep="\t")
            if df.empty:
                return []
            return [(pd.to_datetime(r.ex_date).date(), float(r.factor))
                    for r in df.itertuples()]
        except Exception:
            pass

    try:
        import yfinance as yf
    except ImportError:
        return None

    for _ in range(attempts):
        try:
            ticker = yf.Ticker(symbol)
            splits = ticker.splits
            out: list[tuple[date, float]] = []
            if splits is not None and len(splits):
                for ts, factor in splits.items():
                    d = ts.tz_convert(ET).date() if ts.tzinfo is not None else ts.date()
                    if factor and float(factor) > 0:
                        out.append((d, float(factor)))
            elif not getattr(ticker, "history_metadata", None):
                # Empty splits + empty metadata = Yahoo does not know this
                # ticker at all. Returning [] here would assert "never split"
                # about a symbol we know nothing about, hiding a real split
                # behind a confident zero. Populated metadata is the side
                # effect of the splits fetch, so this costs no extra call.
                return None
            out.sort()
            if use_cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                pd.DataFrame(out, columns=["ex_date", "factor"]).to_csv(
                    path, sep="\t", index=False)
            return out
        except Exception:
            time.sleep(0.5)
    return None


def forward_split_factor(splits, anchor_date, bars_fetched_at=None) -> float | None:
    """Cumulative split factor for splits AFTER the anchor, or None if unknown.

    IBKR back-adjusts every bar it returns to the share basis in force at REQUEST
    time, so a bar for `anchor_date` pulled today is quoted in today's shares, not
    the ones that actually traded that day. This is the single multiplier that puts
    it back:

        as_traded_at_anchor = ibkr_bar_close * F

    F is the product of the splits between the anchor and the request: < 1 for a
    reverse split, so it scales today's basis DOWN. Note the direction -- dividing
    instead of multiplying inverts the correction by F squared, which on a symbol
    like VMAR (two 1-for-10s, F = 0.01) is a factor of 10,000.

    Returns 1.0 when no split follows the anchor, and None when the split list
    is unknown (lookup failed) — never a confident 1.0, because "no data" and
    "no split" are different facts.

    bars_fetched_at bounds the product from above. A split at or after the day
    the bars were pulled cannot be in those bars, so multiplying it out would be
    an error.
    """
    if splits is None:
        return None
    factor = 1.0
    for d, f in splits:
        if d > anchor_date and f > 0:
            if bars_fetched_at is not None and d >= bars_fetched_at:
                continue
            factor *= f
    return factor


def split_prefix_products(splits, anchor_date, bars_fetched_at=None) -> list[float]:
    """Every share basis the symbol passed through after the anchor."""
    out, factor = [], 1.0
    for d, f in sorted(splits or []):
        if d > anchor_date and f > 0:
            if bars_fetched_at is not None and d >= bars_fetched_at:
                break
            factor *= f
            out.append(factor)
    return out


def plausible_split_ratio(ratio: float, tol: float = BASIS_RATIO_TOL) -> float | None:
    """The clean split-like factor `ratio` sits on, or None.

    A share-basis difference is an exact rational with a small denominator; ordinary
    feed disagreement does not land on one.
    """
    if not ratio or not math.isfinite(ratio) or ratio <= 0:
        return None
    big = ratio if ratio >= 1.0 else 1.0 / ratio
    if big < BASIS_MIN_FACTOR:
        return None
    cand = float(Fraction(big).limit_denominator(4))
    if cand <= 0 or abs(math.log(big / cand)) > math.log(1.0 + tol):
        return None
    return cand if ratio >= 1.0 else 1.0 / cand


def basis_check(bar_close, fwd, price_ref, price_ref_src, splits, anchor_date,
                bars_fetched_at):
    """(flag, ratio, effective_factor) for one anchor's share basis.

    yfinance is wrong about microcap splits in BOTH directions -- missing splits IBKR
    has already back-adjusted in, and listing splits IBKR has not yet processed -- so
    `fwd` is a hypothesis and a point-in-time close is the referee. See
    market_state_calculator.basis_check for the measured evidence.

    Flags: ok / bars_behind / ref_stale / basis_mismatch / bars_stale / ref_disagree
    / no_ref.
    """
    if not bar_close or bar_close <= 0 or fwd is None or fwd <= 0:
        return "no_ref", None, fwd

    prefixes = split_prefix_products(splits, anchor_date, bars_fetched_at)

    if not price_ref or price_ref <= 0 or not math.isfinite(price_ref):
        newest = max((d for d, f in (splits or []) if d > anchor_date), default=None)
        if newest is not None and bars_fetched_at is not None \
                and bars_fetched_at <= newest:
            return "bars_stale", None, fwd
        return "no_ref", None, fwd

    ratio = (bar_close * fwd) / price_ref
    agree = lambda r: BASIS_AGREE_LO <= r <= BASIS_AGREE_HI

    if price_ref_src == "universe":
        best, best_err = None, None
        for cand in [1.0] + prefixes:
            err = abs(math.log((bar_close * cand) / price_ref))
            if best_err is None or err < best_err:
                best, best_err = cand, err
        if best is not None and agree((bar_close * best) / price_ref):
            if abs(math.log(best / fwd)) < 1e-9:
                return "ok", ratio, fwd
            return "bars_behind", ratio, best
    elif agree(ratio):
        return "ok", ratio, fwd
    else:
        for prefix in prefixes:
            if agree(ratio / prefix):
                return "ref_stale", ratio, fwd

    if agree(ratio):
        return "ok", ratio, fwd
    if plausible_split_ratio(ratio) is not None:
        return "basis_mismatch", ratio, fwd
    return "ref_disagree", ratio, fwd


def is_missing(value) -> bool:
    """True if a value has no usable numeric value."""
    return pd.isna(pd.to_numeric(value, errors='coerce'))


def load_universe_price_refs(ref_dir: Path, session_map_path: Path) -> dict:
    """{(SYMBOL, session): close} from the nightly universe snapshots.

    A snapshot is written once and never rewritten, so it is the only reference that
    can settle a basis question outright. WHICH SESSION a file describes is not its
    filename: the convention changed partway through the corpus (the June files were
    hand-run before dawn, the evening cron only settles in from ~2026-07-01), so the
    mapping is read from the calibration audit_lastdailyclose.py writes. Without that
    file no universe reference is used -- guessing the session is worse than having
    no referee, because a one-session error hides inside basis_check's agreement band.
    """
    refs: dict = {}
    if not session_map_path.exists() or not ref_dir.is_dir():
        logging.warning(
            f"No universe session map at {session_map_path} — skipping the basis "
            f"referee. Run audit_lastdailyclose.py to build it.")
        return refs
    try:
        smap_df = pd.read_csv(session_map_path, sep='\t')
        smap = {str(r.file_date): str(r.session) for r in smap_df.itertuples()}
    except Exception as e:
        logging.warning(f"Unreadable universe session map {session_map_path}: {e}")
        return refs

    for file_date, session in smap.items():
        path = ref_dir / f"stocks_universe_{file_date}.tsv"
        if not path.exists():
            continue
        try:
            t = pd.read_csv(path, sep='\t', dtype=str, keep_default_na=False,
                            usecols=lambda c: c in ("Symbol", "LastDailyClosePrice"))
        except Exception:
            continue
        if "Symbol" not in t.columns or "LastDailyClosePrice" not in t.columns:
            continue
        try:
            sess = date.fromisoformat(session)
        except ValueError:
            continue
        for sym, px in zip(t["Symbol"], t["LastDailyClosePrice"]):
            try:
                v = float(px)
            except (TypeError, ValueError):
                continue
            if v > 0:
                refs.setdefault((str(sym).strip().upper(), sess), v)
    logging.info(f"Universe price refs: {len(refs)} point-in-time closes "
                 f"from {len(smap)} calibrated snapshots.")
    return refs


def fetch_yf_close(symbol: str, session: date, cache: dict):
    """Close of `session` from yfinance, or None when Yahoo has no such bar.

    The fallback for symbols IBKR will no longer qualify -- microcaps delisted between
    the PR and the backfill, which is every one of the 53 rows the 2026-09-04 run left
    empty. Yahoo keeps some of them and has dropped the pre-July history of others, so
    this recovers a majority, not all.

    The close is split-adjusted to today, exactly like an IBKR bar, so the SAME F
    applies. The caller multiplies; do not adjust here.
    """
    key = symbol.upper()
    if key not in cache:
        hist = {}
        try:
            import yfinance as yf
            h = yf.Ticker(key).history(period='max', auto_adjust=False, actions=False)
            if h is not None and not h.empty:
                for ts, px in zip(h.index, h['Close']):
                    if pd.notna(px) and float(px) > 0:
                        hist[ts.date()] = float(px)
        except Exception as e:
            logging.warning(f"{key}: yfinance history lookup failed — {e}")
        cache[key] = hist
    return cache[key].get(session)


def main():
    parser = argparse.ArgumentParser(
        description=(
            'Enrich a PR-stats TSV with LastDailyClosePrice column by matching PR IDs '
            'against orchestrator5 and orchestrator3 daily tables. For unmatched rows, '
            'fall back to live IBKR fetch. Output is written to <input_stem>_DailyClose.tsv.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('--input', '-i', required=True, metavar='FILE',
                        dest='input_file',
                        help='Input TSV (needs Symbol, ArrivalDate, ArrivalTime, Exchange, ID).')
    parser.add_argument('--output-dir', '-o', metavar='DIR', dest='output_dir',
                        default=None,
                        help="Directory for the output TSV. Defaults to the input file's directory.")
    parser.add_argument('--log', '-l', metavar='FILE', dest='log_file',
                        default=None, help='Optional log file (appended).')
    parser.add_argument('--port', type=int, default=4002, metavar='N',
                        help='IB Gateway port. Default 4002 (PAPER): the live gateway '
                             'on 4001 drops bulk historical requests under contention.')
    parser.add_argument('--fill-missing-only', action='store_true',
                        help='Only fill rows where LastDailyClosePrice is empty. Values '
                             'a previous run already wrote are left untouched — re-fetching '
                             'them would overwrite split-corrected prices.')
    parser.add_argument('--no-yfinance-fallback', action='store_true',
                        help='Do not fall back to yfinance when IBKR cannot qualify the '
                             'contract (delisted symbols stay empty).')
    parser.add_argument('--no-ibkr', action='store_true',
                        help='Skip IBKR entirely and fill from yfinance alone. For a '
                             '--fill-missing-only pass over rows a previous run already '
                             'proved IBKR cannot qualify, the gateway adds nothing but '
                             'a round trip — and this still works when it is down.')
    args = parser.parse_args()

    setup_logging(args.log_file)
    if args.log_file:
        logging.info(f"Logging to {os.path.abspath(args.log_file)}")

    if not os.path.isfile(args.input_file):
        logging.error(f"Input file not found: {args.input_file}")
        return

    df = pd.read_csv(args.input_file, sep='\t')
    if 'ID' not in df.columns:
        logging.error("Input TSV must have an 'ID' column.")
        return
    if 'Symbol' not in df.columns:
        logging.error("Input TSV must have a 'Symbol' column.")
        return
    if 'ArrivalDate' not in df.columns:
        logging.error("Input TSV must have an 'ArrivalDate' column.")
        return

    for col, default in (('LastDailyClosePrice', None),
                         ('LastDailyClosePrice_SplitAdj', ""),
                         ('LastDailyClosePrice_BasisFlag', ""),
                         ('LastDailyClosePrice_Source', "")):
        if col not in df.columns:
            df[col] = default

    orch_matched_5 = orch_matched_3 = filled_live = filled_yf = 0
    filled_yf_unverified = failed = skipped_exchange = 0
    still_missing = []

    if args.fill_missing_only:
        todo = [i for i in df.index if is_missing(df.at[i, 'LastDailyClosePrice'])]
        logging.info(
            f"--fill-missing-only: {len(todo)} of {len(df)} rows are empty; the other "
            f"{len(df) - len(todo)} keep the values they already have.")
        still_missing = todo
    else:
        logging.info(f"Starting orchestrator table lookup for {len(df)} rows...")
        for idx, row in df.iterrows():
            pr_id = str(row['ID']).strip()
            arrival_date = str(row['ArrivalDate']).strip()

            # Try orchestrator tables first.
            price, source = lookup_in_orchestrator_tables(pr_id, arrival_date)
            if price is not None:
                df.at[idx, 'LastDailyClosePrice'] = price
                df.at[idx, 'LastDailyClosePrice_Source'] = source
                if source == 'orch5':
                    orch_matched_5 += 1
                else:
                    orch_matched_3 += 1
            else:
                still_missing.append(idx)

        logging.info(
            f"Orchestrator lookup done: {orch_matched_5} from orch5, "
            f"{orch_matched_3} from orch3, {len(still_missing)} still missing."
        )

    price_refs = load_universe_price_refs(
        UNIVERSE_DIR, SCRIPT_DIR / 'cache' / 'universe_session_map.tsv')

    # Live IBKR fetch for still-missing rows.
    aborted = False
    cache: dict = {}
    if still_missing:
        ib = IB()
        try:
            if args.no_ibkr:
                logging.info("--no-ibkr: filling from yfinance alone, no gateway needed.")
            else:
                logging.info(f"Connecting to IBKR at {IB_HOST}:{args.port} clientId={CLIENT_ID}...")
                try:
                    ib.connect(IB_HOST, args.port, clientId=CLIENT_ID, timeout=20)
                except ConnectionRefusedError:
                    logging.error(
                        f"Connection refused on {IB_HOST}:{args.port}. "
                        "Make sure TWS or IB Gateway is running with API connections enabled."
                    )
                    return
                except Exception as e:
                    logging.error(f"Could not connect to IBKR: {e}")
                    return
                if not ib.isConnected():
                    logging.error("Failed to connect to IBKR. Exiting.")
                    return
                logging.info("Connected.")

            splits_cache: dict = {}  # symbol -> splits list
            yf_cache: dict = {}      # symbol -> {session: close}
            fetched_at = date.today()
            consecutive_failures = 0

            for idx in still_missing:
                row = df.loc[idx]
                symbol = str(row['Symbol']).strip()
                arrival_str = str(row['ArrivalTime']).strip()
                arrival_date_str = str(row.get('ArrivalDate', '')).strip()
                exchange = str(row.get('Exchange', '')).strip()

                if exchange and not is_us_exchange(exchange):
                    logging.info(f"{symbol}: exchange '{exchange}' not in US subscription — skipping.")
                    skipped_exchange += 1
                    continue

                try:
                    target_dt = get_target_date(arrival_str, arrival_date_str)
                    anchor_dt = prev_trading_day(target_dt)
                except Exception as e:
                    logging.warning(f"Row {idx} ({symbol}): could not parse ArrivalTime '{arrival_str}' — {e}")
                    failed += 1
                    continue

                cache_key = (symbol, target_dt)
                if cache_key not in cache:
                    if args.no_ibkr:
                        prev_close, status = None, 'skip'
                    else:
                        logging.info(f"Fetching prior close for {symbol} on {target_dt}...")
                        _high, prev_close, status = fetch_hod_and_prev_close(ib, symbol, target_dt)

                    if status == 'skip' and prev_close is None and not args.no_yfinance_fallback:
                        # IBKR will not qualify the contract — the symbol was delisted
                        # between the PR and now. Yahoo still has some of these.
                        prev_close = fetch_yf_close(symbol, anchor_dt, yf_cache)
                        status = 'yfinance' if prev_close is not None else status
                        logging.info(
                            f"{symbol}: {'skipping IBKR' if args.no_ibkr else 'IBKR could not qualify'}; "
                            f"yfinance "
                            f"{'gave ' + format(prev_close, '.4f') if prev_close is not None else 'has no bar'} "
                            f"for {anchor_dt}.")

                    cache[cache_key] = (prev_close, status)

                    if status == 'failure':
                        consecutive_failures += 1
                        if consecutive_failures >= CONSECUTIVE_FAILURE_ABORT:
                            logging.error(
                                f"{consecutive_failures} consecutive fetches failed — HMDS appears down. "
                                f"Aborting to avoid silent data loss; restart IB Gateway and re-run. "
                                f"Last attempted: {symbol} on {target_dt} (row {idx})."
                            )
                            aborted = True
                            break
                    elif status in ('ok', 'yfinance'):
                        consecutive_failures = 0

                    if not args.no_ibkr:
                        ib.sleep(PACING_SLEEP_SECONDS)
                else:
                    prev_close, status = cache[cache_key]

                if prev_close is None:
                    # Nothing anywhere has this symbol's history any more.
                    df.at[idx, 'LastDailyClosePrice_Source'] = (
                        'unrecoverable_no_history' if status == 'skip' else '')
                    failed += 1
                    continue

                # The bar is quoted in TODAY's shares; F puts it back on the shares
                # that actually traded at the anchor. Anchor on the session the close
                # belongs to (the one before the PR's session), not on the PR's
                # session -- a split with an ex_date ON the PR session moves that
                # close and would otherwise be missed. MSC anchors its own bars
                # differently because its anchor IS the bar's session.
                if symbol not in splits_cache:
                    splits_cache[symbol] = fetch_splits(symbol, MSC_CACHE_DIR, use_cache=True)

                splits = splits_cache[symbol]
                fwd = forward_split_factor(splits, anchor_date=anchor_dt,
                                           bars_fetched_at=fetched_at)

                if fwd is None:
                    # Split lookup failed — keep the bar as-is and say so.
                    value = prev_close
                    df.at[idx, 'LastDailyClosePrice_SplitAdj'] = 'unknown'
                else:
                    value = prev_close * fwd
                    df.at[idx, 'LastDailyClosePrice_SplitAdj'] = (
                        'adjusted' if fwd != 1.0 else 'none_needed')

                # F came from yfinance, which is wrong in both directions on microcaps.
                # Referee it against a point-in-time close whenever one exists.
                ref = price_refs.get((symbol.upper(), anchor_dt))
                flag, _ratio, _eff = basis_check(prev_close, fwd if fwd else 1.0, ref,
                                                 'universe' if ref else '', splits,
                                                 anchor_dt, fetched_at)
                df.at[idx, 'LastDailyClosePrice_BasisFlag'] = flag
                if flag in ('basis_mismatch', 'bars_behind', 'bars_stale'):
                    logging.warning(
                        f"{symbol} {anchor_dt}: basis_check says '{flag}' "
                        f"(value {value}, reference {ref}, F {fwd}) — the split factor "
                        f"is not supported by the point-in-time close.")

                df.at[idx, 'LastDailyClosePrice'] = value
                if status != 'yfinance':
                    src_label = 'ibkr'
                elif fwd is not None and fwd == 1.0:
                    # No split between the anchor and now, so there is no factor to get
                    # wrong: Yahoo's close IS the as-traded close. Safe to use.
                    src_label = 'yfinance'
                else:
                    # A split sits between the anchor and now, so this value depends on
                    # a factor that could not be refereed. Yahoo's own series is not
                    # consistent about whether it has applied a split it lists -- IPW's
                    # 2026-08-06 1-for-9 is in Ticker.splits while the price series
                    # still shows the jump, and GCTK's listed 2026-08-30 ex-date does
                    # not match where its series actually steps. Getting it wrong is a
                    # 9x-15x error, so the value is written but NOT presented as
                    # trustworthy: filter on this before using it as a denominator.
                    src_label = 'yfinance_unverified_basis'
                df.at[idx, 'LastDailyClosePrice_Source'] = src_label
                if status == 'yfinance':
                    filled_yf += 1
                    if src_label == 'yfinance_unverified_basis':
                        filled_yf_unverified += 1
                else:
                    filled_live += 1

        finally:
            if ib.isConnected():
                ib.disconnect()
                logging.info("Disconnected.")

    out_path = build_output_path(args.input_file, args.output_dir)
    if aborted:
        # A truncated run must not be mistaken for a complete one by whatever reads
        # this next: the remaining rows were never attempted, they are not "no data".
        out_path = out_path[:-len('.tsv')] + '.PARTIAL.tsv'
        logging.error(f"Run was ABORTED mid-fetch — writing a partial file to {out_path}")

    df.to_csv(out_path, sep='\t', index=False)

    logging.info(
        f"Done: {orch_matched_5} matched orch5, {orch_matched_3} matched orch3, "
        f"{filled_live} filled via IBKR, {filled_yf} filled via yfinance "
        f"(of which {filled_yf_unverified} carry an UNVERIFIED split basis), "
        f"{failed} failed, {skipped_exchange} skipped (non-US exchange). "
        f"Unique (symbol, date) lookups: {len(cache)}."
    )
    if filled_yf_unverified:
        logging.warning(
            f"{filled_yf_unverified} rows are tagged 'yfinance_unverified_basis': a "
            f"split falls between the anchor and now, and no point-in-time close exists "
            f"to referee the factor. Treat these as provisional.")
    logging.info(f"Saved to {out_path}")


if __name__ == '__main__':
    main()
