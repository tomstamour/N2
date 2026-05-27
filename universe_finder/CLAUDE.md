# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Purpose

`universe_finder.py` is a importable Python library that maintains a filtered list of small-cap stock symbols in memory for use by a downstream short-selling strategy. It requires no IBKR connection — all data comes from yfinance.

## Usage

```python
import universe_finder

universe_finder.start(
    watchlist_path='./watchlist.txt',  # required — one symbol per line, '#' = comment
    max_institution_pct=20,            # exclude if institutional ownership >= 20%
    max_float_m=20,                    # exclude if float >= 20M shares
    max_price=10,                      # exclude if last close >= $10
    refresh_minutes=5,
)

symbols = universe_finder.get_universe()  # blocks on first call until ready
```

## Architecture

**Two-phase data model:**
1. **Startup** — fundamentals (float shares, institutional %) fetched once via `ThreadPoolExecutor(max_workers=10)` using `yf.Ticker(sym).info`. Never re-fetched during the session.
2. **Every refresh cycle** — close prices batch-fetched via `yf.download()` for all watchlist symbols at once. Filter re-applied against cached fundamentals.

**Threading model:**
- `start()` blocks the caller until the first fetch completes (`threading.Event`)
- Background daemon thread runs `_refresh_loop()` indefinitely; killed automatically on process exit
- `threading.Lock` protects `_universe` list between background writes and caller reads

**Filter logic:** ALL three criteria must pass (AND). Missing data (None/NaN from yfinance) is treated as passing — permissive policy.

**`yf.download()` column handling:** Returns a `DataFrame` with symbol columns for multiple tickers, or a `Series` for a single ticker. `_fetch_prices()` handles both shapes via `hasattr(close, 'columns')`.

## Watchlist file format

Plain text, one symbol per line, `#` for comments:
```
# My short candidates
HOOD
CLOV
AMC
```

## Logging

Writes to `./runs/DD-Mon-YYYY/universe_finder.log` at DEBUG level. Date is fixed at `start()` call time. Uses a named logger `"universe_finder"` — will not interfere with the calling script's logging.

## Supporting files

- `ntt-test.tsv` — template TSV with columns `c1_Ticker`, `c2_News`, `c3_Price`, `c4_Float-M`, `c5_Inst-%`; intended as a reference table format for downstream consumers of `get_universe()`

---

# `pipeline_daily.py` — nightly IBKR universe builder

`pipeline_daily.py` is a separate, cron-driven script (NOT part of `universe_finder.py`). It produces the dated universe TSVs in `./data/` that downstream consumers read.

## Four steps + twelve-column output

1. **Step 1** — `NASDAQ_symbols_data.build_dataframe()` fetches NASDAQ symbols + float + market cap (finviz primary, yfinance fallback; see `_finviz_fundamentals` / `_yfinance_fundamentals`). Each metric (float, market cap) falls through to the next source independently via `_fetch_fundamentals` — if finviz has a market cap but `Shs Float = '-'`, the float is still recovered from the yfinance fallback instead of being dropped. **Fix (2026-06-05):** previously the first source that returned *either* value "won" both slots, causing symbols like TURB (finviz had market cap but no float) to lose their float. `_fetch_fundamentals` now sources each metric independently, short-circuiting only when both are filled. Result is cached for 24 h. Caches written before the MarketCap_M column was added are auto-invalidated.
2. **Step 2** — IBKR historical close price per symbol (`closing_price_fetch_addOn.fetch_last_rth_close_async`).
3. **Step 3** — RTH/ETH avg inter-trade interval per symbol (`trade_frequency_addOn.fetch_freq_async`, which returns a 2-tuple `(rth_iti, eth_iti)`). (The `trade_frequency_volume_addOn.py` superset, which also returns `RTH_volPerTrade` / `ETH_volPerTrade` from the same TRADES bars and returns a 4-tuple, is retained on disk but **not** wired into the pipeline — it was reverted out after a full-scale run errored, pending separate testing.)
4. **Step 4** — `ITI_imputer.py` overwrites the 8-column file in place with the 10-column imputed version. Runs after the canonical write so an imputer crash can't lose the fetch. Skipped by `--no-impute`.

Intermediate Step 1–3 columns: `Symbol, Exchange, Float_M, MarketCap_M, Float_Source, LastDailyClosePrice, RTH_avgITI_sec, ETH_avgITI_sec`. Step 1 fields (`Float_M`, `MarketCap_M`) stay **NaN** when no source had the value. Missing IBKR data (Step 2/3 fields) is filled with the sentinel **`44444`** (never NaN). `Float_Source` records where the **float** came from and usually doubles as the provenance for `MarketCap_M` — there is no separate `MarketCap_Source`. The two can diverge: `_fetch_fundamentals` sources each metric independently, so when finviz lists a `Market Cap` but `Shs Float = '-'` (e.g. TURB), the float is taken from the yfinance fallback (`Float_Source=yfinance`) while the market cap stays from finviz.

