#!/usr/bin/env python3
"""
earning_scorer.py

Rule-based scorer for Earnings_Results PRs: extracts key earnings metrics from
the Headline/Body with regexes (sentence-level), then turns them into an
interpretable additive EarningScore aimed at predicting large fromPR_High(%)
moves (calibrated on >=30%).

Extraction families (each hit keeps the metric, the value and the sentence):
    A. explicit growth   "revenue grew 60% year-over-year", "Gross Profit Up 124%",
                         "a 358% increase in EBITDA", "revenue growth of 45%"
    B. implicit growth   "revenue increased to $24.3 million compared to $23.8
                         million" -> % change computed from the dollar pair
    C. record claims     "record quarterly revenue", "record net income"
    D. guidance          raises/increases (pos), reaffirms (neutral),
                         lowers/withdraws (neg), "expects record ..."
    E. profitability flip  "net income of $X compared to a net loss",
                         "returned to profitability", "swung to a profit"
    F. margin            "gross margin expanded 670 basis points to 23.5%"
    G. negative structures  loss widened, going concern, impairment,
                         material weakness, negative flip (income -> loss)
    H. scheduling-only   "to report ... results on <date>" with no real metrics
    I. size-relative     extracted revenue/profit $ divided by float-cap
                         (Float x LastDailyClose; batch mode only)

The family set is deliberately still A-I after the 2026-08-14 rebuild.  Two
candidate families were tested against the train block and REJECTED — see
"rejected families" in Earnings_text_structures_report.md.  Extraction is not
the weak link: only 2 of the 30 train-block positives have nothing to extract.

TWO SCORES ARE EMITTED, and the distinction is load-bearing:

    EarningScore    int, "points".  FROZEN — this is the field the live trade
                    gate reads (`EarningScore >= 4` in
                    orchestrator5/orchestrator_trigger_rules.txt).  Families
                    A-I only, thresholds/points from earn_score_params.json.
                    Family J does NOT feed it.  Do not change its scale: the
                    gate's integer cut would silently mean something else, and
                    O5.2 refuses to start on a fractional threshold.

    EarnScoreNorm   float in [0,1], the 2026-08-14 rebuild.  Freely re-derived
                    weights over the same families, fitted on the
                    2026-05-04..08-13 corpus, weights in earn_norm_params.json.
                    Nothing live reads it yet.  It is a RANKING score, not a
                    calibrated probability — the base rate is regime-shifted
                    across the corpus, so the absolute value does not transfer.

Both params files are written by evaluate_earning_scorer.py; built-in
provisional values are used when a file is absent.  EarnBreakdown /
EarnNormBreakdown are the matching observability strings.

CLI (mirrors keyword_scorer-2.py conventions):
    batch:  /home/tom/venv/bin/python earning_scorer.py --input FILE.tsv
            -> FILE_earnScored.tsv with earn_* / earnH_* columns appended
    single: echo "body text" | /home/tom/venv/bin/python earning_scorer.py \
                --stdin --headline-text "Some headline"   -> JSON on stdout

Module use:  from earning_scorer import extract_metrics, score_metrics
             (live path), or score_norm for the rebuilt score.
"""

import argparse
import html
import json
import math
import os
import re
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(SCRIPT_DIR,
                             "PRstats_2026-05-04-2026-08-13_EarningResults.tsv")
DEFAULT_PARAMS_FILE = os.path.join(SCRIPT_DIR, "earn_score_params.json")
DEFAULT_NORM_FILE = os.path.join(SCRIPT_DIR, "earn_norm_params.json")

# --------------------------------------------------------------------------
# metric vocabulary
# --------------------------------------------------------------------------
REV_TERMS = r"(?:total\s+)?(?:net\s+)?(?:revenues?|sales)"
PROFIT_TERMS = (r"(?:net\s+income|gross\s+profit|operating\s+income|"
                r"(?:adjusted\s+)?ebitda|earnings\s+per\s+share|(?:diluted\s+)?eps|"
                r"net\s+profit|pre-?tax\s+income|operating\s+profit)")
METRIC_TERMS = rf"(?:{REV_TERMS}|{PROFIT_TERMS})"
MARGIN_TERMS = r"(?:gross|operating|net|ebitda)\s+margin"

UP_VERBS = (r"(?:increas\w+|grew|grow\w*|rose|rise[sn]?|up|surg\w+|jump\w+|"
            r"climb\w+|improv\w+|expand\w+|gain\w+|doubl\w+|tripl\w+)")
DOWN_VERBS = r"(?:decreas\w+|declin\w+|fell|fall\w*|down|dropp?\w*|contract\w+|deteriorat\w+)"

PCT = r"(\d{1,4}(?:[.,]\d{1,2})?)\s?%"
APPROX = r"(?:approximately\s+|about\s+|over\s+|more\s+than\s+|nearly\s+|~\s?)?"


