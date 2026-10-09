#!/usr/bin/env python
"""PrClassifier.py — tag each PR row with a catalyst class via regex rules.

Reads a PRstats TSV, matches the Headline (then the lead of the body as
fallback) against an ordered rule list, and appends two columns:
  FineClass  — ~21 specific classes (FDA_Approval, MA, Regain_Compliance, ...)
  BroadClass — grouped rollup (FDA, Clinical, Commercial, ...)
First matching rule wins; rows matching nothing get Other/Other.

Usage:
  /home/tom/venv/bin/python PrClassifier.py --input PRstats_....tsv [--output out.tsv]

  # classify the body fallback on the Orchestrator5.3 200-word body_prep string
  # instead of the first 1500 chars, into differently-named columns:
  ... --body-200w --fine-col FineClass_200w --broad-col BroadClass_200w

Default output: <input-stem>_Classed.tsv next to the input.
"""

import argparse
import csv
import os
import re
import sys
import unicodedata
from collections import Counter

# Body fallback only scans the lead of the article: about-us / safe-harbor
# boilerplate deeper in the text is full of false triggers (designations,
# "forward-looking", law-firm footers...).
BODY_LEAD_CHARS = 1500

# The --body-200w window. 200 words is the Orchestrator5.3 operator decision of
# 2026-09-18 (XGB_BODY_CAP_WORDS): the cap on every body its encoders, its
# embedder and its classifier read. Keep the two numbers in step.
BODY_PREP_WORDS = 200

# Wire headlines carry NBSP (\xa0) and friends between words; the rules below are
# written with literal spaces, so "Announces\xa0First\xa0Quarter\xa02026\xa0Results"
# used to match nothing and fall through to the body. Normalize before matching.
_WS_RUN = re.compile(r"\s+")

# "acquisition" has a marketing sense that has nothing to do with M&A, and it is
# common in exactly the growth PRs the MA rule kept stealing ("drive customer
# acquisition", "acquired more than 2,700 new users"). SPAC shells put it in their
# legal name ("Blue Water Acquisition Corp"). Deleting these senses up front is
# better than vetoing the MA rule outright: a PR can legitimately mention customer
# acquisition AND announce a merger, and only the phrase should be discounted.
NONDEAL_ACQ = re.compile(
    r"\b(customer|user|subscriber|client|patient|talent|land|data|lead|member)s?\s+"
    r"acquisitions?\b"
    r"|\bacquisitions?\s+(cost|costs|strategy|strategies|channel|channels|platform|"
    r"efforts?|marketing|funnel)\b"
    r"|\bacquisition\s+corp(oration)?\b"
    r"|\bacquired?\s+(more than|over|approximately|about|nearly)?\s*[\d,]+\s*"
    r"(new\s+)?(customers?|users?|subscribers?|clients?|patients?|members?|accounts?)\b",
    re.IGNORECASE,
)

