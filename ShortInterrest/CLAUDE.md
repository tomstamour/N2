# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `../CLAUDE.md` first for the cross-cutting N2 rules (venv interpreter, git policy).

## Purpose

Two scripts append the **same 24 short-interest / short-squeeze columns** to a
universe TSV, assembled from free web sources — **no IBKR API connection**:

| Script | Anchors on | Output |
|---|---|---|
| `ShortInterrest_AddON.py` | `eastern_today()` — describes *now* | `<stem>_SI.tsv` |
| `ShortInterrest_backfill_AddON.py` | a reconstructed **run date** — point-in-time | `<stem>_SIback.tsv` |

The live addon is **Step 8 of `../universe_finder/daily_universe_pipeline-2.py`**
(cron 20:30 ET), loaded by path and called as `enrich_dataframe(df)`; it turns
the 14-column universe file into the 38-column one. Step 8 is best-effort — a
failure leaves the 14-column file intact and the addon is re-runnable standalone.
The backfill exists to enrich universe files written before Step 8 existed.

## Commands

```bash
P=/home/tom/venv/bin/python   # never system python3

# live enrichment (writes a _SI.tsv sibling; input never modified)
$P ShortInterrest_AddON.py -i stocks_universe_2026-07-02_head20.tsv
$P ShortInterrest_AddON.py -i ../universe_finder/data/stocks_universe_2026-08-11.tsv \
     --skip-borrow-history            # drops the ~0.8 s/symbol iBorrowDesk calls

# historical backfill (date from the filename; globs + multiple -i OK)
# DEFAULT IS --days-prior 1: the run of the PREVIOUS night, i.e. leak-free
$P ShortInterrest_backfill_AddON.py -i '../universe_finder/data/stocks_universe_2026-0[5-7]-*.tsv'

# per-row anchors: each PR row resolved from its own ArrivalDate
$P ShortInterrest_backfill_AddON.py -i ../price_stats_loader/outputs/07_PRstats_*.tsv \
     --date-col ArrivalDate

# what WOULD be fetched — resolves dates, fetches nothing, writes nothing
$P ShortInterrest_backfill_AddON.py -i <file>.tsv --days-prior 1 --check-dates

# THE TEST — there is no test suite; this is it.
# Re-derives the 24 columns as-of and diffs them against a file the live addon
# already enriched. Prints a per-column match/expected/MISMATCH table.
$P ShortInterrest_backfill_AddON.py \
     -i ../universe_finder/data/stocks_universe_2026-08-11.tsv \
     --days-prior 0 --compare-to same
```

`--days-prior 0` is **required** here: the reference was written by the live addon
on the file's own date, so at the default N=1 every column differs by one run
(the script warns if you forget).

`--compare-to` PASSes only when every *comparable* value reproduces. Two
mismatch classes are counted as `expected`, not failures: rows the live run
sourced from yfinance (blank in backfill by design), and the borrow block
(live snapshot vs. sliced history — the backfill is often the richer side,
because a live run that lost an iBorrowDesk fetch to a 403/444 wrote blanks).
Any other mismatch is a real regression.

Runtime *used* to be dominated by the per-symbol iBorrowDesk politeness delay
(~17 min for 685 symbols). Now that the endpoint is dead the circuit breaker
trips after 20 consecutive failures and a full universe takes seconds;
everything else is a handful of whole-market downloads.

## Architecture

`compute_symbol_metrics()` in the live addon is the **single scoring engine**.
The backfill imports it — plus `borrow_history_metrics`, `regsho_flag_and_streak`,
`_parse_finra_records`, `OUTPUT_COLUMNS` and the thresholds — and reimplements
*only* the date-windowing loops. That is what keeps the two from drifting and
what makes `--compare-to` meaningful. **Never fork a formula into the backfill.**

Six sources → 24 columns:

| Source | Columns | Cadence / as-of mechanism |
|---|---|---|
| FINRA Equity Short Interest (anonymous POST, `api.finra.org`) | `SI_*` ×8 | bi-monthly; backfill queries `settlementDate` with an **EQUAL** CompareFilter (it is a partition key) |
| FINRA daily short volume `CNMSshvol{YYYYMMDD}.txt` | `SVR_*` ×4 | daily, permanently archived, **no publication lag** |
| IBKR shortstock FTP `usa.txt` | `CTB_RebatePct` (+fee/avail when reachable) | **DEAD — never once reachable here** (see below) |
| iBorrowDesk `/api/ticker/<SYM>` | `CTB_*`/`Borrow_*` ×7 | **DEAD since 2026-07-22** (see below); the cached JSONs still serve the backfill for dates ≤ 2026-07-21 |
| IBKR `reqHistoricalData` `FEE_RATE`/`REBATE_RATE` | `CTB_FeePct`, `CTB_RebatePct`, `CTB_FeeDelta5d` | **the working borrow source**; daily bars, **1 year deep**, so any anchor in range is served. Cached by `ibkr_rates_fetch_AddON.py`, read by `--ibkr-rates` |
| Nasdaq Reg SHO threshold list `nasdaqth{YYYYMMDD}.txt` | `RegSHO_*` ×2 | daily, **posted D+1** relative to a 20:30 ET run |