def _g(n, excl=""):
    """Lazy gap of up to n chars that stops at sentence enders but allows
    decimal points ("$13.2 million") which a plain [^.;] would treat as an
    ender.  excl adds extra excluded chars (e.g. "%" or "$")."""
    return rf"(?:[^.;{excl}]|\.(?=\d)){{0,{n}}}?"
# $ amount with unit; group(1)=number, group(2)=unit word
DOLLAR = r"\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*(million|billion|thousand|mm|bn|m|b|k)?\b"

MONTHS = (r"(?:january|february|march|april|may|june|july|august|september|"
          r"october|november|december)")


def _num(s):
    return float(s.replace(",", ""))


def _dollars(num_str, unit):
    v = _num(num_str)
    u = (unit or "").lower()
    if u in ("billion", "bn", "b"):
        return v * 1e9
    if u in ("million", "mm", "m"):
        return v * 1e6
    if u in ("thousand", "k"):
        return v * 1e3
    return v


def _norm_text(text):
    """Light cleanup: decode entities, strip tags if HTML-ish, one-line."""
    if not isinstance(text, str):
        return ""
    t = html.unescape(text)
    if "<" in t and re.search(r"<[a-zA-Z/][^>]*>", t):
        t = re.sub(r"<[^>]+>", " ", t)
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", t).strip()


def _sentences(text):
    """Split one-lined PR body into sentence-ish units (bullets + . ! ? ;)."""
    parts = re.split(r"\s\*\s|•", text)          # bullets
    out = []
    for p in parts:
        out.extend(re.split(r"(?<=[.!?;])\s+(?=[A-Z\$\"(\d])", p))
    return [s.strip() for s in out if len(s.strip()) >= 3]


# --------------------------------------------------------------------------
# family A: explicit growth  (metric ... verb ... X%   in any of 3 shapes)
# --------------------------------------------------------------------------
# shape 1: metric ... up-verb ... X%          "revenue grew 60%"
_A1 = re.compile(
    rf"({METRIC_TERMS}){_g(60)}\b({UP_VERBS}|{DOWN_VERBS})\b{_g(40, '%')}"
    rf"{APPROX}{PCT}", re.I)
# shape 2: X% increase/decrease in metric     "a 358% increase in EBITDA"
_A2 = re.compile(
    rf"{PCT}\s+(increase|growth|gain|improvement|rise|jump|surge|decrease|"
    rf"decline|drop|reduction)\s+(?:in|of)\s+{_g(40)}({METRIC_TERMS})", re.I)
# shape 3: metric growth of X%                "revenue growth of 45%"
_A3 = re.compile(
    rf"({METRIC_TERMS})\s+growth\s+of\s+{APPROX}{PCT}", re.I)
# shape 4 (headline style): metric Up/Down X%   "Revenue Up 60%"
_A4 = re.compile(
    rf"({METRIC_TERMS})\s+(up|down)\s+{APPROX}{PCT}", re.I)

_DOWN_WORDS = ("decreas", "declin", "fell", "fall", "down", "drop",
               "contract", "deteriorat", "reduction")


def _family_A(sent, hits):
    for m in _A1.finditer(sent):
        metric, verb, pct = m.group(1), m.group(2), _num(m.group(3))
        sign = -1 if any(w in verb.lower() for w in _DOWN_WORDS) else 1
        hits.append(("growth", metric.lower(), sign * pct, sent))
    for m in _A2.finditer(sent):
        pct, direction, metric = _num(m.group(1)), m.group(2), m.group(3)
        sign = -1 if any(w in direction.lower() for w in _DOWN_WORDS) else 1
        hits.append(("growth", metric.lower(), sign * pct, sent))
    for m in _A3.finditer(sent):
        hits.append(("growth", m.group(1).lower(), _num(m.group(2)), sent))
    for m in _A4.finditer(sent):
        sign = -1 if m.group(2).lower() == "down" else 1
        hits.append(("growth", m.group(1).lower(), sign * _num(m.group(3)), sent))


# --------------------------------------------------------------------------
# family B: implicit growth from dollar pairs
# "revenue increased to $24.3 million(,) compared to/from $23.8 million"
# --------------------------------------------------------------------------
_B = re.compile(
    rf"({METRIC_TERMS}){_g(80)}(?:{UP_VERBS}|{DOWN_VERBS}|was|were|of|totaled|"
    rf"reached|came\s+in\s+at)\s+(?:to\s+)?{APPROX}(?:US)?{DOLLAR}"
    rf"{_g(60)}(?:compared\s+(?:to|with)|versus|vs\.?|from|up\s+from|down\s+from)"
    rf"{_g(40, '$')}(?:US)?{DOLLAR}", re.I)