# (FineClass, BroadClass, pattern) — priority order: first match wins,
# strongest/most specific catalyst first.
RULES = [
    # A bare `510(k)` names a PATHWAY, not an event, and the class was 20% one
    # issuer because of it: INBS published seven study milestones ("Commences
    # Method Comparison Study FOR FDA 510(k) Submission", "Achieves Key Milestone
    # AHEAD OF Planned FDA 510(k) Submission") that sat beside real clearances.
    # Two guards, both measured: the token must be bound to a grant/receipt, and
    # the leading veto drops the pathway-as-a-goal phrasings. Genuine clearances
    # (APYX "Receives Expanded FDA 510(k) Clearance", CUPR, WALD) are unaffected.
    ("FDA_Approval", "FDA",
     r"^(?!.*\b(supporting|in support of|ahead of|strengthening|in preparation for)\b"
     r"[^.]{0,40}(fda|510\(k\)|clearance|approval|submission))"
     r".*(?:fda (approv|clear)|(approv|clear)(al|ance|ed|es)?\s*(by|from)? the (u\.?s\.? )?fda"
     r"|510\(k\)[^.]{0,30}\b(clearance|cleared|approval|approved|granted)\b"
     r"|\b(receives?|received|grants?|granted|obtains?|obtained)\b[^.]{0,40}510\(k\)"
     r"|de novo (approval|clearance)|marketing authorization"
     r"|nda (submission|filing|accept)|bla (submission|filing|accept)"
     r"|emergency use authorization)"),
    # \brmat\b is load-bearing: unanchored `rmat` matched infoRMATion, foRMAT,
    # transfoRMATive, ARMATa, FoRMATion — 277 of 292 hits on the 3533-row corpus
    # were that bug, and this rule ranks 2nd so it stole from every class.
    # `fast track` and `priority review` were the SAME bug in phrase form: both are
    # ordinary business English, and "Fast Track" is the legal name of a listed
    # media company. 8 of this class's 23 hits were that one issuer plus a
    # "Fast Track to ML-Safe Compression" product launch. Both phrases now have to
    # carry their regulatory sense (a designation/status, or the FDA nearby).
    ("FDA_Designation", "FDA",
     r"orphan drug|breakthrough (therapy|device)|rare pediatric"
     r"|\brmat\b|qidp designation|\bind (clearance|accept)|ide approval"
     r"|fast[- ]track (designation|status)"
     r"|\b(fda|ema|regulatory)\b[^.]{0,30}fast[- ]track"
     r"|fast[- ]track[^.]{0,40}\bdesignation\b"
     r"|priority review (designation|status|voucher)"
     r"|\b(fda|grants?|granted|receives?|received|awarded)\b[^.]{0,30}priority review"),
    # `efficacy` as a bare noun is safe-harbour and about-us furniture ("no
    # assurance as to safety and efficacy"), and it caught a mortgage broker, a
    # disinfectant maker and an analyst-coverage note. It now needs a data object
    # or a demonstration verb. `positive (data|results)` wanted adjacency and so
    # missed "Positive Preclinical and Early Translational Data" — widened to a
    # 40-char window, which is what "Positive 7-Day Dog Study Results" needs too.
    ("Clinical_Data", "Clinical",
     r"topline|top-line|interim (data|results|analysis)"
     r"|phase [1-3][ab]?(/[1-3][ab]?)? (data|results|trial|study)"
     r"|clinical (data|results)|preclinical (data|results)"
     r"|positive [^.]{0,40}\b(data|results)\b"
     r"|primary endpoint"
     r"|efficacy (data|results|endpoint|signal)"
     r"|\b(demonstrat|show|shows|showed|met|achiev)\w*\b[^.]{0,30}\befficacy\b"
     r"|first patient (dosed|enrolled)|patient dos"
     r"|enrollment (complet|of first)|dose (escalation|optimization)"),
    # `clinical-stage` is REMOVED, not tightened: it is a self-description that
    # sits in the About paragraph of essentially every biotech PR, so via the body
    # fallback it stamped Clinical_Update on partnerships, financings, advisory-
    # board appointments and investor-forum notices. It never marks the catalyst.
    # `dosing`/`enrollment` likewise need a clinical object — bare, they matched a
    # supplements PR, a Medicare enrolment approval and "Open Enrollment Customers".
    ("Clinical_Update", "Clinical",
     r"phase [1-3]|clinical trial|clinical study"
     r"|(patient|subject|trial|study|cohort|first|full) (dosing|enrollment)"
     r"|(dosing|enrollment) (of|in|complet|begin|begins|initiat|underway)"
     r"|\b(begun|begins|began|beginning|initiat\w+|complet\w+|resum\w+|advanc\w+"
     r"|halt\w+|paus\w+)\s+(the\s+)?(patient\s+)?(dosing|enrollment)\b"
     r"|\bnda\b|\bbla\b|investigational"),
    # The acquire-family stays deliberately generous — MA is the top-lift class
    # (25.6%, 2.09x), so losing a real deal costs more than catching a stray. The
    # precision comes from NONDEAL_ACQ (above), which deletes the non-deal senses
    # of the word before any rule sees the text; matching here on what survives
    # keeps multi-signal PRs ("customer acquisition ... and a merger") intact,
    # which a whole-rule veto would have wrongly suppressed.
    ("MA", "MA",
     r"\bmergers?\b|\bacquisitions?\b|\bacquires?\b|\bacquired\b|to acquire"
     r"|business combination|strategic combination|takeover|buyout"
     r"|going.private|take[- ]private|tender offer|stock purchase agreement"
     r"|majority (stake|interest)|controlling (stake|interest)"
     r"|strategic alternatives|definitive agreement to (acquire|merge|combine)"),
    # The adjacency in `regain(s|ed)? compliance` was too literal — it missed
    # "Regains NYSE Listing Compliance" and "Compliant with Nasdaq Bid Price
    # Rule", and both then fell to the body, where the issuer's own pipeline
    # ("Phase 3", "NDA") classified them Clinical_Update.
    ("Regain_Compliance", "Compliance",
     r"regain(s|ed|ing)?\b[^.]{0,30}\bcompliance\b"
     r"|compliance with (the )?nasdaq|compliance with (the )?nyse"
     r"|\bcompliant with\b[^.]{0,30}\b(nasdaq|nyse)\b"),
    ("Deficiency_Notice", "Compliance",
     r"deficien|delist|non-?compliance|notification (letter )?from (the )?(nasdaq|nyse)"
     r"|minimum bid price requirement|continued listing"),
    ("Reverse_Split", "Compliance",
     r"reverse (stock |share )?split"),
    ("Crypto_Treasury", "Crypto",
     r"bitcoin|ethereum|\bsolana\b|crypto|digital asset|blockchain|staking|\bbtc\b|\beth\b"),
    # \bcontracts?\b excludes contractOR / contractUAL; the award lookahead drops
    # "award-winning", which is PR-boilerplate furniture rather than a contract win.
    ("Contract_Award", "Commercial",
     r"\bcontracts?\b|purchase order|\baward(ed|s)?\b(?![-\s]*winning)|task order"
     r"|\bidiq\b|procurement|wins? .{0,30}(order|deal|bid)"),
    ("Partnership", "Commercial",
     r"partnership|collaboration|strategic alliance|teams? up|joint venture"
     r"|\bmou\b|memorandum of understanding|letter of intent"
     r"|distribution agreement|license agreement|licensing agreement|supply agreement"),
    ("Product_Launch", "Commercial",
     r"launch|unveil|introduc|debut|now available|releases? new"
     r"|expands? .{0,30}(line|portfolio|offering)|new product"),
    ("Offering_Financing", "Financing",
     r"offering|private placement|pricing of|registered direct|at-the-market"
     r"|convertible note|\bwarrants?\b|pipe financing|capital raise|credit facility"
     r"|loan agreement|financing"),
    ("Buyback_Dividend", "Financing",
     r"buyback|repurchase|dividend|special cash distribution"),
    ("Patent_IP", "IP",
     r"\bpatent(s|ed|ing)?\b|trademark|intellectual property|\buspto\b"),
    # ── Earnings: THREE rules in a deliberate order; do not collapse or reorder ──
    # 1. Scheduling-only, and it must come FIRST: the tense is what separates a
    #    date announcement from a results print. "to report / will report /
    #    announces the date" is always an invitation, never the numbers. Kept
    #    deliberately NARROW — an earlier draft matched any "announces ... call",
    #    which stole all 90 of the "Reports Q2 Results AND Announces Conference
    #    Call" rows and would have silently dropped them from the live earnings
    #    trade branch. Every branch here must be scheduling-only phrasing.
    # The noun group is earnings vocabulary ONLY. It briefly also accepted
    # conference call/webcast/update/call, which made "to Host R&D Webcast" and
    # "to Present at the XYZ Investor Conference with live webcast" an EARNINGS
    # class. Scheduling PRs that really are about earnings match anyway — either
    # here or via the scheduling rule in HEADLINE_FIRST_RULES.
    ("Earnings_Date", "Earnings",
     r"\b(to|will) (report|announce|release|host|present|provide) .{0,60}"
     r"(results|earnings|revenue|financial)"
     r"|announces? (the )?date\b"
     r"|earnings (call|date|release|webcast|conference)"
     r"|(results|earnings) (call|webcast|conference call)"
     r"|(results|earnings) on (monday|tuesday|wednesday|thursday|friday|may|june|july|april)"),
    # 2. An explicit "reports ... results" in the present/past tense is the actual
    #    numbers, and wins over everything below.
    #    .{0,60} not .{0,40}: "Reports Fiscal Fourth Quarter and Full Year 2026
    #    Financial Results" is 41 chars between verb and noun and used to miss.
    ("Earnings_Results", "Earnings",
     r"(reports?|announces?|releases?) .{0,60}(financial|operating)? ?results"
     r"|(first|second|third|fourth)[- ]quarter .{0,20}results|full[- ]year results"
     r"|fiscal (year|q[1-4]) .{0,20}results|quarterly results"),
    # 3. (the widened results rule lives in HEADLINE_RULES, below — headline only)
    ("Management_Change", "Corporate",
     r"appoint|names? .{0,40}(as )?(chief|ceo|cfo|coo|cto|cmo|president|chairman|director)"
     r"|(ceo|cfo|leadership) (transition|change)|joins? .{0,30}as|steps? down|resign"
     r"|inducement (grant|award)"),
    ("Conference_Presentation", "Corporate",
     r"to present|presents? at|poster|symposium|\bsummit\b|investor conference"
     r"|fireside chat|annual meeting|to participate|to attend|rings? .{0,20}bell"),
    # Widened 2026-09-19, and the reason is a lesson this file already teaches in
    # the Regain_Compliance entry: a rule that is too LITERAL fails invisibly. The
    # plaintiff's-firm notice family has a grammar this rule did not hold — it had
    # the NOUN `investigation` but not "Johnson Fistel INVESTIGATES", `deadline
    # alert` but not "ATRA DEADLINE NOTICE", and nothing for "Shareholders Are
    # Encouraged to Reach Out to <firm>" or "Did X Insiders Breach their Fiduciary
    # Duties?". Those headlines matched NOTHING, fell to the body, and came back
    # classified by the ISSUER'S BUSINESS: every "Halper Sadeh is Investigating
    # Whether X is Obtaining a Fair Price" PR was `MA`, AARD's notice was
    # `Clinical_Data` on the biotech's own body, ANGI's was `Contract_Award`.
    # Measured over the 4804-row 2026-08-28 corpus: 34 rows move in, ALL of them
    # headline-decided and all genuine notices; 18 leave the O5 ClassWhitelist and
    # ZERO enter it; no genuine catalyst PR is taken.
    # ⚠ `investigat(e|es|ed|ing|ion|ions)\b` is spelled out deliberately — the old
    # unanchored `investigation` matched **investigation**AL, which is why 8 biotech
    # and mining PRs (TRAW, ADGM, OPTH, PBM, SURG…) carried this class on body prose
    # alone. Same family as the `rmat` bug. `investigator`/`investigational` must
    # not match, so never relax this to `investigat\w+`.
    # ⚠ The investigate-family and the notice grammar are HEADLINE-strength only:
    # over body prose a clinical study that "investigated" an endpoint, an SEC
    # matter and a short-seller reply all fire. See BODY_OVERRIDES["Legal"].
    ("Legal", "Legal",
     r"class action|law firm|lawsuit|litigation|settlement|court (rul|approv)"
     r"|\binvestigat(?:e|es|ed|ing|ion|ions)\b"
     r"|\b(encouraged|urged|reminded|advised|notified)\b[^.]{0,40}"
     r"\bto (contact|reach out|call|speak|act)\b"
     r"|\b(contact|reach out to)\b[^.]{0,30}\b(law offices?|llp|l\.l\.p\.|p\.c\.|attorney)"
     r"|on behalf of[^.]{0,30}\b(shareholders?|investors?|purchasers?|stockholders?)\b"
     r"|\b(deadline|investor)\b[^.]{0,15}\b(alert|notice|reminder)\b"
     r"|\b(alert|notice|reminder)\b[^.]{0,15}\bdeadline\b"
     r"|\blead plaintiff\b|\bclass period\b|\bsecurities fraud\b"
     r"|\bbreach\w*[^.]{0,25}\bfiduciary\b|\bfiduciary (duty|duties)\b[^.]{0,25}\bbreach"
     r"|\binvestor scrutiny\b"),
    # Bare `milestone` is furniture ("a significant milestone for the company"):
    # 71 of this rule's 79 body-decided rows fired on that one word alone. It now
    # needs a verb that makes the milestone the news.
    ("Business_Update", "Corporate",
     r"corporate update|business update|shareholder letter|letter to shareholders"
     r"|operational update|provides? update"
     r"|\b(achiev\w+|reach\w+|announc\w+|marks?|marked|hits?|surpass\w+)\b"
     r"[^.]{0,30}\bmilestones?\b"),
]

