# CLAUDE.md

Guidance for working with universe_finder pipeline code in this directory.

## `universe_finder.py` — in-memory symbol filter library

Importable module that maintains a filtered list of small-cap symbols (no IBKR connection; yfinance only).

```python
universe_finder.start(watchlist_path='./watchlist.txt', max_institution_pct=20,
    max_float_m=20, max_price=10, refresh_minutes=5)
symbols = universe_finder.get_universe()  # blocks on first call until ready
```

**Architecture:** Two-phase. (1) Startup fetches fundamentals once via `ThreadPoolExecutor` using `yf.Ticker.info` — never re-fetched. (2) Every refresh cycle batch-fetches close prices via `yf.download()`, re-applies filters.

**Threading:** `start()` blocks caller until first fetch (`threading.Event`); daemon `_refresh_loop()` runs indefinitely; `threading.Lock` guards `_universe` between background writes and reads.

**Filter logic:** ALL three criteria must pass (AND). Missing data treated as passing (permissive policy).

Watchlist format: plain text, one symbol per line, `#` = comment. Logs to `./runs/DD-Mon-YYYY/universe_finder.log` at DEBUG level.

---

## `pipeline_daily.py` — legacy nightly builder (SUPERSEDED)

Produces dated universe TSVs; replaced by `daily_universe_pipeline-2.py` since 2026-06-08.

Four steps: (1) fundamentals via finviz/yfinance, (2) IBKR close prices, (3) RTH/ETH inter-trade intervals, (4) ITI imputation.

**Crash-safety invariants (do not break):**
1. All result columns pre-initialized to NaN before `connect()` — any exit path must still produce complete file with 44444 sentinels.
2. `df.to_csv(...)` lives OUTSIDE the IBKR `try/finally`.
3. `_run_concurrent` catches `ConnectionError`/`OSError`, tries one reconnect, fills remainder with `nan_value` on failure — does NOT retry indefinitely (would loop forever around 03:59 ET Gateway restart).
4. `nan_value` is caller's parameter (Step 2: `(nan, None)`; Step 3: `(nan, nan)`).

**Throughput:** `--price-concurrency` 12 (default), `--freq-concurrency` 4 (default). Error 366 > ~50 on a full run = Gateway-side slot exhaustion, not connection problem — lower `--freq-concurrency`.

**Two TSV file kinds:** `nasdaq_symbols_data.tsv` (Step 1 cache only, not output); `nasdaq_symbols_data_priced_YYYY-MM-DD.tsv` (the real output).

---

## `daily_universe_pipeline-3.py` — current nightly cron (SINCE 2026-09-17)

v2 plus the two blocks the XGBoost models need, minus the borrow fetch. Outputs
the SAME `stocks_universe_YYYY-MM-DD.tsv` (147 columns, 151 with `--splits`);
O5.2 / daily-board read by column name, so the extra columns are transparent.

```
 38  v2 today       Symbol … SI_DataCoverage          unchanged
 +5  Step 8.5       Date + 4 raw ITI/trade-size sidecars
+84  Step 9         market_state_calculator.py        ~60 min IBKR
+20  Step 10        vix_addOn.py                      ~5 s, 2 bulk HTTP
[+4] Step 11        Stock-Split-finder.py             opt-in, --splits
```

**The anchor contract — the whole point.** Every session-resolving step of the
training-set chain ran at `--days-prior 1` against each PR's `ArrivalDate`. v3
runs on the evening of day D at `--days-prior 0`, which
`tools/market_state/CLAUDE.md` states as an identity:

    training:  PR on ArrivalDate = D+1, --days-prior 1 → resolve(D+1, 1) = D
    v3:        run on day D,            --days-prior 0 → resolve(D,   0) = D
    "Train with 1, infer with 0."

`SESSION_DATE` is resolved ONCE at process start (`_session_date`) and drives the
output filename, the `Date` column, MSC's `--anchor-date`, vix_addOn's anchor and
the splits `--as-of`. Nothing re-reads the clock. ⚠ This is not cosmetic:
`market_state_calculator.load_universe_price_refs` resolves
`stocks_universe_<anchor>.tsv` **by filename** to referee `msc_basis_ratio` — a
model feature — so a drifted name silently costs it its reference price.
Weekends need no special-casing: `--days-prior 0` on a Saturday resolves to Friday.