def _family_B(sent, hits):
    for m in _B.finditer(sent):
        if re.search(r"\bloss\b", m.group(0), re.I):
            # income-vs-loss comparison: % change vs a loss is meaningless;
            # family E (profit flip) owns this case
            continue
        metric = m.group(1).lower()
        a = _dollars(m.group(2), m.group(3))
        b = _dollars(m.group(4), m.group(5))
        # missing unit on one side ("$24.3 million ... $23.8") -> borrow unit
        if m.group(3) and not m.group(5) and b < 1000 and a >= 1000:
            b = _dollars(m.group(4), m.group(3))
        if m.group(5) and not m.group(3) and a < 1000 and b >= 1000:
            a = _dollars(m.group(2), m.group(5))
        if b <= 0:
            continue
        ratio = a / b
        if ratio > 100 or ratio < 0.01:      # mismatched units / garbage
            continue
        pct = (a - b) / b * 100.0
        hits.append(("growth_pair", metric, pct, sent))
        hits.append(("dollar_level", metric, a, sent))


# a lone metric dollar level (for family I) even without a comparison
_LEVEL = re.compile(
    rf"({REV_TERMS}){_g(60)}(?:of|was|were|totaled|reached|to|at)\s+"
    rf"{APPROX}(?:US)?{DOLLAR}", re.I)
_PROFIT_LEVEL = re.compile(
    rf"(net\s+(?:profit|income)|(?:adjusted\s+)?ebitda)"
    rf"{_g(80)}(?:of|was|were|totaled|reached|at)\s+"
    rf"{APPROX}(?:US)?{DOLLAR}", re.I)


def _family_level(sent, hits):
    for m in _LEVEL.finditer(sent):
        hits.append(("dollar_level", m.group(1).lower(),
                     _dollars(m.group(2), m.group(3)), sent))
    for m in _PROFIT_LEVEL.finditer(sent):
        if re.search(r"\bnet\s+loss\b", m.group(0), re.I):
            continue
        hits.append(("profit_level", m.group(1).lower(),
                     _dollars(m.group(2), m.group(3)), sent))


# --------------------------------------------------------------------------
# family C: record claims
# --------------------------------------------------------------------------
_C = re.compile(
    rf"\brecord(?:-breaking|-setting)?\s+(?:[a-z-]+\s){{0,3}}?"
    rf"({METRIC_TERMS}|{MARGIN_TERMS}|backlog|bookings|cash\s+flow|results|quarter)",
    re.I)


def _family_C(sent, hits):
    for m in _C.finditer(sent):
        hits.append(("record", m.group(1).lower(), 1, sent))


# --------------------------------------------------------------------------
# family D: guidance
# --------------------------------------------------------------------------
_D_RAISE = re.compile(
    r"\b(rais\w+|increas\w+|boost\w+|lift\w+|hik\w+)\b[^.;]{0,60}?"
    r"\b(guidance|outlook|forecast|target)\b"
    r"|\b(guidance|outlook|forecast)\b[^.;]{0,40}?\b(raised|increased)\b", re.I)
_D_REAFF = re.compile(
    r"\b(reaffirm\w+|maintain\w+|confirm\w+|reiterat\w+)\b[^.;]{0,60}?"
    r"\b(guidance|outlook|forecast)\b", re.I)
_D_CUT = re.compile(
    r"\b(lower\w+|reduc\w+|cut[s]?|withdraw\w+|suspend\w+|revis\w+\s+down\w*)\b"
    r"[^.;]{0,60}?\b(guidance|outlook|forecast)\b", re.I)
_D_EXP_REC = re.compile(
    r"\b(expect\w+|anticipat\w+|project\w+)\b[^.;]{0,40}?\brecord\b", re.I)


def _family_D(sent, hits):
    if _D_CUT.search(sent):
        hits.append(("guidance", "cut", -1, sent))
    elif _D_RAISE.search(sent):
        hits.append(("guidance", "raise", 1, sent))
    elif _D_REAFF.search(sent):
        hits.append(("guidance", "reaffirm", 0, sent))
    if _D_EXP_REC.search(sent):
        hits.append(("expects_record", "", 1, sent))


# --------------------------------------------------------------------------
# family E: profitability flip (and its negative twin -> family G)
# --------------------------------------------------------------------------
_E_FLIP = re.compile(
    rf"\bnet\s+income\b{_g(80)}(?:compared\s+(?:to|with)|versus|vs\.?|from)"
    rf"{_g(40)}\bnet\s+loss\b"
    r"|\b(?:achiev\w+|return\w+|swung|swing\w*|turn\w+)\s+(?:to\s+)?"
    r"(?:a\s+)?profit\w*\b"
    r"|\bfirst\s+profitable\s+(?:quarter|year)\b"
    r"|\bprofitability\s+(?:achieved|milestone)\b", re.I)
_E_NEGFLIP = re.compile(
    rf"\bnet\s+loss\b{_g(80)}(?:compared\s+(?:to|with)|versus|vs\.?|from)"
    rf"{_g(40)}\bnet\s+income\b"
    r"|\bswung\s+to\s+(?:a\s+)?loss\b", re.I)


