#!/usr/bin/env python3
"""
NewsWatcher3.py
---------------
Real-time press-release / news monitor using the RTPR.io WebSocket firehose.

Applies a filter pipeline to each incoming article.  Articles that pass are
stored as "accepted" objects; articles that fail are stored as "blocked"
objects.  Both sets are flushed to disk on a periodic timer (and once more
on graceful shutdown).

Public API
----------
    import NewsWatcher3 as nw

    nw.start(
        universe_tsv='./nasdaq_symbols_data.tsv',
        black_list='./black_list.csv',
        blacklist_expiry_hours=168,
        api_keys='./RTPR_API-Key.txt',
    )

    df  = nw.get_news_df()                       # accepted DataFrame
    obj = nw.get_news_object('id-51772090')      # accepted article
    obj = nw.get_blocked_object('id-51772090')   # article blocked by filters
    nw.update_universe(new_list)
    nw.stop()
"""

import asyncio
import json
import logging
import os
import signal
import threading
from datetime import datetime
from pathlib import Path

try:
    import pandas as pd
except ImportError:
    raise ImportError("pandas is required. Install with: pip install pandas")

try:
    import websockets
except ImportError:
    raise ImportError("websockets is required. Install with: pip install websockets")


# ─── Module-level private state ───────────────────────────────────────────────

_news_df: pd.DataFrame = pd.DataFrame(columns=["ID", "ArrivalTime", "Symbol", "Headline"])
_news_objects: dict = {}             # accepted articles, keyed by 'id-<id>'
_blocked_objects: dict = {}          # articles that did not pass filters, keyed by 'id-<id>'

_blacklist_set: set = set()          # in-memory O(1) lookup
_blacklist_records: list = []        # [{'Symbol': ..., 'Date': 'DD-MM-YYYY', 'ID': ...}, ...]
_universe_set: set = set()           # O(1) ticker membership

_seen_ids: set = set()               # silent in-memory dedup
_rejected_count: int = 0
_rejected_lock = threading.Lock()

_excluded_strings_lower: list = []   # pre-lowercased substrings

_priced_data: dict = {}              # {symbol: {'Float_M': float|None, 'LastDailyClosePrice': float|None}}

_df_lock = threading.Lock()
_objects_lock = threading.Lock()           # protects _news_objects + _seen_ids
_blocked_lock = threading.Lock()           # protects _blocked_objects
_blacklist_lock = threading.Lock()
_universe_lock = threading.Lock()
_priced_lock = threading.Lock()
_shutdown_event = threading.Event()

_background_thread: threading.Thread | None = None
_config: dict = {}

_news_callback = None
_callback_lock = threading.Lock()

logger = logging.getLogger("NewsWatcherV3")

RTPR_WS_URL_TEMPLATE = "wss://ws.rtpr.io?apiKey={key}"


# ─── Public API ───────────────────────────────────────────────────────────────

