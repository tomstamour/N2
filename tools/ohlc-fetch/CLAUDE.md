# tools/ohlc-fetch

`ohlc_minute_fetch.py` — attach a window of 1-minute IBKR OHLCV bars to every
row of a PR TSV, keyed on that row's `ArrivalDate` + `Created` timestamp.

```bash
/home/tom/venv/bin/python ohlc_minute_fetch.py input_example.tsv \
    --minutes-before 5 --minutes-after 60 --log
```

Output defaults to `<input>_ohlc.tsv`: every input column verbatim, then
`ohlc_anchor_et ohlc_status ohlc_bars_returned`, then five columns per minute
(`m-05_open … m+60_volume`). `m+00` is the minute containing the PR timestamp.

## Facts worth knowing before editing

- **Timestamps are ET.** `Created`/`ArrivalTime` in the pipeline TSVs are
  `America/New_York` wall clock (the 06:00/07:30/08:00 wire slots).
- **Anchor rolling.** A PR before 04:00 ET is clamped to 04:00 (nothing trades
  earlier); one at/after 20:00 ET, or on a holiday/weekend, rolls to 04:00 of
  the next NYSE session (`pandas_market_calendars`, venv only). The anchor
  actually used is written to `ohlc_anchor_et` — always read offsets against
  that column, not against `Created`. `--no-sessions` disables the rolling.
- **Blank ≠ zero.** A minute with no trade gets five empty cells. IB itself
  forward-fills *within* a returned range (repeated price, `volume=0`), so a
  `volume=0` bar came from IB and a blank one did not exist.
- **Empty bar sets are ambiguous and the classification matters.** IB returns
  `[]` both for "this contract has no history" (VYNE in the example file:
  `Error 162 … Unknown contract`) and for the contention failure in
  `hmds-historical-data-timeout` (`162 query cancelled`). `ErrorWatch` reads
  the error text: permanent → row written blank as `nodata`; transient →
  `failure`, row **not** written, retried, and the run aborts after
  `CONSECUTIVE_FAILURE_ABORT` (10) in a row. Getting this backwards silently
  poisons the output with permanent blanks, so extend the pattern lists rather
  than loosening them.
- **Resume is row-count based.** The output is appended and fsynced per row;
  a re-run skips the rows already present. It refuses to append if the header
  differs (changed window) or if the input rows shifted under it. Editing or
  reordering the input mid-run invalidates resume — use `--restart` or a new
  `-o`.
- **`--stop-point yyyy-mm-dd-hh-mm` is a wall-clock ET deadline**, not a row
  filter: the run exits cleanly when that moment arrives (and won't even
  connect if it's already past). Intended for overnight runs that must free
  the Gateway before the open.
- **Port 4002 by default** — bulk 1-min extended-hours queries get dropped on
  the live Gateway. clientId 31 (avoid clerk's 1000–1029, dailyStats' 77).
- **Volume is raw from IB** — share counts on this Gateway (server v176), and
  they can be fractional. `--volume-scale` exists only if that ever changes.

## Verified 2026-07-26

Full 9-row `input_example.tsv` run against paper GW 4002: 8 `ok`, 1 `nodata`
(VYNE, unknown to HMDS), ~2s/row including pacing. Resume, `--stop-point`,
header-mismatch guard and idempotent re-run all exercised.
