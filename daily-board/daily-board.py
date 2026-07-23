#!/usr/bin/env python3
"""
daily-board.py — live terminal board for post-PR price action.

Imports the latest news_output_*.tsv produced by orchestrator3 and shows it as a
scrollable, auto-refreshing table in the terminal (curses TUI — works great over
SSH, no browser needed). Four columns are added immediately after Symbol:

    PostPRhigh(%)  — the run-up from the arrival price to the post-PR high:
                     (High - ArrivalPrice) / ArrivalPrice * 100  (always >= 0).
    PostPRlow(%)   — the drop from the arrival price to the post-PR low:
                     (Low - ArrivalPrice) / ArrivalPrice * 100  (always <= 0).
    PostPRnow(%)   — the current return from the arrival price:
                     (Now - ArrivalPrice) / ArrivalPrice * 100  (either sign).
    Surge          — the local market time when the trade-mole detected a price/volume surge,
                     or "None" if no surge was detected.

Data sourcing (chosen to respect IBKR pacing while refreshing every 30s):
  * ONE historical backfill per symbol at startup  → high & price since ArrivalTime.
  * Then a live streaming market-data subscription per symbol keeps a running max.
  * No tick-by-tick data is used (reqMktData top-of-book/last is an approximation).

The following columns are not displayed: "Tickers", "ID", "ArrivalDate", "Body", "Author".

Controls inside the board:
    Up / Down        scroll one row
    PgUp / PgDn      scroll one page
    Left / Right     pan across columns (Symbol + PostPR columns stay frozen)
    g / G            jump to top / bottom
    s / S            cycle sort column / toggle ascending-descending
    q or Esc         quit

Run with the project's venv (has ib_insync):
    path/to/venv/bin/python daily-board.py
"""

import argparse
import csv
import curses
import glob
import locale
import logging
import math
import os
import re
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ib_insync import IB, Stock

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

# Default directory to scan for the latest news_output_*.tsv. Resolved relative
# to this script's location so it works from any working directory.
# NOTE: the original brief said ".../orchestrator3/tables/", but the files this
# project actually produces live in ".../orchestrator3/outputs/", so that is the
# default here. Override with --dir if needed.
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT_DIR = os.path.normpath(
    os.path.join(_SCRIPT_DIR, '..', 'orchestrator3', 'outputs')
)
INPUT_GLOB_PREFIX = 'news_output_'
MOLE_OUTPUT_DIR = os.path.normpath(
    os.path.join(_SCRIPT_DIR, '..', 'x-wing-mole', 'mole-outputs')
)
NO_SURGE_LABEL = 'No Surge'

IB_HOST = '127.0.0.1'
IB_PORT = 4001
CLIENT_ID = 20000
CONNECT_TIMEOUT = 20

REFRESH_SECONDS = 30           # how often the PostPR values + timestamp recompute
WRITE_SECONDS = 60            # how often the table is written to disk (overwrite)
USE_RTH = False               # False = include pre/post-market in the high
BACKFILL_PACING_SECONDS = 0.15  # pause between startup historical requests
HIST_TIMEOUT = 8              # failed backfills degrade to live-data-only, so fail fast

# TSV export goes to a per-day file in OUTPUT_DIR, named after the input's date
# (e.g. outputs/daily-board_2026-06-15.tsv). It is overwritten every WRITE_SECONDS.
# Override the full path with --out. See _default_out_path().
OUTPUT_DIR = os.path.join(_SCRIPT_DIR, 'outputs')
LOG_DIR = os.path.join(_SCRIPT_DIR, 'logs')

# ArrivalTime values are wall-clock US/Eastern (handles EDT/EST automatically).
ET = ZoneInfo('America/New_York')

# Column layout
# Four columns inserted right after Symbol: three PostPR percentages + Surge time.
# Symbol + PostPRhigh(%) stay pinned left.
DOLLAR_COLS = {'PostPRhigh($)', 'PostPRlow($)', 'PostPRnow($)'}
PCT_COLS = {'PostPRhigh(%)', 'PostPRlow(%)', 'PostPRnow(%)'}
NEW_COLS = ['PostPRhigh(%)', 'PostPRlow(%)', 'PostPRnow(%)', 'Surge']
NUM_FROZEN = 2                # Symbol + PostPRhigh(%) stay pinned left
HIDDEN_COLS = {'Tickers', 'ID', 'ArrivalDate', 'Body', 'Author'}  # never displayed
NUMERIC_RIGHT = set(NEW_COLS) | {
    'Float', 'positive', 'negative', 'neutral', 'sentiment_score', 'Trades/sec',
}
WIDTH_CAPS = {'Headline': 60, 'ArrivalTime': 19, 'Surge': 14}
DEFAULT_WIDTH_CAP = 22

