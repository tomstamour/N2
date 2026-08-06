# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`orchestrator5/` holds a standalone process that turns a firehose news stream into (a) a
daily TSV of scored press releases and (b) real-time "arm/sentiment" trade signals sent to
a warm pool of IBKR clients. **`Orchestrator5.2.py` is the current version**; `5.1` and
`5.0` are frozen predecessors kept for reference. 5.2 has been extended repeatedly in
place, so read the dated notes in `version-control.txt` before comparing TSVs across days:
- **2026-07-20** — added FinMarBa as a second headline model; the headline leg became
  `FinBERT positive >= 0.6 AND FinMarBa indecisive <= 0.13`.
- **2026-07-28** — the keyword leg was REPLACED: the old `model_proba_PLUS_kw_mult_nb`
  combo is gone, now `kw15_body_proba >= 0.60 AND kw15_head_proba >= 0.65` from two
  corrected-label ge15 tfidf pipelines.
- **2026-07-29** — added FLANG-BERT (`../tools/Encoder_Models/FLANG-BERT_headliner.py`,
  avinasht/FLANG-BERT_flang-bert) as a **third headline model** running in parallel with
  the other two, and a **standalone third trade leg**: `FLANG sentiment_score >= 0.97`
  fires the trade on its own, OR-ed with the headline and keyword legs.
- **2026-08-01** — ⚠ **the gate became an AND** and all five thresholds were retuned:
  `FinBERT positive >= 0.8`, `FMB indecisive <= 0.14`, `kw15_body >= 0.50`,
  `kw15_head >= 0.50`, `FLANG sentiment_score >= 0.96`, **every one required**. This
  un-does the "any one leg fires" rule of the two entries above — FLANG is no longer
  standalone — and cuts the fire rate from ~25% of rows to ~2.5% (~1 trade/day) for a
  precision gain (32.6% → 46.2% at the ≥30% target). No TSV schema change (still 39 cols),
  so **the header does not tell you which gate wrote a row** — use the date, or read the
  per-row `Trigger` cell, which embeds the thresholds in force.
- **2026-08-02** — ⚠ **an EARNINGS branch was OR-ed beside that AND**, adding
  `../tools/categorizer/PrClassifier.py` (catalyst class on every PR) and
  `../tools/Earning_Scorer/earning_scorer.py`. The five-way AND is **untouched and still a
  complete branch**; a row can now ALSO fire on
  `FineClass == Earnings_Results AND EarningScore >= 4 AND kw15_body >= 0.50`.
  TSV 39 → **42 cols** (`FineClass`, `EarningScore`, `EarnBreakdown`, appended last), so
  from this date the header identifies the build again.
- **2026-08-03** — ⚠ **three SOLO branches OR-ed beside those two**, each a single
  condition firing unaided: `kw15_body_proba >= 0.70` (`KwBodySolo`), `FLANG
  sentiment_score >= 0.975` (`FLANGSolo`) and `FinBERT positive >= 0.935` (`FinBERTSolo`).
  Purely additive — the AND and the earnings leg are untouched and every row that fired
  before still fires. ⚠ **These are the only thresholds in the file with no corpus
  calibration behind them** (operator-specified). They **subsume** the branches they sit
  beside, re-reading three of the same five scores at strictly tighter cuts, so the AND and
  the earnings leg are only *decisive* below every solo cut — FMB `indecisive` and
  `kw15_head` are the only core conditions left that can veto. Live-tape replay (209 rows,
  07-29..08-03): fires **7 → 31, 3.4% → 14.8%, ~4.4×**. **No schema change (still 42
  cols)** — the `Trigger` cell's **token count (7 = 08-02, 10 = now)** identifies the build.
  ⚠ A **fourth** solo leg (`FMB indecisive <= 0.115`) was written and **removed the same
  day, before it ever ran** — it is direction-agnostic. See the gate section.