def start(
    universe_tsv: str = '/home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/data/nasdaq_symbols_data_priced.tsv',
    black_list: str = '/home/tom/Documents/ibkr_scripts/N2/scripts/orchestrator3/black_list.csv',
    blacklist_expiry_hours: int = 24,
    api_keys: str = './RTPR_API-Key.txt',
    log_dir: str = './logs',
    output_dir: str = './outputs',
    news_df_dir: str = './outputs',
    blocked_dir: str = './blocked_PRs',
    accepted_dir: str = './accepted_PRs',
    excluded_strings_file: str = './excluded_strings.txt',
    excluded_strings: list = None,
    priced_tsv: str | None = None,
    reject_float_greater_then: float = 50,
    reject_price_greater_then: float = 2.00,
    flush_interval_seconds: int = 300,
) -> None:
    """
    Start NewsWatcherV3.

    Loads credentials, universe, blacklist, and excluded-strings, sets up
    logging, and launches a background daemon thread that connects to the
    RTPR.io WebSocket firehose.  Returns immediately.

    Args:
        universe_tsv:           Path to TSV containing the ticker universe.  The
                                first column (header 'Symbol') is read.
        black_list:             CSV path persisting the blacklist (Symbol,Date,ID).
        blacklist_expiry_hours: Entries older than this many hours are purged on
                                load.
        api_keys:               Path to the RTPR API key file (single 'Key:' line).
        log_dir:                Directory for log files.
        output_dir:             Directory where per-symbol (NW2-parity) JSON files
                                are written.
        news_df_dir:            Directory where the daily NewsDF TSV is written.
        blocked_dir:            Directory where articles that did not pass the
                                filter pipeline are written as JSON files on flush.
        accepted_dir:           Directory where articles that passed the filter
                                pipeline are written as JSON files on flush.
        excluded_strings_file:     File containing one excluded substring per line.
                                   Used unless `excluded_strings` is provided.
        excluded_strings:          Explicit list overriding the file.
        priced_tsv:                Path to TSV with Symbol, Float_M, LastDailyClosePrice
                                   columns. When provided, enables the float/price filter.
                                   None disables the filter entirely.
        reject_float_greater_then: Block articles whose ticker has Float_M > this value (M).
        reject_price_greater_then: Block articles whose ticker has LastDailyClosePrice > this.
        flush_interval_seconds:    How often in-memory state is flushed to disk.
    """
    global _background_thread, _config
    global _news_df, _news_objects, _blocked_objects
    global _blacklist_set, _blacklist_records, _universe_set
    global _seen_ids, _shutdown_event, _rejected_count, _excluded_strings_lower, _priced_data

    if _background_thread is not None and _background_thread.is_alive():
        raise RuntimeError(
            "NewsWatcherV3 is already running. Call stop() first."
        )

    # Reset state (safe — background thread not yet alive)
    _shutdown_event = threading.Event()
    _news_df = pd.DataFrame(columns=["ID", "ArrivalTime", "Symbol", "Headline"])
    _news_objects = {}
    _blocked_objects = {}
    _blacklist_set = set()
    _blacklist_records = []
    _universe_set = set()
    _seen_ids = set()
    _rejected_count = 0
    _excluded_strings_lower = []
    _priced_data = {}

    # Restore today's DataFrame from disk if a prior session already wrote it
    today_str = datetime.now().strftime('%Y-%m-%d')
    existing_tsv = Path(news_df_dir) / f"NewsDF-{today_str}.tsv"
    if existing_tsv.exists():
        try:
            loaded_df = pd.read_csv(existing_tsv, sep='\t')
            if not loaded_df.empty:
                _news_df = loaded_df
                _seen_ids.update(str(x) for x in loaded_df['ID'].tolist())
                logger.info(f"Restored {len(loaded_df)} rows from existing TSV: {existing_tsv}")
        except Exception as e:
            logger.warning(f"Could not load existing TSV {existing_tsv}: {e}")

    _config = {
        'universe_tsv':           universe_tsv,
        'black_list':             black_list,
        'blacklist_expiry_hours': blacklist_expiry_hours,
        'api_keys':               api_keys,
        'log_dir':                log_dir,
        'output_dir':             output_dir,
        'news_df_dir':            news_df_dir,
        'blocked_dir':            blocked_dir,
        'accepted_dir':           accepted_dir,
        'excluded_strings_file':    excluded_strings_file,
        'priced_tsv':               priced_tsv,
        'reject_float_greater_then': reject_float_greater_then,
        'reject_price_greater_then': reject_price_greater_then,
        'flush_interval_seconds':   flush_interval_seconds,
    }

    _setup_logging(log_dir)

    logger.info("=" * 60)
    logger.info("NewsWatcherV3 starting")
    logger.info(f"Universe TSV     : {universe_tsv}")
    logger.info(f"Blacklist expiry : {blacklist_expiry_hours} hours")
    logger.info(f"Flush interval   : {flush_interval_seconds}s")
    logger.info(f"Blocked dir      : {blocked_dir}")
    logger.info(f"Accepted dir     : {accepted_dir}")
    if priced_tsv is not None:
        logger.info(f"Priced TSV       : {priced_tsv}")
        logger.info(f"Reject float >   : {reject_float_greater_then}M")
        logger.info(f"Reject price >   : ${reject_price_greater_then}")
    else:
        logger.warning("Priced TSV       : NOT SET — price/float filter DISABLED")
    logger.info("=" * 60)

    _load_universe_tsv(universe_tsv)
    _load_blacklist(black_list, blacklist_expiry_hours)
    if priced_tsv is not None:
        _load_priced_tsv(priced_tsv)

    if excluded_strings is not None:
        _excluded_strings_lower = [s.lower() for s in excluded_strings if s]
        logger.info(f"Excluded strings (override): {len(_excluded_strings_lower)} entries")
    else:
        _load_excluded_strings(excluded_strings_file)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    Path(news_df_dir).mkdir(parents=True, exist_ok=True)
    Path(blocked_dir).mkdir(parents=True, exist_ok=True)
    Path(accepted_dir).mkdir(parents=True, exist_ok=True)

    # Signal handlers — only register from main thread
    def _signal_handler(signum, frame):
        logger.info(f"Signal {signum} received — shutting down...")
        stop()
        raise KeyboardInterrupt

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT,  _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

    _background_thread = threading.Thread(
        target=_thread_main,
        daemon=True,
        name="NewsWatcherV3_bg",
    )
    _background_thread.start()
    logger.info("Background thread started — connecting to RTPR.io firehose...")

    import atexit as _atexit
    _atexit.register(stop)