def _family_E(sent, hits):
    if _E_NEGFLIP.search(sent):
        hits.append(("neg_flag", "flip_to_loss", 1, sent))
    elif _E_FLIP.search(sent):
        hits.append(("profit_flip", "", 1, sent))


# --------------------------------------------------------------------------
# family F: margin moves
# --------------------------------------------------------------------------
_F_BPS = re.compile(
    rf"({MARGIN_TERMS}){_g(60)}(?:{UP_VERBS}|{DOWN_VERBS}){_g(30)}"
    rf"(\d{{2,4}})\s*(?:bps|basis\s+points)", re.I)
_F_PP = re.compile(
    rf"({MARGIN_TERMS}){_g(60)}(?:{UP_VERBS}|{DOWN_VERBS}){_g(30)}"
    rf"(\d{{1,2}}(?:\.\d+)?)\s*(?:percentage\s+points?|pts?\b)", re.I)
_F_TO = re.compile(
    rf"({MARGIN_TERMS}){_g(60)}({UP_VERBS}|{DOWN_VERBS}){_g(30)}"
    rf"to\s+{PCT}\s*(?:from|compared|versus|vs)?{_g(20, '%')}{PCT}?", re.I)


def _family_F(sent, hits):
    for m in _F_BPS.finditer(sent):
        seg = m.group(0).lower()
        sign = -1 if any(w in seg for w in _DOWN_WORDS) else 1
        hits.append(("margin_bps", m.group(1).lower(), sign * _num(m.group(2)), sent))
    for m in _F_PP.finditer(sent):
        seg = m.group(0).lower()
        sign = -1 if any(w in seg for w in _DOWN_WORDS) else 1
        hits.append(("margin_bps", m.group(1).lower(),
                     sign * _num(m.group(2)) * 100, sent))
    for m in _F_TO.finditer(sent):
        if m.group(4):                       # "to 23.5% from 16.8%"
            delta = (_num(m.group(3)) - _num(m.group(4))) * 100
            hits.append(("margin_bps", m.group(1).lower(), delta, sent))


# --------------------------------------------------------------------------
# family G: negative structures
# --------------------------------------------------------------------------
_G_PATTERNS = [
    ("going_concern", re.compile(r"\bgoing\s+concern\b|\bsubstantial\s+doubt\b", re.I)),
    ("impairment", re.compile(r"\bimpairment\b|\bwrite-?(?:down|off)\b", re.I)),
    ("loss_widened", re.compile(
        r"\b(?:net\s+)?loss\s+(?:widen\w+|increas\w+|grew|deepen\w+)\b", re.I)),
    ("material_weakness", re.compile(r"\bmaterial\s+weakness\b", re.I)),
    ("delisting", re.compile(r"\bdelist\w+\b|\bnon-?compliance\b", re.I)),
    ("restatement", re.compile(r"\brestat(?:e|ed|ement)\b", re.I)),
]


def _family_G(sent, hits):
    for name, pat in _G_PATTERNS:
        if pat.search(sent):
            hits.append(("neg_flag", name, 1, sent))


# --------------------------------------------------------------------------
# family H: scheduling-only announcement
# --------------------------------------------------------------------------
_H = re.compile(
    rf"\b(?:will|to|plans\s+to|intends\s+to)\s+(?:report|announce|release|"
    rf"host|provide|discuss|webcast)\b[^.;]{{0,80}}?\b(?:results?|earnings)\b"
    rf"[^.;]{{0,80}}?\b(?:on|before|after)\s+(?:the\s+)?(?:\w+day,?\s+)?"
    rf"(?:{MONTHS}|market)", re.I)
_H_SETDATE = re.compile(
    r"\b(?:sets?|announc\w+)\s+(?:the\s+)?date\b[^.;]{0,60}?"
    r"\b(?:results?|earnings)\b", re.I)


def _is_scheduling(headline, body):
    return bool(_H.search(headline) or _H_SETDATE.search(headline)
                or _H.search(body[:400]))


# --------------------------------------------------------------------------
# extraction driver
# --------------------------------------------------------------------------
def _extract_hits(text):
    hits = []
    for sent in _sentences(text):
        _family_A(sent, hits)
        _family_B(sent, hits)
        _family_level(sent, hits)
        _family_C(sent, hits)
        _family_D(sent, hits)
        _family_E(sent, hits)
        _family_F(sent, hits)
        _family_G(sent, hits)
    return hits


_REV_ONLY = re.compile(rf"^{REV_TERMS}$|revenue|sales", re.I)


def _is_rev(metric):
    return bool(re.search(r"revenue|sales", metric, re.I))