# ── Headline-only rules ──────────────────────────────────────────────────────
# Two lists, applied around RULES (see COMPILED_HEADLINE): FIRST outranks every
# rule, LAST is the fallback nothing else claimed. Both are headline-only because
# they trade precision for recall, which is safe on one curated sentence and NOT
# safe on 1500 chars of body prose — measured, in both directions, below.

# Pieces of the scheduling rule, named so the three conditions stay legible.
# Anything matching _SCHED_GENUINE is a real results release and is EXEMPT.
_SCHED_PERIOD = (r"(?:(first|second|third|fourth)[- ]quarter|q[1-4]\b|fiscal"
                 r"|full[- ]year|annual|interim|half[- ]year|1h|2h|monthly)")
# Earnings topic. Bare "results" does NOT count unless a fiscal period sits next
# to it — otherwise "to Host Webcast to Discuss Topline Results from a Pivotal
# Trial" reads as an earnings date instead of a clinical readout.
_SCHED_TOPIC = (r"(?:\b(earnings|financial|revenues?)\b"
                r"|\bresults\b(?=[^.]{0,40}" + _SCHED_PERIOD + r")"
                r"|" + _SCHED_PERIOD + r"[^.]{0,40}\bresults\b)")
# The exemption that protects the 90-row "Reports Q2 Results AND Announces
# Conference Call" family: a reporting verb bound to a financial noun, the
# results-noun phrases, or ANY hard number (a headline quoting "34% Rise in
# Revenue" or "$12M" is publishing figures, not just booking a date).
_SCHED_GENUINE = (r"(?:\b(reports?|reported|posts?|posted|delivers?|delivered"
                  r"|improves?|improved|achieves?|achieved|generates?|generated)\b"
                  r"[^.]{0,60}\b(results|revenues?|earnings|profit|income|ebitda|sales)\b"
                  r"|\b(quarterly|unaudited|interim|abridged|preliminary)\b"
                  r"[^.]{0,20}\bresults\b"
                  r"|\d+(\.\d+)?\s*%|\$\s?\d)")