**Steps 9–11 are subprocess replays** of each addon's own CLI, not reimplementations.
`_run_addon` skips on a marker column (`msc_anchor_date` / `VIX_AnchorDate` /
`LastSplit_Status`) — MSC and vix_addOn do NOT self-guard and a second pass would
append a duplicate copy of every column — then verifies the `<stem>_<suffix>.tsv`
sibling column-for-column against its input before `os.replace`. Any failure
leaves the pre-step file intact.

**Fixed flags that are NOT free choices:**
- `--session both` (Step 9). The training `msc_` block was built with it; the
  three session modes are not comparable and `msc_session` records which made a row.
- `--split-cache-max-age-days 36500` (Steps 9 and 11). The addons' own default of
  1 re-queries ~830 symbols nightly, and for a **delisted** symbol yfinance
  returns empty splits with live metadata, so `fetch_splits` writes a header-only
  "never split" over real history (NTZ, OMH — 2026-09-07). `forward_split_factor`
  is a no-op in universe mode, so the TTL buys nothing here. See
  [[split-cache-refresh-degrades-delisted]].
- `--never-split-fill blank` (Step 11). The default `maxima` sentinel is derived
  from **the file's own** resolved rows, so a nightly file emits a different
  sentinel than the one frozen into the training corpus.
- `--refresh` (Step 10). Alone among the steps, `vix_addOn` resolves its anchor
  from **data availability**, not the calendar: the session index is whatever rows
  the CBOE history holds, and `resolve_anchor` then returns the newest session at
  or before the target with `problem=None` — no warning, no flag, no blank (it
  even logs the miss as "non-session date (weekend/holiday)", wrong for a
  Thursday). A cache too fresh to refetch therefore slides the whole VIX/FNG
  block back a session, `FNG_Index` included. `--refresh` bypasses the 12 h TTL
  for both caches (`load_history:315`, `load_fng_history:454`). Without it, any
  v3 run between ~08:30 and 20:30 poisons that evening's cron. FNG rides the VIX
  index (`fill_fng` reads `days[j]`), so it slides too even when CNN's own cache
  has the right day. `_check_vix_anchor` then compares `VIX_AnchorDate` against
  **`msc_anchor_date`** and WARNS on mismatch — `--refresh` cannot help when CBOE
  has simply not published yet, which is the remaining cause.
  ⚠ The reference is `msc_anchor_date`, NOT `Date`. `Date` is the calendar day the
  file was produced; `msc_anchor_date` is the session `Sessions.resolve(Date, 0)`
  landed on, and on a **weekend run those legitimately differ** (Sunday 2026-09-20
  → Friday 2026-09-18). Comparing against `Date` made the check fire on both
  2026-09-19 and 2026-09-20 while those files were perfectly aligned; fixed
  2026-09-21. The equality the training corpus validated is
  `VIX_AnchorDate == msc_anchor_date` (4804/4804 rows), and it is weekend-safe
  because vix_addOn walks back to the same preceding session MSC does.

**Borrow is gone.** Step 3.6 and all five `--borrow-*` flags removed: ~72 min of
the run, 0 of the 49 model features, and grepping `orchestrator5/`, `x-wing-mole/`
and `daily-board/` for `CTB_*`/`Borrow_*`/`SqueezeScore`/`RegSHO_*` returns zero
hits. The 7 columns stay in the header, blank. ⚠ Step 8 must be called with
`ibkr_borrow={}` — **not `None`**: `use_ibkr = ibkr_borrow is not None`, so `None`
re-enables the two dead web sources (15 s FTP timeout + ~830 iBorrowDesk calls at
0.8 s = ~15 min to produce nothing).

**Raw ITI/trade-size sidecars** (`RTH_avgITI_sec_raw`, `ETH_avgITI_sec_raw`,
`RTH_tradeSize_raw`, `ETH_TradeSize_raw`). Training used the RAW 44444 sentinel,
which `xgb_cons49_se_prep.json` maps to frozen constants (46800.0 / 68400.0 /
0.0); Steps 4 and 7 overwrite it with HGB predictions, and must, because O5.2
reads the canonical columns as `itiBaseline`/`tradeSizeBaseline`. Both views now
ship. None of the three is in the current 49-feature allowlist — this is for retrains.