def _aggregate(hits, mktcap=None):
    """Reduce raw hits to the earn_* feature dict."""
    growth = [(m, v) for k, m, v, _ in hits if k in ("growth", "growth_pair")]
    rev_g = [v for m, v in growth if _is_rev(m)]
    prof_g = [v for m, v in growth if not _is_rev(m)]
    levels = [v for k, m, v, _ in hits if k == "dollar_level" and _is_rev(m)]
    margin = [v for k, _, v, _ in hits if k == "margin_bps"]
    guid = [v for k, _, v, _ in hits if k == "guidance"]

    f = {
        "rev_growth_pct": max(rev_g) if rev_g else float("nan"),
        "profit_growth_pct": max(prof_g) if prof_g else float("nan"),
        "best_growth_pct": max([v for _, v in growth]) if growth else float("nan"),
        "worst_growth_pct": min([v for _, v in growth]) if growth else float("nan"),
        "record_count": sum(1 for k, _, _, _ in hits if k == "record"),
        "guidance": (min(guid) if any(v < 0 for v in guid)
                     else max(guid)) if guid else 0,
        "expects_record": int(any(k == "expects_record" for k, _, _, _ in hits)),
        "profit_flip": int(any(k == "profit_flip" for k, _, _, _ in hits)),
        "margin_delta_bps": max(margin) if margin else float("nan"),
        "neg_flags": sum(1 for k, _, _, _ in hits if k == "neg_flag"),
        "n_hits": len(hits),
    }
    f["rev_dollars"] = max(levels) if levels else float("nan")
    if mktcap and mktcap > 0 and levels:
        f["rev_to_floatcap"] = f["rev_dollars"] / mktcap
    else:
        f["rev_to_floatcap"] = float("nan")
    plevels = [v for k, m, v, _ in hits if k == "profit_level"]
    f["profit_dollars"] = max(plevels) if plevels else float("nan")
    if mktcap and mktcap > 0 and plevels:
        f["profit_to_floatcap"] = f["profit_dollars"] / mktcap
    else:
        f["profit_to_floatcap"] = float("nan")
    return f


def extract_metrics(headline, body, mktcap=None):
    """Full extraction -> dict with earn_* (body) and earnH_* (headline) keys,
    plus structure stats and the scheduling flag."""
    h = _norm_text(headline)
    b = _norm_text(body)
    body_hits = _extract_hits(b)
    head_hits = _extract_hits(h)

    feats = {}
    for key, val in _aggregate(body_hits, mktcap).items():
        feats["earn_" + key] = val
    for key, val in _aggregate(head_hits, mktcap).items():
        feats["earnH_" + key] = val

    real_metrics = [k for k, _, _, _ in body_hits + head_hits
                    if k in ("growth", "growth_pair", "record", "profit_flip",
                             "margin_bps")]
    feats["earn_scheduling_only"] = int(_is_scheduling(h, b)
                                        and len(real_metrics) == 0)

    toks = b.split()
    feats["earn_num_density"] = (sum(1 for t in toks if any(c.isdigit() for c in t))
                                 / len(toks)) if toks else 0.0
    feats["earnH_has_pct"] = int(bool(re.search(r"\d\s?%", h)))
    feats["earnH_has_metric"] = int(bool(re.search(METRIC_TERMS, h, re.I)))
    feats["_body_hits"] = body_hits
    feats["_head_hits"] = head_hits
    return feats


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------
# provisional params; evaluate_earning_scorer.py writes the calibrated file
BUILTIN_PARAMS = {
    "rev_lo": 20.0, "rev_lo_pts": 2,
    "rev_hi": 50.0, "rev_hi_pts": 3,
    "prof_thr": 30.0, "prof_pts": 2,
    "record_pts": 1, "record_cap": 2,
    "guid_raise_pts": 2, "guid_cut_pts": -2, "expects_record_pts": 1,
    "flip_pts": 2,
    "margin_bps_thr": 200.0, "margin_pts": 1,
    "neg_flag_pts": -1,
    "rev_fcap_thr": 0.25, "rev_fcap_pts": 1,
    "prof_fcap_thr": 0.10, "prof_fcap_pts": 1,
    "headline_growth_bonus": 1,
    "scheduling_zeroes": 1,
}


def load_params(path=None):
    path = path or DEFAULT_PARAMS_FILE
    if os.path.exists(path):
        with open(path) as fh:
            p = dict(BUILTIN_PARAMS)
            p.update(json.load(fh))
            return p
    return dict(BUILTIN_PARAMS)


