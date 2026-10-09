# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this directory is

One script, `PrClassifier.py`: a regex catalyst-classifier that tags each press
release with `FineClass` (~21 specific classes) + `BroadClass` (11 rollups).
Pure stdlib, no model files, no state.

**It is BOTH a batch research tool AND a live-chain dependency.** Read the
"Live consumer" section before touching `RULES` — an edit here changes what the
live orchestrator trades.

## Interpreter

`/home/tom/venv/bin/python` (repo-wide rule). This script itself is stdlib-only
and would run under system python3, but don't — stay consistent with the tree.

## Commands

```bash
# Batch: append FineClass/BroadClass to a PRstats TSV -> <stem>_Classed.tsv,
# then print per-class counts (fine, in rule order; broad, by frequency).
/home/tom/venv/bin/python PrClassifier.py --input PRstats_....tsv [--output out.tsv]

# The LIVE (Orchestrator5.3) body window instead of the 1500-char one. Writes the
# standard FineClass/BroadClass, so an already-classed file is relabelled IN PLACE
# at its own two columns and the schema is unchanged -> drop-in for the XGBoost
# training pipeline, which reads those names:
/home/tom/venv/bin/python PrClassifier.py --input X.tsv --output X_reCat.tsv --body-200w

# ...or into differently-named columns, when both labellings should sit side by
# side in ONE file for comparison (remember to drop one before training):
/home/tom/venv/bin/python PrClassifier.py --input X.tsv --output X_cmp.tsv \
    --body-200w --fine-col FineClass_200w --broad-col BroadClass_200w
```

No test suite. Verification = run it on a PRstats TSV and eyeball the printed
class histogram + the `N classified via body fallback` count against the
previous run; spot-check `Other` rows and any class whose count jumps.

## How the classification works

- `RULES` is an **ordered** list of `(FineClass, BroadClass, pattern)`; **first
  match wins**, so priority is encoded purely by list position — strongest /
  most specific catalyst first. Reordering two rules silently reassigns every
  PR that matches both. Examples of the ordering that is doing real work:
  `FDA_Approval` before `FDA_Designation`; `Earnings_Date` before
  `Earnings_Results` (see below — this one is subtle and load-bearing);
  `Regain_Compliance` before `Deficiency_Notice`; `MA` above everything
  commercial (so a merger PR that also says "definitive agreement" and
  "partnership" lands in MA); `Legal` near the bottom — which is safe only because
  its pattern is wide enough to claim a litigation notice on the HEADLINE; when it
  was not, those PRs fell to the body and were labelled by the issuer's business
  (see the `Legal` entry under the gotchas).
- Two **headline-only** lists bracket `RULES` when the caller passes
  `headline=True`:
  `COMPILED_HEADLINE = HEADLINE_FIRST_RULES + RULES + HEADLINE_LAST_RULES`.
  `FIRST` outranks every shared rule (it holds the scheduling rule, which must
  beat `Earnings_Results`); `LAST` is the fallback nothing else claimed. Both are
  headline-only because they trade precision for recall — safe on one curated
  sentence, measurably unsafe on 1500 chars of body prose. See the gotchas.
- No rule hits → `("Other", "Other")`, ~13% of the corpus. Do not confuse that
  with the **body-decided** rate, which is **~26%** (1134 of 4376). An earlier
  version of this file quoted "~11%" as the body-fallback baseline; that was the
  `Other` rate and the comparison it was used for was wrong by 2.4x.
- `classify()` runs `normalize()` first: NFKC + whitespace collapse (wire
  headlines carry NBSP between words, which used to defeat the literal spaces in
  the patterns) and deletion of the non-M&A senses of "acquisition" via
  `NONDEAL_ACQ`. Deleting the phrase beats vetoing the MA rule, because a PR can
  legitimately mention customer acquisition *and* announce a merger.
- `classify_row` tries the **Headline first** (with `headline=True`), then the
  **first `BODY_LEAD_CHARS` (1500) characters of the body** as fallback (without
  it). The lead-only window is load-bearing: about-us / safe-harbor / law-firm
  footers deeper in the body are full of false triggers ("designation",
  "forward-looking", "clinical-stage"). Never widen it casually.