(5.1 dropped the 5 `nocoref_*` body scores 5.0 computed and gated on headline FinBERT
alone.) It is one node in the larger `scripts/` monorepo — it imports helpers from many
sibling directories but runs as its own OS process. See the `orchestrator5-2-design` /
`orchestrator5-1-design` / `orchestrator5-0-design` memories for the design lineage
(standalone successor of `../orchestrator3/Orchestrator4.2.py`).

## Running

Always use the project venv (system python lacks `ibapi`, `pandas_market_calendars`,
sklearn, etc.):

```bash
/home/tom/venv/bin/python Orchestrator5.2.py
```

Startup pre-warms **all three** headline models (FinBERT + FinMarBa + FLANG-BERT), both
ge15 keyword pipelines and the PR classifier + earnings scorer, then hard-fails
(RuntimeError) if anything is missing or returns the wrong shape — a trading process must
not run with a permanently-None leg, which would silently remove part of the gate. Verify
the console shows all **five** ready lines: `FinBERT-headliner model ready.`,
`FinMarBa-headliner model ready.`, `FLANG-BERT-headliner model ready.`,
`ge15 keyword pipelines ready (smoke: body=… head=…)`, and
`PR classifier + earnings scorer ready (smoke: class=Earnings_Results score=4 …)`.
Startup then echoes the whole gate once — `[Gate] ACTIVE — OR of 5 branches: …` with every
threshold in force. **That line is the only way to tell which gate a running process holds**:
the constants are read at import, so a process started before a retune keeps the old gate
until restarted, and since 2026-08-02 gate changes no longer change the TSV header.

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

**Run exactly ONE orchestrator instance — never two of 5.0 / 5.1 / 5.2 together**
(both would arm the clerk for the same PR). Read-only watchers (`nc 127.0.0.1 8900`) are fine.
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
   FinBERT-headline + **FinMarBa-headline** + **FLANG-headline** + keyword + **PR-class /
   earnings** futures (all five once per ARTICLE — the scores are text-only and identical
   for both tickers of a 2-ticker PR; the three headline models go to separate pools so
   they run at once), fan out one `_collect_and_log` per in-universe ticker.
   - The earnings scorer is the one that also takes a **symbol** — `primary_symbol`, the
     article's first in-universe ticker — because its two size-relative point rules divide
     by float-cap. It is still scored **once per article**, so the second ticker of a
     2-ticker PR inherits the first's denominator (deliberate; such PRs are rare). The
     universe lookup happens on the pool thread, never in `_on_row_built`.

`_collect_and_log` resolves headline FinBERT, FinMarBa and FLANG (submitted together, so
they overlap; FLANG has a `FLANG_SCORE_WAIT_SEC` = 10 s ceiling), then the keyword future
(`KW_SCORE_WAIT_SEC` = 10 s) and the earnings future (`EARN_SCORE_WAIT_SEC` = 10 s) — each
capped so a hung scorer can't stall the trade — evaluates the gate, **waits on the arm
future before sending STEP-1b sentiment** (the clerk must have registered the session
first), then writes the TSV row.

## Thread pools (parallelism knobs, top of file)

- `_finbert_executor` (4) / `_finmarba_executor` (4) / `_flang_executor` (4) — the three
  headline models, each in its OWN pool so they run simultaneously (all ONNX INT8 →
  `session.run()` releases the GIL).
  On this 8-thread box each ~8 ms inference already spreads across all cores via ORT's
  default intra-op pool, so concurrent inferences mostly time-share the CPU (measured
  ~no wall-clock overlap, pair ≈ 20 ms, expect ≈ 30 ms for the trio); the win is that all
  three start at reception and overlap the (much longer) clerk-arm round-trip and kw
  scoring, not each other. Capping each session's `intra_op_num_threads` to 1–2 would let
  them genuinely overlap but diverges from the frozen headliner load path for a few ms —
  not done.
- `_kw_executor` (1) — ge15 keyword scorer (two tfidf `predict_proba` calls), a few ms/PR so one worker absorbs the worst
  observed burst; single worker also sidesteps any sklearn thread-safety question.