def stop() -> None:
    """
    Graceful shutdown.

    Signals the background thread, waits up to 15 seconds for the final
    flush, then clears in-memory state.
    """
    global _background_thread

    if _background_thread is None or not _background_thread.is_alive():
        logger.warning("stop() called but NewsWatcherV3 is not running.")
        return

    logger.info("Shutdown requested — signalling background thread...")
    _shutdown_event.set()
    _background_thread.join(timeout=15)

    if _background_thread.is_alive():
        logger.warning("Background thread did not exit within 15 s.")
    else:
        logger.info("Background thread exited cleanly.")

    _background_thread = None

    with _df_lock:
        global _news_df
        _news_df = pd.DataFrame(columns=["ID", "ArrivalTime", "Symbol", "Headline"])
    with _objects_lock:
        _news_objects.clear()
        _seen_ids.clear()
    with _blocked_lock:
        _blocked_objects.clear()
    with _blacklist_lock:
        _blacklist_records.clear()

    logger.info("NewsWatcherV3 stopped.")


def get_news_df() -> pd.DataFrame:
    """Return a copy of the current accepted news DataFrame (thread-safe)."""
    with _df_lock:
        return _news_df.copy()


def get_news_object(id_str: str) -> dict | None:
    """Return the accepted article dict for an id-key, or None if pruned/absent."""
    with _objects_lock:
        obj = _news_objects.get(id_str)
    if obj is None:
        logger.warning(
            f"get_news_object: '{id_str}' not found in memory — "
            "it may have been flushed and pruned already."
        )
    return obj


def get_blocked_object(id_str: str) -> dict | None:
    """Return the blocked article dict for an id-key, or None if pruned/absent."""
    with _blocked_lock:
        obj = _blocked_objects.get(id_str)
    if obj is None:
        logger.warning(
            f"get_blocked_object: '{id_str}' not found in memory — "
            "it may have been flushed and pruned already."
        )
    return obj


def update_universe(new_list: list) -> None:
    """Replace the in-memory universe set used by the filter pipeline."""
    global _universe_set
    with _universe_lock:
        _universe_set = set(new_list)
    _config['stock_universe'] = list(new_list)
    logger.info(f"Universe updated: {len(new_list)} symbols")


def register_callback(fn) -> None:
    """
    Register a callable invoked each time an article passes all filters.

    Called from the background thread with one dict argument:
      {'Symbol': comma-joined tickers, 'ID': ..., 'ArrivalTime': ..., 'Headline': ...}

    Pass None to deregister.  Exceptions in the callback are caught and
    logged.
    """
    global _news_callback
    with _callback_lock:
        _news_callback = fn
    logger.info(f"Callback registered: {fn}")


# ─── Logging setup ────────────────────────────────────────────────────────────

def _setup_logging(log_dir: str) -> None:
    logger.setLevel(logging.DEBUG)
    if logger.handlers:
        return

    Path(log_dir).mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = Path(log_dir) / f"NewsWatcher3_{today}.log"

    fmt = logging.Formatter(
        '%(asctime)s %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    fh = logging.FileHandler(log_file)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    ch = logging.StreamHandler()
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt)

    logger.addHandler(fh)
    logger.addHandler(ch)


# ─── Credentials ──────────────────────────────────────────────────────────────

def _load_rtpr_credentials(file_path: str) -> str:
    """
    Parse the RTPR API key from a file shaped like:

        #RTPR.io API key informations

        API Endpoint:
        https://api.rtpr.io/articles

        Key:
        rtpr_XXXXXXXXXXXXXXX
    """
    if not Path(file_path).exists():
        raise FileNotFoundError(f"API keys file not found: {file_path}")

    api_key = None
    try:
        with open(file_path, 'r') as f:
            lines = f.readlines()
        for i, line in enumerate(lines):
            if line.strip().startswith('Key:'):
                if i + 1 < len(lines):
                    api_key = lines[i + 1].strip()
                    break
    except Exception as e:
        raise ValueError(f"Error parsing API credentials file: {e}")

    if not api_key:
        raise ValueError("Missing 'Key:' field in API credentials file")
    return api_key


# ─── Universe loader ──────────────────────────────────────────────────────────