- Body column is read as `body` then `Body` (corpus files use both).
- **`--body-200w` is the live window** (2026-09-19). It replaces that slice with
  `body_prep.prep_body(clean_inline(Body), Headline, BODY_PREP_WORDS=200)` — the
  identical string `Orchestrator5.3._prep_body_200` hands its body encoders, its
  embedder **and** its copy of this classifier. `classify_row(row, body_text=...)`
  takes the caller's string verbatim; `body_text=None` is the historical path,
  byte for byte, which is why the flag is opt-in and old `_Classed` files stand.
  `body_prep` is path-loaded **inside `main()` only** — it drags in
  `keyword_finder` → pandas + sklearn, which must never reach the orchestrator's
  import of this module. `_clean_inline` is a deliberate copy of
  `RTPR_connector3`'s `clean_inline_text` (same reason: no non-stdlib imports at
  module level); it is idempotent on an already one-lined TSV cell.
  ⚠ Counter-intuitive and measured: 200 words (~1300 chars) matched **more** rows
  than 1500 chars (body fallback 1224 → 1251 on the 4804-row 2026-08-28 corpus),
  because `clean_body` first removes the wire dateline, the verbatim headline
  repeat and the tracker URLs, so the window reaches further into real prose.
- `--fine-col` / `--broad-col` rename the two output columns. The "append only if
  absent" header rule applies to whatever names are given, so re-classifying an
  already-classed file into new names is safe.

### The body path is NOT the headline path (`COMPILED_BODY`)

Added 2026-08-14. `classify(headline=True)` iterates `COMPILED_HEADLINE`;
`classify()` (the body-lead call) iterates **`COMPILED_BODY`**, which is built
from `RULES` with two modifiers:

- **`BODY_EXCLUDED_CLASSES`** — the class is never assigned from body text.
  Currently **two members**, `{"Earnings_Results", "Crypto_Treasury"}`.
  - `Earnings_Results` is there because that class gates real money: its two
    paths had opposite quality, 612 headline-decided rows near clean versus
    **7 of 9 body-decided rows wrong**. It is not fixable by qualifying the
    regex — the bodies of a law-firm notice and a 20-F filing genuinely do
    contain the phrase "first quarter 2026 financial results".
  - `Crypto_Treasury` was added **2026-08-18** on the strongest reading of the
    body-decided tell this file has produced: of its **41 body-decided rows, all
    41** had a headline with no crypto vocabulary at all (48% body-decided share
    against the ~26% corpus baseline). The rule is also **direction-blind** —
    `digital asset` reads *"orderly exit from Digital Asset Treasury strategy"*
    exactly as it reads an adoption, which is how AIxC's robotics-pivot PR
    (2026-08-18) became `Crypto_Treasury`. Excluded rather than tightened
    because there was no good half of the body path to keep. The class went
    **86 → 45**; the 41 redistribute as Other +16, Partnership +10,
    Contract_Award +6, Product_Launch +4, and five singletons.
  ⚠ The **headline** path is still direction-blind: a headline that says the
  issuer is *exiting* a Digital Asset Treasury still lands in `Crypto_Treasury`.
  Harmless while the class is outside the O5.2 `ClassWhitelist` — but it is a
  precondition to check before anyone whitelists it. Measured, not guessed;
  don't re-derive it.
- **`BODY_OVERRIDES`** — the class uses a *stricter* pattern over body text
  (the four FDA/Clinical rules, and since 2026-09-19 `Legal`). The headline pattern stays loose,
  because on one curated sentence a bare "Phase 2" **is** the news.

**Why the split exists at all.** A headline is one sentence and every word in it
is about the news. 1500 chars of body are not: they carry the issuer's own
description of itself, so a rule that reads a self-description as a catalyst
labels the PR with what the company **is** rather than what it just **did**.
Measured on the 4376-row 2026-08-13 corpus before the fix: of 155 FDA/Clinical
rows decided by the body, **52 had a headline with no clinical vocabulary at
all** — an oil & gas driller and a mining company on "Phase 1 of the drilling
program", an IT-services firm on "Phase 2", a logistics company on "top-line", a
board appointment on "rare pediatric", and two Nasdaq-compliance PRs on "Phase 3"
and "NDA".

⚠ **If you add a rule, ask which path it is safe on.** `_BODY_CATALYST` (a verb
that means the issuer DID something) and `_BODY_CLIN_CTX` (clinical context for
tokens that are ordinary English outside biotech) are the two building blocks.

