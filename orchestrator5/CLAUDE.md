# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`orchestrator5/` holds a standalone process that turns a firehose news stream into (a) a
daily TSV of scored press releases and (b) real-time "arm/sentiment" trade signals sent to
a warm pool of IBKR clients. **`Orchestrator5.2.py` is the current version**; `5.1` and
`5.0` are frozen predecessors kept for reference. 5.2 adds a **second headline model** —
FinMarBa (`../tools/Encoder_Models/FinMarBa_headliner.py`, baptle/FinBERT_market_based) —
running in parallel with FinBERT, and changes the trade gate: the headline leg is now
`FinBERT positive >= 0.6 AND FinMarBa indecisive <= 0.1` (the old `positive >= 0.83`-alone
leg is gone), still OR-ed with the combo leg. (5.1 dropped the 5 `nocoref_*` body scores
5.0 computed and gated on headline FinBERT alone.) It is one node in the larger `scripts/`
monorepo — it imports helpers from many sibling directories but runs as its own OS process.
See the `orchestrator5-2-design` / `orchestrator5-1-design` / `orchestrator5-0-design`
memories for the design lineage (standalone successor of `../orchestrator3/Orchestrator4.2.py`).

## Running

Always use the project venv (system python lacks `ibapi`, `pandas_market_calendars`,
sklearn, etc.):

```bash
/home/tom/venv/bin/python Orchestrator5.2.py
```

Startup pre-warms **both** headline models (FinBERT + FinMarBa) and the keyword/combo
scorer, then hard-fails (RuntimeError) if the frozen combo-weights JSON didn't load — a
trading process must not run with a silently-missing score. Verify the console shows
`FinBERT-headliner model ready.`, `FinMarBa-headliner model ready.`, and
`KeywordScorer: ... | prevalence=0.185 | pipeline=yes | combo=yes`.

O5 is a **client of two other standalone processes** that must be started separately (any
order — O5 retries until they appear):

1. **RTPR_connector3** (the news source) — publishes every accepted firehose article as one
   JSON line on loopback TCP `127.0.0.1:8900`:
   ```bash
   cd ../RTPR_connector3 && /home/tom/venv/bin/python RTPR_connector3_Firehose.py --flush-on-shutdown
   ```
2. **clerk-1.1.py** (`../x-wing-mole/`) — the warm ibapi client pool O5 arms over TCP/JSON
   `127.0.0.1:8765`. Start it **first** if you want arms to land:
   ```bash
   /home/tom/venv/bin/python clerk-1.1.py --client-qty 5 --port 4002 --listen-port 8765
   ```
   `--port 4001` is the **LIVE** Gateway (real money); `4002` is the paper GW for testing.

**Run exactly ONE orchestrator instance — never two of 5.0 / 5.1 / 5.2 together** (both
would arm the clerk for the same PR). Read-only watchers (`nc 127.0.0.1 8900`) are fine.
There are no tests, build, or lint steps; verify by running and inspecting `logs/` +
`tables/`.

## Data flow (single article)

Feed line (`_run_feed_client` → `_handle_feed_line`) → `_on_row_built` (runs on the **feed
reader / main thread — keep it lean**, only dict/string filters + non-blocking executor
submits) → per-ticker `_collect_and_log` on a worker thread.

`_on_row_built` order is load-bearing:
1. **Dedup** — per-day article-ID dedup *and* content dedup `(normalized headline, sorted
   tickers)`, because RTPR re-publishes the same PR under a **new ID** (PRZO, FOFO). Both
   stashes are single-thread → lock-free, FIFO-capped, cleared when the arrival day rolls.
2. **Two kept NW4 input filters**: ticker count ≤ `MAX_TICKERS_PER_ARTICLE` (2) drops the
   article; a headline hit in `../newswatcher3/excluded_strings.txt` drops it entirely.
3. **Arm-block gate** (`excluded_strings-2.txt`, local): a hit still writes the TSV row but
   does **not** arm the clerk.
4. **Arm** every in-universe ticker immediately (STEP 1a), submit the shared
   FinBERT-headline + **FinMarBa-headline** + keyword/combo futures (all three once per
   ARTICLE — the scores are text-only and identical for both tickers of a 2-ticker PR; the
   two headline models go to separate pools so they run at once), fan out one
   `_collect_and_log` per in-universe ticker.