**The model features this file supplies.** ⚠ The live models changed on
2026-09-20: O5.3 runs **model-2 + model-2FT**, whose 10 boosters share ONE
25-feature allowlist (`training_data/model-2/xgb_fromPRhigh_features_*.txt` —
`conservative` and `aggressive` are byte-identical, and model-2FT's five preps
carry the same list). **9 of those 25 come from here**, and `_M2_UNIVERSE_FEATURES`
in the script is that list:

    Float_M→Float   MarketCap_M→MktCap   (O5.2/5.3 rename these two)
    SI_ChangePct   atr_pct_14   float_turnover_max_20d   msc_basis_ratio
    dist_swing_high_20d_pct   range_pct_1d   amihud_63d

The other 16 are PR-level (`FineClass`, `TimeToOpen`, 6 encoder scores, 8 FinBERT
PCA) and belong to O5.3. The superseded 49-feature model-1 took **15** from here;
the 6 it used and model-2 dropped — `SVR_5dAvgPct`, `FNG_Index`,
`realized_vol_5d`, `gap_abs_mean_20d`, `days_since_52w_low`, `illiq_trend` — are
still emitted for retrains, and the end-of-run check still counts all 15.
⚠ `msc_price_ref_src` and `FNG_Rating` appear in every prep's `cat_cols` but not
in `features_used`; `cat_cols` is the whole training frame's categorical list, so
they are not model inputs. Same for the three sentinel columns
(`RTH_avgITI_sec`, `ETH_avgITI_sec`, `RTH_tradeSize`): the inference script
substitutes 44444 on them unconditionally, but none survived the prune, so Steps
4/7 overwriting the raw sentinel with an HGB prediction cannot reach the model.

⚠ **Accepted divergence:** `RegSHO_Flag`/`RegSHO_ConsecDays` get their 1-session
publication lag by accident (the live SI addon walks back from `eastern_today()`
and Nasdaq has not posted yet). A run reaching Step 8 after ~23:00 ET reads day
D's list where the training backfill used D−1's. Neither is a model feature; only
`SqueezeScore` (10 of 100 points) is touched. Keeping Step 8 *before* the
60-minute Step 9 is what keeps most nights on the right side of 23:00.

Runtime ~2h20m on a cold-Step-1 night, ~1h05m warm — **faster than v2** despite
the new fetch, because dropping borrow (~72 min) more than pays for MSC (~60 min).
⚠ MSC's bar cache gives nothing night-over-night: its hit test is
`cached[-1].date >= anchor`, so a new anchor misses every symbol and refetches a
full 2-year series.

**Step 1 finviz and the run lock (2026-10-03).** finviz has given no data since
~2026-06 and answers HTTP 403 since ~2026-09-30. The old code read 403 as a rate
limit and waited 30+60+90 s per symbol, so the 09-30, 10-01 and 10-02 crons all
stalled in Step 1 (7020 symbols ≈ 5 days) and piled up on top of each other.
Now only 429 is retried; 403 fails at once, and after 25 finviz failures in a
row `NASDAQ_symbols_data` switches finviz off for the run (one WARNING line) —
Step 1 is yfinance-only, and yfinance then waits 1.5 s (not 0.5 s) per request:
without finviz's ~1 s call in between, 0.5 s drew yfinance 429s. Step 1 cold ≈
65 min (was ~73 with finviz in the loop). `main()` also takes a non-blocking `flock` on
`runs/pipeline_daily-3.lock` before anything else; a second run prints a line to
`cron.log` and exits 1. Step 1 is only slow on a cold night (cache > 24 h).

**Phone alerts (ntfy, since 2026-10-03).** Same ntfy topic as the IB Gateway
alerts (`/home/tom/IBC/.secrets/ntfy.env`, read by `ntfy_alert.py`; the topic is
never logged). Failures only — silence means the night was fine. Every alert,
sent or not, is also appended to `runs/alerts.log`.
- **blocked** — a run found the previous one still holding the lock (`_acquire_run_lock`).
- **crashed** — `main()` raised; the traceback stays in `cron.log`.
- **flawed** — the file was written but `_problems` is non-empty: Step 9/10/11
  failed or its script is missing, VIX/FNG anchor ≠ MSC anchor, any of the 15
  feature columns absent, fewer than 500 rows, or an IBKR step lost the Gateway /
  tripped the contention canary. One alert per run lists them all.