def _load_universe_tsv(path: str) -> None:
    """Load Symbol column from the universe TSV into _universe_set."""
    global _universe_set

    p = Path(path)
    if not p.exists():
        logger.error(f"Universe TSV not found: {path} — universe_set will be empty")
        return

    try:
        df = pd.read_csv(p, sep='\t')
    except Exception as e:
        logger.error(f"Failed to read universe TSV {path}: {e}")
        return

    if 'Symbol' in df.columns:
        symbols = df['Symbol'].astype(str).str.strip().tolist()
    else:
        symbols = df.iloc[:, 0].astype(str).str.strip().tolist()

    symbols = [s for s in symbols if s and s.lower() != 'nan']
    with _universe_lock:
        _universe_set = set(symbols)
    logger.info(f"Universe loaded: {len(_universe_set)} symbols from {path}")


# ─── Excluded-strings loader ──────────────────────────────────────────────────

def _load_excluded_strings(path: str) -> None:
    global _excluded_strings_lower

    p = Path(path)
    if not p.exists():
        logger.warning(f"Excluded-strings file not found: {path} — using empty list")
        _excluded_strings_lower = []
        return

    try:
        with open(p, 'r') as f:
            entries = [ln.strip().lower() for ln in f if ln.strip()]
    except Exception as e:
        logger.error(f"Failed to read excluded-strings file {path}: {e}")
        _excluded_strings_lower = []
        return

    _excluded_strings_lower = entries
    logger.info(f"Excluded strings loaded: {len(entries)} entries from {path}")


# ─── Priced-data loader ───────────────────────────────────────────────────────

def _load_priced_tsv(path: str) -> None:
    """Load Symbol, Exchange, Float_M, LastDailyClosePrice from the priced TSV into _priced_data."""
    global _priced_data

    p = Path(path)
    if not p.exists():
        logger.error(f"Priced TSV not found: {path} — priced filter will block all articles")
        return

    try:
        df = pd.read_csv(p, sep='\t', usecols=['Symbol', 'Exchange', 'Float_M', 'LastDailyClosePrice'])
    except ValueError:
        # Exchange column absent in older TSV files — load without it
        try:
            df = pd.read_csv(p, sep='\t', usecols=['Symbol', 'Float_M', 'LastDailyClosePrice'])
        except Exception as e:
            logger.error(f"Failed to read priced TSV {path}: {e}")
            return
    except Exception as e:
        logger.error(f"Failed to read priced TSV {path}: {e}")
        return

    result = {}
    for _, row in df.iterrows():
        sym     = str(row['Symbol']).strip()
        float_m = row['Float_M']             if pd.notna(row['Float_M'])             else None
        price   = row['LastDailyClosePrice'] if pd.notna(row['LastDailyClosePrice']) else None
        exch    = str(row['Exchange']).strip() if 'Exchange' in df.columns and pd.notna(row.get('Exchange')) else None
        result[sym] = {'Float_M': float_m, 'LastDailyClosePrice': price, 'Exchange': exch}

    with _priced_lock:
        _priced_data = result
    logger.info(f"Priced TSV loaded: {len(result)} symbols from {path}")


# ─── Blacklist I/O ────────────────────────────────────────────────────────────

