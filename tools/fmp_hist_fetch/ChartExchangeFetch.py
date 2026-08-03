#!/usr/bin/env python3
"""
ChartExchangeFetch.py — small-float NASDAQ enrichment over a date range, backed
by the ChartExchange v1 API (https://chartexchange.com/api/v1/docs/).

Given a candidate symbols list (e.g. a universe_finder ``stocks_universe_*.tsv``),
this keeps the names whose float is under a threshold (default 100M) and emits a
per-(symbol, date) TIME SERIES of the available small-cap metrics over a date
range: float / shares outstanding / free-float, short interest, short volume,
IBKR borrow fee, failures-to-deliver, and daily close with a derived market cap.

Why a symbols list and not the built-in screener
-------------------------------------------------
ChartExchange's ``/screener/stocks/`` endpoint (the "screen the whole market in
one call" path) requires a **Tier 3** subscription. The per-symbol reference and
data endpoints used here work on lower tiers, so we enrich a supplied candidate
list instead of crawling all ~10,774 NASDAQ listings.

Endpoints used (base https://chartexchange.com/api/v1/, auth = ``?api_key=``)
    data/stocks/shares/            date, shares_out, shares_float, free_float   (FLOAT source)
    data/stocks/short-interest/    date, short_interest, short_position, days_to_cover, change_percent
    data/stocks/short-volume/      mode=T -> date, st (short %% of total), rt (total vol)
    data/stocks/borrow-fee/ib/     timestamp, available, fee, rebate           (native timestamp_gte/lt)
    data/stocks/failure-to-deliver/ date, fails, price, ...
    data/stocks/bars/              agg_type=day, start=<unix> -> timestamp, close (historical price)
    feed/stocks/quote/             current name (for the Name column)

Symbols are addressed as ``US:<SYM>``; the input list holds bare tickers.

Output: a tab-separated table
    tables/ChartExchange_NASDAQ_floatLT<M>M_<from>_to_<to>.tsv
one row per (Symbol, Date) that appears in any selected series within the range.
Rows are written and flushed per symbol, so an interrupted or crashed run keeps
what it fetched; ``--resume`` restarts it, skipping the symbols already in the
file. A symbol whose data cannot be fetched is logged ``[warn]`` and skipped —
it never aborts the run.

The series are newest-first and have NO server-side date filtering (only
``borrow-fee/ib/`` does), so pagination stops as soon as a page runs older than
the window. That also dodges an API quirk: requesting a page past the end
answers HTTP 500 rather than an empty list, which bites any series whose length
is an exact multiple of page_size.

Run under the N2 venv:  /home/tom/venv/bin/python ChartExchangeFetch.py \
    --symbols-list stocks_universe_2026-07-12.tsv \
    --date-range 01-06-2026 30-06-2026
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- locate the shared N2 config loader (scripts/config/n2_config.py) ---------
_SCRIPTS_DIR = Path(__file__).resolve().parents[2]  # .../scripts
sys.path.insert(0, str(_SCRIPTS_DIR / "config"))
import n2_config  # noqa: E402

CE_BASE = "https://chartexchange.com/api/v1"
CONFIG_KEY = "CHARTEXCHANGE_API_KEY"

# Metric groups selectable via --metrics.
ALL_METRICS = ("shares", "si", "short_vol", "borrow", "ftd", "price")

# Output columns, in order. Symbol/Name/Date first, then metric groups.
OUT_COLS = [
    "Symbol", "Name", "Date",
    # shares
    "shares_out", "shares_float", "free_float",
    # short interest
    "short_interest", "short_position", "days_to_cover", "si_change_pct",
    # short volume
    "short_vol_pct", "short_vol_total",
    # borrow (IBKR)
    "borrow_fee", "borrow_rebate", "borrow_available",
    # failures to deliver
    "ftd_fails", "ftd_price",
    # price / derived market cap
    "close", "market_cap",
]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _flatten(text) -> str:
    """Collapse a value to a single TSV-safe cell."""
    return " ".join(str(text).replace("\t", " ").split())


def parse_cli_date(s: str) -> datetime:
    """Parse a ``DD-MM-YYYY`` date into a UTC-aware datetime at midnight."""
    try:
        d = datetime.strptime(s, "%d-%m-%Y")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid date {s!r}; expected DD-MM-YYYY (e.g. 01-01-2026)"
        )
    return d.replace(tzinfo=timezone.utc)


def parse_float_max(s: str) -> float:
    """Parse a float-shares threshold, accepting human suffixes.

    Accepts ``100M``, ``50m``, ``1.5B``, ``250K``, ``1e8`` or a plain share
    count like ``100000000``. A bare number below 100_000 is almost certainly a
    millions-typo (e.g. ``100`` meaning 100M) and is rejected with a hint.
    """
    raw = s.strip().lower().replace(",", "")
    mult = 1
    if raw and raw[-1] in "kmb":
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[raw[-1]]
        raw = raw[:-1]
    try:
        val = float(raw) * mult
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"invalid --float-max {s!r}; use e.g. 100M, 50000000, or 1e8"
        )
    if mult == 1 and val < 100_000:
        raise argparse.ArgumentTypeError(
            f"--float-max {s!r} is in SHARES ({val:g}); did you mean "
            f"'{s.strip()}M' (i.e. {val*1e6:.0f} shares)? Add a K/M/B suffix."
        )
    return val


def parse_metrics(s: str):
    """Parse the --metrics arg into an ordered tuple of valid group names."""
    if s.strip().lower() == "all":
        return ALL_METRICS
    chosen = [tok.strip().lower() for tok in s.split(",") if tok.strip()]
    bad = [c for c in chosen if c not in ALL_METRICS]
    if bad:
        raise argparse.ArgumentTypeError(
            f"unknown metric(s) {bad}; choose from {', '.join(ALL_METRICS)} or 'all'"
        )
    # de-dup, preserve ALL_METRICS ordering
    return tuple(m for m in ALL_METRICS if m in chosen)


def _date_only(raw: str) -> str:
    """Normalise a 'date' or ISO/space 'timestamp' string to YYYY-MM-DD."""
    if not raw:
        return ""
    return str(raw).strip().replace("T", " ").split(" ", 1)[0]


def load_symbols(path: Path) -> list:
    """Read column 1 (``Symbol``) of a TSV into an ordered, de-duped list.

    Skips a ``Symbol`` header, uppercases, drops obvious non-tickers.
    """
    seen = set()
    out = []
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for i, row in enumerate(reader):
            if not row:
                continue
            cell = row[0].strip().upper()
            if i == 0 and cell.lower() == "symbol":
                continue  # header
            if not cell or cell in seen:
                continue
            seen.add(cell)
            out.append(cell)
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class CEFetchError(RuntimeError):
    """A request that failed every retry. Caught per-series/per-symbol so one
    bad name cannot abort a whole run."""


class RateLimiter:
    """Simple min-interval gate; ``req_per_min<=0`` disables throttling."""

    def __init__(self, req_per_min: float):
        self.min_interval = 60.0 / req_per_min if req_per_min and req_per_min > 0 else 0.0
        self._last = 0.0

    def wait(self):
        if self.min_interval <= 0:
            return
        now = time.monotonic()
        gap = self.min_interval - (now - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.monotonic()


class CEClient:
    """Thin ChartExchange v1 client: rate-limited GET + client-side pagination."""

    def __init__(self, api_key: str, limiter: RateLimiter,
                 retries: int = 3, backoff: float = 1.5, timeout: int = 30):
        self.api_key = api_key
        self.limiter = limiter
        self.retries = retries
        self.backoff = backoff
        self.timeout = timeout
        self.calls = 0

    def get(self, path: str, params: dict):
        """GET ``{base}/{path}?...`` and return parsed JSON (list or dict).

        Retries on transient errors and on 429 / throttle bodies. Raises
        CEFetchError only on a hard, repeated failure.
        """
        q = dict(params)
        q["api_key"] = self.api_key
        url = f"{CE_BASE}/{path.lstrip('/')}?{urllib.parse.urlencode(q)}"
        last_err = None
        for attempt in range(1, self.retries + 1):
            self.limiter.wait()
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "N2-ChartExchangeFetch/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                self.calls += 1
                payload = json.loads(body)
                if isinstance(payload, dict) and payload.get("detail"):
                    # e.g. {"detail":"Tier 3 needed."} or {"detail":"Not found."}
                    raise RuntimeError(str(payload["detail"]))
                return payload
            except urllib.error.HTTPError as exc:
                last_err = exc
                # 429 = throttled; back off harder. 404 on a data path = no data.
                if exc.code == 404:
                    self.calls += 1
                    return []
                # An out-of-range page answers 500, not []: a series whose length
                # is an exact multiple of page_size looks "full" on its last page
                # (see get_series). Treat a 5xx past page 1 as end-of-data.
                if exc.code >= 500 and int(q.get("page", 1) or 1) > 1:
                    self.calls += 1
                    return []
                sleep = self.backoff * attempt * (3 if exc.code == 429 else 1)
                if attempt < self.retries:
                    time.sleep(sleep)
            except (urllib.error.URLError, RuntimeError, json.JSONDecodeError) as exc:
                last_err = exc
                if attempt < self.retries:
                    time.sleep(self.backoff * attempt)
        raise CEFetchError(f"request failed after {self.retries} attempts: "
                           f"{path} {params} :: {last_err}")

    def get_series(self, path: str, params: dict, page_size: int = 1000,
                   max_pages: int = 20, stop_before: str = "",
                   date_field: str = "date") -> list:
        """Fetch a paginated data series, unwrapping either a bare list or a
        DRF ``{count,next,results}`` envelope. Returns a flat list of records.

        ``stop_before`` (YYYY-MM-DD) short-circuits pagination on the
        newest-first series: once a page ENDS older than that date the range of
        interest is fully covered, so stop. The break happens *after* the page
        is kept, so the newest record on/before ``stop_before`` is always
        included — callers rely on that for as-of lookups and forward-fills.
        """
        rows = []
        page = 1
        while page <= max_pages:
            q = dict(params)
            q["page"] = page
            q["page_size"] = page_size
            payload = self.get(path, q)
            if isinstance(payload, dict):
                batch = payload.get("results", [])
                rows.extend(batch)
                if not payload.get("next") or not batch:
                    break
            elif isinstance(payload, list):
                batch = payload
                rows.extend(batch)
                if len(batch) < page_size:
                    break
            else:
                break
            if stop_before and batch:
                tail = _date_only(batch[-1].get(date_field, ""))
                if tail and tail < stop_before:
                    break
            page += 1
        return rows


# ---------------------------------------------------------------------------
# per-symbol fetch + assembly
# ---------------------------------------------------------------------------
def _in_range(dstr: str, lo: str, hi: str) -> bool:
    """Lexicographic YYYY-MM-DD comparison is date-correct."""
    return bool(dstr) and lo <= dstr <= hi


def fetch_shares(cli: CEClient, sym: str, lo: str = ""):
    """Shares/float series (newest first) back to ``lo``, as list of dicts.

    ``lo`` is the START of the window, not the end: ``float_as_of`` and the
    market_cap forward-fill both need the newest share count on/before a date
    that can predate the window.
    """
    return cli.get_series("data/stocks/shares/", {"symbol": sym, "ordering": "-date"},
                          stop_before=lo)


def float_as_of(shares_rows: list, hi: str):
    """Return shares_float as of ``hi`` (latest row on/before hi), else the
    newest available. Returns (float_value, source_date) or (None, None)."""
    on_or_before = [r for r in shares_rows if _date_only(r.get("date", "")) <= hi]
    pick = on_or_before[0] if on_or_before else (shares_rows[0] if shares_rows else None)
    if not pick:
        return None, None
    return pick.get("shares_float"), _date_only(pick.get("date", ""))


def build_symbol_rows(cli: CEClient, bare: str, sym: str, lo: str, hi: str,
                      metrics, shares_rows: list, ffill_shares_out: bool):
    """Assemble the (Symbol, Date) rows for one qualifying symbol.

    Outer-joins every selected series on date; ``shares_out`` is forward-filled
    (over descending dates) so market_cap is computable on trading days between
    share-count updates.
    """
    lo_unix = int(datetime.strptime(lo, "%Y-%m-%d")
                  .replace(tzinfo=timezone.utc).timestamp())
    days_span = (datetime.strptime(hi, "%Y-%m-%d")
                 - datetime.strptime(lo, "%Y-%m-%d")).days

    # Per-date accumulator: date -> {col: value}
    rows: dict = {}

    def cell(d, **kv):
        rows.setdefault(d, {})
        for k, v in kv.items():
            if v is not None and v != "":
                rows[d][k] = v

    def series(label, path, params, **kw):
        """One metric series; a hard failure blanks that series only."""
        try:
            return cli.get_series(path, params, **kw)
        except CEFetchError as exc:
            print(f"[warn] {bare}: {label} unavailable :: {exc}", file=sys.stderr)
            return []

    # --- shares / float (already fetched for the gate) ---
    shares_by_date = {}
    if "shares" in metrics or "price" in metrics:
        for r in shares_rows:
            d = _date_only(r.get("date", ""))
            shares_by_date[d] = r.get("shares_out")
            if "shares" in metrics and _in_range(d, lo, hi):
                cell(d, shares_out=r.get("shares_out"),
                     shares_float=r.get("shares_float"),
                     free_float=r.get("free_float"))

    # --- short interest ---
    if "si" in metrics:
        for r in series("short-interest", "data/stocks/short-interest/",
                        {"symbol": sym, "ordering": "-date"}, stop_before=lo):
            d = _date_only(r.get("date", ""))
            if _in_range(d, lo, hi):
                cell(d, short_interest=r.get("short_interest"),
                     short_position=r.get("short_position"),
                     days_to_cover=r.get("days_to_cover"),
                     si_change_pct=r.get("change_percent"))

    # --- short volume (mode=T -> st = short % of total; rt = total volume) ---
    if "short_vol" in metrics:
        for r in series("short-volume", "data/stocks/short-volume/",
                        {"symbol": sym, "mode": "T", "ordering": "-date"},
                        stop_before=lo):
            d = _date_only(r.get("date", ""))
            if _in_range(d, lo, hi):
                cell(d, short_vol_pct=r.get("st"), short_vol_total=r.get("rt"))

    # --- borrow fee (IBKR) — native date filter on timestamp ---
    if "borrow" in metrics:
        for r in series("borrow-fee", "data/stocks/borrow-fee/ib/",
                        {"symbol": sym, "ordering": "-timestamp",
                         "timestamp_gte": lo, "timestamp_lt": _next_day(hi)},
                        stop_before=lo, date_field="timestamp"):
            d = _date_only(r.get("timestamp", ""))
            if _in_range(d, lo, hi):
                cell(d, borrow_fee=r.get("fee"), borrow_rebate=r.get("rebate"),
                     borrow_available=r.get("available"))

    # --- failures to deliver ---
    if "ftd" in metrics:
        for r in series("failure-to-deliver", "data/stocks/failure-to-deliver/",
                        {"symbol": sym, "ordering": "-date"}, stop_before=lo):
            d = _date_only(r.get("date", ""))
            if _in_range(d, lo, hi):
                cell(d, ftd_fails=r.get("fails"), ftd_price=r.get("price"))

    # --- daily bars -> close, and derived market cap ---
    if "price" in metrics:
        limit = min(500, max(1, days_span + 5))
        bars = series("bars", "data/stocks/bars/",
                      {"symbol": sym, "agg_type": "day",
                       "start": lo_unix, "limit": limit},
                      page_size=500, max_pages=2)
        for r in bars:
            ts = r.get("timestamp")
            try:
                d = datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OverflowError):
                continue
            if _in_range(d, lo, hi):
                cell(d, close=r.get("close"))

    # --- forward-fill shares_out (descending dates) for market cap ---
    if "price" in metrics and rows:
        so_dates = sorted(shares_by_date.keys())  # ascending

        def shares_out_asof(d):
            # newest share count on/before d
            best = None
            for sd in so_dates:
                if sd <= d and shares_by_date[sd] is not None:
                    best = shares_by_date[sd]
                elif sd > d:
                    break
            return best

        for d, rec in rows.items():
            close = rec.get("close")
            if close is None:
                continue
            so = rec.get("shares_out")
            if so is None and ffill_shares_out:
                so = shares_out_asof(d)
            try:
                if so is not None:
                    rec["market_cap"] = round(float(close) * float(so))
            except (TypeError, ValueError):
                pass

    # --- materialise rows ---
    out = []
    for d in sorted(rows.keys()):
        rec = rows[d]
        rec_out = {"Symbol": bare, "Date": d}
        for k, v in rec.items():
            rec_out[k] = v
        out.append(rec_out)
    return out


def _next_day(dstr: str) -> str:
    return (datetime.strptime(dstr, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")


def fetch_name(cli: CEClient, sym: str) -> str:
    """Best-effort company name from the quote feed."""
    try:
        payload = cli.get("feed/stocks/quote/", {"symbol": sym})
    except CEFetchError:
        return ""
    if isinstance(payload, list) and payload:
        return _flatten(payload[0].get("name", ""))
    if isinstance(payload, dict):
        return _flatten(payload.get("name", ""))
    return ""


def read_done_symbols(out_path: Path) -> set:
    """Symbols already present in a partially-written output TSV (for --resume).

    The last symbol of an interrupted run is complete: rows are written per
    symbol and flushed, so there is no half-symbol to redo.
    """
    if not out_path.exists():
        return set()
    done = set()
    with out_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        for row in reader:
            sym = (row.get("Symbol") or "").strip()
            if sym:
                done.add(sym)
    return done


def sort_output_file(out_path: Path) -> int:
    """Re-sort the finished TSV by (Symbol, Date) in place; returns row count.

    Rows are appended in fetch order while the run streams to disk; this makes
    the final file match the old sorted layout.
    """
    if not out_path.exists():
        return 0
    with out_path.open(encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        rows = list(reader)
    rows.sort(key=lambda r: (r.get("Symbol", ""), r.get("Date", "")))
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=OUT_COLS, delimiter="\t",
                                lineterminator="\n", quoting=csv.QUOTE_MINIMAL,
                                extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return len(rows)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def run(args):
    cfg = n2_config.load_config(args.config)
    api_key = args.api_key or n2_config.get(cfg, CONFIG_KEY)
    if not api_key:
        raise SystemExit(
            f"[fatal] no {CONFIG_KEY} in config file "
            f"({args.config or n2_config.default_config_path()}) and no --api-key. "
            f"Add a line '{CONFIG_KEY}:' followed by your key on the next line."
        )

    sym_path = Path(args.symbols_list)
    if not sym_path.exists():
        raise SystemExit(f"[fatal] symbols list not found: {sym_path}")
    bare_symbols = load_symbols(sym_path)
    if not bare_symbols:
        raise SystemExit(f"[fatal] no symbols read from {sym_path}")
    if args.limit_symbols:
        bare_symbols = bare_symbols[:args.limit_symbols]

    metrics = args.metrics
    start, end = args.date_range
    lo = start.strftime("%Y-%m-%d")
    hi = end.strftime("%Y-%m-%d")
    float_max = args.float_max
    float_max_m = float_max / 1_000_000

    limiter = RateLimiter(args.rate)
    cli = CEClient(api_key, limiter)

    print(f"[info] symbols: {len(bare_symbols)} | range: {lo} .. {hi} (inclusive) | "
          f"float<{float_max_m:g}M | metrics: {','.join(metrics)} | rate: {args.rate}/min",
          file=sys.stderr)

    # --- output file: written as we go, so an abort keeps the work ----------
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = (f"ChartExchange_NASDAQ_floatLT{float_max_m:g}M_"
             f"{start.strftime('%d-%m-%Y')}_to_{end.strftime('%d-%m-%Y')}.tsv")
    out_path = out_dir / fname

    done_symbols = set()
    if args.resume:
        done_symbols = read_done_symbols(out_path)
        if done_symbols:
            print(f"[info] resume: {len(done_symbols)} symbols already in {out_path.name}",
                  file=sys.stderr)
        else:
            print(f"[info] resume: nothing to resume from ({out_path.name})",
                  file=sys.stderr)

    append = args.resume and bool(done_symbols)
    written = 0
    kept = 0
    dropped = 0
    no_float = 0
    skipped_done = 0
    failed = []

    fh = out_path.open("a" if append else "w", encoding="utf-8", newline="")
    writer = csv.DictWriter(fh, fieldnames=OUT_COLS, delimiter="\t",
                            lineterminator="\n", quoting=csv.QUOTE_MINIMAL,
                            extrasaction="ignore")
    try:
        if not append:
            writer.writeheader()
        for i, bare in enumerate(bare_symbols, 1):
            if bare in done_symbols:
                skipped_done += 1
                continue
            sym = f"US:{bare}"
            try:
                shares_rows = fetch_shares(cli, sym, lo)
                fval, fdate = float_as_of(shares_rows, hi)
                if fval is None:
                    no_float += 1
                    print(f"[skip] {i}/{len(bare_symbols)} {bare}: no float data",
                          file=sys.stderr)
                    continue
                try:
                    fnum = float(fval)
                except (TypeError, ValueError):
                    no_float += 1
                    continue
                if fnum >= float_max:
                    dropped += 1
                    print(f"[skip] {i}/{len(bare_symbols)} {bare}: float {fnum/1e6:.1f}M "
                          f"(as of {fdate}) >= {float_max_m:g}M", file=sys.stderr)
                    continue

                name = fetch_name(cli, sym) if not args.no_names else ""
                rows = build_symbol_rows(cli, bare, sym, lo, hi, metrics, shares_rows,
                                         ffill_shares_out=not args.no_ffill)
            except CEFetchError as exc:
                failed.append(bare)
                print(f"[warn] {i}/{len(bare_symbols)} {bare}: skipped :: {exc}",
                      file=sys.stderr)
                continue

            kept += 1
            for r in rows:
                r["Name"] = name
                writer.writerow({c: _flatten(r[c]) if c in r and r[c] is not None else ""
                                 for c in OUT_COLS})
            written += len(rows)
            fh.flush()
            print(f"[fetch] {i}/{len(bare_symbols)} {bare}: float {fnum/1e6:.1f}M "
                  f"(as of {fdate}) -> {len(rows)} rows", file=sys.stderr)
    except KeyboardInterrupt:
        print("\n[warn] interrupted — keeping what was written so far",
              file=sys.stderr)
    finally:
        fh.close()

    total = sort_output_file(out_path)

    print(f"\n[summary] scanned: {len(bare_symbols)} | kept (float<{float_max_m:g}M): "
          f"{kept} | dropped (too big): {dropped} | no float data: {no_float}",
          file=sys.stderr)
    if skipped_done:
        print(f"[summary] skipped (already in file): {skipped_done}", file=sys.stderr)
    if failed:
        print(f"[summary] failed symbols ({len(failed)}): {','.join(failed)}",
              file=sys.stderr)
    print(f"[summary] rows this run: {written} | API calls: {cli.calls}", file=sys.stderr)
    print(f"[done] {total} rows -> {out_path}", file=sys.stderr)


def build_parser():
    p = argparse.ArgumentParser(
        description="Enrich a NASDAQ symbols list with ChartExchange small-cap "
                    "metrics (float/SI/borrow/FTD/short-vol/price) over a date "
                    "range, keeping only float<threshold names.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--symbols-list", required=True,
                   help="TSV whose first column is Symbol (e.g. a stocks_universe_*.tsv)")
    p.add_argument("--date-range", nargs=2, metavar=("FROM", "TO"), required=True,
                   type=parse_cli_date, help="inclusive date range as DD-MM-YYYY DD-MM-YYYY")
    p.add_argument("--float-max", type=parse_float_max, default=100_000_000,
                   help="keep symbols with shares_float strictly below this; "
                        "accepts a share count or a K/M/B suffix, e.g. 100M")
    p.add_argument("--metrics", type=parse_metrics, default="all",
                   help="comma list from: " + ",".join(ALL_METRICS) + " (or 'all')")
    p.add_argument("--rate", type=float, default=60,
                   help="max API requests per minute (self-throttle; 0 = unlimited)")
    p.add_argument("--limit-symbols", type=int, default=0,
                   help="only process the first N symbols (0 = all; for smoke tests)")
    p.add_argument("--resume", action="store_true",
                   help="append to an existing output TSV, skipping the symbols "
                        "it already contains (restart an interrupted run)")
    p.add_argument("--no-names", action="store_true",
                   help="skip the quote call that fetches the company Name")
    p.add_argument("--no-ffill", action="store_true",
                   help="do not forward-fill shares_out when deriving market_cap")
    p.add_argument("--api-key", default=None,
                   help="override the CHARTEXCHANGE_API_KEY from config")
    p.add_argument("--config", default=None,
                   help="path to n2 config file (default: config/n2_config_file.txt)")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "tables"),
                   help="output directory for the TSV")
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())
