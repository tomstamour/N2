# Blacklist User Guide — Orchestrator3

## Purpose

The blacklist prevents the same stock from triggering multiple pipeline runs in quick succession. When an article is **accepted** by the filter pipeline, every ticker it mentions is immediately blacklisted. Any future article mentioning that same ticker is blocked at the filter stage until the blacklist entry expires.

---

## Configuration (Orchestrator3.py)

| Constant | Current value | Meaning |
|---|---|---|
| `NW3_BLACK_LIST` | `orchestrator3/black_list.csv` | Path to the persisted CSV file |
| `NW3_BLACKLIST_EXPIRY_HOURS` | `4` | Hours after which an entry is expired and the ticker can be accepted again |
| `NW3_FLUSH_INTERVAL_SEC` | `3600` | How often (seconds) the flush runs — also the max lag before a mid-session expiry takes effect |

---

## The Blacklist CSV

**Location:** `path/to/N2/scripts/orchestrator3/black_list.csv`

**Format:**
```
Symbol,Date,ID
AAPL,01-05-2026 10:32,nPn21RWsza
TSLA,01-05-2026 09:15,nGNE8zdMpP
```

| Column | Description |
|---|---|
| `Symbol` | Ticker symbol (e.g. `AAPL`) |
| `Date` | Timestamp when the ticker was blacklisted — format `DD-MM-YYYY HH:MM` |
| `ID` | ID of the news article that triggered the blacklisting |

> **Legacy entries** (date-only format `DD-MM-YYYY`, written before the hours-based system) are treated as `00:00` of that day when computing age.

---

## How a Stock Gets Blacklisted

A ticker is blacklisted **automatically** the moment an article passes all filters and is accepted. This happens synchronously in the article handler before the callback fires:

```
Article arrives → passes _passes_filters() → accepted
  → date_str = arrival.strftime('%d-%m-%Y %H:%M')
  → _blacklist_set.add(ticker)           # immediate O(1) block
  → _blacklist_records.append(...)       # persisted on next flush
```

Both the in-memory set (`_blacklist_set`) and the persisted records (`_blacklist_records`) are updated under the same lock, so the block is effective for all subsequent articles instantly — no flush delay.

There is **no manual trigger** needed. Blacklisting is fully automatic on acceptance.

---

## Where the Blacklist Is Checked

Every incoming article goes through `_passes_filters()`. The blacklist check is the **third gate** in the pipeline:

```
1. Ticker list not empty, count ≤ 2
2. At least one ticker in the universe
3. ← BLACKLIST CHECK: any ticker in _blacklist_set → blocked
4. Headline does not contain an excluded string
5. Float_M ≤ NW3_REJECT_FLOAT_GT (50M)
6. LastDailyClosePrice ≤ NW3_REJECT_PRICE_GT ($2.00)
```

If any ticker in the article matches a blacklisted symbol the entire article is blocked, even if the other ticker is clean.

---

## Expiry Mechanism

Entries expire when their age exceeds `NW3_BLACKLIST_EXPIRY_HOURS`. There are **two moments** when expiry is enforced:

### 1. At startup (script launch)

`_load_blacklist()` reads the CSV, computes the age of every entry, discards entries where `age_hours >= expiry_hours`, and loads the survivors into memory. Entries that are already past their expiry when the script starts are never loaded.

### 2. Mid-session (every flush)

`_purge_blacklist_in_memory()` runs at the **start of every flush** cycle. It iterates the in-memory records, drops expired entries, and updates both `_blacklist_set` and `_blacklist_records`. The cleaned list is then written to the CSV.

**Timing accuracy:** a ticker is removed from the active filter within **one flush interval** of its expiry. With the default `NW3_FLUSH_INTERVAL_SEC = 3600` and `NW3_BLACKLIST_EXPIRY_HOURS = 4`, a stock blacklisted at 10:00 will be unblocked at the 14:xx flush — at most 1 hour after the 4-hour window closes.

---

## Complete Lifecycle of a Blacklist Entry

```
10:00  Article for AAPL accepted
         → AAPL added to _blacklist_set (immediate)
         → record {'Symbol':'AAPL','Date':'01-05-2026 10:00','ID':'...'} stored in memory

11:00  Flush 1 — _purge_blacklist_in_memory() runs
         age = 1h < 4h → AAPL survives
         CSV written with AAPL entry

12:00  Flush 2 — age = 2h < 4h → AAPL survives

13:00  Flush 3 — age = 3h < 4h → AAPL survives

14:00  Flush 4 — age = 4h >= 4h → AAPL PURGED
         _blacklist_set and _blacklist_records updated
         CSV written without AAPL
         AAPL can now be accepted again
```

If the script restarts at any point, the CSV is re-read and the same age check is applied at load time — no entry survives a restart if it was already past its expiry.

---

## Manual Blacklisting

To manually blacklist a ticker, add a row to the CSV while the script is **not running** (the file is overwritten on each flush):

```
MYTKR,01-05-2026 09:00,manual
```

Use `manual` or any placeholder string for the `ID` column. The timestamp determines when the entry expires relative to `NW3_BLACKLIST_EXPIRY_HOURS`.

To permanently blacklist a ticker (never expire), there is no built-in mechanism — the simplest workaround is to add it with a timestamp far in the past so it re-expires on every startup and reload. This is not reliable; a dedicated persistent exclusion list is a better fit (see `NW3_EXCLUDED_STRINGS_FILE` for headline-level exclusions, or consider extending the universe filter upstream).

---

## Log Messages to Watch

| Log message | What it means |
|---|---|
| `Blacklist loaded: N active entries, M purged (expiry=Xh)` | Startup load result |
| `Mid-session blacklist purge: M expired entries removed (expiry=Xh, N remaining)` | Flush-time purge removed at least one entry |
| `Blocked id=…: ticker 'SYM' is blacklisted` | An article was dropped because the ticker is still active in the blacklist |
| `Purging blacklist entry: SYM (date, X.Xh old)` | DEBUG-level — individual entry purged (startup or mid-session) |
| `Active blacklist: ['SYM', ...]` | DEBUG-level — lists surviving entries after startup load |
