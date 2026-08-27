# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Read `../CLAUDE.md` first for the cross-cutting N2 rules (venv interpreter, git policy).

## Purpose

Two scripts append the **same 24 short-interest / short-squeeze columns** to a
universe TSV, assembled from free web sources — **no IBKR API connection**:

| Script | Anchors on | Output |
|---|---|---|
| `ShortInterrest_AddON.py` | `eastern_today()` — describes *now* | `<stem>_SI.tsv` |
| `ShortInterrest_backfill_AddON.py` | an `as_of` date — point-in-time | `<stem>_SIback.tsv` |

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

# historical backfill (as-of parsed from the filename; globs + multiple -i OK)
$P ShortInterrest_backfill_AddON.py -i '../universe_finder/data/stocks_universe_2026-0[5-7]-*.tsv'

# THE TEST — there is no test suite; this is it.
# Re-derives the 24 columns as-of and diffs them against a file the live addon
# already enriched. Prints a per-column match/expected/MISMATCH table.
$P ShortInterrest_backfill_AddON.py \
     -i ../universe_finder/data/stocks_universe_2026-08-11.tsv --compare-to same
```

`--compare-to` PASSes only when every *comparable* value reproduces. Two
mismatch classes are counted as `expected`, not failures: rows the live run
sourced from yfinance (blank in backfill by design), and the borrow block
(live snapshot vs. sliced history — the backfill is often the richer side,
because a live run that lost an iBorrowDesk fetch to a 403/444 wrote blanks).
Any other mismatch is a real regression.

Runtime is dominated by the per-symbol iBorrowDesk politeness delay: ~17 min for
685 symbols; everything else is a handful of whole-market downloads. Same-day
re-runs only repeat the iBorrowDesk misses.

## Architecture

`compute_symbol_metrics()` in the live addon is the **single scoring engine**.
The backfill imports it — plus `borrow_history_metrics`, `regsho_flag_and_streak`,
`_parse_finra_records`, `OUTPUT_COLUMNS` and the thresholds — and reimplements
*only* the date-windowing loops. That is what keeps the two from drifting and
what makes `--compare-to` meaningful. **Never fork a formula into the backfill.**

Five sources → 24 columns:

| Source | Columns | Cadence / as-of mechanism |
|---|---|---|
| FINRA Equity Short Interest (anonymous POST, `api.finra.org`) | `SI_*` ×8 | bi-monthly; backfill queries `settlementDate` with an **EQUAL** CompareFilter (it is a partition key) |
| FINRA daily short volume `CNMSshvol{YYYYMMDD}.txt` | `SVR_*` ×4 | daily, permanently archived, **no publication lag** |
| IBKR shortstock FTP `usa.txt` | `CTB_RebatePct` (+fee/avail when reachable) | snapshot only, no history → always blank in backfill |
| iBorrowDesk `/api/ticker/<SYM>` | `CTB_*`/`Borrow_*` ×7 | ~1 y of daily history per JSON → backfill slices it to `<= as_of` |
| Nasdaq Reg SHO threshold list `nasdaqth{YYYYMMDD}.txt` | `RegSHO_*` ×2 | daily, **posted D+1** relative to a 20:30 ET run |

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

## Gotchas

- **These sources are flaky by design.** IBKR FTP port 21 is blocked on this
  network (borrow falls through to iBorrowDesk); iBorrowDesk returns 403/444 for
  many symbols and is now 403 for live backfill fetches (`--allow-network-borrow`
  is the escape hatch, cache-first otherwise); today's `CNMSshvol` 403s until
  ~6 PM ET. All of this is logged and tolerated — do not "fix" it into a hard
  failure.
- **Backfill drops the yfinance SI fallback on purpose.** It is a today-only
  snapshot; using it historically would be a lookahead leak.
- **Reg SHO lag is load-bearing.** `REGSHO_PUBLICATION_LAG_DAYS = 1`; anchoring
  the walk at `as_of` showed up as a systematic +1 on `RegSHO_ConsecDays`. Use
  `--regsho-lag-days 0` only to reproduce a run that overran past ~23:00 ET
  (the 2026-07-06 night).
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
- **Adding a column**: append to `OUTPUT_COLUMNS` and fill it in
  `compute_symbol_metrics` — the backfill and `--compare-to` inherit it for free,
  but `daily_universe_pipeline-2.py`'s docstring hardcodes "24 columns / 38-column
  file" and needs updating too.
- **`cache/regsho/*.txt` and `cache/shvol/*.txt` are tracked by git** — the root
  `.gitignore` un-ignores `*.txt` everywhere. The `.json` caches (`finra_si*/`,
  `iborrowdesk/`, ~600 MB) are not. Don't add the JSON dirs.
