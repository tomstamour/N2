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
  combo is gone, now `kw15_proba_bd >= 0.60 AND kw15_proba_hd >= 0.65` from two
  corrected-label ge15 tfidf pipelines.
- **2026-07-29** — added FLANG-BERT (`../tools/Encoder_Models/FLANG-BERT_headliner.py`,
  avinasht/FLANG-BERT_flang-bert) as a **third headline model** running in parallel with
  the other two, and a **standalone third trade leg**: `FLANG sentiment_score >= 0.97`
  fires the trade on its own, OR-ed with the headline and keyword legs.
- **2026-08-01** — ⚠ **the gate became an AND** and all five thresholds were retuned:
  `FinBERT positive >= 0.8`, `FMB indecisive <= 0.14`, `kw15_proba_bd >= 0.50`,
  `kw15_proba_hd >= 0.50`, `FLANG sentiment_score >= 0.96`, **every one required**. This
  un-does the "any one leg fires" rule of the two entries above — FLANG is no longer
  standalone — and cuts the fire rate from ~25% of rows to ~2.5% (~1 trade/day) for a
  precision gain (32.6% → 46.2% at the ≥30% target). No TSV schema change (still 39 cols),
  so **the header does not tell you which gate wrote a row** — use the date, or read the
  per-row `Trigger` cell, which embeds the thresholds in force.
- **2026-08-02** — ⚠ **an EARNINGS branch was OR-ed beside that AND**, adding
  `../tools/categorizer/PrClassifier.py` (catalyst class on every PR) and
  `../tools/Earning_Scorer/earning_scorer.py`. The five-way AND is **untouched and still a
  complete branch**; a row can now ALSO fire on
  `FineClass == Earnings_Results AND EarningScore >= 4 AND kw15_proba_bd >= 0.50`.
  TSV 39 → **42 cols** (`FineClass`, `EarningScore`, `EarnBreakdown`, appended last), so
  from this date the header identifies the build again.
- **2026-08-03** — ⚠ **three SOLO branches OR-ed beside those two**, each a single
  condition firing unaided: `kw15_proba_bd >= 0.70` (`KwBodySolo`), `FLANG
  sentiment_score >= 0.975` (`FLANGSolo`) and `FinBERT positive >= 0.935` (`FinBERTSolo`).
  Purely additive — the AND and the earnings leg are untouched and every row that fired
  before still fires. ⚠ **These are the only thresholds in the file with no corpus
  calibration behind them** (operator-specified). They **subsume** the branches they sit
  beside, re-reading three of the same five scores at strictly tighter cuts, so the AND and
  the earnings leg are only *decisive* below every solo cut — FMB `indecisive` and
  `kw15_proba_hd` are the only core conditions left that can veto. Live-tape replay (209 rows,
  07-29..08-03): fires **7 → 31, 3.4% → 14.8%, ~4.4×**. **No schema change (still 42
  cols)** — the `Trigger` cell's **token count (7 = 08-02, 10 = 08-03)** identifies the
  build. (That convention ended on 08-07, when the cell became rule-grouped.)
  ⚠ A **fourth** solo leg (`FMB indecisive <= 0.115`) was written and **removed the same
  day, before it ever ran** — it is direction-agnostic. See the gate section.
- **2026-08-07** — ⚠ **the gate moved OUT of the Python entirely.** All nine threshold
  constants and the hand-written five-branch boolean are gone; rules now live in
  **`orchestrator_trigger_rules.txt`**, one rule per line
  (`<RuleName>: <cond> AND <cond> ...`), rules OR-ed, read **once at startup**.
  **Behaviour is unchanged** — verified by replaying all 390 rows of the 08-03..08-07
  TSVs through the old constants and the new rules file: `is_ok` matches **390/390**
  (41 fires, 10.5%), fired rule names match on all 41. Three things did change:
  (a) the `Trigger` cell is now **rule-grouped** (`CORE:NO[...] EARNINGS:NO[...]`) and
  token names are the rules-file field names (`Prosus_pos_hd`, not `FinBERT`) — the
  presence of `[` identifies this build; (b) `ClerkSentiment` now names **rules**, not
  legs, so the sub-leg vocabulary (`Keyword`, `FinBERT_FMB`) is gone and the detail
  moved to `Trigger`; (c) `missing_blocked` is one generalized expression instead of
  three hand-written terms, and is a **strict superset** — it now also catches a dead
  PR classifier. Fields available to a rule went from the 7 the gate read to **27**,
  including per-ticker row fields (`Float`, `LastDailyClose`, `Exchange`, …).
  New: `--check-rules` parses + echoes the gate and exits (~1 s, no models).
  ⚠ A malformed or missing rules file is a **hard fail** — no fallback gate.