_SCHED_VERB = (r"(?:\b(re)?schedul(e|es|ed|ing)\b"
               r"|\b(sets?|announces?|confirms?)\b[^.]{0,40}\b(date|time)\b"
               r"|\brelease date\b|\breporting date\b|\bdate of (the )?(release|earnings)\b"
               r"|\bconference call\b|\bteleconference\b|\bwebcast\b|\binvestor call\b"
               r"|\b(to|will)\s+(hold|discuss|report|announce|release|host)\b)")

# Pieces of the fiscal-period results rule (the second HEADLINE_FIRST rule).
_RESULTS_PERIOD = (r"\b((first|second|third|fourth)[- ]quarter|q[1-4]|fiscal"
                   r"|full[- ]year|half[- ]year)\b")
# MA is the highest-lift class in the corpus (25.6%, 2.09x vs 9.7% for
# Earnings_Results) and the rule order everywhere else keeps a real deal on top.
# A rule living in HEADLINE_FIRST outranks MA, so it has to hand those back:
# "Files Preliminary Proxy Statement for BUSINESS COMBINATION ...; Provides
# Business Update and Reports Q1 Results" is a deal PR that also prints numbers.
_RESULTS_MA_VETO = (r"(?!.*(business combination|\bmergers?\b|\bacquisitions?\b"
                    r"|\bacquires?\b|\bacquired\b|to acquire|takeover|buyout"
                    r"|tender offer))")