- **no file by 06:00** — `universe_watchdog.py` (cron 06:00 ET) checks that
  yesterday's `stocks_universe_D.tsv` exists AND its per-day log says
  "finished"; it reports whether that run is still going and the log's last line.
  This is the only one that catches a hang or a kill.
Test: `ntfy_alert.py --test`; `universe_watchdog.py --date 2026-09-30 --dry-run`.

Cron entry: `30 20 * * * /home/tom/venv/bin/python3 …/daily_universe_pipeline-3.py
--port 4002 --freq-concurrency 2 --freq-timeout 60 >> runs/cron.log 2>&1`
(the crontab block already exports `TZ=America/New_York`, which is what makes
`datetime.now()` in `_session_date` an ET date). Per-day log is
`runs/{DD-MMM-YYYY}/pipeline_daily-3.log`.

---

## `RetroUniverse-3.py` — pipeline-3's columns for PAST PRs (since 2026-10-03)

Input: any TSV with `Symbol` + `ArrivalDate`. Output `<stem>_RetroU3.tsv` beside it:
every input row in order + the 150 universe columns (pipeline-3's 147 − `Symbol`
+ 4 `LastSplit_*`) exactly as `stocks_universe_<ArrivalDate−1>.tsv` would hold
them + `RU3_*` provenance and marketcap_backfill's `MarketCap_Source/AsOf/LagDays`.
Replaces the hand-run `--days-prior 1` chain of
`price_stats_loader/training-set-construction-steps.md`. Colliding input columns
are kept as `<name>_input`.

**Universe orientation.** `resolve(A,1) == resolve(A−1,0)` on every calendar day,
so it builds a work table W (one row per unique (Symbol, ArrivalDate),
`Date = A−1`) and replays each addon against `Date` at `--days-prior 0`, as the
nightly does per file. ⚠ Every addon auto-detects `ArrivalDate` BEFORE `Date`,
so `Date` is always passed explicitly; only marketcap_backfill keeps ArrivalDate
and its own `--days-prior 1`.

**Steps.**
- S1: Float_M / Float_Source / Exchange are joined from the served-by snapshot.
- S2: an ADDITIVE split-cache refresh, then the IBKR RTH close × forward split factor (see Splits). When IBKR no longer serves a symbol (delisted since), it falls back to the nightly's own close, but only from a snapshot whose true session is the row's anchor (`RU3_Close_SplitAdj=snapshot`). That same snapshot referees every close: ≥1.2× off it means yfinance dates a split on another session than IBKR applied it (RUBI: ex 10-06 on yfinance, 10-05 at IBKR → 1.05 vs 0.70), and the snapshot's close is used (`refereed`, since 2026-10-09). S8 runs `--split-source both` so its trade-size restatement follows — which also stops the backfill from undoing a split IBKR has not applied yet (NIVF/NXGL/ZBAO went ex on the run day 10-09; with `yfinance` their sizes were 4×/20×/50× too large).
- S3: marketcap_backfill, unchanged.
- S4: SI backfill with `--skip-borrow-history`.
- S5: vix.
- S6: splits with `--eod-fallback --never-split-fill blank`.
- S7: MSC, chunked by symbol; symbols with a split after their anchor run in separate `mscnc_*` chunks on fresh bars, through a private `--cache-dir` (`<work>/msc_cache/`: empty `bars/` + a link to the shared `splits/`). ⚠ Never MSC's `--no-cache`: it also skips the split cache and asks yfinance live, which on 2026-10-09 listed only NIVF's split of that day and dropped seven older ones (`msc_split_*` read "no split").
- S8: TFB, chunked by unit, plus a Step-3.5-style retry.
- S9: imputers POOLED over the input.

**Live-file semantics** everywhere (operator decision 2026-09-28):
- borrow is blank;
- LastSplit hours are measured to Date 20:00;
- the close is the anchor session's RTH close;
- the MSC referee is a dir of snapshot symlinks renamed to each file's TRUE session (`universe_session_map.tsv`), else this run's own close.