### ⚠ Both borrow sources are dead — live borrow comes from IBKR now

- **iBorrowDesk stopped answering on 2026-07-22.** Every request fails with
  `RemoteDisconnected`; the site root returns HTTP 403 and a browser-UA request
  is reset. We are blocked, so no header or pacing change will revive it.
- **The IBKR shortstock FTP has never worked from this network.**
  `cache/shortstock/` was never created; ports 21/80/443/990 on
  `ftp3.interactivebrokers.com` are unreachable. Since `compute_symbol_metrics`
  reads `CTB_RebatePct` *only* from that snapshot, **that column was blank in
  every file this project ever produced** — live and backfilled alike.

Consequence: `stocks_universe_*.tsv` from **2026-07-22 to 2026-08-29 is 0/N on
all seven borrow columns**. Since 2026-08-30 the nightly pipeline fetches them
from the IBKR API instead — `../universe_finder/ibkr_borrow_addOn.py`, Step 3.6
— and hands them to `enrich_dataframe(..., ibkr_borrow=...)`, which then skips
both fetches above entirely. `Borrow_Source` reads `ibkr_api` on those rows.
See `../universe_finder/CLAUDE.md` for the units (×100), the calibration test,
and the rolling availability store.

`--skip-borrow-history` is now near-vestigial; the iBorrowDesk loop it disables
also self-disables after `IBORROW_FAILURE_BUDGET` (20) consecutive failures,
which is what stopped a full universe costing ~15 min of guaranteed-to-fail
0.8 s sleeps.

### Backfilling borrow: `--ibkr-rates` (two steps)

Without it the backfill's borrow block collapses on any recent corpus — the
iBorrowDesk cache ends at fetch date **2026-07-21** and only ever held the 809
symbols that reached a nightly universe file. On the 4804-row PR-stats corpus
that meant 41% borrow coverage with a hard cliff at 2026-07-28 (August 0/1153),
because `fetch_iborrowdesk_asof` drops a snapshot more than
`IBD_MAX_STALENESS_DAYS` (5) stale rather than backdating it.

IBKR serves both rate series as ordinary historical bars, a **full year** deep
(measured: 251 bars back to 2025-09-02; a 30-symbol sample drawn only from
never-cached symbols returned 30/30). So:

```bash
# 1. build the cache — the ONLY step that touches the Gateway. Use the idle
#    PAPER GW; the live one drops bulk historical under contention. Resumable:
#    an already-cached symbol is skipped unless --overwrite.
$P ibkr_rates_fetch_AddON.py -i <file>.tsv --port 4002 --clientID 63

# 2. backfill offline against it, as usual
$P ShortInterrest_backfill_AddON.py -i <file>.tsv --ibkr-rates --date-col ArrivalDate
```

Whole-history-then-slice, not one request per (symbol, date): cost is 2 requests
per symbol however many dates the corpus spans. Rates are scaled ×100 **once, at
write time**, so `cache/ibkr_rates/*.json` is already in percent.

⚠ **Three of the seven borrow columns can never be backfilled.**
`shortableShares` (generic tick 236) is a live snapshot with **no historical
`whatToShow`**, and the rolling store `../universe_finder/data/borrow_avail_history.tsv`
only begins 2026-08-28. So `Borrow_Available`, `Borrow_AvailDelta5dPct` and
`Borrow_UtilProxy` stay blank wherever iBorrowDesk does not reach. In score
terms the fee-driven **CTB30 (12) + CTB80 (6)** come back; **AVAILDROP (6) +
UTIL90 (6)** do not. Do not synthesise them — `ibkr_borrow_addOn.py` warns that
mixing iBorrowDesk's rounded availability with IBKR's precise values corrupts
every delta spanning the seam.

`--ibkr-rates` **replaces** the fee columns everywhere it has data, rather than
only filling blanks, so the corpus has one vendor end to end; iBorrowDesk's
`fee_delta_5d` indexes 6 records back in a *sparse* array (months, on a thin
name) where IBKR's is a true 5-session step. `Borrow_Source` records the split:
`ibkr_api` for fee-only rows, `ibkr_api+iborrowdesk` where iBorrowDesk still
supplies availability.

Scoring: 0–100 `SqueezeScore` = structure 40 (SIF20/SIF30/DTC3/DTC5) + borrow
friction 30 (CTB30/CTB80/AVAILDROP/UTIL90) + flow 15 (SVR50/SVRUP) + float/reg
15 (LOWFLOAT/REGSHO), thresholds from the three deep-research docs in `doc/`.
`SqueezeFlags` also carries two informational, non-scoring flags: `LOWLIQ`
(dollar-ADV < $1M — a flag, not a veto; this universe is intentionally micro-cap)
and `FLOAT_SUSPECT` (SI % of float > 100).

### Caching (`./cache`, shared by both scripts)

| Dir | Key | Shared? |
|---|---|---|
| `finra_si/` | `{dataset}_{fetch_date}_{sha1(symbols)}.json` | live only |
| `finra_si_asof/` | `{dataset}_settle_{settlementDate}.json` (whole market, ~21k rows) | backfill only |
| `shvol/`, `regsho/` | the data date itself | **both** |
| `iborrowdesk/` | `{SYM}_{fetch_date}.json` | both; backfill indexes by fetch date |