def score_metrics(feats, params=None):
    """feats -> (EarningScore points, breakdown string)."""
    p = params or load_params()
    pts = 0
    parts = []

    rg = feats.get("earn_rev_growth_pct", float("nan"))
    hg = feats.get("earnH_rev_growth_pct", float("nan"))
    best_rev = max([v for v in (rg, hg) if not math.isnan(v)], default=float("nan"))
    if not math.isnan(best_rev):
        if best_rev >= p["rev_hi"]:
            pts += p["rev_hi_pts"]; parts.append(f"revG:+{p['rev_hi_pts']}({best_rev:.0f}%)")
        elif best_rev >= p["rev_lo"]:
            pts += p["rev_lo_pts"]; parts.append(f"revG:+{p['rev_lo_pts']}({best_rev:.0f}%)")
        elif best_rev < 0:
            pts += p["neg_flag_pts"]; parts.append(f"revG:{p['neg_flag_pts']}({best_rev:.0f}%)")

    pg_b = feats.get("earn_profit_growth_pct", float("nan"))
    pg_h = feats.get("earnH_profit_growth_pct", float("nan"))
    pg = max([v for v in (pg_b, pg_h) if not math.isnan(v)], default=float("nan"))
    if not math.isnan(pg) and pg >= p["prof_thr"]:
        pts += p["prof_pts"]; parts.append(f"profG:+{p['prof_pts']}({pg:.0f}%)")

    rec = min(feats.get("earn_record_count", 0) + feats.get("earnH_record_count", 0),
              p["record_cap"])
    if rec:
        pts += rec * p["record_pts"]; parts.append(f"rec:+{rec * p['record_pts']}")

    guid = feats.get("earn_guidance", 0) or feats.get("earnH_guidance", 0)
    if guid > 0:
        pts += p["guid_raise_pts"]; parts.append(f"guid:+{p['guid_raise_pts']}(raise)")
    elif guid < 0:
        pts += p["guid_cut_pts"]; parts.append(f"guid:{p['guid_cut_pts']}(cut)")
    if feats.get("earn_expects_record", 0) or feats.get("earnH_expects_record", 0):
        pts += p["expects_record_pts"]; parts.append(f"expRec:+{p['expects_record_pts']}")

    if feats.get("earn_profit_flip", 0):
        pts += p["flip_pts"]; parts.append(f"flip:+{p['flip_pts']}")

    mb = feats.get("earn_margin_delta_bps", float("nan"))
    if not math.isnan(mb) and mb >= p["margin_bps_thr"]:
        pts += p["margin_pts"]; parts.append(f"marg:+{p['margin_pts']}({mb:.0f}bps)")

    neg = feats.get("earn_neg_flags", 0)
    if neg:
        pts += neg * p["neg_flag_pts"]; parts.append(f"neg:{neg * p['neg_flag_pts']}")

    rmc = feats.get("earn_rev_to_floatcap", float("nan"))
    if not math.isnan(rmc) and rmc >= p["rev_fcap_thr"]:
        pts += p["rev_fcap_pts"]; parts.append(f"rev/fcap:+{p['rev_fcap_pts']}({rmc:.2f})")

    pmc = feats.get("earn_profit_to_floatcap", float("nan"))
    if not math.isnan(pmc) and pmc >= p["prof_fcap_thr"]:
        pts += p["prof_fcap_pts"]; parts.append(f"prof/fcap:+{p['prof_fcap_pts']}({pmc:.2f})")

    if not math.isnan(hg) and hg >= p["rev_lo"]:
        pts += p["headline_growth_bonus"]; parts.append(f"hlineG:+{p['headline_growth_bonus']}")

    if feats.get("earn_scheduling_only", 0) and p["scheduling_zeroes"]:
        if pts > 0:
            parts.append("sched:capped0")
        pts = min(pts, 0)

    return pts, " ".join(parts) if parts else "none"


# --------------------------------------------------------------------------
# EarnScoreNorm: the 2026-08-14 rebuild (0-1)
#
# Everything above this line is FROZEN because the live gate reads it.  This
# block is the retrained score: same extraction, freely re-derived weights.
#
# The indicator set is defined HERE, not in the evaluator, so the weights are
# fitted against exactly the booleans that runtime will compute.  If a feature
# is added, it appears in both places at once or in neither.
# --------------------------------------------------------------------------
def _best(feats, key):
    """Best of the body (earn_) and headline (earnH_) value for one metric."""
    vals = [feats.get("earn_" + key, float("nan")),
            feats.get("earnH_" + key, float("nan"))]
    vals = [v for v in vals if isinstance(v, (int, float)) and not math.isnan(v)]
    return max(vals) if vals else float("nan")


def _ge(v, thr):
    return (not math.isnan(v)) and v >= thr