Step 4 writes the model-predicted ITI values **straight into the canonical `RTH_avgITI_sec` / `ETH_avgITI_sec` columns** (rounded to 1 decimal) and adds 2 provenance sidecars: `ITI_impute_flag, ITI_impute_method`. The 44444 sentinels are converted to NaN on load and then the formerly-sentinel rows are filled with predictions; only the never-fetched `nan_skipped` rows (e.g. `--max-float` skips) remain NaN. The orchestrator reads the canonical columns directly, so it now consumes imputed ITIs for those rows and its `if pd.isna(val): TM_DEFAULT_BASELINE_ITI = 44444.0` fallback fires only for the `nan_skipped` rows.

## Two TSV file kinds in `data/` — do NOT confuse them

- `nasdaq_symbols_data.tsv` (no date, 5 columns) — **Step 1 cache only**. Owned by `NASDAQ_symbols_data.py:51,349`. `pipeline_daily` reads it as input; do not treat it as a pipeline output. Its mtime tells you when Step 1 last ran, not when the pipeline last succeeded.
- `nasdaq_symbols_data_priced_YYYY-MM-DD.tsv` (10 columns after Step 4; 8 columns transiently between Steps 3 and 4) — **the real pipeline output**. Written by `pipeline_daily.py` near the end of `main()`, then overwritten in-place by `ITI_imputer.py`. The 8-column transient state can survive a Step 4 crash; re-running `pipeline_daily.py --no-impute` first then standalone `ITI_imputer.py --date YYYY-MM-DD` is the recovery path.
- `nasdaq_symbols_data_priced_YYYY-MM-DD_HHMM.tsv` and `..._HHMM_superseded.tsv` — sidecars produced by the overwrite-protection logic at the write step. If a run finds a same-date canonical file already present and it has more populated RTH+ETH ITI rows than the new run, the new run goes to a timestamped sidecar instead of clobbering. If the new run is at least as good in both columns, the outgoing canonical file is moved to `..._HHMM_superseded.tsv` for one safety cycle. This was added after the 2026-06-03 incident where a daytime re-run silently overwrote the nightly cron output with a strictly worse file (43%/40% → 41%/37%).

If only the no-date file is fresh, the nightly pipeline crashed before its final write — check `runs/{DD-MMM-YYYY}/pipeline_daily.log` and `runs/cron.log`.

**Operational note:** don't stack two `pipeline_daily.py` runs against the same IB Gateway session without restarting the Gateway between them — Step 3 slot accounting carries over and the second run inherits the depleted state. The 2026-06-03 morning losses were caused by a `--limit 500` test at 07:29 followed by a full run at 08:18 on the same Gateway.

## Hard constraint: IB Gateway restarts at 03:59 ET nightly

The cron fires at 20:30 ET, so the available IBKR window is ~7.5 h. At 03:59 the Gateway forces a socket close that ib_insync re-raises as `ConnectionError: Socket disconnect`. Code in this script must tolerate it without aborting — see invariants below.

## Crash-safety invariants (don't break these)

1. **All result columns are pre-initialised to NaN before `connect()`** (`pipeline_daily.main`). Any code path that exits — clean, mid-run disconnect, or unexpected exception — must still produce a complete output file with 44444 sentinels.
2. **`df.to_csv(...)` lives OUTSIDE the IBKR `try/finally`.** Don't move it back inside; the Steps 2/3 block also has an outer `try/except Exception` whose sole job is to fall through to the write.
3. **`_run_concurrent` catches `ConnectionError`/`OSError` from `ib.run(_gather(...))`.** On catch it tries one `ensure_connected`. If reconnect fails, it fills the remainder with the caller's `nan_value` and returns — it does NOT retry indefinitely (that would loop forever around the 03:59 cutoff). The failed batch's symbols stay NaN; we don't retry the batch even after a successful reconnect.
4. **`nan_value` is a parameter of `_run_concurrent`.** Step 2 passes `(nan, None)` (price + qualified contract); Step 3 passes `(nan, nan)` (RTH + ETH ITI). If you add another step, supply your own sentinel.

## Throughput design