`_collect_and_log` resolves headline FinBERT and FinMarBa (submitted together, so they
overlap), then the keyword/combo future (`KW_SCORE_WAIT_SEC` = 10 s ceiling so a hung
scorer can't stall the trade), evaluates the OR gate, **waits on the arm future before
sending STEP-1b sentiment** (the clerk must have registered the session first), then writes
the TSV row.

## Thread pools (parallelism knobs, top of file)

- `_finbert_executor` (4) / `_finmarba_executor` (4) — the two headline models, each in its
  OWN pool so they run simultaneously (both ONNX INT8 → `session.run()` releases the GIL).
  On this 8-thread box each ~8 ms inference already spreads across all cores via ORT's
  default intra-op pool, so two concurrent inferences mostly time-share the CPU (measured
  ~no wall-clock overlap, pair ≈ 20 ms); the win is that both start at reception and
  overlap the (much longer) clerk-arm round-trip and kw scoring, not each other. Capping
  each session's `intra_op_num_threads` to 1–2 would let them genuinely overlap (~14 ms
  pair, ~1.5×) but diverges from the frozen headliner load path for a ~6 ms gain — not done.
- `_kw_executor` (1) — keyword/combo scorer, ~4 ms/PR so one worker absorbs the worst
  observed burst; single worker also sidesteps any sklearn thread-safety question.
- `_clerk_executor` (6) — dedicated so arming is never starved by collect tasks.
- `_collect_executor` (8).

## Clerk handshake (TCP/JSON to clerk-1.1.py)

- STEP 1a **ARM** on reception: `{ticker, lastDailyClose, itiBaseline, tradeSizeBaseline}`.
- STEP 1b **SENTIMENT** after scoring: `{ticker, Sentiment: "OK"|"BAD"}` — the wire protocol
  is OK/BAD only; the trigger source lives in the TSV.
- **Gate (`_evaluate_trigger`) is an OR of two legs** (5.2): the **headline leg** is
  FinBERT `positive >= SENTIMENT_POSITIVE_MIN` (0.6) **AND** FinMarBa
  `indecisive <= FMB_INDECISIVE_MAX` (0.1); the **keyword leg** is body combo
  `model_proba_PLUS_kw_mult_nb >= COMBO_TRIGGER_MIN` (0.6). Either leg fires. A `None` score
  (model error / scorer timeout) makes only its own condition false — a missing `positive`
  OR `indecisive` kills the headline leg, a missing combo kills the keyword leg; both legs
  false → BAD. No vetoes (pure OR; headline leg tightened from the 5.1 `positive>=0.83`-alone
  rule on 2026-07-20).
- The TSV `ClerkSentiment` cell records trigger + clerk reply:
  `Keyword_FinBERT_FMB:sent` / `FinBERT_FMB:sent` / `Keyword:no_reply` / `BAD:sent`, or
  `skipped:arm_rejected` / `skipped:excluded_string` / `skipped:not_armed`.
- `_send_clerk` never raises — socket/parse errors log and return `{}` so the news
  pipeline keeps running.

## Cross-repo imports (why the file loads modules by path)

Sibling scripts have hyphens in filenames (`FinBERT-headliner.py`,
`keyword_scorer-2.py`) or aren't importable packages, so O5 uses
`importlib.util.spec_from_file_location` and manipulates `sys.path`.
Key gotcha: `RTPR_connector3_Firehose` **must be registered in `sys.modules` before
`exec_module`** — its frozen dataclass resolves string annotations via
`sys.modules[cls.__module__]`. The connector is imported for **shared helpers only** (its
`main()` is `__main__`-guarded and never runs here): `FIXED_TZ`, `now_fixed`,
`format_millis_time/date`, `clean_inline_text`, and the daily fixed-UTC-4 log handler — so
both processes agree byte-for-byte on clocks and formatting.

The keyword/combo scorer is `../tools/keyword/keyword_scorer-2.py` —
`get_default_scorer()` wires the frozen ge15 artifacts, pinned prevalence 0.185 and the
frozen combo JSON (in `../price_stats_loader/outputs/`). **Never import the old
`keyword_scorer.py`** (wrong ge20 defaults) and **never move/regenerate the model
artifacts** — everything is frozen so live == backtest.