- **2026-08-09** — ⚠ **every sentiment column was RENAMED; nothing else changed.**
  The exact inverse of 08-07: that changed the gate and no column, this changes every
  column name and no gate. All scores across the repo — standalone headliner, offline
  batch scorer, live TSV, rules file — now share one convention,
  **`{Model}_{channel}_{hd|bd}`** (`hd` = scored the headline, `bd` = the body):
  `positive` → **`Prosus_pos_hd`** (ProsusAI FinBERT's five columns were the last
  UNPREFIXED ones), `FMB_indecisive` → **`FinMarBa_ind_hd`**,
  `FLANG_sentiment_score` → **`FLANG_ss_hd`**, `kw15_body_proba` →
  **`kw15_proba_bd`**, `FinBERTCompletedAt`/`FMB_time` → `*_at_hd`, `FLANG_ms` →
  `FLANG_ms_hd`. Channels are `pos neu neg ss lab ms` (+ FinMarBa's `ind`).
  **Verified unchanged**: 391/391 rows of the 08-03..08-09 tape replay to identical
  `is_ok`, fired-rule labels and `missing_blocked` (42 fires, 10.74%); and 96/96
  golden probe results across 12 models are byte-identical to the pre-rename values.
  Thresholds, column COUNT (42) and cell VALUES — including `lab`, which stays
  `positive`/`neutral`/`negative` — are all untouched.
  ⚠ **The header now identifies the build again**: `positive` present = pre-08-09,
  `Prosus_pos_hd` present = 08-09 or later. But the **count is 42 either way**, so a
  half-migrated file is invisible to an arity check.
  ⚠ `_TSV_ALIASES` is **gone** — it only ever bridged the TSV and rules-file
  spellings, which are now one. `_LEGACY_FIELD_NAMES` replaces it, mapping
  pre-08-09 names to their replacement for the error message.
  Migrate old TSVs with `../tools/Encoder_Models/rename_encoder_cols.py` (header
  line only — no value changed, so re-scoring would be waste).
  ⚠ **The dated entries above this one, and every section below, have been rewritten
  to the NEW names** so the whole file is greppable with one vocabulary — including
  where they describe builds that predate the rename. Only this entry quotes the old
  spellings. For the names a given historical build literally wrote, read the header
  of a TSV from that date.
- **2026-08-17** — ⚠ **the gate stopped being a flat OR.** A new rule kind: a line
  prefixed `REQUIRE ` is a **PRECONDITION** — it never fires, it only permits, and
  every one is AND-ed over the whole gate, so
  `gate = (every REQUIRE holds) AND (any ordinary rule fires)`. One ships, first in
  the file: `REQUIRE ClassWhitelist: FineClass in MA, Partnership, Regain_Compliance,
  Clinical_Data, Contract_Award, Patent_IP, Earnings_Results`. The five existing rules
  are **unchanged character for character** — they are simply now conditional on the
  catalyst class. Second new feature, needed to express it: an **`in` / `not in`
  operator** on string fields taking a comma list (the DSL has no OR inside a line, so
  a 7-class whitelist would otherwise have been 35 rules).
  Measured (`replay_gate.py`, honest window, 1710 rows, base 8.71%): **177 → 119
  fires, 21.5% → 26.9% precision, 2.46× → 3.09× lift, 3.3 → ~2.2 trades/day** — 52 of
  139 FPs removed for 6 of 38 TPs lost. `Earnings_Results` is whitelisted **on
  purpose**, so EARNINGS is untouched (80 fires either way). ⚠ A class **veto** was
  measured and rejected first: it moves precision only 22.0% → 22.6%, because the
  sentiment cuts already almost never fire on the dead classes.
  Three Trigger-cell changes: a REQUIRE group prints **`ALLOW`/`BLOCK`**;
  ⚠ **a `:YES` group no longer implies the row fired** (a rule can hold while a
  precondition blocks — `NO | ClassWhitelist:BLOCK[...] CORE:YES[...]` is correct);
  and membership renders `FineClass:PASS(MA∈{MA,Partnership,…})` with the full list.
  ⚠ **FAILS CLOSED**: a `None` `FineClass` closes the whole gate, so the PR classifier
  is now a single point of failure — see the `missing_blocked` bullet.
  **No schema change (still 42 cols)**; only the cell content moves.
  Behaviour-neutrality of the refactor was proved before the rule was added: with the
  Python edits in and no REQUIRE line, all 4376 corpus rows and the 457-row live tape
  replay **byte-identically** on `is_ok`, `label`, the full `detail` cell and
  `missing_blocked`, and `--check-rules` echoes byte-identically.

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
/home/tom/venv/bin/python Orchestrator5.2.py --check-rules   # parse+echo the gate, exit
```

`--check-rules` (2026-08-07) parses `orchestrator_trigger_rules.txt`, echoes the gate it
produces and exits — no models, no sockets, ~1 s. **Run it before every restart**: since a
bad rules file refuses to start, this is what keeps a typo from becoming a trading outage.
Exit 0 means the gate echoed is exactly the gate O5.2 would run. Since 2026-08-17 it
prints `REQUIRE (n) <name>: …` lines **first**, then the ordinary rules — and with no
REQUIRE line loaded its output is byte-identical to the pre-08-17 echo, deliberately, so
two runs can be `diff`ed to prove a refactor changed nothing.

Startup order is: **rules file first** (before any model — the rules file is the artifact a
human edits, so failing on it should not cost 30 s of ONNX loading), then pre-warm of **all
three** headline models (FinBERT + FinMarBa + FLANG-BERT), both ge15 keyword pipelines and
the PR classifier + earnings scorer. Every one hard-fails (RuntimeError) if it is missing or
returns the wrong shape — a trading process must not run with a permanently-None leg, which
would silently remove part of the gate. Verify the console shows `[Gate] Loaded N rule(s)`
followed by all **five** ready lines: `FinBERT-headliner model ready.`,
`FinMarBa-headliner model ready.`, `FLANG-BERT-headliner model ready.`,
`ge15 keyword pipelines ready (smoke: body=… head=…)`, and
`PR classifier + earnings scorer ready (smoke: class=Earnings_Results score=4 …)`.
⚠ The two smoke lines no longer quote any threshold — they are *model* checks, and the cuts
now live in the rules file.

Startup then echoes the whole gate — `[Gate] ACTIVE — N PRECONDITION(s) AND (OR of M
rule(s)) from …` (or the plain `OR of N rule(s)` form when no `REQUIRE` line is loaded),
one line per rule **rebuilt from the compiled conditions** (so a parse that differs from
what you meant is visible right there), then a derived `<model> feeds rule(s): …` line per
scorer, which **WARNs when a model is scored on every PR but no rule reads it**. **That
block is the only way to tell which gate a running process holds**: rules are read at
startup, so a process started before an edit keeps the old gate until restarted, and since
2026-08-02 gate changes no longer change the TSV header.

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
1. **Dedup** — per-day article-ID dedup *and* content dedup, because RTPR re-publishes the
   same PR under a **new ID** (PRZO, FOFO). Both stashes are single-thread → lock-free,
   FIFO-capped, cleared when the arrival day rolls.
   ⚠ Since **2026-08-12** the content key is **per TICKER** — `(normalized headline,
   ticker)`, one entry per ticker — not the whole-article `(headline, sorted tickers)` it
   was before. That day four PRs arrived twice and **three armed the clerk twice** (NEXR
   on clients 1014+1015, BBLG, FABC; EXYN double-armed on a BAD gate), because the
   re-publish carries a **different ticker list**: the warrant is added or dropped
   (`NEXR` / `NEXR+NEXRW`) or a partner appears (`FABC` / `FABC+KOPN`). The headline
   always matched; the tuple never did. Per-ticker keying sees them as the same news
   because the ticker that matters is in both copies, and it still lets a genuinely new
   partner through (`FABC` dropped, `KOPN` kept). **Exception**: a partial hit where no
   *in-universe* ticker survives is dropped whole — that is the shape where the second
   copy is the one carrying the warrant, and the fan-out's all-out-of-universe fallback
   would otherwise write a junk warrant-only row into the calibration corpus.
   Tape replay 07-02..08-12: **48 re-publishes on 26 of 39 days**, of which the old key
   caught only 11; zero regressions and zero false positives.
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
  ⚠ Since **2026-08-12** the clerk enforces **one live session per symbol**: an ARM for an
  already-armed ticker comes back `{"status":"rejected","reason":"already_armed"}` and O5.2
  writes `ClerkSentiment = skipped:arm_rejected` (the row is still written, with its full
  `Trigger` cell, so the duplicate stays auditable). This is the **backstop** for the
  per-ticker dedup above — it catches duplicate shapes the headline key cannot see. Before
  it, nothing anywhere stopped two sessions on one ticker: each gets its own clientId, and
  x-wing tracks position from its **own fills only** (no `reqPositions`), so the second
  session starts at position 0 and enters independently — 2× size, 2× commission, two
  ladders competing on the exit. **If this reply ever appears in `clerk.log`, read it** —
  it means O5.2's dedup missed something.
- STEP 1b **SENTIMENT** after scoring: `{ticker, Sentiment: "OK"|"BAD"}` — the wire protocol
  is OK/BAD only; the trigger source lives in the TSV.
  ⚠ **O5.2 has no clock rule anywhere near the gate** (its only two time-of-day
  comparisons are `_is_rth_now()`, which picks `RTH_*` vs `ETH_*` baseline columns, and
  the 03:00 universe reload) — but **until 2026-08-19 the CLERK could discard this `BAD`
  and trade on the surge alone**, so a PR this gate rejected still traded. That was
  `WINDOW 04:00-06:59 … finbert_gate=off` in `../x-wing-mole/clerk-trigger-conditions.txt`,
  and it reached wider than its span: the clerk resolves a closed-market arm against its
  **deferred 04:00 collection start**, not its arrival, so overnight and weekend PRs fell
  under it too (88 rows across `tables/`, only 75 of them 04:00–06:59 arrivals). **Look for
  it in `ClerkSentiment`, which is the only trace of it in O5's output:**
  `BAD:sentiment_ignored_gate_off` (bypassed) vs `BAD:sentiment_bad_released` (honoured).
  The window was **removed 2026-08-19** — the gate is now honoured at every hour and that
  88 is frozen. Recorded here because O5's own docs never mentioned it, which is exactly
  why it reads as an orchestrator rule: **if a PR trades that this gate rejected, the cause
  is in `../x-wing-mole/`, not in this directory.**
- **Gate (`_evaluate_trigger`) reads `orchestrator_trigger_rules.txt`** (since
  2026-08-07 — before that, five hardcoded branches). Since **2026-08-17** it is no
  longer a flat OR: `is_ok = required_ok and any(rule fires)`, where `required_ok` is
  every `REQUIRE` precondition holding. Conditions within any rule are AND-ed.
  **This section is the calibration record — the authority on what the gate currently
  IS is the rules file, and on what a given running process holds, its `[Gate] ACTIVE`
  log block.** One precondition and five rules ship.
  - **ClassWhitelist precondition** (2026-08-17) — `FineClass in MA, Partnership,
    Regain_Compliance, Clinical_Data, Contract_Award, Patent_IP, Earnings_Results`.
    Gates all five rules below; **none of them can fire on any other catalyst class**.
    - It exists because every cut in the gate reads a **sentiment** score and none
      reads the catalyst **type**, so a confidently-positive headline fires whether the
      PR is an acquisition or a share offering. Calibrated on
      `../price_stats_loader/outputs/08_class_trading_value.md` (honest window, 1710
      rows, base 8.71%): **177 → 119 fires, 21.5% → 26.9% precision, 2.46× → 3.09×
      lift, ~2.2 trades/day** (52 of 139 FPs removed for 6 of 38 TPs lost). Full
      corpus 349 → 237.
    - ⚠ A class **VETO** was measured first and **rejected**: dropping the six worst
      classes moves precision only 22.0% → 22.6%, because the sentiment cuts already
      barely fire there (8 fires total across all six). Only a whitelist moves it.
    - `Earnings_Results` is in the list **deliberately**, so EARNINGS keeps working
      (80 fires either way). Without it that rule could never fire again.
    - `Clinical_Data` and `Regain_Compliance` contributed **6 fires and 0 TPs** in that
      window and are kept anyway — operator's call; n is far too small to call them
      dead. Dropping both reads 38.5% on 65 fires.
    - ⚠ n=32 TPs, and EARNINGS is now **34% of all fires** while being the one branch
      the corpus cannot replay faithfully (400-word `Body` cap corrupts `EarningScore`
      on 42/70 earnings rows). The weakest measurement now carries a third of the gate.
  - **CORE rule** — the 2026-08-01 five-way AND, **every one must hold**, unchanged:
    `Prosus_pos_hd >= 0.8`, `FinMarBa_ind_hd <= 0.14` (the only ceiling),
    `kw15_proba_bd >= 0.50`, `kw15_proba_hd >= 0.50`, `FLANG_ss_hd >= 0.96`. Calibrated on
    `price_stats_loader/outputs/06_PRstats_2026-06-22-2026-07-30_11EncBodyHeadScored_kwScored_FinBERT.tsv`
    (1055 labelled rows): fires **26×, 2.5%** at **46.2% precision / 2.69× lift** on the
    ≥30% target (80.8% / 2.51× on ≥15%). Recall 6.6% — a high hit rate on few shots,
    ~1 trade/day, **deliberately**. ⚠ n=26 ⇒ ±19pp CI and post-selection.
  - **EARNINGS rule** (2026-08-02) — also all-required: `FineClass == Earnings_Results`,
    `EarningScore >= 4`, and `kw15_proba_bd >= 0.50` — the same cut CORE uses, one condition
    written in two rules. FinBERT / FMB / FLANG / `kw15_proba_hd` are **not** consulted.
    - It exists because the core AND is **structurally blind to earnings PRs**: its own
      FinBERT headline score measured test AUC **0.297** there (*anti*-predictive) —
      winners and duds share the headline ("X Reports Q1 2026 Financial Results"), the
      numbers are in the body. See `../tools/Earning_Scorer/Earnings_text_structures_report.md`.
    - Calibrated 2026-08-02 on `../tools/Earning_Scorer/PRstats_2026-05-04-2026-07-30_EarningResults_earnScored.tsv`
      (518 labelled rows, base 7.1%) with kw15_proba_bd recomputed by the frozen ge15 pipeline:
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
  - **THREE SOLO rules** (2026-08-03) — **one condition each**, firing unaided, nothing
    else consulted: `KwBodySolo: kw15_proba_bd >= 0.70`, `FLANGSolo: FLANG_ss_hd >= 0.975`,
    `FinBERTSolo: Prosus_pos_hd >= 0.935`. All three are floors.
    - ⚠ **Uncalibrated.** Alone among every threshold in this file these three were
      operator-specified (2026-08-03) with **no fires/precision/lift measurement** behind
      them. The nearest existing evidence, none at these cuts: FLANG standalone at 0.97
      ran 46.1% @≥15% / 24.3% @≥30% on 3434 rows; the ge15 **body channel alone** at 0.60
      ran ~40.9% vs the 57.3% the body+head *pair* bought. Both point **below** the AND
      branch's 46.2% @≥30%. Accepted trade: more trades, lower hit rate.
    - ⚠ They **subsume** the rules they sit beside: each re-reads a CORE score at a
      **strictly tighter** cut (0.70>0.50, 0.975>0.96, 0.935>0.8), hence each solo
      condition **implies its CORE twin**. CORE and EARNINGS are only *decisive* in the
      band below every solo cut: `kw15_proba_bd` [0.50,0.70), `FLANG_ss_hd` [0.96,0.975),
      `Prosus_pos_hd` [0.8,0.935). **`FinMarBa_ind_hd` and `kw15_proba_hd` are the only
      conditions with no solo twin** — the only ones that can still veto. **The 08-01 and
      08-02 calibrations describe rules, not the gate**; no measurement of the combined
      gate exists.
    - ⚠⚠ **Do not add an `indecisive <= x` solo leg.** One (`FMBSolo`, 0.115) was
      implemented on 2026-08-03 and **removed the same day, before it ever ran live**,
      because it is **direction-agnostic**: FinMarBa's classes are negative/indecisive/
      positive, so a low `indecisive` means *confident in either direction*. Inside the core
      AND `positive >= 0.8` pins the sign alongside it; standalone nothing does. Not
      hypothetical — on 2026-07-29 it fired `WETO` *"Announces Share Consolidation"* (a
      **reverse split**) on `indecisive=0.0961` / `FinMarBa_lab_hd=positive`, while **both** other
      headline models disagreed (FinBERT `positive` 0.150, FLANG `sentiment_score`
      **−0.8037**). It was also the loosest cut measured — 44/552 rows (**8.0%**) vs
      FLANGSolo 2.9%, KwBodySolo 2.4%, FinBERTSolo 1.3% — roughly doubling the gate alone.
      If revisited it needs a **direction condition** beside it (e.g. also `FLANG_ss_hd > 0`).
      A standing ⚠⚠ warning, and a commented `FMBDirected` example showing the only
      acceptable shape, sit in the header of `orchestrator_trigger_rules.txt` — which is
      where the edit would actually be made.
    - Live-tape replay (`tables/news_output_2026-07-29..08-03`, 209 rows, current constants
      on both tiers): **7 (08-02 gate) → 31**, i.e. **3.35% → 14.83%, ~4.4×** the 08-02
      build (+11 KwBodySolo, +10 FLANGSolo, +2 FinBERTSolo, +1 both kw/FLANG). A trade-rate
      observation — those rows carry no label, so it is **not** a precision claim.
  - A `None` value still makes only its **own** condition false, and prints `MISSING`.
    `_evaluate_trigger` returns **`missing_blocked`** — true when the row did NOT fire and
    some rule was blocked by a `None` **alone**: every one of that rule's *evaluable*
    conditions passed, and at least one alarming field was `None`. Since 2026-08-07 that is
    **one generalized expression** over the loaded rules, replacing three hand-written
    terms. It reproduces them exactly for CORE and for the solo rules, and is a **strict
    superset** for EARNINGS: a dead PR classifier (`FineClass` `None`) on a row whose other
    earnings conditions passed now warns, where the old `c_class` made it silent.
    Measured: **0** extra warnings on the 390-row 08-03..08-07 tape (no blank `FineClass`),
    **25 of 138** on the 39-col 07-29..08-02 tape where `FineClass` is absent by design.
    A **single-condition rule makes the alarm very loud** — "all evaluable passed" is
    vacuous there, so a dead kw / FLANG / FinBERT scorer warns on *every* row. That volume
    is the signal: an OR-ed gate means a dead model no longer halts trading, so the tape
    looks even less obviously broken. `_collect_and_log` logs it at **WARNING**
    (`BLOCKED BY MISSING SCORE ONLY`). Row fields (`Float`, …) deliberately do **not** arm
    it — a symbol missing from the universe is a universe gap, not a dead model.
    ⚠ Since **2026-08-17** a `REQUIRE` precondition arms it too, and that is now the
    loudest case in the file — it needed no new code, falling straight out of the same
    generalized expression. Because the class whitelist **fails closed**, a dead PR
    classifier (`FineClass` `None`) stops **all** trading, not one branch, and this
    WARNING then fires on **every row** until it is fixed. **The PR classifier is now a
    single point of failure for the entire gate**, and this alarm is the only thing
    distinguishing "trading has stopped" from "a quiet news day" — grep `logs/` for it.
    That is also why `_classify_and_score_earnings` was fixed the same day to **keep
    `FineClass` when only the scorer crashed**: it used to return the all-`None` dict on
    any exception, which after the whitelist would have converted "the earnings scorer
    died" into "this row cannot trade at all", contradicting its own docstring. A future
    *timeout* still yields `None` — that genuinely has no class.
    ⚠ `EarningScore` prints **`NA`**, not `MISSING`, when the class is known and simply
    isn't `Earnings_Results` (~81% of rows, measured 315/390): the scorer was skipped on
    purpose, and conflating that with a fault would bury the one row where it really died.
- The TSV `ClerkSentiment` cell records trigger + clerk reply. Since 2026-08-07 the label
  joins the fired **RULE** names with `_`, in rules-file order — so **the vocabulary is
  the operator's**, set by how rules are named in `orchestrator_trigger_rules.txt`
  (hence the parser rejects `_` in a rule name: it would make this cell ambiguous).
  Observed on the 08-03..08-07 tape: `BAD` (349), `FLANGSolo` (19), `KwBodySolo` (10),
  `EARNINGS` (5), `CORE_FLANGSolo` (3), `CORE_FLANGSolo_FinBERTSolo` (2), `FinBERTSolo`,
  `FLANGSolo_FinBERTSolo`; plus `skipped:arm_rejected` / `skipped:excluded_string` /
  `skipped:not_armed`.
  ⚠ **The old sub-leg vocabulary is gone**: `Keyword`, `FinBERT_FMB` and `FLANG` named
  parts *inside* the CORE branch, so e.g. `Keyword_FLANG_FLANGSolo` and `FLANG_FLANGSolo`
  both now read `FLANGSolo`. Nothing is lost — that detail moved to `Trigger`, which since
  the same date prints every condition of **every** rule. Clean split: `ClerkSentiment` =
  *which rules fired*, `Trigger` = *why*. The wire is unchanged (`OK`/`BAD`).
  The label is gated on **`is_ok`, not on a non-empty `fired`**. ⚠ Since **2026-08-17**
  that guard is **LOAD-BEARING again** (it was merely defensive from 08-07 to then): a
  `REQUIRE` rule does not imply a fire, so a row whose CORE conditions all held but whose
  whitelist blocked it has a **non-empty `fired` and `is_ok` False** — without the guard
  `ClerkSentiment` would read `CORE_FLANGSolo` on a row that never traded, and every
  `fired_*` analysis downstream would count it as a fire. The comment in the code
  predicted exactly this day. (It was load-bearing once before, too: 93 of 390 rows,
  23.8%, had a *leg* true and did not fire.)
  ⚠ `BAD` now also means "a precondition blocked this row". The `Trigger` cell's
  `:BLOCK[` group is what tells the two apart — `grep -c ':BLOCK\[' tables/*.tsv`.
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
  by `kw15_proba_bd` + `kw15_proba_hd`) · **38** = from 2026-07-29 (adds the FLANG
  block after `FinMarBa_lab_hd`: `FLANG_at_hd`, `FLANG_pos_hd`, `FLANG_neu_hd`,
  `FLANG_neg_hd`, `FLANG_ss_hd`, `FLANG_lab_hd`, `FLANG_ms_hd`) · **39** = from
  2026-07-29 (adds `Trigger` as the **last** col — the 38-col build only ever ran in
  testing, so in practice a live file is 31 or 39) · **42** = from 2026-08-02 (adds
  `FineClass`, `EarningScore`, `EarnBreakdown` last). ⚠ **Neither 39 nor 42 identifies a
  gate.** 39 spans the 07-29 OR and the 08-01 AND; 42 spans the 08-02 two-branch OR, the
  08-03 five-branch OR **and every rules-file gate from 08-07 on** — the 08-07 refactor
  changed no columns at all. Use the date, or the `Trigger` cell: flat tokens mean a
  pre-08-07 build (count 5/7/10 = 08-01/08-02/08-03), a `[` means 08-07 or later. For a
  gate the rules file cannot tell you (it changes), read the run's `[Gate] ACTIVE` block.
- ⚠ **2026-08-09 changed every column NAME and kept the count at 42.** So the header
  identifies the *build* again — a bare `positive` column means pre-08-09, a
  `Prosus_pos_hd` means 08-09 or later — but **not by arity**, and a file half-migrated
  by hand would pass any column-count check. `rename_encoder_cols.py` is the safe path;
  it refuses a header that matches neither vocabulary. A migrated tape's header equals
  `_TSV_COLUMNS` exactly, so O5.2 appends to it without rotating.

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
booleans the trade fires on (so it cannot disagree with the gate). Since **2026-08-07** it
is **rule-GROUPED**, one group per loaded rule:
`<YES|NO> | <RuleName>:<verdict>[<field>:<v> <field>:<v> ...] <RuleName>:<verdict>[...]`
where the verdict is `YES`/`NO` for an ordinary rule and, since **2026-08-17**,
`ALLOW`/`BLOCK` for a `REQUIRE` precondition. e.g.
`NO | ClassWhitelist:BLOCK[FineClass:FAIL(Other∉{MA,Partnership,…})]
CORE:NO[Prosus_pos_hd:PASS(0.9100>=0.80) FinMarBa_ind_hd:FAIL(0.2000>0.14)
kw15_proba_bd:PASS(0.5500>=0.50) …] EARNINGS:NO[FineClass:FAIL(Other!=Earnings_Results)
EarningScore:NA kw15_proba_bd:PASS(0.5500>=0.50)] KwBodySolo:NO[kw15_proba_bd:FAIL(0.5500<0.70)] …`
- **Every rule prints on every row, including rows that FIRED** — that is deliberate: it
  keeps the TSV a complete offline replay input, so a candidate retune can be scored from
  `tables/` alone without re-running any model.
- ⚠⚠ **Since 2026-08-17 a `:YES` group does NOT imply the row fired.** Before then, a cell
  starting `NO |` meant every group read `:NO`. Now an ordinary rule can hold while a
  precondition blocks the gate, so `NO | ClassWhitelist:BLOCK[…] CORE:YES[…]` is a normal,
  correct cell. **Read the LEADING verdict for what the row did; read the groups for why.**
  Anything that counted fires by grepping `:YES` is now wrong — and note this is the same
  distinction as `replay_gate.py`'s `fired_<Rule>` columns, which mean "fired the gate",
  not "this rule's conditions held", and so disagree with the cell on blocked rows.
- Token names are the **rules-file field names** (`Prosus_pos_hd`, not `FinBERT`), so a
  token can be pasted straight back into a rule. This also removes a real ambiguity — the
  old `FinBERT:`/`FLANG:` tokens never said *which* score they meant.
- ⚠ **Format history for anyone parsing old TSVs.** Until 08-07 the cell was a flat token
  list that only ever grew by appending, and **the token count identified the build**: 5 =
  08-01, 7 = 08-02, 10 = 08-03. That is now over — **the presence of `[` identifies
  08-07-or-later**, and the group count follows the rules file rather than the release
  date. From **08-17**, an `ALLOW`/`BLOCK` group verdict (or an `∈`/`∉` glyph) identifies
  the build. Every parser written for the flat format breaks; there was no way to avoid
  that while making the cell say which rule a condition belongs to.
- `<v>` is `PASS(<score><op><thr>)`, `FAIL(<score><op><thr>)`, a bare `MISSING` (the value
  was `None` — model error or future timeout, otherwise indistinguishable from a low
  score), or `NA`. The printed operator is always the relation that is TRUE of the row: the
  rule's own operator on a pass, its negation on a fail. Set membership prints the `∈`/`∉`
  glyph and **the whole list, never truncated** —
  `FineClass:PASS(MA∈{MA,Partnership,…})` — because a truncated threshold would break the
  self-describing property this cell exists for. (Glyphs, not the words, because a token
  must not contain a space; `_OP_CELL` is one line to change if that ever bites.)
  Scores are display-rounded to 4 dp
  — the PASS/FAIL token, not the number, is authoritative. Thresholds render at 2 dp when
  exact, else `%g`, so a retuned `0.625` never prints as `0.62`, and they are rendered once
  at **parse** time (cached in `_Cond.thr_str`), never per row.
- Integer-valued fields print as integers: `EarningScore:PASS(5>=4)`, not a misleading
  `5.0000>=4.00`. String fields print bare and compare case-insensitively:
  `FineClass:FAIL(MA!=Earnings_Results)`.
- **`NA` is the fourth verdict** and belongs to `EarningScore`: the class is known and
  simply isn't `Earnings_Results`, so the scorer was deliberately skipped. That keeps
  `MISSING` meaning what it means everywhere else; without it ~81% of rows (measured
  315/390) would read `MISSING` and drown out the one row where the scorer actually died.
  It is a general mechanism (`_FieldSpec.na_when`), not a special case in the gate loop.

`Trigger` is **not** a duplicate of `ClerkSentiment`: it is written on **every** row (the
gate runs before the arm branch), where `ClerkSentiment` is overwritten with `skipped:*`
when the ticker was never armed, and it resolves the gate into its underlying models — so
it shows *which* condition of *which* rule failed and by how much. It also embeds the
thresholds in force, which is what makes a row self-describing across a retune: two rows in
the same daily TSV, written either side of a restart, carry their own cuts.
The `[Gate]` log line for a rejected row is this exact same string; a rejected row where a
`MISSING` score was the **only** blocker additionally logs at WARNING.

Only `FLANG_ss_hd` and `FinMarBa_ind_hd` feed the gate from their blocks; the rest
are for offline re-analysis. The `FLANG_*` cols map 1:1 onto the offline `FLANG-BERT_*` cols
from `HeadScore11encoders.py`; `FLANG_at_hd` is a completion **timestamp** (`FinMarBa_at_hd`
convention) while `FLANG_ms_hd` is the model's own inference **duration**. On a
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
  all three before the gate. Which score reaches which rules-file field is fixed by
  `_TRIGGER_FIELDS` — don't swap which model supplies which.
- **Changing the gate is a text edit, not a code edit.** Retune a threshold, add a rule,
  delete a rule, **add or remove a `REQUIRE` precondition**: edit
  `orchestrator_trigger_rules.txt`, run `--check-rules`, restart. The
  gate, the `Trigger` cell, the `[Gate] ACTIVE` echo and the dead-scorer alarm all derive
  from the same parsed rules, so nothing else needs touching. Rules are read at import-time
  of `main()`: **a running process keeps its old gate until restarted** — its `[Gate]
  ACTIVE` block in `logs/` is the record of what it actually holds.
- **Adding a new addressable field** is one row in `_TRIGGER_FIELDS` (kind, source dict,
  key, documented range, whether a `None` there arms the alarm). No second table: the
  Trigger cell prints the same name, and `_trigger_fields` is derived. If the field can be
  legitimately absent (as `EarningScore` is on ~81% of rows), give it an `na_when`
  predicate so it prints `NA` instead of `MISSING` and stays out of the alarm.
- `_evaluate_trigger` returns `(is_ok, label, detail, missing_blocked)` and takes the five
  score dicts **plus `row_vals`** (positionally), with an optional `rules=` for offline
  replay. **`is_ok` is `required_ok and bool(fired)`** (since 2026-08-17); `label` joins
  fired **rule** names; `detail` is the `Trigger` cell; `missing_blocked` drives the
  WARNING only. All four come from one pass over the compiled rules — never recompute a
  comparison to render the cell or the alarm, or the TSV can drift from the gate.
  `_cond_token` takes `ok` as an argument rather than re-deriving it, which is what makes
  that drift impossible.
  ⚠ The `if is_ok else 'BAD'` guard on `label` is **load-bearing again** — a `REQUIRE`
  rule does not imply a fire, so `fired` can be non-empty while `is_ok` is False. It was
  merely defensive from 08-07 to 08-17, and load-bearing before that too (pre-08-07
  `fired` held *leg* names that were sub-parts of the CORE branch).
  ⚠ The signature and the 4-tuple were **deliberately not extended** with `required_ok`:
  `replay_gate.py` and five importers call this positionally, and the A/B replay answers
  the same question for free.
- **A `REQUIRE` rule is a precondition, not a branch.** `_Rule.kind` is `_RULE_FIRE` or
  `_RULE_REQUIRE` (last field, defaulted, so 4-positional construction still works).
  A REQUIRE rule never enters `fired` and never reaches `ClerkSentiment`; it only sets
  `required_ok`. `_RULE_VERDICT` maps the kind to the group's verdict words
  (`YES`/`NO` vs `ALLOW`/`BLOCK`) — one table, so a third kind is a one-line addition.
  Adding a REQUIRE line **fails closed**: think about what a dead scorer behind it does
  before you write one, because it stops the whole gate rather than one branch.
- **`in` / `not in` are string-only**, and `_OPS`/`_OP_NEG`/`_OP_CELL` must share one key
  set — enforced at **import time** with a `raise` (not an `assert`; `-O` strips those).
  `_op_in`/`_op_not_in` are wrappers because `operator.contains(a, b)` is `b in a`,
  reversed from every other entry; wrapping is what keeps `_cond_true` a single uniform
  `cond.fn(lhs, cond.rhs)` call with no per-operator branch on the trade-critical path.
  ⚠ A list's `thr_str` is built from the **ordered, as-typed items, never the frozenset** —
  frozenset iteration order varies with `PYTHONHASHSEED`, which would make `--check-rules`
  print a different gate on every run.
  ⚠ **`FineClass` VALUES are unvalidated.** Unlike a field name, a misspelled class gets no
  whitelist check, no did-you-mean and no hard fail — it just never matches, silently
  narrowing the gate. That is now on the most load-bearing line in the rules file. Coupling
  the parser to `PrClassifier`'s class list would fix it and is a reasonable follow-up; it
  needs its own forward-compat story for when a class is added.
- Row fields (`Float`, `LastDailyClose`, `Exchange`, `itiBaseline`, `tradeSizeBaseline`,
  `MktCap`, `Symbol`, `TickerCount`) are resolved **once** into `row_vals` at step 2c of
  `_collect_and_log`, before the gate, and `completed_dict` reads from that dict rather than
  calling the `_lookup_*` helpers again — each takes `_iti_df_lock` and does a pandas column
  scan, so keep it to one lookup apiece.
- `_fmt_thr` renders a threshold at 2 dp only when that round-trips exactly, so `0.975` /
  `0.935` print in full and a retune to `0.625` prints as `0.625`. It runs at **parse** time
  only; the result is cached in `_Cond.thr_str`, so no threshold is formatted per row.
- **No `eval()`** anywhere in the rule path — a whitelisted field table plus a closed
  operator dict, resolved to a callable at parse time. (Two of those callables are now
  module-level `def`s rather than `operator` builtins; the guarantee never rested on their
  being builtins, only on the closed text-keyed whitelist and the load-time binding.) Keep
  it that way; the file is operator-edited and a rule is not code.
- A malformed / missing / empty rules file is a **hard fail with no fallback gate**. Don't
  add one: a fallback means a typo silently trades a gate nobody wrote. `--check-rules` is
  the mitigation.
- The futures are passed **positionally** into `_collect_and_log`
  (`f_finbert, f_finmarba, f_flang, f_kw, f_earn, f_arm`) — adding a model means touching
  both the signature and the `_collect_executor.submit(...)` call.
- Both dedup stashes assume a single writer thread (`_on_row_built`); don't call them from a
  pool worker.
- Articles that arrive while O5 is disconnected are **dropped by design** (no replay); the
  connector's own `PR_dataframe` table still records them, and gaps are logged.