LOG_PATH = os.path.join(LOG_DIR, 'daily-board.log')
log = logging.getLogger('daily-board')


def _parse_stop_time(s):
    """Parse a HH:MM auto-stop time (ET) into a datetime.time.

    Returns None when disabled (empty/blank string). Raises ValueError on a
    malformed value so the caller can report it.
    """
    if s is None or not s.strip():
        return None
    return datetime.strptime(s.strip(), '%H:%M').time()


# --------------------------------------------------------------------------- #
# Input file handling
# --------------------------------------------------------------------------- #

def find_latest_file(directory: str):
    """Return the most-recently-modified news_output_*.tsv in directory."""
    try:
        candidates = [
            os.path.join(directory, f)
            for f in os.listdir(directory)
            if f.startswith(INPUT_GLOB_PREFIX) and f.endswith('.tsv')
        ]
    except FileNotFoundError:
        return None
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def _default_out_path(input_path: str):
    """Per-day export path in OUTPUT_DIR, dated from the input filename.

    Falls back to today's date (US/Eastern) if the filename has no YYYY-MM-DD.
    """
    m = re.search(r'(\d{4}-\d{2}-\d{2})', os.path.basename(input_path))
    date_str = m.group(1) if m else datetime.now(ET).strftime('%Y-%m-%d')
    return os.path.join(OUTPUT_DIR, f"daily-board_{date_str}.tsv")


def load_tsv(path: str):
    """Load a TSV into (columns, rows) where rows are dicts keyed by column."""
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        header_line = fh.readline().rstrip('\n')
        columns = header_line.split('\t')
        rows = []
        for line in fh:
            if not line.strip():
                continue
            values = line.rstrip('\n').split('\t')
            # pad/truncate to header length
            values += [''] * (len(columns) - len(values))
            rows.append({c: values[i] for i, c in enumerate(columns)})
    return columns, rows


