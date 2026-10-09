# CLAUDE.md

This file tells Claude Code how to work in this folder.

## What this is

`orchestrator5/` runs a news-to-trade gate. It reads a news feed, scores
each article, and if the score passes the gate, tells a trading process to
trade.

`Orchestrator5.5.py` is the version to run (live since 2026-09-27 11:23). It
is 5.4 plus two things:
- the FM3 shadow lane: new text scores logged on every row, never traded on
  (see "5.5: the FM3 shadow lane");
- since 2026-09-28, kw15_FT: kw15 refit on every labelled row, scored beside
  kw15 (see "5.5: kw15_FT");
- since 2026-09-29, the S4 kw columns: two more single-channel keyword
  pipelines, scored beside kw15/kw15_FT, no gate rule (see "5.5: kw_S4");
- since 2026-10-07, a NEW GATE on 6 priority models, and a late stage for
  every other model (see "5.5: the priority gate and the late stage"). This
  replaces "The gate" and parts of "How one article moves" below.

Everything else in this file describes 5.4 and holds for 5.5 unchanged.

- **Rollback to 5.5 before 2026-10-07:** copy each `*.bak-20261007` over its
  file and `orchestrator_trigger_rules.pre-20261007.txt` over
  `orchestrator_trigger_rules.txt`, then restart, at a day boundary. The old
  and new code each refuse the other's rules file.

- **Rollback to 5.5 without kw15_FT:** `Orchestrator5.5.py --no-kw15ft` (the
  columns stay, blank; this also disables S4, see "5.5: kw_S4"). The
  pre-kw15_FT file itself is `Orchestrator5.5.py.bak-20260928` (untracked).
- **Rollback to 5.5 without S4 only:** `Orchestrator5.5.py --no-s4` (kw15_FT
  keeps running; only the S4 columns stay blank).
- **Rollback to 5.4:** needs `orchestrator_trigger_rules.pre-20261007.txt` as
  the rules file (5.4 cannot load the 2026-10-07 gate). Stop 5.5 (Ctrl+C in tmux
  pane `orch5:1.1`), copy that file over `orchestrator_trigger_rules.txt`, then
  start 5.4.
- **Rollback to 5.3:** the step before that. It still runs, with its own rules
  line (see "The gate").
- **Never run 5.2, 5.1 or 5.0:** they are old and frozen, and neither 5.2 nor
  5.3 can load the current rules file.

Full history of every change lives in `version-control.txt`. This file only
covers how 5.4 works right now.

## Running it

Always use the venv python, never system python3:

```bash
/home/tom/venv/bin/python Orchestrator5.5.py
/home/tom/venv/bin/python Orchestrator5.5.py --check-rules   # check the gate file, then exit
/home/tom/venv/bin/python Orchestrator5.5.py --check-xgb     # check model-3 + its latency, then exit
/home/tom/venv/bin/python Orchestrator5.5.py --check-fm3     # check the FM3 lane + its latency, then exit
/home/tom/venv/bin/python Orchestrator5.5.py --check-kw15ft  # check kw15 + kw15_FT (parity, timing), then exit
```

It runs in tmux pane `orch5:1.1` (cwd `orchestrator5/`).

Run `--check-rules` before every restart (~1 second, no models). If the rules
file has a typo, this catches it before real money is at risk.

Run `--check-xgb` after any model change (~5 seconds). It checks model-3's
files byte for byte (md5), loads the ten boosters, scores a test row, and
prints the model time per PR against a 100 ms budget.

On startup, watch the console for: `[Engine]` lines (layout, which CPUs, CPU
power mode), `[Gate] Loaded N rule(s)`, `[Engine] ready — ... goldens=ok`,
`[Engine] smoke OK`, then `[Gate] ACTIVE — ...` with one line per rule. If any
is missing, the process did not start clean. Startup takes ~6 seconds.

5.4 needs two other processes, started in any order:

```bash
cd ../RTPR_connector3 && /home/tom/venv/bin/python RTPR_connector3_Firehose.py --flush-on-shutdown
/home/tom/venv/bin/python clerk-1.2.py --client-qty 5 --port 4002 --listen-port 8765
```