# name -> fn(feats, thresholds) -> bool.  Order fixes the weight-vector order.
NORM_FEATURES = {
    # headline channel — the strongest structures in the 2026-08-13 corpus
    "hl_rev_growth":  lambda f, t: _ge(f.get("earnH_rev_growth_pct", float("nan")),
                                       t["rev_lo"]),
    "hl_has_pct":     lambda f, t: bool(f.get("earnH_has_pct", 0)),
    "hl_has_metric":  lambda f, t: bool(f.get("earnH_has_metric", 0)),
    # growth
    "rev_growth_lo":  lambda f, t: _ge(_best(f, "rev_growth_pct"), t["rev_lo"]),
    "rev_growth_hi":  lambda f, t: _ge(_best(f, "rev_growth_pct"), t["rev_hi"]),
    "rev_decline":    lambda f, t: (lambda v: (not math.isnan(v)) and v < 0)(
                                       _best(f, "rev_growth_pct")),
    "prof_growth":    lambda f, t: _ge(_best(f, "profit_growth_pct"), t["prof_thr"]),
    # claims / guidance / flips
    "record":         lambda f, t: (f.get("earn_record_count", 0)
                                    + f.get("earnH_record_count", 0)) > 0,
    "guid_raise":     lambda f, t: max(f.get("earn_guidance", 0),
                                       f.get("earnH_guidance", 0)) > 0,
    "guid_cut":       lambda f, t: min(f.get("earn_guidance", 0),
                                       f.get("earnH_guidance", 0)) < 0,
    "expects_record": lambda f, t: bool(f.get("earn_expects_record", 0)
                                        or f.get("earnH_expects_record", 0)),
    "profit_flip":    lambda f, t: bool(f.get("earn_profit_flip", 0)),
    "margin_up":      lambda f, t: _ge(f.get("earn_margin_delta_bps", float("nan")),
                                       t["margin_bps_thr"]),
    "neg_flag":       lambda f, t: f.get("earn_neg_flags", 0) > 0,
    # size-relative
    "rev_fcap":       lambda f, t: _ge(f.get("earn_rev_to_floatcap", float("nan")),
                                       t["rev_fcap_thr"]),
    "prof_fcap":      lambda f, t: _ge(f.get("earn_profit_to_floatcap", float("nan")),
                                       t["prof_fcap_thr"]),
    # shape of the PR itself
    "no_structure":   lambda f, t: f.get("earn_n_hits", 0) == 0,
    "sched_only":     lambda f, t: bool(f.get("earn_scheduling_only", 0)),
}

# The fitted 2026-08-14 values, mirrored here because earn_norm_params.json is
# GITIGNORED (the repo whitelist tracks no .json) -- on a fresh clone the file
# does not exist, and a zeroed fallback would silently return 0.5 for every PR.
# evaluate_earning_scorer.py overwrites the file; if you re-run it, paste the
# new numbers here too or the clone and this disk will disagree.
BUILTIN_NORM = {
    "thresholds": {"rev_lo": 10.0, "rev_hi": 75.0, "prof_thr": 30.0,
                   "margin_bps_thr": 200.0, "rev_fcap_thr": 0.25,
                   "prof_fcap_thr": 0.1},
    "weights": {
        "hl_rev_growth": 0.35, "hl_has_pct": 0.30, "hl_has_metric": 0.40,
        "rev_growth_lo": 0.60, "rev_growth_hi": -0.10, "rev_decline": -0.25,
        "prof_growth": 0.35, "record": 0.0, "guid_raise": 0.25,
        "guid_cut": 0.0, "expects_record": 0.0, "profit_flip": 0.20,
        "margin_up": -0.10, "neg_flag": 0.05, "rev_fcap": 0.35,
        "prof_fcap": 0.10, "no_structure": -0.25, "sched_only": 0.0,
    },
    "bias": -0.70,
}


def load_norm_params(path=None):
    path = path or DEFAULT_NORM_FILE
    if os.path.exists(path):
        with open(path) as fh:
            n = json.loads(json.dumps(BUILTIN_NORM))     # deep copy of defaults
            loaded = json.load(fh)
            n["thresholds"].update(loaded.get("thresholds", {}))
            n["weights"].update(loaded.get("weights", {}))
            n["bias"] = loaded.get("bias", n["bias"])
            return n
    return json.loads(json.dumps(BUILTIN_NORM))


def norm_indicators(feats, thresholds):
    """The binary feature vector, as a dict.  Shared by fit and runtime."""
    return {name: bool(fn(feats, thresholds)) for name, fn in NORM_FEATURES.items()}


def score_norm(feats, norm=None):
    """feats -> (EarnScoreNorm in [0,1], breakdown string).

    Ranking score, NOT a calibrated probability: the >=30% base rate moves
    5.0% -> 20.9% -> 20.0% -> 13.4% across May..Aug 2026, so the absolute
    value does not transfer between regimes.  Compare rows, not thresholds.
    """
    n = norm or load_norm_params()
    ind = norm_indicators(feats, n["thresholds"])
    raw = float(n["bias"])
    parts = []
    for name, on in ind.items():
        if not on:
            continue
        w = float(n["weights"].get(name, 0.0))
        raw += w
        if w:
            parts.append(f"{name}:{w:+.2f}")
    proba = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, raw))))
    return proba, " ".join(parts) if parts else "none"