def _load_blacklist(path: str, expiry_hours: int) -> None:
    """
    Load CSV (Symbol,Date,ID), purge entries older than expiry_hours hours,
    store in memory.  Creates an empty file with header if absent.
    Date field may be 'DD-MM-YYYY HH:MM' (preferred) or legacy 'DD-MM-YYYY'
    (treated as midnight of that day).
    """
    p = Path(path)

    if not p.exists():
        logger.info(f"Blacklist file not found — creating empty: {path}")
        _write_blacklist_atomic(path, [])
        return

    now = datetime.now()
    surviving = []
    purged = 0

    DATETIME_FORMATS = ['%d-%m-%Y %H:%M']
    DATE_FORMATS     = ['%d-%m-%Y', '%Y-%m-%d', '%m-%d-%Y', '%d/%m/%Y']

    try:
        with open(p, 'r') as f:
            lines = f.readlines()

        if not lines:
            logger.info("Blacklist file is empty.")
            return

        data_lines = [l.strip() for l in lines[1:] if l.strip()]

        for line in data_lines:
            parts = line.split(',')
            if len(parts) < 2:
                continue
            symbol, date_str = parts[0].strip(), parts[1].strip()
            id_val = parts[2].strip() if len(parts) >= 3 else 'NA'

            entry_dt = None
            for fmt in DATETIME_FORMATS:
                try:
                    entry_dt = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if entry_dt is None:
                for fmt in DATE_FORMATS:
                    try:
                        entry_dt = datetime.strptime(date_str, fmt).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        break
                    except ValueError:
                        continue

            if entry_dt is None:
                logger.warning(f"Unparseable blacklist date '{date_str}' for {symbol} — skipping")
                purged += 1
                continue

            age_hours = (now - entry_dt).total_seconds() / 3600
            if age_hours >= expiry_hours:
                purged += 1
                logger.debug(f"Purging blacklist entry: {symbol} ({date_str}, {age_hours:.1f}h old)")
            else:
                surviving.append({'Symbol': symbol, 'Date': date_str, 'ID': id_val})

        if data_lines and not surviving and purged == 0:
            logger.warning(
                f"Blacklist file has {len(data_lines)} line(s) but none could be parsed."
            )

    except Exception as e:
        logger.error(f"Error reading blacklist file: {e}")
        return

    with _blacklist_lock:
        _blacklist_records.clear()
        _blacklist_records.extend(surviving)
        _blacklist_set.clear()
        _blacklist_set.update(r['Symbol'] for r in surviving)

    logger.info(
        f"Blacklist loaded: {len(surviving)} active entries, "
        f"{purged} purged (expiry={expiry_hours}h)"
    )
    if surviving:
        logger.debug(f"Active blacklist: {[r['Symbol'] for r in surviving]}")


def _write_blacklist_atomic(path: str, records: list) -> None:
    """Atomic write (temp + os.replace) of the blacklist CSV."""
    temp_path = path + ".tmp"
    try:
        with open(temp_path, 'w') as f:
            f.write("Symbol,Date,ID\n")
            for r in records:
                f.write(f"{r['Symbol']},{r['Date']},{r.get('ID', 'NA')}\n")
        os.replace(temp_path, path)
        logger.debug(f"Blacklist written: {len(records)} entries → {path}")
    except Exception as e:
        logger.error(f"Error writing blacklist: {e}")


# ─── Per-article JSON writer ──────────────────────────────────────────────────

def _safe_filename_part(s: str) -> str:
    """Strip filesystem-hostile characters from a filename fragment."""
    if not s:
        return 'UNK'
    return ''.join(c if c.isalnum() or c in (',', '-', '_') else '_' for c in s)


def _write_article_json(directory: str, obj: dict, today_str: str) -> str | None:
    """
    Write one article dict to `{directory}/{id}-{ticker}-YYYY-MM-DD.json`
    atomically.  Returns the path written, or None on error.
    """
    article_id = obj.get('id', 'NOID')
    ticker = obj.get('ticker') or 'UNK'
    fname = f"{_safe_filename_part(str(article_id))}-{_safe_filename_part(str(ticker))}-{today_str}.json"
    final_path = Path(directory) / fname
    temp_path = final_path.with_suffix('.json.tmp')
    try:
        with open(temp_path, 'w') as f:
            json.dump(obj, f, indent=2, default=str)
        os.replace(temp_path, final_path)
        return str(final_path)
    except Exception as e:
        logger.error(f"Error writing article JSON {final_path}: {e}")
        return None


# ─── Periodic flush ───────────────────────────────────────────────────────────

def _purge_blacklist_in_memory() -> int:
    """Remove expired entries from the in-memory blacklist without re-reading disk.
    Returns the number of entries purged."""
    expiry_hours = _config.get('blacklist_expiry_hours', 24)
    now = datetime.now()

    DATETIME_FORMATS = ['%d-%m-%Y %H:%M']
    DATE_FORMATS     = ['%d-%m-%Y', '%Y-%m-%d', '%m-%d-%Y', '%d/%m/%Y']

    surviving = []
    purged = 0

    with _blacklist_lock:
        for r in _blacklist_records:
            date_str = r['Date']
            entry_dt = None

            for fmt in DATETIME_FORMATS:
                try:
                    entry_dt = datetime.strptime(date_str, fmt)
                    break
                except ValueError:
                    continue

            if entry_dt is None:
                for fmt in DATE_FORMATS:
                    try:
                        entry_dt = datetime.strptime(date_str, fmt).replace(
                            hour=0, minute=0, second=0, microsecond=0
                        )
                        break
                    except ValueError:
                        continue

            if entry_dt is None:
                surviving.append(r)
                continue

            age_hours = (now - entry_dt).total_seconds() / 3600
            if age_hours >= expiry_hours:
                purged += 1
                logger.debug(
                    f"Mid-session purge: {r['Symbol']} ({date_str}, {age_hours:.1f}h old)"
                )
            else:
                surviving.append(r)

        _blacklist_records.clear()
        _blacklist_records.extend(surviving)
        _blacklist_set.clear()
        _blacklist_set.update(r['Symbol'] for r in surviving)

    if purged:
        logger.info(
            f"Mid-session blacklist purge: {purged} expired entries removed "
            f"(expiry={expiry_hours}h, {len(surviving)} remaining)"
        )
    return purged