# Checked BEFORE RULES, so these outrank even the narrow Earnings_Results rule.
HEADLINE_FIRST_RULES = [
    # "Company XYZ to report earnings results on <date>" and its cousins — an
    # invitation, never the numbers. The tense test inside the Earnings_Date rule
    # in RULES catches "to report"/"announces the date" but misses three shapes:
    # a noun between verb and date ("Announces RELEASE Date for Q1 Results"),
    # "Schedules ... Results", and the call being the announcement ("Announces
    # Conference Call and Webcast FOR its Q1 Financial Results"). 27 such rows
    # were still landing in Earnings_Results across 6263 unique headlines.
    # Requires all three: scheduling verb AND earnings topic AND no genuine
    # results evidence. Never move this into RULES — over the body lead it
    # captured a securities lawsuit, an investor day and an annual mining report.
    ("Earnings_Date", "Earnings",
     r"^(?!.*" + _SCHED_GENUINE + r")(?=.*" + _SCHED_TOPIC + r").*" + _SCHED_VERB),
    # An unambiguous fiscal-period results print, ranked here so it beats the
    # Clinical_* rules. Biotechs headline the quarter AND the pipeline in one
    # line ("Agenus Reports First Quarter 2026 Financial Results and Highlights
    # ... Phase 3 Development"); Clinical_Update sits above Earnings in RULES, so
    # it won and those PRs silently lost the O5 EARNINGS trigger branch.
    # MUST stay below the scheduling rule above, and three things are load-bearing:
    #  - The verb group is `reports|reported|posts|posted` with NO optional `s`.
    #    `reports?` also matches the bare infinitive in "X to REPORT First Quarter
    #    Results on May 13" and swallowed 98 scheduling PRs into the live-gated
    #    class. `announces`/`releases` are excluded for the same reason —
    #    `releases?` matches the NOUN in "Announces RELEASE Date for Q1 Results".
    #  - A fiscal period is required, which is what leaves clinical readouts alone:
    #    "Announces Positive Topline Results from Phase 2" has no period.
    #  - Headline-only. Over the body lead this same rule captured a law-firm
    #    notice and a payments partnership.
    ("Earnings_Results", "Earnings",
     r"^" + _RESULTS_MA_VETO + r".*\b(reports|reported|posts|posted)\b[^.]{0,40}"
     + _RESULTS_PERIOD + r"[^.]{0,40}\bresults\b"),
    # 3. The same print reported as a NUMBER rather than as "results". The widened
    #    rule in HEADLINE_LAST_RULES already matches these, but it sits below every
    #    commercial rule, so COSM ("Reports Preliminary Record Q2 and H1 2026
    #    Revenue, Up Over 30% YoY; Continues Open Market REPURCHASES") was claimed
    #    by Buyback_Dividend on one word. Promoting the whole widened rule would be
    #    reckless — it is deliberately loose — so this is the unambiguous subset
    #    only: a fiscal period AND a hard number AND a revenue noun bound to a
    #    reporting verb, plus the same MA veto rule 2 carries, since anything in
    #    HEADLINE_FIRST outranks MA. Measured to take zero rows from Earnings_Date:
    #    the scheduling rule above still sees every headline first.
    ("Earnings_Results", "Earnings",
     r"^" + _RESULTS_MA_VETO
     + r"(?=.*(\d+(\.\d+)?\s*%|\$\s?\d))(?=.*" + _RESULTS_PERIOD + r")"
     r".*\b(reports|reported|posts|posted|delivers|delivered|achieves|achieved)\b"
     r"[^.]{0,60}\b(revenues?|earnings|sales|profits?|profitab\w*|net income"
     r"|ebitda|bookings|results)\b"),
]

# Checked AFTER RULES — the last-resort rules. Run against 1500 chars of prose the
# rule below promoted law-firm notices, analyst coverage, a monthly cash
# distribution and an investor-call announcement into Earnings_Results, a class
# that gates real money in Orchestrator5.2. Measured on the 3533-row corpus: 52
# headline hits (nearly all genuine) vs 31 body hits (nearly all wrong).
HEADLINE_LAST_RULES = [
    # Results reported WITHOUT the word "results" — "Reports Earnings", "Achieves
    # 21.8% Revenue Growth", "Achieves Record Revenues in July". Added 2026-08-07
    # after VTAK, a record monthly-revenue PR, matched nothing and fell through to
    # the body where MA caught it. Deliberately covers non-periodic (monthly,
    # preliminary) revenue prints, so Earnings_Results — and therefore the live O5
    # earnings branch — is wider than quarterly filings.
    # `\bprofit\b` could not match "Profitability", which is how AERT ("Delivers
    # Fiscal 2026 with Record PROFITABILITY") and LSAK ("achieves the upper end of
    # PROFITABILITY guidance") fell through to Other; and the growth verbs lacked
    # triples/doubles/surges/jumps, which is how KSCP ("Q2 Revenue TRIPLES to
    # Approximately $9M") did.
    # A fourth branch for the number-first shape ("Reports 23% Month-over-Month
    # GROWTH") was built and REMOVED: a percentage next to `growth|increase` does
    # not make a headline financial, and it put five non-earnings PRs into the
    # class that gates real money — BNGO's "56% Increase in PUBLICATIONS", AIFF's
    # "291% Increase in ... BRAIN SCAN VOLUMES", ICCM's "70% Growth in INSTALL
    # BASE" and MFM's "25% Increase in Monthly DISTRIBUTION" — to gain one
    # genuine row. The growth verb must attach to a financial noun, never to a
    # bare number.
    ("Earnings_Results", "Earnings",
     r"\b(reports?|reported|announces?|announced|releases?|released|posts?|posted"
     r"|delivers?|delivered|achieves?|achieved|generates?|generated)\b"
     r".{0,50}\b(earnings|revenues?|net income|net loss|profits?|profitab\w*"
     r"|ebitda|sales|bookings)\b"
     r"|\b(revenues?|earnings|sales|profits?|profitab\w*|ebitda|net income|bookings)\b.{0,40}"
     r"\b(increased?|grew|growth|rose|record|triples?|tripled|doubles?|doubled"
     r"|surges?|surged|jumps?|jumped)\b"
     r"|\brecord\b.{0,30}\b(revenues?|earnings|sales|profits?|profitab\w*)\b"),
]