def parse_arrival_utc(arrival_str: str, arrival_date: str = ''):
    """Parse an ArrivalTime string (naive US/Eastern) into an aware UTC datetime.

    Newer news_output files split the timestamp: ArrivalTime is a bare time with
    milliseconds (e.g. '00:45:01.125') and the date lives in a separate ArrivalDate
    column (e.g. '2026-06-15'). When the time carries no date of its own, prepend
    ArrivalDate so the combined string parses.
    """
    arrival_str = arrival_str.strip()
    arrival_date = (arrival_date or '').strip()
    if arrival_date and '-' not in arrival_str:
        arrival_str = f"{arrival_date} {arrival_str}"
    dt = None
    for fmt in ('%Y-%m-%d %H:%M:%S.%f', '%Y-%m-%d %H:%M:%S',
                '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M:%S'):
        try:
            dt = datetime.strptime(arrival_str, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        # last resort: ISO parser
        dt = datetime.fromisoformat(arrival_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ET)
    return dt.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Surge detection lookup
# --------------------------------------------------------------------------- #

def find_surge_time(symbol, arrival_et, directory=MOLE_OUTPUT_DIR):
    """Search mole-outputs for this symbol's trade-mole file closest to
    arrival_et (an aware ET datetime) and return the surge-detection time
    string, or None if no surge was ever flagged / no file found.

    Robust to filename variations: matches files with the symbol and date
    regardless of prefixes, suffixes, or separator styles (: vs -).
    """
    date_str = arrival_et.strftime('%Y-%m-%d')
    # Flexible glob: match files containing both symbol and date
    pattern = os.path.join(directory, f"*{symbol}*{date_str}*.csv")
    candidates = glob.glob(pattern)
    if not candidates:
        return None

    # Try to extract timestamp from each candidate
    scored = []
    for path in candidates:
        basename = os.path.basename(path)
        # Find where the date string ends in the filename, then look for time after it
        date_idx = basename.find(date_str)
        if date_idx == -1:
            continue
        # Look for time pattern HH-MM-SS or HH:MM:SS after the date
        after_date = basename[date_idx + len(date_str):]
        time_match = re.search(r'(\d{2})[:-](\d{2})[:-](\d{2})', after_date)
        if not time_match:
            continue
        try:
            fdt = datetime.strptime(
                f"{date_str} {time_match.group(1)}-{time_match.group(2)}-{time_match.group(3)}",
                '%Y-%m-%d %H-%M-%S'
            ).replace(tzinfo=ET)
        except ValueError:
            continue
        diff = abs((fdt - arrival_et).total_seconds())
        scored.append((diff, -os.path.getmtime(path), path))

    if not scored:
        return None
    scored.sort()
    return _read_surge_time(scored[0][2])


def _read_surge_time(path):
    """Read a trade-mole-table CSV and return the surge-detection time from
    the first TRADE row with surge_detected=True, NO_SURGE_LABEL if the file
    was read but no such row exists, or None only on a read/parse failure.
    """
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as fh:
            reader = csv.DictReader(fh)
            if not reader.fieldnames:
                return None
            if 'surge_detected' in reader.fieldnames:
                for row in reader:
                    if row.get('event_type') == 'TRADE' and \
                            str(row.get('surge_detected', '')).strip().lower() == 'true':
                        return row.get('Time') or row.get('local_arrival_iso')
            return NO_SURGE_LABEL
    except Exception as exc:  # noqa: BLE001
        log.warning("surge lookup failed for %s — %s", path, exc)
    return None


# --------------------------------------------------------------------------- #
# IBKR data layer
# --------------------------------------------------------------------------- #

class DataManager:
    """Owns the IB connection, per-row backfill, and the live running highs."""

    def __init__(self, ib: IB, columns, rows, file_label, refresh=REFRESH_SECONDS):
        self.ib = ib
        self.file_label = file_label
        self.refresh = refresh

        # Display column order: Symbol, computed columns, Headline, then positioned
        # and remaining columns (minus Symbol itself and any hidden columns).
        rest = [c for c in columns if c not in HIDDEN_COLS and c != 'Symbol']

        # Position specific columns right after Headline
        positioned_after_headline = ['positive', 'FMB_indecisive', 'model_proba_PLUS_kw_mult_nb']
        to_reposition = [c for c in positioned_after_headline if c in rest]
        remaining = [c for c in rest if c not in to_reposition]

        # Find Headline and insert positioned columns after it
        if 'Headline' in remaining:
            headline_idx = remaining.index('Headline')
            rest_reordered = remaining[:headline_idx+1] + to_reposition + remaining[headline_idx+1:]
        else:
            rest_reordered = remaining + to_reposition

        self.display_cols = ['Symbol'] + NEW_COLS + rest_reordered

        # Per-row state.  Each entry mirrors a source row plus computed fields.
        self.rows = rows
        self.symbols = []                 # unique, ordered
        self.contracts = {}               # symbol -> qualified Contract
        self.arrival_price = [None] * len(rows)   # price ~at ArrivalTime, per row
        self.backfill_high = [None] * len(rows)   # high arrival->startup, per row
        self.backfill_low = [None] * len(rows)    # low  arrival->startup, per row
        self.backfill_last = [None] * len(rows)   # last close at startup (now fallback)
        self.surge_time = [None] * len(rows)      # surge detection time per row (positive cache)
        self.live_max = {}                # symbol -> running max since startup
        self.live_min = {}                # symbol -> running min since startup
        self.live_last = {}               # symbol -> most recent live price

        self.last_update_str = '--:--:--'
        self.last_write_str = '--:--:--'
        self.out_path = None              # set from --out in main()
        self.widths = {}

    # -- startup -----------------------------------------------------------

    def prepare(self, max_symbols=0):
        """Qualify contracts, backfill highs, and subscribe to live data.

        Prints plain progress to stdout (curses is not running yet).
        """
        # Determine unique symbols (preserve first-seen order).
        seen = set()
        for r in self.rows:
            sym = str(r.get('Symbol', '')).strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                self.symbols.append(sym)

        if max_symbols and len(self.symbols) > max_symbols:
            print(f"[daily-board] capping {len(self.symbols)} symbols to "
                  f"{max_symbols} (--max-symbols).")
            self.symbols = self.symbols[:max_symbols]

        if len(self.symbols) > 90:
            print(f"[daily-board] WARNING: {len(self.symbols)} symbols — IBKR market "
                  "data line limits (~100) may drop some live quotes.")

        self.ib.reqMarketDataType(1)  # live data

        total = len(self.symbols)
        worst = total * (HIST_TIMEOUT + BACKFILL_PACING_SECONDS)
        print(f"[daily-board] backfilling {total} symbols "
              f"(worst case ~{worst:.0f}s if the data farm is slow)...", flush=True)
        for i, sym in enumerate(self.symbols, 1):
            print(f"[daily-board] ({i}/{total}) {sym}: qualifying...", flush=True)
            contract = self._qualify(sym)
            if contract is None:
                continue
            self.contracts[sym] = contract
            self._backfill_symbol(sym, contract)
            # Subscribe to streaming quotes (top-of-book/last — not tick-by-tick).
            self.ib.reqMktData(contract, '', False, False)
            self.ib.sleep(BACKFILL_PACING_SECONDS)

        # One handler updates every running high as ticks stream in.
        self.ib.pendingTickersEvent += self._on_pending_tickers
        print(f"[daily-board] ready: {len(self.contracts)}/{total} symbols live.")

    def _qualify(self, symbol):
        try:
            qualified = self.ib.qualifyContracts(Stock(symbol, 'SMART', 'USD'))
            if qualified:
                return qualified[0]
            log.warning("%s: could not qualify contract", symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: qualify error — %s", symbol, exc)
        return None

    def _backfill_symbol(self, symbol, contract):
        """ONE historical request per symbol; derive every row's high & arrival price.

        The request spans from the symbol's EARLIEST arrival to now; each row then
        reads its own values off the shared bar list (bars at/after its arrival).
        """
        now_utc = datetime.now(timezone.utc)
        # Row index -> arrival datetime (UTC), for the rows that use this symbol.
        arrivals = {}
        for i, r in enumerate(self.rows):
            if str(r.get('Symbol', '')).strip().upper() != symbol:
                continue
            arrival_raw = str(r.get('ArrivalTime', '')).strip()
            arrival_date = str(r.get('ArrivalDate', '')).strip()
            try:
                arrivals[i] = parse_arrival_utc(arrival_raw, arrival_date)
            except Exception as exc:  # noqa: BLE001
                log.warning("%s row %d: bad ArrivalTime '%s' — %s",
                            symbol, i, arrival_raw, exc)

        # Arrivals in the future rely on live data only.
        past = {i: a for i, a in arrivals.items() if a < now_utc}
        if not past:
            return

        secs = int((now_utc - min(past.values())).total_seconds())
        duration, bar_size, bar_len = self._duration_and_bar(secs)
        try:
            bars = self.ib.reqHistoricalData(
                contract,
                endDateTime='',          # now
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow='TRADES',
                useRTH=USE_RTH,
                formatDate=2,
                keepUpToDate=False,
                timeout=HIST_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("%s: reqHistoricalData error — %s", symbol, exc)
            bars = None

        if not bars:
            print(f"[daily-board] {symbol}: backfill failed — using live data only.",
                  flush=True)
            return

        # IBKR labels each intraday bar by its START time, so the bar that
        # actually CONTAINS the arrival instant has a date a fraction of a
        # second (up to bar_len) before arrival_utc. Include it: that minute is
        # where the PR spike lands. Its open is the pre-spike baseline and its
        # high captures the pop. A bar starting at `d` spans [d, d + bar_len),
        # so it covers the arrival when d + bar_len > arrival_utc.
        cutoff = timedelta(seconds=bar_len)
        for i, arrival_utc in past.items():
            later = [b for b in bars if b.date + cutoff > arrival_utc]
            if not later:
                continue
            self.backfill_high[i] = round(max(b.high for b in later), 4)
            self.backfill_low[i] = round(min(b.low for b in later), 4)
            # Newest bar's close ~ latest price until live ticks take over.
            self.backfill_last[i] = round(later[-1].close, 4)
            # Containing (oldest) bar's open ~ price just before the PR spike.
            self.arrival_price[i] = round(later[0].open, 4)

    @staticmethod
    def _duration_and_bar(secs):
        """Pick an IBKR durationStr / barSize for a span of `secs` seconds.

        Returns (durationStr, barSizeSetting, bar_len_seconds).
        Intraday (the real use case) uses 1-min bars. Stale files spanning more
        than a day are capped so the request still succeeds (the high then covers
        the capped window rather than the full since-arrival range).
        """
        if secs <= 86400:
            return f"{max(secs, 60)} S", '1 min', 60
        days = min(math.ceil(secs / 86400) + 1, 10)
        return f"{days} D", '10 mins', 600

    # -- live updates ------------------------------------------------------

    def _on_pending_tickers(self, tickers):
        for t in tickers:
            sym = t.contract.symbol.upper()
            price = t.last
            if price is None or (isinstance(price, float) and math.isnan(price)) \
                    or price <= 0:
                price = t.marketPrice()
            if price is None or (isinstance(price, float) and math.isnan(price)) \
                    or price <= 0:
                continue
            cur = self.live_max.get(sym)
            if cur is None or price > cur:
                self.live_max[sym] = price
            curmin = self.live_min.get(sym)
            if curmin is None or price < curmin:
                self.live_min[sym] = price
            self.live_last[sym] = price

    # -- surge lookup ------------------------------------------------------

    def _lookup_surge(self, i, row):
        """Look up surge-detection time for row i, cached only for genuine timestamps."""
        if self.surge_time[i]:
            return self.surge_time[i]
        symbol = str(row.get('Symbol', '')).strip().upper()
        arrival_raw = str(row.get('ArrivalTime', '')).strip()
        if not symbol or not arrival_raw:
            return None
        try:
            arrival_et = parse_arrival_utc(
                arrival_raw, str(row.get('ArrivalDate', '')).strip()
            ).astimezone(ET)
        except Exception:  # noqa: BLE001
            return None
        found = find_surge_time(symbol, arrival_et)
        # Only cache genuine timestamps, not NO_SURGE_LABEL (re-check every refresh)
        if found and found != NO_SURGE_LABEL:
            self.surge_time[i] = found
        return found

    # -- snapshot ----------------------------------------------------------

    def build_snapshot(self):
        """Recompute PostPR values and return a render-ready snapshot dict.

        Writes the two computed columns into the source row dicts in place (no
        per-refresh copy of the whole table — keeps the memory footprint flat).
        """
        for i, row in enumerate(self.rows):
            sym = str(row.get('Symbol', '')).strip().upper()
            live = self.live_max.get(sym)
            live_lo = self.live_min.get(sym)
            live_now = self.live_last.get(sym)

            highs = [h for h in (self.backfill_high[i], live) if h is not None]
            high = max(highs) if highs else None

            lows = [l for l in (self.backfill_low[i], live_lo) if l is not None]
            low = min(lows) if lows else None

            # Current price: prefer the latest live tick, else the last bar close.
            now_price = live_now if live_now is not None else self.backfill_last[i]

            arrival = self.arrival_price[i]
            if arrival is None and live is not None:
                # backfill missing → lock the first live price as the reference
                arrival = self.arrival_price[i] = live

            def _pct(v):
                if v is None or arrival in (None, 0):
                    return None
                return (v - arrival) / arrival * 100.0

            row['PostPRhigh($)'] = high
            row['PostPRhigh(%)'] = _pct(high)
            row['PostPRlow($)'] = low
            row['PostPRlow(%)'] = _pct(low)
            row['PostPRnow($)'] = now_price
            row['PostPRnow(%)'] = _pct(now_price)
            row['Surge'] = self._lookup_surge(i, row) or '-'

        self.last_update_str = datetime.now(ET).strftime('%H:%M:%S')
        self.widths = self._compute_widths(self.rows)
        return {
            'cols': self.display_cols,
            'rows': self.rows,
            'widths': self.widths,
            'file': self.file_label,
            'updated': self.last_update_str,
            'saved': self.last_write_str,
            'ib': 'connected' if self.ib.isConnected() else 'DISCONNECTED',
        }

    def _compute_widths(self, rows):
        widths = {}
        for c in self.display_cols:
            cap = WIDTH_CAPS.get(c, DEFAULT_WIDTH_CAP)
            longest = len(c) + 1  # room for a sort arrow
            for r in rows[:250]:
                longest = max(longest, _disp_width(_fmt_cell(c, r)))
            widths[c] = min(max(longest, len(c) + 1), cap) + 1
        # keep the two metric columns readable
        for c in NEW_COLS:
            widths[c] = max(widths[c], 12)
        return widths


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #

def _fmt_cell(col, row):
    val = row.get(col)
    if col in DOLLAR_COLS:
        return '—' if val is None else f"{val:.2f}"
    if col in PCT_COLS:
        return '—' if val is None else f"{val:+.1f}%"
    if val is None:
        return ''
    s = str(val).replace('\t', ' ').replace('\n', ' ').strip()
    return s


def _fmt_cell_tsv(col, row):
    """File-oriented cell formatting: plain numbers, no sign/%, empty when missing.

    Distinct from _fmt_cell (which adds +/%/— for the screen) so the written TSV
    stays machine-readable and re-importable.
    """
    val = row.get(col)
    if col in DOLLAR_COLS or col in PCT_COLS:
        return '' if val is None else f"{val:.2f}"
    if val is None:
        return ''
    # keep the full value (no truncation) but never break the TSV grid
    return str(val).replace('\t', ' ').replace('\r', ' ').replace('\n', ' ')


def _sort_value(col, row):
    """Sort key for a cell: numbers compare numerically, others as lowercase str."""
    val = row.get(col)
    if val is None:
        return (1, 0.0, '')         # None sorts last
    try:
        return (0, float(val), '')
    except (TypeError, ValueError):
        return (0, 0.0, str(val).lower())


# --------------------------------------------------------------------------- #
# Curses UI
# --------------------------------------------------------------------------- #

class ViewState:
    def __init__(self, sort_col):
        self.row_off = 0
        self.hscroll = 0          # index into the non-frozen columns
        self.sort_col = sort_col
        self.sort_desc = True
        self.page = 10            # updated each draw from window height


# color pair ids
C_GREEN, C_RED, C_HEADER, C_DIM = 1, 2, 3, 4


def _init_colors():
    if not curses.has_colors():
        return
    curses.start_color()
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        bg = curses.COLOR_BLACK
    curses.init_pair(C_GREEN, curses.COLOR_GREEN, bg)
    curses.init_pair(C_RED, curses.COLOR_RED, bg)
    curses.init_pair(C_HEADER, curses.COLOR_CYAN, bg)
    curses.init_pair(C_DIM, curses.COLOR_YELLOW, bg)


def _char_width(ch):
    """Terminal cell width of a single character (0, 1, or 2)."""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ('W', 'F') else 1


def _disp_width(text):
    """Number of terminal cells `text` occupies (wide chars count as 2).

    Used instead of len() so column budgets match what the terminal actually
    renders — otherwise multibyte/wide characters (e.g. in news headlines) make
    a cell wider than its column and bleed over the next column.
    """
    return sum(_char_width(ch) for ch in text)


def _pad(text, width, right=False):
    if width <= 0:
        return ''
    if _disp_width(text) > width:
        # Truncate to (width - 1) display cells, then append an ellipsis cell.
        if width == 1:
            return '…'
        acc, kept = 0, []
        for ch in text:
            cw = _char_width(ch)
            if acc + cw > width - 1:
                break
            kept.append(ch)
            acc += cw
        text = ''.join(kept) + '…'
    pad = ' ' * (width - _disp_width(text))
    return (pad + text) if right else (text + pad)


def _safe_addstr(win, y, x, text, attr=0):
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass  # writing the bottom-right cell raises; ignore


def _cell_attr(col, row):
    if col in PCT_COLS:
        v = row.get(col)
        if v is not None:
            if v > 0:
                return curses.color_pair(C_GREEN) | curses.A_BOLD
            if v < 0:
                return curses.color_pair(C_RED)
    if col in DOLLAR_COLS:
        return curses.A_BOLD
    return 0


def _sorted_rows(snap, state):
    rows = snap['rows']
    if state.sort_col not in snap['cols']:
        return rows
    return sorted(
        rows,
        key=lambda r: _sort_value(state.sort_col, r),
        reverse=state.sort_desc,
    )


def write_tsv(dm, snap, state):
    """Atomically overwrite dm.out_path with the table as currently displayed.

    Row order matches the on-screen sort (reuses _sorted_rows); columns match the
    display (Tickers already excluded, PostPR columns already after Symbol).
    Failures are logged, never raised, so the UI keeps running.
    """
    path = dm.out_path
    if not path:
        return
    cols = snap['cols']
    rows = _sorted_rows(snap, state)
    lines = ['\t'.join(cols)]
    lines += ['\t'.join(_fmt_cell_tsv(c, r) for c in cols) for r in rows]
    data = '\n'.join(lines) + '\n'

    tmp = path + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as fh:
            fh.write(data)
        os.replace(tmp, path)            # atomic on the same filesystem
        dm.last_write_str = datetime.now(ET).strftime('%H:%M:%S')
    except Exception as exc:  # noqa: BLE001
        log.warning("failed to write %s — %s", path, exc)
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass


def _draw(stdscr, snap, state):
    stdscr.erase()
    maxy, maxx = stdscr.getmaxyx()
    if maxy < 4 or maxx < 20:
        _safe_addstr(stdscr, 0, 0, "terminal too small", curses.A_BOLD)
        stdscr.noutrefresh()
        curses.doupdate()
        return

    cols = snap['cols']
    widths = snap['widths']
    frozen = cols[:NUM_FROZEN]
    rest = cols[NUM_FROZEN:]

    rows = _sorted_rows(snap, state)
    view_h = maxy - 3                       # data lines (rows 2..maxy-2)
    state.page = max(1, view_h)

    # clamp scroll offsets
    max_row_off = max(0, len(rows) - view_h)
    state.row_off = max(0, min(state.row_off, max_row_off))
    max_hscroll = max(0, len(rest) - 1)
    state.hscroll = max(0, min(state.hscroll, max_hscroll))

    # --- status bar (row 0) ---
    status = (f" daily-board │ {snap['file']} │ {len(rows)} symbols │ "
              f"updated {snap['updated']} │ saved {snap['saved']} │ "
              f"IB: {snap['ib']} │ "
              f"sort: {state.sort_col} {'↓' if state.sort_desc else '↑'} ")
    _safe_addstr(stdscr, 0, 0, _pad(status, maxx), curses.A_REVERSE)

    hdr_attr = curses.color_pair(C_HEADER) | curses.A_BOLD

    def draw_header_cell(y, x, col, avail):
        label = col + (' ↓' if (col == state.sort_col and state.sort_desc)
                       else ' ↑' if col == state.sort_col else '')
        attr = hdr_attr | (curses.A_UNDERLINE if col == state.sort_col else 0)
        _safe_addstr(stdscr, y, x, _pad(label, min(widths[col], avail)), attr)

    # --- header row (row 1) ---
    x = 0
    for c in frozen:
        draw_header_cell(1, x, c, widths[c])
        x += widths[c]
    _safe_addstr(stdscr, 1, x, '│', hdr_attr)
    x += 1
    frozen_x = x
    for c in rest[state.hscroll:]:
        if x >= maxx:
            break
        draw_header_cell(1, x, c, maxx - x)
        x += widths[c]

    # --- data rows ---
    for vi, row in enumerate(rows[state.row_off:state.row_off + view_h]):
        y = 2 + vi
        x = 0
        for c in frozen:
            right = c in NUMERIC_RIGHT
            _safe_addstr(stdscr, y, x, _pad(_fmt_cell(c, row), widths[c], right),
                         _cell_attr(c, row))
            x += widths[c]
        _safe_addstr(stdscr, y, x, '│', curses.color_pair(C_HEADER))
        x = frozen_x
        for c in rest[state.hscroll:]:
            if x >= maxx:
                break
            right = c in NUMERIC_RIGHT
            _safe_addstr(stdscr, y, x,
                         _pad(_fmt_cell(c, row), min(widths[c], maxx - x), right),
                         _cell_attr(c, row))
            x += widths[c]

    # --- footer (last row) ---
    footer = (" ↑↓ row  PgUp/PgDn page  ←→ columns  g/G top/bottom  "
              "s sort  S asc/desc  q quit ")
    _safe_addstr(stdscr, maxy - 1, 0, _pad(footer, maxx), curses.A_REVERSE)

    stdscr.noutrefresh()
    curses.doupdate()


def _handle_key(ch, snap, state):
    """Return False to quit, True to keep running."""
    cols = snap['cols']
    if ch in (ord('q'), 27):
        return False
    elif ch == curses.KEY_UP:
        state.row_off -= 1
    elif ch == curses.KEY_DOWN:
        state.row_off += 1
    elif ch == curses.KEY_NPAGE:
        state.row_off += state.page
    elif ch == curses.KEY_PPAGE:
        state.row_off -= state.page
    elif ch in (curses.KEY_LEFT,):
        state.hscroll -= 1
    elif ch in (curses.KEY_RIGHT,):
        state.hscroll += 1
    elif ch in (ord('g'), curses.KEY_HOME):
        state.row_off = 0
    elif ch in (ord('G'), curses.KEY_END):
        state.row_off = 10 ** 9      # clamped in _draw
    elif ch == ord('s'):
        idx = cols.index(state.sort_col) if state.sort_col in cols else -1
        state.sort_col = cols[(idx + 1) % len(cols)]
    elif ch == ord('S'):
        state.sort_desc = not state.sort_desc
    return True


def run_board(stdscr, dm: DataManager, stop_time=None):
    curses.curs_set(0)
    stdscr.timeout(50)            # getch blocks at most 50ms
    _init_colors()

    state = ViewState(sort_col='PostPRhigh(%)')
    snap = dm.build_snapshot()
    last_compute = time.time()
    last_write = 0.0              # 0 => write immediately on the first iteration

    # Concrete cutoff datetime (ET). The launch-time guard in main() ensures we
    # start before it, so today's date always yields a future cutoff.
    stop_dt = None
    if stop_time is not None:
        stop_dt = datetime.now(ET).replace(
            hour=stop_time.hour, minute=stop_time.minute,
            second=0, microsecond=0)

    while True:
        _draw(stdscr, snap, state)

        ch = stdscr.getch()
        if ch != -1:
            if not _handle_key(ch, snap, state):
                # Flush the final state on quit so the saved file isn't up to
                # WRITE_SECONDS stale.
                write_tsv(dm, snap, state)
                break

        # Pump the IB event loop so streaming ticks update the running highs.
        dm.ib.sleep(0.05)

        now = time.time()
        if now - last_compute >= dm.refresh:
            snap = dm.build_snapshot()
            last_compute = now

        if now - last_write >= WRITE_SECONDS:
            write_tsv(dm, snap, state)
            last_write = now

        # Auto-stop at the scheduled ET time so the board stops holding an IB
        # Gateway API slot + streaming subscriptions overnight. Flush first,
        # mirroring the quit path above.
        if stop_dt is not None and datetime.now(ET) >= stop_dt:
            write_tsv(dm, snap, state)
            break


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def main():
    # Curses needs the locale set so ncurses renders multibyte/Unicode chars
    # (e.g. accented letters or symbols in headlines) at their true cell width;
    # otherwise wide cells bleed over the next column on screen.
    try:
        locale.setlocale(locale.LC_ALL, '')
    except locale.Error:
        pass

    parser = argparse.ArgumentParser(
        description="Live terminal board of post-PR highs from the latest "
                    "news_output TSV.")
    parser.add_argument('--dir', default=DEFAULT_INPUT_DIR,
                        help=f"directory to scan for the latest file "
                             f"(default: {DEFAULT_INPUT_DIR})")
    parser.add_argument('--input', default=None,
                        help="use this exact TSV instead of the latest in --dir")
    parser.add_argument('--host', default=IB_HOST)
    parser.add_argument('--port', type=int, default=IB_PORT)
    parser.add_argument('--client', type=int, default=CLIENT_ID)
    parser.add_argument('--refresh', type=float, default=REFRESH_SECONDS,
                        help=f"seconds between value recomputes "
                             f"(default {REFRESH_SECONDS})")
    parser.add_argument('--max-symbols', type=int, default=0,
                        help="cap number of symbols subscribed (0 = no cap)")
    parser.add_argument('--out', default=None,
                        help=f"TSV file written (overwritten) every {WRITE_SECONDS}s "
                             f"(default: daily-board_<input-date>.tsv in {_SCRIPT_DIR})")
    parser.add_argument('--rth', action='store_true',
                        help="regular trading hours only (default: all hours)")
    parser.add_argument('--stop-time', default='20:00',
                        help="auto-stop the board at this ET wall-clock time "
                             "(HH:MM, default 20:00; use '' to disable) so it "
                             "stops holding an IB Gateway slot overnight")
    args = parser.parse_args()

    global USE_RTH
    if args.rth:
        USE_RTH = True

    try:
        stop_time = _parse_stop_time(args.stop_time)
    except ValueError:
        print(f"[daily-board] Invalid --stop-time {args.stop_time!r} "
              f"(expected HH:MM, e.g. 20:00).")
        return

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    logging.basicConfig(
        filename=LOG_PATH, level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s')
    # ib_insync INFO is very chatty (every position/tick/farm message) — keep the
    # log to warnings so the file and string churn stay small.
    logging.getLogger('ib_insync').setLevel(logging.WARNING)

    path = args.input or find_latest_file(args.dir)
    if not path or not os.path.exists(path):
        print(f"[daily-board] No input file found in {args.dir!r} "
              f"(looking for {INPUT_GLOB_PREFIX}*.tsv).")
        return

    columns, rows = load_tsv(path)
    if not rows:
        print(f"[daily-board] {path} has no data rows.")
        return
    print(f"[daily-board] Loaded {len(rows)} rows from {os.path.basename(path)}.")

    # Late-start guard: if we're already past the auto-stop time, don't even
    # open an IB Gateway connection.
    if stop_time is not None:
        stop_dt = datetime.now(ET).replace(
            hour=stop_time.hour, minute=stop_time.minute,
            second=0, microsecond=0)
        if datetime.now(ET) >= stop_dt:
            print(f"[daily-board] Past stop time {args.stop_time} ET — "
                  f"exiting without connecting.")
            return
        print(f"[daily-board] Will auto-stop at {args.stop_time} ET.")

    ib = IB()
    print(f"[daily-board] Connecting to IBKR {args.host}:{args.port} "
          f"clientId={args.client}...")
    try:
        ib.connect(args.host, args.port, clientId=args.client,
                   timeout=CONNECT_TIMEOUT)
    except Exception as exc:  # noqa: BLE001
        print(f"[daily-board] Could not connect to IBKR: {exc}")
        print("              Make sure TWS / IB Gateway is running with the API "
              "enabled on that port.")
        return
    if not ib.isConnected():
        print("[daily-board] Failed to connect to IBKR.")
        return
    print("[daily-board] Connected. Qualifying + backfilling highs...")

    dm = DataManager(ib, columns, rows, os.path.basename(path), refresh=args.refresh)
    dm.out_path = args.out or _default_out_path(path)
    try:
        dm.prepare(max_symbols=args.max_symbols)
        curses.wrapper(run_board, dm, stop_time)
    except KeyboardInterrupt:
        pass
    finally:
        if ib.isConnected():
            ib.disconnect()
        print("[daily-board] Disconnected.")


if __name__ == '__main__':
    main()