def _flush(final: bool = False) -> None:
    """
    Flush in-memory state to disk:
      1. Expire stale blacklist entries (in-memory purge)
      2. Blacklist CSV
      3. Per-article JSON → blocked_PRs/
      4. Per-article JSON → accepted_PRs/
      5. Per-symbol JSON → output_dir/ (NW2 parity)
      6. NewsDF TSV → news_df_dir/
      7. Prune _blocked_objects + _news_objects, clear _seen_ids
    """
    label = "Final flush" if final else "Periodic flush"
    logger.info(f"{label} starting...")

    today_str = datetime.now().strftime('%Y-%m-%d')

    # 1. Mid-session blacklist expiry purge
    _purge_blacklist_in_memory()

    # 2. Blacklist CSV
    with _blacklist_lock:
        records_snapshot = list(_blacklist_records)
    _write_blacklist_atomic(_config['black_list'], records_snapshot)

    # 3. Blocked per-article JSON
    with _blocked_lock:
        blocked_snapshot = dict(_blocked_objects)
    blocked_written = 0
    for key, obj in blocked_snapshot.items():
        if _write_article_json(_config['blocked_dir'], obj, today_str):
            blocked_written += 1

    # 4. Accepted per-article JSON
    with _objects_lock:
        accepted_snapshot = dict(_news_objects)
    accepted_written = 0
    for key, obj in accepted_snapshot.items():
        if _write_article_json(_config['accepted_dir'], obj, today_str):
            accepted_written += 1

    # 5. Per-symbol JSON (NW2 parity) — comma-joined tickers in filename
    output_dir = _config['output_dir']
    per_symbol_written = 0
    for key, obj in accepted_snapshot.items():
        tickers = obj.get('tickers') or []
        if not tickers:
            primary = obj.get('ticker') or 'UNK'
            symbol_str = primary
        else:
            symbol_str = ','.join(tickers)
        symbol_str = _safe_filename_part(symbol_str)
        filepath = Path(output_dir) / f"{symbol_str}-{today_str}.json"
        try:
            with open(filepath, 'w') as f:
                json.dump(obj, f, indent=2, default=str)
            per_symbol_written += 1
        except Exception as e:
            logger.error(f"Error writing per-symbol JSON for {key}: {e}")

    # 6. News DataFrame TSV
    news_df_dir = _config['news_df_dir']
    df_path = Path(news_df_dir) / f"NewsDF-{today_str}.tsv"
    with _df_lock:
        df_snapshot = _news_df.copy()
    try:
        df_snapshot.to_csv(df_path, sep='\t', index=False)
        logger.debug(f"News DataFrame written: {len(df_snapshot)} rows → {df_path}")
    except Exception as e:
        logger.error(f"Error writing news DataFrame TSV: {e}")

    # 7. Prune memory
    with _blocked_lock:
        for key in blocked_snapshot:
            _blocked_objects.pop(key, None)
    with _objects_lock:
        for key in accepted_snapshot:
            _news_objects.pop(key, None)
        _seen_ids.clear()

    logger.info(
        f"{label} complete: {blocked_written} blocked, "
        f"{accepted_written} accepted, {per_symbol_written} per-symbol JSONs, "
        f"{len(df_snapshot)} DataFrame rows."
    )


# ─── Filter pipeline ──────────────────────────────────────────────────────────