Float_M is blank when the symbol is not in the served-by file. That is always the case before 2026-05-14, where `RU3_FilterReason=float_missing`.

**Resume.**
- Work dir: `runs/retro/<stem>[_limitN]/` (W.tsv, msc/ and freq/ chunks, per-addon logs).
- Every step and chunk is marker-guarded, so after any failure re-run the same command. `--fresh` restarts.
- Exit code 1 means something is incomplete.

**Safety.**
- It never writes into `data/`: RTPR_connector3 loads the newest-*created* `stocks_universe_YYYY-MM-DD.tsv` there as the live list.
- Before each IBKR step or chunk it waits while `daily_universe_pipeline-3.py` is running, or between 20:05 and 20:40 ET (`--no-yield` overrides). It detects the nightly through `/proc` and never touches `runs/pipeline_daily-3.lock`.
- It refuses ports 4001/7496 without `--allow-live-gateway`.
- ClientIds: 81 (close), 82 (TFB), 83 (MSC).
- Every split lookup runs at max-age 36500, but only AFTER S2 has refreshed the cache additively (next section).

**Splits — 36500 alone is wrong for past anchors.** A past anchor needs every split *after* it. The 09-07 cache files lacked CDT's and TRUG's 2026-09-29 reverse splits, so the first smoke run read:
- closes 25× and 10× off;
- `msc_basis_ratio` 25 and 10;
- trade sizes divided by 25.

So S2 re-queries yfinance with `fetch_splits(use_cache=False)`, which never writes, and **merges by ex-date into `tools/market_state/tables/splits/`**:
- new ex-dates are added;
- a factor that differs beyond float noise takes yfinance's value;
- an ex-date yfinance dropped is kept (the delisted-symbol wipe);
- a failed lookup changes nothing.

Verified against a full copy of the cache: 0 ex-dates lost or changed, only real splits added.

MSC's split arithmetic also assumes bars pulled *on* an ex-date are still pre-split. That was true for ARTL, but CDT and TRUG were pulled already adjusted by the 09-29 nightly, and no referee can reconcile that. So split-straddling symbols get fresh bars (a private `--cache-dir`, see S7). `--no-split-refresh` skips the refresh for offline re-runs.

⚠ **Live consequence.** The nightly's own 36500 never refreshes an existing list. Its `msc_split_*` columns therefore go stale for any symbol that split after its file was written:
- ALP's 09-09 1-for-50 split was absent from `stocks_universe_2026-09-21.tsv`, which reads `msc_split_sessions_since` 525 where the truth is 8;
- `msc_split_sessions_since` is a model-3 input.

**`--compare-live`** writes `<out>_vs_live.tsv`: per-column agreement with the real snapshot of each row's Date. Checked offline on PRs from 09-18 to 09-24:
- Float_M, MarketCap_M, Float_Source, Exchange and Date, plus all 24 SI columns, agree 100%.
- `FNG_Index` differs on about every row: live holds CNN's provisional same-day score, the history holds the settled one. It is not a model-2/3 input.
- The 09-23 nightly's VIX/FNG block is a session behind (CBOE was late); retro has the right anchor.

Pooled imputation over one real night (09-25) reproduced that night's 8 imputed columns on 824/824 rows.

**Validated 2026-10-03.**
- **Live acceptance**: 232 PRs from 09-18 to 09-24 against their real snapshots, 16 min.
  - Everything outside the market-state block agrees 100%: Float_M, MarketCap_M, Exchange, Date, the close, the 24 SI columns, and VIX except the 09-23 rows (that nightly is a session behind).
  - Volume- and range-based msc features drift by at most ~3%, because IBKR revises bars after the night.
  - `msc_split_*` differs only where the nightly's split cache is stale.
- **Training cross-check**: 100 rows of `08_…_RawData_splits`.
  - All 67 msc features agree on 98% of rows; the rest are symbols IBKR no longer serves.
  - Raw ITI and trade size agree within 1e-3 on 95/100 rows; LastSplit Date/Factor on 62/62.
  - The deliberate differences are borrow, the close definition (RTH, versus the old extended-hours/orchestrator close) and the referee ratio.
  - ⚠ CNN revises its F&G history: the FNG_ block for 07-15..08-27 differs from the corpus's 09-05 fetch, so F&G is not point-in-time. It is a model-1 input only.