The two FINRA dirs are deliberately separate: the live key has no settlement in
it, so reading `finra_si/` from the backfill would silently return today's
records. Failed downloads are never cached.

## The date contract (`--days-prior`)

The live addon anchors on `eastern_today()` and the cron fires **every calendar
day**, so the universe file's name is a *run date*, not a session. The backfill
reconstructs that run date and derives session windows from it:

```
run_date = <row date> - N calendar days   # which nightly run is being replayed
anchor   = last XNYS session <= run_date  # SVR window end + FINRA settlement
reg_end   = last XNYS session <  run_date # newest Reg SHO threshold list
```

The asymmetry is not a guess — it is the publication lag, visible in the mtimes
of `cache/`: `CNMSshvol{D}` is downloaded **on D** (20:33–21:55, 12/12), so the
run sees it; `nasdaqth{D}` is downloaded **on D+1** (12/12), so it does not.

Checked over all 237 calendar days of 2026 to date: `N=0` on D fetches what the
live addon fetched on D, `N=1` on D+1 equals `N=0` on D, and the anchor is
identical to `market_state_calculator.py`'s `Sessions.resolve(date, N)` for
N=0 and N=1. For N≥2 this counts nightly **runs** where MSC counts **sessions**
— they diverge on weekends, and only N=0/N=1 are in scope.

`N` defaults to **1**, so a `_SIback.tsv` produced before 2026-08-27 (when the
flag did not exist) has N=0 semantics. Regenerate with `--overwrite`, or pass
`--days-prior 0`.

## Gotchas

- **These sources are flaky by design.** IBKR FTP port 21 is blocked on this
  network; iBorrowDesk is now 403 for everything (`--allow-network-borrow` is
  the escape hatch, cache-first otherwise, but it will not help — the live
  borrow path moved to the IBKR API, see above); today's `CNMSshvol` 403s until
  ~6 PM ET. All of this is logged and tolerated — do not "fix" it into a hard
  failure.
- **Backfill drops the yfinance SI fallback on purpose.** It is a today-only
  snapshot; using it historically would be a lookahead leak.
- **Reg SHO lag is load-bearing.** `REGSHO_PUBLICATION_LAG_SESSIONS = 1`;
  anchoring the walk at the run date showed up as a systematic +1 on
  `RegSHO_ConsecDays`. Counted in *sessions*, so it steps past a holiday. Use
  `--regsho-lag-sessions 0` only to reproduce a run that overran past ~23:00 ET
  (the 2026-07-06 night); `--regsho-lag-days` is the old name, still accepted.
- **`FINRA_PUBLICATION_LAG_BDAYS = 7` needs a holiday-aware calendar**
  (`pandas_market_calendars`, venv only). Weekday arithmetic picks the wrong
  settlement near holidays — that is what the 2026-07-09 → 07-10 flip caught.
- **`44444` is the upstream pipeline's "fetch failed" sentinel.** The backfill
  nulls it out of `Float_M`/`LastDailyClosePrice`; the live addon does not.
- Read universe TSVs with `dtype=str, keep_default_na=False` — it preserves
  upstream formatting byte-for-byte and stops pandas coercing the ticker `NA`
  (Nano Labs) into NaN, dropping the row.
- **Don't call the live `setup_logging()` from an importing process** — it does
  `basicConfig(force=True)` and wipes the caller's handlers. Both modules log to
  the named logger `short_interest`, which is how the pipeline routes them.
- SI is structurally **9–24 days stale** — a slow prior, not a timing signal.
  Daily short volume is off-exchange flow, **not** short interest. The threshold
  check is Nasdaq-listed only.
- `enrich_dataframe` re-scans the frame per symbol (O(n²)); painful on the 7k-row
  priced files. The backfill's one-pass `first_row` dict is the fixed version.
- **Per-row mode is automatic** when the frame has an `ArrivalDate`/`Date`/`date`
  column (same pick order as MSC). An explicit `--as-of` outranks it and forces a
  single anchor; an explicit `--date-col` outranks `--as-of`. Rows sharing an
  `(anchor, reg_end)` pair are fetched as one group, and per-row mode reads each
  row's **own** `Float_M`/`LastDailyClosePrice` — the single-anchor path used to
  take the first row's for the whole symbol (identical while symbols are unique,
  which they are in every universe file: 748/748).
- **Adding a column**: append to `OUTPUT_COLUMNS` and fill it in
  `compute_symbol_metrics` — the backfill and `--compare-to` inherit it for free,
  but `daily_universe_pipeline-2.py`'s docstring hardcodes "24 columns / 38-column
  file" and needs updating too.
- **`cache/regsho/*.txt` and `cache/shvol/*.txt` are tracked by git** — the root
  `.gitignore` un-ignores `*.txt` everywhere. The `.json` caches (`finra_si*/`,
  `iborrowdesk/`, ~600 MB) are not. Don't add the JSON dirs.