- `_earn_executor` (2) — PrClassifier + earning_scorer. Pure-Python regex, so it **holds
  the GIL** and cannot truly overlap the other pools; 2 workers is not a throughput knob,
  it just stops one long body head-of-line-blocking the next PR. Cheap in practice because
  the scorer is skipped entirely on non-`Earnings_Results` PRs (~85% of rows).
- `_clerk_executor` (6) — dedicated so arming is never starved by collect tasks.
- `_collect_executor` (8).

## Clerk handshake (TCP/JSON to clerk-1.1.py)

- STEP 1a **ARM** on reception: `{ticker, lastDailyClose, itiBaseline, tradeSizeBaseline}`.
- STEP 1b **SENTIMENT** after scoring: `{ticker, Sentiment: "OK"|"BAD"}` — the wire protocol
  is OK/BAD only; the trigger source lives in the TSV.
- **Gate (`_evaluate_trigger`) is an OR of FIVE branches** since 2026-08-03:
  `is_ok = core_and or earnings_leg or kwbody_solo_leg or flang_solo_leg or
  finbert_solo_leg`.
  - **CORE branch** — the 2026-08-01 five-way AND, **every one must hold**, unchanged:
    FinBERT `positive >= SENTIMENT_POSITIVE_MIN` (0.8), FinMarBa
    `indecisive <= FMB_INDECISIVE_MAX` (0.14), `kw15_body_proba >= KW15_BODY_MIN` (0.50),
    `kw15_head_proba >= KW15_HEAD_MIN` (0.50), FLANG-BERT headline
    `sentiment_score >= FLANG_SCORE_MIN` (0.96). Calibrated on
    `price_stats_loader/outputs/06_PRstats_2026-06-22-2026-07-30_11EncBodyHeadScored_kwScored_FinBERT.tsv`
    (1055 labelled rows): fires **26×, 2.5%** at **46.2% precision / 2.69× lift** on the
    ≥30% target (80.8% / 2.51× on ≥15%). Recall 6.6% — a high hit rate on few shots,
    ~1 trade/day, **deliberately**. ⚠ n=26 ⇒ ±19pp CI and post-selection.
  - **EARNINGS branch** (2026-08-02) — also all-required: `FineClass == EARN_CLASS`
    (`Earnings_Results`), `EarningScore >= EARN_SCORE_MIN` (4), and
    `kw15_body_proba >= KW15_BODY_MIN` — **the same `c_body` local**, one condition shared
    by both branches. FinBERT / FMB / FLANG / kw15_head are **not** consulted.
    - It exists because the core AND is **structurally blind to earnings PRs**: its own
      FinBERT headline score measured test AUC **0.297** there (*anti*-predictive) —
      winners and duds share the headline ("X Reports Q1 2026 Financial Results"), the
      numbers are in the body. See `../tools/Earning_Scorer/Earnings_text_structures_report.md`.
    - Calibrated 2026-08-02 on `../tools/Earning_Scorer/PRstats_2026-05-04-2026-07-30_EarningResults_earnScored.tsv`
      (518 labelled rows, base 7.1%) with kw15_body recomputed by the frozen ge15 pipeline:
      the core AND fires **3× at 0.0%** precision on those rows; this leg fires **36× at
      47.2% / 6.6× lift** (test block ≥2026-06-20: 7 fires, 42.9%, 3.1×). **Overlap is
      exactly zero** — purely additive, ~0.71 extra trades/day at the same precision bar.
    - Cut choice: precision is FLAT below 4 (≥1 .321, ≥2 .314, ≥3 .339) and steps up at 4
      (.472). ⚠ n=36 ⇒ wide error bars, and `EarningScore` is a **ranking cut, not a
      calibrated probability** — the ≥30% base rate itself moved 3.7% (May) → ~15-20%
      (Jun/Jul).
    - ⚠ **`EarningScore` is INTEGER POINTS** (observed −7…+9, median 0), never 0–1. A
      threshold like `0.4` would silently mean `>= 1` (36.5% of earnings PRs, 32%
      precision). The points come from `earn_score_params.json`, **frozen** — calibrated
      2026-08-01; changing it changes what `>= 4` means.
  - **THREE SOLO branches** (2026-08-03) — **one condition each**, firing unaided, nothing
    else consulted: `KwBodySolo` `kw15_body_proba >= KW15_BODY_SOLO_MIN` (0.70),
    `FLANGSolo` `FLANG sentiment_score >= FLANG_SOLO_MIN` (0.975), `FinBERTSolo`
    `positive >= SENTIMENT_POSITIVE_SOLO_MIN` (0.935). All three are floors.
    - ⚠ **Uncalibrated.** Alone among every threshold in this file these three were
      operator-specified (2026-08-03) with **no fires/precision/lift measurement** behind
      them. The nearest existing evidence, none at these cuts: FLANG standalone at 0.97
      ran 46.1% @≥15% / 24.3% @≥30% on 3434 rows; the ge15 **body channel alone** at 0.60
      ran ~40.9% vs the 57.3% the body+head *pair* bought. Both point **below** the AND
      branch's 46.2% @≥30%. Accepted trade: more trades, lower hit rate.
    - ⚠ They **subsume** the branches they sit beside: each re-reads a core score at a
      **strictly tighter** cut (0.70>0.50, 0.975>0.96, 0.935>0.8), hence each `c_*_solo`
      **implies its core twin**. The core AND and the earnings leg are only *decisive* in
      the band below every solo cut: `kw15_body` [0.50,0.70), FLANG [0.96,0.975),
      `positive` [0.8,0.935). **FMB `indecisive` and `kw15_head` are the only core
      conditions with no solo twin** — the only ones that can still veto. **The 08-01 and
      08-02 calibrations describe branches, not the gate**; no measurement of the combined
      gate exists.
    - ⚠⚠ **Do not add an `indecisive <= x` solo leg.** One (`FMBSolo`, 0.115) was
      implemented on 2026-08-03 and **removed the same day, before it ever ran live**,
      because it is **direction-agnostic**: FinMarBa's classes are negative/indecisive/
      positive, so a low `indecisive` means *confident in either direction*. Inside the core
      AND `positive >= 0.8` pins the sign alongside it; standalone nothing does. Not
      hypothetical — on 2026-07-29 it fired `WETO` *"Announces Share Consolidation"* (a
      **reverse split**) on `indecisive=0.0961` / `FMB_label=positive`, while **both** other
      headline models disagreed (FinBERT `positive` 0.150, FLANG `sentiment_score`
      **−0.8037**). It was also the loosest cut measured — 44/552 rows (**8.0%**) vs
      FLANGSolo 2.9%, KwBodySolo 2.4%, FinBERTSolo 1.3% — roughly doubling the gate alone.
      If revisited it needs a **direction condition** beside it (e.g. also `FLANG > 0`). A
      standing warning sits in the `SOLO LEGS` constant block.
    - Live-tape replay (`tables/news_output_2026-07-29..08-03`, 209 rows, current constants
      on both tiers): **7 (08-02 gate) → 31**, i.e. **3.35% → 14.83%, ~4.4×** the 08-02
      build (+11 KwBodySolo, +10 FLANGSolo, +2 FinBERTSolo, +1 both kw/FLANG). A trade-rate
      observation — those rows carry no label, so it is **not** a precision claim.
  - A `None` score still makes only its **own** condition false. `_evaluate_trigger`
    returns **`missing_blocked`** — true when the row did NOT fire and a `None` was the
    *only* thing in the way: every evaluable **core** condition passed, **or** a
    known-`Earnings_Results` row with `kw15_body` OK has a `None` `EarningScore`, **or**
    (since 2026-08-03) **any of** `kw15_body` / FLANG / `positive` is `None` **at all** — a
    solo branch is one condition, so a missing score is the whole of what blocked it.
    `indecisive` is deliberately **not** in that term: it backs no solo branch. `_collect_and_log` logs
    it at **WARNING** (`BLOCKED BY MISSING SCORE ONLY`). That third term is deliberately
    much **louder**: a dead kw, FLANG or FinBERT scorer now warns on *every* row. Each OR-ed branch
    makes this alarm matter more, not less — a dead model no longer halts trading, so the
    tape looks even less obviously broken.