⚠ **A body-only window is not an alternative.** Measured and rejected: correct
and incorrect body matches sit at statistically indistinguishable offsets (median
598 vs 872 chars), so every cut from 200 to 1000 chars discards good and bad rows
in nearly equal proportion. The signal is *what* matched, never *where*.

### The six earnings rules (do not reorder)

**`Earnings_Results` is a HEADLINE-ONLY class** (it is in
`BODY_EXCLUDED_CLASSES` — see above). Every rule below therefore only ever fires
on a headline; the body path cannot assign this class at all.

1. `Earnings_Date` in **`HEADLINE_FIRST_RULES`** — the scheduling rule, outranks
   everything. Fires only when all three hold: scheduling vocabulary
   (`schedules`, `release date`, `reporting date`, `conference call`, `webcast`,
   `to hold/discuss/host`…) **AND** an earnings topic **AND** no genuine-results
   evidence. See the two sub-points below — both are load-bearing.
2. `Earnings_Results` (fiscal-period print) in **`HEADLINE_FIRST_RULES`**, second
   — the only rule that outranks `Clinical_*`. See the sub-points below it.
3. `Earnings_Results` (period + hard number) in **`HEADLINE_FIRST_RULES`**, third
   — the same print reported as a *number* instead of as "results". It exists
   only to beat the commercial block: COSM *"Reports Preliminary Record Q2 and H1
   2026 Revenue, Up Over 30% YoY; Continues Open Market **Repurchases**"* was
   claimed by `Buyback_Dividend` on one word. Requires a fiscal period **AND** a
   hard number **AND** a revenue noun bound to a reporting verb, and carries the
   same `_RESULTS_MA_VETO` as rule 2 (anything in `HEADLINE_FIRST` outranks `MA`).
   Measured to take **zero** rows from `Earnings_Date`.
4. `Earnings_Date` in `RULES` — the tense test (`to report` / `will report` /
   `announces the date`). Its noun group is earnings vocabulary **only**; adding
   `call|webcast` back makes "to Host R&D Webcast" an earnings class.