def score_text(headline, body, mktcap=None, params=None, norm=None):
    feats = extract_metrics(headline, body, mktcap)
    score, breakdown = score_metrics(feats, params)
    feats["EarningScore"] = score
    feats["EarnBreakdown"] = breakdown
    nscore, nbreak = score_norm(feats, norm)
    feats["EarnScoreNorm"] = nscore
    feats["EarnNormBreakdown"] = nbreak
    return feats


# --------------------------------------------------------------------------
# batch / CLI
# --------------------------------------------------------------------------
FEATURE_COLS = [
    "earn_rev_growth_pct", "earn_profit_growth_pct", "earn_best_growth_pct",
    "earn_worst_growth_pct", "earn_record_count", "earn_guidance",
    "earn_expects_record", "earn_profit_flip", "earn_margin_delta_bps",
    "earn_neg_flags", "earn_n_hits", "earn_rev_dollars", "earn_rev_to_floatcap",
    "earn_profit_dollars", "earn_profit_to_floatcap",
    "earn_scheduling_only", "earn_num_density",
    "earnH_rev_growth_pct", "earnH_best_growth_pct", "earnH_record_count",
    "earnH_guidance", "earnH_expects_record", "earnH_n_hits",
    "earnH_has_pct", "earnH_has_metric",
]


def _row_size_dollars(r):
    """Size denominator: float-cap = Float(M shares) * LastDailyClose, used for
    ALL rows for consistency (MktCap is populated on only ~16% of PR-stats
    rows).  Falls back to MktCap($M) when Float/close is unusable."""
    try:
        fc = float(r.get("Float")) * float(r.get("LastDailyClose")) * 1e6
        if fc > 0 and not math.isnan(fc):
            return fc
    except (TypeError, ValueError):
        pass
    try:
        mc = float(r.get("MktCap")) * 1e6
        if mc > 0 and not math.isnan(mc):
            return mc
    except (TypeError, ValueError):
        pass
    return None


def score_dataframe(df, params=None, norm=None):
    import pandas as pd
    params = params or load_params()
    norm = norm or load_norm_params()
    rows = []
    for _, r in df.iterrows():
        feats = score_text(str(r.get("Headline", "")), str(r.get("Body", "")),
                           _row_size_dollars(r), params, norm)
        rows.append({c: feats.get(c) for c in FEATURE_COLS}
                    | {"EarningScore": feats["EarningScore"],
                       "EarnBreakdown": feats["EarnBreakdown"],
                       "EarnScoreNorm": feats["EarnScoreNorm"],
                       "EarnNormBreakdown": feats["EarnNormBreakdown"]})
    return pd.concat([df.reset_index(drop=True),
                      pd.DataFrame(rows)], axis=1)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[2])
    ap.add_argument("--input", default=DEFAULT_INPUT)
    ap.add_argument("--out", default=None,
                    help="output TSV (default: <input>_earnScored.tsv)")
    ap.add_argument("--params", default=None,
                    help="score params JSON (default: earn_score_params.json "
                         "next to this script, else built-ins)")
    ap.add_argument("--norm-params", default=None,
                    help="EarnScoreNorm weights JSON (default: "
                         "earn_norm_params.json next to this script)")
    ap.add_argument("--stdin", action="store_true",
                    help="read one Body from stdin, print JSON")
    ap.add_argument("--headline-text", default="", help="headline for --stdin")
    ap.add_argument("--mktcap", type=float, default=None, help="MktCap for --stdin")
    args = ap.parse_args()

    params = load_params(args.params)
    norm = load_norm_params(args.norm_params)

    if args.stdin:
        body = sys.stdin.read()
        feats = score_text(args.headline_text, body, args.mktcap, params, norm)
        feats.pop("_body_hits", None)
        feats.pop("_head_hits", None)
        out = {k: (None if isinstance(v, float) and math.isnan(v) else v)
               for k, v in feats.items()}
        print(json.dumps(out, indent=2))
        return

    import pandas as pd
    df = pd.read_csv(args.input, sep="\t")
    scored = score_dataframe(df, params, norm)
    out = args.out or re.sub(r"\.tsv$", "", args.input) + "_earnScored.tsv"
    scored.to_csv(out, sep="\t", index=False)
    print(f"wrote {out}  ({len(scored)} rows)")
    sc = scored["EarningScore"]
    print(f"EarningScore (frozen, live gate reads this): min {sc.min()}  "
          f"median {sc.median()}  max {sc.max()}  >0: {(sc > 0).sum()}  "
          f">=4: {(sc >= 4).sum()}")
    ns = scored["EarnScoreNorm"]
    print(f"EarnScoreNorm (rebuilt 0-1): min {ns.min():.3f}  median "
          f"{ns.median():.3f}  max {ns.max():.3f}  >=0.5: {(ns >= 0.5).sum()}")


if __name__ == "__main__":
    main()