- The TSV `ClerkSentiment` cell records trigger + clerk reply. The trigger label is
  **built** by joining the fired leg names with `_` in the fixed order `Keyword`,
  `FinBERT_FMB`, `FLANG`, `Earnings`, `KwBodySolo`, `FLANGSolo`, `FinBERTSolo`, gated on **`is_ok`, not on a non-empty `fired`** — a row can still have a leg true and not
  fire (the solo legs narrowed that window but did not close it), and that guard is what
  stops such a row being labelled after a leg the gate rejected. Degenerate for exactly one
  day under the pure AND, the vocabulary is now **wide** — e.g. `KwBodySolo:sent`,
  `FLANG_FLANGSolo:sent`, `FinBERTSolo:sent`, `Keyword_KwBodySolo:sent`, `Earnings:sent`, `Keyword_FinBERT_FMB_FLANG:sent`, `BAD:sent`, plus
  `skipped:arm_rejected` / `skipped:excluded_string` / `skipped:not_armed`. Because every
  solo cut is strictly tighter than its core twin, each solo name **implies** its twin's
  condition: `KwBodySolo` ⇒ `c_body`, `FinBERTSolo` ⇒ `c_fin` (hence `FinBERT_FMB`
  whenever FMB agrees), and **`FLANGSolo` can never appear without `FLANG`**.
  `Trigger` remains the cell that says *why*.
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