5. `Earnings_Results` (narrow) — an explicit present/past "reports … results".
6. `Earnings_Results` (widened, in `HEADLINE_LAST_RULES`) — results reported
   without the word "results" ("Reports Earnings", "Achieves Record Revenues in
   July"). **Deliberately covers non-periodic (monthly, preliminary) revenue
   prints**, so the class — and the live O5 earnings branch — is wider than
   quarterly filings. Two boundary bugs fixed 2026-08-14: `\bprofit\b` could not
   match *"Profitability"* (AERT, LSAK fell to `Other`), and the growth verbs
   lacked `triples|doubles|surges|jumps` (KSCP *"Q2 Revenue **Triples** to
   Approximately $9M"*).

⚠ **A seventh branch was built and REMOVED the same day**: `(reports|announces)
… \d+% … (growth|increase)`, for the number-first shape. A percentage next to
`growth`/`increase` does **not** make a headline financial — it put five
non-earnings PRs into the live-gated class (BNGO *"56% Increase in
**Publications**"*, AIFF *"291% Increase in … **Brain Scan Volumes**"*, ICCM
*"70% Growth in **Install Base**"*, MFM *"25% Increase in Monthly
**Distribution**"*) to gain one genuine row. **The growth verb must attach to a
financial noun, never to a bare number.**

Rule 2 exists because the `Clinical_*` rules sit above Earnings in `RULES`, so a
biotech that headlines the quarter **and** the pipeline in one line — *"Agenus
**Reports First Quarter 2026 Financial Results** and Highlights … Phase 3
Development"* — landed in `Clinical_Update` and silently lost the live O5
earnings branch. Three things in it are load-bearing:

- **The verb group is `reports|reported|posts|posted`, with no optional `s`.**
  `reports?` also matches the bare infinitive in *"X **to Report** First Quarter
  Results on May 13"*, which swallowed **98 scheduling PRs** into the live-gated
  class on the first attempt. `announces`/`releases` are excluded for the same
  reason — `releases?` matches the *noun* in "Announces **Release** Date".
- **A fiscal period is required.** That is what leaves clinical readouts alone:
  "Announces Positive Topline Results from Phase 2" has no period. 17 such rows
  are why the Clinical rules must otherwise stay above Earnings.
- **The MA veto** (`_RESULTS_MA_VETO`). Anything in `HEADLINE_FIRST_RULES`
  outranks `MA`, the highest-lift class (25.6% vs 9.7%), so a headline carrying a
  hard deal token is handed back: *"Files Preliminary Proxy Statement for
  **Business Combination** …; Provides Business Update and Reports Q1 Results"*.

Two guards inside rule 1 that look optional and are not:

- **The genuine-results veto** (`_SCHED_GENUINE`) is the only thing protecting the
  90-row *"**Reports** Q2 Results **and** Announces Conference Call"* family. It
  exempts a reporting verb bound to a financial noun, the `quarterly|unaudited|
  abridged|preliminary results` phrases, and **any hard number** (`\d+%`, `$\d`) —
  a headline quoting "34% Rise in Revenue" is publishing figures, not booking a
  date. Weaken it and those rows silently leave the live earnings branch.
- **The fiscal-period qualifier** on bare "results" (`_SCHED_TOPIC`) keeps
  clinical readouts out: *"to Host Webcast … to Discuss **Topline Results** from a
  Pivotal Trial"* has `results` but no period, so it stays `Clinical_*`.

`Earnings_Date` appears **twice** and `Earnings_Results` **four times** across
the rule lists; `main()` de-dups the printed summary with `dict.fromkeys`.

### The four FDA/Clinical rules

Ranked `FDA_Approval` → `FDA_Designation` → `Clinical_Data` → `Clinical_Update`,
and all four sit **above `MA`** and above the Earnings rules. That ordering is
deliberate but it is also the main thing to check when one of these classes looks
too big — the one place it was measurably wrong (biotechs printing quarterly
numbers) is now bridged by rule 2 of the earnings block above, **not** by
reordering these.

- `FDA_Approval` deliberately holds **submissions as well as approvals**
  (`nda|bla (submission|filing|accept)`) — it is "a marketing-pathway event", not
  strictly a granted approval. But a **pathway is not an event**: bare `510(k)`
  was 20% one issuer, because INBS published seven *study milestones* whose only
  FDA content was a future filing (*"Commences Method Comparison Study **for** FDA
  510(k) Submission"*, *"Achieves Key Milestone **Ahead of Planned** FDA 510(k)
  Submission"*) and they sat beside real clearances. Since 2026-08-14 the token
  must be bound to a grant/receipt (`clearance|approval|granted|receives`), and a
  leading veto drops the pathway-as-a-goal phrasings (`supporting|in support of|
  ahead of|strengthening|in preparation for` within 40 chars of the FDA token).
  ⚠ That veto is `^(?!…)` — it **must** be followed by `.*` before the alternation
  or the whole rule becomes anchored at position 0 and stops matching headlines
  entirely. This was written wrong once and silently moved every genuine clearance
  (APYX, CUPR, WALD) off the headline path.
- `Clinical_Data` means a **readout**; `Clinical_Update` is everything else
  clinical. The boundary is genuinely fuzzy — `Clinical_Data`'s phase branch
  requires an adjacent `data|results|trial|study`, so "Phase 3 Trial" is
  `Clinical_Data` while "Phase 2 **Clinical** Trial" is `Clinical_Update`. That
  split is an accident of adjacency, not a designed distinction. **Do not
  "fix" it by adding a window** — widening that branch to `[^.]{0,25}` moves 71
  rows and collapses `Clinical_Update` into `Clinical_Data`.

Three gaps here were **measured and deliberately not fixed** — re-deriving them
costs an afternoon, so don't redo the work:

- **Roman-numeral phases are invisible** (`phase [1-3]` only). Only 16 corpus
  headlines use "Phase III"-style, 12 of which already land in a Clinical class
  on other vocabulary. Adding roman numerals to the bare rule fixes 2 rows and
  breaks 2 (it captures "Phase I of Smart City Project" and "Phase II Qualifier
  of a DoD program"). **Net zero.**
- **The spelled-out "New Drug Application" is not matched** — only the `nda`
  abbreviation is. Adding it moves 3 rows: 1 right and 2 badly wrong, because it
  promotes *"Files **Investigational** New Drug Application"* (an IND, i.e. a
  trial start) and *"Receives **Complete Response Letter**"* (a **rejection**)
  into `FDA_Approval`. Only add it with an IND exclusion and a CRL veto.
- **There is no negative-regulatory class.** A Complete Response Letter, a
  clinical hold and a failed endpoint all land in `Clinical_Update`/`Clinical_Data`
  alongside good news. Corpus n is tiny (~6 headlines), which is why no
  `FDA_Rejection` class was added — but any *widening* of `FDA_Approval` must
  veto CRLs explicitly, since they cite the same application vocabulary.

## Live consumer — Orchestrator5.2 / 5.3 (⚠ read before editing RULES)

`orchestrator5/Orchestrator5.2.py` and `Orchestrator5.3.py` path-load this file with
`importlib.util.spec_from_file_location("PrClassifier", …)` and use:

- `classify` → aliased `classify_pr`
- `BODY_LEAD_CHARS` → `PRCLASS_BODY_LEAD_CHARS` — **5.2 only**. 5.3 stopped
  importing it on 2026-09-19: its body fallback is now the shared 200-word
  `_prep_body_200` string, i.e. the `--body-200w` path above. **The two live
  orchestrators therefore label differently** — 95 of 4804 corpus rows (2.0%), all
  of them headline-`Other` rows. Match the flag to the orchestrator you are
  reproducing, or a "rescore" silently compares two different things.

Neither calls `classify_row`; both re-implement the headline-then-body fallback
inline (their news dict keys the body as `body_raw`), including the
`headline=True` / body-without-it split. If you change `classify_row`'s fallback
logic, the orchestrator copy at `_classify_and_score_earnings` must be changed to
match or live diverges from offline rescores.

`FineClass == 'Earnings_Results'` is the first condition of the orchestrator's
**EARNINGS trigger branch** (`FineClass == Earnings_Results AND EarningScore >= 4
AND kw15_body >= 0.50`), one of five OR-ed branches in the live trade gate.
Consequences:

- Anything that moves a PR **into** `Earnings_Results` can cause real trades;
  anything that moves PRs **out** silently kills that branch for them.
- `FineClass` is written on **every** row of the orchestrator's TSV, so all
  classes show up in the live log/table even though only one gates trades.
- Orchestrator startup runs a **smoke check**: `classify_pr("Acme Corp Reports
  Second Quarter 2026 Financial Results", headline=True)` must return
  `Earnings_Results` or it **refuses to start**. Any `Earnings_Results` regex edit
  must keep that headline matching.
  ⚠ The `headline=True` was **added 2026-08-14** (`Orchestrator5.2.py:2104`). The
  check previously classified a headline through the *body* path, which was
  harmless only while the two paths agreed. Making `Earnings_Results`
  headline-only broke that assumption and the smoke check would have refused
  startup — the failure was in the check, not the rules. **If you ever add to
  `BODY_EXCLUDED_CLASSES`, re-audit every `classify_pr` call site in O5**; there
  are three (`:597` headline, `:599` body lead, `:2355` smoke — line numbers as of
  2026-08-18; they have drifted once already, grep rather than trust them). That
  re-audit was run for the `Crypto_Treasury` addition: only the body-lead site
  changes behaviour, and the smoke check is unaffected because it passes
  `headline=True` and asserts `Earnings_Results`.
- The module is imported once and its regexes are called concurrently from a
  worker pool — keep this file import-safe and thread-safe: module level stays
  stdlib imports + literals + `re.compile`, no mutable globals, argparse/IO only
  inside `main()` under the `__main__` guard.
- **A running orchestrator holds the old rules until restarted.**

## Downstream (batch) consumers

`_Classed.tsv` is a middle stage of the PR-stats research chain — typical
filename lineage: `PRstats_… → _11EncScored → _Classed → _BodyEnc/_kwScored →
_FromPRhigh`. Readers of the two columns:

- `price_stats_loader/statsByClaude/class_hitrate_ranking.py` — ranks classes by
  P(fromPR_High ≥ 30%) with Wilson CIs + BH-FDR.
- `class_conditional_predictors.py`, `analyze_frompr_predictors.py` — treat
  FineClass/BroadClass as categorical predictors.
- `merge_scores_by_id.py` / `merge_body_encoder_cols.py` — carry the columns
  across merges (body-side files lack them by construction).
- `tools/Earning_Scorer/` — its corpus is the `Earnings_Results` slice of a
  `_Classed` file.

⚠ **These numbers predate the 2026-09-19 `Legal` widening and the 200-word body
window**, both of which moved rows between classes (`Legal` 78 → 97, `Other` 666 →
647 on the 2026-08-28 corpus). Re-run `class_hitrate_ranking.py` before quoting them
as current; the shape below is still the right map of which classes lift.

Measured class predictiveness (regenerated **2026-08-18** after the
`Crypto_Treasury` body exclusion; **4248 rows** of the 2026-05-04→08-13 corpus,
base rate **12.8%** @ ≥30%, report at
`price_stats_loader/outputs/fromprhigh_class_hitrate_ranking_2026-08-18.md`):
**MA 26.4% (2.06x, n=387, sig+)** and **Partnership 21.2% (1.66x, n=368, sig+)**
on top; **Earnings_Date 2.1% (n=188), Legal 2.6% (n=78), Management_Change 3.4%
(n=208), Conference_Presentation 3.4% (n=145)** significantly negative.
`Earnings_Results` is **10.3% (n=604)**, not significant either way.
`Regain_Compliance` is **22.4% (1.75x, n=76)** — it was diluted before the
rule stopped requiring `regain compliance` to be adjacent. The FDA/Clinical
block: `Clinical_Data` 15.8% (n=146), `Clinical_Update` 9.7% (n=155),
`FDA_Approval` n=21 and `FDA_Designation` n=12 — both below the min-n cut and
**not rankable**. `Crypto_Treasury`, now headline-only, is **13.6% (1.06x,
n=44)** — flat, and its CI [6.4%, 26.7%] is too wide to say more. Rule edits
invalidate these numbers — re-run the ranking script after any material change.

The 2026-08-18 re-run barely moved the table: `Partnership` absorbed 10 rows and
`Contract_Award` 6 without either hit-rate shifting (21.2% and 17.0%), which is
the expected shape when a fix removes rows that were *mislabelled* rather than
rows that were *predictive*.

Note that class *purity* and class *predictiveness* move independently, in both
directions. Tightening `Clinical_Data` (2026-08-07) removed 6 misclassified rows
of which 3 happened to be movers, so its hit-rate *fell* 17.3% → 15.7%. Cleaning
the body path (2026-08-14) *raised* `Regain_Compliance` from a mixed bucket to
1.75x. Neither is a reason to revert or to celebrate: a mortgage broker does not
belong in a clinical class whether or not it ran, and the hit-rate table is
downstream of correctness, not a substitute for it.

## Gotchas

- TSV I/O is `QUOTE_NONE` with `escapechar=None` on both sides — matching the
  repo convention that bodies are pre-cleaned to a single line. A field
  containing a literal tab/newline raises at write time; that means the *input*
  is malformed, not this script.
- `csv.field_size_limit(sys.maxsize)` is required — full PR bodies exceed the
  default field limit.
- Input must have a `Headline` column; the script exits otherwise. Everything
  else is optional.
- Patterns are `re.IGNORECASE` and mostly unanchored substrings, so broad verbs
  leak: `Product_Launch` matches any "launch". They sit *below* the specific
  rules precisely so the specific ones absorb the overlap first — check what a
  new rule would steal before inserting it high.
- **Anchor every short token with `\b`.** This file shipped `rmat` unanchored for
  months; it matched info**rmat**ion / fo**rmat** / transfo**rmat**ive /
  A**rmat**a / Fo**rmat**ion, and because `FDA_Designation` ranks 2nd it stole
  from every class — **277 of its 292 corpus hits were that one bug** (fixed
  2026-08-07, class went 292 → 19). Same family, same day: `contract` matched
  *contractor/contractual*, `warrant` matched *warranty*, `award` matched
  *award-winning*, `patent` matched *patently*. Before adding a token, probe it
  against boilerplate ("For more information", "our platform", "award-winning",
  "safe harbor statement") — a rule that fires on those is broken.
- **`\b` is not enough — a multi-word phrase can still be ordinary English or a
  company's name.** The audit that followed the `rmat` fix found the same bug in
  phrase form, twice in the same rule: `fast track` and `priority review` are
  both plain business English, and **"Fast Track" is the registered name of a
  listed media company**. 8 of `FDA_Designation`'s remaining 23 hits (35%) were
  that one issuer's PRs plus a "Fast Track to ML-Safe Compression" product
  launch. A regulatory phrase must be made to carry its regulatory sense — pair
  it with `designation|status` or require `fda|ema|regulatory` nearby. When a
  rule's hits cluster on **one ticker**, suspect a name collision before you
  believe the class.
- **A missed headline becomes someone else's noise — the `Legal` case (2026-09-19).**
  The rule held the noun `investigation` but not *"Johnson Fistel **Investigates**"*,
  `deadline alert` but not *"ATRA **DEADLINE NOTICE**"*, and nothing for the notice
  grammar (*"Shareholders Are Encouraged to Reach Out to <firm>"*, *"Did X Insiders
  **Breach their Fiduciary Duties**?"*). Those headlines matched **nothing**, fell to
  the body, and came back as the issuer's business: every *"Halper Sadeh is
  Investigating Whether X is Obtaining a Fair Price"* was **`MA`**, AARD's notice was
  `Clinical_Data` on the biotech's own body, ANGI's was `Contract_Award` — i.e. a
  plaintiff's-firm PR sitting inside the O5 `ClassWhitelist`. Widening it moved **34
  rows in, all headline-decided, all genuine; 18 left the whitelist and ZERO
  entered**; no genuine catalyst PR was taken. `Legal` is measured at 2.6% vs a 12.8%
  base rate, so these were rows the gate paid to trade.
  ⚠ Two halves, and both matter: `investigat(?:e|es|ed|ing|ion|ions)\b` is spelled
  out because the old unanchored `investigation` matched **investigation**AL — the
  `rmat` bug again, worth 8 wrong `Legal` labels on biotech/mining bodies (TRAW,
  ADGM, OPTH, PBM, SURG…). **Never relax it to `investigat\w+`.** And the whole
  investigate-family plus the notice grammar is headline-strength only, so
  `BODY_OVERRIDES["Legal"]` keeps the body path on litigation **nouns**: over prose a
  study that "investigated" an endpoint, an SEC matter and a short-seller reply all
  fire.
- **A self-description is not a catalyst.** `clinical-stage` was removed (not
  tightened) for this reason: it sits in the About paragraph of essentially every
  biotech PR, so through the body fallback it stamped `Clinical_Update` on
  partnerships, term sheets, advisory-board appointments and investor-forum
  notices. The same trap is why bare `efficacy` (safe-harbour furniture) and bare
  `dosing`/`enrollment` ("Open **Enrollment** Customers", "Medicare
  **Enrollment** Approval") now require an object. Signature to watch for: an
  FDA/Clinical class whose **body-decided share is far above the ~26% corpus
  rate** — before the 2026-08-07 fix `FDA_Approval` was 52%, `Clinical_Update`
  46%. Removing single tokens turned out to be **treating the symptom**: the
  disease is the body path itself, which is why `COMPILED_BODY` now exists.
  A second, blunter tell, cheap to compute and worth running on any class that
  looks wrong: **take the class's body-decided rows and check how many have a
  headline containing no vocabulary of that class at all.** For the four
  FDA/Clinical rules that was 52 of 155, and it is how the oil & gas driller, the
  mining company, the logistics firm and the board appointment were found.
- **Never move a headline-only rule into `RULES`.** Those rules run over 1500
  chars of body prose there, and both were measured doing damage. The widened
  earnings rule: 52 headline hits (nearly all genuine) vs 31 body hits (nearly
  all wrong — law-firm notices, analyst coverage, a monthly cash distribution),
  all promoted into the class that gates real money in O5. The scheduling rule:
  18 wrong body captures including a securities lawsuit, an investor day and an
  annual mining report.
- **A rule can be too literal as easily as too loose, and the failure is
  invisible** — the row does not land in a wrong class loudly, it falls to the
  body and gets classified by the issuer's business. `Regain_Compliance` required
  `regain compliance` **adjacent**, so *"Annovis Bio **Regains NYSE Listing
  Compliance**"* and *"Jaguar Health **Compliant with** Nasdaq Bid Price Rule"*
  both missed, fell through, and came back as `Clinical_Update` on "Phase 3" and
  "NDA". When auditing, read the `Other` bucket **and** the body-decided rows of
  unrelated classes — a recall gap in rule X shows up as noise in rule Y.
- **`milestone` was 90% of one rule's body hits.** Bare `milestone` (in
  `Business_Update`) fired on 71 of that rule's 79 body-decided rows; it is
  furniture in almost every corporate PR. It now needs a verb
  (`achieves|reaches|announces|marks|hits|surpasses`). Probe any candidate token
  against generic corporate prose before adding it — the same test that would
  have caught `rmat`.
- Re-classifying an already-`_Classed` TSV is supported: `main()` appends
  `FineClass`/`BroadClass` only if absent, so the header never gains duplicates.
- `__pycache__/PrClassifier.cpython-312.pyc` exists because the orchestrator
  imports this file; it is gitignored — never force-add it.