def _compile(rules):
    return [(fine, broad, re.compile(pat, re.IGNORECASE | re.DOTALL))
            for fine, broad, pat in rules]


COMPILED = _compile(RULES)
# Headline order: FIRST outranks everything, then the shared rules, then the
# last-resort ones.
COMPILED_HEADLINE = _compile(HEADLINE_FIRST_RULES) + COMPILED + _compile(HEADLINE_LAST_RULES)

# ── The body-lead path ───────────────────────────────────────────────────────
# A headline is one curated sentence and every word in it is about the news. The
# first 1500 chars of a body are not: they carry the issuer's own description of
# itself and its pipeline, and a rule that reads a self-description as a catalyst
# will label the PR with whatever the company IS rather than what it just DID.
# Measured on the 4376-row 2026-08-13 corpus: of 155 FDA/Clinical rows decided by
# the body, 52 had a headline containing no clinical vocabulary at all — an oil &
# gas driller and a mining company on "Phase 1 of the drilling program", an IT
# services firm on "Phase 2", a board appointment on "rare pediatric", and two
# Nasdaq-compliance PRs on "Phase 3" and "NDA".
#
# Two mechanisms, both body-path only so headline recall is untouched:
#   BODY_EXCLUDED_CLASSES — the class is never assigned from body text at all.
#   BODY_OVERRIDES        — the class uses a stricter pattern over body text.

# Earnings_Results gates real money in Orchestrator5.2, and its two paths had
# opposite quality: 612 headline-decided rows were near-clean while 7 of the 9
# body-decided ones were wrong (a law-firm notice, a 20-F filing, an Army Corps
# technology validation, a correction notice, a sepsis-test readout). The rule is
# not fixable by qualifying it — those bodies really do contain the phrase "first
# quarter 2026 financial results" — so the class is headline-only.
#
# Crypto_Treasury joined it 2026-08-18 on the strongest reading of the
# body-decided tell this file has produced: of its 41 body-decided rows, ALL 41
# had a headline carrying no crypto vocabulary whatsoever (48% body-decided share
# vs the ~26% corpus baseline). The rule is also direction-blind — `digital asset`
# reads "orderly exit from Digital Asset Treasury strategy" exactly as it reads an
# adoption, which is how AIxC's robotics pivot became Crypto_Treasury. Excluding
# rather than tightening, because there was no good half of the body path to keep.
# Gate replay over the 4376-row 2026-08-13 corpus: 300 -> 304 fired, ZERO lost,
# and 3 of the 4 newly-firing rows cleared +30% (median 58.9%, base rate 12.8%).
# The 45 headline-decided rows are untouched.
BODY_EXCLUDED_CLASSES = frozenset({"Earnings_Results", "Crypto_Treasury"})

# A catalyst verb: the issuer is DOING something, not describing itself.
_BODY_CATALYST = (r"(?:receiv\w+|grant\w+|award\w+|announc\w+|report\w+|present\w+"
                  r"|initiat\w+|complet\w+|begin\w*|began|commenc\w+|doses|dosed"
                  r"|enroll\w+|submit\w+|file[sd]?|filing|secur\w+|obtain\w+"
                  r"|achiev\w+|demonstrat\w+|show\w*|expand\w+|advanc\w+|resum\w+)")
# Clinical context, required for tokens that are ordinary English outside biotech
# ("Phase 1 of the microgrid", "Phase 2 of the drilling program").
_BODY_CLIN_CTX = (r"(?:clinical|trial|stud(?:y|ies)|patient|dosing|enrollment"
                  r"|cohort|endpoint)")