The keyword leg (**replaced 2026-07-28** — the `get_default_scorer()` /
`model_proba_PLUS_kw_mult_nb` combo path is gone) loads two corrected-label ge15 tfidf
pipelines directly with `joblib` from
`../tools/keyword/model_trained_on_04-PRstats-2026-05-04to06-18_TRAIN/`
(`classify_fromPRhigh_ge15` for the body, `..._ge15_headline` for the headline).
`keyword_scorer-2.py` is still imported, but only for `_strip_html` and to put
`keyword_finder` on `sys.path` for `strip_wire_boilerplate`; `_kw15_prep` reproduces the
exact training-time text prep. **Never import the old `keyword_scorer.py`** (wrong ge20
defaults) and **never move/regenerate the model artifacts** — everything is frozen so
live == backtest.

The second headline model is `../tools/Encoder_Models/FinMarBa_headliner.py`, loaded by
path under a **distinct** module name so its `load_model` / `_inferencer` singleton never
collides with FinBERT's (identical script skeleton, different module). O5 consumes
`score_headline(...)['indecisive']` for the gate and stores the whole result in the `FMB_*`
TSV cols. Its `finmarba_onnx/` build artifact is gitignored — rebuild with
`FinMarBa_headliner.py --export` (then `verify_headliners.py --model FinMarBa`) on a fresh
clone; it was already exported here 2026-07-18.

The third headline model (added 2026-07-29) is `../tools/Encoder_Models/FLANG-BERT_headliner.py`, loaded
the same way under module name `FLANG_BERT_headliner`. **Two name collisions make the
distinct module name and aliases mandatory**: its public fn is `analyze_headline` — the same
name already bound to FinBERT — and its loader is `load_model` like both others; O5 binds
them as `score_flang_headline` / `load_flang_model`. Its timing key is **`inference_ms`**,
not FinMarBa's `elapsed_ms`. O5 consumes `analyze_headline(...)['sentiment_score']`
(= positive − negative) as one of the five AND-ed gate conditions (it was a standalone leg
until 2026-08-01) and stores the whole result in the
`FLANG_*` TSV cols. **No body variant and no new script was written**: this is the exact
module that produced the offline `FLANG-BERT_sentiment_score` column via
`HeadScore11encoders.py`, which is what makes live == backtest true by construction — so do
not edit it or its `flangbert_sentiment_onnx/` artifacts (gitignored; rebuild with
`FLANG-BERT_headliner.py --export`, verify with `verify_headliners.py --model FLANG-BERT`,
which must report `max_drift 0.0000`; exported here 2026-07-18).