def _passes_filters(tickers: list, title: str, article_exchange: str = '') -> tuple[bool, str]:
    """
    Filter pipeline. Returns (passed, reason_if_failed).

      1. len(tickers) <= 2
      2. >= 1 ticker in universe
      3. no ticker in blacklist
      4. no excluded substring in title (case-insensitive)
      5. in-exchange tickers pass Float_M <= reject_float_greater_then and
         LastDailyClosePrice <= reject_price_greater_then (skipped if priced_tsv=None).
         Tickers absent from priced data or listed on a different exchange than
         article_exchange are skipped rather than failed.
    """
    n = len(tickers)
    if n == 0:
        return False, "tickers list is empty"
    if n > 2:
        return False, f"tickers count={n} > 2"

    with _universe_lock:
        in_universe = any(t in _universe_set for t in tickers)
    if not in_universe:
        return False, f"no ticker in universe (tickers={tickers})"

    with _blacklist_lock:
        for t in tickers:
            if t in _blacklist_set:
                return False, f"ticker '{t}' is blacklisted"

    title_lower = title.lower()
    for excl in _excluded_strings_lower:
        if excl in title_lower:
            return False, f"headline contains excluded string '{excl}'"

    with _priced_lock:
        priced_snapshot = _priced_data
    if priced_snapshot:
        reject_float = _config.get('reject_float_greater_then')
        reject_price = _config.get('reject_price_greater_then')
        for t in tickers:
            entry = priced_snapshot.get(t)
            if entry is None:
                continue  # not in our DB — likely a foreign cross-listing; skip
            priced_exch = entry.get('Exchange')
            if article_exchange and priced_exch and priced_exch != article_exchange:
                continue  # different exchange than the article's primary exchange; skip
            float_m = entry['Float_M']
            price   = entry['LastDailyClosePrice']
            if float_m is None:
                return False, f"ticker '{t}' has missing Float_M"
            if price is None:
                return False, f"ticker '{t}' has missing LastDailyClosePrice"
            if reject_float is not None and float_m > reject_float:
                return False, f"ticker '{t}' float={float_m}M > {reject_float}M"
            if reject_price is not None and price > reject_price:
                return False, f"ticker '{t}' price={price} > {reject_price}"

    return True, ''


# ─── Article handler ──────────────────────────────────────────────────────────

async def _handle_article(data: dict) -> None:
    """Process one RTPR article message payload."""
    global _news_df, _rejected_count

    news_id = data.get('id')
    if news_id is None:
        logger.debug("Article without id — skipping")
        return
    news_id = str(news_id)

    tickers_raw = data.get('tickers') or []
    tickers = [str(t).strip() for t in tickers_raw if t]
    title   = data.get('title') or ''
    author  = data.get('author') or ''

    # Silent dedup (not a filter — just skip re-storing)
    with _objects_lock:
        if news_id in _seen_ids:
            return
        _seen_ids.add(news_id)

    arrival = datetime.now()
    id_key = f"id-{news_id}"

    # Apply reduced filter pipeline
    article_exchange = str(data.get('exchange') or '').strip().upper()
    passed, reason = _passes_filters(tickers, title, article_exchange)
    if not passed:
        logger.debug(f"Blocked id={news_id}: {reason}")
        with _blocked_lock:
            _blocked_objects[id_key] = data
        with _rejected_lock:
            _rejected_count += 1
        return

    # Accepted branch: DataFrame + accepted objects + auto-blacklist + callback
    symbol_str = ','.join(tickers)

    new_row = pd.DataFrame([{
        'ID':          news_id,
        'ArrivalTime': arrival,
        'Symbol':      symbol_str,
        'Headline':    title,
    }])
    with _df_lock:
        _news_df = pd.concat([_news_df, new_row], ignore_index=True)

    with _objects_lock:
        _news_objects[id_key] = data

    date_str = arrival.strftime('%d-%m-%Y %H:%M')
    with _blacklist_lock:
        for tk in tickers:
            _blacklist_set.add(tk)
            _blacklist_records.append({'Symbol': tk, 'Date': date_str, 'ID': news_id})

    logger.info(
        f"Accepted: {symbol_str} — id={news_id} | author={author} | "
        f"headline={title[:80]}"
    )

    with _callback_lock:
        cb = _news_callback
    if cb is not None:
        payload = {
            'Symbol':       symbol_str,
            'ID':           news_id,
            'ArrivalTime':  arrival,
            'Headline':     title,
            'article_body': data.get('article_body', ''),
            'exchange':     article_exchange,
        }
        try:
            cb(payload)
        except Exception as exc:
            logger.error(
                f"Exception in callback for id={news_id}: {exc}", exc_info=True
            )


# ─── Async main (runs inside background thread) ───────────────────────────────