BODY_OVERRIDES = {
    # Designation vocabulary is About-paragraph furniture — "developing therapies
    # for rare pediatric conditions" appears in every PR the company ever issues,
    # which is how a board-appointment notice became FDA_Designation. The token
    # must carry the regulatory noun AND sit next to a catalyst.
    "FDA_Designation":
        r"(?:" + _BODY_CATALYST + r"[^.]{0,80})?(?:orphan drug"
        r"|breakthrough (?:therapy|device)|rare pediatric(?: disease)?|\brmat\b|qidp)"
        r"[^.]{0,40}\b(?:designation|status|voucher)\b"
        r"|\b(?:fda|ema)\b[^.]{0,40}\b(?:ind clearance|ide approval)\b"
        r"|priority review (?:designation|status|voucher)",

    # Same idea for the approval pathway: over body prose "which was granted FDA
    # De Novo Classification in May 2026" is product description, not this PR.
    "FDA_Approval":
        r"^(?!.*\b(?:supporting|in support of|ahead of|strengthening"
        r"|in preparation for)\b[^.]{0,40}"
        r"(?:fda|510\(k\)|clearance|approval|submission))"
        r".*(?:fda (?:approv|clear)|(?:approv|clear)(?:al|ance|ed|es)?\s*(?:by|from)?"
        r" the (?:u\.?s\.? )?fda"
        r"|" + _BODY_CATALYST + r"[^.]{0,60}(?:510\(k\)|marketing authorization)"
        r"|(?:510\(k\)|de novo)[^.]{0,30}\b(?:clearance|approval|granted|received)\b"
        r"|\b(?:nda|bla)\b (?:submission|filing|accept)"
        r"|emergency use authorization)",

    # Bare `topline` is NOT safe here: "top-line" is also the financial sense, and
    # it put a logistics company ("Logistics Division Hyper-Growth ... top-line
    # run rate") into a clinical class.
    "Clinical_Data":
        r"top-?line (?:data|results|readout)|(?:data|results) [^.]{0,20}top-?line"
        r"|interim (?:data|results|analysis)"
        r"|phase [1-3][ab]?(?:/[1-3][ab]?)? (?:data|results|trial|study)"
        r"|clinical (?:data|results)|preclinical (?:data|results)"
        r"|positive [^.]{0,40}\b(?:data|results)\b"
        r"|primary endpoint"
        r"|efficacy (?:data|results|endpoint|signal)"
        r"|\b(?:demonstrat|show|shows|showed|met|achiev)\w*\b[^.]{0,30}\befficacy\b"
        r"|first patient (?:dosed|enrolled)|patient dos"
        r"|enrollment (?:complet|of first)|dose (?:escalation|optimization)",

    # `phase [1-3]` needs clinical context or it captures project phases; `nda`
    # and `bla` need a catalyst; `investigational` needs an object. Bare
    # "enrollment in" matched a shareholder loyalty programme.
    "Clinical_Update":
        r"phase [1-3][ab]?(?:/[1-3][ab]?)?[^.]{0,20}" + _BODY_CLIN_CTX
        + r"|" + _BODY_CLIN_CTX + r"[^.]{0,20}phase [1-3]"
        r"|clinical trial|clinical study"
        r"|(?:patient|subject|trial|study|cohort|first|full) (?:dosing|enrollment)"
        r"|(?:dosing|enrollment) (?:of|in)[^.]{0,30}"
        r"\b(?:trial|study|cohort|phase|patients?|subjects?)\b"
        r"|(?:dosing|enrollment) (?:complet|begin|begins|initiat|underway)"
        r"|\b(?:begun|begins|began|beginning|initiat\w+|complet\w+|resum\w+|advanc\w+"
        r"|halt\w+|paus\w+)\s+(?:the\s+)?(?:patient\s+)?(?:dosing|enrollment)\b"
        r"|" + _BODY_CATALYST + r"[^.]{0,60}\b(?:nda|bla)\b"
        r"|\binvestigational\b[^.]{0,40}"
        r"(?:drug|therap|product|agent|candidate|treatment|new drug)",

    # Litigation NOUNS only. The widened headline rule above carries the
    # plaintiff's-firm notice grammar and the investigate-family, and neither
    # survives contact with body prose: a clinical study "investigated" an
    # endpoint, an issuer "is cooperating with an SEC investigation", a reply to a
    # short-seller report, "the Board investigated strategic options". Measured:
    # 8 of this class's body-decided rows were that, plus the investigationAL bug
    # the headline rule's comment describes. The notice grammar is likewise
    # headline-strength — "on behalf of shareholders" is in the boilerplate of any
    # PR that mentions a proxy.
    "Legal":
        r"class action|law firm|lawsuit|litigation|settlement|court (?:rul|approv)"
        r"|\blead plaintiff\b|\bclass period\b|\bsecurities fraud\b",
}

COMPILED_BODY = _compile([(fine, broad, BODY_OVERRIDES.get(fine, pat))
                          for fine, broad, pat in RULES
                          if fine not in BODY_EXCLUDED_CLASSES])


def normalize(text):
    """Fold unicode whitespace/punctuation variants and drop the non-M&A senses of
    "acquisition" so the rules can be written in plain ASCII. Callers that only
    want the rule verdict should use classify(), which applies this itself."""
    text = unicodedata.normalize("NFKC", text)
    text = _WS_RUN.sub(" ", text)
    return NONDEAL_ACQ.sub(" ", text)


def classify(text, headline=False):
    """Return (FineClass, BroadClass) for text; ("Other","Other") if no rule hits.

    Pass headline=True ONLY for a headline: it additionally consults
    HEADLINE_FIRST_RULES/HEADLINE_LAST_RULES, which are too loose to run over body
    prose. Callers that classify a body lead must leave it False — that path uses
    COMPILED_BODY, which drops BODY_EXCLUDED_CLASSES and applies BODY_OVERRIDES."""
    text = normalize(text)
    for fine, broad, rx in (COMPILED_HEADLINE if headline else COMPILED_BODY):
        if rx.search(text):
            return fine, broad
    return "Other", "Other"


def classify_row(row, body_text=None):
    """Headline first; body lead as fallback. Returns (fine, broad, used_body).

    `body_text` lets the caller supply the fallback string itself, and it is then
    used VERBATIM (no BODY_LEAD_CHARS slice). That is how --body-200w reproduces
    Orchestrator5.3, which classifies the same 200-word body_prep string its body
    encoders and its embedder score, not a 1500-char slice of the raw cell.
    Leaving it None is the historical behaviour, byte for byte."""
    fine, broad = classify(row.get("Headline") or "", headline=True)
    if fine != "Other":
        return fine, broad, False
    if body_text is None:
        body = (row.get("body") or "").strip() or (row.get("Body") or "")
        body_text = body[:BODY_LEAD_CHARS]
    fine, broad = classify(body_text)
    return fine, broad, fine != "Other"