The earnings leg (added 2026-08-02) loads **two** more modules the same way, purely for
namespace hygiene — neither filename has a hyphen, but neither directory is a package and
`earning_scorer` is generic enough that putting its dir on `sys.path` invites a collision:
- `../tools/categorizer/PrClassifier.py` → `classify_pr` (its `classify`) and
  `PRCLASS_BODY_LEAD_CHARS`. `_classify_and_score_earnings` reproduces its `classify_row`
  fallback by hand (headline first, then the **body lead only** — boilerplate deeper in the
  text is full of false triggers) because `news_dict`'s body key is `body_raw`, not
  `Body`/`body`.
- `../tools/Earning_Scorer/earning_scorer.py` → `earn_extract_metrics` /
  `earn_score_metrics` / `earn_load_params`. **Always pass `params=_earn_params`**:
  `score_metrics(feats)` with no params calls `load_params()` internally, which
  `os.path.exists` + `json.load`s the file on **every** call — a disk read per PR on the
  trade-critical path. `_earn_params` is loaded once in `main()`.
- Both are import-safe and thread-safe (module level is stdlib imports + literals +
  `re.compile`; `argparse`/`pandas` only inside their `__main__`-guarded `main()`; no
  mutable globals). Neither needs the `sys.modules` pre-registration dance.
- `extract_metrics` returns `_body_hits` / `_head_hits` holding **every matched sentence
  verbatim** — pop both before anything can log or serialize the dict.
- Its `mktcap` argument is really **float-cap in dollars** (`Float(M) × LastDailyClose ×
  1e6`, falling back to `MktCap($M) × 1e6`), and it moves the score by up to 2 points.

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
thread-safely (`_tsv_lock`). Column order is fixed in `_TSV_COLUMNS`. **The schema changed
twice in the same week — the header tells you which build wrote a file:**
- **24 cols** = 5.1 · **30** = 5.2 from 2026-07-20 (adds the FinMarBa block after the
  FinBERT `label` col) · **31** = from 2026-07-28 (`model_proba_PLUS_kw_mult_nb` replaced
  by `kw15_body_proba` + `kw15_head_proba`) · **38** = from 2026-07-29 (adds the FLANG
  block after `FMB_label`: `FLANG_time`, `FLANG_positive`, `FLANG_neutral`,
  `FLANG_negative`, `FLANG_sentiment_score`, `FLANG_label`, `FLANG_ms`) · **39** = from
  2026-07-29 (adds `Trigger` as the **last** col — the 38-col build only ever ran in
  testing, so in practice a live file is 31 or 39) · **42** = from 2026-08-02 (adds
  `FineClass`, `EarningScore`, `EarnBreakdown` last). ⚠ **Neither 39 nor 42 identifies a
  gate.** 39 spans the 07-29 OR and the 08-01 AND; 42 spans the 08-02 two-branch OR and the
  08-03 four-branch OR. Use the date, or the `Trigger` cell — which since 08-03 also
  separates them by **token count (7 vs 9)**.

`FineClass` is written on **every** row; `EarningScore` and `EarnBreakdown` only on
`Earnings_Results` rows (the scorer is skipped otherwise) — **a blank there means "not an
earnings PR", not "scored 0"**. `EarnBreakdown` names the rule families that fired with
their values (`revG:+3(182%) profG:+2(829%) flip:+2`), which is what makes a score
auditable without the 25 `earn_*` feature columns.
⚠ **Reproducing `EarningScore` offline requires the row's own `Float`/`LastDailyClose`**,
not today's universe TSV — the two size-relative rules divide by float-cap, and the
universe file changes daily (10 of 45 rows drifted on a 2026-07-31 replay for that reason
alone). For a 2-ticker PR the score used the **primary** (first in-universe) ticker's
float-cap, so the partner's row cannot reproduce it from its own cells — use `Tickers`.