**Cost.** TFB takes about 2.7 s per unit at concurrency 2, so 4.7k units is about 3.5 h. MSC takes 1–2 h uncached for about 1.3k symbols, and minutes when the nightly's bar cache already covers the anchors.

---

## `daily_universe_pipeline-2.py` — previous nightly cron (2026-07-07 → 2026-09-17, SUPERSEDED by `-3.py`)

Outputs `stocks_universe_YYYY-MM-DD.tsv` (38 columns). Runs on paper Gateway (port 4002) to avoid live-stack contention (2026-07-06 incident: 691/819 Step 3 failures when shared with live gateway + ~130 tick lines).

**Hardcoded output filters** (NOT CLI flags — code edits):
- `_FILTER_MAX_FLOAT_M = 50.0` (must be < 50M shares)
- `_FILTER_MAX_MCAP_M = 50.0` (must be < 50M)
- `_FILTER_MAX_PRICE = 6.00` (must be < $6)

All three strict — NaN fails. Float/mcap filter runs BEFORE IBKR fetches (early skip); price filter runs AFTER Step 2 (close unknown until then). ⚠️ `MarketCap_M` = shares outstanding × price, NOT float × price.

**Step 3 lookback:** `--past-days-lookback` defaults to 1 (LAST SESSION ONLY, since 2026-07-15). Changed after SOBR case where 3-day mean of sparse ITIs let a dead day dominate. `past_trading_days()` is holiday-aware via `pandas_market_calendars`.

Held the cron slot until 2026-09-17; everything below is still accurate for
steps 1–8, which `-3.py` carries over unchanged. Its one removed step is 3.6
(borrow) and its filename tag came from `datetime.now()` at *write* time, so a
run crossing midnight labelled session-D data as D+1.

**Steps 4–8:** ITI imputation (10 col) → trade-size append (12 col) → trade-size imputation (14 col) → Step 3.6 borrow fetch → SI enrichment (38 col final).

---

## `ibkr_borrow_addOn.py` — Step 3.6 borrow data (2026-08-30 → 2026-09-17, NOT in the nightly path)

⚠ `daily_universe_pipeline-3.py` dropped Step 3.6 — ~72 min a night for columns no live consumer reads and no model feature uses. The script is unchanged and still works standalone; `ShortInterrest_backfill_AddON.py --ibkr-rates` reads the cache that `ShortInterrest/ibkr_rates_fetch_AddON.py` (which imports `_rate_bars` from here) writes.

Supplies 7 borrow columns (`CTB_FeePct`, `CTB_RebatePct`, `CTB_FeeDelta5d`, `Borrow_Available`, `Borrow_AvailDelta5dPct`, `Borrow_UtilProxy`, `Borrow_Source`) via IBKR API, replacing two dead web sources (iBorrowDesk 403 since 2026-07-22; IBKR FTP never worked).

**Three IBKR sources:** `FEE_RATE` bars → CTB_FeePct/Delta; `REBATE_RATE` bars → CTB_RebatePct; market data tick 236 → Borrow_Available.

**⚠️ CRITICAL: IBKR returns ANNUALIZED DECIMAL FRACTIONS — everything must be ×100.** Mis-scaling corrupts CTB30/CTB80 flags by 100×.

Runs inside IBKR block after Step 3.5 retry (borrow failure must never cost ITI data). Reuses Step 2's qualified contracts; symbol failing to qualify short-circuits.

Flags: `--no-ibkr-borrow`, `--borrow-concurrency` 4, `--borrow-timeout` 60, `--borrow-retries` 0.

---

## `ShortInterrest_backfill_AddON.py` — historical SI backfill

Computes 24 SI columns **as of a historical date** (Step 8 only started 2026-07-06). Writes `<stem>_SIback.tsv` (inputs never modified).

**Three publication lags** (the core rule):
1. **FINRA SI — 7 business days, holiday-aware.** Newest settlement `≤ as_of` reproduces production exactly.
2. **Nasdaq RegSHO — 1 day.** File posted ~23:00 ET, so 20:30 cron hasn't caught it yet; use `as_of - 1`.
3. **FINRA daily short volume — none.** Downloaded same day.