- Step 2 (close prices) and Step 3 (trade frequency) have separate concurrency knobs: `--price-concurrency` (default 12) and `--freq-concurrency` (default 4). Step 3 is the per-request bottleneck. Both sit well below IBKR's 50-simultaneous-historical-requests cap and ib_insync's 45 req/s client-side throttle (`MaxRequests=45` in `ib_insync/client.py`). `--freq-concurrency` history: **48 → 24 → 8 → 4**. 48 collapsed instantly (Step 3 went 0/7000). 24 *looked* safe but bled slowly via Gateway-side slot accounting drift — the 2026-06-01 run produced **9% Step 3 success on 7152 symbols, with 10k+ Error 162 ("query cancelled") and 1.4k+ Error 366 ("no historical data query found for ticker id") in the log, zero pacing violations and zero socket disconnects** (so the previous `_run_concurrent` reconnect path never triggered — it's not a connection problem, it's slot exhaustion). 8 paired with `--freq-timeout 30` looked clean on the 2026-06-02 500-symbol test (74% success on valid-contract symbols, 7 Error 366 total) but the cascade crept back at full 7000-symbol scale — the 2026-06-02 cron logged **880 Error 162 + 58 Error 366** with **RTH 43% / ETH 40%**. 4 is the next step per the diagnostic ladder (>~50 Error 366 ⇒ lower concurrency before touching anything else).
- Step 3 reuses the qualified `Stock` contracts produced by Step 2 (`fetch_freq_async(..., contract=...)`) — this halves the per-symbol IBKR round-trips. Symbols where Step 2 couldn't qualify short-circuit to NaN in Step 3 instead of re-trying qualify.
- `--freq-timeout` default is **30 s** (was trimmed to 12 s; reverted 2026-06-02). The 12 s trim assumed "real fetches complete in 5–7 s under load" — that was only true under the old high-concurrency regime, where anything slower was queue-buildup pathology rather than a real fetch worth waiting on. With concurrency=8 the queue is no longer the bottleneck; per-symbol latency on thin micro-caps legitimately spans 15–25 s, and 12 s mass-timed-out real symbols (2026-06-02 head-to-head: 28% success at 12 s vs 74% at 30 s, same concurrency=8 on the same 500 symbols). If you raise `--freq-concurrency` above 8 you can likely drop the timeout again — they trade off.
- `--freq-retries` default is **0** (vs `--retries` = 2 for prices): retrying a freq fetch rarely succeeds because the underlying cause is usually "no TRADES history".
- If `Historical Market Data Service error message:Pacing violation` shows up in the log, lower `--freq-concurrency` further (try 4). (These messages are now captured in `pipeline_daily.log` directly — `_setup_logging` attaches the per-day file handler to the `ib_insync` logger too.)
- **The Error 162 / Error 366 cascade is the signature of Gateway-side historical-data slot exhaustion**, not a connection or pacing problem. The reconnect logic in `_run_concurrent` doesn't help here (the socket stays up). If a run shows the cascade, the diagnostic is: (a) check `grep -c "Error 366"` — anything > ~50 on a full run means slots aren't draining; (b) check `grep -c "Pacing violation"` — should be zero; (c) check Step 3 success rate vs the float-filtered fetch count. If the cascade is back, lower `--freq-concurrency` before touching anything else.
- **Step 3.5 — paced retry of failed Step 3 symbols.** After the first Step 3 pass, `main()` identifies rows where `qualified[i] is not None` but `rth_results[i]` came back NaN (i.e. the symbol *should* have been fetchable), sleeps 90 s to let Gateway slot accounting drain, then re-runs `_run_concurrent` on just that cohort at **half the configured `--freq-concurrency`, capped at 2**. Skipped if (a) > 50% of attempted symbols failed (a paced retry won't help that magnitude — check Gateway state instead), or (b) we're within 20 min of the 03:59 ET Gateway restart. This is qualitatively different from `--freq-retries` (which is still 0): inline per-symbol retries on the same loop fail for the same reason the first attempt did because slot bookkeeping is time-paced. The retry uses the qualified contracts already cached from Step 2 — no re-qualification.

## Logging & `runs/cron.log` rotation

- Per-run log: `runs/{DD-MMM-YYYY}/pipeline_daily.log` (DEBUG level, full history).
- Aggregate stdout/stderr: `runs/cron.log` (written by cron's shell `>> 2>&1`).
- `_rotate_cron_log()` runs **once at the top of `_setup_logging`**. If `cron.log` is ≥ 5 MB it's renamed to `cron.log.1` (and any existing `.1` to `.2`) before the new run starts writing. Single-writer (the shell), race-free. Do NOT add a `RotatingFileHandler` pointing at the same file — that would race with the shell's append.

## Cron entry

```
TZ=America/New_York
30 20 * * * /home/tom/venv/bin/python3 /home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/pipeline_daily.py >> /home/tom/Documents/ibkr_scripts/N2/scripts/universe_finder/runs/cron.log 2>&1
```
