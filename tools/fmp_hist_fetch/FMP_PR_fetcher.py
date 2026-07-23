#!/usr/bin/env python3
"""
FMP_PR_fetcher.py — historical press-release fetcher backed by Financial
Modeling Prep (FMP), used to build a research corpus of the same wire-service
press releases that RTPR.io delivers live to the N2 pipeline.

FMP endpoint (stable):
    https://financialmodelingprep.com/stable/news/press-releases-latest
        ?page=<n>&limit=<n>&apikey=<key>
This is a newest-first paginated feed. The FMP docs advertise coverage of the
major wires (Business Wire, PR Newswire, GlobeNewswire, ACCESS Newswire,
NewsFile) — the same set RTPR carries — with the wire reported per-article. This
tool's first job is to *confirm* that: every run prints the distinct source
names it saw so you can eyeball the coverage.

Behaviour
---------
* ``--date-range DD-MM-YYYY DD-MM-YYYY`` — inclusive day range (UTC).
  The script first probes whether the endpoint honours native ``from``/``to``
  query params for this plan; if not, it paginates newest→oldest and filters
  client-side, stopping once a full page predates the range start.
* ``--symbols-list PATH`` — a TSV whose first column is ``Symbol`` (e.g. a
  universe_finder ``stocks_universe_*.tsv``). Only PRs whose symbol is in that
  set are kept.
* All sources are kept and labelled — no wire pre-filtering.

Output: a tab-separated table ``tables/FMP_PR_<from>_to_<to>.tsv`` with columns
    Symbol · Source · Created · Headline · Body
where Body is the raw article text flattened to a single line (newlines → spaces)
to match the one-row-per-PR shape of RTPR's ``PR_dataframe_*`` tables.

Run under the N2 venv:  /home/tom/venv/bin/python FMP_PR_fetcher.py ...
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
from datetime import datetime, timezone
from pathlib import Path

# --- locate the shared N2 config loader (scripts/config/n2_config.py) ---------
_SCRIPTS_DIR = Path(__file__).resolve().parents[2]  # .../scripts
sys.path.insert(0, str(_SCRIPTS_DIR / "config"))
import n2_config  # noqa: E402

FMP_BASE = "https://financialmodelingprep.com/stable/news/press-releases-latest"
CONFIG_KEY = "FMP_API_KEY"

# Candidate field names in an FMP press-release record. FMP has shipped a few
# shapes over time; we probe each candidate in order and use the first present.
SYMBOL_FIELDS = ("symbol", "ticker", "tickers")
SOURCE_FIELDS = ("publisher", "site", "source", "author")
CREATED_FIELDS = ("publishedDate", "date", "created", "publishedAt")
HEADLINE_FIELDS = ("title", "headline")
BODY_FIELDS = ("text", "content", "body")
URL_FIELDS = ("url", "link")

# The five wires RTPR advertises — used only for the coverage summary print.
RTPR_MAIN_WIRES = (
    "business wire",
    "pr newswire",
    "globenewswire",
    "access newswire",
    "accesswire",
    "newsfile",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _first(record: dict, candidates) -> str:
    """Return the first non-empty candidate field from ``record`` as a string."""
    for key in candidates:
        if key in record and record[key] not in (None, ""):
            return str(record[key])
    return ""


def _flatten(text: str) -> str:
    """Collapse a body/headline to a single TSV-safe line."""
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


def parse_created(raw: str):
    """Best-effort parse of an FMP timestamp into a UTC-aware datetime.

    FMP has used ``YYYY-MM-DD HH:MM:SS`` and ISO-8601 (``...T...Z``) forms.
    Returns ``None`` if unparseable.
    """
    if not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    fmts = (None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S")
    for fmt in fmts:
        try:
            dt = datetime.fromisoformat(s) if fmt is None else datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def load_symbols(path: Path) -> set:
    """Read column 1 (``Symbol``) of a TSV into an uppercase set, skipping header."""
    symbols = set()
    with path.open(encoding="utf-8", newline="") as fh:
        reader = csv.reader(fh, delimiter="\t")
        for i, row in enumerate(reader):
            if not row:
                continue
            cell = row[0].strip()
            if i == 0 and cell.lower() == "symbol":
                continue  # header
            if cell:
                symbols.add(cell.upper())
    return symbols


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
def fetch_page(api_key: str, page: int, limit: int, date_from=None, date_to=None,
               retries: int = 3, backoff: float = 1.5):
    """GET one page of the FMP feed; return the parsed JSON list (or [])."""
    params = {"page": page, "limit": limit, "apikey": api_key}
    if date_from:
        params["from"] = date_from
    if date_to:
        params["to"] = date_to
    url = f"{FMP_BASE}?{urllib.parse.urlencode(params)}"
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "N2-FMP_PR_fetcher/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("Error Message"):
                raise RuntimeError(payload["Error Message"])
            if not isinstance(payload, list):
                raise RuntimeError(f"unexpected response type {type(payload).__name__}: {payload!r:.200}")
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt < retries:
                time.sleep(backoff * attempt)
    raise SystemExit(f"[fatal] FMP request failed after {retries} attempts (page {page}): {last_err}")


# ---------------------------------------------------------------------------
# main fetch loop
# ---------------------------------------------------------------------------
def run(args):
    cfg = n2_config.load_config(args.config)
    api_key = n2_config.get(cfg, CONFIG_KEY)
    if not api_key:
        raise SystemExit(
            f"[fatal] no {CONFIG_KEY} in config file "
            f"({args.config or n2_config.default_config_path()}). "
            f"Add a line '{CONFIG_KEY}:' followed by your key on the next line."
        )

    sym_path = Path(args.symbols_list)
    if not sym_path.exists():
        raise SystemExit(f"[fatal] symbols list not found: {sym_path}")
    symbols = load_symbols(sym_path)
    if not symbols:
        raise SystemExit(f"[fatal] no symbols read from {sym_path}")

    start, end = args.date_range
    # inclusive: keep anything strictly before the day AFTER `end`
    range_lo = start
    range_hi = end.replace(hour=23, minute=59, second=59)
    from_str, to_str = start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")
    print(f"[info] symbols: {len(symbols)} | range: {from_str} .. {to_str} (UTC, inclusive)", file=sys.stderr)

    # --- probe: does this plan honour native from/to? -----------------------
    print("[info] probing endpoint (native from/to)…", file=sys.stderr)
    probe = fetch_page(api_key, page=0, limit=args.limit, date_from=from_str, date_to=to_str)
    calls = 1
    native_range = False
    if probe:
        keys = list(probe[0].keys())
        print(f"[probe] sample record keys: {keys}", file=sys.stderr)
        print(f"[probe] sample record: {json.dumps(probe[0])[:600]}", file=sys.stderr)
        probe_dates = [parse_created(_first(r, CREATED_FIELDS)) for r in probe]
        in_range = [d for d in probe_dates if d and range_lo <= d <= range_hi]
        # If (nearly) every probe row already falls in range, treat from/to as honoured.
        native_range = len(in_range) >= max(1, int(0.8 * len([d for d in probe_dates if d])))
        print(f"[probe] {len(in_range)}/{len(probe)} probe rows in range → "
              f"native from/to {'HONOURED' if native_range else 'IGNORED (paginating)'}", file=sys.stderr)
    else:
        print("[probe] empty probe response; falling back to pagination", file=sys.stderr)

    # --- fetch loop ---------------------------------------------------------
    seen_ids = set()
    kept = []            # rows we keep
    sources_all = {}     # source -> count (every in-range PR, pre symbol-filter)
    page = 0
    stop = False
    while page < args.max_pages and not stop:
        records = probe if (page == 0) else fetch_page(
            api_key, page=page, limit=args.limit,
            date_from=(from_str if native_range else None),
            date_to=(to_str if native_range else None),
        )
        if page != 0:
            calls += 1
        if not records:
            break

        page_all_older = True
        for rec in records:
            created = parse_created(_first(rec, CREATED_FIELDS))
            if created is None:
                page_all_older = False  # unknown date: don't let it trigger early stop
                continue
            if created > range_hi:
                page_all_older = False  # newer than range; keep paginating down
                continue
            if created < range_lo:
                continue  # older than range → contributes to early-stop test
            page_all_older = False  # this row is in range

            source = _first(rec, SOURCE_FIELDS)
            sources_all[source] = sources_all.get(source, 0) + 1

            symbol = _first(rec, SYMBOL_FIELDS).upper()
            if symbol not in symbols:
                continue

            uid = _first(rec, URL_FIELDS) or f"{symbol}|{created.isoformat()}|{_first(rec, HEADLINE_FIELDS)}"
            if uid in seen_ids:
                continue
            seen_ids.add(uid)

            kept.append({
                "Symbol": symbol,
                "Source": source,
                "Created": created.isoformat(),
                "Headline": _flatten(_first(rec, HEADLINE_FIELDS)),
                "Body": _flatten(_first(rec, BODY_FIELDS)),
            })

        # In pagination mode, once a whole page is older than the range start we
        # can stop (feed is newest-first). Native-range mode stops on empty page.
        if not native_range and page_all_older:
            stop = True
        page += 1

    # --- coverage summary ---------------------------------------------------
    print(f"\n[summary] API calls: {calls} | pages scanned: {page} | kept rows: {len(kept)}", file=sys.stderr)
    print("[summary] sources seen in range (all symbols, pre-filter):", file=sys.stderr)
    for src, n in sorted(sources_all.items(), key=lambda kv: -kv[1]):
        flag = "  <- RTPR main wire" if src.lower() in RTPR_MAIN_WIRES else ""
        print(f"    {n:6d}  {src or '(blank)'}{flag}", file=sys.stderr)
    covered = {w for src in sources_all for w in RTPR_MAIN_WIRES if src.lower() == w}
    print(f"[summary] RTPR main wires present: {sorted(covered) or 'NONE'}", file=sys.stderr)

    # --- write output -------------------------------------------------------
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"FMP_PR_{start.strftime('%d-%m-%Y')}_to_{end.strftime('%d-%m-%Y')}.tsv"
    cols = ["Symbol", "Source", "Created", "Headline", "Body"]
    kept.sort(key=lambda r: r["Created"])
    with out_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, delimiter="\t", lineterminator="\n",
                                quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        writer.writerows(kept)
    print(f"[done] wrote {len(kept)} rows → {out_path}", file=sys.stderr)


def build_parser():
    p = argparse.ArgumentParser(
        description="Fetch FMP press releases over a date range, filtered to a symbol universe.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--date-range", nargs=2, metavar=("FROM", "TO"), required=True,
                   type=parse_cli_date, help="inclusive date range as DD-MM-YYYY DD-MM-YYYY")
    p.add_argument("--symbols-list", required=True,
                   help="TSV whose first column is Symbol (e.g. a stocks_universe_*.tsv)")
    p.add_argument("--config", default=None,
                   help="path to n2 config file (default: config/n2_config_file.txt)")
    p.add_argument("--limit", type=int, default=100, help="FMP page size")
    p.add_argument("--max-pages", type=int, default=200, help="safety cap on pages fetched")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent / "tables"),
                   help="output directory for the TSV")
    return p


if __name__ == "__main__":
    run(build_parser().parse_args())