`--port 4002` is the paper Gateway. Use `4001` only for real money. Never
run two orchestrators at once — both would try to trade the same news.

Flags for tests and replays (defaults are production): `--rules`,
`--feed-port`, `--clerk-port`, `--out-dir`, `--log-dir`, `--universe-file`,
and the engine layout `--engine --pools --workers --intra-op --cpus`. Never
point a test run at the default `tables/` or `logs/` — daily-board and the
20:05 cron read them.

There are no tests. Check it worked by reading `logs/` and `tables/`, or run
the bench (see "Latency").

## How one article moves through the system

1. A news line arrives on the feed thread. The clock for `Decision_ms`
   starts here. This code must stay fast — only quick checks and handoffs.
2. **Dedup.** Skip an article already seen today. The key is
   `(headline, ticker)`, one per ticker — a PR can re-arrive with a
   different ticker list and this still catches it.
3. **Filters.** Drop an article with more than 2 tickers, or a headline
   matching `excluded_strings.txt`. A match in `excluded_strings-2.txt` still
   logs the row but skips the trade.
4. **Arm.** Tell the trading process to get ready, for every in-universe
   ticker, right away, before any scoring.
5. **Score.** Hand five jobs to the scoring engine at once: the three
   headline models (FinBERT, FinMarBa, FLANG), the keyword model, and
   model-3's headline embedding. The earnings model runs beside them.
6. **Gate.** Once the scores are back (plus the one XGBoost score the rules
   read), check the gate rules, send the trading process OK or BAD, and
   write one row per ticker to the daily TSV. The rest of the XGBoost family
   is scored AFTER the decision is sent — it only fills the TSV.

If a ticker is already armed, the trading process rejects the second arm with
`already_armed` — a safety net for anything dedup missed.

## The scoring engine (o54_engine.py)

Every model call runs in one scoring engine. It holds the three headline
models, the keyword model, model-3's INT8 embedder and the ten XGBoost
boosters, and runs their jobs from ONE priority queue:

- **Priority.** A PR that no clerk will ever hear about (excluded-string
  headline, or no ticker in the universe) is scored after every PR a clerk is
  waiting on. Its row is still fully scored and written.
- **Threads, one per job, each ONNX session single-threaded** (`intra_op=1`).
  A headline is only 15–40 tokens; ORT's own intra-op threads barely speed
  that up (the INT8 embedding takes 14 ms on 1 thread, the same as on ORT's
  default pool), so N single-threaded jobs side by side do N times the work.
  ⚠ This is NOT the 5.3 finding "more threads make it slower" — that was
  measured on 270-token bodies, where one session's threads already scale.
- **CPU pinning.** The whole process runs on CPUs 1–3 and 5–7. Physical core
  0 (CPUs 0 and 4 — hyperthread siblings) is reserved for clerk-1.2, both IB
  Gateways and the connector (operator decision 2026-09-24).
- **Goldens.** At startup the engine scores 10 fixed headlines and compares
  every digit with `bench/o54_goldens.json`, written from 5.3's exact
  default-thread sessions. A thread setting that changed one output digit
  stops the process. Regenerate the file only with `bench/o54_bench.py
  goldens`, and only after a deliberate model change.

The layout is set by `ENGINE_*` / `INFER_CPUS` near the top of
`Orchestrator5.4.py` (or `--engine/--pools/--workers/--intra-op/--cpus`). It
was chosen by the burst benchmark (see "Latency"). A worker-process engine
(`--engine procs`) also exists and passes every check; it is the fallback,
not the default.

Measured 2026-09-24 (`bench/reports/O54_latency_2026-09-24_1426.md`), worst PR
of any of the 16 biggest historical bursts, reception to OK/BAD at the clerk:

| layout | worst burst PR | lone PR | memory |
|---|---|---|---|
| 5.3's (a pool per model, ORT default threads) | 423 ms (529 pinned) | ~77 ms | 1.5 GB |
| **threads/shared, 6 workers, intra_op=1 (shipped)** | **168 ms** | **~60 ms** | 1.7 GB |
| 6 worker processes (`--engine procs`) | 141 ms | ~60 ms | 10 GB |

⚠ Do not raise `intra_op` or go back to ORT's default thread pools "for
speed": 5.3's layout on model-3 misses the 400 ms budget, and the default
pools spin on every core, including the reserved core 0. The process engine
holds ~15 PRs inside 0.2 s under 400 ms where threads hold ~10; switch to it
(3 workers x `--intra-op 2`: 157 ms, 5 GB) only if bursts grow.

⚠ The worker processes use `PR_SET_PDEATHSIG`, which Linux fires when the
*thread* that started the worker exits. Start workers only from long-lived
threads (the main thread, or the worker's feeder). Starting them from
short-lived helper threads killed every worker right after startup.

## The gate

The gate rules live in `orchestrator_trigger_rules.txt`, read once at
startup. Edit it, run `--check-rules`, then restart for changes to apply.

⚠ **Since 2026-10-07 the gate is the one in "5.5: the priority gate and the
late stage".** The six rules below are the gate before that day. They are kept
in `orchestrator_trigger_rules.pre-20261007.txt` and are what 5.4 runs.

The pre-2026-10-07 gate has six rules. Any one firing means OK:

- **CORE** — all five must hold: FinBERT positive score high enough,
  FinMarBa "indecisive" score low enough, both keyword scores high enough,
  FLANG score high enough.
- **EARNINGS** — earnings PRs only: earnings score high enough, one keyword
  score high enough.
- **KwBodySolo** — fires alone on a very high keyword-body score.
- **FLANGSolo** — fires alone on a very high FLANG score.
- **FinBERTSolo** — fires alone on a very high FinBERT score.
- **XgbSolo** — fires alone on `ge50_m3 >= 0.646`, a model-3 score.

CORE, KwBodySolo, FLANGSolo, and FinBERTSolo only apply to a fixed list of
news types (M&A, Partnership, earnings, etc.). `XgbSolo` is the one
exception; it fires on any type.

The rules file works with ONE version at a time: it names `ge50_m3`, which
5.3 does not know, and 5.4 does not know 5.3's `ge50_m2`. Rollback to 5.3:
stop 5.4, copy `orchestrator_trigger_rules.5.3.txt` (5.3's exact file) over
`orchestrator_trigger_rules.txt`, start 5.3. (The rules file is not tracked
by git, so that copy IS the rollback — keep it.)

Warnings:
- ⚠⚠ **Never add a rule keyed only on FinMarBa's `indecisive` score.** A low
  `indecisive` means "confident," not "confident and positive" — it fires on
  bad news as easily as good.
- ⚠⚠ **`ge50_m3` is not a probability.** `0.646` is a rank cut, not 64.6%
  confidence. It is the cut that fires at the same rank model-2's `0.70` did
  on the live tape (the 88.8th percentile; `bench/o54_xgbsolo_cut.py`).
  Re-run that script as the 5.4 tape grows; never read a cut as a percent.

If the last rule that could still fire is blocked only by a blank score, the
log prints WARNING `BLOCKED BY MISSING SCORE ONLY` — a dead model, not bad
news.

## The XGBoost leg (model-3)

Every article is scored by 10 XGBoost models: 5 kinds (`se`, `q80`, `ge20`,
`ge35`, `ge50`) from two versions — `_m3` (held-out, the only one a rule may
read) and `_m3FT` (the same 25 features refit on every row; no honest
performance number). The models are in `../training_data/model-3/configs/`.

model-3 reads the headline only. Its one model call beyond the gate's own
three headline models is an INT8 FinBERT embedding of the headline, projected
onto model-3's own PCA basis: ~14 ms. model-2 (5.3) made seven calls
including two on the body, ~620 ms. Same accuracy on the validation data.

- Only the booster(s) a rule reads run before the decision (today:
  `ge50_m3`, 47 trees). The other nine run after the send.
- ⚠ **One text per embedding call, never batched.** The INT8 model's output
  depends on what else is in the batch.
- ⚠ PCA values are rounded to 6 decimals — the training table's precision.
- ⚠ The model files are checked byte for byte (md5) at startup. If they
  change, retrain — do not patch.
- The leg needs 12 columns that exist only in the newest universe file (147
  columns). ⚠⚠ **Do not restart between about 20:30 and 21:35 ET** — the
  nightly universe file is mid-build then, and a restart fails.
- Never add the borrow columns (`CTB_*`, `Borrow_*`, `SqueezeScore`) as
  features: they are empty in every live universe file.

## 5.5: the FM3 shadow lane

FM3 is the PR-text scorer built in `../tools/keyword/model-FM3/`. 5.5 scores
it on every row and writes it to the TSV. No rule reads it: a rule that names
an FM3 column fails `--check-rules` ("unknown field").

- **Two twins, 11 new columns (73 in all).**
  - `FM3_bd_ge50 FM3_bd_reg FM3_hd_ge50 FM3_hd_reg` come from `R1/DEVfit`, the
    held-out model with honest TEST numbers.
  - The same four with `_FT` come from `R1_FT`, trained on every row. `_FT` has
    no held-out score and its own scale.
  - Plus `FM3_status`, `FM3_ms` and `FM3_at`.
  - ⚠ These are ranking scores, not probabilities, and can be negative. Cut each
    twin by rank, with its own cut.
- **Its own process, after the decision.** One worker subprocess
  (`o55_fm3_lane.py`) holds both twins. It is pinned to CPUs 1-3,5-7 at nice 19.
  - The job starts after the row's STEP-1b.
  - One writer thread waits for FM3, at most 30 s, then writes the row.
  - Collectors never wait on FM3, so it cannot delay a decision.
- **Fail-open.** A dead or slow lane leaves the FM3 cells blank, with the reason
  in `FM3_status`. Trading continues.
- **Checks.** `Orchestrator5.5.py --check-fm3` loads the lane, runs the goldens,
  times 200 tape PRs, then exits. `--no-fm3` runs 5.5 without the lane.
  - The goldens file is `bench/o55_fm3_goldens.json`. Regenerate it with
    `o55_fm3_lane.py goldens`, and only after a deliberate FM3 model change.
- **Thresholds.** Run `bench/o55_fm3_cut.py` for starting cuts ("fire as often as
  kw15 does today"), and `bench/o55_fm3_cut.py --live-parity` after a live day
  (live scores must equal offline).
  - The `_FT` cuts are provisional until ~5 live sessions exist, because its own
    scores on the tape up to 09-24 are in-sample.
- **The quiet gate.** An FM3 job starts only after no PR has entered scoring
  for 300 ms and the engine queue is empty, or after at most 20 s.
  - Without it, a burst's later PRs shared CPU cores with the FM3 work of its
    first PRs. nice 19 cannot prevent that: the two run on hyperthread siblings.
  - Measured 2026-09-27: burst p99 went 152 → 220 ms without the gate.
  - ⚠ Do not remove it.
- **Switch at a day boundary.** Switching 5.4 ↔ 5.5 mid-day rotates that day's
  rows into `news_output_<D>_rotated.tsv`, which the 20:05 dailyStats cron does
  not read. Merge that file by hand.
  - Since 2026-09-28, 5.5 upgrades the day file in place instead when the new
    header only ADDS columns: 5.4 → 5.5 and old-5.5 → 5.5. It rotates only when
    columns go away: 5.5 → 5.4.
- **Latency.** `bench/reports/O54_latency_2026-09-27_1123_fm3gate.md`: 5.4 and
  5.5 interleaved, 2 repeats, on the 328 burst PRs.
  - 5.4: p50/p90/p99 60/93/199 ms. 5.5: 62/96/171 ms.
  - Each burst's worst PR, 5.5 minus 5.4: −1.7 ms (95% CI −17 to +14).
  - Lone PRs: 55 vs 54 ms. 0 missing decisions, and FM3 ok on every row.
  - ⚠ One "burst max" is noisy: 5.4 alone ranged 160–272 ms across runs. Compare
    distributions and paired bursts, not a single max.
  - Re-run with `bench/o54_bench.py sweep --configs C1a --orchs
    Orchestrator5.4.py,Orchestrator5.5.py`. It needs the live orchestrator stopped.

## 5.5: kw15_FT (since 2026-09-28)

kw15_FT is today's two kw15 pipelines (body + headline), refit with the same
recipe on EVERY labelled row: 5,668 PRs, 2026-05-04 → 09-24. Today's kw15 saw
only 2,117 rows of 05-04 → 06-18, because `keyword_finder.py` always holds out
20%. The models live in
`../tools/keyword/model_trained_on_2026-05-04-2026-09-24/`, trained by
`kw15_ft_train.py`.

- **Scored beside kw15, before the decision.**
  - Where: `o55_kw15ft.py` subclasses the engine's `ModelSet` / `ThreadEngine`,
    so `o54_engine.py` and 5.4 are untouched.
  - How: the kw15 task runs today's two pipelines exactly as before, then the
    two `_FT` ones on the same prepped strings.
  - Cost: +~2 ms per article on the kw15 thread (p95 2.5 ms).
- **Two new columns (75 in all), next to kw15's:** `kw15_proba_bd_FT` and
  `kw15_proba_hd_FT`.
  - They are probabilities like kw15's, but on their own scale.
  - ⚠ There is NO held-out score (it trained on every row). Take its cuts from
    live data, never from kw15's.
- **Rule-addressable, unused.** Both are in `_TRIGGER_FIELDS`, so moving the gate
  to them is a rules-file edit. No rule reads them today.
- **Fail-open while no rule reads it.**
  - Startup check: the engine must reproduce the trainer's
    `reference_scores.json` exactly (43 rows). Any mismatch or missing file turns
    `_FT` off: the cells stay blank, an ERROR is logged, and trading goes on.
  - If a loaded rule reads an `_FT` field and `_FT` is not ok, 5.5 refuses to
    start (the XGB leg's policy).
- **Thread engine only.** Under `--engine procs` the `_FT` cells are blank.
- **Checks.**
  - `--check-kw15ft`: goldens, the reference parity, and kw15 time on 200 tape
    PRs (~2 s).
  - `--no-kw15ft`: runs without it.
  - Log lines: `[kw15_FT] ready — ok (...)`, and `[KwScore] ... | _FT bd=… hd=… in X ms`.
- **In-place header upgrade** (in `_append_to_tsv`). When today's file has an
  older header that only lacks some columns:
  - It is rewritten with the new header; the old rows keep every cell, and the
    new cells are blank.
  - The rewrite is verified cell for cell before an atomic `os.replace`.
  - The original is kept as a hidden hard link, `.news_output_<D>.pre-upgrade-HHMMSS.tsv`.
  - Any other header change, or any error, still rotates.

## 5.5: kw_S4 (since 2026-09-29)

Two more keyword pipelines, each a single channel at its own threshold, refit
on every labelled row like kw15_FT — but NOT the same model as kw15_FT: two
independent single-channel fits (`fromPR_High(%) >= 30` on the headline,
`>= 50` on the body), not a head/body pair of one threshold. The models live
in `../tools/keyword/S4_model_trained_on_2026-05-04-2026-09-24_val7030/`
(`kw30_tfidf_S4_FT_hd/`, `kw50_tfidf_S4_FT_bd/`), trained by `s4_ft_train.py`.
Score-only: **no gate rule reads them.**

- **Scored beside kw15/kw15_FT, before the decision.**
  - Where: `o55_s4kw.py` subclasses `o55_kw15ft`'s `ModelSet` / `ThreadEngine`
    (which themselves subclass o54_engine's, unchanged), so `o54_engine.py`
    and `o55_kw15ft.py` are untouched.
  - How: the kw15 task runs kw15 + kw15_FT exactly as before, then
    `kw30_tfidf_S4_FT_hd` on the same prepped headline and
    `kw50_tfidf_S4_FT_bd` on the same prepped 400-word body string.
  - Cost: +~2-4 ms per article on the kw15 thread (p95 ~3 ms; the bd model's
    vocab is far larger than kw15's, so it is the slower of the two).
- **Two new columns (77 in all), next to kw15/kw15_FT's:**
  `kw30_tfidf_S4_FT_hd` and `kw50_tfidf_S4_FT_bd`.
  - They are probabilities like kw15's, but each on its own scale.
  - ⚠ There is NO held-out score (both trained on every row). Take cuts from
    live data, never from kw15's or each other's.
- **Rule-addressable, unused.** Both are in `_TRIGGER_FIELDS`, so moving the
  gate to one is a rules-file edit. No rule reads them today.
- **Fail-open while no rule reads them.**
  - Startup check: the engine must reproduce `s4_reference_gen.py`'s
    `reference_scores.json` exactly (43 rows) — neither model shipped with one
    (nothing loaded them live before this). Any mismatch or missing file turns
    S4 off: the cells stay blank, an ERROR is logged, and trading goes on.
  - If a loaded rule reads an S4 field and S4 is not ok, 5.5 refuses to start
    (the XGB leg's policy).
- **Thread engine only, and only when kw15_FT is also running.** S4 rides on
  the kw15_FT `ModelSet`/`ThreadEngine`: `--engine procs` or `--no-kw15ft`
  disable S4 too (documented limitation — there is no kw15_FT-off/S4-on
  combination). `--no-s4` alone leaves kw15_FT running.
- **Checks.**
  - `--check-s4`: goldens, kw15_FT's reference, S4's own reference parity, and
    kw15 task time on 200 tape PRs (~2 s).
  - `--no-s4`: runs without it (kw15_FT unaffected).
  - Log lines: `[S4] ready — ok (...)`, and `[KwScore] ... | S4 hd=… bd=… in X ms`.
- Regenerate the reference only after a deliberate model change:
  `/home/tom/venv/bin/python ../tools/keyword/s4_reference_gen.py`.

## 5.5: the priority gate and the late stage (since 2026-10-07)

**The gate** (`orchestrator_trigger_rules.txt`, operator's cuts):

```
REQUIRE ClassAlive: FineClass not in __CLASSIFIER_DEAD__      (any news type)
Core:   kw15_proba_hd_FT >= 0.570 AND ge35_m3FT >= 0.619 AND se_m3 >= 14.76 AND FM3_bd_ge50_FT >= -0.678
SeSolo: se_m3 >= 36.6
Combo:  FM3_bd_ge50_FT >= 1.6 AND kw15_proba_bd_FT >= 0.70
KwBodySolo: kw15_proba_bd_FT >= 0.82      (added 2026-10-07; a probability, 0 to 1)
FM3Pair: FM3_hd_reg_FT >= 3.4 AND FM3_bd_ge50_FT >= 1.6   (added 2026-10-07; ranking scores)
```

- ⚠ `se_m3` is in percent. `ge35_m3FT` and `FM3_bd_ge50_FT` are ranking scores,
  not probabilities. Three fields come from full-refit models, so the cuts are
  in-sample: watch the live fire rate.
- Replay of 2026-09-29..10-07 (8 sessions): 45 fires (Core 35, SeSolo 8,
  Combo 12) where the old gate fired 92.

**Only 6 models run before the OK/BAD:**
- FinBERT, FinMarBa, FLANG. No rule names them, but model-3 reads them as
  features, so they cannot wait.
- model-3: the embedding + only the boosters a rule reads (`se_m3`, `ge35_m3FT`,
  one engine task; two parallel tasks measured slower).
- kw15_FT: engine task `kw_ft` (the `_FT` pair only, ~2 ms).
- FM3's `FM3_bd_ge50_FT` and `FM3_hd_reg_FT` (the latter since the FM3Pair rule): the **FM3 priority worker** (`o55_fm3_lane.Fm3PrioLane`),
  its own process, nice 0, no quiet gate, ~1.2 ms, sent at reception.
  `o55_fm3_lane.py check-prio` checks it (also run by `--check-fm3`).
- FineClass still comes from the feed thread (regex).

**One decision deadline**: on an armed row, every gate input is waited for at
most `DECISION_DEADLINE_MS` = 350 ms after reception (`--decision-deadline-ms`).
A score that misses it reads MISSING, logs `[Deadline]`, and only the rules
that need it lose. Its cells are filled later.

**The late stage** (`o55_late.py`). The row is written right after the decision.
- Late models: plain kw15 + S4 (engine task `kw_rest`), the earnings score, the
  other 8 boosters, the FM3 shadow lane (all 8 FM3 cells).
- They run `LATE_DELAY_SEC` = 300 s after reception (`--late-delay-sec`), one
  article at a time, and only when no PR has entered scoring for 2 s and the
  engine queue is empty (at the latest 600 s).
- Their cells are filled by rewriting the day file in place: under `_tsv_lock`,
  checked cell for cell, then `os.replace`. Fills are batched per burst.
- Ctrl+C runs all pending late jobs at once (up to 60 s) before stopping.
- ⚠ A rule may not name a late field (`kw15_proba_bd/hd`, the S4 pair,
  `EarningScore`): startup refuses it, because it would always read blank.

**TSV: 79 columns** (+ `Late_status`, `Late_at` at the end).
- `Late_status`: `pending` (not filled yet, or the process died first), `ok`, or
  `partial` (a late model failed; its own status column says which).
- Right after the decision, `Xgb_status` is `pending` and `FM3_status` is
  `pending`. They get their final value from the late stage.
- `FM3_bd_ge50_FT` is the value the gate read. The shadow lane must give the
  same number; if not, the log shows ERROR `PRIORITY/SHADOW MISMATCH`.
- ⚠ A reader that needs the late cells must skip `Late_status=pending` rows.

**Measured 2026-10-07** (`bench/reports/O54_latency_2026-10-07_1048_prio1007.md`,
old vs new 5.5, 2 repeats): every real-burst PR under 400 ms, 0 missing decisions.
- Burst p90 99 ms (old 107). Lone PR 64 ms (same as old).
- 15 PRs at once peak at 315 ms (old 540).
- The worst burst PR was 277 ms (old 197). That is one PR, where model-3's
  feature build took 143 ms. The rules now read both model groups (m3 and m3FT),
  so each PR builds features twice. That build is Python-heavy and slows down
  when many PRs arrive together.

**Log lines:** `[FM3p] ready`, `[Late] stage ready`, `[KwScore] ... _FT`,
`[KwRest] ... (late)`, `[Late] SYM id=...: ok in N ms`, `[Late] filled N row(s)`.

## Latency

The operator's budget (2026-09-24): from reception to the OK/BAD sent to the
clerk, **under 400 ms for every PR**, including the worst bursts on record
(17 PRs in 2 s; 7 in 0.2 s). The TSV column `Decision_ms` is that number per
row; the log prints `[Decision] SYM OK|BAD sent N ms after reception`.

The clerk's ARM reply (~43 ms median) is a floor: the decision is sent only
after the arm is confirmed.

Measure with the bench (it needs the live orchestrator STOPPED — it refuses
otherwise):

```bash
/home/tom/venv/bin/python bench/o54_bench.py parity --config C1a   # outputs identical to 5.3's sessions?
/home/tom/venv/bin/python bench/o54_bench.py sweep                 # all layouts, ~45 min -> bench/reports/
/home/tom/venv/bin/python bench/o54_xgbsolo_cut.py                 # the XgbSolo cut + gate tape check
/home/tom/venv/bin/python bench/o54_xgbsolo_cut.py --live-parity   # after a live day: live == offline?
```

The sweep runs the REAL `Orchestrator5.4.py` against a fake connector feed
(replaying the biggest historical bursts at their recorded timing) and a fake
clerk (answering like the real one). Results: `bench/reports/O54_latency_*.md`.

`perf_mode.sh on|off|status` switches the CPU to its performance profile
(needs sudo). Measured 2026-09-24: typical decisions ~9 ms faster, the worst
burst unchanged (`bench/reports/O54_latency_2026-09-24_1711_perf.md`). Not
needed for the budget, so not scheduled; the box runs the `balanced` profile.

## Output: the daily TSV

One file per day: `tables/news_output_YYYY-MM-DD.tsv`, one row per
in-universe ticker per article. 5.5 writes **79 columns** (since 2026-10-07):
- 5.4's 62;
- plus the 11 FM3 columns at the end;
- plus `kw15_proba_bd_FT` / `kw15_proba_hd_FT` right after `kw15_proba_hd`;
- plus `kw30_tfidf_S4_FT_hd` / `kw50_tfidf_S4_FT_bd` right after those;
- plus `Late_status` / `Late_at` at the very end (2026-10-07).

Readers go by column name, never position. (Since 2026-10-07 5.5 writes 79,
with `Late_status`/`Late_at` at the end. Before 2026-09-28 5.5 wrote 73;
before 2026-09-29 it wrote 75; 5.4 wrote 62 and 5.3 wrote 90.) Columns follow
`{Model}_{channel}_{hd|bd}`.

Key columns:
- `Trigger` — the gate's full reasoning, grouped by rule, each condition
  marked `PASS`, `FAIL`, `MISSING` (blank score), or `NA`. A leading `YES`
  means the row fired and traded; `NO` means it didn't.
- `ClerkSentiment` — fired rule name(s) joined by `_`, or `BAD`, or
  `skipped:...` if never armed.
- `Decision_ms` — reception to OK/BAD sent. Blank if no decision was sent.
- `Xgb_status` — `ok`, `partial` (the gate's score came in time, the rest
  failed after), `late` (the gate's score missed the decision but arrived
  later), `disabled_universe`, `no_embedding`, `timeout`, `not_loaded`, or
  `error`. Check this before trusting a blank XGBoost cell.
- `Xgb_gen` — which model version scored the row (`m3:int8_c200_s04`).

⚠ This TSV uses normal quoting (a quote mark in a field is escaped). Read it
the same way. Reading it the other way silently corrupts any `Body` cell that
contains a quote mark.

## Why the imports look strange

Some sibling scripts have hyphens in their filenames (`FinBERT-headliner.py`)
or aren't proper packages, so they are loaded by file path, not `import`.
The model loading lives in `o54_engine.py` (a normal module) because worker
processes and the bench must import it. Importing `Orchestrator5.4.py` itself
is safe: it starts no threads and writes no logs until `main()`.

Per-user settings (RTPR key, trading account, host, port) live in
`../config/n2_config_file.txt`. Never hard-code a path.

## Editing rules

- **Changing the gate is a text edit, not a code edit.** Edit
  `orchestrator_trigger_rules.txt`, run `--check-rules`, restart.
- Keep `_on_row_built` (feed thread) fast — heavy work belongs in the engine.
  The one exception is the PR classifier's 200-word body string and its class,
  both regex-only.
- ⚠ The PR classifier's body cap (`PRCLASS_BODY_CAP_WORDS = 200`) must not
  move: the classifier was tuned on it, and FineClass feeds both the gate and
  model-3.
- Don't remove a headline model because model-3 stopped reading it — check
  the rules file first; FinBERT, FinMarBa and FLANG all feed live rules.
- Don't drop the `.1` suffix from any XGBoost feature name
  (`Prosus_ss_hd.1`, etc.) — those are the exact trained names.
- A missing, broken, or empty rules file must fail the process at startup.
  Never add a fallback gate.
- Score the keyword and earnings models once per article, on the full body
  text; the headline models on the plain headline. Scoring anything else
  breaks live-matches-offline-replay.
- Logs: `logs/Orchestrator5.5_YYYY_MM_DD.log` (5.4 wrote `Orchestrator5.4_...`).
  The FM3 lane's worker lines appear there as `[FM3w]`, its per-row lines as `[FM3]`.