The second headline model is `../tools/Encoder_Models/FinMarBa_headliner.py`, loaded by
path under a **distinct** module name so its `load_model` / `_inferencer` singleton never
collides with FinBERT's (identical script skeleton, different module). O5.2 consumes
`score_headline(...)['indecisive']` for the gate and stores the whole result in the `FMB_*`
TSV cols. Its `finmarba_onnx/` build artifact is gitignored — rebuild with
`FinMarBa_headliner.py --export` (then `verify_headliners.py --model FinMarBa`) on a fresh
clone; it was already exported here 2026-07-18.

Per-user config (RTPR key, clerk/feed host+port) lives in `../config/n2_config_file.txt`,
loaded via `../config/n2_config.py`. Paths anchor off `SCRIPTS_DIR` (`__file__.parent.parent`) —
never hard-code `/home/...`.

## Universe TSV (the baseline source)

Baselines and membership come from the newest `../universe_finder/data/stocks_universe_YYYY-MM-DD.tsv`
(loaded into `_iti_df`, reloaded daily at 03:00 ET by `_iti_reload_worker`, which also
rebuilds `_inscope_set` — the arm + TSV fan-out gate). Quirks to respect:
- ~13% of cells are the literal `'skipped_nan'` or blank → always go through `_coerce_float`.
- ITI/trade-size columns are picked by ET clock (RTH 09:30–16:00 vs ETH).
- **Inconsistent header casing**: `RTH_tradeSize` (lowercase t) vs `ETH_TradeSize` (uppercase T).
- Missing trade size → `TRADE_SIZE_SENTINEL` (44444, "no baseline" to trade-mole); missing
  ITI → `DEFAULT_BASELINE_ITI` (44444).

## Output

Daily `tables/news_output_YYYY-MM-DD.tsv`, one row per in-universe ticker, appended
thread-safely (`_tsv_lock`). Column order is fixed in `_TSV_COLUMNS` (30 columns since 5.2:
the 24 of 5.1 plus a FinMarBa block after the FinBERT `label` col — `FMB_time`,
`FMB_positive`, `FMB_indecisive`, `FMB_negative`, `FMB_sentiment_score`, `FMB_label`; only
`FMB_indecisive` feeds the gate, the rest are for offline re-analysis).
`model_proba_PLUS_kw_mult_nb` keeps its exact name — downstream tooling matches on it. On a
header mismatch the old file is rotated to `*_rotated.tsv` (so switching 5.0/5.1/5.2 mid-day
is safe by design). The `Body` cell stores the **RAW** payload one-lined (`clean_inline_text`
on `body_raw`) — this is **exactly the string `_score_keywords` scores**, so an offline
rescore of the TSV (`keyword_scorer-2.py --input <tsv> --text-col Body`) must reproduce the
live combo at 6 decimals (verified 2026-07-15). All timestamps use the connector's fixed
UTC-4 clock, never `datetime.now()` (OrchTime is still stamped, log-only). Logs:
`logs/Orchestrator5.2_YYYY_MM_DD.log`.

## Editing notes

- Keep `_on_row_built` non-blocking — heavy work belongs in the pools.
- Preserve the arm→sentiment ordering in `_collect_and_log`, and keep the kw-future resolve
  before the arm wait (it adds only ms; the arm reply wait dominates).
- Score once per ARTICLE, not per fan-out ticker, and always score the Body-cell string —
  scoring anything else silently breaks the live == backtest invariant.
- Keep the two headline models on separate pools and submit both in `_on_row_built` (not
  inline in `_collect_and_log`) so they start at reception and run concurrently; resolve
  both before the gate. FinBERT `positive` and FinMarBa `indecisive` are the headline-leg
  inputs — don't swap which model supplies which.
- Both dedup stashes assume a single writer thread (`_on_row_built`); don't call them from a
  pool worker.
- Articles that arrive while O5 is disconnected are **dropped by design** (no replay); the
  connector's own `PR_dataframe` table still records them, and gaps are logged.