async def _async_main(api_key: str) -> None:
    RECONNECT_DELAY     = 10
    FLUSH_INTERVAL      = _config.get('flush_interval_seconds', 300)
    STATUS_INTERVAL     = 60
    NO_DATA_TIMEOUT_SEC = 300  # reconnect if WebSocket is silent for this long

    loop = asyncio.get_running_loop()
    async_shutdown = asyncio.Event()

    async def _watch_shutdown():
        while not async_shutdown.is_set():
            await asyncio.sleep(0.5)
            if _shutdown_event.is_set():
                async_shutdown.set()

    async def _periodic_flush():
        try:
            while not async_shutdown.is_set():
                await asyncio.sleep(FLUSH_INTERVAL)
                if async_shutdown.is_set():
                    break
                _flush(final=False)
        except asyncio.CancelledError:
            pass

    async def _status_logger():
        try:
            while not async_shutdown.is_set():
                await asyncio.sleep(STATUS_INTERVAL)
                if async_shutdown.is_set():
                    break
                with _df_lock:
                    accepted_count = len(_news_df)
                with _blocked_lock:
                    blocked_count = len(_blocked_objects)
                with _blacklist_lock:
                    bl_size = len(_blacklist_set)
                with _rejected_lock:
                    rejected_count = _rejected_count
                logger.info(
                    f"Connection active — accepted {accepted_count}, "
                    f"blocked (in mem) {blocked_count}, "
                    f"rejected {rejected_count}, "
                    f"blacklist {bl_size}"
                )
        except asyncio.CancelledError:
            pass

    async def _ws_loop():
        """Connect to RTPR, subscribe firehose, handle messages until disconnect."""
        url = RTPR_WS_URL_TEMPLATE.format(key=api_key)
        async with websockets.connect(url, ping_interval=None) as ws:
            logger.info("WebSocket connected to RTPR.io")
            while not async_shutdown.is_set():
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=NO_DATA_TIMEOUT_SEC)
                except asyncio.TimeoutError:
                    logger.warning(
                        f"No data received for {NO_DATA_TIMEOUT_SEC}s — "
                        "triggering reconnect"
                    )
                    break
                try:
                    msg = json.loads(raw)
                except Exception as e:
                    logger.error(f"Failed to parse RTPR message: {e}; raw={raw[:200]}")
                    continue

                msg_type = msg.get('type')

                if msg_type == 'connected':
                    logger.info(f"RTPR connected: {msg.get('message', '')}")
                    await ws.send(json.dumps({'action': 'subscribe', 'tickers': ['*']}))
                    logger.info("Sent firehose subscription request (tickers=['*']).")
                elif msg_type == 'subscribed':
                    logger.info(f"RTPR subscribed: {msg.get('message', '')}")
                elif msg_type == 'ping':
                    await ws.send(json.dumps({'type': 'pong'}))
                elif msg_type == 'article':
                    data = msg.get('data') or {}
                    await _handle_article(data)
                elif msg_type == 'error':
                    logger.error(f"RTPR error message: {msg}")
                else:
                    logger.debug(f"Unknown RTPR message type '{msg_type}': {str(msg)[:200]}")

    shutdown_watcher_task = asyncio.create_task(_watch_shutdown())

    while not async_shutdown.is_set():
        try:
            logger.info("Establishing WebSocket connection to RTPR firehose...")
            flush_task  = asyncio.create_task(_periodic_flush())
            status_task = asyncio.create_task(_status_logger())
            stream_task = asyncio.create_task(_ws_loop())

            done, pending = await asyncio.wait(
                [stream_task, flush_task, status_task, shutdown_watcher_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                if task is not shutdown_watcher_task:
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass

            for task in done:
                if task is stream_task:
                    try:
                        exc = task.exception()
                        if exc:
                            logger.error(f"Stream error: {exc}")
                    except asyncio.CancelledError:
                        pass

            if async_shutdown.is_set():
                logger.info("Shutdown requested — stopping connection loop.")
                break

        except asyncio.CancelledError:
            logger.info("Connection loop cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in connection loop: {e}")

        if not async_shutdown.is_set():
            logger.info(f"Reconnecting in {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)

    shutdown_watcher_task.cancel()
    try:
        await shutdown_watcher_task
    except asyncio.CancelledError:
        pass

    logger.info("Running final flush before exit...")
    _flush(final=True)

    with _df_lock:
        final_count = len(_news_df)
    with _blacklist_lock:
        bl_size = len(_blacklist_set)

    logger.info("=" * 60)
    logger.info("NewsWatcherV3 shutdown complete")
    logger.info(f"  Total accepted items : {final_count}")
    logger.info(f"  Blacklist size       : {bl_size}")
    logger.info("=" * 60)


# ─── Background thread entry point ────────────────────────────────────────────

def _thread_main() -> None:
    try:
        api_key = _load_rtpr_credentials(_config['api_keys'])
        logger.info("RTPR credentials loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load RTPR credentials: {e}")
        return

    try:
        asyncio.run(_async_main(api_key))
    except Exception as e:
        logger.error(f"Unhandled exception in async main: {e}")