`Trigger` is the gate's own account of itself, built by `_evaluate_trigger` from the same
booleans the trade fires on (so it cannot disagree with the gate). **Ten tokens** since
2026-08-03:
`<YES|NO> | FinBERT:<v> FMB:<v> kw15_body:<v> kw15_head:<v> FLANG:<v> earn:<v> class:<c>`
` kw15_body_solo:<v> FLANG_solo:<v> FinBERT_solo:<v>`.
It has only ever grown by **appending** — the five core tokens keep their position and
order, `earn`/`class` were appended 2026-08-02 and the three solo tokens 2026-08-03 — so a
parser written for the 5- or 7-token format still works, and **the token count is the only
thing that identifies the build** (7 = 08-02, 10 = now), since the solo branches added no
columns. The three solo tokens deliberately **reprint** the `kw15_body` / FLANG / FinBERT
scores against their own cuts, so a row records *both* thresholds it was judged against and
stays self-describing across a retune of either. All three are floors, so `FMB` remains the
only `<=` token. `<v>` is
`PASS(<score><op><thr>)`, `FAIL(<score><op><thr>)` or a bare `MISSING` (score was `None` —
model error or future timeout, otherwise indistinguishable from a low score). The printed
operator is always the relation that is TRUE of the row: `>=`/`<=` on a pass, `<`/`>` on a
fail; `FMB` is the only `<=` condition (`indecisive` is a ceiling). Scores are
display-rounded to 4 dp — the PASS/FAIL token, not the number, is authoritative.
Thresholds render at 2 dp when exact, else `%g`, so a retuned `0.625` never prints as
`0.62`.
`earn` is the odd one out twice over: it prints **integer points** (`earn:PASS(5>=4)`, via
`_cond_token`'s `val_fmt`/`thr_fmt`), and it has a **fourth verdict** — `earn:NA` when the
class is known and simply isn't `Earnings_Results`, i.e. the scorer was deliberately
skipped. That keeps `MISSING` meaning what it means everywhere else; without it ~85% of
rows would read `earn:MISSING` and drown out the one row where the scorer actually died.
`class:<FineClass>` is categorical, so it gets a plain token rather than a PASS/FAIL one.
`Trigger` is **not** a duplicate of `ClerkSentiment`: it is written on **every** row (the
gate runs before the arm branch), where `ClerkSentiment` is overwritten with `skipped:*`
when the ticker was never armed, and it resolves the gate into its underlying models — so
it shows *which* condition failed and by how much. It also embeds the thresholds in force,
which is what makes a row self-describing across a retune: two rows in the same daily TSV,
written either side of a restart, carry their own cuts.
The `[Gate]` log line for a rejected row is this exact same string; a rejected row where a
`MISSING` score was the **only** blocker additionally logs at WARNING.

Only `FLANG_sentiment_score` and `FMB_indecisive` feed the gate from their blocks; the rest
are for offline re-analysis. The `FLANG_*` cols map 1:1 onto the offline `FLANG-BERT_*` cols
from `HeadScore11encoders.py`; `FLANG_time` is a completion **timestamp** (`FMB_time`
convention) while `FLANG_ms` is the model's own inference **duration**. On a
header mismatch the old file is rotated to `*_rotated.tsv` (so switching builds
mid-day is safe by design — but note the rename **overwrites** any existing `_rotated.tsv`
for that day, so a second schema switch the same day destroys the first one). The `Body`
cell stores the **RAW** payload one-lined (`clean_inline_text`
on `body_raw`) — this is **exactly the string `_score_keywords` scores**, so an offline
rescore of the TSV must reproduce the live
kw15 probabilities. All timestamps use the connector's fixed
UTC-4 clock, never `datetime.now()` (OrchTime is still stamped, log-only). Logs:
`logs/Orchestrator5.2_YYYY_MM_DD.log`.

## Editing notes

- Keep `_on_row_built` non-blocking — heavy work belongs in the pools.
- Preserve the arm→sentiment ordering in `_collect_and_log`, and keep the kw-future resolve
  before the arm wait (it adds only ms; the arm reply wait dominates).
- Score once per ARTICLE, not per fan-out ticker; the keyword scorer **and the earnings
  scorer** must always get the Body-cell string (`clean_inline_text(body_raw)`) and the
  three headline models must always get `news_dict['Headline']` — scoring anything else
  silently breaks the live == backtest invariant.
- Keep the three headline models on separate pools and submit all of them in `_on_row_built`
  (not inline in `_collect_and_log`) so they start at reception and run concurrently; resolve
  all three before the gate. FinBERT `positive`, FinMarBa `indecisive` and FLANG
  `sentiment_score` are the gate inputs — don't swap which model supplies which.
- `_evaluate_trigger` returns `(is_ok, label, detail, missing_blocked)`. `is_ok` is
  `core_and or earnings_leg or kwbody_solo_leg or flang_solo_leg`; `label` joins the fired
  leg names in the fixed order `Keyword`, `FinBERT_FMB`, `FLANG`, `Earnings`, `KwBodySolo`,
  `FLANGSolo` but is gated on **`is_ok`, not on `fired`** — that guard is what stops a
  leg-true, non-firing row from being labelled after that leg and contradicting the gate,
  and it is **still load-bearing** (the solo legs narrowed the window to `kw15_body` in
  [0.50, 0.70) with the other two cuts also unmet, they did not close it). `detail` is the
  `Trigger` cell. All four are
  derived from the **same ten per-condition locals** (`c_fin`/`c_fmb`/`c_body`/`c_head`/
  `c_fla`/`c_class`/`c_earn`/`c_body_solo`/`c_fla_solo`/`c_fin_solo`) — never recompute a
  comparison to render the cell or the alarm, or the TSV can drift from the gate. Note
  **`c_body` is shared by the core and earnings branches**: one local, two uses; the three
  `c_*_solo` are *separate* locals reading the same scores at tighter cuts. Adding another
  AND condition to the core means appending to `_vals`/`_conds`, to the `core_and` chain
  and to the `_cond_token` list, not writing a case table; adding another **branch** means
  a leg alias, an entry appended to `fired`, an OR term in `is_ok`, and — if it is a
  single-condition branch — a term in `missing_blocked`.
- Retuning a threshold is a **nine-constant edit** at the top of the file
  (`SENTIMENT_POSITIVE_MIN` / `FMB_INDECISIVE_MAX` / `KW15_BODY_MIN` / `KW15_HEAD_MIN` /
  `FLANG_SCORE_MIN` / `EARN_SCORE_MIN` / `KW15_BODY_SOLO_MIN` / `FLANG_SOLO_MIN` /
  `SENTIMENT_POSITIVE_SOLO_MIN`) — the
  gate, the `Trigger` cell and the startup lines all read them, so nothing else needs
  touching. They are module-level and read at import: **the running process keeps the old
  gate until restarted** — the startup `[Gate] ACTIVE — OR of 5 branches: …` line is what
  tells you from `logs/` which gate a given process is actually running. `_fmt_thr` renders
  a threshold at 2 dp only when that round-trips exactly, so `0.975`/`0.935` print in full
  and a retune to `0.625` prints as `0.625`; pass thresholds to log lines through `_fmt_thr`
  (`%s`), never `%.2f`. `EARN_SCORE_MIN` is an **integer** and renders as one.
- The futures are passed **positionally** into `_collect_and_log`
  (`f_finbert, f_finmarba, f_flang, f_kw, f_earn, f_arm`) — adding a model means touching
  both the signature and the `_collect_executor.submit(...)` call.
- Both dedup stashes assume a single writer thread (`_on_row_built`); don't call them from a
  pool worker.
- Articles that arrive while O5 is disconnected are **dropped by design** (no replay); the
  connector's own `PR_dataframe` table still records them, and gaps are logged.