⚠️ Do NOT point at live `cache/finra_si/` (wrong key structure; silently returns today's records). ⚠️ `ShortInterrest_backfill_AddON.py` is NOT a fix for the iBorrowDesk hole — it reads frozen cache (stale > 5 days is blank).

---

## `trade_frequency_and_size_backfill_AddON.py` — point-in-time ITI/size

Recomputes `RTH_avgITI_sec, ETH_avgITI_sec, RTH_tradeSize, ETH_TradeSize` as of a historical session. Writes `<stem>_freqSizeBack.tsv`.

**`--days-prior` vs `--past-days-lookback` — do NOT confuse.** First shifts the anchor (which nightly run to replay); second is the window length (defaults both to 1).

⚠️ **Share splits are CORRECTED on trade-size columns.** IBKR back-adjusts historical volume; ITI (transaction count) is immune, but trade size (volume/count) scales by cumulative split factor. `yfinance` leads; a price probe audits discrepancies outside ±2%.

⚠️ **`--split-max-age-days`** (since 2026-10-03; the default of 1 is unchanged and is MSC's own). Pass 36500 for historical work. A refresh overwrites a delisted symbol's split history with "never split" (NTZ, OMH), and the restatement then silently uses F=1.0. RetroUniverse-3 always passes 36500.

---

## `marketcap_backfill_AddON.py` — market cap, TWO MODES

`MarketCap_M` entered Step 1 on 2026-06-05. Two holes, two methods, one script since 2026-09-05 (`price_stats_loader/mktcap_backfill_addOn.py` was deleted and its join absorbed here).

**Which mode runs is decided by `--date-column` (default `ArrivalDate`) being present in the input** — not by a flag. Universe file → filename date, formula only. PR-stats → per-row dates, join then formula.

### Universe files (RECONSTRUCTION)

Formula: `MarketCap_M = LastDailyClosePrice × shares_out / 1e6`. Source shares via cross-file median of production values (default `--shares-source implied` — touches no network). Acceptance test: `-i data/stocks_universe_2026-06-09.tsv --compare-to same --all-rows` → **96.9% within 1%**. ⚠️ Without `--all-rows` the test is a fake 100%: rows that already have a value are `kept` and compared to themselves.

### PR-stats files — pass 1 is a JOIN, NOT A FORMULA

⚠️⚠️ **`MktCap` in PR-stats is NOT `LastDailyClose × sharesOut`.** Orchestrator writes it straight from the universe file loaded at start; PR's `LastDailyClose` filled independently. Join `MarketCap_M` from the most recent mcap-bearing universe file dated ≤ **calendar** `ArrivalDate - 1` (D-1 because file for day D is written by 20:30 cron on D). Calendar, not session: **the cron writes weekend files too**, so a Monday PR was served by Sunday's file (they carry Friday's close, byte-equal on 6291/6292 shared symbols). No split handling — the universe file already recorded it in its own share terms.

### PR-stats pass 2 — formula, for what no universe file can serve

Pre-2026-05-14 rows plus symbols absent from their anchor file. Anchored with `--days-prior` (default 1) via `Sessions.resolve`, imported from `tools/market_state/market_state_calculator.py`.

⚠️ **Splits MUST be divided out here** — the universe-file mode's safety (close and shares from the same file) does not hold. `shares_at_row = shares_observed / F`. ⚠️ **F is anchored on `ArrivalDate`, NOT on the session anchor**, matching how `lastDailyClose_AddOn.py` set the close's basis; the session anchor straddles any ex-date in between and re-applies a correction the price already carries (EZRA's 05-17 1-for-40 inflated its 05-18 row 40×). Cross-check: every corrected row must be `LastDailyClosePrice_SplitAdj == adjusted`, never `none_needed`.

⚠️ Split lookup is **cache-only** (`tools/market_state/tables/splits/`); a miss blanks the cell rather than assuming 1.0. `--split-network` opts in, but the cache covers 3313 symbols against 7143 in a universe file — that burst shares the yfinance endpoint the nightly Step 1 depends on.

On the 2026-05-04→08-28 corpus: joined 1705, formula 770 (19 split-corrected), kept 2311, 18 unfillable (15 no close).