# ── The Orchestrator5.3 200-word body path (batch side) ──────────────────────
# O5.3 builds ONE body string per article (_prep_body_200) and hands it to the
# three body encoders, the FinBERT embedder AND — since the 200-word alignment —
# this classifier, so every body consumer reads the same text and an offline
# rescore of the TSV reproduces the live cells. `--body-200w` reproduces that
# string here. It is opt-in: without the flag this script still classifies the
# first BODY_LEAD_CHARS characters of the raw cell, exactly as it always has.

# A verbatim copy of clean_inline_text (RTPR_connector3/RTPR_connector3_curl.py),
# which is what produced the TSV Body cell in the first place. Copied rather than
# imported on purpose: this module is path-loaded by the live orchestrator and its
# top level must stay stdlib-only. It is idempotent on an already one-lined cell —
# it is here so the offline string is provably the same function O5.3 applies.
_INLINE_WS = re.compile(r"\s+")


def _clean_inline(text):
    return _INLINE_WS.sub(" ", (text or "").replace("\xa0", " ")).strip()


def _load_body_prep():
    """Path-load tools/Encoder_Models/body_prep.py — the same module, by the same
    mechanism, that Orchestrator5.3 loads as `o53_body_prep`.

    Called ONLY from main(): body_prep lazily pulls in tools/keyword/keyword_finder
    (pandas + sklearn) for strip_wire_boilerplate, which must never land in the
    live orchestrator's import of this file."""
    import importlib.util

    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "Encoder_Models", "body_prep.py")
    spec = importlib.util.spec_from_file_location("prclass_body_prep", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.prep_body


def main():
    ap = argparse.ArgumentParser(description="Add FineClass/BroadClass columns to a PRstats TSV")
    ap.add_argument("--input", required=True, help="input PRstats TSV")
    ap.add_argument("--output", help="output TSV (default: <input-stem>_Classed.tsv)")
    ap.add_argument("--body-200w", action="store_true",
                    help="classify the body fallback on the Orchestrator5.3 string: "
                         "body_prep.prep_body(clean_inline(Body), Headline, 200) "
                         "instead of the first %d characters of the raw cell"
                         % BODY_LEAD_CHARS)
    ap.add_argument("--fine-col", default="FineClass",
                    help="output column for the fine class (default: FineClass)")
    ap.add_argument("--broad-col", default="BroadClass",
                    help="output column for the broad class (default: BroadClass)")
    args = ap.parse_args()

    prep_body = _load_body_prep() if args.body_200w else None

    out_path = args.output
    if not out_path:
        stem, ext = os.path.splitext(args.input)
        out_path = f"{stem}_Classed{ext or '.tsv'}"

    csv.field_size_limit(sys.maxsize)
    fine_counts, broad_counts = Counter(), Counter()
    n_rows = n_body = 0

    with open(args.input, newline="") as fin, open(out_path, "w", newline="") as fout:
        reader = csv.DictReader(fin, delimiter="\t", quoting=csv.QUOTE_NONE)
        if "Headline" not in reader.fieldnames:
            sys.exit("ERROR: input has no Headline column")
        # Re-classifying an already-classed TSV is routine (rule changes), so append
        # the two columns only if they are not already there — otherwise the header
        # would carry duplicate names and every row would write the value twice.
        fieldnames = list(reader.fieldnames) + [c for c in (args.fine_col, args.broad_col)
                                                if c not in reader.fieldnames]
        writer = csv.DictWriter(fout, fieldnames=fieldnames,
                                delimiter="\t", quoting=csv.QUOTE_NONE, quotechar=None,
                                escapechar=None, lineterminator="\n")
        writer.writeheader()
        for row in reader:
            body_text = None
            if prep_body is not None:
                body = (row.get("body") or "").strip() or (row.get("Body") or "")
                body_text = prep_body(_clean_inline(body), row.get("Headline") or "",
                                      BODY_PREP_WORDS)
            fine, broad, used_body = classify_row(row, body_text)
            row[args.fine_col], row[args.broad_col] = fine, broad
            writer.writerow(row)
            n_rows += 1
            n_body += used_body
            fine_counts[fine] += 1
            broad_counts[broad] += 1

    window = (f"body_prep + first {BODY_PREP_WORDS} words"
              if args.body_200w else f"first {BODY_LEAD_CHARS} chars")
    print(f"{n_rows} rows -> {out_path}  ({n_body} classified via body fallback; "
          f"body window: {window}; columns: {args.fine_col}/{args.broad_col})")
    print("\nFineClass:")
    # dict.fromkeys de-dups while preserving order: Earnings_Results appears twice
    # in RULES (narrow results rule, then the widened one below Earnings_Date).
    order = list(dict.fromkeys([f for f, _, _ in RULES] + ["Other"]))
    for fine in order:
        if fine_counts[fine]:
            print(f"  {fine:26s} {fine_counts[fine]:5d}")
    print("\nBroadClass:")
    for broad, cnt in broad_counts.most_common():
        print(f"  {broad:12s} {cnt:5d}")


if __name__ == "__main__":
    main()
