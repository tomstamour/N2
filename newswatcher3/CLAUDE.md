# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Running and Testing

```bash
# Run the integration test (requires a valid RTPR.io API key)
python3 test_main.py
```

**Importing from another script:**

```python
import NewsWatcher3 as nw
```

**Minimum start call (all defaults assume CWD == this directory):**

```python
nw.start()
```

**Full usage cycle:**

```python
nw.start(
    universe_tsv="./nasdaq_symbols_data.tsv",
    black_list="./black_list.csv",
    blacklist_expiry_days=7,
    api_keys="./RTPR_API-Key.txt",
    blocked_dir="./blocked_PRs",
    accepted_dir="./accepted_PRs",
    excluded_strings_file="./excluded_strings.txt",
)

df  = nw.get_news_df()                      # accepted DataFrame: ID, ArrivalTime, Symbol, Headline
obj = nw.get_news_object("id-51772090")     # full accepted article dict, or None if pruned
raw = nw.get_blocked_object("id-51772090")  # blocked article dict, or None if pruned/absent

nw.update_universe(["AAPL", "MSFT"])        # live swap, no restart needed

nw.stop()                                   # graceful shutdown, flushes to disk
```

**Event-driven callback (fires only on ACCEPTED articles):**

```python
def my_handler(news_dict: dict) -> None:
    # keys: Symbol (comma-joined tickers), ID, ArrivalTime, Headline
    print(news_dict)

# Register BEFORE start() to avoid a race window
nw.register_callback(my_handler)
nw.start(...)
nw.register_callback(None)   # deregister
```

- Called from the background thread — must be thread-safe
- Exceptions are caught and logged; they cannot crash NW3
- Only one callback is supported; calling again replaces the previous one
- Callback does NOT fire on blocked articles (use `get_blocked_object` to access those)

**Dependencies:** `pandas`, `websockets` (no Alpaca SDK — NW3 uses raw `wss://`).

**Credentials:** `RTPR_API-Key.txt` must contain a single line after `Key:`:
```
Key:
rtpr_XXXXXXXXXXXXXXXXXXXXXXXX
```

## Architecture

`NewsWatcher3.py` is a self-contained press-release / news ingestion module. It bridges an async RTPR.io WebSocket stream into a synchronous, thread-safe API for use by orchestrators.

**Threading model (mirrors NewsWatcher2):**
- Caller's main thread uses the synchronous public API: `start()`, `stop()`, `get_news_df()`, `get_news_object()`, `get_blocked_object()`, `update_universe()`, `register_callback()`
- A background daemon thread runs a dedicated asyncio event loop (RTPR is async-only)
- Shutdown is coordinated via a `threading.Event` polled by an async coroutine — the sync/async bridge
- Six `threading.Lock` objects: `_df_lock`, `_objects_lock` (accepted objects + `_seen_ids`), `_blocked_lock`, `_blacklist_lock`, `_universe_lock`, `_callback_lock`

**WebSocket protocol (RTPR.io):**
- Endpoint: `wss://ws.rtpr.io?apiKey=<KEY>` (auth via query param)
- Subscription: client sends `{"action": "subscribe", "tickers": ["*"]}` after the server's `connected` greeting — full firehose
- Heartbeat: server sends `{"type": "ping"}` every ~30s, client must reply `{"type": "pong"}` within 90s or be disconnected
- Article payload: `{"type": "article", "data": {id, ticker, tickers, title, author, created, article_body, ...}}`
- Reconnect: 10s backoff on disconnect; subscription is resent on reconnect

**Two-tier in-memory storage (the core design difference vs NW2):**
```
RTPR firehose
  → _handle_article()
  → silent dedup (_seen_ids — skip if already-seen id, NOT a filter rejection)
  → _passes_filters() — 4-step pipeline (≤2 tickers, ≥1 in universe, none blacklisted, headline exclusion)
  → On FAIL: store in _blocked_objects                ← rejected articles kept here
  → On PASS: store in _news_objects, _news_df; auto-blacklist all tickers; fire callback
  → Periodic flush (every 5 min):
      • blacklist CSV (atomic)
      • one JSON per article → blocked_PRs/   (articles that did not pass filters)
      • one JSON per article → accepted_PRs/  (articles that passed all filters)
      • per-symbol JSON → outputs/SYMBOL,SYMBOL2-YYYY-MM-DD.json (NW2 parity)
      • daily NewsDF TSV → outputs/NewsDF-YYYY-MM-DD.tsv
      • prune both _blocked_objects and _news_objects; clear _seen_ids
  → Final flush on stop()
```

**Filter pipeline (only 3 steps — NW2 dropped its dedup, author, and single-symbol filters):**
1. `len(tickers) <= 2` (reject if >2)
2. At least one ticker is in the universe (loaded from `nasdaq_symbols_data.tsv`)
3. No ticker is in the blacklist (loaded from `black_list.csv`)
4. No `excluded_strings` substring in `title` (case-insensitive, loaded from `excluded_strings.txt`)

Silent dedup by `id` runs BEFORE the filter — it is not a filter, just a guard against re-storing a duplicate the upstream sent twice. RTPR docs note providers occasionally re-emit the same article.

**Auto-blacklist on accept:** when an article passes filters, **every** ticker in `tickers[]` is appended to the blacklist (one row per ticker, sharing the same article id). Both tickers of a 2-ticker article become ineligible until the entry expires.

**Symbol value:** the DataFrame `Symbol` column and per-symbol JSON filename use a comma-joined string of all `tickers[]` entries (e.g. `AAPL,MSFT`). Downstream consumers must parse if they need individual tickers.

**Atomic file writes:** all disk writes use temp file + `os.replace()` to prevent partial-write corruption.

## Output Files

| File | Location | Format | Contents |
|------|----------|--------|----------|
| News DataFrame | `news_df_dir/NewsDF-YYYY-MM-DD.tsv` | TSV: ID, ArrivalTime, Symbol, Headline | Accepted articles only |
| Per-symbol news (NW2 parity) | `output_dir/<comma-joined-symbols>-YYYY-MM-DD.json` | JSON | Accepted articles, one file per symbol-string per day |
| Blocked articles | `blocked_PRs/<id>-<ticker>-YYYY-MM-DD.json` | JSON | Articles that did not pass filters, one file per article |
| Accepted articles | `accepted_PRs/<id>-<ticker>-YYYY-MM-DD.json` | JSON | Articles passing all filters, one file per article |
| Blacklist | `black_list.csv` | CSV: Symbol, Date (DD-MM-YYYY), ID | Auto-extended on each accept |
| Log | `log_dir/NewsWatcher3_YYYY-MM-DD.log` | Standard Python logging | DEBUG to file, INFO to stdout |

## Integration Context

NW3 is the news ingestion layer for the **N2 pipeline** (the N1 pipeline used Alpaca/NewsWatcher2). The `blocked_PRs/` directory exists so downstream tooling can replay/audit all rejected traffic, while the existing N1-style outputs (`outputs/`, `NewsDF-YYYY-MM-DD.tsv`) remain compatible with consumers built against NW2.

The universe is loaded from `nasdaq_symbols_data.tsv` (Symbol column, ~6,966 rows) at start. `update_universe(new_list)` performs an O(1) hot-swap without restarting the WebSocket.
